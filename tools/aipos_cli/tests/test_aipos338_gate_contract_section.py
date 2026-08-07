"""AIPOS-338 S1/S6 — 过门契约节 (gate contract section) tests.

Verifies the card-bound 「认领与交回」 section is single-source derived:
  - branch comes from flow_description.resolve_gate_chain
  - verbs come from verb_contract.resolve_gate_verbs (the live registry)
  - the publisher (draft_writer) carries ZERO lybra_* literals
"""
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.aipos_cli.gate_contract_section import render_gate_contract_section
from tools.aipos_cli.verb_contract import get_verb_names


_WS = "/home/kiwi/example-workspace"


def _meta(task_id, **overrides):
    base = {
        "title": "Example Draft",
        "project": "lybra",
        "assigned_to": "agent-01",
        "agent_instance": "agent-01",
        "context_bundle": "agent-01",
        "task_mode": "code",
        "model_tier": "L2",
        "priority": "medium",
        "status": "pending",
        "created_by": "tester",
        "needs_owner": False,
        "output_target": "tools/aipos_cli/",
        "artifact_policy": "formal_write",
        "task_type": "one_shot",
        "polling_mode": "agent_polling",
        "claim_policy": "assigned_agent_only",
        "report_mode": "forum_reply",
        "recurrence": "none",
    }
    base.update(overrides)
    base["task_id"] = task_id
    return base


def _publish_card(repo_root, task_id, **overrides):
    body = overrides.pop("body", "## Goal\n\nDo it.")
    create_result = create_draft(repo_root, _meta(task_id, **overrides), body)
    draft_rel = create_result["target_path"]
    return publish_draft(repo_root, draft_rel, actor="agent-01")


