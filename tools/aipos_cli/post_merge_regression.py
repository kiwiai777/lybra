"""AIPOS-F118(gap #77): finalize 合并后回归检查——合并结果上跑项目测试清单, 与合并前 main 的失败集合比对, 新红按声明处置。

病根(10-06 实证): F102 与 F106 各自审计 PASS, 合入后 main 新增一红, 由后一张卡顺修才发现; 单卡审计只比对单卡与当时 main,
看不到几张卡合在一起才出现的组合失败。

本模块 = 唯一实现(finalize 同步调用 / async 后台子进程同一函数 execute_check):
  - 策略: 项目声明 config.schema test_contract.post_merge_regression(唯一读取口 workspace_config.project_test_contract),
    mode = warn | block | off(缺省 warn, 说理见声明), execution = sync | async, timeout_seconds = 整个检查总时限。
  - 跑法: 复用测试清单本身(`bash <runall_path>`, 不另写执行器), 在合并提交的临时 detached worktree 里跑(产品仓工作树里的
    未跟踪文件不混入), 整个进程组超时 SIGKILL; 用完 worktree remove。
  - 失败集合口径: runall_discovery.failure_set(`✗` 行 + 失败文件段内 pytest FAILED/ERROR 节点; 与 run-all 基线 grep 同口径)。
  - 合并前集合: 先取「上次合并时记录的基线」——finalization 记录 / 回归事件记录里 merge_commit = 合并前 main(合并提交第一父)
    且合并后集合完整的那份(免每次跑两遍); 找不到才在合并前提交上另跑一次。
  - 结果: finalization 记录字段 post_merge_regression(声明 transitions N5.record.post_merge_regression); block 撤销合并 / async
    后台完成时落回归事件记录(records/events/<卡>/post_merge_regression_*.md, 记录类 events)。
  - 输出: finalize 与 loop 各输出一行, 行首 MARKER(唯一出处)。无新红 = 只这一行(零噪音)。
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

#: finalize / loop 输出本检查结果的那一行的行首(唯一出处; loop_driver 按此前缀从 finalize 输出里取行)。
MARKER = "合并后回归:"
#: 回归事件记录的 event_type(落 records/events/<卡>/<event_type>_<ts>.md; 基线查找按此前缀扫描)。
EVENT_TYPE = "post_merge_regression"
#: 处置为告警/阻断的状态(检查本身失败与新增失败同等处置, 不当作通过)。
ACTIONABLE_STATUSES = ("new_failures", "timeout", "error")
#: async 子进程句柄(本进程内可同步 wait; CLI 进程退出后子进程照跑——独立会话)。
ASYNC_CHILDREN: list[subprocess.Popen] = []
#: 记录里列出的失败条目上限之外截断(记录可读); 比对本身用全集。
_LIST_PREVIEW = 8


def resolve_policy(governance_root: Path, repo_root: Path) -> dict[str, Any]:
    """项目策略(runall_path + post_merge_regression)。形坏 = ValueError(TEST_CONTRACT_INVALID) / SchemaLoadError 原样抛(fail-closed)。"""
    from tools.aipos_cli.next_resolver import loop_step_timeout_seconds
    from tools.aipos_cli.workspace_config import project_test_contract

    contract = project_test_contract(governance_root, repo_root)
    pmr = contract["post_merge_regression"]
    return {
        # 合并前读入(AIPOS-F120: 合并改写代码源, 合并后不再读声明), 供 sync 时限 ≥ loop 步超时的提示用
        "loop_step_timeout_seconds": loop_step_timeout_seconds(),
        "runall_path": contract["runall_path"],
        "mode": pmr["mode"],
        "execution": pmr["execution"],
        "timeout_seconds": pmr["timeout_seconds"],
        "policy_source": pmr["source"],
        "runall_source": contract["source"],
    }


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True, text=True)


def merge_parent(repo_root: Path, merge_commit: str) -> str:
    """合并前 main = 合并提交第一父。取不到 = ValueError(调用方按 error 处置)。"""
    res = _git(repo_root, "rev-parse", "--verify", f"{merge_commit}^1")
    if res.returncode != 0 or not res.stdout.strip():
        raise ValueError(f"取不到合并提交 {merge_commit[:12]} 的第一父: {res.stderr.strip()}")
    return res.stdout.strip()


def _popen_group(cmd: list[str], *, cwd: Path, env: dict[str, str] | None, stdout: Any) -> subprocess.Popen:
    """本检查唯一的子进程拉起口(测试清单一跑 / async 后台检查): 独立会话(进程组号 = pid), 超时或收尾按组清理, stdin 关闭。
    不是 harness 拉起层(agent 拉起唯一在 harness_launch)。"""
    return subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=stdout, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, start_new_session=True)


#: 超时终止: 先 SIGTERM 整组(run-all 执行器据此清掉其独立会话里的测试子进程组, runall_discovery._on_sigterm), 宽限后 SIGKILL。
_TERM_GRACE_SECONDS = 15


def _terminate(proc: subprocess.Popen) -> str:
    from tools.aipos_cli.runall_discovery import _reap_group

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:  # 组已全部退出 = 无可终止, 照收输出
        return proc.communicate()[0] or ""
    try:
        output, _ = proc.communicate(timeout=_TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _reap_group(proc.pid)
        output, _ = proc.communicate()
    return output or ""


def run_suite(repo_root: Path, commit: str, runall_rel: str, timeout: float) -> dict[str, Any]:
    """在 commit 的临时 detached worktree 里跑测试清单一次。返回 {commit, status: ran|timeout|error, exit_code, failures, log, seconds, error}。"""
    from tools.aipos_cli.runall_discovery import _reap_group, failure_set

    started = time.monotonic()
    result: dict[str, Any] = {"commit": commit, "status": "error", "exit_code": None, "failures": None, "log": None, "error": None}
    if timeout <= 0:
        result.update(status="timeout", error="检查总时限已用尽, 未开跑", seconds=0)
        return result
    tmp = Path(tempfile.mkdtemp(prefix="lybra-pmr-"))
    worktree = tmp / "wt"
    log_path = Path(tempfile.gettempdir()) / f"lybra-post-merge-regression-{commit[:12]}-{os.getpid()}-{int(time.time())}.log"
    added = _git(repo_root, "worktree", "add", "--detach", str(worktree), commit)
    try:
        if added.returncode != 0:
            result["error"] = f"git worktree add {commit[:12]} 失败: {added.stderr.strip()}"
            return result
        if not (worktree / runall_rel).is_file():
            result["error"] = f"测试清单 {runall_rel} 在 {commit[:12]} 上不存在"
            return result
        proc = _popen_group(["bash", runall_rel], cwd=worktree, env=None, stdout=subprocess.PIPE)
        try:
            output, _ = proc.communicate(timeout=timeout)
            result["status"], result["exit_code"] = "ran", proc.returncode
        except subprocess.TimeoutExpired:
            output = _terminate(proc)
            result["status"], result["error"] = "timeout", f"测试清单在 {timeout:.0f}s 内未跑完, 已终止(SIGTERM 后整组 SIGKILL)"
        if _reap_group(proc.pid):
            output = (output or "") + "\n[post_merge_regression] 测试清单结束后进程组仍有存活进程, 已整组 SIGKILL\n"
        log_path.write_text(output or "", encoding="utf-8")
        result["log"] = str(log_path)
        if result["status"] == "ran":
            result["failures"] = failure_set(output or "", proc.returncode, runall_rel)
        return result
    finally:
        result["seconds"] = round(time.monotonic() - started, 1)
        if added.returncode == 0:
            removed = _git(repo_root, "worktree", "remove", "--force", str(worktree))
            if removed.returncode != 0:  # 清理失败出声, 不吞
                result["cleanup_warning"] = f"git worktree remove {worktree} 失败: {removed.stderr.strip()}"
        try:
            shutil.rmtree(tmp)
        except OSError as exc:  # 清理失败出声, 不吞
            result["cleanup_warning"] = f"临时目录 {tmp} 未能清理: {exc}"


def _recorded_regressions(governance_root: Path) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    """扫 finalization 记录与回归事件记录, 取各自的 post_merge_regression 字段(按 mtime 新→旧)。
    返回 (记录, 读不出的记录说明)——读不出的不作基线来源(基线缺则另跑一次, 不降级为通过), 说明进结果 notes 出声。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import record_dir

    candidates = list(record_dir(governance_root, "finalizations").glob("*/finalization_*.md"))
    candidates += list(record_dir(governance_root, "events").glob(f"*/{EVENT_TYPE}_*.md"))
    found: list[tuple[Path, dict[str, Any]]] = []
    unreadable: list[str] = []
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append(f"记录读不出(不作基线来源): {path}: {exc}")
            continue
        if f"{EVENT_TYPE}:" not in text:
            continue  # 无回归字段的存量记录(本检查上线前)不是候选, 不解析(不为无关记录出声)
        try:
            meta, _body = require_frontmatter(path, text)
        except FrontmatterReadError as exc:
            unreadable.append(f"记录读不出(不作基线来源): {exc}")
            continue
        pmr = meta.get("post_merge_regression")
        if isinstance(pmr, dict):
            found.append((path, pmr))
    return found, unreadable


