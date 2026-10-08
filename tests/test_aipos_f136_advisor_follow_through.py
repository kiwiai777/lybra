"""AIPOS-F136 — 顾问持续推进: `lybra loop status --wait` 有界等待并给出顾问下一动作(件①); 持续推进守则入顾问章程唯一文本源,
章程按顾问 harness(claude-code / codex)分发(件②); 顾问文档旧写法收口(件③)。

靶场: 件① = AIPOS-F131/F95 同一靶场(tmp 治理根 + 进程内门 + tmp 工位 + 假 harness tests/fake_harness.py; 禁真门/真治理根/真工位/真 pi);
件② = AIPOS-F92 靶场 Range / probe_range(临时 HOME / 临时 home 根 / 自起临时门, 按向导原样走到第 5 步)。

验收(卡面 ★验收):
 ① status --wait 四种下一动作原文: 运行中到时 continue_wait / 结束 card_done_take_next / 需决 owner_needed / stall investigate;
    改 loop_runs_root 后 --wait 仍可感知(等待中落点外运行记录出现即醒)
 ② 章程渲染与分发: claude-code 与 codex 顾问落点均收到含守则的章程原文(临时根); 会话目录已有非 Lybra 文件 = 拒不覆盖
 ③ 文档旧写法 grep 归零(写 project.json 的命令一律两阶段)+ 文档命令可解析
 ⑤ 不变量: 等待只经 run_fs_watch(本模块零 sleep 调用); 哨兵子树零写死; 空技能目录不回潮
"""
from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f84_advisor_skills_generic as f84  # noqa: E402  — 文档命令解析(占位替换 + argparse)唯一实现
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源
import test_aipos_f96_docs_retired_practice_ratchet as f96  # noqa: E402  — 文档扫描面唯一实现
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f92_onboarding_walkthrough import CODE_RE, Range, iso_home, probe_range  # noqa: E402,F401  — 靶场构件唯一来源
from test_aipos_f95_harness_launch import AUDIT_CLAIMER, POLICY_LAUNCH, _land, lrig  # noqa: E402,F401  — 拉起靶场
from test_aipos_f131_loop_run_status import _cli, _meta, _runs  # noqa: E402  — F131 夹具读法唯一来源
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code, declared_exit_codes  # noqa: E402

TASK = f90.TASK
VERB = "lybra_loop_status"


def _show(line: str) -> None:
    print(line, flush=True)


def _code(name: str) -> int:
    return declared_exit_code(VERB, name)


def _status(gov: Path, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov), *extra])
    return rc, out + err


# ===========================================================================
# ① 下一动作: 运行中 continue_wait → stall investigate → 进程被杀(结束)investigate
# ===========================================================================

def test_item1_wait_running_continue_wait_then_stalled_investigate(lrig, monkeypatch):
    from tools.aipos_cli.agent_watch_fs import run_fs_watch

    f90._card(lrig.gov, TASK, "pending", harness="pi")
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang")  # 吐事件后静默挂起
    seen: dict = {}

    def watch(args, expect_ready, stop_when=None, sleeper=None):
        if sleeper is not None and not seen:
            for _ in range(100):  # 有界: 经拉起进程的 pump 把已吐事件读进来并落盘
                sleeper(0.1)
                launch = (_meta(lrig.gov)["launches"] or [{}])[-1]
                if (launch.get("activity") or {}).get("last_activity_kind") == "tool_error:read":
                    break
            # 运行中 + --wait 到时 = continue_wait(CLI 原文, 退出码读声明)
            seen["continue"] = _status(lrig.gov, "--wait", "1")
            # 停滞: 同一判据 judge_run, 阈值取声明副本缩短(进程仍在且静默) → --wait 期间被判出即返回 investigate
            contract = copy.deepcopy(load_loop_contract())
            contract["run_record"]["stall_after_seconds"] = 1
            contract["run_record"]["stall_after_tool_seconds"] = 1
            seen["stalled"] = lrr.wait_for_next_action(lrig.gov, TASK, 60, contract=contract, interval=0.2)
            seen["stalled_text"] = lrr.render_status(seen["stalled"], lrr.run_record_declaration(contract)["states"])
            os.killpg(seen["stalled"]["runs"][0]["launch"]["pgid"], signal.SIGKILL)  # 杀掉假 harness 整组 → loop 早退收尾
        return run_fs_watch(args, expect_ready=expect_ready, stop_when=stop_when, sleeper=sleeper)

    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=30, max_steps=5,
                   watch=watch)
    rc, text = seen["continue"]
    _show(f"---- ① status --wait 1(运行中)原文 exit {rc} ----\n{text}")
    assert rc == _code("continue_wait") == 3, text
    assert "顾问下一动作: continue_wait(running)" in text and "--wait 1s: 到时仍在推进" in text
    stalled = seen["stalled"]
    _show(f"---- ① --wait(停滞)原文 ----\n{seen['stalled_text']}")
    assert stalled["runs"][0]["state"] == "stalled" and stalled["next_action"]["action"] == "investigate", stalled["next_action"]
    assert stalled["wait"]["outcome"] == "ready" and stalled["wait"]["waited_seconds"] < 60
    assert "顾问下一动作: investigate(stalled)" in seen["stalled_text"]
    assert stalled["next_action"]["hint"] == f"lybra next --task-id {TASK} --workspace-root {lrig.gov}"
    assert res.exit_code == 3 and res.outcome == "wait_timeout", res.message
    rc, ended = _status(lrig.gov, "--wait", "5")
    _show(f"---- ① status --wait 5(已结束: 拉起进程被杀)原文 exit {rc} ----\n{ended}")
    assert rc == _code("investigate") == 6 and "reason=launch_early_exit" in ended
    assert "顾问下一动作: investigate(launch_early_exit)" in ended


