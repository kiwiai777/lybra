"""AIPOS-F73D — `lybra loop`: 顾问侧驱动器(Owner 信封授权下, 有界循环推进一张卡到 completed)。

一句话: 把顾问逐步敲 `next --run` 变成一条命令——产物落盘就自动推进, 直到 completed;
agent 步只等产物; 四出口、有界、fail-closed。
AIPOS-F95: 唯一例外——Owner 信封 launch_harnesses 授权该卡 harness 且条件全满足时, 等待前按声明模板在工位拉起一次 harness
(AIPOS-F110: 跨机工位经 ssh 同一分层拉起/清理, harness_launch remote transport)
进程(过程汇总为一行式进度, 产物就绪/超时/早退/中断即清进程组); 否则退回手工模式(工位敲 /go)。无守护/调度/心跳/常驻。

单一实现(禁第二推导核/第二哨兵/第二 claim 路径, 禁直调 board_adapter):
- 推导: next_resolver.derive_next_step(AIPOS-F71 唯一推导核)
- 执行: next_resolver.execute_derived_action(AIPOS-F73 `next --run` 同一执行体)
- 等待: agent_watch_fs.run_fs_watch(AIPOS-268/284 唯一哨兵, `--expect` + 就绪谓词=推导核可推导)
- 拉起: 本模块 plan_launch / LaunchedHarness(AIPOS-F95; 模板 enums.schema harness.launch, 授权 autonomy_policy.envelope_authorizes_launch,
  工位位置 enrollment.workstation_location(经 instance_enrollment, AIPOS-F146), 身份 charter_render.workstation_identity,
  kickoff = 工位 my-tasks --task-id <等待目标卡> 的 next_card.kickoff(AIPOS-F111: 按卡号取, 同工位多卡可各自拉起))
- 拉起前章程(AIPOS-F164): _launched_wait 起点 _charter_before_launch —— 判定 distribution_sync.charter_freshness(= `lybra sync --dry-run`
  同一实现), 修复 distribution_sync.sync_workstation_charter(= `lybra sync` 同一实现); check-workstation 章程项同一判定(check_charter)
- 信封: autonomy_policy(owner_autonomy_policy 族, `lybra envelope mint` 申领)
- 退出码/参数/允许动词集合/等待产物: schema/verbs.schema.json verbs.lybra_loop 一处声明, 本模块只读。
- 部署一致性(AIPOS-F152 件①): deploy_runtime.DeployGuard(声明 verbs.lybra_loop.deploy_consistency)——启动记下运行快照提交,
  每步推导前比对 current, 变了 = end_reason deploy_changed 干净结束(手工等待期经 run_fs_watch deploy_guard 每轮比对; 拉起等待期不打断)。
  AIPOS-F155 件③: 本 loop 自身 finalize 的部署经 DeployGuard.adopt_own_deploy 采纳为比对基线(_adopt_own_deploy), 不 deploy_changed。

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
import os
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from tools.aipos_cli.clock import utc_now
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, TextIO

from tools.aipos_cli.harness_launch import (  # AIPOS-F95: 拉起进程封装(无 sleep; 计时只用于 select 超时)
    LaunchedHarness,
    LoopInterrupted,
    TolerantOutput,
    signals_deferred as _signals_deferred,
    signals_raise_interrupt as _signals_raise_interrupt,
)
from tools.aipos_cli.verb_contract import declared_exit_code  # AIPOS-F101 件③: 退出码唯一读取口
from tools.aipos_cli.loop_run_record import LoopRunRecorder, LoopRunRecordError, model_label  # AIPOS-F169 model_label; AIPOS-F131: 运行记录 + 日志(唯一实现)
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
    frontmatter_unreadable_stop,
    repo_turn_watch,
)
from tools.aipos_cli.frontmatter import FrontmatterReadError

LOOP_VERB = "lybra_loop"
DRIVER_ROLE = "advisor"
# 推导核标出的硬停动作: 产物已落盘但不合规(F78 件③) / 账务记录缺(F73E 件①: 无 claim 记录)——都不是"agent 还在干活", 禁空等, exit 4 点名
HARD_STOP_ACTIONS = frozenset({"artifact_invalid", "record_missing", "frontmatter_unreadable"})  # 驱动方角色(roles.schema advisor 持账务动词; 信封 agent_or_role 可写角色名或实例名)


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
    """按出口名取退出码(唯一来源=声明; AIPOS-F101 件③ 委托唯一实现 verb_contract.exit_code_in)。"""
    from tools.aipos_cli.verb_contract import exit_code_in

    return exit_code_in(contract, outcome, LOOP_VERB)


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
    launch: dict[str, Any] | None = None  # AIPOS-F95 件③: 等待步的拉起判定/结果(None = 非 agent 等待步)

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
            "launch": self.launch,
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
    reason: str = ""  # AIPOS-F131: 结束原因(细于 outcome; 取值声明 verbs.schema lybra_loop.run_record.end_reasons, 空 = 同 outcome)
    run_record: str = ""  # AIPOS-F131: 本次运行记录 / 日志路径(产品给出)
    run_log: str = ""

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
            "reason": self.reason or self.outcome,
            "run_record": self.run_record,
            "run_log": self.run_log,
        }


class _RunOutput:
    """AIPOS-F131 件③: loop 的输出口。人读行照旧上 stdout(TolerantOutput, 行为不变), 运行记录建好后同时写进记录旁的日志
    (LoopRunRecorder.log 逐行过 redact_progress)。拉起进程的一行式进度(含发言首行/工具首参)走 progress = 只上 stdout;
    其活动只以类别经 activity 进运行记录与日志(LaunchedHarness 按这两个可选口取用)。"""

    def __init__(self, stdout: Callable[[str], None]) -> None:
        self.progress = stdout
        self.recorder: LoopRunRecorder | None = None

    def __call__(self, line: str) -> None:
        self.progress(line)
        if self.recorder is not None:
            self.recorder.log(line)

    def activity(self, category: str) -> None:
        if self.recorder is not None:
            self.recorder.activity(category)


# ---------------------------------------------------------------------------
# 件③ 信封校验(复用 owner_autonomy_policy 族)
# ---------------------------------------------------------------------------

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
    """在项目声明的信封目录(project.json paths.policies_root)找覆盖本卡与驱动方身份的有效 PreAuthorized 信封。

    AIPOS-F103 件④: 挑选唯一实现 = autonomy_policy.select_envelope(判据只有 match_claim_envelope 严格 AND: 有效/时间窗/
    agent_or_role 覆盖驱动方实例或 advisor 角色/task_selector 覆盖本卡/额度未尽), 本函数只组驱动方身份与判定对象。
    返回 (policy | None, 每个候选的未匹配原因)。
    AIPOS-F78B 件③: 驱动方身份集合 = {实例, 工位角色名(如 chris 的 hbj-advisor), 角色类 advisor}——信封 agent_or_role 写其一即覆盖
    (门侧 _match_driver_envelope 同口径; AIPOS-F134 件①: 集合只由 autonomy_policy.driver_envelope_identities 构造, 门与 loop 同一函数)。AIPOS-F90 件①: 判定对象(审计卡 = 被审卡)经 envelope_subject, 与门同一规则。
    """
    from tools.aipos_cli.autonomy_policy import driver_envelope_identities, select_envelope

    task = {
        "task_id": task_id,
        "task_mode": str(task_fm.get("task_mode") or ""),
        "project": str(task_fm.get("project") or ""),
        "reviewed_task_id": str(task_fm.get("reviewed_task_id") or ""),
        # AIPOS-F134 件③: 信封 task_selector_lane_repo 判定对象(卡 lane; 解析唯一 workspace_config.resolve_card_repo, 经 envelope_subject)
        "lane": task_fm.get("lane") if isinstance(task_fm.get("lane"), dict) else {},
    }
    return select_envelope(
        governance_root,
        # AIPOS-F134 件①: 驱动方身份集合唯一构造(门侧 _match_driver_envelope / _match_claim_envelope 同用)
        identities=driver_envelope_identities(driver_actor, driver_role, DRIVER_ROLE),
        task=task,
        policy_id=policy_id,
        now=now,
    )


def mint_hint(*, task_id: str, task_fm: dict[str, Any], driver_actor: str, now: datetime | None = None,
              governance_root: Path | None = None) -> str:
    """申领出口: 既有 `lybra envelope mint --confirm` 命令(AIPOS-F92 件①: 经门 owner_decision_record envelope 路径真实落盘;
    Owner 亲自敲, --connection-json 指向持 Owner 凭据的 connection.json), 参数按本卡填好可照抄。"""
    from tools.aipos_cli.autonomy_policy import envelope_renew_declaration, next_policy_id, render_mint_command

    now = now or utc_now()
    project = str(task_fm.get("project") or "project").strip() or "project"
    task_mode = str(task_fm.get("task_mode") or "code").strip() or "code"
    expires = (now + timedelta(days=int(envelope_renew_declaration()["renew_days"]))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # AIPOS-F166 件②: policy_id 不再写死 _1(10-12 实撞: 建议已用过的 <项目>_loop_1)——按该驱动方同前缀信封已有最大序号 +1
    policy_id = (next_policy_id(governance_root, identities={driver_actor, DRIVER_ROLE}, project=project)
                 if governance_root else f"pol_{project}_loop_1")
    return render_mint_command(policy_id=policy_id, agent_or_role=driver_actor, max_tasks=20, task_mode=task_mode, expires_at=expires,
                               decision_summary=f"loop envelope for {task_id}", governance_root=governance_root)


def _envelope_ending(governance_root: Path, envelope_id: str | None, *, task_id: str, task_fm: dict[str, Any], driver_actor: str,
                     driver_role: str | None, what: str, now: datetime | None = None) -> dict[str, Any] | None:
    """AIPOS-F166 件②(gap #152): 信封被拒时读门侧用量真相(autonomy_policy.envelope_usage, 与门 count_bound / 时间窗同一计数)判是否
    「额度用尽 / 已过期」。是 = {reason(envelope_exhausted|envelope_expired), message(用量/上限/到期 + 续签命令), suggested_action(续签命令),
    state(运行记录 envelope_state)}; 否(信封仍有效 / 缺 / 撤销等)= None, 调用方照旧出口。文案与结束原因名读声明 lybra_loop.envelope.renew。"""
    from tools.aipos_cli.autonomy_policy import envelope_renew_declaration, envelope_usage, renew_plan

    if not envelope_id:
        return None
    usage = envelope_usage(governance_root, envelope_id, now=now)
    if usage.get("state") not in ("exhausted", "expired"):
        return None
    decl = envelope_renew_declaration()
    identities = {driver_actor, DRIVER_ROLE} | ({driver_role} if driver_role else set())
    plan = renew_plan(governance_root, usage, identities=identities, task_id=task_id, card_task_mode=str(task_fm.get("task_mode") or ""), now=now)
    head = decl["messages"][usage["state"]].format(policy_id=usage["policy_id"], used=usage["used"], max_tasks=usage["max_tasks"],
                                                    expires_at=usage["expires_at"], what=what)
    renew = decl["messages"]["renew"].format(next_policy_id=plan["next_policy_id"], policy_id=usage["policy_id"], command=plan["renew_command"])
    message = "\n".join([head, renew] + ([f"  注: {plan['note']}"] if plan["note"] else []))
    state = {k: usage.get(k) for k in ("policy_id", "state", "used", "max_tasks", "remaining", "expires_at")}
    state.update(plan)
    return {"reason": str(decl["end_reasons"][usage["state"]]), "message": message, "suggested_action": plan["renew_command"], "state": state}


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


def _supplementary_in_flight(governance_root: Path, task_id: str) -> bool:
    """AIPOS-F162 件②: 已结案卡是否有在途补充审计(唯一判定 audit_derivation.supplementary_in_flight)。"""
    from tools.aipos_cli.audit_derivation import supplementary_in_flight

    return supplementary_in_flight(governance_root, task_id) is not None


def _runtime_evidence_loop_declaration() -> dict[str, Any]:
    """AIPOS-F162 件①: transitions artifact_ingest.verdict.runtime_evidence.loop(派生动作名 / 拉起上限 / 结束原因)。缺 = SchemaLoadError。"""
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration
    from tools.schema_loader import SchemaLoadError

    loop = runtime_evidence_declaration()["loop"]
    if not loop.get("derivation_action") or not loop.get("exhausted_end_reason") or not isinstance(loop.get("relaunch_cap"), int):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.runtime_evidence.loop(derivation_action/relaunch_cap/exhausted_end_reason)未声明")
    return loop


def _report_fingerprint(path: Any) -> str:
    """推导核点名的审计报告(evidence_rework action.path = 裁决入口所读同一份)当前内容 sha256; 无 / 不在 = 空串。"""
    import hashlib

    report = Path(str(path)) if path else None
    try:
        return hashlib.sha256(report.read_bytes()).hexdigest() if report is not None and report.is_file() else ""
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"


def _landing(governance_root: Path, task_id: str) -> dict[str, Any] | None:
    """落账判据(governance_commit.task_landing 唯一实现); 读失败 = None(交推导核按拒因出口, 不在此吞成已落账)。"""
    import subprocess

    from tools.aipos_cli.governance_commit import task_landing

    try:
        return task_landing(governance_root, task_id)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# AIPOS-F95 件③ 拉起薄函数: 信封 launch_harnesses 授权 + 条件全满足 → 等待前在本机工位拉起一次 harness 进程;
# 否则退回手工模式(工位敲 /go)。无守护/调度/心跳/常驻: 进程只活在本次等待内, 等待结束(就绪/超时/早退/中断)即清进程组。
# 声明: verbs.schema lybra_loop.launch(宽限/终止/stderr 末尾/进度截断/手工提示)、enums.schema harness.launch(argv 模板)、
# enums.schema workstation_transport(工位位置 transport); 授权判据 autonomy_policy.envelope_authorizes_launch(唯一实现)。
# ---------------------------------------------------------------------------

_LAUNCH_KEYS = ("grace_seconds", "terminate_wait_seconds", "stderr_tail_lines", "progress_max_chars", "manual_hint")


def launch_declaration(contract: dict[str, Any]) -> dict[str, Any]:
    """verbs.schema lybra_loop.launch。缺键 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = contract.get("launch")
    if not isinstance(decl, dict) or any(k not in decl for k in _LAUNCH_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.{LOOP_VERB}.launch 未声明齐 {list(_LAUNCH_KEYS)}")
    return decl


def resume_prompt_template(decl: dict[str, Any]) -> str:
    """AIPOS-F171B 件②: verbs.schema lybra_loop.launch.resume.prompt(须含 {artifact})。缺 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    prompt = (decl.get("resume") or {}).get("prompt") if isinstance(decl.get("resume"), dict) else None
    if not isinstance(prompt, str) or "{artifact}" not in prompt:
        raise SchemaLoadError(f"verbs.schema.json verbs.{LOOP_VERB}.launch.resume.prompt 未声明(须为含 {{artifact}} 的串)")
    return prompt


@dataclass
class LaunchPlan:
    """一次等待的拉起判定。refusal 非空 = 不拉起(退回手工, 等待提示 = manual_hint)。"""

    card: str
    harness: str = ""
    instance: str = ""
    location: dict[str, Any] = field(default_factory=dict)
    manual_hint: str = ""
    refusal: str = ""
    argv: list[str] = field(default_factory=list)
    cwd: str = ""
    events: str = ""
    kickoff: str = ""
    remote: dict[str, Any] = field(default_factory=dict)  # AIPOS-F110: transport=remote 时 {host, dir, identity_source}
    privilege_guard: dict[str, Any] = field(default_factory=dict)  # AIPOS-F165 件③: harness_launch.privilege_guard_state 判定
    model: dict[str, Any] = field(default_factory=dict)  # AIPOS-F169: {model(None = 不传 --model), source, role}(pi_model_for_role 判定)
    # AIPOS-F171B 件②: 固定会话 id(harness 模板 session 声明; 空 = 不固定、不续接)/ 续接上限(project.json launch_resume_max)/ 续接指令模板
    session_id: str = ""
    resume_max: int = 0
    resume_prompt: str = ""

    def append_kickoff_note(self, note: str) -> None:
        """AIPOS-F162 件①: 开工提示后附一段(拒因补做); argv 中整参数 kickoff 同步替换(跨机 kickoff 经 stdin 读 self.kickoff)。"""
        old = self.kickoff
        self.kickoff = f"{old}\n\n{note}" if old else note
        self.argv = [self.kickoff if a == old else a for a in self.argv]

    def resumed(self, prompt: str) -> "LaunchPlan":
        """AIPOS-F171B 件②: 同会话续接的拉起计划——同一 argv 模板与会话 id, 整参数 kickoff 换成续接指令(跨机经 stdin 读 kickoff)。"""
        import dataclasses

        old = self.kickoff
        return dataclasses.replace(self, kickoff=prompt, argv=[prompt if a == old else a for a in self.argv])

    def to_dict(self) -> dict[str, Any]:
        return {"card": self.card, "harness": self.harness, "instance": self.instance, "launched": not self.refusal,
                "refusal": self.refusal, "manual_hint": self.manual_hint if self.refusal else "",
                "workstation": self.location.get("dir"), "host": self.location.get("host"),
                "transport": self.location.get("transport"), "privilege_guard": dict(self.privilege_guard),
                "model": dict(self.model) or None, "session_id": self.session_id or None, "resume_max": self.resume_max}


def card_harness(task_fm: dict[str, Any]) -> str:
    """卡的 harness: 卡面值, 缺省 card.schema intent_face.harness.default_by_task_mode(与 artifact_ingest 同一规则)。"""
    from tools.aipos_cli.machine_zone import default_harness_for_task_mode

    return str(task_fm.get("harness") or "").strip() or default_harness_for_task_mode(str(task_fm.get("task_mode") or "code"))


def _wait_instance(governance_root: Path, card: str, task_fm: dict[str, Any]) -> str:
    """等待目标卡该由哪个实例干活: 审计卡 = 其认领实例(claim 记录, 推导核 _claimer_instance 唯一实现); 执行卡 = assigned_to。"""
    from tools.aipos_cli.next_resolver import _claimer_instance, forensic_subject

    if forensic_subject(task_fm) is not None:
        return _claimer_instance(_read_task_records(governance_root, card)) or str(task_fm.get("claimed_by") or "").strip()
    return str(task_fm.get("assigned_to") or "").strip()


def wait_instance_is_subagent(governance_root: Path, card: str, task_fm: dict[str, Any]) -> bool:
    """AIPOS-F157 件③(gap #125): 等待目标实例(_wait_instance)是无工位的子 agent 执行者——判定只经 enrollment.instance_enrollment
    (governance_root_mode, AIPOS-F146 唯一判定)。plan_launch 的开工提示与 loop_run_record.run_execution_mode 的顾问下一动作同读此判定,
    子 agent 执行者的等待 / 等交回超时一律子 agent 口径(不出工位 /go)。实例为空 / 接入登记读不出 = False(按既有口径, 不猜)。"""
    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.schema_loader import SchemaLoadError

    instance = _wait_instance(governance_root, card, task_fm)
    if not instance:
        return False
    try:
        return bool(instance_enrollment(governance_root, instance)["governance_root_mode"])
    except (ValueError, OSError, SchemaLoadError):
        return False


def _render_manual_hint(decl: dict[str, Any], *, harness: str, instance: str, location: dict[str, Any],
                        subagent: bool = False, card: str = "") -> str:
    """手工模式等待提示(声明 verbs.schema lybra_loop.launch.manual_hint 唯一来源)。AIPOS-F146 件②: subagent = 等待目标实例为无工位的
    子 agent 执行者(instance_enrollment governance_root_mode)→ manual_hint.subagent(顾问派子 agent 按卡执行并写交回, 非工位 /go)。"""
    from tools.schema_loader import SchemaLoadError

    hints = decl["manual_hint"]
    if subagent:
        if not isinstance(hints.get("subagent"), str) or not hints["subagent"]:
            raise SchemaLoadError(f"verbs.schema.json verbs.{LOOP_VERB}.launch.manual_hint.subagent 未声明")
        template = str(hints["subagent"])
    elif location.get("found") and location.get("transport") == "local":
        template = str(hints["local"])
    elif location.get("found"):
        template = str(hints["remote"])
    else:
        template = str(hints["unlocated"])
    values = {"dir": str(location.get("dir") or ""), "host": str(location.get("host") or ""), "harness": harness or "(未知 harness)",
              "instance": instance or "(未知实例)", "reason": str(location.get("reason") or ""), "card": card or "(未知卡)"}
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def workstation_kickoff(governance_root: Path, workstation: str, card: str, *,
                        remote: bool = False, instance: str = "") -> tuple[str, str]:
    """该工位 `my-tasks --workstation <dir> --task-id <等待目标卡> --json` 的 next_card.kickoff(与工位 `/go <卡号>` 同一产品输出,
    进程内同一 CLI 实现)。

    AIPOS-F111 件①: 按卡号取开工提示——只核验等待目标卡这一张(判据 next_resolver.kickoff_refusal, 与 /go <卡号> 同一判据:
    非 claimed/非本实例认领/已结案/产物已交 → 拒), 不再要求该卡是工位 next_card 选卡结果, 同一工位同时在办多张卡可各自拉起。
    返回 (kickoff, refusal): 产品拒因(原样转述) / kickoff 空 / 工位治理根 ≠ 本治理根 / 产品输出与指向不一致 = refusal 非空。

    AIPOS-F110 件②: remote=True = 跨机工位(门机读不到远端工位目录): 身份 = land 事件实例 → 同一 CLI
    `--workspace-root <本治理根> my-tasks --actor <实例> --task-id <卡> --remote-workstation --json`(同一 kickoff_refusal /
    选卡 / 开工提示渲染单源, 产品附门机材料段), 本函数不另渲染、不另判。"""
    from tools.aipos_cli.aipos_cli import main as cli_main

    if remote:
        argv = ["--workspace-root", str(governance_root), "my-tasks", "--actor", instance, "--task-id", card,
                "--remote-workstation", "--json"]
    else:
        argv = ["my-tasks", "--workstation", workstation, "--task-id", card, "--json"]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli_main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    if rc != 0:
        return "", f"工位 my-tasks --task-id {card} 失败(exit {rc}): {err.getvalue().strip()[-300:]}"
    try:
        data = json.loads(out.getvalue())
    except ValueError as exc:
        return "", f"工位 my-tasks --task-id {card} 输出非 JSON: {exc}"
    if not remote:
        ws_root = str((data.get("workstation") or {}).get("governance_root") or "")
        if not ws_root or Path(ws_root).resolve() != Path(governance_root).resolve():
            return "", f"工位治理根 {ws_root or '(未解析)'} ≠ 本 loop 治理根 {governance_root}"
    elif str(data.get("actor") or "") != instance:
        return "", f"my-tasks --actor {instance} 输出 actor={data.get('actor')}, 与 land 事件实例不一致"
    next_card = data.get("next_card")
    if not isinstance(next_card, dict):
        excluded = [e for e in (data.get("next_card_excluded") or []) if isinstance(e, dict)]
        reasons = "; ".join(f"{e.get('task_id')} {e.get('code')}: {e.get('reason')}" for e in excluded)
        return "", f"工位 my-tasks --task-id {card} 拒开工({reasons or '产品未给出拒因'})"
    if str(next_card.get("task_id") or "") != card:  # --task-id 只核验这一张; 不等 = 产品输出与指向不一致(fail-closed)
        return "", f"工位 my-tasks --task-id {card} 返回 next_card={next_card.get('task_id')}, 与指向不一致"
    kickoff = next_card.get("kickoff")
    if not isinstance(kickoff, str) or not kickoff.strip():
        return "", f"工位 my-tasks --task-id {card} next_card 缺 kickoff"
    return kickoff, ""


def plan_launch(governance_root: Path, card: str, *, policy: dict[str, Any] | None, no_launch: bool,
                decl: dict[str, Any], already_launched: set[str]) -> LaunchPlan:
    """拉起判定(全部条件 AND, 任一不满足 = refusal + 手工提示)。只读, 不起进程。"""
    import shutil

    from tools.aipos_cli.autonomy_policy import envelope_authorizes_launch, launchable_harnesses
    from tools.aipos_cli.charter_render import WorkstationIdentityError, workstation_identity
    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.schema_loader import SchemaLoadError

    plan = LaunchPlan(card=card)
    task_path, _queue = _find_task_in_queue(governance_root, card)
    if not task_path:
        plan.refusal = plan.manual_hint = f"队列中找不到 {card}"
        return plan
    try:
        fm = _read_frontmatter(task_path)
    except FrontmatterReadError as exc:
        # AIPOS-F100 件②: 卡读不出 = 不拉起(手工模式点名拒因), 不按空卡面猜 harness/实例
        plan.refusal = plan.manual_hint = str(exc)
        return plan
    try:
        plan.harness = card_harness(fm)
    except SchemaLoadError as exc:
        plan.refusal = f"卡 harness 不可推导: {exc}"
    plan.instance = _wait_instance(governance_root, card, fm)
    subagent = False
    try:
        # AIPOS-F146 件②: 实例身份与模式只经 instance_enrollment 一处判定; 工位位置 = 其结果中的 workstation_location(同一读取口)
        if plan.instance:
            view = instance_enrollment(governance_root, plan.instance)
            subagent = bool(view["governance_root_mode"])
            plan.location = view["workstation"]
        else:
            plan.location = {"found": False, "reason": f"{card} 无 assigned_to/认领实例"}
    except (ValueError, OSError, SchemaLoadError) as exc:
        plan.location = {"found": False, "reason": f"工位位置读取失败: {exc}"}
    # AIPOS-F156 件①: 本卡执行方式(只读 F143 声明, workspace_config.card_execution_mode 唯一判定)= 子 agent 时, 开工提示同为子 agent 口径
    # (执行者实例无子 agent 接入登记也不出「在工位敲 /go」的 pi 口径); 声明读不出 / 不合 = 不据声明改口径, 照接入模式判定
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError, card_execution_mode, project_execution

    try:
        by_declaration = card_execution_mode(project_execution(governance_root), fm,
                                             audit_card=is_audit_card(card, fm)) == "subagent"
    except (ExecutionDeclarationError, OSError, ValueError):
        by_declaration = False
    plan.manual_hint = _render_manual_hint(decl, harness=plan.harness, instance=plan.instance, location=plan.location,
                                           subagent=subagent or by_declaration, card=card)
    if subagent:  # 无工位可拉起: 不拉起, 等待提示 = 顾问派子 agent(信封是否授权拉起均同)
        plan.refusal = plan.refusal or f"实例 {plan.instance} 为子 agent 执行者(接入模式 {view['mode']}, 无工位), loop 不拉起"
    elif by_declaration:
        plan.refusal = plan.refusal or "本卡执行方式 = 顾问派子 agent(project.json execution, AIPOS-F143), loop 不拉起"

    def refuse(reason: str) -> LaunchPlan:
        plan.refusal = plan.refusal or reason
        return plan

    if plan.refusal:
        return plan
    if no_launch:
        return refuse("--no-launch")
    if card in already_launched:
        return refuse(f"本次 loop 运行已拉起过 {card}, 不自动重试(重跑 loop = 显式再拉起)")
    try:
        template = launchable_harnesses().get(plan.harness)
    except SchemaLoadError as exc:
        return refuse(f"harness 声明读取失败: {exc}")
    if not template or not isinstance(template.get("argv"), list) or not template["argv"]:
        return refuse(f"harness {plan.harness} 无 launch 模板(enums.schema harness.launch=null, 只支持手工)")
    authorized, why = envelope_authorizes_launch(policy, plan.harness)
    if not authorized:
        return refuse(why)
    if not plan.location.get("found"):
        return refuse(f"工位位置定位不到: {plan.location.get('reason')}")
    if not plan.location.get("supported"):
        return refuse(f"transport {plan.location.get('transport')} 已声明未支持(enums.schema workstation_transport)")
    workstation = str(plan.location["dir"])
    argv_template = [str(a) for a in template["argv"]]
    unknown = [a for a in argv_template if "{" in a and a != "{kickoff}"]
    if unknown or "{kickoff}" not in argv_template:
        return refuse(f"harness {plan.harness} launch.argv 占位非法(仅允许整参数 {{kickoff}}): {argv_template}")
    plan.events = str(template.get("events") or "")
    # AIPOS-F165 件③: 拉起禁提权判定(唯一实现 harness_launch.privilege_guard_state; check-workstation 同读); 声明不合 = 不拉起
    from tools.aipos_cli.harness_launch import privilege_guard_state

    try:
        plan.privilege_guard = privilege_guard_state(decl, governance_root, plan.location.get("transport"))
    except (ValueError, OSError, SchemaLoadError) as exc:
        return refuse(f"拉起禁提权声明不可用(fail-closed, 不拉起): {exc}")
    # AIPOS-F169: 模型参数唯一注入处(local 与 remote 同一 argv 模板)。声明 = project.json execution.pi_models(pi_model_for_role 唯一判定,
    # 审计卡 = auditor, 其余 = executor); 未声明 = 不追加(pi 自身缺省); 声明不合 / 模板缺 model 声明 = 不拉起(fail-closed)
    from tools.aipos_cli.harness_launch import model_launch_args
    from tools.aipos_cli.workspace_config import pi_model_for_role

    try:
        plan.model = pi_model_for_role(governance_root, "auditor" if is_audit_card(card, fm) else "executor")
        if plan.model["model"]:
            argv_template = argv_template + model_launch_args(template, plan.harness, plan.model["model"])
    except (ExecutionDeclarationError, ValueError, OSError, SchemaLoadError) as exc:
        return refuse(f"pi 模型声明不可用(fail-closed, 不拉起): {exc}")
    # AIPOS-F171B 件②: 固定会话 id 唯一注入处(模型参数之后; local 与 remote 同一 argv 模板)。模板 session 声明(enums.schema)缺 = 不固定、不续接;
    # 续接上限 = project.json launch_resume_max(唯一读取口), 续接指令 = verbs.schema lybra_loop.launch.resume.prompt; 任一不合 = 不拉起(fail-closed)
    from tools.aipos_cli.harness_launch import session_launch_args
    from tools.aipos_cli.workspace_config import launch_resume_max

    try:
        session = session_launch_args(template, plan.harness, card)
        if session is not None:
            plan.session_id, session_args = session
            plan.resume_max = launch_resume_max(governance_root)
            plan.resume_prompt = resume_prompt_template(decl)
            argv_template = argv_template + session_args
    except (ValueError, OSError, SchemaLoadError) as exc:
        return refuse(f"pi 会话 / 续接声明不可用(fail-closed, 不拉起): {exc}")
    if plan.location.get("transport") == "remote":
        return _plan_remote_launch(governance_root, plan, decl, argv_template, refuse)
    try:
        identity = workstation_identity(workstation)
    except WorkstationIdentityError as exc:
        return refuse(f"工位身份不可解析: {exc}")
    if identity.get("instance") != plan.instance:
        return refuse(f"工位 {workstation} 的实例 {identity.get('instance')} ≠ 卡实例 {plan.instance}")
    if not shutil.which(argv_template[0]):
        return refuse(f"harness 可执行 {argv_template[0]} 不在驱动方 PATH")
    kickoff, why = workstation_kickoff(governance_root, workstation, card)
    if why:
        return refuse(why)
    plan.kickoff = kickoff
    plan.argv = [kickoff if a == "{kickoff}" else a for a in argv_template]
    plan.cwd = workstation
    return plan


def _plan_remote_launch(governance_root: Path, plan: LaunchPlan, decl: dict[str, Any], argv_template: list[str],
                        refuse: Callable[[str], LaunchPlan]) -> LaunchPlan:
    """AIPOS-F110: transport=remote 的拉起判定(plan_launch 内分支, 同一 refusal → 手工提示出口)。

    ①身份 = land 事件实例(门机读不到远端 .lybra/role; workstation_location 按实例取最新 land 事件, 实例即 plan.instance);
    ②材料 = project.json workstations.<实例>(gate_ssh_alias / material_access), 未声明/含凭据 = 拒并提示补声明;
    ③kickoff = 同一 my-tasks(--actor <实例> --task-id --remote-workstation: 同一 render_kickoff 附门机材料段);
    ④ssh 探测可达 + 远端工位目录 + 远端 harness 可执行(不可达 = 不拉起, 退回手工并提示原因);
    ⑤argv = ssh 前缀 + [host, 远端固定脚本 + 引号化参数], kickoff 只走 stdin。"""
    import shutil

    from tools.aipos_cli.harness_launch import probe_remote, remote_declaration, remote_launch_command
    from tools.aipos_cli.workspace_config import WorkstationDeclarationError, project_workstation

    host, workstation = str(plan.location.get("host") or ""), str(plan.location["dir"])
    try:
        remote_decl = remote_declaration(decl)
        project_workstation(governance_root, plan.instance)  # 材料声明先核(my-tasks --remote-workstation 渲染时同一读取口)
    except WorkstationDeclarationError as exc:
        return refuse(f"跨机工位 {host}:{workstation} 开工材料未声明齐({exc}); 补声明: lybra project set-workstation <项目名> "
                      f"--instance {plan.instance} --gate-ssh-alias <门机别名> --material-access <材料访问说明>")
    except (ValueError, OSError) as exc:
        return refuse(f"跨机拉起声明读取失败: {exc}")
    ssh_exe = str(remote_decl["ssh_argv"][0])
    if not shutil.which(ssh_exe):
        return refuse(f"ssh 执行器 {ssh_exe} 不在驱动方 PATH")
    kickoff, why = workstation_kickoff(governance_root, workstation, plan.card, remote=True, instance=plan.instance)
    if why:
        return refuse(why)
    try:
        argv = remote_launch_command(remote_decl, host, workstation, argv_template)
    except ValueError as exc:
        return refuse(f"跨机拉起命令不可构造: {exc}")
    unreachable = probe_remote(remote_decl, host, workstation, argv_template[0])
    if unreachable:
        return refuse(unreachable)
    plan.kickoff, plan.argv, plan.cwd = kickoff, argv, workstation
    plan.remote = {"host": host, "dir": workstation, "identity_source": "land_event"}
    return plan


def charter_refusal_text(fresh: dict[str, Any]) -> str:
    """AIPOS-F164: 章程不新鲜的原因 + 修法(check-workstation FAIL 详情 / loop 章程拒因同一措辞; 命令唯一渲染 distribution_sync.render_sync_command)。"""
    return f"{fresh['state']}: {fresh['detail']}; 修法: {fresh['repair_command']}(复核: {fresh['verify_command']})"


def check_charter(project_root: Path, workstation: str, check: Callable[[str, bool, str], bool]) -> dict[str, Any]:
    """AIPOS-F164 件①: check-workstation 章程项——判定只经 distribution_sync.charter_freshness(= `lybra sync --dry-run` 同一实现,
    F66B 三指纹, 零写入)。最新 / 无章程件 / 会话拉取 = ✓; 未渲染 / 指纹过期 / 缺失 / 核不了 = ✗ 并给修法(sync 命令)。"""
    from tools.aipos_cli.distribution_sync import charter_freshness

    fresh = charter_freshness(workstation, project_root)
    if fresh["fresh"]:
        check("章程新鲜度", True, f"{fresh['state']}: {fresh['detail']}")
    else:
        check("章程新鲜度", False, charter_refusal_text(fresh))
    return fresh


def _charter_before_launch(index: int, step: LoopStep, plan: LaunchPlan, governance_root: Path, result: LoopResult, *,
                           contract: dict[str, Any], say: Callable[[str], None], recorder: LoopRunRecorder) -> bool:
    """AIPOS-F164 件②: 拉起 pi 前核该工位章程新鲜度(挂在唯一拉起口 _launched_wait 起点)。返回 True = 不拉起, loop 以 result 出口返回。

    判定 = distribution_sync.charter_freshness(`lybra sync --dry-run` 同一实现); 最新 = 不 sync 直接拉起; 不新鲜(未渲染 / 过期 / 缺失 /
    核不了)且 project.json charter_auto_sync(缺省 true)= 先 sync 本工位(distribution_sync.sync_workstation_charter = `lybra sync` 同一实现,
    只本工位, 之后复核)再拉起; sync 失败 = 不拉起, 结束原因 charter_sync_failed; 自动 sync 关闭(或声明不合)= 不拉起, 结束原因
    charter_not_fresh。两者例行(lybra_loop_status next_action routine_rejections.end_reasons: advisor_fix, 连败计数同一实现)。
    每次判定进运行记录 charter_checks(detail)并在日志留一行(say)。remote 工位: 门机不跨机读写工位文件, 不核(日志一行说明)。"""
    from tools.aipos_cli.distribution_sync import charter_freshness, sync_workstation_charter
    from tools.aipos_cli.workspace_config import charter_auto_sync
    from tools.schema_loader import SchemaLoadError

    ws = plan.cwd
    entry = {"card": plan.card, "instance": plan.instance, "workstation": ws}
    if plan.location.get("transport") == "remote":
        detail = f"跨机工位 {plan.location.get('host')}:{ws}: 门机不跨机读写工位文件, 章程不在此核(在工位所在机 lybra sync --dry-run 核)"
        recorder.charter_check({**entry, "outcome": "not_applicable", "detail": detail})
        say(f"[{index}] charter: {detail}")
        return False
    fresh = charter_freshness(ws, governance_root)
    entry["repair_command"] = fresh["repair_command"]
    if fresh["fresh"]:
        detail = f"{fresh['state']}: {fresh['detail']}"
        recorder.charter_check({**entry, "outcome": "fresh", "detail": detail})
        say(f"[{index}] charter: {plan.instance} @ {ws} 章程新鲜({detail}), 不 sync, 直接拉起")
        return False
    try:
        auto, off_reason = charter_auto_sync(governance_root), "project.json charter_auto_sync=false"
    except (OSError, ValueError, SchemaLoadError) as exc:
        auto, off_reason = False, f"声明读不出 / 不合, 按不自动 sync: {exc}"
    stale = f"{fresh['state']}: {fresh['detail']}"
    if auto:
        say(f"[{index}] charter: {plan.instance} @ {ws} 章程不新鲜({stale}) → 自动 sync 本工位: {fresh['repair_command']}")
        synced = sync_workstation_charter(ws, governance_root)
        if synced["ok"]:
            recorder.charter_check({**entry, "outcome": "synced", "detail": f"{stale} → {synced['detail']}"})
            say(f"[{index}] charter: 已 sync {ws}({synced['detail']}), 拉起")
            return False
        reason, outcome = "charter_sync_failed", "sync_failed"
        msg = (f"工位 {ws} 章程不新鲜({stale}), 自动 sync 失败({synced['detail']}), 不拉起 {plan.harness}({plan.card}); "
               f"修法: {fresh['repair_command']}(复核: {fresh['verify_command']}), 修好用原命令重跑 loop")
    else:
        reason, outcome = "charter_not_fresh", "refused"
        msg = (f"工位 {ws} 章程不新鲜({stale}), 自动 sync 未开({off_reason}), 不拉起 {plan.harness}({plan.card}); "
               f"修法: {fresh['repair_command']}(复核: {fresh['verify_command']}), 修好用原命令重跑 loop")
    recorder.charter_check({**entry, "outcome": outcome, "detail": msg})
    step.ok, step.message = False, msg
    step.launch = {**(step.launch or {}), "launched": False, "refusal": msg}  # 未起进程(拉起判定已过, 章程拦下)
    say(f"[{index}] exit {exit_code_for(contract, 'not_derivable')} — {msg}")
    result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), msg
    result.suggested_action, result.reason = fresh["repair_command"], reason
    return True


def check_workstation(project_root: Path, instance: str, harness: str) -> dict[str, Any]:
    """AIPOS-F110 件③: `lybra project check-workstation` —— loop 拉起前置的逐项只读检查(与 plan_launch 同一读取口/同一 ssh 代码路径):
    land 事件位置 → (local) 工位身份 + harness 可执行在本机 PATH; (remote) 材料声明 + 门机→工位 ssh 探测(目录 + harness 可执行)
    + 工位→门机反向可达(远端经声明的 gate_ssh_alias 以同一 ssh 前缀 `test -d <门机治理根>`)。不起 harness、不写任何文件。
    AIPOS-F164 件①: (local) 另核章程新鲜度(check_charter: 与 `lybra sync --dry-run` 同一实现, 零写入); remote 工位目录在他机,
    门机不跨机读写工位文件, 本项不核(章程在工位所在机 `lybra sync --dry-run` 核)。"""
    import shutil

    from tools.aipos_cli.autonomy_policy import launchable_harnesses
    from tools.aipos_cli.charter_render import WorkstationIdentityError, workstation_identity
    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.aipos_cli.harness_launch import probe_remote, remote_check_reverse, remote_declaration
    from tools.aipos_cli.workspace_config import WorkstationDeclarationError, project_workstation
    from tools.schema_loader import SchemaLoadError

    checks: list[dict[str, Any]] = []
    guard: dict[str, Any] = {}  # AIPOS-F165 件③: 拉起禁提权状态(与 plan_launch 同一判定 harness_launch.privilege_guard_state)
    model: dict[str, Any] = {}  # AIPOS-F169: 该工位下次拉起将用的模型与来源(与 plan_launch 同一判定 pi_model_for_role)

    def check(name: str, ok: bool, detail: str) -> bool:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def report() -> dict[str, Any]:
        return {"ok": all(c["ok"] for c in checks), "instance": instance, "harness": harness, "checks": checks,
                "privilege_guard": guard or None, "pi_model": model or None}

    try:
        # AIPOS-F146 件②: 身份与模式经 instance_enrollment 一处判定; 工位位置 = 其 workstation(workstation_location 同一读取口)
        view = instance_enrollment(project_root, instance)
        loc = view["workstation"]
        if view["governance_root_mode"]:  # 子 agent 执行者: 无工位, 拉起检查不适用(明确出口 = 子 agent 等待提示, 声明 manual_hint.subagent)
            hint = _render_manual_hint(launch_declaration(load_loop_contract()), harness=harness, instance=instance, location=loc,
                                       subagent=True, card="<卡号>")
            check("接入模式", False, f"实例 {instance} 为子 agent 执行者(接入模式 {view['mode']}, 无工位), loop 不拉起、无工位可检查; "
                                   f"等待时: {hint}")
            return report()
    except (ValueError, OSError, SchemaLoadError) as exc:
        check("land 事件位置", False, f"读取失败: {exc}")
        return report()
    from tools.aipos_cli.harness_launch import privilege_guard_state

    try:
        guard.update(privilege_guard_state(launch_declaration(load_loop_contract()), project_root,
                                           loc.get("transport") if loc.get("found") else None))
    except (ValueError, OSError, SchemaLoadError) as exc:  # 声明不合 = 拉起会被拒(plan_launch 同一判定)
        check("拉起禁提权声明", False, f"{exc}(loop 将拒拉起)")
    if harness == "pi":
        _check_pi_model(project_root, instance, loc, model, check, land_role=view.get("role"))
    if not check("land 事件位置", bool(loc.get("found")),
                 f"{loc.get('host') or '(本机, 存量缺 host)'}:{loc.get('dir')} transport={loc.get('transport')}" if loc.get("found")
                 else str(loc.get("reason"))):
        return report()
    check("transport 已支持", bool(loc.get("supported")), f"{loc.get('transport')}(enums.schema workstation_transport)")
    try:
        template = launchable_harnesses().get(harness)
    except SchemaLoadError as exc:
        check("harness launch 模板", False, f"声明读取失败: {exc}")
        return report()
    if not template or not isinstance(template.get("argv"), list) or not template["argv"]:
        check("harness launch 模板", False, f"{harness}: launch=null 或缺 argv(只支持手工)")
        return report()
    check("harness launch 模板", True, f"{harness}: {template['argv']}")
    exe = str(template["argv"][0])
    workstation = str(loc["dir"])
    if loc.get("transport") != "remote":
        try:
            identity = workstation_identity(workstation)
            check("工位身份", identity.get("instance") == instance, f"{workstation}/.lybra/role instance={identity.get('instance')}")
        except WorkstationIdentityError as exc:
            check("工位身份", False, str(exc))
        check("harness 可执行(本机 PATH)", bool(shutil.which(exe)), exe)
        check_charter(project_root, workstation, check)
        return report()
    host = str(loc.get("host") or "")
    try:
        material = project_workstation(project_root, instance)
        check("材料声明", True, f"gate_ssh_alias={material['gate_ssh_alias']}; material_access={material['material_access']}")
    except WorkstationDeclarationError as exc:
        material = None
        check("材料声明", False, f"{exc}; 补声明: lybra project set-workstation")
    try:
        remote_decl = remote_declaration(launch_declaration(load_loop_contract()))
    except (ValueError, SchemaLoadError) as exc:
        check("remote 声明", False, str(exc))
        return report()
    forward = probe_remote(remote_decl, host, workstation, exe)
    if not check(f"门机→工位 ssh {host}(目录 + {exe})", not forward, forward or "可达, 目录在, 可执行在远端 PATH"):
        return report()
    if material is not None:
        gov = str(Path(project_root).resolve())
        reverse = remote_check_reverse(remote_decl, host, str(material["gate_ssh_alias"]), gov)
        check(f"工位→门机 ssh {material['gate_ssh_alias']}(治理根 {gov})", not reverse, reverse or "可达, 门机治理根在")
    return report()


def _check_pi_model(project_root: Path, instance: str, loc: dict[str, Any], model: dict[str, Any],
                    check: Callable[[str, bool, str], bool], *, land_role: Any = None) -> None:
    """AIPOS-F169: check-workstation 的模型项——该工位下次拉起将用的模型与来源(pi_model_for_role, 与 plan_launch 同一判定;
    角色 = 本实例是 execution.auditor → auditor / execution.pi_executor → executor, 未在声明中 = 接入 land 事件 role, 再缺 = executor)。
    只在出问题时记 ✗ 项(声明不合 = loop 将拒拉起; 本机工位声明的模型不在 pi 已配置清单 / 清单取不到 = 拉起后 pi 会失败),
    正常时只填 model(CLI 单独一行显示, 不增检查项)。"""
    from tools.aipos_cli.harness_launch import harness_model_catalog, near_models
    from tools.aipos_cli.workspace_config import (
        ExecutionDeclarationError,
        pi_model_for_role,
        pi_models_declaration,
        project_execution,
    )
    from tools.schema_loader import SchemaLoadError

    try:
        execution = project_execution(project_root) or {}
        roles = pi_models_declaration()["roles"]
        role = ("auditor" if execution.get("auditor") == instance else "executor" if execution.get("pi_executor") == instance
                else str(land_role) if land_role in roles else "executor")
        model.update(pi_model_for_role(project_root, role))
    except (ExecutionDeclarationError, ValueError, OSError, SchemaLoadError) as exc:
        check("pi 模型声明", False, f"{exc}(loop 将拒拉起)")
        return
    if not model["model"]:
        return
    if loc.get("found") and loc.get("transport") == "remote":
        model["availability"] = "跨机工位: 本机不核该机 pi 配置(在工位所在机 pi --list-models 核)"
        return
    try:
        catalog = harness_model_catalog("pi")
    except (ValueError, SchemaLoadError) as exc:
        check("pi 模型可用", False, f"模型清单声明不可用: {exc}")
        return
    if not catalog["ok"]:
        check("pi 模型可用", False, f"{model['model']}: 本机 pi 已配置模型清单取不到({catalog['detail']})")
    elif model["model"] not in catalog["models"]:
        near = near_models(model["model"], catalog["models"], int(pi_models_declaration()["near_match_limit"]))
        check("pi 模型可用", False, f"{model['model']} 不在本机 pi 已配置模型中({catalog['detail']}); 相近可用模型: {', '.join(near) or '(无)'}"
                                  "; 改声明: lybra project set-model")
    else:
        model["availability"] = f"在本机 pi 已配置模型中({catalog['detail']})"


def _session_event(governance_root: Path, card: str, actor: str, label: str, detail: str) -> str:
    """拉起/收尾事件追加进该卡既有 session record 的 Events(task_progress_writer 同一 writer, 不新建记录类型)。返回失败原因或空串。"""
    from tools.aipos_cli.task_progress_writer import append_session_event

    try:
        append_session_event(governance_root, card, actor=actor, event_label=label, detail=detail)
    except (RuntimeError, OSError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


def _launched_wait(index: int, step: LoopStep, plan: LaunchPlan, governance_root: Path, result: LoopResult, *,
                   contract: dict[str, Any], decl: dict[str, Any], watch: Callable[..., int], watch_args: SimpleNamespace,
                   ready: Callable[[list[str]], bool], say: Callable[[str], None], actor: str, envelope_id: str,
                   wait_patterns: list[str], recorder: LoopRunRecorder) -> bool:
    """拉起一次 harness 并经唯一哨兵等待产物(进程退出也唤醒), 结束即清进程组。返回 True = loop 应以 result 出口返回。

    产物就绪 → 宽限 grace_seconds 后终止进程组, 继续推导; 进程早退而产物未就绪 → exit 3 附 stderr 末尾;
    等待超时 → 终止进程组, exit 3; 收到 SIGINT/SIGTERM/SIGHUP(harness_launch.loop_signals)→ 终止进程组, 抛 LoopInterrupted;
    拉起事件写不进 session record → 终止, exit 4。
    AIPOS-F107 件①: 信号处置覆盖拉起全程——Popen 期间到的信号先记下(拉起后按中断处理), 拉起起至清组+收尾留痕止
    中断处置不撤(清理与留痕期间到的信号记下, 清完再抛), 不留「默认处置直接终止 loop、进程组成孤儿」的窗口。
    AIPOS-F131: 拉起/收尾与进程活动(只类别与时间)进运行记录; 等待期 sleeper 每轮读输出后按节流落盘(静默期也落最后一条)。
    AIPOS-F171B 件②: 进程 exit 0 而产物未就绪(半途停笔)且计划固定了会话(plan.session_id)= 以同一会话续接(plan.resumed, 指令
    plan.resume_prompt), 至多 plan.resume_max 次, 等待总时长仍为同一 --max-wait(续接用剩余时长); 用尽 / 无剩余时长才判早退。
    非 0 退出 / 被信号终止 / 超时不续接(_should_resume 唯一判定)。"""
    if _charter_before_launch(index, step, plan, governance_root, result, contract=contract, say=say, recorder=recorder):
        return True  # AIPOS-F164 件②: 章程不新鲜且未能 sync = 不拉起
    wait_timeout = exit_code_for(contract, "wait_timeout")
    deadline = utc_now() + timedelta(seconds=float(watch_args.timeout))  # 续接共用同一 --max-wait(F73D: 本模块不计时自旋)
    artifact = str(Path(str(watch_args.workspace_root)) / wait_patterns[0]) if wait_patterns else "(产物落点)"
    attempt, resume = plan, 0
    with _signals_raise_interrupt():
        while True:
            args = watch_args if resume == 0 else SimpleNamespace(**{**vars(watch_args), "timeout": (deadline - utc_now()).total_seconds()})
            run = _launch_once(index, step, attempt, governance_root, result, contract=contract, decl=decl, watch=watch,
                               watch_args=args, ready=ready, say=say, actor=actor, envelope_id=envelope_id, recorder=recorder,
                               resume=resume)
            if run["outcome"] == "artifact_ready":
                return False
            if run["outcome"] not in ("early_exit", "timeout"):
                return True  # 拉起失败 / 拉起事件写不进: _launch_once 已按出口填 result
            if run["outcome"] == "early_exit" and _should_resume(plan, run["returncode"], resume, (deadline - utc_now()).total_seconds()):
                resume += 1
                step.launch = {**(step.launch or {}), "session_id": plan.session_id, "resumes": resume}
                say(f"[{index}] resume: {plan.harness} {plan.card} exit 0 而产物未就绪(半途停笔) → 同会话 {plan.session_id} 续接 "
                    f"{resume}/{plan.resume_max}(等的是 {artifact})")
                attempt = plan.resumed(plan.resume_prompt.replace("{artifact}", artifact))
                continue
            step.ok = False
            if run["outcome"] == "early_exit":
                tail = "\n".join(f"    {line}" for line in run["stderr_tail"]) or "    (stderr 无输出)"
                resumed = (f"(同会话 {plan.session_id} 已续接 {resume}/{plan.resume_max} 次仍未写出)"
                           if plan.session_id and run["returncode"] == 0 else "")
                step.message = (f"拉起的 {plan.harness} 进程已退出(exit {run['returncode']})而产物未就绪{resumed}: 等的是 {wait_patterns}\n"
                                f"  stderr 末尾:\n{tail}")
            else:
                step.message = f"等待产物超时(watch exit {run['watch_rc']}), 已终止拉起的 {plan.harness} 进程组: 等的是 {wait_patterns}"
            say(f"[{index}] exit 3 — {step.message}")
            result.outcome, result.exit_code, result.message = "wait_timeout", wait_timeout, step.message
            result.reason = "launch_early_exit" if run["outcome"] == "early_exit" else "launch_timeout"
            return True


def _should_resume(plan: LaunchPlan, returncode: Any, resumed: int, remaining_seconds: float) -> bool:
    """AIPOS-F171B 件②: 是否同会话续接(唯一判定)——固定了会话、进程以 exit 0 自行结束(非 0 / 被信号终止 = 维持早退原判)、
    续接未达上限、等待仍有剩余时长。"""
    return bool(plan.session_id) and returncode == 0 and resumed < int(plan.resume_max) and remaining_seconds > 0


def _launch_once(index: int, step: LoopStep, plan: LaunchPlan, governance_root: Path, result: LoopResult, *,
                 contract: dict[str, Any], decl: dict[str, Any], watch: Callable[..., int], watch_args: SimpleNamespace,
                 ready: Callable[[list[str]], bool], say: Callable[[str], None], actor: str, envelope_id: str,
                 recorder: LoopRunRecorder, resume: int) -> dict[str, Any]:
    """一次拉起(resume=0)或同会话第 resume 次续接: 起进程 → 留痕 → 经唯一哨兵等待 → 收尾清进程组(调用方须已在 _signals_raise_interrupt 内)。
    返回 {outcome: artifact_ready | early_exit | timeout | launch_failed | record_failed, returncode, watch_rc, stderr_tail};
    launch_failed / record_failed 已按出口填 result。"""
    wait_timeout = exit_code_for(contract, "wait_timeout")
    terminate_wait = float(decl["terminate_wait_seconds"])
    where = f"{plan.location.get('host') or '本机'}:{plan.cwd}"
    harness: LaunchedHarness | None = None
    launch_error: OSError | None = None
    outcome = "error"
    session = f"; session={plan.session_id}; resume={resume}/{plan.resume_max}" if plan.session_id else ""
    try:
        with _signals_deferred() as early:  # Popen 期间不被打断(打断 = 子进程已起而无人持有 → 孤儿)
            try:
                harness = LaunchedHarness(plan, decl, say)
            except OSError as exc:
                launch_error = exc
        if harness is None:
            if early:
                raise LoopInterrupted(early[0])
            step.ok, step.message = False, f"拉起 {plan.harness} 失败({type(launch_error).__name__}: {launch_error}); 手工模式: {plan.manual_hint}"
            say(f"[{index}] exit 3 — {step.message}")
            result.outcome, result.exit_code, result.message = "wait_timeout", wait_timeout, step.message
            result.reason = "launch_failed"
            return {"outcome": "launch_failed"}
        step.launch = {**(step.launch or {}), "pid": harness.pid, "pgid": harness.pgid, "remote_pgid": harness.remote_pgid}
        remote_part = f" remote_pgid={harness.remote_pgid}(经 ssh, kickoff 经 stdin)" if plan.remote else ""
        what = f"同会话续接 {resume}/{plan.resume_max} 指令" if resume else f"工位 my-tasks --task-id {plan.card} next_card.kickoff"
        say(f"[{index}] launch: {plan.harness} @ {where} pid={harness.pid} pgid={harness.pgid}{remote_part}(信封 {envelope_id} launch_harnesses 授权; "
            f"kickoff = {what}; 禁提权 {harness.privilege_guard['state']}: {harness.privilege_guard['detail']}; "
            f"模型 {model_label(plan.model)}" + (f"; 会话 {plan.session_id}" if plan.session_id else "") + ")")
        recorder.launch_started(plan, harness, resume=resume)
        if early:
            raise LoopInterrupted(early[0])
        failure = _session_event(governance_root, plan.card, actor, "harness_launch",
                                 f"harness={plan.harness}; transport={plan.location.get('transport')}; host={plan.location.get('host') or '(本机, 存量缺 host)'}; "
                                 f"workstation={plan.cwd}; pid={harness.pid}; pgid={harness.pgid}; remote_pgid={harness.remote_pgid}; envelope={envelope_id}; "
                                 f"privilege_guard={harness.privilege_guard['state']}({harness.privilege_guard['detail']}); "
                                 f"model={(plan.model or {}).get('model') or '(未传, pi 缺省)'}({(plan.model or {}).get('source')}){session}")
        if failure:
            outcome = "record_failed"
            step.ok, step.message = False, f"拉起事件写不进 {plan.card} session record({failure}), 已终止进程组; 拉起须留痕(fail-closed)"
            say(f"[{index}] exit 4 — {step.message}")
            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), step.message
            result.reason = "launch_record_failed"
            return {"outcome": "record_failed"}
        running = harness

        def _pump(seconds: float) -> None:  # 唯一哨兵的 sleeper: 读拉起进程输出(select 事件驱动), 再按节流落运行记录
            running.pump(seconds)
            recorder.flush()

        watch_out = io.StringIO()
        with contextlib.redirect_stdout(watch_out):
            rc = watch(watch_args, expect_ready=ready, stop_when=harness.exited, sleeper=_pump)
        step.exit_code, step.output = int(rc), watch_out.getvalue().strip()
        if rc == declared_exit_code("lybra_agent_watch", "change"):  # AIPOS-F101 件③: 哨兵退出码读声明
            outcome = "artifact_ready"
            harness.pump(float(decl["grace_seconds"]))  # 宽限期内自行退出即收尾(输出照常汇总)
            detail = "宽限期内自行退出" if harness.exited() else f"宽限 {decl['grace_seconds']}s 后终止进程组"
            step.ok, step.message = True, f"产物就绪(拉起的 {plan.harness} 进程: {detail})"
            say(f"[{index}] ready: {step.output}")
            return {"outcome": outcome}
        if harness.exited():
            outcome = "early_exit"
            harness.pump(terminate_wait)  # 进程已退出: 读尽管道余量(EOF 即返回)再取 stderr 末尾
            return {"outcome": outcome, "returncode": harness.proc.returncode, "watch_rc": rc, "stderr_tail": list(harness.stderr_tail)}
        outcome = "timeout"
        return {"outcome": outcome, "returncode": None, "watch_rc": rc, "stderr_tail": []}
    except LoopInterrupted as exc:
        if harness is not None:
            outcome = f"interrupted(signal {exc.signum})"
            step.ok, step.message = False, f"loop 收到信号 {exc.signum}: 已终止拉起的 {plan.harness} 进程组"
            say(f"[{index}] interrupted — {step.message}")
        raise
    finally:
        if harness is not None:
            with _signals_deferred() as pending:  # 清进程组与收尾留痕期间不被信号打断; 期间到的信号清完再按 LoopInterrupted 抛
                how = harness.terminate_group(terminate_wait)
                harness.close()
                say(harness.counts_line())
                step.launch = {**(step.launch or {}), "outcome": outcome, "returncode": harness.proc.returncode, "termination": how}
                recorder.launch_ended(outcome, harness.proc.returncode, how)
                failure = _session_event(governance_root, plan.card, actor, "harness_exit",
                                         f"harness={plan.harness}; pid={harness.pid}; outcome={outcome}; returncode={harness.proc.returncode}; "
                                         f"termination={how}{session}")
                if failure:
                    say(f"[{index}] warning — 收尾事件写不进 {plan.card} session record: {failure}")
                    step.launch["exit_event_error"] = failure
            if pending:
                raise LoopInterrupted(pending[0])


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
    no_launch: bool = False,
    deploy_guard: Any | None = None,
    wait_reexec: "WaitReexec | None" = None,
    continues_run: str | None = None,
) -> LoopResult:
    """有界推进一张卡。返回 LoopResult(exit_code 按 verbs.schema lybra_loop.exit_codes)。

    derive/execute/watch 可注入(靶场用), 缺省 = 产品唯一实现。
    AIPOS-F95: no_launch=True = 不拉起 harness(手工模式); 拉起等待期间收到 SIGINT/SIGTERM/SIGHUP → 清进程组后抛 LoopInterrupted。
    AIPOS-F152 件①: deploy_guard 缺省 = 本进程的 deploy_runtime.DeployGuard(运行代码非部署快照 = 不适用, 行为不变); 靶场可注入。
    AIPOS-F166 件①: wait_reexec = 等待步遇部署更新的续等口(CLI 注入 exec_into_snapshot; None = 原 deploy_changed 行为);
    continues_run = 续等关联的上一运行 run_id(re-exec 来的新进程由 --continues-run 带入, 落运行记录 continues_run)。
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

    # AIPOS-F107 件①: 输出端断开(ssh 断线/管道关闭)→ 静默丢弃, 不打断拉起期清理; AIPOS-F131 件③: 记录建好后同写日志
    say = _RunOutput(TolerantOutput(out))

    # AIPOS-F122 件③: 存量冻结卡拒推进(判定唯一实现 legacy_baseline.frozen_tasks, 经推导核同一硬停项 legacy_frozen_stop;
    # 先于找卡与信封校验——冻结卡不该得到「无信封 exit 5」之类误导出口)
    from tools.aipos_cli.next_resolver import legacy_frozen_stop

    frozen_stop = legacy_frozen_stop(governance_root, task_id)
    if frozen_stop is not None:
        msg = "; ".join(frozen_stop["missing_records"])
        say(f"lybra loop {task_id}: exit {exit_code_for(contract, 'not_derivable')} — {msg}")
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"), msg,
                          missing_records=frozen_stop["missing_records"], suggested_action=frozen_stop["suggested_action"])

    # 卡存在性
    task_path, _queue_dir = _find_task_in_queue(governance_root, task_id)
    if not task_path:
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"),
                          f"queue 目录中找不到任务卡 {task_id}", missing_records=[f"任务卡 {task_id}"])
    try:
        task_fm = _read_frontmatter(task_path)
    except FrontmatterReadError as exc:
        # AIPOS-F100 件②: 卡读不出 = 硬停 exit 4 点名文件与出口(不据空卡面找信封/推导)
        stop = frontmatter_unreadable_stop(task_id, exc)
        say(f"lybra loop {task_id}: exit 4 — {exc}")
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"), str(exc),
                          missing_records=stop["missing_records"], suggested_action=stop["suggested_action"])
    # AIPOS-F78 前置零②: 驱动方身份=显式 --actor → 工位声明(.lybra/role instance)→ connection.json 驱动方 token 实例; 禁占位 advisor
    driver_actor = actor or _driver_actor(governance_root, connection_json=connection_json)
    if not driver_actor:
        msg = f"驱动方身份解析不到: 缺 {DRIVER_ACTOR_MISSING}(或显式 --actor)"
        say(f"lybra loop {task_id}: exit 4 — {msg}")
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"), msg,
                          missing_records=[DRIVER_ACTOR_MISSING])

    # AIPOS-F134 件①: 驱动方实例须有自己的凭据(按实例挑, 禁回落连接文件里首个驱动方 token——同一治理根多顾问实例时会冒名)
    from tools.aipos_cli.next_resolver import driver_token_refusal

    token_refusal = driver_token_refusal(governance_root, driver_actor, connection_json)
    if token_refusal:
        msg = f"驱动方 {driver_actor} 凭据不可用, 拒跑: {token_refusal}"
        say(f"lybra loop {task_id}: exit 4 — {msg}")
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"), msg,
                          missing_records=[f"驱动方实例 {driver_actor} 的凭据条目(connection.json tokens[].agent_instance)"])

    # AIPOS-F131 件①③: 运行记录 + 日志(落点 project.json paths.loop_runs_root)。建不起来 = 拒跑 exit 4(运行须可查, 不裸跑)
    from tools.schema_loader import SchemaLoadError

    try:
        recorder = LoopRunRecorder(governance_root, task_id, driver=driver_actor, contract=contract, warn=say)
    except (LoopRunRecordError, SchemaLoadError) as exc:
        msg = f"loop 运行记录建不起来, 拒跑(运行须可查, fail-closed; 落点声明 project.json paths.loop_runs_root): {exc}"
        say(f"lybra loop {task_id}: exit 4 — {msg}")
        return LoopResult(task_id, "not_derivable", exit_code_for(contract, "not_derivable"), msg, missing_records=[msg])
    say.recorder = recorder
    say(f"lybra loop {task_id}: run_record={recorder.path} log={recorder.log_path}(进度: lybra loop status --task-id {task_id})")
    refused = _single_instance_refusal(task_id, governance_root, recorder, contract=contract, say=say)
    if refused is not None:  # AIPOS-F171B 件①: 同卡已有存活 loop = 拒(未推进任何步)
        return refused
    if continues_run:  # AIPOS-F166 件①: 等待步遇部署更新 re-exec 而来, 与上一运行记录明确关联
        recorder.set_continues(str(continues_run))
        say(f"lybra loop {task_id}: 续等(AIPOS-F166): 接上一运行 {continues_run}(等待中部署已更新, 已 re-exec 到本部署快照, "
            "从同一等待步续等; 已落的步不重复)")
    # AIPOS-F152 件①: 记下本次运行的代码(部署快照 + 提交), 之后每步比对 current
    from tools.aipos_cli.deploy_runtime import DeployGuard

    guard = deploy_guard if deploy_guard is not None else DeployGuard.for_running_process()
    recorder.set_deploy(guard.describe())
    if guard.applicable:
        say(f"lybra loop {task_id}: 运行部署快照 {guard.running.release}(commit {guard.baseline}); 每步比对 "
            f"{guard.running.describe()['deploy_current']}, 部署一变即停下(deploy_changed)"
            + ("; 手工等待步中部署一变 = 换新快照从同一等待步续等(re-exec, AIPOS-F166)" if wait_reexec is not None else ""))
    holder = LoopResult(task_id, "completed", exit_code_for(contract, "completed"), "")
    try:
        result = _run_enveloped(task_id, governance_root, holder, task_fm=task_fm, driver_actor=driver_actor, policy_id=policy_id,
                                now=now, connection_json=connection_json, contract=contract, max_steps=max_steps, max_wait=max_wait,
                                interval=interval, allowed_verbs=allowed_verbs, say=say, derive=derive, execute=execute,
                                watch=watch, no_launch=no_launch, recorder=recorder, guard=guard, wait_reexec=wait_reexec)
    except LoopInterrupted as exc:
        recorder.end(outcome="interrupted", exit_code=128 + exc.signum, reason="interrupted", message=f"收到信号 {exc.signum}",
                     steps=holder.steps)
        raise
    except BaseException as exc:  # 记下崩溃原因后原样抛出(不吞)
        recorder.end(outcome="crashed", exit_code=1, reason="crashed", message=f"{type(exc).__name__}: {exc}", steps=holder.steps)
        raise
    recorder.end(outcome=result.outcome, exit_code=result.exit_code, reason=result.reason or result.outcome, message=result.message,
                 steps=result.steps)
    result.run_record, result.run_log = str(recorder.path), str(recorder.log_path)
    if result.exit_code == exit_code_for(contract, "completed"):
        # AIPOS-F147 件①: 出口 0 = 已结案且已落账, 但本次运行记录的终态写在落账之后——补一次落账(N6 同一提交口, 范围已落账 = no-op);
        # 失败不改 loop 结果(推进已完成), 结果行如实输出; 该记录(已结束)在本卡落账判据内, lint / 下次 loop 会再点名
        import subprocess

        from tools.aipos_cli.governance_commit import reland_after_write

        try:
            say(reland_after_write(governance_root, task_id, driver_actor, what="loop 运行记录终态")["line"])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            say(f"补落账: {task_id} loop 运行记录终态 失败({type(exc).__name__}: {exc}); 出口: lybra loop --task-id {task_id}(推导核派生 N6 落账步)")
    return result


