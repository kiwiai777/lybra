"""AIPOS-213 — README + npm packaging guards (core lane).

Keeps the outward-facing README honest and its Quickstart real:
- claims ⊆ disclosure (references the ledger + runbook; DG-7 positioning; no over-claim);
- every `lybra <subcmd>` in the README is a real CLI subcommand;
- no broken install form (`pip install lybra[tui]` — lybra is npm-only, not on PyPI);
- package.json files array ships tools/ and excludes evidence/secrets.

AIPOS-215 (F-rg-1): the no-broken-install guard is extended from the README to *every*
user-visible install-instruction surface — the CLI `tui` help string and the missing-Textual
ImportError messages — so a future "fixed one surface, missed another" cannot recur.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_README = _REPO / "README.md"
_PKG = _REPO / "package.json"

# AIPOS-215: every user-visible install-instruction surface (README + the CLI strings a user
# hits when launching the TUI without Textual). The broken `pip install lybra[tui]` must appear
# on NONE of them.
_CLI_SURFACES = (
    _REPO / "tools" / "aipos_cli" / "aipos_cli.py",
    _REPO / "tools" / "lybra_tui" / "__main__.py",
)
_BROKEN_FORM = "pip install lybra[tui]"


def _real_subcommands() -> set[str]:
    # Source of truth: the subparsers.add_parser("<name>", ...) calls in aipos_cli.py.
    src = (_REPO / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    subs = set(re.findall(r'subparsers\.add_parser\(\s*"([a-z][a-z0-9\-]+)"', src))
    assert subs, "could not locate any CLI subcommands"
    return subs


class ReadmeGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = _README.read_text(encoding="utf-8")
        cls.pkg = json.loads(_PKG.read_text(encoding="utf-8"))

    # --- T1: claims ⊆ disclosure (references + DG-7 positioning + no over-claim) ---
    def test_references_disclosure_and_runbook(self) -> None:
        self.assertIn("docs/v1_disclosure.md", self.readme)
        self.assertIn("docs/v1_acceptance_runbook.md", self.readme)

    def test_dg7_positioning_no_overclaim(self) -> None:
        self.assertIn("Form B", self.readme)
        self.assertIn("Gate, not engine", self.readme)
        # "heterogeneous accountability loop" may appear ONLY as the rejected claim.
        self.assertRegex(self.readme, r"not[^\n]*heterogeneous")

    # --- T2: Quickstart commands real + no broken install ---
    def test_quickstart_commands_are_real(self) -> None:
        real = _real_subcommands()
        # Only parse fenced code blocks (not prose), and only same-line `lybra <subcmd>`.
        blocks = re.findall(r"```(.*?)```", self.readme, flags=re.DOTALL)
        used: set[str] = set()
        for block in blocks:
            for line in block.splitlines():
                code = line.split("#", 1)[0]  # drop inline comments (prose lives there)
                m = re.search(r"\blybra[ \t]+([a-z][a-z0-9\-]+)", code)
                if m:
                    used.add(m.group(1))
        unknown = used - real
        self.assertEqual(unknown, set(), f"README uses non-existent lybra subcommands: {unknown}")
        self.assertIn("serve", used)
        self.assertIn("tui", used)

    def test_no_broken_pip_install(self) -> None:
        # lybra is npm-only (not on PyPI); the TUI is enabled via `pip install textual`.
        self.assertNotIn("pip install lybra[tui]", self.readme)
        self.assertNotIn('pip install "lybra[tui]"', self.readme)
        self.assertRegex(self.readme, r"pip install\s+\"?textual")
        self.assertIn("NOT on PyPI", self.readme)

    # --- AIPOS-215 (F-rg-1): the same guard, extended to every CLI install-instruction surface ---
    def test_cli_install_instructions_have_no_broken_form(self) -> None:
        for path in _CLI_SURFACES:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                _BROKEN_FORM, text,
                f"{path.name} prints the broken install form `{_BROKEN_FORM}` "
                "(lybra is npm-only, not on PyPI)",
            )
            # the surface that guides TUI install must give the correct command
            self.assertIn(
                "pip install textual", text,
                f"{path.name} must guide the TUI install via `pip install textual`",
            )

    # --- T3: package.json files ship tools/ and exclude evidence/secrets ---
    def test_package_files_ship_tools_exclude_evidence(self) -> None:
        files = self.pkg.get("files", [])
        self.assertIn("tools/", files)  # bin/lybra spawns `python -m tools...`
        for excl in ("!**/task_cards/**", "!**/._*", "!**/__pycache__/**", "!tools/**/tests/**"):
            self.assertIn(excl, files, f"files array missing exclusion: {excl}")

    def test_package_metadata(self) -> None:
        self.assertEqual(self.pkg.get("name"), "lybra")
        self.assertEqual(self.pkg.get("version"), "0.2.0")
        self.assertIn("Form B", self.pkg.get("description", ""))
        self.assertIn("accountability", self.pkg.get("keywords", []))


if __name__ == "__main__":
    unittest.main()
