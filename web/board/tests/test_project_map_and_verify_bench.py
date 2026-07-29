"""AIPOS-262B: project milestone map + verification bench contract tests.

S4: contract (map schema + verify-bench key names) AND real-route assertions.
S2: graceful hide when project-map.md is absent (fixture assertion).

These GET the REAL routes over HTTP (mirroring test_real_routes_task_center) and
assert the two new read surfaces carry the documented schema + that the
/workspace/<n> page serves the new regions.
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


PROJECT_MAP_MD = """---
map_version: 1
portal:
  description: Fixture portal description
  collab_mode: 2-role
  topology: 星型
  workers:
    - exec.fixture
    - audit.fixture
  advisor: advisor.fixture
updated: 2026-07-28
milestones:
  - id: m1
    title: First milestone
    refs: [REF-A, direction_log 2026-06]
  - id: m2
    title: Second milestone
    refs: [REF-C]
current: Currently here at lybra-dev
in_flight:
  - In flight item
next:
  - Next item
horizon:
  - Horizon one
  - Horizon two
---

# Project map fixture body
"""


class _BaseServer(unittest.TestCase):
    def _start(self, repo_root: Path) -> None:
        self.config_path = repo_root / "board_config.json"
        self.config_path.write_text(
            json.dumps({"workspaces": [{"label": "Fixture", "root": str(repo_root)}]}),
            encoding="utf-8",
        )
        self._patch = patch("web.board.app.BOARD_CONFIG_PATH", self.config_path)
        self._patch.start()
        port = _free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(repo_root=repo_root))
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._patch.stop()
        self.temp_dir.cleanup()


class ProjectMapContractTests(_BaseServer):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "governance").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "governance" / "project-map.md").write_text(PROJECT_MAP_MD, encoding="utf-8")
        dl = self.repo_root / "governance" / "direction_log"
        dl.mkdir(parents=True, exist_ok=True)
        (dl / "2026-07-direction-decisions.md").write_text(
            "# DL\n\n## 2026-07-10 — Third decision\n\n## 2026-07-09 — Second decision\n\n"
            "## 2026-07-07 — First decision\n",
            encoding="utf-8",
        )
        self._start(self.repo_root)

    def test_project_map_schema_and_nested_parse(self) -> None:
        """S1/S4: project-map carries the documented schema; the PyYAML-free
        targeted parser resolves nested portal (workers list) + milestones
        (list of maps with refs flow lists) without PyYAML."""
        status, body = _get(f"{self.base}/api/project-map?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        data = payload["data"]
        self.assertTrue(data["available"])
        self.assertEqual(data["map_version"], 1)
        self.assertEqual(data["updated"], "2026-07-28")
        # portal nested map + nested workers list parsed.
        self.assertEqual(data["portal"]["description"], "Fixture portal description")
        self.assertEqual(data["portal"]["workers"], ["exec.fixture", "audit.fixture"])
        # milestones: list of maps with refs (flow list) preserved verbatim.
        self.assertEqual(len(data["milestones"]), 2)
        self.assertEqual(data["milestones"][0]["id"], "m1")
        self.assertEqual(data["milestones"][0]["refs"], ["REF-A", "direction_log 2026-06"])
        # four segments present.
        self.assertEqual(data["current"], "Currently here at lybra-dev")
        self.assertEqual(data["in_flight"], ["In flight item"])
        self.assertEqual(data["next"], ["Next item"])
        self.assertEqual(data["horizon"], ["Horizon one", "Horizon two"])

    def test_direction_log_recent_latest_three(self) -> None:
        """The current-node popup carries direction_log latest 3 dated titles."""
        status, body = _get(f"{self.base}/api/project-map?workspace=0")
        payload = json.loads(body)
        dl = payload["data"]["direction_log_recent"]
        self.assertEqual(len(dl), 3)
        # Newest first by date.
        self.assertEqual(dl[0]["date"], "2026-07-10")
        self.assertEqual(dl[0]["title"], "Third decision")
        self.assertEqual(dl[-1]["date"], "2026-07-07")

    def test_portal_header_schema_five_keys(self) -> None:
        """AIPOS-264 S4: /api/project-map portal carries the documented five keys
        (description / collab_mode / topology / workers[] / advisor)."""
        status, body = _get(f"{self.base}/api/project-map?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        portal = payload["data"]["portal"]
        for key in ("description", "collab_mode", "topology", "workers", "advisor"):
            self.assertIn(key, portal, f"portal missing key {key}")
        self.assertEqual(portal["description"], "Fixture portal description")
        self.assertEqual(portal["collab_mode"], "2-role")
        self.assertEqual(portal["topology"], "星型")
        self.assertEqual(portal["workers"], ["exec.fixture", "audit.fixture"])
        self.assertEqual(portal["advisor"], "advisor.fixture")

    def test_workspace_route_serves_portal_header(self) -> None:
        """AIPOS-264 S1/S3/S4: the real /workspace/0 route serves the portal-header
        region (置顶, 里程碑地图之上), the renderPortalHeader hydrator, and the
        worker→agent-档案 popup wiring (reuses showAgentPopup). Block order:
        门户头(project-header 之后)→ 里程碑地图 → 验证台 → 任务中心."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="portal-header-section"', html)
        self.assertIn("renderPortalHeader", html)
        # Worker chips reuse the 261/265 agent 档案 popup.
        self.assertIn("showAgentPopup", html)
        self.assertIn("parseWorker", html)
        # Graceful-hide guard present (S2).
        self.assertIn("section.hidden = true", html)
        # Block order: portal-header sits between the workspace header and milestone map.
        i_header = html.find('id="project-header"')
        i_portal = html.find('id="portal-header-section"')
        i_map = html.find('id="milestone-map-section"')
        for i in (i_header, i_portal, i_map):
            self.assertGreater(i, -1, "expected section missing")
        self.assertLess(i_header, i_portal)
        self.assertLess(i_portal, i_map)

    def test_workspace_route_serves_milestone_map_section(self) -> None:
        """S1/S4: the real /workspace/0 route serves the milestone-map region.
        F-262B-1: nodes draw only the marker (text -> popup); only the current
        node keeps a <=12-char short label; legend + click popup retained."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="milestone-map-section"', html)
        self.assertIn("项目里程碑", html)
        # Legend retained.
        self.assertIn('id="map-legend"', html)
        # Click-popup carries full detail (zero info loss).
        self.assertIn("showMilestonePopup", html)
        # Current node keeps a <=12-char short label.
        self.assertIn("truncate(d.current, 12)", html)
        # Non-current node text captions are gone (old per-node caption truncate removed).
        self.assertNotIn("truncate(caption, 34)", html)


class ProjectMapGracefulHideTests(_BaseServer):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # NOTE: no governance/project-map.md — region must hide.
        self._start(self.repo_root)

    def test_no_map_file_hides_gracefully(self) -> None:
        """S2: absent project-map.md -> available=false (graceful hide)."""
        status, body = _get(f"{self.base}/api/project-map?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["available"])
        # The static section is baked into the page (hidden by JS at runtime);
        # the API contract is the authoritative hide signal.
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="milestone-map-section"', html)
        self.assertIn("section.hidden = true", html)


class VerifyBenchContractTests(_BaseServer):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # Station card: owner_verify:required, audit PASS recorded, + a fix sibling.
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-st.md").write_text(
            "---\ntask_id: TASK-ST\ntitle: Station task\nstatus: claimed\nowner_verify: required\n"
            "---\n# TASK-ST\n\n## 验收断言\n\n- S1 first assertion\n- S2 second assertion\n",
            encoding="utf-8",
        )
        rd = self.repo_root / "5_tasks" / "records" / "returns" / "TASK-ST"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "return_r1.md").write_text(
            "---\nrecord_type: return_record\nreturn_id: return_r1\ntask_id: TASK-ST\n"
            "actor: exec.lybra.test\nreturned_at: '2026-07-01T00:30:00Z'\n"
            "executor_status: completed\naudit_readiness: ready\n---\n"
            "# Return\n\n## Summary\n\n- All contract tests pass.\n",
            encoding="utf-8",
        )
        vd = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / "TASK-ST"
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "verdict_v1.md").write_text(
            "---\nrecord_type: audit_verdict_record\nverdict_id: verdict_v1\nverdict: PASS\n"
            "reviewed_task_id: TASK-ST\nactor: audit.lybra.test\n"
            "verdict_at: '2026-07-01T00:40:00Z'\n---\n"
            "# Verdict\n\n## Summary\n\n- Findings: PASS, independent evidence.\n",
            encoding="utf-8",
        )
        # Fix sibling (same closure root TASK-ST).
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-stf1.md").write_text(
            "---\ntask_id: TASK-STF1\ntitle: Fix round 1\nstatus: claimed\n---\n# Fix\n",
            encoding="utf-8",
        )
        # Previewable card: owner_verify:required, in flight (no audit verdict).
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-pv.md").write_text(
            "---\ntask_id: TASK-PV\ntitle: Preview task\nstatus: claimed\nowner_verify: required\n"
            "---\n# TASK-PV\n\n## 验收断言\n\n- S1 preview assertion\n",
            encoding="utf-8",
        )
        self._start(self.repo_root)

    def test_verify_bench_key_names_and_evidence(self) -> None:
        """S3/S4: verify-bench carries stations + previewable with the documented
        key names; a station carries acceptance_assertions + three evidence rings."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        data = payload["data"]
        self.assertEqual(payload["summary"]["stations"], 1)
        self.assertEqual(payload["summary"]["previewable"], 1)
        # F-262B-4: TASK-ST has no finalize member -> not closed-loop -> stays a
        # station; closed_excluded is empty.
        self.assertEqual(payload["summary"]["closed_excluded"], 0)
        self.assertEqual(data["closed_excluded"], [])
        # Resolution is read-only (pass/reject deferred to candidate 13).
        self.assertFalse(data["writes_enabled"])
        self.assertFalse(data["resolution_enabled"])

        station = data["stations"][0]
        self.assertEqual(station["task_id"], "TASK-ST")
        self.assertEqual(station["true_stage"], "verdict_pass")
        # Acceptance assertions surfaced verbatim (original wording).
        self.assertEqual(station["acceptance_assertions"], ["S1 first assertion", "S2 second assertion"])
        # Three evidence rings present with documented key names.
        ev = station["evidence"]
        for ring in ("machine_judgment", "audit_verdict", "prior_fixes"):
            self.assertIn(ring, ev)
        self.assertTrue(ev["machine_judgment"]["present"])
        self.assertEqual(ev["machine_judgment"]["executor_status"], "completed")
        self.assertIn("All contract tests pass", ev["machine_judgment"]["summary_excerpt"])
        self.assertTrue(ev["audit_verdict"]["present"])
        self.assertEqual(ev["audit_verdict"]["verdict"], "PASS")
        self.assertIn("independent evidence", ev["audit_verdict"]["findings_excerpt"])
        self.assertEqual(len(ev["prior_fixes"]), 1)
        self.assertEqual(ev["prior_fixes"][0]["task_id"], "TASK-STF1")

        preview = data["previewable"][0]
        self.assertEqual(preview["task_id"], "TASK-PV")
        # Non-station card surfaces its acceptance criteria (它将被怎么验).
        self.assertEqual(preview["acceptance_assertions"], ["S1 preview assertion"])

    def test_workspace_route_serves_verify_bench_region(self) -> None:
        """S4/F-262B-2: the real /workspace/0 route carries the verify-bench section
        (static, hydrated by renderVerifyBench) and the needs-owner merge wiring."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="verify-bench-section"', html)
        self.assertIn("renderVerifyBench", html)
        self.assertIn("vbNeedsOwnerGroup", html)
        self.assertIn("验证台", html)
        # The standalone needs-owner section is gone (merged into verify bench).
        self.assertNotIn("createNeedsOwnerSection", html)
        # F-262B-3: station + preview cards collapse by default (head shows
        # 卡号+标题+待验徽章; click/Enter expands 断言+证据).
        self.assertIn("vb-toggle", html)            # 展开/收起 affordance
        self.assertIn("vb-details", html)           # collapsed content wrapper
        self.assertIn("vbToggle", html)             # disclosure toggle fn
        self.assertIn("vb-station collapsed", html)  # default-collapsed station
        self.assertIn("vb-preview collapsed", html)   # default-collapsed preview

    def test_workspace_block_order_map_verify_taskcenter(self) -> None:
        """F-262B-2: page block order is 门户头 -> 里程碑地图 -> 验证台 -> 任务中心."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        i_header = html.find('id="project-header"')
        i_map = html.find('id="milestone-map-section"')
        i_vb = html.find('id="verify-bench-section"')
        i_tc = html.find('id="task-center-section"')
        for i in (i_header, i_map, i_vb, i_tc):
            self.assertGreater(i, -1, "expected section missing")
        self.assertLess(i_header, i_map)
        self.assertLess(i_map, i_vb)
        self.assertLess(i_vb, i_tc)

    def test_routes_are_registered(self) -> None:
        """S4: both new API routes are registered on the live server (200, not 404)."""
        for path in ("/api/project-map?workspace=0", "/api/verify-bench?workspace=0"):
            status, _body = _get(f"{self.base}{path}")
            self.assertEqual(status, 200, f"{path} not registered")


