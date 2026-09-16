"""AIPOS-F73D — `lybra loop`: 顾问侧驱动器(Owner 信封授权下, 有界循环推进一张卡到 completed)。

一句话: 把顾问逐步敲 `next --run` 变成一条命令——产物落盘就自动推进, 直到 completed;
agent 步只等产物、永不唤醒 agent; 四出口、有界、fail-closed。

单一实现(禁第二推导核/第二哨兵/第二 claim 路径, 禁直调 board_adapter):
- 推导: next_resolver.derive_next_step(AIPOS-F71 唯一推导核)
- 执行: next_resolver.execute_derived_action(AIPOS-F73 `next --run` 同一执行体)
- 等待: agent_watch_fs.run_fs_watch(AIPOS-268/284 唯一哨兵, `--expect` + 就绪谓词=推导核可推导)
- 信封: autonomy_policy(owner_autonomy_policy 族, `lybra envelope mint` 申领)
- 退出码/参数/允许动词集合/等待产物: schema/verbs.schema.json verbs.lybra_loop 一处声明, 本模块只读。

每轮: 推导 → 若账务命令: 先过 aipos_cli argparse 解析(失败=exit 4 禁执行) → 执行 → 重推导;
若 agent 步(执行体在干活 / 已派审等审计体): 经 watch 有界等待产物(产物就绪判据=推导核), 落盘即回到推导;
closure 记录存在 → exit 0。--max-steps 与 --max-wait 为硬上限; 任一出口非零带原文; token 永不上屏。
"""
from __future__ import annotations

import contextlib
import io
import json
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, TextIO

from tools.aipos_cli.next_resolver import (
    REPO_ROOT,
    _action_type_for_command,
    _driver_actor,
    _find_task_in_queue,
    _read_frontmatter,
    _read_task_records,
    _transition_node,
    derive_next_step,
    execute_derived_action,
)

LOOP_VERB = "lybra_loop"
DRIVER_ROLE = "advisor"  # 驱动方角色(roles.schema advisor 持账务动词; 信封 agent_or_role 可写角色名或实例名)


# ---------------------------------------------------------------------------
# 声明读取(verbs.schema lybra_loop 一处)
# ---------------------------------------------------------------------------

