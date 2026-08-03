"""AIPOS-271 — board 无感登录 CLI 客户端(``board open`` / ``board approve``)。

设计原则(卡 §设计原理)
----------------------
**文件权即身份**:本模块读 ``.lybra/connection.json`` 拿到角色 token —— 这就是"我是工作区
主人"的根。据此向 board server 铸一张短时一次性凭据(OTC / 设备码),浏览器只拿会话 cookie,
人手永不触碰 token。

- ``board open``:读 connection.json → 携 token 调 ``/api/auth/otc/mint`` 铸 OTC → 开/打
  ``/login?otc=…``(server redeem 换 cookie)。本机无感进站。
- ``board approve <码>``:跨机设备码流 —— gate 机读到 token 即确认身份 → 调
  ``/api/auth/device/approve`` 批准该码;远端浏览器轮询到 approved 即进站。

红线钉(卡 §红线)
-----------------
- **零依赖**:仅 stdlib(``http.client`` / ``json`` / ``webbrowser``);与 aipos_cli 其余部分一致。
- **凭据不落日志**:原始 token 只在内存中经 loopback 发给 server,绝不打印/绝不写 argv;
  本客户端也不打印 token。指纹可用于人话汇报。
- **文件权即身份不外延**:只读 connection.json 拿本机已有角色 token,不生成、不旁路、不改 token。
"""
from __future__ import annotations

import hashlib
import http.client
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse




DEFAULT_CONNECTION_JSON = "~/.lybra/local/connection.json"
DEFAULT_BOARD_HOST = "127.0.0.1"
DEFAULT_BOARD_PORT = 7117
_HTTP_TIMEOUT = 8  # loopback,失败要快


@dataclass
class MintResult:
    """``board open`` 铸 OTC 的结果。"""

    ok: bool
    otc: str = ""
    login_url: str = ""
    expires_in: int = 0
    error: str = ""


def token_fingerprint(token: str) -> str:
    """token 的非密指纹(sha256 前 12 hex),用于人话汇报;绝不回显原始 token。"""
    if not token:
        return "(none)"
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def load_role_token(
    connection_json: str | Path | None = None,
    *,
    role: str | None = None,
) -> tuple[str, str]:
    """读 connection.json 拿一个角色 token。返回 ``(token, role_used)``。

    - ``role`` 给定:精确取该角色(缺失抛错)。
    - ``role`` 为 None:优先 ``owner``,否则取 **第一个** 带 token 的条目(文件权即身份,
      能读此文件者即工作区主人;具体哪个角色仅影响看板视图)。
    原始 token 仅进程内使用,调用方绝不打印(用 :func:`token_fingerprint` 汇报)。
    """
    path = Path(connection_json or DEFAULT_CONNECTION_JSON).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, list) or not tokens:
        raise ValueError(f"connection.json {path} 没有 tokens 条目")
    if role is not None:
        for item in tokens:
            if isinstance(item, dict) and str(item.get("role") or "") == role:
                tok = str(item.get("token") or "").strip()
                if not tok:
                    raise ValueError(f"角色 {role!r} 在 {path} 没有 token")
                return tok, role
        raise ValueError(f"角色 {role!r} 未在 {path} 中找到")
    # role=None:优先 owner 系,否则第一个有 token 的条目。
    preferred = ("owner", "owner-dispatch", "auditor", "executor")
    pool = [t for t in tokens if isinstance(t, dict) and (t.get("token") or "").strip()]
    for want in preferred:
        for item in pool:
            if str(item.get("role") or "") == want:
                return str(item["token"]).strip(), str(item.get("role"))
    if pool:
        item = pool[0]
        return str(item["token"]).strip(), str(item.get("role"))
    raise ValueError(f"{path} 中没有任何带 token 的角色条目")


