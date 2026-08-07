"""AIPOS-330 tests — verb contract, flow description, gate guidance, and kickoff validation.

Tests cover:
- S1: Verb registry derived from TOOL_HANDLERS, auto-follows changes
- S2: Kickoff validation catches unregistered verbs at generation time
- S3: Gate guidance answers correct next verb/params/scope
- S4: Scope denial includes "who holds this scope"
- S6: Adding a fictional verb → zero changes in generation side
- S7: Adding a fictional branch → S3 answers correctly, zero code changes
- S8: Changing collaboration_profile → S3 answer changes, zero code changes
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Ensure project root is on path
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestVerbContractRegistry(unittest.TestCase):
    """S1: Verb contract registry is derived from TOOL_HANDLERS."""

    def test_registry_returns_all_tools(self):
        """Registry contains every tool in TOOL_HANDLERS."""
        from tools.aipos_cli.verb_contract import get_verb_registry
        from tools.mcp_server.tools import TOOL_HANDLERS

        registry = get_verb_registry()
        registry_names = {entry["name"] for entry in registry}
        handler_names = set(TOOL_HANDLERS.keys())

        self.assertEqual(registry_names, handler_names,
                         f"Registry mismatch: missing={handler_names - registry_names}, "
                         f"extra={registry_names - handler_names}")

    def test_registry_entries_have_required_fields(self):
        """Each entry has name, required_params, optional_params, required_scope."""
        from tools.aipos_cli.verb_contract import get_verb_registry

        registry = get_verb_registry()
        self.assertGreater(len(registry), 0)

        for entry in registry:
            self.assertIn("name", entry)
            self.assertIn("required_params", entry)
            self.assertIn("optional_params", entry)
            self.assertIn("required_scope", entry)
            self.assertIn("confirm_pair", entry)
            self.assertIn("is_confirm", entry)
            self.assertIsInstance(entry["required_params"], list)
            self.assertIsInstance(entry["optional_params"], list)

    def test_known_verb_contracts(self):
        """Spot-check known verbs have correct scope and params."""
        from tools.aipos_cli.verb_contract import get_verb_contract

        # queue_claim_dry_run requires queue_claim scope
        contract = get_verb_contract("lybra_queue_claim_dry_run")
        self.assertIsNotNone(contract)
        self.assertEqual(contract["required_scope"], "queue_claim")
        self.assertIn("actor", contract["required_params"])
        self.assertIn("agent_instance", contract["required_params"])

        # audit_verdict_dry_run requires audit_verdict scope
        contract = get_verb_contract("lybra_audit_verdict_dry_run")
        self.assertIsNotNone(contract)
        self.assertEqual(contract["required_scope"], "audit_verdict")
        self.assertIn("reviewed_task_id", contract["required_params"])
        self.assertIn("verdict", contract["required_params"])

    def test_read_only_tools_have_no_scope(self):
        """Read-only tools have required_scope=None."""
        from tools.aipos_cli.verb_contract import get_verb_contract

        for name in ["lybra_queue_list", "lybra_validate", "lybra_project_status", "lybra_gate_guidance"]:
            contract = get_verb_contract(name)
            self.assertIsNotNone(contract, f"{name} not in registry")
            self.assertIsNone(contract["required_scope"],
                              f"{name} should be read-only (scope=None)")

    def test_confirm_pairing(self):
        """dry_run verbs pair with their confirm counterparts."""
        from tools.aipos_cli.verb_contract import get_verb_contract

        claim_dry = get_verb_contract("lybra_queue_claim_dry_run")
        self.assertEqual(claim_dry["confirm_pair"], "lybra_queue_claim_confirm")
        self.assertFalse(claim_dry["is_confirm"])

        claim_confirm = get_verb_contract("lybra_queue_claim_confirm")
        self.assertEqual(claim_confirm["confirm_pair"], "lybra_queue_claim_dry_run")
        self.assertTrue(claim_confirm["is_confirm"])


class TestVerbValidation(unittest.TestCase):
    """S2: Validation catches unregistered verbs at generation time."""

    def test_valid_verb_passes(self):
        """Registered verb names pass validation."""
        from tools.aipos_cli.verb_contract import validate_verb_name

        self.assertTrue(validate_verb_name("lybra_queue_claim_dry_run"))
        self.assertTrue(validate_verb_name("lybra_audit_verdict_dry_run"))
        self.assertTrue(validate_verb_name("lybra_task_progress"))

    def test_invalid_verb_fails(self):
        """Unregistered verb names fail validation."""
        from tools.aipos_cli.verb_contract import validate_verb_name

        # The exact bug from AIPOS-325R: "lybra_audit_verdict" without suffix
        self.assertFalse(validate_verb_name("lybra_audit_verdict"))
        self.assertFalse(validate_verb_name("lybra_nonexistent_verb"))
        self.assertFalse(validate_verb_name("lybra_claim"))

    def test_kickoff_validation_catches_bad_verbs(self):
        """validate_kickoff_verbs catches unregistered verbs in text."""
        from tools.aipos_cli.verb_contract import validate_kickoff_verbs

        # Good kickoff with real verb names
        good_text = "调用 lybra_queue_claim_dry_run 然后 lybra_audit_verdict_dry_run"
        errors = validate_kickoff_verbs(good_text)
        self.assertEqual(errors, [])

        # Bad kickoff with hallucinated verb (AIPOS-325R scenario)
        bad_text = "调用 lybra_audit_verdict 步骤"
        errors = validate_kickoff_verbs(bad_text)
        self.assertGreater(len(errors), 0)
        self.assertIn("lybra_audit_verdict", errors[0])

    def test_kickoff_validation_suggests_close_matches(self):
        """Validation suggests close matches for misspelled verbs."""
        from tools.aipos_cli.verb_contract import validate_kickoff_verbs

        bad_text = "调用 lybra_audit_verdict 步骤"
        errors = validate_kickoff_verbs(bad_text)
        self.assertGreater(len(errors), 0)
        # Should suggest lybra_audit_verdict_dry_run or lybra_audit_verdict_confirm
        self.assertTrue(
            "lybra_audit_verdict_dry_run" in errors[0] or
            "lybra_audit_verdict_confirm" in errors[0] or
            "Did you mean" in errors[0],
            f"Expected suggestion in: {errors[0]}"
        )


class TestKickoffGeneration(unittest.TestCase):
    """S2: Kickoff generation validates verb names."""

    def test_advisor_pump_kickoff_validates_verbs(self):
        """advisor_pump.generate_kickoff validates verbs (S2)."""
        from tools.aipos_cli.advisor_pump import generate_kickoff

        # Normal generation should succeed
        kickoff = generate_kickoff("AIPOS-999", "executor", "first", "test delta")
        self.assertIn("AIPOS-999", kickoff)
        # Should reference lybra_gate_guidance (a real verb)
        self.assertIn("lybra_gate_guidance", kickoff)

    def test_auditor_loop_kickoff_validates_verbs(self):
        """auditor_runtime kickoff text validates verbs (S2). AIPOS-358: migrated from auditor_loop."""
        from tools.aipos_cli.verb_contract import validate_kickoff_verbs

        # Simulate the kickoff text from auditor_runtime (AIPOS-358 thin shell)
        from tools.aipos_cli.verb_contract import get_verb_contract
        verdict_contract = get_verb_contract("lybra_audit_verdict_dry_run")
        self.assertIsNotNone(verdict_contract)

        # The verb name from the contract should pass validation
        test_kickoff = f"调用 {verdict_contract['name']} 提交裁决"
        errors = validate_kickoff_verbs(test_kickoff)
        self.assertEqual(errors, [])


class TestScopeRoleMap(unittest.TestCase):
    """S4: Scope-to-role mapping for actionable rejection."""

    def test_scope_role_map_populated(self):
        """scope_role_map has entries for known scopes."""
        from tools.aipos_cli.verb_contract import get_scope_role_map

        mapping = get_scope_role_map()
        self.assertIn("queue_claim", mapping)
        self.assertIn("executor", mapping["queue_claim"])
        self.assertIn("audit_verdict", mapping)
        self.assertIn("auditor", mapping["audit_verdict"])

    def test_who_holds_scope(self):
        """who_holds_scope returns correct roles."""
        from tools.aipos_cli.verb_contract import who_holds_scope

        # audit_verdict is held by auditor
        holders = who_holds_scope("audit_verdict")
        self.assertIn("auditor", holders)

        # owner_confirm is held by owner
        holders = who_holds_scope("owner_confirm")
        self.assertIn("owner", holders)

        # nonexistent scope returns empty
        holders = who_holds_scope("nonexistent_scope")
        self.assertEqual(holders, [])


class TestFlowDescription(unittest.TestCase):
    """S3/S7/S8: Data-driven flow description."""

    def test_resolve_gate_chain_code_no_deploy(self):
        """Code project without deploy → code_no_deploy chain."""
        from tools.aipos_cli.flow_description import resolve_gate_chain

        profile = {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}
        task_fields = {"task_mode": "code"}
        chain = resolve_gate_chain(profile, task_fields)
        self.assertEqual(chain.branch_id, "code_no_deploy")

    def test_resolve_gate_chain_noncode(self):
        """Non-code project → noncode_bench_audit chain."""
        from tools.aipos_cli.flow_description import resolve_gate_chain

        profile = {"code_enabled": False, "deploy_gate_enabled": False, "default_audit_mode": "bench"}
        task_fields = {"task_mode": "content"}
        chain = resolve_gate_chain(profile, task_fields)
        self.assertEqual(chain.branch_id, "noncode_bench_audit")

    def test_resolve_gate_chain_with_deploy(self):
        """Code project with deploy → code_with_deploy chain."""
        from tools.aipos_cli.flow_description import resolve_gate_chain

        profile = {"code_enabled": True, "deploy_gate_enabled": True, "default_audit_mode": "agent"}
        task_fields = {"task_mode": "code", "deploy": True}
        chain = resolve_gate_chain(profile, task_fields)
        self.assertEqual(chain.branch_id, "code_with_deploy")

    def test_noncode_chain_has_bench_verbs_implemented(self):
        """AIPOS-336: Non-code chain has bench audit verbs implemented and resolvable."""
        from tools.aipos_cli.flow_description import _NONCODE_CHAIN
        from tools.aipos_cli.verb_contract import resolve_gate_verbs

        bench_steps = [s for s in _NONCODE_CHAIN.steps if 'bench_audit' in s.verb_name]
        self.assertGreater(len(bench_steps), 0, "Non-code chain should have bench_audit steps")
        
        # Bench verbs should NOT be marked not_implemented (336 delivered them)
        unimplemented = [s for s in bench_steps if s.not_implemented]
        self.assertEqual(len(unimplemented), 0, "Bench audit verbs should be implemented (AIPOS-336)")
        
        # Bench verbs should resolve in the verb registry
        resolved = resolve_gate_verbs()
        self.assertIsNotNone(resolved.get('bench_audit_submit'), "bench_audit_submit should resolve")
        self.assertIsNotNone(resolved.get('bench_audit_confirm'), "bench_audit_confirm should resolve")

    def test_task_audit_bench_overrides_profile(self):
        """Task-level audit=bench overrides project's agent audit mode."""
        from tools.aipos_cli.flow_description import resolve_gate_chain

        profile = {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}
        task_fields = {"task_mode": "code", "audit": "bench"}
        chain = resolve_gate_chain(profile, task_fields)
        # audit=bench should route to noncode chain (bench audit)
        self.assertEqual(chain.branch_id, "noncode_bench_audit")