# ===========================================================================
# ① 结束 = card_done_take_next(授权拉起全链走到 completed)
# ===========================================================================

def test_item1_completed_card_done_take_next(lrig):
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    _land(lrig.gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=io.StringIO(), interval=0.05, max_wait=60,
                   max_steps=30)
    assert res.exit_code == 0 and res.outcome == "completed", res.message
    rc, text = _status(lrig.gov, "--wait", "30")
    _show(f"---- ① status --wait 30(已结案)原文 exit {rc} ----\n{text}")
    assert rc == _code("card_done_take_next") == 0
    assert "顾问下一动作: card_done_take_next(completed)" in text
    assert f"下一条: lybra next --workspace-root {lrig.gov}" in text  # F133「下一张」未上线: 声明 take_next_hint 降级为 lybra next
    rc, js, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov), "--wait", "30", "--json"])
    report = json.loads(js)
    assert rc == 0 and report["next_action"]["action"] == "card_done_take_next" and report["wait"]["outcome"] == "ready"
    assert report["runs"][0]["next_action"] == report["next_action"]


# ===========================================================================
# ① owner_needed: 授权(无信封)/ 手工模式等工位(运行中 manual_kickoff → 结束 wait_timeout)/ 连败
# ===========================================================================

def test_item1_owner_needed_authorization_no_envelope(rig):
    f90._card(rig.gov, TASK, "pending")
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id="pol_absent_1", out=io.StringIO(), interval=0.05, max_wait=1, max_steps=2)
    assert res.exit_code == 5
    rc, text = _status(rig.gov, "--wait", "10")
    _show(f"---- ① status --wait(无信封)原文 exit {rc} ----\n{text}")
    assert rc == _code("owner_needed") == 5
    assert "顾问下一动作: owner_needed(no_envelope) — 授权: 无有效信封" in text


def test_item1_manual_kickoff_continue_wait_then_owner_needed(rig):
    """手工模式: loop 在等工位时 = continue_wait(reason=manual_kickoff, 附开工提示, 顾问向 Owner 说一次后继续等);
    等到 loop 自己的 max_wait 结束 = owner_needed(wait_timeout: 工位开工)。"""
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    seen: dict = {}

    def watch(args, expect_ready, **_kw):
        seen["report"] = lrr.wait_for_next_action(rig.gov, TASK, 1, interval=0.2)
        return declared_exit_code("lybra_agent_watch", "timeout")

    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=io.StringIO(), interval=0.05, max_wait=1, max_steps=5,
                   watch=watch)
    na = seen["report"]["next_action"]
    _show(f"[① 手工模式运行中] {na}")
    assert na["action"] == "continue_wait" and na["reason"] == "manual_kickoff" and "/go" in na["detail"], na
    assert seen["report"]["runs"][0]["current_step"]["mode"] == "manual"
    assert res.outcome == "wait_timeout"
    rc, text = _status(rig.gov, "--wait", "5")
    _show(f"---- ① status --wait(手工模式等待超时结束)原文 exit {rc} ----\n{text}")
    assert rc == _code("owner_needed") and "owner_needed(wait_timeout) — 工位开工" in text


