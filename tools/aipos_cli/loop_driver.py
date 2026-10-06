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
  工位位置 enrollment.workstation_location, 身份 charter_render.workstation_identity,
  kickoff = 工位 my-tasks --task-id <等待目标卡> 的 next_card.kickoff(AIPOS-F111: 按卡号取, 同工位多卡可各自拉起))
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
    signals_deferred as _signals_deferred,
    signals_raise_interrupt as _signals_raise_interrupt,
)
from tools.aipos_cli.verb_contract import declared_exit_code  # AIPOS-F101 件③: 退出码唯一读取口
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
    (门侧 _match_driver_envelope 同口径)。AIPOS-F90 件①: 判定对象(审计卡 = 被审卡)经 envelope_subject, 与门同一规则。
    """
    from tools.aipos_cli.autonomy_policy import select_envelope

    roles = [r for r in (str(driver_role or "").strip(), DRIVER_ROLE) if r]
    roles = list(dict.fromkeys(roles))
    task = {
        "task_id": task_id,
        "task_mode": str(task_fm.get("task_mode") or ""),
        "project": str(task_fm.get("project") or ""),
        "reviewed_task_id": str(task_fm.get("reviewed_task_id") or ""),
    }
    return select_envelope(
        governance_root,
        identities=[(driver_actor, driver_actor, role) for role in roles],
        task=task,
        policy_id=policy_id,
        now=now,
    )


def mint_hint(*, task_id: str, task_fm: dict[str, Any], driver_actor: str, now: datetime | None = None,
              governance_root: Path | None = None) -> str:
    """申领出口: 既有 `lybra envelope mint --confirm` 命令(AIPOS-F92 件①: 经门 owner_decision_record envelope 路径真实落盘;
    Owner 亲自敲, --connection-json 指向持 Owner 凭据的 connection.json), 参数按本卡填好可照抄。"""
    now = now or utc_now()
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

    def to_dict(self) -> dict[str, Any]:
        return {"card": self.card, "harness": self.harness, "instance": self.instance, "launched": not self.refusal,
                "refusal": self.refusal, "manual_hint": self.manual_hint if self.refusal else "",
                "workstation": self.location.get("dir"), "host": self.location.get("host"),
                "transport": self.location.get("transport")}


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


def _render_manual_hint(decl: dict[str, Any], *, harness: str, instance: str, location: dict[str, Any]) -> str:
    hints = decl["manual_hint"]
    if location.get("found") and location.get("transport") == "local":
        template = str(hints["local"])
    elif location.get("found"):
        template = str(hints["remote"])
    else:
        template = str(hints["unlocated"])
    values = {"dir": str(location.get("dir") or ""), "host": str(location.get("host") or ""), "harness": harness or "(未知 harness)",
              "instance": instance or "(未知实例)", "reason": str(location.get("reason") or "")}
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
    from tools.aipos_cli.enrollment import workstation_location
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
    try:
        plan.location = workstation_location(governance_root, plan.instance) if plan.instance else {
            "found": False, "reason": f"{card} 无 assigned_to/认领实例"}
    except (ValueError, OSError, SchemaLoadError) as exc:
        plan.location = {"found": False, "reason": f"工位位置读取失败: {exc}"}
    plan.manual_hint = _render_manual_hint(decl, harness=plan.harness, instance=plan.instance, location=plan.location)

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


def check_workstation(project_root: Path, instance: str, harness: str) -> dict[str, Any]:
    """AIPOS-F110 件③: `lybra project check-workstation` —— loop 拉起前置的逐项只读检查(与 plan_launch 同一读取口/同一 ssh 代码路径):
    land 事件位置 → (local) 工位身份 + harness 可执行在本机 PATH; (remote) 材料声明 + 门机→工位 ssh 探测(目录 + harness 可执行)
    + 工位→门机反向可达(远端经声明的 gate_ssh_alias 以同一 ssh 前缀 `test -d <门机治理根>`)。不起 harness、不写任何文件。"""
    import shutil

    from tools.aipos_cli.autonomy_policy import launchable_harnesses
    from tools.aipos_cli.charter_render import WorkstationIdentityError, workstation_identity
    from tools.aipos_cli.enrollment import workstation_location
    from tools.aipos_cli.harness_launch import probe_remote, remote_check_reverse, remote_declaration
    from tools.aipos_cli.workspace_config import WorkstationDeclarationError, project_workstation
    from tools.schema_loader import SchemaLoadError

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> bool:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def report() -> dict[str, Any]:
        return {"ok": all(c["ok"] for c in checks), "instance": instance, "harness": harness, "checks": checks}

    try:
        loc = workstation_location(project_root, instance)
    except (ValueError, OSError, SchemaLoadError) as exc:
        check("land 事件位置", False, f"读取失败: {exc}")
        return report()
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
                   wait_patterns: list[str]) -> bool:
    """拉起一次 harness 并经唯一哨兵等待产物(进程退出也唤醒), 结束即清进程组。返回 True = loop 应以 result 出口返回。

    产物就绪 → 宽限 grace_seconds 后终止进程组, 继续推导; 进程早退而产物未就绪 → exit 3 附 stderr 末尾;
    等待超时 → 终止进程组, exit 3; 收到 SIGINT/SIGTERM → 终止进程组, 抛 LoopInterrupted; 拉起事件写不进 session record → 终止, exit 4。"""
    wait_timeout = exit_code_for(contract, "wait_timeout")
    terminate_wait = float(decl["terminate_wait_seconds"])
    where = f"{plan.location.get('host') or '本机'}:{plan.cwd}"
    try:
        harness = LaunchedHarness(plan, decl, say)
    except OSError as exc:
        step.ok, step.message = False, f"拉起 {plan.harness} 失败({type(exc).__name__}: {exc}); 手工模式: {plan.manual_hint}"
        say(f"[{index}] exit 3 — {step.message}")
        result.outcome, result.exit_code, result.message = "wait_timeout", wait_timeout, step.message
        return True
    step.launch = {**(step.launch or {}), "pid": harness.pid, "pgid": harness.pgid, "remote_pgid": harness.remote_pgid}
    remote_part = f" remote_pgid={harness.remote_pgid}(经 ssh, kickoff 经 stdin)" if plan.remote else ""
    say(f"[{index}] launch: {plan.harness} @ {where} pid={harness.pid} pgid={harness.pgid}{remote_part}(信封 {envelope_id} launch_harnesses 授权; "
        f"kickoff = 工位 my-tasks --task-id {plan.card} next_card.kickoff)")
    outcome = "error"
    try:
        failure = _session_event(governance_root, plan.card, actor, "harness_launch",
                                 f"harness={plan.harness}; transport={plan.location.get('transport')}; host={plan.location.get('host') or '(本机, 存量缺 host)'}; "
                                 f"workstation={plan.cwd}; pid={harness.pid}; pgid={harness.pgid}; remote_pgid={harness.remote_pgid}; envelope={envelope_id}")
        if failure:
            outcome = "record_failed"
            step.ok, step.message = False, f"拉起事件写不进 {plan.card} session record({failure}), 已终止进程组; 拉起须留痕(fail-closed)"
            say(f"[{index}] exit 4 — {step.message}")
            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), step.message
            return True
        watch_out = io.StringIO()
        with _signals_raise_interrupt():
            with contextlib.redirect_stdout(watch_out):
                rc = watch(watch_args, expect_ready=ready, stop_when=harness.exited, sleeper=harness.pump)
            step.exit_code, step.output = int(rc), watch_out.getvalue().strip()
            if rc == declared_exit_code("lybra_agent_watch", "change"):  # AIPOS-F101 件③: 哨兵退出码读声明
                outcome = "artifact_ready"
                harness.pump(float(decl["grace_seconds"]))  # 宽限期内自行退出即收尾(输出照常汇总)
                detail = "宽限期内自行退出" if harness.exited() else f"宽限 {decl['grace_seconds']}s 后终止进程组"
                step.message = f"产物就绪(拉起的 {plan.harness} 进程: {detail})"
                say(f"[{index}] ready: {step.output}")
                return False
            step.ok = False
            if harness.exited():
                outcome = "early_exit"
                harness.pump(terminate_wait)  # 进程已退出: 读尽管道余量(EOF 即返回)再取 stderr 末尾
                tail = "\n".join(f"    {line}" for line in harness.stderr_tail) or "    (stderr 无输出)"
                step.message = (f"拉起的 {plan.harness} 进程已退出(exit {harness.proc.returncode})而产物未就绪: 等的是 {wait_patterns}\n"
                                f"  stderr 末尾:\n{tail}")
            else:
                outcome = "timeout"
                step.message = f"等待产物超时(watch exit {rc}), 已终止拉起的 {plan.harness} 进程组: 等的是 {wait_patterns}"
            say(f"[{index}] exit 3 — {step.message}")
            result.outcome, result.exit_code, result.message = "wait_timeout", wait_timeout, step.message
            return True
    except LoopInterrupted as exc:
        outcome = f"interrupted(signal {exc.signum})"
        step.ok, step.message = False, f"loop 收到信号 {exc.signum}: 已终止拉起的 {plan.harness} 进程组"
        say(f"[{index}] interrupted — {step.message}")
        raise
    finally:
        with _signals_deferred() as pending:  # 清进程组期间不被信号打断; 期间到的信号清完再按 LoopInterrupted 抛
            how = harness.terminate_group(terminate_wait)
            harness.close()
        say(harness.counts_line())
        step.launch = {**(step.launch or {}), "outcome": outcome, "returncode": harness.proc.returncode, "termination": how}
        failure = _session_event(governance_root, plan.card, actor, "harness_exit",
                                 f"harness={plan.harness}; pid={harness.pid}; outcome={outcome}; returncode={harness.proc.returncode}; termination={how}")
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
) -> LoopResult:
    """有界推进一张卡。返回 LoopResult(exit_code 按 verbs.schema lybra_loop.exit_codes)。

    derive/execute/watch 可注入(靶场用), 缺省 = 产品唯一实现。
    AIPOS-F95: no_launch=True = 不拉起 harness(手工模式); 拉起等待期间收到 SIGINT/SIGTERM → 清进程组后抛 LoopInterrupted。
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
        try:
            return _drive(task_id, governance_root, result, contract=contract, max_steps=max_steps, max_wait=max_wait,
                          interval=interval, allowed_verbs=allowed_verbs, say=say, derive=derive, execute=execute, watch=watch,
                          connection_json=connection_json, policy=policy, driver_actor=driver_actor, no_launch=no_launch)
        except FrontmatterReadError as exc:
            # AIPOS-F100 件②: 推导核之外的回读(落账回读 / 结案判据 / 等待实例)遇记录读不出 = 同一硬停 exit 4 点名文件
            stop = frontmatter_unreadable_stop(task_id, exc)
            say(f"lybra loop {task_id}: exit 4 — {exc}")
            result.outcome, result.exit_code, result.message = "not_derivable", exit_code_for(contract, "not_derivable"), str(exc)
            result.missing_records, result.suggested_action = stop["missing_records"], stop["suggested_action"]
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
) -> LoopResult:
    steps = result.steps
    launch_decl = launch_declaration(contract)
    launched: set[str] = set()  # AIPOS-F95 件③④: 一次 loop 运行对一张卡至多拉起一次
    from tools.aipos_cli.governance_commit import n6_landing_declaration

    landing_action = str(n6_landing_declaration()["action_type"])  # AIPOS-F94: N6 落账步(声明 transitions nodes.N6.landing)

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
        launch_card: str | None = None  # AIPOS-F95: 执行体(N1)/审计体(N3)等待才可拉起; external finalize 等待不拉起

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
                    launch_card = audit_card
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
                return result
            if derivation.get("derivable") or wait_patterns:
                pass  # 审计卡可推导 → 下方账务步; 已定等待 → 下方 watch
            elif node == "claim" and state == "claimed":
                # N1→N2: 执行体在干活 → 等 Return 落盘(落点读项目声明; 骨架不算, 判据=推导核)
                watch_root, wait_patterns = executor_artifact_watch(governance_root, task_id)
                launch_card = task_id
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
                # AIPOS-F114: 等待中本轮作废(所审交回过期 / 被下一轮取代)也醒来——重推导被审卡派生重交回, 不空等到超时
                return (bool(d.get("derivable")) or (d.get("action") or {}).get("type") in HARD_STOP_ACTIONS
                        or isinstance(d.get("return_stale"), dict) or isinstance(d.get("superseded_by"), dict))

            plan: LaunchPlan | None = None
            if launch_card is not None:
                plan = plan_launch(governance_root, launch_card, policy=policy, no_launch=no_launch, decl=launch_decl,
                                   already_launched=launched)
                step.launch = plan.to_dict()
                if plan.refusal:
                    say(f"[{index}] manual: {plan.manual_hint}(未拉起: {plan.refusal})")
            watch_args = _watch_args(watch_root, wait_patterns, max_wait=max_wait, interval=interval)
            if plan is not None and not plan.refusal:
                launched.add(launch_card)
                return_now = _launched_wait(index, step, plan, governance_root, result, contract=contract, decl=launch_decl,
                                            watch=watch, watch_args=watch_args, ready=_ready, say=say, actor=driver_actor,
                                            envelope_id=str((policy or {}).get("policy_id") or ""), wait_patterns=wait_patterns)
                steps.append(step)
                if return_now:
                    return result
                continue
            watch_out = io.StringIO()
            with contextlib.redirect_stdout(watch_out):
                rc = watch(watch_args, expect_ready=_ready)
            step.exit_code, step.output = int(rc), watch_out.getvalue().strip()
            if rc != declared_exit_code("lybra_agent_watch", "change"):  # AIPOS-F101 件③: 哨兵退出码读声明
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
            say(f"[{index}] exit 2 — {msg}")
            result.outcome, result.exit_code, result.message = "gate_rejected", exit_code_for(contract, "gate_rejected"), msg
            return result
        say(f"[{index}] ok: {step.message}")
        if action_type == landing_action:
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
    try:
        return _run_loop_cli_body(args, governance_root, json_mode, sink)
    except LoopInterrupted as exc:
        # AIPOS-F95 件③: 拉起的进程组已终止; 按收到的信号退出(不新增退出码)
        import signal as _signal

        if json_mode:
            print(sink.getvalue(), file=sys.stderr)
        print(f"lybra loop {args.task_id}: 收到信号 {exc.signum}, 已终止拉起的 harness 进程组, 按该信号退出", file=sys.stderr, flush=True)
        _signal.signal(exc.signum, _signal.SIG_DFL)
        os.kill(os.getpid(), exc.signum)
        return 128 + exc.signum  # 仅当信号被外部屏蔽时到达


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
    )
    if json_mode:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif result.exit_code != 0:
        print(f"lybra loop {result.task_id}: {result.outcome} (exit {result.exit_code})", file=sys.stderr)
    return result.exit_code


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
