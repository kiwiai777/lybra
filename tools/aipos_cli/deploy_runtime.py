"""AIPOS-F152(gap #127/#87) — 长驻进程与部署版本一致 + `lybra` CLI 从部署快照运行(唯一实现)。

一句话: `lybra` 入口每次启动把部署快照 current 解析成真实路径、整个进程只从这一份不可变快照加载代码(不再吃产品仓工作树的中间态);
长驻命令(loop / agent watch / loop status --wait)启动时记下自己运行的快照提交, 运行中比对 current, 部署一变就干净停下并明示重跑。

件② 入口(唯一实现, 两条安装路径同一份入口脚本文本):
- launcher_source(deploy_current, interpreter): 入口脚本文本——`tools/lybra-deploy install-entry`(部署时自动执行)写进 bin 目录;
  pip 控制台入口(pyproject [project.scripts] lybra = 本模块 main)在新解释器里 exec 同一文本。
- launch(): 入口脚本把快照根放进 sys.path 首位后调用——核对代码确实加载自该根(否则拒跑)、开发开关首行标明、转 aipos_cli.main。
- 显式开发开关 `lybra --dev-tree <产品仓工作树> <子命令> …`(入口级选项, 须为第一个参数; 入口在加载任何产品代码前消费它):
  代码取自该树, stderr 首行标明「开发模式」(stdout 不掺杂, --json 照常可解析)。不另设环境变量(环境变量名单单源
  config.schema environment_variables, 开关只在显式命令行出现, 不会被继承的环境悄悄带进别的命令)。
- 卡工作树里跑测试 / `python3 -m tools.aipos_cli` 不经入口 = 工作树代码(mode=worktree), 行为不变。

件① 部署一致性(唯一判定 DeployGuard): 运行代码来自部署快照(<部署目录>/releases/<名>, 且同目录有 current 链接)= mode deploy,
基线 = 该快照 VERSION 的 git_commit; check() 读 current/VERSION 的 git_commit(解析唯一口 gate_drift.read_version_commit, 与
gate_drift / lybra-deploy verify 同一字段)比对, 变了 = 部署已更新。非快照运行(工作树 / 开发开关)= 不适用(无从比对, 如实标明)。

布局名与 tools/lybra-deploy 同名(DEPLOY_DIR/releases/<名>、DEPLOY_DIR/current、<快照>/VERSION), 夹具核对两边一致。
本模块顶层只 import 标准库(pip 控制台入口从可能处于中间态的工作树导入本模块, 越轻越稳)。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEV_TREE_FLAG = "--dev-tree"
RELEASES_DIRNAME = "releases"
CURRENT_LINKNAME = "current"
VERSION_FILENAME = "VERSION"
DEPLOY_DIRNAME = ".deploy"
LAUNCHER_MARK = "lybra-entry-launcher/v1(AIPOS-F152)"
ENTRY_NAME = "lybra"
DEFAULT_BIN_DIR = "~/.local/bin"
_ENTRY: dict[str, str] = {}  # 入口(launch)本进程登记: dev_tree = 开发开关指向的代码根(running_code 据此判 dev)


class EntryError(RuntimeError):
    """入口装不上 / 加载来源不对(fail-closed: 出声 + 非零退出, 不回落工作树)。"""


# ---------------------------------------------------------------------------
# 运行代码身份(件①②共用)
# ---------------------------------------------------------------------------

@dataclass
class RunningCode:
    code_root: Path
    mode: str  # deploy | dev | worktree
    deploy_dir: Path | None = None
    commit: str | None = None
    release: str | None = None

    def describe(self) -> dict[str, Any]:
        return {"mode": self.mode, "code_root": str(self.code_root), "commit": self.commit, "release": self.release,
                "deploy_current": str(self.deploy_dir / CURRENT_LINKNAME) if self.deploy_dir else None}


def _code_root() -> Path:
    from tools.schema_loader import code_repo_schema_root  # 运行代码所在仓根唯一实现(resolve 后的真实路径)

    return Path(code_repo_schema_root())


def _read_commit(version_file: Path) -> str | None:
    from tools.aipos_cli.gate_drift import read_version_commit  # VERSION git_commit 唯一解析

    return read_version_commit(version_file)


def snapshot_deploy_dir(code_root: Path) -> Path | None:
    """代码根是部署快照(<部署目录>/releases/<名>, 部署目录有 current 链接)= 部署目录; 否则 None。"""
    root = Path(code_root)
    if root.parent.name == RELEASES_DIRNAME and (root.parent.parent / CURRENT_LINKNAME).is_symlink():
        return root.parent.parent
    return None


def running_code(code_root: Path | None = None) -> RunningCode:
    """本进程运行的代码: deploy(部署快照)/ dev(入口 --dev-tree 指向本代码根)/ worktree(其余: 卡工作树测试、python -m)。"""
    root = Path(code_root) if code_root is not None else _code_root()
    deploy_dir = snapshot_deploy_dir(root)
    if deploy_dir is not None:
        return RunningCode(root, "deploy", deploy_dir, _read_commit(root / VERSION_FILENAME), root.name)
    dev = _ENTRY.get("dev_tree")
    if dev and Path(os.path.realpath(dev)) == Path(os.path.realpath(root)):
        return RunningCode(root, "dev")
    return RunningCode(root, "worktree")


# ---------------------------------------------------------------------------
# 件① DeployGuard: 长驻命令每步/每轮比对部署
# ---------------------------------------------------------------------------

class DeployGuard:
    """启动时记下运行快照的提交; check() 比对部署目录 current 的提交, 变了返回 {from, to, ...}, 未变 / 不适用 = None。

    current 不在 / VERSION 不可读 = 视为已变(部署处于不明状态, fail-closed 停下, 原因进 to_detail)。"""

    def __init__(self, running: RunningCode) -> None:
        self.running = running
        self.baseline = running.commit if running.mode == "deploy" else None

    @classmethod
    def for_running_process(cls) -> "DeployGuard":
        return cls(running_code())

    @property
    def applicable(self) -> bool:
        return self.running.mode == "deploy" and self.running.deploy_dir is not None

    def describe(self) -> dict[str, Any]:
        info = self.running.describe()
        info["deploy_check"] = "active" if self.applicable else "not_applicable"
        return info

    def current_commit(self) -> tuple[str | None, str]:
        """(current 的 git_commit, 读不出时的缘由)。"""
        assert self.running.deploy_dir is not None
        current = self.running.deploy_dir / CURRENT_LINKNAME
        if not current.exists():
            return None, f"{current} 不存在(部署链接被移除)"
        try:
            commit = _read_commit(current / VERSION_FILENAME)
        except RuntimeError as exc:
            return None, str(exc)
        return commit, "" if commit else f"{current / VERSION_FILENAME} 无 git_commit"

    def check(self) -> dict[str, Any] | None:
        if not self.applicable:
            return None
        commit, why = self.current_commit()
        if commit is not None and commit == self.baseline:
            return None
        return {"from": self.baseline, "to": commit, "to_detail": why, "running_release": self.running.release,
                "deploy_current": str(self.running.deploy_dir / CURRENT_LINKNAME), "code_root": str(self.running.code_root)}


def short(commit: Any) -> str:
    return str(commit)[:12] if commit else "(无)"


def change_commits(change: dict[str, Any]) -> str:
    """新旧提交: 本进程运行快照 commit A → 现部署 commit B(读不出 = 不明 + 缘由)。"""
    to = short(change.get("to")) if change.get("to") else f"不明: {change.get('to_detail')}"
    return f"本进程运行快照 commit {short(change.get('from'))} → 现部署 commit {to}"


def change_summary(change: dict[str, Any]) -> str:
    """一行: 部署已更新(新旧提交在前, 路径在后; 人读输出共用)。"""
    return (f"部署已更新({change_commits(change)}; 快照 {change.get('running_release') or '?'}, "
            f"部署链接 {change.get('deploy_current')})")


# ---------------------------------------------------------------------------
# 件② 入口脚本(launcher)+ 安装
# ---------------------------------------------------------------------------

def launcher_source(deploy_current: str | Path, interpreter: str | None = None) -> str:
    """入口脚本文本(唯一渲染)。启动时解析一次 current 真实路径, 整个进程只用这一份快照; 第一个参数 --dev-tree <树> 显式改取工作树。"""
    interp = str(interpreter or sys.executable or "").strip()
    if not interp:
        raise EntryError("当前解释器路径 sys.executable 取不到, 无法生成 lybra 入口")
    return f'''#!{interp}
# {LAUNCHER_MARK}: lybra CLI 入口, 由 tools/lybra-deploy install-entry 生成(部署时自动重写); 勿手改。
# 代码从部署快照加载: 启动时把 current 解析成真实路径, 整个进程只用这一份不可变快照(产品仓工作树的中间态不影响 lybra 命令)。
# 本地调试: lybra {DEV_TREE_FLAG} <产品仓工作树> <子命令> ...(须为第一个参数; stderr 首行标明开发模式)。安装/升级见 docs/release_discipline.md。
import os
import sys

DEPLOY_CURRENT = {str(deploy_current)!r}
_argv = sys.argv[1:]
_dev = ""
if _argv and _argv[0] == {DEV_TREE_FLAG!r}:
    if len(_argv) < 2 or not _argv[1].strip():
        sys.stderr.write("lybra: {DEV_TREE_FLAG} 须跟产品仓工作树路径(lybra {DEV_TREE_FLAG} <工作树> <子命令> ...)\\n")
        sys.exit(2)
    _dev, _argv = _argv[1].strip(), _argv[2:]
elif _argv and _argv[0].startswith({DEV_TREE_FLAG + "="!r}):
    _dev, _argv = _argv[0].split("=", 1)[1].strip(), _argv[1:]
_root = os.path.realpath(_dev or DEPLOY_CURRENT)
if not os.path.isfile(os.path.join(_root, "tools", "aipos_cli", "deploy_runtime.py")):
    _what = "{DEV_TREE_FLAG} " + _dev if _dev else "部署快照 " + DEPLOY_CURRENT
    sys.stderr.write("lybra: " + _what + " -> " + _root + " 不是 lybra 产品代码树(缺 tools/aipos_cli/deploy_runtime.py); "
                     "出口: 用 tools/lybra-deploy 部署, 或本地调试 lybra {DEV_TREE_FLAG} <产品仓工作树> ...\\n")
    sys.exit(1)
sys.path.insert(0, _root)
from tools.aipos_cli.deploy_runtime import launch
sys.exit(launch(code_root=_root, deploy_current=DEPLOY_CURRENT, dev_tree=_dev, argv=_argv))
'''


def launch(*, code_root: str, deploy_current: str, dev_tree: str, argv: list[str]) -> int:
    """入口脚本调用: 核对加载来源 → 开发模式 stderr 首行标明 → aipos_cli.main(argv)。"""
    import tools.aipos_cli as _pkg

    root = Path(os.path.realpath(code_root))
    loaded = Path(_pkg.__file__).resolve().parents[2]
    if loaded != root:
        sys.stderr.write(f"lybra: 入口应从 {root} 加载产品代码, 实际加载自 {loaded}(sys.path 被前置覆盖?), 拒跑\n")
        return 1
    if dev_tree:
        _ENTRY["dev_tree"] = str(root)
        sys.stderr.write(f"[lybra] 开发模式({DEV_TREE_FLAG}): 代码取自 {root}, 不是部署快照 {deploy_current}; "
                         f"取消: 去掉 {DEV_TREE_FLAG}\n")
        sys.stderr.flush()
    elif snapshot_deploy_dir(root) is None:
        sys.stderr.write(f"lybra: {deploy_current} → {root} 不是部署快照(应为 <部署目录>/{RELEASES_DIRNAME}/<名>), 拒跑; "
                         f"出口: tools/lybra-deploy 重新部署\n")
        return 1
    from tools.aipos_cli.aipos_cli import main as cli_main

    return int(cli_main(argv) or 0)


def default_deploy_current(code_root: Path | None = None) -> Path:
    """本代码所属产品仓的部署链接: 代码根是快照 = 其部署目录/current; 否则 = 产品仓主检出(git common dir 上级, 卡工作树也落主检出,
    与 tools/lybra-deploy _resolve_product_repo 同语义)/.deploy/current。不在 git 仓 = <代码根>/.deploy/current。"""
    root = Path(code_root) if code_root is not None else _code_root()
    deploy_dir = snapshot_deploy_dir(root)
    if deploy_dir is not None:
        return deploy_dir / CURRENT_LINKNAME
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        out = None
    if out is not None and out.returncode == 0 and out.stdout.strip():
        return Path(out.stdout.strip()).parent / DEPLOY_DIRNAME / CURRENT_LINKNAME
    return root / DEPLOY_DIRNAME / CURRENT_LINKNAME


def main() -> int:
    """pip 控制台入口(pyproject [project.scripts] lybra): 本模块所在代码树只作跳板, 在新解释器里 exec 同一份入口脚本文本
    (部署快照 = 本代码所属产品仓的 .deploy/current), 不在本进程继续加载工作树代码。"""
    src = launcher_source(default_deploy_current(), sys.executable)
    os.execv(sys.executable, [sys.executable, "-c", src, *sys.argv[1:]])
    return 1  # execv 不返回


def entry_kind(path: Path) -> str:
    """bin 目录里现有 lybra 的种类: none / launcher(本入口)/ other(如 pip editable 控制台脚本)。"""
    if not path.exists() and not path.is_symlink():
        return "none"
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:600]
    except OSError as exc:
        raise EntryError(f"{path} 存在但不可读: {exc}") from exc
    return "launcher" if LAUNCHER_MARK in head else "other"


def install_entry(deploy_current: str | Path, bin_dir: str | Path, interpreter: str | None = None) -> dict[str, Any]:
    """把入口脚本原子写进 <bin_dir>/lybra(权限 755)。deploy_current 须是部署链接且指向快照; 原有非本入口文件(如 pip 控制台脚本)另存 <名>.pre-snapshot-entry。"""
    current = Path(deploy_current)
    if not current.is_symlink():
        raise EntryError(f"{current} 不是部署链接(由 tools/lybra-deploy 部署产生), 不装入口")
    target_root = Path(os.path.realpath(current))
    if snapshot_deploy_dir(target_root) is None or not (target_root / "tools" / "aipos_cli" / "deploy_runtime.py").is_file():
        raise EntryError(f"{current} → {target_root} 不是含本入口实现的部署快照, 不装入口(先 tools/lybra-deploy 部署)")
    bin_path = Path(os.path.expanduser(str(bin_dir)))
    bin_path.mkdir(parents=True, exist_ok=True)
    dest = bin_path / ENTRY_NAME
    previous = entry_kind(dest)
    backup = None
    if previous == "other":
        backup = dest.with_name(dest.name + ".pre-snapshot-entry")
        if not backup.exists():
            backup.write_bytes(dest.read_bytes())
    tmp = dest.with_name(f".{dest.name}.tmp{os.getpid()}")
    tmp.write_text(launcher_source(current, interpreter), encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, dest)
    return {"entry": str(dest), "deploy_current": str(current), "points_to": str(target_root), "previous": previous,
            "backup": str(backup) if backup else None}


# ---------------------------------------------------------------------------
# `lybra --version`
# ---------------------------------------------------------------------------

def _git_head(root: Path) -> str:
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"HEAD 不明({type(exc).__name__})"
    if head.returncode != 0:
        return "HEAD 不明(非 git 仓)"
    return f"HEAD {head.stdout.strip()[:12]}" + (" +未提交改动" if dirty.returncode == 0 and dirty.stdout.strip() else "")


def version_line(running: RunningCode | None = None) -> str:
    rc = running or running_code()
    if rc.mode == "deploy":
        return f"lybra 部署快照 {rc.release}(commit {rc.commit or '不明'}); 代码根 {rc.code_root}"
    if rc.mode == "dev":
        return f"lybra 开发模式 {DEV_TREE_FLAG} {rc.code_root}(非部署快照; {_git_head(rc.code_root)})"
    return (f"lybra 工作树代码 {rc.code_root}(未经部署快照入口; {_git_head(rc.code_root)}; "
            f"装入口: tools/lybra-deploy install-entry)")


class VersionAction(argparse.Action):
    """`lybra --version`: 打印本进程运行的代码(部署快照 / 开发模式 / 工作树)一行后退出 0。"""

    def __init__(self, option_strings: list[str], dest: str = argparse.SUPPRESS, default: Any = argparse.SUPPRESS,
                 help: str | None = None) -> None:  # noqa: A002 — argparse 约定参数名
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser: argparse.ArgumentParser, namespace: Any, values: Any, option_string: str | None = None) -> None:
        print(version_line())
        parser.exit(0)


# ---------------------------------------------------------------------------
# CLI(tools/lybra-deploy 调用): python3 -m tools.aipos_cli.deploy_runtime install-entry --deploy-current <链接> --bin-dir <目录>
# ---------------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tools.aipos_cli.deploy_runtime",
                                     description="AIPOS-F152: lybra CLI 入口(从部署快照运行)安装")
    sub = parser.add_subparsers(dest="command", required=True)
    inst = sub.add_parser("install-entry", help="把 lybra 入口脚本写进 bin 目录(指向部署链接 current)")
    inst.add_argument("--deploy-current", required=True, help="部署链接(<产品仓>/.deploy/current)")
    inst.add_argument("--bin-dir", default=DEFAULT_BIN_DIR, help=f"入口所在目录(缺省 {DEFAULT_BIN_DIR})")
    args = parser.parse_args(argv)
    bin_dir = args.bin_dir
    try:
        result = install_entry(args.deploy_current, bin_dir)
    except (EntryError, OSError) as exc:
        print(f"[lybra-deploy] ERROR: lybra 入口未安装: {exc}", file=sys.stderr)
        return 1
    note = {"none": "新装", "launcher": "更新", "other": f"替换原有非本入口文件(已另存 {result['backup']})"}[result["previous"]]
    print(f"[lybra-deploy] ✓ lybra 入口{note}: {result['entry']} → {result['deploy_current']}(当前 {result['points_to']})")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
