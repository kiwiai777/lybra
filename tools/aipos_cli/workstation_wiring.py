"""AIPOS-F54 — 工位可启动最小集(bootstrap minimum)单源实现。

enroll 一次性落齐"可启动最小集"(缺任何一项, 新工位起不来):
  ① .pi/ 接线(settings.json + extensions/{claim.ts, lybra-loop.ts} + skills/<name>)
  ② .lybra/role#owner_policy_ref(从门侧生效 owner_autonomy_policy 信封推导)
  ③ .lybra/connection.json#lybra_bin(指向实际部署位)
  ④ AGENTS.md 章程占位(正式内容由 /lybra sync 分发拉齐)

单源纪律(卡面锚点):
  - role→skills 映射 = schema/roles.schema.json tool_package(按 role_class 取, 禁代码硬编码角色名)
  - 最小集清单 = schema/distribution.schema.json minimum_bootable_set(缺项逐项点名)
  - 信封判定 = tools/aipos_cli/autonomy_policy.py normalize(复用, 禁第二份信封解析)
  - 接线规格 = 卡面 Owner 裁定(2026-08-28 项目顾问逆向+顾问实测复核):
      settings.json 最小配置禁写 defaultModel、禁用 extensions 数组当加载清单;
      claim.ts = 相对软链;lybra-loop.ts = 真实转发文件(多文件扩展经 symlink 丢兄弟模块);
      skills/<name> = 逐技能软链(按角色类分配子集)。

seed_only 语义(F27):已存在则跳过并出声, 绝不覆盖用户定制。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 接线规格常量(卡面 Owner 裁定原文, 禁再摸索)
# ---------------------------------------------------------------------------

#: settings.json 最小配置:禁写 defaultModel;禁用 extensions 数组当加载清单(pi 自动发现)。
SETTINGS_TEMPLATE: dict[str, Any] = {
    "defaultProvider": "kiwiai",
    "enableSkillCommands": True,
    "skills": [],
}

#: lybra-loop.ts 必须是真实转发文件(多文件扩展经 symlink 挂载会丢兄弟模块)。
LOOP_WRAPPER_TS = (
    "// AIPOS-R3: 挂载包装指向分发落点(由 gate 分发器写入 _distributed/)\n"
    "// 真实文件非 symlink:pi 扩展加载器按 symlink 所在位置解析相对导入,\n"
    "// 多文件扩展经文件 symlink 挂载会丢兄弟模块。包装文件以自身真实路径转发,\n"
    "// 兄弟导入在分发落点真实目录内解析。\n"
    'export { default } from "../../../_distributed/extensions/lybra-loop/lybra-loop.ts";\n'
)

#: claim.ts = 相对软链(工位/.pi/extensions/claim.ts → 仓库根/_shared/extensions/claim.ts)。
CLAIM_SYMLINK_TARGET = "../../../_shared/extensions/claim.ts"

#: skills/<name> = 逐技能相对软链(工位/.pi/skills/<name> → 仓库根/_distributed/skills/<name>)。
SKILL_SYMLINK_TEMPLATE = "../../../_distributed/skills/{name}"

#: AGENTS.md 占位(正式章程走 /lybra sync 分发;此处仅保证文件存在可读)。
AGENTS_PLACEHOLDER = (
    "# (章程占位 — AIPOS-F54)\n\n"
    "本文件由 enroll 落占位; 正式角色章程由 `/lybra sync` 从分发单源拉齐。\n"
    "下一步: 在本工位运行 /lybra sync 然后 /reload。\n"
)

#: 无 harness 循环的角色类(不落 .pi 接线; owner/copilot 等不入循环)。
LOOP_ROLE_CLASSES = ("executor", "auditor", "advisor")


# ---------------------------------------------------------------------------
# role_class 解析(单源: token_entry.role_class 优先, 否则 builtin 注册表)
# ---------------------------------------------------------------------------

def resolve_role_class(role: str, token_entry: dict[str, Any] | None = None) -> str:
    """解析角色类:自定义角色按 token 携带的 role_class 取 builtin 类(与 F22D/F44D-A 同源)。"""
    rc = str((token_entry or {}).get("role_class") or "").strip()
    if rc:
        return rc
    from tools.schema_loader import get_role_spec

    spec = get_role_spec(role)
    return str((spec or {}).get("role_class") or role or "").strip()


def load_role_skills(role_class: str) -> list[str] | None:
    """按角色类取 skills 集合(单源 roles.schema tool_package; 无包角色返回 None)。

    自定义角色按 role_class 取 builtin 类的集合 —— 与卡面⑰一致:
    auditor 类无 finalize-slice、有 audit-independent-evidence。
    """
    from tools.schema_loader import load_schema

    roles_schema = load_schema("roles")
    for spec in roles_schema.get("roles", []):
        if str(spec.get("role_class") or "") == role_class and spec.get("tool_package"):
            skills = list((spec["tool_package"] or {}).get("skills") or [])
            if skills:
                return skills
    return None


# ---------------------------------------------------------------------------
# ② owner_policy_ref 推导(单源: 治理仓 5_tasks/policies 信封工件)
# ---------------------------------------------------------------------------

def derive_effective_owner_policy_ref(
    governance_root: Path | str | None,
    *,
    role: str,
    agent_instance: str | None = None,
    now: datetime | None = None,
) -> tuple[str | None, str]:
    """推导当前生效的 owner_autonomy_policy 信封 ID(写入 .lybra/role#owner_policy_ref)。

    判定复用 autonomy_policy.normalize_policy 字段语义(禁第二份信封解析):
      mode=PreAuthorized + status=active + approved_by_owner + 时间窗内 +
      agent_or_role 覆盖 {role, agent_instance} + max_tasks>0。
    多信封命中时确定性择一:实例精确匹配 > 角色匹配, 同级取 active_from 最新。
    推导不出返回 (None, 原因)——调用方按角色类决定报错带路或仅告警(禁静默留空)。
    """
    if not governance_root:
        return None, "无 governance_root(自包含码未携带且 connection.json 未声明), 无法推导信封"
    root = Path(governance_root).expanduser()
    policies_dir = root / "5_tasks" / "policies"
    if not policies_dir.is_dir():
        return None, f"信封目录不存在: {policies_dir}"

    from tools.aipos_cli.autonomy_policy import normalize_policy
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    now = now or datetime.now(timezone.utc)
    identity = {str(role or "").strip(), str(agent_instance or "").strip()}
    identity.discard("")

    candidates: list[tuple[int, str, str]] = []  # (优先级, active_from, policy_id)
    reasons: list[str] = []
    for path in sorted(policies_dir.glob("pol_*.md")):
        try:
            metadata, _body, _warn = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except OSError as exc:
            reasons.append(f"{path.name}: 读取失败 {exc}")
            continue
        policy = normalize_policy(metadata if isinstance(metadata, dict) else {})
        if policy is None:
            continue
        covered = str(policy.get("agent_or_role") or "").strip()
        if policy.get("mode") != "PreAuthorized":
            continue
        if policy.get("status") != "active" or not policy.get("approved_by_owner"):
            reasons.append(f"{policy['policy_id']}: status={policy.get('status')} 非 active/未获 owner 批")
            continue
        try:
            active_from = datetime.fromisoformat(str(policy.get("active_from")).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(policy.get("expires_at")).replace("Z", "+00:00"))
        except ValueError:
            reasons.append(f"{policy['policy_id']}: 时间窗不可解析")
            continue
        if not (active_from <= now < expires_at):
            reasons.append(f"{policy['policy_id']}: 时间窗外({active_from.date()}~{expires_at.date()})")
            continue
        if not covered or covered not in identity:
            continue
        if int(policy.get("max_tasks") or 0) <= 0:
            reasons.append(f"{policy['policy_id']}: max_tasks<=0")
            continue
        priority = 2 if covered == str(agent_instance or "").strip() and agent_instance else 1
        candidates.append((priority, str(policy.get("active_from") or ""), policy["policy_id"]))

    if not candidates:
        detail = f"; {'; '.join(reasons[:3])}" if reasons else ""
        return None, f"无覆盖角色 {role}(实例 {agent_instance or '-'})的生效 PreAuthorized 信封{detail}"
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2], "matched"


# ---------------------------------------------------------------------------
# ③ lybra_bin 推导(指向实际部署位)
# ---------------------------------------------------------------------------

def resolve_deployed_lybra_bin() -> str | None:
    """推导本机 lybra CLI 部署位:运行中的 bin 本身优先(跟随 symlink 到部署位), 否则探测代码仓 .deploy/current。

    AIPOS-F54-fix1: 跟随 symlink —— sys.argv[0] 可能是外部符号链接,
    实际部署位在 .deploy/current/bin/lybra。跟随后返回真实路径。
    禁硬编码任何绝对路径字面量。
    """
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 and argv0.name == "lybra" and argv0.is_file():
        # Follow symlink to actual deployment location
        resolved = argv0.resolve()
        if resolved.is_file():
            return str(resolved)
        return str(argv0.resolve()) if argv0.is_absolute() else str(argv0.absolute())
    repo_root = Path(__file__).resolve().parents[2]
    probe = repo_root / ".deploy" / "current" / "bin" / "lybra"
    if probe.is_file():
        return str(probe.resolve())
    return None


# ---------------------------------------------------------------------------
# ① .pi 接线 + ④ AGENTS.md 占位(seed_only: 已存在则跳过并出声)
# ---------------------------------------------------------------------------

def _seed_file(path: Path, content: str) -> str:
    if path.exists() or path.is_symlink():
        return "skipped(existing)"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "created"


def _seed_symlink(path: Path, target: str) -> str:
    if path.exists() or path.is_symlink():
        return "skipped(existing)"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    return "created(dangling)" if not path.exists() else "created"


def _seed_charter(workspace_root: Path, role: str, role_class: str) -> tuple[str, str]:
    """AGENTS.md 占位:优先从部署仓 agents/roles/<builtin>/AGENTS.md 取正式章程, 否则落占位文本。"""
    path = workspace_root / "AGENTS.md"
    if path.exists():
        return "skipped(existing)", ""
    repo_root = Path(__file__).resolve().parents[2]
    source = repo_root / "agents" / "roles" / role_class / "AGENTS.md"
    if source.is_file():
        path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return "created(from-distribution)", str(source)
    path.write_text(AGENTS_PLACEHOLDER, encoding="utf-8")
    return "created(placeholder)", ""


def materialize_pi_wiring(
    workspace_root: Path,
    *,
    role: str,
    role_class: str,
) -> dict[str, Any]:
    """幂等落 .pi 接线 + AGENTS.md 占位(seed_only, 已存在跳过, 禁覆盖用户定制)。

    返回逐项落盘报告(created/skipped + 软链目标是否存在), 供验收①⑯⑰取证。
    无 harness 循环的角色类不落接线(仅章程占位)。
    """
    report: dict[str, Any] = {"role": role, "role_class": role_class, "items": {}}
    items = report["items"]

    charter_status, charter_source = _seed_charter(workspace_root, role, role_class)
    items["AGENTS.md"] = {"status": charter_status, "source": charter_source or None}

    if role_class not in LOOP_ROLE_CLASSES:
        report["note"] = f"role_class={role_class} 无 harness 循环, 不落 .pi 接线"
        return report

    pi = workspace_root / ".pi"
    items["settings.json"] = {"status": _seed_file(pi / "settings.json", json.dumps(SETTINGS_TEMPLATE, indent=2) + "\n")}
    claim_status = _seed_symlink(pi / "extensions" / "claim.ts", CLAIM_SYMLINK_TARGET)
    items["extensions/claim.ts"] = {
        "status": claim_status,
        "symlink": True,
        "target": CLAIM_SYMLINK_TARGET,
        "target_exists": (pi / "extensions" / "claim.ts").exists(),
    }
    items["extensions/lybra-loop.ts"] = {
        "status": _seed_file(pi / "extensions" / "lybra-loop.ts", LOOP_WRAPPER_TS),
        "symlink": False,
        "target_exists": (pi / "extensions" / "lybra-loop.ts").exists(),
    }

    skills = load_role_skills(role_class) or []
    skills_report: dict[str, Any] = {}
    for name in skills:
        link = pi / "skills" / name
        status = _seed_symlink(link, SKILL_SYMLINK_TEMPLATE.format(name=name))
        skills_report[name] = {"status": status, "target_exists": link.exists()}
    items["skills"] = {"count": len(skills), "links": skills_report}

    # AIPOS-F58: 把 .pi/ 接线路径登记进 .git/info/exclude(防 `git stash -u` 连坐抹掉)
    from tools.aipos_cli.git_exclude import collect_wiring_exclude_paths, register_git_exclude
    wiring_exclude_paths = collect_wiring_exclude_paths(workspace_root)
    if wiring_exclude_paths:
        git_exclude_report = register_git_exclude(workspace_root, wiring_exclude_paths)
        report["git_exclude"] = git_exclude_report

    return report


# ---------------------------------------------------------------------------
# ⑮ 可启动最小集校验(清单单源 config.schema#minimum_bootable_set, 缺项逐项点名)
# ---------------------------------------------------------------------------

def minimum_bootable_set_items() -> list[dict[str, str]]:
    """读 distribution.schema#minimum_bootable_set.items 声明(单源; 禁代码内写第二份清单)。

    声明落位 = 分发清单(卡面⑮"config.schema 或分发清单择一"; 本卡 output_target 声明
    schema/distribution.schema.json, 故落此件)。

    AIPOS-F54-fix1: 追加 workspace_root_match 检查项(代码内补铸, schema 未更新前
    由代码保证自检覆盖; schema 更新后此处自动去重)。
    """
    from tools.schema_loader import load_schema

    dist = load_schema("distribution")
    section = dist.get("minimum_bootable_set")
    items = section.get("items") if isinstance(section, dict) else section
    items = items or []
    result = [dict(item) for item in items if isinstance(item, dict) and item.get("name")]
    # AIPOS-F54-fix1: 补铸 workspace_root_match(若 schema 尚未声明则追加; 已声明则跳过)
    if not any(str(i.get("kind") or "") == "workspace_root_match" for i in result):
        result.append({
            "name": "connection.json#workspace_root==governance_root",
            "kind": "workspace_root_match",
            "container": ".lybra/connection.json",
            "path": ".lybra/connection.json",
            "description": "workspace_root must equal governance_root (single source: code governance_root; harness root must not leak in). Missing or mismatched = missing item.",
        })
    return result


def verify_minimum_bootable_set(workspace_root: Path) -> dict[str, Any]:
    """逐项核对可启动最小集; 缺项逐项点名(禁"少一个键整个起不来但不知道少哪个")。

    AIPOS-F54-fix1: 新增 workspace_root_match 检查 —— workspace_root 须等于 governance_root
    (单源: 码内治理根)。harness root 混入 = 缺项。
    """
    root = Path(workspace_root)
    checks: list[dict[str, Any]] = []
    for item in minimum_bootable_set_items():
        name = str(item.get("name") or "")
        kind = str(item.get("kind") or "file")
        path = root / str(item.get("path") or "")
        if kind == "symlink_dir":
            present = path.is_dir()
        elif kind == "json_key":
            container = root / str(item.get("container") or "")
            key = str(item.get("key") or "")
            try:
                data = json.loads(container.read_text(encoding="utf-8"))
                value = data.get(key) if isinstance(data, dict) else None
                present = bool(value)
                # ⑭ 值须指向实际存在的文件(如 lybra_bin 悬空 = 缺项, sync 侧另有探测回落)
                if present and item.get("value_must_exist") and isinstance(value, str):
                    present = Path(value).expanduser().is_file()
            except (OSError, json.JSONDecodeError):
                present = False
        elif kind == "workspace_root_match":
            # AIPOS-F54-fix1: workspace_root 须等于 governance_root(单源: 码内治理根)
            container = root / str(item.get("container") or "")
            try:
                data = json.loads(container.read_text(encoding="utf-8"))
                ws = str(data.get("workspace_root") or "").strip()
                gov = str(data.get("governance_root") or "").strip()
                if not ws or not gov:
                    present = False
                else:
                    # 规范化比较(消除 symlink/trailing slash 差异)
                    present = Path(ws).resolve() == Path(gov).resolve()
            except (OSError, json.JSONDecodeError):
                present = False
        else:
            present = path.is_file() or path.is_symlink()
        checks.append({"name": name, "path": str(item.get("path") or ""), "present": present})
    missing = [c["name"] for c in checks if not c["present"]]
    return {"ok": not missing, "missing": missing, "checks": checks}
