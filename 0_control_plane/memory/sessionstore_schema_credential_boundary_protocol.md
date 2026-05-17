# SessionStore Schema and Credential Boundary Protocol

## Purpose

AIPOS-92 defines the protocol boundary for Lybra SessionStore schema, source-of-truth discipline, local-first topology, and credential handling.

SessionStore is the future memory and resume-support layer for Lybra agents. It must preserve Lybra's file-authoritative control plane and must not become a hidden database source of truth.

AIPOS-92 is protocol documentation only. It does not create directories, implement schema migrations, add SQLite/HNSW dependencies, implement indexes, modify `.gitignore`, add CLI commands, add backend routes, add Web UI behavior, deploy services, mint tokens, store credentials, launch runtimes, or enable autonomous agents.

## Strategic Sources

AIPOS-92 implements the SessionStore protocol direction from:

- DL-20260513-04 Decision 2: SessionStore placement and stateless agent direction.
- DL-20260515-06 Decision 6: four-tier memory model, explicit supersession, Owner-gated consolidation, capability-token direction, `tenant_id`, and provider-agnostic embeddings.
- DL-20260516-01 Decision 9: local-first deployment discipline and shared-root convention.
- DL-20260516-01 Decision 10: Pattern B append-only log plus derived state source-of-truth discipline.

## Core Principles

- SessionStore is local-first before commercialization.
- File-backed append-only records are the source of truth.
- Derived indexes are caches, never authorities.
- Semantic memory may become queryable through local derived indexes only after Owner approval.
- Sandbox workers must use scoped, short-lived capability tokens instead of long-lived `.env` credentials.
- Every entry must be traceable to file-backed source evidence.
- Consolidation from episodic to semantic memory is Owner-gated and never automatic.

## Source-of-Truth Mode

Lybra SessionStore commits to Pattern B:

```text
append-only file records -> replay/rebuild -> derived state and indexes
```

The source of truth is the file-backed event or entry sequence. Current query state is derived by reading and replaying those records.

Forbidden source-of-truth modes:

- Postgres as source of truth
- MySQL as source of truth
- MongoDB as source of truth
- SQLite as source of truth
- HNSW/vector index as source of truth
- object storage as the only mutable canonical state
- any mutable canonical store that cannot be rebuilt from files

SQLite and HNSW are permitted only as future local derived indexes after a separate Owner Decision Gate. Deleting the derived index must not lose Lybra state.

## Local-First Topology

MVP default topology:

```text
user workstation
  product repo
  private workspace
  file-backed SessionStore records
```

No Lybra-managed cloud, remote database, remote object storage, remote credentials, or SaaS endpoint is required for MVP operation.

Cross-machine collaboration may use a user-owned private shared root, such as NAS, private cloud, self-hosted server, or internal host. This shared root is a user asset, not a Lybra-managed backend.

Recommended protocol fields:

```yaml
sessionstore_location:
  deployment_topology: local_first
  workspace_root_ref:
  shared_root_ref:
  sessionstore_root_ref:
  path_convention:
  owner_override_allowed: true
```

Recommended future config field:

```yaml
shared_root:
```

Recommended future environment alias:

```text
LYBRA_SHARED_ROOT
```

`LYBRA_SHARED_ROOT` is a protocol target only. AIPOS-92 does not implement environment-variable reading or enforcement.

AIPOS-88 `/opt/kiwiai/` remains the Kiwiai instance implementation of a broader shared-root convention. It is not the Lybra product default and AIPOS-92 does not modify AIPOS-88.

## Suggested Future Paths

Future private workspace path candidates:

```text
{workspace_root}/sessionstore/
{workspace_root}/sessionstore/entries/
{workspace_root}/sessionstore/events/
{workspace_root}/sessionstore/indexes/
```

MVP file-backed entries may live under:

```text
{workspace_root}/sessionstore/entries/{scope}/{entry_id}.md
```

MVP consolidation events may live under:

```text
{workspace_root}/sessionstore/events/{event_id}.md
```

Derived indexes may later live under:

```text
{workspace_root}/sessionstore/indexes/
```

These are protocol targets only. AIPOS-92 does not create directories or files.

## Memory Tier Model

SessionStore uses four tiers.

### working

Working tier is active sandbox or local agent state during execution.

Rules:

- default location is in-sandbox or in-process memory
- optional sync to SessionStore is only for resume support
- not retained long-term in SessionStore
- must not become source of truth
- must be discardable after task/session end unless explicitly promoted through approved records

Allowed entry types:

```text
checkpoint
artifact_ref
```

