"""AIPOS-F126 件①②(gap #90/#102): run-all 测试子进程的事前隔离——操作系统级沙箱里只读挂载真实 home 根。

病根: AIPOS-F116/F124 的真实治理根守卫是「事后快照 + 单独重跑猜归因」, 快照只看得到「变了」看不到「谁写的」; 别的项目顾问、门、
并行卡在同一时段持续写入被监视目录时, 重跑窗口里仍落在同一目录 → 误判红不可消除。
现: 每个测试文件在沙箱里启动, 真实 home 根(workspace_config.resolve_home_root_with_source 同一梯, 由 runall_discovery.
governance_guard_targets 解析)与被守卫治理仓的 git 仓根只读——测试任何直接写入当场失败(EROFS / EACCES)、归因到该文件; 他方
在沙箱外的写入与测试无关, 快照守卫只作信息输出。

后端(声明 config.schema test_contract.isolation.mode; auto 按 BACKENDS 顺序探测):
  bwrap     bubblewrap: `--dev-bind / /` 之上 `--ro-bind <只读路径>`(落在只读路径内的产品仓/临时目录再 `--bind` 放行可写),
            network=isolated 时加 `--unshare-net`(新网络命名空间仅 loopback: 测试自起的临时门照用, 宿主生产门与外网不可达)。
            需非特权 user namespace(Ubuntu 由系统 AppArmor 的 bwrap 配置放行); bwrap 沙箱内不可再嵌套 bwrap。
  landlock  Linux Landlock(≥5.13, ABI≥2; 标准库 ctypes 直调系统调用, 无新依赖): 只管文件写类权限, 不需 user namespace、可嵌套
            (run-all 夹具在外层 bwrap 沙箱里再跑执行器即落到这里)。规则 = 只读路径以外处处可写: 只读路径各级祖先目录里的其余
            条目逐个放行(运行开始后在这些祖先目录里新建的条目同样不可写——已知局限, 真实根的祖先是 / 与家目录, 测试 HOME 已隔离)。
            不能隔离网络。
能力探测唯一实现 probe(): 真起一次该后端沙箱、真试写一次只读路径(须被拒)与一次可写路径(须成功), 并从沙箱外核对落盘结果;
network=isolated 的 bwrap 另核沙箱内网卡只剩 lo。不凭 which。全不可用 = 降级为快照守卫, 输出头部明示(不静默)。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

#: auto 的探测顺序; 须与 config.schema test_contract.isolation.mode 的 values(去掉 auto / none)一致(resolve 校验)。
BACKENDS = ("bwrap", "landlock")
#: 测试子进程环境里的沙箱标记(值 = 后端名; 不用 LYBRA_/AIPOS_ 前缀, 嵌套执行器不剥): 嵌套执行器据此在头部说明外层沙箱。
SANDBOX_ENV = "RUNALL_SANDBOX"
PROBE_TIMEOUT_SECONDS = 30

# Landlock(include/uapi/linux/landlock.h); 系统调用号自 Linux 5.13 起各架构统一。
_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_FS_WRITE_FILE = 1 << 1
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
_FS_REFER = 1 << 13  # ABI 2
_FS_TRUNCATE = 1 << 14  # ABI 3
#: 作用于普通文件(非目录)的写类权限(内核拒绝在非目录上授予目录类权限)。
_FS_FILE_RIGHTS = _FS_WRITE_FILE | _FS_TRUNCATE

#: Landlock 启动器: 在子进程里收紧到执行器预建的规则集(fd 经 pass_fds 继承)后 exec 测试命令。纯标准库, 不导入产品模块。
_LANDLOCK_LAUNCHER = (
    "import ctypes, os, sys\n"
    "libc = ctypes.CDLL(None, use_errno=True)\n"
    "libc.syscall.restype = ctypes.c_long\n"
    "fd = int(sys.argv[1])\n"
    "if libc.prctl(38, ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:\n"
    "    raise OSError(ctypes.get_errno(), 'prctl(PR_SET_NO_NEW_PRIVS) 失败')\n"
    "if libc.syscall(ctypes.c_long(446), ctypes.c_int(fd), ctypes.c_uint32(0)) != 0:\n"
    "    raise OSError(ctypes.get_errno(), 'landlock_restrict_self 失败')\n"
    "os.close(fd)\n"
    "os.execvp(sys.argv[2], sys.argv[2:])\n"
)

#: 探测脚本: argv = 只读路径 可写路径 [net]。只读路径写入须被拒; 可写路径写入须成功; net = 网卡须只剩 lo。
_PROBE_SCRIPT = (
    "import os, sys\n"
    "ro, rw = sys.argv[1], sys.argv[2]\n"
    "try:\n"
    "    with open(os.path.join(ro, 'probe'), 'w') as fh:\n"
    "        fh.write('x')\n"
    "except OSError as exc:\n"
    "    denied = exc.errno\n"
    "else:\n"
    "    print('ro-writable'); sys.exit(3)\n"
    "with open(os.path.join(rw, 'probe'), 'w') as fh:\n"
    "    fh.write('x')\n"
    "ifaces = 'unchecked'\n"
    "if len(sys.argv) > 3:\n"
    "    with open('/proc/net/dev') as fh:\n"
    "        ifaces = ','.join(sorted(line.split(':', 1)[0].strip() for line in fh.read().splitlines()[2:]))\n"
    "print(f'ro-denied errno={denied} rw-ok ifaces={ifaces}')\n"
)


class IsolationSetupError(RuntimeError):
    """沙箱搭建失败(探测通过后真实只读路径的规则集建不起来等)。执行器 fail-closed 报红。"""


@dataclass
class Isolation:
    """一轮 run-all 的隔离决定(resolve 的结果)。backend=None = 未隔离(降级 / 声明 none / 无可保护路径 / 指定后端不可用)。"""

    declared: dict[str, Any]
    backend: str | None = None
    network_isolated: bool = False
    protected: tuple[Path, ...] = ()
    writable: tuple[Path, ...] = ()
    attempts: list[str] = field(default_factory=list)
    note: str = ""
    required_unavailable: bool = False
    _ruleset_fd: int | None = None

    @property
    def active(self) -> bool:
        return self.backend is not None

    def command(self, cmd: list[str]) -> list[str]:
        """测试命令 → 沙箱内启动的命令(未隔离 = 原样)。"""
        if self.backend is None:
            return list(cmd)
        return _sandbox_prefix(self.backend, self.protected, self.writable, self.network_isolated, self._ruleset_fd) + list(cmd)

    def popen_kwargs(self) -> dict[str, Any]:
        return {"pass_fds": (self._ruleset_fd,)} if self._ruleset_fd is not None else {}

    def env(self, env: Mapping[str, str]) -> dict[str, str]:
        out = dict(env)
        if self.backend is not None:
            out[SANDBOX_ENV] = self.backend
        return out

    def header(self) -> str:
        """run-all 输出头部的隔离行(一行; 「隔离:」起头便于 grep)。"""
        decl = f"声明 mode={self.declared.get('mode')} network={self.declared.get('network')}, 来源 {self.declared.get('source')}"
        probes = f"; 探测: {' / '.join(self.attempts)}" if self.attempts else ""
        outer = os.environ.get(SANDBOX_ENV)
        outer_note = f"; 本执行器自身已在外层 run-all 沙箱({outer})内" if outer else ""
        if self.backend is not None:
            if self.network_isolated:
                net = "独立网络命名空间, 仅 loopback(宿主生产门与外网不可达)"
            elif self.declared.get("network") == "isolated":
                net = f"未隔离({self.backend} 不能隔离网络)"
            else:
                net = "未隔离(声明 network=host)"
            holes = ", ".join(str(p) for p in self.writable) or "无"
            return (f"隔离:{self.backend}(只读: {', '.join(str(p) for p in self.protected)}; 只读内放行可写: {holes}; 网络: {net}; "
                    f"{decl}{probes}{outer_note})")
        if self.required_unavailable:
            return f"隔离:声明后端 {self.declared.get('mode')} 不可用, 不悄悄换后端({self.note}; {decl}{probes}{outer_note})"
        if self.declared.get("mode") == "none":
            return f"隔离:无(声明 none), 快照守卫照常判定({decl}{outer_note})"
        return f"隔离:无, 降级为快照守卫({self.note}; {decl}{probes}{outer_note})"

    def close(self) -> None:
        if self._ruleset_fd is not None:
            os.close(self._ruleset_fd)
            self._ruleset_fd = None


# ---------------------------------------------------------------------------
# 只读路径 / 放行路径
# ---------------------------------------------------------------------------
def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def protected_paths(targets: Mapping[str, Any]) -> list[Path]:
    """只读路径 = 守卫解析出的 home 根 + 被守卫目录所在 git 仓根(git 写入 .git/index 等落在 home 根之外的仓根时也挡住)。
    只取存在者; 已被另一只读路径包含的去掉。targets = runall_discovery.governance_guard_targets 的返回(同一梯解析, 禁另写)。"""
    candidates = [Path(targets["home_root"]), *(Path(top) for top in targets["repos"])]
    existing = sorted({p.resolve() for p in candidates if p.exists()}, key=lambda p: (len(p.parts), str(p)))
    out: list[Path] = []
    for path in existing:
        if not any(_within(path, kept) for kept in out):
            out.append(path)
    return out


def writable_holes(protected: list[Path], candidates: list[Path]) -> list[Path]:
    """只读路径之内仍须可写的路径(产品仓、其 git 目录、系统临时目录、本轮临时 HOME): 只取严格落在某只读路径之内者
    (祖先不取——放行祖先会把只读路径一并放开)。"""
    out: list[Path] = []
    for raw in candidates:
        path = Path(raw).resolve()
        if any(path != root and root in path.parents for root in protected) and path not in out:
            out.append(path)
    return out


# ---------------------------------------------------------------------------
# 后端
# ---------------------------------------------------------------------------
def _sandbox_prefix(backend: str, protected: tuple[Path, ...] | list[Path], writable: tuple[Path, ...] | list[Path],
                    network_isolated: bool, ruleset_fd: int | None) -> list[str]:
    """沙箱启动前缀(执行器与探测同一构造)。"""
    if backend == "bwrap":
        prefix = ["bwrap", "--dev-bind", "/", "/"]
        for path in protected:
            prefix += ["--ro-bind", str(path), str(path)]
        for path in writable:
            prefix += ["--bind", str(path), str(path)]
        if network_isolated:
            prefix.append("--unshare-net")
        return prefix + ["--die-with-parent", "--"]
    if backend == "landlock":
        if ruleset_fd is None:
            raise IsolationSetupError("landlock 规则集未建立")
        return [sys.executable, "-c", _LANDLOCK_LAUNCHER, str(ruleset_fd)]
    raise ValueError(f"未知隔离后端 {backend!r}(已实现 {list(BACKENDS)})")


def _libc() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """Landlock ABI 版本(内核不支持 / 未启用 = 抛 OSError)。非 Linux = 抛 OSError。"""
    if not sys.platform.startswith("linux"):
        raise OSError(f"Landlock 只在 Linux 可用(本机 {sys.platform})")
    libc = _libc()
    abi = libc.syscall(ctypes.c_long(_SYS_LANDLOCK_CREATE_RULESET), None, ctypes.c_size_t(0),
                       ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION))
    if abi < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"Landlock 不可用: {os.strerror(err)}")
    return int(abi)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


def _path_beneath_attr(allowed_access: int, parent_fd: int) -> ctypes.Array:
    """struct landlock_path_beneath_attr { __u64 allowed_access; __s32 parent_fd; } __attribute__((packed)) = 12 字节(本机字节序)。"""
    return ctypes.create_string_buffer(struct.pack("=Qi", allowed_access, parent_fd), 12)


def _landlock_allowed(protected: list[Path], writable: list[Path]) -> list[Path]:
    """放行写的路径: 只读路径各级祖先目录里除通往只读路径那一支以外的全部条目(逐级下钻), 加只读路径内的放行路径。"""
    ancestors = {a for p in protected for a in p.parents}
    allowed: list[Path] = []

    def walk(directory: Path) -> None:
        for child in sorted(directory.iterdir()):
            if child in protected:
                continue
            if child in ancestors and not child.is_symlink():
                walk(child)
                continue
            if child.is_symlink():
                try:
                    target = child.resolve(strict=True)
                except (FileNotFoundError, RuntimeError):
                    continue  # 悬空 / 环状链接: 无可写目标, 不放行
                if any(_within(target, p) or _within(p, target) for p in protected):
                    continue  # 指向只读路径或其祖先的链接不放行(否则经链接绕开)
            allowed.append(child)

    for root in sorted({Path(p.anchor) for p in protected}):
        walk(root)
    return allowed + list(writable)


def build_landlock_ruleset(protected: list[Path], writable: list[Path]) -> int:
    """建 Landlock 规则集(只读路径之外处处可写), 返回规则集 fd(调用方经 pass_fds 交给启动器, 用毕 close)。失败 = 抛 OSError。"""
    abi = landlock_abi()
    if abi < 2:
        raise OSError(f"Landlock ABI {abi} < 2(不支持跨目录改名 REFER, 测试常见的原子改名会被误拒)")
    handled = (_FS_WRITE_FILE | _FS_REMOVE_DIR | _FS_REMOVE_FILE | _FS_MAKE_CHAR | _FS_MAKE_DIR | _FS_MAKE_REG | _FS_MAKE_SOCK
               | _FS_MAKE_FIFO | _FS_MAKE_BLOCK | _FS_MAKE_SYM | _FS_REFER | (_FS_TRUNCATE if abi >= 3 else 0))
    libc = _libc()
    attr = _RulesetAttr(handled)
    fd = libc.syscall(ctypes.c_long(_SYS_LANDLOCK_CREATE_RULESET), ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)),
                      ctypes.c_uint32(0))
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"landlock_create_ruleset 失败: {os.strerror(err)}")
    fd = int(fd)
    try:
        for path in _landlock_allowed(protected, writable):
            try:
                path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except FileNotFoundError:
                continue  # 枚举与打开之间被删: 已不存在, 无需放行
            try:
                rights = handled if os.path.isdir(f"/proc/self/fd/{path_fd}") else handled & _FS_FILE_RIGHTS
                rule = _path_beneath_attr(rights, path_fd)
                if libc.syscall(ctypes.c_long(_SYS_LANDLOCK_ADD_RULE), ctypes.c_int(fd), ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
                                rule, ctypes.c_uint32(0)) != 0:
                    err = ctypes.get_errno()
                    raise OSError(err, f"landlock_add_rule {path} 失败: {os.strerror(err)}")
            finally:
                os.close(path_fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


# ---------------------------------------------------------------------------
# 能力探测(唯一实现)
# ---------------------------------------------------------------------------
def probe(backend: str, network_isolated: bool) -> tuple[bool, str]:
    """真起一次 backend 沙箱: 只读路径写入须被拒、可写路径写入须成功(沙箱外核对落盘), network_isolated 的 bwrap 须只剩 lo。
    返回 (可用?, 说明)。"""
    tmp = Path(tempfile.mkdtemp(prefix="lybra-runall-probe-")).resolve()
    ro, rw = tmp / "ro", tmp / "rw"
    ro.mkdir()
    rw.mkdir()
    ruleset_fd: int | None = None
    try:
        net = network_isolated and backend == "bwrap"
        if backend == "landlock":
            try:
                ruleset_fd = build_landlock_ruleset([ro], [])
            except OSError as exc:
                return False, f"landlock 不可用: {exc}"
        cmd = _sandbox_prefix(backend, [ro], [], net, ruleset_fd) + [sys.executable, "-c", _PROBE_SCRIPT, str(ro), str(rw)]
        if net:
            cmd.append("net")
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
                                  pass_fds=(ruleset_fd,) if ruleset_fd is not None else ())
        except FileNotFoundError as exc:
            return False, f"{backend} 不可用: 启动失败 {exc}"
        except subprocess.TimeoutExpired:
            return False, f"{backend} 不可用: 探测 {PROBE_TIMEOUT_SECONDS}s 超时"
        said = (done.stdout.strip() or done.stderr.strip()).splitlines()
        said_line = said[-1] if said else f"退出码 {done.returncode} 无输出"
        if done.returncode != 0 or not done.stdout.startswith("ro-denied"):
            return False, f"{backend} 不可用: {said_line}"
        if (ro / "probe").exists() or not (rw / "probe").is_file():
            return False, f"{backend} 不可用: 沙箱外核对落盘不符(只读路径有写入或可写路径无写入)"
        if net and not done.stdout.strip().endswith("ifaces=lo"):
            return False, f"{backend} 不可用: 网络未隔离({said_line})"
        return True, f"{backend} 可用({said_line})"
    finally:
        if ruleset_fd is not None:
            os.close(ruleset_fd)
        shutil.rmtree(tmp)


def resolve(declared: Mapping[str, Any], protected: list[Path], writable: list[Path], *, probe_fn: Any = None) -> Isolation:
    """声明 {mode, network, source} + 只读/放行路径 → 本轮隔离决定。auto 按 BACKENDS 顺序探测第一个可用者; 指定后端不可用 =
    required_unavailable(执行器报红); none / 无可保护路径 = 未隔离(头部明示)。landlock 生效时规则集在此建好(Isolation.close 释放)。"""
    decl = dict(declared)
    mode = decl.get("mode")
    if mode not in ("auto", "none", *BACKENDS):
        raise ValueError(f"隔离声明 mode={mode!r} 不是已实现后端(auto / none / {list(BACKENDS)}; config.schema test_contract.isolation)")
    iso = Isolation(declared=decl, protected=tuple(protected), writable=tuple(writable))
    if mode == "none":
        return iso
    if not protected:
        iso.note = "无可只读挂载的路径(home 根不存在)"
        return iso
    want_net = decl.get("network") == "isolated"
    probe_fn = probe_fn or probe  # 调用时取模块级 probe(唯一探测实现; 夹具可在模块上替换以模拟不可用)
    for backend in (BACKENDS if mode == "auto" else (mode,)):
        ok, detail = probe_fn(backend, want_net)
        iso.attempts.append(detail)
        if not ok:
            continue
        if backend == "landlock":
            try:
                iso._ruleset_fd = build_landlock_ruleset(list(protected), list(writable))
            except OSError as exc:
                raise IsolationSetupError(f"landlock 探测可用但真实只读路径规则集建立失败: {exc}") from exc
        iso.backend = backend
        iso.network_isolated = want_net and backend == "bwrap"
        return iso
    if mode == "auto":
        iso.note = f"无可用沙箱(已探测 {'、'.join(BACKENDS)})"
    else:
        iso.required_unavailable = True
        iso.note = f"声明 mode={mode} 探测不可用"
    return iso
