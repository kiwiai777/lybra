"""AIPOS-F261 — 执行分工收尾(11-01 lybra pi 执行工位上岗实测)。

件① 接入预览提示「目标工位已被别的实例登记」: 工位模式接入(enroll-deliver 预演与确认、roles enroll 换码前)读本项目接入登记
    (判定唯一 enrollment.workstation_occupants, 经 workstation_location 同一读取口), 目标目录已是另一实例的登记工位 = 输出逐个列出
    占用实例 / 角色 / 登记时间 + 换目录出口 + 子 agent 重登记命令(话术 verbs.schema roles_writers.workstation_occupancy); 不拦截。
件② `lybra project set-execution` 核子 agent 执行者登记模式: --subagent-executor 实例最新登记不是子 agent 模式 = 预演即拒
    EXECUTION_SUBAGENT_MODE_MISMATCH + 重登记出口(enrollment.subagent_reenroll_command); 子 agent 模式实例照常通过; 已声明的存量项目
    不变红: roles list / project upgrade 预演把它列为待处理项(同一出口, 不自动改)。

靶场: 临时 home + 临时治理根 + 临时产品仓; 件① 用本检出起的临时门子进程(构件唯一来源 F151 靶场 gate_range); 存量项目用 F176 人肉期
治理根靶场 urig(子 agent 执行者按工位形登记)。不触生产门 / 真实治理根 / 真实工位; 注册码与凭据只断言「不出现」。
"""
from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  — land 事件写法唯一来源(_append_enrollment_trail)
from test_aipos_f143_execution_mode import AUDITOR, SUB, _registry  # noqa: E402  — 接入登记靶场唯一来源
from test_aipos_f151_roles_two_phase_enroll_deliver import CODE_RE, PREVIEW, _cli, gate_range  # noqa: E402,F401  — 临时门靶场(夹具复用)
from test_aipos_f176_project_upgrade import EXE, PROJECT, _upgrade, urig  # noqa: E402,F401  — 人肉期存量治理根靶场(夹具复用)

OLD = "exec.proj-r2.f261old"     # 先以工位模式登记于目录 D 的实例(10-11 形态: exec 登记于 kiwiai-pi/lybra-executor)
NEW = "exec.proj-r2.f261new"     # 对同一目录 D 接入的新实例(11-01 形态: ops 接入同一目录)
WS_EXEC = "exec.lybra.f261ws"    # 件②: 工位模式登记的执行者(被误作子 agent 执行者)
MISMATCH = "EXECUTION_SUBAGENT_MODE_MISMATCH"


def _show(text: str) -> None:
    print(CODE_RE.sub("LYBRAENROLL1.<redacted>", text), flush=True)


def _texts() -> dict:
    from tools.aipos_cli.enrollment import workstation_occupancy_declaration

    return workstation_occupancy_declaration()["texts"]


def _deliver(r, instance: str, ws: Path, *extra: str) -> tuple[int, str, str]:
    return _cli("roles", "--workspace-root", str(r.project), "enroll-deliver", "--role", "executor", "--instance", instance,
                "--governance-root", str(r.project), "--workspace", str(ws), "--gate-url", r.gate_url, "--ttl", "3600",
                "--owner-authorization-ref", "pol_r2_exec_1", *extra)


# ===========================================================================
# 件① 接入预览 / 确认带「目标工位已被别的实例登记」
# ===========================================================================

