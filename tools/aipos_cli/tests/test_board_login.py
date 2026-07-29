"""AIPOS-271 — board 无感登录 CLI 客户端(``board open`` / ``board approve``)单元 + 集成测试。

覆盖:
  - ``load_role_token``:角色精确 / role=None 优先级 / 缺失抛错。
  - ``resolve_board_url`` / ``build_login_url`` 优先级。
  - 真实 board server 集成:``mint_otc`` 铸 OTC、OTC redeem 换 cookie、``approve_device`` 设备码流。

红线钉:原始 token 不进 stdout/不写 argv(本测试不 assert token 不被打印,因测试夹具需读取;
真实运行时 token 仅进程内传递)。零依赖(stdlib)。
"""
from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.aipos_cli.board_login import (
    approve_device,
    build_login_url,
    load_role_token,
    mint_otc,
    resolve_board_url,
    token_fingerprint,
)
from web.board.app import SESSION_COOKIE_NAME, SessionStore, _token_fingerprint, make_handler, parse_session_cookie
from web.board.auth_otc import DeviceCodeStore, OTCStore


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LoadRoleTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.owner = "owner-cli-XYZ"
        self.exec = "exec-cli-ABC"
        self.aud = "aud-cli-DEF"
        self.conn = self.root / "connection.json"
        self.conn.write_text(
            json.dumps({"board": {"url": "http://127.0.0.1:7117"}, "tokens": [
                {"role": "executor", "token": self.exec, "fingerprint": _token_fingerprint(self.exec)},
                {"role": "auditor", "token": self.aud, "fingerprint": _token_fingerprint(self.aud)},
                {"role": "owner", "token": self.owner, "fingerprint": _token_fingerprint(self.owner)},
            ]}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_explicit_role(self) -> None:
        tok, role = load_role_token(self.conn, role="auditor")
        self.assertEqual((tok, role), (self.aud, "auditor"))

    def test_role_none_prefers_owner(self) -> None:
        tok, role = load_role_token(self.conn)
        self.assertEqual(role, "owner")
        self.assertEqual(tok, self.owner)

    def test_role_none_without_owner_takes_preferred_pool(self) -> None:
        self.conn.write_text(
            json.dumps({"tokens": [
                {"role": "executor", "token": self.exec, "fingerprint": _token_fingerprint(self.exec)},
                {"role": "auditor", "token": self.aud, "fingerprint": _token_fingerprint(self.aud)},
            ]}),
            encoding="utf-8",
        )
        tok, role = load_role_token(self.conn)
        self.assertEqual(role, "auditor")  # preferred order: auditor before executor

    def test_missing_role_raises(self) -> None:
        with self.assertRaises(ValueError):
            load_role_token(self.conn, role="nope")

    def test_empty_tokens_raises(self) -> None:
        self.conn.write_text(json.dumps({"tokens": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_role_token(self.conn)

    def test_fingerprint_is_not_raw_token(self) -> None:
        self.assertEqual(token_fingerprint(self.owner), "sha256:" + _token_fingerprint(self.owner).split(":", 1)[1])
        self.assertNotIn(self.owner, token_fingerprint(self.owner))


class ResolveUrlTests(unittest.TestCase):
    def test_explicit_url_wins(self) -> None:
        self.assertEqual(resolve_board_url(url="http://example.com:9000/"), "http://example.com:9000")

    def test_host_port(self) -> None:
        self.assertEqual(resolve_board_url(host="1.2.3.4", port=8000), "http://1.2.3.4:8000")

    def test_connection_json_board_url(self) -> None:
        tmp = tempfile.mkdtemp()
        conn = Path(tmp) / "connection.json"
        conn.write_text(json.dumps({"board": {"url": "http://9.9.9.9:4242"}}), encoding="utf-8")
        self.assertEqual(resolve_board_url(conn), "http://9.9.9.9:4242")

    def test_default_when_nothing(self) -> None:
        tmp = tempfile.mkdtemp()
        conn = Path(tmp) / "nope.json"  # 不存在
        self.assertEqual(resolve_board_url(conn), "http://127.0.0.1:7117")

    def test_build_login_url(self) -> None:
        self.assertEqual(build_login_url("http://h:1", "/login?otc=abc"), "http://h:1/login?otc=abc")
        self.assertEqual(build_login_url("http://h:1/", "https://abs/x"), "https://abs/x")


class _Boardd:
    """In-process real board server bound to a free port; connection.json carries board.url."""

    def __init__(self, conn_path: Path) -> None:
        self.conn_path = conn_path
        data = json.loads(conn_path.read_text(encoding="utf-8"))
        self.base_url = data["board"]["url"]
        port = _free_port()
        # 重写 board.url 到真实端口(connection.json 里的端口只是给 resolve_board_url 读)。
        from urllib.parse import urlparse
        host = urlparse(self.base_url).hostname or "127.0.0.1"
        self.base_url = f"http://{host}:{port}"
        self.server = ThreadingHTTPServer(
            (host, port),
            make_handler(
                repo_root=conn_path.parent,
                session_store=SessionStore(),
                connection_paths=[conn_path],
                otc_store=OTCStore(),
                device_store=DeviceCodeStore(),
                auth_log_path=conn_path.parent / "auth-log.jsonl",
            ),
        )
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._t.start()

    def get(self, path: str, cookie: str | None = None) -> tuple[int, str | None, str | None]:
        from urllib.parse import urlparse
        u = urlparse(self.base_url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
        headers = {"Cookie": cookie} if cookie else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        sc, loc = resp.getheader("Set-Cookie"), resp.getheader("Location")
        conn.close()
        return resp.status, sc, loc

    def device_code(self) -> str:
        from urllib.parse import urlparse
        u = urlparse(self.base_url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
        conn.request("POST", "/api/auth/device/code", body=b"{}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8") or "{}")
        conn.close()
        return data["code"]

    def device_poll(self, code: str) -> tuple[int, str | None, dict]:
        from urllib.parse import urlparse
        u = urlparse(self.base_url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
        conn.request("POST", "/api/auth/device/poll", body=json.dumps({"code": code}).encode(), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        sc = resp.getheader("Set-Cookie")
        conn.close()
        return resp.status, sc, json.loads(body or "{}")

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _make_conn(tmp: Path, owner_tok: str) -> Path:
    conn = tmp / "connection.json"
    conn.write_text(
        json.dumps({"board": {"url": "http://127.0.0.1:7117"}, "tokens": [
            {"role": "owner", "token_ref": "svc-owner", "scopes": ["owner_confirm"],
             "token": owner_tok, "fingerprint": _token_fingerprint(owner_tok)},
        ]}),
        encoding="utf-8",
    )
    return conn


class CliServerIntegrationTests(unittest.TestCase):
    """CLI 客户端 ↔ 真实 board server 端到端。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for st in ("pending", "claimed", "completed", "blocked"):
            (self.root / "5_tasks" / "queue" / st).mkdir(parents=True, exist_ok=True)
        self.owner_tok = "owner-integration-271"
        self.conn = _make_conn(self.root, self.owner_tok)
        self.boardd = _Boardd(self.conn)

    def tearDown(self) -> None:
        self.boardd.shutdown()
        self._tmp.cleanup()

    def test_board_open_mints_otc_and_browser_redeems(self) -> None:
        # CLI: mint_otc(文件权即身份 —— 读 connection.json 的 owner token)
        result = mint_otc(self.boardd.base_url, self.owner_tok)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.otc)
        login_url = build_login_url(self.boardd.base_url, result.login_url)
        self.assertIn(result.otc, login_url)
        # 浏览器 GET /login?otc=… → 302 到 / + Set-Cookie(进站)。
        status, sc, loc = self.boardd.get(f"/login?otc={result.otc}")
        self.assertEqual((status, loc), (302, "/"))
        self.assertIsNotNone(sc)
        cookie = f"{SESSION_COOKIE_NAME}={parse_session_cookie(sc)}"
        status, _sc, _loc = self.boardd.get("/api/health", cookie=cookie)
        self.assertEqual(status, 200)

    def test_board_open_bad_token_fails_clean(self) -> None:
        result = mint_otc(self.boardd.base_url, "wrong-token")
        self.assertFalse(result.ok)
        self.assertIn("无效", result.error)

    def test_approve_device_full_flow(self) -> None:
        code = self.boardd.device_code()
        ok, msg = approve_device(self.boardd.base_url, self.owner_tok, code)
        self.assertTrue(ok, msg)
        # 远端浏览器 poll → approved + cookie
        _s, sc, body = self.boardd.device_poll(code)
        self.assertEqual(body.get("status"), "approved")
        self.assertIsNotNone(sc)
        cookie = f"{SESSION_COOKIE_NAME}={parse_session_cookie(sc)}"
        status, _sc, _loc = self.boardd.get("/api/health", cookie=cookie)
        self.assertEqual(status, 200)

    def test_approve_device_bad_token_rejected(self) -> None:
        code = self.boardd.device_code()
        ok, msg = approve_device(self.boardd.base_url, "wrong", code)
        self.assertFalse(ok)
        # 码仍是 pending(approve 未绑身份)
        _s, _sc, body = self.boardd.device_poll(code)
        self.assertEqual(body.get("status"), "pending")

    def test_approve_device_unknown_code(self) -> None:
        ok, msg = approve_device(self.boardd.base_url, self.owner_tok, "000000")
        self.assertFalse(ok)
