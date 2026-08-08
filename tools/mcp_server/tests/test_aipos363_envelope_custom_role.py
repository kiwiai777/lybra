"""AIPOS-363 S4 — envelope covers AIPOS-352 custom roles (agent_or_role by role OR instance).

Pins (card S4 + 362 坑2):
- an Owner-signed envelope naming a CUSTOM role (e.g. agent_or_role: kaia-asst) auto-releases
  a claim made by an agent whose capability-token role is exactly that custom role, even though
  the concrete agent_instance differs (the 362 坑2 stop-all fix);
- NOT loosened: an agent whose token role is a DIFFERENT role still falls back to Supervised
  (偏窄 fail-safe preserved — uncovered roles still stop);
- direct unit pin on match_claim_envelope: claiming_role is matched alongside instance/actor.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from tools.aipos_cli.autonomy_policy import match_claim_envelope
from tools.aipos_cli.confirm_client import GateClient
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server


def _policy(agent_or_role: str = "kaia-asst", *, task_mode: str = "code", max_tasks: int = 5) -> dict[str, Any]:
    return {
        "policy_id": "pol_agency_1",
        "mode": "PreAuthorized",
        "status": "active",
        "approved_by_owner": True,
        "owner_approval_ref": "owner:arm",
        "active_from": "2020-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z",
        "agent_or_role": agent_or_role,
        "task_selector_task_mode": task_mode,
        "task_selector_project": "",
        "task_selector_task_ids": [],
        "max_tasks": max_tasks,
    }


class MatchClaimEnvelopeCustomRoleTests(unittest.TestCase):
    """Direct unit pins on match_claim_envelope (AIPOS-363 S4)."""

    NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)

    def test_custom_role_matches_when_instance_differs(self) -> None:
        """362 坑2 fix: policy names the custom ROLE; claiming agent is a concrete INSTANCE of
        that role (instance != role string). Before S4 this missed → stop-all."""
        ok, reason = match_claim_envelope(
            policy=_policy("kaia-asst"), task_id="T-1", task_mode="code", project="agency",
            agent_instance="exec.agency.kaia-asst", actor="exec.agency.kaia-asst",
            now=self.NOW, released_count=0, claiming_role="kaia-asst",
        )
        self.assertTrue(ok, reason)

    def test_builtin_role_name_matches(self) -> None:
        ok, reason = match_claim_envelope(
            policy=_policy("executor"), task_id="T-1", task_mode="code", project="agency",
            agent_instance="exec.cc", actor="exec.cc",
            now=self.NOW, released_count=0, claiming_role="executor",
        )
        self.assertTrue(ok, reason)

    def test_uncovered_role_still_falls_back(self) -> None:
        """不放宽: agent of a DIFFERENT role is not covered by an envelope naming kaia-asst."""
        ok, reason = match_claim_envelope(
            policy=_policy("kaia-asst"), task_id="T-1", task_mode="code", project="agency",
            agent_instance="exec.other", actor="exec.other",
            now=self.NOW, released_count=0, claiming_role="some-other-role",
        )
        self.assertFalse(ok)
        self.assertIn("not covered", reason)

    def test_instance_match_still_works_without_role(self) -> None:
        """Backward-compat: an envelope naming the concrete instance still matches even when no
        role is supplied (claiming_role=None)."""
        ok, reason = match_claim_envelope(
            policy=_policy("exec.agency.kaia-asst"), task_id="T-1", task_mode="code", project="agency",
            agent_instance="exec.agency.kaia-asst", actor="exec.agency.kaia-asst",
            now=self.NOW, released_count=0, claiming_role=None,
        )
        self.assertTrue(ok, reason)

    def test_role_class_is_not_auto_matched(self) -> None:
        """不放宽 (strict): an envelope naming the builtin class `executor` does NOT cover a custom
        role of that class unless the policy literally names it. claiming_role=kaia-asst (class
        executor) vs agent_or_role=executor → NOT covered (owner must name the custom role)."""
        ok, _reason = match_claim_envelope(
            policy=_policy("executor"), task_id="T-1", task_mode="code", project="agency",
            agent_instance="exec.agency.kaia-asst", actor="exec.agency.kaia-asst",
            now=self.NOW, released_count=0, claiming_role="kaia-asst",
        )
        self.assertFalse(ok, "role-class matching would loosen coverage; owner must name the role")


def _registry_custom_role() -> dict[str, dict[str, Any]]:
    """A custom-role agent token (role=kaia-asst, role_class=executor) bound to a concrete instance."""
    return {
        "owner-secret": {
            "role": "owner", "token_ref": "svc-owner",
            "scopes": ["queue_claim", "queue_return", "owner_confirm", "owner_decision_record"],
            "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:ownfp363",
        },
        "kaia-asst-secret": {
            "role": "kaia-asst", "role_class": "executor", "token_ref": "svc-kaia-asst",
            "scopes": ["queue_claim", "queue_return"],
            "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:kafp363",
            "agent_instance": "exec.agency.kaia-asst",
        },
        "other-secret": {
            "role": "other-role", "role_class": "executor", "token_ref": "svc-other",
            "scopes": ["queue_claim", "queue_return"],
            "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:otfp363",
            "agent_instance": "exec.agency.other",
        },
    }


def _pending(task_id: str, *, agent: str = "exec.agency.kaia-asst", task_mode: str = "code") -> str:
    return "\n".join([
        "---",
        f"task_id: {task_id}",
        f"title: 363 envelope custom-role {task_id}",
        "project: agency",
        f"assigned_to: {agent}",
        f"agent_instance: {agent}",
        f"context_bundle: {agent}",
        f"task_mode: {task_mode}",
        "priority: medium",
        "status: pending",
        "created_by: t",
        "needs_owner: false",
        "output_target: docs/",
        "artifact_policy: formal_write",
        "---",
        "body",
    ])


def _policy_block(agent_or_role: str, *, task_mode: str = "code", max_tasks: int = 5) -> dict[str, Any]:
    return {
        "policy_id": "pol_agency_1",
        "agent_or_role": agent_or_role,
        "active_from": "2020-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z",
        "max_tasks": max_tasks,
        "task_selector": {"task_mode": task_mode},
    }


class EnvelopeCustomRoleGateTests(unittest.TestCase):
    """Real HTTP gate: an envelope naming a custom role auto-releases a custom-role agent;
    an agent of a different role still falls back to Supervised (362 坑2 live fix)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @contextmanager
    def gate(self) -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST, port=0, token="", keepalive_seconds=0.01,
            max_keepalive_events=1, service_role_registry=_registry_custom_role(),
        )
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown()
                thread.join(timeout=2)
                httpd.server_close()

    def _grant(self, owner: GateClient, agent_or_role: str) -> None:
        payload = {
            "decision_id": "pol-decision-agency-1", "actor": "owner", "decided_by_ref": "owner",
            "decision_summary": "Arm envelope for custom role.",
            "autonomy_policy": _policy_block(agent_or_role),
        }
        dry = owner.call_tool("lybra_owner_decision_record_dry_run", payload)
        self.assertTrue(dry.get("dry_run_token"), f"grant dry-run failed: {dry}")
        conf = owner.call_tool(
            "lybra_owner_decision_record_confirm",
            {"dry_run_token": dry["dry_run_token"], "actor": "owner", "owner_confirmation_token": "OWNER_CONFIRMED"},
        )
        self.assertTrue(conf.get("ok"), f"grant confirm failed: {conf}")

    def _claim(self, client: GateClient, task_id: str, instance: str, policy_ref: str) -> dict[str, Any]:
        return client.call_tool("lybra_queue_claim_dry_run", {
            "task_id": task_id, "actor": instance, "agent_instance": instance,
            "autonomy_mode": "PreAuthorized", "owner_policy_ref": policy_ref,
            "active_session_id": f"session_{task_id}",
        })

    def test_custom_role_envelope_auto_releases(self) -> None:
        """362 坑2 fix (live): pol_agency_1.agent_or_role=kaia-asst covers the kaia-asst agent even
        though its concrete instance is exec.agency.kaia-asst → no stop-all, auto-release."""
        (self.repo_root / "5_tasks" / "queue" / "pending" / "agency-task-1.md").write_text(
            _pending("AGENCY-TASK-1", agent="exec.agency.kaia-asst"), encoding="utf-8")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret"); owner.initialize()
            self._grant(owner, agent_or_role="kaia-asst")
            agent = GateClient(url, "kaia-asst-secret"); agent.initialize()
            claim = self._claim(agent, "AGENCY-TASK-1", "exec.agency.kaia-asst", "pol_agency_1")
            self.assertTrue(claim.get("ok"), f"custom-role envelope should auto-release: {claim}")
            self.assertEqual(claim.get("autonomy_mode"), "PreAuthorized", claim)
            self.assertTrue(claim.get("preauthorized_release"), claim)
        self.assertTrue(list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md")))

    def test_different_role_not_covered_falls_back(self) -> None:
        """不放宽 (live): an agent of role other-role claiming under pol_agency_1 (kaia-asst)
        falls back to Supervised — uncovered roles still stop."""
        (self.repo_root / "5_tasks" / "queue" / "pending" / "agency-task-2.md").write_text(
            _pending("AGENCY-TASK-2", agent="exec.agency.other"), encoding="utf-8")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret"); owner.initialize()
            self._grant(owner, agent_or_role="kaia-asst")
            other = GateClient(url, "other-secret"); other.initialize()
            claim = self._claim(other, "AGENCY-TASK-2", "exec.agency.other", "pol_agency_1")
            self.assertNotEqual(claim.get("autonomy_mode"), "PreAuthorized", claim)
            self.assertTrue(claim.get("owner_confirmation_required"), claim)
        self.assertTrue(list((self.repo_root / "5_tasks" / "queue" / "pending").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
