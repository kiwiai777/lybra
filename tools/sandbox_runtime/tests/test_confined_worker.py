from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from tools.sandbox_runtime import confined_worker as cw


def _request(tmp: Path, **overrides):
    approved = tmp / "approved_scratch"
    approved.mkdir(parents=True, exist_ok=True)
    run_id = overrides.pop("run_id", "cw_20260611T000000Z_abcd1234")
    control = tmp / "control"
    control.mkdir(parents=True, exist_ok=True)
    projection = tmp / "projection"
    projection.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        image="lybra/claude-worker:test",
        prompt="do the bounded task",
        run_id=run_id,
        network="lybra-worker-net",
        gate_url="http://172.18.0.1:7118/mcp",
        gate_ip="172.18.0.1",
        approved_scratch_root=approved,
        scratch_run_dir=approved / run_id,
        projection_dir=projection,
        mcp_config_path=control / f"{run_id}.json",
        mcp_token="EXEC-TOKEN-RAW-VALUE",
        mcp_token_fingerprint=cw.secret_fingerprint("EXEC-TOKEN-RAW-VALUE"),
        anthropic_key_present=True,
        anthropic_key_fingerprint=cw.secret_fingerprint("sk-ant-secret"),
        # AIPOS-198: real path always derives a non-root --user from the orchestrator
        # (== the 0600 token-file owner). Mirror that default in the test fixture.
        run_as_uid=os.getuid(),
        run_as_gid=os.getgid(),
        dry_run=True,
    )
    kwargs.update(overrides)
    return cw.ConfinedWorkerRequest(**kwargs)


class ConfinedWorkerArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def test_argv_has_hardening_flags(self) -> None:
        argv = cw.build_docker_argv(_request(self.tmp))
        joined = " ".join(argv)
        self.assertIn("--rm", argv)
        self.assertIn("never", argv)  # --pull never
        self.assertIn("no-new-privileges", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--pids-limit", argv)
        self.assertIn("--tmpfs", argv)
        self.assertIn("/tmp", argv)
        self.assertIn("--network", argv)
        self.assertIn("lybra-worker-net", argv)
        # Forbidden escalations must be absent.
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("--pid=host", joined)
        self.assertNotIn("docker.sock", joined)
        # AIPOS-198 F-c9: non-root --user matching the orchestrator/token-file owner.
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")

    def test_argv_carries_no_raw_secret(self) -> None:
        argv = cw.build_docker_argv(_request(self.tmp))
        joined = " ".join(argv)
        self.assertNotIn("EXEC-TOKEN-RAW-VALUE", joined)  # token only in mounted file
        self.assertNotIn("sk-ant-secret", joined)  # key via env passthrough
        # Env passthrough form: name only, no value.
        self.assertIn("--env", argv)
        self.assertIn(cw.ANTHROPIC_KEY_ENV, argv)
        self.assertNotIn(f"{cw.ANTHROPIC_KEY_ENV}=", joined)

    def test_mounts_topology(self) -> None:
        req = _request(self.tmp)
        argv = cw.build_docker_argv(req)
        mounts = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--mount"]
        projection_mount = next(m for m in mounts if m.endswith(f"target={cw.PROJECTION_TARGET},readonly"))
        scratch_mount = next(m for m in mounts if f"target={cw.SCRATCH_TARGET}" in m)
        cfg_mount = next(m for m in mounts if f"target={cw.MCP_CONFIG_TARGET}" in m)
        self.assertIn("readonly", projection_mount)
        self.assertIn("readonly", cfg_mount)
        # scratch is the ONLY writable bind (no readonly suffix).
        self.assertFalse(scratch_mount.endswith("readonly"))
        self.assertEqual(sum(1 for m in mounts if not m.endswith("readonly")), 1)
        self.assertIn(str((self.tmp / "approved_scratch" / req.run_id)), scratch_mount)

    def test_scratch_mapping_env(self) -> None:
        req = _request(self.tmp)
        argv = cw.build_docker_argv(req)
        env_assignments = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--env"]
        scratch_env = next(e for e in env_assignments if e.startswith(f"{cw.SCRATCH_HOST_ENV}="))
        self.assertTrue(scratch_env.endswith(str((self.tmp / "approved_scratch" / req.run_id).resolve())))
        no_proxy = next(e for e in env_assignments if e.startswith("NO_PROXY="))
        self.assertIn("127.0.0.1", no_proxy)
        self.assertIn("172.18.0.1", no_proxy)

    def test_no_bind_source_inside_repo(self) -> None:
        repo = self.tmp / "repo"
        (repo / "5_tasks").mkdir(parents=True, exist_ok=True)
        # scratch inside repo truth must be refused.
        bad = _request(self.tmp, repo_root=repo, approved_scratch_root=repo / "5_tasks",
                       scratch_run_dir=repo / "5_tasks" / "cw_20260611T000000Z_abcd1234")
        with self.assertRaises(cw.ConfinedWorkerError):
            cw.build_docker_argv(bad)

    def test_forbidden_network_refused(self) -> None:
        for net in ("host", "none", "bridge", ""):
            with self.assertRaises(cw.ConfinedWorkerError):
                cw.build_docker_argv(_request(self.tmp, network=net))

    def test_public_gate_url_refused(self) -> None:
        with self.assertRaises(cw.ConfinedWorkerError):
            cw.build_docker_argv(_request(self.tmp, gate_url="http://0.0.0.0:7118/mcp"))

    def test_scratch_run_dir_must_be_under_approved_root(self) -> None:
        elsewhere = self.tmp / "elsewhere" / "x"
        with self.assertRaises(cw.ConfinedWorkerError):
            cw.build_docker_argv(_request(self.tmp, scratch_run_dir=elsewhere))

    def test_token_file_outside_scratch_and_projection(self) -> None:
        req = _request(self.tmp, mcp_config_path=self.tmp / "approved_scratch" / "cw_20260611T000000Z_abcd1234" / "x.json")
        with self.assertRaises(cw.ConfinedWorkerError):
            cw.build_docker_argv(req)


class ConfinedWorkerReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def test_dry_run_report_fields_and_no_secret(self) -> None:
        report = cw.run_confined_worker(_request(self.tmp, dry_run=True))
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "dry_run")
        self.assertEqual(report["execution_model"], "confined_autonomous_worker")
        self.assertEqual(report["network"]["posture"], "dedicated_bridge")
        self.assertFalse(report["network"]["gate_public"])
        self.assertEqual(report["mounts"]["scratch"]["mode"], "rw")
        self.assertEqual(report["mounts"]["projection"]["mode"], "ro")
        self.assertEqual(report["mounts"]["mcp_config"]["mode"], "ro")
        self.assertIn("5_tasks/**", report["unmounted_truth_paths"])
        self.assertEqual(report["credentials"]["mcp_token_fingerprint"], cw.secret_fingerprint("EXEC-TOKEN-RAW-VALUE"))
        self.assertFalse(report["credentials"]["baked_into_image"])
        # No raw secret anywhere in the serialized report.
        blob = json.dumps(report)
        self.assertNotIn("EXEC-TOKEN-RAW-VALUE", blob)
        self.assertNotIn("sk-ant-secret", blob)
        # Dry-run writes no token file.
        self.assertFalse((self.tmp / "control" / "cw_20260611T000000Z_abcd1234.json").exists())

    def test_dry_run_teardown_not_yet_done(self) -> None:
        report = cw.run_confined_worker(_request(self.tmp, dry_run=True))
        self.assertFalse(report["teardown"]["token_file_removed"])
        self.assertTrue(report["teardown"]["container_rm"])


class McpConfigAndTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def _connection_json(self) -> Path:
        path = self.tmp / "connection.json"
        path.write_text(
            json.dumps(
                {
                    "tokens": [
                        {"role": "executor", "token_ref": "svc-executor", "scopes": ["queue_claim", "queue_return"], "token": "EXEC-RAW"},
                        {"role": "owner-dispatch", "token_ref": "svc-owner-dispatch", "scopes": ["audit_dispatch"], "token": "OWNER-RAW"},
                        {"role": "auditor", "token_ref": "svc-auditor", "scopes": ["queue_claim", "audit_verdict"], "token": "AUDIT-RAW"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_executor_token_selection(self) -> None:
        self.assertEqual(cw.executor_token(self._connection_json()), "EXEC-RAW")

    def test_all_raw_secrets(self) -> None:
        self.assertEqual(sorted(cw.all_raw_secrets(self._connection_json())), ["AUDIT-RAW", "EXEC-RAW", "OWNER-RAW"])

    def test_mcp_config_bearer_and_perms(self) -> None:
        cfg = self.tmp / "control" / "run.json"
        config = cw.build_mcp_client_config("http://172.18.0.1:7118/mcp", "EXEC-RAW")
        self.assertEqual(config["mcpServers"]["lybra"]["headers"]["Authorization"], "Bearer EXEC-RAW")
        cw.write_mcp_config_file(cfg, config)
        mode = stat.S_IMODE(cfg.stat().st_mode)
        self.assertEqual(mode & 0o077, 0)  # not group/other readable

    def test_teardown_token_file_unlinks(self) -> None:
        cfg = self.tmp / "control" / "run.json"
        cw.write_mcp_config_file(cfg, {"x": 1})
        self.assertTrue(cfg.exists())
        self.assertTrue(cw.teardown_token_file(cfg))
        self.assertFalse(cfg.exists())
        # Idempotent on already-removed file.
        self.assertTrue(cw.teardown_token_file(cfg))


class ProjectionScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def test_render_projection_minimal_no_secrets(self) -> None:
        dest = self.tmp / "projection"
        context_pack = {
            "scope": "task",
            "task": {"task_id": "AIPOS-196B", "title": "confined worker", "status": "claimed", "path": "5_tasks/queue/claimed/x.md"},
            "context_bundle": {"ref": "dev_claude"},
            "source_refs": ["5_tasks/queue/claimed/x.md"],
        }
        written = cw.render_projection(context_pack, dest)
        self.assertIn("context_pack.json", written)
        self.assertIn("TASK.md", written)
        summary = json.loads((dest / "context_pack.json").read_text(encoding="utf-8"))
        self.assertFalse(summary["writes_enabled"])
        self.assertEqual(summary["task"]["task_id"], "AIPOS-196B")
        # Clean scan passes.
        cw.assert_no_secrets(dest, ["RAW-TOKEN-XYZ"])

    def test_assert_no_secrets_detects_leak(self) -> None:
        dest = self.tmp / "projection"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "leak.txt").write_text("authorization: Bearer RAW-TOKEN-XYZ\n", encoding="utf-8")
        with self.assertRaises(cw.ConfinedWorkerError):
            cw.assert_no_secrets(dest, ["RAW-TOKEN-XYZ"])

    def test_assert_no_secrets_detects_filename_leak(self) -> None:
        # AIPOS-191B Slice A: a secret value appearing in a path name is caught,
        # and the raised message must not echo the raw secret.
        dest = self.tmp / "projection"
        (dest / "sub").mkdir(parents=True, exist_ok=True)
        (dest / "sub" / "RAW-TOKEN-XYZ.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(cw.ConfinedWorkerError) as ctx:
            cw.assert_no_secrets(dest, ["RAW-TOKEN-XYZ"])
        self.assertNotIn("RAW-TOKEN-XYZ", str(ctx.exception))


class ScratchProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def test_provision_scratch_dir_is_world_writable(self) -> None:
        # AIPOS-191B Slice B: the per-run scratch dir must be writable by any
        # container uid (no --user is set), so it is provisioned world-writable.
        approved = self.tmp / "approved"
        approved.mkdir(parents=True, exist_ok=True)
        os.chmod(approved, 0o700)  # operator may keep the approved root locked down
        run_dir = approved / "cw_run"
        cw.provision_scratch_dir(run_dir)
        self.assertTrue(run_dir.is_dir())
        self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o777)
        # The approved-root parent mode is left untouched.
        self.assertEqual(stat.S_IMODE(approved.stat().st_mode), 0o700)

    def test_provision_scratch_dir_idempotent(self) -> None:
        run_dir = self.tmp / "approved" / "cw_run"
        cw.provision_scratch_dir(run_dir)
        cw.provision_scratch_dir(run_dir)  # exist_ok; re-chmod stays 0777
        self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o777)


class ByoLlmWiringTests(unittest.TestCase):
    """AIPOS-196c: base_url + model + config dir + auth_mode wiring."""

    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def _envs(self, argv):
        return [argv[i + 1] for i, tok in enumerate(argv) if tok == "--env"]

    def test_base_url_injected_when_set_absent_when_unset(self) -> None:
        argv = cw.build_docker_argv(_request(self.tmp, anthropic_base_url="https://xchai.xyz"))
        self.assertIn("ANTHROPIC_BASE_URL=https://xchai.xyz", self._envs(argv))
        argv2 = cw.build_docker_argv(_request(self.tmp))
        self.assertFalse(any(e.startswith("ANTHROPIC_BASE_URL=") for e in self._envs(argv2)))

    def test_model_flag_on_claude_command(self) -> None:
        argv = cw.build_docker_argv(_request(self.tmp, model="claude-sonnet-4-6"))
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-4-6")
        argv2 = cw.build_docker_argv(_request(self.tmp))
        self.assertNotIn("--model", argv2)

    def test_home_and_config_dir_always_injected(self) -> None:
        envs = self._envs(cw.build_docker_argv(_request(self.tmp)))
        self.assertIn("HOME=/tmp", envs)
        self.assertIn("CLAUDE_CONFIG_DIR=/tmp/.claude", envs)

    def test_auth_mode_selects_credential_env(self) -> None:
        envs_default = self._envs(cw.build_docker_argv(_request(self.tmp)))
        self.assertIn("ANTHROPIC_API_KEY", envs_default)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", envs_default)
        envs_token = self._envs(cw.build_docker_argv(_request(self.tmp, auth_mode="auth_token")))
        self.assertIn("ANTHROPIC_AUTH_TOKEN", envs_token)
        self.assertNotIn("ANTHROPIC_API_KEY", envs_token)

    def test_invalid_auth_mode_refused(self) -> None:
        with self.assertRaises(cw.ConfinedWorkerError):
            cw.build_docker_argv(_request(self.tmp, auth_mode="bogus"))

    def test_argv_carries_no_raw_secret_with_byo_llm(self) -> None:
        req = _request(self.tmp, anthropic_base_url="https://xchai.xyz", model="claude-sonnet-4-6")
        joined = " ".join(cw.build_docker_argv(req))
        self.assertNotIn("EXEC-TOKEN-RAW-VALUE", joined)
        self.assertNotIn("sk-ant-secret", joined)

    def test_report_llm_block_non_secret(self) -> None:
        req = _request(self.tmp, anthropic_base_url="https://xchai.xyz", model="claude-sonnet-4-6", dry_run=True)
        report = cw.run_confined_worker(req)
        llm = report["llm"]
        self.assertEqual(llm["base_url"], "https://xchai.xyz")
        self.assertEqual(llm["model"], "claude-sonnet-4-6")
        self.assertEqual(llm["auth_mode"], "api_key")
        self.assertEqual(llm["config_dir"], "/tmp/.claude")
        self.assertEqual(llm["home"], "/tmp")
        self.assertEqual(llm["key_fingerprint"], cw.secret_fingerprint("sk-ant-secret"))
        blob = json.dumps(report)
        self.assertNotIn("sk-ant-secret", blob)
        self.assertNotIn("EXEC-TOKEN-RAW-VALUE", blob)


class RunAsUserTests(unittest.TestCase):
    """AIPOS-198 F-c9: --user matches the 0600 token-file owner; never hardcoded."""

    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def test_user_value_matches_derived_owner(self) -> None:
        argv = cw.build_docker_argv(_request(self.tmp, run_as_uid=4321, run_as_gid=8765))
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user") + 1], "4321:8765")

    def test_user_gid_defaults_to_uid_when_gid_none(self) -> None:
        argv = cw.build_docker_argv(_request(self.tmp, run_as_uid=4321, run_as_gid=None))
        self.assertEqual(argv[argv.index("--user") + 1], "4321:4321")

    def test_no_user_escape_hatch_omits_user(self) -> None:
        argv = cw.build_docker_argv(_request(self.tmp, run_as_uid=None, run_as_gid=None))
        self.assertNotIn("--user", argv)

    def test_cli_default_derives_orchestrator_uid(self) -> None:
        argv = self._build_via_cli([])
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")

    def test_cli_run_as_uid_overrides(self) -> None:
        argv = self._build_via_cli(["--run-as-uid", "1234", "--run-as-gid", "5678"])
        self.assertEqual(argv[argv.index("--user") + 1], "1234:5678")

    def test_cli_run_as_combined_overrides(self) -> None:
        argv = self._build_via_cli(["--run-as", "1234:5678"])
        self.assertEqual(argv[argv.index("--user") + 1], "1234:5678")
        argv2 = self._build_via_cli(["--run-as", "1234"])  # gid defaults to orchestrator gid
        self.assertEqual(argv2[argv2.index("--user") + 1], f"1234:{os.getgid()}")

    def test_cli_no_user_disables(self) -> None:
        argv = self._build_via_cli(["--no-user"])
        self.assertNotIn("--user", argv)

    def test_uid_consistency_guard_fail_closed(self) -> None:
        # write_mcp_config_file writes as os.getuid(); a mismatched run_as_uid must
        # be rejected before launch (the --user could not read the 0600 mount).
        req = _request(self.tmp, run_as_uid=os.getuid() + 1, dry_run=False)
        with self.assertRaises(cw.ConfinedWorkerError):
            cw.run_confined_worker(req)
        # the token file is cleaned up on the fail-closed path.
        self.assertFalse(req.mcp_config_path.exists())

    def test_report_run_as_block(self) -> None:
        report = cw.run_confined_worker(_request(self.tmp, run_as_uid=4321, run_as_gid=8765, dry_run=True))
        self.assertEqual(report["run_as"]["uid"], 4321)
        self.assertEqual(report["run_as"]["gid"], 8765)
        self.assertFalse(report["run_as"]["root"])

    def _build_via_cli(self, extra):
        repo = self.tmp / "repo"
        (repo / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        approved = self.tmp / "approved"
        approved.mkdir(parents=True, exist_ok=True)
        connection = self.tmp / "connection.json"
        connection.write_text(
            json.dumps({"tokens": [{"role": "executor", "token_ref": "svc-executor", "scopes": ["queue_claim"], "token": "EXEC-RAW"}]}),
            encoding="utf-8",
        )

        captured = {}

        def fake_build_projection(repo_root, dest, **kwargs):
            Path(dest).mkdir(parents=True, exist_ok=True)
            return {"projection_dir": str(dest), "files": [], "context_pack_verdict": "ok"}

        orig = cw.build_projection
        cw.build_projection = fake_build_projection
        try:
            args = cw.build_parser().parse_args(
                [
                    "--image", "img:test",
                    "--prompt", "do x",
                    "--task-id", "AIPOS-1",
                    "--connection-json", str(connection),
                    "--approved-scratch-root", str(approved),
                    "--network", "lybra-net",
                    "--gate-url", "http://172.18.0.1:7118/mcp",
                    "--repo-root", str(repo),
                    "--dry-run",
                    *extra,
                ]
            )
            request = cw.build_request_from_args(args)
        finally:
            cw.build_projection = orig
        captured["argv"] = cw.build_docker_argv(request)
        return captured["argv"]


class TranscriptRedactionTests(unittest.TestCase):
    """AIPOS-198 F-c10: capture stdout/stderr, redact raw secrets, fail-closed."""

    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name).resolve()

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def test_redact_transcript_replaces_with_fingerprint(self) -> None:
        clean, hits = cw.redact_transcript("before SEKRIT-TOKEN after", ["SEKRIT-TOKEN"])
        self.assertNotIn("SEKRIT-TOKEN", clean)
        self.assertIn(f"«redacted:{cw.secret_fingerprint('SEKRIT-TOKEN')}»", clean)
        self.assertEqual(hits, [cw.secret_fingerprint("SEKRIT-TOKEN")])

    def test_redaction_needles_always_include_mcp_token(self) -> None:
        req = _request(self.tmp, mcp_token="LIVE-MCP", redaction_secrets=("ROLE-A", "LLM-KEY"))
        needles = cw.redaction_needles(req)
        self.assertIn("LIVE-MCP", needles)
        self.assertIn("ROLE-A", needles)
        self.assertIn("LLM-KEY", needles)
        # deduped, no empties
        self.assertEqual(len(needles), len(set(needles)))

    def test_transcript_block_redacts_fake_secrets_deep(self) -> None:
        secrets = ["FAKE-ROLE-TOKEN", "sk-fake-llm-key", "LIVE-MCP-TOKEN"]
        stdout = "calling gate with Bearer LIVE-MCP-TOKEN and key sk-fake-llm-key\n"
        stderr = "auth used FAKE-ROLE-TOKEN here\n"
        block = cw.build_transcript_block(stdout, stderr, secrets, captured=True)
        blob = json.dumps(block, ensure_ascii=False)
        for raw in secrets:
            self.assertNotIn(raw, blob)
        fps = block["redaction"]["redacted_fingerprints"]
        self.assertIn(cw.secret_fingerprint("LIVE-MCP-TOKEN"), fps)
        self.assertIn(cw.secret_fingerprint("sk-fake-llm-key"), fps)
        self.assertIn(cw.secret_fingerprint("FAKE-ROLE-TOKEN"), fps)
        self.assertTrue(block["captured"])

    def test_redaction_before_truncation_straddle(self) -> None:
        secret = "STRADDLE-SECRET-VALUE"
        # secret placed so it straddles the tail cut; padding pushes past max_bytes.
        padding = "A" * 100
        text = padding + secret + ("B" * 50)
        block = cw.build_transcript_block(text, "", [secret], captured=True, max_bytes=60)
        blob = json.dumps(block, ensure_ascii=False)
        self.assertNotIn(secret, blob)
        self.assertTrue(block["truncated"])
        self.assertEqual(block["stdout_bytes"], len(text.encode("utf-8")))

    def test_fail_closed_when_needle_survives(self) -> None:
        # secret_fingerprint is deterministic; simulate a needle that cannot be
        # redacted by monkeypatching redact_transcript to a no-op.
        orig = cw.redact_transcript
        cw.redact_transcript = lambda text, secrets: (text, [])
        try:
            with self.assertRaises(cw.ConfinedWorkerError):
                cw.build_transcript_block("leak RAW-SECRET here", "", ["RAW-SECRET"], captured=True)
        finally:
            cw.redact_transcript = orig

    def test_dry_run_report_has_empty_transcript(self) -> None:
        report = cw.run_confined_worker(_request(self.tmp, dry_run=True))
        self.assertIn("transcript", report)
        self.assertFalse(report["transcript"]["captured"])
        self.assertEqual(report["transcript"]["stdout"], "")

    def test_run_report_transcript_redacts_subprocess_output(self) -> None:
        # Drive run_confined_worker without docker by stubbing subprocess.run to
        # emit a fake leak; the report transcript must carry only fingerprints.
        import subprocess as _sp

        class _Completed:
            returncode = 0
            stdout = "gate ok; token EXEC-TOKEN-RAW-VALUE; key sk-ant-secret\n"
            stderr = ""

        req = _request(
            self.tmp,
            dry_run=False,
            run_as_uid=os.getuid(),
            run_as_gid=os.getgid(),
            redaction_secrets=("sk-ant-secret",),
        )
        orig_run = cw.subprocess.run
        cw.subprocess.run = lambda *a, **k: _Completed()
        try:
            report = cw.run_confined_worker(req)
        finally:
            cw.subprocess.run = orig_run
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("EXEC-TOKEN-RAW-VALUE", blob)
        self.assertNotIn("sk-ant-secret", blob)
        self.assertTrue(report["transcript"]["captured"])
        self.assertIn(cw.secret_fingerprint("EXEC-TOKEN-RAW-VALUE"), report["transcript"]["redaction"]["redacted_fingerprints"])


if __name__ == "__main__":
    unittest.main()
