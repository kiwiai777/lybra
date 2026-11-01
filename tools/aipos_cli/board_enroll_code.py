"""AIPOS-F270 — 看板写例外⑤「签注册码」(产品化 P3 第三张): Owner 会话在接入页为项目的顾问 / 执行 / 审计实例预演 → 确认签发注册码。

一句话: 装好 Lybra 的 Owner 不开终端也能给新项目的各角色实例签码——接入页未完成的接入项旁点「签注册码」, 先看将绑定什么, 确认后码只显示一次。

防碎片化(卡面硬约束), 本模块只给 F172 同一安全框架的三个回调 + 声明读取 + 角色清单 / 建议实例名 / 待兑换码视图, 不另写任何一步的实现:
- 安全判定 = envelope_board.handle_owner_write(Owner 会话 / Origin / JSON / stage / workspace = home 根已注册项目 / 一次性预演令牌六类 /
  看板审计日志), workspace 解析方式缺省 registered。
- 预演 / 确认 = CLI `lybra roles enroll-code` 同一实现 enroll_deliver.gate_enroll_code(门动词 lybra_enroll_code_dry_run → _confirm;
  门内 enrollment.issue_self_contained_code → create_enrollment_code, 发码只住门进程); 门凭据 = 看板签发凭据库(写例外①同一取法
  two_phase_shell_factory.load_role_token, 角色 = board_owner_envelopes.token_role)。
- 实例名规则 = 既有校验: enrollment.governance_seat_instance_refusal(治理席位类必填)+ roles.schema executor_modes(子 agent 执行者
  requires_instance, 判据 naming_profile.parse_instance_name); 门地址 = enrollment.resolve_gate_url_default(CLI / 门同一缺省推导)。
- 角色清单 = roles.schema 注册表(class_groups workstation / governance_seat)+ custom_roles.load_custom_roles; 建议实例名 =
  naming_profile.default_instance_name; 待兑换码 = enrollment.list_enrollment_codes(enroll-list 同一实现, 不含原文)。
- 交给对方的一句话 = 门应答 paste_text(onboarding.enroll_delivery 同一渲染); 作废出口 = 声明 exits(enroll-revoke / enroll-void)。
码原文只进确认应答(code.self_contained_code / paste_text); 审计日志只记角色 / 实例 / 时效 / 到期 / 码编号 / 码指纹。任何项目 / 角色同一组声明,
不写死项目名、实例名、主机或路径。界面文字在看板 i18n 单通道(web/board/app.py, 前缀 board.text_prefix)。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

VERB = "lybra_roles_enroll_code"
#: 声明 verbs.lybra_roles_enroll_code.board 必有的键
_BOARD_KEYS = ("page", "write_path", "script_path", "action", "text_prefix", "row_roles", "exits", "errors")
_EXIT_KEYS = ("revoke", "void")
#: 清单行明细里点名实例的文案键(board_pages._instance_rows): 未接入 / 无工位 / 读不出 = 该实例待签码
_OPEN_INSTANCE_DETAILS = ("rp_ob_detail_instance_missing", "rp_ob_detail_instance_no_ws", "rp_ob_detail_instance_error")
_OPEN_STATES = ("todo", "error")


def board_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_roles_enroll_code.board(fail-closed): 键齐; 路径以 / 起首; page ∈ 看板只读页; row_roles 键 ∈ 接入清单项;
    exits 有 revoke / void 模板; errors 每码有 int status 与非空 message。"""
    from tools.aipos_cli.board_pages import ONBOARDING_CHECKS, PAGES
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = ((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(VERB) or {}).get("board")
    where = f"verbs.schema.json verbs.{VERB}.board"
    if not isinstance(decl, dict) or any(k not in decl for k in _BOARD_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_BOARD_KEYS)}")
    if (any(not str(decl[k] or "").startswith("/") for k in ("write_path", "script_path")) or decl["page"] not in PAGES
            or not all(isinstance(decl[k], str) and decl[k] for k in ("action", "text_prefix"))):
        raise SchemaLoadError(f"{where}: write_path / script_path 须以 / 起首, page ∈ {list(PAGES)}, action / text_prefix 非空")
    rows = decl["row_roles"]
    if not isinstance(rows, dict) or not rows or any(k not in ONBOARDING_CHECKS or not isinstance(v, str) or not v for k, v in rows.items()):
        raise SchemaLoadError(f"{where}.row_roles 须为 {{接入清单项: 角色名}}, 清单项 ∈ {list(ONBOARDING_CHECKS)}")
    exits = decl["exits"]
    if not isinstance(exits, dict) or any(not isinstance(exits.get(k), str) or not exits[k] for k in _EXIT_KEYS):
        raise SchemaLoadError(f"{where}.exits 须有非空模板 {list(_EXIT_KEYS)}")
    for code, spec in (decl["errors"] or {}).items():
        if not isinstance(spec, dict) or not isinstance(spec.get("status"), int) or not str(spec.get("message") or "").strip():
            raise SchemaLoadError(f"{where}.errors.{code} 须有 int status 与非空 message")
    return decl


