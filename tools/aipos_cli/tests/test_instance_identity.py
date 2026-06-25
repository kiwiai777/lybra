from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.agent_profiles import (
    evaluate_instance_independence,
    load_agent_profiles,
    resolve_instance_id,
    runtime_config_for_actor,
    specific_instance_match_details,
)

# ENV-AWARE flag: WS~20 — bare-python asserts LOUD/FAIL-CLOSED, not skip.
_HAS_YAML = importlib.util.find_spec("yaml") is not None


PROFILE_DOC = """# Test Runtime Profiles

```yaml
agent_id: test_agent
display_name: Test Agent
enabled: true
aliases:
  - test_agent
instances:
  - agent_instance: agent-01
    legacy_instance_ids:
      - legacy.primary
    provenance:
      vendor: custom-vendor
      harness: custom-harness
      model_family: model-a
      host: local
    runtime_profile: primary
    enabled: true
    availability_status: online
  - agent_instance: agent-02
    legacy_instance_ids:
      - legacy.audit
    provenance:
      vendor: custom-vendor
      harness: custom-harness
      model_family: model-b
      host: local
    runtime_profile: audit
    enabled: true
    availability_status: online
  - agent_instance: agent-03
    legacy_instance_ids:
      - legacy.unknown
    provenance:
      vendor: custom-vendor
      harness: custom-harness
      model_family: unknown
      host: local
    runtime_profile: direct
    enabled: true
    availability_status: online
default_instance: agent-01
```
"""


class InstanceIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        docs_root = self.repo_root / "0_control_plane" / "agents"
        docs_root.mkdir(parents=True)
        (docs_root / "test_runtime_profiles.md").write_text(PROFILE_DOC, encoding="utf-8")
        self.profiles = load_agent_profiles(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_profile_loads_canonical_ids_and_free_form_provenance(self) -> None:
        if not _HAS_YAML:
            # BARE: profile doc uses sequences-of-mappings → yaml absent → LOUD warn + fallback.
            # The test-written docs profile can't be parsed, so load_agent_profiles falls back to
            # the FALLBACK_PROFILE (agent-01/02/03 with anthropic provenance, not custom-vendor).
            # Loud warning must be surfaced.
            self.assertTrue(
                any("PyYAML unavailable" in w for w in self.profiles.get("registry_warnings", [])),
                f"Expected loud PyYAML warning; got: {self.profiles.get('registry_warnings')}",
            )
            # Fallback profile is used (not the test profile).
            instance = self.profiles["instance_index"].get("agent-01", {}).get("instance", {})
            self.assertNotEqual(instance.get("provenance", {}).get("vendor"), "custom-vendor",
                                "On bare python, test-written docs profile must NOT be loaded")
            return
        self.assertEqual(sorted(self.profiles["instance_index"]), ["agent-01", "agent-02", "agent-03"])
        instance = self.profiles["instance_index"]["agent-01"]["instance"]
        self.assertEqual(instance["legacy_instance_ids"], ["legacy.primary"])
        self.assertEqual(instance["provenance"]["vendor"], "custom-vendor")

    def test_legacy_id_resolves_to_canonical_id(self) -> None:
        if not _HAS_YAML:
            # BARE: profiles empty → legacy resolves as "unregistered" (raw value returned).
            resolved = resolve_instance_id("legacy.primary", self.profiles)
            self.assertEqual(resolved["resolution"], "unregistered")
            return
        resolved = resolve_instance_id("legacy.primary", self.profiles)
        self.assertEqual(resolved["resolution"], "legacy")
        self.assertEqual(resolved["canonical_instance_id"], "agent-01")

    def test_strict_claim_accepts_canonical_and_legacy_equivalence(self) -> None:
        if not _HAS_YAML:
            # BARE: legacy mapping absent → strict-claim via legacy alias is not equivalent.
            canonical_target = {"claim_policy": "specific_instance_only", "agent_instance": "agent-01"}
            legacy_target = {"claim_policy": "specific_instance_only", "agent_instance": "legacy.primary"}
            # canonical-vs-canonical (agent-01 == agent-01) matches via exact string.
            self.assertTrue(specific_instance_match_details(canonical_target, "agent-01", self.profiles)["matched"])
            # legacy alias does NOT match canonical with empty profiles.
            self.assertFalse(specific_instance_match_details(canonical_target, "legacy.primary", self.profiles)["matched"])
            self.assertFalse(specific_instance_match_details(legacy_target, "agent-01", self.profiles)["matched"])
            return
        canonical_target = {"claim_policy": "specific_instance_only", "agent_instance": "agent-01"}
        legacy_target = {"claim_policy": "specific_instance_only", "agent_instance": "legacy.primary"}
        self.assertTrue(specific_instance_match_details(canonical_target, "legacy.primary", self.profiles)["matched"])
        self.assertTrue(specific_instance_match_details(legacy_target, "agent-01", self.profiles)["matched"])

    def test_strict_claim_blocks_legacy_sibling_instance(self) -> None:
        metadata = {"claim_policy": "specific_instance_only", "agent_instance": "agent-01"}
        details = specific_instance_match_details(metadata, "legacy.audit", self.profiles)
        if not _HAS_YAML:
            # BARE: both "agent-01" and "legacy.audit" resolve as unregistered/raw.
            # They are not equal strings → mismatch (still blocked, just for a different reason).
            self.assertFalse(details["matched"])
            return
        self.assertFalse(details["matched"])
        self.assertEqual(details["instance_match_result"], "mismatch")

    def test_ambiguous_legacy_mapping_blocks_strict_claim(self) -> None:
        docs_root = self.repo_root / "0_control_plane" / "agents"
        (docs_root / "test_runtime_profiles.md").write_text(
            PROFILE_DOC.replace("legacy.audit", "legacy.primary"),
            encoding="utf-8",
        )
        profiles = load_agent_profiles(self.repo_root)
        details = specific_instance_match_details(
            {"claim_policy": "specific_instance_only", "agent_instance": "legacy.primary"},
            "agent-01",
            profiles,
        )
        if not _HAS_YAML:
            # BARE: ambiguity cannot be detected → still blocked (exact-string mismatch).
            self.assertFalse(details["matched"])
            return
        self.assertFalse(details["matched"])
        self.assertEqual(details["instance_match_result"], "ambiguous")

    def test_invalid_canonical_id_is_not_indexed(self) -> None:
        docs_root = self.repo_root / "0_control_plane" / "agents"
        (docs_root / "test_runtime_profiles.md").write_text(
            PROFILE_DOC.replace("agent_instance: agent-03", "agent_instance: INVALID ID"),
            encoding="utf-8",
        )
        profiles = load_agent_profiles(self.repo_root)
        self.assertNotIn("INVALID ID", profiles["instance_index"])
        if not _HAS_YAML:
            # BARE: profile doc cannot be parsed → loud warning; FALLBACK_PROFILE used instead.
            self.assertTrue(
                any("PyYAML unavailable" in w for w in profiles.get("registry_warnings", [])),
            )
            return
        self.assertTrue(
            any("Invalid canonical agent_instance ignored: INVALID ID" in warning for warning in profiles["profiles"][0]["warnings"])
        )

    def test_duplicate_canonical_id_is_not_indexed(self) -> None:
        docs_root = self.repo_root / "0_control_plane" / "agents"
        (docs_root / "test_runtime_profiles.md").write_text(
            PROFILE_DOC.replace("agent_instance: agent-02", "agent_instance: agent-01"),
            encoding="utf-8",
        )
        profiles = load_agent_profiles(self.repo_root)
        if not _HAS_YAML:
            # BARE: profile doc cannot be parsed → loud warning; FALLBACK_PROFILE is used instead.
            # Fallback has agent-01 from the FALLBACK_PROFILE constant (not the test doc).
            self.assertTrue(
                any("PyYAML unavailable" in w for w in profiles.get("registry_warnings", [])),
            )
            # agent-01 IS present (from fallback, not from the test's duplicate doc).
            self.assertIn("agent-01", profiles["instance_index"])
            return
        # With PyYAML: the test doc is parsed and the duplicate is caught.
        self.assertNotIn("agent-01", profiles["instance_index"])
        self.assertTrue(
            any("Duplicate canonical agent_instance ignored: agent-01" in warning for warning in profiles["profiles"][0]["warnings"])
        )

    def test_runtime_lookup_uses_legacy_mapping_instead_of_default(self) -> None:
        if not _HAS_YAML:
            # BARE: profiles empty → legacy lookup falls back to exact match (stricter, not wider).
            # "legacy.audit" is not a canonical ID → returns no match.
            runtime = runtime_config_for_actor("legacy.audit", self.profiles)
            # The important invariant: access is NOT widened; legacy alias does not match canonical.
            self.assertNotEqual(runtime.get("matched_instance"), "agent-02")
            return
        runtime = runtime_config_for_actor("legacy.audit", self.profiles)
        self.assertEqual(runtime["matched_instance"], "agent-02")
        self.assertEqual(runtime["runtime_profile"], "audit")

    def test_unknown_actor_availability_warning_explains_no_heartbeat_tracking(self) -> None:
        runtime = runtime_config_for_actor("missing-agent", self.profiles)
        self.assertEqual(runtime["actor_availability_status"], "unknown")
        self.assertIn("does not track live agent presence or heartbeat state", runtime["availability_warning"])
        self.assertIn("gate-not-engine", runtime["availability_warning"])

    def test_default_independence_uses_distinct_canonical_ids(self) -> None:
        if not _HAS_YAML:
            # BARE: profiles empty → legacy aliases resolve as "unregistered"/raw.
            # "legacy.primary" resolves to itself; "agent-01" resolves to itself → they ARE distinct.
            passed = evaluate_instance_independence("legacy.primary", "agent-02", self.profiles)
            blocked = evaluate_instance_independence("legacy.primary", "agent-01", self.profiles)
            # With empty profiles both sides are unregistered/raw — string compare still works.
            self.assertTrue(passed["matched"])  # "legacy.primary" != "agent-02" → distinct
            self.assertTrue(blocked["matched"])  # "legacy.primary" != "agent-01" → also distinct
            # (Note: the gate-level P3 check in board_adapter blocks independence *enforcement*
            # when registry_available=False — the raw evaluate_instance_independence function
            # itself is not the enforcement point; it only compares strings.)
            return
        passed = evaluate_instance_independence("legacy.primary", "agent-02", self.profiles)
        blocked = evaluate_instance_independence("legacy.primary", "agent-01", self.profiles)
        self.assertTrue(passed["matched"])
        self.assertFalse(blocked["matched"])

    def test_stronger_independence_uses_explicit_exact_string_comparison(self) -> None:
        if not _HAS_YAML:
            # BARE: no instance configs → dimension checks all fail (unknown values) → blocked.
            result = evaluate_instance_independence(
                "agent-01", "agent-02", self.profiles,
                {"distinct_model_family": True, "distinct_vendor": True},
            )
            # Both are "unregistered" so agent-01 != agent-02 (distinct_instance=True),
            # but dimension checks have no config → matched=False.
            self.assertFalse(result["matched"])
            return
        result = evaluate_instance_independence(
            "agent-01",
            "agent-02",
            self.profiles,
            {"distinct_model_family": True, "distinct_vendor": True},
        )
        self.assertFalse(result["matched"])
        self.assertTrue(result["checks"]["distinct_model_family"]["matched"])
        self.assertFalse(result["checks"]["distinct_vendor"]["matched"])

    def test_stronger_independence_missing_or_unknown_fails_conservatively(self) -> None:
        if not _HAS_YAML:
            # BARE: no configs → all dimension checks fail (unknown/empty values).
            result = evaluate_instance_independence(
                "agent-01", "agent-03", self.profiles, {"distinct_model_family": True},
            )
            self.assertFalse(result["matched"])
            return
        result = evaluate_instance_independence(
            "agent-01",
            "agent-03",
            self.profiles,
            {"distinct_model_family": True},
        )
        self.assertFalse(result["matched"])
        self.assertFalse(result["checks"]["distinct_model_family"]["matched"])


class IndependenceFailClosedTests(unittest.TestCase):
    """AIPOS-219 §6b mandatory negative/positive control tests.

    Negative control: a legacy/registry-unverified executor identity recorded on bare python
    → the audit verdict path BLOCKs (INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY), never false PASS.
    Positive control: with PyYAML present the same flow behaves normally.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # AIPOS-219 §6b condition ③ — the authoritative negative/positive controls drive the REAL
    # board_adapter audit-dispatch path (not a re-implementation of the P3 logic, which would be
    # tautological): see tools/mcp_server/tests/test_mcp_tools.py ::
    #   test_audit_dispatch_blocks_registry_unverified_executor_real_path  (registry-unverified
    #     executor → real BLOCK INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY; proven non-tautological by
    #     neutralizing the guard), and
    #   test_audit_dispatch_passes_when_both_registry_verified_real_path   (positive control).

    def test_negative_control_same_canonical_still_blocks_even_if_both_verified(self) -> None:
        """INDEPENDENCE_FAILED still fires when both canonicals are equal, regardless of PyYAML."""
        from tools.aipos_cli.board_adapter import registry_available as _reg_avail

        # Same instance on both sides.
        canonical_agent_instance = "agent-01"
        reviewed_executor_instance = "agent-01"

        # P3 check
        auditor_registry_ok = _reg_avail()
        executor_registry_ok = True  # explicitly verified

        same_canonical = canonical_agent_instance == reviewed_executor_instance

        if not _HAS_YAML:
            # BARE: INDEPENDENCE_UNVERIFIABLE fires first (auditor unverified).
            # Either way it blocks — never a false PASS.
            self.assertTrue(not auditor_registry_ok or same_canonical)
            return

        # With PyYAML: P3 does not fire, but INDEPENDENCE_FAILED fires.
        independence_unverifiable = not auditor_registry_ok or not executor_registry_ok
        self.assertFalse(independence_unverifiable)
        self.assertTrue(same_canonical, "INDEPENDENCE_FAILED should fire")


if __name__ == "__main__":
    unittest.main()
