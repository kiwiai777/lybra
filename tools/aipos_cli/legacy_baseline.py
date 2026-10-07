"""AIPOS-F122: 接入既有项目①——存量冻结(迁移基线)。

人肉 gate 时期留下的卡没有门记录(claimed 无 claim、completed 无 closure、记录无 frontmatter …): 全项目 state lint 报几百条断层,
next 项目扫描把它们当待推进。本模块给产品一个只追加的冻结清单——一次声明哪些卡是历史:

  声明  config.schema configuration_sources.project_json.schema.legacy_baseline(落点 manifest_dir + 条目形 + 拒因码)。
        project.json 缺段 = 无冻结, 行为不变。落点唯一读取口 workspace_config.project_legacy_baseline。
  清单  manifest_dir 下每批一条带 record_type 的记录文件(render_markdown 单源渲染, 排他创建 = 只追加)。
        解冻 = 追加 action=unfreeze 条目, 不删不改既有条目。
  判定  frozen_tasks(唯一「是否冻结」实现)——state lint 全项目扫描 / next scan_project / 门写动作(claim/return/dispatch/
        verdict/finalize)/ lybra loop --task-id 共用; 门与 loop 的拒因经 frozen_rejection(LEGACY_FROZEN + 解冻命令)。
  命令  `lybra project freeze-legacy`(legacy_freeze.plan_freeze_legacy → 干跑 / write_freeze_entry → 落条目); 不移动、不改写任何卡文件与记录。

fail-closed: 清单条目读不出 / 形不合 = LegacyBaselineError(LEGACY_BASELINE_INVALID), 调用方不得当「无冻结」继续隐藏或放行。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

LEGACY_FROZEN = "LEGACY_FROZEN"
LEGACY_UNKNOWN_TASK = "LEGACY_UNKNOWN_TASK"
LEGACY_BASELINE_INVALID = "LEGACY_BASELINE_INVALID"
ACTION_FREEZE = "freeze"
ACTION_UNFREEZE = "unfreeze"


class LegacyBaselineError(ValueError):
    """存量冻结声明 / 清单 / 命令参数不可用(fail-closed)。code ∈ config.schema project_json.legacy_baseline.reject_codes。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.reason = message


