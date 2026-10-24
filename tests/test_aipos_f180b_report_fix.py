"""AIPOS-F180B: audit report header commit_sha form error -> loop resumes the same audit session to fix only the header.

Real case (10-17 AIPOS-F180R): the audit report commit_sha was copied with 3 middle chars missing (37 chars); the verdict
ingest correctly rejected INGEST_TIP_MISMATCH, but printed only 12 chars per side and the loop ended owner_needed.

item 1 diagnosis: INGEST_TIP_MISMATCH prints both full values plus a form diagnosis (single implementation
       artifact_ingest.tip_mismatch_diagnosis, declaration transitions.schema artifact_ingest.tip_mismatch_diagnosis).
item 2 report fix: correctable diagnosis -> loop resumes the recorded fixed session (F171B) once with the declared
       instruction (verbs.schema lybra_loop.launch.report_fix), then re-ingests; other_commit -> no resume, original rule;
       no session / exhausted -> audit_report_form_error (advisor_fix, not owner_needed).
item 3 charter: report header hint renders "git rev-parse ... paste as is" into the auditor charter.

Rig: F90/F95 launch rig (temporary governance root + fake gate + temporary workstations); fake pi = tests/fake_pi.py,
fake harness modes sha_* in tests/fake_harness.py. No real pi, no real loop, no real gate.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  - loop rig single source
import test_aipos_f95_harness_launch as f95  # noqa: E402  - launch rig single source
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  - pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  - pytest fixture (no leftover processes asserted)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f171b_loop_single_instance_resume import _launch_rig, _records, _run  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import tip_mismatch_declaration, tip_mismatch_diagnosis, validate_task_artifact  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, report_fix_declaration  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402

TASK, AUDIT = f95.TASK, f95.AUDIT


def _show(text: str) -> None:
    print(text, flush=True)


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _typo(tip: str) -> str:
    return tip[:20] + tip[23:]  # the F180R shape: 3 middle chars missing (same as tests/fake_harness.py sha_typo_once)


def _typo_text(tip: str) -> str:
    """Expected diagnosis text for _typo(tip); the common prefix is computed (a random tip may extend it past 20)."""
    common = len(os.path.commonprefix([_typo(tip), tip]))
    return f"长度 37 位 ≠ 40(形式错误); 与 tip 前 {common} 位相同、第 {common + 1} 位起不同(疑似抄错)"


def _report(gov: Path) -> Path:
    root, patterns = nr.auditor_artifact_watch(gov, AUDIT)
    return root / patterns[0]


def _audit_launches(gov: Path) -> list[dict]:
    return [la for r in _records(gov) for la in r.get("launches") or [] if la["card"] == AUDIT]


def _fixes(gov: Path) -> list[dict]:
    return [f for r in _records(gov) for f in r.get("report_fixes") or []]


# ===========================================================================
# item 1: diagnosis kinds (temporary git repo)
# ===========================================================================

def test_item1_diagnosis_kinds_full_values_and_declaration(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a").write_text("1", encoding="utf-8")
    _git(repo, "add", "a")
    _git(repo, "commit", "-qm", "a")
    parent = _git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("2", encoding="utf-8")
    _git(repo, "commit", "-qam", "b")
    tip = _git(repo, "rev-parse", "HEAD")
    cases = {
        "f180r_shape": _typo(tip),
        "short_prefix": tip[:12],
        "upper": tip.upper(),
        "garbage": "z" * 40,
        "other_commit": parent,
        "nowhere": ("0" if tip[0] != "0" else "1") * 40,
        "last_char": tip[:39] + ("0" if tip[39] != "0" else "1"),
    }
    got = {name: tip_mismatch_diagnosis(repo, tip, sha, branch="card/X") for name, sha in cases.items()}
    for name, diag in got.items():
        _show(f"[item1] {name}: commit_sha={diag['commit_sha']} tip={diag['tip']} kinds={diag['kinds']} "
              f"correctable={diag['correctable']} text={diag['text']}")
    assert got["f180r_shape"]["kinds"] == ["length_invalid", "prefix_miscopy"] and got["f180r_shape"]["correctable"]
    assert got["f180r_shape"]["text"] == _typo_text(tip)
    assert got["short_prefix"]["kinds"] == ["length_invalid", "prefix_miscopy"] and got["short_prefix"]["resolved"] == tip
    assert got["upper"]["kinds"][0] == "non_hex" and got["upper"]["correctable"] and got["upper"]["resolved"] == tip
    assert got["garbage"]["kinds"] == ["non_hex"] and got["garbage"]["text"] == "含非小写十六进制字符 z(形式错误)"
    assert got["other_commit"]["kinds"] == ["other_commit"] and not got["other_commit"]["correctable"]
    assert got["other_commit"]["text"] == f"指向被审仓里另一个提交 {parent}(真审错了产物)"
    assert got["other_commit"]["outlet"] == "审的不是当前产物"
    assert got["nowhere"]["kinds"] == ["not_a_commit"] and got["nowhere"]["correctable"]
    assert got["last_char"]["kinds"] == ["prefix_miscopy"] and got["last_char"]["text"].startswith("与 tip 前 39 位相同")
    assert all(d["branch"] == "card/X" and d["tip"] == tip for d in got.values())
    decl = tip_mismatch_declaration()
    assert set(decl["kinds"]) == {"length_invalid", "non_hex", "prefix_miscopy", "not_a_commit", "other_commit"}
    assert [k for k, v in decl["kinds"].items() if not v["correctable"]] == ["other_commit"]


def test_item1_declaration_fail_closed(monkeypatch):
    from tools.aipos_cli import next_resolver

    real = next_resolver._artifact_ingest_declaration()
    broken = {k: v for k, v in real.items() if k != "tip_mismatch_diagnosis"}
    monkeypatch.setattr(next_resolver, "_artifact_ingest_declaration", lambda: broken)
    with pytest.raises(SchemaLoadError, match="tip_mismatch_diagnosis"):
        tip_mismatch_declaration()
    bad = json.loads(json.dumps(real))
    bad["tip_mismatch_diagnosis"]["kinds"]["other_commit"].pop("correctable")
    monkeypatch.setattr(next_resolver, "_artifact_ingest_declaration", lambda: bad)
    with pytest.raises(SchemaLoadError, match="other_commit"):
        tip_mismatch_declaration()


# ===========================================================================
# item 2: typo -> diagnosis -> same-session resume fixes only the header -> ingest passes -> completed
# ===========================================================================

def test_item2_typo_resumed_same_session_then_ingest_passes(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "sha_typo_once")
    res, text = _run(lrig)
    _show("---- item2 typo -> resume -> completed: lybra loop output (excerpt) ----\n"
          + "\n".join(ln for ln in text.splitlines() if re.search(r"launch:|report_fix:|门拒 @|ready:|exit \d|done:", ln)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    tip = _git(lrig.code_repo, "rev-parse", f"card/{TASK}")
    typo = _typo(tip)
    reject = next(ln for ln in text.splitlines() if "门拒 @ verdict" in ln)
    assert (f"被审分支 card/{TASK} tip={tip} ≠ 报告自述 commit_sha={typo}。形式诊断: {_typo_text(tip)}。"
            "出口: 报告头形式错误, 不是审错了产物") in reject, reject
    first = json.loads((lrig.log / f"{AUDIT}.argv.json").read_text(encoding="utf-8"))
    second = json.loads((lrig.log / f"{AUDIT}.argv.resume1.json").read_text(encoding="utf-8"))
    sid = first[first.index("--session-id") + 1]
    _show(f"[item2] first launch argv: {first}\n[item2] resume argv: {second}")
    assert second == [("<resume>" if a == "<kickoff>" else a) for a in first], "same argv template and session id, only the instruction"
    fix_line = next(ln for ln in text.splitlines() if "report_fix:" in ln)
    assert fix_line.endswith(f"→ 同会话 {sid} 续接修报告头 1/1"), fix_line
    prompt = (lrig.log / f"{AUDIT}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[item2] resume instruction: {prompt}")
    expected = report_fix_declaration(load_loop_contract())["prompt"]
    for key, value in (("artifact", str(_report(lrig.gov))), ("branch", f"card/{TASK}"), ("commit_sha", typo), ("tip", tip),
                       ("diagnosis", _typo_text(tip))):
        expected = expected.replace("{" + key + "}", value)
    assert prompt == expected
    body = _report(lrig.gov).read_text(encoding="utf-8")
    assert f"commit_sha: {tip}\n" in body and "verdict: PASS\n" in body and "逐条复核通过(假 harness)" in body
    launches = [(la["session_id"], la["resume"], la["outcome"]) for la in _audit_launches(lrig.gov)]
    fixes = _fixes(lrig.gov)
    _show(f"[item2] run record launches[] (audit card): {launches}\n[item2] run record report_fixes: "
          + json.dumps([{k: f[k] for k in ('card', 'outcome', 'kinds', 'session_id')} for f in fixes], ensure_ascii=False))
    assert launches == [(sid, 0, "artifact_ready"), (sid, 1, "artifact_ready")]
    assert [(f["card"], f["outcome"], f["kinds"], f["session_id"]) for f in fixes] == [
        (AUDIT, "resumed", ["length_invalid", "prefix_miscopy"], sid)]
    log = Path(_records(lrig.gov)[-1]["log_path"]).read_text(encoding="utf-8")
    resume_line = next(ln for ln in log.splitlines() if " [resume] " in ln)
    _show(f"[item2] run log [resume] line: {resume_line}")
    assert resume_line.split(" ", 1)[1] == f"[resume] pi {AUDIT} 会话 {sid} 续接 1/2(AIPOS-F180B 修报告头 commit_sha)"


def test_item2_other_commit_not_resumed_original_rule(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    other = _git(lrig.code_repo, "rev-parse", "main")  # card branch starts here: a real commit that is not the tip
    monkeypatch.setenv("FAKE_AUDIT_MODE", "sha_other")
    monkeypatch.setenv("FAKE_AUDIT_OTHER_SHA", other)
    res, text = _run(lrig)
    _show("---- item2 other commit: lybra loop result ----\n" + res.message)
    assert res.exit_code == exit_code_for(load_loop_contract(), "gate_rejected") and res.reason == "gate_rejected", text
    tip = _git(lrig.code_repo, "rev-parse", f"card/{TASK}")
    assert (f"被审分支 card/{TASK} tip={tip} ≠ 报告自述 commit_sha={other}。形式诊断: 指向被审仓里另一个提交 {other}(真审错了产物): "
            "审的不是当前产物。出口: 审计体对被审分支当前 tip 重审并更新报告 commit_sha") in res.message
    assert not (lrig.log / f"{AUDIT}.argv.resume1.json").exists(), "other_commit must not resume"
    fixes = _fixes(lrig.gov)
    assert [(f["outcome"], f["kinds"], f["session_id"]) for f in fixes] == [("not_correctable", ["other_commit"], None)]
    rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show("[item2] loop status (excerpt): " + " | ".join(ln.strip() for ln in out.splitlines() if "下一动作" in ln))
    assert rc == 0 and "顾问下一动作: investigate(gate_rejected) — 第三档(归顾问, AIPOS-F225)" in out  # AIPOS-F225: 原判 owner_needed 归顾问


def test_item2_exhausted_then_next_run_resumes_recorded_session(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "sha_typo")  # resume rewrites the header with another wrong value
    res, text = _run(lrig)
    _show("---- item2 exhausted: lybra loop result ----\n" + res.message)
    tip = _git(lrig.code_repo, "rev-parse", f"card/{TASK}")
    assert res.exit_code == 2 and res.reason == "audit_report_form_error", text
    assert res.message.startswith(f"审计报告头形式错误, 本运行已续接审计会话修报告头 1/1 次仍被拒: 审计卡 {AUDIT} 报告头 "
                                  f"commit_sha={tip[:38]} ≠ 被审分支 tip={tip}(形式诊断: 长度 38 位 ≠ 40(形式错误); "
                                  "与 tip 前 38 位相同、第 39 位起不同(疑似抄错))"), res.message
    rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show("---- item2 exhausted: loop status ----\n" + out)
    assert rc == 0 and "顾问下一动作: advisor_fix(audit_report_form_error) — 例行结束 audit_report_form_error 第 1/3 次" in out
    # next run (new loop process, the earlier one is gone): the recorded session is resumed, this time the header is fixed
    monkeypatch.setenv("FAKE_AUDIT_MODE", "sha_typo_once")
    again, again_text = _run(lrig)
    _show("---- item2 next run: lybra loop output (excerpt) ----\n"
          + "\n".join(ln for ln in again_text.splitlines() if re.search(r"report_fix:|门拒 @|exit \d|done:", ln)))
    assert again.exit_code == 0 and again.outcome == "completed", again_text
    sids = {la["session_id"] for la in _audit_launches(lrig.gov)}
    got = [(la["resume"], la["outcome"]) for la in _audit_launches(lrig.gov)]
    _show(f"[item2] audit launches over both runs (resume, outcome): {got}; sessions={sids}")
    assert len(sids) == 1 and got == [(0, "artifact_ready"), (1, "artifact_ready"), (2, "artifact_ready")]
    assert [f["outcome"] for f in _fixes(lrig.gov)] == ["resumed", "exhausted", "resumed"]


def test_item2_no_session_ends_advisor_fix_not_owner(lrig, monkeypatch):
    # F95 rig template has no session declaration = the audit launch has no fixed session id = not resumable
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "sha_typo_once")
    res, text = _run(lrig)
    _show("---- item2 no session: lybra loop result ----\n" + res.message)
    assert res.exit_code == 2 and res.reason == "audit_report_form_error", text
    assert res.message.startswith("审计报告头形式错误, 审计会话不可续接(本卡运行记录无该审计卡的固定会话 id): ")
    assert [f["outcome"] for f in _fixes(lrig.gov)] == ["not_resumable"]
    status = lrr.loop_status(lrig.gov, TASK)
    na = status["next_action"]
    _show("---- item2 no session: next action ----\n" + json.dumps(na, ensure_ascii=False, indent=1))
    assert na["action"] == "advisor_fix" and na["reason"] == "audit_report_form_error"
    assert "lybra artifact ingest --task-id <审计卡ID> --kind verdict --workspace-root" in na["hint"]
    assert na["hint"].splitlines()[-1].startswith(f"lybra loop --task-id {TASK} --workspace-root {lrig.gov}")


# ===========================================================================
# item 3: charter renders the header rule; declarations single source
# ===========================================================================

def test_item3_auditor_charter_renders_rev_parse_rule(rig):
    from tools.aipos_cli import charter_render as cr

    master = (REPO_ROOT / "agents" / "roles" / "auditor" / "AGENTS.md").read_text(encoding="utf-8")
    text = cr.render_charter(master, cr.charter_render_context(rig.gov, identity={
        "instance": AUDITOR, "project": "lybra", "role": "auditor", "harness_root": str(rig.gov)}))
    hint = next(ln for ln in text.splitlines() if "`commit_sha`(" in ln)
    bullet = next(ln for ln in text.splitlines() if "**报告头 `commit_sha` 用 `git rev-parse" in ln)
    _show(f"---- item3 rendered auditor charter lines ----\n{hint}\n{bullet}")
    assert "用 `git rev-parse card/<被审卡ID>` 的输出原样粘贴, 禁手抄" in hint and "{{" not in hint
    assert "禁手抄**(AIPOS-F180B" in bullet


def test_item3_declarations_single_source():
    contract = load_loop_contract()
    decl = report_fix_declaration(contract)
    run_decl = lrr.run_record_declaration(contract)
    rules = lrr.next_action_declaration(run_decl)
    assert decl["max_per_run"] == 1 and decl["end_reason"] in run_decl["end_reasons"]
    assert rules["by_end_reason"][decl["end_reason"]] == "advisor_fix"
    assert rules["routine_rejections"]["end_reasons"][decl["end_reason"]]["fix"]
    assert "report_fixes" in run_decl
    broken = json.loads(json.dumps(contract))
    broken["launch"]["report_fix"]["prompt"] = "no placeholders"
    with pytest.raises(SchemaLoadError, match="report_fix.prompt"):
        report_fix_declaration(broken)
    ingest = (REPO_ROOT / "tools" / "aipos_cli" / "artifact_ingest.py").read_text(encoding="utf-8")
    driver = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert ingest.count("tip_mismatch_diagnosis(") == 3 and "tip_mismatch_diagnosis(" not in driver  # def + 2 decision sites
    assert driver.count("validate_task_artifact(") == 1 and driver.count("_launch_once(") == 3  # def + 2 call sites
    assert driver.count("_should_resume(") == 2 and "sleep(" not in driver and "except Exception" not in driver
    assert "tip[:12]" not in ingest and "commit_sha[:12]}: 审的不是当前产物" not in ingest
    assert validate_task_artifact.__module__ == "tools.aipos_cli.artifact_ingest"
