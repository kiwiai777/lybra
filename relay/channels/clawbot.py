"""Weixin ClawBot (个人微信 ClawBot 插件, iLink Bot API) — QR login + receive-only probe.

Protocol source: Tencent's own channel plugin github.com/Tencent/openclaw-weixin (MIT, npm
@tencent-weixin/openclaw-weixin, maintainers @tencent.com), docs/protocol_zh_CN.md ("微信后端 API 协议",
written for developers implementing or connecting to the backend) and src/api/api.ts, src/api/types.ts,
src/auth/login-qr.ts, src/media/media-download.ts. This is the official ClawBot channel; no iPad / PC-hook /
web protocol / UI automation is involved.

What the probe sends (and nothing else):
  login:  POST ilink/bot/get_bot_qrcode?bot_type=3 {"local_token_list": []}; GET ilink/bot/get_qrcode_status
  run:    POST ilink/bot/msg/notifystart (once), POST ilink/bot/getupdates (long poll, cursor only),
          POST ilink/bot/msg/notifystop (once, on exit) — the same lifecycle signals the official client sends;
          they reach the backend, not any user. Disable with --no-lifecycle-notify.
          GET <CDN>/download?encrypted_query_param=... (or the server-given full_url) for attachments.
Never called: sendmessage, sendtyping, getconfig, getuploadurl.
"""
from __future__ import annotations

import base64
import json
import random
import time
import urllib.parse
from typing import Any

from relay import aes, net
from relay.channels import (EXIT_AUTH, EXIT_ERROR, EXIT_OK, EXIT_SESSION_EXPIRED, Downloader, MediaRef, ProbeContext,
                            b64decode_lenient, should_stop)
from relay.config import ConfigError, channel_spec, credential_path, write_private_env

DEFAULT_API_BASE = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"
BOT_TYPE = "3"
ILINK_APP_ID = "bot"
#: version of the official plugin whose protocol this probe follows (iLink-App-ClientVersion = 0x00MMNNPP)
PROTOCOL_VERSION = "2.4.10"
BOT_AGENT = "LybraRelayProbe/0.1"
LONG_POLL_SECONDS = 35.0
SESSION_EXPIRED = -14
ENDPOINT_UPDATES = "ilink/bot/getupdates"
ENDPOINT_START = "ilink/bot/msg/notifystart"
ENDPOINT_STOP = "ilink/bot/msg/notifystop"
ENDPOINT_QRCODE = "ilink/bot/get_bot_qrcode"
ENDPOINT_QRSTATUS = "ilink/bot/get_qrcode_status"
#: every endpoint this module may call (fixture tests assert the server saw nothing else)
ALLOWED_ENDPOINTS = (ENDPOINT_UPDATES, ENDPOINT_START, ENDPOINT_STOP, ENDPOINT_QRCODE, ENDPOINT_QRSTATUS)
ITEM_MEDIA = {2: "image_item", 3: "voice_item", 4: "file_item", 5: "video_item"}
LOGIN_DEADLINE_SECONDS = 480.0
MAX_QR_REFRESH = 3


