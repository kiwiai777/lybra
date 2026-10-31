"""AIPOS-F256 — 执行分工收紧: 非代码一律 pi, 顾问子 agent 由产品限定只做代码卡(Owner 10-31 产品化第 5 项)。

一处声明(enums.schema task_mode.executor: 每个 task_mode 值的执行方 kind + 各 kind 接受的执行方类别 accepts) + 一个判定:
  - 执行方类别(classes: subagent / pi)按 harness 声明判(distribution.schema harness_semantics.kinds.<kind>.advisor_session), 不按 harness 名单;
    实例按执行方式声明(project.json execution)与接入登记(enrollment.instance_enrollment 唯一判定)判;
  - division_refusal = 五处同判(起草缺省 / 发卡核身份 / 认领 / 开工稿 / 交回入口)的唯一判定, 拒因带出口原文(话术读声明 texts), 不静默改派;
  - code_change_problem = 代码卡交回须在卡车道内相对认领基线有产品仓改动(件③ 防挂羊头, 交回入口调用);
  - surface_class_problem = 验证根 / 发布目标等 pi 专属写面的身份判定(roles.schema write_boundary.executor_class_surfaces; write_boundary.check_access 调用);
  - division_upgrade_step = `lybra project upgrade` 的迁移步(预演列出未声明 pi_executor / 在途违例卡与逐张出口, 确认只删退役键 pi_allowed_task_modes;
    既有卡与记录不改写)。
fail-closed: 声明缺 = SchemaLoadError; 未知 task_mode = 拒; 连接文件读不出 = ValueError 上抛(调用方拒, 不吞)。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _decl() -> dict[str, Any]:
    from tools.aipos_cli.next_resolver import task_mode_executor_declaration

    return task_mode_executor_declaration()


def harness_class(kind: Any) -> str | None:
    """harness kind → 执行方类别: 顾问会话 kind(advisor_session = true)= 子 agent 类, 否则 pi 类; 未知 / 空 = None(不可判)。"""
    from tools.aipos_cli.distribution_sync import declared_harness_kinds, harness_kind_declaration
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI, EXECUTION_MODE_SUBAGENT

    name = str(kind or "").strip()
    if not name or name not in declared_harness_kinds():
        return None
    return EXECUTION_MODE_SUBAGENT if harness_kind_declaration(name)["advisor_session"] else EXECUTION_MODE_PI


def instance_class(governance_root: str | Path, instance: Any, execution: dict[str, Any] | None) -> tuple[str | None, str]:
    """实例 → (执行方类别, 依据)。先执行方式声明(subagent_executor = 子 agent; pi_executor / auditor = pi), 再接入登记
    (凭据落治理根的执行者模式 = 子 agent; land 事件 harness 按 harness_class; 有工位位置 = pi); 都判不出 = (None, 原因)。连接文件读不出 = ValueError。"""
    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI, EXECUTION_MODE_SUBAGENT

    name = str(instance or "").strip()
    if not name:
        return None, "实例名空"
    if execution:
        if name == execution.get("subagent_executor"):
            return EXECUTION_MODE_SUBAGENT, "execution.subagent_executor"
        for key in ("pi_executor", "auditor"):
            if name == execution.get(key):
                return EXECUTION_MODE_PI, f"execution.{key}"
    view = instance_enrollment(Path(governance_root), name)
    if view["governance_root_mode"]:
        return EXECUTION_MODE_SUBAGENT, f"接入登记 mode={view['mode']}"
    cls = harness_class(view.get("harness"))
    if cls:
        return cls, f"接入登记 harness={view['harness']}"
    if view["workstation"].get("found"):
        return EXECUTION_MODE_PI, "有工位位置"
    return None, view.get("reason") or "接入登记判不出执行方类别"


def _execution(governance_root: Path) -> dict[str, Any] | None:
    from tools.aipos_cli.workspace_config import project_execution

    return project_execution(governance_root)


def card_executor(governance_root: str | Path, card: dict[str, Any], *, execution: dict[str, Any] | None, actor: str | None = None,
                  fallback_class: str | None = None) -> dict[str, Any]:
    """本次动作的执行方 → {actor, class, basis}: 给了 actor(认领者 / 交回者)且判得出 = 其类别; 否则卡面 harness(显式)→ assigned_to 实例;
    都判不出 = fallback_class(开工稿 = 子 agent 开工稿)或 None(不可判, 不适用)。"""
    gov = Path(governance_root)
    fm = card or {}
    name = str(actor or "").strip()
    if name:
        cls, basis = instance_class(gov, name, execution)
        if cls:
            return {"actor": name, "class": cls, "basis": basis}
    harness = str(fm.get("harness") or "").strip()
    assigned = str(fm.get("assigned_to") or "").strip()
    cls = harness_class(harness)
    if cls:
        return {"actor": name or assigned or "-", "class": cls, "basis": f"卡面 harness={harness}"}
    if assigned and assigned != name:
        cls, basis = instance_class(gov, assigned, execution)
        if cls:
            return {"actor": assigned, "class": cls, "basis": f"assigned_to {basis}"}
    return {"actor": name or assigned or "-", "class": fallback_class, "basis": "缺省类别" if fallback_class else "不可判"}


def _advisor(card: dict[str, Any]) -> str:
    from tools.aipos_cli.enrollment import card_dispatch_advisor

    return card_dispatch_advisor(card) or "<顾问实例>"


def refusal_exits(governance_root: str | Path, card: dict[str, Any], *, stage: str, execution: dict[str, Any] | None) -> list[str]:
    """拒因出口(话术读声明 texts): 未声明 pi_executor = 先接入并声明; 起草 / 发卡 = 改卡稿; 待认领卡 = 改派 amend; 已认领卡 = 撤卡重发;
    另给「确为代码工作改 task_mode」出口。"""
    texts = _decl()["texts"]
    gov = str(Path(governance_root))
    task_id = str(card.get("task_id") or "<卡ID>")
    pi = (execution or {}).get("pi_executor")
    fill = {"governance_root": gov, "task_id": task_id, "advisor": _advisor(card), "pi_executor": pi or "<pi 执行工位实例>",
            "subagent": (execution or {}).get("subagent_executor") or "<子 agent 执行者>",
            "auditor": (execution or {}).get("auditor") or "<pi 审计工位>"}
    exits: list[str] = [] if pi else [str(texts["exit_pi_undeclared"]).format(**fill)]
    if stage in ("draft", "publish"):
        exits.append(str(texts["exit_draft"]).format(**fill))
    elif stage == "claim":
        from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI

        amendments = json.dumps({"harness": EXECUTION_MODE_PI, "assigned_to": fill["pi_executor"], "agent_instance": fill["pi_executor"]},
                                ensure_ascii=False)
        exits.append(str(texts["exit_pending"]).format(**fill, amendments=amendments))
    else:
        exits.append(str(texts["exit_withdraw"]).format(**fill))
    exits.append(str(texts["exit_mode"]))
    return exits


def division_refusal(governance_root: str | Path, card: dict[str, Any], *, stage: str, actor: str | None = None,
                     execution: Any = ..., fallback_class: str | None = None) -> dict[str, Any] | None:
    """五处同判的唯一判定。None = 放行(执行方类别 ∈ 该模式 accepts, 或执行方不可判且无缺省类别); 否则 {code, message, exits, required,
    executor}。未知 task_mode = EXECUTOR_TASK_MODE_UNKNOWN(fail-closed)。execution 缺省 = 读 project.json(形坏 = ExecutionDeclarationError 上抛)。"""
    from tools.aipos_cli.next_resolver import task_mode_executor
    from tools.schema_loader import get_enum_values

    decl = _decl()
    texts, stages = decl["texts"], decl["stages"]
    if stage not in stages:
        raise ValueError(f"未声明的判定处 {stage!r}(enums.schema task_mode.executor.stages: {sorted(stages)})")
    gov = Path(governance_root)
    fm = card or {}
    task_id = str(fm.get("task_id") or "<卡ID>")
    mode = str(fm.get("task_mode") or "").strip()
    kind = task_mode_executor(mode)
    if kind is None:
        return {"code": "EXECUTOR_TASK_MODE_UNKNOWN", "exits": [], "required": None, "executor": None,
                "message": str(texts["unknown"]).format(task_id=task_id, task_mode=mode, values=list(get_enum_values("task_mode")))}
    if execution is ...:
        execution = _execution(gov)
    who = card_executor(gov, fm, execution=execution, actor=actor, fallback_class=fallback_class)
    spec = decl["kinds"][kind]
    if who["class"] is None or who["class"] in spec["accepts"]:
        return None
    exits = refusal_exits(gov, fm, stage=stage, execution=execution)
    message = str(texts["refusal"]).format(code="EXECUTOR_DIVISION_REFUSED", task_id=task_id, task_mode=mode or "(缺)",
                                           required_label=spec["label"], stage_label=stages[stage], actor=who["actor"],
                                           actor_class=who["class"], basis=who["basis"], exits="; ".join(exits))
    return {"code": "EXECUTOR_DIVISION_REFUSED", "message": message, "exits": exits, "required": kind, "executor": who}


def pi_undeclared_refusal(governance_root: str | Path, card: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    """卡须由 pi 执行工位做而项目未声明 execution.pi_executor(起草缺省派不出)= EXECUTOR_PI_UNDECLARED(出口 = 接入 + 声明 + 改卡稿)。"""
    texts = _decl()["texts"]
    exits = refusal_exits(governance_root, card, stage="draft", execution=execution)
    return {"code": "EXECUTOR_PI_UNDECLARED", "exits": exits,
            "message": str(texts["pi_undeclared"]).format(task_id=str(card.get("task_id") or "<卡ID>"),
                                                          task_mode=str(card.get("task_mode") or "(缺)"), exits="; ".join(exits))}


# ---------------------------------------------------------------------------
# 件③ 代码卡交回须有车道内产品仓改动
# ---------------------------------------------------------------------------

def _git(repo: Path, *argv: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def code_change_problem(governance_root: str | Path, task_id: str, card: dict[str, Any], *, code_repo: Path, commit_sha: str) -> str | None:
    """执行方 kind ∈ code_change.kinds 的卡分支交回: 认领基线 = git merge-base <基线分支> <commit_sha>(next_resolver.card_base_branch 唯一读取口),
    车道 = 卡 lane.paths(缺 = 全仓); 车道内 diff 空 = 拒因原文(INGEST_CODE_CHANGE_EMPTY); 非该类模式 / 有改动 = None。基线算不出 = 拒因(fail-closed)。"""
    from tools.aipos_cli.next_resolver import card_base_branch, task_mode_executor

    decl = _decl()
    kind = task_mode_executor(str(card.get("task_mode") or ""))
    if kind not in decl["code_change"]["kinds"]:
        return None
    lane = card.get("lane") if isinstance(card.get("lane"), dict) else {}
    paths = [str(p).strip() for p in (lane.get("paths") or []) if str(p).strip()]
    base_ref = card_base_branch()
    base = _git(code_repo, "merge-base", base_ref, commit_sha)
    fill = {"task_id": task_id, "commit_sha": commit_sha, "base_ref": base_ref, "repo": str(code_repo), "paths": paths or ["(全仓)"]}
    if base is None:
        return (f"AIPOS-F256 INGEST_CODE_CHANGE_EMPTY: 代码卡 {task_id} 交回 {commit_sha} 与基线分支 {base_ref} 在 {code_repo} 无共同祖先 / "
                "基线读不出, 车道内改动不可核(fail-closed)。出口: 卡分支须基于基线分支")
    changed = _git(code_repo, "diff", "--name-only", f"{base}..{commit_sha}", "--", *(paths or ["."]))
    if changed:
        return None
    exits = refusal_exits(governance_root, {**card, "task_id": task_id}, stage="return", execution=_execution(Path(governance_root)))
    return str(decl["texts"]["code_change_empty"]).format(**fill, base=base, exits="; ".join(exits))


# ---------------------------------------------------------------------------
# pi 专属写面(验证根 / 发布目标等; 声明 roles.schema write_boundary.executor_class_surfaces)
# ---------------------------------------------------------------------------

def surface_class_declaration() -> dict[str, Any]:
    """roles.schema write_boundary.executor_class_surfaces(surfaces / classes / code / reason)。缺 / 形变 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("roles").get("write_boundary") or {}).get("executor_class_surfaces")
    if (not isinstance(decl, dict) or not isinstance(decl.get("surfaces"), list) or not isinstance(decl.get("classes"), list)
            or not all(isinstance(decl.get(k), str) and decl[k] for k in ("code", "reason"))):
        raise SchemaLoadError("roles.schema.json write_boundary.executor_class_surfaces(surfaces / classes / code / reason)未声明齐(AIPOS-F256)")
    return decl


