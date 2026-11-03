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


def claim_evidence(governance_root: str | Path, card: dict[str, Any]) -> str | None:
    """AIPOS-F256B 件⑤: 卡已认领的凭据(enums.schema task_mode.executor.transition.evidence 序): 门生 / 存量 claim 记录(records claims/<ID>/claim_*.md)
    → 卡面 claimed_by → 卡在非待认领的在途队列目录(人肉期存量卡常无前两者)。都无 = None(未认领)。多义卡 = AmbiguousTaskCard 上抛(fail-closed)。"""
    from tools.aipos_cli.next_resolver import _find_task_in_queue
    from tools.aipos_cli.record_writer import record_dir
    from tools.aipos_cli.task_loader import QUEUE_STATES, QUEUE_TERMINAL_STATES

    gov = Path(governance_root)
    task_id = str((card or {}).get("task_id") or "").strip()
    if not task_id:
        return None
    try:
        claims = record_dir(gov, "claims", task_id)
    except ValueError:
        claims = None
    if claims is not None and claims.is_dir():
        found = sorted(claims.glob("claim_*.md"))
        if found:
            return f"claim 记录 {found[0].name}"
    claimed_by = str((card or {}).get("claimed_by") or "").strip()
    if claimed_by:
        return f"卡面 claimed_by={claimed_by}"
    in_flight = tuple(q for q in QUEUE_STATES if q not in QUEUE_TERMINAL_STATES)
    _path, queue = _find_task_in_queue(gov, task_id)
    if queue in in_flight[1:]:  # 首个在途目录(声明序)= 待认领, 其余 = 已认领后的在途态
        return f"队列目录 {queue}"
    return None


def division_transition(governance_root: str | Path, card: dict[str, Any], *, actor: str | None = None, execution: Any = ...,
                        fallback_class: str | None = None) -> dict[str, Any] | None:
    """AIPOS-F256B 件⑤: 过渡在途卡判定(唯一实现; 判定处 division_refusal / 写面 surface_class_problem / 只读诊断同读)。
    = 认领后口径(交回)下新规则会拒(严格判定非空)且卡已认领(claim_evidence)→ {code, message, evidence, refusal}; 否则 None。"""
    strict = _strict_refusal(governance_root, card, stage="return", actor=actor, execution=execution, fallback_class=fallback_class)
    if strict is None or strict["code"] in _capability_codes():  # AIPOS-F284: 能力卡不走过渡
        return None
    evidence = claim_evidence(governance_root, card)
    if evidence is None:
        return None
    who = strict.get("executor") or {}
    message = str(_decl()["texts"]["transition"]).format(
        task_id=str(card.get("task_id") or "<卡ID>"), task_mode=str(card.get("task_mode") or "(缺)"), evidence=evidence,
        actor=who.get("actor") or str(card.get("claimed_by") or card.get("assigned_to") or "-"), code=strict["code"])
    return {"code": strict["code"], "message": message, "evidence": evidence, "refusal": strict}


def division_refusal(governance_root: str | Path, card: dict[str, Any], *, stage: str, actor: str | None = None,
                     execution: Any = ..., fallback_class: str | None = None) -> dict[str, Any] | None:
    """五处同判的唯一判定。None = 放行(执行方类别 ∈ 该模式 accepts, 或执行方不可判且无缺省类别, 或 AIPOS-F256B 件⑤ 过渡在途:
    认领后各处(transition.stages)且卡已认领 = 按认领时执行方继续); 否则 {code, message, exits, required, executor}。
    未知 task_mode = EXECUTOR_TASK_MODE_UNKNOWN(fail-closed; 已认领的人肉期存量卡同按过渡)。execution 缺省 = 读 project.json。
    AIPOS-F284 能力例外(卡带 requires_capabilities)的拒因不走过渡(能力卡认领即按能力判, 不存在规则前认领的能力违例卡)。"""
    refusal = _strict_refusal(governance_root, card, stage=stage, actor=actor, execution=execution, fallback_class=fallback_class)
    if (refusal is not None and refusal["code"] not in _capability_codes() and stage in _decl()["transition"]["stages"]
            and claim_evidence(governance_root, card or {}) is not None):
        return None
    return refusal


