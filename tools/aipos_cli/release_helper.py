#!/usr/bin/env python3
"""AIPOS-F251: 受控发布的目标主机侧执行体(纯 python3 标准库单文件; 不 import 产品其他模块)。

一句话: 治理根所在机的 `lybra release plan|apply|verify|rollback`(唯一实现 tools/aipos_cli/release_target.py)把本文件经 stdin 送到
目标主机执行: 远端 = `ssh <别名> 'python3 -I -c <引导>'`, 本机主机 = 同一引导直接起子进程。引导里钉着本文件的 sha256——目标主机先读
`<字节数>\\n<本文件原文>`, 摘要不等即拒(HELPER_SHA256_MISMATCH, 不执行), 等才执行 serve(); stdin 余下部分 = 一份 JSON 请求。
本文件不落目标主机、不起后台进程、不调任何外部命令。

请求 op(全部 fail-closed; 输出 = stdout 末行一份 JSON; 退出码 0 = 成功, 3 = 拒 / 中途停, 2 = 请求不合):
  probe    只读: 逐个目的路径 lstat(已存在含悬空链接如实报)+ 逐个父目录非递归列条目(类型 / 模式 / 属主 / 链接目标 / 普通文件 sha256)。
  apply    只执行与计划哈希一致的计划: 先核计划哈希(canonical_json 同一实现)与每个文件载荷 sha256, 再逐项预检(目的路径一律不得已存在,
           父目录须已存在或由本计划先建); 然后按计划顺序逐项原子不覆盖地新建——目录 os.mkdir(不越级建父目录)、文件 O_CREAT|O_EXCL|
           O_NOFOLLOW、链接 os.symlink(已存在即失败, 不带强制); 写后逐文件重读核 sha256; 任一步失败立即停(不继续、不猜、不清理)。
           写前 / 写后各对计划给出的父目录拍一次非递归基线。
  verify   只读: 计划里每个新建对象仍在且一致(目录是目录 / 文件 sha256 / 链接目标原文), 入口链接解析到的对象摘要 = 源清单。
  rollback 只删给出的链接(按给出顺序): 当前 lstat 须为符号链接且链接目标原文与记录精确相等, 否则停线(不删该项及其后各项);
           只 os.unlink 链接本身, 不跟随、不递归、不删目录。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import sys
from typing import Any

SCHEMA = "lybra-release-helper-v1"
#: 新建对象的权限位(目录可进入、文件可读; 属主 = 运行用户, 不 chown / 不改已有对象权限)
DIR_MODE = 0o755
FILE_MODE = 0o644
#: 父目录基线里普通文件求 sha256 的单文件上限(超限只记大小, 不读内容)
BASELINE_HASH_MAX_BYTES = 8 * 1024 * 1024
CREATE_KINDS = ("dir", "file", "link")


def canonical_json(value: Any) -> bytes:
    """计划哈希的唯一规范化(治理根侧算计划哈希与目标主机侧复核同一实现)。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def plan_hash(plan: Any) -> str:
    return hashlib.sha256(canonical_json(plan)).hexdigest()