class TestS6Extensibility(unittest.TestCase):
    """S6: Adding a fictional verb → zero changes in generation side."""

    def test_new_verb_appears_in_registry(self):
        """Adding a verb to TOOL_HANDLERS makes it appear in the registry automatically."""
        from tools.aipos_cli.verb_contract import get_verb_registry, validate_verb_name

        # Simulate adding a new verb by patching TOOL_HANDLERS
        from tools.mcp_server.tools import TOOL_HANDLERS

        new_verb_name = "lybra_fictional_verb_dry_run"
        original_handlers = dict(TOOL_HANDLERS)

        try:
            # Add a fictional verb
            TOOL_HANDLERS[new_verb_name] = lambda args: {}

            # It should now appear in the registry
            registry = get_verb_registry()
            registry_names = {entry["name"] for entry in registry}
            self.assertIn(new_verb_name, registry_names)

            # It should pass validation
            self.assertTrue(validate_verb_name(new_verb_name))
        finally:
            # Restore
            TOOL_HANDLERS.clear()
            TOOL_HANDLERS.update(original_handlers)

    def test_new_verb_kickoff_validates(self):
        """A kickoff containing a newly-added verb passes validation (zero code changes)."""
        from tools.aipos_cli.verb_contract import validate_kickoff_verbs
        from tools.mcp_server.tools import TOOL_HANDLERS

        new_verb_name = "lybra_fictional_new_dry_run"
        original_handlers = dict(TOOL_HANDLERS)

        try:
            TOOL_HANDLERS[new_verb_name] = lambda args: {}

            kickoff = f"调用 {new_verb_name} 完成操作"
            errors = validate_kickoff_verbs(kickoff)
            self.assertEqual(errors, [], f"New verb should pass validation: {errors}")
        finally:
            TOOL_HANDLERS.clear()
            TOOL_HANDLERS.update(original_handlers)


