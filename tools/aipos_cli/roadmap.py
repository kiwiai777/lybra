"""AIPOS-F171 件①②: 结构化路线图(里程碑 + 验收标准)的唯一实现。

件① 声明: 每个治理根一份路线图文件(落点 = project.json paths.roadmap_file, 缺省 governance/roadmap.yaml; 文件形唯一声明
      config.schema governance_structure.roadmap)。读 = frontmatter.parse_yaml_mapping(与卡 frontmatter 同一解析), 写 =
      record_writer.render_yaml_document(同一序列化 + 回读校验), 零新依赖。编辑 = apply_roadmap_change(两阶段: 预演零写入;
      确认写文件 + decision_log 指针(board_adapter._auto_generate_decision_log_pointer, Record = 路线图文件) + 经
      governance_commit.land_written_paths 落账)。删除只允许无挂接卡时。
件② 卡挂接: 卡面 milestone / criteria(card.schema fields + roadmap_link)。游离判定唯一函数 card_link_status(F172 偏差推导复用);
      出卡(draft_validator)与 amend(board_adapter.amend_task)同调 link_problems: 引用不存在 = 拒, 未挂 = 警告(不拒), 项目无路线图 =
      不校验不警告(行为不变)。卡遍历只经 task_loader.iter_queue_task_paths。

任何项目同一组命令与声明: 本模块不写死项目 ID / 路径 / 卡号前缀。
"""
from __future__ import annotations

import copy
import difflib
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

#: 写命令子命令 → 计划函数(与 verbs.schema two_phase_protocol.roadmap_writers.commands 同集; 夹具锁一致)
SUBCOMMANDS = ("add-milestone", "add-criterion", "set", "remove")
#: 指针 / 落账来源说明
_ORIGIN = "lybra roadmap {subcommand} --confirm"
#: 渲染键序(声明里 required + optional 的序即落盘序)
_MILESTONE = "milestone"
_CRITERION = "criterion"


class RoadmapError(ValueError):
    """路线图读不出 / 不合声明 / 编辑被拒。code = config.schema governance_structure.roadmap.reject_codes 的键。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# 声明读取(唯一读取口; 缺键 = SchemaLoadError, fail-closed)
# ---------------------------------------------------------------------------

def roadmap_declaration() -> dict[str, Any]:
    """config.schema governance_structure.roadmap。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("config").get("governance_structure") or {}).get("roadmap")
    need = ("path_key", "top_level_keys", "milestone_keys", "criterion_keys", "id_pattern", "target_date_pattern", "status",
            "verify", "settable_fields", "reject_codes")
    if (not isinstance(decl, dict) or not all(k in decl for k in need)
            or not isinstance((decl.get("status") or {}).get("enum"), list)
            or (decl.get("status") or {}).get("default") not in (decl.get("status") or {}).get("enum", [])
            or not isinstance((decl.get("verify") or {}).get("enum"), list)
            or not all(isinstance((decl.get("settable_fields") or {}).get(k), list) for k in (_MILESTONE, _CRITERION))):
        raise SchemaLoadError("config.schema.json governance_structure.roadmap(path_key / *_keys / id_pattern / target_date_pattern / "
                              "status{enum, default} / verify{enum} / settable_fields{milestone, criterion} / reject_codes)未声明齐")
    return decl


def link_declaration() -> dict[str, Any]:
    """card.schema roadmap_link(挂接字段 / amend 允许状态 / 警告与拒因模板)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("card").get("roadmap_link")
    if (not isinstance(decl, dict) or decl.get("fields") != ["milestone", "criteria"]
            or not isinstance(decl.get("amend_states"), list) or not decl["amend_states"]
            or not all(isinstance(decl.get(k), str) and decl[k] for k in ("unlinked_warning", "invalid", "unreadable"))):
        raise SchemaLoadError("card.schema.json roadmap_link(fields=[milestone, criteria] / amend_states / unlinked_warning / invalid / "
                              "unreadable)未声明齐")
    return decl


def roadmap_texts() -> dict[str, str]:
    """verbs.schema verbs.lybra_roadmap.texts(CLI 文案)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    texts = ((load_schema("verbs").get("verbs") or {}).get("lybra_roadmap") or {}).get("texts")
    if not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) for k in ("no_roadmap", "header", "unlinked_line",
                                                                                     "decision_summary")):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_roadmap.texts(no_roadmap / header / unlinked_line / decision_summary)未声明齐")
    return texts


