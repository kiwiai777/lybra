"""AIPOS-F241 — 审计运行证据不合时的处理: 拒因可见 / 同会话只补证据 / `cd <工作树> && X` 等价互认 / 步间部署变更自动续跑。

依据(10-26 chris 岚图 LANTU-CLTOOL4 运行 looprun_LANTU-CLTOOL4_20261026_145744): 审计 pi 第一会话写出 PASS 报告, 推导核按运行证据判据
判不合(卡面 `cd {worktree} && env -i … python3 …`, 报告条目同写带 cd 的命令并引用 run-long 记录, 记录命令为不带 cd 的 X、--cwd = 审计工作树),
loop 日志与运行记录不留原因、另开新会话从头再审约 14 分钟; 同晚两次部署 loop 都在两步之间按 deploy_changed 停下要人重跑。

验收(卡面 ★验收):
 ① 夹具审计卡报告证据缺一条 → loop 日志 / 运行记录 / loop status / next 同一拒因原文;
 ② 审计会话可续接 → 同会话(会话 id 同上一次)续接只补证据, 报告改写后裁决入门; 不可续接 → 新拉起开工稿含拒因原文与只补证据指令;
    续接后仍不合 = 声明的结束原因(runtime_evidence_exhausted, advisor_fix);
 ③ `cd {worktree} && X` 卡面 + cwd = 审计工作树的 run-long 记录 → 判合原文; cwd 不同 → 仍判不合原文(chris 第一会话同形样本自造);
 ④ 夹具 loop 步间部署变更 → 自动换快照续跑原文; 连续换快照超声明上限 → 照旧 deploy_changed 停下。
 (⑤ 对 chris 真实报告只读重判见 RETURN.md, 不进夹具: 夹具禁读真实治理根。)

靶场一律临时根(F90 进程内真门 + 临时治理根 / 产品仓 / HOME; F95 拉起靶场 + 假 pi tests/fake_pi.py + 假 harness tests/fake_harness.py
evidence_* 模式); 不连真门、不起真 pi、不读真实治理根。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  - loop rig single source
import test_aipos_f95_harness_launch as f95  # noqa: E402  - launch rig single source
import test_aipos_f152_deploy_consistency as f152  # noqa: E402  - deploy dir fixtures single source
import test_aipos_f162_runtime_evidence_supplementary_audit as f162  # noqa: E402  - runtime evidence report helpers
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  - pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  - pytest fixture (no leftover processes asserted)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f166_loop_friction import _deploy_with_code_stub, _Replaced  # noqa: E402
from test_aipos_f171b_loop_single_instance_resume import _launch_rig, _records, _run  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import validate_task_artifact  # noqa: E402
from tools.aipos_cli.board_adapter import evidence_command_key, runtime_evidence_check  # noqa: E402
from tools.aipos_cli.loop_driver import (  # noqa: E402
    WaitReexec, exit_code_for, load_loop_contract, run_loop, runtime_evidence_fix_declaration, runtime_evidence_fix_prompt,
    step_reexec_declaration,
)
from tools.aipos_cli.run_long import _files as run_long_files  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402

TASK, AUDIT = f90.TASK, f90.AUDIT
CMD_X = "git rev-parse HEAD"
CMD_CD = "cd {worktree} && " + CMD_X  # chris LANTU-CLTOOL4 卡面同形: `cd {worktree} && X`
EVIDENCE = {"required": True, "commands": [CMD_CD]}


def _show(text: str) -> None:
    print(text, flush=True)


def _audit_wt(gov: Path) -> str:
    return str(nr.card_worktree_location(gov, AUDIT)[1])


def _run_long_dir(base: Path, name: str, command: str, cwd: str) -> Path:
    """自造一份已收尾的 run-long 运行记录(与 `lybra run-long` 同形: run.json / output.log / exit_code / result.json)。"""
    directory = base / name
    files = run_long_files(directory)
    directory.mkdir(parents=True)
    log = files["log"]
    log.write_text(f"$ {command}\nok\n", encoding="utf-8")
    sha = hashlib.sha256(log.read_bytes()).hexdigest()
    files["meta"].write_text(json.dumps({"record_type": "run_long", "name": name, "task_id": AUDIT, "command": command, "cwd": cwd,
                                         "log_path": str(log)}, ensure_ascii=False), encoding="utf-8")
    files["exit"].write_text("0\n", encoding="utf-8")
    files["result"].write_text(json.dumps({"exit_code": 0, "log_sha256": sha}), encoding="utf-8")
    return directory


def _write_report(rig, entries: list[dict] | None) -> Path:
    gov = rig.gov
    tip = _git(rig.code_repo, "rev-parse", f"card/{TASK}")
    section = "" if entries is None else "\n## 独立运行证据\n\n```json\n" + json.dumps(entries, ensure_ascii=False) + "\n```\n"
    report = gov / "task_cards" / AUDIT / "audit_report.md"
    _write(report, "---\n" + "".join(f"{k}: {v}\n" for k, v in {"task_id": AUDIT, "reviewed_task_id": TASK, "verdict": "PASS",
                                                                     "commit_sha": tip, "model": f90.SELF_AUDIT_MODEL}.items())
           + f"---\n# 审计报告 {AUDIT}\n\n## 一句话结论\n逐条复核通过。\n{section}")
    f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL,
                    name=f"sess-{AUDIT}-{hashlib.sha256(report.read_bytes()).hexdigest()[:6]}.jsonl")
    return report


def _loop_until_audit(rig, script):
    seen: list[str] = []
    checks: dict[str, list] = {}
    res, text = f162._loop(rig, TASK, f162._watch(rig, script, seen, checks))
    return res, text, seen, checks


# ===========================================================================
# 件③ 等价命令互认(判据唯一 board_adapter.runtime_evidence_check / 比对键 evidence_command_key)
# ===========================================================================

def test_item3_command_key_only_cd_prefix_on_same_worktree(tmp_path):
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(wt)
    cases = {
        "cd 同树 → X": (f"cd {wt} && {CMD_X}", str(wt), CMD_X),
        "cd 经符号链接 / .. 规范化后同树 → X": (f"cd {link}/sub/.. &&   {CMD_X}", str(wt), CMD_X),
        "cd 引号同树 → X": (f"cd '{wt}' && {CMD_X}", str(wt), CMD_X),
        "cd 别的树 → 原文": (f"cd {tmp_path} && {CMD_X}", str(wt), f"cd {tmp_path} && {CMD_X}"),
        "cd 相对路径 → 原文": (f"cd wt && {CMD_X}", str(wt), f"cd wt && {CMD_X}"),
        "别的前缀(;) → 原文": (f"cd {wt}; {CMD_X}", str(wt), f"cd {wt}; {CMD_X}"),
        "无 worktree → 原文": (f"cd {wt} && {CMD_X}", None, f"cd {wt} && {CMD_X}"),
        "不带 cd → 空白折叠原文": (f"  {CMD_X}  ", str(wt), CMD_X),
    }
    for label, (command, worktree, want) in cases.items():
        got = evidence_command_key(command, worktree)
        _show(f"[件③·比对键] {label}: {command!r} @ {worktree} → {got!r}")
        assert got == want, label
    decl = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    match = decl["artifact_ingest"]["verdict"]["runtime_evidence"]["command_match"]
    assert "AIPOS-F241 件③" in match and "board_adapter.evidence_command_key" in match


@pytest.mark.parametrize("shape", ["chris_cd_entry_with_run_long", "run_long_only", "plain_entry_x"])
def test_item3_cd_card_command_matches_run_long_in_audit_worktree(rig, shape):
    """卡面 `cd {worktree} && X`: ① chris 第一会话同形(条目 command 写带 cd 的命令 + 引用 run-long 记录, 记录命令 X、--cwd = 审计工作树);
    ② 只引用 run-long 记录; ③ 条目直接写 X + worktree = 审计工作树 + 日志 → 都判合, loop 一轮入门结案(不再另拉审计)。"""
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(rig.gov)

    def write(r):
        wt = _audit_wt(r.gov)
        rl = _run_long_dir(r.gov / "task_cards" / AUDIT / "run_long", "audit-test-full", CMD_X, wt)
        entry = {"chris_cd_entry_with_run_long": {"command": f"cd {wt} && {CMD_X}", "worktree": wt, "exit_code": 0, "run_long": str(rl)},
                 "run_long_only": {"run_long": str(rl)},
                 "plain_entry_x": {"command": CMD_X, "worktree": wt, "exit_code": 0, "log_path": str(rl / "output.log"),
                                   "log_sha256": hashlib.sha256((rl / "output.log").read_bytes()).hexdigest()}}[shape]
        _write_report(r, [entry])

    res, text, seen, checks = _loop_until_audit(rig, {AUDIT: [write]})
    check = checks[AUDIT][0]
    _show(f"[件③·{shape}] 裁决入口预检: category={check['category']} ok={check['ok']} reasons={check.get('reasons')}")
    assert check["category"] == "OK" and check["ok"], check
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert seen == ["executor", f"auditor:{AUDIT}"], seen
    assert "runtime_evidence:" not in text


def test_item3_cd_card_command_other_cwd_still_rejected(rig):
    """run-long 记录 --cwd 不是审计分配工作树(别的目录)= 仍判不合; 拒因写明报告与记录两侧原文。"""
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(rig.gov)
    other = rig.gov.parent / "elsewhere"
    other.mkdir()
    results: list[dict] = []

    def write(r):
        wt = _audit_wt(r.gov)
        rl = _run_long_dir(r.gov / "task_cards" / AUDIT / "run_long", "other-cwd", CMD_X, str(other))
        rl2 = _run_long_dir(r.gov / "task_cards" / AUDIT / "run_long", "same-cwd-other-cmd", f"{CMD_X} --short", wt)
        report = _write_report(r, [{"command": f"cd {wt} && {CMD_X}", "worktree": wt, "exit_code": 0, "run_long": str(rl)},
                                       {"run_long": str(rl2)}])
        fm = nr._read_frontmatter(nr._find_task_in_queue(r.gov, AUDIT)[0])
        results.append(runtime_evidence_check(r.gov, AUDIT, fm, report_path=report,
                                              report_fm=nr._read_frontmatter(report, allow_missing_block=True)))

    _loop_until_audit(rig, {AUDIT: [write]})
    got = results[0]
    _show("[件③·cwd 不同 / 加参数] 判不合原文:\n  " + "\n  ".join(got["problems"]) + f"\n  missing_commands={got['missing_commands']}")
    assert got["category"] == "RUNTIME_EVIDENCE_MISSING"
    assert any("字段 command, worktree 与引用的 run-long 记录" in p and f"worktree: 报告写 {_audit_wt(rig.gov)!r} vs 记录 {str(other)!r}" in p
               for p in got["problems"]), got["problems"]
    rendered = f"cd {_audit_wt(rig.gov)} && {CMD_X}"
    assert got["missing_commands"] == [rendered]
    assert any(f"卡面声明的命令 {rendered!r}(声明原文 {CMD_CD!r})只有不合格的运行记录(见上列该条拒因), 不算已运行" in p
               for p in got["problems"]), got["problems"]


# ===========================================================================
# 件① 拒因可见(手工 / 子 agent 审计 = 不可续接, 照原规则重拉, 提示附只补证据指令)
# ===========================================================================

def test_item1_reason_visible_in_log_record_status_next_and_manual_hint_carries_instruction(rig):
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(rig.gov)
    script = {AUDIT: [lambda r: _write_report(r, None)]}  # 只交一份缺证据节的 PASS 报告, 之后不再改写(等待超时结束)
    res, text, _seen, checks = _loop_until_audit(rig, script)
    _show(f"---- 件① loop 输出原文 ----\n{text}")
    line = next(ln for ln in text.splitlines() if re.match(r"^\[\d+\] runtime_evidence: ", ln))
    index, detail = re.match(r"^\[(\d+)\] (runtime_evidence: .*)$", line).groups()
    report = rig.gov / "task_cards" / AUDIT / "audit_report.md"
    assert detail == (f"runtime_evidence: RUNTIME_EVIDENCE_MISSING: {report}: 报告缺「独立运行证据」节(标题行 `## 独立运行证据`); "
                      "卡面要求独立运行证据(来源 reviewed_card)。"), detail
    # 运行记录该步 detail(同文, 经 redact_progress)
    meta = _records(rig.gov)[-1]
    step = next(s for s in meta["steps"] if s.get("detail"))
    _show(f"[件①] 运行记录该步: {json.dumps(step, ensure_ascii=False)}")
    assert step["index"] == int(index) and step["detail"] == detail and step["card"] == AUDIT
    # 不可续接(手工模式无会话)= 记 report_fixes not_resumable, 照原规则重拉, 手工提示附拒因与只补证据指令
    fixes = meta.get("report_fixes") or []
    _show(f"[件①] 运行记录 report_fixes: {json.dumps(fixes, ensure_ascii=False)}")
    assert [(f["card"], f["role"], f["outcome"], f["kind"], f["kinds"]) for f in fixes] == [
        (AUDIT, "auditor", "not_resumable", "runtime_evidence", ["RUNTIME_EVIDENCE_MISSING"])]
    manual = text[text.index(f"[{index}] manual: "):]
    manual = manual[:manual.index("(未拉起: ")]  # 手工提示原文(指令多行, 至「未拉起」说明止)
    rendered = f"cd {_audit_wt(rig.gov)} && {CMD_X}"
    assert "返工: 审计报告被拒收(RUNTIME_EVIDENCE_MISSING), 只补证据指令(派给审计实例): 你的审计报告" in manual
    assert f"- `{rendered}`" in manual and "只改写报告「独立运行证据」节" in manual
    # loop status: 同一原文
    rc, status, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    _show(f"---- 件① loop status 原文 ----\n{status}{err}")
    assert f"  [{index}] {detail}" in status.splitlines()
    # next(审计卡): 同一原文
    rc, nxt, err = _cli(["next", "--task-id", AUDIT, "--workspace-root", str(rig.gov)])
    _show(f"---- 件① next --task-id {AUDIT} 原文 ----\n{nxt}{err}")
    assert detail in nxt.splitlines(), nxt
    derivation = nr.derive_next_step(AUDIT, rig.gov)
    assert f"runtime_evidence: {derivation['runtime_evidence']['line']}" == detail


# ===========================================================================
# 件② 同会话只补证据(F95 拉起靶场 + 假 pi 固定会话)
# ===========================================================================

def _ev_fixes(gov: Path) -> list[dict]:
    return [f for r in _records(gov) for f in r.get("report_fixes") or [] if f.get("kind") == "runtime_evidence"]


def test_item2_resumable_same_session_supplements_only_evidence_then_verdict_ingests(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "evidence_once")
    res, text = _run(lrig)
    _show("---- 件② 可续接: lybra loop 输出原文(节选) ----\n"
          + "\n".join(ln for ln in text.splitlines() if re.search(r"launch:|runtime_evidence:|report_fix:|ready:|exit \d|done:", ln)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    first = json.loads((lrig.log / f"{AUDIT}.argv.json").read_text(encoding="utf-8"))
    second = json.loads((lrig.log / f"{AUDIT}.argv.resume1.json").read_text(encoding="utf-8"))
    sid = first[first.index("--session-id") + 1]
    _show(f"[件②] 首次拉起 argv: {first}\n[件②] 续接 argv: {second}")
    assert second == [("<resume>" if a == "<kickoff>" else a) for a in first], "同一 argv 模板与会话 id, 只换指令"
    assert not (lrig.log / f"{AUDIT}.argv.resume2.json").exists()
    prompt = (lrig.log / f"{AUDIT}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[件②] 续接指令原文:\n{prompt}")
    root, patterns = nr.auditor_artifact_watch(lrig.gov, AUDIT)
    report = root / patterns[0]
    wt = _audit_wt(lrig.gov)
    assert prompt.startswith(f"你的审计报告 {report} 的「独立运行证据」节被裁决入口判运行证据不合(RUNTIME_EVIDENCE_MISSING): {report}: "
                             "报告缺「独立运行证据」节(标题行 `## 独立运行证据`); 卡面要求独立运行证据(来源 reviewed_card)。只补跑下列")
    assert f"\n- `cd {wt} && {CMD_X}`\n" in prompt and "裁决与其余正文不动" in prompt
    fix_line = next(ln for ln in text.splitlines() if "report_fix:" in ln)
    assert fix_line.endswith(f"→ 同会话 {sid} 续接补运行证据 1/1"), fix_line
    ev_line = next(ln for ln in text.splitlines() if " runtime_evidence: " in ln or re.match(r"^\[\d+\] runtime_evidence: ", ln))
    assert "RUNTIME_EVIDENCE_MISSING" in ev_line
    body = report.read_text(encoding="utf-8")
    _show(f"[件②] 续接后报告全文:\n{body}")
    assert "verdict: PASS\n" in body and "逐条复核通过(假 harness)" in body and "## 独立运行证据" in body
    assert f'"command": "{CMD_X}", "worktree": "{wt}"' in body, "补证据条目 = 在审计工作树跑 X(件③ 等价互认入门)"
    fixes = _ev_fixes(lrig.gov)
    _show(f"[件②] 运行记录 report_fixes: {json.dumps(fixes, ensure_ascii=False)}")
    assert [(f["outcome"], f["session_id"], f["kinds"]) for f in fixes] == [("resumed", sid, ["RUNTIME_EVIDENCE_MISSING"])]
    audit_launches = [(la["session_id"], la["resume"]) for r in _records(lrig.gov) for la in r.get("launches") or [] if la["card"] == AUDIT]
    assert audit_launches == [(sid, 0), (sid, 1)], "只续接, 不另开新审计会话"
    verdict = nr._read_frontmatter(f90._records(lrig.gov, "audit_verdicts", TASK)[0])
    assert verdict["verdict"] == "PASS" and verdict["audit_task_id"] == AUDIT


def test_item2_resumed_still_unequal_ends_declared_reason_advisor_fix(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "evidence_bad")
    res, text = _run(lrig)
    _show("---- 件② 续接后仍不合: lybra loop 结果原文 ----\n" + res.message)
    decl = runtime_evidence_fix_declaration(load_loop_contract())
    assert res.reason == decl["end_reason"] == "runtime_evidence_exhausted", text
    assert res.exit_code == exit_code_for(load_loop_contract(), "not_derivable")
    assert res.message.startswith("审计报告运行证据不合, 本运行已续接审计会话补运行证据 1/1 次仍被拒: ")
    assert "只补证据指令(会话不可续接时顾问派给本卡审计实例): 你的审计报告" in res.message
    assert [f["outcome"] for f in _ev_fixes(lrig.gov)] == ["resumed", "exhausted"]
    lines = [ln for ln in text.splitlines() if re.match(r"^\[\d+\] runtime_evidence: ", ln)]
    _show("[件②] 两轮拒因原文:\n" + "\n".join(lines))
    assert len(lines) == 2 and f"{CMD_X} --extra" in lines[1], "第二轮拒因写明卡面命令与报告命令两侧原文"
    rc, status, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show("[件②] loop status 下一动作: " + " | ".join(ln.strip() for ln in status.splitlines() if "下一动作" in ln))
    assert "advisor_fix(runtime_evidence_exhausted)" in status


def test_item2_not_resumable_fresh_launch_kickoff_carries_reason_and_supplement_only(lrig, monkeypatch):
    # F95 靶场模板无会话声明 = 审计拉起无固定会话 id = 不可续接 → 照原规则新拉起, 开工稿附上一轮拒因原文与只补证据指令
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)
    f90._card(lrig.gov, TASK, "pending", harness="pi", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "evidence_once")
    res, text = _run(lrig)
    _show("---- 件② 不可续接: lybra loop 输出原文(节选) ----\n"
          + "\n".join(ln for ln in text.splitlines() if re.search(r"launch:|runtime_evidence:|report_fix:|ready:|exit \d|done:", ln)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    kickoff = (lrig.log / f"{AUDIT}.kickoff").read_text(encoding="utf-8")  # 末次(重拉)开工稿
    note = kickoff[kickoff.index("返工(审计报告被拒收, 只补证据"):]
    _show(f"[件②] 重拉开工稿附注原文:\n{note}")
    assert "上一轮审计会话不可续接, 本会话按此指令只补证据, 不从头重审): 你的审计报告" in note
    assert "缺「独立运行证据」节" in note and f"- `cd {_audit_wt(lrig.gov)} && {CMD_X}`" in note
    fixes = _ev_fixes(lrig.gov)
    assert [(f["outcome"], f["session_id"]) for f in fixes] == [("not_resumable", None)]
    assert "本卡运行记录无该审计卡的固定会话 id" in fixes[0]["detail"]


def test_item2_declaration_single_source_and_fail_closed():
    contract = load_loop_contract()
    decl = runtime_evidence_fix_declaration(contract)
    assert decl["max_per_run"] == 1 and decl["end_reason"] in contract["run_record"]["end_reasons"]
    prompt = runtime_evidence_fix_prompt(contract, {"report": "/r.md", "category": "C", "line": "C: a | b", "missing_commands": ["x", "y"]})
    assert prompt.startswith("你的审计报告 /r.md 的「独立运行证据」节被裁决入口判运行证据不合(C): a | b。") and "\n- `x`\n- `y`\n" in prompt
    broken = json.loads(json.dumps(contract))
    del broken["launch"]["report_fix"]["runtime_evidence"]["fresh_note"]
    with pytest.raises(SchemaLoadError, match="fresh_note"):
        runtime_evidence_fix_declaration(broken)
    broken = json.loads(json.dumps(contract))
    broken["launch"]["report_fix"]["runtime_evidence"]["end_reason"] = "no_such_reason"
    with pytest.raises(SchemaLoadError, match="end_reason"):
        runtime_evidence_fix_declaration(broken)


# ===========================================================================
# 件④ 步间部署变更自动换快照续跑
# ===========================================================================

def _executor_then_deploy(rig, deploy, release):
    base = f90._agent_watch(rig, [])

    def watch(args, expect_ready, deploy_guard=None):
        rc = base(args, expect_ready)  # 交回已落后才部署: 下一步是执行步(return), 本轮在两步之间
        f152._switch(deploy, release)
        return rc

    return watch


def test_item4_deploy_switch_between_steps_reexecs_and_continues_to_completion(rig, tmp_path):
    deploy = _deploy_with_code_stub(tmp_path / "product")
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    launched: dict = {}

    def launcher(args, target):
        from tools.aipos_cli.aipos_cli import build_parser

        launched.update(args=list(args), target=Path(target))
        ns = build_parser().parse_args(list(args))
        out2 = io.StringIO()
        res2 = run_loop(ns.task_id, Path(ns.workspace_root), actor=ns.actor, policy_id=ns.envelope, out=out2,
                        watch=f152._guarded(f90._agent_watch(rig, [])), interval=ns.interval, max_wait=ns.max_wait,
                        max_steps=ns.max_steps, deploy_guard=f152._guard(deploy, Path(target).name), continues_run=ns.continues_run)
        raise _Replaced((res2, out2.getvalue(), ns))

    out = io.StringIO()
    with pytest.raises(_Replaced) as replaced:
        run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=_executor_then_deploy(rig, deploy, "r-b"), interval=0.05,
                 max_wait=5, max_steps=30, deploy_guard=f152._guard(deploy, "r-a"), wait_reexec=WaitReexec(launcher=launcher))
    res2, text2, ns = replaced.value.result
    text = out.getvalue()
    _show(f"---- 件④ 旧进程(快照 r-a)loop 原文(交回落盘后部署切到 r-b)----\n{text}")
    _show(f"---- 件④ 续跑参数原文 ----\nlybra {' '.join(launched['args'])}")
    _show(f"---- 件④ 新进程(快照 r-b)loop 原文 exit {res2.exit_code} ----\n"
          + "\n".join(ln for ln in text2.splitlines() if not ln.startswith("[ENVELOPE")))
    line = next(ln for ln in text.splitlines() if "部署变更: 两步之间部署已更新" in ln)
    assert line.startswith("[3] 部署变更: 两步之间部署已更新(本进程运行快照 commit aaaaaaaaaaaa → 现部署 commit bbbbbbbbbbbb); "
                           "本轮尚未推导 / 执行(无门动词在执行) → re-exec 到新部署快照 r-b"), line
    assert "连续步间换快照 1/3" in line
    assert ns.max_steps == 30 - 2, "已落 2 轮(认领 + 等交回), 本轮未执行不计"
    assert res2.exit_code == 0 and res2.outcome == "completed", text2
    assert f"({TASK}): lybra queue claim" not in text2 and f"run return @ claim/claimed ({TASK})" in text2, "已落的步不重复, 从当前推导点(交回)续跑"
    old, new = sorted(_records(rig.gov), key=lambda m: (str(m.get("started_at")), str(m.get("run_id"))))[:2]
    _show("---- 件④ 运行记录 ----\n" + json.dumps({k: old.get(k) for k in ("run_id", "end_reason", "end_message", "reexec")}, ensure_ascii=False)
          + "\n" + json.dumps({k: new.get(k) for k in ("run_id", "continues_run", "end_reason")}, ensure_ascii=False))
    assert old["end_reason"] == "deploy_reexec" and old["reexec"]["kind"] == "step" and old["reexec"]["to_release"] == "r-b"
    assert old["end_message"] == "两步之间部署已更新, 已 re-exec 到新部署快照 r-b 从当前推导点续跑"
    assert new["continues_run"] == old["run_id"] and new["end_reason"] == "completed"
    rc, status, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    _show(f"---- 件④ loop status 原文 ----\n{status}")
    assert f"续等: 接上一运行 {old['run_id']}(部署已更新" in status


def test_item4_consecutive_step_reexec_over_cap_stops_deploy_changed(rig, tmp_path):
    """部署连环更新: 新进程起跑即见部署又变(未落任何一步)= 连续步间换快照; 达声明上限 max_consecutive 照旧 deploy_changed 停下。"""
    deploy = _deploy_with_code_stub(tmp_path / "product")
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    cap = int(step_reexec_declaration(load_loop_contract())["max_consecutive"])
    hops: list[str] = []
    texts: list[str] = []

    def launcher(args, target):
        from tools.aipos_cli.aipos_cli import build_parser

        name = Path(target).name
        hops.append(name)
        f152._switch(deploy, "r-a" if name == "r-b" else "r-b")  # 新进程起跑前部署又变
        ns = build_parser().parse_args(list(args))
        out2 = io.StringIO()
        try:
            res2 = run_loop(ns.task_id, Path(ns.workspace_root), actor=ns.actor, policy_id=ns.envelope, out=out2,
                            watch=f152._guarded(f90._agent_watch(rig, [])), interval=ns.interval, max_wait=ns.max_wait,
                            max_steps=ns.max_steps, deploy_guard=f152._guard(deploy, name), continues_run=ns.continues_run,
                            wait_reexec=WaitReexec(launcher=launcher))
        finally:
            texts.append(out2.getvalue())
        raise _Replaced(res2)

    with pytest.raises(_Replaced) as replaced:
        run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=io.StringIO(), watch=_executor_then_deploy(rig, deploy, "r-b"),
                 interval=0.05, max_wait=5, max_steps=30, deploy_guard=f152._guard(deploy, "r-a"), wait_reexec=WaitReexec(launcher=launcher))
    last = replaced.value.result
    _show(f"[件④·连环] 换快照 {len(hops)} 次: {hops}\n[件④·连环] 末个进程结束原文: exit {last.exit_code} {last.message}")
    assert len(hops) == cap
    assert last.reason == "deploy_changed" and last.exit_code == exit_code_for(load_loop_contract(), "deploy_changed")
    assert (f"步间换快照续跑未成(AIPOS-F241): 已连续 {cap} 次步间换快照且其间未落任何一步(声明 step_reexec.max_consecutive={cap})"
            in last.message)
    runs = sorted(_records(rig.gov), key=lambda m: (str(m.get("started_at")), str(m.get("run_id"))))
    assert [r["end_reason"] for r in runs] == ["deploy_reexec"] * cap + ["deploy_changed"]
    assert lrr.step_reexec_streak(rig.gov, TASK, runs[-1], contract=load_loop_contract()) == cap


def test_item4_declaration_fail_closed():
    contract = load_loop_contract()
    assert step_reexec_declaration(contract)["max_consecutive"] >= 1
    broken = json.loads(json.dumps(contract))
    broken["deploy_consistency"]["step_reexec"]["max_consecutive"] = 0
    with pytest.raises(SchemaLoadError, match="max_consecutive"):
        step_reexec_declaration(broken)
    broken = json.loads(json.dumps(contract))
    del broken["deploy_consistency"]["step_reexec"]
    with pytest.raises(SchemaLoadError, match="step_reexec"):
        step_reexec_declaration(broken)
