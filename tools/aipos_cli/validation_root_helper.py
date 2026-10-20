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

  AIPOS-F194 iOS 本 run 专用模拟器(Owner 10-20 定; 声明 roles.schema write_boundary.validation_root.ios_simulator; 只调
  `xcrun simctl create` / `xcrun simctl delete <本 run UDID>`, 不 boot / erase / delete 他台, 不 delete all / unavailable):
  python3 validation_root_helper.py sim-create --run-dir <run> --expect-fingerprint <hfp1:…> --device-type <设备型号 id> --runtime <运行时 id>
      在 Xcode 缺省设备集(~/Library/Developer/CoreSimulator/Devices)由本 run 新建一台 lybra-<卡ID>-<run-id>; 前后对 Devices
      顶层 UDID 清单拍快照, 新 UDID 与快照写 <run>/.lybra-vr/simulator.json; 末行 `SIM_CREATED udid=…` + 退出码 0 = 只多出这一台。
  python3 validation_root_helper.py sim-verify --run-dir <run> --expect-fingerprint <hfp1:…>
      运行中核验: 当前 Devices 顶层 = 建前清单 + 本 run 这一台(只多出这一台、未删他台); 末行 `SIM_OK` + 退出码 0。
  python3 validation_root_helper.py sim-delete --run-dir <run> --expect-fingerprint <hfp1:…>
      删除本 run 这一台(先核设备目录真实存在且 device.plist name = 本 run 名), 再比对「只少了这一台、其他设备未变」; 末行 `SIM_DELETED`。
  python3 validation_root_helper.py sys-snapshot --run-dir <run> --expect-fingerprint <hfp1:…> --phase before|after --pattern <glob>…
      Xcode 自写系统临时目录(Darwin 用户临时目录, `getconf DARWIN_USER_TEMP_DIR`; 或 --temp-dir)顶层匹配项的前后快照, 写
      <run>/.lybra-vr/xcode-side-effects.json; after 列出新增 / 删除 / 变更(系统副作用: 不删除、不冒充 run 产物), 退出码 0。
  三个 sim-* 与 sys-snapshot 全部 fail-closed: 退出码 3 = 拒(DENY 行给原因), 2 = 用法或环境错。

  AIPOS-F197 候选源码送达核对(源码由治理根所在机 `git -C <候选仓> archive --prefix=src/ <候选> | … tar -x -C <run>` 送入, 完整命令由产品铸):
  python3 - src-verify --run-dir <run> --candidate <候选 sha> --expect-tree <候选 tree> --expect-fingerprint <hfp1:…> --seal <vrs1:…>
      按 git 规则对 <run>/src 算 tree 指纹(根下 .git 不计), 须 = 候选 tree(混入改动 / 多出缺文件 / 错版本 = 拒); 证据追加
      <run>/.lybra-vr/src-verify.jsonl; 末行 `SRC_OK …` + 退出码 0 = 送达树即候选; 其余一律视为未送达。

  AIPOS-F200 远端 run 证据取回(审计 run 在目标主机时; 完整命令由产品铸: 开工稿「验证卡运行证据步骤」/ 章程验证根节):
  python3 - evidence-manifest --run-dir <run> --candidate <候选 sha> --expect-fingerprint <hfp1:…> --include <证据目录>… --skip-ext <扩展名>…
          --max-file-bytes <n> --max-total-bytes <n> --max-files <n> --seal <vre1:…>
      列 run 目录内声明证据目录下的文件(相对路径 / 大小 / sha256)+ 主机指纹 + 封签, 写 <run>/.lybra-vr/evidence-manifest.json;
      软链 / 非普通文件 / 超限 = 拒; 代码文件扩展名不取回(记 skipped)。末行 `EVIDENCE_OK manifest=… sha256=…` + 退出码 0。
  python3 - evidence-bundle --run-dir <run> --expect-fingerprint <hfp1:…>
      只读: 核清单(形 / 封签 / run 目录 / 指纹)与清单内每个文件(不跟软链、真实路径在 run 内、大小与 sha256 一致), 全过才向 stdout
      写 tar 流(manifest.json + files/<相对路径>); 由治理根所在机 `lybra project validation-root evidence-fetch` 经 ssh 别名调用。

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
import fnmatch
import hashlib
import json
import os
import platform
import plistlib
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
# AIPOS-F194: 本 run 专用模拟器(值 = roles.schema write_boundary.validation_root.ios_simulator 同名键; 本文件在目标主机脱离产品运行,
# 不读 schema, 两处一致由测试 test_aipos_f194 断言)
SIM_NAME_TEMPLATE = "lybra-{task_id}-{run_id}"
SIM_DEVICES_REL = "Library/Developer/CoreSimulator/Devices"
SIM_EVIDENCE = ".lybra-vr/simulator.json"
SIDE_EVIDENCE = ".lybra-vr/xcode-side-effects.json"
SIM_EVIDENCE_SCHEMA = "lybra-vr-sim-v1"
SIDE_EVIDENCE_SCHEMA = "lybra-vr-side-v1"
_UDID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
SIMCTL_TIMEOUT_SECONDS = 600


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
# AIPOS-F197 件④: 候选源码送达核对(src-verify): run 目录内 src/ 的 git tree 指纹 = 候选 tree(不含未提交改动 / 错版本)
# ---------------------------------------------------------------------------

SRC_DIR_NAME = "src"
SRC_EVIDENCE = ".lybra-vr/src-verify.jsonl"
_SRC_SEAL_RE = re.compile(r"^vrs1:[0-9a-f]{32}$")
_TREE_RE = re.compile(r"^[0-9a-f]{40}$")


