# Provenance Authority Gap Detection Protocol

## Status

AIPOS-193 defines the protocol-only boundary for detecting authoritative Lybra files that bypassed an approved gate.

This document does not implement a scanner, validator change, Board surface, state-recovery behavior, writer, signature system, migration, quarantine move, controlled-execute operation, runtime, scheduler, polling loop, heartbeat, agent launcher, credential handling, deployment behavior, public endpoint, or network-egress policy.

## 1. Purpose

AIPOS-191 exposed a concrete failure mode: a capable autonomous harness can write directly into a Lybra workspace, modify queue task cards, create a self-authored "audit" artifact, and edit product code without using the Lybra gate surface.

Layer 3 answers that failure mode:

```text
authoritative file exists
+ no matching gate provenance
-> invalid or quarantined derived state
-> excluded from effective truth
-> Owner-visible report
```

The goal is not to prevent every write. Prevention belongs to Layer 2 filesystem and credential boundaries. The goal here is accountability and recovery: bypass writes must be visible, local to the affected file, and unable to silently become valid Lybra truth.

## 2. Threat Model: Gate Bypass Writes

The target user population includes full-capability autonomous agent harnesses such as Claude Code, Codex, Hermes, OpenClaw, and similar tools. These harnesses may have ordinary filesystem access to the workspace unless Layer 2 narrows that access.

Assumed risks:

- an agent can create a file under `5_tasks/queue/pending/` without `draft_publish`;
- an agent can edit a claimed task card without `queue_claim`, `queue_return`, `audit_dispatch`, or `audit_verdict`;
- an agent can create records that look plausible but were not emitted by a gate;
- an agent can produce a report that claims audit PASS without the verdict gate;
- an agent can try to cause denial of service by planting one bad file.

AIPOS-193 addresses detection and authority interpretation. It does not provide data-loss prevention, external-system isolation, or secret-exfiltration prevention.

## 3. Authoritative File Classes

Authoritative files are durable files that Lybra readers may use to derive task, gate, audit, or orchestration state.

### Queue Task Cards

```text
5_tasks/queue/pending/*.md
5_tasks/queue/claimed/*.md
5_tasks/queue/completed/*.md
5_tasks/queue/blocked/*.md
```

Queue directory placement plus task-card frontmatter form current queue state. AIPOS-193 applies strongest provenance-authority checks to queue task cards because they are directly claimable, executable, auditable, or finalizable state.

Important current-state boundary:

- `pending` and `claimed` have existing gate paths that can produce provenance.
- `completed` and `blocked` currently do not have dedicated queue-complete or queue-block record writers. Existing `queue_mutation.py` with records can update a session record for complete/block, but it does not write a dedicated completion/block authority record.
- Therefore AIPOS-193 must not retroactively classify existing `completed` or `blocked` task cards as orphan-invalid solely because no dedicated complete/block provenance exists. They are a no-dedicated-provenance class until a future writer gate adds canonical records for those states.

### Drafts

```text
5_tasks/drafts/**/*.md
```

Drafts are pre-authoritative material. They are not executable truth until a publish gate creates a pending task. An orphan draft may be reported as `INFO` or `WARN`, but it must not be classified as `ORPHAN_INVALID` by this protocol.

### Records

```text
5_tasks/records/claims/**/*.md
5_tasks/records/sessions/**/*.md
5_tasks/records/returns/**/*.md
5_tasks/records/audit_dispatches/**/*.md
5_tasks/records/audit_verdicts/**/*.md
5_tasks/records/owner_decisions/*.md
```

Records are durable evidence. They are not a separate authority that overrides task cards, but forged or orphan records can mislead readers and must be detected.

### Orchestration Files

```text
5_tasks/orchestration/**/orchestration_events.md
5_tasks/orchestration/**/planner_iterations.md
5_tasks/orchestration/**/orchestration_state.md
5_tasks/orchestration/**/loop_state.md
5_tasks/orchestration/**/subtask_index.md
```

Append-only orchestration and planner files are authoritative only to the extent their entries come from the approved append gates. Generated summary/index state remains derived and must not overrule queue task cards or records.

## 4. Canonical Provenance Requirements

Canonical provenance defines which gate operation may create or advance each authoritative state and which durable evidence should prove it.

