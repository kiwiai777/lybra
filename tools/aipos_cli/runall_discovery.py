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

用法(由 tests/run-all.sh 调用, cwd = 产品仓根):
  python3 -m tools.aipos_cli.runall_discovery --runall tests/run-all.sh [--governance-root <治理根>] [--list]
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import signal
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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


def _child_env(home: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not (k.startswith("LYBRA_") or k.startswith("AIPOS_"))}
    env["PYTHONUSERBASE"] = os.environ.get("PYTHONUSERBASE") or site.getuserbase()
    env["HOME"] = home
    return env


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


#: 正在执行的测试子进程组(独立会话, 上游 killpg 够不着)——收到 SIGTERM 时由 _on_sigterm 整组清掉(AIPOS-F118: 合并后回归超时
#: 先 SIGTERM 本执行器, 不留孤儿测试进程)。
_RUNNING_GROUPS: set[int] = set()


def _on_sigterm(signum: int, _frame: Any) -> None:
    for pgid in sorted(_RUNNING_GROUPS):
        _reap_group(pgid)
    raise SystemExit(128 + signum)


def _run(cmd: list[str], repo_root: Path, env: dict[str, str]) -> tuple[int | None, str]:
    """独立进程组执行一个测试文件; 结束(或超时)后清掉组内残留进程——夹具环境不留孤儿(测试泄漏的后台进程照实出声)。"""
    proc = subprocess.Popen(cmd, cwd=repo_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    _RUNNING_GROUPS.add(proc.pid)
    try:
        return _collect(proc)
    finally:
        _RUNNING_GROUPS.discard(proc.pid)


def _collect(proc: subprocess.Popen) -> tuple[int | None, str]:
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


def run(repo_root: Path, runall_rel: str, contract: dict[str, Any], *, out=None) -> int:
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
    home = tempfile.mkdtemp(prefix="lybra-runall-home-")
    previous_handler = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        env = _child_env(home)
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
            rc, output = _run(_command(path, runner, node_excludes), repo_root, env)
            if runner == "pytest" and rc == PYTEST_NO_TESTS_COLLECTED:
                emit("[runall_discovery] pytest 未收集到用例 → 脚本式夹具, 以 python3 直跑")
                runner = "script"
                rc, output = _run([sys.executable, path], repo_root, env)
            emit(output.rstrip("\n"))
            ok, notes = judge(path, runner, rc, output, plan["known_failures"])
            for note in notes:
                emit(f"[runall_discovery] {note}")
            if ok:
                emit(f"✓ {path} PASS")
            else:
                emit(f"✗ {path} FAIL")
                overall = 1
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