def src_seal(run_dir: str, candidate: str, tree: str, fingerprint: str) -> str:
    """src-verify 命令的完整性封印(治理根侧铸命令与主机侧核对同一实现; 同 init_seal: 无密钥, 只防手改参数)。"""
    payload = "\n".join(("lybra-vr-src-v1", run_dir, candidate, tree, fingerprint))
    return "vrs1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _git_object(kind: bytes, data: bytes) -> str:
    return hashlib.sha1(kind + b" " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def git_tree_hash(root: str) -> dict[str, Any]:
    """目录树按 git 规则算 tree 指纹(纯标准库, 目标主机零安装): 普通文件 = blob(100644, 属主可执行 = 100755), 符号链接 = blob(链接目标,
    120000), 目录 = 子 tree(40000; 空目录 git 不记, 跳过); 条目按 git 序(目录名按「名/」比较)。根下 `.git` 不计入(记 git_dir_present)。
    其他类型(设备 / fifo / socket)= problems(fail-closed)。返回 {tree, files, symlinks, git_dir_present, problems}。"""
    import stat as stat_mod

    out: dict[str, Any] = {"tree": None, "files": 0, "symlinks": 0, "git_dir_present": False, "problems": []}

    def tree_of(directory: str, top: bool) -> str | None:
        entries: list[tuple[bytes, bytes, str]] = []
        for name in os.listdir(directory):
            if top and name == ".git":
                out["git_dir_present"] = True
                continue
            path = os.path.join(directory, name)
            st = os.lstat(path)
            raw = os.fsencode(name)
            if stat_mod.S_ISLNK(st.st_mode):
                entries.append((raw, b"120000", _git_object(b"blob", os.fsencode(os.readlink(path)))))
                out["symlinks"] += 1
            elif stat_mod.S_ISDIR(st.st_mode):
                sub = tree_of(path, False)
                if sub is not None:
                    entries.append((raw + b"/", b"40000", sub))
            elif stat_mod.S_ISREG(st.st_mode):
                with open(path, "rb") as handle:
                    data = handle.read()
                entries.append((raw, b"100755" if st.st_mode & stat_mod.S_IXUSR else b"100644", _git_object(b"blob", data)))
                out["files"] += 1
            else:
                out["problems"].append(f"{path}: 非普通文件 / 目录 / 符号链接(mode {oct(st.st_mode)})")
        if not entries:
            return None
        entries.sort(key=lambda e: e[0])
        body = b"".join(mode + b" " + (name[:-1] if name.endswith(b"/") else name) + b"\0" + bytes.fromhex(sha)
                        for name, mode, sha in entries)
        return _git_object(b"tree", body)

    out["tree"] = tree_of(root, True) or _git_object(b"tree", b"")
    return out


def _src_verify_cli(args: argparse.Namespace) -> int:
    """src-verify: 核本 run 的 src/ = 候选 tree(封印核参数 → run 目录合规 → 算 tree → 比对), 证据追加 run 目录 .lybra-vr/src-verify.jsonl。"""
    if not _SRC_SEAL_RE.match(args.seal or "") or not _TREE_RE.match(args.expect_tree or "") or not _TREE_RE.match(args.candidate or ""):
        print("DENY [SRC_SEAL_MISMATCH] --seal / --expect-tree / --candidate 形不合(命令须原样取自产品)")
        return EXIT_DENY
    if src_seal(args.run_dir, args.candidate, args.expect_tree, args.expect_fingerprint) != args.seal:
        print("DENY [SRC_SEAL_MISMATCH] 参数与产品铸的封印不符(run 目录 / 候选 / 期望 tree / 指纹被改动; 命令须原样取自产品)")
        return EXIT_DENY
    problem = _run_dir_problem(args.run_dir, SRC_EVIDENCE)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    src = posixpath.join(args.run_dir, SRC_DIR_NAME)
    verdict = judge_realpath(args.run_dir, src)
    if not verdict["allowed"]:
        print(f"DENY [{verdict['code']}] {verdict['reason']}")
        return EXIT_DENY
    if os.path.islink(src) or not os.path.isdir(src):
        print(f"DENY [SRC_MISSING] {src} 不存在或不是真实目录(先按开工稿运行候选源码送达命令)")
        return EXIT_DENY
    got = git_tree_hash(src)
    ok = not got["problems"] and got["tree"] == args.expect_tree
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host_label(), "fingerprint": args.expect_fingerprint,
              "run_dir": args.run_dir, "src": src, "candidate": args.candidate, "expect_tree": args.expect_tree, "actual_tree": got["tree"],
              "files": got["files"], "symlinks": got["symlinks"], "git_dir_present": got["git_dir_present"], "problems": got["problems"],
              "ok": ok}
    evidence = posixpath.join(args.run_dir, SRC_EVIDENCE)
    try:
        os.makedirs(posixpath.dirname(evidence), mode=0o700, exist_ok=True)
        with open(evidence, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"ERROR [SRC_EVIDENCE_UNWRITABLE] {evidence}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not ok:
        why = "; ".join(got["problems"]) if got["problems"] else (
            f"送达树 tree={got['tree']} ≠ 候选 {args.candidate} 的 tree {args.expect_tree}(混入未提交改动 / 多出或缺文件 / 送错版本)")
        print(f"DENY [SRC_TREE_MISMATCH] {why}; 证据 {evidence}; 出口: 删掉 {src} 后按产品给的送达命令重送(禁手改 src 凑指纹)")
        return EXIT_DENY
    print(f"SRC_OK candidate={args.candidate} tree={got['tree']} files={got['files']} evidence={evidence}")
    return EXIT_ALLOW


# ---------------------------------------------------------------------------
# AIPOS-F200 远端 run 证据清单(evidence-manifest)与只读打包(evidence-bundle): 审计 run 在目标主机时, 治理根所在机经声明 ssh 别名
# 取回清单内证据到审计报告落点副本, 裁决入口对副本做与本机 run 同一核对(清单 / 封签 / 副本核对 = 本节 + validation_root.evidence_copy_check)
# ---------------------------------------------------------------------------

EVIDENCE_MANIFEST = ".lybra-vr/evidence-manifest.json"
EVIDENCE_SCHEMA = "lybra-vr-evidence-v1"
_EVIDENCE_SEAL_RE = re.compile(r"^vre1:[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_MANIFEST_KEYS = ("schema", "at", "host", "fingerprint", "run_dir", "task_id", "run_id", "candidate", "include", "skip_ext", "limits",
                          "seal", "files", "skipped", "total_bytes")
EVIDENCE_LIMIT_KEYS = ("max_file_bytes", "max_total_bytes", "max_files")


def evidence_seal(run_dir: str, candidate: str, fingerprint: str, include: list[str], skip_ext: list[str], limits: dict[str, int]) -> str:
    """evidence-manifest 命令的完整性封印(治理根侧铸命令、主机侧核对、取回与裁决入口复核同一实现; 同 init_seal: 无密钥, 只防手改参数)。"""
    payload = "\n".join(("lybra-vr-evidence-v1", run_dir, candidate, fingerprint, ",".join(include), ",".join(skip_ext),
                         *(str(limits.get(k)) for k in EVIDENCE_LIMIT_KEYS)))
    return "vre1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def evidence_rel_problem(rel: Any, include: list[str]) -> str | None:
    """清单内相对路径判据(主机侧打包与治理根侧副本核对同一实现): 规范相对路径、落在声明证据目录内、不是清单自身。"""
    if not isinstance(rel, str) or not rel or rel.startswith("/"):
        return f"清单路径 {rel!r} 须为相对路径"
    problem = canonical_problem("/" + rel)
    if problem:
        return f"清单路径 {rel!r} 不规范({problem})"
    if rel == EVIDENCE_MANIFEST:
        return f"清单路径 {rel!r} 是清单自身"
    if not any(is_within("/" + rel, "/" + d) and rel != d for d in include):
        return f"清单路径 {rel!r} 不在证据目录 {include} 内(越界)"
    return None


def evidence_manifest_problems(manifest: Any, *, run_dir: str, fingerprint: str) -> list[str]:
    """清单形 + 封签自洽 + run 目录 / 主机指纹核对(主机侧打包与治理根侧副本核对同一实现)。返回问题原文(空 = 通过)。"""
    if not isinstance(manifest, dict) or set(manifest) != set(EVIDENCE_MANIFEST_KEYS):
        keys = sorted(manifest) if isinstance(manifest, dict) else type(manifest).__name__
        return [f"证据清单键须恰为 {list(EVIDENCE_MANIFEST_KEYS)}, 实为 {keys}"]
    problems: list[str] = []
    if manifest["schema"] != EVIDENCE_SCHEMA:
        problems.append(f"证据清单 schema={manifest['schema']!r} ≠ {EVIDENCE_SCHEMA}")
    include, skip_ext, limits = manifest["include"], manifest["skip_ext"], manifest["limits"]
    if (not isinstance(include, list) or not all(isinstance(d, str) and d for d in include) or not isinstance(skip_ext, list)
            or not all(isinstance(e, str) for e in skip_ext) or not isinstance(limits, dict) or set(limits) != set(EVIDENCE_LIMIT_KEYS)
            or not all(isinstance(limits[k], int) and not isinstance(limits[k], bool) and limits[k] > 0 for k in EVIDENCE_LIMIT_KEYS)):
        return problems + ["证据清单 include / skip_ext / limits 形不合"]
    for key, want in (("run_dir", run_dir), ("fingerprint", fingerprint)):
        if manifest[key] != want:
            problems.append(f"证据清单 {key}={manifest[key]!r} ≠ {want!r}")
    candidate = manifest["candidate"]
    if not isinstance(candidate, str) or not _TREE_RE.match(candidate):
        problems.append(f"证据清单 candidate={candidate!r} 不是 40 位 sha")
    elif manifest["seal"] != evidence_seal(manifest["run_dir"], candidate, manifest["fingerprint"], include, skip_ext, limits):
        problems.append("证据清单封签不符(run 目录 / 候选 / 主机指纹 / 证据目录 / 上限被改动; 清单须由产品铸的 evidence-manifest 命令生成)")
    files = manifest["files"]
    if not isinstance(files, list):
        return problems + ["证据清单 files 须为列表"]
    seen: set[str] = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            problems.append(f"证据清单条目 {item!r} 键须恰为 path / size / sha256")
            continue
        rel_problem = evidence_rel_problem(item["path"], include)
        if rel_problem:
            problems.append(rel_problem)
            continue
        if item["path"] in seen:
            problems.append(f"清单路径 {item['path']!r} 重复")
        seen.add(item["path"])
        if not isinstance(item["size"], int) or isinstance(item["size"], bool) or item["size"] < 0 or not _SHA256_RE.match(str(item["sha256"])):
            problems.append(f"清单条目 {item['path']!r} size / sha256 形不合")
            continue
        if item["size"] > limits["max_file_bytes"]:
            problems.append(f"清单条目 {item['path']!r} 大小 {item['size']} 超单文件上限 {limits['max_file_bytes']}")
        total += item["size"]
    if len(files) > limits["max_files"]:
        problems.append(f"清单文件数 {len(files)} 超上限 {limits['max_files']}")
    if total > limits["max_total_bytes"] or manifest["total_bytes"] != total:
        problems.append(f"清单总字节 {total}(自述 {manifest['total_bytes']!r})超上限 {limits['max_total_bytes']} 或自述不符")
    return problems


def _read_nofollow(path: str) -> bytes:
    """只读一个普通文件(O_NOFOLLOW: 末段是符号链接 = OSError; 非普通文件 = OSError)。"""
    import stat as stat_mod

    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat_mod.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} 不是普通文件")
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def build_evidence_manifest(run_dir: str, *, candidate: str, fingerprint: str, include: list[str], skip_ext: list[str],
                            limits: dict[str, int]) -> dict[str, Any]:
    """在本机列 run 目录内声明证据目录(include)下的文件(相对路径 / 大小 / sha256)。符号链接 / 非普通文件 / 越界 / 超限 = 拒(fail-closed);
    扩展名在 skip_ext(治理仓代码文件黑名单)的文件不取回, 记入 skipped(裁决入口引用它即「不在清单内」拒)。返回 {ok, code, reason, manifest}。"""
    import stat as stat_mod

    def deny(code: str, reason: str) -> dict[str, Any]:
        return {"ok": False, "code": code, "reason": reason, "manifest": None}

    parts = run_dir.rstrip("/").split("/")
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total = 0
    for rel_dir in include:
        problem = canonical_problem("/" + rel_dir) if rel_dir and not rel_dir.startswith("/") else f"证据目录 {rel_dir!r} 须为相对路径"
        if problem:
            return deny("EVIDENCE_PATH_INVALID", f"证据目录 {rel_dir!r}: {problem}")
        top = posixpath.join(run_dir, rel_dir)
        if not os.path.lexists(top):
            continue
        verdict = judge_realpath(run_dir, top)
        if not verdict["allowed"]:
            return deny("EVIDENCE_SYMLINK" if verdict["code"] in ("SYMLINK_ESCAPE", "RUN_DIR_SYMLINK") else verdict["code"],
                        f"证据目录 {top}: {verdict['reason']}")
        if os.path.islink(top) or not os.path.isdir(top):
            return deny("EVIDENCE_SYMLINK", f"证据目录 {top} 是符号链接或不是目录")
        for current, dirs, names in os.walk(top, followlinks=False):
            dirs.sort()
            for name in [*sorted(dirs), *sorted(names)]:
                full = posixpath.join(current, name)
                st = os.lstat(full)
                rel = full[len(run_dir) + 1:]
                if stat_mod.S_ISLNK(st.st_mode):
                    return deny("EVIDENCE_SYMLINK", f"{full} 是符号链接(证据目录内不得有软链, 防越界取回)")
                if stat_mod.S_ISDIR(st.st_mode):
                    continue
                if not stat_mod.S_ISREG(st.st_mode):
                    return deny("EVIDENCE_NOT_REGULAR", f"{full} 不是普通文件(mode {oct(st.st_mode)})")
                if rel == EVIDENCE_MANIFEST or rel == EVIDENCE_MANIFEST + ".tmp":
                    continue
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext and ext in skip_ext:
                    skipped.append({"path": rel, "reason": f"扩展名 .{ext} 属治理仓代码文件黑名单, 不进审计报告落点(须作证据 = 改存为 {name}.txt)"})
                    continue
                if st.st_size > limits["max_file_bytes"]:
                    return deny("EVIDENCE_TOO_LARGE", f"{full} 大小 {st.st_size} 超单文件上限 {limits['max_file_bytes']}"
                                                      "(roles.schema validation_root.run_evidence.max_file_bytes)")
                data = _read_nofollow(full)
                files.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                total += len(data)
                if len(files) > limits["max_files"] or total > limits["max_total_bytes"]:
                    return deny("EVIDENCE_TOO_LARGE", f"证据合计超上限(文件数 {len(files)} / {limits['max_files']}, 字节 {total} / "
                                                      f"{limits['max_total_bytes']}; roles.schema validation_root.run_evidence)")
    files.sort(key=lambda f: f["path"])
    manifest = {"schema": EVIDENCE_SCHEMA, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host_label(),
                "fingerprint": fingerprint, "run_dir": run_dir, "task_id": parts[-2], "run_id": parts[-1], "candidate": candidate,
                "include": list(include), "skip_ext": list(skip_ext), "limits": dict(limits),
                "seal": evidence_seal(run_dir, candidate, fingerprint, include, skip_ext, limits), "files": files, "skipped": skipped,
                "total_bytes": total}
    return {"ok": True, "code": "OK", "reason": "", "manifest": manifest}