class VerifyBenchClosedLoopExcludedTests(_BaseServer):
    """F-262B-4: 待验站排除已闭环任务(过渡判据=闭环即退站).

    A verdict-PASS main card whose closure unit already finalized (FZ member
    returned / 收编) is no longer 待验 — it exits the station and is surfaced in
    closed_excluded for transparency. Mirrors the real workspace where 260/261/265
    carry FZ return records yet sat in claimed/ with no completed/ dossier.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # Main card: owner_verify:required + audit PASS => would be a station...
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-cl.md").write_text(
            "---\ntask_id: TASK-CL\ntitle: Closed-loop task\nstatus: claimed\nowner_verify: required\n"
            "---\n# TASK-CL\n\n## 验收断言\n\n- S1 closed-loop assertion\n",
            encoding="utf-8",
        )
        rd = self.repo_root / "5_tasks" / "records" / "returns" / "TASK-CL"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "return_cl.md").write_text(
            "---\nrecord_type: return_record\nreturn_id: return_cl\ntask_id: TASK-CL\n"
            "actor: exec.lybra.test\nreturned_at: '2026-07-01T00:30:00Z'\n"
            "executor_status: completed\naudit_readiness: ready\n---\n# Return\n",
            encoding="utf-8",
        )
        vd = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / "TASK-CL"
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "verdict_cl.md").write_text(
            "---\nrecord_type: audit_verdict_record\nverdict_id: verdict_cl\nverdict: PASS\n"
            "reviewed_task_id: TASK-CL\nactor: audit.lybra.test\nverdict_at: '2026-07-01T00:40:00Z'\n"
            "---\n# Verdict\n",
            encoding="utf-8",
        )
        # ...BUT its closure unit already finalized (FZ member returned) => 闭环即退站.
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-clfz.md").write_text(
            "---\ntask_id: TASK-CLFZ\ntitle: Finalize\nstatus: claimed\n---\n# Finalize\n",
            encoding="utf-8",
        )
        fz = self.repo_root / "5_tasks" / "records" / "returns" / "TASK-CLFZ"
        fz.mkdir(parents=True, exist_ok=True)
        (fz / "return_clfz.md").write_text(
            "---\nrecord_type: return_record\nreturn_id: return_clfz\ntask_id: TASK-CLFZ\n"
            "actor: exec.lybra.test\nreturned_at: '2026-07-02T00:00:00Z'\n"
            "executor_status: completed\naudit_readiness: ready\n---\n# Finalize return\n",
            encoding="utf-8",
        )
        self._start(self.repo_root)

    def test_closed_loop_excluded_from_station(self) -> None:
        """F-262B-4 S2: finalized closure unit => main card exits the 待验站."""
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        data = payload["data"]
        # Not on the station (闭环即退站).
        self.assertEqual(data["stations"], [])
        self.assertEqual(payload["summary"]["stations"], 0)
        # Surfaced in closed_excluded (transparency).
        self.assertEqual(payload["summary"]["closed_excluded"], 1)
        ids = [c["task_id"] for c in data["closed_excluded"]]
        self.assertIn("TASK-CL", ids)
        # Not promoted to previewable either (it is done, not in flight).
        self.assertEqual(data["previewable"], [])


class PortalSegmentAbsentTests(_BaseServer):
    """AIPOS-264 S2: project-map.md present but WITHOUT a portal segment — the
    portal region hides gracefully (portal.description empty; the static section
    stays in the DOM, hidden by the renderPortalHeader guard)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "governance").mkdir(parents=True, exist_ok=True)
        # project-map.md with milestones but NO portal segment.
        (self.repo_root / "governance" / "project-map.md").write_text(
            "---\nmap_version: 1\nupdated: 2026-07-28\n"
            "milestones:\n  - id: m1\n    title: Only milestone\n    refs: []\n"
            "current: here\n---\n# map\n",
            encoding="utf-8",
        )
        self._start(self.repo_root)

    def test_no_portal_segment_yields_empty_portal(self) -> None:
        """S2: absent portal segment -> portal keys present but empty (hide signal)."""
        status, body = _get(f"{self.base}/api/project-map?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        portal = payload["data"]["portal"]
        # All five keys present (schema-stable) but empty -> frontend hides.
        self.assertEqual(portal["description"], "")
        self.assertEqual(portal["collab_mode"], "")
        self.assertEqual(portal["topology"], "")
        self.assertEqual(portal["workers"], [])
        self.assertEqual(portal["advisor"], "")

    def test_route_serves_portal_hide_guard(self) -> None:
        """S2: the portal-header section is baked into the page (hidden by JS at
        runtime when portal.description is empty); the hide guard is present."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="portal-header-section"', html)
        self.assertIn("renderPortalHeader", html)
        # The renderPortalHeader hide guard (no description -> hide).
        self.assertIn("section.hidden = true", html)


if __name__ == "__main__":
    unittest.main()
