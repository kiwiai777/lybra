"""AIPOS-F246 — 看板首页(每位顾问一页纸)与项目一页纸视图的数据; 渲染在 web/board/readonly_pages.py。

一句话(Owner 10-28「太工程化、眼花缭乱」): 首页只放「需要你」最多三件 + 每个项目下每位在任顾问一张卡(顾问写的现在 / 做成两行,
产品算的进度 / 卡住); 项目页按顾问范围只放在推 / 卡住 / 里程碑 / 最近交付物 / 等你验收, 工程细节收进二级。

防碎片化(卡面硬约束), 每个数据面只读既有唯一实现, 本模块只做「按顾问归拢」与「卡住」判定:
- 项目枚举 / 隐藏 / 暂停 = board_pages.home_entries / visible_entries; 每项目各面 = board_pages.ProjectFacts(顾问 / 卡统计 / 路线图)。
- 顾问 = advisor_updates_cli.advisor_instances; 卡归属 = machine_zone.card_stats 子项目(以顾问为键的组 + advisor 判为该顾问的
  lane 组; 项目只有一位顾问时无归属 lane 组也归他); 顾问的里程碑 = board_pages._milestone_filter(路线图页子项目筛选同一归属)。
- 简报 = advisor_headline.latest_headlines / is_stale; 需要你 = owner_inbox.collect_inbox(同一事项, 只换人话标题)。
- 卡住 = 本模块 card_last_activity / stall_rows(唯一实现; 声明 verbs.schema board_readonly_pages.home.stall): 只读既有记录文件的修改时刻。
- 声明读取口 = home_declaration(fail-closed); 界面文字在看板 i18n 单通道(web/board/app.py _I18N_TEMPLATES), 本模块只给数据。
- 结果经 task_loader.truth_cached(AIPOS-F245 真相指纹缓存: 看板服务进程内, 任一真相变化下一次请求即重算)。只读: 不写、不连门。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.board_pages import (
    ProjectFacts,
    _err,
    _milestone_filter,
    home_entries,
    page_url,
    pages_declaration,
    visible_entries,
)

_HOME_KEYS = ("nav", "engineering_links", "cards_view_value", "asks_limit", "recent_days", "title_chars", "progress_milestone_statuses",
              "deliverables_limit", "thumbs_limit", "page_budget_bytes", "ask_titles", "stall", "forbidden_words")
NAV_ITEMS = ("home", "project", "history", "engineering")
ENGINEERING_LINKS = ("inbox", "overview", "roadmap", "envelopes", "onboarding", "workstation", "upgrade", "cards")


def home_declaration(decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """verbs.schema board_readonly_pages.home(fail-closed): 键齐; nav / engineering_links 只含已实现项; 计数正整数; stall 阈值正数、
    活动记录类都是按卡分目录的既有记录类(record_writer.record_dir 接受 key)。"""
    from tools.aipos_cli.record_writer import record_dir
    from tools.schema_loader import SchemaLoadError

    home = (decl or pages_declaration()).get("home")
    if not isinstance(home, dict) or any(k not in home for k in _HOME_KEYS):
        raise SchemaLoadError(f"verbs.schema.json board_readonly_pages.home 未声明齐 {list(_HOME_KEYS)}")
    if not set(home["nav"]) <= set(NAV_ITEMS) or not set(home["engineering_links"]) <= set(ENGINEERING_LINKS):
        raise SchemaLoadError(f"board_readonly_pages.home.nav ⊆ {list(NAV_ITEMS)}, engineering_links ⊆ {list(ENGINEERING_LINKS)}")
    for key in ("asks_limit", "recent_days", "title_chars", "deliverables_limit", "thumbs_limit", "page_budget_bytes"):
        if not isinstance(home[key], int) or isinstance(home[key], bool) or home[key] < 1:
            raise SchemaLoadError(f"board_readonly_pages.home.{key} 须为正整数")
    stall = home["stall"]
    hours = stall.get("threshold_hours") if isinstance(stall, dict) else None
    if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
        raise SchemaLoadError("board_readonly_pages.home.stall.threshold_hours 须为正数")
    kinds = stall.get("activity_record_kinds")
    if not isinstance(kinds, list) or not kinds:
        raise SchemaLoadError("board_readonly_pages.home.stall.activity_record_kinds 须为非空列表")
    for kind in kinds:
        try:
            record_dir(Path("/"), str(kind), "PROBE")
        except (ValueError, KeyError, LookupError) as exc:
            raise SchemaLoadError(f"board_readonly_pages.home.stall.activity_record_kinds: {kind} 不是按卡分目录的记录类: {exc}") from exc
    if not isinstance(stall.get("cleanup_commands"), list) or not isinstance(home["forbidden_words"], list):
        raise SchemaLoadError("board_readonly_pages.home: stall.cleanup_commands / forbidden_words 须为列表")
    return home


# ---------------------------------------------------------------------------
# 卡住判定(唯一实现)
# ---------------------------------------------------------------------------

def _newest_mtime(directory: Path) -> float | None:
    """目录下文件(不递归)的最新修改时刻; 目录不在 = None。逐个 stat(看板真相缓存记下每个文件身份: 原地改写也算变化)。"""
    if not directory.is_dir():
        return None
    newest: float | None = None
    for name in os.listdir(directory):
        path = directory / name
        try:
            st = os.stat(path)
        except FileNotFoundError:  # 列目录与 stat 之间被移走 = 不计(下次请求按新目录重算)
            continue
        if not os.path.isdir(path):
            newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
    return newest


def card_last_activity(governance_root: Path, task_id: str, home: dict[str, Any] | None = None) -> dict[str, Any]:
    """一张卡最近一次活动(声明 home.stall): 本卡各活动记录目录 + loop 运行记录目录 + 卡文件本身, 取最新修改时刻。
    返回 {at: epoch 秒 | None, source: 最新那处(记录类名 / loop_runs / card)}。卡找不到 / 多义 = ValueError(点名)。只读。"""
    from tools.aipos_cli.loop_run_record import loop_runs_dir
    from tools.aipos_cli.record_writer import record_dir
    from tools.aipos_cli.task_loader import find_task_card

    root = Path(governance_root)
    spec = (home or home_declaration())["stall"]
    best: tuple[float, str] | None = None
    for kind in spec["activity_record_kinds"]:
        got = _newest_mtime(record_dir(root, str(kind), task_id))
        if got is not None and (best is None or got > best[0]):
            best = (got, str(kind))
    got = _newest_mtime(loop_runs_dir(root, task_id))
    if got is not None and (best is None or got > best[0]):
        best = (got, "loop_runs")
    path, _state = find_task_card(root, task_id)
    if path is not None:
        got = os.stat(path).st_mtime
        if best is None or got > best[0]:
            best = (got, "card")
    return {"at": best[0] if best else None, "source": best[1] if best else None}


def stall_rows(governance_root: Path, rows: list[dict[str, Any]], *, now: datetime, home: dict[str, Any] | None = None
               ) -> list[dict[str, Any]]:
    """在途卡行 → 每行加 {last_activity(ISO | None), idle_hours, stalled}; 停滞 = 距今 ≥ 声明 threshold_hours 或一条活动也读不到。
    只读展示, 不改卡状态。"""
    spec = (home or home_declaration())["stall"]
    threshold = float(spec["threshold_hours"])
    out = []
    for row in rows:
        act = card_last_activity(governance_root, str(row.get("card_id") or row["task_id"]), home)
        if act["at"] is None:
            idle = None
            stalled = True
        else:
            idle = max(0.0, (now.timestamp() - float(act["at"])) / 3600.0)
            stalled = idle >= threshold
        at = datetime.fromtimestamp(act["at"], tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if act["at"] else None
        out.append({**row, "last_activity": at, "activity_source": act["source"], "idle_hours": idle, "stalled": stalled})
    return out


def closed_at(governance_root: Path, task_id: str) -> float | None:
    """已结案卡的结案时刻 = 本卡结案记录(record_writer.record_dir closures)最新修改时刻; 无 = None。"""
    from tools.aipos_cli.record_writer import record_dir

    return _newest_mtime(record_dir(Path(governance_root), "closures", task_id))


# ---------------------------------------------------------------------------
# 人话标题
# ---------------------------------------------------------------------------

def human_title(task_id: str | None, title: str | None, limit: int) -> str:
    """卡标题去掉开头的卡号(及其后的分隔符), 截到 limit 字(超出加 …); 无标题 = ""。"""
    text = str(title or "").strip()
    tid = str(task_id or "").strip()
    if tid and text.upper().startswith(tid.upper()):
        text = text[len(tid):]
    text = re.sub(r"^[\s:：·\-—|]+", "", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _clip(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# 按顾问归拢
# ---------------------------------------------------------------------------

def _display_name(project: str, instance: str, advisors: list[str], headline: dict[str, Any] | None) -> str:
    """显示名: 简报 title; 无简报 = 唯一顾问用项目名, 否则实例名前缀段(machine_zone.subproject_label; 前缀撞了再带项目段)。"""
    from tools.aipos_cli.machine_zone import subproject_label
    from tools.aipos_cli.naming_profile import parse_instance_name

    from tools.aipos_cli.advisor_updates import updates_declaration

    if headline and str(headline.get("title") or "").strip():
        return str(headline["title"]).strip()
    if len(advisors) == 1:
        return project
    audience = str(updates_declaration()["banner"]["audience"])  # 顾问角色类名(声明), 前缀段去掉它只留区分部分
    label = subproject_label(instance)
    if label.startswith(audience + "-") and len(label) > len(audience) + 1:
        return label[len(audience) + 1:]
    seg = str((parse_instance_name(instance) or {}).get("project") or "")
    return seg or label


def _advisor_keys(instance: str, advisors: list[str], stats: dict[str, Any]) -> set[str]:
    keys = {instance}
    for sub in stats.get("subprojects") or []:
        if sub.get("advisor") == instance or (len(advisors) == 1 and not sub.get("advisor")):
            keys.add(str(sub["key"]))
    return keys


def _advisor_milestones(view: dict[str, Any] | None, stats: dict[str, Any], keys: set[str]) -> list[dict[str, Any]]:
    if not view or not view.get("exists"):
        return []
    allowed: set[str] = set()
    for key in keys:
        allowed |= set(_milestone_filter(view, stats, key) or [])
    return [ms for ms in view["milestones"] if ms["id"] in allowed]


def _progress(milestones: list[dict[str, Any]], closed_recent: int, home: dict[str, Any]) -> dict[str, Any]:
    counted = [ms for ms in milestones if ms.get("status") in home["progress_milestone_statuses"]]
    total = sum(int((ms.get("progress") or {}).get("total") or 0) for ms in counted)
    if counted and total:
        met = sum(int((ms.get("progress") or {}).get("met") or 0) for ms in counted)
        return {"kind": "criteria", "met": met, "total": total}
    return {"kind": "closed_recent", "count": closed_recent, "days": home["recent_days"]}


def _project_advisors(f: ProjectFacts, home: dict[str, Any], now: datetime) -> dict[str, Any]:
    """一个项目全部在任顾问的一页纸数据(每位一项) + 本项目读不出的面(errors)。"""
    from tools.aipos_cli.advisor_headline import headline_age_hours, is_stale, latest_headlines

    errors: list[str] = []
    advisors_got = f.advisors()
    stats_got = f.queue_counts()
    roadmap_got = f.roadmap()
    for got in (advisors_got, stats_got, roadmap_got):
        if got.get("error"):
            errors.append(str(got["error"]))
    try:
        headlines = latest_headlines(f.root)
    except (ValueError, OSError) as exc:
        headlines = {}
        errors.append(_err(exc))
    advisors = list(advisors_got.get("value") or [])
    stats = stats_got.get("value") or {"rows": [], "subprojects": []}
    titles = {str(r["task_id"]): r.get("title") for r in stats["rows"]}
    for r in stats["rows"]:  # an audit card reads as the card it reviews (its own title is "Audit <card id>")
        if r.get("audit_of") and titles.get(str(r["audit_of"])):
            r["title"] = titles[str(r["audit_of"])]
    view = roadmap_got.get("value")
    groups = []
    for inst in advisors:
        keys = _advisor_keys(inst, advisors, stats)
        rows = [r for r in stats["rows"] if r["subproject"] in keys]
        inflight = stall_rows(f.root, [r for r in rows if r["group"] == "inflight"], now=now, home=home)
        todo = [r for r in rows if r["group"] == "todo"]
        closed = [r for r in rows if r["group"] == "closed"]
        milestones = _advisor_milestones(view, stats, keys)
        cutoff = now.timestamp() - 86400 * int(home["recent_days"])
        recent: list[dict[str, Any]] = []
        for r in closed:
            at = closed_at(f.root, str(r.get("card_id") or r["task_id"]))
            if at is not None and at >= cutoff:
                recent.append({**r, "closed_at": at})
        recent.sort(key=lambda r: -r["closed_at"])
        stalled = sorted((r for r in inflight if r["stalled"]), key=lambda r: -(r["idle_hours"] if r["idle_hours"] is not None else 1e9))
        headline = headlines.get(inst)
        age = headline_age_hours(headline, now)
        groups.append({
            "instance": inst, "keys": sorted(keys), "display": _display_name(f.name, inst, advisors, headline),
            "headline": ({**headline, "age_hours": age, "stale": is_stale(headline, now)} if headline else None),
            "paused": bool(headline and headline.get("status") == "paused"),
            "pushing": [r for r in inflight if not r["stalled"]], "stalled": stalled, "todo": todo,
            "milestones": milestones, "recent_closed": recent,
            "progress": _progress(milestones, len(recent), home),
        })
    groups.sort(key=lambda g: (g["paused"], -(len(g["pushing"]) + len(g["stalled"]) + len(g["todo"])), g["display"]))
    return {"advisors": groups, "errors": errors, "view": view, "stats": stats}


def _ask_title(item: dict[str, Any], by_task: dict[str, dict[str, Any]], milestone_titles: dict[str, str], displays: dict[str, str],
               project_display: str, home: dict[str, Any]) -> dict[str, Any]:
    """收件箱事项 → 人话标题(文案键 hm_ask_<类别>, 未声明类别 = hm_ask_other)+ 填值; 不含标准 ID / 卡号。"""
    category = str(item.get("category") or "")
    key = f"hm_ask_{category}" if category in home["ask_titles"]["categories"] else "hm_ask_other"
    facts = item.get("facts") or {}
    row = by_task.get(str(item.get("task_id") or "").upper())
    card = human_title(item.get("task_id"), (row or {}).get("title") or item.get("title"), int(home["title_chars"]))
    milestone = milestone_titles.get(str(facts.get("milestone") or ""), "")
    advisor = displays.get(str(item.get("source") or ""), "") or (displays.get(str((row or {}).get("owner") or ""), ""))
    detail = ""
    if category == "owner_check":
        crit = str(facts.get("statement") or "")
        detail = _clip(crit, 2 * int(home["title_chars"])) if crit else ""
    return {"key": key, "values": {"card": card or project_display, "milestone": milestone or card or project_display,
                                   "advisor": advisor or project_display, "project": project_display}, "detail": detail}


def collect_home(home_root: str | Path | None = None, *, show_all: bool = False) -> dict[str, Any]:
    """首页数据(看板服务进程内经真相指纹缓存; 任一真相变化 / 换分钟 = 下一次请求重算)。"""
    from tools.aipos_cli.task_loader import read_request_scope, truth_cached

    def compute() -> dict[str, Any]:
        with read_request_scope():
            return _collect_home(home_root, show_all=show_all)

    return truth_cached(("board_home.collect_home", str(home_root), bool(show_all)), compute)


def _owner_check_statements(view: dict[str, Any] | None) -> dict[str, str]:
    return {str(c["id"]): str(c.get("statement") or "") for ms in (view or {}).get("milestones") or [] for c in ms.get("criteria") or []}


def _collect_home(home_root: str | Path | None, *, show_all: bool) -> dict[str, Any]:
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.owner_inbox import collect_inbox

    decl = pages_declaration()
    home = home_declaration(decl)
    entries = home_entries(home_root)
    shown, hidden = visible_entries(entries, show_all)
    now = utc_now()
    cards: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    by_project: dict[str, dict[str, Any]] = {}
    for entry in shown:
        f = ProjectFacts(entry, entries["home_root"])
        got = _project_advisors(f, home, now)
        by_project[f.name] = got
        for err in got["errors"]:
            errors.append({"project": f.name, "error": err})
        for g in got["advisors"]:
            stalled = g["stalled"]
            oldest = stalled[0] if stalled else None
            cards.append({
                "project": f.name, "project_paused": entry["paused"], "instance": g["instance"], "display": g["display"],
                "paused": g["paused"] or entry["paused"], "headline": g["headline"], "progress": g["progress"],
                "pushing": len(g["pushing"]) + len(g["todo"]), "stalled": len(stalled),
                "stalled_oldest": ({"title": human_title(oldest["task_id"], oldest.get("title"), int(home["title_chars"])),
                                    "days": (int(oldest["idle_hours"] // 24) if oldest["idle_hours"] is not None else None)}
                                   if oldest else None),
                "url": page_url(decl, "project", project=f.name, subproject=g["instance"]),
            })
    asks: list[dict[str, Any]] = []
    inbox_error = None
    total = 0
    try:
        inbox = collect_inbox(entries["home_root"], include_paused=show_all)
        items = [i for i in inbox["items"] if i["project"] in by_project]
        total = len(items)
        items.sort(key=lambda i: (int(i.get("rank") or 9), str(i.get("since") or "9999")))
        for item in items[: int(home["asks_limit"])]:
            got = by_project[item["project"]]
            by_task = {str(r["task_id"]).upper(): r for r in got["stats"].get("rows") or []}
            ms_titles = {str(ms["id"]): str(ms.get("title") or "") for ms in (got["view"] or {}).get("milestones") or []}
            displays = {g["instance"]: g["display"] for g in got["advisors"]}
            row = by_task.get(str(item.get("task_id") or "").upper())
            owner = next((g for g in got["advisors"] if row is not None and row["subproject"] in g["keys"]), None)
            project_display = owner["display"] if owner else item["project"]
            facts = dict(item.get("facts") or {})
            if item.get("category") == "owner_check" and facts.get("criterion"):
                facts["statement"] = _owner_check_statements(got["view"]).get(str(facts["criterion"]), "")
            title = _ask_title({**item, "facts": facts}, by_task, ms_titles, displays, project_display, home)
            since = _parse_iso(item.get("since"))
            asks.append({**title, "project": item["project"], "rank": item.get("rank"), "rank_label": item.get("rank_label"),
                         "waited_days": (int((now - since).total_seconds() // 86400) if since else None),
                         "url": f"{decl['nav_links']['inbox']}#item-{item['item_id']}"})
    except (OSError, ValueError, LookupError) as exc:
        inbox_error = _err(exc)
    return {"decl": decl, "home": home, "home_root": str(entries["home_root"]), "cards": cards, "hidden": hidden, "show_all": show_all,
            "asks": asks, "asks_total": total, "inbox_error": inbox_error, "inbox_url": str(decl["nav_links"]["inbox"]), "errors": errors,
            "now": now.replace(microsecond=0).isoformat().replace("+00:00", "Z")}


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 项目一页纸(按顾问范围)
# ---------------------------------------------------------------------------

def collect_project_brief(home_root: str | Path | None = None, *, project: str | None = None, advisor: str | None = None,
                          show_all: bool = False) -> dict[str, Any]:
    """项目页缺省视图: 选中项目(缺省 = 首个未暂停)× 选中顾问(query.subproject; 缺省 = 该项目首位顾问)。经真相指纹缓存。"""
    from tools.aipos_cli.task_loader import read_request_scope, truth_cached

    def compute() -> dict[str, Any]:
        with read_request_scope():
            return _collect_project_brief(home_root, project=project, advisor=advisor, show_all=show_all)

    return truth_cached(("board_home.collect_project_brief", str(home_root), project, advisor, bool(show_all)), compute)


def _deliverable(f: ProjectFacts, row: dict[str, Any], home: dict[str, Any], decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.board_pages import artifact_url
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.roadmap import card_artifacts
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    thumbs: list[dict[str, str]] = []
    problem = None
    try:
        card_id = str(row.get("card_id") or row["task_id"])
        path, _state = find_task_card(f.root, card_id)
        if path is not None:
            meta, _body = require_frontmatter(path)
            for entry in card_artifacts(f.root, meta)["entries"]:
                for item in entry["files"]:
                    if item["media"] == "image":
                        thumbs.append({"url": artifact_url(decl, f.name, card_id, item["name"]), "name": item["name"]})
    except (AmbiguousTaskCard, FrontmatterReadError, OSError, ValueError) as exc:
        problem = _err(exc)
    return {"task_id": row["task_id"], "title": human_title(row["task_id"], row.get("title"), int(home["title_chars"])),
            "closed_at": datetime.fromtimestamp(row["closed_at"], tz=timezone.utc).strftime("%m-%d"), "thumbs": thumbs, "problem": problem}


def _collect_project_brief(home_root: str | Path | None, *, project: str | None, advisor: str | None, show_all: bool) -> dict[str, Any]:
    from tools.aipos_cli.board_pages import _envelope_url, _pick_entry, _with_chosen, roadmap_url
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.owner_inbox import collect_inbox

    decl = pages_declaration()
    home = home_declaration(decl)
    entries = home_entries(home_root)
    shown, hidden = visible_entries(entries, show_all)
    chosen = _pick_entry(entries, shown, project)
    out: dict[str, Any] = {"decl": decl, "home": home, "home_root": str(entries["home_root"]), "hidden": hidden, "show_all": show_all,
                           "projects": [{"project": e["name"], "paused": e["paused"], "hidden": e["hidden"],
                                         "url": page_url(decl, "project", project=e["name"])} for e in _with_chosen(shown, chosen)],
                           "unknown_project": project if project and chosen is None else None, "selected": None}
    if chosen is None:
        return out
    now = utc_now()
    f = ProjectFacts(chosen, entries["home_root"])
    got = _project_advisors(f, home, now)
    groups = got["advisors"]
    pick = next((g for g in groups if g["instance"] == advisor), None) if advisor else None
    unknown_advisor = advisor if advisor and pick is None else None
    pick = pick or (groups[0] if groups else None)
    sel: dict[str, Any] = {
        "project": f.name, "paused": chosen["paused"], "hidden": chosen["hidden"], "errors": got["errors"], "unknown_advisor": unknown_advisor,
        "advisors": [{"instance": g["instance"], "display": g["display"], "paused": g["paused"],
                      "url": page_url(decl, "project", project=f.name, subproject=g["instance"])} for g in groups],
        "advisor": None, "governance_root": str(f.root),
        "links": {"cards": page_url(decl, "project", project=f.name, view=home["cards_view_value"]),
                  "roadmap": roadmap_url(decl, f.name), "envelopes": _envelope_url(f.name),
                  "onboarding": page_url(decl, "onboarding") + f"#{f.name}"},
        "cleanup_commands": [str(c).format(governance_root=str(f.root)) for c in home["stall"]["cleanup_commands"]],
    }
    out["selected"] = sel
    if pick is None:
        return out
    limit = int(home["title_chars"])
    today = str((got["view"] or {}).get("today") or now.date().isoformat())
    milestones = []
    for ms in pick["milestones"]:
        prog = ms.get("progress") or {}
        target = str(ms.get("target_date") or "") or None
        milestones.append({"id": ms["id"], "title": ms.get("title") or ms["id"], "status": ms.get("status"),
                           "met": int(prog.get("met") or 0), "total": int(prog.get("total") or 0), "target_date": target,
                           "overdue": bool(target and ms.get("status") == "active" and target < today
                                           and int(prog.get("met") or 0) < int(prog.get("total") or 0)),
                           "owner_pending": sum(1 for c in ms.get("criteria") or [] if c.get("state") == "owner_pending")})
    milestones.sort(key=lambda m: (m["status"] != "active", m["status"] == "done", m["id"]))
    asks: list[dict[str, Any]] = []
    inbox_error = None
    ms_ids = {m["id"] for m in milestones}
    try:
        inbox = collect_inbox(entries["home_root"], project=f.name, include_paused=True)
        ms_titles = {m["id"]: m["title"] for m in milestones}
        for item in inbox["items"]:
            facts = item.get("facts") or {}
            if item.get("category") != "owner_check" or (ms_ids and str(facts.get("milestone") or "") not in ms_ids):
                continue
            asks.append({"title": ms_titles.get(str(facts.get("milestone") or ""), "") or human_title(item.get("task_id"), item.get("title"), limit),
                         "url": f"{decl['nav_links']['inbox']}#item-{item['item_id']}"})
    except (OSError, ValueError, LookupError) as exc:
        inbox_error = _err(exc)
    headline = pick["headline"]

    def row_view(r: dict[str, Any], state: str) -> dict[str, Any]:
        return {"task_id": r["task_id"], "title": human_title(r["task_id"], r.get("title"), 2 * limit) or str(r["task_id"]), "state": state,
                "days": (int(r["idle_hours"] // 24) if r.get("idle_hours") is not None else None)}

    sel["advisor"] = {
        "instance": pick["instance"], "display": pick["display"], "paused": pick["paused"], "headline": headline,
        "stage": (headline or {}).get("stage") or "",
        "pushing": [row_view(r, "inflight") for r in pick["pushing"]] + [row_view(r, "todo") for r in pick["todo"]],
        "stalled": [row_view(r, "stalled") for r in pick["stalled"]],
        "milestones": milestones, "progress": pick["progress"],
        "deliverables": [_deliverable(f, r, home, decl) for r in pick["recent_closed"][: int(home["deliverables_limit"])]],
        "recent_closed": len(pick["recent_closed"]), "asks": asks, "inbox_error": inbox_error,
    }
    return out


# ---------------------------------------------------------------------------
# 禁词守卫(渲染测试与验收同用)
# ---------------------------------------------------------------------------

def visible_text(page_html: str, *, include_authored: bool = True) -> str:
    """页面可见文字(去 <script> / <style> / 标签与属性); include_authored=False = 另去掉 data-authored 标记的顾问 / 卡作者原文。"""
    import html as _html

    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page_html)
    if not include_authored:
        text = re.sub(r"(?is)<span\b[^>]*\bdata-authored\b[^>]*>.*?</span>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _html.unescape(text)


def forbidden_hits(page_html: str, words: list[str], *, extra: list[str] | None = None, include_authored: bool = True) -> list[str]:
    """页面可见文字里出现的禁词(声明 forbidden_words + extra, 如本根全部实例全名)。"""
    text = visible_text(page_html, include_authored=include_authored)
    return [w for w in list(words) + list(extra or []) if w and w in text]


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
