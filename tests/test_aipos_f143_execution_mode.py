"""AIPOS-F143 — 执行方式缺省落地(chris HBJOTA-44 实撞, CONVERGENCE §3e)。

件① loop / next --run 派生 lybra 子命令不依赖调用方 PATH(唯一构造 workstation_wiring.lybra_cli_invocation, 经
    next_resolver._run_product_command); 子进程失败(异常 / 非零退出)真因原文进 loop 输出与运行记录 end_message, 不再吞成「claim 失败: 1」。
件② project.json execution 声明(config.schema project_json.execution; 唯一读取口 workspace_config.project_execution, 唯一写入口
    `lybra project set-execution` = enrollment.set_project_execution 经 update_project_json); 起草缺省读声明(draft_writer.apply_execution_defaults):
    子 agent(缺省)/ pi(Owner 明示的 task_mode 且卡稿显式要 pi)/ 审计恒为声明的独立 pi。
件③ 发卡核身份(draft_writer.publish_identity_refusals): assigned_to / 审计者须为本项目已接入实例(判定唯一实现 enrollment.instance_enrollment:
    land 事件 / 治理根连接文件绑定), harness=pi 执行者与审计者须有工位位置; 拒因列已接入实例与 set-execution 出口。
件④ 子 agent 执行者正式接入: `lybra roles enroll --executor-mode subagent`(判据声明 roles.schema executor_modes)凭据落治理根连接文件、
    land 记 mode=subagent harness=<顾问会话 kind>; 工位类不带此模式落治理根照旧拒; 向导 `onboarding guide --executor-mode subagent`。

靶场: 临时治理根 / 临时产品仓 / 临时 HOME; loop 全链复用 F90 靶场(进程内真门 + 派生命令进程内执行); 无真实凭据、不连真实门、不起 pi。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + loop 全链靶场(禁第二份)
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f85_onboarding_guide_generic import SHELL_VERBS, _iter_command_lines, _parse_lybra, _segments  # noqa: E402
from test_aipos_f90_loop_one_stage import POLICY, TASK, rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.token_resolver import token_fingerprint  # noqa: E402

SUB = "exec.lybra.advhost"      # 子 agent 执行者(主机段 = 顾问会话所在机)
PIX = "exec.lybra.piws"         # pi 执行工位
GHOST_EXEC = "exec.lybra.kiwiai-mac-ghost"
GHOST_AUDIT = "audit.lybra.kiwiai-mac-ghost"
NO_PATH = "/usr/bin:/bin"       # 不含 lybra 的 PATH(断言 which lybra 为空)


def _show(text: str) -> None:
    print(text, flush=True)


def _land(gov: Path, instance: str, role: str, reason: str) -> None:
    """接入日志 land 行(产品唯一渲染 _trail_line + 唯一写口 _write_trail_line)。"""
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path

    _write_trail_line(enrollment_trail_path(gov), _trail_line(action="land", code_id="enroll_fixture", role=role, instance=instance,
                                                              project="lybra", by="(agent-enroll)", reason=reason))


def _registry(gov: Path) -> None:
    """本项目已接入: 子 agent 执行者 SUB(mode=subagent harness=claude-code, 无工位)、审计工位 AUDITOR、pi 执行工位 PIX(有工位)。"""
    from tools.aipos_cli.enrollment import subagent_land_detail

    _land(gov, SUB, "executor", subagent_land_detail(host="advhost", harness="claude-code", mode="subagent", files=["connection.json"]))
    _land(gov, AUDITOR, "auditor", f"host={socket.gethostname()} workstation={gov.parent / 'ws-audit'} files=['connection.json']")
    _land(gov, PIX, "executor", f"host={socket.gethostname()} workstation={gov.parent / 'ws-pi'} files=['connection.json']")


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


def _meta(task_id: str, **extra) -> dict:
    meta = {"task_id": task_id, "title": f"{task_id} t", "project": "lybra", "context_bundle": "t", "task_mode": "code",
            "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False,
            "output_target": "tools/aipos_cli/, tests/", "artifact_policy": "formal_write"}
    meta.update(extra)
    return {k: v for k, v in meta.items() if v is not None}


def _declare(gov: Path, **kw) -> dict:
    from tools.aipos_cli.enrollment import set_project_execution

    return set_project_execution(gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=kw.get("pi_executor", PIX),
                                 pi_allowed_task_modes=kw.get("modes", ["config"]), dry_run=False)


# ===========================================================================
# 件① 派生子命令不依赖 PATH + 真因透传
# ===========================================================================

def test_item1_spawn_needs_no_lybra_on_path(monkeypatch):
    """真子进程: PATH 不含 lybra 时旧派生法(按 PATH 找 lybra)即 FileNotFoundError; 产品派生(同一解释器 + 本代码树)照常执行。"""
    monkeypatch.setenv("PATH", NO_PATH)
    assert shutil.which("lybra") is None
    with pytest.raises(FileNotFoundError) as old:
        subprocess.run(["lybra", "loop", "--help"], capture_output=True, text=True)
    _show(f"[件①·旧派生(PATH={NO_PATH})] {type(old.value).__name__}: {old.value}")
    res = nr._run_product_command("lybra loop --help", "claim")
    _show(f"[件①·产品派生] ok={res['ok']} exit={res['exit_code']} message={res['message']}\n  output 首行: {res['output'].splitlines()[0]}")
    assert res["ok"] and res["exit_code"] == 0 and "--task-id" in res["output"]
    from tools.aipos_cli.workstation_wiring import lybra_cli_invocation

    argv, env = lybra_cli_invocation(["loop", "--help"], {"PATH": NO_PATH, "PYTHONPATH": "/x"})
    assert argv[:3] == [sys.executable, "-m", "tools.aipos_cli"] and env["PYTHONPATH"].split(os.pathsep) == [str(REPO_ROOT), "/x"]
    assert env["PATH"] == NO_PATH  # 调用方环境原样下传, 只前置 PYTHONPATH


def test_item1_loop_claims_with_path_lacking_lybra(rig, monkeypatch):
    """loop 认领(派生 queue claim 子命令)在 PATH 不含 lybra 时成功: 派生 argv = 同一解释器 -m tools.aipos_cli, PYTHONPATH 前置本代码树。"""
    monkeypatch.setenv("PATH", NO_PATH)
    assert shutil.which("lybra") is None
    seen: list[tuple[list, str]] = []
    rig_run = subprocess.run  # = F90 靶场进程内执行(认得产品派生 argv)

    def spy(argv, *a, **kw):
        if isinstance(argv, list) and argv and argv[0] == sys.executable:
            seen.append((list(argv[:5]), str((kw.get("env") or {}).get("PYTHONPATH") or "")))
        return rig_run(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    f90._card(rig.gov, TASK, "pending")
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=lambda args, expect_ready: 3, interval=0.05, max_wait=1)
    _show(out.getvalue())
    claim = res.steps[0]
    assert claim.action_type == "claim" and claim.ok, claim.message
    assert seen and seen[0][0][:4] == [sys.executable, "-m", "tools.aipos_cli", "queue"], seen
    assert seen[0][1].split(os.pathsep)[0] == str(REPO_ROOT)
    claimer = nr._claimer_instance(nr._read_task_records(rig.gov, TASK))  # claim 记录认领实例(推导核唯一读取口)
    _show(f"[件①·claim 记录] {f90._records(rig.gov, 'claims', TASK)[0].name} agent_instance={claimer}")
    assert claimer == EXEC


def _run_record_end(res) -> str:
    fm, _b, _w = parse_markdown_frontmatter(Path(res.run_record).read_text(encoding="utf-8"))
    return str(fm.get("end_message") or "")


def test_item1_spawn_exception_cause_reaches_output_and_run_record(rig, monkeypatch, tmp_path):
    """派生子进程起不来(FileNotFoundError)= loop exit 2, 输出首行与运行记录 end_message 带异常原文(原: 「claim 失败: 1」)。"""
    from tools.aipos_cli import workstation_wiring as ww

    missing = tmp_path / "no-such-interpreter"
    monkeypatch.setattr(ww, "lybra_cli_invocation", lambda args, env=None: ([str(missing), "-m", ww.LYBRA_CLI_MODULE, *args], dict(env or {})))
    f90._card(rig.gov, TASK, "pending")
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=lambda args, expect_ready: 3, interval=0.05, max_wait=1)
    _show(out.getvalue())
    end = _run_record_end(res)
    _show(f"[件①·运行记录 end_reason={res.reason}] end_message={end}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "gate_rejected")
    first = res.message.splitlines()[0]
    assert "claim 失败(exit 1): FileNotFoundError: [Errno 2] No such file or directory" in first and str(missing) in first
    assert "FileNotFoundError: [Errno 2]" in end and res.reason == "gate_rejected"
    assert "claim 失败: 1" not in out.getvalue()


def test_item1_spawn_nonzero_exit_stderr_cause_passed_through(rig, monkeypatch, tmp_path):
    """派生子进程非零退出: stderr 关键行(异常行)原文进 message 首行与运行记录(原: 「claim 失败: 3」)。"""
    from tools.aipos_cli import workstation_wiring as ww

    script = tmp_path / "failing_cli.py"
    script.write_text("import sys\nprint('Traceback (most recent call last):', file=sys.stderr)\n"
                      "print('ValueError: gate said no (fixture)', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8")
    monkeypatch.setattr(ww, "lybra_cli_invocation", lambda args, env=None: ([sys.executable, str(script), *args], dict(env or {})))
    f90._card(rig.gov, TASK, "pending")
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=lambda args, expect_ready: 3, interval=0.05, max_wait=1)
    _show(out.getvalue())
    end = _run_record_end(res)
    _show(f"[件①·运行记录] end_message={end}")
    assert res.message.splitlines()[0].endswith("claim 失败(exit 3): ValueError: gate said no (fixture)")
    assert "ValueError: gate said no (fixture)" in end and "[stderr]" in res.message


# ===========================================================================
# 件② 执行方式声明 + 起草缺省
# ===========================================================================

def test_item2_set_execution_two_phase_and_refusals(tmp_path, monkeypatch):
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    _registry(gov)
    before = (gov / "project.json").read_text(encoding="utf-8")
    base = ("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", SUB, "--auditor", AUDITOR)
    rc, out, err = _cli(*base, "--pi-executor", PIX, "--pi-allowed-task-mode", "config")
    _show(f"[件②·set-execution 预演 rc={rc}]\n{out}{err}")
    assert rc == 0 and "预览" in out and '"execution"' in out and (gov / "project.json").read_text(encoding="utf-8") == before
    rc, out, err = _cli(*base, "--pi-executor", PIX, "--pi-allowed-task-mode", "config", "--confirm")
    _show(f"[件②·set-execution 确认 rc={rc}]\n{out}{err}")
    assert rc == 0 and "已写入" in out
    from tools.aipos_cli.workspace_config import project_execution

    view = project_execution(gov)
    _show(f"[件②·读取口 project_execution] {json.dumps(view, ensure_ascii=False)}")
    assert view == {"default_mode": "subagent", "subagent_executor": SUB, "pi_allowed_task_modes": ["config"], "pi_executor": PIX,
                    "auditor": AUDITOR}
    after = (gov / "project.json").read_text(encoding="utf-8")
    for argv, code in (((*base[:-1], GHOST_AUDIT), "EXECUTION_INSTANCE_NOT_ENROLLED"),
                       ((*base[:-1], SUB), "EXECUTION_DECLARATION_INVALID"),
                       ((*base, "--pi-allowed-task-mode", "bogus"), "EXECUTION_DECLARATION_INVALID"),
                       ((*base, "--pi-allowed-task-mode", "config"), "EXECUTION_DECLARATION_INVALID")):
        rc, out, err = _cli(*argv, "--confirm")
        _show(f"[件②·拒 {code} rc={rc}] {err.strip()}")
        assert rc == 1 and code in err and (gov / "project.json").read_text(encoding="utf-8") == after
    # 审计者已接入但无工位位置(子 agent 模式实例)= 拒
    rc, _out, err = _cli("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", PIX, "--auditor", SUB, "--confirm")
    _show(f"[件②·审计者无工位拒] {err.strip()}")
    assert rc == 1 and "无工位位置" in err and "本项目已接入实例" in err


def test_item2_draft_defaults_follow_declaration_three_states(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    _registry(gov)
    _declare(gov)

    def fm_of(created: dict) -> dict:
        return parse_markdown_frontmatter((gov / created["target_path"]).read_text(encoding="utf-8"))[0]

    # ① 缺省子 agent(代码卡, 卡稿不写执行者 / harness / 审计者)
    a = create_draft(gov, _meta("F143-SUB"), "## Goal\n\nx\n")
    fa = fm_of(a)
    _show(f"[件②·子 agent 缺省] verdict={a['verdict']} assigned_to={fa['assigned_to']} agent_instance={fa['agent_instance']} "
          f"harness={fa['harness']} audit_by={fa['audit_by']}")
    assert a["verdict"] != "BLOCK" and (fa["assigned_to"], fa["agent_instance"], fa["harness"], fa["audit_by"]) == (SUB, SUB, "claude-code", AUDITOR)
    # ② pi 例外: Owner 明示的 task_mode(config)且卡稿显式要 pi
    b = create_draft(gov, _meta("F143-PI", task_mode="config", harness="pi"), "## Goal\n\nx\n")
    fb = fm_of(b)
    _show(f"[件②·pi 例外] verdict={b['verdict']} assigned_to={fb['assigned_to']} harness={fb['harness']} audit_by={fb['audit_by']}")
    assert b["verdict"] != "BLOCK" and (fb["assigned_to"], fb["harness"], fb["audit_by"]) == (PIX, "pi", AUDITOR)
    # 未列明的 task_mode 要 pi = 拒(不静默改派); 未明示要 pi 的 config 卡 = 按缺省子 agent
    c = create_draft(gov, _meta("F143-PI-NO", harness="pi"), "## Goal\n\nx\n", dry_run=True)
    _show(f"[件②·pi 未列明拒] verdict={c['verdict']} {c['blocking_reasons'][0]}")
    assert c["verdict"] == "BLOCK" and "EXECUTION_PI_NOT_ALLOWED" in c["blocking_reasons"][0]
    d = create_draft(gov, _meta("F143-CFG", task_mode="config"), "## Goal\n\nx\n")
    fd = fm_of(d)
    _show(f"[件②·config 未明示 = 缺省] assigned_to={fd['assigned_to']} harness={fd['harness']}")
    assert (fd["assigned_to"], fd["harness"]) == (SUB, "claude-code")
    # 审计者恒为声明的独立 pi 审计工位(卡稿写了 audit_by 原样保留, 交发卡核身份判)
    e = create_draft(gov, _meta("F143-AUD", audit_by=AUDITOR), "## Goal\n\nx\n", dry_run=True)
    assert e["verdict"] != "BLOCK"


def test_item2_undeclared_project_unchanged(tmp_path, monkeypatch):
    """未声明 execution(存量项目)= 起草行为不变: 不补执行者 / 审计者, harness 照 default_by_task_mode。"""
    from tools.aipos_cli.draft_writer import create_draft

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    created = create_draft(gov, _meta("F143-OLD", assigned_to=EXEC), "## Goal\n\nx\n")
    fm = parse_markdown_frontmatter((gov / created["target_path"]).read_text(encoding="utf-8"))[0]
    _show(f"[件②·未声明] verdict={created['verdict']} assigned_to={fm['assigned_to']} harness={fm['harness']} audit_by={fm.get('audit_by')}")
    assert created["verdict"] != "BLOCK" and fm["harness"] == "pi" and "audit_by" not in fm and "agent_instance" not in fm
    missing = create_draft(gov, _meta("F143-OLD2"), "## Goal\n\nx\n", dry_run=True)
    assert missing["verdict"] == "BLOCK" and any("assigned_to" in r for r in missing["blocking_reasons"])


# ===========================================================================
# 件③ 发卡核身份
# ===========================================================================

def _publish(gov: Path, task_id: str, **extra) -> dict:
    from tools.aipos_cli.draft_writer import create_draft, publish_draft

    created = create_draft(gov, _meta(task_id, **extra), "## Goal\n\nx\n")
    assert created.get("wrote"), created
    return publish_draft(gov, created["target_path"], dry_run=True)


def test_item3_publish_refuses_unenrolled_identities(tmp_path, monkeypatch):
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    _registry(gov)
    ghost = _publish(gov, "F143-GHOST", assigned_to=GHOST_EXEC, harness="pi", audit_by=GHOST_AUDIT)
    _show("[件③·未接入执行者与审计者拒]\n  " + "\n  ".join(r for r in ghost["blocking_reasons"] if "F143" in r or "已接入实例" in r))
    reasons = "\n".join(ghost["blocking_reasons"])
    assert ghost["verdict"] == "BLOCK"
    assert f"assigned_to={GHOST_EXEC} 不是本项目已接入实例" in reasons and f"audit_by={GHOST_AUDIT} 不是本项目已接入实例" in reasons
    assert "本项目已接入实例: " in reasons and SUB in reasons and "lybra project set-execution" in reasons
    nows = _publish(gov, "F143-NOWS", assigned_to=SUB, harness="pi", audit_by=SUB)
    _show("[件③·无工位拒]\n  " + "\n  ".join(r for r in nows["blocking_reasons"] if "无工位位置" in r))
    assert sum("无工位位置" in r for r in nows["blocking_reasons"]) == 2  # pi 执行者 + 审计者(恒独立 pi)都须有工位
    ok = _publish(gov, "F143-OK", assigned_to=SUB, harness="claude-code", audit_by=AUDITOR)
    _show(f"[件③·已接入通过] verdict={ok['verdict']} blocking={ok['blocking_reasons']} identity={json.dumps(ok['identity_check'], ensure_ascii=False)}")
    assert ok["verdict"] != "BLOCK" and ok["identity_check"]["instances"]["assigned_to"]["enrolled"]
    # 缺 audit_by 的代码卡: 核派审时将用的实例(audit_derivation.resolve_audit_instance), 模板名未接入 = 拒
    noaud = _publish(gov, "F143-NOAUD", assigned_to=SUB, harness="claude-code")
    assert noaud["verdict"] == "BLOCK" and any("audit_by(缺省推导)" in r for r in noaud["blocking_reasons"])
    # 声明了执行方式: 派 pi 须在 pi_allowed_task_modes
    _declare(gov)
    from tools.aipos_cli.draft_writer import publish_identity_refusals

    refusals, _v = publish_identity_refusals(gov, _meta("F143-PICODE", assigned_to=PIX, harness="pi", audit_by=AUDITOR))
    _show(f"[件③·声明后 code 卡派 pi 拒] {refusals[0]}")
    assert "EXECUTION_PI_NOT_ALLOWED" in refusals[0]
    passed, _v = publish_identity_refusals(gov, _meta("F143-PICFG", task_mode="config", assigned_to=PIX, harness="pi", audit_by=AUDITOR))
    assert passed == []  # Owner 明示的 task_mode 派 pi 执行工位(有工位位置)= 通过


def test_item3_bare_root_without_registry_unchanged(tmp_path, monkeypatch):
    """无接入登记且未声明 execution 的裸治理根(夹具 / 未经产品接入)= 发卡核身份不适用, 行为不变。"""
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    res = _publish(gov, "F143-BARE", assigned_to=GHOST_EXEC, audit_by=GHOST_AUDIT)
    _show(f"[件③·裸治理根] verdict={res['verdict']} identity={res['identity_check']}")
    assert res["verdict"] != "BLOCK" and res["identity_check"]["applied"] is False


# ===========================================================================
# 件④ 子 agent 执行者正式接入
# ===========================================================================

def _issue(gate: Path, gov: Path, role: str, instance: str | None) -> str:
    from tools.aipos_cli.enrollment import issue_self_contained_code

    if not (gate / ".lybra" / "connection.json").is_file():
        _write(gate / ".lybra" / "connection.json", json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": []}))
    return issue_self_contained_code(gate, role=role, instance=instance, ttl_seconds=600, governance_root=str(gov), by="t")["self_contained_code"]


def _enroll(gate: Path, sc: str, workspace: Path, role: str, instance: str | None, token: str, **kw) -> dict:
    """兑换 = 门侧同一实现(mark_enrollment_used)、land = 门侧同一实现(land_enrollment, 写所属项目 enrollment_log); 只替换 HTTP 传输。"""
    from tools.aipos_cli.enroll_client import enroll
    from tools.aipos_cli.enrollment import decode_self_contained_code, land_enrollment, mark_enrollment_used

    inner = decode_self_contained_code(sc)["code"]
    entry = {"role": role, "agent_instance": instance, "fingerprint": token_fingerprint(token), "scopes": [role], "token": token,
             "projects": ["lybra"]}

    def exchange(_url, _code, _boot=None):
        mark_enrollment_used(gate, inner, token_entry=entry)
        return {"ok": True, "token_entry": entry}

    def land(_url, _code, *, transport_token=None, landed_detail=""):
        land_enrollment(gate, inner, landed_detail=landed_detail)
        return True

    with patch("tools.aipos_cli.enroll_client.exchange_enrollment_code", side_effect=exchange), \
            patch("tools.aipos_cli.enroll_client.land_enrollment_code", side_effect=land):
        return enroll(code=sc, gate_url="", workspace_root=workspace, **kw)


def test_item4_subagent_executor_enrolls_into_governance_root_and_loop_claims_as_it(rig, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft, publish_draft
    from tools.aipos_cli.enrollment import enrollment_trail_path, instance_enrollment

    gov, gate = rig.gov, rig.gov.parent / "gate"
    role_before = (gov / ".lybra" / "role").read_bytes()
    res = _enroll(gate, _issue(gate, gov, "executor", EXEC), gov, "executor", EXEC, "fixture-subagent-exec",
                  executor_mode="subagent", harness_kind="claude-code", harness_host="advhost")
    _show(f"[件④·enroll --executor-mode subagent] ok={res['ok']} executor_mode={res['executor_mode']} harness={res['subagent_harness']} "
          f"files={res['files_written']} landed={res['landed']} pi接线={res['wiring']}\n  next_step: {res['next_step']}")
    assert res["ok"] and res["executor_mode"] == "subagent" and res["wiring"] is None and res["harness_delivery"] is None
    conn = json.loads((gov / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    shown = [{k: (token_fingerprint(v) if k == "token" else v) for k, v in t.items() if k in ("role", "agent_instance", "token")}
             for t in conn["tokens"]]
    _show(f"[件④·治理根连接文件(token 只出指纹)] {json.dumps(shown, ensure_ascii=False)}")
    assert any(t.get("agent_instance") == EXEC and t.get("role") == "executor" for t in conn["tokens"])
    assert any(t.get("agent_instance") == DRIVER for t in conn["tokens"])  # 顾问驱动方条目原样
    assert (gov / ".lybra" / "role").read_bytes() == role_before and not (gov / ".pi").exists()
    land_line = [ln for ln in enrollment_trail_path(gov).read_text(encoding="utf-8").splitlines() if " land " in ln and EXEC in ln][-1]
    _show(f"[件④·land 事件] {land_line}")
    assert "mode=subagent harness=claude-code" in land_line and "host=advhost" in land_line and "workstation=" not in land_line
    view = instance_enrollment(gov, EXEC)
    _show(f"[件④·已接入判定] {json.dumps(view, ensure_ascii=False)}")
    assert view["enrolled"] and view["via"] == ["land", "connection"] and view["mode"] == "subagent" and not view["workstation"]["found"]
    # 审计工位(pi)已接入 → 声明执行方式 → 起草缺省 → 发卡核身份通过 → loop 以子 agent 执行者身份认领
    _land(gov, AUDITOR, "auditor", f"host={socket.gethostname()} workstation={gov.parent / 'ws-audit'} files=['connection.json']")
    rc, out, err = _cli("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", EXEC, "--auditor", AUDITOR, "--confirm")
    assert rc == 0, err
    created = create_draft(gov, _meta(TASK), "## Goal\n\nx\n")
    published = publish_draft(gov, created["target_path"])
    pub = parse_markdown_frontmatter((gov / published["target_path"]).read_text(encoding="utf-8"))[0]
    _show(f"[件④·发卡] verdict={published['verdict']} assigned_to={pub['assigned_to']} harness={pub['harness']} audit_by={pub['audit_by']}")
    assert published["verdict"] != "BLOCK" and (pub["assigned_to"], pub["harness"], pub["audit_by"]) == (EXEC, "claude-code", AUDITOR)
    loop_out = io.StringIO()
    run = run_loop(TASK, gov, actor=DRIVER, policy_id=POLICY, out=loop_out, watch=lambda args, expect_ready: 3, interval=0.05, max_wait=1)
    _show(loop_out.getvalue())
    assert run.steps[0].action_type == "claim" and run.steps[0].ok
    claim = nr._read_task_records(gov, TASK)["latest_claim"]
    _show(f"[件④·claim 记录] {f90._records(gov, 'claims', TASK)[0].name} agent_instance={nr._claimer_instance({'latest_claim': claim})}")
    assert nr._claimer_instance({"latest_claim": claim}) == EXEC


def test_item4_workstation_classes_still_refused_in_governance_root(tmp_path, monkeypatch):
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    gate = tmp_path / "gate"
    _write(gov / ".lybra" / "connection.json", json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": []}))
    conn_before = (gov / ".lybra" / "connection.json").read_bytes()
    cases = [
        ("auditor", "audit.lybra.advhost", {}, "拒绝落盘"),                                   # pi 工位类不带模式: 照旧拒
        ("executor", "exec.lybra.advhost", {}, "拒绝落盘"),
        ("auditor", "audit.lybra.advhost", {"executor_mode": "subagent", "harness_kind": "claude-code"}, "EXECUTOR_MODE_ROLE_CLASS"),
        ("executor", None, {"executor_mode": "subagent", "harness_kind": "claude-code"}, "EXECUTOR_MODE_INSTANCE_REQUIRED"),
        ("executor", "exec.lybra.advhost", {"executor_mode": "subagent"}, "EXECUTOR_MODE_HARNESS"),
    ]
    for role, inst, kw, code in cases:
        with pytest.raises(RuntimeError) as exc:
            _enroll(gate, _issue(gate, gov, role, inst), gov, role, inst, f"fixture-{role}", **kw)
        _show(f"[件④·{role} {kw or '无模式'} 落治理根] {str(exc.value).splitlines()[0]}")
        assert code in str(exc.value)
    with pytest.raises(RuntimeError) as exc:  # 子 agent 模式目标须为治理根
        _enroll(gate, _issue(gate, gov, "executor", "exec.lybra.advhost"), tmp_path / "some-workstation", "executor",
                "exec.lybra.advhost", "fixture-x", executor_mode="subagent", harness_kind="codex")
    _show(f"[件④·子 agent 模式目标非治理根] {str(exc.value).splitlines()[0]}")
    assert "EXECUTOR_MODE_TARGET" in str(exc.value)
    assert (gov / ".lybra" / "connection.json").read_bytes() == conn_before  # 拒 = 治理根凭据零写入
    from tools.aipos_cli.enrollment import enrollment_trail_path

    trail = enrollment_trail_path(gov)
    assert not trail.is_file() or " land " not in trail.read_text(encoding="utf-8")


BASE = dict(project_name="f143-probe", home_root="/tmp/f143-home", gate_url="http://127.0.0.1:7118", host_segment="devbox",
            workspace_dir="/tmp/f143-ws/exec", auditor_dir="/tmp/f143-ws/audit", owner_workspace="/tmp/f143-home/ops",
            code_repo="/tmp/f143-code/app")


def test_item4_onboarding_guide_subagent_mode():
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    default = generate_onboarding_guide(**BASE, advisor_dir="/tmp/f143-session")
    sub = generate_onboarding_guide(**BASE, advisor_harness="codex", advisor_host="mac-probe.local", executor_mode="subagent")
    s7, s9 = sub["steps"][6]["command"], sub["steps"][8]["command"]
    _show(f"[件④·向导 --executor-mode subagent 第 7 步]\n{s7}\n[第 9 步]\n{s9}")
    assert sub["instances"]["executor"] == "exec.f143-probe.mac-probe" and sub["executor_mode"] == "subagent"
    gov = sub["governance_root"]
    enroll_line = next(ln for ln in s7.splitlines() if "--executor-mode subagent" in ln)
    args = _parse_lybra(_segments(enroll_line)[0][1:], enroll_line)
    assert (args.workspace, args.executor_mode, args.harness, args.harness_host) == (gov, "subagent", "codex", "mac-probe.local")
    assert "/tmp/f143-ws/exec" not in json.dumps(sub["steps"][6:8], ensure_ascii=False)  # 无执行工位 enroll / sync / pi
    assert "project set-execution" in s9 and f"--subagent-executor {sub['instances']['executor']}" in s9
    n = 0
    for _, line in _iter_command_lines(sub):
        if line.startswith("/"):
            continue
        for seg in _segments(line):
            if seg[0] == "lybra":
                _parse_lybra(seg[1:], line)
                n += 1
            else:
                assert seg[0] in SHELL_VERBS, line
    _show(f"[件④·向导 lybra 命令全部可解析] {n} 条")
    # 缺省(workstation)= 既有 pi 执行工位步骤不变
    d7 = default["steps"][6]["command"]
    assert default["executor_mode"] == "workstation" and "--executor-mode" not in json.dumps(default["steps"], ensure_ascii=False)
    assert "lybra sync --harness-root /tmp/f143-ws/exec" in d7 and default["instances"]["executor"] == "exec.f143-probe.devbox"
