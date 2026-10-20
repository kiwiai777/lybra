"""AIPOS-F198 — 工位设置: 每个项目的执行方式与工位现状 + 目标分工的切换计划与「告诉顾问」话术(`lybra project workstation-plan` 与看板同一推导)。

一句话: 看清现在谁在做哪类卡、哪些工位没接好; 选好想要的分工后, 生成交给顾问的切换提示词, 里面全是按依赖排好的产品命令(先预演后确认),
看板与本命令都不改任何东西(只读: 不写文件、不连门、不取凭据值)。

防碎片化(卡面硬约束), 本模块只编排, 各面只调既有唯一实现:
- 执行方式 = workspace_config.project_execution; 卡类型派给谁 = workspace_config.card_execution_mode + next_resolver.task_mode_independent_audit;
- 接入 = enrollment.instance_enrollment(工位位置 = 其 workstation_location); 覆盖信封 = workstation_wiring.derive_effective_owner_policy_ref
  (接入预检 enroll_envelope_requirement 同一推导), 缺信封出口命令 = workstation_wiring.envelope_mint_exit;
- 章程状态 = distribution_sync.charter_local_state(sync --dry-run 同一判据 + hand_maintained_charters 手写判定; 不连门);
  章程命令 = distribution_sync.render_sync_command + hand_maintained_guard 声明的采纳旗标;
- 目标主机 / 验证根 = validation_root.project_validation_hosts / project_validation_roots / declaration_status / active_run;
- 工位候选 = distribution_sync.discover_workstations + charter_render.workstation_identity(实例项目段 = naming_profile 声明);
- 决策记录落点 = governance_add.decision_entry_path(`lybra governance add decision` 同一命名);
- 步骤 / 命令模板 / 话术(中英)读 verbs.schema verbs.lybra_project_workstation_plan(declaration 唯一读取口, fail-closed)。
任何项目同一组命令: 不写死项目 ID / 路径 / 卡号前缀 / 实例名。
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

VERB = "lybra_project_workstation_plan"
STEP_NAMES = ("decision", "envelope", "enroll", "charter_adopt", "charter_sync", "execution", "ssh_host", "verify", "notify")
STEP_STATES = ("pending", "done", "na", "check")
#: 会改东西的步骤(其一待办 = 有切换要做; 决策记录与通知随之待办)
_CHANGE_STEPS = ("envelope", "enroll", "charter_adopt", "charter_sync", "execution", "ssh_host")
_COMMAND_KEYS = ("decision_preview", "decision_confirm", "decision_land", "enroll_preview", "enroll_confirm", "execution_preview",
                 "execution_confirm", "ssh_host_preview", "ssh_host_confirm", "verify")
_PLACEHOLDER_KEYS = ("advisor", "policy", "workspace", "pi_executor", "subagent", "auditor")
_STEP_TEXT_KEYS = ("label", "why", "expect", "refused")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._@-]+$")


class TargetError(ValueError):
    """目标分工输入不合法(消息 = 声明文案渲染后的人话)。"""


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_project_workstation_plan(fail-closed): 步骤恰为 STEP_NAMES 依序(只追加须先实现); 命令模板 / 占位 / 两种语言文案
    (含每步 label·why·expect·refused 与各卡类型名)齐; 可选卡类型 ⊆ 路由卡类型 ⊆ enums task_mode; 看板页路径与参数齐。"""
    from tools.schema_loader import SchemaLoadError, get_enum_values, load_schema

    decl = (load_schema("verbs").get("verbs") or {}).get(VERB)
    where = f"verbs.schema.json verbs.{VERB}"
    if not isinstance(decl, dict):
        raise SchemaLoadError(f"{where} 未声明")
    steps = decl.get("steps")
    if not isinstance(steps, list) or [s.get("name") if isinstance(s, dict) else None for s in steps] != list(STEP_NAMES):
        raise SchemaLoadError(f"{where}.steps 须依序恰为 {list(STEP_NAMES)}(新步骤只追加且须先实现)")
    if any(s.get("by") not in ("advisor", "owner") for s in steps) or decl.get("step_states") != list(STEP_STATES):
        raise SchemaLoadError(f"{where}.steps[].by ∈ advisor|owner, step_states 须恰为 {list(STEP_STATES)}")
    modes = set(get_enum_values("task_mode"))
    offered, routing = decl.get("offered_pi_modes"), decl.get("routing_modes")
    if (not isinstance(offered, list) or not isinstance(routing, list) or not offered or not set(offered) <= set(routing)
            or not set(routing) <= modes):
        raise SchemaLoadError(f"{where}: offered_pi_modes ⊆ routing_modes ⊆ enums.schema task_mode {sorted(modes)}")
    commands = decl.get("commands") or {}
    holders = decl.get("placeholders") or {}
    if (not all(isinstance(commands.get(k), str) and commands[k] for k in _COMMAND_KEYS)
            or not all(isinstance(holders.get(k), str) and holders[k] for k in _PLACEHOLDER_KEYS)
            or not all(isinstance((decl.get("decision") or {}).get(k), str) for k in ("title", "body"))):
        raise SchemaLoadError(f"{where}: commands {list(_COMMAND_KEYS)} / placeholders {list(_PLACEHOLDER_KEYS)} / decision(title, body)未声明齐")
    texts = decl.get("texts") or {}
    reference = texts.get("zh") if isinstance(texts.get("zh"), dict) else {}
    for lang, table in texts.items():
        missing = sorted(k for k in reference if k != "steps" and not isinstance((table or {}).get(k), str))
        missing += [f"mode_{m}" for m in routing if not isinstance((table or {}).get(f"mode_{m}"), str)]
        step_texts = (table or {}).get("steps") or {}
        missing += [f"steps.{n}.{k}" for n in STEP_NAMES for k in _STEP_TEXT_KEYS if not isinstance((step_texts.get(n) or {}).get(k), str)]
        if missing:
            raise SchemaLoadError(f"{where}.texts.{lang} 缺 {missing}")
    from tools.aipos_cli.board_pages import pages_declaration

    supported = list(pages_declaration()["locale"]["supported"])
    if sorted(texts) != sorted(supported):
        raise SchemaLoadError(f"{where}.texts 语言须恰为看板 locale.supported {supported}")
    board = decl.get("board") or {}
    params = board.get("params") or {}
    if (not str(board.get("page_path") or "").startswith("/")
            or not all(isinstance(params.get(k), str) and params[k] for k in ("pi_mode", "pi_executor", "workspace", "host", "ssh_alias",
                                                                                "owner_ref", "submitted"))):
        raise SchemaLoadError(f"{where}.board(page_path / params)未声明齐")
    codes = decl.get("exit_codes") or {}
    if not all(isinstance((codes.get(k) or {}).get("code"), int) for k in ("ok", "refused", "usage")):
        raise SchemaLoadError(f"{where}.exit_codes(ok / refused / usage)未声明齐")
    return decl


