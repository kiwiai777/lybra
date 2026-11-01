"""AIPOS-F272 — 看板写例外⑥「首签项目授权」与⑦「执行方式 / pi 模型」(产品化 P3 收尾): Owner 会话在接入页预演 → 确认
`lybra project grant sign`、`lybra project set-execution`、`lybra project set-model`。

一句话: 装好 Lybra 的 Owner 不开终端也能给新项目签第一份项目授权、声明执行分工与 pi 模型——接入清单对应未完成项旁点按钮, 先看授权全文 /
project.json diff, 确认后用 CLI 同一实现写入并落账, 清单即时变绿。

防碎片化(卡面硬约束), 本模块只给 F172 同一安全框架的回调 + 声明读取 + 预填 / 候选视图, 不另写任何一步的实现:
- 安全判定 = envelope_board.handle_owner_write(Owner 会话 / Origin / JSON / stage / workspace = home 根已注册项目 / 一次性预演令牌六类 /
  看板审计日志)。
- ⑥ 预演 / 确认 = 写例外①「项目授权」同一回调 envelope_board._authorization_callbacks(预演 envelope_mint.mint_preview = CLI 预演
  record_owner_decision dry_run; 确认 envelope_mint.mint_via_gate = CLI --confirm 同一门路径并落账); 授权块唯一构造 autonomy_policy.authorization_block
  (envelope_board._authorization_payload action=grant)。预填 = onboarding.grant_plan / _repo_names / grant_task_modes(向导第 3 步同一推导)+
  advisor_updates_cli.advisor_instances + validation_root.local_host_label。
- ⑦ 执行方式 = enrollment.set_project_execution(CLI set-execution 唯一实现; 候选判定 = 其既有校验 execution_instance_refusals /
  subagent_mode_mismatch); pi 模型 = enrollment.set_project_pi_model(CLI set-model 唯一实现; 候选 = 看板 setup check 缓存结果, 不另起子进程);
  写后落账 = board_adapter.land_after_write(CLI --confirm 同一落账口, command = project_writers)。
任何项目 / 实例 / 模型同一组声明, 不写死项目名、实例名、模型名、主机或路径。界面文字在看板 i18n 单通道(web/board/app.py, 前缀 text_prefix)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GRANT_VERB = "lybra_project_authorization"
#: 声明 verbs.lybra_project_authorization.board 必有的键
_GRANT_KEYS = ("page", "write_path", "script_path", "action", "row", "text_prefix", "in_use_statuses", "defaults", "errors")
#: 声明 two_phase_protocol.project_json_writers.board 必有的键
_EXEC_KEYS = ("page", "write_path", "script_path", "action", "actions", "rows", "text_prefix", "errors")
#: set-execution 的实例键 → 该位置实例应属的角色类(候选视图按登记角色归位; 写入校验仍只在 set_project_execution)
_EXECUTION_SLOTS = (("subagent_executor", "executor"), ("auditor", "auditor"), ("pi_executor", "executor"))
_OPEN_STATES = ("todo", "error")


def _check_errors(where: str, errors: Any) -> None:
    from tools.schema_loader import SchemaLoadError

    if not isinstance(errors, dict) or not errors:
        raise SchemaLoadError(f"{where}.errors 须为非空码表")
    for code, spec in errors.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("status"), int) or not str(spec.get("message") or "").strip():
            raise SchemaLoadError(f"{where}.errors.{code} 须有 int status 与非空 message")


def grant_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_project_authorization.board(fail-closed): 键齐; 路径以 / 起首; page ∈ 看板只读页; row ∈ 接入清单项;
    in_use_statuses 非空; defaults {expires_days, max_tasks} 正整数; errors 每码有 int status 与非空 message。"""
    from tools.aipos_cli.board_pages import ONBOARDING_CHECKS, PAGES
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = ((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(GRANT_VERB) or {}).get("board")
    where = f"verbs.schema.json verbs.{GRANT_VERB}.board"
    if not isinstance(decl, dict) or any(k not in decl for k in _GRANT_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_GRANT_KEYS)}")
    if (any(not str(decl[k] or "").startswith("/") for k in ("write_path", "script_path")) or decl["page"] not in PAGES
            or decl["row"] not in ONBOARDING_CHECKS or not all(isinstance(decl[k], str) and decl[k] for k in ("action", "text_prefix"))):
        raise SchemaLoadError(f"{where}: write_path / script_path 须以 / 起首, page ∈ {list(PAGES)}, row ∈ {list(ONBOARDING_CHECKS)}, "
                              "action / text_prefix 非空")
    statuses, defaults = decl["in_use_statuses"], decl["defaults"]
    if not isinstance(statuses, list) or not statuses or not all(isinstance(s, str) and s for s in statuses):
        raise SchemaLoadError(f"{where}.in_use_statuses 须为非空字符串列表")
    if (not isinstance(defaults, dict) or any(not isinstance(defaults.get(k), int) or isinstance(defaults.get(k), bool) or defaults[k] < 1
                                              for k in ("expires_days", "max_tasks"))):
        raise SchemaLoadError(f"{where}.defaults 须有正整数 expires_days / max_tasks")
    _check_errors(where, decl["errors"])
    return decl


