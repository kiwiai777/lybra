"""AIPOS-F177 - read performance: libyaml CSafeLoader (same results), per-file / per-text read caches, request-level reuse.

Fixture range = temporary roots only (pytest tmp_path, the F174 rig for the board). No real governance root is read or
written here; the real-root equivalence check, the CLI / board timings and both baselines are attached to the card RETURN.

Items covered:
  1. the parser choice lives only in frontmatter._yaml_safe_load; libyaml results equal SafeLoader results; any libyaml
     failure is re-parsed by the pure-Python loader (same mapping, same warning text);
  2. per-file cache: only a modified / new file is read and parsed again (counter assertions), deletes and same-size
     rewrites are seen, young files are never cached, the F55 half-write semantics are kept, LRU bound, thread safety,
     returned values are private copies; record listing keeps the pre-F177 order and suffix rule;
  3. request-level read scope: per project the roadmap status / envelope overview are derived once per request (and every
     call outside a scope still derives), scopes do not leak across threads; board pages render the same bytes with and
     without the scope and the caches; the optional perf log writes one JSON line per GET without the query string;
  5. single implementation: cache / parser symbols live in one module each.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from tools.aipos_cli import frontmatter as fm  # noqa: E402
from tools.aipos_cli.records import clear_records_cache, load_records  # noqa: E402
# the F174 rig / board fixtures (3 probe projects with roadmap, cards, verdicts, envelopes, enrollment) are reused as-is
from test_aipos_f174_board_readonly_pages import P1, board, rig  # noqa: E402,F401


def _show(text: str) -> None:
    print(text, flush=True)


@pytest.fixture(autouse=True)
def _fresh_caches():
    fm.clear_frontmatter_memo()
    clear_records_cache()
    yield
    fm.clear_frontmatter_memo()
    clear_records_cache()


def _md(path: Path, meta: dict, body: str = "body\n") -> None:
    from tools.aipos_cli.record_writer import render_markdown

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(meta, body), encoding="utf-8")


def _claims_root(tmp_path: Path, n: int = 6) -> Path:
    from tools.aipos_cli.record_writer import record_dir

    root = tmp_path / "gov"
    for i in range(n):
        tid = f"T177-{i}"
        _md(record_dir(root, "claims", tid) / f"claim_{tid}.md", {"record_type": "claim_log", "claim_id": f"claim_{tid}", "task_id": tid,
                                                                    "claimed_at": f"2026-10-0{i + 1}T00:00:00Z"})
    return root


def _code_hits(symbol: str) -> list[str]:
    """Product code files (tools/, web/; Python sources, tests excluded) containing the literal symbol."""
    out = subprocess.run(["git", "grep", "-l", "-F", symbol, "--", "tools", "web"], cwd=REPO_ROOT, capture_output=True,
                         text=True).stdout.split()
    return [f for f in out if f.endswith(".py") and "/tests/" not in f and not Path(f).name.startswith("test_")]


def _typed(value):
    """Type-strict, order-strict form (1 / True / 1.0 differ; dict key order kept)."""
    if isinstance(value, dict):
        return ("dict", tuple((_typed(k), _typed(v)) for k, v in value.items()))
    if isinstance(value, list):
        return ("list", tuple(_typed(v) for v in value))
    if isinstance(value, set):
        return ("set", tuple(sorted(repr(_typed(v)) for v in value)))
    return (type(value).__name__, repr(value))


# ---------------------------------------------------------------------------
# item 1: parser choice
# ---------------------------------------------------------------------------

SHAPES = [
    "a: 1\nb: true\nc: 1.5\nd: null\ne: ~\nf: 0o17\ng: 017\nh: 0x1f\ni: 1_000\nj: .inf\nk: -.inf\n",
    "created_at: 2026-10-16T15:37:26Z\nday: 2026-10-16\nquoted: '2026-10-16'\nsexa: 1:30\n",
    "title: \"AIPOS-F177 读取性能: frontmatter 用 libyaml\"\nnote: 'it''s fine'\nmulti: |\n  line 1\n  line 2\nfold: >\n  a\n  b\n",
    "lane:\n  repo: /x/y\n  paths:\n  - tools/aipos_cli/\n  - web/\n  roles:\n  - executor\nempty_list: []\nempty_map: {}\n",
    "rework_rounds:\n- round: 1\n  verdict: FAIL\n- round: 2\n  verdict: PASS\nflow: [a, b, {c: d}]\n",
    "base: &b {x: 1, y: [1, 2]}\nref: *b\nmerged:\n  <<: *b\n  z: 3\n",
    "yes_word: yes\nno_word: No\non_word: on\nescaped: \"tab\\tnew\\nline \\u00e9\"\nset_value: !!set {a, b}\n",
]


def test_item1_parser_choice_single_place_and_equal_results():
    hits = _code_hits("CSafeLoader")
    _show(f"CSafeLoader referenced in: {hits}")
    assert hits == ["tools/aipos_cli/frontmatter.py"]
    for shape in SHAPES:
        expected = yaml.load(shape, Loader=yaml.SafeLoader)
        if hasattr(yaml, "CSafeLoader"):
            assert _typed(yaml.load(shape, Loader=yaml.CSafeLoader)) == _typed(expected), shape
        # the reading point hands YAML the fence-to-fence lines joined by "\n" (no trailing newline), as before F177
        as_read = yaml.load("\n".join(shape.splitlines()), Loader=yaml.SafeLoader)
        data, body, warnings = fm.parse_markdown_frontmatter(f"---\n{shape}---\nbody\n")
        assert warnings == [] and body == "body" and _typed(data) == _typed(as_read), shape


def test_item1_libyaml_used_when_present(monkeypatch):
    if not hasattr(yaml, "CSafeLoader"):
        # PyYAML without libyaml: the pure-Python loader is the only path (behaviour unchanged)
        assert fm._yaml_safe_load("a: 1\n") == {"a": 1}
        return
    used = {"n": 0}

    class Spy(yaml.CSafeLoader):  # type: ignore[misc,valid-type]
        def __init__(self, stream):
            used["n"] += 1
            super().__init__(stream)

    monkeypatch.setattr(yaml, "CSafeLoader", Spy)
    assert fm._yaml_safe_load("a: [1, 2]\n") == {"a": [1, 2]}
    assert used["n"] == 1


@pytest.mark.parametrize("bad", ["a: [1, 2\n", "a: b: c\n", "a:\n\t- x\n", "- just\n- a list\n", "k: v\nk2: *missing\n"])
def test_item1_libyaml_failure_gives_exact_safeloader_outcome(monkeypatch, bad):
    with_c = fm.parse_markdown_frontmatter(f"---\n{bad}---\nbody\n")
    fm.clear_frontmatter_memo()
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    pure = fm.parse_markdown_frontmatter(f"---\n{bad}---\nbody\n")
    _show(f"{bad!r}: warnings with libyaml = {with_c[2]}")
    assert _typed(with_c[0]) == _typed(pure[0]) and with_c[1:] == pure[1:]


def test_item1_libyaml_raising_falls_back_to_pure_loader(monkeypatch):
    class Boom(yaml.SafeLoader):
        def __init__(self, stream):
            raise yaml.YAMLError("simulated libyaml failure")

    monkeypatch.setattr(yaml, "CSafeLoader", Boom, raising=False)
    assert fm.parse_markdown_frontmatter("---\nx: 1\ny: [a]\n---\nB\n") == ({"x": 1, "y": ["a"]}, "B", [])


# ---------------------------------------------------------------------------
# item 2: per-file cache
# ---------------------------------------------------------------------------

def _stats() -> tuple[int, int, int]:
    s = fm.frontmatter_memo_stats()
    return s["file"]["misses"], s["file"]["hits"], s["text"]["misses"]


def test_item2_only_the_changed_file_is_read_and_parsed_again(tmp_path, monkeypatch):
    from tools.aipos_cli.record_writer import record_dir

    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)  # fixture files are fresh; 0 = cache them at once
    root = _claims_root(tmp_path)
    first = load_records(root)
    reads, _hits, parses = _stats()
    _show(f"cold load: {first['summary']['claim_logs']} claims, file reads {reads}, yaml parses {parses}")
    assert first["summary"]["claim_logs"] == 6 and reads == 6 and parses == 6

    load_records(root)  # unchanged group: F55 fast path, nothing read
    assert _stats()[0] == 6

    target = record_dir(root, "claims", "T177-2") / "claim_T177-2.md"
    _md(target, {"record_type": "claim_log", "claim_id": "claim_T177-2", "task_id": "T177-2", "claimed_at": "2026-10-09T00:00:00Z"},
        "changed body\n")
    changed = load_records(root)
    reads, hits, parses = _stats()
    _show(f"after modifying one file: file reads {reads} (+{reads - 6}), file hits {hits}, yaml parses {parses} (+{parses - 6})")
    assert (reads, parses) == (7, 7) and hits >= 5
    rec = next(r for r in changed["claims"] if r["task_id"] == "T177-2")
    assert rec["body"] == "changed body" and rec["claimed_at"] == "2026-10-09T00:00:00Z"

    _md(record_dir(root, "claims", "T177-NEW") / "claim_T177-NEW.md",
        {"record_type": "claim_log", "claim_id": "claim_T177-NEW", "task_id": "T177-NEW"})
    added = load_records(root)
    assert added["summary"]["claim_logs"] == 7 and _stats()[0] == 8 and _stats()[2] == 8

    os.remove(record_dir(root, "claims", "T177-0") / "claim_T177-0.md")
    removed = load_records(root)
    assert removed["summary"]["claim_logs"] == 6 and "T177-0" not in removed["task_claims"]
    assert _stats()[0] == 8  # nothing else re-read


def test_item2_young_files_are_never_cached_and_replaced_files_are_seen(tmp_path):
    path = tmp_path / "card.md"
    path.write_text("---\nv: aaaa\n---\nB\n", encoding="utf-8")
    assert fm.read_markdown_frontmatter(path)[0] == {"v": "aaaa"}
    assert fm.read_markdown_frontmatter(path)[0] == {"v": "aaaa"}
    stats = fm.frontmatter_memo_stats()["file"]
    _show(f"fresh file read twice with the default racy window: {stats}")
    assert stats["hits"] == 0 and stats["unsettled"] == 2 and stats["entries"] == 0

    # same size, same mtime restored, written in place while young: still read again (never cached while young)
    st = path.stat()
    path.write_text("---\nv: bbbb\n---\nB\n", encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert fm.read_markdown_frontmatter(path)[0] == {"v": "bbbb"}


def test_item2_settled_file_replaced_with_same_size_and_mtime_is_seen(tmp_path, monkeypatch):
    path = tmp_path / "card.md"
    path.write_text("---\nv: aaaa\n---\nB\n", encoding="utf-8")
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    assert fm.read_markdown_frontmatter(path)[0] == {"v": "aaaa"}
    assert fm.read_markdown_frontmatter(path)[0] == {"v": "aaaa"}
    assert fm.frontmatter_memo_stats()["file"]["hits"] == 1
    st = path.stat()
    tmp = tmp_path / "card.md.tmp"
    tmp.write_text("---\nv: bbbb\n---\nB\n", encoding="utf-8")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, path)  # atomic replace: same size, same mtime, new inode
    assert path.stat().st_size == st.st_size and path.stat().st_mtime_ns == st.st_mtime_ns
    assert fm.read_markdown_frontmatter(path)[0] == {"v": "bbbb"}


def test_item2_half_write_then_complete_is_seen(tmp_path, monkeypatch):
    """F55 red line 3 under the per-file cache: a half-written record (no closing fence) is read with its parse error and
    the completed write is read again (different size / ctime), never served from the cache."""
    from tools.aipos_cli.record_writer import record_dir

    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    root = _claims_root(tmp_path, n=2)
    target = record_dir(root, "claims", "T177-HALF") / "claim_T177-HALF.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\nrecord_type: claim_log\n", encoding="utf-8")
    half = load_records(root)
    assert half["summary"]["parse_errors"] == 1
    target.write_text("---\nrecord_type: claim_log\nclaim_id: claim_T177-HALF\ntask_id: T177-HALF\n---\nbody\n", encoding="utf-8")
    full = load_records(root)
    assert full["summary"]["claim_logs"] == 3 and full["summary"]["parse_errors"] == 0


def test_item2_returned_values_are_private_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    path = tmp_path / "c.md"
    path.write_text("---\nlist: [1, 2]\nmap: {k: v}\n---\nB\n", encoding="utf-8")
    for read in (lambda: fm.read_markdown_frontmatter(path)[0], lambda: fm.parse_markdown_frontmatter(path.read_text())[0]):
        data = read()
        data["list"].append(3)
        data["map"]["k"] = "changed"
        data["new"] = 1
        assert read() == {"list": [1, 2], "map": {"k": "v"}}


def test_item2_lru_bound_declared_and_enforced(tmp_path, monkeypatch):
    assert isinstance(fm._FILE_CACHE_MAX_ENTRIES, int) and isinstance(fm._FRONTMATTER_MEMO_MAX_ENTRIES, int)
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    monkeypatch.setattr(fm, "_FILE_CACHE_MAX_ENTRIES", 3)
    monkeypatch.setattr(fm, "_FRONTMATTER_MEMO_MAX_ENTRIES", 3)
    paths = []
    for i in range(5):
        p = tmp_path / f"c{i}.md"
        p.write_text(f"---\ni: {i}\n---\nB\n", encoding="utf-8")
        paths.append(p)
    for _ in range(2):
        assert [fm.read_markdown_frontmatter(p)[0]["i"] for p in paths] == [0, 1, 2, 3, 4]
    stats = fm.frontmatter_memo_stats()
    _show(f"bounded caches after 2 x 5 reads: {stats}")
    assert stats["file"]["entries"] <= 3 and stats["text"]["entries"] <= 3
    assert stats["file"]["evictions"] >= 2 and stats["text"]["evictions"] >= 2


def test_item2_thread_safety_under_concurrent_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    paths = []
    for i in range(20):
        p = tmp_path / f"c{i}.md"
        p.write_text(f"---\ni: {i}\nv: old\n---\nB\n", encoding="utf-8")
        paths.append(p)
    errors: list[str] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            for i, p in enumerate(paths):
                try:
                    data = fm.read_markdown_frontmatter(p)[0]
                except Exception as exc:  # noqa: BLE001 - collected and asserted below (not swallowed)
                    errors.append(repr(exc))
                    continue
                if data.get("i") != i or data.get("v") not in ("old", "new"):
                    errors.append(f"{p.name}: {data!r}")

    threads = [threading.Thread(target=reader) for _ in range(6)]
    for t in threads:
        t.start()
    for round_no in range(30):
        p = paths[round_no % len(paths)]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(f"---\ni: {round_no % len(paths)}\nv: new\n---\nB\n", encoding="utf-8")
        os.replace(tmp, p)
    stop.set()
    for t in threads:
        t.join()
    assert errors == []
    assert all(fm.read_markdown_frontmatter(p)[0]["v"] == "new" for p in paths)


def test_item2_record_listing_same_order_and_suffix_rule(tmp_path):
    from tools.aipos_cli.records import _iter_record_files

    def before(root: Path, task_filter: str | None = None):  # the pre-F177 implementation, verbatim
        if not root.exists():
            return []
        files = []
        task_dirs = [root / task_filter] if task_filter else sorted(path for path in root.iterdir() if path.is_dir())
        for task_dir in task_dirs:
            if not task_dir.is_dir():
                continue
            for path in sorted(task_dir.iterdir()):
                if path.is_file() and path.suffix == ".md":
                    files.append((path, task_dir.name))
        return files

    root = tmp_path / "group"
    for name in ("T-B", "T-A", "T-a", "T-10", "T-9"):
        (root / name).mkdir(parents=True)
    for name in ("z.md", "a.md", "..md", ".md", "b.MD", "c.md.bak", "B.md"):
        (root / "T-B" / name).write_text("x", encoding="utf-8")
    (root / "T-B" / "dir.md").mkdir()
    (root / "T-A" / "link.md").symlink_to(root / "T-B" / "a.md")
    (root / "T-A" / "dangling.md").symlink_to(root / "nowhere.md")
    (root / "loose.md").write_text("x", encoding="utf-8")
    (root / "T-link").symlink_to(root / "T-A")
    assert _iter_record_files(root) == before(root)
    assert _iter_record_files(root, "T-B") == before(root, "T-B")
    assert _iter_record_files(root, "missing") == before(root, "missing") == []
    assert _iter_record_files(tmp_path / "absent") == []


def test_item2_card_relative_path_same_inside_and_outside_scope(tmp_path):
    from tools.aipos_cli.task_loader import _card_rel_path, task_card_lookup_scope

    real_root = tmp_path / "real-gov"
    queue = real_root / "5_tasks" / "queue" / "pending"
    queue.mkdir(parents=True)
    (queue / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "elsewhere.md").write_text("x", encoding="utf-8")
    (queue / "linked.md").symlink_to(tmp_path / "elsewhere.md")
    alias = tmp_path / "alias-gov"
    alias.symlink_to(real_root)
    for root in (real_root, alias):
        card = root / "5_tasks" / "queue" / "pending" / "a.md"
        outside = _card_rel_path(card, root)
        with task_card_lookup_scope(root):
            assert _card_rel_path(card, root) == outside == "5_tasks/queue/pending/a.md"
        link = root / "5_tasks" / "queue" / "pending" / "linked.md"
        with pytest.raises(ValueError):
            _card_rel_path(link, root)
        with task_card_lookup_scope(root), pytest.raises(ValueError):
            _card_rel_path(link, root)


# ---------------------------------------------------------------------------
# item 3: request-level reuse + perf log
# ---------------------------------------------------------------------------

def test_item3_request_scope_derives_once_per_project_and_copies_out(rig, monkeypatch):
    from tools.aipos_cli import autonomy_policy, roadmap
    from tools.aipos_cli.autonomy_policy import envelope_overview
    from tools.aipos_cli.roadmap_drift import roadmap_status
    from tools.aipos_cli.task_loader import read_request_scope

    calls = {"envelope": 0, "roadmap": 0}
    real_env, real_links = autonomy_policy._envelope_overview, roadmap.card_links

    def spy_env(*a, **k):
        calls["envelope"] += 1
        return real_env(*a, **k)

    def spy_links(*a, **k):
        calls["roadmap"] += 1
        return real_links(*a, **k)

    monkeypatch.setattr(autonomy_policy, "_envelope_overview", spy_env)
    monkeypatch.setattr(roadmap, "card_links", spy_links)
    g1 = rig["g1"]
    roadmap_status(g1, project=P1)
    roadmap_status(g1, project=P1)
    envelope_overview(g1)
    envelope_overview(g1)
    assert calls == {"envelope": 2, "roadmap": 2}  # outside a scope: every call derives (gate paths unchanged)
    with read_request_scope():
        a = roadmap_status(g1, project=P1)
        b = roadmap_status(g1, project=P1)
        e1 = envelope_overview(g1)
        e2 = envelope_overview(g1)
        seen_in_thread: list[int] = []
        t = threading.Thread(target=lambda: (envelope_overview(g1), seen_in_thread.append(calls["envelope"])))
        t.start()
        t.join()
    _show(f"derivations with 2 calls each inside one request scope (+1 envelope call from another thread): {calls}")
    assert calls == {"envelope": 4, "roadmap": 3}  # one each in the scope; the other thread has no scope (derives again)
    assert a == b and a is not b and e1 == e2 and e1 is not e2
    a["milestones"].append("mutated")
    assert roadmap_status(g1, project=P1)["milestones"] != a["milestones"]


def _get(b, path: str) -> str:
    req = urllib.request.Request(b["base"] + path, headers={"Cookie": b["cookie"]})
    with urllib.request.urlopen(req, timeout=120) as resp:
        assert resp.status == 200
        return resp.read().decode("utf-8")


PAGES = ("/owner/overview", "/owner/inbox", "/owner/roadmap", "/owner/onboarding")


def test_item3_board_pages_same_bytes_with_and_without_scope_and_caches(board, monkeypatch):
    from tools.aipos_cli import task_loader

    fast = {p: _get(board, p) for p in PAGES}
    fast_again = {p: _get(board, p) for p in PAGES}
    # pre-F177 reading: no request scope, no read caches, pure-Python SafeLoader
    monkeypatch.setattr(task_loader, "read_request_scope", contextlib.nullcontext)
    monkeypatch.setattr(fm, "_FILE_CACHE_MAX_ENTRIES", 0)
    monkeypatch.setattr(fm, "_FRONTMATTER_MEMO_MAX_ENTRIES", 0)
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    fm.clear_frontmatter_memo()
    clear_records_cache()
    plain = {p: _get(board, p) for p in PAGES}
    for p in PAGES:
        _show(f"{p}: {len(fast[p])} chars, same with / without scope and caches: {fast[p] == plain[p]}")
        assert fast[p] == fast_again[p] == plain[p], p
    assert fm.frontmatter_memo_stats()["file"]["entries"] == 0


def test_item3_perf_log_optional_one_line_per_get_without_query(board, tmp_path):
    from http.server import ThreadingHTTPServer

    from web.board.app import make_handler

    from web.board.app import SESSION_COOKIE_NAME, SessionStore

    log = tmp_path / "perf" / "board-perf.jsonl"

    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'], token_ref='owner-fixture')}"
    serve_root = tmp_path / "serve-root-perf"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        repo_root=serve_root, home_root=board["home"], session_store=store, connection_paths=[],
        board_audit_log_path=tmp_path / "audit.jsonl", perf_log_path=log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        b = {"base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie}
        _get(b, "/owner/overview?lang=en")
        _get(b, "/owner/inbox")
    finally:
        server.shutdown()
        server.server_close()
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    _show(f"perf log lines: {lines}")
    assert [line["path"] for line in lines] == ["/owner/overview", "/owner/inbox"]
    assert all(isinstance(line["server_ms"], float) and line["method"] == "GET" and "file" in line["frontmatter_cache"]
               for line in lines)
    assert not (tmp_path / "audit.jsonl").exists()  # separate from the board audit log
    # off by default: a handler built without the switch has no perf log
    assert make_handler(repo_root=serve_root, home_root=board["home"], session_store=store, connection_paths=[])._perf_log is None


# ---------------------------------------------------------------------------
# item 5: single implementation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol, owner", [
    ("_FILE_CACHE", "tools/aipos_cli/frontmatter.py"),
    ("_FRONTMATTER_MEMO", "tools/aipos_cli/frontmatter.py"),
    ("def read_request_scope", "tools/aipos_cli/task_loader.py"),
    ("def request_memo", "tools/aipos_cli/task_loader.py"),
    ("def _yaml_safe_load", "tools/aipos_cli/frontmatter.py"),
])
def test_item5_single_implementation(symbol, owner):
    hits = _code_hits(symbol)
    assert hits == [owner], (symbol, hits)
