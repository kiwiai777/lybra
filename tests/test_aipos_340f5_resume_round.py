"""AIPOS-340F5 测试 — command_builder 补 resume_round 命令模板。

验收断言:
1. 活体:resume_round 输出可直接执行的完整 resume 命令(贴 JSON);
2. manual 打印与 auto 执行同源(测试);无 runtime-cmd 时命令含明确等待提示(测试);
3. 零回归:既有 action 不受影响。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tools.turn_advancer.command_builder import build_command
from tools.turn_advancer import resolve_next_command


# --- 夹具 ---

def _state_resume(
    task_id="AIPOS-339",
    assigned_to="exec.lybra.kiwiai-dev",
    runtime="pi",
):
    """构造一个能触发 resume_round 的状态(claimed+无return+失败事件)。"""
    return {
        "task_id": task_id,
        "queue_status": "claimed",
        "task_frontmatter": {
            "task_mode": "code",
            "audit": "required",
            "assigned_to": assigned_to,
            "agent_instance": assigned_to,
            "runtime": runtime,
        },
        "latest_claim": {
            "canonical_agent_instance": assigned_to,
            "claim_id": "claim_x",
        },
        "latest_return": None,
        "latest_verdict": None,
        "has_return_artifact": False,
        "has_audit_card": False,
        "events": [
            {"type": "started", "timestamp": "2026-08-05T04:44:30Z"},
            {"type": "launch_failed", "timestamp": "2026-08-05T04:45:11Z", "reason": "silent_hang"},
            {"type": "blocked", "timestamp": "2026-08-05T04:46:51Z", "reason": "max_launch_attempts_exceeded"},
        ],
    }


# 注意:find_active_policy 和 check_retry_limit 在 command_builder 中是函数体内
# from ... import 的,所以 patch 目标必须是源模块的属性。
_POLICY_PATCH = "tools.aipos_cli.policy_resolver.find_active_policy"
_RETRY_PATCH = "tools.turn_advancer.failure_tracker.check_retry_limit"


# --- resume_round 核心测试 ---

class TestResumeRoundCommandBuilder:
    """build_command(action='resume_round', ...) 直接测试。"""

    @patch(_RETRY_PATCH, return_value={
        "action": "allow_retry", "reason": "连败0次", "stats": {},
    })
    @patch(_POLICY_PATCH, return_value="pol_lybra_dev_7")
    def test_resume_round_with_runtime_cmd_config(self, mock_policy, mock_retry, tmp_path):
        """有 runtime-cmd 配置 → 输出完整可执行 CLI 命令(command_type=cli)。"""
        # 创建 runtime_cmds.yaml
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "runtime_cmds.yaml").write_text(
            "pi: 'pi claim --card {kickoff}'\ndefault: 'bash {kickoff}'\n",
            encoding="utf-8",
        )

        state = _state_resume()
        result = build_command("resume_round", state, tmp_path)

        assert result["command_type"] == "cli"
        assert "lybra pump run" in result["copyable_line"]
        assert "--card AIPOS-339" in result["copyable_line"]
        assert "--role executor" in result["copyable_line"]
        assert "--round-type resume" in result["copyable_line"]
        assert f"--workspace-root {tmp_path}" in result["copyable_line"]
        assert "--envelope pol_lybra_dev_7" in result["copyable_line"]
        assert "--runtime pi" in result["copyable_line"]
        assert "--runtime-cmd" in result["copyable_line"]
        assert "--executor-instance exec.lybra.kiwiai-dev" in result["copyable_line"]
        assert result["args"]["card"] == "AIPOS-339"
        assert result["args"]["round_type"] == "resume"
        assert result["args"]["envelope"] == "pol_lybra_dev_7"
        assert result["args"]["runtime_cmd"] == "pi claim --card {kickoff}"

    @patch(_RETRY_PATCH, return_value={
        "action": "allow_retry", "reason": "连败0次", "stats": {},
    })
    @patch(_POLICY_PATCH, return_value="pol_lybra_dev_7")
    def test_resume_round_no_runtime_cmd_wait_human(self, mock_policy, mock_retry, tmp_path):
        """无 runtime-cmd 配置 → command_type=wait_human + 明确等待提示。"""
        state = _state_resume()
        result = build_command("resume_round", state, tmp_path)

        assert result["command_type"] == "wait_human"
        assert "等待人工选择" in result["copyable_line"]
        assert "无 runtime-cmd 配置" in result["copyable_line"]
        assert result["args"]["missing"] == "runtime_cmd"
        # 命令骨架仍在(只是 runtime-cmd 是占位符)
        assert "--card AIPOS-339" in result["copyable_line"]
        assert "--round-type resume" in result["copyable_line"]

    def test_resume_round_no_assigned_to(self, tmp_path):
        """缺 assigned_to → wait_human,不崩溃。"""
        state = _state_resume(assigned_to=None)
        state["task_frontmatter"].pop("assigned_to", None)
        state["task_frontmatter"].pop("agent_instance", None)
        result = build_command("resume_round", state, tmp_path)

        assert result["command_type"] == "wait_human"
        assert "无法构建 resume 命令" in result["copyable_line"]

    @patch(_POLICY_PATCH, return_value=None)
    def test_resume_round_no_envelope(self, mock_policy, tmp_path):
        """无活跃信封 → wait_human。"""
        state = _state_resume()
        result = build_command("resume_round", state, tmp_path)

        assert result["command_type"] == "wait_human"
        assert "无法解析活跃 resume 信封" in result["copyable_line"]

    @patch(_RETRY_PATCH, return_value={
        "action": "trigger_substitution",
        "reason": "连败3次达阈值(3),触发模型顶替",
        "stats": {},
    })
    @patch(_POLICY_PATCH, return_value="pol_lybra_dev_7")
    def test_resume_round_model_substitution_note(self, mock_policy, mock_retry, tmp_path):
        """连败达阈值 → 命令含模型顶替等待提示。"""
        state = _state_resume()
        result = build_command("resume_round", state, tmp_path)

        assert "模型顶替" in result["copyable_line"]
        assert "等待人工选择替代模型" in result["copyable_line"]

    @patch(_RETRY_PATCH, return_value={
        "action": "allow_retry", "reason": "连败0次", "stats": {},
    })
    @patch(_POLICY_PATCH, return_value="pol_lybra_dev_7")
    def test_resume_round_env_runtime_cmd(self, mock_policy, mock_retry, tmp_path, monkeypatch):
        """环境变量 LYBRA_RUNTIME_CMD 优先于配置文件。"""
        monkeypatch.setenv("LYBRA_RUNTIME_CMD", "custom-pi --run {kickoff}")
        state = _state_resume()
        result = build_command("resume_round", state, tmp_path)

        assert result["command_type"] == "cli"
        assert "custom-pi --run {kickoff}" in result["copyable_line"]


# --- wait_executor 测试 ---

class TestWaitExecutorCommandBuilder:
    """build_command(action='wait_executor', ...) 测试。"""

    def test_wait_executor_returns_wait_human(self):
        """wait_executor → command_type=wait_human。"""
        state = _state_resume()
        result = build_command("wait_executor", state, Path("/tmp"))
        assert result["command_type"] == "wait_human"
        assert "等待执行体完成" in result["copyable_line"]


# --- 端到端集成:resolve_next_command ---

class TestResumeRoundIntegration:
    """通过 resolve_next_command 端到端验证 rules→command_builder 管线。"""

    @patch(_RETRY_PATCH, return_value={
        "action": "allow_retry", "reason": "连败0次", "stats": {},
    })
    @patch(_POLICY_PATCH, return_value="pol_lybra_dev_7")
    def test_manual_and_auto_same_source(self, mock_policy, mock_retry, tmp_path):
        """验收断言2:manual 与 auto 模式输出同源(同一 copyable_line)。"""
        # 创建 runtime_cmds.yaml 使命令完整
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "runtime_cmds.yaml").write_text(
            "pi: 'pi claim --card {kickoff}'\n", encoding="utf-8",
        )

        # mock state_reader 返回 resume 态
        state = _state_resume()
        with patch("tools.turn_advancer.resolver.read_task_state", return_value=state):
            manual = resolve_next_command("AIPOS-339", tmp_path, "manual")
            auto = resolve_next_command("AIPOS-339", tmp_path, "auto")

        assert manual["next_action"] == "resume_round"
        assert auto["next_action"] == "resume_round"
        # 同源:copyable_line 一致
        assert manual["copyable_line"] == auto["copyable_line"]
        assert manual["command_type"] == "cli"
        assert "lybra pump run" in manual["copyable_line"]

    @patch(_RETRY_PATCH, return_value={
        "action": "allow_retry", "reason": "连败0次", "stats": {},
    })
    @patch(_POLICY_PATCH, return_value="pol_lybra_dev_7")
    def test_no_runtime_cmd_wait_prompt(self, mock_policy, mock_retry, tmp_path):
        """验收断言2(续):无 runtime-cmd 时命令含明确等待提示。"""
        state = _state_resume()
        with patch("tools.turn_advancer.resolver.read_task_state", return_value=state):
            result = resolve_next_command("AIPOS-339", tmp_path, "manual")

        assert result["next_action"] == "resume_round"
        assert result["command_type"] == "wait_human"
        assert "等待人工选择" in result["copyable_line"]
        assert "无 runtime-cmd 配置" in result["copyable_line"]


# --- 零回归 ---

class TestZeroRegression:
    """验收断言3:既有 action 不受影响。"""

    def test_unknown_action_still_unknown(self):
        """未知 action 仍走 else 分支。"""
        state = _state_resume()
        result = build_command("nonexistent_action", state, Path("/tmp"))
        assert result["command_type"] == "unknown"
        assert "未知动作" in result["copyable_line"]

    def test_done_still_done(self):
        state = _state_resume()
        result = build_command("done", state, Path("/tmp"))
        assert result["command_type"] == "done"

    def test_wait_human_still_wait_human(self):
        state = _state_resume()
        result = build_command("wait_human", state, Path("/tmp"))
        assert result["command_type"] == "wait_human"
        assert "等待人工判断" in result["copyable_line"]

    def test_finalize_still_cli(self):
        state = _state_resume()
        result = build_command("finalize", state, Path("/tmp"))
        assert result["command_type"] == "cli"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
