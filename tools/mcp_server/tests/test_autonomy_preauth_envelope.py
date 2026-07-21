"""AIPOS-250 — autonomy tier: PreAuthorized envelope (claim) + capability-ledger fields.

Gate-side, real HTTP gate + real capability tokens (mirrors test_planner_role fixture).

Red lines pinned (card §1 + R-1/R-2 folds):
- 红线1/5: an Owner-signed active envelope that STRICTLY matches → claim auto-releases in ONE
  stage (no owner_confirm), and the claim record self-attributes autonomy_mode=PreAuthorized +
  owner_policy_ref=<policy_id> (positive content assertion, not a proxy).
- 红线3: a task OUTSIDE any envelope → claim falls back to Supervised (owner_confirmation_required,
  never a silent auto-release). Designed to be RED against a naive "PreAuthorized ⇒ release" impl.
- 红线1 (policy needs owner action): a policy that is not active / not approved_by_owner does NOT
  grant; arming an envelope through owner_decision_record requires owner_confirm + OWNER_CONFIRMED.
- 红线2 (bounded): expiry (expires_at) AND count (max_tasks) both drop matching claims to Supervised.
- ★A1: an executor presenting PreAuthorized + a FORGED owner_policy_ref (no such policy) gets a
  Supervised fallback, and can never self-confirm (no owner_confirm scope → SCOPE_DENIED).
- 红线4: return stays Supervised-only — autonomy_mode=PreAuthorized on return is INVALID.
- 红线6: actual_model / reported_tokens land in the records; the gate records but never verifies.
- R-2: the FORBIDDEN_QUEUE_CLAIM_FIELDS guardrail is untouched — those fields stay UNSUPPORTED.
"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import unittest

from tools.aipos_cli.confirm_client import GateClient
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server


def _registry() -> dict[str, dict[str, Any]]:
    return {
        "owner-secret": {
            "role": "owner",
            "token_ref": "svc-owner",
            "scopes": ["queue_claim", "queue_return", "owner_confirm", "owner_decision_record", "draft_publish"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:ownfp250",
        },
        "executor-secret": {
            "role": "executor",
            "token_ref": "svc-executor",
            "scopes": ["queue_claim", "queue_return"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:exfp250",
        },
    }


def _pending_task(task_id: str, *, task_mode: str = "code", project: str = "lybra", agent: str = "exec.cc") -> str:
    return "\n".join(
        [
            "---",
            f"task_id: {task_id}",
            f"title: Envelope test {task_id}",
            f"project: {project}",
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
        ]
    )


def _owner_decision_payload(decision_id: str, *, autonomy_policy: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    # AIPOS-250 relaxed grant path: the advisor supplies ONLY decision_id + the autonomy_policy block
    # (+ optional decision_summary/decided_by_ref). No hand-fabricated owner_approval_evidence — the
    # approval is the in-band harness owner_confirm; the gate synthesizes a truthful in-band marker.
    data: dict[str, Any] = {
        "decision_id": decision_id,
        "decision_summary": "Arm a bounded PreAuthorized claim envelope.",
        "decided_by_ref": "owner",
    }
    if autonomy_policy is not None:
        data["autonomy_policy"] = autonomy_policy
    data.update(overrides)
    return data


def _policy_block(
    policy_id: str,
    *,
    agent_or_role: str = "exec.cc",
    task_mode: str | None = "code",
    project: str | None = None,
    task_ids: list[str] | None = None,
    active_from: str = "2020-01-01T00:00:00Z",
    expires_at: str = "2999-01-01T00:00:00Z",
    max_tasks: int = 5,
) -> dict[str, Any]:
    selector: dict[str, Any] = {}
    if task_mode:
        selector["task_mode"] = task_mode
    if project:
        selector["project"] = project
    if task_ids:
        selector["task_ids"] = task_ids
    return {
        "policy_id": policy_id,
        "agent_or_role": agent_or_role,
        "active_from": active_from,
        "expires_at": expires_at,
        "max_tasks": max_tasks,
        "task_selector": selector,
    }


class PreAuthEnvelopeGateTests(unittest.TestCase):
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
            host=DEFAULT_HTTP_HOST,
            port=0,
            token="",
            keepalive_seconds=0.01,
            max_keepalive_events=1,
            service_role_registry=_registry(),
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

    # --- helpers ---

    def _seed_pending(self, task_id: str, **kw: Any) -> None:
        (self.repo_root / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md").write_text(
            _pending_task(task_id, **kw), encoding="utf-8"
        )

    def _grant_policy(self, owner: GateClient, decision_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        payload = _owner_decision_payload(decision_id, autonomy_policy=policy, actor="owner")
        dry = owner.call_tool("lybra_owner_decision_record_dry_run", payload)
        self.assertTrue(dry.get("dry_run_token"), f"policy grant dry-run failed: {dry}")
        self.assertTrue(dry.get("data", {}).get("autonomy_policy_grant"), dry)
        confirm = owner.call_tool(
            "lybra_owner_decision_record_confirm",
            {"dry_run_token": dry["dry_run_token"], "actor": "owner", "owner_confirmation_token": "OWNER_CONFIRMED"},
        )
        self.assertTrue(confirm.get("ok"), f"policy grant confirm failed: {confirm}")
        return confirm

    def _claim_records(self) -> list[dict[str, Any]]:
        root = self.repo_root / "5_tasks" / "records" / "claims"
        out: list[dict[str, Any]] = []
        if root.exists():
            for path in root.rglob("*.md"):
                md, _b, _w = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
                out.append(md)
        return out

    def _claim(self, client: GateClient, task_id: str, *, mode: str, policy_ref: str, **extra: Any) -> dict[str, Any]:
        args = {
            "task_id": task_id,
            "actor": "exec.cc",
            "agent_instance": "exec.cc",
            "autonomy_mode": mode,
            "owner_policy_ref": policy_ref,
            "active_session_id": f"session_{task_id}",
        }
        args.update(extra)
        return client.call_tool("lybra_queue_claim_dry_run", args)

    # --- test 1: 信封内 claim 自动放行 + 记录 PreAuthorized/owner_policy_ref ---

    def test_in_envelope_claim_auto_releases_and_records_preauthorized(self) -> None:
        self._seed_pending("AIPOS-ENV1")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            self._grant_policy(owner, "pol-decision-1", _policy_block("pol_env_1", task_mode="code"))

            ex = GateClient(url, "executor-secret")
            ex.initialize()
            claim = self._claim(ex, "AIPOS-ENV1", mode="PreAuthorized", policy_ref="pol_env_1")

            self.assertTrue(claim.get("ok"), f"in-envelope claim should auto-release: {claim}")
            self.assertEqual(claim.get("autonomy_mode"), "PreAuthorized", claim)
            self.assertTrue(claim.get("preauthorized_release"), claim)
            self.assertFalse(claim.get("owner_confirmation_required"), claim)
            self.assertNotIn("dry_run_token", {k: v for k, v in claim.items() if v})
        # the task actually moved pending -> claimed (one-stage write happened)
        self.assertEqual(list((self.repo_root / "5_tasks" / "queue" / "pending").glob("*.md")), [])
        self.assertTrue(list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md")))
        # positive content assertion on the claim record
        records = [r for r in self._claim_records() if r.get("record_type") == "claim_record"]
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0].get("autonomy_mode"), "PreAuthorized")
        self.assertEqual(records[0].get("owner_policy_ref"), "pol_env_1")

    # --- test 2: 信封外回落 Supervised (RED against naive PreAuthorized release) ---

    def test_out_of_envelope_falls_back_to_supervised(self) -> None:
        # task_mode 'ops' is NOT covered by a code-only envelope
        self._seed_pending("AIPOS-OUT1", task_mode="ops")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            self._grant_policy(owner, "pol-decision-2", _policy_block("pol_env_2", task_mode="code"))

            ex = GateClient(url, "executor-secret")
            ex.initialize()
            claim = self._claim(ex, "AIPOS-OUT1", mode="PreAuthorized", policy_ref="pol_env_2")

            self.assertNotEqual(claim.get("autonomy_mode"), "PreAuthorized", claim)
            self.assertEqual(claim.get("autonomy_mode"), "Supervised", claim)
            self.assertTrue(claim.get("owner_confirmation_required"), claim)
            self.assertFalse(claim.get("preauthorized_release"), claim)
        # NOT auto-released: still pending, no claim record landed
        self.assertTrue(list((self.repo_root / "5_tasks" / "queue" / "pending").glob("*.md")))
        self.assertEqual(list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md")), [])
        self.assertEqual([r for r in self._claim_records() if r.get("record_type") == "claim_record"], [])

    # --- test 3: policy needs owner_confirm to be armed; unarmed artifact does not grant ---

    def test_policy_grant_requires_owner_confirm_token(self) -> None:
        self._seed_pending("AIPOS-ENV3")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            dry = owner.call_tool(
                "lybra_owner_decision_record_dry_run",
                _owner_decision_payload("pol-decision-3", autonomy_policy=_policy_block("pol_env_3")),
            )
            self.assertTrue(dry.get("data", {}).get("autonomy_policy_grant"), dry)
            # confirm WITHOUT owner_confirmation_token → refused (envelope never armed)
            refused = owner.call_tool(
                "lybra_owner_decision_record_confirm",
                {"dry_run_token": dry["dry_run_token"], "actor": "owner"},
            )
            self.assertEqual(refused.get("error_code"), "OWNER_CONFIRMATION_REQUIRED", refused)
        # no policy artifact landed
        self.assertFalse((self.repo_root / "5_tasks" / "policies").exists() and any((self.repo_root / "5_tasks" / "policies").glob("*.md")))

    def test_executor_cannot_arm_policy_scope_denied(self) -> None:
        with self.gate() as url:
            ex = GateClient(url, "executor-secret")
            ex.initialize()
            denied = ex.call_tool(
                "lybra_owner_decision_record_dry_run",
                _owner_decision_payload("pol-decision-x", autonomy_policy=_policy_block("pol_env_x")),
            )
            self.assertEqual(denied.get("error_code"), "SCOPE_DENIED", denied)
        self.assertFalse((self.repo_root / "5_tasks" / "policies").exists())

    # --- test 4: expiry / revocation fall back ---

    def test_expired_policy_falls_back(self) -> None:
        self._seed_pending("AIPOS-ENV4")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            self._grant_policy(
                owner,
                "pol-decision-4",
                _policy_block("pol_env_4", active_from="2020-01-01T00:00:00Z", expires_at="2020-06-01T00:00:00Z"),
            )
            ex = GateClient(url, "executor-secret")
            ex.initialize()
            claim = self._claim(ex, "AIPOS-ENV4", mode="PreAuthorized", policy_ref="pol_env_4")
            self.assertEqual(claim.get("autonomy_mode"), "Supervised", claim)
            self.assertTrue(claim.get("owner_confirmation_required"), claim)

    def test_revoked_policy_falls_back(self) -> None:
        self._seed_pending("AIPOS-ENV5")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            self._grant_policy(owner, "pol-decision-5", _policy_block("pol_env_5"))
            # revoke = an Owner action flipping status; assert the gate reader honors it
            policy_path = self.repo_root / "5_tasks" / "policies" / "pol_env_5.md"
            text = policy_path.read_text(encoding="utf-8").replace("status: active", "status: revoked")
            policy_path.write_text(text, encoding="utf-8")

            ex = GateClient(url, "executor-secret")
            ex.initialize()
            claim = self._claim(ex, "AIPOS-ENV5", mode="PreAuthorized", policy_ref="pol_env_5")
            self.assertEqual(claim.get("autonomy_mode"), "Supervised", claim)
            self.assertTrue(claim.get("owner_confirmation_required"), claim)

    # --- test 5: model/token land in records; gate does not verify ---

    def test_model_token_land_in_claim_record(self) -> None:
        self._seed_pending("AIPOS-ENV6")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            self._grant_policy(owner, "pol-decision-6", _policy_block("pol_env_6"))
            ex = GateClient(url, "executor-secret")
            ex.initialize()
            claim = self._claim(
                ex, "AIPOS-ENV6", mode="PreAuthorized", policy_ref="pol_env_6",
                actual_model="claude-opus-4-8", reported_tokens=12345,
            )
            self.assertTrue(claim.get("ok"), claim)
        records = [r for r in self._claim_records() if r.get("record_type") == "claim_record"]
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0].get("actual_model"), "claude-opus-4-8")
        self.assertEqual(records[0].get("reported_tokens"), 12345)

    # --- test 6: return stays Supervised-only (regression pin, red line 4) ---

    def test_return_rejects_preauthorized_mode(self) -> None:
        with self.gate() as url:
            ex = GateClient(url, "executor-secret")
            ex.initialize()
            result = ex.call_tool(
                "lybra_queue_return_dry_run",
                {
                    "task_id": "AIPOS-ENV7",
                    "actor": "exec.cc",
                    "agent_instance": "exec.cc",
                    "autonomy_mode": "PreAuthorized",
                    "owner_policy_ref": "pol_env_7",
                    "result_summary": "done",
                },
            )
            self.assertEqual(result.get("error_code"), "INVALID_AUTONOMY_MODE", result)

    # --- test 7: ★A1 — forged owner_policy_ref falls back; executor can never self-confirm ---

    def test_forged_policy_ref_falls_back_and_no_self_confirm(self) -> None:
        self._seed_pending("AIPOS-ENV8")
        with self.gate() as url:
            ex = GateClient(url, "executor-secret")
            ex.initialize()
            # PreAuthorized + a policy_ref that names no artifact → Supervised fallback, not release
            claim = self._claim(ex, "AIPOS-ENV8", mode="PreAuthorized", policy_ref="pol_does_not_exist")
            self.assertEqual(claim.get("autonomy_mode"), "Supervised", claim)
            self.assertTrue(claim.get("owner_confirmation_required"), claim)
            # and the executor cannot self-confirm the fallback (no owner_confirm scope)
            confirm = ex.call_tool(
                "lybra_queue_claim_confirm",
                {
                    "dry_run_token": claim.get("dry_run_token") or "t",
                    "actor": "exec.cc",
                    "agent_instance": "exec.cc",
                    "owner_policy_ref": "pol_does_not_exist",
                    "owner_confirmation_token": "OWNER_CONFIRMED",
                },
            )
            self.assertEqual(confirm.get("error_code"), "SCOPE_DENIED", confirm)
        self.assertEqual(list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md")), [])

    # --- test 8: R-1 max_tasks count bound ---

    def test_max_tasks_count_bound(self) -> None:
        self._seed_pending("AIPOS-CNT1")
        self._seed_pending("AIPOS-CNT2")
        with self.gate() as url:
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            self._grant_policy(owner, "pol-decision-8", _policy_block("pol_env_8", task_mode="code", max_tasks=1))
            ex = GateClient(url, "executor-secret")
            ex.initialize()
            first = self._claim(ex, "AIPOS-CNT1", mode="PreAuthorized", policy_ref="pol_env_8")
            self.assertEqual(first.get("autonomy_mode"), "PreAuthorized", first)
            # the 2nd envelope-covered claim exceeds max_tasks=1 → Supervised fallback
            second = self._claim(ex, "AIPOS-CNT2", mode="PreAuthorized", policy_ref="pol_env_8")
            self.assertEqual(second.get("autonomy_mode"), "Supervised", second)
            self.assertTrue(second.get("owner_confirmation_required"), second)
        self.assertEqual(len(list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md"))), 1)

    # --- test 9: R-2 FORBIDDEN fields stay UNSUPPORTED even under PreAuthorized ---

    def test_forbidden_fields_still_unsupported(self) -> None:
        self._seed_pending("AIPOS-ENV9")
        with self.gate() as url:
            ex = GateClient(url, "executor-secret")
            ex.initialize()
            for field in ("delegated_policy", "standing_policy", "auto_pick", "auto_select", "background_worker", "batch", "policy_budget"):
                claim = self._claim(
                    ex, "AIPOS-ENV9", mode="PreAuthorized", policy_ref="pol_env_9", **{field: "x"}
                )
                self.assertEqual(claim.get("error_code"), "UNSUPPORTED_QUEUE_CLAIM_FIELD", f"{field}: {claim}")


class PreAuthEnvelopeRealRotateEndToEndTests(unittest.TestCase):
    """AIPOS-250 #4 — end-to-end via REAL serve-rotate creds (no hand-built registry) using the
    EXACT minimal payload shape the owner-console SKILL tells the advisor to copy-paste. Proves the
    Owner can actually get through: arm envelope (owner) -> in-envelope claim auto-release (executor).
    This is the integration guard the earlier lanes missed (mechanism green, path unreachable)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-env-e2e.md").write_text(
            _pending_task("AIPOS-ENV-E2E", task_mode="code", agent="exec.cc.local"), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @contextmanager
    def _gate(self, registry) -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST, port=0, token="", keepalive_seconds=0.01,
            max_keepalive_events=1, service_role_registry=registry,
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

    def test_real_rotate_owner_arms_envelope_executor_auto_releases(self) -> None:
        from tools.aipos_cli.service_mode import build_connection_config, write_connection_config
        from tools.mcp_server.http_sse import load_service_role_registry

        config = build_connection_config(
            self.repo_root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118
        )
        write_connection_config(self.repo_root, config)
        registry = load_service_role_registry(self.repo_root / ".lybra" / "local" / "connection.json")
        tokens = {t["role"]: t["token"] for t in config["tokens"]}

        # EXACT minimal payload shape from the owner-console SKILL (decision_id + autonomy_policy only).
        grant_payload = {
            "decision_id": "pol-decision-exec-mp-e2e",
            "actor": "owner",
            "decided_by_ref": "owner",
            "decision_summary": "给 exec 池预授权:只覆盖 code 类卡、最多 5 张。",
            "autonomy_policy": {
                "policy_id": "pol_exec_mp_e2e",
                "agent_or_role": "exec.cc.local",
                "active_from": "2020-01-01T00:00:00Z",
                "expires_at": "2999-01-01T00:00:00Z",
                "max_tasks": 5,
                "task_selector": {"task_mode": "code"},
            },
        }
        with self._gate(registry) as url:
            owner = GateClient(url, tokens["owner"]); owner.initialize()
            dry = owner.call_tool("lybra_owner_decision_record_dry_run", grant_payload)
            self.assertTrue(dry.get("dry_run_token"), f"owner arm dry-run failed (unreachable?): {dry}")
            self.assertTrue(dry.get("data", {}).get("autonomy_policy_grant"), dry)
            conf = owner.call_tool(
                "lybra_owner_decision_record_confirm",
                {"dry_run_token": dry["dry_run_token"], "actor": "owner", "owner_confirmation_token": "OWNER_CONFIRMED"},
            )
            self.assertTrue(conf.get("ok"), f"owner arm confirm failed: {conf}")

            executor = GateClient(url, tokens["executor"]); executor.initialize()
            claim = executor.call_tool(
                "lybra_queue_claim_dry_run",
                {"task_id": "AIPOS-ENV-E2E", "actor": "exec.cc.local", "agent_instance": "exec.cc.local",
                 "autonomy_mode": "PreAuthorized", "owner_policy_ref": "pol_exec_mp_e2e",
                 "active_session_id": "session_e2e"},
            )
            self.assertTrue(claim.get("ok"), f"in-envelope claim did NOT auto-release: {claim}")
            self.assertEqual(claim.get("autonomy_mode"), "PreAuthorized", claim)
            self.assertTrue(claim.get("preauthorized_release"), claim)
        # policy artifact + claimed card both on disk (end-to-end truth)
        self.assertTrue((self.repo_root / "5_tasks" / "policies" / "pol_exec_mp_e2e.md").is_file())
        self.assertTrue(list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md")))


def _schema_violations(payload: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Minimal MCP inputSchema conformance check (required + additionalProperties + declared type),
    mirroring what a schema-validating MCP client does BEFORE the request reaches the gate. Zero-dep
    (no jsonschema) so it runs on the bare-python lane too."""
    props = schema.get("properties", {})
    errs: list[str] = []
    if schema.get("additionalProperties") is False:
        for key in payload:
            if key not in props:
                errs.append(f"additionalProperties=False rejects undeclared key: {key}")
    for req in schema.get("required", []):
        if req not in payload:
            errs.append(f"missing required key: {req}")
    _JSON_TYPE = {"string": str, "integer": int, "number": (int, float), "object": dict, "array": list, "boolean": bool}
    for key, value in payload.items():
        spec = props.get(key)
        if isinstance(spec, dict) and spec.get("type") in _JSON_TYPE:
            py = _JSON_TYPE[spec["type"]]
            if not isinstance(value, py) or (spec["type"] != "boolean" and isinstance(value, bool)):
                errs.append(f"key {key} type {type(value).__name__} != schema {spec['type']}")
    return errs


class OwnerDecisionSchemaConformanceTests(unittest.TestCase):
    """AIPOS-250 #4 (systematic): the gate does NOT enforce inputSchema server-side, so a
    schema↔writer↔SKILL drift stays invisible to gate tests — a real schema-validating MCP client
    strips an undeclared field or rejects the call. This test validates the SKILL's minimal envelope
    payload against the PUBLISHED inputSchema, catching the O3 defect (autonomy_policy undeclared +
    additionalProperties:False + owner_approval_evidence in required forced evidence)."""

    def _descriptor(self, name: str) -> dict[str, Any]:
        from tools.mcp_server.tools import READ_TOOL_DESCRIPTORS, WRITE_TOOL_DESCRIPTORS
        for tool in list(READ_TOOL_DESCRIPTORS) + list(WRITE_TOOL_DESCRIPTORS):
            if tool.get("name") == name:
                return tool
        self.fail(f"tool descriptor not found: {name}")

    def test_owner_decision_schema_declares_autonomy_policy_and_relaxes_required(self) -> None:
        schema = self._descriptor("lybra_owner_decision_record_dry_run")["inputSchema"]
        self.assertIn("autonomy_policy", schema.get("properties", {}), "autonomy_policy must be a declared property")
        # the envelope path must be reachable: owner_approval_evidence NOT unconditionally required
        self.assertNotIn("owner_approval_evidence", schema.get("required", []))
        self.assertEqual(schema.get("required"), ["decision_id"], "only decision_id is schema-required")
        self.assertIs(schema.get("additionalProperties"), False, "additionalProperties stays locked")

    def test_skill_minimal_envelope_payload_passes_published_schema(self) -> None:
        schema = self._descriptor("lybra_owner_decision_record_dry_run")["inputSchema"]
        # EXACT minimal shape the owner-console SKILL tells the advisor to send.
        skill_payload = {
            "decision_id": "pol-decision-exec-mp-20260715",
            "actor": "owner",
            "decided_by_ref": "owner",
            "decision_summary": "给 exec 池预授权:只覆盖 code 类卡、最多 5 张。",
            "autonomy_policy": {
                "policy_id": "pol_exec_mp_20260715",
                "agent_or_role": "exec.cc.local",
                "active_from": "2026-07-15T00:00:00Z",
                "expires_at": "2026-07-16T00:00:00Z",
                "max_tasks": 5,
                "task_selector": {"task_mode": "code"},
            },
        }
        violations = _schema_violations(skill_payload, schema)
        self.assertEqual(violations, [], f"SKILL envelope payload rejected by published schema: {violations}")


class OwnerConsolePreAuthEnvelopeSkillTests(unittest.TestCase):
    """AIPOS-250 SKILL delta: owner-console must teach how to ARM a PreAuthorized envelope, or the
    O3 script step 1 dead-locks (the advisor still reads the stale 'zero autonomy' line and refuses).
    """

    _REPO = Path(__file__).resolve().parents[3]

    def _skill(self, name: str) -> str:
        path = self._REPO / "skills" / name / "SKILL.md"
        self.assertTrue(path.is_file(), f"missing {path}")
        return path.read_text(encoding="utf-8")

    def test_owner_console_teaches_preauthorized_envelope(self) -> None:
        text = self._skill("owner-console")
        for needle in (
            "预授权信封",
            "owner_autonomy_policy",
            "PreAuthorized",
            "autonomy_policy",
            "max_tasks",
            "task_selector",
            "lybra_owner_decision_record_confirm",
            "revoked",
            # AIPOS-250 #1: a COMPLETE copy-paste payload (decision_id + actor + policy block), not
            # just field names — the advisor must be able to copy-change-run without guessing.
            '"decision_id"',
            '"autonomy_policy"',
            '"policy_id"',
            '"actor"',
            # AIPOS-250 #2: the design rationale that the heavy owner_approval_evidence is NOT needed.
            "harness_owner_confirm",
            "带内",
        ):
            self.assertIn(needle, text, f"owner-console missing envelope teaching: {needle}")

    def test_owner_console_drops_stale_zero_autonomy_claim(self) -> None:
        text = self._skill("owner-console")
        # the stale line that would make the advisor refuse to arm an envelope must be gone
        self.assertNotIn("不实现任何免确认路径", text)

    def test_owner_console_arms_confirm_is_in_ask_snippet(self) -> None:
        """F-06: arming an envelope goes through owner_decision_record_confirm (owner_confirm-gated),
        so it MUST be in the harness ask list or the Owner's one hand-press could be auto-approved."""
        text = self._skill("owner-console")
        self.assertIn("mcp__lybra__lybra_owner_decision_record_confirm", text)

    def test_owner_console_holds_preauth_is_not_delegation_redline(self) -> None:
        text = self._skill("owner-console")
        self.assertIn("预授权 ≠ 委托", text)
        # return/publish/audit stay per-task (claim-only tier)
        self.assertIn("只放行 claim", text)

    def test_executor_skill_teaches_envelope_and_no_confirm(self) -> None:
        text = self._skill("lybra-executor")
        self.assertIn("PreAuthorized", text)
        self.assertIn("信封", text)
        self.assertIn("SCOPE_DENIED", text)


if __name__ == "__main__":
    unittest.main()