### episodic

Episodic tier records checkpoint snapshots at session or sandbox teardown.

Rules:

- must include `source_record_ref`
- SessionStore is an index over file-backed task/orchestration/queue evidence
- not source of truth for task completion or queue status
- may support resume, audit trace, and later consolidation

Allowed entry types:

```text
checkpoint
memory_ref
artifact_ref
decision_ref
```

### semantic

Semantic tier contains consolidated project or orchestration facts.

Rules:

- only tier where SessionStore may own canonical semantic entries
- each entry still requires `source_record_ref`
- consolidation is Owner-gated controlled execute in future
- no auto-consolidation
- explicit supersession is required for changes
- no Ebbinghaus-style automatic decay

Allowed entry types:

```text
memory_ref
decision_ref
artifact_ref
```

### procedural

Procedural tier indexes skills, patterns, and repeatable methods.

Rules:

- stores pointers and indexes, not raw private data
- should point to `3_context_bundles/`, AIPOS-78 Context Pack outputs, or approved protocol docs
- derived vector indexes are allowed only as rebuildable indexes

Allowed entry types:

```text
pattern
skill_index
artifact_ref
```

## Entry Schema

Recommended file-backed entry frontmatter:

```yaml
store_id:
tenant_id: default
scope:
scope_ref:
tier:
entry_type:
entry_id:
title:
status: live
created_at:
created_by_agent_instance:
created_by_session_id:
source_record_ref:
source_refs: []
supersedes:
superseded_by:
capability_token_ref:
capability_scope:
capability_expires_at:
mutation_log_ref:
content_hash:
signed_by:
retention_policy:
embedding:
embedding_provider:
embedding_dim:
embedding_model_version:
hnsw_index_ref:
access_history_ref:
```

Allowed `scope` values:

```text
project
orchestration
task
global
```

Allowed `tier` values:

```text
working
episodic
semantic
procedural
```

Allowed `entry_type` values:

```text
checkpoint
memory_ref
artifact_ref
pattern
skill_index
decision_ref
```

Allowed `status` values:

```text
live
superseded
archived
```

Allowed `retention_policy` values:

```text
session_only
keep_until_superseded
project_lifetime
global_lifetime
owner_review_required
```

`tenant_id` is required from day one. Single-tenant local MVP uses:

```yaml
tenant_id: default
```

## Entry Body

Recommended markdown body:

```markdown
# SessionStore Entry: {entry_id}

## Summary

## Content

## Source Evidence

## Scope

## Supersession

## Access / Capability Notes

## Notes
```

Large artifacts, transcripts, model outputs, and binary files should be linked, not embedded.

## Source References

Every durable entry must include `source_record_ref`.

Allowed source reference families include:

```text
5_tasks/records/
5_tasks/orchestration/
5_tasks/queue/
2_projects/lybra/
3_context_bundles/
1_shared_memory/
approved external artifact refs
```

If source evidence is ambiguous, the entry must remain proposed or blocked until Owner or reviewer clarification.

## Supersession Discipline

SessionStore does not mutate entries in place.

Change pattern:

```text
old entry remains
new entry is appended
old entry is marked superseded by metadata or supersession event
new entry points back through supersedes
```

`superseded` is not deletion.

Hard deletion is outside AIPOS-92 and requires separate Owner-approved retention and privacy policy.

## Consolidation Event Schema

Episodic to semantic consolidation is future controlled execute and Owner-gated.

Recommended consolidation event frontmatter:

```yaml
event_id:
tenant_id: default
event_type: sessionstore_consolidation
scope:
scope_ref:
consolidated_from_entries: []
produced_entry:
owner_confirmation_token:
dry_run_token:
dry_run_snapshot_hash:
consolidator_agent_instance:
consolidated_at:
rationale:
source_record_refs: []
verdict:
```

Allowed `verdict` values:

```text
approved
rejected
needs_owner
blocked
```

Rules:

- auto-consolidation is forbidden
- consolidation requires dry-run preview
- consolidation requires Owner confirmation
- consolidation must preserve source refs
- consolidation must not delete episodic entries
- consolidation must not bypass audit when audit is required

AIPOS-92 does not implement consolidation, dry-run token creation, or controlled execute behavior.

## Credential Boundary

SessionStore access by sandbox workers must use ephemeral capability tokens.

Capability token rules:

- minted per task, session, or sandbox worker
- scoped to explicit operations
- expires on sandbox destroy or short TTL
- revocable by Owner-controlled boundary
- logged by reference only
- never committed to git
- never copied into reports
- never stored as durable `.env`

