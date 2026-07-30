"""AIPOS-283 FIX-1: Task center ordering contract — latest activity first (descending).

Owner 网页打回 verify_AIPOS-283_20260730T163735: "任务里不管是已发布还是正在执行还是
已闭环,都以最近一次的任务往下排序,现在是最下面才是最近的任务,其他没问题了"。

断言:
S1 — HTTP 契约: 混合新旧任务 fixture → 响应列表顺序 = 最近活动时间倒序(最新在上)
S2 — 三态混排: 已发布/执行中/已闭环任务混在一起时，顺序仍按活动时间倒序，不按状态分组
S3 — 零回归: 原有 tasks/closure_units/activity_feed 字段全保留
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.owner_truth_view import build_owner_truth_view


class TaskCenterOrderingContractTests(unittest.TestCase):
    """AIPOS-283 FIX-1: Task center list ordering — latest activity first."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for folder in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / folder).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "publishes").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "claims").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "returns").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "project.json").write_text('{"project": "lybra"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_task_with_timeline(
        self,
        task_id: str,
        title: str,
        queue_state: str,
        publish_time: str | None = None,
        claim_time: str | None = None,
        return_time: str | None = None,
    ) -> None:
        """Helper: create a task card + records to drive its timeline."""
        folder = queue_state
        card_path = self.repo_root / "5_tasks" / "queue" / folder / f"{task_id.lower()}.md"
        card_path.write_text(
            f"---\n"
            f"task_id: {task_id}\n"
            f"title: {title}\n"
            f"project: lybra\n"
            f"status: {queue_state}\n"
            f"assigned_to: exec.test\n"
            f"agent_instance: exec.test\n"
            f"context_bundle: exec.test\n"
            f"task_mode: code\n"
            f"priority: high\n"
            f"created_by: advisor.test\n"
            f"needs_owner: false\n"
            f"output_target: tools/\n"
            f"artifact_policy: formal_write\n"
            f"---\n"
            f"# {title}\n",
            encoding="utf-8",
        )
        if publish_time:
            pub_dir = self.repo_root / "5_tasks" / "records" / "publishes" / task_id
            pub_dir.mkdir(parents=True, exist_ok=True)
            pub_rec = pub_dir / f"publish_{task_id.lower()}.md"
            pub_rec.write_text(
                f"---\n"
                f"record_type: publish\n"
                f"task_id: {task_id}\n"
                f"record_id: publish_{task_id}\n"
                f"published_at: '{publish_time}'\n"
                f"published_by: advisor.test\n"
                f"---\n"
                f"# Published {task_id}\n",
                encoding="utf-8",
            )
        if claim_time:
            claim_dir = self.repo_root / "5_tasks" / "records" / "claims" / task_id
            claim_dir.mkdir(parents=True, exist_ok=True)
            claim_rec = claim_dir / f"claim_{task_id.lower()}.md"
            claim_rec.write_text(
                f"---\n"
                f"record_type: claim\n"
                f"task_id: {task_id}\n"
                f"record_id: claim_{task_id}\n"
                f"claimed_at: '{claim_time}'\n"
                f"claimed_by: exec.test\n"
                f"---\n"
                f"# Claimed {task_id}\n",
                encoding="utf-8",
            )
        if return_time:
            (self.repo_root / "5_tasks" / "records" / "returns" / task_id).mkdir(exist_ok=True)
            ret_rec = self.repo_root / "5_tasks" / "records" / "returns" / task_id / f"return_{task_id}.md"
            ret_rec.write_text(
                f"---\n"
                f"record_type: return\n"
                f"task_id: {task_id}\n"
                f"record_id: return_{task_id}\n"
                f"returned_at: '{return_time}'\n"
                f"returned_by: exec.test\n"
                f"---\n"
                f"# Returned {task_id}\n",
                encoding="utf-8",
            )

    def test_s1_latest_activity_first_mixed_timeline(self) -> None:
        """S1: HTTP contract — task list ordered by latest activity descending (newest on top)."""
        # Create tasks with different latest activity times:
        # OLD-001: publish only, oldest
        # MID-002: publish + claim, middle
        # NEW-003: publish + claim + return, newest
        self._create_task_with_timeline(
            "OLD-001",
            "Oldest task",
            "pending",
            publish_time="2026-07-01T10:00:00Z",
        )
        self._create_task_with_timeline(
            "MID-002",
            "Middle task",
            "claimed",
            publish_time="2026-07-15T10:00:00Z",
            claim_time="2026-07-20T14:00:00Z",
        )
        self._create_task_with_timeline(
            "NEW-003",
            "Newest task",
            "completed",
            publish_time="2026-07-25T10:00:00Z",
            claim_time="2026-07-26T10:00:00Z",
            return_time="2026-07-30T16:00:00Z",
        )

        response = build_owner_truth_view(repo_root=self.repo_root)
        self.assertTrue(response["ok"], f"build_owner_truth_view failed: {response}")

        tasks = response["data"]["tasks"]
        task_ids = [t["task_id"] for t in tasks]

        # Expected order: NEW-003 (2026-07-30) > MID-002 (2026-07-20) > OLD-001 (2026-07-01)
        self.assertEqual(
            task_ids,
            ["NEW-003", "MID-002", "OLD-001"],
            "S1: Tasks must be ordered by latest activity time descending (newest first)",
        )

    def test_s2_mixed_states_ordered_by_activity_not_stage(self) -> None:
        """S2: Mixed 已发布/执行中/已闭环 tasks ordered by activity time, not stage grouping."""
        # Create tasks in different stages but with interleaved activity times:
        # EXEC-A (executing): latest activity 2026-07-29
        # CLOSED-B (closed): latest activity 2026-07-28
        # PUB-C (published): latest activity 2026-07-30 (most recent!)
        self._create_task_with_timeline(
            "EXEC-A",
            "Executing task",
            "claimed",
            publish_time="2026-07-20T10:00:00Z",
            claim_time="2026-07-29T15:00:00Z",
        )
        self._create_task_with_timeline(
            "CLOSED-B",
            "Closed task",
            "completed",
            publish_time="2026-07-10T10:00:00Z",
            claim_time="2026-07-11T10:00:00Z",
            return_time="2026-07-28T12:00:00Z",
        )
        self._create_task_with_timeline(
            "PUB-C",
            "Recently published",
            "pending",
            publish_time="2026-07-30T17:00:00Z",
        )

        response = build_owner_truth_view(repo_root=self.repo_root)
        self.assertTrue(response["ok"])

        tasks = response["data"]["tasks"]
        task_ids = [t["task_id"] for t in tasks]

        # Order should be PUB-C (2026-07-30) > EXEC-A (2026-07-29) > CLOSED-B (2026-07-28)
        # NOT grouped by stage (published/executing/closed)
        self.assertEqual(
            task_ids,
            ["PUB-C", "EXEC-A", "CLOSED-B"],
            "S2: Tasks must be ordered by activity time regardless of stage (no stage grouping)",
        )

    def test_s3_stable_sort_same_timestamp(self) -> None:
        """S3: Tasks with identical latest activity time ordered by task_id descending (stable)."""
        # Create tasks with same claim time
        self._create_task_with_timeline(
            "AAA-001",
            "Task AAA",
            "claimed",
            publish_time="2026-07-20T10:00:00Z",
            claim_time="2026-07-30T12:00:00Z",
        )
        self._create_task_with_timeline(
            "BBB-002",
            "Task BBB",
            "claimed",
            publish_time="2026-07-20T10:00:00Z",
            claim_time="2026-07-30T12:00:00Z",
        )
        self._create_task_with_timeline(
            "CCC-003",
            "Task CCC",
            "claimed",
            publish_time="2026-07-20T10:00:00Z",
            claim_time="2026-07-30T12:00:00Z",
        )

        response = build_owner_truth_view(repo_root=self.repo_root)
        self.assertTrue(response["ok"])

        tasks = response["data"]["tasks"]
        task_ids = [t["task_id"] for t in tasks]

        # Same timestamp: order by task_id descending (CCC > BBB > AAA)
        self.assertEqual(
            task_ids,
            ["CCC-003", "BBB-002", "AAA-001"],
            "S3: Same activity time → stable sort by task_id descending",
        )

    def test_zero_regression_response_structure(self) -> None:
        """Zero regression: response structure unchanged (tasks/closure_units/activity_feed)."""
        self._create_task_with_timeline(
            "TEST-REG",
            "Regression check",
            "pending",
            publish_time="2026-07-30T10:00:00Z",
        )

        response = build_owner_truth_view(repo_root=self.repo_root)
        self.assertTrue(response["ok"])
        self.assertIn("data", response)
        data = response["data"]

        # Core keys must exist
        self.assertIn("tasks", data)
        self.assertIn("closure_units", data)
        self.assertIn("activity_feed", data)
        self.assertIn("stage_counts", data)
        self.assertIn("top_level_counts", data)
        self.assertIn("summary", data)

        # Task row keys (contract)
        tasks = data["tasks"]
        self.assertGreater(len(tasks), 0)
        task = tasks[0]
        for key in ("task_id", "title", "purpose", "queue_state", "true_stage", "timeline"):
            self.assertIn(key, task, f"Task row must include key: {key}")


if __name__ == "__main__":
    unittest.main()
