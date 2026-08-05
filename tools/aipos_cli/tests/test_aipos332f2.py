"""AIPOS-332F2 修复轮测试:resume/fix 轮接进六步编排。

验收断言(卡内):
1. --round-type resume 与 fix 走与 first 相同的六步编排,差异仅:
   跳过 claim 步(校验卡已由本 executor_instance 持有,不是则报错不拉起)、
   kickoff 用对应轮次模板(增量式,--delta 注入);
2. 删除『需要在调用方实现』占位路径 —— 三种轮次同一条编排代码;
3. resume 轮对已持有卡真走到 launch 步(无害 runtime);对未认领卡报错不拉起;
   fix 轮同验;
4. first 轮零回归。

红线:不拉起真实 agent、不碰 daemon、不碰 test_aipos296。
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tools.aipos_cli import pump_orchestration as po
from tools.aipos_cli.advisor_pump import generate_kickoff, validate_and_dispatch


# ---------------------------------------------------------------------------
# 辅助:构造 claim 记录(模拟卡已被某实例持有)
# ---------------------------------------------------------------------------

def _write_claim_record(
    workspace: Path,
    task_id: str,
    holder: str = "exec.lybra.kiwiai-dev",
    timestamp: str = "20260805_030000",
) -> Path:
    """在 workspace 下写一条 claim 记录,模拟卡已被 holder 持有。"""
    claims_dir = workspace / "5_tasks" / "records" / "claims" / task_id
    claims_dir.mkdir(parents=True, exist_ok=True)
    record = claims_dir / f"claim_{task_id}_{timestamp}_{holder.replace('.', '-')}.md"
    record.write_text(
        f"""---
