"""AIPOS-F101 推导核与动词声明单源(碎片化族 C-a: H7/H8/M13/L2)夹具。

件① 门工具 lybra_gate_guidance 委托唯一推导核 next_resolver.derive_next_step(同输入同结论); 第二推导核死函数删净(git grep 零)。
件② 门工具名与 scope 全部声明在 schema/verbs.schema.json; 执法 / 可见性 / verb_contract 一律读声明(改声明即改行为), 碎片化不变量 b 清零。
件③ artifact ingest 与 agent watch 退出码只在 verbs.schema 声明(transitions 重复处删), 代码读声明且与声明一致。
件④ 自描述与门指南示例改占位, 不出现 lybra 身份字面。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 复用 F71 推导核靶场(冷启动工作区 + 卡/记录/产物造法), 不另造第二套
from test_aipos_f71_next_command import (  # noqa: E402,F401
    _create_record,
    _create_return_artifact,
    _create_task_card,
    cold_start_workspace,
)
from tools.mcp_server import tools as gate  # noqa: E402

VERBS_SCHEMA = REPO_ROOT / "schema" / "verbs.schema.json"
TRANSITIONS_SCHEMA = REPO_ROOT / "schema" / "transitions.schema.json"


def _payload(result: dict) -> dict:
    return result["structuredContent"]


def _verbs() -> dict:
    return json.loads(VERBS_SCHEMA.read_text(encoding="utf-8"))["verbs"]


def _token(*ops: str) -> str:
    return json.dumps({"operations": list(ops), "token_ref": "f101", "expires_at": "2999-01-01T00:00:00Z"})


# ---------------------------------------------------------------------------
# 验收① gate_guidance 与 derive_next_step 同输入同结论
# ---------------------------------------------------------------------------

def _rig_cases(ws: Path) -> list[str]:
    queue = ws / "5_tasks" / "queue"
    _create_task_card(queue, "F101-P", "pending")  # 待认领 → claim
    _create_task_card(queue, "F101-C", "claimed")  # 已认领 + 认领记录 + 产物 → 产物入口 return
    _create_record(ws / "5_tasks" / "records", "claims", "F101-C", "claim", claim_id="claim_F101-C_x")
    _create_return_artifact(ws, "F101-C")
    _create_task_card(queue, "F101-N", "claimed")  # 已认领但无认领记录 → 不可推导(fail-closed)
    return ["F101-P", "F101-C", "F101-N", "F101-NOPE"]


def test_item1_gate_guidance_same_input_same_conclusion_as_derive_next_step(cold_start_workspace, monkeypatch):
    from tools.aipos_cli.next_resolver import derive_next_step

    ws = cold_start_workspace
    cases = _rig_cases(ws)
    monkeypatch.setattr(gate, "_repo_root", lambda: ws)
    seen_verbs = set()
    for task_id in cases:
        for role in ("executor", "auditor", "advisor"):
            out = _payload(gate.lybra_gate_guidance({"task_id": task_id, "role": role}))
            expected = derive_next_step(task_id, ws)
            assert out["ok"] is True and out["source"] == "gate"
            assert out["guidance"] == expected, (task_id, role)  # 同输入同结论(整份推导原样)
            assert out["derivation_core"] == "tools/aipos_cli/next_resolver.derive_next_step"
            assert out["role_turn"] is (str(expected.get("triggered_by") or "") == role)
            verb = str(expected.get("verb") or "")
            seen_verbs.add(verb)
            if verb in gate.TOOL_HANDLERS:  # 派生的门工具: 所需 scope = 声明
                assert out["scope_needed"] == _verbs()[verb]["required_scope"]
            else:
                assert out["scope_needed"] is None
    assert "lybra_queue_claim_dry_run" in seen_verbs and "lybra_queue_return_dry_run" in seen_verbs
    # 不可推导 / 找不到卡: 推导核原样 fail-closed 结论透出, 门工具不另猜
    nope = _payload(gate.lybra_gate_guidance({"task_id": "F101-NOPE", "role": "executor"}))["guidance"]
    assert nope["derivable"] is False and nope["current_state"] == "not_found"
    missing_claim = _payload(gate.lybra_gate_guidance({"task_id": "F101-N", "role": "executor"}))["guidance"]
    assert missing_claim["derivable"] is False and missing_claim["missing_records"]


def test_item1_gate_guidance_delegates_and_requires_inputs():
    src = Path(gate.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "lybra_gate_guidance")
    body = ast.get_source_segment(src, fn) or ""
    assert "derive_next_step(" in body and "flow_description" not in body and "transition_engine" not in body
    assert _payload(gate.lybra_gate_guidance({"role": "executor"}))["ok"] is False
    assert _payload(gate.lybra_gate_guidance({"task_id": "X-1"}))["ok"] is False


# ---------------------------------------------------------------------------
# 验收② 第二推导核死函数 git grep 零; 有调用方的保留
# ---------------------------------------------------------------------------

def test_item2_dead_derivation_functions_git_grep_zero():
    from tools.aipos_cli import flow_description, transition_engine

    names = ["resolve_next_step_from_schema", "resolve_next_step_with_profile", r"resolve_next_step\b"]
    proc = subprocess.run(
        ["git", "grep", "-n", "-E", "|".join(names), "--", ".", ":!tests/test_aipos_f101_derivation_verb_single_source.py"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 1 and proc.stdout == "", proc.stdout  # git grep: 1 = 无命中
    for mod, gone in ((flow_description, ("resolve_next_step", "resolve_next_step_with_profile")),
                      (transition_engine, ("resolve_next_step_from_schema",))):
        for name in gone:
            assert not hasattr(mod, name), f"{mod.__name__}.{name} 未删"
    # 有调用方的保留(卡面明令)
    assert callable(flow_description.resolve_gate_chain) and callable(flow_description.resolve_collaboration_profile)
    assert callable(transition_engine.apply_transition_metadata)


# ---------------------------------------------------------------------------
# 验收③ 门工具名与 scope 全量声明(不变量 b = 0); 执法/可见性/verb_contract 读声明
# ---------------------------------------------------------------------------

def test_item3_every_gate_tool_declared_with_scope_and_visibility():
    verbs = _verbs()
    text = Path(gate.__file__).read_text(encoding="utf-8")
    descriptor_names = set(re.findall(r'"name":\s*"(lybra_[A-Za-z0-9_]+)"', text))
    assert descriptor_names == set(gate.TOOL_HANDLERS), descriptor_names ^ set(gate.TOOL_HANDLERS)
    for name in gate.TOOL_HANDLERS:
        decl = verbs.get(name)
        assert isinstance(decl, dict), f"{name} 未在 verbs.schema 声明"
        assert "required_scope" in decl and decl["visibility"] in ("always", "scope", "hidden"), name
        assert "mcp" in decl["surface"], name
    # 碎片化不变量 b: 现存 0, 基线 0
    from test_aipos_f87_fragmentation_ratchet import load_baseline, scan_b_undeclared_tools

    assert scan_b_undeclared_tools() == []
    base = load_baseline()["invariants"]["b"]
    assert base["entries"] == [] and base["count"] == 0


def test_item3_no_second_scope_source_in_code():
    # tools.py 模块级不得再有 *_SCOPE 常量; verb_contract 不得再有按前缀的 scope 映射
    tree = ast.parse(Path(gate.__file__).read_text(encoding="utf-8"))
    consts = [t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
              for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
              if isinstance(t, ast.Name) and t.id.endswith("_SCOPE")]
    assert consts == [], consts
    per_scope = [n for n in vars(gate) if re.fullmatch(r"_\w+_scope_allowed", n) and n != "_verb_scope_allowed"]
    assert per_scope == [], f"按 scope 一函数的第二来源未删: {per_scope}"
    from tools.aipos_cli import verb_contract

    vc_src = Path(verb_contract.__file__).read_text(encoding="utf-8")
    assert "read_only_prefixes" not in vc_src and "scope_map = {" not in vc_src
    verbs = _verbs()
    for entry in verb_contract.get_verb_registry():
        assert entry["required_scope"] == verbs[entry["name"]]["required_scope"], entry["name"]


def _with_declaration(monkeypatch, mutate):
    """在声明副本上改一处, 经门的同一读取口生效(证明代码读声明而非写死)。"""
    from tools import schema_loader

    real = schema_loader.load_schema("verbs", schema_loader.code_repo_schema_root())
    patched = json.loads(json.dumps(real))
    mutate(patched["verbs"])

    def fake(kind, root=None):
        return patched if kind == "verbs" else schema_loader.load_schema(kind, root)

    monkeypatch.setattr(gate, "load_schema", fake)


def test_item3_enforcement_and_visibility_follow_declaration(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPOS_WORKSPACE_ROOT", str(tmp_path))
    for st in ("pending", "claimed", "completed", "blocked"):
        (tmp_path / "5_tasks" / "queue" / st).mkdir(parents=True)
    # 现声明: task_progress 索 task_progress
    monkeypatch.setenv("LYBRA_CAPABILITY_TOKEN", _token("queue_amend"))
    denied = _payload(gate.lybra_task_progress({"task_id": "X-1"}))
    assert denied.get("error_code") == "SCOPE_DENIED" and "'task_progress'" in denied["message"]
    # 改声明 → 执法跟随(持 queue_amend 即不再 SCOPE_DENIED)
    _with_declaration(monkeypatch, lambda v: v["lybra_task_progress"].update(required_scope="queue_amend"))
    assert _payload(gate.lybra_task_progress({"task_id": "X-1"})).get("error_code") != "SCOPE_DENIED"
    # 可见性跟随声明: hidden → always
    names = [d["name"] for d in gate.visible_tool_descriptors()]
    assert "lybra_queue_rework_dry_run" not in names
    _with_declaration(monkeypatch, lambda v: v["lybra_queue_rework_dry_run"].update(visibility="always"))
    assert "lybra_queue_rework_dry_run" in [d["name"] for d in gate.visible_tool_descriptors()]
    # 未声明 = fail-closed(不回落代码猜测)
    from tools.schema_loader import SchemaLoadError

    _with_declaration(monkeypatch, lambda v: v.pop("lybra_task_progress"))
    with pytest.raises(SchemaLoadError):
        gate.lybra_task_progress({"task_id": "X-1"})


def test_item3_owner_confirm_waiver_and_close_family_read_declaration(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPOS_WORKSPACE_ROOT", str(tmp_path))
    for st in ("pending", "claimed", "completed", "blocked"):
        (tmp_path / "5_tasks" / "queue" / st).mkdir(parents=True)
    # also_requires_scope: claim confirm 无 owner_confirm → 点名 owner_confirm
    monkeypatch.setenv("LYBRA_CAPABILITY_TOKEN", _token("queue_claim"))
    r = _payload(gate.lybra_queue_claim_confirm({"dry_run_token": "x"}))
    assert r.get("error_code") == "SCOPE_DENIED" and "'owner_confirm'" in r["message"]
    # conditional_scope: task_preview 读正文才索 queue_claim
    monkeypatch.setenv("LYBRA_CAPABILITY_TOKEN", _token())
    r = _payload(gate.lybra_task_preview({"task_id": "X-1", "include_body": True}))
    assert r.get("error_code") == "SCOPE_DENIED" and "'queue_claim'" in r["message"]
    # scope_waiver: PreAuthorized 一阶段不索 queue_return(拒因不再是 SCOPE_DENIED); Supervised 仍索
    r = _payload(gate.lybra_queue_return_dry_run({"autonomy_mode": "Supervised"}))
    assert r.get("error_code") == "SCOPE_DENIED"
    r = _payload(gate.lybra_queue_return_dry_run({"autonomy_mode": "PreAuthorized"}))
    assert r.get("error_code") != "SCOPE_DENIED"
    # converge / mark_concluded: 声明 queue_close, 调用时同样索取(原只在可见性上索)
    for name in ("lybra_converge_r_cards", "lybra_mark_concluded"):
        r = _payload(gate.TOOL_HANDLERS[name]({"task_id": "X-1"}))
        assert r.get("error_code") == "SCOPE_DENIED" and "'queue_close'" in r["message"], (name, r)
    monkeypatch.setenv("LYBRA_CAPABILITY_TOKEN", _token("queue_close"))
    assert _payload(gate.lybra_converge_r_cards({})).get("error_code") != "SCOPE_DENIED"


# ---------------------------------------------------------------------------
# 验收④ 退出码只在 verbs.schema 声明, 代码与声明一致
# ---------------------------------------------------------------------------

def test_item4_exit_codes_declared_once_and_code_reads_them():
    from tools.aipos_cli import agent_watch_fs, artifact_ingest
    from tools.aipos_cli.verb_contract import declared_exit_codes

    ingest = declared_exit_codes("lybra_artifact_ingest")
    assert ingest == {"recorded": 0, "shell_rejected": 1, "rejected": 4}
    assert (artifact_ingest.INGEST_EXIT_RECORDED, artifact_ingest.INGEST_EXIT_SHELL_REJECTED,
            artifact_ingest.INGEST_EXIT_REJECTED) == (ingest["recorded"], ingest["shell_rejected"], ingest["rejected"])
    watch = declared_exit_codes("lybra_agent_watch")
    assert watch == {"change": 0, "timeout": 2, "end_no_product": 3, "stall": 4, "usage": 5, "signal": 130}
    for outcome, const in (("change", "EXIT_CHANGE"), ("timeout", "EXIT_TIMEOUT"), ("end_no_product", "EXIT_END_NO_PRODUCT"),
                           ("stall", "EXIT_STALL"), ("usage", "EXIT_USAGE"), ("signal", "EXIT_SIGNAL")):
        assert getattr(agent_watch_fs, const) == watch[outcome], const
    # transitions 不再重复声明 ingest 退出码
    assert "exit_codes" not in json.loads(TRANSITIONS_SCHEMA.read_text(encoding="utf-8"))["artifact_ingest"]
    # 代码无写死: 两模块不再以数字字面量赋值退出码常量
    for mod in (agent_watch_fs, artifact_ingest):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert not re.search(r"^(INGEST_)?EXIT_\w+\s*=\s*\d+", src, re.M), mod.__name__
    # loop 的出口读取与 ingest/watch 同一实现
    from tools.aipos_cli import loop_driver

    assert "exit_code_in(" in Path(loop_driver.__file__).read_text(encoding="utf-8")


def test_item4_missing_exit_code_declaration_fails_closed():
    from tools.aipos_cli.verb_contract import exit_code_in
    from tools.schema_loader import SchemaLoadError

    with pytest.raises(SchemaLoadError):
        exit_code_in({"exit_codes": {"x": {"meaning": "no code"}}}, "x", "lybra_fake")
    with pytest.raises(SchemaLoadError):
        exit_code_in({}, "timeout", "lybra_fake")


def test_item4_real_cli_exit_codes_match_declaration(tmp_path):
    from tools.aipos_cli.aipos_cli import main
    from tools.aipos_cli.verb_contract import declared_exit_codes

    (tmp_path / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (tmp_path / "5_tasks" / "records").mkdir(parents=True)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    watch = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli", "agent", "watch", "--workspace-root", str(tmp_path),
         "--timeout", "0.3", "--interval", "0.05"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert watch.returncode == declared_exit_codes("lybra_agent_watch")["timeout"], (watch.stdout, watch.stderr)
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(io.StringIO()):
        rc = main(["artifact", "ingest", "--task-id", "F101-NOPE", "--workspace-root", str(tmp_path), "--dry-run", "--json"])
    assert rc == declared_exit_codes("lybra_artifact_ingest")["rejected"], buf.getvalue()
    # CLI help 的退出码从声明渲染
    help_buf = io.StringIO()
    with redirect_stdout(help_buf), pytest.raises(SystemExit):
        main(["agent", "--help"])
    flat = " ".join(help_buf.getvalue().split())
    for outcome, code in declared_exit_codes("lybra_agent_watch").items():
        assert f"{code}={outcome}" in flat.replace("- ", "-"), (outcome, flat)


# ---------------------------------------------------------------------------
# 验收⑤ 自描述与门指南示例无 lybra 身份字面
# ---------------------------------------------------------------------------

LYBRA_IDENTITY_RE = re.compile(r"\b(exec|owner|advisor|audit|auditor|owner-dispatch)\.lybra\b|pol_lybra_|kiwiai-dev")


def test_item5_examples_use_placeholders_not_lybra_identities():
    from tools.aipos_cli import cli_self_describe

    for path in (Path(cli_self_describe.__file__), Path(gate.__file__)):
        hits = [f"{path.name}:{i}: {line.strip()}" for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                if LYBRA_IDENTITY_RE.search(line)]
        assert hits == [], hits
    example = cli_self_describe._generate_example("lybra_queue_return", {})
    assert "<实例>" in example and "<信封>" in example and not LYBRA_IDENTITY_RE.search(example)
    shape = gate._get_verb_param_shape("lybra_queue_return_dry_run")
    assert shape["example"]["actor"] == "<实例>" and shape["example"]["owner_policy_ref"] == "<信封>"
    err = _payload(gate._validate_enroll_code_args({})[1])
    assert "<实例>" in json.dumps(err, ensure_ascii=False) and not LYBRA_IDENTITY_RE.search(json.dumps(err, ensure_ascii=False))