#: CLI 字段旗标帮助(旗标名 = 声明键投影; 本表只给帮助文案, 键须在声明里)
_FIELD_HELP = {
    ("milestone", "id"): "Milestone id (unique in the project)",
    ("milestone", "lane"): "Lane (sub-project) of the milestone",
    ("milestone", "title"): "One-line goal",
    ("milestone", "target_date"): "Target date YYYY-MM-DD (optional)",
    ("milestone", "status"): "Milestone status (default planned)",
    ("criterion", "id"): "Criterion id (unique in the project)",
    ("criterion", "statement"): "A checkable one-line statement",
    ("criterion", "verify"): "How it is verified (auto | artifact | owner_check)",
}
#: add-criterion 的所属里程碑旗标(非条目字段)
_PARENT_FLAG = {"flag": "--milestone", "dest": "milestone_id", "required": True, "help": "Milestone id the criterion belongs to"}


def roadmap_cli_fields() -> dict[str, Any]:
    """`lybra roadmap add-milestone / add-criterion / show` 的字段旗标 = 声明 milestone_keys / criterion_keys 的投影(键名 `_` → `-`;
    required 键必填; 列表型键 criteria 不作旗标)。show 的过滤旗标 = 里程碑 lane 键。"""
    decl = roadmap_declaration()

    def _flags(kind: str) -> list[dict[str, Any]]:
        keys = decl[f"{kind}_keys"]
        out = []
        for key in [*keys["required"], *keys["optional"]]:
            if key == "criteria":
                continue
            out.append({"flag": "--" + key.replace("_", "-"), "dest": key, "required": key in keys["required"],
                        "help": _FIELD_HELP.get((kind, key), key)})
        return out

    lane_key = next(k for k in decl["milestone_keys"]["required"] if k == "lane")
    return {"milestone": _flags(_MILESTONE), "criterion": [_PARENT_FLAG, *_flags(_CRITERION)],
            "lane_filter": {"flag": "--" + lane_key, "help": "Only milestones of this lane"}}


def roadmap_param_keys() -> list[str]:
    """CLI 薄壳交给 apply_roadmap_change 的参数键(字段旗标 dest + set 的 field/value)。"""
    fields = roadmap_cli_fields()
    keys = [f["dest"] for f in fields["milestone"]] + [f["dest"] for f in fields["criterion"]] + ["field", "value"]
    return list(dict.fromkeys(keys))


def _reject(code: str, detail: str, decl: dict[str, Any] | None = None) -> RoadmapError:
    decl = decl or roadmap_declaration()
    hint = str((decl.get("reject_codes") or {}).get(code) or "")
    return RoadmapError(code, f"{detail}({hint})" if hint else detail)


def roadmap_file(governance_root: str | Path) -> Path:
    """路线图文件路径: project.json paths.<path_key>(唯一读取口 workspace_config.project_paths; 缺省 = 声明 default)。"""
    from tools.aipos_cli.workspace_config import project_paths

    return Path(project_paths(Path(governance_root))[roadmap_declaration()["path_key"]])


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# 读与校验
# ---------------------------------------------------------------------------