Recommended capability fields:

```yaml
capability_token_ref:
capability_scope:
capability_expires_at:
capability_issuer_ref:
capability_subject:
capability_bound_task_id:
capability_bound_session_id:
capability_bound_sandbox_id:
capability_revocation_ref:
```

Allowed `capability_scope` values:

```text
read
write
append
rotate
reindex
```

`write` means append-or-supersede under Pattern B. It does not mean mutable in-place update.

Long-lived `.env` credentials are forbidden for sandbox worker SessionStore access. Host-local service configuration may still use `.env` for other services only when kept out of git, permissioned, and Owner-approved under the relevant environment policy.

## Two-Stage Architecture

### Stage 1 — MVP Default

Stage 1 uses:

```text
file-backed entries + plain directory scan
```

Recommended performance planning target:

```text
approximately 5k semantic entries
```

The exact threshold is advisory and may be revised by later implementation tasks.

Stage 1 does not require SQLite, HNSW, embeddings, vector search, daemon, server, or database.

### Stage 2 — Owner-Gated Derived Index

Stage 2 may add:

```text
local SQLite metadata index
local HNSW/vector index
```

Stage 2 requirements:

- separate Owner Decision Gate
- separate AIPOS implementation task
- independent audit
- index must be local by default
- index must be rebuildable from file-backed entries
- index files must not be committed to git
- deleting index files must not lose Lybra state
- CLI/UI must not require inspecting indexes to understand current state

Candidate future command:

```text
lybra sessionstore reindex
```

This is a naming target only. AIPOS-92 does not implement it.

## Embedding Metadata

Embedding schema must be provider-agnostic.

Recommended fields:

```yaml
embedding:
embedding_provider:
embedding_dim:
embedding_model_version:
embedding_created_at:
embedding_source_entry_hash:
```

Allowed dimensions are not fixed. Examples include:

```text
384
768
1536
other
```

Provider names must be metadata, not hard dependencies. AIPOS-92 does not download models, call embedding providers, or add embedding dependencies.

## Access History

Access history is optional metadata.

Recommended shape:

```yaml
access_history_ref:
last_read_at:
last_read_by:
last_write_at:
last_write_by:
```

Access history must not leak secrets or raw prompts. Detailed audit may be a future append-only event stream.

## Owner Decision Gates

Owner approval is required before:

- enabling derived index Stage 2
- adding SQLite/HNSW dependencies
- adding embedding provider calls
- changing credential storage
- minting or revoking real capability tokens
- granting sandbox worker access
- enabling shared-root cross-machine SessionStore use
- enabling remote database or remote object storage
- changing retention or deletion policy
- adding hard delete behavior
- exposing SessionStore through MCP
- adding Web UI write controls
- adding CLI write commands
- enabling auto-consolidation
- enabling autonomous planner/runtime memory writes
- treating any derived index as source of truth

## Relationship To Existing Protocols

- AIPOS-83 defines product repo and private workspace separation. SessionStore belongs to private workspace data unless a future task explicitly templates examples.
- AIPOS-88 defines Kiwiai `/opt/kiwiai/` as an instance server policy, not the Lybra product default.
- AIPOS-90 defines sandbox runtime credential boundary direction. AIPOS-92 defines SessionStore capability-token expectations.
- AIPOS-90D defines SessionStore tier direction and role catalog context.
- AIPOS-91 defines Session Tree lineage metadata. SessionStore may reference session tree evidence but does not implement Session Tree operations.
- AIPOS-91D defines local-first and Pattern B source-of-truth discipline. AIPOS-92 follows that discipline.
- Future AIPOS-96 MCP work may expose read tools for SessionStore only through approved boundaries and must not bypass controlled execute for writes.

## Non-Goals

AIPOS-92 does not implement:

- SessionStore directory creation
- file writer
- schema migration
- SQLite
- HNSW
- vector search
- embedding model download
- embedding provider calls
- `.gitignore` changes
- `lybra sessionstore reindex`
- CLI commands
- backend routes
- Web UI controls
- MCP server tools
- capability token minting
- credential storage
- credential rotation
- sandbox runtime integration
- remote database
- remote object storage
- cloud synchronization
- Lybra-managed cloud
- auth/RBAC
- OAuth
- TLS
- reverse proxy
- deployment configuration
- public endpoint behavior
- queue mutation
- draft mutation
- records mutation
- orchestration writes
- autonomous planner runtime
- background polling
- agent execution UI
- git automation
- automatic commit/push
- self-audit