def _kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _file_sha256(path: str) -> str:
    """读普通文件内容(不跟随链接: O_NOFOLLOW)求 sha256。"""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def lstat_info(path: str, *, with_sha: bool = True) -> dict[str, Any]:
    """一个路径的 lstat 事实(不跟随链接)。不存在 = {exists: False}; 悬空链接 = exists True / type link。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return {"path": path, "exists": False}
    info: dict[str, Any] = {"path": path, "exists": True, "type": _kind(st.st_mode), "mode": oct(stat.S_IMODE(st.st_mode)),
                            "uid": st.st_uid, "gid": st.st_gid, "size": st.st_size, "link_target": None, "sha256": None}
    if info["type"] == "link":
        info["link_target"] = os.readlink(path)
    elif info["type"] == "file" and with_sha and st.st_size <= BASELINE_HASH_MAX_BYTES:
        info["sha256"] = _file_sha256(path)
    return info


def list_parent(path: str) -> dict[str, Any]:
    """父目录非递归基线: 目录自身 lstat + 每个直接条目的 lstat(普通文件带 sha256)。不存在 / 不是目录 = entries None。"""
    info = lstat_info(path, with_sha=False)
    entries = None
    if info.get("type") == "dir":
        info["writable"] = os.access(path, os.W_OK | os.X_OK)  # 当前用户能否在其下新建(不提权; 不可写 = 计划 / 执行前即拒)
        entries = [lstat_info(os.path.join(path, name)) for name in sorted(os.listdir(path))]
        for item in entries:
            item["name"] = os.path.basename(item["path"])
    return {"info": info, "entries": entries}


def _probe(req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    paths = req.get("paths")
    parents = req.get("parents")
    if not isinstance(paths, list) or not isinstance(parents, list):
        return 2, {"ok": False, "code": "REQUEST_INVALID", "reason": "probe 须给 paths / parents 列表"}
    return 0, {"ok": True, "paths": {p: lstat_info(p) for p in paths}, "parents": {d: list_parent(d) for d in parents}}


def _deny(code: str, reason: str, **extra: Any) -> tuple[int, dict[str, Any]]:
    return 3, {"ok": False, "code": code, "reason": reason, **extra}


def _apply(req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    plan, given = req.get("plan"), req.get("plan_hash")
    files = req.get("files")
    if not isinstance(plan, dict) or not isinstance(given, str) or not isinstance(files, dict):
        return 2, {"ok": False, "code": "REQUEST_INVALID", "reason": "apply 须给 plan / plan_hash / files"}
    got = plan_hash(plan)
    if got != given:
        return _deny("PLAN_HASH_MISMATCH", f"计划哈希 {got} ≠ 给出的 {given}(计划被改, 不执行)")
    creates = plan.get("creates")
    if not isinstance(creates, list) or not creates:
        return _deny("PLAN_INVALID", "计划无新建项")
    payload: dict[str, bytes] = {}
    planned: set[str] = set()
    for item in creates:
        if not isinstance(item, dict) or item.get("kind") not in CREATE_KINDS or not isinstance(item.get("path"), str):
            return _deny("PLAN_INVALID", f"计划项形不合: {item!r}")
        path = item["path"]
        if not path.startswith("/") or os.path.normpath(path) != path:
            return _deny("PLAN_INVALID", f"计划项路径须为规范绝对路径: {path!r}")
        if item["kind"] == "file":
            data = files.get(item.get("src"))
            if not isinstance(data, str):
                return _deny("PAYLOAD_MISSING", f"文件 {path} 缺载荷(src={item.get('src')!r})")
            raw = base64.b64decode(data.encode("ascii"), validate=True)
            if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
                return _deny("PAYLOAD_SHA256_MISMATCH", f"文件 {path} 载荷 sha256 ≠ 计划 {item.get('sha256')}")
            payload[path] = raw
        if item["kind"] == "link" and not isinstance(item.get("target"), str):
            return _deny("PLAN_INVALID", f"链接 {path} 缺链接目标")
        if os.path.lexists(path):
            return _deny("TARGET_EXISTS", f"目的路径 {path} 已存在(含悬空链接; 只新建, 不覆盖), 未做任何写入", existing=lstat_info(path))
        parent = os.path.dirname(path)
        if parent not in planned:
            pinfo = lstat_info(parent, with_sha=False)
            if pinfo.get("type") != "dir":
                return _deny("PARENT_MISSING", f"{path} 的父目录 {parent} 不是已存在的目录(也不由本计划先建; 不越级建父目录), 未做任何写入",
                             parent=pinfo)
            if not os.access(parent, os.W_OK | os.X_OK):
                return _deny("PARENT_NOT_WRITABLE", f"{path} 的父目录 {parent} 当前用户不可写(不提权), 未做任何写入", parent=pinfo)
        planned.add(path)
    parents = [p for p in plan.get("baseline_parents") or [] if isinstance(p, str)]
    before = {d: list_parent(d) for d in parents}
    created: list[dict[str, Any]] = []
    failed: dict[str, Any] | None = None
    for index, item in enumerate(creates):
        path, kind = item["path"], item["kind"]
        try:
            if kind == "dir":
                os.mkdir(path, DIR_MODE)
            elif kind == "file":
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(path, flags, FILE_MODE)
                try:
                    view = memoryview(payload[path])
                    while view:
                        written = os.write(fd, view)
                        view = view[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:
                os.symlink(item["target"], path)
        except OSError as exc:
            failed = {"step": index, "path": path, "kind": kind, "error": f"{type(exc).__name__}: {exc}"}
            break
        created.append({"kind": kind, "path": path})
        if kind == "file":
            digest = _file_sha256(path)
            if digest != item["sha256"]:
                failed = {"step": index, "path": path, "kind": kind, "error": f"写后 sha256 {digest} ≠ 计划 {item['sha256']}"}
                break
    after = {d: list_parent(d) for d in parents}
    result = {"ok": failed is None, "created": created, "failed": failed, "baseline_before": before, "baseline_after": after,
              "plan_hash": given}
    if failed is not None:
        result.update(code="APPLY_STOPPED", reason=f"第 {failed['step'] + 1} 项 {failed['path']} 失败即停: {failed['error']}")
    return (0 if failed is None else 3), result


def _verify(req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    plan = req.get("plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("creates"), list):
        return 2, {"ok": False, "code": "REQUEST_INVALID", "reason": "verify 须给 plan"}
    items: list[dict[str, Any]] = []
    for item in plan["creates"]:
        info = lstat_info(item["path"])
        problem = None
        if not info["exists"]:
            problem = "不存在"
        elif info["type"] != item["kind"]:
            problem = f"类型 {info['type']} ≠ 计划 {item['kind']}"
        elif item["kind"] == "file" and info["sha256"] != item["sha256"]:
            problem = f"sha256 {info['sha256']} ≠ 计划 {item['sha256']}"
        elif item["kind"] == "link" and info["link_target"] != item["target"]:
            problem = f"链接目标 {info['link_target']!r} ≠ 计划 {item['target']!r}"
        items.append({"path": item["path"], "kind": item["kind"], "ok": problem is None, "problem": problem})
    for entry in plan.get("entries") or []:
        real = os.path.realpath(entry["path"])
        want = entry.get("object_sha256")
        problem = None
        if not os.path.exists(real):
            problem = f"入口解析到 {real} 不存在"
        elif want is not None:
            got = _file_sha256(real) if os.path.isfile(real) else None
            if got != want:
                problem = f"入口解析到 {real} 的 sha256 {got} ≠ 源清单 {want}"
        elif not os.path.isdir(real):
            problem = f"入口解析到 {real} 不是目录"
        items.append({"path": entry["path"], "kind": "entry_resolved", "resolved": real, "ok": problem is None, "problem": problem})
    ok = all(i["ok"] for i in items)
    return (0 if ok else 3), {"ok": ok, "items": items, **({} if ok else {"code": "VERIFY_MISMATCH", "reason": "已发布对象与计划不一致"})}


def _rollback(req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    links = req.get("links")
    if not isinstance(links, list) or not links:
        return 2, {"ok": False, "code": "REQUEST_INVALID", "reason": "rollback 须给 links 列表"}
    removed: list[str] = []
    for link in links:
        path, target = link.get("path"), link.get("target")
        info = lstat_info(path, with_sha=False)
        if not info["exists"]:
            return _deny("ROLLBACK_STOPPED", f"{path} 已不存在(与记录不符, 停线; 已删 {len(removed)} 项)", removed=removed, stopped=info)
        if info["type"] != "link" or info["link_target"] != target:
            return _deny("ROLLBACK_STOPPED", f"{path} 当前不是记录中的链接(类型 {info['type']}, 目标 {info.get('link_target')!r} ≠ 记录 {target!r}),"
                                             f" 停线不删(已删 {len(removed)} 项)", removed=removed, stopped=info)
        os.unlink(path)
        removed.append(path)
    return 0, {"ok": True, "removed": removed}


OPS = {"probe": _probe, "apply": _apply, "verify": _verify, "rollback": _rollback}


def serve(raw: bytes) -> int:
    """stdin 余下部分 = JSON 请求 {schema, op, ...}; 结果 JSON 写 stdout 一行(另带主机名)。"""
    try:
        req = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        code, result = 2, {"ok": False, "code": "REQUEST_INVALID", "reason": f"请求不是 JSON: {exc}"}
    else:
        if not isinstance(req, dict) or req.get("schema") != SCHEMA or req.get("op") not in OPS:
            code, result = 2, {"ok": False, "code": "REQUEST_INVALID", "reason": f"请求 schema / op 不合(要 {SCHEMA}, op ∈ {sorted(OPS)})"}
        else:
            try:
                code, result = OPS[req["op"]](req)
            except OSError as exc:
                code, result = 3, {"ok": False, "code": "HOST_IO_ERROR", "reason": f"{type(exc).__name__}: {exc}"}
    result["host"] = socket.gethostname()
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(serve(sys.stdin.buffer.read()))
