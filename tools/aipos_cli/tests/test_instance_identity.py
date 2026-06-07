from __future__ import annotations

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
        self.assertEqual(sorted(self.profiles["instance_index"]), ["agent-01", "agent-02", "agent-03"])
        instance = self.profiles["instance_index"]["agent-01"]["instance"]
        self.assertEqual(instance["legacy_instance_ids"], ["legacy.primary"])
        self.assertEqual(instance["provenance"]["vendor"], "custom-vendor")

    def test_legacy_id_resolves_to_canonical_id(self) -> None:
        resolved = resolve_instance_id("legacy.primary", self.profiles)
        self.assertEqual(resolved["resolution"], "legacy")
        self.assertEqual(resolved["canonical_instance_id"], "agent-01")

    def test_strict_claim_accepts_canonical_and_legacy_equivalence(self) -> None:
        canonical_target = {"claim_policy": "specific_instance_only", "agent_instance": "agent-01"}
        legacy_target = {"claim_policy": "specific_instance_only", "agent_instance": "legacy.primary"}
        self.assertTrue(specific_instance_match_details(canonical_target, "legacy.primary", self.profiles)["matched"])
        self.assertTrue(specific_instance_match_details(legacy_target, "agent-01", self.profiles)["matched"])

    def test_strict_claim_blocks_legacy_sibling_instance(self) -> None:
        metadata = {"claim_policy": "specific_instance_only", "agent_instance": "agent-01"}
        details = specific_instance_match_details(metadata, "legacy.audit", self.profiles)
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
        self.assertNotIn("agent-01", profiles["instance_index"])
        self.assertTrue(
            any("Duplicate canonical agent_instance ignored: agent-01" in warning for warning in profiles["profiles"][0]["warnings"])
        )

    def test_runtime_lookup_uses_legacy_mapping_instead_of_default(self) -> None:
        runtime = runtime_config_for_actor("legacy.audit", self.profiles)
        self.assertEqual(runtime["matched_instance"], "agent-02")
        self.assertEqual(runtime["runtime_profile"], "audit")

    def test_unknown_actor_availability_warning_explains_no_heartbeat_tracking(self) -> None:
        runtime = runtime_config_for_actor("missing-agent", self.profiles)

        self.assertEqual(runtime["actor_availability_status"], "unknown")
        self.assertIn("does not track live agent presence or heartbeat state", runtime["availability_warning"])
        self.assertIn("gate-not-engine", runtime["availability_warning"])

    def test_default_independence_uses_distinct_canonical_ids(self) -> None:
        passed = evaluate_instance_independence("legacy.primary", "agent-02", self.profiles)
        blocked = evaluate_instance_independence("legacy.primary", "agent-01", self.profiles)
        self.assertTrue(passed["matched"])
        self.assertFalse(blocked["matched"])

    def test_stronger_independence_uses_explicit_exact_string_comparison(self) -> None:
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
        result = evaluate_instance_independence(
            "agent-01",
            "agent-03",
            self.profiles,
            {"distinct_model_family": True},
        )
        self.assertFalse(result["matched"])
        self.assertFalse(result["checks"]["distinct_model_family"]["matched"])


if __name__ == "__main__":
    unittest.main()
