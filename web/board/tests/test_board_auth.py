"""AIPOS-270 — board 鉴权:owner/角色 token 登录 + 会话 cookie。

测试四象限(卡 §测试):
  - 未登录 302 到登录页(页面 + /api/**,静态资源放行)
  - 错 token 拒(401)
  - 正确登录后读面正常(owner + 非 owner 读面一致)
  - cookie 篡改/未知 → 拒

红线钉:零依赖(stdlib);token 明文不落日志、不进 cookie、不进会话表;常量时间指纹比较。
"""
from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from web.board.app import (
    SESSION_COOKIE_NAME,
    SessionStore,
    _token_fingerprint,
    build_clear_cookie_header,
    build_session_cookie_header,
    is_authorized,
    is_public_path,
    is_static_asset_path,
    make_handler,
    parse_session_cookie,
    verify_login_token,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """Minimal real-HTTP board server over http.client (no auto-redirect)."""

    def __init__(self, repo_root: Path, connection_path: Path, store: SessionStore) -> None:
        self.store = store
        self.connection_path = connection_path
        port = _free_port()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(repo_root=repo_root, session_store=store, connection_paths=[connection_path]),
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.port = port

    def request(self, method: str, path: str, body: dict | None = None, cookie: str | None = None) -> tuple[int, str, str | None, str | None]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers: dict[str, str] = {}
        if cookie:
            headers["Cookie"] = cookie
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        set_cookie = resp.getheader("Set-Cookie")
        location = resp.getheader("Location")
        conn.close()
        return resp.status, data, set_cookie, location

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _make_fixture() -> tuple[tempfile.TemporaryDirectory, Path, Path, str, str]:
    """Temp repo + connection.json with one owner + one executor token. Returns
    (tmpdir, repo_root, connection_path, owner_token, executor_token)."""
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name)
    for state in ("pending", "claimed", "completed", "blocked"):
        (repo / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
    owner_tok = "owner-plaintext-secret-ABCD270"
    exec_tok = "executor-plaintext-secret-EFGH270"
    conn = {
        "tokens": [
            {"role": "owner", "token_ref": "svc-owner", "scopes": ["owner_confirm"], "token": owner_tok, "fingerprint": _token_fingerprint(owner_tok)},
            {"role": "executor", "token_ref": "svc-exec", "scopes": ["queue_claim"], "token": exec_tok, "fingerprint": _token_fingerprint(exec_tok)},
        ]
    }
    conn_path = repo / "connection.json"
    conn_path.write_text(json.dumps(conn), encoding="utf-8")
    return tmp, repo, conn_path, owner_tok, exec_tok


class BoardAuthHttpTests(unittest.TestCase):
    """Real-HTTP end-to-end auth gate tests."""

    def setUp(self) -> None:
        self._tmp, self.repo, self.conn_path, self.owner_tok, self.exec_tok = _make_fixture()
        self.server = _Server(self.repo, self.conn_path, SessionStore())

    def tearDown(self) -> None:
        self.server.shutdown()
        self._tmp.cleanup()

    def _login(self, token: str) -> str | None:
        status, _data, set_cookie, _loc = self.server.request("POST", "/api/auth/login", {"token": token})
        self.assertEqual(status, 200, f"login failed: {status}")
        self.assertIsNotNone(set_cookie)
        return parse_session_cookie(set_cookie)

    # ---- S1: 未登录任何数据路由/页面不可达 → 302 到 /login ----

    def test_unauthenticated_api_route_redirects_to_login(self) -> None:
        status, _body, _sc, loc = self.server.request("GET", "/api/health")
        self.assertEqual(status, 302)
        self.assertEqual(loc, "/login")

    def test_unauthenticated_protected_page_redirects(self) -> None:
        for path in ("/", "/workspace/0", "/index.html"):
            status, _body, _sc, loc = self.server.request("GET", path)
            self.assertEqual(status, 302, f"{path} should 302")
            self.assertEqual(loc, "/login", f"{path} should point to /login")

    def test_static_assets_served_without_auth(self) -> None:
        """静态资源 + 登录页本身:放行(否则登录页渲染不了)。"""
        for path, needle in (("/login", "login-form"), ("/login.css", "login-card"), ("/auth-chrome.js", "auth-chrome")):
            status, body, _sc, _loc = self.server.request("GET", path)
            self.assertEqual(status, 200, f"{path} should be public")
            self.assertIn(needle, body, f"{path} content mismatch")

    def test_unauthenticated_api_status_reports_not_authenticated(self) -> None:
        status, body, _sc, _loc = self.server.request("GET", "/api/auth/status")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["authenticated"])

    # ---- S2: 错 token 拒 ----

    def test_wrong_token_rejected_401(self) -> None:
        for bad in ("bogus", "", "not-a-real-token"):
            status, body, sc, _loc = self.server.request("POST", "/api/auth/login", {"token": bad})
            self.assertEqual(status, 401, f"token {bad!r} must be rejected")
            payload = json.loads(body)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"], "INVALID_TOKEN")
            self.assertIsNone(sc, "no session cookie on failed login")

    def test_login_wrong_token_does_not_consume_a_session(self) -> None:
        before = len(self.server.store)
        self.server.request("POST", "/api/auth/login", {"token": "wrong"})
        self.assertEqual(len(self.server.store), before, "failed login must not create a session")

    # ---- S3: 正确登录后读面正常 ----

    def test_owner_login_then_reads_work(self) -> None:
        status, body, sc, _loc = self.server.request("POST", "/api/auth/login", {"token": self.owner_tok})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["is_owner"])
        self.assertEqual(payload["role"], "owner")
        self.assertEqual(payload["redirect"], "/")
        cookie = f"{SESSION_COOKIE_NAME}={parse_session_cookie(sc)}"
        # Read面 now reachable for owner.
        status, _b, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie)
        self.assertEqual(status, 200)
        status, _b, _sc, _loc = self.server.request("GET", "/", cookie=cookie)
        self.assertEqual(status, 200)

    def test_non_owner_login_reads_same_read_surface(self) -> None:
        status, body, sc, _loc = self.server.request("POST", "/api/auth/login", {"token": self.exec_tok})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["is_owner"])
        self.assertEqual(payload["role"], "executor")
        cookie = f"{SESSION_COOKIE_NAME}={parse_session_cookie(sc)}"
        status, body2, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body2)["ok"])
        # status endpoint reflects role.
        status, body3, _sc, _loc = self.server.request("GET", "/api/auth/status", cookie=cookie)
        self.assertTrue(json.loads(body3)["authenticated"])
        self.assertFalse(json.loads(body3)["is_owner"])

    # ---- S4: cookie 篡改/未知 → 拒 ----

    def test_tampered_cookie_rejected(self) -> None:
        status, _b, _sc, loc = self.server.request("GET", "/api/health", cookie=f"{SESSION_COOKIE_NAME}=tamperedXYZ")
        self.assertEqual(status, 302)
        self.assertEqual(loc, "/login")

    def test_unknown_session_id_rejected(self) -> None:
        # A syntactically valid but unknown session id.
        status, _b, _sc, loc = self.server.request("GET", "/api/health", cookie=f"{SESSION_COOKIE_NAME}=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        self.assertEqual(status, 302)
        self.assertEqual(loc, "/login")

    def test_logout_revokes_session(self) -> None:
        cookie = f"{SESSION_COOKIE_NAME}={self._login(self.owner_tok)}"
        status, _b, sc, loc = self.server.request("POST", "/api/auth/logout", cookie=cookie)
        self.assertEqual(status, 303)
        self.assertEqual(loc, "/login")
        self.assertIn("Max-Age=0", sc or "")
        # Old cookie no longer valid.
        status, _b, _sc, loc = self.server.request("GET", "/api/health", cookie=cookie)
        self.assertEqual(status, 302)
        self.assertEqual(loc, "/login")

    # ---- 红线:token 明文不落 cookie / 不进响应体 ----

    def test_raw_token_never_in_cookie_or_response(self) -> None:
        status, body, sc, _loc = self.server.request("POST", "/api/auth/login", {"token": self.owner_tok})
        self.assertEqual(status, 200)
        self.assertNotIn(self.owner_tok, sc or "", "raw token must NOT appear in Set-Cookie")
        self.assertNotIn(self.owner_tok, body, "raw token must NOT appear in login response body")
        # And not retrievable via status.
        cookie = f"{SESSION_COOKIE_NAME}={parse_session_cookie(sc)}"
        status, body2, _sc, _loc = self.server.request("GET", "/api/auth/status", cookie=cookie)
        self.assertNotIn(self.owner_tok, body2, "raw token must NOT leak via /api/auth/status")

    def test_session_cookie_is_httponly(self) -> None:
        _s, _b, sc, _loc = self.server.request("POST", "/api/auth/login", {"token": self.owner_tok})
        self.assertIsNotNone(sc)
        self.assertIn("HttpOnly", sc)
        self.assertIn("Path=/", sc)
        self.assertIn("SameSite=Lax", sc)


class VerifyLoginTokenTests(unittest.TestCase):
    """Unit tests for the pure token-verification function."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.owner_tok = "owner-unit-token-123"
        self.exec_tok = "exec-unit-token-456"
        self.conn_path = self.root / "connection.json"
        self.conn_path.write_text(
            json.dumps({"tokens": [
                {"role": "owner", "token_ref": "svc-owner", "scopes": ["owner_confirm"], "token": self.owner_tok, "fingerprint": _token_fingerprint(self.owner_tok)},
                {"role": "executor", "token_ref": "svc-exec", "scopes": ["queue_claim"], "token": self.exec_tok, "fingerprint": _token_fingerprint(self.exec_tok)},
            ]}),
            encoding="utf-8",
        )
        self.paths = [self.conn_path]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_matches_owner_fingerprint(self) -> None:
        info = verify_login_token(self.owner_tok, self.paths)
        self.assertIsNotNone(info)
        self.assertEqual(info["role"], "owner")
        self.assertTrue(info["is_owner"])
        self.assertEqual(info["scopes"], ["owner_confirm"])
        self.assertEqual(info["token_ref"], "svc-owner")

    def test_matches_non_owner_role(self) -> None:
        info = verify_login_token(self.exec_tok, self.paths)
        self.assertIsNotNone(info)
        self.assertFalse(info["is_owner"])
        self.assertEqual(info["role"], "executor")

    def test_rejects_unknown_token(self) -> None:
        self.assertIsNone(verify_login_token("no-such-token", self.paths))

    def test_rejects_empty_and_none(self) -> None:
        self.assertIsNone(verify_login_token("", self.paths))
        self.assertIsNone(verify_login_token(None, self.paths))  # type: ignore[arg-type]

    def test_ignores_entries_without_fingerprint(self) -> None:
        """An entry lacking fingerprint is skipped (we never compare raw token)."""
        path = self.root / "no_fp.json"
        path.write_text(json.dumps({"tokens": [
            {"role": "owner", "token": "raw-without-fp-field", "scopes": []},
        ]}), encoding="utf-8")
        # The raw token would match if we compared raw; we must NOT.
        self.assertIsNone(verify_login_token("raw-without-fp-field", [path]))

    def test_malformed_connection_json_is_skipped(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text("{ not valid json", encoding="utf-8")
        # Must not raise; just yields no match.
        self.assertIsNone(verify_login_token(self.owner_tok, [bad]))

    def test_no_paths_yields_none(self) -> None:
        self.assertIsNone(verify_login_token(self.owner_tok, []))


class SessionStoreTests(unittest.TestCase):
    def test_create_get_roundtrip(self) -> None:
        store = SessionStore()
        sid = store.create(role="owner", scopes=["owner_confirm"], token_ref="svc-owner")
        self.assertTrue(sid)
        info = store.get(sid)
        self.assertIsNotNone(info)
        self.assertEqual(info["role"], "owner")
        self.assertTrue(info["is_owner"])

    def test_unknown_id_returns_none(self) -> None:
        store = SessionStore()
        self.assertIsNone(store.get("does-not-exist"))
        self.assertIsNone(store.get(None))
        self.assertIsNone(store.get(""))

    def test_revoke_removes_session(self) -> None:
        store = SessionStore()
        sid = store.create(role="executor", scopes=["queue_claim"])
        self.assertTrue(store.revoke(sid))
        self.assertIsNone(store.get(sid))
        self.assertFalse(store.revoke(sid), "revoke twice = False")
        self.assertFalse(store.revoke("never-existed"))

    def test_store_is_isolated_per_instance(self) -> None:
        a, b = SessionStore(), SessionStore()
        sid = a.create(role="owner", scopes=[])
        self.assertIsNotNone(a.get(sid))
        self.assertIsNone(b.get(sid))


class CookieHelperTests(unittest.TestCase):
    def test_parse_session_cookie_extracts_value(self) -> None:
        self.assertEqual(parse_session_cookie("board_session=abc123; other=x"), "abc123")

    def test_parse_session_cookie_missing_returns_none(self) -> None:
        self.assertIsNone(parse_session_cookie("other=x"))
        self.assertIsNone(parse_session_cookie(None))
        self.assertIsNone(parse_session_cookie(""))

    def test_parse_session_cookie_empty_value_returns_none(self) -> None:
        self.assertIsNone(parse_session_cookie("board_session="))

    def test_build_session_cookie_has_security_flags(self) -> None:
        header = build_session_cookie_header("sid123")
        self.assertIn("board_session=sid123", header)
        self.assertIn("HttpOnly", header)
        self.assertIn("Path=/", header)
        self.assertIn("SameSite=Lax", header)
        self.assertNotIn("Secure", header, "no Secure (board may run on plain http)")

    def test_build_clear_cookie_expires(self) -> None:
        header = build_clear_cookie_header()
        self.assertIn("Max-Age=0", header)
        self.assertIn("1970", header)


class AuthGatePredicateTests(unittest.TestCase):
    def test_public_paths_bypass_auth(self) -> None:
        for p in ("/login", "/api/auth/login", "/api/auth/logout", "/api/auth/status"):
            self.assertTrue(is_public_path(p), f"{p} should be public")

    def test_non_public_paths_require_auth(self) -> None:
        for p in ("/", "/api/health", "/workspace/0", "/overview.html"):
            self.assertFalse(is_public_path(p))

    def test_static_asset_predicate(self) -> None:
        from web.board.app import STATIC_DIR
        # real asset files served from STATIC_DIR root
        for name in ("login.css", "auth-chrome.js"):
            self.assertTrue(is_static_asset_path(f"/{name}"), f"/{name} is a static asset")
        # html pages are NOT assets (they are protected app pages)
        self.assertFalse(is_static_asset_path("/overview.html"))
        self.assertFalse(is_static_asset_path("/login.html"))
        # nonexistent + traversal guarded
        self.assertFalse(is_static_asset_path("/nope.css"))
        self.assertFalse(is_static_asset_path("/../app.py"))
        self.assertFalse(is_static_asset_path("/"))

    def test_is_authorized_matrix(self) -> None:
        store = SessionStore()
        sid = store.create(role="owner", scopes=[])
        good = f"board_session={sid}"
        # public + static → True with no cookie
        self.assertTrue(is_authorized("/login", "GET", None, store))
        self.assertTrue(is_authorized("/login.css", "GET", None, store))
        # protected, no cookie → False
        self.assertFalse(is_authorized("/api/health", "GET", None, store))
        self.assertFalse(is_authorized("/", "GET", None, store))
        # protected, good cookie → True
        self.assertTrue(is_authorized("/api/health", "GET", good, store))
        # protected, tampered cookie → False
        self.assertFalse(is_authorized("/api/health", "GET", "board_session=tampered", store))
        # static-asset predicate only applies to GET
        self.assertFalse(is_authorized("/login.css", "POST", None, store))


if __name__ == "__main__":
    unittest.main()
