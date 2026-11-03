"""AIPOS-F282 — 顾问出口身份收口。

件① 交回阻塞卡(blocked)的 queue reopen 接受本项目顾问类实例为 actor(声明 transitions.schema queue_mutations.transitions.reopen
     .advisor_actor, 判定唯一实现 queue_mutation.reopen_advisor_refusal: 角色类经 custom_roles.authorize_instance_class + 实例名项目段
     属本项目); 记录里 reopen actor 如实记顾问实例; 推导核续跑出口(owner_texts.reopen_exit, 双语)的 actor = 驱动方顾问实例
     (loop 运行记录 driver)。其余状态 / 其余队列变更的 actor 判定不变。
件② roles enroll-deliver 加 --project 透传(F280 三命令同一写法), 缺省不变; onboarding guide 第 4 / 6–8 步带 --project。

验收(卡面 ★验收)①–⑤ 在本文件; ⑥ = F252 / F280 既有用例(全量); ⑦⑧⑨ 见交回报告。
靶场: ①–④ 复用 F90 / F252 进程内门靶场(临时治理根, 执行实例 exec.lybra.test, 驱动方顾问 advisor.lybra.test); ⑤ 复用 F280 临时
home 根三项目 + 本检出起的临时门子进程(只经 tools/aipos_cli/tests/fixture_procs.py 起停, F280 rng 夹具)。不碰真实治理根 / 真实门 /
真实工位; 凭据 / 令牌 / 码一律不打印原文。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
import test_aipos_f252_card_policy_blocked_return as f252  # noqa: E402  — 阻塞交回夹具(报告 / watch / CLI 进程内调用)
from test_aipos_f90_loop_one_stage import rig as f90_rig  # noqa: E402,F401  — 夹具复用(pytest 按名注入)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

TASK = f90.TASK
CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
_show = f252._show
f90_cli = f252.f90_cli


def _blocked_card(r) -> str:
    """loop 跑到交回阻塞: claim → return(outcome=blocked)→ block, 卡进 blocked(驱动方 = 顾问 advisor.lybra.test)。返回 loop 输出原文。"""
    f90._card(r.gov, TASK, "pending")
    init_governance_repo(r.gov)
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=out, watch=f252._watch_blocked(r, []),
                   interval=0.05, max_wait=5, max_steps=20)
    assert res.outcome == "not_derivable" and res.reason == "return_blocked", out.getvalue()
    card = r.gov / "5_tasks" / "queue" / "blocked" / f"{TASK.lower()}.md"
    assert card.is_file()
    return out.getvalue()


def _reopen(r, actor: str, *extra: str) -> tuple[int, str, str]:
    return f90_cli(["queue", "reopen", "--task-id", TASK, "--actor", actor, "--reason", "前置已补齐: 开新一轮续跑",
                    "--workspace-root", str(r.gov), *extra])


def _queue_snapshot(r) -> dict:
    return {"files": sorted(str(p.relative_to(r.gov)) for p in (r.gov / "5_tasks").rglob("*.md")),
            "head": f78._git(r.gov, "rev-parse", "HEAD")}


# ===========================================================================
# 件① 声明与唯一判定
# ===========================================================================

def test_declaration_and_single_judgment(f90_rig):
    from tools.aipos_cli.queue_mutation import REOPEN_SOURCE_STATES, reopen_advisor_declaration, reopen_advisor_refusal

    r = f90_rig
    decl = reopen_advisor_declaration()
    _show(f"[件① 声明 transitions.queue_mutations.transitions.reopen.advisor_actor] {decl}; reopen.from_states={REOPEN_SOURCE_STATES}")
    assert decl == {"from_states": ("blocked",), "actor_role_class": "advisor"} and set(decl["from_states"]) <= set(REOPEN_SOURCE_STATES)
    cases = {
        f78.DRIVER: True,                  # 本项目内建顾问
        "advisor.other.test": False,       # 他项目顾问(项目段不属本项目)
        f78.AUDITOR: False,                # 审计类
        f78.EXEC: False,                   # 执行类(执行实例走既有卡归属判定, 不经本判定)
        "advisor": False,                  # 裸角色名(非完整实例名)
    }
    got = {a: reopen_advisor_refusal(r.gov, a, state="blocked", task_id=TASK) for a in cases}
    for actor, res in got.items():
        _show(f"  {actor}: ok={res['ok']} role_class={res['role_class']} segment={res['segment']} message={res['message']}")
    assert {a: g["ok"] for a, g in got.items()} == cases
    # completed 不适用(applies=False = 搬卡执行器按既有卡归属判)
    assert reopen_advisor_refusal(r.gov, f78.DRIVER, state="completed", task_id=TASK) == {
        "applies": False, "ok": False, "role_class": None, "segment": None, "message": None}
    # 判定只经唯一实现(角色类 authorize_instance_class + 项目段 instance_project_segment), 源码不写死实例名 / 项目 ID
    import inspect

    from tools.aipos_cli import queue_mutation

    src = inspect.getsource(queue_mutation.reopen_advisor_refusal)
    assert "authorize_instance_class(" in src and "instance_project_segment(" in src
    assert "lybra" not in src and "advisor." not in src


# ===========================================================================
# 验收① pi / 子 agent 执行卡交回 blocked → loop 转 blocked → 顾问实例 reopen 成功(记录 actor = 顾问)→ loop 重认领续跑
# ===========================================================================

def test_acceptance1_advisor_reopens_blocked_return_then_loop_resumes(f90_rig):
    r = f90_rig
    _blocked_card(r)
    fm = nr._read_frontmatter(r.gov / "5_tasks" / "queue" / "blocked" / f"{TASK.lower()}.md")
    _show(f"[验收① 交回阻塞后] status={fm['status']} claimed_by={fm.get('claimed_by')} assigned_to={fm.get('assigned_to')}")
    rc, out, err = _reopen(r, f78.DRIVER, "--dry-run")
    _show(f"---- 验收① 顾问 {f78.DRIVER} queue reopen --dry-run exit {rc} ----\n{out[-1200:]}{err}")
    assert rc == 0, out + err
    rc, out, err = _reopen(r, f78.DRIVER)
    _show(f"---- 验收① 顾问 {f78.DRIVER} queue reopen exit {rc} ----\n{out[-1500:]}{err}")
    assert rc == 0, out + err
    pending = r.gov / "5_tasks" / "queue" / "pending" / f"{TASK.lower()}.md"
    fm = nr._read_frontmatter(pending)
    events = sorted((r.gov / "5_tasks" / "records" / "events" / TASK).glob("reopen_round_*.md"))
    ev = nr._read_frontmatter(events[-1]) if events else {}
    log = f78._git(r.gov, "log", "-1", "--format=%s%n%b")
    _show(f"[验收① 记录] 卡面 reopened_by={fm.get('reopened_by')} round={fm.get('round')}; 轮界记录 {events[-1].name if events else '-'} "
          f"reopened_by={ev.get('reopened_by')}; 落账 {log.splitlines()[0] if log else '-'}")
    assert fm["status"] == "pending" and fm["reopened_by"] == f78.DRIVER  # actor 如实记顾问实例
    assert len(events) == 1 and ev["reopened_by"] == f78.DRIVER and ev["from_state"] == "blocked"
    assert (r.gov / "task_cards" / TASK / "rounds" / "round-1" / "RETURN.md").is_file()  # 旧阻塞交回报告归档
    # reopen 自己的写出(搬卡 + 轮界记录 + 报告归档)随 reopen 落账(loop 账务记录由 N6 落账, 不在此判)
    assert f78._git(r.gov, "status", "--porcelain", "--", "task_cards", "5_tasks/queue", "5_tasks/records/events") == ""
    # loop 原命令续跑: 重认领 → 交回(done)→ 派审 → 裁决 → 合入 → 结案。
    # AIPOS-F288 件③: 认领记录名同秒重认领加单调序号(record_writer.claim_runtime_ids), 不再需要跨秒等待
    out = io.StringIO()
    seen: list[str] = []
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=out, watch=f90._agent_watch(r, seen), interval=0.05,
                   max_wait=5, max_steps=30)
    _show(f"---- 验收① reopen 后续跑 loop exit {res.exit_code} outcome {res.outcome} ----\n{out.getvalue()[-2500:]}")
    assert res.outcome == "completed", out.getvalue()
    assert [s.action_type for s in res.steps if s.kind == "execute" and s.ok][0] == "claim"
    assert (r.gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    recs = nr._read_task_records(r.gov, TASK)
    assert nr._claimer_instance(recs) == f78.EXEC  # 新一轮仍由执行实例认领(轮界之后的本轮 claim 记录)
    assert len(f90._records(r.gov, "closures", TASK)) == 1 and len(f90._records(r.gov, "returns", TASK)) == 2


# ===========================================================================
# 验收② 非顾问非执行体(审计 / 他项目顾问 / 裸角色名)reopen 仍拒, 零写入
# ===========================================================================

def test_acceptance2_non_advisor_non_executor_reopen_refused(f90_rig):
    r = f90_rig
    _blocked_card(r)
    before = _queue_snapshot(r)
    for actor in (f78.AUDITOR, "advisor.other.test", "advisor"):
        rc, out, err = _reopen(r, actor)
        text = out + err
        _show(f"---- 验收② {actor} queue reopen exit {rc} ----\n" + "\n".join(
            ln for ln in text.splitlines() if "actor" in ln or "BLOCK" in ln)[:1500])
        assert rc == 1 and "Current actor does not match" in text, text
        assert "既不是本卡认领执行实例, 也不是本项目 lybra 的顾问类实例" in text and "出口: --actor 换成本项目顾问实例" in text, text
    assert _queue_snapshot(r) == before  # 零写入、零落账
    assert (r.gov / "5_tasks" / "queue" / "blocked" / f"{TASK.lower()}.md").is_file()
    # en: 拒因按用户语言出声, 零中日韩字符(实例 / 项目名 / 命令不译)
    from tools.aipos_cli.queue_mutation import reopen_advisor_refusal

    msg = reopen_advisor_refusal(r.gov, f78.AUDITOR, state="blocked", task_id=TASK)["message"]
    from tools.aipos_cli.i18n import pick

    _show(f"[验收② en 拒因] {pick(msg, 'en')}")
    assert not CJK.search(pick(msg, "en")) and "advisor-class instance of project lybra" in pick(msg, "en")


# ===========================================================================
# 验收③ 非 blocked 状态的 reopen / 其余队列变更的 actor 判定不变(回归)
# ===========================================================================

def test_acceptance3_other_states_and_mutations_unchanged(f90_rig):
    r = f90_rig
    # 执行实例 reopen blocked 卡: 既有路径照旧放行
    _blocked_card(r)
    rc, out, err = _reopen(r, f78.EXEC, "--dry-run")
    _show(f"[验收③ 执行实例 reopen blocked 卡 --dry-run] exit {rc}")
    assert rc == 0, out + err
    # 顾问对 completed 卡 reopen: 不适用顾问判定, 按卡归属拒(行为不变; 不追加顾问拒因)
    f90._card(r.gov, "PROBE-F90-9", "completed", completed_by=f78.EXEC, completed_at="2026-11-01T00:00:00Z")
    rc, out, err = f90_cli(["queue", "reopen", "--task-id", "PROBE-F90-9", "--actor", f78.DRIVER, "--reason", "x", "--dry-run",
                            "--workspace-root", str(r.gov)])
    _show(f"[验收③ 顾问 reopen completed 卡] exit {rc}: " + " | ".join(ln.strip() for ln in (out + err).splitlines() if "actor" in ln)[:600])
    assert rc == 1 and "Current actor does not match" in out + err and "顾问类实例" not in out + err
    rc, out, err = f90_cli(["queue", "reopen", "--task-id", "PROBE-F90-9", "--actor", f78.EXEC, "--reason", "x", "--dry-run",
                            "--workspace-root", str(r.gov)])
    assert rc == 0, out + err  # 执行实例 reopen completed 卡照旧
    # 顾问对 claimed 卡 block / complete: 照旧按卡归属拒
    f90._card(r.gov, "PROBE-F90-8", "claimed", claimed_by=f78.EXEC, claimed_at="2026-11-01T00:00:00Z",
              claim_id="claim_PROBE-F90-8_20261101_000000_exec-lybra-test",
              active_session_id="session_PROBE-F90-8_20261101_000000_exec-lybra-test")
    for argv in (["queue", "block", "--task-id", "PROBE-F90-8", "--actor", f78.DRIVER, "--reason", "x", "--dry-run"],
                 ["queue", "reopen", "--task-id", "PROBE-F90-8", "--actor", f78.DRIVER, "--reason", "x", "--dry-run"]):
        rc, out, err = f90_cli([*argv, "--workspace-root", str(r.gov)])
        _show(f"[验收③ 顾问 {argv[1]} claimed 卡] exit {rc}")
        assert rc == 1, out + err
    rc, out, err = f90_cli(["queue", "block", "--task-id", "PROBE-F90-8", "--actor", f78.EXEC, "--reason", "x", "--dry-run",
                            "--workspace-root", str(r.gov)])
    assert rc == 0, out + err  # 执行实例 block 照旧


# ===========================================================================
# 验收④ lybra next / loop status 对 blocked 卡给的建议命令 actor = 顾问实例
# ===========================================================================

def test_acceptance4_next_and_loop_status_suggest_advisor(f90_rig, monkeypatch):
    r = f90_rig
    loop_out = _blocked_card(r)
    stop = [ln for ln in loop_out.splitlines() if "queue reopen" in ln]
    _show("---- 验收④ loop 结束原文(续跑出口行) ----\n" + "\n".join(stop))
    assert stop and f"queue reopen --task-id {TASK} --actor {f78.DRIVER}" in "\n".join(stop)  # loop 进程内 = 本次 --actor
    rc, out, err = f90_cli(["next", "--task-id", TASK, "--workspace-root", str(r.gov)])
    _show(f"---- 验收④ lybra next exit {rc} ----\n{out[-2000:]}{err[-400:]}")
    assert f"queue reopen --task-id {TASK} --actor {f78.DRIVER}" in out + err
    assert f"--actor {f78.EXEC}" not in out + err
    assert nr.return_blocked_advisor(r.gov, TASK) == f78.DRIVER  # 取自 loop 运行记录 driver
    # loop status: 下一动作 owner_needed, 建议命令(hint)= lybra next(即上面的续跑出口); 运行记录 driver = 顾问(出口 actor 的来源)
    rc, sout, _err = f90_cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(r.gov), "--json"])
    status = json.loads(sout)
    _show(f"[验收④ loop status] next_action={json.dumps(status['next_action'], ensure_ascii=False)[:1500]}")
    assert status["next_action"]["action"] == "owner_needed" and status["next_action"]["hint"].startswith(f"lybra next --task-id {TASK}")
    runs = sorted((r.gov / "5_tasks" / "records" / "loop_runs" / TASK).glob("looprun_*.md"))
    end = nr._read_frontmatter(runs[-1])
    _show(f"[验收④ 运行记录 {runs[-1].name}] driver={end.get('driver')} end_reason={end.get('end_reason')}")
    assert end["driver"] == f78.DRIVER and end["end_reason"] == "return_blocked"
    # 出口按用户语言: en 零中日韩字符(命令 / 实例不译)
    monkeypatch.setenv("LYBRA_LANG", "en")
    d = nr.derive_next_step(TASK, r.gov)
    _show(f"[验收④ en 出口] {d['suggested_action']}")
    assert f"--actor {f78.DRIVER}" in d["suggested_action"] and not CJK.search(d["suggested_action"])


def test_acceptance4_no_run_record_gives_placeholder_not_executor(f90_rig):
    """无 loop 运行记录(驱动方无据)= 双语占位, 不回落执行实例或别的身份。"""
    r = f90_rig
    f252._claimed_with_return(r.gov, outcome="blocked")
    assert nr.return_blocked_advisor(r.gov, TASK) == ""
    d = nr.derive_next_step(TASK, r.gov)
    _show(f"[验收④ 无运行记录] verb={d['verb']}\n  出口={d['suggested_action']}")
    assert d["verb"] == "lybra_queue_block" and "--actor <本项目顾问实例>" in d["suggested_action"]
    assert f"queue reopen --task-id {TASK} --actor {f78.EXEC}" not in d["suggested_action"]


# ===========================================================================
# 件② onboarding guide 第 4 / 6–8 步带 --project(文案双语)
# ===========================================================================

def test_onboarding_guide_enroll_deliver_steps_carry_project(tmp_path, monkeypatch):
    from tools.aipos_cli import i18n
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    for code in ("zh", "en"):
        tpl = i18n.owner_block("onboarding_guide")[code]["enroll"]["fail_ambiguous"]
        assert "--project {pq}" in tpl and "--governance-root" in tpl and (code == "zh" or not CJK.search(tpl))
    guide = generate_onboarding_guide("probe-dl", home_root=str(tmp_path / "h"), actor="advisor.probe-dl.h1", repos=[f"app={tmp_path}/app"])
    cmds = [c for s in guide["steps"] for c in (s.get("commands") or []) if " enroll-deliver " in c]
    _show("[件② 向导 enroll-deliver 命令]\n" + "\n".join(cmds))
    assert len(cmds) >= 8 and all(" --project probe-dl " in c for c in cmds)
    steps = [s for s in guide["steps"] if any(" enroll-deliver " in c for c in (s.get("commands") or []))]
    assert all("PROJECT_AMBIGUOUS" in s["on_fail"] for s in steps)


# ===========================================================================
# 验收⑤ enroll-deliver --project: Owner 多项目无缺省凭据对第二个项目成功; 不带时拒因含 --project 出口
#   (F280 rng 夹具: 模块级临时门子进程; 放最后, 其 HOME / LYBRA_HOME_ROOT 覆盖到模块结束)
# ===========================================================================

from test_aipos_f280_enroll_scope_cli_project import C, HOST, _cli, rng  # noqa: E402,F401  — F280 靶场(临时 home 根 + 临时门)


def test_acceptance5_enroll_deliver_project_flag(rng, monkeypatch):
    home, gov_c = rng["home"], rng["gov_c"]
    conn = str(rng["clients"]["owner-multi"])
    neutral = rng["tmp"] / "neutral-cwd-f282"
    neutral.mkdir(exist_ok=True)
    monkeypatch.chdir(neutral)
    for key in [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    lines: list[str] = []

    def run(label: str, *argv: str) -> tuple[int, str]:
        rc, out, err = _cli(*argv)
        text = out + err
        lines.append(f"$ lybra {' '.join(argv)}  [{label}]\n  rc={rc}\n  " + "\n  ".join(
            ln for ln in text.splitlines() if ln.strip() and not ln.startswith("签码凭据文件"))[:1800])
        return rc, text

    # 工位类接入前置: 项目常设授权(F280 同一命令, --project 透传)
    grant = ["project", "grant", "sign", C, "--home-root", str(home), "--grant-lane", "app", "--task-mode", "code",
             "--driver", f"advisor.{C}.{HOST}", "--host", HOST, "--max-tasks", "5", "--expires-at", "2099-01-01T00:00:00Z",
             "--connection-json", conn, "--project", C, "--confirm"]
    rc, text = run("project grant sign --project(前置)", *grant)
    assert rc == 0 and f"signed auth_{C}" in text, text
    ws = rng["tmp"] / "ws-f282-exec"
    ws.mkdir(exist_ok=True)
    instance = f"exec.{C}.{HOST}"
    deliver = ["roles", "--connection-json", conn, "enroll-deliver", "--role", "executor", "--instance", instance, "--governance-root", C,
               "--workspace", str(ws), "--executor-mode", "workstation", "--gate-url", rng["gate_url"],
               "--owner-authorization-ref", f"auth_{C}", "--token-role", "owner"]
    rc, text = run("roles enroll-deliver 不带 --project(预演)", *deliver, "--dry-run")
    assert rc == 1 and "PROJECT_AMBIGUOUS" in text and "--project" in text and "lybra roles enroll-deliver" in text, text
    assert not (ws / ".lybra" / "connection.json").exists()
    rc, text = run("roles enroll-deliver --project(预演)", *deliver, "--project", C, "--dry-run")
    assert rc == 0 and "预览" in text, text
    assert not (ws / ".lybra" / "connection.json").exists()  # 预演零写入
    rc, text = run("roles enroll-deliver --project(确认)", *deliver, "--project", C, "--confirm")
    assert rc == 0, text
    assert "LYBRAENROLL1." not in text  # 注册码不出进程
    landed = json.loads((ws / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    entries = [t for t in landed.get("tokens") or [] if t.get("agent_instance") == instance]
    enrollments = json.loads((gov_c / ".lybra" / "enrollments.json").read_text(encoding="utf-8"))
    rec = [x for x in enrollments.values() if isinstance(x, dict) and x.get("instance") == instance]
    lines.append(f"[落点 {ws.name}/.lybra/connection.json] 实例 {instance} 条目 {len(entries)} 条(token 不打印)")
    lines.append(f"[码记录 {C}/.lybra/enrollments.json] " + json.dumps(
        [{k: x.get(k) for k in ("role", "instance", "governance_root", "status")} for x in rec], ensure_ascii=False))
    _show("[验收⑤·roles enroll-deliver --project · Owner 多项目无缺省凭据 [proj-r2, proj-c] · 原文(码 / 令牌不打印)]\n" + "\n".join(lines))
    assert entries and rec and rec[0]["governance_root"] == str(gov_c)
    # --project 与 --governance-root 不是同一项目 = 门既有 PROJECT_MISMATCH(预演即拒, 零写入)
    # --project 与 --governance-root 不是同一项目: 本机预检过(C 已签授权)后由门一处判(PROJECT_MISMATCH / 项目域外 PROJECT_SCOPE_DENIED)
    rc, o, e = _cli(*deliver, "--project", rng["gov_b"].name, "--dry-run")
    text = o + e
    _show(f"[验收⑤ --project 与 --governance-root 不一致] rc={rc}: " + " ".join(text.split())[:600])
    assert rc == 1 and "PROJECT_" in text
    rc, out, _err = _cli("roles", "enroll-deliver", "--help")
    assert "--project" in out and "AIPOS-F282" in out


def test_f282_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f282_advisor_reopen_enroll_deliver_project.py", default_test_contract())
