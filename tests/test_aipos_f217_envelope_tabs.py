"""AIPOS-F217 — 看板信封页「待签 / 在用 / 历史」三个标签页 + 刷新 + 签发后整页按服务端真相刷新(Owner 10-23)。

验收(卡面):
② 分类判定唯一函数 envelope_board.envelope_placement 单测: 生效 / 将尽 / 将到期 / 额度用尽有接替 / 额度用尽无接替 / 过期 / 被接替 / 停用
   (另: 未到生效时间、一次性信封用尽而同驱动方仍有在用信封);每行恰在一个归属页(在用 / 历史), 待签计数与切分同源。
③ 签发成功后跳转 URL(页面 data-gr-texts.after_sign, 真实 static/envelopes.js 在 node 下跑)→ 服务端横幅 + 高亮行: 续签 / 新签落在用页
   (授权请求落待签页 + 「已定待转达」见 F201 / F180 测试; 收件箱页签后整页刷新见 F180 测试)。
④ 非 Owner 会话三个标签都有标签与刷新, 但无表单无按钮。
⑤ 缺省标签两种情形(待签有内容 = 待签; 否则在用)+ 显式 tab 参数 / 不认识的参数。
另: 刷新链接保留标签 / 测试项目开关 / 语言; 声明 fail-closed。

靶场 = F172 同一靶场(临时 home 根 + 进程内门替身 + 测试进程内看板; 构件从 F172 测试导入, 不另写)。全程不读真实凭据、不碰真实治理根。
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f172_board_envelopes import (P1, P2, _claims, _iso, _mint, _policy, _renew_body, _request, _show,  # noqa: E402,F401
                                             board, rig)
from test_aipos_f180_grant_request import _run_client  # noqa: E402  — 真实页脚本 node 驱动唯一来源
from test_aipos_f73d_loop_driver import _write  # noqa: E402


def _tab_links(page: str) -> list[tuple[str, str, str, bool]]:
    nav = re.search(r'<nav class="env-tabs"[^>]*>.*?</nav>', page, re.S).group(0)
    return [(m.group(1), html.unescape(m.group(3)), html.unescape(m.group(5)), bool(m.group(4)))
            for m in re.finditer(r'<a class="env-tab" data-tab="(\w+)" data-count="(\d+)" href="([^"]*)"( aria-current="page")?>(.*?)</a>', nav)]


def _refresh(page: str) -> str:
    return html.unescape(re.search(r'<a class="env-refresh" href="([^"]*)">', page).group(1))


def _conf(page: str) -> dict:
    return json.loads(html.unescape(re.search(r'data-gr-texts="([^"]*)"', page).group(1)))


# ===========================================================================
# ② 分类判定(唯一函数, 真实推导 envelope_overview)
# ===========================================================================

@pytest.fixture
def states_root(tmp_path: Path) -> Path:
    """一个治理根, 每种展示状态各一封(驱动方各自独立, 除接替 / 一次性信封两组)。"""
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown
    from tools.aipos_cli.clock import utc_now

    root = tmp_path / "gov"
    now, d = utc_now(), timedelta(days=1)
    _policy(root, "pol_t_act_1", who="exec.act", max_tasks=10, active_from=now - 5 * d, expires_at=now + 20 * d)
    _claims(root, "pol_t_act_1", 1)
    # 一次性信封(1/1)用尽, 比同驱动方的在用信封新(不被接替) = 历史, 不待签(驱动方仍有在用信封)
    _policy(root, "pol_t_act_2", who="exec.act", max_tasks=1, active_from=now - d, expires_at=now + 20 * d)
    _claims(root, "pol_t_act_2", 1)
    _policy(root, "pol_t_low_1", who="exec.low", max_tasks=3, active_from=now - 2 * d, expires_at=now + 20 * d)  # 将尽: 剩 2/3
    _claims(root, "pol_t_low_1", 1)
    _policy(root, "pol_t_due_1", who="exec.due", max_tasks=40, active_from=now - 2 * d, expires_at=now + timedelta(hours=5))  # 将到期
    _claims(root, "pol_t_due_1", 1)
    # 接替组: sup_0(生效)与 sup_1(1/1 用尽)都被同驱动方更新的 sup_2 接替
    _policy(root, "pol_t_sup_0", who="exec.sup", max_tasks=20, active_from=now - 6 * d, expires_at=now + 20 * d)
    _policy(root, "pol_t_sup_1", who="exec.sup", max_tasks=1, active_from=now - 4 * d, expires_at=now + 20 * d)
    _claims(root, "pol_t_sup_1", 1)
    _policy(root, "pol_t_sup_2", who="exec.sup", max_tasks=20, active_from=now - d, expires_at=now + 20 * d)
    _policy(root, "pol_t_alone_1", who="exec.alone", max_tasks=2, active_from=now - 3 * d, expires_at=now + 20 * d)  # 用尽无接替
    _claims(root, "pol_t_alone_1", 2)
    _policy(root, "pol_t_old_1", who="exec.old", max_tasks=20, active_from=now - 30 * d, expires_at=now - d)  # 过期
    _policy(root, "pol_t_soon_1", who="exec.soon", max_tasks=20, active_from=now + d, expires_at=now + 20 * d)  # 已签待生效
    _write(root / "5_tasks" / "policies" / "pol_t_off_1.md", build_autonomy_policy_markdown(  # 停用
        policy_id="pol_t_off_1", agent_or_role="exec.off", active_from=_iso(now - 2 * d), expires_at=_iso(now + 20 * d), max_tasks=20,
        owner_approval_ref="fixture-pol_t_off_1", task_selector_task_mode="code", approved_by_owner=False))
    return root


#: policy_id → (展示状态, 归属页, 是否待签)
EXPECTED = {
    "pol_t_act_1": ("active", "current", False),       # 生效
    "pol_t_low_1": ("warn", "current", True),          # 将尽
    "pol_t_due_1": ("warn", "current", True),          # 将到期
    "pol_t_soon_1": ("not_yet_active", "current", False),
    "pol_t_sup_1": ("exhausted", "history", False),    # 额度用尽有接替
    "pol_t_alone_1": ("exhausted", "history", True),   # 额度用尽无接替
    "pol_t_act_2": ("exhausted", "history", False),    # 一次性信封用尽, 同驱动方仍有在用信封
    "pol_t_old_1": ("expired", "history", False),      # 过期
    "pol_t_sup_0": ("superseded", "history", False),   # 被接替
    "pol_t_sup_2": ("active", "current", False),
    "pol_t_off_1": ("inactive", "history", False),     # 停用
}


def test_item2_placement_each_state(states_root: Path):
    from tools.aipos_cli.autonomy_policy import envelope_overview
    from tools.aipos_cli.envelope_board import envelope_placement

    rows = envelope_overview(states_root)["envelopes"]
    got = {}
    for row in rows:
        place = envelope_placement(row, rows)
        got[row["policy_id"]] = (row["status"], place["tab"], place["pending"])
        _show(f"[②·判定] {row['policy_id']}: who={row['agent_or_role']} status={row['status']} used={row['used']}/{row['max_tasks']} "
              f"superseded_by={row['superseded_by']} why={row['why']} → tab={place['tab']} pending={place['pending']}")
    assert got == EXPECTED
    whys = {r["policy_id"]: r["why"] for r in rows}
    assert "剩余 2/3" in whys["pol_t_low_1"] and "到期" in whys["pol_t_due_1"], "将尽 / 将到期是同一展示状态 warn 的两种原因"


def test_item2_tabs_split_each_row_exactly_one_home_and_counts(states_root: Path):
    from tools.aipos_cli.autonomy_policy import envelope_overview
    from tools.aipos_cli.envelope_board import envelope_tabs

    ov = envelope_overview(states_root)
    view = {"projects": [{"project": "t", "envelopes": ov["envelopes"], "drivers": ov["drivers"]}]}
    split = envelope_tabs(view)
    parts = split["projects"]["t"]
    ids = lambda tab: sorted(r["policy_id"] for r in parts[tab])  # noqa: E731
    _show(f"[②·切分] pending={ids('pending')} current={ids('current')} history={ids('history')} counts={split['counts']}")
    assert sorted(ids("current") + ids("history")) == sorted(EXPECTED) and not set(ids("current")) & set(ids("history"))
    assert ids("pending") == sorted(pid for pid, (_s, _t, p) in EXPECTED.items() if p)
    assert split["counts"] == {"pending": 3, "current": 5, "history": 6}


def test_tabs_declaration_fail_closed(monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import envelope_board
    from tools.schema_loader import SchemaLoadError

    good = envelope_board.board_envelopes_declaration()["tabs"]
    statuses = set(good["in_use_statuses"]) | {"exhausted", "expired", "superseded", "inactive", "invalid", "missing"}
    envelope_board._check_tabs(good, statuses)
    assert set(good["texts"]["zh"]) == set(good["texts"]["en"])
    bad_cases = {"缺键": {k: v for k, v in good.items() if k != "after_sign"},
                 "未知状态": {**good, "in_use_statuses": ["active", "nonsense"]},
                 "续签状态不在在用": {**good, "renew_statuses": ["expired"]},
                 "after_sign 指向未知标签": {**good, "after_sign": {**good["after_sign"], "renew": "elsewhere"}},
                 "缺一种语言": {**good, "texts": {"zh": good["texts"]["zh"]}},
                 "缺文案键": {**good, "texts": {**good["texts"], "en": {k: v for k, v in good["texts"]["en"].items() if k != "refresh"}}}}
    for name, bad in bad_cases.items():
        with pytest.raises(SchemaLoadError):
            envelope_board._check_tabs(bad, statuses)
        _show(f"[声明·fail-closed] {name} → SchemaLoadError")


# ===========================================================================
# ⑤ 缺省标签两种情形 + 显式参数
# ===========================================================================

def test_item5_default_tab_two_cases(rig):
    from tools.aipos_cli.envelope_board import resolve_tab
    from web.board.readonly_pages import render_envelopes

    page = render_envelopes(rig["home"], {}, owner=True)
    links = _tab_links(page)
    _show(f"[⑤·有待签内容 → 缺省标签] {[(t, label, cur) for t, _h, label, cur in links]}")
    assert [t for t, _h, _l, cur in links if cur] == ["pending"] and links[0][2] == "待签(2)"
    # 让待签清空: 将尽的 audit_1 与用尽的 two_loop_1 都撤掉已放行认领(used=0 不预警, F201 件④)
    for pid in ("pol_one_audit_1", "pol_two_loop_1"):
        shutil.rmtree(rig["g1" if "one" in pid else "g2"] / "5_tasks" / "records" / "claims", ignore_errors=True)
    page = render_envelopes(rig["home"], {}, owner=True)
    links = _tab_links(page)
    _show(f"[⑤·无待签内容 → 缺省标签] {[(t, label, cur) for t, _h, label, cur in links]}")
    assert [t for t, _h, _l, cur in links if cur] == ["current"] and links[0][2] == "待签(0)"
    pending = render_envelopes(rig["home"], {"tab": ["pending"]}, owner=True)
    assert "没有待签的事项。" in pending and [t for t, _h, _l, cur in _tab_links(pending) if cur] == ["pending"]
    assert resolve_tab({"tab": ["history"]}, pending_count=3, relays=0) == "history"
    assert resolve_tab({"tab": ["bogus"]}, pending_count=3, relays=0) == "pending"
    assert resolve_tab({}, pending_count=0, relays=1) == "pending", "只有待转达段 = 待签有内容"
    assert resolve_tab({}, pending_count=0, relays=0) == "current"


# ===========================================================================
# ④ 非 Owner 会话 + 刷新链接
# ===========================================================================

def test_item4_non_owner_tabs_and_refresh_without_forms_or_buttons(board):
    for tab in ("pending", "current", "history"):
        status, page = _request(board, f"/owner/envelopes?tab={tab}", who="advisor")
        links = _tab_links(page)
        _show(f"[④·非 Owner tab={tab}] 标签={[(t, label, cur) for t, _h, label, cur in links]} 刷新={_refresh(page)} "
              f"表单={page.count('<form')} 按钮={page.count('<button')}")
        assert status == 200 and [t for t, _h, _l, _c in links] == ["pending", "current", "history"]
        assert [t for t, _h, _l, cur in links if cur] == [tab] and _refresh(page) == f"/owner/envelopes?tab={tab}"
        assert "<form" not in page and "<button" not in page and "envelopes.js" not in page and "data-gr-texts" not in page


def test_refresh_keeps_tab_switch_and_language(board):
    status, page = _request(board, "/owner/envelopes?tab=history&show=all&lang=en")
    links = _tab_links(page)
    _show(f"[刷新·en + show=all] 刷新={_refresh(page)} 标签={[(t, h, label) for t, h, label, _c in links]}")
    assert status == 200 and _refresh(page) == "/owner/envelopes?tab=history&show=all&lang=en"
    assert [label for _t, _h, label, _c in links] == ["To sign (2)", "In use (4)", "History (3)"]
    assert all(h.endswith("&show=all&lang=en") for _t, h, _l, _c in links)
    assert _conf(page)["after_sign"]["renew"] == "/owner/envelopes?tab=current&show=all&lang=en&signed={policy_id}#{policy_id}"


# ===========================================================================
# ③ 签发后整页刷新: 续签 / 新签(真实页脚本)→ 在用页 + 横幅 + 高亮
# ===========================================================================

def _sign_with_script(board, tmp_path: Path, page: str, body: dict, form: dict) -> tuple[dict, dict]:
    status, pre = _mint(board, {**body, "stage": "preview"})
    assert status == 200 and pre["ok"], pre
    status, done = _mint(board, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    assert status == 200 and done["ok"], done
    out = _run_client(tmp_path, page, None, [{"status": 200, "data": pre}, {"status": 200, "data": done}], form=form)
    return out, done


def test_item3_renew_and_new_land_on_in_use_with_banner_and_highlight(board, tmp_path: Path):
    from tools.aipos_cli.clock import utc_now

    status, page = _request(board, "/owner/envelopes?tab=current")
    conf = _conf(page)
    _show(f"[③·页脚本跳转模板] {conf['after_sign']}")
    assert conf["after_sign"] == {"request": "/owner/envelopes?tab=pending&signed={policy_id}#{policy_id}",
                                  "renew": "/owner/envelopes?tab=current&signed={policy_id}#{policy_id}",
                                  "new": "/owner/envelopes?tab=current&signed={policy_id}#{policy_id}"}
    # 续签(在用行折叠的续签表单)
    body = _renew_body()
    form = {"mode": "renew", "workspace": P1, "template": "pol_one_loop_9",
            "inputs": {"max_tasks": str(body["max_tasks"]), "expires_at": body["expires_at"]}}
    (tmp_path / "renew").mkdir()
    out, done = _sign_with_script(board, tmp_path / "renew", page, body, form)
    _show(f"[③·续签 真实脚本] 面板={out['after_confirm']['heading']} / {out['after_confirm']['lines']} 跳转={out['navigated']}")
    assert out["calls"][0]["stage"] == "preview" and out["after_confirm"]["lines"] == ["已签发, 正在刷新页面…"]
    assert out["navigated"] == {"assigned": "/owner/envelopes?tab=current&signed=pol_one_loop_10#pol_one_loop_10", "reloaded": False}
    status, landed = _request(board, out["navigated"]["assigned"])
    banner = re.search(r'<p class="env-banner" data-signed="([^"]+)">(.*?)</p>', landed).group(0)
    row = re.search(r'<tr id="pol_one_loop_10"[^>]*>', landed).group(0)
    _show(f"[③·续签落地页] 横幅={banner}\n  高亮行={row}\n  标签={[(t, label, cur) for t, _h, label, cur in _tab_links(landed)]}")
    assert status == 200 and 'data-signed="pol_one_loop_10"' in banner and "已签发 pol_one_loop_10, 页面已按最新真相刷新。" in banner
    assert "envelope-row-signed" in row and 'data-signed="true"' in row and landed.count('data-signed="true"') == 1
    assert [t for t, _h, _l, cur in _tab_links(landed) if cur] == ["current"]
    assert 'id="pol_one_loop_9"' not in landed, "被新签接替的旧信封随刷新离开「在用」"
    # 新签(在用页项目底部的新签表单)
    new_body = {"workspace": P2, "mode": "new", "agent_or_role": f"advisor.{P2}.h1", "task_mode": "code", "lane_repo": "", "launch_harness": "",
                "max_tasks": "12", "expires_at": _iso(utc_now() + timedelta(days=5)), "policy_id": ""}
    form = {"mode": "new", "workspace": P2, "template": None,
            "inputs": {k: new_body[k] for k in ("agent_or_role", "task_mode", "lane_repo", "launch_harness", "max_tasks", "expires_at", "policy_id")}}
    (tmp_path / "new").mkdir()
    out, done = _sign_with_script(board, tmp_path / "new", page, new_body, form)
    pid = done["signed"][0]["policy_id"]
    _show(f"[③·新签 真实脚本] 跳转={out['navigated']}")
    assert out["navigated"]["assigned"] == f"/owner/envelopes?tab=current&signed={pid}#{pid}"
    status, landed = _request(board, out["navigated"]["assigned"])
    assert f'<p class="env-banner" data-signed="{pid}">' in landed and re.search(rf'<tr id="{pid}"[^>]*envelope-row-signed', landed)
    # 横幅只认页面上真实存在的信封(参数乱填 = 不出横幅)
    status, odd = _request(board, "/owner/envelopes?tab=current&signed=pol_not_there_1")
    assert "env-banner" not in odd and 'data-signed="true"' not in odd
    # 待签页上的横幅: 指向「在用」该行
    status, on_pending = _request(board, f"/owner/envelopes?tab=pending&signed={pid}")
    link = re.search(r'<p class="env-banner"[^>]*>.*?<a href="([^"]+)">(.*?)</a></p>', on_pending)
    assert link and html.unescape(link.group(1)) == f"/owner/envelopes?tab=current#{pid}" and link.group(2) == "在「在用」中查看"
