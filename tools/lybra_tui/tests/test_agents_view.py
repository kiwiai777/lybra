from __future__ import annotations

import unittest
from pathlib import Path

from tools.lybra_tui.agents_view import (
    NOT_LIVE_LABEL,
    UNASSIGNED,
    aggregate_agents,
    render_agents,
)


def _task(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {"task_id": "AIPOS-1", "queue_state": "claimed"}
    base.update(kw)
    return base


class FaithfulProjectionTests(unittest.TestCase):
    """AIPOS-234 R-1: every input row appears in EXACTLY one group; counts match; no fabrication."""

    def test_each_task_appears_exactly_once_count_matches(self) -> None:
        tasks = [
            _task(task_id="A", claimed_by="dev.codex.local"),
            _task(task_id="B", claimed_by="dev.codex.local"),
            _task(task_id="C", agent_instance="audit.glm.local"),
            _task(task_id="D"),  # no owner -> unassigned
        ]
        grouped = aggregate_agents(tasks)
        emitted = [e["task_id"] for g in grouped for e in g["tasks"]]
        self.assertCountEqual(emitted, ["A", "B", "C", "D"])
        self.assertEqual(len(emitted), len(tasks))  # no double-count, no drop
        # each id exactly once
        self.assertEqual(len(set(emitted)), len(emitted))

    def test_unassigned_bucket_is_explicit_and_last(self) -> None:
        tasks = [_task(task_id="D"), _task(task_id="A", claimed_by="dev.codex.local")]
        grouped = aggregate_agents(tasks)
        self.assertEqual(grouped[-1]["agent"], UNASSIGNED)
        self.assertEqual([e["task_id"] for e in grouped[-1]["tasks"]], ["D"])


class CanonicalGroupingKeyTests(unittest.TestCase):
    """R-1: group key = owning canonical instance (claimed_by precedence over agent_instance)."""

    def test_claimed_by_takes_precedence_over_agent_instance(self) -> None:
        tasks = [_task(task_id="A", claimed_by="owner.local", agent_instance="other.local")]
        grouped = aggregate_agents(tasks)
        self.assertEqual(grouped[0]["agent"], "owner.local")

    def test_agent_instance_used_when_no_claimed_by(self) -> None:
        tasks = [_task(task_id="A", agent_instance="exec.local")]
        grouped = aggregate_agents(tasks)
        self.assertEqual(grouped[0]["agent"], "exec.local")

    def test_same_owner_collapses_to_one_group(self) -> None:
        tasks = [
            _task(task_id="A", claimed_by="x.local"),
            _task(task_id="B", claimed_by="x.local"),
        ]
        grouped = aggregate_agents(tasks)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(len(grouped[0]["tasks"]), 2)


class DivergenceTests(unittest.TestCase):
    """R-1: assigned_to != owning instance is meaningful truth — shown, never silently collapsed."""

    def test_divergence_flagged_when_assigned_differs_from_owner(self) -> None:
        tasks = [_task(task_id="A", assigned_to="planned.local", claimed_by="actual.local")]
        grouped = aggregate_agents(tasks)
        entry = grouped[0]["tasks"][0]
        self.assertTrue(entry["divergence"])
        self.assertEqual(grouped[0]["agent"], "actual.local")
        self.assertIn("planned.local", render_agents(tasks))  # both surfaced

    def test_no_divergence_when_assigned_equals_owner(self) -> None:
        tasks = [_task(task_id="A", assigned_to="same.local", claimed_by="same.local")]
        entry = aggregate_agents(tasks)[0]["tasks"][0]
        self.assertFalse(entry["divergence"])

    def test_unassigned_is_not_a_divergence(self) -> None:
        tasks = [_task(task_id="A", assigned_to="planned.local")]  # no claim yet
        grouped = aggregate_agents(tasks)
        self.assertEqual(grouped[0]["agent"], UNASSIGNED)
        self.assertFalse(grouped[0]["tasks"][0]["divergence"])


class RecordedTimestampTests(unittest.TestCase):
    """R-4: recorded timestamps shown ONLY when present; never fabricated."""

    def test_timestamp_shown_when_present(self) -> None:
        tasks = [_task(task_id="A", claimed_by="x.local", claimed_at="2026-06-29T10:00:00")]
        entry = aggregate_agents(tasks)[0]["tasks"][0]
        self.assertEqual(entry["claimed_at"], "2026-06-29T10:00:00")
        self.assertIn("2026-06-29T10:00:00", render_agents(tasks))

    def test_timestamp_absent_when_not_recorded(self) -> None:
        tasks = [_task(task_id="A", claimed_by="x.local")]
        entry = aggregate_agents(tasks)[0]["tasks"][0]
        self.assertNotIn("claimed_at", entry)  # not fabricated
        self.assertNotIn("returned_at", entry)

    def test_audit_readiness_read_from_metadata_when_present(self) -> None:
        tasks = [_task(task_id="A", claimed_by="x.local", metadata={"audit_readiness": "ready"})]
        entry = aggregate_agents(tasks)[0]["tasks"][0]
        self.assertEqual(entry["audit_readiness"], "ready")


class DisclosureAndRenderTests(unittest.TestCase):
    """§5: the 'not live' disclosure is rendered; claims ⊆ disclosure (no liveness wording)."""

    def test_not_live_label_present_in_render(self) -> None:
        out = render_agents([_task(task_id="A", claimed_by="x.local")])
        self.assertIn(NOT_LIVE_LABEL, out)
        self.assertIn("does not track live presence", out)

    def test_empty_queue_renders_label_and_no_crash(self) -> None:
        out = render_agents([])
        self.assertIn(NOT_LIVE_LABEL, out)
        self.assertIn("no tasks", out)

    def test_render_never_implies_online(self) -> None:
        out = render_agents([_task(task_id="A", claimed_by="x.local")]).lower()
        for liveness in ("online", "is live", "heartbeat", "presence:"):
            self.assertNotIn(liveness, out)


class GateNotEngineTests(unittest.TestCase):
    """§3/§6 R-3: the projection introduces NO runtime — pure, one-shot, no timer/poll/daemon."""

    # Executable runtime forms (NOT prose). The module's docstring honestly says "no polling /
    # heartbeat", so we match code shapes, not the words, to avoid a false positive on the negation.
    _FORBIDDEN = (
        "set_interval",
        "while True",
        "Timer(",
        "asyncio",
        "threading",
        ".poll(",
        "scheduler",
        "Thread(",
    )

    def test_agents_view_has_no_runtime_primitive(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "agents_view.py").read_text(encoding="utf-8")
        for token in self._FORBIDDEN:
            self.assertNotIn(token, source, f"gate-not-engine violated: '{token}' in agents_view.py")


if __name__ == "__main__":
    unittest.main()
