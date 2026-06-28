"""AIPOS-228/229 — the OPERATION-scope gate (★A1) is INERT to `projects`.

`_capability_has_scope` reads only operations/token_ref/expires_at, never `projects`. These tests
assert that the operation-scope decision is byte-identical with and without `projects` — INCLUDING
a token whose projects do NOT contain the active project. This is the ★A1-not-weakened anchor for
AIPOS-229: project ENFORCEMENT lives at the dispatch choke-point (`_project_gate_denied` /
`dispatch_tool`), NOT in the operation-scope gate, which stays unchanged. (The end-to-end
PROJECT_SCOPE_DENIED behavior is covered in test_token_project_enforcement.) As of Slice 5 the
scope_basis echo marks `projects_enforced: true`.
"""

from __future__ import annotations

import unittest

from tools.mcp_server.tools import (
    _capability_has_scope,
    _tool_result,
    request_capability_scope,
)

_VALID = "2999-01-01T00:00:00Z"


def _cap(operations, *, projects=None):
    cap = {
        "token_ref": "test-token",
        "role": "executor",
        "operations": list(operations),
        "expires_at": _VALID,
        "source": "service_v0",
    }
    if projects is not None:
        cap["projects"] = list(projects)
        cap["projects_enforced"] = True
    return cap


class TokenProjectsGateInertTests(unittest.TestCase):
    def test_scope_decision_identical_with_and_without_projects(self) -> None:
        # Same operations; projects present (matching the active project) vs absent -> identical.
        for scope in ("queue_claim", "queue_return"):
            with request_capability_scope(_cap(["queue_claim", "queue_return"])):
                without = _capability_has_scope(scope)
            with request_capability_scope(_cap(["queue_claim", "queue_return"], projects=["lybra"])):
                with_field = _capability_has_scope(scope)
            self.assertTrue(without)
            self.assertEqual(without, with_field, scope)

    def test_flip_case_projects_mismatch_still_allows(self) -> None:
        # ★ R-a: a token carrying projects that do NOT contain the requested active project — the
        # exact case Slice 5 will DENY — must STILL be allowed in Slice 4 (field is inert).
        with request_capability_scope(_cap(["queue_claim"])):
            baseline = _capability_has_scope("queue_claim")
        with request_capability_scope(_cap(["queue_claim"], projects=["some-other-project"])):
            mismatch = _capability_has_scope("queue_claim")
        self.assertTrue(baseline)
        self.assertEqual(baseline, mismatch)  # projects mismatch does NOT deny in Slice 4

    def test_denied_scope_stays_denied_regardless_of_projects(self) -> None:
        # A scope NOT granted is denied whether or not projects is present (no widening).
        with request_capability_scope(_cap(["queue_claim"])):
            self.assertFalse(_capability_has_scope("owner_confirm"))
        with request_capability_scope(_cap(["queue_claim"], projects=["lybra"])):
            self.assertFalse(_capability_has_scope("owner_confirm"))

    def test_scope_basis_echoes_projects_only_when_present(self) -> None:
        with request_capability_scope(_cap(["queue_claim"], projects=["lybra"])):
            result = _tool_result({"ok": True})
        basis = result["structuredContent"]["scope_basis"]
        self.assertEqual(basis.get("projects"), ["lybra"])
        self.assertEqual(basis.get("projects_enforced"), True)

        with request_capability_scope(_cap(["queue_claim"])):
            result2 = _tool_result({"ok": True})
        basis2 = result2["structuredContent"]["scope_basis"]
        self.assertNotIn("projects", basis2)  # absence byte-stable
        self.assertNotIn("projects_enforced", basis2)


if __name__ == "__main__":
    unittest.main()
