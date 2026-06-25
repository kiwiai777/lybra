"""AIPOS-218 WS4 — manifest static contract guard.

Statically parses every templates/*/manifest.md and asserts that its frontmatter uses only
the frozen-contract shapes (ruling 1):

  scalar / bool / int / float / empty / quoted scalar / scalar list
  depth-1 nested maps of scalars or scalar-lists (e.g. output_policy:, controlled_execute:)

Forbidden (turn RED, force an explicit decision):
  sequences-of-mappings  (- key: ... inside a list)
  depth >= 2 (nested maps inside nested maps)
  block scalars (| or >)
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "templates"

_BLOCK_SCALAR_HEADER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*: [|>]")
_SEQUENCE_OF_MAPS_ITEM = re.compile(r"^\s*- [a-zA-Z_][a-zA-Z0-9_]*:")


def _parse_frontmatter_text(text: str) -> list[str]:
    """Return the raw frontmatter lines (between --- delimiters), or []."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return []


def _check_manifest(path: Path) -> list[str]:
    """Return a list of contract-violation messages for the given manifest file."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    fm_lines = _parse_frontmatter_text(text)
    if not fm_lines:
        violations.append(f"{path.name}: no frontmatter found")
        return violations

    # Track indentation depth to detect depth >= 2 nesting.
    in_nested = False  # inside a depth-1 nested map or list
    for raw in fm_lines:
        line = raw  # preserve indentation for checks
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip(" "))

        # Block scalar header is always forbidden.
        if _BLOCK_SCALAR_HEADER.match(stripped):
            violations.append(f"{path.name}: block scalar forbidden: {raw!r}")
            continue

        # Sequences-of-mappings: a list item "- key: ..." is forbidden.
        if _SEQUENCE_OF_MAPS_ITEM.match(raw):
            violations.append(f"{path.name}: sequence-of-mappings forbidden: {raw!r}")
            continue

        if indent == 0:
            # Top-level key; check if bare (start of nested block) or has value.
            in_nested = ":" in stripped and stripped.endswith(":")
        elif indent == 2:
            # One level deep: allowed (depth-1 nested maps / scalar lists).
            pass
        elif indent > 2:
            # depth >= 2 is over-bound.
            violations.append(
                f"{path.name}: depth >= 2 nesting forbidden (indent={indent}): {raw!r}"
            )

    return violations


class ManifestContractTests(unittest.TestCase):
    def test_every_bundled_manifest_respects_contract(self) -> None:
        manifests = list(TEMPLATES_DIR.rglob("manifest.md"))
        self.assertGreater(len(manifests), 0, "No manifest.md files found under templates/")
        all_violations: list[str] = []
        for path in sorted(manifests):
            all_violations.extend(_check_manifest(path))
        self.assertEqual(
            all_violations,
            [],
            "Manifest contract violations:\n" + "\n".join(f"  {v}" for v in all_violations),
        )

    def test_manifests_are_parseable_by_fallback(self) -> None:
        """Every manifest must parse without warnings on the fallback parser (no PyYAML needed)."""
        from tools.aipos_cli.frontmatter import _fallback_parse

        manifests = list(TEMPLATES_DIR.rglob("manifest.md"))
        self.assertGreater(len(manifests), 0)
        for path in sorted(manifests):
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            if not lines or lines[0].strip() != "---":
                continue
            fm_text = ""
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    fm_text = "\n".join(lines[1:i])
                    break
            data, warnings = _fallback_parse(fm_text)
            self.assertEqual(
                warnings,
                [],
                f"{path.name}: fallback parser warnings: {warnings}",
            )
            self.assertIsInstance(data, dict)
            self.assertIn("template_id", data, f"{path.name}: missing template_id")


if __name__ == "__main__":
    unittest.main()
