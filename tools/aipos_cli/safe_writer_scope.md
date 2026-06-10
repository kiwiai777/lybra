# Safe Writer Scope

## Purpose

This document defines the scope for the Safe Task Draft Writer CLI MVP targeted by AIPOS-29.

This is a scope document only. It does not implement writer behavior.

## Target Commands

Planned commands:

```bash
python3 tools/aipos_cli/aipos_cli.py draft create --from-json <file>
python3 tools/aipos_cli/aipos_cli.py draft create --from-template <template>
python3 tools/aipos_cli/aipos_cli.py draft validate --path <draft_path>
python3 tools/aipos_cli/aipos_cli.py draft publish --path <draft_path>
python3 tools/aipos_cli/aipos_cli.py draft list
```

Optional output modes:

```bash
--json
--dry-run
```

Future commands but out of scope for AIPOS-29:

```bash
queue claim
queue block
queue complete
records create-session
records create-claim
orchestration write-state
```

## Allowed Write Paths

First-phase allowed write path:

```text
5_tasks/drafts/
```

Publish target:

```text
5_tasks/queue/pending/
```

Rules:

- `draft create` may only write under `5_tasks/drafts/`
- `draft publish` may only move or copy a validated draft into `5_tasks/queue/pending/`
- `draft publish` must never overwrite an existing pending task
- `draft publish` must preserve the original draft unless an explicit published-marker policy is later added
- `draft publish` must run validation before writing pending
- `draft publish` must support `--dry-run`

## Forbidden Write Paths

Forbidden in AIPOS-29:

```text
5_tasks/queue/claimed/
5_tasks/queue/completed/
5_tasks/queue/blocked/
5_tasks/records/
5_tasks/orchestration/
0_control_plane/
1_shared_memory/
2_projects/
3_context_bundles/
4_inbox/
task_cards/
.codex/
.git/
```

Additional rules:

- AIPOS-29 writer must not modify project management docs at runtime
- AIPOS-29 writer must not write shared memory
- AIPOS-29 writer must not write records
- AIPOS-29 writer must not claim, complete, or block tasks

## Draft Format

Draft format is a Markdown task card with YAML frontmatter.

Drafts use the same `task_schema` fields as normal task cards. Additional draft-only metadata is allowed:

```yaml
draft_id:
draft_status: draft
draft_created_by:
draft_created_at:
draft_updated_at:
draft_publish_target: 5_tasks/queue/pending/
draft_validation_summary:
```

Rules:

- draft metadata is optional
- draft-only fields must not be required in final pending task unless `task_schema` later allows them
- publish may strip or retain draft-only fields based on documented policy
- published task must satisfy required `task_schema` fields
- `task_id` must be present before publish
- `status` must be `pending` at publish

## Validation Boundary

Minimum validation before publish:

- required selectors present
- `task_id` valid
- `task_id` not duplicated in pending/claimed/completed/blocked
- `status == pending`
- `assigned_to` present
- `context_bundle` present
- `task_mode` present
- `priority` present
- `output_target` present
- `artifact_policy` present
- `session_policy` / `context_isolation` / `artifact_scope` / `memory_scope` warnings as existing validator policy
- no forbidden frontmatter fields that imply runtime claim or completion
- path safe
- filename safe

Rules:

- AIPOS-29 may reuse existing validator but must not weaken it
- AIPOS-29 must distinguish draft validation from queue validation
- AIPOS-29 must return machine-readable JSON for validation result

## Task ID and Filename Policy

Task ID policy:

- manual `task_id` allowed
- generated `task_id` optional
- generated IDs should be deterministic enough for dry-run display
- collision check required before publish

Suggested filename policy:

```text
{task_id_slug}.md
```

Rules:

- filename must be path-safe
- no path traversal
- no absolute path
- no overwrite
- case-insensitive collision warning is recommended

## Dry-run and Review Output

`--dry-run` requirements:

- must not write files
- should show target path
- should show rendered markdown preview
- should show validation result
- should show collision status

Review-oriented output:

- writer should support owner-reviewable output
- JSON should include `planned_writes`
- JSON should include `would_write: true|false`
- JSON should include `blocking_reasons`
- JSON should include `warnings`

## Idempotency and Overwrite Safety

Rules:

- no overwrite by default
- explicit overwrite flag should not exist in AIPOS-29
- same input plus same `task_id` should detect existing draft or pending file and stop safely
- publish twice should not create duplicate pending task
- publish after pending exists should BLOCK

## Audit and Finalize Workflow

AIPOS-29 implementation must follow:

```text
task card -> execution -> audit by dev_claude -> finalize -> push
```

Audit expectations for writer implementation:

- dry-run does not write
- create only writes drafts
- publish writes pending plus the publish provenance record
- no writes to forbidden paths
- duplicate `task_id` blocked
- path traversal blocked
- JSON valid
- `py_compile` passes
- unit tests use `tempfile`, not real queue
