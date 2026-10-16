"""AIPOS-262B: project milestone map + verification bench contract tests.

S4: contract (map schema + verify-bench key names) AND real-route assertions.
S2: graceful hide when project-map.md is absent (fixture assertion).

AIPOS-F171 件③(gap #156): the hand-written governance/project-map.md reading is offline (the file is kept, never read;
/api/project-map is gone) and so is the portal header built from it; the milestone region now shows the project's
roadmap declaration (roadmap.yaml, read-only simple view, /api/roadmap = `lybra roadmap show` data). The ProjectMap*
classes below keep their names but assert the new contract.

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
from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# AIPOS-270: board routes are now auth-gated; these real-route tests authenticate
# via an injected SessionStore + a session cookie minted in _start.
_AUTH_COOKIE: str | None = None


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url)
    if _AUTH_COOKIE:
        req.add_header("Cookie", _AUTH_COOKIE)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def _get_any(url: str) -> tuple[int, str]:
    """GET that returns (status, body) for 4xx too (AIPOS-F171: retired routes answer 404)."""
    import urllib.error

    try:
        return _get(url)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _write_roadmap(root: Path, text: str) -> None:
    (root / "governance").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "roadmap.yaml").write_text(text, encoding="utf-8")


ROADMAP_YAML = """milestones:
- id: m1
  lane: core
  title: First milestone
  target_date: '2026-11-30'
  status: active
  criteria:
  - id: c1
    statement: First criterion holds
    verify: auto
- id: m2
  lane: web
  title: Second milestone
"""


def _write_card(root: Path, state: str, task_id: str, extra: str = "") -> None:
    path = root / "5_tasks" / "queue" / state / f"{task_id.lower()}.md"
    path.write_text(f"---\ntask_id: {task_id}\ntitle: {task_id} title\nstatus: {state}\n{extra}---\n# {task_id}\n", encoding="utf-8")


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
        self._auth_store = SessionStore()
        _sid = self._auth_store.create(role="owner", scopes=["owner_confirm"])
        global _AUTH_COOKIE
        _AUTH_COOKIE = f"{SESSION_COOKIE_NAME}={_sid}"
        port = _free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(repo_root=repo_root, board_config_path=self.config_path, session_store=self._auth_store))
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self) -> None:
        global _AUTH_COOKIE
        _AUTH_COOKIE = None
        self.server.shutdown()
        self.server.server_close()
        self.temp_dir.cleanup()


class ProjectMapContractTests(_BaseServer):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "governance").mkdir(parents=True, exist_ok=True)
        # 旧手写项目地图仍在盘上(文件不删), 但看板不再读它
        (self.repo_root / "governance" / "project-map.md").write_text(PROJECT_MAP_MD, encoding="utf-8")
        _write_roadmap(self.repo_root, ROADMAP_YAML)
        _write_card(self.repo_root, "pending", "FIX-1", "milestone: m1\ncriteria:\n- c1\n")
        _write_card(self.repo_root, "completed", "FIX-2", "milestone: m1\n")
        _write_card(self.repo_root, "claimed", "FIX-3")
        self._start(self.repo_root)

    def test_project_map_schema_and_nested_parse(self) -> None:
        """AIPOS-F171: /api/project-map(读手写 project-map.md)已下线 = 404, 即使文件仍在; 文件原样保留。"""
        before = (self.repo_root / "governance" / "project-map.md").read_text(encoding="utf-8")
        status, body = _get_any(f"{self.base}/api/project-map?workspace=0")
        print(f"[F171 /api/project-map] {status} {body[:200]}")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "NOT_FOUND")
        self.assertEqual((self.repo_root / "governance" / "project-map.md").read_text(encoding="utf-8"), before)

    def test_direction_log_recent_latest_three(self) -> None:
        """AIPOS-F171: /api/roadmap = 路线图声明的里程碑与标准(与 `lybra roadmap show` 同一 roadmap_view)。"""
        status, body = _get(f"{self.base}/api/roadmap?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        print(f"[F171 /api/roadmap] {json.dumps(payload['data'], ensure_ascii=False)}")
        self.assertTrue(payload["ok"], payload)
        data = payload["data"]
        self.assertTrue(data["exists"])
        self.assertEqual(data["rel"], "governance/roadmap.yaml")
        self.assertEqual([m["id"] for m in data["milestones"]], ["m1", "m2"])
        m1 = data["milestones"][0]
        self.assertEqual((m1["lane"], m1["status"], m1["target_date"]), ("core", "active", "2026-11-30"))
        self.assertEqual(data["milestones"][1]["status"], "planned")  # 缺键 = 声明 default
        self.assertEqual(m1["criteria"][0]["id"], "c1")
        self.assertEqual(m1["criteria"][0]["verify"], "auto")

    def test_direction_log_date_suffix_compatible(self) -> None:
        """AIPOS-F171: 挂接卡数与游离卡(未挂里程碑)出自同一判定 roadmap.card_link_status。"""
        _status, body = _get(f"{self.base}/api/roadmap?workspace=0")
        data = json.loads(body)["data"]
        m1 = data["milestones"][0]
        self.assertEqual(sorted(m1["cards"]), ["FIX-1", "FIX-2"])
        self.assertEqual(m1["criteria"][0]["cards"], ["FIX-1"])
        self.assertEqual(data["milestones"][1]["cards"], [])
        self.assertEqual(data["unlinked"], ["FIX-3"])

    def test_portal_header_schema_five_keys(self) -> None:
        """AIPOS-F171: 门户头(project-map.md portal 段)随旧地图下线: 页面无门户区, 无 portal 水合。"""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertNotIn('id="portal-header-section"', html)
        self.assertNotIn("renderPortalHeader", html)
        self.assertNotIn("fetch(`/api/project-map", html)

    def test_workspace_route_serves_portal_header(self) -> None:
        """Block order (AIPOS-F171: portal header retired): project-header → 里程碑区 → 验证台 → 任务中心."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        order = [html.find(f'id="{sid}"') for sid in ("project-header", "milestone-map-section", "verify-bench-section",
                                                       "task-center-section")]
        for i in order:
            self.assertGreater(i, -1, "expected section missing")
        self.assertEqual(order, sorted(order))

    def test_workspace_route_serves_milestone_map_section(self) -> None:
        """AIPOS-F171: 里程碑区改读路线图(只读简版): 页面取 /api/roadmap 并经 renderRoadmap 渲染, 状态 / 核对方式走 i18n 键。"""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="milestone-map-section"', html)
        self.assertIn('id="map-title" data-i18n="map.title"', html)
        self.assertIn("/api/roadmap?workspace=", html)
        self.assertIn("function renderRoadmap(roadmapData)", html)
        self.assertIn("i18n.t('roadmap.status.'", html)
        self.assertNotIn("showMilestonePopup", html)

    def test_map_updated_badge_in_html(self) -> None:
        """AIPOS-F171: 里程碑区标题旁的 badge 改显示路线图来源路径(roadmap.source_prefix + rel)。"""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="map-updated-badge"', html)
        self.assertIn("i18n.t('roadmap.source_prefix') + (d.rel || '')", html)


