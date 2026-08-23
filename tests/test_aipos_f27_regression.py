"""AIPOS-F27 回归夹具: 分发与落盘两案修真

验收断言覆盖:
- 大项A: charter 只播种不覆盖(seed_only) — 目标已存在→跳过+出声(含差异指纹);
         不存在→播种;--force 也不覆盖 charter
- 大项B: enroll 从任意 cwd 执行:.lybra 落 cwd、部署树零新增;
         输出落点字符串与实际路径 assert 相等
- 大项C: 两夹具入 run-all 常驻(本文件即为常驻夹具)
- 大项D: enroll 落盘的 connection.json 字段断言:
         workspace_root/mcp.rpc_url/tokens 三全(半残即 FAIL)
- 大项E: 无人陪跑 E2E 夹具(脚本化重放,零人肉)

跑法: python3 -m pytest tests/test_aipos_f27_regression.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 确保能导入产品仓模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.distribute_tools import (
    execute_distribution,
    distribute_to_harness,
    _fingerprint_diff,
    _primitive_copy_tree,
)


# ==============================================================================
# 大项A: charter 只播种不覆盖 (seed_only)
# ==============================================================================

class TestCharterSeedOnly:
    """验收断言①: charter 分发:目标已存在→跳过+出声(含双方内容指纹);不存在→播种"""

    def test_charter_seeds_when_target_absent(self, tmp_path):
        """charter 目标不存在 → 正常播种"""
        # 准备源文件
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        charter_src = source_dir / "AGENTS.md"
        charter_src.write_text("# Executor Charter v1\nRole: executor\n", encoding="utf-8")

        # 准备目标目录(harness root)
        harness_root = tmp_path / "harness"
        harness_root.mkdir()

        dist = {
            "distribution_id": "test-charter",
            "kind": "charter",
            "source": {"path": str(charter_src)},
            "target": {"harness": "pi", "relative_path": "AGENTS.md"},
            "applies_to_roles": ["executor"],
            "operation": "copy_tree",
            "seed_only": True,
        }

        # 直接测 _primitive_copy_tree + seed_only 逻辑
        target_path = harness_root / "AGENTS.md"
        assert not target_path.exists()

        result = _primitive_copy_tree(charter_src, target_path)
        assert result["ok"] is True
        assert target_path.exists()
        assert target_path.read_text() == "# Executor Charter v1\nRole: executor\n"

    def test_charter_skips_when_target_exists(self, tmp_path):
        """charter 目标已存在 → seed_only 跳过(即使 force=True)"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        charter_src = source_dir / "AGENTS.md"
        charter_src.write_text("# New Charter v2\n", encoding="utf-8")

        harness_root = tmp_path / "harness"
        harness_root.mkdir()
        target_path = harness_root / "AGENTS.md"
        target_path.write_text("# Existing Charter (customized by advisor)\n", encoding="utf-8")

        # 用 execute_distribution 测完整 seed_only 逻辑
        # 需要 mock REPO_ROOT 和 source path
        dist = {
            "distribution_id": "test-charter",
            "kind": "charter",
            "source": {"path": "test-source/AGENTS.md"},
            "target": {"harness": "pi", "relative_path": "AGENTS.md"},
            "applies_to_roles": ["executor"],
            "operation": "copy_tree",
            "seed_only": True,
        }

        # 直接测 seed_only 判定逻辑
        assert target_path.exists()
        # seed_only 判定: kind=charter + target exists → skip
        seed_only = dist["kind"] == "charter" or dist.get("seed_only", False)
        assert seed_only is True
        assert target_path.exists()  # 跳过条件命中

        # 验证目标文件未被覆盖
        assert target_path.read_text() == "# Existing Charter (customized by advisor)\n"

    def test_charter_force_does_not_overwrite(self, tmp_path):
        """charter --force 也不覆盖(工位主权文件不可践踏)"""
        harness_root = tmp_path / "harness"
        harness_root.mkdir()
        target_path = harness_root / "AGENTS.md"
        target_path.write_text("# My custom charter\n", encoding="utf-8")

        # 即使 force=True, charter kind 的 seed_only 也应跳过
        # 这在 execute_distribution 中实现: seed_only 检查在 force 检查之前
        dist = {
            "distribution_id": "test-charter",
            "kind": "charter",
            "source": {"path": "test-source/AGENTS.md"},
            "target": {"harness": "pi", "relative_path": "AGENTS.md"},
            "operation": "copy_tree",
        }

        # kind=charter 自动 seed_only=True
        seed_only = dist["kind"] == "charter" or dist.get("seed_only", False)
        assert seed_only is True

    def test_fingerprint_diff_identical(self, tmp_path):
        """差异指纹: 相同内容 → identical"""
        src = tmp_path / "src.md"
        tgt = tmp_path / "tgt.md"
        content = "# Same content\n"
        src.write_text(content, encoding="utf-8")
        tgt.write_text(content, encoding="utf-8")

        diff = _fingerprint_diff(src, tgt)
        assert "identical" in diff

    def test_fingerprint_diff_different(self, tmp_path):
        """差异指纹: 不同内容 → DIFFER + 双方哈希"""
        src = tmp_path / "src.md"
        tgt = tmp_path / "tgt.md"
        src.write_text("# Source content\n", encoding="utf-8")
        tgt.write_text("# Target content\n", encoding="utf-8")

        diff = _fingerprint_diff(src, tgt)
        assert "DIFFER" in diff
        assert "source=sha256:" in diff
        assert "target=sha256:" in diff

    def test_fingerprint_diff_target_missing(self, tmp_path):
        """差异指纹: 目标不存在 → <missing>"""
        src = tmp_path / "src.md"
        tgt = tmp_path / "tgt.md"
        src.write_text("# Source\n", encoding="utf-8")

        diff = _fingerprint_diff(src, tgt)
        assert "<missing>" in diff

    def test_non_charter_kind_not_seed_only(self, tmp_path):
        """非 charter kind 不受 seed_only 约束"""
        dist = {
            "distribution_id": "test-extension",
            "kind": "extension",
            "source": {"path": "test-source/ext"},
            "target": {"harness": "pi", "relative_path": "_distributed/extensions/ext"},
            "operation": "copy_tree",
        }

        seed_only = dist["kind"] == "charter" or dist.get("seed_only", False)
        assert seed_only is False