def _single_line(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value and "\r" not in value


def _normalize_scalars(data: Any) -> Any:
    """手写文件里裸写的日期(YAML 解析成 date)按 ISO 文本对待, 其余原样。"""
    if isinstance(data, datetime):
        return data.date().isoformat()
    if isinstance(data, date):
        return data.isoformat()
    if isinstance(data, list):
        return [_normalize_scalars(v) for v in data]
    if isinstance(data, dict):
        return {k: _normalize_scalars(v) for k, v in data.items()}
    return data


def validate_roadmap(data: Any, decl: dict[str, Any] | None = None) -> list[str]:
    """整份路线图对声明逐条核对, 返回问题列表(空 = 合格)。"""
    decl = decl or roadmap_declaration()
    problems: list[str] = []
    if not isinstance(data, dict):
        return [f"顶层须为映射(得 {type(data).__name__})"]
    top = decl["top_level_keys"]
    allowed_top = set(top["required"]) | set(top["optional"])
    for key in sorted(set(data) - allowed_top):
        problems.append(f"顶层含未声明的键 {key!r}(允许 {sorted(allowed_top)})")
    for key in top["required"]:
        if key not in data:
            problems.append(f"缺顶层键 {key!r}")
    if "description" in data and data["description"] is not None and not isinstance(data["description"], str):
        problems.append("description 须为文本")
    milestones = data.get("milestones")
    if milestones is None:
        milestones = []
    if not isinstance(milestones, list):
        return problems + [f"milestones 须为列表(得 {type(milestones).__name__})"]
    id_re = re.compile(str(decl["id_pattern"]))
    date_re = re.compile(str(decl["target_date_pattern"]))
    seen: dict[str, str] = {}

    def _check_id(raw: Any, where: str) -> None:
        if not isinstance(raw, str) or not id_re.fullmatch(raw):
            problems.append(f"{where} id={raw!r} 不合式样 {decl['id_pattern']}")
            return
        if raw in seen:
            problems.append(f"{where} id={raw!r} 与 {seen[raw]} 重复(里程碑与标准同一命名空间, 项目内唯一)")
        else:
            seen[raw] = where

    def _check_keys(item: dict[str, Any], keys: dict[str, list[str]], where: str) -> None:
        allowed = set(keys["required"]) | set(keys["optional"])
        for key in sorted(set(item) - allowed):
            problems.append(f"{where} 含未声明的键 {key!r}(允许 {sorted(allowed)})")
        for key in keys["required"]:
            if key not in item:
                problems.append(f"{where} 缺 {key!r}")

    for i, ms in enumerate(milestones):
        where = f"milestones[{i}]"
        if not isinstance(ms, dict):
            problems.append(f"{where} 须为映射")
            continue
        _check_keys(ms, decl["milestone_keys"], where)
        _check_id(ms.get("id"), where)
        where = f"里程碑 {ms.get('id')!r}"
        for key in ("lane", "title"):
            if key in ms and not _single_line(ms[key]):
                problems.append(f"{where} {key} 须为非空单行文本")
        if ms.get("target_date") is not None and not (isinstance(ms["target_date"], str) and date_re.fullmatch(ms["target_date"])):
            problems.append(f"{where} target_date={ms['target_date']!r} 须为 YYYY-MM-DD")
        if ms.get("status") is not None and ms["status"] not in decl["status"]["enum"]:
            problems.append(f"{where} status={ms['status']!r} 不在值域 {decl['status']['enum']}")
        criteria = ms.get("criteria")
        if criteria is None:
            continue
        if not isinstance(criteria, list):
            problems.append(f"{where} criteria 须为列表")
            continue
        for j, crit in enumerate(criteria):
            cwhere = f"{where}.criteria[{j}]"
            if not isinstance(crit, dict):
                problems.append(f"{cwhere} 须为映射")
                continue
            _check_keys(crit, decl["criterion_keys"], cwhere)
            _check_id(crit.get("id"), cwhere)
            if "statement" in crit and not _single_line(crit["statement"]):
                problems.append(f"{cwhere} statement 须为非空单行文本")
            if "verify" in crit and crit["verify"] not in decl["verify"]["enum"]:
                problems.append(f"{cwhere} verify={crit['verify']!r} 不在值域 {decl['verify']['enum']}")
    return problems


def load_roadmap(governance_root: str | Path) -> dict[str, Any]:
    """读本治理根的路线图。返回 {path, rel, exists, data, text, milestones}; 文件不存在 = exists False(未声明路线图, 非错);
    读不出 / 不合声明 = RoadmapError(ROADMAP_INVALID, 逐条问题)。"""
    from tools.aipos_cli.frontmatter import parse_yaml_mapping

    root = Path(governance_root)
    decl = roadmap_declaration()
    path = roadmap_file(root)
    base = {"path": path, "rel": _rel(path, root), "governance_root": root}
    if not path.exists():
        return {**base, "exists": False, "data": {"milestones": []}, "text": "", "milestones": []}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _reject("ROADMAP_INVALID", f"{path} 读不出: {exc}", decl) from exc
    if not text.strip():  # 空文件 = 未声明(与不存在同判; 确认写入时锁内新建的空文件即此形)
        return {**base, "exists": False, "data": {"milestones": []}, "text": "", "milestones": []}
    data, warnings = parse_yaml_mapping(text)
    if warnings:
        raise _reject("ROADMAP_INVALID", f"{path} 解析失败: {'; '.join(warnings)}", decl)
    data = _normalize_scalars(data)
    problems = validate_roadmap(data, decl)
    if problems:
        raise _reject("ROADMAP_INVALID", f"{path}: " + "; ".join(problems), decl)
    return {**base, "exists": True, "data": data, "text": text, "milestones": list(data.get("milestones") or [])}


def roadmap_index(roadmap: dict[str, Any]) -> dict[str, Any]:
    """{milestones: {id: 里程碑}, criteria: {id: (所属里程碑 id, 标准)}}。"""
    milestones: dict[str, dict[str, Any]] = {}
    criteria: dict[str, tuple[str, dict[str, Any]]] = {}
    for ms in roadmap.get("milestones") or []:
        milestones[str(ms["id"])] = ms
        for crit in ms.get("criteria") or []:
            criteria[str(crit["id"])] = (str(ms["id"]), crit)
    return {"milestones": milestones, "criteria": criteria}


# ---------------------------------------------------------------------------
# 卡挂接(件②): 游离判定唯一函数 + 出卡 / amend 校验
# ---------------------------------------------------------------------------

LINK_NO_ROADMAP = "no_roadmap"
LINK_LINKED = "linked"
LINK_UNLINKED = "unlinked"
LINK_INVALID = "invalid"


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def card_link_status(metadata: dict[str, Any], roadmap: dict[str, Any] | None) -> dict[str, Any]:
    """卡面挂接判定(唯一实现; F172 偏差推导复用)。roadmap = load_roadmap 的结果(None / 文件不存在 = 项目未声明路线图)。
    返回 {state: no_roadmap|linked|unlinked|invalid, milestone, criteria, problems}。
    - no_roadmap: 项目未声明路线图(不判游离)
    - unlinked: 未挂里程碑(游离卡)
    - invalid: 引用不存在 / 标准不属所挂里程碑 / 只给标准不给里程碑 / 字段形不合
    - linked: 已挂且引用全部存在"""
    milestone = metadata.get("milestone")
    criteria = metadata.get("criteria")
    out: dict[str, Any] = {"milestone": milestone, "criteria": criteria, "problems": []}
    if roadmap is None or not roadmap.get("exists"):
        return {**out, "state": LINK_NO_ROADMAP}
    if _missing(milestone) and _missing(criteria):
        return {**out, "state": LINK_UNLINKED}
    problems: list[str] = []
    if not _missing(milestone) and not isinstance(milestone, str):
        problems.append(f"milestone 须为文本(得 {type(milestone).__name__})")
    if not _missing(criteria) and (not isinstance(criteria, list) or not all(isinstance(c, str) and c for c in criteria)):
        problems.append("criteria 须为文本列表")
        criteria = []
    if _missing(milestone) and not _missing(criteria):
        problems.append("给 criteria 须同时给 milestone(标准挂在里程碑之下)")
    index = roadmap_index(roadmap)
    rel = roadmap.get("rel")
    if isinstance(milestone, str) and milestone and milestone not in index["milestones"]:
        problems.append(f"milestone {milestone!r} 不在路线图 {rel}")
    for crit in criteria or []:
        owner = index["criteria"].get(crit)
        if owner is None:
            problems.append(f"标准 {crit!r} 不在路线图 {rel}")
        elif isinstance(milestone, str) and milestone and owner[0] != milestone:
            problems.append(f"标准 {crit!r} 属于里程碑 {owner[0]!r}, 不属 {milestone!r}")
    return {**out, "problems": problems, "state": LINK_INVALID if problems else LINK_LINKED}


def link_problems(metadata: dict[str, Any], governance_root: str | Path) -> tuple[list[str], list[str]]:
    """出卡(draft create / validate / publish)与 queue amend 的挂接校验: (拒因, 警告)。
    无路线图 = 无拒无警; 引用不合 = 拒 ROADMAP_LINK_INVALID; 未挂 = 警告 ROADMAP_UNLINKED(不拒);
    路线图读不出: 卡给了挂接 = 拒(无法核对), 未给 = 警告。"""
    link = link_declaration()
    has_link = any(not _missing(metadata.get(f)) for f in link["fields"])
    try:
        roadmap = load_roadmap(governance_root)
    except RoadmapError as exc:
        message = link["unreadable"].format(reason=str(exc))
        return ([message], []) if has_link else ([], [message])
    status = card_link_status(metadata, roadmap)
    if status["state"] == LINK_INVALID:
        return [link["invalid"].format(reason="; ".join(status["problems"]))], []
    if status["state"] == LINK_UNLINKED:
        ids = [str(ms["id"]) for ms in roadmap["milestones"] if ms.get("status") != "dropped"]
        return [], [link["unlinked_warning"].format(roadmap=roadmap["rel"], milestones=", ".join(ids) or "(无)")]
    return [], []


def card_links(governance_root: str | Path, roadmap: dict[str, Any] | None = None) -> dict[str, Any]:
    """本治理根队列全部状态的卡(task_loader.iter_queue_task_paths 唯一遍历)的挂接判定。
    返回 {cards: [{task_id, queue_state, path, state, milestone, criteria, problems}], unreadable: [{path, reason}]}。
    卡面读不出 = 进 unreadable(不当作未挂, 删除判定据此 fail-closed)。"""
    from tools.aipos_cli.task_loader import iter_queue_task_paths, load_task_file

    root = Path(governance_root)
    if roadmap is None:
        roadmap = load_roadmap(root)
    cards: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    for path in iter_queue_task_paths(root):
        task = load_task_file(path, root)
        if task.get("parse_errors"):
            unreadable.append({"path": task["path"], "reason": "; ".join(task["parse_errors"])})
            continue
        status = card_link_status(task.get("metadata") or {}, roadmap)
        cards.append({"task_id": task.get("task_id"), "queue_state": task.get("queue_state"), "path": task["path"], **status})
    return {"cards": cards, "unreadable": unreadable}


def _linked_to(links: dict[str, Any], *, milestone: str | None = None, criterion: str | None = None) -> list[dict[str, Any]]:
    hits = []
    for card in links["cards"]:
        crits = card.get("criteria") if isinstance(card.get("criteria"), list) else []
        if (milestone is not None and card.get("milestone") == milestone) or (criterion is not None and criterion in crits):
            hits.append(card)
    return hits


# ---------------------------------------------------------------------------
# 编辑(件①): 计划 → 校验 → 两阶段写 + 指针 + 落账
# ---------------------------------------------------------------------------

def _ordered_item(item: dict[str, Any], keys: dict[str, list[str]]) -> dict[str, Any]:
    order = [*keys["required"], *keys["optional"]]
    return {k: item[k] for k in order if k in item and item[k] is not None}


def _ordered_document(data: dict[str, Any], decl: dict[str, Any]) -> dict[str, Any]:
    milestones = []
    for ms in data.get("milestones") or []:
        out = _ordered_item(ms, decl["milestone_keys"])
        if "criteria" in out:
            out["criteria"] = [_ordered_item(c, decl["criterion_keys"]) for c in out["criteria"]]
        milestones.append(out)
    doc = {k: v for k, v in data.items() if k != "milestones" and v is not None}
    doc["milestones"] = milestones
    return doc


def render_roadmap(data: dict[str, Any]) -> str:
    """路线图文本(record_writer.render_yaml_document 同一序列化 + 回读校验)。"""
    from tools.aipos_cli.record_writer import render_yaml_document

    decl = roadmap_declaration()
    top = decl["top_level_keys"]
    return render_yaml_document(_ordered_document(data, decl), [*top["optional"], *top["required"]])


def _find(data: dict[str, Any], item_id: str) -> tuple[str, dict[str, Any], dict[str, Any] | None] | None:
    """id → (kind, 条目, 所属里程碑 | None)。"""
    for ms in data.get("milestones") or []:
        if ms.get("id") == item_id:
            return _MILESTONE, ms, None
        for crit in ms.get("criteria") or []:
            if crit.get("id") == item_id:
                return _CRITERION, crit, ms
    return None


def plan_change(roadmap: dict[str, Any], subcommand: str, params: dict[str, Any], *, links: dict[str, Any] | None = None) -> dict[str, Any]:
    """按子命令算改动后的整份路线图(不写盘)。返回 {data, target, detail}; 拒 = RoadmapError。
    remove 需 links(card_links 结果; 删除前核挂接卡)。"""
    decl = roadmap_declaration()
    data = copy.deepcopy(roadmap["data"]) if roadmap.get("exists") else {"milestones": []}
    data.setdefault("milestones", [])
    if data["milestones"] is None:
        data["milestones"] = []
    if subcommand not in SUBCOMMANDS:
        raise ValueError(f"未知子命令 {subcommand!r}(只认 {SUBCOMMANDS})")
    item_id = str(params.get("milestone_id" if subcommand == "add-criterion" else "id") or "").strip()
    if subcommand == "add-milestone":
        new_id = str(params.get("id") or "").strip()
        if _find(data, new_id):
            raise _reject("ROADMAP_ID_DUPLICATE", f"id {new_id!r} 已在路线图", decl)
        item = {"id": new_id, "lane": params.get("lane"), "title": params.get("title")}
        if params.get("target_date"):
            item["target_date"] = params["target_date"]
        if params.get("status"):
            item["status"] = params["status"]
        data["milestones"].append(item)
        return {"data": data, "target": new_id, "detail": f"新增里程碑 {new_id}(lane={item['lane']}): {item['title']}"}
    if subcommand == "add-criterion":
        found = _find(data, item_id)
        if not found or found[0] != _MILESTONE:
            raise _reject("ROADMAP_ID_UNKNOWN", f"里程碑 {item_id!r} 不在路线图", decl)
        new_id = str(params.get("id") or "").strip()
        if _find(data, new_id):
            raise _reject("ROADMAP_ID_DUPLICATE", f"id {new_id!r} 已在路线图", decl)
        crit = {"id": new_id, "statement": params.get("statement"), "verify": params.get("verify")}
        found[1].setdefault("criteria", [])
        if found[1]["criteria"] is None:
            found[1]["criteria"] = []
        found[1]["criteria"].append(crit)
        return {"data": data, "target": new_id, "detail": f"里程碑 {item_id} 新增标准 {new_id}({crit['verify']}): {crit['statement']}"}
    found = _find(data, item_id)
    if not found:
        raise _reject("ROADMAP_ID_UNKNOWN", f"id {item_id!r} 不在路线图", decl)
    kind, item, parent = found
    if subcommand == "set":
        field = str(params.get("field") or "").strip()
        if field not in decl["settable_fields"][kind]:
            raise _reject("ROADMAP_FIELD_NOT_SETTABLE",
                          f"{'里程碑' if kind == _MILESTONE else '标准'} {item_id} 的字段 {field!r} 不可改(可改: {decl['settable_fields'][kind]})", decl)
        value = params.get("value")
        before = item.get(field)
        optional = field in decl[f"{kind}_keys"]["optional"]
        if optional and (value is None or str(value) == ""):
            item.pop(field, None)
        else:
            item[field] = value
        return {"data": data, "target": item_id, "detail": f"{item_id}.{field}: {before!r} → {item.get(field)!r}"}
    # remove
    if links is None:
        raise ValueError("remove 须给 links(card_links 结果)")
    if links["unreadable"]:
        raise _reject("ROADMAP_REMOVE_LINKED", f"有 {len(links['unreadable'])} 张卡卡面读不出, 挂接无法核对, 不删(首张: "
                                               f"{links['unreadable'][0]['path']})", decl)
    if kind == _MILESTONE:
        hits = _linked_to(links, milestone=item_id)
        for crit in item.get("criteria") or []:
            hits += [c for c in _linked_to(links, criterion=str(crit["id"])) if c not in hits]
    else:
        hits = _linked_to(links, criterion=item_id)
    if hits:
        names = ", ".join(f"{c['task_id']}({c['queue_state']})" for c in hits)
        raise _reject("ROADMAP_REMOVE_LINKED", f"{'里程碑' if kind == _MILESTONE else '标准'} {item_id} 有 {len(hits)} 张挂接卡: {names}", decl)
    if kind == _MILESTONE:
        data["milestones"] = [ms for ms in data["milestones"] if ms is not item]
    else:
        assert parent is not None
        parent["criteria"] = [c for c in parent.get("criteria") or [] if c is not item]
        if not parent["criteria"]:
            parent.pop("criteria")
    return {"data": data, "target": item_id, "detail": f"删除{'里程碑' if kind == _MILESTONE else '标准'} {item_id}(无挂接卡)"}


def _decision_id(subcommand: str, target: str) -> str:
    from tools.aipos_cli.clock import file_slug

    return f"roadmap-{subcommand}-{target}-{file_slug('compact')}"


def apply_roadmap_change(governance_root: str | Path, subcommand: str, params: dict[str, Any], *, actor: str, confirm: bool,
                         project: str, push: bool = True) -> dict[str, Any]:
    """两阶段写(唯一实现)。预演: 校验改动后的整份路线图, 给 unified diff 与将生成的指针名, 零写入。确认: 在路线图文件排他锁内重读、
    重算、写入, 生成 decision_log 指针(Record = 路线图文件), 经 land_written_paths(roadmap_writers)落账。
    返回 {dry_run, changed, written, path, rel, diff, target, detail, pointer, pointer_warning, landing}。"""
    from tools.aipos_cli.board_adapter import _auto_generate_decision_log_pointer
    from tools.aipos_cli.workspace_config import exclusive_flock

    root = Path(governance_root)
    decl = roadmap_declaration()
    texts = roadmap_texts()

    def _compute() -> dict[str, Any]:
        roadmap = load_roadmap(root)
        links = card_links(root, roadmap) if subcommand == "remove" else None
        plan = plan_change(roadmap, subcommand, params, links=links)
        problems = validate_roadmap(plan["data"], decl)
        if problems:
            raise _reject("ROADMAP_INVALID", "改动后的路线图不合声明: " + "; ".join(problems), decl)
        new_text = render_roadmap(plan["data"])
        old_text = roadmap["text"] if roadmap["exists"] else ""
        diff = "".join(difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                            fromfile=f"a/{roadmap['rel']}", tofile=f"b/{roadmap['rel']}"))
        return {"roadmap": roadmap, "plan": plan, "new_text": new_text, "changed": new_text != old_text, "diff": diff}

    computed = _compute()
    roadmap, plan = computed["roadmap"], computed["plan"]
    summary = texts["decision_summary"].format(subcommand=subcommand, target=plan["target"], detail=plan["detail"])
    decision_id = _decision_id(subcommand, plan["target"])
    out: dict[str, Any] = {"dry_run": not confirm, "changed": computed["changed"], "written": False, "path": str(roadmap["path"]),
                           "rel": roadmap["rel"], "diff": computed["diff"], "target": plan["target"], "detail": plan["detail"],
                           "summary": summary, "pointer": None, "pointer_warning": None, "landing": None}
    if not confirm or not computed["changed"]:
        if computed["changed"]:
            probe = _auto_generate_decision_log_pointer(root, decision_id, summary, actor, roadmap["rel"], dry_run=True)
            out["pointer"], out["pointer_warning"] = probe.get("path"), probe.get("warning")
        return out
    path = Path(roadmap["path"])
    created = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a+", encoding="utf-8") as handle, exclusive_flock(handle):
            computed = _compute()  # 锁内重读重算(并发写方排队, 不覆盖他方改动)
            roadmap, plan = computed["roadmap"], computed["plan"]
            out.update(changed=computed["changed"], diff=computed["diff"], target=plan["target"], detail=plan["detail"])
            if not computed["changed"]:
                return out
            handle.seek(0)
            handle.truncate()
            handle.write(computed["new_text"])
            handle.flush()
            os.fsync(handle.fileno())
            out["written"] = True
    finally:
        if created and not out["written"] and path.exists() and path.stat().st_size == 0:
            path.unlink()  # 为加锁新建的空文件: 拒写 / 无改动即撤掉(零写入)
    summary = texts["decision_summary"].format(subcommand=subcommand, target=plan["target"], detail=plan["detail"])
    pointer = _auto_generate_decision_log_pointer(root, _decision_id(subcommand, plan["target"]), summary, actor, roadmap["rel"],
                                                  origin=_ORIGIN.format(subcommand=subcommand))
    out["summary"], out["pointer"], out["pointer_warning"] = summary, pointer.get("path"), pointer.get("warning")
    from tools.aipos_cli.governance_commit import land_written_paths

    written = [roadmap["rel"], *([pointer["path"]] if pointer.get("path") else [])]
    out["landing"] = land_written_paths(root, written, actor, command="roadmap_writers", push=push, subcommand=subcommand,
                                        target=plan["target"], project=project)
    return out


