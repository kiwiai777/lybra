"""AIPOS-266: four-area UI label i18n (portal header / milestone map /
verify bench / task center) — acceptance over the REAL routes.

Acceptance mapping:
- S1 (en mode, four areas carry no hardcoded zh chrome): asserted by proving the
  four-area chrome is WIRED through i18n.t() in the served page source, and that
  the i18n dictionary carries distinct zh/en values for those keys. (The board UI
  is client-rendered; default lang is zh, so a route fetch returns the static zh
  skeleton — the en contract is the i18n.t() wiring + the en dictionary.)
- S2 (record & statement content stays original, red line): asserted by proving
  the render paths still bind record-derived fields directly (portal.description,
  m.title, u.purpose …) and do NOT route content through i18n.
- S3 (zh default, zero regression) is covered by the existing real-route modules;
  here we additionally pin the static zh skeleton + ids so a future refactor that
  drops the i18n override (or the baked zh default) is caught.
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


class FourAreaI18nTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-z.md").write_text(
            "---\ntask_id: TASK-Z\ntitle: Z task\nstatus: claimed\n---\n# TASK-Z\n\nPurpose.\n",
            encoding="utf-8",
        )
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

    # ---- S1: four-area chrome is wired to i18n (no hardcoded zh in render paths) ----

    def test_workspace_detail_loads_i18n_and_four_sections(self) -> None:
        """GET /workspace/0 loads i18n.js and serves all four area sections."""
        status, html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn('src="/i18n.js"', html)
        for sid in ("portal-header-section", "milestone-map-section",
                    "verify-bench-section", "task-center-section"):
            self.assertIn(f'id="{sid}"', html)

    def test_four_area_static_headings_have_i18n_ids_and_zh_default(self) -> None:
        """S3 anchor: the baked zh skeleton + i18n ids must stay (curl-visible),
        and renderPage overrides each via i18n.t (so en mode rewrites them)."""
        _, html = _get(f"{self.base}/workspace/0")
        # Baked zh default (regression: the zh the existing route tests pin).
        self.assertIn('<h3 id="map-title">项目里程碑</h3>', html)
        self.assertIn('<h3 id="vb-title">验证台 · Owner 核验</h3>', html)
        self.assertIn('<h3 id="tc-title">任务中心 · Owner 真相摘要</h3>', html)
        # renderPage overrides each heading through i18n.t (the en switch path).
        self.assertIn("getElementById('map-title').textContent = i18n.t('map.title')", html)
        self.assertIn("getElementById('vb-title').textContent = i18n.t('vb.title')", html)
        self.assertIn("getElementById('tc-title').textContent = i18n.t('tc.title')", html)
        self.assertIn("getElementById('tc-hint').textContent = i18n.t('tc.hint')", html)

    def test_four_area_dynamic_chrome_uses_i18n_keys(self) -> None:
        """S1: the dynamic render paths (popups, badges, buttons, rings, legend,
        member chips, drawer) reference i18n keys, not raw zh literals."""
        _, html = _get(f"{self.base}/workspace/0")
        # Portal header collab labels + worker chip title.
        self.assertIn("i18n.t('portal.label.workers')", html)
        self.assertIn("i18n.t('portal.worker_chip_title')", html)
        # Milestone legend + popup kinds.
        self.assertIn("i18n.t('map.legend.done')", html)
        self.assertIn("i18n.t('map.popup.kind.done')", html)
        # Verify bench rings + pass/reject + station title.
        self.assertIn("i18n.t('vb.ring.machine')", html)
        self.assertIn("i18n.t('vb.action.pass')", html)
        self.assertIn("i18n.t('vb.station.head_title')", html)
        # Task center member chips + view-source + duration units.
        self.assertIn("i18n.t('tc.member.main')", html)
        self.assertIn("i18n.t('tc.view_card')", html)
        self.assertIn("i18n.t('tc.dur.sec')", html)
        # Shared popups (agent profile + md drawer).
        self.assertIn("i18n.t('agent.profile.latest')", html)
        self.assertIn("i18n.t('md.drawer.loading')", html)
        self.assertIn("i18n.t('popup.close')", html)

    def test_i18n_dictionary_has_distinct_zh_and_en_for_four_areas(self) -> None:
        """S1: /i18n.js carries zh AND en values for the four-area chrome, and
        en is a real translation (not a zh passthrough)."""
        _, js = _get(f"{self.base}/i18n.js")
        pairs = {
            "map.title": ("项目里程碑", "Project Milestones"),
            "vb.title": ("验证台 · Owner 核验", "Verify Bench · Owner Review"),
            "tc.title": ("任务中心 · Owner 真相摘要", "Task Center · Owner Truth Summary"),
            "portal.label.workers": ("牛马", "Workers"),
            "vb.ring.machine": ("机判记录", "Machine judgment"),
            "tc.view_card": ("查看原卡", "View source card"),
            "popup.close": ("关闭", "Close"),
        }
        for key, (zh_val, en_val) in pairs.items():
            self.assertIn(f"'{key}': '{zh_val}'", js, f"missing zh value for {key}")
            self.assertIn(f"'{key}': '{en_val}'", js, f"missing en value for {key}")
            self.assertNotEqual(zh_val, en_val, f"{key}: en must differ from zh")

    # ---- S2: red line — record & statement content stays original (not i18n) ----

    def test_record_content_is_not_translated(self) -> None:
        """S2 red line: record-derived content binds the data field directly,
        never routed through i18n. Spot-check the four areas' content bindings."""
        _, html = _get(f"{self.base}/workspace/0")
        # Portal description (statement content) — rendered as-is.
        self.assertIn("descEl.textContent = portal.description", html)
        # Milestone node title / current text — record content, as-is.
        self.assertIn("title: m.title || m.id", html)
        self.assertIn("desc: d.current", html)
        # Task purpose — content as-is, only the *fallback* is chrome (i18n).
        self.assertIn("(u.purpose || i18n.t('tc.purpose_fallback'))", html)
        # Verify bench assertions + stage_note — content as-is.
        self.assertIn("li.textContent = a; ul.appendChild(li)", html)
        self.assertIn("p.stage_note || i18n.t('vb.preview.note_fallback')", html)

    def test_data_matching_logic_keeps_zh_tokens(self) -> None:
        """The topology/role DATA matching still inspects the original zh tokens
        in the source data (these are value inspections, not displayed chrome)."""
        _, html = _get(f"{self.base}/workspace/0")
        self.assertIn("t.includes('星')", html)
        self.assertIn("roleText === '审计员'", html)


if __name__ == "__main__":
    unittest.main()
