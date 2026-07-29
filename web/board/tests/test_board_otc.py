"""AIPOS-271 — board 无感登录:OTC(本机)+ 设备码(跨机)+ auth-log 留痕。

测试覆盖卡 §验收断言:
  - S1 本机:board open 铸 OTC → ``/login?otc=…`` 换 cookie 进站;OTC 复用/过期拒。
  - S2 跨机:设备码 issue → approve(文件权/token 确认)→ poll 换 cookie 进站;过期/未知拒。
  - S3 留痕:token/otc/device_code 三类登录各追加一条 auth-log;原始 token/OTC/码值不落日志。

红线钉:零依赖(stdlib);原始 token / OTC 值 / 设备码值不落 auth-log、不进 cookie、不进 OTC mint
响应(token 字段);OTC/设备码 TTL 与单次即焚;文件权即身份(server 只校验 CLI 递来的 token 指纹)。
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from web.board.app import (
    REMEMBER_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SessionStore,
    _token_fingerprint,
    make_handler,
    parse_session_cookie,
)
from web.board.auth_otc import (
    DEVICE_CODE_TTL_SECONDS,
    OTC_TTL_SECONDS,
    DeviceCodeStore,
    OTCStore,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """Real-HTTP board server with injectable stores + auth-log path (no auto-redirect)."""

    def __init__(
        self,
        repo_root: Path,
        connection_path: Path,
        *,
        store: SessionStore,
        otc: OTCStore,
        device: DeviceCodeStore,
        auth_log_path: Path,
    ) -> None:
        self.store = store
        self.otc = otc
        self.device = device
        self.connection_path = connection_path
        self.auth_log_path = auth_log_path
        port = _free_port()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(
                repo_root=repo_root,
                session_store=store,
                connection_paths=[connection_path],
                otc_store=otc,
                device_store=device,
                auth_log_path=auth_log_path,
            ),
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.port = port

    def request(
        self, method: str, path: str, body: dict | None = None, cookie: str | None = None
    ) -> tuple[int, str, str | None, str | None]:
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
    """Temp repo + connection.json with one owner + one executor token."""
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name)
    for state in ("pending", "claimed", "completed", "blocked"):
        (repo / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
    owner_tok = "owner-otc-secret-XYZ271"
    exec_tok = "exec-otc-secret-ABC271"
    conn = {
        "tokens": [
            {"role": "owner", "token_ref": "svc-owner", "scopes": ["owner_confirm"], "token": owner_tok, "fingerprint": _token_fingerprint(owner_tok)},
            {"role": "executor", "token_ref": "svc-exec", "scopes": ["queue_claim"], "token": exec_tok, "fingerprint": _token_fingerprint(exec_tok)},
        ]
    }
    conn_path = repo / "connection.json"
    conn_path.write_text(json.dumps(conn), encoding="utf-8")
    return tmp, repo, conn_path, owner_tok, exec_tok


class OtcHttpTests(unittest.TestCase):
    """S1: CLI mint OTC → browser redeem 换 cookie;复用/过期拒。"""

    def setUp(self) -> None:
        self._tmp, self.repo, self.conn_path, self.owner_tok, self.exec_tok = _make_fixture()
        self.auth_log = self.repo / "auth-log.jsonl"
        self.server = _Server(
            self.repo, self.conn_path,
            store=SessionStore(), otc=OTCStore(), device=DeviceCodeStore(),
            auth_log_path=self.auth_log,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self._tmp.cleanup()

    def _mint(self, token: str) -> tuple[int, dict]:
        status, body, _sc, _loc = self.server.request("POST", "/api/auth/otc/mint", {"token": token})
        return status, (json.loads(body) if body else {})

    def test_mint_with_valid_token_returns_otc_and_link(self) -> None:
        status, payload = self._mint(self.owner_tok)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        otc = payload["otc"]
        self.assertTrue(otc, "OTC must be non-empty")
        self.assertEqual(payload["login_url"], f"/login?otc={otc}")
        self.assertEqual(payload["expires_in"], OTC_TTL_SECONDS)
        # 红线:原始 token 不进 mint 响应。
        self.assertNotIn(self.owner_tok, json.dumps(payload))

    def test_mint_with_bad_token_rejected_401(self) -> None:
        status, payload = self._mint("not-a-real-token")
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "INVALID_TOKEN")

    def test_browser_redeems_otc_then_reads_board(self) -> None:
        _s, payload = self._mint(self.owner_tok)
        otc = payload["otc"]
        # 浏览器 GET /login?otc=… → 302 到 / + Set-Cookie。
        status, _body, sc, loc = self.server.request("GET", f"/login?otc={otc}")
        self.assertEqual(status, 302)
        self.assertEqual(loc, "/")
        self.assertIsNotNone(sc, "redeem must set a session cookie")
        cookie = f"{SESSION_COOKIE_NAME}={parse_session_cookie(sc)}"
        # 进站后受保护路由可达。
        status, _b, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie)
        self.assertEqual(status, 200)

    def test_otc_single_use_reuse_rejected(self) -> None:
        _s, payload = self._mint(self.owner_tok)
        otc = payload["otc"]
        # 第一次 redeem 成功。
        s1, _b, sc1, loc1 = self.server.request("GET", f"/login?otc={otc}")
        self.assertEqual((s1, loc1), (302, "/"))
        self.assertIsNotNone(sc1)
        # 复用同一 OTC → 不进站(回登录页 + 无会话 cookie)。
        s2, _b, sc2, loc2 = self.server.request("GET", f"/login?otc={otc}")
        self.assertEqual(s2, 302)
        self.assertEqual(loc2, "/login?otc_err=1")
        self.assertIsNone(sc2, "reused OTC must NOT mint a session cookie")

    def test_otc_expiry_rejected(self) -> None:
        # 注入一个 TTL=0 的 OTC 存储,铸出的票立即过期。
        self.server.shutdown()
        self.server = _Server(
            self.repo, self.conn_path,
            store=SessionStore(), otc=OTCStore(ttl_seconds=0), device=DeviceCodeStore(),
            auth_log_path=self.auth_log,
        )
        _s, payload = self._mint(self.owner_tok)
        otc = payload["otc"]
        time.sleep(0.02)
        status, _b, sc, loc = self.server.request("GET", f"/login?otc={otc}")
        self.assertEqual(loc, "/login?otc_err=1")
        self.assertIsNone(sc, "expired OTC must NOT mint a session cookie")


class DeviceCodeHttpTests(unittest.TestCase):
    """S2: 跨机设备码流 —— issue → approve(文件权/token 确认)→ poll 换 cookie 进站。"""

    def setUp(self) -> None:
        self._tmp, self.repo, self.conn_path, self.owner_tok, self.exec_tok = _make_fixture()
        self.auth_log = self.repo / "auth-log.jsonl"
        self.server = _Server(
            self.repo, self.conn_path,
            store=SessionStore(), otc=OTCStore(), device=DeviceCodeStore(),
            auth_log_path=self.auth_log,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self._tmp.cleanup()

    def _code(self) -> str:
        status, body, _sc, _loc = self.server.request("POST", "/api/auth/device/code", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        code = payload["code"]
        self.assertTrue(re.fullmatch(r"\d{6}", code), f"device code must be 6 digits: {code}")
        return code

    def test_full_device_flow_login(self) -> None:
        code = self._code()
        # poll pending
        s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/poll", {"code": code})
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(body)["status"], "pending")
        # approve on the gate machine (CLI carries a valid token)
        s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        self.assertEqual(s, 200)
        self.assertTrue(json.loads(body)["ok"])
        # poll approved -> cookie + redirect
        s, body, sc, _loc = self.server.request("POST", "/api/auth/device/poll", {"code": code})
        payload = json.loads(body)
        self.assertEqual(payload["status"], "approved")
        self.assertEqual(payload["role"], "owner")
        self.assertIsNotNone(sc, "approved poll must set session cookie")
        cookie = f"{SESSION_COOKIE_NAME}={parse_session_cookie(sc)}"
        s, _b, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie)
        self.assertEqual(s, 200)

    def test_approve_with_bad_token_rejected(self) -> None:
        code = self._code()
        s, body, sc, _loc = self.server.request("POST", "/api/auth/device/approve", {"code": code, "token": "bogus"})
        self.assertEqual(s, 401)
        self.assertFalse(json.loads(body)["ok"])
        self.assertIsNone(sc)
        # subsequent poll still pending (approve did not bind identity)
        s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/poll", {"code": code})
        self.assertEqual(json.loads(body)["status"], "pending")

    def test_approve_unknown_code_rejected(self) -> None:
        s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/approve", {"code": "000000", "token": self.owner_tok})
        self.assertEqual(s, 200)
        self.assertFalse(json.loads(body)["ok"])

    def test_approved_code_single_consume(self) -> None:
        code = self._code()
        self.server.request("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        self.server.request("POST", "/api/auth/device/poll", {"code": code})  # consume
        s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/poll", {"code": code})
        self.assertEqual(json.loads(body)["status"], "unknown")

    def test_expired_device_code_reports_expired(self) -> None:
        self.server.shutdown()
        self.server = _Server(
            self.repo, self.conn_path,
            store=SessionStore(), otc=OTCStore(), device=DeviceCodeStore(ttl_seconds=0),
            auth_log_path=self.auth_log,
        )
        code = self._code()
        time.sleep(0.02)
        s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/poll", {"code": code})
        self.assertEqual(json.loads(body)["status"], "expired")


def _log_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


class AuthLogTests(unittest.TestCase):
    """S3: 三类登录(token/otc/device_code)各留痕;原始 token/OTC/码值不落日志。"""

    def setUp(self) -> None:
        self._tmp, self.repo, self.conn_path, self.owner_tok, self.exec_tok = _make_fixture()
        self.owner_tok = self.owner_tok
        self.auth_log = self.repo / "auth-log.jsonl"
        self.server = _Server(
            self.repo, self.conn_path,
            store=SessionStore(), otc=OTCStore(), device=DeviceCodeStore(),
            auth_log_path=self.auth_log,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self._tmp.cleanup()

    def _login_token(self) -> None:
        self.server.request("POST", "/api/auth/login", {"token": self.owner_tok})

    def _login_otc(self) -> None:
        _s, body, _sc, _loc = self.server.request("POST", "/api/auth/otc/mint", {"token": self.owner_tok})
        otc = json.loads(body)["otc"]
        self.server.request("GET", f"/login?otc={otc}")

    def _login_device(self) -> None:
        _s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/code", {})
        code = json.loads(body)["code"]
        self.server.request("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        self.server.request("POST", "/api/auth/device/poll", {"code": code})

    def test_three_methods_each_logged(self) -> None:
        self._login_token()
        self._login_otc()
        self._login_device()
        recs = _log_records(self.auth_log)
        methods = [r["method"] for r in recs]
        self.assertIn("token", methods)
        self.assertIn("otc", methods)
        self.assertIn("device_code", methods)
        # 每条只有这 5 个键:ts/method/role/token_ref/source_ip(无凭据值)。
        for r in recs:
            self.assertEqual(set(r), {"ts", "method", "role", "token_ref", "source_ip"}, r)
            self.assertEqual(r["role"], "owner")
            self.assertEqual(r["token_ref"], "svc-owner")

    def test_no_secrets_in_auth_log(self) -> None:
        """红线:原始 token / OTC 值 / 设备码值一律不出现在 auth-log。"""
        # 拿到本次会话实际用到的 OTC 与设备码值,确保它们不落日志。
        _s, body, _sc, _loc = self.server.request("POST", "/api/auth/otc/mint", {"token": self.owner_tok})
        otc_val = json.loads(body)["otc"]
        _s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/code", {})
        code_val = json.loads(body)["code"]
        self.server.request("GET", f"/login?otc={otc_val}")
        self.server.request("POST", "/api/auth/device/approve", {"code": code_val, "token": self.owner_tok})
        self.server.request("POST", "/api/auth/device/poll", {"code": code_val})
        self._login_token()
        blob = self.auth_log.read_text(encoding="utf-8") if self.auth_log.exists() else ""
        self.assertNotIn(self.owner_tok, blob, "raw token must NOT appear in auth-log")
        self.assertNotIn(otc_val, blob, "OTC value must NOT appear in auth-log")
        self.assertNotIn(code_val, blob, "device code value must NOT appear in auth-log")

    def test_failed_logins_leave_no_trail(self) -> None:
        """失败登录(错 token)不留痕 —— 避免凭据探测留指纹。"""
        self.server.request("POST", "/api/auth/login", {"token": "wrong"})
        self.server.request("POST", "/api/auth/otc/mint", {"token": "wrong"})
        self.assertEqual(_log_records(self.auth_log), [])

    def test_log_skipped_gracefully_when_path_none(self) -> None:
        """auth-log 路径不可写时不应阻断登录(repo_root=None → 不落盘)。"""
        self.server.shutdown()
        self.server = _Server(
            self.repo, self.conn_path,
            store=SessionStore(), otc=OTCStore(), device=DeviceCodeStore(),
            auth_log_path=Path("/nonexistent-root/no/permission/.lybra/auth-log.jsonl"),
        )
        s, _b, sc, _loc = self.server.request("POST", "/api/auth/login", {"token": self.owner_tok})
        self.assertEqual(s, 200)
        self.assertIsNotNone(sc, "login must still succeed when auth-log is unwritable")


class LoginPageContractTests(unittest.TestCase):
    """前端契约:login.html 含设备码 UI + otc_err 提示;login.css 含设备码样式。"""

    def setUp(self) -> None:
        self._tmp, self.repo, self.conn_path, self.owner_tok, self.exec_tok = _make_fixture()
        self.auth_log = self.repo / "auth-log.jsonl"
        self.server = _Server(
            self.repo, self.conn_path,
            store=SessionStore(), otc=OTCStore(), device=DeviceCodeStore(),
            auth_log_path=self.auth_log,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self._tmp.cleanup()

    def test_login_page_has_device_code_ui(self) -> None:
        status, body, _sc, _loc = self.server.request("GET", "/login")
        self.assertEqual(status, 200)
        for needle in ("设备码登录", "device-code", "device/poll", "lybra board approve", "otc_err"):
            self.assertIn(needle, body, f"login.html missing {needle!r}")

    def test_login_css_has_device_panel(self) -> None:
        status, body, _sc, _loc = self.server.request("GET", "/login.css")
        self.assertEqual(status, 200)
        self.assertIn("device-panel", body)
        self.assertIn("device-code", body)


class RememberDeviceRestartTests(unittest.TestCase):
    """F-271-6: remember token 重启场景测试 —— 新 SessionStore 实例(模拟重启)后，
    持 remember token 请求数据路由应自动重建会话并 200。钉死 remember 真调用链。"""

    def setUp(self) -> None:
        self._tmp, self.repo, self.conn_path, self.owner_tok, self.exec_tok = _make_fixture()
        self.auth_log = self.repo / "auth-log.jsonl"
        # 首次启动：勾选 remember 登录
        self.store1 = SessionStore()
        self.server = _Server(
            self.repo, self.conn_path,
            store=self.store1, otc=OTCStore(), device=DeviceCodeStore(),
            auth_log_path=self.auth_log,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self._tmp.cleanup()

    def test_remember_token_survives_restart(self) -> None:
        """F-271-6 S3: 勾选 remember 登录 → serve 重启(新 SessionStore) → 持 remember cookie 仍可访问数据路由。"""
        # 1. 设备码登录 with remember=True
        _s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/code", {})
        code = json.loads(body)["code"]
        self.server.request("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        s, body, set_cookie, _loc = self.server.request("POST", "/api/auth/device/poll", {"code": code, "remember": True})
        self.assertEqual(s, 200, body)
        self.assertIsNotNone(set_cookie, "should get Set-Cookie with remember=True")
        
        # 解析两枚 cookie：board_session + board_remember
        from web.board.app import REMEMBER_COOKIE_NAME
        from http.cookies import SimpleCookie
        cookies = SimpleCookie()
        cookies.load(set_cookie)
        session_cookie = cookies.get(SESSION_COOKIE_NAME)
        remember_cookie = cookies.get(REMEMBER_COOKIE_NAME)
        self.assertIsNotNone(session_cookie, "should have session cookie")
        self.assertIsNotNone(remember_cookie, "should have remember cookie when remember=True")
        
        session_id = session_cookie.value
        remember_token = remember_cookie.value
        
        # 2. 验证登录后可访问数据路由（首次，会话存在）
        cookie_header = f"{SESSION_COOKIE_NAME}={session_id}; {REMEMBER_COOKIE_NAME}={remember_token}"
        s, body, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie_header)
        self.assertEqual(s, 200, "should access protected route with valid session")
        
        # 3. 模拟 serve 重启：清空 SessionStore（内存态丢失）
        self.store1.clear()
        self.assertEqual(len(self.store1), 0, "session store should be empty after restart simulation")
        
        # 4. 持 remember token 再次访问数据路由 → 应自动重建会话并 200
        s, body, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie_header)
        self.assertEqual(s, 200, f"should auto-restore session from remember token; got {s}: {body}")
        
        # 5. 验证会话已重建（SessionStore 不再为空）
        self.assertGreater(len(self.store1), 0, "session should be restored after remember token validation")
        
        # 6. 再次访问应直接命中会话（不再需要 remember token 重建）
        s, body, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie_header)
        self.assertEqual(s, 200, "restored session should persist")

    def test_remember_token_functions_are_called(self) -> None:
        """F-271-6: 防死代码复发 —— sign_remember_token / verify_remember_token 必须被真实调用。
        间接验证：remember=True 登录后，cookie 含签名；重启后能自动续登即证明 verify 被调用。"""
        import urllib.parse
        _s, body, _sc, _loc = self.server.request("POST", "/api/auth/device/code", {})
        code = json.loads(body)["code"]
        self.server.request("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        s, body, set_cookie, _loc = self.server.request("POST", "/api/auth/device/poll", {"code": code, "remember": True})
        self.assertEqual(s, 200)
        
        from web.board.app import REMEMBER_COOKIE_NAME
        from http.cookies import SimpleCookie
        cookies = SimpleCookie()
        cookies.load(set_cookie)
        remember_cookie = cookies.get(REMEMBER_COOKIE_NAME)
        self.assertIsNotNone(remember_cookie)
        remember_token_encoded = remember_cookie.value
        
        # URL 解码 remember token
        remember_token = urllib.parse.unquote(remember_token_encoded)
        
        # remember token 应该是多段结构：session_id:role:scopes:token_ref:signature
        parts = remember_token.split(":")
        self.assertGreaterEqual(len(parts), 5, f"remember token should have at least 5 parts (got {len(parts)}): {remember_token}")
        
        # 模拟重启并验证自动续登（证明 verify_remember_token 被调用）
        self.store1.clear()
        session_id = cookies.get(SESSION_COOKIE_NAME).value
        cookie_header = f"{SESSION_COOKIE_NAME}={session_id}; {REMEMBER_COOKIE_NAME}={remember_token_encoded}"
        s, _body, _sc, _loc = self.server.request("GET", "/api/health", cookie=cookie_header)
        self.assertEqual(s, 200, "auto-restore proves verify_remember_token is called")


class Fix4HttpHeaderTests(unittest.TestCase):
    """FIX-4: 验证三条登录路径在 HTTP 层正确发送独立的 Set-Cookie header。
    
    铁证: device/poll remember=true 响应仅 1 枚 Set-Cookie header(两枚 cookie 被错误拼接)。
    根因: build_session_cookie_header 返回拼接字符串,_send_json_cookie 只调用一次 send_header。
    修法: 返回列表,_send_json_cookie/_redirect 循环发送多个独立 header(符合 HTTP 规范)。
    """

    def setUp(self) -> None:
        self.tmpdir, self.repo, self.conn, self.owner_tok, _ = _make_fixture()
        self.store = SessionStore()
        self.otc = OTCStore()
        self.device = DeviceCodeStore()
        
        handler = make_handler(
            repo_root=self.repo,
            session_store=self.store,
            connection_paths=[self.conn],
            otc_store=self.otc,
            device_store=self.device,
            auth_log_path=None,
        )
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmpdir.cleanup()

    def _http_req(self, method: str, path: str, body: dict[str, Any] | None = None, cookie: str | None = None) -> tuple[http.client.HTTPResponse, bytes]:
        """发送原始 HTTP 请求(绕过 _Server 包装,直接访问 HTTPResponse.msg.get_all)。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, 
                    body=json.dumps(body).encode() if body else None, 
                    headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_device_poll_remember_true_sends_two_independent_headers(self) -> None:
        """路径1(设备码): remember=true → HTTP 响应包含 2 个独立的 Set-Cookie header。"""
        # 设备码流程
        _, body = self._http_req("POST", "/api/auth/device/code", {})
        code = json.loads(body)["code"]
        self._http_req("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        
        resp, _ = self._http_req("POST", "/api/auth/device/poll", {"code": code, "remember": True})
        
        # 核心断言: HTTPResponse.msg.get_all("Set-Cookie") 返回 2 个元素(不是 1 个拼接字符串)
        set_cookies = resp.msg.get_all("Set-Cookie")
        self.assertIsNotNone(set_cookies, "should have Set-Cookie headers")
        self.assertEqual(len(set_cookies), 2, 
                        f"FIX-4: should send 2 independent Set-Cookie headers, got {len(set_cookies)}")
        
        cookie_names = [c.split("=")[0] for c in set_cookies]
        self.assertIn(SESSION_COOKIE_NAME, cookie_names, "should have session cookie")
        self.assertIn(REMEMBER_COOKIE_NAME, cookie_names, "should have remember cookie")

    def test_device_poll_remember_false_sends_one_header(self) -> None:
        """路径1(设备码): remember=false → HTTP 响应包含 1 个 Set-Cookie header。"""
        _, body = self._http_req("POST", "/api/auth/device/code", {})
        code = json.loads(body)["code"]
        self._http_req("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        
        resp, _ = self._http_req("POST", "/api/auth/device/poll", {"code": code, "remember": False})
        
        set_cookies = resp.msg.get_all("Set-Cookie")
        self.assertEqual(len(set_cookies), 1, "remember=false should send 1 Set-Cookie header")
        self.assertTrue(set_cookies[0].startswith(SESSION_COOKIE_NAME))

    def test_token_login_sends_one_header(self) -> None:
        """路径2(token 直接登录): 不支持 remember → HTTP 响应包含 1 个 Set-Cookie header。"""
        resp, _ = self._http_req("POST", "/api/auth/login", {"token": self.owner_tok})
        
        self.assertEqual(resp.status, 200)
        set_cookies = resp.msg.get_all("Set-Cookie")
        self.assertEqual(len(set_cookies), 1, "token login should send 1 Set-Cookie header")
        self.assertTrue(set_cookies[0].startswith(SESSION_COOKIE_NAME))

    def test_otc_redeem_sends_one_header(self) -> None:
        """路径3(OTC): 不支持 remember → HTTP 响应包含 1 个 Set-Cookie header。"""
        resp, body = self._http_req("POST", "/api/auth/otc/mint", {"token": self.owner_tok})
        self.assertEqual(resp.status, 200)
        otc = json.loads(body)["otc"]
        
        resp, _ = self._http_req("GET", f"/login?otc={otc}")
        
        self.assertEqual(resp.status, 302, "OTC redeem should redirect")
        set_cookies = resp.msg.get_all("Set-Cookie")
        self.assertEqual(len(set_cookies), 1, "OTC redeem should send 1 Set-Cookie header")
        self.assertTrue(set_cookies[0].startswith(SESSION_COOKIE_NAME))

    def test_logout_sends_two_clear_headers(self) -> None:
        """登出: HTTP 响应包含 2 个独立的 Set-Cookie header(清除 session + remember)。"""
        # 先登录
        _, body = self._http_req("POST", "/api/auth/device/code", {})
        code = json.loads(body)["code"]
        self._http_req("POST", "/api/auth/device/approve", {"code": code, "token": self.owner_tok})
        resp, _ = self._http_req("POST", "/api/auth/device/poll", {"code": code, "remember": True})
        
        set_cookies = resp.msg.get_all("Set-Cookie")
        cookies_dict = {}
        for sc in set_cookies:
            name = sc.split("=")[0]
            value = sc.split(";")[0].split("=", 1)[1]
            cookies_dict[name] = value
        cookie_header = f"{SESSION_COOKIE_NAME}={cookies_dict[SESSION_COOKIE_NAME]}; {REMEMBER_COOKIE_NAME}={cookies_dict[REMEMBER_COOKIE_NAME]}"
        
        # 登出
        resp, _ = self._http_req("POST", "/api/auth/logout", cookie=cookie_header)
        
        self.assertEqual(resp.status, 303, "logout should redirect")
        clear_cookies = resp.msg.get_all("Set-Cookie")
        self.assertEqual(len(clear_cookies), 2, "logout should send 2 clear Set-Cookie headers")
        
        # 验证两枚都是清除指令(Max-Age=0)
        for cc in clear_cookies:
            self.assertIn("Max-Age=0", cc, f"clear cookie should have Max-Age=0: {cc}")