def surface_class_problem(governance_root: str | Path, *, surface: str | None, instance: str | None, task_id: str | None) -> dict[str, Any] | None:
    """写访问落在 pi 专属面时的身份判定: 写者 = instance, 缺 = 本卡认领者(claimed_by / assigned_to); 判得出且类别不在 classes = 拒
    {code, reason}; 面不在声明内 / 写者不可判 = None(既有判据照旧)。"""
    decl = surface_class_declaration()
    if surface not in decl["surfaces"]:
        return None
    gov = Path(governance_root)
    name = str(instance or "").strip()
    card: dict[str, Any] = {}
    if task_id:
        from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

        path, _queue = _find_task_in_queue(gov, str(task_id))
        card = _read_frontmatter(path) if path else {}
    if not name:
        name = str(card.get("claimed_by") or card.get("assigned_to") or "").strip()
    if not name:
        return None
    cls, basis = instance_class(gov, name, _execution(gov))
    if cls is None or cls in decl["classes"]:
        return None
    return {"code": str(decl["code"]), "reason": str(decl["reason"]).format(surface=surface, instance=name, cls=cls, basis=basis,
                                                                              classes=list(decl["classes"]))}


# ---------------------------------------------------------------------------
# 件④ 存量与接入: project upgrade 迁移步 + 在途违例卡清单
# ---------------------------------------------------------------------------