def declaration() -> dict[str, Any]:
    """config.schema project_json.schema.legacy_baseline(schema / entry / reject_codes / unfreeze_command_template)。
    缺 / 形不合 = SchemaLoadError(fail-closed, 代码不写第二份形)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (((load_schema("config").get("configuration_sources") or {}).get("project_json") or {}).get("schema") or {}).get("legacy_baseline")
    entry = decl.get("entry") if isinstance(decl, dict) else None
    ok = (
        isinstance(decl, dict)
        and isinstance(decl.get("schema"), dict)
        and isinstance(decl.get("reject_codes"), dict)
        and all(code in decl["reject_codes"] for code in (LEGACY_FROZEN, LEGACY_UNKNOWN_TASK, LEGACY_BASELINE_INVALID))
        and isinstance(decl.get("unfreeze_command_template"), str)
        and isinstance(entry, dict)
        and isinstance(entry.get("record_type"), str)
        and isinstance(entry.get("actions"), dict)
        and all(isinstance((entry["actions"].get(a) or {}).get("at_field"), str) for a in (ACTION_FREEZE, ACTION_UNFREEZE))
        and isinstance(entry.get("required_fields"), list)
        and isinstance(entry.get("filename_template"), str)
    )
    if not ok:
        raise SchemaLoadError(
            "config.schema.json configuration_sources.project_json.schema.legacy_baseline"
            "(schema / entry{record_type, actions.freeze|unfreeze.at_field, required_fields, filename_template} / "
            "reject_codes{LEGACY_FROZEN, LEGACY_UNKNOWN_TASK, LEGACY_BASELINE_INVALID} / unfreeze_command_template)未声明"
        )
    return decl


def unfreeze_command(governance_root: Path, task_ids: list[str]) -> str:
    """解冻出口命令(模板读声明 unfreeze_command_template)。"""
    template = declaration()["unfreeze_command_template"]
    return template.format(governance_root=str(Path(governance_root)), task_ids=" ".join(task_ids))


def _read_entry(path: Path, decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    entry_decl = decl["entry"]
    try:
        meta, _body = require_frontmatter(path)
    except FrontmatterReadError as exc:
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"清单条目读不出: {exc}") from exc
    if str(meta.get("record_type") or "") != entry_decl["record_type"]:
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID,
                                  f"{path}: record_type={meta.get('record_type')!r} ≠ 声明 {entry_decl['record_type']!r}")
    action = str(meta.get("action") or "")
    action_decl = entry_decl["actions"].get(action)
    if not isinstance(action_decl, dict):
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{path}: action={action!r} 不在声明 {sorted(entry_decl['actions'])}")
    missing = [f for f in list(entry_decl["required_fields"]) + [action_decl["at_field"], action_decl.get("by_field")]
               if f and meta.get(f) in (None, "", [])]
    if missing:
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{path}: 缺必填字段 {missing}")
    task_ids = meta.get("task_ids")
    if not isinstance(task_ids, list) or not all(isinstance(t, str) and t.strip() for t in task_ids):
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{path}: task_ids 须为非空卡号列表")
    return {
        "path": path,
        "action": action,
        "at": str(meta[action_decl["at_field"]]),
        "by": str(meta.get(action_decl.get("by_field") or "") or ""),
        "batch_id": str(meta["batch_id"]),
        "reason": str(meta["reason"]),
        "owner_policy_ref": meta.get("owner_policy_ref"),
        "task_ids": [t.strip().upper() for t in task_ids],
        "tasks": meta.get("tasks") if isinstance(meta.get("tasks"), list) else [],
    }


def manifest_location(governance_root: Path) -> Path | None:
    """清单目录(未声明 = None)。落点读 workspace_config.project_legacy_baseline(声明形不合 = LegacyBaselineError)。"""
    from tools.aipos_cli.workspace_config import project_legacy_baseline

    declared = project_legacy_baseline(governance_root)
    return None if declared is None else declared["manifest_dir"]


def read_manifest(governance_root: Path) -> list[dict[str, Any]]:
    """清单全部条目, 按 (条目时间, 文件名) 排序。未声明 / 目录不在 = []。任一条目不合 = LegacyBaselineError。"""
    directory = manifest_location(governance_root)
    if directory is None or not directory.is_dir():
        return []
    decl = declaration()
    entries = [_read_entry(path, decl) for path in sorted(directory.glob("*.md"))]
    return sorted(entries, key=lambda e: (e["at"], e["path"].name))


def frozen_tasks(governance_root: Path) -> dict[str, dict[str, Any]]:
    """AIPOS-F122 件③: 「是否冻结」唯一判定——重放清单条目, 返回 {卡号(大写): 冻结它的那批条目摘要}。
    项目未声明 legacy_baseline = {}(行为不变)。清单不合 = LegacyBaselineError(调用方 fail-closed)。"""
    frozen: dict[str, dict[str, Any]] = {}
    for entry in read_manifest(Path(governance_root)):
        for task_id in entry["task_ids"]:
            if entry["action"] == ACTION_FREEZE:
                frozen[task_id] = {"batch_id": entry["batch_id"], "frozen_at": entry["at"], "frozen_by": entry["by"],
                                   "reason": entry["reason"], "manifest_entry": str(entry["path"])}
            else:
                frozen.pop(task_id, None)
    return frozen


def frozen_rejection(governance_root: Path, task_ids: list[str | None], *, action: str) -> dict[str, Any] | None:
    """门写动作 / loop 的拒因(None = 无一冻结, 放行)。task_ids 中任一冻结 → {code: LEGACY_FROZEN, message(含解冻命令), ...}。
    清单不合 = LegacyBaselineError 原样抛(调用方拒写, 不放行)。"""
    wanted = [str(t).strip().upper() for t in task_ids if t and str(t).strip()]
    if not wanted:
        return None
    frozen = frozen_tasks(Path(governance_root))
    hits = [t for t in dict.fromkeys(wanted) if t in frozen]
    if not hits:
        return None
    command = unfreeze_command(governance_root, hits)
    batches = "; ".join(f"{t} ∈ {frozen[t]['batch_id']}({frozen[t]['reason']})" for t in hits)
    return {
        "code": LEGACY_FROZEN,
        "task_ids": hits,
        "batches": {t: frozen[t] for t in hits},
        "unfreeze_command": command,
        "message": (f"{LEGACY_FROZEN}: 卡 {', '.join(hits)} 在存量冻结清单内(历史, {batches}), 拒 {action}。"
                    f"确需推进先解冻: {command}"),
    }


# ---------------------------------------------------------------------------
# 命令侧: 落条目(不改任何卡文件与记录)。选卡干跑在 legacy_freeze.plan_freeze_legacy(依赖 state lint 计数, 不进本模块:
# 本模块被 workspace_config 懒导入, 在 finalize 合并后预载闭包内, 须保持轻依赖)
# ---------------------------------------------------------------------------

def default_manifest_dir(governance_root: Path) -> Path:
    """未声明时 --confirm 要写入 project.json 的落点(声明 schema.manifest_dir.default, 相对治理根)。"""
    default = ((declaration()["schema"].get("manifest_dir") or {}).get("default"))
    if not isinstance(default, str) or not default.strip():
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("config.schema project_json.legacy_baseline.schema.manifest_dir.default 未声明")
    return Path(governance_root) / default


def write_freeze_entry(
    governance_root: Path,
    plan: dict[str, Any],
    *,
    reason: str,
    actor: str,
    owner_policy_ref: str | None = None,
) -> dict[str, Any]:
    """按干跑 plan 追加一条清单条目(本批 selected 为空 = 不写, 幂等)。未声明落点则先在 project.json 声明缺省落点。
    条目文件排他创建(同名即拒, 不覆盖); 写后回读须能被 read_manifest 读出, 否则删除本条并抛(不留半成品)。"""
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.record_writer import actor_slug, render_markdown
    from tools.aipos_cli.workspace_config import project_legacy_baseline, set_project_legacy_baseline

    root = Path(governance_root)
    reason_text = str(reason or "").strip()
    actor_text = str(actor or "").strip()
    if not reason_text or not actor_text:
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, "--reason 与 --actor 必填(清单条目必填 reason / 冻结人)")
    if not plan["selected"]:
        return {"written": False, "entry": None, "project_json_declared": False}
    decl = declaration()
    entry_decl = decl["entry"]
    action = plan["action"]
    action_decl = entry_decl["actions"][action]
    declared_now = False
    if project_legacy_baseline(root) is None:
        rel = default_manifest_dir(root).relative_to(root).as_posix()
        set_project_legacy_baseline(root, rel)
        declared_now = True
    directory = manifest_location(root)
    if directory is None:  # 上一步刚声明却读不回 = 声明写入失败(fail-closed)
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{root}/project.json legacy_baseline 声明写入后读不回")
    timestamp = iso_z()
    slug = file_slug(entry_decl.get("timestamp_style") or "compact", timestamp)
    name = entry_decl["filename_template"].format(action=action, timestamp=slug, actor=actor_slug(actor_text))
    batch_id = name[:-3] if name.endswith(".md") else name
    task_ids = [item["task_id"] for item in plan["selected"]]
    metadata: dict[str, Any] = {
        "record_type": entry_decl["record_type"],
        "action": action,
        "batch_id": batch_id,
        action_decl["at_field"]: timestamp,
        action_decl["by_field"]: actor_text,
        "reason": reason_text,
        "owner_policy_ref": str(owner_policy_ref).strip() if owner_policy_ref and str(owner_policy_ref).strip() else None,
        "task_ids": task_ids,
        "tasks": [{"task_id": i["task_id"], "queue_state": i["queue_state"], "card_status": i["card_status"]} for i in plan["selected"]],
    }
    order = ["record_type", "action", "batch_id", action_decl["at_field"], action_decl["by_field"], "reason",
             "owner_policy_ref", "task_ids", "tasks"]
    verb = "冻结为历史" if action == ACTION_FREEZE else "解冻"
    body = "\n".join([
        f"# Legacy baseline: {action} {len(task_ids)} 张卡",
        "",
        f"由 `lybra project freeze-legacy` 写入(只追加清单条目); 本批卡{verb}。卡文件与记录未被移动或改写。",
        "",
        f"- 理由: {reason_text}",
        "",
    ])
    markdown = render_markdown(metadata, body, order)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with open(path, "x", encoding="utf-8") as handle:  # 排他创建: 只追加, 同名即拒(FileExistsError 原样上抛)
        handle.write(markdown)
    try:
        read_manifest(root)
    except LegacyBaselineError:
        path.unlink()
        raise
    return {"written": True, "entry": str(path), "batch_id": batch_id, "project_json_declared": declared_now,
            "task_ids": task_ids}


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
