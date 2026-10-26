"""AIPOS-F187 件① — `lybra kickoff`: 子 agent 开工稿由产品生成。

一句话: 顾问派执行 / 审计子 agent 时不再手写开工稿, `lybra kickoff --task-id <卡>` 给出一份自足开工稿(顾问可在其后追加本卡特别说明)。

防碎片化: 开工稿是渲染, 不是第二份规则文本——骨架与句子只在 verbs.schema lybra_kickoff(layout / texts / item_templates / refusals),
值全部取自既有单源:
  - 卡面(task_id / title / lane / claimed_by / carried_from)与开工面: next_resolver.card_workstation_view(工作树 / 报告落点 /
    报告必填字段 / 承继来源), 报告必填行式 = verbs.schema lybra_my_tasks.kickoff.report_field_line(next_resolver.render_report_field_lines)
  - 可开工判据: next_resolver.kickoff_refusal(与工位 /go 同一判据)
  - 实例身份 / 章程可取 / 门地址 / 工位根: charter_render.instance_charter(与 `lybra charter` 同一判定); 章程命令 = charter_pull_command
  - 写权限边界与全角色硬规矩: roles.schema write_boundary(write_boundary.build_write_boundary)
  - 跨机临时验证根(AIPOS-F190 件④): 本卡本实例的 project.json validation_roots 声明; AIPOS-F212 件①: 节与拒因只取开工面
    card_workstation_view(validation_root_lines / validation_root_refusal, 与工位 /go 同读同码, 本模块不重算; 无声明 = 该节省略)
  - 门交互硬规矩: hard_rules_extractor.gate_hard_rules(AIPOS-F212 件②: 与章程硬规矩节同读同序同数; 项目 paths.hard_rules_source § 0.5,
    未声明 = roles.schema gate_hard_rules.fallback 按角色类)
  - 全量命令与时限: charter_render.runall_context(project.json test_contract); 长命令: run_long.charter_commands
  - 时限措辞(AIPOS-F189 件②): 卡 harness(开工面 card_workstation_view.harness = loop_driver.card_harness)的单次工具时限 enums.schema harness.<值>.tool_timeout_seconds
    (harness_tool_timeout 唯一读取口): 全量时限在其内 = 同步跑写设足秒数; 超限 = 一律 run-long; 未声明 = 不写秒数只教 run-long
  - 草稿目录: project.json paths.scratch_root(未声明 = 系统临时目录; 落治理根内 = 拒)
  - main 侧基线(AIPOS-F188 件②): 命令 = post_merge_regression.audit_baseline_command, 现状 = post_merge_regression.audit_baseline
    (三态 recorded / pending / self_run_required, 唯一判定; 开工稿只引用其结论, 措辞读 texts.baseline_state_<态>)
只读: 不写文件、不连门、不起进程。
"""
from __future__ import annotations

import re
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any

KICKOFF_VERB = "lybra_kickoff"
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
TEXT_KEYS = ("ratchet_default", "runall_absent", "runall_absent_timeout", "lane_paths_absent", "repo_line_multi", "repo_line_single",
             "commands_rules_title", "scratch_source_declared", "scratch_source_default", "host_hint_alias",
             "host_hint_undeclared", "baseline_state_recorded", "baseline_state_pending", "baseline_state_self_run_required",
             "baseline_state_unavailable", "runall_timing_sync", "runall_timing_run_long", "runall_timing_undeclared",
             "tool_timeout_source", "runall_timing_charter", "run_long_threshold_charter", "run_long_threshold_declared",
             "run_long_threshold_undeclared", "runall_timeout_derived")
ITEM_KEYS = ("boundary_line", "hard_rule_line", "commands_rule_line", "no_write_line")
REFUSAL_KEYS = ("KICKOFF_ROLE_MISMATCH", "KICKOFF_INSTANCE_MISSING", "KICKOFF_NOT_STARTABLE", "KICKOFF_CHARTER_REFUSED",
                "KICKOFF_WORKTREE_UNRESOLVED", "KICKOFF_REPORT_UNRESOLVED", "KICKOFF_SCRATCH_IN_GOVERNANCE",
                "KICKOFF_VALIDATION_ROOT_UNRESOLVED", "KICKOFF_HARNESS_UNRESOLVED", "KICKOFF_HARNESS_MISMATCH")