class TestS7BranchExtensibility(unittest.TestCase):
    """S7: Adding a fictional branch → S3 answers correctly, zero code changes."""

    def test_add_fictional_branch(self):
        """Adding a new branch description → S3 resolves it without code changes."""
        from tools.aipos_cli.flow_description import (
            GateChain, GateChainStep, _BRANCH_REGISTRY, resolve_gate_chain,
        )

        # Add a fictional branch: "research → peer review"
        fictional_chain = GateChain(
            branch_id="research_peer_review",
            branch_label="调研任务 → 同行评审",
            steps=(
                GateChainStep(
                    verb_name="lybra_queue_claim_dry_run",
                    required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
                    scope_needed="queue_claim",
                    description="认领",
                ),
                GateChainStep(
                    verb_name="lybra_fictional_peer_review",
                    not_implemented=True,
                    required_params=["task_id", "review_refs"],
                    scope_needed="peer_review",
                    description="同行评审（虚构动词）",
                ),
            ),
        )

        original_registry = dict(_BRANCH_REGISTRY)
        try:
            # Register the fictional branch
            _BRANCH_REGISTRY[(False, False, "peer_review")] = fictional_chain

            # Resolve with the new branch key
            profile = {"code_enabled": False, "deploy_gate_enabled": False, "default_audit_mode": "peer_review"}
            task_fields = {"task_mode": "research"}
            chain = resolve_gate_chain(profile, task_fields)
            self.assertEqual(chain.branch_id, "research_peer_review")
            self.assertEqual(len(chain.steps), 2)
        finally:
            _BRANCH_REGISTRY.clear()
            _BRANCH_REGISTRY.update(original_registry)


