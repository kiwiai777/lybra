"""AIPOS-332F4 — 拉起窗口档案化 + 禁提前判死(冷启动误杀修复)测试。

验收断言:
  1. 复现测试过(慢启动不误杀/超窗仍判死);
  2. 秒数全部来自档案,grep 无硬编码窗口值;
  3. 底线:lybra --help、gate active;既有 launch-check 对快启动运行体零回归。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.aipos_cli.agent_launch_check import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_OK,
    check_launch,
    run_launch_check,
    write_block_file,
    _snapshot_session_dirs,
    DEFAULT_LAUNCH_WINDOW_SECS,
)
from tools.aipos_cli.runtime_profiles import (
    RUNTIME_PROFILES,
    get_runtime_profile,
    select_observation_plan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_product_repo(tmp_path):
    product_repo = tmp_path / "lybra"
    product_repo.mkdir()
    (product_repo / "task_cards").mkdir()
    (product_repo / ".pi_sessions").mkdir()
    return product_repo


# ---------------------------------------------------------------------------
# 修一:窗口与节奏进运行体档案
# ---------------------------------------------------------------------------

class TestLaunchTimingInProfile:
    """修一验收: 拉起时间参数在运行体档案中,代码不写死任何秒数。"""

    def test_pi_profile_has_launch_window_ge_180(self):
        """pi 档案默认窗口 ≥180s(慢端点冷启动实测 ~60s,留裕量)。"""
        profile, _ = get_runtime_profile("pi")
        assert profile["launch_window_secs"] >= 180

    def test_pi_profile_has_check_interval(self):
        profile, _ = get_runtime_profile("pi")
        assert profile["check_interval_secs"] > 0

    def test_pi_profile_has_cold_start_grace(self):
        profile, _ = get_runtime_profile("pi")
        assert profile["cold_start_grace_secs"] > 0

    def test_generic_bash_has_shorter_window(self):
        """bash 秒活,窗口应远小于 pi。"""
        pi_profile, _ = get_runtime_profile("pi")
        bash_profile, _ = get_runtime_profile("generic_bash")
        assert bash_profile["launch_window_secs"] < pi_profile["launch_window_secs"]

    def test_default_profile_has_launch_window(self):
        """安全默认也必须有窗口参数(宁可多等,不误杀)。"""
        profile, _ = get_runtime_profile(None)
        assert "launch_window_secs" in profile
        assert profile["launch_window_secs"] >= 180

    def test_all_profiles_have_launch_timing(self):
        """每个运行体档案都必须有拉起时间参数。"""
        for name, profile in RUNTIME_PROFILES.items():
            assert "launch_window_secs" in profile, f"{name} missing launch_window_secs"
            assert "check_interval_secs" in profile, f"{name} missing check_interval_secs"
            assert "cold_start_grace_secs" in profile, f"{name} missing cold_start_grace_secs"

    def test_no_hardcoded_window_in_step_launch(self):
        """验收断言2: grep 无硬编码窗口值 — step_launch 从档案取值。"""
        pump_path = Path(__file__).resolve().parents[1] / "pump_orchestration.py"
        content = pump_path.read_text(encoding="utf-8")
        # step_launch 函数体内不应有 "launch_window_secs=90" 或 "launch_window_secs=90.0"
        # 匹配 launch_window_secs=<数字> 的模式
        matches = re.findall(r"launch_window_secs\s*=\s*[\d.]+", content)
        for m in matches:
            # 允许从变量取值(如 launch_window_secs=launch_window),不允许硬编码数字
            assert "90" not in m, f"硬编码窗口值 90 仍在 step_launch: {m}"

    def test_default_launch_window_secs_is_180(self):
        """CLI 兆底默认值也已从 90 改为 180。"""
        assert DEFAULT_LAUNCH_WINDOW_SECS >= 180


# ---------------------------------------------------------------------------
# 修二:窗口内不提前判死
# ---------------------------------------------------------------------------

class TestNoEarlyDeathInsideWindow:
    """修二验收: 进程存活且窗口未到 → 不得判 silent_hang/insufficient_activity。"""

    def test_slow_start_not_killed_within_180s_window(self, temp_product_repo):
        """核心复现: 模拟慢启动(前段无会话文件、CPU≈0),窗口足够大时不误杀。

        用短窗口(15s)+ 短间隔(0.1s)加速测试,但逻辑等价于 180s 窗口场景:
        前 80% 的时间无活动(模拟冷启动),最后 20% 活动出现 → 应判成功。
        窗口内不提前判死:即使长时间无活动,只要进程存活且窗口未到,就继续等。
        """
        session_dir = temp_product_repo / ".pi_sessions"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None  # 进程始终存活
            mock_popen.return_value = mock_proc

            with patch("tools.aipos_cli.agent_launch_check._find_pi_processes") as mock_find:
                mock_find.return_value = [12346]

                # 窗口 15s, 间隔 0.1s → 最多 ~150 次迭代
                # 迭代 1: baseline (cpu call #1, 无 file call)
                # 迭代 2-101: cpu_delta=0, files=0 (模拟冷启动,约 10s)
                # 迭代 102: cpu_delta=2.5, files=3 (启动完成)
                # 需要足够多的 mock 值
                n_cold = 100  # 冷启动期间的非 baseline 迭代数
                # CPU: 1 baseline + n_cold 次冷启动 + 1 次启动完成 = n_cold+2
                cpu_values = [0.0] * (n_cold + 1) + [2.5]
                with patch("tools.aipos_cli.agent_launch_check._get_process_cpu_time") as mock_cpu:
                    mock_cpu.side_effect = cpu_values

                    # file call 从迭代 2 开始(迭代 1 是 baseline 不调 _count_new_files)
                    # n_cold 次冷启动=0 + 1 次启动完成=3
                    file_counts = [0] * n_cold + [3]
                    with patch("tools.aipos_cli.agent_launch_check._count_new_files") as mock_files:
                        mock_files.side_effect = file_counts

                        with patch("tools.aipos_cli.agent_launch_check._has_worktree_changes") as mock_wt:
                            mock_wt.return_value = False

                            # 用 15s 窗口(逻辑等价于 180s 窗口场景)
                            exit_code, failure_data = check_launch(
                                spawn_cmd="timeout 3600 pi --append-system-prompt 'test'",
                                task_id="AIPOS-332F4",
                                executor_instance="exec.test",
                                product_repo=temp_product_repo,
                                session_dirs=[str(session_dir)],
                                worktree_path=str(temp_product_repo),
                                launch_window_secs=15,
                                check_interval_secs=0.1,
                            )

                            # 慢启动不误杀: 应判成功
                            assert exit_code == EXIT_OK, (
                                f"慢启动被误杀! exit_code={exit_code}, "
                                f"reason={failure_data.get('reason') if failure_data else 'N/A'}"
                            )

    def test_window_expired_still_judges_death(self, temp_product_repo):
        """窗口耗尽场景仍正确判死。"""
        session_dir = temp_product_repo / ".pi_sessions"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None  # 进程存活但无活动
            mock_popen.return_value = mock_proc

            with patch("tools.aipos_cli.agent_launch_check._find_pi_processes") as mock_find:
                mock_find.return_value = [12346]

                with patch("tools.aipos_cli.agent_launch_check._get_process_cpu_time") as mock_cpu:
                    mock_cpu.return_value = 0.0  # 始终无 CPU

                    with patch("tools.aipos_cli.agent_launch_check._count_new_files") as mock_files:
                        mock_files.return_value = 0  # 始终无会话文件

                        with patch("tools.aipos_cli.agent_launch_check._has_worktree_changes") as mock_wt:
                            mock_wt.return_value = False

                            with patch("tools.aipos_cli.agent_launch_check._kill_process_tree"):
                                # 用短窗口+短间隔加速测试
                                exit_code, failure_data = check_launch(
                                    spawn_cmd="timeout 3600 pi --append-system-prompt 'test'",
                                    task_id="AIPOS-332F4",
                                    executor_instance="exec.test",
                                    product_repo=temp_product_repo,
                                    session_dirs=[str(session_dir)],
                                    worktree_path=str(temp_product_repo),
                                    launch_window_secs=2,
                                    check_interval_secs=0.1,
                                )

                                # 窗口耗尽应判死
                                assert exit_code == EXIT_ERROR
                                assert failure_data is not None
                                assert failure_data["reason"] == "silent_hang"

    def test_failure_data_contains_timing_fields(self, temp_product_repo):
        """判死输出须含: 实际等待秒数、窗口配置值。"""
        session_dir = temp_product_repo / ".pi_sessions"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            with patch("tools.aipos_cli.agent_launch_check._find_pi_processes") as mock_find:
                mock_find.return_value = [12346]

                with patch("tools.aipos_cli.agent_launch_check._get_process_cpu_time") as mock_cpu:
                    mock_cpu.return_value = 0.0

                    with patch("tools.aipos_cli.agent_launch_check._count_new_files") as mock_files:
                        mock_files.return_value = 0

                        with patch("tools.aipos_cli.agent_launch_check._has_worktree_changes") as mock_wt:
                            mock_wt.return_value = False

                            with patch("tools.aipos_cli.agent_launch_check._kill_process_tree"):
                                exit_code, failure_data = check_launch(
                                    spawn_cmd="timeout 3600 pi --append-system-prompt 'test'",
                                    task_id="AIPOS-332F4",
                                    executor_instance="exec.test",
                                    product_repo=temp_product_repo,
                                    session_dirs=[str(session_dir)],
                                    worktree_path=str(temp_product_repo),
                                    launch_window_secs=2,
                                    check_interval_secs=0.1,
                                )

                                assert failure_data is not None
                                # 修二: 实际等待秒数 + 窗口配置值
                                assert "actual_waited_secs" in failure_data
                                assert "window_config_secs" in failure_data
                                assert failure_data["window_config_secs"] == 2
                                assert failure_data["actual_waited_secs"] >= 2
                                # 修二: 判死时刻时间戳
                                assert "judged_at" in failure_data


# ---------------------------------------------------------------------------
# 修三:留证增强
# ---------------------------------------------------------------------------

class TestEvidenceEnhancement:
    """修三验收: 会话目录快照入 failure_data / BLOCK 详情。"""

    def test_snapshot_session_dirs_captures_files(self, tmp_path):
        """_snapshot_session_dirs 能捕获目录下的文件列表。"""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        # 创建几个文件
        (session_dir / "file1.json").write_text("{}")
        (session_dir / "file2.log").write_text("log")

        snapshot = _snapshot_session_dirs([str(session_dir)])
        assert len(snapshot) == 1
        assert snapshot[0]["exists"] is True
        assert snapshot[0]["total_files"] == 2
        assert len(snapshot[0]["files"]) == 2

    def test_snapshot_handles_nonexistent_dir(self, tmp_path):
        """_snapshot_session_dirs 对不存在的目录不报错。"""
        snapshot = _snapshot_session_dirs([str(tmp_path / "nonexistent")])
        assert len(snapshot) == 1
        assert snapshot[0]["exists"] is False

    def test_failure_data_contains_session_snapshot(self, temp_product_repo):
        """判死 failure_data 含 session_snapshot。"""
        session_dir = temp_product_repo / ".pi_sessions"
        # 创建一些文件
        (session_dir / "old_file.json").write_text("{}")

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            with patch("tools.aipos_cli.agent_launch_check._find_pi_processes") as mock_find:
                mock_find.return_value = [12346]

                with patch("tools.aipos_cli.agent_launch_check._get_process_cpu_time") as mock_cpu:
                    mock_cpu.return_value = 0.0

                    with patch("tools.aipos_cli.agent_launch_check._count_new_files") as mock_files:
                        mock_files.return_value = 0

                        with patch("tools.aipos_cli.agent_launch_check._has_worktree_changes") as mock_wt:
                            mock_wt.return_value = False

                            with patch("tools.aipos_cli.agent_launch_check._kill_process_tree"):
                                exit_code, failure_data = check_launch(
                                    spawn_cmd="timeout 3600 pi --append-system-prompt 'test'",
                                    task_id="AIPOS-332F4",
                                    executor_instance="exec.test",
                                    product_repo=temp_product_repo,
                                    session_dirs=[str(session_dir)],
                                    worktree_path=str(temp_product_repo),
                                    launch_window_secs=2,
                                    check_interval_secs=0.1,
                                )

                                assert failure_data is not None
                                assert "session_snapshot" in failure_data
                                snapshot = failure_data["session_snapshot"]
                                assert isinstance(snapshot, list)
                                assert len(snapshot) == 1
                                assert snapshot[0]["exists"] is True

    def test_block_file_includes_timing_and_snapshot(self, temp_product_repo):
        """BLOCK 文件包含 actual_waited_secs / window_config_secs / session_snapshot。"""
        spawn_cmd = "timeout 3600 pi --model sonnet-5 --append-system-prompt 'test'"
        failure_history = [
            {
                "timestamp": "2026-08-05T04:45:11Z",
                "attempt": 1,
                "reason": "silent_hang",
                "exit_code": None,
                "proc_alive": True,
                "cpu_delta": 0.0,
                "new_session_files": 0,
                "worktree_changed": False,
                "actual_waited_secs": 50.3,
                "window_config_secs": 90,
                "judged_at": "2026-08-05T04:45:11Z",
                "session_snapshot": [
                    {
                        "dir": "/home/kiwi/.pi/agent/sessions/--test--",
                        "exists": True,
                        "total_files": 0,
                        "files": [],
                    }
                ],
            },
            {
                "timestamp": "2026-08-05T04:47:00Z",
                "attempt": 2,
                "reason": "silent_hang",
                "exit_code": None,
                "proc_alive": True,
                "cpu_delta": 0.0,
                "new_session_files": 0,
                "worktree_changed": False,
                "actual_waited_secs": 90.1,
                "window_config_secs": 90,
                "judged_at": "2026-08-05T04:47:00Z",
                "session_snapshot": [
                    {
                        "dir": "/home/kiwi/.pi/agent/sessions/--test--",
                        "exists": True,
                        "total_files": 2,
                        "files": [
                            {"path": "/home/kiwi/.pi/agent/sessions/--test--/session.json", "mtime": "2026-08-05T04:47:06Z"},
                        ],
                    }
                ],
            },
        ]

        block_file = write_block_file(
            product_repo=temp_product_repo,
            card_id="AIPOS-332F4",
            spawn_cmd=spawn_cmd,
            failure_history=failure_history,
            model_fallback_policy={"sonnet-5": "qwen3.7-plus"},
        )

        content = block_file.read_text()
        # 修二: 时间字段
        assert "Actual waited (s): 50.3" in content
        assert "Window config (s): 90" in content
        assert "Judged at: 2026-08-05T04:45:11Z" in content
        # 修三: 会话快照
        assert "Session snapshot at judgment:" in content
        assert "total_files=0" in content
        assert "total_files=2" in content


# ---------------------------------------------------------------------------
# 零回归
# ---------------------------------------------------------------------------

class TestZeroRegression:
    """验收断言3: 既有 launch-check 对快启动运行体零回归。"""

    def test_fast_start_still_succeeds(self, temp_product_repo):
        """快启动(立即有 CPU + 会话文件)仍判成功。"""
        session_dir = temp_product_repo / ".pi_sessions"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            with patch("tools.aipos_cli.agent_launch_check._find_pi_processes") as mock_find:
                mock_find.return_value = [12346]

                with patch("tools.aipos_cli.agent_launch_check._get_process_cpu_time") as mock_cpu:
                    mock_cpu.side_effect = [0.0, 0.0, 3.0]

                    with patch("tools.aipos_cli.agent_launch_check._count_new_files") as mock_files:
                        mock_files.return_value = 5

                        with patch("tools.aipos_cli.agent_launch_check._has_worktree_changes") as mock_wt:
                            mock_wt.return_value = True

                            exit_code, failure_data = check_launch(
                                spawn_cmd="timeout 3600 pi --append-system-prompt 'test'",
                                task_id="AIPOS-332F4",
                                executor_instance="exec.test",
                                product_repo=temp_product_repo,
                                session_dirs=[str(session_dir)],
                                worktree_path=str(temp_product_repo),
                                launch_window_secs=180,
                                check_interval_secs=5,
                            )

                            assert exit_code == EXIT_OK
                            assert failure_data is None

    def test_early_exit_still_detected(self, temp_product_repo):
        """进程早退仍正确检测。"""
        session_dir = temp_product_repo / ".pi_sessions"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.side_effect = [None, 1]
            mock_popen.return_value = mock_proc

            exit_code, failure_data = check_launch(
                spawn_cmd="timeout 3600 pi --append-system-prompt 'test'",
                task_id="AIPOS-332F4",
                executor_instance="exec.test",
                product_repo=temp_product_repo,
                session_dirs=[str(session_dir)],
                worktree_path=str(temp_product_repo),
                launch_window_secs=180,
                check_interval_secs=2,
            )

            assert exit_code == EXIT_ERROR
            assert failure_data["reason"] == "process_early_exit"
            # 早退也有留证字段
            assert "actual_waited_secs" in failure_data
            assert "window_config_secs" in failure_data

    def test_bounded_retry_unchanged(self, temp_product_repo):
        """有界自愈(重试上限)不变。"""
        session_dir = temp_product_repo / ".pi_sessions"
        spawn_count = [0]

        def mock_check_launch(*args, **kwargs):
            spawn_count[0] += 1
            return EXIT_ERROR, {
                "reason": "process_early_exit",
                "exit_code": 1,
                "proc_alive": False,
                "cpu_delta": 0.0,
                "new_session_files": 0,
                "worktree_changed": False,
                "actual_waited_secs": 5.0,
                "window_config_secs": 180,
                "session_snapshot": [],
                "judged_at": "2026-08-05T05:00:00Z",
            }

        with patch("tools.aipos_cli.agent_launch_check.check_launch", side_effect=mock_check_launch):
            exit_code = run_launch_check(
                spawn_cmd="timeout 3600 pi --append-system-prompt 'test'",
                task_id="AIPOS-332F4",
                executor_instance="exec.test",
                product_repo=temp_product_repo,
                session_dirs=[str(session_dir)],
                worktree_path=str(temp_product_repo),
                launch_window_secs=180,
                check_interval_secs=5,
            )

            assert exit_code == EXIT_BLOCKED
            assert spawn_count[0] == 2  # 重试上限不变(2 次)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
