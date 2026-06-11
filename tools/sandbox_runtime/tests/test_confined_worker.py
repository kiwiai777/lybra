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
        self.assertNotIn("--user", argv)

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


if __name__ == "__main__":
    unittest.main()
