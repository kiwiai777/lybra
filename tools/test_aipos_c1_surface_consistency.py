#!/usr/bin/env python3
"""AIPOS-C1: Surface consistency assertion (AIPOS-F214 件⑥: known-failure → 基线棘轮).

Validates that verbs.schema.json is the single source of truth:
- Every verb in schema has a corresponding CLI entry (where surface includes "cli")
- Every verb in schema has a corresponding MCP handler (where surface includes "mcp")
- Declared preconditions / response field derivation rules are present

AIPOS-F214 件⑥(F208 §5 E46): 原实现按源码文本 grep `add_parser("<名>"` 判 CLI 入口(多行 add_parser 写法、顶层命令都误报), MCP 只查
名字字面出现; 自 F109 起整文件 known-failure = 无守卫。现:
- CLI 入口 = 真实 argparse 树(aipos_cli.build_parser)按 cli_command 逐词走子命令;
- MCP 入口 = 门真实注册表 tools.mcp_server.tools.TOOL_HANDLERS;
- 现存不一致入基线 tests/f214_surface_consistency_baseline.jsonl(一行一条带理由, 只减不增): 新增不一致 = 红, 基线条目已修 = 红(删行)。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))
SCHEMA_PATH = REPO_ROOT / "schema" / "verbs.schema.json"
BASELINE_PATH = REPO_ROOT / "tests" / "f214_surface_consistency_baseline.jsonl"


def load_verbs_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def cli_subparser(parser: argparse.ArgumentParser, words: list[str]) -> argparse.ArgumentParser | None:
    """按命令词逐级走 argparse 子命令树; 走不通 = None。"""
    for word in words:
        sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        if sub is None or word not in sub.choices:
            return None
        parser = sub.choices[word]
    return parser


def surface_gaps(schema: dict, parser: argparse.ArgumentParser, handlers: dict) -> list[dict[str, str]]:
    """verbs.schema 声明的面与真实 CLI / MCP 注册的差: [{check, verb, detail}]。"""
    gaps: list[dict[str, str]] = []
    for verb_name, verb_def in (schema.get("verbs") or {}).items():
        surfaces = verb_def.get("surface") or []
        if "cli" in surfaces:
            cli_command = str(verb_def.get("cli_command") or "")
            words = cli_command.split()
            if not words or words[0] != "lybra" or cli_subparser(parser, words[1:]) is None:
                gaps.append({"check": "cli_entry", "verb": verb_name, "detail": cli_command or "(未声明 cli_command)"})
        if "mcp" in surfaces and verb_name not in handlers:
            gaps.append({"check": "mcp_handler", "verb": verb_name, "detail": "门 TOOL_HANDLERS 未注册"})
    return gaps


def _key(entry: dict) -> tuple[str, str, str]:
    return (str(entry["check"]), str(entry["verb"]), str(entry["detail"]))


def _baseline() -> list[dict]:
    from ratchet_baseline import read_entries

    return read_entries(BASELINE_PATH)


def _ratchet(gaps: list[dict], baseline: list[dict]) -> tuple[list, list]:
    current, allowed = Counter(_key(g) for g in gaps), Counter(_key(e) for e in baseline)
    return sorted((current - allowed).elements()), sorted((allowed - current).elements())


def _live_gaps(schema: dict | None = None) -> list[dict[str, str]]:
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.mcp_server.tools import TOOL_HANDLERS

    return surface_gaps(schema or load_verbs_schema(), build_parser(), TOOL_HANDLERS)


def test_surface_baseline_well_formed():
    for entry in _baseline():
        assert set(entry) == {"check", "verb", "detail", "reason"} and str(entry["reason"]).strip(), entry


def test_cli_and_mcp_surface_only_shrinks():
    gaps = _live_gaps()
    baseline = _baseline()
    new, stale = _ratchet(gaps, baseline)
    print(f"[C1 面一致性棘轮] 声明面与真实 CLI / MCP 不一致 {len(gaps)} 处; 基线 {len(baseline)} 条")
    for gap in gaps:
        print(f"  [{gap['check']}] {gap['verb']}: {gap['detail']}")
    assert not new, f"verbs.schema 声明的 CLI / MCP 面与真实注册不一致(新增): {new}"
    assert not stale, f"基线条目已不存在(已修)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {stale}"


def test_surface_ratchet_red_on_tampered_declaration():
    """故意改一侧即红: 把一个已注册动词的 cli_command 改成不存在的子命令、给一个只读动词加 mcp 面 → 两条新增。"""
    schema = json.loads(json.dumps(load_verbs_schema()))
    schema["verbs"]["lybra_kickoff"]["cli_command"] = "lybra kickoff-nope"
    schema["verbs"]["lybra_kickoff"]["surface"] = ["cli", "mcp"]
    new, _stale = _ratchet(_live_gaps(schema), _baseline())
    print(f"[C1 反例] 篡改 lybra_kickoff 后新增: {new}")
    assert ("cli_entry", "lybra_kickoff", "lybra kickoff-nope") in new and ("mcp_handler", "lybra_kickoff", "门 TOOL_HANDLERS 未注册") in new


def test_preconditions_and_response_derivation_declared():
    schema = load_verbs_schema()
    verbs = schema.get("verbs") or {}
    assert any(p.get("id") == "no_formal_verdict" for p in verbs.get("lybra_mark_concluded", {}).get("preconditions", []))
    assert any(p.get("id") == "audit_idempotent" for p in verbs.get("lybra_audit_dispatch_dry_run", {}).get("preconditions", []))
    rules = (schema.get("response_field_derivation") or {}).get("rules") or {}
    assert "owner_confirmation_required" in rules and "client_hint" in rules
    assert str(schema.get("schema_version", "")).startswith("2.1")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