def texts(lang: str) -> dict[str, Any]:
    """一种语言的全部文案(未知语言 = 看板缺省语言)。"""
    from tools.aipos_cli.board_pages import pages_declaration

    table = declaration()["texts"]
    return table[lang] if lang in table else table[str(pages_declaration()["locale"]["default"])]


def _fmt(template: Any, **values: Any) -> str:
    from tools.schema_loader import SchemaLoadError

    try:
        return str(template).format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB} 文案 / 命令模板占位无值: {template!r}: {exc}") from exc


def page_url(project: str | None = None) -> str:
    from urllib.parse import quote

    path = str(declaration()["board"]["page_path"])
    return f"{path}?project={quote(str(project))}" if project else path


# ---------------------------------------------------------------------------
# 目标分工(CLI 参数 / 看板 URL 参数 → 同一规范形)
# ---------------------------------------------------------------------------

def normalize_target(*, pi_modes: list[str] | None, pi_executor: str | None, workspace: str | None, host: str | None,
                     ssh_alias: str | None, owner_ref: str | None, lang: str) -> dict[str, Any]:
    """目标分工输入的唯一校验: pi_modes None = 保持现状; 卡类型 ∈ offered_pi_modes; 实例名合命名模板; 工位目录绝对路径; 主机与别名成对且为单 token;
    Owner 引用单行。不合 = TargetError(人话)。"""
    from tools.aipos_cli.naming_profile import parse_instance_name

    t = texts(lang)
    offered = list(declaration()["offered_pi_modes"])
    modes: list[str] | None = None
    if pi_modes is not None:
        modes = []
        for raw in pi_modes:
            mode = str(raw).strip()
            if not mode:
                continue
            if mode not in offered:
                raise TargetError(_fmt(t["error_mode"], mode=mode, allowed=", ".join(offered)))
            if mode not in modes:
                modes.append(mode)
        modes = [m for m in offered if m in modes]  # 声明序(CLI 与看板同一顺序)
    inst = str(pi_executor or "").strip() or None
    if inst is not None and parse_instance_name(inst) is None:
        raise TargetError(_fmt(t["error_instance"], instance=inst))
    ws = str(workspace or "").strip() or None
    if ws is not None and not Path(ws).is_absolute():
        raise TargetError(_fmt(t["error_workspace"], workspace=ws))
    h, alias = str(host or "").strip() or None, str(ssh_alias or "").strip() or None
    if (h is None) != (alias is None) or any(v is not None and (not _TOKEN_RE.match(v) or v.startswith("-")) for v in (h, alias)):
        raise TargetError(_fmt(t["error_host"], value=f"{h or ''} {alias or ''}".strip()))
    ref = str(owner_ref or "").strip() or None
    if owner_ref is not None and str(owner_ref).strip() and "\n" in str(owner_ref).strip():
        raise TargetError(_fmt(t["error_owner_ref"], value=str(owner_ref)))
    return {"pi_modes": modes, "pi_executor": inst, "workspace": ws, "host": h, "ssh_alias": alias, "owner_ref": ref}


def target_from_query(params: dict[str, list[str]], lang: str) -> dict[str, Any]:
    """看板 URL 参数(board.params)→ normalize_target。未提交表单(无 submitted 参数)= 卡类型保持现状。"""
    names = declaration()["board"]["params"]

    def one(key: str) -> str | None:
        return (params.get(names[key]) or [None])[0]

    submitted = bool(params.get(names["submitted"]))
    return normalize_target(pi_modes=list(params.get(names["pi_mode"]) or []) if submitted else None, pi_executor=one("pi_executor"),
                            workspace=one("workspace"), host=one("host"), ssh_alias=one("ssh_alias"), owner_ref=one("owner_ref"), lang=lang)