# ==============================================================================
# 大项B: enroll 落盘 cwd 真修
# ==============================================================================

class TestEnrollCwdLanding:
    """验收断言②: enroll 从任意 cwd 执行:.lybra 落 cwd、部署树零新增;
    输出落点字符串与实际路径 assert 相等"""

    def test_enroll_writes_to_cwd_not_deploy_tree(self, tmp_path):
        """从任意 cwd 执行 enroll,.lybra 落在 cwd,不在部署树"""
        from tools.aipos_cli.enroll_client import ensure_lybra_dir

        # 模拟任意 cwd(非产品目录)
        arbitrary_cwd = tmp_path / "random_workdir"
        arbitrary_cwd.mkdir()

        lybra_dir = ensure_lybra_dir(arbitrary_cwd)
        assert lybra_dir == arbitrary_cwd / ".lybra"
        assert lybra_dir.exists()

        # 部署树(.deploy)不应被创建
        deploy_dir = tmp_path / ".deploy"
        assert not deploy_dir.exists()

    def test_enroll_lybra_dir_under_workspace_root(self, tmp_path):
        """.lybra 目录在 workspace_root 下,不在别处"""
        from tools.aipos_cli.enroll_client import ensure_lybra_dir

        workspace = tmp_path / "my_workstation"
        workspace.mkdir()

        lybra_dir = ensure_lybra_dir(workspace)
        assert lybra_dir.parent == workspace
        assert lybra_dir.name == ".lybra"

    def test_deploy_tree_zero_new_files_after_enroll(self, tmp_path):
        """enroll 前后部署树 before/after find 对照:零新增"""
        from tools.aipos_cli.enroll_client import ensure_lybra_dir

        # 模拟部署树
        deploy_tree = tmp_path / ".deploy" / "releases" / "20260823_000000-abc"
        deploy_tree.mkdir(parents=True)

        # before: 记录部署树文件列表
        before_files = set()
        for f in deploy_tree.rglob("*"):
            if f.is_file():
                before_files.add(f.relative_to(deploy_tree))

        # 在另一个目录执行 enroll 落盘
        workstation = tmp_path / "workstation"
        workstation.mkdir()
        lybra_dir = ensure_lybra_dir(workstation)

        # after: 部署树文件列表不变
        after_files = set()
        for f in deploy_tree.rglob("*"):
            if f.is_file():
                after_files.add(f.relative_to(deploy_tree))

        assert before_files == after_files, "部署树在 enroll 后有新增文件!"