def _evidence_limits(args: argparse.Namespace) -> dict[str, int]:
    return {"max_file_bytes": args.max_file_bytes, "max_total_bytes": args.max_total_bytes, "max_files": args.max_files}


def _evidence_manifest_cli(args: argparse.Namespace) -> int:
    """evidence-manifest: 封印核参数 → run 目录合规 → 列证据 → 清单写 <run>/.lybra-vr/evidence-manifest.json; 末行 EVIDENCE_OK。"""
    limits = _evidence_limits(args)
    if (not _EVIDENCE_SEAL_RE.match(args.seal or "") or not _TREE_RE.match(args.candidate or "")
            or any(v <= 0 for v in limits.values())):
        print("DENY [EVIDENCE_SEAL_MISMATCH] --seal / --candidate / 上限形不合(命令须原样取自产品)")
        return EXIT_DENY
    if evidence_seal(args.run_dir, args.candidate, args.expect_fingerprint, args.include, args.skip_ext, limits) != args.seal:
        print("DENY [EVIDENCE_SEAL_MISMATCH] 参数与产品铸的封印不符(run 目录 / 候选 / 指纹 / 证据目录 / 上限被改动; 命令须原样取自产品)")
        return EXIT_DENY
    problem = _run_dir_problem(args.run_dir, EVIDENCE_MANIFEST)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    result = build_evidence_manifest(args.run_dir, candidate=args.candidate, fingerprint=args.expect_fingerprint, include=args.include,
                                     skip_ext=args.skip_ext, limits=limits)
    if not result["ok"]:
        print(f"DENY [{result['code']}] {result['reason']}")
        return EXIT_DENY
    manifest = result["manifest"]
    problem = _write_json_in_run(args.run_dir, EVIDENCE_MANIFEST, manifest)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    path = posixpath.join(args.run_dir, EVIDENCE_MANIFEST)
    digest = hashlib.sha256(_read_nofollow(path)).hexdigest()
    for item in manifest["skipped"]:
        print(f"skipped {item['path']}: {item['reason']}")
    print(f"EVIDENCE_OK manifest={path} sha256={digest} files={len(manifest['files'])} bytes={manifest['total_bytes']} "
          f"fingerprint={manifest['fingerprint']}")
    return EXIT_ALLOW


