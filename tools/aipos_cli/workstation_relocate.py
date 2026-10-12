"""AIPOS-F161 — pi 工位迁出共用检出(F77 收窄重写): `lybra project relocate-workstation`。

一句话: 把一个 pi 工位(审计 / 执行)的工位态从当前落点(常见 = 与别的项目共用的 git 检出)整体迁到独立落点, 迁后分发 sync、
改位置声明并落账; 同一命令 --rollback 对称迁回。缺省预演(零写入), --confirm 才执行。

单一声明来源(不新增第二份路径表):
- 工位位置唯一来源 = 本项目 enrollment_log 中该实例最新的未作废位置事件(enrollment.workstation_location, AIPOS-F95);
  迁移 = 向同一日志追加一行 `relocate` 位置事件(enrollment.relocation_event_line 唯一渲染), 拉起(loop_driver.plan_launch)、
  check-workstation、enroll-where、`lybra charter` 全部经 workstation_location 读到新落点, 零改动。
- 迁移语义 / 退役指针名 / 建议落点模板 / 拒因码 = enums.schema workstation_transport.relocation(唯一读取口 relocation_declaration)。
- 分发 = distribution_sync.sync(与 `lybra sync` 同一实现; 章程按新落点重渲染, 清单写新工位父根 _distributed/.version-<角色>)。
- 落账 = aipos_cli._land_project_writes → governance_commit.land_written_paths(AIPOS-F158/F159 同一提交口)。

凭据: .lybra/ 下文件只随目录移动(rename; 跨文件系统 = shutil.move 保权限), 本命令只取路径 / 权限 / 大小(stat), 不读不印内容;
工位身份经既有 charter_render.workstation_identity(与 sync 同一读取口, 只取非秘密字段)。
本模块不执行任何 git 命令(检出判定只看祖先目录有无 .git 项), 不发任何信号。
"""
from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import stat
from pathlib import Path
from typing import Any

from tools.aipos_cli.clock import iso_z

_DECL_KEYS = ("event_action", "pointer_file", "suggested_target", "reject_codes", "steps")
_REJECT_CODES = ("RELOCATE_NOT_THIS_PROJECT", "RELOCATE_NO_LOCATION", "RELOCATE_REMOTE_UNSUPPORTED", "RELOCATE_IDENTITY_MISMATCH",
                 "RELOCATE_TARGET_REQUIRED", "RELOCATE_TARGET_INVALID", "RELOCATE_TARGET_IN_CHECKOUT", "RELOCATE_TARGET_NOT_EMPTY",
                 "RELOCATE_IN_FLIGHT", "RELOCATE_PROBE_UNAVAILABLE", "RELOCATE_NOTHING_TO_ROLLBACK", "RELOCATE_SYNC_FAILED",
                 "RELOCATE_MOVE_FAILED", "RELOCATE_HARNESS_UNSUPPORTED")


class RelocationRefused(ValueError):
    """迁移拒绝(code = 声明拒因码; problems = 逐条原因)。调用方 exit 1, 零写入(RELOCATE_SYNC_FAILED / MOVE_FAILED 已复原)。"""

    def __init__(self, code: str, problems: list[str]) -> None:
        self.code = code
        self.problems = list(problems)
        super().__init__(f"{code}: " + "; ".join(self.problems))


