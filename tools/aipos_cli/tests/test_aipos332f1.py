"""AIPOS-332F1 修复轮测试(F-332-01 P1 + F-332-03 P2)。

F-332-01 (P1): 一次性 pump run 路径的 launch_failed/blocked 信号须落工作区 events
    (S10 硬约束2/3)。复现审计实验:workspace_only + 拉起失败 → events/<ID>/ 下有
    blocked/launch_failed 文件,S9 派生的 expect(blocked_*.md)能命中。

F-332-03 (P2): --dry-run 缺 --envelope 降级为告警 + 展示未展开占位符 + exit 0
    (旧表层行为恢复);非 dry-run 缺 envelope 仍硬失败(S7 语义不变)。两态有覆盖。

红线遵守:不拉起真实 agent(判断留人)、不碰 daemon(auditor_loop 有自己的
write_block_file/write_blocked_event,本路径无关)、不碰 test_aipos296。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.aipos_cli import pump_orchestration as po
from tools.aipos_cli.agent_launch_check import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    run_launch_check,
    write_event_to_workspace,
)


# ---------------------------------------------------------------------------
# F-332-01: launch-check 失败类事件落工作区
# ---------------------------------------------------------------------------

def _always_fail_check_launch(*args, **kwargs):
    """模拟拉起失败:进程早退(exit 1)。"""
    return EXIT_ERROR, {
        "reason": "process_early_exit",
        "exit_code": 1,
        "proc_alive": False,
        "cpu_delta": 0.0,
        "new_session_files": 0,
        "worktree_changed": False,
    }


def test_double_failure_writes_workspace_events(tmp_path):
    """F-332-01: 双拉失败 → 工作区 events/<ID>/ 下有 launch_failed(2) + blocked(1)。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    product_repo = tmp_path / "repo"
    (product_repo / "task_cards").mkdir(parents=True)

    with patch("tools.aipos_cli.agent_launch_check.check_launch",
               side_effect=_always_fail_check_launch), \
         patch("tools.aipos_cli.agent_launch_check.time.sleep"):  # 加速:跳过 backoff
        code = run_launch_check(
            spawn_cmd="timeout 10 false",
            task_id="AIPOS-T1",
            executor_instance="exec.test",
            product_repo=product_repo,
            session_dirs=[],
            worktree_path=str(product_repo),
            launch_window_secs=1.0,
            check_interval_secs=1.0,
            workspace_root=workspace,
        )

    assert code == EXIT_BLOCKED
    events_dir = workspace / "5_tasks" / "records" / "events" / "AIPOS-T1"
    launch_failed = sorted(events_dir.glob("launch_failed_*.md"))
    blocked = sorted(events_dir.glob("blocked_*.md"))
    assert len(launch_failed) == 2, f"expected 2 launch_failed, got {launch_failed}"
    assert len(blocked) == 1, f"expected 1 blocked, got {blocked}"

    # 内容诚实:launch_check_event + event_kind + task_id
    content = blocked[0].read_text(encoding="utf-8")
    assert "record_type: launch_check_event" in content
    assert "event_kind: blocked" in content
    assert "task_id: AIPOS-T1" in content
    # 同秒去重:两次 launch_failed 文件名不冲突(都存在)
    names = {p.name for p in launch_failed}
    assert len(names) == 2


