# Owner Decision Record Protocol

## Purpose

AIPOS-111 defines the protocol boundary for `owner_decision_record`.

An Owner decision record is an auditable artifact that captures an explicit Owner decision after an Owner Decision Gate has been raised. It records what was decided, which scope the decision applies to, which evidence supports the decision, and which future action may proceed.

This protocol exists before any writer, MCP tool, Board action, CLI command, or HTTP route is added. It prevents future surfaces from inventing incompatible ways to mark Owner gates as resolved.

## Status

AIPOS-111 is protocol-only.

It does not implement parsing, validation, writers, MCP tools, CLI commands, Board UI, backend routes, controlled execute allowlist changes, records writes, orchestration appends, SessionStore writes, token/auth infrastructure, runtime behavior, queue mutation, draft publish, or external client integration.

## Core Rule

`owner_decision_record` records an Owner decision. It does not make the decision.

The record may be used by future planners, dashboards, summaries, MCP tools, or auditors to determine that a specific Owner gate has visible evidence. It must not be treated as a broad permission grant.

An Owner decision record must not:

- grant Owner authority to an agent
- grant audit authority
- grant write authority beyond the specific future controlled operation
- resolve unrelated Owner gates
- publish drafts by itself
- move queue items by itself
- launch runtimes
- change model authority
- change credential boundaries
- change audit boundaries
- override controlled execute
- bypass dry-run, token, actor, scope, or snapshot revalidation
- convert a blocked operation into a warning

If a decision record is missing, malformed, contradictory, stale, outside scope, or unsupported by evidence, future operations must block or request Owner review.

## Relationship To Owner Approval Evidence

AIPOS-110 defines `owner_approval_evidence`.

`owner_approval_evidence` is the evidence envelope. `owner_decision_record` is the governance record that may cite that evidence.

Recommended relationship:

```yaml
owner_decision_record:
  owner_approval_evidence:
    evidence_id:
    evidence_hash:
    refs: []
```

The evidence envelope should explain why the decision claim is auditable. The decision record should explain what Lybra is allowed to consider decided.

Evidence alone is not a decision record. A decision record without evidence is incomplete for external or high-risk decisions.

## Record Shape

Future writer implementations should produce records with this vendor-neutral shape.

```yaml
owner_decision_record:
  decision_id:
  decision_type:
  decision_status:
  decided_at:
  decided_by_ref:
  captured_by:
  capture_surface:
  decision_summary:
  decision_rationale:
  applies_to:
    project:
    task_id:
    draft_path:
    orchestration_id:
    iteration_id:
    event_id:
    external_ref:
  approval_scope:
    operation:
    authority_boundary:
    allowed_next_action:
    expires_at:
  owner_approval_evidence:
    evidence_id:
    source_tag:
    client_tag:
    external_ref:
    approval_actor_ref:
    approval_timestamp:
    approval_intent:
    evidence_hash:
    evidence_ref:
    captured_by:
    capture_method:
    redaction_status:
    refs: []
  refs: []
```

### Required Fields

`decision_id`
: Stable id for this decision record. Future writers must reject duplicate ids within the target persistence scope.

`decision_type`
: Normalized type of decision, such as `approve_intake`, `approve_scope`, `approve_risk`, `approve_architecture`, `approve_audit_boundary`, `approve_model_authority`, `approve_credential_boundary`, `approve_publish`, or `reject_request`.

`decision_status`
: Normalized status. Suggested values are `approved`, `rejected`, `needs_revision`, `superseded`, and `expired`.

`decided_at`
: Timestamp for when the Owner decision was made or captured.

`decided_by_ref`
: Non-secret Owner-side actor reference. This is audit metadata only and does not prove identity by itself.

`captured_by`
: Actor or system that captured the decision record.

`capture_surface`
: Generic surface such as `board`, `cli`, `mcp`, or `external_client`. It must not encode vendor-specific product names.

`decision_summary`
: Short normalized statement of the decision.

`applies_to`
: Structured scope for the decision. It must be specific enough to prevent broad reuse.

`approval_scope`
: The operation and authority boundary this decision applies to. It must not imply additional operations.

`owner_approval_evidence`
: Evidence envelope aligned with AIPOS-110. Required for external-client, MCP-submitted, high-risk, or non-Board-native decision records.

`refs`
: Non-secret references to related dry-runs, drafts, tasks, orchestration events, planner iterations, reports, or external refs.

## Decision Types

Initial protocol-level decision types:

