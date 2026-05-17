# Agent Instance Policy

## Purpose

This policy defines the difference between logical agent roles and concrete agent instances in AI Project OS.

It also defines the naming convention, required instance fields, tier binding, escalation behavior, and write authority boundaries used by future routing and dispatch work.

This policy is protocol documentation only. It does not implement agent polling, matching, claim competition, session leases, runtime launch, or queue mutation.

## Logical Role vs Concrete Instance

A logical agent role describes a class of responsibility, such as information collection, business development, coding, review, or operations.

A concrete agent instance is a runnable or manually selected execution identity with a runtime, runtime profile, model tier, task scope, write target, and authority boundary.

One logical role may have multiple concrete instances. This is especially important for remote 24h agents, where each persistent instance may be bound to one model tier.

## Naming Convention

Recommended concrete instance ID format:

```text
{domain}.{agent}.{runtime}.{tier}
```

Examples:

```text
info.hermes.cloud.l1
info.hermes.cloud.l2
info.hermes.cloud.l3
biz.lobster.cloud.l1
biz.lobster.cloud.l2
biz.lobster.cloud.l3
dev.claude.local.l2
dev.codex.local.l2
```

Local manual agents may also use a stable non-tiered identity, such as `dev.codex.local`, when the task card records the selected `model_tier`.

## Required Instance Fields

Concrete instance definitions should include:

```yaml
id:
role:
runtime:
runtime_profile:
model_tier:
task_scope:
write_target:
formal_memory_write:
external_action_allowed:
escalation_to:
owner_approval_required:
```

Field meanings:

- `id`: Concrete instance ID.
- `role`: Logical role or agent family.
- `runtime`: Runtime location or type, such as `local`, `cloud`, or `browser`.
- `runtime_profile`: Runtime profile reference when one exists.
- `model_tier`: Bound model tier for this instance, or task-selected tier for local manual identities.
- `task_scope`: Allowed task categories.
- `write_target`: Allowed default output target or target family.
- `formal_memory_write`: `false`, `candidate_only`, or `owner_approval_required`.
- `external_action_allowed`: `false` or `owner_approval_required` unless a future policy defines narrower permissions.
- `escalation_to`: Higher tier instance, Owner, or review path.
- `owner_approval_required`: Whether this instance requires Owner approval for specific outputs or actions.

## Model Tier Binding

Remote 24h agents may expose multiple persistent instances, each bound to one model tier.

Local manual agents may be switched by Owner per task according to the task card `model_tier`. The selected tier must be reported in the completion report.

Model tier binding does not override task risk policy. A tier-bound instance may still need to escalate if the task exceeds its scope or write authority.

## Write Authority

An instance may only write to targets allowed by the task card, context bundle, and instance policy.

L1 instances default to inbox, candidate, and draft targets. They must not write formal memory directly.

L2 instances may write review artifacts and drafts but must not finalize strategic decisions or formal memory promotions.

L3 instances may write formal memory candidates, decision candidates, and high-risk drafts. Final formal commit, overwrite, external publication, destructive action, or live production action requires Owner approval where applicable.

## Escalation

`escalation_to` names the next review or execution target when an instance reaches its boundary.

Escalation may point to:

- a higher-tier instance, such as `info.hermes.cloud.l2`
- an L3 instance, such as `biz.lobster.cloud.l3`
- Owner review
- a future dispatch or audit task

Escalation does not imply automatic queue mutation in this task. Future AIPOS-48 work may define matching and claim behavior.

## Example Instances

### Info Hermes

```yaml
id: info.hermes.cloud.l1
role: info.hermes
runtime: cloud
runtime_profile: hermes_cloud_24h
model_tier: L1
task_scope:
  - hourly information collection
  - extraction
  - tagging
  - dedupe
write_target: 4_inbox/cloud.hermes.info/
formal_memory_write: false
external_action_allowed: false
escalation_to: info.hermes.cloud.l2
owner_approval_required: false
```

```yaml
id: info.hermes.cloud.l2
role: info.hermes
runtime: cloud
runtime_profile: hermes_cloud_24h
model_tier: L2
task_scope:
  - daily synthesis
  - multi-source summary
  - issue clustering
  - draft research memo
write_target: review artifacts
formal_memory_write: false
external_action_allowed: false
escalation_to: info.hermes.cloud.l3
owner_approval_required: false
```

```yaml
id: info.hermes.cloud.l3
role: info.hermes
runtime: cloud
runtime_profile: hermes_cloud_24h
model_tier: L3
task_scope:
  - weekly strategic analysis
  - formal research conclusion candidates
  - high-confidence recommendations
write_target: formal memory candidates
formal_memory_write: candidate_only
external_action_allowed: owner_approval_required
escalation_to: Owner
owner_approval_required: true
```

### Biz Lobster

```yaml
id: biz.lobster.cloud.l1
role: biz.lobster
runtime: cloud
runtime_profile: lobster_cloud_24h
model_tier: L1
task_scope:
  - lead list cleanup
  - simple campaign status extraction
  - low-risk content variants
write_target: 4_inbox/cloud.openclaw.biz/
formal_memory_write: false
external_action_allowed: false
escalation_to: biz.lobster.cloud.l2
owner_approval_required: false
```

```yaml
id: biz.lobster.cloud.l2
role: biz.lobster
runtime: cloud
runtime_profile: lobster_cloud_24h
model_tier: L2
task_scope:
  - campaign plan draft
  - customer objection grouping
  - sales material draft
write_target: review artifacts
formal_memory_write: false
external_action_allowed: false
escalation_to: biz.lobster.cloud.l3
owner_approval_required: false
```

```yaml
id: biz.lobster.cloud.l3
role: biz.lobster
runtime: cloud
runtime_profile: lobster_cloud_24h
model_tier: L3
task_scope:
  - pricing recommendation
  - key customer proposal
  - business strategy recommendation
write_target: formal memory candidates
formal_memory_write: candidate_only
external_action_allowed: owner_approval_required
escalation_to: Owner
owner_approval_required: true
```

### Local Dev Agents

```yaml
id: dev.claude.local.l2
role: dev.claude
runtime: local
runtime_profile: claude_code
model_tier: L2
task_scope:
  - implementation
  - code explanation
  - medium-risk review
write_target: repository workspace
formal_memory_write: false
external_action_allowed: false
escalation_to: dev.claude.local.l3 or Owner
owner_approval_required: task_dependent
```

```yaml
id: dev.codex.local.l2
role: dev.codex
runtime: local
runtime_profile: codex_cli
model_tier: L2
task_scope:
  - implementation
  - audit support
  - repository review
write_target: repository workspace
formal_memory_write: false
external_action_allowed: false
escalation_to: dev.codex.local.l3 or Owner
owner_approval_required: task_dependent
```

## Completion Report Requirements

Completion reports must include:

- selected `model_tier`
- concrete `agent_instance`
- whether escalation was triggered
- escalation target when relevant
- any Owner approval requirement encountered

If the actual model tier or instance differs from the task card, the report must state the difference and why it occurred.
