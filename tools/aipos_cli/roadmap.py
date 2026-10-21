"""AIPOS-F171 件①②: 结构化路线图(里程碑 + 验收标准)的唯一实现。

件① 声明: 每个治理根一份路线图文件(落点 = project.json paths.roadmap_file, 缺省 governance/roadmap.yaml; 文件形唯一声明
      config.schema governance_structure.roadmap)。读 = frontmatter.parse_yaml_mapping(与卡 frontmatter 同一解析), 写 =
      record_writer.render_yaml_document(同一序列化 + 回读校验), 零新依赖。编辑 = apply_roadmap_change(两阶段: 预演零写入;
      确认写文件 + decision_log 指针(board_adapter._auto_generate_decision_log_pointer, Record = 路线图文件) + 经
      governance_commit.land_written_paths 落账)。删除只允许无挂接卡时。
件② 卡挂接: 卡面 milestone / criteria(card.schema fields + roadmap_link)。游离判定唯一函数 card_link_status(F172 偏差推导复用);
      出卡(draft_validator)与 amend(board_adapter.amend_task)同调 link_problems: 引用不存在 = 拒, 未挂 = 警告(不拒), 项目无路线图 =
      不校验不警告(行为不变)。卡遍历只经 task_loader.iter_queue_task_paths。

AIPOS-F175 件②③: 卡面验收产物(card.schema fields.artifacts, 约定 card_artifacts)的形校验 artifact_problems(出卡 / amend 经
      link_problems 同调)、解析 card_artifacts(推导 roadmap_drift 与看板同读)、看板取图判定 artifact_file; Owner 抽查结论两阶段写
      apply_owner_verification(写既有 owner_verifications 记录, 不符经既有出卡口 draft_writer.create_draft 起草修复卡, 经
      land_written_paths 落账)。

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

#: 写命令子命令(与 verbs.schema two_phase_protocol.roadmap_writers.commands 同集; 夹具锁一致)。前四个经 plan_change 计划;
#: AIPOS-F175: verify = Owner 抽查结论(apply_owner_verification)
SUBCOMMANDS = ("add-milestone", "add-criterion", "set", "remove", "verify")
PLAN_SUBCOMMANDS = SUBCOMMANDS[:4]
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
    # AIPOS-F175 件②: 卡面 artifacts 形不合 = 拒(与是否声明路线图无关; 唯一判据 artifact_problems)
    art = artifact_problems(metadata)
    art_block = [artifacts_declaration()["invalid"].format(reason="; ".join(art))] if art else []
    has_link = any(not _missing(metadata.get(f)) for f in link["fields"])
    try:
        roadmap = load_roadmap(governance_root)
    except RoadmapError as exc:
        message = link["unreadable"].format(reason=str(exc))
        return ([*art_block, message], []) if has_link else (art_block, [message])
    status = card_link_status(metadata, roadmap)
    if status["state"] == LINK_INVALID:
        return [*art_block, link["invalid"].format(reason="; ".join(status["problems"]))], []
    if status["state"] == LINK_UNLINKED:
        ids = [str(ms["id"]) for ms in roadmap["milestones"] if ms.get("status") != "dropped"]
        return art_block, [link["unlinked_warning"].format(roadmap=roadmap["rel"], milestones=", ".join(ids) or "(无)")]
    return art_block, []


def card_links(governance_root: str | Path, roadmap: dict[str, Any] | None = None) -> dict[str, Any]:
    """本治理根队列全部状态的卡(task_loader.iter_queue_task_paths 唯一遍历)的挂接判定。
    返回 {cards: [{task_id, queue_state, path, state, milestone, criteria, problems, metadata}], unreadable: [{path, reason}]}。
    卡面读不出 = 进 unreadable(不当作未挂, 删除判定据此 fail-closed)。AIPOS-F173: 每卡带卡面 metadata(偏差推导 roadmap_drift 读
    时刻 / 返工轮次 / 运行证据声明, 不再二次读卡)。"""
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
        cards.append({"task_id": task.get("task_id"), "queue_state": task.get("queue_state"), "path": task["path"], **status,
                      "metadata": task.get("metadata") or {}})
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
    if subcommand not in PLAN_SUBCOMMANDS:
        raise ValueError(f"未知子命令 {subcommand!r}(只认 {PLAN_SUBCOMMANDS})")
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


def milestone_declared_at(roadmap: dict[str, Any], pointer_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """AIPOS-F181 item 3: when each milestone was declared = the earliest decision_log pointer (Record = this roadmap file) written by
    `lybra roadmap add-milestone` for it; matched by the summary prefix of the same template the writer uses
    (texts.decision_summary, subcommand add-milestone, target = milestone id), never by guessing from the file name.
    Returns {"milestones": {id: ISO instant}, "first_pointer": earliest ISO instant of any pointer to this roadmap | None}."""
    texts = roadmap_texts()
    head, _sep, _rest = str(texts["decision_summary"]).partition("{detail}")
    entries = [e for e in pointer_entries if roadmap["rel"] in (e.get("records") or []) and e.get("decided_at")]
    stamps = sorted(str(e["decided_at"]) for e in entries)
    out: dict[str, str] = {}
    for ms in (roadmap.get("data") or {}).get("milestones") or []:
        prefix = head.format(subcommand="add-milestone", target=str(ms.get("id")))
        hits = sorted(str(e["decided_at"]) for e in entries if str(e.get("summary") or "").startswith(prefix))
        if hits:
            out[str(ms.get("id"))] = hits[0]
    return {"milestones": out, "first_pointer": stamps[0] if stamps else None}


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


# ---------------------------------------------------------------------------
# AIPOS-F175 件②: 卡面验收产物(card.schema fields.artifacts; 约定 card.schema card_artifacts)
# ---------------------------------------------------------------------------

def artifacts_declaration() -> dict[str, Any]:
    """card.schema card_artifacts(唯一读取口; fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("card").get("card_artifacts")
    ok = (isinstance(decl, dict) and isinstance(decl.get("field"), str) and decl["field"]
          and isinstance(decl.get("dir"), str) and "{task_id}" in decl["dir"] and "{return_root}" in decl["dir"]
          and isinstance((decl.get("entry_keys") or {}).get("required"), list)
          and isinstance((decl.get("entry_keys") or {}).get("optional"), list)
          and {"criterion", "path_glob", "kind"} <= set(decl["entry_keys"]["required"])
          and all(isinstance(decl.get(k), list) and decl[k] for k in ("kinds", "image_suffixes", "text_suffixes"))
          and isinstance(decl.get("excerpt_bytes"), int) and not isinstance(decl.get("excerpt_bytes"), bool) and decl["excerpt_bytes"] > 0
          and isinstance(decl.get("invalid"), str) and decl["invalid"])
    if not ok:
        raise SchemaLoadError("card.schema.json card_artifacts(field / dir 含 {return_root} {task_id} / entry_keys{required ⊇ "
                              "criterion,path_glob,kind; optional} / kinds / image_suffixes / text_suffixes / excerpt_bytes>0 / invalid)未声明齐")
    from tools.schema_loader import get_card_field_schema

    if get_card_field_schema(decl["field"]) is None:
        raise SchemaLoadError(f"card.schema.json card_artifacts.field={decl['field']!r} 未在 fields 声明")
    return decl


def _glob_problem(raw: Any) -> str | None:
    if not _single_line(raw):
        return "path_glob 须为非空单行文本"
    text = str(raw).strip()
    if text.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:", text):
        return f"path_glob {text!r} 须相对卡产物落点(禁绝对路径 / ~)"
    if ".." in re.split(r"[\\/]", text):
        return f"path_glob {text!r} 不得含 .. (禁出产物落点)"
    return None


def artifact_problems(metadata: dict[str, Any]) -> list[str]:
    """卡面 artifacts 形校验(唯一判据; 出卡 / amend 经 link_problems 同调)。未声明 = []。"""
    decl = artifacts_declaration()
    raw = metadata.get(decl["field"])
    if _missing(raw):
        return []
    if not isinstance(raw, list):
        return [f"{decl['field']} 须为列表(得 {type(raw).__name__})"]
    keys = decl["entry_keys"]
    allowed = set(keys["required"]) | set(keys["optional"])
    criteria = metadata.get("criteria") if isinstance(metadata.get("criteria"), list) else []
    problems: list[str] = []
    for i, entry in enumerate(raw):
        where = f"{decl['field']}[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} 须为映射")
            continue
        for key in sorted(set(entry) - allowed):
            problems.append(f"{where} 含未声明的键 {key!r}(允许 {sorted(allowed)})")
        for key in keys["required"]:
            if _missing(entry.get(key)):
                problems.append(f"{where} 缺 {key!r}")
        crit = entry.get("criterion")
        if not _missing(crit) and crit not in criteria:
            problems.append(f"{where} criterion {crit!r} 不在本卡 criteria {criteria}(产物挂在本卡挂接的标准上)")
        if not _missing(entry.get("kind")) and entry.get("kind") not in decl["kinds"]:
            problems.append(f"{where} kind={entry.get('kind')!r} 不在值域 {decl['kinds']}")
        if not _missing(entry.get("path_glob")):
            glob_problem = _glob_problem(entry.get("path_glob"))
            if glob_problem:
                problems.append(f"{where} {glob_problem}")
    return problems