def division_violations(governance_root: str | Path) -> dict[str, Any]:
    """只读盘点: {execution, pi_executor_missing, retired_key, noncode_cards, violations[{task_id, state, message, exits}], errors}。
    在途 = 非终态队列目录(task_loader.iter_queue_task_paths, 状态集读 enums queue_state 声明); 违例 = division_refusal(认领 / 交回口径)非空。"""
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.next_resolver import task_mode_executor
    from tools.aipos_cli.task_loader import QUEUE_STATES, QUEUE_TERMINAL_STATES, iter_queue_task_paths
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_SUBAGENT, read_project_json

    gov = Path(governance_root)
    execution = _execution(gov)
    raw = (read_project_json(gov).get("execution") or {})
    out: dict[str, Any] = {"execution": execution, "retired_key": isinstance(raw, dict) and "pi_allowed_task_modes" in raw,
                           "retired_value": raw.get("pi_allowed_task_modes") if isinstance(raw, dict) else None,
                           "pi_executor_missing": False, "noncode_cards": 0, "violations": [], "errors": []}
    accepts_subagent = {k for k, v in _decl()["kinds"].items() if EXECUTION_MODE_SUBAGENT in v["accepts"]}
    in_flight = tuple(s for s in QUEUE_STATES if s not in QUEUE_TERMINAL_STATES)
    for path in iter_queue_task_paths(gov, states=in_flight):
        try:
            meta, _body, _warn = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            out["errors"].append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        card = {**meta, "task_id": str(meta.get("task_id") or path.stem)}
        if task_mode_executor(card.get("task_mode")) not in accepts_subagent:
            out["noncode_cards"] += 1
        claimer = str(card.get("claimed_by") or "").strip() or None
        try:  # 已认领 = 交回口径(出口撤卡重发); 未认领 = 认领口径(出口改派 amend)
            refusal = division_refusal(gov, card, stage="return" if claimer else "claim", actor=claimer, execution=execution)
        except ValueError as exc:
            out["errors"].append(f"{card['task_id']}: {exc}")
            continue
        if refusal is not None:
            out["violations"].append({"task_id": card["task_id"], "state": path.parent.name, "code": refusal["code"],
                                      "message": refusal["message"], "exits": refusal["exits"]})
    out["pi_executor_missing"] = execution is not None and not execution.get("pi_executor")
    return out


