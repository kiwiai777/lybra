"""AIPOS-350 — Instance naming productization tests.

Tests cover:
  S1: Canonical name generation from naming profile
  S2: Alias layer as workspace data (read/write/modify/trail)
  S3: Validator zero-hardcoding (reads from naming profile)
  Acceptance: agency example, rename-without-code-change, backward compat
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.naming_profile import (
    DEFAULT_PREFIX_MAPPING,
    default_naming_profile,
    get_naming_profile,
    set_prefix_mapping,
    set_project_segment,
    set_host_segment,
    add_project_segment_alias,
    add_host_segment_alias,
    generate_canonical_name,
    validate_instance_name_default,
    validate_instance_name_with_profile,
    ROLE_NAMES,
)
from tools.aipos_cli.workspace_config import write_project_json


def _make_project(tmp: Path, name: str = "lybra", *, host: str = "kiwiai-dev") -> Path:
    """Create a minimal project workspace.

    AIPOS-350F1: host_segment is explicitly configured (no baked-in default).
    """
    root = tmp / name
    (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (root / "governance").mkdir(parents=True, exist_ok=True)
    write_project_json(root, name)
    # Explicitly set host_segment (350F1: no hardcoded default)
    if host:
        pj = root / "project.json"
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["naming_profile"] = {"host_segment": host}
        pj.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return root


class TestDefaultNamingProfile(unittest.TestCase):
    """S2: Default naming profile — AIPOS-350F1: no baked-in project/host."""

    def test_default_has_all_roles(self):
        profile = default_naming_profile()
        for role in ("executor", "auditor", "owner", "copilot", "planner", "owner-dispatch"):
            self.assertIn(role, profile["prefix_mapping"])

    def test_default_prefixes_match_convention(self):
        profile = default_naming_profile()
        self.assertEqual(profile["prefix_mapping"]["executor"], "exec")
        self.assertEqual(profile["prefix_mapping"]["auditor"], "audit")
        # AIPOS-350F1: no project_segment or host_segment in defaults
        self.assertNotIn("project_segment", profile)
        self.assertNotIn("host_segment", profile)

    def test_default_aliases_empty(self):
        profile = default_naming_profile()
        self.assertEqual(profile["project_segment_aliases"], [])
        self.assertEqual(profile["host_segment_aliases"], [])

    def test_default_with_project_root_derives_project_segment(self):
        """With project_root, project_segment comes from project.json's 'project' field."""
        with TemporaryDirectory() as tmp:
            root = _make_project(Path(tmp), "myproject")
            profile = default_naming_profile(root)
            self.assertEqual(profile["project_segment"], "myproject")
            # host_segment still absent (no default)
            self.assertNotIn("host_segment", profile)

    def test_default_without_project_field_raises(self):
        """If project.json has no 'project' field, raises ValueError."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "bare"
            (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
            (root / "project.json").write_text('{"config_version": 1}', encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                default_naming_profile(root)
            self.assertIn("project_segment", str(ctx.exception))


class TestGetNamingProfile(unittest.TestCase):
    """S2: get_naming_profile reads from project.json, falls back to defaults."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_absent_naming_profile_derives_from_project_json(self):
        """AIPOS-350F1: without naming_profile, project_segment comes from project.json 'project'."""
        profile = get_naming_profile(self.root)
        self.assertEqual(profile["prefix_mapping"]["executor"], "exec")
        # project_segment derived from project.json's 'project' field
        self.assertEqual(profile["project_segment"], "lybra")
        # host_segment from naming_profile only — _make_project sets it
        self.assertEqual(profile["host_segment"], "kiwiai-dev")

    def test_partial_merge_with_defaults(self):
        """A project.json with only project_segment still gets default prefixes."""
        data = json.loads((self.root / "project.json").read_text())
        data["naming_profile"] = {"project_segment": "myproject"}
        (self.root / "project.json").write_text(json.dumps(data), encoding="utf-8")
        profile = get_naming_profile(self.root)
        self.assertEqual(profile["project_segment"], "myproject")
        # Default prefixes still present
        self.assertEqual(profile["prefix_mapping"]["executor"], "exec")
        self.assertEqual(profile["prefix_mapping"]["auditor"], "audit")

    def test_full_profile_roundtrip(self):
        set_prefix_mapping(self.root, "planner", "advisor", by="owner", reason="test")
        profile = get_naming_profile(self.root)
        self.assertEqual(profile["prefix_mapping"]["planner"], "advisor")
        # Other prefixes unchanged
        self.assertEqual(profile["prefix_mapping"]["executor"], "exec")