def _single_instance_refusal(task_id: str, governance_root: Path, recorder: LoopRunRecorder, *, contract: dict[str, Any],
                             say: Callable[[str], None]) -> LoopResult | None:
    """AIPOS-F171B 件①: 同卡单实例(loop 启动唯一口; 判定唯一实现 loop_run_record.live_run_conflict, 与 lybra loop status 同一 judge_run)。
    本运行记录已落盘后判: 先于本运行的同卡 loop 仍存活 = 本运行以 single_instance.end_reason 结束(exit_codes 同名), 不推进任何步;
    运行记录读不出 = 无从判定, 拒跑(not_derivable, fail-closed)。None = 可继续。"""
    from tools.aipos_cli.loop_run_record import live_run_conflict, single_instance_declaration

    end_reason = str(single_instance_declaration(contract)["end_reason"])
    status_cmd = f"lybra loop status --task-id {task_id} --workspace-root {governance_root}"
    try:
        conflict = live_run_conflict(governance_root, task_id, own=recorder.meta, contract=contract)
    except LoopRunRecordError as exc:
        msg = f"本卡运行记录读不出, 无从判定是否已有 loop 在跑, 拒跑(fail-closed): {exc}"
        code = exit_code_for(contract, "not_derivable")
        say(f"lybra loop {task_id}: exit {code} — {msg}")
        recorder.end(outcome="not_derivable", exit_code=code, reason="not_derivable", message=msg)
        return LoopResult(task_id, "not_derivable", code, msg, missing_records=[msg], reason="not_derivable",
                          run_record=str(recorder.path), run_log=str(recorder.log_path))
    if conflict is None:
        return None
    code = exit_code_for(contract, end_reason)
    say(f"lybra loop {task_id}: exit {code} — {conflict['message']}")
    recorder.end(outcome=end_reason, exit_code=code, reason=end_reason, message=conflict["message"])
    return LoopResult(task_id, end_reason, code, conflict["message"], suggested_action=status_cmd, reason=end_reason,
                      run_record=str(recorder.path), run_log=str(recorder.log_path))


