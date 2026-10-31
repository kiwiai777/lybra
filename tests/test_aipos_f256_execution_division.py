"""AIPOS-F256 — 执行分工收紧: 非代码一律 pi, 顾问子 agent 由产品限定只做代码卡(Owner 10-31 产品化第 5 项)。

件① 单一声明 + 单一判定: enums.schema task_mode.executor(每个 task_mode 的执行方 kind + 各 kind 接受的执行方类别; 类别按 harness 声明
    advisor_session 判, 不按名单); 未知 task_mode = fail-closed 拒; pi_allowed_task_modes 退役为兼容读取(不再改变路由)。
件② 五处同判(起草缺省 / 发卡核身份 / 认领 / 开工稿 / 交回入口), 子 agent 接非代码卡 = 拒并带出口原文(不静默改派)。
件③ 代码卡交回须在卡车道内相对认领基线有产品仓改动; 验证根 / 发布目标只认 pi 执行身份。
件④ project upgrade 预演 / 确认(删退役键, 在途违例卡逐张出口); roles list 诊断段; 向导 pi 执行工位必备步(见 test_aipos_f143 件④)。

靶场: 临时治理根 / 临时产品仓 / 临时 HOME(复用 F78 / F143 / F176 / F190 靶场构件, 禁第二份); 不连真实门、不读真实凭据、不起 pi。
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f143_execution_mode import AUDITOR, DRIVER, PIX, SUB, _cli, _meta, _registry  # noqa: E402  — 接入登记靶场唯一来源
from test_aipos_f176_project_upgrade import urig  # noqa: E402,F401  — 旧版治理根靶场(pytest 夹具复用)
from test_aipos_f190_validation_root import rig as vrig  # noqa: E402,F401  — 验证根靶场(pytest 夹具复用)
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter  # noqa: E402

ACTORS = {"subagent": (SUB, "claude-code"), "pi": (PIX, "pi")}
DIVISION = "EXECUTOR_DIVISION_REFUSED"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _gov(tmp_path, monkeypatch, *, pi_executor: str | None = PIX, modes: list[str] | None = None, git: bool = False) -> Path:
    from test_aipos_f73d_loop_driver import init_governance_repo
    from tools.aipos_cli.enrollment import set_project_execution

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    _registry(gov)
    set_project_execution(gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=pi_executor, pi_allowed_task_modes=modes or [],
                          dry_run=False)
    if git:  # 写治理真相的命令 --confirm 写后即落账(靶场治理仓 + 临时远端)
        init_governance_repo(gov)
    return gov


def _task_modes() -> list[str]:
    from tools.schema_loader import get_enum_values

    return list(get_enum_values("task_mode"))


# ===========================================================================
# 件① 声明形 + 判定按类别(不按名单)+ 未知 task_mode fail-closed
# ===========================================================================

def test_item1_declaration_single_source_and_class_by_advisor_session(monkeypatch):
    from tools.aipos_cli import distribution_sync
    from tools.aipos_cli.execution_division import division_refusal, harness_class
    from tools.aipos_cli.next_resolver import task_mode_executor, task_mode_executor_declaration

    decl = task_mode_executor_declaration()
    table = {m: task_mode_executor(m) for m in _task_modes()}
    _show(f"[件①·声明] 执行方 = {json.dumps(table, ensure_ascii=False)}; 缺 task_mode = {task_mode_executor('')}; "
          f"accepts = {json.dumps({k: v['accepts'] for k, v in decl['kinds'].items()}, ensure_ascii=False)}")
    assert table == {"code": "subagent_executor", "docs": "pi_executor", "governance": "pi_executor", "config": "pi_executor",
                     "audit": "pi_auditor", "validation": "pi_executor"}
    assert task_mode_executor("") == "pi_executor" and task_mode_executor("implement") is None
    assert {k: harness_class(k) for k in ("pi", "claude-code", "codex", "bogus")} == {
        "pi": "pi", "claude-code": "subagent", "codex": "subagent", "bogus": None}
    # 判据按 advisor_session 声明: 新增一个顾问会话 kind(假设声明)= 自动归子 agent 类, 同样被拒
    real_kinds, real_decl = distribution_sync.declared_harness_kinds, distribution_sync.harness_kind_declaration
    monkeypatch.setattr(distribution_sync, "declared_harness_kinds", lambda: (*real_kinds(), "hermes"))
    monkeypatch.setattr(distribution_sync, "harness_kind_declaration",
                        lambda k: {"advisor_session": True} if k == "hermes" else real_decl(k))
    refusal = division_refusal(Path("/nonexistent-gov"), {"task_id": "X-H", "task_mode": "docs", "harness": "hermes"}, stage="draft",
                               execution=None)
    _show(f"[件①·新 harness kind 自动受限] {refusal['message'][:160]}…")
    assert refusal["code"] == DIVISION and "subagent" in refusal["message"]
    unknown = division_refusal(Path("/nonexistent-gov"), {"task_id": "X-U", "task_mode": "implement"}, stage="claim", execution=None)
    _show(f"[件①·未知 task_mode] {unknown['message']}")
    assert unknown["code"] == "EXECUTOR_TASK_MODE_UNKNOWN"


# ===========================================================================
# 验收①: 6 task_mode × (子 agent / pi) × 五处判定原文表
# ===========================================================================

def _stage_results(gov: Path, mode: str, who: str, inst: str, harness: str) -> dict[str, str]:
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.draft_writer import create_draft, publish_identity_refusals
    from tools.aipos_cli.queue_mutation import mutate_queue_task
    from tools.aipos_cli.subagent_kickoff import KickoffRefused, render_kickoff_brief

    tid = f"F256-{mode.upper()}-{who.upper()}"
    meta = _meta(tid, task_mode=mode, harness=harness, assigned_to=inst, audit_by=AUDITOR)
    out: dict[str, str] = {}
    draft = create_draft(gov, dict(meta), "## Goal\n\nx\n", dry_run=True)
    out["draft"] = next((r for r in draft["blocking_reasons"] if "AIPOS-F256" in r), "放行(分工)")
    refusals, _view = publish_identity_refusals(gov, dict(meta))
    out["publish"] = next((r for r in refusals if "AIPOS-F256" in r), "放行(分工)")
    f78._card(gov, tid, "pending", assigned=inst, task_mode=mode, extra={"harness": harness})
    claim = mutate_queue_task(gov, "claim", task_id=tid, actor=inst, dry_run=True, profiles=load_agent_profiles(gov))
    out["claim"] = next((r for r in claim["blocking_reasons"] if "AIPOS-F256" in r), "放行(分工)")
    (gov / "5_tasks" / "queue" / "pending" / f"{tid.lower()}.md").unlink()
    f78._card(gov, tid, "claimed", assigned=inst, task_mode=mode, extra={"harness": harness, "claimed_by": inst})
    try:
        render_kickoff_brief(gov, tid)
        out["kickoff"] = "放行(出稿)"
    except KickoffRefused as exc:
        out["kickoff"] = exc.message if exc.code == "KICKOFF_EXECUTOR_DIVISION" else f"放行(分工; 后续 {exc.code})"
    if mode == "audit":
        out["return"] = "不适用(审计卡交的是审计报告, 走裁决入口; 认领即已按 pi_auditor 判)"
    else:
        f78._write(gov / "task_cards" / tid / "RETURN.md", f78._return_text(tid, f78.SHA_A, f78.SHA_A))
        check = validate_task_artifact(tid, gov)
        out["return"] = check["reasons"][0] if check["category"] == DIVISION else f"放行(分工; 后续 {check['category']})"
    return out


def test_accept1_matrix_six_modes_two_executors_five_stages(tmp_path, monkeypatch):
    gov = _gov(tmp_path, monkeypatch)
    matrix: dict[str, dict[str, dict[str, str]]] = {}
    for mode in _task_modes():
        for who, (inst, harness) in ACTORS.items():
            matrix.setdefault(mode, {})[who] = _stage_results(gov, mode, who, inst, harness)
    lines = ["| task_mode | 执行方 | 起草 | 发卡 | 认领 | 开工稿 | 交回 |", "|---|---|---|---|---|---|---|"]
    for mode, rows in matrix.items():
        for who, res in rows.items():
            cells = ["拒 " + DIVISION if DIVISION in v else v for v in res.values()]
            lines.append(f"| {mode} | {who} | " + " | ".join(cells) + " |")
    _show("[验收①·判定矩阵]\n" + "\n".join(lines))
    _show("[验收①·拒因原文(docs × 子 agent 五处)]\n" + "\n".join(f"  {k}: {v}" for k, v in matrix["docs"]["subagent"].items()))
    for mode, rows in matrix.items():
        for stage, text in rows["pi"].items():
            assert DIVISION not in text, (mode, "pi", stage, text)
        for stage, text in rows["subagent"].items():
            if mode == "code" or stage in ("kickoff", "return"):
                # AIPOS-F256B 件⑤: 开工稿 / 交回 = 认领后各处, 本矩阵的卡已在认领后队列目录(已认领凭据)= 过渡在途, 按认领时执行方继续
                assert DIVISION not in text, (mode, stage, text)
            elif not text.startswith("不适用"):
                assert DIVISION in text and "出口" in text, (mode, stage, text)


# ===========================================================================
# 验收②: 显式 harness=claude-code 的 docs 卡被拒 + 出口照抄可走通
# ===========================================================================

def test_accept2_explicit_subagent_docs_card_refused_and_exits_work(tmp_path, monkeypatch):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.draft_writer import create_draft
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    gov = _gov(tmp_path, monkeypatch, git=True)
    # 起草: 显式子 agent = 拒, 出口 = 去掉 harness / assigned_to(起草缺省派 pi)
    refused = create_draft(gov, _meta("F256-D1", task_mode="docs", harness="claude-code"), "## Goal\n\nx\n", dry_run=True)
    reason = next(r for r in refused["blocking_reasons"] if DIVISION in r)
    _show(f"[验收②·起草拒] {reason}")
    assert refused["verdict"] == "BLOCK" and "去掉 harness / assigned_to" in reason and PIX in reason
    ok = create_draft(gov, _meta("F256-D1", task_mode="docs"), "## Goal\n\nx\n")
    fm = parse_markdown_frontmatter((gov / ok["target_path"]).read_text(encoding="utf-8"))[0]
    _show(f"[验收②·按出口重起草] verdict={ok['verdict']} harness={fm['harness']} assigned_to={fm['assigned_to']}")
    assert ok["verdict"] != "BLOCK" and (fm["harness"], fm["assigned_to"]) == ("pi", PIX)
    # 在途待认领卡(规则前已发布, 显式子 agent): 认领拒, 出口 = queue amend 改派命令原文; 照抄执行后 pi 执行工位认领通过
    f78._card(gov, "F256-D2", "pending", assigned=SUB, task_mode="docs", extra={"harness": "claude-code"})
    claim = mutate_queue_task(gov, "claim", task_id="F256-D2", actor=SUB, dry_run=False, profiles=load_agent_profiles(gov))
    reason = next(r for r in claim["blocking_reasons"] if DIVISION in r)
    _show(f"[验收②·认领拒] {reason}")
    assert not claim.get("moved")
    command = re.search(r"(lybra --workspace-root \S+ queue amend .*?\"AIPOS-F256: 非代码卡改派 pi 执行工位\")", reason).group(1)
    argv = shlex.split(command)
    rc, out, err = _cli(*argv[1:])
    _show(f"[验收②·照抄出口] $ {command}\nexit {rc}\n{out}{err}")
    assert rc == 0, out + err
    fm = parse_markdown_frontmatter((gov / "5_tasks" / "queue" / "pending" / "f256-d2.md").read_text(encoding="utf-8"))[0]
    assert (fm["harness"], fm["assigned_to"], fm["agent_instance"]) == ("pi", PIX, PIX)
    moved = mutate_queue_task(gov, "claim", task_id="F256-D2", actor=PIX, dry_run=False, profiles=load_agent_profiles(gov))
    _show(f"[验收②·改派后 pi 认领] moved={moved.get('moved')} blocking={moved['blocking_reasons']}")
    assert moved.get("moved"), moved


def test_accept2_pi_executor_undeclared_exit_set_execution(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft

    gov = _gov(tmp_path, monkeypatch, pi_executor=None, git=True)
    refused = create_draft(gov, _meta("F256-U1", task_mode="config"), "## Goal\n\nx\n", dry_run=True)
    reason = refused["blocking_reasons"][0]
    _show(f"[验收②·未声明 pi_executor 起草拒] {reason}")
    assert "EXECUTOR_PI_UNDECLARED" in reason and "lybra onboarding guide" in reason
    command = re.search(r"(lybra project set-execution .*?) --dry-run", reason).group(1).replace("<pi 执行工位实例>", PIX)
    rc, out, err = _cli(*shlex.split(command)[1:], "--actor", DRIVER, "--confirm")
    _show(f"[验收②·照出口声明 pi 执行工位] $ {command} --actor {DRIVER} --confirm\nexit {rc}\n{out[:600]}{err}")
    assert rc == 0 and "落账: verdict=PASS" in out, out + err
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))["execution"]
    assert data["pi_executor"] == PIX and "pi_allowed_task_modes" not in data
    ok = create_draft(gov, _meta("F256-U1", task_mode="config"), "## Goal\n\nx\n", dry_run=True)
    assert ok["verdict"] != "BLOCK", ok["blocking_reasons"]


# ===========================================================================
# 验收③: 代码卡空 diff 交回被拒, 车道内有改动通过(blocked 停线交回不核)
# ===========================================================================

def _code_return(tmp_path, monkeypatch, task_id: str, rel: str | None, *, outcome: str | None = None) -> dict:
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    f78._card(gov, task_id, "claimed", extra={"lane": {"repo": "lybra", "paths": ["tools/aipos_cli/", "tests/"]}, "claimed_by": f78.EXEC})
    f78._claim_record(gov, task_id, f78.EXEC)
    data = json.loads((gov / "project.json").read_text())
    data["repos"] = {"default": "lybra", "items": {"lybra": data["code_repo"]}}
    (gov / "project.json").write_text(json.dumps(data), encoding="utf-8")
    code_repo = Path(data["code_repo"])
    if rel is None:
        f78._git(code_repo, "checkout", "-q", "-b", f"card/{task_id}")
        f78._git(code_repo, "commit", "-q", "--allow-empty", "-m", f"{task_id}: nothing")
        sha, tree = f78._git(code_repo, "rev-parse", "HEAD"), f78._git(code_repo, "rev-parse", "HEAD^{tree}")
        f78._git(code_repo, "checkout", "-q", "main")
    else:
        sha, tree = f78._branch_with_commit(code_repo, task_id, rel=rel)
    text = f78._return_text(task_id, sha, tree)
    if outcome:
        text = text.replace("---\n", f"---\noutcome: {outcome}\n", 1)
    f78._write(gov / "task_cards" / task_id / "RETURN.md", text)
    return validate_task_artifact(task_id, gov)


def test_accept3_code_card_empty_lane_diff_refused(tmp_path, monkeypatch):
    empty = _code_return(tmp_path / "a", monkeypatch, "F256-C1", None)
    _show(f"[验收③·空提交交回] category={empty['category']}\n  {empty['reasons'][0] if empty['reasons'] else ''}")
    assert empty["ok"] is False and empty["category"] == "INGEST_CODE_CHANGE_EMPTY" and "改 task_mode" in empty["reasons"][0]
    outside = _code_return(tmp_path / "b", monkeypatch, "F256-C2", "docs/notes.md")
    _show(f"[验收③·只改车道外] category={outside['category']}")
    assert outside["category"] == "INGEST_CODE_CHANGE_EMPTY" and "tools/aipos_cli/" in outside["reasons"][0]
    inside = _code_return(tmp_path / "c", monkeypatch, "F256-C3", "tools/aipos_cli/x.py")
    _show(f"[验收③·车道内有改动] ok={inside['ok']} category={inside['category']}")
    assert inside["ok"] and inside["category"] == "OK", inside
    blocked = _code_return(tmp_path / "d", monkeypatch, "F256-C4", None, outcome="blocked")
    _show(f"[验收③·blocked 停线交回不核改动] category={blocked['category']}")
    assert blocked["category"] != "INGEST_CODE_CHANGE_EMPTY", blocked


# ===========================================================================
# 验收④: 子 agent 身份写验证根 / 发布目标被拒
# ===========================================================================

def test_accept4_subagent_identity_refused_on_validation_root_and_release_target(vrig):
    from test_aipos_f187_kickoff_worktree_cleanup import _land_subagent
    from test_aipos_f190_validation_root import CARD, CHRIS, EXEC, _declare, _fp, _run_start
    from tools.aipos_cli.execution_division import surface_class_problem
    from tools.aipos_cli.write_boundary import _executor_class_gate, check_access

    gov = vrig["gov"]
    sub = f"exec.{CHRIS}.subx"
    _land_subagent(gov, sub, project=CHRIS, harness="codex", host="advmac")
    f78._card(gov, "HBX-70", "pending", assigned=sub, extra={"project": CHRIS, "harness": "codex", "lane": {"repo": "android-app", "paths": ["app/"]}})
    rc, out, err = _declare(vrig, sub, "HBX-70", "--host-fingerprint", _fp())
    _show(f"[验收④·子 agent 声明验证根] exit {rc}\n{err.strip()}")
    assert rc == 1 and "SURFACE_EXECUTOR_CLASS" in err
    # pi 执行工位声明 + 开 run 通过; 同一实例改登记为子 agent 后写 run 目录 = 拒(check_access 面判定放行后再核写者类别)
    rc, out, err = _declare(vrig, EXEC, CARD, "--host-fingerprint", _fp())
    assert rc == 0, out + err
    assert _run_start(vrig, "vr-hbx-60-executor-1")[0] == 0
    target = f"{vrig['base']}/{CARD}/run-1/out.txt"
    ok = check_access(gov, role="executor", path=target, level="mutate", host="sim-mac", instance=EXEC, task_id=CARD, run_id="run-1",
                      host_fingerprint=_fp())
    _show(f"[验收④·pi 执行工位写 run 目录] allowed={ok['allowed']} code={ok['code']}")
    assert ok["allowed"], ok
    _land_subagent(gov, EXEC, project=CHRIS, harness="codex", host="advmac")
    denied = check_access(gov, role="executor", path=target, level="mutate", host="sim-mac", instance=EXEC, task_id=CARD, run_id="run-1",
                          host_fingerprint=_fp())
    _show(f"[验收④·子 agent 身份写验证根] allowed={denied['allowed']} code={denied['code']}\n  {denied['reason']}")
    assert not denied["allowed"] and denied["code"] == "SURFACE_EXECUTOR_CLASS"
    # 发布目标面: 面判定放行后同一核(release_target.plan_release 以本卡认领实例传 task_id)
    granted = {"allowed": True, "code": "OK", "surface": "release_target", "reason": "fixture"}
    rt = _executor_class_gate(gov, granted, want_rank=1, instance=sub, task_id=None)
    _show(f"[验收④·子 agent 身份写发布目标] allowed={rt['allowed']} code={rt['code']}\n  {rt['reason']}")
    assert not rt["allowed"] and rt["code"] == "SURFACE_EXECUTOR_CLASS"
    assert surface_class_problem(gov, surface="release_target", instance=f"audit.{CHRIS}.devbox", task_id=None) is None
    assert _executor_class_gate(gov, granted, want_rank=0, instance=sub, task_id=None)["allowed"]  # 只读不受限


# ===========================================================================
# 验收⑤: 带 pi_allowed_task_modes 旧声明的项目经 upgrade 预演 / 确认后行为
# ===========================================================================

def test_accept5_legacy_pi_allowed_task_modes_upgrade(urig, monkeypatch):
    from test_aipos_f176_project_upgrade import AUD, EXE, PROJECT, _upgrade
    from test_aipos_f176_project_upgrade import ADV as UADV
    import test_aipos_f95_harness_launch as f95
    from tools.aipos_cli.draft_writer import create_draft
    from tools.aipos_cli.workspace_config import update_project_json

    gov = urig["gov"]
    pi_exec = f"exec.{PROJECT}.pi"
    f95._land(gov, pi_exec, "executor", f95._workstation(urig["tmp"] / "stations", gov, "f176-pi", "executor", pi_exec))

    def legacy(data: dict) -> None:
        data["execution"] = {"subagent_executor": EXE, "auditor": AUD, "pi_executor": pi_exec, "pi_allowed_task_modes": ["config"]}

    update_project_json(gov, legacy)
    f78._git(urig["home"], "add", "-A")
    f78._git(urig["home"], "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "-m", "fixture: legacy pi_allowed_task_modes")
    f78._git(urig["home"], "push", "-q", "origin", "main")

    def route(task_id: str, mode: str) -> tuple[str, str]:
        res = create_draft(gov, {**_meta(task_id, task_mode=mode, project=PROJECT, created_by=UADV)}, "## Goal\n\nx\n", dry_run=True)
        fm = parse_markdown_frontmatter(res["rendered_markdown"])[0]
        return res["verdict"], f"{fm.get('harness')}/{fm.get('assigned_to')}"

    before = {m: route(f"F256-L-{m}", m) for m in ("config", "docs")}
    rc, out, err = _upgrade(urig)
    _show(f"[验收⑤·预演] rc={rc}\n{out}{err}")
    assert rc == 0 and re.search(r"^\[5/5\] 执行分工迁移\(非代码一律 pi\): 待升级 — ", out, re.M)
    assert "删退役键 execution.pi_allowed_task_modes = [\"config\"]" in out
    rc, out, err = _upgrade(urig, "--confirm", "--actor", UADV)
    _show(f"[验收⑤·确认] rc={rc}\n{out}{err}")
    assert re.search(r"^\[5/5\] 执行分工迁移\(非代码一律 pi\): 已执行 — ", out + err, re.M), out + err
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))["execution"]
    assert "pi_allowed_task_modes" not in data and data["pi_executor"] == pi_exec
    rc, out, err = _upgrade(urig)
    assert re.search(r"^\[5/5\] 执行分工迁移\(非代码一律 pi\): 已是最新", out, re.M), out
    after = {m: route(f"F256-L2-{m}", m) for m in ("config", "docs")}
    _show(f"[验收⑤·路由前后] 升级前 {before} / 升级后 {after}(docs 不在旧列表亦派 pi: 旧键不再改变路由)")
    assert before == after and all(v == ("WARN", f"pi/{pi_exec}") or v[1] == f"pi/{pi_exec}" for v in after.values()), after


def test_item4_upgrade_lists_inflight_violations_and_roles_list_section(tmp_path, monkeypatch):
    from tools.aipos_cli.execution_division import division_summary, division_upgrade_step

    gov = _gov(tmp_path, monkeypatch, pi_executor=None, modes=[])
    f78._card(gov, "F256-V1", "claimed", assigned=SUB, task_mode="validation", extra={"harness": "claude-code", "claimed_by": SUB})
    f78._card(gov, "F256-V2", "pending", assigned=SUB, task_mode="config", extra={"harness": "claude-code"})
    f78._card(gov, "F256-V3", "pending", assigned=SUB, task_mode="code", extra={"harness": "claude-code"})
    before = (gov / "5_tasks" / "queue" / "claimed" / "f256-v1.md").read_bytes()
    view = division_upgrade_step(gov, dry_run=True)
    _show("[件④·upgrade 预演盘点] " + json.dumps({k: view[k] for k in ("pi_executor_missing", "noncode_cards", "retired_key")},
                                                 ensure_ascii=False) + "\n" + "\n".join(f"  {v['task_id']}: {v['exits'][-2]}" for v in view["violations"]))
    # AIPOS-F256B 件⑤: 已认领的 V1 = 过渡在途(信息项), 违例只剩待认领的 V2
    assert view["pi_executor_missing"] and [v["task_id"] for v in view["violations"]] == ["F256-V2"]
    assert [t["task_id"] for t in view["transitions"]] == ["F256-V1"]
    assert "queue amend" in view["violations"][0]["exits"][1]
    assert (gov / "5_tasks" / "queue" / "claimed" / "f256-v1.md").read_bytes() == before, "既有卡不改写"
    summary = division_summary(gov)
    _show("[件④·roles list 诊断段]\n" + "\n".join(summary["lines"]))
    assert summary["lines"][0].startswith("执行分工(AIPOS-F256") and "未声明 pi 执行工位" in summary["lines"][0]
    assert sum("✗ 在途违例" in ln for ln in summary["lines"]) == 1 and sum("~ 过渡在途" in ln for ln in summary["lines"]) == 1
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list")
    assert "执行分工(AIPOS-F256" in out, out + err


def test_item2_next_does_not_derive_claim_for_division_violation(tmp_path, monkeypatch):
    from tools.aipos_cli.next_resolver import derive_next_step

    gov = _gov(tmp_path, monkeypatch)
    f78._card(gov, "F256-N1", "pending", assigned=SUB, task_mode="governance", extra={"harness": "claude-code"})
    d = derive_next_step("F256-N1", gov)
    _show(f"[件④·next 违例卡] derivable={d['derivable']} action={d['action']}\n  suggested: {d['suggested_action']}")
    assert d["derivable"] is False and d["action"]["type"] == "executor_division" and "queue amend" in d["suggested_action"]


def test_kickoff_and_return_stage_refuse_only_without_claim_evidence(tmp_path, monkeypatch):
    """开工稿 / 交回口径的严格判定仍在(无认领凭据的卡 = 拒并给撤卡重发出口); 有认领凭据 = 过渡(见验收⑧)。"""
    from tools.aipos_cli.execution_division import division_refusal

    gov = _gov(tmp_path, monkeypatch)
    card = {"task_id": "F256-K0", "task_mode": "docs", "harness": "claude-code", "assigned_to": SUB}  # 不在队列、无 claim 记录
    for stage in ("kickoff", "return"):
        refusal = division_refusal(gov, card, stage=stage, actor=SUB)
        _show(f"[件②·{stage} 无认领凭据] {refusal['message'][:200]}…")
        assert refusal["code"] == DIVISION and "queue withdraw" in refusal["message"]


# ===========================================================================
# 验收⑧(AIPOS-F256B 件⑤): 已认领的子 agent 非代码卡 = 过渡在途(交回 / 开工稿 / 写面通过); 待认领同类卡认领被拒
# ===========================================================================

def test_accept8_inflight_subagent_noncode_card_transitions_pending_refused(tmp_path, monkeypatch):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.execution_division import division_summary, division_transition, surface_class_problem
    from tools.aipos_cli.queue_mutation import mutate_queue_task
    from tools.aipos_cli.subagent_kickoff import KickoffRefused, render_kickoff_brief

    gov = _gov(tmp_path, monkeypatch)
    # 新规则前已认领(门生 claim 记录 + 卡面 claimed_by)的子 agent validation / docs 卡
    for tid, mode in (("F256-T1", "docs"), ("F256-T2", "validation")):
        f78._card(gov, tid, "claimed", assigned=SUB, task_mode=mode, extra={"harness": "claude-code", "claimed_by": SUB})
        f78._claim_record(gov, tid, SUB)
    # 人肉期存量: 卡在已认领目录、无 claim 记录 / claimed_by、task_mode 不在值域
    f78._card(gov, "F256-T3", "claimed", assigned="codex-legacy-exec", task_mode="ops", extra={"harness": "codex"})
    trans = division_transition(gov, {"task_id": "F256-T1", "task_mode": "docs", "harness": "claude-code", "claimed_by": SUB}, actor=SUB)
    _show(f"[验收⑧·过渡判定] {trans['message']}")
    assert trans["code"] == DIVISION and "claim 记录" in trans["evidence"]
    try:
        render_kickoff_brief(gov, "F256-T1")
        kick = "出稿"
    except KickoffRefused as exc:
        kick = exc.code
    _show(f"[验收⑧·已认领子 agent docs 卡开工稿] {kick}")
    assert kick == "出稿", kick  # 交回落盘前开工(落盘后 = 已交待入门, 不再开工)
    f78._write(gov / "task_cards" / "F256-T1" / "RETURN.md", f78._return_text("F256-T1", f78.SHA_A, f78.SHA_A))
    check = validate_task_artifact("F256-T1", gov)
    _show(f"[验收⑧·已认领子 agent docs 卡交回] category={check['category']}(分工判定放行; 后续为本靶场未布置的分支)")
    assert check["category"] != DIVISION and not any("AIPOS-F256" in r for r in check["reasons"]), check
    assert surface_class_problem(gov, surface="validation_root", instance=SUB, task_id="F256-T2") is None  # 过渡卡写面按认领时执行方
    assert surface_class_problem(gov, surface="validation_root", instance=SUB, task_id=None)["code"] == "SURFACE_EXECUTOR_CLASS"
    # 待认领的同类卡: 认领仍按新规则拒
    f78._card(gov, "F256-T4", "pending", assigned=SUB, task_mode="docs", extra={"harness": "claude-code"})
    claim = mutate_queue_task(gov, "claim", task_id="F256-T4", actor=SUB, dry_run=True, profiles=load_agent_profiles(gov))
    reason = next(r for r in claim["blocking_reasons"] if DIVISION in r)
    _show(f"[验收⑧·待认领同类卡认领拒] {reason}")
    assert "queue amend" in reason
    summary = division_summary(gov)
    _show("[验收⑧·roles list 诊断段]\n" + "\n".join(summary["lines"]))
    assert [v["task_id"] for v in summary["violations"]] == ["F256-T4"]
    assert sorted(t["task_id"] for t in summary["transitions"]) == ["F256-T1", "F256-T2", "F256-T3"]
    assert sum("~ 过渡在途" in ln for ln in summary["lines"]) == 3 and sum("✗ 在途违例" in ln for ln in summary["lines"]) == 1


def test_single_implementation_pointers_resolve():
    from tools.aipos_cli.next_resolver import task_mode_executor_declaration

    decl = task_mode_executor_declaration()
    for pointer in (decl["single_implementation"], decl["judgment"]):
        module, _, attr = pointer.replace("/", ".").rpartition(".")
        mod = __import__(module, fromlist=[attr])
        assert callable(getattr(mod, attr)), pointer
    roles = json.loads((REPO_ROOT / "schema" / "roles.schema.json").read_text(encoding="utf-8"))["write_boundary"]
    assert roles["executor_class_surfaces"]["surfaces"] == ["validation_root", "release_target"]
    src = subprocess.run(["git", "grep", "-n", "_pi_mode_refusal", "--", "tools/"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert src.returncode == 1, src.stdout  # F143 选配拒因已删除(无调用方)
