"""Channel adapters. Each adapter module exposes `run(ctx) -> int` (receive-only probe loop) and may expose
`login(ctx) -> int` (channels whose credentials are issued by a QR scan). Register new channels in
relay.config.CHANNELS and ADAPTERS below; nothing else changes."""
from __future__ import annotations

import base64
import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from relay import net
from relay.aes import AESError
from relay.store import ProbeStore, iso, utc_now

ADAPTERS = {"wecom": "relay.channels.wecom", "clawbot": "relay.channels.clawbot"}

# exit codes shared by all adapters (documented in docs/relay-probe-setup*.md)
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_LOCKED = 3
EXIT_AUTH = 4
EXIT_REPLACED = 5
EXIT_SESSION_EXPIRED = 6
EXIT_NETWORK = 7


def adapter(channel: str):
    return importlib.import_module(ADAPTERS[channel])


@dataclass
class ProbeContext:
    channel: str
    store: ProbeStore
    creds: dict[str, str]
    t: Callable[..., str]
    say: Callable[[str], None]
    stop: threading.Event
    deadline: float | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaRef:
    """One downloadable attachment referenced by a received frame."""
    msgid: str
    frame_seq: int
    path: str            # JSON path inside the message, e.g. "mixed.msg_item[1].image"
    url: str
    key: bytes | None    # decoded AES key (None = not encrypted / no key given)
    decrypt: Callable[[bytes, bytes], bytes] | None
    declared_name: str | None = None
    declared_size: str | None = None


def b64decode_lenient(text: str) -> bytes:
    """Base64 with missing '=' padding tolerated (the official WeCom SDK notes Node's Buffer is lenient)."""
    text = text.strip()
    return base64.b64decode(text + "=" * (-len(text) % 4))


class Downloader:
    """Downloads attachments right away (WeCom URLs expire after 5 minutes) on worker threads, so the
    receive loop keeps answering heartbeats."""

    def __init__(self, ctx: ProbeContext, workers: int = 2) -> None:
        self.ctx = ctx
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"relay-{ctx.channel}-dl")
        self._counter = 0
        self._lock = threading.Lock()
        self.crashed = 0

    def submit(self, ref: MediaRef) -> None:
        with self._lock:
            self._counter += 1
            n = self._counter
        self.pool.submit(self._run, ref, n).add_done_callback(self._report)

    def _report(self, future) -> None:
        exc = future.exception()
        if exc is not None:
            self.crashed += 1
            self.ctx.store.log(self.ctx.t("attachment_crashed", error=f"{type(exc).__name__}: {exc}"))

    def drain(self) -> None:
        self.pool.shutdown(wait=True)

    def _run(self, ref: MediaRef, n: int) -> None:
        ctx = self.ctx
        stem = f"{net.safe_file_part(ref.msgid, 48)}_{n}"
        record: dict[str, Any] = {
            "channel": ctx.channel, "msgid": ref.msgid, "frame_seq": ref.frame_seq, "path": ref.path,
            "url": net.describe_url(ref.url), "declared_name": ref.declared_name, "declared_size": ref.declared_size,
            "downloaded_at": None, "http_status": None, "content_type": None, "content_disposition_filename": None,
            "encrypted_file": None, "encrypted_size": None, "encrypted_sha256": None,
            "decrypted_file": None, "decrypted_size": None, "decrypted_sha256": None,
            "sniffed_type": None, "decrypt": None, "error": None,
        }
        try:
            result = net.request(ref.url, timeout=120.0)
        except (net.HttpError, net.NetPolicyError) as exc:
            record["error"] = f"download failed: {exc}"
            ctx.store.append_attachment(record)
            ctx.store.log(ctx.t("attachment_failed", path=ref.path, msgid=ref.msgid, error=record["error"]))
            return
        body = result.body
        record["downloaded_at"] = iso(utc_now())
        record["http_status"] = result.status
        record["content_type"] = result.headers.get("Content-Type")
        record["content_disposition_filename"] = net.content_disposition_filename(result.headers.get("Content-Disposition"))
        raw_name = record["content_disposition_filename"] or ref.declared_name
        if ref.key is None or ref.decrypt is None:
            ext = net.sniff_type(body)
            path, digest = ctx.store.save_attachment_file(self._plain_name(stem, raw_name, ext), body)
            record.update(decrypted_file=path.name, decrypted_size=len(body), decrypted_sha256=digest,
                          sniffed_type=ext, decrypt="not_encrypted")
        else:
            path, digest = ctx.store.save_attachment_file(f"{stem}.enc", body)
            record.update(encrypted_file=path.name, encrypted_size=len(body), encrypted_sha256=digest)
            try:
                plain = ref.decrypt(ref.key, body)
            except AESError as exc:
                record["decrypt"] = "failed"
                record["error"] = f"decrypt failed: {exc}"
            else:
                ext = net.sniff_type(plain)
                ppath, pdigest = ctx.store.save_attachment_file(self._plain_name(stem, raw_name, ext), plain)
                record.update(decrypted_file=ppath.name, decrypted_size=len(plain), decrypted_sha256=pdigest,
                              sniffed_type=ext, decrypt="ok")
        ctx.store.append_attachment(record)
        ctx.store.log(ctx.t("attachment_saved", path=ref.path, msgid=ref.msgid, size=len(body),
                            status=record["decrypt"], name=record["decrypted_file"] or record["encrypted_file"]))

    @staticmethod
    def _plain_name(stem: str, raw_name: str | None, ext: str | None) -> str:
        if raw_name:
            return f"{stem}__{net.safe_file_part(raw_name)}"
        return f"{stem}.{ext or 'bin'}"


def wait_slice(ctx: ProbeContext, now: float, cap: float) -> float:
    if ctx.deadline is not None:
        return max(0.0, min(cap, ctx.deadline - now))
    return cap


def should_stop(ctx: ProbeContext, now: float) -> bool:
    return ctx.stop.is_set() or (ctx.deadline is not None and now >= ctx.deadline)
