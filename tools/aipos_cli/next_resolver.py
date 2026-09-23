"""AIPOS-F71 — lybra next 推导核。

唯一实现:合并 turn-advancer(AIPOS-340)与 next-step(AIPOS-R7A)为单一推导器。
推导只认记录(queue 目录 + records 三查 + 卡 frontmatter),禁读日志/会话残留。
状态不明 → 输出"不可推导 + 缺哪份记录 + 建议动作",禁猜。
token 值永不出现在输出。

推导核从 transitions.schema.json(节点与迁移)+ verbs.schema.json(动词参数配方)
派生含全参数的可照抄命令。命令指向产品 CLI 薄壳(lybra queue claim/return/close
--confirm 等),零本地状态变更逻辑。

两种模式:
- 无参:项目级扫描(队列 → 当前最小待办卡 + 所处节点 + 该谁动)
- --task-id:单卡模式

项目无关:推导全由声明 + 工作区推导,不写死项目名。

AIPOS-F71 返工第5件:所有治理路径经 schema_loader 单一读取口,禁手拼路径字面量。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.schema_loader import resolve_governance_path

# 产品仓根(schema 所在地)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_governance_path_with_relative(key: str, governance_root: Path) -> Path:
    """解析治理路径,处理 relative_to 链。
    
    如 queue 相对于 tasks_root,需递归解析:
    tasks_root → 5_tasks/
    queue → tasks_root + queue/ = 5_tasks/queue/
    """
    from tools.schema_loader import get_governance_path, resolve_governance_path
    
    entry = get_governance_path(key, REPO_ROOT)
    relative_to = entry.get("relative_to")
    
    # governance_root 是终点,直接返回根目录
    if key == "governance_root" or not relative_to:
        # 无 relative_to 或到达根,直接用 path
        rel_path = str(entry.get("path", "")).strip().strip("/")
        if not rel_path:
            # governance_root 没有 path,返回根本身
            return governance_root
        return governance_root / rel_path
    else:
        # 递归解析父路径
        parent_path = _resolve_governance_path_with_relative(relative_to, governance_root)
        # 拼接当前路径
        rel_path = str(entry.get("path", "")).strip().strip("/")
        return parent_path / rel_path

# ---------------------------------------------------------------------------
# AIPOS-F73D: 声明读取口(transitions 节点 / 产品仓根 / worktree 落点 / 驱动方身份)——全部读声明, 禁写死
# ---------------------------------------------------------------------------

_RETURN_PLACEHOLDER_PREFIX = "(待填写"  # record_writer.build_return_skeleton_markdown 骨架占位符前缀(N2.artifact.readiness)


def _transition_node(node_id: str) -> dict[str, Any]:
    """读 transitions.schema.json nodes[<id>](产品根单一源)。缺失 = 声明缺失, 抛 SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    nodes = load_schema("transitions", REPO_ROOT).get("nodes") or {}
    node = nodes.get(node_id) if isinstance(nodes, dict) else None
    if not isinstance(node, dict):
        raise SchemaLoadError(f"transitions.schema.json nodes.{node_id} 未声明")
    return node


def _card_repo_root(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None,
                    *, allow_governance_root: bool = True) -> Path:
    """AIPOS-F78C 件②: 卡所在产品仓 = workspace_config.resolve_card_repo(唯一解析: 卡 lane.repo → project.json repos 清单
    → code_repo 别名 → 治理根)。card_frontmatter 缺则按 task_id 读卡。解析不到 = CardRepoUnresolved(调用方转不可派生/拒, fail-closed)。
    allow_governance_root=False(finalize 派生): 无声明时不把治理根当产品仓(F73D 前置一②)。"""
    from tools.aipos_cli.workspace_config import resolve_card_repo

    fm = card_frontmatter
    if fm is None:
        task_path, _ = _find_task_in_queue(workspace_root, task_id)
        fm = _read_frontmatter(task_path) if task_path else {}
    return resolve_card_repo(workspace_root, {**fm, "task_id": str(fm.get("task_id") or task_id)},
                             allow_governance_root=allow_governance_root)


def _resolve_worktree_root(workspace_root: Path, code_repo: Path) -> Path:
    """AIPOS-F73D 前置三: worktree 落点读声明——config.schema configuration_sources.workspace_config.schema.worktree_root
    (default `{code_repo}/.worktrees`, 产品仓 .gitignore 已声明), 治理根 .lybra/config.json 的 worktree_root 可覆盖。禁写死。"""
    from tools.schema_loader import load_schema
    from tools.aipos_cli.workspace_config import CONFIG_RELATIVE_PATH, load_workspace_config

    decl = (
        load_schema("config", REPO_ROOT)
        .get("configuration_sources", {})
        .get("workspace_config", {})
        .get("schema", {})
        .get("worktree_root", {})
    )
    template = str(decl.get("default") or "").strip()
    if not template:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("config.schema.json workspace_config.schema.worktree_root.default 未声明")
    override = ""
    cfg_path = workspace_root / CONFIG_RELATIVE_PATH
    if cfg_path.is_file():
        try:
            override = str(load_workspace_config(cfg_path).get("worktree_root") or "").strip()
        except (OSError, ValueError) as exc:
            import sys

            print(f"Warning: {cfg_path} unreadable, using declared default worktree_root: {exc}", file=sys.stderr)
    chosen = override or template
    return Path(chosen.replace("{code_repo}", str(code_repo))).expanduser()


def _artifact_ingest_declaration() -> dict[str, Any]:
    """AIPOS-F78 件④: 读 transitions.schema.json 顶层 artifact_ingest(文件候选/必填 frontmatter 的唯一声明)。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("transitions", REPO_ROOT).get("artifact_ingest")
    if not isinstance(decl, dict) or "return" not in decl or "verdict" not in decl:
        raise SchemaLoadError("transitions.schema.json artifact_ingest(return/verdict 落点与必填字段)未声明")
    return decl


def _project_paths(workspace_root: Path) -> dict[str, Any]:
    """AIPOS-F78 件④: 项目落点声明(project.json paths, 缺省=现行路径)——唯一读取口 workspace_config.project_paths。"""
    from tools.aipos_cli.workspace_config import project_paths

    return project_paths(workspace_root)


def _pick_candidate(directory: Path, candidates: list[str], *, ready: Any = None) -> Path | None:
    """按候选顺序取首个存在的文件; 通配候选取 mtime 最新; ready(path) 谓词可选(骨架/无 verdict 不算)。"""
    if not directory.is_dir():
        return None
    for cand in candidates:
        cand = str(cand)
        if any(ch in cand for ch in "*?["):
            matches = sorted((p for p in directory.glob(cand) if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        else:
            matches = [directory / cand] if (directory / cand).is_file() else []
        for path in matches:
            if ready is None or ready(path):
                return path
    return None


def return_artifact_dir(workspace_root: Path, task_id: str) -> Path:
    """执行体 Return 落点目录 = <paths.return_root>/<task_id>(声明驱动, lybra 默认 task_cards/<ID>)。"""
    return Path(_project_paths(workspace_root)["return_root"]) / task_id


def find_return_artifact(workspace_root: Path, task_id: str) -> Path | None:
    """AIPOS-F78 件④: 按项目声明找执行体 Return 文件(候选序读 transitions artifact_ingest.return.return_file_candidates)。"""
    cands = list(_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])
    if not cands:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.return_file_candidates 未声明")
    return _pick_candidate(return_artifact_dir(workspace_root, task_id), cands)


def _return_artifact_path(workspace_root: Path, task_id: str) -> Path:
    """执行体产物路径: 已落盘的 Return 文件; 未落盘时返回声明位默认文件(首个非通配候选)。"""
    found = find_return_artifact(workspace_root, task_id)
    if found is not None:
        return found
    cands = list(_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])
    first = next((str(c) for c in cands if not any(ch in str(c) for ch in "*?[")), "RETURN.md")
    return return_artifact_dir(workspace_root, task_id) / first


def executor_artifact_watch(workspace_root: Path, task_id: str) -> tuple[Path, list[str]]:
    """AIPOS-F78: loop 等待执行体产物的 (watch 根, 相对 glob 列表)——落点读声明; 声明根在治理根外时以落点根为 watch 根。"""
    directory = return_artifact_dir(workspace_root, task_id)
    cands = [str(c) for c in (_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])]
    try:
        rel = directory.resolve().relative_to(Path(workspace_root).resolve())
        return Path(workspace_root), [str(rel / c) for c in cands]
    except ValueError:
        return directory.parent, [f"{directory.name}/{c}" for c in cands]


def verdict_artifact_dir(workspace_root: Path, audit_task_id: str) -> Path:
    """审计报告落点目录 = <paths.verdict_root>/<audit_task_id>(声明驱动, lybra 默认 task_cards/<ID>R)。"""
    return Path(_project_paths(workspace_root)["verdict_root"]) / audit_task_id


def _audit_report_candidates(workspace_root: Path, audit_task_id: str) -> list[Path]:
    """审计体产物候选: 落点根读项目声明(verdict_root), 文件候选读 transitions artifact_ingest.verdict.verdict_file_candidates
    (与 N4.audit_report.location_candidates 同序: RETURN.md 优先, 回退 audit_report.md)。"""
    directory = verdict_artifact_dir(workspace_root, audit_task_id)
    cands = list(_artifact_ingest_declaration()["verdict"].get("verdict_file_candidates") or [])
    out: list[Path] = []
    for cand in cands:
        cand = str(cand)
        if any(ch in cand for ch in "*?["):
            if directory.is_dir():
                out.extend(sorted((p for p in directory.glob(cand) if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True))
        else:
            out.append(directory / cand)
    return out


def audit_report_artifact_path(workspace_root: Path, audit_task_id: str) -> Path:
    """AIPOS-F66B 件③: 审计报告落点的唯一读取口 = 已落盘的报告文件; 未落盘时返回声明位默认文件
    (<paths.verdict_root>/<审计卡ID>/<首个非通配候选>)。与 _return_artifact_path 同构:
    落点根读项目声明 verdict_root, 文件候选读 transitions artifact_ingest.verdict.verdict_file_candidates。
    派生审计卡文案 / card render / ingest 全部经此, 禁写死 task_cards/<被审卡ID>/。"""
    for path in _audit_report_candidates(workspace_root, audit_task_id):
        if path.is_file():
            return path
    cands = list(_artifact_ingest_declaration()["verdict"].get("verdict_file_candidates") or [])
    first = next((str(c) for c in cands if not any(ch in str(c) for ch in "*?[")), None)
    if first is None:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.verdict_file_candidates 无非通配候选")
    return verdict_artifact_dir(workspace_root, audit_task_id) / first


def _finalize_mode(workspace_root: Path) -> str:
    """AIPOS-F78B 件②: finalize 场地声明(project.json paths.finalize_mode, config.schema 声明表; 缺省 internal)。"""
    return str(_project_paths(workspace_root).get("finalize_mode") or "internal")


def _finalization_ingest_declaration() -> dict[str, Any]:
    """AIPOS-F78B 件②: transitions artifact_ingest.finalization(FINALIZE 卡 ID 规则 + Return 必填字段)唯一声明; 缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("transitions", REPO_ROOT).get("artifact_ingest") or {}).get("finalization")
    if not isinstance(decl, dict) or not isinstance(decl.get("finalize_task_id"), dict) or not decl.get("required_frontmatter"):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.finalization(FINALIZE 卡 ID 规则/Return 必填字段)未声明")
    return decl


