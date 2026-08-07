"""AIPOS-347 — scope resolved at call time from ROLE_SPECS, not from token snapshot.

Red lines (from task card):
1. 活体: change a role's ROLE_SPECS and deploy → no token rotation → the role immediately
   gains/loses the scope (reproduce the planner draft_publish scenario).
2. 老 token 继续可用 (backward compat: tokens with stale operations still work).
3. 零放宽:逐条验证 return/audit_verdict/close/draft_publish/amend/withdraw/owner_confirm
   的判定结果与改造前一致 (same role+scope decisions as before).
4. tools/list visibility matches real-time scope.
5. Identity checks preserved: expiry, token_ref, instance binding all still enforced.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.mcp_server.tools import (
    _capability_has_scope,
    _resolve_role_scopes,
    _tool_result,
    request_capability_scope,
    visible_tool_descriptors,
)


_VALID = "2999-01-01T00:00:00Z"
_EXPIRED = "2020-01-01T00:00:00Z"


def _cap(role: str, operations: list[str] | None = None, *, expires_at: str = _VALID,
         token_ref: str = "test-token", **extra) -> dict:
    """Build a capability dict mimicking what http_sse._service_role_capability produces."""
    cap: dict = {
        "token_ref": token_ref,
        "role": role,
        "expires_at": expires_at,
        "source": "service_v0",
    }
    if operations is not None:
        cap["operations"] = list(operations)
    cap.update(extra)
    return cap


# ---------------------------------------------------------------------------
# 1. 活体: ROLE_SPECS change takes effect immediately
# ---------------------------------------------------------------------------

class LiveRoleSpecsTests(unittest.TestCase):
    """Changing ROLE_SPECS immediately affects scope resolution — no re-mint needed."""

    def test_planner_gains_draft_publish_without_remint(self) -> None:
        """Reproduce the AIPOS-342 scenario: planner token minted BEFORE draft_publish
        was added to ROLE_SPECS.  After the ROLE_SPECS change, the old token immediately
        gains draft_publish — no rotation needed."""
        # Simulate an OLD planner token: operations baked before draft_publish was added
        old_planner_cap = _cap("planner", operations=["draft_submit"])
        with request_capability_scope(old_planner_cap):
            # With AIPOS-347: role resolved from ROLE_SPECS → planner has draft_publish
            self.assertTrue(_capability_has_scope("draft_publish"))
            self.assertTrue(_capability_has_scope("draft_submit"))

    def test_role_scope_change_immediate(self) -> None:
        """Patching ROLE_SPECS to add a scope to a role → existing tokens gain it."""
        from tools.aipos_cli import service_mode
        original = service_mode.ROLE_SPECS

        try:
            # Patch ROLE_SPECS: add a new scope to copilot (normally empty)
            patched = tuple(
                {**spec, "scopes": ["queue_claim"]} if spec["role"] == "copilot" else spec
                for spec in original
            )
            with patch.object(service_mode, "ROLE_SPECS", patched):
                copilot_cap = _cap("copilot", operations=[])  # old token: no scopes
                with request_capability_scope(copilot_cap):
                    # Immediately gains queue_claim from patched ROLE_SPECS
                    self.assertTrue(_capability_has_scope("queue_claim"))
        finally:
            pass  # patch.object auto-restores

    def test_role_scope_removal_immediate(self) -> None:
        """Patching ROLE_SPECS to remove a scope from a role → existing tokens lose it."""
        from tools.aipos_cli import service_mode
        original = service_mode.ROLE_SPECS

        try:
            # Patch ROLE_SPECS: remove queue_close from executor
            patched = tuple(
                {**spec, "scopes": [s for s in spec["scopes"] if s != "queue_close"]}
                if spec["role"] == "executor" else spec
                for spec in original
            )
            with patch.object(service_mode, "ROLE_SPECS", patched):
                executor_cap = _cap("executor", operations=["queue_claim", "queue_return", "queue_close"])
                with request_capability_scope(executor_cap):
                    # queue_close is gone from ROLE_SPECS → denied even though old token has it
                    self.assertFalse(_capability_has_scope("queue_close"))
                    # queue_claim still works
                    self.assertTrue(_capability_has_scope("queue_claim"))
        finally:
            pass


# ---------------------------------------------------------------------------
# 2. 老 token 继续可用 (backward compat)
# ---------------------------------------------------------------------------

class BackwardCompatTests(unittest.TestCase):
    """Old tokens (with stale operations, or without role) continue to work."""

    def test_old_token_with_role_gets_current_scopes(self) -> None:
        """Token minted with old operations but valid role → gets CURRENT role scopes."""
        # Executor token minted before queue_close and task_progress were added
        old_executor = _cap("executor", operations=["queue_claim", "queue_return"])
        with request_capability_scope(old_executor):
            # Gets current ROLE_SPECS scopes, not just baked operations
            self.assertTrue(_capability_has_scope("queue_claim"))
            self.assertTrue(_capability_has_scope("queue_return"))
            self.assertTrue(_capability_has_scope("queue_close"))  # added later
            self.assertTrue(_capability_has_scope("task_progress"))  # added later

    def test_legacy_token_without_role_falls_back_to_operations(self) -> None:
        """Token without role field → falls back to baked operations (backward compat)."""
        legacy_cap = {
            "token_ref": "legacy-token",
            "operations": ["queue_claim", "queue_return"],
            "expires_at": _VALID,
            "source": "service_v0",
            # NO role field
        }
        with request_capability_scope(legacy_cap):
            self.assertTrue(_capability_has_scope("queue_claim"))
            self.assertTrue(_capability_has_scope("queue_return"))
            self.assertFalse(_capability_has_scope("owner_confirm"))

    def test_legacy_token_without_role_cannot_exceed_operations(self) -> None:
        """Legacy token without role can only use its baked operations."""
        legacy_cap = {
            "token_ref": "legacy-token",
            "operations": ["queue_claim"],
            "expires_at": _VALID,
            "source": "service_v0",
        }
        with request_capability_scope(legacy_cap):
            self.assertTrue(_capability_has_scope("queue_claim"))
            self.assertFalse(_capability_has_scope("queue_return"))
            self.assertFalse(_capability_has_scope("owner_confirm"))


# ---------------------------------------------------------------------------
# 3. 零放宽 (zero widening)
# ---------------------------------------------------------------------------

class ZeroWideningTests(unittest.TestCase):
    """Every scope gate decision is identical to pre-change behavior for same role+scope."""

    def _check_role_scopes(self, role: str, expected_scopes: list[str],
                           denied_scopes: list[str]) -> None:
        cap = _cap(role, operations=expected_scopes)
        with request_capability_scope(cap):
            for scope in expected_scopes:
                self.assertTrue(_capability_has_scope(scope),
                                f"{role} should have {scope}")
            for scope in denied_scopes:
                self.assertFalse(_capability_has_scope(scope),
                                 f"{role} should NOT have {scope}")

    def test_executor_scopes_unchanged(self) -> None:
        self._check_role_scopes(
            "executor",
            expected_scopes=["queue_claim", "queue_return", "queue_close", "task_progress", "bench_audit_submit"],
            denied_scopes=["owner_confirm", "draft_publish", "audit_verdict",
                           "audit_dispatch", "intake_submit", "owner_decision_record",
                           "queue_amend", "queue_withdraw", "draft_submit", "bench_audit_confirm"],
        )

    def test_owner_scopes_unchanged(self) -> None:
        self._check_role_scopes(
            "owner",
            expected_scopes=["queue_claim", "queue_return", "owner_confirm",
                             "draft_publish", "owner_decision_record",
                             "queue_amend", "queue_withdraw"],
            denied_scopes=["audit_verdict", "audit_dispatch", "intake_submit",
                           "task_progress", "queue_close", "draft_submit"],
        )

    def test_auditor_scopes_unchanged(self) -> None:
        self._check_role_scopes(
            "auditor",
            expected_scopes=["queue_claim", "audit_verdict", "task_progress"],
            denied_scopes=["owner_confirm", "draft_publish", "queue_return",
                           "queue_close", "intake_submit", "audit_dispatch",
                           "queue_amend", "queue_withdraw"],
        )

    def test_owner_dispatch_scopes_unchanged(self) -> None:
        self._check_role_scopes(
            "owner-dispatch",
            expected_scopes=["audit_dispatch"],
            denied_scopes=["audit_verdict", "queue_claim", "owner_confirm",
                           "draft_publish", "queue_close"],
        )

    def test_copilot_scopes_unchanged(self) -> None:
        self._check_role_scopes(
            "copilot",
            expected_scopes=[],
            denied_scopes=["queue_claim", "owner_confirm", "draft_publish",
                           "audit_verdict", "intake_submit", "task_progress"],
        )

    def test_planner_scopes_unchanged(self) -> None:
        self._check_role_scopes(
            "planner",
            expected_scopes=["draft_submit", "draft_publish"],
            denied_scopes=["queue_claim", "queue_return", "owner_confirm",
                           "audit_verdict", "intake_submit", "task_progress",
                           "queue_close", "queue_amend", "queue_withdraw"],
        )

    def test_unknown_role_denied(self) -> None:
        """Unknown role → fail-closed (all scopes denied)."""
        cap = _cap("nonexistent-role", operations=["queue_claim"])
        with request_capability_scope(cap):
            self.assertFalse(_capability_has_scope("queue_claim"))
            self.assertFalse(_capability_has_scope("owner_confirm"))


# ---------------------------------------------------------------------------
# 4. tools/list visibility matches real-time scope
# ---------------------------------------------------------------------------

class ToolsListVisibilityTests(unittest.TestCase):
    """tools/list filtering uses real-time ROLE_SPECS resolution."""

    def test_executor_sees_current_tools(self) -> None:
        """Executor token sees tools for ALL current executor scopes."""
        cap = _cap("executor", operations=["queue_claim", "queue_return"])  # old baked ops
        with request_capability_scope(cap):
            descriptors = visible_tool_descriptors()
            names = [d["name"] for d in descriptors]
            # Read tools always visible
            self.assertIn("lybra_queue_list", names)
            # queue_claim scope tools
            self.assertIn("lybra_queue_claim_dry_run", names)
            # queue_return scope tools
            self.assertIn("lybra_queue_return_dry_run", names)
            # queue_close scope tools (added after this token was minted)
            self.assertIn("lybra_queue_close_dry_run", names)
            # task_progress scope tools (added after this token was minted)
            self.assertIn("lybra_task_progress", names)
            # Owner-only tools NOT visible
            self.assertNotIn("lybra_draft_publish_dry_run", names)
            self.assertNotIn("lybra_audit_verdict_dry_run", names)

    def test_copilot_sees_only_read_tools(self) -> None:
        """Copilot (scopes=[]) sees only read tools."""
        cap = _cap("copilot", operations=[])
        with request_capability_scope(cap):
            descriptors = visible_tool_descriptors()
            names = [d["name"] for d in descriptors]
            self.assertIn("lybra_queue_list", names)
            self.assertIn("lybra_validate", names)
            # No write tools
            self.assertNotIn("lybra_queue_claim_dry_run", names)
            self.assertNotIn("lybra_intake_submit_dry_run", names)

    def test_planner_sees_draft_tools(self) -> None:
        """Planner sees draft_submit and draft_publish tools."""
        cap = _cap("planner", operations=["draft_submit"])  # old token without draft_publish
        with request_capability_scope(cap):
            descriptors = visible_tool_descriptors()
            names = [d["name"] for d in descriptors]
            self.assertIn("lybra_draft_submit_dry_run", names)
            self.assertIn("lybra_draft_publish_dry_run", names)  # gained from ROLE_SPECS
            self.assertNotIn("lybra_queue_claim_dry_run", names)


# ---------------------------------------------------------------------------
# 5. Identity checks preserved
# ---------------------------------------------------------------------------

class IdentityChecksTests(unittest.TestCase):
    """Expiry, token_ref, and other identity checks are NOT weakened."""

    def test_expired_token_denied(self) -> None:
        """Expired token → all scopes denied regardless of role."""
        cap = _cap("executor", expires_at=_EXPIRED)
        with request_capability_scope(cap):
            self.assertFalse(_capability_has_scope("queue_claim"))
            self.assertFalse(_capability_has_scope("queue_return"))

    def test_missing_token_ref_denied(self) -> None:
        """Token without token_ref → denied."""
        cap = {
            "role": "executor",
            "expires_at": _VALID,
            "source": "service_v0",
            # no token_ref
        }
        with request_capability_scope(cap):
            self.assertFalse(_capability_has_scope("queue_claim"))

    def test_missing_expires_at_denied(self) -> None:
        """Token without expires_at → denied."""
        cap = {
            "token_ref": "test",
            "role": "executor",
            "source": "service_v0",
            # no expires_at
        }
        with request_capability_scope(cap):
            self.assertFalse(_capability_has_scope("queue_claim"))

    def test_empty_role_with_valid_operations_still_works(self) -> None:
        """Empty role + valid operations → backward compat via operations fallback."""
        cap = {
            "token_ref": "test",
            "role": "",
            "operations": ["queue_claim"],
            "expires_at": _VALID,
            "source": "service_v0",
        }
        with request_capability_scope(cap):
            self.assertTrue(_capability_has_scope("queue_claim"))

    def test_no_capability_token_all_denied(self) -> None:
        """No capability token at all → all scopes denied."""
        with request_capability_scope(None):
            self.assertFalse(_capability_has_scope("queue_claim"))
            self.assertFalse(_capability_has_scope("owner_confirm"))

    def test_empty_capability_dict_all_denied(self) -> None:
        """Empty capability dict → all scopes denied."""
        with request_capability_scope({}):
            self.assertFalse(_capability_has_scope("queue_claim"))


# ---------------------------------------------------------------------------
# scope_basis echo
# ---------------------------------------------------------------------------

class ScopeBasisEchoTests(unittest.TestCase):
    """scope_basis in tool results echoes real-time resolved scopes."""

    def test_scope_basis_shows_resolved_scopes(self) -> None:
        """scope_basis['scopes'] shows ROLE_SPECS-resolved scopes, not baked operations."""
        cap = _cap("executor", operations=["queue_claim", "queue_return"])
        with request_capability_scope(cap):
            result = _tool_result({"ok": True})
            basis = result["structuredContent"]["scope_basis"]
            # Real-time resolved: executor has 5 scopes in ROLE_SPECS (AIPOS-336F1: +bench_audit_submit)
            self.assertEqual(basis["scopes"],
                             ["queue_claim", "queue_return", "queue_close", "task_progress", "bench_audit_submit"])
            # Minted (baked) operations echoed separately
            self.assertEqual(basis.get("minted_scopes"), ["queue_claim", "queue_return"])

    def test_scope_basis_no_minted_when_identical(self) -> None:
        """When minted == resolved, no minted_scopes field (clean output)."""
        cap = _cap("executor",
                    operations=["queue_claim", "queue_return", "queue_close", "task_progress", "bench_audit_submit"])
        with request_capability_scope(cap):
            result = _tool_result({"ok": True})
            basis = result["structuredContent"]["scope_basis"]
            self.assertEqual(basis["scopes"],
                             ["queue_claim", "queue_return", "queue_close", "task_progress", "bench_audit_submit"])
            self.assertNotIn("minted_scopes", basis)

    def test_scope_basis_legacy_token_no_role(self) -> None:
        """Legacy token without role → scope_basis falls back to operations."""
        cap = {
            "token_ref": "legacy",
            "operations": ["queue_claim"],
            "expires_at": _VALID,
            "source": "service_v0",
        }
        with request_capability_scope(cap):
            result = _tool_result({"ok": True})
            basis = result["structuredContent"]["scope_basis"]
            self.assertEqual(basis["scopes"], ["queue_claim"])


# ---------------------------------------------------------------------------
# _resolve_role_scopes helper
# ---------------------------------------------------------------------------

class ResolveRoleScopesTests(unittest.TestCase):
    """Unit tests for the _resolve_role_scopes helper."""

    def test_known_roles(self) -> None:
        self.assertIn("queue_claim", _resolve_role_scopes("executor"))
        self.assertIn("owner_confirm", _resolve_role_scopes("owner"))
        self.assertIn("audit_verdict", _resolve_role_scopes("auditor"))
        self.assertIn("audit_dispatch", _resolve_role_scopes("owner-dispatch"))
        self.assertEqual(_resolve_role_scopes("copilot"), [])
        self.assertIn("draft_submit", _resolve_role_scopes("planner"))
        self.assertIn("draft_publish", _resolve_role_scopes("planner"))

    def test_unknown_role_returns_empty(self) -> None:
        self.assertEqual(_resolve_role_scopes("nonexistent"), [])
        self.assertEqual(_resolve_role_scopes(""), [])


if __name__ == "__main__":
    unittest.main()
