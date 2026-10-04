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
closure 记录存在且治理已落账(AIPOS-F94 件①: 未落账先跑推导核派生的 N6 落账步 `lybra governance-commit --task-id`)→ exit 0。--max-steps 与 --max-wait 为硬上限; 任一出口非零带原文; token 永不上屏。

身份(AIPOS-F73E 件①, F73C 定案): 驱动方身份(--actor / 工位声明)只用于信封与 claim token; 派生账务命令(return/verdict/
finalize/close)的 --actor/--agent-instance 一律 = 该卡 claim 记录的认领实例(推导核 _claimer_instance 唯一实现), 无 claim
记录即 exit 4; 提交身份由门记进记录 submitted_by(transitions record_authenticity.submission_identity)。
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
    DRIVER_ACTOR_MISSING,
    REPO_ROOT,
    _action_type_for_command,
    _driver_actor,
    _driver_role_name,
    _find_task_in_queue,
    _read_frontmatter,
    _read_task_records,
    _transition_node,
    derive_next_step,
    driver_scope,
    execute_derived_action,
    auditor_artifact_watch,
    executor_artifact_watch,
)

LOOP_VERB = "lybra_loop"
DRIVER_ROLE = "advisor"
# 推导核标出的硬停动作: 产物已落盘但不合规(F78 件③) / 账务记录缺(F73E 件①: 无 claim 记录)——都不是"agent 还在干活", 禁空等, exit 4 点名
HARD_STOP_ACTIONS = frozenset({"artifact_invalid", "record_missing"})  # 驱动方角色(roles.schema advisor 持账务动词; 信封 agent_or_role 可写角色名或实例名)


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
    shell_command: str = ""  # AIPOS-F90 件②: return/verdict 步 = 产物入口内部执行的薄壳命令(含产品填写的模型字段)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "node": self.node,
            "state": self.state,
            "card": self.card,
            "action_type": self.action_type,
            "command": self.command,
            "shell_command": self.shell_command,
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
    driver_role: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """在 5_tasks/policies/ 找覆盖本卡与驱动方身份的有效 PreAuthorized 信封。

    判据 = autonomy_policy.match_claim_envelope 严格 AND(有效/时间窗/agent_or_role 覆盖驱动方实例或
    advisor 角色/task_selector 覆盖本卡/额度未尽)。返回 (policy | None, 每个候选的未匹配原因)。
    AIPOS-F78B 件③: 驱动方身份集合 = {实例, 工位角色名(如 chris 的 hbj-advisor), 角色类 advisor}——信封 agent_or_role 写其一即覆盖
    (门侧 _match_driver_envelope 同口径)。
    """
    from tools.aipos_cli.autonomy_policy import count_preauthorized_claims, envelope_subject, load_policy, match_claim_envelope

    now = now or datetime.now(timezone.utc)
    candidates = [policy_id] if policy_id else _policy_ids(governance_root)
    roles = [r for r in (str(driver_role or "").strip(), DRIVER_ROLE) if r]
    roles = list(dict.fromkeys(roles))
    reasons: list[str] = []
    # AIPOS-F90 件①: 判定对象(审计卡 = 被审卡), 与门 _match_claim_envelope 同一规则
    subject_id, subject_mode, subject_project = envelope_subject(
        governance_root, task_id=task_id, task_mode=str(task_fm.get("task_mode") or ""), project=str(task_fm.get("project") or ""),
        reviewed_task_id=str(task_fm.get("reviewed_task_id") or ""))
    if not candidates:
        reasons.append("5_tasks/policies/ 下没有任何信封")
    for pid in candidates:
        policy = load_policy(governance_root, pid)
        if policy is None:
            reasons.append(f"{pid}: 信封文件缺失或格式不合规(owner_autonomy_policy)")
            continue
        released = count_preauthorized_claims(governance_root, pid)
        reason = ""
        for role in roles:
            matched, reason, _code = match_claim_envelope(
                policy=policy,
                task_id=subject_id,
                task_mode=subject_mode,
                project=subject_project,
                agent_instance=driver_actor,
                actor=driver_actor,
                now=now,
                released_count=released,
                claiming_role=role,
            )
            if matched:
                return policy, []
        reasons.append(f"{pid}: {reason}")
    return None, reasons


