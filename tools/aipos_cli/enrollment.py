"""AIPOS-362 — 远端 agent 凭据注册式下发(enrollment).

设计:
  - 注册码(enrollment code)是一次性、可时效、可吊销的设备注册凭证。
  - Owner/授权顾问签发注册码,绑定 role/instance/ttl。
  - 远端 agent 用注册码兑换(exchange)出真实的 capability token。
  - Token 明文只在 gate→agent 的网络响应中传输,不落任何中间态。
  - 注册码 ≠ token,可以明文传递(它本身不是凭据,只是兑换凭据的临时通行证)。

存储:
  - 注册码数据存于 <workspace>/.lybra/enrollments.json(仅 gate 侧,0600)。
  - 格式: {code_id: {code, role, instance, status, created_at, expires_at, used_at, revoked_at}}
  - 状态: pending(未使用) / used(已兑换) / revoked(已吊销) / expired(已过期)

Red lines:
  - Token 明文永不进日志/输出/argv。
  - 注册码签发需 owner 授权(owner-gated)。
  - 兑换是公开端点(不需要已有 token),但需校验 code 有效性。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

EnrollmentStatus = Literal["pending", "used", "revoked", "expired"]


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


def mark_enrollment_used(workspace_root: str | Path, code: str) -> dict[str, Any]:
    """标记注册码为已使用。
    
    Returns:
        更新后的记录(含 code_id)
    
    Raises:
        ValueError: 如果 code 不是 pending 状态
    """
    status, record = get_enrollment_status(workspace_root, code)
    if status != "pending":
        raise ValueError(f"Enrollment code is {status}, cannot use")
    if not record:
        raise ValueError("Enrollment code not found")
    
    code_id = record["code_id"]
    enrollments = _load_enrollments(workspace_root)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    enrollments[code_id]["status"] = "used"
    enrollments[code_id]["used_at"] = now.isoformat().replace("+00:00", "Z")
    _save_enrollments(workspace_root, enrollments)
    
    root = _workspace_root_path(workspace_root)
    _append_enrollment_trail(
        root,
        action="use",
        code_id=code_id,
        role=record["role"],
        instance=record.get("instance"),
        by="(agent-exchange)",
        reason="enrollment code exchanged for token",
    )
    
    return {**enrollments[code_id], "code_id": code_id}


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
            "created_at": rec.get("created_at"),
            "expires_at": rec.get("expires_at"),
            "used_at": rec.get("used_at"),
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
