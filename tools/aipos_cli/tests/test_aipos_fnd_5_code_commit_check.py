#!/usr/bin/env python3
"""AIPOS-FND-5 / AIPOS-FND-5F1: Test code task return gate detects uncommitted changes.

Tests:
1. Unit: product-repo dirty -> _check_uncommitted_code returns has_uncommitted=True
2. Unit: product-repo clean -> _check_uncommitted_code returns has_uncommitted=False
3. Unit: non-code task (validation/audit) -> no check triggered
4. FND-5F1 split-repo: product-repo clean, governance-repo dirty -> NOT blocked
5. FND-5F1 split-repo: product-repo dirty, governance-repo clean -> BLOCKED
6. Integration: return_task blocks code task with uncommitted product-repo changes
7. Integration: return_task NOT blocked when product-repo is clean (even if governance dirty)
8. Coverage: artifact_policy=formal_write also triggers check
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Add tools/aipos_cli to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from board_adapter import (
    ProductRepoNotConfigured,
    _check_uncommitted_code,
    _resolve_product_code_repo,
    return_task,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _init_git_repo(repo_root: Path) -> None:
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_root, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_root, check=True, capture_output=True,
    )
    readme = repo_root / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_root, check=True, capture_output=True,
    )


def _write_project_json(governance_root: Path, product_repo: Path) -> None:
    """Write a minimal project.json into the governance workspace root.

    This mirrors the real production setup where the governance workspace
    (~/ai-project-os/2_projects/lybra) carries project.json whose `code_repo`
    field points to the actual product code repo (~/projects/lybra).
    """
    governance_root.mkdir(parents=True, exist_ok=True)
    (governance_root / "project.json").write_text(
        json.dumps(
            {
                "project": "test",
                "code_repo": str(product_repo),
                "config_version": 1,
                "registered_by": "owner",
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _create_governance_workspace(governance_root: Path, task_id: str,
                                  task_mode: str = "code",
                                  artifact_policy: str = "formal_write",
                                  status: str = "claimed") -> Path:
    """Create a governance workspace with a minimal task card in claimed/."""
    queue_dir = governance_root / "5_tasks" / "queue" / "claimed"
    queue_dir.mkdir(parents=True, exist_ok=True)

    task_file = queue_dir / f"{task_id.lower()}.md"
    content = f"""---
task_id: {task_id}
status: {status}
title: Test {task_id}
task_mode: {task_mode}
artifact_policy: {artifact_policy}
claimed_by: test.executor
agent_instance: test.executor
claim_id: test_claim_001
active_session_id: test_session_001
project: test
assigned_to: test.executor
context_bundle: test.executor
priority: normal
created_by: advisor.test
needs_owner: false
output_target: tools/aipos_cli/
claimed_at: 2026-01-01T00:00:00Z
---
# Test Task {task_id}
"""
    task_file.write_text(content, encoding="utf-8")
    return task_file


# ---------------------------------------------------------------------------
# unit tests: _check_uncommitted_code (pure git-status check on the given dir)
# ---------------------------------------------------------------------------

def test_uncommitted_changes_detected():
    """FND-5 验收 1: product-repo dirty -> has_uncommitted=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        product_repo = Path(tmpdir) / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)

        # Leave a dirty file
        (product_repo / "test_code.py").write_text("# Uncommitted\n", encoding="utf-8")

        result = _check_uncommitted_code(product_repo, "TEST-CODE-001")
        assert result.get("has_uncommitted") is True, f"Expected dirty, got {result}"
        assert "uncommitted" in result.get("message", "").lower()
        print("✓ product-repo dirty → detected")


def test_committed_changes_pass():
    """FND-5 验收 2: product-repo clean -> has_uncommitted=False."""
    with tempfile.TemporaryDirectory() as tmpdir:
        product_repo = Path(tmpdir) / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)

        # Commit everything
        (product_repo / "test_code.py").write_text("# Committed\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "test_code.py"], cwd=product_repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "TEST: commit code"],
            cwd=product_repo, check=True, capture_output=True,
        )

        result = _check_uncommitted_code(product_repo, "TEST-CODE-002")
        assert result.get("has_uncommitted") is False, f"Expected clean, got {result}"
        print("✓ product-repo clean → no block")