def lookup_recorded_baseline(governance_root: Path, commit: str) -> dict[str, Any]:
    """「上次合并时记录的基线」: merge_commit == commit 且合并后集合完整(merged_complete)的最新一份记录。
    无 = {"failures": None, "unreadable": [...]}; 有 = {"failures", "ref", "previous", "unreadable"}。"""
    records, unreadable = _recorded_regressions(governance_root)
    for path, pmr in records:
        if str(pmr.get("merge_commit") or "") == commit and pmr.get("merged_complete") is True and isinstance(pmr.get("merged_failures"), list):
            try:
                ref = str(path.relative_to(governance_root))
            except ValueError:
                ref = str(path)
            return {"failures": [str(x) for x in pmr["merged_failures"]], "ref": ref, "previous": pmr, "unreadable": unreadable}
    return {"failures": None, "unreadable": unreadable}


def _preview(items: list[str]) -> str:
    head = ", ".join(items[:_LIST_PREVIEW])
    return head + (f" …(共 {len(items)} 条)" if len(items) > _LIST_PREVIEW else "")


def execute_check(*, governance_root: Path, repo_root: Path, merge_commit: str, runall_path: str,
                  timeout_seconds: int) -> dict[str, Any]:
    """同步执行检查(finalize sync 与 async 子进程同一函数)。返回记录形字段(不含策略处置)。"""
    deadline = time.monotonic() + timeout_seconds
    out: dict[str, Any] = {"merge_commit": merge_commit, "runall_path": runall_path, "timeout_seconds": timeout_seconds,
                           "merged_complete": False, "notes": []}
    try:
        pre = merge_parent(repo_root, merge_commit)
    except ValueError as exc:
        out.update(status="error", error=str(exc))
        return out
    out["pre_merge_commit"] = pre
    merged = run_suite(repo_root, merge_commit, runall_path, deadline - time.monotonic())
    out["merged_log"], out["merged_seconds"] = merged.get("log"), merged.get("seconds")
    if merged.get("cleanup_warning"):
        out["notes"].append(merged["cleanup_warning"])
    if merged["status"] != "ran":
        out.update(status=merged["status"], error=f"合并后一跑: {merged['error']}")
        return out
    out["merged_failures"], out["merged_complete"] = merged["failures"], True
    recorded = lookup_recorded_baseline(governance_root, pre)
    out["notes"].extend(recorded["unreadable"])
    if recorded["failures"] is not None:
        baseline = recorded["failures"]
        out["baseline_source"] = f"record:{recorded['ref']}"
        prev = recorded["previous"]
        if prev.get("execution") == "async" and prev.get("new_failures"):
            out["notes"].append(f"上次合并 {pre[:8]} 的异步回归结果: 新增失败 {len(prev['new_failures'])} 条 [{_preview(list(prev['new_failures']))}]")
    else:
        before = run_suite(repo_root, pre, runall_path, deadline - time.monotonic())
        out["baseline_log"], out["baseline_seconds"] = before.get("log"), before.get("seconds")
        if before.get("cleanup_warning"):
            out["notes"].append(before["cleanup_warning"])
        if before["status"] != "ran":
            out.update(status=before["status"], error=f"合并前基线一跑(无记录基线): {before['error']}")
            return out
        baseline = before["failures"]
        out["baseline_source"] = f"run:{pre}"
    out["baseline_failures"] = baseline
    out["new_failures"] = sorted(set(out["merged_failures"]) - set(baseline))
    out["fixed_failures"] = sorted(set(baseline) - set(out["merged_failures"]))
    out["status"] = "new_failures" if out["new_failures"] else "pass"
    return out


