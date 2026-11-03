"""WeCom (企业微信) smart bot, long-connection mode — receive-only probe.

Protocol source (official): developer.work.weixin.qq.com/document/path/101463 (智能机器人长连接) and
/document/path/100719 (接收消息); cross-checked with the WeCom team's SDK github.com/WecomTeam/aibot-node-sdk
(src/ws.ts, src/api.ts, src/crypto.ts).

What the probe sends (and nothing else):
  - aibot_subscribe  {cmd, headers.req_id, body.bot_id, body.secret}   once per connection (authentication)
  - ping             {cmd, headers.req_id}                              every 30 s (heartbeat)
  - WebSocket pong / close control frames (RFC 6455 protocol level)
Callbacks (aibot_msg_callback / aibot_event_callback) need no acknowledgement: replying is optional
(aibot_respond_msg / aibot_respond_welcome_msg) and the probe never replies, never sends welcome messages,
never pushes (aibot_send_msg). The official SDK likewise sends no ack for callbacks.

One live connection per bot: a newer subscription makes the server send `disconnected_event` to the old one and
close it. On that event the probe stops instead of reconnecting (reconnecting would kick the other side off).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from relay import aes
from relay.channels import (EXIT_AUTH, EXIT_NETWORK, EXIT_OK, EXIT_REPLACED, Downloader, MediaRef, ProbeContext,
                            b64decode_lenient, should_stop, wait_slice)
from relay.wsclient import OP_TEXT, ConnectionClosed, WebSocketClient, WebSocketError

DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"
CMD_SUBSCRIBE = "aibot_subscribe"
CMD_PING = "ping"
CMD_MSG = "aibot_msg_callback"
CMD_EVENT = "aibot_event_callback"
EVENT_DISCONNECTED = "disconnected_event"
#: the only commands this probe may ever send (fixture tests assert the server saw nothing else)
SENDABLE_CMDS = (CMD_SUBSCRIBE, CMD_PING)
HEARTBEAT_SECONDS = 30.0
MAX_MISSED_PONGS = 2
SUBSCRIBE_TIMEOUT = 15.0
MEDIA_KEYS = ("image", "file", "video", "voice")
MAX_RECONNECTS = 10


def new_req_id(cmd: str) -> str:
    return f"{cmd}_{uuid.uuid4().hex}"


def find_media(body: Any, msgid: str, seq: int) -> list[MediaRef]:
    """Every dict carrying a download `url` under a media key (image/file/video/voice) or next to an `aeskey`,
    anywhere in the message body (covers mixed items, quotes, and undocumented nested shapes)."""
    refs: list[MediaRef] = []

    def walk(node: Any, path: str, parent_key: str) -> None:
        if isinstance(node, dict):
            url = node.get("url")
            if isinstance(url, str) and url and (parent_key in MEDIA_KEYS or "aeskey" in node):
                key_text = node.get("aeskey")
                key = b64decode_lenient(key_text) if isinstance(key_text, str) and key_text else None
                refs.append(MediaRef(msgid=msgid, frame_seq=seq, path=path or parent_key, url=url, key=key,
                                     decrypt=aes.wecom_decrypt if key is not None else None,
                                     declared_name=node.get("filename") or node.get("name"),
                                     declared_size=str(node["size"]) if "size" in node else None))
            for k, v in node.items():
                if k == "url":
                    continue
                walk(v, f"{path}.{k}" if path else k, k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]", parent_key)

    walk(body, "", "")
    return refs


class WecomProbe:
    def __init__(self, ctx: ProbeContext) -> None:
        self.ctx = ctx
        self.url = ctx.options.get("ws_url") or ctx.creds.get("WECOM_WS_URL") or DEFAULT_WS_URL
        self.heartbeat = float(ctx.options.get("heartbeat") or HEARTBEAT_SECONDS)
        self.downloader = Downloader(ctx)
        self.messages = 0
        self.events = 0
        self.session_authed = False

    # ---- helpers --------------------------------------------------------------------------------
    def _send(self, client: WebSocketClient, cmd: str, body: dict | None = None) -> str:
        if cmd not in SENDABLE_CMDS:
            raise AssertionError(f"probe must not send {cmd}")
        req_id = new_req_id(cmd)
        frame: dict[str, Any] = {"cmd": cmd, "headers": {"req_id": req_id}}
        if body is not None:
            frame["body"] = body
        client.send_text(json.dumps(frame, ensure_ascii=False))
        self.ctx.store.append_sent("ws", cmd=cmd, req_id=req_id)  # body never stored (subscribe carries the secret)
        return req_id

    def _record(self, opcode: int, payload: bytes) -> tuple[dict | None, int]:
        if opcode == OP_TEXT:
            try:
                raw = payload.decode("utf-8")
            except UnicodeDecodeError:
                rec = self.ctx.store.append_frame("ws_text_bytes_hex", payload.hex())
                return None, rec["seq"]
            rec = self.ctx.store.append_frame("ws_text", raw)
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                self.ctx.store.log(self.ctx.t("frame_not_json", seq=rec["seq"]))
                return None, rec["seq"]
            return (frame if isinstance(frame, dict) else None), rec["seq"]
        rec = self.ctx.store.append_frame("ws_binary_hex", payload.hex())
        return None, rec["seq"]

    # ---- one connection -------------------------------------------------------------------------
    def _session(self, client: WebSocketClient) -> str:
        ctx = self.ctx
        sub_id = self._send(client, CMD_SUBSCRIBE, {"bot_id": ctx.creds["WECOM_BOT_ID"],
                                                     "secret": ctx.creds["WECOM_BOT_SECRET"]})
        authed = False
        sub_deadline = time.monotonic() + SUBSCRIBE_TIMEOUT
        next_ping = time.monotonic() + self.heartbeat
        missed = 0
        while True:
            now = time.monotonic()
            if should_stop(ctx, now):
                return "stop"
            if not authed and now >= sub_deadline:
                ctx.store.log(ctx.t("wecom_subscribe_timeout"))
                return "reconnect"
            if authed and now >= next_ping:
                if missed >= MAX_MISSED_PONGS:
                    ctx.store.log(ctx.t("wecom_heartbeat_lost", missed=missed))
                    return "reconnect"
                self._send(client, CMD_PING)
                missed += 1
                next_ping = now + self.heartbeat
            try:
                messages = client.recv_messages(wait_slice(ctx, now, 1.0) or 0.05)
            except ConnectionClosed as exc:
                ctx.store.log(ctx.t("wecom_closed", code=exc.code, reason=exc.reason))
                return "reconnect"
            except (OSError, WebSocketError) as exc:
                ctx.store.log(ctx.t("wecom_socket_error", error=f"{type(exc).__name__}: {exc}"))
                return "reconnect"
            for opcode, payload in messages:
                frame, seq = self._record(opcode, payload)
                if frame is None:
                    continue
                cmd = frame.get("cmd")
                req_id = str((frame.get("headers") or {}).get("req_id") or "")
                # responses are matched by our own req_id (documented without `cmd`; tolerated if a cmd is echoed)
                is_response = "errcode" in frame and cmd not in (CMD_MSG, CMD_EVENT)
                if is_response or not cmd:
                    if req_id == sub_id:
                        if frame.get("errcode") == 0:
                            authed = True
                            self.session_authed = True
                            ctx.say(ctx.t("wecom_subscribed"))
                            next_ping = time.monotonic() + self.heartbeat
                        else:
                            ctx.store.log(ctx.t("wecom_auth_failed", errcode=frame.get("errcode"),
                                                errmsg=frame.get("errmsg")))
                            return "auth_failed"
                    elif req_id.startswith(CMD_PING + "_") and frame.get("errcode") == 0:
                        missed = 0
                    continue
                body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
                if cmd == CMD_MSG:
                    self.messages += 1
                    msgid = str(body.get("msgid") or f"seq{seq}")
                    refs = find_media(body, msgid, seq)
                    ctx.say(ctx.t("wecom_message", seq=seq, msgtype=body.get("msgtype"), chattype=body.get("chattype"),
                                  media=len(refs)))
                    for ref in refs:
                        self.downloader.submit(ref)
                elif cmd == CMD_EVENT:
                    self.events += 1
                    eventtype = (body.get("event") or {}).get("eventtype") if isinstance(body.get("event"), dict) else None
                    ctx.say(ctx.t("wecom_event", seq=seq, eventtype=eventtype))
                    if eventtype == EVENT_DISCONNECTED:
                        return "replaced"
                else:
                    ctx.say(ctx.t("wecom_other_frame", seq=seq, cmd=cmd))

    # ---- main loop ------------------------------------------------------------------------------
    def run(self) -> int:
        ctx = self.ctx
        failures = 0
        code = EXIT_OK
        try:
            while not should_stop(ctx, time.monotonic()):
                client = WebSocketClient(self.url)
                try:
                    client.connect()
                except (OSError, WebSocketError) as exc:
                    failures += 1
                    ctx.store.log(ctx.t("wecom_connect_failed", attempt=failures, error=f"{type(exc).__name__}: {exc}"))
                    if failures > MAX_RECONNECTS:
                        code = EXIT_NETWORK
                        break
                    ctx.stop.wait(min(30.0, 2 ** (failures - 1)))
                    continue
                ctx.store.log(ctx.t("wecom_connected"))
                self.session_authed = False
                try:
                    outcome = self._session(client)
                finally:
                    client.close()
                if outcome == "stop":
                    break
                if outcome == "auth_failed":
                    code = EXIT_AUTH
                    break
                if outcome == "replaced":
                    ctx.store.log(ctx.t("wecom_replaced"))
                    code = EXIT_REPLACED
                    break
                failures = 1 if self.session_authed else failures + 1
                if failures > MAX_RECONNECTS:
                    code = EXIT_NETWORK
                    break
                ctx.stop.wait(min(30.0, 2 ** (failures - 1)))
        finally:
            self.downloader.drain()
        ctx.store.log(ctx.t("probe_stopped", messages=self.messages, events=self.events))
        return code


def run(ctx: ProbeContext) -> int:
    return WecomProbe(ctx).run()