def _run_enveloped(task_id: str, governance_root: Path, result: LoopResult, *, task_fm: dict[str, Any], driver_actor: str,
                   policy_id: str | None, now: datetime | None, connection_json: str | None, contract: dict[str, Any],
                   max_steps: int, max_wait: float, interval: float, allowed_verbs: set[str], say: _RunOutput,
                   derive: Callable[[str, Path], dict[str, Any]], execute: Callable[..., dict[str, Any]],
                   watch: Callable[..., int], no_launch: bool, recorder: LoopRunRecorder, guard: Any,
                   wait_reexec: "WaitReexec | None" = None) -> LoopResult:
    # 件③ 信封(启动前校验; 无信封 exit 5 带申领出口, 禁裸跑); 身份集合含工位角色名(chris: hbj-advisor)
    # AIPOS-F134 件①: 驱动方角色名按本次驱动方实例取(其凭据条目的 role), 故在 driver_scope 内解析(否则取的是工位声明/首个驱动方)
    with driver_scope(actor=driver_actor, policy_id=policy_id):
        driver_role = _driver_role_name(governance_root, connection_json)
    policy, reasons = find_envelope(
        governance_root, task_id=task_id, task_fm=task_fm, driver_actor=driver_actor, policy_id=policy_id, now=now,
        driver_role=driver_role,
    )
    if policy is None and policy_id:
        # AIPOS-F166 件②: 显式 --envelope(续跑命令一律带)的信封额度用尽 / 已过期 = 专门出口(用量/上限/到期 + 续签命令), 不给泛化申领出口
        ending = _envelope_ending(governance_root, policy_id, task_id=task_id, task_fm=task_fm, driver_actor=driver_actor,
                                  driver_role=driver_role, what="loop 启动校验不过, 未推进任何步", now=now)
        if ending is not None:
            recorder.set_envelope(str(policy_id))
            recorder.set_envelope_state(ending["state"])
            say(f"lybra loop {task_id}: exit {exit_code_for(contract, 'no_envelope')} — {ending['message']}")
            return LoopResult(task_id, "no_envelope", exit_code_for(contract, "no_envelope"), ending["message"], envelope=str(policy_id),
                              suggested_action=ending["suggested_action"], reason=ending["reason"])
    if policy is None:
        hint = mint_hint(task_id=task_id, task_fm=task_fm, driver_actor=driver_actor, now=now, governance_root=governance_root)
        msg = "无有效 autonomy 信封, 拒绝裸跑。\n" + "\n".join(f"  - {r}" for r in reasons) + f"\n申领出口(Owner 亲自敲):\n  {hint}"
        say(f"lybra loop {task_id}: exit 5 — {msg}")
        return LoopResult(task_id, "no_envelope", exit_code_for(contract, "no_envelope"), msg, suggested_action=hint)
    envelope_id = str(policy.get("policy_id"))
    recorder.set_envelope(envelope_id)
    say(f"lybra loop {task_id}: envelope={envelope_id} driver={driver_actor} max_steps={max_steps} max_wait={max_wait}s")

    result.envelope = envelope_id
    from tools.aipos_cli.machine_zone import card_owner_refusal

    refusal = card_owner_refusal(governance_root, task_id, task_fm, driver_actor=driver_actor, policy=policy)
    if refusal:  # AIPOS-F156 件②(gap #140): 卡归属 ≠ 驱动方且非总顾问 = 拒(防跑走别人的卡; 例行清单之外, 该停)
        say(f"lybra loop {task_id}: exit {exit_code_for(contract, 'not_derivable')} — {refusal}")
        result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), refusal
        result.reason = "card_owner_mismatch"
        return result
    # AIPOS-F90 件①: 已校验的驱动方身份与信封贯穿推导核与执行体(派生 claim/return/verdict/close 带同一 owner_policy_ref)
    with driver_scope(actor=driver_actor, policy_id=envelope_id):
        try:
            return _drive(task_id, governance_root, result, contract=contract, max_steps=max_steps, max_wait=max_wait,
                          interval=interval, allowed_verbs=allowed_verbs, say=say, derive=derive, execute=execute, watch=watch,
                          connection_json=connection_json, policy=policy, driver_actor=driver_actor, no_launch=no_launch,
                          recorder=recorder, guard=guard, driver_role=driver_role, task_fm=task_fm, now=now, wait_reexec=wait_reexec)
        except FrontmatterReadError as exc:
            # AIPOS-F100 件②: 推导核之外的回读(落账回读 / 结案判据 / 等待实例)遇记录读不出 = 同一硬停 exit 4 点名文件
            stop = frontmatter_unreadable_stop(task_id, exc)
            say(f"lybra loop {task_id}: exit 4 — {exc}")
            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), str(exc)
            result.missing_records, result.suggested_action = stop["missing_records"], stop["suggested_action"]
            result.reason = "frontmatter_unreadable"
            return result


