# Startup Contract

This directory holds the Lybra agent **startup contract** master spec and its
per-target adapters. It implements the content layer of AIPOS-99 (Agent Startup
Contract Render Protocol); the protocol itself is defined in
`0_control_plane/agents/agent_startup_contract_render_protocol.md`.

## Model

```text
vendor-neutral master spec  +  per-target adapter  =  concrete startup artifact
```

- `project_runtime_contract.spec.md` — the vendor-neutral master spec, layered
  L0–L6. It contains no vendor or product names in its behavioral content.
- `adapters/<target>.adapter.md` — per-target translation notes. Concrete target
  names (and output filenames such as `CLAUDE.md`) appear only in adapter
  metadata. Adapters translate; they do not add behavioral rules.

Currently provided: `adapters/claude_code.adapter.md`. Other targets
(codex / cursor / gemini / internal runtime) are separate later slices.

## File Authority

- The in-repo files here are the **source of truth** for the startup contract. A
  copy of the master may exist in an external knowledge vault as a stable display;
  on any conflict the in-repo file wins and supersedes the vault copy.

## Update Discipline

- These files are versioned by `schema_version` and evolve by **explicit
  supersession**, not silent rewrite. Adapters declare a compatible
  `schema_version` range against the master.
- Editing the master or any adapter is a governed change: it goes through the
  controlled persistence gate (`draft_create → OWNER_CONFIRMED → draft_publish`).

## Scope (what is NOT here)

Per AIPOS-99, this content layer does not include a renderer, a `render_target`
implementation, `context_pack` code changes, generated-artifact writes to project
roots, workspace template changes, role-template YAML files (the role catalog
lives under `3_context_bundles/roles/`), or the agent lifecycle SOP. Each is a
separate Owner-approved, independently audited slice.