def test_item1_enroll_deliver_preview_and_confirm_name_the_registered_occupant(gate_range):
    from tools.aipos_cli.enrollment import enrollment_trail_path, location_event

    r = gate_range
    d = r.tmp / "stations" / "shared-ws"
    d.mkdir(parents=True)
    f95._land(r.project, OLD, "executor", d)  # OLD 以工位模式登记于 D(land 事件 workstation=D)
    landed_at = location_event(r.project, OLD)["ts"]
    _show(f"[件①·靶场] {OLD} 最新 land: {[ln for ln in enrollment_trail_path(r.project).read_text(encoding='utf-8').splitlines() if OLD in ln][-1]}")
    texts = _texts()
    occupied = texts["occupied"].format(dir=d.resolve(), instance=OLD, role="executor", landed_at=landed_at)
    rc, out, err = _deliver(r, NEW, d)
    _show(f"[件①·enroll-deliver 预演(D 已被 {OLD} 登记)] rc={rc}\n{out}{err}")
    assert rc == 0 and f"roles enroll-deliver executor: {PREVIEW}" in out, out + err
    assert f"⚠ {occupied}" in out
    assert f"⚠ {texts['exit_move'].format(instance=OLD)}" in out
    reenroll = (f"lybra roles enroll-deliver --role executor --instance {OLD} --executor-mode subagent --harness <顾问会话 kind: "
                f"claude-code|codex> --governance-root {r.project.resolve()} --workspace {r.project.resolve()} "
                "--owner-authorization-ref <Owner 授权引用> --dry-run")
    assert f"若 {OLD} 是子 agent 执行者: 先按子 agent 模式重登记 {OLD}: {reenroll}" in out
    assert not (d / ".lybra").exists(), "预演零写入"
    rc, out, err = _deliver(r, NEW, d, "--json")
    payload = json.loads(out)
    assert [o["instance"] for o in payload["workstation_occupants"]] == [OLD] and payload["occupancy_notice"][0] == occupied
    # D 未被登记 = 无该行
    free = r.tmp / "stations" / "free-ws"
    rc, out, err = _deliver(r, NEW, free)
    _show(f"[件①·enroll-deliver 预演(目录未被登记)] rc={rc}\n{out}{err}")
    assert rc == 0 and "已被实例" not in out and "⚠" not in out
    assert json.loads(_deliver(r, NEW, free, "--json")[1])["workstation_occupants"] == []
    # 确认: 不拦截(Owner 可能就是要换人), 确认输出同带该行
    rc, out, err = _deliver(r, NEW, d, "--confirm")
    _show(f"[件①·enroll-deliver --confirm(D 已被 {OLD} 登记)] rc={rc}\n{out}{err}")
    assert rc == 0 and "已执行" in out and f"⚠ {occupied}" in out, out + err
    assert not CODE_RE.search(out + err), "注册码不出现在输出"
    assert json.loads((d / ".lybra" / "role").read_text(encoding="utf-8"))["instance"] == NEW
    # 同一实例对自己已登记的目录重接入不提示自己; OLD 仍在 D 登记 = 照提示
    rc, out, _err = _deliver(r, NEW, d)
    assert rc == 0 and f"已被实例 {NEW} " not in out and f"已被实例 {OLD} " in out


def test_item1_roles_enroll_names_the_registered_occupant(gate_range):
    """roles enroll(工位模式, 换码前读本机治理根登记)= 输出同带该行; 子 agent 模式(凭据落治理根)不判。"""
    r = gate_range
    d = r.tmp / "stations" / "shared-ws2"
    d.mkdir(parents=True)
    f95._land(r.project, OLD, "executor", d)
    rc, out, err = _cli("roles", "--workspace-root", str(r.project), "enroll-code", "--role", "executor", "--instance", NEW,
                        "--governance-root", str(r.project), "--gate-url", r.gate_url, "--ttl", "3600",
                        "--owner-authorization-ref", "pol_r2_exec_1", "--confirm", "--json")
    assert rc == 0, err
    code = json.loads(out)["self_contained_code"]
    rc, out, err = _cli("roles", "enroll", "--code", code, "--workspace", str(d))
    _show(f"[件①·roles enroll(D 已被 {OLD} 登记)] rc={rc}\n{out}{err}")
    assert rc == 0 and "Enrollment successful" in out, out + err
    assert f"目标工位 {d.resolve()} 已被实例 {OLD} 登记(角色 executor" in out and f"先按子 agent 模式重登记 {OLD}" in out


def test_item1_occupancy_judgement_host_dir_and_voided(tmp_path, monkeypatch):
    """判定单元: 同目录同机 = 占用; 他机同路径 / 不同目录 / 子 agent 模式登记(无 workstation=)不算; 作废后的 land 不再定位。"""
    from tools.aipos_cli.enrollment import subagent_land_detail, workstation_occupants

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    d = tmp_path / "ws-d"
    d.mkdir()
    f95._land(gov, "exec.lybra.a", "executor", d)
    f95._land(gov, "exec.lybra.remote", "executor", d, host="far-away-host")
    f95._land(gov, "exec.lybra.other", "executor", tmp_path / "ws-other")
    from tools.aipos_cli.enrollment import _append_enrollment_trail

    _append_enrollment_trail(gov, action="land", code_id="enroll_fixture_sub", role="executor", instance="exec.lybra.sub",
                             by="(agent-enroll)", reason=subagent_land_detail(host="advhost", harness="claude-code", mode="subagent",
                                                                             files=["connection.json"]))
    got = workstation_occupants(gov, d, instance="exec.lybra.new")
    _show(f"[件①·判定] 本机 {d}: {[o['instance'] for o in got]}; 他机 far-away-host:{d}: "
          f"{[o['instance'] for o in workstation_occupants(gov, d, instance='x', host='kiwi@far-away-host')]}")
    assert [o["instance"] for o in got] == ["exec.lybra.a"] and got[0]["role"] == "executor"
    assert [o["instance"] for o in workstation_occupants(gov, d, instance="x", host="kiwi@far-away-host")] == ["exec.lybra.remote"]
    assert workstation_occupants(gov, d, instance="exec.lybra.a") == []  # 自己不算
    assert workstation_occupants(gov, tmp_path / "ws-free", instance="x") == []
    # a 重登记为子 agent 模式(最新位置事件无 workstation=)= 不再占用
    _append_enrollment_trail(gov, action="land", code_id="enroll_fixture_a2", role="executor", instance="exec.lybra.a",
                             by="(agent-enroll)", reason=subagent_land_detail(host="advhost", harness="claude-code", mode="subagent",
                                                                             files=["connection.json"]))
    assert workstation_occupants(gov, d, instance="exec.lybra.new") == []


