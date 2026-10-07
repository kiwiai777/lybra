"""AIPOS-F109 件④(gap #40): run-all 自动发现执行器——测试清单不再逐卡登记。

原状: 每张卡往 tests/run-all.sh 末尾追加登记块, 并行卡合并必冲突(F95–F99 每次合并都撞; F111 以 git union 合并驱动止血)。
现: 测试清单文件(项目声明 test_contract.runall_path)写一行 `# lybra-runall: discover`, 本执行器按项目 test_file_globs
(与门「本卡测试文件」判据同一判定 workspace_config.is_test_file)发现产品仓内全部测试文件并逐个执行; 新测试文件零登记。
清单内声明行(唯一解析 workspace_config.runall_directives, 门交回检查共用):
  exclude <目标> <理由>   不执行, 逐条打印(不静默); 文件级排除 = 门视为未登记(TEST_NOT_IN_RUNALL)
  known-failure <目标>     执行, 已知存量失败被容忍; 目标转绿 / 节点已不存在 = 报红, 须删该行(只减不增)
输出格式与原清单一致: 每文件「── <文件> ──」段 + 子进程输出 + `✓ <文件> PASS` / `✗ <文件> FAIL` 行(各卡基线 grep 不失效)。

夹具隔离: 子进程 HOME = 每次执行新建的临时目录(PYTHONUSERBASE 钉回原用户 site, 已装工具照用), 清掉 LYBRA_*/AIPOS_* 环境变量——
自动发现会纳入未经逐个审看的存量测试, 不得借真实 ~/.lybra 解析到真实治理根/工位/门凭据(执行体禁写真实治理根)。
环境构造唯一实现 isolated_test_env; 各测试目录 conftest.py 经 isolate_test_session 复用(直接跑 pytest 同样隔离, AIPOS-F116 件②)。

AIPOS-F116 防护夹具(执行器内建, 每个测试文件前后各取一次, 变则该文件判红并出声):
  真实治理根守卫(件①, gap #34/#70): 监视「执行器自身环境」(测试子进程隔离之前的真实 HOME / LYBRA_HOME_ROOT)按
    workspace_config.resolve_home_root_with_source 同一梯解析出的 home 根下各项目 governance/ 与 5_tasks/——git status 脏项
    (状态码 + 文件 size/mtime)与关键日志(governance/*_log.md)md5。只读: git 一律 --no-optional-locks(不刷新/不写 index)。
    时间窗归因复核: 某文件期间出现变动 → 单独重跑该文件一次(结果不计)再快照; 复现 = 该文件所致 → 判红; 不复现 = 同时段
    门/顾问的正常写入(他卡认领/交回、治理文档提交)→ 照列不判红。代价: 真污染的测试在复核时再写一次(已知污染源须 exclude)。
    AIPOS-F124 件④(F121/F122 合并后异步回归 f107 持续红的真因): 「复现」须落在首轮变动的同一落点(同一检查类 + 同一父目录,
    attribute_rerun)——测试的污染位置是确定的(同一文件, 或同一目录下新时间戳文件); 复核窗口里落在别处的变动(F122 实例: 首轮
    governance/FOUNDATION-BACKLOG.md, 重跑窗口 queue/claimed→completed 移卡 + closures/ 结案记录 = loop 在合并后异步回归运行期间
    继续推进的结案写入)= 同时段他方写入, 照列不判红。已知局限: 他方恰在重跑窗口写入首轮同一目录 → 仍判红(偏保守, 不放过污染)。
  孤儿进程守卫(件③, gap #12/#25/#55): 测试子进程环境带本轮唯一标记变量(不带 LYBRA_/AIPOS_ 前缀, 嵌套执行器不剥);
    文件结束后仍带标记的存活进程(含另起会话逃出进程组收尸的 web.board.app / serve 子进程)= 泄漏 → SIGKILL 并判红。

用法(由 tests/run-all.sh 调用, cwd = 产品仓根):
  python3 -m tools.aipos_cli.runall_discovery --runall tests/run-all.sh [--governance-root <治理根>] [--list]
"""
from __future__ import annotations

import argparse
import atexit
import fnmatch
import hashlib
import os
import re
import secrets
import shutil
import signal
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