def _evidence_bundle_cli(args: argparse.Namespace) -> int:
    """evidence-bundle(只读): 读本 run 清单 → 清单形 / 封签 / run 目录 / 指纹核对 → 逐个核清单内文件(O_NOFOLLOW、真实路径在 run 内、
    大小与 sha256 = 清单)→ 全部通过才向 stdout 写 tar 流(manifest.json + files/<相对路径>); 任一不过 = stderr 写 DENY、stdout 无输出、退出 3。"""
    import io
    import tarfile

    def deny(code: str, reason: str) -> int:
        print(f"DENY [{code}] {reason}", file=sys.stderr)
        return EXIT_DENY

    problem = _run_dir_problem(args.run_dir, EVIDENCE_MANIFEST)
    if problem:
        return deny("RUN_DIR_INVALID", problem)
    path = posixpath.join(args.run_dir, EVIDENCE_MANIFEST)
    try:
        raw = _read_nofollow(path)
    except FileNotFoundError:
        return deny("EVIDENCE_MANIFEST_MISSING", f"{path} 不存在(先在目标主机运行产品铸的 evidence-manifest 命令生成证据清单)")
    except OSError as exc:
        return deny("EVIDENCE_SYMLINK", f"{path} 读不出或不是普通文件({exc})")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        return deny("EVIDENCE_MANIFEST_INVALID", f"{path} 不是 JSON({exc})")
    problems = evidence_manifest_problems(manifest, run_dir=args.run_dir, fingerprint=args.expect_fingerprint)
    if problems:
        return deny("EVIDENCE_MANIFEST_INVALID", "; ".join(problems))
    payloads: list[tuple[str, bytes]] = []
    for item in manifest["files"]:
        full = posixpath.join(args.run_dir, item["path"])
        verdict = judge_realpath(args.run_dir, full)
        if not verdict["allowed"]:
            return deny("EVIDENCE_SYMLINK", f"{item['path']}: {verdict['reason']}")
        try:
            data = _read_nofollow(full)
        except FileNotFoundError:
            return deny("EVIDENCE_FILE_MISSING", f"清单内文件 {full} 不存在(清单生成后被删)")
        except OSError as exc:
            return deny("EVIDENCE_SYMLINK", f"清单内文件 {full} 是符号链接或不是普通文件({exc})")
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != item["size"] or digest != item["sha256"]:
            return deny("EVIDENCE_FILE_CHANGED", f"清单内文件 {full} 现 sha256={digest[:12]} 大小 {len(data)} ≠ 清单 {item['sha256'][:12]} / "
                                                 f"{item['size']}(清单生成后被改; 重新生成清单须在 run 活动时由产品命令完成)")
        payloads.append((f"files/{item['path']}", data))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in [("manifest.json", raw), *payloads]:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime, info.type = len(data), 0o600, 0, tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    sys.stdout.buffer.write(buffer.getvalue())
    sys.stdout.buffer.flush()
    print(f"BUNDLE_OK files={len(payloads)} bytes={manifest['total_bytes']}", file=sys.stderr)
    return EXIT_ALLOW


# ---------------------------------------------------------------------------
# AIPOS-F194 iOS 本 run 专用模拟器: 缺省设备集内本 run 新建一台, 只写该 UDID, 结束删除, 前后比对
# ---------------------------------------------------------------------------

def is_udid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UDID_RE.match(value))


def sim_name(run_dir: str) -> str:
    """本 run 专用模拟器名 = lybra-<卡ID>-<run-id>(取 run 目录末两段; 声明 ios_simulator.name_template)。"""
    parts = run_dir.rstrip("/").split("/")
    if len(parts) < 3 or not parts[-1] or not parts[-2]:
        raise ValueError(f"run 目录 {run_dir!r} 须为 <base>/<卡ID>/<run-id>")
    return SIM_NAME_TEMPLATE.replace("{task_id}", parts[-2]).replace("{run_id}", parts[-1])


def devices_dir(home: str | None = None) -> str:
    """Xcode 缺省设备集目录 = <用户主目录>/Library/Developer/CoreSimulator/Devices(home 缺省 = 本机 ~)。"""
    return posixpath.join(home if home is not None else os.path.expanduser("~"), SIM_DEVICES_REL)