# ---------------------------------------------------------------------------
# AIPOS-F284 能力例外: 卡声明所需主机能力(requires_capabilities)= 「非代码一律 pi」的唯一声明化例外
# (声明 enums.schema host_capability; 判定只在 _strict_refusal 一处, 五处同判经 division_refusal)
# ---------------------------------------------------------------------------

def _cap_decl() -> dict[str, Any]:
    from tools.aipos_cli.next_resolver import host_capability_declaration

    return host_capability_declaration()


def _capability_codes() -> frozenset[str]:
    return frozenset(("EXECUTOR_CAPABILITY_UNDECLARED", "EXECUTOR_CAPABILITY_REFUSED", "CARD_CAPABILITIES_INVALID"))


def capability_text(key: str, values: dict[str, Any]) -> str:
    """host_capability.texts 一条文案 → 双语渲染值(AIPOS-F276 / F277 唯一读取器: str 本身 = 缺省语言, 出声点按用户语言 i18n.pick)。"""
    from tools.aipos_cli.i18n import block_localize

    return block_localize(_cap_decl()["texts"], key, values, where="enums.schema.json enums.host_capability.texts")


def capability_labels(capabilities: list[str]) -> str:
    """能力值 → 各语言标签串(「图形会话(gui_session), …」)的双语渲染值。"""
    from tools.aipos_cli.i18n import Localized, default_language

    block = _cap_decl()["texts"]
    by_lang = {lang: ", ".join(f"{table['labels'].get(c, c)}({c})" if lang == default_language() else f"{table['labels'].get(c, c)} ({c})"
                               for c in capabilities) for lang, table in block.items()}
    return Localized(by_lang, default_language())


def card_capabilities(card: dict[str, Any] | None) -> list[str]:
    """卡面所需主机能力(card.schema requires_capabilities; 值 ∈ enums.schema host_capability)唯一形校验(出卡 draft_validator 与判定同调)。
    缺 / [] = []; 形不合 = ValueError(消息 = CARD_CAPABILITIES_INVALID 双语渲染值)。去重保序。"""
    from tools.schema_loader import get_enum_values

    decl = _cap_decl()
    field = str(decl["card_field"])
    value = (card or {}).get(field) if isinstance(card, dict) else None
    if value is None or value == []:
        return []
    allowed = list(get_enum_values("host_capability"))
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in allowed for v in value):
        raise ValueError(capability_text("invalid", {"field": field, "allowed": allowed, "value": repr(value), "example": json.dumps(allowed[0])}))
    out: list[str] = []
    for item in value:
        if item not in out:
            out.append(item)
    return out


def capability_exception(governance_root: str | Path, card: dict[str, Any] | None) -> dict[str, Any] | None:
    """卡是否适用能力例外: 卡带 requires_capabilities 且执行方 kind 不在 exempt_kinds(审计卡照旧 pi)= {capabilities, instances[{instance,
    host, capabilities}](项目声明里具备全部所需能力者, 声明序), labels}; 否则 None。卡面形不合 = ValueError; 执行声明形坏 =
    ExecutionDeclarationError(调用方 fail-closed)。"""
    from tools.aipos_cli.next_resolver import task_mode_executor
    from tools.aipos_cli.workspace_config import project_capability_executors

    caps = card_capabilities(card)
    if not caps:
        return None
    kind = task_mode_executor(str((card or {}).get("task_mode") or ""))
    if kind is None or kind in _cap_decl()["exempt_kinds"]:
        return None
    declared = project_capability_executors(Path(governance_root))
    instances = [{"instance": name, **entry} for name, entry in declared.items() if set(caps) <= set(entry["capabilities"])]
    return {"capabilities": caps, "instances": instances, "declared": declared, "labels": capability_labels(caps)}


