"""AIPOS-F170 — 收件箱清理与更新通知首发: 遗留 blocked / needs_owner 卡批量定(件①)、暂停项目不进收件箱(件②)、
裁决记转达顾问与 Owner 原话(件③)、顾问章程教 loop note / 看板入口 / next 选项标签(件④)、F168–F170 顾问须知回填(件⑤)。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; `lybra project new` 建 alpha / gamma 两个项目, 自造人肉期遗留样本:
08–09 月 blocked + needs_owner 卡、pending needs_owner 卡; 近期 loop owner_needed 运行)。待决真相全部经产品既有写入口 / 记录器造
(F168 靶场构件唯一来源), 裁决 / 冻结 / 撤回 / 暂停由被测命令自己写。不读真实凭据、不碰真实治理根、不起门。

验收(卡面 ★验收): ① 遗留项按项目分组原文、批量 decide 预演 / 确认原文、blocked 卡经裁决 withdraw 与 freeze-legacy 原文
② paused 项目缺省不显示 / --include-paused 显示原文、set-meta --status 两阶段原文 ③ decide 缺 --relayed-by / --owner-words 拒原文、
记录与顾问侧显示原文 ④ 章程渲染含新引导原文、next 选项标签原文、看板 overview 入口(测试客户端 HTML 断言)原文
⑤ 回填汇总生成 F160–F170 更新说明原文(临时部署夹具)。真实只读预演见 RETURN。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 靶场治理仓唯一来源
from test_aipos_f168_owner_inbox import _card, _cli, _free_port, _git, _owner_needed_run, _show, _tree_digest  # noqa: E402
from test_aipos_f167_advisor_updates import ADV_A, PROJECT as F167_PROJECT  # noqa: E402
from test_aipos_f167_advisor_updates import _card as _f167_card  # noqa: E402
from test_aipos_f167_advisor_updates import rig as f167_rig  # noqa: E402,F401  — 章程渲染 / 部署库靶场(F167 同一夹具)

OWNER = "owner"
ALPHA, GAMMA = "alpha", "gamma"
DRIVER = f"advisor.{ALPHA}.h1"
WORDS = "这几张八月的旧卡都冻结掉, 不用再提"


def _relay(by: str = DRIVER, words: str = WORDS) -> tuple[str, ...]:
    return ("--relayed-by", by, "--owner-words", words)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """home 根 = 治理仓(带 origin 裸仓): alpha = 在推项目(遗留 blocked ×3 + 遗留 pending ×1 + 近期 loop owner_needed ×1),
    gamma = 将被暂停的项目(遗留 blocked ×1)。全部提交并推送(落账从干净树起)。"""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hroot = h / "lybra-home"
    for name in (ALPHA, GAMMA):
        rc, out = _cli("project", "new", name, "--home-root", str(hroot), "--actor", OWNER)
        assert rc == 0, out
    alpha, gamma = hroot / ALPHA, hroot / GAMMA
    for tid, at, why in (("ALPHA-B1", "2026-08-30T03:21:48Z", "历史僵尸卡隔离待分诊"), ("ALPHA-B2", "2026-09-01T07:49:37Z", "历史僵尸卡隔离待分诊"),
                         ("ALPHA-B3", "2026-09-02T16:39:53Z", "duplicate claim record")):
        _card(alpha, tid, "blocked", ALPHA, needs_owner=True, blocked_by=DRIVER, blocked_at=at, block_reason=why,
              title=f"{tid} 人肉期遗留")
    _card(alpha, "ALPHA-P1", "pending", ALPHA, needs_owner=True, created_at="2026-08-15T00:00:00Z", title="ALPHA-P1 早期草案")
    _card(alpha, "ALPHA-L1", "claimed", ALPHA, title="ALPHA-L1 在推卡")
    _owner_needed_run(alpha, "ALPHA-L1", DRIVER)  # 近期(今天)= 不算遗留
    _card(gamma, "GAMMA-1", "blocked", GAMMA, needs_owner=True, blocked_by=f"advisor.{GAMMA}.h1", blocked_at="2026-08-20T00:00:00Z",
          block_reason="客户暂停", title="GAMMA-1 客户侧阻塞")
    remote = init_governance_repo(hroot)
    return {"home": hroot, "alpha": alpha, "gamma": gamma, "remote": remote, "tmp": tmp_path}


def _inbox(home, *extra: str) -> tuple[int, str]:
    return _cli("owner", "inbox", "--home-root", str(home["home"]), *extra)


def _inbox_json(home, *extra: str) -> dict:
    rc, out = _inbox(home, "--json", *extra)
    assert rc in (0, 4), out
    return json.loads(out)


def _ids(home, *task_ids: str) -> list[str]:
    items = _inbox_json(home, "--include-paused")["items"]
    return [next(i["item_id"] for i in items if i.get("task_id") == t) for t in task_ids]


def _clean_and_pushed(home) -> bool:
    return (_git(home["home"], "status", "--porcelain") == ""
            and _git(home["home"], "rev-parse", "HEAD") == _git(home["remote"], "rev-parse", "main"))


# ===========================================================================
# ① 遗留视图: 按项目分组, 每项一行(卡 / 标题 / 卡住原因 / 建议), 同建议给批量裁决命令
# ===========================================================================

def test_item1_stale_view_grouped_by_project_with_batch_commands(home):
    before = _tree_digest(home["home"])
    rc, text = _inbox(home, "--stale-before", "2026-10-01", "--include-paused")
    _show(f"---- ① lybra owner inbox --stale-before 2026-10-01 --include-paused 原文 exit {rc} ----\n{text}")
    assert rc == 0 and _tree_digest(home["home"]) == before, "遗留视图只读零写入"
    assert text.startswith("遗留待决(发生早于 2026-10-01) — 5 项 / 2 个项目")
    assert "■ alpha(4 项)" in text and "■ gamma(1 项)" in text
    b1 = next(ln for ln in text.splitlines() if "ALPHA-B1" in ln and "建议" in ln)
    assert "卡 ALPHA-B1 [blocked]「ALPHA-B1 人肉期遗留」" in b1 and "卡住: needs_owner is true; 历史僵尸卡隔离待分诊" in b1
    assert b1.endswith("→ 建议: 冻结为历史")
    assert next(ln for ln in text.splitlines() if "ALPHA-P1" in ln and "建议" in ln).endswith("→ 建议: 撤回")
    assert "ALPHA-L1" not in text, "近期 loop owner_needed 不算遗留"
    b_ids = _ids(home, "ALPHA-B1", "ALPHA-B2", "ALPHA-B3")
    assert (f"  批量冻结为历史(3 项): lybra owner decide --items {','.join(b_ids)} --choice freeze_legacy --relayed-by <转达的顾问实例|owner>"
            f" --owner-words <Owner 原话摘要> --home-root {home['home']}") in text
    view = json.loads(_inbox(home, "--stale-before", "2026-10-01", "--include-paused", "--json")[1])
    assert [g["project"] for g in view["groups"]] == [ALPHA, GAMMA] and view["count"] == 5
    assert {r["task_id"]: r["suggest"] for r in view["groups"][0]["items"]} == {
        "ALPHA-B1": "freeze_legacy", "ALPHA-B2": "freeze_legacy", "ALPHA-B3": "freeze_legacy", "ALPHA-P1": "withdraw"}
    rc, text = _inbox(home, "--stale-before", "2026-13-40")
    assert rc == 2 and "--stale-before 须为 YYYY-MM-DD" in text


def test_item1_batch_decide_freeze_preview_then_confirm(home):
    ids = _ids(home, "ALPHA-B1", "ALPHA-B2")
    argv = ("owner", "decide", "--items", ",".join(ids), "--choice", "freeze_legacy", "--home-root", str(home["home"]), *_relay())
    before, status = _tree_digest(home["home"]), _git(home["home"], "status", "--porcelain", "-uall")
    rc, text = _cli(*argv)
    _show(f"---- ① 批量 decide freeze_legacy 预演 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "预演(零写入" in text and "批量 2 项" in text
    assert "解除动作 freeze_legacy(既有口 project freeze-legacy)" in text and "卡 ALPHA-B1, ALPHA-B2 ok=True verdict=PASS" in text
    assert _tree_digest(home["home"]) == before and _git(home["home"], "status", "--porcelain", "-uall") == status, "预演零写入"
    head = _git(home["home"], "rev-parse", "HEAD")
    rc, text = _cli(*argv, "--confirm")
    _show(f"---- ① 批量 decide freeze_legacy --confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and text.count("落账: verdict=PASS") == 1, "同一治理根一次落账"
    from tools.aipos_cli.legacy_baseline import frozen_tasks, read_manifest

    entries = read_manifest(home["alpha"])
    assert len(entries) == 1 and entries[0]["task_ids"] == ["ALPHA-B1", "ALPHA-B2"]
    decision_ids = sorted(f"owner-inbox-{i[3:].lower()}" for i in ids)
    assert sorted(str(entries[0]["owner_policy_ref"]).split(",")) == decision_ids, "清单条目记 Owner 裁决引用"
    _show(f"---- ① 存量冻结清单条目原文 {entries[0]['path'].name} ----\n{entries[0]['path'].read_text(encoding='utf-8')}")
    assert {"ALPHA-B1", "ALPHA-B2"} <= set(frozen_tasks(home["alpha"]))
    names = _git(home["home"], "show", "--name-only", "--format=", "HEAD").splitlines()
    _show(f"[① 落账 git show --name-only HEAD] {names}\n[① git log -1] {_git(home['home'], 'log', '-1', '--format=%B')}")
    assert _git(home["home"], "rev-list", "--count", f"{head}..HEAD") == "1"
    assert all(f"{ALPHA}/5_tasks/records/owner_decisions/{d}.md" in names for d in decision_ids)
    assert any(n.startswith(f"{ALPHA}/") and "legacy" in n for n in names) and _clean_and_pushed(home)
    left = [i.get("task_id") for i in _inbox_json(home)["items"]]
    assert "ALPHA-B1" not in left and "ALPHA-B2" not in left and "ALPHA-B3" in left, "冻结卡不再进收件箱(四视图同一口径)"


def test_item1_blocked_card_withdraw_via_ruling(home):
    (iid,) = _ids(home, "ALPHA-B3")
    argv = ("owner", "decide", "--item", iid, "--choice", "withdraw", "--note", "重复认领的脏卡", "--home-root", str(home["home"]))
    rc, text = _cli(*argv, *_relay(OWNER, "B3 撤掉"))
    _show(f"---- ① blocked 卡 decide withdraw 预演 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "解除动作 withdraw_card(既有队列出口): ok=True" in text
    rc, text = _cli(*argv, *_relay(OWNER, "B3 撤掉"), "--confirm")
    _show(f"---- ① blocked 卡 decide withdraw --confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "落账: verdict=PASS" in text
    card = home["alpha"] / "5_tasks" / "queue" / "withdrawn" / "alpha-b3.md"
    assert card.is_file() and not (home["alpha"] / "5_tasks" / "queue" / "blocked" / "alpha-b3.md").exists()
    fm_text = card.read_text(encoding="utf-8")
    _show(f"---- ① 撤回后卡面 frontmatter 原文 ----\n{fm_text.split('# ALPHA-B3')[0]}")
    decision_id = f"owner-inbox-{iid[3:].lower()}"
    assert "status: withdrawn" in fm_text and f"Owner inbox ruling {decision_id}" in fm_text, "撤回原因记 Owner 裁决引用"
    assert _clean_and_pushed(home)


def test_item1_blocked_withdraw_without_ruling_is_refused(home):
    from tools.aipos_cli.board_adapter import withdraw_task

    resp = withdraw_task(task_id="ALPHA-B1", actor=DRIVER, reason="想撤", dry_run=True, repo_root=home["alpha"])
    _show(f"---- ① 无裁决引用撤 blocked 卡(既有口)拒因原文 ----\n{resp.get('blocking_reasons')}")
    assert resp["verdict"] == "BLOCK" and any("requires an Owner ruling reference" in r for r in resp["blocking_reasons"])
    resp = withdraw_task(task_id="ALPHA-B1", actor=DRIVER, reason="想撤", dry_run=False, repo_root=home["alpha"],
                         owner_ruling_ref="owner-inbox-0000000000")
    _show(f"---- ① 伪造裁决引用(确认)拒因原文 ----\n{resp.get('blocking_reasons')}")
    assert not resp["ok"] and any("not found" in r for r in resp["blocking_reasons"])
    assert (home["alpha"] / "5_tasks" / "queue" / "blocked" / "alpha-b1.md").is_file()


def test_item1_batch_is_all_or_nothing(home):
    ids = _ids(home, "ALPHA-B1", "ALPHA-L1")  # loop 事项无 freeze_legacy 选项
    before = _tree_digest(home["home"])
    rc, text = _cli("owner", "decide", "--items", ",".join(ids), "--choice", "freeze_legacy", "--home-root", str(home["home"]),
                    *_relay(), "--confirm")
    _show(f"---- ① 批量含不适用选项的事项 原文 exit {rc} ----\n{text}")
    assert rc == 2 and "不属事项" in text and _tree_digest(home["home"]) == before
    rc, text = _cli("owner", "decide", "--items", f"{ids[0]},OI-nonexistent", "--choice", "freeze_legacy",
                    "--home-root", str(home["home"]), *_relay(), "--confirm")
    assert rc == 4 and "OI-nonexistent" in text and _tree_digest(home["home"]) == before
    rc, text = _cli("owner", "decide", "--item", ids[0], "--items", ids[0], "--choice", "freeze_legacy", "--home-root", str(home["home"]),
                    *_relay())
    assert rc == 2 and "须且只能给 --item" in text


# ===========================================================================
# ② 暂停项目: set-meta --status 两阶段 + 落账; 收件箱缺省不列, --include-paused 列
# ===========================================================================

def test_item2_paused_project_hidden_by_default(home):
    pj = home["gamma"] / "project.json"
    before = pj.read_text(encoding="utf-8")
    rc, text = _cli("project", "set-meta", GAMMA, "--home-root", str(home["home"]), "--status", "paused")
    _show(f"---- ② set-meta --status paused 预演 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "project set-meta gamma: 预览" in text and '+  "status": "paused"' in text.replace('",', '"') and pj.read_text(encoding="utf-8") == before
    rc, text = _cli("project", "set-meta", GAMMA, "--home-root", str(home["home"]), "--status", "frozen")
    assert rc == 1 and "META_VALUE_INVALID" in text and "['active', 'paused', 'test']" in text
    rc, text = _cli("project", "set-meta", GAMMA, "--home-root", str(home["home"]), "--status", "paused", "--confirm", "--actor", DRIVER)
    _show(f"---- ② set-meta --status paused --confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "已写入" in text and "落账: verdict=PASS" in text and _clean_and_pushed(home)
    assert json.loads(pj.read_text(encoding="utf-8"))["status"] == "paused"
    rc, text = _inbox(home)
    _show(f"---- ② 缺省视图(gamma 已暂停)原文 exit {rc} ----\n{text}")
    assert rc == 0 and "GAMMA-1" not in text and "(已暂停 / 测试项目缺省不列: gamma; 加 --include-paused 查看)" in text
    rc, text = _inbox(home, "--include-paused")
    _show(f"---- ② --include-paused 原文(节选 GAMMA)----\n" + "\n".join(ln for ln in text.splitlines() if "GAMMA" in ln or "gamma" in ln))
    assert rc == 0 and "GAMMA-1" in text and "测试项目缺省不列" not in text
    rc, text = _inbox(home, "--project", GAMMA)
    assert rc == 0 and "GAMMA-1" in text, "显式 --project 照列"
    rc, text = _inbox(home, "--stale-before", "2026-10-01")
    assert "■ gamma" not in text and "测试项目缺省不列: gamma" in text
    rc, text = _cli("project", "set-meta", GAMMA, "--home-root", str(home["home"]), "--status", "active", "--confirm", "--actor", DRIVER)
    assert rc == 0 and "GAMMA-1" in _inbox(home)[1], "恢复 active = 照列"


def test_item2_bad_status_value_is_named_not_hidden(home):
    pj = home["gamma"] / "project.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    data["status"] = "sleeping"
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rc, text = _inbox(home)
    _show(f"---- ② project.json status 值不合 原文 exit {rc} ----\n{text}")
    assert rc == 4 and "GAMMA-1" in text and "gamma: project.json status:" in text and "sleeping" in text


# ===========================================================================
# ③ 转达归因: 缺即拒; 记录写进既有字段; 顾问侧 loop status / next 显示转达人与原话(next 显示自定义选项标签)
# ===========================================================================

def test_item3_decide_requires_relayed_by_and_owner_words(home):
    (iid,) = _ids(home, "ALPHA-B1")
    before = _tree_digest(home["home"])
    for extra, needle in (((), "缺 --relayed-by / --owner-words"), (("--relayed-by", DRIVER), "缺 --owner-words"),
                          (("--owner-words", WORDS), "缺 --relayed-by"),
                          (("--relayed-by", DRIVER, "--owner-words", "第一句\n第二句"), "--owner-words 须为非空单行文本")):
        rc, text = _cli("owner", "decide", "--item", iid, "--choice", "acknowledge", "--home-root", str(home["home"]), *extra, "--confirm")
        _show(f"---- ③ decide {' '.join(extra[:1]) or '(都缺)'} 拒因原文 exit {rc} ----\n{text}")
        assert rc == 2 and needle in text, (extra, text)
    assert _tree_digest(home["home"]) == before, "拒 = 零写入"


def test_item3_relay_recorded_and_shown_on_advisor_side(home):
    rc, text = _cli("loop", "note", "--task-id", "ALPHA-L1", "--workspace-root", str(home["alpha"]), "--actor", DRIVER,
                    "--ask", "接口改法二选一", "--options", "plan_a=方案A 改接口", "plan_b=方案B 加适配层",
                    "--recommend", "建议 plan_b", "--confirm")
    assert rc == 0, text
    (iid,) = _ids(home, "ALPHA-L1")
    rc, text = _cli("owner", "decide", "--item", iid, "--choice", "plan_b", "--home-root", str(home["home"]),
                    *_relay(DRIVER, "先上适配层, 接口下季度再改"), "--confirm")
    _show(f"---- ③ decide(顾问转达)--confirm 原文 exit {rc} ----\n{text}")
    assert rc == 0 and f"转达: {DRIVER} · Owner 原话: 「先上适配层, 接口下季度再改」" in text
    record = (home["alpha"] / "5_tasks" / "records" / "owner_decisions" / f"owner-inbox-{iid[3:].lower()}.md").read_text(encoding="utf-8")
    _show(f"---- ③ owner_decision_record 原文 ----\n{record}")
    assert f"captured_by: {DRIVER}" in record and "decided_by_ref: owner" in record
    assert "- Rationale: Owner 原话: 先上适配层, 接口下季度再改" in record
    relay = f"(经 {DRIVER} 转达; Owner 原话: 「先上适配层, 接口下季度再改」)"
    rc, status = _cli("loop", "status", "--task-id", "ALPHA-L1", "--workspace-root", str(home["alpha"]))
    _show(f"---- ③ 顾问侧 loop status 原文 ----\n{status}")
    assert f"advisor_fix(owner_ruled) — Owner 已裁决: 选 plan_b(方案B 加适配层){relay} @" in status
    rc, nxt = _cli("next", "--task-id", "ALPHA-L1", "--workspace-root", str(home["alpha"]))
    line = next(ln for ln in nxt.splitlines() if ln.startswith("Owner 已裁决"))
    _show(f"---- ④ 顾问侧 lybra next --task-id 裁决行原文 ----\n{line}")
    assert line.startswith(f"Owner 已裁决: 选 plan_b(方案B 加适配层){relay} @"), "next 裁决行显示自定义选项标签 + 转达"


# ===========================================================================
# ④ 章程渲染含新引导 / 看板 overview 入口
# ===========================================================================

def test_item4_rendered_advisor_charter_teaches_loop_note_and_relayed_decide(f167_rig):
    rc, out, err = _f167_cli(["charter", "--role", "advisor", "--instance", ADV_A, "--workspace-root", str(f167_rig.gov)])
    assert rc == 0, err
    start = out.index("**报 owner_needed 前先写进运行记录**")
    para = out[start:out.index("**只在 `owner_needed` 时停。**", start)]
    _show(f"---- ④ 渲染后顾问章程(实例 {ADV_A})新引导原文 ----\n{para}")
    assert f"--workspace-root {f167_rig.gov} --actor {ADV_A}` 先预演, 再加 `--confirm`" in para
    assert "lybra loop note --task-id <卡ID> --ask <要 Owner 定什么>" in para
    assert f"--relayed-by {ADV_A}\n     --owner-words <Owner 原话摘要>" in para and "Owner 本人直接敲时 `--relayed-by owner`" in para
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    assert "#### Owner 待决: `lybra loop note` / `lybra owner inbox` / `lybra owner decide`(AIPOS-F168 / F170)" in skill


def _f167_cli(argv: list[str]):
    from test_aipos_f131_loop_run_status import _cli as cli3

    return cli3(argv)


def test_item4_board_overview_has_readonly_inbox_entry(home, monkeypatch: pytest.MonkeyPatch):
    from http.server import ThreadingHTTPServer

    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home["home"]))
    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'])}"
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(repo_root=home["alpha"], session_store=store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        def get(path: str) -> str:
            with urllib.request.urlopen(urllib.request.Request(base + path, headers={"Cookie": cookie}), timeout=30) as resp:
                assert resp.status == 200
                return resp.read().decode("utf-8")

        # AIPOS-F181: the board root redirects to the read-only overview; its top nav carries the inbox entry
        root = get("/")
        assert '<body class="rp-page rp-overview"' in root and 'href="/owner/inbox"' in root.split('<header class="rp-nav">', 1)[1].split("</header>", 1)[0]
        overview = get("/overview.html")  # former home stays reachable
        entry = next(ln.strip() for ln in overview.splitlines() if "owner-inbox-link" in ln and "<a " in ln)
        _show(f"---- ④ 看板 GET / overview 入口原文 ----\n{entry}")
        assert 'href="/owner/inbox"' in entry and 'data-owner-inbox-entry="readonly"' in entry
        assert "i18n.t('overview.owner_inbox')" in overview
        i18n = get("/i18n.js")
        assert "'overview.owner_inbox': 'Owner 待决收件箱'" in i18n and "'overview.owner_inbox': 'Owner inbox'" in i18n
        page = get("/owner/inbox")
        # AIPOS-F180: 页面套 F174 骨架(含语言切换内联脚本); 无授权请求 = 不载签发脚本(仍是只读页)
        assert "<form" not in page and 'src="/envelopes.js"' not in page and "data-owner-inbox-count=" in page, "入口指向只读页"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


# ===========================================================================
# ⑤ 回填: 本卡部署时汇总口把 F160–F170 一并生成更新说明(临时部署夹具)
# ===========================================================================

def test_item5_deploy_compiles_f160_to_f170_update_note(f167_rig):
    from tools.aipos_cli import advisor_updates as au

    backfill = json.loads((REPO_ROOT / "docs" / "advisor_notice_backfill.json").read_text(encoding="utf-8"))
    (f167_rig.product / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "docs" / "advisor_notice_backfill.json", f167_rig.product / "docs" / "advisor_notice_backfill.json")
    own = next(e for e in backfill["entries"] if e["task_id"] == "AIPOS-F170")
    notice = {k: v for k, v in own.items() if k != "task_id"}
    _f167_card(f167_rig.gov, "AIPOS-F170", "completed", {"advisor_notice": notice})  # 卡号形同本卡(靶场样本, 产品代码不写死)
    compiled = au.compile_on_deploy(f167_rig.product, f167_rig.gov, "AIPOS-F170")
    _show(f"---- ⑤ 部署汇总操作行原文 ----\n{compiled['line']}")
    record = json.loads(Path(compiled["written"]).read_text(encoding="utf-8"))
    _show(f"---- ⑤ 更新说明原文 {Path(compiled['written']).name} ----\n{json.dumps(record, ensure_ascii=False, indent=2)}")
    ids = [e["task_id"] for e in record["entries"]]
    assert ids[0] == "AIPOS-F170" and record["entries"][0]["source"] == "card"
    assert sorted(ids, key=lambda t: int(t.split("F")[-1])) == [f"AIPOS-F{n}" for n in range(160, 171)] and len(ids) == 11, "F170 只汇一次"
    rc, out, err = _f167_cli(["updates", "--workspace-root", str(f167_rig.gov), "--actor", ADV_A])
    _show(f"---- ⑤ 顾问 `lybra updates` 原文(节选 F168–F170)----\n"
          + "\n".join(ln for ln in out.splitlines() if any(t in ln for t in ("AIPOS-F168", "AIPOS-F169", "AIPOS-F170", "owner decide", "set-model"))))
    assert rc == 0 and "AIPOS-F170" in out and "--relayed-by <你的实例>" in out and "lybra project set-model" in out
    assert F167_PROJECT  # 靶场项目名来自 F167 夹具


# ===========================================================================
# ⑥ 声明 / 单一实现不变量
# ===========================================================================

def test_declarations_and_single_implementations():
    from tools.aipos_cli import owner_inbox, queue_mutation
    from tools.aipos_cli.workspace_config import declared_meta_keys, project_meta_declaration
    from tools.schema_loader import load_schema

    assert queue_mutation.withdraw_owner_ruling_states() == ("blocked",)
    assert queue_mutation.ALLOWED_TRANSITIONS["withdraw"] == (("pending", "claimed", "blocked"), "withdrawn")
    status = load_schema("config")["configuration_sources"]["project_json"]["schema"]["status"]
    assert status["enum"] == ["active", "paused", "test"] and status["default"] == "active" and status["required"] is False
    assert project_meta_declaration()["keys"]["status"] == "--status" and "status" in declared_meta_keys()
    decl = owner_inbox.inbox_declaration()
    assert decl["options"]["freeze_legacy"]["effect"] == "freeze_legacy"
    assert decl["categories"]["needs_owner_card"]["options_by_queue_state"]["blocked"] == ["withdraw", "freeze_legacy", "new_card", "acknowledge"]
    decide = load_schema("verbs")["verbs"]["lybra_owner_decide"]
    assert set(decide["parameters"]["required"]) == {"choice", "relayed_by", "owner_words"}
    src = Path(owner_inbox.__file__).read_text(encoding="utf-8")
    # 只经既有出口: 决策记录门侧实现 / 撤卡 / 冻结 / 同一落账口; 不自写文件 / 不自起 git
    for call in ("record_owner_decision(", "execute_dry_run(", "withdraw_task(", "plan_freeze_legacy(", "write_freeze_entry(",
                 "land_written_paths(", "project_status("):
        assert call in src, call
    assert "write_text(" not in src and "subprocess" not in src and "\"git\"" not in src
    for literal in ("/home/", "2_projects", "chris", "kiwiai"):
        assert literal not in src.split('"""', 2)[2], f"产品代码写死 {literal!r}"
    assert src.count("def collect_inbox(") == 1 and src.count("status in hidden_statuses") == 1, "暂停过滤只在 collect_inbox 一处"  # AIPOS-F181: paused + test
    assert subprocess.run(["git", "grep", "-n", "def withdraw_owner_ruling_problem", "--", "tools"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.count("\n") == 1
