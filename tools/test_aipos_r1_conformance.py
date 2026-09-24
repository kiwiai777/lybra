"""AIPOS-R1 Conformance 测试 (Python 侧)

读取 schema/conformance/loop_context_fixtures.json 夹具,
验证 Python ConnectionResolver 解析结果与 expected 一致。

与 TS 测试读同一夹具, 确保两边解析逻辑同构 (一机制一实现红线)。

AIPOS-F82 件③: 工位层 token 取值改走产品单源 token_resolver.select_token_entry(原先本文件内嵌一套
「取第一条 instance/role 命中」的循环 = 第 N 套挑选逻辑, 不认 retired); 补 retired 形态用例(RETIRED_FIXTURES:
[retired, new] 取 new / 全 retired fail-closed 带重签出口 / instance 全 retired 落 role 可用条目), 并与
config.schema#identity_resolution.keys.token.entry 声明对账。共享夹具 schema/conformance/loop_context_fixtures.json
(TS 同读)在本卡车道外, retired 形态暂落本文件, 待迁入共享夹具。
"""

import json
import sys
from pathlib import Path
from typing import Any

# 添加产品仓到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.aipos_cli.token_resolver import (
    TOKEN_ENTRY_FIELDS,
    TOKEN_REENROLL_EXIT,
    TokenAllRetiredError,
    TokenNotFoundError,
    select_token_entry,
)

PRODUCT_REPO_ROOT = Path(__file__).resolve().parents[1]

# AIPOS-F82 件③: retired 形态(与共享夹具同结构; expected.token_error = 期望的单源异常类名)
_RETIRED_CONN = {"mode": "service_v0", "workspace_root": "/home/user/projects/lybra", "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"}}
RETIRED_FIXTURES: list[dict[str, Any]] = [
    {
        "name": "retired_then_new_picks_new",
        "description": "重 enroll 后 [retired 旧, new] —— 取 new(旧实现取第一条 = 旧 token → 401)",
        "input": {
            "connection_json": {**_RETIRED_CONN, "tokens": [
                {"role": "executor", "agent_instance": "exec.lybra.test", "token": "old_retired_token", "retired": True,
                 "retired_at": "2026-09-24T02:41:41Z", "retired_reason": "superseded by re-enrollment"},
                {"role": "executor", "agent_instance": "exec.lybra.test", "token": "new_active_token"},
            ]},
            "token_data": {"role": "executor", "agent_instance": "exec.lybra.test", "projects": ["lybra"]},
            "env": {}, "explicit_args": {},
        },
        "expected": {"gate_url": "http://127.0.0.1:7118/mcp", "token": "new_active_token", "project_scope": "lybra", "instance_scope": "exec.lybra.test"},
    },
    {
        "name": "all_retired_fail_closed",
        "description": "命中条目全 retired —— fail-closed(不取旧 token), 拒因带重签出口",
        "input": {
            "connection_json": {**_RETIRED_CONN, "tokens": [
                {"role": "executor", "agent_instance": "exec.lybra.test", "token": "old_retired_token", "retired": True},
            ]},
            "token_data": {"role": "executor", "agent_instance": "exec.lybra.test", "projects": ["lybra"]},
            "env": {}, "explicit_args": {},
        },
        "expected": {"gate_url": "http://127.0.0.1:7118/mcp", "token": None, "token_error": "TokenAllRetiredError",
                     "project_scope": "lybra", "instance_scope": "exec.lybra.test"},
    },
    {
        "name": "instance_retired_falls_to_active_role",
        "description": "本实例条目全 retired 但同角色有可用条目 —— 落 role 阶段取可用条目",
        "input": {
            "connection_json": {**_RETIRED_CONN, "tokens": [
                {"role": "executor", "agent_instance": "exec.lybra.test", "token": "old_retired_token", "retired": True},
                {"role": "executor", "agent_instance": "exec.lybra.other", "token": "role_active_token"},
            ]},
            "token_data": {"role": "executor", "agent_instance": "exec.lybra.test", "projects": ["lybra"]},
            "env": {}, "explicit_args": {},
        },
        "expected": {"gate_url": "http://127.0.0.1:7118/mcp", "token": "role_active_token", "project_scope": "lybra", "instance_scope": "exec.lybra.test"},
    },
]


def load_fixtures(product_repo_root: Path) -> list[dict[str, Any]]:
    """加载 conformance 夹具(共享夹具 + AIPOS-F82 retired 形态)"""
    fixtures_path = product_repo_root / "schema/conformance/loop_context_fixtures.json"
    data = json.loads(fixtures_path.read_text(encoding="utf-8"))
    return list(data["fixtures"]) + RETIRED_FIXTURES


def mock_lybra_dir_exists(workspace_root: Path, connection_json: dict[str, Any]) -> bool:
    """模拟 .lybra/ 目录存在"""
    # 在测试中, 我们假设 .lybra/ 存在并包含 connection_json
    return True


def mock_load_connection_config(lybra_dir: Path, connection_json: dict[str, Any]) -> dict[str, Any]:
    """模拟加载 connection.json"""
    return connection_json