| File or state | Canonical gate source | Required provenance for effective truth |
| --- | --- | --- |
| draft task card | `draft_create`, AI authoring draft, external intake draft, or Owner/manual pre-authoring | For drafts only: source metadata or grandfather manifest; missing source is at most WARN |
| pending queue task | `draft_publish` or Owner-approved publish path | Publish provenance or adoption manifest entry; future implementation may require publish record / owner decision ref |
| claimed queue task | `queue_claim` | Matching claim record and session record, or grandfather manifest entry for legacy state |
| returned/audit-ready task metadata | `queue_return` | Matching return record and task-card return refs |
| audit dispatch task | `audit_dispatch` | Matching audit dispatch record linking reviewed task, audit task, reviewed executor, and return record |
| audit verdict metadata | `audit_verdict` | Matching audit verdict record; PASS may set `dependency_audit_status: PASS` but does not finalize |
| owner decision record | `owner_decision_record` | Valid owner decision record shape and matching scoped evidence fields |
| orchestration event entry | `orchestration_event_append` | Valid append entry with unique event id, refs, actor, source, and matching orchestration id |
| completed queue task | no dedicated current writer | Not subject to orphan-invalid based only on missing complete record until a future complete/finalize writer exists |
| blocked queue task | no dedicated current writer | Not subject to orphan-invalid based only on missing block record until a future block writer exists |

Records must match by explicit fields such as `task_id`, `claim_id`, `session_id`, `return_id`, `dispatch_id`, `verdict_id`, `reviewed_task_id`, `audit_task_id`, `owner_policy_ref`, canonical agent instance, and record path. Readers must not infer provenance from filename similarity, actor display names, role labels, or instance-id string parsing.

## 5. Gap Types: Orphan vs Dangling

AIPOS-193 distinguishes two directions of provenance failure.

### Orphan Authoritative File

An orphan authoritative file is a file that asserts Lybra truth but lacks a matching canonical gate provenance path.

Examples:

- a new pending task card appears without publish provenance and is not in the adoption manifest;
- a claimed task card references no valid claim/session provenance and is not grandfathered;
- a return record exists but has no matching claimed task/session context;
- an audit verdict record claims PASS for a task but does not match a dispatch/audit task context.

This is the primary AIPOS-193 / AIPOS-191 F-06 case.

### Dangling Provenance Reference

Dangling provenance is the opposite direction: a task or record points to a file or record that is missing.

Examples:

- task card has `claim_id`, but no claim record exists;
- task card has `return_record_ref`, but that return record cannot be found;
- audit task points to an audit dispatch record that is missing.

Dangling references are already represented by AIPOS-172/173 as `provenance_completeness: partial | missing | contradictory`. AIPOS-193 extends that model; it does not duplicate it.

### Contradictory Authority

Contradictory authority exists when durable files exist but disagree about the same state.

Examples:

- task card says claimed by `agent-01`; claim record says `agent-02`;
- audit verdict record reviews task A but the audit task claims to review task B;
- duplicate record ids exist for different task contexts.

Contradiction must fail safe and route to Owner review or BLOCK depending on the attempted operation.

## 6. Authority Verdicts And Quarantine Semantics

Recommended derived fields:

```yaml
authority_verdict: VALID | GRANDFATHERED | PRE_AUTHORITY_WARN | ORPHAN_INVALID | QUARANTINED | DANGLING | CONTRADICTORY
effective_truth: true | false
severity: info | warn | needs_owner | block
subject_type:
subject_ref:
source_refs:
reason_code:
reason:
suggested_next_action:
```

Meanings:

- `VALID`: matching canonical provenance exists and agrees with the file.
- `GRANDFATHERED`: file is covered by an explicit adoption manifest baseline captured before the untrusted agent was granted write access.
- `PRE_AUTHORITY_WARN`: draft or other pre-authority file lacks source provenance; report for hygiene but do not invalidate queue truth.
- `ORPHAN_INVALID`: a queue or record file asserts authority, has no acceptable provenance, is not grandfathered, and should be excluded from effective truth.
- `QUARANTINED`: a file is suspicious or ambiguous and requires Owner decision before it can be treated as effective truth.
- `DANGLING`: a provenance reference points to missing evidence; connect to AIPOS-173 completeness reporting.
- `CONTRADICTORY`: durable files conflict and must not be silently reconciled.

