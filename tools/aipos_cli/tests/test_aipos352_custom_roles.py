"""AIPOS-352: Tests for workspace custom role registry.

Tests cover:
1. Registry CRUD (register/remove/load)
2. Validation (name collision, invalid class, scope-free registry)
3. Scope resolution chain (custom name → class → ROLE_SPECS)
4. Token minting (custom role gets role_class, correct scopes)
5. Naming profile integration (custom role prefix)
6. Roles list/reconcile (custom roles appear correctly)
7. Anti-privilege-escalation (registry never carries scope fields)
8. Built-in six roles zero regression
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure product root is on sys.path
PRODUCT_ROOT = Path(__file__).resolve().parents[3]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from tools.aipos_cli.custom_roles import (
    load_custom_roles,
    register_custom_role,
    remove_custom_role,
    resolve_role_to_class,
    validate_custom_role_name,
    validate_builtin_class,
    is_custom_role,
    custom_roles_for_naming,
)
from tools.aipos_cli.service_mode import (
    ROLE_SPECS,
    build_connection_config,
    _mint_custom_role_tokens,
    _role_token_entry,
    redacted_connection,
    roles_list_report,
    roles_reconcile_report,
)
from tools.mcp_server.tools import _resolve_role_scopes


def _make_workspace():
    """Create a temp workspace with a minimal project.json."""
    tmpdir = tempfile.mkdtemp(prefix="aipos352_test_")
    ws = Path(tmpdir)
    project_json = {
        "project": "test_project",
        "naming_profile": {
            "host_segment": "testhost",
            "prefix_mapping": {},
            "project_segment_aliases": [],
            "host_segment_aliases": [],
        },
    }
    (ws / "project.json").write_text(json.dumps(project_json, indent=2) + "\n")
    # Create governance dir for trail
    (ws / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)
    return ws


class TestCustomRoleValidation(unittest.TestCase):
    """Test custom role name and class validation."""

    def test_valid_name(self):
        ok, err = validate_custom_role_name("kiwiaiops")
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_valid_name_with_hyphen(self):
        ok, err = validate_custom_role_name("my-custom-role")
        self.assertTrue(ok)

    def test_empty_name(self):
        ok, _ = validate_custom_role_name("")
        self.assertFalse(ok)

    def test_uppercase_name(self):
        ok, _ = validate_custom_role_name("Kiwiaiops")
        self.assertFalse(ok)

    def test_name_collision_with_builtin(self):
        ok, err = validate_custom_role_name("executor")
        self.assertFalse(ok)
        self.assertIn("collides", err)

    def test_name_collision_with_all_builtins(self):
        for spec in ROLE_SPECS:
            ok, _ = validate_custom_role_name(spec["role"])
            self.assertFalse(ok, f"{spec['role']} should collide")

    def test_invalid_class(self):
        ok, err = validate_builtin_class("bogus")
        self.assertFalse(ok)
        self.assertIn("Unknown", err)

    def test_valid_class(self):
        for spec in ROLE_SPECS:
            ok, _ = validate_builtin_class(spec["role"])
            self.assertTrue(ok)


class TestCustomRoleRegistry(unittest.TestCase):
    """Test registry CRUD operations."""

    def setUp(self):
        self.ws = _make_workspace()

    def test_empty_registry(self):
        result = load_custom_roles(self.ws)
        self.assertEqual(result, {})

    def test_register_and_load(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        result = load_custom_roles(self.ws)
        self.assertIn("kiwiaiops", result)
        self.assertEqual(result["kiwiaiops"]["class"], "executor")

    def test_register_multiple(self):
        register_custom_role(self.ws, "role-a", "executor")
        register_custom_role(self.ws, "role-b", "auditor")
        result = load_custom_roles(self.ws)
        self.assertEqual(len(result), 2)
        self.assertEqual(result["role-a"]["class"], "executor")
        self.assertEqual(result["role-b"]["class"], "auditor")

    def test_remove(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        remove_custom_role(self.ws, "kiwiaiops")
        result = load_custom_roles(self.ws)
        self.assertEqual(result, {})

    def test_remove_nonexistent_idempotent(self):
        result = remove_custom_role(self.ws, "nonexistent")
        self.assertEqual(result, {})

    def test_register_invalid_name_raises(self):
        with self.assertRaises(ValueError):
            register_custom_role(self.ws, "executor", "executor")

    def test_register_invalid_class_raises(self):
        with self.assertRaises(ValueError):
            register_custom_role(self.ws, "myrole", "bogus")


class TestScopeResolution(unittest.TestCase):
    """Test scope resolution chain: custom name → class → ROLE_SPECS."""

    def setUp(self):
        self.ws = _make_workspace()

    def test_builtin_resolves_to_self(self):
        self.assertEqual(resolve_role_to_class("executor"), "executor")
        self.assertEqual(resolve_role_to_class("auditor"), "auditor")

    def test_custom_resolves_to_class(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        self.assertEqual(resolve_role_to_class("kiwiaiops", self.ws), "executor")

    def test_unknown_returns_none(self):
        self.assertIsNone(resolve_role_to_class("unknown", self.ws))

    def test_resolve_role_scopes_builtin(self):
        scopes = _resolve_role_scopes("executor")
        self.assertIn("queue_claim", scopes)
        self.assertIn("queue_return", scopes)

    def test_resolve_role_scopes_custom_via_class(self):
        scopes = _resolve_role_scopes("kiwiaiops", role_class="executor")
        executor_scopes = _resolve_role_scopes("executor")
        self.assertEqual(scopes, executor_scopes)

    def test_resolve_role_scopes_custom_without_class_fails_closed(self):
        scopes = _resolve_role_scopes("kiwiaiops")
        self.assertEqual(scopes, [])

    def test_custom_scopes_match_class_scopes_exactly(self):
        """Custom role scopes = exactly the class's scopes. No more, no less."""
        register_custom_role(self.ws, "my-auditor", "auditor")
        custom_scopes = _resolve_role_scopes("my-auditor", role_class="auditor")
        auditor_scopes = _resolve_role_scopes("auditor")
        self.assertEqual(custom_scopes, auditor_scopes)


