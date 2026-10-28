"""AIPOS-F247 — history & audit: `lybra history search` + the board page (same search, same index, same expanded content).

Rig = temporary HOME (the index lives under it, never in a governance root) + temporary home root with two projects built through
product commands (`lybra project new`, `lybra project freeze-legacy`): a customer-shaped project with two enrolled advisors, closed
cards carrying gate-shaped return / verdict / closure records, report files and an audit card, one frozen manual-era card; and a
second project. Project / instance / card names are rig values (the product hard-codes none). The board runs in a thread of the test
process and is shut down afterwards. No real credential, no real governance root, no real gate.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  - single source of rig helpers
from test_aipos_f171_roadmap_board_readonly import _cli  # noqa: E402
from test_aipos_f181_board_usability import _free_port, _get  # noqa: E402
from test_aipos_f246_board_home import _enroll, forbidden_hits, visible_text  # noqa: E402

C, O = "f247-cust", "f247-other"  # rig project names
ADV_X, ADV_Y, ADV_O = f"advisor-x.{C}.h1", f"advisor-y.{C}.h1", f"advisor.{O}.h1"
DAY = 86400


def _show(text: str) -> None:
    print(text, flush=True)


def _ok(rc_out) -> None:
    rc, out, err = rc_out
    assert rc == 0, out + err


def _age(path: Path, days: float) -> None:
    moment = time.time() - days * DAY
    os.utime(path, (moment, moment))


def _card(gov: Path, project: str, state: str, task_id: str, *, title: str, created_by: str, body: str = "", **extra) -> Path:
    from tools.aipos_cli.record_writer import render_markdown
    from tools.aipos_cli.task_loader import queue_root_for

    meta = {"task_id": task_id, "title": title, "project": project, "status": state, "task_mode": extra.pop("task_mode", "code"),
            "created_by": created_by, "created_at": "2026-10-01T00:00:00Z", **extra}
    path = queue_root_for(gov) / state / f"{task_id.lower()}.md"
    _write(path, render_markdown(meta, f"# {task_id}\n\n{body}\n"))
    return path


def _record(gov: Path, kind: str, task_id: str, name: str, meta: dict, body: str = "") -> Path:
    from tools.aipos_cli.record_writer import record_dir, render_markdown

    path = record_dir(gov, kind, task_id) / name
    _write(path, render_markdown(meta, body or f"# {name}\n"))
    return path


def _closed(gov: Path, project: str, task_id: str, *, title: str, owner: str, goal: str, summary: str, audit: str,
            verdicts: tuple[str, ...] = ("PASS",), days: float = 1.0, evidence: str | None = "python3 verify.py --offline",
            rework: list | None = None) -> None:
    """One closed business card the way the gate leaves it: card in completed/, return record + RETURN.md, verdict records (+ audit
    report with the runtime evidence section), closure record aged `days` (the closing time = its mtime, board_home.closed_at)."""
    from tools.aipos_cli.next_resolver import default_return_file, verdict_artifact_dir

    extra = {"rework_rounds": rework} if rework else {}
    _card(gov, project, "completed", task_id, title=title, created_by=owner, body=f"一句话:{goal}", **extra)
    ret = default_return_file(gov, task_id)
    _write(ret, f"---\ncommit_sha: abc\n---\n# {task_id} RETURN\n\n## 一句话结论\n\n{summary}\n第二行接着说完。\n\n## 证据\n\n- ok\n")
    _record(gov, "returns", task_id, f"return_{task_id}_20261020_000000_exec.md",
            {"record_type": "return_record", "task_id": task_id, "returned_at": "2026-10-20T00:00:00Z",
             "completion_report_ref": ret.relative_to(gov).as_posix()})
    for n, verdict in enumerate(verdicts, 1):
        report = verdict_artifact_dir(gov, f"{task_id}R{n if n > 1 else ''}") / "RETURN.md"
        section = (f"## 独立运行证据\n\n```json\n" + json.dumps([{"command": evidence, "exit_code": 0, "worktree": "/w"}]) + "\n```\n"
                   if evidence else "## 独立运行证据\n\n真机录屏 + 日志核对\n")
        _write(report, f"# audit\n\n## 审计结论\n\n裁决: {verdict} — {audit} 第 {n} 轮\n\n{section}")
        _record(gov, "audit_verdicts", task_id, f"verdict_{task_id}_2026102{n}_000000_audit.md",
                {"record_type": "audit_verdict_record", "verdict": verdict, "verdict_id": f"verdict_{task_id}_{n}",
                 "reviewed_task_id": task_id, "verdict_at": f"2026-10-2{n}T00:00:00Z", "findings_summary_present": False,
                 "report_source_ref": report.relative_to(gov).as_posix(), "evidence_refs": [report.relative_to(gov).as_posix()]})
    closure = _record(gov, "closures", task_id, f"close_{task_id}_20261025_000000_exec.md", {"record_type": "closure", "task_id": task_id})
    _age(closure, days)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.project_upgrade import clear_badge_cache

    clear_badge_cache()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    for name, actor in ((C, ADV_X), (O, ADV_O)):
        _ok(_cli("project", "new", name, "--home-root", str(hroot), "--actor", actor))
    gc, go = hroot / C, hroot / O
    _enroll(gc, [ADV_X, ADV_Y], owner=True)
    _enroll(go, [ADV_O], owner=False)
    _closed(gc, C, "X-10", title="X-10 Android 维修直连打通解绑再绑定控锁", owner=ADV_X, goal="修正绑定握手, 解绑后能再绑定",
            summary="解绑再绑定离线跑通, 两边基线 0 失败", audit="七条验收独立核验通过", days=2)
    _closed(gc, C, "X-11", title="X-11 Signed package build", owner=ADV_X, goal="正式签名包构建核验",
            summary="签名包构建成功, 证书一致", audit="独立重跑构建通过", verdicts=("FAIL", "PASS"), days=40,
            rework=[{"round": 1, "focus_items": ["证据不全: 缺签名证书指纹"], "created_at": "2026-10-21T00:00:00Z"}])
    _closed(gc, C, "Y-20", title="Y-20 Web 登录凭据轮换", owner=ADV_Y, goal="凭据按名引用, 轮换后看板可登录",
            summary="凭据轮换完成", audit="轮换核验通过", days=3, evidence=None)
    _card(gc, C, "completed", "X-10R", title="Audit X-10", created_by="gate_derivation", task_mode="audit", reviewed_task_id="X-10")
    _card(gc, C, "claimed", "L-1", title="L-1 人工期解绑方案调研", created_by=ADV_X, body="一句话:人工期调研解绑流程")
    _card(gc, C, "claimed", "X-12", title="X-12 still in flight 解绑", created_by=ADV_X)
    _closed(go, O, "O-1", title="O-1 Other project 签名 note", owner=ADV_O, goal="other goal", summary="other done", audit="ok", days=5,
            evidence="mysql --password=" + "Zq7" + "Kw9mPlq4 -u root")  # a credential-shaped value (built here, not a secret)
    init_governance_repo(hroot)
    _ok(_cli("project", "freeze-legacy", "--workspace-root", str(gc), "--task-ids", "L-1", "--reason", "manual-gate era (rig)",
             "--actor", ADV_X, "--confirm"))
    return {"home": hroot, "gc": gc, "go": go, "tmp": tmp_path, "user_home": home}


@pytest.fixture
def board(rig):
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'], token_ref='owner-fixture')}"
    serve_root = rig["tmp"] / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=serve_root, home_root=rig["home"], session_store=store, connection_paths=[],
        board_audit_log_path=rig["tmp"] / "board-runtime" / "audit.jsonl"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**rig, "base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _page(b, path: str) -> str:
    status, page, _h = _get(b, path)
    assert status == 200, (path, page[:400])
    assert "rp-page-error" not in page, page[-800:]
    return page


def _search_json(rig, *argv: str) -> tuple[int, dict]:
    rc, out, err = _cli("history", "search", *argv, "--home-root", str(rig["home"]), "--json")
    assert rc in (0, 1), out + err
    return rc, json.loads(out)


def _hits(page: str) -> list[str]:
    return re.findall(r'<details class="hs-hit" data-task-id="([^"]+)"', page)


# ===========================================================================
# item 1 / 2: CLI search (AND, Chinese substring, filters, audit cards folded in, legacy) + page = same result
# ===========================================================================

def test_item1_cli_keywords_and_filters(rig):
    rc, res = _search_json(rig, "解绑", "再绑定")
    _show(f"[item1 CLI 解绑 再绑定] rc={rc} ids={[r['task_id'] for r in res['results']]} total={res['total']}")
    assert rc == 0 and [r["task_id"] for r in res["results"]] == ["X-10"]  # AND: L-1 / X-12 carry 解绑 only; X-12 is in flight
    hit = res["results"][0]
    assert hit["project"] == C and hit["owner"] == ADV_X and hit["verdict_key"] == "pass" and hit["audit"]["rounds"] == 1
    assert hit["done"] == "解绑再绑定离线跑通, 两边基线 0 失败 第二行接着说完"  # folded one-sentence summary (paragraph mode)
    assert hit["verification"]["entries"] == [{"command": "python3 verify.py --offline", "exit_code": 0}]
    assert "七条验收独立核验通过" in hit["audit"]["summary"] and hit["audit"]["rejected"] is False
    rc, res = _search_json(rig, "SIGNED")  # case-insensitive
    assert [r["task_id"] for r in res["results"]] == ["X-11"]
    assert res["results"][0]["audit"]["rejected"] is True and res["results"][0]["audit"]["reject_reason"] == "证据不全: 缺签名证书指纹"
    assert res["results"][0]["audit"]["rounds"] == 2 and res["results"][0]["verdict_key"] == "pass"
    rc, res = _search_json(rig, "签名")
    assert sorted(r["task_id"] for r in res["results"]) == ["O-1", "X-11"]
    rc, res = _search_json(rig, "签名", "--project", O)
    assert [r["task_id"] for r in res["results"]] == ["O-1"]
    rc, res = _search_json(rig, "--advisor", ADV_Y)
    assert [r["task_id"] for r in res["results"]] == ["Y-20"]
    rc, res = _search_json(rig, "--rejected-once")
    assert [r["task_id"] for r in res["results"]] == ["X-11"]
    rc, res = _search_json(rig, "--since", "30d")
    assert "X-11" not in [r["task_id"] for r in res["results"]] and "X-10" in [r["task_id"] for r in res["results"]]
    rc, res = _search_json(rig, "解绑", "--legacy", "only")
    assert [r["task_id"] for r in res["results"]] == ["L-1"] and res["results"][0]["legacy"] is True
    assert res["results"][0]["verdict_key"] == "legacy"
    rc, res = _search_json(rig, "解绑", "--legacy", "exclude")
    assert [r["task_id"] for r in res["results"]] == ["X-10"]
    rc, res = _search_json(rig)
    ids = [r["task_id"] for r in res["results"]]
    _show(f"[item1 no keywords, newest first] {ids}")
    assert "X-10R" not in ids and "X-12" not in ids  # audit card folded into the reviewed card; in-flight card is not history
    assert ids == ["X-10", "Y-20", "O-1", "X-11", "L-1"]  # closing time (closure record) newest first; no closing date (manual era) last
    rc, out, err = _cli("history", "search", "没有这个词", "--home-root", str(rig["home"]))
    _show(f"[item1 no result raw] rc={rc}\n{out}")
    assert rc == 1 and "换个说法" in out
    rc, out, err = _cli("history", "search", "x", "--since", "yesterday", "--home-root", str(rig["home"]))
    assert rc == 2 and "--since" in err
    rc, out, err = _cli("history", "search", "x", "--project", "nope", "--home-root", str(rig["home"]))
    assert rc == 2 and C in err
    rc, out, err = _cli("history", "search", "解绑", "再绑定", "--home-root", str(rig["home"]))
    _show(f"[item1 CLI text raw]\n{out}")
    assert rc == 0 and "X-10" in out and "做了什么: 解绑再绑定离线跑通" in out and "审计: 审计通过, 共 1 轮" in out


def test_item2_page_equals_cli_and_filters(board):
    for argv, query in (((), ""), (("解绑",), "q=" + quote("解绑")), (("签名",), "q=" + quote("签名")),
                        (("签名", "--project", C), "q=" + quote("签名") + f"&project={C}"),
                        (("--rejected-once",), "rejected=1"), (("解绑", "--legacy", "only"), "q=" + quote("解绑") + "&legacy=only"),
                        (("--advisor", ADV_Y), f"advisor={quote(ADV_Y)}"), (("--since", "30d"), "since=30d")):
        _rc, res = _search_json(board, *argv)
        page = _page(board, f"/owner/history?{query}")
        _show(f"[item2 CLI {argv} = page ?{query}] {[r['task_id'] for r in res['results']]} / {_hits(page)}")
        assert _hits(page) == [r["task_id"] for r in res["results"]], (argv, query)
    page = _page(board, "/owner/history?q=" + quote("解绑 再绑定"))
    hit = re.search(r'<details class="hs-hit".*?</details>', page, re.S).group(0)
    _show(f"[item2 page hit raw] {hit}")
    assert ' open>' in hit and "<mark>解绑再绑定</mark>" in hit  # adjacent hits of two keywords merge into one highlight
    assert "2 天" not in hit and "审计通过" in hit and 'class="hs-id">X-10<' in hit
    for label in ("做了什么", "怎么验证", "审计", "产物"):
        assert f"<dt>{label}</dt>" in hit, label
    assert 'data-nav="history" aria-current="page"' in page and 'name="q" value="解绑 再绑定"' in page
    none = _page(board, "/owner/history?q=" + quote("没有这个词"))
    assert 'class="hs-none"' in none and "换个说法" in none and 'data-total="0"' in none
    chips = _page(board, f"/owner/history?project={C}")
    adv = re.search(r'<div class="hs-filters" data-filters="advisor">.*?</div>', chips, re.S)
    assert adv is not None and "advisor=" in adv.group(0)  # two enrolled advisors -> advisor chips for the chosen project
    bad = _page(board, "/owner/history?since=yesterday")
    assert "筛选参数不对" in bad
    en = _page(board, "/owner/history?lang=en&q=" + quote("解绑"))
    assert "History &amp; audit" in en and "Audit passed" in en


# ===========================================================================
# item 3: expanded content = the one derivation (history_search.card_entry over the existing parsers)
# ===========================================================================

def test_item3_expanded_content_from_the_existing_parsers(board, monkeypatch):
    from tools.aipos_cli import history_search, next_resolver, roadmap_drift

    calls = {"summary": 0, "evidence": 0}
    real_summary, real_evidence = next_resolver.extract_return_summary_text, roadmap_drift.card_evidence

    def summary_spy(content, source=None, *, paragraph=False):
        calls["summary"] += 1
        return real_summary(content, source, paragraph=paragraph)

    def evidence_spy(root, card, truth, criterion=None):
        calls["evidence"] += 1
        return real_evidence(root, card, truth, criterion)

    monkeypatch.setattr(next_resolver, "extract_return_summary_text", summary_spy)
    monkeypatch.setattr(roadmap_drift, "card_evidence", evidence_spy)
    from tools.aipos_cli.next_resolver import _verdict_reason_excerpt
    from tools.aipos_cli.task_loader import find_task_card

    path, state = find_task_card(board["gc"], "X-11")
    entry = history_search.card_entry(board["gc"], path, state)
    _show(f"[item3 card_entry X-11] {json.dumps(entry, ensure_ascii=False)} calls={calls}")
    assert calls["summary"] >= 1 and calls["evidence"] == 1
    verdict_path = board["gc"] / entry["audit"]["record_ref"]
    from tools.aipos_cli.frontmatter import require_frontmatter

    assert entry["audit"]["summary"] == history_search._plain(
        _verdict_reason_excerpt(verdict_path, require_frontmatter(verdict_path)[0], workspace_root=board["gc"]), 400)
    rc, res = _search_json(board, "SIGNED")
    cli = res["results"][0]
    assert {k: cli[k] for k in ("done", "verification", "audit", "artifacts", "return_ref")} == \
        {k: entry[k] for k in ("done", "verification", "audit", "artifacts", "return_ref")}
    page = _page(board, "/owner/history?q=SIGNED")
    assert entry["done"] in visible_text(page) and entry["audit"]["reject_reason"] in visible_text(page)
    y = _search_json(board, "--advisor", ADV_Y)[1]["results"][0]
    assert y["verification"]["entries"] == [] and y["verification"]["excerpt"] == "真机录屏 + 日志核对"
    doc = _page(board, f"/owner/history?project={C}&task=X-11&doc=audit&q=SIGNED")
    _show(f"[item3 audit report view raw] {re.search(r'<div class=.hs-doc.*?</div>', doc, re.S).group(0)[:600]}")
    assert 'data-doc="audit"' in doc and "独立重跑构建通过 第 2 轮" in doc and "回到检索结果" in doc
    ret = _page(board, f"/owner/history?project={C}&task=X-11&doc=return")
    assert 'data-doc="return"' in ret and "签名包构建成功" in ret and "commit_sha" not in visible_text(ret)
    nope = _page(board, f"/owner/history?project={C}&task=..%2F..%2Fproject.json&doc=return")
    assert "这份报告找不到" in nope and "hs-doc" not in nope  # only the report the derivation names, never a path from the URL


# ===========================================================================
# item 4: the index (disposable, outside the governance root, incremental by truth identity) + freshness
# ===========================================================================

def test_item4_racy_window_not_kept(rig):
    """Production racy window (2 s, AIPOS-F245 judgement): truth written a moment ago is used for this search but not kept in the
    index (the next search derives those cards again) — a same-tick rewrite can never be served stale."""
    from tools.aipos_cli.history_search import search

    first = search(rig["home"], ["签名"])
    second = search(rig["home"], ["签名"])
    one, two = ({i["project"]: i for i in r["index"]} for r in (first, second))
    _show(f"[item4 racy window: fixture just written] first={one[C]} second={two[C]}")
    assert [r["task_id"] for r in first["results"]] == [r["task_id"] for r in second["results"]] == ["O-1", "X-11"]
    assert two[C]["rebuilt"] == two[C]["cards"] and two[C]["reused"] == 0


def test_item4_index_rebuild_incremental_and_fresh(rig, monkeypatch):
    from tools.aipos_cli import frontmatter
    from tools.aipos_cli.history_search import index_dir, search

    monkeypatch.setattr(frontmatter, "_FILE_CACHE_RACY_WINDOW_NS", 0)  # the fixture files were written a moment ago (see above)
    first = search(rig["home"], ["签名"])
    idx = {i["project"]: i for i in first["index"]}
    _show(f"[item4 cold] {idx}")
    assert idx[C]["rebuilt"] == idx[C]["cards"] and idx[C]["reused"] == 0 and idx[C]["rebuilt_reason"] == "absent"
    for i in idx.values():
        path = Path(i["path"])
        assert path.is_file() and path.parent == index_dir() and rig["home"] not in path.parents  # never in a governance root
        assert str(rig["user_home"]) in str(path)
    warm = search(rig["home"], ["签名"])
    widx = {i["project"]: i for i in warm["index"]}
    _show(f"[item4 warm] {widx} took_ms={warm['took_ms']}")
    assert widx[C]["rebuilt"] == 0 and widx[C]["reused"] == widx[C]["cards"] and warm["took_ms"] < 1000
    # a new closed card in the truth: the very next search sees it, only that card is derived
    _closed(rig["gc"], C, "X-30", title="X-30 签名 newly closed", owner=ADV_X, goal="g", summary="新结案的签名卡", audit="ok", days=0.5)
    fresh = search(rig["home"], ["签名"])
    fidx = {i["project"]: i for i in fresh["index"]}
    _show(f"[item4 after new closed card] ids={[r['task_id'] for r in fresh['results']]} index={fidx[C]}")
    assert "X-30" in [r["task_id"] for r in fresh["results"]] and fidx[C]["rebuilt"] == 1
    # a new verdict record for an indexed card: only that card is derived again
    from tools.aipos_cli.record_writer import record_dir

    _record(rig["gc"], "audit_verdicts", "X-10", "verdict_X-10_20261029_000000_audit.md",
            {"record_type": "audit_verdict_record", "verdict": "PASS_WITH_NOTES", "verdict_id": "verdict_X-10_9",
             "verdict_at": "2026-10-29T00:00:00Z"})
    assert record_dir(rig["gc"], "audit_verdicts", "X-10").is_dir()
    again = search(rig["home"], ["解绑", "再绑定"])
    aidx = {i["project"]: i for i in again["index"]}
    assert aidx[C]["rebuilt"] == 1 and again["results"][0]["audit"]["rounds"] == 2 and again["results"][0]["verdict_key"] == "pass_with_notes"
    # delete the index: the next search rebuilds it
    for i in again["index"]:
        Path(i["path"]).unlink()
    rebuilt = search(rig["home"], ["签名"])
    ridx = {i["project"]: i for i in rebuilt["index"]}
    _show(f"[item4 after delete] {ridx[C]}")
    assert ridx[C]["rebuilt"] == ridx[C]["cards"] and [r["task_id"] for r in rebuilt["results"]] == [r["task_id"] for r in fresh["results"]]
    # a corrupt index is just rebuilt (it is a derived cache), the reason is reported
    Path(ridx[C]["path"]).write_text("{not json", encoding="utf-8")
    from tools.aipos_cli import history_search

    history_search._LOADED.clear()
    broken = search(rig["home"], ["签名"])
    bidx = {i["project"]: i for i in broken["index"]}
    assert bidx[C]["rebuilt"] == bidx[C]["cards"] and str(bidx[C]["rebuilt_reason"]).startswith("JSONDecodeError")
    # a card moved out of the closed directory (reopened) disappears from history
    from tools.aipos_cli.task_loader import queue_root_for

    src = queue_root_for(rig["gc"]) / "completed" / "x-30.md"
    src.rename(queue_root_for(rig["gc"]) / "claimed" / "x-30.md")
    gone = search(rig["home"], ["签名"])
    assert "X-30" not in [r["task_id"] for r in gone["results"]]


def test_item4_truth_record_keeps_only_the_cards_own_truth(rig):
    """The per-card fingerprint (task_loader.truth_record, the F245 recorder) keeps governance-root truth only: not other cards'
    queue files, not queue directory listings, not project.json (project settings are the index header)."""
    from tools.aipos_cli.history_search import _keep_truth, card_entry
    from tools.aipos_cli.task_loader import find_task_card, queue_root_for, task_card_lookup_scope, truth_record
    from tools.aipos_cli.workspace_config import project_json_path

    gc = rig["gc"]
    path, state = find_task_card(gc, "X-10")
    with task_card_lookup_scope(gc):
        entry, truth = truth_record(lambda: card_entry(gc, path, state), keep=_keep_truth(gc, queue_root_for(gc), path, project_json_path(gc)))
    paths = [p for p, _m, _i in truth["stats"]]
    _show(f"[item4 truth of X-10] refused={truth['refused']} paths={paths}")
    assert truth["refused"] is None and str(path) in paths
    assert not any(p.startswith(str(queue_root_for(gc))) and p != str(path) for p in paths)
    assert str(project_json_path(gc)) not in paths and all(p.startswith(str(gc)) for p in paths)
    assert any("/returns/X-10" in p for p in paths) and any("/audit_verdicts/X-10" in p for p in paths)


# ===========================================================================
# item 4 (move): project page no longer lists closed cards; 「历史存量」 only in the history page filters; nav
# ===========================================================================

def test_item4_project_page_links_history_and_words(board):
    from tools.aipos_cli.board_home import home_declaration

    cards = _page(board, f"/owner/project?project={C}&view=cards")
    closed = re.findall(r'<p class="rp-closed" data-closed="(\d+)"><a href="([^"]+)">([^<]+)</a></p>', cards)
    _show(f"[item4 cards view closed links] {closed}")
    assert closed and all("/owner/history?project=" in href for _n, href, _t in closed)
    assert "X-10" not in visible_text(cards) and "rp-row" not in "".join(re.findall(r'<p class="rp-closed".*?</p>', cards))
    assert 'data-nav="engineering" aria-current="page"' in cards
    brief = _page(board, f"/owner/project?project={C}")
    assert 'data-link="history"' in brief and f"/owner/history?project={C}" in brief
    home = _page(board, "/owner/home")
    for page in (home, brief, cards):
        assert "历史存量" not in visible_text(page)
    nav = re.search(r'<header class="rp-nav">.*?</header>', home, re.S).group(0)
    assert 'href="/owner/history" data-nav="history"' in nav
    hist = _page(board, "/owner/history")
    filters = "".join(re.findall(r'<div class="hs-filters".*?</div>', hist, re.S))
    outside = hist.replace(filters, "")
    assert "历史存量" in visible_text(filters) and "历史存量" not in visible_text(outside)
    words = [w for w in home_declaration()["forbidden_words"] if w != "历史存量"]
    hits = forbidden_hits(outside, words, include_authored=False)
    _show(f"[item4 history page interface forbidden words (except the legacy filter)] {hits}")
    assert hits == []


def test_page_budget_and_declarations(board):
    from tools.aipos_cli.board_pages import pages_declaration
    from tools.aipos_cli.history_search import LEGACY_CHOICES, declaration

    decl = pages_declaration()
    page = _page(board, "/owner/history")
    assert len(page.encode("utf-8")) <= int(decl["history"]["page_budget_bytes"])
    assert decl["pages"]["history"] == "/owner/history" and "history" in decl["home"]["nav"]
    verb = declaration()
    assert list(verb["parameters"]["properties"]["legacy"]["enum"]) == list(LEGACY_CHOICES)
    from web.board.app import _I18N_TEMPLATES

    keys = {k for k in _I18N_TEMPLATES["zh"] if k.startswith("hs_")}
    assert keys == {k for k in _I18N_TEMPLATES["en"] if k.startswith("hs_")}
    from tools.aipos_cli.history_search import VERDICT_WORDS

    assert {f"hs_verdict_{w}" for w in VERDICT_WORDS} <= keys


def test_credential_values_masked_in_history(board):
    """Report text shown by the history (CLI and page) passes the ingest credential judgement (artifact_ingest.credential_redact =
    credential_hits' matcher): the value is masked, and the index only holds the masked text (searching the value finds nothing)."""
    value = "Zq7" + "Kw9mPlq4"
    rc, res = _search_json(board, "--project", O)
    cmd = res["results"][0]["verification"]["entries"][0]["command"]
    _show(f"[credential mask] {cmd}")
    assert value not in cmd and "[已打码]" in cmd and "--password=" in cmd
    page = _page(board, f"/owner/history?project={O}")
    assert value not in page and "[已打码]" in page
    rc, res = _search_json(board, value)
    assert rc == 1 and res["total"] == 0
    from tools.aipos_cli.history_search import index_path

    assert value not in index_path(O, board["go"]).read_text(encoding="utf-8")