# AIPOS-F90 件①(缺陷③): 账务步「门侧是否已落」的回读判据——该步对应的门生记录(_read_task_records 唯一读取口)在执行前后是否换了一份。
# verdict 记录挂被审卡(audit_verdicts/<被审卡>), 其余挂目标卡; finalize 不在此表(无回读 = 按门拒处理)。
_LANDED_RECORD = {"claim": "latest_claim", "return": "latest_return", "dispatch": "latest_audit_dispatch",
                  "verdict": "latest_verdict", "close": "latest_closure"}


def _record_card(action_type: str, governance_root: Path, target_card: str) -> str:
    """verdict 记录挂被审卡: 被审卡号 = 审计卡卡面 reviewed_task_id(AIPOS-F112: audit_derivation.audit_card_reviewed_id 唯一口,
    认得复审轮 R2/R3…; 原按号尾去一位, 对 R2 取出 <ID>R)。"""
    if action_type != "verdict":
        return target_card
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.task_loader import find_task_card

    path, _state = find_task_card(governance_root, target_card)
    fm = _read_frontmatter(path) if path else {}
    return audit_card_reviewed_id(target_card, fm) if is_audit_card(target_card, fm) else target_card


def _landed_record(governance_root: Path, action_type: str, target_card: str) -> dict[str, Any] | None:
    key = _LANDED_RECORD.get(action_type)
    if not key:
        return None
    return _read_task_records(governance_root, _record_card(action_type, governance_root, target_card)).get(key)


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
    policy: dict[str, Any] | None = None,
    driver_actor: str = "",
    no_launch: bool = False,
    recorder: LoopRunRecorder,
    guard: Any = None,
    driver_role: str | None = None,
    task_fm: dict[str, Any] | None = None,
    now: datetime | None = None,
    wait_reexec: "WaitReexec | None" = None,
) -> LoopResult:
    steps = result.steps
    launch_decl = launch_declaration(contract)
    launched: set[str] = set()  # AIPOS-F95 件③④: 一次 loop 运行对一张卡至多拉起一次
    # AIPOS-F162 件①: 运行证据拒收补做——同一运行内每张审计卡被拒收过的报告指纹(拉起次数上限读声明 runtime_evidence.loop.relaunch_cap)
    evidence_rejected: dict[str, set[str]] = {}
    evidence_loop = _runtime_evidence_loop_declaration()
    from tools.aipos_cli.governance_commit import n6_landing_declaration

    landing_action = str(n6_landing_declaration()["action_type"])  # AIPOS-F94: N6 落账步(声明 transitions nodes.N6.landing)

    for index in range(1, max_steps + 1):
        recorder.sync_steps(steps)  # AIPOS-F131 件①: 上一轮落定的步立即进运行记录
        # AIPOS-F152 件①: 每步推导前比对部署——本进程跑的快照已不是现部署 = 步间干净停下(不执行下一步), 重跑从断点续做
        change = guard.check() if guard is not None else None
        if change:
            return _deploy_changed_end(index, change, result, contract=contract, say=say, recorder=recorder)
        # 出口 0: closure 记录存在且治理已落账(AIPOS-F94 件①: 本卡落账范围已提交且已推送); 未落账 → 推导核派生 N6 落账步
        # AIPOS-F162 件②: 已结案卡有在途补充审计 = 未完(推导核派 await_artifact 等补审卡), 结案出口不成立
        if _has_closure(governance_root, task_id) and not _supplementary_in_flight(governance_root, task_id):
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
        launch_card: str | None = None  # AIPOS-F95: 执行体(N1)/审计体(N3)等待才可拉起; external finalize 等待不拉起
        repo_turn_wait = False  # AIPOS-F135 件①: external 同仓串行等待(就绪 = 本卡不再 await_repo_turn)
        rework_note = ""  # AIPOS-F155 件②: 等返工交回时附在手工等待提示后的返工节要点(推导核 suggested_action)
        evidence_relaunch = False  # AIPOS-F162 件①: 本次等待 = 审计报告缺独立运行证据被拒收, 让审计体补做(可再拉起)
        evidence_report = ""  # 被拒收的那份报告(推导核 action.path), 等待起点指纹的依据

        if not derivation.get("derivable"):
            action = derivation.get("action") or {}
            if action.get("type") == "await_artifact" and action.get("card") and action.get("kind") == "finalize_return":
                # AIPOS-F78B 件②: finalize_mode=external, N4 PASS 后等外部 FINALIZE 卡的 Return(落点读项目声明, 与执行体 Return 同候选);
                # 就绪 = 本卡重推导可推导(派生 artifact ingest 铸 finalization 记录)或硬停(Return 不合规)
                target_card = str(action["card"])
                watch_root, wait_patterns = executor_artifact_watch(governance_root, target_card)
            elif action.get("type") == "await_repo_turn" and action.get("card"):
                # AIPOS-F135 件①: external 同仓合入串行——同 lane 仓先合入卡未落 finalization 记录, 不派本卡 FINALIZE;
                # 等该卡 finalization 记录落盘(不拉起任何 harness), 醒来重推导本卡
                target_card = str(action["card"])
                watch_root, wait_patterns = repo_turn_watch(governance_root, target_card)
                repo_turn_wait = True
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
                    launch_card = audit_card
                    if audit_action.get("type") == evidence_loop["derivation_action"]:
                        # AIPOS-F162 件①: 报告被运行证据硬门拒收 = 审计产物不合格 → 让审计体补做(拒因附开工/手工提示); 同一运行内
                        # 对同一审计卡因此拉起超过声明上限 = 结束(runtime_evidence_exhausted, loop status 判 owner_needed = 连败上限)
                        seen = evidence_rejected.setdefault(audit_card, set())
                        seen.add(_report_fingerprint(audit_action.get("path")))
                        rework_note = str(audit_derivation.get("suggested_action") or "")
                        if len(seen) > int(evidence_loop["relaunch_cap"]):
                            missing = list(audit_derivation.get("missing_records") or [])
                            msg = (f"审计卡 {audit_card} 报告缺独立运行证据被拒收, 本次运行已让审计体补做 {evidence_loop['relaunch_cap']} 次仍不合格"
                                   f"(声明 relaunch_cap): missing={missing}")
                            steps.append(LoopStep(index, "execute", audit_derivation.get("current_node"), audit_derivation.get("current_state"),
                                                  audit_card, ok=False, message=msg))
                            say(f"[{index}] exit 4 — {msg}")
                            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), msg
                            result.missing_records, result.suggested_action = missing, rework_note
                            result.reason = str(evidence_loop["exhausted_end_reason"])
                            return result
                        evidence_relaunch, evidence_report = True, str(audit_action.get("path") or "")
            node = derivation.get("current_node")
            state = derivation.get("current_state")
            if action.get("type") in HARD_STOP_ACTIONS:
                # AIPOS-F78 件③ / F73E 件①: 产物已落盘但不合规 / 账务记录缺 → 不空等, exit 4 点名缺项(补齐后重跑 loop)
                missing = list(derivation.get("missing_records") or [])
                what = {"artifact_invalid": "产物不合规", "frontmatter_unreadable": "frontmatter 读不出"}.get(str(action.get("type")), "记录缺失")
                where = f": {action.get('path')}" if action.get("path") else ""
                msg = f"{what} @ {node}/{state} ({target_card}){where}: missing={missing}; suggested={derivation.get('suggested_action', '')}"
                steps.append(LoopStep(index, "execute", node, state, target_card, ok=False, message=msg))
                say(f"[{index}] exit 4 — {msg}")
                result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), msg
                result.missing_records, result.suggested_action = missing, str(derivation.get("suggested_action") or "")
                result.reason = str(action.get("type"))
                return result
            if derivation.get("derivable") or wait_patterns:
                pass  # 审计卡可推导 → 下方账务步; 已定等待 → 下方 watch
            elif (node == "claim" and state == "claimed") or action.get("type") == "await_rework":
                # N1→N2: 执行体在干活 → 等 Return 落盘(落点读项目声明; 骨架不算, 判据=推导核)
                # AIPOS-F155 件②: N4 非 PASS 裁决已登记返工轮(推导核 await_rework)= 同一执行体等待(等返工修复后的新交回), 不以 not_derivable 结束
                watch_root, wait_patterns = executor_artifact_watch(governance_root, task_id)
                launch_card = task_id
                rework_note = str(derivation.get("suggested_action") or "") if action.get("type") == "await_rework" else ""
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

            start_report = _report_fingerprint(evidence_report) if evidence_relaunch else ""

            def _ready(_matched: list[str], _card: str = ready_card, _turn: bool = repo_turn_wait, _start: str = start_report) -> bool:
                # 就绪 = 推导核可推导; 或硬停(产物不合规 F78 件③ / 记录缺 F73E 件①)——都该让 loop 醒来判定, 而非空等到超时
                d = derive(_card, governance_root)
                if _turn and (d.get("action") or {}).get("type") != "await_repo_turn":
                    return True  # AIPOS-F135 件①: 同仓轮到本卡(先合入卡 finalization 已落)
                if (d.get("action") or {}).get("type") == evidence_loop["derivation_action"]:
                    # AIPOS-F162 件①: 审计报告(改写后)仍缺独立运行证据 = 醒来重判(补做计数), 未改写不算(防同一份被拒报告空转)
                    return _report_fingerprint((d.get("action") or {}).get("path")) != _start
                # AIPOS-F114: 等待中本轮作废(所审交回过期 / 被下一轮取代)也醒来——重推导被审卡派生重交回, 不空等到超时
                return (bool(d.get("derivable")) or (d.get("action") or {}).get("type") in HARD_STOP_ACTIONS
                        or isinstance(d.get("return_stale"), dict) or isinstance(d.get("superseded_by"), dict))

            plan: LaunchPlan | None = None
            if launch_card is not None:
                plan = plan_launch(governance_root, launch_card, policy=policy, no_launch=no_launch, decl=launch_decl,
                                   already_launched=launched - {launch_card} if evidence_relaunch else launched)
                step.launch = plan.to_dict()
                if plan.refusal and rework_note:
                    plan.manual_hint = f"{plan.manual_hint}。返工: {rework_note}"  # AIPOS-F155 件②: 手工提示附返工节要点
                if evidence_relaunch and not plan.refusal and rework_note:
                    plan.append_kickoff_note(f"返工(审计报告被拒收, 须补做): {rework_note}")  # AIPOS-F162 件①: 拉起的审计体看得到拒因
                if plan.refusal:
                    say(f"[{index}] manual: {plan.manual_hint}(未拉起: {plan.refusal})")
            # AIPOS-F131 件① / F136 件①: 等待步进运行记录(拉起判定之后: 记开工方式, 手工模式附开工提示, 供 loop status 给顾问下一动作)
            launching = plan is not None and not plan.refusal
            recorder.step_started(index, "wait", step.node, step.state, target_card, waiting_for=wait_patterns,
                                  mode="launch" if launching else "manual",
                                  hint=None if launching or plan is None else plan.manual_hint)
            watch_args = _watch_args(watch_root, wait_patterns, max_wait=max_wait, interval=interval)
            if plan is not None and not plan.refusal:
                launched.add(launch_card)
                return_now = _launched_wait(index, step, plan, governance_root, result, contract=contract, decl=launch_decl,
                                            watch=watch, watch_args=watch_args, ready=_ready, say=say, actor=driver_actor,
                                            envelope_id=str((policy or {}).get("policy_id") or ""), wait_patterns=wait_patterns,
                                            recorder=recorder)
                steps.append(step)
                if return_now:
                    return result
                continue
            watch_out = io.StringIO()
            # AIPOS-F152 件①: 手工等待期每轮比对部署(唯一等待原语的 deploy_guard); 不适用时不传(注入的靶场 watch 签名不变)
            guard_kw = {"deploy_guard": guard} if guard is not None and guard.applicable else {}
            with contextlib.redirect_stdout(watch_out):
                rc = watch(watch_args, expect_ready=_ready, **guard_kw)
            step.exit_code, step.output = int(rc), watch_out.getvalue().strip()
            if guard_kw and rc == declared_exit_code("lybra_agent_watch", "deploy_changed"):
                change = guard.check()
                if change:
                    step.ok, step.message = False, "等待中部署已更新"
                    steps.append(step)
                    note = ""
                    if wait_reexec is not None:
                        # AIPOS-F166 件①: 等待步无副作用 → re-exec 到新快照从同一等待步续等(成功不返回: 本进程已被替换)
                        recorder.sync_steps(steps)
                        note = _wait_reexec(index, change, guard, wait_reexec, recorder=recorder, say=say, args=_reexec_args(
                            task_id, governance_root, driver_actor=driver_actor, envelope_id=str((policy or {}).get("policy_id") or ""),
                            remaining=max_steps - index, max_wait=max_wait, interval=interval, no_launch=no_launch,
                            connection_json=connection_json, run_id=recorder.run_id))
                    return _deploy_changed_end(index, change, result, contract=contract, say=say, recorder=recorder, note=note)
            if rc != declared_exit_code("lybra_agent_watch", "change"):  # AIPOS-F101 件③: 哨兵退出码读声明
                step.ok = False
                step.message = f"等待产物超时/停滞(watch exit {rc}): 等的是 {wait_patterns}"
                steps.append(step)
                say(f"[{index}] exit 3 — {step.message}")
                result.outcome, result.exit_code, result.message = "wait_timeout", exit_code_for(contract, "wait_timeout"), step.message
                result.reason = "wait_timeout"
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
            result.reason = "manual_required"
            return result
        if action_type not in allowed_verbs:
            step.ok, step.message = False, f"信封未授权动词 {action_type}(允许: {sorted(allowed_verbs)})"
            steps.append(step)
            say(f"[{index}] exit 5 — {step.message}")
            result.outcome, result.exit_code, result.message = "no_envelope", exit_code_for(contract, "no_envelope"), step.message
            result.reason = "verb_not_authorized"
            return result
        parses, parse_error = check_command_parses(command)
        if not parses:
            step.ok, step.message = False, f"派生命令解析失败, 禁执行: {parse_error}"
            steps.append(step)
            say(f"[{index}] exit 4 — {step.message}\n  command: {command}")
            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), step.message
            result.reason = "command_unparseable"
            return result

        recorder.step_started(index, "execute", node, state, target_card, action=action_type)
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
            what = "落账拒" if action_type == landing_action else "门拒"
            msg = f"{what} @ {action_type} ({target_card}) exit {step.exit_code}: {step.message}\n{step.output}".rstrip()
            if _is_envelope_rejection(step, contract):
                # AIPOS-F166 件②(gap #152): 拒因属信封且门侧用量真相 = 用尽 / 过期 → 专门出口(不再泛化 gate_rejected)
                ending = _envelope_ending(governance_root, str((policy or {}).get("policy_id") or ""), task_id=task_id,
                                          task_fm=task_fm or {}, driver_actor=driver_actor, driver_role=driver_role,
                                          what=f"{action_type} 步({target_card})被拒, 未执行", now=now)
                if ending is not None:
                    recorder.set_envelope_state(ending["state"])
                    say(f"[{index}] exit {exit_code_for(contract, 'no_envelope')} — {ending['message']}")
                    result.outcome, result.exit_code = "no_envelope", exit_code_for(contract, "no_envelope")
                    result.message = f"{ending['message']}\n  门拒原文: {msg}"
                    result.suggested_action, result.reason = ending["suggested_action"], ending["reason"]
                    return result
            say(f"[{index}] exit 2 — {msg}")
            result.outcome, result.exit_code, result.message = "gate_rejected", exit_code_for(contract, "gate_rejected"), msg
            result.reason = "landing_rejected" if action_type == landing_action else "gate_rejected"
            return result
        say(f"[{index}] ok: {step.message}")
        if action_type == "finalize":
            # AIPOS-F118 件②: finalize 步输出合并后回归检查结果一行(行首/取行唯一出处 post_merge_regression)
            from tools.aipos_cli.post_merge_regression import result_line

            regression_line = result_line(step.output)
            if regression_line:
                say(f"[{index}] {regression_line}")
            if guard is not None:  # AIPOS-F155 件③: 本 loop 自身 finalize 的部署不算外部更新(并入 F152 DeployGuard)
                _adopt_own_deploy(index, guard, governance_root, target_card, say=say, recorder=recorder)
        if action_type == landing_action:
            # AIPOS-F94 件①: 落账步报成功后以判据复核(已提交且已推送); 仍未落账 = 不重试, exit 4 点名缺什么
            landing = _landing(governance_root, target_card)
            if landing is None or not landing.get("landed"):
                reason = (landing or {}).get("reason") or "落账判据读取失败"
                msg = f"落账步报成功但判据仍未落账 ({target_card}): {reason}"
                say(f"[{index}] exit 4 — {msg}")
                result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), msg
                result.missing_records = [reason]
                result.reason = "landing_unverified"
                return result

    msg = f"--max-steps {max_steps} 用尽, 未走到 completed"
    say(f"exit 3 — {msg}")
    result.outcome, result.exit_code, result.message = "wait_timeout", exit_code_for(contract, "wait_timeout"), msg
    result.reason = "max_steps_exhausted"
    return result


