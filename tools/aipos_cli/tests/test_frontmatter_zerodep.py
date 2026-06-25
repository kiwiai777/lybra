"""AIPOS-218 WS5 — bare-python round-trip parity test.

With the ``yaml`` module blocked via a sys.meta_path finder (same pattern as
v1_acceptance.py:34-41), asserts that the fallback parser produces the same
result as yaml.safe_load for:

- real records from the actual emitters (return w/ artifact_refs, audit-verdict
  w/ evidence_refs+bool, publish);
- a real task card (examples/sample_workspace/.../sample_task.md);
- every real bundled templates/*/manifest.md (nested maps + scalar lists).

Adversarial values: colon in value, ISO timestamp, ``#``, brackets/braces,
embedded quote, leading/trailing whitespace, empty vs null, flat list w/ colon
item, empty list, ``True-Name`` stays string, int, depth-1 nested map.

Also asserts warnings empty for all writer-emitted samples.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "templates"
SAMPLE_TASK = REPO_ROOT / "examples" / "sample_workspace" / "5_tasks" / "queue" / "pending" / "sample_task.md"

# Capture yaml.safe_load BEFORE blocking it.
try:
    import yaml as _real_yaml
    _HAS_YAML = True
except ImportError:
    _real_yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


class _BlockYaml:
    """sys.meta_path finder that raises ImportError for the yaml module."""

    def find_spec(self, name: str, path=None, target=None):  # type: ignore[override]
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("yaml blocked for AIPOS-218 WS5 bare-python test")
        return None


def _extract_frontmatter_text(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return ""


def _yaml_baseline(fm_text: str) -> dict:
    if not _HAS_YAML or _real_yaml is None:
        return {}
    result = _real_yaml.safe_load(fm_text)
    return result if isinstance(result, dict) else {}


def _run_with_yaml_blocked(fn):
    """Execute fn() with yaml import blocked; restore sys.modules state after."""
    blocker = _BlockYaml()
    # Remove yaml from sys.modules so the module under test re-imports it (and fails).
    saved = sys.modules.pop("yaml", None)
    sys.meta_path.insert(0, blocker)
    # Also remove the frontmatter module from sys.modules so it re-evaluates `yaml = None`.
    # Actually frontmatter.py does `try: import yaml` at module level, so if it's already
    # imported we need to patch the module attribute directly.
    import tools.aipos_cli.frontmatter as _fm_mod
    saved_yaml_attr = _fm_mod.yaml
    _fm_mod.yaml = None  # type: ignore[assignment]
    try:
        return fn()
    finally:
        sys.meta_path.remove(blocker)
        if saved is not None:
            sys.modules["yaml"] = saved
        _fm_mod.yaml = saved_yaml_attr


class FrontmatterZerodepParityTests(unittest.TestCase):
    """Parity: fallback_parse == yaml.safe_load for every real-world sample."""

    def _assert_parity(self, label: str, md_text: str) -> None:
        fm_text = _extract_frontmatter_text(md_text)
        if not fm_text:
            self.fail(f"{label}: no frontmatter found")

        baseline = _yaml_baseline(fm_text)

        def _parse_fallback():
            from tools.aipos_cli.frontmatter import _fallback_parse
            return _fallback_parse(fm_text)

        data, warnings = _run_with_yaml_blocked(_parse_fallback)

        if _HAS_YAML:
            self.assertEqual(
                data,
                baseline,
                f"{label}: fallback parse differs from yaml.safe_load baseline.\n"
                f"  baseline: {baseline}\n  fallback: {data}",
            )
        # Warnings must be empty for writer-emitted content.
        self.assertEqual(warnings, [], f"{label}: unexpected fallback warnings: {warnings}")

    def test_sample_task_card(self) -> None:
        if not SAMPLE_TASK.exists():
            self.skipTest(f"sample_task.md not found at {SAMPLE_TASK}")
        self._assert_parity("sample_task_card", SAMPLE_TASK.read_text(encoding="utf-8"))

    def test_every_manifest(self) -> None:
        manifests = list(TEMPLATES_DIR.rglob("manifest.md"))
        self.assertGreater(len(manifests), 0, "No manifests found")
        for path in sorted(manifests):
            self._assert_parity(path.name, path.read_text(encoding="utf-8"))

    def test_return_record_with_artifact_refs(self) -> None:
        from tools.aipos_cli.record_writer import build_mcp_return_record_markdown
        md = build_mcp_return_record_markdown(
            task_id="AIPOS-WS5-001",
            task_path="5_tasks/queue/claimed/aipos-ws5-001.md",
            actor="agent-01",
            canonical_agent_instance="agent-01",
            owner_policy_ref="DL-20260625-01",
            return_id="return-ws5-001",
            claim_id="claim-ws5-001",
            session_id="session-ws5-001",
            returned_at="2026-06-25T00:01:00Z",
            result_summary="Fix: completed the colon-value task",
            artifact_refs=["5_tasks/records/returns/r1.md", "docs/out #2.md"],
            completion_report_ref="5_tasks/records/returns/ws5/completion.md",
        )
        self._assert_parity("return_record_with_artifacts", md)

    def test_audit_verdict_record_with_bool_and_evidence(self) -> None:
        from tools.aipos_cli.record_writer import build_mcp_audit_verdict_record_markdown
        md = build_mcp_audit_verdict_record_markdown(
            verdict_id="verdict-ws5-001",
            verdict="PASS",
            reviewed_task_id="AIPOS-WS5-001",
            reviewed_task_path="5_tasks/queue/completed/aipos-ws5-001.md",
            reviewed_return_record_ref="5_tasks/records/returns/r1.md",
            audit_dispatch_record_ref="5_tasks/records/audit_dispatches/d1.md",
            audit_task_id="AIPOS-WS5-AUDIT",
            audit_task_path="5_tasks/queue/completed/aipos-ws5-audit.md",
            audit_claim_id="claim-ws5-002",
            audit_session_id="session-ws5-002",
            reviewed_executor_instance="agent-01",
            auditor_instance="agent-02",
            actor="agent-02",
            canonical_agent_instance="agent-02",
            owner_policy_ref="DL-20260625-01",
            verdict_at="2026-06-25T00:03:00Z",
            findings_summary="No issues found: all tests green.",
            evidence_refs=["5_tasks/records/sessions/s2.md"],
            recommended_next_action="finalize",
        )
        self._assert_parity("audit_verdict_with_bool_and_evidence", md)

    def test_publish_record(self) -> None:
        import tempfile
        from tools.aipos_cli.draft_writer import create_draft, publish_draft

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo_root = Path(td.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (repo_root / "5_tasks" / "queue" / state).mkdir(parents=True)
        metadata = {
            "task_id": "AIPOS-WS5-PUB",
            "title": "WS5 publish parity test",
            "project": "lybra",
            "assigned_to": "dev_claude",
            "agent_instance": "agent-01",
            "context_bundle": "default",
            "task_mode": "coding",
            "priority": "medium",
            "status": "pending",
            "created_by": "tester",
            "needs_owner": False,
            "artifact_policy": "formal_write",
            "model_tier": "L2",
            "output_target": "tools/",
        }
        draft = create_draft(repo_root, metadata, "WS5 test body.")
        self.assertNotEqual(draft.get("verdict"), "BLOCK", draft)
        self.assertTrue(draft.get("wrote"), draft)
        draft_path = repo_root / draft["target_path"]
        result = publish_draft(repo_root, draft_path, actor="tester", dry_run=False)
        self.assertNotEqual(result.get("verdict"), "BLOCK", result)
        task_path = repo_root / result["target_path"]
        md = task_path.read_text(encoding="utf-8")
        self._assert_parity("publish_record", md)


class FrontmatterZerodepAdversarialTests(unittest.TestCase):
    """Adversarial value coverage: colon, #, brackets, quote, whitespace, int, bool string."""

    def _parse(self, fm_text: str):
        def _go():
            from tools.aipos_cli.frontmatter import _fallback_parse
            return _fallback_parse(fm_text)
        return _run_with_yaml_blocked(_go)

    def _both(self, fm_text: str):
        """Return (fallback_data, yaml_data) — yaml_data is {} if not available."""
        data, warnings = self._parse(fm_text)
        baseline = _yaml_baseline(fm_text) if _HAS_YAML else {}
        return data, baseline, warnings

    def test_colon_in_quoted_value(self) -> None:
        fm = "title: 'Fix: thing'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("title"), "Fix: thing")
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_iso_timestamp_quoted(self) -> None:
        fm = "created_at: '2026-06-25T12:00:00Z'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("created_at"), "2026-06-25T12:00:00Z")
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_hash_in_quoted_value(self) -> None:
        fm = "ref: 'docs/output #2.md'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("ref"), "docs/output #2.md")
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_embedded_single_quote(self) -> None:
        fm = "name: 'o''brien'"
        data, _ = self._parse(fm)
        self.assertEqual(data.get("name"), "o'brien")

    def test_bool_coercion(self) -> None:
        fm = "enabled: true\ndisabled: false"
        data, baseline, _ = self._both(fm)
        self.assertIs(data.get("enabled"), True)
        self.assertIs(data.get("disabled"), False)
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_true_name_stays_string(self) -> None:
        fm = "key: True-Name"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("key"), "True-Name")
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_int_coercion(self) -> None:
        fm = "event_count: 42"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("event_count"), 42)
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_empty_vs_null(self) -> None:
        fm = "empty_key:"
        data, baseline, _ = self._both(fm)
        self.assertIsNone(data.get("empty_key"))
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_empty_list(self) -> None:
        fm = "refs: []"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("refs"), [])
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_flat_list_with_colon_item(self) -> None:
        fm = "refs:\n  - 'Fix: item'\n  - plain"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("refs"), ["Fix: item", "plain"])
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_depth_1_nested_map(self) -> None:
        fm = "output_policy:\n  overwrite_existing_files: false\n  remote_fetch_allowed: false"
        data, baseline, warnings = self._both(fm)
        self.assertEqual(
            data.get("output_policy"),
            {"overwrite_existing_files": False, "remote_fetch_allowed": False},
        )
        self.assertEqual(warnings, [])
        if _HAS_YAML:
            self.assertEqual(data, baseline)

    def test_leading_trailing_whitespace_in_value(self) -> None:
        fm = "title:   spaced value   "
        data, _ = self._parse(fm)
        # scalar strips surrounding whitespace
        self.assertEqual(data.get("title"), "spaced value")

    def test_brackets_braces_in_quoted_value(self) -> None:
        fm = "spec: '[a] {b}'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("spec"), "[a] {b}")
        if _HAS_YAML:
            self.assertEqual(data, baseline)


if __name__ == "__main__":
    unittest.main()