def test_workspace_event_format_parsable_and_honest(tmp_path):
    """F-332-01: 事件文件 frontmatter 可解析,event_kind/actor/timestamp 齐全。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ev = {
        "kind": "blocked", "task_id": "AIPOS-T2",
        "reason": "max_launch_attempts_exceeded",
        "timestamp": "2026-08-05T02:30:00Z", "attempt": 2,
        "failure_history": [{"big": "ignored"}],  # 复杂字段不进 frontmatter
    }
    p = write_event_to_workspace(workspace, "AIPOS-T2", ev, actor="exec.lybra.kiwiai-dev")
    assert p.name == "blocked_20260805_023000.md"
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "actor: exec.lybra.kiwiai-dev" in text
    assert "reason: max_launch_attempts_exceeded" in text
    assert "failure_history" not in text  # 复杂字段不泄漏进 frontmatter


def test_write_event_failure_not_swallowed(tmp_path):
    """F-332-01 红线: 落库失败不得吞掉 —— 必须抛出(调用方据此响)。"""
    # workspace_root 指向一个已存在文件 → 其下建目录失败 → 抛 OSError
    bad = tmp_path / "afile"
    bad.write_text("x", encoding="utf-8")
    ev = {"kind": "blocked", "task_id": "AIPOS-T3", "timestamp": "2026-08-05T02:30:00Z"}
    with pytest.raises(OSError):
        write_event_to_workspace(bad, "AIPOS-T3", ev)


def test_no_workspace_root_keeps_legacy_stdout_only(tmp_path, capsys):
    """F-332-01 回归: workspace_root=None → 仅 stdout + 产品仓 BLOCK(旧行为/daemon 不变)。"""
    product_repo = tmp_path / "repo"
    (product_repo / "task_cards").mkdir(parents=True)
    with patch("tools.aipos_cli.agent_launch_check.check_launch",
               side_effect=_always_fail_check_launch), \
         patch("tools.aipos_cli.agent_launch_check.time.sleep"):
        code = run_launch_check(
            spawn_cmd="timeout 10 false", task_id="AIPOS-T4", executor_instance="exec.test",
            product_repo=product_repo, session_dirs=[], worktree_path=str(product_repo),
            launch_window_secs=1.0, check_interval_secs=1.0,
            workspace_root=None,
        )
    assert code == EXIT_BLOCKED
    # 旧行为:无工作区 events 目录
    assert not (tmp_path / "5_tasks").exists()
    # 产品仓 BLOCK 仍写(降为详情附件)
    assert list((product_repo / "task_cards" / "AIPOS-T4").glob("BLOCK-launch-*.md"))
    # stdout 仍 emit 事件(契约不变)
    out = capsys.readouterr().out
    assert "launch_failed" in out and "blocked" in out


def test_workspace_only_launch_failure_s9_expect_hits(tmp_path):
    """F-332-01 审计复现实验: workspace_only + 拉起失败
    → events/<ID>/ 下有 blocked/launch_failed 文件;S9 派生的 expect(blocked_*.md)能命中。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    product_repo = tmp_path / "repo"  # workspace_only:产出不落产品仓
    (product_repo / "task_cards").mkdir(parents=True)

    # 1) 拉起失败,事件落工作区(workspace_only:worktree_path 空串,不监听产品仓)
    with patch("tools.aipos_cli.agent_launch_check.check_launch",
               side_effect=_always_fail_check_launch), \
         patch("tools.aipos_cli.agent_launch_check.time.sleep"):
        run_launch_check(
            spawn_cmd="timeout 10 false", task_id="AIPOS-T5", executor_instance="exec.test",
            product_repo=product_repo, session_dirs=[], worktree_path="",
            launch_window_secs=1.0, check_interval_secs=1.0,
            workspace_root=workspace,
        )
    events_dir = workspace / "5_tasks" / "records" / "events" / "AIPOS-T5"
    assert list(events_dir.glob("blocked_*.md")), "工作区应有 blocked_*.md"
    assert list(events_dir.glob("launch_failed_*.md")), "工作区应有 launch_failed_*.md"

    # 2) S9 派生(workspace_only profile:monitors_product_repo=False)
    deriv = po.derive_expect_patterns(
        "AIPOS-T5", "executor",
        {"expect_source": "workspace", "monitors_product_repo": False},
    )
    patterns = [e["pattern"] for e in deriv["patterns"]]
    assert "5_tasks/records/events/AIPOS-T5/blocked_*.md" in patterns
    # workspace_only 不派生产品仓路径
    for p in patterns:
        assert "task_cards" not in p

    # 3) 哨兵自证:blocked_*.md 命中(布防即检,本轮新产出)
    verify = po.verify_sentinel_params(
        workspace, deriv["patterns"], None,
        {"stall_surfaces": ["session_dirs"], "run_log_role": "end_only", "warnings": []},
    )
    blocked_status = next(s for s in verify["expect_status"] if s["meaning"] == "blocked")
    assert blocked_status["matched"] is True
    assert blocked_status["count"] >= 1


