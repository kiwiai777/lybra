"""AIPOS-F218 — 验证卡(固定候选)执行 BLOCK 后的正式恢复出口(10-23 chris HBJOTA-61V2 / LANTU-VALID2 实例)。

件① 返工轮对交付主体非卡分支的卡(next_resolver.card_delivery_subject, F210 唯一判定: 固定候选 / 治理根产出清单)不再要求卡分支新提交:
    「已返工」= 晚于裁决的新交回(next_resolver.non_branch_re_return; 声明 transitions nodes.N4.non_branch_rework)→ verdict_staleness 判过期 →
    同一重交回出口 → 门侧按卡号演进派 R2; 固定候选卡报告 commit_sha 不是卡面候选 = artifact_invalid(INGEST_CANDIDATE_MISMATCH 原文);
    治理根产出卡(顾问追加范围 10-23, 依据 chris HBLX-31)不校验 commit_sha / 卡分支。卡分支卡不变(F155 测试回归)。
件② N4 非 PASS 时 next 与 loop status 同给两条路(登记返工轮重新执行 / 仅重审; next_resolver.non_pass_recovery); 执行产物本身是 BLOCK
    报告 = 推荐登记返工轮。
件③ queue return-repair: claimed + 已有交回 = 正常形态, 不判不一致, 改指 lybra next。
件④ 见 tests/test_aipos_f218_context_json_sentence.py(验证根靶场不同)。

靶场 = AIPOS-F90 进程内门 + loop 全链靶场(临时治理根 + 临时产品仓 + 临时 HOME; 禁真门 / 真治理根 / 真工位 / 起 pi), 卡面自造人肉期
61V2 形态(validation 卡固定候选、执行交回 BLOCK 报告、审计 BLOCK)。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + loop 全链靶场(禁第二份)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402  — F131 夹具读法唯一来源
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

SRC = "PROBE-F218-S"      # 来源卡(产出固定候选)
VAL = "PROBE-F218-V"      # 验证卡(61V2 形态)
POLICY_V = "pol_probe_f218_validation"
GOV = "PROBE-F218-G"      # 只写治理根的 docs 卡(HBLX-31 形态: lane.repo = governance_root, 独立审计)
POLICY_D = "pol_probe_f218_docs"
BLOCK_RETURN = ("**BLOCK — 中止**: 本卡两个验证根声明均无活动 run, 执行体无法自铸 run(章程 + 开工提示明文禁止), 无法源码送达 / 初始化 / "
                "运行完整入口, 停在初始化阶段")
BLOCK_AUDIT = ("BLOCK — 执行体 BLOCK 正当, 但完整验证链未完成: 审计体在自己的 run 内完成 init-run 与 src-verify, 卡面完整入口因缺 context.json "
               "无法运行, 全部 71 项验证均未执行")


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _quiet(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("[ENVELOPE"))


def _pick(d: dict, *keys: str) -> str:
    return json.dumps({k: d.get(k) for k in keys}, ensure_ascii=False, indent=2)


def _card_fm(gov: Path, task_id: str) -> dict:
    path, _q = nr._find_task_in_queue(gov, task_id)
    return {**nr._read_frontmatter(path), "task_id": task_id}


def _real_finalize(gov: Path, code_repo: Path, argv: list[str]) -> subprocess.CompletedProcess:
    """验证卡 N5 = 产品真实 finalize(无合入早出口, 不碰产品仓 git); 其余沿用 F90 替身。"""
    task_id = argv[argv.index("--task-id") + 1]
    if task_id not in (VAL, GOV):
        return _F90_FINALIZE(gov, code_repo, argv)
    rc, out, err = _cli(list(argv[1:]))
    return subprocess.CompletedProcess(argv, rc, out, err)


_F90_FINALIZE = f90._finalize_double


@pytest.fixture
def vrig(rig, monkeypatch):  # noqa: F811
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    monkeypatch.setattr(f90, "_finalize_double", _real_finalize)
    _write(rig.gov / "5_tasks" / "policies" / f"{POLICY_V}.md", build_autonomy_policy_markdown(
        policy_id=POLICY_V, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f218", task_selector_task_mode="validation", launch_harnesses=["pi"]))
    sha, tree = f78._branch_with_commit(rig.code_repo, SRC, rel="Scripts/joint/run.py")
    f78._card(rig.gov, SRC, "completed", extra={"harness": "claude-code"})
    f78._card(rig.gov, VAL, "pending", task_mode="validation",
              extra={"harness": "claude-code", "audit_by": AUDITOR, "validation_source_task": SRC, "validation_candidate": sha,
                     "output_target": f"task_cards/{VAL}/artifacts/"})
    init_governance_repo(rig.gov)
    rig.candidate, rig.tree = sha, tree
    return rig


def _return(rig, conclusion: str, *, commit_sha: str | None = None) -> Path:
    """执行体交回报告(固定候选三键照抄产品给值; commit_sha 可改作反例)。"""
    path = rig.gov / "task_cards" / VAL / "RETURN.md"
    _write(path, _fm({"commit_sha": commit_sha or rig.candidate, "tree_hash": rig.tree, "branch": f"card/{SRC}",
                      "model": f90.SELF_EXEC_MODEL},
                     f"# RETURN — {VAL}\n\n## 一句话结论\n{conclusion}\n\n## 做了什么\n- 读卡 / 开工稿; 验证根列表\n"))
    f90._claude_session(rig.home, path, "claude-opus-5-5", name=f"sess-{VAL}-{time.time_ns()}.jsonl")
    return path


def _audit(rig, audit_id: str, verdict: str, summary: str) -> None:
    report = rig.gov / "task_cards" / audit_id / "audit_report.md"
    _write(report, _fm({"task_id": audit_id, "reviewed_task_id": VAL, "verdict": verdict, "commit_sha": rig.candidate,
                        "model": f90.SELF_AUDIT_MODEL}, f"# 审计报告 {audit_id}\n\n## 一句话结论\n{summary}\n\n## 证据\n- 夹具\n"))
    f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL,
                    name=f"sess-{audit_id}-{verdict}.jsonl")


def _watch(rig, seen: list[str], *, executor, verdicts: dict[str, tuple[str, str]]):
    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        audit_id = next((a for a in sorted(verdicts, key=len, reverse=True) if f"task_cards/{a}/" in patterns), None)
        if audit_id:
            verdict, summary = verdicts[audit_id]
            seen.append(f"auditor:{audit_id}:{verdict}")
            _audit(rig, audit_id, verdict, summary)
        else:
            seen.append("executor")
            executor(rig)
        return 0 if expect_ready([]) else 3

    return watch


def _loop(rig, watch, **kw):
    out = io.StringIO()
    res = run_loop(VAL, rig.gov, actor=DRIVER, policy_id=POLICY_V, out=out, watch=watch, interval=0.05, max_wait=5,
                   max_steps=kw.pop("max_steps", 40), no_launch=True)
    return res, out.getvalue()


def _status(gov: Path) -> tuple[str, dict]:
    _rc, text, err = _cli(["loop", "status", "--task-id", VAL, "--workspace-root", str(gov)])
    _rcj, raw, _e = _cli(["loop", "status", "--task-id", VAL, "--workspace-root", str(gov), "--json"])
    return text + err, json.loads(raw)


def _block_then_rework(rig) -> tuple[dict, str]:
    """61V2 形态: 执行交回 BLOCK 报告 → 审计 BLOCK(loop 以 audit_blocked 结束)→ 顾问按 next 给出的 a) 命令登记返工轮。"""
    seen: list[str] = []
    res, text = _loop(rig, _watch(rig, seen, executor=lambda r: _return(r, BLOCK_RETURN),
                                  verdicts={f"{VAL}R": ("BLOCK", BLOCK_AUDIT)}))
    _show(f"---- ①② lybra loop 首跑原文(61V2 形态: 执行 BLOCK 交回 → 审计 BLOCK)exit {res.exit_code} reason {res.reason} ----\n"
          + _quiet(text))
    assert seen == ["executor", f"auditor:{VAL}R:BLOCK"] and res.reason == "audit_blocked", text
    d = nr.derive_next_step(VAL, rig.gov)
    _show("---- ③ lybra next(N4 BLOCK, 未登记返工)原文 ----\n" + _pick(d, "derivable", "current_node", "triggered_by", "suggested_action",
                                                                       "action", "recovery"))
    status_text, status = _status(rig.gov)
    _show("---- ③ lybra loop status(N4 BLOCK)原文 ----\n" + status_text)
    return d, status_text if status else status_text


# ===========================================================================
# 件①②: 执行 BLOCK → 审计 BLOCK → 两条路(推荐登记返工)→ 登记返工 → 同一候选重新执行 → 重交回派 R2 → PASS → 无合入结案
# ===========================================================================

def test_item1_item2_candidate_block_rework_re_execute_dispatches_r2(vrig):
    rig = vrig
    d, status_text = _block_then_rework(rig)
    rec = d["recovery"]
    verdict_id = d["action"]["verdict_id"]
    assert d["action"]["type"] == "audit_blocked" and d["action"]["recommended"] == "rework" and rec["executor_blocked"] is True
    assert "交回摘要以 BLOCK 开头" in rec["why"] and "会因产物不变再 BLOCK" in rec["choice"]
    assert rec["rework_command"].startswith(f"lybra --workspace-root {rig.gov} queue rework --task-id {VAL} --actor {DRIVER} "
                                            f"--verdict-ref {verdict_id} ")
    assert "a) 登记返工轮后由执行体重新执行同一卡" in d["suggested_action"] and "b) 仅重审" in d["suggested_action"]
    assert rec["reaudit_command"] in d["suggested_action"] and f"--audit-task-id {VAL}R2" in rec["reaudit_command"]
    # loop status 与 next 同源: 推荐句 / 返工命令 / 重审命令逐字相同
    assert rec["choice"] in status_text and rec["rework_command"] in status_text and rec["reaudit_command"] in status_text

    # 件③: claimed + 已有交回 = 正常形态(return-repair 不判不一致, 改指 lybra next)
    rc, out, err = _cli(["queue", "return-repair", "--task-id", VAL, "--actor", DRIVER, "--workspace-root", str(rig.gov), "--dry-run"])
    _show(f"---- ④ lybra queue return-repair(claimed + 已交回)原文 exit {rc} ----\n{out}{err}")
    report = json.loads(out)
    assert rc == 0 and report["verdict"] == "OK" and report["return_count"] >= 1
    assert "正常形态" in report["diagnosis"][-1] and "manual" not in report["recommended_action"].lower()
    assert report["recommended_action"].endswith(f"lybra --workspace-root {rig.gov} next --task-id {VAL}")

    # 顾问照抄 a) 命令登记返工(预演 → --confirm)
    argv = shlex.split(rec["rework_command"])[1:]
    rc, out, err = _cli(argv)
    _show(f"---- ① lybra queue rework(按 next 给出的 a) 命令, 缺省预演)exit {rc} ----\n{out}{err}")
    assert rc == 0
    rc, out, err = _cli(argv + ["--confirm"])
    _show(f"---- ① lybra queue rework --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0, err
    wait = nr.derive_next_step(VAL, rig.gov)
    _show("---- ① lybra next(登记返工后, 执行体尚未重新执行)原文 ----\n"
          + _pick(wait, "derivable", "current_node", "triggered_by", "missing_records", "suggested_action", "notes", "action"))
    assert wait["action"] == {"type": "await_rework", "card": VAL, "round": 1}
    said = wait["missing_records"][0] + wait["suggested_action"]
    assert "新 tip" not in said and "卡工作树提交" not in said and "不要求新提交" in said, "固定候选卡不要求卡分支新提交"
    assert f"同一固定候选 {rig.candidate}" in wait["suggested_action"] and "重写交回报告" in wait["suggested_action"]

    # loop 续跑: 执行体在同一候选上重新执行并重写交回 → 重交回(verdict_stale 同一出口)→ 门派 R2 → 审计 PASS → 无合入结案
    seen: list[str] = []
    res, text = _loop(rig, _watch(rig, seen, executor=lambda r: _return(r, "PASS — 活动 run 就绪后在同一固定候选上重新执行, 71 项全过"),
                                  verdicts={f"{VAL}R2": ("PASS", "逐条复核通过")}))
    _show(f"---- ① lybra loop 续跑原文(返工 → 重交回 → R2 → 结案)exit {res.exit_code} ----\n" + _quiet(text))
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert seen == ["executor", f"auditor:{VAL}R2:PASS"], seen
    assert f"run return @ verdict_stale/claimed ({VAL})" in text
    recs = nr._read_task_records(rig.gov, VAL)
    assert recs["audit_round_ids"][:2] == [f"{VAL}R", f"{VAL}R2"] and recs["latest_verdict"]["audit_task_id"] == f"{VAL}R2"
    returns = sorted(f90._records(rig.gov, "returns", VAL))
    subjects = [nr._read_frontmatter(p).get("artifact_subject") for p in returns if p.name.startswith("return_")]
    _show(f"[① 两份门交回记录绑定] {json.dumps(subjects, ensure_ascii=False)}")
    assert len(subjects) == 2 and all(s and s["kind"] == "candidate" and s["commit_sha"] == rig.candidate for s in subjects)
    assert subjects[0]["return_sha256"] != subjects[1]["return_sha256"]
    dispatch = f90._records(rig.gov, "audit_dispatches", f"{VAL}R2")
    _show(f"[① R2 派审记录] supersedes={nr._read_frontmatter(dispatch[0]).get('supersedes')}")
    assert nr._read_frontmatter(dispatch[0]).get("supersedes") == f"{VAL}R"
    assert subprocess.run(["git", "-C", str(rig.code_repo), "rev-parse", "--verify", "--quiet", f"card/{VAL}"],
                          capture_output=True).returncode != 0, "固定候选卡全程不建卡分支"
    assert f90._records(rig.gov, "closures", VAL)


def test_item1_rewritten_return_with_other_sha_is_artifact_invalid(vrig):
    rig = vrig
    d, _status_text = _block_then_rework(rig)
    argv = shlex.split(d["recovery"]["rework_command"])[1:]
    assert _cli(argv + ["--confirm"])[0] == 0
    untouched = nr.derive_next_step(VAL, rig.gov)
    assert untouched["action"]["type"] == "await_rework", "交回报告未重写(指纹同被裁决那份)= 仍等返工交回"
    other = _git(rig.code_repo, "rev-parse", "main")
    _return(rig, "PASS — 重新执行(但报告头抄成了别的提交)", commit_sha=other)
    bad = nr.derive_next_step(VAL, rig.gov)
    _show("---- ① lybra next(重写交回但 commit_sha 不是卡面固定候选)原文 ----\n"
          + _pick(bad, "derivable", "current_node", "triggered_by", "missing_records", "suggested_action", "notes", "action"))
    assert bad["action"]["type"] == "artifact_invalid" and bad["current_node"] == "verdict_stale"
    assert any("INGEST_CANDIDATE_MISMATCH" in m and other in m and rig.candidate in m for m in bad["missing_records"])
    assert f"commit_sha={rig.candidate}" in bad["suggested_action"] and "固定候选卡不建卡分支" in bad["suggested_action"]


def test_item1_legacy_record_without_fingerprint_compares_time(vrig, monkeypatch):
    """存量交回记录(人肉期 / 旧门, 无报告指纹): 报告修改时刻(按秒)严格晚于裁决才算重新执行; 同一秒不算(不把被裁决那份误当新交回)。"""
    rig = vrig
    d, _status_text = _block_then_rework(rig)
    assert _cli(shlex.split(d["recovery"]["rework_command"])[1:] + ["--confirm"])[0] == 0
    ret = next(p for p in f90._records(rig.gov, "returns", VAL) if p.name.startswith("return_"))
    text = ret.read_text(encoding="utf-8")
    start = text.index("artifact_subject:")
    end = text.index("\n", text.index("return_sha256", start))
    ret.write_text(text[:start] + text[end + 1:], encoding="utf-8")  # 去掉绑定 = 存量形
    assert nr.return_record_subject(rig.gov, VAL, ret.stem) is None
    verdict_path, verdict_fm = nr._regular_verdicts(rig.gov, VAL)[0]
    report = nr.find_return_artifact(rig.gov, VAL)
    moment = nr._verdict_moment(verdict_path, verdict_fm).timestamp()
    os.utime(report, (moment + 0.5, moment + 0.5))
    assert nr.non_branch_re_return(rig.gov, VAL, _card_fm(rig.gov, VAL), verdict_path, verdict_fm) is None, "同一秒 = 不算"
    os.utime(report, (moment + 61, moment + 61))
    got = nr.non_branch_re_return(rig.gov, VAL, _card_fm(rig.gov, VAL), verdict_path, verdict_fm)
    _show(f"[① 存量记录无指纹: 报告晚于裁决 61s] {got}")
    assert got and got["source"] == "file"
    assert nr.derive_next_step(VAL, rig.gov)["current_node"] == "verdict_stale"


def test_item2_block_with_complete_product_recommends_reaudit(vrig):
    """执行产物完整(交回非 BLOCK)、审计 BLOCK 在环境 = 推荐 b 仅重审(两条路仍都给)。"""
    rig = vrig
    seen: list[str] = []
    res, _text = _loop(rig, _watch(rig, seen, executor=lambda r: _return(r, "PASS — 71 项全过"),
                                   verdicts={f"{VAL}R": ("BLOCK", "BLOCK — 审计体模拟器被他卡占用, UI 套件超时")}))
    assert res.reason == "audit_blocked"
    d = nr.derive_next_step(VAL, rig.gov)
    _show("---- ③ lybra next(执行产物完整, 审计环境 BLOCK)原文 ----\n" + _pick(d, "suggested_action", "action"))
    assert d["action"]["recommended"] == "reaudit" and d["recovery"]["executor_blocked"] is False
    assert "推荐 b" in d["suggested_action"] and "a) 登记返工轮" in d["suggested_action"]


# ===========================================================================
# 顾问追加范围(10-23, 依据 chris HBLX-31): 只写治理根的 docs 卡审计 FAIL → 登记返工 → 修正产出并重写交回 → 重交回派 R2
# ===========================================================================

def _gov_return(rig, version: str) -> Path:
    slot = rig.gov / "task_cards" / GOV
    _write(slot / "artifacts" / "findings.md", f"# 认证与密钥核查结论({version})\n")
    path = slot / "RETURN.md"
    _write(path, _fm({"outputs": [f"task_cards/{GOV}/artifacts/findings.md"], "model": f90.SELF_EXEC_MODEL},
                     f"# RETURN — {GOV}\n\n## 一句话结论\n核查结论 {version}。\n\n## 做了什么\n- 只读核查\n"))
    f90._claude_session(rig.home, path, "claude-opus-5-5", name=f"sess-{GOV}-{time.time_ns()}.jsonl")
    return path


def _gov_audit(rig, audit_id: str, verdict: str, summary: str) -> None:
    digest = nr.governance_outputs_return_binding(rig.gov, GOV)["outputs_digest"]
    report = rig.gov / "task_cards" / audit_id / "audit_report.md"
    _write(report, _fm({"task_id": audit_id, "reviewed_task_id": GOV, "verdict": verdict, "outputs_digest": digest,
                        "model": f90.SELF_AUDIT_MODEL}, f"# 审计报告 {audit_id}\n\n## 一句话结论\n{summary}\n\n## 证据\n- 夹具\n"))
    f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL,
                    name=f"sess-{audit_id}-{verdict}.jsonl")


def _gov_loop(rig, seen: list[str], executor, verdicts: dict[str, tuple[str, str]]):
    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        audit_id = next((a for a in sorted(verdicts, key=len, reverse=True) if f"task_cards/{a}/" in patterns), None)
        if audit_id:
            seen.append(f"auditor:{audit_id}:{verdicts[audit_id][0]}")
            _gov_audit(rig, audit_id, *verdicts[audit_id])
        else:
            seen.append("executor")
            executor(rig)
        return 0 if expect_ready([]) else 3

    out = io.StringIO()
    res = run_loop(GOV, rig.gov, actor=DRIVER, policy_id=POLICY_D, out=out, watch=watch, interval=0.05, max_wait=5, max_steps=40,
                   no_launch=True)
    return res, out.getvalue()


def test_scope_add_governance_outputs_card_fail_rework_rewrite_dispatches_r2(rig, monkeypatch):  # noqa: F811
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    monkeypatch.setattr(f90, "_finalize_double", _real_finalize)
    _write(rig.gov / "5_tasks" / "policies" / f"{POLICY_D}.md", build_autonomy_policy_markdown(
        policy_id=POLICY_D, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f218", task_selector_task_mode="docs", launch_harnesses=["pi"]))
    f78._card(rig.gov, GOV, "pending", task_mode="docs",
              extra={"harness": "claude-code", "audit": "required", "audit_by": AUDITOR, "output_target": f"task_cards/{GOV}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{GOV}/artifacts/"], "roles": ["executor"]}})
    init_governance_repo(rig.gov)
    seen: list[str] = []
    res, text = _gov_loop(rig, seen, lambda r: _gov_return(r, "v1"), {f"{GOV}R": ("FAIL", "FAIL — 遗漏两处密钥引用点")})
    _show(f"---- 追加·HBLX-31 形态 loop 首跑(治理根 docs 卡, 审计 FAIL)exit {res.exit_code} reason {res.reason} ----\n" + _quiet(text))
    assert seen == ["executor", f"auditor:{GOV}R:FAIL"] and "处理裁决 FAIL" in res.message, text
    d = nr.derive_next_step(GOV, rig.gov)
    _show("---- 追加·lybra next(FAIL, 未登记返工)原文 ----\n" + _pick(d, "suggested_action", "recovery"))
    rec = d["recovery"]
    assert rec["recommended"] == "rework" and "推荐 a" in d["suggested_action"] and "b) 仅重审" in d["suggested_action"]
    assert "non_pass_reaudit: 重审" in rec["reaudit_command"]
    assert _cli(shlex.split(rec["rework_command"])[1:] + ["--confirm"])[0] == 0
    wait = nr.derive_next_step(GOV, rig.gov)
    _show("---- 追加·lybra next(登记返工后, 未重写交回)原文 ----\n"
          + _pick(wait, "derivable", "current_node", "missing_records", "suggested_action", "notes", "action"))
    said = wait["missing_records"][0] + wait["suggested_action"] + wait["notes"]
    assert wait["action"]["type"] == "await_rework" and "新 tip" not in said and f"card/{GOV}" not in said
    assert "只写治理根的卡无卡分支, 不要求新提交" in said and "frontmatter outputs" in said
    seen2: list[str] = []
    res2, text2 = _gov_loop(rig, seen2, lambda r: _gov_return(r, "v2(补齐两处)"), {f"{GOV}R2": ("PASS", "逐条复核通过")})
    _show(f"---- 追加·loop 续跑(修正产出并重写交回 → 重交回 → R2 → 结案)exit {res2.exit_code} ----\n" + _quiet(text2))
    assert res2.exit_code == 0 and res2.outcome == "completed", text2
    assert seen2 == ["executor", f"auditor:{GOV}R2:PASS"] and f"run return @ verdict_stale/claimed ({GOV})" in text2
    recs = nr._read_task_records(rig.gov, GOV)
    assert recs["audit_round_ids"][:2] == [f"{GOV}R", f"{GOV}R2"] and recs["latest_verdict"]["audit_task_id"] == f"{GOV}R2"
    subjects = [nr._read_frontmatter(p).get("artifact_subject") for p in sorted(f90._records(rig.gov, "returns", GOV))
                if p.name.startswith("return_")]
    _show(f"[追加·两份门交回记录绑定] {json.dumps(subjects, ensure_ascii=False)}")
    assert len(subjects) == 2 and all(x["kind"] == "governance_outputs" and "commit_sha" not in x for x in subjects)
    assert subjects[0]["outputs_digest"] != subjects[1]["outputs_digest"]
    assert f90._records(rig.gov, "closures", GOV)


# ===========================================================================
# 声明单源 / 单一判定
# ===========================================================================

def test_declarations_single_source_and_single_judgement():
    decl = nr.non_branch_rework_declaration()
    assert decl["return_fingerprint_key"] == "return_sha256" and set(decl["by_subject"]) == {"candidate", "governance_outputs"}
    rec = nr.non_pass_recovery_declaration()
    assert rec["return_values"] == ["BLOCK"] and "{rework_command}" in rec["texts"]["paths"]
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert src.count("non_pass_recovery(workspace_root, task_id, fm,") == 2, "推导核 BLOCK / FAIL 两处调用同一实现"
    assert src.count("non_pass_recovery(Path(workspace_root), task_id, fm, state,") == 1, "loop status 占位同一实现"
    assert src.count("_non_branch_verdict_staleness(workspace_root, task_id, card_frontmatter, non_branch)") == 1
    assert src.count("non_branch_rework_subject(workspace_root, task_id, ") == 2, "过期判据与等待提示同一主体判定"
    routine = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    rr = routine["verbs"]["lybra_loop_status"]["next_action"]["routine_rejections"]
    assert {"recovery_choice", "rework_command"} <= set(rr["placeholders"])
    fix = rr["end_reasons"]["audit_blocked"]["fix"]
    assert any("{rework_command}" in s for s in fix) and any(s.startswith("修好后重新派审计下一轮: {reaudit_command}") for s in fix)


def test_f218_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f218_candidate_rework_recovery.py"
    assert rel in discover_test_files([rel], default_test_contract())
