"""AIPOS-F229 - board performance: overview / inbox without re-reading and re-deriving the same truth over and over.

Fixture range = temporary roots only (pytest tmp_path, the F174 rig). No real governance root is read or written here; the
real-root CSafeLoader / SafeLoader equivalence check, the timings (before / after, cold / hot, HTTP) and both baselines are
attached to the card RETURN.

Items covered:
  1. parser: unchanged single place (frontmatter._yaml_safe_load, CSafeLoader when present; F177 tests own the result equality);
  2. per-file cache for JSON (frontmatter.read_json_cached, used by workspace_config.load_workspace_config: project.json was read and
     decoded thousands of times per page): same value and same exceptions as json.loads(open().read()), private copies (shared=True
     only for read-only callers), a change is seen on the next read (in place / replace with same size and mtime / young file never
     cached); record group fingerprint made cheaper with the same result;
  3. request-level de-duplication: inside a request-level read scope load_records (full) / load_all_tasks run once per governance root
     (outside a scope every call still loads), callers get private containers; get_overview runs in one scope (was 5 records loads and
     3 queue loads per project) and returns the same data as without the scope; a new card / record is seen by the very next request;
  4. board prewarm at start: read-only, fills the caches, a failure is reported (not swallowed); switch --no-prewarm;
  5. single implementation: the per-file cache and its helpers live only in frontmatter.py.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.aipos_cli import frontmatter as fm  # noqa: E402
from tools.aipos_cli import records as records_mod  # noqa: E402
from tools.aipos_cli import task_loader  # noqa: E402
from tools.aipos_cli.records import clear_records_cache, load_records  # noqa: E402
# the F174 rig (3 probe projects with roadmap, cards, verdicts, envelopes, enrollment) is reused as-is
from test_aipos_f174_board_readonly_pages import P1, rig  # noqa: E402,F401


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


def _claims_root(tmp_path: Path, n: int = 4) -> Path:
    from tools.aipos_cli.record_writer import record_dir

    root = tmp_path / "gov"
    for i in range(n):
        tid = f"T229-{i}"
        _md(record_dir(root, "claims", tid) / f"claim_{tid}.md", {"record_type": "claim_log", "claim_id": f"claim_{tid}", "task_id": tid,
                                                                    "claimed_at": f"2026-10-0{i + 1}T00:00:00Z"})
    return root


def _code_hits(symbol: str) -> list[str]:
    out = subprocess.run(["git", "grep", "-l", "-F", symbol, "--", "tools", "web"], cwd=REPO_ROOT, capture_output=True,
                         text=True).stdout.split()
    return [f for f in out if f.endswith(".py") and "/tests/" not in f and not Path(f).name.startswith("test_")]


# ---------------------------------------------------------------------------
# item 1: parser choice unchanged
# ---------------------------------------------------------------------------

def test_item1_parser_choice_still_single_place_and_libyaml_used(monkeypatch):
    import yaml

    assert _code_hits("CSafeLoader") == ["tools/aipos_cli/frontmatter.py"]
    if getattr(yaml, "CSafeLoader", None) is None:
        pytest.skip("PyYAML without libyaml on this host (pure SafeLoader path covered by the F177 tests)")
    seen: list[str] = []
    real_load = yaml.load

    def spy(stream, Loader):  # noqa: N803
        seen.append(Loader.__name__)
        return real_load(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", spy)
    assert fm.parse_markdown_frontmatter("---\nx: 1\nwhen: 2026-10-25\n---\nB\n")[0]["x"] == 1
    _show(f"loader used for a frontmatter parse: {seen}")
    assert seen == ["CSafeLoader"]


# ---------------------------------------------------------------------------
# item 2: per-file JSON cache + cheaper group fingerprint
# ---------------------------------------------------------------------------

def test_item2_json_same_value_and_exceptions_as_json_loads(tmp_path, monkeypatch):
    from tools.aipos_cli.workspace_config import load_workspace_config

    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    doc = {"project": "p", "paths": {"queue_root": "5_tasks/queue"}, "n": 1, "f": 1.5, "b": True, "z": None, "l": [1, {"k": "v"}],
           "中文": "值"}
    path = tmp_path / "project.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    first, second = fm.read_json_cached(path), fm.read_json_cached(path)
    assert first == second == json.loads(path.read_text(encoding="utf-8"))
    assert list(first) == list(doc) and type(first["n"]) is int and type(first["b"]) is bool
    assert fm.frontmatter_memo_stats()["file"]["hits"] == 1
    with pytest.raises(FileNotFoundError):
        fm.read_json_cached(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        fm.read_json_cached(bad)
    with pytest.raises(ValueError, match="Invalid Lybra workspace config JSON"):
        load_workspace_config(bad)
    listing = tmp_path / "list.json"
    listing.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_workspace_config(listing)


def test_item2_json_private_copies_and_shared_view_for_read_only_callers(tmp_path, monkeypatch):
    from tools.aipos_cli.workspace_config import load_workspace_config, read_project_json

    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    (tmp_path / "project.json").write_text(json.dumps({"project": "p", "repos": {"items": {"a": "/x"}}, "l": [1]}), encoding="utf-8")
    got = read_project_json(tmp_path)
    got["l"].append(2)
    got["repos"]["items"]["b"] = "/y"
    got["new"] = 1
    assert read_project_json(tmp_path) == {"project": "p", "repos": {"items": {"a": "/x"}}, "l": [1]}
    shared_a = load_workspace_config(tmp_path / "project.json", shared=True)
    shared_b = load_workspace_config(tmp_path / "project.json", shared=True)
    assert shared_a is shared_b and shared_a == read_project_json(tmp_path) and read_project_json(tmp_path) is not shared_a


def test_item2_json_change_is_seen_on_the_next_read(tmp_path, monkeypatch):
    from tools.aipos_cli.workspace_config import project_paths

    path = tmp_path / "project.json"
    (tmp_path / "5_tasks" / "queue").mkdir(parents=True)
    path.write_text(json.dumps({"project": "p", "paths": {"return_root": "aaaa"}}), encoding="utf-8")
    # young file (default racy window): never cached, an in-place rewrite with the same size and mtime is still seen
    assert project_paths(tmp_path)["return_root"] == tmp_path / "aaaa"
    st = path.stat()
    path.write_text(json.dumps({"project": "p", "paths": {"return_root": "bbbb"}}), encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert project_paths(tmp_path)["return_root"] == tmp_path / "bbbb"
    assert fm.frontmatter_memo_stats()["file"]["entries"] == 0
    # settled file: cached, then an atomic replace with the same size and mtime (new inode) is seen at once
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    assert project_paths(tmp_path)["return_root"] == tmp_path / "bbbb"
    assert project_paths(tmp_path)["return_root"] == tmp_path / "bbbb"
    hits_before = fm.frontmatter_memo_stats()["file"]["hits"]
    assert hits_before >= 1
    st = path.stat()
    tmp = tmp_path / "project.json.tmp"
    tmp.write_text(json.dumps({"project": "p", "paths": {"return_root": "cccc"}}), encoding="utf-8")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, path)
    assert project_paths(tmp_path)["return_root"] == tmp_path / "cccc"
    # in-place edit of a settled file (ctime moves) is seen too
    path.write_text(json.dumps({"project": "p", "paths": {"return_root": "dddd"}}), encoding="utf-8")
    assert project_paths(tmp_path)["return_root"] == tmp_path / "dddd"
    path.unlink()
    assert project_paths(tmp_path)["return_root"] != tmp_path / "dddd"  # deleted = declared default, never the cached value


def _reference_fingerprint(group_root: Path):
    """The pre-F229 algorithm (stat every directory entry, Path objects), kept here as the equality oracle."""
    if not group_root.is_dir():
        return None
    entries = []
    stack = [group_root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                        entries.append((entry.path, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    entries.sort()
    return tuple(entries)


def test_item2_group_fingerprint_same_result_as_before(tmp_path):
    root = tmp_path / "claims"
    for tid in ("T1", "T2", "T3"):
        _md(root / tid / f"claim_{tid}.md", {"record_type": "claim_log", "claim_id": f"claim_{tid}", "task_id": tid})
    (root / "T1" / "notes.txt").write_text("not a record", encoding="utf-8")
    (root / "T2" / "nested").mkdir()
    _md(root / "T2" / "nested" / "deep.md", {"record_type": "claim_log"})
    (root / "T3" / "dir.md").mkdir()  # a directory named *.md is not a record file
    os.symlink(root / "T1" / "claim_T1.md", root / "T3" / "link.md")  # a symlink is not a regular record file (follow_symlinks=False)
    got = records_mod._group_fingerprint(root)
    _show(f"fingerprint entries: {[Path(p).relative_to(root).as_posix() for p, _m, _s in got]}")
    assert got == _reference_fingerprint(root)
    assert len(got) == 4
    assert records_mod._group_fingerprint(tmp_path / "absent") is None


# ---------------------------------------------------------------------------
# item 3: request-level de-duplication
# ---------------------------------------------------------------------------

def _count(monkeypatch, module, name: str) -> dict[str, int]:
    calls = {"n": 0}
    real = getattr(module, name)

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(module, name, spy)
    return calls


def test_item3_load_records_once_per_root_per_request_private_containers(tmp_path, monkeypatch):
    from tools.aipos_cli.task_loader import read_request_scope, task_card_lookup_scope

    root = _claims_root(tmp_path)
    calls = _count(monkeypatch, records_mod, "_load_records")
    load_records(root)
    load_records(root)
    assert calls["n"] == 2  # outside a request scope: every call loads (gate paths unchanged)
    with task_card_lookup_scope(root):  # a plain lookup scope is not a request scope: still loads every time
        load_records(root)
        load_records(root)
    assert calls["n"] == 4
    with read_request_scope():
        a = load_records(root)
        b = load_records(root.parent / root.name)  # same root, other spelling
        c = load_records(root, groups=["claims"])  # subset loads are not memoised
    _show(f"_load_records calls: 2 outside + 2 lookup scope + {calls['n'] - 4} in one request scope (2 full calls = 1 load, + 1 subset)")
    assert calls["n"] == 6
    assert a == b and a is not b and c["summary"]["claim_logs"] == 4
    a["claims"].append({"bogus": True})
    a["task_claims"]["T229-0"].append({"bogus": True})
    a["summary"]["claim_logs"] = 99
    with read_request_scope():
        fresh = load_records(root)
    assert fresh["summary"]["claim_logs"] == 4 and len(fresh["claims"]) == 4 and len(fresh["task_claims"]["T229-0"]) == 1
    assert b["summary"]["claim_logs"] == 4 and len(b["claims"]) == 4


def test_item3_new_and_changed_records_seen_by_the_next_request(tmp_path):
    from tools.aipos_cli.record_writer import record_dir
    from tools.aipos_cli.task_loader import read_request_scope

    root = _claims_root(tmp_path)
    with read_request_scope():
        assert load_records(root)["summary"]["claim_logs"] == 4
    _md(record_dir(root, "claims", "T229-NEW") / "claim_T229-NEW.md",
        {"record_type": "claim_log", "claim_id": "claim_T229-NEW", "task_id": "T229-NEW"})
    target = record_dir(root, "claims", "T229-1") / "claim_T229-1.md"
    _md(target, {"record_type": "claim_log", "claim_id": "claim_T229-1", "task_id": "T229-1", "claimed_at": "2026-10-25T01:02:03Z"},
        "changed body, longer than before\n")
    with read_request_scope():
        got = load_records(root)
    _show(f"next request after adding one record and rewriting another: {got['summary']['claim_logs']} claims")
    assert got["summary"]["claim_logs"] == 5
    assert next(r for r in got["claims"] if r["task_id"] == "T229-1")["claimed_at"] == "2026-10-25T01:02:03Z"


def _card(root: Path, state: str, tid: str) -> Path:
    path = root / "5_tasks" / "queue" / state / f"{tid.lower()}.md"
    _md(path, {"task_id": tid, "title": tid, "status": state, "project": "p229", "task_mode": "code"}, f"# {tid}\n")
    return path


def test_item3_load_all_tasks_once_per_root_per_request_private_copies(tmp_path, monkeypatch):
    from tools.aipos_cli.task_loader import load_all_tasks, read_request_scope

    root = tmp_path / "gov"
    for i in range(3):
        _card(root, "pending", f"T229-C{i}")
    calls = _count(monkeypatch, task_loader, "load_task_file")
    outside = load_all_tasks(root)
    load_all_tasks(root)
    assert calls["n"] == 6
    with read_request_scope():
        a = load_all_tasks(root)
        b = load_all_tasks(root)
    assert calls["n"] == 9  # one load of the 3 cards for the whole request
    assert a == b == outside and a is not b and a[0] is not b[0]
    a[0]["metadata"]["title"] = "mutated"
    a[0]["parse_errors"].append("x")
    assert b[0]["metadata"]["title"] == "T229-C0" and b[0]["parse_errors"] == []
    _card(root, "claimed", "T229-C9")
    with read_request_scope():
        assert [t["task_id"] for t in load_all_tasks(root)][-1] == "T229-C9"


def _overview_projects(result: dict) -> dict:
    return {w["project"]: w for w in result["workspaces"]}


def test_item3_get_overview_one_scope_same_data_and_fewer_loads(rig, monkeypatch):
    from web.board import app

    home = rig["home"]
    records_calls = _count(monkeypatch, records_mod, "_load_records")
    task_calls = _count(monkeypatch, task_loader, "load_task_file")
    fast = app.get_overview(home_root=home)
    fast_counts = (records_calls["n"], task_calls["n"])
    projects = [w for w in fast["workspaces"] if w["status"] == "ok"]
    monkeypatch.setattr(task_loader, "read_request_scope", contextlib.nullcontext)  # pre-F229: no request scope
    records_calls["n"] = task_calls["n"] = 0
    plain = app.get_overview(home_root=home)
    plain_counts = (records_calls["n"], task_calls["n"])
    _show(f"get_overview over {len(projects)} projects: (_load_records, load_task_file) calls with one request scope {fast_counts}, "
          f"without {plain_counts}")
    assert fast == plain
    assert fast_counts[0] == len(projects)  # one full records load per project
    assert plain_counts[0] >= 4 * len(projects) and plain_counts[1] >= 2 * fast_counts[1] > 0


def test_item3_get_overview_next_request_sees_a_new_card(rig):
    from web.board import app

    home = rig["home"]
    before = _overview_projects(app.get_overview(home_root=home))[P1]["queue_counts"]["pending"]
    _card(rig["g1"], "pending", "F229-NEWCARD")
    after = _overview_projects(app.get_overview(home_root=home))[P1]["queue_counts"]["pending"]
    _show(f"{P1} pending before / after adding one card: {before} / {after}")
    assert after == before + 1


# ---------------------------------------------------------------------------
# item 4: board prewarm
# ---------------------------------------------------------------------------

def test_item4_prewarm_fills_caches_read_only(rig):
    from web.board import app

    home = rig["home"]
    before = subprocess.run(["git", "status", "--porcelain"], cwd=home, capture_output=True, text=True).stdout
    res = app.prewarm_read_caches(home_root=home)
    stats = fm.frontmatter_memo_stats()
    _show(f"prewarm: {res}; caches after: {stats}")
    assert res["ok"] is True and res["error"] is None and isinstance(res["seconds"], float)
    assert stats["file"]["misses"] > 0 and stats["text"]["entries"] > 0
    assert subprocess.run(["git", "status", "--porcelain"], cwd=home, capture_output=True, text=True).stdout == before


def test_item4_prewarm_failure_reported_not_swallowed(rig, monkeypatch, capsys):
    from tools.aipos_cli import board_pages
    from web.board import app

    def boom(*_a, **_k):
        raise RuntimeError("f229 prewarm probe")

    monkeypatch.setattr(board_pages, "collect_overview", boom)
    res = app.prewarm_read_caches(home_root=rig["home"])
    err = capsys.readouterr().err
    assert res["ok"] is False and res["error"] == "RuntimeError: f229 prewarm probe"
    assert "board read-cache prewarm failed" in err and "f229 prewarm probe" in err


def test_item4_prewarm_switch(monkeypatch):
    from web.board import app

    monkeypatch.setattr(sys, "argv", ["web.board.app", "--no-prewarm"])
    assert app.parse_args().no_prewarm is True
    monkeypatch.setattr(sys, "argv", ["web.board.app"])
    assert app.parse_args().no_prewarm is False
    seen: dict = {}
    monkeypatch.setattr(app, "run_server", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sys, "argv", ["web.board.app", "--no-prewarm", "--port", "0"])
    assert app.main() == 0 and seen["prewarm"] is False


# ---------------------------------------------------------------------------
# item 5: single implementation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol, owner", [
    ("_FILE_CACHE", "tools/aipos_cli/frontmatter.py"),
    ("def read_json_cached", "tools/aipos_cli/frontmatter.py"),
    ("def _file_cache_get", "tools/aipos_cli/frontmatter.py"),
    ("def _file_cache_put", "tools/aipos_cli/frontmatter.py"),
    ("def request_scope_active", "tools/aipos_cli/task_loader.py"),
    ("def prewarm_read_caches", "web/board/app.py"),
])
def test_item5_single_implementation(symbol, owner):
    assert _code_hits(symbol) == [owner], symbol