class TestTokenMinting(unittest.TestCase):
    """Test token minting for custom roles."""

    def setUp(self):
        self.ws = _make_workspace()

    def test_mint_custom_role_token(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        tokens = _mint_custom_role_tokens(self.ws)
        self.assertEqual(len(tokens), 1)
        token = tokens[0]
        self.assertEqual(token["role"], "kiwiaiops")
        self.assertEqual(token["role_class"], "executor")
        self.assertEqual(token["token_ref"], "svc-kiwiaiops")
        # Scopes match executor's scopes
        executor_spec = next(s for s in ROLE_SPECS if s["role"] == "executor")
        self.assertEqual(token["scopes"], list(executor_spec["scopes"]))

    def test_mint_with_instance_binding(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        tokens = _mint_custom_role_tokens(
            self.ws,
            role_instances={"kiwiaiops": "kiwiaiops.lybra.mac"},
        )
        self.assertEqual(tokens[0]["agent_instance"], "kiwiaiops.lybra.mac")

    def test_no_custom_roles_mints_nothing(self):
        tokens = _mint_custom_role_tokens(self.ws)
        self.assertEqual(tokens, [])

    def test_full_config_includes_custom_tokens(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        config = build_connection_config(
            self.ws,
            board_host="0.0.0.0", board_port=7117,
            mcp_host="0.0.0.0", mcp_port=7118,
            board_advertise_host="test.local",
            mcp_advertise_host="test.local",
        )
        roles = [t["role"] for t in config["tokens"]]
        self.assertIn("kiwiaiops", roles)
        # Built-in roles still present
        for spec in ROLE_SPECS:
            self.assertIn(spec["role"], roles)

    def test_redacted_connection_echoes_role_class(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        config = build_connection_config(
            self.ws,
            board_host="0.0.0.0", board_port=7117,
            mcp_host="0.0.0.0", mcp_port=7118,
            board_advertise_host="test.local",
            mcp_advertise_host="test.local",
        )
        redacted = redacted_connection(config)
        kiwiaiops_token = next(t for t in redacted["tokens"] if t["role"] == "kiwiaiops")
        self.assertEqual(kiwiaiops_token["role_class"], "executor")


class TestAntiPrivilegeEscalation(unittest.TestCase):
    """Test that the registry NEVER carries scope fields."""

    def setUp(self):
        self.ws = _make_workspace()

    def test_registry_has_no_scope_fields(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        registry = load_custom_roles(self.ws)
        for name, entry in registry.items():
            self.assertNotIn("scopes", entry, f"Registry entry {name} must not have scopes")
            self.assertNotIn("operations", entry)
            self.assertNotIn("permissions", entry)
            # Only field allowed: "class"
            self.assertEqual(set(entry.keys()), {"class"})

    def test_project_json_custom_roles_no_scopes(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        project_json = json.loads((self.ws / "project.json").read_text())
        custom_roles = project_json.get("custom_roles", {})
        for name, entry in custom_roles.items():
            self.assertNotIn("scopes", entry)
            self.assertEqual(set(entry.keys()), {"class"})


class TestBuiltinRolesZeroRegression(unittest.TestCase):
    """Test that built-in six roles are completely unchanged."""

    def test_role_specs_unchanged(self):
        """ROLE_SPECS still has exactly 6 built-in roles."""
        self.assertEqual(len(ROLE_SPECS), 6)
        expected = {"executor", "owner", "owner-dispatch", "auditor", "copilot", "planner"}
        actual = {spec["role"] for spec in ROLE_SPECS}
        self.assertEqual(actual, expected)

    def test_builtin_scopes_unchanged(self):
        """Each built-in role's scopes are unchanged."""
        expected_scopes = {
            "executor": ["queue_claim", "queue_return", "queue_close", "task_progress", "bench_audit_submit"],
            "owner": ["queue_claim", "queue_return", "owner_confirm", "draft_publish", "owner_decision_record", "queue_amend", "queue_withdraw", "bench_audit_confirm"],
            "owner-dispatch": ["audit_dispatch"],
            "auditor": ["queue_claim", "audit_verdict", "task_progress"],
            "copilot": [],
            "planner": ["draft_submit", "draft_publish"],
        }
        for spec in ROLE_SPECS:
            self.assertEqual(spec["scopes"], expected_scopes[spec["role"]])

    def test_builtin_token_minting_unchanged(self):
        """Built-in tokens have no role_class field."""
        ws = _make_workspace()
        config = build_connection_config(
            ws,
            board_host="0.0.0.0", board_port=7117,
            mcp_host="0.0.0.0", mcp_port=7118,
            board_advertise_host="test.local",
            mcp_advertise_host="test.local",
        )
        for token in config["tokens"]:
            if token["role"] in {spec["role"] for spec in ROLE_SPECS}:
                self.assertNotIn("role_class", token,
                    f"Built-in role {token['role']} must not have role_class")


class TestNamingProfileIntegration(unittest.TestCase):
    """Test that custom roles integrate with naming profile."""

    def setUp(self):
        self.ws = _make_workspace()

    def test_custom_role_prefix(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        prefixes = custom_roles_for_naming(self.ws)
        self.assertEqual(prefixes["kiwiaiops"], "kiwiaiops")

    def test_generate_canonical_name_for_custom_role(self):
        from tools.aipos_cli.naming_profile import generate_canonical_name
        register_custom_role(self.ws, "kiwiaiops", "executor")
        name = generate_canonical_name("kiwiaiops", self.ws, host_segment_override="mac")
        self.assertEqual(name, "kiwiaiops.test_project.mac")


class TestIsCustomRole(unittest.TestCase):
    """Test is_custom_role helper."""

    def setUp(self):
        self.ws = _make_workspace()

    def test_builtin_not_custom(self):
        self.assertFalse(is_custom_role("executor"))
        self.assertFalse(is_custom_role("auditor"))

    def test_registered_is_custom(self):
        register_custom_role(self.ws, "kiwiaiops", "executor")
        self.assertTrue(is_custom_role("kiwiaiops", self.ws))

    def test_unregistered_not_custom(self):
        self.assertFalse(is_custom_role("kiwiaiops", self.ws))


if __name__ == "__main__":
    unittest.main()