def summary_line(result: dict[str, Any]) -> str:
    """本检查结果的一行(行首 MARKER)。"""
    status, mode = result.get("status"), result.get("mode")
    if status == "skipped":
        return f"{MARKER} 跳过({result.get('reason')})"
    if status == "off":
        return f"{MARKER} 已关闭(项目声明 mode=off, 来源 {result.get('policy_source')}); 本次合并未做组合回归"
    if status == "async_started":
        return (f"{MARKER} 异步执行中(pid {result.get('async_pid')}, 日志 {result.get('async_log')}); "
                f"结果落回归事件记录 events/{result.get('task_id')}/{EVENT_TYPE}_*.md")
    if status == "pass":
        fixed = result.get("fixed_failures") or []
        return (f"{MARKER} 无新增失败(合并后 {len(result.get('merged_failures') or [])} 条失败 / 基线 "
                f"{len(result.get('baseline_failures') or [])} 条, 基线来源 {result.get('baseline_source')}"
                + (f", 转绿 {len(fixed)} 条" if fixed else "") + ")")
    disposition = {"warned": "策略 warn: 已记录, 合并照常", "blocked": "策略 block: 已撤销合并, 未推送未部署",
                   "recorded": f"策略 {mode}: 后台结果已落记录"}.get(str(result.get("action")), f"策略 {mode}")
    if status == "new_failures":
        new = list(result.get("new_failures") or [])
        return f"{MARKER} ✗ 新增失败 {len(new)} 条 [{_preview(new)}]({disposition})"
    return f"{MARKER} ✗ 检查未完成({status}: {result.get('error')})——不当作通过({disposition})"


