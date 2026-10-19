#!/usr/bin/env python3
"""AIPOS-F190 件③: 跨机临时验证根的主机侧检查入口(纯 python3 标准库单文件; 不 import 产品其他模块)。

一句话: 远端主机上的路径在治理根所在机无法 resolve(符号链接 / 真实路径只在目标主机可知), 故由本文件在**目标主机**上对
realpath 做与 write-boundary 同一判定并 fail-closed; 主机身份(指纹)也只在此一处计算——治理根侧
tools/aipos_cli/validation_root.py 直接 import 本模块的同名函数(声明时记录指纹、本机判定 realpath), 禁第二份实现。

下发: `lybra project validation-root helper` 原样打印本文件(开工稿给出取用命令与 sha256 核对); 在目标主机运行:
  python3 validation_root_helper.py fingerprint
      打印本机主机指纹(hfp1:…)与主机名; 只读。
  python3 validation_root_helper.py check --run-dir <base>/<卡ID>/<run-id> --path <目标> --expect-fingerprint <hfp1:…>
      退出码 0 且末行 `ALLOW …` = 允许; 其余(含 3 = 拒 / 2 = 用法或环境错 / 无输出)一律视为拒。
  python3 validation_root_helper.py snapshot --out <文件> <目录>...
      系统服务副作用核验: 列出目录下文件(相对路径 / 大小 / mtime), 写 JSON 到 --out(--out 须在本 run 目录内); 只读被列目录。
  python3 validation_root_helper.py compare <前.json> <后.json>
      前后清单比对: 打印新增 / 删除 / 变更; 无变化退出 0, 有变化退出 4(由报告如实列出, 不静默)。

判定(全部 fail-closed):
  1. 本机指纹 = --expect-fingerprint, 否则 HOST_FINGERPRINT_MISMATCH(跨机调用核对);
  2. run 目录与目标须为规范绝对路径(无 `..` / `.` / 重复斜杠), 否则 PATH_NOT_CANONICAL;
  3. run 目录的真实路径须与其字面一致(验证根自身不得含符号链接), 否则 RUN_DIR_SYMLINK;
  4. 目标的真实路径(已存在的最长前缀取 realpath, 其余按字面接上)须在 run 目录内, 否则 SYMLINK_ESCAPE / OUTSIDE_RUN_DIR。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import posixpath
import re
import socket
import subprocess
import sys
from typing import Any

FINGERPRINT_SCHEME = "hfp1"
_FINGERPRINT_RE = re.compile(r"^hfp1:[0-9a-f]{32}$")
_LINUX_MACHINE_ID_FILES = ("/etc/machine-id", "/var/lib/dbus/machine-id")
_DARWIN_UUID_RE = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')
EXIT_ALLOW, EXIT_ERROR, EXIT_DENY, EXIT_CHANGED = 0, 2, 3, 4


class HostIdentityError(RuntimeError):
    """本机机器标识读不出(fail-closed: 不以主机名凑指纹)。"""


# ---------------------------------------------------------------------------
# 主机身份(唯一实现)
# ---------------------------------------------------------------------------

def host_label() -> str:
    """本机主机名标签(短名小写; 只作人读与本机判定, 身份以指纹为准)。"""
    return socket.gethostname().split(".")[0].strip().lower()


def machine_id() -> str:
    """本机机器标识: Linux = /etc/machine-id(或 dbus 副本); macOS = IOPlatformUUID(ioreg 只读查询)。读不出 = HostIdentityError。"""
    system = platform.system()
    if system == "Darwin":
        try:
            proc = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostIdentityError(f"ioreg 查询 IOPlatformUUID 失败: {exc}") from exc
        match = _DARWIN_UUID_RE.search(proc.stdout or "")
        if proc.returncode != 0 or not match:
            raise HostIdentityError(f"ioreg 输出无 IOPlatformUUID(退出码 {proc.returncode})")
        return match.group(1).strip().lower()
    for candidate in _LINUX_MACHINE_ID_FILES:
        try:
            with open(candidate, encoding="utf-8") as handle:
                value = handle.read().strip().lower()
        except OSError:
            continue
        if value:
            return value
    raise HostIdentityError(f"本机({system})无可读机器标识({', '.join(_LINUX_MACHINE_ID_FILES)} / IOPlatformUUID)")


def host_fingerprint(machine: str | None = None) -> str:
    """主机指纹 = hfp1:<sha256('lybra-host-fingerprint-v1:' + 机器标识) 前 32 位>。machine 缺省 = 本机 machine_id()。
    不含主机名(主机名会变), 不泄露机器标识原文。"""
    value = (machine if machine is not None else machine_id()).strip().lower()
    if not value:
        raise HostIdentityError("机器标识为空")
    digest = hashlib.sha256(f"lybra-host-fingerprint-v1:{value}".encode("utf-8")).hexdigest()[:32]
    return f"{FINGERPRINT_SCHEME}:{digest}"


def is_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and bool(_FINGERPRINT_RE.match(value))


# ---------------------------------------------------------------------------
# 路径判定(字面 + 真实路径; 治理根侧本机判定与主机侧同一实现)
# ---------------------------------------------------------------------------

def canonical_problem(path: str) -> str | None:
    """规范绝对路径判据: 绝对、无 `..` / `.` 段、无重复斜杠、无结尾斜杠(根除外)、无控制字符。返回问题原文或 None。"""
    text = str(path or "")
    if not text.startswith("/"):
        return f"须为绝对路径: {text!r}"
    if any(ord(ch) < 32 for ch in text):
        return f"含控制字符: {text!r}"
    if text != "/" and text.endswith("/"):
        return f"结尾不得带斜杠: {text!r}"
    if "//" in text:
        return f"含重复斜杠: {text!r}"
    parts = text.split("/")[1:]
    if any(p in ("..", ".") for p in parts):
        return f"含 `..` 或 `.` 段(路径穿越): {text!r}"
    if posixpath.normpath(text) != text:
        return f"非规范路径(normpath = {posixpath.normpath(text)!r}): {text!r}"
    return None


def is_within(path: str, root: str) -> bool:
    """字面包含(按段): path == root 或 path 以 root + '/' 起头。"""
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/") or root == "/"


def real_target(path: str) -> str:
    """目标真实路径: 已存在的最长前缀取 os.path.realpath(解符号链接), 其余段按字面接上(待建文件)。"""
    head, tail = path, []
    while head != "/" and not os.path.lexists(head):
        head, name = posixpath.split(head)
        tail.insert(0, name)
    base = os.path.realpath(head)
    return posixpath.join(base, *tail) if tail else base


def judge_realpath(run_dir: str, target: str) -> dict[str, Any]:
    """本机 realpath 判定(主机侧 check 与治理根侧本机判定同一实现)。返回 {allowed, code, reason, real_target, real_run_dir}。"""
    for label, value in (("run 目录", run_dir), ("目标", target)):
        problem = canonical_problem(value)
        if problem:
            return {"allowed": False, "code": "PATH_NOT_CANONICAL", "reason": f"{label}{problem}", "real_target": None, "real_run_dir": None}
    if not is_within(target, run_dir):
        return {"allowed": False, "code": "OUTSIDE_RUN_DIR", "reason": f"目标 {target} 不在本 run 目录 {run_dir} 内",
                "real_target": None, "real_run_dir": None}
    real_run = real_target(run_dir)
    if real_run != run_dir:
        return {"allowed": False, "code": "RUN_DIR_SYMLINK",
                "reason": f"run 目录 {run_dir} 的真实路径为 {real_run}(验证根路径含符号链接, fail-closed 拒)",
                "real_target": None, "real_run_dir": real_run}
    resolved = real_target(target)
    if not is_within(resolved, real_run):
        return {"allowed": False, "code": "SYMLINK_ESCAPE",
                "reason": f"目标 {target} 的真实路径 {resolved} 逃出 run 目录 {real_run}(符号链接逃逸)",
                "real_target": resolved, "real_run_dir": real_run}
    return {"allowed": True, "code": "OK", "reason": f"目标真实路径 {resolved} 在 run 目录 {real_run} 内",
            "real_target": resolved, "real_run_dir": real_run}


# ---------------------------------------------------------------------------
# 系统服务副作用核验: 前后清单
# ---------------------------------------------------------------------------

def snapshot(paths: list[str]) -> dict[str, Any]:
    """只读列出各目录下文件 {目录: {相对路径: [大小, mtime 整秒]}}; 目录不存在 = null(如实记)。不跟随符号链接。"""
    out: dict[str, Any] = {}
    for root in paths:
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            out[root] = None
            continue
        entries: dict[str, list[int]] = {}
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(current, name)
                try:
                    st = os.lstat(full)
                except OSError as exc:
                    entries[os.path.relpath(full, root)] = [-1, -1]
                    sys.stderr.write(f"snapshot: {full} 不可读({exc}), 记 -1\n")
                    continue
                entries[os.path.relpath(full, root)] = [int(st.st_size), int(st.st_mtime)]
        out[root] = entries
    return out


def compare(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for root in sorted(set(before) | set(after)):
        b, a = before.get(root), after.get(root)
        if b is None and a is None:
            continue
        if b is None or a is None:
            lines.append(f"{root}: 目录{'新出现' if b is None else '消失'}")
            b, a = b or {}, a or {}
        for rel in sorted(set(a) - set(b)):
            lines.append(f"+ {root}/{rel}")
        for rel in sorted(set(b) - set(a)):
            lines.append(f"- {root}/{rel}")
        for rel in sorted(set(a) & set(b)):
            if a[rel] != b[rel]:
                lines.append(f"~ {root}/{rel}")
    return lines


# ---------------------------------------------------------------------------
# CLI(目标主机上运行)
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validation_root_helper.py", description="Lybra validation-root host-side check (AIPOS-F190)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fingerprint", help="Print this host's fingerprint (hfp1:...) and host label; read-only")
    check = sub.add_parser("check", help="Judge one write target against the declared run directory on this host (realpath, fail-closed)")
    check.add_argument("--run-dir", required=True)
    check.add_argument("--path", required=True)
    check.add_argument("--expect-fingerprint", required=True)
    snap = sub.add_parser("snapshot", help="List files under directories (side-effect check); writes JSON to --out inside --run-dir")
    snap.add_argument("--run-dir", required=True)
    snap.add_argument("--out", required=True)
    snap.add_argument("paths", nargs="+")
    cmp_ = sub.add_parser("compare", help="Compare two snapshots; exit 0 unchanged, 4 changed")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "fingerprint":
        try:
            print(f"{host_fingerprint()} host={host_label()}")
        except HostIdentityError as exc:
            print(f"ERROR [HOST_IDENTITY_UNREADABLE] {exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_ALLOW
    if args.command == "check":
        if not is_fingerprint(args.expect_fingerprint):
            print(f"DENY [HOST_FINGERPRINT_INVALID] --expect-fingerprint {args.expect_fingerprint!r} 不是 hfp1 指纹")
            return EXIT_DENY
        try:
            mine = host_fingerprint()
        except HostIdentityError as exc:
            print(f"DENY [HOST_IDENTITY_UNREADABLE] {exc}")
            return EXIT_DENY
        if mine != args.expect_fingerprint:
            print(f"DENY [HOST_FINGERPRINT_MISMATCH] 本机指纹 {mine} ≠ 声明 {args.expect_fingerprint}(跨机调用, 本主机不是声明主机)")
            return EXIT_DENY
        result = judge_realpath(args.run_dir, args.path)
        print(f"{'ALLOW' if result['allowed'] else 'DENY'} [{result['code']}] {result['reason']}")
        return EXIT_ALLOW if result["allowed"] else EXIT_DENY
    if args.command == "snapshot":
        verdict = judge_realpath(args.run_dir, args.out)
        if not verdict["allowed"]:
            print(f"DENY [{verdict['code']}] --out {verdict['reason']}")
            return EXIT_DENY
        data = snapshot(list(args.paths))
        os.makedirs(posixpath.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"snapshot {len(args.paths)} 个目录 → {args.out}")
        return EXIT_ALLOW
    try:
        with open(args.before, encoding="utf-8") as handle:
            before = json.load(handle)
        with open(args.after, encoding="utf-8") as handle:
            after = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"ERROR [SNAPSHOT_UNREADABLE] {exc}", file=sys.stderr)
        return EXIT_ERROR
    lines = compare(before, after)
    for line in lines:
        print(line)
    print(f"{'CHANGED' if lines else 'UNCHANGED'} {len(lines)} 处")
    return EXIT_CHANGED if lines else EXIT_ALLOW


if __name__ == "__main__":
    sys.exit(main())
