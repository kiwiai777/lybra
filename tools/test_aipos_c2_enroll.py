#!/usr/bin/env python3
"""AIPOS-C2 大项B 验收测试 (headless, 不连 gate) —— enroll 铸全 connection.json 必填键。

覆盖任务卡验收 ④: 对执行体工位重跑 enroll → connection.json 含 config.schema 全部必填键 (workspace_root 在列)。

关键断言:
1. 幂等补铸模式 (code=None): 已有入册工位缺 workspace_root → 重跑 enroll 即补全。
2. 补铸不动 token (token 条目数量/值不变)。
3. 缺必填键时 validate_connection_complete 报出缺失键 (不落半成品)。
4. 完整 enroll (code 提供) 走 token 兑换 (此处不连 gate, 只验铸全校验拦截路径)。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.aipos_cli.enroll_client import (
    enroll,
    load_or_create_connection_json,
    validate_connection_complete,
    write_connection_json,
)


def test_backfill_casts_workspace_root() -> bool:
    """④: 补铸模式写入 workspace_root 且不动 token。"""
    with tempfile.TemporaryDirectory(prefix="aipos_c2_") as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        lybra = ws / ".lybra"
        lybra.mkdir()

        # 模拟已入册工位: 有 token, 但缺 workspace_root (顾问手补前状态)
        conn_file = lybra / "connection.json"
        original = {
            "config_version": 1,
            "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
            "tokens": [
                {"role": "executor", "agent_instance": "exec.lybra.test", "token": "ORIGINAL-TOKEN-ABC"}
            ],
        }
        conn_file.write_text(json.dumps(original))

        # 幂等补铸: code=None → 不动 token, 只铸全必填键
        result = enroll(code=None, gate_url="http://127.0.0.1:7118", workspace_root=ws)

        assert result.get("ok") is True, f"backfill should succeed: {result}"
        assert result.get("operation") == "backfill", f"expected operation=backfill: {result}"
        assert result.get("rotated") is False, "backfill must not rotate token"

        # 重读 connection.json
        data = json.loads(conn_file.read_text())
        assert "workspace_root" in data, "④ connection.json 缺 workspace_root"
        assert data["workspace_root"] == str(ws), f"workspace_root mismatch: {data['workspace_root']}"
        assert validate_connection_complete(data) == [], f"④ 铸全后仍有缺键: {validate_connection_complete(data)}"

        # 不动 token: 数量/值不变
        tokens = data.get("tokens", [])
        assert len(tokens) == 1, f"token 数量变化: {len(tokens)}"
        assert tokens[0]["token"] == "ORIGINAL-TOKEN-ABC", "④ 补铸动了 token 值"

        print("  ✓ ④ 补铸写入 workspace_root 且不动 token")
        return True


def test_idempotent_backfill_no_duplicate() -> bool:
    """幂等补铸: 重复补铸不重复/不损坏 tokens。"""
    with tempfile.TemporaryDirectory(prefix="aipos_c2_idem_") as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        lybra = ws / ".lybra"
        lybra.mkdir()
        conn_file = lybra / "connection.json"
        conn_file.write_text(json.dumps({
            "config_version": 1,
            "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
            "workspace_root": str(ws),
            "tokens": [{"role": "executor", "agent_instance": "exec.lybra.test", "token": "KEEP-ME"}],
        }))

        enroll(code=None, gate_url="http://127.0.0.1:7118", workspace_root=ws)
        enroll(code=None, gate_url="http://127.0.0.1:7118", workspace_root=ws)

        data = json.loads(conn_file.read_text())
        assert len(data["tokens"]) == 1, "重复补铸导致 token 重复"
        assert data["tokens"][0]["token"] == "KEEP-ME", "重复补铸动了 token"
        assert data["workspace_root"] == str(ws), "重复补铸覆盖了正确 workspace_root"

        print("  ✓ 幂等补铸不重复不损坏")
        return True


def test_validate_connection_complete_reports_missing() -> bool:
    """铸全校验报出缺失键。"""
    missing = validate_connection_complete({"config_version": 1})
    assert "workspace_root" in missing, f"应报 workspace_root 缺失: {missing}"
    assert "mcp.rpc_url" in missing, f"应报 mcp.rpc_url 缺失: {missing}"
    assert "tokens" in missing, f"应报 tokens 缺失: {missing}"
    print(f"  ✓ 铸全校验报出缺失键: {missing}")
    return True


def test_backfill_fails_loudly_when_incomplete() -> bool:
    """缺必填键且无法铸全 → enroll 失败出声, 不落半成品。"""
    with tempfile.TemporaryDirectory(prefix="aipos_c2_fail_") as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        lybra = ws / ".lybra"
        lybra.mkdir()
        conn_file = lybra / "connection.json"
        # 损坏文件: 无 mcp 配置 (gate_url=None 时不会补 mcp)
        conn_file.write_text(json.dumps({"config_version": 1}))

        try:
            enroll(code=None, gate_url=None, workspace_root=ws)
        except RuntimeError as exc:
            msg = str(exc)
            assert "铸全失败" in msg, f"应报铸全失败: {msg}"
            print(f"  ✓ 铸全失败出声: {msg[:80]}...")
            return True
        # 不应到达这里
        assert False, "enroll 缺必填键应失败"
        return False


def main() -> int:
    print("=" * 60)
    print("AIPOS-C2 大项B enroll 铸全验收测试")
    print("=" * 60)
    try:
        test_validate_connection_complete_reports_missing()
        test_backfill_casts_workspace_root()
        test_idempotent_backfill_no_duplicate()
        test_backfill_fails_loudly_when_incomplete()
        print()
        print("=" * 60)
        print("✅ 全部通过")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print()
        print("=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60)
        return 1
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