def finalize_task_id_for(task_id: str, task_fm: dict[str, Any]) -> str:
    """AIPOS-F78B 件②: 外部 FINALIZE 卡 ID = 卡面 <card_field> 声明优先, 缺省 <task_id><suffix>(规则读 artifact_ingest.finalization.finalize_task_id)。"""
    rule = _finalization_ingest_declaration()["finalize_task_id"]
    declared = str(task_fm.get(str(rule.get("card_field") or "finalize_task_id")) or "").strip()
    if declared:
        return declared
    suffix = str(rule.get("suffix") or "").strip()
    if not suffix:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.finalization.finalize_task_id.suffix 未声明")
    return f"{task_id}{suffix}"


def missing_finalization_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    """外部 FINALIZE Return 缺失的必填 frontmatter(声明: artifact_ingest.finalization.required_frontmatter)。"""
    missing = []
    for key in [str(k) for k in _finalization_ingest_declaration()["required_frontmatter"]]:
        value = str(frontmatter.get(key) or "").strip()
        if not value or value.startswith(_RETURN_PLACEHOLDER_PREFIX) or value.startswith("<"):
            missing.append(key)
    return missing


def invalid_finalization_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    """外部 FINALIZE Return 不合规项(唯一判据, 推导核与 artifact ingest 共用): 必填缺项 + deploy_status 不在 N5.record.deploy_status.values。"""
    problems = [f"FINALIZE Return frontmatter 缺 {k}" for k in missing_finalization_frontmatter(frontmatter)]
    values = [str(v) for v in (_transition_node("N5").get("record", {}).get("deploy_status", {}).get("values") or [])]
    status = str(frontmatter.get("deploy_status") or "").strip()
    if status and values and status not in values:
        problems.append(f"FINALIZE Return deploy_status={status!r} 不在声明值域 {values}(transitions N5.record.deploy_status.values)")
    return problems


def _derive_external_finalize(
    workspace_root: Path,
    task_id: str,
    fm: dict[str, Any],
    *,
    claimer: str,
    verdict_result: str,
) -> dict[str, Any]:
    """AIPOS-F78B 件②: finalize_mode=external 的 N4→N5——不派生 `lybra finalize`, 等外部 FINALIZE 卡的 Return 落盘
    (落点 = return_root/<finalize_task_id>, 与执行体 Return 同一候选序), 齐则派生 `lybra artifact ingest --task-id <被审卡>` 铸 finalization 记录。"""
    base = {"task_id": task_id, "current_node": "audit_verdict", "current_state": "claimed", "verb": "lybra_finalize", "command": ""}
    fin_id = finalize_task_id_for(task_id, fm)
    ret = find_return_artifact(workspace_root, fin_id)
    if ret is None:
        cands = ", ".join(str(c) for c in (_artifact_ingest_declaration()["return"].get("return_file_candidates") or []))
        return {
            **base,
            "derivable": False,
            "triggered_by": "executor",
            "missing_records": [f"FINALIZE 卡 {fin_id} 的 Return({return_artifact_dir(workspace_root, fin_id)}/ 候选: {cands})"],
            "suggested_action": f"等待外部 FINALIZE 卡 {fin_id}(merge/push/部署在产品仓所在机执行)交回 Return, frontmatter 含 "
                                f"{', '.join(str(k) for k in _finalization_ingest_declaration()['required_frontmatter'])}",
            "notes": f"N4→N5(external): 裁决 {verdict_result}, finalize_mode=external, 等 FINALIZE Return(声明: transitions N5.finalize_mode)",
            "action": {"type": "await_artifact", "card": fin_id, "kind": "finalize_return"},
        }
    problems = invalid_finalization_frontmatter(_read_frontmatter(ret))
    if problems:
        return {
            **base,
            "derivable": False,
            "triggered_by": "executor",
            "missing_records": problems,
            "suggested_action": f"FINALIZE 卡执行体在 {ret} 的 frontmatter 补齐/修正(声明: transitions artifact_ingest.finalization)",
            "notes": "N4→N5(external): FINALIZE Return 已落盘但不合规, 产品无法铸 finalization 记录(fail-closed)",
            "action": {"type": "artifact_invalid", "card": fin_id, "path": str(ret)},
        }
    if not claimer:
        return _not_derivable_no_claim(task_id, node="audit_verdict", state="claimed", verb="lybra_finalize", triggered_by="advisor",
                                       notes=f"N4→N5(external): 裁决 {verdict_result}, 但无 claim 记录, finalization actor 无据(AIPOS-F73E 件①)")
    return {
        **base,
        "derivable": True,
        "triggered_by": "advisor",
        "command": f"lybra artifact ingest --task-id {task_id} --workspace-root {workspace_root}",
        "missing_records": [],
        "suggested_action": "外部 FINALIZE Return 已落盘, 经产物入口铸 finalization 记录",
        "notes": f"N4→N5(external): 裁决 {verdict_result}, FINALIZE 卡 {fin_id} Return 在 {ret}",
    }


def _driver_envelope_ref(workspace_root: Path, task_id: str, task_fm: dict[str, Any], connection_json: str | None) -> str | None:
    """AIPOS-F78B 件③: 覆盖驱动方身份与本卡的有效 PreAuthorized 信封 policy_id(loop_driver.find_envelope 同一判据); 无 = None
    (派生命令回落 Supervised 形, execute_derived_action 对账务动词 fail-closed exit 5 带申领出口)。"""
    from tools.aipos_cli.loop_driver import find_envelope  # 延迟导入(loop_driver 依赖本模块)

    driver_actor = _driver_actor(workspace_root, connection_json=connection_json)
    if not driver_actor:
        return None
    policy, _reasons = find_envelope(workspace_root, task_id=task_id, task_fm=task_fm, driver_actor=driver_actor,
                                     driver_role=_driver_role_name(workspace_root, connection_json))
    return str(policy.get("policy_id")) if policy else None


def _driver_token_instance(workspace_root: Path, connection_json: str | None = None) -> str:
    """驱动方 token 绑定的实例: connection.json 中 role_class==roles.schema driver.role_class 的 token 的 agent_instance。"""
    import json

    from tools.aipos_cli.two_phase_shell_factory import driver_role_class

    conn = connection_json or _find_connection_json(workspace_root)
    if not conn or not Path(conn).is_file():
        return ""
    try:
        tokens = json.loads(Path(conn).read_text(encoding="utf-8")).get("tokens") or []
    except (OSError, ValueError) as exc:
        import sys

        print(f"Warning: {conn} unreadable, driver instance unresolved: {exc}", file=sys.stderr)
        return ""
    wanted = driver_role_class()
    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        role_class = str(tok.get("role_class") or tok.get("role") or "").strip()
        if role_class == wanted:
            return str(tok.get("agent_instance") or "").strip()
    return ""


def _driver_actor(workspace_root: Path, fallback: str | None = None, *, connection_json: str | None = None) -> str:
    """驱动方身份(AIPOS-F78 前置零②: 禁回退占位 advisor)。

    顺序: ① 治理根 .lybra/role 的 instance(工位声明) → ② connection.json 驱动方 token 绑定的 agent_instance
    → ③ 调用方显式 fallback(仅靶场/显式传入) → 解析不到返回 ""(调用方 fail-closed: 不可推导 + 点名缺项)。
    读失败精确捕获 + warning。
    """
    import json

    role_file = workspace_root / ".lybra" / "role"
    if role_file.is_file():
        try:
            role_data = json.loads(role_file.read_text(encoding="utf-8"))
            instance = str(role_data.get("instance") or "").strip()
            if instance:
                return instance
        except (OSError, ValueError) as exc:
            import sys

            print(f"Warning: {role_file} unreadable, driver actor unresolved from role file: {exc}", file=sys.stderr)
    instance = _driver_token_instance(workspace_root, connection_json)
    if instance:
        return instance
    return str(fallback or "")


DRIVER_ACTOR_MISSING = "驱动方身份(治理根 .lybra/role 的 instance, 或 connection.json 驱动方 token 的 agent_instance)"


def _driver_role_name(workspace_root: Path, connection_json: str | None = None) -> str:
    """AIPOS-F78B 件③: 驱动方的角色名(自定义角色如 chris 的 hbj-advisor)——工位声明 .lybra/role 的 role, 其次 connection.json
    驱动方 token(role_class==driver.role_class)的 role; 解析不到返回 ""(调用方回退角色类 advisor)。信封 agent_or_role 可写角色名。"""
    import json

    from tools.aipos_cli.two_phase_shell_factory import driver_role_class

    role_file = workspace_root / ".lybra" / "role"
    if role_file.is_file():
        try:
            role = str(json.loads(role_file.read_text(encoding="utf-8")).get("role") or "").strip()
            if role:
                return role
        except (OSError, ValueError) as exc:
            import sys

            print(f"Warning: {role_file} unreadable, driver role unresolved from role file: {exc}", file=sys.stderr)
    conn = connection_json or _find_connection_json(workspace_root)
    if not conn or not Path(conn).is_file():
        return ""
    try:
        tokens = json.loads(Path(conn).read_text(encoding="utf-8")).get("tokens") or []
    except (OSError, ValueError):
        return ""
    wanted = driver_role_class()
    for tok in tokens:
        if isinstance(tok, dict) and str(tok.get("role_class") or tok.get("role") or "").strip() == wanted:
            return str(tok.get("role") or "").strip()
    return ""


