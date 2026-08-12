#!/usr/bin/env python3
"""AIPOS-R4B-2: Simple test for scoped commit check functionality."""

from pathlib import Path
from tools.aipos_cli.scoped_commit_check import (
    check_uncommitted_in_scope,
    resolve_check_scope_from_task,
)

# Test 1: resolve_check_scope_from_task
print("=" * 60)
print("Test 1: resolve_check_scope_from_task")
print("=" * 60)

# Code task with output_target
metadata1 = {
    "task_mode": "code",
    "output_target": "tools/",
    "artifact_scope": "loop自助动词层"
}
scope1 = resolve_check_scope_from_task(metadata1)
print(f"Code task with output_target='tools/': {scope1}")
assert scope1 == ["tools/"], f"Expected ['tools/'], got {scope1}"

# Non-code docs task
metadata2 = {
    "task_mode": "docs",
    "artifact_scope": "documentation update"
}
scope2 = resolve_check_scope_from_task(metadata2)
print(f"Docs task (non-code): {scope2}")
assert scope2 == [], f"Expected [], got {scope2}"

# Code task without output_target, artifact_scope with path hint
metadata3 = {
    "task_mode": "code",
    "artifact_policy": "formal_write",
    "artifact_scope": "schema/ changes for role registry"
}
scope3 = resolve_check_scope_from_task(metadata3)
print(f"Code task with artifact_scope containing path: {scope3}")
assert scope3 == ["schema/"], f"Expected ['schema/'], got {scope3}"

# Code task with no clear path - fallback to None (full repo check)
metadata4 = {
    "task_mode": "code",
    "artifact_scope": "various improvements"
}
scope4 = resolve_check_scope_from_task(metadata4)
print(f"Code task without clear path (fallback): {scope4}")
assert scope4 is None, f"Expected None, got {scope4}"

print("\n✓ All resolve_check_scope_from_task tests passed!\n")

# Test 2: check_uncommitted_in_scope (dry test, doesn't need actual git repo)
print("=" * 60)
print("Test 2: check_uncommitted_in_scope function signature")
print("=" * 60)

# Just verify function can be called (will skip actual git check if not in git repo)
result = check_uncommitted_in_scope(
    Path.cwd(),
    "TEST-TASK",
    scoped_paths=["tools/"],
)
print(f"Scoped check result keys: {sorted(result.keys())}")
assert "has_uncommitted" in result
assert "scoped" in result
print(f"  has_uncommitted: {result.get('has_uncommitted')}")
print(f"  scoped: {result.get('scoped')}")
print(f"  skip_reason: {result.get('skip_reason', 'N/A')}")

print("\n✓ check_uncommitted_in_scope function signature test passed!\n")

print("=" * 60)
print("All tests passed! ✓")
print("=" * 60)