def execution_board_declaration() -> dict[str, Any]:
    """verbs.schema two_phase_protocol.project_json_writers.board(fail-closed): 键齐; actions 值 ∈ 本族 commands; rows 键 = actions 键、
    值 ⊆ 接入清单项; 路径以 / 起首; errors 码表形同 F172。"""
    from tools.aipos_cli.board_pages import ONBOARDING_CHECKS, PAGES
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    family = ((load_schema("verbs", code_repo_schema_root()).get("two_phase_protocol") or {}).get("project_json_writers") or {})
    decl = family.get("board")
    where = "verbs.schema.json two_phase_protocol.project_json_writers.board"
    if not isinstance(decl, dict) or any(k not in decl for k in _EXEC_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_EXEC_KEYS)}")
    if (any(not str(decl[k] or "").startswith("/") for k in ("write_path", "script_path")) or decl["page"] not in PAGES
            or not all(isinstance(decl[k], str) and decl[k] for k in ("action", "text_prefix"))):
        raise SchemaLoadError(f"{where}: write_path / script_path 须以 / 起首, page ∈ {list(PAGES)}, action / text_prefix 非空")
    actions, rows = decl["actions"], decl["rows"]
    if not isinstance(actions, dict) or set(actions) != {"execution", "model"} or any(v not in (family.get("commands") or []) for v in actions.values()):
        raise SchemaLoadError(f"{where}.actions 须恰为 execution / model, 值为本族 commands 内命令")
    if (not isinstance(rows, dict) or set(rows) != set(actions)
            or any(not isinstance(v, list) or not v or any(k not in ONBOARDING_CHECKS for k in v) for v in rows.values())):
        raise SchemaLoadError(f"{where}.rows 须为 {{动作: [接入清单项]}}, 键 = actions 键, 清单项 ∈ {list(ONBOARDING_CHECKS)}")
    _check_errors(where, decl["errors"])
    return decl


# ---------------------------------------------------------------------------
# 只读视图(接入清单行判定 / 页面预填与候选; 页面与回调同读)
# ---------------------------------------------------------------------------

