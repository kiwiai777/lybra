"""AIPOS-F141 — 视图尊重冻结与 lane 归属: 四视图(lybra next 项目扫描 / brief / loop status / needs-owner)共用唯一「可见卡」入口
machine_zone.visible_cards。

靶场 = 临时治理根(自造人肉期样本: 项目 hbjfx, 两仓 web/api 各一 lane + 未声明仓的卡 + 存量冻结卡)。禁真门/真治理根/真工位/真 pi。
复用 F133 靶场与 CLI 包装(_hcard / _cli / _show)、F78C 骨架(_gov_skeleton / _product_repo), 禁第二份靶场。

件① 冻结卡不入视图: 是否冻结只调 F122 唯一判定 legacy_baseline.frozen_tasks(经 visible_cards); 缺省不列, 汇总行「冻结 N 张未列」;
    --include-frozen 显式列出并标 frozen; 冻结清单读不出 = 汇总行点名错误, 不隐藏任何卡(fail-closed)。
件② lane 过滤: 给定 --lane 只列 lane ∈ 所给集合的卡(唯一过滤 filter_rows_by_lane); lane 解析不了的卡不混入, 输出末尾单独一组
    「未归 lane N 张」(计数 + 卡号); 不带 --lane 分组输出不变(未解析组照列)。
件③ 文档: 顾问文档写明视图口径。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write  # noqa: E402  — 靶场/替身唯一来源
from test_aipos_f78_engine_agnostic import DRIVER  # noqa: E402
from test_aipos_f78c_card_repo import _gov_skeleton, _product_repo  # noqa: E402
from test_aipos_f133_lane_view_dependencies import PROJECT, _cli, _hcard, _show  # noqa: E402
from tools.aipos_cli import machine_zone as mz  # noqa: E402

FROZEN = ["HBJ-F1", "HBJ-F2", "HBJ-F3"]


def _decl() -> dict:
    return mz.lane_view_declaration()


@pytest.fixture
def gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """两 lane(web/api)+ 未声明仓卡 + 三张存量冻结卡(均带 needs_owner 原因; F3 同时 lane 不可解析)。"""
    root = _gov_skeleton(tmp_path / "gov-hbjfx", monkeypatch)
    for sub in ("stage_archive", "governance/decision_log"):
        (root / sub).mkdir(parents=True)
    repos = {"web": _product_repo(tmp_path / "repo-web"), "api": _product_repo(tmp_path / "repo-api")}
    _write(root / "project.json", json.dumps({"project": PROJECT, "config_version": 1,
                                              "repos": {"default": "web", "items": {k: str(v) for k, v in repos.items()}}}))
    _hcard(root, "HBJ-W1", "pending", lane_repo="web", priority="medium")
    _hcard(root, "HBJ-W2", "pending", lane_repo="web", priority="critical", needs_owner=True)
    _hcard(root, "HBJ-A1", "pending", lane_repo="api", priority="high", needs_owner=True)
    _hcard(root, "HBJ-X1", "pending", lane_repo="ghost", priority="high", needs_owner=True)  # 未声明仓 → lane 不可解析
    # 人肉期历史卡(冻结前在视图里冒出来的那类): 目录/状态不一致 + needs_owner
    _hcard(root, "HBJ-F1", "claimed", lane_repo="web", needs_owner=True, status="completed")
    _hcard(root, "HBJ-F2", "blocked", lane_repo="api", needs_owner=True, status="claimed")
    _hcard(root, "HBJ-F3", "claimed", lane_repo="ghost", needs_owner=True, status="completed")
    rc, out, err = _cli(["project", "freeze-legacy", "--workspace-root", str(root), "--task-ids", *FROZEN,
                         "--reason", "人肉期存量", "--actor", DRIVER, "--confirm"])
    assert rc == 0, (out, err)
    return root


def _lines(out: str, word: str) -> list[str]:
    return [line for line in out.splitlines() if word in line]


# ===========================================================================
# 件① 冻结卡不入视图(needs-owner / brief / next / loop status)
# ===========================================================================

def test_item1_needs_owner_skips_frozen_with_summary_and_include_frozen(gov):
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner"])
    _show("件① lybra needs-owner(缺省):\n" + out)
    assert rc == 0, err
    assert not any(t in out for t in FROZEN)
    assert "冻结 3 张未列" in out and "--include-frozen" in out
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner", "--include-frozen"])
    _show("件① lybra needs-owner --include-frozen:\n" + out)
    assert rc == 0, err
    for tid in FROZEN:
        line = next(line for line in out.splitlines() if line.startswith(tid))
        assert _decl()["visible_cards"]["frozen_marker"] in line, line
    assert "冻结 3 张未列" not in out
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner", "--json"])
    payload = json.loads(out)
    assert sorted(payload["frozen_hidden"]) == FROZEN and payload["frozen_error"] is None
    assert not {t["task_id"] for t in payload["tasks"]} & set(FROZEN)


def test_item1_board_needs_owner_same_filter_skips_frozen(gov):
    """看板「需 Owner 决定」(board_adapter.get_needs_owner)与 CLI 同一筛选 + 同一可见卡入口(原第二份判据已删)。"""
    from tools.aipos_cli.board_adapter import get_needs_owner

    data = get_needs_owner(gov)["data"]
    _show("件① 看板 get_needs_owner: " + json.dumps({"tasks": [t["task_id"] for t in data["tasks"]],
                                                    "frozen_hidden": data["frozen_hidden"]}, ensure_ascii=False))
    assert sorted(t["task_id"] for t in data["tasks"]) == ["HBJ-A1", "HBJ-W2", "HBJ-X1"]
    assert sorted(data["frozen_hidden"]) == FROZEN and data["summary"]["needs_owner"] == 3
    src = (REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")
    body = src.split("def get_needs_owner(", 1)[1].split("\ndef ", 1)[0]
    assert "_filter_needs_owner(" in body and "owner_review_required" not in body


def test_item1_brief_and_next_and_loop_status_skip_frozen(gov):
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    rc, out, err = _cli(["brief", "--workspace-root", str(gov)])
    section = out.split("【3. 当前在跑什么】", 1)[1].split("【4.", 1)[0]
    _show("件① lybra brief【3】:" + section)
    assert rc == 0, err
    assert "claimed:   0" in section and "blocked:   0" in section and "pending:   4" in section  # 冻结卡不计
    assert "冻结 3 张未列" in section
    rc, out, err = _cli(["brief", "--workspace-root", str(gov), "--include-frozen", "--json"])
    queue = json.loads(out)["queue"]
    assert rc == 0, err
    assert (queue["claimed"], queue["blocked"], queue["frozen_hidden"]) == (2, 1, [])

    rc, out, err = _cli(["next", "--workspace-root", str(gov)])
    _show("件① lybra next(项目扫描):\n" + out)
    assert rc == 0, err
    assert not any(t in out.split("冻结 3 张未列")[0] for t in FROZEN) and "冻结 3 张未列" in out
    rc, out, err = _cli(["next", "--workspace-root", str(gov), "--include-frozen", "--json"])
    rows = {r["task_id"]: r for r in json.loads(out)}
    assert all(rows[t]["frozen"] is True and rows[t]["current_state"] == "legacy_frozen" for t in FROZEN)
    assert rows["HBJ-W2"].get("frozen") is not True

    recorders = [LoopRunRecorder(gov, tid, driver=DRIVER, contract=load_loop_contract(), warn=lambda _m: None)
                 for tid in ("HBJ-W1", "HBJ-F1")]
    try:
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov)])
        _show("件① lybra loop status:\n" + out)
        assert rc == 0, err
        assert "卡 HBJ-W1" in out and "卡 HBJ-F1" not in out and "冻结 1 张未列" in out
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), "--include-frozen"])
        assert "卡 HBJ-F1" in out and _decl()["visible_cards"]["frozen_marker"] in out
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), "--task-id", "HBJ-F1", "--json"])
        runs = json.loads(out)["runs"]  # 显式点名一张卡 = 照列(标 frozen), 不按冻结隐藏
        assert [r["task_id"] for r in runs] == ["HBJ-F1"] and runs[0]["frozen"] is True
    finally:
        for rec in recorders:
            rec.close_log()


def test_item1_manifest_unreadable_fails_closed_hides_nothing(gov):
    from tools.aipos_cli.legacy_baseline import manifest_location

    _write(manifest_location(gov) / "zz-broken.md", "no frontmatter\n")
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner"])
    _show("件① 清单读不出 lybra needs-owner:\n" + out)
    assert rc == 0, err
    assert all(t in out for t in FROZEN) and "冻结 3 张未列" not in out
    assert "LEGACY_BASELINE_INVALID" in out
    rc, out, err = _cli(["brief", "--workspace-root", str(gov), "--json"])
    queue = json.loads(out)["queue"]
    assert queue["frozen_error"].startswith("LEGACY_BASELINE_INVALID") and queue["claimed"] == 2


# ===========================================================================
# 件② --lane 只列所选; 未归 lane 单独一组(计数 + 卡号); 不带 --lane 分组不变
# ===========================================================================

def test_item2_lane_filter_sets_aside_unresolved_in_all_four_views(gov):
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    unresolved = _decl()["unresolved_lane"]

    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner", "--lane", "web"])
    _show("件② lybra needs-owner --lane web:\n" + out)
    assert rc == 0, err
    listed = out.split("未归 lane")[0]
    assert "HBJ-W2" in listed and "HBJ-X1" not in listed and "HBJ-A1" not in listed
    assert _lines(out, "未归 lane 1 张") and "HBJ-X1" in _lines(out, "未归 lane 1 张")[0]
    assert "HBJ-F3" not in out  # 冻结在先: 冻结卡不进「未归 lane」组
    payload = json.loads(_cli(["--workspace-root", str(gov), "needs-owner", "--lane", "web", "--json"])[1])
    assert [t["task_id"] for t in payload["tasks"]] == ["HBJ-W2"]
    assert [u["task_id"] for u in payload["unresolved_lane"]] == ["HBJ-X1"]
    assert payload["unresolved_lane"][0]["lane_error"].startswith("LANE_REPO_UNDECLARED")

    rc, out, err = _cli(["next", "--workspace-root", str(gov), "--lane", "web"])
    _show("件② lybra next --lane web:\n" + out)
    assert rc == 0, err
    listed = out.split("未归 lane")[0]
    assert "HBJ-W1" in listed and "HBJ-W2" in listed and "HBJ-X1" not in listed
    assert "HBJ-X1" in _lines(out, "未归 lane 1 张")[0]

    rc, out, err = _cli(["brief", "--workspace-root", str(gov), "--lane", "web", "--json"])
    queue = json.loads(out)["queue"]
    _show("件② lybra brief --lane web --json queue: " + json.dumps(
        {k: queue[k] for k in ("pending", "lane_filter", "unresolved_lane", "frozen_hidden")} | {"lanes": list(queue["lanes"])},
        ensure_ascii=False))
    assert rc == 0, err
    assert queue["pending"] == 2 and list(queue["lanes"]) == ["web"]
    assert [u["task_id"] for u in queue["unresolved_lane"]] == ["HBJ-X1"]
    rc, out, err = _cli(["brief", "--workspace-root", str(gov), "--lane", "web"])
    assert "HBJ-X1" in _lines(out, "未归 lane 1 张")[0]

    recorders = [LoopRunRecorder(gov, tid, driver=DRIVER, contract=load_loop_contract(), warn=lambda _m: None)
                 for tid in ("HBJ-W1", "HBJ-X1")]
    try:
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), "--lane", "web"])
        _show("件② lybra loop status --lane web:\n" + out)
        assert rc == 0, err
        assert "卡 HBJ-W1" in out and "卡 HBJ-X1" not in out and "HBJ-X1" in _lines(out, "未归 lane 1 张")[0]
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov)])
        assert "卡 HBJ-X1" in out and f"lane {unresolved}" in out  # 不带 --lane: 未解析照列
    finally:
        for rec in recorders:
            rec.close_log()


def test_item2_without_lane_groups_unchanged(gov):
    unresolved = _decl()["unresolved_lane"]
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner"])
    assert rc == 0, err
    tail = out.index(f"== lane {unresolved}")  # 声明序(freeze-legacy 写回 project.json 后的仓序)+ 未解析组末尾, 与 F133 同形
    assert out.index("== lane web") < out.index("HBJ-W2") < tail and out.index("== lane api") < out.index("HBJ-A1") < tail
    assert tail < out.index("HBJ-X1")
    assert "未归 lane" not in out
    payload = json.loads(_cli(["--workspace-root", str(gov), "needs-owner", "--json"])[1])
    assert payload["lanes"] == {"web": ["HBJ-W2"], "api": ["HBJ-A1"], unresolved: ["HBJ-X1"]}
    assert payload["unresolved_lane"] == []
    queue = json.loads(_cli(["brief", "--workspace-root", str(gov), "--json"])[1])["queue"]
    assert sorted(list(queue["lanes"])[:2]) == ["api", "web"] and list(queue["lanes"])[2:] == [unresolved]
    assert queue["unresolved_lane"] == []


def test_item2_four_views_same_visible_set(gov):
    """四命令口径一致: 同一 --lane 下, next / needs-owner / brief 列出的卡都 ⊆ visible_cards 结果, 未归 lane 组相同。"""
    from tools.aipos_cli.next_resolver import scan_project_view

    view = scan_project_view(gov, lane=["api"])
    assert [r["task_id"] for r in view["rows"]] == ["HBJ-A1"]
    assert [u["task_id"] for u in view["unresolved_lane"]] == ["HBJ-X1"] and sorted(view["frozen_hidden"]) == FROZEN
    payload = json.loads(_cli(["--workspace-root", str(gov), "needs-owner", "--lane", "api", "--json"])[1])
    queue = json.loads(_cli(["brief", "--workspace-root", str(gov), "--lane", "api", "--json"])[1])["queue"]
    assert [t["task_id"] for t in payload["tasks"]] == ["HBJ-A1"] and queue["pending"] == 1 and queue["blocked"] == 0
    for part in (payload, queue):
        assert [u["task_id"] for u in part["unresolved_lane"]] == ["HBJ-X1"] and sorted(part["frozen_hidden"]) == FROZEN


# ===========================================================================
# 防碎片化不变量: 冻结判定 / lane 过滤 / 可见卡入口各一处
# ===========================================================================

def test_single_visible_cards_entry_and_no_second_path():
    src = {name: (REPO_ROOT / "tools/aipos_cli" / name).read_text(encoding="utf-8")
           for name in ("machine_zone.py", "brief.py", "loop_run_record.py", "next_resolver.py", "aipos_cli.py")}
    assert src["machine_zone.py"].count("def visible_cards(") == 1
    assert src["machine_zone.py"].count("frozen_tasks(") == 1  # 冻结判定在可见卡入口内只调 F122 唯一判定
    for name in ("brief.py", "loop_run_record.py"):
        assert "filter_rows_by_lane(" not in src[name] and "frozen_tasks(" not in src[name], name
        assert "visible_cards(" in src[name], name
    scan = src["next_resolver.py"].split("def scan_project_view(", 1)[1].split("\ndef ", 1)[0]
    assert "visible_cards(" in scan and "filter_rows_by_lane(" not in scan and "frozen_tasks(" not in scan
    needs = src["aipos_cli.py"].split("def _filter_needs_owner(", 1)[1].split("\ndef ", 1)[0]
    assert "visible_cards(" in needs and "filter_rows_by_lane(" not in needs
    assert "frozen_count = len(frozen_tasks" not in src["aipos_cli.py"]  # next 旧汇总行已并入 visible_cards 汇总
    # filter_rows_by_lane 只被可见卡入口调用
    assert src["machine_zone.py"].count("filter_rows_by_lane(") == 2  # 定义 + visible_cards 内一次调用
    unresolved = _decl()["unresolved_lane"]
    rows = [{"lane": "a"}, {"lane": "b"}, {"lane": unresolved}]
    aside: list = []
    assert mz.filter_rows_by_lane(rows, ["a"], set_aside=aside) == [rows[0]] and aside == [rows[2]]
    assert mz.filter_rows_by_lane(rows, None) == rows


def test_declaration_in_verbs_schema():
    from tools.schema_loader import load_schema

    decl = load_schema("verbs")["lane_view"]
    vc = decl["visible_cards"]
    assert vc["include_frozen_flag"] == "--include-frozen" and "{count}" in vc["frozen_summary"]
    assert "{count}" in vc["unresolved_summary"] and "{task_ids}" in vc["unresolved_summary"]
    assert "visible_cards" in decl["single_implementation"]
    assert "不混入" in decl["description"]
    params = load_schema("verbs")["verbs"]["lybra_loop_status"]["parameters"]["properties"]
    assert params["include_frozen"]["type"] == "boolean"


def test_item3_docs_state_view_rules():
    setup = (REPO_ROOT / "docs/mcp-agent-setup.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "tools/aipos_cli/README.md").read_text(encoding="utf-8")
    assert "--include-frozen" in setup and "not mixed in" in setup
    assert "--include-frozen" in readme and "未归 lane" in readme and "冻结" in readme
