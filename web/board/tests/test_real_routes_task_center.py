"""AIPOS-260 FIX-1: real-route task-center escape-patch tests.

F-260-1 root cause: the task center was rendered only on /index.html, which no
active route serves as a landing page (Owner uses / and /workspace/<n>). These
tests GET the REAL routes over HTTP and assert the task-center block is served
there, and that the counts are record-derived (not the raw claimed/ "进行中").
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
from unittest.mock import patch

from web.board.app import make_handler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


class RealRouteTaskCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # Claimed task WITH a verdict -> must read verdict_pass, NOT "executing".
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-v.md").write_text(
            "---\ntask_id: TASK-V\ntitle: Verdicted task\nstatus: claimed\n---\n"
            "# TASK-V\n\nReal purpose line.\n",
            encoding="utf-8",
        )
        vd = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / "TASK-V"
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "verdict_v1.md").write_text(
            "---\nrecord_type: audit_verdict_record\nverdict_id: verdict_v1\nverdict: PASS\n"
            "reviewed_task_id: TASK-V\nactor: audit.lybra.test\n"
            "verdict_at: '2026-07-01T00:40:00Z'\n---\n"
            "# Verdict\n\n- Findings summary: PASS, independent evidence.\n",
            encoding="utf-8",
        )
        # board config: workspace 0 -> fixture root (exercises the real path).
        self.config_path = self.repo_root / "board_config.json"
        self.config_path.write_text(
            json.dumps({"workspaces": [{"label": "Fixture", "root": str(self.repo_root)}]}),
            encoding="utf-8",
        )
        self._patch = patch("web.board.app.BOARD_CONFIG_PATH", self.config_path)
        self._patch.start()
        port = _free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(repo_root=self.repo_root))
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._patch.stop()
        self.temp_dir.cleanup()

    def test_workspace_detail_route_serves_task_center(self) -> None:
        """GET /workspace/0 (the real detail route) must serve the task-center block."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn("任务中心", html)
        self.assertIn('id="task-center-section"', html)

    def test_overview_route_serves_real_progress(self) -> None:
        """GET / (the real landing route) must carry the real-progress rendering."""
        status, html = _get(f"{self.base}/")
        self.assertEqual(status, 200)
        self.assertIn('data-owner-truth="overview"', html)
        self.assertIn("stage_counts", html)

    def test_owner_truth_count_is_record_derived_not_fake(self) -> None:
        """Counts must be record-derived: TASK-V is in claimed/ but has a PASS
        verdict -> verdict_pass, NOT counted as executing (the old fake)."""
        status, body = _get(f"{self.base}/api/owner-truth?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        task_v = next(t for t in payload["data"]["tasks"] if t["task_id"] == "TASK-V")
        self.assertEqual(task_v["true_stage"], "verdict_pass")
        counts = payload["summary"]["stage_counts"]
        self.assertEqual(counts.get("executing", 0), 0)
        self.assertGreater(counts.get("verdict_pass", 0), 0)
        # FIX-2 F-261-3: fine-grained verdict_pass collapses to top-level 执行中.
        self.assertEqual(task_v["top_level_state"], "executing")
        self.assertEqual(task_v["badge_label"], "执行中 · 判决 PASS")
        tlc = payload["summary"]["top_level_counts"]
        self.assertEqual(tlc.get("executing", 0), 1)   # TASK-V
        self.assertEqual(tlc.get("published", 0), 0)
        self.assertEqual(tlc.get("closed", 0), 0)

    def test_overview_api_carries_real_stage_counts(self) -> None:
        """The overview aggregation must surface record-derived stage_counts
        per workspace (the data the overview card renders)."""
        status, body = _get(f"{self.base}/api/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        ws = payload["workspaces"][0]
        self.assertIn("stage_counts", ws)
        self.assertEqual(ws["stage_counts"].get("verdict_pass", 0), 1)
        # FIX-2 F-261-3: overview surfaces three-state top_level_counts (the key
        # the landing page pills render); verdict_pass collapses to 执行中.
        self.assertIn("top_level_counts", ws)
        self.assertEqual(ws["top_level_counts"].get("executing", 0), 1)

    def test_overview_api_keeps_queue_counts_data_interface(self) -> None:
        """FIX-3 F-261-5: the old four-count DISPLAY is removed from the landing
        page, but the queue_counts DATA INTERFACE is preserved untouched (the
        blocked anomaly light reads it); three-state stays the display source."""
        status, body = _get(f"{self.base}/api/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        ws = payload["workspaces"][0]
        # Data interface preserved — blocked anomaly light reads queue_counts.blocked.
        self.assertIn("queue_counts", ws)
        for key in ("pending", "claimed", "blocked", "completed"):
            self.assertIn(key, ws["queue_counts"])
        # Three-state top_level_counts stays the landing-page display source.
        self.assertIn("top_level_counts", ws)

    def test_overview_route_drops_queue_display_shows_blocked_signal(self) -> None:
        """FIX-3 F-261-5: landing page / no longer renders the old four-count
        queue display (待认领/进行中/受阻/已完成 = queue raw counts); it carries
        the three-state pills and a 受阻 anomaly light rendered only when > 0."""
        status, html = _get(f"{self.base}/")
        self.assertEqual(status, 200)
        # Old four-count queue display removed (no render loop, no queue-counts class).
        self.assertNotIn("['pending', 'claimed', 'blocked', 'completed']", html)
        self.assertNotIn("queue-counts", html)
        # Three-state pills + blocked anomaly light (JS) are present.
        self.assertIn("top_level_counts", html)
        self.assertIn("blocked-signal", html)
        self.assertIn("stage.blocked", html)

    def test_published_badge_is_purple_on_both_pages(self) -> None:
        """FIX-3 F-261-6: 已发布 badge is purple on both pages — the landing-page
        pill (.rp-published in overview.css) and the workspace-detail task badge
        (.sb-published in project-detail.html) — consistent with the violet
        published card background; old blue/indigo values gone."""
        status, css = _get(f"{self.base}/overview.css")
        self.assertEqual(status, 200)
        self.assertIn(".rp-published { background: #7c3aed; }", css)
        self.assertNotIn(".rp-published { background: #4f46e5; }", css)
        status, detail = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn(".sb-published { background: #7c3aed; }", detail)
        self.assertNotIn(".sb-published { background: #0284c7; }", detail)

    def test_aipos261_owner_truth_route_carries_closure_units_and_human_phrasing(self) -> None:
        """AIPOS-261 S5: the live /api/owner-truth route serves closure_units and
        human-worded events (role + phrase) — the data the task-center hydrates."""
        status, body = _get(f"{self.base}/api/owner-truth?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        data = payload["data"]
        # Closure units are served (task list groups by family).
        self.assertIn("closure_units", data)
        self.assertGreater(len(data["closure_units"]), 0)
        unit = data["closure_units"][0]
        for key in ("root_task_id", "stage_chain", "members", "timeline"):
            self.assertIn(key, unit)
        # FIX-2 F-261-3: closure unit carries top-level bucket + composed badge.
        self.assertIn("top_level_state", unit)
        self.assertIn("badge_label", unit)
        # Timeline events carry human role + phrase (additive; 人话化).
        ev = unit["timeline"][0]
        self.assertIn("role", ev)
        self.assertIn("phrase", ev)
        # AIPOS-265 S1 (real route): every timeline event carries agent_info so its
        # agent name renders as a clickable popup target on the live board.
        self.assertIn("agent_info", ev)
        self.assertIn("profile", ev["agent_info"])
        self.assertIn("round", ev["agent_info"])
        # S2: no bare-jargon phrase leaks into the feed (no "操作了任务").
        for feed_ev in data["activity_feed"]:
            self.assertNotIn("操作了任务", str(feed_ev.get("phrase", "")))
            # AIPOS-265 S1 (real route): feed events are clickable too.
            self.assertIn("agent_info", feed_ev)


if __name__ == "__main__":
    unittest.main()
