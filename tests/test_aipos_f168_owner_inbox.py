"""AIPOS-F168 — Owner 待决收件箱: 跨项目汇总需 Owner 定的事项(件①), Owner 一处裁决(件②), loop 运行记录补写待决结构字段(件③);
顾问 `loop status` / `next` 直接读到裁决继续推进; 看板「Owner 待决」页同一数据。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; `lybra project new` 建两个项目 alpha / beta-x, 后者名带连字符 = chris 形)。
待决真相全部经产品既有写入口造: loop 运行记录 = loop_run_record.LoopRunRecorder(loop 自身的记录器)/ loop_driver.run_loop(无信封真跑);
卡 = 队列卡文件; 信封 = autonomy_policy.build_autonomy_policy_markdown; 裁决记录 = 被测命令自己写。不读真实凭据、不碰真实治理根、不起门。

验收(卡面 ★验收): ① 两临时项目各造 owner_needed 运行记录 / needs_owner 卡 / 信封将尽 → inbox 汇总原文与排序
② decide 预演 / 确认原文、owner_decision_record 落账、顾问侧 loop status 显示裁决原文、needs_owner 卡经既有口解除原文
③ 看板页渲染(测试客户端取页面 HTML 断言)原文 ④(真实只读预演见 RETURN)⑥ 棘轮 / 声明不变量。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import socket
import subprocess
import sys
import threading
import urllib.request
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源

OWNER = "owner"
# AIPOS-F170 件③: owner decide 必填转达人与 Owner 原话(Owner 本人直接敲 = owner)
RELAY = ("--relayed-by", OWNER, "--owner-words", "夹具: Owner 本人裁决")
ALPHA, BETA = "alpha", "beta-x"
DRIVER_A, DRIVER_B = f"advisor.{ALPHA}.h1", f"advisor.{BETA}.h1"


def _show(text: str) -> None:
    print(text, flush=True)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
                          cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _cli(*argv: str) -> tuple[int, str]:
    from tools.aipos_cli.aipos_cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue()


def _tree_digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


def _card(gov: Path, task_id: str, queue: str, project: str, **extra) -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} fixture", "project": project, "task_mode": "code",
            "assigned_to": f"exec.{project}.h1", "agent_instance": f"exec.{project}.h1", "status": queue, "audit": "required",
            "audit_by": f"audit.{project}.h1", "output_target": "src/", "priority": "high", "created_by": f"advisor.{project}.h1",
            "needs_owner": False, "context_bundle": "t", "artifact_policy": "formal_write"}
    meta.update(extra)
    path = gov / "5_tasks" / "queue" / queue / f"{task_id.lower()}.md"
    _write(path, _fm(meta, f"# {task_id}\n\n## Goal\n把 {task_id} 做完。\n\n## 验收\n- 夹具绿\n"))
    return path


def _owner_needed_run(gov: Path, task_id: str, driver: str, *, reason: str = "gate_rejected",
                      message: str = "GATE_DESIGN_FORK: 方案 A / B 二选一, 门拒") -> str:
    """loop 自身的记录器(LoopRunRecorder)落一份已结束运行 = 真实运行记录形(不手拼 frontmatter)。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    rec = LoopRunRecorder(gov, task_id, driver=driver, contract=load_loop_contract(), warn=_show)
    rec.set_envelope("pol_fixture_1")
    rec.end(outcome=reason, exit_code=2, reason=reason, message=message)
    return rec.run_id


