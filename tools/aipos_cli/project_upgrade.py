"""AIPOS-F176 — 项目升级: `lybra project upgrade` 预演 / 确认 + 看板升级页(写例外②) + 「告诉顾问」引导。

一句话: 一条命令(和看板上一个按钮)把项目跟上最新版本——数据迁移、配置补齐、工位同步一次做完; 做不到的新能力, 逐个顾问写好该怎么告诉他。

防碎片化(卡面硬约束), 本模块只编排, 不另写任何一步的实现:
- 步骤清单 = verbs.schema verbs.lybra_project_upgrade.upgrade_steps(每步 名称 / 适用条件 / 预演函数 / 执行函数 / 是否机械 / 落账命令;
  只追加)。预演 / 执行函数按声明的点路径解析(只认 tools.aipos_cli.*), 各步只有一个结果适配(_ADAPTERS, 把既有函数的结果读成同一形):
    config_defaults       = workspace_config.fill_project_json_defaults(update_project_json 唯一写路径) + F159 project_writers 落账
    workstation_charter   = distribution_sync.charter_freshness(= `lybra sync --dry-run`)/ sync_workstation_charter(= `lybra sync`, F164)
    record_type           = record_type_migration.run_record_type_migration(F158, 自带落账)
    decision_log_pointers = decision_log_pointers.run_pointer_backfill(F160, 自带落账)
- 工位 = board_pages.workstation_instances(F174 接入清单同一推导: 执行方式声明 + 接入日志); 未读更新 = advisor_updates_cli.advisor_view(F167)。
- 引导 = 一个推导函数 derive_guidance: F167 未读更新的 advisor_notice actions + F174 接入清单缺项(board_pages.onboarding_rows)+
  近期卡卡面字段(task_loader.iter_queue_task_paths); 话术 / 判据文案读声明 guidance。
- 看板写路由 = envelope_board.handle_owner_write(F172 写例外同一安全框架: Owner 会话 / workspace / 一次性预演令牌 / Origin / 审计日志)。
- 落账 = governance_commit.land_written_paths(F158 唯一实现); 任何项目同一组命令, 不写死项目 ID / 路径 / 卡号前缀。
"""
from __future__ import annotations

import html
import importlib
import json
import subprocess
from pathlib import Path
from typing import Any

VERB = "lybra_project_upgrade"
CONDITIONS = ("project_json", "local_workstations", "git_governance_root")
STATES = ("pending", "current", "not_applicable", "error", "unchecked")
OUTCOMES = ("done", "skipped", "failed", "not_run")
GUIDANCE_SOURCES = ("notice", "onboarding", "card_field")
_TEXT_KEYS = ("head_preview", "head_confirm", "step_line", "change_line", "detail_line", "run_line", "unread_head", "unread_line",
              "unread_unavailable", "unread_error", "unread_none", "summary_preview", "summary_confirm", "stopped", "nothing",
              "guidance_head", "guidance_group", "guidance_item", "guidance_say", "guidance_done", "guidance_none", "guidance_error")
_BOARD_TEXT_KEYS = ("title", "project_heading", "steps_heading", "guidance_heading", "preview_button", "confirm_button", "cancel_button",
                    "preview_heading", "result_heading", "readonly_hint", "page_error")