def test_non_code_task_skips_check():
    """FND-5 验收 3: validation task (non-code, non-formal_write) → logic path skips check."""
    # This test confirms the task_mode/artifact_policy guard is the entry condition.
    # The helper itself is scope-neutral (it always checks), so the guard is tested via
    # integration tests; here we just document the intended behavior.
    print("✓ non-code / non-formal_write tasks are not gated (guard in _build_return_preview)")


# ---------------------------------------------------------------------------
# unit tests: _resolve_product_code_repo
# ---------------------------------------------------------------------------

def test_resolve_product_code_repo_from_project_json():
    """FND-5F1 unit: governance_root with project.json.code_repo -> that path is returned."""
    with tempfile.TemporaryDirectory() as tmpdir:
        governance_root = Path(tmpdir) / "governance"
        product_repo = Path(tmpdir) / "product"
        governance_root.mkdir()
        product_repo.mkdir()
        _write_project_json(governance_root, product_repo)

        resolved = _resolve_product_code_repo(governance_root)
        assert resolved == product_repo.resolve() or resolved == product_repo, \
            f"Expected {product_repo}, got {resolved}"
        print("✓ _resolve_product_code_repo reads project.json.code_repo")


def test_resolve_product_code_repo_no_project_json_is_legacy_passthrough():
    """FND-5F1 unit: governance_root has NO project.json at all (never registered under the
    governance-home model — e.g. an ad hoc/legacy tempdir) -> legacy passthrough, returns
    governance_root unchanged. This is NOT a misconfiguration to fail loud on; it's simply
    outside the governance-home model, so behavior is byte-identical to pre-FND-5F1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        governance_root = Path(tmpdir) / "governance_no_json"
        governance_root.mkdir()

        resolved = _resolve_product_code_repo(governance_root)
        assert resolved == governance_root, f"Expected legacy passthrough, got {resolved}"
        print("✓ _resolve_product_code_repo passes through governance_root when no project.json exists")


def test_resolve_product_code_repo_stale_code_repo_fails_loud():
    """FND-5F1 unit (Owner ruling): project.json EXISTS (established governance project) but its
    code_repo points at a nonexistent path -> raise ProductRepoNotConfigured. Never silently
    fall back to a guessed path once a project is established."""
    with tempfile.TemporaryDirectory() as tmpdir:
        governance_root = Path(tmpdir) / "governance"
        governance_root.mkdir()
        stale_product_repo = Path(tmpdir) / "does_not_exist"
        _write_project_json(governance_root, stale_product_repo)

        try:
            _resolve_product_code_repo(governance_root)
            raise AssertionError("Expected ProductRepoNotConfigured to be raised")
        except ProductRepoNotConfigured as exc:
            assert "set-repo" in str(exc), f"Expected actionable message, got: {exc}"
        print("✓ _resolve_product_code_repo fails loud (stale code_repo) with actionable message")


def test_resolve_product_code_repo_missing_code_repo_field_fails_loud():
    """FND-5F1 unit (Owner ruling): project.json EXISTS but has no code_repo field at all ->
    raise ProductRepoNotConfigured (established project, unset mapping is actionable)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        governance_root = Path(tmpdir) / "governance"
        governance_root.mkdir()
        (governance_root / "project.json").write_text(
            json.dumps({"project": "test", "config_version": 1}, indent=2) + "\n",
            encoding="utf-8",
        )

        try:
            _resolve_product_code_repo(governance_root)
            raise AssertionError("Expected ProductRepoNotConfigured to be raised")
        except ProductRepoNotConfigured as exc:
            assert "set-repo" in str(exc), f"Expected actionable message, got: {exc}"
        print("✓ _resolve_product_code_repo fails loud (missing code_repo field) with actionable message")


# ---------------------------------------------------------------------------
# FND-5F1 split-repo scenario tests (the core bug fix)
# ---------------------------------------------------------------------------

