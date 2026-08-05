"""AIPOS-332F3 修复轮测试(修一/修二/修三)。

修一(P0): 会话目录派生取自运行体档案,不硬编码 product_repo/.pi;
         派生结果校验存在性,不存在时降级到 CPU 判据。
修二(P1): 真派路径 kickoff 与 dry-run 同一条展开逻辑;零占位符残留。
修三:     kickoff 模板极简三要素;与 338 卡面契约节重复的【过门】句已删。

红线:不拉起真实 agent、不碰 daemon、不碰 test_aipos296。
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.aipos_cli import pump_orchestration as po
from tools.aipos_cli.advisor_pump import generate_kickoff
from tools.aipos_cli.runtime_profiles import (
    RUNTIME_PROFILES,
    get_runtime_profile,
    select_observation_plan,
)


# ---------------------------------------------------------------------------
# 修一:会话目录派生
# ---------------------------------------------------------------------------

class TestSessionDirDerivation:
    """修一:会话目录取自运行体档案,不硬编码 product_repo/.pi。"""

    def test_pi_profile_has_session_root(self):
        """pi 运行体档案声明了 session_root。"""
        profile = RUNTIME_PROFILES["pi"]
        assert profile.get("session_root") == "~/.pi/agent/sessions"
        assert profile.get("session_dir_encoding") == "pi_cwd_dash"

    def test_encode_cwd_for_pi_matches_real_directory(self):
        """编码规则与本机真实路径断言:/ → -,前后加 --。"""
        # 本机实证:/home/kiwi/projects/lybra → --home-kiwi-projects-lybra--
        result = po._encode_cwd_for_pi(Path("/home/kiwi/projects/lybra"))
        assert result == "--home-kiwi-projects-lybra--"

        # 另一个实证
        result2 = po._encode_cwd_for_pi(Path("/home/kiwi/projects/kiwiai-pi/lybra-executor"))
        assert result2 == "--home-kiwi-projects-kiwiai-pi-lybra-executor--"

    def test_session_dirs_derived_from_runtime_profile(self, tmp_path):
        """会话目录从运行体档案派生,不是 product_repo/.pi/sessions。"""
        # 创建真实的 pi 会话目录
        home_pi = tmp_path / "fake_home" / ".pi" / "agent" / "sessions"
        product_repo = tmp_path / "repo"
        product_repo.mkdir()
        # AIPOS-332F5:编码用 workdir(运行体真实工作目录),不用 product_repo
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        encoded = po._encode_cwd_for_pi(workdir)
        real_session_dir = home_pi / encoded
        real_session_dir.mkdir(parents=True)

        ctx = po.DispatchContext(
            card_id="AIPOS-T1", role="executor",
            workspace_root=tmp_path / "ws",
            product_repo=product_repo,
            workdir=workdir,
        )
        # 模拟 observation_plan 里有 pi 运行体档案
        ctx.observation_plan = {
            "runtime_profile": {
                "session_root": str(tmp_path / "fake_home" / ".pi" / "agent" / "sessions"),
                "session_dir_encoding": "pi_cwd_dash",
            },
            "warnings": [],
        }

        dirs = po._session_dirs_for(ctx)
        assert len(dirs) == 1
        assert dirs[0] == str(real_session_dir)
        # .assertNotIn 确保不硬编码 product_repo/.pi
        for d in dirs:
            assert ".pi/sessions" not in d or str(home_pi.parent) in d

    def test_session_dirs_nonexistent_degrades_with_warning(self, tmp_path):
        """目录不存在 → 告警 + 降级(空列表),不带着死判据开杀。"""
        product_repo = tmp_path / "repo"
        product_repo.mkdir()
        # AIPOS-332F5:workdir 必须配置才会编码;这里配一个使目录不存在的场景
        workdir = tmp_path / "actual_workdir"
        workdir.mkdir()

        ctx = po.DispatchContext(
            card_id="AIPOS-T2", role="executor",
            workspace_root=tmp_path / "ws",
            product_repo=product_repo,
            workdir=workdir,
        )
        ctx.observation_plan = {
            "runtime_profile": {
                "session_root": str(tmp_path / "nonexistent" / "sessions"),
                "session_dir_encoding": "pi_cwd_dash",
            },
            "warnings": [],
        }

        dirs = po._session_dirs_for(ctx)
        # 目录不存在 → 不加入列表(降级)
        assert len(dirs) == 0
        # 告警注入 observation_plan
        warnings = ctx.observation_plan["warnings"]
        assert len(warnings) >= 1
        assert "不存在" in warnings[0]
        assert "降级" in warnings[0]

    def test_no_hardcoded_product_repo_pi_sessions(self):
        """grep 源码验证:无 product_repo/.pi/sessions 硬编码。"""
        import inspect
        source = inspect.getsource(po._session_dirs_for)
        # 不应有 product_repo / ".pi" / "sessions" 这种硬编码
        assert '".pi"' not in source or "session_root" in source
        assert "product_repo / \".pi\" / \"sessions\"" not in source

    def test_runtime_profile_propagates_session_root(self):
        """select_observation_plan 把 session_root 传到 runtime_profile。"""
        plan = select_observation_plan("pi", "tools", None)
        assert plan["runtime_profile"].get("session_root") == "~/.pi/agent/sessions"
        assert plan["runtime_profile"].get("session_dir_encoding") == "pi_cwd_dash"

    def test_unknown_runtime_has_no_session_root(self):
        """未知运行体 → session_root=None,不派生会话目录。"""
        profile, warns = get_runtime_profile("unknown_runtime_xyz")
        assert profile.get("session_root") is None
        assert profile.get("session_dir_encoding") is None


# ---------------------------------------------------------------------------
# 修二:占位符真派路径展开
# ---------------------------------------------------------------------------

class TestPlaceholderExpansionUnified:
    """修二:真派与 dry-run 同一条展开逻辑;零占位符残留。"""

    def _ctx(self, tmp_path, *, envelope="pol_lybra_dev_1"):
        return po.DispatchContext(
            card_id="AIPOS-T10", role="executor", round_type="first", delta="",
            workspace_root=tmp_path / "ws", product_repo=tmp_path / "repo",
            gate_url="http://g:7118", envelope=envelope,
            executor_instance="exec.test",
        )

    def test_real_dispatch_zero_placeholder_residual(self, tmp_path):
        """真派路径 kickoff 零占位符残留。"""
        (tmp_path / "ws").mkdir()
        (tmp_path / "repo").mkdir()
        ctx = self._ctx(tmp_path)
        res = po.run_pump_dispatch(
            ctx, dry_run=False, do_claim=False, do_launch=False, do_watch=False,
        )
        assert res["ok"] is True, f"errors={res['errors']}"
        # 零占位符残留
        leftover = re.findall(r"\{[a-z_]+\}", res["kickoff"])
        assert leftover == [], f"真派 kickoff 残留占位符: {leftover}"

    def test_real_dispatch_and_dry_run_same_expansion(self, tmp_path):
        """真派与 dry-run 产出相同展开结果(给定相同输入)。"""
        (tmp_path / "ws").mkdir()
        (tmp_path / "repo").mkdir()

        # dry-run
        ctx_dry = self._ctx(tmp_path)
        res_dry = po.run_pump_dispatch(
            ctx_dry, dry_run=True, do_claim=False, do_launch=False, do_watch=False,
        )
        assert res_dry["ok"] is True

        # 真派
        ctx_real = self._ctx(tmp_path)
        res_real = po.run_pump_dispatch(
            ctx_real, dry_run=False, do_claim=False, do_launch=False, do_watch=False,
        )
        assert res_real["ok"] is True

        # 两者展开后应一致(同样输入 → 同样输出)
        assert res_dry["kickoff"] == res_real["kickoff"]

    def test_real_dispatch_missing_envelope_hard_fails(self, tmp_path):
        """真派 + 缺 envelope → 硬失败(与 dry-run 降级区分)。"""
        (tmp_path / "ws").mkdir()
        (tmp_path / "repo").mkdir()
        ctx = self._ctx(tmp_path, envelope="")
        res = po.run_pump_dispatch(
            ctx, dry_run=False, do_claim=False, do_launch=False, do_watch=False,
        )
        assert res["ok"] is False
        assert res["step"] == "expand_kickoff"

    def test_dogfood_regression_sample(self, tmp_path):
        """dogfood 348 字符样本作回归:真派路径产出无 {...} 残留。"""
        (tmp_path / "ws").mkdir()
        (tmp_path / "repo").mkdir()
        ctx = self._ctx(tmp_path)
        # 生成 kickoff 并展开
        raw = generate_kickoff("AIPOS-T10", "executor", "first", "")
        expanded, missing = po.step_expand_kickoff_lenient(ctx, raw)
        assert missing == [], f"缺失: {missing}"
        leftover = re.findall(r"\{[a-z_]+\}", expanded)
        assert leftover == [], f"dogfood 回归:残留占位符 {leftover}"


# ---------------------------------------------------------------------------
# 修三:kickoff 极简 + 过门句删除
# ---------------------------------------------------------------------------

class TestKickoffSimplification:
    """修三:模板三要素;与 338 卡面契约节重复的【过门】句从模板删除。"""

    def test_first_round_three_elements(self):
        """first 轮 kickoff 包含三要素:卡在哪、已认领、问 gate。"""
        kickoff = generate_kickoff("AIPOS-T20", "executor", "first", "")
        # 要素1:卡在哪
        assert "AIPOS-T20" in kickoff
        assert "5_tasks/queue/pending/aipos-t20.md" in kickoff
        # 要素2:已认领
        assert "认领" in kickoff or "无需再 /claim" in kickoff
        # 要素3:问 gate 下一步
        assert "lybra_gate_guidance" in kickoff
        assert "gate" in kickoff.lower()

    def test_no_guomen_sentence_in_first_round(self):
        """first 轮 kickoff 不含与 338 卡面契约节重复的【过门】句。"""
        kickoff = generate_kickoff("AIPOS-T21", "executor", "first", "")
        assert "【过门】" not in kickoff

    def test_no_guomen_sentence_in_fix_round(self):
        """fix 轮 kickoff 不含【过门】句。"""
        kickoff = generate_kickoff(
            "AIPOS-T22", "executor", "fix", "修复 F-332"
        )
        assert "【过门】" not in kickoff

    def test_no_guomen_sentence_in_resume_round(self):
        """resume 轮 kickoff 不含【过门】句。"""
        kickoff = generate_kickoff(
            "AIPOS-T23", "executor", "resume", ""
        )
        assert "【过门】" not in kickoff

    def test_fix_round_has_constraint(self):
        """fix 轮保留约束(只修审计指出的问题)。"""
        kickoff = generate_kickoff(
            "AIPOS-T24", "executor", "fix", "修复 F-332"
        )
        assert "约束" in kickoff
        assert "修复" in kickoff

    def test_kickoff_with_delta(self):
        """delta 正确注入。"""
        kickoff = generate_kickoff(
            "AIPOS-T25", "executor", "first", "特别关注性能问题"
        )
        assert "特别关注性能问题" in kickoff

    def test_kickoff_no_write_return_instruction(self):
        """模板不再包含 write-return 指令(由 338 卡面契约自动携带)。"""
        kickoff = generate_kickoff("AIPOS-T26", "executor", "first", "")
        assert "write-return" not in kickoff


# ---------------------------------------------------------------------------
# 验收断言(卡内)
# ---------------------------------------------------------------------------

class TestAcceptanceAssertions:
    """卡内验收断言。"""

    def test_session_dir_from_profile_not_hardcoded(self):
        """验收3:会话目录来自档案数据,grep 无 product_repo/.pi 硬编码。"""
        import inspect
        source = inspect.getsource(po._session_dirs_for)
        # 不应有旧的硬编码路径
        assert 'ctx.product_repo / ".pi" / "sessions"' not in source

    def test_real_dispatch_kickoff_clean(self, tmp_path):
        """验收2:真派 kickoff 零占位符残留、零过门重复句。"""
        (tmp_path / "ws").mkdir()
        (tmp_path / "repo").mkdir()
        ctx = po.DispatchContext(
            card_id="AIPOS-T30", role="executor", round_type="first", delta="",
            workspace_root=tmp_path / "ws", product_repo=tmp_path / "repo",
            gate_url="http://g:7118", envelope="pol_lybra_dev_6",
            executor_instance="exec.test",
        )
        res = po.run_pump_dispatch(
            ctx, dry_run=False, do_claim=False, do_launch=False, do_watch=False,
        )
        assert res["ok"] is True
        # 零占位符残留
        assert re.findall(r"\{[a-z_]+\}", res["kickoff"]) == []
        # 零过门重复句
        assert "【过门】" not in res["kickoff"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