class TestS8ProfileEvolution(unittest.TestCase):
    """S8: Changing collaboration_profile → S3 answer changes, zero code changes."""

    def test_profile_change_switches_chain(self):
        """Toggling code_enabled changes the gate chain for the same task."""
        from tools.aipos_cli.flow_description import resolve_gate_chain

        task_fields = {"task_mode": "code"}

        # Profile A: code enabled
        profile_a = {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}
        chain_a = resolve_gate_chain(profile_a, task_fields)
        self.assertEqual(chain_a.branch_id, "code_no_deploy")

        # Profile B: code disabled (project evolved away from code)
        profile_b = {"code_enabled": False, "deploy_gate_enabled": False, "default_audit_mode": "bench"}
        chain_b = resolve_gate_chain(profile_b, task_fields)
        self.assertEqual(chain_b.branch_id, "noncode_bench_audit")

        # Different chains → different steps
        self.assertNotEqual(chain_a.branch_id, chain_b.branch_id)


class TestScopeDenialMessage(unittest.TestCase):
    """S4: Scope denial includes actionable info."""

    def test_scope_denied_includes_holders(self):
        """_scope_denied_result_for includes who holds the scope."""
        from tools.mcp_server.tools import _scope_denied_result_for

        result = _scope_denied_result_for("audit_verdict", "audit verdict tools")
        # Extract the text content
        content = result.get("content", [])
        self.assertGreater(len(content), 0)
        text = content[0].get("text", "")

        # Should mention who holds audit_verdict
        self.assertIn("auditor", text)

    def test_scope_denied_mentions_parameter_clarity(self):
        """Scope denial clarifies that 'owner' in param name ≠ owner scope."""
        from tools.mcp_server.tools import _scope_denied_result_for

        result = _scope_denied_result_for("audit_verdict", "audit verdict tools")
        content = result.get("content", [])
        text = content[0].get("text", "")

        # Should mention the AIPOS-325R confusion case
        self.assertIn("owner_confirmation_token", text)