def _capability_refusal(governance_root: Path, card: dict[str, Any], cap: dict[str, Any], *, stage: str, actor: str | None,
                        execution: dict[str, Any] | None, fallback_class: str | None) -> dict[str, Any] | None:
    """能力例外下的执行方判定(只经 _strict_refusal 调用): 无实例具备 = EXECUTOR_CAPABILITY_UNDECLARED; 执行方 ∈ 具备能力的实例 = 放行;
    执行方有名而不在其中 / 执行方属 pi 类 = EXECUTOR_CAPABILITY_REFUSED(出口按判定处); 执行方不可判 = 放行(同既有口径)。"""
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI

    task_id = str(card.get("task_id") or "<卡ID>")
    field = str(_cap_decl()["card_field"])
    caps, names = cap["capabilities"], [i["instance"] for i in cap["instances"]]
    if not names:
        set_execution = (f"lybra project set-execution --workspace-root {governance_root} --subagent-executor "
                         f"{(execution or {}).get('subagent_executor') or '<subagent-executor>'} --auditor {(execution or {}).get('auditor') or '<pi-auditor>'}"
                         + (f" --pi-executor {execution['pi_executor']}" if (execution or {}).get("pi_executor") else ""))
        message = capability_text("undeclared", {"task_id": task_id, "capabilities": cap["labels"], "capability_values": ",".join(caps),
                                           "declared": json.dumps(cap["declared"], ensure_ascii=False) if cap["declared"] else "{}",
                                           "set_execution": set_execution, "field": field})
        return {"code": "EXECUTOR_CAPABILITY_UNDECLARED", "message": message, "exits": [message], "required": "capability", "executor": None,
                "capabilities": caps}
    who = card_executor(governance_root, card, execution=execution, actor=actor, fallback_class=fallback_class)
    named = who["actor"] not in ("", "-")
    if who["actor"] in names or (not named and who["class"] != EXECUTION_MODE_PI):
        return None
    instance = names[0]
    from tools.aipos_cli.enrollment import card_dispatch_advisor

    fill = {"governance_root": str(governance_root), "task_id": task_id, "advisor": card_dispatch_advisor(card) or "<advisor-instance>",
            "instance": instance}
    if stage in ("draft", "publish"):
        exit_text = capability_text("exit_draft", fill)
    elif stage == "claim":
        harness = str(card.get("harness") or "").strip()
        amend: dict[str, Any] = {"assigned_to": instance, "agent_instance": instance}
        if harness == EXECUTION_MODE_PI:
            from tools.aipos_cli.enrollment import instance_enrollment

            amend["harness"] = instance_enrollment(governance_root, instance).get("harness") or "<advisor-session-harness>"
        exit_text = capability_text("exit_pending", {**fill, "amendments": json.dumps(amend, ensure_ascii=False)})
    else:
        exit_text = capability_text("exit_withdraw", fill)
    message = capability_text("refused", {"task_id": task_id, "capabilities": cap["labels"], "instances": names, "actor": who["actor"],
                                    "stage_label": _stage_label(stage), "exit": exit_text})
    return {"code": "EXECUTOR_CAPABILITY_REFUSED", "message": message, "exits": [exit_text], "required": "capability", "executor": who,
            "capabilities": caps}


def _stage_label(stage: str) -> str:
    from tools.aipos_cli.i18n import Localized, default_language

    return Localized({lang: str(table["stages"][stage]) for lang, table in _cap_decl()["texts"].items()}, default_language())


def capability_assignee(governance_root: str | Path, card: dict[str, Any], *, execution: dict[str, Any] | None) -> dict[str, Any] | None:
    """起草缺省用: 卡适用能力例外 = {instance, refusal}(instance = 具备全部能力的首个实例; 无 = refusal 为 UNDECLARED 拒因); 否则 None。"""
    cap = capability_exception(governance_root, card)
    if cap is None:
        return None
    if not cap["instances"]:
        return {"instance": None, "refusal": _capability_refusal(Path(governance_root), card, cap, stage="draft", actor=None,
                                                                 execution=execution, fallback_class=None)}
    return {"instance": cap["instances"][0]["instance"], "refusal": None}


