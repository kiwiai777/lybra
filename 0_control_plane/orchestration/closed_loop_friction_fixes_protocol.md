# Closed-Loop Friction Fixes Protocol

## Status

AIPOS-144 defines protocol requirements for two frictions exposed by the AIPOS-143 complete multi-agent closed-loop dogfood.

AIPOS-144R independent audit returned `PASS`.

This document is protocol-only. It does not implement validators, queue mutation changes, draft publishing changes, Board behavior, MCP behavior, runtime behavior, autonomous orchestration, agent launch, heartbeat behavior, credential changes, deployment changes, or workspace mutations.

## Purpose

AIPOS-143 proved the complex-class closed loop can be completed with distinct executor and auditor instances, but it also exposed two protocol and implementation gaps:

1. `claim_policy: specific_instance_only` is not mechanically strict when sibling runtime instances share one logical-agent alias set.
2. Complex dependency metadata cannot separately express executor completion, audit readiness, and audit PASS.

AIPOS-144 makes both semantics explicit before any implementation slice changes claim or dependency behavior.

## Owner-Approved Direction

Owner approved AIPOS-144 as the immediate protocol-only follow-up to AIPOS-143.

The implementation direction is:

- enforce `specific_instance_only` by explicit concrete instance identity comparison
- block wrong-instance claims during dry-run and confirm
- do not rely on semantic parsing of instance identifier strings
- keep the future Instance Identity Generalization out of this task
- split dependency semantics into executor completion, audit readiness, and audit PASS states
- require regression coverage for wrong-instance claim blocking and the dependency-state split in the later implementation task

## Strict Specific-Instance Claim Semantics

When a task declares:

```yaml
claim_policy: specific_instance_only
agent_instance: <required_instance>
```

only the concrete instance whose explicit instance identity equals `<required_instance>` may claim the task.

The same rule applies when the required instance is supplied by:

```yaml
requirements:
  preferred_agent_instance: <required_instance>
```

or by an explicit Owner override:

```yaml
owner_override:
  allowed_agent_instance: <required_instance>
```

The required instance source precedence is:

1. `owner_override.allowed_agent_instance`, when present
2. task top-level `agent_instance`
3. `requirements.preferred_agent_instance`

If `claim_policy: specific_instance_only` is set and no required concrete instance is present, claim must BLOCK as ambiguous metadata.

## Explicit Identity Comparison Rule

The claim check must compare explicit instance identities as opaque keys:

```text
claimant_instance_id == required_instance_id
```

This check must not use:

- logical-agent alias expansion
- canonical logical-agent collapse
- role-family matching
- runtime-profile sibling matching
- string-prefix or string-segment parsing
- vendor, harness, host, or model-name inference
- same-family continuity preference

Alias matching may still be used by other claim policies, such as `assigned_agent_only`, but it must not satisfy the mandatory instance check for `specific_instance_only`.

This rule intentionally prepares for the later Instance Identity Generalization protocol, where instance IDs become Owner-defined opaque labels. Any matching logic that depends on parsing names such as `dev.claude.cc.local` would be incompatible with that follow-on.

## Claimant Identity Resolution

The claim path must know the claimant's concrete instance identity before evaluating `specific_instance_only`.

For the implementation slice, acceptable explicit identity inputs are:

- the claim actor string when it exactly names a registered or task-visible concrete instance
- a future explicit `actor_instance_id` parameter, if approved
- an Owner override that explicitly binds the current actor to a concrete instance for this claim attempt

The implementation must not silently choose a default instance under the claimant's logical agent to satisfy `specific_instance_only`.

If the system cannot determine a concrete claimant instance, the claim must BLOCK rather than fall back to aliases.

## Dry-Run And Confirm Requirements

The same strict instance rule applies to:

- queue claim dry-run
- controlled execute token creation
- controlled execute confirm
- direct queue mutation claim paths, when available
- Board or MCP wrappers that delegate to the same claim path

A dry-run for the wrong concrete instance must return BLOCK and must not return an executable token.

If confirm receives a token created for one concrete instance but is executed by another actor or instance, confirm must fail through the existing actor or revalidation discipline, plus the strict instance policy where applicable.

