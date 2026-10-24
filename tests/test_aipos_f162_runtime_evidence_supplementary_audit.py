"""AIPOS-F162(gap #146 + chris OTA HBJOTA-57R/58R 实撞) — 审计独立运行证据硬门 + 已结案补充审计入口 + 审计章程「独立核验=审计树重跑」。

依据: chris HBJOTA-58R 审计只在审计树跑 preflight、完整诊断链仅核执行体原件 hash/退出码即判 PASS_WITH_NOTES 并合入;
OTA 顾问欲追加重审, `audit dispatch` 以「已有通过裁决」拒, 争议无产品出口。

各件并入点(唯一实现, 禁第二份):
  件① 卡面声明 = card.schema audit_runtime_evidence(唯一字段); 判据 = board_adapter.runtime_evidence_check(AIPOS-F163 下沉门侧
      裁决写入唯一口; 裁决入口 _validate_task_artifact 预检 + 推导核审计卡裁决步同读); 声明 = transitions artifact_ingest.verdict.runtime_evidence;
      loop = 既有审计等待/拉起(evidence_rework 让审计体补做, 上限读声明, 超限结束原因 runtime_evidence_exhausted)。
  件② 派审 = 同一 audit dispatch(board_adapter._build_audit_dispatch_preview supplementary 分支); 拉起 = 原卡 loop 推导核
      await_artifact(补审卡); 裁决 = 同一 artifact ingest → audit-verdict; FAIL = 同一 derive_repair_card_on_fail;
      链路 = audit_derivation.supplementary_audit_chain(next/brief/loop status/needs-owner 同读)。
  件③ 审计章程母本 agents/roles/auditor/AGENTS.md(lybra / chris 两种项目上下文同源渲染)。

靶场: F90 进程内真门 + 临时治理根 / 临时产品仓; 人肉期样本(chris 形 HBJOTA-58R 报告: 只跑 preflight + 核执行体原件 hash)自造,
不读真实治理根、不连真实门、不碰真实工位。
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import validate_task_artifact  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

TASK = f90.TASK
AUDIT = f90.AUDIT
SUP1 = f"{TASK}-S1R"
SUP2 = f"{TASK}-S2R"
CMD_TESTS = "python3 -m pytest -q tests"
CMD_DIAG = "python3 tools/run.py --worktree {worktree} --commit {commit_sha} --tree {tree_hash}"
EVIDENCE = {"required": True, "commands": [CMD_TESTS, CMD_DIAG]}


def _show(text: str) -> None:
    print(text, flush=True)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rendered(rig, audit_id: str, reviewed: str = TASK) -> dict[str, str]:
    _code, wt = nr.card_worktree_location(rig.gov, audit_id)
    tip = _git(rig.code_repo, "rev-parse", f"card/{reviewed}")
    tree = _git(rig.code_repo, "rev-parse", f"{tip}^{{tree}}")
    return {"worktree": str(wt), "commit_sha": tip, "tree_hash": tree}


def _entry(rig, audit_id: str, command: str, *, worktree: str | None = None, excerpt: bool = False) -> dict:
    vals = _rendered(rig, audit_id)
    rendered = command.format(**vals)
    entry = {"command": rendered, "worktree": worktree or vals["worktree"], "exit_code": 0}
    if excerpt:
        entry["log_excerpt"] = "RESULT run=43 failures=0"
    else:
        log = rig.gov.parent / "audit-logs" / f"{audit_id}-{hashlib.sha1(rendered.encode()).hexdigest()[:8]}.log"
        _write(log, f"$ {rendered}\nRESULT ok\n")
        entry["log_path"], entry["log_sha256"] = str(log), _sha(log)
    return entry


def _report(rig, audit_id: str, verdict: str, *, evidence: list | None = None, body_extra: str = "", reviewed: str = TASK) -> Path:
    tip = _git(rig.code_repo, "rev-parse", f"card/{reviewed}")
    section = ""
    if evidence is not None:
        section = "\n## 独立运行证据\n\n```json\n" + json.dumps(evidence, ensure_ascii=False, indent=1) + "\n```\n"
    report = rig.gov / "task_cards" / audit_id / "audit_report.md"
    _write(report, _fm({"task_id": audit_id, "reviewed_task_id": reviewed, "verdict": verdict, "commit_sha": tip, "model": f90.SELF_AUDIT_MODEL},
                       f"# 审计报告 {audit_id}\n\n## 一句话结论\n{'逐条复核通过' if verdict.startswith('PASS') else '复跑失败, 打回'}。\n{body_extra}{section}"))
    f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL, name=f"sess-{audit_id}-{_sha(report)[:6]}.jsonl")
    return report


def _chris_style_58r(rig, audit_id: str) -> str:
    """人肉期样本(chris HBJOTA-58R 同形): 审计树只跑 preflight; 完整诊断链只核执行体原件 hash/退出码。"""
    vals = _rendered(rig, audit_id)
    _code, exec_wt = nr.card_worktree_location(rig.gov, TASK)
    return ("\n## 逐项验收\n\n### A1 入口 — PASS\n\n```\npwd: " + vals["worktree"] + "\n命令: python3 tools/run.py --worktree " + vals["worktree"]
            + " --commit " + vals["commit_sha"] + " --preflight-only\n退出: 0\n```\n\n### A2 产物可用 — PASS\n\n执行体工作树 " + str(exec_wt)
            + " 下 summary.json 四阶段 exit=0, 119 项原件 hash 全匹配(核执行体原件)。\n")


def _watch(rig, script: dict[str, list], seen: list[str], checks: dict[str, list] | None = None):
    """watch 注入: 等审计卡 = 依次落 script[审计卡] 里的报告(callable(rig) 写报告), 每落一份即经裁决入口校验并记原文; 等执行体 = f90 执行体。"""

    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        card = next((c for c in script if f"/{c}/" in patterns or patterns.startswith(f"task_cards/{c}")), None)
        if card is None:
            seen.append("executor")
            f90._executor_work(rig)
        else:
            queue = script[card]
            writer = queue.pop(0) if queue else None
            seen.append(f"auditor:{card}")
            if writer is not None:
                writer(rig)
                if checks is not None:
                    checks.setdefault(card, []).append(validate_task_artifact(card, rig.gov))
        return 0 if expect_ready([]) else 3

    return watch


def _loop(rig, task_id: str, watch, **kw):
    out = io.StringIO()
    res = run_loop(task_id, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=watch, interval=0.05,
                   max_wait=kw.pop("max_wait", 5), max_steps=kw.pop("max_steps", 40))
    return res, out.getvalue()


# ===========================================================================
# 件① 运行证据硬门
# ===========================================================================

def test_item1_declared_card_rejections_original_and_loop_relaunch_cap(rig):
    """声明 runtime_evidence 的卡: 缺证据节(chris 58R 形) / 工作树为执行体树 / 命令缺一条 依次被裁决入口拒收(原文);
    loop 让审计体补做(不升 Owner)直到声明上限, 超限 = runtime_evidence_exhausted(loop status 判 owner_needed)。"""
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(rig.gov)
    _code, exec_wt = nr.card_worktree_location(rig.gov, TASK)
    script = {AUDIT: [
        lambda r: _report(r, AUDIT, "PASS_WITH_NOTES", body_extra=_chris_style_58r(r, AUDIT)),
        lambda r: _report(r, AUDIT, "PASS", evidence=[_entry(r, AUDIT, CMD_TESTS, worktree=str(exec_wt), excerpt=True),
                                                     _entry(r, AUDIT, CMD_DIAG, worktree=str(exec_wt), excerpt=True)]),
        lambda r: _report(r, AUDIT, "PASS", evidence=[_entry(r, AUDIT, CMD_TESTS)]),
    ]}
    seen: list[str] = []
    checks: dict[str, list] = {}
    res, text = _loop(rig, TASK, _watch(rig, script, seen, checks))
    labels = ["缺证据节(chris 58R 形: 只跑 preflight + 核执行体原件)", "工作树为执行体树", "命令缺一条"]
    for label, check in zip(labels, checks[AUDIT]):
        _show(f"[件①·拒收原文·{label}] category={check['category']}\n  " + "\n  ".join(check["reasons"]))
    _show(f"[件①·loop 对拒收的处理] seen={seen}\n{text}")
    cats = [c["category"] for c in checks[AUDIT]]
    assert cats == ["RUNTIME_EVIDENCE_MISSING"] * 3, cats
    assert "缺「独立运行证据」节" in checks[AUDIT][0]["reasons"][0]
    assert any("是被审执行卡" in r and "不是审计分配工作树" in r for r in checks[AUDIT][1]["reasons"])
    assert any("卡面声明的命令缺有效运行记录" in r and "tools/run.py" in r for r in checks[AUDIT][2]["reasons"])
    assert not any("python3 -m pytest -q tests'" in r and "缺有效运行记录" in r for r in checks[AUDIT][2]["reasons"])
    # loop: 不升 Owner——三次都让审计体补做(等待提示附拒因), 第三份仍不合格 = 超声明上限(relaunch_cap=2)结束
    assert seen == ["executor", f"auditor:{AUDIT}", f"auditor:{AUDIT}", f"auditor:{AUDIT}"], seen
    assert "返工: 审计报告被拒收(RUNTIME_EVIDENCE_MISSING)" in text, text
    assert res.exit_code == 4 and res.reason == "runtime_evidence_exhausted", (res.reason, text)
    # 拒收期间零裁决记录、审计卡仍 claimed(审计产物不合格, 不入门)
    assert not f90._records(rig.gov, "audit_verdicts", TASK)
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    _show(f"[件①·loop status 原文]\n{out}{err}")
    # AIPOS-F225: 审计补做连败 = 第三档(审计执行侧问题归顾问 investigate), 原判事由留在 detail
    assert "investigate(runtime_evidence_exhausted) — 第三档(归顾问, AIPOS-F225)" in out + err and "审计补做连败" in out + err


def test_item1_full_evidence_passes_and_loop_completes_after_one_rework(rig):
    """首份 PASS 缺证据被拒 → loop 让审计体补做 → 证据齐全(两条命令、审计分配工作树、日志 sha256)入门, 全链结案。"""
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(rig.gov)
    script = {AUDIT: [
        lambda r: _report(r, AUDIT, "PASS"),
        lambda r: _report(r, AUDIT, "PASS", evidence=[_entry(r, AUDIT, CMD_TESTS), _entry(r, AUDIT, CMD_DIAG)]),
    ]}
    seen: list[str] = []
    checks: dict[str, list] = {}
    res, text = _loop(rig, TASK, _watch(rig, script, seen, checks))
    _show(f"[件①·证据齐全通过原文] category={checks[AUDIT][1]['category']} ok={checks[AUDIT][1]['ok']}\n{text}")
    assert [c["category"] for c in checks[AUDIT]] == ["RUNTIME_EVIDENCE_MISSING", "OK"]
    assert res.exit_code == 0 and res.outcome == "completed", text
    verdict = nr._read_frontmatter(f90._records(rig.gov, "audit_verdicts", TASK)[0])
    assert verdict["verdict"] == "PASS" and verdict["audit_task_id"] == AUDIT


def test_item1_fail_unrestricted_undeclared_unchanged_and_declaration_invalid(rig):
    """FAIL 不受限; 未声明卡 PASS 无证据节照旧入门(行为不变); 声明形不对 = fail-closed 拒(不当未声明放行)。"""
    outcomes: dict[str, dict] = {}

    def run(task_id: str, extra: dict, verdict: str) -> None:
        f90._card(rig.gov, task_id, "pending", **extra)
        audit_id = f"{task_id}R"

        def watch(args, expect_ready, deploy_guard=None):
            if f"/{audit_id}/" in " ".join(args.expect):
                _report(rig, audit_id, verdict, reviewed=task_id)
                outcomes[task_id] = validate_task_artifact(audit_id, rig.gov)
                return 2  # 只要校验原文, 停在此处
            f90._executor_work(rig, task_id)
            return 0 if expect_ready([]) else 3

        _loop(rig, task_id, watch, max_wait=2)

    run("PROBE-F162-FAIL", {"audit_runtime_evidence": EVIDENCE}, "FAIL")
    run("PROBE-F162-UNDECL", {}, "PASS")
    run("PROBE-F162-BAD", {"audit_runtime_evidence": {"required": "yes", "commands": [CMD_TESTS]}}, "PASS")
    for key, label in (("PROBE-F162-FAIL", "FAIL 不受限"), ("PROBE-F162-UNDECL", "未声明卡行为不变"), ("PROBE-F162-BAD", "声明形不对")):
        o = outcomes[key]
        _show(f"[件①·{label}原文] ok={o['ok']} category={o['category']} reasons={o['reasons']}")
    assert outcomes["PROBE-F162-FAIL"]["ok"] and outcomes["PROBE-F162-FAIL"]["category"] == "OK"
    assert outcomes["PROBE-F162-UNDECL"]["ok"] and outcomes["PROBE-F162-UNDECL"]["category"] == "OK"
    assert outcomes["PROBE-F162-BAD"]["category"] == "RUNTIME_EVIDENCE_DECLARATION_INVALID"
    assert "required 须为布尔" in outcomes["PROBE-F162-BAD"]["reasons"][0]


def test_item1_derived_audit_card_renders_requirement_and_draft_passes_field(rig):
    """派生审计卡正文渲染「独立运行证据要求」(命令清单 + 格式); 卡面字段已在 card.schema 声明(出卡 draft 透传不报未知字段)。"""
    from tools.schema_loader import get_card_field_schema, validate_field_value

    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence=EVIDENCE)
    init_governance_repo(rig.gov)
    script = {AUDIT: [lambda r: _report(r, AUDIT, "PASS", evidence=[_entry(r, AUDIT, CMD_TESTS), _entry(r, AUDIT, CMD_DIAG)])]}
    _loop(rig, TASK, _watch(rig, script, []))
    audit_card, _q = nr._find_task_in_queue(rig.gov, AUDIT)
    text = audit_card.read_text(encoding="utf-8")
    _show("[件①·派生审计卡要求段]\n" + text[text.index("## 独立运行证据要求"):][:900])
    assert "## 独立运行证据要求" in text and f"`{CMD_DIAG}`" in text and "## 独立运行证据" in text
    assert get_card_field_schema("audit_runtime_evidence")["type"] == "object"
    assert validate_field_value("audit_runtime_evidence", EVIDENCE) == (True, None)


def test_item1_draft_create_publish_pass_field_through_and_bad_shape_blocked(rig, tmp_path):
    """出卡: draft create --from-json 原样透传 audit_runtime_evidence 到发布后的卡面; 声明形不对 = 发布拒(同一解析)。"""
    def draft(task_id: str, evidence) -> Path:
        payload = {"frontmatter": {"task_id": task_id, "title": f"{task_id} t", "project": "lybra", "context_bundle": "t",
                                   "task_mode": "code", "priority": "high", "status": "pending", "needs_owner": False,
                                   "assigned_to": f90.EXEC, "agent_instance": f90.EXEC, "audit": "required", "audit_by": AUDITOR,
                                   "harness": "claude-code", "output_target": "tests/", "artifact_policy": "formal_write",
                                   "audit_runtime_evidence": evidence},
                   "body": f"# {task_id}\n\n## Goal\n做 {task_id}。\n\n## 验收\n- 夹具绿\n"}
        path = tmp_path / f"{task_id.lower()}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    rc, out, err = _cli(["--workspace-root", str(rig.gov), "draft", "create", "--from-json", str(draft("PROBE-F162-D1", EVIDENCE)), "--actor", DRIVER])
    assert rc == 0, out + err
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "draft", "publish", "--path", "5_tasks/drafts/probe-f162-d1.md", "--actor", DRIVER])
    assert rc == 0, out + err
    card, queue = nr._find_task_in_queue(rig.gov, "PROBE-F162-D1")
    fm = nr._read_frontmatter(card)
    _show(f"[件①·出卡透传] queue={queue} audit_runtime_evidence={fm.get('audit_runtime_evidence')}")
    assert queue == "pending" and fm["audit_runtime_evidence"] == EVIDENCE
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "draft", "create", "--from-json",
                         str(draft("PROBE-F162-D2", {"required": True, "commands": "pytest"})), "--actor", DRIVER])
    rc2, out2, err2 = _cli(["--workspace-root", str(rig.gov), "draft", "publish", "--path", "5_tasks/drafts/probe-f162-d2.md", "--actor", DRIVER])
    text = out + err + out2 + err2
    _show(f"[件①·出卡声明形不对] create rc={rc} publish rc={rc2}: "
          + next((l.strip() for l in text.splitlines() if "RUNTIME_EVIDENCE_DECLARATION_INVALID" in l), "(无)"))
    assert "RUNTIME_EVIDENCE_DECLARATION_INVALID" in text and nr._find_task_in_queue(rig.gov, "PROBE-F162-D2")[1] != "pending"


# ===========================================================================
# 件② 已结案补充审计
# ===========================================================================

def _close_original(rig) -> None:
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = _loop(rig, TASK, f90._agent_watch(rig, []))
    assert res.exit_code == 0 and res.outcome == "completed", text


def _frozen_truth(rig) -> dict[str, str]:
    """原 closure / 原裁决记录 / 产品仓 main 的指纹(补审前后须一致)。"""
    out = {str(p.relative_to(rig.gov)): _sha(p) for kind in ("closures", "audit_verdicts")
           for p in f90._records(rig.gov, kind, TASK)}
    out["product main"] = _git(rig.code_repo, "rev-parse", "main")
    return out


def _dispatch(rig, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "audit", "dispatch", "--supplementary", "--task-id", TASK,
                         "--actor", DRIVER, "--reason", "58R 只在审计树跑 preflight, 完整诊断链未独立重跑", *extra])
    return rc, out + err


def test_item2_supplementary_preview_confirm_pass_untouched_and_second_in_flight_refused(rig):
    _close_original(rig)
    before = _frozen_truth(rig)
    queue_before = sorted(str(p.relative_to(rig.gov)) for p in (rig.gov / "5_tasks").rglob("*") if p.is_file())
    # 预演: 零写入
    rc, text = _dispatch(rig, "--runtime-evidence-command", CMD_TESTS)
    preview = json.loads(text)
    _show(f"[件②·补审预演原文] rc={rc} verdict={preview['verdict']} audit_task_id={preview['data']['audit_task_id']} "
          f"planned_writes={preview['planned_writes']} blocking={preview['blocking_reasons']}")
    assert rc == 0 and preview["dry_run"] is True and preview["data"]["audit_task_id"] == SUP1
    assert sorted(str(p.relative_to(rig.gov)) for p in (rig.gov / "5_tasks").rglob("*") if p.is_file()) == queue_before
    # 确认: 写补审卡 + 派审记录, 原卡卡面不改
    card_path, _q = nr._find_task_in_queue(rig.gov, TASK)
    card_sha = _sha(card_path)
    rc, text = _dispatch(rig, "--runtime-evidence-command", CMD_TESTS, "--confirm")
    confirm = json.loads(text)
    _show(f"[件②·补审确认原文] rc={rc} performed_writes={confirm.get('performed_writes')}")
    assert rc == 0 and confirm["dry_run"] is False
    assert _sha(card_path) == card_sha, "派审不改原卡卡面"
    sup_path, sup_q = nr._find_task_in_queue(rig.gov, SUP1)
    sup_fm = nr._read_frontmatter(sup_path)
    assert sup_q == "pending" and sup_fm["supplementary_of"] == TASK and sup_fm["task_mode"] == "audit"
    assert sup_fm["audit_runtime_evidence"] == {"required": True, "commands": [CMD_TESTS]}
    # 同卡第二个在途补审被拒
    rc, text = _dispatch(rig, "--confirm")
    second = json.loads(text)
    _show(f"[件②·第二个在途补审被拒原文] rc={rc} blocking={second['blocking_reasons']}")
    assert rc == 1 and any(r.startswith(f"SUPPLEMENTARY_AUDIT_IN_FLIGHT: {TASK} 已有在途补充审计 {SUP1}") for r in second["blocking_reasons"])
    # 视图: next 显示在途
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "next", "--task-id", TASK])
    _show(f"[件②·next 在途原文]\n{out}{err}")
    assert f"{TASK} 补审: {SUP1}[pending] 补审在途·{SUP1}" in out + err
    # loop 推进: 认领补审卡 → 拉起审计体(缺证据被拒补做)→ 证据齐全入门 → 原卡附「补审通过」
    script = {SUP1: [lambda r: _report(r, SUP1, "PASS"),
                     lambda r: _report(r, SUP1, "PASS", evidence=[_entry(r, SUP1, CMD_TESTS)])]}
    seen: list[str] = []
    res, text = _loop(rig, TASK, _watch(rig, script, seen))
    _show(f"[件②·补审 loop 原文] seen={seen}\n{text}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert seen == [f"auditor:{SUP1}", f"auditor:{SUP1}"]
    card_path, _q = nr._find_task_in_queue(rig.gov, TASK)
    fm = nr._read_frontmatter(card_path)
    _show(f"[件②·补审 PASS 附结论原文] supplementary_audit_ref={fm.get('supplementary_audit_ref')} "
          f"supplementary_audit_status={fm.get('supplementary_audit_status')} audit_verdict(原)={fm.get('audit_verdict')}")
    assert fm["supplementary_audit_status"] == f"补审通过·{SUP1}" and fm["audit_verdict"] == "PASS"
    after = _frozen_truth(rig)
    new_records = sorted(set(after) - set(before))
    _show(f"[件②·原 closure/原裁决 sha 前后]\n  before={before}\n  after(同键)={ {k: after[k] for k in before} }\n  新增={new_records}")
    assert {k: after[k] for k in before} == before
    sup_verdict = nr._read_frontmatter(rig.gov / new_records[0])
    assert sup_verdict["supplementary_of"] == TASK and sup_verdict["audit_task_id"] == SUP1
    records = nr._read_task_records(rig.gov, TASK)
    assert records["latest_verdict"]["audit_task_id"] == AUDIT and records["latest_supplementary_verdict"]["audit_task_id"] == SUP1
    assert nr._find_task_in_queue(rig.gov, SUP1)[1] == "completed"


def test_item2_supplementary_fail_derives_repair_card_and_views_show_chain(rig):
    _close_original(rig)
    before = _frozen_truth(rig)
    rc, text = _dispatch(rig, "--confirm")
    assert rc == 0, text
    script = {SUP1: [lambda r: _report(r, SUP1, "FAIL", body_extra="\n## 发现\n- 完整诊断链在审计树复跑失败\n")]}
    res, text = _loop(rig, TASK, _watch(rig, script, []))
    _show(f"[件②·补审 FAIL loop 原文]\n{text}")
    assert res.exit_code == 0, text
    repair_path, repair_q = nr._find_task_in_queue(rig.gov, f"{TASK}-fix1")
    repair = nr._read_frontmatter(repair_path)
    _show(f"[件②·补审 FAIL 派生修复卡原文] {repair_path.relative_to(rig.gov)} queue={repair_q} supplementary_of={repair.get('supplementary_of')} "
          f"derived_from_verdict_id={repair.get('derived_from_verdict_id')} lane={repair.get('lane')} harness={repair.get('harness')}\n"
          + repair_path.read_text(encoding="utf-8").split("---", 2)[2][:400])
    assert repair_q == "pending" and repair["supplementary_of"] == TASK and "derived_from_audit_task_id" not in repair
    assert repair["harness"] == "claude-code"
    card_path, _q = nr._find_task_in_queue(rig.gov, TASK)
    fm = nr._read_frontmatter(card_path)
    _show(f"[件②·原卡补审结论] {fm.get('supplementary_audit_status')}")
    assert fm["supplementary_audit_status"] == f"补审未通过·修复卡 {TASK}-fix1"
    assert {k: _frozen_truth(rig)[k] for k in before} == before
    chain = f"{TASK} 补审: {SUP1}[completed] 补审未通过·修复卡 {TASK}-fix1(修复卡 {TASK}-fix1[pending])"
    (rig.gov / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)  # brief 读决策日志目录(靶场空目录)
    views = {
        "next": _cli(["--workspace-root", str(rig.gov), "next", "--task-id", TASK]),
        "brief": _cli(["--workspace-root", str(rig.gov), "brief"]),
        "loop status": _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov)]),
        "needs-owner": _cli(["--workspace-root", str(rig.gov), "needs-owner"]),
    }
    for name, (rc, out, err) in views.items():
        line = next((l for l in (out + err).splitlines() if "补审" in l), "")
        _show(f"[件②·视图链路·{name}] rc={rc} {line.strip()}")
        assert chain in out + err, (name, out, err)
    # 第二次补审(上一张已出裁决)可发起, 卡号递增
    rc, text = _dispatch(rig)
    assert rc == 0 and json.loads(text)["data"]["audit_task_id"] == SUP2


def test_item2_supplementary_refused_for_unconcluded_card_and_non_advisor(rig):
    f90._card(rig.gov, TASK, "claimed")
    rc, text = _dispatch(rig)
    res = json.loads(text)
    _show(f"[件②·未结案卡拒] rc={rc} blocking={res['blocking_reasons']}")
    assert rc == 1 and any(r.startswith("SUPPLEMENTARY_SOURCE_NOT_CONCLUDED") for r in res["blocking_reasons"])
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "audit", "dispatch", "--supplementary", "--task-id", TASK,
                         "--actor", AUDITOR, "--reason", "x"])
    res = json.loads(out)
    _show(f"[件②·非顾问发起拒] rc={rc} blocking={res['blocking_reasons']}")
    assert any(r.startswith("SUPPLEMENTARY_ACTOR_NOT_ADVISOR") for r in res["blocking_reasons"])
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "audit", "dispatch", "--task-id", TASK, "--actor", DRIVER])
    assert rc == 2 and "常规 audit dispatch 缺必填参数" in err


# ===========================================================================
# 件③ 审计章程母本(lybra / chris 两种项目上下文同源渲染)
# ===========================================================================

@pytest.mark.parametrize("shape,project", [("lybra", "lybra"), ("chris", "chris-huibojin")])
def test_item3_auditor_charter_renders_independent_rerun_clause(tmp_path, monkeypatch, shape, project):
    from test_aipos_f80_zero_gate_charter import FOREIGN_TO_CHRIS, _identity, _make_gov
    from tools.aipos_cli.charter_render import charter_render_context, render_charter

    gov = _make_gov(tmp_path, monkeypatch, shape=shape)
    harness = tmp_path / f"pi-{shape}" / f"{project}-auditor"
    harness.mkdir(parents=True)
    master = (REPO_ROOT / "agents" / "roles" / "auditor" / "AGENTS.md").read_text(encoding="utf-8")
    ctx = charter_render_context(gov, identity=_identity(harness, "auditor", f"audit.{project}.hostx", project), product_commit="fixture")
    text = render_charter(master, ctx)
    if shape == "chris":
        assert FOREIGN_TO_CHRIS.findall(text) == []
    start = text.index("独立核验 = 在审计自己的分配工作树重跑")
    _show(f"[件③·章程渲染·{project}]\n" + text[start - 40:start + 700])
    assert "{{" not in text
    assert "只核执行体产物 hash/退出码不构成独立核验" in text
    assert "## 独立运行证据" in text and "FAIL 或 BLOCK" in text
    assert f"`{project}` 项目" in text
