"""AIPOS-F181 - board usability, part one (gap #169/#173/#174/#179/#182).

Items: (1) one skeleton + one top nav for every board page, the board root redirects to the overview, no command-line hints and no
server paths in titles / page body (only inside folded technical details); (2) cards grouped by subproject (one derivation,
machine_zone.card_stats / card_subproject): overview project -> subproject counts with the owning advisor, project page and roadmap
page filter by subproject, in flight + to do listed, closed / finished folded; (3) counts exclude frozen legacy cards (history line
instead), project.json status=test hides probe projects by default (page switch), unlinked ratio only counts cards created after the
lane's first milestone declaration; (4) inbox items in plain words, engineering lines folded under "show details", request cards keep
their item folded; (5) the upgrade badge is cached per fingerprint (hot overview without git subprocesses).

Rig = temporary HOME + temporary home root (git repo + temporary bare remote) with four projects built through product commands
(`lybra project new / set-meta / freeze-legacy / roadmap ... --confirm`): a multi-subproject project shaped like a project onboarded
from the manual-gate era (two advisors sharing repos, frozen legacy cards, cards before and after the roadmap, an authorization
request), a single-repo project, a status=test probe and a paused project. Project / instance / card names are rig values (the
product hard-codes none). The board runs in a thread of the test process (urllib test client) and is shut down afterwards. No real
credential, no real governance root, no real gate. With F181_SNAPSHOT_DIR set, every page is also written there as standalone HTML
(stylesheet inlined) for the Owner preview.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  - single source of rig helpers
from test_aipos_f171_roadmap_board_readonly import _cli, _git  # noqa: E402
from test_aipos_f180_grant_request import _ended_run, _policy  # noqa: E402

M, S, T, Q = "f181-multi", "f181-single", "f181-probe", "f181-paused"  # rig project names
ADV_A, ADV_B, ADV_S = f"advisor-a.{M}.h1", f"advisor-b.{M}.h1", f"advisor.{S}.h1"
PAGES = ("/owner/overview", "/owner/project", "/owner/roadmap", "/owner/inbox", "/owner/envelopes", "/owner/onboarding", "/owner/upgrade")


def _show(text: str) -> None:
    print(text, flush=True)


def _iso(delta_days: float) -> str:
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=delta_days))


def _card(gov: Path, project: str, state: str, task_id: str, *, created_days_ago: float, created_by: str, **extra) -> None:
    from tools.aipos_cli.record_writer import render_markdown
    from tools.aipos_cli.task_loader import queue_root_for

    meta = {"task_id": task_id, "title": f"{task_id} probe", "project": project, "status": state, "task_mode": extra.pop("task_mode", "code"),
            "created_by": created_by, "created_at": _iso(-created_days_ago), **extra}
    _write(queue_root_for(gov) / state / f"{task_id.lower()}.md", render_markdown(meta, f"# {task_id}\n"))


def _ok(rc_out) -> None:
    rc, out, err = rc_out
    assert rc == 0, out + err


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.project_upgrade import clear_badge_cache

    clear_badge_cache()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    for name, actor in ((M, ADV_A), (S, ADV_S), (T, ADV_S), (Q, ADV_S)):
        _ok(_cli("project", "new", name, "--home-root", str(hroot), "--actor", actor))
    gm, gs, gt, gq = (hroot / n for n in (M, S, T, Q))
    repos = {}
    for name in ("web-r", "app-r"):
        repos[name] = tmp_path / "repos" / name
        repos[name].mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repos[name]), check=True)
    pj = json.loads((gm / "project.json").read_text(encoding="utf-8"))
    pj.pop("code_repo", None)
    pj["repos"] = {"default": "web-r", "items": {k: str(v) for k, v in repos.items()}}
    (gm / "project.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2), encoding="utf-8")
    # manual-gate era cards (no gate records): frozen through the product command; one older unlinked card stays unfrozen
    for tid, state in (("L-1", "claimed"), ("L-2", "blocked"), ("L-3", "completed"), ("L-4", "claimed")):
        _card(gm, M, state, tid, created_days_ago=60, created_by=ADV_A)
    _card(gm, M, "completed", "O-1", created_days_ago=30, created_by=ADV_A)
    # published by an instance that drives no envelope of the project -> grouped by its lane, not as a subproject of its own
    _card(gm, M, "completed", "P-1", created_days_ago=20, created_by=f"planner.{M}.h1", lane={"repo": "app-r"})
    init_governance_repo(hroot)
    _ok(_cli("project", "freeze-legacy", "--workspace-root", str(gm), "--task-ids", "L-1", "L-2", "L-3", "L-4",
             "--reason", "manual-gate era backlog (rig)", "--actor", ADV_A, "--confirm"))
    for argv in (("add-milestone", "--id", "MA", "--lane", "web-r", "--title", "web ready", "--status", "active", "--target-date",
                  (utc_now() - timedelta(days=2)).date().isoformat()),
                 ("add-criterion", "--milestone", "MA", "--id", "CA1", "--statement", "web tests green", "--verify", "auto"),
                 ("add-milestone", "--id", "MB", "--lane", "app-r", "--title", "app ready", "--status", "active"),
                 ("add-criterion", "--milestone", "MB", "--id", "CB1", "--statement", "app tests green", "--verify", "auto"),
                 ("add-milestone", "--id", "MD", "--lane", "web-r", "--title", "skeleton", "--status", "done"),
                 ("add-criterion", "--milestone", "MD", "--id", "CD1", "--statement", "skeleton runs", "--verify", "auto")):
        _ok(_cli("roadmap", argv[0], "--workspace-root", str(gm), *argv[1:], "--confirm", "--actor", ADV_A))
    web, app = {"repo": "web-r", "paths": ["src/"], "roles": ["executor"]}, {"repo": "app-r", "paths": ["src/"], "roles": ["executor"]}
    _card(gm, M, "claimed", "A-1", created_days_ago=0, created_by=ADV_A, lane=web, milestone="MA", criteria=["CA1"])
    _card(gm, M, "pending", "A-2", created_days_ago=0, created_by=ADV_A, lane=web)
    _card(gm, M, "completed", "A-3", created_days_ago=0, created_by=ADV_A, lane=web, milestone="MD", criteria=["CD1"])
    _card(gm, M, "claimed", "B-1", created_days_ago=0, created_by=ADV_B, lane=app, milestone="MB", criteria=["CB1"])
    _card(gm, M, "blocked", "B-2", created_days_ago=0, created_by=ADV_B, lane=app, milestone="MB", criteria=["CB1"])
    _card(gm, M, "completed", "B-3", created_days_ago=0, created_by=ADV_B, lane=app)
    _card(gm, M, "completed", "B-1R", created_days_ago=0, created_by="gate_derivation", task_mode="audit", reviewed_task_id="B-1")
    # authorization request: G-1 is a docs card, ADV_B only has a code envelope -> loop stopped no_envelope (F180 request card)
    _card(gm, M, "claimed", "G-1", created_days_ago=0, created_by=ADV_B, task_mode="docs", lane=app)
    now = utc_now()
    _policy(gm, "pol_f181_b_code_1", who=ADV_B, max_tasks=20, task_mode="code", active_from=now - timedelta(days=1),
            expires_at=now + timedelta(days=6))
    _policy(gm, "pol_f181_a_code_1", who=ADV_A, max_tasks=20, task_mode="code", active_from=now - timedelta(days=1),
            expires_at=now + timedelta(days=6))
    _policy(gs, "pol_f181_s_code_1", who=ADV_S, max_tasks=20, task_mode="code", active_from=now - timedelta(days=1),
            expires_at=now + timedelta(days=6))
    _ended_run(gm, "G-1", ADV_B, reason="no_envelope", message="no envelope covers this card")
    for tid, state in (("S-1", "claimed"), ("S-2", "pending"), ("S-3", "completed")):
        _card(gs, S, state, tid, created_days_ago=1, created_by=ADV_S)
    _card(gt, T, "blocked", "T-1", created_days_ago=1, created_by=ADV_S, needs_owner=True, block_reason="probe needs the Owner")
    _card(gq, Q, "blocked", "Q-1", created_days_ago=1, created_by=ADV_S, needs_owner=True, block_reason="paused needs the Owner")
    _git(hroot, "add", "-A")
    _git(hroot, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fixture: cards")
    _ok(_cli("project", "set-meta", T, "--status", "test", "--home-root", str(hroot), "--confirm", "--actor", ADV_S))
    _ok(_cli("project", "set-meta", Q, "--status", "paused", "--home-root", str(hroot), "--confirm", "--actor", ADV_S))
    return {"home": hroot, "gm": gm, "gs": gs, "gt": gt, "gq": gq, "tmp": tmp_path}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def _get(b, path: str, *, follow: bool = True) -> tuple[int, str, dict]:
    req = urllib.request.Request(b["base"] + path, headers={"Cookie": b["cookie"]})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


def _page(b, path: str) -> str:
    status, page, _h = _get(b, path)
    assert status == 200, (path, page[:400])
    _snapshot(path, page)
    return page


def _snapshot(path: str, page: str) -> None:
    out = os.environ.get("F181_SNAPSHOT_DIR")
    if not out:
        return
    css = (REPO_ROOT / "web" / "board" / "static" / "readonly-pages.css").read_text(encoding="utf-8")
    name = re.sub(r"[^A-Za-z0-9]+", "-", path.strip("/")).strip("-") or "root"
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"{name}.html").write_text(page.replace('<link rel="stylesheet" href="/readonly-pages.css">', f"<style>{css}</style>"),
                                            encoding="utf-8")


def _visible(page: str) -> str:
    """Text a reader sees without opening anything: scripts, folded <details> blocks and tags removed."""
    text = re.sub(r"<script.*?</script>", " ", page, flags=re.S)
    while True:
        folded = re.sub(r"<details[^>]*>(?:(?!<details).)*?</details>", " ", text, flags=re.S)
        if folded == text:
            break
        text = folded
    import html as _html

    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


def _nav(page: str) -> str:
    return re.search(r'<header class="rp-nav">.*?</header>', page, re.S).group(0)


def _block(page: str, pattern: str) -> str:
    found = re.search(pattern, page, re.S)
    assert found, pattern
    return found.group(0)


# ===========================================================================
# item 1: one skeleton / nav everywhere, root redirect, no server paths in the page body
# ===========================================================================

def test_item1_one_skeleton_nav_and_root_redirect(board):
    navs = {}
    for path in PAGES:
        page = _page(board, path)
        assert '<link rel="stylesheet" href="/readonly-pages.css">' in page and '<body class="rp-page ' in page, path
        navs[path] = re.sub(r' aria-current="page"', "", _nav(page))
    _show("[item1 top nav (same on every page) raw] " + navs["/owner/overview"])
    assert len(set(navs.values())) == 1, navs
    for href in ("/owner/overview", "/owner/project", "/owner/roadmap", "/owner/inbox", "/owner/envelopes", "/owner/onboarding"):
        assert f'href="{href}"' in navs["/owner/overview"], href
    assert 'href="/"' not in navs["/owner/overview"]
    status, body, headers = _get(board, "/", follow=False)
    _show(f"[item1 GET / raw] {status} Location={headers.get('Location')} body={body!r}")
    assert status == 302 and headers.get("Location") == "/owner/overview"
    status, legacy, _h = _get(board, "/overview.html")
    assert status == 200 and 'id="workspace-grid"' in legacy  # former home still reachable (linked from the project page footer)
    upgrade = _page(board, "/owner/upgrade")
    assert 'href="/owner/upgrade" aria-current="page"' not in upgrade and "<style>" not in upgrade  # styles live in the one stylesheet


def test_item1_no_server_paths_or_cli_hints_in_page_body(board):
    home, tmp = str(board["home"]), str(board["tmp"])
    for path in PAGES + (f"/owner/project?project={M}", f"/owner/roadmap?project={M}", "/owner/inbox?show=all"):
        page = _page(board, path)
        seen = _visible(page)
        title = re.search(r"<title>(.*?)</title>", page).group(1) + " " + re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1)
        assert home not in seen and tmp not in seen, (path, seen[max(0, seen.find(tmp) - 200):seen.find(tmp) + 200])
        assert home not in title and tmp not in title and "home 根" not in title, (path, title)
        assert "--include-paused" not in page, path
        _show(f"[item1 {path}] visible chars {len(seen)}, server path in visible text: no; title: {title.strip()[:80]}")
    # the paths are still there for the advisor, folded
    roadmap = _page(board, f"/owner/roadmap?project={M}&milestone=MA")
    cmd = _block(roadmap, r'<details class="rp-cmd">.*?</details>')
    _show(f"[item1 folded advisor command raw] {cmd}")
    assert str(board["gm"]) in cmd and "<summary" in cmd


# ===========================================================================
# item 2 / 3: subproject grouping + counts (frozen legacy cards only as history) + test projects hidden
# ===========================================================================

def test_item2_item3_overview_counts_subprojects_and_test_hidden(board):
    page = _page(board, "/owner/overview")
    multi = _block(page, rf'<article class="rp-card" data-project="{M}".*?</article>')
    _show(f"[item2/3 overview card {M} raw] {multi}")
    counts = _block(multi, r'<li class="rp-inflight".*?</li>')
    assert 'data-inflight="4" data-todo="1" data-closed="5" data-frozen="4"' in counts
    assert "在途 4 张 · 待办 1 张 · 已结案 5 张" in counts and "进行中 3" in counts and "受阻 1" in counts
    assert 'class="rp-frozen" data-frozen="4">历史存量 4 张(已冻结, 不计入上面的数字)' in multi
    table = _block(multi, r'<table class="rp-subs".*?</table>')
    rows = dict(re.findall(r'<tr data-subproject="([^"]+)" (data-inflight="\d+" data-todo="\d+" data-closed="\d+")>', table))
    _show(f"[item2 subproject rows] {rows}")
    assert rows == {ADV_B: 'data-inflight="3" data-todo="0" data-closed="2"', ADV_A: 'data-inflight="1" data-todo="1" data-closed="2"',
                    "app-r": 'data-inflight="0" data-todo="0" data-closed="1"'}
    assert list(rows) == [ADV_B, ADV_A, "app-r"]  # most open work first; a lane group shows "(unknown)" when no envelope maps it
    assert f">{ADV_A}</td>" in table and f">{ADV_B}</td>" in table and ">advisor-a</a>" in table
    assert f'href="/owner/project?project={M}&amp;sub={ADV_A}"' in table
    single = _block(page, rf'<article class="rp-card" data-project="{S}".*?</article>')
    assert f"归属顾问:{ADV_S}" in single and "rp-subs" not in single and 'data-inflight="1" data-todo="1" data-closed="1"' in single
    assert "rp-frozen" not in single
    assert f'data-project="{T}"' not in page
    toggle = _block(page, r'<p class="rp-toggle".*?</p>')
    _show(f"[item3 test-project switch raw] {toggle}")
    assert 'data-hidden="1"' in toggle and "显示测试项目(1 个)" in toggle and 'href="/owner/overview?show=all"' in toggle
    paused = _block(page, r'<details class="rp-paused".*?</details>')
    assert f'data-project="{Q}"' in paused
    named = _page(board, f"/owner/project?project={T}")  # a hidden test project asked for by name is shown, marked as test
    assert f"项目 · {T}" in named and f'data-project="{T}" aria-current="page"' in named and "T-1" in named
    shown = _page(board, "/owner/overview?show=all")
    probe = _block(shown, rf'<article class="rp-card" data-project="{T}".*?</article>')
    _show(f"[item3 show=all probe card head raw] {probe[:400]}")
    assert 'data-hidden="true"' in probe and '<span class="rp-badge rp-badge-test">测试</span>' in probe and "隐藏测试项目" in shown


def test_item2_project_page_filter_open_lists_closed_folded(board):
    page = _page(board, f"/owner/project?project={M}")
    nav = _block(page, r'<nav class="rp-subnav">.*?</nav>')
    _show(f"[item2 project page subproject filter raw] {nav}")
    assert re.findall(r'data-subproject="([^"]*)"', nav) == ["", ADV_B, ADV_A, "app-r"] and 'data-subproject="" aria-current="page"' in nav
    a = _block(page, rf'<section class="rp-subproject" data-subproject="{re.escape(ADV_A)}".*?</section>')
    _show(f"[item2 project page section {ADV_A} raw] {a}")
    open_part = a.split("<details", 1)[0]
    assert 'data-task-id="A-1"' in open_part and 'data-task-id="A-2"' in open_part and "A-3" not in open_part
    closed = _block(a, r'<details class="rp-closed" data-closed="2">.*?</details>')
    assert 'data-task-id="A-3"' in closed and 'data-task-id="O-1"' in closed and " open" not in closed.split(">", 1)[0]
    assert "L-1" not in page and "L-3" not in page  # frozen legacy cards are not listed, only counted as history
    assert "历史存量 4 张" in page and 'href="/workspace/0"' in page
    only_b = _page(board, f"/owner/project?project={M}&sub={ADV_B}")
    assert f'data-subproject="{ADV_B}" data-inflight="3"' in only_b and f'<section class="rp-subproject" data-subproject="{ADV_A}"' not in only_b
    assert 'data-task-id="B-1R"' in only_b  # an audit card without its own owner joins the reviewed card's subproject
    unknown = _page(board, f"/owner/project?project={M}&sub=nobody")
    assert "子项目 nobody 不在本项目, 已显示全部" in unknown and f'data-subproject="{ADV_A}"' in unknown


def test_item2_item3_roadmap_filter_done_folded_and_unlinked_window(board):
    page = _page(board, f"/owner/roadmap?project={M}")
    left = _block(page, r'<aside class="rp-left">.*?</aside>')
    _show(f"[item2 roadmap left column raw] {left}")
    folded = _block(left, r'<details class="rp-ms-folded" data-folded="1">.*?</details>')
    assert 'data-milestone="MD"' in folded and 'data-milestone="MD"' not in left.replace(folded, "")
    assert 'data-milestone="MA"' in left and 'data-milestone="MB"' in left
    nxt = _block(page, r'<section class="rp-next">.*?</section>')
    _show(f"[item3 roadmap unlinked lines raw] {nxt}")
    unlinked = _block(nxt, r'<p class="roadmap-unlinked" data-unlinked="3">.*?</p>')
    assert all(t in unlinked for t in ("A-2", "B-3", "G-1")) and "O-1" not in unlinked and "L-1" not in unlinked
    assert 'data-before="6">另有建路线图之前的历史卡 6 张没挂里程碑, 不算游离卡' in nxt
    only_b = _page(board, f"/owner/roadmap?project={M}&sub={ADV_B}")
    left_b = _block(only_b, r'<aside class="rp-left">.*?</aside>')
    _show(f"[item2 roadmap filtered to {ADV_B} raw] {left_b}")
    assert 'data-milestone="MB"' in left_b and 'data-milestone="MA"' not in left_b and 'data-milestone="MD"' not in left_b
    assert f"&amp;sub={ADV_B}" in left_b and _block(only_b, r'<section class="rp-right" data-milestone="([^"]*)"').endswith('"MB"')


def test_item3_unlinked_window_starts_per_lane(rig):
    """Lane start = earliest add-milestone pointer of that lane's milestones; other lanes = the project's first one."""
    from tools.aipos_cli.decision_log_pointers import pointer_index
    from tools.aipos_cli.roadmap import load_roadmap, milestone_declared_at, roadmap_texts
    from tools.aipos_cli.roadmap_drift import _roadmap_start

    gov = rig["gm"]
    roadmap = load_roadmap(gov)
    entries = pointer_index(gov)["entries"]
    declared = milestone_declared_at(roadmap, entries)
    _show(f"[item3 milestone declared at (from decision_log pointers)] {declared}")
    assert set(declared["milestones"]) == {"MA", "MB", "MD"} and declared["first_pointer"]
    head = roadmap_texts()["decision_summary"].partition("{detail}")[0]
    synthetic = [{"records": [roadmap["rel"]], "decided_at": _iso(-10), "summary": head.format(subcommand="add-milestone", target="MA")},
                 {"records": [roadmap["rel"]], "decided_at": _iso(-2), "summary": head.format(subcommand="add-milestone", target="MB")},
                 {"records": [roadmap["rel"]], "decided_at": _iso(-1), "summary": head.format(subcommand="add-milestone", target="MD")}]
    started = _roadmap_start(gov, roadmap, synthetic)

    def card(repo: str, days: float) -> dict:
        return {"metadata": {"task_id": "X", "lane": {"repo": repo}, "created_at": _iso(-days)}}

    verdicts = {"web-r 5d ago (MA declared 10d ago)": started(card("web-r", 5)),
                "app-r 5d ago (MB declared 2d ago)": started(card("app-r", 5)),
                "app-r 1d ago": started(card("app-r", 1)), "web-r 11d ago": started(card("web-r", 11))}
    _show(f"[item3 per-lane window] {verdicts}")
    assert list(verdicts.values()) == [True, False, True, False]
    assert _roadmap_start(gov, roadmap, [])(card("web-r", 400)) is True  # no pointer at all = behaviour unchanged


def test_item3_status_test_declared_and_inbox_cli_hides(rig):
    from tools.aipos_cli.workspace_config import project_status, project_status_visibility

    assert project_status(rig["gt"]) == "test" and project_status(rig["gq"]) == "paused"
    assert project_status_visibility() == {"inbox_hidden": ("paused", "test"), "board_hidden": ("test",)}
    rc, out, _err = _cli("owner", "inbox", "--home-root", str(rig["home"]))
    line = next(ln for ln in out.splitlines() if "缺省不列" in ln)
    _show(f"[item3 CLI owner inbox hidden line raw] {line}")
    assert f"(已暂停 / 测试项目缺省不列: {T}, {Q}; 加 --include-paused 查看)" == line.strip() or \
        f"(已暂停 / 测试项目缺省不列: {Q}, {T}; 加 --include-paused 查看)" == line.strip()
    assert "T-1" not in out and "Q-1" not in out
    rc, out, _err = _cli("owner", "inbox", "--home-root", str(rig["home"]), "--include-paused")
    assert "T-1" in out and "Q-1" in out


# ===========================================================================
# item 4: inbox in plain words, details folded, request cards keep their item folded
# ===========================================================================

def test_item4_inbox_plain_titles_details_folded_and_switch(board):
    page = _page(board, "/owner/inbox")
    h1 = _block(page, r"<h1[^>]*>.*?</h1>")
    _show(f"[item4 inbox title raw] {h1}")
    assert re.search(r"待你决定 · \d+ 件\(2 个项目\)", h1) and str(board["home"]) not in h1
    grant = _block(page, r'<section class="owner-inbox-item[^"]*"[^>]*data-grant="true">.*?</section>')
    _show(f"[item4 item with a request card raw] {grant}")
    item_id = re.search(r'data-item-id="([^"]+)"', grant).group(1)
    assert f'<a href="#{item_id}">这件事上面已有「待授权请求」卡, 在那里按建议签发</a>' in grant
    assert f"<h2>{M} · 卡 G-1「G-1 probe」</h2>" in grant and "要你做什么" not in grant.split("<details", 1)[0]
    details = _block(grant, r'<details class="ib-details"><summary>查看详情</summary>.*?</details>')
    assert "lybra owner decide --item" in details and str(board["home"]) in details and " open" not in details.split(">", 1)[0]
    assert f'<article class="gr-card" id="{item_id}"' in page  # the request card itself is on top
    other = _block(page, r'<section class="owner-inbox-item[^"]*"[^>]*data-category="roadmap_drift"[^>]*data-grant="false">.*?</section>')
    _show(f"[item4 plain item raw] {other}")
    seen = _visible(other)
    assert seen.strip().startswith(f"{M} · ") and "要你做什么:" in seen and "lybra" not in seen and "OI-" not in seen
    switch = _block(page, r'<p class="rp-toggle".*?</p>')
    note = _block(page, r'<p class="owner-inbox-paused rp-sub">.*?</p>')
    _show(f"[item4 switch raw] {note} {switch}")
    assert "显示已暂停 / 测试项目(2 个)" in switch and 'href="/owner/inbox?show=all"' in switch and T in note and Q in note
    assert "T-1" not in page and "Q-1" not in page
    everything = _page(board, "/owner/inbox?show=all")
    assert "T-1" in everything and "Q-1" in everything and "隐藏已暂停 / 测试项目" in everything


# ===========================================================================
# item 5: upgrade badge cached per fingerprint; hot overview timing
# ===========================================================================

def test_item5_badge_cache_fingerprint_and_hot_overview(board, monkeypatch):
    from tools.aipos_cli import project_upgrade

    calls = {"plan": 0, "git": 0}
    real_plan, real_run = project_upgrade.plan_upgrade, subprocess.run

    def spy_plan(*args, **kwargs):
        calls["plan"] += 1
        return real_plan(*args, **kwargs)

    def spy_run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and argv and str(argv[0]) == "git":
            calls["git"] += 1
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(project_upgrade, "plan_upgrade", spy_plan)
    monkeypatch.setattr(subprocess, "run", spy_run)
    project_upgrade.clear_badge_cache()
    t0 = time.perf_counter()
    cold = _page(board, "/owner/overview")
    cold_s = time.perf_counter() - t0
    cold_calls = dict(calls)
    badges = re.findall(r'<li class="rp-upgrade[^"]*" data-upgrade-pending="(\d*)">', cold)
    hot_times = []
    for _ in range(3):
        calls.update(plan=0, git=0)
        t0 = time.perf_counter()
        hot = _page(board, "/owner/overview")
        hot_times.append(time.perf_counter() - t0)
        assert calls == {"plan": 0, "git": 0}, calls
        assert re.findall(r'<li class="rp-upgrade[^"]*" data-upgrade-pending="(\d*)">', hot) == badges
    _show(f"[item5 overview timing] cold {cold_s:.3f}s (plan_upgrade {cold_calls['plan']} / git subprocesses {cold_calls['git']}); "
          f"hot {', '.join(f'{t:.3f}s' for t in hot_times)} (plan_upgrade 0 / git subprocesses 0); badges {badges}")
    assert cold_calls["plan"] == 3 and cold_calls["git"] > 0
    assert max(hot_times) < 1.5  # product target 0.5s (measured above / in the RETURN); slack for a loaded sandbox
    # governance truth changed (project.json rewritten) -> that project's badge recomputes, the others stay cached
    pj = board["gs"] / "project.json"
    pj.write_text(pj.read_text(encoding="utf-8"), encoding="utf-8")
    calls.update(plan=0, git=0)
    _page(board, "/owner/overview")
    assert calls["plan"] == 1, calls
    # a landing (git commit in the governance repo) changes every fingerprint of that repository
    (board["home"] / "touch.md").write_text("x\n", encoding="utf-8")
    _git(board["home"], "add", "-A")
    _git(board["home"], "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "touch")
    calls.update(plan=0, git=0)
    _page(board, "/owner/overview")
    _show(f"[item5 after a commit] plan_upgrade calls {calls['plan']}")
    assert calls["plan"] == 3


# ===========================================================================
# declarations / i18n / single implementations
# ===========================================================================

def test_declarations_i18n_and_single_implementations():
    from tools.aipos_cli.board_pages import PAGES as DECLARED, pages_declaration
    from tools.aipos_cli.machine_zone import card_state_groups, card_stats_declaration
    from tools.aipos_cli.task_loader import QUEUE_STATES
    from web.board.app import _I18N_TEMPLATES

    decl = pages_declaration()
    assert decl["pages"]["project"] == "/owner/project" and decl["root_redirect"] == "overview" and DECLARED[1] == "project"
    assert "board_home" not in decl["nav_links"] and decl["query"]["subproject"] == "sub" and decl["show_all_value"] == "all"
    assert card_stats_declaration()["todo_states"] == ["pending"]
    groups = card_state_groups()
    _show(f"[decl card groups] {groups}")
    assert groups == {"inflight": ("claimed", "blocked"), "todo": ("pending",), "closed": ("completed",)}
    zh, en = _I18N_TEMPLATES["zh"], _I18N_TEMPLATES["en"]
    assert set(zh) == set(en)
    src = (REPO_ROOT / "web" / "board" / "readonly_pages.py").read_text(encoding="utf-8")
    used = set(re.findall(r'"((?:rp|ib)_[a-z_]+)"', src)) | set(re.findall(r"'((?:rp|ib)_[a-z_]+)'", src))
    partial = {"rp_rm_status_", "rp_rm_verify_", "rp_sev_", "rp_ob_state_", "rp_ob_item_", "rp_ob_ask_", "rp_rm_state_", "rp_qs_"}
    missing = sorted(k for k in used - partial if k not in zh)
    assert not missing, missing
    assert all(f"rp_qs_{s}" in zh for s in QUEUE_STATES)
    import importlib.util

    spec = importlib.util.spec_from_file_location("f181_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    hits = guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / "readonly_pages.py")
    _show(f"[CJK guard line check readonly_pages.py] {hits}")
    assert hits == []

    def code(path: str) -> str:
        return (REPO_ROOT / path).read_text(encoding="utf-8")

    hits = {p: code(p).count("def card_stats(") for p in ("tools/aipos_cli/machine_zone.py", "tools/aipos_cli/board_pages.py",
                                                          "web/board/readonly_pages.py", "tools/aipos_cli/owner_inbox.py")}
    assert hits == {"tools/aipos_cli/machine_zone.py": 1, "tools/aipos_cli/board_pages.py": 0, "web/board/readonly_pages.py": 0,
                    "tools/aipos_cli/owner_inbox.py": 0}
    pages = code("tools/aipos_cli/board_pages.py").split('"""', 2)[2]  # code after the module docstring
    assert "card_stats(" in pages and "iter_queue_task_paths(" not in pages and "frozen_tasks(" not in pages
    assert "frozen_tasks(" not in code("tools/aipos_cli/machine_zone.py").split("def card_stats_declaration", 1)[1]  # via visible_cards
    assert code("tools/aipos_cli/owner_inbox.py").count("project_status_visibility()") == 1
    assert "def inbox_sections_html" not in code("tools/aipos_cli/owner_inbox.py")  # board renders the inbox page itself (F181)
    assert code("tools/aipos_cli/project_upgrade.py").count("_BADGE_CACHE: ") == 1
    for path in ("web/board/readonly_pages.py", "tools/aipos_cli/board_pages.py", "tools/aipos_cli/machine_zone.py"):
        text = code(path)
        for literal in ("chris", "kiwiai", "lybra-probe", "/home/", "2_projects"):
            assert literal not in text.split('"""', 2)[2], (path, literal)
    css = code("web/board/static/readonly-pages.css")
    assert css.count(":root {") == 1  # design tokens declared once
