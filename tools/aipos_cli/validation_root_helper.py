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
  python3 - init-run --base <base> --run-dir <base>/<卡ID>/<run-id> --expect-fingerprint <hfp1:…> --expires-at <ISO> --seal <vri1:…>
      AIPOS-F191 目录链初始化(run-start 后第一步; 完整命令由产品铸: run-start --confirm 输出 / 开工稿 / `validation-root init-command`;
      run 目录尚不存在, 故 helper 经管道交给 `python3 -` 运行, 不落盘): 只用 mkdir 建缺失的目录链(base 最近已存在祖先之下
      → base → <base>/<卡ID> → run 目录, 新建一律 0700), 不写任何文件(证据追加到 run 目录内 .lybra-vr/init-run.jsonl);
      逐级拒软链 / 非目录 / 他人属主, 最近已存在祖先当前用户不可写(须提权的位置)= 拒并给人话出口;
      已存在且合规 = 幂等(退出 0)。末行 `INIT_OK …` + 退出码 0 = 成功; 其余(3 = 拒 / 2 = 用法或环境错)一律视为未建。

判定(全部 fail-closed):
  1. 本机指纹 = --expect-fingerprint, 否则 HOST_FINGERPRINT_MISMATCH(跨机调用核对);
  2. run 目录与目标须为规范绝对路径(无 `..` / `.` / 重复斜杠), 否则 PATH_NOT_CANONICAL;
  3. run 目录的真实路径须与其字面一致(验证根自身不得含符号链接), 否则 RUN_DIR_SYMLINK;
  4. 目标的真实路径(已存在的最长前缀取 realpath, 其余按字面接上)须在 run 目录内, 否则 SYMLINK_ESCAPE / OUTSIDE_RUN_DIR。
init-run 另判(全部 fail-closed, 任一不合 = 零 mkdir):
  5. --seal = 产品按 (base, run 目录, 指纹, 到期) 铸的完整性封印(init_seal), 改任一参数 = INIT_SEAL_MISMATCH(命令须原样取自产品;
     审计实例的命令只含审计卡目录链, 改卡段建执行卡层即封印不符);
  6. run 目录须恰为 <base>/<卡段>/<run 段>(INIT_CHAIN_INVALID); 到期(--expires-at 已过)= DECLARATION_EXPIRED;
  7. 链上已存在各级不得是符号链接(CHAIN_SYMLINK)/ 非目录(CHAIN_NOT_DIRECTORY), 最近已存在祖先真实路径 = 字面;
     base 及以下已存在的级属主须为当前用户(CHAIN_NOT_OWNED); 最近已存在祖先须当前用户可写(ANCESTOR_NOT_WRITABLE, 禁 sudo)。
  run 已结束 / 声明已撤销只在治理根侧可知: 产品对非活动 run 不铸命令(VR_STATE_CONFLICT), 已铸命令在收口后建出的空目录
  也不可写(write-boundary 判 RUN_NOT_ACTIVE / DECLARATION_REVOKED)。
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import platform
import posixpath
import re
import socket
import subprocess
import sys
import time
from typing import Any

FINGERPRINT_SCHEME = "hfp1"
_FINGERPRINT_RE = re.compile(r"^hfp1:[0-9a-f]{32}$")
_LINUX_MACHINE_ID_FILES = ("/etc/machine-id", "/var/lib/dbus/machine-id")
_DARWIN_UUID_RE = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')
_SEAL_RE = re.compile(r"^vri1:[0-9a-f]{32}$")
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
INIT_EVIDENCE = ".lybra-vr/init-run.jsonl"
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
# AIPOS-F191 目录链初始化(init-run): 只建空目录, 逐级拒软链与越界
# ---------------------------------------------------------------------------

