"""AIPOS-F283 — Owner 收件箱阻塞事项看得懂、选得对 + 顾问 loop note 的说明在收件箱可见。

起因(11-02 人肉期项目实撞, 本文件全部在临时根自造样本, 产品不写死项目 / 卡号):
  ① 阻塞事项只贴执行体交回的技术原文, 没说 Owner 要定什么、有哪几个选项、推荐哪个;
  ② 顾问用 `lybra loop note` 写了带选项与建议的说明, 命令提示「已写入, 但收件箱此刻未列出预期事项」(inbox_missing), Owner 看不到;
  ③ blocked 卡的选项缺「补齐前置后重开」。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi):
  A. F90 进程内门 + loop 全链靶场(F252 阻塞交回同一构件, 禁第二份): 执行体交回 outcome=blocked → loop 转 blocked → 收件箱事项首行
     (要定)= 一句话决定, 选项含 reopen 且标推荐, 技术原文降为详情(中英各一份原文); 顾问 loop note 后不再 inbox_missing, 同一事项显示
     顾问说明与选项; Owner 选 reopen = 只记意向, loop status / lybra next 给顾问以自身实例 reopen 的命令(不改队列)。
  B. F168 多项目 home 根靶场(project new + 卡面 + loop 自身记录器落运行记录): blocked / claimed / pending 三态 needs_owner 卡各一,
     loop note 后各自并入「卡待 Owner」事项, 不另列 loop 事项、不报 inbox_missing。
  C. 看板收件箱页(readonly_pages.render_owner_inbox, 与 CLI 同一 collect_inbox 数据)同口径: 一句话决定 / 推荐选项 / 折叠技术原文。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f168_owner_inbox as f168  # noqa: E402  — 多项目 home 根靶场构件(禁第二份)
import test_aipos_f252_card_policy_blocked_return as f252  # noqa: E402  — 阻塞交回构件(禁第二份)
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig as f90_rig  # noqa: E402,F401  — 夹具复用(pytest 按名注入)
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

TASK = f90.TASK
_show = f168._show
NOTE_ASK = "iOS 签名打包经 ssh 读不到登录钥匙串, 这张卡怎么出包?"
NOTE_RECOMMEND = "建议 local_cmd: 由本机终端出包, 不碰钥匙串"


def _cli(*argv: str) -> tuple[int, str]:
    return f168._cli(*[str(a) for a in argv])


def _item_block(text: str, task_id: str) -> list[str]:
    """CLI 收件箱文本里某卡那一项的行(事项头行起到空行止)。"""
    blocks = [b.splitlines() for b in text.split("\n\n")]
    found = [b for b in blocks if b and re.search(rf"卡 {re.escape(task_id)}\b|card {re.escape(task_id)}\b", b[0])]
    assert len(found) == 1, (task_id, text)
    return found[0]


def _blocked_return(r) -> None:
    """F90 靶场: 执行体交回 outcome=blocked(F252 同一构件)→ loop 转 blocked。"""
    f90._card(r.gov, TASK, "pending")
    init_governance_repo(r.gov)
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=__import__("io").StringIO(),
                   watch=f252._watch_blocked(r, []), interval=0.05, max_wait=5, max_steps=20)
    assert res.outcome == "not_derivable" and res.reason == "return_blocked"
    assert (r.gov / "5_tasks" / "queue" / "blocked" / f"{TASK.lower()}.md").is_file()


def _inbox_text(gov: Path, *global_args: str) -> tuple[int, str]:
    return _cli(*global_args, "owner", "inbox", "--home-root", gov.parent, "--project", gov.name)


def _inbox_items(gov: Path, task_id: str) -> list[dict]:
    rc, out = _cli("owner", "inbox", "--home-root", gov.parent, "--project", gov.name, "--json")
    data = json.loads(out)
    assert rc == 0 and not data["errors"], out
    return [i for i in data["items"] if i.get("task_id") == task_id]


# ===========================================================================
# 件①② 交回阻塞事项: 一句话决定 + 选项含 reopen 且标推荐 + 技术原文降为详情(中英原文)
# ===========================================================================

def test_item1_blocked_return_headline_options_reopen_recommended_zh_en(f90_rig):
    r = f90_rig
    _blocked_return(r)
    rc, zh = _inbox_text(r.gov)
    block = _item_block(zh, TASK)
    _show("---- 件① owner inbox(zh)交回阻塞事项原文 ----\n" + "\n".join(block))
    assert rc == 0
    assert block[1].startswith("  要定: 执行体按卡停线交回阻塞(outcome=blocked, 卡面验收未达成; 阻塞原因见详情): 要你定这张卡下一步")
    options = next(ln for ln in block if ln.startswith("  选项: "))
    assert options.startswith("  选项: reopen=补齐前置后重开(顾问执行; 开新一轮续跑)【推荐】 / withdraw=")
    assert any(ln.startswith("  推荐: 补齐前置后重开(顾问执行; 开新一轮续跑) — 执行体按卡停线并写明了缺的前置") and ln.endswith("(产品缺省)")
               for ln in block)
    detail = next(ln for ln in block if ln.startswith("  详情: "))
    assert "交回声明阻塞(outcome=blocked" in detail and "queue reopen --task-id" in detail  # 技术原文(含续跑出口)整段留在详情
    assert "交回声明阻塞" not in block[1]
    rc, en = _inbox_text(r.gov, "--lang", "en")
    eblock = _item_block(en, TASK)
    _show("---- 件① owner inbox --lang en 交回阻塞事项原文 ----\n" + "\n".join(eblock))
    assert rc == 0
    assert eblock[1].startswith("  To decide: The executor stopped at the card's line and returned it blocked (outcome=blocked")
    eopt = next(ln for ln in eblock if ln.startswith("  Options: "))
    assert eopt.startswith("  Options: reopen=reopen once the prerequisites are in place (done by the advisor; a new round) [recommended] / ")
    assert any(ln.startswith("  Recommended: reopen once the prerequisites are in place") and ln.endswith("; product default)") for ln in eblock)
    assert any(ln.startswith("  Details: ") for ln in eblock)
    items = _inbox_items(r.gov, TASK)
    assert [i["category"] for i in items] == ["needs_owner_card"]
    item = items[0]
    assert item["facts"]["return_outcome"] == "blocked" and item["recommended"]["choice"] == "reopen"
    assert item["recommended"]["source"] == "default" and item["recommended"]["action"] == "decide"
    assert [o["key"] for o in item["options"]] == ["reopen", "withdraw", "freeze_legacy", "new_card", "acknowledge"]


def test_item2_owner_picks_reopen_records_intent_only_advisor_exit_shows_reopen(f90_rig):
    """选 reopen = 记录 Owner 意向(既有 owner decide 同一路径), 卡不动; 顾问侧出口(loop status / lybra next)给 reopen 命令。"""
    r = f90_rig
    _blocked_return(r)
    item = _inbox_items(r.gov, TASK)[0]
    card = r.gov / "5_tasks" / "queue" / "blocked" / f"{TASK.lower()}.md"
    before = card.read_text(encoding="utf-8")
    _past_run_start_second(r.gov)
    rc, out = _cli("owner", "decide", "--item", item["item_id"], "--choice", "reopen", "--relayed-by", f78.DRIVER, "--owner-words",
                   "前置补上后重开", "--home-root", r.gov.parent, "--confirm")
    _show(f"---- 件② owner decide --choice reopen exit {rc} ----\n{out}")
    assert rc == 0, out
    assert card.read_text(encoding="utf-8") == before, "收件箱裁决不改队列(reopen 由顾问执行)"
    rc, status = _cli("loop", "status", "--task-id", TASK, "--workspace-root", r.gov)
    _show(f"---- 件② loop status(裁决后)exit {rc} ----\n{status}")
    want = f"queue reopen --task-id {TASK} --actor <你的顾问实例>"
    assert "选 reopen(补齐前置后重开(顾问执行; 开新一轮续跑))" in status and want in status
    rc, nxt = _cli("--workspace-root", r.gov, "next", "--task-id", TASK)
    _show(f"---- 件② lybra next --task-id(裁决后)exit {rc} ----\n{nxt}")
    assert want in nxt and "Owner 已裁决: 选 reopen" in nxt
    rc, text = _inbox_text(r.gov)
    _show(f"---- 件② 收件箱(裁决后, 已定待转达)----\n{text}")
    assert "无待定事项" in text.splitlines()[0] and "已定待转达" in text and want in text  # 已定不再待定; 转达段给顾问 reopen 命令


def _past_run_start_second(gov: Path) -> None:
    """loop status 补读裁决按秒比较「裁决晚于本卡最近一次运行开始」(owner_inbox.pending_ruling_lines, 既有判定): 靶场 loop 一秒内跑完, 裁决须落在
    运行开始的下一秒或之后——只等到跨过该秒界(一次有界等待, 不轮询)。"""
    import time
    from datetime import datetime, timezone

    from tools.aipos_cli.owner_inbox import _run_metas

    started = datetime.fromisoformat(str(_run_metas(gov, TASK)[-1]["started_at"]).replace("Z", "+00:00"))
    gap = (started.replace(microsecond=0).timestamp() + 1.05) - datetime.now(timezone.utc).timestamp()
    if gap > 0:
        time.sleep(gap)


# ===========================================================================
# 件③ 顾问 loop note 写在交回阻塞的运行上: 不再 inbox_missing, 同一事项显示顾问说明与选项
# ===========================================================================

def test_item3_loop_note_on_blocked_return_is_listed_in_same_item(f90_rig):
    r = f90_rig
    _blocked_return(r)
    before = _inbox_items(r.gov, TASK)
    argv = ["--workspace-root", r.gov, "loop", "note", "--task-id", TASK, "--ask", NOTE_ASK,
            "--options", "local_cmd=本机终端出包(推荐)", "xcode_manual=Owner 在 Xcode 手动 Archive", "--recommend", NOTE_RECOMMEND,
            "--owner-reason", "beyond_authority", "--actor", f78.DRIVER]
    rc, preview = _cli(*argv)
    _show(f"---- 件③ loop note 预演 exit {rc} ----\n{preview}")
    assert rc == 0 and f"确认后进 Owner 收件箱: {before[0]['item_id']}(卡待 Owner(needs_owner), 并入该卡「卡待 Owner」事项" in preview
    rc, out = _cli(*argv, "--confirm")
    _show(f"---- 件③ loop note --confirm exit {rc} ----\n{out}")
    assert rc == 0, out
    assert "收件箱此刻未列出" not in out and f"已进 Owner 收件箱: {before[0]['item_id']}" in out
    items = _inbox_items(r.gov, TASK)
    assert [i["category"] for i in items] == ["needs_owner_card"] and items[0]["item_id"] == before[0]["item_id"]
    item = items[0]
    assert item["ask"] == NOTE_ASK and item["recommend"] == NOTE_RECOMMEND
    assert [o["key"] for o in item["options"]] == ["reopen", "withdraw", "freeze_legacy", "new_card", "acknowledge", "local_cmd", "xcode_manual"]
    assert item["recommended"] is None, "顾问 loop note 写了建议 = 不用产品缺省推荐"
    assert item["relay_to"] == f78.DRIVER and "交回声明阻塞" in item["detail"]
    rc, text = _inbox_text(r.gov)
    block = _item_block(text, TASK)
    _show("---- 件③ owner inbox(zh)loop note 后同一事项原文 ----\n" + "\n".join(block))
    assert block[1] == f"  要定: {NOTE_ASK}(第一档事由: 超出项目授权 beyond_authority)"
    assert f"  顾问建议: {NOTE_RECOMMEND}" in block and any("local_cmd=本机终端出包(推荐)" in ln for ln in block)
    rc, status = _cli("loop", "status", "--task-id", TASK, "--workspace-root", r.gov)
    _show(f"---- 件③ loop status(note 后)----\n{status}")
    assert f"已进 Owner 收件箱: {item['item_id']}" in status


# ===========================================================================
# 件③ 三态(blocked / claimed / pending)needs_owner 卡各一: loop note 并入「卡待 Owner」事项, 不另列 loop 事项
# ===========================================================================

@pytest.fixture
def home_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_LANG"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    project = "gamma"
    rc, out = _cli("project", "new", project, "--home-root", hroot, "--actor", f168.OWNER)
    assert rc == 0, out
    gov = hroot / project
    driver = f"advisor.{project}.h1"
    cards = {"blocked": "GAMMA-1", "claimed": "GAMMA-2", "pending": "GAMMA-3"}
    extra = {"blocked": {"blocked_by": driver, "blocked_at": "2026-10-01T00:00:00Z", "block_reason": "依赖方接口未定"},
             "claimed": {"claimed_by": f"exec.{project}.h1", "claimed_at": "2026-10-01T00:00:00Z"}, "pending": {}}
    for state, task_id in cards.items():
        f168._card(gov, task_id, state, project, needs_owner=True, owner_reason="beyond_authority", **extra[state])
        f168._owner_needed_run(gov, task_id, driver, reason="gate_rejected", message="GATE_REFUSED: 夹具门拒")
    f168._card(gov, "GAMMA-9", "claimed", project)  # 对照: 无 needs_owner 的在途卡
    f168._owner_needed_run(gov, "GAMMA-9", driver, reason="gate_rejected", message="GATE_REFUSED: 夹具门拒")
    init_governance_repo(hroot)
    return {"home": hroot, "gov": gov, "driver": driver, "cards": cards}


@pytest.mark.parametrize("state", ["blocked", "claimed", "pending"])
def test_item3_loop_note_three_queue_states_merge_into_needs_owner_item(home_rig, state):
    gov, task_id, driver = home_rig["gov"], home_rig["cards"][state], home_rig["driver"]
    rc, out = _cli("owner", "inbox", "--home-root", home_rig["home"], "--json")
    before = [i for i in json.loads(out)["items"] if i.get("task_id") == task_id]
    assert [i["category"] for i in before] == ["needs_owner_card"], before
    head = before[0]["ask"]
    _show(f"[件③ {state}] 改前要定(声明一句话): {head}\n  详情: {before[0]['detail']}")
    assert "要你定" in head
    ask = f"{task_id}: 夹具请求, 请 Owner 定方向({state})"
    rc, out = _cli("--workspace-root", gov, "loop", "note", "--task-id", task_id, "--ask", ask, "--options", "plan_a=方案 A",
                   "--recommend", "建议 plan_a", "--owner-reason", "direction_fork", "--actor", driver, "--confirm")
    _show(f"---- 件③ {state} 卡 loop note --confirm exit {rc} ----\n{out}")
    assert rc == 0, out
    assert "收件箱此刻未列出" not in out and f"已进 Owner 收件箱: {before[0]['item_id']}(卡待 Owner(needs_owner)" in out
    rc, out = _cli("owner", "inbox", "--home-root", home_rig["home"], "--json")
    items = [i for i in json.loads(out)["items"] if i.get("task_id") == task_id]
    _show(f"[件③ {state}] note 后事项: " + json.dumps([{k: i[k] for k in ("item_id", "category", "ask", "recommend")} for i in items],
                                                     ensure_ascii=False))
    assert [i["category"] for i in items] == ["needs_owner_card"], "并入「卡待 Owner」事项, 不另列 loop 事项"
    assert items[0]["item_id"] == before[0]["item_id"] and items[0]["ask"] == ask and items[0]["recommend"] == "建议 plan_a"
    assert items[0]["options"][-1] == {"key": "plan_a", "label": "方案 A"}
    assert items[0]["detail"] == before[0]["detail"]


def test_loop_note_without_needs_owner_card_unchanged(home_rig):
    """无 needs_owner 的在途卡: 顾问请求照旧列 loop 事项(F222 既有路径不变)。"""
    gov, driver = home_rig["gov"], home_rig["driver"]
    rc, out = _cli("--workspace-root", gov, "loop", "note", "--task-id", "GAMMA-9", "--ask", "定方向", "--owner-reason", "direction_fork",
                   "--actor", driver, "--confirm")
    _show(f"---- 无 needs_owner 卡 loop note exit {rc} ----\n{out}")
    assert rc == 0 and "已进 Owner 收件箱" in out and "(loop 停下等 Owner)" in out
    rc, out = _cli("owner", "inbox", "--home-root", home_rig["home"], "--json")
    assert [i["category"] for i in json.loads(out)["items"] if i.get("task_id") == "GAMMA-9"] == ["loop_owner_needed"]


# ===========================================================================
# 看板收件箱页同口径(页面 HTML 断言)
# ===========================================================================

def test_board_inbox_page_same_headline_recommended_option_and_folded_detail(f90_rig):
    import html as _html

    from web.board.readonly_pages import render_owner_inbox

    r = f90_rig
    _blocked_return(r)
    item = _inbox_items(r.gov, TASK)[0]
    page = render_owner_inbox(r.gov.parent, {}, owner=False)
    sec = page.split(f"id=\"item-{item['item_id']}\"", 1)[1].split("</section>", 1)[0]
    _show(f"---- 看板收件箱页(只读会话)事项 HTML ----\n{sec[:2400]}")
    assert f"<p class=\"ib-ask\">要你做什么:{_html.escape(str(item['ask']))}</p>" in sec
    assert "<p class=\"ib-options\" data-recommended=\"reopen\">可选:补齐前置后重开(顾问执行; 开新一轮续跑)(推荐) / 撤卡(不再做)" in sec
    assert "<details class=\"ib-tech-detail\"><summary>技术原文(执行体交回 / 阻塞原因)</summary><pre>needs_owner is true; 交回声明阻塞(outcome=blocked" in sec
    owner_page = render_owner_inbox(r.gov.parent, {}, owner=True)
    osec = owner_page.split(f"id=\"item-{item['item_id']}\"", 1)[1].split("</section>", 1)[0]
    _show(f"---- 看板收件箱页(Owner 会话)裁决按钮 ----\n{osec[osec.find('ib-decide-actions'):][:900]}")
    assert "data-ib-choice=\"reopen\" data-ib-label=\"补齐前置后重开(顾问执行; 开新一轮续跑)\" data-recommended=\"true\"" in osec
    assert osec.index("data-ib-choice=\"reopen\"") < osec.index("data-ib-choice=\"withdraw\"")  # 推荐的排第一
    en = render_owner_inbox(r.gov.parent, {"lang": ["en"]}, owner=False)
    esec = en.split(f"id=\"item-{item['item_id']}\"", 1)[1].split("</section>", 1)[0]
    _show(f"---- 看板收件箱页(en)----\n{esec[:1600]}")
    assert "What you are asked: The executor stopped at the card&#x27;s line" in esec
    assert "reopen once the prerequisites are in place (done by the advisor; a new round) (Recommended)" in esec


# ===========================================================================
# 声明 / 单一实现
# ===========================================================================

def test_declarations_single_implementation():
    from tools.aipos_cli import owner_inbox, owner_recommend

    decl = owner_inbox.inbox_declaration()
    cat = decl["categories"]["needs_owner_card"]
    assert set(cat["headline_by_queue_state"]) == set(cat["queue_states"]) and cat["headline_return_blocked"]
    assert cat["options_by_queue_state"]["blocked"][0] == "reopen" and not decl["options"]["reopen"].get("effect")
    assert owner_inbox.inbox_overlay_problems(decl) == []  # 新文案中英齐(F277 覆盖层守卫)
    rdecl = owner_recommend.recommend_declaration()
    spec = rdecl["defaults"]["return_blocked_reopen"]
    assert spec["category"] == "needs_owner_card" and spec["choice"] == "reopen" and spec["action"] == "decide"
    assert owner_inbox.NOTE_CATEGORIES == ("loop_owner_needed", "rework_limit", "needs_owner_card")
    src = Path(owner_inbox.__file__).read_text(encoding="utf-8")
    assert src.count("get_needs_owner(") == 1, "needs_owner 筛选只经 _needs_owner_tasks 一处"
    assert src.count("_needs_owner_identity(") == 3  # 定义 + 收件箱 + 判归, 事项身份同一构造