```text
approve_intake
approve_owner_decision_record
approve_draft_publish
approve_scope
approve_risk
approve_architecture
approve_workflow
approve_audit_boundary
approve_model_authority
approve_credential_boundary
approve_runtime_boundary
approve_external_service
reject_request
request_revision
supersede_decision
```

This list is descriptive for future validators. AIPOS-111 does not implement validation. AIPOS-112 implements the first narrow validator/writer slice for this record shape.

## Persistence Boundary

AIPOS-112 implements the first separately approved writer task for Owner decision records.

The AIPOS-112 MVP persistence target is:

- record artifact under `5_tasks/records/owner_decisions/<decision_id>.md`

Other possible future persistence targets remain separate tasks:

- append-only orchestration event with `event_type: owner_decision_recorded`
- draft-only review artifact under `5_tasks/drafts/owner_decisions/`
- project governance note under `2_projects/<project>/` only after a separate governance-write task

Persistence rules:

- records must be append-only or explicitly superseded by a new record
- correction must use a new correction/supersession record, not silent mutation
- raw chat messages or platform-native payloads are forbidden by default
- evidence refs must be non-secret
- records must preserve scope and source references
- derived indexes must not become source of truth
- SessionStore must not store decision records unless a future memory-layer protocol permits it

## Controlled Execute Integration

AIPOS-112 implements the first `owner_decision_record` writer and preserves AIPOS-77 controlled execute discipline:

- dry-run before write
- planned writes listed explicitly
- dry-run token returned
- snapshot hash or equivalent input hash returned
- confirm references the dry-run token
- execute-time revalidation runs before write
- actor and scope checks are enforced
- Owner approval evidence is checked when required
- blocking reasons remain blocking
- write target is constrained to the approved persistence path

The writer must not combine decision recording with draft publish, queue mutation, orchestration event append, SessionStore write, runtime launch, git automation, or other side effects.

## MCP Integration

AIPOS-109 defines the MCP-native discipline baseline.

Future MCP wrapping for `owner_decision_record` must use a tool pair:

```text
lybra_owner_decision_record_dry_run
lybra_owner_decision_record_confirm
```

MCP requirements:

- self-documenting tool descriptions
- `owner_decision_record` scope-gated visibility
- required dry-run then confirm sequence
- required `dry_run_token` on confirm
- structured teaching errors
- no raw stack traces
- no hidden record writes
- no publish side effects
- no queue mutation side effects

Suggested teaching errors:

```text
MISSING_OWNER_APPROVAL_EVIDENCE
INVALID_OWNER_DECISION_RECORD
DECISION_SCOPE_MISMATCH
DECISION_ALREADY_RECORDED
DECISION_EVIDENCE_MISMATCH
DRY_RUN_TOKEN_REQUIRED
SNAPSHOT_MISMATCH
SCOPE_DENIED
```

AIPOS-111 does not add these tools.

## Relationship To Existing Protocols

### AIPOS-77 Controlled Persistence Gate

Owner decision recording is a write and must use controlled execute when implemented. It may not bypass dry-run, token, confirm, actor, scope, or revalidation checks.

### AIPOS-96 MCP Server Boundary

MCP may expose future Owner decision recording only as controlled execute translation. It must not confirm decisions on behalf of the Owner or expose tools without explicit scope.

### AIPOS-106 External Intake Registry

External clients may request narrowly scoped Owner decision recording only after a future implementation task. IM-specific concepts remain outside Lybra; external clients submit normalized evidence only.

### AIPOS-109 MCP Intake Submit Write Tools

AIPOS-109 is the pattern for future MCP write tools. Owner decision record tools must preserve the same self-documenting, sequence-enforced, scope-gated, teaching-error style.

### AIPOS-110 Owner Approval Evidence

AIPOS-110 provides the evidence envelope used by this protocol. Owner decision records cite evidence; evidence alone does not resolve a gate.

## Future Implementation Notes

Recommended sequence after AIPOS-112:

1. Add MCP dry-run/confirm wrappers using AIPOS-109 discipline.
2. Decide separately whether Board should display or create decision records.
3. Decide separately whether orchestration timeline events should cite records.

Each step requires independent audit.

## Non-Goals

AIPOS-111 by itself does not:

- add MCP tools
- add HTTP/SSE transport
- add Board UI
- append orchestration events
- write task cards or drafts
- publish drafts
- mutate queue state
- launch runtime behavior
- add token minting, signing, revocation, auth/RBAC, or credential storage
- store raw chat messages
- store platform-native payloads
- create project directories
- alter SessionStore
- alter MCP read tools
- modify lybra-im or any external client repository
