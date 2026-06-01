from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from tools.aipos_cli.ai_assisted_authoring import (
    build_authoring_draft,
    build_live_authoring_draft,
    confirm_authoring_draft,
    confirm_live_authoring_draft,
)
from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter


class _LiveAdapterHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8")
        self.server.last_request = {
            "path": self.path,
            "headers": {key: value for key, value in self.headers.items()},
            "body": body,
        }
        response_payload = getattr(self.server, "response_payload", {})
        status_code = int(getattr(self.server, "response_code", 200))
        data = json.dumps(response_payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def _start_live_adapter_server(response_payload: dict[str, object], response_code: int = 200) -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LiveAdapterHandler)
    server.response_payload = response_payload
    server.response_code = response_code
    server.last_request = None
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _FakeLiveAdapterResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeLiveAdapterResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def _live_urlopen_stub(response_payload: dict[str, object], capture: dict[str, object]) -> Any:
    def _stub(request: object, timeout: object = None) -> _FakeLiveAdapterResponse:
        capture["url"] = getattr(request, "full_url", None)
        capture["headers"] = {key: value for key, value in getattr(request, "header_items")()}
        body = getattr(request, "data", b"")
        capture["body"] = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
        capture["timeout"] = timeout
        return _FakeLiveAdapterResponse(response_payload)

    return _stub


class AiAssistedAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def intent(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "intent_id": "intent-demo-001",
            "submitted_at": "2026-06-01T12:00:00Z",
            "submitted_by": "owner",
            "requirement": "Prepare a concise status summary.",
        }
        data.update(overrides)
        return data

    def live_response(self, *, intent_id: str = "intent-live-001", task_id: str = "LIVE-CLI-01") -> dict[str, object]:
        return {
            "status": "drafted",
            "adapter_id": "live-http-json-v1",
            "provider_ref": "provider-neutral",
            "endpoint_ref": "http://127.0.0.1/live-authoring",
            "model_ref": "demo-model",
            "request_config_ref": "live-default",
            "token_cost_estimate": {
                "input_tokens": 140,
                "output_tokens": 240,
                "estimated_cost": "live-mock",
            },
            "proposal": {
                "frontmatter": {
                    "task_id": task_id,
                    "title": "Produce a live-slice status summary",
                    "project": "demo_project",
                    "assigned_to": "agent-01",
                    "agent_instance": "agent-01",
                    "context_bundle": "default_dev",
                    "task_mode": "docs",
                    "task_class": "simple",
                    "complexity_note": "Small documentation summary from live adapter output.",
                    "model_tier": "L1",
                    "priority": "medium",
                    "status": "pending",
                    "created_by": "ai-assisted-live",
                    "needs_owner": True,
                    "output_target": "workspace_artifacts/live-status-summary.md",
                    "artifact_policy": "formal_write",
                    "task_type": "one_shot",
                    "polling_mode": "agent_polling",
                    "claim_policy": "assigned_agent_only",
                    "report_mode": "completion_summary",
                    "recurrence": "none",
                },
                "body": "## Goal\n\nProduce one concise live-slice status summary.\n\n## Context\n\nUse only the supplied project context and preserve unresolved questions.\n\n## Acceptance Criteria\n\n- Produce one concise status summary.\n- Keep assumptions visible.\n\n## Completion Report Instructions\n\n- Report the output path and unresolved questions.\n",
            },
            "triage": {
                "recommended_task_class": "simple",
                "rationale": "The live slice remains bounded and independently completable.",
                "confidence": "high",
                "assumptions": [],
                "missing_information": [],
                "possible_owner_gates": [],
            },
            "assignment_recommendations": {
                "assigned_to": "agent-01",
                "agent_instance": "agent-01",
                "reviewer": None,
                "audit_by": None,
                "rationale": "Live adapter recommendation only.",
            },
            "warnings": [],
        }

    def confirm(self, preview: dict[str, object]) -> dict[str, object]:
        return confirm_authoring_draft(
            self.repo_root,
            preview,
            actor="owner",
            owner_confirmation_token="OWNER_CONFIRMED",
        )

    def test_preview_writes_nothing_then_confirm_writes_standard_draft_and_provenance(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(),
            fixture_id="standard-simple",
            actor="owner",
        )

        self.assertEqual(preview["verdict"], "NEEDS_OWNER")
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())
        self.assertFalse((self.repo_root / "5_tasks" / "records").exists())

        confirmed = self.confirm(preview)

        self.assertEqual(confirmed["verdict"], "PASS")
        draft = self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md"
        provenance = self.repo_root / confirmed["data"]["provenance_path"]
        self.assertTrue(draft.exists())
        self.assertTrue(provenance.exists())
        metadata, _body, warnings = parse_markdown_frontmatter(draft.read_text(encoding="utf-8"))
        self.assertEqual(warnings, [])
        self.assertEqual(metadata["task_class"], "simple")
        self.assertTrue(metadata["needs_owner"])

    def test_provenance_is_non_secret_sidecar_without_raw_prompt_or_response(self) -> None:
        confirmed = self.confirm(
            build_authoring_draft(self.repo_root, self.intent(), fixture_id="standard-simple", actor="owner")
        )
        provenance = self.repo_root / confirmed["data"]["provenance_path"]
        text = provenance.read_text(encoding="utf-8")

        self.assertIn("adapter_id: fixture-only-v1", text)
        self.assertIn("prompt_template_version: 1", text)
        self.assertIn("network_call_performed: false", text)
        self.assertIn("credential_read_performed: false", text)
        self.assertIn("raw_prompt_persisted: false", text)
        self.assertIn("raw_response_persisted: false", text)
        self.assertNotIn(self.intent()["requirement"], text)

    def test_injection_escalation_fixture_blocks_deterministically(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(intent_id="intent-injection"),
            fixture_id="injection-escalation",
            actor="owner",
        )

        self.assertEqual(preview["verdict"], "BLOCK")
        self.assertIn("AI-assisted proposal must keep needs_owner: true until Owner review", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: bypass_owner_review", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: request_credentials", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: authority_expansion", preview["blocking_reasons"])
        self.assertEqual(preview["planned_writes"], [])

    def test_fixture_adapter_failure_is_visible_and_writes_nothing(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(intent_id="intent-failure"),
            fixture_id="adapter-failure",
            actor="owner",
        )

        self.assertEqual(preview["verdict"], "BLOCK")
        self.assertIn("Fixture adapter failure for visible degradation testing.", preview["blocking_reasons"])
        self.assertEqual(preview["planned_writes"], [])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_manual_retry_relationship_is_recorded(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(intent_id="intent-retry", retry_of="authoring_previous"),
            fixture_id="standard-simple",
            actor="owner",
        )
        confirmed = self.confirm(preview)
        provenance = self.repo_root / confirmed["data"]["provenance_path"]

        self.assertIn("retry_of: authoring_previous", provenance.read_text(encoding="utf-8"))

    def test_stale_preview_blocks_confirm(self) -> None:
        preview = build_authoring_draft(self.repo_root, self.intent(), fixture_id="standard-simple", actor="owner")
        target = self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md"
        target.parent.mkdir(parents=True)
        target.write_text("occupied\n", encoding="utf-8")

        confirmed = self.confirm(preview)

        self.assertEqual(confirmed["verdict"], "BLOCK")
        self.assertIn("AI authoring preview snapshot mismatch; run draft again", confirmed["blocking_reasons"])

    def test_wrong_owner_token_blocks_confirm(self) -> None:
        preview = build_authoring_draft(self.repo_root, self.intent(), fixture_id="standard-simple", actor="owner")

        result = confirm_authoring_draft(
            self.repo_root,
            preview,
            actor="owner",
            owner_confirmation_token="WRONG",
        )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("owner confirmation token is required", result["blocking_reasons"][0])

    def test_fixture_only_flow_does_not_read_environment_credentials(self) -> None:
        with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "must-not-be-read"}):
            preview = build_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-no-creds"),
                fixture_id="standard-simple",
                actor="owner",
            )

        self.assertFalse(preview["data"]["credential_read_performed"])
        self.assertNotIn("must-not-be-read", json.dumps(preview))

    def test_live_adapter_preview_and_confirm_writes_standard_draft_and_provenance(self) -> None:
        endpoint_ref = "http://127.0.0.1:8787/live-authoring"
        capture: dict[str, object] = {}

        with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "live-secret"}), patch(
            "tools.aipos_cli.ai_assisted_authoring.urlopen",
            side_effect=_live_urlopen_stub({**self.live_response(), "endpoint_ref": endpoint_ref}, capture),
        ):
            preview = build_live_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-live-preview"),
                endpoint_ref=endpoint_ref,
                credential_ref="env:LYBRA_LLM_API_KEY",
                model_ref="demo-model",
                actor="owner",
            )

        self.assertEqual(preview["verdict"], "NEEDS_OWNER")
        self.assertEqual(capture["headers"]["Authorization"], "Bearer live-secret")
        request_body = json.loads(capture["body"])
        self.assertEqual(request_body["credential_ref"], "env:LYBRA_LLM_API_KEY")
        self.assertIn("messages", request_body)

        confirmed = confirm_live_authoring_draft(
            self.repo_root,
            preview,
            actor="owner",
            owner_confirmation_token="OWNER_CONFIRMED",
        )

        self.assertEqual(confirmed["verdict"], "PASS")
        draft = self.repo_root / "5_tasks" / "drafts" / "live-cli-01.md"
        provenance = self.repo_root / confirmed["data"]["provenance_path"]
        self.assertTrue(draft.exists())
        self.assertTrue(provenance.exists())
        text = provenance.read_text(encoding="utf-8")
        metadata, _body, _warnings = parse_markdown_frontmatter(text)
        self.assertEqual(metadata["credential_ref"], "env:LYBRA_LLM_API_KEY")
        self.assertEqual(metadata["provider_ref"], "provider-neutral")
        self.assertTrue(metadata["network_call_performed"])
        self.assertTrue(metadata["credential_read_performed"])
        self.assertFalse(metadata["raw_prompt_persisted"])
        self.assertFalse(metadata["raw_response_persisted"])
        self.assertNotIn("live-secret", text)
        self.assertNotIn(self.intent(intent_id="intent-live-preview")["requirement"], text)

    def test_live_adapter_missing_credential_ref_blocks_without_network_call(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("tools.aipos_cli.ai_assisted_authoring.urlopen") as mocked_urlopen:
            result = build_live_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-live-missing-credential"),
                endpoint_ref="http://127.0.0.1:65530/live-authoring",
                credential_ref="env:LYBRA_LLM_API_KEY",
                model_ref="demo-model",
                actor="owner",
            )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("credential_ref environment variable is missing", result["blocking_reasons"][0])
        mocked_urlopen.assert_not_called()
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_live_adapter_missing_proposal_blocks_visibly(self) -> None:
        endpoint_ref = "http://127.0.0.1:8787/live-authoring"
        with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "live-secret"}), patch(
            "tools.aipos_cli.ai_assisted_authoring.urlopen",
            side_effect=_live_urlopen_stub({"status": "drafted", "endpoint_ref": endpoint_ref}, {}),
        ):
            result = build_live_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-live-missing-proposal"),
                endpoint_ref=endpoint_ref,
                credential_ref="env:LYBRA_LLM_API_KEY",
                model_ref="demo-model",
                actor="owner",
            )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("proposal must be a mapping", result["blocking_reasons"][0])
        self.assertEqual(result["planned_writes"], [])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_live_adapter_network_failure_is_visible_with_zero_writes(self) -> None:
        with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "live-secret"}), patch(
            "tools.aipos_cli.ai_assisted_authoring.urlopen", side_effect=URLError("boom")
        ):
            result = build_live_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-live-network-failure"),
                endpoint_ref="http://127.0.0.1:65531/live-authoring",
                credential_ref="env:LYBRA_LLM_API_KEY",
                model_ref="demo-model",
                actor="owner",
            )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("live adapter network failure", result["blocking_reasons"][0])
        self.assertEqual(result["planned_writes"], [])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_live_adapter_http_error_is_visible_with_zero_writes(self) -> None:
        with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "live-secret"}), patch(
            "tools.aipos_cli.ai_assisted_authoring.urlopen",
            side_effect=HTTPError(
                url="http://127.0.0.1:65532/live-authoring",
                code=502,
                msg="bad gateway",
                hdrs=None,
                fp=None,
            ),
        ):
            result = build_live_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-live-http-error"),
                endpoint_ref="http://127.0.0.1:65532/live-authoring",
                credential_ref="env:LYBRA_LLM_API_KEY",
                model_ref="demo-model",
                actor="owner",
            )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("live adapter HTTP 502: bad gateway", result["blocking_reasons"][0])
        self.assertEqual(result["planned_writes"], [])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_live_adapter_timeout_is_visible_with_zero_writes(self) -> None:
        with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "live-secret"}), patch(
            "tools.aipos_cli.ai_assisted_authoring.urlopen", side_effect=TimeoutError("timed out")
        ):
            result = build_live_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-live-timeout"),
                endpoint_ref="http://127.0.0.1:65533/live-authoring",
                credential_ref="env:LYBRA_LLM_API_KEY",
                model_ref="demo-model",
                actor="owner",
            )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("live adapter timed out", result["blocking_reasons"][0])
        self.assertEqual(result["planned_writes"], [])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_cli_draft_and_confirm(self) -> None:
        intent_path = self.repo_root / "intent.json"
        intent_path.write_text(json.dumps(self.intent()), encoding="utf-8")
        previous_cwd = Path.cwd()
        try:
            os.chdir(self.repo_root)
            preview = self.cli_json(
                [
                    "ai-author",
                    "draft",
                    "--intent-json",
                    str(intent_path),
                    "--fixture",
                    "standard-simple",
                    "--actor",
                    "owner",
                    "--json",
                ]
            )
            preview_path = self.repo_root / "preview.json"
            preview_path.write_text(json.dumps(preview), encoding="utf-8")
            confirmed = self.cli_json(
                [
                    "ai-author",
                    "confirm",
                    "--from-json",
                    str(preview_path),
                    "--actor",
                    "owner",
                    "--owner-confirmation-token",
                    "OWNER_CONFIRMED",
                    "--json",
                ]
            )
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(confirmed["verdict"], "PASS")
        self.assertTrue((self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md").exists())

    def test_cli_live_draft_and_confirm(self) -> None:
        endpoint_ref = "http://127.0.0.1:8787/live-authoring"
        capture: dict[str, object] = {}
        intent_path = self.repo_root / "intent-live.json"
        intent_path.write_text(json.dumps(self.intent(intent_id="intent-live-cli")), encoding="utf-8")
        previous_cwd = Path.cwd()
        try:
            os.chdir(self.repo_root)
            with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "live-secret"}), patch(
                "tools.aipos_cli.ai_assisted_authoring.urlopen",
                side_effect=_live_urlopen_stub({**self.live_response(), "endpoint_ref": endpoint_ref}, capture),
            ):
                preview = self.cli_json(
                    [
                        "ai-author",
                        "live",
                        "draft",
                        "--intent-json",
                        str(intent_path),
                        "--endpoint-ref",
                        endpoint_ref,
                        "--credential-ref",
                        "env:LYBRA_LLM_API_KEY",
                        "--model-ref",
                        "demo-model",
                        "--actor",
                        "owner",
                        "--json",
                    ]
                )
            preview_path = self.repo_root / "live-preview.json"
            preview_path.write_text(json.dumps(preview), encoding="utf-8")
            confirmed = self.cli_json(
                [
                    "ai-author",
                    "live",
                    "confirm",
                    "--from-json",
                    str(preview_path),
                    "--actor",
                    "owner",
                    "--owner-confirmation-token",
                    "OWNER_CONFIRMED",
                    "--json",
                ]
            )
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(confirmed["verdict"], "PASS")
        self.assertEqual(capture["headers"]["Authorization"], "Bearer live-secret")
        self.assertTrue((self.repo_root / "5_tasks" / "drafts" / "live-cli-01.md").exists())

    def cli_json(self, argv: list[str]) -> dict[str, object]:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main(argv)
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