class TestSetPrefixMapping(unittest.TestCase):
    """S2: set_prefix_mapping modifies alias layer, leaves trail."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_prefix_writes_and_preserves(self):
        set_prefix_mapping(self.root, "planner", "advisor", by="owner", reason="agency convention")
        profile = get_naming_profile(self.root)
        self.assertEqual(profile["prefix_mapping"]["planner"], "advisor")
        # project.json still has other fields
        data = json.loads((self.root / "project.json").read_text())
        self.assertEqual(data["project"], "lybra")

    def test_set_prefix_appends_trail(self):
        set_prefix_mapping(self.root, "planner", "advisor", by="owner", reason="test1")
        set_prefix_mapping(self.root, "planner", "plnr", by="owner", reason="test2")
        trail = self.root / "governance" / "naming_profile_log.md"
        self.assertTrue(trail.exists())
        log = trail.read_text(encoding="utf-8")
        self.assertIn("advisor", log)
        self.assertIn("plnr", log)
        self.assertIn("test1", log)
        self.assertIn("test2", log)

    def test_empty_role_rejected(self):
        with self.assertRaises(ValueError):
            set_prefix_mapping(self.root, "", "x")

    def test_empty_prefix_rejected(self):
        with self.assertRaises(ValueError):
            set_prefix_mapping(self.root, "planner", "")


class TestSetProjectSegment(unittest.TestCase):
    """S2: set_project_segment modifies project name + optional aliases."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_project_segment(self):
        set_project_segment(self.root, "kiwiaiops", aliases=["lybra", "ops"])
        profile = get_naming_profile(self.root)
        self.assertEqual(profile["project_segment"], "kiwiaiops")
        self.assertEqual(sorted(profile["project_segment_aliases"]), ["lybra", "ops"])

    def test_add_alias_idempotent(self):
        add_project_segment_alias(self.root, "ops")
        add_project_segment_alias(self.root, "ops")  # duplicate
        profile = get_naming_profile(self.root)
        self.assertEqual(profile["project_segment_aliases"].count("ops"), 1)


class TestSetHostSegment(unittest.TestCase):
    """S2: set_host_segment modifies host name + optional aliases."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_host_segment(self):
        set_host_segment(self.root, "kiwiai-mac", aliases=["kiwiai-dev"])
        profile = get_naming_profile(self.root)
        self.assertEqual(profile["host_segment"], "kiwiai-mac")
        self.assertIn("kiwiai-dev", profile["host_segment_aliases"])


class TestGenerateCanonicalName(unittest.TestCase):
    """S1: Canonical name auto-generation."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_generation(self):
        name = generate_canonical_name("executor", self.root)
        self.assertEqual(name, "exec.lybra.kiwiai-dev")

    def test_auditor_generation(self):
        name = generate_canonical_name("auditor", self.root)
        self.assertEqual(name, "audit.lybra.kiwiai-dev")

    def test_after_prefix_change(self):
        set_prefix_mapping(self.root, "planner", "advisor")
        name = generate_canonical_name("planner", self.root)
        self.assertEqual(name, "advisor.lybra.kiwiai-dev")

    def test_after_project_change(self):
        set_project_segment(self.root, "newproj")
        name = generate_canonical_name("executor", self.root)
        self.assertEqual(name, "exec.newproj.kiwiai-dev")

    def test_after_host_change(self):
        set_host_segment(self.root, "kiwiai-mac")
        name = generate_canonical_name("executor", self.root)
        self.assertEqual(name, "exec.lybra.kiwiai-mac")

    def test_host_segment_override(self):
        """AIPOS-350F1: per-instance host override at minting time."""
        name = generate_canonical_name("executor", self.root, host_segment_override="kiwiai-mac")
        self.assertEqual(name, "exec.lybra.kiwiai-mac")

    def test_no_host_segment_raises(self):
        """AIPOS-350F1: missing host_segment raises, not silently uses a default."""
        with TemporaryDirectory() as tmp:
            root = _make_project(Path(tmp), "nohost", host="")
            with self.assertRaises(ValueError) as ctx:
                generate_canonical_name("executor", root)
            self.assertIn("host_segment", str(ctx.exception))

    def test_unknown_role_raises(self):
        with self.assertRaises(ValueError):
            generate_canonical_name("nonexistent", self.root)

    def test_full_agency_scenario(self):
        """After configuring planner->advisor, agency's naming works."""
        set_prefix_mapping(self.root, "planner", "advisor")
        set_project_segment(self.root, "kiwiaiagency")
        set_host_segment(self.root, "kiwiai-mac")
        name = generate_canonical_name("planner", self.root)
        self.assertEqual(name, "advisor.kiwiaiagency.kiwiai-mac")


