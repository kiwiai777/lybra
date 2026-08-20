"""AIPOS-F2: 裁决存在性单源——终态锁与真相选取共用同一份门生特征声明。

死锁五号修复:手写文件不构成终态,终态锁与选取同源。
验收:
  ① 手写 PASS 文件在场时,正规 audit_verdict 提交不被拒
  ② 手写文件在场时 close/finalize/依赖/派审各判定与无该文件时行为一致
  ③ 单测覆盖手写+门生并存场景
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any


def _make_gate_born_verdict(directory: Path, task_id: str, verdict: str = "PASS", timestamp: str = "2026-08-20T07:00:00Z") -> Path:
    """写一个门生裁决文件。"""
    filename = f"verdict_{task_id}_{timestamp.replace(':', '')}_audit.lybra.md"
    path = directory / filename
    path.write_text(f"""---
record_type: audit_verdict_record
verdict_id: verdict_{task_id}_{timestamp.replace(':', '')}_audit.lybra
verdict: {verdict}
reviewed_task_id: {task_id}
auditor_instance: audit.lybra.kiwiai-dev
verdict_at: '{timestamp}'
---
# Gate-born verdict
""")
    return path


def _make_hand_written_verdict(directory: Path, task_id: str, verdict: str = "PASS") -> Path:
    """写一个手写裁决文件(缺门生标记)。"""
    path = directory / "handwritten_pass.md"
    path.write_text(f"""---
verdict: {verdict}
reviewed_task_id: {task_id}
auditor_instance: someone
---
# Hand-written verdict (no gate markers)
""")
    return path


class TestIsGateBornVerdictMetadata(unittest.TestCase):
    """is_gate_born_verdict_metadata 单源判定。"""

    def test_gate_born_returns_true(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        metadata = {
            "record_type": "audit_verdict_record",
            "verdict_id": "verdict_AIPOS-123_20260820",
            "verdict_at": "2026-08-20T07:00:00Z",
        }
        self.assertTrue(is_gate_born_verdict_metadata(metadata))

    def test_missing_record_type_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        metadata = {
            "verdict_id": "verdict_AIPOS-123_20260820",
            "verdict_at": "2026-08-20T07:00:00Z",
        }
        self.assertFalse(is_gate_born_verdict_metadata(metadata))

    def test_wrong_record_type_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        metadata = {
            "record_type": "session",
            "verdict_id": "verdict_AIPOS-123_20260820",
            "verdict_at": "2026-08-20T07:00:00Z",
        }
        self.assertFalse(is_gate_born_verdict_metadata(metadata))

    def test_missing_verdict_id_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        metadata = {
            "record_type": "audit_verdict_record",
            "verdict_at": "2026-08-20T07:00:00Z",
        }
        self.assertFalse(is_gate_born_verdict_metadata(metadata))

    def test_verdict_id_wrong_prefix_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        metadata = {
            "record_type": "audit_verdict_record",
            "verdict_id": "not_verdict_prefix",
            "verdict_at": "2026-08-20T07:00:00Z",
        }
        self.assertFalse(is_gate_born_verdict_metadata(metadata))

    def test_missing_verdict_at_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        metadata = {
            "record_type": "audit_verdict_record",
            "verdict_id": "verdict_AIPOS-123_20260820",
        }
        self.assertFalse(is_gate_born_verdict_metadata(metadata))

    def test_empty_dict_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        self.assertFalse(is_gate_born_verdict_metadata({}))

    def test_none_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        self.assertFalse(is_gate_born_verdict_metadata(None))

    def test_timestamp_fallback_for_verdict_at(self):
        """verdict_at 缺失但 timestamp 存在时也应通过。"""
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
        metadata = {
            "record_type": "audit_verdict_record",
            "verdict_id": "verdict_AIPOS-123_20260820",
            "timestamp": "2026-08-20T07:00:00Z",
        }
        self.assertTrue(is_gate_born_verdict_metadata(metadata))


class TestIsGateBornVerdictRecord(unittest.TestCase):
    """is_gate_born_verdict_record — load_records 返回的 record dict。"""

    def test_top_level_gate_born(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_record
        record = {
            "record_type": "audit_verdict",
            "verdict_id": "verdict_test",
            "verdict_at": "2026-08-20T07:00:00Z",
        }
        self.assertTrue(is_gate_born_verdict_record(record))

    def test_nested_metadata_gate_born(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_record
        record = {
            "metadata": {
                "record_type": "audit_verdict_record",
                "verdict_id": "verdict_test",
                "verdict_at": "2026-08-20T07:00:00Z",
            }
        }
        self.assertTrue(is_gate_born_verdict_record(record))

    def test_hand_written_returns_false(self):
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_record
        record = {"verdict": "PASS"}
        self.assertFalse(is_gate_born_verdict_record(record))


class TestRecordsFiltering(unittest.TestCase):
    """records.load_records() 过滤手写裁决。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_only_gate_born_in_task_audit_verdicts(self):
        """手写文件不在 task_audit_verdicts 中。"""
        from tools.aipos_cli.records import load_records

        verdicts_dir = self.tmpdir / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST-1"
        verdicts_dir.mkdir(parents=True)
        _make_gate_born_verdict(verdicts_dir, "AIPOS-TEST-1")
        _make_hand_written_verdict(verdicts_dir, "AIPOS-TEST-1")

        records = load_records(self.tmpdir)
        verdicts = records.get("task_audit_verdicts", {}).get("AIPOS-TEST-1", [])
        self.assertEqual(len(verdicts), 1, "Only gate-born verdict should be in index")

    def test_hand_written_warnings_populated(self):
        """手写文件触发 hand_written_verdict_warnings。"""
        from tools.aipos_cli.records import load_records

        verdicts_dir = self.tmpdir / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST-2"
        verdicts_dir.mkdir(parents=True)
        _make_hand_written_verdict(verdicts_dir, "AIPOS-TEST-2")

        records = load_records(self.tmpdir)
        hw_warnings = records.get("hand_written_verdict_warnings", [])
        self.assertEqual(len(hw_warnings), 1)
        self.assertIn("hand-written verdict ignored", hw_warnings[0])

    def test_no_verdicts_no_warnings(self):
        """空目录无警告。"""
        from tools.aipos_cli.records import load_records

        records = load_records(self.tmpdir)
        hw_warnings = records.get("hand_written_verdict_warnings", [])
        self.assertEqual(len(hw_warnings), 0)


