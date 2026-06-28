"""AIPOS-229 Slice 5 — token project ENFORCEMENT (PROJECT_SCOPE_DENIED) end-to-end.

Covers: R-α "18 gated / 0 exempt" enumeration over TOOL_HANDLERS; the inverse of the Slice-4
flip-case (mismatch now DENIES); R-ii back-compat (no `projects` => never resolves the active
project, never newly denied); ordering (project gate precedes the operation gate); fail-closed
(projects present + unresolvable active project => deny, never silent allow).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.mcp_server import tools as gate
from tools.mcp_server.tools import (
    TOOL_HANDLERS,
    _project_gate_denied,
    dispatch_tool,
    request_capability_scope,
    visible_tool_descriptors,
)

_VALID = "2999-01-01T00:00:00Z"


def _cap(*, projects=None, operations=("queue_claim",)):
    cap = {
        "token_ref": "t",
        "role": "executor",
        "operations": list(operations),
        "expires_at": _VALID,
        "source": "service_v0",
    }
    if projects is not None:
        cap["projects"] = list(projects)
        cap["projects_enforced"] = True
    return cap


def _err(result):
    return (result.get("structuredContent") or {}).get("error_code")


class ProjectEnforcementTests(unittest.TestCase):
    # --- R-α: 18 gated / 0 exempt --------------------------------------------------------------
    def test_all_tool_handlers_are_project_gated_no_exemptions(self) -> None:
        self.assertEqual(len(TOOL_HANDLERS), 18)  # contract: a new tool must be counted + gated
        with patch.object(gate, "_repo_root", return_value="/tmp/x"), patch.object(
            gate, "_resolve_active_project_for", return_value="proj-A"
        ), request_capability_scope(_cap(projects=["other-proj"])):
            gated = 0
            for name in TOOL_HANDLERS:
                result = dispatch_tool(name, {})
                self.assertEqual(_err(result), "PROJECT_SCOPE_DENIED", name)
                gated += 1
            self.assertEqual(gated, len(TOOL_HANDLERS))  # every handler, incl. the 4 read tools

    # --- inverse flip-case ---------------------------------------------------------------------
    def test_inverse_flip_case_mismatch_now_denies(self) -> None:
        with patch.object(gate, "_repo_root", return_value="/tmp/x"), patch.object(
            gate, "_resolve_active_project_for", return_value="proj-A"
        ), request_capability_scope(_cap(projects=["proj-B"])):
            self.assertEqual(_err(dispatch_tool("lybra_queue_list", {})), "PROJECT_SCOPE_DENIED")

    def test_match_passes_project_gate(self) -> None:
        # projects contains the active project -> project gate allows (None), falls through to handler.
        with patch.object(gate, "_repo_root", return_value="/tmp/x"), patch.object(
            gate, "_resolve_active_project_for", return_value="proj-A"
        ), request_capability_scope(_cap(projects=["proj-A"])):
            self.assertIsNone(_project_gate_denied())

    # --- R-ii back-compat: absent projects never resolves -------------------------------------
    def test_absent_projects_never_resolves_active_project(self) -> None:
        # Hard proof: with NO projects field the resolver is never called, so a token in an
        # ambiguous workspace is NOT newly denied. Patched resolver RAISES if reached.
        def _boom(*a, **k):
            raise AssertionError("active_project must NOT be resolved when projects is absent")

        with patch.object(gate, "_repo_root", return_value="/tmp/x"), patch.object(
            gate, "_resolve_active_project_for", side_effect=_boom
        ), request_capability_scope(_cap(projects=None)):
            self.assertIsNone(_project_gate_denied())

    # --- fail-closed --------------------------------------------------------------------------
    def test_unresolvable_active_project_with_projects_present_denies(self) -> None:
        with patch.object(gate, "_repo_root", return_value="/tmp/x"), patch.object(
            gate, "_resolve_active_project_for", side_effect=ValueError("PROJECT_AMBIGUOUS")
        ), request_capability_scope(_cap(projects=["proj-A"])):
            denied = _project_gate_denied()
            self.assertIsNotNone(denied)
            self.assertEqual(_err(denied), "PROJECT_SCOPE_DENIED")

    # --- ordering: project gate precedes operation gate --------------------------------------
    def test_project_gate_precedes_operation_gate(self) -> None:
        # A token that would fail BOTH (wrong project AND lacking the op scope) is denied by the
        # PROJECT gate first.
        with patch.object(gate, "_repo_root", return_value="/tmp/x"), patch.object(
            gate, "_resolve_active_project_for", return_value="proj-A"
        ), request_capability_scope(_cap(projects=["proj-B"], operations=[])):
            self.assertEqual(_err(dispatch_tool("lybra_intake_submit_dry_run", {})), "PROJECT_SCOPE_DENIED")

    # --- introspection reflects the gate -----------------------------------------------------
    def test_visible_descriptors_empty_on_project_mismatch(self) -> None:
        with patch.object(gate, "_repo_root", return_value="/tmp/x"), patch.object(
            gate, "_resolve_active_project_for", return_value="proj-A"
        ), request_capability_scope(_cap(projects=["proj-B"])):
            self.assertEqual(visible_tool_descriptors(), [])

    def test_no_lybra_literal_in_board_adapter_source(self) -> None:
        # AIPOS-229 §4: the audit-dispatch "lybra" literal is de-hardcoded — no `or "lybra"` fallback.
        import tools.aipos_cli.board_adapter as ba

        with open(ba.__file__, encoding="utf-8") as handle:
            src = handle.read()
        self.assertNotIn('or "lybra"', src)


if __name__ == "__main__":
    unittest.main()
