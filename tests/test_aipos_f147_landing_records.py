"""AIPOS-F147 — 记录与入库收口(gap #104/#99/#110/#120/#115/#113)。

靶场一律临时 git 仓 + 临时远端裸仓 + 临时治理根(复用 F94 靶场 rig 与已结案卡构造); 不连生产门, 不碰真实治理仓。

件① 落账完整: N6 任务范围(governance_commit.card_own_paths / resolve_task_scope)纳入本卡部署记录(authorization_ref = 本卡裁决)、
     审计报告快照(带 record_type, 过 B④)、loop 运行记录 .md(已结束 = 入判据; 运行中 = 只随提交带走; .log 经本地 exclude 不入库);
     合并后回归 async 事件记录写完后、loop 运行记录终态写完后经 reland_after_write 补一次落账(N6 同一提交口); 卡未结案 = 不提交。
     全链后 git status 无本卡未入账门记录(验收①)。
件② 记录类型单源: enums.schema record_type 含产品写出的全部取值(代码字面量 / RecordType / schema 声明, 不变量夹具); B④ 拒非法取值
     (取值检查只对新增; 豁免同 F128); schema 三处声明(#110 部署可选字段 / #120 lane_repo 集合 / role 读口)与实现对齐。
件③ 台账根缺省: paths.task_cards_root 未声明 = 取 return_root(default_from project_json.paths.return_root); lybra 形不变;
     未声明台账根的人肉期形项目 N6 完整性检查通过。
件④ 钩子探针: pre-commit 自检探针不写调用方索引; `git commit -- <路径>`(部分提交)成功, 他人暂存原样留在索引。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f73d_loop_driver import AUDITOR, DRIVER, EXEC, _fm, _write  # noqa: E402
from test_aipos_f94_governance_landing import (  # noqa: E402
    GOV_REL,
    TASK,
    _closed_card,
    _git,
    _head_files,
    rig,  # noqa: F401  (pytest fixture)
)
from tools.aipos_cli import governance_commit as gc  # noqa: E402

AUDIT = f"{TASK}R"
OTHER_CARD = "AIPOS-F147O"
HOOK_SRC = REPO_ROOT / "tools" / "hooks" / "governance-pre-commit"


def _show(line: str) -> None:
    print(line, flush=True)


def _verdict_id(task_id: str = TASK) -> str:
    return f"verdict_{task_id}_x"  # _closed_card 落的门生裁决记录文件名 stem


def _deploy(gov: Path, commit: str, *, verdict_ref: str | None = None, reason: str | None = None, at: str) -> Path:
    from tools.aipos_cli.deployment_record import write_deployment_record

    result = write_deployment_record(
        governance_root=gov, commit=commit, actor=EXEC,
        authorization_type="verdict_ref" if verdict_ref else "dev_override",
        authorization_ref=verdict_ref or reason or "", deployed_at=at,
        coverage={"interval": f"{'0' * 40}..{commit}", "covering_verdicts": [verdict_ref]} if verdict_ref else None,
    )
    path = Path(result["path"])
    return path if path.is_absolute() else gov / path


def _snapshot(gov: Path) -> Path:
    """审计报告快照经产品唯一实现 board_adapter._audit_report_snapshot_plan 生成(落点 / record_type 读声明)。"""
    from tools.aipos_cli.board_adapter import _audit_report_snapshot_plan

    _write(gov / "task_cards" / AUDIT / "RETURN.md",
           _fm({"task_id": AUDIT, "reviewed_task_id": TASK, "verdict": "PASS", "commit_sha": "a" * 40,
                "actor": AUDITOR, "agent_instance": AUDITOR}, "# audit\n\n## 一句话结论\nPASS\n"))
    plan = _audit_report_snapshot_plan(gov, AUDIT, TASK, _verdict_id())
    assert plan is not None
    path = gov / plan["path"]
    _write(path, plan["content"])
    return path


def _recorder(gov: Path, task_id: str = TASK):
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    warnings: list[str] = []
    rec = LoopRunRecorder(gov, task_id, driver=DRIVER, contract=load_loop_contract(), warn=warnings.append)
    return rec, warnings


def _rel(gov: Path, path: Path) -> str:
    return path.resolve().relative_to(gov.resolve()).as_posix()


# ===========================================================================
# 件① 验收①: 一张卡全链后 git status 无本卡未入账门记录
# ===========================================================================

def test_item1_full_chain_leaves_no_unlanded_gate_records_of_the_card(rig: dict, monkeypatch: pytest.MonkeyPatch):
    gov, repo = rig["gov"], rig["repo"]
    _closed_card(gov)
    # 一次更早的 loop 运行(门拒后结束)+ 本次运行中的 loop(N6 由它驱动)
    earlier, _w = _recorder(gov)
    earlier.end(outcome="gate_rejected", exit_code=2, reason="gate_rejected", message="门拒(夹具)")
    active, warnings = _recorder(gov)
    snapshot = _snapshot(gov)
    own_deploy = _deploy(gov, "b" * 40, verdict_ref=_verdict_id(), at="2026-10-10T01:00:00Z")
    other_deploy = _deploy(gov, "c" * 40, verdict_ref=_verdict_id(OTHER_CARD), at="2026-10-10T01:01:00Z")
    override_deploy = _deploy(gov, "d" * 40, reason="hotfix(夹具)", at="2026-10-10T01:02:00Z")

    scope = gc.task_scope_candidates(gov, TASK)
    _show("[① 范围候选 paths]\n  " + "\n  ".join(scope["paths"]) + "\n[① trailing]\n  " + "\n  ".join(scope["trailing"]))
    assert _rel(gov, own_deploy) in scope["paths"]
    assert _rel(gov, other_deploy) not in scope["paths"] and _rel(gov, override_deploy) not in scope["paths"]
    assert _rel(gov, earlier.path) in scope["paths"]  # 已结束 = 入判据
    assert _rel(gov, active.path) in scope["trailing"] and _rel(gov, active.path) not in scope["paths"]  # 运行中 = 只随提交
    assert not any(p.endswith(".log") for p in scope["paths"] + scope["trailing"])
    assert warnings == [], warnings

    # N6: task 范围精确提交(与 loop 派生的落账步同一函数)
    n6 = gc.governance_commit(gov, TASK, DRIVER, push=True)
    _show(f"[① N6] verdict={n6['verdict']} committed={n6['committed']} pushed={n6['pushed']}\n  " + "\n  ".join(n6["operations"]))
    assert n6["verdict"] == "PASS" and n6["committed"] and n6["pushed"], n6.get("message")
    committed = _head_files(repo)
    prefix = f"{GOV_REL}/"
    for path in (own_deploy, snapshot, earlier.path, active.path):
        assert prefix + _rel(gov, path) in committed, (path, committed)
    assert prefix + _rel(gov, other_deploy) not in committed and prefix + _rel(gov, override_deploy) not in committed
    assert not any(f.endswith(".log") for f in committed)
    assert gc.task_landing(gov, TASK)["landed"]  # 运行中的记录继续写也不拖住判据

    # loop 结束: 运行记录终态写在 N6 之后 → 补落账(loop 出口 0 同一调用)
    active.end(outcome="completed", exit_code=0, reason="completed", message="已结案")
    assert not gc.task_landing(gov, TASK)["landed"]  # 已结束的运行 .md 未提交 = 未落账(state lint 会点名)
    reland_loop = gc.reland_after_write(gov, TASK, DRIVER, what="loop 运行记录终态")
    _show(f"[① loop 终态补落账] {reland_loop['line']}")
    assert reland_loop["committed"] and reland_loop["pushed"]
    assert _head_files(repo) == {prefix + _rel(gov, active.path)}

    # 合并后回归 async 后台: 事件记录写完 → 补落账(post_merge_regression 后台入口原样执行, 检查本体替身)
    from tools.aipos_cli import post_merge_regression as pmr

    monkeypatch.setattr(pmr, "execute_check", lambda **kw: {"status": "pass", "merged_failures": [], "baseline_failures": [],
                                                            "baseline_source": "record:fixture", "merged_complete": True})
    args = argparse.Namespace(governance_root=str(gov), repo_root=str(repo), task_id=TASK, actor=EXEC, merge_commit="e" * 40,
                              runall_path="tests/run-all.sh", timeout_seconds=60, mode="warn", policy_source="fixture")
    rc = pmr._run_async_main(args)
    events = sorted((gov / "5_tasks" / "records" / "events" / TASK).glob("post_merge_regression_*.md"))
    assert rc == 0 and len(events) == 1
    assert _head_files(repo) == {prefix + _rel(gov, events[0])}
    assert _git(repo, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")

    status_card = _git(repo, "status", "--porcelain", "-uall", "--", f"{GOV_REL}/5_tasks", f"{GOV_REL}/task_cards")
    _show(f"[① 全链后 git status --porcelain -uall -- 5_tasks task_cards]\n{status_card or '(空)'}")
    leftover = [ln for ln in status_card.splitlines() if TASK in ln and OTHER_CARD not in ln]
    assert leftover == [], leftover
    # 剩下的只是非本卡者: 他卡的部署 / dev_override 部署 / 他卡记录(rig 预置)
    assert all(any(k in ln for k in ("deployment_", "AIPOS-F94O")) for ln in status_card.splitlines()), status_card
    assert gc.task_landing(gov, TASK)["landed"]
    from tools.aipos_cli.state_lint import GOVERNANCE_UNCOMMITTED, run_state_lint

    lint = run_state_lint(gov)
    assert not [i for i in lint["issues"] if i.get("code") == GOVERNANCE_UNCOMMITTED and i.get("task_id") in (TASK, AUDIT)]


def test_item1_reland_skips_unconcluded_card_and_loop_log_is_excluded_locally(rig: dict):
    gov, repo = rig["gov"], rig["repo"]
    head = _git(repo, "rev-parse", "HEAD")
    out = gc.reland_after_write(gov, OTHER_CARD, DRIVER, what="合并后回归事件记录")
    _show(f"[① 未结案] {out['line']}")
    assert out["committed"] is False and out["verdict"] is None and "未结案" in out["line"]
    assert _git(repo, "rev-parse", "HEAD") == head
    rec, warnings = _recorder(gov, OTHER_CARD)
    rec.end(outcome="completed", exit_code=0, reason="completed", message="x")
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    _show(f"[① .git/info/exclude]\n{exclude}")
    assert f"/{GOV_REL}/5_tasks/records/loop_runs/*/*.log" in exclude.splitlines() and warnings == []
    status = _git(repo, "status", "--porcelain", "-uall", "--", f"{GOV_REL}/5_tasks/records/loop_runs")
    assert status == f"?? {GOV_REL}/5_tasks/records/loop_runs/{OTHER_CARD}/{rec.path.name}", status  # .md 待落账, .log 不现


def test_item1_unreadable_running_record_blocks_scope_and_unreadable_legacy_deploy_is_reported(rig: dict):
    gov = rig["gov"]
    _closed_card(gov)
    legacy = gov / "5_tasks" / "records" / "deployments" / "1234abcd" / "deployment_20260801_000000.md"
    _write(legacy, "---\nrecord_type: deployment_record\nauthorization_ref: **broken: yaml\n---\n")
    scope = gc.resolve_task_scope(gov, TASK)
    assert scope["unattributed_deployments"] == [_rel(gov, legacy)]
    dry = gc.governance_commit(gov, TASK, DRIVER, dry_run=True, push=False)
    op = dry["operations"][0]
    _show(f"[① 读不出的部署记录出声] {op}")
    assert "unreadable, not attributable" in op and _rel(gov, legacy) in op
    bad = gov / "5_tasks" / "records" / "loop_runs" / TASK / "looprun_bad.md"
    _write(bad, "no frontmatter\n")
    blocked = gc.governance_commit(gov, TASK, DRIVER, dry_run=True, push=False)
    _show(f"[① 运行记录读不出] {blocked['verdict']} {blocked['message']}")
    assert blocked["verdict"] == "BLOCK" and "loop 运行记录读不出" in blocked["message"]


# ===========================================================================
# 件② 验收②: record_type 清单单源 + B④ 拒非法取值
# ===========================================================================

def _enum_record_types() -> set[str]:
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))
    return {v["value"] for v in enums["enums"]["record_type"]["values"]}


def _product_py_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "tools", "bin", "web"], cwd=REPO_ROOT, capture_output=True,
                         text=True, check=True).stdout
    files = []
    for rel in (r for r in out.split("\0") if r.endswith(".py")):
        parts = Path(rel).parts
        if any(p in ("tests", "test", "fixtures") for p in parts[:-1]) or Path(rel).name.startswith("test_"):
            continue
        files.append(REPO_ROOT / rel)
    return files


def _written_record_types() -> dict[str, set[str]]:
    """产品写出 record_type 的全部点(AST): dict 字面量键 "record_type" 的字符串值 / 关键字参数 record_type="…" /
    RecordType.<NAME> 引用; schema 声明里的 record_type / log_record_type 字符串。返回 {取值: {出处}}。"""
    found: dict[str, set[str]] = {}
    for path in _product_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "record_type" and isinstance(v, ast.Constant) and isinstance(v.value, str):
                        found.setdefault(v.value, set()).add(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.keyword) and node.arg == "record_type" and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) and node.value.value:
                found.setdefault(node.value.value, set()).add(f"{rel}:{node.value.lineno}")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "RecordType":
                found.setdefault(f"RecordType.{node.attr}", set()).add(f"{rel}:{node.lineno}")

    def walk(obj, where: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if where.endswith(".value_enums"):  # 字段 → 枚举名映射(record_file.value_enums), 不是记录类型取值
                    continue
                if k in ("record_type", "log_record_type") and isinstance(v, str) and re.fullmatch(r"[a-z_]+", v):
                    found.setdefault(v, set()).add(where)
                walk(v, f"{where}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{where}[{i}]")

    for name in ("verbs", "transitions", "config"):
        walk(json.loads((REPO_ROOT / "schema" / f"{name}.schema.json").read_text(encoding="utf-8")), f"schema/{name}.schema.json")
    return found


def test_item2_every_record_type_the_product_writes_is_in_the_single_enum():
    """不变量夹具(零容忍, 防回潮): 产品写出的每个 record_type 取值都在 enums.schema record_type; RecordType.<名> 都可解析。"""
    from tools.schema_constants import RecordType

    allowed = _enum_record_types()
    found = _written_record_types()
    literals = {v: sorted(w) for v, w in found.items() if not v.startswith("RecordType.")}
    consts = {v: sorted(w) for v, w in found.items() if v.startswith("RecordType.")}
    _show("[② 产品写出的 record_type 字面量/声明值]\n" + "\n".join(f"  {v}: {w[0]}" + (f" (+{len(w) - 1})" if len(w) > 1 else "")
                                                       for v, w in sorted(literals.items())))
    _show("[② RecordType 引用]\n  " + ", ".join(sorted(c.split('.', 1)[1] for c in consts)))
    missing = {v: w for v, w in literals.items() if v not in allowed}
    assert missing == {}, f"record_type 取值未在 enums.schema record_type 登记: {missing}"
    unresolved = {c: w for c, w in consts.items() if not hasattr(RecordType, c.split(".", 1)[1])}
    assert unresolved == {}, unresolved
    # gap #99 点名的在用类型均已登记(含 F131 loop_run / loop_run_log)
    for value in ("finalization_record", "amendment_record", "fix_closure_derivation", "audit_report_snapshot", "loop_run", "loop_run_log"):
        assert value in allowed
    # 写入点改读枚举常量(单源): 原字面量写入点不再出现
    for rel, literal in (("tools/aipos_cli/finalization_record.py", '"record_type": "finalization_record"'),
                         ("tools/aipos_cli/deployment_record.py", '"record_type": "deployment_record"'),
                         ("tools/aipos_cli/board_adapter.py", '"record_type": "amendment_record"'),
                         ("tools/aipos_cli/board_adapter.py", '"record_type": "fix_closure_derivation"')):
        assert literal not in (REPO_ROOT / rel).read_text(encoding="utf-8"), (rel, literal)


def _guardrail_rig(tmp_path: Path) -> Path:
    repo = tmp_path / "g"
    for d in ("governance", "5_tasks/records/claims/X-1"):
        (repo / d).mkdir(parents=True)
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    return repo


def test_item2_b4_rejects_record_type_value_outside_enum_and_passes_valid(tmp_path: Path):
    from tools.aipos_cli.governance_guardrails import check_entries, format_report, load_guardrail_declarations

    repo = _guardrail_rig(tmp_path)
    rec = repo / "5_tasks" / "records" / "claims" / "X-1"
    (rec / "good.md").write_text("---\nrecord_type: claim_record\n---\n", encoding="utf-8")
    (rec / "quoted.md").write_text("---\nrecord_type: 'loop_run'\n---\n", encoding="utf-8")
    (rec / "bad.md").write_text("---\nrecord_type: made_up_record\n---\n", encoding="utf-8")
    (rec / "old.md").write_text("---\nrecord_type: made_up_record\n---\n", encoding="utf-8")
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    assert decls.record_value_enums and decls.record_value_enums[0][:2] == ("record_type", "record_type")
    entries = [("A", "5_tasks/records/claims/X-1/good.md"), ("A", "5_tasks/records/claims/X-1/quoted.md"),
               ("A", "5_tasks/records/claims/X-1/bad.md"), ("M", "5_tasks/records/claims/X-1/old.md")]
    report = check_entries(repo, entries, decls, current_branch="main")
    text = format_report(report, decls)
    _show(f"[② B④ 拒非法取值 原文]\n{text}")
    b4 = [(v["file"], v["reason"]) for v in report.violations if v["check"] == "B④"]
    assert b4 == [("5_tasks/records/claims/X-1/bad.md",
                   "New record field 'record_type' value 'made_up_record' not in enums.schema record_type "
                   "(B④, declared in config.schema file_declarations.record_file.value_enums)")], b4  # 存量(M)不追溯


def test_item2_b4_value_enum_declaration_fail_closed_and_absent_unchanged(tmp_path: Path):
    from tools.aipos_cli.governance_guardrails import GuardrailDeclarationError, load_guardrail_declarations

    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    no_enums = tmp_path / "s1"
    no_enums.mkdir()
    (no_enums / "config.schema.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(GuardrailDeclarationError, match="enums.schema unreadable"):
        load_guardrail_declarations(no_enums)
    bad = json.loads(json.dumps(config))
    bad["governance_structure"]["file_declarations"]["record_file"]["value_enums"] = {"record_type": "no_such_enum"}
    s2 = tmp_path / "s2"
    s2.mkdir()
    (s2 / "config.schema.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    shutil.copy(REPO_ROOT / "schema" / "enums.schema.json", s2 / "enums.schema.json")
    with pytest.raises(GuardrailDeclarationError, match="no_such_enum"):
        load_guardrail_declarations(s2)
    absent = json.loads(json.dumps(config))
    del absent["governance_structure"]["file_declarations"]["record_file"]["value_enums"]
    s3 = tmp_path / "s3"
    s3.mkdir()
    (s3 / "config.schema.json").write_text(json.dumps(absent, ensure_ascii=False), encoding="utf-8")
    assert load_guardrail_declarations(s3).record_value_enums == ()


def test_item2_schema_declarations_aligned_with_implementation():
    transitions = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    decl = transitions["nodes"]["N5"]["deployment_record"]
    from tools.aipos_cli.deployment_record import build_deployment_record

    fm = build_deployment_record(commit="f" * 40, actor="a", authorization_type="verdict_ref", authorization_ref="v",
                                 runtime_directory="/x", coverage={"interval": "a..b", "covering_verdicts": ["v"]})
    fm_override = build_deployment_record(commit="f" * 40, actor="a", authorization_type="dev_override", authorization_ref="why")
    declared = set(decl["required_fields"]) | (set(decl["optional_fields"]) - {"writer"})
    undeclared = (set(fm) | set(fm_override)) - declared - {"operation", "commit_short"}
    assert {"coverage_interval", "covering_verdicts"} <= set(decl["optional_fields"])  # gap #110
    assert undeclared == set(), undeclared
    card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
    lane = card["fields"]["task_selector_lane_repo"]
    assert lane["type"] == "array" and lane["items"] == {"type": "string"}  # gap #120: F139 起为仓集合
    from tools.aipos_cli.workspace_config import repo_name_set

    assert repo_name_set(["a", "b", "a"]) == ["a", "b"] and repo_name_set("a") == ["a"]
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    role_desc = config["configuration_sources"]["role"]["description"]
    from tools.aipos_cli import enroll_client

    assert "enroll_client.read_role_record" in role_desc and callable(enroll_client.read_role_record)
    assert "读取唯一实现: charter_render.workstation_identity" not in role_desc


# ===========================================================================
# 件③ 验收③: 未声明 task_cards_root 的项目 N6 通过
# ===========================================================================

def _project(gov: Path, paths: dict | None) -> None:
    data = {"project": "gov", "config_version": 1}
    if paths is not None:
        data["paths"] = paths
    _write(gov / "project.json", json.dumps(data))


def test_item3_task_cards_root_defaults_to_return_root(tmp_path: Path):
    from tools.aipos_cli.workspace_config import _project_paths_declaration, project_paths

    decl = _project_paths_declaration()
    assert "default" not in decl["task_cards_root"] and decl["task_cards_root"]["default_from"] == "project_json.paths.return_root"
    lybra = tmp_path / "lybra"
    _project(lybra, None)
    p = project_paths(lybra)
    assert p["task_cards_root"] == lybra / "task_cards" == p["return_root"] and p["declared"]["task_cards_root"] is False
    chris = tmp_path / "chris"
    _project(chris, {"return_root": "5_tasks/records/returns"})
    c = project_paths(chris)
    assert c["task_cards_root"] == chris / "5_tasks" / "records" / "returns" and c["declared"]["task_cards_root"] is False
    both = tmp_path / "both"
    _project(both, {"return_root": "5_tasks/records/returns", "task_cards_root": "ledger"})
    assert project_paths(both)["task_cards_root"] == both / "ledger"  # 显式声明优先
    _show(f"[③ 台账根] lybra={p['task_cards_root']} chris形(未声明台账根)={c['task_cards_root']}")


def test_item3_default_from_cycle_or_unknown_key_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from tools import schema_loader
    from tools.aipos_cli import workspace_config as wc

    real = wc._project_paths_declaration()
    cyc = json.loads(json.dumps(real))
    cyc["return_root"] = {"type": "string", "default_from": "project_json.paths.task_cards_root"}
    monkeypatch.setattr(wc, "_project_paths_declaration", lambda: cyc)
    _project(tmp_path, None)
    with pytest.raises(schema_loader.SchemaLoadError, match="不成环"):
        wc.project_paths(tmp_path)
    unknown = json.loads(json.dumps(real))
    unknown["task_cards_root"] = {"type": "string", "default_from": "project_json.paths.nope"}
    monkeypatch.setattr(wc, "_project_paths_declaration", lambda: unknown)
    with pytest.raises(schema_loader.SchemaLoadError, match="nope"):
        wc.project_paths(tmp_path)


def test_item3_n6_completeness_passes_without_declared_task_cards_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """人肉期形(chris 形): return_root 声明在 5_tasks/records/returns, 未声明台账根; 交回文件名按项目候选(非 RETURN.md 亦认)。"""
    gov = tmp_path / "gov"
    for d in ("governance/decision_log", "5_tasks/queue/completed", "stage_archive"):
        (gov / d).mkdir(parents=True)
    _project(gov, {"return_root": "5_tasks/records/returns"})
    _write(gov / "5_tasks" / "records" / "returns" / "HBJ-1" / "return-HBJ-1.md", "---\ncommit_sha: x\n---\n# r\n")
    result = gc.check_governance_completeness(gov, "HBJ-1", repo_root=REPO_ROOT)
    _show(f"[③ N6 完整性(未声明 task_cards_root)] complete={result['complete']} missing={result['missing']} "
          f"task_cards={result['details']['task_cards']} archive={result['details']['archive_files']}")
    assert result["complete"] is True and result["missing"] == []
    assert result["details"]["archive_files"]["files"] == ["return-HBJ-1.md"]
    empty = gc.check_governance_completeness(gov, "HBJ-2", repo_root=REPO_ROOT)
    assert any("5_tasks/records/returns/HBJ-2/ (台账条目不存在)" in m for m in empty["missing"])  # 拒因指向随交回根的台账位


# ===========================================================================
# 件④ 验收④: 部分提交夹具
# ===========================================================================

def test_item4_partial_commit_with_hook_succeeds_and_probe_never_touches_caller_index(tmp_path: Path):
    repo = tmp_path / "gov"
    for d in ("governance", "5_tasks/records", "notes"):
        (repo / d).mkdir(parents=True)
    (repo / "governance" / "README.md").write_text("---\nstatus: active\n---\n# g\n", encoding="utf-8")
    (repo / "5_tasks" / "records" / ".keep.md").write_text("---\nrecord_type: claim_record\n---\n", encoding="utf-8")
    (repo / "notes" / "a.md").write_text("# a\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "--no-verify", "-m", "init")
    hook = repo / ".git" / "hooks" / "pre-commit"
    shutil.copy(HOOK_SRC, hook)
    os.chmod(hook, 0o755)
    (repo / "notes" / "a.md").write_text("# a\nchanged\n", encoding="utf-8")
    (repo / "notes" / "b.md").write_text("# b (staged by someone else)\n", encoding="utf-8")
    _git(repo, "add", "--", "notes/b.md")
    env = {**os.environ, "LYBRA_SCHEMA_DIR": str(REPO_ROOT / "schema")}
    proc = subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-m",
                           "partial", "--", "notes/a.md"], cwd=str(repo), capture_output=True, text=True, env=env)
    _show(f"[④ git commit -- notes/a.md] rc={proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode == 0, proc.stderr
    assert "invalid object" not in proc.stderr and "Error building trees" not in proc.stderr
    assert _head_files(repo) == {"notes/a.md"}
    assert _git(repo, "ls-files").splitlines() == ["5_tasks/records/.keep.md", "governance/README.md", "notes/a.md", "notes/b.md"]
    assert _git(repo, "diff", "--cached", "--name-only") == "notes/b.md"  # 他人暂存原样
    assert "决策日志_非ASCII自检.md" not in _git(repo, "ls-files", "--stage")
    # 普通提交照常(探针不受影响, 四检照跑)
    full = subprocess.run(["git", "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "-m", "rest"], cwd=str(repo),
                          capture_output=True, text=True, env=env)
    assert full.returncode == 0, full.stderr
    assert _head_files(repo) == {"notes/b.md"}
