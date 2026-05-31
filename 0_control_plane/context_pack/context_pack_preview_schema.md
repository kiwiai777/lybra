# Context Pack Preview Schema

## Envelope

Context Pack preview responses use the local adapter response envelope when served through the Board adapter. The underlying builder result uses this shape:

```yaml
action: context_pack_preview
verdict: PASS | WARN | NEEDS_OWNER | BLOCK
scope: task | orchestration
source_type: queue_task | orchestration
pack_id: ctxpack_<source>
dry_run: true
writes_enabled: false
execute_allowed: false
controlled_mutation_enabled: false
external_rag_enabled: false
agent_execution_enabled: false
git_automation_enabled: false
dry_run_token: null
planned_writes: []
planned_moves: []
task: {}
context_bundle: {}
orchestration: {}
source_refs: []
governance: {}
disabled_capabilities: []
warnings: []
blocking_reasons: []
needs_owner_reasons: []
```

## `task`

Required when `scope: task`:

- `task_id`
- `title`
- `path`
- `queue_state`
- `status`
- `project`
- `assigned_to`
- `agent_instance`
- `task_mode`
- raw `task_class`
- `effective_task_class`
- `task_class_explicit`
- optional `complexity_note`
- `model_tier`
- `context_bundle_ref`
- `artifact_scope`
- `memory_scope`
- `output_target`
- `body_excerpt`

## `context_bundle`

Expected fields:

- `ref`
- `found`
- `path`
- `role_instance`
- `agent_instance`
- `environment`
- `description`
- `allowed_task_modes`
- `preferred_model_tiers`
- `allowed_model_tiers`
- `memory_access`
- `output_target`
- `escalation_rules`
- `constraints`

Missing bundle files are warnings in AIPOS-78, not writes or auto-repair triggers.

## `orchestration`

Expected fields:

- `orchestration_id`
- `summary_available`
- `summary`
- `owner_attention`
- `source_refs`
- `conflicts`

The builder may reconstruct orchestration summary from append-only logs and queue task metadata. It must not write summary state.

## Governance

The `governance` object must preserve:

- Owner decision gates
- independent audit requirement
- no self-audit
- no hidden queue mutation
- no autonomous push
- no external RAG call in AIPOS-78

## Non-Goals

AIPOS-78 does not define a persistent context pack file format, context pack writer, external retrieval schema, Cortex bridge, or runtime handoff protocol.