def devices_listing(path: str) -> dict[str, Any]:
    """Devices 顶层快照(只读, 不跟随符号链接): udids = UDID 形目录名(设备); service = 其余顶层项(如 device_set.plist,
    CoreSimulator 服务维护性写入 = 系统服务副作用){名: [类型, 大小, mtime 整秒]}。目录不存在 = exists false(如实记)。"""
    if not os.path.isdir(path):
        return {"exists": False, "udids": [], "service": {}}
    udids: list[str] = []
    service: dict[str, list[Any]] = {}
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        st = os.lstat(full)
        kind = "link" if os.path.islink(full) else "dir" if os.path.isdir(full) else "file"
        if kind == "dir" and is_udid(name):
            udids.append(name)
        else:
            service[name] = [kind, int(st.st_size), int(st.st_mtime)]
    return {"exists": True, "udids": udids, "service": service}


def listing_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """两次顶层快照之差: added / removed(UDID), service(服务维护项变化 +/-/~ 名)。"""
    b, a = set(before.get("udids") or []), set(after.get("udids") or [])
    bs, as_ = before.get("service") or {}, after.get("service") or {}
    service = [f"+ {n}" for n in sorted(set(as_) - set(bs))] + [f"- {n}" for n in sorted(set(bs) - set(as_))]
    service += [f"~ {n}" for n in sorted(set(as_) & set(bs)) if as_[n] != bs[n]]
    return {"added": sorted(a - b), "removed": sorted(b - a), "service": service}


def device_plist_name(device_dir: str) -> tuple[str | None, str | None]:
    """读 <设备目录>/device.plist 的 name(plistlib, XML / 二进制均可)。返回 (name, 问题)。"""
    path = os.path.join(device_dir, "device.plist")
    try:
        with open(path, "rb") as handle:
            data = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        return None, f"{path} 读不出({exc})"
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name:
        return None, f"{path} 无 name 键"
    return name, None


def verify_sim_evidence(evidence: Any, *, run_dir: str, expect_udid: str | None = None, require_deleted: bool = True,
                        current: dict[str, Any] | None = None) -> dict[str, Any]:
    """本 run 专用模拟器证据核对(目标主机 sim-* 与治理根侧 run-finish 同一实现; 只读纯函数)。
    判据: ①证据形 / run 目录 / 名 = lybra-<卡>-<run> / UDID 形与(给出时)= 绑定 UDID; ②建: simctl 退出 0, 建前→建后只多出这一台、
    未少任何一台, device.plist name = 本 run 名; ③(require_deleted)删: simctl 退出 0, 删前有这一台, 删前→删后只少这一台、未多出,
    且删后清单 = 建前清单(其他设备未变); ④(current 给出 = 运行中核验)当前清单 = 建前清单 + 这一台。
    返回 {ok, problems: [...], service: [...](服务维护项变化, 记为系统服务副作用, 不判失败), udid, name}。"""
    problems: list[str] = []
    service: list[str] = []
    if not isinstance(evidence, dict) or evidence.get("schema") != SIM_EVIDENCE_SCHEMA:
        return {"ok": False, "problems": [f"证据不是 {SIM_EVIDENCE_SCHEMA}(须原样取自 helper sim-create 写的 {SIM_EVIDENCE})"], "service": [],
                "udid": None, "name": None}
    try:
        want_name = sim_name(run_dir)
    except ValueError as exc:
        return {"ok": False, "problems": [str(exc)], "service": [], "udid": None, "name": None}
    udid, name = evidence.get("udid"), evidence.get("name")
    if evidence.get("run_dir") != run_dir:
        problems.append(f"证据 run 目录 {evidence.get('run_dir')!r} ≠ 本 run {run_dir}(他 run 的证据)")
    if name != want_name:
        problems.append(f"证据模拟器名 {name!r} ≠ 本 run 名 {want_name}")
    if not is_udid(udid):
        problems.append(f"证据 UDID {udid!r} 不是 UDID(simctl create 未成功或输出不合)")
    elif expect_udid is not None and udid != expect_udid:
        problems.append(f"证据 UDID {udid} ≠ 本 run 绑定的 {expect_udid}(他台 / 他 run 的模拟器)")
    create = evidence.get("create") if isinstance(evidence.get("create"), dict) else None
    if create is None:
        problems.append("证据缺建台记录 create")
        return {"ok": False, "problems": problems, "service": service, "udid": udid, "name": name}
    if create.get("exit") != 0:
        problems.append(f"simctl create 退出码 {create.get('exit')!r}(≠ 0): {str(create.get('stderr') or '').strip()[:300]}")
    d = listing_diff(create.get("before") or {}, create.get("after") or {})
    service += [f"建台 {x}" for x in d["service"]]
    if d["added"] != ([udid] if is_udid(udid) else []):
        problems.append(f"建台前后多出的设备 = {d['added'] or '(无)'}, 应只多出本 run 这一台 {udid}(多出他台 = 拒)")
    if d["removed"]:
        problems.append(f"建台前后少了设备 {d['removed']}(不得删除 / 改动他台)")
    if create.get("device_name") != want_name:
        problems.append(f"新设备 device.plist name = {create.get('device_name')!r}, 应为 {want_name}"
                        + (f"({create.get('device_name_problem')})" if create.get("device_name_problem") else ""))
    origin = sorted(create.get("before", {}).get("udids") or [])
    if current is not None:
        cd = listing_diff(create.get("before") or {}, current)
        service += [f"运行中 {x}" for x in cd["service"]]
        if cd["added"] != ([udid] if is_udid(udid) else []) or cd["removed"]:
            problems.append(f"运行中 Devices 相对建前: 多出 {cd['added'] or '(无)'} / 少了 {cd['removed'] or '(无)'}; 应只多出本 run 这一台 {udid}")
    if require_deleted:
        delete = evidence.get("delete") if isinstance(evidence.get("delete"), dict) else None
        if delete is None:
            problems.append(f"本 run 专用模拟器 {udid} 未删除(证据无删除记录; 先在目标主机 sim-delete)")
        else:
            if delete.get("exit") != 0:
                problems.append(f"simctl delete 退出码 {delete.get('exit')!r}(≠ 0): {str(delete.get('stderr') or '').strip()[:300]}")
            dd = listing_diff(delete.get("before") or {}, delete.get("after") or {})
            service += [f"删台 {x}" for x in dd["service"]]
            if udid not in (delete.get("before") or {}).get("udids", []):
                problems.append(f"删台前清单无本 run 这一台 {udid}")
            if dd["removed"] != ([udid] if is_udid(udid) else []):
                problems.append(f"删台前后少了的设备 = {dd['removed'] or '(无)'}, 应只少本 run 这一台 {udid}(删错他台 / 未删除 = 拒)")
            if dd["added"]:
                problems.append(f"删台前后多出设备 {dd['added']}")
            final = sorted((delete.get("after") or {}).get("udids") or [])
            if final != origin:
                extra, gone = sorted(set(final) - set(origin)), sorted(set(origin) - set(final))
                problems.append(f"删台后清单 ≠ 建台前清单(其他设备有变: 多 {extra or '(无)'} / 少 {gone or '(无)'}); 若确认非本 run 所为, "
                                "由顾问在报告写明并撤销声明处理, 不放行收口")
    return {"ok": not problems, "problems": problems, "service": service, "udid": udid, "name": name}


