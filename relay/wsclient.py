"""Minimal RFC 6455 WebSocket client (stdlib only: socket + ssl).

Enough for a JSON text-frame long connection: client handshake, masked client frames, fragmented server messages,
ping -> pong (protocol-level), close handshake. Not a general-purpose library (no extensions, no compression).
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct

from relay.net import check_url

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE = 16 * 1024 * 1024


class WebSocketError(Exception):
    pass


class ConnectionClosed(WebSocketError):
    def __init__(self, code: int | None, reason: str) -> None:
        super().__init__(f"closed code={code} reason={reason!r}")
        self.code = code
        self.reason = reason


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + _GUID).digest()).decode("ascii")


def encode_frame(opcode: int, payload: bytes, *, mask: bool = True, fin: bool = True) -> bytes:
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head.append(mbit | n)
    elif n < 65536:
        head.append(mbit | 126)
        head += struct.pack(">H", n)
    else:
        head.append(mbit | 127)
        head += struct.pack(">Q", n)
    if not mask:
        return bytes(head) + payload
    key = os.urandom(4)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + key + masked


class WebSocketClient:
    def __init__(self, url: str, *, timeout: float = 15.0) -> None:
        self.url = url
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self._frag_op: int | None = None
        self._frag = bytearray()
        self.closed = False

    # ---- connection -----------------------------------------------------------------------------
    def connect(self) -> None:
        parts = check_url(self.url, "wss", "ws")
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        raw = socket.create_connection((host, port), timeout=self.timeout)
        if parts.scheme == "wss":
            ctx = ssl.create_default_context()
            sock: socket.socket = ctx.wrap_socket(raw, server_hostname=host)
        else:
            sock = raw
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        host_header = host if parts.port is None else f"{host}:{parts.port}"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise WebSocketError("connection closed during handshake")
            data += chunk
            if len(data) > 65536:
                sock.close()
                raise WebSocketError("handshake response too large")
        head, _, rest = bytes(data).partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            sock.close()
            raise WebSocketError(f"handshake refused: {lines[0]}")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != accept_key(key):
            sock.close()
            raise WebSocketError("handshake: bad Sec-WebSocket-Accept")
        self._sock = sock
        self._buf = bytearray(rest)
        self.closed = False

    def close(self, code: int = 1000) -> None:
        if self._sock is None:
            return
        if not self.closed:
            try:
                self._sock.sendall(encode_frame(OP_CLOSE, struct.pack(">H", code)))
            except OSError:
                self.closed = True  # peer already gone; nothing left to tell it
        self.closed = True
        try:
            self._sock.close()
        finally:
            self._sock = None

    # ---- send -----------------------------------------------------------------------------------
    def send_text(self, text: str) -> None:
        self._send(encode_frame(OP_TEXT, text.encode("utf-8")))

    def _send(self, frame: bytes) -> None:
        if self._sock is None:
            raise WebSocketError("not connected")
        self._sock.sendall(frame)

    # ---- receive --------------------------------------------------------------------------------
    def _parse_one(self) -> tuple[bool, int, bytes] | None:
        buf = self._buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        fin, opcode = bool(b0 & 0x80), b0 & 0x0F
        masked, n = bool(b1 & 0x80), b1 & 0x7F
        pos = 2
        if n == 126:
            if len(buf) < 4:
                return None
            n = struct.unpack(">H", buf[2:4])[0]
            pos = 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n = struct.unpack(">Q", buf[2:10])[0]
            pos = 10
        if n > MAX_MESSAGE:
            raise WebSocketError(f"frame too large: {n}")
        key = b""
        if masked:
            if len(buf) < pos + 4:
                return None
            key = bytes(buf[pos:pos + 4])
            pos += 4
        if len(buf) < pos + n:
            return None
        payload = bytes(buf[pos:pos + n])
        del buf[:pos + n]
        if masked:
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv_messages(self, wait: float) -> list[tuple[int, bytes]]:
        """Read for up to `wait` seconds; return complete data messages [(opcode, payload)].
        Answers server pings with pongs. Raises ConnectionClosed when the server closes."""
        if self._sock is None:
            raise WebSocketError("not connected")
        out: list[tuple[int, bytes]] = []
        self._sock.settimeout(wait)
        try:
            chunk = self._sock.recv(65536)
        except (socket.timeout, TimeoutError):
            chunk = None
        except ssl.SSLWantReadError:
            chunk = None
        if chunk is not None:
            if not chunk:
                self.closed = True
                raise ConnectionClosed(None, "eof")
            self._buf += chunk
        while True:
            frame = self._parse_one()
            if frame is None:
                break
            fin, opcode, payload = frame
            if opcode == OP_PING:
                self._send(encode_frame(OP_PONG, payload))
            elif opcode == OP_PONG:
                continue
            elif opcode == OP_CLOSE:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                reason = payload[2:].decode("utf-8", "replace")
                try:
                    self._send(encode_frame(OP_CLOSE, payload[:2]))
                except OSError:
                    reason = reason or "peer gone"
                self.closed = True
                raise ConnectionClosed(code, reason)
            elif opcode in (OP_TEXT, OP_BINARY):
                if fin:
                    out.append((opcode, payload))
                else:
                    self._frag_op, self._frag = opcode, bytearray(payload)
            elif opcode == OP_CONT:
                if self._frag_op is None:
                    raise WebSocketError("continuation frame without start")
                self._frag += payload
                if len(self._frag) > MAX_MESSAGE:
                    raise WebSocketError("fragmented message too large")
                if fin:
                    out.append((self._frag_op, bytes(self._frag)))
                    self._frag_op, self._frag = None, bytearray()
            else:
                raise WebSocketError(f"unknown opcode {opcode}")
        return out