# ---------------------------------------------------------------------------
# 现状
# ---------------------------------------------------------------------------

def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _instance_view(root: Path, instance: str | None, *, needs_workstation: bool) -> dict[str, Any] | None:
    """实例接入 / 工位 / 章程(enrollment.instance_enrollment + distribution_sync.charter_local_state)。None = 未声明。"""
    from tools.aipos_cli.distribution_sync import charter_local_state
    from tools.aipos_cli.enrollment import instance_enrollment

    if not instance:
        return None
    out: dict[str, Any] = {"instance": instance, "enrollment": "error", "error": None, "role": None, "dir": None, "transport": None,
                           "host": None, "charter": None}
    try:
        view = instance_enrollment(root, instance)
    except (OSError, ValueError) as exc:
        out["error"] = _err(exc)
        return out
    loc = view["workstation"]
    out["role"] = view.get("role")
    if loc.get("found"):
        out.update(dir=str(loc["dir"]), transport=loc.get("transport"), host=loc.get("host"))
    if not view["enrolled"]:
        out["enrollment"] = "not_enrolled"
    elif needs_workstation and not loc.get("found"):
        out["enrollment"] = "no_workstation"
    else:
        out["enrollment"] = "enrolled"
    if needs_workstation and out["dir"]:
        out["charter"] = ({"state": "remote", "detail": f"{out['host']}:{out['dir']}", "path": None, "markers": []}
                          if out["transport"] == "remote" else charter_local_state(out["dir"], root))
    return out


def _validation_summary(root: Path) -> dict[str, Any]:
    from tools.aipos_cli.validation_root import (
        ValidationRootError,
        active_run,
        declaration_status,
        is_local_host,
        project_validation_hosts,
        project_validation_roots,
    )

    try:
        entries = project_validation_roots(root)
        hosts = project_validation_hosts(root)
    except (ValidationRootError, OSError, ValueError) as exc:
        return {"error": _err(exc), "total": 0, "counts": {}, "runs": 0, "hosts": {}, "used_hosts": []}
    counts = {k: 0 for k in ("active", "expired", "revoked", "unbound")}
    for entry in entries:
        counts[declaration_status(entry)] += 1
    used = sorted({str(e["host"]) for e in entries if not is_local_host(e.get("host"))})
    return {"error": None, "total": len(entries), "counts": counts, "runs": sum(1 for e in entries if active_run(e)),
            "hosts": {k: str(v["ssh_alias"]) for k, v in sorted(hosts.items())}, "used_hosts": used}


def _routing(execution: dict[str, Any] | None, modes: list[str]) -> list[dict[str, Any]]:
    """各卡类型派给谁(card_execution_mode 同一判定)+ 是否独立审计(task_mode_independent_audit)。"""
    from tools.aipos_cli.next_resolver import task_mode_independent_audit
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI, card_execution_mode

    rows = []
    for mode in modes:
        independent = task_mode_independent_audit(mode)
        if execution is None:
            rows.append({"mode": mode, "kind": "undeclared", "instance": None, "independent_audit": independent, "auditor": None})
            continue
        kind = card_execution_mode(execution, {"task_mode": mode})
        rows.append({"mode": mode, "kind": "pi" if kind == EXECUTION_MODE_PI else "subagent",
                     "instance": execution["pi_executor"] if kind == EXECUTION_MODE_PI else execution["subagent_executor"],
                     "independent_audit": independent, "auditor": execution["auditor"]})
    return rows


def _candidates(root: Path, project: str, known_dirs: list[str], reserved: set[str]) -> dict[str, Any]:
    """pi 执行工位候选: 已登记工位所在父目录下的已接入工位(discover_workstations), 其 .lybra/role 实例项目段属本项目
    (naming_profile project_segment + 别名; workstation_identity 唯一读口), 排除审计 / 子 agent 实例。"""
    from tools.aipos_cli.charter_render import WorkstationIdentityError, workstation_identity
    from tools.aipos_cli.distribution_sync import discover_workstations
    from tools.aipos_cli.naming_profile import get_naming_profile

    errors: list[str] = []
    try:
        profile = get_naming_profile(root)
        segments = {profile["project_segment"], *profile.get("project_segment_aliases", [])}
    except (OSError, ValueError) as exc:
        errors.append(_err(exc))
        segments = {project}
    found: dict[str, str] = {}
    for parent in sorted({str(Path(d).parent) for d in known_dirs}):
        try:
            stations = discover_workstations(Path(parent))
        except (OSError, ValueError) as exc:
            errors.append(_err(exc))
            continue
        for ws in stations:
            try:
                identity = workstation_identity(ws)
            except (WorkstationIdentityError, OSError) as exc:
                errors.append(_err(exc))
                continue
            if identity["project"] in segments and identity["instance"] not in reserved:
                found.setdefault(str(identity["instance"]), str(ws))
    return {"items": [{"instance": k, "dir": v} for k, v in sorted(found.items())], "errors": errors}