def card_artifact_dir(governance_root: str | Path, task_id: str) -> Path:
    """卡产物落点 = card_artifacts.dir(return_root 经唯一读取口 workspace_config.project_paths; 在执行体交回落点内)。"""
    from tools.aipos_cli.workspace_config import project_paths

    root = Path(governance_root)
    text = artifacts_declaration()["dir"].format(return_root=str(project_paths(root)["return_root"]), task_id=task_id)
    path = Path(text)
    return path if path.is_absolute() else root / path


def _within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return False
    return True


def card_artifacts(governance_root: str | Path, metadata: dict[str, Any], *, criterion: str | None = None) -> dict[str, Any]:
    """卡面声明的产物 → 落点内实际文件(唯一解析; 推导 roadmap_drift.card_evidence 与看板同读)。只读。
    返回 {task_id, dir, dir_rel, declared, problems, entries: [{criterion, kind, path_glob, files: [{rel, name, size, media, excerpt}]}]};
    criterion 给出 = 只列该标准的项。声明形不合 = problems 非空、entries 空(不猜); 文件读不出 = 该文件不计入(problems 点名)。"""
    decl = artifacts_declaration()
    root = Path(governance_root)
    task_id = str(metadata.get("task_id") or "")
    base = card_artifact_dir(root, task_id)
    out: dict[str, Any] = {"task_id": task_id, "dir": str(base), "dir_rel": _rel(base, root), "declared": False,
                           "problems": [], "entries": []}
    problems = artifact_problems(metadata)
    if problems:
        out["problems"] = problems
        return out
    raw = metadata.get(decl["field"]) or []
    image = {s.lower() for s in decl["image_suffixes"]}
    text_suffixes = {s.lower() for s in decl["text_suffixes"]}
    limit = int(decl["excerpt_bytes"])
    for entry in raw:
        if criterion is not None and entry.get("criterion") != criterion:
            continue
        out["declared"] = True
        files: list[dict[str, Any]] = []
        candidates = sorted(base.glob(str(entry["path_glob"]).strip())) if base.is_dir() else []
        for path in candidates:
            if not path.is_file() or not _within(path, base):
                continue
            suffix = path.suffix.lower()
            media = "image" if suffix in image else ("text" if suffix in text_suffixes else "other")
            item: dict[str, Any] = {"rel": _rel(path, root), "name": path.relative_to(base).as_posix(), "media": media,
                                    "size": None, "excerpt": None}
            try:
                item["size"] = path.stat().st_size
                if media == "text":
                    with open(path, "rb") as handle:
                        chunk = handle.read(limit + 1)
                    clipped = chunk[:limit].decode("utf-8", errors="replace")
                    item["excerpt"] = clipped + (" …" if len(chunk) > limit else "")
            except OSError as exc:
                out["problems"].append(f"产物 {item['rel']} 读不出: {exc}")
                continue
            files.append(item)
        out["entries"].append({"criterion": entry["criterion"], "kind": entry["kind"], "path_glob": str(entry["path_glob"]).strip(),
                               "files": files})
    return out


