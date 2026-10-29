"""AIPOS-F245 - board first page fast: overview / inbox / roadmap / onboarding results cached on the truth fingerprint, hot-spot
de-duplication, read paths quiet about envelope traces, cache counters in the perf log.

Fixture range = temporary roots only (pytest tmp_path, the F174 rig: three probe projects with roadmap, cards, verdicts, envelopes,
enrollment, loop run record). No real governance root is read or written; the real-root timings (HTTP, temporary port instance,
before / after), the byte-for-byte page comparison on the real root and both baselines are attached to the card RETURN.
The F177 racy window (frontmatter._FILE_CACHE_RACY_WINDOW_NS) is set to 0 where a test needs fixture files written a moment ago to
be cacheable (production keeps 2 s: truth younger than that is never stored); one test keeps it and checks exactly that.

Items covered:
  1. result cache (task_loader.truth_cached): a hit returns the same data as computing again; each truth change (new card, queue state
     move, ruling appended through `owner decide`, envelope changed, record edited in place, roadmap changed) is seen by the very next
     request; the minute rolls = served (AIPOS-F249: time judgments report their flips, tests/test_aipos_f249_*); young truth = stored
     with a content fingerprint (AIPOS-F249); an undeclared subprocess = not stored; a declared external read is stored;
     /proc liveness is fingerprinted by content (a process that died is seen next request although its /proc file kept its identity);
     the inbox cached inside the overview is a nested dependency of the overview (AIPOS-F249: by value digest); off unless the board
     server switches it on; the board
     HTTP pages reflect a change on the next GET;
  2. de-duplication: the inbox declaration checked once per page request (was 7000+), schema files parsed at most once per request
     (was 500+), project paths / repos / rulings computed once per request inside a request scope and every call outside one;
  3. read paths quiet: no [ENVELOPE_TRACE] inside a request-level read scope; outside (claim / match diagnostics) and with the
     debug switch unchanged;
  4. perf log line carries the truth cache counters.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.aipos_cli import frontmatter as fm  # noqa: E402
from tools.aipos_cli import task_loader  # noqa: E402
from tools.aipos_cli.records import clear_records_cache  # noqa: E402
# the F174 rig (3 probe projects) and its helpers are reused as-is
from test_aipos_f174_board_readonly_pages import P1, _card, _free_port, _policy, _request, rig  # noqa: E402,F401


def _show(text: str) -> None:
    print(text, flush=True)


@pytest.fixture(autouse=True)
def _fresh_caches():
    fm.clear_frontmatter_memo()
    clear_records_cache()
    yield
    task_loader.enable_truth_cache(False)
    fm.clear_frontmatter_memo()
    clear_records_cache()


@pytest.fixture
def cached(rig, monkeypatch):
    """Truth cache on (as in the board server), racy window 0 so the fixture files written a moment ago may be stored."""
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    yield rig
    task_loader.enable_truth_cache(False)


@contextlib.contextmanager
def _uncached():
    """Compute as before F245 (cache switched off for the block, stored results kept)."""
    previous = task_loader._TRUTH_STATE["enabled"]
    task_loader._TRUTH_STATE["enabled"] = False
    try:
        yield
    finally:
        task_loader._TRUTH_STATE["enabled"] = previous


def _stats() -> dict:
    return task_loader.truth_cache_stats()


def _pages(home: Path) -> dict[str, str]:
    from tools.aipos_cli import board_pages
    from web.board.readonly_pages import render_owner_inbox, render_page

    decl = board_pages.pages_declaration()
    return {"overview": render_page("overview", home, {}), "onboarding": render_page("onboarding", home, {}),
            "roadmap": render_page("roadmap", home, {decl["query"]["project"]: [P1]}),
            "inbox": render_owner_inbox(home, {}, owner=True)}


def _api(home: Path) -> dict[str, str]:
    from tools.aipos_cli.board_adapter import get_owner_inbox
    from web.board.app import get_overview

    inbox = get_owner_inbox(home)
    inbox.pop("timestamp", None)  # response time stamp (made per response, never cached)
    return {"api_overview": json.dumps(get_overview(home_root=home), ensure_ascii=False, sort_keys=True, default=str),
            "api_inbox": json.dumps(inbox, ensure_ascii=False, sort_keys=True, default=str)}


#: the only clock-derived text on these pages: suggested authorization commands carry "now + validity" to the second (a cached page
#: shows the value of the minute it was derived in; the comparison masks it)
_SUGGESTED_EXPIRY = re.compile(r"(--expires-at )\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def _mask(text: str) -> str:
    return _SUGGESTED_EXPIRY.sub(r"\1<now+validity>", text)


def _everything(home: Path) -> dict[str, str]:
    return {k: _mask(v) for k, v in {**_pages(home), **_api(home)}.items()}


# ---------------------------------------------------------------------------
# item 1: result cache
# ---------------------------------------------------------------------------

def test_item1_hit_returns_the_same_bytes_as_computing_again(cached):
    home = cached["home"]
    with _uncached():
        before = _everything(home)
    first = _everything(home)
    hits = _stats()["hits"]
    second = _everything(home)
    _show(f"truth cache after two rounds: {_stats()}")
    assert _stats()["hits"] > hits  # the second round was served from the cache
    assert first == before and second == before


def test_item1_callers_get_private_copies(cached):
    from tools.aipos_cli.board_pages import collect_overview

    home = cached["home"]
    got = collect_overview(home)
    got["projects"].clear()
    assert collect_overview(home)["projects"], "a caller's change leaked into the stored result"


def _move(gov: Path, task_id: str, src: str, dst: str) -> None:
    from tools.aipos_cli.task_loader import queue_root_for

    path = queue_root_for(gov) / src / f"{task_id.lower()}.md"
    target = queue_root_for(gov) / dst / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8").replace(f"status: {src}", f"status: {dst}")
    target.write_text(text, encoding="utf-8")
    path.unlink()


def _decide_roadmap_notice(rig: dict) -> None:
    from tools.aipos_cli.owner_inbox import collect_inbox, decide

    with _uncached():
        notice = next(n for n in collect_inbox(rig["home"])["notices"] if n["category"] == "roadmap_drift")
    out = decide(notice["item_id"], "acknowledge", home_root=rig["home"], actor="owner", relayed_by=f"advisor.{P1}.h1",
                 owner_words="F245 fixture ruling", confirm=True)
    assert out["ok"], out


def _edit_verdict_in_place(rig: dict) -> None:
    from tools.aipos_cli.record_writer import record_dir

    path = next(record_dir(rig["g1"], "audit_verdicts", "K-1").glob("*.md"))
    path.write_text(path.read_text(encoding="utf-8").replace("verdict: PASS", "verdict: FAIL"), encoding="utf-8")


def _envelope_changed(rig: dict) -> None:
    path = rig["g2"] / "5_tasks" / "policies" / "pol_beta_loop_1.md"
    path.write_text(path.read_text(encoding="utf-8").replace("max_tasks: 2", "max_tasks: 9"), encoding="utf-8")


def _roadmap_changed(rig: dict) -> None:
    from test_aipos_f171_roadmap_board_readonly import _cli

    rc, out, err = _cli("roadmap", "add-criterion", "--workspace-root", str(rig["g1"]), "--milestone", "W1", "--id", "W1C2",
                        "--statement", "F245 new criterion", "--verify", "auto", "--confirm", "--actor", f"advisor.{P1}.h1")
    assert rc == 0, out + err


MUTATIONS = {
    "new_card": lambda rig: _card(rig["g1"], P1, "pending", "F245-NEW", created_days_ago=0),
    "queue_state_move": lambda rig: _move(rig["g1"], "K-3", "claimed", "pending"),
    "ruling_appended": _decide_roadmap_notice,
    "envelope_changed": _envelope_changed,
    "record_edited_in_place": _edit_verdict_in_place,
    "roadmap_changed": _roadmap_changed,
}


@pytest.mark.parametrize("change", sorted(MUTATIONS))
def test_item1_every_truth_change_is_seen_by_the_next_request(cached, change):
    home = cached["home"]
    first = _everything(home)
    assert _everything(home) == first  # stored and served
    MUTATIONS[change](cached)
    after = _everything(home)  # the very next request
    with _uncached():
        expected = _everything(home)
    changed = sorted(k for k in after if after[k] != first[k])
    _show(f"{change}: pages changed on the next request = {changed}")
    assert after == expected, "the next request did not reflect the truth change"
    assert changed, "the mutation did not change any page (fixture would not prove freshness)"


def test_item1_inbox_inside_overview_carries_its_truth(cached):
    """The overview contains the inbox: the overview's stored entry depends on the inbox (AIPOS-F249 件③: by the inbox's key and value
    digest, also when the inbox was served from its own stored result inside the overview; the inbox's own truth is not copied into the
    overview); a ruling = both derived again on the next request."""
    from tools.aipos_cli.board_pages import collect_overview
    from tools.aipos_cli.owner_inbox import collect_inbox

    home = cached["home"]
    collect_inbox(home)  # the inbox stored on its own first, then served inside the overview
    collect_overview(home)
    entries = dict(task_loader._TRUTH_ENTRIES)
    inbox_key, inbox = next((k, e) for k, e in entries.items() if k[0] == "owner_inbox.collect_inbox")
    assert any(k[0] == "board_pages.collect_overview" for k in entries), _stats()["last_not_stored"]
    overview = next(e for k, e in entries.items() if k[0] == "board_pages.collect_overview")
    assert overview["children"].get(inbox_key) == inbox["digest"]
    _decide_roadmap_notice(cached)
    after_inbox, after_overview = collect_inbox(home), collect_overview(home)
    with _uncached():
        assert after_inbox == collect_inbox(home) and after_overview == collect_overview(home)


def test_item1_next_minute_is_served(cached, monkeypatch):
    """AIPOS-F249 件①: the minute left the fingerprint; a time judgment reports when it flips (tests/test_aipos_f249_* checks the flips)."""
    from tools.aipos_cli import board_pages

    home = cached["home"]
    derived = _count(monkeypatch, board_pages, "_collect_onboarding")
    board_pages.collect_onboarding(home)
    board_pages.collect_onboarding(home)
    assert derived[0] == 1  # same minute = served
    real = task_loader._time.time
    monkeypatch.setattr(task_loader._time, "time", lambda: real() + 61)
    board_pages.collect_onboarding(home)
    assert derived[0] == 1  # next minute, no judgment on this page flipped = still served


def test_item1_young_truth_is_stored_by_content(rig):
    """Production racy window (2 s): the fixture was written a moment ago. AIPOS-F249: young truth the derivation opened / listed is
    fingerprinted by content (a same-tick rewrite keeping the identity is still seen, tests/test_aipos_f249_*), so derivations over it are
    stored and served; a young explicit fingerprint (records group: re-checked by its own stat-based recompute) still = not stored."""
    from tools.aipos_cli.board_pages import collect_overview

    task_loader.enable_truth_cache()
    first = collect_overview(rig["home"])
    assert collect_overview(rig["home"]) == first
    stats = _stats()
    _show(f"young fixture: {stats}")
    young = [k for k, e in task_loader._TRUTH_ENTRIES.items() if e["young"]]
    assert stats["hits"] >= 1 and young, stats
    assert stats["not_stored"] == 0 or "unsettled ('records.group'" in str(stats["last_not_stored"]), stats


def test_item1_undeclared_subprocess_not_stored_declared_external_read_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    git = shutil.which("git") or "git"

    def undeclared() -> str:
        return subprocess.run([git, "--version"], capture_output=True, text=True).stdout

    def declared() -> str:
        with task_loader.truth_external_read():
            return subprocess.run([git, "--version"], capture_output=True, text=True).stdout

    task_loader.truth_cached(("f245.undeclared",), undeclared)
    assert "subprocess.Popen" in str(_stats()["last_not_stored"])
    task_loader.truth_cached(("f245.undeclared",), undeclared)
    assert _stats()["hits"] == 0
    task_loader.truth_cached(("f245.declared",), declared)
    task_loader.truth_cached(("f245.declared",), declared)
    assert _stats()["hits"] == 1


def test_item1_file_read_and_missing_file_are_fingerprinted(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    present, later = tmp_path / "a.txt", tmp_path / "later.txt"
    present.write_text("one", encoding="utf-8")

    def derive() -> tuple[str, bool]:
        return present.read_text(encoding="utf-8"), later.exists()

    assert task_loader.truth_cached(("f245.files",), derive) == ("one", False)
    assert task_loader.truth_cached(("f245.files",), derive) == ("one", False) and _stats()["hits"] == 1
    later.write_text("x", encoding="utf-8")  # a path checked while missing appears
    assert task_loader.truth_cached(("f245.files",), derive) == ("one", True)
    present.write_text("two!", encoding="utf-8")  # content change
    assert task_loader.truth_cached(("f245.files",), derive) == ("two!", True)


def test_item1_proc_liveness_fingerprinted_by_content(tmp_path, monkeypatch):
    """/proc/<pid>/stat keeps its identity when the process dies (zombie): liveness is fingerprinted by what is read."""
    from tools.aipos_cli.loop_run_record import _proc_available, process_alive, process_fingerprint

    if not _proc_available():
        pytest.skip("no /proc on this host (ps fallback = subprocess = never stored)")
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    child = subprocess.Popen(["sleep", "60"])  # this test's own fixture process
    try:
        fingerprint = process_fingerprint(child.pid)

        def derive() -> bool:
            return process_alive(child.pid, fingerprint)

        assert task_loader.truth_cached(("f245.alive",), derive) is True
        assert task_loader.truth_cached(("f245.alive",), derive) is True and _stats()["hits"] == 1
        child.kill()
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)  # dead, not reaped: /proc entry still there (zombie)
        assert Path(f"/proc/{child.pid}/stat").exists()
        assert task_loader.truth_cached(("f245.alive",), derive) is False
    finally:
        child.kill()
        child.wait()


def test_item1_off_unless_the_board_server_switches_it_on(rig):
    from tools.aipos_cli.board_pages import collect_overview

    collect_overview(rig["home"])
    collect_overview(rig["home"])
    assert _stats()["enabled"] is False and _stats()["entries"] == 0 and _stats()["hits"] == 0


def test_item1_run_server_switches_it_on(monkeypatch):
    import web.board.app as app

    class _Stub:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def serve_forever(self) -> None:
            return None

    monkeypatch.setattr(app, "ThreadingHTTPServer", _Stub)
    task_loader.enable_truth_cache(False)
    app.run_server(host="127.0.0.1", port=0, prewarm=False)
    assert _stats()["enabled"] is True


def test_item1_board_http_reflects_a_change_on_the_next_get(cached):
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'], token_ref='owner-fixture')}"
    serve_root = cached["tmp"] / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=serve_root, home_root=cached["home"], session_store=store, connection_paths=[],
        board_audit_log_path=cached["tmp"] / "board-runtime" / "audit.jsonl"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    b = {"base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie}
    try:
        status, first, _ = _request(b, "/owner/overview")
        assert status == 200
        hits, misses = _stats()["hits"], _stats()["misses"]
        # served: nothing derived again (AIPOS-F249: hits also count the nested results checked on the way)
        assert _request(b, "/owner/overview")[1] == first and _stats()["hits"] > hits and _stats()["misses"] == misses
        _card(cached["g1"], P1, "pending", "F245-HTTP", created_days_ago=0)
        status, after, _ = _request(b, "/owner/overview")
        assert status == 200 and after != first
        with _uncached():
            assert _mask(after) == _mask(_request(b, "/owner/overview")[1])
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# item 2: de-duplication
# ---------------------------------------------------------------------------

def _count(monkeypatch, module, name: str) -> list[int]:
    calls = [0]
    real = getattr(module, name)

    def spy(*args, **kwargs):
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return calls


@pytest.mark.parametrize("page", ["overview", "inbox"])
def test_item2_inbox_declaration_checked_once_and_schemas_parsed_once_per_request(rig, monkeypatch, page):
    import tools.schema_loader as schema_loader
    from tools.aipos_cli import owner_inbox
    from web.board.readonly_pages import render_owner_inbox, render_page

    def render() -> str:
        return render_page("overview", rig["home"], {}) if page == "overview" else render_owner_inbox(rig["home"], {}, owner=False)

    render()  # warm process
    checks = _count(monkeypatch, owner_inbox, "_check_tiers")
    reads = _count(monkeypatch, schema_loader, "_load_json_file")
    render()
    _show(f"{page}: inbox declaration checks = {checks[0]}, schema file parses = {reads[0]} (one request)")
    assert checks[0] == 1
    assert reads[0] <= 7  # at most each schema file once (the read-only per-card "fresh" schema reload = once per request)


def test_item2_request_memos_inside_a_scope_only(rig, monkeypatch):
    from tools.aipos_cli import workspace_config
    from tools.aipos_cli.task_loader import read_request_scope

    g1 = rig["g1"]
    paths = _count(monkeypatch, workspace_config, "_project_paths")
    repos = _count(monkeypatch, workspace_config, "_project_repos")
    first, second = workspace_config.project_paths(g1), workspace_config.project_paths(g1)
    workspace_config.project_repos(g1), workspace_config.project_repos(g1)
    assert paths[0] == 2 and repos[0] == 2 and first == second  # outside a request scope: every call, as before
    with read_request_scope():
        a, b = workspace_config.project_paths(g1), workspace_config.project_paths(g1)
        workspace_config.project_repos(g1), workspace_config.project_repos(g1)
        assert a == b == first
        a["declared"]["queue_root"] = "mutated by a caller"
        assert workspace_config.project_paths(g1) == first  # private copies
    assert paths[0] == 3 and repos[0] == 3


def test_item2_rulings_scanned_once_per_request(rig, monkeypatch):
    from tools.aipos_cli import owner_inbox
    from tools.aipos_cli.task_loader import read_request_scope

    _decide_roadmap_notice(rig)
    scans = _count(monkeypatch, owner_inbox, "_scan_rulings")
    outside = owner_inbox.rulings_for_task(rig["g1"], "K-1"), owner_inbox.rulings_for_task(rig["g1"], "K-1")
    assert scans[0] == 2
    with read_request_scope():
        inside = [owner_inbox.rulings_for_task(rig["g1"], t) for t in ("K-1", "K-2", "K-3")]
        decl = owner_inbox.inbox_declaration()
        everything = owner_inbox._all_rulings(rig["g1"], decl)
        everything.append({"task_id": "leak"})
        assert all(r.get("task_id") != "leak" for r in owner_inbox._all_rulings(rig["g1"], decl))
    assert scans[0] == 3 and inside[0] == outside[0]


# ---------------------------------------------------------------------------
# item 3: read paths quiet about envelope traces
# ---------------------------------------------------------------------------

def test_item3_no_envelope_trace_in_read_scope_claim_path_and_debug_switch_unchanged(rig, monkeypatch):
    from tools.aipos_cli import autonomy_policy
    from tools.aipos_cli.machine_zone import _valid_envelopes
    from tools.aipos_cli.task_loader import read_request_scope

    lines: list[str] = []
    monkeypatch.setattr(autonomy_policy._ENVELOPE_LOGGER, "info", lambda msg, *a, **k: lines.append(str(msg)))
    monkeypatch.setitem(autonomy_policy._TRACE_SWITCH, "enabled", None)  # board / gate process: no CLI decision
    monkeypatch.delenv(str(autonomy_policy.envelope_trace_declaration()["debug_env"]), raising=False)
    _policy(rig["g1"], "pol_alpha_old_1", who=f"advisor.{P1}.h1", max_tasks=5, used=1, days_left=-1)

    with read_request_scope():
        _valid_envelopes(rig["g1"])  # the board overview path that flooded the gate log (card_stats -> owner_viewer)
        autonomy_policy.trace_envelope({"phase": "probe"})
    assert lines == []
    _valid_envelopes(rig["g1"])  # outside a read scope (claim / match diagnostics): unchanged
    outside = len(lines)
    assert outside > 0 and all(line.startswith("[ENVELOPE_TRACE] ") for line in lines)
    monkeypatch.setenv(str(autonomy_policy.envelope_trace_declaration()["debug_env"]), "1")
    with read_request_scope():
        autonomy_policy.trace_envelope({"phase": "probe"})  # explicit debug switch: still printed
    monkeypatch.delenv(str(autonomy_policy.envelope_trace_declaration()["debug_env"]))
    monkeypatch.setitem(autonomy_policy._TRACE_SWITCH, "enabled", True)  # CLI --verbose
    with read_request_scope():
        autonomy_policy.trace_envelope({"phase": "probe"})
    assert len(lines) == outside + 2
    _show(f"[ENVELOPE_TRACE] lines: read scope 0, outside {outside}, debug switch / --verbose in read scope 2")


def test_item3_declaration_key_missing_keeps_old_behaviour(monkeypatch):
    from tools.aipos_cli import autonomy_policy
    from tools.aipos_cli.task_loader import read_request_scope

    decl = dict(autonomy_policy.envelope_trace_declaration())
    assert decl["read_scope_enabled"] is False
    decl.pop("read_scope_enabled")
    monkeypatch.setattr(autonomy_policy, "envelope_trace_declaration", lambda: decl)
    monkeypatch.setitem(autonomy_policy._TRACE_SWITCH, "enabled", None)
    monkeypatch.delenv(str(decl["debug_env"]), raising=False)
    with read_request_scope():
        assert autonomy_policy.envelope_trace_enabled() is bool(decl["non_cli_default_enabled"])


# ---------------------------------------------------------------------------
# item 4: perf log
# ---------------------------------------------------------------------------

def test_item4_perf_log_line_carries_truth_cache_counters(tmp_path):
    import time

    from web.board.app import _append_perf_line

    log = tmp_path / "perf.jsonl"
    _append_perf_line(log, "GET", "/owner/overview", time.perf_counter())
    line = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert set(line["truth_cache"]) >= {"hits", "misses", "stored", "not_stored", "last_not_stored", "entries", "enabled"}