def division_upgrade_step(governance_root: str | Path, *, dry_run: bool = True) -> dict[str, Any]:
    """`lybra project upgrade` 迁移步(声明 verbs.schema lybra_project_upgrade.upgrade_steps execution_division): 预演 = 盘点(零写入);
    确认(dry_run=False)= 只删退役键 execution.pi_allowed_task_modes(经 update_project_json 唯一写路径), 卡与记录不改写。
    返回 {execution, retired_key, retired_value, removed, project_json, written, pi_executor_missing, violations, noncode_cards, errors}。"""
    from tools.aipos_cli.workspace_config import project_execution, project_json_path, update_project_json

    gov = Path(governance_root)
    view = division_violations(gov)
    out = {**{k: view[k] for k in ("execution", "retired_key", "retired_value", "pi_executor_missing", "violations", "noncode_cards",
                                   "errors")},
           "removed": False, "written": False, "project_json": str(project_json_path(gov))}
    if dry_run or not view["retired_key"]:
        return out

    def _mutate(data: dict[str, Any]) -> None:  # update_project_json 原地修改约定
        section = data.get("execution")
        if isinstance(section, dict):
            section.pop("pi_allowed_task_modes", None)

    outcome = update_project_json(gov, _mutate, verify=project_execution, dry_run=False)
    return {**out, "removed": bool(outcome.get("changed")), "written": bool(outcome.get("written"))}