def collect_status(root: str | Path, *, project: str, home_root: str | Path | None = None) -> dict[str, Any]:
    """一个项目的工位现状(只读)。"""
    from tools.aipos_cli.advisor_updates_cli import advisor_instances
    from tools.aipos_cli.custom_roles import RoleRegistryReadError
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError, project_execution

    gov = Path(root).expanduser().resolve()
    decl = declaration()
    status: dict[str, Any] = {"project": project, "governance_root": str(gov),
                              "home_root": str(Path(home_root).expanduser().resolve()) if home_root else str(gov.parent),
                              "execution": None, "execution_error": None}
    try:
        status["execution"] = project_execution(gov)
    except (ExecutionDeclarationError, OSError, ValueError) as exc:
        status["execution_error"] = _err(exc)
    execution = status["execution"] or {}
    status["subagent"] = _instance_view(gov, execution.get("subagent_executor"), needs_workstation=False)
    status["pi_executor"] = _instance_view(gov, execution.get("pi_executor"), needs_workstation=True)
    status["auditor"] = _instance_view(gov, execution.get("auditor"), needs_workstation=True)
    status["pi_modes"] = list(execution.get("pi_allowed_task_modes") or [])
    status["validation"] = _validation_summary(gov)
    status["routing"] = _routing(status["execution"], list(decl["routing_modes"]))
    try:
        status["advisors"] = advisor_instances(gov)
    except (OSError, ValueError, LookupError, RoleRegistryReadError) as exc:
        status["advisors"] = []
        status["advisors_error"] = _err(exc)
    known = [v["dir"] for v in (status["pi_executor"], status["auditor"]) if v and v.get("dir") and v.get("transport") != "remote"]
    reserved = {n for n in (execution.get("auditor"), execution.get("subagent_executor")) if n}
    status["candidates"] = _candidates(gov, project, known, reserved)
    return status