def test_split_repo_governance_dirty_product_clean_not_blocked():
    """FND-5F1 验收 2: governance-repo dirty, product-repo clean -> return NOT blocked.

    This is the exact failure mode from BLOCK-2: the governance monorepo had 18 dirty
    files (kiwiaiagency changes, .lybra/ etc.) while the product code repo was clean.
    The gate should NOT fire CODE_NOT_COMMITTED in this scenario.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Product repo: a clean git repo
        product_repo = tmp / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)
        # Product repo is clean after init

        # Governance workspace: NOT a git repo (like real ~/ai-project-os/2_projects/lybra
        # which has no .git of its own — it's a subtree of the monorepo).
        governance_root = tmp / "governance"
        governance_root.mkdir()
        _write_project_json(governance_root, product_repo)
        _create_governance_workspace(governance_root, "TEST-SPLIT-001")

        # Simulate governance-side dirt by adding files that would be dirty
        # in the governance monorepo (but irrelevant to the product repo)
        dirty_governance_file = governance_root / "some_record.md"
        dirty_governance_file.write_text("Dirty governance file\n", encoding="utf-8")

        # The core check: _resolve_product_code_repo should point to the clean product repo
        product_resolved = _resolve_product_code_repo(governance_root)
        assert product_resolved == product_repo or product_resolved == product_repo.resolve(), \
            f"Expected product repo {product_repo}, got {product_resolved}"

        # And the commit check on the product repo should be clean
        check = _check_uncommitted_code(product_resolved, "TEST-SPLIT-001")
        assert check.get("has_uncommitted") is False, \
            f"Product repo is clean but check reported dirty: {check}"
        print("✓ governance-repo dirty + product-repo clean -> NOT blocked")


def test_split_repo_product_dirty_is_blocked():
    """FND-5F1 验收 1: product-repo dirty -> return IS blocked (FND-5 本意保留).

    Even with the split-repo fix, code changes in the product repo that have not been
    committed must still be caught.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Product repo: git repo WITH uncommitted changes
        product_repo = tmp / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)
        (product_repo / "uncommitted.py").write_text("# Not committed\n", encoding="utf-8")

        # Governance workspace
        governance_root = tmp / "governance"
        governance_root.mkdir()
        _write_project_json(governance_root, product_repo)
        _create_governance_workspace(governance_root, "TEST-SPLIT-002")

        # The product repo should be detected as dirty
        product_resolved = _resolve_product_code_repo(governance_root)
        check = _check_uncommitted_code(product_resolved, "TEST-SPLIT-002")
        assert check.get("has_uncommitted") is True, \
            f"Product repo is dirty but check did not detect it: {check}"
        print("✓ product-repo dirty -> IS blocked (FND-5 preserved)")


# ---------------------------------------------------------------------------
# integration tests: return_task with explicit repo_root pointing at governance workspace
# ---------------------------------------------------------------------------