def result_line(output: str) -> str | None:
    """从 finalize 输出(人读「  - <op>」或 --json 的 operations 字符串)取本检查结果那一行(行首 MARKER); 无 = None。loop 用。"""
    for raw in output.splitlines():
        text = raw.strip()
        if text.startswith("- "):
            text = text[2:].strip()
        text = text.rstrip(",").strip('"')
        if text.startswith(MARKER):
            return text
    return None


def write_event_record(governance_root: Path, task_id: str, actor: str, result: dict[str, Any]) -> str:
    """回归事件记录(block 撤销合并 / async 后台完成): 统一写入器 write_records_atomic, 记录类 events。返回相对路径。"""
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.schema_constants import RecordType

    timestamp = iso_z()
    record_id = f"{EVENT_TYPE}_{task_id}_{file_slug('compact', timestamp)}"
    metadata = {
        "record_type": RecordType.TASK_PROGRESS_EVENT,
        "event_type": EVENT_TYPE,
        "task_id": task_id,
        "actor": actor,
        "timestamp": timestamp,
        "post_merge_regression": result,
    }
    body = f"# Post-merge regression: {task_id}\n\n{summary_line(result)}\n"
    markdown = render_markdown(metadata, body, ["record_type", "event_type", "task_id", "actor", "timestamp", "post_merge_regression"])
    written = write_records_atomic(governance_root, [("event", record_id, markdown, task_id)])
    return str(written["paths"][0])


def _spawn_async(*, governance_root: Path, repo_root: Path, task_id: str, actor: str, merge_commit: str, policy: dict[str, Any]) -> dict[str, Any]:
    """拉起独立会话后台子进程跑同一 execute_check(本模块 run-async 入口), 立即返回。包根 = 本模块所在目录的实路径(不经
    .deploy/current 符号链接)。子进程是新进程, 从包根一次性加载一份代码(editable 安装时即合并后的代码), 不与 finalize 进程
    混用新旧(AIPOS-F120 导入闸管的是 finalize 本进程)。"""
    package_root = Path(__file__).resolve().parents[2]
    log_path = Path(tempfile.gettempdir()) / f"lybra-post-merge-regression-async-{merge_commit[:12]}-{os.getpid()}-{int(time.time())}.log"
    cmd = [sys.executable, "-m", "tools.aipos_cli.post_merge_regression", "run-async",
           "--governance-root", str(governance_root), "--repo-root", str(repo_root), "--task-id", task_id, "--actor", actor,
           "--merge-commit", merge_commit, "--runall-path", str(policy["runall_path"]),
           "--timeout-seconds", str(policy["timeout_seconds"]), "--mode", str(policy["mode"]),
           "--policy-source", str(policy["policy_source"])]
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    with open(log_path, "w", encoding="utf-8") as log:
        proc = _popen_group(cmd, cwd=package_root, env=env, stdout=log)
    ASYNC_CHILDREN.append(proc)
    return {"async_pid": proc.pid, "async_log": str(log_path)}