#: 执行器表(文件名式样 → 执行方式)。命中 test_file_globs 却无执行器的文件 = 红(须补执行器或 exclude 并写理由), 不静默跳过。
RUNNERS: tuple[tuple[str, str], ...] = (
    ("*.py", "pytest"),
    ("*.sh", "bash"),
    ("*.test.ts", "node"),
    ("*.test.js", "node"),
)
#: 单文件超时(秒); 超时 = 红。
FILE_TIMEOUT_SECONDS = 900
#: pytest 退出码 5 = 未收集到用例 → 脚本式夹具, 以 python3 直跑。
PYTEST_NO_TESTS_COLLECTED = 5
_SUMMARY_RE = re.compile(r"^(FAILED|ERROR) (.+?)(?: - .*)?$")
_RULE = "─" * 42
#: 测试进程「已隔离」标记(值 = 隔离 HOME): conftest 会话层见标记 == HOME 即不再二次隔离(不用 LYBRA_ 前缀: 非产品环境变量, 不入 F106 声明集)。
ISOLATED_HOME_ENV = "RUNALL_ISOLATED_HOME"
#: 孤儿进程守卫标记变量名前缀(后接本轮随机串, 值 "1"); 故意不用 LYBRA_/AIPOS_ 前缀——嵌套执行器只剥这两类, 外层标记一路下传。
LEAK_MARK_PREFIX = "RUNALL_LEAK_MARK_"
#: 真实治理根守卫监视的项目子目录与关键日志式样(卡面: home 根下各项目 governance/ 与 5_tasks/; 关键日志 = 只追加的治理日志)。
GUARD_SUBDIRS = ("governance", "5_tasks")
GUARD_KEY_LOG_GLOB = "*_log.md"


