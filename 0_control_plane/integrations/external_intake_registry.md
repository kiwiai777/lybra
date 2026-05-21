# External Intake Registry Protocol

## Purpose

AIPOS-106 defines the protocol boundary for future external intake clients that submit normalized work requests into Lybra.

The first expected consumer is a separate `lybra-im` project. That project is outside the Lybra product repository. Lybra does not import chat-platform, messaging-vendor, contact, group, channel, or bot-specific concepts into its core protocol layer.

The registry model is declarative governance only. It describes how a future external intake source may be approved, named, routed, and scoped before any writer, API route, MCP write tool, capability token implementation, or task schema extension is enabled.

## Status

AIPOS-106 is protocol-only.

It does not create a live registry instance, approve any external source, mint or validate tokens, add a backend route, add a CLI command, add a Board UI control, add an MCP tool, change the controlled execute allowlist, write task cards, change task metadata validation, deploy a service, or enable an external bot.

## Surface Boundary

External intake is an integration boundary, not a hidden privileged write surface.

Lybra core peer surfaces remain:

- CLI
- local Dashboard / Board UI
- MCP server

An external intake client may later consume Lybra through approved MCP tools or HTTP API routes, but it must preserve the same backend authority model as every other surface:

- files remain authoritative
- controlled execute remains the only write gate for exposed mutations
- Owner Decision Gates remain non-delegable
- independent audit remains non-delegable
- blocked operations must stay blocked
- no source may bypass dry-run, token, revalidation, actor/scope checks, or confirmation rules

## External Client Boundary

External client projects are responsible for platform-specific behavior before data reaches Lybra.

External clients must handle:

- platform API integration
- user and channel identity mapping
- personally identifiable information filtering
- consent and privacy controls
- message deduplication before submit
- source-specific retry behavior
- source-specific audit evidence capture
- conversion into a normalized Lybra intake payload

Lybra accepts only normalized, planner-acceptable intake metadata. Platform-native message shapes are not Lybra protocol inputs.

## Client And Project Mapping

A future external intake client may route a normalized request to a project using `client_tag`.

Protocol rule:

```text
one client_tag maps to one 2_projects/<client_id>/ project workspace
```

The external client owns the private mapping between platform identities and `client_tag`. Lybra does not store platform contact names, channel ids, chat ids, group ids, user handles, or vendor-specific identifiers as routing authority.

Each routed project keeps its own governance files:

- `2_projects/<client_id>/decision_log.md`
- `2_projects/<client_id>/project_status.md`
- `2_projects/<client_id>/roadmap.md`

A future writer may use `client_tag` only as a routing hint into an Owner-approved project mapping. It must not infer a new project, create project directories, or expand routing authority without an Owner Decision Gate.

## Source Tag Rules

Every approved external intake source must use a stable `source_tag`.

Rules:

- use lowercase snake case
- keep names functional and deployment-neutral
- do not include vendor names, host names, personal names, phone numbers, chat ids, channel ids, group ids, account ids, domains, or secret material
- do not encode model names or agent names
- do not imply write authority
- do not reuse a tag for different external trust boundaries
- treat renames as supersession, not silent mutation

Examples of valid tag shape:

```text
external_owner_inbox
customer_intake_relay
support_triage_bridge
```

These are shape examples only. They do not approve live sources.

## Future Registry Descriptor Shape

A future registry entry may use the following descriptor shape.

```yaml
external_intake_source:
  source_tag:
  status: proposed | approved | paused | retired
  owner:
  purpose:
  client_tag_policy:
    required: true
    allowed_patterns:
    project_mapping_authority: external_client
  normalized_payload:
    required_fields:
      - source_tag
      - client_tag
      - external_ref
      - title
      - body
      - submitted_at
      - submitter_ref
    optional_fields:
      - priority_hint
      - requested_due_date
      - source_thread_ref
      - owner_approval_evidence
  privacy_boundary:
    pii_filtered_before_lybra: true
    platform_native_payload_allowed: false
    raw_message_storage_allowed: false
  capability_scope_template:
    operations:
      - intake_submit
      - owner_decision_record
    projects:
    expires_at_required: true
    owner_approval_evidence_required_for_confirm: true
  controlled_execute:
    dry_run_required: true
    confirm_required: true
    snapshot_revalidation_required: true
    owner_gate_preserved: true
  audit:
    independent_audit_required_for_enablement: true
```