def _claimer_instance(records: dict[str, Any]) -> str:
    """AIPOS-F73E 件①: 账务动词(return/verdict/finalize/close)的 actor/agent_instance = 该卡 claim 记录的 agent_instance
    (审计卡=审计实例, 执行卡=执行实例); token 仍归驱动方(two_phase_shell_factory.resolve_driver_role_from_connection)。
    唯一实现: 只读 claim 记录, 无记录返回 ""(调用方 fail-closed: 不可推导 + 点名缺项), 禁回退卡面/驱动方实例。
    声明: transitions.schema record_authenticity.submission_identity。"""
    latest_claim = records.get("latest_claim") or {}
    return str(latest_claim.get("agent_instance") or latest_claim.get("actor") or "").strip()


def _claim_record_missing(task_id: str) -> str:
    return f"claim 记录 agent_instance(5_tasks/records/claims/{task_id}/claim_*.md, 账务动词 actor 依据)"


def _not_derivable_no_claim(task_id: str, *, node: str, state: str, verb: str, triggered_by: str, notes: str) -> dict[str, Any]:
    """AIPOS-F73E 件①: 无 claim 记录 → 不可推导(exit 4)带出口。"""
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": node,
        "current_state": state,
        "triggered_by": triggered_by,
        "command": "",
        "verb": verb,
        "missing_records": [_claim_record_missing(task_id)],
        "suggested_action": f"lybra state repair --task-id {task_id} --workspace-root <治理根>(按 records 重建卡态; 仍无 claim 记录则经门认领 lybra queue claim --confirm 铸记录后重推导; 手写记录不算 record_authenticity)",
        "notes": notes,
        # loop 据此硬停 exit 4(与 artifact_invalid 同款), 不把「缺 claim 记录」误当「执行体/审计体还在干活」空等
        "action": {"type": "record_missing", "card": task_id, "record": "claim"},
    }


def _action_type_for_command(command: str) -> str:
    """派生命令 → action_type(唯一映射, next --run 与 lybra loop 共用)。
    先匹配 "queue close" 再匹配 "finalize", 避免 close 命令被误判为 finalize。"""
    if "artifact ingest" in command:
        return "finalize"  # AIPOS-F78B 件②: finalize_mode=external 的 finalize 步 = 产物入口铸 finalization 记录
    if "queue claim" in command:
        return "claim"
    if "queue return" in command:
        return "return"
    if "queue close" in command:
        return "close"
    if "audit-verdict" in command or "audit verdict" in command:
        return "verdict"
    if "audit dispatch" in command:
        return "dispatch"
    if "finalize" in command:
        return "finalize"
    return "unknown"


# ---------------------------------------------------------------------------
# 状态 → 节点映射(读 transitions.schema.json nodes 声明)
# ---------------------------------------------------------------------------

# 队列目录名 → 状态机节点
# 按 transitions.schema.json nodes 定义:
#   N0: publish (draft → pending)
#   N1: claim   (pending → claimed)
#   N2: return  (claimed → returned)
#   N3: audit_dispatch (returned → audit_dispatched)
#   N4: audit_verdict  (audit_dispatched → verdict_issued)
#   N5: finalize (verdict_issued → finalized)
#   N6: close   (finalized → completed)

# 状态推导:从记录推,不从 frontmatter 猜
# queue 目录位置 = 事实;records = 事实;frontmatter = 声明(辅助)