def artifact_file(governance_root: str | Path, task_id: str, name: str) -> Path | None:
    """看板取图判定(唯一): 卡 task_id 的卡面 artifacts 声明匹配到、在卡产物落点内、后缀属 image_suffixes 的文件(name = 落点内相对名)。
    其余(卡不存在 / 未声明 / 不在落点 / 非图片)= None。只读。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    root = Path(governance_root)
    try:
        path, _state = find_task_card(root, str(task_id))
        if path is None:
            return None
        meta, _body = require_frontmatter(path)
    except (AmbiguousTaskCard, FrontmatterReadError, OSError, ValueError):
        return None
    view = card_artifacts(root, meta)
    for entry in view["entries"]:
        for item in entry["files"]:
            if item["name"] == name and item["media"] == "image":
                return card_artifact_dir(root, str(task_id)) / item["name"]
    return None


# ---------------------------------------------------------------------------
# AIPOS-F175 件③: Owner 抽查结论(`lybra roadmap verify`; 写既有 owner_verifications 记录 + 不符起草修复卡)
# ---------------------------------------------------------------------------

#: verbs.lybra_roadmap.verify.fix_card text keys (AIPOS-F180C appended artifacts_missing / evidence_none / manual_hint)
_FIX_CARD_TEXT_KEYS = ("task_id_template", "title_template", "body_template", "publish_hint", "artifacts_missing", "evidence_none",
                       "manual_hint")
#: verbs.lybra_roadmap.verify.texts keys (AIPOS-F180C appended fix_blocked / record_kept_* / fix_error)
_VERIFY_TEXT_KEYS = ("header", "outcome", "record", "state", "fix", "will", "fix_blocked", "record_kept_preview", "record_kept_written",
                     "fix_error")


def verify_declaration() -> dict[str, Any]:
    """verbs.schema lybra_roadmap.verify(唯一读取口; fail-closed): results 键 = owner_verification_writer.RESULT_DECISIONS 且 decision 一致,
    state 属 status.verification.states; verifiable_states 属 states; fix_card 模板齐。"""
    from tools.aipos_cli.owner_verification_writer import RESULT_DECISIONS
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    verb = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_roadmap") or {}
    decl = verb.get("verify")
    states = (((verb.get("status") or {}).get("verification") or {}).get("states") or {})
    need = ("results", "verifiable_states", "reject_codes", "fix_card", "texts")
    if not isinstance(decl, dict) or any(k not in decl for k in need):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_roadmap.verify 未声明齐 {list(need)}")
    results = decl["results"]
    if (not isinstance(results, dict) or set(results) != set(RESULT_DECISIONS)
            or any(results[k].get("decision") != RESULT_DECISIONS[k] or results[k].get("state") not in states
                   or not str(results[k].get("label") or "") for k in results)):
        raise SchemaLoadError(f"verbs.lybra_roadmap.verify.results 须恰为 {RESULT_DECISIONS} 且 state 属 status.verification.states")
    if not set(decl["verifiable_states"]) <= set(states):
        raise SchemaLoadError("verbs.lybra_roadmap.verify.verifiable_states 须属 status.verification.states")
    fix = decl["fix_card"]
    if (not isinstance(fix, dict) or not all(isinstance(fix.get(k), str) and fix[k] for k in _FIX_CARD_TEXT_KEYS)
            or "{round}" not in fix["task_id_template"] or not isinstance(fix.get("inherit_fields"), list)):
        raise SchemaLoadError(f"verbs.lybra_roadmap.verify.fix_card(task_id_template 含 {{round}} / {' / '.join(_FIX_CARD_TEXT_KEYS[1:])} / "
                              "inherit_fields)未声明齐")
    texts = decl["texts"]
    if not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k] for k in _VERIFY_TEXT_KEYS):
        raise SchemaLoadError(f"verbs.lybra_roadmap.verify.texts 未声明齐 {list(_VERIFY_TEXT_KEYS)}")
    return decl


def _verify_reject(code: str, detail: str, decl: dict[str, Any]) -> RoadmapError:
    hint = str((decl.get("reject_codes") or {}).get(code) or "")
    return RoadmapError(code, f"{detail}({hint})" if hint else detail)


def _fix_task_id(governance_root: Path, source_task_id: str, decl: dict[str, Any], template: str | None = None) -> str:
    """修复卡 / 承接卡卡号: 模板序号取首个未占用(队列 + 草稿, draft_validator.find_task_id_collisions 唯一判定)。
    template 缺省 = verify.fix_card.task_id_template; AIPOS-F207 queue correct 传 queue_mutations.correct.followup.task_id_template。"""
    from tools.aipos_cli.draft_validator import find_task_id_collisions

    template = str(template or decl["fix_card"]["task_id_template"])
    for round_no in range(1, 1000):
        candidate = template.format(task_id=source_task_id, round=round_no)
        if not find_task_id_collisions(governance_root, candidate):
            return candidate
    raise ValueError(f"修复卡卡号 {template} 序号 1..999 都已占用")


def _rebind_task_segments(value: Any, source_task_id: str, new_task_id: str) -> Any:
    """AIPOS-F207: 路径值(或列表元素)按 / 分段, 等于原卡号的段换成新卡号(只写治理根卡的专属落点); 其余原样。"""
    if isinstance(value, list):
        return [_rebind_task_segments(v, source_task_id, new_task_id) for v in value]
    if isinstance(value, str) and source_task_id:
        return "/".join(new_task_id if part == source_task_id else part for part in value.split("/"))
    return value


def followup_draft_payload(governance_root: Path, source_meta: dict[str, Any], *, template: dict[str, Any], task_id: str,
                           milestone: str | None, criteria: list[str], ref_field: str, ref_value: str, created_by: str,
                           title: str | None = None, fill: dict[str, Any] | None = None,
                           rebind_fields: list[str] | None = None) -> tuple[dict[str, Any], str]:
    """承接卡草稿唯一起草口(AIPOS-F175 修复卡 / AIPOS-F207 Owner 纠正承接卡共用)。承继 template.inherit_fields, 外加承继来的
    task_class 声明的必填字段(enums.schema task_class required_fields, 经 task_complexity.task_class_declarations 读; 如 complex 的
    planner_agent / reviewer)。AIPOS-F180C: 承继的 artifacts 只留挂在 criteria 上的项(非映射项原样保留交形校验 artifact_problems 判,
    不放松), 过滤后为空 = 不带 artifacts、正文提示补声明(template.artifacts_missing); 正文附原卡这些标准的产物文件。
    rebind_fields(AIPOS-F207)= 键或「键.子键」, 值中等于原卡号的路径段换成新卡号。milestone 缺 / criteria 空 = 不带挂接字段。
    正文 = template.body_template, 占位 = fill + {task_id, criterion, source_task_id, artifact_dir, evidence, artifacts_note}。"""
    from tools.aipos_cli.task_complexity import effective_task_class, task_class_declarations

    field = artifacts_declaration()["field"]
    source_id = str(source_meta.get("task_id") or "")
    inherit = list(template["inherit_fields"])
    if "task_class" in inherit and not _missing(source_meta.get("task_class")):
        class_decl = task_class_declarations().get(effective_task_class(source_meta)) or {}
        inherit += [k for k in class_decl.get("required_fields") or [] if k not in inherit]
    meta: dict[str, Any] = {k: copy.deepcopy(source_meta[k]) for k in inherit if not _missing(source_meta.get(k))}
    if isinstance(meta.get(field), list):
        kept = [e for e in meta[field] if not isinstance(e, dict) or e.get("criterion") in criteria]
        if kept:
            meta[field] = kept
        else:
            meta.pop(field)
    for key in rebind_fields or []:
        head, _, sub = str(key).partition(".")
        if sub and isinstance(meta.get(head), dict) and sub in meta[head]:
            meta[head][sub] = _rebind_task_segments(meta[head][sub], source_id, task_id)
        elif not sub and head in meta:
            meta[head] = _rebind_task_segments(meta[head], source_id, task_id)
    meta.update({"task_id": task_id, "title": title or "", "status": "pending", "created_by": created_by, ref_field: ref_value})
    if milestone and criteria:
        meta.update({"milestone": milestone, "criteria": list(criteria)})
    files = [item["rel"] for criterion in criteria
             for entry in card_artifacts(governance_root, source_meta, criterion=criterion)["entries"] for item in entry["files"]]
    shown = ", ".join(criteria)
    body = template["body_template"].format(**{
        **(fill or {}), "task_id": task_id, "criterion": shown, "source_task_id": source_id,
        "artifact_dir": _rel(card_artifact_dir(governance_root, source_id), governance_root),
        "evidence": ", ".join(files) or template["evidence_none"],
        "artifacts_note": "" if field in meta or not criteria else template["artifacts_missing"].format(criterion=shown)})
    return meta, body


def _fix_draft_payload(governance_root: Path, source_meta: dict[str, Any], *, fix_task_id: str, milestone: str, criterion: str,
                       statement: str, record_id: str, record_rel: str, relayed_by: str, owner_words: str, note: str | None,
                       decl: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """修复卡草稿(AIPOS-F175; 起草实现 = followup_draft_payload, 模板 verify.fix_card, owner_check_ref = 本次抽查记录 id)。"""
    fix = decl["fix_card"]
    return followup_draft_payload(
        governance_root, source_meta, template=fix, task_id=fix_task_id, milestone=milestone, criteria=[criterion],
        ref_field="owner_check_ref", ref_value=record_id, created_by=relayed_by,
        title=fix["title_template"].format(criterion=criterion, statement=statement),
        fill={"statement": statement, "record": record_rel, "relayed_by": relayed_by, "owner_words": owner_words, "note": note or "-"})


def apply_owner_verification(governance_root: str | Path, *, criterion: str, result: str, relayed_by: str | None,
                             owner_words: str | None, note: str | None = None, card: str | None = None, actor: str = "owner",
                             confirm: bool, project: str, push: bool = True) -> dict[str, Any]:
    """Owner 抽查结论两阶段写(唯一实现)。转达人 / 原话缺 = owner_inbox.InboxUsageError(用法错, 与 owner decide 同一校验)。
    预演: 推导该标准状态(roadmap_drift.roadmap_status), 核前置, 渲染将写的 owner_verifications 记录(writer dry_run)与不符时的修复卡草稿
    (draft_writer.create_draft dry_run), 零写入。确认: 写记录 → (不符)写草稿(owner_check_ref = 本次记录 id)→ land_written_paths 落账。
    拒 = RoadmapError(code ∈ verify.reject_codes)。返回 {dry_run, written, criterion, milestone, task_id, result, label, state_before,
    state_after, record{path, record_id, rendered}, fix{task_id, path, rendered, publish_hint} | None, fix_error, fix_manual, landing}。
    AIPOS-F180C 不连坐: 修复卡起草被拒不拦抽查记录——预演照常渲染记录, fix = None、fix_error = 拒因、fix_manual = 手工出卡指引;
    确认先写记录, 起草预演已被拒则不写草稿, 记录照样落账(调用方见 fix_error 非零退出); 记录 fix_task_id 仍为预留卡号(标准状态「待出」)。"""
    from tools.aipos_cli.draft_writer import create_draft
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.owner_inbox import relay_fields
    from tools.aipos_cli.owner_verification_writer import build_owner_verification_record
    from tools.aipos_cli.roadmap_drift import roadmap_status, status_declaration
    from tools.aipos_cli.task_loader import find_task_card
    from tools.schema_constants import Verdict

    root = Path(governance_root)
    relayed, words = relay_fields(relayed_by, owner_words)
    decl = verify_declaration()
    if result not in decl["results"]:
        raise ValueError(f"--result 只认 {sorted(decl['results'])}(得 {result!r})")
    spec = decl["results"][result]
    states = status_declaration()["verification"]["states"]
    view = roadmap_status(root, project=project)
    if not view["exists"]:
        raise _verify_reject("VERIFY_CRITERION_UNKNOWN", f"项目 {project} 未声明路线图({view['path']} 不存在)", decl)
    found = next(((ms, row) for ms in view["milestones"] for row in ms["criteria"] if row["id"] == criterion), None)
    if found is None:
        raise _verify_reject("VERIFY_CRITERION_UNKNOWN", f"标准 {criterion!r} 不在路线图 {view['rel']}", decl)
    ms, row = found
    if row["verify"] != "owner_check":
        raise _verify_reject("VERIFY_NOT_OWNER_CHECK", f"标准 {criterion} 的 verify = {row['verify']}", decl)
    if row["state"] not in decl["verifiable_states"]:
        raise _verify_reject("VERIFY_NOT_READY", f"标准 {criterion} 当前「{row['state_label']}」: {row.get('reason') or '-'}", decl)
    target = str(card or row["verify_target"] or "")
    if card and card not in row["qualified_cards"]:
        raise _verify_reject("VERIFY_CARD_INVALID", f"--card {card} 不在标准 {criterion} 已自动验证通过的挂接卡 {row['qualified_cards']}", decl)
    fix_task_id = None
    source_meta: dict[str, Any] = {}
    if result == "mismatch":
        card_path, _state = find_task_card(root, target)
        if card_path is None:
            raise _verify_reject("VERIFY_CARD_INVALID", f"被抽查卡 {target} 在队列中找不到", decl)
        source_meta, _body = require_frontmatter(card_path)
        fix_task_id = _fix_task_id(root, target, decl)
    payload = {"task_id": target, "decision": spec["decision"], "reason": note or words, "decided_via": "cli", "workspace": project,
               "milestone": str(ms["id"]), "criterion": criterion, "result": result, "relayed_by": relayed, "owner_words": words,
               "note": note, "fix_task_id": fix_task_id}
    record = build_owner_verification_record(root, payload, actor=actor, dry_run=True)
    out: dict[str, Any] = {"dry_run": not confirm, "written": False, "criterion": criterion, "statement": row["statement"],
                           "milestone": ms["id"], "task_id": target, "result": result, "label": spec["label"],
                           "relayed_by": relayed, "owner_words": words, "state_before": row["state_label"],
                           "state_after": states[spec["state"]], "record": None, "fix": None, "fix_error": None,
                           "fix_task_id": fix_task_id, "fix_manual": None, "landing": None}
    if record["verdict"] == Verdict.BLOCK:
        raise _verify_reject("VERIFY_RECORD_BLOCKED", "; ".join(record["blocking_reasons"]), decl)

    def _record_view(res: dict[str, Any]) -> dict[str, Any]:
        rel = str(res["target_path"])
        return {"path": rel, "record_id": Path(rel).stem, "rendered": res["rendered_markdown"], "warnings": res["warnings"]}

    def _plan_fix(record_view: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        meta, body = _fix_draft_payload(root, source_meta, fix_task_id=str(fix_task_id), milestone=str(ms["id"]), criterion=criterion,
                                        statement=str(row["statement"]), record_id=record_view["record_id"], record_rel=record_view["path"],
                                        relayed_by=relayed, owner_words=words, note=note, decl=decl)
        res = create_draft(root, meta, body, dry_run=dry_run)
        if res["verdict"] == Verdict.BLOCK:
            raise _verify_reject("VERIFY_FIX_DRAFT_BLOCKED", "; ".join(res["blocking_reasons"]), decl)
        path = str(res.get("target_path") or "")
        return {"task_id": fix_task_id, "path": path, "rendered": res.get("rendered_markdown"), "warnings": res.get("warnings") or [],
                "wrote": bool(res.get("wrote")),
                "publish_hint": decl["fix_card"]["publish_hint"].format(path=path, governance_root=str(root))}

    def _manual(record_view: dict[str, Any]) -> str:
        return decl["fix_card"]["manual_hint"].format(task_id=fix_task_id, milestone=ms["id"], criterion=criterion,
                                                      record_id=record_view["record_id"], source_task_id=target,
                                                      governance_root=str(root))

    def _try_fix(record_view: dict[str, Any], *, dry_run: bool) -> None:
        try:
            out["fix"], out["fix_error"], out["fix_manual"] = _plan_fix(record_view, dry_run=dry_run), None, None
        except RoadmapError as exc:  # draft refused: reported to the caller (non-zero), never rolls back the owner record
            out["fix"], out["fix_error"], out["fix_manual"] = None, str(exc), _manual(record_view)

    out["record"] = _record_view(record)
    if fix_task_id:
        _try_fix(out["record"], dry_run=True)
    if not confirm:
        return out
    written_record = build_owner_verification_record(root, payload, actor=actor, dry_run=False)
    if written_record["verdict"] == Verdict.BLOCK or not written_record.get("wrote"):
        raise _verify_reject("VERIFY_RECORD_BLOCKED", "; ".join(written_record["blocking_reasons"]) or "记录未写出", decl)
    out["record"] = _record_view(written_record)
    out["written"] = True
    written = [out["record"]["path"]]
    if fix_task_id and out["fix_error"] is None:
        _try_fix(out["record"], dry_run=False)
        if out["fix"] is not None:
            written.append(out["fix"]["path"])
    elif out["fix_error"] is not None:  # preview already refused: no draft write; re-point the hint at the written record id
        out["fix_manual"] = _manual(out["record"])
    from tools.aipos_cli.governance_commit import land_written_paths

    out["landing"] = land_written_paths(root, written, actor, command="roadmap_writers", push=push, subcommand="verify",
                                        target=criterion, project=project)
    after = roadmap_status(root, project=project)
    row_after = next((r for m in after["milestones"] for r in m["criteria"] if r["id"] == criterion), None)
    out["state_after"] = row_after["state_label"] if row_after else out["state_after"]
    return out


def render_verify_text(outcome: dict[str, Any], *, project: str, source: str, mode: str) -> list[str]:
    """`lybra roadmap verify` 文本(文案读 verbs.lybra_roadmap.verify.texts)。"""
    texts = verify_declaration()["texts"]
    will = "" if outcome["written"] else texts["will"]
    lines = [texts["header"].format(project=project, source=source, criterion=outcome["criterion"], statement=outcome["statement"],
                                    task_id=outcome["task_id"]),
             texts["outcome"].format(criterion=outcome["criterion"], mode=mode),
             texts["record"].format(will=will, path=outcome["record"]["path"], label=outcome["label"], relayed_by=outcome["relayed_by"],
                                    owner_words=outcome["owner_words"]),
             texts["state"].format(before=outcome["state_before"], after=outcome["state_after"])]
    fix = outcome.get("fix")
    if fix:
        lines.append(texts["fix"].format(will=will, task_id=fix["task_id"], path=fix["path"], milestone=outcome["milestone"],
                                         criterion=outcome["criterion"], record_id=outcome["record"]["record_id"]))
        lines.append("  " + fix["publish_hint"])
    elif outcome.get("fix_error"):  # AIPOS-F180C: draft refused, owner record kept; say why and how to draft by hand
        lines.append(texts["fix_blocked"].format(will=will, task_id=outcome["fix_task_id"], reason=outcome["fix_error"],
                                                 record_state=verify_record_state(outcome)))
        lines.append("  " + str(outcome.get("fix_manual") or ""))
    return lines


def verify_record_state(outcome: dict[str, Any]) -> str:
    """AIPOS-F180C: 修复卡起草被拒时抽查记录的处境文案(预演 = 照常写入; 确认 = 已写入并落账)。"""
    texts = verify_declaration()["texts"]
    return texts["record_kept_written"] if outcome["written"] else texts["record_kept_preview"]


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
