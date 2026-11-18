"""F293: real argparse/main entry, isolated registered repos, no handler mocks."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_aipos_f184_queue_landing_supersede import rig, _git
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(rig):
    gov = rig["gov"]
    config = json.loads((gov / "project.json").read_text())
    repo = Path(config["code_repo"])
    config["repos"] = {"default": "mobile", "items": {"mobile": str(repo)}, "no_deploy": ["mobile"]}
    (gov / "project.json").write_text(json.dumps(config))
    return gov, repo, _git(repo, "rev-parse", "main")


def _snapshot(gov, repo):
    return (
        _git(repo, "show-ref"),
        _git(repo, "symbolic-ref", "HEAD"),
        {str(p.relative_to(gov)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in gov.rglob("*") if p.is_file()},
    )


def _cli(gov, *args):
    env = os.environ.copy()
    # Use this candidate's entry point, never the deployed CLI or a real connection.
    env["PYTHONPATH"] = str(ROOT)
    for key in tuple(env):
        if key.startswith(("LYBRA_", "AIPOS_")):
            env.pop(key)
    return subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(gov),
         "project", "init-branch", *args],
        cwd=gov, env=env, text=True, capture_output=True, timeout=30,
    )


def _args(sha, *, repo="mobile", branch="ota"):
    return ["--repo", repo, "--branch", branch, "--start-commit", sha, "--actor", "owner"]


def _json(result):
    # Governance-root announcements precede the actual JSON result.
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert len(lines) == 1, (result.stdout, result.stderr)
    return json.loads(lines[0])


@pytest.mark.parametrize("flags", [[], ["--dry-run"]], ids=["default", "explicit"])
def test_real_cli_preview_exact_plan_and_zero_writes(project, flags):
    gov, repo, sha = project
    before = _snapshot(gov, repo)
    result = _cli(gov, *_args(sha), *flags)
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = _json(result)
    assert report["ok"] and report["verdict"] != "BLOCK", report
    data = report["data"]
    assert data["branch_initialization"] and not data.get("wrote"), report
    assert data["branch_plan"] == {
        "repo": str(repo), "repo_name": "mobile", "branch": "ota", "start_commit": sha,
        "existing_commit": None, "action": "create",
    }
    assert _snapshot(gov, repo) == before


def test_real_cli_confirm_requires_original_owner_credentials(project):
    gov, repo, sha = project
    before = _snapshot(gov, repo)
    missing = gov / "absent-owner-connection.json"
    result = _cli(gov, *_args(sha), "--confirm", "--connection-json", str(missing))
    assert result.returncode == 1, (result.stdout, result.stderr)
    report = _json(result)
    # Real run_branch_init_cli -> mint_via_gate refuses before contacting any gate.
    assert report["ok"] is False and report["early"] is True and report["signed"] == []
    assert "凭据文件不存在" in report["error"] and str(missing) in report["error"]
    assert "AttributeError" not in result.stderr
    assert _snapshot(gov, repo) == before


@pytest.mark.parametrize("missing", ["--repo", "--branch", "--start-commit"])
def test_real_cli_missing_required_argument(project, missing):
    gov, repo, sha = project
    before = _snapshot(gov, repo)
    args = _args(sha)
    at = args.index(missing)
    del args[at:at + 2]
    result = _cli(gov, *args)
    assert result.returncode == 2
    assert "required" in result.stderr and missing in result.stderr
    assert _snapshot(gov, repo) == before


@pytest.mark.parametrize("repo_name,branch", [("mobile", "../bad"), ("unregistered", "ota")])
def test_real_cli_rejects_invalid_target_without_writes(project, repo_name, branch):
    gov, repo, sha = project
    before = _snapshot(gov, repo)
    result = _cli(gov, *_args(sha, repo=repo_name, branch=branch))
    assert result.returncode == 1, (result.stdout, result.stderr)
    report = _json(result)
    assert report["ok"] is False and report["reason"]
    assert "AttributeError" not in result.stderr
    assert _snapshot(gov, repo) == before