def _adopt_own_deploy(index: int, guard: Any, governance_root: Path, task_id: str, *, say: Callable[[str], None],
                      recorder: LoopRunRecorder) -> None:
    """AIPOS-F155 件③(gap #136): 本 loop 的 finalize 步成功后, 以本卡 finalization 记录的合入提交(merge_commit, 推导核派 close 同一字段;
    记录唯一判据 finalization_record.existing_finalization_records)让 DeployGuard 采纳本进程自己引起的部署(DeployGuard.adopt_own_deploy
    唯一实现); 部署链接未指向该提交(未部署 / 部署失败 / 期间他人部署)= 基线不动, 下一步照常比对(非本 loop 引起的变化仍 deploy_changed)。"""
    if not getattr(guard, "applicable", False):
        return
    from tools.aipos_cli.deploy_runtime import short
    from tools.aipos_cli.finalization_record import existing_finalization_records

    records = existing_finalization_records(governance_root, task_id)
    fm = _read_frontmatter(records[0]) if records else {}
    merge_commit = str(fm.get("merge_commit") or fm.get("commit") or "").strip()
    adopted = guard.adopt_own_deploy(merge_commit) if merge_commit else None
    if adopted is None:
        return
    recorder.deploy_adopted(adopted)
    say(f"[{index}] 本 loop finalize 已部署 commit {short(adopted['to'])}(本卡合入提交): 部署比对基线 {short(adopted['from'])} → "
        f"{short(adopted['to'])}, 自身部署不触发 deploy_changed, 继续收尾(收尾仍跑本进程快照 {adopted.get('running_release')} 的代码, "
        "本卡新代码下次 loop 运行生效)")