def init_seal(base: str, run_dir: str, fingerprint: str, expires_at: str) -> str:
    """init-run 命令的完整性封印(治理根侧铸命令与主机侧核对同一实现): vri1:<sha256 前 32 位>。
    无密钥 = 只防手改参数(命令须原样取自产品), 不是凭据。"""
    payload = "\n".join(("lybra-vr-init-v1", base, run_dir, fingerprint, expires_at))
    return "vri1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def plan_init_chain(base: str, run_dir: str) -> dict[str, Any]:
    """只读规划目录链: {ok, code, reason, existing: [...], missing: [...], ancestor}。逐级(自 / 起)lstat:
    已存在的级不得是符号链接 / 非目录; base 及以下已存在的级属主须为当前用户; 第一个缺失级之上的最近已存在祖先真实路径须 = 字面
    且当前用户可写可进(只在有缺失级时要求)。"""
    def deny(code: str, reason: str) -> dict[str, Any]:
        return {"ok": False, "code": code, "reason": reason, "existing": [], "missing": [], "ancestor": None}

    for label, value in (("base", base), ("run 目录", run_dir)):
        problem = canonical_problem(value)
        if problem:
            return deny("PATH_NOT_CANONICAL", f"{label}{problem}")
    rel = run_dir[len(base):].lstrip("/").split("/") if is_within(run_dir, base) and run_dir != base else []
    if base == "/" or len(rel) != 2 or not all(rel):
        return deny("INIT_CHAIN_INVALID", f"run 目录 {run_dir} 须恰为 <base>/<卡段>/<run 段>(base = {base})")
    parts = run_dir.split("/")[1:]
    chain = ["/" + "/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    existing: list[str] = []
    missing: list[str] = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for path in chain:
        if missing:
            missing.append(path)
            continue
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            missing.append(path)
            continue
        except OSError as exc:
            return deny("CHAIN_UNREADABLE", f"{path} 无法检查({exc})")
        if os.path.islink(path):
            return deny("CHAIN_SYMLINK", f"{path} 是符号链接(目录链各级不得是软链, 防越界逃逸)")
        if not os.path.isdir(path):
            return deny("CHAIN_NOT_DIRECTORY", f"{path} 已存在但不是目录")
        if uid is not None and is_within(path, base) and st.st_uid != uid:
            return deny("CHAIN_NOT_OWNED", f"{path} 已存在但属主 uid={st.st_uid} ≠ 当前用户 uid={uid}(不接管他人目录)")
        existing.append(path)
    ancestor = existing[-1] if existing else "/"
    if missing:
        real = os.path.realpath(ancestor)
        if real != ancestor:
            return deny("CHAIN_SYMLINK", f"最近已存在祖先 {ancestor} 的真实路径为 {real}(路径含符号链接)")
        if not os.access(ancestor, os.W_OK | os.X_OK):
            return deny("ANCESTOR_NOT_WRITABLE",
                        f"最近已存在祖先 {ancestor} 当前用户不可写——需要提权的位置不能做验证根; 出口: 请顾问把声明 base 改到当前用户可写的位置"
                        "(revoke 后重新 declare), 禁 sudo / 禁换路径自建")
    return {"ok": True, "code": "OK", "reason": "", "existing": existing, "missing": missing, "ancestor": ancestor}


def init_run(base: str, run_dir: str) -> dict[str, Any]:
    """按 plan_init_chain 只用 mkdir(0700)建缺失级; 每建一级立即 lstat 复核(非目录 / 软链 = 中止)。
    返回 {ok, code, reason, created, existing, modes}。不写任何文件(证据由调用方写进 run 目录内)。"""
    plan = plan_init_chain(base, run_dir)
    if not plan["ok"]:
        return {**plan, "created": [], "modes": {}}
    created: list[str] = []
    for path in plan["missing"]:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass  # 并发已建: 下行 lstat 复核决定合规与否(不计入本次新建, 不改其权限)
        except OSError as exc:
            return {**plan, "ok": False, "code": "MKDIR_FAILED", "reason": f"mkdir {path} 失败({exc}); 已建 {created}", "created": created,
                    "modes": {}}
        else:
            created.append(path)
        if os.path.islink(path) or not os.path.isdir(path):
            return {**plan, "ok": False, "code": "CHAIN_SYMLINK", "reason": f"{path} 建后复核不是真实目录(并发替换?), 中止; 已建 {created}",
                    "created": created, "modes": {}}
        if path in created:
            os.chmod(path, 0o700)  # mkdir 的 mode 受 umask 影响; 新建级一律 0700
    existing = plan["existing"] + [p for p in plan["missing"] if p not in created]
    modes = {p: oct(os.lstat(p).st_mode & 0o7777) for p in [*existing, *created] if is_within(p, base)}
    return {**plan, "existing": existing, "created": created, "modes": modes}


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
    init = sub.add_parser("init-run", help="AIPOS-F191: create the empty directory chain <base>/<card>/<run-id> (mkdir only, 0700, refuse "
                                           "symlinks / non-directories / foreign owners / unwritable ancestors); command minted by the product")
    init.add_argument("--base", required=True)
    init.add_argument("--run-dir", required=True)
    init.add_argument("--expect-fingerprint", required=True)
    init.add_argument("--expires-at", required=True)
    init.add_argument("--seal", required=True)
    cmp_ = sub.add_parser("compare", help="Compare two snapshots; exit 0 unchanged, 4 changed")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    return parser


def _own_fingerprint_problem(expect: str) -> str | None:
    """主机指纹核对(check / init-run 同一口径): 返回 `[CODE] 原因` 或 None。"""
    if not is_fingerprint(expect):
        return f"[HOST_FINGERPRINT_INVALID] --expect-fingerprint {expect!r} 不是 hfp1 指纹"
    try:
        mine = host_fingerprint()
    except HostIdentityError as exc:
        return f"[HOST_IDENTITY_UNREADABLE] {exc}"
    if mine != expect:
        return f"[HOST_FINGERPRINT_MISMATCH] 本机指纹 {mine} ≠ 声明 {expect}(跨机调用, 本主机不是声明主机)"
    return None


def _init_run_cli(args: argparse.Namespace) -> int:
    problem = _own_fingerprint_problem(args.expect_fingerprint)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    if not _SEAL_RE.match(args.seal or "") or not _ISO_Z_RE.match(args.expires_at or ""):
        print("DENY [INIT_SEAL_MISMATCH] --seal / --expires-at 形不合(命令须原样取自产品)")
        return EXIT_DENY
    if init_seal(args.base, args.run_dir, args.expect_fingerprint, args.expires_at) != args.seal:
        print("DENY [INIT_SEAL_MISMATCH] 参数与产品铸的封印不符(base / run 目录 / 指纹 / 到期被改动; 命令须原样取自产品, "
              "只能建本实例本卡本活动 run 的目录链)")
        return EXIT_DENY
    try:
        expires = calendar.timegm(time.strptime(args.expires_at, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        print(f"DENY [INIT_SEAL_MISMATCH] --expires-at {args.expires_at!r} 不是合法时刻")
        return EXIT_DENY
    if expires <= time.time():
        print(f"DENY [DECLARATION_EXPIRED] 声明已于 {args.expires_at} 到期(请顾问重新声明 / 起 run)")
        return EXIT_DENY
    result = init_run(args.base, args.run_dir)
    modes = result.get("modes") or {}
    for path in result.get("existing") or []:
        if is_within(path, args.base):
            print(f"exists {path} {modes.get(path, '')}".rstrip())
    for path in result.get("created") or []:
        print(f"mkdir  {path} {modes.get(path, '')}".rstrip())
    if not result["ok"]:
        print(f"DENY [{result['code']}] {result['reason']}")
        return EXIT_DENY
    evidence = posixpath.join(args.run_dir, INIT_EVIDENCE)
    verdict = judge_realpath(args.run_dir, evidence)
    if not verdict["allowed"]:
        print(f"DENY [{verdict['code']}] 证据落点 {verdict['reason']}")
        return EXIT_DENY
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host_label(), "fingerprint": args.expect_fingerprint,
              "base": args.base, "run_dir": args.run_dir, "created": result["created"], "existing": result["existing"], "modes": result["modes"]}
    try:
        os.makedirs(posixpath.dirname(evidence), mode=0o700, exist_ok=True)
        with open(evidence, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"ERROR [INIT_EVIDENCE_UNWRITABLE] {evidence}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"INIT_OK run_dir={args.run_dir} created={len(result['created'])} evidence={evidence}")
    return EXIT_ALLOW


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "fingerprint":
        try:
            print(f"{host_fingerprint()} host={host_label()}")
        except HostIdentityError as exc:
            print(f"ERROR [HOST_IDENTITY_UNREADABLE] {exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_ALLOW
    if args.command == "init-run":
        return _init_run_cli(args)
    if args.command == "check":
        problem = _own_fingerprint_problem(args.expect_fingerprint)
        if problem:
            print(f"DENY {problem}")
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
