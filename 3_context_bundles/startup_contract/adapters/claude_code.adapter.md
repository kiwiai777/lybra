---
title: Claude Code Adapter
type: agent_startup_contract_adapter
adapter_id: claude_code
adapter_family: native_file
render_target: claude_code
output_path: CLAUDE.md
output_format: markdown
compatible_schema_versions: ">=0.1.0 <0.2.0"
source_protocol: AIPOS-99
status: active
supersession: explicit
translate_only: true
---

# Claude Code Adapter

This adapter describes how the vendor-neutral master spec
(`../project_runtime_contract.spec.md`) is rendered for the Claude Code runtime.
Concrete target names appear here only as **translation-target metadata**. This
adapter **translates; it does not legislate** — it adds no behavioral rule absent
from the master spec, the role contract, the context pack, the project anchors,
or explicit Owner/user instruction.

## Render Target

- Target id: `claude_code` (NativeFileAdapter family)
- Output file: `CLAUDE.md` at the project root
- Output format: Markdown
- Compatible master `schema_version`: `>=0.1.0 <0.2.0`

## Translation Rules

- Render the master L0–L6 sections in order, preserving their semantics and
  section headings. Section titles may be lightly reworded for Markdown, but the
  L0–L6 meaning must remain stable and recognizable.
- Drop any layer whose `INCLUDE_*` flag is `false`. In particular, omit L2 when
  `INCLUDE_GOVERNANCE_LAYER = false`; never drop L0, L1, L3, L4, L5, or L6.
- Replace every `{{ ... }}` placeholder from the resolved contract. An unresolved
  placeholder is a hard error — do not emit `CLAUDE.md` with leftover `{{ }}`.
- When no role template is selected, render the `generic_worker` fallback from L3.
- Keep the L5 final-response block verbatim in intent: Claude Code must end
  non-trivial work with `Changed / Verified / Not verified / Risks / Follow-ups`.

## Target-Specific Delta (Claude Code only)

This delta describes Claude Code capabilities/limitations; it does not add new
governance rules.

- Claude Code reads `CLAUDE.md` from the project root as standing instructions, so
  the rendered artifact is that file (no separate `agent init` step).
- Claude Code may have skills/plugins and tool permissions. The adapter may note
  which tools are expected for the selected role (e.g. read tools for a reviewer
  role), but tool permission policy itself comes from the role contract (L3) and
  governance layer (L2), not from this adapter.
- Communication tone follows L6, including any Owner/user language preference.
- Claude Code does not finalize, commit, push, or self-audit unless the task and
  governance gates explicitly authorize it (this restates L1.5 / L2; the adapter
  introduces nothing new).

## Out of Scope

- No rendering code, `render_target` implementation, or `context_pack` change.
- No generated-artifact write to a project root (that path needs a separate
  Owner Decision Gate per AIPOS-99).
- No other target adapters (codex / cursor / gemini / internal runtime) — each is
  a separate later slice.

## Update Discipline

This adapter is versioned and superseded explicitly, aligned to the master
`schema_version`. Editing this file is a governed change that goes through the
controlled persistence gate (`draft_create → OWNER_CONFIRMED → draft_publish`).
