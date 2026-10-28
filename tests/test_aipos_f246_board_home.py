"""AIPOS-F246 — board home (one sheet per advisor) / project brief / advisor headline / stall judgment / nav / envelope display.

Rig = temporary HOME + temporary home root (git repo + temporary bare remote) with two projects built through product commands
(`lybra project new`): a customer-shaped project with two enrolled advisors (one with cards, a roadmap and deliverables, one with no
card at all, like a chief advisor) and a single-advisor project whose cards are grouped by lane. Advisors are enrolled the way the
gate lands them (enrollment_log land events + a governance-root connection file with fixture token values that are not secrets).
Project / instance / card names are rig values (the product hard-codes none). The board runs in a thread of the test process and is
shut down afterwards. No real credential, no real governance root, no real gate.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  - single source of rig helpers
from test_aipos_f171_roadmap_board_readonly import _cli, _git  # noqa: E402
from test_aipos_f180_grant_request import _policy  # noqa: E402
from test_aipos_f181_board_usability import _free_port, _get  # noqa: E402

C, S = "f246-cust", "f246-solo"  # rig project names
ADV_X, ADV_Y, ADV_S = f"advisor-x.{C}.h1", f"advisor.{C}.h1", f"advisor.{S}.h1"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def visible_text(page_html: str, *, include_authored: bool = True) -> str:
    """Page text a reader sees (no <script> / <style> / tags / attributes); include_authored=False also drops the advisor /
    card-author text marked data-authored (shown as written; the guard covers the interface text)."""
    import html as _html

    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page_html)
    if not include_authored:
        text = re.sub(r"(?is)<span\b[^>]*\bdata-authored\b[^>]*>.*?</span>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _html.unescape(text)


def forbidden_hits(page_html: str, words: list[str], *, extra: list[str] | None = None, include_authored: bool = True) -> list[str]:
    """Home forbidden-word guard: declared board_readonly_pages.home.forbidden_words + extra (e.g. every instance full name)."""
    text = visible_text(page_html, include_authored=include_authored)
    return [w for w in list(words) + list(extra or []) if w and w in text]


def _show(text: str) -> None:
    print(text, flush=True)


def _iso(delta_days: float) -> str:
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=delta_days))


def _ok(rc_out) -> None:
    rc, out, err = rc_out
    assert rc == 0, out + err


def _card(gov: Path, project: str, state: str, task_id: str, *, title: str, created_by: str, **extra) -> Path:
    from tools.aipos_cli.record_writer import render_markdown
    from tools.aipos_cli.task_loader import queue_root_for

    meta = {"task_id": task_id, "title": title, "project": project, "status": state, "task_mode": extra.pop("task_mode", "code"),
            "created_by": created_by, "created_at": _iso(-1), **extra}
    path = queue_root_for(gov) / state / f"{task_id.lower()}.md"
    _write(path, render_markdown(meta, f"# {task_id}\n"))
    return path


def _age(path: Path, days: float) -> None:
    moment = time.time() - days * 86400
    os.utime(path, (moment, moment))


def _record(gov: Path, kind: str, task_id: str, name: str, *, days: float) -> Path:
    from tools.aipos_cli.record_writer import record_dir

    path = record_dir(gov, kind, task_id) / name
    _write(path, "---\nrecord_type: fixture\n---\n")
    _age(path, days)
    return path


def _enroll(gov: Path, advisors: list[str], *, owner: bool) -> None:
    from tools.aipos_cli.enroll_client import write_role_file
    from tools.aipos_cli.enrollment import _append_enrollment_trail

    tokens = [{"role": "advisor", "role_class": "advisor", "agent_instance": a, "token": f"fixture-f246-{i}-not-a-secret",
               "token_ref": f"fixture-{i}", "scopes": []} for i, a in enumerate(advisors)]
    if owner:
        tokens.append({"role": "owner", "token": "fixture-f246-owner-not-a-secret", "token_ref": "fixture-owner", "scopes": ["owner_confirm"]})
    _write(gov / ".lybra" / "connection.json", json.dumps({"config_version": 1, "governance_root": str(gov),
                                                           "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": tokens}, indent=2))
    for inst in advisors:
        write_role_file(gov / ".lybra", "advisor", inst, None, slot_by_instance=True)
        _append_enrollment_trail(gov, action="land", code_id=f"enroll_f246_{inst}", role="advisor", instance=inst, by="(agent-enroll)",
                                 reason=f"host={socket.gethostname()} workstation={gov} files=['connection.json', 'role']")


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
    for name, actor in ((C, ADV_X), (S, ADV_S)):
        _ok(_cli("project", "new", name, "--home-root", str(hroot), "--actor", actor))
    gc, gs = hroot / C, hroot / S
    repo = tmp_path / "repos" / "app-r"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    pj = json.loads((gc / "project.json").read_text(encoding="utf-8"))
    pj.pop("code_repo", None)
    pj["repos"] = {"default": "app-r", "items": {"app-r": str(repo)}}
    (gc / "project.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2), encoding="utf-8")
    _enroll(gc, [ADV_X, ADV_Y], owner=True)
    _enroll(gs, [ADV_S], owner=False)
    now = utc_now()
    for gov, who in ((gc, ADV_X), (gc, ADV_Y), (gs, ADV_S)):
        _policy(gov, f"pol_f246_{who.split('.')[0].replace('-', '_')}_{gov.name.replace('-', '_')}", who=who, max_tasks=20, task_mode="code",
                active_from=now - timedelta(days=1), expires_at=now + timedelta(days=6))
    init_governance_repo(hroot)
    for argv in (("add-milestone", "--id", "MX", "--lane", "app-r",
                  "--title", "Android entry wired", "--status", "active", "--target-date", (now - timedelta(days=2)).date().isoformat()),
                 ("add-criterion", "--milestone", "MX", "--id", "CX1", "--statement", "entry screen shows", "--verify", "auto"),
                 ("add-criterion", "--milestone", "MX", "--id", "CX2", "--statement", "upgrade result shows", "--verify", "auto")):
        _ok(_cli("roadmap", argv[0], "--workspace-root", str(gc), *argv[1:], "--confirm", "--actor", ADV_X))
    cards = {
        "fresh": _card(gc, C, "claimed", "X-1", title="X-1 Wire the maintenance page by lock state", created_by=ADV_X,
                       milestone="MX", criteria=["CX1"]),
        "stale": _card(gc, C, "claimed", "X-2", title="X-2: Two phones HTTPS login check", created_by=ADV_X),
        "older": _card(gc, C, "claimed", "X-3", title="X-3 Old validation superseded later", created_by=ADV_X),
        "todo": _card(gc, C, "pending", "X-4", title="X-4 Weekly candidate check", created_by=ADV_X),
        "done": _card(gc, C, "completed", "X-5", title="X-5 Signed package built", created_by=ADV_X,
                      artifacts=[{"criterion": "CX2", "path_glob": "shot.png", "kind": "screenshot"}], milestone="MX", criteria=["CX2"]),
        "solo": _card(gs, S, "claimed", "S-1", title="S-1 Solo lane card", created_by=f"planner.{S}.h1"),
    }
    _age(cards["stale"], 5)
    _record(gc, "claims", "X-2", "claim_X-2_old.md", days=5)
    _age(cards["older"], 9)
    _record(gc, "claims", "X-3", "claim_X-3_old.md", days=9)
    _record(gc, "closures", "X-5", "close_X-5_now.md", days=0.5)
    from tools.aipos_cli.roadmap import card_artifact_dir

    _write(card_artifact_dir(gc, "X-5") / "shot.png", "")
    (card_artifact_dir(gc, "X-5") / "shot.png").write_bytes(PNG)
    _git(hroot, "add", "-A")
    _git(hroot, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fixture: cards")
    _git(hroot, "push", "-q", "origin", "main")
    return {"home": hroot, "gc": gc, "gs": gs, "tmp": tmp_path, "cards": cards}


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


def _headline(gov: Path, instance: str, *, actor: str | None = None, confirm: bool = True, **fields) -> tuple[int, str, str]:
    argv = ["project", "headline", "--workspace-root", str(gov), "--instance", instance, "--title", fields.get("title", "Cust"),
            "--now", fields.get("now", "Wiring the maintenance page."), "--done", fields.get("done", "Signed package out.")]
    for key in ("stage", "status"):
        if fields.get(key):
            argv += [f"--{key}", fields[key]]
    if actor:
        argv += ["--actor", actor]
    argv.append("--confirm" if confirm else "--dry-run")
    return _cli(*argv)


def _headline_files(gov: Path) -> list[str]:
    base = gov / "5_tasks" / "records" / "advisor_headlines"
    return sorted(p.relative_to(gov).as_posix() for p in base.rglob("*.md")) if base.is_dir() else []


# ===========================================================================
# item 1: advisor headline command (dry-run / confirm / others refused / Owner / limits) + stale
# ===========================================================================

def test_item1_headline_dry_run_confirm_refusals_owner_and_stale(rig):
    from tools.aipos_cli.advisor_headline import headline_declaration, headline_hint_lines, is_stale, latest_headlines, write_headline
    from tools.aipos_cli.clock import utc_now

    gc = rig["gc"]
    rc, out, err = _headline(gc, ADV_X, confirm=False, stage="delivery")
    _show(f"[item1 dry-run rc={rc}]\n{out}{err}")
    assert rc == 0 and "预演(零写入)" in out and _headline_files(gc) == []
    rc, out, err = _headline(gc, ADV_X, stage="delivery", title="Cust")
    _show(f"[item1 confirm rc={rc}]\n{out}{err}")
    assert rc == 0 and "已写简报" in out and "落账: verdict=PASS" in out
    files = _headline_files(gc)
    assert len(files) == 1 and files[0].startswith(f"5_tasks/records/advisor_headlines/{ADV_X}/headline_{ADV_X}_")
    text = (gc / files[0]).read_text(encoding="utf-8")
    assert "record_type: advisor_headline" in text and f"written_by: {ADV_X}" in text and "fixture-f246" not in text
    assert _git(rig["home"], "status", "--porcelain", "--", f"{C}/5_tasks/records/advisor_headlines") == ""  # landed
    rc, out, err = _headline(gc, ADV_X, actor=ADV_Y)
    _show(f"[item1 other advisor writes X's headline rc={rc}] {err.strip()}")
    assert rc == 1 and "拒(not_self)" in err and len(_headline_files(gc)) == 1
    rc, out, err = _headline(gc, f"advisor-z.{C}.h1")
    _show(f"[item1 not an advisor rc={rc}] {err.strip()}")
    assert rc == 1 and "拒(not_advisor)" in err
    rc, out, err = _headline(rig["gs"], ADV_S, actor="owner")
    _show(f"[item1 Owner writes where no Owner credential rc={rc}] {err.strip()}")
    assert rc == 1 and "拒(no_credential)" in err and "not-a-secret" not in err
    rc, out, err = _headline(gc, ADV_Y, actor="owner", title="Chief", now="Waiting for the customer scope.", done="Takeover review done.")
    _show(f"[item1 Owner writes Y's headline rc={rc}] {out.strip()}")
    assert rc == 0 and "写入方 owner" in out
    limit = headline_declaration()["limits"]["now"]
    rc, out, err = _headline(gc, ADV_X, now="x" * (limit + 1))
    assert rc == 1 and "拒(too_long)" in err
    # stale: a headline written stale_hours ago -> stale; the advisor's loop-status / next hint appears once
    hours = float(headline_declaration()["stale_hours"])
    write_headline(rig["gs"], instance=ADV_S, title="Solo", now_text="Old news.", done="Old done.", confirm=True,
                   moment=utc_now() - timedelta(hours=hours + 1))
    latest = latest_headlines(rig["gs"])[ADV_S]
    hint = headline_hint_lines(rig["gs"])
    _show(f"[item1 stale headline] {latest} | hint={hint}")
    assert is_stale(latest) and len(hint) == 1 and "小时未更新" in hint[0] and f"--instance {ADV_S}" in hint[0]
    assert headline_hint_lines(gc, ADV_X) == []  # fresh = no hint
    assert set(latest_headlines(gc)) == {ADV_X, ADV_Y}


# ===========================================================================
# item 3: stall judgment (inside / outside the threshold, no record at all)
# ===========================================================================

def test_item3_stall_judgment_threshold_and_no_record(rig):
    from tools.aipos_cli.board_home import card_last_activity, home_declaration, stall_rows
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.machine_zone import card_stats

    gc = rig["gc"]
    spec = home_declaration()["stall"]
    assert spec["threshold_hours"] == 48
    rows = [r for r in card_stats(gc)["rows"] if r["group"] == "inflight"]
    judged = {r["task_id"]: r for r in stall_rows(gc, rows, now=utc_now())}
    _show("[item3 stall rows] " + json.dumps({k: {x: v[x] for x in ("last_activity", "activity_source", "idle_hours", "stalled")}
                                               for k, v in judged.items()}, ensure_ascii=False))
    assert judged["X-1"]["stalled"] is False and judged["X-1"]["idle_hours"] < 1
    assert judged["X-2"]["stalled"] is True and 4.9 * 24 < judged["X-2"]["idle_hours"] < 5.1 * 24
    assert judged["X-3"]["stalled"] is True and judged["X-3"]["idle_hours"] > 8.9 * 24
    # a fresh loop run record / claim record = activity: X-2 comes back inside the threshold
    _record(gc, "claims", "X-2", "claim_X-2_new.md", days=1)
    again = {r["task_id"]: r for r in stall_rows(gc, rows, now=utc_now())}
    assert again["X-2"]["stalled"] is False and again["X-2"]["activity_source"] == "claims"
    # no record at all (card file not found either) = stalled, idle unknown
    none = stall_rows(gc, [{"task_id": "NOPE-1", "card_id": "NOPE-1", "title": "missing"}], now=utc_now())[0]
    assert card_last_activity(gc, "NOPE-1") == {"at": None, "source": None} and none["stalled"] is True and none["idle_hours"] is None
    # read-only: the judgment changes no card state
    assert "status: claimed" in rig["cards"]["stale"].read_text(encoding="utf-8")


# ===========================================================================
# item 2: home page (one card per advisor, needs-you, forbidden words) + item 6 budget
# ===========================================================================

def test_item2_home_one_card_per_advisor_and_forbidden_words(board):
    from tools.aipos_cli.board_home import home_declaration

    gc = board["gc"]
    _ok(_headline(gc, ADV_X, title="Cust", stage="delivery", now="Wiring the maintenance page by lock state.",
                  done="Signed package out."))
    page = _page(board, "/owner/home")
    cards = re.findall(r'<a class="hm-proj".*?</a>', page, re.S)
    _show("[item2 home cards raw]\n" + "\n".join(cards))
    assert len(cards) == 3  # two advisors of the customer project (the one with no card too) + the solo project
    x = next(c for c in cards if "Cust" in c)
    assert "验收标准 0/2" in x and "Wiring the maintenance page by lock state." in x and "Signed package out." in x
    assert 'data-stalled="2"' in x and "2 件停滞" in x and "Old validation superseded later" in x and "9 天无进展" in x
    assert 'data-pushing="2"' in x and "X-3" not in x and "刚刚更新" in x
    y = next(c for c in cards if 'data-headline="false"' in c and C in c)
    assert "顾问还没写简报" in y and "近 7 天结案 0 件" in y and "卡住</b><span>无" in y
    solo = next(c for c in cards if S in c)
    assert 'data-pushing="1"' in solo  # a lane-keyed card of a single-advisor project belongs to that advisor
    words = home_declaration()["forbidden_words"]
    instances = [ADV_X, ADV_Y, ADV_S]
    hits = forbidden_hits(page, words, extra=instances)
    _show(f"[item2 forbidden words on the fixture home] {hits}")
    assert hits == []
    assert 'href="/owner/home" data-nav="home" aria-current="page"' in page


def test_item2_needs_you_top_three_plain_titles(board, monkeypatch):
    from tools.aipos_cli import owner_inbox

    def fake(home_root=None, **_kw):
        items = []
        for i, (rank, since) in enumerate(((3, "2026-10-01T00:00:00Z"), (1, "2026-10-20T00:00:00Z"), (2, "2026-10-02T00:00:00Z"),
                                           (2, "2026-10-01T00:00:00Z"), (1, "2026-10-21T00:00:00Z"))):
            items.append({"item_id": f"OI-{i}", "project": C, "category": "owner_check" if rank == 2 else "loop_owner_needed",
                          "rank": rank, "rank_label": str(rank), "since": since, "task_id": "X-1", "title": None, "source": ADV_X,
                          "facts": {"milestone": "MX", "criterion": "CX1"}, "subject": "标准 CX1 待 Owner 抽查"})
        return {"items": items, "projects": [C, S]}

    monkeypatch.setattr(owner_inbox, "collect_inbox", fake)
    page = _page(board, "/owner/home")
    asks = re.search(r'<section class="hm-asks".*?</section>', page, re.S).group(0)
    _show(f"[item2 needs-you raw] {asks}")
    assert 'data-asks="3" data-asks-total="5"' in asks and "还有 2 件 →" in asks
    order = re.findall(r'href="/owner/inbox#item-(OI-\d)"', asks)
    assert order == ["OI-1", "OI-4", "OI-3"]  # rank first, then the longest waiting
    assert "Android entry wired" in asks and "entry screen shows" in asks and "Wire the maintenance page by lock state" in asks
    assert "CX1" not in re.sub(r"<[^>]+>", " ", asks) and "X-1" not in re.sub(r"<[^>]+>", " ", asks)


# ===========================================================================
# item 4: project brief per advisor (+ budget), all-cards view kept, engineering details folded
# ===========================================================================

def test_item4_project_brief_sections_and_budget(board):
    from tools.aipos_cli.board_home import home_declaration

    page = _page(board, f"/owner/project?project={C}&sub={ADV_X}")
    _show(f"[item4 brief size] {len(page.encode())} bytes")
    assert len(page.encode()) <= home_declaration()["page_budget_bytes"]
    sec = {m.group(1): m.group(0) for m in re.finditer(r'<section class="hm-sec" data-section="(\w+)".*?</section>', page, re.S)}
    _show("[item4 sections raw]\n" + "\n".join(f"{k}: {v}" for k, v in sec.items()))
    assert 'data-task-id="X-1"' in sec["pushing"] and 'data-task-id="X-4"' in sec["pushing"] and "X-2" not in sec["pushing"]
    assert 'data-task-id="X-2"' in sec["stalled"] and "5 天无进展" in sec["stalled"]
    assert 'data-milestone="MX" data-met="0" data-total="2"' in sec["milestones"] and "已逾期" in sec["milestones"]
    assert 'data-task-id="X-5"' in sec["deliverables"] and '/owner/artifact?project=' in sec["deliverables"] and "shot.png" in sec["deliverables"]
    assert "pb_" not in page and "工程详情" in page
    eng = re.search(r'<details class="hm-eng">.*?</details>', page, re.S).group(0)
    assert f"/owner/project?project={C}&amp;view=cards" in eng and "lybra queue withdraw" in eng and " open" not in eng.split(">", 1)[0]
    # thumbnail served through the existing artifact route
    thumb = re.search(r'src="(/owner/artifact[^"]+)"', page).group(1).replace("&amp;", "&")
    import urllib.request

    with urllib.request.urlopen(urllib.request.Request(board["base"] + thumb, headers={"Cookie": board["cookie"]}), timeout=60) as resp:
        assert resp.status == 200 and resp.headers.get("Content-Type") == "image/png" and resp.read() == PNG
    cards_view = _page(board, f"/owner/project?project={C}&view=cards")
    assert 'data-nav="history" aria-current="page"' in cards_view and 'class="rp-subproject"' in cards_view
    chief = _page(board, f"/owner/project?project={C}&sub={ADV_Y}")
    assert "没有在推的事" in chief and "没有归这位顾问的里程碑" in chief


def test_item5_nav_engineering_page_and_root(board):
    status, _body, headers = _get(board, "/", follow=False)
    assert status == 302 and headers.get("Location") == "/owner/home"
    page = _page(board, "/owner/engineering")
    links = re.findall(r'<li data-link="(\w+)"><a href="([^"]+)"', page)
    _show(f"[item5 engineering links] {links}")
    assert [k for k, _u in links] == ["inbox", "overview", "roadmap", "envelopes", "onboarding", "workstation", "upgrade", "cards"]
    for _k, url in links:  # every collected page still answers (none removed)
        assert _get(board, url.replace("&amp;", "&"))[0] == 200, url
    nav = re.search(r'<header class="rp-nav">.*?</header>', _page(board, "/owner/inbox"), re.S).group(0)
    assert [m for m in re.findall(r'data-nav="(\w+)"', nav)] == ["home", "project", "history", "engineering"]
    assert 'data-nav="engineering" aria-current="page"' in nav


# ===========================================================================
# item 5: envelope display — an envelope covered by a newer project authorization in use is no longer "in use"
# ===========================================================================

def test_item5_envelope_covered_by_newer_authorization_is_superseded(rig):
    from tools.aipos_cli.autonomy_policy import (
        AUTHORIZATION_FRONTMATTER_ORDER,
        authorization_revision_relpath,
        envelope_overview,
        project_authorization_declaration,
    )
    from tools.aipos_cli.clock import iso_z, utc_now
    from tools.aipos_cli.record_writer import render_markdown

    gc = rig["gc"]
    before = {r["policy_id"]: (r["current"], r["superseded_by"]) for r in envelope_overview(gc)["envelopes"]}
    repo = "app-r"
    now = utc_now()
    record = {"record_type": project_authorization_declaration()["record_type"], "authorization_id": f"auth_{C}", "revision": 1,
              "project": C, "status": "active", "approved_by_owner": True, "owner_approval_ref": "fixture-f246", "action": "grant",
              "revised_at": iso_z(now), "active_from": iso_z(now - timedelta(hours=1)), "expires_at": iso_z(now + timedelta(days=30)),
              "max_tasks": 50, "lanes": [{"repo": repo, "task_modes": ["code", "docs"], "drivers": [ADV_X]}], "hosts": ["h1"],
              "launch_harnesses": [], "zones_open": [], "supersedes_policies": [], "previous_revision": None, "notes": ""}
    meta = {k: record[k] for k in AUTHORIZATION_FRONTMATTER_ORDER}
    _write(gc / authorization_revision_relpath(gc, f"auth_{C}", 1), render_markdown(meta, "# fixture grant\n", AUTHORIZATION_FRONTMATTER_ORDER))
    after = {r["policy_id"]: (r["current"], r["superseded_by"], r["status"]) for r in envelope_overview(gc)["envelopes"]}
    _show(f"[item5 envelopes before] {before}\n[item5 envelopes after authorization] {after}")
    x_pid = next(p for p in after if "advisor_x" in p)
    y_pid = next(p for p in after if p != x_pid)
    assert before[x_pid] == (True, None)
    assert after[x_pid] == (False, f"auth_{C}", "superseded")  # covered (driver + mode + lane) by the newer grant in use
    assert after[y_pid][0] is True and after[y_pid][1] is None  # Y is not a driver of the grant: still in use
    from tools.aipos_cli.board_pages import collect_overview

    card = next(c for c in collect_overview(rig["home"])["projects"] if c["project"] == C)
    _show(f"[item5 engineering overview envelopes in use] {card['envelopes']}")
    assert card["envelopes"]["active"][0] == f"auth_{C}" and x_pid not in card["envelopes"]["active"] and y_pid in card["envelopes"]["active"]


# ===========================================================================
# item 6: home hot ≤ 0.5 s through HTTP with the truth cache; a truth change shows on the next request
# ===========================================================================

def test_item6_home_hot_and_fresh_with_truth_cache(board):
    from tools.aipos_cli import task_loader

    task_loader.enable_truth_cache()
    try:
        first = _page(board, "/owner/home")
        timings = []
        for _ in range(7):
            started = time.perf_counter()
            assert _page(board, "/owner/home") == first
            timings.append(time.perf_counter() - started)
        median = sorted(timings)[len(timings) // 2]
        _show(f"[item6 hot home timings] {[round(t, 4) for t in timings]} median={median:.4f}s")
        assert median <= 0.5
        _ok(_headline(board["gc"], ADV_X, title="Cust", now="Changed just now.", done="Something new."))
        after = _page(board, "/owner/home")
        assert after != first and "Changed just now." in after
    finally:
        task_loader.enable_truth_cache(False)


def test_declarations_single_sources():
    from tools.aipos_cli.advisor_headline import headline_declaration
    from tools.aipos_cli.board_home import ENGINEERING_LINKS, NAV_ITEMS, home_declaration
    from tools.aipos_cli.board_pages import pages_declaration
    from tools.aipos_cli.governance_commit import post_write_landing_declaration
    from tools.aipos_cli.record_writer import record_location
    from tools.schema_loader import get_enum_values, load_schema
    from web.board.app import _I18N_TEMPLATES

    decl = pages_declaration()
    home = home_declaration(decl)
    assert decl["root_redirect"] == "home" and tuple(home["nav"]) == NAV_ITEMS and tuple(home["engineering_links"]) == ENGINEERING_LINKS
    hd = headline_declaration()
    assert hd["record_type"] in get_enum_values("record_type") and hd["landing_command"] in post_write_landing_declaration()["commands"]
    assert record_location(hd["record_kind"]).startswith("5_tasks/records/advisor_headlines/{agent_instance}/")
    assert "project headline" in load_schema("verbs")["governance_root_resolution"]["commands"]
    zh, en = _I18N_TEMPLATES["zh"], _I18N_TEMPLATES["en"]
    for cat in home["ask_titles"]["categories"] + ["other"]:
        assert f"hm_ask_{cat}" in zh and f"hm_ask_{cat}" in en
    used = set()
    for rel in ("web/board/readonly_pages.py", "tools/aipos_cli/board_home.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        used |= set(re.findall(r"""['"]((?:hm|pb|eng)_[a-z_]+)['"]""", src))
    missing = sorted(k for k in used if not k.endswith("_") and k not in zh)
    assert not missing, missing
    assert set(zh) == set(en)
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    assert "lybra project headline" in skill and "活跃期至少每天一次" in skill