def _read_frontmatter(task_path: Path) -> dict[str, Any]:
    """读取卡 frontmatter(YAML)。读失败精确捕获 + warning, 返回空 dict(不静默)。"""
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    try:
        fm, _, _ = parse_markdown_frontmatter(task_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        import sys

        print(f"Warning: frontmatter unreadable: {task_path}: {exc}", file=sys.stderr)
        return {}
    return fm if isinstance(fm, dict) else {}


def _find_task_in_queue(workspace_root: Path, task_id: str) -> tuple[Path | None, str | None]:
    """AIPOS-F78B 件①: 卡查找唯一实现 = task_loader.find_task_card(frontmatter task_id 精确匹配, 文件名不限);
    本名仅为推导核内的调用点, 不含第二套查找逻辑。多义(AmbiguousTaskCard)向上抛, 由调用方 fail-closed。"""
    from tools.aipos_cli.task_loader import find_task_card

    return find_task_card(workspace_root, task_id)


def _find_latest_record(records_dir: Path, prefix: str) -> dict[str, Any] | None:
    """在 records 子目录中找最新记录(按修改时间)。返回 frontmatter dict 或 None。"""
    if not records_dir.is_dir():
        return None
    files = sorted(records_dir.glob(f"{prefix}_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    return _read_frontmatter(files[0]) or None


def _read_task_records(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """读取任务的全部记录状态。纯读事实,不判活。"""
    records_root = _resolve_governance_path_with_relative("records", workspace_root)

    result: dict[str, Any] = {
        "latest_claim": None,
        "latest_return": None,
        "latest_audit_dispatch": None,
        "latest_verdict": None,
        "latest_closure": None,
        "events": [],
    }

    # claims
    claims_dir = records_root / "claims" / task_id
    result["latest_claim"] = _find_latest_record(claims_dir, "claim")

    # returns
    returns_dir = records_root / "returns" / task_id
    result["latest_return"] = _find_latest_record(returns_dir, "return")

    # audit_dispatches (AIPOS-F73 前置一: 门写在审计卡 ID 目录下,如 AIPOS-F75R)
    # 派审记录在 audit_dispatches/<audit_task_id>/ 而非 <task_id>/
    audit_task_id = f"{task_id}R"
    dispatches_dir = records_root / "audit_dispatches" / audit_task_id
    result["latest_audit_dispatch"] = _find_latest_record(dispatches_dir, "dispatch")

    # audit_verdicts (keyed by reviewed_task_id)
    verdicts_dir = records_root / "audit_verdicts" / task_id
    result["latest_verdict"] = _find_latest_record(verdicts_dir, "verdict")

    # closures(AIPOS-F73E: 前缀与门写侧同源 record_writer.CLOSURE_ID_PREFIX——门落 close_*, 读 closure_* 即永远找不到 → loop 不得 exit 0)
    from tools.aipos_cli.record_writer import CLOSURE_ID_PREFIX

    closures_dir = records_root / "closures" / task_id
    result["latest_closure"] = _find_latest_record(closures_dir, CLOSURE_ID_PREFIX)

    # events
    events_dir = records_root / "events" / task_id
    if events_dir.is_dir():
        for ef in sorted(events_dir.glob("*.md"), key=lambda p: p.stat().st_mtime):
            fm = _read_frontmatter(ef)
            if fm:
                result["events"].append(fm)

    return result


def _check_return_artifact(workspace_root: Path, task_id: str) -> bool:
    """执行体产物就绪判据(唯一): 路径读 transitions N2.artifact.location; 存在且非骨架
    (「一句话结论」非 `(待填写` 占位)才算。骨架由 claim 创建(N1.return_skeleton), 不是交回。"""
    path = _return_artifact_path(workspace_root, task_id)
    if not path.is_file():
        return False
    return _extract_return_summary(workspace_root, task_id) is not None


def _check_verdict_artifact(workspace_root: Path, task_id: str) -> Path | None:
    """审计体产物就绪判据(唯一): 落点根读项目声明(verdict_root), 文件候选读 transitions artifact_ingest.verdict
    (RETURN.md 优先, 回退 audit_report.md), 取首个 frontmatter 含 `verdict` 的文件;
    兼容旧命名 VERDICT-<ID>R.md。骨架 RETURN.md(无 verdict)不算。返回路径或 None。"""
    if not task_id.upper().endswith("R"):
        return None
    task_work_dir = verdict_artifact_dir(workspace_root, task_id)
    candidates = list(_audit_report_candidates(workspace_root, task_id))
    candidates.append(task_work_dir / f"VERDICT-{task_id}.md")
    candidates.append(task_work_dir / f"verdict-{task_id.lower()}.md")
    seen: set[Path] = set()
    for cand in candidates:
        if cand in seen or not cand.is_file():
            continue
        seen.add(cand)
        fm = _read_frontmatter(cand)
        if str(fm.get("verdict") or "").strip():
            return cand
    return None


def _check_audit_card(workspace_root: Path, task_id: str) -> bool:
    """检查审计卡是否已生成(<ID>R 在 queue 中)。"""
    from tools.aipos_cli.task_loader import find_task_card

    return find_task_card(workspace_root, f"{task_id}R", states=("pending", "claimed", "completed"))[0] is not None


# ---------------------------------------------------------------------------
# 推导核心:状态 → 下一步节点 + 角色 + 命令
# ---------------------------------------------------------------------------

def _extract_return_summary(workspace_root: Path, task_id: str) -> str | None:
    """从 RETURN.md 提取一句话结论。
    
    第4轮③: result_summary 必须从 RETURN.md「一句话结论」节提取,不存在→该步不该是return。
    AIPOS-F73D: 占位符(骨架 `(待填写`)不算结论 → None(= 产物未就绪)。路径读 N2.artifact.location。
    """
    try:
        return_path = _return_artifact_path(workspace_root, task_id)

        if not return_path.is_file():
            return None

        return extract_return_summary_text(return_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        import sys

        print(f"Warning: RETURN.md unreadable for {task_id}: {exc}", file=sys.stderr)
        return None


def extract_return_summary_text(content: str) -> str | None:
    """从 Return 正文提取「一句话结论」(唯一解析; 推导核与 artifact ingest 共用)。骨架占位 → None。"""
    lines = content.split("\n")
    in_summary_section = False
    for line in lines:
        if "一句话结论" in line or "## 一句话" in line:
            in_summary_section = True
            continue
        if in_summary_section:
            # 跳过空行
            if not line.strip():
                continue
            # 遇到下一个标题,结束
            if line.startswith("##"):
                break
            # 找到内容
            if line.strip():
                # 去掉 **完成** 等格式标记
                summary = line.strip().lstrip("*").rstrip("*").strip()
                # 去掉句号
                summary = summary.rstrip("。")
                if summary.startswith(_RETURN_PLACEHOLDER_PREFIX):
                    return None  # 骨架占位, 未交回
                return summary
    return None


def _resolve_active_policy(workspace_root: Path, task_id: str, role: str = "exec") -> str | None:
    """从 records/claims 最新记录读取当前有效信封。
    
    第4轮要求:推导必须只认记录,从 records/claims 最新成功认领取当前信封,禁用陈旧来源。
    """
    try:
        records_root = _resolve_governance_path_with_relative("records", workspace_root)
        claims_dir = records_root / "claims" / task_id
        
        if not claims_dir.is_dir():
            return None
        
        # 找最新记录(按修改时间)
        claim_files = sorted(claims_dir.glob("claim_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not claim_files:
            return None
        
        # 读 frontmatter
        latest_claim = _read_frontmatter(claim_files[0])
        return latest_claim.get("owner_policy_ref")
    except OSError as exc:
        import sys

        print(f"Warning: claims dir unreadable for {task_id}: {exc}", file=sys.stderr)
        return None


def _find_connection_json(workspace_root: Path) -> str | None:
    """查找 connection.json 路径。"""
    # 优先治理仓
    gov_conn = workspace_root / ".lybra" / "connection.json"
    if gov_conn.is_file():
        return str(gov_conn)
    # 环境变量
    env_conn = os.environ.get("LYBRA_CONNECTION_JSON")
    if env_conn and Path(env_conn).is_file():
        return env_conn
    return None


def _extract_artifact_subject_from_branch(
    workspace_root: Path,
    reviewed_task_id: str,
    task_mode: str = "code",
) -> dict[str, str] | None:
    """AIPOS-F73前置①: 从卡分支 tip 提取 artifact_subject (repository/commit_sha/tree_hash)。
    
    Args:
        workspace_root: 产品仓根目录
        reviewed_task_id: 被审任务 ID
        task_mode: 任务模式（code 卡需要 artifact_subject）
    
    Returns:
        artifact_subject dict 或 None（非 code 卡或提取失败）
    """
    if task_mode != "code":
        return None
    
    import subprocess
    
    # 推导分支名（按 transitions.schema N5.branch_integration.branch_pattern）
    branch_name = f"card/{reviewed_task_id}"
    
    try:
        # 获取分支 tip commit SHA
        result = subprocess.run(
            ["git", "rev-parse", branch_name],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        
        commit_sha = result.stdout.strip()
        if not commit_sha or len(commit_sha) != 40:
            return None
        
        # 获取 tree hash
        result = subprocess.run(
            ["git", "rev-parse", f"{commit_sha}^{{tree}}"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        
        tree_hash = result.stdout.strip()
        if not tree_hash or len(tree_hash) != 40:
            return None
        
        # 推导 repository（从 workspace_root 名称）
        repository = workspace_root.name
        
        return {
            "repository": repository,
            "commit_sha": commit_sha,
            "tree_hash": tree_hash,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: artifact_subject extraction failed for {branch_name}: {exc}", file=sys.stderr)
        return None


def _build_copyable_command(
    *,
    verb_base: str,
    task_id: str,
    actor: str,
    agent_instance: str,
    autonomy_mode: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    extra_args: dict[str, str] | None = None,
) -> str:
    """构建可照抄 CLI 命令。token 值永不出现。

    命令指向产品 CLI 薄壳(--confirm 两阶段一段式)。
    """
    parts = [f"lybra queue {verb_base}"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")

    if connection_json:
        parts.append(f"--connection-json {connection_json}")

    parts.append(f"--agent-instance {agent_instance}")
    # AIPOS-F73D 前置一③(parser 夹具实撞): 只有 `queue claim` 声明了 --autonomy-mode; `queue return` 的 argparse 无此参数,
    # 带上即 "unrecognized arguments" 禁执行。参数集以 aipos_cli 声明为准。
    if verb_base in ("claim", "return"):
        parts.append(f"--autonomy-mode {autonomy_mode}")

    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")

    if extra_args:
        for k, v in extra_args.items():
            parts.append(f"--{k} {v}")

    return " ".join(parts)


def required_return_frontmatter() -> list[str]:
    """Return 必填 frontmatter 键(唯一声明: transitions artifact_ingest.return.required_frontmatter)。"""
    keys = list(_artifact_ingest_declaration()["return"].get("required_frontmatter") or [])
    if not keys:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.required_frontmatter 未声明")
    return [str(k) for k in keys]


def missing_return_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    """缺失的 Return 必填 frontmatter 键列表(空值/占位视为缺)。"""
    missing = []
    for key in required_return_frontmatter():
        value = str(frontmatter.get(key) or "").strip()
        if not value or value.startswith(_RETURN_PLACEHOLDER_PREFIX) or value.startswith("<"):
            missing.append(key)
    return missing


def build_return_command_from_artifact(
    workspace_root: Path,
    task_id: str,
    *,
    claimer: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    result_summary: str,
    return_path: Path,
    autonomy_mode: str = "Supervised",
) -> str:
    """AIPOS-F78 件③: 由 Return 文件派生 `lybra queue return --confirm` 命令(推导核与 artifact ingest 共用, 禁第二路径)。
    AIPOS-F78B 件③: autonomy_mode=PreAuthorized + owner_policy_ref=驱动方信封 → 门一阶段落记录。

    completion_report_ref = Return 相对治理根路径; artifact_refs = [<branch>@<commit_sha>]; actual_model = frontmatter.model。
    """
    import json as _json

    fm = _read_frontmatter(return_path)
    try:
        report_ref = str(Path(return_path).resolve().relative_to(Path(workspace_root).resolve()))
    except ValueError:
        report_ref = str(return_path)
    extra: dict[str, str] = {"result-summary": _json.dumps(result_summary, ensure_ascii=False)}
    extra["completion-report-ref"] = report_ref
    branch = str(fm.get("branch") or "").strip()
    commit_sha = str(fm.get("commit_sha") or "").strip()
    if branch and commit_sha:
        extra["artifact-refs"] = "'" + _json.dumps([f"{branch}@{commit_sha}"]) + "'"
    model = str(fm.get("model") or "").strip()
    if model:
        extra["actual-model"] = _json.dumps(model, ensure_ascii=False)
    return _build_copyable_command(
        verb_base="return",
        task_id=task_id,
        actor=claimer,
        agent_instance=claimer,
        autonomy_mode=autonomy_mode,
        owner_policy_ref=owner_policy_ref,
        connection_json=connection_json,
        extra_args=extra,
    )


def _build_audit_dispatch_command(
    *,
    task_id: str,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    audit_task_id: str,
    audit_agent_instance: str,
) -> str:
    """构建审计派发命令。"""
    parts = ["lybra audit dispatch"]
    parts.append(f"--source-task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append(f"--agent-instance {agent_instance}")
    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
    parts.append(f"--audit-task-id {audit_task_id}")
    parts.append(f"--audit-agent-instance {audit_agent_instance}")
    return " ".join(parts)


def _build_verdict_submit_command(
    *,
    reviewed_task_id: str,
    audit_task_id: str,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    verdict: str = "PASS",
    artifact_subject: dict[str, str] | None = None,
    autonomy_mode: str = "Supervised",
) -> str:
    """构建审计裁决提交命令。
    
    AIPOS-F73前置①: artifact_subject 从卡分支 tip 提取，code 卡必填。
    AIPOS-F78B 件③: autonomy_mode=PreAuthorized + owner_policy_ref=驱动方信封 → 门一阶段落记录。
    """
    parts = ["lybra audit-verdict"]
    parts.append(f"--reviewed-task-id {reviewed_task_id}")
    parts.append(f"--audit-task-id {audit_task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")
    if connection_json:
        parts.append(f"--connection-json {connection_json}")
    parts.append(f"--agent-instance {agent_instance}")
    if autonomy_mode == "PreAuthorized":
        parts.append(f"--autonomy-mode {autonomy_mode}")
    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
    parts.append(f"--verdict {verdict}")
    
    # AIPOS-F73前置①: artifact_subject for code tasks
    if artifact_subject:
        repo = artifact_subject.get("repository", "")
        commit_sha = artifact_subject.get("commit_sha", "")
        tree_hash = artifact_subject.get("tree_hash", "")
        if repo:
            parts.append(f"--artifact-subject-repository {repo}")
        if commit_sha:
            parts.append(f"--artifact-subject-commit-sha {commit_sha}")
        if tree_hash:
            parts.append(f"--artifact-subject-tree-hash {tree_hash}")
    
    return " ".join(parts)


def _build_finalize_command(
    *,
    task_id: str,
    actor: str,
    code_repo_root: Path,
    governance_root: Path,
) -> str:
    """AIPOS-F73D 前置一②: finalize 命令模板(唯一)。必带 --actor; --workspace-root=产品仓根(git 操作地),
    --governance-root=治理根(裁决记录地)——两根永不混用(F75/F73C2 实撞同病)。"""
    parts = ["lybra finalize"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--push")
    parts.append("--deploy")
    parts.append(f"--workspace-root {code_repo_root}")
    parts.append(f"--governance-root {governance_root}")
    return " ".join(parts)


def _build_close_command(
    *,
    task_id: str,
    actor: str,
    connection_json: str | None,
    closure_evidence_json: str = '{}',
    autonomy_mode: str = "Supervised",
    owner_policy_ref: str | None = None,
) -> str:
    """构建结案命令。AIPOS-F78B 件③: 驱动方信封在 → `--confirm --autonomy-mode PreAuthorized --owner-policy-ref <pid>`
    经门一阶段落记录(queue close 薄壳走 MCP); 否则存量本地 close_task 形。"""
    parts = ["lybra queue close"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append(f"--closure-evidence '{closure_evidence_json}'")
    if autonomy_mode == "PreAuthorized" and owner_policy_ref:
        parts.append("--confirm")
        parts.append(f"--autonomy-mode {autonomy_mode}")
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
        if connection_json:
            parts.append(f"--connection-json {connection_json}")
    return " ".join(parts)


def derive_next_step(
    task_id: str,
    workspace_root: Path,
) -> dict[str, Any]:
    """推导单卡下一步。

    返回:
    {
        "task_id": str,
        "derivable": bool,  # 是否可推导
        "current_node": str | None,  # 当前节点名(如 "claim", "return")
        "current_state": str,  # 队列位置
        "triggered_by": str,  # 该谁动(角色)
        "command": str,  # 可照抄命令
        "verb": str,  # 门动词名
        "missing_records": list[str],  # 缺失记录(fail-closed 时用)
        "suggested_action": str,  # 建议动作
        "notes": str,
    }
    """
    workspace_root = Path(workspace_root)

    # 1. 找任务卡(AIPOS-F78B 件①: frontmatter task_id 精确匹配; 多义 = 不可推导点名两份文件)
    from tools.aipos_cli.task_loader import AmbiguousTaskCard

    try:
        task_path, queue_dir = _find_task_in_queue(workspace_root, task_id)
    except AmbiguousTaskCard as exc:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": None,
            "current_state": "ambiguous",
            "triggered_by": "advisor",
            "command": "",
            "verb": "",
            "missing_records": [str(exc)],
            "suggested_action": "同一 task_id 只能有一份卡文件: 撤废/合并多余的那份后重推导",
            "notes": "",
        }
    if not task_path:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": None,
            "current_state": "not_found",
            "triggered_by": "unknown",
            "command": "",
            "verb": "",
            "missing_records": [f"queue 目录中找不到任务卡 {task_id}"],
            "suggested_action": f"确认任务 ID 正确,或检查 5_tasks/queue/ 各子目录",
            "notes": "",
        }

    fm = _read_frontmatter(task_path)
    records = _read_task_records(workspace_root, task_id)
    has_return_artifact = _check_return_artifact(workspace_root, task_id)
    has_audit_card = _check_audit_card(workspace_root, task_id)
    verdict_artifact = _check_verdict_artifact(workspace_root, task_id)

    task_mode = fm.get("task_mode", "code")
    assigned_to = fm.get("assigned_to") or fm.get("agent_instance") or ""
    audit_required = fm.get("audit") == "required"
    owner_verify = fm.get("owner_verify") == "required"
    is_audit_card = task_id.upper().endswith("R")

    connection_json = _find_connection_json(workspace_root)

    # 公共参数
    conn_arg = connection_json

    # -----------------------------------------------------------------------
    # 审计卡特殊处理: claimed + 有 verdict 报告 → 提交裁决
    # -----------------------------------------------------------------------
    if is_audit_card and queue_dir == "claimed" and verdict_artifact:
        # 从 verdict 报告提取参数
        verdict_fm = _read_frontmatter(verdict_artifact)
        reviewed_task_id = verdict_fm.get("reviewed_task_id") or task_id.rstrip("Rr")
        verdict = verdict_fm.get("verdict", "PASS")
        # AIPOS-F73E 件①: actor/agent_instance = 审计卡 claim 记录的审计实例(禁读报告自报/卡面/驱动方); 无 claim 记录不可推导
        actor = _claimer_instance(records)
        if not actor:
            return _not_derivable_no_claim(task_id, node="audit_verdict", state="claimed", verb="lybra_audit_verdict_dry_run",
                                           triggered_by="auditor", notes="N4: 审计卡有 VERDICT 报告但无 claim 记录, 裁决 actor 无据(AIPOS-F73E 件①)")
        agent_inst = actor
        policy_ref = _resolve_active_policy(workspace_root, task_id, role="audit")
        
        # AIPOS-F73前置①: 从被审卡分支提取 artifact_subject (code 卡必填)
        reviewed_task_path, _ = _find_task_in_queue(workspace_root, reviewed_task_id)
        reviewed_fm = _read_frontmatter(reviewed_task_path) if reviewed_task_path else {}
        reviewed_task_mode = reviewed_fm.get("task_mode", "code")
        # AIPOS-F73D + F78C: 卡分支活在被审卡声明的产品仓(resolve_card_repo: lane.repo → repos 清单 → code_repo → 治理根)
        from tools.aipos_cli.workspace_config import CardRepoUnresolved

        try:
            reviewed_repo = _card_repo_root(workspace_root, reviewed_task_id, reviewed_fm) if reviewed_task_mode == "code" else workspace_root
        except CardRepoUnresolved as exc:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "audit_verdict",
                "current_state": "claimed",
                "triggered_by": "auditor",
                "command": "",
                "verb": "lybra_audit_verdict_dry_run",
                "missing_records": [f"被审卡 {reviewed_task_id} 产品仓不可解析: {exc}"],
                "suggested_action": exc.reason,
                "notes": f"N4: 被审卡产品仓解析失败({exc.code}), 裁决 artifact_subject 无据(AIPOS-F78C)",
                "action": {"type": "artifact_invalid", "card": reviewed_task_id, "path": ""},
            }
        artifact_subject = _extract_artifact_subject_from_branch(reviewed_repo, reviewed_task_id, reviewed_task_mode)
        # AIPOS-F78B 件③: 驱动方信封(覆盖被审卡, 与 loop 同一判据)在 → 一阶段 PreAuthorized; 否则 Supervised 形(执行时 exit 5)
        driver_policy = _driver_envelope_ref(workspace_root, reviewed_task_id, reviewed_fm, conn_arg)
        
        cmd = _build_verdict_submit_command(
            reviewed_task_id=reviewed_task_id,
            audit_task_id=task_id,
            actor=actor,
            agent_instance=agent_inst,
            owner_policy_ref=driver_policy or policy_ref,
            connection_json=conn_arg,
            verdict=verdict,
            artifact_subject=artifact_subject,
            autonomy_mode="PreAuthorized" if driver_policy else "Supervised",
        )
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "audit_verdict",
            "current_state": "claimed",
            "triggered_by": "auditor",
            "command": cmd,
            "verb": "lybra_audit_verdict_dry_run",
            "missing_records": [],
            "suggested_action": "提交审计裁决",
            "notes": f"N4: 审计卡 claimed + VERDICT 报告存在,需提交裁决",
        }
    
    # 审计卡 claimed 但无 verdict 报告 → 不可推导
    if is_audit_card and queue_dir == "claimed" and not verdict_artifact:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "claim",
            "current_state": "claimed",
            "triggered_by": "auditor",
            "command": "",
            "verb": "",
            "missing_records": [f"VERDICT-{task_id}.md 审计报告"],
            "suggested_action": "等待 auditor 完成审计并生成 VERDICT 报告",
            "notes": "审计卡 claimed + 无 VERDICT 报告,等待审计员完成工作",
        }

    # -----------------------------------------------------------------------
    # 按 queue 位置 + 记录推导下一步
    # -----------------------------------------------------------------------

    # --- pending → N1: claim ---
    if queue_dir == "pending":
        actor = assigned_to or "<executor>"
        agent_inst = assigned_to or "<executor-instance>"
        policy_ref = _resolve_active_policy(workspace_root, task_id, role="exec")
        cmd = _build_copyable_command(
            verb_base="claim",
            task_id=task_id,
            actor=actor,
            agent_instance=agent_inst,
            autonomy_mode="PreAuthorized",
            owner_policy_ref=policy_ref,
            connection_json=conn_arg,
        )
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "publish",
            "current_state": "pending",
            "triggered_by": "executor",
            "command": cmd,
            "verb": "lybra_queue_claim_dry_run",
            "missing_records": [],
            "suggested_action": "认领任务",
            "notes": f"N0→N1: 任务已发布,等待认领",
        }

    # --- claimed → N2 (return) 或等待执行 ---
    if queue_dir == "claimed":
        latest_claim = records.get("latest_claim")
        latest_return = records.get("latest_return")
        # AIPOS-F73E 件①: 账务动词 actor = claim 记录实例(唯一实现 _claimer_instance); 缺记录时各派生点 fail-closed
        claimer = _claimer_instance(records)

        # 已有 return 记录 → 检查后续节点 N3→N6 (AIPOS-F73B前置零)
        if latest_return:
            latest_audit_dispatch = records.get("latest_audit_dispatch")
            latest_verdict = records.get("latest_verdict")
            latest_closure = records.get("latest_closure")
            
            # 检查是否有 finalization 记录
            finalizations_dir = _resolve_governance_path_with_relative("records", workspace_root) / "finalizations" / task_id
            has_finalization = finalizations_dir.is_dir() and any(finalizations_dir.glob("finalization_*.md"))
            
            # N5→N6: 有 finalization 但无 closure → close
            if has_finalization and not latest_closure:
                # AIPOS-F73C前置零之一 + F78 前置零③: closure_evidence 从记录自填三字段, 三字段齐才派生
                closure_evidence = {}

                # 1. finalize_commit_hash: 从 finalization 记录读取 merge_commit(F78 声明字段), 兼容 commit/commit_hash
                fin_fm: dict[str, Any] = {}
                if finalizations_dir.is_dir():
                    finalization_files = sorted(finalizations_dir.glob("finalization_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if finalization_files:
                        fin_fm = _read_frontmatter(finalization_files[0])
                        merge_commit = str(fin_fm.get("merge_commit") or fin_fm.get("commit") or fin_fm.get("commit_hash") or "").strip()
                        if merge_commit:
                            closure_evidence["finalize_commit_hash"] = merge_commit

                # 2. finalize_return_ref: 从 returns 记录读取
                returns_dir = _resolve_governance_path_with_relative("records", workspace_root) / "returns" / task_id
                if returns_dir.is_dir():
                    return_files = sorted(returns_dir.glob("return_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if return_files:
                        ret_fm = _read_frontmatter(return_files[0])
                        if ret_fm.get("return_id"):
                            closure_evidence["finalize_return_ref"] = ret_fm["return_id"]

                # 3. verdict_ref: 从 audit_verdicts 记录读取
                if latest_verdict and latest_verdict.get("verdict_id"):
                    closure_evidence["verdict_ref"] = latest_verdict["verdict_id"]

                missing_evidence = [
                    f"{key}({src})"
                    for key, src in (
                        ("finalize_commit_hash", "finalization 记录 merge_commit"),
                        ("finalize_return_ref", "return 记录 return_id"),
                        ("verdict_ref", "裁决记录 verdict_id"),
                    )
                    if not closure_evidence.get(key)
                ]
                if missing_evidence:
                    return {
                        "task_id": task_id,
                        "derivable": False,
                        "current_node": "finalize",
                        "current_state": "claimed",
                        "triggered_by": "advisor",
                        "command": "",
                        "verb": "lybra_queue_close_dry_run",
                        "missing_records": missing_evidence,
                        "suggested_action": "补齐记录后重推导(finalization 记录须含 merge_commit: transitions N5.record.merge_commit)",
                        "notes": "N5→N6: closure_evidence 三字段未齐, 不派生 close(AIPOS-F78 前置零③)",
                    }

                import json
                closure_evidence_json = json.dumps(closure_evidence)

                # AIPOS-F73E 件①(改写 F78 前置零②): actor = 该卡 claim 记录的执行实例(F73C 定案: token 归驱动方, actor 归认领实例);
                # 门按 actor==claimer 判(queue_mutation complete), 驱动方实例当 actor 必被拒(F78 活体实撞); 无 claim 记录不可推导
                if not claimer:
                    return _not_derivable_no_claim(task_id, node="finalize", state="claimed", verb="lybra_queue_close_dry_run",
                                                   triggered_by="advisor", notes="N5→N6: 已 finalize 但无 claim 记录, close actor 无据(AIPOS-F73E 件①)")

                driver_policy = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg)  # AIPOS-F78B 件③
                cmd = _build_close_command(
                    task_id=task_id,
                    actor=claimer,
                    connection_json=conn_arg,
                    closure_evidence_json=closure_evidence_json,
                    autonomy_mode="PreAuthorized" if driver_policy else "Supervised",
                    owner_policy_ref=driver_policy,
                )
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "finalize",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": cmd,
                    "verb": "lybra_queue_close_dry_run",
                    "missing_records": [],
                    "suggested_action": "结案",
                    "notes": "N5→N6: 已 finalize,需 close",
                }
            
            # N6: 有 closure → 已完成
            if latest_closure:
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "close",
                    "current_state": "claimed",
                    "triggered_by": "none",
                    "command": "",
                    "verb": "",
                    "missing_records": [],
                    "suggested_action": "无(任务已结案)",
                    "notes": "N6: 任务已有 closure 记录,无下一步",
                }
            
            # N4→N5: 有 verdict 但无 finalization → finalize
            if latest_verdict and not has_finalization:
                verdict_result = latest_verdict.get("verdict", "")
                if verdict_result in ("PASS", "PASS_WITH_NOTES"):
                    # AIPOS-F78B 件②: 产品仓不在本机(finalize_mode=external) → 不派生 lybra finalize, 等外部 FINALIZE 卡 Return 经 ingest 铸记录
                    if _finalize_mode(workspace_root) == "external":
                        return _derive_external_finalize(workspace_root, task_id, fm, claimer=claimer, verdict_result=verdict_result)
                    # AIPOS-F73D 前置一② + F78C: finalize 模板必带 --actor, --workspace-root=该卡声明的产品仓(resolve_card_repo), --governance-root=治理根
                    from tools.aipos_cli.workspace_config import CardRepoUnresolved

                    try:
                        code_repo_root = _card_repo_root(workspace_root, task_id, fm, allow_governance_root=False)
                    except CardRepoUnresolved as exc:
                        return {
                            "task_id": task_id,
                            "derivable": False,
                            "current_node": "audit_verdict",
                            "current_state": "claimed",
                            "triggered_by": "advisor",
                            "command": "",
                            "verb": "lybra_finalize",
                            "missing_records": [f"卡 {task_id} 产品仓声明(finalize --workspace-root 依据): {exc}"],
                            "suggested_action": exc.reason,
                            "notes": f"N4→N5: 裁决 {verdict_result}, 但产品仓不可解析({exc.code}), 不可派生 finalize 命令",
                        }
                    # AIPOS-F73E 件①(改写 F78 前置零②): finalize actor = 该卡 claim 记录的执行实例; 无 claim 记录不可推导
                    if not claimer:
                        return _not_derivable_no_claim(task_id, node="audit_verdict", state="claimed", verb="lybra_finalize",
                                                       triggered_by="advisor", notes=f"N4→N5: 裁决 {verdict_result}, 但无 claim 记录, finalize actor 无据(AIPOS-F73E 件①)")
                    cmd = _build_finalize_command(
                        task_id=task_id,
                        actor=claimer,
                        code_repo_root=code_repo_root,
                        governance_root=workspace_root,
                    )
                    return {
                        "task_id": task_id,
                        "derivable": True,
                        "current_node": "audit_verdict",
                        "current_state": "claimed",
                        "triggered_by": "advisor",
                        "command": cmd,
                        "verb": "lybra_finalize",
                        "missing_records": [],
                        "suggested_action": "finalize 并部署",
                        "notes": f"N4→N5: 裁决 {verdict_result},需 finalize",
                    }
                else:
                    # FAIL/BLOCK 等非 PASS 裁决
                    return {
                        "task_id": task_id,
                        "derivable": False,
                        "current_node": "audit_verdict",
                        "current_state": "claimed",
                        "triggered_by": "executor",
                        "command": "",
                        "verb": "",
                        "missing_records": [],
                        "suggested_action": f"处理裁决 {verdict_result} (返工或修复)",
                        "notes": f"N4: 裁决 {verdict_result},需人工处理",
                    }
            
            # N3→N4: 已派审但无 verdict → 等待审计体产物
            if latest_audit_dispatch and not latest_verdict:
                audit_id = f"{task_id}R"
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "audit_dispatch",
                    "current_state": "claimed",
                    "triggered_by": "auditor",
                    "command": "",
                    "verb": "",
                    "missing_records": [f"审计卡 {audit_id} 的 VERDICT 报告"],
                    "suggested_action": f"等待审计体完成 {audit_id} 并生成 VERDICT",
                    "notes": "N3: 已派审,等待审计体产物",
                    "action": {"type": "await_artifact", "card": audit_id},
                }
            
            # N2→N3: 已 return + 未派审 → 派审。AIPOS-F73D: 审计卡由派审动作生成(transitions N3.automation.creates_audit_card),
            # 执行体零门(F73C)后不再"自产审计卡"; 审计卡已存在时派审幂等(AIPOS-C1 大项C②)。
            if not latest_audit_dispatch and (task_mode == "code" or audit_required):
                audit_id = f"{task_id}R"
                policy_ref = _resolve_active_policy(workspace_root, task_id, role="exec")
                # 第4轮②: 派审是 owner-dispatch 的动词,不是 exec
                dispatch_actor = "owner-dispatch.lybra.kiwiai-dev"
                dispatch_agent = "owner-dispatch.lybra.kiwiai-dev"
                # 审计体实例: 卡面 audit_by 声明优先(card.schema), 缺省沿用存量实例名
                audit_instance = str(fm.get("audit_by") or "").strip() or "audit.lybra.kiwiai-dev"
                cmd = _build_audit_dispatch_command(
                    task_id=task_id,
                    actor=dispatch_actor,
                    agent_instance=dispatch_agent,
                    owner_policy_ref=policy_ref,
                    connection_json=conn_arg,
                    audit_task_id=audit_id,
                    audit_agent_instance=audit_instance,
                )
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": cmd,
                    "verb": "lybra_audit_dispatch_dry_run",
                    "missing_records": [],
                    "suggested_action": "派发审计",
                    "notes": "N2→N3: 已 return, 需派审" + ("(审计卡已存在, 派审幂等)" if has_audit_card else "(派审生成审计卡)"),
                }
            # 非代码卡,不需要独立审计
            if task_mode not in ("code",) and not audit_required:
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": f"# 非代码卡(task_mode={task_mode}):顾问审核后直接提交裁决",
                    "verb": "lybra_audit_verdict_dry_run",
                    "missing_records": [],
                    "suggested_action": "顾问审核并提交裁决",
                    "notes": "非代码卡快车道:顾问直接审",
                }
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "return",
                "current_state": "claimed",
                "triggered_by": "advisor",
                "command": "",
                "verb": "",
                "missing_records": [],
                "suggested_action": "等待审计流程",
                "notes": "已 return,审计流程进行中",
            }

        # 有 RETURN.md 但还没 return 记录 → 交回工作(N2)
        if has_return_artifact:
            # 第4轮③: 从 RETURN.md 提取 result_summary
            result_summary = _extract_return_summary(workspace_root, task_id)
            if not result_summary:
                # RETURN.md 存在但无法提取一句话结论,fail-closed
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "claim",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "",
                    "missing_records": ["RETURN.md 存在但无法提取一句话结论"],
                    "suggested_action": "检查 RETURN.md 是否包含『一句话结论』节",
                    "notes": "RETURN.md 格式不完整,推导不出",
                }

            # AIPOS-F78 件③: Return 必填 frontmatter(transitions artifact_ingest.return.required_frontmatter)缺 = 不可推导
            # (action=artifact_invalid, loop 据此 exit 4 而非空等), 齐则派生与 ingest 同一条 return 命令
            return_path = _return_artifact_path(workspace_root, task_id)
            missing_fm = missing_return_frontmatter(_read_frontmatter(return_path))
            if missing_fm:
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "claim",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "lybra_queue_return_dry_run",
                    "missing_records": [f"Return frontmatter 缺 {k}" for k in missing_fm],
                    "suggested_action": f"执行体在 {return_path} 的 frontmatter 补齐 {', '.join(missing_fm)}(声明: transitions.schema artifact_ingest.return.required_frontmatter)",
                    "notes": "Return 已落盘但必填 frontmatter 不齐, 产品无法铸交回记录(AIPOS-F78 件③ fail-closed)",
                    "action": {"type": "artifact_invalid", "card": task_id, "path": str(return_path)},
                }

            if not claimer:
                return _not_derivable_no_claim(task_id, node="claim", state="claimed", verb="lybra_queue_return_dry_run",
                                               triggered_by="executor", notes="N1→N2: Return 已落盘但无 claim 记录, return actor 无据(AIPOS-F73E 件①)")
            policy_ref = _resolve_active_policy(workspace_root, task_id, role="exec")
            driver_policy = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg)  # AIPOS-F78B 件③
            cmd = build_return_command_from_artifact(
                workspace_root,
                task_id,
                claimer=claimer,
                owner_policy_ref=driver_policy or policy_ref,
                connection_json=conn_arg,
                result_summary=result_summary,
                return_path=return_path,
                autonomy_mode="PreAuthorized" if driver_policy else "Supervised",
            )
            return {
                "task_id": task_id,
                "derivable": True,
                "current_node": "claim",
                "current_state": "claimed",
                "triggered_by": "executor",
                "command": cmd,
                "verb": "lybra_queue_return_dry_run",
                "missing_records": [],
                "suggested_action": "交回工作(RETURN.md 已存在,执行 return)",
                "notes": "N1→N2: RETURN.md 已生成,需执行 return 动词",
            }

        # 无 return 记录也无 RETURN.md → 检查是否有返工节 (AIPOS-F75 件③)
        rework_rounds = fm.get("rework_rounds", [])
        if isinstance(rework_rounds, list) and rework_rounds:
            # 取最新且未销账的轮次
            uncleared_rounds = [r for r in rework_rounds if not r.get("cleared_at")]
            if uncleared_rounds:
                # 有未销账返工节,输出点杀清单
                latest_round = uncleared_rounds[-1]
                focus_items = latest_round.get("focus_items", [])
                round_num = latest_round.get("round", len(rework_rounds))
                verdict_ref = latest_round.get("verdict_ref", "")
                
                focus_text = "\n".join(f"  - {item}" for item in focus_items) if focus_items else "  (无具体项)"
                
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "claim",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "",
                    "missing_records": [],
                    "suggested_action": f"执行第 {round_num} 轮返工，完成后生成 RETURN.md 并执行 return",
                    "notes": f"AIPOS-F75: 卡面有返工节 (第 {round_num} 轮，依据 {verdict_ref})，点杀清单:\n{focus_text}",
                    "rework_round": latest_round,
                }
            # 全部返工节已销账，走原逻辑
        
        # 检查 events 是否有失败信号
        events = records.get("events", [])
        has_blocked = any(
            str(e.get("event_type") or e.get("event_kind") or "") in ("blocked", "launch_failed")
            for e in events
        )
        if has_blocked:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "claim",
                "current_state": "claimed",
                "triggered_by": "unknown",
                "command": "",
                "verb": "",
                "missing_records": ["RETURN.md 工作产物", "return 记录"],
                "suggested_action": "执行体遇到阻塞,需人工检查或 resume 轮派工",
                "notes": "claimed + 有 blocked/launch_failed 事件,无 return 产物",
            }

        # 事实不足以推导 → fail-closed
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "claim",
            "current_state": "claimed",
            "triggered_by": "executor",
            "command": "",
            "verb": "",
            "missing_records": ["RETURN.md 工作产物", "return 记录"],
            "suggested_action": "等待执行体完成工作并生成 RETURN.md,或检查执行状态",
            "notes": "claimed + 无 return 产物/记录,事实不足以推导下一步",
        }

    # --- completed → done ---
    if queue_dir == "completed":
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "close",
            "current_state": "completed",
            "triggered_by": "none",
            "command": "# 任务已完成,无下一步",
            "verb": "",
            "missing_records": [],
            "suggested_action": "无(任务已结束)",
            "notes": "N6: 任务已结案",
        }

    # --- blocked → 需人工裁定 ---
    if queue_dir == "blocked":
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "blocked",
            "current_state": "blocked",
            "triggered_by": "advisor",
            "command": "",
            "verb": "",
            "missing_records": ["blocked 恢复策略需人工裁定"],
            "suggested_action": "检查阻塞原因,决定 reopen 或释放",
            "notes": "任务被阻塞,需人工裁定恢复策略",
        }

    # --- 未知 queue 位置 ---
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": None,
        "current_state": queue_dir or "unknown",
        "triggered_by": "unknown",
        "command": "",
        "verb": "",
        "missing_records": [f"无法识别的队列位置: {queue_dir}"],
        "suggested_action": "检查任务卡在 queue/ 中的位置是否正确",
        "notes": "",
    }