class UpgradeError(RuntimeError):
    """升级不可进行(声明读不出以外的前提不成立), 消息带原因。"""


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def upgrade_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_project_upgrade(fail-closed): 步骤名唯一且都有结果适配; 适用条件已实现且已说明; 预演 / 执行函数可解析;
    落账命令已登记于 post_write_landing; 状态 / 结果 / 文案 / 引导 / 看板键齐。"""
    from tools.aipos_cli.board_pages import ONBOARDING_CHECKS
    from tools.schema_loader import SchemaLoadError, load_schema

    verbs = load_schema("verbs")
    decl = (verbs.get("verbs") or {}).get(VERB)
    where = f"verbs.schema.json verbs.{VERB}"
    if not isinstance(decl, dict):
        raise SchemaLoadError(f"{where} 未声明")
    steps = decl.get("upgrade_steps")
    if not isinstance(steps, list) or not steps:
        raise SchemaLoadError(f"{where}.upgrade_steps 须为非空列表")
    landing_cmds = (((verbs.get("verbs") or {}).get("lybra_governance_commit") or {}).get("post_write_landing") or {}).get("commands") or {}
    conditions = decl.get("applies_when") or {}
    names: list[str] = []
    for i, step in enumerate(steps):
        need = ("name", "label", "why", "applies_when", "preview", "execute", "mechanical", "landing")
        if not isinstance(step, dict) or any(k not in step for k in need):
            raise SchemaLoadError(f"{where}.upgrade_steps[{i}] 须有 {list(need)}")
        name = str(step["name"])
        if name in names or name not in _ADAPTERS:
            raise SchemaLoadError(f"{where}.upgrade_steps[{i}].name={name!r} 重复或无结果适配(已实现 {sorted(_ADAPTERS)}; 新步骤只追加且须先实现)")
        names.append(name)
        if step["applies_when"] not in CONDITIONS or step["applies_when"] not in conditions:
            raise SchemaLoadError(f"{where}.upgrade_steps[{i}].applies_when={step['applies_when']!r} 须 ∈ {list(CONDITIONS)} 且在 applies_when 说明")
        if (not isinstance(step["mechanical"], bool) or (step["landing"] is not None and step["landing"] not in landing_cmds)
                or not isinstance(step.get("preview_command", ""), str)):
            raise SchemaLoadError(f"{where}.upgrade_steps[{i}]: mechanical 须为布尔, landing 须为 null 或 post_write_landing.commands 的键, "
                                  "preview_command(可选)须为串")
        for key in ("preview", "execute"):
            resolve_function(str(step[key]), f"{where}.upgrade_steps[{i}].{key}")
    if set(decl.get("step_states") or {}) != set(STATES) or set(decl.get("run_outcomes") or {}) != set(OUTCOMES):
        raise SchemaLoadError(f"{where}.step_states / run_outcomes 键须恰为 {list(STATES)} / {list(OUTCOMES)}")
    if any(not isinstance((decl.get("texts") or {}).get(k), str) for k in _TEXT_KEYS):
        raise SchemaLoadError(f"{where}.texts 须有 {list(_TEXT_KEYS)}")
    guidance = decl.get("guidance") or {}
    unknown = sorted(set(guidance.get("onboarding_items") or {}) - set(ONBOARDING_CHECKS))
    recent = guidance.get("recent_cards")
    if (unknown or not isinstance(recent, int) or isinstance(recent, bool) or recent < 1
            or not all(isinstance(guidance.get(k), str) for k in ("tell_line", "unknown_advisor"))
            or not isinstance(guidance.get("card_task_modes"), list)
            or not all(isinstance(v, dict) and all(isinstance(v.get(k), str) for k in ("why", "say", "done_when"))
                       for group in ("onboarding_items", "card_fields") for v in (guidance.get(group) or {}).values())
            or not all(isinstance((guidance.get("notice") or {}).get(k), str) for k in ("why", "say", "done_when"))):
        raise SchemaLoadError(f"{where}.guidance(recent_cards 正整数 / card_task_modes / tell_line / unknown_advisor / notice / "
                              f"onboarding_items ⊆ {list(ONBOARDING_CHECKS)} / card_fields, 每项 why·say·done_when)未声明齐; 未实现项 {unknown}")
    board = decl.get("board") or {}
    if (any(not str(board.get(k) or "").startswith("/") for k in ("page_path", "write_path", "script_path"))
            or not str(board.get("action") or "") or any(not isinstance((board.get("texts") or {}).get(k), str) for k in _BOARD_TEXT_KEYS)
            or not isinstance(board.get("errors"), dict)):
        raise SchemaLoadError(f"{where}.board(page_path / write_path / script_path / action / texts / errors)未声明齐")
    for code, spec in board["errors"].items():
        if not isinstance(spec, dict) or not isinstance(spec.get("status"), int) or not str(spec.get("message") or "").strip():
            raise SchemaLoadError(f"{where}.board.errors.{code} 须有 int status 与非空 message")
    codes = decl.get("exit_codes") or {}
    if not all(isinstance((codes.get(k) or {}).get("code"), int) for k in ("ok", "refused", "usage", "step_failed")):
        raise SchemaLoadError(f"{where}.exit_codes(ok / refused / usage / step_failed)未声明齐")
    return decl


def resolve_function(dotted: str, where: str = "upgrade step") -> Any:
    """声明的预演 / 执行函数点路径 → 可调用对象(只认 tools.aipos_cli.* 模块内的函数); 解析不出 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError

    module_name, _, attr = str(dotted).rpartition(".")
    if not module_name.startswith("tools.aipos_cli.") or not attr:
        raise SchemaLoadError(f"{where}={dotted!r} 须为 tools.aipos_cli.<模块>.<函数>")
    try:
        fn = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise SchemaLoadError(f"{where}={dotted!r} 解析不出: {exc}") from exc
    if not callable(fn):
        raise SchemaLoadError(f"{where}={dotted!r} 不是函数")
    return fn


def _fmt(template: Any, **values: Any) -> str:
    from tools.schema_loader import SchemaLoadError

    try:
        return str(template).format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB} 文案模板占位无值: {template!r}: {exc}") from exc


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _step_errors() -> tuple[type[BaseException], ...]:
    """单步预演 / 执行的捕获面(声明读不出 SchemaLoadError 不在内: 整条命令 fail-closed)。"""
    from tools.aipos_cli.custom_roles import RoleRegistryReadError

    return (OSError, ValueError, RuntimeError, LookupError, subprocess.SubprocessError, RoleRegistryReadError)


# ---------------------------------------------------------------------------
# 推导上下文(每个项目一份; 各面只算一次)
# ---------------------------------------------------------------------------

class _Ctx:
    def __init__(self, root: Path, project: str, home_root: str | Path | None, *, charter_mode: str, running: Any) -> None:
        self.root = Path(root)
        self.project = str(project)
        self.home_root = home_root
        self.charter_mode = charter_mode
        self.running = running
        self._facts: Any = None

    @property
    def facts(self) -> Any:
        """F174 项目事实(board_pages.ProjectFacts; 治理根 = 本目标, 不要求在 home 根注册)。"""
        if self._facts is None:
            from tools.aipos_cli.board_pages import ProjectFacts

            entry = {"name": self.project, "root": self.root, "index": -1, "status": None, "paused": False, "status_error": None}
            self._facts = ProjectFacts(entry, Path(self.home_root) if self.home_root else self.root.parent)
        return self._facts

    def stations(self) -> dict[str, Any]:
        """有工位的已接入实例(F174 同一推导): {local: [view], remote: [view], error: str | None}。"""
        from tools.aipos_cli.board_pages import workstation_instances

        got = self.facts.execution()
        if "error" in got:
            return {"local": [], "remote": [], "error": got["error"]}
        views = workstation_instances(self.facts)
        return {"local": [v for v in views if v["workstation"].get("transport") != "remote"],
                "remote": [v for v in views if v["workstation"].get("transport") == "remote"], "error": None}


