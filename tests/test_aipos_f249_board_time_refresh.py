"""AIPOS-F249 - board home fast when opened a while later or while a loop runs: the truth cache no longer expires by the minute (time
judgments report when they flip), pages built on a per-card result are not derived again when that result came out the same (a loop
run record rewritten every few seconds), and the board server re-derives the served pages in the background after their truth changed.

Fixture range = temporary roots only (the F246 rig: temporary HOME + temporary home root with two projects, enrolled advisors, cards
stalled / fresh / closed, an overdue milestone; the F174 rig for the overview). No real governance root is read or written; the
real-root timings (temporary port instance, crossing minutes, a loop running on a scratch copy), the byte-for-byte page comparison
on the real root, the CPU figures and both baselines are attached to the card RETURN.
The F177 racy window is set to 0 where a test needs fixture files written a moment ago to be stored by identity alone; the
young-content test keeps the production window. Every 'now' in the time tests is a frozen virtual instant (tools.aipos_cli.clock's
datetime and the truth cache's wall clock patched together), so 'cached' and 'computed again' are compared at the very same instant.

Items covered:
  1. time: cached pages (home, needs-you, project, inbox, roadmap) are byte-identical to computing again at the same instant across a
     sweep of instants crossing every judgment of the rig (stalled / stale / recent deliveries / overdue days / days waited), and within
     the sweep nothing is derived again unless a judgment flipped (next minute = served); a plain clock read inside a derivation =
     valid to the end of that minute (fail-closed); a reported flip = valid until then; young truth is fingerprinted by content
     (a rewrite that keeps the stat identity is seen);
  2. background: a request meeting a stale result being re-derived waits for it and gets the new result (never the old one); the
     refresher pass re-derives a served page after its truth changed so the next request is served, and inside the debounce window a
     request still sees the change (derives itself); the declaration is fail-closed, a missing one = no refresher; run_server starts it;
  3. per card: a nested result that came out the same leaves the outer one served (early cut-off), one whose re-derivation raises is
     met by the outer derivation in its own context; a loop run record rewritten with
     the product's writer re-derives that card's loop status / stall judgment / run instants but not the home page or the inbox, and a
     rewrite that changes what the page shows is seen by the very next request; truth_record (history index) stays flat.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.aipos_cli import clock  # noqa: E402
from tools.aipos_cli import frontmatter as fm  # noqa: E402
from tools.aipos_cli import task_loader  # noqa: E402
from tools.aipos_cli.records import clear_records_cache  # noqa: E402
# the F246 rig (home page: advisors, stalled / fresh / closed cards, overdue milestone) and its helpers are reused as-is
from test_aipos_f246_board_home import ADV_S, ADV_X, ADV_Y, C, _headline, _ok, rig  # noqa: E402,F401


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


@contextlib.contextmanager
def _uncached():
    previous = task_loader._TRUTH_STATE["enabled"]
    task_loader._TRUTH_STATE["enabled"] = False
    try:
        yield
    finally:
        task_loader._TRUTH_STATE["enabled"] = previous


@pytest.fixture
def frozen(monkeypatch):
    """A frozen virtual 'now' for the clock module and the truth cache's wall clock (set .at = epoch seconds)."""
    state = types.SimpleNamespace(at=time.time())
    real_datetime = clock.datetime

    class _Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            value = real_datetime.fromtimestamp(state.at, timezone.utc)
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(clock, "datetime", _Frozen)
    monkeypatch.setattr(task_loader, "_time", types.SimpleNamespace(time=lambda: state.at, monotonic=time.monotonic,
                                                                     perf_counter=time.perf_counter))
    return state


def _pages(home: Path) -> dict[str, str]:
    from tools.aipos_cli.board_home import collect_home
    from web.board.readonly_pages import render_owner_inbox, render_page

    return {"home": render_page("home", home, {}, owner=True), "asks": render_page("home", home, {"view": ["asks"]}, owner=True),
            "project": render_page("project", home, {"project": [C]}), "project_adv_y": render_page("project", home, {"project": [C], "subproject": [ADV_Y]}),
            "inbox": render_owner_inbox(home, {}, owner=True), "roadmap": render_page("roadmap", home, {"project": [C]}),
            "data_home": json.dumps(collect_home(home), ensure_ascii=False, sort_keys=True, default=str)}


