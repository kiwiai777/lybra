"""AIPOS-274F2: HTTP-level contract tests for envelope alignment.

Iron evidence (advisor raw probe): /api/owner-truth returned total_tasks at
top-level summary only; data.* had no summary -> frontend reading data.summary
got nothing. Verify-bench stations suspected of same misalignment.

Fix: backend mirrors summary into data.summary (top-level retained for compat).
These tests hit the REAL HTTP routes (not function calls) and assert the
contract at the wire level — the only layer that matters for this regression.
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


class OwnerTruthEnvelopeAlignmentTests(unittest.TestCase):
    """AIPOS-274F2 S1+S3: /api/owner-truth HTTP response carries summary at
    BOTH top level AND inside data (mirrored). Contract test asserts
    data.summary.total_tasks at the wire level."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # Write 3 tasks so total_tasks is unambiguously 3.
        for i, tid in enumerate(("TASK-A", "TASK-B", "TASK-C"), 1):
            (self.repo_root / "5_tasks" / "queue" / "claimed" / f"task-{tid.lower()}.md").write_text(
                f"---\ntask_id: {tid}\ntitle: Task {i}\nstatus: claimed\n---\n# {tid}\n",
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

    def test_http_data_summary_total_tasks_matches_top_level(self) -> None:
        """S1: HTTP /api/owner-truth returns data.summary.total_tasks == 3 AND
        top-level summary.total_tasks == 3 (mirrored, both paths resolve)."""
        status, body = _get(f"{self.base}/api/owner-truth?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        # Top-level summary (backward compat).
        self.assertIn("summary", payload, "top-level summary missing from HTTP response")
        self.assertEqual(payload["summary"]["total_tasks"], 3)

        # AIPOS-274F2: data.summary mirrored (the fix under test).
        self.assertIn("data", payload, "data key missing from HTTP response")
        self.assertIn("summary", payload["data"], "data.summary missing — F2 mirror broken at HTTP layer")
        self.assertEqual(payload["data"]["summary"]["total_tasks"], 3)

        # Both paths must agree.
        self.assertEqual(payload["summary"]["total_tasks"], payload["data"]["summary"]["total_tasks"])

    def test_http_data_summary_stage_counts_present(self) -> None:
        """S1: data.summary carries stage_counts (mirrored from top-level)."""
        status, body = _get(f"{self.base}/api/owner-truth?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        # Top-level stage_counts.
        self.assertIn("stage_counts", payload["summary"])
        # data.summary.stage_counts must match.
        self.assertIn("stage_counts", payload["data"]["summary"])
        self.assertEqual(payload["summary"]["stage_counts"], payload["data"]["summary"]["stage_counts"])

    def test_http_data_summary_closure_units_present(self) -> None:
        """S1: data.summary carries closure_units count (mirrored)."""
        status, body = _get(f"{self.base}/api/owner-truth?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        self.assertIn("closure_units", payload["summary"])
        self.assertIn("closure_units", payload["data"]["summary"])
        self.assertEqual(payload["summary"]["closure_units"], payload["data"]["summary"]["closure_units"])

    def test_http_data_tasks_still_present(self) -> None:
        """S4: data.tasks still present (no regression on the main payload)."""
        status, body = _get(f"{self.base}/api/owner-truth?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        self.assertIn("tasks", payload["data"])
        self.assertEqual(len(payload["data"]["tasks"]), 3)


class VerifyBenchEnvelopeAlignmentTests(unittest.TestCase):
    """AIPOS-274F2 S1+S3: /api/verify-bench HTTP response carries summary at
    BOTH top level AND inside data. Stations are in data.stations (already
    correct) AND data.summary.stations carries the count (mirrored)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # Station card: owner_verify:required, audit PASS recorded.
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-vb.md").write_text(
            "---\ntask_id: TASK-VB\ntitle: Verify bench task\nstatus: claimed\n"
            "owner_verify: required\n---\n# TASK-VB\n\n## 验收断言\n\n- S1 assertion\n",
            encoding="utf-8",
        )
        rd = self.repo_root / "5_tasks" / "records" / "returns" / "TASK-VB"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "return_vb.md").write_text(
            "---\nrecord_type: return_record\nreturn_id: return_vb\ntask_id: TASK-VB\n"
            "actor: exec.lybra.test\nreturned_at: '2026-07-30T00:30:00Z'\n"
            "executor_status: completed\naudit_readiness: ready\n---\n# Return\n",
            encoding="utf-8",
        )
        vd = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / "TASK-VB"
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "verdict_vb.md").write_text(
            "---\nrecord_type: audit_verdict_record\nverdict_id: verdict_vb\nverdict: PASS\n"
            "reviewed_task_id: TASK-VB\nactor: audit.lybra.test\n"
            "verdict_at: '2026-07-30T00:40:00Z'\n---\n# Verdict\n",
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

    def test_http_data_stations_present(self) -> None:
        """S1: HTTP /api/verify-bench returns data.stations as a list with the
        station card (TASK-VB, verdict_pass)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        self.assertIn("data", payload)
        self.assertIn("stations", payload["data"])
        self.assertIsInstance(payload["data"]["stations"], list)
        self.assertEqual(len(payload["data"]["stations"]), 1)
        self.assertEqual(payload["data"]["stations"][0]["task_id"], "TASK-VB")

    def test_http_data_summary_stations_matches_top_level(self) -> None:
        """S1: data.summary.stations == top-level summary.stations (mirrored)."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        # Top-level summary.
        self.assertIn("summary", payload)
        self.assertEqual(payload["summary"]["stations"], 1)

        # AIPOS-274F2: data.summary mirrored.
        self.assertIn("summary", payload["data"], "data.summary missing — F2 mirror broken at HTTP layer")
        self.assertEqual(payload["data"]["summary"]["stations"], 1)

        # Both paths must agree.
        self.assertEqual(payload["summary"]["stations"], payload["data"]["summary"]["stations"])

    def test_http_data_summary_previewable_matches(self) -> None:
        """S1: data.summary.previewable == top-level summary.previewable."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)

        self.assertIn("previewable", payload["summary"])
        self.assertIn("previewable", payload["data"]["summary"])
        self.assertEqual(payload["summary"]["previewable"], payload["data"]["summary"]["previewable"])


if __name__ == "__main__":
    unittest.main()
