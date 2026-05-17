# Subtask Index Schema

## Purpose

This document defines the future file-driven subtask index for planner-created orchestration work.

Suggested future path:

```text
5_tasks/orchestration/{orchestration_id}/subtask_index.md
```

The subtask index is an index, not an execution queue.

## Record Shape

Each subtask entry should expose:

```yaml
- subtask_id:
  task_id:
  task_path:
  title:
  subtask_type: coding
  assigned_to:
  agent_instance:
  reviewer:
  audit_by:
  status:
  queue_state:
  created_by_planner:
  planner_agent:
  iteration:
  subtask_sequence:
  depends_on: []
  blocks: []
  dag_id:
  dag_node_id:
  dag_node_type:
  dag_layer:
  fanout_group_id:
  join_gate_id:
  depends_on_nodes: []
  blocks_nodes: []
  join_input_for: []
  join_output_from: []
  dependency_condition:
  artifact_links: []
  report_refs: []
  needs_owner:
  last_updated_at:
```

Allowed `subtask_type` values:

```text
coding
audit
research
docs
finalize
repair
validation
```

## Rules

- `subtask_index` is an index, not an execution queue.
- Task cards remain in `5_tasks/queue/*`.
- `subtask_index` must not replace `task_schema`.
- `subtask_index` can be rebuilt by scanning task frontmatter with matching `orchestration_id`.
- AIPOS-93 DAG fields are optional and must remain compact index metadata.
- duplicate `task_id` must be Needs Owner.
- missing `task_path` must be Needs Owner.
- `status` mismatch must follow the existing directory/status mismatch policy.
- DAG node or edge conflicts must not override task cards or queue directory state.
- index rows should remain compact and file-driven for CLI/Board readers.

## Rebuildability

The index is explicitly rebuildable.

Rebuild sources:

- task frontmatter with matching `orchestration_id`
- queue directory location
- planner-created draft metadata
- optional DAG metadata from task cards
- append-only planner iterations
- append-only orchestration events
- report refs
- explicit artifact link refs

If index content and queue state disagree, task cards and queue directories win.

## AIPOS-93 DAG Metadata

AIPOS-93 adds optional DAG/fanout/join metadata for planner-created subtasks.

The subtask index may display these fields, but it must not become the source of truth for dependency execution. A future `subtask_dag.md` index, if approved, must be rebuildable from task cards, drafts, append-only logs, records, Owner decisions, audit reports, and artifact refs.

DAG metadata should be treated as blocking or `needs_owner` when:

- a node id is duplicated
- an edge references a missing node
- a cycle is detected
- a fanout group exceeds orchestration limits
- a join gate has ambiguous satisfaction criteria
- queue/task status conflicts with DAG status

AIPOS-93 does not implement a DAG scheduler, `subtask_index.md` writer, `subtask_dag.md` writer, queue mutation, draft publish automation, backend route, Web UI control, or CLI command.

## Recommended Rendering

The index may be rendered as:

- YAML list in frontmatter
- Markdown table with YAML block per row
- Markdown sections with compact metadata blocks

Whichever format is chosen in implementation should remain stable and machine-readable.

## AIPOS-67 Scope Decision

AIPOS-67 keeps `subtask_index.md` deferred.

Subtask index writing should not be the first summary-state writer because it requires a stable row serialization format and careful conflict handling against queue task cards. A future index preview or writer must be rebuildable from task frontmatter with matching `orchestration_id`, queue directory state, reports, and explicit artifact refs.

The first future summary writer, if approved, should target `orchestration_state.md` only. `subtask_index.md` can follow after the summary preview contract is stable.