class TestGateGuidanceTool(unittest.TestCase):
    """S3: lybra_gate_guidance tool works correctly."""

    def test_gate_guidance_registered(self):
        """lybra_gate_guidance is in TOOL_HANDLERS."""
        from tools.mcp_server.tools import TOOL_HANDLERS

        self.assertIn("lybra_gate_guidance", TOOL_HANDLERS)

    def test_gate_guidance_requires_task_id(self):
        """Gate guidance returns error without task_id."""
        from tools.mcp_server.tools import lybra_gate_guidance

        result = lybra_gate_guidance({"role": "executor"})
        self.assertTrue(result.get("isError", False))

    def test_gate_guidance_requires_role(self):
        """Gate guidance returns error without role."""
        from tools.mcp_server.tools import lybra_gate_guidance

        result = lybra_gate_guidance({"task_id": "AIPOS-999"})
        self.assertTrue(result.get("isError", False))

    def test_gate_guidance_returns_structure(self):
        """Gate guidance returns proper structure for valid input."""
        from tools.mcp_server.tools import lybra_gate_guidance

        # This will resolve against the actual workspace, which may not have the task
        # But it should still return a valid structure (possibly with unknown status)
        result = lybra_gate_guidance({"task_id": "AIPOS-330", "role": "executor"})
        content = result.get("content", [])
        if content:
            text = content[0].get("text", "")
            data = json.loads(text)
            self.assertIn("ok", data)
            self.assertIn("guidance", data)


class TestValidationRuleExtensibility(unittest.TestCase):
    """S6③: Validation rules are extensible."""

    def test_register_custom_rule(self):
        """Custom validation rules can be registered and run."""
        from tools.aipos_cli.verb_contract import (
            VerbValidationRule, register_validation_rule, validate_verb_usage,
            _validation_rules,
        )

        class CustomRule(VerbValidationRule):
            name = "test_custom_rule"

            def check(self, verb_name, context=None):
                if "forbidden" in verb_name:
                    return ["Verb contains 'forbidden'"]
                return []

        original_len = len(_validation_rules)
        try:
            register_validation_rule(CustomRule())

            # Should catch the forbidden pattern
            errors = validate_verb_usage("lybra_forbidden_verb")
            self.assertIn("Verb contains 'forbidden'", errors)

            # Normal verbs should pass this rule
            errors = validate_verb_usage("lybra_queue_claim_dry_run")
            self.assertNotIn("Verb contains 'forbidden'", errors)
        finally:
            # Clean up
            while len(_validation_rules) > original_len:
                _validation_rules.pop()


if __name__ == "__main__":
    unittest.main()