def runner_for(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    for pattern, runner in RUNNERS:
        if fnmatch.fnmatchcase(name, pattern):
            return runner
    return None


def repo_files(repo_root: Path) -> list[str]:
    """产品仓文件清单: git 跟踪 + 未跟踪但未被忽略(本地新写未提交的测试也执行)。git 失败 = 抛(fail-closed)。"""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    return sorted({p for p in out.split("\0") if p and (repo_root / p).is_file()})


def build_plan(repo_root: Path, runall_rel: str, contract: dict[str, Any]) -> dict[str, Any]:
    """发现 + 声明行 → 执行计划。声明目标不存在 / 不是发现集里的测试文件 = problems(执行器报红)。"""
    from tools.aipos_cli.workspace_config import discover_test_files, runall_directives

    runall_path = repo_root / runall_rel
    directives = runall_directives(runall_path.read_text(encoding="utf-8"))
    contract = {**contract, "runall_path": runall_rel}
    discovered = discover_test_files(repo_files(repo_root), contract)
    discovered_set = set(discovered)
    problems: list[str] = []
    for bucket, word in (("exclude", "exclude"), ("known_failures", "known-failure")):
        for target in directives[bucket]:
            file_part = target.split("::", 1)[0]
            if file_part not in discovered_set:
                problems.append(
                    f"{runall_rel} 声明 `{word} {target}`: 文件 {file_part} 不在自动发现集(不存在或不命中 test_file_globs)——已删/改名的测试须同步删此行"
                )
    file_excluded = {t: r for t, r in directives["exclude"].items() if "::" not in t}
    to_run = [p for p in discovered if p not in file_excluded]
    return {
        "discover": directives["discover"],
        "discovered": discovered,
        "to_run": to_run,
        "file_excluded": file_excluded,
        "node_excluded": {t: r for t, r in directives["exclude"].items() if "::" in t},
        "known_failures": directives["known_failures"],
        "problems": problems,
        "globs": list(contract["test_file_globs"]),
        "globs_source": contract.get("test_file_globs_source"),
    }


def isolated_test_env(home: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """测试进程环境的唯一构造(run-all 子进程与 conftest 会话层共用): 清 LYBRA_*/AIPOS_*(含 LYBRA_HOME_ROOT), HOME = 给定临时目录,
    PYTHONUSERBASE 钉回原用户 site(已装工具照用), 打已隔离标记。"""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not (k.startswith("LYBRA_") or k.startswith("AIPOS_"))}
    env["PYTHONUSERBASE"] = source.get("PYTHONUSERBASE") or site.getuserbase()
    env["HOME"] = home
    env[ISOLATED_HOME_ENV] = home
    return env


def isolate_test_session() -> str | None:
    """AIPOS-F116 件②(gap #56): pytest 会话层 HOME 隔离, 由各测试目录 conftest.py 在导入时调用(早于测试模块导入——
    模块级 Path.home() 也拿到临时 HOME)。已由 run-all 执行器隔离(标记 == HOME)→ 不动, 返回 None; 否则新建临时 HOME 原地改写
    os.environ(isolated_test_env 同一构造), 进程退出时删除, 返回该目录。直接跑 pytest 也不借真实 ~/.lybra 解析到真实治理根。"""
    marked = os.environ.get(ISOLATED_HOME_ENV)
    if marked and marked == os.environ.get("HOME"):
        return None
    home = tempfile.mkdtemp(prefix="lybra-test-home-")
    env = isolated_test_env(home)
    os.environ.clear()
    os.environ.update(env)
    atexit.register(shutil.rmtree, home)
    return home


def _child_env(home: str, leak_mark: str) -> dict[str, str]:
    env = isolated_test_env(home)
    env[leak_mark] = "1"
    return env


# ---------------------------------------------------------------------------
# AIPOS-F116 件①: 真实治理根守卫(只读)
# ---------------------------------------------------------------------------
def _git_readonly(cwd: Path, *args: str) -> str:
    """只读 git: --no-optional-locks(status 不刷新/不写 index)。失败 = 抛(fail-closed)。"""
    return subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def governance_guard_targets(env: Mapping[str, str]) -> dict[str, Any]:
    """监视对象: env(= 执行器自身环境, 测试子进程隔离之前)按 workspace_config.resolve_home_root_with_source 同一梯解析 home 根,
    其下各项目的 governance/ 与 5_tasks/; 按所在 git 仓分组(非 git 目录单列, 退回逐文件 size/mtime)。"""
    from tools.aipos_cli.workspace_config import resolve_home_root_with_source

    home_root, source = resolve_home_root_with_source(env=dict(env))
    dirs = []
    if home_root.is_dir():
        for child in sorted(home_root.iterdir()):
            if child.is_dir():
                dirs += [child / sub for sub in GUARD_SUBDIRS if (child / sub).is_dir()]
    repos: dict[Path, list[str]] = {}
    plain: list[Path] = []
    for d in dirs:
        inside = subprocess.run(["git", "--no-optional-locks", "rev-parse", "--is-inside-work-tree"], cwd=d,
                                capture_output=True, text=True)
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            top = Path(_git_readonly(d, "rev-parse", "--show-toplevel").strip())
            repos.setdefault(top, []).append(d.relative_to(top).as_posix())
        else:
            plain.append(d)
    key_logs = sorted(p for d in dirs if d.name == "governance" for p in d.glob(GUARD_KEY_LOG_GLOB) if p.is_file())
    return {"home_root": home_root, "source": source, "dirs": dirs, "repos": repos, "plain": plain, "key_logs": key_logs}


def _stat_fp(path: Path) -> str:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return "<missing>"
    return f"{st.st_size}:{st.st_mtime_ns}"


def governance_snapshot(targets: dict[str, Any]) -> dict[str, str]:
    """只读快照 {键: 指纹}: git 仓内 status 脏项(状态码 + size/mtime); 非 git 目录逐文件 size/mtime; 关键日志 md5。"""
    snap: dict[str, str] = {}
    for top, rels in targets["repos"].items():
        raw = _git_readonly(top, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *rels)
        entries = raw.split("\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if not entry:
                continue
            code, rel = entry[:2], entry[3:]
            if "R" in code or "C" in code:  # -z 格式: 重命名/复制后跟原路径一项
                i += 1
            path = top / rel
            snap[f"status {path}"] = f"{code} {_stat_fp(path)}"
    for d in targets["plain"]:
        for path in sorted(p for p in d.rglob("*") if p.is_file()):
            snap[f"file {path}"] = _stat_fp(path)
    for log in targets["key_logs"]:
        snap[f"md5 {log}"] = hashlib.md5(log.read_bytes()).hexdigest() if log.is_file() else "<missing>"
    return snap


def changed_keys(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """两次快照之差的键(任一键出现/消失/指纹变 = 一处变动), 按键排序。"""
    return [key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]


def snapshot_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """两次快照之差(任一键出现/消失/指纹变 = 一处变动), 按键排序。"""
    return [f"{key}: {before.get(key, '(无)')} → {after.get(key, '(无)')}" for key in changed_keys(before, after)]


def _change_site(key: str) -> tuple[str, str]:
    """快照键(`<检查类> <绝对路径>`)→ 落点 (检查类, 父目录)。"""
    kind, _sep, path = key.partition(" ")
    return kind, str(Path(path).parent)


def attribute_rerun(first_keys: list[str], rerun_keys: list[str]) -> tuple[list[str], list[str]]:
    """AIPOS-F124 件④: 时间窗归因复核的判定(唯一实现)。first_keys = 本文件首轮期间的变动键, rerun_keys = 单独重跑期间的变动键。
    重跑变动落在首轮任一变动的同一落点(同检查类 + 同父目录)= 复现 → 归本文件; 落在别处 = 同时段他方写入。返回 (归本文件, 他方)。"""
    sites = {_change_site(k) for k in first_keys}
    mine = [k for k in rerun_keys if _change_site(k) in sites]
    return mine, [k for k in rerun_keys if _change_site(k) not in sites]


# ---------------------------------------------------------------------------
# AIPOS-F116 件③: 孤儿进程守卫
# ---------------------------------------------------------------------------
def marked_processes(leak_mark: str) -> list[tuple[int, str]]:
    """环境里带标记变量的存活进程 [(pid, 命令行)]。Linux 读 /proc/<pid>/environ; 无 /proc 退回 `ps eww`(BSD/macOS)。"""
    needle = f"{leak_mark}=1"
    found: list[tuple[int, str]] = []
    me = os.getpid()
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) == me:
                continue
            try:
                environ = (entry / "environ").read_bytes().split(b"\0")
                cmdline = (entry / "cmdline").read_bytes()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue  # 已退出 / 他用户进程(不可能带本轮标记)
            if needle.encode() in environ:
                found.append((int(entry.name), cmdline.replace(b"\0", b" ").decode("utf-8", "replace").strip()))
        return sorted(found)
    listing = subprocess.run(["ps", "-axeww", "-o", "pid=,command="], capture_output=True, text=True, check=True).stdout
    for line in listing.splitlines():
        pid_text, _sep, rest = line.strip().partition(" ")
        if pid_text.isdigit() and int(pid_text) != me and needle in rest.split():
            found.append((int(pid_text), rest.split(f" {needle}", 1)[0]))
    return sorted(found)


def reap_marked(leak_mark: str) -> list[tuple[int, str]]:
    """SIGKILL 所有带标记的存活进程, 返回被清的 [(pid, 命令行)](调用方出声并判红)。"""
    leaked = marked_processes(leak_mark)
    for pid, _cmd in leaked:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue  # 取快照与清理之间自行退出
    return leaked


def _command(path: str, runner: str, node_excludes: list[str]) -> list[str]:
    if runner == "pytest":
        cmd = [sys.executable, "-m", "pytest", path, "-v", "--tb=short", "-rfE", "-p", "no:cacheprovider"]
        for node in node_excludes:
            cmd += ["--deselect", node]
        return cmd
    if runner == "bash":
        return ["bash", path]
    if runner == "node":
        return ["node", path]
    raise ValueError(f"未知执行器 {runner!r}")


def _reap_group(pgid: int) -> bool:
    """测试子进程组收尾: 组内仍有存活进程(测试拉起的后台服务未收尾)= SIGKILL 整组并返回 True(调用方出声)。"""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    return True


def _run(cmd: list[str], repo_root: Path, env: dict[str, str]) -> tuple[int | None, str]:
    """独立进程组执行一个测试文件; 结束(或超时)后清掉组内残留进程——夹具环境不留孤儿(测试泄漏的后台进程照实出声)。"""
    proc = subprocess.Popen(cmd, cwd=repo_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=FILE_TIMEOUT_SECONDS)
        rc: int | None = proc.returncode
        tail = ""
    except subprocess.TimeoutExpired:
        _reap_group(proc.pid)
        stdout, stderr = proc.communicate()
        rc, tail = None, f"\n[runall_discovery] 超时 {FILE_TIMEOUT_SECONDS}s, 判红\n"
    if _reap_group(proc.pid):
        tail += "\n[runall_discovery] 测试结束后其进程组仍有存活进程(未收尾的后台服务), 已整组 SIGKILL 清理\n"
    return rc, (stdout or "") + (stderr or "") + tail


def judge(path: str, runner: str, rc: int | None, output: str, known: dict[str, str]) -> tuple[bool, list[str]]:
    """一个文件的执行结果 + 已知存量失败声明 → (通过?, 说明行)。known-failure 严格: 未声明的失败 = 红; 声明了却没失败 = 红(转绿须删条)。"""
    notes: list[str] = []
    if rc is None:
        return False, ["超时"]
    file_known = path in known
    node_known = {t for t in known if t.startswith(path + "::")}
    if file_known:
        if rc == 0:
            return False, [f"声明 `known-failure {path}`(整文件)但本次通过 → 已转绿, 须从清单删此行(只减不增)"]
        notes.append(f"已知存量失败(整文件, 声明 known-failure {path}): 退出码 {rc} 被容忍")
        return True, notes
    if runner != "pytest" or not node_known:
        if rc != 0:
            return False, [f"退出码 {rc}"]
        return True, notes
    failed = {m.group(2).strip() for line in output.splitlines() if (m := _SUMMARY_RE.match(line.strip()))}
    unexpected = sorted(failed - node_known)
    stale = sorted(node_known - failed)
    ok = True
    if unexpected:
        ok = False
        notes += [f"新增失败(不在 known-failure 声明): {node}" for node in unexpected]
    if stale:
        ok = False
        notes += [f"声明 `known-failure {node}` 本次未失败(已转绿或节点已不存在)→ 须从清单删此行(只减不增)" for node in stale]
    if rc != 0 and not failed:
        ok = False
        notes.append(f"退出码 {rc} 但未解析到失败节点(收集/内部错误)")
    if ok and node_known:
        notes.append(f"已知存量失败 {len(node_known)} 条被容忍(声明 known-failure)")
    return ok, notes


_SECTION_RE = re.compile(r"^── (\S+)")
_RESULT_RE = re.compile(r"^([✓✗]) (\S+) (PASS|FAIL)$")


def failure_set(output: str, exit_code: int | None, runall_rel: str) -> list[str]:
    """AIPOS-F118 件①: 测试清单一次运行的失败集合——唯一解析(finalize 合并后回归比对用; 口径与 run-all 基线 `grep "^✗"` 同一)。

    条目: 每条 `✗ <文件> FAIL` 行 → <文件>; 其余 `✗` 行(声明行失效等)→ 该行去 `✗ ` 的原文; 失败文件段(`── <文件>` 起至其 ✗ 行)内
    pytest 摘要 `FAILED|ERROR <节点>` → <节点>(同一文件已红时新增失败节点仍可见)。known-failure 被容忍的文件段以 ✓ 收尾, 其节点不计。
    退出码非 0 却无任何 `✗` 行(非本格式的项目清单 / 中途崩溃)→ 一条「<清单> 退出码 N(未解析到 ✗ 行)」, 不当作通过(fail-closed)。
    返回去重排序列表。"""
    failures: set[str] = set()
    section_nodes: list[str] = []
    saw_cross = False
    for raw in output.splitlines():
        line = raw.rstrip()
        if _SECTION_RE.match(line):
            section_nodes = []
            continue
        summary = _SUMMARY_RE.match(line.strip())
        if summary:
            section_nodes.append(summary.group(2).strip())
            continue
        result = _RESULT_RE.match(line)
        if result:
            if result.group(1) == "✗":
                saw_cross = True
                failures.add(result.group(2))
                failures.update(section_nodes)
            section_nodes = []
            continue
        if line.startswith("✗"):
            saw_cross = True
            failures.add(line[1:].strip())
    if exit_code != 0 and not saw_cross:
        failures.add(f"{runall_rel} 退出码 {exit_code}(未解析到 ✗ 行)")
    return sorted(failures)


def run(repo_root: Path, runall_rel: str, contract: dict[str, Any], *, out=None,
        guard_env: Mapping[str, str] | None = None) -> int:
    """guard_env: 真实治理根守卫解析 home 根所用环境(缺省 = 执行器自身 os.environ, 即测试子进程隔离之前的真实环境)。"""
    out = out or sys.stdout
    plan = build_plan(repo_root, runall_rel, contract)

    def emit(text: str = "") -> None:
        print(text, file=out, flush=True)

    if not plan["discover"]:
        emit(f"✗ {runall_rel} 无 `discover` 声明行, 自动发现未启用 FAIL")
        return 1
    emit(f"[runall_discovery] 式样 {plan['globs']}(来源 {plan['globs_source']}); 发现 {len(plan['discovered'])} 个测试文件, "
         f"执行 {len(plan['to_run'])}, 声明排除 {len(plan['file_excluded'])} 个文件 + {len(plan['node_excluded'])} 个节点, "
         f"已知存量失败声明 {len(plan['known_failures'])} 条")
    overall = 0
    for problem in plan["problems"]:
        emit(f"✗ 声明行失效: {problem}")
        overall = 1
    for target, reason in sorted({**plan["file_excluded"], **plan["node_excluded"]}.items()):
        emit(f"⊘ 未执行(声明 exclude): {target} —— {reason}")
    targets = governance_guard_targets(os.environ if guard_env is None else guard_env)
    emit(f"[runall_discovery] 真实治理根守卫: home 根 {targets['home_root']}(来源 {targets['source']}); 监视 {len(targets['dirs'])} 个目录"
         f"(各项目 {'/'.join(GUARD_SUBDIRS)}/; git 仓 {len(targets['repos'])} 个, 非 git 目录 {len(targets['plain'])} 个), "
         f"关键日志 {len(targets['key_logs'])} 个(md5); 只读")
    leak_mark = LEAK_MARK_PREFIX + secrets.token_hex(8)
    snapshot = governance_snapshot(targets)
    guard_changes = 0
    guard_concurrent = 0
    leaks_total = 0
    home = tempfile.mkdtemp(prefix="lybra-runall-home-")

    def _on_sigterm(signum: int, _frame: Any) -> None:
        # AIPOS-F118: 被上游终止(finalize 合并后回归超时先 SIGTERM 整组)时, 在跑的测试子进程在独立会话里、上游 killpg 够不着——
        # 清理与文件结束后的孤儿收尸同一实现(reap_marked: 带本轮标记的存活进程一律 SIGKILL), 不另设进程组登记。
        reap_marked(leak_mark)
        raise SystemExit(128 + signum)

    previous_handler = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        env = _child_env(home, leak_mark)
        for path in plan["to_run"]:
            emit()
            emit(f"── {path} {_RULE}")
            runner = runner_for(path)
            if runner is None:
                emit(f"无执行器(文件名不命中 {[p for p, _r in RUNNERS]}); 补执行器或在清单 exclude 并写理由")
                emit(f"✗ {path} FAIL")
                overall = 1
                continue
            node_excludes = [t for t in plan["node_excluded"] if t.startswith(path + "::")]
            cmd = _command(path, runner, node_excludes)
            rc, output = _run(cmd, repo_root, env)
            if runner == "pytest" and rc == PYTEST_NO_TESTS_COLLECTED:
                emit("[runall_discovery] pytest 未收集到用例 → 脚本式夹具, 以 python3 直跑")
                runner = "script"
                cmd = [sys.executable, path]
                rc, output = _run(cmd, repo_root, env)
            emit(output.rstrip("\n"))
            ok, notes = judge(path, runner, rc, output, plan["known_failures"])
            leaked = reap_marked(leak_mark)
            if leaked:
                ok = False
                leaks_total += len(leaked)
                notes += [f"孤儿进程守卫: 测试结束后仍存活(逃出进程组收尸), 已 SIGKILL: pid {pid} {line}" for pid, line in leaked]
            after = governance_snapshot(targets)
            before_file = snapshot
            changed = snapshot_changes(snapshot, after)
            snapshot = after
            if changed:
                # 时间窗归因复核: 单独重跑本文件一次(测试结果不计, 只看守卫), 前后再快照。在首轮同一落点复现 = 本文件所致 → 红;
                # 不复现 / 只在他处变动 = 同时段他方(门/顾问)的正常写入 → 照列不判红(判定 attribute_rerun, AIPOS-F124 件④)。
                _rc_again, _out_again = _run(cmd, repo_root, env)
                leaked_again = reap_marked(leak_mark)
                if leaked_again:
                    ok = False
                    leaks_total += len(leaked_again)
                    notes += [f"孤儿进程守卫(复核重跑): 已 SIGKILL: pid {pid} {line}" for pid, line in leaked_again]
                again = governance_snapshot(targets)
                rerun_keys = changed_keys(snapshot, again)
                rerun_line = dict(zip(rerun_keys, snapshot_changes(snapshot, again)))
                mine, elsewhere = attribute_rerun(changed_keys(before_file, after), rerun_keys)
                snapshot = again
                if mine:
                    ok = False
                    guard_changes += len(mine)
                    notes += [f"真实治理根守卫: 本文件执行期间真实治理根变动: {c}" for c in changed]
                    notes += [f"真实治理根守卫: 单独重跑本文件复现变动(判为本文件所致): {rerun_line[k]}" for k in mine]
                    guard_concurrent += len(elsewhere)
                    notes += [f"真实治理根守卫: 单独重跑期间他处变动(落点与本文件首轮变动不重合) → 判为同时段他方写入, 不计本文件: "
                              f"{rerun_line[k]}" for k in elsewhere]
                elif elsewhere:
                    guard_concurrent += len(changed) + len(elsewhere)
                    notes += [f"真实治理根守卫: 本文件执行期间有变动, 单独重跑未在同一落点复现 → 判为同时段他方写入(门/顾问), 不计本文件: {c}"
                              for c in changed]
                    notes += [f"真实治理根守卫: 单独重跑期间他处变动(落点与本文件首轮变动不重合) → 判为同时段他方写入, 不计本文件: "
                              f"{rerun_line[k]}" for k in elsewhere]
                else:
                    guard_concurrent += len(changed)
                    notes += [f"真实治理根守卫: 本文件执行期间有变动, 单独重跑未复现 → 判为同时段他方写入(门/顾问), 不计本文件: {c}"
                              for c in changed]
            for note in notes:
                emit(f"[runall_discovery] {note}")
            if ok:
                emit(f"✓ {path} PASS")
            else:
                emit(f"✗ {path} FAIL")
                overall = 1
        emit()
        emit(f"[runall_discovery] 真实治理根守卫汇总: 测试所致变动 {guard_changes} 处"
             + ("" if guard_changes else "(无测试写入真实治理根)")
             + f"; 同时段他方写入(重跑未复现, 不计) {guard_concurrent} 处")
        emit(f"[runall_discovery] 孤儿进程守卫汇总: 泄漏进程 {leaks_total} 个" + ("" if leaks_total else "(无带本轮标记的存活进程)"))
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        try:
            shutil.rmtree(home)
        except OSError as exc:  # 清理失败出声, 不吞
            print(f"[runall_discovery] 警告: 临时 HOME {home} 未能清理: {exc}", file=sys.stderr)
    return overall


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIPOS-F109: run-all 自动发现执行器")
    parser.add_argument("--runall", required=True, help="测试清单文件(相对产品仓根; = 项目 test_contract.runall_path)")
    parser.add_argument("--repo-root", default=".", help="产品仓根(缺省 cwd)")
    parser.add_argument("--governance-root", help="治理根: 给了按项目 project.json test_contract 取式样, 否则取 config.schema 缺省")
    parser.add_argument("--list", action="store_true", help="只打印执行计划(发现集/排除/已知失败), 不执行")
    args = parser.parse_args(argv)
    from tools.aipos_cli.workspace_config import default_test_contract, project_test_contract

    repo_root = Path(args.repo_root).resolve()
    contract = project_test_contract(args.governance_root, repo_root) if args.governance_root else default_test_contract()
    if args.list:
        plan = build_plan(repo_root, args.runall, contract)
        for path in plan["to_run"]:
            print(f"run\t{runner_for(path) or '-'}\t{path}")
        for target, reason in sorted({**plan["file_excluded"], **plan["node_excluded"]}.items()):
            print(f"exclude\t{target}\t{reason}")
        for target in sorted(plan["known_failures"]):
            print(f"known-failure\t{target}")
        for problem in plan["problems"]:
            print(f"problem\t{problem}")
        return 1 if plan["problems"] or not plan["discover"] else 0
    return run(repo_root, args.runall, contract)


if __name__ == "__main__":
    sys.exit(main())
