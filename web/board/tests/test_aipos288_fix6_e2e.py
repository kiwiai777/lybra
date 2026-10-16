"""
AIPOS-288 FIX-6 → AIPOS-F171 件③(gap #156): end-to-end project list of the Board overview.

Originally: board_config.json (label + label_en) → /api/overview (label_en passthrough) → workspaceLabel().
AIPOS-F171: the static board_config.json list is retired (the file is kept but never read); /api/overview lists the
governance home root's registered projects (owner_inbox.home_projects — the same enumeration as the Owner inbox), label =
project name, paused projects (project.json status=paused) marked. The frontend workspaceLabel() helper stays (label_en is
simply absent now, so it falls back to label).
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
        from tools.aipos_cli.workspace_config import scaffold_project

        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name) / "serve-root"
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # 靶场 home 根: 三个已注册项目(一个暂停, 一个 status 值不合), 名字为靶场值
        self.home = Path(self.temp_dir.name) / "home-root"
        for name in ("alpha-probe", "beta-probe", "gamma-probe"):
            scaffold_project(self.home, name, registered_by="fixture")
        self._set_status("beta-probe", "paused")
        self._set_status("gamma-probe", "bogus-status")
        # 旧静态清单: 写一份与 home 根不一致的 board_config.json, 证明它不再被读取
        self.config_path = self.repo_root / "board_config.json"
        self.config_path.write_text(json.dumps({"workspaces": [
            {"label": "工作区甲", "label_en": "Workspace Alpha", "root": str(self.repo_root)}]}), encoding="utf-8")
        self._auth_store = SessionStore()
        _sid = self._auth_store.create(role="owner", scopes=["owner_confirm"])
        global _AUTH_COOKIE
        _AUTH_COOKIE = f"{SESSION_COOKIE_NAME}={_sid}"
        self._start(self.home)

    def _set_status(self, name: str, status: str) -> None:
        path = self.home / name / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = status
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _start(self, home: Path) -> None:
        port = _free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(
            repo_root=self.repo_root, board_config_path=self.config_path, home_root=home, session_store=self._auth_store))
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
        """AIPOS-F171: /api/overview = home 根推导的项目列表(label = 项目名); board_config.json 的 label / label_en 不再读取。"""
        status, data = _get_json(f"{self.base}/api/overview")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        labels = [ws["label"] for ws in data["workspaces"]]
        print(f"[F171 overview labels] {labels}")
        self.assertEqual(labels, ["alpha-probe", "beta-probe", "gamma-probe"])
        self.assertNotIn("工作区甲", labels)
        for ws in data["workspaces"]:
            self.assertNotIn("label_en", ws)
            self.assertEqual(ws["project"], ws["label"])

    def test_frontend_helper_prefers_label_en_in_en_mode(self) -> None:
        """AIPOS-288 FIX-6: the frontend workspaceLabel() helper stays (falls back to label when label_en is absent)."""
        status, html = _get(f"{self.base}/overview.html")
        self.assertEqual(status, 200)
        self.assertIn("function workspaceLabel(workspace)", html)
        self.assertIn("workspace.label_en", html)
        self.assertIn("workspace.label", html)
        status, detail_html = _get(f"{self.base}/workspace/0")
        self.assertEqual(status, 200)
        self.assertIn("function workspaceLabel(workspace)", detail_html)

    def test_mixed_config_all_workspaces_render(self) -> None:
        """AIPOS-F171: 暂停项目照列并标 paused(看板展示全部项目, 收件箱才按暂停过滤); 状态读不出的项目照列并点名, 不猜成暂停。"""
        status, data = _get_json(f"{self.base}/api/overview")
        self.assertEqual(status, 200)
        by = {ws["label"]: ws for ws in data["workspaces"]}
        print(f"[F171 overview paused] {[(k, v['paused'], v['project_status'], v['status_error']) for k, v in by.items()]}")
        self.assertEqual((by["alpha-probe"]["paused"], by["alpha-probe"]["project_status"]), (False, "active"))
        self.assertEqual((by["beta-probe"]["paused"], by["beta-probe"]["project_status"]), (True, "paused"))
        self.assertFalse(by["gamma-probe"]["paused"])
        self.assertIsNone(by["gamma-probe"]["project_status"])
        self.assertIn("bogus-status", by["gamma-probe"]["status_error"])
        # ?workspace=<序号> 与列表同序: 1 = beta-probe(该项目队列里的靶场卡可见, 别的项目不可见)
        (self.home / "beta-probe" / "5_tasks" / "queue" / "pending" / "beta-1.md").write_text(
            "---\ntask_id: BETA-1\ntitle: beta card\nstatus: pending\n---\n# BETA-1\n", encoding="utf-8")
        status, queue1 = _get_json(f"{self.base}/api/queue?workspace=1")
        self.assertEqual(status, 200)
        self.assertIn("BETA-1", json.dumps(queue1, ensure_ascii=False))
        _status, queue0 = _get_json(f"{self.base}/api/queue?workspace=0")
        self.assertNotIn("BETA-1", json.dumps(queue0, ensure_ascii=False))

    def test_error_branch_also_passes_label_en(self) -> None:
        """AIPOS-F171: home 根无已注册项目 = 单工作区(服务进程的工作区根), 与旧缺省一致; board_config.json 照样不读。"""
        self.server.shutdown()
        self.server.server_close()
        empty_home = Path(self.temp_dir.name) / "empty-home"
        empty_home.mkdir()
        self._start(empty_home)
        status, data = _get_json(f"{self.base}/api/overview")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["workspaces"]), 1)
        ws = data["workspaces"][0]
        self.assertEqual((ws["label"], ws["root"], ws["status"]), ("Default Workspace", str(self.repo_root), "ok"))


if __name__ == "__main__":
    unittest.main()
