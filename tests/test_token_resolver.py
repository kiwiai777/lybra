"""AIPOS-F59: Tests for token_resolver module."""

import json
import tempfile
from pathlib import Path

import pytest

from tools.aipos_cli.token_resolver import (
    detect_wrong_domain_tokens,
    get_token_for_role_and_project,
    retire_token_entry,
    token_fingerprint,
)


def test_token_fingerprint():
    """Token fingerprint returns sha256 prefix."""
    fp = token_fingerprint("test_token_123")
    assert fp.startswith("sha256:")
    assert len(fp) == len("sha256:") + 12


def test_get_token_by_role_only():
    """Get token by role when no project filtering."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        data = {
            "tokens": [
                {"role": "owner", "token": "owner_token_123", "projects": ["lybra"]},
                {"role": "executor", "token": "exec_token_456", "projects": ["lybra"]},
            ]
        }
        json.dump(data, f)
        path = Path(f.name)
    
    try:
        token = get_token_for_role_and_project(path, "owner", project=None)
        assert token == "owner_token_123"
    finally:
        path.unlink()


def test_get_token_by_role_and_project():
    """Get token by (role, project) filters correctly."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        data = {
            "tokens": [
                {"role": "planner", "token": "planner_lybra", "projects": ["lybra"]},
                {"role": "planner", "token": "planner_chris", "projects": ["chris-huibojin"]},
            ]
        }
        json.dump(data, f)
        path = Path(f.name)
    
    try:
        # Should get chris token when filtering by chris-huibojin
        token = get_token_for_role_and_project(path, "planner", "chris-huibojin")
        assert token == "planner_chris"
        
        # Should get lybra token when filtering by lybra
        token = get_token_for_role_and_project(path, "planner", "lybra")
        assert token == "planner_lybra"
    finally:
        path.unlink()


def test_get_token_excludes_retired():
    """Retired tokens are excluded by default."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        data = {
            "tokens": [
                {"role": "owner", "token": "old_token", "projects": ["lybra"], "retired": True},
                {"role": "owner", "token": "new_token", "projects": ["lybra"]},
            ]
        }
        json.dump(data, f)
        path = Path(f.name)
    
    try:
        token = get_token_for_role_and_project(path, "owner", "lybra")
        assert token == "new_token"
    finally:
        path.unlink()


def test_get_token_wrong_project_raises():
    """Requesting wrong project domain raises ValueError."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        data = {
            "tokens": [
                {"role": "planner", "token": "planner_lybra", "projects": ["lybra"]},
            ]
        }
        json.dump(data, f)
        path = Path(f.name)
    
    try:
        with pytest.raises(ValueError, match="project domain="):
            get_token_for_role_and_project(path, "planner", "chris-huibojin")
    finally:
        path.unlink()


def test_retire_token_entry():
    """Retire marks tokens as retired without deleting them."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        data = {
            "tokens": [
                {"role": "owner", "token": "old_token", "projects": ["lybra"]},
                {"role": "executor", "token": "exec_token", "projects": ["lybra"]},
            ]
        }
        json.dump(data, f)
        path = Path(f.name)
    
    try:
        count = retire_token_entry(path, role="owner", reason="test retirement")
        assert count == 1
        
        # Read back and verify
        updated = json.loads(path.read_text())
        owner_entry = [t for t in updated["tokens"] if t["role"] == "owner"][0]
        assert owner_entry["retired"] is True
        assert "retired_at" in owner_entry
        assert owner_entry["retired_reason"] == "test retirement"
        
        # Executor should not be retired
        exec_entry = [t for t in updated["tokens"] if t["role"] == "executor"][0]
        assert "retired" not in exec_entry or not exec_entry["retired"]
    finally:
        path.unlink()


def test_detect_wrong_domain_tokens():
    """Detect tokens with wrong project domain."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        data = {
            "tokens": [
                {"role": "planner", "token": "planner_lybra", "projects": ["lybra"]},  # Wrong!
                {"role": "executor", "token": "exec_chris", "projects": ["chris-huibojin"]},  # Correct
            ]
        }
        json.dump(data, f)
        path = Path(f.name)
    
    try:
        wrong = detect_wrong_domain_tokens(path, "chris-huibojin")
        assert len(wrong) == 1
        assert wrong[0]["role"] == "planner"
        assert "lybra" in wrong[0]["mismatch_reason"]
    finally:
        path.unlink()


def test_legacy_token_without_projects_field():
    """Legacy tokens (no projects field) are flagged in reconcile."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        data = {
            "tokens": [
                {"role": "owner", "token": "legacy_token"},  # No projects field
            ]
        }
        json.dump(data, f)
        path = Path(f.name)
    
    try:
        wrong = detect_wrong_domain_tokens(path, "lybra")
        assert len(wrong) == 1
        assert "no projects field" in wrong[0]["mismatch_reason"]
    finally:
        path.unlink()