def _simctl(*args: str) -> dict[str, Any]:
    """只调 `xcrun simctl create|delete`(调用方保证参数); 返回 {argv, exit, stdout, stderr}。工具缺 = exit None + 原因。"""
    argv = ["xcrun", "simctl", *args]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=SIMCTL_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"argv": argv, "exit": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {"argv": argv, "exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def _write_json_in_run(run_dir: str, rel: str, data: Any) -> str | None:
    """证据只写进本 run 目录(落点先经 judge_realpath); 返回问题原文或 None。"""
    path = posixpath.join(run_dir, rel)
    verdict = judge_realpath(run_dir, path)
    if not verdict["allowed"]:
        return f"[{verdict['code']}] 证据落点 {verdict['reason']}"
    os.makedirs(posixpath.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)
    return None


def _read_json(path: str) -> tuple[Any, str | None]:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), None
    except FileNotFoundError:
        return None, f"{path} 不存在"
    except (OSError, ValueError) as exc:
        return None, f"{path} 读不出({exc})"


def _run_dir_problem(run_dir: str, rel: str) -> str | None:
    """sim-* / sys-snapshot 前置: run 目录规范、已由 init-run 建出、真实路径 = 字面, 证据落点在 run 目录内。"""
    verdict = judge_realpath(run_dir, posixpath.join(run_dir, rel))
    if not verdict["allowed"]:
        return f"[{verdict['code']}] {verdict['reason']}"
    if not os.path.isdir(run_dir):
        return f"[RUN_DIR_MISSING] run 目录 {run_dir} 不存在(先按开工稿运行 init-run 建目录链)"
    return None


def _sim_create_cli(args: argparse.Namespace) -> int:
    problem = _run_dir_problem(args.run_dir, SIM_EVIDENCE)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    evidence_path = posixpath.join(args.run_dir, SIM_EVIDENCE)
    if os.path.lexists(evidence_path):
        print(f"DENY [SIM_ALREADY_CREATED] 本 run 已建过专用模拟器(证据 {evidence_path}); 一个 run 只建一台, 不重建 / 不覆盖证据")
        return EXIT_DENY
    for label, value in (("--device-type", args.device_type), ("--runtime", args.runtime)):
        if not value or value.startswith("-") or any(ch.isspace() for ch in value):
            print(f"DENY [SIM_ARGS_INVALID] {label} {value!r} 须为单个标识(xcrun simctl list devicetypes / runtimes 所列 id)")
            return EXIT_DENY
    name = sim_name(args.run_dir)
    ddir = devices_dir()
    before = devices_listing(ddir)
    result = _simctl("create", name, args.device_type, args.runtime)
    out_lines = [ln.strip() for ln in str(result["stdout"] or "").splitlines() if ln.strip()]
    udid = out_lines[-1] if result["exit"] == 0 and out_lines and is_udid(out_lines[-1]) else None
    after = devices_listing(ddir)
    device_name, name_problem = (None, "未取得 UDID")
    if udid is not None:
        device = posixpath.join(ddir, udid)
        if os.path.islink(device) or not os.path.isdir(device):
            name_problem = f"{device} 不是真实目录"
        else:
            device_name, name_problem = device_plist_name(device)
    evidence = {"schema": SIM_EVIDENCE_SCHEMA, "run_dir": args.run_dir, "name": name, "udid": udid, "device_type": args.device_type,
                "runtime": args.runtime, "devices_dir": ddir, "host": host_label(), "fingerprint": args.expect_fingerprint,
                "create": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **result, "before": before, "after": after, "device_name": device_name,
                           "device_name_problem": name_problem},
                "delete": None, "verifies": []}
    problem = _write_json_in_run(args.run_dir, SIM_EVIDENCE, evidence)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    verdict = verify_sim_evidence(evidence, run_dir=args.run_dir, require_deleted=False)
    d = listing_diff(before, after)
    print(f"devices {ddir}: 建前 {len(before['udids'])} 台 → 建后 {len(after['udids'])} 台; 多出 {d['added'] or '(无)'} 少了 {d['removed'] or '(无)'}")
    for line in verdict["service"]:
        print(f"service-side-effect {line}")
    if not verdict["ok"]:
        for line in verdict["problems"]:
            print(f"DENY [SIM_CREATE_UNVERIFIED] {line}")
        print(f"证据 {posixpath.join(args.run_dir, SIM_EVIDENCE)}(已建出的本 run 设备如有, 只能经 sim-delete 删除; 不碰他台)")
        return EXIT_DENY
    print(f"SIM_CREATED udid={udid} name={name} evidence={posixpath.join(args.run_dir, SIM_EVIDENCE)}")
    return EXIT_ALLOW


def _load_sim_evidence(run_dir: str) -> tuple[dict[str, Any] | None, str | None]:
    problem = _run_dir_problem(run_dir, SIM_EVIDENCE)
    if problem:
        return None, problem
    data, problem = _read_json(posixpath.join(run_dir, SIM_EVIDENCE))
    if problem:
        return None, f"[SIM_EVIDENCE_MISSING] {problem}(本 run 未经 sim-create 建台)"
    if not isinstance(data, dict) or data.get("schema") != SIM_EVIDENCE_SCHEMA:
        return None, f"[SIM_EVIDENCE_INVALID] 证据形不合(须为 {SIM_EVIDENCE_SCHEMA})"
    return data, None


def _sim_verify_cli(args: argparse.Namespace) -> int:
    evidence, problem = _load_sim_evidence(args.run_dir)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    current = devices_listing(str(evidence.get("devices_dir") or devices_dir()))
    verdict = verify_sim_evidence(evidence, run_dir=args.run_dir, require_deleted=False, current=current)
    evidence.setdefault("verifies", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "current": current, "problems": verdict["problems"]})
    problem = _write_json_in_run(args.run_dir, SIM_EVIDENCE, evidence)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    for line in verdict["service"]:
        print(f"service-side-effect {line}")
    if not verdict["ok"]:
        for line in verdict["problems"]:
            print(f"DENY [SIM_SET_CHANGED] {line}")
        return EXIT_DENY
    print(f"SIM_OK udid={evidence['udid']} 当前只多出本 run 这一台")
    return EXIT_ALLOW