def blocking_authorizations(rows: list[dict[str, Any]], decl: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """项目授权(authorization_overview 行)中挡住首签的: 最新修订可读且 status ∈ 声明 in_use_statuses(未撤销)。这些授权的出口是
    扩大 / 撤销(信封页「项目授权」与 CLI grant extend), 不重复签; 接入清单「项目授权」行与写例外⑥预演同读本判定。"""
    statuses = set((decl or grant_declaration())["in_use_statuses"])
    return [r for r in rows if r.get("state") != "missing" and r.get("status") in statuses]


def default_expiry(now: datetime, decl: dict[str, Any] | None = None) -> str:
    """缺省到期 = 今天(UTC)零点 + defaults.expires_days 天: 预演与确认在同一天内取同一值(参数摘要不因秒数漂移), 期限按整天给。"""
    days = int((decl or grant_declaration())["defaults"]["expires_days"])
    day = (now.astimezone(timezone.utc) if now.tzinfo else now).date() + timedelta(days=days)
    return f"{day.isoformat()}T00:00:00Z"


def _instance_names(root: Path) -> tuple[list[str], list[str]]:
    """(本项目已接入实例名, 执行方式声明里的实例名)。读不出 = 空(预填只是建议, Owner 可改; 写入校验在同一实现)。"""
    from tools.aipos_cli.enrollment import enrolled_instances
    from tools.aipos_cli.workspace_config import project_execution

    try:
        enrolled = [str(v["instance"]) for v in enrolled_instances(root)]
    except (OSError, ValueError):
        enrolled = []
    try:
        execution = project_execution(root) or {}
    except (OSError, ValueError):
        execution = {}
    declared = [str(execution[k]) for k in ("subagent_executor", "pi_executor", "auditor") if execution.get(k)]
    return enrolled, declared


def grant_prefill(governance_root: str | Path, project: str, *, now: datetime | None = None) -> dict[str, Any]:
    """首签表单预填(既有推导, Owner 可改): 车道 = project.json repos 仓名(onboarding._repo_names, 无 = 空); 任务类型 = onboarding.grant_task_modes;
    驱动方 = 已接入顾问实例(advisor_updates_cli.advisor_instances); 主机 = onboarding.grant_plan 的实例名主机段(已接入 ∪ 执行方式声明实例)∪ 本机
    (validation_root.local_host_label); 拉起 = grant_plan launch_harnesses; 禁区缺省全禁; 总卡数 / 到期 = 声明 defaults。另给可选值
    (拉起 = launchable_harnesses, 禁区 = project_authorization zones)与等价 CLI 命令(autonomy_policy.render_authorization_command)。"""
    from tools.aipos_cli.advisor_updates_cli import advisor_instances
    from tools.aipos_cli.autonomy_policy import launchable_harnesses, project_authorization_declaration, render_authorization_command
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.custom_roles import RoleRegistryReadError
    from tools.aipos_cli.onboarding import REPO_NAME_PLACEHOLDER, _repo_names, grant_plan, grant_task_modes
    from tools.aipos_cli.validation_root import local_host_label

    decl = grant_declaration()
    root = Path(governance_root)
    now = now or utc_now()
    lanes = [r for r in _repo_names(None, None, project, root) if r != REPO_NAME_PLACEHOLDER]
    try:
        advisors = advisor_instances(root)
    except (OSError, ValueError, RoleRegistryReadError):
        advisors = []
    enrolled, declared = _instance_names(root)
    inst = {"advisor": advisors[0] if advisors else ""} | {f"instance_{n}": name for n, name in enumerate(dict.fromkeys(enrolled + declared))}
    expires = default_expiry(now, decl)
    plan = grant_plan(root, project, inst=inst, repo_names=lanes, max_tasks=int(decl["defaults"]["max_tasks"]), expires_at=expires)
    hosts = list(dict.fromkeys([*plan["hosts"], local_host_label()]))
    prefill = {"authorization_id": plan["authorization_id"], "lanes": lanes, "task_modes": grant_task_modes(), "drivers": advisors,
               "hosts": hosts, "launch_harnesses": list(plan["launch_harnesses"]), "zones_open": [], "max_tasks": plan["max_tasks"],
               "expires_at": expires}
    flags = ([f"--grant-lane {r}" for r in lanes or ["<仓名>"]] + [f"--task-mode {m}" for m in prefill["task_modes"]]
             + [f"--driver {d}" for d in advisors or ["<顾问实例>"]] + [f"--host {h}" for h in hosts]
             + [f"--launch-harness {k}" for k in prefill["launch_harnesses"]] + [f"--max-tasks {prefill['max_tasks']}", f"--expires-at {expires}"])
    return {**prefill, "choices": {"launch_harnesses": sorted(launchable_harnesses()), "zones": list(project_authorization_declaration()["zones"])},
            "cli": render_authorization_command(root, "sign", flags)}


def execution_candidates(governance_root: str | Path) -> dict[str, Any]:
    """set-execution 候选: 本项目已接入实例(enrollment.enrolled_instances)逐个经 set-execution 既有校验(execution_instance_refusals: 已接入 /
    审计与 pi 执行者有工位; subagent_mode_mismatch: 子 agent 执行者为子 agent 模式登记)判定可放的位置, 再按登记角色类归位(执行类 → 执行者
    位置, 审计类 → 审计位置)。返回 {slots: {键: [实例]}, current: 现声明 | None, error}。"""
    from tools.aipos_cli.custom_roles import RoleRegistryReadError, resolve_instance_role_class, resolve_role_to_class
    from tools.aipos_cli.enrollment import enrolled_instances, execution_instance_refusals, subagent_mode_mismatch
    from tools.aipos_cli.workspace_config import project_execution

    root = Path(governance_root)
    slots: dict[str, list[str]] = {key: [] for key, _cls in _EXECUTION_SLOTS}
    try:
        current = project_execution(root)
        views = enrolled_instances(root)
    except (OSError, ValueError) as exc:
        return {"slots": slots, "current": None, "error": f"{type(exc).__name__}: {exc}"}
    for view in views:
        name = str(view["instance"])
        try:
            cls = resolve_role_to_class(str(view["role"]), root) if view.get("role") else None
            if cls is None:
                pair = resolve_instance_role_class(name, root)
                cls = pair[1] if pair else None
        except RoleRegistryReadError:
            cls = None
        for key, want in _EXECUTION_SLOTS:
            if cls != want or execution_instance_refusals(root, {key: name}):
                continue
            if key == "subagent_executor" and subagent_mode_mismatch(root, name, view=view) is not None:
                continue
            if key == "pi_executor" and view.get("governance_root_mode"):
                continue
            slots[key].append(name)
    return {"slots": slots, "current": current, "error": None}


def model_candidates(setup_entry: dict[str, Any] | None) -> dict[str, Any]:
    """pi 模型候选 = 看板进程内 setup check 缓存结果(board_pages.setup_check_latest; run_setup_check 的 pi_models 项 models)——不另起 pi 子进程。
    读不到(还没跑 / pi 未装 / 无模型)= models 空 + reason(页面允许手填并提示; 写入仍由 set_project_pi_model 核本机 pi 清单)。"""
    result = (setup_entry or {}).get("result")
    if not result:
        return {"models": [], "reason": "none" if setup_entry is None else "error", "detail": (setup_entry or {}).get("error")}
    item = next((it for it in result.get("items") or [] if it.get("id") == "pi_models"), None)
    models = list((item or {}).get("models") or [])
    return {"models": models, "reason": None if models else "unavailable", "detail": (item or {}).get("detail"),
            "checked_at": setup_entry.get("checked_at")}


def setup_view(proj: dict[str, Any], *, owner: bool, setup_entry: dict[str, Any] | None) -> dict[str, Any]:
    """接入页一个项目的「首签授权 / 执行方式 / pi 模型」视图(清单行 = board_pages.onboarding_rows 同一推导, 不另算): 各写例外的按钮落在哪些行
    (该行未完成; 首签另须无未撤销项目授权), Owner 会话另带预填与候选。非 Owner = 只给 owner False(页面出只读提示, 命令沿用清单行折叠命令)。"""
    from tools.aipos_cli.autonomy_policy import authorization_overview
    from tools.aipos_cli.workspace_config import pi_models_declaration

    grant_decl, exec_decl = grant_declaration(), execution_board_declaration()
    open_rows = {row["key"]: row for row in proj["rows"] if row["state"] in _OPEN_STATES}
    root = Path(proj["governance_root"])
    view: dict[str, Any] = {"owner": owner, "buttons": {}}
    if grant_decl["row"] in open_rows:
        try:
            blocked = blocking_authorizations(authorization_overview(root), grant_decl)
        except (OSError, ValueError, LookupError):
            blocked = [{"authorization_id": "?"}]  # 读不出 = 不出首签按钮(清单行自身已点名错误)
        if not blocked:
            view["buttons"].setdefault(grant_decl["row"], []).append("grant")
    for action, keys in exec_decl["rows"].items():
        for key in keys:
            if key in open_rows:
                view["buttons"].setdefault(key, []).append(action)
    if not owner:
        return view
    actions = {a for acts in view["buttons"].values() for a in acts}
    if "grant" in actions:
        view["grant"] = grant_prefill(root, str(proj["project"]))
    if "execution" in actions:
        view["execution"] = execution_candidates(root)
    if "model" in actions:
        unset = [values.get("role") for row in proj["rows"] if row["key"] in exec_decl["rows"]["model"]
                 for key, values in row["details"] if key == "rp_ob_detail_pi_model_unset"]
        view["model"] = {**model_candidates(setup_entry), "roles": list(pi_models_declaration()["roles"]),
                         "role": next((r for r in unset if r), None)}
    return view


# ---------------------------------------------------------------------------
# 写例外⑥: 首签项目授权(回调; 安全判定全在 handle_owner_write)
# ---------------------------------------------------------------------------

def _list_field(body: dict[str, Any], key: str, *, required: bool) -> list[str]:
    from tools.aipos_cli.envelope_board import MintRequestError, _split_list

    raw = body.get(key)
    if raw is not None and not isinstance(raw, (str, list)):
        raise MintRequestError("PARAMS_INVALID", detail=f"{key} 须为字符串列表或逗号分隔串")
    items = list(dict.fromkeys(_split_list(raw)))
    if required and not items:
        raise MintRequestError("PARAMS_INVALID", detail=f"{key} 至少一项")
    if any(any(ch.isspace() for ch in item) for item in items):
        raise MintRequestError("PARAMS_INVALID", detail=f"{key} 各项不得含空白: {items}")
    return items


def normalize_grant_params(body: dict[str, Any], *, project: str, governance_root: Path, now: datetime,
                           decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """表单 → 首签参数(预演与确认同一归一; 摘要据此算, 不含随时刻变化的值)。车道 = 每个仓 × 全部任务类型 × 全部驱动方(CLI 不带冒号的
    --grant-lane 同形); 总卡数必给正整数; 到期缺 = default_expiry。值域(仓可解析 / 任务类型 / 驱动方形 / 主机标签 / 拉起 / 禁区)由同一实现
    build_authorization_decision 在预演校验(拒因原文)。"""
    from tools.aipos_cli.envelope_board import MintRequestError, _parse_expiry, _parse_max_tasks

    d = decl or grant_declaration()
    repos = _list_field(body, "lanes", required=True)
    modes = _list_field(body, "task_modes", required=True)
    drivers = _list_field(body, "drivers", required=True)
    hosts = _list_field(body, "hosts", required=True)
    if body.get("max_tasks") is None or not str(body.get("max_tasks")).strip():
        raise MintRequestError("PARAMS_INVALID", detail="max_tasks 必给(总卡数硬上限)")
    raw_exp = body.get("expires_at")
    expires = _parse_expiry(raw_exp, now) if str(raw_exp or "").strip() else default_expiry(now, d)
    return {"project": project, "governance_root": str(governance_root), "mode": "authorization", "action": "grant", "request_item": "",
            "lanes": [{"repo": r, "task_modes": modes, "drivers": drivers} for r in repos], "hosts": hosts,
            "launch_harnesses": _list_field(body, "launch_harnesses", required=False),
            "zones_open": _list_field(body, "zones_open", required=False), "max_tasks": _parse_max_tasks(body["max_tasks"]),
            "expires_at": expires}


def handle_grant_request(
    body: dict[str, Any],
    *,
    session: dict[str, Any] | None,
    home_root: str | Path | None,
    preview_store: Any,
    audit_log_path: Path | None,
    connection_json: Path | None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = None,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """写例外⑥(唯一写路由): 安全判定全在 envelope_board.handle_owner_write(workspace = home 根已注册项目)。prepare 归一首签参数并拒已有未撤销
    项目授权(AUTHORIZATION_IN_USE); 预演 / 确认 / 凭据前置拒 = 写例外①项目授权同一回调(envelope_board._authorization_callbacks)。
    connection_json = 看板签发凭据库(写例外①同一份)。"""
    from tools.aipos_cli.autonomy_policy import authorization_overview, render_authorization_command
    from tools.aipos_cli.envelope_board import (
        MintRequestError,
        _authorization_callbacks,
        _authorization_payload,
        board_envelopes_declaration,
        handle_owner_write,
        session_provenance,
    )

    decl = grant_declaration()
    frame = board_envelopes_declaration()
    _prepare, preview_a, confirm_a, pre_confirm_a = _authorization_callbacks(frame, connection_json)

    def prepare(body: dict[str, Any], project: str, root: Path, now: datetime, session: dict[str, Any]) -> tuple[dict, dict, dict]:
        try:
            blocked = blocking_authorizations(authorization_overview(root), decl)
        except (OSError, ValueError, LookupError) as exc:  # 现有授权读不出 = 判不了是否重复签: 拒(不猜成放行)
            raise MintRequestError("PARAMS_INVALID", detail=f"现有项目授权读不出({type(exc).__name__}: {exc})") from exc
        if blocked:
            raise MintRequestError("AUTHORIZATION_IN_USE", authorizations=", ".join(
                f"{a['authorization_id']} r{a.get('revision')}({a.get('state')}, 已放行 {a.get('used')}/{a.get('max_tasks')}, 到期 {a.get('expires_at')})"
                for a in blocked), command=render_authorization_command(root, "extend", ["--max-tasks <N>", "--expires-at <YYYY-MM-DDTHH:MM:SSZ>"]))
        params = {**normalize_grant_params(body, project=project, governance_root=root, now=now, decl=decl),
                  "actor": session_provenance(frame, session)["captured_by"]}
        payload = _authorization_payload(params, frame, session, now)
        audit = {"project": project, "mode": "authorization", "grant_action": "grant", "policy_id": payload["project_authorization"].get("authorization_id"),
                 "lanes": [lane["repo"] for lane in params["lanes"]], "drivers": params["lanes"][0]["drivers"], "hosts": params["hosts"],
                 "max_tasks": params["max_tasks"], "expires_at": params["expires_at"], "actor": params["actor"],
                 "capture_surface": frame["capture_surface"]}
        return params, audit, {"payload": payload}

    def preview(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        out = preview_a(params, context)
        data = (out.get("dry_run") or {}).get("data") or {}
        return {**out, "authorization_markdown": data.get("authorization_markdown"),
                "capture_surface": frame["capture_surface"]}

    def confirm(params: dict[str, Any], context: dict[str, Any]) -> tuple[dict, dict, Any]:
        result, response, error = confirm_a(params, context)
        return {**result, "actor": params["actor"]}, response, error

    return handle_owner_write(body, decl=decl, action=str(decl["action"]), session=session, home_root=home_root, preview_store=preview_store,
                              audit_log_path=audit_log_path, prepare=prepare, preview=preview, confirm=confirm, pre_confirm=pre_confirm_a,
                              origin=origin, host=host, content_type=content_type, now=now)


# ---------------------------------------------------------------------------
# 写例外⑦: 执行方式 / pi 模型(一个路由按 action 分派; 安全判定全在 handle_owner_write)
# ---------------------------------------------------------------------------

def _text_field(body: dict[str, Any], key: str, *, required: bool) -> str | None:
    from tools.aipos_cli.envelope_board import MintRequestError

    raw = body.get(key)
    if raw is not None and not isinstance(raw, str):
        raise MintRequestError("PARAMS_INVALID", detail=f"{key} 须为字符串")
    value = (raw or "").strip() or None
    if required and value is None:
        raise MintRequestError("PARAMS_INVALID", detail=f"{key} 必给")
    if value is not None and (any(ch.isspace() for ch in value) if key != "owner_ref" else "\n" in value or "\r" in value):
        raise MintRequestError("PARAMS_INVALID", detail=f"{key} 不合: {value!r}")
    return value


def _refused(command: str, exc: Any) -> Any:
    from tools.aipos_cli.envelope_board import MintRequestError

    return MintRequestError("EXECUTION_REFUSED", command=command, refusal=exc.code, detail="; ".join(str(p) for p in exc.problems)[:1500])


def _run(params: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """CLI 同一实现: execution = enrollment.set_project_execution; model = enrollment.set_project_pi_model。拒 = ExecutionDeclarationError 上抛。"""
    from tools.aipos_cli.enrollment import set_project_execution, set_project_pi_model

    root = params["governance_root"]
    if params["action"] == "execution":
        return set_project_execution(root, subagent_executor=params["subagent_executor"], auditor=params["auditor"],
                                     pi_executor=params["pi_executor"], default_mode=params["default_mode"], dry_run=dry_run)
    return set_project_pi_model(root, model=params["model"], owner_ref=params["owner_ref"], role=params["role"], dry_run=dry_run)


def _outcome_view(params: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    keys = ("execution",) if params["action"] == "execution" else ("pi_models", "model", "role", "target_key", "affected", "effect_note")
    return {"project_json": str(outcome["project_json"]), "changed": outcome["changed"], "written": outcome["written"], "diff": outcome["diff"],
            **{k: outcome.get(k) for k in keys}}


def handle_execution_request(
    body: dict[str, Any],
    *,
    session: dict[str, Any] | None,
    home_root: str | Path | None,
    preview_store: Any,
    audit_log_path: Path | None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = None,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """写例外⑦(唯一写路由, 按 action 分派 execution / model): 安全判定全在 envelope_board.handle_owner_write(workspace = home 根已注册项目)。
    参数摘要 = {action, project, governance_root, 实例 / 模型参数, actor}: 预演后改实例 / 模型 / 角色 = 确认拒(PREVIEW_PARAMS_MISMATCH)。
    预演 = 同一实现 dry_run(project.json diff 全文; 拒 = EXECUTION_REFUSED 原文); 确认 = 同一实现写入 + board_adapter.land_after_write 落账。"""
    from tools.aipos_cli.envelope_board import MintRequestError, board_envelopes_declaration, handle_owner_write, session_provenance
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError, pi_models_declaration

    decl = execution_board_declaration()
    frame = board_envelopes_declaration()

    def prepare(body: dict[str, Any], project: str, root: Path, now: datetime, session: dict[str, Any]) -> tuple[dict, dict, dict]:
        from tools.aipos_cli.workspace_config import project_execution

        action = str(body.get("action") or "").strip()
        if action not in decl["actions"]:
            raise MintRequestError("PARAMS_INVALID", detail=f"action 须为 {list(decl['actions'])}")
        actor = session_provenance(frame, session)["captured_by"]
        params: dict[str, Any] = {"action": action, "project": project, "governance_root": str(root), "actor": actor}
        if action == "execution":
            try:
                current = project_execution(root) or {}
            except (OSError, ValueError) as exc:  # 现声明读不出 = 不猜缺省执行方式(同一实现预检亦会拒)
                raise MintRequestError("PARAMS_INVALID", detail=f"project.json execution 读不出({type(exc).__name__}: {exc})") from exc
            params.update({"subagent_executor": _text_field(body, "subagent_executor", required=True),
                           "auditor": _text_field(body, "auditor", required=True),
                           "pi_executor": _text_field(body, "pi_executor", required=False),
                           "default_mode": _text_field(body, "default_mode", required=False) or current.get("default_mode")})
            audit = {k: params[k] for k in ("subagent_executor", "auditor", "pi_executor", "default_mode")}
        else:
            role = _text_field(body, "role", required=False)
            if role is not None and role not in pi_models_declaration()["roles"]:
                raise MintRequestError("PARAMS_INVALID", detail=f"role 须为 {pi_models_declaration()['roles']} 或留空(项目缺省)")
            params.update({"model": _text_field(body, "model", required=True), "role": role,
                           "owner_ref": _text_field(body, "owner_ref", required=False) or actor})
            audit = {k: params[k] for k in ("model", "role", "owner_ref")}
        return params, {"project": project, "setup_action": action, **audit, "actor": actor, "capture_surface": frame["capture_surface"]}, {}

    def preview(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        command = str(decl["actions"][params["action"]])
        try:
            outcome = _run(params, dry_run=True)
        except ExecutionDeclarationError as exc:
            raise _refused(command, exc) from exc
        return {"params": params, "command": command, "plan": _outcome_view(params, outcome), "capture_surface": frame["capture_surface"]}

    def confirm(params: dict[str, Any], context: dict[str, Any]) -> tuple[dict, dict, Any]:
        from tools.aipos_cli.board_adapter import land_after_write, landing_view

        command = str(decl["actions"][params["action"]])
        fields = {"actor": params["actor"], "capture_surface": frame["capture_surface"]}
        try:
            outcome = _run(params, dry_run=False)
        except ExecutionDeclarationError as exc:  # 预演后声明 / 接入有变: 同一实现拒因原文, project.json 未改动
            return {**fields, "outcome": "refused", "error": exc.code}, {}, _refused(command, exc)
        landing = land_after_write(params["governance_root"], [outcome["project_json"]], actor=params["actor"], command="project_writers",
                                   subcommand=command, project=params["project"])
        result = {**fields, "outcome": "written" if outcome["written"] else "unchanged", "landed": bool(landing and landing.get("landed")),
                  "commit": (landing or {}).get("commit_hash")}
        response = {"ok": True, "stage": "confirm", "params": params, "actor": params["actor"], "captured_by": params["actor"],
                    "capture_surface": fields["capture_surface"], "command": command, "plan": _outcome_view(params, outcome),
                    "landing": landing_view(landing), "landing_line": (landing or {}).get("line")}
        return result, response, None

    return handle_owner_write(body, decl=decl, action=str(decl["action"]), session=session, home_root=home_root, preview_store=preview_store,
                              audit_log_path=audit_log_path, prepare=prepare, preview=preview, confirm=confirm, origin=origin, host=host,
                              content_type=content_type, now=now)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
