"""AIPOS-F174 — 看板三张只读页(总览 / 路线图与验收 / 项目接入)的数据: 只调既有唯一推导, 看板只渲染(web/board/readonly_pages.py)。

一句话: Owner 在看板上看到每个项目在途多少卡、有几件等他决定、信封还够不够、路线图走到哪、偏了没有、每条验收标准凭什么算过、
项目接入还差哪步; 要他出手的地方只给「告诉 <顾问实例>: …」与顾问命令, 不给按钮(Owner 10-16 裁定: 看板只读, 交互经顾问)。

防碎片化(卡面硬约束), 每个数据面只读一个既有唯一实现, 本模块不另判:
- 项目枚举 = owner_inbox.home_projects(收件箱 / 看板项目列表 F171 / 信封页 F172 同调); 暂停 = 其 status(workspace_config.project_status)。
- 在途 / 待办 / 结案卡数与子项目分组 = machine_zone.card_stats(AIPOS-F181 唯一统计口径: 冻结卡只计历史存量; 声明 lane_view.card_stats);
  结案 = lybra_roadmap.status.satisfied_queue_states(与路线图「挂接卡已结案」同一口径)。
- 待 Owner 项 = owner_inbox.collect_inbox; 信封 = autonomy_policy.envelope_overview(续签命令 = 其 F166 renew_command);
  路线图进度 / 偏差 / 逐条标准证据 = roadmap_drift.roadmap_status(证据经 roadmap_drift.card_evidence 随行带出, 含 parse_runtime_evidence_section 摘录)。
- 接入: 顾问 = advisor_updates_cli.advisor_instances; 执行 / 审计 = workspace_config.project_execution + enrollment.instance_enrollment;
  章程 = loop_run_record.latest_charter_checks(F164 拉起前判定的记录真相); pi 模型 = workspace_config.pi_model_for_role。
- 页面路径、清单项与顺序(只追加)、命令模板读 verbs.schema board_readonly_pages(pages_declaration 唯一读取口, fail-closed);
  人话文案在看板 i18n 单通道(web/board/app.py _I18N_TEMPLATES), 本模块只给文案键与填值, 不出界面文字。
- 任何项目同一组命令: 不写死项目 ID / 路径 / 卡号前缀 / 实例名。只读: 不写任何文件、不连门、不读凭据原文。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

DECL_KEY = "board_readonly_pages"
PAGES = ("overview", "project", "roadmap", "onboarding", "home", "engineering", "history")  # AIPOS-F246: + 首页 / 工程页; AIPOS-F247: + 历史与审计(只追加)
#: 清单项判定(键 = 声明 onboarding_items 的值; 声明里出现未实现的项 = SchemaLoadError, 只追加)
ONBOARDING_CHECKS = ("governance_root", "advisor", "executor", "auditor", "envelope", "charter", "pi_model", "roadmap", "first_closed")
_COMMAND_KEYS = ("governance_root", "advisor", "execution_undeclared", "instance_not_enrolled", "envelope_mint", "charter_check",
                 "pi_model", "first_closed")
#: 章程判定 outcome(F164 LoopRunRecorder.charter_check 写入值): 新鲜 / 已自动 sync = 过; 跨机 = 不适用; 其余 = 未过
_CHARTER_OK = ("fresh", "synced")
_CHARTER_NA = ("not_applicable",)
STATES = ("ok", "todo", "na", "error")


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def pages_declaration() -> dict[str, Any]:
    """verbs.schema board_readonly_pages(fail-closed): pages 恰为 PAGES 且路径以 / 起首; onboarding_items 无重复且都已实现;
    onboarding_commands 键齐; locale 缺省值在 supported 内; evidence_excerpt_chars 为正整数。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = load_schema("verbs", code_repo_schema_root()).get(DECL_KEY)
    keys = ("pages", "nav_links", "stylesheet", "query", "locale", "evidence_excerpt_chars", "onboarding_items", "onboarding_commands")
    if not isinstance(decl, dict) or any(k not in decl for k in keys):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY} 未声明齐 {list(keys)}")
    pages = decl["pages"]
    if not isinstance(pages, dict) or set(pages) != set(PAGES) or not all(isinstance(v, str) and v.startswith("/") for v in pages.values()):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.pages 须恰为 {list(PAGES)} 且为 / 起首路径")
    items = decl["onboarding_items"]
    if (not isinstance(items, list) or not items or len(set(items)) != len(items)
            or any(item not in ONBOARDING_CHECKS for item in items)):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.onboarding_items 须为不重复的已实现清单项 {list(ONBOARDING_CHECKS)}, 得到 {items!r}")
    commands = decl["onboarding_commands"]
    if not isinstance(commands, dict) or not all(isinstance(commands.get(k), str) and commands[k] for k in _COMMAND_KEYS):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.onboarding_commands 须有 {list(_COMMAND_KEYS)}")
    locale = decl["locale"]
    if (not isinstance(locale, dict) or not isinstance(locale.get("supported"), list) or locale.get("default") not in locale["supported"]
            or not all(isinstance(locale.get(k), str) and locale[k] for k in ("param", "storage_key"))):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.locale(param / default ∈ supported / storage_key)未声明齐")
    chars = decl["evidence_excerpt_chars"]
    if not isinstance(chars, int) or isinstance(chars, bool) or chars < 1:
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.evidence_excerpt_chars 须为正整数")
    if not all(isinstance(decl["query"].get(k), str) and decl["query"][k] for k in ("project", "milestone", "subproject", "show", "view")):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.query(project / milestone / subproject / show / view)未声明齐")
    # AIPOS-F181: root redirect target page / show-all toggle value / legacy project page template
    if (decl.get("root_redirect") not in PAGES or not isinstance(decl.get("show_all_value"), str) or not decl["show_all_value"]
            or "{index}" not in str(decl.get("legacy_project_page") or "") or not str(decl["legacy_project_page"]).startswith("/")
            or not str((decl.get("nav_links") or {}).get("inbox") or "").startswith("/")):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}: root_redirect ∈ pages / show_all_value / legacy_project_page(/…{{index}}) / "
                              "nav_links.inbox 未声明齐(AIPOS-F181)")
    # AIPOS-F247: 历史与审计页参数名 / 报告视图种类 / 近 N 天筛选
    hist = decl.get("history")
    if (not isinstance(hist, dict) or not all(isinstance((hist.get("query") or {}).get(k), str) and hist["query"][k]
                                              for k in ("keywords", "advisor", "since", "rejected_once", "legacy", "task", "doc"))
            or list(hist.get("docs") or []) != ["return", "audit"] or not isinstance(hist.get("recent_days"), int)
            or not str(hist.get("flag_value") or "") or not isinstance(hist.get("open_first"), int)
            or not isinstance(hist.get("page_budget_bytes"), int)):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.history(query 七键 / docs = [return, audit] / recent_days / flag_value / "
                              "open_first / page_budget_bytes)未声明齐(AIPOS-F247)")
    route = decl.get("artifact_route")
    if (not isinstance(route, dict) or not str(route.get("path") or "").startswith("/")
            or not all(isinstance((route.get("params") or {}).get(k), str) and route["params"][k] for k in ("project", "task", "file"))):
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.artifact_route(path 以 / 起首 / params{{project, task, file}})未声明齐(AIPOS-F175)")
    return decl


