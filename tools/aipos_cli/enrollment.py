"""AIPOS-362 — 远端 agent 凭据注册式下发(enrollment).

设计:
  - 注册码(enrollment code)是一次性、可时效、可吊销的设备注册凭证。
  - Owner/授权顾问签发注册码,绑定 role/instance/ttl。
  - 远端 agent 用注册码兑换(exchange)出真实的 capability token。
  - Token 明文只在 gate→agent 的网络响应中传输,不落任何中间态。
  - 注册码 ≠ token,可以明文传递(它本身不是凭据,只是兑换凭据的临时通行证)。

AIPOS-F23 自包含码(单一格式定义处, 红线: 码格式只有一处定义):
  - `LYBRAENROLL1.<base64url(JSON)>` 其中 JSON = {v, gate_url, governance_root,
    transport_token, code}。内嵌 gate 对外可达地址/治理根/运输通行凭证;
    码即认证(单次 + TTL + 可撤销),工位侧不再需要任何 bootstrap token。
  - 运输通行凭证(transport token)是零 scope 的 service token,只够过 HTTP
    transport 层认证调公开动词(enroll_exchange/enroll_land),兑换出的角色
    token 才是真正的凭据。
  - 交换与落盘原子(F23 验收⑦): 首次 exchange 开 grace 窗口(默认 10 分钟),
    窗口内同码免费重试(返回同一 token, 不重铸);工位落盘成功后调
    lybra_roles_enroll_land 标记 landed;landed 后或 grace 过期后码彻底消费。

存储:
  - 注册码数据存于 <workspace>/.lybra/enrollments.json(仅 gate 侧,0600)。
  - 格式: {code_id: {code, role, instance, status, created_at, expires_at, used_at,
      landed_at, grace_until, minted_token_entry, revoked_at, ...}}
  - 状态: pending(未使用) / used(已兑换) / revoked(已吊销) / expired(已过期)

Red lines:
  - Token 明文永不进日志/输出/argv。
  - 注册码签发需 owner 授权(owner-gated)。
  - 兑换是公开端点(不需要已有 token),但需校验 code 有效性。
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

EnrollmentStatus = Literal["pending", "used", "revoked", "expired"]

# ---------------------------------------------------------------------------
# AIPOS-F23: 自包含码格式 —— 唯一定义处(编码/解码/前缀/字段表都在本模块)
# ---------------------------------------------------------------------------

SELF_CONTAINED_CODE_PREFIX = "LYBRAENROLL1."
SELF_CONTAINED_CODE_VERSION = 1
#: grace 窗口秒数: exchange 后未落盘, 同码可免费重试的时限(验收⑦ "码不白烧")
ENROLL_LANDING_GRACE_SECONDS = 600
#: 自包含码默认 TTL(未显式给 ttl 时; 同时绑定运输凭证的有效期)
ENROLL_DEFAULT_TTL_SECONDS = 86400
#: 运输凭证的角色名(零 scope, 仅过 transport 层; 兑换出的角色 token 才是真凭据)
TRANSPORT_TOKEN_ROLE = "enroll-transport"


def encode_self_contained_code(
    *,
    gate_url: str,
    governance_root: str,
    transport_token: str,
    code: str,
) -> str:
    """F23: 编码自包含码 —— 格式唯一定义处。

    payload = {v:1, gate_url, governance_root, transport_token, code}
    输出 = "LYBRAENROLL1." + base64url(JSON, 无 padding)
    """
    payload = {
        "v": SELF_CONTAINED_CODE_VERSION,
        "gate_url": str(gate_url),
        "governance_root": str(governance_root),
        "transport_token": str(transport_token),
        "code": str(code),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return SELF_CONTAINED_CODE_PREFIX + b64


def decode_self_contained_code(text: str) -> dict[str, Any] | None:
    """F23: 解码自包含码。非自包含码(旧裸码)返回 None, 调用方走旧路径。

    Returns:
        {v, gate_url, governance_root, transport_token, code} 或 None
    """
    s = str(text or "").strip()
    if not s.startswith(SELF_CONTAINED_CODE_PREFIX):
        return None
    b64 = s[len(SELF_CONTAINED_CODE_PREFIX):]
    # 容错: base64url 无 padding → 补齐
    pad = (-len(b64)) % 4
    try:
        raw = base64.urlsafe_b64decode(b64 + "=" * pad).decode("utf-8")
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != SELF_CONTAINED_CODE_VERSION:
        return None
    for key in ("gate_url", "transport_token", "code"):
        if not str(payload.get(key) or "").strip():
            return None
    return {
        "v": payload["v"],
        "gate_url": str(payload["gate_url"]),
        "governance_root": str(payload.get("governance_root") or ""),
        "transport_token": str(payload["transport_token"]),
        "code": str(payload["code"]),
    }


def mint_transport_token_entry(
    workspace_root: str | Path,
    *,
    ttl_seconds: int | None = None,
    code_id: str | None = None,
) -> dict[str, Any]:
    """F23: 铸一枚零 scope 运输通行凭证并注册进 gate 侧 connection.json。

    - role=enroll-transport(不在 roles 注册表 → 任何 scope 检查 fail-closed,
      只能过 transport 层调公开动词)
    - scopes=[] 显式声明零权限
    - expires_at 跟随码 TTL(码即认证: 码过期 = 运输凭证同时失效)
    - 注册后由调用方触发 _reload_token_registry() 热生效
    """
    from tools.aipos_cli.service_mode import secret_fingerprint
    from tools.aipos_cli.enroll_client import (
        ensure_lybra_dir,
        load_or_create_connection_json,
        upsert_token_entry,
        write_connection_json,
    )

    root = _workspace_root_path(workspace_root)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ttl = int(ttl_seconds) if ttl_seconds and int(ttl_seconds) > 0 else ENROLL_DEFAULT_TTL_SECONDS
    expires_at = (now + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
    entry = {
        "role": TRANSPORT_TOKEN_ROLE,
        "token": token,
        "token_ref": f"svc-{TRANSPORT_TOKEN_ROLE}",
        "scopes": [],
        "fingerprint": secret_fingerprint(token),
        "expires_at": expires_at,
    }
    if code_id:
        entry["agent_instance"] = code_id  # 可追溯: 运输凭证 ←→ 码

    lybra_dir = ensure_lybra_dir(root)
    connection_data = load_or_create_connection_json(lybra_dir, gate_url=None)
    upsert_token_entry(connection_data, entry)
    write_connection_json(lybra_dir, connection_data)
    return entry


def resolve_gate_url_default(workspace_root: str | Path) -> str:
    """F23: 自包含码内嵌 gate_url 的缺省推导。

    优先 connection.json#mcp.rpc_url(非 loopback 才用 —— 对外可达);
    否则 http://127.0.0.1:7118(config.schema 缺省口)。
    """
    from tools.aipos_cli.enroll_client import ensure_lybra_dir, load_or_create_connection_json

    root = _workspace_root_path(workspace_root)
    try:
        lybra_dir = ensure_lybra_dir(root)
        data = load_or_create_connection_json(lybra_dir, gate_url=None)
        rpc_url = str(((data.get("mcp") or {}).get("rpc_url")) or "")
    except Exception:
        rpc_url = ""
    if rpc_url:
        url = rpc_url[:-len("/mcp")] if rpc_url.endswith("/mcp") else rpc_url
        host_part = url.split("//", 1)[-1].split(":", 1)[0]
        if host_part not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            return url
    return "http://127.0.0.1:7118"


def issue_self_contained_code(
    workspace_root: str | Path,
    *,
    role: str,
    instance: str | None = None,
    ttl_seconds: int | None = None,
    gate_url: str | None = None,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """F23: 发码的唯一实现(门动词 lybra_enroll_code_* 与 CLI roles enroll-code 共用)。

    红线: 发码只有一份实现 —— 本函数。产出:
      - 既有 enrollment 记录(create_enrollment_code, 单次/TTL/撤销面沿用)
      - 运输通行凭证(零 scope, 注册进 gate connection.json)
      - 自包含码(encode_self_contained_code)
      - 可转贴的会话指令文本: "/lybra enroll <自包含码>"
    """
    root = _workspace_root_path(workspace_root)
    effective_ttl = int(ttl_seconds) if ttl_seconds and int(ttl_seconds) > 0 else ENROLL_DEFAULT_TTL_SECONDS

    # ① 既有发码实现(单次/TTL/撤销面不动)
    enrollment = create_enrollment_code(
        root,
        role=role,
        instance=instance,
        ttl_seconds=effective_ttl,
        by=by,
        reason=reason,
    )

    # ② 运输通行凭证(TTL 与码一致; 码即认证 —— 码过期凭证同步失效)
    transport_entry = mint_transport_token_entry(
        root, ttl_seconds=effective_ttl, code_id=enrollment["code_id"]
    )

    # ③ 自包含码(gate_url 缺省推导, 显式参数优先)
    resolved_gate_url = (gate_url or "").strip() or resolve_gate_url_default(root)
    self_contained = encode_self_contained_code(
        gate_url=resolved_gate_url,
        governance_root=str(root),
        transport_token=transport_entry["token"],
        code=enrollment["code"],
    )

    # ④ 可转贴会话指令文本(Owner 唯一要做的事: 转贴这一条)
    paste_text = f"/lybra enroll {self_contained}"

    return {
        "ok": True,
        "code_id": enrollment["code_id"],
        "self_contained_code": self_contained,
        "paste_text": paste_text,
        "role": role,
        "instance": instance,
        "ttl_seconds": effective_ttl,
        "expires_at": enrollment.get("expires_at"),
        "gate_url": resolved_gate_url,
        "governance_root": str(root),
        "transport_token_fingerprint": transport_entry.get("fingerprint"),
        "transport_token_expires_at": transport_entry.get("expires_at"),
        "fingerprint": enrollment["fingerprint"],
        "by": by,
        "reason": reason,
    }


def _workspace_root_path(workspace_root: str | Path) -> Path:
    """Convert workspace_root to Path."""
    return Path(workspace_root).resolve()


def _enrollments_path(workspace_root: str | Path) -> Path:
    """返回 enrollment codes 存储路径: <workspace>/.lybra/enrollments.json"""
    root = _workspace_root_path(workspace_root)
    return root / ".lybra" / "enrollments.json"


def _load_enrollments(workspace_root: str | Path) -> dict[str, dict[str, Any]]:
    """加载所有注册码记录。返回 {code_id: enrollment_record}。"""
    path = _enrollments_path(workspace_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save_enrollments(workspace_root: str | Path, data: dict[str, dict[str, Any]]) -> None:
    """保存注册码记录,0600 权限。"""
    path = _enrollments_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _generate_enrollment_code() -> str:
    """生成一个 48 字节的 URL-safe 注册码(64 字符)。"""
    return secrets.token_urlsafe(48)


def _code_fingerprint(code: str) -> str:
    """返回注册码的非秘密指纹(sha256 前 12 位)。"""
    import hashlib
    return "sha256:" + hashlib.sha256(code.encode()).hexdigest()[:12]


def _append_enrollment_trail(
    workspace_root: Path,
    *,
    action: str,
    code_id: str,
    role: str,
    instance: str | None,
    by: str,
    reason: str,
) -> Path:
    """Append-only 审计日志。"""
    from tools.aipos_cli.workspace_config import governance_paths
    trail = governance_paths(workspace_root)["decision_log"].parent / "enrollment_log.md"
    trail.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    inst_str = f"instance={instance}" if instance else "instance=(any)"
    line = f"- {ts}  {action}  code_id={code_id}  role={role}  {inst_str}  by={by}  reason={reason or '(none)'}\n"
    with trail.open("a", encoding="utf-8") as fh:
        if trail.stat().st_size == 0:
            fh.write("# Enrollment Codes Log (append-only)\n\n")
        fh.write(line)
    return trail


def create_enrollment_code(
    workspace_root: str | Path,
    *,
    role: str,
    instance: str | None = None,
    ttl_seconds: int | None = None,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """创建一个注册码。
    
    Args:
        workspace_root: 工作区根路径
        role: 要绑定的角色名(如 executor, auditor, 或自定义角色)
        instance: 可选的实例名(如 exec.lybra.mac1);None 表示不绑定实例
        ttl_seconds: 过期时间(秒);None 表示永不过期
        by: 签发者(owner 或授权引用)
        reason: 签发原因
    
    Returns:
        {
            "code_id": str,
            "code": str,  # 明文注册码,仅此处返回一次
            "fingerprint": str,
            "role": str,
            "instance": str | None,
            "status": "pending",
            "created_at": str (ISO8601),
            "expires_at": str | None (ISO8601),
            "by": str,
            "reason": str
        }
    """
    root = _workspace_root_path(workspace_root)
    code = _generate_enrollment_code()
    code_id = f"enroll_{secrets.token_hex(8)}"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = (now + timedelta(seconds=ttl_seconds)) if ttl_seconds else None
    
    record = {
        "code": code,
        "fingerprint": _code_fingerprint(code),
        "role": role,
        "instance": instance,
        "status": "pending",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z") if expires_at else None,
        "used_at": None,
        "revoked_at": None,
        "by": by,
        "reason": reason,
    }
    
    enrollments = _load_enrollments(root)
    enrollments[code_id] = record
    _save_enrollments(root, enrollments)
    
    _append_enrollment_trail(
        root,
        action="create",
        code_id=code_id,
        role=role,
        instance=instance,
        by=by,
        reason=reason,
    )
    
    return {
        "code_id": code_id,
        "code": code,  # 明文 code,仅此处返回
        "fingerprint": record["fingerprint"],
        "role": role,
        "instance": instance,
        "status": "pending",
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "by": by,
        "reason": reason,
    }


def get_enrollment_status(workspace_root: str | Path, code: str) -> tuple[EnrollmentStatus, dict[str, Any] | None]:
    """根据注册码查询其状态。
    
    Returns:
        (status, record)
        - status: "pending" | "used" | "revoked" | "expired"
        - record: 完整记录(含 code_id),如果找不到则为 None
    """
    enrollments = _load_enrollments(workspace_root)
    now = datetime.now(timezone.utc)
    
    for code_id, rec in enrollments.items():
        if rec.get("code") == code:
            # 检查状态
            if rec.get("status") == "revoked":
                return "revoked", {**rec, "code_id": code_id}
            if rec.get("status") == "used":
                return "used", {**rec, "code_id": code_id}
            # 检查是否过期
            expires_str = rec.get("expires_at")
            if expires_str:
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                if now > expires_at:
                    return "expired", {**rec, "code_id": code_id}
            return "pending", {**rec, "code_id": code_id}
    
    return "expired", None  # 找不到视为已过期(防止暴力枚举)


def mark_enrollment_used(
    workspace_root: str | Path,
    code: str,
    *,
    token_entry: dict[str, Any] | None = None,
    grace_seconds: int = ENROLL_LANDING_GRACE_SECONDS,
) -> dict[str, Any]:
    """标记注册码为已使用(F23: 交换与落盘原子 —— grace 窗口机制)。

    首次调用: status=pending → used, 记 used_at / grace_until / minted_token_entry;
    窗口内重试(同码): 幂等返回既有记录(同一 token, 不重铸);
    landed 后 / grace 过期后: ValueError(码已彻底消费)。

    Args:
        token_entry: 首次兑换时铸出的 token entry(存入记录供免费重试返回同一 token)

    Returns:
        更新后的记录(含 code_id)

    Raises:
        ValueError: 如果 code 不是 pending 且不在免费重试窗口内
    """
    root = _workspace_root_path(workspace_root)
    enrollments = _load_enrollments(root)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # 找到对应记录(按明文 code 匹配)
    code_id = None
    record = None
    for cid, rec in enrollments.items():
        if rec.get("code") == code:
            code_id, record = cid, rec
            break
    if record is None:
        raise ValueError("Enrollment code not found")

    status, _ = get_enrollment_status(root, code)

    if status == "pending":
        record["status"] = "used"
        record["used_at"] = now.isoformat().replace("+00:00", "Z")
        record["landed_at"] = None
        record["grace_until"] = (
            now + timedelta(seconds=max(1, int(grace_seconds)))
        ).isoformat().replace("+00:00", "Z")
        if token_entry is not None:
            record["minted_token_entry"] = token_entry
        _save_enrollments(root, enrollments)
        _append_enrollment_trail(
            root,
            action="use",
            code_id=code_id,
            role=record["role"],
            instance=record.get("instance"),
            by="(agent-exchange)",
            reason=(
                f"enrollment code exchanged for token"
                f" (landing grace until {record['grace_until']})"
            ),
        )
        return {**record, "code_id": code_id}

    if status == "used":
        # F23 验收⑦: grace 窗口内同码免费重试(落盘前中断 → 码不白烧)
        if not record.get("landed_at") and record.get("minted_token_entry"):
            grace_until = record.get("grace_until")
            in_grace = False
            if grace_until:
                try:
                    in_grace = now <= datetime.fromisoformat(grace_until.replace("Z", "+00:00"))
                except ValueError:
                    in_grace = False
            if in_grace:
                return {**record, "code_id": code_id, "retry": True}
            reason = f"Enrollment code is used (landing grace expired at {grace_until}; token was minted but workstation never landed it)"
        elif record.get("landed_at"):
            reason = f"Enrollment code is used (already landed at {record['landed_at']})"
        else:
            reason = "Enrollment code is used"
        raise ValueError(reason)

    raise ValueError(f"Enrollment code is {status}, cannot use")


def land_enrollment(
    workspace_root: str | Path,
    code: str,
    *,
    landed_detail: str = "",
) -> dict[str, Any]:
    """F23 验收⑦/⑧: 工位落盘成功后确认 —— 关闭 grace 窗口, 码彻底消费。

    幂等: 已 landed 再调不报错(返回既有记录, retry=True)。

    Returns:
        更新后的记录(含 code_id)

    Raises:
        ValueError: code 不存在 / 尚未 exchange(必须先 exchange 再 land)/ 已吊销或过期
    """
    root = _workspace_root_path(workspace_root)
    enrollments = _load_enrollments(root)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    code_id = None
    record = None
    for cid, rec in enrollments.items():
        if rec.get("code") == code:
            code_id, record = cid, rec
            break
    if record is None:
        raise ValueError("Enrollment code not found")

    if record.get("landed_at"):
        return {**record, "code_id": code_id, "retry": True}

    status, _ = get_enrollment_status(root, code)
    if status != "used":
        raise ValueError(
            f"Enrollment code is {status}; land requires a prior exchange (used). "
            "Call lybra_roles_enroll_exchange first."
        )

    record["landed_at"] = now.isoformat().replace("+00:00", "Z")
    if landed_detail:
        record["landed_detail"] = str(landed_detail)[:400]
    _save_enrollments(root, enrollments)
    _append_enrollment_trail(
        root,
        action="land",
        code_id=code_id,
        role=record["role"],
        instance=record.get("instance"),
        by="(agent-enroll)",
        reason=landed_detail or "workstation landed .lybra/ config",
    )
    return {**record, "code_id": code_id}


def revoke_enrollment_code(
    workspace_root: str | Path,
    code_id: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """吊销一个注册码。幂等:已吊销的再次吊销不报错。
    
    Returns:
        更新后的记录
    
    Raises:
        ValueError: 如果 code_id 不存在
    """
    root = _workspace_root_path(workspace_root)
    enrollments = _load_enrollments(root)
    if code_id not in enrollments:
        raise ValueError(f"Enrollment code_id not found: {code_id}")
    
    rec = enrollments[code_id]
    if rec.get("status") != "revoked":
        now = datetime.now(timezone.utc).replace(microsecond=0)
        rec["status"] = "revoked"
        rec["revoked_at"] = now.isoformat().replace("+00:00", "Z")
        _save_enrollments(root, enrollments)
        
        _append_enrollment_trail(
            root,
            action="revoke",
            code_id=code_id,
            role=rec["role"],
            instance=rec.get("instance"),
            by=by,
            reason=reason,
        )
    
    return {**rec, "code_id": code_id}


def list_enrollment_codes(workspace_root: str | Path, *, include_code: bool = False) -> list[dict[str, Any]]:
    """列出所有注册码。
    
    Args:
        workspace_root: 工作区根路径
        include_code: 是否包含明文 code(默认 False,仅返回 fingerprint)
    
    Returns:
        注册码列表,每项含 code_id 和状态
    """
    enrollments = _load_enrollments(workspace_root)
    now = datetime.now(timezone.utc)
    result = []
    
    for code_id, rec in enrollments.items():
        # 动态计算状态(考虑过期)
        status = rec.get("status", "pending")
        if status == "pending":
            expires_str = rec.get("expires_at")
            if expires_str:
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                if now > expires_at:
                    status = "expired"
        
        item = {
            "code_id": code_id,
            "fingerprint": rec.get("fingerprint", ""),
            "role": rec.get("role", ""),
            "instance": rec.get("instance"),
            "status": status,
            "landed": bool(rec.get("landed_at")),
            "created_at": rec.get("created_at"),
            "expires_at": rec.get("expires_at"),
            "used_at": rec.get("used_at"),
            "landed_at": rec.get("landed_at"),
            "grace_until": rec.get("grace_until"),
            "revoked_at": rec.get("revoked_at"),
            "by": rec.get("by", ""),
            "reason": rec.get("reason", ""),
        }
        if include_code:
            item["code"] = rec.get("code", "")
        result.append(item)
    
    return result


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