def _status_risks(status: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """现状风险(文案键, 填值); 角色名 / 状态名由渲染按语言取。"""
    risks: list[tuple[str, dict[str, Any]]] = []
    if status["execution_error"]:
        risks.append(("risk_execution_error", {"error": status["execution_error"]}))
    elif status["execution"] is None:
        risks.append(("risk_execution_undeclared", {}))
    for key in ("subagent", "pi_executor", "auditor"):
        view = status[key]
        if view is None:
            continue
        if view["enrollment"] != "enrolled":
            risks.append(("risk_instance", {"role": f"row_{key}", "instance": view["instance"], "state": f"enrollment_{view['enrollment']}"}))
        charter = view.get("charter")
        if charter and charter["state"] not in ("current", "remote"):
            risks.append(("risk_charter", {"role": f"row_{key}", "instance": view["instance"], "state": f"charter_{charter['state']}"}))
    vr = status["validation"]
    if vr["error"]:
        risks.append(("risk_vr_error", {"error": vr["error"]}))
    for host in vr["used_hosts"]:
        if host not in vr["hosts"] and status["pi_executor"] is not None and status["pi_executor"].get("transport") != "remote":
            risks.append(("risk_host_no_alias", {"host": host}))
    for error in status["candidates"]["errors"]:
        risks.append(("risk_candidates_error", {"error": error}))
    return risks


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------

def _q(value: Any) -> str:
    return shlex.quote(str(value))


def _holder(value: str | None, placeholder: str) -> str:
    """真值 shell 引用; 缺值 = 声明占位(原样, 由顾问替换)。"""
    return _q(value) if value else placeholder


def _resolve_target(status: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """目标缺省补全: 卡类型缺 = 现状; pi 实例缺 = 现状 pi 执行工位; 工位目录缺 = 已接入位置 / 候选目录。"""
    execution = status["execution"] or {}
    modes = list(status["pi_modes"]) if target["pi_modes"] is None else list(target["pi_modes"])
    inst = (target["pi_executor"] or execution.get("pi_executor")) if modes else None
    enrolled = _instance_view(Path(status["governance_root"]), inst, needs_workstation=True) if inst else None
    if inst and status["pi_executor"] and status["pi_executor"]["instance"] == inst:
        enrolled = status["pi_executor"]
    candidate = next((c["dir"] for c in status["candidates"]["items"] if c["instance"] == inst), None)
    workspace = target["workspace"] or (enrolled or {}).get("dir") or candidate
    return {**target, "pi_modes": modes, "pi_executor": inst, "workspace": workspace, "view": enrolled}


def _station_identity(workspace: str | None) -> dict[str, Any] | None:
    """所选工位目录的 .lybra/role(无 = None; 读不出 = {error})。"""
    from tools.aipos_cli.charter_render import WorkstationIdentityError, is_enrolled_workstation, workstation_identity

    if not workspace or not Path(workspace).is_dir() or not is_enrolled_workstation(Path(workspace)):
        return None
    try:
        return workstation_identity(Path(workspace))
    except (WorkstationIdentityError, OSError) as exc:
        return {"error": _err(exc)}


def build_plan(root: str | Path, *, project: str, home_root: str | Path | None = None, target: dict[str, Any] | None = None,
               lang: str = "zh", status: dict[str, Any] | None = None) -> dict[str, Any]:
    """现状 + 目标 → 依赖序步骤(每步 state / 命令 / 现状说明)+ 两段话术 + 全文行(CLI 与看板同一文本)。只读。"""
    from tools.aipos_cli.distribution_sync import charter_local_state, hand_maintained_guard_declaration, render_sync_command
    from tools.aipos_cli.governance_add import decision_entry_path
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI
    from tools.aipos_cli.workstation_wiring import derive_effective_owner_policy_ref, envelope_mint_exit

    decl = declaration()
    t = texts(lang)
    zh = texts("zh")
    holders = decl["placeholders"]
    cmds = decl["commands"]
    gov = Path(root).expanduser().resolve()
    st = status if status is not None else collect_status(gov, project=project, home_root=home_root)
    tgt = _resolve_target(st, target or normalize_target(pi_modes=None, pi_executor=None, workspace=None, host=None, ssh_alias=None,
                                                         owner_ref=None, lang=lang))
    execution = st["execution"] or {}
    advisors = list(st.get("advisors") or [])
    advisor = _q(advisors[0]) if len(advisors) == 1 else holders["advisor"]
    inst, ws, modes = tgt["pi_executor"], tgt["workspace"], tgt["pi_modes"]
    view = tgt["view"]
    risks = list(_status_risks(st))
    # 目标侧风险
    if modes and not inst:
        risks.append(("risk_target_no_pi", {}))
    if inst and inst in {execution.get("auditor"), execution.get("subagent_executor")}:
        raise TargetError(_fmt(t["error_instance_reserved"], instance=inst))
    enrolled = bool(view and view["enrollment"] == "enrolled")
    if inst and not enrolled and not ws:
        risks.append(("risk_target_no_dir", {"instance": inst}))
    if enrolled and tgt["workspace"] and view.get("dir") and Path(tgt["workspace"]) != Path(view["dir"]):
        risks.append(("risk_target_dir_differs", {"instance": inst, "current": view["dir"], "chosen": tgt["workspace"]}))
    station = _station_identity(ws) if inst else None
    if station and "error" not in station and station["instance"] != inst:
        risks.append(("risk_target_slot_taken", {"dir": ws, "other": station["instance"], "instance": inst}))
    if "validation" in modes and not tgt["host"] and not st["validation"]["hosts"]:
        risks.append(("risk_target_no_host", {}))
    role = (view or {}).get("role") or (station or {}).get("role") or "executor"
    # 决策引用: 已给 = 用它; 否则 = 第 1 步将写出的决策记录(同一命名 decision_entry_path)
    mode_names = zh["list_sep"].join(zh[f"mode_{m}"] for m in modes)
    division = (_fmt(zh["target_pi"], modes=mode_names, instance=inst or holders["pi_executor"], dir=ws or holders["workspace"])
                if modes else zh["target_none"])
    title = _fmt(decl["decision"]["title"], project=project)
    body = _fmt(decl["decision"]["body"], division=division, auditor=execution.get("auditor") or holders["auditor"])
    decision_path = decision_entry_path(gov, title=title)
    decision_rel = decision_path.relative_to(gov).as_posix() if decision_path.is_relative_to(gov) else str(decision_path)
    owner_ref = tgt["owner_ref"] or decision_rel
    steps: dict[str, dict[str, Any]] = {n: {"name": n, "state": "na", "detail": "", "preview": None, "confirm": None, "land": None,
                                            "check": None} for n in STEP_NAMES}

    # Owner 签信封 / 接入
    policy = None
    if inst:
        if enrolled:
            steps["envelope"].update(state="done", detail=f"{inst} {t['enrollment_enrolled']}")
            steps["enroll"].update(state="done", detail=f"{inst} @ {view.get('dir') or '-'}")
        else:
            from tools.aipos_cli.autonomy_policy import envelope_trace_output

            with envelope_trace_output(False):  # 只读视图: 不打门侧信封诊断行(判定与结果不变; 同 loop status)
                policy, reason = derive_effective_owner_policy_ref(gov, role=role, agent_instance=inst)
            if policy:
                steps["envelope"].update(state="done", detail=f"{policy}")
            else:
                mint = envelope_mint_exit(gov, inst)
                steps["envelope"].update(state="pending", detail=reason, preview=mint,
                                         confirm=re.sub(r" --dry-run$", " --confirm", mint))
            values = {"governance_root": _q(gov), "role": _q(role), "instance": _q(inst), "workspace": _holder(ws, holders["workspace"]),
                      "advisor": advisor, "policy": _holder(policy, holders["policy"])}
            steps["enroll"].update(state="pending", detail=(view or {}).get("enrollment") or "not_enrolled",
                                   preview=_fmt(cmds["enroll_preview"], **values), confirm=_fmt(cmds["enroll_confirm"], **values))
    # 章程(所选工位目录; 已接入 = 其登记位置)
    if inst and ws:
        charter = ((view or {}).get("charter") if enrolled and view.get("dir") and Path(view["dir"]) == Path(ws)
                   else charter_local_state(ws, gov))
        charter = charter or {"state": "unverifiable", "detail": ""}
        guard = hand_maintained_guard_declaration()
        state = charter["state"]
        if state == "hand_maintained":
            base = f"{render_sync_command(ws, gov)} {guard['adopt_flag']} {guard['owner_ref_flag']} {_q(owner_ref)}"
            steps["charter_adopt"].update(state="pending", detail=charter["detail"], preview=f"{base} --dry-run", confirm=base)
        elif state in ("stale", "missing") or (state == "not_workstation" and not enrolled):
            steps["charter_sync"].update(state="pending", detail=charter["detail"], preview=render_sync_command(ws, gov, dry_run=True),
                                         confirm=render_sync_command(ws, gov))
        elif state == "current":
            steps["charter_sync"].update(state="done", detail=charter["detail"])
        else:
            steps["charter_sync"].update(detail=f"{state}: {charter.get('detail') or ''}".strip())
    # 执行方式
    want_exec = {"pi_executor": inst if modes else None, "modes": sorted(modes)}
    have_exec = {"pi_executor": execution.get("pi_executor") if execution.get("pi_allowed_task_modes") else None,
                 "modes": sorted(execution.get("pi_allowed_task_modes") or [])}
    if st["execution"] is not None and want_exec == have_exec:
        steps["execution"].update(state="done", detail=f"pi_executor={have_exec['pi_executor']} pi_allowed_task_modes={have_exec['modes']}")
    else:
        from tools.aipos_cli.workspace_config import execution_declaration

        default_mode = execution_declaration()["schema"]["default_mode"].get("default")
        extra = ""
        if execution.get("default_mode") and execution["default_mode"] != default_mode:
            extra += f" --default-mode {_q(execution['default_mode'])}"
        if modes:
            extra += f" --pi-executor {_holder(inst, holders['pi_executor'])}" + "".join(f" --pi-allowed-task-mode {_q(m)}" for m in modes)
        values = {"governance_root": _q(gov), "subagent": _holder(execution.get("subagent_executor"), holders["subagent"]),
                  "auditor": _holder(execution.get("auditor"), holders["auditor"]), "extra": extra, "advisor": advisor}
        steps["execution"].update(state="pending", detail=f"pi_executor={have_exec['pi_executor']} pi_allowed_task_modes={have_exec['modes']}",
                                  preview=_fmt(cmds["execution_preview"], **values), confirm=_fmt(cmds["execution_confirm"], **values))
    # 目标主机 ssh 别名(未选目标主机 = 列出已声明的别名, 不改)
    declared_hosts = st["validation"]["hosts"]
    if not tgt["host"] and declared_hosts:
        steps["ssh_host"].update(state="done", detail="; ".join(f"{h} → {a}" for h, a in declared_hosts.items()))
    if tgt["host"]:
        current = st["validation"]["hosts"].get(tgt["host"])
        if current == tgt["ssh_alias"]:
            steps["ssh_host"].update(state="done", detail=f"{tgt['host']} → {current}")
        else:
            values = {"governance_root": _q(gov), "host": _q(tgt["host"]), "alias": _q(tgt["ssh_alias"]), "owner_ref": _q(owner_ref),
                      "advisor": advisor}
            steps["ssh_host"].update(state="pending", detail=f"{tgt['host']} → {current or '-'}",
                                     preview=_fmt(cmds["ssh_host_preview"], **values), confirm=_fmt(cmds["ssh_host_confirm"], **values))
    changes = any(steps[n]["state"] == "pending" for n in _CHANGE_STEPS)
    if changes and not tgt["owner_ref"]:
        values = {"governance_root": _q(gov), "title": _q(title), "body": _q(body), "advisor": advisor, "decision_rel": _q(decision_rel)}
        steps["decision"].update(state="pending", detail=decision_rel, preview=_fmt(cmds["decision_preview"], **values),
                                 confirm=_fmt(cmds["decision_confirm"], **values), land=_fmt(cmds["decision_land"], **values))
    elif tgt["owner_ref"]:
        steps["decision"].update(state="done", detail=tgt["owner_ref"])
    if inst and modes:
        steps["verify"].update(state="check", check=_fmt(cmds["verify"], project=_q(project), home_root=_q(st["home_root"]), instance=_q(inst),
                                                         harness=EXECUTION_MODE_PI))
    if changes:
        steps["notify"].update(state="pending")
    target_exec = None
    if st["execution"] is not None or modes:
        target_exec = {"default_mode": execution.get("default_mode") or "subagent", "subagent_executor": execution.get("subagent_executor"),
                       "pi_allowed_task_modes": modes, "pi_executor": inst if modes else None, "auditor": execution.get("auditor")}
    plan = {"project": project, "governance_root": str(gov), "lang": lang, "status": st, "risks": risks,
            "target": {k: tgt[k] for k in ("pi_modes", "pi_executor", "workspace", "host", "ssh_alias", "owner_ref")},
            "owner_ref": owner_ref if (changes or tgt["owner_ref"]) else None, "decision_path": decision_rel,
            "steps": [steps[n] for n in STEP_NAMES], "changes": changes,
            "target_routing": _routing(target_exec, list(decl["routing_modes"])), "envelope_url": _envelope_url(project)}
    plan["prompts"] = {"lead": _lead_prompt(plan, t), "specialist": _specialist_prompt(plan, t)}
    plan["lines"] = render_lines(plan)
    return plan


def _envelope_url(project: str) -> str:
    from tools.aipos_cli.envelope_board import board_envelope_url

    return board_envelope_url(project)


# ---------------------------------------------------------------------------
# 渲染(文本行 = CLI 输出 = 看板 <pre> 全文; 话术)
# ---------------------------------------------------------------------------

def instance_parts(t: dict[str, Any], view: dict[str, Any] | None) -> dict[str, Any]:
    """实例一行的各段(看板分段渲染 = 文本同一推导): who(实例 + 接入状态)/ where(工位位置)/ charter(章程状态)/ ok(无缺项)。"""
    if view is None:
        return {"who": t["undeclared"], "where": None, "charter": None, "ok": False, "enrollment": None, "charter_state": None}
    who = _fmt(t["instance_value"], instance=view["instance"], enrollment=t[f"enrollment_{view['enrollment']}"])
    if view.get("error"):
        who += f" {view['error']}"
    where = (_fmt(t["workstation_value"], dir=(f"{view['host']}:" if view.get("transport") == "remote" else "") + view["dir"])
             if view.get("dir") else None)
    state = (view.get("charter") or {}).get("state")
    charter = _fmt(t["charter_value"], state=t[f"charter_{state}"]) if state else None
    return {"who": who, "where": where, "charter": charter, "enrollment": view["enrollment"], "charter_state": state,
            "ok": view["enrollment"] == "enrolled" and state in (None, "current", "remote")}


def _instance_text(t: dict[str, Any], view: dict[str, Any] | None) -> str:
    parts = instance_parts(t, view)
    return "; ".join(x for x in (parts["who"], parts["where"], parts["charter"]) if x)


def status_rows(plan: dict[str, Any], t: dict[str, Any]) -> list[tuple[str, str]]:
    """现状各行(标签键, 值文本): 看板表格与文本同一推导。"""
    st = plan["status"]
    vr = st["validation"]
    modes = t["list_sep"].join(t[f"mode_{m}"] for m in st["pi_modes"]) or t["modes_none"]
    hosts = sorted(set(vr["hosts"]) | set(vr["used_hosts"]))
    host_text = t["list_sep"].join(_fmt(t["host_value"], host=h, alias=vr["hosts"][h]) if h in vr["hosts"] else _fmt(t["host_no_alias"], host=h)
                                   for h in hosts) or t["none"]
    if vr["error"]:
        vr_text = vr["error"]
    elif vr["total"]:
        vr_text = _fmt(t["vr_value"], total=vr["total"], runs=vr["runs"], hosts=", ".join(vr["used_hosts"]) or "-", **vr["counts"])
    else:
        vr_text = t["none"]
    return [("row_subagent", _instance_text(t, st["subagent"])), ("row_pi_executor", _instance_text(t, st["pi_executor"])),
            ("row_auditor", _instance_text(t, st["auditor"])), ("row_pi_modes", modes), ("row_hosts", host_text),
            ("row_validation_roots", vr_text)]


def routing_lines(rows: list[dict[str, Any]], t: dict[str, Any], *, rule: bool = False) -> list[str]:
    out = []
    for r in rows:
        if r["kind"] == "undeclared":
            who = t["route_undeclared"]
        else:
            who = _fmt(t["route_pi" if r["kind"] == "pi" else "route_subagent"], instance=r["instance"] or "-")
        audit = _fmt(t["audit_independent"], auditor=r["auditor"] or "-") if r["independent_audit"] else t["audit_advisor"]
        out.append(_fmt(t["specialist_rule" if rule else "route_line"], mode=t[f"mode_{r['mode']}"], who=who, audit=audit))
    return out


def risk_texts(plan: dict[str, Any], t: dict[str, Any]) -> list[str]:
    out = []
    for key, values in plan["risks"]:
        filled = {k: (t[v] if k in ("role", "state") else v) for k, v in values.items()}
        out.append(_fmt(t[key], **filled))
    return out


def target_text(plan: dict[str, Any], t: dict[str, Any]) -> str:
    tgt = plan["target"]
    holders = declaration()["placeholders"]
    if tgt["pi_modes"]:
        text = _fmt(t["target_pi"], modes=t["list_sep"].join(t[f"mode_{m}"] for m in tgt["pi_modes"]),
                    instance=tgt["pi_executor"] or holders["pi_executor"], dir=tgt["workspace"] or holders["workspace"])
    else:
        text = t["target_none"]
    if tgt["host"]:
        text += "; " + _fmt(t["target_host"], host=tgt["host"], alias=tgt["ssh_alias"])
    if tgt["owner_ref"]:
        text += "; " + _fmt(t["target_owner_ref"], ref=tgt["owner_ref"])
    return text


def step_lines(plan: dict[str, Any], t: dict[str, Any], step: dict[str, Any], n: int, *, full: bool = True) -> list[str]:
    st = t["steps"][step["name"]]
    lines = [_fmt(t["step_line"], n=n, state=t[f"state_{step['state']}"], label=st["label"])]
    if step["detail"] and full:
        lines.append(_fmt(t["step_detail"], detail=step["detail"]))
    if step["state"] in ("pending", "check"):
        lines.append(_fmt(t["step_why"], why=st["why"]))
        if step["name"] == "envelope":
            lines.append(_fmt(t["step_by_owner"], url=plan["envelope_url"]))
        for key in ("preview", "confirm", "land", "check"):
            if step[key]:
                lines.append(_fmt(t[f"step_{key}"], command=step[key]))
        lines.append(_fmt(t["step_expect"], expect=st["expect"]))
        lines.append(_fmt(t["step_refused"], refused=st["refused"]))
    return lines


def _lead_prompt(plan: dict[str, Any], t: dict[str, Any]) -> list[str]:
    lines = [_fmt(t["lead_intro"], project=plan["project"]), _fmt(t["lead_target"], target=target_text(plan, t))]
    if not plan["changes"]:
        lines.append(t["lead_nothing"])
    for n, step in enumerate(plan["steps"], 1):
        if step["state"] in ("pending", "check"):
            lines += step_lines(plan, t, step, n, full=False)
        elif step["state"] == "done":
            lines.append(_fmt(t["lead_skip"], n=n, label=t["steps"][step["name"]]["label"]))
    lines.append(t["lead_outro"])
    return lines


def _specialist_prompt(plan: dict[str, Any], t: dict[str, Any]) -> list[str]:
    auditor = (plan["status"]["execution"] or {}).get("auditor") or declaration()["placeholders"]["auditor"]
    ref = plan["owner_ref"] or (plan["target"]["owner_ref"]) or t["none"]
    return ([_fmt(t["specialist_intro"], project=plan["project"], ref=ref)] + routing_lines(plan["target_routing"], t, rule=True)
            + [_fmt(t["specialist_audit"], auditor=auditor), t["specialist_outro"]])


def render_lines(plan: dict[str, Any]) -> list[str]:
    """全文(CLI 文本输出 = 看板「完整计划」<pre> 逐字节同一)。"""
    t = texts(plan["lang"])
    lines = [_fmt(t["head"], project=plan["project"], governance_root=plan["governance_root"]), "", t["status_heading"]]
    lines += ["  " + _fmt(t["row_line"], label=t[key], value=value) for key, value in status_rows(plan, t)]
    lines += ["", t["routing_heading"]] + ["  " + line for line in routing_lines(plan["status"]["routing"], t)]
    auditor = (plan["status"]["execution"] or {}).get("auditor")
    if auditor:
        lines.append("  " + _fmt(t["audit_row"], auditor=auditor))
    lines += ["", t["risks_heading"]] + (["  " + _fmt(t["risk_line"], text=r) for r in risk_texts(plan, t)] or ["  " + t["none"]])
    lines += ["", t["target_heading"], "  " + target_text(plan, t), "", t["plan_heading"]]
    if not plan["changes"]:
        lines.append("  " + t["nothing"])
    for n, step in enumerate(plan["steps"], 1):
        lines += ["  " + line for line in step_lines(plan, t, step, n)]
    lines += ["", t["lead_heading"]] + ["  " + line for line in plan["prompts"]["lead"]]
    lines += ["", t["specialist_heading"]] + ["  " + line for line in plan["prompts"]["specialist"]]
    return lines


def plan_view(plan: dict[str, Any]) -> dict[str, Any]:
    """--json 视图(可序列化)。"""
    t = texts(plan["lang"])
    return {"project": plan["project"], "governance_root": plan["governance_root"], "lang": plan["lang"],
            "status": {k: v for k, v in plan["status"].items()}, "risks": risk_texts(plan, t), "target": plan["target"],
            "owner_ref": plan["owner_ref"], "decision_path": plan["decision_path"], "changes": plan["changes"],
            "steps": [{**s, "label": t["steps"][s["name"]]["label"]} for s in plan["steps"]], "target_routing": plan["target_routing"],
            "prompts": plan["prompts"], "lines": plan["lines"]}


# ---------------------------------------------------------------------------
# 看板页数据
# ---------------------------------------------------------------------------

def collect_page(home_root: str | Path | None, params: dict[str, list[str]], *, lang: str, show_all: bool = False) -> dict[str, Any]:
    """工位设置页: 项目页签(home 根注册项目, 同项目页)+ 选中项目的计划(目标 = URL 参数)。输入不合 = input_error(仍给现状)。只读。"""
    from tools.aipos_cli import board_pages
    from tools.aipos_cli.task_loader import read_request_scope

    with read_request_scope():
        decl = board_pages.pages_declaration()
        home = board_pages.home_entries(home_root)
        shown, hidden = board_pages.visible_entries(home, show_all)
        project = (params.get(decl["query"]["project"]) or [None])[0]
        chosen = board_pages._pick_entry(home, shown, project)
        out: dict[str, Any] = {"home_root": str(home["home_root"]), "hidden": hidden, "show_all": show_all,
                               "projects": [{"project": e["name"], "paused": e["paused"], "hidden": e["hidden"], "url": page_url(e["name"])}
                                            for e in board_pages._with_chosen(shown, chosen)],
                               "unknown_project": project if project and chosen is None else None, "selected": None,
                               "input_error": None, "params": params}
        if chosen is None:
            return out
        status = collect_status(chosen["root"], project=chosen["name"], home_root=home["home_root"])
        try:
            target = target_from_query(params, lang)
            plan = build_plan(chosen["root"], project=chosen["name"], home_root=home["home_root"], target=target, lang=lang, status=status)
        except TargetError as exc:
            out["input_error"] = str(exc)
            plan = build_plan(chosen["root"], project=chosen["name"], home_root=home["home_root"], lang=lang, status=status)
        out["selected"] = plan
        return out


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
