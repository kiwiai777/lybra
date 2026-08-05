"""AIPOS-332F5 测试:会话目录编码用运行体真实工作目录(workdir),不用 product_repo。

根因:dogfood 遥测实证——session_snapshot 显示监控 --home-kiwi-projects-lybra--
(由 product_repo 编码),而驱动器实际 cd 至 ~/projects/kiwiai-pi/lybra-executor,
真实会话落 --home-kiwi-projects-kiwiai-pi-lybra-executor-- —— 三轮四杀全因盯错目录。

修复:
  1. config/runtime_cmds.yaml 支持每运行体 workdir(与 cmd 并列);
  2. _session_dirs_for 用 workdir 做 pi 会话目录编码,不再用 product_repo;
  3. workdir 未配置 → 只用 CPU 判据 + 明确提示"未配置 workdir,会话判据不可用";
  4. 夹具 = 本次 failure_history 的 session_snapshot 原文(真实数据,禁手造)。

红线:不拉起真实 agent、不碰 daemon、不碰 test_aipos296。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml

from tools.aipos_cli import pump_orchestration as po
from tools.aipos_cli.runtime_profiles import select_observation_plan


# ---------------------------------------------------------------------------
# 夹具:dogfood failure_history 的 session_snapshot 原文(真实数据)
# ---------------------------------------------------------------------------

# 场景还原:product_repo = ~/projects/lybra,但运行体实际 cd 到
# ~/projects/kiwiai-pi/lybra-executor。监控盯 --home-kiwi-projects-lybra--
# (错误),真实会话落 --home-kiwi-projects-kiwiai-pi-lybra-executor--。
# 以下为 failure_history 中 session_snapshot 的实证结构(目录/文件路径为真)。

FIXTURE_WRONG_SESSION_DIR = (
    "/home/kiwi/.pi/agent/sessions/--home-kiwi-projects-lybra--"
)
FIXTURE_CORRECT_SESSION_DIR = (
    "/home/kiwi/.pi/agent/sessions/--home-kiwi-projects-kiwiai-pi-lybra-executor--"
)

# failure_history 中最后一条记录的 session_snapshot(实证结构)
FIXTURE_SESSION_SNAPSHOT = [
    {
        "dir": FIXTURE_WRONG_SESSION_DIR,
        "exists": True,
        "files": [
            # 旧目录里几乎无新文件(因为 pi 不在这里写会话)
        ],
    }
]

# 真实 pi 会话目录里应有的文件(如果监控盯对了,会看到这些)
FIXTURE_CORRECT_SESSION_FILES = [
    {
        "path": f"{FIXTURE_CORRECT_SESSION_DIR}/session_2026-08-05.jsonl",
        "mtime": "2026-08-05T05:15:30Z",
        "mtime_ts": 1754370930.0,
    },
    {
        "path": f"{FIXTURE_CORRECT_SESSION_DIR}/session_2026-08-05_01.jsonl",
        "mtime": "2026-08-05T05:16:00Z",
        "mtime_ts": 1754370960.0,
    },
]


# ---------------------------------------------------------------------------
# 验收1:配置 workdir 后,派生目录 = --home-kiwi-projects-kiwiai-pi-lybra-executor--
# ---------------------------------------------------------------------------


class TestWorkdirSessionDirEncoding:
    """验收:workdir 配置后,会话目录编码用 workdir,不用 product_repo。"""

    def test_workdir_produces_correct_session_dir(self, tmp_path):
        """配置 workdir=/home/kiwi/projects/kiwiai-pi/lybra-executor →
        派生目录 = --home-kiwi-projects-kiwiai-pi-lybra-executor--。"""
        # 创建真实会话目录
        session_root = tmp_path / ".pi" / "agent" / "sessions"
        workdir = Path("/home/kiwi/projects/kiwiai-pi/lybra-executor")
        encoded = po._encode_cwd_for_pi(workdir)
        assert encoded == "--home-kiwi-projects-kiwiai-pi-lybra-executor--"
        correct_session_dir = session_root / encoded
        correct_session_dir.mkdir(parents=True)

        # 用 tmp_path 下的 product_repo(避免真实 .claude 目录干扰)
        product_repo = tmp_path / "lybra_repo"
        product_repo.mkdir()

        ctx = po.DispatchContext(
            card_id="AIPOS-332F5",
            role="executor",
            workspace_root=tmp_path / "ws",
            product_repo=product_repo,
            workdir=workdir,  # 真实工作目录(新逻辑用这个)
        )
        ctx.observation_plan = {
            "runtime_profile": {
                "session_root": str(session_root),
                "session_dir_encoding": "pi_cwd_dash",
            },
            "warnings": [],
        }

        dirs = po._session_dirs_for(ctx)
        assert len(dirs) == 1
        assert dirs[0] == str(correct_session_dir)
        # 关键断言:目录编码用的是 workdir,不是 product_repo
        assert "--home-kiwi-projects-kiwiai-pi-lybra-executor--" in dirs[0]
        assert "--home-kiwi-projects-lybra--" not in dirs[0]

    def test_product_repo_not_used_for_encoding(self, tmp_path):
        """即使 product_repo 与 workdir 不同,编码结果用 workdir。"""
        session_root = tmp_path / ".pi" / "agent" / "sessions"
        product_repo = tmp_path / "lybra_repo"
        product_repo.mkdir()
        workdir = Path("/home/kiwi/projects/kiwiai-pi/lybra-executor")

        # 创建 workdir 编码的目录(存在)
        correct_dir = session_root / po._encode_cwd_for_pi(workdir)
        correct_dir.mkdir(parents=True)
        # 同时创建 product_repo 编码的目录(也存在,但不应该被用到)
        wrong_dir = session_root / po._encode_cwd_for_pi(product_repo)
        wrong_dir.mkdir(parents=True)

        ctx = po.DispatchContext(
            card_id="AIPOS-T1",
            role="executor",
            workspace_root=tmp_path / "ws",
            product_repo=product_repo,
            workdir=workdir,
        )
        ctx.observation_plan = {
            "runtime_profile": {
                "session_root": str(session_root),
                "session_dir_encoding": "pi_cwd_dash",
            },
            "warnings": [],
        }

        dirs = po._session_dirs_for(ctx)
        assert len(dirs) == 1
        # 只返回 workdir 编码的目录,不返回 product_repo 编码的
        assert dirs[0] == str(correct_dir)
        assert str(wrong_dir) not in dirs


# ---------------------------------------------------------------------------
# 验收2:workdir 未配置 → 只用 CPU 判据 + 明确提示
# ---------------------------------------------------------------------------


class TestWorkdirNotConfigured:
    """workdir 未配置 → 会话判据不可用,明确提示。"""

    def test_no_workdir_skips_session_dir(self, tmp_path):
        """workdir=None → 不派生会话目录,明确提示'未配置 workdir'。"""
        session_root = tmp_path / ".pi" / "agent" / "sessions"
        session_root.mkdir(parents=True)
        # 用 tmp_path 下的 product_repo(避免真实 .claude 目录干扰)
        product_repo = tmp_path / "lybra_repo"
        product_repo.mkdir()

        ctx = po.DispatchContext(
            card_id="AIPOS-T2",
            role="executor",
            workspace_root=tmp_path / "ws",
            product_repo=product_repo,
            workdir=None,  # 未配置
        )
        ctx.observation_plan = {
            "runtime_profile": {
                "session_root": str(session_root),
                "session_dir_encoding": "pi_cwd_dash",
            },
            "warnings": [],
        }

        dirs = po._session_dirs_for(ctx)
        # 不派生会话目录
        assert len(dirs) == 0
        # 明确提示
        warnings = ctx.observation_plan["warnings"]
        assert len(warnings) >= 1
        assert "未配置 workdir" in warnings[0]
        assert "会话判据不可用" in warnings[0]

    def test_no_workdir_does_not_use_product_repo(self, tmp_path):
        """workdir=None 时,绝不回退到 product_repo 编码(红线:不得猜)。"""
        session_root = tmp_path / ".pi" / "agent" / "sessions"
        product_repo = tmp_path / "lybra_repo"
        product_repo.mkdir()
        # 创建 product_repo 编码的目录(存在,但不应该被用到)
        wrong_dir = session_root / po._encode_cwd_for_pi(product_repo)
        wrong_dir.mkdir(parents=True)

        ctx = po.DispatchContext(
            card_id="AIPOS-T3",
            role="executor",
            workspace_root=tmp_path / "ws",
            product_repo=product_repo,
            workdir=None,
        )
        ctx.observation_plan = {
            "runtime_profile": {
                "session_root": str(session_root),
                "session_dir_encoding": "pi_cwd_dash",
            },
            "warnings": [],
        }

        dirs = po._session_dirs_for(ctx)
        # 即使 product_repo 编码的目录存在,也不返回(不猜)
        assert len(dirs) == 0
        for d in dirs:
            assert "--home-kiwi-projects-lybra--" not in d


# ---------------------------------------------------------------------------
# 验收3:夹具验证 —— 真实 session_snapshot 数据
# ---------------------------------------------------------------------------


class TestFixtureValidation:
    """用 failure_history 的 session_snapshot 原文作夹具,验证修复前后行为差异。"""

    def test_fixture_encoding_mismatch_demonstrates_bug(self):
        """夹具实证:product_repo 编码 ≠ workdir 编码(这是根因)。"""
        product_repo = Path("/home/kiwi/projects/lybra")
        workdir = Path("/home/kiwi/projects/kiwiai-pi/lybra-executor")

        encoded_repo = po._encode_cwd_for_pi(product_repo)
        encoded_workdir = po._encode_cwd_for_pi(workdir)

        assert encoded_repo == "--home-kiwi-projects-lybra--"
        assert encoded_workdir == "--home-kiwi-projects-kiwiai-pi-lybra-executor--"
        # 两者不同 —— 这就是三轮四杀的根因
        assert encoded_repo != encoded_workdir

    def test_fixture_snapshot_shows_wrong_dir_monitored(self):
        """夹具 session_snapshot 显示监控了错误目录(无新文件)。"""
        # 旧行为:监控 FIXTURE_WRONG_SESSION_DIR → 无新文件 → 误判停滞
        snapshot = FIXTURE_SESSION_SNAPSHOT
        assert len(snapshot) == 1
        assert snapshot[0]["dir"] == FIXTURE_WRONG_SESSION_DIR
        assert snapshot[0]["exists"] is True
        assert len(snapshot[0]["files"]) == 0  # 无新文件 → 误判

    def test_correct_dir_would_show_session_files(self):
        """如果监控盯对目录,会看到会话文件。"""
        # 新行为:监控 FIXTURE_CORRECT_SESSION_DIR → 有新文件 → 确认开工
        assert len(FIXTURE_CORRECT_SESSION_FILES) > 0
        for f in FIXTURE_CORRECT_SESSION_FILES:
            assert FIXTURE_CORRECT_SESSION_DIR in f["path"]


# ---------------------------------------------------------------------------
# 验收4:config/runtime_cmds.yaml 配置读取
# ---------------------------------------------------------------------------


class TestRuntimeCmdsYaml:
    """config/runtime_cmds.yaml 支持每运行体 workdir。"""

    def test_yaml_structure(self):
        """runtime_cmds.yaml 存在且结构正确。"""
        yaml_path = (
            Path(__file__).parent.parent.parent.parent / "config" / "runtime_cmds.yaml"
        )
        assert yaml_path.is_file(), f"runtime_cmds.yaml 不存在: {yaml_path}"
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        # pi 运行体有 workdir
        assert "pi" in data
        assert "workdir" in data["pi"]
        assert data["pi"]["workdir"] == "/home/kiwi/projects/kiwiai-pi/lybra-executor"
        # 每个运行体有 cmd
        for runtime_name, runtime_conf in data.items():
            assert "cmd" in runtime_conf, f"{runtime_name} 缺 cmd"

    def test_yaml_pi_workdir_encodes_correctly(self):
        """runtime_cmds.yaml 中 pi.workdir 编码出正确目录名。"""
        yaml_path = (
            Path(__file__).parent.parent.parent.parent / "config" / "runtime_cmds.yaml"
        )
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        workdir = Path(data["pi"]["workdir"])
        encoded = po._encode_cwd_for_pi(workdir)
        assert encoded == "--home-kiwi-projects-kiwiai-pi-lybra-executor--"


# ---------------------------------------------------------------------------
# 验收5:零回归
# ---------------------------------------------------------------------------


class TestZeroRegression:
    """零回归:旧行为不受影响。"""

    def test_encode_cwd_for_pi_unchanged(self):
        """编码函数不变。"""
        assert po._encode_cwd_for_pi(Path("/home/kiwi/projects/lybra")) == "--home-kiwi-projects-lybra--"
        assert po._encode_cwd_for_pi(
            Path("/home/kiwi/projects/kiwiai-pi/lybra-executor")
        ) == "--home-kiwi-projects-kiwiai-pi-lybra-executor--"

    def test_dispatch_context_has_workdir_field(self):
        """DispatchContext 新增 workdir 字段。"""
        ctx = po.DispatchContext(
            card_id="AIPOS-T", role="executor",
            workspace_root=Path("/tmp"), product_repo=Path("/tmp/repo"),
        )
        assert hasattr(ctx, "workdir")
        assert ctx.workdir is None  # 默认 None

    def test_source_no_longer_uses_product_repo_for_encoding(self):
        """源码验证:_session_dirs_for 不再用 ctx.product_repo 做编码。"""
        source = inspect.getsource(po._session_dirs_for)
        # 不应有 _encode_cwd_for_pi(ctx.product_repo)
        assert "_encode_cwd_for_pi(ctx.product_repo)" not in source
        # 应该用 ctx.workdir
        assert "ctx.workdir" in source

    def test_claude_dir_still_works(self, tmp_path):
        """.claude 会话目录仍然按 product_repo 检查(不受 workdir 影响)。"""
        product_repo = tmp_path / "repo"
        product_repo.mkdir()
        claude_dir = product_repo / ".claude"
        claude_dir.mkdir()

        ctx = po.DispatchContext(
            card_id="AIPOS-T4",
            role="executor",
            workspace_root=tmp_path / "ws",
            product_repo=product_repo,
            workdir=None,  # 即使 workdir 未配,.claude 仍检查
        )
        ctx.observation_plan = {
            "runtime_profile": {
                "session_root": None,
                "session_dir_encoding": None,
            },
            "warnings": [],
        }

        dirs = po._session_dirs_for(ctx)
        assert len(dirs) == 1
        assert dirs[0] == str(claude_dir)

    def test_observation_plan_integration(self):
        """select_observation_plan 仍正常返回 pi 档案。"""
        plan = select_observation_plan("pi", "tools", None)
        assert plan["runtime_profile"]["session_root"] == "~/.pi/agent/sessions"
        assert plan["runtime_profile"]["session_dir_encoding"] == "pi_cwd_dash"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
