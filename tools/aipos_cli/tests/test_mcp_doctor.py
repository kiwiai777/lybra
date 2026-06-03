from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from tools.aipos_cli.aipos_cli import build_mcp_doctor_report, main


class McpDoctorTests(unittest.TestCase):
    def capability_token(self, operations: list[str]) -> str:
        return json.dumps(
            {
                "token_ref": "cap_mcp_doctor_test",
                "operations": operations,
                "expires_at": "2999-01-01T00:00:00Z",
            }
        )

    def test_report_distinguishes_bearer_auth_from_capability_scopes(self) -> None:
        report = build_mcp_doctor_report(
            {
                "LYBRA_MCP_TOKEN": "transport-secret-value",
                "LYBRA_CAPABILITY_TOKEN": self.capability_token(["queue_claim", "queue_return"]),
            }
        )

        self.assertTrue(report["ok"])
        self.assertTrue(report["transport_auth"]["present"])
        self.assertTrue(report["capability_scope"]["present"])
        self.assertEqual(report["capability_scope"]["operations"], ["queue_claim", "queue_return"])
        self.assertEqual(report["tool_visibility"]["queue_claim"], "visible")
        self.assertEqual(report["tool_visibility"]["queue_return"], "visible")
        self.assertNotIn("transport-secret-value", json.dumps(report))

    def test_cli_outputs_redacted_human_diagnostics(self) -> None:
        stdout = StringIO()
        env = {
            "LYBRA_MCP_TOKEN": "bearer-secret",
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(["queue_claim"]),
        }

        with patch.dict("os.environ", env, clear=True), redirect_stdout(stdout):
            exit_code = main(["mcp", "doctor"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Bearer lets the MCP client connect", output)
        self.assertIn("lybra_queue_claim_*: visible", output)
        self.assertIn("lybra_queue_return_*: hidden", output)
        self.assertNotIn("bearer-secret", output)

    def test_invalid_capability_token_is_visible_without_printing_raw_value(self) -> None:
        report = build_mcp_doctor_report(
            {
                "LYBRA_MCP_TOKEN": "transport-secret-value",
                "LYBRA_CAPABILITY_TOKEN": "not-json-secret",
            }
        )

        self.assertFalse(report["ok"])
        self.assertIn("LYBRA_CAPABILITY_TOKEN is not valid JSON", report["diagnostics"])
        self.assertNotIn("not-json-secret", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