def load_loop_contract(repo_root: Path | None = None) -> dict[str, Any]:
    """读 verbs.schema.json verbs.lybra_loop(退出码/参数默认/允许动词/等待产物)。缺声明 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    contract = (load_schema("verbs", repo_root or REPO_ROOT).get("verbs") or {}).get(LOOP_VERB)
    if not isinstance(contract, dict):
        raise SchemaLoadError(f"verbs.schema.json verbs.{LOOP_VERB} 未声明")
    for key in ("exit_codes", "parameters", "envelope"):
        if key not in contract:
            raise SchemaLoadError(f"verbs.schema.json verbs.{LOOP_VERB}.{key} 未声明")
    return contract


def exit_code_for(contract: dict[str, Any], outcome: str) -> int:
    """按出口名取退出码(唯一来源=声明)。"""
    entry = contract["exit_codes"].get(outcome)
    if not isinstance(entry, dict) or "code" not in entry:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.schema.json verbs.{LOOP_VERB}.exit_codes.{outcome} 未声明")
    return int(entry["code"])


def parameter_default(contract: dict[str, Any], name: str) -> Any:
    return ((contract.get("parameters") or {}).get("properties") or {}).get(name, {}).get("default")


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class LoopStep:
    index: int
    kind: str  # execute | wait | done
    node: str | None
    state: str | None
    card: str
    action_type: str = ""
    command: str = ""
    ok: bool = True
    exit_code: int = 0
    output: str = ""
    artifacts: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "node": self.node,
            "state": self.state,
            "card": self.card,
            "action_type": self.action_type,
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "output": self.output,
            "artifacts": list(self.artifacts),
            "message": self.message,
        }


@dataclass
class LoopResult:
    task_id: str
    outcome: str
    exit_code: int
    message: str
    envelope: str | None = None
    steps: list[LoopStep] = field(default_factory=list)
    missing_records: list[str] = field(default_factory=list)
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "message": self.message,
            "envelope": self.envelope,
            "steps": [s.to_dict() for s in self.steps],
            "missing_records": list(self.missing_records),
            "suggested_action": self.suggested_action,
        }


# ---------------------------------------------------------------------------
# 件③ 信封校验(复用 owner_autonomy_policy 族)
# ---------------------------------------------------------------------------

def _policy_ids(governance_root: Path) -> list[str]:
    from tools.aipos_cli.autonomy_policy import POLICIES_DIR

    policies_dir = governance_root / POLICIES_DIR
    if not policies_dir.is_dir():
        return []
    return sorted(p.stem for p in policies_dir.glob("*.md") if p.is_file())


def find_envelope(
    governance_root: Path,
    *,
    task_id: str,
    task_fm: dict[str, Any],
    driver_actor: str,
    policy_id: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """在 5_tasks/policies/ 找覆盖本卡与驱动方身份的有效 PreAuthorized 信封。

    判据 = autonomy_policy.match_claim_envelope 严格 AND(有效/时间窗/agent_or_role 覆盖驱动方实例或
    advisor 角色/task_selector 覆盖本卡/额度未尽)。返回 (policy | None, 每个候选的未匹配原因)。
    """
    from tools.aipos_cli.autonomy_policy import count_preauthorized_claims, load_policy, match_claim_envelope

    now = now or datetime.now(timezone.utc)
    candidates = [policy_id] if policy_id else _policy_ids(governance_root)
    reasons: list[str] = []
    if not candidates:
        reasons.append("5_tasks/policies/ 下没有任何信封")
    for pid in candidates:
        policy = load_policy(governance_root, pid)
        if policy is None:
            reasons.append(f"{pid}: 信封文件缺失或格式不合规(owner_autonomy_policy)")
            continue
        released = count_preauthorized_claims(governance_root, pid)
        matched, reason, _code = match_claim_envelope(
            policy=policy,
            task_id=task_id,
            task_mode=str(task_fm.get("task_mode") or ""),
            project=str(task_fm.get("project") or ""),
            agent_instance=driver_actor,
            actor=driver_actor,
            now=now,
            released_count=released,
            claiming_role=DRIVER_ROLE,
        )
        if matched:
            return policy, []
        reasons.append(f"{pid}: {reason}")
    return None, reasons


def mint_hint(*, task_id: str, task_fm: dict[str, Any], driver_actor: str, now: datetime | None = None) -> str:
    """申领出口: 既有 `lybra envelope mint` 命令(顾问 skill 行), 参数按本卡填好可照抄。"""
    now = now or datetime.now(timezone.utc)
    project = str(task_fm.get("project") or "project").strip() or "project"
    task_mode = str(task_fm.get("task_mode") or "code").strip() or "code"
    expires = (now + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return (
        f"lybra envelope mint --policy-id pol_{project}_loop_1 --agent-or-role {driver_actor} "
        f"--max-tasks 20 --task-mode {task_mode} --expires-at {expires} "
        f"--decision-summary \"loop envelope for {task_id}\" --actor owner"
    )


# ---------------------------------------------------------------------------
# 前置一③ parser 夹具: 派生命令执行前先解析
# ---------------------------------------------------------------------------

def check_command_parses(command: str) -> tuple[bool, str]:
    """用 aipos_cli 的 argparse(build_parser)解析派生命令; 失败返回 (False, 原文)。"""
    from tools.aipos_cli.aipos_cli import build_parser

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return False, f"shlex: {exc}"
    if not argv or argv[0] != "lybra":
        return False, f"派生命令须以 `lybra` 开头: {command}"
    parser = build_parser()
    parser.prog = "lybra"  # 报错原文以产品命令名出现, 不带调用方脚本名
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured), contextlib.redirect_stdout(io.StringIO()):
        try:
            parser.parse_args(argv[1:])
        except SystemExit as exc:
            text = captured.getvalue().strip()
            return False, text or f"argparse exit {exc.code}"
    return True, ""


# ---------------------------------------------------------------------------
# 等待产物(读 transitions N2.artifact / N4.audit_report 声明)
# ---------------------------------------------------------------------------

def executor_artifact_patterns(task_id: str) -> list[str]:
    template = str(_transition_node("N2").get("artifact", {}).get("location") or "").strip()
    if not template:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json N2.artifact.location 未声明")
    return [template.replace("{task_id}", task_id)]


def auditor_artifact_patterns(audit_task_id: str) -> list[str]:
    cands = _transition_node("N4").get("audit_report", {}).get("location_candidates") or []
    if not cands:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json N4.audit_report.location_candidates 未声明")
    return [str(c).replace("{audit_task_id}", audit_task_id) for c in cands]


def _watch_args(governance_root: Path, patterns: list[str], *, max_wait: float, interval: float) -> SimpleNamespace:
    """构造 `lybra agent watch --workspace-root <gov> --expect <p>... --timeout <max_wait> --interval <i>` 的参数对象(同一哨兵)。"""
    return SimpleNamespace(
        workspace_root=str(governance_root),
        expect=list(patterns),
        timeout=float(max_wait),
        interval=float(interval),
        events="expect",
        stream=False,
        run_log=None,
        end_pattern=None,
        stall_secs=None,
        health=None,
        pid_file=None,
        proc_pattern=None,
        session_dirs=None,
        worktree_path=None,
        unhealthy_cycles=2,
    )


def _has_closure(governance_root: Path, task_id: str) -> bool:
    return bool(_read_task_records(governance_root, task_id).get("latest_closure"))


# ---------------------------------------------------------------------------
# 件① 主循环
# ---------------------------------------------------------------------------

def run_loop(
    task_id: str,
    governance_root: Path,
    *,
    connection_json: str | None = None,
    actor: str | None = None,
    policy_id: str | None = None,
    max_steps: int | None = None,
    max_wait: float | None = None,
    interval: float | None = None,
    out: TextIO | None = None,
    derive: Callable[[str, Path], dict[str, Any]] = derive_next_step,
    execute: Callable[..., dict[str, Any]] = execute_derived_action,
    watch: Callable[..., int] | None = None,
    now: datetime | None = None,
) -> LoopResult:
    """有界推进一张卡。返回 LoopResult(exit_code 按 verbs.schema lybra_loop.exit_codes)。

    derive/execute/watch 可注入(靶场用), 缺省 = 产品唯一实现。
    """
    out = out or sys.stdout
    governance_root = Path(governance_root)
    contract = load_loop_contract()
    max_steps = int(max_steps if max_steps is not None else parameter_default(contract, "max_steps"))
    max_wait = float(max_wait if max_wait is not None else parameter_default(contract, "max_wait"))
    interval = float(interval if interval is not None else parameter_default(contract, "interval"))
    if max_steps < 1 or max_wait <= 0 or interval <= 0:
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"),
                          "--max-steps ≥1, --max-wait >0, --interval >0 为硬约束")
    allowed_verbs = set(contract["envelope"].get("allowed_verbs") or [])
    if watch is None:
        from tools.aipos_cli.agent_watch_fs import run_fs_watch as watch  # noqa: F811 — 唯一哨兵

    def say(line: str) -> None:
        print(line, file=out, flush=True)

    # 卡存在性
    task_path, _queue_dir = _find_task_in_queue(governance_root, task_id)
    if not task_path:
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"),
                          f"queue 目录中找不到任务卡 {task_id}", missing_records=[f"任务卡 {task_id}"])
    task_fm = _read_frontmatter(task_path)
    driver_actor = actor or _driver_actor(governance_root, fallback=DRIVER_ROLE)

    # 件③ 信封(启动前校验; 无信封 exit 5 带申领出口, 禁裸跑)
    policy, reasons = find_envelope(
        governance_root, task_id=task_id, task_fm=task_fm, driver_actor=driver_actor, policy_id=policy_id, now=now
    )
    if policy is None:
        hint = mint_hint(task_id=task_id, task_fm=task_fm, driver_actor=driver_actor, now=now)
        msg = "无有效 autonomy 信封, 拒绝裸跑。\n" + "\n".join(f"  - {r}" for r in reasons) + f"\n申领出口(Owner 亲自敲):\n  {hint}"
        say(f"lybra loop {task_id}: exit 5 — {msg}")
        return LoopResult(task_id, "no_envelope", exit_code_for(contract, "no_envelope"), msg, suggested_action=hint)
    envelope_id = str(policy.get("policy_id"))
    say(f"lybra loop {task_id}: envelope={envelope_id} driver={driver_actor} max_steps={max_steps} max_wait={max_wait}s")

    result = LoopResult(task_id, "completed", exit_code_for(contract, "completed"), "", envelope=envelope_id)
    steps = result.steps

    for index in range(1, max_steps + 1):
        # 出口 0: closure 记录存在
        if _has_closure(governance_root, task_id):
            steps.append(LoopStep(index, "done", "close", "completed", task_id, message="closure 记录存在"))
            result.message = f"{task_id} 已结案(closure 记录存在), 共 {index} 轮"
            say(f"[{index}] done: {result.message}")
            return result

        derivation = derive(task_id, governance_root)
        target_card = task_id
        wait_patterns: list[str] = []

        if not derivation.get("derivable"):
            action = derivation.get("action") or {}
            node = derivation.get("current_node")
            state = derivation.get("current_state")
            if action.get("type") == "await_artifact" and action.get("card"):
                # N3: 已派审, 审计体在干活 → 审计卡自身可能已可推导(claim 审计卡 / 提交裁决)
                audit_card = str(action["card"])
                audit_derivation = derive(audit_card, governance_root)
                if audit_derivation.get("derivable"):
                    derivation, target_card = audit_derivation, audit_card
                else:
                    target_card = audit_card
                    wait_patterns = auditor_artifact_patterns(audit_card)
            elif node == "claim" and state == "claimed":
                # N1→N2: 执行体在干活 → 等 RETURN.md(骨架不算, 判据=推导核)
                wait_patterns = executor_artifact_patterns(task_id)
            else:
                missing = list(derivation.get("missing_records") or [])
                msg = f"不可推导 @ {node}/{state}: missing={missing}; suggested={derivation.get('suggested_action', '')}"
                steps.append(LoopStep(index, "execute", node, state, task_id, ok=False, message=msg))
                say(f"[{index}] exit 4 — {msg}")
                result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), msg
                result.missing_records, result.suggested_action = missing, str(derivation.get("suggested_action") or "")
                return result

        if wait_patterns:
            step = LoopStep(index, "wait", derivation.get("current_node"), derivation.get("current_state"), target_card,
                            artifacts=wait_patterns)
            say(f"[{index}] wait: {target_card} 产物 {wait_patterns} (≤{max_wait}s, 经 agent watch)")

            def _ready(_matched: list[str], _card: str = target_card) -> bool:
                return bool(derive(_card, governance_root).get("derivable"))

            watch_out = io.StringIO()
            with contextlib.redirect_stdout(watch_out):
                rc = watch(_watch_args(governance_root, wait_patterns, max_wait=max_wait, interval=interval), expect_ready=_ready)
            step.exit_code, step.output = int(rc), watch_out.getvalue().strip()
            if rc != 0:
                step.ok = False
                step.message = f"等待产物超时/停滞(watch exit {rc}): 等的是 {wait_patterns}"
                steps.append(step)
                say(f"[{index}] exit 3 — {step.message}")
                result.outcome, result.exit_code, result.message = "wait_timeout", exit_code_for(contract, "wait_timeout"), step.message
                return result
            step.message = "产物就绪"
            steps.append(step)
            say(f"[{index}] ready: {step.output}")
            continue

        # 账务步: 信封动词集合 → parser 夹具 → 执行(next --run 同一执行体)
        command = str(derivation.get("command") or "").strip()
        node, state = derivation.get("current_node"), derivation.get("current_state")
        action_type = _action_type_for_command(command) if command and not command.startswith("#") else "manual"
        step = LoopStep(index, "execute", node, state, target_card, action_type=action_type, command=command)
        if action_type == "manual" or action_type == "unknown":
            step.ok, step.message = False, f"需人工介入/无法识别动词: {derivation.get('suggested_action', '')} :: {command}"
            steps.append(step)
            say(f"[{index}] exit 4 — {step.message}")
            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), step.message
            result.suggested_action = str(derivation.get("suggested_action") or "")
            return result
        if action_type not in allowed_verbs:
            step.ok, step.message = False, f"信封未授权动词 {action_type}(允许: {sorted(allowed_verbs)})"
            steps.append(step)
            say(f"[{index}] exit 5 — {step.message}")
            result.outcome, result.exit_code, result.message = "no_envelope", exit_code_for(contract, "no_envelope"), step.message
            return result
        parses, parse_error = check_command_parses(command)
        if not parses:
            step.ok, step.message = False, f"派生命令解析失败, 禁执行: {parse_error}"
            steps.append(step)
            say(f"[{index}] exit 4 — {step.message}\n  command: {command}")
            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), step.message
            return result

        say(f"[{index}] run {action_type} @ {node}/{state} ({target_card}): {command}")
        exec_result = execute(derivation, governance_root, connection_json)
        step.ok = bool(exec_result.get("ok"))
        step.exit_code = int(exec_result.get("exit_code") or 0)
        step.output = str(exec_result.get("output") or "")
        step.message = str(exec_result.get("message") or "")
        steps.append(step)
        if not step.ok:
            msg = f"门拒 @ {action_type} ({target_card}) exit {step.exit_code}: {step.message}\n{step.output}".rstrip()
            say(f"[{index}] exit 2 — {msg}")
            result.outcome, result.exit_code, result.message = "gate_rejected", exit_code_for(contract, "gate_rejected"), msg
            return result
        say(f"[{index}] ok: {step.message}")

    msg = f"--max-steps {max_steps} 用尽, 未走到 completed"
    say(f"exit 3 — {msg}")
    result.outcome, result.exit_code, result.message = "wait_timeout", exit_code_for(contract, "wait_timeout"), msg
    return result


def run_loop_cli(args: Any) -> int:
    """CLI 入口(`lybra loop`)。参数缺省读 verbs.schema; 输出人读或 --json; 返回声明的退出码。"""
    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args

    try:
        governance_root = Path(getattr(args, "workspace_root", None) or _find_repo_root_for_args(args))
    except FileNotFoundError as exc:
        print(f"lybra loop: cannot resolve governance root: {exc}", file=sys.stderr)
        return exit_code_for(load_loop_contract(), "not_derivable")
    json_mode = bool(getattr(args, "json", False))
    sink = io.StringIO() if json_mode else sys.stdout
    result = run_loop(
        args.task_id,
        governance_root,
        connection_json=getattr(args, "connection_json", None),
        actor=getattr(args, "actor", None),
        policy_id=getattr(args, "envelope", None),
        max_steps=getattr(args, "max_steps", None),
        max_wait=getattr(args, "max_wait", None),
        interval=getattr(args, "interval", None),
        out=sink,
    )
    if json_mode:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif result.exit_code != 0:
        print(f"lybra loop {result.task_id}: {result.outcome} (exit {result.exit_code})", file=sys.stderr)
    return result.exit_code


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
