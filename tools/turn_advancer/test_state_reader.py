#!/usr/bin/env python3
"""AIPOS-340F4 — state_reader 三来源事件读取测试。

夹具 = 真实事件文件**字节拷贝**(禁手造格式),各取一来源代表样本:
- task_progress_event (frontmatter event_type): fixtures/events/AIPOS-339/started_*.md
- launch_check_event  (frontmatter event_kind): fixtures/events/AIPOS-339/launch_failed_*.md
- audit_event 守护    (frontmatter event_kind): fixtures/events/AIPOS-315R/audit_incomplete_*.md

修复前:state_reader 只读 event_type → launch_check/audit 的 event_kind 事件 type=None 被吞,
rules 看不到 launch_failed → 误判 wait_human 而非 resume(F3R FAIL)。
"""
import shutil
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tools.turn_advancer.state_reader import read_task_state, _normalize_event_type
from tools.turn_advancer.rules import _classify_claimed_no_return_events

FIX = Path(__file__).parent / "fixtures" / "events"


def _build_ws(tmp: Path) -> Path:
    """最小工作区:仅含 5_tasks/records/events/ 下三来源夹具(自包含,不依赖活态)。"""
    events = tmp / "5_tasks" / "records" / "events"
    events.mkdir(parents=True)
    shutil.copytree(FIX / "AIPOS-339", events / "AIPOS-339")
    shutil.copytree(FIX / "AIPOS-315R", events / "AIPOS-315R")
    return tmp


def test_three_sources_event_types():
    """三来源各自被正确读出统一 type 字段。"""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws = _build_ws(Path(d))
        # AIPOS-339: task_progress(started) + launch_check(launch_failed)
        st = read_task_state(ws, "AIPOS-339")
        types = sorted(e["type"] for e in st["events"] if e["type"])
        assert types == ["launch_failed", "started"], f"AIPOS-339 types={types}"
        lf = next(e for e in st["events"] if e["type"] == "launch_failed")
        assert lf["source"] in ("launch_check", "launch_check_event"), lf
        # AIPOS-315R: audit 守护(audit_incomplete)
        st2 = read_task_state(ws, "AIPOS-315R")
        types2 = [e["type"] for e in st2["events"] if e["type"]]
        assert types2 == ["audit_incomplete"], f"AIPOS-315R types={types2}"
    print("✓ test_three_sources_event_types passed")


def test_launch_failed_feeds_rules_failure():
    """launch_check 的 launch_failed 被 rules 当作失败(修复前 type=None 被吞)。"""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws = _build_ws(Path(d))
        st = read_task_state(ws, "AIPOS-339")
        cls = _classify_claimed_no_return_events(st["events"])
        assert cls["latest_failure"] is not None, "launch_failed 未被 rules 识别为失败(回归)"
        # started 03:00:59 < launch_failed 03:01:17 → failure_after_started=True
        assert cls["failure_after_started"] is True, cls
    print("✓ test_launch_failed_feeds_rules_failure passed")


def test_filename_fallback():
    """frontmatter 缺 event_type/event_kind 时回退文件名前缀(容错,不判活只读事实)。"""
    assert _normalize_event_type({}, "blocked_20260805_030256.md") == "blocked"
    assert _normalize_event_type({"event_kind": "launch_failed"}, "x.md") == "launch_failed"
    assert _normalize_event_type({"event_type": "started"}, "x.md") == "started"
    assert _normalize_event_type({}, "no_ts_here.md") is None
    print("✓ test_filename_fallback passed")


if __name__ == "__main__":
    print("=== AIPOS-340F4 state_reader Tests ===\n")
    failures = 0
    for fn in (test_three_sources_event_types, test_launch_failed_feeds_rules_failure, test_filename_fallback):
        try:
            fn()
        except Exception as e:
            failures += 1
            print(f"❌ {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
    print()
    if failures:
        print(f"❌ {failures} failed")
        sys.exit(1)
    print("✅ All passed")
    sys.exit(0)