record_type: claim_record
task_id: {task_id}
canonical_agent_instance: {holder}
actor: {holder}
claim_id: claim_{task_id}_{timestamp}
claimed_at: '2026-08-05T03:00:00Z'
---
""",
        encoding="utf-8",
    )
    return record


def _make_ctx(
    tmp_path: Path,
    *,
    card_id: str = "AIPOS-332F2T",
    round_type: str = "resume",
    holder: str = "exec.lybra.kiwiai-dev",
    executor_instance: str = "exec.lybra.kiwiai-dev",
) -> po.DispatchContext:
    """构造一个最小可用的 DispatchContext。"""
    ws = tmp_path / "ws"
    repo = tmp_path / "repo"
    ws.mkdir(exist_ok=True)
    repo.mkdir(exist_ok=True)
    return po.DispatchContext(
        card_id=card_id,
        role="executor",
        round_type=round_type,
        delta="修复审计指出的问题",
        workspace_root=ws,
        product_repo=repo,
        gate_url="http://g:7118",
        connection_json=ws / ".lybra" / "connection.json",
        envelope="pol_lybra_dev_6",
        executor_instance=executor_instance,
        runtime_cmd_template="echo {kickoff}",
    )


# ---------------------------------------------------------------------------
# 验收1:resume/fix 轮对已持有卡走到 launch 步
# ---------------------------------------------------------------------------

class TestResumeFixReachLaunch:
    """resume/fix 轮对已持有卡真走到 launch 步(无害 runtime)。"""

    def test_resume_round_reaches_launch_when_held(self, tmp_path):
        """resume 轮:卡已持有 → 跳过 claim → 走到 launch 步。"""
        ctx = _make_ctx(tmp_path, round_type="resume")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)

        # mock step_launch 和 step_watch 避免真实拉起
        with patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}) as mock_launch, \
             patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is True, f"errors={res['errors']}"
        assert res["step"] == "watch"  # 走完了全部步骤
        # launch 被调用(证明走到了 launch 步)
        mock_launch.assert_called_once()
        # claim 槽位是 verify_held 的结果(不是 gate claim)
        claim_result = res["claim"]
        assert claim_result["ok"] is True
        assert claim_result["holder"] == ctx.executor_instance

    def test_fix_round_reaches_launch_when_held(self, tmp_path):
        """fix 轮:卡已持有 → 跳过 claim → 走到 launch 步。"""
        ctx = _make_ctx(tmp_path, round_type="fix")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)

        with patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}) as mock_launch, \
             patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is True, f"errors={res['errors']}"
        mock_launch.assert_called_once()
        claim_result = res["claim"]
        assert claim_result["ok"] is True

    def test_resume_round_kickoff_uses_resume_template(self, tmp_path):
        """resume 轮 kickoff 用 resume 模板(含续跑轮标识)。"""
        ctx = _make_ctx(tmp_path, round_type="resume")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)

        with patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}), \
             patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is True
        # kickoff 含 resume 模板特征
        assert "续跑轮" in res["kickoff"]
        assert ctx.card_id in res["kickoff"]

    def test_fix_round_kickoff_uses_fix_template(self, tmp_path):
        """fix 轮 kickoff 用 fix 模板(含约束句)。"""
        ctx = _make_ctx(tmp_path, round_type="fix")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)

        with patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}), \
             patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is True
        assert "修复轮" in res["kickoff"]
        assert "约束" in res["kickoff"]


# ---------------------------------------------------------------------------
# 验收2:对未认领卡报错不拉起
# ---------------------------------------------------------------------------

class TestUnclaimedCardBlocked:
    """resume/fix 轮对未认领卡报错不拉起。"""

    def test_resume_round_no_claim_record_errors_out(self, tmp_path):
        """resume 轮:无 claim 记录 → 报错,不拉起。"""
        ctx = _make_ctx(tmp_path, round_type="resume")
        # 不写 claim 记录 → 卡未被持有

        with patch.object(po, "step_launch") as mock_launch:
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is False
        assert res["step"] == "claim"
        assert any("claim" in e.lower() or "持有" in e for e in res["errors"]), res["errors"]
        # launch 未被调用(证明没走到 launch)
        mock_launch.assert_not_called()

    def test_fix_round_no_claim_record_errors_out(self, tmp_path):
        """fix 轮:无 claim 记录 → 报错,不拉起。"""
        ctx = _make_ctx(tmp_path, round_type="fix")
        # 不写 claim 记录

        with patch.object(po, "step_launch") as mock_launch:
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is False
        assert res["step"] == "claim"
        mock_launch.assert_not_called()

    def test_resume_round_wrong_holder_errors_out(self, tmp_path):
        """resume 轮:卡被其他实例持有 → 报错,不拉起。"""
        ctx = _make_ctx(tmp_path, round_type="resume", executor_instance="exec.lybra.kiwiai-dev")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder="other.instance.here")

        with patch.object(po, "step_launch") as mock_launch:
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is False
        assert res["step"] == "claim"
        assert any("other.instance.here" in e for e in res["errors"]), res["errors"]
        mock_launch.assert_not_called()

    def test_fix_round_wrong_holder_errors_out(self, tmp_path):
        """fix 轮:卡被其他实例持有 → 报错,不拉起。"""
        ctx = _make_ctx(tmp_path, round_type="fix", executor_instance="exec.lybra.kiwiai-dev")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder="other.instance.here")

        with patch.object(po, "step_launch") as mock_launch:
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert res["ok"] is False
        assert res["step"] == "claim"
        mock_launch.assert_not_called()


# ---------------------------------------------------------------------------
# 验收3:first 轮零回归
# ---------------------------------------------------------------------------

class TestFirstRoundZeroRegression:
    """first 轮行为不变(走 step_claim,不走 step_verify_held)。"""

    def test_first_round_still_calls_step_claim(self, tmp_path):
        """first 轮:走 step_claim(即使卡已持有也重新 claim,幂等续派)。"""
        ctx = _make_ctx(tmp_path, round_type="first")
        # 即使已有 claim 记录,first 轮仍走 step_claim
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)

        with patch.object(po, "step_claim", return_value={"ok": True, "auto_released": True, "claim_record": "fake", "reason": ""}) as mock_claim, \
             patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}), \
             patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        # first 轮走 step_claim(不是 step_verify_held)
        mock_claim.assert_called_once()
        assert res["ok"] is True

    def test_first_round_kickoff_uses_first_template(self, tmp_path):
        """first 轮 kickoff 用 first 模板(冷启动)。"""
        ctx = _make_ctx(tmp_path, round_type="first")

        with patch.object(po, "step_claim", return_value={"ok": True, "auto_released": True, "claim_record": "fake", "reason": ""}), \
             patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}), \
             patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
            res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

        assert "冷启动" in res["kickoff"]


# ---------------------------------------------------------------------------
# 验收4:删除占位路径
# ---------------------------------------------------------------------------

class TestPlaceholderRemoved:
    """『需要在调用方实现』占位路径已删除。"""

    def test_validate_and_dispatch_no_placeholder_message(self, tmp_path):
        """validate_and_dispatch 不再返回『需要在调用方实现』消息。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        # 写一个任务卡
        card_dir = ws / "5_tasks" / "queue" / "pending"
        card_dir.mkdir(parents=True)
        card_file = card_dir / "aipos-test.md"
        card_file.write_text("---\ntask_id: AIPOS-TEST\n---\n# Test card\nSome content here.", encoding="utf-8")

        for rt in ("first", "fix", "resume"):
            result = validate_and_dispatch(
                card_id="AIPOS-TEST",
                role="executor",
                round_type=rt,
                delta="test delta",
                workspace_root=ws,
                dry_run=False,
            )
            # 不再含占位消息
            assert "需要在调用方实现" not in result.get("message", ""), \
                f"round_type={rt} 仍含占位消息"

    def test_validate_and_dispatch_no_placeholder_dry_run(self, tmp_path):
        """dry-run 也不含占位消息。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        card_dir = ws / "5_tasks" / "queue" / "pending"
        card_dir.mkdir(parents=True)
        card_file = card_dir / "aipos-test2.md"
        card_file.write_text("---\ntask_id: AIPOS-TEST2\n---\n# Test\nContent.", encoding="utf-8")

        for rt in ("first", "fix", "resume"):
            result = validate_and_dispatch(
                card_id="AIPOS-TEST2",
                role="executor",
                round_type=rt,
                delta="",
                workspace_root=ws,
                dry_run=True,
            )
            assert result["ok"] is True
            assert "需要在调用方实现" not in str(result)


# ---------------------------------------------------------------------------
# 验收5:三种轮次同一条编排代码(轮次差异是数据不是分支)
# ---------------------------------------------------------------------------

class TestUnifiedOrchestration:
    """三种轮次走同一条编排代码。"""

    def test_all_round_types_go_through_same_code_path(self, tmp_path):
        """first/fix/resume 都经过 context → kickoff → expand → claim/verify → launch → watch。"""
        results = {}
        for rt in ("first", "fix", "resume"):
            ctx = _make_ctx(tmp_path, round_type=rt)
            if rt != "first":
                _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)

            with patch.object(po, "step_claim", return_value={"ok": True, "auto_released": True, "claim_record": "fake", "reason": ""}), \
                 patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}), \
                 patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
                res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)
            results[rt] = res

        # 三种轮次都成功
        for rt, res in results.items():
            assert res["ok"] is True, f"{rt} 轮失败: errors={res['errors']}"
            assert res["step"] == "watch", f"{rt} 轮未走完全部步骤"

    def test_resume_fix_do_not_call_gate_claim(self, tmp_path):
        """resume/fix 轮不调用 gate claim(通过检查 step_claim 不被调用)。"""
        for rt in ("resume", "fix"):
            ctx = _make_ctx(tmp_path, round_type=rt)
            _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)

            with patch.object(po, "step_claim") as mock_claim, \
                 patch.object(po, "step_launch", return_value={"ok": True, "exit_code": 0}), \
                 patch.object(po, "step_watch", return_value={"verdict": "product_landed", "exit_code": 2}):
                res = po.run_pump_dispatch(ctx, dry_run=False, do_claim=True, do_launch=True, do_watch=True)

            # step_claim 不被调用(resume/fix 走 step_verify_held)
            mock_claim.assert_not_called(), f"{rt} 轮不应调用 step_claim"
            assert res["ok"] is True


# ---------------------------------------------------------------------------
# step_verify_held 单元测试
# ---------------------------------------------------------------------------

class TestStepVerifyHeld:
    """step_verify_held 单元级测试。"""

    def test_no_claim_dir_returns_false(self, tmp_path):
        """无 claims 目录 → ok=False。"""
        ctx = _make_ctx(tmp_path, round_type="resume")
        result = po.step_verify_held(ctx)
        assert result["ok"] is False
        assert "无 claim 记录" in result["reason"]

    def test_matching_holder_returns_true(self, tmp_path):
        """持有者匹配 → ok=True。"""
        ctx = _make_ctx(tmp_path, round_type="resume")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder=ctx.executor_instance)
        result = po.step_verify_held(ctx)
        assert result["ok"] is True
        assert result["holder"] == ctx.executor_instance

    def test_mismatched_holder_returns_false(self, tmp_path):
        """持有者不匹配 → ok=False。"""
        ctx = _make_ctx(tmp_path, round_type="resume", executor_instance="exec.lybra.kiwiai-dev")
        _write_claim_record(ctx.workspace_root, ctx.card_id, holder="someone.else")
        result = po.step_verify_held(ctx)
        assert result["ok"] is False
        assert "someone.else" in result["reason"]
        assert result["holder"] == "someone.else"

    def test_empty_holder_in_record(self, tmp_path):
        """claim 记录缺 canonical_agent_instance → 回退到 actor 字段。"""
        ctx = _make_ctx(tmp_path, round_type="resume")
        claims_dir = ctx.workspace_root / "5_tasks" / "records" / "claims" / ctx.card_id
        claims_dir.mkdir(parents=True)
        record = claims_dir / f"claim_{ctx.card_id}_20260805.md"
        # 只有 actor,没有 canonical_agent_instance
        record.write_text(
            f"""---
record_type: claim_record
task_id: {ctx.card_id}
actor: {ctx.executor_instance}
---
""",
            encoding="utf-8",
        )
        result = po.step_verify_held(ctx)
        assert result["ok"] is True
        assert result["holder"] == ctx.executor_instance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