def resolve_board_url(
    connection_json: str | Path | None = None,
    *,
    url: str | None = None,
    host: str | None = None,
    port: int | None = None,
    workspace_root: str | Path | None = None,
) -> str:
    """解析 board server 的 base URL。优先级:显式 url → host/port → connection.json board → workspace config → 默认。
    
    F-271-1: 单工作区自动发现 —— workspace_root 给定时优先读 <workspace>/.lybra/config.json,
    否则读 connection.json,兜底默认。
    """
    if url:
        return url.rstrip("/")
    if host or port:
        return f"http://{host or DEFAULT_BOARD_HOST}:{port or DEFAULT_BOARD_PORT}"
    
    # F-271-1: 优先从 workspace config 读取(单工作区场景)
    if workspace_root:
        ws_config = Path(workspace_root).expanduser() / ".lybra" / "config.json"
        if ws_config.is_file():
            try:
                data = json.loads(ws_config.read_text(encoding="utf-8"))
                board = data.get("board") if isinstance(data, dict) else None
                if isinstance(board, dict):
                    if board.get("url"):
                        return str(board["url"]).rstrip("/")
                    bhost = board.get("host") or DEFAULT_BOARD_HOST
                    bport = board.get("port") or DEFAULT_BOARD_PORT
                    return f"http://{bhost}:{bport}"
            except (OSError, json.JSONDecodeError):
                pass
    
    # 回退到 connection.json
    path = Path(connection_json or DEFAULT_CONNECTION_JSON).expanduser()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            board = data.get("board") if isinstance(data, dict) else None
            if isinstance(board, dict):
                if board.get("url"):
                    return str(board["url"]).rstrip("/")
                bhost = board.get("host") or DEFAULT_BOARD_HOST
                bport = board.get("port") or DEFAULT_BOARD_PORT
                return f"http://{bhost}:{bport}"
        except (OSError, json.JSONDecodeError):
            pass
    return f"http://{DEFAULT_BOARD_HOST}:{DEFAULT_BOARD_PORT}"


def _post_json(base_url: str, path: str, body: dict) -> tuple[int, dict]:
    """向 board server POST JSON(loopback),返回 (status, parsed_json)。

    连接/解析失败 → 抛 ConnectionError(给 CLI 上层转人话)。原始 token 在 body 内经
    loopback 传输,不进任何日志。"""
    parsed = urlparse(base_url)
    host = parsed.hostname or DEFAULT_BOARD_HOST
    port = parsed.port or DEFAULT_BOARD_PORT
    payload = json.dumps(body).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=_HTTP_TIMEOUT)
    try:
        conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        status = resp.status
    finally:
        conn.close()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def mint_otc(base_url: str, token: str) -> MintResult:
    """/api/auth/otc/mint:携 token 铸 OTC。成功返回 OTC + login_url;失败填 error。"""
    status, data = _post_json(base_url, "/api/auth/otc/mint", {"token": token})
    if status == 200 and data.get("ok"):
        return MintResult(
            ok=True,
            otc=str(data.get("otc") or ""),
            login_url=str(data.get("login_url") or ""),
            expires_in=int(data.get("expires_in") or 0),
        )
    if status == 401:
        return MintResult(ok=False, error="token 无效或未匹配到任何角色(server 拒绝)")
    return MintResult(ok=False, error=str(data.get("message") or f"server 返回 HTTP {status}"))


def approve_device(base_url: str, token: str, code: str) -> tuple[bool, str]:
    """/api/auth/device/approve:确认一个 pending 设备码。返回 (ok, message)。"""
    code_clean = (code or "").strip()
    if not code_clean:
        return False, "设备码不能为空"
    status, data = _post_json(base_url, "/api/auth/device/approve", {"code": code_clean, "token": token})
    if status == 401:
        return False, "token 无效或未匹配到任何角色(server 拒绝)"
    if status == 200:
        if data.get("ok"):
            return True, "已批准:远端浏览器将自动进站"
        return False, f"未批准:码 {code_clean} 不存在 / 已过期 / 已批准"
    return False, str(data.get("message") or f"server 返回 HTTP {status}")


def build_login_url(base_url: str, login_path: str) -> str:
    """把 server 返回的相对 login_url(/login?otc=…)拼成可开/可打印的绝对 URL。"""
    if login_path.startswith("http://") or login_path.startswith("https://"):
        return login_path
    return base_url.rstrip("/") + "/" + login_path.lstrip("/")
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
