"""AIPOS-F228 — F225(介入分级)× F226(看板一键裁决)跨卡衔接守卫: 看板收件箱「已自动放行」栏(设计稿
governance/research/2026-10-24-owner-intervention-tiers-design.md §5)。

件② 回归守卫: 第二档事项(自动放行记录 = owner_inbox.revocable_notices, 与 CLI 同读 collect_inbox)出现在看板「已自动放行」栏、不计待定件数;
Owner 会话一键撤回(F226 同一写路由 envelope_board.handle_owner_write + owner_inbox.decide, 不另立)→ 卡停住(推导核 / loop 以
auto_release_revoked 结束, 第一档事由 withdraw); 第一档事项仍在待定区一键裁决(撤回后的停住事项 / 返工上限事项)。

靶场 = F90 进程内门替身 + 临时治理根 / 临时 HOME + 测试进程内线程起的看板(F226 Board 构件, 测试 Owner 会话 / 测试端口);
人肉期样本全部自造(F202 连续 BLOCK → F225 部署变化自动重新派审 / F222 返工上限构件, 禁第二份)。
全程不碰真实治理根 / 生产看板 / 真实凭据 / 真门。验收原文经 _show 打到 stdout(RETURN 证据)。
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

import test_aipos_f202_owner_reaudit as f202  # noqa: E402  — 连续 BLOCK 人肉期样本(禁第二份)
import test_aipos_f222_rework_limit_arbitration as f222  # noqa: E402  — 返工上限样本(禁第二份)
import test_aipos_f226_board_inbox_decide as f226  # noqa: E402  — 看板靶场 / 裁决两跳构件(禁第二份)
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f179_long_run_resources_block import _block_watch, _quiet  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import owner_inbox as oi  # noqa: E402

_show = f226._show
TASK = f202.TASK
RW = f226.RW


def _inbox(home: Path) -> dict:
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    assert rc == 0, out + err
    return json.loads(out)


def _auto_column(page: str) -> str:
    m = re.search(r'<section class="ib-auto-released" data-auto-released="\d+">.*?</section></section>', page, re.S)
    return m.group(0) if m else ""


def _buttons(section: str) -> list[str]:
    return re.findall(r'<button type="button" class="ib-decide-btn[^"]*" data-ib-choice="(\w+)"', section)


@pytest.fixture
def tiers(rig, tmp_path):  # noqa: F811
    """第一档样本: 返工上限卡 RW(F226 同形); 第二档样本: 连续 BLOCK 后部署更新 → loop 自动重新派审(F225, 落自动放行记录, R3 再 BLOCK 1 次)。"""
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": f78.DRIVER,
               "focus_items": ["x"]} for n in (1, 2)]
    f78._card(rig.gov, RW, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'", "rework_rounds": rounds})
    f222._verdict(rig.gov, RW, 3, "2026-10-09T00:00:00Z")
    f202._two_blocks(rig)
    res, text = f202._loop(rig, _block_watch(rig, {f"{TASK}R3": "BLOCK"}), f202.COMMIT_NEW)
    _show(f"---- 靶场·部署更新后续跑 loop(第二档自动重新派审): exit {res.exit_code} reason={res.reason} ----\n{_quiet(text)}")
    assert "auto_reaudit: 第二档自动重新派审 auto_release_" in text and res.reason == "audit_blocked", text
    board = f226.Board(rig.gov.parent, tmp_path)
    try:
        yield {"rig": rig, "home": rig.gov.parent, "gov": rig.gov, "board": board}
    finally:
        board.close()


def test_item2_auto_released_column_revoke_stops_card_and_tier1_still_decidable(tiers):
    rig, home, gov, board = tiers["rig"], tiers["home"], tiers["gov"], tiers["board"]
    data = _inbox(home)
    auto = [n for n in oi.revocable_notices(data) if n.get("task_id") == TASK]
    rework = next(i for i in data["items"] if i.get("task_id") == RW and i["category"] == "rework_limit")
    _show(f"[靶场] 第一档 items={[(i['task_id'], i['category']) for i in data['items']]} count={data['count']} / "
          f"第二档 notices={[(n['task_id'], n['category']) for n in data['notices']]}")
    assert len(auto) == 1 and auto[0]["category"] == "auto_released" and auto[0]["facts"]["kind"] == "auto_reaudit"
    assert not [i for i in data["items"] if i.get("task_id") == TASK], "第二档不进待定区"
    item = auto[0]

    # 看板(Owner 会话): 「已自动放行」栏列出该事项, 每个选项一个按钮(撤回 / 知悉); 不计待定件数; 第一档事项仍在待定区带按钮
    status, page, _h = board.request("/owner/inbox")
    column = _auto_column(page)
    _show(f"---- 件②·看板「已自动放行」栏 HTML 原文(Owner 会话)----\n{column}")
    assert status == 200 and column and f'data-item-id="{item["item_id"]}"' in column
    assert "已自动放行 · 1 项(不需你定, 可撤回)" in column and _buttons(column) == ["revoke_auto", "acknowledge"]
    assert re.search(r'data-owner-inbox-count="(\d+)"', page).group(1) == str(data["count"]) == "1", "自动放行不计待定件数"
    pending_area = page.split('<section class="ib-auto-released"', 1)[0]
    assert f'data-item-id="{item["item_id"]}"' not in pending_area and f'data-item-id="{rework["item_id"]}"' in pending_area
    assert _buttons(f226._section(pending_area, rework["item_id"]))[0] == "extend_rework"
    assert '<script src="/inbox-decide.js"></script>' in page
    # 非 Owner 会话: 栏照列(只读), 无按钮 / 无脚本
    status, ro, _h = board.request("/owner/inbox", who="advisor")
    ro_column = _auto_column(ro)
    assert status == 200 and f'data-item-id="{item["item_id"]}"' in ro_column and not _buttons(ro_column) and "inbox-decide.js" not in ro
    # 英文页同键
    status, en, _h = board.request("/owner/inbox?lang=en")
    assert status == 200 and "Auto-released · 1 (no decision needed, can be revoked)" in en

    # 一键撤回(F226 同一写路由: 预演 → 确认)
    _pre, done = f226._rule(board, item, "revoke_auto", words="先别自动重审, 我要看看")
    text = f226._decision_text(gov, item["decision_id"])
    _show("[件②·撤回决策记录字段原文]\n" + "\n".join(f226._fm_lines(text, ("decided_by_ref", "capture_surface", "task_id")))
          + "\n" + "\n".join(ln for ln in text.splitlines() if ln.startswith(("- Rationale:", "- Allowed next action:"))))
    assert "capture_surface: board" in text and "- Allowed next action: revoke_auto" in text
    assert "- Rationale: Owner 原话: 先别自动重审, 我要看看" in text

    # 撤回后卡停住: 唯一判定 auto_release_stop / 推导核硬停 / loop 以 auto_release_revoked 结束
    stop = oi.auto_release_stop(gov, TASK)
    d = nr.derive_next_step(TASK, gov)
    _show(f"---- 件②·撤回后推导 ----\nauto_release_stop={stop}\n" + json.dumps(
        {k: d.get(k) for k in ("derivable", "suggested_action")}, ensure_ascii=False))
    assert stop and stop["auto_release_id"] == item["facts"]["auto_release_id"] and stop["end_reason"] == "auto_release_revoked"
    assert not d["derivable"] and d["action"]["type"] == "auto_release_revoked"
    res, loop_text = f202._loop(rig, _block_watch(rig, {}), f202.COMMIT_NEW)
    _show(f"---- 件②·撤回后续跑 loop: exit {res.exit_code} reason={res.reason} ----\n{_quiet(loop_text)}")
    assert res.reason == "auto_release_revoked", loop_text
    rc, st, err = _cli(["--workspace-root", str(gov), "loop", "status", "--task-id", TASK])
    _show(f"---- 件②·撤回后 loop status 原文尾 ----\n{st[-900:]}")
    assert "owner_needed(auto_release_revoked)" in st and "第一档事由: 撤卡 / 放弃 withdraw" in st

    # 栏内该事项消失(已裁决); 停住的卡作为第一档事项进待定区, 看板一键裁决(恢复推进)= 停住解除
    data = _inbox(home)
    assert not [n for n in oi.revocable_notices(data) if n["item_id"] == item["item_id"]]
    stopped = next(i for i in data["items"] if i.get("task_id") == TASK and i["category"] == "loop_owner_needed")
    assert stopped["owner_reason"]["key"] == "withdraw"
    status, page, _h = board.request("/owner/inbox")
    assert f'data-item-id="{item["item_id"]}"' not in page and not _auto_column(page), "无可撤回事项 = 不渲染该栏"
    assert _buttons(f226._section(page, stopped["item_id"]))[:2] == ["continue", "rescope"]
    f226._rule(board, stopped, "continue", words="看过了, 继续")
    assert oi.auto_release_stop(gov, TASK) is None
    assert (nr.derive_next_step(TASK, gov).get("action") or {}).get("type") != "auto_release_revoked"

    # 第一档返工上限事项同样一键裁决(F226 同一写路由)
    _pre, done_rw = f226._rule(board, rework, "extend_rework")
    assert done_rw["decision_id"] == rework["decision_id"]
    outcomes = [(r.get("item_id"), r["outcome"]) for r in f226._audit_lines(board)]
    _show(f"[看板审计日志] {outcomes}")
    assert [o for _i, o in outcomes] == ["previewed", "confirming", "ruled"] * 3
    assert [i for i, o in outcomes if o == "ruled"] == [item["item_id"], stopped["item_id"], rework["item_id"]]


def test_revocable_notices_single_reader_and_discovery():
    """「已自动放行」栏只读 owner_inbox.revocable_notices(判据 = 声明 tiers.tier2.auto_release.revoke_option), 看板不另判;
    新夹具按 test_file_globs 命名被 run-all 自动发现。"""
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    src = (REPO_ROOT / "web" / "board" / "readonly_pages.py").read_text(encoding="utf-8")
    section = src.split("def _auto_released_section(", 1)[1].split("\ndef ", 1)[0]
    assert "revocable_notices(inbox)" in section
    assert not re.search(r"[\"']revoke_auto[\"']|[\"']auto_released[\"']", src), "看板不写死选项 / 类别名(判据只在声明与 owner_inbox)"
    decl = oi.inbox_declaration()
    revoke = oi.auto_release_declaration(decl)["revoke_option"]
    assert revoke in decl["categories"]["auto_released"]["options"]
    sample = {"notices": [{"item_id": "a", "options": [{"key": revoke}]}, {"item_id": "b", "options": [{"key": "renew"}]}]}
    assert [n["item_id"] for n in oi.revocable_notices(sample, decl)] == ["a"]
    assert is_test_file("tests/test_aipos_f228_board_auto_released_revoke.py", default_test_contract())