def _sim_delete_cli(args: argparse.Namespace) -> int:
    evidence, problem = _load_sim_evidence(args.run_dir)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    if evidence.get("delete") is not None:
        print(f"DENY [SIM_ALREADY_DELETED] 本 run 已执行过删除(证据 {posixpath.join(args.run_dir, SIM_EVIDENCE)}), 不重复删除")
        return EXIT_DENY
    pre = verify_sim_evidence(evidence, run_dir=args.run_dir, require_deleted=False)
    udid = evidence.get("udid")
    if not is_udid(udid) or evidence.get("name") != sim_name(args.run_dir):
        for line in pre["problems"] or ["证据无本 run UDID"]:
            print(f"DENY [SIM_NOT_OWN] {line}")
        return EXIT_DENY
    ddir = str(evidence.get("devices_dir") or devices_dir())
    device = posixpath.join(ddir, udid)
    if os.path.islink(device) or not os.path.isdir(device):
        print(f"DENY [SIM_NOT_OWN] {device} 不是真实目录(已不存在或被替换), 不删除")
        return EXIT_DENY
    device_name, name_problem = device_plist_name(device)
    if device_name != evidence["name"]:
        print(f"DENY [SIM_NOT_OWN] {device} 的 device.plist name = {device_name!r}{f'({name_problem})' if name_problem else ''}, "
              f"不是本 run 的 {evidence['name']}——不删除(防删错他台)")
        return EXIT_DENY
    before = devices_listing(ddir)
    result = _simctl("delete", udid)
    after = devices_listing(ddir)
    evidence["delete"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **result, "before": before, "after": after}
    problem = _write_json_in_run(args.run_dir, SIM_EVIDENCE, evidence)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    verdict = verify_sim_evidence(evidence, run_dir=args.run_dir, require_deleted=True)
    d = listing_diff(before, after)
    print(f"devices {ddir}: 删前 {len(before['udids'])} 台 → 删后 {len(after['udids'])} 台; 少了 {d['removed'] or '(无)'} 多出 {d['added'] or '(无)'}")
    for line in verdict["service"]:
        print(f"service-side-effect {line}")
    if not verdict["ok"]:
        for line in verdict["problems"]:
            print(f"DENY [SIM_DELETE_UNVERIFIED] {line}")
        return EXIT_DENY
    print(f"SIM_DELETED udid={udid} name={evidence['name']} 前后比对: 只多出并只删除这一台 evidence={posixpath.join(args.run_dir, SIM_EVIDENCE)}")
    return EXIT_ALLOW


def darwin_user_temp_dir() -> str:
    """Darwin 用户临时目录(`getconf DARWIN_USER_TEMP_DIR`; 不读 TMPDIR——TMPDIR 已被重定向到 run 内)。读不出 = HostIdentityError 同类拒。"""
    try:
        proc = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostIdentityError(f"getconf DARWIN_USER_TEMP_DIR 失败: {exc}") from exc
    value = (proc.stdout or "").strip().rstrip("/")
    if proc.returncode != 0 or not value.startswith("/"):
        raise HostIdentityError(f"getconf DARWIN_USER_TEMP_DIR 无可用输出(退出码 {proc.returncode})")
    return value


def temp_listing(temp_dir: str, patterns: list[str]) -> dict[str, list[Any]]:
    """系统临时目录顶层与 patterns 匹配的项 {名: [类型, 大小, mtime 整秒]}(只读, 不进入, 不跟随链接)。"""
    out: dict[str, list[Any]] = {}
    if not os.path.isdir(temp_dir):
        return out
    for name in sorted(os.listdir(temp_dir)):
        if any(fnmatch.fnmatchcase(name, p) for p in patterns):
            full = os.path.join(temp_dir, name)
            st = os.lstat(full)
            kind = "link" if os.path.islink(full) else "dir" if os.path.isdir(full) else "file"
            out[name] = [kind, int(st.st_size), int(st.st_mtime)]
    return out


