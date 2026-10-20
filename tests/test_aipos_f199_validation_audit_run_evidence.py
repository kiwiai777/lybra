"""AIPOS-F199 — 验证卡审计运行证据绑定审计自己的验证根 run(gap #204)+ roles 签码凭据按治理根定位(gap #203)。

件① 被审卡交付主体为固定候选(enums.schema task_mode.candidate_subject, validation)时, 运行证据判据唯一实现 board_adapter.runtime_evidence_check
    不再推导 / 要求审计产品工作树: 每条记录 {command, run_id, run_dir, exit_code, log_path, log_sha256} 须绑定审计实例自己的验证根 run
    (声明归属 / 已 run-start / 候选 = 卡面候选 / run 内 src-verify SRC_OK 且 tree = 候选 tree / 日志文件指纹); 占位 {run_dir}/{src_dir}/
    {candidate}/{candidate_tree}; 旧占位 {worktree}/{commit_sha}/{tree_hash} 给迁移提示。code 卡行为不变。
件② 派审注入(审计卡正文「独立运行证据要求」)与审计开工稿同一渲染 artifact_ingest.render_candidate_evidence_steps:
    declare → run-start → init-run → 源码送达 → src-verify → 按卡面命令运行 → 证据填 run_id/run_dir。
件③ roles enroll-code / enroll-deliver 凭据: 显式 --workspace-root > --governance-root > F144 解析梯(当前目录所在治理根), 拒因写明文件与来源。

靶场 = AIPOS-F90/F95/F196/F197 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 「目标主机」= 本机模拟, 验证根 base 在 tmp);
件③ 另起 tmp HOME + 两个 tmp 治理根(活动项目 alpha / 所在目录 beta), 假门客户端只记录所用凭据。禁真门 / 真治理根 / 真工位 / 真 chris / 真 pi。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f196_validation_to_pi import (  # noqa: E402,F401  — F196 靶场(执行方式声明 + 假 ssh + 模拟目标主机)
    OWNER_REF, SRC, VAL, _cli, _show, _validation_card, _vr, vrig,
)
from tools.aipos_cli import next_resolver as nr  # noqa: E402

VALR = f"{VAL}R"
VAL2 = "PROBE-F199-V2"       # 另一张验证卡(他卡 run 靶)
VAL2R = f"{VAL2}R"
VALL = "PROBE-F199-V3"       # 旧占位验证卡
CODE = "PROBE-F199-K1"       # code 卡(行为不变靶)
CMDS = ["cat {src_dir}/app/feature.txt", "echo verify {candidate} {candidate_tree} {run_dir}"]


def _run(command: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-o", "pipefail", "-c", command], capture_output=True, text=True, timeout=120,
                          cwd=str(cwd) if cwd else None)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _local() -> str:
    from tools.aipos_cli.validation_root import local_host_label

    return local_host_label()


def _declare(gov: Path, base: Path, task_id: str, instance: str) -> str:
    rc, out, err = _vr(gov, "declare", "--host", _local(), "--base", base, "--task-id", task_id, "--instance", instance,
                       "--owner-ref", OWNER_REF, "--actor", DRIVER, "--confirm")
    assert rc == 0, out + err
    from tools.aipos_cli.validation_root import project_validation_roots

    return next(e["id"] for e in project_validation_roots(gov) if e["task_id"] == task_id and e["instance"] == instance)


def _run_start(gov: Path, decl: str, candidate: str) -> str:
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", candidate, "--actor", DRIVER, "--confirm")
    assert rc == 0, out + err
    from tools.aipos_cli.validation_root import active_run, project_validation_roots

    return active_run(next(e for e in project_validation_roots(gov) if e["id"] == decl))["run_id"]


def _prepare_run(gov: Path, decl: str, *, verify: bool = True) -> Path:
    """目标主机(本机模拟)上按产品铸的命令: init-run → 候选源码送达 → src-verify(末行 SRC_OK)。返回 run 目录。"""
    from tools.aipos_cli import validation_root as vr

    info = vr.init_command(gov, decl)
    steps = [("init-run", info["command"]), ("送达", info["source_delivery"]["deliver"])]
    if verify:
        steps.append(("src-verify", info["source_delivery"]["verify"]))
    for label, command in steps:
        proc = _run(command)
        assert proc.returncode == 0, f"{label}: {proc.stdout}{proc.stderr}"
    return Path(info["run_dir"])


def _exec_commands(run_dir: Path, candidate: str, tree: str) -> list[dict]:
    """在本 run 按卡面声明命令运行(占位按本 run 渲染, 唯一渲染 render_candidate_evidence_command), 日志落本 run logs/。"""
    from tools.aipos_cli.artifact_ingest import render_candidate_evidence_command

    values = {"run_dir": str(run_dir), "src_dir": str(run_dir / "src"), "candidate": candidate, "candidate_tree": tree}
    (run_dir / "logs").mkdir(exist_ok=True)
    entries = []
    for i, declared in enumerate(CMDS, 1):
        command = render_candidate_evidence_command(declared, values)
        proc = _run(command, cwd=run_dir / "src")
        log = run_dir / "logs" / f"cmd{i}.log"
        log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        entries.append({"command": command, "run_id": run_dir.name, "run_dir": str(run_dir), "exit_code": proc.returncode,
                        "log_path": str(log), "log_sha256": _sha(log)})
    return entries


def _report(gov: Path, audit_id: str, reviewed: str, candidate: str, entries: list) -> Path:
    section = "\n## 独立运行证据\n\n```json\n" + json.dumps(entries, ensure_ascii=False, indent=1) + "\n```\n"
    path = nr.verdict_artifact_dir(gov, audit_id) / "RETURN.md"
    f78._write(path, f78._fm({"task_id": audit_id, "reviewed_task_id": reviewed, "verdict": "PASS", "commit_sha": candidate, "model": "fixture"},
                             f"# 审计报告 {audit_id}\n\n## 一句话结论\n验证复核通过\n{section}"))
    return path


def _check(gov: Path, audit_id: str, reviewed: str, candidate: str, entries: list, *, reviewed_fm: dict | None = None):
    from tools.aipos_cli.board_adapter import runtime_evidence_check

    report = _report(gov, audit_id, reviewed, candidate, entries)
    fm = nr._read_frontmatter(report)
    return runtime_evidence_check(gov, audit_id, {"task_id": audit_id, "task_mode": "audit", "reviewed_task_id": reviewed,
                                                  "claimed_by": AUDITOR, "assigned_to": AUDITOR},
                                  report_path=report, report_fm=fm, reviewed_fm=reviewed_fm)


def _evidence_decl() -> dict:
    return {"required": True, "commands": list(CMDS)}


def _setup_cards(vrig) -> tuple[str, str]:
    gov = vrig.gov
    _validation_card(vrig, audit_runtime_evidence=_evidence_decl())
    f78._card(gov, VAL2, "pending", assigned=EXEC, task_mode="validation",
              extra={"harness": "pi", "audit_by": AUDITOR, "validation_source_task": SRC, "validation_candidate": vrig.candidate,
                     "output_target": f"task_cards/{VAL2}/artifacts/", "audit_runtime_evidence": _evidence_decl()})
    init_governance_repo(gov)
    tree = f78._git(vrig.code_repo, "rev-parse", f"{vrig.candidate}^{{tree}}")
    return vrig.candidate, tree


# ===========================================================================
# 件①: 审计证据绑定审计自己的验证根 run —— 通过 / 执行 run / 他卡 run / 未开启 run / 候选不符 / 无 SRC_OK / 日志缺失 各拒
# ===========================================================================

def test_item1_validation_evidence_binds_audit_own_run(vrig):
    gov, base = vrig.gov, vrig.base
    candidate, tree = _setup_cards(vrig)
    audit_decl = _declare(gov, base, VALR, AUDITOR)
    exec_decl = _declare(gov, base, VAL, EXEC)
    other_decl = _declare(gov, base, VAL2R, AUDITOR)
    rc, out, _err = _vr(gov, "list")
    _show(f"---- 件① 验证根声明(审计本卡 / 执行本卡 / 审计他卡)----\n{out}")

    _run_start(gov, audit_decl, candidate)
    audit_run = _prepare_run(gov, audit_decl)
    entries = _exec_commands(audit_run, candidate, tree)
    _show(f"[件① 审计 run 实跑记录] {json.dumps(entries, ensure_ascii=False)}")
    assert all(e["exit_code"] == 0 for e in entries)
    ok = _check(gov, VALR, VAL, candidate, entries)
    _show(f"[件① 审计自己的 run · 裁决入口判据] {ok}")
    assert ok is None

    # 同一份记录经 artifact ingest 入门前预检(同一判据)= OK
    f78._card(gov, VALR, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": VAL, "claimed_by": AUDITOR, "harness": "pi", "audit": "", "audit_by": ""})
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    _report(gov, VALR, VAL, candidate, entries)
    got = validate_task_artifact(VALR, gov)
    _show(f"[件① artifact ingest 预检(--kind verdict 同一判据)] ok={got['ok']} category={got['category']} reasons={got.get('reasons')}")
    assert got["ok"] and got["category"] == "OK"

    # run-long 引用(--cwd = 本 run src)同样绑定
    rc, out, err = _cli("--workspace-root", gov, "run-long", "start", "--task-id", VALR, "--name", "audit-cmd1", "--cwd", audit_run / "src",
                        "--", entries[0]["command"])
    assert rc == 0, out + err
    for _ in range(30):
        rc_w, out_w, _e = _cli("--workspace-root", gov, "run-long", "wait", "--task-id", VALR, "--name", "audit-cmd1", "--timeout", "2")
        if rc_w != 3:
            break
    from tools.aipos_cli import run_long

    rl_dir = run_long.run_dir(gov, VALR, "audit-cmd1")
    rl_entries = [{"run_long": str(rl_dir), "run_id": audit_run.name, "run_dir": str(audit_run)}, entries[1]]
    got_rl = _check(gov, VALR, VAL, candidate, rl_entries)
    _show(f"[件① run-long 引用 {{run_long, run_id, run_dir}}(cwd = 本 run src)] {got_rl}")
    assert got_rl is None

    # 拒 ①: 执行实例的 run
    _run_start(gov, exec_decl, candidate)
    exec_run = _prepare_run(gov, exec_decl)
    exec_entries = _exec_commands(exec_run, candidate, tree)
    bad = _check(gov, VALR, VAL, candidate, exec_entries)
    _show("[件① 拒·执行 run]\n  " + "\n  ".join(bad["reasons"]))
    assert bad["category"] == "RUNTIME_EVIDENCE_MISSING" and f"引用被审执行卡 {VAL} 的验证 run 目录" in bad["reasons"][0]

    # 拒 ②: 他卡 run(同一审计实例, 另一张审计卡的 run)
    _run_start(gov, other_decl, candidate)
    other_run = _prepare_run(gov, other_decl)
    other_entries = _exec_commands(other_run, candidate, tree)
    bad = _check(gov, VALR, VAL, candidate, other_entries)
    _show("[件① 拒·他卡 run]\n  " + "\n  ".join(bad["reasons"]))
    assert f"是卡 {VAL2R} 的 run, 不是本审计卡 {VALR} 的" in bad["reasons"][0]

    # 拒 ③: 未开启 run(本声明下没有 run-start 过的 run-id)
    ghost = base / VALR / "run-9"
    bad = _check(gov, VALR, VAL, candidate, [{**e, "run_id": "run-9", "run_dir": str(ghost)} for e in entries])
    _show("[件① 拒·未开启 run]\n  " + "\n  ".join(bad["reasons"]))
    assert "未开启(声明 runs 中无 run-9" in bad["reasons"][0]
    bad = _check(gov, VALR, VAL, candidate, [{**e, "run_id": "run-1", "run_dir": str(base.parent / "nowhere" / "run-1")} for e in entries])
    _show("[件① 拒·不是任何声明的 run 目录]\n  " + bad["reasons"][0])
    assert "不是任何验证根声明的 run 目录" in bad["reasons"][0]

    # 拒 ④: 候选不符(卡面候选改为另一提交, run 仍是旧候选)
    sha2, _t2 = f78._branch_with_commit(vrig.code_repo, "PROBE-F199-ALT", rel="app/other.txt")
    val_path, _q = nr._find_task_in_queue(gov, VAL)
    moved = {**nr._read_frontmatter(val_path), "validation_candidate": sha2}
    bad = _check(gov, VALR, VAL, sha2, entries, reviewed_fm=moved)
    _show("[件① 拒·候选不符]\n  " + "\n  ".join(bad["reasons"]))
    assert f"候选 {candidate} ≠ 被审卡卡面固定候选 {sha2}" in bad["reasons"][0]

    # 拒 ⑤: 无 SRC_OK —— 新 run 送达后混入改动再核对(末条 DENY); 另一 run 送达后未核对
    assert _vr(gov, "run-finish", "--id", audit_decl, "--run-id", audit_run.name, "--actor", DRIVER, "--confirm")[0] == 0
    _run_start(gov, audit_decl, candidate)
    from tools.aipos_cli import validation_root as vr

    run2 = _prepare_run(gov, audit_decl, verify=False)
    entries2 = _exec_commands(run2, candidate, tree)
    bad = _check(gov, VALR, VAL, candidate, entries2)
    _show("[件① 拒·未核对(无 src-verify 证据)]\n  " + "\n  ".join(bad["reasons"]))
    assert "源码封签不过: run 目录无送达核对证据" in bad["reasons"][0]
    (run2 / "src" / "app" / "feature.txt").write_text("现场改动\n", encoding="utf-8")
    proc = _run(vr.init_command(gov, audit_decl)["source_delivery"]["verify"])
    assert proc.returncode == 3 and "DENY [SRC_TREE_MISMATCH]" in proc.stdout
    entries2 = _exec_commands(run2, candidate, tree)
    bad = _check(gov, VALR, VAL, candidate, entries2)
    _show("[件① 拒·末条非 SRC_OK(混入改动)]\n  " + "\n  ".join(bad["reasons"]))
    assert "末条不是 SRC_OK(ok=False" in bad["reasons"][0]

    # 已正常收口的 run 仍可作证据(run-1 已 run-finish)
    assert _check(gov, VALR, VAL, candidate, entries) is None

    # 拒 ⑥: 日志缺失 / 只给摘要 / 日志被改
    no_log = [{**entries[0], "log_path": str(audit_run / "logs" / "missing.log")}, entries[1]]
    bad = _check(gov, VALR, VAL, candidate, no_log)
    _show("[件① 拒·日志文件缺失]\n  " + bad["reasons"][0])
    assert "missing.log 不可读" in bad["reasons"][0]
    excerpt = [{k: v for k, v in entries[0].items() if k not in ("log_path", "log_sha256")} | {"log_excerpt": "ok"}, entries[1]]
    bad = _check(gov, VALR, VAL, candidate, excerpt)
    _show("[件① 拒·只给日志摘要]\n  " + bad["reasons"][0])
    assert "缺字段 log_path, log_sha256" in bad["reasons"][0]
    (audit_run / "logs" / "cmd2.log").write_text("改过\n", encoding="utf-8")
    bad = _check(gov, VALR, VAL, candidate, entries)
    _show("[件① 拒·日志被改]\n  " + bad["reasons"][0])
    assert "cmd2.log sha256=" in bad["reasons"][0]

    # 拒 ⑦: 命令与声明不逐字一致(多加参数)
    bumped = [entries[0], {**entries[1], "command": entries[1]["command"] + " --preflight-only"}]
    bumped[1]["log_sha256"] = _sha(Path(bumped[1]["log_path"]))
    bad = _check(gov, VALR, VAL, candidate, bumped)
    _show("[件① 拒·命令不逐字一致]\n  " + "\n  ".join(bad["reasons"][:-1]))
    assert any("卡面声明的命令缺有效运行记录: 'echo verify" in r for r in bad["reasons"])


def test_item1_legacy_placeholders_migration_hint_and_draft_refusal(vrig):
    from tools.aipos_cli.board_adapter import runtime_evidence_check
    from tools.aipos_cli.draft_writer import create_draft
    from tools.aipos_cli.next_resolver import card_branch_name  # noqa: F401  — 声明读取已加载

    gov = vrig.gov
    legacy = {"required": True, "commands": ["python3 {worktree}/Scripts/run.py --worktree {worktree} --commit {commit_sha} --tree {tree_hash}"]}
    f78._card(gov, VALL, "pending", assigned=EXEC, task_mode="validation",
              extra={"harness": "pi", "audit_by": AUDITOR, "validation_source_task": SRC, "validation_candidate": vrig.candidate,
                     "output_target": f"task_cards/{VALL}/artifacts/", "audit_runtime_evidence": legacy})
    init_governance_repo(gov)
    report = _report(gov, f"{VALL}R", VALL, vrig.candidate, [])
    got = runtime_evidence_check(gov, f"{VALL}R", {"task_id": f"{VALL}R", "task_mode": "audit", "reviewed_task_id": VALL},
                                 report_path=report, report_fm=nr._read_frontmatter(report))
    _show("[件① 旧占位·裁决入口迁移提示]\n  " + "\n  ".join(got["reasons"]))
    assert got["category"] == "RUNTIME_EVIDENCE_DECLARATION_INVALID"
    assert "旧占位 {worktree} / {commit_sha} / {tree_hash}" in got["reasons"][0] and "{worktree} → {src_dir}" in got["reasons"][0]
    meta = {"task_id": "PROBE-F199-V4", "title": "旧占位验证卡", "project": "lybra", "context_bundle": "t", "task_mode": "validation",
            "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False, "output_target": "task_cards/PROBE-F199-V4/artifacts/",
            "artifact_policy": "formal_write", "validation_source_task": SRC, "validation_candidate": vrig.candidate,
            "audit_runtime_evidence": legacy}
    res = create_draft(gov, meta, "## Goal\n\n固定候选验证\n", dry_run=True)
    hits = [r for r in res["blocking_reasons"] if "旧占位" in r]
    _show(f"[件① 旧占位·起草拒] verdict={res['verdict']}\n  {hits[0] if hits else res['blocking_reasons']}")
    assert res["verdict"] == "BLOCK" and hits and hits[0].startswith("RUNTIME_EVIDENCE_DECLARATION_INVALID")
    fixed = {**meta, "audit_runtime_evidence": {"required": True, "commands": [
        "python3 {src_dir}/Scripts/run.py --worktree {src_dir} --commit {candidate} --tree {candidate_tree}"]}}
    res = create_draft(gov, fixed, "## Goal\n\n固定候选验证\n", dry_run=True)
    _show(f"[件① 新占位·起草] verdict={res['verdict']} blocking={res['blocking_reasons']}")
    assert res["verdict"] != "BLOCK", res["blocking_reasons"]


def test_item1_code_card_runtime_evidence_unchanged(vrig):
    from tools.aipos_cli.board_adapter import runtime_evidence_check

    gov = vrig.gov
    sha, _tree = f78._branch_with_commit(vrig.code_repo, CODE)
    f78._card(gov, CODE, "pending", extra={"harness": "claude-code", "audit_runtime_evidence": {"required": True, "commands": ["make test"]}})
    init_governance_repo(gov)
    _code, audit_wt = nr.card_worktree_location(gov, f"{CODE}R")
    audit_fm = {"task_id": f"{CODE}R", "task_mode": "audit", "reviewed_task_id": CODE}
    cases = {
        "审计分配工作树(通过)": [{"command": "make test", "worktree": str(audit_wt), "exit_code": 0, "log_excerpt": "ok"}],
        "run_id/run_dir 形(code 卡不认)": [{"command": "make test", "run_id": "run-1", "run_dir": "/tmp/x/run-1", "exit_code": 0,
                                        "log_excerpt": "ok"}],
        "别的工作树": [{"command": "make test", "worktree": "/tmp/elsewhere", "exit_code": 0, "log_excerpt": "ok"}],
    }
    results = {}
    for name, entries in cases.items():
        report = _report(gov, f"{CODE}R", CODE, sha, entries)
        results[name] = runtime_evidence_check(gov, f"{CODE}R", audit_fm, report_path=report, report_fm=nr._read_frontmatter(report))
        _show(f"[件① code 卡行为不变·{name}] {results[name]['reasons'][0] if results[name] else None}")
    assert results["审计分配工作树(通过)"] is None
    assert "缺字段 worktree" in results["run_id/run_dir 形(code 卡不认)"]["reasons"][0]
    assert f"≠ 审计分配工作树 {audit_wt}" in results["别的工作树"]["reasons"][0]


# ===========================================================================
# 件②: 派审注入与审计开工稿(同一渲染): declare → run-start → init-run → 送达 → src-verify → 运行 → 证据
# ===========================================================================

def test_item2_audit_steps_rendered_in_card_and_kickoff(vrig):
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section
    from tools.aipos_cli.subagent_kickoff import render_kickoff_brief

    gov, base = vrig.gov, vrig.base
    candidate, tree = _setup_cards(vrig)
    val_path, _q = nr._find_task_in_queue(gov, VAL)
    val_fm = nr._read_frontmatter(val_path)
    before = build_forensic_anchor_section(VAL, gov, val_fm, audit_task_id=VALR)
    section = before[before.index("## 独立运行证据要求"):]
    _show(f"---- 件② 派审注入(审计卡正文; 审计实例尚未声明验证根)----\n{section}")
    assert "验证卡运行证据步骤(AIPOS-F199" in section and "审计分配工作树" not in section
    assert f"project validation-root declare --host <目标主机> --base <base> --task-id {VALR} --instance {AUDITOR}" in section
    assert "   - `cat {src_dir}/app/feature.txt`" in section and '"run_id": "<run-id>"' in section

    decl = _declare(gov, base, VALR, AUDITOR)
    _run_start(gov, decl, candidate)
    entry = next(e for e in vr.project_validation_roots(gov) if e["id"] == decl)
    run = vr.active_run(entry)
    run_dir = vr.run_directory(entry, run["run_id"])
    delivery = vr.source_delivery(gov, entry, run)
    f78._card(gov, VALR, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": VAL, "claimed_by": AUDITOR, "agent_instance": AUDITOR, "harness": "pi", "audit": "", "audit_by": "",
                     "claim_id": f"claim_{VALR}_x", "claimed_at": "2026-10-20T00:00:00Z"})
    f78._claim_record(gov, VALR, AUDITOR)
    after = build_forensic_anchor_section(VAL, gov, val_fm, audit_task_id=VALR)
    brief = render_kickoff_brief(gov, VALR)
    text = brief["text"]
    steps = text[text.index("### 验证卡运行证据步骤"):]
    steps = steps[:steps.index("## 禁写与禁止动作")]
    _show(f"---- 件② 审计开工稿「验证卡运行证据步骤」(已声明 + run-start 后)----\n{steps}")
    for needle in (f"{decl}(主机 {_local()}", f"活动 run {run['run_id']}", vr.init_run_command(gov, entry, run), delivery["deliver"],
                   delivery["verify"], f"`cat {run_dir}/src/app/feature.txt`", f"`echo verify {candidate} {tree} {run_dir}`",
                   f'"run_id": "{run["run_id"]}", "run_dir": "{run_dir}"'):
        assert needle in steps, needle
        assert needle in after, needle  # 派审注入同一渲染
    assert "审计分配工作树" not in steps


# ===========================================================================
# 件③: roles enroll-code / enroll-deliver 凭据按治理根(F144 解析梯), 拒因含来源
# ===========================================================================

class _RecGate:
    """假门客户端: 只记录所用凭据(进程内, 不上屏), 门调用一律失败(预演止于凭据选择)。"""

    tokens: list[str] = []

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        _RecGate.tokens.append(token)

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        from tools.aipos_cli.confirm_client import GateError

        raise GateError("fixture gate: 只记录凭据, 不发码")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    from tools.aipos_cli import confirm_client
    from tools.aipos_cli.workspace_config import scaffold_project

    userhome = tmp_path / "userhome"
    (userhome / ".lybra").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(userhome))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_WORKSPACE_ROOT", "AIPOS_WORKSPACE_ROOT", "LYBRA_CONNECTION_JSON", "LYBRA_ACTIVE_PROJECT",
                "LYBRA_DRIVER_INSTANCE"):
        monkeypatch.delenv(var, raising=False)
    home = userhome / "ai-project-os" / "2_projects"
    alpha = scaffold_project(home, "alpha", registered_by="owner.alpha")
    beta = scaffold_project(home, "beta", registered_by="owner.beta")
    for root, name in ((alpha, "alpha"), (beta, "beta")):
        f78._write(root / ".lybra" / "connection.json", json.dumps({
            "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "workspace_root": str(root),
            "tokens": [{"role": "advisor", "role_class": "advisor", "agent_instance": f"advisor.{name}.h1", "token": f"fixture-{name}-adv",
                        "token_ref": f"tr-{name}", "scopes": []}]}))
    (userhome / ".lybra" / "config.json").write_text(json.dumps({"config_version": 2, "home_root": str(home), "active_project": "alpha"}),
                                                     encoding="utf-8")
    monkeypatch.setattr(confirm_client, "GateClient", _RecGate)
    _RecGate.tokens = []
    return {"alpha": alpha.resolve(), "beta": beta.resolve(), "home": home, "userhome": userhome}


def test_item3_roles_issuer_credential_follows_governance_root(roots, monkeypatch):
    from tools.aipos_cli.workspace_config import resolve_workspace_root

    alpha, beta = roots["alpha"], roots["beta"]
    monkeypatch.chdir(beta / "5_tasks")
    old = resolve_workspace_root()
    _show(f"[件③ 对照] cwd=beta 原 roles 取法(AIPOS-226 梯 resolve_workspace_root)= {old}(活动项目 alpha; gap #203 病理)")
    assert old == alpha

    deliver = ["roles", "enroll-deliver", "--role", "advisor", "--instance", "advisor.beta.h2", "--governance-root", str(beta),
               "--workspace", str(beta), "--owner-authorization-ref", "f199-test"]
    rc, out, err = _cli(*deliver)
    _show(f"---- 件③ beta 根下 roles enroll-deliver --governance-root beta(不带全局参数)rc={rc} ----\n{out}{err}")
    assert _RecGate.tokens == ["fixture-beta-adv"]
    assert f"签码凭据文件: {beta / '.lybra' / 'connection.json'}" in err and f"--governance-root {beta}" in err

    _RecGate.tokens = []
    rc, out, err = _cli("roles", "enroll-code", "--role", "executor", "--instance", "exec.beta.h9", "--owner-authorization-ref", "f199-test")
    _show(f"---- 件③ beta 根下 roles enroll-code(无 --governance-root, F144 当前目录治理根)rc={rc} ----\n{out}{err}")
    assert _RecGate.tokens == ["fixture-beta-adv"] and "当前目录所在治理根" in err

    _RecGate.tokens = []
    monkeypatch.chdir(roots["userhome"])
    rc, out, err = _cli(*deliver)
    _show(f"---- 件③ 治理根外 roles enroll-deliver --governance-root beta rc={rc} ----\n{err}")
    assert _RecGate.tokens == ["fixture-beta-adv"]

    _RecGate.tokens = []
    rc, out, err = _cli("roles", "--workspace-root", str(alpha), *deliver[1:])
    _show(f"---- 件③ 显式 --workspace-root alpha(原取法不变)rc={rc} ----\n{err}")
    assert _RecGate.tokens == ["fixture-alpha-adv"] and f"--workspace-root {alpha}" in err

    (beta / ".lybra" / "connection.json").unlink()
    _RecGate.tokens = []
    monkeypatch.chdir(beta / "5_tasks")
    rc, out, err = _cli(*deliver)
    _show(f"---- 件③ beta 无凭据文件 rc={rc} ----\n{err}")
    assert rc == 1 and _RecGate.tokens == []
    assert f"local connection.json not found: {beta / '.lybra' / 'connection.json'}(读取来源: 治理根 {beta}" in err
    assert f"--governance-root {beta}" in err

    rc, out, err = _cli("roles", "enroll-deliver", "--role", "advisor", "--instance", "advisor.beta.h2", "--governance-root",
                        str(roots["userhome"]), "--workspace", str(beta), "--owner-authorization-ref", "f199-test")
    _show(f"---- 件③ --governance-root 不是治理根 rc={rc} ----\n{err}")
    assert rc == 1 and "签发凭据所在治理根解析不出" in err and "PROJECT_NOT_ESTABLISHED" in err


def test_item3_single_resolution_point():
    import ast

    src = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    funcs = {n.name: ast.unparse(n) for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    body = funcs["_roles_issuer_root"]
    assert "_find_repo_root_for_args(" in body and "resolve_local_governance_root(" in body
    assert src.count("_roles_issuer_root(args)") == 1  # roles 签码两命令唯一调用点
    _show("[件③ 单源] _roles_issuer_root = --workspace-root > --governance-root(resolve_local_governance_root)> F144 _find_repo_root_for_args")


# ===========================================================================
# 单源: 判据 / 渲染只扩唯一实现
# ===========================================================================

def test_single_source_runtime_evidence_candidate():
    import ast

    ba = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    funcs = {n.name: ast.unparse(n) for n in ast.parse(ba).body if isinstance(n, ast.FunctionDef)}
    check, helper = funcs["runtime_evidence_check"], funcs["_candidate_run_problem"]
    assert "candidate_subject_values(" in check and "_candidate_run_problem(" in check and "executor_run_dir_refs(" in check
    for name in ("locate_run(", "task_ownership_problems(", "src_verify_evidence_check("):
        assert name in helper, name
    assert ba.count("_candidate_run_problem(") == 2  # 定义 + runtime_evidence_check 唯一调用
    ai = (REPO_ROOT / "tools" / "aipos_cli" / "artifact_ingest.py").read_text(encoding="utf-8")
    assert ai.count("def render_candidate_evidence_command") == 1
    for rel in ("tools/aipos_cli/audit_derivation.py", "tools/aipos_cli/subagent_kickoff.py"):
        assert "render_candidate_evidence_steps(" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel
    decl = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    cand = decl["artifact_ingest"]["verdict"]["runtime_evidence"]["candidate_subject"]
    _show(f"[单源] candidate_subject.entry_required_fields={cand['entry_required_fields']} placeholders={list(cand['placeholders'])}")
    assert list(cand["placeholders"]) == ["run_dir", "src_dir", "candidate", "candidate_tree"]


def test_item1_cwd_outside_run_and_remote_host_fail_closed(vrig):
    """运行目录(run-long --cwd / worktree)须在本 run 内; run 在非治理根所在机 = 门读不到 run 内证据 = 拒(fail-closed)。"""
    from test_aipos_f196_validation_to_pi import HOST, _fp

    gov, base = vrig.gov, vrig.base
    candidate, tree = _setup_cards(vrig)
    decl = _declare(gov, base, VALR, AUDITOR)
    _run_start(gov, decl, candidate)
    run = _prepare_run(gov, decl)
    entries = _exec_commands(run, candidate, tree)
    outside = [{**entries[0], "worktree": str(vrig.code_repo)}, entries[1]]
    bad = _check(gov, VALR, VAL, candidate, outside)
    _show("[件① 拒·运行目录不在本 run]\n  " + bad["reasons"][0])
    assert f"运行目录(worktree / run-long --cwd){vrig.code_repo} 不在本 run 目录 {run} 内" in bad["reasons"][0]
    sneaky = [{**entries[0], "worktree": f"{run}/src/../../run-9"}, entries[1]]
    assert "不在本 run 目录" in _check(gov, VALR, VAL, candidate, sneaky)["reasons"][0]

    rc, out, err = _vr(gov, "declare", "--host", HOST, "--base", base / "remote", "--task-id", VAL2R, "--instance", AUDITOR,
                       "--owner-ref", OWNER_REF, "--host-fingerprint", _fp(), "--actor", DRIVER, "--confirm")
    assert rc == 0, out + err
    remote_decl = f"vr-{VAL2R.lower()}-auditor-1"
    _run_start(gov, remote_decl, candidate)
    rdir = base / "remote" / VAL2R / "run-1"
    remote_entries = [{**e, "run_id": "run-1", "run_dir": str(rdir)} for e in entries]
    bad = _check(gov, VAL2R, VAL2, candidate, remote_entries)
    _show("[件① 拒·run 在非治理根所在机(fail-closed)]\n  " + bad["reasons"][0])
    assert f"在主机 {HOST}(非治理根所在机), 裁决入口读不到 run 内送达核对证据与日志(fail-closed)" in bad["reasons"][0]
