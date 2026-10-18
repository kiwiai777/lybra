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

    parts = ["lybra", "--workspace-root", str(governance_root), "regression", "baseline"]
    if repo_root:
        parts += ["--repo-root", str(repo_root)]
    return " ".join(shlex.quote(part) for part in parts)


def render_audit_baseline(result: dict[str, Any]) -> str:
    """audit_baseline 结果的人读输出(CLI 唯一出口)。"""
    commit = str(result.get("commit") or "")
    lines = [f"合并后回归记录基线: sha {commit}(来源 {result.get('sha_source') or '--sha'}; 治理根 {result.get('governance_root')})"]
    if result.get("status") == AUDIT_BASELINE_RECORDED:
        failures = list(result.get("failures") or [])
        lines.append(f"结论: 已记录, 可复用(记录 {result.get('ref')}; execution={result.get('execution')}, "
                     f"合并后一跑 {result.get('merged_seconds')}s, 清单 {result.get('runall_path')})")
        lines.append(f"失败集合 {len(failures)} 条(口径 runall_discovery.failure_set: `✗` 行的文件/原文 + 失败文件段内 FAILED/ERROR 节点):")
        lines.append(f"  ✗ 行条目 {len(result.get('failure_lines') or [])} 条:")
        lines += [f"    {item}" for item in result.get("failure_lines") or []] or ["    (无)"]
        lines.append(f"  失败节点 {len(result.get('failure_nodes') or [])} 条:")
        lines += [f"    {item}" for item in result.get("failure_nodes") or []] or ["    (无)"]
        lines.append("下一步: main 侧以本失败集合为基线, 不再自跑; 被审分支一侧仍须自跑一次全量, 按同一口径取失败集合逐条比对。")
    elif result.get("status") == AUDIT_BASELINE_PENDING:
        pending = result.get("pending") or {}
        lines.append("结论: 合并后回归在跑(该 sha 的记录基线尚未落定, 可等)")
        lines.append(f"  进行中: 卡 {pending.get('task_id')} 记录 {pending.get('record')}(pid {pending.get('pid')} @ {pending.get('host') or '本机'}, "
                     f"日志 {pending.get('log')})")
        lines.append(f"  开始 {pending.get('started_at') or '(记录未写开始时刻)'}, 时限 {pending.get('timeout_seconds') or '(未记)'}s, "
                     f"预计最迟 {pending.get('deadline') or '(不可推算)'} 完成"
                     + (f"(已运行 {pending['elapsed_seconds']}s)" if isinstance(pending.get("elapsed_seconds"), int) else ""))
        lines.append(f"下一步: 先做本卡 / 被审分支一侧(该侧仍须自跑一次全量); 交回前再查一次本命令, 或 `{result.get('wait_command')}` "
                     "有界等待——到点仍未完成 = 须自跑 main 侧。")
    else:
        lines.append("结论: 须自跑 main 侧(无可复用的记录基线)")
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
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    with open(log_path, "w", encoding="utf-8") as log:
        proc = _popen_group(cmd, cwd=package_root, env=env, stdout=log, stdin=subprocess.PIPE)
    ASYNC_CHILDREN.append(proc)
    running = {"task_id": task_id, "mode": policy["mode"], "execution": "async", "timeout_seconds": policy["timeout_seconds"],
               "policy_source": policy["policy_source"], "runall_path": policy["runall_path"], "merge_commit": merge_commit,
               "status": "running", "action": "none", "trigger": trigger, "async_log": str(log_path), **_process_identity(proc.pid)}
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
    if log:
        result.update({**_process_identity(os.getpid()), "async_log": log})
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
    plan = (f"补跑: 在产品仓 {repo} 的 {commit[:12]} 上跑 {policy['runall_path']}(总时限 {policy['timeout_seconds']}s, 合并前基线优先取记录), "
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
            "policy_source": policy["policy_source"], "runall_path": policy["runall_path"], "merge_commit": commit, "trigger": "rerun"}
    running = {**base, "status": "running", "action": "none", **_process_identity(os.getpid()), "log_dir": str(log_dir)}
    running["summary"] = summary_line(running)
    try:
        started = write_event_record(root, task, str(actor), running, record_id=record_id)
    except (OSError, ValueError, RuntimeError) as exc:
        out.update(exit_code=codes["failed"])
        out["lines"].append(f"进行中记录写不进({exc}), 未开跑")
        return out
    out["lines"].append(f"进行中记录: {started}")
    result = {**running, **execute_check(governance_root=root, repo_root=repo, merge_commit=commit, runall_path=policy["runall_path"],
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
    args = parser.parse_args(argv)
    if args.event_record_id:
        sys.stdin.read()  # 拉起方写完进行中记录即关管道(EOF); 拉起方退出同样 EOF
    return _run_async_main(args)


if __name__ == "__main__":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    sys.exit(main())
