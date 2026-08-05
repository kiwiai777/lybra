"""AIPOS-332 — 派工编排层(pump run 名副其实:一条命令走完全程)。

本模块是**编排层**(S6①):把 launch-check / watch / supervise 这些**各自独立**的
零件串成一个动作,而不把零件逻辑内联进来。编排出问题时人可单步接管;替换编排实现,
零件不受影响。

六步之间的**契约**(S6③,输入/输出显式,步骤可插拔):
  1. step_build_context       : (cli args) -> DispatchContext
  2. step_generate_kickoff    : (ctx) -> {kickoff_raw}        (三层制约,已有)
  3. step_expand_kickoff      : (ctx, kickoff_raw) -> {kickoff}  (S7 占位符必须展开)
  4. step_claim               : (ctx) -> {claim_landed:bool}     (S11 拉起前认领;失败即止)
  5. step_launch              : (ctx, kickoff) -> {launch_ok, proc}  (launch-check 三合一)
  6. step_watch               : (ctx, plan) -> {sentinel_verdict}    (watch 四出口,派生参数)
  7. step_report              : (各步结果) -> 结论 + 落库事件

红线:
  - 判断留人:本层不选卡、不无限重试、不推送(S11b:不削弱拉取式自认领)。
  - 不吞错:任何一步失败都响(S1 硬约束)。
  - 不削弱既有语义:watch 四出口 / launch-check 有界自愈 / supervise exit 75 一律不动。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 记录布局(与 records.py 对齐;S9 expect 派生以此为准,不许人手填)。
# 工作区(gate 领地)是唯一保证存在的根(S10)。
# ---------------------------------------------------------------------------
RECORDS_ROOT = "5_tasks/records"
RETURNS_DIR = f"{RECORDS_ROOT}/returns"
CLAIMS_DIR = f"{RECORDS_ROOT}/claims"
AUDIT_VERDICTS_DIR = f"{RECORDS_ROOT}/audit_verdicts"
EVENTS_DIR = f"{RECORDS_ROOT}/events"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_rel_dir(record_kind: str, task_id: str, reviewed_task_id: str | None) -> str:
    """某类记录相对 workspace-root 的目录(S9 硬约束1:编码"落哪个 ID 目录")。

    - return(执行体交回):落在【自身 ID】目录。
    - audit_verdict(审计裁决):落在【被审卡 ID】目录(非审计卡 ID)—— 当日 P0 失效的根因。
    - claim / event:落在【自身 ID】目录。
    """
    if record_kind == "audit_verdict":
        # 裁决归被审卡(原执行卡)的 ID 目录
        return f"{AUDIT_VERDICTS_DIR}/{reviewed_task_id or task_id}"
    if record_kind == "return":
        return f"{RETURNS_DIR}/{task_id}"
    if record_kind == "claim":
        return f"{CLAIMS_DIR}/{task_id}"
    if record_kind == "event":
        return f"{EVENTS_DIR}/{task_id}"
    return f"{RECORDS_ROOT}/{task_id}"


# ---------------------------------------------------------------------------
# S9 — expect 路径由产品从 gate 记录布局派生,不由人手填。
# ---------------------------------------------------------------------------

def derive_expect_patterns(
    task_id: str,
    role: str,
    output_location_profile: dict[str, Any],
    reviewed_task_id: str | None = None,
) -> dict[str, Any]:
    """从记录布局派生 watch 的 --expect 模式(相对 workspace-root 的 glob)。

    S9 硬约束1:
      - 执行体 return 落【自身 ID】目录;
      - 审计裁决落【被审卡 ID】目录;
      - BLOCK 的可观测信号是工作区 events 下的 blocked_* (不是产品仓 task_cards)。
    S9b 硬约束3:派生结果精确到本轮,**不生成会命中历史产物的宽松时间片段 glob**
      (如 completed_2026080*14*.md)—— 只用目录级 glob,新鲜/陈旧由自证区分。
    S12 落实3:remote / no_file_output 只从工作区 events 派生,不派生产品仓路径。
    """
    expect_source = output_location_profile.get("expect_source", "workspace")
    monitors_product_repo = bool(output_location_profile.get("monitors_product_repo"))

    patterns: list[dict[str, str]] = []
    # 成功信号:工作区记录(无条件落工作区,S10)
    if role == "auditor":
        success_dir = _record_rel_dir("audit_verdict", task_id, reviewed_task_id)
        patterns.append({"pattern": f"{success_dir}/*.md", "meaning": "audit_verdict"})
    else:
        success_dir = _record_rel_dir("return", task_id, None)
        patterns.append({"pattern": f"{success_dir}/*.md", "meaning": "return"})
    # BLOCK 信号:工作区 events(不是产品仓 task_cards;当日 P0 失效的根因)
    block_dir = _record_rel_dir("event", task_id, None)
    patterns.append({"pattern": f"{block_dir}/blocked_*.md", "meaning": "blocked"})

    note = ""
    if not monitors_product_repo:
        note = (
            "产出位置不用产品仓,expect 仅从工作区 events/records 派生(S12 落实3);"
            "产品仓内的文件不得作为唯一载体(S10 硬约束3)。"
        )
    return {
        "patterns": patterns,
        "expect_source": expect_source,
        "monitors_product_repo": monitors_product_repo,
        "note": note,
    }


def _glob_in_workspace(workspace_root: Path, pattern: str) -> list[Path]:
    """在 workspace_root 下做相对 glob,返回已存在的匹配(已排序)。"""
    matches: list[Path] = []
    # 支持含 ** 与单段 * 的相对 glob。
    parts = pattern.split("/")
    current: list[Path] = [workspace_root]
    for part in parts:
        nxt: list[Path] = []
        for base in current:
            if part == "**":
                # 退化为递归(本场景只用到单层与末段 *)
                if base.is_dir():
                    nxt.extend(base.rglob("*"))
            else:
                if base.is_dir():
                    nxt.extend(sorted(base.glob(part)))
        current = sorted(set(p for p in nxt if p.exists()))
    # 仅保留文件
    matches = [p for p in current if p.is_file()]
    return sorted(matches)


def verify_sentinel_params(
    workspace_root: Path,
    expect_patterns: list[dict[str, str]],
    run_log_path: Path | None,
    observation_plan: dict[str, Any],
) -> dict[str, Any]:
    """S9 硬约束3 / S9b — 挂哨自证:参数与真实布局对拍。

    对每个 expect:报告当前是否已命中(布防即检),命中则给出时间戳并明确标注
    “布防前已存在”(S9b 硬约束1,区分陈旧与新鲜)。run-log 是否属本轮、观测面是否有效。
    对不上 → errors(非空时编排层不得带着错参数空等)。
    """
    errors: list[str] = []
    expect_status: list[dict[str, Any]] = []
    for entry in expect_patterns:
        pattern = entry["pattern"]
        matches = _glob_in_workspace(workspace_root, pattern)
        status: dict[str, Any] = {
            "pattern": pattern,
            "meaning": entry.get("meaning", ""),
            "matched": bool(matches),
            "count": len(matches),
        }
        if matches:
            # 记录布防前已存在的命中(陈旧产物),标注时间戳(S9b 硬约束1)
            snapshots = []
            for m in matches[:5]:
                try:
                    ts = datetime.fromtimestamp(m.stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    ts = "?"
                snapshots.append({"path": str(m.relative_to(workspace_root)), "mtime": ts})
            status["pre_existing"] = True
            status["samples"] = snapshots
            status["label"] = "布防前已存在(陈旧命中,非本轮新产出)"
        expect_status.append(status)

    # run-log 校验:run_log_role 决定它该用于结束检测还是停滞检测
    run_log_status: dict[str, Any] = {"role": observation_plan.get("run_log_role", "end_only")}
    if run_log_path is not None:
        if run_log_path.exists():
            run_log_status["exists"] = True
            run_log_status["path"] = str(run_log_path)
        else:
            # run-log 尚未产生(本轮拉起时直接传递,正常);若 role=end_only 可接受
            run_log_status["exists"] = False
            run_log_status["note"] = "本轮 run-log 尚未产生(由拉起步骤直接传递,非取最新推断)"
    # 观测面有效性:停滞面不应是 run_log(除非运行体明确非缓冲)
    stall_surfaces = observation_plan.get("stall_surfaces", [])
    if "run_log" in stall_surfaces and observation_plan.get("run_log_role") == "end_only":
        errors.append(
            "观测面冲突:运行体输出缓冲却用 run_log 判停滞(恒不变→假 STALL)。"
            "请检查 runtime profile。"
        )

    warnings = list(observation_plan.get("warnings", []))
    return {
        "expect_status": expect_status,
        "run_log": run_log_status,
        "stall_surfaces": stall_surfaces,
        "errors": errors,
        "warnings": warnings,
        "verified_at": _utc_now(),
    }


# ---------------------------------------------------------------------------
# 步骤间契约载体(S6③):六步共享的上下文,显式字段,可序列化。
# ---------------------------------------------------------------------------

@dataclass
class DispatchContext:
    """派工全程的上下文(各步只读写自己声明的字段,互不暗耦合)。"""
    card_id: str
    role: str
    round_type: str = "first"
    delta: str = ""
    # 根与连接
    workspace_root: Path = field(default_factory=Path)
    product_repo: Path = field(default_factory=Path)
    gate_url: str = ""
    connection_json: Path = field(default_factory=Path)
    envelope: str = ""
    executor_instance: str = ""
    reviewed_task_id: str | None = None  # 审计裁决落该 ID 目录
    # 运行体与产出
    runtime_type: str | None = None
    output_target: str | None = None
    collaboration_profile: dict[str, Any] | None = None
    # 运行时命令模板(顾问/harness 提供,含 {kickoff} 占位)
    runtime_cmd_template: str | None = None
    # 派生结果(各步填)
    observation_plan: dict[str, Any] = field(default_factory=dict)
    kickoff: str = ""
    expect_derivation: dict[str, Any] = field(default_factory=dict)
    sentinel_verify: dict[str, Any] = field(default_factory=dict)


def step_build_context(ctx: DispatchContext) -> DispatchContext:
    """步骤1:按运行体类型 × 产出位置 选择观测面(S2/S8/S12),写入 ctx。"""
    from tools.aipos_cli.runtime_profiles import select_observation_plan

    plan = select_observation_plan(
        ctx.runtime_type, ctx.output_target, ctx.collaboration_profile
    )
    ctx.observation_plan = plan
    return ctx


# S7 — kickoff 占位符表:占位符 → 取值来源(必须全部展开,不得留待人工补)。
# 无对应取值时**当场报错并说清缺什么**,不输出带占位符的半成品。
_PLACEHOLDER_REQUIRED = {
    "workspace": "治理工作区根(workspace_root)",
    "gate": "gate URL(gate_url)",
    "product_repo": "产品仓根(product_repo)",
    "envelope": "预授权信封(envelope)",
}


def step_expand_kickoff(
    ctx: DispatchContext, kickoff_raw: str, *, strict: bool = True
) -> str:
    """步骤3(S7):把 kickoff 中的 {workspace}/{gate}/{product_repo}/{envelope} 展开。

    取值来源为命令参数与工作区配置。无法取得某个值时当场报错(不输出半成品)。
    strict=True:残留任何 {...} 未展开占位符 → 抛错(默认)。
    """
    values = {
        "workspace": str(ctx.workspace_root) if ctx.workspace_root else "",
        "gate": ctx.gate_url,
        "product_repo": str(ctx.product_repo) if ctx.product_repo else "",
        "envelope": ctx.envelope,
    }
    out = kickoff_raw
    for key, val in values.items():
        if not val:
            raise ValueError(
                f"无法展开 kickoff 占位符 {{{key}}}:缺少 {_PLACEHOLDER_REQUIRED[key]}。"
                " 派工中止——不输出带占位符的半成品(S7)。"
            )
        out = out.replace("{" + key + "}", val)
    if strict:
        leftover = re.findall(r"\{[a-z_]+\}", out)
        if leftover:
            raise ValueError(
                f"kickoff 仍含未展开占位符: {leftover}。请检查模板(S7)。"
            )
    return out


def step_expand_kickoff_lenient(
    ctx: DispatchContext, kickoff_raw: str
) -> tuple[str, list[str]]:
    """F-332-03:dry-run 友好版占位符展开。

    展开**可得**的占位符,缺值**不报错**:缺失的占位符原样保留(如 ``{envelope}``),
    供 dry-run 展示未展开并告警(旧表层行为恢复——裸跑 ``--dry-run`` 不需 envelope 即 exit 0)。

    与 :func:`step_expand_kickoff` 的区别:后者在非 dry-run 派工时缺值即硬失败(S7 语义不变);
    本函数仅供 dry-run 使用,返回 ``(文本, 缺失键列表)``。
    """
    values = {
        "workspace": str(ctx.workspace_root) if ctx.workspace_root else "",
        "gate": ctx.gate_url,
        "product_repo": str(ctx.product_repo) if ctx.product_repo else "",
        "envelope": ctx.envelope,
    }
    out = kickoff_raw
    missing: list[str] = []
    for key, val in values.items():
        if not val:
            missing.append(key)
            continue
        out = out.replace("{" + key + "}", val)
    return out, missing


# ---------------------------------------------------------------------------
# S11 — 认领是派工的一部分(编排式):拉起前由泵代认领,失败即止。
# 与 auditor_loop 现有做法一致;拉取式自认领不受影响(S11b)。
# ---------------------------------------------------------------------------

def _load_role_token(connection_json: Path, role: str) -> str | None:
    """从 connection.json 取某角色的 token(只按名引用,不回显)。"""
    try:
        data = json.loads(connection_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for item in data.get("tokens", []):
        if isinstance(item, dict) and item.get("role") == role:
            tok = (item.get("token") or "").strip()
            return tok or None
    return None


def _claim_record_landed(workspace_root: Path, task_id: str) -> Path | None:
    """S11 硬约束3:认领成功的判据是记录落地(claims/<ID>/claim_*.md 存在)。"""
    d = workspace_root / _record_rel_dir("claim", task_id, None)
    if not d.is_dir():
        return None
    for p in sorted(d.glob("claim_*.md")):
        return p
    return None


def step_claim(ctx: DispatchContext) -> dict[str, Any]:
    """步骤4(S11):经预授权信封一发式认领。失败即不拉起(S11 硬约束2/附带查明的权限事实)。

    返回 {ok, auto_released, claim_record, reason}。ok=False 时编排层终止派工。
    """
    from tools.aipos_cli.confirm_client import GateClient, GateError

    token = _load_role_token(ctx.connection_json, ctx.role)
    if not token:
        return {
            "ok": False,
            "reason": f"在 {ctx.connection_json} 未找到 {ctx.role} 角色 token",
            "auto_released": False,
            "claim_record": None,
        }
    if not ctx.envelope:
        return {
            "ok": False,
            "reason": "未提供预授权信封(envelope);编排式派工须由泵代认领(S11)。",
            "auto_released": False,
            "claim_record": None,
        }
    client = GateClient(ctx.gate_url, token)
    actor = ctx.executor_instance or f"{ctx.role}.lybra.kiwiai-dev"
    try:
        resp = client.call_tool("lybra_queue_claim_dry_run", {
            "actor": actor,
            "agent_instance": actor,
            "autonomy_mode": "PreAuthorized",
            "owner_policy_ref": ctx.envelope,
            "task_id": ctx.card_id,
        })
    except GateError as exc:
        return {
            "ok": False,
            "reason": f"gate 调用失败(瞬态/网络): {exc}",
            "auto_released": False,
            "claim_record": None,
        }
    auto_released = bool(resp.get("preauthorized_release")) and resp.get("autonomy_mode") == "PreAuthorized"
    if not auto_released:
        reasons = resp.get("blocking_reasons") or resp.get("owner_confirmation_reasons") or resp
        return {
            "ok": False,
            "reason": (
                "预授权信封未自动放行(信封不匹配/额度耗尽/卡已被他人持有)。"
                f" gate 应答: {json.dumps(reasons, ensure_ascii=False)[:300]}"
            ),
            "auto_released": False,
            "claim_record": None,
        }
    # S11 硬约束3:认领成功以记录落地为准
    record = _claim_record_landed(ctx.workspace_root, ctx.card_id)
    if record is None:
        return {
            "ok": False,
            "reason": "gate 返回 auto_released 但 claims/<ID>/claim_*.md 未落地;以记录为准判失败。",
            "auto_released": True,
            "claim_record": None,
        }
    return {"ok": True, "auto_released": True, "claim_record": str(record), "reason": ""}


# ---------------------------------------------------------------------------
# AIPOS-332F2 — resume/fix 轮:跳过 claim,校验卡已由本实例持有。
# 与 step_claim 同层(编排式),不内联 gate 调用。
# ---------------------------------------------------------------------------

def _find_claim_holder(workspace_root: Path, task_id: str) -> str | None:
    """从 claims/<ID>/claim_*.md 读出当前持有者的 canonical_agent_instance。

    返回持有者实例名(如 ``exec.lybra.kiwiai-dev``);无记录或解析失败返回 None。
    """
    d = workspace_root / _record_rel_dir("claim", task_id, None)
    if not d.is_dir():
        return None
    for p in sorted(d.glob("claim_*.md"), reverse=True):
        # 取最新的一条 claim 记录
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if not content.startswith("---"):
            continue
        end = content.find("\n---", 3)
        if end < 0:
            continue
        holder: str | None = None
        for line in content[3:end].splitlines():
            if line.startswith("canonical_agent_instance:"):
                holder = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("actor:") and holder is None:
                holder = line.split(":", 1)[1].strip().strip("'\"")
        if holder:
            return holder
    return None


def step_verify_held(ctx: DispatchContext) -> dict[str, Any]:
    """AIPOS-332F2:resume/fix 轮替代 step_claim。

    校验卡已由本 executor_instance 持有(claims 记录存在且持有者匹配)。
    不发起新 claim(幂等:重复 claim 可能产生重复记录)。

    返回 {ok, holder, reason}。ok=False 时编排层终止派工。
    """
    holder = _find_claim_holder(ctx.workspace_root, ctx.card_id)
    expected = ctx.executor_instance or f"{ctx.role}.lybra.kiwiai-dev"
    if holder is None:
        return {
            "ok": False,
            "holder": None,
            "reason": (
                f"resume/fix 轮要求卡已被持有,但 claims/{ctx.card_id}/ 下无 claim 记录"
                f"(或记录无法解析)。请先用 first 轮认领本卡。"
            ),
        }
    if holder != expected:
        return {
            "ok": False,
            "holder": holder,
            "reason": (
                f"resume/fix 轮:卡 {ctx.card_id} 由 {holder!r} 持有,"
                f"但当前实例为 {expected!r}。不允许跨实例续派/修复。"
            ),
        }
    return {"ok": True, "holder": holder, "reason": ""}


# ---------------------------------------------------------------------------
# S1 步骤5/6 — 拉起(launch-check)与挂哨(watch):组合零件,不内联。
# ---------------------------------------------------------------------------

def _build_spawn_cmd(ctx: DispatchContext) -> tuple[str, Path | None]:
    """把 runtime_cmd_template 的 {kickoff} 用 @file 安全传递(AIPOS-327F1/S1.2)。"""
    if not ctx.runtime_cmd_template:
        raise ValueError(
            "未提供 runtime_cmd_template(含 {kickoff} 占位)。编排层不猜如何拉起运行体。"
        )
    if "{kickoff}" not in ctx.runtime_cmd_template:
        raise ValueError(
            "runtime_cmd_template 不含 {kickoff} 占位;编排层无法安全传递 kickoff。"
        )
    fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="lybra_kickoff_pump_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ctx.kickoff)
    cmd = ctx.runtime_cmd_template.replace("{kickoff}", f"@{tmp}")
    return cmd, Path(tmp)


def step_launch(ctx: DispatchContext) -> dict[str, Any]:
    """步骤5:走 launch-check 的三合一判据与有界自愈。起不来当场说起不来,非零退出。

    组合 tools.aipos_cli.agent_launch_check.run_launch_check,不内联其逻辑(S6①)。
    AIPOS-332F4 修一:拉起窗口与检查间隔从运行体档案取,不硬编码秒数。
    返回 {ok, exit_code, pid?}。
    """
    from tools.aipos_cli.agent_launch_check import run_launch_check, EXIT_OK

    spawn_cmd, tmp = _build_spawn_cmd(ctx)
    plan = ctx.observation_plan
    session_dirs = _session_dirs_for(ctx)
    worktree_path = str(ctx.product_repo) if plan.get("worktree_criterion") else ""
    # AIPOS-332F4 修一:窗口/节奏从运行体档案取,代码不写死任何秒数。
    runtime_profile = plan.get("runtime_profile", {})
    launch_window = float(runtime_profile.get("launch_window_secs", 180))
    check_interval = float(runtime_profile.get("check_interval_secs", 5))
    try:
        code = run_launch_check(
            spawn_cmd=spawn_cmd,
            task_id=ctx.card_id,
            executor_instance=ctx.executor_instance or f"{ctx.role}.lybra.kiwiai-dev",
            product_repo=ctx.product_repo,
            session_dirs=session_dirs,
            worktree_path=worktree_path,
            launch_window_secs=launch_window,
            check_interval_secs=check_interval,
            # F-332-01: 失败类事件无条件落工作区(工作区是唯一保证存在的根,S10)。
            # daemon 路径不调本函数,行为不受影响。
            workspace_root=ctx.workspace_root or None,
        )
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return {"ok": code == EXIT_OK, "exit_code": code}


def _encode_cwd_for_pi(cwd: Path) -> str:
    """pi 会话目录编码规则:绝对路径的 / 替换为 -,前后加 --。

    例:``/home/kiwi/projects/lybra`` → ``--home-kiwi-projects-lybra--``
    与本机 ``~/.pi/agent/sessions/`` 下的实际目录名一致(实证)。
    """
    abs_path = str(cwd.resolve())
    return "--" + abs_path.lstrip("/").replace("/", "-") + "--"


def _session_dirs_for(ctx: DispatchContext) -> list[str]:
    """AIPOS-332F3 修一:会话目录取自运行体档案,不硬编码 product_repo/.pi。

    派生逻辑:
      1. 从运行体档案取 session_root(如 pi → ``~/.pi/agent/sessions``);
      2. 按档案的编码规则 + 运行目录(产品仓)算出具体子目录;
      3. **校验存在性**:目录不存在 → 明确告警并降级到 CPU/工作树判据,
         不得带着死判据开杀(AIPOS-332F3 根因修复)。
    """
    dirs: list[str] = []
    warnings: list[str] = []

    # 从运行体档案取 session_root(纯数据,不硬编码)
    runtime_profile = ctx.observation_plan.get("runtime_profile", {})
    session_root_raw = runtime_profile.get("session_root")
    encoding = runtime_profile.get("session_dir_encoding")

    if session_root_raw and encoding:
        session_root = Path(session_root_raw).expanduser()
        if encoding == "pi_cwd_dash":
            encoded = _encode_cwd_for_pi(ctx.product_repo)
            session_dir = session_root / encoded
        else:
            session_dir = session_root

        if session_dir.is_dir():
            dirs.append(str(session_dir))
        else:
            # 目录不存在 → 告警 + 降级(不带着死判据开杀)
            warnings.append(
                f"运行体档案派生的会话目录不存在: {session_dir}"
                ";已降级到 CPU/工作树判据,不因此误杀健康 agent(AIPOS-332F3 修一)。"
            )

    # 其他已知会话目录(cc 等,按产品仓内是否存在加入)
    if (ctx.product_repo / ".claude").is_dir():
        dirs.append(str(ctx.product_repo / ".claude"))

    # 把告警注入 observation_plan 以便上层可见
    if warnings:
        existing = ctx.observation_plan.get("warnings", [])
        ctx.observation_plan["warnings"] = existing + warnings

    return dirs


def _derive_watch_namespace(ctx: DispatchContext) -> Any:
    """构造 run_fs_watch 需要的 args 命名空间(派生 expect + 观测面)。"""
    from argparse import Namespace

    deriv = derive_expect_patterns(
        ctx.card_id, ctx.role,
        ctx.observation_plan.get("output_location_profile", {}),
        ctx.reviewed_task_id,
    )
    ctx.expect_derivation = deriv
    expect = [e["pattern"] for e in deriv["patterns"]]
    return Namespace(
        workspace_root=str(ctx.workspace_root),
        expect=expect,
        run_log=None,  # 本轮实际产生的 run-log 由拉起步骤传递;不取最新推断(S9 硬约束2)
        end_pattern=None,
        stall_secs=600.0,
        interval=15.0,
        timeout=0,  # 常驻(--stream 模式)
        stream=True,
        events="expect",
    )


def step_watch(ctx: DispatchContext) -> dict[str, Any]:
    """步骤6:挂哨。观测面按 S2 选择,四出口语义原样保留;参数自证(S9③/S9b)。

    返回 {verdict, exit_code, expect_status, verify}。verdict ∈
    {product_landed, end_no_product, stalled, timeout}。
    """
    from tools.aipos_cli.agent_watch_fs import run_fs_watch, EXIT_CHANGE, EXIT_TIMEOUT, EXIT_END_NO_PRODUCT, EXIT_STALL

    ns = _derive_watch_namespace(ctx)
    verify = verify_sentinel_params(
        ctx.workspace_root,
        ctx.expect_derivation["patterns"],
        None,
        ctx.observation_plan,
    )
    ctx.sentinel_verify = verify
    if verify["errors"]:
        # S9 硬约束3:对不上当场报错,不空等
        return {
            "verdict": "verify_failed",
            "exit_code": EXIT_USAGE_SENTINEL,
            "verify": verify,
        }
    code = run_fs_watch(ns)
    verdict_map = {
        EXIT_CHANGE: "product_landed",
        EXIT_END_NO_PRODUCT: "end_no_product",
        EXIT_STALL: "stalled",
        EXIT_TIMEOUT: "timeout",
    }
    return {
        "verdict": verdict_map.get(code, f"exit_{code}"),
        "exit_code": code,
        "expect_status": verify["expect_status"],
        "verify": verify,
    }


EXIT_USAGE_SENTINEL = 5


# ---------------------------------------------------------------------------
# S3 — 未经泵派出的 agent 可被发现(只读检查,告警不阻止)。
# ---------------------------------------------------------------------------

def list_unmanaged_agents(
    product_repo: Path,
    workspace_root: Path,
    managed_task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """**只读**列出当前在跑但并非由泵派出的 agent(S3)。

    判据:产品仓内存在 task_cards/<ID>/ 产出活动(或工作区 events/started),
    但 <ID> 不在 managed_task_ids(泵记录的已派集合)中。仅告警,不阻止人工介入。
    用文件活动判定,不靠 pgrep 模式匹配(S9 硬约束4:清点不用会命中自身的模式)。
    """
    managed = managed_task_ids or set()
    unmanaged: list[dict[str, Any]] = []
    # 1) 产品仓内的在跑迹象:events/started_* 但无 completed_*/blocked_*
    ev_root = workspace_root / EVENTS_DIR
    if ev_root.is_dir():
        for task_dir in sorted(ev_root.iterdir()):
            if not task_dir.is_dir():
                continue
            tid = task_dir.name
            has_started = any(task_dir.glob("started_*.md"))
            has_closed = any(task_dir.glob("completed_*.md")) or any(task_dir.glob("blocked_*.md"))
            if has_started and not has_closed and tid not in managed:
                unmanaged.append({
                    "task_id": tid,
                    "signal": f"{task_dir.relative_to(workspace_root)}/started_*.md",
                    "source": "workspace_events",
                })
    return unmanaged


# ---------------------------------------------------------------------------
# 编排主入口:一条命令走完全程(S1)。dry_run = 只到步骤3(校验不派,语义不变)。
# ---------------------------------------------------------------------------

def run_pump_dispatch(
    ctx: DispatchContext,
    *,
    dry_run: bool = False,
    do_claim: bool = True,
    do_launch: bool = True,
    do_watch: bool = True,
    emit: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """编排六步。任何一步失败都响(S1 硬约束:不吞错)。

    开关用于测试与单步接管(S6① 验收:任意单步可单独调用)。
    返回 dict {ok, step, dry_run, plan, kickoff, claim, launch, watch, errors}。
    """
    _log = emit or (lambda _m: None)
    result: dict[str, Any] = {
        "ok": True, "dry_run": dry_run, "step": "init",
        "errors": [], "warnings": [],
        "plan": None, "kickoff": "", "claim": None, "launch": None, "watch": None,
    }

    def _fail(step: str, msg: str) -> dict[str, Any]:
        result["ok"] = False
        result["step"] = step
        result["errors"].append(msg)
        _log(f"[{step}] FAIL: {msg}")
        return result

    # AIPOS-338 S5: manual mode refuses auto-dispatch (pump ≠ manual-permission).
    # auto mode leaves manual /claim untouched. dry_run still previews so the
    # operator can see the mode without a real dispatch.
    try:
        from tools.aipos_cli.workspace_config import get_dispatch_mode
        mode = get_dispatch_mode(ctx.workspace_root)
        result["dispatch_mode"] = mode
        if mode == "manual" and not dry_run:
            return _fail(
                "dispatch_mode",
                "当前工作区为 manual 模式,pump run 已关闭自动派工"
                "(防泵与 Owner 同派一卡撞车)。请手动 /claim,或确认后切回 auto:"
                "`lybra project dispatch-mode set --mode auto`。",
            )
        if mode == "manual":
            result["warnings"].append(
                "当前工作区为 manual 模式;dry-run 仅预览,实际派工将被拒绝。"
            )
    except Exception:
        pass  # never block dispatch on a mode-read failure (default auto)

    # 步骤1:选择观测面
    ctx = step_build_context(ctx)
    result["plan"] = ctx.observation_plan
    result["warnings"].extend(ctx.observation_plan.get("warnings", []))
    result["step"] = "context"

    # 步骤2:生成 kickoff(三层制约,已有)
    from tools.aipos_cli.advisor_pump import generate_kickoff
    try:
        kickoff_raw = generate_kickoff(
            ctx.card_id, ctx.role, ctx.round_type, ctx.delta, ctx.workspace_root
        )
    except Exception as exc:  # noqa: BLE001 — 生成失败要响,不吞
        return _fail("generate_kickoff", str(exc))
    result["step"] = "generate_kickoff"

    # 步骤3(S7):展开占位符
    # AIPOS-332F3 修二:真派与 dry-run 同一条展开逻辑,统一调用
    # step_expand_kickoff_lenient 取得展开结果 + 缺失列表;非 dry-run 时缺失即硬失败。
    kickoff_text, missing = step_expand_kickoff_lenient(ctx, kickoff_raw)
    if dry_run:
        # F-332-03:dry-run 下缺值降级为告警 + 展示未展开占位符 + exit 0
        ctx.kickoff = kickoff_text
        result["kickoff"] = kickoff_text
        if missing:
            result["warnings"].append(
                f"dry-run 缺少占位符取值 {missing},kickoff 未完全展开(已展示未展开占位符)。"
                "非 dry-run 派工时缺值将硬失败(S7);dry-run 仅告警不中止(exit 0)。"
            )
    else:
        # 真派路径:缺值硬失败(S7 语义不变)
        if missing:
            return _fail(
                "expand_kickoff",
                f"无法展开 kickoff 占位符 {missing}:缺少对应取值。"
                " 派工中止——不输出带占位符的半成品(S7)。",
            )
        # AIPOS-332F3 修二:真派路径零占位符残留断言
        leftover = re.findall(r"\{[a-z_]+\}", kickoff_text)
        if leftover:
            return _fail(
                "expand_kickoff",
                f"kickoff 仍含未展开占位符 {leftover}(真派路径零容忍,S7)。",
            )
        ctx.kickoff = kickoff_text
        result["kickoff"] = kickoff_text
    result["step"] = "expand_kickoff"

    # dry_run 到此为止:只校验不派(现有语义保留)。但仍展示派生结果(确定性证据)。
    if dry_run:
        # 派生 expect + 哨兵自证(不需活 agent,可作 dry-run 证据)
        deriv = derive_expect_patterns(
            ctx.card_id, ctx.role,
            ctx.observation_plan.get("output_location_profile", {}),
            ctx.reviewed_task_id,
        )
        ctx.expect_derivation = deriv
        result["expect_derivation"] = deriv
        result["sentinel_verify"] = verify_sentinel_params(
            ctx.workspace_root, deriv["patterns"], None, ctx.observation_plan
        )
        result["step"] = "dry_run_ok"
        _log("[dry-run] 校验通过,未派工(--dry-run 保留现有语义)")
        return result

    # 步骤4(S11/AIPOS-332F2):认领或校验持有
    #   first 轮:走 step_claim(一发式认领,失败即止)
    #   resume/fix 轮:走 step_verify_held(校验卡已由本实例持有,不发起新 claim)
    #   三种轮次同一条编排代码,轮次差异是数据不是分支。
    if do_claim:
        if ctx.round_type == "first":
            claim = step_claim(ctx)
            result["claim"] = claim
            if not claim["ok"]:
                return _fail("claim", claim["reason"])
        else:
            # resume / fix: 跳过 claim, 校验已持有
            verify = step_verify_held(ctx)
            result["claim"] = verify  # 复用 claim 槽位,下游渲染一致
            if not verify["ok"]:
                return _fail("claim", verify["reason"])
    result["step"] = "claim"

    # 步骤5:拉起(launch-check)
    if do_launch:
        if not ctx.runtime_cmd_template:
            return _fail("launch", "缺 runtime_cmd_template,无法拉起(判断留人:派不派由人决定)")
        launch = step_launch(ctx)
        result["launch"] = launch
        if not launch["ok"]:
            return _fail("launch", f"launch-check 未确认开工(exit={launch.get('exit_code')})")
    result["step"] = "launch"

    # 步骤6:挂哨(watch)
    if do_watch:
        watch = step_watch(ctx)
        result["watch"] = watch
        if watch["verdict"] == "verify_failed":
            return _fail("watch", "哨兵参数自证失败(见 verify)")
    result["step"] = "watch"
    result["ok"] = True
    return result


def render_dispatch_plan(result: dict[str, Any]) -> str:
    """把编排结果渲染为人可读的派生计划(供 dry-run 与 RETURN 取证)。"""
    lines: list[str] = []
    ok = "✓" if result["ok"] else "✗"
    lines.append(f"{ok} pump dispatch — step={result['step']} dry_run={result['dry_run']}")
    plan = result.get("plan") or {}
    if plan:
        lines.append(f"  观测面: 运行体={plan.get('runtime_type')} 产出位置={plan.get('output_location')}")
        lines.append(f"    worktree判据={plan.get('worktree_criterion')} 停滞面={plan.get('stall_surfaces')} run_log角色={plan.get('run_log_role')}")
        lines.append(f"    expect来源={plan.get('expect_source')} 监听产品仓={plan.get('monitors_product_repo')}")
    for w in result.get("warnings", []):
        lines.append(f"  [提示] {w}")
    if result.get("kickoff"):
        lines.append("  kickoff(已展开占位符):")
        for ln in result["kickoff"].splitlines():
            lines.append(f"    | {ln}")
        # S7 断言:残留占位符检测
        import re as _re
        leftover = _re.findall(r"\{[a-z_]+\}", result["kickoff"])
        lines.append(f"  [S7] 未展开占位符残留: {leftover if leftover else '无(成品可直接投递)'}")
    if result.get("errors"):
        lines.append("  错误:")
        for e in result["errors"]:
            lines.append(f"    - {e}")
    return "\n".join(lines)