def _policy(gov: Path, policy_id: str, *, who: str, max_tasks: int, hours_left: float) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown
    from tools.aipos_cli.clock import iso_z, utc_now

    now = utc_now()
    _write(gov / "5_tasks" / "policies" / f"{policy_id}.md", build_autonomy_policy_markdown(
        policy_id=policy_id, agent_or_role=who, active_from=iso_z(now - timedelta(days=1)),
        expires_at=iso_z(now + timedelta(hours=hours_left)), max_tasks=max_tasks, owner_approval_ref="fixture-envelope",
        task_selector_task_mode="code"))


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """home 根 = 治理仓(带 origin 裸仓), 两个项目 alpha / beta-x 各造三类待决真相, 全部提交并推送(落账从干净树起)。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    for name in (ALPHA, BETA):
        rc, out = _cli("project", "new", name, "--home-root", str(hroot), "--actor", OWNER)
        assert rc == 0, out
    alpha, beta = hroot / ALPHA, hroot / BETA
    # alpha: 门拒 owner_needed 运行(A-1) + 无信封 owner_needed 运行(A-2, 卡已结案 = 已自行解除, 不列) + pending needs_owner 卡(A-3)
    _card(alpha, "ALPHA-1", "claimed", ALPHA)
    _owner_needed_run(alpha, "ALPHA-1", DRIVER_A)
    _card(alpha, "ALPHA-2", "completed", ALPHA)
    _owner_needed_run(alpha, "ALPHA-2", DRIVER_A, reason="no_envelope", message="no envelope")
    _card(alpha, "ALPHA-3", "pending", ALPHA, needs_owner=True)
    _policy(alpha, "pol_alpha_far_1", who=DRIVER_A, max_tasks=50, hours_left=24 * 30)  # 远未到预警: 不列
    # beta-x: 连败 owner_needed 运行(B-1) + blocked needs_owner 卡(B-2) + 信封将尽(B 信封 2 小时后到期)
    _card(beta, "BETA-1", "claimed", BETA)
    _owner_needed_run(beta, "BETA-1", DRIVER_B, reason="wait_timeout", message="工位未交产物")
    _card(beta, "BETA-2", "blocked", BETA, needs_owner=True, blocked_by=DRIVER_B, blocked_at="2026-10-01T00:00:00Z",
          block_reason="依赖方接口未定")
    _policy(beta, "pol_beta_soon_1", who=DRIVER_B, max_tasks=50, hours_left=2)
    remote = init_governance_repo(hroot)
    return {"home": hroot, "alpha": alpha, "beta": beta, "remote": remote, "tmp": tmp_path}


def _inbox(rig, *extra: str) -> tuple[int, str]:
    return _cli("owner", "inbox", "--home-root", str(rig["home"]), *extra)


def _inbox_json(rig) -> dict:
    rc, out = _inbox(rig, "--json")
    assert rc in (0, 4), out
    return json.loads(out)


# ===========================================================================
# ① 跨项目汇总 + 排序 + 已自行解除不列
# ===========================================================================

def test_item1_inbox_aggregates_two_projects_sorted_by_blocking(rig):
    rc, text = _inbox(rig)
    _show(f"---- ① lybra owner inbox 原文 exit {rc} ----\n{text}")
    assert rc == 0, text
    data = _inbox_json(rig)
    rows = [(i["project"], i["category"], i.get("task_id") or i.get("policy_id"), i["rank"]) for i in data["items"]]
    _show(f"[① 事项(项目, 类别, 主体, 阻塞级)] {rows}")
    assert data["projects"] == [ALPHA, BETA]
    got = {(p, c, s) for p, c, s, _r in rows}
    assert got == {(ALPHA, "loop_owner_needed", "ALPHA-1"), (ALPHA, "needs_owner_card", "ALPHA-3"),
                   (BETA, "loop_owner_needed", "BETA-1"), (BETA, "needs_owner_card", "BETA-2"),
                   (BETA, "envelope_warning", "pol_beta_soon_1")}, rows
    assert ("alpha", "loop_owner_needed", "ALPHA-2") not in got, "卡已结案 = 已自行解除, 不列"
    ranks = [r for *_x, r in rows]
    assert ranks == sorted(ranks) and ranks[0] == 1 and ranks[-1] == 3, "按阻塞程度排序: 阻塞 → 待定 → 预警"
    first = data["items"][0]
    for key in ("item_id", "project", "subject", "source", "category_label", "ask", "impact", "options", "decide_command"):
        assert first[key], key
    a1 = next(i for i in data["items"] if i.get("task_id") == "ALPHA-1")
    assert a1["source"] == DRIVER_A and "GATE_DESIGN_FORK" in a1["ask"] and "门拒" in a1["ask"]
    assert [o["key"] for o in a1["options"]] == ["continue", "rework", "pause"]
    b2 = next(i for i in data["items"] if i.get("task_id") == "BETA-2")
    assert b2["rank"] == 1 and b2["source"] == DRIVER_B and "依赖方接口未定" in b2["ask"]
    # AIPOS-F170 件①: blocked 卡经 Owner 裁决可撤(既有 withdraw 口带裁决引用)/ 冻结为历史(既有 freeze-legacy 口); 仍不给 release
    assert [o["key"] for o in b2["options"]] == ["withdraw", "freeze_legacy", "new_card", "acknowledge"]
    env = next(i for i in data["items"] if i["category"] == "envelope_warning")
    assert "lybra envelope mint" in env["recommend"] and env["source"] == DRIVER_B
    assert f"--home-root {rig['home']}" in a1["decide_command"]
    assert "lybra owner decide --item OI-" in text and "要定:" in text and "影响:" in text and "选项:" in text


def test_item1_project_filter_and_read_only(rig):
    before, status = _tree_digest(rig["home"]), _git(rig["home"], "status", "--porcelain", "-uall")
    rc, text = _inbox(rig, "--project", BETA)
    _show(f"---- ① inbox --project {BETA} 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "ALPHA-" not in text and "BETA-1" in text
    assert _tree_digest(rig["home"]) == before and _git(rig["home"], "status", "--porcelain", "-uall") == status, "inbox 只读零写入"
    rc, text = _inbox(rig, "--project", "no-such")
    assert rc == 2 and "不在 home 根" in text


def test_item1_unreadable_truth_is_named_not_skipped(rig):
    bad = rig["alpha"] / "5_tasks" / "records" / "loop_runs" / "ALPHA-1"
    run = sorted(bad.glob("looprun_*.md"))[-1]
    run.write_text("---\nrecord_type: [broken\n---\n", encoding="utf-8")
    rc, text = _inbox(rig)
    _show(f"---- ① 运行记录读不出 原文 exit {rc} ----\n{text}")
    assert rc == 4 and "读不出的真相(1 处" in text and "ALPHA-1" in text.split("读不出的真相")[1]


# ===========================================================================
# ③ loop note 补写结构字段 → 收件箱展示
# ===========================================================================

def _note(rig, *extra: str) -> tuple[int, str]:
    return _cli("loop", "note", "--task-id", "ALPHA-1", "--workspace-root", str(rig["alpha"]),
                "--ask", "门拒设计分叉: 采用方案 A(改接口)还是方案 B(加适配层)",
                "--options", "plan_a=方案A 改接口", "plan_b=方案B 加适配层",
                "--recommend", "建议 plan_b: 不动已部署后端", "--actor", DRIVER_A, *extra)


def test_item3_loop_note_preview_then_confirm_lands_and_inbox_shows_it(rig):
    before = _tree_digest(rig["home"])
    rc, text = _note(rig)
    _show(f"---- ③ loop note 预演 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "预演(未写入)" in text and _tree_digest(rig["home"]) == before
    head = _git(rig["home"], "rev-parse", "HEAD")
    rc, text = _note(rig, "--confirm")
    _show(f"---- ③ loop note --confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "已写入" in text and "落账: verdict=PASS" in text
    assert _git(rig["home"], "rev-parse", "HEAD") != head and _git(rig["home"], "status", "--porcelain") == ""
    assert "loop 运行记录补写 Owner 待决结构字段 ALPHA-1" in _git(rig["home"], "log", "-1", "--format=%B")
    rc, status = _cli("loop", "status", "--task-id", "ALPHA-1", "--workspace-root", str(rig["alpha"]))
    _show(f"---- ③ loop status(补写后)原文 exit {rc} ----\n{status}")
    assert "Owner 待决(顾问补写" in status and "plan_b=方案B 加适配层" in status
    a1 = next(i for i in _inbox_json(rig)["items"] if i.get("task_id") == "ALPHA-1")
    assert a1["ask"].startswith("门拒设计分叉") and a1["recommend"] == "建议 plan_b: 不动已部署后端"
    assert [o["key"] for o in a1["options"]] == ["plan_a", "plan_b"]


def test_item3_loop_note_refuses_bad_options_and_missing_run(rig):
    rc, text = _cli("loop", "note", "--task-id", "ALPHA-1", "--workspace-root", str(rig["alpha"]), "--ask", "x", "--options", "Bad Key=y")
    assert rc == 2 and "键=标签" in text
    rc, text = _cli("loop", "note", "--task-id", "ALPHA-3", "--workspace-root", str(rig["alpha"]), "--ask", "x")
    assert rc == 4 and "无 loop 运行记录" in text


def test_item3_loop_note_refuses_running_run(rig):
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    rec = LoopRunRecorder(rig["alpha"], "ALPHA-9", driver=DRIVER_A, contract=load_loop_contract(), warn=_show)
    try:
        rc, text = _cli("loop", "note", "--task-id", "ALPHA-9", "--workspace-root", str(rig["alpha"]), "--ask", "x")
        _show(f"---- ③ loop note(运行中)原文 exit {rc} ----\n{text}")
        assert rc == 1 and "running" in text and "补写会被覆盖" in text
    finally:
        rec.end(outcome="interrupted", exit_code=3, reason="interrupted", message="fixture end")


# ===========================================================================
# ② 裁决: 预演 / 确认 / 落账 / 顾问侧读到 / needs_owner 经既有口解除
# ===========================================================================

def _item(rig, task_id: str) -> dict:
    return next(i for i in _inbox_json(rig)["items"] if i.get("task_id") == task_id)


def test_item2_decide_loop_item_preview_confirm_and_advisor_reads_ruling(rig):
    _note(rig, "--confirm")
    item = _item(rig, "ALPHA-1")
    before, status = _tree_digest(rig["home"]), _git(rig["home"], "status", "--porcelain", "-uall")
    relay = ("--relayed-by", DRIVER_A, "--owner-words", "就按适配层来, 接口下季度再说")
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "plan_b", "--note", "先上适配层, 接口下季度再改",
                    "--home-root", str(rig["home"]), *relay)
    _show(f"---- ② owner decide 预演 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "预演(零写入" in text and "owner-inbox-" in text
    assert _tree_digest(rig["home"]) == before and _git(rig["home"], "status", "--porcelain", "-uall") == status, "预演零写入"
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "nope", "--home-root", str(rig["home"]), *relay)
    assert rc == 2 and "可选: plan_a, plan_b" in text
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "plan_b", "--note", "先上适配层, 接口下季度再改",
                    "--home-root", str(rig["home"]), "--confirm", *relay)
    _show(f"---- ② owner decide --confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "落账: verdict=PASS" in text
    rec = rig["alpha"] / "5_tasks" / "records" / "owner_decisions" / f"{item['decision_id']}.md"
    record = rec.read_text(encoding="utf-8")
    _show(f"---- ② owner_decision_record 原文 ----\n{record}")
    assert "record_type: owner_decision_record" in record and "decision_type: owner_inbox_ruling" in record
    assert "allowed_next_action: plan_b" in record and f"external_ref: owner_inbox:{item['item_id']}" in record
    names = _git(rig["home"], "show", "--name-only", "--format=", "HEAD").splitlines()
    _show(f"[② 落账 git show --name-only HEAD] {names}\n[② git log -1] {_git(rig['home'], 'log', '-1', '--format=%B')}")
    assert f"{ALPHA}/5_tasks/records/owner_decisions/{item['decision_id']}.md" in names
    assert any("auto-pointer" in n for n in names), "decision_log 自动指针随门侧同一执行写出并落账"
    assert _git(rig["home"], "status", "--porcelain") == "" and _git(rig["home"], "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")
    # 顾问侧: loop status 读到裁决
    rc, status = _cli("loop", "status", "--task-id", "ALPHA-1", "--workspace-root", str(rig["alpha"]))
    _show(f"---- ② 顾问侧 loop status(裁决后)原文 exit {rc} ----\n{status}")
    assert ("顾问下一动作: advisor_fix(owner_ruled) — Owner 已裁决: 选 plan_b(方案B 加适配层) — 备注: 先上适配层, 接口下季度再改"
            f"(经 {DRIVER_A} 转达; Owner 原话: 「就按适配层来, 接口下季度再说」) @") in status
    assert "按此继续" in status and f"lybra loop --task-id ALPHA-1 --workspace-root {rig['alpha']} --actor {DRIVER_A} --envelope pol_fixture_1" in status
    from tools.aipos_cli import loop_run_record as lrr

    na = lrr.loop_status(rig["alpha"], "ALPHA-1")["next_action"]
    assert na["action"] == "advisor_fix" and na["reason"] == "owner_ruled" and na["owner_ruling"]["choice"] == "plan_b"
    rc, nxt = _cli("next", "--task-id", "ALPHA-1", "--workspace-root", str(rig["alpha"]))
    _show(f"---- ② 顾问侧 lybra next --task-id(裁决后)原文尾 ----\n{nxt[-600:]}")
    assert "Owner 已裁决: 选 plan_b" in nxt
    # 已裁决 = 不再出现
    assert "ALPHA-1" not in [i.get("task_id") for i in _inbox_json(rig)["items"]]
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "plan_b", "--home-root", str(rig["home"]), *relay)
    assert rc == 4 and "收件箱中无事项" in text


def test_item2_decide_release_pending_needs_owner_card_via_existing_exit(rig):
    item = _item(rig, "ALPHA-3")
    assert [o["key"] for o in item["options"]] == ["release", "withdraw", "freeze_legacy", "acknowledge"]
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "release", "--home-root", str(rig["home"]), *RELAY)
    _show(f"---- ② decide release 预演 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "解除动作 release_needs_owner(既有队列出口): ok=True" in text
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "release", "--note", "需求已澄清",
                    "--home-root", str(rig["home"]), "--confirm", *RELAY)
    _show(f"---- ② decide release --confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "落账: verdict=PASS" in text
    card = (rig["alpha"] / "5_tasks" / "queue" / "pending" / "alpha-3.md").read_text(encoding="utf-8")
    assert "needs_owner: false" in card
    amend = list((rig["alpha"] / "5_tasks" / "records" / "amendments" / "ALPHA-3").glob("*.md"))
    assert len(amend) == 1 and item["decision_id"] in amend[0].read_text(encoding="utf-8"), "经既有解除口 queue amend(留 amendment 记录), 不手改卡"
    names = _git(rig["home"], "show", "--name-only", "--format=", "HEAD").splitlines()
    _show(f"[② release 落账文件] {names}")
    assert f"{ALPHA}/5_tasks/queue/pending/alpha-3.md" in names and any("amendments/ALPHA-3" in n for n in names)
    rc, needs = _cli("needs-owner", "--workspace-root", str(rig["alpha"]))
    assert "ALPHA-3" not in needs, "needs-owner 同一筛选也不再列"
    assert "ALPHA-3" not in [i.get("task_id") for i in _inbox_json(rig)["items"]]


def test_item2_decide_envelope_renew_prints_owner_command(rig):
    item = next(i for i in _inbox_json(rig)["items"] if i["category"] == "envelope_warning")
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "renew", "--home-root", str(rig["home"]), "--confirm", *RELAY)
    _show(f"---- ② decide 信封 renew --confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "续签由 Owner 执行: 续签(Owner 执行): lybra envelope mint" in text
    assert not any(i["category"] == "envelope_warning" for i in _inbox_json(rig)["items"])


def test_item2_hyphen_project_record_and_blocked_card_acknowledge(rig):
    item = _item(rig, "BETA-2")
    rc, text = _cli("owner", "decide", "--item", item["item_id"], "--choice", "acknowledge", "--home-root", str(rig["home"]), "--confirm",
                    *RELAY)
    _show(f"---- ② decide blocked 卡 acknowledge(连字符项目)原文 exit {rc} ----\n{text}")
    assert rc == 0
    rec = (rig["beta"] / "5_tasks" / "records" / "owner_decisions" / f"{item['decision_id']}.md").read_text(encoding="utf-8")
    assert "client_tag: beta_x" in rec and "task_id: BETA-2" in rec
    assert (rig["beta"] / "5_tasks" / "queue" / "blocked" / "beta-2.md").is_file(), "acknowledge 不动卡"


# ===========================================================================
# ④⑤ 返工轮次上限 / 补审 FAIL 两类真相
# ===========================================================================

def test_item1_rework_limit_and_supplementary_fail_items(rig):
    from tools.aipos_cli.record_writer import record_file_prefix

    gov = rig["alpha"]
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": DRIVER_A,
               "focus_items": ["x"]} for n in (1, 2)]
    path = _card(gov, "ALPHA-7", "claimed", ALPHA)
    text = path.read_text(encoding="utf-8").replace("---\n# ALPHA-7", f"rework_rounds: {json.dumps(rounds)}\n---\n# ALPHA-7", 1)
    path.write_text(text, encoding="utf-8")
    vprefix = record_file_prefix("audit_verdicts")
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / "ALPHA-7" / f"{vprefix}_ALPHA-7_r3.md", _fm({
        "record_type": "audit_verdict_record", "verdict_id": f"{vprefix}_ALPHA-7_r3", "verdict": "FAIL", "reviewed_task_id": "ALPHA-7",
        "audit_task_id": "ALPHA-7R3", "verdict_at": "2026-10-09T00:00:00Z", "actor": "audit.alpha.h1"}))
    # 补审 FAIL 未派修复卡: 原卡已结案, 补审卡 ALPHA-2-S1R(task_mode=audit, supplementary_of=ALPHA-2), 补审裁决 FAIL
    _card(gov, "ALPHA-2-S1R", "completed", ALPHA, task_mode="audit", supplementary_of="ALPHA-2", reviewed_task_id="ALPHA-2")
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / "ALPHA-2" / f"{vprefix}_ALPHA-2_s1.md", _fm({
        "record_type": "audit_verdict_record", "verdict_id": f"{vprefix}_ALPHA-2_s1", "verdict": "FAIL", "reviewed_task_id": "ALPHA-2",
        "audit_task_id": "ALPHA-2-S1R", "supplementary_of": "ALPHA-2", "verdict_at": "2026-10-10T00:00:00Z", "actor": "audit.alpha.h1"}))
    rc, text = _inbox(rig, "--project", ALPHA)
    _show(f"---- ① 返工上限 / 补审 FAIL 原文 exit {rc} ----\n{text}")
    items = _inbox_json(rig)["items"]
    rework = next(i for i in items if i["category"] == "rework_limit")
    assert rework["task_id"] == "ALPHA-7" and "已返工 2/2 轮" in rework["ask"] and "FAIL" in rework["ask"] and rework["rank"] == 1
    sup = next(i for i in items if i["category"] == "supplementary_fail")
    assert sup["task_id"] == "ALPHA-2" and "ALPHA-2-S1R" in sup["ask"] and [o["key"] for o in sup["options"]] == ["repair", "accept"]


# ===========================================================================
# ③ 看板页: 测试客户端取页面 HTML 断言(同一数据)
# ===========================================================================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_item3_board_owner_inbox_page_and_api_same_data(rig, monkeypatch: pytest.MonkeyPatch):
    from http.server import ThreadingHTTPServer

    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    monkeypatch.setenv("LYBRA_HOME_ROOT", str(rig["home"]))
    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'])}"
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(repo_root=rig["alpha"], session_store=store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        def get(path: str) -> tuple[int, str, str]:
            req = urllib.request.Request(base + path, headers={"Cookie": cookie})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")

        status, ctype, page = get("/owner/inbox")
        _show(f"---- ③ 看板 GET /owner/inbox HTML 原文(status {status}, {ctype}) ----\n{page}")
        assert status == 200 and ctype.startswith("text/html")
        cli = _inbox_json(rig)
        # AIPOS-F181: page title in plain words (no home root); the CLI lines are folded per item under "show details"
        assert f'data-owner-inbox-count="{cli["count"]}"' in page and "待你决定 · 5 件" in page and str(rig["home"]) not in page.split("<details", 1)[0]
        for item in cli["items"]:
            assert f'data-item-id="{item["item_id"]}"' in page
        # AIPOS-F180: 页面套 F174 骨架(含语言切换内联脚本); 无授权请求 = 不载签发脚本、无表单(仍是只读展示)
        assert "GATE_DESIGN_FORK" in page and "lybra owner decide --item" in page
        assert 'src="/envelopes.js"' not in page and "<form" not in page and "data-gr-action" not in page
        status, _ctype, api = get("/api/owner/inbox")
        data = json.loads(api)
        assert data["ok"] and [i["item_id"] for i in data["data"]["items"]] == [i["item_id"] for i in cli["items"]], "API 与 CLI 同一数据"
        unauth = urllib.request.Request(base + "/owner/inbox")
        opener = urllib.request.build_opener(_NoRedirect)
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(unauth, timeout=10)
        assert exc.value.code in (302, 401), "页面受看板鉴权闸门保护"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 — 不跟随, 让 302 原样抛出
        return None


# ===========================================================================
# ⑥ 声明 / 单一实现不变量
# ===========================================================================

def test_declarations_and_single_implementations():
    from tools.aipos_cli import owner_inbox
    from tools.aipos_cli.governance_commit import post_write_landing_declaration
    from tools.aipos_cli.verb_contract import declared_exit_codes

    decl = owner_inbox.inbox_declaration()
    assert set(decl["categories"]) == set(owner_inbox.CATEGORIES)
    assert {"owner_decide", "loop_note"} <= set(post_write_landing_declaration()["commands"])
    assert declared_exit_codes("lybra_owner_inbox") == {"ok": 0, "partial": 4, "usage": 2}
    assert declared_exit_codes("lybra_owner_decide")["not_found"] == 4
    src = Path(owner_inbox.__file__).read_text(encoding="utf-8")
    # 裁决只经既有 owner_decision_record 门侧实现 + 既有队列出口 + 同一落账口; 不自写记录 / 不自起 git
    assert "record_owner_decision(" in src and "execute_dry_run(" in src and "land_after_write(" in src  # AIPOS-F213: 写后落账唯一薄壳
    assert "write_text(" not in src and "subprocess" not in src and "\"git\"" not in src
    for literal in ("/home/", "2_projects", "chris", "kiwiai"):
        assert literal not in src.split('"""', 2)[2], f"产品代码写死 {literal!r}"
    from tools.aipos_cli import queue_mutation

    assert queue_mutation.rework_max_rounds(Path("/nonexistent")) == 2
    qsrc = Path(queue_mutation.__file__).read_text(encoding="utf-8")
    assert qsrc.count("rework_limit\", {}).get(\"max_rounds\"") == 1, "返工上限读取只一处(rework_max_rounds)"