def capability_trace(governance_root: str | Path, card: dict[str, Any] | None, actor: str | None) -> str | None:
    """认领 / 交回记录留痕(record_field)一行: 卡适用能力例外且执行方 = 具备能力的实例 = 「能力例外: requires_capabilities=…, 实例=…」
    双语渲染值(落盘为缺省语言); 否则 None。卡面 / 声明形坏 = 异常上抛(记录不写半截)。"""
    cap = capability_exception(governance_root, card)
    name = str(actor or "").strip()
    if cap is None or not name:
        return None
    entry = next((i for i in cap["instances"] if i["instance"] == name), None)
    if entry is None:
        return None
    return capability_text("trace", {"capability_values": ",".join(cap["capabilities"]), "instance": name, "host": entry["host"]})


def capability_line(governance_root: str | Path, card: dict[str, Any] | None, *, kind: str, instance: str | None = None,
                    lang: str | None = None) -> str | None:
    """开工稿(kind=kickoff_line)/ loop 等待产物步手工提示(kind=manual_hint)的能力一行, 按 lang 取那种语言; 不适用 = None。
    实例 = 给定且具备能力者, 否则具备能力的首个实例。"""
    from tools.aipos_cli.i18n import pick

    cap = capability_exception(governance_root, card)
    if cap is None or not cap["instances"]:
        return None
    entry = next((i for i in cap["instances"] if i["instance"] == str(instance or "").strip()), cap["instances"][0])
    return pick(capability_text(kind, {"capabilities": cap["labels"], "host": entry["host"], "instance": entry["instance"]}), lang)


def _strict_refusal(governance_root: str | Path, card: dict[str, Any], *, stage: str, actor: str | None = None,
                    execution: Any = ..., fallback_class: str | None = None) -> dict[str, Any] | None:
    """新规则的严格判定(不含过渡; 只经 division_refusal / division_transition 调用)。"""
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
    cap = capability_exception(gov, fm)  # AIPOS-F284: 能力例外(唯一声明化例外; 卡面形不合 = ValueError 上抛, 调用方 fail-closed)
    if cap is not None:
        return _capability_refusal(gov, fm, cap, stage=stage, actor=actor, execution=execution, fallback_class=fallback_class)
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
    execution = _execution(gov)
    cls, basis = instance_class(gov, name, execution)
    if cls is None or cls in decl["classes"]:
        return None
    if card and name in {str(card.get(k) or "").strip() for k in ("claimed_by", "assigned_to", "agent_instance")} and \
            division_transition(gov, {**card, "task_id": str(task_id)}, actor=name, execution=execution) is not None:
        return None  # AIPOS-F256B 件⑤: 过渡在途卡的写面按认领时执行方继续
    if card and capability_trace(gov, {**card, "task_id": str(task_id)}, name) is not None:
        return None  # AIPOS-F284: 能力卡的写面放行其具备能力的执行实例(写面范围不变, 只放行身份类别)
    return {"code": str(decl["code"]), "reason": str(decl["reason"]).format(surface=surface, instance=name, cls=cls, basis=basis,
                                                                              classes=list(decl["classes"]))}


# ---------------------------------------------------------------------------
# 件④ 存量与接入: project upgrade 迁移步 + 在途违例卡清单
# ---------------------------------------------------------------------------