def relocation_declaration() -> dict[str, Any]:
    """enums.schema workstation_transport.relocation 唯一读取口。缺键 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.enrollment import RELOCATE_ACTION, workstation_transport_declaration
    from tools.schema_loader import SchemaLoadError

    decl = workstation_transport_declaration().get("relocation")
    codes = decl.get("reject_codes") if isinstance(decl, dict) else None
    tmpl = str((decl or {}).get("suggested_target") or "") if isinstance(decl, dict) else ""
    if (not isinstance(decl, dict) or any(not decl.get(k) for k in _DECL_KEYS)
            or not isinstance(codes, dict) or any(not isinstance(codes.get(c), str) or not codes[c] for c in _REJECT_CODES)
            or not all(f"{{{k}}}" in tmpl for k in ("base", "project", "name"))
            or "/" in str(decl.get("pointer_file")) or not isinstance(decl.get("steps"), list)
            or decl.get("event_action") != RELOCATE_ACTION):
        raise SchemaLoadError("enums.schema.json enums.workstation_transport.relocation(event_action / pointer_file / "
                              "suggested_target 含 {base} {project} {name} / steps / reject_codes 齐; "
                              f"event_action = {RELOCATE_ACTION}) "
                              f"{list(_REJECT_CODES)})未声明齐")
    return decl


def _refuse(decl: dict[str, Any], code: str, *problems: str) -> RelocationRefused:
    return RelocationRefused(code, [f"{decl['reject_codes'][code]} —— {p}" if p else decl["reject_codes"][code] for p in problems or ("",)])


def enclosing_checkout(path: Path) -> Path | None:
    """path(含自身)向上首个含 .git 项(目录或 gitdir 文件)的目录 = 所在 git 检出顶; 无 = None。只看文件系统, 不跑 git。"""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists() or (candidate / ".git").is_symlink():
            return candidate
    return None


def suggested_target(old_dir: Path, project: str, decl: dict[str, Any]) -> Path:
    """建议落点(声明 suggested_target 模板): base = 旧落点所在 git 检出顶的父目录(不在检出内 = 旧工位父根的父目录),
    name = 旧工位目录名。只是建议: --confirm 必须显式 --to。"""
    checkout = enclosing_checkout(old_dir.parent)
    base = (checkout or old_dir.parent).parent
    return Path(str(decl["suggested_target"]).format(base=base, project=project, name=old_dir.name))


# ---------------------------------------------------------------------------
# 在途判定(只读): 运行记录(loop_run_record 唯一判活 process_alive / judge_run)+ 本机进程 cwd 探活
# ---------------------------------------------------------------------------

def _within(path: str | Path | None, root: Path) -> bool:
    if not path:
        return False
    try:
        Path(str(path)).expanduser().resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _cwd_probe(root: Path) -> dict[str, Any]:
    """本机 /proc/<pid>/cwd 落在工位目录内的进程(手工开的 harness 会话等)。只读 readlink, 不发信号。
    无 /proc = available False(调用方 fail-closed 拒确认)。他用户进程读不到 cwd = 计数, 不当作「不在」。"""
    from tools.aipos_cli.loop_run_record import _proc_available

    out: dict[str, Any] = {"available": _proc_available(), "hits": [], "scanned": 0, "unreadable": 0}
    if not out["available"]:
        return out
    me = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        out["scanned"] += 1
        try:
            cwd = os.readlink(entry / "cwd")
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            out["unreadable"] += 1
            continue
        if _within(cwd, root):
            try:
                comm = (entry / "comm").read_text(encoding="utf-8").strip()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                comm = "?"
            out["hits"].append({"pid": int(entry.name), "comm": comm, "cwd": cwd})
    return out


def in_flight(project_root: Path, instance: str, workstation: Path) -> dict[str, Any]:
    """该实例 / 该工位目录有无在途工作(精确判定, 只读):
    ①运行记录中未收尾的拉起(launches[].ended_at 空)其 workstation 在本工位目录内且进程仍活(process_alive 启动时刻指纹; 记录写于
      他机 = 无从探活, 按在途计);
    ②运行记录中 loop 进程仍活(judge_run 状态 running / stalled / launch_dead; 他机记录 unprobeable 亦计)且当前步是等待步、
      等待目标卡的干活实例(loop_driver._wait_instance 唯一判定)= 本实例;
    ③本机进程 cwd 在工位目录内(无 /proc = probe_unavailable)。
    运行记录读不出 = 计入 problems(fail-closed)。返回 {findings, problems, probe}。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.loop_driver import _wait_instance, load_loop_contract
    from tools.aipos_cli.loop_run_record import (
        LoopRunRecordError,
        _run_files,
        judge_run,
        loop_runs_root,
        process_alive,
        read_run,
        run_record_declaration,
    )
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    findings: list[str] = []
    problems: list[str] = []
    decl = run_record_declaration(load_loop_contract())
    runs_root = loop_runs_root(project_root)
    local = socket.gethostname()
    for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()) if runs_root.is_dir() else []:
        for path in _run_files(run_dir):
            try:
                meta = read_run(path, decl)
            except LoopRunRecordError as exc:
                problems.append(f"运行记录读不出(无从判定在途): {exc}")
                continue
            if meta.get("status") == "ended":
                continue
            foreign = str(meta.get("host") or "") != local
            for launch in (l for l in (meta.get("launches") or []) if isinstance(l, dict) and not l.get("ended_at")):
                if not _within(launch.get("workstation"), workstation):
                    continue
                alive = True if foreign else process_alive(launch.get("pid"), launch.get("process_fingerprint"))
                if alive:
                    findings.append(f"在途拉起: 卡 {launch.get('card')} 的 {launch.get('harness')} 进程 pid={launch.get('pid')} "
                                    f"cwd={launch.get('workstation')}{'(记录写于他机, 无从探活, 按在途计)' if foreign else ''}"
                                    f"(运行记录 {path})")
            view = judge_run(meta, decl)
            if view["state"] not in ("running", "stalled", "launch_dead", "unprobeable"):
                continue
            step = meta.get("current_step") if isinstance(meta.get("current_step"), dict) else {}
            card = str(step.get("card") or "") if step.get("kind") == "wait" else ""
            if not card:
                continue
            try:
                card_path, _queue = find_task_card(project_root, card)
                fm = require_frontmatter(card_path)[0] if card_path is not None else {}
            except (AmbiguousTaskCard, FrontmatterReadError) as exc:
                problems.append(f"等待卡 {card} 读不出(无从判定等待实例): {exc}")
                continue
            if _wait_instance(project_root, card, fm) == instance:
                findings.append(f"在途 loop 等待本实例产物: 卡 {card}(运行 {meta.get('run_id')} 状态 {view['state']}, "
                                f"loop pid={meta.get('pid')}; 运行记录 {path})")
    probe = _cwd_probe(workstation)
    for hit in probe["hits"]:
        findings.append(f"在途进程: pid={hit['pid']} ({hit['comm']}) cwd={hit['cwd']} 在工位目录内")
    return {"findings": findings, "problems": problems, "probe": probe}


