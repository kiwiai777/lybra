"""AIPOS-F164 — 工位章程新鲜度核查(gap #149): check-workstation 报章程未渲染 / 过期; loop 拉起 pi 前核章程指纹, 过期先 sync。

靶场 = AIPOS-F95 拉起靶场 lrig(F90 tmp 治理根 + 进程内门 + tmp 工位 + 假 harness; 工位章程经假门 RepoGate 由产品 sync 渲染,
RepoGate 清单 = 真 build_role_manifest、内容 = 产品树真文件)。人肉期样本 = 临时工位上自造的手写章程(无渲染指纹), 不读写任何真实
治理根 / 真实工位 / 真门。

验收(卡面 ★验收):
 ① 临时根: unrendered 工位 check-workstation FAIL 原文(给 sync 修法)、sync 后 ✓ 原文; 声明变 = 指纹过期 FAIL
 ② 临时根 loop 拉起审计: 章程未渲染 → 自动 sync → 拉起(运行记录 charter_checks detail); sync 失败 → 不拉起 advisor_fix;
    关闭自动 sync → 拒拉起; 章程最新 → 不 sync 直接拉起
 防碎片化: 判定只经 distribution_sync.sync(dry_run=True)(= `lybra sync --dry-run`), 修复只经 distribution_sync.sync(= `lybra sync`)
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC  # noqa: E402
from test_aipos_f82_workstation_intake import RepoGate  # noqa: E402  — 假门唯一来源
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import AUDIT_CLAIMER, POLICY_LAUNCH, _land, lrig  # noqa: E402,F401  — 拉起靶场
from tools.aipos_cli import distribution_sync as ds  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract, run_loop  # noqa: E402

TASK = f90.TASK
AUDIT = f90.AUDIT
#: 人肉期手写章程样本(chris hbj-auditor 08-30 形: 无渲染指纹, 写着不依赖门)
HANDWRITTEN = "# hbj-auditor 章程(08-30 手写)\n\n文件制人工编排, 不依赖 Lybra Gate。\n"
RENDER_MARK = "<!-- lybra:charter-render master_sha256="


def _show(line: str) -> None:
    print(line, flush=True)


def _cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main([str(a) for a in argv])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _unrender(ws: Path) -> None:
    """把已渲染工位退回人肉期形: 手写 AGENTS.md + 本角色本地清单无渲染指纹(删 .version-<role>)。"""
    (ws / "AGENTS.md").write_text(HANDWRITTEN, encoding="utf-8")
    role = json.loads((ws / ".lybra" / "role").read_text(encoding="utf-8"))["role"]
    (ws.parent / "_distributed" / f".version-{role}").unlink()


def _check_ws(gov: Path, instance: str) -> tuple[int, str]:
    rc, out, err = _cli(["project", "check-workstation", gov.name, "--home-root", gov.parent, "--instance", instance, "--harness", "pi"])
    return rc, out + err


def _runs(gov: Path, card: str = TASK) -> list[Path]:
    return sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md"))


def _meta(gov: Path, card: str = TASK) -> dict:
    paths = _runs(gov, card)
    assert len(paths) == 1, paths
    return lrr.read_run(paths[0], lrr.run_record_declaration(load_loop_contract()))


def _start_card(r) -> None:
    f90._card(r.gov, TASK, "pending", harness="pi")
    init_governance_repo(r.gov)
    _land(r.gov, EXEC, "executor", r.ws_exec)
    _land(r.gov, AUDIT_CLAIMER, "auditor", r.ws_audit)


def _loop(r) -> tuple[object, str]:
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30)
    return res, out.getvalue()


def _charter_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "charter:" in line or "章程不新鲜" in line]


# ===========================================================================
# ① check-workstation 章程项(临时根)
# ===========================================================================

def test_item1_check_workstation_unrendered_fails_with_sync_fix_then_ok_after_sync(lrig):
    gov, ws = lrig.gov, lrig.ws_audit
    _land(gov, AUDIT_CLAIMER, "auditor", ws)
    _unrender(ws)
    rc, text = _check_ws(gov, AUDIT_CLAIMER)
    _show(f"---- ① check-workstation(人肉期手写章程, 无渲染指纹)rc={rc} 原文 ----\n{text}")
    fix = ds.render_sync_command(ws, gov)
    assert rc == 1 and f"check-workstation {AUDIT_CLAIMER}: FAILED" in text
    line = next(ln for ln in text.splitlines() if "章程新鲜度" in ln)
    assert line.startswith("✗ 章程新鲜度: stale: ") and "unrendered(无渲染指纹" in line and f"修法: {fix}" in line
    assert f"复核: {fix} --dry-run" in line
    assert (ws / "AGENTS.md").read_text(encoding="utf-8") == HANDWRITTEN  # check-workstation 零写入

    # 修法 = lybra sync(同一 CLI)
    rc_sync, out_sync, err_sync = _cli(["sync", "--harness-root", ws, "--workspace-root", gov])
    _show(f"---- ① 修法 lybra sync rc={rc_sync} 原文 ----\n{out_sync}{err_sync}")
    assert rc_sync == 0 and "probe-auditor: synced" in out_sync
    rc, text = _check_ws(gov, AUDIT_CLAIMER)
    _show(f"---- ① sync 后 check-workstation rc={rc} 原文 ----\n{text}")
    assert rc == 0 and f"check-workstation {AUDIT_CLAIMER}: OK" in text
    line = next(ln for ln in text.splitlines() if "章程新鲜度" in ln)
    assert line.startswith("✓ 章程新鲜度: fresh: 章程件 ") and "渲染指纹与母本 / 声明一致" in line
    assert RENDER_MARK in (ws / "AGENTS.md").read_text(encoding="utf-8")


def test_item1_check_workstation_declaration_changed_is_stale_fingerprint(lrig):
    """指纹过期: 已渲染工位, 治理根项目声明变(project.json paths)→ 声明指纹不符 = FAIL(declaration_changed)。"""
    gov = lrig.gov
    _land(gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl["paths"] = {**(decl.get("paths") or {}), "return_root": "5_tasks/records/returns"}
    (gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    rc, text = _check_ws(gov, AUDIT_CLAIMER)
    _show(f"---- ① check-workstation(声明变 = 指纹过期)rc={rc} 原文 ----\n{text}")
    assert rc == 1 and "✗ 章程新鲜度: stale: " in text and "declaration_changed(项目/角色声明变)" in text


def test_item1_check_workstation_unverifiable_is_fail_closed(lrig, monkeypatch):
    """核不了(门不可达)= ✗ 并点名原因与修法, 不当作最新。"""
    from tools.aipos_cli import confirm_client

    _land(lrig.gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit)

    class DownGate(RepoGate):
        def initialize(self) -> None:
            raise confirm_client.GateError("Failed to connect to gate: fixture 门不可达\n  第二行诊断")

    monkeypatch.setattr(confirm_client, "GateClient", DownGate)
    rc, text = _check_ws(lrig.gov, AUDIT_CLAIMER)
    _show(f"---- ① check-workstation(门不可达)rc={rc} 原文 ----\n{text}")
    line = next(ln for ln in text.splitlines() if "章程新鲜度" in ln)
    assert rc == 1 and line.startswith("✗ 章程新鲜度: unverifiable: sync --dry-run 出错: GateError: ")
    assert "fixture 门不可达 | 第二行诊断" in line and "修法: lybra sync --harness-root" in line


# ===========================================================================
# ② loop 拉起前核章程(临时根)
# ===========================================================================

def test_item2_stale_auditor_charter_auto_synced_then_launched(lrig):
    """执行工位章程最新 → 不 sync 直接拉起; 审计工位人肉期手写章程 → 自动 sync 本工位 → 复核最新 → 拉起审计体 → completed。"""
    _start_card(lrig)
    _unrender(lrig.ws_audit)
    exec_before = _md5(lrig.ws_exec / "AGENTS.md")
    res, text = _loop(lrig)
    _show("---- ② lybra loop 输出原文(章程行)----\n" + "\n".join(_charter_lines(text)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    exec_line = next(ln for ln in text.splitlines() if "charter:" in ln and EXEC in ln)
    assert "章程新鲜(fresh: " in exec_line and "不 sync, 直接拉起" in exec_line
    assert _md5(lrig.ws_exec / "AGENTS.md") == exec_before  # 最新 = 未 sync
    stale_line = next(ln for ln in text.splitlines() if "charter:" in ln and AUDIT_CLAIMER in ln)
    assert "章程不新鲜(stale: " in stale_line and "unrendered(无渲染指纹" in stale_line
    assert f"自动 sync 本工位: {ds.render_sync_command(lrig.ws_audit, lrig.gov)}" in stale_line
    assert any(f"charter: 已 sync {lrig.ws_audit}(sync 已跑: files_fetched=" in ln and "复核: fresh), 拉起" in ln for ln in text.splitlines())
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [TASK, AUDIT]
    rendered = (lrig.ws_audit / "AGENTS.md").read_text(encoding="utf-8")
    assert RENDER_MARK in rendered and "不依赖 Lybra Gate" not in rendered
    assert ds.charter_freshness(lrig.ws_audit, lrig.gov)["state"] == "fresh"
    meta = _meta(lrig.gov)
    checks = meta["charter_checks"]
    _show("---- ② 运行记录 charter_checks ----\n" + json.dumps(checks, ensure_ascii=False, indent=1))
    assert [(c["card"], c["instance"], c["outcome"]) for c in checks] == [(TASK, EXEC, "fresh"), (AUDIT, AUDIT_CLAIMER, "synced")]
    assert "unrendered(无渲染指纹" in checks[1]["detail"] and "sync 已跑" in checks[1]["detail"]
    assert checks[1]["repair_command"] == ds.render_sync_command(lrig.ws_audit, lrig.gov)
    log = Path(meta["log_path"]).read_text(encoding="utf-8")
    assert sum(1 for ln in log.splitlines() if "charter: 已 sync" in ln) == 1  # 日志一行


def test_item2_sync_failure_refuses_launch_advisor_fix(lrig, monkeypatch):
    """审计工位章程不新鲜且门对审计凭据不可达(sync 失败)→ 不拉起审计体, 结束原因 charter_sync_failed, loop status 下一动作 advisor_fix。"""
    from tools.aipos_cli import confirm_client
    from tools.aipos_cli.verb_contract import declared_exit_code

    _start_card(lrig)
    _unrender(lrig.ws_audit)
    before = _md5(lrig.ws_audit / "AGENTS.md")

    class AuditorDownGate(RepoGate):
        def initialize(self) -> None:
            if self.role == "auditor":
                raise confirm_client.GateError("Failed to connect to gate: fixture 审计凭据连不上门")

    monkeypatch.setattr(confirm_client, "GateClient", AuditorDownGate)
    res, text = _loop(lrig)
    _show("---- ② sync 失败: lybra loop 输出原文(章程行)----\n" + "\n".join(_charter_lines(text)))
    assert res.exit_code == 4 and res.reason == "charter_sync_failed", text
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [TASK]  # 审计体未拉起
    assert "自动 sync 失败(sync 出错: GateError: Failed to connect to gate: fixture 审计凭据连不上门)" in res.message
    assert f"修法: {ds.render_sync_command(lrig.ws_audit, lrig.gov)}" in res.message and "修好用原命令重跑 loop" in res.message
    assert _md5(lrig.ws_audit / "AGENTS.md") == before
    meta = _meta(lrig.gov)
    assert meta["end_reason"] == "charter_sync_failed" and meta["charter_checks"][-1]["outcome"] == "sync_failed"
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", lrig.gov, "--json", "--wait", "1"])
    na = json.loads(out)["next_action"]
    _show("---- ② loop status --json next_action 原文 ----\n" + json.dumps(na, ensure_ascii=False, indent=1))
    assert rc == declared_exit_code("lybra_loop_status", "advisor_fix") and na["action"] == "advisor_fix"
    assert na["reason"] == "charter_sync_failed" and "例行结束 charter_sync_failed 第 1/" in na["detail"]
    fix = lrr.status_contract()["next_action"]["routine_rejections"]["end_reasons"]["charter_sync_failed"]["fix"]
    assert f"lybra loop --task-id {TASK} --workspace-root {lrig.gov}" in na["hint"] and len(fix) >= 3


def test_item2_auto_sync_off_refuses_launch(lrig):
    """project.json charter_auto_sync=false: 章程不新鲜即拒拉起(charter_not_fresh, 给 sync 修法), 工位零写入。"""
    decl = json.loads((lrig.gov / "project.json").read_text(encoding="utf-8"))
    decl["charter_auto_sync"] = False
    (lrig.gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    for ws in (lrig.ws_exec, lrig.ws_audit):  # 声明变后先按新声明渲染(本用例只看审计工位的「拒」)
        assert ds.sync(harness_root=ws, governance_root=lrig.gov)["status"] == "synced"
    _start_card(lrig)
    _unrender(lrig.ws_audit)
    before = _md5(lrig.ws_audit / "AGENTS.md")
    res, text = _loop(lrig)
    _show("---- ② 关闭自动 sync: lybra loop 输出原文(章程行)----\n" + "\n".join(_charter_lines(text)))
    assert res.exit_code == 4 and res.reason == "charter_not_fresh", text
    assert "自动 sync 未开(project.json charter_auto_sync=false), 不拉起 pi" in res.message
    assert f"修法: {ds.render_sync_command(lrig.ws_audit, lrig.gov)}" in res.message
    assert [s.launch["card"] for s in res.steps if s.launch and s.launch.get("launched")] == [TASK]
    assert _md5(lrig.ws_audit / "AGENTS.md") == before and not (lrig.ws_audit.parent / "_distributed" / ".version-auditor").exists()
    meta = _meta(lrig.gov)
    assert meta["end_reason"] == "charter_not_fresh" and meta["charter_checks"][-1]["outcome"] == "refused"
    assert lrr.status_contract()["next_action"]["by_end_reason"]["charter_not_fresh"] == "advisor_fix"


# ===========================================================================
# 防碎片化 + 声明
# ===========================================================================

def test_single_implementation_check_and_repair_go_through_sync(lrig, monkeypatch):
    """判定 = sync(dry_run=True)(`lybra sync --dry-run`), 修复 = sync(dry_run=False)(`lybra sync`); check-workstation / loop 无第二比对。"""
    from tools.aipos_cli.loop_driver import check_workstation

    calls: list[bool] = []
    real = ds.sync

    def spy(**kwargs):
        calls.append(bool(kwargs.get("dry_run")))
        return real(**kwargs)

    monkeypatch.setattr(ds, "sync", spy)
    _land(lrig.gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    report = check_workstation(lrig.gov, AUDIT_CLAIMER, "pi")
    assert report["ok"] and calls == [True]
    _unrender(lrig.ws_audit)
    calls.clear()
    out = ds.sync_workstation_charter(lrig.ws_audit, lrig.gov)
    assert out["ok"] and calls == [False, True]  # sync 本工位, 再以 dry-run 复核
    src = {name: (REPO_ROOT / "tools" / "aipos_cli" / name).read_text(encoding="utf-8")
           for name in ("loop_driver.py", "loop_run_record.py")}
    for name, text in src.items():
        assert "_charter_render_reason" not in text and "compute_diffs" not in text and "rendered_sha256" not in text, name


def test_declarations_auto_sync_key_and_end_reasons(tmp_path):
    from tools.aipos_cli.workspace_config import CharterAutoSyncError, charter_auto_sync
    from tools.schema_loader import load_schema

    key = load_schema("config")["configuration_sources"]["project_json"]["schema"]["charter_auto_sync"]
    assert key["type"] == "boolean" and key["default"] is True and key["required"] is False
    (tmp_path / "project.json").write_text(json.dumps({"project": "p"}), encoding="utf-8")
    assert charter_auto_sync(tmp_path) is True  # 缺键 = 缺省(行为: 自动 sync)
    assert charter_auto_sync(tmp_path, project={"charter_auto_sync": False}) is False
    with pytest.raises(CharterAutoSyncError, match="CHARTER_AUTO_SYNC_INVALID"):
        charter_auto_sync(tmp_path, project={"charter_auto_sync": "no"})
    run_decl = lrr.run_record_declaration(load_loop_contract())
    for reason in ("charter_sync_failed", "charter_not_fresh"):
        assert reason in run_decl["end_reasons"]
        assert lrr.status_contract()["next_action"]["by_end_reason"][reason] == "advisor_fix"
    lrr.next_action_declaration(run_decl)  # advisor_fix 结束原因须有修法(声明自检不抛)
    assert "charter_checks" in run_decl
