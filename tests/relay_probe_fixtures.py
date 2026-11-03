"""AIPOS-F290 test fixtures: local fake servers for the relay probe (127.0.0.1 only; never a real WeCom / Weixin host).

- FakeWecomServer: WebSocket server speaking the WeCom smart-bot long-connection frames
  (developer.work.weixin.qq.com/document/path/101463, /100719): answers aibot_subscribe / ping, pushes scripted
  aibot_msg_callback / aibot_event_callback frames, records every frame the probe sends.
  Its WebSocket framing is written here independently of relay/wsclient.py (so the two check each other).
- MediaServer: HTTP server for attachment downloads (encrypted bytes, optional Content-Disposition).
- FakeIlinkServer: HTTP server for the Weixin ClawBot iLink API (github.com/Tencent/openclaw-weixin
  docs/protocol_zh_CN.md): QR login, getupdates long poll, notifystart/stop, CDN download.
Not a test file (no test_ prefix); imported by tests/test_aipos_f290_relay_*.py.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import socketserver
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---- WebSocket server side (independent framing) --------------------------------------------------

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("eof")
        data += chunk
    return data


def ws_read_frame(sock: socket.socket) -> tuple[int, bool, bytes]:
    b0, b1 = _recv_exact(sock, 2)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    key = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, n)
    if masked:
        payload = bytes(c ^ key[i % 4] for i, c in enumerate(payload))
    return opcode, masked, payload


def ws_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


class FakeWecomServer:
    """scenario: 'normal' | 'auth_fail' | 'replace' (send disconnected_event after the pushes)."""

    def __init__(self, bot_id: str, secret: str, pushes: list[str], scenario: str = "normal") -> None:
        self.bot_id = bot_id
        self.secret = secret
        self.pushes = pushes          # raw JSON texts pushed after a successful subscribe
        self.scenario = scenario
        self.received: list[dict] = []   # every JSON frame the probe sent
        self.unmasked_client_frames = 0
        self.pongs: list[bytes] = []
        self.connections = 0
        self.subscribed = threading.Event()
        self._lock = threading.Lock()
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                outer._serve(self.request)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"ws://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeWecomServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def cmds(self) -> list[str]:
        with self._lock:
            return [str(f.get("cmd")) for f in self.received]

    def _send_json(self, sock: socket.socket, obj: dict) -> None:
        sock.sendall(ws_frame(0x1, json.dumps(obj, ensure_ascii=False).encode("utf-8")))

    def _serve(self, sock: socket.socket) -> None:
        with self._lock:
            self.connections += 1
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                return
            data += chunk
        headers = {}
        for line in data.split(b"\r\n\r\n")[0].decode("iso-8859-1").split("\r\n")[1:]:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        accept = base64.b64encode(hashlib.sha1(headers["sec-websocket-key"].encode() + GUID).digest()).decode()
        sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        try:
            while True:
                opcode, masked, payload = ws_read_frame(sock)
                if not masked:
                    self.unmasked_client_frames += 1
                if opcode == 0x8:
                    sock.sendall(ws_frame(0x8, payload[:2]))
                    return
                if opcode == 0xA:
                    self.pongs.append(payload)
                    continue
                if opcode != 0x1:
                    continue
                frame = json.loads(payload.decode("utf-8"))
                with self._lock:
                    self.received.append(frame)
                req_id = (frame.get("headers") or {}).get("req_id")
                cmd = frame.get("cmd")
                if cmd == "aibot_subscribe":
                    body = frame.get("body") or {}
                    ok = self.scenario != "auth_fail" and body.get("bot_id") == self.bot_id and body.get("secret") == self.secret
                    self._send_json(sock, {"headers": {"req_id": req_id}, "errcode": 0 if ok else 40058,
                                           "errmsg": "ok" if ok else "invalid bot_id or secret"})
                    if not ok:
                        continue
                    self.subscribed.set()
                    sock.sendall(ws_frame(0x9, b"srv-ping"))  # protocol-level ping: probe must pong
                    for i, raw in enumerate(self.pushes):
                        payload_bytes = raw.encode("utf-8")
                        if i == 0 and len(payload_bytes) > 10:   # first push fragmented: continuation handling
                            sock.sendall(ws_frame(0x1, payload_bytes[:10], fin=False))
                            sock.sendall(ws_frame(0x0, payload_bytes[10:], fin=True))
                        else:
                            sock.sendall(ws_frame(0x1, payload_bytes))
                    if self.scenario == "replace":
                        self._send_json(sock, {"cmd": "aibot_event_callback", "headers": {"req_id": "evt-replace"},
                                               "body": {"msgid": "evt-1", "create_time": int(time.time()),
                                                        "aibotid": self.bot_id,
                                                        "event": {"eventtype": "disconnected_event"}}})
                elif cmd == "ping":
                    self._send_json(sock, {"headers": {"req_id": req_id}, "errcode": 0, "errmsg": "ok"})
        except (ConnectionError, OSError):
            return


# ---- HTTP media + iLink --------------------------------------------------------------------------

class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return


class MediaServer:
    """Serves {path: (bytes, content_disposition or None)}; records request paths."""

    def __init__(self, files: dict[str, tuple[bytes, str | None]]) -> None:
        self.files = files
        self.requests: list[str] = []
        outer = self

        class Handler(_QuietHandler):
            def do_GET(self) -> None:
                outer.requests.append(self.path)
                entry = outer.files.get(urllib.parse.urlsplit(self.path).path)
                if entry is None:
                    self.send_error(404)
                    return
                body, disposition = entry
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                if disposition:
                    self.send_header("Content-Disposition", disposition)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "MediaServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


class FakeIlinkServer:
    """iLink Bot API fake. `updates` = list of raw JSON texts returned by successive getupdates calls; after they run
    out, getupdates holds briefly and answers an empty batch. `cdn` = {encrypted_query_param: bytes}."""

    def __init__(self, token: str, updates: list[str], cdn: dict[str, bytes], qr_statuses: list[dict] | None = None) -> None:
        self.token = token
        self.updates = list(updates)
        self.cdn = cdn
        self.qr_statuses = list(qr_statuses or [])
        self.calls: list[tuple[str, str]] = []        # (method, path without query)
        self.auth_headers: list[str | None] = []
        self.update_bodies: list[dict] = []
        self.started = threading.Event()
        outer = self

        class Handler(_QuietHandler):
            def _json(self, obj: dict, status: int = 200) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _raw(self, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parts = urllib.parse.urlsplit(self.path)
                outer.calls.append(("GET", parts.path))
                query = urllib.parse.parse_qs(parts.query)
                if parts.path == "/ilink/bot/get_qrcode_status":
                    status = outer.qr_statuses.pop(0) if outer.qr_statuses else {"status": "wait"}
                    self._json(status)
                elif parts.path == "/c2c/download":
                    data = outer.cdn.get((query.get("encrypted_query_param") or [""])[0])
                    if data is None:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)

            def do_POST(self) -> None:
                parts = urllib.parse.urlsplit(self.path)
                outer.calls.append(("POST", parts.path))
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if parts.path == "/ilink/bot/get_bot_qrcode":
                    self._json({"qrcode": "QR-FIXTURE-1",
                                "qrcode_img_content": "https://liteapp.weixin.qq.com/q/fixture?qrcode=QR-FIXTURE-1"})
                    return
                outer.auth_headers.append(self.headers.get("Authorization"))
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    self._json({"ret": -1, "errmsg": "unauthorized"}, status=401)
                    return
                if parts.path in ("/ilink/bot/msg/notifystart", "/ilink/bot/msg/notifystop"):
                    self._json({"ret": 0, "errmsg": ""})
                elif parts.path == "/ilink/bot/getupdates":
                    outer.update_bodies.append(body)
                    outer.started.set()
                    if outer.updates:
                        self._raw(outer.updates.pop(0))
                    else:
                        time.sleep(0.3)
                        self._json({"ret": 0, "msgs": [], "get_updates_buf": body.get("get_updates_buf", ""),
                                    "longpolling_timeout_ms": 1000})
                else:
                    self._json({"ret": -1, "errmsg": "not in fixture"}, status=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeIlinkServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