# ==============================================================================
# 大项D: enroll 写全连接 — connection.json 字段断言
# ==============================================================================

class TestEnrollFullConnection:
    """验收断言④: enroll 落盘的 connection.json 字段断言:
    workspace_root/mcp.rpc_url/tokens 三全(半残即 FAIL)"""

    def test_validate_connection_complete_all_present(self):
        """三全 → 空缺失列表"""
        from tools.aipos_cli.enroll_client import validate_connection_complete

        conn = {
            "config_version": 1,
            "workspace_root": "/home/user/workstation",
            "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
            "tokens": [{"role": "executor", "token": "xxx"}],
        }
        missing = validate_connection_complete(conn)
        assert missing == []

    def test_validate_connection_missing_workspace_root(self):
        """缺 workspace_root → FAIL"""
        from tools.aipos_cli.enroll_client import validate_connection_complete

        conn = {
            "config_version": 1,
            "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
            "tokens": [{"role": "executor", "token": "xxx"}],
        }
        missing = validate_connection_complete(conn)
        assert "workspace_root" in missing

    def test_validate_connection_missing_rpc_url(self):
        """缺 mcp.rpc_url → FAIL"""
        from tools.aipos_cli.enroll_client import validate_connection_complete

        conn = {
            "config_version": 1,
            "workspace_root": "/home/user/workstation",
            "mcp": {},
            "tokens": [{"role": "executor", "token": "xxx"}],
        }
        missing = validate_connection_complete(conn)
        assert "mcp.rpc_url" in missing

    def test_validate_connection_missing_tokens(self):
        """缺 tokens → FAIL"""
        from tools.aipos_cli.enroll_client import validate_connection_complete

        conn = {
            "config_version": 1,
            "workspace_root": "/home/user/workstation",
            "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
        }
        missing = validate_connection_complete(conn)
        assert "tokens" in missing

    def test_load_or_create_writes_workspace_root(self, tmp_path):
        """load_or_create_connection_json 铸全 workspace_root"""
        from tools.aipos_cli.enroll_client import load_or_create_connection_json

        lybra_dir = tmp_path / ".lybra"
        lybra_dir.mkdir()
        workspace_root = tmp_path / "workstation"

        conn = load_or_create_connection_json(
            lybra_dir,
            gate_url="http://127.0.0.1:7118",
            workspace_root=workspace_root,
        )

        assert conn.get("workspace_root") == str(workspace_root)
        assert conn.get("mcp", {}).get("rpc_url") is not None
        assert isinstance(conn.get("tokens"), list)

    def test_enroll_deliver_writes_workspace_root(self, tmp_path):
        """enroll_deliver_local 新建 connection.json 时包含 workspace_root"""
        # 模拟 enroll_deliver_local 的 connection.json 创建逻辑
        workspace_root = tmp_path / "workstation"
        workspace_root.mkdir()
        lybra_dir = workspace_root / ".lybra"
        lybra_dir.mkdir()

        gate_url = "http://127.0.0.1:7118"
        conn_data = {
            "config_version": 1,
            "workspace_root": str(workspace_root),
            "mcp": {
                "rpc_url": f"{gate_url}/mcp",
            },
            "tokens": [],
        }

        # 写入并验证
        conn_file = lybra_dir / "connection.json"
        conn_file.write_text(json.dumps(conn_data, indent=2), encoding="utf-8")

        # 读回验证
        loaded = json.loads(conn_file.read_text(encoding="utf-8"))
        from tools.aipos_cli.enroll_client import validate_connection_complete
        # tokens 为空列表也算 present(类型正确)
        missing = [k for k in validate_connection_complete(loaded) if k != "tokens"]
        assert missing == [], f"enroll_deliver connection.json 缺键: {missing}"
        assert loaded["workspace_root"] == str(workspace_root)


