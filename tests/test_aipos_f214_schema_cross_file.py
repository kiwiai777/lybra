"""AIPOS-F214 — schema 跨文件对表(F208 P1-7 / P1-8 值域 / P2-2~P2-5)与新棘轮 R6 跨文件引用对表。

件① 承继字段单基表: transitions queue_mutations.followup_inherit_fields 一处声明; 路线图修复卡(verbs lybra_roadmap.verify.fix_card)
    与 Owner 纠正承接卡(transitions queue_mutations.correct.followup)只以 inherit_fields_ref 引用(读取口 roadmap.followup_inherit_fields);
    另列 inherit_fields = SchemaLoadError。(验证卡修复卡发布实撞见 tests/test_aipos_f214_fix_card_candidate_binding.py)
件② 结案值域单声明: card.schema dependency_gate.conclusion.outcomes 一处; card 字段 conclusion_outcome / verbs outcome 参数不另列值,
    CLI choices 与 MCP inputSchema enum 由 task_complexity.conclusion_outcomes 投影(F209 落地, 本卡 R6 钉住)。
件③ 集合单源: worktree_cleanup.terminal_states = enums 终态投影, carry_protect_states = 撤换承继 successor_states 投影; 另列 = 拒。
件④ argparse 读 schema: kickoff --role(enums kickoff_role)、手工章程收编旗标(distribution hand_maintained_guard)、roadmap verify --result
    (verbs lybra_roadmap.verify.results)、validation-root 写动作表(verbs two_phase_protocol.project_json_writers.commands)。
件⑤ 验证根条目键表(config.schema 结构化键表 → validation_root.entry_key_table)、交付主体 kind(enums → next_resolver.governance_outputs_kind)、
    helper 证据文件常量(roles.schema run_evidence.helper_files 对表, 不符 = 拒)。
R6 跨文件引用对表: choices / decision_choice ∈ 收件箱选项(复用 F213 require_inbox_choices, 不重做)、fix_card ⊇ correct、终态 == enums、
    argparse choices == 动词参数值域、MCP 手写 inputSchema 属性集 == verbs 参数集(现存不一致入基线 tests/f214_mcp_param_set_baseline.jsonl,
    只减不增)。每件附「故意改一侧即红」反例(内存里改声明副本, 不写盘)。只读; 不起进程、不碰真实治理根。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from ratchet_baseline import read_entries  # noqa: E402
from test_aipos_c1_surface_consistency import cli_subparser  # noqa: E402  (argparse 子命令树走法唯一一份)

MCP_PARAM_BASELINE = Path(__file__).resolve().parent / "f214_mcp_param_set_baseline.jsonl"


def _show(text: str) -> None:
    print(text, flush=True)


def _schema(name: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))


def _patch_schema(monkeypatch: pytest.MonkeyPatch, schema_type: str, mutate: Callable[[dict[str, Any]], None]) -> None:
    """读取口经 tools.schema_loader.load_schema 读到的某份 schema 换成改过的副本(内存, 不写盘)。"""
    import tools.schema_loader as sl

    real = sl.load_schema

    def fake(kind: str, repo_root: Path | None = None) -> dict[str, Any]:
        data = real(kind, repo_root)
        if kind == schema_type:
            data = json.loads(json.dumps(data))
            mutate(data)
        return data

    fake.cache_clear = real.cache_clear  # type: ignore[attr-defined]
    monkeypatch.setattr(sl, "load_schema", fake)


def _parser() -> argparse.ArgumentParser:
    from tools.aipos_cli.aipos_cli import build_parser

    return build_parser()


def _action(parser: argparse.ArgumentParser, option: str) -> argparse.Action:
    return next(a for a in parser._actions if option in a.option_strings)


# ===========================================================================
# 件① 承继字段单基表(R6: fix_card ⊇ correct)
# ===========================================================================

def test_item1_inherit_fields_single_base_table():
    from tools.aipos_cli.queue_mutation import correct_declaration
    from tools.aipos_cli.roadmap import FOLLOWUP_INHERIT_REF, verify_declaration

    base = _schema("transitions")["queue_mutations"]["followup_inherit_fields"]
    raw_fix = _schema("verbs")["verbs"]["lybra_roadmap"]["verify"]["fix_card"]
    raw_correct = _schema("transitions")["queue_mutations"]["correct"]["followup"]
    fix = verify_declaration()["fix_card"]["inherit_fields"]
    correct = correct_declaration()["followup"]["inherit_fields"]
    _show(f"[件①·单基表] {FOLLOWUP_INHERIT_REF} = {base}")
    _show(f"[件①·引用] fix_card.inherit_fields_ref={raw_fix.get('inherit_fields_ref')} correct.followup.inherit_fields_ref="
          f"{raw_correct.get('inherit_fields_ref')}; 投影 fix_card {len(fix)} 项 / correct {len(correct)} 项")
    assert "inherit_fields" not in raw_fix and "inherit_fields" not in raw_correct
    assert raw_fix["inherit_fields_ref"] == raw_correct["inherit_fields_ref"] == FOLLOWUP_INHERIT_REF
    assert fix == correct == base and set(fix) >= set(correct)  # R6: fix_card ⊇ correct
    assert {"validation_source_task", "validation_candidate", "artifact_scope", "owner_verify", "resources"} <= set(fix)


@pytest.mark.parametrize("side", ["fix_card", "correct"])
def test_item1_second_inherit_list_red(monkeypatch: pytest.MonkeyPatch, side: str):
    from tools.aipos_cli.queue_mutation import correct_declaration
    from tools.aipos_cli.roadmap import verify_declaration
    from tools.schema_loader import SchemaLoadError

    old15 = ["project", "assigned_to", "agent_instance", "context_bundle", "task_mode", "task_class", "priority", "output_target",
             "artifact_policy", "harness", "lane", "audit", "audit_by", "audit_runtime_evidence", "artifacts"]
    if side == "fix_card":
        _patch_schema(monkeypatch, "verbs", lambda d: d["verbs"]["lybra_roadmap"]["verify"]["fix_card"].update(inherit_fields=old15))
        load = verify_declaration
    else:
        _patch_schema(monkeypatch, "transitions", lambda d: d["queue_mutations"]["correct"]["followup"].update(inherit_fields=old15))
        load = correct_declaration
    with pytest.raises(SchemaLoadError) as exc:
        load()
    _show(f"[件①·反例 {side} 另列 15 项旧表] {exc.value}")
    assert "inherit_fields_ref" in str(exc.value)


# ===========================================================================
# 件② 结案值域单声明(R6)
# ===========================================================================

def test_item2_conclusion_outcomes_single_declaration():
    from tools.aipos_cli.task_complexity import conclusion_outcomes
    from tools.mcp_server.tools import WRITE_TOOL_DESCRIPTORS

    outcomes = list(conclusion_outcomes())
    declared = list(_schema("card")["dependency_gate"]["conclusion"]["outcomes"])
    field = _schema("card")["fields"]["conclusion_outcome"]
    param = _schema("verbs")["verbs"]["lybra_mark_concluded"]["parameters"]["properties"]["outcome"]
    mcp = next(d for d in WRITE_TOOL_DESCRIPTORS if d["name"] == "lybra_mark_concluded")["inputSchema"]["properties"]["outcome"]
    cli = _action(cli_subparser(_parser(), ["mark-concluded"]), "--outcome").choices
    _show(f"[件②] 声明 outcomes={declared}; CLI choices={cli}; MCP enum={mcp['enum']}; card 字段 / verbs 参数另列值: "
          f"{[k for k in ('enum', '$enum') if k in field or k in param] or '无'}")
    # AIPOS-F221 件②: CLI choices / MCP enum = 结案值域 + 复核打回返工(verbs.schema lybra_mark_concluded.review_rework.outcome, 不结案,
    # 不进结案值域; 唯一渲染 board_adapter.mark_concluded_outcome_choices)
    rework = _schema("verbs")["verbs"]["lybra_mark_concluded"]["review_rework"]["outcome"]
    assert declared == outcomes and rework not in declared
    assert list(cli) == list(mcp["enum"]) == [*declared, rework]
    assert not {"enum", "$enum"} & (set(field) | set(param)), "card 字段 / verbs 参数不得另列结案值域"


# ===========================================================================
# 件③ 集合单源(R6: 终态 == enums)
# ===========================================================================

def test_item3_state_sets_projected():
    from tools.aipos_cli.queue_mutation import supersede_carry_declaration
    from tools.aipos_cli.task_loader import QUEUE_TERMINAL_STATES
    from tools.aipos_cli.worktree_cleanup import cleanup_declaration

    raw = _schema("transitions")["queue_mutations"]["worktree_cleanup"]
    decl = cleanup_declaration()
    _show(f"[件③] terminal_states={decl['terminal_states']}(enums 终态 {QUEUE_TERMINAL_STATES}); carry_protect_states="
          f"{decl['carry_protect_states']}(carry_branch.successor_states {supersede_carry_declaration()['successor_states']})")
    assert "terminal_states" not in raw and "carry_protect_states" not in raw
    assert decl["terminal_states"] == tuple(QUEUE_TERMINAL_STATES)
    assert decl["carry_protect_states"] == tuple(supersede_carry_declaration()["successor_states"])


def test_item3_second_state_set_red(monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.worktree_cleanup import cleanup_declaration
    from tools.schema_loader import SchemaLoadError

    _patch_schema(monkeypatch, "transitions", lambda d: d["queue_mutations"]["worktree_cleanup"].update(terminal_states=["completed"]))
    with pytest.raises(SchemaLoadError) as exc:
        cleanup_declaration()
    _show(f"[件③·反例 worktree_cleanup 另列 terminal_states] {exc.value}")
    assert "terminal_states" in str(exc.value)


def test_item3_terminal_follows_enums(monkeypatch: pytest.MonkeyPatch):
    """enums 终态一改, 清理判据跟着变(投影, 非第二份)。"""
    from tools.aipos_cli import task_loader, worktree_cleanup

    monkeypatch.setattr(task_loader, "QUEUE_TERMINAL_STATES", ("completed",))
    assert worktree_cleanup.cleanup_declaration()["terminal_states"] == ("completed",)


# ===========================================================================
# 件④ argparse 读 schema(R6: argparse choices == 动词参数值域)
# ===========================================================================

def _help(words: list[str]) -> str:
    return cli_subparser(_parser(), words).format_help()


def test_item4_argparse_choices_equal_schema():
    from tools.aipos_cli.distribution_sync import hand_maintained_guard_declaration
    from tools.aipos_cli.roadmap import verify_declaration
    from tools.aipos_cli.subagent_kickoff import kickoff_roles
    from tools.aipos_cli.validation_root import write_actions
    from tools.schema_loader import get_enum_values, resolve_field_enum

    parser = _parser()
    verbs = _schema("verbs")["verbs"]
    role_param = verbs["lybra_kickoff"]["parameters"]["properties"]["role"]
    kick = _action(cli_subparser(parser, ["kickoff"]), "--role").choices
    assert role_param.get("$enum") == "kickoff_role" and "enum" not in role_param
    assert list(kick) == list(kickoff_roles()) == get_enum_values("kickoff_role") == resolve_field_enum(role_param)
    result = _action(cli_subparser(parser, ["roadmap", "verify"]), "--result").choices
    assert list(result) == list(verify_declaration()["results"]) == verbs["lybra_roadmap"]["parameters"]["properties"]["result"]["enum"]
    guard = hand_maintained_guard_declaration()
    sync = cli_subparser(parser, ["sync"])
    assert {guard["adopt_flag"], guard["owner_ref_flag"]} <= {o for a in sync._actions for o in a.option_strings}
    assert _action(sync, guard["adopt_flag"]).dest == "adopt_hand_charter" and _action(sync, guard["owner_ref_flag"]).dest == "owner_ref"
    vr = cli_subparser(parser, ["project", "validation-root"])
    sub = next(a for a in vr._actions if isinstance(a, argparse._SubParsersAction))
    writers = [name for name, p in sub.choices.items() if any("--confirm" in a.option_strings for a in p._actions)]
    assert sorted(writers) == sorted(write_actions())
    _show("---- 件④ argparse --help 原文(kickoff --role) ----\n" + "\n".join(
        ln for ln in _help(["kickoff"]).splitlines() if "role" in ln or "executor" in ln))
    _show("---- 件④ argparse --help 原文(roadmap verify --result) ----\n" + "\n".join(
        ln for ln in _help(["roadmap", "verify"]).splitlines() if "--result" in ln))
    _show("---- 件④ argparse --help 原文(sync 收编旗标) ----\n" + "\n".join(
        ln for ln in _help(["sync"]).splitlines() if guard["adopt_flag"] in ln or guard["owner_ref_flag"] in ln))
    _show(f"[件④ 声明值] kickoff_role={get_enum_values('kickoff_role')}; verify.results={list(verify_declaration()['results'])}; "
          f"hand_maintained_guard={guard['adopt_flag']} / {guard['owner_ref_flag']}; validation-root 写动作={list(write_actions())}"
          f"(argparse 带 --confirm 的子命令 {sorted(writers)})")


def test_r6_every_verb_enum_param_equals_argparse_choices():
    """通用对表: 动词声明了值域的参数, 在其 CLI 子命令上同名旗标若限值域 = 必须相等(漂移即红)。"""
    from tools.schema_loader import resolve_field_enum

    parser, pairs = _parser(), []
    for name, verb in _schema("verbs")["verbs"].items():
        words = str(verb.get("cli_command") or "").split()[1:]
        sub = cli_subparser(parser, words) if words else None
        for prop, spec in ((verb.get("parameters") or {}).get("properties") or {}).items():
            values = resolve_field_enum(spec) if isinstance(spec, dict) else None
            action = next((a for a in sub._actions if f"--{prop.replace('_', '-')}" in a.option_strings), None) if sub else None
            if values is not None and action is not None and action.choices is not None:
                pairs.append((name, prop, list(values), list(action.choices)))
    _show(f"[R6·argparse 对表] {[(n, p) for n, p, _v, _c in pairs]}")
    assert pairs and all(sorted(v) == sorted(c) for _n, _p, v, c in pairs), [x for x in pairs if sorted(x[2]) != sorted(x[3])]


def test_item4_argparse_follows_schema(monkeypatch: pytest.MonkeyPatch):
    """故意改一侧即红的另一面: 改 schema 一侧, CLI 面跟着变(读 schema, 不写死)。"""
    def kick(d):
        d["enums"]["kickoff_role"]["values"].append({"value": "planner", "description": "probe"})

    def kick_contract(d):  # 开工稿声明须覆盖每个角色(fail-closed), 同步补上
        k = d["verbs"]["lybra_kickoff"]
        k["layout"]["planner"], k["role_labels"]["planner"] = list(k["layout"]["auditor"]), "规划(探针)"

    _patch_schema(monkeypatch, "enums", kick)
    _patch_schema(monkeypatch, "verbs", kick_contract)
    _patch_schema(monkeypatch, "distribution", lambda d: d["charter_render_semantics"]["hand_maintained_guard"].update(
        adopt_flag="--adopt-probe-charter"))
    parser = _parser()
    assert "planner" in _action(cli_subparser(parser, ["kickoff"]), "--role").choices
    assert _action(cli_subparser(parser, ["sync"]), "--adopt-probe-charter").dest == "adopt_hand_charter"


def test_item4_no_hardcoded_literals_left():
    src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    vr = (REPO_ROOT / "tools/aipos_cli/validation_root.py").read_text(encoding="utf-8")
    assert 'choices=["executor", "auditor"]' not in src and 'choices=["confirm", "mismatch"]' not in src
    assert 'add_argument("--adopt-hand-charter"' not in src and "\nACTIONS = (" not in vr


def test_item4_validation_root_actions_red_on_unlanded(monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.validation_root import write_actions
    from tools.schema_loader import SchemaLoadError

    _patch_schema(monkeypatch, "verbs", lambda d: d["two_phase_protocol"]["project_json_writers"]["commands"].append(
        "validation-root probe-write"))
    with pytest.raises(SchemaLoadError) as exc:
        write_actions()
    _show(f"[件④·反例 两阶段声明新增写动作却未登记写后落账] {exc.value}")
    assert "validation-root probe-write" in str(exc.value)


# ===========================================================================
# 件⑤ 验证根键表 / 交付主体 kind / helper 常量
# ===========================================================================

def test_item5_validation_root_key_table_projected():
    from tools.aipos_cli.validation_root import entry_key_table

    spec = _schema("config")["configuration_sources"]["project_json"]["schema"]["validation_roots"]["schema"]
    table = entry_key_table()
    _show(f"[件⑤·键表] {json.dumps(table, ensure_ascii=False)}")
    assert table["entry"] == tuple(spec["declarations"]["entry_schema"]) and table["host"] == tuple(spec["hosts"]["entry_schema"])
    assert table["simulator"] == tuple(spec["declarations"]["simulator_entry_schema"])
    assert set(table["run"]) | set(table["run_optional"]) == set(spec["declarations"]["run_entry_schema"])
    src = (REPO_ROOT / "tools/aipos_cli/validation_root.py").read_text(encoding="utf-8")
    assert not any(f"\n{name} = (" in src for name in ("ENTRY_KEYS", "RUN_KEYS", "RUN_OPTIONAL_KEYS", "SIM_KEYS", "HOST_ENTRY_KEYS"))


def test_item5_key_table_drives_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """config.schema 键表一改, 条目形校验跟着变: 声明里删掉 hosts 条目键 declared_at → 带该键的 hosts 条目被拒(VR_DECLARATION_INVALID)。"""
    from tools.aipos_cli import validation_root as vr

    project = {"project": "probe", "validation_roots": {"declarations": [], "hosts": {"mac": {
        "ssh_alias": "mac", "owner_ref": "probe", "declared_by": "advisor.probe.h1", "declared_at": "2026-10-22T00:00:00Z"}}}}
    assert vr.project_validation_hosts(tmp_path, project=project)["mac"]["ssh_alias"] == "mac"

    def drop(d):
        d["configuration_sources"]["project_json"]["schema"]["validation_roots"]["schema"]["hosts"]["entry_schema"].pop("declared_at")

    _patch_schema(monkeypatch, "config", drop)
    with pytest.raises(vr.ValidationRootError) as exc:
        vr.project_validation_hosts(tmp_path, project=project)
    _show(f"[件⑤·反例 键表删 declared_at] {exc.value}")
    assert exc.value.code == "VR_DECLARATION_INVALID"


def test_item5_governance_outputs_kind_reads_declaration(monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import next_resolver
    from tools.schema_loader import SchemaLoadError

    enums = _schema("enums")["enums"]["task_mode"]
    assert next_resolver.governance_outputs_kind() == enums["governance_outputs_subject"]["subject_kind"]
    assert next_resolver.delivery_subject_kinds() == tuple(enums["delivery_subject"]["kinds"])
    for rel in ("tools/aipos_cli/next_resolver.py", "tools/aipos_cli/audit_derivation.py", "tools/aipos_cli/machine_zone.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        literals = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "governance_outputs"]
        assert not literals, f"{rel} 仍写死 kind 字面量 'governance_outputs'(行 {literals})"
    _patch_schema(monkeypatch, "enums", lambda d: d["enums"]["task_mode"]["governance_outputs_subject"].update(subject_kind="outputs"))
    with pytest.raises(SchemaLoadError) as exc:
        next_resolver.governance_outputs_kind()
    _show(f"[件⑤·反例 subject_kind 不在三态] {exc.value}")


def test_item5_helper_constants_pinned(monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli import validation_root_helper as helper
    from tools.schema_loader import SchemaLoadError

    files = vr.run_evidence_declaration()["helper_files"]
    _show(f"[件⑤·helper 对表] 声明 {files}; helper INIT_EVIDENCE={helper.INIT_EVIDENCE} SRC_EVIDENCE={helper.SRC_EVIDENCE} "
          f"EVIDENCE_MANIFEST={helper.EVIDENCE_MANIFEST}")
    assert (files["init_run"], files["src_verify"], files["evidence_manifest"]) == (
        helper.INIT_EVIDENCE, helper.SRC_EVIDENCE, helper.EVIDENCE_MANIFEST)
    assert files["evidence_dir"] == ".lybra-vr" and files["evidence_dir"] in vr.run_evidence_declaration()["include_dirs"]
    monkeypatch.setattr(helper, "SRC_EVIDENCE", ".lybra-vr/src-verify-v2.jsonl")
    with pytest.raises(SchemaLoadError) as exc:
        vr.run_evidence_declaration()
    _show(f"[件⑤·反例 helper 常量改名未改声明] {exc.value}")
    assert "helper_files" in str(exc.value)


# ===========================================================================
# R6 汇总: 选项引用(复用 F213)+ MCP 手写 inputSchema 属性集 == verbs 参数集(棘轮)
# ===========================================================================

def test_r6_choice_refs_reuse_f213_single_check():
    """choices / decision_choice ∈ 收件箱选项: F213 已收进 queue_mutation.require_inbox_choices(加载口与前置口同调); 本卡复用、不重做。"""
    from test_aipos_f213_queue_write_landing import _walk_choice_refs
    from tools.aipos_cli.queue_mutation import require_inbox_choices

    refs = [(f"{p.name}:{where}", value) for p in sorted((REPO_ROOT / "schema").glob("*.json"))
            for where, value in _walk_choice_refs(json.loads(p.read_text(encoding="utf-8")))]
    for where, value in refs:
        require_inbox_choices(value if isinstance(value, list) else [value], where)
    _show(f"[R6·选项引用(复用 F213 require_inbox_choices)] {refs}")
    assert len(refs) >= 3


def mcp_param_set_gaps(verbs: dict[str, Any], descriptors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """mcp 面动词: 门手写 inputSchema 属性集 vs verbs parameters 属性集的差(两侧都有声明才比)。"""
    by_name = {d["name"]: d for d in descriptors}
    gaps = []
    for name, verb in verbs.items():
        props = ((verb.get("parameters") or {}).get("properties")) if isinstance(verb.get("parameters"), dict) else None
        if "mcp" not in (verb.get("surface") or []) or name not in by_name or props is None:
            continue
        mcp = set(((by_name[name].get("inputSchema") or {}).get("properties")) or {})
        if mcp != set(props):
            gaps.append({"verb": name, "mcp_only": sorted(mcp - set(props)), "verbs_only": sorted(set(props) - mcp)})
    return gaps


def _gap_key(entry: dict) -> tuple[str, str, str]:
    return (str(entry["verb"]), json.dumps(entry["mcp_only"]), json.dumps(entry["verbs_only"]))


def _live_mcp_gaps(verbs: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from tools.mcp_server.tools import READ_TOOL_DESCRIPTORS, WRITE_TOOL_DESCRIPTORS

    return mcp_param_set_gaps(verbs or _schema("verbs")["verbs"], READ_TOOL_DESCRIPTORS + WRITE_TOOL_DESCRIPTORS)


def test_r6_mcp_param_baseline_well_formed():
    for entry in read_entries(MCP_PARAM_BASELINE):
        assert set(entry) == {"verb", "mcp_only", "verbs_only", "reason"} and str(entry["reason"]).strip(), entry


def test_r6_mcp_input_schema_equals_verbs_params_only_shrinks():
    gaps = _live_mcp_gaps()
    current, allowed = Counter(_gap_key(g) for g in gaps), Counter(_gap_key(e) for e in read_entries(MCP_PARAM_BASELINE))
    new, stale = sorted((current - allowed).elements()), sorted((allowed - current).elements())
    _show(f"[R6·MCP inputSchema 对表] 不一致 {len(gaps)} 个动词; 基线 {sum(allowed.values())} 条")
    for g in gaps:
        _show(f"  {g['verb']}: 仅 MCP {g['mcp_only']} / 仅 verbs {g['verbs_only']}")
    assert not new, f"门手写 inputSchema 与 verbs 参数集不一致(新增; 改一侧须同改另一侧): {new}"
    assert not stale, f"基线条目已修 → 从 {MCP_PARAM_BASELINE.name} 删除这些行(只减不增): {stale}"


def test_r6_mcp_param_red_on_tampered_side():
    verbs = json.loads(json.dumps(_schema("verbs")["verbs"]))
    verbs["lybra_mark_concluded"]["parameters"]["properties"]["probe_only_in_verbs"] = {"type": "string"}
    allowed = Counter(_gap_key(e) for e in read_entries(MCP_PARAM_BASELINE))
    new = [k for k in Counter(_gap_key(g) for g in _live_mcp_gaps(verbs)) if k not in allowed]
    _show(f"[R6·反例 verbs 侧加参数未改 MCP] 新增 {new}")
    assert new == [("lybra_mark_concluded", "[]", '["probe_only_in_verbs"]')]