class TestValidateInstanceNameDefault(unittest.TestCase):
    """S3: Backward-compatible validation (format-only, no project/host value check).

    AIPOS-350F1: validate_instance_name_default checks format, prefix, and role-name
    guard only.  It does NOT check project/host values (no hardcoded defaults).
    """

    def test_valid_names(self):
        ok, msg = validate_instance_name_default("exec.lybra.kiwiai-dev", "executor")
        self.assertTrue(ok, msg)
        ok, msg = validate_instance_name_default("audit.lybra.kiwiai-dev", "auditor")
        self.assertTrue(ok, msg)

    def test_detects_role_name_as_project(self):
        """audit.auditor.kiwiai-dev — project part is a role name."""
        ok, msg = validate_instance_name_default("audit.auditor.kiwiai-dev", "auditor")
        self.assertFalse(ok)
        self.assertIn("role name", msg.lower())

    def test_detects_wrong_prefix(self):
        ok, msg = validate_instance_name_default("planner.lybra.kiwiai-dev", "executor")
        self.assertFalse(ok)
        self.assertIn("prefix", msg.lower())

    def test_single_part_rejected(self):
        ok, msg = validate_instance_name_default("something", "executor")
        self.assertFalse(ok)
        self.assertIn("3 parts", msg)

    def test_empty_rejected(self):
        ok, msg = validate_instance_name_default("", "executor")
        self.assertFalse(ok)

    def test_default_accepts_planner_prefix(self):
        """Default profile has planner->planner, so planner.lybra.kiwiai-dev passes."""
        ok, msg = validate_instance_name_default("planner.lybra.kiwiai-dev", "planner")
        self.assertTrue(ok, msg)

    def test_any_project_host_accepted(self):
        """AIPOS-350F1: any non-empty project/host values accepted (no value check)."""
        ok, msg = validate_instance_name_default("exec.anyproject.anyhost", "executor")
        self.assertTrue(ok, msg)