def _deploy_changed_end(index: int, change: dict[str, Any], result: LoopResult, *, contract: dict[str, Any],
                        say: Callable[[str], None], recorder: LoopRunRecorder, note: str = "") -> LoopResult:
    """AIPOS-F152 件①: 部署已更新 → end_reason deploy_changed(exit_codes.deploy_changed), 运行记录 deploy 段写新旧提交。
    AIPOS-F166 件①: note = 等待步续等不可行的原因(新快照不可用 / 余步为 0 / exec 失败), 附在结束原文末。"""
    from tools.aipos_cli.deploy_runtime import change_commits

    code = exit_code_for(contract, "deploy_changed")
    msg = (f"部署已更新({change_commits(change)}), 本 loop 跑的是旧版本代码, 已在步间停下(未执行下一步): "
           f"用原命令重跑 loop 从断点续做(已落的步不重复)。快照 {change.get('running_release')}, 部署链接 {change.get('deploy_current')}"
           + (f"。等待步续等未成(AIPOS-F166): {note}" if note else ""))
    recorder.deploy_changed(change)
    say(f"[{index}] exit {code} — {msg}")
    result.outcome, result.exit_code, result.message, result.reason = "deploy_changed", code, msg, "deploy_changed"
    return result


def _is_envelope_rejection(step: LoopStep, contract: dict[str, Any]) -> bool:
    """AIPOS-F166 件②: 账务步拒因属信封——退出码 = exit_codes.no_envelope(派生命令无覆盖驱动方的有效信封 / 薄壳一阶段被信封门拒,
    AIPOS-F78B 件③), 或拒因原文含 transitions envelope_guards 声明的 ENVELOPE_* 码(autonomy_policy.envelope_guard_codes)。"""
    from tools.aipos_cli.autonomy_policy import envelope_guard_codes

    if int(step.exit_code) == exit_code_for(contract, "no_envelope"):
        return True
    text = f"{step.message}\n{step.output}"
    return any(code in text for code in envelope_guard_codes())