_AGE = __import__("re").compile(r'"age_hours": [0-9.e-]+')


_NOW = __import__("re").compile(r'"now": "[^"]*"')


def _masked(pages: dict[str, str]) -> dict[str, str]:
    """Pages taken on the real clock: the headline age in the data is a continuous time field (computed when the result is taken);
    so is the data time `now` (second precision: a cached page and one computed again right after differ when a second boundary falls
    between them — AIPOS-F254 found this flaking ~1 in 4 on main as well)."""
    return {k: _NOW.sub('"now": <now>', _AGE.sub('"age_hours": <age>', v)) for k, v in pages.items()}


def _timeless(data: dict) -> dict:
    """collect_home data taken on the real clock without its time fields (data time now, each headline's age_hours)."""
    out = json.loads(json.dumps(data, default=str))
    out.pop("now", None)
    for card in out.get("cards") or []:
        if isinstance(card.get("headline"), dict):
            card["headline"].pop("age_hours", None)
    return out


def _derived() -> int:
    return task_loader.truth_cache_stats()["misses"]


# ---------------------------------------------------------------------------
# item 1: time judgments
# ---------------------------------------------------------------------------

def test_item1_cached_pages_equal_computing_again_at_every_instant(rig, frozen, monkeypatch):
    """One long-lived cache taken at each instant of a sweep that crosses every time judgment of the rig vs computing afresh at that
    same instant: byte-identical; next minute / next hour without a flip = nothing derived again."""
    from tools.aipos_cli.advisor_headline import headline_declaration
    from tools.aipos_cli.board_home import home_declaration

    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    gc = rig["gc"]
    _ok(_headline(gc, ADV_X, stage="delivery", title="Cust"))
    _ok(_headline(rig["gs"], ADV_S, title="Solo"))
    home = rig["home"]
    base = time.time()
    frozen.at = base
    task_loader.enable_truth_cache()
    stale_h = float(headline_declaration()["stale_hours"])
    home_decl = home_declaration()
    stall_h = float(home_decl["stall"]["threshold_hours"])
    recent_d = int(home_decl["recent_days"])
    # instants: next minute / hour (no flip), around the fresh card becoming stalled, the headline going stale, the closed card leaving the
    # recent window, the next UTC midnight (overdue days / roadmap date), and far later
    midnight = (datetime.fromtimestamp(base, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    offsets = sorted({0, 61, 3600, stall_h * 3600 - 30, stall_h * 3600 + 30, stale_h * 3600 + 30, recent_d * 86400 - 3600 * 13,
                      recent_d * 86400 + 30, midnight - base - 1, midnight - base + 1, 3 * 86400 + 7, 30 * 86400})
    log = []
    for off in offsets:
        frozen.at = base + off
        before = _derived()
        cached = _pages(home)
        derived = _derived() - before
        with _uncached():
            fresh = _pages(home)
        changed = sorted(k for k in cached if cached[k] != fresh[k])
        log.append(f"+{off:>9.0f}s derived={derived} differs={changed}")
        assert not changed, f"cached page differs from computing again at +{off}s: {changed}"
        if off in (61, 3600):
            assert derived == 0, f"+{off}s: no judgment flipped, yet {derived} derived again"
    _show("[item1 time sweep]\n" + "\n".join(log))
    st = task_loader.truth_cache_stats()
    assert st["minute_capped"] == 0, st["last_minute_cap"]


def test_item1_plain_clock_read_is_bounded_to_its_minute_and_a_reported_flip_to_its_moment(frozen):
    task_loader.enable_truth_cache()
    frozen.at = 1_800_000_000.0 + 10  # 10 s into a minute
    calls = {"raw": 0, "judged": 0}

    def raw() -> str:
        calls["raw"] += 1
        return clock.iso_z(clock.utc_now())  # a plain read: its judgments do not report

    flip = frozen.at + 600.0

    def judged() -> bool:
        calls["judged"] += 1
        now = clock.judged_now()
        clock.holds_until(now, flip)
        return now.timestamp() < flip

    for _ in range(2):
        task_loader.truth_cached(("f249.raw",), raw)
        task_loader.truth_cached(("f249.judged",), judged)
    assert calls == {"raw": 1, "judged": 1}
    frozen.at += 55  # next minute
    task_loader.truth_cached(("f249.raw",), raw)
    task_loader.truth_cached(("f249.judged",), judged)
    assert calls == {"raw": 2, "judged": 1}, "plain read = next minute derived again; judged read = served"
    assert "f249.raw" in str(task_loader.truth_cache_stats()["last_minute_cap"])
    frozen.at = flip - 0.5
    assert task_loader.truth_cached(("f249.judged",), judged) is True and calls["judged"] == 1
    frozen.at = flip
    assert task_loader.truth_cached(("f249.judged",), judged) is False and calls["judged"] == 2


def test_item1_young_truth_fingerprinted_by_content(tmp_path, monkeypatch):
    """Production racy window: a file written a moment ago is read; a rewrite of the same size that keeps the stat identity (a same-tick
    rewrite; simulated by holding the identity) is still seen by the next request (content fingerprint)."""
    task_loader.enable_truth_cache()
    path = tmp_path / "young.txt"
    path.write_text("aaaa", encoding="utf-8")

    def derive() -> str:
        return path.read_text(encoding="utf-8")

    assert task_loader.truth_cached(("f249.young",), derive) == "aaaa"
    entry = task_loader._TRUTH_ENTRIES[("f249.young",)]
    key = (str(path), True)
    assert key in entry["young"], entry["young"]
    assert task_loader.truth_cached(("f249.young",), derive) == "aaaa"  # stored and served
    ident = entry["stats"][key]
    path.write_text("bbbb", encoding="utf-8")
    real = task_loader._path_identity
    monkeypatch.setattr(task_loader, "_path_identity", lambda p, m=True: ident if (p, m) == key else real(p, m))
    assert task_loader.truth_cached(("f249.young",), derive) == "bbbb", "same identity, new content: must be seen"


# ---------------------------------------------------------------------------
# item 2: background re-derivation and freshness
# ---------------------------------------------------------------------------

def test_item2_request_during_rederivation_waits_and_gets_the_new_result(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    path = tmp_path / "truth.txt"
    path.write_text("old", encoding="utf-8")
    started, release = threading.Event(), threading.Event()
    slow = {"on": False}

    def derive() -> str:
        value = path.read_text(encoding="utf-8")
        if slow["on"]:
            started.set()
            release.wait(10)
        return value

    assert task_loader.truth_cached(("f249.slow",), derive) == "old"
    waiting = threading.Event()

    class _Event(threading.Event):  # the in-flight marker; tells the test when a request starts waiting on it
        def wait(self, timeout=None):
            waiting.set()
            return super().wait(timeout)

    monkeypatch.setattr(task_loader, "_threading", types.SimpleNamespace(Event=_Event, Thread=threading.Thread,
                                                                         current_thread=threading.current_thread))
    path.write_text("new", encoding="utf-8")  # truth changed
    slow["on"] = True
    background = threading.Thread(target=task_loader.truth_cached, args=(("f249.slow",), derive))
    background.start()
    assert started.wait(10)  # the re-derivation is in flight
    got: dict[str, str] = {}
    request = threading.Thread(target=lambda: got.setdefault("value", task_loader.truth_cached(("f249.slow",), derive)))
    request.start()
    assert waiting.wait(10)  # the request waits on the in-flight derivation (bounded) instead of answering with "old"
    assert "value" not in got
    slow["on"] = False
    release.set()
    background.join(10)
    request.join(10)
    _show(f"[item2 in flight] request got {got} stats={task_loader.truth_cache_stats()}")
    assert got == {"value": "new"} and task_loader.truth_cache_stats()["waited"] >= 1


def test_item2_refresher_pass_rederives_a_changed_page_debounce_keeps_freshness(rig, monkeypatch):
    from tools.aipos_cli.board_home import collect_home
    from tools.aipos_cli.board_pages import pages_declaration
    from test_aipos_f246_board_home import _card

    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    home = rig["home"]
    task_loader.enable_truth_cache()
    spec = task_loader.truth_refresh_declaration(pages_declaration())
    assert spec is not None and set(spec) >= {"check_interval_seconds", "debounce_seconds", "max_wait_seconds", "max_pages"}
    monkeypatch.setitem(task_loader._TRUTH_REFRESH, "decl", {**spec, "debounce_seconds": 3600})
    first = collect_home(home)  # a served page (registered for the refresher)
    assert task_loader.truth_cache_stats()["pages"] >= 1
    assert task_loader.truth_refresh_pass() == 0  # nothing changed = nothing derived
    _card(rig["gc"], C, "claimed", "X-9", title="X-9 New work", created_by=ADV_X)
    assert task_loader.truth_refresh_pass() >= 1  # the change is derived in the background
    misses = _derived()
    after = _timeless(collect_home(home))
    assert _derived() == misses, "the request after the background pass must be served"
    with _uncached():
        assert after == _timeless(collect_home(home))
    assert after != _timeless(first)
    # inside the debounce window the background does not derive again, a request still sees the change
    _card(rig["gc"], C, "claimed", "X-10", title="X-10 More work", created_by=ADV_X)
    assert task_loader.truth_refresh_pass() == 0
    latest = _timeless(collect_home(home))
    with _uncached():
        assert latest == _timeless(collect_home(home))
    assert latest != after
    _show(f"[item2 refresher] {task_loader.truth_cache_stats()}")


def test_item2_refresher_thread_and_declaration(monkeypatch):
    from tools.schema_loader import SchemaLoadError

    assert task_loader.truth_refresh_declaration({}) is None
    with pytest.raises(SchemaLoadError):
        task_loader.truth_refresh_declaration({"truth_refresh": {"check_interval_seconds": 1}})
    with pytest.raises(SchemaLoadError):
        task_loader.truth_refresh_declaration({"truth_refresh": {"check_interval_seconds": 1, "debounce_seconds": 0, "max_wait_seconds": 1,
                                                                "max_pages": 1}})
    assert task_loader.start_truth_refresher(None) is False  # missing declaration = no refresher
    task_loader.enable_truth_cache()
    passed = threading.Event()
    monkeypatch.setattr(task_loader, "truth_refresh_pass", lambda: passed.set() or 0)
    spec = {"check_interval_seconds": 0.01, "debounce_seconds": 1, "max_wait_seconds": 1, "max_pages": 2}
    assert task_loader.start_truth_refresher(spec) is True and task_loader.truth_cache_stats()["refresher"] is True
    assert passed.wait(10)  # the refresher thread ran a pass
    task_loader.stop_truth_refresher()
    assert task_loader.truth_cache_stats()["refresher"] is False


def test_item2_run_server_starts_the_refresher(monkeypatch):
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
    stats = task_loader.truth_cache_stats()
    assert stats["enabled"] is True and stats["refresher"] is True


# ---------------------------------------------------------------------------
# item 3: per card, early cut-off
# ---------------------------------------------------------------------------

def test_item3_nested_result_that_came_out_the_same_leaves_the_outer_served(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    path = tmp_path / "count.txt"
    path.write_text("1", encoding="utf-8")
    calls = {"inner": 0, "outer": 0}

    def inner() -> bool:
        calls["inner"] += 1
        return int(path.read_text(encoding="utf-8")) > 5

    def outer() -> str:
        calls["outer"] += 1
        return "many" if task_loader.truth_cached(("f249.inner",), inner) else "few"

    assert task_loader.truth_cached(("f249.outer",), outer) == "few"
    path.write_text("2", encoding="utf-8")  # inner truth changed, inner value the same
    assert task_loader.truth_cached(("f249.outer",), outer) == "few"
    assert calls == {"inner": 2, "outer": 1}
    path.write_text("9", encoding="utf-8")  # inner value changed
    assert task_loader.truth_cached(("f249.outer",), outer) == "many"
    assert calls == {"inner": 3, "outer": 2}


def test_item3_nested_rederivation_error_is_met_by_the_outer_derivation(tmp_path, monkeypatch):
    """A nested result whose re-derivation now raises (an unreadable record) = changed: the outer derivation runs again and handles the
    error in its own context (as computing without the cache does), the check itself does not raise."""
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    path = tmp_path / "record.txt"
    path.write_text("ok", encoding="utf-8")

    def inner() -> str:
        text = path.read_text(encoding="utf-8")
        if text == "broken":
            raise ValueError("record unreadable")
        return text

    def outer() -> str:
        try:
            return "fine:" + task_loader.truth_cached(("f249.err.inner",), inner)
        except ValueError as exc:
            return f"named: {exc}"

    assert task_loader.truth_cached(("f249.err.outer",), outer) == "fine:ok"
    path.write_text("broken", encoding="utf-8")
    assert task_loader.truth_cached(("f249.err.outer",), outer) == "named: record unreadable"
    with _uncached():
        assert outer() == "named: record unreadable"


def _run_record(gov: Path, task_id: str, *, events: int) -> Path:
    """A running loop run record for task_id written with the product's writer (atomic replace); this test process is the recorded
    loop / launch process (alive, start-time fingerprinted)."""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import _write_record, loop_runs_dir, process_fingerprint, run_record_declaration

    decl = run_record_declaration(load_loop_contract())
    pid = os.getpid()
    path = loop_runs_dir(gov, task_id) / f"looprun_{task_id}_20261029_000000_{pid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = iso_z()
    fp = process_fingerprint(pid)
    meta = {"record_type": decl["record_type"], "run_id": path.stem, "task_id": task_id, "status": "running", "driver": ADV_X,
            "host": socket.gethostname(), "pid": pid, "process_fingerprint": fp, "started_at": now, "updated_at": now,
            "log_path": str(path.with_suffix(".log")), "steps": [],
            "current_step": {"index": 1, "kind": "execute", "node": "claim", "state": "claimed", "card": task_id, "action": "launch"},
            "launches": [{"card": task_id, "harness": "pi", "transport": "local", "host": socket.gethostname(), "pid": pid, "pgid": pid,
                          "process_fingerprint": fp, "started_at": now, "ended_at": None, "outcome": None,
                          "activity": {"events": events, "last_event_at": now, "last_activity_at": now, "last_activity_kind": "tool:bash",
                                       "counts": {"tool": events}}}]}
    _write_record(path, meta)
    return path


def test_item3_loop_run_record_rewrite_rederives_the_card_not_the_pages(rig, monkeypatch):
    from tools.aipos_cli.loop_run_record import _proc_available

    if not _proc_available():
        pytest.skip("no /proc on this host (ps fallback = subprocess = never stored)")
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    home, gc = rig["home"], rig["gc"]
    _ok(_headline(gc, ADV_X, stage="delivery", title="Cust"))
    _run_record(gc, "X-1", events=1)
    task_loader.enable_truth_cache()
    first = _masked(_pages(home))
    assert _masked(_pages(home)) == first
    derived: list[str] = []
    real = task_loader._derive

    def spy(key, compute, changed):
        derived.append(key[0])
        return real(key, compute, changed)

    monkeypatch.setattr(task_loader, "_derive", spy)
    for events in (2, 3):  # the loop flushes its activity: nothing the pages show changes
        _run_record(gc, "X-1", events=events)
        derived.clear()
        again = _masked(_pages(home))
        _show(f"[item3 flush {events}] derived again: {sorted(set(derived))}")
        assert again == first
        assert not {"board_home.collect_home", "owner_inbox.collect_inbox", "board_home.collect_project_brief"} & set(derived), derived
        assert {"loop_run_record.loop_status", "board_home.card_stall_state"} & set(derived), derived
    # a rewrite that changes what the pages show: a running loop on the stalled card X-2 = it is moving again
    derived.clear()
    _run_record(gc, "X-2", events=1)
    moved = _masked(_pages(home))
    with _uncached():
        assert moved == _masked(_pages(home))
    assert moved["home"] != first["home"] and "board_home.collect_home" in derived


def test_item3_truth_record_stays_flat_with_the_cache_on(rig, monkeypatch):
    """The history index (truth_record, kept outside this process) records every path it read even where the board's cache would
    otherwise hand it a nested result by digest (the card index)."""
    from tools.aipos_cli.task_loader import find_task_card, read_request_scope

    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    task_loader.enable_truth_cache()
    gc = rig["gc"]

    def compute() -> str:
        with read_request_scope():
            path, _state = find_task_card(gc, "X-1")
        return str(path)

    value, truth = task_loader.truth_record(compute)
    paths = {p for p, _m, _i in truth["stats"]}
    assert value.endswith("x-1.md") and value in paths and truth["refused"] is None
    assert any(p.endswith("x-2.md") for p in paths), "the queue scan behind the card index must be in the record"
