"""AIPOS-F222 — 返工上限仲裁闭环: ①快车道卡(F221 顾问复核打回)返工达上限进 Owner 收件箱; ②loop note 写的 owner_ask 在运行非「等 Owner」
时不再被静默丢弃(进收件箱 / 并入返工上限事项, 回显事项 ID; 进不了 = 拒写指路); ③返工上限事项新增「授权本卡追加一轮返工」(extend_rework):
每个 Owner 裁决只放一轮, 追加的返工轮记 owner_extension_ref, 已消费的裁决不再计入。

靶场 = F90 进程内门 + loop 全链靶场(临时治理根 + 临时 HOME; 禁真门 / 真治理根 / 真工位 / 起 pi), 复用 F221 快车道构件(禁第二份):
件①② 卡面自造人肉期 LANTU-DEMO1 形态(task_mode=config, lane.repo=governance_root, 不声明独立审计 = 快车道): 2 轮打回 → 第 3 轮打回被拒
(预演) → loop note owner_ask(运行 manual_required)= 收件箱列 loop 事项(含 extend_rework)→ 第 3 轮打回(正式)= 复核记录落、升返工上限事项,
owner_ask 并入不重复 → CLI + 看板 API 同列 → Owner decide extend_rework(预演 + 确认)→ loop status / next 顾问侧读到裁决与命令 → 第 3 轮通过
(owner_extension_ref = 决策记录 ID, 复用复核记录)→ 重交回 → 第 4 轮再拒(裁决已消费)。审计裁决型卡同样走通一遍(queue rework)。
验收原文经 _show 打到 stdout(RETURN 证据)。项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f168_owner_inbox as f168  # noqa: E402  — 看板靶场构件(禁第二份)
import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402
import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402  — 快车道构件(禁第二份)
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _fm, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402

_show, _pick, _quiet = f218._show, f218._pick, f218._quiet
CARD = f221.CARD
POLICY = f221.POLICY
OWNER_WORDS = ("--relayed-by", DRIVER, "--owner-words", "演示要用, 这张卡再改一轮")


def _setup_fast_lane(rig) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(rig.gov / "5_tasks" / "policies" / f"{POLICY}.md", build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f222", task_selector_task_mode="config", launch_harnesses=["pi"]))
    f78._card(rig.gov, CARD, "pending", task_mode="config",
              extra={"harness": "claude-code", "audit": "none", "output_target": f"task_cards/{CARD}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{CARD}/artifacts/"], "roles": ["executor"]}})
    init_governance_repo(rig.gov)


def _rewrite_and_reloop(rig, version: str, bump: int) -> str:
    """执行体按返工重写交回(报告改于当前之后)→ loop 续跑重交回; 返回 loop 输出。"""
    f221._report(rig, version)
    os.utime(rig.gov / "task_cards" / CARD / "RETURN.md", ((datetime.now(timezone.utc) + timedelta(seconds=bump)).timestamp(),) * 2)
    _res, text = f221._loop(rig, [], lambda r: None)
    assert f"run return @ fast_lane_rework/claimed ({CARD})" in text, text
    return text


@contextlib.contextmanager
def _past(monkeypatch, review_ago: int | None, round_ago: int | None):
    """复核记录 / 返工轮登记时刻取过去(秒级时刻: 同一秒内连做两次复核会撞同名记录、轮次登记与下一次复核同秒会被认作「针对」它)。"""
    from tools.aipos_cli import board_adapter, queue_mutation

    now = datetime.now(timezone.utc).replace(microsecond=0)
    with monkeypatch.context() as m:
        if review_ago is not None:
            m.setattr(board_adapter, "iso_ms", lambda: f221._iso(now - timedelta(seconds=review_ago)))  # AIPOS-F232: 复核时刻改 iso_ms
        if round_ago is not None:
            m.setattr(queue_mutation, "iso_ms", lambda: f221._iso(now - timedelta(seconds=round_ago)))  # AIPOS-F232: 登记时刻改 iso_ms
        yield


def _inbox(home: Path) -> tuple[int, str, dict]:
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(home)])
    rc2, js, _err = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    return rc, out + err, json.loads(js)


def _card_items(data: dict, task_id: str) -> list[dict]:
    return [i for i in data["items"] if i.get("task_id") == task_id]


def _note(rig, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "note", "--task-id", CARD,
                         "--ask", "返工已达上限仍不通过: 请 Owner 定是否授权本卡再改一轮(27 日演示要用)",
                         "--options", "arbitrate=Owner 仲裁", "hold=暂缓", "--recommend", "建议授权追加一轮: 只差超时清理一项",
                         "--owner-reason", "rework_cap_exceeded", "--actor", DRIVER, *extra])  # AIPOS-F225: 升 Owner 必带第一档事由
    return rc, out + err


def _decide(home: Path, item_id: str, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["owner", "decide", "--item", item_id, "--choice", "extend_rework", "--home-root", str(home), *OWNER_WORDS,
                         *extra])
    return rc, out + err


def _board_api(home: Path, repo_root: Path, monkeypatch) -> tuple[dict, str]:
    from http.server import ThreadingHTTPServer

    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'])}"
    server = ThreadingHTTPServer(("127.0.0.1", f168._free_port()), make_handler(repo_root=repo_root, session_store=store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        def get(path: str) -> str:
            with urllib.request.urlopen(urllib.request.Request(base + path, headers={"Cookie": cookie}), timeout=60) as resp:
                return resp.read().decode("utf-8")

        return json.loads(get("/api/owner/inbox")), get("/owner/inbox")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


# ===========================================================================
# 件①②③ 快车道卡: LANTU-DEMO1 形态全链
# ===========================================================================

def test_fast_lane_limit_reaches_inbox_extend_rework_one_round(rig, monkeypatch):  # noqa: F811
    from tools.aipos_cli import loop_run_record as lrr

    home = rig.gov.parent
    _setup_fast_lane(rig)
    f221._loop(rig, [], lambda r: f221._report(r, "v1"))
    with _past(monkeypatch, 400, 390):
        rc, payload, err = f221._rework(rig, f221.FOCUS1, f221.CRITERIA1)
    assert rc == 0, f221._reason(payload, err)
    _rewrite_and_reloop(rig, "v2", 2)
    with _past(monkeypatch, 300, 290):
        rc, payload, err = f221._rework(rig, ["第 2 轮: 超时清理未实现"])
    assert rc == 0 and payload["data"]["round_added"] == 2, f221._reason(payload, err)
    _rewrite_and_reloop(rig, "v3", 4)

    # 第 3 次打回(预演)被拒: 拒因附升仲裁出口; 零写入
    rc, payload, err = f221._rework(rig, ["第 3 轮: 超时清理仍缺"], dry_run=True)
    why = f221._reason(payload, err)
    _show(f"---- ① 第 3 轮打回 --dry-run(上限 2)rc={rc} ----\n{why}")
    assert rc == 1 and "REVIEW_REWORK_ROUND_REFUSED" in why and "返工轮次已达上限 (2 轮)" in why
    assert "extend_rework" in why and "去掉 --dry-run 重跑 = 记录本次复核不通过并升 Owner 收件箱" in why and len(f221._events(rig)) == 2

    # ② loop note owner_ask(该运行 manual_required: 下一动作 = 顾问复核, 非等 Owner)→ 进收件箱(loop 事项, 返工额度已尽 = 带 extend_rework)
    status = lrr.loop_status(rig.gov, CARD)
    run = status["runs"][-1]
    _show(f"[② 运行] {run['run_id']} end_reason={run.get('end_reason')} 下一动作={run['next_action']['action']}({run['next_action']['reason']})")
    assert run["next_action"]["action"] != "owner_needed"
    rc, text = _note(rig)
    _show(f"---- ② loop note 预演 rc={rc} ----\n{text}")
    assert rc == 0 and "确认后进 Owner 收件箱: OI-" in text
    rc, text = _note(rig, "--confirm")
    _show(f"---- ② loop note --confirm rc={rc} ----\n{text}")
    assert rc == 0 and "已进 Owner 收件箱: OI-" in text and "loop 停下等 Owner" in text
    rc, out, data = _inbox(home)
    items = _card_items(data, CARD)
    _show(f"---- ② 收件箱(note 后, 复核尚未正式记录)rc={rc} ----\n{out}")
    assert len(items) == 1 and items[0]["category"] == "loop_owner_needed" and items[0]["facts"]["reason"] == "advisor_ask"
    assert items[0]["recommend"] == "建议授权追加一轮: 只差超时清理一项"
    assert [o["key"] for o in items[0]["options"]] == ["arbitrate", "hold", "extend_rework"]
    rc, st, err = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", CARD])
    _show(f"---- ② loop status(note 后)----\n{st[-900:]}")
    assert f"已进 Owner 收件箱: {items[0]['item_id']}" in st

    # ① 第 3 次打回(正式)= 复核记录照落(无返工轮)、升返工上限事项; owner_ask 并入, 不重复列
    rc, payload, err = f221._rework(rig, ["第 3 轮: 超时清理仍缺"])
    why = f221._reason(payload, err)
    events = f221._events(rig)
    _show(f"---- ① 第 3 轮打回(正式)rc={rc} ----\n{why}\n[复核记录] {events[-1].name}")
    assert rc == 1 and "REVIEW_REWORK_ESCALATED" in why and len(events) == 3 and len(f221._rounds(rig)) == 2
    review3 = payload["data"]["review_record"]["review_id"]
    assert nr._read_frontmatter(events[-1]).get("rework_round") in (None, "")
    rc, out, data = _inbox(home)
    items = _card_items(data, CARD)
    _show(f"---- ①② 收件箱 CLI(升返工上限事项后)rc={rc} ----\n{out}")
    assert len(items) == 1, "owner_ask 并入返工上限事项, 不另列 loop 事项"
    item = items[0]
    assert item["category"] == "rework_limit" and item["item_id"] in why
    assert f"最新顾问复核 {review3}" in item["ask"] and "已返工 2/2 轮" in item["ask"] and "非代码快车道卡" in item["ask"]
    assert item["recommend"] == "建议授权追加一轮: 只差超时清理一项" and item["relay_to"] == DRIVER
    assert [o["key"] for o in item["options"]] == ["extend_rework", "new_card", "rescope", "withdraw", "acknowledge", "arbitrate", "hold"]
    api, page = _board_api(home, rig.gov, monkeypatch)
    _show("---- ① 看板 /api/owner/inbox 该卡事项 ----\n" + json.dumps(_card_items(api["data"], CARD), ensure_ascii=False)[:1500])
    assert [i["item_id"] for i in _card_items(api["data"], CARD)] == [item["item_id"]]
    assert f'data-item-id="{item["item_id"]}"' in page and 'data-choice="extend_rework"' in page and "授权本卡追加一轮返工" in page
    rc, text = _note(rig)
    _show(f"---- ② loop note 预演(已并入)----\n{text}")
    assert rc == 0 and f"确认后进 Owner 收件箱: {item['item_id']}(返工轮次达上限, 并入该卡返工上限事项)" in text

    # ③ Owner decide extend_rework: 预演零写入 → 确认落既有决策记录
    rc, text = _decide(home, item["item_id"])
    _show(f"---- ③ owner decide extend_rework 预演 rc={rc} ----\n{text}")
    assert rc == 0 and "预演(零写入" in text
    rc, text = _decide(home, item["item_id"], "--confirm")
    _show(f"---- ③ owner decide extend_rework --confirm rc={rc} ----\n{text}")
    assert rc == 0 and "落账: verdict=PASS" in text
    decision = item["decision_id"]
    record = (rig.gov / "5_tasks" / "records" / "owner_decisions" / f"{decision}.md").read_text(encoding="utf-8")
    assert "allowed_next_action: extend_rework" in record and f"task_id: {CARD}" in record
    rc, st, err = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", CARD])
    _show(f"---- ③ 顾问侧 loop status(裁决后)----\n{st[-1200:]}")
    assert "advisor_fix(owner_ruled)" in st and "选 extend_rework(授权本卡追加一轮返工" in st and "--outcome rework" in st
    rc, nxt, err = _cli(["--workspace-root", str(rig.gov), "next", "--task-id", CARD])
    _show(f"---- ③ 顾问侧 lybra next --task-id(裁决后)原文尾 ----\n{nxt[-900:]}")
    assert "Owner 已裁决: 选 extend_rework" in nxt and "mark-concluded --task-id" in nxt and "--outcome rework" in nxt
    rc, out, data = _inbox(home)
    assert not _card_items(data, CARD), "已裁决 = 不再列"
    relay = [e for g in data.get("relays") or [] for e in g.get("entries") or [] if e.get("task_id") == CARD]
    _show(f"[③ 已定待转达] {json.dumps(relay, ensure_ascii=False)[:800]}")
    assert len(relay) == 1 and relay[0]["choice"] == "extend_rework" and "--outcome rework" in relay[0]["advisor_next"]

    # ③ 第 3 轮按裁决通过: 复用复核记录, 轮次记 owner_extension_ref = 决策记录 ID
    with _past(monkeypatch, None, 100):
        rc, payload, err = f221._rework(rig, ["第 3 轮: 超时清理(Owner 授权追加)"])
    _show(f"---- ③ 第 3 轮打回(裁决后)rc={rc} ----\n" + _pick(payload.get("data") or {}, "round_added", "owner_extension_ref",
                                                                     "review_record", "rework_round"))
    assert rc == 0 and payload["data"]["round_added"] == 3, f221._reason(payload, err)
    rounds = f221._rounds(rig)
    assert rounds[-1]["owner_extension_ref"] == decision and rounds[-1]["verdict_ref"] == review3 and len(f221._events(rig)) == 3
    assert payload["data"]["review_record"]["reused"] is True

    # 登记后续跑(裁决的下一步): 执行体按第 3 轮重写交回 → 重交回; 裁决已执行 = 不再列也不再转达
    _rewrite_and_reloop(rig, "v4", 6)
    rc, out, data = _inbox(home)
    _show(f"---- ③ 收件箱(第 3 轮登记并重交回后)----\n{out}")
    assert not _card_items(data, CARD) and not [e for g in data.get("relays") or [] for e in g.get("entries") or []
                                                if e.get("task_id") == CARD], "裁决已执行 = 不再列也不再转达"

    # 第 4 轮再拒(裁决已消费)
    rc, payload, err = f221._rework(rig, ["第 4 轮"], dry_run=True)
    why = f221._reason(payload, err)
    _show(f"---- ③ 第 4 轮打回(裁决已消费)rc={rc} ----\n{why}")
    assert rc == 1 and "返工轮次已达上限 (2 轮; 另含 Owner 授权追加 1 轮, 已用完)" in why and "当前已有 3 轮" in why
    rc, payload, err = f221._rework(rig, ["第 4 轮"])
    assert rc == 1 and "REVIEW_REWORK_ESCALATED" in f221._reason(payload, err)
    rc, out, data = _inbox(home)
    items = _card_items(data, CARD)
    _show(f"---- ③ 收件箱(第 4 轮被拒后)----\n{out}")
    assert len(items) == 1 and items[0]["category"] == "rework_limit" and items[0]["item_id"] != item["item_id"]
    assert "已返工 3/3 轮(含 Owner 授权追加 1 轮)" in items[0]["ask"]


# ===========================================================================
# 件③ 审计裁决型卡: 同一上限判据与同一裁决出口
# ===========================================================================

def _verdict(gov: Path, task_id: str, n: int, at: str) -> str:
    from tools.aipos_cli.record_writer import record_file_prefix

    prefix = record_file_prefix("audit_verdicts")
    vid = f"{prefix}_{task_id}_r{n}"
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / task_id / f"{vid}.md", _fm({
        "record_type": "audit_verdict_record", "verdict_id": vid, "verdict": "FAIL", "reviewed_task_id": task_id,
        "audit_task_id": f"{task_id}R{n}", "verdict_at": f"'{at}'", "actor": "audit.lybra.test"}))  # 引号 = 落盘为字符串(同门写口)
    return vid


def _queue_rework(gov: Path, task_id: str, verdict_ref: str, *, dry_run: bool) -> dict:
    from tools.aipos_cli.board_adapter import queue_rework_task

    return queue_rework_task(task_id=task_id, verdict_ref=verdict_ref, focus_items=["修 FAIL 点"], actor=DRIVER, agent_instance=DRIVER,
                             dry_run=dry_run, repo_root=gov)


def test_audit_verdict_card_extend_rework_one_round(rig):  # noqa: F811
    task = "PROBE-F222-A"
    home = rig.gov.parent
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": DRIVER,
               "focus_items": ["x"]} for n in (1, 2)]
    f78._card(rig.gov, task, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'",
                                                "rework_rounds": rounds})
    init_governance_repo(rig.gov)
    v3 = _verdict(rig.gov, task, 3, "2026-10-09T00:00:00Z")
    res = _queue_rework(rig.gov, task, v3, dry_run=True)
    _show(f"---- ③ 审计裁决型卡 queue rework 第 3 轮(上限 2)----\n{res.get('blocking_reasons')}")
    assert res["verdict"] == "BLOCK" and "extend_rework" in res["blocking_reasons"][0]
    rc, out, data = _inbox(home)
    items = _card_items(data, task)
    _show(f"---- ③ 收件箱(审计裁决型卡)----\n{out}")
    assert len(items) == 1 and items[0]["category"] == "rework_limit" and f"最新裁决 FAIL({v3})" in items[0]["ask"]
    assert items[0]["options"][0]["key"] == "extend_rework"
    rc, text = _decide(home, items[0]["item_id"], "--confirm")
    assert rc == 0 and "落账: verdict=PASS" in text, text
    res = _queue_rework(rig.gov, task, v3, dry_run=True)
    assert res.get("ok") and res["data"]["rework_round"]["owner_extension_ref"] == items[0]["decision_id"], json.dumps({k: res.get(k) for k in ("blocking_reasons", "errors")}, ensure_ascii=False)
    res = _queue_rework(rig.gov, task, v3, dry_run=False)
    card = f218._card_fm(rig.gov, task)
    _show(f"---- ③ queue rework 第 3 轮(裁决后, 确认)----\n{json.dumps(card['rework_rounds'][-1], ensure_ascii=False)}")
    assert res.get("ok") and card["rework_rounds"][-1]["owner_extension_ref"] == items[0]["decision_id"]
    assert not _card_items(_inbox(home)[2], task)
    v4 = _verdict(rig.gov, task, 4, f221._iso(datetime.now(timezone.utc) + timedelta(seconds=5)))  # 第 3 轮登记之后的新 FAIL
    res = _queue_rework(rig.gov, task, v4, dry_run=True)
    _show(f"---- ③ queue rework 第 4 轮(裁决已消费)----\n{res.get('blocking_reasons')}")
    assert res["verdict"] == "BLOCK" and "另含 Owner 授权追加 1 轮, 已用完" in res["blocking_reasons"][0]
    items = _card_items(_inbox(home)[2], task)
    assert len(items) == 1 and "已返工 3/3 轮(含 Owner 授权追加 1 轮)" in items[0]["ask"] and f"({v4})" in items[0]["ask"]


# ===========================================================================
# 件② 进不了收件箱 = 拒写指路; 声明 / 单一实现
# ===========================================================================

def test_loop_note_not_listed_refused(rig):  # noqa: F811
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    task = "PROBE-F222-DONE"
    f78._card(rig.gov, task, "completed")
    rec = LoopRunRecorder(rig.gov, task, driver=DRIVER, contract=load_loop_contract(), warn=_show)
    rec.end(outcome="manual_required", exit_code=2, reason="manual_required", message="fixture")
    before = rec.path.read_text(encoding="utf-8") if hasattr(rec, "path") else None
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "note", "--task-id", task, "--ask", "x", "--confirm"])
    _show(f"---- ② loop note(卡已结案)rc={rc} ----\n{out}{err}")
    assert rc == 5 and "NOT_LISTED" in err and "completed" in err
    if before is not None:
        assert rec.path.read_text(encoding="utf-8") == before, "拒写 = 零写入"


def test_declarations_single_source():
    from tools.aipos_cli import owner_inbox, queue_mutation
    from tools.aipos_cli.verb_contract import declared_exit_codes

    ext = queue_mutation.rework_extension_declaration()
    decl = owner_inbox.inbox_declaration()
    assert ext["choice"] == "extend_rework" and ext["round_field"] == "owner_extension_ref"
    assert ext["choice"] in decl["categories"]["rework_limit"]["options"] and ext["choice"] in decl["options"]
    assert decl["categories"]["loop_owner_needed"]["advisor_ask"]["pinned_when_rework_exhausted"] == [ext["choice"]]
    assert declared_exit_codes("lybra_loop_note")["not_listed"] == 5
    card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
    assert ext["round_field"] in card["fields"]["rework_rounds"]["items"]["properties"]
    qsrc = (REPO_ROOT / "tools" / "aipos_cli" / "queue_mutation.py").read_text(encoding="utf-8")
    body = qsrc.split("def build_rework_round(", 1)[1].split("\ndef ", 1)[0]
    assert "rework_round_budget(" in body, "上限判据只经 rework_round_budget"
    isrc = (REPO_ROOT / "tools" / "aipos_cli" / "owner_inbox.py").read_text(encoding="utf-8")
    core = isrc.split("def rework_limit_core(", 1)[1].split("\ndef ", 1)[0]
    assert "rework_round_budget(" in core and "advisor_review_anchor(" in core and "rework_round_for_verdict(" in core
    assert isrc.count("def advisor_review_anchor") == 0 and isrc.count("max_rounds\", 2)") == 0, "锚 / 上限不另写第二份"
    lsrc = (REPO_ROOT / "tools" / "aipos_cli" / "loop_run_record.py").read_text(encoding="utf-8")
    assert "owner_ask_destination(" in lsrc.split("def _note_destination(", 1)[1].split("\ndef ", 1)[0]


def test_f222_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f222_rework_limit_arbitration.py", default_test_contract())