# ---------------------------------------------------------------------------
# 角色清单 / 建议实例名 / 清单行的签码目标 / 待兑换码(只读视图; 页面与回调同读)
# ---------------------------------------------------------------------------

def role_choices(governance_root: str | Path) -> list[str]:
    """本项目可签码的角色: roles.schema 注册表中属 workstation / governance_seat 分组的内建角色(注册表顺序)+ 门注册表自定义角色
    (custom_roles.load_custom_roles, 其类同属这两组)。门注册表读不出 = RoleRegistryReadError 上抛(调用方点名, 不当「无自定义角色」)。"""
    from tools.aipos_cli.custom_roles import load_custom_roles, resolve_role_to_class, role_classes_in_group
    from tools.schema_loader import get_all_role_names

    classes = set(role_classes_in_group("workstation")) | set(role_classes_in_group("governance_seat"))
    out = [r for r in get_all_role_names() if resolve_role_to_class(r, governance_root) in classes]
    for name, spec in sorted(load_custom_roles(governance_root).items()):
        if name not in out and str((spec or {}).get("class") or "") in classes:
            out.append(name)
    return out


def suggested_instance(role: str, project: str) -> str | None:
    """建议实例名 = naming_profile.default_instance_name(roles.schema naming.template; 前缀 = 该角色注册表 naming.prefix; 主机段 = 看板所在机,
    即治理根所在机)。角色无注册前缀(如自定义角色)= None(不猜, 由 Owner 填)。"""
    from tools.aipos_cli.naming_profile import _registry_prefix_mapping, default_instance_name

    prefix = _registry_prefix_mapping().get(role)
    return default_instance_name(prefix, project=project) if prefix else None