def check_after_merge(*, governance_root: Path, repo_root: Path, task_id: str, actor: str, merge_commit: str,
                      policy: dict[str, Any]) -> dict[str, Any]:
    """finalize 合并后入口(merge --no-ff 之后、push/deploy 之前)。返回记录形 dict, 含 status / action / summary。

    action: none(通过/关闭/跳过/异步已拉起) | warned(mode=warn 且 新增失败/超时/出错) | blocked(mode=block 且同上; 调用方撤销合并)。"""
    result: dict[str, Any] = {
        "task_id": task_id,
        "mode": policy["mode"],
        "execution": policy["execution"],
        "timeout_seconds": policy["timeout_seconds"],
        "policy_source": policy["policy_source"],
        "runall_path": policy["runall_path"],
        "merge_commit": merge_commit,
        "action": "none",
    }
    if not policy["runall_path"]:
        result.update(status="skipped", reason=f"项目未声明 test_contract.runall_path(来源 {policy['runall_source']})")
    elif policy["mode"] == "off":
        result.update(status="off")
    elif policy["execution"] == "async":
        result.update(status="async_started", **_spawn_async(governance_root=governance_root, repo_root=repo_root, task_id=task_id,
                                                            actor=actor, merge_commit=merge_commit, policy=policy))
    else:
        result.update(execute_check(governance_root=governance_root, repo_root=repo_root, merge_commit=merge_commit,
                                    runall_path=policy["runall_path"], timeout_seconds=policy["timeout_seconds"]))
        if result["status"] in ACTIONABLE_STATUSES:
            result["action"] = "blocked" if policy["mode"] == "block" else "warned"
        _loop_timeout_note(result, policy["loop_step_timeout_seconds"])
    result["summary"] = summary_line(result)
    return result


def _loop_timeout_note(result: dict[str, Any], step_timeout: float) -> None:
    """sync 且声明总时限 ≥ loop 步超时(verbs.schema lybra_loop.step_timeout_seconds, resolve_policy 合并前读入): loop 驱动下
    finalize 会在合并后被杀, 附提示。"""
    if result["timeout_seconds"] >= step_timeout:
        result.setdefault("notes", []).append(
            f"声明 timeout_seconds={result['timeout_seconds']} ≥ loop 步超时 {step_timeout:g}s: loop 驱动 finalize 时可能在合并后、记录前被杀;"
            " 全量清单耗时长的项目宜声明 execution=async 或 mode=off"
        )


def undo_merge(repo_root: Path, merge_commit: str, pre_merge_commit: str) -> tuple[bool, str]:
    """block 处置: main 复位到合并前提交(git reset --keep: 工作树有改动会被拒而不是被覆盖)。复位后 HEAD 必须 = 合并前提交。"""
    head = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    if head != merge_commit:
        return False, f"HEAD {head[:12]} 已不是本次合并提交 {merge_commit[:12]}, 拒绝复位(不动他人提交)"
    res = _git(repo_root, "reset", "--keep", pre_merge_commit)
    after = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    if res.returncode != 0 or after != pre_merge_commit:
        return False, f"git reset --keep {pre_merge_commit[:12]} 失败(HEAD={after[:12]}): {res.stderr.strip()}"
    return True, f"main 已复位到合并前 {pre_merge_commit[:12]}(撤销合并提交 {merge_commit[:12]}; 卡分支保留)"


def _run_async_main(args: argparse.Namespace) -> int:
    """async 后台子进程: 跑 execute_check → 写回归事件记录。写不了 = 出声(日志)且非 0 退出, 不吞。"""
    result: dict[str, Any] = {
        "task_id": args.task_id, "mode": args.mode, "execution": "async", "timeout_seconds": args.timeout_seconds,
        "policy_source": args.policy_source, "runall_path": args.runall_path, "merge_commit": args.merge_commit,
    }
    result.update(execute_check(governance_root=Path(args.governance_root), repo_root=Path(args.repo_root),
                                merge_commit=args.merge_commit, runall_path=args.runall_path, timeout_seconds=args.timeout_seconds))
    result["action"] = "recorded"
    result["summary"] = summary_line(result)
    print(result["summary"], flush=True)
    try:
        path = write_event_record(Path(args.governance_root), args.task_id, args.actor, result)
    except (OSError, ValueError) as exc:
        print(f"[post_merge_regression] 回归事件记录写入失败: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"[post_merge_regression] 回归事件记录: {path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIPOS-F118: finalize 合并后回归检查(async 后台入口; 由 finalize 拉起)")
    sub = parser.add_subparsers(dest="command", required=True)
    run_async = sub.add_parser("run-async", help="后台跑合并后回归并写回归事件记录")
    for flag in ("--governance-root", "--repo-root", "--task-id", "--actor", "--merge-commit", "--runall-path", "--mode", "--policy-source"):
        run_async.add_argument(flag, required=True)
    run_async.add_argument("--timeout-seconds", type=int, required=True)
    args = parser.parse_args(argv)
    return _run_async_main(args)


if __name__ == "__main__":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    sys.exit(main())