def division_violations(governance_root: str | Path) -> dict[str, Any]:
    """只读盘点: {execution, pi_executor_missing, retired_key, noncode_cards, violations[{task_id, state, message, exits}],
    transitions[{task_id, state, code, message, evidence}](AIPOS-F256B 件⑤ 过渡在途信息项), errors,
    subagent_mode_pending(AIPOS-F261 件②: 已声明的子 agent 执行者最新登记不是子 agent 模式 = {instance, how, command, message}, 否则 None)}。
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
                           "pi_executor_missing": False, "noncode_cards": 0, "violations": [], "transitions": [], "errors": []}
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
        waiting = path.parent.name == in_flight[0]  # 首个非终态队列目录(声明序 = 待认领): 认领口径(出口改派 amend)
        try:  # 其余在途目录 = 认领后口径: AIPOS-F256B 件⑤ 已认领的违例卡 = 过渡在途(信息项, 不拒), 否则交回口径
            transition = None if waiting else division_transition(gov, card, actor=claimer, execution=execution)
            refusal = None if transition else division_refusal(gov, card, stage="claim" if waiting else "return", actor=claimer,
                                                               execution=execution)
        except ValueError as exc:
            out["errors"].append(f"{card['task_id']}: {exc}")
            continue
        if transition is not None:
            out["transitions"].append({"task_id": card["task_id"], "state": path.parent.name, "code": transition["code"],
                                       "message": transition["message"], "evidence": transition["evidence"]})
        if refusal is not None:
            out["violations"].append({"task_id": card["task_id"], "state": path.parent.name, "code": refusal["code"],
                                      "message": refusal["message"], "exits": refusal["exits"]})
    out["pi_executor_missing"] = execution is not None and not execution.get("pi_executor")
    out["subagent_mode_pending"] = None
    if execution and execution.get("subagent_executor"):
        # AIPOS-F261 件②: 已声明的子 agent 执行者最新登记不是子 agent 模式 = 待处理项(只列出口, 不自动改; 判定唯一 enrollment.subagent_mode_mismatch)
        from tools.aipos_cli.enrollment import execution_texts, subagent_mode_mismatch

        try:
            mismatch = subagent_mode_mismatch(gov, str(execution["subagent_executor"]))
        except ValueError as exc:  # 连接文件读不出 = 如实记入读不出(只读盘点不拒, 不吞)
            out["errors"].append(f"subagent_executor={execution['subagent_executor']}: 接入状态不可得: {exc}")
            mismatch = None
        if mismatch is not None:
            out["subagent_mode_pending"] = {**mismatch, "message": str(execution_texts()["pending_item"]).format(**mismatch)}
    return out


def division_upgrade_step(governance_root: str | Path, *, dry_run: bool = True) -> dict[str, Any]:
    """`lybra project upgrade` 迁移步(声明 verbs.schema lybra_project_upgrade.upgrade_steps execution_division): 预演 = 盘点(零写入);
    确认(dry_run=False)= 只删退役键 execution.pi_allowed_task_modes(经 update_project_json 唯一写路径), 卡与记录不改写。
    返回 {execution, retired_key, retired_value, removed, project_json, written, pi_executor_missing, violations, noncode_cards, errors}。"""
    from tools.aipos_cli.workspace_config import project_execution, project_json_path, update_project_json

    gov = Path(governance_root)
    view = division_violations(gov)
    out = {**{k: view[k] for k in ("execution", "retired_key", "retired_value", "pi_executor_missing", "violations", "transitions",
                                   "noncode_cards", "errors", "subagent_mode_pending")},
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
    from tools.aipos_cli.workspace_config import project_capability_executors

    for name, entry in project_capability_executors(Path(governance_root)).items():  # AIPOS-F284: 能力执行实例(声明序)
        lines.append("  " + capability_text("summary_line", {"instance": name, "host": entry["host"], "capabilities": capability_labels(entry["capabilities"])}))
    if view["subagent_mode_pending"]:  # AIPOS-F261 件②
        lines.append(f"  ! {view['subagent_mode_pending']['message']}")
    if view["retired_key"]:
        lines.append("  execution.pi_allowed_task_modes 已退役(不再改变路由); lybra project upgrade --confirm 删除")
    for v in view["violations"]:
        lines.append(f"  ✗ 在途违例 {v['task_id']}({v['state']}, {v['code']}): 出口 {'; '.join(v['exits']) or v['message']}")
    for t in view["transitions"]:  # AIPOS-F256B 件⑤: 信息项, 不拒
        lines.append(f"  ~ 过渡在途 {t['task_id']}({t['state']}): {t['message']}")
    lines += [f"  读不出: {e}" for e in view["errors"]]
    return {"ok": True, "pi_executor": pi, "retired_key": view["retired_key"], "noncode_cards": view["noncode_cards"],
            "subagent_mode_pending": view["subagent_mode_pending"],
            "violations": view["violations"], "transitions": view["transitions"], "errors": view["errors"], "lines": lines}



from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