def test_item1_consecutive_failures_escalate_to_owner_needed(rig):
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)

    def watch(args, expect_ready, **_kw):
        raise RuntimeError("fixture crash in watch")

    with pytest.raises(RuntimeError):
        run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=io.StringIO(), interval=0.05, max_wait=1, max_steps=5,
                 watch=watch)
    first = lrr.loop_status(rig.gov, TASK)["next_action"]
    assert first["action"] == "investigate" and first["reason"] == "crashed", first  # 一次异常 = 查
    with pytest.raises(RuntimeError):
        run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=io.StringIO(), interval=0.05, max_wait=1, max_steps=5,
                 watch=watch)
    rc, text = _status(rig.gov, "--wait", "5")
    _show(f"---- ① status --wait(连败)原文 exit {rc} ----\n{text}")
    assert rc == _code("owner_needed") and "owner_needed(consecutive_failures) — 连败" in text and "连续 2 次" in text
    assert len(_runs(rig.gov)) == 2


# ===========================================================================
# ① 改 loop_runs_root 后 --wait 仍可感知(等待中声明落点下的运行记录出现即醒); 哨兵子树读声明
# ===========================================================================

def test_item1_declared_loop_runs_root_wait_senses_and_watch_subtrees(rig, tmp_path):
    from tools.aipos_cli.agent_watch_fs import _watch_subtrees, snapshot

    custom = tmp_path / "elsewhere" / "loop-runs"
    custom.mkdir(parents=True)
    project = json.loads((rig.gov / "project.json").read_text(encoding="utf-8"))
    project.setdefault("paths", {})["loop_runs_root"] = str(custom)
    (rig.gov / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    f90._card(rig.gov, TASK, "pending")
    subtrees = _watch_subtrees(rig.gov)
    _show(f"[① 哨兵子树(读声明)] {subtrees}")
    assert str(custom) in subtrees and len(subtrees) == 3

    box: dict = {}

    def waiter() -> None:
        try:
            box["report"] = lrr.wait_for_next_action(rig.gov, TASK, 60, interval=0.1)
        except BaseException as exc:  # noqa: BLE001 — 线程内异常原样带回主线程断言(不吞)
            box["error"] = exc

    th = threading.Thread(target=waiter)
    th.start()  # 先布防(此刻无任何运行记录), 再跑 loop
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id="pol_absent_1", out=io.StringIO(), interval=0.05, max_wait=1, max_steps=2)
    th.join(timeout=90)
    assert not th.is_alive() and "error" not in box, box.get("error")
    report = box["report"]
    _show(f"[① 声明落点外的运行记录 --wait] wait={report['wait']} next={report['next_action']}")
    assert res.exit_code == 5 and report["wait"]["watch_root"] == str(custom) and report["wait"]["outcome"] == "ready"
    assert report["wait"]["waited_seconds"] < 60 and report["next_action"]["action"] == "owner_needed"
    assert report["runs"][0]["record_path"].startswith(str(custom)) and not _runs(rig.gov)
    snap = snapshot(rig.gov)
    assert any(Path(os.path.normpath(rig.gov / rel)).is_relative_to(custom) for rel in snap), sorted(snap)[:5]
    rc, text = _status(rig.gov, "--wait", "1")
    assert rc == _code("owner_needed") and str(custom) in text


# ===========================================================================
# ① CLI 用法 / 无运行 / 判据表声明完整
# ===========================================================================

def test_item1_cli_usage_and_no_run(rig):
    max_seconds = lrr.status_contract()["wait"]["max_seconds"]
    for argv, why in ((["loop", "status", "--workspace-root", str(rig.gov), "--wait", "5"], "须同时给 --task-id"),
                      (["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--wait", "0"], "越界"),
                      (["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--wait", str(max_seconds + 1)], "越界")):
        rc, out, err = _cli(argv)
        _show(f"[① 用法错] {' '.join(argv[2:])} → exit {rc}: {err.strip()}")
        assert rc == _code("usage") == 2 and why in err, (out, err)
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--wait", "1"])
    assert rc == _code("no_run") == 4 and "无 loop 运行记录" in out, (out, err)
    rc, out, _ = _cli(["loop", "status", "--help"])
    assert all(f"{k}={c}" in out for k, c in declared_exit_codes(VERB).items()), out  # help 出口读声明


