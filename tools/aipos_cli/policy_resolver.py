"""AIPOS-340F1 S6 / AIPOS-343 — 从工作区读取活跃策略信封。

扫描治理仓 5_tasks/policies/ 下未过期未耗尽的策略,返回最新活跃信封。
AIPOS-343: 工作区无关 —— 不假设文件名前缀,通过 frontmatter 字段匹配角色。
"""
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import yaml
from tools.schema_constants import RecordType


# role 参数值 → agent_or_role 字段中应包含的子串
_ROLE_MATCH_SUBSTRINGS: dict[str, list[str]] = {
    "exec": ["exec"],
    "audit": ["audit"],
}


def _builtin_class_candidates(role: str) -> set[str]:
    """AIPOS-F32: role 参数(exec/audit 或内建全名)对应的内建类名集合。

    从 roles 注册表单源(schema roles.schema.json,经 schema_loader)推导,
    本文件不自建 role→class 映射(防碎片化;与 F26C 分发类展开同一单源)。
    例: role="exec" → {"executor"};role="audit" → {"auditor"};
    role="executor"(全名) → {"executor"}。加载失败 → 空集合(退回直配语义)。
    """
    clean = str(role or "").strip().lower()
    if not clean:
        return set()
    try:
        from tools.schema_loader import get_all_role_names
        names = {str(n).strip().lower() for n in get_all_role_names()}
    except Exception:
        return set()
    return {n for n in names if n == clean or n.startswith(clean)}


def _parse_policy_frontmatter(content: str) -> dict[str, Any] | None:
    """Parse YAML frontmatter from a policy markdown file. Returns None on failure."""
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1])
        return meta if isinstance(meta, dict) else None
    except yaml.YAMLError:
        return None


def _is_policy_active_and_valid(meta: dict[str, Any], now: datetime) -> bool:
    """Check if a policy frontmatter indicates an active, non-expired policy."""
    if meta.get("status") != "active":
        return False
    expires_at = meta.get("expires_at")
    if expires_at:
        try:
            expires_dt = datetime.fromisoformat(str(expires_at).replace('Z', '+00:00'))
            if now > expires_dt:
                return False
        except (ValueError, TypeError):
            return False
    return True


def _policy_matches_role(
    meta: dict[str, Any],
    role: str,
    custom_roles: dict[str, dict[str, str]] | None = None,
) -> bool:
    """Check if a policy's agent_or_role field matches the requested role.

    AIPOS-343: matching is by frontmatter content, NOT filename.
    The agent_or_role field (e.g. "exec.lybra.kiwiai-dev") contains the role
    as a prefix component. We check if any of the role's expected substrings
    appear as a dot-separated component.

    AIPOS-F32: 自定义角色按 roles 注册表所属内建类匹配(与 F26C 分发类展开同一修法、
    同一注册表单源), 而非点分量对固定词 exec/audit。例: agent_or_role
    "hbj-coder.chris-huibojin.kiwiai-dev" 在注册表 {"hbj-coder": {"class":
    "executor"}} 下匹配 role="exec"。既有直配语义(exec↔exec)原样保留;
    custom_roles 未提供/为空时行为与旧版完全一致。

    AIPOS-F32B: 注册表 = 门注册表(connection.json tokens, 与凭据同源), 经
    custom_roles.load_custom_roles 统一加载(与 F26C 分发类展开同一加载函数)。
    本函数的 custom_roles 参数**仅限测试注入**; 生产调用方(find_active_policy)
    一律默认从注册表取, 禁调用方自喂注册表变体。
    """
    agent_or_role = str(meta.get("agent_or_role") or "").strip()
    if not agent_or_role:
        # Fallback: check legacy "role" field (used in some test policies)
        legacy_role = str(meta.get("role") or "").strip()
        return legacy_role == role

    expected_substrings = _ROLE_MATCH_SUBSTRINGS.get(role, [role])
    # Split agent_or_role by dots and check if any component matches
    components = {c.strip().lower() for c in agent_or_role.split(".")}
    for substr in expected_substrings:
        if substr.lower() in components:
            return True

    # AIPOS-F32: custom-role class match. Each dot component is looked up in the
    # gate custom-roles registry ({name: {"class": builtin_class}}, loaded from the
    # GATE registry — connection.json tokens via custom_roles.load_custom_roles,
    # the same single loader F26C's distribution class expansion reads).
    # A component that IS a registered custom role matches when its registered class
    # is one of the requested role's builtin classes. Registry is owner-gated, so this
    # grants nothing beyond what the gate already registered (anti-escalation).
    if custom_roles:
        target_classes = _builtin_class_candidates(role)
        if target_classes:
            for comp in components:
                entry = custom_roles.get(comp)
                if isinstance(entry, dict):
                    cls = str(entry.get("class") or "").strip().lower()
                    if cls and cls in target_classes:
                        return True
    return False


def find_active_policy(
    workspace_root: Path,
    role: str,
    policy_type: str = "dev",
) -> str | None:
    """从治理仓读取活跃策略信封。

    AIPOS-343: workspace-agnostic. Scans ALL .md files in the policies directory
    and matches by frontmatter fields (status, expires_at, agent_or_role),
    NOT by filename pattern. Works for any project workspace (lybra, kiwiaiagency, etc.).

    Args:
        workspace_root: 治理仓根目录(任意项目工作区)
        role: "exec" | "audit"
        policy_type: "dev" | "audit" (kept for API compatibility; role is the primary filter)

    Returns:
        策略 ID(如 pol_lybra_dev_7 或 pol_agency_1),或 None(无活跃策略)
    """
    policies_dir = workspace_root / "5_tasks" / "policies"
    if not policies_dir.exists():
        return None

    # AIPOS-F32B: 门级自定义角色注册表(connection.json tokens 单源; 与 F26C 分发类
    # 展开同一加载函数 custom_roles.load_custom_roles)。只加载一次, 防御式:
    # 注册表不可得 → 退回旧版直配语义。
    custom_roles: dict[str, dict[str, str]] | None = None
    try:
        from tools.aipos_cli.custom_roles import load_custom_roles
        custom_roles = load_custom_roles(workspace_root)
    except Exception:
        custom_roles = None

    # AIPOS-343: scan ALL .md files, not a hardcoded filename pattern
    policy_files = sorted(policies_dir.glob("*.md"), reverse=True)  # 最新文件优先

    now = datetime.now(timezone.utc)

    for policy_file in policy_files:
        try:
            content = policy_file.read_text(encoding="utf-8")
            meta = _parse_policy_frontmatter(content)
            if meta is None:
                continue

            # Must be an autonomy policy record
            record_type = str(meta.get("record_type") or "").strip()
            if record_type and record_type != RecordType.OWNER_AUTONOMY_POLICY:
                continue

            if not _is_policy_active_and_valid(meta, now):
                continue

            if not _policy_matches_role(meta, role, custom_roles=custom_roles):
                continue

            policy_id = meta.get("policy_id")
            if policy_id:
                return str(policy_id)
        except Exception:
            continue

    return None
