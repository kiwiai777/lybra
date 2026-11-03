"""AIPOS-F290 件① 验收① — relay/ is a separate subsystem: the gate core never imports it, it imports nothing of the gate,
calls no gate verb, spawns no process, and uses the standard library only (no runtime dependency added).

Scanner = AST over every .py under tools/ and web/ (gate side) and relay/ (relay side); an injected violation of each
kind is caught by the same scanner functions (so a broken scanner cannot pass silently).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_DIRS = ("tools", "web")
RELAY_DIR = "relay"
SKIP_PARTS = {"__pycache__", "node_modules", ".deploy", ".worktrees"}
DYNAMIC_IMPORT_RE = re.compile(r"""(?:import_module|__import__)\(\s*["']relay(?:\.|["'])""")
GATE_VERB_RE = re.compile(r"\blybra_[a-z]+_[a-z_]+\b")
FORBIDDEN_RELAY_MODULES = {"subprocess", "multiprocessing", "tools", "web"}


def _py_files(base: str) -> list[Path]:
    root = REPO_ROOT / base
    return sorted(p for p in root.rglob("*.py") if not SKIP_PARTS & set(p.relative_to(REPO_ROOT).parts))


def imported_modules(source: str) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
    return mods


def gate_side_violations(rel: str, source: str) -> list[str]:
    hits = [f"{rel}: imports {m}" for m in sorted(imported_modules(source)) if m == "relay" or m.startswith("relay.")]
    if DYNAMIC_IMPORT_RE.search(source):
        hits.append(f"{rel}: dynamic import of relay")
    return hits


def relay_side_violations(rel: str, source: str) -> list[str]:
    hits: list[str] = []
    for mod in sorted(imported_modules(source)):
        top = mod.split(".")[0]
        if top == "relay":
            continue
        if top in FORBIDDEN_RELAY_MODULES:
            hits.append(f"{rel}: imports {mod} (gate internals / process spawning are off-limits)")
        elif top not in sys.stdlib_module_names and top != "__future__":
            hits.append(f"{rel}: imports non-stdlib {mod}")
    for match in GATE_VERB_RE.finditer(source):
        hits.append(f"{rel}: names gate verb {match.group(0)}")
    return hits


def test_gate_core_never_imports_relay():
    files = [p for base in GATE_DIRS for p in _py_files(base)]
    assert len(files) > 50   # scanner really walked the gate code
    hits = [h for p in files for h in gate_side_violations(str(p.relative_to(REPO_ROOT)),
                                                           p.read_text(encoding="utf-8"))]
    assert hits == []


def test_relay_imports_only_stdlib_and_itself_and_calls_no_gate_verb():
    files = _py_files(RELAY_DIR)
    assert {p.name for p in files} >= {"__init__.py", "probe.py", "config.py", "wecom.py", "clawbot.py"}
    hits = [h for p in files for h in relay_side_violations(str(p.relative_to(REPO_ROOT)), p.read_text(encoding="utf-8"))]
    assert hits == []


def test_scanners_catch_injected_violations():
    assert gate_side_violations("tools/x.py", "from relay.probe import main\n")
    assert gate_side_violations("tools/x.py", "import relay\n")
    assert gate_side_violations("web/x.py", "m = importlib.import_module('relay.channels')\n")
    assert not gate_side_violations("web/x.py", "relay_lines = 1  # owner_inbox.relay_lines\n")
    assert relay_side_violations("relay/x.py", "from tools.aipos_cli import workspace_config\n")
    assert relay_side_violations("relay/x.py", "import subprocess\n")
    assert relay_side_violations("relay/x.py", "import requests\n")
    assert relay_side_violations("relay/x.py", "VERB = 'lybra_queue_claim'\n")
    assert not relay_side_violations("relay/x.py", "import json\nfrom relay import net\n")


def test_no_runtime_dependency_added():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^dependencies = \[\]", text, re.MULTILINE)
    assert "relay" not in re.findall(r"^(\w+)\s*=\s*\[", text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0],
                                     re.MULTILINE)
