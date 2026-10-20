"""AIPOS-F197 — 非代码交 pi 补完(gap #178 / #201 / #202; Owner 10-20: 两点补掉后统一切换 chris)。

件① 只写治理根的卡(card_needs_worktree = false, 非固定候选)进 pi 工位开工: my-tasks next_card 入选, 开工提示按
    verbs.schema lybra_my_tasks.kickoff.template_governance_root 渲染(产物落点说明, 不提分支 / 工作树)。
件② 交回契约分形(transitions.schema artifact_ingest.return.governance_root): Return frontmatter outputs 产出清单, 无分支三键;
    产品逐个核对并计算 sha256, 门交回记录绑定清单指纹; 顾问复核结案(mark-concluded)/ 声明独立审计时的裁决 / N5 无合入 finalize
    同核「此刻指纹 = 交回绑定」。
件③ 写仓 docs 卡(card_needs_worktree = true 的非 code 卡)门侧 artifact_subject 与 code 卡同样强制(判据 card_needs_worktree)。
件④ validation 卡候选源码送达: run-start / 开工稿 / 章程同源给出 `git archive <候选> | [ssh <别名>] tar -x -C <run>`, helper src-verify
    在目标主机按 git 规则核送达树 = 候选 tree(混入改动 / 错 sha 拒); ssh 形与本机形同一铸造口。

靶场 = AIPOS-F90/F95/F196 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 假 harness tests/fake_harness.py 替身 pi, 禁起真实 pi)
+ 本机模拟「目标主机」(假 ssh 把远端命令就地交 sh 执行并记录别名)。禁真门 / 真治理根 / 真工位 / 真 Mac / 真 chris。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + loop 全链靶场
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f196_validation_to_pi import (  # noqa: E402,F401  — F196 靶场(执行方式声明 + 假 ssh + 模拟目标主机)
    ALIAS, HOST, OWNER_REF, SRC, VAL, _cli, _declare_vr, _fp, _show, _validation_card, _vr, vrig,
)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

GOV = "PROBE-F197-G1"      # 只写治理根的 docs 卡(顾问复核)
GOVA = "PROBE-F197-G2"     # 只写治理根的 docs 卡(声明独立审计)
GOVX = "PROBE-F197-G3"     # 入口拒因靶
DOCW = "PROBE-F197-W1"     # 写仓 docs 卡(lane.repo = 产品仓)
POL_DOCS = "pol_probe_f197_docs"


def _docs_policy(gov: Path) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{POL_DOCS}.md", build_autonomy_policy_markdown(
        policy_id=POL_DOCS, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f197", task_selector_task_mode="docs", launch_harnesses=["pi"]))


def _gov_lane(task_id: str) -> dict:
    """只写治理根: lane.repo = 保留值 governance_root(card.schema intent_face.lane.governance_root_ref)。"""
    return {"repo": "governance_root", "paths": [f"task_cards/{task_id}/artifacts/"], "roles": ["executor"]}


def _gov_card(gov: Path, task_id: str, queue: str = "pending", *, audit: bool = False, **extra) -> Path:
    fields = {"harness": "pi", "audit": "required" if audit else "", "audit_by": AUDITOR if audit else "",
              "output_target": f"task_cards/{task_id}/artifacts/", "lane": _gov_lane(task_id)}
    fields.update(extra)
    return f78._card(gov, task_id, queue, assigned=EXEC, task_mode="docs", extra=fields)


def _claim(gov: Path, task_id: str, actor: str = EXEC) -> dict:
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    result = mutate_queue_task(gov, "claim", task_id=task_id, actor=actor, dry_run=False, profiles=load_agent_profiles(gov), with_records=True)
    assert result.get("moved"), result
    return result


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ===========================================================================
# 件①: 起草声明「只写治理根」= lane.repo 保留值 governance_root(缺 lane.repo 的缺省派生不变)
# ===========================================================================

def test_item1_draft_declares_governance_root_lane(vrig):
    from tools.aipos_cli.draft_writer import create_draft
    from test_aipos_f196_validation_to_pi import _draft_fm

    gov = vrig.gov
    base = {"title": "文档卡", "project": "lybra", "context_bundle": "t", "priority": "high", "status": "pending", "created_by": DRIVER,
            "needs_owner": False, "artifact_policy": "formal_write"}
    cases = {
        "docs + lane.repo=governance_root": ({**base, "task_id": "PROBE-F197-D1", "task_mode": "docs",
                                             "output_target": "task_cards/PROBE-F197-D1/artifacts/", "lane": _gov_lane("PROBE-F197-D1")}, False),
        "docs 缺 lane.repo(缺省派生不变)": ({**base, "task_id": "PROBE-F197-D2", "task_mode": "docs",
                                          "output_target": "task_cards/PROBE-F197-D2/artifacts/"}, True),
    }
    for label, (meta, want_tree) in cases.items():
        res = create_draft(gov, meta, "## Goal\n\n写文档\n", dry_run=True)
        fm = _draft_fm(res)
        needs = nr.card_needs_worktree(gov, {**fm, "task_id": meta["task_id"]})
        _show(f"[件① 起草 {label}] verdict={res['verdict']} harness={fm.get('harness')} assigned_to={fm.get('assigned_to')} "
              f"lane.repo={(fm.get('lane') or {}).get('repo')} card_needs_worktree={needs}")
        assert res["verdict"] != "BLOCK" and needs is want_tree, res["blocking_reasons"]
        if not want_tree:
            assert fm["harness"] == "pi" and fm["assigned_to"] == EXEC and nr.card_governance_outputs(gov, {**fm, "task_id": meta["task_id"]})
    bad = create_draft(gov, {**base, "task_id": "PROBE-F197-D3", "task_mode": "code", "output_target": "tools/x/",
                             "lane": {"repo": "governance_root", "paths": ["tools/x/"]}}, "## Goal\n\nx\n", dry_run=True)
    hit = [r for r in [*bad["blocking_reasons"], *bad.get("warnings", [])] if "governance_root" in r]
    _show(f"[件① 起草 code 卡写 lane.repo=governance_root] verdict={bad['verdict']} {hit}")
    assert hit and "LANE_REPO_UNDECLARED" in hit[0]
    from tools.aipos_cli.machine_zone import derive_intent_declarations

    intent = derive_intent_declarations({"task_id": "PROBE-F197-D3", "task_mode": "code", "output_target": "tools/x/",
                                         "lane": {"repo": "governance_root", "paths": ["tools/x/"]}}, gov)
    _show(f"[件① 发卡意图面判据(publish 同一函数)] blocking={intent['blocking_reasons']}")
    assert any("LANE_REPO_UNDECLARED" in r and "governance_root" in r for r in intent["blocking_reasons"])


# ===========================================================================
# 件①②: 只写治理根的 docs 卡 → loop 拉起 pi → 开工提示(无工作树模板)→ 交回无三键、产品核 sha256 → 顾问复核结案
# ===========================================================================

def test_item1_item2_governance_docs_card_pi_kickoff_outputs_return_advisor_close(vrig, monkeypatch):
    gov = vrig.gov
    _docs_policy(gov)
    _gov_card(gov, GOV)
    init_governance_repo(gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "docs")
    out = io.StringIO()
    res = run_loop(GOV, gov, actor=DRIVER, policy_id=POL_DOCS, out=out, interval=0.05, max_wait=60, max_steps=20)
    text = out.getvalue()
    _show("---- 件①② 只写治理根的 docs 卡 lybra loop 输出原文 ----\n" + text + "------------------------------")
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [GOV] and launches[0]["workstation"] == str(vrig.ws_exec)
    kickoff = (vrig.log / f"{GOV}.kickoff").read_text(encoding="utf-8")
    _show("---- 件① 拉起 pi 的开工提示原文(只写治理根的 docs 卡; template_governance_root)----\n" + kickoff + "\n------------------------------")
    assert "产物落点: 只写治理根——产物写本卡报告落点目录" in kickoff and "- outputs: (本卡产出文件清单" in kickoff
    assert "工作树" not in kickoff and "分支" not in kickoff and "commit_sha" not in kickoff
    # 交回: 门交回记录绑定产出清单(产品算 sha256), 无分支三键
    doc = gov / "task_cards" / GOV / "artifacts" / f"{GOV.lower()}-guide.md"
    ret = f90._records(gov, "returns", GOV)
    assert ret, "门交回记录缺"
    rfm = nr._read_frontmatter(ret[-1])
    subject = rfm.get("artifact_subject") or {}
    _show(f"[件② 门交回记录 {ret[-1].name}] artifact_subject = {json.dumps(subject, ensure_ascii=False)}")
    manifest = nr.governance_outputs_manifest(gov, GOV)
    _show(f"[件② 产品计算的产出清单] {json.dumps(manifest, ensure_ascii=False)}")
    assert subject.get("kind") == "governance_outputs" and subject.get("outputs_digest") == manifest["outputs_digest"]
    assert subject.get("outputs") == [f"{_sha(doc)}  task_cards/{GOV}/artifacts/{doc.name}"]
    assert "commit_sha" not in subject
    rtext = (gov / "task_cards" / GOV / "RETURN.md").read_text(encoding="utf-8")
    assert "commit_sha" not in rtext.split("---")[1]
    # 顾问复核: loop 停在快车道, 给出 mark-concluded 结案命令(带交回绑定指纹)
    _show(f"[件② loop 结束] outcome={res.outcome} exit={res.exit_code}\n  message: {res.message}")
    assert res.outcome == "not_derivable" and "只写治理根" in res.message and f"mark-concluded --task-id {GOV}" in res.message
    assert manifest["outputs_digest"] in res.message
    assert not f90._records(gov, "audit_dispatches", f"{GOV}R")
    # 交回后改产出 = 结案拒(INGEST_OUTPUTS_CHANGED), 复原后放行
    original = doc.read_bytes()
    doc.write_text(original.decode("utf-8") + "交回后偷改一行\n", encoding="utf-8")
    report = f"task_cards/{GOV}/RETURN.md"
    base = ["--workspace-root", gov, "mark-concluded", "--task-id", GOV, "--actor", DRIVER, "--report-path", report,
            "--conclusion-note", "顾问复核: 产出清单逐项核对通过"]
    rc, txt, err = _cli(*base, "--dry-run")
    _show(f"---- 件② 交回后改产出, 顾问 mark-concluded --dry-run exit {rc} ----\n{txt}{err}")
    assert rc == 1 and "INGEST_OUTPUTS_CHANGED" in txt and "交回后产出被改" in txt
    doc.write_bytes(original)
    rc, txt, err = _cli(*base, "--dry-run")
    assert rc == 0 and '"verdict": "PASS"' in txt, txt + err
    rc, txt, err = _cli(*base)
    _show(f"---- 件② 顾问复核结案 mark-concluded(确认)exit {rc} ----\n{txt}{err}")
    assert rc == 0 and (gov / "5_tasks" / "queue" / "completed" / f"{GOV.lower()}.md").is_file()
    rc, txt, err = _cli("--workspace-root", gov, "next", "--task-id", GOV)
    _show(f"---- 件② 结案后 lybra next --task-id {GOV} exit {rc} ----\n{txt}{err}")


def test_item1_item2_ingest_contract_and_refusals(vrig, capsys):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    gov = vrig.gov
    _gov_card(gov, GOVX)
    claimed = _claim(gov, GOVX)
    assert not claimed.get("worktree_created")
    slot = gov / "task_cards" / GOVX
    skeleton = (slot / "RETURN.md").read_text(encoding="utf-8")
    _show(f"[件② 认领模板 frontmatter(只写治理根)]\n{skeleton.split('---')[1].strip()}")
    assert "outputs: '(待填写: 本卡产出文件清单" in skeleton and "commit_sha" not in skeleton.split("---")[1]
    # my-tasks 开工面 / next_card(工位 /go 同读)
    path, _q = nr._find_task_in_queue(gov, GOVX)
    view = nr.card_workstation_view(gov, GOVX, nr._read_frontmatter(path))
    picked = nr.select_next_card([{**view, "task_id": GOVX, "queue_state": "claimed", "kickoff_refusal": None, "card_path": str(path)}])
    _show(f"---- 件① my-tasks next_card.kickoff(只写治理根)----\n{(picked['next_card'] or {}).get('kickoff')}")
    assert picked["next_card"] and picked["next_card"]["outputs_note"].startswith("只写治理根")
    # 子 agent 开工稿(lybra kickoff)同一契约
    rc, brief, err = _cli("kickoff", "--task-id", GOVX, "--workspace-root", gov)
    _show(f"---- 件① lybra kickoff(子 agent 开工稿, 只写治理根)exit {rc} ----\n{brief}{err}")
    assert rc == 0 and "outputs: (本卡产出文件清单" in brief and "commit_sha" not in brief
    rc, rendered, err = _cli("--workspace-root", gov, "card", "render", "--task-id", GOVX, "--harness", "pi")
    _show(f"[件① card render(pi)首行] {rendered.splitlines()[0] if rendered else err}")
    assert rc == 0 and rendered.splitlines()[0].startswith("工作树: 不建(本卡只写治理根")

    doc = slot / "artifacts" / "guide.md"
    _write(doc, "# guide\n")
    body = "# RETURN\n\n## 一句话结论\n文档完成。\n"
    cases = [
        ("旧契约(分支三键, 无 outputs)", f"---\ncommit_sha: {'a' * 40}\ntree_hash: {'b' * 40}\nbranch: card/{GOVX}\n---\n{body}",
         "INGEST_FRONTMATTER_MISSING"),
        ("清单含治理根他处(写权限边界外)", f"---\noutputs:\n- docs/outside.md\n---\n{body}", "INGEST_OUTPUT_OUT_OF_BOUNDARY"),
        ("清单文件不存在", f"---\noutputs:\n- task_cards/{GOVX}/artifacts/nope.md\n---\n{body}", "INGEST_OUTPUT_MISSING"),
        ("清单含 .. 段", f"---\noutputs:\n- task_cards/{GOVX}/../{GOVX}/artifacts/guide.md\n---\n{body}", "INGEST_OUTPUTS_INVALID"),
        ("清单项为符号链接", f"---\noutputs:\n- task_cards/{GOVX}/artifacts/link.md\n---\n{body}", "INGEST_OUTPUT_MISSING"),
        ("合规", f"---\noutputs:\n- task_cards/{GOVX}/artifacts/guide.md\n---\n{body}", "OK"),
    ]
    _write(gov / "docs" / "outside.md", "x\n")
    (slot / "artifacts" / "link.md").symlink_to(doc)
    from tools.aipos_cli.board_adapter import return_task

    for label, content, want in cases:
        (slot / "RETURN.md").write_text(content, encoding="utf-8")
        got = validate_task_artifact(GOVX, gov)
        _show(f"[件② artifact ingest 校验·{label}] category={got['category']} reasons={got['reasons']}"
              + (f" manifest={json.dumps(got.get('outputs_manifest'), ensure_ascii=False)}" if got.get("outputs_manifest") else ""))
        assert got["category"] == want, got
        # 门侧交回预演同核(手工 queue return 不绕过产物入口的清单核对)
        gate = return_task(task_id=GOVX, actor=EXEC, agent_instance=EXEC, owner_policy_ref="pol", result_summary="x", dry_run=True,
                           repo_root=gov)
        hits = [r for r in gate.get("blocking_reasons") or [] if "AIPOS-F197" in r]
        _show(f"  [门侧交回预演·{label}] {hits or '无产出清单拒因'}")
        assert bool(hits) is (want != "OK"), gate.get("blocking_reasons")
    assert got["ok"] and got["outputs_manifest"]["files"] == [{"path": f"task_cards/{GOVX}/artifacts/guide.md", "sha256": _sha(doc),
                                                               "size": doc.stat().st_size}]


# ===========================================================================
# 件②: 只写治理根的卡声明独立审计 → 审计 pi(不建取证树, 报告绑 outputs_digest)→ N5 无合入 → 结案
# ===========================================================================

def _real_finalize_for(cards: set[str]):
    original = f90._finalize_double

    def run(gov: Path, code_repo: Path, argv: list[str]) -> subprocess.CompletedProcess:
        task_id = argv[argv.index("--task-id") + 1]
        if task_id not in cards:
            return original(gov, code_repo, argv)
        rc, out, err = _cli(*argv[1:])
        _show(f"---- N5 真实 finalize({' '.join(argv[:8])} …)exit {rc} ----\n{out}{err}")
        return subprocess.CompletedProcess(argv, rc, out, err)

    return run


def test_item2_governance_docs_card_independent_audit_no_merge(vrig, monkeypatch):
    gov, code = vrig.gov, vrig.code_repo
    monkeypatch.setattr(f90, "_finalize_double", _real_finalize_for({GOVA}))
    _docs_policy(gov)
    _gov_card(gov, GOVA, audit=True)
    init_governance_repo(gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "docs")
    monkeypatch.setenv("FAKE_AUDIT_MODE", "work")
    main_before = f78._git(code, "rev-parse", "main")
    out = io.StringIO()
    res = run_loop(GOVA, gov, actor=DRIVER, policy_id=POL_DOCS, out=out, interval=0.05, max_wait=90, max_steps=40)
    text = out.getvalue()
    _show("---- 件② 只写治理根 + 独立审计 lybra loop 输出原文 ----\n" + text + "------------------------------")
    assert res.exit_code == 0 and res.outcome == "completed", text
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [GOVA, f"{GOVA}R"], launches
    digest = nr.governance_outputs_return_binding(gov, GOVA)["outputs_digest"]
    audit_kickoff = (vrig.log / f"{GOVA}R.kickoff").read_text(encoding="utf-8")
    _show("---- 件② 审计 pi 开工提示原文(被审卡只写治理根)----\n" + audit_kickoff + "\n------------------------------")
    assert "工作树: 不建(被审卡只写治理根" in audit_kickoff and f"- outputs_digest: {digest}(" in audit_kickoff
    assert "commit_sha" not in audit_kickoff
    assert not (code / ".worktrees" / GOVA).exists() and not (code / ".worktrees" / f"{GOVA}R").exists()
    vfm = nr._read_frontmatter(f90._records(gov, "audit_verdicts", GOVA)[-1])
    _show(f"[件② 裁决记录] verdict={vfm.get('verdict')} artifact_subject={json.dumps(vfm.get('artifact_subject'), ensure_ascii=False)}")
    assert vfm.get("verdict") == "PASS" and (vfm.get("artifact_subject") or {}).get("outputs_digest") == digest
    fin = nr._read_frontmatter(f90._records(gov, "finalizations", GOVA)[0])
    _show(f"[件② N5 finalization 记录] merge_commit={fin.get('merge_commit')} deploy_status={fin.get('deploy_status')} "
          f"push_status={fin.get('push_status')} reason={fin.get('deploy_status_reason')}")
    assert fin["merge_commit"] == f"outputs:{digest}" and fin["deploy_status"] == "not_applicable" and fin["push_status"] == "not_applicable"
    assert f90._records(gov, "closures", GOVA) and (gov / "5_tasks" / "queue" / "completed" / f"{GOVA.lower()}.md").is_file()
    assert f78._git(code, "rev-parse", "main") == main_before


def test_item2_verdict_ingest_refuses_changed_outputs(vrig):
    """被审卡交回后产出被改: 审计报告入口(INGEST_OUTPUTS_CHANGED)与门侧裁决预演同拒; 复原后入口放行。"""
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.board_adapter import audit_verdict_task

    gov = vrig.gov
    _gov_card(gov, GOVA, audit=True)
    _claim(gov, GOVA)
    doc = gov / "task_cards" / GOVA / "artifacts" / "guide.md"
    _write(doc, "# guide v1\n")
    (gov / "task_cards" / GOVA / "RETURN.md").write_text(
        f"---\noutputs:\n- task_cards/{GOVA}/artifacts/guide.md\n---\n# RETURN\n\n## 一句话结论\n文档完成。\n", encoding="utf-8")
    manifest = nr.governance_outputs_manifest(gov, GOVA)
    _write(gov / "5_tasks" / "records" / "returns" / GOVA / f"return_{GOVA}_x.md", f78._fm({
        "record_type": "return_record", "return_id": f"return_{GOVA}_x", "task_id": GOVA, "agent_instance": EXEC,
        "returned_at": "2026-10-20T00:00:00Z", "artifact_subject": {k: manifest[k] for k in ("kind", "repository", "outputs_digest", "outputs")}}))
    f78._card(gov, f"{GOVA}R", "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": GOVA, "claimed_by": AUDITOR, "audit": "", "audit_by": ""})
    report = gov / "task_cards" / f"{GOVA}R" / "RETURN.md"
    _write(report, f"---\nverdict: PASS\noutputs_digest: {manifest['outputs_digest']}\n---\n# 审计\n\n## 一句话结论\n通过。\n")
    ok = validate_task_artifact(f"{GOVA}R", gov)
    _show(f"[件② 审计报告入口(产出未改)] category={ok['category']} reasons={ok['reasons']}")
    assert ok["ok"], ok
    doc.write_text("# guide v2(交回后改)\n", encoding="utf-8")
    bad = validate_task_artifact(f"{GOVA}R", gov)
    _show(f"[件② 审计报告入口(交回后产出被改)] category={bad['category']} reasons={bad['reasons']}")
    assert bad["category"] == "INGEST_OUTPUTS_CHANGED"
    gate = audit_verdict_task(audit_task_id=f"{GOVA}R", reviewed_task_id=GOVA, actor=AUDITOR, agent_instance=AUDITOR, owner_policy_ref="pol",
                              verdict="PASS", findings_summary="x", evidence_refs=[], dry_run=True, repo_root=gov)
    hits = [r for r in gate.get("blocking_reasons") or [] if "AIPOS-F197" in r]
    _show(f"[件② 门侧裁决预演(交回后产出被改)] verdict={gate.get('verdict')} {hits}")
    assert gate.get("verdict") == "BLOCK" and hits and "INGEST_OUTPUTS_CHANGED" in hits[0]


# ===========================================================================
# 件③: 写仓 docs 卡(lane.repo = 产品仓)门侧 artifact_subject 与 code 卡同样强制(判据 card_needs_worktree)
# ===========================================================================

def test_item3_write_repo_docs_card_artifact_subject_enforced(vrig):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.board_adapter import audit_verdict_task

    gov, code = vrig.gov, vrig.code_repo
    f78._card(gov, DOCW, "claimed", assigned=EXEC, task_mode="docs",
              extra={"claimed_by": EXEC, "lane": {"repo": str(code), "paths": ["docs/"], "roles": ["executor"]}, "output_target": "docs/"})
    assert nr.card_needs_worktree(gov, {**nr._read_frontmatter(nr._find_task_in_queue(gov, DOCW)[0]), "task_id": DOCW})
    old_tip, _t = f78._branch_with_commit(code, DOCW, rel="docs/guide.md")
    f78._git(code, "checkout", "-q", f"card/{DOCW}")
    _write(code / "docs" / "guide.md", "# v2\n")
    f78._git(code, "commit", "-qam", "v2")
    tip = f78._git(code, "rev-parse", "HEAD")
    tree = f78._git(code, "rev-parse", "HEAD^{tree}")
    f78._git(code, "checkout", "-q", "main")
    f78._card(gov, f"{DOCW}R", "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": DOCW, "claimed_by": AUDITOR, "audit": "", "audit_by": ""})
    common = dict(audit_task_id=f"{DOCW}R", reviewed_task_id=DOCW, actor=AUDITOR, agent_instance=AUDITOR, owner_policy_ref="pol",
                  verdict="PASS", findings_summary="x", evidence_refs=[], dry_run=True, repo_root=gov)
    missing = audit_verdict_task(**common)
    hits = [r for r in missing.get("blocking_reasons") or [] if "AIPOS-F70" in r]
    _show(f"[件③ 写仓 docs 卡裁决缺 artifact_subject(门侧预演)] verdict={missing.get('verdict')} {hits}")
    assert missing.get("verdict") == "BLOCK" and hits and "被审卡写产品仓(task_mode=docs" in hits[0]
    given = audit_verdict_task(**common, artifact_subject={"repository": code.name, "commit_sha": tip, "tree_hash": tree})
    assert not [r for r in given.get("blocking_reasons") or [] if "AIPOS-F70" in r], given.get("blocking_reasons")
    # 入口: 报告 commit_sha ≠ 被审分支 tip = 拒(审的必须是当前产物); = tip 放行
    report = gov / "task_cards" / f"{DOCW}R" / "RETURN.md"
    _write(report, f"---\nverdict: PASS\ncommit_sha: {old_tip}\n---\n# 审计\n\n## 一句话结论\n通过。\n")
    stale = validate_task_artifact(f"{DOCW}R", gov)
    _show(f"[件③ 写仓 docs 卡审计报告 commit_sha = 旧 tip] category={stale['category']} reasons={stale['reasons']}")
    assert stale["category"] == "INGEST_TIP_MISMATCH"
    _write(report, f"---\nverdict: PASS\ncommit_sha: {tip}\n---\n# 审计\n\n## 一句话结论\n通过。\n")
    assert validate_task_artifact(f"{DOCW}R", gov)["ok"]
    # 判据单源: 门侧强制按 card_needs_worktree, 不再按 task_mode == code
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    assert 'elif reviewed_task_mode == "code":' not in src and "reviewed_writes_repo = card_needs_worktree(" in src


# ===========================================================================
# 件④: validation 卡候选源码送达(本机模拟目标主机 + 假 ssh): 本机形 / ssh 形渲染、送达 + 树指纹核对、混入改动 / 错 sha 拒
# ===========================================================================

def _run(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-o", "pipefail", "-c", command], capture_output=True, text=True, timeout=120)


def _src_line(lines: list[str]) -> str:
    return next(ln for ln in lines if "② 候选源码送达" in ln)


def test_item4_source_delivery_local_and_ssh_forms(vrig):
    from tools.aipos_cli import validation_root as vr

    gov = vrig.gov
    _validation_card(vrig)
    init_governance_repo(gov)
    decl = _declare_vr(vrig)
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", vrig.candidate, "--actor", DRIVER, "--confirm")
    _show(f"---- 件④ run-start(未声明 ssh 别名 = 本机形)exit {rc} ----\n{out}{err}")
    assert rc == 0 and "第二步(候选源码送达, 在治理根所在机运行): git -C " in out and f"| tar -x -C {vrig.base}/{VAL}/run-1" in out
    local_lines = vr.render_section_lines(gov, instance=EXEC, task_id=VAL, include_inactive=True)
    line = _src_line(local_lines)
    _show(f"[件④ 开工稿 / 章程验证根节·本机形]\n{line}")
    entry = next(e for e in vr.project_validation_roots(gov) if e["id"] == decl)
    run = vr.active_run(entry)
    local = vr.source_delivery(gov, entry, run)
    assert local["ssh_alias"] is None and local["deliver"] in line and local["verify"] in line
    assert local["deliver"].endswith(f"| tar -x -C {vrig.base}/{VAL}/run-1") and "| python3 - src-verify " in local["verify"]
    candidate_tree = f78._git(vrig.code_repo, "rev-parse", f"{vrig.candidate}^{{tree}}")
    assert local["tree"] == candidate_tree and local["repo"] == str(vrig.code_repo)

    # ssh 形(pi 工位在治理根所在机, 经 ssh 别名操作目标主机): 同一铸造口
    assert _vr(gov, "set-host", "--host", HOST, "--ssh-alias", ALIAS, "--owner-ref", OWNER_REF, "--actor", DRIVER, "--confirm")[0] == 0
    ssh = vr.source_delivery(gov, entry, run)
    ssh_line = _src_line(vr.render_section_lines(gov, instance=EXEC, task_id=VAL, include_inactive=True))
    _show(f"[件④ 开工稿 / 章程验证根节·ssh 形]\n{ssh_line}")
    assert ssh["ssh_alias"] == ALIAS and ssh["deliver"] in ssh_line and ssh["verify"] in ssh_line
    assert f"| ssh {ALIAS} 'tar -x -C {vrig.base}/{VAL}/run-1'" in ssh["deliver"]
    assert f"project validation-root helper | ssh {ALIAS} 'python3 - src-verify " in ssh["verify"]
    js = json.loads(_vr(gov, "init-command", "--id", decl, "--json")[1])
    assert js["source_delivery"]["deliver"] == ssh["deliver"] and js["source_delivery"]["verify"] == ssh["verify"]
    # 章程(本实例有效声明)同源
    charter = vr.render_section_lines(gov, instance=EXEC)
    assert ssh["deliver"] in _src_line(charter)

    # 目标主机(本机模拟): 目录链 → 送达 → 核对
    init = vr.init_command(gov, decl)["command"]
    steps = [("init-run", init), ("送达", ssh["deliver"]), ("核对", ssh["verify"])]
    for label, command in steps:
        proc = _run(command)
        _show(f"[件④ 经 ssh·{label}] $ {command}\n  exit {proc.returncode}: {(proc.stdout + proc.stderr).strip().splitlines()[-1] if (proc.stdout + proc.stderr).strip() else ''}")
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().splitlines()[-1].startswith(f"SRC_OK candidate={vrig.candidate} tree={candidate_tree}")
    src = vrig.base / VAL / "run-1" / "src"
    evidence = vrig.base / VAL / "run-1" / ".lybra-vr" / "src-verify.jsonl"
    rec = json.loads(evidence.read_text(encoding="utf-8").splitlines()[-1])
    _show(f"[件④ 送达核对证据(run 目录内)] {evidence}: {rec}")
    assert rec["ok"] and rec["actual_tree"] == candidate_tree and rec["git_dir_present"] is False
    ssh_log = vrig.ssh_log.read_text(encoding="utf-8")
    _show(f"[件④ 假 ssh 记录]\n{ssh_log}")
    assert f"{ALIAS}\ttar -x -C {vrig.base}/{VAL}/run-1" in ssh_log and f"{ALIAS}\tpython3 - src-verify " in ssh_log

    # 混入改动 = 拒
    (src / "app" / "feature.txt").write_text("本地未提交改动\n", encoding="utf-8")
    proc = _run(ssh["verify"])
    _show(f"[件④ 混入改动后核对] exit {proc.returncode}: {proc.stdout.strip()}")
    assert proc.returncode == 3 and "DENY [SRC_TREE_MISMATCH]" in proc.stdout
    # 错 sha(把 main 的树当候选送达)= 拒
    subprocess.run(["rm", "-rf", str(src)], check=True)
    wrong = ssh["deliver"].replace(f"--prefix=src/ {vrig.candidate}", "--prefix=src/ main")
    assert wrong != ssh["deliver"] and _run(wrong).returncode == 0
    proc = _run(ssh["verify"])
    _show(f"[件④ 错版本(main)送达后核对] $ {wrong}\n  exit {proc.returncode}: {proc.stdout.strip()}")
    assert proc.returncode == 3 and "DENY [SRC_TREE_MISMATCH]" in proc.stdout
    # 改参凑指纹(把期望 tree 改成实际)= 封印拒
    actual = json.loads(evidence.read_text(encoding="utf-8").splitlines()[-1])["actual_tree"]
    forged = ssh["verify"].replace(f"--expect-tree {candidate_tree}", f"--expect-tree {actual}")
    proc = _run(forged)
    _show(f"[件④ 改 --expect-tree 凑指纹] exit {proc.returncode}: {proc.stdout.strip()}")
    assert proc.returncode == 3 and "DENY [SRC_SEAL_MISMATCH]" in proc.stdout
    # 重送正确候选 = 放行
    subprocess.run(["rm", "-rf", str(src)], check=True)
    assert _run(ssh["deliver"]).returncode == 0 and _run(ssh["verify"]).returncode == 0


def test_item4_helper_tree_hash_matches_git(tmp_path):
    """helper 的 git tree 指纹(纯标准库)与 git 一致: 可执行位 / 符号链接 / 嵌套目录 / 「名/」排序 / 非 ASCII 名。"""
    from tools.aipos_cli.validation_root_helper import git_tree_hash

    repo = f78._init_product_repo(tmp_path / "repo")
    for rel, text in (("a/f", "x\n"), ("a.b", "y\n"), ("a-c", "z\n"), ("sub/deep/g", "q\n"), ("中.md", "中文\n"), ("run.sh", "#!/bin/sh\n")):
        _write(repo / rel, text)
    (repo / "run.sh").chmod(0o755)
    (repo / "link").symlink_to("a.b")
    f78._git(repo, "add", "-A")
    f78._git(repo, "commit", "-qm", "tree")
    out = tmp_path / "out"
    out.mkdir()
    proc = _run(f"git -C {repo} archive --format=tar --prefix=src/ HEAD | tar -x -C {out}")
    assert proc.returncode == 0, proc.stderr
    got = git_tree_hash(str(out / "src"))
    want = f78._git(repo, "rev-parse", "HEAD^{tree}")
    _show(f"[件④ helper tree 指纹 vs git] helper={got} git={want}")
    assert got["tree"] == want and got["symlinks"] == 1 and not got["problems"]


# ===========================================================================
# 件①②: chris 形(code_repo = 治理根且非 git 仓, 落点 5_tasks/records/returns)只写治理根卡: 开工 / 入口不再撞 LANE_REPO_UNDECLARED
# ===========================================================================

def test_item1_item2_chris_shape_governance_card_kickoff_and_ingest(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    gov = f78._make_gov(tmp_path, monkeypatch, shape="chris", project="chris-demo")
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    data["code_repo"] = str(gov)  # chris 现状形: code_repo 指治理根自身(非 git 仓)
    _write(gov / "project.json", json.dumps(data))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    card = "HBX-D1"
    f78._card(gov, card, "pending", task_mode="docs", extra={"project": "chris-demo", "harness": "pi", "audit": "required",
                                                             "output_target": "docs/p1/", "lane": {"repo": "governance_root",
                                                                                                    "paths": ["docs/p1/"]}})
    _claim(gov, card)
    path, _q = nr._find_task_in_queue(gov, card)
    view = nr.card_workstation_view(gov, card, nr._read_frontmatter(path))
    picked = nr.select_next_card([{**view, "task_id": card, "queue_state": "claimed", "kickoff_refusal": None, "card_path": str(path)}])
    kickoff = (picked["next_card"] or {}).get("kickoff")
    _show(f"---- 件① chris 形只写治理根卡 my-tasks next_card.kickoff ----\n{kickoff}\n  excluded={picked['next_card_excluded']}")
    slot = gov / "5_tasks" / "records" / "returns" / card
    assert kickoff and f"产物落点: 只写治理根——产物写本卡报告落点目录 {slot}/" in kickoff
    assert f"5_tasks/records/returns/{card}/artifacts/" in kickoff
    _write(slot / "artifacts" / "vendor-readiness-summary.md", "# 汇总\n")
    (slot / "RETURN.md").write_text(f"---\noutputs:\n- 5_tasks/records/returns/{card}/artifacts/vendor-readiness-summary.md\n---\n"
                                    "# RETURN\n\n## 一句话结论\n资料包完成。\n", encoding="utf-8")
    got = validate_task_artifact(card, gov)
    _show(f"[件② chris 形入口] category={got['category']} manifest={json.dumps(got.get('outputs_manifest'), ensure_ascii=False)}")
    assert got["ok"] and got["category"] == "OK"
