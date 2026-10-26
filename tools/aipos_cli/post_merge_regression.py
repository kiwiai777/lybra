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

    # AIPOS-F234 件③: 时限按近 N 次全量实测耗时推导(声明值为下限; timeout_recent_runs=0 = 不推导, 行为不变)
    # AIPOS-F237 件②: 推导经 full_run_timeout(loop 等交回上限 / 开工稿时限句 / set-test-contract 预演同读)
    derived = full_run_timeout(governance_root, repo_root)
    contract, pmr = derived["contract"], derived["post_merge_regression"]
    return {
        # 合并前读入(AIPOS-F120: 合并改写代码源, 合并后不再读声明), 供 sync 时限 ≥ loop 步超时的提示用
        "loop_step_timeout_seconds": loop_step_timeout_seconds(),
        "runall_path": contract["runall_path"],
        "mode": pmr["mode"],
        "execution": pmr["execution"],
        "timeout_seconds": derived["timeout_seconds"],
        "declared_timeout_seconds": int(pmr["timeout_seconds"]),
        "timeout_basis": derived["basis"],
        "serialize": bool(pmr["serialize"]),
        "policy_source": pmr["source"],
        "runall_source": contract["source"],
    }


def full_run_timeout(governance_root: Path, repo_root: str | Path | None = None, *, project: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F237 件②: 本项目(卡仓)同一测试清单「全量跑一次」的时限——唯一推导(合并后回归 resolve_policy / loop 等交回上限
    loop_driver.full_runs_wait_floor / 开工稿时限句 subagent_kickoff / set-test-contract 预演同读)。= 唯一读取口 project_test_contract
    (repo_root = 卡仓, project = 将写入的 project.json 内容, 给出即校验这份)+ derive_timeout(近 N 次实测推导, 未开 = 声明值)。
    返回 {runall_path, timeout_seconds, declared, median, samples, basis, derived(是否开了推导), source, post_merge_regression, contract}。
    声明形坏 = ValueError(TEST_CONTRACT_INVALID) / SchemaLoadError 原样抛。只读。"""
    from tools.aipos_cli.workspace_config import project_test_contract

    contract = project_test_contract(governance_root, repo_root, project=project)
    pmr = contract["post_merge_regression"]
    derived = derive_timeout(Path(governance_root), runall_path=contract["runall_path"], declared=int(pmr["timeout_seconds"]),
                             recent_runs=int(pmr["timeout_recent_runs"]), factor=float(pmr["timeout_factor"]))
    return {"runall_path": contract["runall_path"], "timeout_seconds": int(derived["timeout_seconds"]), "declared": int(pmr["timeout_seconds"]),
            "median": derived["median"], "samples": derived["samples"], "basis": derived["basis"],
            "derived": int(pmr["timeout_recent_runs"]) > 0, "source": str(pmr["source"]), "post_merge_regression": pmr, "contract": contract}


#: AIPOS-F234 件③: 时限推导样本的口径——合并后一跑超时(error 以此开头)= 该一跑耗时只是下限
_MERGED_TIMEOUT_PREFIX = "合并后一跑: "
_BASELINE_TIMEOUT_PREFIX = "合并前基线一跑"


def _duration_samples(governance_root: Path, runall_path: str | None) -> list[dict[str, Any]]:
    """本治理根已记录的单次全量耗时样本(新→旧; 与基线查找同一扫描 _recorded_regressions): [{seconds, lower_bound, ref, which}]。
    合并后一跑: merged_complete = 实测; 超时终止 = 下限(≥)。合并前一跑(无记录基线时另跑): 跑完 = 实测; 该一跑超时 = 下限。"""
    records, _unreadable = _recorded_regressions(Path(governance_root))
    out: list[dict[str, Any]] = []
    for path, pmr in records:
        if runall_path and str(pmr.get("runall_path") or "") != str(runall_path):
            continue
        ref = _record_ref(Path(governance_root), path)
        error = str(pmr.get("error") or "")
        merged = pmr.get("merged_seconds")
        if isinstance(merged, (int, float)) and not isinstance(merged, bool) and merged > 0:
            if pmr.get("merged_complete") is True:
                out.append({"seconds": float(merged), "lower_bound": False, "ref": ref, "which": "merged"})
            elif pmr.get("status") == "timeout" and error.startswith(_MERGED_TIMEOUT_PREFIX):
                out.append({"seconds": float(merged), "lower_bound": True, "ref": ref, "which": "merged"})
        base = pmr.get("baseline_seconds")
        if isinstance(base, (int, float)) and not isinstance(base, bool) and base > 0:
            timed_out = pmr.get("status") == "timeout" and error.startswith(_BASELINE_TIMEOUT_PREFIX)
            out.append({"seconds": float(base), "lower_bound": timed_out, "ref": ref, "which": "baseline"})
    return out


def derive_timeout(governance_root: Path, *, runall_path: str | None, declared: int, recent_runs: int, factor: float) -> dict[str, Any]:
    """AIPOS-F234 件③: 合并后回归时限推导(唯一实现; resolve_policy 合并前读入)。recent_runs = 0 → 声明值; 否则
    max(declared, ceil(factor × 近 recent_runs 个样本(_duration_samples)耗时中位数)), 无样本 = 声明值。
    返回 {timeout_seconds, declared, median, samples, basis(人读一行, 进记录 timeout_basis)}。只读。"""
    import math
    import statistics

    if recent_runs <= 0:
        return {"timeout_seconds": declared, "declared": declared, "median": None, "samples": [],
                "basis": f"时限 {declared}s = 声明 timeout_seconds(未开推导: timeout_recent_runs=0)"}
    samples = _duration_samples(governance_root, runall_path)[:recent_runs]
    if not samples:
        return {"timeout_seconds": declared, "declared": declared, "median": None, "samples": [],
                "basis": f"时限 {declared}s = 声明 timeout_seconds(无全量实测样本可推导)"}
    median = float(statistics.median(x["seconds"] for x in samples))
    derived = max(int(declared), int(math.ceil(float(factor) * median)))
    shown = ", ".join(("≥" if x["lower_bound"] else "") + f"{x['seconds']:g}s" for x in samples)
    basis = (f"时限 {derived}s = max(声明下限 {declared}s, ceil({factor:g} × 近 {len(samples)} 次全量实测中位数 {median:g}s)); "
             f"样本(新→旧, ≥ = 超时终止的下限) [{shown}]")
    return {"timeout_seconds": derived, "declared": declared, "median": median, "samples": samples, "basis": basis}


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True, text=True)


def merge_parent(repo_root: Path, merge_commit: str) -> str:
    """合并前 main = 合并提交第一父。取不到 = ValueError(调用方按 error 处置)。"""
    res = _git(repo_root, "rev-parse", "--verify", f"{merge_commit}^1")
    if res.returncode != 0 or not res.stdout.strip():
        raise ValueError(f"取不到合并提交 {merge_commit[:12]} 的第一父: {res.stderr.strip()}")
    return res.stdout.strip()


def _popen_group(cmd: list[str], *, cwd: Path, env: dict[str, str] | None, stdout: Any, stdin: Any = subprocess.DEVNULL) -> subprocess.Popen:
    """本检查唯一的子进程拉起口(测试清单一跑 / async 后台检查): 独立会话(进程组号 = pid), 超时或收尾按组清理, stdin 缺省关闭
    (AIPOS-F166 件③: async 后台检查 stdin = 管道, 作「进行中记录已落」的握手)。不是 harness 拉起层(agent 拉起唯一在 harness_launch)。
    AIPOS-F179 件①: lybra run-long 的 supervisor(长验证命令脱离运行)同经此口拉起, 不另设第二处独立会话拉起。"""
    return subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=stdout, stderr=subprocess.STDOUT, stdin=stdin,
                            text=True, start_new_session=True)


#: 超时终止: 先 SIGTERM 整组(run-all 执行器据此按本轮标记清掉其独立会话里的测试进程, runall_discovery.run 的 SIGTERM 处理 → reap_marked), 宽限后 SIGKILL。
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


def concurrent_full_runs() -> int | None:
    """AIPOS-F234 件③: 本机此刻在跑的全量测试清单数——run-all 执行器给每轮测试子进程打的本轮标记(runall_discovery.LEAK_MARK_PREFIX
    开头的环境变量)按不同标记计数(只读本用户进程 /proc/<pid>/environ, 不读命令行)。无 /proc = None(无从计数, 记录照写 None)。
    两个测试文件之间的瞬间无子进程会少计, 是下限。"""
    from tools.aipos_cli.runall_discovery import LEAK_MARK_PREFIX

    proc = Path("/proc")
    if not proc.is_dir():
        return None
    prefix, uid, marks = LEAK_MARK_PREFIX.encode(), os.getuid(), set()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            environ = (entry / "environ").read_bytes()
        except OSError:  # 进程已退出 / 不可读 = 不计(只计读得到的本用户进程)
            continue
        marks.update(item.split(b"=", 1)[0] for item in environ.split(b"\0") if item.startswith(prefix))
    return len(marks)


def run_suite(repo_root: Path, commit: str, runall_rel: str, timeout: float, log_dir: Path | None = None) -> dict[str, Any]:
    """在 commit 的临时 detached worktree 里跑测试清单一次。返回 {commit, status: ran|timeout|error, exit_code, failures, log, seconds, error}。
    AIPOS-F166 件③: log_dir = 日志落点(产品调用一律给治理根运行记录旁 regression_log_dir; 机器重启不丢); 未给(直接调用)= 系统临时目录。"""
    from tools.aipos_cli.runall_discovery import _reap_group, failure_set

    started = time.monotonic()
    result: dict[str, Any] = {"commit": commit, "status": "error", "exit_code": None, "failures": None, "log": None, "error": None}
    if timeout <= 0:
        result.update(status="timeout", error="检查总时限已用尽, 未开跑", seconds=0)
        return result
    tmp = Path(tempfile.mkdtemp(prefix="lybra-pmr-"))
    worktree = tmp / "wt"
    log_path = (Path(log_dir) / f"{EVENT_TYPE}_suite_{commit[:12]}_{os.getpid()}_{int(time.time())}.log" if log_dir is not None
                else Path(tempfile.gettempdir()) / f"lybra-post-merge-regression-{commit[:12]}-{os.getpid()}-{int(time.time())}.log")
    added = _git(repo_root, "worktree", "add", "--detach", str(worktree), commit)
    try:
        if added.returncode != 0:
            result["error"] = f"git worktree add {commit[:12]} 失败: {added.stderr.strip()}"
            return result
        if not (worktree / runall_rel).is_file():
            result["error"] = f"测试清单 {runall_rel} 在 {commit[:12]} 上不存在"
            return result
        result["concurrent_at_start"] = concurrent_full_runs()  # AIPOS-F234 件③: 开跑时本机已在跑的全量数(不含本次)
        proc = _popen_group(["bash", runall_rel], cwd=worktree, env=None, stdout=subprocess.PIPE)
        try:
            output, _ = proc.communicate(timeout=timeout)
            result["status"], result["exit_code"] = "ran", proc.returncode
        except subprocess.TimeoutExpired:
            concurrent = concurrent_full_runs()  # AIPOS-F234 件③: 超时当刻本机并发全量数(含本次), 写进结论
            result["concurrent_full_runs"] = concurrent
            output = _terminate(proc)
            result["status"], result["error"] = "timeout", (
                f"测试清单在 {timeout:.0f}s 内未跑完(当时本机并发全量 {concurrent if concurrent is not None else '?'} 个, 含本次), "
                "已终止(SIGTERM 后整组 SIGKILL)")
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


def _record_ref(governance_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(governance_root))
    except ValueError:
        return str(path)


def lookup_recorded_baseline(governance_root: Path, commit: str) -> dict[str, Any]:
    """「上次合并时记录的基线」: merge_commit == commit 且合并后集合完整(merged_complete)的最新一份记录。
    无 = {"failures": None, "unreadable": [...], "incomplete": [...]}; 有 = {"failures", "ref", "previous", "unreadable", "incomplete"}。
    AIPOS-F149 件③: incomplete = merge_commit == commit 却不可作基线的记录 [{ref, status, error}](超时 / 出错 / 异步未落结果 /
    合并后集合缺), 供审计基线查询说明「为何须自跑」; finalize 侧不读此键(行为不变)。"""
    records, unreadable = _recorded_regressions(governance_root)
    incomplete: list[dict[str, Any]] = []
    for path, pmr in records:
        if str(pmr.get("merge_commit") or "") != commit:
            continue
        if pmr.get("merged_complete") is True and isinstance(pmr.get("merged_failures"), list):
            return {"failures": [str(x) for x in pmr["merged_failures"]], "ref": _record_ref(governance_root, path), "previous": pmr,
                    "unreadable": unreadable, "incomplete": incomplete}
        incomplete.append({"ref": _record_ref(governance_root, path), "status": str(pmr.get("status") or "(无 status)"),
                           "error": str(pmr.get("error") or "")})
    return {"failures": None, "unreadable": unreadable, "incomplete": incomplete}


#: AIPOS-F149 件③ + AIPOS-F188 件①: main 侧基线查询的三种结论(输出与 --json 的 status 值; 声明 verbs.schema lybra_regression_baseline.states)。
AUDIT_BASELINE_RECORDED = "recorded"
AUDIT_BASELINE_PENDING = "pending"
AUDIT_BASELINE_SELF_RUN = "self_run_required"
BASELINE_VERB = "lybra_regression_baseline"
BASELINE_STATES = (AUDIT_BASELINE_RECORDED, AUDIT_BASELINE_PENDING, AUDIT_BASELINE_SELF_RUN)


def baseline_declaration() -> dict[str, Any]:
    """verbs.schema lybra_regression_baseline(states / wait / exit_codes)唯一读取口。缺 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(BASELINE_VERB)
    wait = decl.get("wait") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not isinstance(decl.get("states"), dict) or set(decl["states"]) != set(BASELINE_STATES)
            or not isinstance(wait, dict) or not isinstance(wait.get("hint"), str)
            or any(not isinstance(wait.get(k), (int, float)) or isinstance(wait.get(k), bool) or wait[k] <= 0
                   for k in ("max_seconds", "interval_seconds"))
            or not isinstance(decl.get("exit_codes"), dict)):
        raise SchemaLoadError(f"verbs.schema.json verbs.{BASELINE_VERB}(states {list(BASELINE_STATES)} / wait.max_seconds / "
                              "wait.interval_seconds / wait.hint / exit_codes)未声明齐")
    return decl