AIPOS-106 does not create this descriptor as a live file format and does not add parser or validator support.

## Normalized Intake Payload Boundary

Future external intake payloads must be normalized before reaching Lybra.

The normalized payload should carry enough information for a planner or controlled writer to produce a reviewable task draft without exposing platform-native data.

Required future payload concepts:

- `source_tag`: approved external source identifier
- `client_tag`: project routing tag
- `external_ref`: stable external evidence reference
- `title`: short human-readable request title
- `body`: normalized request body
- `submitted_at`: source-side timestamp
- `submitter_ref`: non-PII submitter reference or redacted actor handle

Optional future payload concepts:

- `priority_hint`
- `requested_due_date`
- `source_thread_ref`
- `owner_approval_evidence`

AIPOS-106 does not add these fields to task cards. Task metadata extension is a separate future task.

## Capability Token Scope Template

A future external intake client may hold a long-lived scoped capability token only after a separate Owner-approved implementation task.

The maximum initial scope template is:

```yaml
capability_scope:
  operations:
    - intake_submit
    - owner_decision_record
  projects:
    - <client_id_or_project_id>
  expires_at:
  evidence_required: true
```

Rules:

- token scope must be explicit by operation
- token scope must be explicit by project
- expiration is required
- token use must be auditable
- high-risk writes are excluded
- confirmation must carry Owner approval evidence when required
- token authority must not grant audit authority
- token authority must not grant Owner decision authority

Excluded operations include, but are not limited to:

- git automation
- draft publish
- controlled execute allowlist changes
- runtime launch
- queue claim
- queue complete/block/reopen
- SessionStore append
- orchestration event append unless separately approved
- planner iteration append unless separately approved

This direction is aligned with the AIPOS-92 credential boundary: external automation may receive scoped, revocable capability, not broad workspace credentials.

## Owner Approval Evidence

Future confirm requests from an external intake client may include Owner approval evidence.

Evidence may describe:

- external message id or redacted evidence id
- evidence hash
- approval timestamp
- source_tag
- client_tag
- approving actor reference

AIPOS-106 does not define the final evidence schema and does not write evidence to records. AIPOS-110 defines the Owner approval evidence field protocol in `0_control_plane/board/owner_approval_evidence_protocol.md`; persistence remains a separate future task.

## Relationship To Existing Protocols

### AIPOS-77 Controlled Persistence Gate

External intake writes must go through controlled execute when enabled. This protocol does not bypass dry-run, token issuance, confirmation, snapshot revalidation, actor/scope checks, or blocking reasons.

### AIPOS-78 Context Pack

External intake may later become a source reference for context pack construction, but AIPOS-106 does not change context pack schema or render behavior.

### AIPOS-92 SessionStore Credential Boundary

External clients may later use scoped capability tokens, not broad workspace credentials or long-lived secret material inside sandbox workers.

### AIPOS-96 MCP Server

External intake write tools may later be exposed through MCP only as controlled execute translations. AIPOS-106 does not add MCP tools.

### AIPOS-97 Role Catalog

External intake does not create new agent roles or coordinator authority. Planner/coordinator authority remains governed by the existing role and workflow protocols.

## Future Task Boundary

The expected follow-up sequence is:

- task metadata extension for source/client/external reference fields
- external intake writer and capability scope protocol
- MCP write tools for intake submit and Owner decision record
- Owner approval evidence field protocol and persistence

Each follow-up requires its own Owner Decision Gate and independent audit.

## Non-Goals

AIPOS-106 does not:

- implement `lybra-im`
- create an external client repository
- name or approve a real messaging provider
- store raw chat messages
- store platform-native identifiers as Lybra routing authority
- create task card metadata fields
- change task validators
- create writer code
- create backend routes
- create CLI commands
- create Board controls
- create MCP tools
- create tokens
- validate tokens
- expand controlled execute allowlist
- mutate queue, drafts, records, orchestration logs, or SessionStore
- launch runtime behavior
- change deployment, auth/RBAC, TLS, reverse proxy, or public endpoint posture
- copy private workspace data into the product repo