# ---------------------------------------------------------------------------
# 只读呈现(`lybra roadmap show`; 看板项目页简版读同一 roadmap_view)
# ---------------------------------------------------------------------------

def roadmap_view(governance_root: str | Path, *, lane: str | None = None, project: str | None = None) -> dict[str, Any]:
    """只读视图: 里程碑(按 lane 过滤)+ 标准 + 各自挂接卡数 + 游离卡。读不出 = RoadmapError 上抛(调用方出声)。"""
    root = Path(governance_root)
    roadmap = load_roadmap(root)
    out: dict[str, Any] = {"project": project, "path": str(roadmap["path"]), "rel": roadmap["rel"], "exists": roadmap["exists"],
                           "lane": lane, "milestones": [], "unlinked": [], "invalid": [], "unreadable_cards": []}
    if not roadmap["exists"]:
        return out
    links = card_links(root, roadmap)
    decl = roadmap_declaration()
    for ms in roadmap["milestones"]:
        if lane and ms.get("lane") != lane:
            continue
        ms_cards = _linked_to(links, milestone=str(ms["id"]))
        criteria = [{"id": c["id"], "statement": c["statement"], "verify": c["verify"],
                     "cards": [x["task_id"] for x in _linked_to(links, criterion=str(c["id"]))]} for c in ms.get("criteria") or []]
        out["milestones"].append({"id": ms["id"], "lane": ms["lane"], "title": ms["title"], "target_date": ms.get("target_date"),
                                  "status": ms.get("status") or decl["status"]["default"], "criteria": criteria,
                                  "cards": [x["task_id"] for x in ms_cards]})
    out["unlinked"] = [c["task_id"] for c in links["cards"] if c["state"] == LINK_UNLINKED]
    out["invalid"] = [{"task_id": c["task_id"], "problems": c["problems"]} for c in links["cards"] if c["state"] == LINK_INVALID]
    out["unreadable_cards"] = links["unreadable"]
    return out