def run_conformance_tests(product_repo_root: Path) -> bool:
    """运行 conformance 测试"""
    fixtures = load_fixtures(product_repo_root)
    passed = 0
    failed = 0

    print("=" * 72)
    print("AIPOS-R1 Conformance 测试 (Python 侧)")
    print("=" * 72)
    print(f"加载 {len(fixtures)} 个夹具\n")

    for fixture in fixtures:
        name = fixture["name"]
        description = fixture["description"]
        input_data = fixture["input"]
        expected = fixture["expected"]

        print(f"[{name}] {description}")

        try:
            connection_json = input_data["connection_json"]
            token_data = input_data["token_data"]
            env = input_data["env"]
            explicit_args = input_data["explicit_args"]

            workspace_root = Path(connection_json.get("workspace_root", "/tmp"))

            # 模拟 .lybra/ 自动发现
            # (在实际测试中, 我们直接传递 connection_json 数据, 不依赖文件系统)
            
            # 解析 gate URL
            # 由于我们不能真正写文件, 这里手动模拟优先级逻辑
            gate_url = None
            if "gate_url" in explicit_args:
                gate_url = explicit_args["gate_url"]
            elif "LYBRA_GATE_URL" in env:
                gate_url = env["LYBRA_GATE_URL"]
            else:
                # 从 connection_json 读取
                mcp_config = connection_json.get("mcp", {})
                gate_url = mcp_config.get("rpc_url", "http://127.0.0.1:7118/mcp")

            # 解析 token
            token = None
            token_error = None
            if "token" in explicit_args:
                token = explicit_args["token"]
            elif "LYBRA_TOKEN" in env:
                token = env["LYBRA_TOKEN"]
            else:
                # 从 connection_json 读取 —— AIPOS-F82: 走产品单源 select_token_entry(instance→role·排除 retired)
                try:
                    entry = select_token_entry(
                        connection_json.get("tokens", []),
                        role=token_data.get("role"),
                        agent_instance=token_data.get("agent_instance"),
                        source=f"fixture:{name}",
                    )
                    token = entry.get(TOKEN_ENTRY_FIELDS["token"])
                except TokenAllRetiredError as exc:
                    token_error = exc.__class__.__name__
                    if TOKEN_REENROLL_EXIT not in str(exc):
                        raise AssertionError(f"全 retired 拒因缺重签出口: {exc}") from exc
                except TokenNotFoundError as exc:
                    token_error = exc.__class__.__name__

            # 解析 project scope
            projects = token_data.get("projects", [])
            project_scope = None
            
            if "project" in explicit_args:
                project_scope = explicit_args["project"]
            elif len(projects) == 1:
                project_scope = projects[0]
            elif len(projects) > 1:
                default_project = token_data.get("default_project")
                if default_project:
                    project_scope = default_project

            # 解析 instance scope
            instance_scope = token_data.get("agent_instance")

            # 验证结果
            errors = []
            
            if gate_url != expected["gate_url"]:
                errors.append(f"  gate_url: got \"{gate_url}\", expected \"{expected['gate_url']}\"")
            
            if token != expected["token"]:
                errors.append(f"  token: got \"{token}\", expected \"{expected['token']}\"")

            if token_error != expected.get("token_error"):
                errors.append(f"  token_error: got {token_error!r}, expected {expected.get('token_error')!r}")
            
            if project_scope != expected["project_scope"]:
                errors.append(f"  project_scope: got \"{project_scope}\", expected \"{expected['project_scope']}\"")
            
            if instance_scope != expected["instance_scope"]:
                errors.append(f"  instance_scope: got \"{instance_scope}\", expected \"{expected['instance_scope']}\"")

            if not errors:
                print("  ✓ PASS\n")
                passed += 1
            else:
                print("  ✗ FAIL")
                for err in errors:
                    print(err)
                print()
                failed += 1

        except Exception as e:
            print(f"  ✗ FAIL: {e}\n")
            failed += 1

    print("=" * 72)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 72)

    return failed == 0


def test_conformance_fixtures_including_retired_shapes() -> None:
    """pytest 入口(AIPOS-F82 入 run-all): 共享夹具 + retired 形态全过。"""
    assert run_conformance_tests(PRODUCT_REPO_ROOT)


def test_retired_fields_declared_in_config_schema() -> None:
    """AIPOS-F82 件③: config.schema 一处声明 tokens[] 条目退役字段与「解析排除 retired」语义, token_resolver 读同一声明。"""
    from tools.aipos_cli.token_resolver import TOKEN_RETIREMENT_FIELDS

    decl = json.loads((PRODUCT_REPO_ROOT / "schema/config.schema.json").read_text(encoding="utf-8"))
    entry = decl["identity_resolution"]["keys"]["token"]["entry"]
    assert set(entry["retirement_fields"]) == {"retired", "retired_at", "retired_reason"} == set(TOKEN_RETIREMENT_FIELDS)
    assert entry["fields"] == TOKEN_ENTRY_FIELDS and entry["reenroll_exit"] == TOKEN_REENROLL_EXIT
    assert "排除 retired" in entry["resolution"] and "fail-closed" in entry["resolution"]
    workstation = next(s for s in decl["identity_resolution"]["keys"]["token"]["sources"] if s["layer"] == "workstation")
    assert "排除 retired" in workstation["field"]


if __name__ == "__main__":
    # AIPOS-F82: 产品仓根取本文件所在仓(旧写死 /home/kiwi/projects/lybra = 工作树跑也读共享检出)
    success = run_conformance_tests(PRODUCT_REPO_ROOT)
    sys.exit(0 if success else 1)