def test_item1_next_action_table_covers_declarations_and_reasons():
    contract = load_loop_contract()
    decl = lrr.run_record_declaration(contract)
    rules = lrr.next_action_declaration(decl)
    assert set(rules["by_end_reason"]) == set(decl["end_reasons"]) and set(rules["by_state"]) == set(decl["states"]) - {"ended"}
    gov = Path("/tmp/f136-gov")
    base = {"task_id": TASK, "run_id": "r", "state": "ended", "end_message": "门拒原文: lane 越界"}
    got = {r: lrr.next_action({**base, "end_reason": r}, rules, governance_root=gov)["action"] for r in decl["end_reasons"]}
    _show(f"[① 结束原因 → 下一动作] {got}")
    assert got["completed"] == "card_done_take_next" and got["gate_rejected"] == "owner_needed"
    gate = lrr.next_action({**base, "end_reason": "gate_rejected"}, rules, governance_root=gov)
    assert "门拒" in gate["detail"] and "lane 越界" in gate["detail"]
    for state in ("stalled", "launch_dead", "loop_dead", "unprobeable"):
        assert lrr.next_action({"task_id": TASK, "state": state}, rules, governance_root=gov)["action"] == "investigate"
    assert lrr.next_action({"task_id": TASK, "state": "running"}, rules, governance_root=gov)["action"] == "continue_wait"
    # 声明缺一条结束原因 = fail-closed(顾问拿不到下一动作, 宁拒不猜)
    from tools.schema_loader import SchemaLoadError

    broken = copy.deepcopy(lrr.status_contract())
    broken["next_action"]["by_end_reason"].pop("crashed")
    with pytest.raises(SchemaLoadError):
        lrr.next_action_declaration(decl, broken)
    with pytest.raises(lrr.LoopRunRecordError):
        lrr.next_action({**base, "end_reason": "not_declared_reason"}, rules, governance_root=gov)


# ===========================================================================
# ② 章程渲染与分发: claude-code 与 codex 顾问落点收到含守则的章程(隔离靶场, 向导原样走到第 5 步)
# ===========================================================================

GUARD_TEXT = ("持续推进守则", "lybra loop status --task-id <卡ID>", "只在 `owner_needed` 时停", "禁 `until` / `sleep`")


def _run_guide_to_step5(r: Range, guide_args: str, *, ok_step5: tuple[int, ...] = (0,)) -> tuple[dict, str]:
    guide = json.loads(r.sh(f"lybra onboarding guide {guide_args} --json"))
    fills: dict[str, str] = {}
    enroll_out = ""
    for step in guide["steps"][:5]:
        for line in (l.strip() for l in step["command"].splitlines()):
            if not line or line.startswith("#"):
                continue
            for k, v in fills.items():
                line = line.replace(k, v)
            out = r.sh(line, ok=ok_step5 if "roles enroll " in line else (0,))
            if "enroll-code" in line:
                fills["<ADVISOR_CODE>"] = CODE_RE.findall(out)[0]
            if "roles enroll " in line:
                enroll_out = CODE_RE.sub("LYBRAENROLL1.<redacted>", out)
                break  # 第 5 步其余行(sync 复核)由调用方按场景跑
    return guide, enroll_out


def _assert_rendered_charter(path: Path, project: str) -> str:
    text = path.read_text(encoding="utf-8")
    for needle in GUARD_TEXT:
        assert needle in text, f"{path} 缺 {needle!r}"
    assert "<!-- lybra:charter-render" in text and "{{" not in text and f"`{project}` 项目的顾问" in text
    return text


def test_item2_claude_code_advisor_session_receives_charter(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    guide, enroll_out = _run_guide_to_step5(r, f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-dir {r.session} "
                                               f"--envelope-days 7 --max-tasks 20")
    _show("[② claude-code 第 5 步 enroll 输出]\n" + enroll_out)
    assert ".claude/rules/lybra-advisor.md" in guide["steps"][4]["command"]
    charter = r.session / ".claude" / "rules" / "lybra-advisor.md"
    text = _assert_rendered_charter(charter, "lybra-probe")
    _show(f"[② claude-code 章程落点 {charter}] 前 6 行:\n" + "\n".join(text.splitlines()[:6]))
    assert (r.session / ".claude" / "skills" / "advisor-commands" / "SKILL.md").is_file()
    assert not (r.session / "AGENTS.md").exists() and not (gov / "AGENTS.md").exists()
    sync = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))
    res = sync["workstations"][0]["result"]
    assert res["plan"] == [] and res["harness"]["kind"] == "claude-code"  # 稳态: 章程三指纹已记本地清单
    manifest = json.loads((gov / ".lybra" / ".version-advisor").read_text(encoding="utf-8"))
    rec = {d["distribution_id"]: d for d in manifest["distributions"]}["advisor-charter-claude-code"]
    assert rec["rendered"] is True and rec["files"][0]["rendered_sha256"]