def _sys_snapshot_cli(args: argparse.Namespace) -> int:
    problem = _run_dir_problem(args.run_dir, SIDE_EVIDENCE)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    patterns = [p for p in (args.pattern or []) if p]
    if not patterns or any("/" in p for p in patterns):
        print("DENY [SIDE_PATTERN_INVALID] --pattern 须至少一个且为顶层名 glob(取自开工稿, 声明 ios_simulator.xcode_temp_side_effects)")
        return EXIT_DENY
    try:
        temp_dir = args.temp_dir.rstrip("/") if args.temp_dir else darwin_user_temp_dir()
    except HostIdentityError as exc:
        print(f"ERROR [TEMP_DIR_UNREADABLE] {exc}(非 macOS 或 getconf 不可用; 显式 --temp-dir 给出系统临时目录)", file=sys.stderr)
        return EXIT_ERROR
    if canonical_problem(temp_dir):
        print(f"DENY [PATH_NOT_CANONICAL] --temp-dir {canonical_problem(temp_dir)}")
        return EXIT_DENY
    path = posixpath.join(args.run_dir, SIDE_EVIDENCE)
    data, read_problem = _read_json(path)
    if args.phase == "before":
        if data is not None:
            print(f"DENY [SIDE_BEFORE_EXISTS] 本 run 已有前快照 {path}(一个 run 只取一次前快照, 不覆盖)")
            return EXIT_DENY
        data = {"schema": SIDE_EVIDENCE_SCHEMA, "run_dir": args.run_dir, "temp_dir": temp_dir, "patterns": patterns,
                "before_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "before": temp_listing(temp_dir, patterns), "after": None, "added": [], "removed": [], "changed": []}
        problem = _write_json_in_run(args.run_dir, SIDE_EVIDENCE, data)
        if problem:
            print(f"DENY {problem}")
            return EXIT_DENY
        print(f"SIDE_BEFORE {len(data['before'])} 项 temp_dir={temp_dir} patterns={patterns} evidence={path}")
        return EXIT_ALLOW
    if data is None or not isinstance(data, dict) or data.get("schema") != SIDE_EVIDENCE_SCHEMA:
        print(f"DENY [SIDE_BEFORE_MISSING] 无前快照({read_problem or '证据形不合'}); 先 --phase before")
        return EXIT_DENY
    if data.get("temp_dir") != temp_dir or data.get("patterns") != patterns:
        print(f"DENY [SIDE_SCOPE_MISMATCH] 后快照的 temp_dir / patterns 须与前快照一致({data.get('temp_dir')} {data.get('patterns')})")
        return EXIT_DENY
    after = temp_listing(temp_dir, patterns)
    before = data.get("before") or {}
    data.update({"after_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "after": after, "added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
                 "changed": sorted(n for n in set(after) & set(before) if after[n] != before[n])})
    problem = _write_json_in_run(args.run_dir, SIDE_EVIDENCE, data)
    if problem:
        print(f"DENY {problem}")
        return EXIT_DENY
    for key, mark in (("added", "+"), ("removed", "-"), ("changed", "~")):
        for name in data[key]:
            print(f"SIDE_EFFECT {mark} {posixpath.join(temp_dir, name)}")
    total = len(data["added"]) + len(data["removed"]) + len(data["changed"])
    print(f"SIDE_EFFECTS {total} 处(Xcode 自写系统临时目录 = 系统副作用: 已记入 {path}, 未删除, 不是 run 产物)")
    return EXIT_ALLOW


def judge_sim_target(run_dir: str, udid: str, target: str, home: str | None = None) -> dict[str, Any]:
    """主机侧本 run 专用模拟器设备目录判定: 目标真实路径须在 <Devices>/<udid> 内, 设备目录真实 = 字面, device.plist name = 本 run 名。"""
    if not is_udid(udid):
        return {"allowed": False, "code": "SIM_NOT_BOUND", "reason": f"--sim-udid {udid!r} 不是 UDID", "real_target": None, "real_run_dir": None}
    device = posixpath.join(devices_dir(home), udid)
    verdict = judge_realpath(device, target)
    if not verdict["allowed"]:
        return verdict
    try:
        want = sim_name(run_dir)
    except ValueError as exc:
        return {"allowed": False, "code": "PATH_NOT_CANONICAL", "reason": str(exc), "real_target": None, "real_run_dir": None}
    name, problem = device_plist_name(device)
    if name != want:
        return {"allowed": False, "code": "SIM_NOT_OWN_RUN", "reason": f"设备 {udid} 的 device.plist name = {name!r}"
                f"{f'({problem})' if problem else ''}, 不是本 run 的 {want}", "real_target": verdict["real_target"], "real_run_dir": None}
    return {**verdict, "reason": f"目标真实路径 {verdict['real_target']} 在本 run 专用模拟器 {want}({udid})设备目录内"}


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
    check.add_argument("--sim-udid", default=None, help="AIPOS-F194: judge a path inside this run's own simulator device directory "
                                                          "(~/Library/Developer/CoreSimulator/Devices/<udid>) instead of the run directory")
    for verb, text in (("sim-create", "AIPOS-F194: create this run's own simulator lybra-<card>-<run> in the default device set "
                                      "(xcrun simctl create), snapshot the Devices UDID list before/after, write evidence into the run directory"),
                       ("sim-verify", "AIPOS-F194: verify the Devices UDID list = before-create list + this run's simulator only"),
                       ("sim-delete", "AIPOS-F194: delete this run's simulator only (xcrun simctl delete <udid>) and verify only it is gone")):
        sp = sub.add_parser(verb, help=text)
        sp.add_argument("--run-dir", required=True)
        sp.add_argument("--expect-fingerprint", required=True)
        if verb == "sim-create":
            sp.add_argument("--device-type", required=True, help="Device type id (xcrun simctl list devicetypes)")
            sp.add_argument("--runtime", required=True, help="Runtime id (xcrun simctl list runtimes)")
    side = sub.add_parser("sys-snapshot", help="AIPOS-F194: snapshot Xcode's own writes in the Darwin user temp dir (top-level entries "
                                               "matching --pattern) before/after the run; evidence in the run directory, nothing deleted")
    side.add_argument("--run-dir", required=True)
    side.add_argument("--expect-fingerprint", required=True)
    side.add_argument("--phase", required=True, choices=("before", "after"))
    side.add_argument("--pattern", action="append", default=[])
    side.add_argument("--temp-dir", default=None, help="System temp dir (default: getconf DARWIN_USER_TEMP_DIR)")
    srcv = sub.add_parser("src-verify", help="AIPOS-F197: verify <run-dir>/src (delivered by `git archive <candidate> | tar -x`) hashes to the "
                                             "candidate's git tree (no uncommitted edits / wrong version); evidence in the run directory")
    srcv.add_argument("--run-dir", required=True)
    srcv.add_argument("--candidate", required=True)
    srcv.add_argument("--expect-tree", required=True)
    srcv.add_argument("--expect-fingerprint", required=True)
    srcv.add_argument("--seal", required=True)
    evm = sub.add_parser("evidence-manifest", help="AIPOS-F200: list this run's evidence files (declared evidence dirs; path / size / sha256) "
                                                   "with host fingerprint and seal into <run-dir>/.lybra-vr/evidence-manifest.json")
    evm.add_argument("--run-dir", required=True)
    evm.add_argument("--candidate", required=True)
    evm.add_argument("--expect-fingerprint", required=True)
    evm.add_argument("--include", action="append", default=[], help="Evidence dir relative to the run dir (repeatable; sealed)")
    evm.add_argument("--skip-ext", action="append", default=[], help="File extension not fetched (code files; repeatable; sealed)")
    evm.add_argument("--max-file-bytes", type=int, required=True)
    evm.add_argument("--max-total-bytes", type=int, required=True)
    evm.add_argument("--max-files", type=int, required=True)
    evm.add_argument("--seal", required=True)
    evb = sub.add_parser("evidence-bundle", help="AIPOS-F200: read-only; verify the run's evidence manifest and every listed file, then write a "
                                                 "tar stream (manifest.json + files/<path>) to stdout; any mismatch = DENY on stderr, exit 3")
    evb.add_argument("--run-dir", required=True)
    evb.add_argument("--expect-fingerprint", required=True)
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
    if args.command == "src-verify":  # AIPOS-F197 件④
        problem = _own_fingerprint_problem(args.expect_fingerprint)
        if problem:
            print(f"DENY {problem}")
            return EXIT_DENY
        try:
            return _src_verify_cli(args)
        except OSError as exc:
            print(f"ERROR [HOST_IO_FAILED] {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_ERROR
    if args.command in ("evidence-manifest", "evidence-bundle"):  # AIPOS-F200
        problem = _own_fingerprint_problem(args.expect_fingerprint)
        if problem:
            print(f"DENY {problem}", file=sys.stderr if args.command == "evidence-bundle" else sys.stdout)
            return EXIT_DENY
        try:
            return _evidence_manifest_cli(args) if args.command == "evidence-manifest" else _evidence_bundle_cli(args)
        except OSError as exc:
            print(f"ERROR [HOST_IO_FAILED] {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_ERROR
    if args.command in ("sim-create", "sim-verify", "sim-delete", "sys-snapshot"):
        problem = _own_fingerprint_problem(args.expect_fingerprint)
        if problem:
            print(f"DENY {problem}")
            return EXIT_DENY
        handler = {"sim-create": _sim_create_cli, "sim-verify": _sim_verify_cli, "sim-delete": _sim_delete_cli,
                   "sys-snapshot": _sys_snapshot_cli}[args.command]
        try:
            return handler(args)
        except OSError as exc:
            print(f"ERROR [HOST_IO_FAILED] {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_ERROR
    if args.command == "check":
        problem = _own_fingerprint_problem(args.expect_fingerprint)
        if problem:
            print(f"DENY {problem}")
            return EXIT_DENY
        if args.sim_udid is not None:
            result = judge_sim_target(args.run_dir, args.sim_udid, args.path)
            print(f"{'ALLOW' if result['allowed'] else 'DENY'} [{result['code']}] {result['reason']}")
            return EXIT_ALLOW if result["allowed"] else EXIT_DENY
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
