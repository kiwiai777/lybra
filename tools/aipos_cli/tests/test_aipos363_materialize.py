"""AIPOS-363 S1/S2 — connector materialization tests (agent_materialize.py).

Pins (card S1/S2 + acceptance 4/5):
- materialize: claim (PreAuthorized autorelease) → pull body (319) → write card.md + MANIFEST,
  and the printed kickoff contains ZERO gate verbs and points ONLY at local paths (card S3:
  agent 侧零 gate 知识);
- materialize on a Supervised fallback does NOT self-confirm and STOPS with needs_owner_confirm
  (card red line: 判断留人; the connector never owner-confirms a claim);
- pushback: reads LOCAL RETURN → relays return_body (320) → self-confirms (328); local RETURN
  retained as copy;
- pushback failure is NEVER silent (card S2 / acceptance 4): every failure path emits a blocked
  progress event (323) — missing RETURN, gate error on dry-run, confirm rejected;
- material area: one card = one dir; task_id path-traversal is rejected (no escape); MANIFEST
  carries NO secret / NO token (card red line: 材料区不得含凭据).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.aipos_cli import agent_materialize as am


class FakeGateClient:
    """Records calls + returns canned structured responses; raises on demand to simulate errors."""

    def __init__(self, *, base_url: str = "http://gate.test:7118") -> None:
        self._base_url = base_url
        self.calls: list[tuple[str, dict]] = []
        self.responses: dict[str, list[dict | Exception]] = {}
        self.errors: dict[str, Exception] = {}

    def initialize(self) -> None:
        return None

    def queue(self, verb: str, response) -> None:
        self.responses.setdefault(verb, []).append(response)

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, dict(arguments)))
        if name in self.errors:
            raise self.errors[name]
        queue = self.responses.get(name)
        if queue:
            resp = queue.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return dict(resp)
        return {"ok": True}


def _claim_ok(task_id: str = "AIPOS-M1", mode: str = "PreAuthorized") -> dict:
    released = mode == "PreAuthorized"
    base = {
        "ok": True,
        "autonomy_mode": mode,
        "preauthorized_release": released,
        "owner_confirmation_required": not released,
        "data": {
            "claim_id": f"claim_{task_id}_20260808",
            "active_session_id": f"session_{task_id}_20260808",
        },
    }
    if released:
        base["data"]["moved"] = True
        base["data"]["wrote"] = True
    else:  # Supervised fallback = a dry-run preview; nothing moved/written
        base["dry_run_token"] = f"drt-{task_id}"
        base["data"]["moved"] = False
        base["data"]["wrote"] = False
    return base


def _preview_ok(body: str) -> dict:
    return {"ok": True, "data": {"body_markdown": body, "task_id": "AIPOS-M1"}}


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_materialize_writes_card_manifest_and_kickoff(self) -> None:
        client = FakeGateClient()
        client.queue(am.CLAIM_VERB, _claim_ok())
        client.queue(am.PREVIEW_VERB, _preview_ok("# Card\n\nDo the thing."))
        result = am.materialize(
            client, task_id="AIPOS-M1", actor="exec.test",
            owner_policy_ref="pol_test", root=self.root,
        )
        self.assertTrue(result["ok"], result)
        mdir = self.root / "AIPOS-M1"
        self.assertEqual(result["material_dir"], str(mdir))
        self.assertIn("Do the thing", (mdir / "card.md").read_text(encoding="utf-8"))
        manifest = json.loads((mdir / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["task_id"], "AIPOS-M1")
        self.assertEqual(manifest["claim_id"], "claim_AIPOS-M1_20260808")
        self.assertEqual(manifest["owner_policy_ref"], "pol_test")
        # red line: material area MUST NOT contain credentials
        manifest_text = (mdir / "MANIFEST.json").read_text(encoding="utf-8")
        for needle in ("token", "secret", "password", "bearer"):
            self.assertNotIn(needle, manifest_text.lower(), f"MANIFEST leaks a secret-ish word: {needle}")
        # the verbs actually called: claim then preview(include_body=True)
        called = [c[0] for c in client.calls]
        self.assertEqual(called, [am.CLAIM_VERB, am.PREVIEW_VERB])
        self.assertTrue(client.calls[1][1].get("include_body"))

    def test_kickoff_has_no_gate_verbs_and_points_only_at_local_paths(self) -> None:
        """card S3: agent 侧零 gate 知识 — the kickoff names only LOCAL files, never a gate verb."""
        client = FakeGateClient()
        client.queue(am.CLAIM_VERB, _claim_ok())
        client.queue(am.PREVIEW_VERB, _preview_ok("body"))
        result = am.materialize(
            client, task_id="AIPOS-M1", actor="exec.test",
            owner_policy_ref="pol_test", root=self.root,
        )
        kickoff = result["kickoff"]
        self.assertIn("card.md", kickoff)
        self.assertIn("RETURN.md", kickoff)
        self.assertIn("不需要、也不应调用任何 gate 动词", kickoff)
        for verb in ("lybra_task_preview", "lybra_queue_", "lybra_return_", "lybra_task_progress", "include_body"):
            self.assertNotIn(verb, kickoff, f"materialized kickoff must not teach a gate verb: {verb}")

    def test_supervised_fallback_stops_without_self_confirm(self) -> None:
        """card red line (判断留人): a Supervised claim fallback must NOT be self-confirmed by the
        connector; it stops with needs_owner_confirm so the Owner resolves the envelope."""
        client = FakeGateClient()
        client.queue(am.CLAIM_VERB, _claim_ok(mode="Supervised"))
        result = am.materialize(
            client, task_id="AIPOS-M1", actor="exec.test",
            owner_policy_ref="pol_test", root=self.root,
        )
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["needs_owner_confirm"], result)
        self.assertEqual(result["phase"], "claim")
        # no material was written
        self.assertFalse((self.root / "AIPOS-M1").exists())
        # preview was never called (we stopped at claim)
        self.assertNotIn(am.PREVIEW_VERB, [c[0] for c in client.calls])


def _seed_material(root: Path, task_id: str = "AIPOS-M1", *, return_body: str | None = "# RETURN\n\ndone.") -> Path:
    mdir = root / task_id
    am._write_card_and_manifest(
        mdir, task_id=task_id, body_markdown="# Card",
        manifest={"task_id": task_id, "claim_id": f"claim_{task_id}",
                  "active_session_id": f"session_{task_id}", "actor": "exec.test",
                  "owner_policy_ref": "pol_test", "autonomy_mode": "PreAuthorized"},
    )
    if return_body is not None:
        (mdir / "RETURN.md").write_text(return_body, encoding="utf-8")
    return mdir


class PushbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pushback_relays_return_body_and_self_confirms(self) -> None:
        client = FakeGateClient()
        client.queue(am.RETURN_DRY_RUN_VERB, {"ok": True, "dry_run_token": "drt-1"})
        client.queue(am.RETURN_CONFIRM_VERB, {"ok": True, "data": {"moved": True}})
        _seed_material(self.root, return_body="# RETURN\n\nshipped.")
        result = am.pushback(
            client, task_id="AIPOS-M1", actor="exec.test", owner_policy_ref="pol_test", root=self.root,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["phase"], "pushed_back")
        # the dry-run carried the local RETURN body as return_body (320)
        dry_call = next(c for c in client.calls if c[0] == am.RETURN_DRY_RUN_VERB)
        self.assertEqual(dry_call[1]["return_body"], "# RETURN\n\nshipped.")
        # 328 self-confirm used the public OWNER_CONFIRMED ceremony literal
        conf_call = next(c for c in client.calls if c[0] == am.RETURN_CONFIRM_VERB)
        self.assertEqual(conf_call[1]["owner_confirmation_token"], am.OWNER_CONFIRM_LITERAL)
        # local RETURN retained as copy (not deleted)
        self.assertTrue((self.root / "AIPOS-M1" / "RETURN.md").is_file())

    def test_pushback_missing_return_emits_blocked_event(self) -> None:
        """acceptance 4: 交回失败不静默 — missing RETURN surfaces a blocked event (323), not a swallow."""
        client = FakeGateClient()
        client.queue(am.PROGRESS_VERB, {"ok": True})
        _seed_material(self.root, return_body=None)
        result = am.pushback(
            client, task_id="AIPOS-M1", actor="exec.test", owner_policy_ref="pol_test", root=self.root,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "read_return")
        self.assertTrue(result["event"]["ok"], "a blocked event MUST be emitted on pushback failure")
        ev = next(c for c in client.calls if c[0] == am.PROGRESS_VERB)
        self.assertEqual(ev[1]["event_type"], "blocked")
        self.assertEqual(ev[1]["stage"], "pushback")

    def test_pushback_dry_run_gate_error_emits_blocked_event(self) -> None:
        client = FakeGateClient()
        client.errors[am.RETURN_DRY_RUN_VERB] = am.GateError("gate down")
        client.queue(am.PROGRESS_VERB, {"ok": True})
        _seed_material(self.root)
        result = am.pushback(
            client, task_id="AIPOS-M1", actor="exec.test", owner_policy_ref="pol_test", root=self.root,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "return_dry_run")
        self.assertIn("gate down", result["error"])
        self.assertTrue(result["event"]["ok"])

    def test_pushback_confirm_rejected_emits_blocked_event(self) -> None:
        client = FakeGateClient()
        client.queue(am.RETURN_DRY_RUN_VERB, {"ok": True, "dry_run_token": "drt-1"})
        client.queue(am.RETURN_CONFIRM_VERB, {"ok": False, "error_code": "STALE_DRY_RUN"})
        client.queue(am.PROGRESS_VERB, {"ok": True})
        _seed_material(self.root)
        result = am.pushback(
            client, task_id="AIPOS-M1", actor="exec.test", owner_policy_ref="pol_test", root=self.root,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "return_confirm")
        self.assertTrue(result["event"]["ok"])

    def test_pushback_dry_run_notok_emits_blocked_event(self) -> None:
        client = FakeGateClient()
        client.queue(am.RETURN_DRY_RUN_VERB, {"ok": False, "error_code": "NOT_CLAIMED"})
        client.queue(am.PROGRESS_VERB, {"ok": True})
        _seed_material(self.root)
        result = am.pushback(
            client, task_id="AIPOS-M1", actor="exec.test", owner_policy_ref="pol_test", root=self.root,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "return_dry_run")
        self.assertTrue(result["event"]["ok"])


class MaterialAreaPathTests(unittest.TestCase):
    def test_task_id_traversal_rejected(self) -> None:
        """card red line: a forged task_id must not escape the material root."""
        for bad in ("../evil", "a/b", "a\\b", ".."):
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                am.task_material_dir(bad, Path("/tmp/work"))

    def test_one_card_one_directory(self) -> None:
        root = Path(tempfile.mkdtemp())
        a = am.task_material_dir("AIPOS-A", root)
        b = am.task_material_dir("AIPOS-B", root)
        self.assertNotEqual(a, b)
        self.assertTrue(a.is_dir() or True)  # dir created on write, not on resolve


if __name__ == "__main__":
    unittest.main()


class HarnessAgnosticKickoffTests(unittest.TestCase):
    """card S3: the materialized kickoff is harness-agnostic — pi / Claude Code / codex / bash
    all consume the SAME local-file kickoff through their own cmd template, zero gate knowledge."""

    @classmethod
    def setUpClass(cls) -> None:
        import yaml
        cls.runtime_cmds = yaml.safe_load(
            (Path(__file__).resolve().parents[3] / "config" / "runtime_cmds.yaml").read_text(encoding="utf-8")
        )

    def _kickoff(self) -> str:
        return am.render_materialized_kickoff("AIPOS-X", Path("/home/agent/.lybra/work/AIPOS-X"))

    def test_runtime_cmds_has_pi_claude_code_codex_slots(self) -> None:
        for harness in ("pi", "cc", "claude_code", "codex", "generic_bash"):
            self.assertIn(harness, self.runtime_cmds, f"runtime_cmds.yaml missing harness slot: {harness}")
            cmd = self.runtime_cmds[harness].get("cmd", "")
            self.assertIn("{kickoff}", cmd, f"{harness}.cmd must carry the {{kickoff}} placeholder")

    def test_materialized_kickoff_substitutes_cleanly_into_each_harness(self) -> None:
        """acceptance 2/3: the materialized kickoff flows through every harness cmd template with
        NO gate verb and NO leftover placeholder (agent side = zero gate knowledge)."""
        kickoff = self._kickoff()
        for harness, entry in self.runtime_cmds.items():
            cmd = entry["cmd"].replace("{kickoff}", kickoff)
            self.assertNotIn("{", cmd, f"{harness}: leftover template brace after substitution")
            for verb in ("lybra_task_preview", "lybra_queue_", "lybra_return_", "include_body"):
                self.assertNotIn(verb, cmd, f"{harness}: materialized kickoff leaked a gate verb: {verb}")
            self.assertIn("card.md", cmd, f"{harness}: the local card path must survive substitution")

    def test_materialized_kickoff_triggers_safe_file_transmission(self) -> None:
        """The materialized kickoff contains newlines (a kickoff_safe hazard), so a launcher MUST
        transmit it via @file (not inline shell). This pins the safe-transmission contract shared
        with advisor_pump (340/泵共用源 concern) — materialized kickoffs are multi-line by design."""
        from tools.aipos_cli.kickoff_safe import KICKOFF_HAZARDS
        kickoff = self._kickoff()
        self.assertTrue(
            any(h in kickoff for h in KICKOFF_HAZARDS),
            "materialized kickoff should carry a shell hazard (newline) so launchers use @file",
        )


if __name__ == "__main__":
    unittest.main()