def _command(decl: dict[str, Any], key: str, **values: Any) -> str:
    from tools.schema_loader import SchemaLoadError

    try:
        return str(decl["onboarding_commands"][key]).format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.schema.json {DECL_KEY}.onboarding_commands.{key} 模板占位无值: {exc}") from exc


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 项目枚举 + 每项目事实(各面只算一次; 读不出 = 该面 error 点名, 不猜)
# ---------------------------------------------------------------------------

def home_entries(home_root: str | Path | None = None) -> dict[str, Any]:
    """home 根注册项目(owner_inbox.home_projects 唯一枚举; 序号 = 看板 ?workspace=<序号> 同一序)。"""
    from tools.aipos_cli.owner_inbox import home_projects

    from tools.aipos_cli.workspace_config import project_status_visibility

    enumerated = home_projects(home_root)
    board_hidden = project_status_visibility()["board_hidden"]  # AIPOS-F181: test / probe projects hidden by default
    entries = [{"name": p["name"], "root": Path(p["root"]), "index": i, "status": p["status"], "paused": p["status"] == "paused",
                "hidden": p["status"] in board_hidden, "status_error": p["status_error"]} for i, p in enumerate(enumerated["projects"])]
    return {"home_root": Path(enumerated["home_root"]), "home_source": enumerated["home_source"], "projects": entries}