class KickoffRefused(ValueError):
    """开工稿拒(code ∈ verbs.schema lybra_kickoff.refusals; message = 拒因原文与出口)。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _contract_roles(contract: dict[str, Any]) -> tuple[str, ...]:
    """角色集 = 声明参数 role 的值域(AIPOS-F214 件④: $enum kickoff_role 引用 enums.schema, 经 schema_loader.resolve_field_enum 唯一解析;
    不在代码里另列角色元组)。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, resolve_field_enum

    param = (((contract.get("parameters") or {}).get("properties") or {}).get("role")) or {}
    roles = tuple(resolve_field_enum(param, code_repo_schema_root()) or ()) if isinstance(param, dict) else ()
    if not roles:
        raise SchemaLoadError(f"verbs.schema.json verbs.{KICKOFF_VERB}.parameters.properties.role 值域未声明($enum kickoff_role)")
    return roles


def kickoff_roles() -> tuple[str, ...]:
    """AIPOS-F214 件④: 开工稿角色值域唯一读取口(CLI `lybra kickoff --role` choices 与开工稿判角色同读; 声明 enums.schema kickoff_role)。"""
    return _contract_roles(kickoff_contract())


def kickoff_contract() -> dict[str, Any]:
    """verbs.schema lybra_kickoff 唯一读取口(layout / texts / item_templates / refusals / role_labels)。缺键 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    contract = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(KICKOFF_VERB)
    if not isinstance(contract, dict):
        raise SchemaLoadError(f"verbs.schema.json verbs.{KICKOFF_VERB} 未声明")
    roles = _contract_roles(contract)
    layout, texts = contract.get("layout") or {}, contract.get("texts") or {}
    items, refusals, labels = contract.get("item_templates") or {}, contract.get("refusals") or {}, contract.get("role_labels") or {}
    problems = [f"layout.{r}" for r in roles if not isinstance(layout.get(r), list) or not layout[r]]
    problems += [f"texts.{k}" for k in TEXT_KEYS if not isinstance(texts.get(k), str)]
    problems += [f"texts.{k}" for r in roles for k in (layout.get(r) or []) if k and not isinstance(texts.get(k), str)]
    problems += [f"item_templates.{k}" for k in ITEM_KEYS if not isinstance(items.get(k), str)]
    problems += [f"refusals.{k}" for k in REFUSAL_KEYS if not isinstance(refusals.get(k), str)]
    problems += [f"role_labels.{r}" for r in roles if not isinstance(labels.get(r), str)]
    if problems:
        raise SchemaLoadError(f"verbs.schema.json verbs.{KICKOFF_VERB} 未声明齐: {sorted(set(problems))}")
    return contract


def harness_tool_timeout(harness: str) -> int | None:
    """AIPOS-F189 件②(gap #191): harness 单次工具调用时限(秒)= enums.schema harness.values[<harness>].tool_timeout_seconds(唯一读取口)。
    未声明 = None(开工稿不写具体秒数, 只教 run-long); harness 不在值域 / 值非正整数 = 声明错误(SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("enums", code_repo_schema_root()).get("enums") or {}).get("harness")
    values = decl.get("values") if isinstance(decl, dict) else None
    if not isinstance(values, list):
        raise SchemaLoadError("enums.schema.json enums.harness.values 未声明")
    item = next((v for v in values if isinstance(v, dict) and str(v.get("value")) == harness), None)
    if item is None:
        raise SchemaLoadError(f"卡面 harness {harness!r} 不在 enums.schema harness 值域 {[v.get('value') for v in values if isinstance(v, dict)]}")
    if "tool_timeout_seconds" not in item:
        return None
    value = item["tool_timeout_seconds"]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchemaLoadError(f"enums.schema.json harness.{harness}.tool_timeout_seconds={value!r} 须为正整数(秒)")
    return value


def runall_timing(texts: dict[str, Any], harness: str | None, timeout_seconds: int, runall_timeout: str) -> tuple[str, str]:
    """AIPOS-F189 件②: 开工稿全量时限句(措辞读 texts.runall_timing_<态>)。返回 (态, 句子); 态 ∈ sync / run_long / undeclared / charter。
    态判据唯一在此: 卡面 harness 声明了单次工具时限且全量时限(post_merge_regression.timeout_seconds)不超过它 = sync; 超过 = run_long; 未声明 = undeclared。
    AIPOS-F212 件③: 时限句只由本函数产出——章程不按卡(harness=None)= charter 态, 句子只指向开工稿时限句并给全量时限声明
    (审计母本占位 {{runall_timing_charter}}, 母本不写死「显式传 timeout = 全量时限」之类句子)。"""
    if harness is None:
        return "charter", str(texts["runall_timing_charter"]).format(runall_timeout=runall_timeout)
    tool_timeout = harness_tool_timeout(harness)
    source = str(texts["tool_timeout_source"]).format(harness=harness)
    if tool_timeout is None:
        state = "undeclared"
    else:
        state = "sync" if int(timeout_seconds) <= tool_timeout else "run_long"
    sentence = str(texts[f"runall_timing_{state}"]).format(harness=harness, tool_timeout=tool_timeout, tool_timeout_source=source,
                                                          runall_timeout=runall_timeout)
    return state, sentence


def run_long_threshold(texts: dict[str, Any], harness: str | None) -> str:
    """AIPOS-F212 件④: run-long 门槛短语(与声明的单次工具时限同口径, 不再写「预计超过 5 分钟」)唯一产出; 措辞读
    texts.run_long_threshold_<charter|declared|undeclared>。harness=None = 章程态(秒数以开工稿时限句为准); 卡 harness 声明了
    tool_timeout_seconds = 写该秒数; 未声明 = 不写秒数(长命令一律 run-long)。章程占位 {{run_long_threshold}} 与开工稿 verify_long 同读。"""
    if harness is None:
        return str(texts["run_long_threshold_charter"])
    tool_timeout = harness_tool_timeout(harness)
    key = "run_long_threshold_undeclared" if tool_timeout is None else "run_long_threshold_declared"
    return str(texts[key]).format(harness=harness, tool_timeout=tool_timeout)


def read_discipline_rule(contract: dict[str, Any] | None = None) -> str:
    """AIPOS-F240 件③: 大文件读法纪律唯一渲染(声明 verbs.schema lybra_kickoff.read_discipline: text 占位 {threshold_chars})。开工稿
    texts.read_discipline、章程占位 {{read_discipline_rule}}(charter_timing_values 带出)、loop 运行中上下文超限续接指令 / 新会话摘要同读。
    缺 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = (contract or kickoff_contract()).get("read_discipline")
    threshold = decl.get("threshold_chars") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not isinstance(decl.get("text"), str) or "{threshold_chars}" not in decl["text"]
            or isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0):
        raise SchemaLoadError(f"verbs.schema.json verbs.{KICKOFF_VERB}.read_discipline 未声明齐(text 含 {{threshold_chars}}, threshold_chars 为正整数; AIPOS-F240)")
    return str(decl["text"]).format(threshold_chars=threshold)


def charter_timing_values(runall_timeout: str) -> dict[str, str]:
    """AIPOS-F212 件③④: 章程渲染上下文的时限两键(charter_render.charter_render_context 唯一调用; 章程不按卡 = charter 态)。
    AIPOS-F240 件③: 另带读法纪律 read_discipline_rule(章程占位 {{read_discipline_rule}}, 与开工稿同一渲染 read_discipline_rule)。"""
    contract = kickoff_contract()
    texts = contract["texts"]
    return {"runall_timing_charter": runall_timing(texts, None, 0, runall_timeout)[1], "run_long_threshold": run_long_threshold(texts, None),
            "read_discipline_rule": read_discipline_rule(contract)}


def _refuse(contract: dict[str, Any], refusal_code: str, **values: Any) -> KickoffRefused:
    return KickoffRefused(refusal_code, str(contract["refusals"][refusal_code]).format(**values))


def scratch_dir(governance_root: Path, project: str, task_id: str, contract: dict[str, Any] | None = None) -> tuple[Path, str]:
    """草稿目录 = paths.scratch_root/<卡ID 小写>(未声明 = 系统临时目录下 config.schema paths.scratch_root.default_system_temp)。
    返回 (目录, 来源说明)。落在治理根内 = KickoffRefused(KICKOFF_SCRATCH_IN_GOVERNANCE)——草稿永不落治理根。"""
    from tools.aipos_cli.workspace_config import _project_paths_declaration, project_paths
    from tools.schema_loader import SchemaLoadError

    contract = contract or kickoff_contract()
    texts = contract["texts"]
    gov = Path(governance_root).expanduser().resolve()
    declared = project_paths(gov).get("scratch_root")
    if declared is not None:
        base, source = Path(declared).expanduser(), str(texts["scratch_source_declared"])
    else:
        template = (_project_paths_declaration().get("scratch_root") or {}).get("default_system_temp")
        if not isinstance(template, str) or "{project}" not in template:
            raise SchemaLoadError("config.schema.json project_json.paths.scratch_root.default_system_temp 未声明(须含 {project})")
        base = Path(tempfile.gettempdir()) / template.replace("{project}", project)
        source = f"{texts['scratch_source_default']} {tempfile.gettempdir()}"
    directory = base / task_id.lower()
    resolved = directory.resolve()
    if resolved == gov or gov in resolved.parents:
        raise _refuse(contract, "KICKOFF_SCRATCH_IN_GOVERNANCE", scratch_dir=directory, governance_root=gov, project=project)
    return directory, source


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise ValueError(f"git {' '.join(args)} 失败: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def _other_project_roots(governance_root: Path) -> list[str]:
    """同一 home 根(治理根的上级, AIPOS-226 home 模型: 项目根 = <home>/<项目>)下其他已建项目的治理根。"""
    from tools.aipos_cli.workspace_config import _project_candidates

    gov = Path(governance_root).resolve()
    return [str(gov.parent / name) for name in _project_candidates(gov.parent) if (gov.parent / name).resolve() != gov]


def _repo_name(governance_root: Path, code_repo: Path) -> str | None:
    """卡仓在 project.json repos.items 中的仓名; 单仓项目(无 repos 段)= None。"""
    from tools.aipos_cli.workspace_config import project_repos

    repos = project_repos(governance_root)
    if not repos["declared"]:
        return None
    for name, path in repos["items"].items():
        if Path(path).resolve() == Path(code_repo).resolve():
            return str(name)
    return None


def _render(contract: dict[str, Any], role: str, values: dict[str, Any]) -> str:
    """按 layout 逐行渲染: 占位值 None = 该行省略(本卡不适用); 占位未知 / 值非串 = KickoffRenderError(fail-closed)。"""
    from tools.aipos_cli.next_resolver import KickoffRenderError

    lines: list[str] = []
    for key in contract["layout"][role]:
        if not key:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        template = str(contract["texts"][key])
        names = _PLACEHOLDER_RE.findall(template)
        unknown = [n for n in names if n not in values]
        if unknown:
            raise KickoffRenderError(f"verbs.schema lybra_kickoff.texts.{key} 占位 {unknown} 未知")
        if any(values[n] is None for n in names):
            continue
        bad = [n for n in names if not isinstance(values[n], str)]
        if bad:
            raise KickoffRenderError(f"lybra_kickoff.texts.{key} 占位 {bad} 值非串")
        lines.append(_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template))
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def baseline_state(governance_root: Path, code_repo: Path, contract: dict[str, Any], base: str) -> dict[str, str]:
    """AIPOS-F188 件②: 开工稿验证节的 main 侧基线——命令(audit_baseline_command)与开工稿生成时的现状一行(三态, 唯一判定
    post_merge_regression.audit_baseline, 本函数不另写判据)。sha = 卡仓基线分支当前 HEAD(resolve_audit_sha 同一解析)。
    查询失败 = baseline_state_unavailable 措辞写明原文(不吞, 不猜成已记录)。返回 {baseline_command, baseline_sha, baseline_state, status}。"""
    from tools.aipos_cli import post_merge_regression as pmr
    from tools.schema_loader import SchemaLoadError

    texts = contract["texts"]
    labels = pmr.baseline_state_labels()  # AIPOS-F212 件④: 三态标签唯一声明(措辞模板只引 {label})
    command = pmr.audit_baseline_command(governance_root, code_repo)
    try:
        sha, _source = pmr.resolve_audit_sha(governance_root, None, code_repo)
        result = pmr.audit_baseline(governance_root, sha)
    except (ValueError, OSError, SchemaLoadError) as exc:
        return {"baseline_command": command, "baseline_sha": "(不可解析)", "status": "unavailable",
                "baseline_state": str(texts["baseline_state_unavailable"]).format(
                    error=exc, base=base, label_self_run_required=labels[pmr.AUDIT_BASELINE_SELF_RUN])}
    status = str(result["status"])
    if status == pmr.AUDIT_BASELINE_RECORDED:
        line = str(texts["baseline_state_recorded"]).format(ref=result.get("ref"), failure_count=len(result.get("failures") or []), base=base,
                                                            label=labels[status])
    elif status == pmr.AUDIT_BASELINE_PENDING:
        pending = result.get("pending") or {}
        line = str(texts["baseline_state_pending"]).format(
            task_id=pending.get("task_id"), started_at=pending.get("started_at") or "(未记)", deadline=pending.get("deadline") or "(不可推算)",
            timeout_seconds=pending.get("timeout_seconds") or "(未记)", base=base, label=labels[status])
    else:
        line = str(texts["baseline_state_self_run_required"]).format(reason="; ".join(result.get("reasons") or []), base=base,
                                                                     label=labels[pmr.AUDIT_BASELINE_SELF_RUN])
    return {"baseline_command": command, "baseline_sha": sha, "baseline_state": line, "status": status}


def _item_lines(template: str, rows: list[dict[str, Any]]) -> str | None:
    out = [_PLACEHOLDER_RE.sub(lambda m, row=row: str(row.get(m.group(1), "")), template) for row in rows]
    return "\n".join(out) if out else None


def _report_code_rule(report_dir: Path) -> str | None:
    from tools.aipos_cli.governance_guardrails import slot_code_rule_text

    return slot_code_rule_text(f"`{report_dir}/`")


def render_kickoff_brief(governance_root: str | Path, task_id: str, *, role: str | None = None) -> dict[str, Any]:
    """一张已认领卡的子 agent 开工稿(唯一实现)。返回 {ok, task_id, role, instance, text, values}。
    拒 = KickoffRefused(code, 原文); 声明 / 治理根坏 = SchemaLoadError / ValueError / OSError 上抛(fail-closed)。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.charter_render import CharterRefused, charter_pull_command, instance_charter, runall_context
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.hard_rules_extractor import gate_hard_rules
    from tools.aipos_cli.next_resolver import (
        card_base_branch, card_branch_name, card_workstation_view, kickoff_refusal, render_report_field_lines, report_refusal_clause,
    )
    from tools.aipos_cli.run_long import charter_commands
    from tools.aipos_cli.task_loader import find_task_card
    from tools.aipos_cli.workspace_config import read_project_json
    from tools.aipos_cli.validation_common import report_write_last_rule
    from tools.aipos_cli.write_boundary import build_write_boundary, no_write_lines, role_rows

    contract = kickoff_contract()
    texts, items = contract["texts"], contract["item_templates"]
    gov = Path(governance_root).expanduser().resolve()
    project = str(read_project_json(gov).get("project") or "").strip()
    if not project:
        raise ValueError(f"{gov}/project.json 缺 project 字段")
    task_id = str(task_id or "").strip()
    card_path, state = find_task_card(gov, task_id)
    fm: dict[str, Any] = {}
    if card_path is not None:
        fm, _body = require_frontmatter(card_path)
        fm = {**fm, "task_id": str(fm.get("task_id") or task_id)}
    card_role = "auditor" if is_audit_card(task_id, fm) else "executor"
    labels = contract["role_labels"]
    if role and role != card_role:
        raise _refuse(contract, "KICKOFF_ROLE_MISMATCH", task_id=task_id, card_role_label=labels[card_role], role=role, card_role=card_role)
    instance = str(fm.get("claimed_by") or fm.get("assigned_to") or "").strip()
    if card_path is not None and not instance:
        raise _refuse(contract, "KICKOFF_INSTANCE_MISSING", task_id=task_id)
    refusal = kickoff_refusal(gov, task_id, instance, queue_state=state, card_frontmatter=fm)
    if refusal:
        raise _refuse(contract, "KICKOFF_NOT_STARTABLE", task_id=task_id, code=refusal["code"], reason=refusal["reason"])
    view = card_workstation_view(gov, task_id, fm)
    # AIPOS-F212 件①: 验证根节 / 卡 harness 不可得 = 与 /go 同码拒(开工稿给出口, 不抛异常; /go 同判不入选)。先于章程判:
    # 验证根声明形坏时章程通用段同样读不出, 拒因以开工面为准(两面同码同原文)
    vr_refusal = view.get("validation_root_refusal") or {}
    if vr_refusal:
        raise _refuse(contract, "KICKOFF_VALIDATION_ROOT_UNRESOLVED", task_id=task_id, code=vr_refusal.get("code"), reason=vr_refusal.get("reason"))
    harness_refusal = view.get("harness_refusal") or {}
    if harness_refusal:  # 时限句按卡 harness 产出, harness 不可推导 = 拒(原文含出口), 不猜
        raise _refuse(contract, "KICKOFF_HARNESS_UNRESOLVED", task_id=task_id, code=harness_refusal.get("code"), reason=harness_refusal.get("reason"))
    mismatch = view.get("harness_mismatch") or {}
    if mismatch:  # AIPOS-F235 件③: 卡面 harness ≠ 派发顾问现登记 = 时限句 / 派发说明会错, 拒并给受限 amend 出口原文
        raise _refuse(contract, "KICKOFF_HARNESS_MISMATCH", reason=mismatch["reason"], amend_command=mismatch["amend_command"])
    try:
        charter = instance_charter(gov, role_class=card_role, instance=instance)
    except CharterRefused as exc:
        raise _refuse(contract, "KICKOFF_CHARTER_REFUSED", instance=instance, reason=str(exc)) from exc

    report_refusal = view.get("report_refusal") or {}
    if not view.get("report_path") or report_refusal:
        raise _refuse(contract, "KICKOFF_REPORT_UNRESOLVED", task_id=task_id, code=report_refusal.get("code") or "REPORT_LOCATION_UNDECLARED",
                      reason=report_refusal.get("reason") or "")
    worktree_refusal = view.get("worktree_refusal") or {}
    needs_worktree = view.get("worktree_required") is not False
    if needs_worktree and (worktree_refusal or not view.get("worktree_exists")):
        raise _refuse(contract, "KICKOFF_WORKTREE_UNRESOLVED", task_id=task_id, code=worktree_refusal.get("code") or "WORKTREE_NOT_CREATED",
                      reason=worktree_refusal.get("reason") or "")

    reviewed = audit_card_reviewed_id(task_id, fm) if card_role == "auditor" else ""
    base = card_base_branch()
    worktree = Path(view["worktree_path"]) if needs_worktree else None
    code_repo = branch = base_sha = tip = repo_line = None
    baseline: dict[str, Any] = {"baseline_command": None, "baseline_sha": None, "baseline_state": None, "status": None}
    if worktree is not None:
        from tools.aipos_cli.next_resolver import card_worktree_location

        code_repo, _tree = card_worktree_location(gov, task_id, fm)
        branch = card_branch_name(reviewed or task_id)
        try:
            tip = _git(worktree, "rev-parse", "HEAD")
            base_sha = _git(Path(code_repo), "merge-base", base, tip)
        except ValueError as exc:
            raise _refuse(contract, "KICKOFF_WORKTREE_UNRESOLVED", task_id=task_id, code="WORKTREE_GIT_UNREADABLE", reason=str(exc)) from exc
        name = _repo_name(gov, Path(code_repo))
        repo_line = (texts["repo_line_multi"].format(repo_name=name, code_repo=code_repo) if name
                     else texts["repo_line_single"].format(code_repo=code_repo))
        baseline = baseline_state(gov, Path(code_repo), contract, base)  # AIPOS-F188 件②
    reviewed_card = find_task_card(gov, reviewed)[0] if reviewed else None
    lane_fm = fm
    if reviewed_card is not None and not isinstance(fm.get("lane"), dict):
        lane_fm, _rbody = require_frontmatter(reviewed_card)  # 审计卡无车道 = 被审卡车道(审计看的改动面)
    lane = lane_fm.get("lane") if isinstance(lane_fm.get("lane"), dict) else {}
    lane_paths = [str(p) for p in (lane.get("paths") or []) if str(p).strip()]

    # 会话所在机 ≠ 治理根所在机 = 章程拉取式 + remote_note(charter_render.charter_pull_command 唯一拼装)
    gate_host = socket.gethostname()
    session_host = str(charter.get("session_host") or "").strip()
    remote = bool(session_host) and session_host != gate_host
    pull = charter_pull_command(gov, instance=instance, role_class=card_role)
    if pull["host_declared"]:
        host_hint = texts["host_hint_alias"].format(alias=pull["host"])
    else:
        host_hint = texts["host_hint_undeclared"].format(hint=pull["host_hint"].lstrip("; "))

    boundary = build_write_boundary(gov, role=str(charter["role"]), harness_root=charter["harness_root"])
    rows = role_rows(boundary, str(charter["role"]), task_id=task_id)  # AIPOS-F212 件④: 选行 / 落卡与章程表同一实现
    # AIPOS-F215 件③: 禁写行(治理根 / 产品仓只读 / 工位)= 同一选行派生(roles.schema write_boundary.no_write); 子 agent 无工位 = 不出工位行
    no_write = no_write_lines(boundary, str(charter["role"]), task_id=task_id,
                              omit_surfaces=() if charter.get("workstation") else ("harness_root",))
    # AIPOS-F212 件②: 门交互硬规矩 = gate_hard_rules(与章程硬规矩节同读同序同数; 未声明来源 = 同一 fallback, 声明了读不出 = 同拒)
    gate_rules = gate_hard_rules(gov, role_class=card_role)
    commands_rules = "\n".join([texts["commands_rules_title"].format(source=gate_rules["source_ref"])]
                               + [items["commands_rule_line"].format(rule=rule) for rule in gate_rules["rules"]])

    scratch, scratch_source = scratch_dir(gov, project, task_id, contract)
    runall = runall_context(gov, code_repo, log_dir=str(scratch))
    test_contract = runall["test_contract"]
    long_cmds = charter_commands(gov, task_id=task_id, cwd=str(worktree) if worktree is not None else None)
    report_path = Path(str(view["report_path"]))
    others = _other_project_roots(gov)
    # AIPOS-F189 件②: 全量时限句按本卡 harness 的单次工具时限(审计卡 = 审计卡的 harness; 同 loop 拉起判据 card_harness)
    # AIPOS-F212 件①: harness 与验证根节一律取开工面 card_workstation_view(与工位 /go 同读, 拒因同码), 不在此重算
    harness = view.get("harness")
    validation_lines = list(view.get("validation_root_lines") or [])
    evidence_lines = _candidate_evidence_lines(gov, task_id=task_id, fm=fm, instance=instance, reviewed=reviewed,
                                               reviewed_card=reviewed_card) if card_role == "auditor" else []
    if runall["runall"]:
        # AIPOS-F237 件②: 全量时限 = post_merge_regression.full_run_timeout(唯一推导; loop 等交回上限 / 合并后回归同读)。未开推导 =
        # 声明值与 charter_render.runall_context 同一措辞(行为不变); 开了 = 推导值 + 依据(措辞读 texts.runall_timeout_derived)
        from tools.aipos_cli.post_merge_regression import full_run_timeout

        full = full_run_timeout(gov, code_repo)
        runall_timeout = (str(texts["runall_timeout_derived"]).format(seconds=full["timeout_seconds"], source=full["source"], basis=full["basis"])
                          if full["derived"] else runall["runall_timeout_seconds"])
        timing_state, timing = runall_timing(texts, harness, int(full["timeout_seconds"]), runall_timeout)
    else:
        timing_state, timing = "absent", str(texts["runall_absent_timeout"])
    from tools.aipos_cli.post_merge_regression import baseline_state_labels

    baseline_labels = baseline_state_labels()
    values: dict[str, Any] = {
        "project": project, "task_id": task_id, "title": str(fm.get("title") or task_id), "instance": instance,
        "role_label": labels[card_role], "reviewed_task_id": reviewed or None,
        "charter_command": pull["command"] if remote else pull["local_command"],
        "card_path": str(card_path), "reviewed_card_path": str(reviewed_card) if reviewed_card else None,
        "worktree_path": str(worktree) if worktree is not None else None,
        "no_worktree_reason": None if worktree is not None else str(worktree_refusal.get("reason") or ""),
        "branch": branch, "base": base, "base_sha": base_sha, "tip": tip,
        "baseline_command": baseline["baseline_command"], "baseline_sha": baseline["baseline_sha"],
        "baseline_state": baseline["baseline_state"],
        **{f"label_{state}": label for state, label in baseline_labels.items()},
        "code_repo": str(code_repo) if code_repo else None, "repo_line": repo_line,
        "lane_paths": ", ".join(f"`{p}`" for p in lane_paths) if lane_paths else str(texts["lane_paths_absent"]),
        "carried_line": view.get("carried_line"),
        "session_host": session_host if remote else None, "gate_host": gate_host, "host_hint": host_hint,
        "boundary_lines": _item_lines(items["boundary_line"], rows),
        "no_write_boundary_lines": _item_lines(items["no_write_line"], [{"text": line} for line in no_write]),
        "validation_root_lines": "\n".join(validation_lines) if validation_lines else None,
        "runtime_evidence_lines": "\n".join(evidence_lines) if evidence_lines else None,  # AIPOS-F199 件②: 验证卡审计证据步骤
        "governance_root": str(gov), "report_dir": str(report_path.parent),
        "other_projects": ", ".join(f"`{p}`" for p in others) if others else None,
        "harness_root": str(charter["harness_root"]) if charter.get("workstation") else None,
        "gate_url": str(charter["gate_url"]),
        "hard_rule_lines": _item_lines(items["hard_rule_line"], boundary["hard_rules"]),
        "commands_rule_lines": commands_rules,
        "scratch_dir": str(scratch), "scratch_source": scratch_source,
        "read_discipline": read_discipline_rule(contract),  # AIPOS-F240 件③: 大文件读法纪律(与章程同一渲染)
        "runall_command": runall["runall_command"] if runall["runall"] else str(texts["runall_absent"]),
        "runall_timing": timing, "harness": harness, "run_long_threshold": run_long_threshold(texts, harness),
        "run_long_start": long_cmds["run_long_start"], "run_long_wait": long_cmds["run_long_wait"],
        "ratchet": test_contract.get("ratchet") or str(texts["ratchet_default"]),
        "residual_pattern": test_contract.get("residual_processes"),
        "report_path": str(report_path),
        "refusal_clause": report_refusal_clause(),  # AIPOS-F215 件③: 拒收条款唯一声明(与 /go 开工提示、章程同读)
        "report_write_last_rule": report_write_last_rule(str(report_path)),  # AIPOS-F215 件②: 报告最后落盘(与章程同一渲染)
        # AIPOS-F195 件①: 报告落点不得放代码文件(与章程同一渲染; 声明缺 = 该行省略)
        "report_code_rule": _report_code_rule(report_path.parent),
        "report_field_lines": "\n".join(f"  {line}" for line in render_report_field_lines(view.get("report_required_frontmatter"))),
        # AIPOS-F227 件①: 被审卡交付主体为固定候选 = 「FAIL 时写 fail_scope」说明(next_resolver.fail_scope_audit_rule, 审计卡取证锚点同读)
        "fail_scope_rule": _fail_scope_rule(reviewed_card) if card_role == "auditor" else None,
        # AIPOS-F230 件⑥: 返工只涉交付物 = 可引用上一轮对同一候选的运行证据(next_resolver.audit_evidence_reuse_line 唯一渲染; 不可复用 = 省略)
        "evidence_reuse_rule": _evidence_reuse_rule(gov, task_id, reviewed, reviewed_card) if card_role == "auditor" else None,
    }
    text = _render(contract, card_role, values)
    return {"ok": True, "task_id": task_id, "role": card_role, "instance": instance, "text": text,
            "values": {k: v for k, v in values.items() if v is not None},
            "sources": {"charter": charter["distribution_id"], "ratchet": test_contract.get("ratchet_source"),
                        "residual_processes": test_contract.get("residual_processes_source"), "scratch": scratch_source,
                        "remote": remote, "baseline_status": baseline["status"], "runall_timing": timing_state}}


def _evidence_reuse_rule(gov: Path, task_id: str, reviewed: str, reviewed_card: Path | None) -> str | None:
    """AIPOS-F230 件⑥: 审计开工稿「运行证据复用」一行; 被审卡非固定候选 / 不可复用 = None(该行省略)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.next_resolver import audit_evidence_reuse_line, task_mode_subject_is_candidate

    if reviewed_card is None or not reviewed:
        return None
    reviewed_fm, _body = require_frontmatter(reviewed_card)
    if not task_mode_subject_is_candidate(str(reviewed_fm.get("task_mode") or "")):
        return None
    from tools.aipos_cli.next_resolver import CandidateSubjectError, card_candidate_subject

    try:
        candidate = str((card_candidate_subject(reviewed_fm) or {}).get("candidate") or "")
    except CandidateSubjectError:
        candidate = ""  # 候选声明不合: 裁决入口另按候选判据拒收, 此处只渲染说明
    return audit_evidence_reuse_line(gov, task_id, candidate=candidate, reviewed=reviewed)


def _fail_scope_rule(reviewed_card: Path | None) -> str | None:
    """AIPOS-F227 件①: 审计开工稿交回节「FAIL 时写 fail_scope」一行; 被审卡非固定候选 / 无被审卡 = None(该行省略)。
    被审卡候选声明不合 = 照出(裁决入口另按候选判据拒收, 不在此另判)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.next_resolver import fail_scope_audit_rule, task_mode_subject_is_candidate

    if reviewed_card is None:
        return None
    reviewed_fm, _body = require_frontmatter(reviewed_card)
    return fail_scope_audit_rule() if task_mode_subject_is_candidate(str(reviewed_fm.get("task_mode") or "")) else None


def _candidate_evidence_lines(gov: Path, *, task_id: str, fm: dict[str, Any], instance: str, reviewed: str,
                              reviewed_card: Path | None) -> list[str]:
    """AIPOS-F199 件②: 审计开工稿「验证卡运行证据步骤」(被审卡交付主体为固定候选且声明 audit_runtime_evidence.required; 派审注入同一渲染
    artifact_ingest.render_candidate_evidence_steps)。不适用 = []。声明 / 候选不合 = 拒因一行(开工照常, 裁决入口同判据 fail-closed)。"""
    from tools.aipos_cli.artifact_ingest import (
        RuntimeEvidenceDeclarationError, render_candidate_evidence_steps, runtime_evidence_requirement,
    )
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.next_resolver import CandidateSubjectError, card_candidate_subject

    if not reviewed or reviewed_card is None:
        return []
    reviewed_fm, _body = require_frontmatter(reviewed_card)
    try:
        if card_candidate_subject(reviewed_fm) is None:
            return []
        requirement = runtime_evidence_requirement(fm, reviewed_fm)
        if requirement is None:
            return []
        return render_candidate_evidence_steps(gov, audit_task_id=task_id, audit_instance=instance, reviewed_task_id=reviewed,
                                               reviewed_fm=reviewed_fm, requirement=requirement)
    except (CandidateSubjectError, RuntimeEvidenceDeclarationError) as exc:
        return [f"### 验证卡运行证据步骤(AIPOS-F199): 渲染不出({exc}); 裁决入口将同判据拒收, 停线报告顾问修正卡面"]


def run_kickoff_cli(args: Any) -> int:
    """`lybra kickoff` 薄壳: stdout 只出开工稿(或 --json); 拒因走 stderr; 退出码读 verbs.schema lybra_kickoff.exit_codes。"""
    import json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.next_resolver import KickoffRenderError
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    try:
        gov = Path(_find_repo_root_for_args(args))
        result = render_kickoff_brief(gov, str(args.task_id), role=getattr(args, "role", None))
    except KickoffRefused as exc:
        print(f"lybra kickoff: 拒({exc.code}): {exc.message}", file=sys.stderr)
        return declared_exit_code(KICKOFF_VERB, "refused")
    except (FileNotFoundError, SchemaLoadError, KickoffRenderError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"lybra kickoff: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(KICKOFF_VERB, "unreadable")
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(result["text"])
    return declared_exit_code(KICKOFF_VERB, "ok")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