def _applies(ctx: _Ctx, condition: str) -> tuple[bool, str]:
    from tools.aipos_cli.workspace_config import project_json_path

    if condition == "project_json":
        path = project_json_path(ctx.root)
        return path.is_file(), f"{path} 不存在"
    if condition == "git_governance_root":
        from tools.aipos_cli.governance_guardrails import git_toplevel

        try:
            git_toplevel(ctx.root)
        except (subprocess.SubprocessError, OSError) as exc:
            return False, f"治理根 {ctx.root} 不在 git 工作树内({type(exc).__name__})"
        return True, ""
    if condition == "local_workstations":
        st = ctx.stations()
        if st["error"]:
            raise ValueError(f"执行方式声明读不出: {st['error']}")
        if st["local"]:
            return True, ""
        remote = ", ".join(f"{v['instance']}@{v['workstation'].get('host')}" for v in st["remote"])
        return False, ("仅有跨机工位(" + remote + "), 门机不跨机读写, 在工位所在机 lybra sync") if remote else "本项目无已接入的本机工位"
    raise ValueError(f"未实现的适用条件 {condition!r}")


# ---------------------------------------------------------------------------
# 各步结果适配(调用声明的既有函数, 把结果读成同一形)
#   返回 {state, changes: [str], details: [str]}; 执行另带 {ok, detail, written: [...], landing_line}
# ---------------------------------------------------------------------------

def _rel(root: Path, path: str | Path) -> str:
    p = Path(path)
    return (p.resolve().relative_to(root.resolve()) if p.is_absolute() else p).as_posix()


def _config_defaults(ctx: _Ctx, step: dict[str, Any], fn: Any, *, execute: bool, actor: str | None) -> dict[str, Any]:
    outcome = fn(ctx.root, dry_run=not execute)
    changes = [f"project.json 补 {item['key']} = {json.dumps(item['value'], ensure_ascii=False)}(缺省值, 行为不变)" for item in outcome["filled"]]
    details = [f"已声明: {', '.join(outcome['present'])}"] if outcome["present"] else []
    out = {"state": "pending" if outcome["filled"] else "current", "changes": changes, "details": details}
    if not execute:
        return out
    from tools.aipos_cli.governance_commit import land_written_paths

    written = [_rel(ctx.root, outcome["project_json"])] if outcome["written"] else []
    landing = land_written_paths(ctx.root, written, str(actor), command=str(step["landing"]), subcommand="upgrade",
                                 project=ctx.project) if written else None
    return {**out, "ok": landing is None or bool(landing["landed"]), "written": written, "landing_line": (landing or {}).get("line"),
            "detail": "; ".join(changes) or "无缺键"}


def _charter_recorded(ctx: _Ctx, stations: list[dict[str, Any]]) -> dict[str, Any]:
    """看板升级页 / 总览角标: 章程步取记录真相(F164 拉起前判定 = loop_run_record.latest_charter_checks, F174 同读), 不连门。"""
    got = ctx.facts.charter_checks()
    if "error" in got:
        return {"state": "error", "changes": [], "details": [got["error"]]}
    checks = got["value"]["checks"]
    changes, details = [], [f"运行记录读不出: {u}" for u in got["value"]["unreadable"]]
    unchecked = False
    for view in stations:
        last = checks.get(str(view["instance"]))
        where = f"{view['instance']} @ {view['workstation'].get('dir')}"
        if last is None:
            unchecked = True
            details.append(f"{where}: 运行记录无章程判定; 预演时现核(lybra sync --dry-run)")
        elif str(last.get("outcome")) not in ("fresh", "synced", "not_applicable"):
            changes.append(f"{where}: 最近记录 {last.get('outcome')}({last.get('at') or '?'}); 预演时现核(lybra sync --dry-run)")
        else:
            details.append(f"{where}: 最近记录 {last.get('outcome')}({last.get('at') or '?'})")
    state = "pending" if changes else "unchecked" if unchecked else "current"
    return {"state": state, "changes": changes, "details": details}


def _workstation_charter(ctx: _Ctx, step: dict[str, Any], fn: Any, *, execute: bool, actor: str | None) -> dict[str, Any]:
    st = ctx.stations()
    remote = [f"{v['instance']} @ {v['workstation'].get('host')}:{v['workstation'].get('dir')}: 跨机工位, 不适用(在工位所在机 lybra sync)"
              for v in st["remote"]]
    if ctx.charter_mode == "recorded" and not execute:
        out = _charter_recorded(ctx, st["local"])
        return {**out, "details": out["details"] + remote}
    preview_fn = resolve_function(str(step["preview"]))
    stale: list[tuple[dict[str, Any], dict[str, Any]]] = []
    changes, details = [], list(remote)
    for view in st["local"]:
        ws = str(view["workstation"]["dir"])
        fresh = preview_fn(ws, ctx.root)
        where = f"{view['instance']} @ {ws}"
        if fresh["fresh"]:
            details.append(f"{where}: {fresh['state']}")
        else:
            stale.append((view, fresh))
            changes.append(f"{where}: {fresh['state']}: {fresh['detail']} → {fresh['repair_command']}")
    out = {"state": "pending" if stale else "current", "changes": changes, "details": details}
    if not execute:
        return out
    done: list[str] = []
    for view, fresh in stale:
        synced = fn(str(view["workstation"]["dir"]), ctx.root)
        if not synced["ok"]:
            return {**out, "ok": False, "written": [], "landing_line": None,
                    "detail": f"{view['instance']}: {synced['detail']}(修法: {fresh['repair_command']})"
                              + (f"; 已同步 {done}" if done else "")}
        done.append(f"{view['instance']}: {synced['detail']}")
    return {**out, "ok": True, "written": [], "landing_line": None, "detail": "; ".join(done) or "无过期工位"}


