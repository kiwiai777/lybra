#!/usr/bin/env python3
"""AIPOS-C1 大项D: Generate ADVISOR-COMMANDS verb reference from verbs.schema.json.

This tool generates the verb reference section of the advisor manual from
the single source of truth (verbs.schema.json). The generated output has a
"machine-generated, do not edit" header.

Usage:
    python3 tools/generate_advisor_commands.py [--output <path>]

If --output is not specified, prints to stdout.
The generated content replaces the verb reference section in ADVISOR-COMMANDS.md.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "verbs.schema.json"


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def generate_verb_entry(verb_name: str, verb_def: dict) -> str:
    """Generate a single verb reference entry."""
    lines = []
    description = verb_def.get("description", "(no description)")
    phase = verb_def.get("phase", "unknown")
    surface = verb_def.get("surface", [])
    cli_command = verb_def.get("cli_command", "")
    stage = verb_def.get("stage_contract", {})
    
    lines.append(f"### `{verb_name}`")
    lines.append(f"")
    lines.append(f"**描述**: {description}")
    lines.append(f"")
    lines.append(f"**阶段**: {phase}")
    lines.append(f"**表面**: {', '.join(surface) if surface else 'N/A'}")
    
    if cli_command:
        lines.append(f"**CLI**: `{cli_command}`")
    
    # Stage contract
    if stage:
        two_stage = stage.get("two_stage", False)
        if two_stage:
            confirm_via = verb_def.get("confirm_via", "unknown")
            dry_run_emits = stage.get("dry_run_emits_token", False)
            self_confirm = stage.get("self_confirm_allowed", [])
            lines.append(f"")
            lines.append(f"**两阶段协议**:")
            lines.append(f"- confirm_via: `{confirm_via}`")
            lines.append(f"- dry_run 发 token: {'是' if dry_run_emits else '否'}")
            if self_confirm:
                lines.append(f"- 自 confirm 允许: {', '.join(self_confirm)}")
                mechanism = stage.get("self_confirm_mechanism", "")
                if mechanism:
                    lines.append(f"- 自 confirm 机制: {mechanism}")
            else:
                lines.append(f"- 自 confirm 允许: 否(需 Owner 亲自确认)")
    
    # Preconditions
    preconditions = verb_def.get("preconditions", [])
    if preconditions:
        lines.append(f"")
        lines.append(f"**前置条件**:")
        for precond in preconditions:
            lines.append(f"- `{precond.get('id', '?')}`: {precond.get('description', '')}")
            on_violation = precond.get("on_violation", "")
            if on_violation == "reject_with_redirect":
                redirect = precond.get("redirect_verb", "")
                hint = precond.get("redirect_hint", "")
                lines.append(f"  - 违反时: 拒绝并指路 → `{redirect}`")
                if hint:
                    lines.append(f"  - 提示: {hint}")
            elif on_violation == "idempotent_supplement":
                lines.append(f"  - 违反时: 幂等补录(不阻塞)")
    
    # Parameters
    params = verb_def.get("parameters", {})
    properties = params.get("properties", {})
    required = params.get("required", [])
    
    if properties:
        lines.append(f"")
        lines.append(f"**参数**:")
        for param_name, param_def in properties.items():
            req_marker = "**必填**" if param_name in required else "可选"
            param_type = param_def.get("type", "string")
            param_desc = param_def.get("description", "")
            lines.append(f"- `{param_name}` ({param_type}, {req_marker}): {param_desc}")
    
    # Response contract
    client_hint = stage.get("client_hint_template", "")
    if client_hint:
        lines.append(f"")
        lines.append(f"**应答提示**(从 schema 生成):")
        lines.append(f"> {client_hint}")
    
    lines.append(f"")
    return "\n".join(lines)


def generate_full_reference(schema: dict) -> str:
    """Generate the complete verb reference section."""
    lines = []
    
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    schema_version = schema.get("schema_version", "unknown")
    
    lines.append("<!-- MACHINE-GENERATED FROM verbs.schema.json — DO NOT EDIT BY HAND -->")
    lines.append(f"<!-- Generated at: {now} -->")
    lines.append(f"<!-- Schema version: {schema_version} -->")
    lines.append(f"<!-- Regenerate: python3 tools/generate_advisor_commands.py -->")
    lines.append("")
    lines.append("## 动词参考(由 verbs.schema.json 生成)")
    lines.append("")
    lines.append(f"> ⚠️ **本节由 `verbs.schema.json` (v{schema_version}) 自动生成,勿手改**。")
    lines.append(f"> 重跑: `python3 tools/generate_advisor_commands.py`")
    lines.append(f"> 唯一真相: `schema/verbs.schema.json`")
    lines.append("")
    
    # Group verbs by base_verb or category
    verbs = schema.get("verbs", {})
    
    # Read-only verbs
    lines.append("### 只读动词")
    lines.append("")
    for name, defn in verbs.items():
        if defn.get("phase") == "single" and not defn.get("stage_contract", {}).get("two_stage", False):
            if "queue_list" in name or "preview" in name or "return_content" in name or "progress" in name:
                lines.append(generate_verb_entry(name, defn))
    
    # Mutation verbs — claim
    lines.append("### 认领(claim)")
    lines.append("")
    for name, defn in verbs.items():
        if "claim" in name:
            lines.append(generate_verb_entry(name, defn))
    
    # Mutation verbs — return
    lines.append("### 交回(return)")
    lines.append("")
    for name, defn in verbs.items():
        if "return" in name and "return_content" not in name and "return_repair" not in name:
            lines.append(generate_verb_entry(name, defn))
    
    # Mutation verbs — close
    lines.append("### 结案(close)")
    lines.append("")
    for name, defn in verbs.items():
        if "close" in name:
            lines.append(generate_verb_entry(name, defn))
    
    # Audit verbs
    lines.append("### 审计(audit)")
    lines.append("")
    for name, defn in verbs.items():
        if "audit" in name or "mark_concluded" in name:
            lines.append(generate_verb_entry(name, defn))
    
    # Bench verbs
    lines.append("### 基准审计(bench)")
    lines.append("")
    for name, defn in verbs.items():
        if "bench" in name:
            lines.append(generate_verb_entry(name, defn))
    
    # Response field derivation rules
    lines.append("### 应答字段派生规则")
    lines.append("")
    derivation = schema.get("response_field_derivation", {})
    rules = derivation.get("rules", {})
    if rules:
        for field_name, rule_desc in rules.items():
            lines.append(f"- **`{field_name}`**: {rule_desc}")
    lines.append("")
    
    return "\n".join(lines)


def main() -> int:
    output_path = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = Path(sys.argv[idx + 1])
    
    try:
        schema = load_schema()
    except Exception as e:
        print(f"Error loading schema: {e}", file=sys.stderr)
        return 1
    
    generated = generate_full_reference(schema)
    
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(generated, encoding="utf-8")
        print(f"Generated verb reference written to: {output_path}")
    else:
        print(generated)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