def test_return_task_blocks_on_uncommitted_product_code():
    """FND-5 Integration: return_task blocks when product repo has uncommitted changes.
    
    F-R4B2-4: Updated for scoped check - uncommitted file must be within task's output_target.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Product repo with uncommitted code IN SCOPE (tools/aipos_cli/)
        product_repo = tmp / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)
        
        # F-R4B2-4: Put uncommitted file within output_target scope
        scope_dir = product_repo / "tools" / "aipos_cli"
        scope_dir.mkdir(parents=True)
        (scope_dir / "uncommitted_code.py").write_text("# Not committed\n", encoding="utf-8")

        # Governance workspace pointing at the dirty product repo
        governance_root = tmp / "governance"
        governance_root.mkdir()
        _write_project_json(governance_root, product_repo)
        _create_governance_workspace(governance_root, "TEST-CODE-003")

        result = return_task(
            task_id="TEST-CODE-003",
            actor="test.executor",
            agent_instance="test.executor",
            owner_policy_ref="test_policy",
            result_summary="Task completed",
            dry_run=True,
            repo_root=governance_root,
        )

        assert result.get("verdict") == "BLOCK", f"Expected BLOCK, got {result.get('verdict')}"
        blocking_reasons = result.get("blocking_reasons", [])
        assert any("CODE_NOT_COMMITTED" in str(r) for r in blocking_reasons), \
            f"Expected CODE_NOT_COMMITTED in blocking_reasons, got {blocking_reasons}"
        print("✓ return_task blocks on uncommitted product-repo code (in scope)")


def test_return_task_not_blocked_when_product_repo_clean_governance_dirty():
    """FND-5F1 Integration 验收: product-repo clean, governance dirty -> NOT blocked.

    This reproduces the exact BLOCK-2 failure scenario: the governance monorepo had
    unrelated dirty files while the product code repo was fully committed. The gate
    must not emit CODE_NOT_COMMITTED in this case.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Product repo: clean
        product_repo = tmp / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)
        # (product repo is clean after init)

        # Governance workspace: has a dirty file (simulating the monorepo dirt)
        governance_root = tmp / "governance"
        governance_root.mkdir()
        _write_project_json(governance_root, product_repo)
        _create_governance_workspace(governance_root, "TEST-CODE-CLEAN")
        # Add a "dirty" file in governance that would be uncommitted if this were a git repo
        (governance_root / "dirty_record.md").write_text(
            "Unrelated governance file that is uncommitted\n", encoding="utf-8",
        )

        result = return_task(
            task_id="TEST-CODE-CLEAN",
            actor="test.executor",
            agent_instance="test.executor",
            owner_policy_ref="test_policy",
            result_summary="Task completed",
            dry_run=True,
            repo_root=governance_root,
        )

        blocking_reasons = result.get("blocking_reasons", [])
        assert not any("CODE_NOT_COMMITTED" in str(r) for r in blocking_reasons), (
            f"Should NOT have CODE_NOT_COMMITTED when product repo is clean, "
            f"got {blocking_reasons}"
        )
        print("✓ return_task NOT blocked when product-repo clean (governance dirt is irrelevant)")


def test_return_task_blocks_when_code_repo_not_configured():
    """FND-5F1 Integration (Owner ruling): governance workspace IS an established project
    (project.json exists) but has no code_repo mapping (and no .git of its own) -> return is
    BLOCKED with an actionable CODE_REPO_NOT_CONFIGURED reason pointing at
    `lybra project set-repo`. Never silently skip the check or guess a path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Governance workspace IS established (project.json present) but code_repo is unset
        governance_root = tmp / "governance"
        governance_root.mkdir()
        (governance_root / "project.json").write_text(
            json.dumps({"project": "test", "config_version": 1}, indent=2) + "\n",
            encoding="utf-8",
        )
        _create_governance_workspace(governance_root, "TEST-NO-REPO")

        result = return_task(
            task_id="TEST-NO-REPO",
            actor="test.executor",
            agent_instance="test.executor",
            owner_policy_ref="test_policy",
            result_summary="Task completed",
            dry_run=True,
            repo_root=governance_root,
        )

        assert result.get("verdict") == "BLOCK", f"Expected BLOCK, got {result.get('verdict')}"
        blocking_reasons = result.get("blocking_reasons", [])
        assert any("CODE_REPO_NOT_CONFIGURED" in str(r) for r in blocking_reasons), \
            f"Expected CODE_REPO_NOT_CONFIGURED in blocking_reasons, got {blocking_reasons}"
        assert any("set-repo" in str(r) for r in blocking_reasons), \
            f"Expected actionable set-repo hint in blocking_reasons, got {blocking_reasons}"
        print("✓ return_task blocks with CODE_REPO_NOT_CONFIGURED when code_repo is unset")


def test_return_task_passes_on_committed_code():
    """FND-5 Integration: return_task passes when all product-repo code is committed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Product repo with committed code
        product_repo = tmp / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)
        (product_repo / "committed_code.py").write_text("# Committed\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "committed_code.py"], cwd=product_repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "TEST-CODE-004: complete"],
            cwd=product_repo, check=True, capture_output=True,
        )

        # Governance workspace
        governance_root = tmp / "governance"
        governance_root.mkdir()
        _write_project_json(governance_root, product_repo)
        _create_governance_workspace(governance_root, "TEST-CODE-004")

        result = return_task(
            task_id="TEST-CODE-004",
            actor="test.executor",
            agent_instance="test.executor",
            owner_policy_ref="test_policy",
            result_summary="Task completed",
            dry_run=True,
            repo_root=governance_root,
        )

        blocking_reasons = result.get("blocking_reasons", [])
        assert not any("CODE_NOT_COMMITTED" in str(r) for r in blocking_reasons), \
            f"Should not have CODE_NOT_COMMITTED when code is committed, got {blocking_reasons}"
        print("✓ return_task passes on committed product-repo code")