Recommended rule:

```text
ORPHAN_INVALID and QUARANTINED both have effective_truth: false.
ORPHAN_INVALID means "definitively lacks provenance."
QUARANTINED means "potentially valid but not safely decidable by the reader."
```

An implementation may map both to a top-level `BLOCK` verdict for mutation attempts, but it should preserve the distinction in detail fields.

### Localized Impact

Authority verdicts are file-local and task-local.

One orphan task must not block unrelated valid tasks, valid records, or a whole workspace read. This avoids self-denial-of-service where an untrusted agent plants one bad file to make the entire workspace unusable.

Readers should:

- exclude the orphan file from effective truth;
- report the exact offending path;
- continue deriving state for unrelated valid files;
- block only operations that depend on the orphan/quarantined file.

## 7. Reporting Surfaces

### `lybra validate`

Future validation should report authority gaps alongside existing schema, status, complexity, and record-reference checks.

Recommended additions:

```yaml
authority_summary:
  valid:
  grandfathered:
  pre_authority_warn:
  orphan_invalid:
  quarantined:
  dangling:
  contradictory:
authority_findings:
  - authority_verdict:
    effective_truth:
    severity:
    subject_type:
    subject_ref:
    source_refs:
    reason_code:
    reason:
```

Validation must not silently count `ORPHAN_INVALID` files as valid queue tasks.

### `lybra state recovery preview`

State recovery should use AIPOS-193 to extend the AIPOS-173 preview:

- `provenance_completeness` remains the dangling-reference view.
- `authority_verdict` reports whether the selected file itself is authorized.
- `effective_truth: false` means the file may be displayed for diagnosis but must not drive claim, return, audit, finalize, accepted-work unblock, or dependency satisfaction.

### Board

Board should eventually surface authority findings in the existing read-only lifecycle and runtime areas:

- show a clear `Invalid / quarantined provenance` badge for affected tasks;
- hide or de-emphasize invalid files from normal actionable queues;
- provide exact paths and reason codes;
- avoid raw tokens, credentials, shell transcripts, or raw prompts;
- avoid mutation controls unless a future Owner-gated remediation surface exists.

## 8. Historical Compatibility And Adoption Manifests

Grandfathering must not be inferred from timestamps.

The only acceptable grandfather mechanism is an explicit adoption manifest captured once before exposing the workspace to an untrusted agent.

Recommended manifest properties:

```yaml
manifest_type: lybra_authority_adoption_manifest
manifest_id:
workspace_id:
captured_at:
captured_by:
capture_surface:
hash_algorithm: sha256
entries:
  - path:
    file_hash:
    file_class:
    task_id:
    record_id:
    queue_state:
    adopted_authority_verdict: GRANDFATHERED
```

Rules:

- the manifest is additive and must not rewrite history;
- adoption captures only files that already existed at adoption time;
- adoption after the fact must not add new files to the grandfather set;
- files created after adoption must satisfy current provenance requirements;
- a manifest must be owned by the gate or Owner and placed outside untrusted agent write permission;
- if no Layer 2 wall or signature protects the manifest, its integrity is limited and must be reported as such;
- manual Owner migration or repair remains a separate gate.

This avoids the unsafe pattern "old timestamp equals trusted." Timestamp inference is not acceptable for authority.

## 9. Cryptographic Signature Direction

AIPOS-193 v0 does not require signatures.

The protocol reserves future fields:

```yaml
gate_signature:
authority_seal:
signature_key_ref:
signature_algorithm:
signed_payload_hash:
signed_at:
```

Signatures are the long-term hardening path for the target population of full-capability autonomous agents. Without signatures or a Layer 2 filesystem wall, a sufficiently capable agent may forge records that structurally match expected provenance.

Recommended direction:

- AIPOS-194 may implement structural provenance matching first if Owner accepts the residual forgery risk.
- After Layer 2 filesystem / credential boundaries are available, a later slice should add gate signatures or authority seals for records and adoption manifests.
- Signature keys must not be placed inside the untrusted agent write boundary.
- Signatures do not grant authority by themselves; they authenticate that the gate wrote the provenance being evaluated.