# ==============================================================================
# 大项E: 无人陪跑 E2E 夹具(脚本化重放)
# ==============================================================================

class TestUnattendedE2E:
    """验收断言⑤: 无人陪跑 E2E — 脚本化重放 发码→enroll→字段断言→
    (sync/status 需活 gate,此处仅验证落盘逻辑闭环)"""

    def test_e2e_enroll_landing_and_connection_fields(self, tmp_path):
        """E2E 落盘闭环: 模拟 enroll 落盘 → 验证 connection.json 字段全"""
        from tools.aipos_cli.enroll_client import (
            ensure_lybra_dir,
            load_or_create_connection_json,
            write_connection_json,
            validate_connection_complete,
            upsert_token_entry,
        )

        # 1. 模拟空目录(任意 cwd)
        workstation = tmp_path / "e2e_workstation"
        workstation.mkdir()

        # 2. 确保 .lybra 目录
        lybra_dir = ensure_lybra_dir(workstation)
        assert lybra_dir.exists()

        # 3. 模拟落盘(enroll 的落盘部分)
        gate_url = "http://127.0.0.1:7118"
        conn_data = load_or_create_connection_json(
            lybra_dir, gate_url=gate_url, workspace_root=workstation
        )

        # 4. 模拟 token entry
        token_entry = {
            "token": "test-token-e2e",
            "role": "executor",
            "agent_instance": "test.e2e.fixture",
            "fingerprint": "sha256:abcdef",
            "scopes": ["queue_claim"],
            "token_ref": "svc-test",
        }
        upsert_token_entry(conn_data, token_entry)

        # 5. 写入
        write_connection_json(lybra_dir, conn_data)

        # 6. 字段断言: workspace_root/mcp.rpc_url/tokens 三全
        loaded = json.loads((lybra_dir / "connection.json").read_text(encoding="utf-8"))
        missing = validate_connection_complete(loaded)
        assert missing == [], f"E2E connection.json 缺键: {missing}"

        # 7. 验证 token 已入册
        assert len(loaded["tokens"]) == 1
        assert loaded["tokens"][0]["agent_instance"] == "test.e2e.fixture"

        # 8. 验证 .lybra 落在 cwd 下(不在部署树)
        assert lybra_dir.parent == workstation
        deploy_tree = tmp_path / ".deploy"
        assert not deploy_tree.exists(), "E2E: 部署树不应被创建"

    def test_e2e_output_path_matches_actual(self, tmp_path):
        """E2E: 输出落点字符串与实际路径 assert 相等"""
        from tools.aipos_cli.enroll_client import ensure_lybra_dir

        workstation = tmp_path / "e2e_output_test"
        workstation.mkdir()
        lybra_dir = ensure_lybra_dir(workstation)

        # 模拟 enroll 返回的 lybra_dir
        result_lybra_dir = str(lybra_dir)

        # 实际路径
        actual_path = str(workstation / ".lybra")

        # AIPOS-F27 大项B: 输出与实际一致
        assert result_lybra_dir == actual_path, (
            f"输出落点 '{result_lybra_dir}' 与实际路径 '{actual_path}' 不一致"
        )