Dry-run evidence should include a clear blocking reason such as:

```text
specific_instance_only requires <required_instance>; current instance is <claimant_instance>
```

## Claim Record Expectations

Claim records should preserve the concrete instance that satisfied the strict check.

Future implementation may add or populate:

```yaml
claimant_instance_id:
required_instance_id:
instance_match_policy: exact
instance_match_result: exact | mismatch | missing_claimant | missing_required
```

These fields are evidence fields. They do not grant authority and do not replace the task card as source of truth.

Historical claim records without these fields remain valid but may not be sufficient evidence for strict instance enforcement.

## Dependency State Split

Complex-class dependent work must distinguish three dependency states:

```text
executor_completion
audit_readiness
audit_pass
```

These states answer different questions.

### `executor_completion`

`executor_completion` means the upstream executor task reached completed state and produced its declared artifact or completion report.

It does not mean the work is accepted.

Typical allowed downstream use:

- prepare an audit task
- prepare a review task
- prepare a repair-planning draft
- update planner visibility that execution is ready for review

### `audit_readiness`

`audit_readiness` means the upstream executor completion has enough evidence for independent audit to start.

Readiness may be based on completed queue state, artifact links, self-validation output, report links, or explicit planner handoff metadata.

It does not mean audit PASS.

Typical allowed downstream use:

- publish or claim an audit subtask
- start independent review of the completed executor output

### `audit_pass`

`audit_pass` means an independent auditor or reviewer has returned PASS for the upstream work under the required audit policy.

Only `audit_pass` may unblock accepted-work follow-ons, finalize recommendations, or dependent execution that assumes upstream output is accepted.

`REQUEST_CHANGES` remains a non-pass audit result. It may justify a repair task, but it must not unblock accepted-work follow-ons or finalize.

## Dependency Metadata Shape

AIPOS-144 keeps the existing flat task-card style and extends it conservatively.

Existing fields remain valid:

```yaml
depends_on:
  - AIPOS-143-PRIMARY-01
dependency_condition: audit_pass
dependency_audit_status: PASS
```

New or clarified optional fields:

```yaml
dependency_condition: executor_completion | audit_readiness | audit_pass
dependency_executor_status: pending | completed | blocked | unknown
dependency_audit_readiness: not_ready | ready | blocked | unknown
dependency_audit_status: pending | REQUEST_CHANGES | PASS | WAIVED | unknown
dependency_evidence_refs:
  - 5_tasks/records/sessions/<task>/<session>.md
  - workspace_artifacts/<artifact>.md
```

`dependency_condition` states which gate the dependent task requires.

The status field corresponding to the selected condition must satisfy that condition:

- `executor_completion` requires `dependency_executor_status: completed`
- `audit_readiness` requires `dependency_audit_readiness: ready`
- `audit_pass` requires `dependency_audit_status: PASS`

`WAIVED` is not equivalent to `PASS` unless a separate explicit Owner Decision Record or task policy permits the waiver for that specific dependency.

## Complex-Class Dependency Rules

For `task_class: complex` tasks with `depends_on`:

- audit subtasks may depend on `audit_readiness`
- review subtasks may depend on `executor_completion` or `audit_readiness`, depending on required evidence
- repair subtasks may be created after `dependency_audit_status: REQUEST_CHANGES`
- finalize subtasks require `audit_pass`
- accepted-work follow-ons require `audit_pass`
- if the dependency condition is missing or ambiguous, the task must BLOCK or become `needs_owner`

The implementation must stop treating every complex-class dependency as if it requires immediate `audit_pass`. That old behavior blocks valid audit tasks before the audit can run.

The implementation must also avoid the opposite error: executor completion and audit readiness must not be treated as acceptance.

## Backward Compatibility

Historical task cards using:

```yaml
dependency_condition: audit_pass
dependency_audit_status: PASS
```

remain valid and continue to satisfy accepted-work dependency gates.

Historical cards with `dependency_condition: audit_pass` and a non-PASS audit status remain blocked for accepted-work follow-ons.

Historical cards that omit the new dependency state fields must not be silently treated as accepted. The safe default is:

- pass only when the old `audit_pass` shape is explicitly satisfied
- otherwise block, warn, or require Owner review depending on the publication path

