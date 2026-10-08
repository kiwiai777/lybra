"""AIPOS-F138 — 他机顾问取章程: `lybra charter` 拉取式输出渲染后的章程(件①), sync 对他机件给拉取出口(件①), 接入向导与顾问章程
给开局取章程命令(件②), `lybra loop status` 缺省不打印 [ENVELOPE_TRACE](件③), verbs.schema 探活指纹说明改为进程启动时刻(件④)。

靶场: 件①② 顾问 = AIPOS-F92 靶场 Range / probe_range(临时 HOME / 临时 home 根 / 自起临时门, 按向导原样走到第 5 步);
件① 执行工位与拒因 = AIPOS-F90 rig(tmp 治理根 + 进程内门; 工位与 land 事件用 F95 夹具同一 writer);
件③ = AIPOS-F95 lrig(授权拉起假 harness 走到 completed)。禁真门 / 真治理根 / 真工位 / 真 pi。

验收(卡面 ★验收):
 ① 临时治理根: claude-code / codex 顾问实例 `lybra charter` 输出含持续推进守则原文、未接入实例拒原文、输出无凭据断言
 ② sync 对他机件的 pull 出口原文
 ③ guide codex 顾问步骤含开局命令原文
 ④ loop status 缺省无 ENVELOPE_TRACE、--verbose 有原文
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import re
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f92_onboarding_walkthrough import CODE_RE, Range, probe_range  # noqa: E402,F401  — 靶场构件唯一来源
from test_aipos_f95_harness_launch import AUDIT_CLAIMER, POLICY_LAUNCH, _land, _workstation, lrig  # noqa: E402,F401
from test_aipos_f131_loop_run_status import _cli  # noqa: E402  — 进程内 CLI 读法唯一来源
from test_aipos_f136_advisor_follow_through import GUARD_TEXT, _run_guide_to_step5  # noqa: E402  — 守则原文 / 向导走法唯一来源
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

TASK = f90.TASK
VERB = "lybra_charter"
PLACEHOLDER = "<治理根主机>"


def _show(line: str) -> None:
    print(line, flush=True)


def _code(name: str) -> int:
    return declared_exit_code(VERB, name)


def _rc_sh(r: Range, cmd: str, *, cwd: Path) -> tuple[int, str, str]:
    """靶场子进程跑一条命令, 分开 stdout / stderr(章程只走 stdout)。"""
    import subprocess

    p = subprocess.run(["bash", "-c", cmd], cwd=str(cwd), env=r.env, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


def _secrets(gov: Path) -> list[str]:
    """靶场治理根 .lybra/connection.json 里的全部凭据值(夹具自造的临时凭据, 非真实工位)。"""
    conn = json.loads((gov / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    out = [str(t.get("token")) for t in conn.get("tokens") or [] if t.get("token")]
    assert out, "靶场 connection.json 应有凭据(否则无凭据断言无意义)"
    return out


def _assert_no_credentials(text: str, secrets: list[str]) -> None:
    for s in secrets:
        assert s not in text, "章程输出含凭据值"
    assert not CODE_RE.search(text), "章程输出含注册码"
    assert "fixture-ws-not-a-secret" not in text and "fixture-not-a-secret" not in text


def _assert_guard(text: str, project: str) -> None:
    for needle in GUARD_TEXT:
        assert needle in text, f"缺守则原文 {needle!r}"
    assert "<!-- lybra:charter-render" in text and "{{" not in text and f"`{project}` 项目的顾问" in text


# ===========================================================================
# ① claude-code 顾问: `lybra charter` 输出 == sync 落盘的章程(同一渲染器同一上下文), 含守则, 无凭据
# ===========================================================================

def test_item1_claude_code_advisor_charter_equals_synced_copy(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    guide, _ = _run_guide_to_step5(r, f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-dir {r.session} "
                                      f"--envelope-days 7 --max-tasks 20")
    inst = guide["instances"]["advisor"]
    synced = (r.session / ".claude" / "rules" / "lybra-advisor.md").read_text(encoding="utf-8")
    rc, out, err = _rc_sh(r, "lybra charter --role advisor", cwd=gov)  # 缺 --instance = 治理根 .lybra/role 的实例; cwd = 治理根
    _show(f"---- ① claude-code `cd <治理根> && lybra charter --role advisor` exit {rc}, stdout 前 8 行 ----\n"
          + "\n".join(out.splitlines()[:8]) + f"\n... (共 {len(out.splitlines())} 行) stderr={err.strip()!r}")
    assert rc == _code("ok") == 0 and err.strip() == ""
    _assert_guard(out, "lybra-probe")
    assert out == synced, "lybra charter 输出须与 sync 落盘的渲染物逐字节一致(同一 render_charter + charter_render_context)"
    rc2, out2, _ = _rc_sh(r, f"lybra charter --role advisor --instance {inst} --workspace-root {gov}", cwd=r.home)
    assert rc2 == 0 and out2 == out
    _assert_no_credentials(out, _secrets(gov))
    _show(f"[① 无凭据] 输出不含 connection.json 凭据值({len(_secrets(gov))} 个)/ 注册码 / 夹具凭据字面: OK")


# ===========================================================================
# ①②③ codex 他机顾问: guide 第 5 步开局命令 → sync pull 出口 → 照出口跑(模拟 ssh 落在治理根所在机)取章程
# ===========================================================================

def test_item123_remote_codex_advisor_pull_exit_and_charter(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    guide, enroll_out = _run_guide_to_step5(
        r, f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-harness codex --advisor-host mac-probe "
           f"--host-segment devbox --envelope-days 7 --max-tasks 20")
    inst = guide["instances"]["advisor"]
    assert inst == "advisor.lybra-probe.mac-probe"
    role = json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))
    assert role["harness"] == {"kind": "codex", "dir": None, "host": "mac-probe"}

    # ③ 向导第 5 步: 开局取章程命令(注释行 —— 在会话所在机每次开局跑, 不是本步现在跑的命令)
    step5 = guide["steps"][4]
    _show("---- ③ guide --advisor-harness codex 第 5 步 command 原文 ----\n" + step5["command"])
    expected = f"ssh {PLACEHOLDER} 'cd {gov} && lybra charter --role advisor --instance {inst}'"
    assert f"#   {expected}" in step5["command"] and step5["session_start"]["command"] == expected
    assert "lybra project set-workstation lybra-probe --instance advisor.lybra-probe.mac-probe" in step5["command"]
    text_guide = r.sh(f"lybra onboarding guide lybra-probe --repo app={r.session}/app --default-repo app --advisor-harness codex "
                      f"--advisor-host mac-probe --host-segment devbox --envelope-days 7 --max-tasks 20")
    assert expected in text_guide

    # ② sync: 章程件 = pull 出口(主机未声明 = 占位 + 声明出口), 零写入, 不再列 undelivered
    out = r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run")
    _show("---- ② sync --dry-run 原文(主机未声明) ----\n" + out)
    assert f"= pull: 在会话开局运行 `{expected}`" in out and "set-workstation" in out
    res = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))["workstations"][0]["result"]
    assert res["undelivered"] == [] and [p["distribution_id"] for p in res["pull"]] == ["advisor-charter-codex"]
    assert res["pull"][0]["command"] == expected and res["pull"][0]["host_declared"] is False and res["files_fetched"] == 0

    # 声明他机视角治理根主机(project.json workstations.<实例>.gate_ssh_alias, 既有两阶段写口)后出口带真实主机
    r.sh(f"lybra project set-workstation lybra-probe --instance {inst} --gate-ssh-alias dev-gate "
         f"--material-access '经 ssh dev-gate 读写治理根' --home-root {r.hroot} --confirm")
    out = r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov}")
    _show("---- ② sync 原文(主机已声明) ----\n" + out)
    declared = f"ssh dev-gate 'cd {gov} && lybra charter --role advisor --instance {inst}'"
    assert f"= pull: 在会话开局运行 `{declared}`" in out and "set-workstation" not in out
    assert not (gov / "AGENTS.md").exists() and not (gov / ".claude").exists()  # 他机会话: 本机零落盘(只给出口)

    # ① 照出口跑: ssh 落在治理根所在机执行的那段(本靶场同机, 直接以 bash 执行引号内命令)
    argv = shlex.split(declared)
    assert argv[:2] == ["ssh", "dev-gate"] and len(argv) == 3
    rc, charter, err = _rc_sh(r, argv[2], cwd=r.home)
    _show(f"---- ① codex 开局取章程 `{argv[2]}` exit {rc}, stdout 前 8 行 ----\n" + "\n".join(charter.splitlines()[:8]))
    assert rc == 0 and err.strip() == ""
    _assert_guard(charter, "lybra-probe")
    assert f"实例 `{inst}`" in charter and "他机会话开局先取本章程" in charter
    assert f"ssh <治理根主机> 'cd {gov} && lybra charter --role advisor --instance {inst}'" in charter  # 母本一句, 占位已渲染
    _assert_no_credentials(charter, _secrets(gov))
    _show("[① codex 无凭据] OK")

    # 未接入实例 / 角色类不符 / 不在治理根 = 拒原文
    for cmd, cwd, code, needle in (
        ("lybra charter --role advisor --instance advisor.lybra-probe.nowhere", gov, "refused", "未在本治理根接入"),
        ("lybra charter --role executor", gov, "refused", "≠ --role 'executor'"),
        ("lybra charter --role advisor", r.home, "unreadable", "不在任何已建治理根内"),
        ("lybra charter --role advisor --harness nope", gov, "usage", "invalid choice"),
    ):
        rc, so, se = _rc_sh(r, cmd, cwd=cwd)
        _show(f"[① 拒] (cwd={cwd.name}) {cmd} → exit {rc}: {se.strip()}")
        assert rc == _code(code) and needle in se and so == "", (rc, so, se)


# ===========================================================================
# ① 执行工位(pi)与拒因: 同一命令同一判序(rig 治理根; 工位与 land 事件用 F95 夹具同一 writer)
# ===========================================================================

def test_item1_executor_charter_and_refusals(rig, tmp_path, monkeypatch):
    from tools.aipos_cli import charter_render as cr

    ws = _workstation(tmp_path / "home", rig.gov, "exec", "executor", EXEC)
    rc, out, err = _cli(["charter", "--role", "executor", "--instance", EXEC, "--workspace-root", str(rig.gov)])
    _show(f"[① 执行实例未 land] exit {rc}: {err.strip()}")
    assert rc == _code("refused") and "未在本治理根接入" in err and out == ""
    _land(rig.gov, EXEC, "executor", ws)
    rc, out, err = _cli(["charter", "--role", "executor", "--instance", EXEC, "--workspace-root", str(rig.gov)])
    _show(f"[① 执行实例 pi 章程] exit {rc}, 首行: {out.splitlines()[0] if out else ''}")
    # stderr 只许有 rig 夹具 connection.json(无 token_ref)引起的注册表告警, 不许有拒因 / 错误
    assert all(l.startswith("Warning: ") for l in err.splitlines()), err
    assert rc == 0 and "<!-- lybra:charter-render" in out and f"实例 `{EXEC}`" in out and "{{" not in out
    assert "fixture-ws-not-a-secret" not in out
    master = (REPO_ROOT / "agents" / "roles" / "executor" / "AGENTS.md").read_text(encoding="utf-8")
    from tools.distribution_manifest import get_product_commit

    ctx = cr.charter_render_context(rig.gov, identity=cr.workstation_identity(ws), product_commit=get_product_commit())
    assert out == cr.render_charter(master, ctx)  # 同一渲染器同一上下文构造, 无第二份模板
    # 角色无章程(该 harness 无 kind=charter 条目)= 拒
    monkeypatch.setattr("tools.aipos_cli.workstation_wiring.declared_role_distributions", lambda role, role_class: [])
    with pytest.raises(cr.CharterRefused) as exc:
        cr.instance_charter(rig.gov, role_class="executor", instance=EXEC)
    _show(f"[① 角色无章程] {exc.value}")
    assert "无章程分发条目" in str(exc.value)
    # 工位 .lybra/role 已被别的实例接入 = 拒
    (ws / ".lybra" / "role").write_text(json.dumps({"role": "executor", "instance": "exec.lybra.other"}), encoding="utf-8")
    monkeypatch.undo()
    with pytest.raises(cr.CharterRefused) as exc:
        cr.instance_charter(rig.gov, role_class="executor", instance=EXEC)
    _show(f"[① 工位实例不符] {exc.value}")
    assert "≠ 'exec.lybra.test'" in str(exc.value)


def test_item1_pull_command_single_assembly_and_declarations():
    """拉取出口拼装唯一(charter_render.charter_pull_command); verb 与出口声明齐; argparse 参数 = verbs.schema parameters。"""
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.charter_render import charter_verb_contract, remote_session_delivery

    decl = remote_session_delivery()
    contract = charter_verb_contract()
    assert decl["mode"] == "pull" and decl["verb"] == VERB and decl["host_placeholder"] == PLACEHOLDER
    sub = next(a for a in build_parser()._actions if getattr(a, "choices", None) and "charter" in a.choices).choices["charter"]
    flags = {o for a in sub._actions for o in a.option_strings if o.startswith("--")} - {"--help"}
    assert flags == {"--" + k.replace("_", "-") for k in contract["parameters"]["properties"]}, flags
    for name in ("distribution_sync.py", "onboarding.py"):
        src = (REPO_ROOT / "tools" / "aipos_cli" / name).read_text(encoding="utf-8")
        assert "charter_pull_command(" in src and "lybra charter --role" not in src, name  # 消费方只调拼装口, 不自拼


# ===========================================================================
# ④(件③)loop status: 缺省不打印 [ENVELOPE_TRACE], --verbose 打印; 判定结果一致
# ===========================================================================

@contextlib.contextmanager
def _trace_sink():
    """把信封诊断 logger 的输出流临时换成内存缓冲(其余不变), 取它本会打到 stderr 的原文。"""
    handler = logging.getLogger("lybra.envelope").handlers[0]
    buf = io.StringIO()
    old = handler.setStream(buf)
    try:
        yield buf
    finally:
        handler.setStream(old)


def test_item4_loop_status_trace_only_with_verbose(lrig):
    from tools.aipos_cli.loop_driver import run_loop

    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    _land(lrig.gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=io.StringIO(), interval=0.05, max_wait=60,
                   max_steps=30)
    assert res.exit_code == 0 and res.outcome == "completed", res.message
    f90._card(lrig.gov, TASK.replace("-1", "-2"), "pending", harness="pi")  # 同 lane 下一张: 「下一张」扫描逐卡判信封
    argv = ["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)]
    with _trace_sink() as quiet:
        rc0, out0, err0 = _cli(argv)
    with _trace_sink() as loud:
        rc1, out1, err1 = _cli([*argv, "--verbose"])
    _show(f"---- ④ loop status(缺省)exit {rc0} 原文 ----\n{out0}{err0}[信封诊断输出 {len(quiet.getvalue().splitlines())} 行]")
    trace_lines = [l for l in loud.getvalue().splitlines() if l.startswith("[ENVELOPE_TRACE]")]
    _show(f"---- ④ loop status --verbose 信封诊断原文(共 {len(trace_lines)} 行, 前 2 行) ----\n" + "\n".join(trace_lines[:2]))
    assert rc0 == rc1 == 0 and out0 == out1  # 判定与状态结论不变, 只开关诊断输出
    assert "[ENVELOPE_TRACE]" not in quiet.getvalue() + out0 + err0
    assert trace_lines and all(json.loads(l.split(" ", 1)[1]).get("phase") for l in trace_lines)
    assert "顾问下一动作: card_done_take_next(completed)" in out0
    assert logging.getLogger("lybra.envelope").disabled is False  # 区段外恢复(门侧留痕不受影响)
    rc, out, _ = _cli(["loop", "status", "--help"])
    assert "--verbose" in out


# ===========================================================================
# ⑤(件④)verbs.schema 探活指纹说明 = 进程启动时刻(F137), 无「命令行指纹」残留
# ===========================================================================

def test_item5_verbs_schema_fingerprint_wording_matches_f137():
    text = (REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8")
    assert "命令行指纹" not in text and "命令行只落 sha1 指纹" not in text
    verbs = json.loads(text)["verbs"]
    rr = verbs["lybra_loop"]["run_record"]
    for blob in (rr["description"], rr["stall_note"], verbs["lybra_loop_status"]["description"]):
        assert "进程启动时刻指纹" in blob, blob[:120]
    assert "process_fingerprint" in rr["description"]
    assert "verbose" in verbs["lybra_loop_status"]["parameters"]["properties"]


def test_item2_master_has_single_pull_sentence():
    master = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    assert master.count("他机会话开局先取本章程") == 1 and master.count("lybra charter") == 1
    assert re.search(r"lybra charter --role \{\{role_class\}\} --instance \{\{instance\}\}", master)