def client_version(version: str = PROTOCOL_VERSION) -> str:
    major, minor, patch = (int(p) for p in (version.split(".") + ["0", "0"])[:3])
    return str(((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF))


def _uin() -> str:
    return base64.b64encode(str(random.getrandbits(32)).encode("ascii")).decode("ascii")


def app_headers() -> dict[str, str]:
    return {"iLink-App-Id": ILINK_APP_ID, "iLink-App-ClientVersion": client_version()}


def bot_headers(token: str | None) -> dict[str, str]:
    headers = {"AuthorizationType": "ilink_bot_token", "X-WECHAT-UIN": _uin(), **app_headers()}
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def base_info() -> dict[str, str]:
    return {"channel_version": PROTOCOL_VERSION, "bot_agent": BOT_AGENT}


def _join(base: str, endpoint: str) -> str:
    return base.rstrip("/") + "/" + endpoint


def decode_media_key(item_key_hex: str | None, media_key_b64: str | None) -> bytes | None:
    """image_item.aeskey (32 hex chars) wins; else media.aes_key = base64 of 16 raw bytes or of a 32-char hex string."""
    if item_key_hex:
        return bytes.fromhex(item_key_hex)
    if not media_key_b64:
        return None
    raw = b64decode_lenient(media_key_b64)
    if len(raw) == 16:
        return raw
    if len(raw) == 32:
        return bytes.fromhex(raw.decode("ascii"))
    raise ValueError(f"media aes_key decodes to {len(raw)} bytes (want 16, or 32 hex chars)")


def find_media(msg: dict, msgid: str, seq: int, cdn_base: str) -> list[MediaRef]:
    refs: list[MediaRef] = []

    def from_item(item: Any, path: str) -> None:
        if not isinstance(item, dict):
            return
        kind = ITEM_MEDIA.get(item.get("type"))
        sub = item.get(kind) if kind else None
        if isinstance(sub, dict) and isinstance(sub.get("media"), dict):
            media = sub["media"]
            url = media.get("full_url")
            if not url and media.get("encrypt_query_param"):
                url = f"{cdn_base.rstrip('/')}/download?encrypted_query_param=" + urllib.parse.quote(
                    str(media["encrypt_query_param"]), safe="")
            if url:
                key_hex = sub.get("aeskey") if kind == "image_item" else None
                key = decode_media_key(key_hex, media.get("aes_key"))
                refs.append(MediaRef(msgid=msgid, frame_seq=seq, path=f"{path}.{kind}.media", url=url, key=key,
                                     decrypt=aes.ilink_decrypt if key is not None else None,
                                     declared_name=sub.get("file_name"), declared_size=sub.get("len")))
        ref_msg = item.get("ref_msg")
        if isinstance(ref_msg, dict):
            from_item(ref_msg.get("message_item"), f"{path}.ref_msg.message_item")

    for i, item in enumerate(msg.get("item_list") or []):
        from_item(item, f"item_list[{i}]")
    return refs


class ClawbotProbe:
    def __init__(self, ctx: ProbeContext) -> None:
        self.ctx = ctx
        self.api = ctx.options.get("api_base") or ctx.creds.get("CLAWBOT_BASE_URL") or DEFAULT_API_BASE
        self.cdn = ctx.options.get("cdn_base") or ctx.creds.get("CLAWBOT_CDN_BASE_URL") or DEFAULT_CDN_BASE
        self.token = ctx.creds["CLAWBOT_BOT_TOKEN"]
        self.notify = not ctx.options.get("no_lifecycle_notify")
        self.downloader = Downloader(ctx)
        self.messages = 0
        self.empty_polls = 0

    def _post(self, endpoint: str, payload: dict, timeout: float) -> tuple[str, dict]:
        if endpoint not in ALLOWED_ENDPOINTS:
            raise AssertionError(f"probe must not call {endpoint}")
        body = {**payload, "base_info": base_info()}
        self.ctx.store.append_sent("http", endpoint=endpoint)  # headers / body never stored (token)
        result = net.request(_join(self.api, endpoint), method="POST",
                             headers={**bot_headers(self.token), "Content-Type": "application/json"},
                             body=json.dumps(body).encode("utf-8"), timeout=timeout)
        raw = result.body.decode("utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return raw, data if isinstance(data, dict) else {}

    def _lifecycle(self, endpoint: str) -> None:
        if not self.notify:
            return
        try:
            _raw, data = self._post(endpoint, {}, timeout=10.0)
        except (net.HttpError, net.NetPolicyError, ValueError) as exc:
            self.ctx.store.log(self.ctx.t("clawbot_lifecycle_failed", endpoint=endpoint, error=str(exc)))
            return
        if data.get("ret") not in (0, None):
            self.ctx.store.log(self.ctx.t("clawbot_lifecycle_failed", endpoint=endpoint, error=f"ret={data.get('ret')}"))

    def run(self) -> int:
        ctx = self.ctx
        state = ctx.store.read_state()
        cursor = str(state.get("get_updates_buf") or "")
        poll = LONG_POLL_SECONDS
        failures = 0
        code = EXIT_OK
        self._lifecycle(ENDPOINT_START)
        ctx.say(ctx.t("clawbot_polling"))
        try:
            while not should_stop(ctx, time.monotonic()):
                timeout = poll + 5.0
                if ctx.deadline is not None:
                    timeout = max(1.0, min(timeout, ctx.deadline - time.monotonic()))
                try:
                    raw, data = self._post(ENDPOINT_UPDATES, {"get_updates_buf": cursor}, timeout=timeout)
                except net.HttpError as exc:
                    if exc.timed_out:
                        continue  # long poll ran out client-side: same as an empty answer (official client does this)
                    failures += 1
                    ctx.store.log(ctx.t("clawbot_poll_failed", attempt=failures, error=exc.what))
                    if exc.status in (401, 403):
                        code = EXIT_AUTH
                        break
                    ctx.stop.wait(2.0 if failures < 3 else 30.0)
                    continue
                except ValueError as exc:
                    failures += 1
                    ctx.store.log(ctx.t("clawbot_poll_failed", attempt=failures, error=f"bad JSON: {exc}"))
                    ctx.stop.wait(2.0 if failures < 3 else 30.0)
                    continue
                failures = 0
                msgs = data.get("msgs") or []
                ret, errcode = data.get("ret"), data.get("errcode")
                if msgs or ret not in (0, None) or errcode not in (0, None):
                    rec = ctx.store.append_frame("getupdates", raw)
                else:
                    rec = None
                    self.empty_polls += 1
                if SESSION_EXPIRED in (ret, errcode):
                    ctx.store.log(ctx.t("clawbot_session_expired"))
                    code = EXIT_SESSION_EXPIRED
                    break
                if ret not in (0, None) or errcode not in (0, None):
                    ctx.store.log(ctx.t("clawbot_poll_failed", attempt=1, error=f"ret={ret} errcode={errcode}"))
                    ctx.stop.wait(2.0)
                    continue
                new_cursor = data.get("get_updates_buf")
                if new_cursor:
                    cursor = str(new_cursor)
                    ctx.store.write_state({"get_updates_buf": cursor})
                if isinstance(data.get("longpolling_timeout_ms"), (int, float)) and data["longpolling_timeout_ms"] > 0:
                    poll = float(data["longpolling_timeout_ms"]) / 1000.0
                for msg in msgs:
                    if not isinstance(msg, dict):
                        continue
                    self.messages += 1
                    msgid = str(msg.get("message_id") or msg.get("client_id") or f"seq{rec['seq'] if rec else 0}")
                    try:
                        refs = find_media(msg, msgid, rec["seq"] if rec else 0, self.cdn)
                    except ValueError as exc:
                        ctx.store.log(ctx.t("clawbot_bad_media_key", msgid=msgid, error=str(exc)))
                        refs = []
                    types = ",".join(str((it or {}).get("type")) for it in (msg.get("item_list") or []) if isinstance(it, dict))
                    ctx.say(ctx.t("clawbot_message", seq=rec["seq"] if rec else 0, types=types, media=len(refs)))
                    for ref in refs:
                        self.downloader.submit(ref)
        finally:
            self._lifecycle(ENDPOINT_STOP)
            self.downloader.drain()
        ctx.store.log(ctx.t("clawbot_stopped", messages=self.messages, empty=self.empty_polls))
        return code


def run(ctx: ProbeContext) -> int:
    return ClawbotProbe(ctx).run()


# ---- QR login -------------------------------------------------------------------------------------

def login(ctx: ProbeContext) -> int:
    """Owner-run, interactive: show the official QR link, wait for the scan, save the issued bot token to
    <config_dir>/clawbot.env (mode 600). The token is never printed."""
    config_dir = ctx.options["config_dir"]
    target = credential_path(config_dir, channel_spec("clawbot"))
    if target.exists() and not ctx.options.get("force"):
        raise ConfigError("file_exists", path=str(target))
    base = ctx.options.get("api_base") or DEFAULT_API_BASE
    read_line = ctx.options.get("read_line") or input
    deadline = time.monotonic() + float(ctx.options.get("login_deadline") or LOGIN_DEADLINE_SECONDS)
    refreshes = 0
    while refreshes <= MAX_QR_REFRESH:
        qr = net.post_json(_join(base, f"{ENDPOINT_QRCODE}?bot_type={BOT_TYPE}"), {"local_token_list": []},
                           headers=bot_headers(None), timeout=15.0)
        ctx.store.append_sent("http", endpoint=ENDPOINT_QRCODE)
        qrcode, link = qr.get("qrcode"), qr.get("qrcode_img_content")
        if not qrcode or not link:
            ctx.say(ctx.t("clawbot_qr_unavailable"))
            return EXIT_ERROR
        ctx.say(ctx.t("clawbot_qr_link", link=link))
        poll_base = base
        verify_code = None
        while time.monotonic() < deadline:
            url = _join(poll_base, f"{ENDPOINT_QRSTATUS}?qrcode=" + urllib.parse.quote(str(qrcode), safe=""))
            if verify_code:
                url += "&verify_code=" + urllib.parse.quote(verify_code, safe="")
            ctx.store.append_sent("http", endpoint=ENDPOINT_QRSTATUS)
            try:
                result = net.request(url, headers=app_headers(), timeout=40.0)
                status = json.loads(result.body.decode("utf-8") or "{}")
            except net.HttpError as exc:
                if not exc.timed_out:
                    ctx.store.log(ctx.t("clawbot_qr_poll_failed", error=exc.what))
                    ctx.stop.wait(2.0)
                continue
            state = status.get("status")
            if state == "confirmed":
                values = {"CLAWBOT_BOT_TOKEN": str(status.get("bot_token") or "")}
                if not values["CLAWBOT_BOT_TOKEN"]:
                    ctx.say(ctx.t("clawbot_qr_no_token"))
                    return EXIT_AUTH
                if status.get("baseurl"):
                    values["CLAWBOT_BASE_URL"] = str(status["baseurl"])
                if status.get("ilink_bot_id"):
                    values["CLAWBOT_BOT_ID"] = str(status["ilink_bot_id"])
                if status.get("ilink_user_id"):
                    values["CLAWBOT_USER_ID"] = str(status["ilink_user_id"])
                write_private_env(target, values, overwrite=bool(ctx.options.get("force")))
                ctx.say(ctx.t("clawbot_login_saved", path=str(target)))
                return EXIT_OK
            if state == "scaned":
                ctx.say(ctx.t("clawbot_qr_scanned"))
            elif state == "scaned_but_redirect" and status.get("redirect_host"):
                poll_base = f"https://{status['redirect_host']}"
            elif state == "need_verifycode":
                verify_code = read_line(ctx.t("clawbot_qr_verify_prompt")).strip()
            elif state == "verify_code_blocked":
                ctx.say(ctx.t("clawbot_qr_verify_blocked"))
                return EXIT_AUTH
            elif state == "binded_redirect":
                ctx.say(ctx.t("clawbot_qr_already_bound"))
                return EXIT_AUTH
            elif state == "expired":
                ctx.say(ctx.t("clawbot_qr_expired"))
                break
        else:
            ctx.say(ctx.t("clawbot_qr_timeout"))
            return EXIT_AUTH
        refreshes += 1
    ctx.say(ctx.t("clawbot_qr_timeout"))
    return EXIT_AUTH