def visible_entries(home: dict[str, Any], show_all: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """AIPOS-F181: projects shown on a board page (status in config.schema status.board_hidden = hidden unless the page toggle
    query.show = show_all_value) + names of the hidden ones (the page offers the toggle with their count)."""
    hidden = [e["name"] for e in home["projects"] if e["hidden"]]
    return ([e for e in home["projects"] if show_all or not e["hidden"]], [] if show_all else hidden)


def show_all_requested(decl: dict[str, Any], params: dict[str, list[str]] | None) -> bool:
    """The page toggle (query.show == show_all_value)."""
    return (params or {}).get(decl["query"]["show"], [""])[0] == decl["show_all_value"]


class ProjectFacts:
    """一个项目的各数据面(惰性, 每面只算一次)。每面返回 {"value": …} 或 {"error": "<异常类>: <原文>"}。"""

    def __init__(self, entry: dict[str, Any], home_root: Path) -> None:
        self.entry = entry
        self.root = Path(entry["root"])
        self.name = str(entry["name"])
        self.home_root = home_root
        self._cache: dict[str, dict[str, Any]] = {}

    def _get(self, key: str, compute: Any, errors: tuple[type[BaseException], ...]) -> dict[str, Any]:
        if key not in self._cache:
            from tools.schema_loader import SchemaLoadError

            try:
                self._cache[key] = {"value": compute()}
            except SchemaLoadError:
                raise  # 声明坏 = 整页拒(不是某项目的真相问题)
            except errors as exc:
                self._cache[key] = {"error": _err(exc)}
        return self._cache[key]

    def roadmap(self) -> dict[str, Any]:
        from tools.aipos_cli.roadmap import RoadmapError
        from tools.aipos_cli.roadmap_drift import json_view, roadmap_status
        from tools.aipos_cli.workspace_config import RoadmapDriftThresholdError

        return self._get("roadmap", lambda: json_view(roadmap_status(self.root, project=self.name)),
                         (RoadmapError, RoadmapDriftThresholdError, OSError, ValueError, LookupError))

    def roadmap_progress(self) -> dict[str, Any]:
        """AIPOS-F249 件③: the roadmap view without the milestones' last_activity (roadmap_drift.roadmap_progress; the home / project
        pages do not show it, and a running loop's run record moves it every few seconds)."""
        from tools.aipos_cli.roadmap import RoadmapError
        from tools.aipos_cli.roadmap_drift import json_view, roadmap_progress
        from tools.aipos_cli.workspace_config import RoadmapDriftThresholdError

        return self._get("roadmap_progress", lambda: json_view(roadmap_progress(self.root, project=self.name)),
                         (RoadmapError, RoadmapDriftThresholdError, OSError, ValueError, LookupError))

    def envelopes(self) -> dict[str, Any]:
        from tools.aipos_cli.autonomy_policy import envelope_overview

        return self._get("envelopes", lambda: envelope_overview(self.root)["envelopes"], (OSError, ValueError, LookupError))

    def authorizations(self) -> dict[str, Any]:
        """AIPOS-F246 件⑤: 本项目在用的项目授权(autonomy_policy.authorization_overview 用量真相 state=active)的授权编号。"""
        from tools.aipos_cli.autonomy_policy import authorization_overview

        return self._get("authorizations", lambda: [a["authorization_id"] for a in authorization_overview(self.root) if a.get("state") == "active"],
                         (OSError, ValueError, LookupError))

    def advisors(self) -> dict[str, Any]:
        from tools.aipos_cli.advisor_updates_cli import advisor_instances
        from tools.aipos_cli.custom_roles import RoleRegistryReadError

        # 门注册表读不出(RoleRegistryReadError, 非 ValueError 子类)= 该项目这一面点名, 不当「无顾问」
        return self._get("advisors", lambda: advisor_instances(self.root), (OSError, ValueError, LookupError, RoleRegistryReadError))

    def execution(self) -> dict[str, Any]:
        from tools.aipos_cli.workspace_config import project_execution

        return self._get("execution", lambda: project_execution(self.root), (OSError, ValueError))

    def enrollment(self, instance: str) -> dict[str, Any]:
        from tools.aipos_cli.enrollment import instance_enrollment

        return self._get(f"enrollment:{instance}", lambda: instance_enrollment(self.root, instance), (OSError, ValueError))

    def charter_checks(self) -> dict[str, Any]:
        from tools.aipos_cli.loop_run_record import LoopRunRecordError, latest_charter_checks

        return self._get("charter", lambda: latest_charter_checks(self.root), (OSError, ValueError, LoopRunRecordError))

    def pi_model(self, role: str) -> dict[str, Any]:
        from tools.aipos_cli.workspace_config import pi_model_for_role

        return self._get(f"pi_model:{role}", lambda: pi_model_for_role(self.root, role), (OSError, ValueError))

    def queue_counts(self) -> dict[str, Any]:
        """AIPOS-F181: card statistics = machine_zone.card_stats (single derivation: frozen legacy cards only counted as history,
        groups in-flight / to-do / closed, subproject grouping)."""
        from tools.aipos_cli.machine_zone import card_stats

        return self._get("queue", lambda: card_stats(self.root), (OSError, ValueError, LookupError))

    def tell_target(self, preferred: str | None = None) -> list[str]:
        """「告诉谁」: 优先给定实例(信号 advisor), 否则本治理根已接入顾问实例; 都没有 = 空(渲染用「你的顾问」)。"""
        if preferred:
            return [preferred]
        got = self.advisors()
        return list(got.get("value") or [])


# ---------------------------------------------------------------------------
# 接入清单(每项一行: state ∈ STATES, details = [(文案键, 填值)], ask = (文案键, 填值) | None, commands, links)
# ---------------------------------------------------------------------------

def _row(key: str, state: str, details: list[tuple[str, dict[str, Any]]], *, ask: tuple[str, dict[str, Any]] | None = None,
         commands: list[str] | None = None, links: list[tuple[str, str]] | None = None, tell: list[str] | None = None) -> dict[str, Any]:
    return {"key": key, "state": state, "details": details, "ask": ask if state in ("todo", "error") else None,
            "commands": (commands or []) if state in ("todo", "error") else [], "links": links or [], "tell": tell or []}


def _fill(f: ProjectFacts, **extra: Any) -> dict[str, Any]:
    return {"project": f.name, "home_root": str(f.home_root), "governance_root": str(f.root), **extra}


def _check_governance_root(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    if f.entry.get("status_error"):
        return _row("governance_root", "error", [("rp_ob_detail_governance_root", {"root": str(f.root)}),
                                                 ("rp_error", {"error": f.entry["status_error"]})],
                    ask=("rp_ob_ask_governance_root", {"project": f.name}),
                    commands=[_command(decl, "governance_root", **_fill(f))], tell=f.tell_target())
    return _row("governance_root", "ok", [("rp_ob_detail_governance_root", {"root": str(f.root)})])


def _check_advisor(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    got = f.advisors()
    if "error" in got:
        return _row("advisor", "error", [("rp_error", {"error": got["error"]})], ask=("rp_ob_ask_advisor", {"project": f.name}),
                    commands=[_command(decl, "advisor", **_fill(f))])
    if got["value"]:
        return _row("advisor", "ok", [("rp_ob_detail_advisor_ok", {"instances": ", ".join(got["value"])})])
    return _row("advisor", "todo", [("rp_ob_detail_advisor_none", {})], ask=("rp_ob_ask_advisor", {"project": f.name}),
                commands=[_command(decl, "advisor", **_fill(f))])


def _instance_rows(f: ProjectFacts, decl: dict[str, Any], key: str, instances: list[str], *, needs_workstation: bool) -> dict[str, Any]:
    details: list[tuple[str, dict[str, Any]]] = []
    commands: list[str] = []
    state = "ok"
    for name in instances:
        got = f.enrollment(name)
        if "error" in got:
            state = "error"
            details.append(("rp_ob_detail_instance_error", {"instance": name, "error": got["error"]}))
            commands.append(_command(decl, "instance_not_enrolled", **_fill(f, instance=name)))
            continue
        view = got["value"]
        if not view["enrolled"]:
            state = "todo" if state == "ok" else state
            details.append(("rp_ob_detail_instance_missing", {"instance": name, "reason": view["reason"]}))
            commands.append(_command(decl, "instance_not_enrolled", **_fill(f, instance=name)))
        elif needs_workstation and not view["workstation"].get("found"):
            state = "todo" if state == "ok" else state
            details.append(("rp_ob_detail_instance_no_ws", {"instance": name, "reason": view["workstation"].get("reason") or ""}))
            commands.append(_command(decl, "instance_not_enrolled", **_fill(f, instance=name)))
        else:
            details.append(("rp_ob_detail_instance_ok", {"instance": name, "via": "+".join(view["via"])}))
    return _row(key, state, details, ask=(f"rp_ob_ask_{key}", {"project": f.name, "instance": ", ".join(instances)}),
                commands=commands, tell=f.tell_target())


def _execution_or_row(f: ProjectFacts, decl: dict[str, Any], key: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    got = f.execution()
    if "error" in got:
        return None, _row(key, "error", [("rp_error", {"error": got["error"]})], ask=(f"rp_ob_ask_{key}", {"project": f.name, "instance": "-"}),
                          commands=[_command(decl, "execution_undeclared", **_fill(f))], tell=f.tell_target())
    if got["value"] is None:
        return None, _row(key, "todo", [("rp_ob_detail_execution_undeclared", {})],
                          ask=(f"rp_ob_ask_{key}", {"project": f.name, "instance": "-"}),
                          commands=[_command(decl, "execution_undeclared", **_fill(f))], tell=f.tell_target())
    return got["value"], None


def _check_executor(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    execution, row = _execution_or_row(f, decl, "executor")
    if row is not None:
        return row
    names = [n for n in (execution.get("subagent_executor"), execution.get("pi_executor")) if n]
    return _instance_rows(f, decl, "executor", names, needs_workstation=False)


def _check_auditor(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    execution, row = _execution_or_row(f, decl, "auditor")
    if row is not None:
        return row
    return _instance_rows(f, decl, "auditor", [str(execution["auditor"])], needs_workstation=True)


def _check_envelope(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.envelope_board import board_envelope_url

    link = [("rp_nav_envelopes", board_envelope_url(f.name))]
    got = f.envelopes()
    mint = _command(decl, "envelope_mint", **_fill(f))
    if "error" in got:
        return _row("envelope", "error", [("rp_error", {"error": got["error"]})], ask=("rp_ob_ask_envelope", {"project": f.name}),
                    commands=[mint], links=link, tell=f.tell_target())
    current = [r for r in got["value"] if r.get("current")]
    fine = [r for r in current if r.get("status") == "active"]
    low = [r for r in current if r.get("status") != "active"]
    if fine and not low:
        return _row("envelope", "ok", [("rp_ob_detail_envelope_ok", {"policies": _envelope_text(fine)})], links=link)
    details: list[tuple[str, dict[str, Any]]] = []
    if fine:
        details.append(("rp_ob_detail_envelope_ok", {"policies": _envelope_text(fine)}))
    if low:
        details.append(("rp_ob_detail_envelope_low", {"policies": _envelope_text(low)}))
        commands = [str(r["renew_command"]) for r in low if r.get("renew_command")] or [mint]
    else:
        details.append(("rp_ob_detail_envelope_none", {}))
        commands = [mint]
    return _row("envelope", "todo", details, ask=("rp_ob_ask_envelope", {"project": f.name}), commands=commands, links=link,
                tell=f.tell_target())


def _envelope_text(rows: list[dict[str, Any]]) -> str:
    return "; ".join(f"{r['policy_id']}({r.get('agent_or_role') or '?'} {r.get('used')}/{r.get('max_tasks')}"
                     f" · {r.get('status')} · {r.get('expires_at') or '?'})" for r in rows)


def workstation_instances(f: ProjectFacts) -> list[dict[str, Any]]:
    """执行方式声明里有工位位置的实例(审计者 / pi 执行者; 子 agent 执行者无工位)。读不出的面由各自清单项点名, 这里跳过。"""
    execution = f.execution().get("value") or {}
    out = []
    for name in dict.fromkeys(n for n in (execution.get("auditor"), execution.get("pi_executor")) if n):
        view = f.enrollment(str(name)).get("value")
        if view and view["enrolled"] and view["workstation"].get("found"):
            out.append(view)
    return out


def _check_charter(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI

    stations = workstation_instances(f)
    if not stations:
        return _row("charter", "na", [("rp_ob_detail_charter_na", {})])
    got = f.charter_checks()
    if "error" in got:
        return _row("charter", "error", [("rp_error", {"error": got["error"]})], ask=("rp_ob_ask_charter", {"project": f.name, "instance": "-"}),
                    tell=f.tell_target())
    checks = got["value"]["checks"]
    details: list[tuple[str, dict[str, Any]]] = [("rp_error", {"error": text}) for text in got["value"]["unreadable"]]
    commands: list[str] = []
    state = "error" if details else "ok"
    pending: list[str] = []
    for view in stations:
        name = str(view["instance"])
        if view["workstation"].get("transport") == "remote":
            details.append(("rp_ob_detail_charter_remote", {"instance": name}))
            continue
        last = checks.get(name)
        harness = str(view.get("harness") or EXECUTION_MODE_PI)  # 工位 land 事件无 harness= 段 = pi 工位(审计者 / pi 执行者恒为 pi)
        if last is None:
            state = "todo" if state == "ok" else state
            pending.append(name)
            details.append(("rp_ob_detail_charter_unchecked", {"instance": name}))
            commands.append(_command(decl, "charter_check", **_fill(f, instance=name, harness=harness)))
        elif str(last.get("outcome")) in _CHARTER_OK or str(last.get("outcome")) in _CHARTER_NA:
            details.append(("rp_ob_detail_charter_ok", {"instance": name, "outcome": last.get("outcome"), "at": last.get("at") or "?"}))
        else:
            state = "todo" if state == "ok" else state
            pending.append(name)
            details.append(("rp_ob_detail_charter_bad", {"instance": name, "outcome": last.get("outcome"), "at": last.get("at") or "?",
                                                         "detail": last.get("detail") or ""}))
            commands.append(str(last.get("repair_command") or _command(decl, "charter_check", **_fill(f, instance=name, harness=harness))))
    return _row("charter", state, details, ask=("rp_ob_ask_charter", {"project": f.name, "instance": ", ".join(pending) or "-"}),
                commands=commands, tell=f.tell_target())


def _check_pi_model(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    execution, row = _execution_or_row(f, decl, "pi_model")
    if row is not None:
        return {**row, "state": "na", "ask": None, "commands": []} if row["state"] == "todo" else row
    roles = ["auditor"] + (["executor"] if execution.get("pi_executor") else [])
    details: list[tuple[str, dict[str, Any]]] = []
    commands: list[str] = []
    state = "ok"
    for role in roles:
        got = f.pi_model(role)
        if "error" in got:
            state = "error"
            details.append(("rp_error", {"error": got["error"]}))
            continue
        model = got["value"]
        if model["model"]:
            details.append(("rp_ob_detail_pi_model_ok", {"role": role, "model": model["model"], "source": model["source"]}))
        else:
            state = "todo" if state == "ok" else state
            details.append(("rp_ob_detail_pi_model_unset", {"role": role, "source": model["source"]}))
            commands.append(_command(decl, "pi_model", **_fill(f, role=role)))
    return _row("pi_model", state, details, ask=("rp_ob_ask_pi_model", {"project": f.name}), commands=commands, tell=f.tell_target())


def _check_roadmap(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.roadmap import roadmap_texts

    link = [("rp_nav_roadmap", roadmap_url(decl, f.name))]
    got = f.roadmap()
    if "error" in got:
        return _row("roadmap", "error", [("rp_error", {"error": got["error"]})], ask=("rp_ob_ask_roadmap", {"project": f.name}),
                    links=link, tell=f.tell_target())
    view = got["value"]
    if view["exists"]:
        return _row("roadmap", "ok", [("rp_ob_detail_roadmap_ok", {"rel": view["rel"], "milestones": len(view["milestones"]),
                                                                   "met": view["totals"]["met"], "total": view["totals"]["total"]})],
                    links=link)
    hint = roadmap_texts()["no_roadmap"].format(project=f.name, path=view["rel"])  # AIPOS-F181: governance-root relative path
    return _row("roadmap", "todo", [("rp_ob_detail_roadmap_none", {"rel": view["rel"]})], ask=("rp_ob_ask_roadmap", {"project": f.name}),
                commands=[hint], links=link, tell=f.tell_target())


def _check_first_closed(f: ProjectFacts, decl: dict[str, Any]) -> dict[str, Any]:
    got = f.queue_counts()
    if "error" in got:
        return _row("first_closed", "error", [("rp_error", {"error": got["error"]})], ask=("rp_ob_ask_first_closed", {"project": f.name}),
                    commands=[_command(decl, "first_closed", **_fill(f))], tell=f.tell_target())
    totals = got["value"]["totals"]
    if totals["closed"]:
        return _row("first_closed", "ok", [("rp_ob_detail_first_closed_ok", {"count": totals["closed"]})])
    return _row("first_closed", "todo", [("rp_ob_detail_first_closed_none", {"inflight": totals["inflight"] + totals["todo"]})],
                ask=("rp_ob_ask_first_closed", {"project": f.name}), commands=[_command(decl, "first_closed", **_fill(f))],
                tell=f.tell_target())


_CHECKS = {"governance_root": _check_governance_root, "advisor": _check_advisor, "executor": _check_executor, "auditor": _check_auditor,
           "envelope": _check_envelope, "charter": _check_charter, "pi_model": _check_pi_model, "roadmap": _check_roadmap,
           "first_closed": _check_first_closed}


def onboarding_rows(f: ProjectFacts, decl: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    d = decl or pages_declaration()
    return [_CHECKS[key](f, d) for key in d["onboarding_items"]]


def onboarding_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """完成度: 不适用项不计入分母。"""
    counted = [r for r in rows if r["state"] != "na"]
    return {"done": sum(1 for r in counted if r["state"] == "ok"), "total": len(counted)}


# ---------------------------------------------------------------------------
# 页面数据
# ---------------------------------------------------------------------------

def page_url(decl: dict[str, Any], page: str, **query: Any) -> str:
    from urllib.parse import urlencode

    pairs = [(decl["query"].get(k, k), str(v)) for k, v in query.items() if v not in (None, "")]
    return str(decl["pages"][page]) + (f"?{urlencode(pairs)}" if pairs else "")


def roadmap_url(decl: dict[str, Any], project: str, milestone: str | None = None) -> str:
    return page_url(decl, "roadmap", project=project, milestone=milestone)


def artifact_url(decl: dict[str, Any], project: str, task_id: str, name: str) -> str:
    """AIPOS-F175: 卡产物图片的看板只读取图地址(声明 artifact_route)。"""
    from urllib.parse import urlencode

    route = decl["artifact_route"]
    params = route["params"]
    return f"{route['path']}?" + urlencode({params["project"]: project, params["task"]: task_id, params["file"]: name})


def artifact_for_route(home_root: str | Path | None, query: dict[str, list[str]]) -> dict[str, Any] | None:
    """AIPOS-F175: 取图路由的唯一判定入口——项目 = home 根注册枚举(home_entries), 文件 = roadmap.artifact_file(卡面声明 + 落点内 + 图片后缀)。
    任一不合 = None(路由回 404)。返回 {path, content_type}。只读。"""
    import mimetypes

    from tools.aipos_cli.roadmap import artifact_file

    params = pages_declaration()["artifact_route"]["params"]
    values = {k: str((query.get(v) or [""])[0]).strip() for k, v in params.items()}
    if not all(values.values()):
        return None
    entry = next((e for e in home_entries(home_root)["projects"] if e["name"] == values["project"]), None)
    if entry is None:
        return None
    path = artifact_file(entry["root"], values["task"], values["file"])
    if path is None:
        return None
    return {"path": path, "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"}


def pending_badge(root: Path, project: str, home_root: Path) -> dict[str, Any]:
    """AIPOS-F176: per-project "upgradable (N)" badge = project_upgrade.pending_badge (single derivation; charter from recorded truth).
    AIPOS-F245 item 1: the git commands its plan runs are declared to the truth-fingerprinted result cache under the badge's own
    declared validity (verbs.schema board.badge_cache: project_upgrade._badge_fingerprint = project.json, the governance repo HEAD /
    logs/HEAD, the workstation directories and the declaration files, all stat-ed and so recorded; its ttl is re-checked at least every
    minute because a cached page is derived again each minute) = the same badge the page showed before the cache."""
    from tools.aipos_cli.project_upgrade import pending_badge as upgrade_badge
    from tools.aipos_cli.task_loader import truth_external_read

    with truth_external_read():
        return upgrade_badge(root, project, home_root)


def collect_overview(home_root: str | Path | None = None, *, show_all: bool = False) -> dict[str, Any]:
    """总览页: 每项目一张卡片(在途 / 待办 / 已结案 + 历史存量 + 子项目分组 / 待 Owner 项 / 信封 / 路线图摘要 / 接入完成度), 暂停项目标 paused
    (渲染折叠); AIPOS-F181: status ∈ board_hidden(测试项目)缺省不列, show_all = 页面开关。"""
    from tools.aipos_cli.task_loader import read_request_scope, truth_cached

    def compute() -> dict[str, Any]:
        with read_request_scope():  # AIPOS-F177 item 3: one request-level read scope (per-project derivations shared)
            return _collect_overview(home_root, show_all=show_all)

    # AIPOS-F245 item 1: result cached on the truth fingerprint (board server only; any truth change = recomputed next request)
    return truth_cached(("board_pages.collect_overview", str(home_root), bool(show_all)), compute, page=True)


def _collect_overview(home_root: str | Path | None, *, show_all: bool) -> dict[str, Any]:
    from tools.aipos_cli.envelope_board import board_envelope_url
    from tools.aipos_cli.owner_inbox import collect_inbox

    decl = pages_declaration()
    home = home_entries(home_root)
    shown, hidden = visible_entries(home, show_all)
    inbox_counts: dict[str, int] = {}
    inbox_error = None
    try:
        inbox = collect_inbox(home["home_root"], include_paused=show_all)
        for item in inbox["items"]:
            inbox_counts[item["project"]] = inbox_counts.get(item["project"], 0) + 1
    except (OSError, ValueError, LookupError) as exc:
        inbox, inbox_error = None, _err(exc)
    listed = set(inbox["projects"]) if inbox is not None else set()
    cards = []
    for entry in shown:
        f = ProjectFacts(entry, home["home_root"])
        envelopes = f.envelopes()
        current = [r for r in envelopes.get("value") or [] if r.get("current")]
        auths = f.authorizations()  # AIPOS-F246 件⑤: 在用的项目授权也是「在用」(其全覆盖的旧信封已由 envelope_overview 判接替)
        roadmap = f.roadmap()
        view = roadmap.get("value") or {}
        rows = onboarding_rows(f, decl)
        cards.append({
            "project": f.name, "index": entry["index"], "paused": entry["paused"], "hidden": entry["hidden"], "status": entry["status"],
            "status_error": entry["status_error"], "governance_root": str(f.root), "queue": f.queue_counts(),
            "inbox": {"count": inbox_counts.get(f.name, 0) if inbox is not None and f.name in listed else None, "error": inbox_error},
            "envelopes": {"error": envelopes.get("error") or auths.get("error"),
                          "active": list(auths.get("value") or []) + [r["policy_id"] for r in current if r.get("status") == "active"],
                          "low": [r["policy_id"] for r in current if r.get("status") != "active"], "url": board_envelope_url(f.name)},
            "roadmap": {"error": roadmap.get("error"), "exists": bool(view.get("exists")), "milestones": len(view.get("milestones") or []),
                        "met": (view.get("totals") or {}).get("met", 0), "total": (view.get("totals") or {}).get("total", 0),
                        "signals": len(view.get("signals") or []),
                        "severe": sum(1 for s in view.get("signals") or [] if s.get("severity") == "inbox"),
                        "url": roadmap_url(decl, f.name)},
            "onboarding": {**onboarding_summary(rows), "url": page_url(decl, "onboarding") + f"#{f.name}"},
            "upgrade": pending_badge(f.root, f.name, home["home_root"]),  # AIPOS-F176: project_upgrade.plan_upgrade (charter = recorded truth)
            "url": project_url(decl, f.name),
        })
    return {"decl": decl, "home_root": str(home["home_root"]), "projects": cards, "hidden": hidden, "show_all": show_all,
            "inbox_url": str(decl["nav_links"]["inbox"]), "paused": [c["project"] for c in cards if c["paused"]]}


def project_url(decl: dict[str, Any], project: str, subproject: str | None = None, view: str | None = None) -> str:
    """AIPOS-F181: project page address (query.project / query.subproject). AIPOS-F246: view = query.view (the all-cards list view
    keeps its view on project tabs / subproject filters)."""
    return page_url(decl, "project", project=project, subproject=subproject, view=view)


def collect_onboarding(home_root: str | Path | None = None, *, show_all: bool = False) -> dict[str, Any]:
    """接入页: 每项目一张清单(项与顺序 = 声明 onboarding_items)。"""
    from tools.aipos_cli.task_loader import read_request_scope, truth_cached

    def compute() -> dict[str, Any]:
        with read_request_scope():  # AIPOS-F177 item 3: one request-level read scope (per-project derivations shared)
            return _collect_onboarding(home_root, show_all=show_all)

    return truth_cached(("board_pages.collect_onboarding", str(home_root), bool(show_all)), compute, page=True)  # AIPOS-F245 item 1


def _collect_onboarding(home_root: str | Path | None, *, show_all: bool) -> dict[str, Any]:
    decl = pages_declaration()
    home = home_entries(home_root)
    shown, hidden = visible_entries(home, show_all)
    projects = []
    for entry in shown:
        f = ProjectFacts(entry, home["home_root"])
        rows = onboarding_rows(f, decl)
        projects.append({"project": f.name, "index": entry["index"], "paused": entry["paused"], "hidden": entry["hidden"],
                         "governance_root": str(f.root), "rows": rows, "summary": onboarding_summary(rows), "advisors": f.tell_target(),
                         "upgrade": pending_badge(f.root, f.name, home["home_root"])})
    return {"decl": decl, "home_root": str(home["home_root"]), "projects": projects, "hidden": hidden, "show_all": show_all}


def _pick_entry(home: dict[str, Any], entries: list[dict[str, Any]], project: str | None) -> dict[str, Any] | None:
    """Selected project: the named one (any registered project, a hidden test project too: it was asked for by name; None if not
    registered), else the first shown project not paused, else the first shown."""
    if project:
        return next((e for e in home["projects"] if e["name"] == project), None)
    return next((e for e in entries if not e["paused"]), entries[0] if entries else None)


def _with_chosen(entries: list[dict[str, Any]], chosen: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project tabs: the shown projects, plus the chosen one when it is hidden (named explicitly)."""
    return entries + ([chosen] if chosen is not None and chosen not in entries else [])


def _subproject_choice(stats: dict[str, Any] | None, wanted: str | None) -> tuple[str | None, str | None]:
    """(selected subproject key | None = all, unknown key named back)."""
    if not wanted:
        return None, None
    keys = [s["key"] for s in (stats or {}).get("subprojects") or []]
    return (wanted, None) if wanted in keys else (None, wanted)


def collect_project(home_root: str | Path | None = None, *, project: str | None = None, subproject: str | None = None,
                    show_all: bool = False, view: str | None = None) -> dict[str, Any]:
    """AIPOS-F181 item 2: project page = card_stats of the selected project grouped by subproject (in-flight + to-do listed,
    closed folded by the renderer). Unknown project / subproject = named back (not guessed)."""
    from tools.aipos_cli.task_loader import read_request_scope

    with read_request_scope():
        decl = pages_declaration()
        home = home_entries(home_root)
        shown, hidden = visible_entries(home, show_all)
        chosen = _pick_entry(home, shown, project)
        out: dict[str, Any] = {"decl": decl, "home_root": str(home["home_root"]), "hidden": hidden, "show_all": show_all,
                               "projects": [{"project": e["name"], "paused": e["paused"], "hidden": e["hidden"],
                                             "url": project_url(decl, e["name"], view=view)} for e in _with_chosen(shown, chosen)],
                               "unknown_project": project if project and chosen is None else None, "selected": None, "view": view}
        if chosen is None:
            return out
        f = ProjectFacts(chosen, home["home_root"])
        got = f.queue_counts()
        stats = got.get("value")
        key, unknown = _subproject_choice(stats, subproject)
        out["selected"] = {"project": f.name, "index": chosen["index"], "paused": chosen["paused"], "hidden": chosen["hidden"],
                           "error": got.get("error"), "stats": stats, "subproject": key, "unknown_subproject": unknown,
                           "legacy_url": str(decl["legacy_project_page"]).format(index=chosen["index"]),
                           "roadmap_url": roadmap_url(decl, f.name), "envelope_url": _envelope_url(f.name)}
        return out


def _envelope_url(project: str) -> str:
    from tools.aipos_cli.envelope_board import board_envelope_url

    return board_envelope_url(project)


def collect_roadmap(home_root: str | Path | None = None, *, project: str | None = None, milestone: str | None = None,
                    subproject: str | None = None, show_all: bool = False) -> dict[str, Any]:
    """路线图与验收页: 选中项目(缺省 = 首个未暂停项目)的 roadmap_status 视图 + 选中里程碑(缺省 = 首个 active, 再缺省首个)。
    项目名不在 home 根注册项目 = selected None + unknown_project(渲染点名, 不猜)。AIPOS-F181: subproject 给出 = 只列该子项目挂接卡所在
    (或其 lane 下无卡)的里程碑(card_stats 同一子项目推导)。"""
    from tools.aipos_cli.task_loader import read_request_scope, truth_cached

    def compute() -> dict[str, Any]:
        with read_request_scope():  # AIPOS-F177 item 3: one request-level read scope (per-project derivations shared)
            return _collect_roadmap(home_root, project=project, milestone=milestone, subproject=subproject, show_all=show_all)

    return truth_cached(("board_pages.collect_roadmap", str(home_root), project, milestone, subproject, bool(show_all)),
                        compute, page=True)  # AIPOS-F245 item 1


def _milestone_filter(view: dict[str, Any], stats: dict[str, Any] | None, key: str | None) -> list[str] | None:
    """Milestone ids shown for a subproject (None = all): a linked card belongs to it, or no linked card and its lane is one of the
    subproject's lanes."""
    if key is None or not stats:
        return None
    sub = next((s for s in stats["subprojects"] if s["key"] == key), None)
    mine = {r["task_id"] for r in stats["rows"] if r["subproject"] == key}
    lanes = set((sub or {}).get("lanes") or []) | {key}
    return [ms["id"] for ms in view["milestones"]
            if any(str(c).upper() in mine for c in ms.get("cards") or []) or (not ms.get("cards") and str(ms.get("lane")) in lanes)]


def _collect_roadmap(home_root: str | Path | None, *, project: str | None, milestone: str | None, subproject: str | None,
                     show_all: bool) -> dict[str, Any]:
    decl = pages_declaration()
    home = home_entries(home_root)
    entries, hidden = visible_entries(home, show_all)
    names = [e["name"] for e in entries]
    chosen = _pick_entry(home, entries, project)
    out: dict[str, Any] = {"decl": decl, "home_root": str(home["home_root"]), "hidden": hidden, "show_all": show_all,
                           "projects": [{"project": e["name"], "paused": e["paused"], "hidden": e["hidden"], "url": roadmap_url(decl, e["name"])}
                                        for e in _with_chosen(entries, chosen)],
                           "selected": None, "unknown_project": project if project and chosen is None else None, "names": names}
    if chosen is None:
        return out
    f = ProjectFacts(chosen, home["home_root"])
    roadmap = f.roadmap()
    view = roadmap.get("value")
    stats = f.queue_counts().get("value")
    key, unknown_sub = _subproject_choice(stats, subproject)
    selected: dict[str, Any] = {"project": f.name, "index": chosen["index"], "paused": chosen["paused"], "governance_root": str(f.root),
                                "error": roadmap.get("error"), "view": view, "advisors": f.tell_target(), "milestone": None,
                                "unknown_milestone": None, "subprojects": [{k: s[k] for k in ("key", "label", "advisor")}
                                                                           for s in (stats or {}).get("subprojects") or []],
                                "subproject": key, "unknown_subproject": unknown_sub, "milestone_filter": None}
    if view and view.get("exists"):
        allowed = _milestone_filter(view, stats, key)
        selected["milestone_filter"] = allowed
        milestones = [m for m in view["milestones"] if allowed is None or m["id"] in allowed]
        pick = None
        if milestone:
            pick = next((m for m in milestones if m["id"] == milestone), None)
            if pick is None:
                selected["unknown_milestone"] = milestone
        if pick is None:
            pick = next((m for m in milestones if m["status"] == "active"), milestones[0] if milestones else None)
        selected["milestone"] = pick["id"] if pick else None
        selected["signal_targets"] = [f.tell_target(s.get("advisor")) for s in view["signals"]]
        # AIPOS-F175: criteria awaiting the Owner spot check -> "tell <advisor>" targets + the advisor's verify command
        # (command template = owner inbox owner_check.verify_command, same as the inbox item)
        from tools.aipos_cli.owner_inbox import inbox_declaration

        template = str(inbox_declaration()["categories"]["owner_check"]["verify_command"])
        selected["owner_checks"] = {
            str(row["id"]): {"tell": f.tell_target(row.get("verify_target_by")),
                             "command": template.format(governance_root=str(f.root), criterion=row["id"])}
            for ms in view["milestones"] for row in ms["criteria"] if row.get("state") == "owner_pending"}
    else:
        from tools.aipos_cli.roadmap import roadmap_texts

        selected["no_roadmap_hint"] = roadmap_texts()["no_roadmap"].format(project=f.name, path=(view or {}).get("rel") or "?")
    out["selected"] = selected
    return out



# ---------------------------------------------------------------------------
# AIPOS-F247: 历史与审计页数据(检索 = history_search.search, CLI 同一实现; 本模块只把查询参数换成检索参数)
# ---------------------------------------------------------------------------

def history_url(decl: dict[str, Any], **values: Any) -> str:
    """历史与审计页地址: 键 = project / show(board query)或 history.query 的键; 空值不带。"""
    from urllib.parse import urlencode

    names = {**{k: v for k, v in decl["history"]["query"].items()}, "project": decl["query"]["project"], "show": decl["query"]["show"]}
    pairs = [(names[k], str(v)) for k, v in values.items() if v not in (None, "", False)]
    return str(decl["pages"]["history"]) + (f"?{urlencode(pairs)}" if pairs else "")


def collect_history(home_root: str | Path | None, params: dict[str, list[str]] | None) -> dict[str, Any]:
    """页面查询参数 → history_search.search 同一检索(参数不合 = usage_error 点名, 不猜); query.doc ∈ docs 且给了 task = 报告视图:
    只认该卡由 history_search.card_entry 推导给出的那份交回 / 审计报告(md_source.render_markdown 安全渲染)。"""
    from tools.aipos_cli.history_search import HistoryUsageError, declaration, search

    decl = pages_declaration()
    hq = decl["history"]["query"]
    params = params or {}

    def arg(name: str) -> str:
        return str((params.get(name) or [""])[0] or "").strip()

    flag = str(decl["history"]["flag_value"])
    filters = {"keywords": arg(hq["keywords"]), "project": arg(decl["query"]["project"]) or None, "advisor": arg(hq["advisor"]) or None,
               "since": arg(hq["since"]) or None, "rejected_once": arg(hq["rejected_once"]) == flag,
               "legacy": arg(hq["legacy"]) or None, "show": arg(decl["query"]["show"]) == decl["show_all_value"]}
    out: dict[str, Any] = {"decl": decl, "filters": filters, "result": None, "usage_error": None, "doc": None,
                           "verb": declaration(), "projects": [e["name"] for e in visible_entries(home_entries(home_root), filters["show"])[0]]}
    try:
        out["result"] = search(home_root, filters["keywords"], project=filters["project"], advisor=filters["advisor"], since=filters["since"],
                               rejected_once=filters["rejected_once"], legacy=filters["legacy"], all_projects=filters["show"])
    except HistoryUsageError as exc:
        out["usage_error"] = str(exc)
        return out
    task, doc = arg(hq["task"]), arg(hq["doc"])
    if task and doc in decl["history"]["docs"] and filters["project"]:
        from tools.aipos_cli.history_search import report_doc

        entry = next((e for e in home_entries(home_root)["projects"] if e["name"] == filters["project"]), None)
        out["doc"] = report_doc(filters["project"], Path(entry["root"]) if entry else None, task, doc)
    return out


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