class TestValidateInstanceNameWithProfile(unittest.TestCase):
    """S3: Workspace-aware validation using naming profile (alias layer)."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_profile_validation(self):
        """_make_project sets host_segment=kiwiai-dev, project.json has project=lybra."""
        ok, msg = validate_instance_name_with_profile("exec.lybra.kiwiai-dev", "executor", self.root)
        self.assertTrue(ok, msg)

    def test_agency_scenario_after_config(self):
        """After configuring planner->advisor + project=kiwiaiagency + host=kiwiai-mac,
        advisor.kiwiaiagency.kiwiai-mac passes validation for planner role."""
        set_prefix_mapping(self.root, "planner", "advisor")
        set_project_segment(self.root, "kiwiaiagency")
        set_host_segment(self.root, "kiwiai-mac")
        ok, msg = validate_instance_name_with_profile("advisor.kiwiaiagency.kiwiai-mac", "planner", self.root)
        self.assertTrue(ok, msg)

    def test_audit_auditor_still_caught(self):
        """audit.auditor.kiwiai-dev — project part is a role name, still caught."""
        ok, msg = validate_instance_name_with_profile("audit.auditor.kiwiai-dev", "auditor", self.root)
        self.assertFalse(ok)
        self.assertIn("role name", msg.lower())

    def test_project_alias_accepted(self):
        """With project alias 'ops', exec.ops.kiwiai-dev passes."""
        set_project_segment(self.root, "lybra", aliases=["ops"])
        ok, msg = validate_instance_name_with_profile("exec.ops.kiwiai-dev", "executor", self.root)
        self.assertTrue(ok, msg)

    def test_host_alias_accepted(self):
        """With host alias 'kiwiai-mac', exec.lybra.kiwiai-mac passes."""
        add_host_segment_alias(self.root, "kiwiai-mac")
        ok, msg = validate_instance_name_with_profile("exec.lybra.kiwiai-mac", "executor", self.root)
        self.assertTrue(ok, msg)

    def test_unknown_project_rejected(self):
        ok, msg = validate_instance_name_with_profile("exec.unknown.kiwiai-dev", "executor", self.root)
        self.assertFalse(ok)
        self.assertIn("not the project segment", msg)

    def test_unknown_host_rejected(self):
        ok, msg = validate_instance_name_with_profile("exec.lybra.unknown", "executor", self.root)
        self.assertFalse(ok)
        self.assertIn("not the host segment", msg)


class TestServiceModeDelegation(unittest.TestCase):
    """S3: service_mode.validate_instance_name delegates to naming_profile (zero hardcode)."""

    def test_delegation_works(self):
        from tools.aipos_cli.service_mode import validate_instance_name
        ok, msg = validate_instance_name("exec.lybra.kiwiai-dev", "executor")
        self.assertTrue(ok, msg)

    def test_detects_role_name_as_project(self):
        from tools.aipos_cli.service_mode import validate_instance_name
        ok, msg = validate_instance_name("audit.auditor.kiwiai-dev", "auditor")
        self.assertFalse(ok)
        self.assertIn("role name", msg.lower())

    def test_no_hardcoded_prefix_map_in_source(self):
        """Verify the hardcoded role_prefixes dict is gone from service_mode.py."""
        import inspect
        from tools.aipos_cli import service_mode
        source = inspect.getsource(service_mode.validate_instance_name)
        self.assertNotIn('"executor": "exec"', source)
        self.assertNotIn('"auditor": "audit"', source)
        self.assertIn("naming_profile", source)


class TestRenameWithoutCodeChange(unittest.TestCase):
    """Acceptance 5: Rename全程零代码改动 (proven with fictional aliases)."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_fictional_rename_flow(self):
        """Demonstrate: change all naming via config, zero code changes."""
        # Start with defaults
        name = generate_canonical_name("executor", self.root)
        self.assertEqual(name, "exec.lybra.kiwiai-dev")

        # "Rename" project and host via config (prefix stays the same)
        set_project_segment(self.root, "newproject", aliases=["np", "old"])
        set_host_segment(self.root, "newhost", aliases=["nh"])

        # Verify new canonical name
        name = generate_canonical_name("executor", self.root)
        self.assertEqual(name, "exec.newproject.newhost")

        # Old names fail because project/host changed (old values not in aliases)
        ok, _ = validate_instance_name_with_profile("exec.lybra.kiwiai-dev", "executor", self.root)
        self.assertFalse(ok)

        # Add old values as aliases -> now they validate
        add_project_segment_alias(self.root, "lybra")
        add_host_segment_alias(self.root, "kiwiai-dev")
        ok, msg = validate_instance_name_with_profile("exec.lybra.kiwiai-dev", "executor", self.root)
        self.assertTrue(ok, msg)

    def test_fictional_prefix_rename(self):
        """Demonstrate: rename a role prefix via config, zero code changes."""
        # Default: planner -> planner
        name = generate_canonical_name("planner", self.root)
        self.assertEqual(name, "planner.lybra.kiwiai-dev")

        # Rename planner prefix to advisor
        set_prefix_mapping(self.root, "planner", "advisor")
        name = generate_canonical_name("planner", self.root)
        self.assertEqual(name, "advisor.lybra.kiwiai-dev")

        # Old prefix no longer valid (single prefix per role by design)
        ok, _ = validate_instance_name_with_profile("planner.lybra.kiwiai-dev", "planner", self.root)
        self.assertFalse(ok)

        # New prefix validates
        ok, msg = validate_instance_name_with_profile("advisor.lybra.kiwiai-dev", "planner", self.root)
        self.assertTrue(ok, msg)


class TestBackwardCompatibility(unittest.TestCase):
    """Acceptance 6: Zero regression — format checks still pass.

    AIPOS-350F1: validate_instance_name_default is now format-only (no project/host
    value check).  Prefix and role-name guards still work.
    """

    def test_validate_instance_name_valid(self):
        """Same assertions as the original test_service_mode.py tests."""
        ok, msg = validate_instance_name_default("exec.lybra.kiwiai-dev", "executor")
        self.assertTrue(ok)
        self.assertIsNone(msg)
        ok, msg = validate_instance_name_default("audit.lybra.kiwiai-dev", "auditor")
        self.assertTrue(ok)

    def test_validate_instance_name_detects_agency_issues(self):
        """Same assertions as the original test_service_mode.py tests."""
        ok, msg = validate_instance_name_default("audit.auditor.kiwiai-dev", "auditor")
        self.assertFalse(ok)
        self.assertIn("role name", msg.lower())
        ok, msg = validate_instance_name_default("something", "executor")
        self.assertFalse(ok)
        self.assertIn("3 parts", msg)


if __name__ == "__main__":
    unittest.main()