## 10. Layer 2 Relationship: Filesystem And Credential Boundary

AIPOS-193 is Layer 3 detection. Layer 2 is still required for prevention.

Roadmap position:

- AIPOS-195/196 should cover local filesystem write boundaries and credential boundaries.
- The sandbox should expose only the role MCP token and the agent's own LLM key where needed.
- It must not expose git push, deployment, production, cloud, or other state-changing credentials.
- Network egress control is explicitly deferred to a future Owner gate.

Residual risk:

```text
accountability is not data-loss prevention.
```

AIPOS-193 can detect invalid authority writes after they happen. It does not stop secret exfiltration or unwanted external calls.

Trigger for the deferred egress gate: a real requirement appears to prevent external disclosure or constrain third-party system access.

## 11. Recommended AIPOS-194 Thin Implementation Slice

Recommended first implementation:

1. Add a read-only authority scanner for queue task cards and records.
2. Report `ORPHAN_INVALID`, `QUARANTINED`, `DANGLING`, and `CONTRADICTORY` findings without writing anything.
3. Integrate the scanner into `lybra validate` and `lybra state recovery preview`.
4. Exclude orphan-invalid queue tasks from effective truth for actionable operations.
5. Preserve localized impact: bad file blocks only itself and dependent operations.
6. Treat drafts as `PRE_AUTHORITY_WARN`, not invalid authority.
7. Treat completed/blocked queue files as no-dedicated-provenance classes until a future complete/block/finalize writer exists.
8. Support an adoption manifest path only if Owner approves where that manifest lives outside untrusted writes.
9. Do not sign in v0 unless Owner explicitly expands the slice before implementation.

Board surfacing, remediation UX, signed gates, historical backfill, and manifest authoring can be separate slices.

## 12. AIPOS-194 Audit Requirements

The AIPOS-194 independent audit must include adversarial evidence, not only unit tests and static review.

Required adversarial test:

```text
direct filesystem injection:
  create 5_tasks/queue/pending/<orphan>.md
  do not create matching publish/gate provenance
expected:
  scanner reports ORPHAN_INVALID or QUARANTINED
  effective_truth is false
  unrelated valid tasks remain valid/actionable
  orphan does not enter claimable/effective queue truth
```

Additional audit expectations:

- direct injected orphan record under `5_tasks/records/` is detected;
- dangling refs still route through AIPOS-173 provenance completeness;
- draft orphan reports only info/warn;
- completed/blocked current no-provenance classes are not retroactively invalidated;
- adoption manifest behavior is deterministic and not timestamp-based;
- no auto-delete, auto-move, auto-repair, hidden migration, controlled-execute expansion, runtime launch, scheduler, polling, heartbeat, or credential behavior appears.

This audit requirement follows the AIPOS-190 lesson: launch/authority-sensitive features need real-path evidence, not only mocked tests.

## 13. Deferred Gates And Non-Goals

Deferred:

- AIPOS-194 implementation;
- Layer 2 filesystem wall and credential boundary, AIPOS-195/196;
- network egress allowlist or DLP controls;
- adoption manifest authoring and storage policy;
- gate signatures / authority seals;
- remediation UI or CLI;
- quarantine move/delete/repair writers;
- finalize writer;
- accepted-work unblock;
- active lease writer;
- Delegated / Standing automation;
- Trace-Native Audit;
- runtime launcher, daemon, scheduler, polling, heartbeat;
- deployment, public endpoint, credential proxy, live BYO-LLM, external-intake behavior.

Non-goals:

- do not rewrite history;
- do not infer trust from timestamps;
- do not parse semantic meaning from agent instance ids;
- do not treat signatures, manifests, or records as authority independent of gate semantics;
- do not let one orphan file deny service to unrelated valid files;
- do not narrow Lybra's target audience away from full-capability autonomous agent harnesses.

### AIPOS-191B Readiness

AIPOS-191B, the true heterogeneous autonomous-agent closed-loop rerun, should wait until:

```text
Layer 3: AIPOS-193/194 provenance-authority gap detection
+ Layer 2: AIPOS-195/196 filesystem and credential boundary
```

are available. Otherwise, the AIPOS-191 F-06 bypass remains structurally possible and only manually observable after the fact.