def division_summary(governance_root: str | Path) -> dict[str, Any]:
    """`lybra roles list` 只读诊断段(文本 lines 与 --json 同读; 判定同 division_violations): pi 执行工位声明 + 在途违例卡逐张出口。
    声明 / 卡读不出 = lines 点名原因(只读诊断不拒, 不吞)。"""
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError

    try:
        view = division_violations(governance_root)
    except (ExecutionDeclarationError, ValueError, OSError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "lines": [f"执行分工(AIPOS-F256: 非代码一律 pi): 读不出 —— {type(exc).__name__}: {exc}"]}
    execution = view["execution"] or {}
    pi = execution.get("pi_executor")
    head = (f"执行分工(AIPOS-F256: 非代码一律 pi, 子 agent 只接代码卡): pi 执行工位 = {pi}" if pi else
            "执行分工(AIPOS-F256: 非代码一律 pi, 子 agent 只接代码卡): 未声明 pi 执行工位" + (
                f" —— 出口: {refusal_exits(governance_root, {}, stage='draft', execution=view['execution'])[0]}" if view["execution"] else
                "(项目未声明 execution)"))
    lines = [head]
    if view["retired_key"]:
        lines.append("  execution.pi_allowed_task_modes 已退役(不再改变路由); lybra project upgrade --confirm 删除")
    for v in view["violations"]:
        lines.append(f"  ✗ 在途违例 {v['task_id']}({v['state']}, {v['code']}): 出口 {'; '.join(v['exits']) or v['message']}")
    lines += [f"  读不出: {e}" for e in view["errors"]]
    return {"ok": True, "pi_executor": pi, "retired_key": view["retired_key"], "noncode_cards": view["noncode_cards"],
            "violations": view["violations"], "errors": view["errors"], "lines": lines}