def _migration_labels() -> dict[str, str]:
    from tools.schema_loader import load_schema

    return dict(((load_schema("verbs").get("two_phase_protocol") or {}).get("records_migration_writers") or {}).get("item_status_labels") or {})


def _migration(ctx: _Ctx, step: dict[str, Any], fn: Any, *, execute: bool, actor: str | None, item_key: str) -> dict[str, Any]:
    labels = _migration_labels()
    result = fn(ctx.root, actor=actor if execute else None, confirm=execute)
    items = result["items"]
    pending = [i for i in items if i["status"] == "derived"]
    changes = [f"{i[item_key]}" + (f" → {i['pointer']}" if i.get("pointer") else f"  类型 {i.get('record_type')}" if i.get("record_type") else "")
               for i in pending]
    others: dict[str, int] = {}
    for i in items:
        if i["status"] != "derived":
            others[i["status"]] = others.get(i["status"], 0) + 1
    hint = _fmt(step["preview_command"], governance_root=str(ctx.root)) if step.get("preview_command") else ""
    details = [f"{labels.get(k, k)} {n} 份(列出不改" + (f"; 明细: {hint}" if hint else "") + ")" for k, n in sorted(others.items())]
    out = {"state": "pending" if (pending if not execute else result["written"]) else "current", "changes": changes, "details": details}
    if not execute:
        return out
    landing = result.get("landing")
    return {**out, "ok": landing is None or bool(landing["landed"]), "written": list(result["written"]),
            "landing_line": (landing or {}).get("line"), "detail": f"写出 {len(result['written'])} 份"}


def _record_type(ctx: _Ctx, step: dict[str, Any], fn: Any, *, execute: bool, actor: str | None) -> dict[str, Any]:
    return _migration(ctx, step, fn, execute=execute, actor=actor, item_key="path")


def _decision_log_pointers(ctx: _Ctx, step: dict[str, Any], fn: Any, *, execute: bool, actor: str | None) -> dict[str, Any]:
    return _migration(ctx, step, fn, execute=execute, actor=actor, item_key="record")


_ADAPTERS = {"config_defaults": _config_defaults, "workstation_charter": _workstation_charter, "record_type": _record_type,
             "decision_log_pointers": _decision_log_pointers}


# ---------------------------------------------------------------------------
# 预演 / 执行
# ---------------------------------------------------------------------------

def _preview_step(ctx: _Ctx, step: dict[str, Any]) -> dict[str, Any]:
    base = {"name": step["name"], "label": step["label"], "why": step["why"], "mechanical": bool(step["mechanical"]),
            "landing": step["landing"], "changes": [], "details": []}
    try:
        ok, reason = _applies(ctx, str(step["applies_when"]))
        if not ok:
            return {**base, "state": "not_applicable", "details": [reason]}
        got = _ADAPTERS[str(step["name"])](ctx, step, resolve_function(str(step["preview"])), execute=False, actor=None)
    except _step_errors() as exc:
        return {**base, "state": "error", "details": [_err(exc)]}
    return {**base, **got}


def unread_counts(root: Path, *, running: Any = None) -> dict[str, Any]:
    """本项目各顾问实例未读更新数(F167 advisor_view 同读; 条数口径 = 提示 banner 的受众条目数)。
    返回 {available, reason, instances: [{instance, count, error}], error}。"""
    from tools.aipos_cli.advisor_updates import AdvisorUpdatesError, audience_entries
    from tools.aipos_cli.advisor_updates_cli import advisor_instances, advisor_view

    out: dict[str, Any] = {"available": True, "reason": "", "instances": [], "error": None}
    try:
        advisors = advisor_instances(root)
    except _step_errors() as exc:
        out["error"] = _err(exc)
        return out
    for inst in advisors:
        try:
            view = advisor_view(root, inst, running=running)
        except (AdvisorUpdatesError, *_step_errors()) as exc:
            out["instances"].append({"instance": inst, "count": None, "error": _err(exc), "unread": []})
            continue
        if not view["available"]:
            out.update(available=False, reason=view["reason"])
            return out
        out["instances"].append({"instance": inst, "count": sum(len(audience_entries(r)) for r in view["unread"]), "error": None,
                                 "unread": view["unread"]})
    return out


def plan_upgrade(governance_root: str | Path, *, project: str, home_root: str | Path | None = None, charter_mode: str = "live",
                 running: Any = None, with_guidance: bool = True) -> dict[str, Any]:
    """预演(零写入): 每步 {name, label, why, mechanical, landing, state, changes, details} + 未读更新数 + 引导。
    charter_mode=live = 章程现核(`lybra sync --dry-run` 同一实现); recorded = 读运行记录真相(看板 GET 页 / 角标, 不连门)。"""
    decl = upgrade_declaration()
    ctx = _Ctx(Path(governance_root), project, home_root, charter_mode=charter_mode, running=running)
    steps = [_preview_step(ctx, step) for step in decl["upgrade_steps"]]
    plan: dict[str, Any] = {"project": ctx.project, "governance_root": str(ctx.root), "charter_mode": charter_mode, "steps": steps,
                            "pending": [s["name"] for s in steps if s["state"] == "pending" and s["mechanical"]],
                            "errors": [s["name"] for s in steps if s["state"] == "error"]}
    if with_guidance:
        plan["unread"] = unread_counts(ctx.root, running=running)
        plan["guidance"] = derive_guidance(ctx.root, project=ctx.project, home_root=home_root, running=running, _ctx=ctx,
                                           _unread=plan["unread"])
    return plan