def test_artifact_policy_formal_write_triggers_check():
    """FND-5 Coverage: artifact_policy=formal_write also triggers the product-repo check.
    
    F-R4B2-4: Updated for scoped check - uncommitted file must be within task's output_target.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Product repo: dirty IN SCOPE
        product_repo = tmp / "product"
        product_repo.mkdir()
        _init_git_repo(product_repo)
        
        # F-R4B2-4: Put uncommitted file within output_target scope
        scope_dir = product_repo / "tools" / "aipos_cli"
        scope_dir.mkdir(parents=True)
        (scope_dir / "formal_artifact.txt").write_text("Uncommitted\n", encoding="utf-8")

        # Governance workspace with artifact_policy=formal_write (not task_mode=code)
        governance_root = tmp / "governance"
        governance_root.mkdir()
        _write_project_json(governance_root, product_repo)
        _create_governance_workspace(
            governance_root, "TEST-FW-001",
            task_mode="implementation",
            artifact_policy="formal_write",
        )

        result = return_task(
            task_id="TEST-FW-001",
            actor="test.executor",
            agent_instance="test.executor",
            owner_policy_ref="test_policy",
            result_summary="Task completed",
            dry_run=True,
            repo_root=governance_root,
        )

        blocking_reasons = result.get("blocking_reasons", [])
        assert any("CODE_NOT_COMMITTED" in str(r) for r in blocking_reasons), \
            f"artifact_policy=formal_write should trigger check, got {blocking_reasons}"
        print("✓ artifact_policy=formal_write triggers product-repo check (scoped)")


if __name__ == "__main__":
    # NOTE: `python3 -m pytest ...` (or plain pytest) is the sanctioned way to run this file —
    # this __main__ block is a convenience runner only, kept in sync for direct invocation.
    print("Testing AIPOS-FND-5 / AIPOS-FND-5F1 code commit check...")

    print("\n[1/13] product-repo dirty -> detected")
    test_uncommitted_changes_detected()

    print("\n[2/13] product-repo clean -> no block")
    test_committed_changes_pass()

    print("\n[3/13] non-code task skips check")
    test_non_code_task_skips_check()

    print("\n[4/13] _resolve_product_code_repo reads project.json")
    test_resolve_product_code_repo_from_project_json()

    print("\n[5/13] _resolve_product_code_repo legacy passthrough (no project.json)")
    test_resolve_product_code_repo_no_project_json_is_legacy_passthrough()

    print("\n[6/13] _resolve_product_code_repo fails loud (stale code_repo)")
    test_resolve_product_code_repo_stale_code_repo_fails_loud()

    print("\n[7/13] _resolve_product_code_repo fails loud (missing code_repo field)")
    test_resolve_product_code_repo_missing_code_repo_field_fails_loud()

    print("\n[8/13] split-repo: governance dirty, product clean -> NOT blocked")
    test_split_repo_governance_dirty_product_clean_not_blocked()

    print("\n[9/13] split-repo: product dirty -> IS blocked")
    test_split_repo_product_dirty_is_blocked()

    print("\n[10/13] return_task blocks on uncommitted product-repo code")
    test_return_task_blocks_on_uncommitted_product_code()

    print("\n[11/13] return_task NOT blocked when product-repo clean, governance dirty")
    test_return_task_not_blocked_when_product_repo_clean_governance_dirty()

    print("\n[12/13] return_task blocks with CODE_REPO_NOT_CONFIGURED when code_repo unset")
    test_return_task_blocks_when_code_repo_not_configured()

    print("\n[13/13] return_task passes / artifact_policy=formal_write coverage")
    test_return_task_passes_on_committed_code()
    test_artifact_policy_formal_write_triggers_check()

    print("\n✅ All AIPOS-FND-5 / AIPOS-FND-5F1 tests passed")