class TestGateContractSectionRendering(unittest.TestCase):
    def _verbs_in(self, text):
        return set(re.findall(r"\b(lybra_[a-z_]+)\b", text))

    def test_section_contains_real_registered_verbs(self):
        profile = {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}
        section = render_gate_contract_section(
            profile, {"task_mode": "code"}, role="executor",
            gate_url="http://127.0.0.1:7118", connection_json_rel=".lybra/connection.json",
            workspace_display=_WS, task_id="AIPOS-1",
            claim_envelope="pol_lybra_dev_7", return_envelope="pol_lybra_dev_1",
        )
        found = self._verbs_in(section)
        self.assertTrue(found, "section must reference verbs")
        # every verb token in the section is a real registered verb (no stale hand-written names)
        names = get_verb_names()
        stale = found - names
        self.assertEqual(stale, set(), f"section references unregistered verbs: {stale}")
        # the three executor-facing verbs are present
        self.assertIn("lybra_queue_claim_dry_run", found)
        self.assertIn("lybra_queue_return_dry_run", found)
        self.assertIn("lybra_task_progress", found)

    def test_section_carries_connection_and_block_location(self):
        section = render_gate_contract_section(
            {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
            {"task_mode": "code"}, role="executor",
            gate_url="http://127.0.0.1:7118", connection_json_rel=".lybra/connection.json",
            workspace_display=_WS, task_id="AIPOS-1",
            claim_envelope="pol_lybra_dev_7", return_envelope="pol_lybra_dev_1",
        )
        self.assertIn("http://127.0.0.1:7118", section)
        self.assertIn(".lybra/connection.json", section)
        # BLOCK lands in workspace events (S10-compatible)
        self.assertIn("blocked_*.md", section)
        self.assertIn("claims/<ID>/claim_*.md", section)

    def test_branch_code_with_deploy_has_deploy_gate_reminder(self):
        section = render_gate_contract_section(
            {"code_enabled": True, "deploy_gate_enabled": True, "default_audit_mode": "agent"},
            {"task_mode": "code", "deploy": True}, role="executor",
            gate_url="http://g", connection_json_rel=".lybra/connection.json",
            workspace_display=_WS, task_id="AIPOS-2",
            claim_envelope="pol_lybra_dev_7", return_envelope="pol_lybra_dev_1",
        )
        self.assertIn("部署门提醒", section)
        # prod-grade only; dev-loop deploy does NOT trigger (S6 判据澄清)
        self.assertIn("生产级部署", section)
        self.assertIn("不触发", section)

    def test_branch_noncode_has_no_independent_audit_and_bench_degradation(self):
        section = render_gate_contract_section(
            {"code_enabled": False, "deploy_gate_enabled": False, "default_audit_mode": "bench"},
            {"task_mode": "content"}, role="executor",
            gate_url="http://g", connection_json_rel=".lybra/connection.json",
            workspace_display=_WS, task_id="AIPOS-3",
            claim_envelope="pol_lybra_dev_7", return_envelope="pol_lybra_dev_1",
        )
        # non-code branch does NOT derive an independent audit R card (S6②)
        self.assertIn("不派生独立审计 R 卡", section)
        # AIPOS-336F1: bench verbs now implemented -> no degradation marker, real verb name present
        self.assertNotIn("bench 动词尚未实现", section)
        self.assertIn("lybra_bench_audit_submit_dry_run", section)
        # evidence requirements attached
        self.assertIn("证据要求", section)

    def test_auditor_role_section(self):
        section = render_gate_contract_section(
            {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
            {"task_mode": "audit"}, role="auditor",
            gate_url="http://g", connection_json_rel=".lybra/connection.json",
            workspace_display=_WS, task_id="AIPOS-1R",
            claim_envelope="pol_lybra_dev_7", return_envelope="pol_lybra_dev_1",
            audit_envelope="pol_lybra_audit_2",
        )
        self.assertIn("审计体必读", section)
        self.assertIn("lybra_audit_verdict_dry_run", section)


class TestPublisherCarriesNoVerbLiterals(unittest.TestCase):
    """S1 red line: the publisher must not hand-write a verb list."""

    def test_draft_writer_has_no_lybra_verb_name_literals(self):
        src = Path("tools/aipos_cli/draft_writer.py").read_text(encoding="utf-8")
        # find all lybra_* tokens in the source
        tokens = set(re.findall(r"(lybra_[a-z_]+)", src))
        # the publisher may name NO gate verb (the section is derived).
        offending = {t for t in tokens if t in get_verb_names()}
        self.assertEqual(
            offending, set(),
            f"draft_writer.py hand-writes gate verb literal(s): {offending} "
            "(must derive all verbs from verb_contract)",
        )


class TestRegistryRenameAutoFollows(unittest.TestCase):
    """Acceptance #2: a registry rename auto-flows into the section."""

    def test_rename_flows_through_resolver(self):
        from tools.aipos_cli.verb_contract import resolve_gate_verbs
        from tools.mcp_server.tools import TOOL_HANDLERS, WRITE_TOOL_DESCRIPTORS

        old_name = "lybra_queue_claim_dry_run"
        # realistic rename: the operation stem changes, the _dry_run/_confirm
        # suffix convention is stable (it's the gate two-step protocol).
        new_name = "lybra_queue_obtain_claim_dry_run"
        orig_handlers = dict(TOOL_HANDLERS)
        orig_desc = [dict(d) for d in WRITE_TOOL_DESCRIPTORS]

        try:
            # rename in registry (handler + descriptor)
            TOOL_HANDLERS[new_name] = TOOL_HANDLERS.pop(old_name)
            for d in WRITE_TOOL_DESCRIPTORS:
                if d.get("name") == old_name:
                    d["name"] = new_name
            verbs = resolve_gate_verbs()
            self.assertEqual(verbs["claim_dry_run"]["name"], new_name)
            # the section uses the resolver, so it shows the new name
            section = render_gate_contract_section(
                {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
                {"task_mode": "code"}, role="executor",
                gate_url="http://g", connection_json_rel=".lybra/connection.json",
                workspace_display=_WS, task_id="AIPOS-1",
                claim_envelope="pol_lybra_dev_7", return_envelope="pol_lybra_dev_1",
            )
            self.assertIn(new_name, section)
            self.assertNotIn(old_name, section)
        finally:
            TOOL_HANDLERS.clear()
            TOOL_HANDLERS.update(orig_handlers)
            WRITE_TOOL_DESCRIPTORS[:] = orig_desc


def _create_test_policies(repo_root: Path) -> None:
    """Create minimal active policies so render_gate_contract_section can resolve envelopes.

    AIPOS-340F2: tests that exercise the production path (publish_draft → _append_gate_contract_section
    → render_gate_contract_section with workspace_root) need real policy files to resolve from.
    """
    policies_dir = repo_root / "5_tasks" / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)
    # dev policy
    (policies_dir / "pol_lybra_dev_7.md").write_text(
        "---\n"
        "policy_id: pol_lybra_dev_7\n"
        "status: active\n"
        "role: exec\n"
        "policy_type: dev\n"
        "---\n"
        "# Dev Policy 7\n",
        encoding="utf-8",
    )
    # audit policy
    (policies_dir / "pol_lybra_audit_2.md").write_text(
        "---\n"
        "policy_id: pol_lybra_audit_2\n"
        "status: active\n"
        "role: audit\n"
        "policy_type: audit\n"
        "---\n"
        "# Audit Policy 2\n",
        encoding="utf-8",
    )


class TestPublishAppendsSection(unittest.TestCase):
    """Acceptance #1: a newly published card auto-includes the contract section."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        _create_test_policies(self.repo_root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_publish_includes_contract_section(self):
        result = _publish_card(self.repo_root, "AIPOS-777")
        self.assertEqual(result["verdict"], "PASS", result.get("blocking_reasons"))
        published = (self.repo_root / result["target_path"]).read_text(encoding="utf-8")
        self.assertIn("【认领与交回】", published)
        # verbs in the published card are all registered
        found = set(re.findall(r"\b(lybra_[a-z_]+)\b", published))
        self.assertTrue(found & {"lybra_queue_claim_dry_run", "lybra_queue_return_dry_run"})
        stale = found - get_verb_names()
        self.assertEqual(stale, set())

    def test_source_draft_unchanged_after_publish(self):
        create_result = create_draft(self.repo_root, _meta("AIPOS-778"), "## Goal\n\nDo it.")
        draft_rel = create_result["target_path"]
        publish_draft(self.repo_root, draft_rel, actor="agent-01")
        source = (self.repo_root / draft_rel).read_text(encoding="utf-8")
        # the draft does NOT carry the section (only the published card does)
        self.assertNotIn("【认领与交回】", source)


class TestNoHardcodedFallback(unittest.TestCase):
    """AIPOS-340F2: no silent fallback — missing envelopes must raise."""

    def test_missing_workspace_root_raises(self):
        """No workspace_root → ValueError (no silent baking)."""
        with self.assertRaises(ValueError) as ctx:
            render_gate_contract_section(
                {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
                {"task_mode": "code"}, role="executor",
                gate_url="http://g", connection_json_rel=".lybra/connection.json",
                workspace_display=_WS, task_id="AIPOS-1",
                # no workspace_root, no explicit envelopes
            )
        self.assertIn("workspace_root is required", str(ctx.exception))

    def test_no_active_policies_raises(self):
        """workspace_root with no valid policies → ValueError with actionable message."""
        with TemporaryDirectory() as tmp:
            empty_root = Path(tmp)
            with self.assertRaises(ValueError) as ctx:
                render_gate_contract_section(
                    {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
                    {"task_mode": "code"}, role="executor",
                    gate_url="http://g", connection_json_rel=".lybra/connection.json",
                    workspace_display=_WS, task_id="AIPOS-1",
                    workspace_root=empty_root,
                )
            msg = str(ctx.exception)
            self.assertIn("cannot resolve policy envelope", msg)
            self.assertIn("claim_envelope", msg)

    def test_explicit_envelopes_skip_policy_resolution(self):
        """Explicit envelopes bypass policy resolution (test injection path)."""
        # Even with no workspace policies, explicit envelopes work fine
        section = render_gate_contract_section(
            {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
            {"task_mode": "code"}, role="executor",
            gate_url="http://g", connection_json_rel=".lybra/connection.json",
            workspace_display=_WS, task_id="AIPOS-1",
            claim_envelope="pol_test_explicit", return_envelope="pol_test_explicit",
            workspace_root=Path("/nonexistent"),  # doesn't matter when explicit
        )
        self.assertIn("pol_test_explicit", section)

    def test_no_hardcoded_pol_lybra_dev_in_source(self):
        """AIPOS-340F2 acceptance #3: grep no pol_lybra_dev_* hardcoded in gate_contract_section.py."""
        src = Path("tools/aipos_cli/gate_contract_section.py").read_text(encoding="utf-8")
        import re as _re
        hardcoded = _re.findall(r"pol_lybra_dev_\d+", src)
        self.assertEqual(hardcoded, [], f"gate_contract_section.py still has hardcoded envelopes: {hardcoded}")


if __name__ == "__main__":
    unittest.main()