def test_item2_codex_advisor_session_receives_charter_and_refuses_foreign_file(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    session = r.home / "codex-session"
    session.mkdir()
    (session / "AGENTS.md").write_text("# 会话仓自带的 AGENTS.md(非 Lybra 渲染物)\n", encoding="utf-8")
    guide, enroll_out = _run_guide_to_step5(
        r, f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-harness codex --advisor-dir {session} "
           f"--host-segment devbox --envelope-days 7 --max-tasks 20", ok_step5=(2,))
    _show("[② codex 第 5 步 enroll 输出(会话目录已有非 Lybra AGENTS.md)]\n" + enroll_out)
    assert "章程落点已有非 Lybra 渲染物" in enroll_out and "不覆盖" in enroll_out
    assert (session / "AGENTS.md").read_text(encoding="utf-8").startswith("# 会话仓自带的")  # 未被覆盖
    assert json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))["harness"] == {"kind": "codex", "dir": str(session), "host": None}
    (session / "AGENTS.md").unlink()  # 出口: 移走后重跑 sync(凭据保留, 勿重跑 enroll)
    out = r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov}")
    _show("[② codex sync 输出]\n" + out)
    text = _assert_rendered_charter(session / "AGENTS.md", "lybra-probe")
    _show("[② codex 章程落点 AGENTS.md] 前 6 行:\n" + "\n".join(text.splitlines()[:6]))
    assert not (session / ".claude").exists()
    sync = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))
    assert sync["workstations"][0]["result"]["plan"] == []


def test_item2_remote_codex_session_lists_undelivered_not_refused(tmp_path):
    """他机会话(无本机落点): 零写入, 声明给 codex 的章程列 undelivered 并点名原因, 不拒(sync 全量巡检不因此失败)。"""
    from tools.aipos_cli.distribution_sync import _sync_harness_dir

    class _NoGate:
        def call_tool(self, *_a, **_k):  # 本机无落点时不得拉取任何件
            raise AssertionError("remote session must not fetch")

    ctx = {"role": "advisor", "gate_url": "http://127.0.0.1:1", "harness_root": tmp_path, "lybra_dir": tmp_path / ".lybra"}
    remote = {"product_commit": "x", "distributions": [{"distribution_id": "advisor-charter-codex", "kind": "charter",
                                                        "target_path": "AGENTS.md", "files": []}]}
    res = _sync_harness_dir(_NoGate(), ctx, remote, identity={"instance": "advisor.p.mac", "project": "p", "role": "advisor"},
                            harness={"kind": "codex", "dir": None, "host": "mac-probe", "local": False}, scope="p", dry_run=True)
    _show(f"[② 他机 codex] {res['note']}")
    assert res["ok"] is True and res["plan"] == [] and res["files_fetched"] == 0
    assert [u["distribution_id"] for u in res["undelivered"]] == ["advisor-charter-codex"] and "未交付" in res["note"]


