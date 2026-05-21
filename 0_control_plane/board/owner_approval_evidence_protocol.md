# Owner Approval Evidence Protocol

## Purpose

AIPOS-110 defines the protocol boundary for structured Owner approval evidence.

Owner approval evidence is metadata that makes a claimed Owner approval auditable. It is not a credential, not a permission grant, not a substitute for controlled execute, and not a replacement for explicit Owner Decision Gates.

The immediate driver is external intake and MCP write-tool work after AIPOS-108 and AIPOS-109. Future external clients may need to submit evidence that an Owner approved a narrowly scoped action in another surface, such as a chat message or redacted external approval record. Lybra needs a stable, vendor-neutral envelope before adding `owner_decision_record`, evidence-bearing confirm requests, or higher-risk write tools.

## Status

AIPOS-110 is protocol-only.

It does not implement parsing, validation, storage, MCP tools, CLI commands, Board UI, backend routes, controlled execute allowlist changes, records writes, orchestration appends, SessionStore writes, token minting, signature verification, revocation, auth/RBAC, runtime behavior, or external client integration.

## Core Rule

Owner approval evidence is evidence only.

It may support future audit and review of an approval claim, but it does not:

- grant Owner authority
- grant audit authority
- grant write authority
- grant publish authority
- grant queue authority
- grant runtime authority
- grant model-routing authority
- grant credential access
- override controlled execute
- bypass dry-run, token, confirm, actor, scope, or snapshot revalidation checks
- convert a blocked operation into a warning

If evidence is missing, malformed, stale, contradictory, or outside scope, the future operation must block or require Owner review. It must not silently continue.

## Relationship To Owner Confirmation Token

`owner_confirmation_token` and `owner_approval_evidence` are separate concepts.

`owner_confirmation_token` is an immediate controlled execute gate input. In current local MVP flows, it may be a simple token value such as `OWNER_CONFIRMED`.

`owner_approval_evidence` is a structured audit envelope. It records where the approval claim came from, what was approved, when it was captured, and which non-secret references can be inspected later.

Future execute requests may require both:

```yaml
owner_confirmation_token:
owner_approval_evidence:
```

Having evidence does not satisfy the token gate by itself. Having a token does not create durable evidence by itself.

## Evidence Envelope

Future evidence-bearing requests should use this vendor-neutral envelope shape.

```yaml
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
```

### Required Fields

`evidence_id`
: Stable evidence identifier within the submitting source boundary. It must not contain secret material or raw platform-native identity.

`source_tag`
: Functional external source tag, aligned with AIPOS-106. It must not encode vendor names, host names, personal names, phone numbers, chat ids, channel ids, group ids, account ids, domains, or secrets.

`client_tag`
: Project routing tag, aligned with AIPOS-106 and AIPOS-107. It must refer to an existing approved project mapping in future implementations; this protocol does not create projects.

`external_ref`
: Stable redacted source reference for the item being approved. It should match the intake or decision object when approval is tied to a prior external request.

`approval_actor_ref`
: Non-PII reference for the approving Owner-side actor. It is evidence metadata only and does not prove identity by itself.

`approval_timestamp`
: Timestamp of the approval event as captured by the source. Future validators should require an ISO timestamp.

`approval_intent`
: Human-readable normalized intent, such as `approve_intake_submit`, `approve_owner_decision_record`, or `approve_draft_publish`. Values are future implementation details and must remain operation-scoped.

`evidence_hash`
: Hash of the redacted approval evidence payload or source-side evidence record. It must not require Lybra to store raw platform payloads.

`captured_by`
: Non-secret actor or system reference that captured the evidence.

`capture_method`
: Generic capture method such as `external_client`, `board_manual_entry`, `cli_manual_entry`, or `mcp_client`. It must not encode vendor-specific product names.

`redaction_status`
: Whether PII/platform-native data was filtered before reaching Lybra. Future implementations should accept only redacted or non-sensitive evidence summaries.

`refs`
: List of non-secret related references, such as draft path, dry-run id, external ref, orchestration id, or record id.

### Optional Fields

`evidence_ref`
: Non-secret source-side reference or URI-like pointer. It may be omitted when `external_ref` plus `evidence_hash` is sufficient.

`approval_expires_at`
: Optional expiry for evidence freshness. If present and expired, future operations must require a new approval or Owner review.

`approval_summary`
: Optional short summary of what the Owner approved. It must be normalized and must not include raw private chat content.

`scope`
: Optional structured operation/project scope, if future token or confirm paths need to compare evidence with a capability scope.

## Confirm Request Integration

Future controlled execute confirm requests may carry approval evidence as an optional or required field.

