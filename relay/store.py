"""Probe output store: raw frames, outbound protocol frames (no bodies), attachments, status log.

Layout (all under <probe_root>/<channel>/, dirs 700, files 600, never inside a git repo by default):
  probe.lock                      single-instance lock (one live connection per channel)
  state.json                      channel cursor (e.g. ClawBot get_updates_buf), private
  <YYYY-MM-DD>/frames.jsonl       one line per received frame: {seq, received_at, kind, raw}; `raw` is the frame text exactly
  <YYYY-MM-DD>/sent.jsonl         protocol frames the probe sent: {sent_at, kind, cmd/endpoint, req_id}; bodies are never stored
  <YYYY-MM-DD>/attachments.jsonl  one line per attachment download (sha256 of encrypted and decrypted bytes)
  <YYYY-MM-DD>/attachments/       downloaded files
  <YYYY-MM-DD>/probe.log          status lines (no credential values)
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DIR_MODE = 0o700
FILE_MODE = 0o600


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    os.chmod(path, DIR_MODE)
    return path


def _append_line(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def write_private_bytes(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)


class LockBusy(Exception):
    def __init__(self, path: Path, holder: str) -> None:
        super().__init__(str(path))
        self.path = path
        self.holder = holder


class ChannelLock:
    """flock-based single-instance lock; released by the kernel when the process exits (no stale lock files).
    Not workspace_config.exclusive_flock: relay/ must not import gate internals (AIPOS-F290 boundary); this is the
    relay's only flock call site, registered in tests/test_aipos_f135_finalize_repo_serial.py's flock invariant."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                os.close(fd)
                raise
            holder = os.read(fd, 64).decode("utf-8", "replace").strip() or "?"
            os.close(fd)
            raise LockBusy(self.path, holder) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the only descriptor drops the flock (no separate unlock call)
            self._fd = None


class ProbeStore:
    def __init__(self, root: Path, channel: str, clock: Callable[[], datetime] = utc_now,
                 echo: Callable[[str], None] | None = None) -> None:
        self.root = root
        self.channel = channel
        self.clock = clock
        self.echo = echo
        self._lock = threading.Lock()
        self._seq = 0
        ensure_private_dir(root)
        self.channel_dir = ensure_private_dir(root / channel)

    # ---- locations ------------------------------------------------------------------------------
    def lock(self) -> ChannelLock:
        return ChannelLock(self.channel_dir / "probe.lock")

    def day_dir(self, ts: datetime | None = None) -> Path:
        ts = ts or self.clock()
        # directory per local calendar day (what the Owner remembers); timestamps inside records stay UTC
        return ensure_private_dir(self.channel_dir / ts.astimezone().strftime("%Y-%m-%d"))

    def attachments_dir(self, ts: datetime | None = None) -> Path:
        return ensure_private_dir(self.day_dir(ts) / "attachments")

    # ---- writers --------------------------------------------------------------------------------
    def append_frame(self, kind: str, raw: str) -> dict[str, Any]:
        with self._lock:
            ts = self.clock()
            self._seq += 1
            record = {"seq": self._seq, "received_at": iso(ts), "kind": kind, "raw": raw}
            _append_line(self.day_dir(ts) / "frames.jsonl", json.dumps(record, ensure_ascii=False))
            return record

    def append_sent(self, kind: str, **fields: Any) -> None:
        with self._lock:
            ts = self.clock()
            record = {"sent_at": iso(ts), "kind": kind, **fields}
            _append_line(self.day_dir(ts) / "sent.jsonl", json.dumps(record, ensure_ascii=False))

    def append_attachment(self, record: dict[str, Any]) -> None:
        with self._lock:
            _append_line(self.day_dir() / "attachments.jsonl", json.dumps(record, ensure_ascii=False))

    def save_attachment_file(self, name: str, data: bytes) -> tuple[Path, str]:
        path = self.attachments_dir() / name
        write_private_bytes(path, data)
        return path, hashlib.sha256(data).hexdigest()

    def log(self, line: str) -> None:
        with self._lock:
            ts = self.clock()
            _append_line(self.day_dir(ts) / "probe.log", f"{iso(ts)} {line}")
        if self.echo is not None:
            self.echo(line)

    # ---- state ----------------------------------------------------------------------------------
    def read_state(self) -> dict[str, Any]:
        path = self.channel_dir / "state.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def write_state(self, state: dict[str, Any]) -> None:
        path = self.channel_dir / "state.json"
        tmp = path.with_name("state.json.tmp")
        write_private_bytes(tmp, json.dumps(state, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{lineno}: not JSON ({exc.msg})") from None
