"""AIPOS-F187 件① — `lybra kickoff`: 子 agent 开工稿由产品生成。

一句话: 顾问派执行 / 审计子 agent 时不再手写开工稿, `lybra kickoff --task-id <卡>` 给出一份自足开工稿(顾问可在其后追加本卡特别说明)。

防碎片化: 开工稿是渲染, 不是第二份规则文本——骨架与句子只在 verbs.schema lybra_kickoff(layout / texts / item_templates / refusals),
值全部取自既有单源:
  - 卡面(task_id / title / lane / claimed_by / carried_from)与开工面: next_resolver.card_workstation_view(工作树 / 报告落点 /
    报告必填字段 / 承继来源), 报告必填行式 = verbs.schema lybra_my_tasks.kickoff.report_field_line(next_resolver.render_report_field_lines)
  - 可开工判据: next_resolver.kickoff_refusal(与工位 /go 同一判据)
  - 实例身份 / 章程可取 / 门地址 / 工位根: charter_render.instance_charter(与 `lybra charter` 同一判定); 章程命令 = charter_pull_command
  - 写权限边界与全角色硬规矩: roles.schema write_boundary(write_boundary.build_write_boundary)
  - 门交互硬规矩: 项目 paths.hard_rules_source § 0.5(hard_rules_extractor)
  - 全量命令与时限: charter_render.runall_context(project.json test_contract); 长命令: run_long.charter_commands
  - 草稿目录: project.json paths.scratch_root(未声明 = 系统临时目录; 落治理根内 = 拒)
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
             "commands_rules_title", "commands_rules_absent", "scratch_source_declared", "scratch_source_default", "host_hint_alias",
             "host_hint_undeclared")
ITEM_KEYS = ("boundary_line", "hard_rule_line", "commands_rule_line")
REFUSAL_KEYS = ("KICKOFF_ROLE_MISMATCH", "KICKOFF_INSTANCE_MISSING", "KICKOFF_NOT_STARTABLE", "KICKOFF_CHARTER_REFUSED",
                "KICKOFF_WORKTREE_UNRESOLVED", "KICKOFF_REPORT_UNRESOLVED", "KICKOFF_SCRATCH_IN_GOVERNANCE")
ROLES = ("executor", "auditor")


class KickoffRefused(ValueError):
    """开工稿拒(code ∈ verbs.schema lybra_kickoff.refusals; message = 拒因原文与出口)。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def kickoff_contract() -> dict[str, Any]:
    """verbs.schema lybra_kickoff 唯一读取口(layout / texts / item_templates / refusals / role_labels)。缺键 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    contract = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(KICKOFF_VERB)
    if not isinstance(contract, dict):
        raise SchemaLoadError(f"verbs.schema.json verbs.{KICKOFF_VERB} 未声明")
    layout, texts = contract.get("layout") or {}, contract.get("texts") or {}
    items, refusals, labels = contract.get("item_templates") or {}, contract.get("refusals") or {}, contract.get("role_labels") or {}
    problems = [f"layout.{r}" for r in ROLES if not isinstance(layout.get(r), list) or not layout[r]]
    problems += [f"texts.{k}" for k in TEXT_KEYS if not isinstance(texts.get(k), str)]
    problems += [f"texts.{k}" for r in ROLES for k in (layout.get(r) or []) if k and not isinstance(texts.get(k), str)]
    problems += [f"item_templates.{k}" for k in ITEM_KEYS if not isinstance(items.get(k), str)]
    problems += [f"refusals.{k}" for k in REFUSAL_KEYS if not isinstance(refusals.get(k), str)]
    problems += [f"role_labels.{r}" for r in ROLES if not isinstance(labels.get(r), str)]
    if problems:
        raise SchemaLoadError(f"verbs.schema.json verbs.{KICKOFF_VERB} 未声明齐: {sorted(set(problems))}")
    return contract


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


def _item_lines(template: str, rows: list[dict[str, Any]]) -> str | None:
    out = [_PLACEHOLDER_RE.sub(lambda m, row=row: str(row.get(m.group(1), "")), template) for row in rows]
    return "\n".join(out) if out else None


def render_kickoff_brief(governance_root: str | Path, task_id: str, *, role: str | None = None) -> dict[str, Any]:
    """一张已认领卡的子 agent 开工稿(唯一实现)。返回 {ok, task_id, role, instance, text, values}。
    拒 = KickoffRefused(code, 原文); 声明 / 治理根坏 = SchemaLoadError / ValueError / OSError 上抛(fail-closed)。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.charter_render import CharterRefused, charter_pull_command, instance_charter, runall_context
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.hard_rules_extractor import extract_hard_rules_from_handbook, hard_rules_source_ref
    from tools.aipos_cli.next_resolver import (
        card_base_branch, card_branch_name, card_workstation_view, kickoff_refusal, render_report_field_lines,
    )
    from tools.aipos_cli.run_long import charter_commands
    from tools.aipos_cli.task_loader import find_task_card
    from tools.aipos_cli.workspace_config import read_project_json
    from tools.aipos_cli.write_boundary import build_write_boundary

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
    try:
        charter = instance_charter(gov, role_class=card_role, instance=instance)
    except CharterRefused as exc:
        raise _refuse(contract, "KICKOFF_CHARTER_REFUSED", instance=instance, reason=str(exc)) from exc

    view = card_workstation_view(gov, task_id, fm)
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
    rows = [{**row, "path": str(row["path"]).replace("<task_id>", task_id)} for row in boundary["rows"] if row["role"] == str(charter["role"])]
    handbook = extract_hard_rules_from_handbook(gov)
    if handbook.get("ok") and handbook.get("rules_list"):
        commands_rules = "\n".join([texts["commands_rules_title"].format(source=hard_rules_source_ref(gov))]
                                   + [items["commands_rule_line"].format(rule=rule) for rule in handbook["rules_list"]])
    else:
        commands_rules = texts["commands_rules_absent"].format(source=hard_rules_source_ref(gov))

    scratch, scratch_source = scratch_dir(gov, project, task_id, contract)
    runall = runall_context(gov, code_repo, log_dir=str(scratch))
    test_contract = runall["test_contract"]
    long_cmds = charter_commands(gov, task_id=task_id, cwd=str(worktree) if worktree is not None else None)
    report_path = Path(str(view["report_path"]))
    others = _other_project_roots(gov)
    values: dict[str, Any] = {
        "project": project, "task_id": task_id, "title": str(fm.get("title") or task_id), "instance": instance,
        "role_label": labels[card_role], "reviewed_task_id": reviewed or None,
        "charter_command": pull["command"] if remote else pull["local_command"],
        "card_path": str(card_path), "reviewed_card_path": str(reviewed_card) if reviewed_card else None,
        "worktree_path": str(worktree) if worktree is not None else None,
        "no_worktree_reason": None if worktree is not None else str(worktree_refusal.get("reason") or ""),
        "branch": branch, "base": base, "base_sha": base_sha, "tip": tip,
        "code_repo": str(code_repo) if code_repo else None, "repo_line": repo_line,
        "lane_paths": ", ".join(f"`{p}`" for p in lane_paths) if lane_paths else str(texts["lane_paths_absent"]),
        "carried_line": view.get("carried_line"),
        "session_host": session_host if remote else None, "gate_host": gate_host, "host_hint": host_hint,
        "boundary_lines": _item_lines(items["boundary_line"], rows),
        "governance_root": str(gov), "report_dir": str(report_path.parent),
        "other_projects": ", ".join(f"`{p}`" for p in others) if others else None,
        "harness_root": str(charter["harness_root"]) if charter.get("workstation") else None,
        "gate_url": str(charter["gate_url"]),
        "hard_rule_lines": _item_lines(items["hard_rule_line"], boundary["hard_rules"]),
        "commands_rule_lines": commands_rules,
        "scratch_dir": str(scratch), "scratch_source": scratch_source,
        "runall_command": runall["runall_command"] if runall["runall"] else str(texts["runall_absent"]),
        "runall_timeout": runall["runall_timeout_seconds"] if runall["runall"] else str(texts["runall_absent_timeout"]),
        "run_long_start": long_cmds["run_long_start"], "run_long_wait": long_cmds["run_long_wait"],
        "ratchet": test_contract.get("ratchet") or str(texts["ratchet_default"]),
        "residual_pattern": test_contract.get("residual_processes"),
        "report_path": str(report_path),
        "report_field_lines": "\n".join(f"  {line}" for line in render_report_field_lines(view.get("report_required_frontmatter"))),
    }
    text = _render(contract, card_role, values)
    return {"ok": True, "task_id": task_id, "role": card_role, "instance": instance, "text": text,
            "values": {k: v for k, v in values.items() if v is not None},
            "sources": {"charter": charter["distribution_id"], "ratchet": test_contract.get("ratchet_source"),
                        "residual_processes": test_contract.get("residual_processes_source"), "scratch": scratch_source,
                        "remote": remote}}


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