def baseline_state_labels() -> dict[str, str]:
    """AIPOS-F212 件④: 三态结论标签 verbs.schema lybra_regression_baseline.state_labels 唯一读取口(人读输出 / 开工稿 / 章程三态条 /
    审计卡取证锚点同读)。缺 / 非串 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    labels = baseline_declaration().get("state_labels")
    if not isinstance(labels, dict) or any(not isinstance(labels.get(k), str) or not labels[k] for k in BASELINE_STATES):
        raise SchemaLoadError(f"verbs.schema.json verbs.{BASELINE_VERB}.state_labels({list(BASELINE_STATES)} 非空串)未声明齐")
    return {k: str(labels[k]) for k in BASELINE_STATES}


def baseline_cli_help() -> str:
    """AIPOS-F215 件③: argparse `regression baseline` 的 help(verbs.schema lybra_regression_baseline.cli_help, 标签 = state_labels)。
    缺 / 非串 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    template = baseline_declaration().get("cli_help")
    if not isinstance(template, str) or not template:
        raise SchemaLoadError(f"verbs.schema.json verbs.{BASELINE_VERB}.cli_help 未声明")
    return template.format(**baseline_state_labels())


def baseline_state_rules(role_class: str) -> str | None:
    """AIPOS-F212 件④: 章程「按输出三态处理」条(verbs.schema lybra_regression_baseline.charter_state_rules.<角色类>, 标签 = state_labels)。
    章程渲染键 regression_baseline_states 的唯一产出; 角色类无条目 = None(不入键)。形不合 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError

    rules = baseline_declaration().get("charter_state_rules")
    if not isinstance(rules, dict):
        raise SchemaLoadError(f"verbs.schema.json verbs.{BASELINE_VERB}.charter_state_rules 未声明")
    template = rules.get(role_class)
    if template is None:
        return None
    if not isinstance(template, str) or not template:
        raise SchemaLoadError(f"verbs.schema.json verbs.{BASELINE_VERB}.charter_state_rules.{role_class} 须为非空串")
    return template.format(**baseline_state_labels())


def _parse_iso(text: Any) -> Any:
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _pending_view(entry: dict[str, Any], now: Any = None) -> dict[str, Any]:
    """在跑的回归(regression_states state=running 一条)→ pending 段: 卡 / 记录 / pid / 主机 / 日志 / 开始时刻 / 时限 / 预计最迟完成 / 已运行秒数。
    开始时刻缺(F166 前 finalization 记录 async_started 无 started_at)= 取 finalized_at; 都缺 = None(不猜)。"""
    from datetime import timedelta

    from tools.aipos_cli.clock import iso_z, utc_now

    started = _parse_iso(entry.get("started_at")) if entry.get("started_at") else None
    timeout = entry.get("timeout_seconds")
    timeout = int(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0 else None
    now = now or utc_now()
    return {"task_id": entry.get("task_id"), "record": entry.get("record"), "pid": entry.get("pid"), "host": entry.get("host"),
            "log": entry.get("log"), "started_at": iso_z(started) if started else None, "timeout_seconds": timeout,
            "deadline": iso_z(started + timedelta(seconds=timeout)) if started and timeout else None,
            "elapsed_seconds": int((now - started).total_seconds()) if started else None}


def audit_baseline(governance_root: Path, commit: str) -> dict[str, Any]:
    """AIPOS-F149 件③(gap #89) + AIPOS-F188 件①: main 侧基线按 sha 取已记录的合并后回归失败集合(只读; 记录基线判据 =
    lookup_recorded_baseline, 与 finalize 取「记录基线」同一实现; 在跑判定 = regression_states, 与 loop status / regression run 同一实现)。

    有完整记录 = {status: recorded, failures, failure_lines, failure_nodes, ref, execution, merged_seconds}——main 侧不必自跑;
    该 sha 的回归仍在跑(进程在)= {status: pending, pending: {开始时刻 / 时限 / 预计最迟完成 …}, wait_command}——先做卡, 交回前再查或 --wait;
    无记录 / 记录为超时·出错·异步未落结果·集合不完整 / 进程已不在 / 他机判不出 / 记录读不出 = {status: self_run_required, reasons: [...]}。
    被审 / 本卡分支一侧无论哪种结论都必须自跑(本函数不涉该侧, 独立性不降)。"""
    found = lookup_recorded_baseline(governance_root, commit)
    out: dict[str, Any] = {"commit": commit, "governance_root": str(governance_root), "unreadable": list(found["unreadable"])}
    if found["failures"] is not None:
        prev = found["previous"]
        failures = list(found["failures"])
        out.update(status=AUDIT_BASELINE_RECORDED, ref=found["ref"], failures=failures,
                   failure_lines=[f for f in failures if "::" not in f], failure_nodes=[f for f in failures if "::" in f],
                   execution=prev.get("execution"), merged_seconds=prev.get("merged_seconds"), runall_path=prev.get("runall_path"))
        return out
    states = regression_states(Path(governance_root), sha=commit)
    running = next((e for e in states if e.get("state") == "running"), None)
    if running is not None:
        out.update(status=AUDIT_BASELINE_PENDING, pending=_pending_view(running),
                   wait_command=str(baseline_declaration()["wait"]["hint"]).format(
                       command=audit_baseline_command(governance_root), sha=commit))
        return out
    reasons = [f"记录 {item['ref']} 不可作基线: status={item['status']}" + (f"({item['error']})" if item["error"] else "")
               + "——超时 / 出错 / 异步未落结果 / 合并后集合不完整的记录一律不作基线" for item in found["incomplete"]]
    # AIPOS-F188 件①: 有进行中记录却不在跑 = 写明为什么不能等(进程已不在 / 他机判不出), 补跑命令同 loop status
    reasons += [f"该 sha 的合并后回归不在跑({e['state']}): {e.get('detail')}" + (f"; 补跑: {e['rerun']}" if e["state"] == "incomplete" else "")
                for e in states if e.get("state") in ("incomplete", "unprobeable")]
    if not reasons:
        reasons.append(f"无 merge_commit = {commit} 的合并后回归记录(该提交不是经 finalize 合并产生, 或合并后回归关闭/跳过)")
    reasons += list(found["unreadable"])
    out.update(status=AUDIT_BASELINE_SELF_RUN, reasons=reasons)
    return out


def resolve_audit_sha(governance_root: Path, sha: str | None, repo_root: str | Path | None = None) -> tuple[str, str]:
    """AIPOS-F149 件③: 审计基线查询的 sha。返回 (完整 sha, 来源说明)。
    sha 给了完整 40 位 = 原样; 给了短 sha / 引用 = 在产品仓解析; 不给 = 产品仓基线分支(transitions N5.branch_integration.base_branch,
    唯一读取口 next_resolver.card_base_branch)当前 HEAD。产品仓 = --repo-root, 缺省 = project.json repos.default / code_repo
    (唯一读取口 workspace_config.project_repos)。解析不出 = ValueError(含出口)。"""
    import re

    text = str(sha or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", text):
        return text, "--sha"
    given = repo_root is not None and str(repo_root).strip()
    repo = _product_repo(governance_root, repo_root)  # AIPOS-F166: 产品仓解析唯一实现(补跑同读)
    repo_note = f"--repo-root {repo}" if given else f"project.json 声明的产品仓 {repo}"
    if not text:
        from tools.aipos_cli.next_resolver import card_base_branch

        text = card_base_branch()
        what = f"{repo_note} 的 {text} 当前 HEAD"
    else:
        what = f"{repo_note} 中解析 {text}"
    res = _git(repo, "rev-parse", "--verify", "--quiet", f"{text}^{{commit}}")
    full = res.stdout.strip()
    if res.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", full):
        raise ValueError(f"{what}: 解析不出提交({res.stderr.strip() or '无此引用'}); 出口: 给完整 40 位 --sha 或正确的 --repo-root")
    return full, what


def audit_baseline_command(governance_root: str | Path, repo_root: str | Path | None = None) -> str:
    """AIPOS-F149 件③: 审计基线查询命令的唯一渲染(章程占位 regression_baseline_command 与审计卡取证锚点共用, 禁第二份字面量)。
    治理根 / 产品仓按声明给出(调用方经 project.json 解析), 不写死主机与路径。"""
    import shlex

    from tools.aipos_cli.workspace_config import lybra_command_prefix

    parts = ["regression", "baseline"]
    if repo_root:
        parts += ["--repo-root", str(repo_root)]
    return " ".join([lybra_command_prefix(governance_root), *(shlex.quote(part) for part in parts)])  # AIPOS-F215 件③: 前缀唯一渲染


def render_audit_baseline(result: dict[str, Any]) -> str:
    """audit_baseline 结果的人读输出(CLI 唯一出口)。"""
    commit = str(result.get("commit") or "")
    labels = baseline_state_labels()  # AIPOS-F212 件④: 三态标签唯一声明
    lines = [f"合并后回归记录基线: sha {commit}(来源 {result.get('sha_source') or '--sha'}; 治理根 {result.get('governance_root')})"]
    if result.get("status") == AUDIT_BASELINE_RECORDED:
        failures = list(result.get("failures") or [])
        lines.append(f"结论: {labels[AUDIT_BASELINE_RECORDED]}(记录 {result.get('ref')}; execution={result.get('execution')}, "
                     f"合并后一跑 {result.get('merged_seconds')}s, 清单 {result.get('runall_path')})")
        lines.append(f"失败集合 {len(failures)} 条(口径 runall_discovery.failure_set: `✗` 行的文件/原文 + 失败文件段内 FAILED/ERROR 节点):")
        lines.append(f"  ✗ 行条目 {len(result.get('failure_lines') or [])} 条:")
        lines += [f"    {item}" for item in result.get("failure_lines") or []] or ["    (无)"]
        lines.append(f"  失败节点 {len(result.get('failure_nodes') or [])} 条:")
        lines += [f"    {item}" for item in result.get("failure_nodes") or []] or ["    (无)"]
        lines.append("下一步: main 侧以本失败集合为基线, 不再自跑; 被审分支一侧仍须自跑一次全量, 按同一口径取失败集合逐条比对。")
    elif result.get("status") == AUDIT_BASELINE_PENDING:
        pending = result.get("pending") or {}
        lines.append(f"结论: {labels[AUDIT_BASELINE_PENDING]}(该 sha 的记录基线尚未落定, 可等)")
        lines.append(f"  进行中: 卡 {pending.get('task_id')} 记录 {pending.get('record')}(pid {pending.get('pid')} @ {pending.get('host') or '本机'}, "
                     f"日志 {pending.get('log')})")
        lines.append(f"  开始 {pending.get('started_at') or '(记录未写开始时刻)'}, 时限 {pending.get('timeout_seconds') or '(未记)'}s, "
                     f"预计最迟 {pending.get('deadline') or '(不可推算)'} 完成"
                     + (f"(已运行 {pending['elapsed_seconds']}s)" if isinstance(pending.get("elapsed_seconds"), int) else ""))
        lines.append(f"下一步: 先做本卡 / 被审分支一侧(该侧仍须自跑一次全量); 交回前再查一次本命令, 或 `{result.get('wait_command')}` "
                     f"有界等待——到点仍未完成 = {labels[AUDIT_BASELINE_SELF_RUN]} main 侧。")
    else:
        lines.append(f"结论: {labels[AUDIT_BASELINE_SELF_RUN]} main 侧(无可复用的记录基线)")
        lines += [f"  原因: {reason}" for reason in result.get("reasons") or []]
        lines.append("下一步: main 侧按审计章程建 main 基线副本自跑一次; 被审分支一侧同样自跑一次。")
    waited = result.get("waited")
    if waited:
        lines.append(f"(--wait {waited.get('requested_seconds'):g}s: {waited.get('outcome')}, 实等 {waited.get('waited_seconds')}s)")
    for note in result.get("unreadable") or []:
        if result.get("status") == AUDIT_BASELINE_RECORDED:
            lines.append(f"  注: {note}")
    return "\n".join(lines)


def _preview(items: list[str]) -> str:
    head = ", ".join(items[:_LIST_PREVIEW])
    return head + (f" …(共 {len(items)} 条)" if len(items) > _LIST_PREVIEW else "")


def execute_check(*, governance_root: Path, repo_root: Path, merge_commit: str, runall_path: str,
                  timeout_seconds: int, log_dir: Path | None = None) -> dict[str, Any]:
    """同步执行检查(finalize sync 与 async 子进程同一函数; AIPOS-F166 件③ `lybra regression run --confirm` 补跑同一函数)。
    返回记录形字段(不含策略处置)。log_dir = 测试清单日志落点(regression_log_dir)。"""
    deadline = time.monotonic() + timeout_seconds
    out: dict[str, Any] = {"merge_commit": merge_commit, "runall_path": runall_path, "timeout_seconds": timeout_seconds,
                           "merged_complete": False, "notes": []}
    try:
        pre = merge_parent(repo_root, merge_commit)
    except ValueError as exc:
        out.update(status="error", error=str(exc))
        return out
    out["pre_merge_commit"] = pre
    merged = run_suite(repo_root, merge_commit, runall_path, deadline - time.monotonic(), log_dir)
    out["merged_log"], out["merged_seconds"] = merged.get("log"), merged.get("seconds")
    for key in ("concurrent_at_start", "concurrent_full_runs"):  # AIPOS-F234 件③
        if key in merged:
            out[key] = merged[key]
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
        before = run_suite(repo_root, pre, runall_path, deadline - time.monotonic(), log_dir)
        out["baseline_log"], out["baseline_seconds"] = before.get("log"), before.get("seconds")
        if "concurrent_full_runs" in before:  # AIPOS-F234 件③
            out["concurrent_full_runs"] = before["concurrent_full_runs"]
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
    """本检查结果的一行(行首 MARKER)。AIPOS-F234 件③: 排过队(queue.behind 非空)= 行尾附排队说明。"""
    line = _summary_line(result)
    queue = result.get("queue") if isinstance(result.get("queue"), dict) else {}
    if queue.get("behind") and result.get("status") in CONCLUSION_STATUSES:
        line += f"(排队: {queue.get('note')})"
    return line


def _summary_line(result: dict[str, Any]) -> str:
    status, mode = result.get("status"), result.get("mode")
    if status == "skipped":
        return f"{MARKER} 跳过({result.get('reason')})"
    if status == "off":
        return f"{MARKER} 已关闭(项目声明 mode=off, 来源 {result.get('policy_source')}); 本次合并未做组合回归"
    if status == "async_started":
        record = result.get("event_record") or f"events/{result.get('task_id')}/{EVENT_TYPE}_*.md"
        return (f"{MARKER} 异步执行中(pid {result.get('async_pid')}, 日志 {result.get('async_log')}); "
                f"进行中记录已落, 结论落同一回归事件记录 {record}")
    if status == "running":  # AIPOS-F166 件③: 回归事件记录的进行中态(启动即落)
        return (f"{MARKER} 进行中(pid {result.get('async_pid')} @ {result.get('host')}, 日志 {result.get('async_log') or result.get('log_dir')}; "
                f"起于 {result.get('started_at')})")
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


def new_event_record_id(governance_root: Path, task_id: str) -> str:
    """回归事件记录 id(落 records/events/<卡>/<id>.md)。同秒已有同名记录(进行中记录与补跑同秒)= 追加序号, 绝不覆盖既有记录。"""
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.record_writer import record_dir

    base = f"{EVENT_TYPE}_{task_id}_{file_slug('compact', iso_z())}"
    directory = record_dir(Path(governance_root), "events") / task_id
    for n in range(1, 1000):
        record_id = base if n == 1 else f"{base}_{n}"
        if not (directory / f"{record_id}.md").exists():
            return record_id
    raise ValueError(f"回归事件记录名 {base}_* 已用尽(同秒 999 份)")


def write_event_record(governance_root: Path, task_id: str, actor: str, result: dict[str, Any], record_id: str | None = None) -> str:
    """回归事件记录(block 撤销合并 / async 后台完成 / AIPOS-F166 件③ 进行中与补跑): 统一写入器 write_records_atomic, 记录类 events。
    record_id 给出 = 写同一份(进行中 → 结论同一记录更新); 缺省 = 新 id。返回相对路径。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.schema_constants import RecordType

    timestamp = iso_z()
    record_id = record_id or new_event_record_id(governance_root, task_id)
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