No automatic migration of historical task cards is required in AIPOS-144.

## AIPOS-145 Implementation Expectations

The later implementation slice should:

- update queue claim dry-run and confirm behavior so wrong concrete instance claims BLOCK under `specific_instance_only`
- ensure actor/profile alias expansion cannot satisfy the mandatory instance check
- preserve alias-based behavior for `assigned_agent_only` and other non-specific policies where already valid
- surface clear blocking reasons in CLI, Board adapter, and controlled execute responses
- add claim evidence fields when records are written, if doing so fits the existing records writer boundary
- update complex dependency validation to support `executor_completion`, `audit_readiness`, and `audit_pass`
- preserve conservative blocking for ambiguous dependency metadata
- update planner and draft-publish validation so audit tasks can be published when audit readiness is satisfied but audit PASS is still pending
- keep accepted-work follow-ons and finalize blocked until independent audit PASS

Regression coverage must include:

- wrong sibling instance dry-run BLOCK for `specific_instance_only`
- target instance dry-run PASS for `specific_instance_only`
- wrong sibling instance confirm failure if a stale or mismatched token path is attempted
- alias-based `assigned_agent_only` behavior unchanged where policy allows it
- audit task publication allowed with `dependency_condition: audit_readiness` and `dependency_audit_readiness: ready`
- accepted-work follow-on BLOCK while `dependency_audit_status` is pending
- accepted-work follow-on PASS after `dependency_audit_status: PASS`
- ambiguous complex dependency metadata BLOCK or NEEDS_OWNER

## Affected Surface Inventory

Expected implementation and protocol alignment surfaces:

- `0_control_plane/dispatch/task_matching_policy.md`
- `0_control_plane/dispatch/task_claim_protocol.md`
- `0_control_plane/orchestration/planner_subtask_policy.md`
- `0_control_plane/orchestration/planner_subtask_draft_publish_flow.md`
- `0_control_plane/orchestration/orchestration_task_schema.md`
- `0_control_plane/orchestration/subtask_dag_fanout_join_schema.md`
- `0_control_plane/tasks/task_complexity_class_protocol.md`
- `0_control_plane/board/start_task_session_preview.md`
- `5_tasks/claim_event_schema.md`
- `tools/aipos_cli/agent_profiles.py`
- `tools/aipos_cli/validator.py`
- `tools/aipos_cli/task_complexity.py`
- `tools/aipos_cli/queue_mutation.py`
- `tools/aipos_cli/draft_writer.py`
- `tools/aipos_cli/draft_validator.py`
- `tools/aipos_cli/board_adapter.py`
- `web/board/app.py`
- CLI and Board regression tests around controlled execute, queue claim, draft publish, and complexity validation

The affected inventory is review guidance for the implementation task. AIPOS-144 itself does not edit these behavior surfaces.

## Relationship To Instance Identity Generalization

AIPOS-144 does not rename instance IDs, add opaque instance labels, migrate historical records, or change capability profile provenance fields.

It establishes one prerequisite for that later task: strict enforcement must use explicit instance identity equality and must not parse instance identifier strings.

The later Instance Identity Generalization protocol may change what valid instance IDs look like. If AIPOS-144 is implemented correctly, that later change should not reopen the AIPOS-143 sibling-instance bug.

## Owner Decision Gates

Pause for Owner decision before any implementation expands beyond this protocol, including:

- changing instance naming rules
- changing role registry semantics
- adding new claim policies
- adding runtime launch, polling, scheduler, heartbeat, or background recovery behavior
- broadening controlled execute allowlists
- adding MCP write tools
- treating audit waiver as PASS without explicit Owner decision semantics
- changing independent audit authority or allowing self-audit

## Non-Goals

AIPOS-144 does not implement:

- claim enforcement code
- dependency validator code
- task writer changes
- queue mutation behavior changes
- Board UI changes
- MCP behavior changes
- controlled execute allowlist changes
- instance identity renaming
- capability profile migration
- role registry migration
- claim record writer changes
- autonomous runtime
- agent launcher
- heartbeat expansion
- scheduler or queue polling
- credential changes
- deployment changes
- public endpoints
- automatic finalize
- self-audit
