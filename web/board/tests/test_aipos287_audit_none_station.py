"""AIPOS-287: audit:none + owner_verify:required 卡也要有站位(以 return 记录起站).

契约测试：
- S1: audit:none 且有 return 记录的卡能上核验台(站位推导扩展)
- S2: 站面 audit_policy 字段传给前端，前端可判断显示"免审计"badge
- S3: 本卡自指验收(本卡自己交付后应出现在核验台)
"""
from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_AUTH_COOKIE: str | None = None


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url)
    if _AUTH_COOKIE:
        req.add_header("Cookie", _AUTH_COOKIE)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


class AuditNoneStationTests(unittest.TestCase):
    """AIPOS-287: audit:none + owner_verify:required 卡以 return 记录起站."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

        # 免审计卡: audit:none + owner_verify:required + 已 return (无 verdict)
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-287.md").write_text(
            """---
task_id: AIPOS-287
title: 核验台缺口audit:none且owner_verify:required的卡也要有站位
status: claimed
audit: none
owner_verify: required
owner_verify_checklist:
- 一张免审计但要人核验的任务交付后核验台上能看到它的站
- 站面显示免审计标识与return摘要
---
# AIPOS-287

## 验收断言

- S1: audit:none 且有 return 记录也起站
- S2: 站面加免审计badge
- S3: 本卡自指验收
""",
            encoding="utf-8",
        )
        rd = self.repo_root / "5_tasks" / "records" / "returns" / "AIPOS-287"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "return_287.md").write_text(
            """---
record_type: return_record
return_id: return_287
task_id: AIPOS-287
actor: exec.lybra.kiwiai-dev
returned_at: '2026-07-31T10:00:00Z'
executor_status: completed
---
# AIPOS-287 交付报告

## 实现摘要

已完成 S1-S3。
""",
            encoding="utf-8",
        )

        # 普通卡: 有 audit verdict PASS (对照组)
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-normal.md").write_text(
            """---
task_id: TASK-NORMAL
title: 普通卡
status: claimed
owner_verify: required
---
# TASK-NORMAL

## 验收断言

- S1 assertion
""",
            encoding="utf-8",
        )
        rd2 = self.repo_root / "5_tasks" / "records" / "returns" / "TASK-NORMAL"
        rd2.mkdir(parents=True, exist_ok=True)
        (rd2 / "return_normal.md").write_text(
            """---
record_type: return_record
return_id: return_normal
task_id: TASK-NORMAL
actor: exec.lybra.test
returned_at: '2026-07-30T00:30:00Z'
executor_status: completed
---
# Return
""",
            encoding="utf-8",
        )
        vd2 = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / "TASK-NORMAL"
        vd2.mkdir(parents=True, exist_ok=True)
        (vd2 / "verdict_normal.md").write_text(
            """---