def regression_log_dir(governance_root: Path, task_id: str) -> tuple[Path, str]:
    """AIPOS-F166 件③(gap #153: /tmp 日志随机器重启丢失): 合并后回归日志落点 = 治理根运行记录旁(<loop_runs_root>/<卡ID>/, 唯一读取口
    loop_run_record.loop_runs_dir), 与 loop 运行日志同一条 git 排除规则(loop_run_record.exclude_run_logs, .log 不入库)。
    返回 (目录, 排除登记失败的说明 | "")——登记失败不阻断检查(日志照落, 说明进结果 notes 出声)。目录建不起来 = OSError / ValueError 原样抛。"""
    from tools.aipos_cli.loop_run_record import exclude_run_logs, loop_runs_dir

    directory = loop_runs_dir(Path(governance_root), task_id)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        exclude_run_logs(directory)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return directory, f"回归日志 git 排除登记失败({type(exc).__name__}: {exc}); 日志在 {directory}, 勿提交"
    return directory, ""


def _process_identity(pid: int) -> dict[str, Any]:
    """进行中记录的进程身份: pid + 主机 + 进程启动时刻指纹(唯一实现 loop_run_record.process_fingerprint; 探活同读 process_alive)。"""
    import socket

    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.loop_run_record import LoopRunRecordError, process_fingerprint

    out: dict[str, Any] = {"async_pid": int(pid), "host": socket.gethostname(), "started_at": iso_z()}
    try:
        out["process_fingerprint"] = process_fingerprint(int(pid))
    except LoopRunRecordError as exc:  # 指纹读不出(无权限 / /proc 形不合): 记下原因, 判活时按「不在」处理(宁报未完成, 不报在跑)
        out.update(process_fingerprint=None, fingerprint_error=str(exc)[:240])
    return out