# ---------------------------------------------------------------------------
# AIPOS-F166 件①(gap #151): 等待步遇部署更新 → re-exec 到新部署快照, 从同一等待步续等(声明 verbs.schema
# lybra_loop.deploy_consistency.wait_reexec)。判定 = F152 DeployGuard(check / current_snapshot); 换代码 = F143 子进程新代码机制
# workstation_wiring.lybra_cli_invocation(同一解释器 + 代码树根, 代码树根 = 新快照), os.execve 替换本进程(同一 pid)。
# ---------------------------------------------------------------------------

@dataclass
class WaitReexec:
    """续等口: launcher(参数, 新快照根) 以新快照代码替换本进程(成功不返回; 失败抛 OSError / RuntimeError);
    extra_args = CLI 层旗标(--json / --verbose), 原样带给新进程。"""

    launcher: Callable[[list[str], Path], None]
    extra_args: list[str] = field(default_factory=list)


def _reexec_args(task_id: str, governance_root: Path, *, driver_actor: str, envelope_id: str, remaining: int, max_wait: float,
                 interval: float, no_launch: bool, connection_json: str | None, run_id: str) -> list[str]:
    """新进程的 `lybra loop` 参数: 原参数原样(路径一律绝对: 新进程 cwd = 新快照根), --max-steps = 余步, --continues-run = 本 run_id。"""
    args = ["loop", "--task-id", task_id, "--workspace-root", str(Path(governance_root).resolve()), "--actor", driver_actor]
    if envelope_id:
        args += ["--envelope", envelope_id]
    args += ["--max-steps", str(int(remaining)), "--max-wait", f"{float(max_wait):g}", "--interval", f"{float(interval):g}",
             "--continues-run", run_id]
    if no_launch:
        args.append("--no-launch")
    if connection_json:
        args += ["--connection-json", str(Path(connection_json).expanduser().resolve())]
    return args