def test_item2_charter_single_source_and_declared_per_advisor_harness():
    from tools.aipos_cli.distribution_sync import advisor_harness_kinds, harness_distributions
    from tools.aipos_cli.workstation_wiring import declared_role_distributions

    dists = declared_role_distributions("advisor", "advisor")
    for kind in advisor_harness_kinds():
        charters = [d for d in harness_distributions(dists, kind) if d["kind"] == "charter"]
        assert len(charters) == 1 and charters[0]["source_path"] == "agents/roles/advisor/AGENTS.md", (kind, charters)
    master = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    assert all(needle in master for needle in GUARD_TEXT)
    # skills 只引用不复述: 守则全文只在章程母本
    for skill in sorted((REPO_ROOT / "agents" / "skills").glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        assert "只在 `owner_needed` 时停" not in text, skill


# ===========================================================================
# ③ 文档旧写法收口: 写 project.json 的命令一律两阶段; 文档命令可解析
# ===========================================================================

PROJECT_JSON_WRITERS = re.compile(r"\blybra project (set-repos|set-repo|set-paths|set-workstation|set-meta)\b")
EXTRA_PLACEHOLDERS = {"<CARD-ID>": "PROJ-1", "<card>": "proj-1", "<governance-root>": "/tmp/f136-gov", "<门工作区>": "/tmp/f136-ops",
                      "<治理根>": "/tmp/f136-gov", "<session-host>": "mac-probe", "<决策ID>": "arb-2026-10-08-01"}


def _code_block_commands(text: str) -> list[str]:
    """全部 ``` 代码块内的命令行(续行合并, 去行尾注释)。"""
    out: list[str] = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        for line in re.sub(r"\\\n\s*", " ", block).splitlines():
            line = line.split(" #", 1)[0].strip()
            if line.startswith("lybra "):
                out.append(line)
    return out


def test_item3_project_json_writers_are_two_phase_in_all_docs():
    hits: list[str] = []
    total = 0
    for rel in f96.doc_files():
        for line in _code_block_commands((REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")):
            if PROJECT_JSON_WRITERS.search(line):
                total += 1
                if "--confirm" not in line and "--dry-run" not in line:
                    hits.append(f"{rel}: {line}")
    _show(f"[③ 写 project.json 命令] 文档内共 {total} 条, 缺两阶段旗标 {len(hits)} 条: {hits or '0'}")
    assert total >= 4 and hits == []


def test_item3_readme_quickstart_and_charter_commands_parse(monkeypatch):
    monkeypatch.setattr(f84, "PLACEHOLDER_VALUES", {**f84.PLACEHOLDER_VALUES, **EXTRA_PLACEHOLDERS})
    from tools.aipos_cli.aipos_cli import build_parser

    parsed = 0
    for rel in ("README.md", "QUICKSTART.md", "agents/roles/advisor/AGENTS.md"):
        text = re.sub(r"\{\{\s*\w+\s*\}\}", "/tmp/f136-x", (REPO_ROOT / rel).read_text(encoding="utf-8"))
        for raw in f84._lybra_examples(text):
            argv = shlex.split(f84._substitute(raw))[1:]
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    build_parser().parse_args(argv)
            except SystemExit as exc:
                if exc.code not in (0, None):  # `lybra --help` = 0, 合法
                    raise AssertionError(f"{rel} argparse 拒: {raw}\n  {err.getvalue().strip()}") from exc
            parsed += 1
    _show(f"[③ README / QUICKSTART / 顾问章程 bash 示例] 解析通过 {parsed} 条")
    assert parsed >= 20
    for rel in ("README.md", "QUICKSTART.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "--harness codex" in text and "lybra loop status --task-id" in text and "--wait" in text, rel


# ===========================================================================
# ⑤ 不变量(防回潮)
# ===========================================================================

def _non_doc_string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


def test_invariant_wait_only_via_run_fs_watch_and_no_hardcoded_subtrees():
    src = REPO_ROOT / "tools" / "aipos_cli" / "loop_run_record.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    sleeps = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr == "sleep") or (isinstance(n.func, ast.Name) and n.func.id == "sleep"))]
    assert sleeps == [], f"loop_run_record 不得自行 sleep 轮询(等待只经 agent_watch_fs.run_fs_watch): 行 {sleeps}"
    assert "run_fs_watch(" in src.read_text(encoding="utf-8")
    watch_src = REPO_ROOT / "tools" / "aipos_cli" / "agent_watch_fs.py"
    literals = [s for s in _non_doc_string_constants(watch_src) if "5_tasks" in s]
    assert literals == [], f"哨兵子树须读声明, 不得写死: {literals}"


def test_invariant_no_empty_skill_dirs():
    """agents/skills/ 下每个目录都是技能(含 SKILL.md); 空目录(如已删的 lybra-advisor/)不回潮。"""
    empty = sorted(p.name for p in (REPO_ROOT / "agents" / "skills").iterdir() if p.is_dir() and not (p / "SKILL.md").is_file())
    assert empty == [], empty
    out = subprocess.run(["git", "grep", "-n", "skills/lybra-advisor", "--", "."], cwd=REPO_ROOT, capture_output=True, text=True,
                         check=False)
    assert out.returncode in (0, 1) and [l for l in out.stdout.splitlines() if not l.startswith("tests/test_aipos_f136")] == []