Recommended future confirm shape:

```yaml
operation:
actor:
dry_run_id:
dry_run_snapshot_hash:
owner_confirmation_token:
owner_approval_evidence:
execute_requested_at:
```

Rules:

- evidence must never replace `dry_run_id` or `dry_run_snapshot_hash`
- evidence must never replace execute-time revalidation
- evidence must never change the operation being confirmed
- evidence must match the operation, project, source, and external reference claimed by the dry-run when those fields exist
- evidence must be non-secret and redacted before reaching Lybra
- evidence must be included in structured responses when accepted, blocked, or requiring Owner review
- evidence mismatch must block or raise Owner review; it must not be downgraded to a silent warning

## MCP Tool Integration

AIPOS-109 established the MCP-native discipline baseline:

- self-documenting tool descriptions
- sequence enforcement
- scope-gated visibility
- teaching error responses

Future evidence-bearing MCP tools must preserve that baseline.

Tool descriptions must say whether `owner_approval_evidence` is optional or required, what fields are expected, and what happens when evidence is missing or mismatched.

Teaching errors should use structured codes such as:

```text
MISSING_OWNER_APPROVAL_EVIDENCE
INVALID_OWNER_APPROVAL_EVIDENCE
EVIDENCE_SCOPE_MISMATCH
EVIDENCE_EXPIRED
EVIDENCE_HASH_MISMATCH
```

This protocol does not add these errors to any implementation.

## Persistence Boundary

Future implementations may persist approval evidence only through separately approved writer tasks.

Allowed future persistence targets may include:

- controlled execute response envelope
- task draft metadata or body
- `owner_decision_record` artifact
- orchestration event with `event_type: owner_decision_recorded`
- records entry under `5_tasks/records/`

Every persistence path must be separately Owner-approved and audited.

Persistence rules:

- evidence writes must be append-only or draft-only unless a specific protocol says otherwise
- raw platform payload storage is forbidden by default
- evidence records must carry non-secret references, not credentials
- evidence records must preserve source/project/operation scope
- evidence must not be stored in SessionStore unless a future SessionStore-specific protocol allows it
- evidence indexes, if any, are derived state and must not become source of truth

## Relationship To Existing Protocols

### AIPOS-77 Controlled Persistence Gate

Owner approval evidence does not bypass controlled execute. Dry-run, token, revalidation, actor checks, scope checks, Owner confirmation, and blocking reasons remain authoritative.

### AIPOS-92 SessionStore Credential Boundary

Evidence is not credential material. Future external clients may hold scoped capabilities, but evidence must not expose bearer tokens, OAuth credentials, session cookies, API keys, or raw private identity.

### AIPOS-96 MCP Server Boundary

MCP may carry evidence only as protocol translation over existing backend semantics. MCP must not confirm on behalf of the Owner, invent approvals, write hidden records, or expose broader tool visibility because evidence is present.

### AIPOS-106 External Intake Registry

`source_tag`, `client_tag`, `external_ref`, and redaction expectations align with AIPOS-106. This protocol defines the evidence envelope that AIPOS-106 deliberately left for a later task.

### AIPOS-108 External Intake Writer

AIPOS-108 accepts optional normalized `owner_approval_evidence` text in intake payloads, but it does not implement this structured envelope. A future patch may align that optional field with this protocol.

### AIPOS-109 MCP Intake Submit Write Tools

AIPOS-109 does not require owner approval evidence for `intake_submit`; it writes only a draft. Future MCP write tools that carry higher authority, such as `owner_decision_record`, must use this protocol before implementation.

## Future Implementation Notes

Recommended next slices:

- add optional structured evidence parsing to controlled execute confirm envelopes
- implement `owner_decision_record` as a controlled write with evidence required
- add MCP `owner_decision_record` dry-run and confirm tools using AIPOS-109 discipline
- decide whether any Board or CLI review surface should display evidence summaries

Each slice requires its own Owner Decision Gate and independent audit.

## Non-Goals

AIPOS-110 does not:

- implement `owner_decision_record`
- implement MCP write tools
- implement HTTP/SSE transport
- implement Board UI
- implement CLI evidence commands
- expand controlled execute allowlist
- alter existing dry-run token behavior
- alter existing owner confirmation token behavior
- create a live evidence registry
- store evidence in records, orchestration logs, drafts, task cards, SessionStore, or databases
- create token minting, token signing, revocation, auth/RBAC, or credential storage
- validate real external messages
- store raw chat messages or platform-native payloads
- create project directories
- mutate queue state
- publish drafts
- launch runtime behavior
- change MCP read tools
- modify lybra-im or any external client repository