record_type: audit_verdict_record
verdict_id: verdict_normal
verdict: PASS
reviewed_task_id: TASK-NORMAL
actor: audit.lybra.test
verdict_at: '2026-07-30T00:40:00Z'
---
# Verdict
""",
            encoding="utf-8",
        )

        self.config_path = self.repo_root / "board_config.json"
        self.config_path.write_text(
            json.dumps({"workspaces": [{"label": "Fixture", "root": str(self.repo_root)}]}),
            encoding="utf-8",
        )
        self._auth_store = SessionStore()
        _sid = self._auth_store.create(role="owner", scopes=["owner_confirm"])
        global _AUTH_COOKIE
        _AUTH_COOKIE = f"{SESSION_COOKIE_NAME}={_sid}"
        port = _free_port()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(
                repo_root=self.repo_root,
                board_config_path=self.config_path,
                session_store=self._auth_store,
            ),
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self) -> None:
        global _AUTH_COOKIE
        _AUTH_COOKIE = None
        self.server.shutdown()
        self.server.server_close()
        self.temp_dir.cleanup()

    def test_audit_none_card_appears_on_station(self) -> None:
        """S1: audit:none 且有 return 记录的卡能上核验台(站位推导扩展)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        self.assertIsInstance(stations, list)
        # 应有 2 张站: AIPOS-287 (audit:none) + TASK-NORMAL (verdict_pass)
        self.assertEqual(len(stations), 2, f"Expected 2 stations, got {len(stations)}")

        station_ids = [s["task_id"] for s in stations]
        self.assertIn("AIPOS-287", station_ids, "AIPOS-287 (audit:none) should appear on station")
        self.assertIn("TASK-NORMAL", station_ids, "TASK-NORMAL (verdict_pass) should appear on station")

    def test_audit_none_station_carries_audit_policy_field(self) -> None:
        """S2: 站面 audit_policy 字段传给前端，前端可判断显示"免审计"badge."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        aipos287 = next((s for s in stations if s["task_id"] == "AIPOS-287"), None)
        self.assertIsNotNone(aipos287, "AIPOS-287 station not found")

        # audit_policy 字段应为 "none"
        self.assertIn("audit_policy", aipos287, "audit_policy field missing in station data")
        self.assertEqual(aipos287["audit_policy"], "none", "audit_policy should be 'none' for AIPOS-287")

        # 普通卡的 audit_policy 应为空或非 "none"
        normal = next((s for s in stations if s["task_id"] == "TASK-NORMAL"), None)
        self.assertIsNotNone(normal, "TASK-NORMAL station not found")
        self.assertIn("audit_policy", normal, "audit_policy field missing in TASK-NORMAL")
        self.assertNotEqual(normal["audit_policy"], "none", "TASK-NORMAL should not have audit:none")

    def test_audit_none_station_has_machine_judgment_evidence(self) -> None:
        """S2: audit:none 卡的证据面显示 RETURN 摘要(machine_judgment 环有内容)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        aipos287 = next((s for s in stations if s["task_id"] == "AIPOS-287"), None)
        self.assertIsNotNone(aipos287, "AIPOS-287 station not found")

        evidence = aipos287.get("evidence", {})
        self.assertIn("machine_judgment", evidence, "machine_judgment evidence missing")
        mj = evidence["machine_judgment"]
        self.assertTrue(mj.get("present"), "machine_judgment should be present (return record exists)")
        self.assertEqual(mj.get("actor"), "exec.lybra.kiwiai-dev", "actor should match return record")
        self.assertIn("summary_excerpt", mj, "summary_excerpt should be present in machine_judgment")

    def test_audit_none_station_audit_verdict_empty(self) -> None:
        """S2: audit:none 卡的 audit_verdict 环标记为 absent (无 verdict 记录)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        aipos287 = next((s for s in stations if s["task_id"] == "AIPOS-287"), None)
        self.assertIsNotNone(aipos287, "AIPOS-287 station not found")

        evidence = aipos287.get("evidence", {})
        self.assertIn("audit_verdict", evidence, "audit_verdict evidence missing")
        av = evidence["audit_verdict"]
        # audit:none 卡无 verdict 记录，present 应为 False
        self.assertFalse(av.get("present"), "audit_verdict should be absent for audit:none card")

    def test_audit_none_delivered_stage(self) -> None:
        """S1: audit:none 卡的 true_stage 应为 'delivered' (已 return, 无 verdict)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        aipos287 = next((s for s in stations if s["task_id"] == "AIPOS-287"), None)
        self.assertIsNotNone(aipos287, "AIPOS-287 station not found")

        # true_stage 应为 'delivered' (has return, no verdict)
        self.assertEqual(aipos287.get("true_stage"), "delivered", "true_stage should be 'delivered' for audit:none card")

    def test_normal_card_verdict_pass_stage(self) -> None:
        """对照组: 普通卡的 true_stage 应为 'verdict_pass' (有 verdict PASS)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        normal = next((s for s in stations if s["task_id"] == "TASK-NORMAL"), None)
        self.assertIsNotNone(normal, "TASK-NORMAL station not found")

        self.assertEqual(normal.get("true_stage"), "verdict_pass", "true_stage should be 'verdict_pass' for normal card")

    def test_owner_verify_checklist_present(self) -> None:
        """S2: owner_verify_checklist 字段传给前端(人话清单)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        aipos287 = next((s for s in stations if s["task_id"] == "AIPOS-287"), None)
        self.assertIsNotNone(aipos287, "AIPOS-287 station not found")

        checklist = aipos287.get("owner_verify_checklist", [])
        self.assertIsInstance(checklist, list, "owner_verify_checklist should be a list")
        self.assertGreater(len(checklist), 0, "owner_verify_checklist should not be empty")
        # 检查是否包含预期的清单项
        self.assertTrue(
            any("免审计" in item or "核验台" in item for item in checklist),
            "owner_verify_checklist should contain expected items from frontmatter"
        )

    def test_self_reference_verification_aipos287(self) -> None:
        """S3: 本卡自指验收 — AIPOS-287 出现在核验台上(本测试即验收)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        stations = payload["data"]["stations"]
        station_ids = [s["task_id"] for s in stations]
        self.assertIn(
            "AIPOS-287",
            station_ids,
            "S3 自指验收失败: AIPOS-287 应出现在核验台上(audit:none + owner_verify:required + 已 return)"
        )


if __name__ == "__main__":
    unittest.main()