def enroll_targets(project: str, row: dict[str, Any], decl: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """接入清单一行(board_pages.onboarding_rows 同一推导, 不另算)的签码目标: 只对声明 row_roles 内的行、且行未完成(todo / error)。
    行明细点名的未接入 / 无工位 / 读不出实例 = 各一个目标(预填该实例); 没点名实例(未接入顾问 / 执行方式未声明)= 一个建议名目标。"""
    d = decl or board_declaration()
    role = d["row_roles"].get(row.get("key"))
    if not role or row.get("state") not in _OPEN_STATES:
        return []
    named = [str(values["instance"]) for key, values in row.get("details") or [] if key in _OPEN_INSTANCE_DETAILS and values.get("instance")]
    if named:
        return [{"role": role, "instance": name, "suggested": False} for name in dict.fromkeys(named)]
    return [{"role": role, "instance": suggested_instance(role, project), "suggested": True}]


def _row_for_role(role: str, governance_root: Path, decl: dict[str, Any]) -> str | None:
    """码的角色 → 清单行: 角色类与行角色类相同; 否则同属治理席位组(如规划方码列在顾问行)。解析不到 = None(不列)。"""
    from tools.aipos_cli.custom_roles import resolve_role_to_class, role_classes_in_group

    cls = resolve_role_to_class(role, governance_root)
    if not cls:
        return None
    seat = set(role_classes_in_group("governance_seat"))
    for key, row_role in decl["row_roles"].items():
        row_cls = resolve_role_to_class(row_role, governance_root)
        if cls == row_cls or (cls in seat and row_cls in seat):
            return key
    return None


def pending_codes(registry_root: str | Path | None, governance_root: str | Path, decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """已签未兑换的码(enrollment.list_enrollment_codes, enroll-list 同一实现, include_code=False: 不读原文), 只取签给本治理根且状态 pending 的,
    按角色归到清单行: {"rows": {行键: [{code_id, fingerprint, role, instance, expires_at, revoke}]}} 或 {"error": 拒因}(注册码库读不出 / 看板无工作区)。
    注册码库 = 看板服务工作区(签发凭据库所在工作区; 门按该凭据项目路由发码的同一注册表)。"""
    from tools.aipos_cli.enrollment import list_enrollment_codes

    d = decl or board_declaration()
    if registry_root is None:
        return {"rows": {}, "error": None}
    root = Path(governance_root).expanduser().resolve()
    try:
        codes = list_enrollment_codes(registry_root, include_code=False)
    except (OSError, ValueError) as exc:
        return {"rows": {}, "error": f"{type(exc).__name__}: {exc}"}
    rows: dict[str, list[dict[str, Any]]] = {}
    for c in codes:
        if c.get("status") != "pending" or not c.get("governance_root"):
            continue
        if Path(str(c["governance_root"])).expanduser().resolve() != root:
            continue
        key = _row_for_role(str(c.get("role") or ""), root, d)
        if key is None:
            continue
        rows.setdefault(key, []).append({k: c.get(k) for k in ("code_id", "fingerprint", "role", "instance", "expires_at")}
                                        | {"revoke": exit_commands(d, code_id=str(c["code_id"]), instance=c.get("instance"),
                                                                   project=root.name)["revoke"]})
    return {"rows": rows, "error": None}


def exit_commands(decl: dict[str, Any], *, code_id: str, instance: str | None, project: str) -> dict[str, str]:
    """作废重签出口(声明 exits 模板): revoke = 未兑换的码(enroll-revoke <code_id>); void = 已兑换的接入事件(enroll-void, 作用域旗标读
    two_phase_protocol.enrollment_log_writers.scope.flags, 与 CLI 注册同一来源)。"""
    import shlex

    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    flags = (((load_schema("verbs", code_repo_schema_root()).get("two_phase_protocol") or {}).get("enrollment_log_writers") or {})
             .get("scope") or {}).get("flags") or {}
    if not all(isinstance(flags.get(k), str) and flags[k].startswith("--") for k in ("project", "code_id")):
        raise SchemaLoadError("verbs.schema.json two_phase_protocol.enrollment_log_writers.scope.flags 须有 project / code_id")
    values = {"code_id": shlex.quote(code_id), "instance": shlex.quote(instance or "<实例>"), "project": shlex.quote(project),
              "project_flag": flags["project"], "code_id_flag": flags["code_id"]}
    try:
        return {k: str(decl["exits"][k]).format_map(values) for k in _EXIT_KEYS}
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}.board.exits 模板占位无值: {exc}") from exc


# ---------------------------------------------------------------------------
# 写例外⑤: 三回调(安全判定全在 handle_owner_write)
# ---------------------------------------------------------------------------

def _instance_rule(role: str, instance: str | None, root: Path) -> None:
    """实例名既有校验: 治理席位类必填(enrollment.governance_seat_instance_refusal, 门 dry-run 同一判定); 项目已声明子 agent 执行者时执行类
    必填且须合实例名模板(roles.schema executor_modes 中凭据落治理根且 requires_instance 的模式, 拒因读 reject_codes)。"""
    from tools.aipos_cli.custom_roles import resolve_role_to_class
    from tools.aipos_cli.enrollment import executor_mode_declaration, governance_seat_instance_refusal
    from tools.aipos_cli.envelope_board import MintRequestError
    from tools.aipos_cli.naming_profile import parse_instance_name
    from tools.aipos_cli.workspace_config import project_execution

    refusal = governance_seat_instance_refusal(role, instance, root)
    if refusal:
        raise MintRequestError("INSTANCE_REQUIRED", detail=refusal)
    modes = executor_mode_declaration()
    cls = resolve_role_to_class(role, root)
    strict = [m for m, spec in modes["values"].items()
              if spec.get("requires_instance") and spec["credential_landing"] == "governance_root" and cls in spec["allowed_role_classes"]]
    if not strict:
        return
    try:
        execution = project_execution(root)
    except (OSError, ValueError) as exc:  # 执行方式声明读不出 = 判不了是否子 agent 执行者: 拒(不猜成放行)
        raise MintRequestError("PARAMS_INVALID", detail=f"project.json execution 读不出({type(exc).__name__}: {exc})") from exc
    subagent = str((execution or {}).get("subagent_executor") or "").strip()
    if not subagent:
        return
    text = str(modes["reject_codes"]["EXECUTOR_MODE_INSTANCE_REQUIRED"])
    if not instance:
        raise MintRequestError("INSTANCE_REQUIRED", detail=f"本项目已声明子 agent 执行者 {subagent}, {role} 码须带实例: {text}")
    if instance == subagent and parse_instance_name(instance) is None:
        raise MintRequestError("INSTANCE_INVALID", detail=f"{instance!r} 不合实例名模板: {text}")


def _parse_ttl(raw: Any) -> int | None:
    """时效(秒): 缺 / 空 = None(CLI 缺省, 门按 ENROLL_DEFAULT_TTL_SECONDS); 否则正整数(门 INVALID_TTL 同一规则)。"""
    from tools.aipos_cli.envelope_board import MintRequestError

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise MintRequestError("PARAMS_INVALID", detail=f"ttl 须为正整数(秒), 得到 {raw!r}")
    try:
        ttl = int(str(raw).strip())
    except ValueError as exc:
        raise MintRequestError("PARAMS_INVALID", detail=f"ttl 须为正整数(秒), 得到 {raw!r}") from exc
    if ttl <= 0:
        raise MintRequestError("PARAMS_INVALID", detail=f"ttl 须为正整数(秒), 得到 {raw!r}")
    return ttl


def _gate_client(connection_json: Path | None, token_role: str) -> tuple[Any, str]:
    """门客户端: 看板签发凭据库(写例外①同一取法 load_role_token; 门基址唯一推导口 resolve_gate_base_url, 须声明 mcp.rpc_url)。
    凭据原文只在本进程内作 HTTP Authorization, 返回 (client, 凭据指纹)。"""
    from tools.aipos_cli.confirm_client import GateAddressError, GateClient, resolve_gate_base_url, token_fingerprint
    from tools.aipos_cli.envelope_board import MintRequestError
    from tools.aipos_cli.two_phase_shell_factory import load_role_token

    if connection_json is None or not Path(connection_json).is_file():
        raise MintRequestError("CREDENTIAL_UNAVAILABLE", role=token_role, detail=f"凭据文件不存在: {connection_json}")
    try:
        token = load_role_token(connection_json_path=str(connection_json), role=token_role)
    except (OSError, ValueError) as exc:
        raise MintRequestError("CREDENTIAL_UNAVAILABLE", role=token_role, detail=f"{type(exc).__name__}: {exc}") from exc
    try:
        base_url = resolve_gate_base_url(connection_json=connection_json, require_declared=True)
    except GateAddressError as exc:
        raise MintRequestError("GATE_UNAVAILABLE", detail=str(exc)) from exc
    return GateClient(base_url, token, timeout=30.0), token_fingerprint(token)


def _gate_args(params: dict[str, Any]) -> dict[str, Any]:
    return {k: params[k] for k in ("role", "instance", "ttl", "gate_url", "governance_root", "owner_authorization_ref", "actor")}


def _call_gate(params: dict[str, Any], connection_json: Path | None, token_role: str, *, confirm: bool) -> tuple[dict[str, Any], str]:
    """CLI 同一实现 enroll_deliver.gate_enroll_code; 门拒 = GATE_REFUSED(门原文, 不含码), 门连不上 = GATE_UNAVAILABLE。"""
    from tools.aipos_cli.confirm_client import GateError
    from tools.aipos_cli.enroll_deliver import GateVerbRefused, gate_enroll_code
    from tools.aipos_cli.envelope_board import MintRequestError

    client, fingerprint = _gate_client(connection_json, token_role)
    try:
        return gate_enroll_code(client, _gate_args(params), confirm=confirm), fingerprint
    except GateVerbRefused as exc:
        resp = exc.response or {}
        detail = " ".join(str(resp.get(k) or "") for k in ("error", "message", "next_step") if resp.get(k)) or str(exc)
        raise MintRequestError("GATE_REFUSED", verb=exc.verb, detail=detail[:800]) from exc
    except GateError as exc:
        raise MintRequestError("GATE_UNAVAILABLE", detail=str(exc)[:800]) from exc


def handle_enroll_code_request(
    body: dict[str, Any],
    *,
    session: dict[str, Any] | None,
    home_root: str | Path | None,
    preview_store: Any,
    audit_log_path: Path | None,
    connection_json: Path | None,
    workspace_root: Path | None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = None,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """写例外⑤(唯一写路由): 安全判定全在 envelope_board.handle_owner_write(workspace = home 根已注册项目); 本函数只给三个回调。
    请求体: workspace = 项目名, role, instance(可缺), ttl(可缺 = CLI 缺省), stage, preview_token。参数摘要 = {action, project, governance_root,
    role, instance, ttl, gate_url, actor, owner_authorization_ref}: 预演后改角色 / 实例 / 时效 = 确认拒(PREVIEW_PARAMS_MISMATCH)。
    connection_json = 看板签发凭据库(写例外①同一份); workspace_root = 看板服务工作区(门地址缺省推导 + 注册码库)。"""
    from tools.aipos_cli.envelope_board import MintRequestError, board_envelopes_declaration, handle_owner_write, session_provenance

    board = board_declaration()
    frame = board_envelopes_declaration()
    token_role = str(frame["token_role"])

    def prepare(body: dict[str, Any], project: str, root: Path, now: Any, session: dict[str, Any]) -> tuple[dict, dict, dict]:
        from tools.aipos_cli.confirm_client import GateAddressError
        from tools.aipos_cli.custom_roles import RoleRegistryReadError
        from tools.aipos_cli.enrollment import resolve_gate_url_default
        from tools.aipos_cli.workspace_config import WorkstationDeclarationError

        raw_role, raw_instance = body.get("role"), body.get("instance")
        if not isinstance(raw_role, str) or (raw_instance is not None and not isinstance(raw_instance, str)):
            raise MintRequestError("PARAMS_INVALID", detail="role 须为字符串, instance 须为字符串或留空")
        role, instance = raw_role.strip(), (raw_instance or "").strip() or None
        try:
            choices = role_choices(root)
        except RoleRegistryReadError as exc:  # 门注册表读不出 = 判不了自定义角色: 拒(不当「无此角色」)
            raise MintRequestError("PARAMS_INVALID", detail=f"门注册表读不出({exc})") from exc
        if role not in choices:
            raise MintRequestError("ROLE_INVALID", role=role, choices=", ".join(choices))
        if instance and any(ch.isspace() for ch in instance):
            raise MintRequestError("INSTANCE_INVALID", detail=f"{instance!r} 含空白")
        _instance_rule(role, instance, root)
        ttl = _parse_ttl(body.get("ttl"))
        if workspace_root is None:
            raise MintRequestError("GATE_URL_UNRESOLVED", detail="看板服务进程无工作区根")
        try:
            gate = resolve_gate_url_default(workspace_root, instance=instance, governance_root=str(root))
        except (GateAddressError, WorkstationDeclarationError, ValueError, OSError) as exc:
            raise MintRequestError("GATE_URL_UNRESOLVED", detail=f"{type(exc).__name__}: {exc}") from exc
        provenance = session_provenance(frame, session)
        actor = provenance["captured_by"]
        params = {"action": str(board["action"]), "project": project, "governance_root": str(root), "role": role, "instance": instance,
                  "ttl": ttl, "gate_url": gate["url"], "actor": actor, "owner_authorization_ref": actor}
        audit_fields = {"project": project, "role": role, "instance": instance, "ttl": ttl, "actor": actor,
                        "capture_surface": provenance["capture_surface"]}
        return params, audit_fields, {"provenance": provenance, "gate_url_source": gate["source"]}

    def preview(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        issued, fingerprint = _call_gate(params, connection_json, token_role, confirm=False)
        dry = issued["dry_run"]
        return {"params": params, "preview": dry.get("preview") or {}, "gate_url_source": context["gate_url_source"],
                "gate_verb": dry.get("operation"), "credential_role": token_role, "credential_fingerprint": fingerprint,
                "capture_surface": context["provenance"]["capture_surface"]}

    def confirm(params: dict[str, Any], context: dict[str, Any]) -> tuple[dict, dict, Any]:
        fields = {"actor": params["actor"], "capture_surface": context["provenance"]["capture_surface"]}
        try:
            issued, fingerprint = _call_gate(params, connection_json, token_role, confirm=True)
        except MintRequestError as err:
            return {**fields, "outcome": "enroll_code_failed", "error": err.code}, {}, err
        conf = issued["confirm"]
        # 码原文只进应答, 不进审计: 审计只记角色 / 实例 / 时效 / 到期 / 码编号 / 码指纹
        result = {**fields, "outcome": "issued", "code_id": conf.get("code_id"), "code_fingerprint": conf.get("fingerprint"),
                  "role": conf.get("role"), "instance": conf.get("instance"), "ttl_seconds": conf.get("ttl_seconds"),
                  "expires_at": conf.get("expires_at"), "gate_url": conf.get("gate_url"), "governance_root": conf.get("governance_root")}
        code = {k: conf.get(k) for k in ("code_id", "self_contained_code", "paste_text", "paste_instruction", "next_step", "fingerprint",
                                          "role", "instance", "ttl_seconds", "expires_at", "gate_url", "gate_url_source", "governance_root")}
        exits = exit_commands(board, code_id=str(conf.get("code_id") or ""), instance=conf.get("instance"), project=params["project"])
        response = {"ok": True, "stage": "confirm", "params": params, "actor": params["actor"], "captured_by": params["actor"],
                    "capture_surface": fields["capture_surface"], "credential_role": token_role, "credential_fingerprint": fingerprint,
                    "code": code, "exits": exits, "warnings": conf.get("warnings") or []}
        return result, response, None

    return handle_owner_write(body, decl=board, action=str(board["action"]), session=session, home_root=home_root,
                              preview_store=preview_store, audit_log_path=audit_log_path, prepare=prepare, preview=preview,
                              confirm=confirm, origin=origin, host=host, content_type=content_type, now=now)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
