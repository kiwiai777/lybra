"""Network helpers for relay channels (stdlib only).

Policy: encrypted transports (https / wss) anywhere; plain http / ws only to a loopback host (local test fixtures).
No helper here logs URLs with query strings, headers or bodies (they may carry credentials).
"""
from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message

MAX_DOWNLOAD_BYTES = 110 * 1024 * 1024  # WeCom documents a 100 MB attachment ceiling


class NetPolicyError(ValueError):
    pass


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url: str, secure_scheme: str, plain_scheme: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == secure_scheme and parts.hostname:
        return parts
    if parts.scheme == plain_scheme and parts.hostname and is_loopback_host(parts.hostname):
        return parts
    raise NetPolicyError(f"refused URL scheme/host: {parts.scheme}://{parts.hostname or ''} "
                         f"({secure_scheme} required; {plain_scheme} only for loopback test fixtures)")


def describe_url(url: str) -> str:
    """scheme://host/path without query or fragment (safe for logs)."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlsplit(url).hostname or ""
    if is_loopback_host(host):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


@dataclass
class HttpResult:
    status: int
    headers: Message
    body: bytes


class HttpError(Exception):
    def __init__(self, what: str, status: int | None = None, timed_out: bool = False) -> None:
        super().__init__(what)
        self.what = what
        self.status = status
        self.timed_out = timed_out


def request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None,
            timeout: float = 30.0, max_bytes: int = MAX_DOWNLOAD_BYTES) -> HttpResult:
    check_url(url, "https", "http")
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with _opener(url).open(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise HttpError(f"response larger than {max_bytes} bytes")
            return HttpResult(status=resp.status, headers=resp.headers, body=data)
    except urllib.error.HTTPError as exc:
        raise HttpError(f"HTTP {exc.code} from {describe_url(url)}", status=exc.code) from None
    except urllib.error.URLError as exc:
        timed_out = isinstance(exc.reason, TimeoutError)
        raise HttpError(f"{type(exc.reason).__name__} reaching {describe_url(url)}", timed_out=timed_out) from None
    except TimeoutError:
        raise HttpError(f"timeout reaching {describe_url(url)}", timed_out=True) from None
    except OSError as exc:
        raise HttpError(f"{type(exc).__name__} talking to {describe_url(url)}") from None


def post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    result = request(url, method="POST", headers={**headers, "Content-Type": "application/json"},
                     body=json.dumps(payload).encode("utf-8"), timeout=timeout)
    return json.loads(result.body.decode("utf-8")) if result.body.strip() else {}


_FILENAME_STAR = re.compile(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;\s]+)")
_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?')


def content_disposition_filename(value: str | None) -> str | None:
    """Same two forms the official WeCom SDK reads: RFC 5987 filename*=UTF-8''... then plain filename=..."""
    if not value:
        return None
    match = _FILENAME_STAR.search(value) or _FILENAME.search(value)
    if not match:
        return None
    return urllib.parse.unquote(match.group(1).strip())


_UNSAFE = re.compile(r"[^\w.\-]+", re.UNICODE)


def safe_file_part(name: str, limit: int = 80) -> str:
    """Make a server-supplied name safe as one path segment (no separators, no leading dots)."""
    cleaned = _UNSAFE.sub("_", name).strip("._") or "file"
    return cleaned[:limit]


_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"RIFF", "riff"),
)


def sniff_type(data: bytes) -> str | None:
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            if kind == "riff" and data[8:12] == b"WEBP":
                return "webp"
            return None if kind == "riff" else kind
    if data[4:8] == b"ftyp":
        return "mp4"
    return None