def mint_hint(*, task_id: str, task_fm: dict[str, Any], driver_actor: str, now: datetime | None = None,
              governance_root: Path | None = None) -> str:
    """申领出口: 既有 `lybra envelope mint --confirm` 命令(AIPOS-F92 件①: 经门 owner_decision_record envelope 路径真实落盘;
    Owner 亲自敲, --connection-json 指向持 Owner 凭据的 connection.json), 参数按本卡填好可照抄。"""
    now = now or datetime.now(timezone.utc)
    project = str(task_fm.get("project") or "project").strip() or "project"
    task_mode = str(task_fm.get("task_mode") or "code").strip() or "code"
    expires = (now + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    where = f" --workspace-root {shlex.quote(str(governance_root))}" if governance_root else ""
    return (
        f"lybra envelope mint --confirm --policy-id pol_{project}_loop_1 --agent-or-role {driver_actor} "
        f"--max-tasks 20 --task-mode {task_mode} --expires-at {expires} "
        f"--decision-summary \"loop envelope for {task_id}\" --actor owner{where} --connection-json <Owner 凭据 connection.json>"
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

# AIPOS-F89 件① H9: 原 executor_artifact_patterns(无治理根时回退 N2.artifact.location)与 auditor_artifact_patterns
# (读 N4.audit_report.location_candidates, 写死 task_cards/{audit_task_id}/…)两份第二声明删除; 等待落点一律经
# next_resolver.executor_artifact_watch / auditor_artifact_watch(project.json paths.return_root / verdict_root + artifact_ingest 候选)。


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


# AIPOS-F94 件①: N6 落账步(governance_commit)属 N6 close 节点的收尾(transitions N6: 结案时机含 governance-commit 完成),
# 信封授权按 close 判——信封准 close 即准结案后落账; 其余动作按自身动词判(verbs.schema lybra_loop.envelope.allowed_verbs)。
_ENVELOPE_VERB_FOR_ACTION = {"governance_commit": "close"}


def _landing(governance_root: Path, task_id: str) -> dict[str, Any] | None:
    """落账判据(governance_commit.task_landing 唯一实现); 读失败 = None(交推导核按拒因出口, 不在此吞成已落账)。"""
    import subprocess

    from tools.aipos_cli.governance_commit import task_landing

    try:
        return task_landing(governance_root, task_id)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
        return None


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
    # AIPOS-F78 前置零②: 驱动方身份=显式 --actor → 工位声明(.lybra/role instance)→ connection.json 驱动方 token 实例; 禁占位 advisor
    driver_actor = actor or _driver_actor(governance_root, connection_json=connection_json)
    if not driver_actor:
        msg = f"驱动方身份解析不到: 缺 {DRIVER_ACTOR_MISSING}(或显式 --actor)"
        say(f"lybra loop {task_id}: exit 4 — {msg}")
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"), msg,
                          missing_records=[DRIVER_ACTOR_MISSING])

    # 件③ 信封(启动前校验; 无信封 exit 5 带申领出口, 禁裸跑); 身份集合含工位角色名(chris: hbj-advisor)
    policy, reasons = find_envelope(
        governance_root, task_id=task_id, task_fm=task_fm, driver_actor=driver_actor, policy_id=policy_id, now=now,
        driver_role=_driver_role_name(governance_root, connection_json),
    )
    if policy is None:
        hint = mint_hint(task_id=task_id, task_fm=task_fm, driver_actor=driver_actor, now=now, governance_root=governance_root)
        msg = "无有效 autonomy 信封, 拒绝裸跑。\n" + "\n".join(f"  - {r}" for r in reasons) + f"\n申领出口(Owner 亲自敲):\n  {hint}"
        say(f"lybra loop {task_id}: exit 5 — {msg}")
        return LoopResult(task_id, "no_envelope", exit_code_for(contract, "no_envelope"), msg, suggested_action=hint)
    envelope_id = str(policy.get("policy_id"))
    say(f"lybra loop {task_id}: envelope={envelope_id} driver={driver_actor} max_steps={max_steps} max_wait={max_wait}s")

    result = LoopResult(task_id, "completed", exit_code_for(contract, "completed"), "", envelope=envelope_id)
    # AIPOS-F90 件①: 已校验的驱动方身份与信封贯穿推导核与执行体(派生 claim/return/verdict/close 带同一 owner_policy_ref)
    with driver_scope(actor=driver_actor, policy_id=envelope_id):
        return _drive(task_id, governance_root, result, contract=contract, max_steps=max_steps, max_wait=max_wait,
                      interval=interval, allowed_verbs=allowed_verbs, say=say, derive=derive, execute=execute, watch=watch,
                      connection_json=connection_json)


# AIPOS-F90 件①(缺陷③): 账务步「门侧是否已落」的回读判据——该步对应的门生记录(_read_task_records 唯一读取口)在执行前后是否换了一份。
# verdict 记录挂被审卡(audit_verdicts/<被审卡>), 其余挂目标卡; finalize 不在此表(无回读 = 按门拒处理)。
_LANDED_RECORD = {"claim": "latest_claim", "return": "latest_return", "dispatch": "latest_audit_dispatch",
                  "verdict": "latest_verdict", "close": "latest_closure"}


def _record_card(action_type: str, target_card: str) -> str:
    return target_card[:-1] if action_type == "verdict" and target_card.upper().endswith("R") else target_card


def _landed_record(governance_root: Path, action_type: str, target_card: str) -> dict[str, Any] | None:
    key = _LANDED_RECORD.get(action_type)
    if not key:
        return None
    return _read_task_records(governance_root, _record_card(action_type, target_card)).get(key)


def _drive(
    task_id: str,
    governance_root: Path,
    result: LoopResult,
    *,
    contract: dict[str, Any],
    max_steps: int,
    max_wait: float,
    interval: float,
    allowed_verbs: set[str],
    say: Callable[[str], None],
    derive: Callable[[str, Path], dict[str, Any]],
    execute: Callable[..., dict[str, Any]],
    watch: Callable[..., int],
    connection_json: str | None,
) -> LoopResult:
    steps = result.steps

    for index in range(1, max_steps + 1):
        # 出口 0: closure 记录存在且治理已落账(AIPOS-F94 件①: 本卡落账范围已提交且已推送); 未落账 → 推导核派生 N6 落账步
        if _has_closure(governance_root, task_id):
            landing = _landing(governance_root, task_id)
            if landing is not None and landing.get("landed"):
                steps.append(LoopStep(index, "done", "close", "completed", task_id, message="closure 记录存在, 治理已落账"))
                result.message = (f"{task_id} 已结案(closure 记录存在), 治理已落账(已提交并推送到 {landing.get('upstream')}), "
                                  f"共 {index} 轮")
                say(f"[{index}] done: {result.message}")
                return result

        derivation = derive(task_id, governance_root)
        target_card = task_id
        ready_card = task_id  # 就绪谓词重推导的卡(缺省=等待目标; external finalize 时=本卡)
        wait_patterns: list[str] = []
        watch_root = governance_root

        if not derivation.get("derivable"):
            action = derivation.get("action") or {}
            if action.get("type") == "await_artifact" and action.get("card") and action.get("kind") == "finalize_return":
                # AIPOS-F78B 件②: finalize_mode=external, N4 PASS 后等外部 FINALIZE 卡的 Return(落点读项目声明, 与执行体 Return 同候选);
                # 就绪 = 本卡重推导可推导(派生 artifact ingest 铸 finalization 记录)或硬停(Return 不合规)
                target_card = str(action["card"])
                watch_root, wait_patterns = executor_artifact_watch(governance_root, target_card)
            elif action.get("type") == "await_artifact" and action.get("card"):
                # N3: 已派审, 审计体在干活 → 审计卡自身可能已可推导(claim 审计卡 / 提交裁决); 或已硬停(报告不合规/无 claim 记录)
                audit_card = str(action["card"])
                audit_derivation = derive(audit_card, governance_root)
                audit_action = audit_derivation.get("action") or {}
                if audit_derivation.get("derivable") or audit_action.get("type") in HARD_STOP_ACTIONS:
                    derivation, target_card, action = audit_derivation, audit_card, audit_action
                else:
                    target_card = ready_card = audit_card
                    watch_root, wait_patterns = auditor_artifact_watch(governance_root, audit_card)
            node = derivation.get("current_node")
            state = derivation.get("current_state")
            if action.get("type") in HARD_STOP_ACTIONS:
                # AIPOS-F78 件③ / F73E 件①: 产物已落盘但不合规 / 账务记录缺 → 不空等, exit 4 点名缺项(补齐后重跑 loop)
                missing = list(derivation.get("missing_records") or [])
                what = "产物不合规" if action.get("type") == "artifact_invalid" else "记录缺失"
                where = f": {action.get('path')}" if action.get("path") else ""
                msg = f"{what} @ {node}/{state} ({target_card}){where}: missing={missing}; suggested={derivation.get('suggested_action', '')}"
                steps.append(LoopStep(index, "execute", node, state, target_card, ok=False, message=msg))
                say(f"[{index}] exit 4 — {msg}")
                result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), msg
                result.missing_records, result.suggested_action = missing, str(derivation.get("suggested_action") or "")
                return result
            if derivation.get("derivable") or wait_patterns:
                pass  # 审计卡可推导 → 下方账务步; 已定等待 → 下方 watch
            elif node == "claim" and state == "claimed":
                # N1→N2: 执行体在干活 → 等 Return 落盘(落点读项目声明; 骨架不算, 判据=推导核)
                watch_root, wait_patterns = executor_artifact_watch(governance_root, task_id)
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

            def _ready(_matched: list[str], _card: str = ready_card) -> bool:
                # 就绪 = 推导核可推导; 或硬停(产物不合规 F78 件③ / 记录缺 F73E 件①)——都该让 loop 醒来判定, 而非空等到超时
                d = derive(_card, governance_root)
                return bool(d.get("derivable")) or (d.get("action") or {}).get("type") in HARD_STOP_ACTIONS

            watch_out = io.StringIO()
            with contextlib.redirect_stdout(watch_out):
                rc = watch(_watch_args(watch_root, wait_patterns, max_wait=max_wait, interval=interval), expect_ready=_ready)
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
        envelope_verb = _ENVELOPE_VERB_FOR_ACTION.get(action_type, action_type)
        if envelope_verb not in allowed_verbs:
            step.ok, step.message = False, f"信封未授权动词 {envelope_verb}(动作 {action_type}; 允许: {sorted(allowed_verbs)})"
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
        before = _landed_record(governance_root, action_type, target_card)
        exec_result = execute(derivation, governance_root, connection_json)
        step.ok = bool(exec_result.get("ok"))
        step.exit_code = int(exec_result.get("exit_code") or 0)
        step.output = str(exec_result.get("output") or "")
        step.message = str(exec_result.get("message") or "")
        step.shell_command = str(exec_result.get("shell_command") or derivation.get("shell_command") or "")
        steps.append(step)
        if not step.ok:
            # AIPOS-F90 件①(缺陷③): 执行报失败后先回读该步的门生记录——执行前后换了一份(门侧已落, 如薄壳等门超时的假失败)
            # = 记为已落继续, 绝不重复执行同一步; 未落 = 真门拒, exit 2 透传原文(不重试)
            landed = _landed_record(governance_root, action_type, target_card)
            if landed and landed != before:
                step.ok = True
                step.message = (f"执行端报失败(exit {step.exit_code}), 但回读门生记录已新落 "
                                f"({_LANDED_RECORD[action_type]}: {landed.get('claim_id') or landed.get('return_id') or landed.get('verdict_id') or landed.get('dispatch_id') or landed.get('closure_id') or '新记录'}) = 门侧已落, 不重复执行: {step.message}")
                say(f"[{index}] landed(回读): {step.message}")
                continue
            what = "落账拒" if action_type == "governance_commit" else "门拒"
            msg = f"{what} @ {action_type} ({target_card}) exit {step.exit_code}: {step.message}\n{step.output}".rstrip()
            say(f"[{index}] exit 2 — {msg}")
            result.outcome, result.exit_code, result.message = "gate_rejected", exit_code_for(contract, "gate_rejected"), msg
            return result
        say(f"[{index}] ok: {step.message}")
        if action_type == "governance_commit":
            # AIPOS-F94 件①: 落账步报成功后以判据复核(已提交且已推送); 仍未落账 = 不重试, exit 4 点名缺什么
            landing = _landing(governance_root, target_card)
            if landing is None or not landing.get("landed"):
                reason = (landing or {}).get("reason") or "落账判据读取失败"
                msg = f"落账步报成功但判据仍未落账 ({target_card}): {reason}"
                say(f"[{index}] exit 4 — {msg}")
                result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), msg
                result.missing_records = [reason]
                return result

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