# ---------------------------------------------------------------------------
# 工位态清点(只读: 路径 / 类型 / 权限 / 大小 / 链接目标; 不读文件内容)
# ---------------------------------------------------------------------------

def _mode(path: Path) -> str:
    return oct(stat.S_IMODE(path.lstat().st_mode))[2:].zfill(4)


def inventory(workstation: Path, pointer_file: str) -> dict[str, Any]:
    """工位目录全部条目(顶层逐项: 类型 / 文件数 / 字节), .lybra/ 文件只列路径 + 权限 + 大小, 符号链接逐个列目标与是否指向工位外
    (指向工位父根的 = 产品接线挂载, 新落点由 sync 在新父根落地)。退役指针文件不计入工位态。"""
    entries: list[dict[str, Any]] = []
    credentials: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    total_files = total_bytes = 0
    for top in sorted(workstation.iterdir(), key=lambda p: p.name):
        if top.name == pointer_file:
            continue
        files = size = 0
        kind = "symlink" if top.is_symlink() else ("dir" if top.is_dir() else "file")
        walk = [top] if kind != "dir" else [Path(d) / n for d, dirs, names in os.walk(top) for n in (*names, *dirs)]
        for item in walk:
            if item.is_symlink():
                target = os.readlink(item)
                resolved = (item.parent / target).resolve()
                links.append({"path": item.relative_to(workstation).as_posix(), "target": target,
                              "outside": not _within(resolved, workstation.resolve()), "resolves": resolved.exists()})
                files += 1
            elif item.is_file():
                files += 1
                size += item.lstat().st_size
        total_files += files
        total_bytes += size
        entries.append({"name": top.name, "kind": kind, "mode": _mode(top), "files": files, "bytes": size})
    lybra_dir = workstation / ".lybra"
    if lybra_dir.is_dir():
        for item in sorted(lybra_dir.rglob("*")):
            if item.is_file() or item.is_symlink():
                credentials.append({"path": item.relative_to(workstation).as_posix(), "mode": _mode(item), "bytes": item.lstat().st_size})
    return {"entries": entries, "credentials": credentials, "links": links, "files": total_files, "bytes": total_bytes,
            "lybra_dir_mode": _mode(lybra_dir) if lybra_dir.is_dir() else None}


def _stat_view(workstation: Path) -> dict[str, tuple[int, int, int]]:
    """.lybra/ 下文件的 (权限, 大小, mtime 秒): 迁移前后比对完整性(只 stat, 不读内容)。"""
    lybra_dir = workstation / ".lybra"
    return {p.relative_to(workstation).as_posix(): (stat.S_IMODE(p.lstat().st_mode), p.lstat().st_size, int(p.lstat().st_mtime))
            for p in sorted(lybra_dir.rglob("*")) if p.is_file()} if lybra_dir.is_dir() else {}


