"""AIPOS-F223 — 多主机验证审计的运行证据步骤选错主机(chris HBLX-42VC2R 实撞)。

形态: 验证卡(task_mode=validation, 固定候选)卡面 audit_runtime_evidence 命令只能在治理根所在机(dev)跑; 审计卡同一审计实例有两个有效
验证根声明——本机(dev, 用于 build / APK 核验)+ 远端(Mac, 用于 UI / 模拟器), 两个都已 run-start。原 validation_root.candidate_evidence_values
取 live[-1] = 远端, 把卡面命令渲染到远端 run 目录并附取回步骤。

件① 卡面 audit_runtime_evidence 可选 host(card.schema); run 选择唯一实现 validation_root.select_evidence_declaration(开工稿 / 派审步骤经
    candidate_evidence_values, 裁决入口经 locate_run → audit_run_selection_problem 同读)。未声明 host: 一个声明 = 照旧, 多个 = 拒因一行
    (列各声明 id / 主机 + 补声明出口), 裁决入口同判据 fail-closed; 报告 run 属未选中声明 = 拒因点名所选声明。
件② 在途卡经 `queue amend --restricted` 补 host(card.schema restricted_amend.claimed_card_subfields: audit_runtime_evidence 只许改 host;
    判据 artifact_ingest.runtime_evidence_amend_problems), 补后派审步骤 / 开工稿立即按新声明渲染。

靶场 = AIPOS-F196/F199/F200 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 「远端主机」sim-mac = 本机模拟, base 在 tmp)。
禁真门 / 真治理根 / 真工位 / 真 chris / 真 Mac / 真 pi。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import json
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
from test_aipos_f196_validation_to_pi import HOST, VAL, _cli, _show, _validation_card, _vr, vrig  # noqa: E402,F401  — F196 靶场
from test_aipos_f199_validation_audit_run_evidence import (  # noqa: E402  — F199 靶场(验证卡 + 审计证据判据入口)
    CMDS, VALR, _check, _declare, _exec_commands, _local, _prepare_run, _run_start,
)
from test_aipos_f200_remote_run_evidence_fetch import _declare_remote, _set_host  # noqa: E402  — F200 远端声明
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import validation_root as vr  # noqa: E402

REASON = "多主机验证: 声明运行证据执行主机(AIPOS-F223 靶场)"


def _evidence(host: str | None = None) -> dict:
    out = {"required": True, "commands": list(CMDS)}
    if host:
        out["host"] = host
    return out


@pytest.fixture
def twohost(vrig):  # noqa: F811
    """HBLX-42VC2R 形态: 在途(claimed, 已有返工轮)验证卡 + 审计卡(claimed)同一审计实例本机 + 远端两个声明, 各有活动 run; 卡面命令无 host。"""
    gov, base = vrig.gov, vrig.base
    _validation_card(vrig, queue="claimed", audit_runtime_evidence=_evidence(), claimed_by=EXEC,
                     rework_rounds=[{"round": 1, "focus_items": ["靶场: 返工轮存在"], "acceptance_criteria": ["靶场"], "cleared_at": None}])
    init_governance_repo(gov)
    candidate = vrig.candidate
    tree = f78._git(vrig.code_repo, "rev-parse", f"{candidate}^{{tree}}")
    _set_host(gov)
    local_decl = _declare(gov, base, VALR, AUDITOR)
    remote_decl = _declare_remote(gov, base / "mac", VALR, AUDITOR)
    _run_start(gov, local_decl, candidate)
    _run_start(gov, remote_decl, candidate)
    f78._card(gov, VALR, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": VAL, "claimed_by": AUDITOR, "agent_instance": AUDITOR, "harness": "pi", "audit": "", "audit_by": "",
                     "claim_id": f"claim_{VALR}_x", "claimed_at": "2026-10-24T00:00:00Z"})
    f78._claim_record(gov, VALR, AUDITOR)
    entries = {e["id"]: e for e in vr.project_validation_roots(gov)}
    vrig.tree, vrig.local_decl, vrig.remote_decl = tree, local_decl, remote_decl
    vrig.local_run = vr.run_directory(entries[local_decl], vr.active_run(entries[local_decl])["run_id"])
    vrig.remote_run = vr.run_directory(entries[remote_decl], vr.active_run(entries[remote_decl])["run_id"])
    return vrig


def _kickoff_steps(gov: Path) -> str:
    from tools.aipos_cli.subagent_kickoff import render_kickoff_brief

    text = render_kickoff_brief(gov, VALR)["text"]
    steps = text[text.index("### 验证卡运行证据步骤"):]
    return steps[:steps.index("## 禁写与禁止动作")]


def _dispatch_steps(gov: Path) -> str:
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section

    val_path, _q = nr._find_task_in_queue(gov, VAL)
    section = build_forensic_anchor_section(VAL, gov, nr._read_frontmatter(val_path), audit_task_id=VALR)
    return section[section.index("## 独立运行证据要求"):]


def _amend(gov: Path, value: dict, *extra: str) -> tuple[int, str, str]:
    return _cli("queue", "--workspace-root", gov, "amend", "--task-id", VAL, "--actor", DRIVER, "--restricted",
                "--amendment-reason", REASON, "--amendments", json.dumps({"audit_runtime_evidence": value}, ensure_ascii=False), *extra)


# ===========================================================================
# 验收①: 靶场复现 → 无 host 拒因一行 → host=本机渲染本机 run → host=远端渲染远端 run + 取回步骤
# ===========================================================================

def test_acceptance1_kickoff_selects_run_by_declared_host(twohost):
    gov, local_decl, remote_decl = twohost.gov, twohost.local_decl, twohost.remote_decl
    rc, out, _err = _vr(gov, "list")
    _show(f"---- 验收① 靶场声明(审计卡 {VALR} 同一审计实例 {AUDITOR}: 本机 {_local()} + 远端 {HOST})----\n{out}")
    assert local_decl in out and remote_decl in out

    steps = _kickoff_steps(gov)
    _show(f"---- 验收① 无 host · 审计开工稿「验证卡运行证据步骤」----\n{steps}")
    assert "VR_EVIDENCE_HOST_AMBIGUOUS" in steps and "禁任取" in steps
    for needle in (f"{local_decl}(主机 {_local()}", f"{remote_decl}(主机 {HOST}"):
        assert needle in steps, needle
    assert f"queue amend --task-id {VAL} --restricted" in steps and '"host": "<主机>"' in steps
    assert twohost.remote_run not in steps and twohost.local_run not in steps  # 拒 = 不渲染任何 run 命令
    assert "evidence-fetch" not in steps
    refusal = [ln for ln in steps.splitlines() if "VR_EVIDENCE_HOST_AMBIGUOUS" in ln]
    assert len(refusal) == 1  # 拒因一行
    dispatch = _dispatch_steps(gov)
    _show(f"---- 验收① 无 host · 派审注入(审计卡正文)----\n{dispatch}")
    assert refusal[0] in dispatch  # 派审注入同一渲染

    # host = 本机 → 本机 run 目录, 无远端取回步骤
    rc, out, err = _amend(gov, _evidence(_local()))
    assert rc == 0, out + err
    steps = _kickoff_steps(gov)
    _show(f"---- 验收① host={_local()}(本机)· 审计开工稿 ----\n{steps}")
    assert f"{local_decl}(主机 {_local()}" in steps and f"`cat {twohost.local_run}/src/app/feature.txt`" in steps
    assert twohost.remote_run not in steps and "evidence-fetch" not in steps and "evidence-manifest" not in steps
    assert "多主机验证(AIPOS-F223)" in steps and f"{remote_decl} 主机 {HOST}" in steps

    # host = 远端 → 远端 run 目录 + 证据清单 / 取回两步
    rc, out, err = _amend(gov, _evidence(HOST))
    assert rc == 0, out + err
    steps = _kickoff_steps(gov)
    _show(f"---- 验收① host={HOST}(远端)· 审计开工稿 ----\n{steps}")
    assert f"{remote_decl}(主机 {HOST}" in steps and f"`cat {twohost.remote_run}/src/app/feature.txt`" in steps
    assert f"project validation-root evidence-fetch --id {remote_decl}" in steps and "evidence-manifest" in steps
    assert twohost.local_run not in steps


def test_acceptance1_host_without_declaration_refused(twohost):
    gov = twohost.gov
    rc, out, err = _amend(gov, _evidence("other-host"))
    assert rc == 0, out + err
    steps = _kickoff_steps(gov)
    _show(f"---- 验收① host=other-host(无该主机声明)· 审计开工稿 ----\n{steps}")
    assert "VR_EVIDENCE_HOST_AMBIGUOUS" in steps and "host=other-host" in steps and "无该主机的有效验证根声明" in steps


# ===========================================================================
# 验收②: 在途卡经正式入口补 host(预演 / 确认原文), 只许改 host; 随后派审步骤按新声明
# ===========================================================================

def test_acceptance2_inflight_card_amend_host_restricted(twohost):
    gov = twohost.gov
    card_path, queue = nr._find_task_in_queue(gov, VAL)
    assert queue == "claimed" and nr._read_frontmatter(card_path).get("rework_rounds")

    # 只许改 host: 改命令 / required = 拒(不改验收); host 形不合 = 拒; 不带 --restricted 对 claimed 卡 = 原闸拒
    bad_cmd = {**_evidence(_local()), "commands": [*CMDS, "echo extra"]}
    rc, out, err = _amend(gov, bad_cmd, "--dry-run")
    _show(f"---- 验收② 受限 amend 改命令(拒)exit {rc} ----\n{out}{err}")
    assert rc == 1 and "已认领卡受限 amend 只许改 audit_runtime_evidence 的子键 ['host']" in out and "'commands'" in out
    rc, out, err = _amend(gov, _evidence("bad host!"), "--dry-run")
    _show(f"---- 验收② host 形不合(拒)exit {rc} ----\n{out}{err}")
    assert rc == 1 and "audit_runtime_evidence.host 须为主机标签" in out
    rc, out, err = _cli("queue", "--workspace-root", gov, "amend", "--task-id", VAL, "--actor", DRIVER, "--amendment-reason", REASON,
                        "--amendments", json.dumps({"audit_runtime_evidence": _evidence(_local())}), "--dry-run")
    assert rc == 1 and "Only pending (unclaimed) tasks can be amended" in out

    before = nr._read_frontmatter(card_path)["audit_runtime_evidence"]
    rc, out, err = _amend(gov, _evidence(_local()), "--dry-run")
    _show(f"---- 验收② 受限 amend 补 host 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and '"dry_run": true' in out
    assert nr._read_frontmatter(card_path)["audit_runtime_evidence"] == before  # 预演不写
    rc, out, err = _amend(gov, _evidence(_local()))
    _show(f"---- 验收② 受限 amend 补 host 确认 exit {rc} ----\n{out}{err}")
    assert rc == 0 and '"amendments_applied"' in out
    after = nr._read_frontmatter(card_path)
    assert after["audit_runtime_evidence"] == _evidence(_local()) and after.get("rework_rounds")
    dispatch = _dispatch_steps(gov)
    _show(f"---- 验收② 补 host 后 · 派审注入(审计卡正文)----\n{dispatch}")
    assert f"`cat {twohost.local_run}/src/app/feature.txt`" in dispatch and "VR_EVIDENCE_HOST_AMBIGUOUS" not in dispatch


def test_acceptance2_pending_card_amend_shape_checked(vrig):
    gov = vrig.gov
    _validation_card(vrig, audit_runtime_evidence=_evidence())
    init_governance_repo(gov)
    rc, out, err = _cli("queue", "--workspace-root", gov, "amend", "--task-id", VAL, "--actor", DRIVER, "--amendment-reason", REASON,
                        "--amendments", json.dumps({"audit_runtime_evidence": {"required": True, "commands": CMDS, "hosts": "x"}}), "--dry-run")
    _show(f"---- 验收② 未认领卡 amend 形不合(拒)exit {rc} ----\n{out}{err}")
    assert rc == 1 and "含未声明的键 ['hosts']" in out
    rc, out, err = _cli("queue", "--workspace-root", gov, "amend", "--task-id", VAL, "--actor", DRIVER, "--amendment-reason", REASON,
                        "--amendments", json.dumps({"audit_runtime_evidence": {"required": True, "commands": ["echo changed"], "host": _local()}}),
                        "--dry-run")
    assert rc == 0, out + err  # 未认领卡: 命令照常可改(行为不变), 只做形校验


# ===========================================================================
# 验收③: 裁决入口——报告 run 属未选中主机 = 拒因点名; 多声明无 host = 同判据拒; 选中主机的 run = 通过; 单声明旧卡不变
# ===========================================================================

def test_acceptance3_verdict_entry_binds_selected_host(twohost):
    gov, local_decl, remote_decl = twohost.gov, twohost.local_decl, twohost.remote_decl
    candidate, tree = twohost.candidate, twohost.tree
    local_run = _prepare_run(gov, local_decl)
    local_entries = _exec_commands(local_run, candidate, tree)
    remote_entries = [{**e, "run_id": Path(twohost.remote_run).name, "run_dir": twohost.remote_run} for e in local_entries]

    bad = _check(gov, VALR, VAL, candidate, local_entries)
    _show("[验收③ 无 host · 裁决入口(本机 run 记录)]\n  " + "\n  ".join(bad["reasons"]))
    assert bad["category"] == "RUNTIME_EVIDENCE_MISSING" and "VR_EVIDENCE_HOST_AMBIGUOUS" in bad["reasons"][0]
    assert local_decl in bad["reasons"][0] and remote_decl in bad["reasons"][0] and f"queue amend --task-id {VAL} --restricted" in bad["reasons"][0]

    assert _amend(gov, _evidence(_local()))[0] == 0
    ok = _check(gov, VALR, VAL, candidate, local_entries)
    _show(f"[验收③ host={_local()} · 裁决入口(所选本机 run 记录)] {ok}")
    assert ok is None
    bad = _check(gov, VALR, VAL, candidate, remote_entries)
    _show(f"[验收③ host={_local()} · 裁决入口(报告 run 属未选中远端声明)]\n  " + "\n  ".join(bad["reasons"]))
    assert "VR_EVIDENCE_HOST_MISMATCH" in bad["reasons"][0]
    assert f"属声明 {remote_decl}(主机 {HOST}), 不是运行证据所选声明 {local_decl}(主机 {_local()}" in bad["reasons"][0]
    assert f"run 目录 {twohost.local_run}" in bad["reasons"][0]

    assert _amend(gov, _evidence(HOST))[0] == 0
    bad = _check(gov, VALR, VAL, candidate, local_entries)
    _show(f"[验收③ host={HOST} · 裁决入口(报告 run 属未选中本机声明)]\n  " + "\n  ".join(bad["reasons"]))
    assert "VR_EVIDENCE_HOST_MISMATCH" in bad["reasons"][0] and f"不是运行证据所选声明 {remote_decl}(主机 {HOST}" in bad["reasons"][0]


def test_acceptance3_single_declaration_unchanged(vrig):
    """单声明旧卡(无 host): 开工稿照旧渲染该声明的 run、裁决入口照旧通过(回归)。"""
    gov, base = vrig.gov, vrig.base
    _validation_card(vrig, audit_runtime_evidence=_evidence())
    init_governance_repo(gov)
    candidate = vrig.candidate
    tree = f78._git(vrig.code_repo, "rev-parse", f"{candidate}^{{tree}}")
    decl = _declare(gov, base, VALR, AUDITOR)
    _run_start(gov, decl, candidate)
    f78._card(gov, VALR, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": VAL, "claimed_by": AUDITOR, "agent_instance": AUDITOR, "harness": "pi", "audit": "", "audit_by": "",
                     "claim_id": f"claim_{VALR}_x", "claimed_at": "2026-10-24T00:00:00Z"})
    f78._claim_record(gov, VALR, AUDITOR)
    run = _prepare_run(gov, decl)
    steps = _kickoff_steps(gov)
    _show(f"---- 验收③ 单声明无 host · 审计开工稿(行为不变)----\n{steps}")
    assert f"{decl}(主机 {_local()}" in steps and f"`cat {run}/src/app/feature.txt`" in steps
    assert "多主机验证" not in steps and "VR_EVIDENCE_HOST" not in steps
    assert _check(gov, VALR, VAL, candidate, _exec_commands(run, candidate, tree)) is None


# ===========================================================================
# 单源: 选择只在 validation_root 一处, 渲染与裁决入口同读; 禁 live[-1]
# ===========================================================================

def test_single_source_evidence_host_selection():
    import ast

    src = (REPO_ROOT / "tools" / "aipos_cli" / "validation_root.py").read_text(encoding="utf-8")
    funcs = {n.name: ast.unparse(n) for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    assert "select_evidence_declaration(" in funcs["candidate_evidence_values"]
    assert "select_evidence_declaration(" in funcs["audit_run_selection_problem"]
    assert "audit_run_selection_problem(" in funcs["locate_run"]
    assert "live[-1]" not in funcs["candidate_evidence_values"]
    assert src.count("def select_evidence_declaration") == 1
    decl = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
    assert decl["restricted_amend"]["claimed_card_subfields"]["audit_runtime_evidence"] == ["host"]
    assert "audit_runtime_evidence" not in decl["restricted_amend"]["claimed_card_fields"]  # 不进全集: 只按子键受限
    _show("[单源] 选择 validation_root.select_evidence_declaration; 开工稿 / 派审经 candidate_evidence_values, 裁决入口经 locate_run 同读")
