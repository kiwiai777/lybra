"""AIPOS-F195 — 报告落点里的代码文件(10-20 chris 岚图 LANTU-SW13F1 landing_rejected 实撞)。

病根: 执行体为跑 main 侧基线临时写的驱动脚本 run.py 原样留在报告落点 artifacts/ 下; 交回入门与派审都不查, 卡审计 PASS、合入、
结案后, loop 第 10 步 N6 落账被提交门护栏 B① 拒(config.schema code_files_blacklist), 以 owner_needed(landing_rejected) 结束且无正式出口。

靶场一律临时治理仓 + 临时远端裸仓 + 临时产品仓(F78 / F94 同一夹具; 不连生产门, 不碰真实治理仓 / 真实产品仓)。落账走真实产品实现
(governance_commit 唯一落账实现; loop 用 F73D GateDouble 只替门侧记账, N6 落账步交真实 governance-commit)。

件① 入门即拦: 交回 / 审计报告入门(artifact_ingest)时报告落点出现黑名单扩展名文件 = 拒(INGEST_CODE_FILE_IN_SLOT), 拒因列文件与修法;
   改存 .py.txt 后入门; 开工稿与执行 / 审计章程同源明示。
件② 已审结卡 N6 处置: 产物槽内代码文件不入库、同槽 code-files-excluded.json(路径 / sha256 / 大小 / 原因)一并落账、原文件留盘, 推送成功,
   结果行「已排除 N 个代码文件, 清单已入库」; 槽外代码文件仍按 B① 拒; 提交门(真 pre-commit 钩子)不放宽。
件③ 恢复: F195 前形的 landing_rejected 运行记录(end_codes 空)→ loop status 以 N6 预演复核判 advisor_fix(ARTIFACT_SLOT_CODE_FILES),
   hint = 原命令重跑 loop; 原命令重跑只补 N6(不重复合入 / 结案)→ exit 0。槽外代码文件 = owner_needed 照旧。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f73d_loop_driver import POLICY, GateDouble, _policy, _write  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR as F78_AUDITOR  # noqa: E402
from test_aipos_f78_engine_agnostic import _card as _f78_card  # noqa: E402
from test_aipos_f78_engine_agnostic import _fm as _f78_fm  # noqa: E402
from test_aipos_f78_engine_agnostic import _ingest_setup, _return_text  # noqa: E402
from test_aipos_f94_governance_landing import DRIVER, GOV_REL, TASK, _closed_card, _git, rig  # noqa: E402,F401
from tools.aipos_cli import governance_commit as gc  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

HOOK_SRC = REPO_ROOT / "tools" / "hooks" / "governance-pre-commit"
MANIFEST = "code-files-excluded.json"
RUN_PY = "print('main baseline driver')\n"


def _show(line: str) -> None:
    sys.__stdout__.write(line + "\n")
    sys.__stdout__.flush()


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head_files(repo: Path, rev: str = "HEAD") -> set[str]:
    return set(_git(repo, "show", "--name-only", "--no-renames", "--format=", rev).splitlines())


def _chris_shape(rig: dict, *, hook: bool = True) -> Path:
    """把靶场治理根改为人肉期接入项目形(chris 现行: 交回 / 裁决落点在 5_tasks/records 下), 装母本 pre-commit 钩子(真提交门)。"""
    gov, repo = rig["gov"], rig["repo"]
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl["paths"] = {"return_root": "5_tasks/records/returns", "verdict_root": "5_tasks/records/audit_verdicts",
                     "task_cards_root": "5_tasks/records/returns"}
    _write(gov / "project.json", json.dumps(decl))
    _git(repo, "add", "--", f"{GOV_REL}/project.json")
    _git(repo, "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "-m", "chris-shape paths")
    _git(repo, "push", "-q")
    if hook:
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        shutil.copy(HOOK_SRC, hooks / "pre-commit")
        os.chmod(hooks / "pre-commit", 0o755)
    return gov


def _slot_run_py(gov: Path, task_id: str = TASK) -> Path:
    path = gov / "5_tasks" / "records" / "returns" / task_id / "artifacts" / "main-baseline" / "run.py"
    _write(path, RUN_PY)
    _write(path.parent / "summary.json", '{"passed": 3}\n')  # 同目录非代码证据照常入库
    return path


# ===========================================================================
# 件① 入门即拦(交回 / 审计报告 ingest 同一校验链)
# ===========================================================================

@pytest.mark.parametrize("shape", ["lybra", "chris"])
def test_item1_return_ingest_rejects_code_file_then_txt_passes(tmp_path, monkeypatch, shape):
    from tools.aipos_cli.aipos_cli import main
    from tools.aipos_cli.artifact_ingest import INGEST_EXIT_REJECTED, validate_task_artifact

    task_id = f"F195-ING-{shape.upper()}"
    gov, _code_repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, shape, task_id)
    _write(ret, _return_text(task_id, sha, tree))
    script = ret.parent / "artifacts" / "main-baseline" / "run.py"
    _write(script, RUN_PY)
    _write(ret.parent / "run_long" / "main-baseline" / "output.log", "ok\n")

    def ingest() -> tuple[int, dict]:
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        try:
            rc = main(["artifact", "ingest", "--task-id", task_id, "--workspace-root", str(gov), "--dry-run", "--json"])
        finally:
            monkeypatch.setattr(sys, "stdout", sys.__stdout__)
        return rc, json.loads(buf.getvalue())

    rc, data = ingest()
    _show(f"[件①·{shape}] 交回落点含 run.py → artifact ingest rc={rc} category={data['category']}\n  拒因原文: {data['reasons'][0]}")
    assert rc == INGEST_EXIT_REJECTED and data["ok"] is False and data["category"] == "INGEST_CODE_FILE_IN_SLOT", data
    reason = data["reasons"][0]
    assert reason.startswith("INGEST_CODE_FILE_IN_SLOT: ")  # 码形 = 门拒原文既有写法(loop end_codes 可抽)
    assert "artifacts/main-baseline/run.py" in reason and _sha(script) in reason
    assert "卡工作树或草稿目录" in reason and "run.py → run.py.txt" in reason and "sha256" in reason  # 修法
    # 修法: 改存 .py.txt(证据保留, 报告写来源与 sha256)
    script.rename(script.with_name("run.py.txt"))
    rc2, data2 = ingest()
    _show(f"[件①·{shape}] 改存 run.py.txt 后 → artifact ingest rc={rc2} category={data2['category']} message={data2['message']}")
    assert rc2 == 0 and data2["ok"] and data2["category"] == "DRY_RUN", data2
    assert validate_task_artifact(task_id, gov)["category"] == "OK"


def test_item1_verdict_ingest_rejects_code_file_in_audit_slot(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    task_id = "F195-ING-V"
    audit = f"{task_id}R"
    gov, _code_repo, sha, _tree, _ret = _ingest_setup(tmp_path, monkeypatch, "chris", task_id)
    _f78_card(gov, audit, "claimed", assigned=F78_AUDITOR, task_mode="audit", extra={"reviewed_task_id": task_id})
    report = gov / "5_tasks" / "records" / "audit_verdicts" / audit / "RETURN.md"
    _write(report, _f78_fm({"verdict": "PASS", "reviewed_task_id": task_id, "commit_sha": sha}, "# audit\n\n## 一句话结论\nPASS\n"))
    _write(report.parent / "verify.sh", "#!/bin/sh\necho verify\n")
    bad = validate_task_artifact(audit, gov)
    _show(f"[件①·审计落点] category={bad['category']}\n  拒因原文: {bad['reasons'][0]}")
    assert bad["ok"] is False and bad["kind"] == "verdict" and bad["category"] == "INGEST_CODE_FILE_IN_SLOT"
    assert "verify.sh" in bad["reasons"][0]
    (report.parent / "verify.sh").rename(report.parent / "verify.sh.txt")
    good = validate_task_artifact(audit, gov)
    assert good["ok"] and good["category"] == "OK", good


def test_item1_kickoff_and_charters_render_the_same_rule():
    """开工稿与执行 / 审计章程同读一处渲染(governance_guardrails.slot_code_rule_text), 扩展名来自 code_files_blacklist。"""
    from tools.aipos_cli.governance_guardrails import load_guardrail_declarations, slot_code_rule_text

    decls = load_guardrail_declarations()
    rule = slot_code_rule_text("`X/`", decls)
    _show(f"[件①·规则句] {rule}")
    assert rule and ".py" in rule and ".sh" in rule and "run.py → run.py.txt" in rule and "草稿目录" in rule
    assert all(f".{ext}" in rule for ext in decls.code_extensions)
    for role, key in (("executor", "return_slot_code_rule"), ("auditor", "verdict_slot_code_rule")):
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert "{{" + key + "}}" in master, role
    kickoff = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]["lybra_kickoff"]
    assert kickoff["texts"]["return_code_files"] == "- {report_code_rule}"
    for role in ("executor", "auditor"):
        layout = kickoff["layout"][role]
        assert layout.index("return_code_files") == layout.index("report_field_lines") + 1
    src = (REPO_ROOT / "tools" / "aipos_cli" / "subagent_kickoff.py").read_text(encoding="utf-8")
    assert "slot_code_rule_text" in src and "slot_code_rule_text" in (REPO_ROOT / "tools" / "aipos_cli" / "charter_render.py").read_text(encoding="utf-8")


def test_item1_ingest_rejection_is_routine_advisor_fix(tmp_path, monkeypatch):
    """loop 门拒原文(execute_derived_action 产物入口)抽得 INGEST_CODE_FILE_IN_SLOT = 例行码 → advisor_fix(派写报告者移出脚本, 原命令续跑)。"""
    from tools.aipos_cli import loop_run_record as lrr
    from tools.aipos_cli import next_resolver as nr
    from tools.aipos_cli.loop_driver import load_loop_contract

    task_id = "F195-ING-LOOP"
    gov, _code_repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, "chris", task_id)
    _write(ret, _return_text(task_id, sha, tree))
    _write(ret.parent / "artifacts" / "run.py", RUN_PY)
    command = f"lybra artifact ingest --task-id {task_id} --kind return --workspace-root {gov}"
    out = nr.execute_derived_action({"derivable": True, "task_id": task_id, "current_node": "claim", "command": command}, gov, None)
    msg = f"门拒 @ return ({task_id}) exit {out['exit_code']}: {out['message']}\n{out['output']}"  # loop_driver 门拒结束原文同形
    decl = lrr.run_record_declaration(load_loop_contract())
    codes = lrr.extract_end_codes(msg, decl)
    assert out["ok"] is False and codes == ["INGEST_CODE_FILE_IN_SLOT"], (out, codes)
    view = {"task_id": task_id, "run_id": "r", "state": "ended", "end_reason": "gate_rejected", "end_message": msg.splitlines()[0],
            "end_codes": codes, "driver": DRIVER, "envelope": POLICY}
    na = lrr.next_action(view, lrr.next_action_declaration(decl), governance_root=gov)
    _show(f"[件①·loop] 入门拒 → 下一动作 {na['action']}({na['reason']}) codes={na.get('codes')}\n{na['hint']}")
    assert na["action"] == "advisor_fix" and na["codes"] == ["INGEST_CODE_FILE_IN_SLOT"]
    assert "run.py → run.py.txt" in na["hint"] and na["hint"].splitlines()[-1].startswith(f"lybra loop --task-id {task_id}")


# ===========================================================================
# 件② 已审结卡 N6: 产物槽内代码文件正式处置
# ===========================================================================

def test_item2_n6_excludes_slot_code_file_records_manifest_keeps_file_and_pushes(rig: dict):
    gov, repo = _chris_shape(rig), rig["repo"]
    _closed_card(gov)
    script = _slot_run_py(gov)
    sha = _sha(script)
    head = _git(repo, "rev-parse", "HEAD")

    dry = gc.governance_commit(gov, TASK, DRIVER, dry_run=True)
    _show(f"[件②] N6 预演 verdict={dry['verdict']}\n  首行: {dry['message'].splitlines()[0]}")
    assert dry["verdict"] == "PASS" and [i["path"] for i in dry["excluded_code_files"]] == [
        f"5_tasks/records/returns/{TASK}/artifacts/main-baseline/run.py"]
    assert "将排除 1 个代码文件" in dry["message"].splitlines()[0]
    assert not (script.parent.parent.parent / MANIFEST).exists()  # 预演不写清单

    result = gc.governance_commit(gov, TASK, DRIVER)
    first = result["message"].splitlines()[0]
    _show(f"[件②] N6 落账 verdict={result['verdict']} committed={result['committed']} pushed={result['pushed']}\n  结果行: {first}")
    _show("[件②] " + "\n[件②] ".join(op for op in result["operations"] if "Slot code" in op or "Guardrails" in op))
    assert result["verdict"] == "PASS" and result["committed"] and result["pushed"], result["message"]
    assert "已排除 1 个代码文件" in first and "清单已入库" in first and MANIFEST in first
    files = _head_files(repo)
    manifest_repo = f"{GOV_REL}/5_tasks/records/returns/{TASK}/{MANIFEST}"
    assert manifest_repo in files and f"{GOV_REL}/5_tasks/records/returns/{TASK}/artifacts/main-baseline/summary.json" in files
    assert not any(f.endswith(".py") for f in files), files  # 治理仓零代码文件(真 pre-commit 钩子已过)
    assert script.read_text(encoding="utf-8") == RUN_PY  # 原文件留盘
    manifest = json.loads((gov / "5_tasks" / "records" / "returns" / TASK / MANIFEST).read_text(encoding="utf-8"))
    _show(f"[件②] 清单原文: {json.dumps(manifest, ensure_ascii=False)}")
    assert manifest["kind"] == "code_files_excluded" and manifest["task_id"] == TASK
    assert [(e["path"], e["sha256"], e["size"]) for e in manifest["files"]] == [("artifacts/main-baseline/run.py", sha, len(RUN_PY))]
    assert manifest["files"][0]["reason"] and manifest["files"][0]["excluded_by"] == DRIVER
    assert _git(repo, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main") != head
    landing = gc.task_landing(gov, TASK)
    _show(f"[件②] 落账判据 landed={landing['landed']} reason={landing['reason']!r}")
    assert landing["landed"], landing
    # 幂等: 重跑 = 已落账 no-op(留盘的 run.py 已记清单, 不算未落账)
    again = gc.governance_commit(gov, TASK, DRIVER)
    assert again["verdict"] == "PASS" and again["committed"] is False and _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")
    # 留盘文件被改 = 指纹变 = 未落账; 重跑 N6 = 清单追加新指纹(只追加)
    _write(script, RUN_PY + "# edited\n")
    assert not gc.task_landing(gov, TASK)["landed"]
    third = gc.governance_commit(gov, TASK, DRIVER)
    assert third["verdict"] == "PASS" and third["committed"], third["message"]
    entries = json.loads((gov / "5_tasks" / "records" / "returns" / TASK / MANIFEST).read_text(encoding="utf-8"))["files"]
    assert [e["sha256"] for e in entries] == [sha, _sha(script)]
    assert gc.task_landing(gov, TASK)["landed"]


def test_item2_code_file_outside_slot_still_rejected_by_b1(rig: dict):
    gov, repo = _chris_shape(rig), rig["repo"]
    _closed_card(gov)
    _slot_run_py(gov)
    outside = gov / "5_tasks" / "records" / "events" / TASK / "helper.py"  # 卡范围内但不在产物槽
    _write(outside, "x = 1\n")
    head = _git(repo, "rev-parse", "HEAD")
    result = gc.governance_commit(gov, TASK, DRIVER)
    _show(f"[件②·槽外] verdict={result['verdict']} rejected={result.get('rejected_files')}\n"
          + "\n".join(ln for ln in result["message"].splitlines() if "B①" in ln or "BLOCKED" in ln))
    assert result["verdict"] == "BLOCK" and result["committed"] is False
    assert result["rejected_files"] == [f"{GOV_REL}/5_tasks/records/events/{TASK}/helper.py"]
    assert "Code file forbidden in governance repo (B①)" in result["message"]
    assert _git(repo, "rev-parse", "HEAD") == head and not _git(repo, "diff", "--cached", "--name-only")


def test_item2_commit_gate_b1_not_relaxed(rig: dict):
    """提交门护栏 B① 不放宽: 绕过 N6 处置直接 git commit 槽内 .py, 真 pre-commit 钩子照拒。"""
    gov, repo = _chris_shape(rig), rig["repo"]
    script = _slot_run_py(gov, "AIPOS-F195X")
    _git(repo, "add", "--", str(script.relative_to(repo)))
    env = {**os.environ, "LYBRA_SCHEMA_DIR": str(REPO_ROOT / "schema")}
    proc = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], cwd=str(repo),
                          capture_output=True, text=True, env=env)
    _show(f"[件②·提交门] git commit 槽内 .py rc={proc.returncode}: "
          + " | ".join(ln for ln in (proc.stdout + proc.stderr).splitlines() if "B①" in ln or "BLOCKED" in ln))
    assert proc.returncode != 0 and "Code file forbidden in governance repo (B①)" in proc.stdout + proc.stderr
    _git(repo, "reset", "-q", "--", str(script.relative_to(repo)))


# ===========================================================================
# 件③ 恢复路径: loop status advisor_fix + 原命令重跑只补 N6
# ===========================================================================

def _pre_f195_n6(monkeypatch: pytest.MonkeyPatch) -> None:
    """复现 F195 前的 N6(无产物槽代码文件处置): 处置函数替为不排除。"""
    def _none(governance_root, manifest, selected_paths, repo_root, actor, *, dry_run):
        return {"excluded": [], "manifests": [], "written": [], "manifest": manifest, "selected_paths": selected_paths,
                "exclude_specs": [], "line": ""}

    monkeypatch.setattr(gc, "_apply_slot_code_exclusion", _none)
    monkeypatch.setattr(gc, "recorded_slot_code_files", lambda *a, **k: set())


def test_item3_loop_status_advisor_fix_then_rerun_only_n6(rig: dict, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.loop_run_record import loop_status

    gov, repo = _chris_shape(rig), rig["repo"]
    _closed_card(gov)
    _policy(gov)
    script = _slot_run_py(gov)
    with monkeypatch.context() as m:  # 实撞现场: F195 前的 loop 跑到第 N6 步被 B① 拒
        _pre_f195_n6(m)
        old = io.StringIO()
        res = run_loop(TASK, gov, actor=DRIVER, out=old, execute=GateDouble(gov), interval=0.01, max_wait=1, max_steps=5)
    _show(f"[件③] F195 前形 loop: exit={res.exit_code} outcome={res.outcome} reason={res.reason}")
    assert res.exit_code == 2 and res.reason == "landing_rejected" and "B①" in res.message
    status = loop_status(gov, TASK)
    na = status["next_action"]
    _show(f"[件③] loop status 下一动作: action={na['action']} reason={na['reason']} codes={na.get('codes')}\n  detail: {na['detail']}\n"
          f"  hint:\n{na['hint']}")
    assert status["runs"][-1]["end_reason"] == "landing_rejected" and status["runs"][-1]["end_codes"] == []
    assert na["action"] == "advisor_fix" and na["codes"] == ["ARTIFACT_SLOT_CODE_FILES"], na
    resume = f"lybra loop --task-id {TASK} --workspace-root {gov} --actor {DRIVER}"
    assert na["hint"].splitlines()[-1].startswith(resume), na["hint"]
    assert "只派 N6 落账步" in na["hint"] and "不重复合入 / 结案" in na["hint"]
    # 原命令重跑: 只补 N6(门侧动作零调用), exit 0, 结果行写明已排除
    gate = GateDouble(gov)
    out = io.StringIO()
    res2 = run_loop(TASK, gov, actor=DRIVER, out=out, execute=gate, interval=0.01, max_wait=1, max_steps=5)
    text = out.getvalue()
    _show("[件③] 原命令重跑 loop 原文(节选):\n" + "\n".join(ln for ln in text.splitlines() if "governance_commit" in ln or "已排除" in ln or "[end]" in ln))
    assert res2.exit_code == 0, res2.message
    assert gate.calls == [("governance_commit", TASK)]  # 已结案: 不重复合入 / 结案
    assert "已排除 1 个代码文件" in text and "清单已入库" in text
    assert gc.task_landing(gov, TASK)["landed"] and script.is_file()
    assert _git(repo, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")
    after = loop_status(gov, TASK)["next_action"]
    assert after["action"] == "card_done_take_next", after


def test_item3_outside_slot_code_file_stays_owner_needed(rig: dict, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.loop_run_record import loop_status

    gov = _chris_shape(rig)
    _closed_card(gov)
    _policy(gov)
    _slot_run_py(gov)
    _write(gov / "5_tasks" / "records" / "events" / TASK / "helper.py", "x = 1\n")
    with monkeypatch.context() as m:
        _pre_f195_n6(m)
        res = run_loop(TASK, gov, actor=DRIVER, out=io.StringIO(), execute=GateDouble(gov), interval=0.01, max_wait=1, max_steps=5)
    assert res.reason == "landing_rejected"
    na = loop_status(gov, TASK)["next_action"]
    _show(f"[件③·对照] 槽外代码文件: action={na['action']} reason={na['reason']}")
    assert na["action"] == "owner_needed" and na["reason"] == "landing_rejected"


def test_declarations_single_source():
    """新键只进既有 schema 文件; 判据唯一实现; 例行码 / 探针码各声明一处。"""
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    decl = config["governance_worktree"]["path_constraints"]["artifact_slot_code_files"]
    assert decl["ingest_category"] == "INGEST_CODE_FILE_IN_SLOT" and decl["excluded_manifest_name"] == MANIFEST
    transitions = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    assert transitions["nodes"]["N6"]["landing"]["slot_code_files"]["probe_code"] == "ARTIFACT_SLOT_CODE_FILES"
    routine = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))[
        "verbs"]["lybra_loop_status"]["next_action"]["routine_rejections"]
    assert {"INGEST_CODE_FILE_IN_SLOT", "ARTIFACT_SLOT_CODE_FILES"} <= set(routine["codes"])
    assert routine["landing_probe"]["end_reasons"] == ["landing_rejected"]
    hits = subprocess.run(["git", "grep", "-n", "-e", "ext in decls.code_extensions", "--", "tools/"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.strip().splitlines()
    assert len(hits) == 1 and "governance_guardrails.py" in hits[0], hits  # B① 扩展名判据唯一实现 is_code_file
    assert POLICY  # 信封夹具同 F73D