def _wait_reexec(index: int, change: dict[str, Any], guard: Any, wait_reexec: WaitReexec, *, recorder: LoopRunRecorder,
                 say: Callable[[str], None], args: list[str]) -> str:
    """等待步续等: 新快照可用且有余步 → 日志 / 运行记录各一行(本运行以 deploy_reexec 结束, 记录封存)→ launcher 替换本进程(不返回)。
    返回 = 续等不可行的原因(调用方照 deploy_changed 停, 原因附结束原文); launcher 失败 = 解封记录, 由调用方写 deploy_changed 终态。"""
    from tools.aipos_cli.deploy_runtime import EntryError, change_commits, short

    remaining = int(args[args.index("--max-steps") + 1])
    if remaining < 1:
        return f"--max-steps 已用尽(本等待步为第 {index} 轮), 不再续等"
    try:
        target = guard.current_snapshot()
    except EntryError as exc:
        return f"新部署快照不可用: {exc}"
    args = [*args, *wait_reexec.extra_args]
    say(f"[{index}] 等待中部署已更新({change_commits(change)}); 本步是等待步(无副作用) → re-exec 到新部署快照 {target.name}"
        f"(commit {short(change.get('to'))}), 从同一等待步续等(进程号不变; 本运行记录 {recorder.run_id} 以 deploy_reexec 结束, "
        f"新运行记录 continues_run={recorder.run_id}; 余 {remaining} 轮)")
    recorder.reexec(change, target=target)
    try:
        wait_reexec.launcher(args, target)
    except (OSError, RuntimeError, EntryError) as exc:
        recorder.reexec_failed(f"{type(exc).__name__}: {exc}")
        return f"re-exec 失败({type(exc).__name__}: {exc})"
    recorder.reexec_failed("launcher 返回而未替换进程")
    return "re-exec 未替换进程(launcher 返回)"


def exec_into_snapshot(args: list[str], target: Path) -> None:
    """以新部署快照的代码替换本进程(同一 pid), 跑 `lybra <args>`。派生构造 = F143 workstation_wiring.lybra_cli_invocation(同一解释器 +
    代码树根前置 PYTHONPATH, 零 PATH 依赖; 代码树根 = target)。cwd 切到快照根: `python -m` 的 sys.path[0] = cwd, 防调用方 cwd 里同名
    tools 包遮蔽快照代码(新进程参数已全为绝对路径)。exec 失败 = cwd 复原后原样抛 OSError(调用方退回 deploy_changed)。"""
    from tools.aipos_cli.workstation_wiring import lybra_cli_invocation

    argv, env = lybra_cli_invocation(list(args), code_root=Path(target))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):  # 输出端已断开(ssh 断线): 无可冲刷, 照常替换进程
            continue
    cwd = os.getcwd()
    os.chdir(str(target))
    try:
        os.execve(argv[0], argv, env)
    except OSError:
        os.chdir(cwd)
        raise


def run_loop_cli(args: Any) -> int:
    """CLI 入口(`lybra loop`)。参数缺省读 verbs.schema; 输出人读或 --json; 返回声明的退出码。"""
    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args

    if not getattr(args, "task_id", None):
        # AIPOS-F131: --task-id 由 argparse 必填改为此处校验(父解析器挂了 status 子命令); 文案与退出码同 argparse 必填缺失
        print("lybra loop: error: the following arguments are required: --task-id(看进度: lybra loop status)", file=sys.stderr)
        return 2
    try:
        governance_root = Path(_find_repo_root_for_args(args))  # AIPOS-F144: 唯一薄壳(全局/子命令级 --workspace-root 同义)
    except FileNotFoundError as exc:
        print(f"lybra loop: cannot resolve governance root: {exc}", file=sys.stderr)
        return exit_code_for(load_loop_contract(), "not_derivable")
    json_mode = bool(getattr(args, "json", False))
    sink = io.StringIO() if json_mode else sys.stdout
    try:
        return _run_loop_cli_body(args, governance_root, json_mode, sink)
    except LoopInterrupted as exc:
        # AIPOS-F95 件③: 拉起的进程组已终止; 按收到的信号退出(不新增退出码)
        import signal as _signal

        err = TolerantOutput(sys.stderr)  # AIPOS-F107: ssh 断线后 stderr 也已断开, 写失败不改按信号退出
        if json_mode:
            err(sink.getvalue())
        err(f"lybra loop {args.task_id}: 收到信号 {exc.signum}, 已终止拉起的 harness 进程组, 按该信号退出")
        _signal.signal(exc.signum, _signal.SIG_DFL)
        os.kill(os.getpid(), exc.signum)
        return 128 + exc.signum  # 仅当信号被外部屏蔽时到达


def _cli_wait_reexec(args: Any, json_mode: bool, sink: TextIO) -> WaitReexec:
    """AIPOS-F166 件①: CLI 的续等口——新进程带上本进程的 CLI 层旗标(--json / --verbose); --json 模式本进程攒下的人读行先写 stderr
    (stdout 留给新进程的那一份 JSON 结果; 这些行也已在运行日志里)。"""
    extra = (["--json"] if json_mode else []) + (["--verbose"] if getattr(args, "verbose", False) else [])

    def launcher(reexec_args: list[str], target: Path) -> None:
        if json_mode:
            TolerantOutput(sys.stderr)(sink.getvalue())
        exec_into_snapshot(reexec_args, target)

    return WaitReexec(launcher=launcher, extra_args=extra)


def _run_loop_cli_body(args: Any, governance_root: Path, json_mode: bool, sink: TextIO) -> int:
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
        no_launch=bool(getattr(args, "no_launch", False)),
        wait_reexec=_cli_wait_reexec(args, json_mode, sink),
        continues_run=getattr(args, "continues_run", None),
    )
    if json_mode:
        TolerantOutput(sys.stdout)(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif result.exit_code != 0:
        TolerantOutput(sys.stderr)(f"lybra loop {result.task_id}: {result.outcome} (exit {result.exit_code})")
    return result.exit_code  # AIPOS-F107: 输出端断开不改声明退出码


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
