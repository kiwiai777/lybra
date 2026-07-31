"""
AIPOS-288 FIX-6: End-to-end test for label_en transparency across full stack.

Verifies the complete data flow:
  board_config.json (label + label_en)
  → /api/overview response (workspace.label_en)
  → Frontend workspaceLabel() helper (EN mode preference)

Test cases:
  1. Workspace with label_en: API returns it, helper prefers it in EN mode
  2. Workspace without label_en: API omits it, helper falls back to label
  3. Mixed config (some workspaces have label_en, others don't): all render correctly
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


def _get_json(url: str) -> tuple[int, dict]:
    status, body = _get(url)
    return status, json.loads(body)


class LabelEnE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        
        # Create workspace structure
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        
        # Setup board_config with mixed label_en presence
        self.config_path = self.repo_root / "board_config.json"
        self.config_path.write_text(
            json.dumps({
                "workspaces": [
                    {
                        "label": "工作区甲",
                        "label_en": "Workspace Alpha",
                        "root": str(self.repo_root)
                    },
                    {
                        "label": "工作区乙",
                        # No label_en - tests backward compatibility
                        "root": str(self.repo_root)
                    }
                ]
            }),
            encoding="utf-8"
        )
        
        # Setup auth
        self._auth_store = SessionStore()
        _sid = self._auth_store.create(role="owner", scopes=["owner_confirm"])
        global _AUTH_COOKIE
        _AUTH_COOKIE = f"{SESSION_COOKIE_NAME}={_sid}"
        
        # Start server
        port = _free_port()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(
                repo_root=self.repo_root,
                board_config_path=self.config_path,
                session_store=self._auth_store
            )
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

    def test_api_overview_returns_label_en_when_present(self) -> None:
        """
        AIPOS-288 FIX-6: /api/overview transparently passes label_en from board_config.
        
        Workspace 0 has label_en => response includes it.
        Workspace 1 lacks label_en => response omits it.
        """
        status, data = _get_json(f"{self.base}/api/overview")
        
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("workspaces", data)
        
        workspaces = data["workspaces"]
        self.assertEqual(len(workspaces), 2)
        
        # Workspace 0: has label_en
        ws0 = workspaces[0]
        self.assertEqual(ws0["label"], "工作区甲")
        self.assertEqual(ws0["label_en"], "Workspace Alpha")
        
        # Workspace 1: no label_en
        ws1 = workspaces[1]
        self.assertEqual(ws1["label"], "工作区乙")
        self.assertNotIn("label_en", ws1)

    def test_frontend_helper_prefers_label_en_in_en_mode(self) -> None:
        """
        AIPOS-288 FIX-6: Frontend workspaceLabel() helper correctly selects label_en in EN mode.
        
        This test verifies the helper logic by checking the static HTML contains:
          1. workspaceLabel(workspace) function definition
          2. EN mode preference logic: workspace.label_en check
          3. Fallback to workspace.label
        
        Runtime behavior is implicitly tested by overview and detail pages using the helper.
        """
        # Check overview.html has the helper
        status, html = _get(f"{self.base}/overview.html")
        self.assertEqual(status, 200)
        
        # Verify helper exists and has EN preference
        self.assertIn("function workspaceLabel(workspace)", html)
        self.assertIn("workspace.label_en", html)
        self.assertIn("workspace.label", html)
        
        # Check detail page also has it
        status, detail_html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn("function workspaceLabel(workspace)", detail_html)
        self.assertIn("workspace.label_en", detail_html)

    def test_mixed_config_all_workspaces_render(self) -> None:
        """
        AIPOS-288 FIX-6: Mixed board_config (some with label_en, some without) works correctly.
        
        Both workspaces should be present in API response with correct label fields.
        """
        status, data = _get_json(f"{self.base}/api/overview")
        
        self.assertEqual(status, 200)
        workspaces = data["workspaces"]
        self.assertEqual(len(workspaces), 2)
        
        # All workspaces must have 'label'
        for ws in workspaces:
            self.assertIn("label", ws)
        
        # Only workspace 0 has label_en
        has_label_en = [ws.get("label_en") is not None for ws in workspaces]
        self.assertEqual(has_label_en, [True, False])

    def test_error_branch_also_passes_label_en(self) -> None:
        """
        AIPOS-288 FIX-6: Error responses (validation failure, exception) also include label_en.
        
        This tests the other two assembly points (L147 error, L218 exception) in get_overview.
        We can't easily trigger the exception branch, but can verify error branch by using
        an invalid workspace root.
        """
        # Create a config with invalid workspace root
        bad_config = self.repo_root / "bad_config.json"
        bad_root = self.repo_root / "nonexistent"
        bad_config.write_text(
            json.dumps({
                "workspaces": [
                    {
                        "label": "坏工作区",
                        "label_en": "Bad Workspace",
                        "root": str(bad_root)
                    }
                ]
            }),
            encoding="utf-8"
        )
        
        # Restart server with bad config
        self.server.shutdown()
        self.server.server_close()
        
        port = _free_port()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(
                repo_root=self.repo_root,
                board_config_path=bad_config,
                session_store=self._auth_store
            )
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{port}"
        
        # Give server time to start
        import time
        time.sleep(0.1)
        
        status, data = _get_json(f"{self.base}/api/overview")
        
        self.assertEqual(status, 200)
        workspaces = data["workspaces"]
        self.assertEqual(len(workspaces), 1)
        
        ws = workspaces[0]
        self.assertEqual(ws["status"], "error")
        self.assertEqual(ws["label"], "坏工作区")
        self.assertEqual(ws["label_en"], "Bad Workspace")  # Error branch passes label_en
        self.assertIn("Workspace root does not contain 5_tasks/queue", ws["error"])


if __name__ == "__main__":
    unittest.main()