def _spawn_async(*, governance_root: Path, repo_root: Path, task_id: str, actor: str, merge_commit: str, policy: dict[str, Any],
                 trigger: str = "finalize") -> dict[str, Any]:
    """拉起独立会话后台子进程跑同一 execute_check(本模块 run-async 入口), 立即返回。包根 = 本模块所在目录的实路径(不经
    .deploy/current 符号链接)。子进程是新进程, 从包根一次性加载一份代码(editable 安装时即合并后的代码), 不与 finalize 进程
    混用新旧(AIPOS-F120 导入闸管的是 finalize 本进程)。
    AIPOS-F166 件③: 日志落治理根运行记录旁(regression_log_dir, 不落 /tmp); 子进程起后即落「进行中」回归事件记录(合并提交 / pid / 主机 /
    进程启动时刻指纹 / 日志), 子进程先等本进程写完进行中记录(stdin 握手: 读到 EOF 才开跑)再执行, 结论落定时覆写同一记录——不会出现
    结论先落、进行中记录后写把结论盖掉。返回 {async_pid, async_log, event_record, notes}。"""
    from tools.aipos_cli.clock import file_slug, iso_z

    package_root = Path(__file__).resolve().parents[2]
    log_dir, note = regression_log_dir(governance_root, task_id)
    log_path = log_dir / f"{EVENT_TYPE}_{merge_commit[:12]}_{file_slug('compact', iso_z())}_{os.getpid()}.log"
    record_id = new_event_record_id(governance_root, task_id)
    cmd = [sys.executable, "-m", "tools.aipos_cli.post_merge_regression", "run-async",
           "--governance-root", str(governance_root), "--repo-root", str(repo_root), "--task-id", task_id, "--actor", actor,
           "--merge-commit", merge_commit, "--runall-path", str(policy["runall_path"]),
           "--timeout-seconds", str(policy["timeout_seconds"]), "--mode", str(policy["mode"]),
           "--policy-source", str(policy["policy_source"]), "--event-record-id", record_id, "--async-log", str(log_path),
           "--trigger", trigger]
    if policy.get("serialize"):  # AIPOS-F234 件③: 排队串行(子进程开跑前等在前的回归结束)
        cmd.append("--serialize")
    for key, flag in (("declared_timeout_seconds", "--declared-timeout-seconds"), ("timeout_basis", "--timeout-basis")):
        if policy.get(key) is not None:
            cmd += [flag, str(policy[key])]
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    with open(log_path, "w", encoding="utf-8") as log:
        proc = _popen_group(cmd, cwd=package_root, env=env, stdout=log, stdin=subprocess.PIPE)
    ASYNC_CHILDREN.append(proc)
    running = {"task_id": task_id, "mode": policy["mode"], "execution": "async", "timeout_seconds": policy["timeout_seconds"],
               "policy_source": policy["policy_source"], "runall_path": policy["runall_path"], "merge_commit": merge_commit,
               "status": "running", "action": "none", "trigger": trigger, "async_log": str(log_path), **_process_identity(proc.pid),
               **_policy_timing(policy)}
    running["summary"] = summary_line(running)
    try:
        event_path = write_event_record(governance_root, task_id, actor, running, record_id=record_id)
    finally:
        try:
            proc.stdin.close()  # 握手: 子进程读到 EOF 才开跑(进行中记录已落 / 写失败也放行, 结论照落同一 id)
        except OSError as exc:  # 子进程已退出(管道断): 无可放行, 出声进 notes
            note = f"{note}; " if note else ""
            note += f"后台子进程握手管道已断({exc})"
    return {"async_pid": proc.pid, "async_log": str(log_path), "event_record": event_path, "notes": [note] if note else []}


