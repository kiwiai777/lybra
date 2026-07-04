"""AIPOS-242 (Slice D) — `lybra_project_status`: the gate's OWN read-only project view.

The single source of truth for the project view is the GATE (F-o3-2 / F-o3-18). This tool
self-reports what the gate resolves: home_root, active_project (or the resolution error —
REPORTED, not crashed), and the established projects (same criterion as the single-project
fallback: 5_tasks/queue + project.json). It is registered like every other tool — project-gated
at the dispatch choke-point with NO exemption (the 19/0 enumeration in
test_token_project_enforcement covers it; the explicit in/out-of-scope pair here pins both sides).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.mcp_server import tools as gate
from tools.mcp_server.tools import dispatch_tool, lybra_project_status, request_capability_scope

_VALID = "2999-01-01T00:00:00Z"


def _cap(*, projects=None):
    cap = {
        "token_ref": "t",
        "role": "executor",
        "operations": ["queue_claim"],
        "expires_at": _VALID,
        "source": "service_v0",
    }
    if projects is not None:
        cap["projects"] = list(projects)
        cap["projects_enforced"] = True
    return cap


class ProjectStatusToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        # two ESTABLISHED projects (queue + project.json) + one decoy (queue only, no project.json)
        for name in ("lybra", "demo"):
            root = self.home / name
            (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
            (root / "project.json").write_text(json.dumps({"project": name}), encoding="utf-8")
        decoy = self.home / "not-established"
        (decoy / "5_tasks" / "queue").mkdir(parents=True)  # queue but NO project.json
        self.workspace = self.home / "lybra"
        # hermetic env: fake HOME (isolates the REAL ~/.lybra/config.json), explicit home root
        self.env = {
            "HOME": str(Path(self.temp.name) / "fakehome"),
            "AIPOS_WORKSPACE_ROOT": str(self.workspace),
            "LYBRA_HOME_ROOT": str(self.home),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _paths(self) -> list[str]:
        return sorted(p.as_posix() for p in Path(self.temp.name).rglob("*"))

    def test_payload_reports_gate_view_and_writes_nothing(self) -> None:
        env = dict(self.env)
        env["LYBRA_ACTIVE_PROJECT"] = "demo"  # deterministic resolution (env step)
        before = self._paths()
        with patch.dict(os.environ, env, clear=True):
            result = lybra_project_status({})
        after = self._paths()
        self.assertEqual(before, after)  # ZERO write
        payload = result["structuredContent"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "gate")
        self.assertEqual(Path(payload["home_root"]), self.home.resolve())
        self.assertEqual(payload["active_project"], "demo")
        self.assertIsNone(payload["resolution_error"])
        # established criterion (queue + project.json): decoy excluded
        self.assertEqual(payload["projects"], ["demo", "lybra"])

    def test_resolution_failure_is_reported_not_crashed(self) -> None:
        # no env, fake HOME (no global config), 2 established projects -> PROJECT_AMBIGUOUS.
        # The tool REPORTS the gate's failure honestly; it does not raise and does not guess.
        with patch.dict(os.environ, self.env, clear=True):
            result = lybra_project_status({})
        payload = result["structuredContent"]
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["active_project"])
        self.assertIn("PROJECT_AMBIGUOUS", str(payload["resolution_error"]))
        self.assertEqual(payload["projects"], ["demo", "lybra"])

    def test_project_gated_out_of_scope_denied_in_scope_allowed(self) -> None:
        # Same choke-point as every tool; NO exemption. Out-of-scope active -> DENIED (and the
        # standardized deny message names the gate-resolved project — the client's honest signal);
        # in-scope -> the handler runs and reports.
        with patch.object(gate, "_repo_root", return_value=self.workspace), patch.object(
            gate, "_resolve_active_project_for", return_value="demo"
        ), patch.dict(os.environ, self.env, clear=True):
            with request_capability_scope(_cap(projects=["lybra"])):
                denied = dispatch_tool("lybra_project_status", {})
                sc = denied["structuredContent"]
                self.assertEqual(sc["error_code"], "PROJECT_SCOPE_DENIED")
                self.assertIn("active project 'demo'", sc["message"])
            with request_capability_scope(_cap(projects=["demo"])):
                allowed = dispatch_tool("lybra_project_status", {})
                sc = allowed["structuredContent"]
                self.assertTrue(sc["ok"])
                self.assertEqual(sc["active_project"], "demo")


if __name__ == "__main__":
    unittest.main()