def run_upgrade(governance_root: str | Path, *, project: str, actor: str, home_root: str | Path | None = None, running: Any = None,
                plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """确认: 依次执行待升级的机械步骤(各步执行函数自带两阶段确认与落账); 任一步失败(含核不了 / 落账未完成)即停, 已成功步保留。
    幂等: 各步执行前状态取预演(已是最新 / 不适用 = 跳过)。返回 {project, governance_root, actor, steps, counts, stopped_at, exit_code}。"""
    if not str(actor or "").strip():
        raise UpgradeError("--confirm 须给 --actor(落账提交身份)")
    decl = upgrade_declaration()
    # 执行只按现核预演(记录真相的「待现核」不作执行依据)
    if plan is None or plan.get("charter_mode") != "live":
        plan = plan_upgrade(governance_root, project=project, home_root=home_root, running=running, with_guidance=False)
    ctx = _Ctx(Path(governance_root), project, home_root, charter_mode="live", running=running)
    by_name = {str(s["name"]): s for s in decl["upgrade_steps"]}
    results: list[dict[str, Any]] = []
    stopped: int | None = None
    for index, pre in enumerate(plan["steps"], 1):
        row = {"name": pre["name"], "label": pre["label"], "state": pre["state"], "written": [], "landing_line": None}
        if stopped is not None:
            results.append({**row, "outcome": "not_run", "detail": ""})
            continue
        if pre["state"] in ("current", "not_applicable", "unchecked") or not pre["mechanical"]:
            results.append({**row, "outcome": "skipped", "detail": decl["step_states"][pre["state"]]})
            continue
        if pre["state"] == "error":
            results.append({**row, "outcome": "failed", "detail": "; ".join(pre["details"])})
            stopped = index
            continue
        step = by_name[str(pre["name"])]
        try:
            got = _ADAPTERS[str(pre["name"])](ctx, step, resolve_function(str(step["execute"])), execute=True, actor=actor)
        except _step_errors() as exc:
            got = {"ok": False, "detail": _err(exc), "written": [], "landing_line": None}
        ok = bool(got.get("ok"))
        results.append({**row, "outcome": "done" if ok else "failed", "detail": str(got.get("detail") or ""),
                        "written": list(got.get("written") or []), "landing_line": got.get("landing_line")})
        if not ok:
            stopped = index
    counts = {k: sum(1 for r in results if r["outcome"] == k) for k in OUTCOMES}
    code = decl["exit_codes"]["step_failed" if stopped is not None else "ok"]["code"]
    return {"project": ctx.project, "governance_root": str(ctx.root), "actor": actor, "steps": results, "counts": counts,
            "stopped_at": stopped, "exit_code": int(code)}


# ---------------------------------------------------------------------------
# 「告诉顾问」引导(一个推导函数)
# ---------------------------------------------------------------------------

def _recent_cards(root: Path, decl: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.task_loader import iter_queue_task_paths

    modes = {str(m) for m in decl["guidance"]["card_task_modes"]}
    cards, errors = [], []
    for path in iter_queue_task_paths(root):
        try:
            meta, _body, _warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: {_err(exc)}")
            continue
        if str(meta.get("task_mode") or "") in modes:
            cards.append(meta)
    cards.sort(key=lambda m: str(m.get("created_at") or m.get("draft_created_at") or ""))
    return cards[-int(decl["guidance"]["recent_cards"]):], errors


def _declared(value: Any) -> bool:
    return value not in (None, "", [], {})


def _item(source: str, key: str, why: str, say: str, done_when: str, commands: list[str] | None = None) -> dict[str, Any]:
    return {"id": f"{source}:{key}", "source": source, "key": key, "why": why, "say": say, "done_when": done_when,
            "commands": list(commands or [])}


def derive_guidance(governance_root: str | Path, *, project: str, home_root: str | Path | None = None, running: Any = None,
                    _ctx: _Ctx | None = None, _unread: dict[str, Any] | None = None) -> dict[str, Any]:
    """按钮做不到、需顾问主动用起来的新能力(唯一推导): 逐顾问实例 {advisor, items}; 每项 {id, source, why, say, tell, done_when, commands}。
    来源: F167 本实例未读更新的 advisor_notice actions / F174 接入清单缺项 / 近期卡卡面字段。任一来源读不出 = errors 点名(不吞, 其余照列)。"""
    from tools.aipos_cli.advisor_updates import audience_entries
    from tools.aipos_cli.board_pages import onboarding_rows

    decl = upgrade_declaration()
    g = decl["guidance"]
    ctx = _ctx or _Ctx(Path(governance_root), project, home_root, charter_mode="recorded", running=running)
    fill = {"project": ctx.project, "governance_root": str(ctx.root), "recent": g["recent_cards"]}
    common: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        rows = {r["key"]: r for r in onboarding_rows(ctx.facts)}
    except _step_errors() as exc:
        rows = {}
        errors.append({"source": "onboarding", "error": _err(exc)})
    for key, spec in (g.get("onboarding_items") or {}).items():
        row = rows.get(key)
        if row is None or row["state"] not in ("todo", "error"):
            continue
        commands = [str(c) for c in row["commands"]]
        say = _fmt(spec["say"], **fill, commands="; ".join(c if "`" in c else f"`{c}`" for c in commands) or "-")
        common.append(_item("onboarding", key, _fmt(spec["why"], **fill), say, _fmt(spec["done_when"], **fill), commands))
    try:
        cards, card_errors = _recent_cards(ctx.root, decl)
        errors.extend({"source": "card_field", "error": e} for e in card_errors)
    except _step_errors() as exc:
        cards = []
        errors.append({"source": "card_field", "error": _err(exc)})
    for field, spec in (g.get("card_fields") or {}).items():
        if cards and not any(_declared(meta.get(field)) for meta in cards):
            common.append(_item("card_field", field, _fmt(spec["why"], **fill), _fmt(spec["say"], **fill), _fmt(spec["done_when"], **fill)))
    unread = _unread if _unread is not None else unread_counts(ctx.root, running=running)
    if unread.get("error"):
        errors.append({"source": "notice", "error": str(unread["error"])})
    groups: list[dict[str, Any]] = []
    for entry in unread.get("instances") or []:
        inst = str(entry["instance"])
        if entry.get("error"):
            errors.append({"source": "notice", "error": f"{inst}: {entry['error']}"})
        notices: list[dict[str, Any]] = []
        for record in entry.get("unread") or []:
            for e in audience_entries(record):
                for n, action in enumerate(e.get("actions") or []):
                    notices.append(_item("notice", f"{record['update_id']}:{e['task_id']}:{n}",
                                         _fmt(g["notice"]["why"], **fill, task_id=e["task_id"], summary=e["summary"]),
                                         _fmt(g["notice"]["say"], **fill, action=action),
                                         _fmt(g["notice"]["done_when"], **fill, advisor=inst)))
        groups.append({"advisor": inst, "items": common + notices})
    if not groups:
        try:
            advisors = list(ctx.facts.advisors().get("value") or [])
        except _step_errors():
            advisors = []
        groups = [{"advisor": a, "items": list(common)} for a in advisors] or [{"advisor": None, "items": list(common)}]
    for group in groups:
        who = group["advisor"] or g["unknown_advisor"]
        group["items"] = [{**item, "tell": _fmt(g["tell_line"], advisor=who, say=item["say"])} for item in group["items"]]
    distinct = {item["id"] for group in groups for item in group["items"]}
    return {"project": ctx.project, "governance_root": str(ctx.root), "groups": groups, "count": len(distinct), "errors": errors}


# ---------------------------------------------------------------------------
# 文本渲染(CLI 与看板预演全文同一份)
# ---------------------------------------------------------------------------

def render_plan_lines(plan: dict[str, Any], *, source: str = "-") -> list[str]:
    decl = upgrade_declaration()
    t, states = decl["texts"], decl["step_states"]
    lines = [_fmt(t["head_preview"], project=plan["project"], source=source, governance_root=plan["governance_root"])]
    total = len(plan["steps"])
    for i, step in enumerate(plan["steps"], 1):
        lines.append(_fmt(t["step_line"], index=i, total=total, label=step["label"], state=states[step["state"]], why=step["why"]))
        lines.extend(_fmt(t["change_line"], text=c) for c in step["changes"])
        lines.extend(_fmt(t["detail_line"], text=d) for d in step["details"])
    unread = plan.get("unread")
    if unread is not None:
        lines.append(t["unread_head"])
        if unread.get("error"):
            lines.append(_fmt(t["unread_error"], error=unread["error"]))
        elif not unread["available"]:
            lines.append(_fmt(t["unread_unavailable"], reason=unread["reason"]))
        elif not unread["instances"]:
            lines.append(t["unread_none"])
        for entry in unread.get("instances") or []:
            lines.append(_fmt(t["unread_error"], error=f"{entry['instance']}: {entry['error']}") if entry.get("error")
                         else _fmt(t["unread_line"], instance=entry["instance"], count=entry["count"]))
    guidance = plan.get("guidance") or {"count": 0}
    lines.append(_fmt(t["summary_preview"], pending=len(plan["pending"]), guidance=guidance["count"]))
    return lines


def render_run_lines(run: dict[str, Any], *, source: str = "-") -> list[str]:
    decl = upgrade_declaration()
    t, outcomes = decl["texts"], decl["run_outcomes"]
    lines = [_fmt(t["head_confirm"], project=run["project"], source=source, governance_root=run["governance_root"], actor=run["actor"])]
    total = len(run["steps"])
    for i, step in enumerate(run["steps"], 1):
        lines.append(_fmt(t["run_line"], index=i, total=total, label=step["label"], outcome=outcomes[step["outcome"]],
                          detail=step["detail"] or "-"))
        lines.extend(_fmt(t["detail_line"], text=f"wrote {w}") for w in step["written"])
        if step.get("landing_line"):
            lines.extend(_fmt(t["detail_line"], text=ln) for ln in str(step["landing_line"]).splitlines())
    if run["stopped_at"] is not None:
        failed = run["steps"][run["stopped_at"] - 1]
        lines.append(_fmt(t["stopped"], index=run["stopped_at"], label=failed["label"]))
    elif not run["counts"]["done"]:
        lines.append(t["nothing"])
    lines.append(_fmt(t["summary_confirm"], **run["counts"]))
    return lines


def render_guidance_lines(guidance: dict[str, Any]) -> list[str]:
    decl = upgrade_declaration()
    t = decl["texts"]
    unknown = decl["guidance"]["unknown_advisor"]
    lines = [_fmt(t["guidance_head"], project=guidance["project"], count=guidance["count"])]
    lines.extend(_fmt(t["guidance_error"], source=e["source"], error=e["error"]) for e in guidance["errors"])
    for group in guidance["groups"]:
        lines.append(_fmt(t["guidance_group"], advisor=group["advisor"] or unknown))
        if not group["items"]:
            lines.append(t["guidance_none"])
        for item in group["items"]:
            lines.append(_fmt(t["guidance_item"], source=item["source"], why=item["why"]))
            lines.append(_fmt(t["guidance_say"], tell=item["tell"]))
            lines.append(_fmt(t["guidance_done"], done_when=item["done_when"]))
    return lines


def plan_view(plan: dict[str, Any]) -> dict[str, Any]:
    """JSON 视图(未读条目只出计数, 不出更新说明原文)。"""
    out = dict(plan)
    if plan.get("unread") is not None:
        out["unread"] = {**plan["unread"], "instances": [{k: v for k, v in e.items() if k != "unread"} for e in plan["unread"]["instances"]]}
    return out


# ---------------------------------------------------------------------------
# 看板: 升级页(GET)+ 写例外②(POST, F172 同一安全框架)
# ---------------------------------------------------------------------------

def board_declaration() -> dict[str, Any]:
    return upgrade_declaration()["board"]


def upgrade_page_url(project: str | None = None) -> str:
    from urllib.parse import quote

    path = str(board_declaration()["page_path"])
    return f"{path}?project={quote(str(project))}" if project else path


def pending_badge(root: Path, project: str, home_root: str | Path | None = None) -> dict[str, Any]:
    """总览 / 接入页角标: 待升级机械步数(章程步取记录真相, 不连门)。读不出 = {error}(调用方点名)。"""
    from tools.schema_loader import SchemaLoadError

    try:
        plan = plan_upgrade(root, project=project, home_root=home_root, charter_mode="recorded", with_guidance=False)
    except SchemaLoadError:
        raise
    except _step_errors() as exc:
        return {"error": _err(exc), "pending": None, "url": upgrade_page_url(project)}
    return {"error": None, "pending": len(plan["pending"]), "errors": plan["errors"], "url": upgrade_page_url(project)}


def collect_upgrade_page(home_root: str | Path | None = None, *, project: str | None = None, running: Any = None) -> dict[str, Any]:
    """升级页数据: home 根注册项目(owner_inbox.home_projects 唯一枚举; 给 project = 只该项目)各一份预演(章程取记录真相)+ 引导。"""
    from tools.aipos_cli.owner_inbox import home_projects

    enumerated = home_projects(home_root)
    projects = []
    for p in enumerated["projects"]:
        if project and p["name"] != project:
            continue
        entry: dict[str, Any] = {"project": p["name"], "governance_root": str(p["root"]), "paused": p["status"] == "paused",
                                 "plan": None, "error": None}
        try:
            entry["plan"] = plan_upgrade(p["root"], project=p["name"], home_root=enumerated["home_root"], charter_mode="recorded",
                                         running=running)
        except _step_errors() as exc:
            entry["error"] = _err(exc)
        projects.append(entry)
    return {"home_root": str(enumerated["home_root"]), "projects": projects, "unknown_project": project if project and not projects else None}


def _pre(lines: list[str]) -> str:
    return "<pre class=\"upgrade-lines\">" + html.escape("\n".join(lines)) + "</pre>"


def render_upgrade_html(view: dict[str, Any], *, owner: bool) -> str:
    """升级页(服务端渲染; 全部值经 html.escape)。owner=False = 只读(无按钮)。界面文字读声明 board.texts; 步骤 / 引导内容 = CLI 同一渲染。"""
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    esc = html.escape
    board = board_declaration()
    texts = board["texts"]
    title = _fmt(texts["title"], projects=len(view["projects"]), home_root=view["home_root"])
    data_attrs = "".join(f" data-text-{k.replace('_', '-')}=\"{esc(str(texts[k]))}\""
                         for k in ("preview_heading", "result_heading", "confirm_button", "cancel_button"))
    parts = ["<!doctype html>", "<html lang=\"zh\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             f"<title>{esc(title)}</title><link rel=\"stylesheet\" href=\"/styles.css\">",
             "<style>.upgrade-lines{white-space:pre-wrap;background:#f6f7f9;padding:.6em}.upgrade-preview pre{white-space:pre-wrap;"
             "background:#f6f7f9;padding:.6em}.upgrade-item{margin:.4em 0}</style></head>",
             f"<body class=\"owner-upgrade\" data-owner=\"{str(owner).lower()}\" data-write-path=\"{esc(str(board['write_path']))}\""
             f" data-owner-role=\"{esc(str(board_envelopes_declaration()['owner_role']))}\"{data_attrs}>",
             f"<main><h1 data-upgrade-projects=\"{len(view['projects'])}\">{esc(title)}</h1>"]
    if not owner:
        parts.append(f"<p class=\"upgrade-readonly\">{esc(texts['readonly_hint'])}</p>")
    if view.get("unknown_project"):
        parts.append(f"<p class=\"upgrade-error\">{esc(_fmt(texts['page_error'], error='project ' + str(view['unknown_project'])))}</p>")
    for proj in view["projects"]:
        name = str(proj["project"])
        parts.append(f"<section class=\"upgrade-project\" id=\"{esc(name)}\" data-project=\"{esc(name)}\">")
        if proj["error"]:
            parts.append(f"<h2>{esc(name)}</h2><p class=\"upgrade-error\">{esc(_fmt(texts['page_error'], error=proj['error']))}</p></section>")
            continue
        plan = proj["plan"]
        guidance = plan["guidance"]
        parts.append(f"<h2 data-pending=\"{len(plan['pending'])}\" data-guidance=\"{guidance['count']}\">"
                     f"{esc(_fmt(texts['project_heading'], project=name, pending=len(plan['pending']), guidance=guidance['count']))}</h2>")
        parts.append(f"<h3>{esc(texts['steps_heading'])}</h3>" + _pre(render_plan_lines(plan)))
        if owner:
            parts.append(f"<p><button type=\"button\" class=\"upgrade-preview-button\" data-workspace=\"{esc(name)}\">"
                         f"{esc(texts['preview_button'])}</button></p>")
        parts.append(f"<h3>{esc(texts['guidance_heading'])}</h3>")
        for group in guidance["groups"]:
            items = "".join(f"<li class=\"upgrade-item\" data-item=\"{esc(item['id'])}\" data-source=\"{esc(item['source'])}\">"
                            f"<p>{esc(item['why'])}</p><p class=\"upgrade-tell\">{esc(item['tell'])}</p>"
                            f"<p class=\"upgrade-done\">{esc(item['done_when'])}</p></li>" for item in group["items"])
            who = group["advisor"] or upgrade_declaration()["guidance"]["unknown_advisor"]
            parts.append(f"<div class=\"upgrade-group\" data-advisor=\"{esc(who)}\" data-items=\"{len(group['items'])}\">"
                         f"<h4>{esc(who)}</h4><ul>{items}</ul></div>")
        parts.extend(f"<p class=\"upgrade-error\">{esc(e['source'])}: {esc(e['error'])}</p>" for e in guidance["errors"])
        parts.append("</section>")
    if owner:
        parts.append("<section class=\"upgrade-preview\" hidden><h2></h2><pre></pre><div class=\"upgrade-actions\"></div></section>")
        parts.append(f"<script src=\"{esc(str(board['script_path']))}\"></script>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def render_upgrade_error_html(message: str) -> str:
    text = _fmt(board_declaration()["texts"]["page_error"], error=message)
    return ("<!doctype html>\n<html lang=\"zh\"><head><meta charset=\"utf-8\"><title>upgrade</title></head>"
            f"<body class=\"owner-upgrade\"><main><p class=\"upgrade-error\">{html.escape(text)}</p></main></body></html>")


def handle_upgrade_request(
    body: dict[str, Any],
    *,
    session: dict[str, Any] | None,
    home_root: str | Path | None,
    preview_store: Any,
    audit_log_path: Path | None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = None,
    running: Any = None,
) -> tuple[int, dict[str, Any]]:
    """写例外②(唯一写路由): 安全判定全在 envelope_board.handle_owner_write(F172 同一框架); 本函数只给三个回调。
    参数摘要 = {action, project, governance_root, pending(预演时待升级步骤)}: 预演后待升级步骤变了 = 确认拒(重新预演)。
    actor = 登录会话 Owner 的 captured_by(F172 同一模板, 来源 = 看板), 各步落账提交身份即此。"""
    from tools.aipos_cli.envelope_board import MintRequestError, board_envelopes_declaration, handle_owner_write, session_provenance

    board = board_declaration()
    frame = board_envelopes_declaration()

    def prepare(body: dict[str, Any], project: str, root: Path, now: Any, session: dict[str, Any]) -> tuple[dict, dict, dict]:
        from tools.schema_loader import SchemaLoadError

        try:
            plan = plan_upgrade(root, project=project, home_root=home_root, running=running)
        except SchemaLoadError as exc:
            raise MintRequestError("PREVIEW_BLOCKED", detail=_err(exc)) from exc
        provenance = session_provenance(frame, session)
        params = {"action": str(board["action"]), "project": project, "governance_root": str(root), "pending": list(plan["pending"])}
        return params, {"project": project, "pending": list(plan["pending"])}, {"plan": plan, "provenance": provenance}

    def preview(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        plan = context["plan"]
        lines = render_plan_lines(plan, source=context["provenance"]["capture_surface"])
        lines += [""] + render_guidance_lines(plan["guidance"])
        return {"params": params, "preview_lines": lines, "plan": plan_view(plan)}

    def confirm(params: dict[str, Any], context: dict[str, Any]) -> tuple[dict, dict, Any]:
        prov = context["provenance"]
        actor = prov["captured_by"]
        try:
            run = run_upgrade(params["governance_root"], project=params["project"], actor=actor, home_root=home_root, running=running,
                              plan=context["plan"])
        except (UpgradeError, *_step_errors()) as exc:
            return {"outcome": "upgrade_failed", "error": _err(exc)}, {}, MintRequestError("UPGRADE_FAILED", detail=_err(exc))
        lines = render_run_lines(run, source=prov["capture_surface"])
        fields = {"outcome": "upgraded" if run["stopped_at"] is None else "upgrade_failed", "actor": actor,
                  "capture_surface": prov["capture_surface"], "steps": [[s["name"], s["outcome"]] for s in run["steps"]]}
        if run["stopped_at"] is not None:
            return fields, {}, MintRequestError("UPGRADE_FAILED", detail=" | ".join(lines[1:])[:1500])
        return fields, {"ok": True, "stage": "confirm", "params": params, "actor": actor, "captured_by": actor,
                        "capture_surface": prov["capture_surface"], "result_lines": lines, "run": run,
                        "exit_code": run["exit_code"]}, None

    return handle_owner_write(body, decl=board, action=str(board["action"]), session=session, home_root=home_root,
                              preview_store=preview_store, audit_log_path=audit_log_path, prepare=prepare, preview=preview,
                              confirm=confirm, origin=origin, host=host, content_type=content_type)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
