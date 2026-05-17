# Context Pack Builder Policy

## Purpose

A Context Pack is a read-only briefing artifact for AIPOS workbench and agent handoff flows.

It gathers the minimum useful project, task, orchestration, context bundle, and governance references needed for an Owner, planner, coder, reviewer, or auditor to understand the next action without copying large task cards between chat surfaces.

## Boundary

AIPOS-78 introduces Context Pack protocol and a read-only builder preview.

The builder may read:

- task cards under `5_tasks/queue/`
- context bundle declarations under `3_context_bundles/`
- record summaries under `5_tasks/records/`
- append-only orchestration events and planner iterations under `5_tasks/orchestration/`
- AIPOS project status, roadmap, and decision log references when declared as source refs

The builder must not write:

- task cards
- drafts
- queue files
- records
- orchestration logs
- summary state
- memory files
- stage archives
- git state

## Relation To Other Systems

AIPOS remains the file-driven control plane for multi-agent project workflows.

Context Pack is not:

- a RAG backend
- a general knowledge base
- a Cortex replacement
- an Obsidian replacement
- a Dify replacement
- an external search connector
- an agent runtime launcher

Cortex remains the User Layer for user preferences, long-term constraints, personal model, and cross-agent injection. External RAG or search providers remain replaceable future infrastructure.

## Required Preview Fields

A Context Pack preview should include:

- `pack_id`
- `scope`
- `source_type`
- `task`
- `context_bundle`
- `orchestration`
- `source_refs`
- `governance`
- `disabled_capabilities`
- `warnings`
- `blocking_reasons`

## Required Safety Flags

A Context Pack preview must return:

- `dry_run: true`
- `writes_enabled: false`
- `execute_allowed: false`
- `controlled_mutation_enabled: false`
- `external_rag_enabled: false`
- `agent_execution_enabled: false`
- `git_automation_enabled: false`
- `dry_run_token: null`
- `planned_writes: []`
- `planned_moves: []`

## Owner Gates

The builder must preserve visible Owner decision gates. It may surface needs-owner reasons and blocking context, but it must not approve, resolve, publish, claim, append, commit, push, or finalize any action.

## Future Work

AIPOS-79 may expose the read-only preview in the Board UI.

Any future Context Pack writer, external retrieval connector, Cortex injection bridge, or agent execution handoff requires a separate Owner-approved task and independent audit.
