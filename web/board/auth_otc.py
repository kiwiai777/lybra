"""AIPOS-271 — token-less login: one-time tickets (OTC + device code) + auth-log trail.

设计原则(卡 §设计原则)
----------------------
**文件系统访问权 = 身份根**:能读 ``.lybra/connection.json`` 的人就是工作区主人。
CLI 据此铸短时一次性凭据(OTC / 设备码),浏览器只拿会话 cookie,人手永不触碰 token。

红线钉(卡 §红线)
-----------------
- **零依赖**:本模块仅用 stdlib,与 ``app.py`` 的 SessionStore 一致。
- **凭据不落日志**:原始 token、OTC 值、设备码值 **绝不**写入 auth-log;auth-log 只记录
  时间 / 方式 / 角色 / token_ref / 来源 IP。
- **文件权即身份不外延**:本模块不读 connection.json(那是 CLI 侧的职责);server 只在
  mint / approve 时校验 CLI 递来的 token 指纹(复用 ``app.verify_login_token``)。
- 进程内内存态,重启即失效(重新登录),与 SessionStore 语义一致;线程安全。
- **F-271-3**: 长效 cookie(30天)仍 HttpOnly,secret 持久化到 .lybra/remember_secret 0600 文件。

三类一次性凭据
-------------
- **OTC**(本机无感):``secrets.token_urlsafe`` 长随机串,放进 ``/login?otc=…`` 链接;
  TTL 60s,单次即焚,mint 时由 CLI 携 token 鉴权,redeem 时换会话 cookie。
- **设备码**(跨机):6 位数字短码,供人在另一台机器上口报/手敲;TTL 300s,单次;
  ``board approve <码>`` 在 gate 机以文件权(token)确认 → 浏览器轮询到 approved 即换 cookie。

时间基准用 ``time.monotonic()``(免疫墙钟回拨),与 wall-clock ISO 时间戳分开:
expiry 判定走 monotonic,auth-log 落盘走 UTC ISO。
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# 卡钉死的 TTL(秒)。OTC 60s / 设备码 300s。
OTC_TTL_SECONDS = 60
DEVICE_CODE_TTL_SECONDS = 300

# 设备码字符集与长度(6 位数字,便于人手敲)。
_DEVICE_ALPHABET = "0123456789"
_DEVICE_CODE_LEN = 6


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _random_otc() -> str:
    """长随机不透明串(放 URL,本机用,不可猜)。"""
    return secrets.token_urlsafe(24)


def _random_device_code() -> str:
    """6 位数字短码(跨机口报,人手敲)。"""
    return "".join(secrets.choice(_DEVICE_ALPHABET) for _ in range(_DEVICE_CODE_LEN))


class _BaseTicketStore:
    """通用一次性票根表: monotonic 过期判定 + 单次即焚 + 线程安全。

    子类负责 mint 用的随机串生成器与 TTL。redeem/poll 命中即删除(无论是否过期)。
    """

    def __init__(self, *, ttl_seconds: int, mk_token: Callable[[], str]) -> None:
        self._ttl = int(ttl_seconds)
        self._mk = mk_token
        self._tickets: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def _issue(self, **payload: Any) -> str:
        """铸一张票(重试避碰撞),记 monotonic 过期点。"""
        with self._lock:
            for _ in range(8):
                code = self._mk()
                if code and code not in self._tickets:
                    self._tickets[code] = {
                        "expire_at": self._now() + self._ttl,
                        **payload,
                    }
                    return code
            raise RuntimeError("ticket collision exhausted (impossibly unlikely)")

    def _peek(self, code: str | None) -> dict[str, Any] | None:
        """取出且删除(单次即焚);过期也删并返回 None。"""
        if not code:
            return None
        with self._lock:
            entry = self._tickets.pop(code, None)
        if entry is None:
            return None
        if self._now() > float(entry.get("expire_at", 0.0)):
            return None  # 过期:已删除,视为未命中  # i18n-exempt: code comment
        return entry


class OTCStore(_BaseTicketStore):
    """一次性换票码(本机无感)。TTL 60s,单次即焚,内存态。

    - :meth:`mint`:由 CLI 携 token 鉴权后调用 → 返回不透明 OTC 串。
    - :meth:`redeem`:浏览器 GET ``/login?otc=…`` 时调用 → 命中返回会话信息并删除;
      未命中 / 过期 / 已用 → None(三者等价地表现为"票无效")。
    """

    def __init__(self, *, ttl_seconds: int = OTC_TTL_SECONDS) -> None:
        super().__init__(ttl_seconds=ttl_seconds, mk_token=_random_otc)

    def mint(self, *, role: str, scopes: list[str], token_ref: str = "") -> str:
        """铸一张 OTC(绑定登录角色信息,redeem 时据此建会话)。返回不透明 OTC 串。"""
        return self._issue(role=role, scopes=list(scopes), token_ref=token_ref)

    def redeem(self, otc: str | None) -> dict[str, Any] | None:
        entry = self._peek(otc)
        if entry is None:
            return None
        return {
            "role": str(entry.get("role") or ""),
            "scopes": [str(s) for s in entry.get("scopes") or []],
            "token_ref": str(entry.get("token_ref") or ""),
        }


class DeviceCodeStore(_BaseTicketStore):
    """跨机设备码(6 位数字)。TTL 300s,单次即焚。

    三态流转:``issue``(pending) → ``approve``(由 CLI 携 token 确认,写入会话信息)
    → ``consume``(浏览器轮询命中 approved 时取出,换 cookie)。任一步过期/未命中 → None。
    """

    def __init__(self, *, ttl_seconds: int = DEVICE_CODE_TTL_SECONDS) -> None:
        super().__init__(ttl_seconds=ttl_seconds, mk_token=_random_device_code)

    def issue(self) -> str:
        """浏览器申请一个 pending 设备码(此时无身份,approve 才绑身份)。"""
        return self._issue(status="pending")

    def approve(
        self, code: str | None, *, role: str, scopes: list[str], token_ref: str = ""
    ) -> bool:
        """CLI 携 token 确认一个 pending 码 → 写入会话信息。仅 pending 且未过期可批。"""
        if not code:
            return False
        with self._lock:
            entry = self._tickets.get(code)
            if entry is None:
                return False
            if self._now() > float(entry.get("expire_at", 0.0)):
                self._tickets.pop(code, None)
                return False
            if entry.get("status") != "pending":
                return False
            entry["status"] = "approved"
            entry["role"] = role
            entry["scopes"] = list(scopes)
            entry["token_ref"] = token_ref
            return True

    def poll(self, code: str | None) -> dict[str, Any]:
        """浏览器轮询。返回 ``{status}``;approved 且未过期时 **取出**(单次)并带回
        会话信息,调用方据此建会话 + 发 cookie。status ∈ {pending, approved, expired, unknown}。"""
        if not code:
            return {"status": "unknown"}
        with self._lock:
            entry = self._tickets.get(code)
            if entry is None:
                return {"status": "unknown"}
            if self._now() > float(entry.get("expire_at", 0.0)):
                self._tickets.pop(code, None)
                return {"status": "expired"}
            if entry.get("status") != "approved":
                return {"status": "pending"}
            # approved 且未过期:单次取出。
            self._tickets.pop(code, None)
            return {
                "status": "approved",
                "role": str(entry.get("role") or ""),
                "scopes": [str(s) for s in entry.get("scopes") or []],
                "token_ref": str(entry.get("token_ref") or ""),
            }


def resolve_auth_log_path(repo_root: Path | None) -> Path | None:
    """auth-log 落点 = ``<repo_root>/.lybra/auth-log.jsonl``;repo_root 为 None → None(不落盘)。"""
    if repo_root is None:
        return None
    return Path(repo_root).expanduser() / ".lybra" / "auth-log.jsonl"


def append_auth_log(
    log_path: Path | None,
    *,
    method: str,
    role: str,
    token_ref: str,
    source_ip: str,
) -> bool:
    """追加一条登录留痕(JSONL)。**只记** 时间/方式/角色/token_ref/来源 IP;
    **绝不记** 原始 token / OTC 值 / 设备码值(卡红线)。落盘失败不抛(登录优先,留痕尽力)。

    method ∈ {"token", "otc", "device_code"}。返回是否成功写入。
    """
    if log_path is None:
        return False
    record = {
        "ts": _utc_now(),
        "method": method,
        "role": role,
        "token_ref": token_ref,
        "source_ip": source_ip,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


# F-271-3: 长效 cookie 支持(30天),secret 持久化到 .lybra/remember_secret
REMEMBER_DAYS = 30
REMEMBER_SECRET_FILE = "remember_secret"


def load_or_create_remember_secret(repo_root: Path | None) -> str:
    """读取或生成 remember secret(用于签名长效 cookie)。
    
    F-271-3: secret 持久化到 <repo_root>/.lybra/remember_secret (0600),免 serve 重启后全体掉登录。
    repo_root 为 None 时返回一个临时 secret(进程内存态,重启失效)。
    """
    if repo_root is None:
        return secrets.token_urlsafe(32)
    
    secret_path = Path(repo_root).expanduser() / ".lybra" / REMEMBER_SECRET_FILE
    try:
        if secret_path.is_file():
            content = secret_path.read_text(encoding="utf-8").strip()
            if content:
                return content
    except OSError:
        pass
    
    # 生成新 secret 并持久化
    new_secret = secrets.token_urlsafe(32)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text(new_secret, encoding="utf-8")
        secret_path.chmod(0o600)  # 仅所有者可读写  # i18n-exempt: code comment
        return new_secret
    except OSError:
        return new_secret  # 写入失败也返回,降级为进程内存态  # i18n-exempt: code comment


def sign_remember_token(secret: str, session_id: str, role: str, scopes: list[str], token_ref: str = "") -> str:
    """签名一个 remember token，携带重建会话所需的完整信息。
    
    格式：session_id:role:scopes_json:token_ref:hmac_signature
    """
    import hashlib
    import hmac
    import json
    scopes_str = json.dumps(sorted(scopes), separators=(',', ':'), ensure_ascii=False)
    payload = f"{session_id}:{role}:{scopes_str}:{token_ref}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_remember_token(secret: str, token: str) -> dict[str, Any] | None:
    """验证 remember token 签名并返回会话信息。失败返回 None。
    
    返回：{"session_id": str, "role": str, "scopes": list[str], "token_ref": str}
    """
    import hashlib
    import hmac
    import json
    
    parts = token.split(":")
    if len(parts) < 5:  # session_id:role:scopes:token_ref:signature (至少5段)  # i18n-exempt: code comment
        return None
    
    # 最后一段是签名，前面可能因为 scopes_json 中有冒号而多段
    signature = parts[-1]
    payload_parts = parts[:-1]
    
    # 重组：session_id, role 各一段，token_ref 一段（可能为空），中间都是 scopes_json
    if len(payload_parts) < 3:
        return None
    
    session_id = payload_parts[0]
    role = payload_parts[1]
    token_ref = payload_parts[-1]  # 最后一段是 token_ref  # i18n-exempt: code comment
    scopes_json = ":".join(payload_parts[2:-1])  # 中间所有段重组为 scopes_json  # i18n-exempt: code comment
    
    payload = f"{session_id}:{role}:{scopes_json}:{token_ref}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(expected, signature):
        return None
    
    try:
        scopes = json.loads(scopes_json)
        if not isinstance(scopes, list):
            return None
    except (json.JSONDecodeError, ValueError):
        return None
    
    return {
        "session_id": session_id,
        "role": role,
        "scopes": [str(s) for s in scopes],
        "token_ref": token_ref,
    }
