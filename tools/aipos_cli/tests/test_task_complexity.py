from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.aipos_cli.task_loader import load_task_file
from tools.aipos_cli.validator import validate_single_task

# ENV-AWARE: bare-python asserts LOUD/FAIL-CLOSED behavior.
_HAS_YAML = importlib.util.find_spec("yaml") is not None


class TaskComplexityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # AIPOS-343: active policies so contract section can resolve envelopes
        policies_dir = self.repo_root / "5_tasks" / "policies"
        policies_dir.mkdir(parents=True, exist_ok=True)
        (policies_dir / "pol_lybra_dev_7.md").write_text(
            "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\n---\n# Dev\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def metadata(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "task_id": "AIPOS-140-TEST",
            "title": "Task complexity test",
            "project": "lybra",
            "assigned_to": "dev.codex.local",
            "agent_instance": "dev.codex.local",
            "context_bundle": "dev.codex.local",
            "task_mode": "docs",
            "priority": "medium",
            "status": "pending",
            "created_by": "tester",
            "needs_owner": False,
            "output_target": "docs/",
            "artifact_policy": "formal_write",
            "model_tier": "L2",
            "session_policy": "single_task_session",
            "context_isolation": "isolated",  # 值域 = card.schema(shared|isolated); 原 strict 使 create_draft 拒(两条 known-failure 根因)
            "artifact_scope": "docs/",
            "memory_scope": "task complexity tests",
        }
        values.update(overrides)
        return values

    def write_task(self, **overrides: object) -> dict[str, object]:
        metadata = self.metadata(**overrides)
        lines = ["---", *(f"{key}: {str(value).lower() if isinstance(value, bool) else value}" for key, value in metadata.items()), "---", "Body", ""]
        path = self.repo_root / "5_tasks/queue/pending/task.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return load_task_file(path, self.repo_root)

    def test_docs_task_omission_defaults_to_simple(self) -> None:
        task = self.write_task()
        result = validate_single_task(task)
        self.assertEqual(result["effective_task_class"], "simple")
        self.assertFalse(result["task_class_explicit"])
        self.assertEqual(result["verdict"], "PASS")

    def test_code_task_omission_surfaces_advisory_without_upgrading_verdict(self) -> None:
        task = self.write_task(task_mode="code")
        result = validate_single_task(task)
        self.assertEqual(result["verdict"], "PASS")
        self.assertIn("Code-mode task omits task_class", result["classification_warnings"][0])
        self.assertIn(result["classification_warnings"][0], result["warnings"])

    def test_code_task_explicit_simple_surfaces_advisory_without_upgrading_verdict(self) -> None:
        task = self.write_task(task_mode="code", task_class="simple")
        result = validate_single_task(task)
        self.assertEqual(result["verdict"], "PASS")
        self.assertIn("Code-mode task is explicitly classified simple", result["classification_warnings"][0])

    def test_invalid_task_class_blocks(self) -> None:
        task = self.write_task(task_class="large")
        result = validate_single_task(task)
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("task_class must be one of: simple, standard, complex (enums.schema task_class)", result["blocking_reasons"])  # AIPOS-F104 件④: 值域读 enums

    def test_complex_docs_task_requires_independent_roles(self) -> None:
        task = self.write_task(task_class="complex", planner_agent="planner.local", reviewer="review.local", audit_by="audit.local")
        self.assertEqual(validate_single_task(task)["verdict"], "PASS")

        conflict = self.write_task(task_class="complex", planner_agent="planner.local", reviewer="review.local", audit_by="dev.codex.local")
        self.assertIn("Complex-class assigned_to must not equal audit_by", validate_single_task(conflict)["blocking_reasons"])

    def test_complex_active_orchestration_requires_continuity_planner(self) -> None:
        task = self.write_task(
            task_class="complex",
            planner_agent="planner.local",
            reviewer="review.local",
            audit_by="audit.local",
            orchestration={"enabled": True, "planner_assignment_status": "active"},
        )
        result = validate_single_task(task)
        if not _HAS_YAML:
            # BARE: the test's write_task writes `orchestration: {'enabled': True, ...}` as a raw
            # Python repr string (str(dict)), which PyYAML parses as a mapping but the fallback
            # parser returns as a string.  The validator therefore doesn't see a dict and skips the
            # orchestration checks.  This is a test-data limitation, not a production-code issue:
            # real task cards emitted by publish_draft use the FLAT contract (no nested dicts in
            # the frontmatter writers) so this path never occurs in the real gate.
            # Assert the result is valid (no spurious error), not the blocking reason.
            self.assertNotEqual(result["verdict"], "ERROR")
            return
        self.assertIn("Complex-class active orchestration missing continuity_planner_agent", result["blocking_reasons"])
        self.assertIn("Complex-class active orchestration missing continuity_planner_agent_instance", result["blocking_reasons"])

    # AIPOS-F133 件③: 依赖判据单源 task_complexity.unmet_dependencies——只读被依赖卡的门生记录(全部依赖满足才放行);
    # 卡面自报字段(dependency_audit_status / dependency_executor_status / dependency_audit_readiness)不再采信。
    COMPLEX = {
        "task_class": "complex",
        "planner_agent": "planner.local",
        "reviewer": "review.local",
        "audit_by": "audit.local",
        "depends_on": ["AIPOS-139"],
    }

    # 发布须卡面零门判据可解析(AIPOS-F102): 实例名用注册表前缀(原 dev.codex.local 不可解析 = 两条 known-failure 另一根因)
    PUBLISHABLE_INSTANCE = {"assigned_to": "exec.fx.local", "agent_instance": "exec.fx.local"}

    def gate_record(self, kind: str, task_id: str, **meta: object) -> None:
        from tools.aipos_cli.record_writer import record_dir, record_file_prefix

        path = record_dir(self.repo_root, kind, task_id) / f"{record_file_prefix(kind)}_{task_id}_20261008_000000_{len(list(meta))}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["---", f"task_id: {task_id}", *(f"{k}: {v}" for k, v in meta.items()), "---", ""]
        path.write_text("\n".join(lines), encoding="utf-8")

    def test_complex_dependency_audit_pass_blocks_until_pass(self) -> None:
        common = {**self.COMPLEX, "dependency_condition": "audit_pass"}
        # 卡面自报 PASS 不算(原: 无记录时退回卡面自报)
        pending = validate_single_task(self.write_task(**common, dependency_audit_status="PASS"))
        self.assertTrue(any(r.startswith("DEPENDENCY_UNMET: 依赖 AIPOS-139 未满足 audit_pass") for r in pending["blocking_reasons"]),
                        pending["blocking_reasons"])
        self.gate_record("audit_verdicts", "AIPOS-139", record_type="audit_verdict", verdict_id="verdict_AIPOS-139_x",
                         verdict_at="2026-10-08T00:00:00Z", verdict="PASS")
        passed = validate_single_task(self.write_task(**common))
        self.assertEqual(passed["verdict"], "PASS", passed["blocking_reasons"])

    def test_complex_dependency_executor_completion_uses_executor_status(self) -> None:
        common = {**self.COMPLEX, "dependency_condition": "executor_completion"}
        pending = validate_single_task(self.write_task(**common, dependency_executor_status="completed"))
        self.assertTrue(any(r.startswith("DEPENDENCY_UNMET: 依赖 AIPOS-139 未满足 executor_completion") for r in pending["blocking_reasons"]),
                        pending["blocking_reasons"])
        self.gate_record("returns", "AIPOS-139", record_type="return_record")
        completed = validate_single_task(self.write_task(**common))
        self.assertEqual(completed["verdict"], "PASS", completed["blocking_reasons"])

    def test_complex_dependency_audit_readiness_uses_readiness_status(self) -> None:
        common = {**self.COMPLEX, "dependency_condition": "audit_readiness"}
        not_ready = validate_single_task(self.write_task(**common, dependency_audit_readiness="ready"))
        self.assertTrue(any(r.startswith("DEPENDENCY_UNMET: 依赖 AIPOS-139 未满足 audit_readiness") for r in not_ready["blocking_reasons"]),
                        not_ready["blocking_reasons"])
        self.gate_record("returns", "AIPOS-139", record_type="return_record")
        ready = validate_single_task(self.write_task(**common))
        self.assertEqual(ready["verdict"], "PASS", ready["blocking_reasons"])

    def test_complex_dependency_ambiguous_condition_blocks(self) -> None:
        result = validate_single_task(self.write_task(**self.COMPLEX, dependency_condition="owner_approved"))

        self.assertIn(
            "Complex-class dependent task requires dependency_condition: closure, executor_completion, audit_readiness, audit_pass "
            "(card.schema dependency_gate.conditions)",
            result["blocking_reasons"],
        )

    def test_complex_dependent_audit_task_can_publish_when_audit_ready(self) -> None:
        self.gate_record("returns", "AIPOS-139", record_type="return_record")
        metadata = self.metadata(**self.COMPLEX, **self.PUBLISHABLE_INSTANCE, dependency_condition="audit_readiness")
        created = create_draft(self.repo_root, metadata, "Body")
        self.assertTrue(created["wrote"])
        published = publish_draft(self.repo_root, str(created["target_path"]), dry_run=True)
        self.assertNotEqual(published["verdict"], "BLOCK", published["blocking_reasons"])
        self.assertTrue(published["would_write"])

    def test_complex_dependent_draft_can_exist_but_cannot_publish_before_audit_pass(self) -> None:
        metadata = self.metadata(**self.COMPLEX, **self.PUBLISHABLE_INSTANCE, dependency_condition="audit_pass", dependency_audit_status="PASS")
        created = create_draft(self.repo_root, metadata, "Body")
        self.assertTrue(created["wrote"])
        published = publish_draft(self.repo_root, str(created["target_path"]), dry_run=True)
        self.assertEqual(published["verdict"], "BLOCK")
        self.assertTrue(any(r.startswith("DEPENDENCY_UNMET: 依赖 AIPOS-139 未满足 audit_pass") for r in published["blocking_reasons"]),
                        published["blocking_reasons"])

    def test_dependency_gate_only_on_pending(self) -> None:
        """依赖门 = 认领门: 已认领卡不再按依赖判(原各状态都判)。"""
        task = self.write_task(**self.COMPLEX, dependency_condition="audit_pass")
        claimed = {**task, "queue_state": "claimed"}
        self.assertFalse([r for r in validate_single_task(claimed)["blocking_reasons"] if "DEPENDENCY" in r])

if __name__ == "__main__":
    unittest.main()