class TestQueueMutationGateBorn(unittest.TestCase):
    """queue_mutation._check_for_pass_audit_verdict 只认门生。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_hand_written_only_returns_false(self):
        """只有手写 PASS → 返回 False(不构成终态)。"""
        from tools.aipos_cli.queue_mutation import _check_for_pass_audit_verdict

        verdicts_dir = self.tmpdir / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST-3"
        verdicts_dir.mkdir(parents=True)
        _make_hand_written_verdict(verdicts_dir, "AIPOS-TEST-3", "PASS")

        result = _check_for_pass_audit_verdict(self.tmpdir, "AIPOS-TEST-3")
        self.assertFalse(result, "Hand-written PASS should not count")

    def test_gate_born_pass_returns_true(self):
        """门生 PASS → 返回 True。"""
        from tools.aipos_cli.queue_mutation import _check_for_pass_audit_verdict

        verdicts_dir = self.tmpdir / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST-4"
        verdicts_dir.mkdir(parents=True)
        _make_gate_born_verdict(verdicts_dir, "AIPOS-TEST-4", "PASS")

        result = _check_for_pass_audit_verdict(self.tmpdir, "AIPOS-TEST-4")
        self.assertTrue(result)

    def test_coexistence_gate_born_wins(self):
        """手写+门生并存时,只看门生。"""
        from tools.aipos_cli.queue_mutation import _check_for_pass_audit_verdict

        verdicts_dir = self.tmpdir / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST-5"
        verdicts_dir.mkdir(parents=True)
        _make_hand_written_verdict(verdicts_dir, "AIPOS-TEST-5", "PASS")
        _make_gate_born_verdict(verdicts_dir, "AIPOS-TEST-5", "PASS")

        result = _check_for_pass_audit_verdict(self.tmpdir, "AIPOS-TEST-5")
        self.assertTrue(result, "Gate-born PASS should be found despite hand-written presence")

    def test_hand_written_pass_gate_born_fail(self):
        """手写 PASS + 门生 FAIL → 返回 False(最新门生是 FAIL)。"""
        from tools.aipos_cli.queue_mutation import _check_for_pass_audit_verdict

        verdicts_dir = self.tmpdir / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST-6"
        verdicts_dir.mkdir(parents=True)
        _make_hand_written_verdict(verdicts_dir, "AIPOS-TEST-6", "PASS")
        _make_gate_born_verdict(verdicts_dir, "AIPOS-TEST-6", "FAIL", "2026-08-20T08:00:00Z")

        result = _check_for_pass_audit_verdict(self.tmpdir, "AIPOS-TEST-6")
        self.assertFalse(result, "Latest gate-born is FAIL, should return False")


class TestDetectHandWrittenVerdicts(unittest.TestCase):
    """detect_hand_written_verdicts — 立墙带路。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_detects_hand_written(self):
        from tools.aipos_cli.audit_helpers import detect_hand_written_verdicts

        verdicts_dir = self.tmpdir / "verdicts"
        verdicts_dir.mkdir()
        _make_hand_written_verdict(verdicts_dir, "AIPOS-TEST")
        _make_gate_born_verdict(verdicts_dir, "AIPOS-TEST")

        rejected = detect_hand_written_verdicts(verdicts_dir)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["file"], "handwritten_pass.md")

    def test_no_hand_written_returns_empty(self):
        from tools.aipos_cli.audit_helpers import detect_hand_written_verdicts

        verdicts_dir = self.tmpdir / "verdicts"
        verdicts_dir.mkdir()
        _make_gate_born_verdict(verdicts_dir, "AIPOS-TEST")

        rejected = detect_hand_written_verdicts(verdicts_dir)
        self.assertEqual(len(rejected), 0)

    def test_nonexistent_dir_returns_empty(self):
        from tools.aipos_cli.audit_helpers import detect_hand_written_verdicts

        rejected = detect_hand_written_verdicts(self.tmpdir / "nonexistent")
        self.assertEqual(len(rejected), 0)


class TestDeploymentAuthorizationConsistency(unittest.TestCase):
    """deployment_authorization.check_verdict_record_authentic 与共享函数一致。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_gate_born_authentic(self):
        from tools.aipos_cli.deployment_authorization import check_verdict_record_authentic

        verdicts_dir = self.tmpdir / "verdicts"
        verdicts_dir.mkdir()
        path = _make_gate_born_verdict(verdicts_dir, "AIPOS-TEST")

        result = check_verdict_record_authentic(path)
        self.assertTrue(result["authentic"])

    def test_hand_written_not_authentic(self):
        from tools.aipos_cli.deployment_authorization import check_verdict_record_authentic

        verdicts_dir = self.tmpdir / "verdicts"
        verdicts_dir.mkdir()
        path = _make_hand_written_verdict(verdicts_dir, "AIPOS-TEST")

        result = check_verdict_record_authentic(path)
        self.assertFalse(result["authentic"])


if __name__ == "__main__":
    unittest.main()