def _policy_timing(policy: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F234 件③: 记录里的时限来历(声明值 / 推导依据; resolve_policy 给出时才有)。"""
    return {k: policy[k] for k in ("declared_timeout_seconds", "timeout_basis") if policy.get(k) is not None}


def check_after_merge(*, governance_root: Path, repo_root: Path, task_id: str, actor: str, merge_commit: str,
                      policy: dict[str, Any]) -> dict[str, Any]:
    """finalize 合并后入口(merge --no-ff 之后、push/deploy 之前)。返回记录形 dict, 含 status / action / summary。

    action: none(通过/关闭/跳过/异步已拉起) | warned(mode=warn 且 新增失败/超时/出错) | blocked(mode=block 且同上; 调用方撤销合并)。"""
    from tools.schema_loader import SchemaLoadError

    result: dict[str, Any] = {
        "task_id": task_id,
        "mode": policy["mode"],
        "execution": policy["execution"],
        "timeout_seconds": policy["timeout_seconds"],
        "policy_source": policy["policy_source"],
        "runall_path": policy["runall_path"],
        "merge_commit": merge_commit,
        "action": "none",
        **_policy_timing(policy),
    }
    if not policy["runall_path"]:
        result.update(status="skipped", reason=f"项目未声明 test_contract.runall_path(来源 {policy['runall_source']})")
    elif policy["mode"] == "off":
        result.update(status="off")
    elif policy["execution"] == "async":
        try:
            spawned = _spawn_async(governance_root=governance_root, repo_root=repo_root, task_id=task_id, actor=actor,
                                   merge_commit=merge_commit, policy=policy)
        except (OSError, ValueError, RuntimeError, SchemaLoadError) as exc:  # AIPOS-F166 件③: 日志落点 / 拉起 / 进行中记录不可得 = 检查未完成(不当作通过, 按 warn 出声)
            result.update(status="error", error=f"异步回归未能拉起({type(exc).__name__}: {exc})", action="warned")
        else:
            notes = spawned.pop("notes")
            result.update(status="async_started", **spawned)
            if notes:
                result["notes"] = notes
    else:
        try:
            log_dir, note = regression_log_dir(governance_root, task_id)
        except (OSError, ValueError, SchemaLoadError) as exc:
            log_dir, note = None, f"回归日志落点不可得({type(exc).__name__}: {exc}), 日志落系统临时目录"
        _queued(result, governance_root, task_id, serialize=bool(policy.get("serialize")), own_pid=os.getpid(),
                default_timeout=int(policy["timeout_seconds"]))  # AIPOS-F234 件③: 声明 serialize = 排队串行
        result.update(execute_check(governance_root=governance_root, repo_root=repo_root, merge_commit=merge_commit,
                                    runall_path=policy["runall_path"], timeout_seconds=policy["timeout_seconds"], log_dir=log_dir))
        if note:
            result.setdefault("notes", []).append(note)
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


def record_conclusion(governance_root: Path, task_id: str, actor: str, result: dict[str, Any], record_id: str | None
                      ) -> tuple[int, list[str]]:
    """结论落回归事件记录(record_id 给出 = 覆写同一份进行中记录)+ 补落账(governance_commit.reland_after_write, N6 同一提交口)。
    async 后台与 `lybra regression run --confirm` 前台同一实现。返回 (退出码 0/1, 输出行)。写不了 / 补落账失败 = 出声 + 1, 不吞。"""
    lines: list[str] = []
    try:
        path = write_event_record(Path(governance_root), task_id, actor, result, record_id=record_id)
    except (OSError, ValueError, RuntimeError) as exc:
        return 1, [f"[post_merge_regression] 回归事件记录写入失败: {exc}"]
    lines.append(f"[post_merge_regression] 回归事件记录: {path}")
    # AIPOS-F147 件①(gap #104): 记录写完后补一次落账——复用 N6 同一提交口(governance_commit.reland_after_write);
    # 卡未结案 = 由其 N6 带上。补落账失败 = 出声 + 非 0 退出(记录在本卡落账判据范围内, lint/loop 会再点名, 不丢)
    from tools.aipos_cli.governance_commit import reland_after_write

    try:
        reland = reland_after_write(Path(governance_root), task_id, actor, what="合并后回归事件记录")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        lines.append(f"[post_merge_regression] 补落账失败({type(exc).__name__}: {exc}); 记录在本卡落账范围内, 由 N6 / state lint 再点名")
        return 1, lines
    lines.append(f"[post_merge_regression] {reland['line']}")
    from tools.schema_constants import Verdict

    return (0 if reland["verdict"] in (None, Verdict.PASS) else 1), lines


def _run_async_main(args: argparse.Namespace) -> int:
    """async 后台子进程: 跑 execute_check → 写回归事件记录。写不了 = 出声(日志)且非 0 退出, 不吞。
    AIPOS-F166 件③: --event-record-id 给出 = 结论覆写拉起方已落的同一份进行中记录(进程身份 / 日志落点保留); 测试清单日志落同一目录。"""
    log = str(getattr(args, "async_log", None) or "")
    result: dict[str, Any] = {
        "task_id": args.task_id, "mode": args.mode, "execution": "async", "timeout_seconds": args.timeout_seconds,
        "policy_source": args.policy_source, "runall_path": args.runall_path, "merge_commit": args.merge_commit,
    }
    if getattr(args, "trigger", None):
        result["trigger"] = args.trigger
    if getattr(args, "declared_timeout_seconds", None) is not None:  # AIPOS-F234 件③: 时限来历
        result["declared_timeout_seconds"] = args.declared_timeout_seconds
    if getattr(args, "timeout_basis", None):
        result["timeout_basis"] = args.timeout_basis
    if log:
        result.update({**_process_identity(os.getpid()), "async_log": log})
    _queued(result, Path(args.governance_root), args.task_id, serialize=bool(getattr(args, "serialize", False)), own_pid=os.getpid(),
            default_timeout=int(args.timeout_seconds))  # AIPOS-F234 件③: 排队串行(时限只计开跑后)
    result.update(execute_check(governance_root=Path(args.governance_root), repo_root=Path(args.repo_root),
                                merge_commit=args.merge_commit, runall_path=args.runall_path, timeout_seconds=args.timeout_seconds,
                                log_dir=Path(log).parent if log else None))
    result["action"] = "recorded"
    result["summary"] = summary_line(result)
    print(result["summary"], flush=True)
    rc, lines = record_conclusion(Path(args.governance_root), args.task_id, args.actor, result, getattr(args, "event_record_id", None))
    for i, line in enumerate(lines):
        print(line, file=sys.stderr if rc and i == len(lines) - 1 else sys.stdout, flush=True)
    return rc


# ---------------------------------------------------------------------------
# AIPOS-F166 件③(gap #153): 合并后回归状态(唯一判定)+ 补跑 `lybra regression run --sha <提交>`(预演缺省, --confirm 执行)。
# 声明 = verbs.schema lybra_regression_run(states / rerun_hint / exit_codes); 记录格式 = F149 同一(post_merge_regression 字段)。
# ---------------------------------------------------------------------------

REGRESSION_VERB = "lybra_regression_run"
#: 有结论的状态(检查跑完了, 无论通过与否); 其中 COMPLETE 的结论完整可作基线, 不再补跑。
CONCLUSION_STATUSES = ("pass", "new_failures", "timeout", "error")
COMPLETE_STATUSES = ("pass", "new_failures")
_STATE_KEYS = ("running", "incomplete", "concluded", "unprobeable", "none")


def regression_declaration() -> dict[str, Any]:
    """verbs.schema lybra_regression_run(states / rerun_hint / exit_codes)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(REGRESSION_VERB)
    if (not isinstance(decl, dict) or not isinstance(decl.get("states"), dict) or set(decl["states"]) != set(_STATE_KEYS)
            or not isinstance(decl.get("rerun_hint"), str) or not isinstance(decl.get("exit_codes"), dict)):
        raise SchemaLoadError(f"verbs.schema.json verbs.{REGRESSION_VERB}(states {list(_STATE_KEYS)} / rerun_hint / exit_codes)未声明齐")
    return decl


def rerun_command(governance_root: Path, sha: str) -> str:
    """补跑命令(预演形; 执行加 --confirm --actor <实例>)的唯一渲染(声明 rerun_hint)。"""
    return str(regression_declaration()["rerun_hint"]).format(sha=sha, governance_root=governance_root)


def _record_task(path: Path, pmr: dict[str, Any]) -> str:
    return str(pmr.get("task_id") or path.parent.name)


def regression_states(governance_root: Path, *, task_id: str | None = None, sha: str | None = None,
                      local_host: str | None = None) -> list[dict[str, Any]]:
    """合并后回归状态(唯一判定; loop status / brief / next / regression run 同读): 按合并提交归并 finalization 记录与回归事件记录
    (_recorded_regressions, 与基线查找同一扫描), 每个提交一条 {merge_commit, task_id, state, status, detail, record, pid, host, log, rerun}。
    state(声明 lybra_regression_run.states): concluded(任一记录有结论)/ running(进行中记录的进程在)/ unprobeable(进行中记录写于他机)/
    incomplete(进行中 / 已拉起而无结论, 进程已不在)。进程判活 = loop_run_record.process_alive(pid + 启动时刻指纹); F166 前的
    finalization 记录 async_started 无指纹无主机 = 仅按 pid 判(注明)。只读。"""
    import socket

    from tools.aipos_cli.loop_run_record import LEGACY_FINGERPRINT_NOTE, LoopRunRecordError, process_alive, process_fingerprint

    root = Path(governance_root)
    local_host = local_host or socket.gethostname()
    records, _unreadable = _recorded_regressions(root)
    by_commit: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path, pmr in records:
        commit = str(pmr.get("merge_commit") or "")
        if not commit or (sha and commit != sha) or (task_id and _record_task(path, pmr) != task_id):
            continue
        by_commit.setdefault(commit, []).append((path, pmr))
    out: list[dict[str, Any]] = []
    for commit, items in by_commit.items():
        task = _record_task(*items[0])
        entry: dict[str, Any] = {"merge_commit": commit, "task_id": task, "rerun": rerun_command(root, commit)}
        concluded = next(((p, r) for p, r in items if r.get("status") in CONCLUSION_STATUSES), None)
        if concluded is not None:
            path, pmr = concluded
            entry.update(state="concluded", status=pmr.get("status"), record=_record_ref(root, path),
                         complete=bool(pmr.get("merged_complete")) and pmr.get("status") in COMPLETE_STATUSES,
                         detail=str(pmr.get("summary") or summary_line(pmr)))
            out.append(entry)
            continue
        running = next(((p, r) for p, r in items if r.get("status") == "running"), None)
        started = next(((p, r) for p, r in items if r.get("status") == "async_started"), None)
        if running is None and started is None:
            continue  # 只有 off / skipped 等无检查的记录: 不归本判定管
        path, pmr = running or started
        pid = pmr.get("async_pid")
        log = pmr.get("async_log") or pmr.get("log_dir")
        entry.update(record=_record_ref(root, path), pid=pid, log=log, host=pmr.get("host"),
                     # AIPOS-F188 件①: pending 基线给开始时刻与时限(F166 前 finalization 记录无 started_at = 退取 finalized_at)
                     started_at=pmr.get("started_at") or _finalized_at(path), timeout_seconds=pmr.get("timeout_seconds"))
        probe_error = ""
        try:
            if running is not None:
                alive, how = process_alive(pid, pmr.get("process_fingerprint")), "pid + 进程启动时刻指纹"
            else:  # F166 前的 finalization 记录: 无指纹无主机, 仅按 pid(pid 被复用会误判为在跑, 如实注明)
                alive, how = isinstance(pid, int) and process_fingerprint(pid) is not None, f"{LEGACY_FINGERPRINT_NOTE}(F166 前记录无进程指纹)"
        except LoopRunRecordError as exc:
            alive, how, probe_error = False, "", str(exc)
        if running is not None and str(pmr.get("host") or "") != local_host:
            entry.update(state="unprobeable", detail=f"进行中记录写于主机 {pmr.get('host')}, 本机 {local_host} 无从探活: 在治理根所在机执行")
        elif probe_error:
            entry.update(state="unprobeable", detail=f"pid {pid} 探活失败({probe_error}), 判不出是否仍在跑")
        else:
            what = "进行中记录" if running is not None else "finalization 记录 async_started"
            if alive:
                entry.update(state="running", detail=f"{what}: pid {pid} 仍在(判活: {how}); 日志 {log}")
            else:
                entry.update(state="incomplete",
                             detail=(f"合并后回归未完成: {what}(pid {pid}, 日志 {log})无结论且进程已不在(判活: {how}; "
                                     "机器重启 / 进程被杀)"))
        out.append(entry)
    return out


#: AIPOS-F234 件③: 排队等待上限的宽限(在前者 started_at + 其时限 + 本宽限 之后仍在 = 不再等, 照跑并注明)与观察间隔
_QUEUE_GRACE_SECONDS = 60
_QUEUE_POLL_SECONDS = 5.0


def regressions_ahead(governance_root: Path, *, own_pid: int | None, own_key: tuple[str, str]) -> list[dict[str, Any]]:
    """AIPOS-F234 件③: 排在本检查之前、仍在跑的合并后回归(判定 = regression_states 同一判定的 running: 进行中记录的进程在, 本机)。
    own_key = (本检查 started_at, 本检查记录); (started_at, record) 先于 own_key 者在前; own_pid 的那条是自己(排除)。只读。"""
    rows = []
    for entry in regression_states(Path(governance_root)):
        if entry.get("state") != "running" or (own_pid is not None and entry.get("pid") == own_pid):
            continue
        if (str(entry.get("started_at") or ""), str(entry.get("record") or "")) < own_key:
            rows.append(entry)
    return rows


def own_queue_key(governance_root: Path, own_pid: int) -> tuple[str, str]:
    """本检查的排队序键: 本进程的进行中记录(regression_states 中 pid = own_pid 者)的 (started_at, record); 无进行中记录(finalize 同步检查)=
    (此刻, 最大记录名)——此刻已在跑的都在前。"""
    from tools.aipos_cli.clock import iso_z

    for entry in regression_states(Path(governance_root)):
        if entry.get("pid") == own_pid and entry.get("state") == "running":
            return str(entry.get("started_at") or ""), str(entry.get("record") or "")
    return iso_z(), "\uffff"


def wait_for_turn(governance_root: Path, task_id: str, *, own_pid: int | None, own_key: tuple[str, str], default_timeout: int) -> dict[str, Any]:
    """AIPOS-F234 件③(声明 config.schema post_merge_regression.serialize): 本检查开跑前排队等在前的合并后回归结束(经唯一等待原语
    agent_watch_fs.run_fs_watch, 经 loop_run_record._wait_gone; 观察本卡运行记录目录, stop_when = 已无在前者)。上限 = 各在前者 started_at + 其记录时限(缺 = default_timeout)
    + 宽限 的最晚时刻; 到点仍在 = 不再等、照跑, note 注明。返回 {behind: [{task_id, merge_commit, pid, started_at, timeout_seconds}],
    waited_seconds, still_ahead, note}(无在前者 = behind 空, 不等)。"""
    from datetime import timedelta

    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.loop_run_record import _wait_gone

    root = Path(governance_root)
    ahead = regressions_ahead(root, own_pid=own_pid, own_key=own_key)
    out: dict[str, Any] = {"behind": [{k: e.get(k) for k in ("task_id", "merge_commit", "pid", "started_at", "timeout_seconds")} for e in ahead],
                           "waited_seconds": 0.0, "still_ahead": 0, "note": ""}
    if not ahead:
        return out
    now = utc_now()
    deadlines = []
    for entry in ahead:
        started = _parse_iso(entry.get("started_at")) or now
        limit = entry.get("timeout_seconds") if isinstance(entry.get("timeout_seconds"), int) else default_timeout
        deadlines.append(started + timedelta(seconds=int(limit) + _QUEUE_GRACE_SECONDS))
    remaining = (max(deadlines) - now).total_seconds()
    began = time.monotonic()
    if remaining > 0:  # 唯一等待原语(loop_run_record._wait_gone → agent_watch_fs.run_fs_watch), 观察本卡运行记录目录(回归日志同落点)
        _wait_gone(root, task_id, lambda: bool(regressions_ahead(root, own_pid=own_pid, own_key=own_key)), remaining,
                   interval=_QUEUE_POLL_SECONDS)
    out["waited_seconds"] = round(time.monotonic() - began, 1)
    left = regressions_ahead(root, own_pid=own_pid, own_key=own_key)
    out["still_ahead"] = len(left)
    if left:
        out["note"] = (f"排队到上限仍有 {len(left)} 个在前的合并后回归未结束(" + ", ".join(f"{e.get('task_id')} pid {e.get('pid')}" for e in left)
                       + "), 不再等, 照跑")
    else:
        out["note"] = (f"排队 {out['waited_seconds']:g}s, 在前 " + ", ".join(f"{e.get('task_id')}({str(e.get('merge_commit') or '')[:12]})" for e in ahead)
                       + " 已结束后开跑")
    return out


def _queued(result: dict[str, Any], governance_root: Path, task_id: str, *, serialize: bool, own_pid: int, default_timeout: int) -> None:
    """serialize 声明为真 = 排队(wait_for_turn)并把排队结果写进 result["queue"]; 否则不动(行为不变)。"""
    if not serialize:
        return
    result["queue"] = wait_for_turn(governance_root, task_id, own_pid=own_pid, own_key=own_queue_key(governance_root, own_pid),
                                    default_timeout=default_timeout)


def _finalized_at(path: Path) -> Any:
    """finalization 记录的 finalized_at(F166 前 async_started 段无 started_at 时作开始时刻); 读不出 / 无 = None(不猜)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    if not path.name.startswith("finalization_"):
        return None
    try:
        meta, _body = require_frontmatter(path)
    except (FrontmatterReadError, OSError):
        return None  # 该记录已在 _recorded_regressions 读过; 此处只取展示用时刻, 读不出 = 不显示开始时刻(pending 段注明未写)
    return meta.get("finalized_at")


def unfinished_regressions(governance_root: Path, *, task_id: str | None = None) -> list[dict[str, Any]]:
    """合并后回归未完成(state=incomplete)的提交; 每条另带 line(loop status / brief / next 同一行文案)。"""
    rows = [e for e in regression_states(governance_root, task_id=task_id) if e["state"] == "incomplete"]
    for row in rows:
        row["line"] = (f"合并后回归未完成: {row['task_id']} 合并提交 {row['merge_commit'][:12]} — {row['detail']}; "
                       f"补跑: {row['rerun']}(预演; 执行加 --confirm --actor <驱动方实例>)")
    return rows


def _product_repo(governance_root: Path, repo_root: str | Path | None) -> Path:
    """补跑所在产品仓: --repo-root, 缺省 = project.json repos.default / code_repo(唯一读取口 workspace_config.project_repos,
    与 resolve_audit_sha 同一口径)。声明缺 = ValueError(含出口)。"""
    if repo_root is not None and str(repo_root).strip():
        return Path(repo_root).expanduser().resolve()
    from tools.aipos_cli.workspace_config import project_repos

    repos = project_repos(governance_root)
    declared = repos["items"].get(repos["default"]) if repos["declared"] else repos["code_repo"]
    if declared is None:
        raise ValueError(f"治理根 {governance_root} 的 project.json 未声明产品仓(repos / code_repo); 出口: 给 --repo-root <产品仓> 或完整 40 位 --sha")
    return Path(declared)


def rerun(governance_root: Path, sha: str, *, repo_root: str | Path | None = None, task_id: str | None = None,
          actor: str | None = None, confirm: bool = False, background: bool = False) -> dict[str, Any]:
    """`lybra regression run`(AIPOS-F166 件③): 预演(缺省, 只读)/ --confirm 前台补跑 / --confirm --background 后台(同 finalize async 入口)。
    执行 = execute_check(F118 唯一实现), 结论 = 同格式回归事件记录(F149), 落账 = record_conclusion。返回 {exit_code, lines, ...}。"""
    from tools.schema_loader import SchemaLoadError

    decl = regression_declaration()
    codes = {k: int(v["code"]) for k, v in decl["exit_codes"].items()}
    root = Path(governance_root)
    out: dict[str, Any] = {"governance_root": str(root), "sha": sha, "dry_run": not confirm, "lines": []}

    def refuse(why: str) -> dict[str, Any]:
        out.update(exit_code=codes["refused"], refused=why)
        out["lines"].append(f"拒: {why}")
        return out

    if background and not confirm:
        return refuse("--background 须与 --confirm 同用(预演不起进程)")
    try:
        repo = _product_repo(root, repo_root)
        commit, what = resolve_audit_sha(root, sha, repo_root)
    except (ValueError, OSError, SchemaLoadError) as exc:
        return refuse(str(exc))
    out["commit"] = commit
    states = regression_states(root, sha=commit)
    entry = states[0] if states else {"merge_commit": commit, "state": "none", "detail": decl["states"]["none"]}
    out["state"] = entry
    task = task_id or entry.get("task_id")
    out["lines"].append(f"合并后回归状态: 提交 {commit}(来源 {what}) — {entry['state']}: {entry.get('detail')}"
                        + (f"(记录 {entry['record']})" if entry.get("record") else ""))
    if not task:
        return refuse(f"找不到提交 {commit[:12]} 所属卡(无该提交的 finalization / 回归记录); 出口: 加 --task-id <卡ID>")
    out["task_id"] = task
    if entry["state"] == "running":
        return refuse(f"该提交的回归仍在跑(pid {entry.get('pid')}), 不重复补跑; 看结论: lybra regression baseline --sha {commit}")
    if entry["state"] == "unprobeable":
        return refuse(f"{entry['detail']}(本机判不出是否仍在跑, 不补跑)")
    if entry["state"] == "concluded" and entry.get("complete"):
        return refuse(f"已有完整结论 {entry.get('status')}(记录 {entry.get('record')}), 不重跑; 基线: lybra regression baseline --sha {commit}")
    try:
        policy = resolve_policy(root, repo)
    except (ValueError, SchemaLoadError) as exc:
        return refuse(f"测试约定声明不可用: {exc}")
    if not policy["runall_path"]:
        return refuse(f"项目未声明 test_contract.runall_path(来源 {policy['runall_source']}), 无清单可跑")
    from tools.aipos_cli.loop_run_record import loop_runs_dir

    try:
        log_dir = loop_runs_dir(root, task)  # 预演只算落点, 不建目录(零写入)
    except (OSError, ValueError, SchemaLoadError) as exc:
        return refuse(f"日志落点不可得: {exc}")
    plan = (f"补跑: 在产品仓 {repo} 的 {commit[:12]} 上跑 {policy['runall_path']}(总时限 {policy['timeout_seconds']}s, 合并前基线优先取记录"
            + (f"; {policy['timeout_basis']}" if policy.get("timeout_basis") else "") + ("; 并发时排队串行" if policy.get("serialize") else "") + "), "
            f"结论落 records/events/{task}/{EVENT_TYPE}_*.md(同格式回归事件记录), 日志落 {log_dir}/")
    out["lines"].append(plan)
    if not confirm:
        out["lines"].append(f"预演(未执行、未写任何文件)。执行: 原命令加 --confirm --actor <驱动方实例>(前台; 加 --background = 后台)")
        out["exit_code"] = codes["ok"]
        return out
    if not str(actor or "").strip():
        return refuse("--confirm 须给 --actor <驱动方实例>(事件记录与补落账的执行身份)")
    if background:
        try:
            spawned = _spawn_async(governance_root=root, repo_root=repo, task_id=task, actor=str(actor), merge_commit=commit,
                                   policy=policy, trigger="rerun")
        except (OSError, ValueError, RuntimeError, SchemaLoadError) as exc:
            out.update(exit_code=codes["failed"])
            out["lines"].append(f"后台补跑未能拉起({type(exc).__name__}: {exc})")
            return out
        out.update(exit_code=codes["ok"], spawned=spawned)
        out["lines"].append(f"{MARKER} 后台补跑已拉起(pid {spawned['async_pid']}, 日志 {spawned['async_log']}); "
                            f"进行中记录 {spawned['event_record']}, 结论落定时同一记录更新")
        out["lines"] += [f"  ↳ 提示: {n}" for n in spawned["notes"]]
        return out
    try:
        record_id = new_event_record_id(root, task)
        log_dir, note = regression_log_dir(root, task)
    except (OSError, ValueError, SchemaLoadError) as exc:
        out.update(exit_code=codes["failed"])
        out["lines"].append(f"日志落点 / 记录名不可得({type(exc).__name__}: {exc}), 未开跑")
        return out
    base = {"task_id": task, "mode": policy["mode"], "execution": "sync", "timeout_seconds": policy["timeout_seconds"],
            "policy_source": policy["policy_source"], "runall_path": policy["runall_path"], "merge_commit": commit, "trigger": "rerun",
            **_policy_timing(policy)}
    running = {**base, "status": "running", "action": "none", **_process_identity(os.getpid()), "log_dir": str(log_dir)}
    running["summary"] = summary_line(running)
    try:
        started = write_event_record(root, task, str(actor), running, record_id=record_id)
    except (OSError, ValueError, RuntimeError) as exc:
        out.update(exit_code=codes["failed"])
        out["lines"].append(f"进行中记录写不进({exc}), 未开跑")
        return out
    out["lines"].append(f"进行中记录: {started}")
    queue: dict[str, Any] = {}
    _queued(queue, root, task, serialize=bool(policy.get("serialize")), own_pid=os.getpid(), default_timeout=int(policy["timeout_seconds"]))
    if queue.get("queue", {}).get("behind"):
        out["lines"].append(f"排队: {queue['queue']['note']}")
    result = {**running, **queue, **execute_check(governance_root=root, repo_root=repo, merge_commit=commit, runall_path=policy["runall_path"],
                                         timeout_seconds=policy["timeout_seconds"], log_dir=log_dir)}
    if note:
        result.setdefault("notes", []).append(note)
    result["action"] = "recorded"
    result["summary"] = summary_line(result)
    out["lines"].append(result["summary"])
    rc, lines = record_conclusion(root, task, str(actor), result, record_id)
    out["lines"] += lines
    out.update(exit_code=codes["ok"] if rc == 0 else codes["failed"], result=result)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIPOS-F118: finalize 合并后回归检查(async 后台入口; 由 finalize 拉起)")
    sub = parser.add_subparsers(dest="command", required=True)
    run_async = sub.add_parser("run-async", help="后台跑合并后回归并写回归事件记录")
    for flag in ("--governance-root", "--repo-root", "--task-id", "--actor", "--merge-commit", "--runall-path", "--mode", "--policy-source"):
        run_async.add_argument(flag, required=True)
    run_async.add_argument("--timeout-seconds", type=int, required=True)
    # AIPOS-F166 件③: 进行中记录 id(拉起方已落)/ 日志落点 / 触发方(finalize | rerun); 给了 id = 先等 stdin EOF(握手)再开跑
    run_async.add_argument("--event-record-id", default=None)
    run_async.add_argument("--async-log", default=None)
    run_async.add_argument("--trigger", default=None)
    # AIPOS-F234 件③: 排队串行 / 时限来历(拉起方按 resolve_policy 传入)
    run_async.add_argument("--serialize", action="store_true", default=False)
    run_async.add_argument("--declared-timeout-seconds", type=int, default=None)
    run_async.add_argument("--timeout-basis", default=None)
    args = parser.parse_args(argv)
    if args.event_record_id:
        sys.stdin.read()  # 拉起方写完进行中记录即关管道(EOF); 拉起方退出同样 EOF
    return _run_async_main(args)


if __name__ == "__main__":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    sys.exit(main())