def render_view_text(view: dict[str, Any]) -> str:
    texts = roadmap_texts()
    if not view["exists"]:
        return texts["no_roadmap"].format(project=view.get("project") or "-", path=view["path"])
    n_crit = sum(len(ms["criteria"]) for ms in view["milestones"])
    lines = [texts["header"].format(project=view.get("project") or "-", path=view["rel"], milestones=len(view["milestones"]),
                                    criteria=n_crit)]
    for ms in view["milestones"]:
        tail = f" · 目标 {ms['target_date']}" if ms.get("target_date") else ""
        lines.append(f"[{ms['lane']}] {ms['id']} ({ms['status']}{tail}) {ms['title']} — 挂接卡 {len(ms['cards'])}")
        for crit in ms["criteria"]:
            lines.append(f"    - {crit['id']} [{crit['verify']}] {crit['statement']} — 挂接卡 {len(crit['cards'])}")
    sample = (": " + ", ".join(str(t) for t in view["unlinked"][:10]) + (" …" if len(view["unlinked"]) > 10 else "")) if view["unlinked"] else ""
    lines.append(texts["unlinked_line"].format(count=len(view["unlinked"]), sample=sample))
    for bad in view["invalid"]:
        lines.append(f"  [挂接不合] {bad['task_id']}: {'; '.join(bad['problems'])}")
    for bad in view["unreadable_cards"]:
        lines.append(f"  [卡面读不出] {bad['path']}: {bad['reason']}")
    return "\n".join(lines)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