def scan_project(workspace_root: Path) -> list[dict[str, Any]]:
    """项目级扫描:返回所有活跃任务的最小待办清单。

    按优先级排序:pending(先出) > claimed(有 return 产物) > claimed(无产物) > blocked。
    """
    workspace_root = Path(workspace_root)
    queue_root = _resolve_governance_path_with_relative("queue", workspace_root)
    results: list[dict[str, Any]] = []

    # 扫描 pending + claimed(活跃任务)
    for status_dir in ["pending", "claimed", "blocked"]:
        status_path = queue_root / status_dir
        if not status_path.is_dir():
            continue
        for task_file in sorted(status_path.glob("*.md")):
            # 从文件名提取 task_id
            raw_id = task_file.stem
            # 尝试从 frontmatter 取真实 task_id
            fm = _read_frontmatter(task_file)
            task_id = fm.get("task_id", raw_id.upper()) if fm else raw_id.upper()
            # 跳过审计卡(以 R 结尾的)—— 审计卡单独处理
            # 但在扫描中仍显示
            try:
                result = derive_next_step(str(task_id), workspace_root)
                results.append(result)
            except Exception as e:
                results.append({
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": None,
                    "current_state": status_dir,
                    "triggered_by": "unknown",
                    "command": "",
                    "verb": "",
                    "missing_records": [f"推导异常: {e}"],
                    "suggested_action": "检查任务状态",
                    "notes": str(e),
                })

    # 排序:pending 优先,然后 claimed 中可推导的优先
    priority = {"pending": 0, "claimed": 1, "blocked": 2, "completed": 3}
    results.sort(key=lambda r: (
        priority.get(r.get("current_state", ""), 9),
        0 if r.get("derivable") else 1,
    ))

    return results