class ProjectMapGracefulHideTests(_BaseServer):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # NOTE: no governance/roadmap.yaml — region must hide.
        self._start(self.repo_root)

    def test_no_map_file_hides_gracefully(self) -> None:
        """S2 → AIPOS-F171: 无路线图文件 -> exists=false(整区隐藏); 页面带隐藏守卫。"""
        status, body = _get(f"{self.base}/api/roadmap?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["exists"])
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('id="milestone-map-section"', html)
        self.assertIn("if (!d.exists) { section.hidden = true; return; }", html)


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
        # AIPOS-273: Resolution enabled (approve/reject buttons functional).
        self.assertTrue(data["writes_enabled"])
        self.assertTrue(data["resolution_enabled"])

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
        """S4: the read API routes are registered on the live server (200, not 404); AIPOS-F171: /api/roadmap replaces the
        retired /api/project-map (404)."""
        for path in ("/api/roadmap?workspace=0", "/api/verify-bench?workspace=0"):
            status, _body = _get(f"{self.base}{path}")
            self.assertEqual(status, 200, f"{path} not registered")
        self.assertEqual(_get_any(f"{self.base}/api/project-map?workspace=0")[0], 404)


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


class VerifyBenchOwnerApprovedExcludedTests(_BaseServer):
    """AIPOS-274F1: 待验站排除已核验任务(owner_verification approve 记录即退站,
    不必等 FZ 收编).

    Regression fixture pinning the 263 vs 273 divergence the advisor caught by
    eye: both cards carry an approve owner_verification record, but only 273's
    FZ member had returned. Before the fix, verify_bench.py's station-exclusion
    check (_closure_unit_finalized) only looked at FZ returns, so 263 kept
    reappearing on 待验站 despite the Owner already having approved it — a
    genuinely closed loop revived by a blind spot in the exclusion logic.
    Two fixtures, side by side: TASK-APPROVED (approve record, FZ member
    still pending) must be excluded; TASK-UNAPPROVED (same shape, no
    owner_verification record at all) must still surface on the station.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

        def _card(task_id: str) -> None:
            (self.repo_root / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md").write_text(
                f"---\ntask_id: {task_id}\ntitle: {task_id} main card\nstatus: claimed\n"
                "owner_verify: required\n---\n"
                f"# {task_id}\n\n## 验收断言\n\n- S1 assertion\n",
                encoding="utf-8",
            )
            rd = self.repo_root / "5_tasks" / "records" / "returns" / task_id
            rd.mkdir(parents=True, exist_ok=True)
            (rd / f"return_{task_id.lower()}.md").write_text(
                f"---\nrecord_type: return_record\nreturn_id: return_{task_id.lower()}\ntask_id: {task_id}\n"
                "actor: exec.lybra.test\nreturned_at: '2026-07-30T00:20:00Z'\n"
                "executor_status: completed\naudit_readiness: ready\n---\n# Return\n",
                encoding="utf-8",
            )
            vd = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / task_id
            vd.mkdir(parents=True, exist_ok=True)
            (vd / f"verdict_{task_id.lower()}.md").write_text(
                f"---\nrecord_type: audit_verdict_record\nverdict_id: verdict_{task_id.lower()}\nverdict: PASS\n"
                f"reviewed_task_id: {task_id}\nactor: audit.lybra.test\nverdict_at: '2026-07-30T00:40:00Z'\n"
                "---\n# Verdict\n",
                encoding="utf-8",
            )
            # FZ member declared but NOT returned yet (still pending) — the
            # exact shape that tripped up _closure_unit_finalized-only exclusion.
            (self.repo_root / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}fz.md").write_text(
                f"---\ntask_id: {task_id}FZ\ntitle: Finalize\nstatus: pending\n---\n# Finalize\n",
                encoding="utf-8",
            )

        _card("TASK-APPROVED")
        _card("TASK-UNAPPROVED")

        # Only TASK-APPROVED carries an Owner approve record.
        vr = self.repo_root / "5_tasks" / "records" / "owner_verifications" / "TASK-APPROVED"
        vr.mkdir(parents=True, exist_ok=True)
        (vr / "verify_approved_nofz_20260730T053058.md").write_text(
            "---\ndecided_at: '2026-07-30T05:30:58Z'\ndecided_by: owner\n"
            "decided_via: web_session\ndecision: approve\nreason:\n"
            "record_type: owner_verification\ntask_id: TASK-APPROVED\n---\n"
            "# Owner 核验记录: TASK-APPROVED\n",
            encoding="utf-8",
        )

        self._start(self.repo_root)

    def test_approved_without_fz_excluded_unapproved_still_stations(self) -> None:
        status, body = _get(f"{self.base}/api/verify-bench?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        data = payload["data"]

        station_ids = [s["task_id"] for s in data["stations"]]
        closed_ids = [c["task_id"] for c in data["closed_excluded"]]

        # Approved (even without FZ return yet) => excluded from 待验站.
        self.assertNotIn("TASK-APPROVED", station_ids)
        self.assertIn("TASK-APPROVED", closed_ids)

        # No approval record at all => stays on 待验站 (real work still pending).
        self.assertIn("TASK-UNAPPROVED", station_ids)
        self.assertNotIn("TASK-UNAPPROVED", closed_ids)


class PortalSegmentAbsentTests(_BaseServer):
    """AIPOS-264 S2 → AIPOS-F171: the portal header is retired; this class now covers a roadmap file that does not match
    the declaration (config.schema governance_structure.roadmap): /api/roadmap answers BLOCK with ROADMAP_INVALID (named, not
    swallowed) and the page renders the error line instead of a stale map."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        _write_roadmap(self.repo_root, "milestones:\n- id: m1\n  title: lane missing\n  status: someday\n")
        self._start(self.repo_root)

    def test_no_portal_segment_yields_empty_portal(self) -> None:
        status, body = _get(f"{self.base}/api/roadmap?workspace=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        print(f"[F171 /api/roadmap invalid] {json.dumps(payload, ensure_ascii=False)[:600]}")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["verdict"], "BLOCK")
        reason = json.dumps(payload, ensure_ascii=False)
        self.assertIn("ROADMAP_INVALID", reason)
        self.assertIn("lane", reason)
        self.assertIn("someday", reason)

    def test_route_serves_portal_hide_guard(self) -> None:
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn("i18n.t('roadmap.error')", html)
        self.assertNotIn('id="portal-header-section"', html)


if __name__ == "__main__":
    unittest.main()
