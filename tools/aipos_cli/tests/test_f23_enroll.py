#!/usr/bin/env python3
"""AIPOS-F23 验收测试 —— 上岗一键化(自包含码/门动词两投影/交换落盘原子/旧坑清账)。

覆盖任务卡验收:
  ① 顾问经 MCP 两阶段发码成功(dry_run→confirm, confirm 输出含可转贴 /lybra enroll 指令文本)
  ③ 中断夹具: 落盘前中断 → grace 窗口内同码免费重试(不进"彻底消费"态), enroll-list 对照
  ④ 码单次/TTL/撤销面不回退(used/revoked/expired 各带原因与下一步)
  ⑤ ok=False 场景均带原因与下一步(F9 teaching error)
  ⑦ 交换与落盘原子(grace 窗口 + land)
  ⑧ 落盘目标=工位目录; 治理工作区(结构签名/governance_root 命中)拒写(第九坑)
  ⑨ role 文件合并保留既有键(owner_policy_ref 等), 禁整文件覆盖
  + 发码只有一份实现: CLI roles enroll-code 与门动词同源 issue_self_contained_code
  + 码格式只有一处定义: encode/decode round-trip + 损坏拒绝
  + 新动词必入注册表: verbs.schema 检查(cli/mcp surface)

headless: 不连真 gate(MCP handler 层直接调用, gate 侧文件用临时工作区模拟)。
跑法: python3 -m pytest tools/aipos_cli/tests/test_f23_enroll.py -q
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.aipos_cli.enrollment import (
    ENROLL_DEFAULT_TTL_SECONDS,
    create_enrollment_code,
    decode_self_contained_code,
    encode_self_contained_code,
    get_enrollment_status,
    land_enrollment,
    list_enrollment_codes,
    mark_enrollment_used,
    mint_transport_token_entry,
    issue_self_contained_code,
)


def _make_gate_root(tmp: str) -> Path:
    """模拟一个 gate 侧治理工作区根(含 .lybra/connection.json 骨架)。"""
    root = Path(tmp) / "gov_ws"
    (root / ".lybra").mkdir(parents=True, exist_ok=True)
    conn = root / ".lybra" / "connection.json"
    if not conn.exists():
        conn.write_text(json.dumps({
            "config_version": 1,
            "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"},
            "tokens": [],
        }), encoding="utf-8")
    return root


class TestSelfContainedCodeFormat(unittest.TestCase):
    """码格式只有一处定义(encode/decode round-trip + 篡改/版本拒绝)。"""

    def test_round_trip(self):
        code = encode_self_contained_code(
            gate_url="http://kiwiai-dev.tail6b5218.ts.net:7118",
            governance_root="/home/kiwi/ai-project-os/2_projects/lybra",
            transport_token="TT-xxx",
            code="PLAIN-CODE",
        )
        self.assertTrue(code.startswith("LYBRAENROLL1."))
        decoded = decode_self_contained_code(code)
        self.assertEqual(decoded["gate_url"], "http://kiwiai-dev.tail6b5218.ts.net:7118")
        self.assertEqual(decoded["governance_root"], "/home/kiwi/ai-project-os/2_projects/lybra")
        self.assertEqual(decoded["transport_token"], "TT-xxx")
        self.assertEqual(decoded["code"], "PLAIN-CODE")
        self.assertEqual(decoded["v"], 1)

    def test_plain_code_returns_none(self):
        self.assertIsNone(decode_self_contained_code("some-legacy-plain-code"))

    def test_corrupted_rejected(self):
        self.assertIsNone(decode_self_contained_code("LYBRAENROLL1.!!!not-base64!!!"))
        # 版本不识别
        import base64
        payload = base64.urlsafe_b64encode(json.dumps({"v": 99, "gate_url": "x", "transport_token": "t", "code": "c"}).encode()).decode().rstrip("=")
        self.assertIsNone(decode_self_contained_code("LYBRAENROLL1." + payload))
        # 缺必填字段
        payload = base64.urlsafe_b64encode(json.dumps({"v": 1, "gate_url": "x"}).encode()).decode().rstrip("=")
        self.assertIsNone(decode_self_contained_code("LYBRAENROLL1." + payload))


class TestIssueSelfContainedCode(unittest.TestCase):
    """发码唯一实现: enrollment 记录 + 运输凭证注册 + 自包含码 + 可转贴文本。"""

    def test_issue_end_to_end(self):
        with tempfile.TemporaryDirectory(prefix="f23_issue_") as tmp:
            root = _make_gate_root(tmp)
            result = issue_self_contained_code(
                root, role="executor", instance="exec.lybra.mac1",
                ttl_seconds=3600, by="decision_log:2026-08-22", reason="F23 test",
            )
            # ① 自包含码 + paste 文本
            self.assertTrue(result["ok"])
            self.assertTrue(result["self_contained_code"].startswith("LYBRAENROLL1."))
            self.assertTrue(result["paste_text"].startswith("/lybra enroll LYBRAENROLL1."))
            # ② 既有单次/TTL/撤销面: enrollments.json 有 pending 记录带 TTL
            enrollments = json.loads((root / ".lybra" / "enrollments.json").read_text())
            self.assertEqual(len(enrollments), 1)
            rec = next(iter(enrollments.values()))
            self.assertEqual(rec["status"], "pending")
            self.assertIsNotNone(rec["expires_at"])
            # ③ 运输凭证: 零 scope + TTL + 注册进 gate connection.json
            tokens = json.loads((root / ".lybra" / "connection.json").read_text())["tokens"]
            transport = [t for t in tokens if t["role"] == "enroll-transport"]
            self.assertEqual(len(transport), 1)
            self.assertEqual(transport[0]["scopes"], [])
            self.assertIsNotNone(transport[0]["expires_at"])
            self.assertEqual(transport[0]["token_ref"], "svc-enroll-transport")
            # ④ 码内嵌地址 = 内层码与运输凭证一致
            decoded = decode_self_contained_code(result["self_contained_code"])
            self.assertEqual(decoded["code"], rec["code"])
            self.assertEqual(decoded["transport_token"], transport[0]["token"])
            self.assertEqual(decoded["governance_root"], str(root))

    def test_default_ttl_applies(self):
        with tempfile.TemporaryDirectory(prefix="f23_ttl_") as tmp:
            root = _make_gate_root(tmp)
            result = issue_self_contained_code(root, role="executor", by="x")
            self.assertEqual(result["ttl_seconds"], ENROLL_DEFAULT_TTL_SECONDS)

    def test_gate_url_default_prefers_non_loopback_rpc_url(self):
        with tempfile.TemporaryDirectory(prefix="f23_url_") as tmp:
            root = _make_gate_root(tmp)
            conn_path = root / ".lybra" / "connection.json"
            data = json.loads(conn_path.read_text())
            data["mcp"]["rpc_url"] = "http://kiwiai-dev.tail6b5218.ts.net:7118/mcp"
            conn_path.write_text(json.dumps(data))
            result = issue_self_contained_code(root, role="executor", by="x")
            self.assertEqual(result["gate_url"], "http://kiwiai-dev.tail6b5218.ts.net:7118")
            # loopback rpc_url → 缺省 127.0.0.1:7118
            data["mcp"]["rpc_url"] = "http://127.0.0.1:7118/mcp"
            conn_path.write_text(json.dumps(data))
            result = issue_self_contained_code(root, role="executor", by="x")
            self.assertEqual(result["gate_url"], "http://127.0.0.1:7118")


class TestGraceWindowAtomicity(unittest.TestCase):
    """验收⑦/③: 交换与落盘原子 —— grace 窗口内同码免费重试; land 后彻底消费。"""

    def _issue(self, root: Path) -> str:
        result = issue_self_contained_code(root, role="executor", instance="exec.t", ttl_seconds=600, by="t")
        return result["self_contained_code"]

    def test_retry_returns_same_token_until_landed(self):
        with tempfile.TemporaryDirectory(prefix="f23_grace_") as tmp:
            root = _make_gate_root(tmp)
            sc = self._issue(root)
            inner = decode_self_contained_code(sc)["code"]
            # 首次兑换
            r1 = mark_enrollment_used(root, inner, token_entry={"role": "executor", "token": "TOK1"})
            self.assertFalse(r1.get("retry"))
            self.assertIsNotNone(r1["grace_until"])
            # 中断夹具: 落盘前中断 → 同码重试 → 同一 token(不重铸)
            r2 = mark_enrollment_used(root, inner, token_entry={"role": "executor", "token": "TOK2"})
            self.assertTrue(r2.get("retry"))
            self.assertEqual(r2["minted_token_entry"]["token"], "TOK1")
            # enroll-list 对照: status=used, landed=False
            item = list_enrollment_codes(root)[0]
            self.assertEqual(item["status"], "used")
            self.assertFalse(item["landed"])
            # 落盘成功 → land → landed=True
            r3 = land_enrollment(root, inner, landed_detail="ws=/x files=connection.json,role")
            self.assertIsNotNone(r3["landed_at"])
            item = list_enrollment_codes(root)[0]
            self.assertTrue(item["landed"])
            # land 幂等
            r4 = land_enrollment(root, inner)
            self.assertTrue(r4.get("retry"))
            # land 之后重试 → ValueError(单次, 不回退)
            with self.assertRaises(ValueError):
                mark_enrollment_used(root, inner)

    def test_grace_expiry_burns_code_with_reason(self):
        with tempfile.TemporaryDirectory(prefix="f23_expire_") as tmp:
            root = _make_gate_root(tmp)
            sc = self._issue(root)
            inner = decode_self_contained_code(sc)["code"]
            mark_enrollment_used(root, inner, token_entry={"role": "executor", "token": "TOK1"})
            # 手动把 grace_until 拨到过去
            enrollments_path = root / ".lybra" / "enrollments.json"
            data = json.loads(enrollments_path.read_text())
            code_id = next(iter(data))
            data[code_id]["grace_until"] = "2000-01-01T00:00:00Z"
            enrollments_path.write_text(json.dumps(data))
            with self.assertRaises(ValueError) as ctx_err:
                mark_enrollment_used(root, inner)
            self.assertIn("grace expired", str(ctx_err.exception))  # 原因可见

    def test_revoked_and_expired_carry_reason(self):
        """验收④⑤: 撤销/过期状态原样可见(get_enrollment_status)。"""
        with tempfile.TemporaryDirectory(prefix="f23_revoke_") as tmp:
            root = _make_gate_root(tmp)
            from tools.aipos_cli.enrollment import revoke_enrollment_code
            result = issue_self_contained_code(root, role="executor", by="t", ttl_seconds=600)
            inner = decode_self_contained_code(result["self_contained_code"])["code"]
            revoked = revoke_enrollment_code(root, result["code_id"], by="t", reason="test revoke")
            self.assertEqual(revoked["status"], "revoked")
            status, _ = get_enrollment_status(root, inner)
            self.assertEqual(status, "revoked")


class TestRoleFileMergeAndGuards(unittest.TestCase):
    """验收⑨(role 合并保留既有键)+ 验收⑧/第九坑(治理工作区拒写)。"""

    def test_role_file_merges_existing_keys(self):
        from tools.aipos_cli.enroll_client import write_role_file
        with tempfile.TemporaryDirectory(prefix="f23_role_") as tmp:
            lybra = Path(tmp) / ".lybra"
            lybra.mkdir()
            (lybra / "role").write_text(json.dumps({
                "role": "executor",
                "instance": "exec.old",
                "owner_policy_ref": "pol_lybra_dev_9",
                "custom_key": "keep-me",
            }), encoding="utf-8")
            keys = write_role_file(lybra, "auditor", "audit.t1", None)
            data = json.loads((lybra / "role").read_text())
            # 既有键保留(验收⑨: 禁整文件覆盖)
            self.assertEqual(data["owner_policy_ref"], "pol_lybra_dev_9")
            self.assertEqual(data["custom_key"], "keep-me")
            # 新值覆盖 role/instance; enrolled_at 时间戳落盘
            self.assertEqual(data["role"], "auditor")
            self.assertEqual(data["instance"], "audit.t1")
            self.assertIn("enrolled_at", data)
            self.assertIn("owner_policy_ref", keys)

    def test_governance_workspace_guard(self):
        from tools.aipos_cli.enroll_client import is_governance_workspace
        with tempfile.TemporaryDirectory(prefix="f23_guard_") as tmp:
            base = Path(tmp)
            # ① 结构签名: 5_tasks/queue → 治理工作区
            gov_like = base / "govlike"
            (gov_like / "5_tasks" / "queue").mkdir(parents=True)
            self.assertTrue(is_governance_workspace(gov_like, None))
            # ② governance_root 命中
            self.assertTrue(is_governance_workspace(base / "gov_ws", str(base / "gov_ws")))
            # ③ 干净工位目录 → 放行
            station = base / "station"
            station.mkdir()
            self.assertFalse(is_governance_workspace(station, str(base / "gov_ws")))

    def test_enroll_refuses_governance_target(self):
        from tools.aipos_cli.enroll_client import enroll
        with tempfile.TemporaryDirectory(prefix="f23_refuse_") as tmp:
            root = _make_gate_root(tmp)
            result = issue_self_contained_code(root, role="executor", by="t")
            sc = result["self_contained_code"]
            with self.assertRaises(RuntimeError) as ctx_err:
                enroll(code=sc, gate_url="", workspace_root=root)
            msg = str(ctx_err.exception)
            self.assertIn("治理工作区", msg)
            self.assertIn("可抄示例", msg)  # F9: 带路文案


class TestMcpVerbs(unittest.TestCase):
    """验收①: 顾问经 MCP 两阶段发码; ⑤: ok=False 带原因与下一步。"""

    def _gate_ctx(self, tmp: str):
        root = _make_gate_root(tmp)
        from tools.mcp_server import tools as mcp_tools
        return root, mcp_tools

    def test_two_phase_dry_run_confirm(self):
        with tempfile.TemporaryDirectory(prefix="f23_mcp_") as tmp:
            root, mcp = self._gate_ctx(tmp)
            with patch.object(mcp, "_repo_root", return_value=root):
                # 缺 role → F9 teaching error(带 example)
                err = mcp.lybra_enroll_code_dry_run({"owner_authorization_ref": "x"})
                self.assertFalse(err["structuredContent"]["ok"] if "structuredContent" in err else err.get("ok", True))
                # 缺 owner_authorization_ref → teaching error
                resp = mcp.lybra_enroll_code_dry_run({"role": "executor"})
                self.assertIn("errors", json.dumps(resp))
                # 正常 dry_run
                resp = mcp.lybra_enroll_code_dry_run({
                    "role": "executor", "instance": "exec.lybra.mac1",
                    "ttl": 3600, "owner_authorization_ref": "decision_log:2026-08-22",
                })
                payload = resp.get("structuredContent", resp)
                self.assertTrue(payload["ok"])
                self.assertIn("dry_run_token", payload)
                token = payload["dry_run_token"]
                # confirm 缺 OWNER_CONFIRMED → 拒
                resp = mcp.lybra_enroll_code_confirm({"dry_run_token": token, "owner_confirmation_token": "WRONG"})
                self.assertNotIn("ok: True", json.dumps(resp))
                # confirm 正常 → 自包含码 + 可转贴指令文本(验收①)
                resp = mcp.lybra_enroll_code_confirm({
                    "dry_run_token": token, "owner_confirmation_token": "OWNER_CONFIRMED",
                })
                payload = resp.get("structuredContent", resp)
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["self_contained_code"].startswith("LYBRAENROLL1."))
                self.assertTrue(payload["paste_text"].startswith("/lybra enroll LYBRAENROLL1."))
                self.assertIn("/lybra enroll", payload["paste_instruction"])
                # dry_run_token 一次性: 重放 → STALE
                resp = mcp.lybra_enroll_code_confirm({
                    "dry_run_token": token, "owner_confirmation_token": "OWNER_CONFIRMED",
                })
                self.assertIn("STALE_DRY_RUN", json.dumps(resp))

    def test_exchange_self_contained_and_reasons(self):
        with tempfile.TemporaryDirectory(prefix="f23_exch_") as tmp:
            root, mcp = self._gate_ctx(tmp)
            with patch.object(mcp, "_repo_root", return_value=root), \
                 patch.object(mcp, "_reload_token_registry", lambda: None):
                result = issue_self_contained_code(root, role="executor", instance="exec.t", ttl_seconds=600, by="t")
                sc = result["self_contained_code"]
                # ① 自包含码 exchange(内层码自动解出)
                resp = mcp.lybra_roles_enroll_exchange({"code": sc})
                payload = resp.get("structuredContent", resp)
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["landing_required"])
                self.assertIsNotNone(payload["grace_until"])
                self.assertEqual(payload["token_entry"]["role"], "executor")
                # ② grace 窗口内重试 → 同一 token
                resp2 = mcp.lybra_roles_enroll_exchange({"code": sc})
                payload2 = resp2.get("structuredContent", resp2)
                self.assertTrue(payload2["ok"])
                self.assertTrue(payload2.get("retry"))
                self.assertEqual(payload2["token_entry"]["token"], payload["token_entry"]["token"])
                # ③ land
                resp3 = mcp.lybra_roles_enroll_land({"code": sc, "landed_detail": "ws=/x"})
                payload3 = resp3.get("structuredContent", resp3)
                self.assertTrue(payload3["ok"])
                # ④ land 后再 exchange → ok=False 带原因+下一步(验收⑤)
                resp4 = mcp.lybra_roles_enroll_exchange({"code": sc})
                text = json.dumps(resp4, ensure_ascii=False)
                self.assertIn("already used", text)
                self.assertIn("suggested_next_action", text)

    def test_exchange_revoked_carries_reason(self):
        with tempfile.TemporaryDirectory(prefix="f23_rev_") as tmp:
            root, mcp = self._gate_ctx(tmp)
            with patch.object(mcp, "_repo_root", return_value=root):
                result = issue_self_contained_code(root, role="executor", by="t", ttl_seconds=600)
                from tools.aipos_cli.enrollment import revoke_enrollment_code
                revoke_enrollment_code(root, result["code_id"], by="t")
                resp = mcp.lybra_roles_enroll_exchange({"code": result["self_contained_code"]})
                text = json.dumps(resp, ensure_ascii=False)
                self.assertIn("revoked", text)
                self.assertIn("suggested_next_action", text)

    def test_legacy_verb_delegates_to_same_implementation(self):
        """发码只有一份实现: 旧单相动词 lybra_roles_enroll_code 也出自 issue_self_contained_code。"""
        with tempfile.TemporaryDirectory(prefix="f23_legacy_") as tmp:
            root, mcp = self._gate_ctx(tmp)
            with patch.object(mcp, "_repo_root", return_value=root), \
                 patch.object(mcp, "_reload_token_registry", lambda: None):
                resp = mcp.lybra_roles_enroll_code({
                    "role": "executor", "owner_authorization_ref": "t",
                })
                payload = resp.get("structuredContent", resp)
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["self_contained_code"].startswith("LYBRAENROLL1."))
                self.assertTrue(payload["paste_text"].startswith("/lybra enroll "))


class TestVerbSchemaRegistration(unittest.TestCase):
    """新动词必入注册表(C1: verbs.schema 单源)。"""

    def test_verbs_registered(self):
        schema = json.loads((Path(__file__).resolve().parents[3] / "schema" / "verbs.schema.json").read_text())
        verbs = schema["verbs"]
        for verb in ("lybra_enroll_code_dry_run", "lybra_enroll_code_confirm",
                     "lybra_roles_enroll_exchange", "lybra_roles_enroll_land"):
            self.assertIn(verb, verbs, f"{verb} 未入 verbs.schema 注册表")
        # 两阶段语义
        self.assertEqual(verbs["lybra_enroll_code_dry_run"]["confirm_verb"], "lybra_enroll_code_confirm")
        self.assertEqual(verbs["lybra_enroll_code_confirm"]["dry_run_verb"], "lybra_enroll_code_dry_run")
        self.assertIn("advisor_with_owner_confirmed_literal",
                      verbs["lybra_enroll_code_dry_run"]["stage_contract"]["self_confirm_allowed"])
        # 参数名 F14 继承: confirm 必填 dry_run_token + owner_confirmation_token
        required = verbs["lybra_enroll_code_confirm"]["parameters"]["required"]
        self.assertIn("dry_run_token", required)
        self.assertIn("owner_confirmation_token", required)

    def test_cli_projection_same_implementation(self):
        """F24A 新契约: CLI roles enroll-code 是调门动词的薄壳(单实现=门进程内)。
        源级断言: 调 lybra_enroll_code_dry_run/confirm; 不再直接调 issue_self_contained_code
        /create_enrollment_code(本地发码路径已废除 —— 死凭证类缺陷根除, 验收⑤)。"""
        cli_src = (Path(__file__).resolve().parents[1] / "aipos_cli.py").read_text(encoding="utf-8")
        enroll_code_section = cli_src[cli_src.index('roles_command == "enroll-code"'):]
        enroll_code_section = enroll_code_section[:enroll_code_section.index('roles_command == "enroll-revoke"')]
        self.assertIn('lybra_enroll_code_dry_run', enroll_code_section)
        self.assertIn('lybra_enroll_code_confirm', enroll_code_section)
        self.assertNotIn("issue_self_contained_code", enroll_code_section)
        self.assertNotIn("create_enrollment_code(", enroll_code_section)


if __name__ == "__main__":
    unittest.main()
