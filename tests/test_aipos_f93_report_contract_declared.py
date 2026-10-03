"""AIPOS-F93 — 报告契约与交回约定声明化三件(F92R 实撞)。靶场复用 F73D/F78/F78C/F80/F89 既有构件(禁第二份靶场构造逻辑)。

① 报告必填字段单源告知: transitions artifact_ingest.<return|verdict>.required_frontmatter(+ field_hints)为唯一声明, 产品经
   next_resolver.report_frontmatter_contract / render_report_frontmatter_clause 渲染进五处——(a) 派生审计卡报告落点句、(b) 执行卡
   报告落点句(纪律段)、(c) my-tasks next_card.report_required_frontmatter(go.ts 原样列出; 审计卡带被审 tip/tree 实值)、
   (d) 认领时生成的报告模板、(e) 章程报告节。return 必填移除 model(产品按会话记录填写, 自报可选只作对照)。
   夹具: 声明加一项 → 五处同步变化; F92R 原样(缺 commit_sha)的报告仍被拒且拒因列出缺项。
② 注册码交付文案 = onboarding.enroll_delivery / render_enroll_command 唯一渲染(向导、门 paste_text / 动词说明、CLI 共用), 无退役斜杠命令。
③ 交回检查 TEST_NOT_IN_RUNALL / NO_TESTS 读项目声明 project.json test_contract(workspace_config.project_test_contract): lybra 形
   (声明 runall_path/require_tests)行为不变; probe 形(未声明)加测试的卡不被拒, 判据跳过并 warning; 拒因只引用声明值。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import AUDITOR, EXEC, _fm, _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _card, _git  # noqa: E402
from test_aipos_f78c_card_repo import _single_gov  # noqa: E402
from test_aipos_f80_zero_gate_charter import _identity, _make_gov as _charter_gov  # noqa: E402
from test_aipos_f89_audit_kickoff_surface import AUDIT, REVIEWED, _audit_rig, _claim_audit, _my_tasks_cli, _workstation  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.agent_profiles import load_agent_profiles  # noqa: E402
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter  # noqa: E402
from tools.aipos_cli.queue_mutation import mutate_queue_task  # noqa: E402
from tools.aipos_cli.task_loader import queue_root_for  # noqa: E402

EXEC_TASK = "AIPOS-F93X"
GO_FIXTURE = REPO_ROOT / "tests" / "ts" / "f93-go-report-fields.test.ts"
PROBE = {"return": "probe_return_field", "verdict": "probe_verdict_field"}


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _declare_probe_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """改声明一项: 两种报告各加一个必填键(+ 说明)。替换声明唯一读取口 next_resolver._artifact_ingest_declaration 的返回
    (五处渲染全经此口; 不改 load_schema 缓存对象——其 LRU 会被其他 schema 读取挤出重载)。monkeypatch 结束即还原。"""
    import copy

    decl = copy.deepcopy(nr._artifact_ingest_declaration())
    for kind, key in PROBE.items():
        decl[kind]["required_frontmatter"] = [*decl[kind]["required_frontmatter"], key]
        decl[kind]["field_hints"] = {**decl[kind]["field_hints"], key: f"{kind} 探针字段说明"}
    monkeypatch.setattr(nr, "_artifact_ingest_declaration", lambda: decl)


def _clause(kind: str, task_id: str | None) -> str:
    return nr.render_report_frontmatter_clause(nr.report_frontmatter_contract(kind, branch_task_id=task_id))


def _keys_of(path: Path) -> list[str]:
    meta, _body, warns = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    assert warns == [], warns
    return list(meta)


def _rig_with_exec_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    gov, repo, tip = _audit_rig(tmp_path, monkeypatch)
    _card(gov, EXEC_TASK, "pending")
    return gov, repo, tip


def _claim_exec(gov: Path) -> dict:
    return mutate_queue_task(gov, "claim", task_id=EXEC_TASK, actor=EXEC, dry_run=False,
                             profiles=load_agent_profiles(gov), with_records=True)


# ===========================================================================
# ① 五处同源: 声明加一项 → 五处同步变化
# ===========================================================================

def test_item1_five_places_render_one_declaration_and_follow_a_change(tmp_path, monkeypatch, capsys):
    from tools.aipos_cli.audit_derivation import build_derived_audit_task
    from tools.aipos_cli.card_render import render_card
    from tools.aipos_cli.charter_render import charter_render_context, render_charter
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段

    gov, repo, tip = _rig_with_exec_card(tmp_path, monkeypatch)  # 靶场先建(靶场构件会清 schema 缓存), 再改声明
    charter_gov = _charter_gov(tmp_path / "charter", monkeypatch, shape="lybra")
    before = {kind: _clause(kind, None) for kind in PROBE}
    _show("[①·改声明前] return: " + before["return"])
    _show("[①·改声明前] verdict: " + before["verdict"])
    assert "`model`" not in before["return"]
    _declare_probe_fields(monkeypatch)
    declared = {kind: list(nr._artifact_ingest_declaration()[kind]["required_frontmatter"]) for kind in PROBE}
    _show("[①·改声明后] 声明 required_frontmatter = " + json.dumps(declared, ensure_ascii=False))
    for kind, key in PROBE.items():
        assert key not in before[kind] and f"`{key}`({kind} 探针字段说明)" in _clause(kind, None)

    assert _claim_audit(gov)["wrote"] and _claim_exec(gov)["wrote"]
    tree = _git(repo, "rev-parse", f"{tip}^{{tree}}")

    # (a) 派生审计卡报告落点句(派生入口 build_derived_audit_task, 落点句 zero_gate_report_sentence)
    reviewed_fm = nr._read_frontmatter(queue_root_for(gov) / "claimed" / f"{REVIEWED.lower()}.md")
    derived = build_derived_audit_task(source_task_id=REVIEWED, source_metadata=reviewed_fm, source_path="x.md",
                                       return_record_ref="r", artifact_refs=[], repo_root=gov)
    line_a = next(ln for ln in derived["body"].splitlines() if ln.startswith("- **报告落位**"))
    _show("[①(a)·派生审计卡落点句] " + line_a)
    assert _clause("verdict", REVIEWED) in line_a and f"`{PROBE['verdict']}`" in line_a

    # (b) 执行卡报告落点句(机器区纪律段)
    exec_fm = nr._read_frontmatter(queue_root_for(gov) / "claimed" / f"{EXEC_TASK.lower()}.md")
    discipline = derive_machine_zone_纪律段(EXEC_TASK, exec_fm, governance_root=gov)
    line_b = next(ln for ln in discipline.splitlines() if ln.startswith("- **报告必填**"))
    _show("[①(b)·执行卡纪律段] " + line_b)
    assert _clause("return", EXEC_TASK) in line_b and f"`{PROBE['return']}`" in line_b

    # (c) my-tasks next_card.report_required_frontmatter(执行卡 / 审计卡; 审计卡带被审 tip/tree 实值)
    outputs = {}
    for role, instance, task_id in (("executor", EXEC, EXEC_TASK), ("auditor", AUDITOR, AUDIT)):
        ws = _workstation(tmp_path, gov, role=role, instance=instance)
        rc, out, err = _my_tasks_cli(["my-tasks", "--workstation", str(ws), "--task-id", task_id, "--json"], capsys)
        assert rc == 0, err
        outputs[role] = json.loads(out)
        card = outputs[role]["next_card"]
        assert card and card["task_id"] == task_id, outputs[role]
        _show(f"[①(c)·my-tasks {role}] next_card.report_required_frontmatter = "
              + json.dumps(card["report_required_frontmatter"], ensure_ascii=False))
    exec_contract = outputs["executor"]["next_card"]["report_required_frontmatter"]
    audit_contract = outputs["auditor"]["next_card"]["report_required_frontmatter"]
    assert [e["key"] for e in exec_contract] == declared["return"]
    assert [e["key"] for e in audit_contract] == declared["verdict"]
    sha_entry = next(e for e in audit_contract if e["key"] == "commit_sha")
    assert sha_entry["value"] == tip and tree in sha_entry["hint"] and "取证工作树 HEAD" in sha_entry["hint"]

    # go.ts 开工提示: 给定上面真实 my-tasks JSON → 原样列出(含被审 tip/tree)
    for role in ("executor", "auditor"):
        path = tmp_path / f"my-tasks-{role}.json"
        path.write_text(json.dumps(outputs[role], ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(["node", str(GO_FIXTURE), str(path)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        plan = json.loads(proc.stdout)
        assert plan["kind"] == "kickoff", plan
        _show(f"[①(c)·go.ts kickoff {role}]\n" + plan["kickoff"])
        for entry in outputs[role]["next_card"]["report_required_frontmatter"]:
            expect = f"- {entry['key']}: {entry['value']}({entry['hint']})" if entry["value"] else f"- {entry['key']}: ({entry['hint']})"
            assert expect in plan["kickoff"], (expect, plan["kickoff"])
    assert f"- commit_sha: {tip}(" in json.loads(subprocess.run(
        ["node", str(GO_FIXTURE), str(tmp_path / "my-tasks-auditor.json")], capture_output=True, text=True, timeout=60).stdout)["kickoff"]

    # (d) 认领时生成的报告模板: 键 = 声明清单, 值全为占位
    for task_id, kind in ((EXEC_TASK, "return"), (AUDIT, "verdict")):
        skeleton = nr.card_report_path(gov, task_id)
        keys = _keys_of(skeleton)
        meta, _b, _w = parse_markdown_frontmatter(skeleton.read_text(encoding="utf-8"))
        _show(f"[①(d)·认领模板 {task_id}] {skeleton.name} frontmatter 键 = {keys}")
        assert keys == declared[kind] and all(nr._is_placeholder_value(v) for v in meta.values())

    # (e) 章程报告节(executor / auditor 母本渲染)
    for role, prefix, kind in (("executor", "exec", "return"), ("auditor", "audit", "verdict")):
        harness = tmp_path / "pi" / f"lybra-{role}"
        harness.mkdir(parents=True)
        ctx = charter_render_context(charter_gov, identity=_identity(harness, role, f"{prefix}.lybra.hostl", "lybra"))
        rendered = render_charter((REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8"), ctx)
        line_e = next(ln for ln in rendered.splitlines() if "报告 frontmatter 必填" in ln)
        _show(f"[①(e)·{role} 章程] " + line_e.strip())
        assert _clause(kind, None) in line_e and f"`{PROBE[kind]}`" in line_e

    # card render(三 harness 同一模型)亦随声明
    assert f"`{PROBE['return']}`" in render_card(EXEC_TASK, gov, harness="pi")["files"]["stdout"]


def test_item1_model_not_required_and_f92r_original_report_still_rejected(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    ingest = nr._artifact_ingest_declaration()
    assert nr.required_return_frontmatter() == ["commit_sha", "tree_hash", "branch"]
    assert "model" in ingest["return"]["optional_frontmatter"]
    assert nr.missing_return_frontmatter({"commit_sha": "a" * 40, "tree_hash": "b" * 40, "branch": "card/X"}) == []

    gov, repo, tip = _audit_rig(tmp_path, monkeypatch)
    assert _claim_audit(gov)["wrote"]
    report = nr.audit_report_artifact_path(gov, AUDIT)
    # F92R 原样: frontmatter 只有 verdict / audit_task_id / reviewed_task_id / model(缺 commit_sha)
    report.write_text(_fm({"verdict": "PASS", "audit_task_id": AUDIT, "reviewed_task_id": REVIEWED, "model": "claude-sonnet-4-20250514"},
                          f"# 审计报告 — {AUDIT}\n\n## 一句话结论\nPASS: 原样复现 F92R。\n"), encoding="utf-8")
    derived = nr.derive_next_step(AUDIT, gov)
    _show("[①·F92R 原样报告 推导核] " + json.dumps({k: derived.get(k) for k in ("derivable", "missing_records", "action")},
                                                   ensure_ascii=False))
    assert derived["derivable"] is False and derived["action"]["type"] == "artifact_invalid"
    assert derived["missing_records"] == ["审计报告 frontmatter 未填/仍为占位: commit_sha"]
    checked = validate_task_artifact(AUDIT, gov)
    _show("[①·F92R 原样报告 ingest 校验] " + json.dumps({k: checked.get(k) for k in ("ok", "category", "reasons")}, ensure_ascii=False))
    assert checked["ok"] is False and any("commit_sha" in r for r in checked["reasons"])
    # 照开工提示补上产品给出的被审 tip → 完成判据成立
    meta, body, _w = parse_markdown_frontmatter(report.read_text(encoding="utf-8"))
    report.write_text(_fm({**meta, "commit_sha": tip}, body), encoding="utf-8")
    assert nr.missing_verdict_frontmatter(nr._read_frontmatter(report)) == []


def test_item1_contract_fail_closed_and_no_second_list():
    with pytest.raises(ValueError):
        nr.report_frontmatter_contract("finalization")
    # 本地 hints 表 / card_render 自写说明 / go.ts 自写清单 已退役: 只剩声明 field_hints
    rw = (REPO_ROOT / "tools" / "aipos_cli" / "record_writer.py").read_text(encoding="utf-8")
    cr = (REPO_ROOT / "tools" / "aipos_cli" / "card_render.py").read_text(encoding="utf-8")
    assert "实际模型自报, 如 claude-sonnet-5" not in rw and "model=实际模型自报" not in cr
    assert "(待填写: 被审分支" not in rw  # 原审计模板手写 hints 退役
    assert 'required_return_frontmatter()\n    except Exception' not in rw  # 原「声明读不到出无键模板」退役(fail-closed)


# ===========================================================================
# ② 注册码交付文案单源
# ===========================================================================

def test_item2_enroll_delivery_single_render_gate_cli_guide(tmp_path):
    from tools.aipos_cli.onboarding import (
        ENROLL_WORKSPACE_PLACEHOLDER,
        EXECUTOR_CODE,
        enroll_delivery,
        generate_onboarding_guide,
        render_enroll_command,
    )
    from tools.mcp_server import tools as gate

    delivery = enroll_delivery("LYBRAENROLL1.Zm9v")
    _show("[②·交付文案] " + json.dumps(delivery, ensure_ascii=False))
    assert delivery["paste_text"] == f"lybra roles enroll --code LYBRAENROLL1.Zm9v --workspace {ENROLL_WORKSPACE_PLACEHOLDER} --verify"
    assert delivery["paste_text"] in delivery["paste_instruction"]
    # 向导 Step 7 同一渲染
    guide = generate_onboarding_guide("probe", home_root=str(tmp_path / "home"), workspace_dir=str(tmp_path / "ws-exec"),
                                      auditor_dir=str(tmp_path / "ws-audit"), advisor_dir=str(tmp_path / "adv"))
    step7 = next(s for s in guide["steps"] if s["step_number"] == 7)["command"]
    assert render_enroll_command(EXECUTOR_CODE, str(tmp_path / "ws-exec")) in step7
    # 门: 动词说明 / 预演 confirm_output 同一渲染
    descs = {d["name"]: d["description"] for d in gate.WRITE_TOOL_DESCRIPTORS if "enroll_code" in d["name"]}
    assert descs and all(render_enroll_command("<code>") in text for text in descs.values()), descs
    # 产品面零退役斜杠命令
    hits = subprocess.run(["git", "grep", "-n", "/lybra enroll", "--", ".", ":!tests", ":!**/tests/**"], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    _show(f"[②·退役文案] git grep '/lybra enroll'(产品面) rc={hits.returncode} 命中={hits.stdout.strip() or 0}")
    assert hits.returncode == 1 and hits.stdout == ""


def test_item2_gate_issue_paste_text_equals_shared_render(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import issue_self_contained_code
    from tools.aipos_cli.onboarding import enroll_delivery

    root = tmp_path / "gate-ws"
    (root / ".lybra").mkdir(parents=True)
    result = issue_self_contained_code(root, role="executor", instance="exec.probe.hostl", gate_url="http://127.0.0.1:1",
                                       governance_root=str(tmp_path / "gov"), by="owner", reason="F93")
    expected = enroll_delivery(result["self_contained_code"])
    assert {k: result[k] for k in expected} == expected
    assert result["paste_text"].startswith("lybra roles enroll --code LYBRAENROLL1.")


# ===========================================================================
# ③ 交回检查读项目声明 test_contract
# ===========================================================================

def _card_branch_adding_test(repo: Path, task_id: str, *, register_in: str | None = None) -> None:
    _git(repo, "checkout", "-q", "-b", f"card/{task_id}")
    _write(repo / "tools" / "aipos_cli" / f"{task_id.lower()}.py", f"# {task_id}\n")
    _write(repo / "tests" / f"test_{task_id.lower()}.py", "def test_x(): pass\n")
    if register_in:
        _write(repo / register_in, f"tests/test_{task_id.lower()}.py\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{task_id}: work + test")
    _git(repo, "checkout", "-q", "main")


def _contract_reasons(gov: Path, task_id: str) -> tuple[list[str], list[str]]:
    import tools.aipos_cli.board_adapter as adapter

    fm = nr._read_frontmatter(queue_root_for(gov) / "claimed" / f"{task_id.lower()}.md")
    warnings: list[str] = []
    reasons = adapter._check_test_in_runall(task_id=task_id, repo_root=gov, card_frontmatter=fm, warnings=warnings)
    reasons += adapter._check_has_tests(task_id=task_id, repo_root=gov, card_frontmatter=fm, warnings=warnings)
    return reasons, warnings


def _declare_contract(gov: Path, contract: dict | None) -> None:
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl.pop("test_contract", None)
    if contract is not None:
        decl["test_contract"] = contract
    (gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")


def test_item3_probe_shape_undeclared_skips_with_warning_not_block(tmp_path, monkeypatch):
    gov, repo = _single_gov(tmp_path, monkeypatch)
    _card(gov, "PROBE-1", "claimed")
    _card_branch_adding_test(repo, "PROBE-1")  # 加了测试, 仓里没有任何测试清单
    reasons, warnings = _contract_reasons(gov, "PROBE-1")
    _show(f"[③·probe 形 未声明] reasons={reasons} warnings={json.dumps(warnings, ensure_ascii=False)}")
    assert reasons == []
    assert any(w.startswith("TEST_REGISTRY_UNDECLARED") for w in warnings)
    assert any(w.startswith("TEST_REQUIREMENT_UNDECLARED") for w in warnings)
    # 无测试改动的 code 卡同样不被 NO_TESTS 拒(未声明 require_tests)
    _card(gov, "PROBE-2", "claimed")
    _git(repo, "checkout", "-q", "-b", "card/PROBE-2")
    _write(repo / "tools" / "aipos_cli" / "p2.py", "# p2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "PROBE-2: code only")
    _git(repo, "checkout", "-q", "main")
    reasons2, _w = _contract_reasons(gov, "PROBE-2")
    assert reasons2 == []


def test_item3_lybra_shape_declared_behaves_as_before_and_texts_cite_declaration(tmp_path, monkeypatch):
    gov, repo = _single_gov(tmp_path, monkeypatch)
    _declare_contract(gov, {"runall_path": "tests/run-all.sh", "require_tests": True})
    _write(repo / "tests" / "run-all.sh", "#!/bin/bash\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "run-all")
    # 未登记 → TEST_NOT_IN_RUNALL(与 F49 判据同语义)
    _card(gov, "LYB-1", "claimed")
    _card_branch_adding_test(repo, "LYB-1")
    reasons, warnings = _contract_reasons(gov, "LYB-1")
    _show(f"[③·lybra 形 未登记] {reasons}")
    assert len(reasons) == 1 and reasons[0].startswith("TEST_NOT_IN_RUNALL") and "tests/run-all.sh" in reasons[0] and warnings == []
    # 登记了 → 绿
    _card(gov, "LYB-2", "claimed")
    _card_branch_adding_test(repo, "LYB-2", register_in="tests/run-all.sh")
    assert _contract_reasons(gov, "LYB-2") == ([], [])
    # 无测试 → NO_TESTS(require_tests=true)
    _card(gov, "LYB-3", "claimed")
    _git(repo, "checkout", "-q", "-b", "card/LYB-3")
    _write(repo / "tools" / "aipos_cli" / "l3.py", "# l3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "LYB-3: code only")
    _git(repo, "checkout", "-q", "main")
    reasons3, _w = _contract_reasons(gov, "LYB-3")
    _show(f"[③·lybra 形 无测试] {reasons3}")
    assert len(reasons3) == 1 and reasons3[0].startswith("NO_TESTS") and "tests/run-all.sh" in reasons3[0]

    # 其他项目自定清单: 拒因只引用声明值, 不出现 lybra 的 run-all 字面
    gov2, repo2 = _single_gov(tmp_path / "custom", monkeypatch)
    _declare_contract(gov2, {"runall_path": "ci/suite.txt", "require_tests": True})
    _write(repo2 / "ci" / "suite.txt", "tests/test_old.py\n")
    _git(repo2, "add", "-A")
    _git(repo2, "commit", "-q", "-m", "suite")
    _card(gov2, "CUS-1", "claimed")
    _card_branch_adding_test(repo2, "CUS-1")
    reasons4, _w = _contract_reasons(gov2, "CUS-1")
    _show(f"[③·自定清单 未登记] {reasons4}")
    assert len(reasons4) == 1 and "ci/suite.txt" in reasons4[0] and "run-all" not in reasons4[0]


def test_item3_contract_malformed_fail_closed_and_per_repo_override(tmp_path, monkeypatch):
    from tools.aipos_cli.workspace_config import project_test_contract

    gov, repo = _single_gov(tmp_path, monkeypatch)
    _card(gov, "BAD-1", "claimed")
    _card_branch_adding_test(repo, "BAD-1")
    for bad in ({"runall_path": "/abs/run-all.sh"}, {"require_tests": "yes"}, {"surprise": 1}, ["tests/run-all.sh"]):
        _declare_contract(gov, bad)
        reasons, _w = _contract_reasons(gov, "BAD-1")
        assert reasons and reasons[0].startswith("TEST_CONTRACT_INVALID"), (bad, reasons)
    # 按仓覆盖: repos.items 内的仓取覆盖值, 其余取顶层
    other = tmp_path / "repo-other"
    other.mkdir()
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl.pop("code_repo", None)
    decl["repos"] = {"default": "app", "items": {"app": str(repo), "lib": str(other)}}
    decl["test_contract"] = {"require_tests": True, "repos": {"lib": {"require_tests": False, "runall_path": "suite.list"}}}
    (gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    assert project_test_contract(gov, repo)["require_tests"] is True and project_test_contract(gov, repo)["runall_path"] is None
    lib = project_test_contract(gov, other)
    assert lib["require_tests"] is False and lib["runall_path"] == "suite.list"
    decl["test_contract"]["repos"] = {"nope": {"require_tests": True}}
    (gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    with pytest.raises(ValueError, match="TEST_CONTRACT_INVALID"):
        project_test_contract(gov, repo)


def test_f93_fixtures_registered_in_runall_and_no_swallowed_exceptions():
    import re

    runall = (Path(__file__).resolve().parent / "run-all.sh").read_text(encoding="utf-8")
    for name in ("tests/test_aipos_f93_report_contract_declared.py", "tests/ts/f93-go-report-fields.test.ts"):
        assert name in runall, name
    for rel in ("tools/aipos_cli/next_resolver.py", "tools/aipos_cli/record_writer.py", "tools/aipos_cli/workspace_config.py",
                "tools/aipos_cli/onboarding.py"):
        assert not re.search(r"except Exception:\s*\n\s*pass", (REPO_ROOT / rel).read_text(encoding="utf-8")), rel


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