def format_output(result: dict[str, Any], *, json_mode: bool = False) -> str:
    """格式化输出。面向三类扣扳机者(Owner 人肉/agent 会话/未来 cron)同一份。"""
    if json_mode:
        import json
        return json.dumps(result, indent=2, ensure_ascii=False)

    lines: list[str] = []
    task_id = result.get("task_id", "?")
    lines.append(f"Task: {task_id}")
    lines.append(f"State: {result.get('current_state', '?')}")
    lines.append(f"Node: {result.get('current_node', '?')}")
    lines.append(f"Triggered by: {result.get('triggered_by', '?')}")

    if result.get("derivable"):
        lines.append(f"Verb: {result.get('verb', '')}")
        cmd = result.get("command", "")
        if cmd:
            lines.append(f"Command:")
            lines.append(f"  {cmd}")
    else:
        missing = result.get("missing_records", [])
        if missing:
            lines.append(f"Missing: {', '.join(missing)}")
        lines.append(f"Suggested: {result.get('suggested_action', '?')}")

    notes = result.get("notes", "")
    if notes:
        lines.append(f"Notes: {notes}")

    return "\n".join(lines)


def format_scan_output(results: list[dict[str, Any]], *, json_mode: bool = False) -> str:
    """格式化项目级扫描输出。"""
    if json_mode:
        import json
        return json.dumps(results, indent=2, ensure_ascii=False)

    if not results:
        return "No active tasks found in queue."

    lines: list[str] = []
    lines.append(f"=== lybra next — project scan ({len(results)} active tasks) ===")
    lines.append("")

    for r in results:
        task_id = r.get("task_id", "?")
        state = r.get("current_state", "?")
        node = r.get("current_node", "?")
        triggered = r.get("triggered_by", "?")

        if r.get("derivable"):
            cmd = r.get("command", "")
            lines.append(f"[{state}/{node}] {task_id} → {triggered}")
            if cmd:
                lines.append(f"  Command: {cmd}")
        else:
            missing = r.get("missing_records", [])
            suggested = r.get("suggested_action", "?")
            lines.append(f"[{state}/{node}] {task_id} → NOT DERIVABLE")
            if missing:
                lines.append(f"  Missing: {', '.join(missing)}")
            lines.append(f"  Suggested: {suggested}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AIPOS-F73件②③: next --run 机器扣扳机 — 推导后立即执行
# ---------------------------------------------------------------------------

def _check_branch_has_commits(workspace_root: Path, task_id: str) -> bool:
    """AIPOS-F73件③: 检查卡分支是否有提交（相对 main）。
    
    Args:
        workspace_root: 产品仓根目录
        task_id: 任务 ID
    
    Returns:
        True if branch has commits, False otherwise
    """
    import subprocess
    
    branch_name = f"card/{task_id}"
    
    try:
        # 检查分支是否存在
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        
        # 检查是否有相对 main 的提交
        result = subprocess.run(
            ["git", "rev-list", "--count", f"main..{branch_name}"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        
        commit_count = int(result.stdout.strip())
        return commit_count > 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: branch commit check failed for {branch_name}: {exc}", file=sys.stderr)
        return False


def _ensure_worktree(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-F73件② + F73D 前置三: 确保 worktree 存在(建/复用分支 card/<ID>)。

    落点 = 该卡产品仓根(AIPOS-F78C: workspace_config.resolve_card_repo, 卡 lane.repo → project.json repos 清单 → code_repo 别名)
    下的声明位(config.schema worktree_root, 默认 `{code_repo}/.worktrees`), 禁在治理根下建工作树(F73B 原实现
    `workspace_root/card/<ID>` 之误)。仓解析不到/不是 git 仓 → fail-closed。
    
    Args:
        workspace_root: 治理根(推导核工作区); 若其 project.json 无仓声明且自身是 git 仓, 视为产品仓(靶场单根)
        task_id: 任务 ID
    
    Returns:
        {"ok": bool, "worktree_path": str, "message": str}
    """
    import subprocess

    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    # AIPOS-F78C 件②: 落点仓 = 该卡声明的仓(resolve_card_repo), 多仓项目两张卡各落各仓 .worktrees/<ID>
    try:
        code_repo = _card_repo_root(workspace_root, task_id)
    except CardRepoUnresolved as exc:
        return {"ok": False, "worktree_path": "", "message": f"无法定位卡 {task_id} 的产品仓建 worktree: {exc}"}
    if not (code_repo / ".git").exists():
        return {
            "ok": False,
            "worktree_path": "",
            "message": f"卡 {task_id} 解析到的产品仓 {code_repo} 不是 git 仓根(无 .git), 无法建 worktree; 出口: project.json repos/code_repo 指向真实产品仓",
        }
    try:
        worktree_path = _resolve_worktree_root(workspace_root, code_repo) / task_id
    except (OSError, ValueError) as exc:
        return {"ok": False, "worktree_path": "", "message": f"worktree 落点声明读取失败: {exc}"}
    workspace_root = code_repo  # 以下 git 操作全部在产品仓
    branch_name = f"card/{task_id}"
    
    # 检查 worktree 是否已存在
    if worktree_path.exists():
        return {
            "ok": True,
            "worktree_path": str(worktree_path),
            "message": f"Worktree already exists: {worktree_path}",
        }
    
    # 创建 worktree 目录
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # 检查分支是否已存在
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch_exists = result.returncode == 0
        
        if branch_exists:
            # 复用已有分支
            result = subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch_name],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            # 创建新分支
            result = subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(worktree_path), "main"],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        
        if result.returncode != 0:
            return {
                "ok": False,
                "worktree_path": "",
                "message": f"Failed to create worktree: {result.stderr}",
            }
        
        return {
            "ok": True,
            "worktree_path": str(worktree_path),
            "message": f"Worktree created: {worktree_path}",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "worktree_path": "",
            "message": f"Exception creating worktree: {exc}",
        }


def _execute_claim_with_role_token(
    *,
    task_id: str,
    workspace_root: Path,
    connection_json: str | None,
) -> dict[str, Any]:
    """AIPOS-F73B件① + F73C件⑥返工: pending 卡按 advisor token 认领。
    
    账务动词由驱动方 token 执行、actor=该卡实例:
    - token: advisor (从 connection.json 读取)
    - actor: assigned_to/agent_instance (该卡声明的实例)
    
    认领后建 worktree、输出 spawn_worker action。
    
    Args:
        task_id: 任务 ID
        workspace_root: 产品仓根目录
        connection_json: connection.json 路径（可选）
    
    Returns:
        execute_derived_action 格式的响应
    """
    import subprocess
    import json
    
    # 1. 读取任务卡获取 assigned_to
    task_path, queue_dir = _find_task_in_queue(workspace_root, task_id)
    if not task_path:
        return {
            "ok": False,
            "action_type": "claim",
            "message": f"Task {task_id} not found in queue",
            "command": "",
            "exit_code": 1,
            "output": "",
        }
    
    task_fm = _read_frontmatter(task_path)
    if not task_fm:
        return {
            "ok": False,
            "action_type": "claim",
            "message": f"Cannot read task frontmatter: {task_path}",
            "command": "",
            "exit_code": 1,
            "output": "",
        }
    
    assigned_to = task_fm.get("assigned_to") or task_fm.get("agent_instance", "")
    if not assigned_to:
        return {
            "ok": False,
            "action_type": "claim",
            "message": f"Task {task_id} has no assigned_to or agent_instance",
            "command": "",
            "exit_code": 1,
            "output": "",
        }
    
    # 2. AIPOS-F73C件⑥返工 + F78 前置零①: token=驱动方(roles.schema driver), actor/agent_instance=该卡实例。
    #    claim 的 Supervised confirm 索 owner_confirm(驱动方 token 无此 scope) → 须由 Owner 信封
    #    (owner_autonomy_policy, F73D 已接)一阶段放行: 找覆盖本卡与驱动方身份的信封, 带 PreAuthorized + policy id。
    conn_arg = f"--connection-json {connection_json}" if connection_json else ""
    from tools.aipos_cli.loop_driver import DRIVER_ROLE, find_envelope  # 延迟导入(loop_driver 依赖本模块)

    driver_actor = _driver_actor(workspace_root, fallback=DRIVER_ROLE, connection_json=connection_json)
    policy, envelope_reasons = find_envelope(workspace_root, task_id=task_id, task_fm=task_fm, driver_actor=driver_actor,
                                             driver_role=_driver_role_name(workspace_root, connection_json))
    if policy is not None:
        policy_ref = str(policy.get("policy_id"))
        mode_arg = "--autonomy-mode PreAuthorized"
    else:
        # 无信封: 仍派生 Supervised 命令(门会索 owner_confirm 并拒), 拒因原文随 output 带出, 不静默
        policy_ref = _resolve_active_policy(workspace_root, task_id, role="advisor")
        mode_arg = ""
    policy_arg = f"--owner-policy-ref {policy_ref}" if policy_ref else ""

    command = f"lybra queue claim --task-id {task_id} --actor {assigned_to} --agent-instance {assigned_to} {mode_arg} {policy_arg} {conn_arg} --confirm"
    command = " ".join(command.split())
    if policy is None:
        return {
            "ok": False,
            "action_type": "claim",
            "message": "claim 阻塞: 无覆盖本卡与驱动方身份的 Owner 信封(驱动方 token 无 owner_confirm, Supervised 认领必撞)",
            "command": command,
            "exit_code": 5,
            "output": "\n".join(f"  - {r}" for r in envelope_reasons) or "5_tasks/policies/ 下没有任何信封",
        }
    
    # 4. 执行 claim
    try:
        result = subprocess.run(
            command.split(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        success = result.returncode == 0
        output = result.stdout + result.stderr
        
        if not success:
            return {
                "ok": False,
                "action_type": "claim",
                "message": f"claim 失败: {result.returncode}",
                "command": command,
                "exit_code": result.returncode,
                "output": output,
            }
        
        # 5. claim 成功后建 worktree
        worktree_result = _ensure_worktree(workspace_root, task_id)
        worktree_path = worktree_result.get("worktree_path", "")
        
        if not worktree_result.get("ok"):
            return {
                "ok": False,
                "action_type": "claim",
                "message": f"claim 成功但 worktree 失败: {worktree_result.get('message')}",
                "command": command,
                "exit_code": 1,
                "output": output + "\n" + worktree_result.get("message", ""),
                "worktree_path": "",
            }
        
        # 6. 构建 spawn_worker action
        spawn_action = {
            "type": "spawn_worker",
            "card": task_id,
            "worktree": worktree_path,
            "instance": assigned_to,
        }
        
        return {
            "ok": True,
            "action_type": "claim",
            "message": "claim 成功 + worktree 已建立",
            "command": command,
            "exit_code": 0,
            "output": output + f"\nWorktree: {worktree_path}",
            "worktree_path": worktree_path,
            "spawn_action": spawn_action,
        }
        
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "action_type": "claim",
            "message": "claim 超时 (>120s)",
            "command": command,
            "exit_code": 124,
            "output": "Command timed out after 120 seconds",
        }
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "action_type": "claim",
            "message": f"claim 执行异常: {exc}",
            "command": command,
            "exit_code": 1,
            "output": str(exc),
        }

def execute_derived_action(
    derivation: dict[str, Any],
    workspace_root: Path,
    connection_json: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F73件②③ + F73B件①: 执行推导出的下一步动作。
    
    铁律三条:
    1. 单步即退禁循环 (连接器病根禁复刻)
    2. 每步过门零旁路 (全部通过薄壳 CLI 执行)
    3. fail-closed 非零退出带拒因
    
    推导结果 → 执行:
    - pending → claim (AIPOS-F73B件①: 按角色 token 认领) + 建/复用 worktree + 输出 spawn_worker action
    - claimed + RETURN.md + 分支有提交 → return (执行 lybra queue return --confirm)
    - claimed + VERDICT → verdict (执行 lybra audit verdict --confirm)
    - returned + audit card → dispatch (执行 lybra audit dispatch --confirm)
    - verdict PASS → finalize --push --deploy → close
    
    Args:
        derivation: derive_next_step() 的返回结果
        workspace_root: 产品仓根目录
        connection_json: connection.json 路径 (可选)
    
    Returns:
        {
            "ok": bool,
            "action_type": str,  # "claim" | "return" | "verdict" | "dispatch" | "finalize" | "close" | "none"
            "message": str,
            "command": str,  # 实际执行的命令
            "exit_code": int,
            "output": str,
            "worktree_path": str | None,  # claim 时返回 worktree 路径
            "spawn_action": dict | None,  # claim 时输出 spawn_worker action
        }
    """
    import subprocess
    import shlex
    import json
    
    if not derivation.get("derivable"):
        return {
            "ok": False,
            "action_type": "none",
            "message": f"不可推导: {', '.join(derivation.get('missing_records', []))}",
            "command": "",
            "exit_code": 1,
            "output": "",
        }
    
    task_id = derivation.get("task_id", "")
    current_node = derivation.get("current_node", "")
    command = derivation.get("command", "").strip()
    
    # 如果推导出的命令是注释或空,说明需要人工介入
    if not command or command.startswith("#"):
        return {
            "ok": False,
            "action_type": "manual",
            "message": f"需要人工介入: {derivation.get('suggested_action', '?')}",
            "command": command,
            "exit_code": 0,
            "output": "",
        }
    
    # AIPOS-F73C前置零之一 + F73D: action_type 按命令真实动词, 唯一映射 _action_type_for_command(loop 共用)
    action_type = _action_type_for_command(command)
    
    # AIPOS-F73B件①: pending 卡特殊处理 — 按角色 token 认领
    if action_type == "claim" and current_node == "pending":
        return _execute_claim_with_role_token(
            task_id=task_id,
            workspace_root=workspace_root,
            connection_json=connection_json,
        )
    
    # AIPOS-F73件② + F78C: return 前先检查分支提交(卡分支活在该卡声明的产品仓 resolve_card_repo)
    if action_type == "return":
        from tools.aipos_cli.workspace_config import CardRepoUnresolved

        try:
            has_commits = _check_branch_has_commits(_card_repo_root(workspace_root, task_id), task_id)
        except CardRepoUnresolved as exc:
            return {
                "ok": False,
                "action_type": "return",
                "message": f"return 阻塞: 卡产品仓不可解析({exc.code})",
                "command": command,
                "exit_code": 1,
                "output": str(exc),
            }
        if not has_commits:
            return {
                "ok": False,
                "action_type": "return",
                "message": "return 阻塞: 分支无提交",
                "command": command,
                "exit_code": 1,
                "output": f"Branch card/{task_id} has no commits relative to main. Cannot return without commits.",
            }

    # AIPOS-F78 件③: return/verdict 步经产物入口(artifact_ingest.ingest_task_artifact): 读项目声明落点找 Return/裁决报告,
    # 校验必填 frontmatter 与分支 tip==commit_sha, 通过后才执行同一条薄壳命令(单一入口, 禁第二 return/verdict 路径)
    if action_type in ("return", "verdict"):
        from tools.aipos_cli.artifact_ingest import validate_task_artifact

        check = validate_task_artifact(task_id, workspace_root)
        if not check.get("ok"):
            return {
                "ok": False,
                "action_type": action_type,
                "message": f"{action_type} 阻塞: 产物入口校验拒(AIPOS-F78 件③): {check.get('category')}",
                "command": command,
                "exit_code": int(check.get("exit_code") or 4),
                "output": "\n".join(check.get("reasons") or []),
            }

    # AIPOS-F78B 件③: 账务动词(return/verdict/close)一律驱动方经信封一阶段放行; 派生命令无 PreAuthorized 形 = 无覆盖驱动方的信封
    # → exit 5 带申领出口(与 claim 步 _execute_claim_with_role_token 同款), 禁裸撞 Supervised 索 owner_confirm
    if action_type in ("return", "verdict", "close") and "--autonomy-mode PreAuthorized" not in command:
        from tools.aipos_cli.loop_driver import DRIVER_ROLE, exit_code_for, load_loop_contract, mint_hint

        task_path_for_hint, _q = _find_task_in_queue(workspace_root, task_id)
        hint = mint_hint(task_id=task_id, task_fm=_read_frontmatter(task_path_for_hint) if task_path_for_hint else {},
                         driver_actor=_driver_actor(workspace_root, fallback=DRIVER_ROLE, connection_json=connection_json))
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 阻塞: 无覆盖驱动方的有效 autonomy 信封(AIPOS-F78B 件③), 拒绝裸跑 Supervised",
            "command": command,
            "exit_code": exit_code_for(load_loop_contract(), "no_envelope"),
            "output": f"申领出口(Owner 亲自敲):\n  {hint}",
        }

    # AIPOS-F73件②③: 每步过门零旁路 — 执行产品 CLI
    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=120,  # 2分钟超时
        )
        
        success = result.returncode == 0
        output = result.stdout + result.stderr
        
        response = {
            "ok": success,
            "action_type": action_type,
            "message": f"{action_type} {'成功' if success else '失败'}",
            "command": command,
            "exit_code": result.returncode,
            "output": output,
        }
        
        return response
        
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 超时 (>120s)",
            "command": command,
            "exit_code": 124,
            "output": "Command timed out after 120 seconds",
        }
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 执行异常: {exc}",
            "command": command,
            "exit_code": 1,
            "output": str(exc),
        }
