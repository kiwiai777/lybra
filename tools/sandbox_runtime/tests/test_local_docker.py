from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.sandbox_runtime.local_docker import (
    DEFAULT_NETWORK,
    LocalDockerRequest,
    SandboxValidationError,
    build_docker_argv,
    provider_descriptor,
    run_local_docker,
    validate_request,
)


class LocalDockerRuntimeTests(unittest.TestCase):
    def test_descriptor_matches_aipos_90_mvp_boundary(self) -> None:
        descriptor = provider_descriptor()
        self.assertEqual(descriptor["provider"], "local_docker")
        self.assertEqual(descriptor["execution_model"], "ephemeral_worker")
        self.assertEqual(descriptor["credential_boundary"]["credential_mode"], "none")
        self.assertEqual(descriptor["resource_limits"]["network_policy"], "none_by_default")
        self.assertTrue(descriptor["audit"]["independent_audit_required"])

    def test_build_argv_uses_readonly_mount_and_network_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = LocalDockerRequest(
                image="python:3.12-alpine",
                command=("python", "--version"),
                workspace=Path(temp),
                timeout_seconds=5,
                cpus="1",
                memory="128m",
            )
            argv = build_docker_argv(request)

        self.assertEqual(argv[:7], ["docker", "run", "--rm", "--pull", "never", "--network", DEFAULT_NETWORK])
        self.assertIn("--mount", argv)
        self.assertIn("readonly", " ".join(argv))
        self.assertIn("--workdir", argv)
        self.assertIn("--cpus", argv)
        self.assertIn("--memory", argv)
        self.assertEqual(argv[-3:], ["python:3.12-alpine", "python", "--version"])

    def test_validation_rejects_missing_image_command_network_and_mount(self) -> None:
        with self.assertRaisesRegex(SandboxValidationError, "image"):
            validate_request(LocalDockerRequest(image="", command=("echo", "hi")))
        with self.assertRaisesRegex(SandboxValidationError, "command"):
            validate_request(LocalDockerRequest(image="alpine:3.20", command=()))
        with self.assertRaisesRegex(SandboxValidationError, "network none"):
            validate_request(LocalDockerRequest(image="alpine:3.20", command=("true",), network="bridge"))
        with self.assertRaisesRegex(SandboxValidationError, "mount path"):
            validate_request(LocalDockerRequest(image="alpine:3.20", command=("true",), workspace=Path("/missing/nope")))

    def test_dry_run_does_not_call_subprocess(self) -> None:
        request = LocalDockerRequest(image="alpine:3.20", command=("echo", "hello"), dry_run=True)
        with patch("tools.sandbox_runtime.local_docker.subprocess.run") as run:
            report = run_local_docker(request)

        run.assert_not_called()
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "dry_run")
        self.assertFalse(report["writes_enabled"])
        self.assertFalse(report["credentials_injected"])

    def test_run_success_and_failure_reports(self) -> None:
        success = subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="ok\n", stderr="")
        failure = subprocess.CompletedProcess(args=["docker"], returncode=7, stdout="", stderr="bad\n")
        request = LocalDockerRequest(image="alpine:3.20", command=("true",))

        with patch("tools.sandbox_runtime.local_docker.subprocess.run", Mock(return_value=success)) as run:
            report = run_local_docker(request)
        run.assert_called_once()
        self.assertTrue(report["ok"])
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["stdout"], "ok\n")

        with patch("tools.sandbox_runtime.local_docker.subprocess.run", Mock(return_value=failure)):
            report = run_local_docker(request)
        self.assertFalse(report["ok"])
        self.assertEqual(report["exit_code"], 7)
        self.assertEqual(report["stderr"], "bad\n")

    def test_timeout_and_docker_unavailable_reports(self) -> None:
        request = LocalDockerRequest(image="alpine:3.20", command=("sleep", "10"), timeout_seconds=1)
        timeout = subprocess.TimeoutExpired(cmd=["docker"], timeout=1, output="partial", stderr="late")
        with patch("tools.sandbox_runtime.local_docker.subprocess.run", Mock(side_effect=timeout)):
            report = run_local_docker(request)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "timeout")
        self.assertTrue(report["timed_out"])

        with patch("tools.sandbox_runtime.local_docker.subprocess.run", Mock(side_effect=FileNotFoundError("docker"))):
            report = run_local_docker(request)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "docker_unavailable")


if __name__ == "__main__":
    unittest.main()
