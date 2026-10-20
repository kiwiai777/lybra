"""AIPOS-F196 — 非代码工作交 pi(Owner 10-20 定: 只有代码卡由顾问派子 agent; 验证 / 环境 / 文档拉 pi; 审计 = pi)。

件① task_mode=validation: 固定候选(来源卡 + 40 位 sha 必填)的环境 / 模拟器构建验证与截图取证; 不写产品仓(不建卡工作树 / 卡分支),
    独立审计(enums.schema task_mode.independent_audit), N5 无合入(finalize 写明不合并不推送不部署), N6 结案。
件② 路由: execution.pi_allowed_task_modes 列内的 task_mode 起草缺省 harness=pi / assigned_to=pi_executor(loop 自动拉起); code 卡仍子 agent;
    手写工位章程(「禁止 Lybra 分发器覆盖」标记)sync 拒覆盖, 正式解除 = sync --adopt-hand-charter --owner-ref(备份保留)。
件③ dev 上 pi 经 ssh 操作目标主机: project.json validation_roots.hosts 声明 ssh 别名; 开工提示 / 章程验证根节与 init-command 渲染 ssh 形,
    helper 经管道送目标主机。
件④ 顾问提示: code 卡等待提示 = 子 agent; pi 路由卡 = loop 自动拉起。

靶场 = AIPOS-F90/F95 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 假 harness tests/fake_harness.py 替身 pi, 禁起真实 pi)
+ 本机模拟「目标主机」sim-mac(假 ssh 把远端命令就地交 sh 执行并记录别名; 目录 = tmp/mac/...)。禁真门 / 真治理根 / 真工位 / 真 Mac。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + loop 全链靶场(禁第二份)
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场(假 harness / 工位 / 章程同步)
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

SUB = "exec.lybra.advhost"          # 子 agent 执行者(顾问会话所在机)
SRC = "PROBE-F196-SRC"              # 来源卡(代码卡, 产出候选)
VAL = "PROBE-F196-V1"               # 验证卡
CFG = "PROBE-F196-C1"               # 环境(config)卡
CODE = "PROBE-F196-K1"              # 代码卡
POLICY_V = "pol_probe_f196_launch"
HOST = "sim-mac"
ALIAS = "sim-mac-alias"
OWNER_REF = "Owner 10-20 定: dev 上的 pi 经 ssh 操作 Mac(靶场)"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main([str(a) for a in argv])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


def _fp() -> str:
    from tools.aipos_cli.validation_root_helper import host_fingerprint

    return host_fingerprint()  # 「目标主机」= 本机模拟, 其 helper 算出的指纹


def _land_subagent(gov: Path) -> None:
    from tools.aipos_cli.enrollment import _append_enrollment_trail, subagent_land_detail

    _append_enrollment_trail(gov, action="land", code_id="enroll_fixture_sub", role="executor", instance=SUB, by="(agent-enroll)",
                             reason=subagent_land_detail(host="advhost", harness="claude-code", mode="subagent", files=["connection.json"]))


def _declare_execution(gov: Path, modes: list[str]) -> dict:
    from tools.aipos_cli.enrollment import set_project_execution

    return set_project_execution(gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=EXEC, pi_allowed_task_modes=modes, dry_run=False)


def _fake_bin(tmp: Path, mp: pytest.MonkeyPatch) -> Path:
    """模拟目标主机通道: 假 ssh(丢弃别名、记录、把远端命令交本机 sh, 标准输入原样透传 = helper 管道)+ lybra 薄壳(本代码树)。"""
    bindir = tmp / "fakebin"
    bindir.mkdir()
    log = tmp / "fake-ssh.log"
    (bindir / "ssh").write_text('#!/bin/sh\nalias="$1"; shift\nprintf \'%s\\t%s\\n\' "$alias" "$*" >> "$FAKE_SSH_LOG"\nexec sh -c "$*"\n',
                                encoding="utf-8")
    (bindir / "lybra").write_text(f'#!/bin/sh\nPYTHONPATH="{REPO_ROOT}" exec "{sys.executable}" -m tools.aipos_cli "$@"\n', encoding="utf-8")
    for name in ("ssh", "lybra"):
        (bindir / name).chmod(0o755)
    mp.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    mp.setenv("FAKE_SSH_LOG", str(log))
    return log


def _validation_meta(task_id: str, candidate: str, **extra) -> dict:
    meta = {"task_id": task_id, "title": f"{task_id} 固定候选验证", "project": "lybra", "context_bundle": "t", "task_mode": "validation",
            "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False,
            "output_target": f"task_cards/{task_id}/artifacts/", "artifact_policy": "formal_write",
            "validation_source_task": SRC, "validation_candidate": candidate}
    meta.update(extra)
    return {k: v for k, v in meta.items() if v is not None}


def _draft_fm(result: dict) -> dict:
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    fm, _b, _w = parse_markdown_frontmatter(result["rendered_markdown"])
    return fm


@pytest.fixture
def vrig(rig, monkeypatch, tmp_path):  # noqa: F811
    """F95 拉起靶场 + 执行方式声明(validation/config/docs → pi 执行工位 EXEC; code → 子 agent SUB)+ 来源卡候选。"""
    extra = f95.setup_launch(rig, monkeypatch)
    for k, v in vars(extra).items():
        setattr(rig, k, v)
    f95._land(rig.gov, EXEC, "executor", rig.ws_exec)
    f95._land(rig.gov, AUDITOR, "auditor", rig.ws_audit)
    _land_subagent(rig.gov)
    view = _declare_execution(rig.gov, ["validation", "config", "docs"])
    assert view.get("ok", True) is not False, view
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    for mode in ("validation", "config", "code"):
        _write(rig.gov / "5_tasks" / "policies" / f"{POLICY_V}_{mode}.md", build_autonomy_policy_markdown(
            policy_id=f"{POLICY_V}_{mode}", agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
            max_tasks=20, owner_approval_ref="fixture-envelope-f196", task_selector_task_mode=mode, launch_harnesses=["pi"]))
    sha, _tree = f78._branch_with_commit(rig.code_repo, SRC, rel="app/feature.txt")
    f78._card(rig.gov, SRC, "completed", extra={"harness": "claude-code"})
    rig.candidate = sha
    rig.ssh_log = _fake_bin(tmp_path, monkeypatch)
    rig.mac = tmp_path / "mac" / "Users" / "kiwi" / "lybra"
    rig.mac.mkdir(parents=True)
    rig.base = rig.mac / "scratch"
    yield rig
    leftover = f95._pgrep_fake()
    assert leftover == "", f"残留假 harness 进程:\n{leftover}"


# ===========================================================================
# 件① + 件②: 起草缺省 / 发卡判据(validation 缺省 pi, code 仍子 agent; 来源卡与候选必填)
# ===========================================================================

def test_item1_item2_draft_defaults_and_candidate_refusals(vrig):
    from tools.aipos_cli.draft_writer import create_draft

    gov = vrig.gov
    v = create_draft(gov, _validation_meta(VAL, vrig.candidate), "## Goal\n\n固定候选验证\n", dry_run=True)
    fv = _draft_fm(v)
    _show(f"[件②·validation 起草缺省] verdict={v['verdict']} harness={fv.get('harness')} assigned_to={fv.get('assigned_to')} "
          f"audit_by={fv.get('audit_by')} task_mode={fv.get('task_mode')} validation_candidate={fv.get('validation_candidate')}")
    assert v["verdict"] != "BLOCK", v["blocking_reasons"]
    assert (fv["harness"], fv["assigned_to"], fv["audit_by"]) == ("pi", EXEC, AUDITOR)
    c = create_draft(gov, {**_validation_meta(CODE, vrig.candidate), "task_mode": "code", "output_target": "app/"}, "## Goal\n\nx\n",
                     dry_run=True)
    fc = _draft_fm(c)
    _show(f"[件②·code 起草缺省] verdict={c['verdict']} harness={fc.get('harness')} assigned_to={fc.get('assigned_to')}")
    assert (fc["harness"], fc["assigned_to"]) == ("claude-code", SUB)
    k = create_draft(gov, {**_validation_meta(CFG, vrig.candidate), "task_mode": "config", "output_target": "app/"}, "## Goal\n\nx\n",
                     dry_run=True)
    fk = _draft_fm(k)
    _show(f"[件②·config 起草缺省] verdict={k['verdict']} harness={fk.get('harness')} assigned_to={fk.get('assigned_to')}")
    assert (fk["harness"], fk["assigned_to"]) == ("pi", EXEC)

    cases = {
        "缺候选": _validation_meta("PROBE-F196-V2", vrig.candidate, validation_candidate=None),
        "候选非 40 位": _validation_meta("PROBE-F196-V3", vrig.candidate[:12]),
        "候选不在仓": _validation_meta("PROBE-F196-V4", "f" * 40),
        "来源卡不在队列": _validation_meta("PROBE-F196-V5", vrig.candidate, validation_source_task="PROBE-NOPE-9"),
    }
    expect = {"缺候选": "VALIDATION_SUBJECT_MISSING", "候选非 40 位": "VALIDATION_CANDIDATE_INVALID",
              "候选不在仓": "VALIDATION_CANDIDATE_INVALID", "来源卡不在队列": "VALIDATION_SOURCE_UNKNOWN"}
    for name, meta in cases.items():
        res = create_draft(gov, meta, "## Goal\n\nx\n", dry_run=True)
        hit = [r for r in res["blocking_reasons"] if "AIPOS-F196" in r]
        _show(f"[件①·起草拒 {name}] verdict={res['verdict']} {hit[0] if hit else res['blocking_reasons']}")
        assert res["verdict"] == "BLOCK" and hit and expect[name] in hit[0]


def test_item1_declarations_single_source():
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]
    entry = next(v for v in enums["values"] if v["value"] == "validation")
    _show(f"[件①·声明] validation = {json.dumps(entry, ensure_ascii=False)}")
    _show(f"[件①·声明] routing_rules.validation = {enums['routing_rules']['validation']}")
    assert entry["writes_product_repo"] is False and entry["independent_audit"] is True and entry["subject"] == "candidate"
    assert {v["value"]: v["independent_audit"] for v in enums["values"]} == {
        "code": True, "docs": False, "governance": False, "config": False, "audit": False, "validation": True}
    assert nr.task_mode_independent_audit("validation") and nr.task_mode_independent_audit("code")
    assert not nr.task_mode_independent_audit("config") and not nr.task_mode_independent_audit("") and not nr.task_mode_independent_audit("nope")
    card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
    assert card["intent_face"]["harness"]["default_by_task_mode"]["validation"] == "pi"
    # 禁写死: 推导核 / 门 next_action / 发卡核审计者不再以 task_mode == "code" 判独立审计
    for rel in ("tools/aipos_cli/next_resolver.py", "tools/mcp_server/tools.py", "tools/aipos_cli/draft_writer.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "task_mode_independent_audit(" in text, rel
    assert 'task_mode == "code" or audit_required' not in (REPO_ROOT / "tools/aipos_cli/next_resolver.py").read_text(encoding="utf-8")


# ===========================================================================
# 件③: 目标主机 ssh 声明 + 验证根对 dev 实例 / 目标主机 host 的 declare / run-start / init-command
# ===========================================================================

def _validation_card(r: SimpleNamespace, queue: str = "pending", **extra) -> Path:
    fields = {"harness": "pi", "audit_by": AUDITOR, "validation_source_task": SRC, "validation_candidate": r.candidate,
              "output_target": f"task_cards/{VAL}/artifacts/"}
    fields.update(extra)
    return f78._card(r.gov, VAL, queue, assigned=EXEC, task_mode="validation", extra=fields)


def _vr(gov: Path, action: str, *argv: str) -> tuple[int, str, str]:
    return _cli("project", "validation-root", action, "--workspace-root", gov, *argv)


def _declare_vr(r: SimpleNamespace) -> str:
    rc, out, err = _vr(r.gov, "declare", "--host", HOST, "--base", r.base, "--task-id", VAL, "--instance", EXEC, "--owner-ref", OWNER_REF,
                       "--host-fingerprint", _fp(), "--actor", DRIVER, "--confirm")
    _show(f"---- 件③ validation-root declare(dev 上 pi 执行实例 {EXEC}, host={HOST})exit {rc} ----\n{out}{err}")
    assert rc == 0, out + err
    return f"vr-{VAL.lower()}-executor-1"


def test_item3_set_host_declare_run_start_init_command(vrig):
    gov = vrig.gov
    _validation_card(vrig)
    init_governance_repo(gov)
    rc, out, err = _vr(gov, "set-host", "--host", HOST, "--ssh-alias", ALIAS, "--owner-ref", OWNER_REF, "--actor", DRIVER)
    _show(f"---- 件③ set-host 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "validation_roots" in out and ALIAS in out
    for bad, why in ((("--host", socket.gethostname().split(".")[0].lower(), "--ssh-alias", ALIAS), "本机"),
                     (("--host", HOST, "--ssh-alias=-oProxyCommand=x"), "ssh 别名")):
        rc_bad, out_bad, err_bad = _vr(gov, "set-host", *bad, "--owner-ref", OWNER_REF, "--actor", DRIVER)
        _show(f"[件③ set-host 拒({why})] exit {rc_bad}: {err_bad.strip()}")
        assert rc_bad == 1 and "VR_HOST_ALIAS_INVALID" in err_bad
    rc, out, err = _vr(gov, "set-host", "--host", HOST, "--ssh-alias", ALIAS, "--owner-ref", OWNER_REF, "--actor", DRIVER, "--confirm")
    assert rc == 0, out + err
    decl = _declare_vr(vrig)
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", vrig.code_repo and f78._git(vrig.code_repo, "rev-parse", "main"),
                       "--actor", DRIVER, "--confirm")
    _show(f"[件① run-start 候选 ≠ 卡面候选] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_CANDIDATE_INVALID" in err and vrig.candidate in err
    rc, out, err = _vr(gov, "run-start", "--id", decl, "--candidate", vrig.candidate, "--actor", DRIVER, "--confirm")
    _show(f"---- 件③ run-start exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"经 ssh {ALIAS} 到目标主机 {HOST}" in out and f"| ssh {ALIAS} 'python3 - init-run " in out
    rc, out, err = _vr(gov, "init-command", "--id", decl, "--instance", EXEC)
    _show(f"---- 件③ init-command exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"| ssh {ALIAS} 'python3 - init-run --base {vrig.base} --run-dir {vrig.base}/{VAL}/run-1 " in out
    rc, out, err = _vr(gov, "list")
    _show(f"---- 件③ list ----\n{out}")
    assert f"目标主机 {HOST}: ssh 别名 {ALIAS}" in out
    # 目标主机侧: 按产品铸的 ssh 命令跑(假 ssh 就地执行, 管道送 helper)= 建出 run 目录链
    command = out and next(ln for ln in _vr(gov, "init-command", "--id", decl)[1].splitlines() if "init-run" in ln)
    proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=120)
    _show(f"[件③ 经 ssh init-run] exit {proc.returncode}: {proc.stdout.strip().splitlines()[-1]}\n  ssh 记录: {vrig.ssh_log.read_text().strip()}")
    assert proc.returncode == 0 and proc.stdout.strip().splitlines()[-1].startswith("INIT_OK")
    assert (vrig.base / VAL / "run-1").is_dir() and stat.S_IMODE(os.lstat(vrig.base / VAL / "run-1").st_mode) == 0o700
    assert vrig.ssh_log.read_text().startswith(f"{ALIAS}\tpython3 - init-run ")
    # 未声明 ssh 别名的主机 / 执行实例在目标主机本机(子 agent 形)= 照 F190-F194 渲染(行为不变)
    from tools.aipos_cli import validation_root as vr

    entry = next(e for e in vr.project_validation_roots(gov) if e["id"] == decl)
    assert vr.ssh_alias_for(gov, entry) == ALIAS
    # 验证卡不写产品仓源码: 产品仓写判定拒(CARD_NO_WORKTREE, 点名验证根与报告落点); 验证根本 run 目录写判定放行(治理根侧字面判定)
    from tools.aipos_cli.write_boundary import check_access

    denied = check_access(gov, role="executor", path=vrig.code_repo / "app" / "feature.txt", level="mutate", task_id=VAL)
    _show(f"[件① 产品仓写判定] {denied['code']}: {denied['reason']}")
    assert denied["allowed"] is False and denied["code"] == "CARD_NO_WORKTREE" and "固定候选" in denied["reason"]
    rc_wb, out_wb, _err = _cli("roles", "--workspace-root", gov, "write-boundary", "--instance", EXEC, "--task-id", VAL, "--host", HOST,
                               "--run-id", "run-1", "--level", "mutate", "--check", f"{vrig.base}/{VAL}/run-1/screenshot.png")
    _show(f"[件③ 验证根写判定] exit {rc_wb}: {out_wb.strip()}")
    assert rc_wb == 0 and "ALLOW" in out_wb
    rc_cr, out_cr, err_cr = _cli("--workspace-root", gov, "card", "render", "--task-id", VAL, "--harness", "pi")
    _show(f"[件① card render(pi)] exit {rc_cr}\n{out_cr}{err_cr}")
    assert rc_cr == 0 and "不建(交付主体 = 来源卡的固定候选" in out_cr
    assert vr.ssh_alias_for(gov, {**entry, "host": "other-mac"}) is None
    assert vr.ssh_alias_for(gov, {**entry, "instance": SUB}) is None


# ===========================================================================
# 件①②③④: loop 对 validation 卡自动拉起 pi(假 harness)→ 经 ssh 写验证根 → 交回 → 独立审计 → N5 无合入 → 结案
# ===========================================================================

def _real_finalize(gov: Path, code_repo: Path, argv: list[str]) -> subprocess.CompletedProcess:
    """靶场 finalize = 产品真实 CLI(验证卡走无合入早出口, 不碰产品仓 git); 其余卡沿用 F90 替身。"""
    task_id = argv[argv.index("--task-id") + 1]
    if task_id != VAL:
        return f90_original_finalize(gov, code_repo, argv)
    rc, out, err = _cli(*argv[1:])
    _show(f"---- N5 真实 finalize({' '.join(argv[:6])} …)exit {rc} ----\n{out}{err}")
    return subprocess.CompletedProcess(argv, rc, out, err)


f90_original_finalize = f90._finalize_double


def test_item1_to_item4_loop_launches_pi_validates_over_ssh_audits_no_merge(vrig, monkeypatch):
    gov, code = vrig.gov, vrig.code_repo
    monkeypatch.setattr(f90, "_finalize_double", _real_finalize)
    _validation_card(vrig)
    init_governance_repo(gov)
    assert _vr(gov, "set-host", "--host", HOST, "--ssh-alias", ALIAS, "--owner-ref", OWNER_REF, "--actor", DRIVER, "--confirm")[0] == 0
    decl = _declare_vr(vrig)
    assert _vr(gov, "run-start", "--id", decl, "--candidate", vrig.candidate, "--actor", DRIVER, "--confirm")[0] == 0
    monkeypatch.setenv("FAKE_EXEC_MODE", "validate")
    monkeypatch.setenv("FAKE_AUDIT_MODE", "work")
    main_before = f78._git(code, "rev-parse", "main")
    out = io.StringIO()
    res = run_loop(VAL, gov, actor=DRIVER, policy_id=f"{POLICY_V}_validation", out=out, interval=0.05, max_wait=90, max_steps=40)
    text = out.getvalue()
    _show("---- 件①-④ lybra loop 输出原文(validation 卡)----\n" + text + "------------------------------")
    assert res.exit_code == 0 and res.outcome == "completed", text
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [VAL, f"{VAL}R"], launches
    assert launches[0]["workstation"] == str(vrig.ws_exec)
    kickoff = (vrig.log / f"{VAL}.kickoff").read_text(encoding="utf-8")
    _show("---- 件② 拉起 pi 的开工提示原文(validation 卡)----\n" + kickoff + "\n------------------------------")
    assert "工作树: 不建(交付主体 = 来源卡的固定候选" in kickoff and "工作树路径" not in kickoff
    assert f"- commit_sha: {vrig.candidate}(" in kickoff and f"- branch: card/{SRC}(" in kickoff
    assert f"ssh 别名 = {ALIAS}" in kickoff and "只写本 run 验证根" in kickoff and "禁 sudo" in kickoff and "禁改 ssh 配置" in kickoff
    assert f"| ssh {ALIAS} 'python3 - init-run " in kickoff and f"| ssh {ALIAS} 'python3 - check --run-dir " in kickoff
    assert f"| ssh {ALIAS} 'python3 - sim-create " in kickoff
    audit_kickoff = (vrig.log / f"{VAL}R.kickoff").read_text(encoding="utf-8")
    _show("---- 件① 审计 pi 开工提示原文(validation 审计卡)----\n" + audit_kickoff + "\n------------------------------")
    assert "工作树: 不建" in audit_kickoff and f"- commit_sha: {vrig.candidate}(" in audit_kickoff
    # 不写产品仓: 无卡工作树 / 无卡分支 / main 不动
    assert not (code / ".worktrees" / VAL).exists() and not (code / ".worktrees" / f"{VAL}R").exists()
    assert subprocess.run(["git", "-C", str(code), "rev-parse", "--verify", "--quiet", f"card/{VAL}"], capture_output=True).returncode != 0
    assert f78._git(code, "rev-parse", "main") == main_before
    # 验证根: 经 ssh 写入本 run 目录; 截图进报告落点 artifacts/
    shot = vrig.base / VAL / "run-1" / "screenshot.png"
    assert shot.read_text() == f"fake-png-{VAL}"
    assert (gov / "task_cards" / VAL / "artifacts" / "screenshot.png").is_file()
    ssh_log = vrig.ssh_log.read_text(encoding="utf-8")
    _show(f"[件③ 假 ssh 记录(别名\\t远端命令)]\n{ssh_log}")
    assert all(line.startswith(f"{ALIAS}\t") for line in ssh_log.strip().splitlines())
    assert "python3 - init-run" in ssh_log and "python3 - check --run-dir" in ssh_log and "printf 'fake-png" in ssh_log
    # 独立审计 + 裁决绑候选 + N5 无合入 + 结案
    verdict = f90._records(gov, "audit_verdicts", VAL)
    vfm = nr._read_frontmatter(verdict[-1])
    _show(f"[件① 裁决记录] verdict={vfm.get('verdict')} artifact_subject={vfm.get('artifact_subject')}")
    assert vfm.get("verdict") == "PASS" and (vfm.get("artifact_subject") or {}).get("commit_sha") == vrig.candidate
    fin = nr._read_frontmatter(f90._records(gov, "finalizations", VAL)[0])
    _show(f"[件① N5 finalization 记录] merge_commit={fin.get('merge_commit')} deploy_status={fin.get('deploy_status')} "
          f"push_status={fin.get('push_status')} reason={fin.get('deploy_status_reason')}")
    assert fin["merge_commit"] == vrig.candidate and fin["deploy_status"] == "not_applicable" and fin["push_status"] == "not_applicable"
    assert "N5 无合入" in fin["deploy_status_reason"]
    assert f90._records(gov, "closures", VAL) and (gov / "5_tasks" / "queue" / "completed" / f"{VAL.lower()}.md").is_file()
    assert "N5 真实 finalize" not in text  # finalize 输出走 _show, 不混入 loop 原文


# ===========================================================================
# 件①④: config 卡 = 顾问复核(不派独立审计); code 卡 = 子 agent(loop 不拉起)
# ===========================================================================

def test_item1_config_card_advisor_review_and_code_card_subagent(vrig, monkeypatch):
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract, plan_launch

    gov, code = vrig.gov, vrig.code_repo
    f78._card(gov, CFG, "pending", assigned=EXEC, task_mode="config",
              extra={"harness": "pi", "audit": "", "audit_by": "", "lane": {"repo": str(code), "paths": ["tests/"]}})
    f78._card(gov, CODE, "pending", assigned=SUB, task_mode="code", extra={"harness": "claude-code"})
    init_governance_repo(gov)
    out = io.StringIO()
    res = run_loop(CFG, gov, actor=DRIVER, policy_id=f"{POLICY_V}_config", out=out, interval=0.05, max_wait=60, max_steps=20)
    text = out.getvalue()
    _show("---- 件① config 卡 lybra loop 输出原文 ----\n" + text + "------------------------------")
    launches = [s.launch for s in res.steps if s.launch and s.launch.get("launched")]
    assert [l["card"] for l in launches] == [CFG]
    assert res.outcome == "not_derivable" and "非代码卡(task_mode=config):顾问审核后直接提交裁决" in res.message
    assert not f90._records(gov, "audit_dispatches", f"{CFG}R") and not (gov / "5_tasks" / "queue" / "pending" / f"{CFG.lower()}r.md").exists()
    plan = plan_launch(gov, CODE, policy=None, no_launch=False, decl=launch_declaration(load_loop_contract()), already_launched=set())
    _show(f"[件④ code 卡拉起判定] refusal={plan.refusal}\n  等待提示: {plan.manual_hint}")
    assert "子 agent" in plan.refusal and "lybra kickoff" in plan.manual_hint and "/go" not in plan.manual_hint


# ===========================================================================
# 件②: 手写工位章程(「禁止 Lybra 分发器覆盖」)sync 拒覆盖; --adopt-hand-charter --owner-ref 正式解除(备份保留)
# ===========================================================================

HAND = ("# 角色：hbj-coder — 慧泊金项目执行工位（文件制冷启动）\n\n"
        "<!-- 手工维护：本章程属 chris-huibojin 项目，禁止 Lybra 分发器按角色类别覆盖（2026-09-08 工位维护侧修复） -->\n\n手写章程正文。\n")


def test_item2_hand_maintained_charter_refused_then_adopted_with_backup(vrig, monkeypatch, tmp_path):
    from test_aipos_f82_workstation_intake import RepoGate

    from tools.aipos_cli import confirm_client
    from tools.aipos_cli import distribution_sync as ds

    ws = f95._workstation(vrig.home, vrig.gov, "probe-hand", "executor", EXEC)
    (ws / "AGENTS.md").write_text(HAND, encoding="utf-8")
    RepoGate.roles = {f95.ws_token("executor"): ("executor", None)}
    monkeypatch.setattr(confirm_client, "GateClient", RepoGate)
    before = (ws / "AGENTS.md").read_bytes()
    for argv, label in ((["--dry-run"], "dry-run"), ([], "sync")):
        rc, out, err = _cli("sync", "--harness-root", ws, "--workspace-root", vrig.gov, *argv)
        _show(f"---- 件② 手写章程 sync {label} exit {rc} ----\n{out}{err}")
        assert rc == 1 and "CHARTER_HAND_MAINTAINED" in out and "--adopt-hand-charter --owner-ref" in out
        assert (ws / "AGENTS.md").read_bytes() == before
    fresh = ds.charter_freshness(ws, vrig.gov)
    _show(f"[件② loop 拉起前章程核] fresh={fresh['fresh']} state={fresh['state']} detail={fresh['detail'][:200]}")
    assert fresh["fresh"] is None and "CHARTER_HAND_MAINTAINED" in fresh["detail"]
    rc, out, err = _cli("sync", "--harness-root", ws, "--workspace-root", vrig.gov, "--adopt-hand-charter")
    _show(f"[件② 缺 --owner-ref] exit {rc}: {err.strip()}")
    assert rc == 1 and "--owner-ref" in err
    rc, out, err = _cli("sync", "--harness-root", ws, "--workspace-root", vrig.gov, "--adopt-hand-charter", "--owner-ref", OWNER_REF, "--dry-run")
    _show(f"---- 件② 采纳预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "would-backup(手写章程采纳" in out and (ws / "AGENTS.md").read_bytes() == before
    assert not list(ws.glob("AGENTS.md.hand-maintained-*.bak"))
    rc, out, err = _cli("sync", "--harness-root", ws, "--workspace-root", vrig.gov, "--adopt-hand-charter", "--owner-ref", OWNER_REF)
    _show(f"---- 件② 采纳 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "backed-up(手写章程采纳" in out
    backups = list(ws.glob("AGENTS.md.hand-maintained-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    log = [json.loads(ln) for ln in (ws / ".lybra" / "charter-adoptions.jsonl").read_text(encoding="utf-8").splitlines()]
    _show(f"[件② 采纳记录] {log}")
    assert log[0]["owner_ref"] == OWNER_REF and log[0]["backup"] == str(backups[0])
    rendered = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "lybra:charter-render" in rendered and "禁止 Lybra 分发器" not in rendered
    after = ds.charter_freshness(ws, vrig.gov)
    assert after["fresh"] is True, after
    rc, out, err = _cli("sync", "--harness-root", vrig.home / "workstations", "--workspace-root", vrig.gov, "--adopt-hand-charter",
                        "--owner-ref", OWNER_REF, "--dry-run")
    _show(f"[件② 父根批量采纳拒] exit {rc}: {err.strip()}")
    assert rc == 1 and "只对单工位" in err


# ===========================================================================
# 件②⑤: chris 形接入预演(临时 chris 形治理根; 禁写真实 chris)— set-execution 预演 + pi 工位接入步骤
# ===========================================================================

def test_item2_chris_shape_set_execution_dry_run(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import _append_enrollment_trail, subagent_land_detail

    project = "chris-demo"
    gov = f78._make_gov(tmp_path, monkeypatch, shape="chris", project=project)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    coder, auditor, sub = f"hbj-coder.{project}.devbox", f"hbj-auditor.{project}.devbox", f"exec.{project}.advmac"
    _append_enrollment_trail(gov, action="land", code_id="e1", role="executor", instance=sub, by="(agent-enroll)",
                             reason=subagent_land_detail(host="advmac", harness="codex", mode="subagent", files=["connection.json"]))
    _append_enrollment_trail(gov, action="land", code_id="e2", role="auditor", instance=auditor, by="(agent-enroll)",
                             reason=f"host={socket.gethostname()} workstation={home / 'kiwiai-pi' / 'hbj-auditor'} files=['connection.json']")
    base = ["project", "set-execution", "--workspace-root", gov, "--subagent-executor", sub, "--auditor", auditor,
            "--pi-executor", coder, "--pi-allowed-task-mode", "validation", "--pi-allowed-task-mode", "config",
            "--pi-allowed-task-mode", "docs", "--dry-run"]
    before = (gov / "project.json").read_text(encoding="utf-8")
    rc, out, err = _cli(*base)
    _show(f"---- 件⑤ chris 形 set-execution 预演(hbj-coder 未按产品接入)exit {rc} ----\n{out}{err}")
    assert rc == 1 and "EXECUTION_INSTANCE_NOT_ENROLLED" in (out + err) and coder in (out + err)
    _append_enrollment_trail(gov, action="land", code_id="e3", role="executor", instance=coder, by="(agent-enroll)",
                             reason=f"host={socket.gethostname()} workstation={home / 'kiwiai-pi' / 'hbj-coder'} files=['connection.json']")
    rc, out, err = _cli(*base)
    _show(f"---- 件⑤ chris 形 set-execution 预演(接入后)exit {rc} ----\n{out}{err}")
    assert rc == 0 and '"validation"' in out and coder in out
    assert (gov / "project.json").read_text(encoding="utf-8") == before, "预演零写入"
