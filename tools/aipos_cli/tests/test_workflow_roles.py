from __future__ import annotations

import unittest
from pathlib import Path

from tools.aipos_cli.task_complexity import (
    ONE_ROLE,
    TWO_ROLE,
    complexity_payload,
    suggest_workflow_roles,
    validate_task_complexity,
)


class SuggestWorkflowRolesTests(unittest.TestCase):
    """AIPOS-232 §6 verify #2 + R-3: the suggestion RECOMMENDS, never SELECTS."""

    def test_complex_recommends_two_role(self) -> None:
        result = suggest_workflow_roles({"task_class": "complex"})
        self.assertEqual(result["suggested_workflow"], TWO_ROLE)
        self.assertEqual(result["suggested_role_count"], 2)

    def test_simple_recommends_one_role(self) -> None:
        result = suggest_workflow_roles({"task_class": "simple"})
        self.assertEqual(result["suggested_workflow"], ONE_ROLE)
        self.assertEqual(result["suggested_role_count"], 1)

    def test_omitted_task_class_defaults_to_one_role(self) -> None:
        # effective_task_class defaults to "simple" -> 1-role suggestion.
        result = suggest_workflow_roles({})
        self.assertEqual(result["suggested_workflow"], ONE_ROLE)
        self.assertEqual(result["suggested_role_count"], 1)

    def test_never_auto_selects(self) -> None:
        # R-3: no auto-select across every class; advisory honesty flag set.
        for task_class in ("simple", "complex", None, "anything"):
            result = suggest_workflow_roles({"task_class": task_class})
            self.assertFalse(result["auto_selected"])
            self.assertTrue(result["suggestion_is_heuristic"])
            self.assertEqual(result["suggestion_basis"], "task_class")

    def test_returns_no_card_assignment(self) -> None:
        # The hint must not emit role-assignment fields (those are the Owner's to set).
        result = suggest_workflow_roles({"task_class": "complex"})
        self.assertNotIn("assigned_to", result)
        self.assertNotIn("audit_by", result)
        # Every emitted key is an advisory/suggestion field, not an assignment.
        for key in result:
            self.assertTrue(
                key.startswith("suggest") or key in {"auto_selected"},
                f"unexpected non-advisory key in suggestion output: {key}",
            )

    def test_is_pure_no_side_effect(self) -> None:
        # Pure function: same input -> identical output, input dict untouched.
        metadata = {"task_class": "complex"}
        first = suggest_workflow_roles(metadata)
        second = suggest_workflow_roles(metadata)
        self.assertEqual(first, second)
        self.assertEqual(metadata, {"task_class": "complex"})  # no mutation


class ComplexityPayloadSuggestionTests(unittest.TestCase):
    """The advisory suggestion rides on the card read-only; it is never applied."""

    def test_payload_carries_advisory_suggestion(self) -> None:
        payload = complexity_payload({"task_class": "complex"})
        self.assertIn("workflow_suggestion", payload)
        self.assertEqual(
            payload["workflow_suggestion"],
            suggest_workflow_roles({"task_class": "complex"}),
        )

    def test_payload_does_not_inject_assignment(self) -> None:
        payload = complexity_payload({"task_class": "complex"})
        # Read-only advisory: payload must not carry executor/auditor assignment.
        self.assertNotIn("assigned_to", payload)
        self.assertNotIn("audit_by", payload)
        self.assertFalse(payload["workflow_suggestion"]["auto_selected"])


class ComplexCannotBeSilentlyOneRoleTests(unittest.TestCase):
    """AIPOS-232 §6 verify #3 + R-2: complex structurally requires an auditor."""

    def _validate(self, metadata: dict[str, object]) -> dict[str, list[str]]:
        return validate_task_complexity(metadata, enforce_dependency_gate=False)

    def test_complex_without_audit_by_blocks(self) -> None:
        # Dropping the auditor leg from a complex task fails validation -> a
        # complex task can NOT be silently turned into 1-role.
        result = self._validate(
            {
                "task_class": "complex",
                "planner_agent": "planner.local",
                "reviewer": "review.local",
                # audit_by deliberately omitted
            }
        )
        self.assertIn("Complex-class task missing audit_by", result["blocking_reasons"])

    def test_complex_with_distinct_auditor_passes_role_gate(self) -> None:
        # Positive: a properly staffed 2-role complex card has no role blocking.
        result = self._validate(
            {
                "task_class": "complex",
                "assigned_to": "dev.codex.local",
                "planner_agent": "planner.local",
                "reviewer": "review.local",
                "audit_by": "audit.local",
            }
        )
        self.assertEqual(result["blocking_reasons"], [])


class RoleIndependenceLayerIsClassScopedTests(unittest.TestCase):
    """AIPOS-232 R-1 seam: the role-level (declaration) independence rule fires
    ONLY for complex-class. Therefore 2-role's guarantee for ANY class must rest
    on the UNIVERSAL instance-level backstop (board_adapter.py:2143/2150, run on
    every audit_verdict regardless of class) — covered by test_mcp_tools.py's
    INDEPENDENCE_FAILED / INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY tests.
    """

    def _validate(self, metadata: dict[str, object]) -> dict[str, list[str]]:
        return validate_task_complexity(metadata, enforce_dependency_gate=False)

    def test_role_level_rule_does_not_fire_for_non_complex(self) -> None:
        # A simple-class card declaring the SAME identity for executor and auditor
        # is NOT blocked by the role-level layer — proving that layer is class-
        # scoped and cannot be the universal guarantee for a non-complex 2-role.
        result = self._validate(
            {
                "task_class": "simple",
                "assigned_to": "same.local",
                "audit_by": "same.local",
            }
        )
        self.assertNotIn(
            "Complex-class assigned_to must not equal audit_by",
            result["blocking_reasons"],
        )

    def test_role_level_rule_fires_for_complex(self) -> None:
        result = self._validate(
            {
                "task_class": "complex",
                "assigned_to": "same.local",
                "planner_agent": "planner.local",
                "reviewer": "review.local",
                "audit_by": "same.local",
            }
        )
        self.assertIn(
            "Complex-class assigned_to must not equal audit_by",
            result["blocking_reasons"],
        )


class GateNotEngineTests(unittest.TestCase):
    """AIPOS-232 §3/§6 verify #4: the workflow change introduces NO runtime.

    Positive-absence assertion against the actual product source touched by this
    slice — not a wish. If a later edit smuggles a scheduler/loop/daemon in, this
    fails loudly.
    """

    _FORBIDDEN = (
        "scheduler",
        "poller",
        "polling",
        "while True",
        "daemon",
        "Timer",
        "asyncio",
        "heartbeat",
        "threading",
    )

    def test_task_complexity_has_no_runtime_primitive(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "task_complexity.py"
        ).read_text(encoding="utf-8")
        for token in self._FORBIDDEN:
            self.assertNotIn(
                token,
                source,
                f"gate-not-engine violated: '{token}' appeared in task_complexity.py",
            )


if __name__ == "__main__":
    unittest.main()