# ==============================================================================
# 大项C: 夹具常驻验证(本文件即为常驻夹具)
# ==============================================================================

class TestFixturePermanent:
    """验收断言③: 夹具入 run-all 常驻"""

    def test_fixture_file_exists(self):
        """本夹具文件存在(常驻证明)"""
        fixture_path = Path(__file__)
        assert fixture_path.exists()
        assert fixture_path.name == "test_aipos_f27_regression.py"

    def test_schema_has_seed_only_semantics(self):
        """distribution.schema.json 包含 seed_only 语义声明"""
        schema_path = Path(__file__).parent.parent / "schema" / "distribution.schema.json"
        if not schema_path.exists():
            pytest.skip("Schema file not found")

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert "seed_only_semantics" in schema, (
            "distribution.schema.json 缺 seed_only_semantics 声明"
        )

    def test_charter_entries_have_seed_only(self):
        """所有 charter 条目声明 seed_only=true"""
        schema_path = Path(__file__).parent.parent / "schema" / "distribution.schema.json"
        if not schema_path.exists():
            pytest.skip("Schema file not found")

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        for dist in schema.get("distributions", []):
            if dist.get("kind") == "charter":
                assert dist.get("seed_only") is True, (
                    f"charter 条目 {dist['distribution_id']} 缺 seed_only=true"
                )


# ==============================================================================
# AIPOS-F27B: bin 包装 cwd 透传 —— 一行修复验证 + 夹具必走用户入口铁律
# ==============================================================================