# ---------------------------------------------------------------------------
# 计划(预演与确认同一判定)
# ---------------------------------------------------------------------------

def _parent_view(parent: Path, pointer_file: str) -> dict[str, Any]:
    """新工位父根现状(只读): 已有的分发落点目录(_distributed / _shared; sync 的共享落点 prune 作用于此)与已 enroll 工位。"""
    from tools.aipos_cli.charter_render import is_enrolled_workstation

    if not parent.is_dir():
        return {"exists": False, "landings": [], "workstations": []}
    return {"exists": True, "landings": sorted(n for n in ("_distributed", "_shared") if (parent / n).is_dir()),
            "workstations": sorted(p.name for p in parent.iterdir() if p.is_dir() and is_enrolled_workstation(p))}


def _only_own_pointer(target: Path, pointer_file: str, instance: str) -> bool:
    """目标目录只含本实例的退役指针(前次迁移 / 回滚留下)= 可作落点。指针读不出 / 不是本实例 = False。"""
    names = [p.name for p in target.iterdir()]
    if names != [pointer_file]:
        return False
    try:
        data = json.loads((target / pointer_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("instance") == instance


def plan_relocation(project_root: str | Path, instance: str, *, to: str | Path | None = None, rollback: bool = False,
                    for_confirm: bool = False, actor: str = "<actor>") -> dict[str, Any]:
    """迁移计划(只读, 零写入)。非本项目实例 / 无工位位置 / 他机 / 身份不符 = RelocationRefused(先于任何清点)。
    其余核查逐项记入 checks; refusals 非空 = 确认会被拒(预演照列全貌)。"""
    from tools.aipos_cli.charter_render import WorkstationIdentityError, workstation_identity
    from tools.aipos_cli.enrollment import enrollment_trail_path, location_event, workstation_location
    from tools.aipos_cli.naming_profile import parse_instance_name
    from tools.aipos_cli.workspace_config import project_execution, project_json_path, read_project_json

    decl = relocation_declaration()
    root = Path(project_root).expanduser().resolve()
    inst = str(instance or "").strip()
    project = str(read_project_json(root).get("project") or "").strip()
    parsed = parse_instance_name(inst) or {}
    if not project or parsed.get("project") != project:
        raise _refuse(decl, "RELOCATE_NOT_THIS_PROJECT",
                      f"实例 {inst!r} 的项目段 = {parsed.get('project')!r}, 本治理根 {root} 的项目 = {project!r}; "
                      "只能迁本项目声明的工位(他项目工位由该项目在其治理根运行同一命令)")
    loc = workstation_location(root, inst)
    if not loc["found"]:
        raise _refuse(decl, "RELOCATE_NO_LOCATION", str(loc["reason"]))
    if loc["transport"] != "local":
        raise _refuse(decl, "RELOCATE_REMOTE_UNSUPPORTED", f"位置事件 host={loc['host']} dir={loc['dir']}(transport={loc['transport']})")
    old = Path(str(loc["dir"])).expanduser().resolve()
    event = location_event(root, inst)
    try:
        identity = workstation_identity(old, instance=inst)
    except WorkstationIdentityError as exc:
        raise _refuse(decl, "RELOCATE_IDENTITY_MISMATCH", f"声明落点 {old}: {exc}") from exc
    if identity.get("instance") != inst or identity.get("project") != project:
        raise _refuse(decl, "RELOCATE_IDENTITY_MISMATCH",
                      f"声明落点 {old} 的 .lybra/role 实例 {identity.get('instance')!r} / 项目 {identity.get('project')!r} ≠ {inst!r} / {project!r}")
    from tools.aipos_cli.distribution_sync import default_harness_kind, workstation_harness

    try:
        harness_kind = str(workstation_harness(old, inst)["kind"])
    except (ValueError, OSError) as exc:
        raise _refuse(decl, "RELOCATE_IDENTITY_MISMATCH", f"声明落点 {old} 的 harness 不可解析: {exc}") from exc
    if harness_kind != default_harness_kind():
        raise _refuse(decl, "RELOCATE_HARNESS_UNSUPPORTED", f"{inst} 的工位 harness = {harness_kind}(本命令迁 {default_harness_kind()} 工位)")
    pointer = str(decl["pointer_file"])
    refusals: list[RelocationRefused] = []
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, code: str | None = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok and code:
            refusals.append(_refuse(decl, code, detail))

    suggestion = None
    if rollback:
        if event.get("action") != decl["event_action"] or not event.get("relocated_from"):
            raise _refuse(decl, "RELOCATE_NOTHING_TO_ROLLBACK",
                          f"{inst} 最新位置事件 = {event.get('action')}(无前次迁移记录可回滚)")
        target = Path(str(event["relocated_from"])).expanduser().resolve()
        source = "回滚: 最新 relocate 事件的 relocated_from"
    elif to:
        target = Path(str(to)).expanduser()
        source = "--to"
        if not target.is_absolute():
            raise _refuse(decl, "RELOCATE_TARGET_INVALID", f"--to {to} 不是绝对路径")
        target = target.resolve()
    else:
        suggestion = suggested_target(old, project, decl)
        target = suggestion
        source = "建议(声明 suggested_target; 确认须显式 --to)"
        if for_confirm:
            raise _refuse(decl, "RELOCATE_TARGET_REQUIRED", f"建议落点 {suggestion}")
    if any(ch.isspace() for ch in str(target)):
        raise _refuse(decl, "RELOCATE_TARGET_INVALID", f"落点 {target} 含空白(位置事件按空白分段)")
    if target == old or _within(target, old) or _within(old, target):
        raise _refuse(decl, "RELOCATE_TARGET_INVALID", f"新落点 {target} 与旧落点 {old} 相同或互相包含")
    checkout_old = enclosing_checkout(old)
    checkout_new = enclosing_checkout(target)
    if rollback:
        check("新落点 git 检出", True, f"回滚 = 迁回前次落点(位于检出 {checkout_new or '无'}), 不适用「不得在他人检出内」")
    else:
        check("新落点不在任何 git 检出内", checkout_new is None,
              f"{target} 位于 git 检出 {checkout_new} 内" if checkout_new else f"{target} 祖先目录均无 .git", "RELOCATE_TARGET_IN_CHECKOUT")
    if target.exists() and not target.is_dir():
        check("新落点为空", False, f"{target} 已存在且不是目录", "RELOCATE_TARGET_NOT_EMPTY")
    elif target.is_dir() and any(target.iterdir()):
        own = _only_own_pointer(target, pointer, inst)
        check("新落点为空", own, f"{target} 只含本实例退役指针 {pointer}(将移除后落地)" if own
              else f"{target} 已存在且非空: {sorted(p.name for p in target.iterdir())[:8]}", "RELOCATE_TARGET_NOT_EMPTY")
    else:
        check("新落点为空", True, f"{target} {'已存在且为空' if target.is_dir() else '不存在(将新建)'}")
    flight = in_flight(root, inst, old)
    probe = flight["probe"]
    check("在途运行(运行记录 + 进程 cwd)", not flight["findings"],
          "; ".join(flight["findings"]) or f"无在途拉起 / 无 loop 等待 {inst} / 本机 {probe['scanned']} 个进程无 cwd 在工位内"
          f"(他用户进程 {probe['unreadable']} 个读不到 cwd)", "RELOCATE_IN_FLIGHT")
    if flight["problems"]:
        check("运行记录可读", False, "; ".join(flight["problems"]), "RELOCATE_IN_FLIGHT")
    if not probe["available"]:
        check("进程 cwd 探活", False, "本机无 /proc", "RELOCATE_PROBE_UNAVAILABLE")
    inv = inventory(old, pointer)
    role = str(identity.get("role") or "")
    try:
        execution = project_execution(root) or {}
    except (ValueError, OSError) as exc:
        execution = {"_error": str(exc)}
    roles_in_execution = [k for k in ("auditor", "pi_executor", "subagent_executor") if execution.get(k) == inst]
    workstations_decl = (read_project_json(root).get("workstations") or {}).get(inst)
    trail = enrollment_trail_path(root)
    line = None
    if event.get("line_fields"):
        from tools.aipos_cli.enrollment import relocation_event_line

        line = relocation_event_line(event, instance=inst, project=project, old=old, new=target, by=actor or "<actor>",
                                     rollback=rollback).rstrip("\n")
    return {
        "instance": inst, "project": project, "governance_root": str(root), "project_json": str(project_json_path(root)),
        "rollback": rollback, "role": role, "harness": harness_kind,
        "old": {"dir": str(old), "host": loc["host"], "transport": loc["transport"], "event": event.get("action"),
                "landed_at": loc["landed_at"], "checkout": str(checkout_old) if checkout_old else None},
        "new": {"dir": str(target), "source": source, "checkout": str(checkout_new) if checkout_new else None},
        "suggested_target": str(suggestion) if suggestion else None,
        "checks": checks, "refusals": [{"code": r.code, "problems": r.problems} for r in refusals],
        "inventory": inv,
        "declarations": {
            "enrollment_log": str(trail), "append_line": line,
            "project_json_workstations": workstations_decl,
            "execution_roles": roles_in_execution,
            "manifest_old": str(old.parent / "_distributed" / f".version-{role}"),
            "manifest_new": str(target.parent / "_distributed" / f".version-{role}"),
            "shared_parent_old": str(old.parent), "shared_parent_new": str(target.parent),
            "pointer": str(old / pointer),
            "new_parent_existing": _parent_view(target.parent, pointer),
        },
        "steps": list(decl["steps"]),
        "pointer_file": pointer,
        "_refusal_objects": refusals,
    }


# ---------------------------------------------------------------------------
# 执行(--confirm)
# ---------------------------------------------------------------------------

def _move_entries(src: Path, dst: Path, names: list[str]) -> list[str]:
    """逐项移动(rename; 跨文件系统 = shutil.move, 保权限 / 符号链接原样)。中途失败 = 已移动者移回后上抛 OSError。"""
    moved: list[str] = []
    try:
        for name in names:
            try:
                os.rename(src / name, dst / name)
            except OSError as exc:
                if exc.errno != errno.EXDEV:  # 跨文件系统
                    raise
                shutil.move(str(src / name), str(dst / name))
            moved.append(name)
    except OSError:
        for name in reversed(moved):
            shutil.move(str(dst / name), str(src / name))
        raise
    return moved


def _write_pointer(old: Path, plan: dict[str, Any], *, actor: str) -> Path:
    """旧落点只读退役指针(不删旧目录, 回滚用): 本实例 / 去向 / 时刻 / 操作者 / 回滚命令。权限 0444。"""
    path = old / plan["pointer_file"]
    data = {"record": "lybra_workstation_relocated", "instance": plan["instance"], "project": plan["project"],
            "governance_root": plan["governance_root"], "moved_to": plan["new"]["dir"], "moved_at": iso_z(), "by": actor,
            "rollback": f"lybra project relocate-workstation {plan['project']} --instance {plan['instance']} --rollback --dry-run",
            "note": "工位态已迁出; 位置声明 = 治理根 enrollment_log 最新位置事件。本目录不删, 供回滚。"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o444)
    return path


def _sync_and_verify(target: Path, governance_root: Path) -> dict[str, Any]:
    """新落点 sync(distribution_sync.sync 唯一实现)+ 立即 dry-run 核稳态(plan / would_prune 皆空 = 清单与章程一致)。"""
    from tools.aipos_cli import distribution_sync

    result = distribution_sync.sync(harness_root=target, governance_root=governance_root)
    if not result.get("ok") or result.get("status") != "synced":
        return {"ok": False, "reason": f"sync 未成: status={result.get('status')} {result.get('error') or result.get('reason') or ''}".strip(),
                "sync": result}
    steady = distribution_sync.sync(harness_root=target, governance_root=governance_root, dry_run=True)
    ok = bool(steady.get("ok")) and steady.get("plan") == [] and steady.get("would_prune") == []
    return {"ok": ok, "reason": "" if ok else f"sync 后预演非稳态: plan={steady.get('plan')} would_prune={steady.get('would_prune')}",
            "sync": result, "steady": steady}


def relocate_workstation(project_root: str | Path, instance: str, *, to: str | Path | None = None, rollback: bool = False,
                         dry_run: bool = True, actor: str = "") -> dict[str, Any]:
    """`lybra project relocate-workstation` 唯一实现。dry_run(缺省)= plan_relocation 原样返回(零写入)。
    确认: 计划有拒因 = 首条 RelocationRefused(零写入); 否则 ①迁移工位态 ②sync 新落点并核稳态(失败 = 移回 + RELOCATE_SYNC_FAILED)
    ③追加 relocate 位置事件(enrollment 唯一写口)④核查新声明(位置 / 身份 / check_workstation)⑤旧落点写只读退役指针。
    落账由 CLI 薄壳经 _land_project_writes 完成(返回 written 供其使用)。"""
    from tools.aipos_cli.charter_render import workstation_identity
    from tools.aipos_cli.enrollment import append_relocation_event, workstation_location
    from tools.aipos_cli.loop_driver import check_workstation

    plan = plan_relocation(project_root, instance, to=to, rollback=rollback, for_confirm=not dry_run, actor=actor)
    refusal_objects = plan.pop("_refusal_objects")
    if dry_run:
        return {**plan, "dry_run": True, "written": []}
    if refusal_objects:
        raise RelocationRefused(refusal_objects[0].code, [f"{r.code}: {p}" for r in refusal_objects for p in r.problems])
    decl = relocation_declaration()
    root = Path(plan["governance_root"])
    old, target = Path(plan["old"]["dir"]), Path(plan["new"]["dir"])
    before = _stat_view(old)
    if target.is_dir() and (target / plan["pointer_file"]).exists():
        (target / plan["pointer_file"]).chmod(0o644)
        (target / plan["pointer_file"]).unlink()
    if (old / plan["pointer_file"]).exists():  # 旧落点上一轮留下的本实例指针(回滚后再迁)不随迁
        (old / plan["pointer_file"]).chmod(0o644)
        (old / plan["pointer_file"]).unlink()
    target.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in old.iterdir())
    try:
        moved = _move_entries(old, target, names)
    except OSError as exc:
        raise _refuse(decl, "RELOCATE_MOVE_FAILED", f"{type(exc).__name__}: {exc}(已移回旧落点)") from exc
    after = _stat_view(target)
    try:
        synced = _sync_and_verify(target, root)
    except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:  # 与 sync_many 同一捕获面: 出错 = 复原, 不吞
        synced = {"ok": False, "reason": f"sync 出错: {type(exc).__name__}: {exc}"}
    if not synced["ok"] or before != after:
        _move_entries(target, old, moved)
        why = synced["reason"] if not synced["ok"] else "凭据文件权限 / 大小 / mtime 迁移前后不一致"
        raise _refuse(decl, "RELOCATE_SYNC_FAILED", f"{why}; 工位态已移回 {old}, 位置声明未改(旧落点章程如被重渲染, 在旧落点 lybra sync 一次即复原)")
    trail = append_relocation_event(root, plan["instance"], project=plan["project"], old=old, new=target, by=actor, rollback=rollback)
    loc = workstation_location(root, plan["instance"])
    identity = workstation_identity(target, instance=plan["instance"])
    checked = check_workstation(root, plan["instance"], plan["harness"])
    pointer = _write_pointer(old, plan, actor=actor)
    verify = {"location_dir": loc.get("dir"), "location_ok": loc.get("found") and Path(str(loc.get("dir"))).resolve() == target,
              "identity_ok": identity.get("instance") == plan["instance"], "check_workstation": checked,
              "credentials_intact": before == after, "credentials": sorted(after)}
    sync_view = {"manifest_path": synced["sync"].get("manifest_path"), "files_fetched": synced["sync"].get("files_fetched"),
                 "changes": [(c.get("distribution_id"), c.get("action"), c.get("files_written")) for c in synced["sync"].get("changes") or []],
                 "steady_plan": synced["steady"].get("plan"), "steady_would_prune": synced["steady"].get("would_prune"),
                 "pi_mount_warnings": synced["steady"].get("pi_mount_warnings")}
    return {**plan, "dry_run": False, "moved": moved, "sync": sync_view, "verify": verify, "pointer": str(pointer),
            "written": [str(trail)], "ok": bool(verify["location_ok"] and verify["identity_ok"])}


def render_relocation(result: dict[str, Any]) -> list[str]:
    """预演 / 确认结果的人读行(唯一渲染; CLI 原样打印)。凭据只出路径 / 权限 / 大小。"""
    inv = result["inventory"]
    decls = result["declarations"]
    kind = "回滚" if result["rollback"] else "迁移"
    lines = [f"  实例: {result['instance']}(角色 {result['role']}, 项目 {result['project']}) · {kind}",
             f"  旧落点: {result['old']['dir']}(host={result['old']['host'] or '(本机, 存量缺 host)'}, transport={result['old']['transport']}, "
             f"位置事件={result['old']['event']}; {'位于 git 检出 ' + result['old']['checkout'] + ' 内' if result['old']['checkout'] else '不在 git 检出内'})",
             f"  新落点: {result['new']['dir']}(来源: {result['new']['source']})"]
    lines.append("  核查:")
    lines += [f"    {'✓' if c['ok'] else '✗'} {c['check']}: {c['detail']}" for c in result["checks"]]
    lines.append(f"  工位态(整体迁移; 顶层 {len(inv['entries'])} 项, 文件与链接 {inv['files']} 个, {inv['bytes']} 字节):")
    lines += [f"    {e['name']}{'/' if e['kind'] == 'dir' else ''} [{e['kind']} {e['mode']}] {e['files']} 个, {e['bytes']} 字节" for e in inv["entries"]]
    lines.append(f"  凭据(.lybra/ {inv['lybra_dir_mode'] or '-'}; 只列路径 / 权限 / 大小, 不读内容; 随目录 rename 移动):")
    lines += [f"    {c['path']} [{c['mode']}] {c['bytes']} 字节" for c in inv["credentials"]] or ["    (无)"]
    outside = [l for l in inv["links"] if l["outside"]]
    lines.append(f"  指向工位外的链接 {len(outside)} 个(产品接线挂载指向工位父根: 旧 {decls['shared_parent_old']} → 新 {decls['shared_parent_new']}, "
                 "新父根的 _distributed / _shared 由 sync 落地):")
    lines += [f"    {l['path']} -> {l['target']}{'' if l['resolves'] else '(当前悬空)'}" for l in outside] or ["    (无)"]
    lines.append("  受影响的声明:")
    lines.append(f"    位置声明(唯一来源) {decls['enrollment_log']}: 追加 {decls['append_line'] or '(位置事件无可沿用字段)'}")
    lines.append(f"    project.json workstations.{result['instance']}: "
                 + (f"已声明(跨机开工材料, 与目录无关, 不改) {decls['project_json_workstations']}" if decls["project_json_workstations"]
                    else "未声明(本机工位不需要; 不改)"))
    lines.append(f"    project.json execution: {', '.join(decls['execution_roles']) or '(未引用本实例)'}(按实例名引用, 实例名不变, 不改)")
    lines.append(f"    分发清单: 旧 {decls['manifest_old']}(留原处, 共用父根其他工位照用) · 新 {decls['manifest_new']}(sync 写)")
    lines.append(f"    旧落点退役指针: {decls['pointer']}(只读 0444, 旧目录不删, 回滚用)")
    parent = decls["new_parent_existing"]
    lines.append(f"    新工位父根 {decls['shared_parent_new']}: " + ("不存在(将新建)" if not parent["exists"] else
                 f"已有分发落点 {parent['landings'] or '无'}(sync 的共享落点 prune 作用于此), 已 enroll 工位 {parent['workstations'] or '无'}"))
    lines.append("  确认后步骤: " + " → ".join(f"{i}.{s}" for i, s in enumerate(result["steps"], 1)))
    if result.get("suggested_target"):
        lines.append(f"  建议落点: {result['suggested_target']}(确认须显式 --to)")
    if result["refusals"]:
        lines.append("  确认将被拒:")
        lines += [f"    {r['code']}: {p}" for r in result["refusals"] for p in r["problems"]]
    if not result["dry_run"]:
        sync = result["sync"]
        verify = result["verify"]
        lines.append(f"  已迁移 {len(result['moved'])} 项: {', '.join(result['moved'])}")
        lines.append(f"  sync 新落点: {sync['changes']} · 清单 {sync['manifest_path']}")
        lines.append(f"  sync 稳态预演: plan={sync['steady_plan']} would_prune={sync['steady_would_prune']}")
        lines.append(f"  凭据完整(权限 / 大小 / mtime 迁移前后一致): {verify['credentials_intact']} {verify['credentials']}")
        lines.append(f"  位置声明读回(workstation_location): {verify['location_dir']} {'✓' if verify['location_ok'] else '✗'}; "
                     f"工位身份 {'✓' if verify['identity_ok'] else '✗'}")
        lines.append(f"  check-workstation: {'OK' if verify['check_workstation']['ok'] else 'FAILED'}")
        lines += [f"    {'✓' if c['ok'] else '✗'} {c['check']}: {c['detail']}" for c in verify["check_workstation"]["checks"]]
        lines.append(f"  旧落点退役指针: {result['pointer']}")
    return lines
