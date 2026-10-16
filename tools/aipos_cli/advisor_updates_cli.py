"""AIPOS-F167 件③ — 产品内更新通知的送达面(唯一实现): 查看实例解析、四出口提示挂载、`lybra updates [--ack]`、已读标记写入与落账。

核心(声明 / 库读写 / 未读判定 / 提示渲染)在 advisor_updates; 本模块只做「谁在看」「怎么显示」「怎么记已读」:
- resolve_viewer: 查看实例 = 显式 --actor > 本治理根唯一已接入顾问实例 > 未知(按全部已接入顾问实例「任一未读」计)。
- advisor_view / advisor_banner_lines: charter 开头、loop status / next / brief 末尾同一渲染; 无未读 / 库不可用 = 不显示,
  读不出 = 一行告警(不静默, 不掀翻原命令)。
- ack_updates: 已读标记(records advisor_update_acks/<实例>/, 只追加)两阶段写入 + 经 governance_commit.land_written_paths 落账。
- run_updates_cli: `lybra updates` 薄壳(退出码读 verbs.schema lybra_updates.exit_codes)。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from tools.aipos_cli.advisor_updates import (
    VERB,
    AdvisorUpdatesError,
    acked_update_ids,
    audience_entries,
    banner_lines,
    load_updates,
    store_dir_for_running_code,
    unread_updates,
    updates_declaration,
    version_label,
)


def advisor_instances(governance_root: str | Path) -> list[str]:
    """本治理根已接入的、角色类 = 提示受众(banner.audience)的实例(接入判定唯一 enrollment.enrolled_instances; 角色类经
    custom_roles.resolve_role_to_class(land 事件 role)/ resolve_instance_role_class(仅连接文件接入者), 不按实例名前缀判)。"""
    from tools.aipos_cli.custom_roles import resolve_instance_role_class, resolve_role_to_class
    from tools.aipos_cli.enrollment import enrolled_instances

    audience = str(updates_declaration()["banner"]["audience"])
    gov = Path(governance_root)
    out = []
    for view in enrolled_instances(gov):
        cls = resolve_role_to_class(str(view["role"]), gov) if view.get("role") else None
        if cls is None:
            pair = resolve_instance_role_class(str(view["instance"]), gov)
            cls = pair[1] if pair else None
        if cls == audience:
            out.append(str(view["instance"]))
    return out


def resolve_viewer(governance_root: str | Path, actor: str | None = None) -> dict[str, Any]:
    """查看实例: {instance, source, candidates}。显式 actor > 本治理根唯一已接入顾问实例 > 未知(instance=None, candidates = 全部
    已接入顾问实例: 按「任一实例未读」计, 提示里用占位, 防多顾问项目里一人 ack 后别人看不到)。"""
    name = str(actor or "").strip()
    if name:
        return {"instance": name, "source": "--actor", "candidates": [name]}
    advisors = advisor_instances(governance_root)
    if len(advisors) == 1:
        return {"instance": advisors[0], "source": "本治理根唯一已接入顾问实例", "candidates": advisors}
    why = (f"本治理根接入 {len(advisors)} 个顾问实例({', '.join(advisors)}), 未给 --actor: 按任一实例未读计" if advisors
           else "本治理根无已接入顾问实例, 未给 --actor: 按全部未读计")
    return {"instance": None, "source": why, "candidates": advisors}


def _acked_by_viewer(governance_root: str | Path, viewer: dict[str, Any]) -> set[str]:
    """查看者的已读集合: 确定实例 = 其已读集合; 未知 = 全部候选实例已读集合的交集(无候选 = 空集)。"""
    sets = [acked_update_ids(governance_root, inst) for inst in viewer["candidates"]]
    return set.intersection(*sets) if sets else set()


def advisor_view(governance_root: str | Path, actor: str | None = None, *, running: Any = None,
                 skip_viewer_when_empty: bool = False) -> dict[str, Any]:
    """{available, reason, store, mode, viewer, updates, acked, unread}; 库 / 已读标记读不出 = AdvisorUpdatesError 上抛。
    库不可用 = 不读治理根(不解析查看者); skip_viewer_when_empty = 库内无提示受众条目时也不解析(四出口提示走此路, 无事零开销)。"""
    located = store_dir_for_running_code(running)
    base = {"available": located["available"], "reason": located["reason"], "mode": located["mode"],
            "store": str(located["store"]) if located["store"] else None,
            "viewer": {"instance": str(actor or "").strip() or None, "source": "未解析(库不可用或无更新)", "candidates": []},
            "updates": [], "acked": [], "unread": []}
    if not located["available"]:
        return base
    updates = load_updates(located["store"])
    if skip_viewer_when_empty and not any(audience_entries(r) for r in updates):
        return {**base, "updates": updates}
    viewer = resolve_viewer(governance_root, actor)
    acked = _acked_by_viewer(governance_root, viewer)
    return {**base, "viewer": viewer, "updates": updates, "acked": sorted(acked), "unread": unread_updates(updates, acked)}


def advisor_banner_lines(governance_root: str | Path, actor: str | None = None, *, running: Any = None) -> list[str]:
    """四出口(charter / loop status / next / brief)同一提示。无未读 / 库不可用 = [](行为不变); 读不出 = 一行告警(出口原文)。"""
    from tools.aipos_cli.custom_roles import RoleRegistryReadError
    from tools.schema_loader import SchemaLoadError

    try:
        view = advisor_view(governance_root, actor, running=running, skip_viewer_when_empty=True)
        return banner_lines(view["unread"], governance_root=governance_root, instance=view["viewer"]["instance"])
    except (AdvisorUpdatesError, SchemaLoadError, RoleRegistryReadError, ValueError, OSError, RuntimeError) as exc:
        try:
            template = str(updates_declaration()["banner"]["unreadable"])
        except SchemaLoadError:
            template = "⚠ Lybra 更新说明读不出: {reason}(verbs.schema lybra_updates.banner 亦读不出); 治理根 {governance_root}"
        return [template.format(reason=f"{type(exc).__name__}: {exc}", governance_root=governance_root)]


# ---------------------------------------------------------------------------
# 已读标记(两阶段 + 落账)
# ---------------------------------------------------------------------------

class AckRefused(ValueError):
    """--ack 拒(缺 --actor / 实例未接入 / 库不可用), 消息带出口。"""


def ack_updates(governance_root: str | Path, instance: str, *, confirm: bool, running: Any = None) -> dict[str, Any]:
    """把该实例当前未读的更新说明全部记已读。预演(confirm=False)零写入; 确认 = 写一份已读标记记录并落账。
    返回 {instance, record, update_ids, versions, wrote, landing}; 拒 = AckRefused。"""
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.aipos_cli.record_writer import record_dir, record_location, render_markdown

    gov = Path(governance_root)
    name = str(instance or "").strip()
    if not name:
        raise AckRefused("--ack 须给 --actor <你的顾问实例>(已读按本治理根本实例记); 未写入")
    enrolled = instance_enrollment(gov, name)
    if not enrolled["enrolled"]:
        raise AckRefused(f"实例 {name} 未在本治理根接入: {enrolled['reason']}; 出口: 核对实例名"
                         "(lybra roles enroll-where --instance <实例>)")
    view = advisor_view(gov, name, running=running)
    if not view["available"]:
        raise AckRefused(f"更新说明库不可用: {view['reason']}; 未写入")
    decl = updates_declaration()["ack"]
    unread = view["unread"]
    out: dict[str, Any] = {"instance": name, "update_ids": [str(r["update_id"]) for r in unread],
                           "versions": [version_label(r) for r in unread], "record": None, "wrote": False, "landing": None,
                           "confirm": bool(confirm)}
    if not unread:
        return out
    now = iso_z()
    style = "compact"
    kind = str(decl["record_kind"])
    filename = Path(record_location(kind)).name.format(agent_instance=name, timestamp=file_slug(style, now))
    path = record_dir(gov, kind, name) / filename
    out["record"] = path.relative_to(gov).as_posix()
    if not confirm:
        return out
    if path.exists():
        raise AckRefused(f"{out['record']} 已存在(同秒), 只追加不覆盖; 出口: 稍后重跑")
    meta = {"record_type": str(decl["record_type"]), "ack_id": path.stem, "agent_instance": name, "acked_at": now,
            "acked_update_ids": out["update_ids"], "acked_versions": out["versions"]}
    body = "# Lybra 更新已读标记\n\n" + "\n".join(
        f"- {r['update_id']}(生效版本 {version_label(r)}): " + "; ".join(f"[{e['task_id']}] {e['summary']}" for e in audience_entries(r))
        for r in unread) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(meta, body), encoding="utf-8")
    out["wrote"] = True
    from tools.aipos_cli.governance_commit import land_written_paths

    out["landing"] = land_written_paths(gov, [out["record"]], name, command=str(decl["landing_command"]), instance=name,
                                        count=len(unread))
    return out


# ---------------------------------------------------------------------------
# CLI 薄壳
# ---------------------------------------------------------------------------

def _render_update(record: dict[str, Any], *, read: bool | None) -> list[str]:
    mark = "" if read is None else ("[已读] " if read else "[未读] ")
    lines = [f"── {mark}{record['update_id']} · 生效版本 {version_label(record)} · 部署于 {record['deployed_at']}"
             f" · 汇总自 {record['source_project']} {record['source_task_id']}"]
    for entry in record["entries"]:
        lines.append(f"  [{entry['task_id']}] {entry['summary']}(面向: {', '.join(entry['audience'])})")
        for i, action in enumerate(entry.get("actions") or [], 1):
            lines.append(f"      顾问动作 {i}. {action}")
    return lines


def run_updates_cli(args: Any) -> int:
    """`lybra updates [--actor] [--all] [--json]` / `lybra updates --ack --actor <实例> [--dry-run|--confirm]`。"""
    import json

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args, _two_phase_family_declaration
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    ack = bool(getattr(args, "ack", False))
    if not ack and (getattr(args, "confirm", False) or getattr(args, "dry_run", False)):
        print("lybra updates: --dry-run / --confirm 只与 --ack 同用(查看本身只读)", file=sys.stderr)
        return declared_exit_code(VERB, "usage")
    try:
        gov = Path(_find_repo_root_for_args(args))
        if ack:
            result = ack_updates(gov, getattr(args, "actor", None) or "", confirm=bool(getattr(args, "confirm", False)))
        else:
            view = advisor_view(gov, getattr(args, "actor", None))
    except AckRefused as exc:
        print(f"lybra updates: 拒: {exc}", file=sys.stderr)
        return declared_exit_code(VERB, "refused")
    except (AdvisorUpdatesError, SchemaLoadError, FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        print(f"lybra updates: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(VERB, "unreadable")

    if ack:
        labels = _two_phase_family_declaration("advisor_update_writers")["outcome_labels"]
        landing = result["landing"]
        rc = declared_exit_code(VERB, "ok") if landing is None or landing["landed"] else declared_exit_code(VERB, "landing_failed")
        if getattr(args, "json", False):
            print(json.dumps({**result, "landing": {k: v for k, v in landing.items() if k != "line"} if landing else None},
                             ensure_ascii=False, indent=2))
            return rc
        outcome = labels["nothing"] if not result["update_ids"] else (labels["done"] if result["wrote"] else labels["preview"])
        print(f"lybra updates --ack {result['instance']}: {outcome} 治理根 {gov}")
        for uid, version in zip(result["update_ids"], result["versions"]):
            print(f"  {uid}(生效版本 {version})")
        if result["record"]:
            print(f"  {'wrote' if result['wrote'] else '将写'} {result['record']}")
        if landing is not None:
            print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
        return rc

    viewer = view["viewer"]
    if getattr(args, "json", False):
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return declared_exit_code(VERB, "ok")
    who = viewer["instance"] or updates_declaration()["banner"]["unknown_viewer"]
    print(f"Lybra 更新说明 · 治理根 {gov} · 顾问实例 {who}(来源: {viewer['source']})")
    if not view["available"]:
        print(f"  更新说明库不可用: {view['reason']}")
        return declared_exit_code(VERB, "ok")
    shown = view["updates"] if getattr(args, "all", False) else view["unread"]
    acked = set(view["acked"])
    print(f"  库 {view['store']}: 共 {len(view['updates'])} 份更新说明, 本实例未读 {len(view['unread'])} 份"
          + ("(--all 列全部)" if getattr(args, "all", False) else ""))
    for record in shown:
        for line in _render_update(record, read=(str(record["update_id"]) in acked) if getattr(args, "all", False) else None):
            print(line)
    if view["unread"]:
        for line in banner_lines(view["unread"], governance_root=gov, instance=viewer["instance"]):
            print(line)
    return declared_exit_code(VERB, "ok")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
