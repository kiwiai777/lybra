"""AIPOS-F291 — 能力例外卡的审计证据新增「交付物验证」(Owner 11-03 选①)。

依据: governance/decision_log/2026-11/2026-11-03-能力卡审计-无签名复现加交付物验签.md。chris HBLX-160(iOS 签名打包后的固定候选验证卡)经
AIPOS-F289 改写后, 审计须在自己 run 内产出签名产物, 但签名需目标主机图形会话钥匙串, 审计固定 pi(F284)经 ssh 做不到 → 审计必败。

验收(卡面 ★验收):
 ① 临时治理根 + 临时跨机验证根(假 ssh 就地执行模拟目标主机, 不连真实 Mac): 能力卡声明 deliverable_checks → 执行开工稿给摘要命令 →
    执行体交回带 sha256(交回入口接受; 缺节 = 拒)→ 审计开工稿给取回行与核验执行行 → 取回(哈希一致)→ 核验命令在审计 run 内执行 →
    证据清单 + 取回 → 裁决入口接受;
 ② 哈希不一致 = 取回拒 + 裁决入口拒; 核验命令不在审计 run 内 = 拒; 缺常规运行证据只有交付物验证 = 拒;
 ③ 无 requires_capabilities 的卡声明 deliverable_checks = 出卡预演拒(双语);
 ④ HBLX-160 形态(卡面同形复制到临时根)得到两段式改写建议与撤换出口原文; 无能力字段(HBLX-153 形态)= F289 出口 + 补能力提示; loop status 同出口;
 ⑥ en 新文案零 CJK; 唯一实现守卫。

靶场 = AIPOS-F196/F199/F200/F223/F289 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 目标主机 sim-mac = 本机模拟, 假 ssh 把远端命令就地交 sh);
测试夹具进程一律经 fixture_procs 助手(经 F95 / F196 靶场)。禁真门 / 真治理根 / 真工位 / 真 chris / 真 Mac / 真 pi。验收原文经 _show 打到 stdout。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli as _cli_list  # noqa: E402
from test_aipos_f196_validation_to_pi import ALIAS, HOST, SRC, SUB, VAL, _show, _validation_card, vrig  # noqa: E402,F401
from test_aipos_f199_validation_audit_run_evidence import VALR, _check, _run, _run_start  # noqa: E402
from test_aipos_f200_remote_run_evidence_fetch import (  # noqa: E402
    _declare_remote, _entry, _fake_ssh_double_dash, _fetch, _manifest, _prepare_remote_run, _set_host,
)
from test_aipos_f223_multi_host_evidence_host import _dispatch_steps, _kickoff_steps  # noqa: E402
from test_aipos_f234_in_transit_truth import _run_record  # noqa: E402
from test_aipos_f289_cross_host_evidence_redaction import _claimed_audit_card, _draft_meta, _exec_lines  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import validation_root as vr  # noqa: E402

MAC = "exec.lybra.simmac"      # 具备图形会话 + 登录钥匙串的子 agent 执行实例(登记主机 = 目标主机 sim-mac)
CAPS = ["gui_session", "login_keychain"]
APP = "archive/W.xcarchive/Products/Applications/W.app"   # 执行体签名交付物(目录包)在执行 run 内的相对路径
REGULAR = "cat {src_dir}/app/feature.txt"                  # 审计自己的常规运行证据(代表无签名复现)
VERIFY = ["cat {artifact_path}/Info.plist", "cat {artifact_path}/_CodeSignature/CodeResources"]
EVIDENCE = {"required": True, "host": HOST, "commands": [REGULAR], "deliverable_checks": [{"artifact": APP, "commands": VERIFY}]}


def _land_mac(gov: Path) -> None:
    from tools.aipos_cli.enrollment import _append_enrollment_trail, subagent_land_detail

    _append_enrollment_trail(gov, action="land", code_id="enroll_fixture_mac", role="executor", instance=MAC, by="(agent-enroll)",
                             reason=subagent_land_detail(host=HOST, harness="claude-code", mode="subagent", files=["connection.json"]))


@pytest.fixture
def drig(vrig):  # noqa: F811
    """跨机靶场 + 能力执行实例 MAC(子 agent, 主机 sim-mac; project.json execution.capability_executors)。卡面由各测试先写, 再 _arm。"""
    from tools.aipos_cli.enrollment import set_project_execution

    _fake_ssh_double_dash(vrig)
    vrig.tree = f78._git(vrig.code_repo, "rev-parse", f"{vrig.candidate}^{{tree}}")
    _land_mac(vrig.gov)
    view = set_project_execution(vrig.gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=EXEC,
                                 pi_allowed_task_modes=["validation", "config", "docs"], capability_executors=[f"{MAC}={','.join(CAPS)}"],
                                 dry_run=False)
    assert view.get("ok", True) is not False, view
    return vrig


def _cap_card(x, evidence: dict | None = None, *, capabilities: list[str] | None = CAPS, queue: str = "claimed") -> None:
    extra = {"harness": "claude-code", "claimed_by": MAC, "audit_runtime_evidence": evidence if evidence is not None else EVIDENCE}
    if capabilities:
        extra["requires_capabilities"] = capabilities
    _validation_card(x, queue=queue, assigned_to=MAC, agent_instance=MAC, **extra)


def _arm(x, *, audit_local: bool = False) -> None:
    """治理仓基线 + 目标主机别名 + 执行实例 MAC / 审计实例在 sim-mac(audit_local = 治理根所在机)的声明; 执行 run 起好并放入「已签名」
    交付物(目录包, 含软链)。"""
    from test_aipos_f199_validation_audit_run_evidence import _declare

    gov = x.gov
    init_governance_repo(gov)
    _set_host(gov)
    x.exec_decl = _declare_remote(gov, x.base, VAL, MAC)
    x.decl = _declare(gov, x.base, VALR, AUDITOR) if audit_local else _declare_remote(gov, x.base, VALR, AUDITOR)
    _run_start(gov, x.exec_decl, x.candidate)
    x.exec_run = _prepare_remote_run(gov, x.exec_decl)
    app = x.exec_run / APP
    (app / "_CodeSignature").mkdir(parents=True)
    (app / "Info.plist").write_text("CFBundleVersion=20261103.1\n", encoding="utf-8")
    (app / "_CodeSignature" / "CodeResources").write_text("signed-by=TEAM-FIXTURE\n", encoding="utf-8")
    (app / "W").write_text("binary\n", encoding="utf-8")
    (app / "W").chmod(0o755)
    (app / "Frameworks" / "K.framework" / "Versions" / "A").mkdir(parents=True)
    (app / "Frameworks" / "K.framework" / "Versions" / "A" / "K").write_text("lib\n", encoding="utf-8")
    (app / "Frameworks" / "K.framework" / "Versions" / "Current").symlink_to("A")


def _digest_line(x) -> str:
    """执行开工稿(本卡验证根节同一槽位, card_workstation_view)「交付物哈希」节 → 摘要命令。"""
    fm = nr._read_frontmatter(nr._find_task_in_queue(x.gov, VAL)[0])
    lines = nr.card_workstation_view(x.gov, VAL, fm)["validation_root_lines"]
    section = "\n".join(lines[next(i for i, ln in enumerate(lines) if ln.startswith("### 交付物哈希")):])
    _show(f"---- 验收① 执行开工稿「交付物哈希」节 ----\n{section}")
    commands = re.findall(r"交回前在本 run 内计算: `(.+)`$", section, re.MULTILINE)
    assert len(commands) == 1 and "deliverable-digest" in commands[0] and APP in commands[0] and "「交付物哈希」节" in section
    return commands[0]


def _return(x, entries: list | None, *, section: bool = True) -> Path:
    body = "# RETURN\n\n## 一句话结论\n已在图形会话签名打包。\n"
    if section:
        body += "\n## 交付物哈希\n\n```json\n" + json.dumps(entries, ensure_ascii=False) + "\n```\n"
    path = nr.return_artifact_dir(x.gov, VAL) / "RETURN.md"
    f78._write(path, f78._fm({"commit_sha": x.candidate, "tree_hash": x.tree, "branch": f"card/{SRC}", "model": "fixture"}, body))
    return path


def _run_ok(command: str) -> str:
    proc = _run(command)
    _show(f"[执行] exit {proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def _executor_returns(x) -> dict:
    out = _run_ok(_digest_line(x))
    entry = json.loads(next(ln for ln in out.splitlines() if ln.startswith("DELIVERABLE_ENTRY "))[len("DELIVERABLE_ENTRY "):])
    assert entry["artifact"] == APP and entry["run_id"] == "run-1" and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
    return entry


def _audit_entries(x, steps: str) -> tuple[list[dict], str]:
    """审计开工稿: 取回行 + 全部执行行(常规 + 核验)→ 跑 → CMD_ENTRY 作报告条目。返回 (条目, 取回输出)。"""
    pull = re.findall(r"^\s+(?:取回|fetch): `(.+)`$", steps, re.MULTILINE)
    assert len(pull) == 1 and "deliverable-pull" in pull[0] and str(x.exec_run) in pull[0]
    pulled = _run_ok(pull[0])
    entries = []
    for line in _exec_lines(steps):
        out = _run_ok(line)
        entries.append(json.loads(next(ln for ln in out.splitlines() if ln.startswith("CMD_ENTRY "))[len("CMD_ENTRY "):]))
    return entries, pulled


# ===========================================================================
# 验收① + ②: 全链(执行摘要 → 交回 → 审计取回 → 核验 → 裁决入口)与三种拒
# ===========================================================================

def test_acceptance1_2_capability_card_deliverable_verification_end_to_end(drig):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    x, gov = drig, drig.gov
    _cap_card(x)
    _claimed_audit_card(gov)
    _arm(x)
    # 交回入口: 卡面声明了交付物验证而报告缺「交付物哈希」节 = 拒
    _return(x, None, section=False)
    refused = validate_task_artifact(VAL, gov)
    _show(f"[验收① 交回缺「交付物哈希」节 → 交回入口] category={refused['category']}\n  " + "\n  ".join(refused["reasons"]))
    assert refused["ok"] is False and refused["category"] == "INGEST_DELIVERABLE_HASH_MISSING" and "交付物哈希" in refused["reasons"][0]
    # 执行体: 开工稿给出的摘要命令 → DELIVERABLE_ENTRY 原样填交回报告 → 交回入口接受
    entry = _executor_returns(x)
    _return(x, [entry])
    accepted = validate_task_artifact(VAL, gov)
    _show(f"[验收① 交回带 sha256 → 交回入口] ok={accepted['ok']} category={accepted['category']}")
    assert accepted["ok"] is True, accepted
    # 审计: run-start → 目录链 / 送达 / SRC_OK → 开工稿(派审注入同一渲染)
    run_id = _run_start(gov, x.decl, x.candidate)
    run_dir = _prepare_remote_run(gov, x.decl)
    steps = _kickoff_steps(gov)
    _show(f"---- 验收① 审计开工稿「验证卡运行证据步骤」(交付物验证段)----\n{steps}")
    assert "交付物验证(AIPOS-F291" in steps and entry["sha256"] in steps and f"{run_dir}/deliverables/1/W.app" in steps
    dispatch = _dispatch_steps(gov)
    assert "deliverable-pull" in dispatch and all(ln in dispatch for ln in _exec_lines(steps))
    entries, pulled = _audit_entries(x, steps)
    assert f"DELIVERABLE_OK item=1 kind=dir copy={run_dir}/deliverables/1/W.app sha256={entry['sha256']}" in pulled
    assert (run_dir / "deliverables" / "1" / "W.app" / "Frameworks" / "K.framework" / "Versions" / "Current").is_symlink()
    assert len(entries) == 3 and all(e["run_dir"] == str(run_dir) for e in entries)
    assert _manifest(gov, x.decl).returncode == 0 and _fetch(gov, x.decl, run_id)[0] == 0
    copy = vr.run_evidence_copy_dir(gov, _entry(gov, x.decl), run_id)
    assert (copy / "files" / ".lybra-vr" / "deliverables.jsonl").is_file() and not (copy / "files" / "deliverables").exists()
    got = _check(gov, VALR, VAL, x.candidate, entries)
    _show(f"[验收① 常规运行证据 + 交付物验证 → 裁决入口] {got}")
    assert got is None
    # ②-a 缺常规运行证据、只有交付物验证 = 拒
    only = _check(gov, VALR, VAL, x.candidate, entries[1:])
    _show("[验收② 只有交付物验证、缺常规运行证据 → 拒]\n  " + "\n  ".join(only["problems"][:2]))
    assert only["category"] == "RUNTIME_EVIDENCE_MISSING" and any("cat " + str(run_dir) + "/src/app/feature.txt" in p for p in only["problems"])
    # ②-b 核验命令不在审计 run 内(在执行 run 里核原件)= 拒
    outside = [*entries[:1], {**entries[1], "command": f"cat {x.exec_run}/{APP}/Info.plist"}, entries[2]]
    bad = _check(gov, VALR, VAL, x.candidate, outside)
    _show("[验收② 核验命令在执行 run 内 → 拒]\n  " + "\n  ".join(bad["problems"][:3]))
    assert bad["category"] == "RUNTIME_EVIDENCE_MISSING" and any("引用被审执行卡" in p for p in bad["problems"])
    assert any(f"{run_dir}/deliverables/1/W.app/Info.plist" in c for c in bad["missing_commands"])
    # ②-c 哈希不一致: 交回声明与取回副本不是同一份 = 裁决入口拒; 按错哈希取回 = helper 拒且不落副本(记 ok=false)
    wrong = {**entry, "sha256": "0" * 64}
    _return(x, [wrong])
    mismatch = _check(gov, VALR, VAL, x.candidate, entries)
    _show("[验收② 交回声明 sha256 ≠ 取回记录 → 裁决入口拒]\n  " + "\n  ".join(mismatch["problems"]))
    assert mismatch["category"] == "RUNTIME_EVIDENCE_MISSING" and any("哈希不一致" in p for p in mismatch["problems"])
    pull = re.findall(r"^\s+(?:取回|fetch): `(.+)`$", _kickoff_steps(gov), re.MULTILINE)[0]
    assert "0" * 64 in pull
    proc = _run(pull)
    _show(f"[验收② 按不一致的交回声明取回] exit {proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode != 0 and "DELIVERABLE_SHA_MISMATCH" in proc.stdout
    last = json.loads((run_dir / ".lybra-vr" / "deliverables.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["ok"] is False and last["source_sha256"] == entry["sha256"] and last["expect_sha256"] == "0" * 64


def test_acceptance2_audit_run_on_other_host_gives_host_outlet(drig):
    """审计 run 与执行 run 不在同一主机 = 取回行换成原因 + 改卡面 host 出口(交付物只在同一主机内复制)。"""
    x, gov = drig, drig.gov
    _cap_card(x, {k: v for k, v in EVIDENCE.items() if k != "host"})
    _claimed_audit_card(gov)
    _arm(x, audit_local=True)
    _return(x, [_executor_returns(x)])
    _run_start(gov, x.decl, x.candidate)
    steps = _kickoff_steps(gov)
    _show(f"---- 验收② 审计 run 在治理根所在机、执行 run 在 {HOST} ----\n{steps}")
    assert not re.findall(r"^\s+取回: `", steps, re.MULTILINE) and f"执行 run 在主机 {HOST}" in steps and f"host 设为 {HOST}" in steps


# ===========================================================================
# 验收③: 出卡预演(无 requires_capabilities / 非固定候选 / 常规命令空 = 拒; 双语)
# ===========================================================================

def test_acceptance3_draft_refuses_without_capabilities(drig, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft

    x, gov = drig, drig.gov
    _cap_card(x)
    init_governance_repo(gov)
    res = create_draft(gov, _draft_meta("PROBE-F291-V2", x.candidate, EVIDENCE), "## Goal\n\n签名验证\n", dry_run=True)
    reasons = [r for r in res["blocking_reasons"] if "deliverable_checks" in r]
    _show(f"---- 验收③ 无 requires_capabilities 声明 deliverable_checks · 出卡预演 verdict={res['verdict']} ----\n" + "\n".join(reasons))
    assert res["verdict"] == "BLOCK" and any(r.startswith("RUNTIME_EVIDENCE_DECLARATION_INVALID") and "requires_capabilities" in r for r in reasons)
    ok = create_draft(gov, _draft_meta("PROBE-F291-V3", x.candidate, EVIDENCE) | {"requires_capabilities": CAPS}, "## Goal\n\n签名验证\n",
                      dry_run=True)
    _show(f"[验收③ 带 requires_capabilities] verdict={ok['verdict']} 交付物验证拒因={[r for r in ok['blocking_reasons'] if 'deliverable' in r]}")
    assert not [r for r in ok["blocking_reasons"] if "deliverable_checks" in r]
    empty = create_draft(gov, _draft_meta("PROBE-F291-V4", x.candidate, {**EVIDENCE, "commands": []}) | {"requires_capabilities": CAPS},
                         "## Goal\n\nx\n", dry_run=True)
    assert any("不能替代审计自己的常规运行证据" in r for r in empty["blocking_reasons"])
    malformed = create_draft(gov, _draft_meta("PROBE-F291-V5", x.candidate, {**EVIDENCE, "deliverable_checks": [{"artifact": "../x", "commands":
                             ["cat x"]}]}) | {"requires_capabilities": CAPS}, "## Goal\n\nx\n", dry_run=True)
    _show("[验收③ 形不合] " + "\n".join(r for r in malformed["blocking_reasons"] if "deliverable_checks" in r))
    assert malformed["verdict"] == "BLOCK" and any("{artifact_path}" in r for r in malformed["blocking_reasons"])
    monkeypatch.setenv("LYBRA_LANG", "en")
    en = create_draft(gov, _draft_meta("PROBE-F291-V6", x.candidate, EVIDENCE), "## Goal\n\nx\n", dry_run=True)
    line = next(r for r in en["blocking_reasons"] if "deliverable_checks" in r)
    _show(f"[验收③ en] {line}")
    assert "is only open to cards declaring requires_capabilities" in line


# ===========================================================================
# 验收④: HBLX-160 / HBLX-153 形态(卡面同形, 执行 run 路径 = 本靶场执行声明的 run)
# ===========================================================================

def _hblx_commands(exec_run: Path) -> list[str]:
    app = f"{exec_run}/{APP}"
    return [f"ssh {ALIAS} 'plutil -extract CFBundleVersion raw -o - {app}/Info.plist'",
            f"ssh {ALIAS} 'for k in APP_SDK_ID APP_TOKEN; do v=$(plutil -extract $k raw -o - {app}/Info.plist 2>/dev/null); "
            "[ -n \"$v\" ] && echo \"$k non_empty\" || echo \"$k EMPTY\"; done'",
            f"ssh {ALIAS} 'codesign -dvv {app} 2>&1 | grep TeamIdentifier'"]


def test_acceptance4_hblx160_shape_two_stage_rewrite_and_supersede_outlet(drig):
    from tools.aipos_cli.artifact_ingest import evidence_declaration_problem

    x, gov = drig, drig.gov
    exec_run = x.base / VAL / "run-1"
    bad = {"required": True, "host": HOST, "commands": _hblx_commands(exec_run)}
    _cap_card(x, bad)
    _claimed_audit_card(gov)
    _arm(x)
    assert x.exec_run == exec_run
    audit_fm = nr._read_frontmatter(nr._find_task_in_queue(gov, VALR)[0])
    problem = evidence_declaration_problem(gov, VALR, audit_fm)
    _show(f"---- 验收④ HBLX-160 形态(带 requires_capabilities)· 推导核 / 裁决入口 ----\n{problem['line']}")
    two = problem["cross_host"]["two_stage"]["value"]
    _show(f"[验收④ 两段式声明] {json.dumps(two, ensure_ascii=False)}")
    assert problem["category"] == "RUNTIME_EVIDENCE_DECLARATION_INVALID"
    assert two["deliverable_checks"] == [{"artifact": APP, "commands": [
        "plutil -extract CFBundleVersion raw -o - {artifact_path}/Info.plist",
        "for k in APP_SDK_ID APP_TOKEN; do v=$(plutil -extract $k raw -o - {artifact_path}/Info.plist 2>/dev/null); "
        "[ -n \"$v\" ] && echo \"$k non_empty\" || echo \"$k EMPTY\"; done",
        "codesign -dvv {artifact_path} 2>&1 | grep TeamIdentifier"]}]
    assert two["commands"][0] == "plutil -extract CFBundleVersion raw -o - {run_dir}/" + APP + "/Info.plist" and two["host"] == HOST
    assert "两段式改写建议" in problem["line"] and f"queue supersede --task-id {VAL} --by <新卡号>" in problem["outlet"]
    assert "--restricted" not in problem["outlet"] and problem["amend"] == ""
    # 两段式声明本身过出卡判据(带能力); 照建议补一条复现构建命令即为可满足的新卡声明
    from tools.aipos_cli.artifact_ingest import check_runtime_evidence_declaration

    checked = check_runtime_evidence_declaration(two, governance_root=gov, audit_instance=AUDITOR, card_task_id="PROBE-F291-NEW",
                                                 subject_frontmatter={"task_id": "PROBE-F291-NEW", "task_mode": "validation",
                                                                      "requires_capabilities": CAPS})
    assert checked["problems"] == [], checked["problems"]
    # loop 以 runtime_evidence_exhausted 结束的存量运行: loop status 下一动作直接给同一出口
    _run_record(gov, VAL, status="ended", pid=999999, fingerprint=None)
    rc, status, err = _cli_list(["loop", "status", "--task-id", VAL, "--workspace-root", str(gov)])
    hint = [ln for ln in status.splitlines() if "两段式改写建议" in ln]
    _show("---- 验收④ loop status(runtime_evidence_exhausted)----\n" + "\n".join(hint))
    assert hint and f"queue supersede --task-id {VAL}" in hint[0]


def test_acceptance4_hblx153_shape_without_capabilities_keeps_f289_outlet(drig):
    from tools.aipos_cli.artifact_ingest import evidence_declaration_problem

    x, gov = drig, drig.gov
    bad = {"required": True, "host": HOST, "commands": _hblx_commands(x.base / VAL / "run-1")}
    _cap_card(x, bad, capabilities=None)
    _claimed_audit_card(gov)
    _arm(x)
    problem = evidence_declaration_problem(gov, VALR, nr._read_frontmatter(nr._find_task_in_queue(gov, VALR)[0]))
    _show(f"---- 验收④ HBLX-153 形态(无 requires_capabilities)----\n{problem['outlet']}")
    assert "--restricted" in problem["amend"] and "两段式改写建议" in problem["line"]
    assert "卡补 requires_capabilities 后按两段式撤换重发" in problem["outlet"]


# ===========================================================================
# 验收⑥ + 单源
# ===========================================================================

def test_acceptance6_en_texts_zero_cjk_and_single_source():
    from tools.aipos_cli import validation_root_helper as helper
    from tools.aipos_cli.artifact_ingest import deliverable_checks_declaration
    from tools.aipos_cli.i18n import has_cjk

    texts = deliverable_checks_declaration()["texts"]
    cjk = [k for k, v in texts["en"].items() if has_cjk(v)]
    _show(f"[验收⑥ deliverable_checks en 文案 {len(texts['en'])} 条, 含 CJK: {cjk}]")
    assert cjk == [] and set(texts["en"]) == set(texts["zh"])
    assert tuple(deliverable_checks_declaration()["entry_fields"]) == helper.DELIVERABLE_ENTRY_KEYS
    files = vr.run_evidence_declaration()["helper_files"]
    assert files["deliverable_record"] == helper.DELIVERABLE_EVIDENCE and files["deliverable_dir"] == helper.DELIVERABLE_DIR
    assert helper.DELIVERABLE_DIR not in vr.run_evidence_declaration()["include_dirs"]
    src = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "tools").rglob("*.py") if "tests" not in p.parts)
    for name in ("def deliverable_digest(", "def deliverable_check_problems(", "def return_deliverables(", "def deliverable_pull_command(",
                 "def deliverable_digest_command(", "def deliverable_record_problems(", "def deliverable_expanded_commands(",
                 "def parse_deliverable_checks("):
        assert src.count(name) == 1, name
    for path in (REPO_ROOT / "tools" / "aipos_cli").glob("*.py"):  # 换项目 / 换机器: 新增段不写死主机 / 工具名 / 卡号
        added = [ln for ln in path.read_text(encoding="utf-8").splitlines() if "deliverable" in ln.lower() or "AIPOS-F291" in ln]
        assert not [ln for ln in added for literal in ("kiwiai-mac", "codesign", "xcodebuild", "plutil", "HBLX") if literal in ln], path.name


def test_helper_digest_rejects_escaping_symlink(tmp_path):
    from tools.aipos_cli.validation_root_helper import deliverable_digest

    app = tmp_path / "A.app"
    app.mkdir()
    (app / "x").write_text("1", encoding="utf-8")
    first = deliverable_digest(str(app))
    (app / "x").write_text("2", encoding="utf-8")
    assert deliverable_digest(str(app))["sha256"] != first["sha256"]
    (app / "out").symlink_to("../../etc")
    escaped = deliverable_digest(str(app))
    _show(f"[helper 摘要: 软链逃出交付物目录] {escaped['problem']}")
    assert escaped["ok"] is False and "逃出" in escaped["problem"]
    assert os.path.islink(app / "out")