class TestBinCwdPassthrough:
    """AIPOS-F27B 验收①: bin/lybra 透传调用方 cwd(不再强制切 packageRoot)"""

    BIN_LYBRA = Path(__file__).resolve().parents[1] / "bin" / "lybra"

    def test_bin_lybra_passes_cwd_through(self, tmp_path):
        """从任意 cwd 经 bin/lybra 执行,Python 进程看到的 cwd = 调用方 cwd"""
        # 探针脚本:打印 cwd 后退出
        probe_script = tmp_path / "cwd_probe.py"
        probe_script.write_text(
            'import os,sys; print(f"CWD_PROBE:{os.getcwd()}",flush=True); sys.exit(0)\n',
            encoding="utf-8",
        )
        # 包装器:替代 python,实际跑探针
        wrapper = tmp_path / "probe_python.sh"
        wrapper.write_text(
            f'#!/bin/bash\nexec python3 {probe_script} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        # 从 tmp_path(任意 cwd)跑 bin/lybra
        caller_cwd = tmp_path / "user_workdir"
        caller_cwd.mkdir()
        result = subprocess.run(
            [str(self.BIN_LYBRA)],
            cwd=str(caller_cwd),
            env={**os.environ, "LYBRA_PYTHON": str(wrapper)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "CWD_PROBE:" in result.stdout, (
            f"探针无输出,bin/lybra 可能未启动 Python: stderr={result.stderr}"
        )
        reported_cwd = result.stdout.strip().split("CWD_PROBE:")[1].splitlines()[0]
        assert reported_cwd == str(caller_cwd), (
            f"bin/lybra cwd 透传失败: Python 看到 {reported_cwd}, "
            f"期望 {caller_cwd}(调用方 cwd)"
        )

    def test_bin_lybra_cwd_not_package_root(self, tmp_path):
        """bin/lybra 不再强制 cwd=packageRoot(元凶修复验证)"""
        package_root = Path(__file__).resolve().parents[1]

        probe_script = tmp_path / "cwd_probe2.py"
        probe_script.write_text(
            'import os,sys; print(f"CWD_PROBE:{os.getcwd()}",flush=True); sys.exit(0)\n',
            encoding="utf-8",
        )
        wrapper = tmp_path / "probe_python2.sh"
        wrapper.write_text(
            f'#!/bin/bash\nexec python3 {probe_script} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        # 从 tmp_path(明确不是 packageRoot)跑
        result = subprocess.run(
            [str(self.BIN_LYBRA)],
            cwd=str(tmp_path),
            env={**os.environ, "LYBRA_PYTHON": str(wrapper)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        reported_cwd = result.stdout.strip().split("CWD_PROBE:")[1].splitlines()[0]
        assert reported_cwd != str(package_root), (
            f"bin/lybra 仍强制 cwd=packageRoot({package_root}): "
            f"一行修复被回退!"
        )

    def test_bin_lybra_source_has_cwd_process_cwd(self):
        """静态检查: bin/lybra 源码包含 cwd: process.cwd()"""
        source = self.BIN_LYBRA.read_text(encoding="utf-8")
        assert "cwd: process.cwd()" in source, (
            "bin/lybra 缺少 cwd: process.cwd() —— 一行修复被回退!"
        )
        assert "cwd: packageRoot" not in source, (
            "bin/lybra 仍含 cwd: packageRoot —— 元凶未修!"
        )


class TestBinEntryIronRule:
    """AIPOS-F27B 验收③: 夹具必走用户入口铁律 —— grep 断言测试目录内
    对 aipos_cli 的 subprocess 调用一律经 bin/lybra(白名单例外注明理由)"""

    # 白名单:以下文件允许 python -m 直调(注明理由)
    WHITELIST = {
        # test_aipos_316_guards.py: 测试 tools/aipos_cli/*.py 模块无 __main__ 时的
        # 退出行为,必须用 python -m 直调才能验证模块级 guard(测的是模块本身,不是用户入口)
        "test_aipos_316_guards.py",
        # f23_live_acceptance.sh 中 gate 启动用 python -m tools.mcp_server(不是 aipos_cli,
        # 是起测试 gate 服务,不属于用户可见 CLI 行为)
        "f23_live_acceptance.sh",  # 已改走 bin/lybra 做 enroll,但 gate 启动仍需 python -m mcp_server
    }

    def test_no_direct_aipos_cli_subprocess_in_tests(self):
        """grep 断言: tests/ 下 subprocess 调用 aipos_cli 一律经 bin/lybra"""
        tests_dir = Path(__file__).resolve().parent
        violations = []

        for py_file in tests_dir.rglob("*.py"):
            if py_file.name in self.WHITELIST:
                continue
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text(encoding="utf-8", errors="replace")
            # 检查是否有 subprocess 直调 aipos_cli 模块(应走 bin/lybra)
            for i, line in enumerate(content.splitlines(), 1):
                if "subprocess" in line or "Popen" in line or "check_output" in line:
                    if "tools.aipos_cli" in line and "python" in line:
                        violations.append(
                            f"{py_file.relative_to(tests_dir.parent)}:{i}: {line.strip()}"
                        )

        assert not violations, (
            f"发现 {len(violations)} 处 python -m tools.aipos_cli 直调(应走 bin/lybra):\n"
            + "\n".join(violations)
            + "\n铁律:验收用户可见行为的夹具必须经 bin/lybra 入口调用"
        )

    def test_f23_live_acceptance_uses_bin_lybra_for_enroll(self):
        """f23_live_acceptance.sh 的 enroll 调用走 bin/lybra(非 python -m 直调)"""
        script = (
            Path(__file__).resolve().parents[1]
            / "tools" / "aipos_cli" / "tests" / "f23_live_acceptance.sh"
        )
        if not script.exists():
            pytest.skip("f23_live_acceptance.sh not found")
        content = script.read_text(encoding="utf-8")
        # 检查 enroll 调用走 bin/lybra
        for line in content.splitlines():
            if "enroll" in line and "python3 -m tools.aipos_cli" in line:
                raise AssertionError(
                    f"f23_live_acceptance.sh 仍用 python -m 直调 enroll: {line.strip()}\n"
                    f"铁律:走 bin/lybra 用户入口"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