def test_step_launch_passes_workspace_root(tmp_path):
    """F-332-01 接线: step_launch 把 ctx.workspace_root 透传给 run_launch_check。"""
    ctx = po.DispatchContext(
        card_id="AIPOS-T6", role="executor",
        workspace_root=tmp_path / "ws", product_repo=tmp_path / "repo",
        runtime_cmd_template="pi --append-system-prompt {kickoff}",
        executor_instance="exec.test",
    )
    (tmp_path / "repo").mkdir()
    ctx.kickoff = "hello"
    with patch("tools.aipos_cli.agent_launch_check.run_launch_check", return_value=0) as mock_run:
        po.step_launch(ctx)
    kwargs = mock_run.call_args.kwargs
    assert kwargs.get("workspace_root") == tmp_path / "ws"


# ---------------------------------------------------------------------------
# F-332-03: dry-run 缺 envelope 降级(两态)
# ---------------------------------------------------------------------------

def _ctx_for_dispatch(tmp_path, *, envelope="pol_lybra_dev_1"):
    return po.DispatchContext(
        card_id="AIPOS-T7", role="executor", round_type="first", delta="",
        workspace_root=tmp_path / "ws", product_repo=tmp_path / "repo",
        gate_url="http://g:7118", envelope=envelope,
        executor_instance="exec.test",
    )


def test_dry_run_missing_envelope_degrades_to_warning(tmp_path):
    """F-332-03: dry-run + 缺 envelope → 告警 + 展示未展开 + exit 0(旧表层恢复)。"""
    import re
    ctx = _ctx_for_dispatch(tmp_path, envelope="")
    res = po.run_pump_dispatch(
        ctx, dry_run=True, do_claim=False, do_launch=False, do_watch=False
    )
    assert res["ok"] is True, f"dry-run 缺 envelope 应 exit 0(ok=True),errors={res['errors']}"
    assert res["step"] == "dry_run_ok"
    # 告警提到 envelope 缺失
    joined = " ".join(res["warnings"])
    assert "envelope" in joined
    # kickoff 仍含未展开的 {envelope}(展示未展开占位符)
    assert "{envelope}" in res["kickoff"]
    # 其余可得占位符已展开(workspace/gate/product_repo 有值)
    assert "{workspace}" not in res["kickoff"]
    assert "{gate}" not in res["kickoff"]


def test_non_dry_run_missing_envelope_still_hard_fails(tmp_path):
    """F-332-03: 非 dry-run + 缺 envelope → 仍硬失败(S7 语义不变)。"""
    ctx = _ctx_for_dispatch(tmp_path, envelope="")
    res = po.run_pump_dispatch(
        ctx, dry_run=False, do_claim=False, do_launch=False, do_watch=False
    )
    assert res["ok"] is False
    assert res["step"] == "expand_kickoff"
    assert any("envelope" in e for e in res["errors"]), res["errors"]


def test_dry_run_with_envelope_expands_fully(tmp_path):
    """F-332-03 回归: dry-run + 有 envelope → 全展开,无缺失占位符(原行为不变)。"""
    import re
    ctx = _ctx_for_dispatch(tmp_path, envelope="pol_lybra_dev_1")
    res = po.run_pump_dispatch(
        ctx, dry_run=True, do_claim=False, do_launch=False, do_watch=False
    )
    assert res["ok"] is True
    assert re.findall(r"\{[a-z_]+\}", res["kickoff"]) == [], "不应残留未展开占位符"
    # 无 envelope 缺失告警
    assert not any("envelope" in w and "缺少" in w for w in res["warnings"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