# ===========================================================================
# 件② set-execution 核子 agent 执行者登记模式
# ===========================================================================

def test_item2_set_execution_refuses_workstation_mode_subagent_executor(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import _append_enrollment_trail

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    _registry(gov)  # SUB = 子 agent 模式; AUDITOR / PIX = 工位
    ws = tmp_path / "kiwiai-pi" / "exec-ws"
    f95._land(gov, WS_EXEC, "executor", ws)  # 工位模式登记(09-24 形态: 无 harness / mode)
    before = (gov / "project.json").read_text(encoding="utf-8")
    rc, out, err = _cli("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", WS_EXEC, "--auditor", AUDITOR)
    _show(f"[件②·工位模式实例作 --subagent-executor 预演] rc={rc}\n{out}{err}")
    assert rc == 1 and MISMATCH in err and (gov / "project.json").read_text(encoding="utf-8") == before
    assert f"subagent_executor={WS_EXEC}: 最新登记不是子 agent 模式(工位模式登记于 {ws}" in err
    assert (f"lybra roles enroll-deliver --role executor --instance {WS_EXEC} --executor-mode subagent --harness <顾问会话 kind: "
            f"claude-code|codex> --governance-root {gov.resolve()} --workspace {gov.resolve()}") in err
    rc, out, err = _cli("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", SUB, "--auditor", AUDITOR)
    _show(f"[件②·子 agent 模式实例预演] rc={rc}\n{out}{err}")
    assert rc == 0 and "预览" in out and f"execution.subagent_executor = '{SUB}'" in out
    # 按出口重登记为子 agent 模式(land 事件 mode=subagent)后同一命令通过; 重登记命令 harness 取登记记下的顾问会话 kind
    from tools.aipos_cli.enrollment import subagent_land_detail, subagent_reenroll_command

    _append_enrollment_trail(gov, action="land", code_id="enroll_fixture_re", role="executor", instance=WS_EXEC, by="(agent-enroll)",
                             reason=subagent_land_detail(host="advhost", harness="codex", mode="subagent", files=["connection.json"]))
    rc, out, err = _cli("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", WS_EXEC, "--auditor", AUDITOR)
    _show(f"[件②·重登记后预演] rc={rc}\n{out}{err}")
    assert rc == 0 and MISMATCH not in err
    assert "--harness codex " in subagent_reenroll_command(gov, WS_EXEC, role="executor", harness="codex")
    # --pi-executor / --auditor 仍要求工位位置(子 agent 模式实例作审计者 = 拒, 拒因不变)
    rc, _out, err = _cli("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", SUB, "--auditor", WS_EXEC)
    assert rc == 1 and "EXECUTION_INSTANCE_NOT_ENROLLED" in err and "无工位位置" in err


def test_item2_stored_project_lists_pending_item_not_red(urig):
    """存量: F176 人肉期治理根的子 agent 执行者 EXE 按工位形登记(land workstation=)——roles list / upgrade 预演列待处理项与同一出口,
    upgrade 该步状态不因此变(不自动改, project.json 不动); 按出口重登记后待处理项消失。"""
    from tools.aipos_cli.enrollment import subagent_land_detail, subagent_reenroll_command
    from tools.aipos_cli.execution_division import division_summary

    gov = urig["gov"]
    before = (gov / "project.json").read_bytes()
    command = subagent_reenroll_command(gov, EXE, role="executor")
    rc, out, err = _upgrade(urig)
    _show(f"[件②·存量 upgrade 预演] rc={rc}\n{out}{err}")
    step = re.search(r"^\[\d+/\d+\] 执行分工迁移\(非代码一律 pi\): (\S+)", out, re.M)
    assert rc == 0 and step and step.group(1) == "已是最新", out
    assert f"待处理(不自动改): 子 agent 执行者 {EXE} 登记为工位模式(工位模式登记于" in out and command in out
    rc, out, err = _cli("roles", "--workspace-root", str(gov), "list")
    _show(f"[件②·存量 roles list] rc={rc}\n" + "\n".join(ln for ln in out.splitlines() if "执行分工" in ln or "待处理" in ln))
    assert f"! 待处理(不自动改): 子 agent 执行者 {EXE} 登记为工位模式" in out and command in out
    assert (gov / "project.json").read_bytes() == before, "只列不改"
    from tools.aipos_cli.enrollment import _append_enrollment_trail

    _append_enrollment_trail(gov, action="land", code_id="enroll_fixture_re", role="executor", instance=EXE, by="(agent-enroll)",
                             reason=subagent_land_detail(host=socket.gethostname(), harness="claude-code", mode="subagent",
                                                         files=["connection.json"]))
    assert division_summary(gov)["subagent_mode_pending"] is None
    rc, out, _err = _upgrade(urig)
    assert rc == 0 and "待处理(不自动改): 子 agent 执行者" not in out
