# Model Routing Policy

## Purpose

This policy defines how AI Project OS selects model tiers and concrete agent instances for task execution.

Model tier is not a fixed role property. Model routing is determined by task level, risk level, runtime type, write target, context bundle, and external action risk.

This policy is protocol documentation only. It does not implement agent matching, task claim competition, session leases, runtime switching, queue mutation, or scheduler behavior.

## Core Rule

Model routing is bound to task requirements, model tier, and agent instance policy. It is not permanently bound to an agent role.

A logical role such as `info.hermes`, `biz.lobster`, `dev.claude`, or `dev.codex` may run tasks at different model tiers when the task card, context bundle, and concrete instance allow it.

## Routing Inputs

Routing decisions must consider:

- task `model_tier`
- task risk level
- task mode
- preferred logical role
- preferred concrete `agent_instance`
- context bundle
- runtime type, such as local manual or remote 24h
- write target
- formal memory write intent
- external action risk
- owner approval requirements

Future automated matching must preserve these inputs instead of treating role identity as a fixed model selector.

## Tier Behavior

### L1 Tasks

L1 is for high-frequency, low-risk, structured work with verifiable output.

L1 output defaults to inbox, candidate, or draft areas. L1 must not directly write formal memory, publish externally, make commitments, or perform irreversible actions.

L1 should escalate to L2 or L3 when confidence is low, sources conflict, customer impact appears, or the output becomes a formal memory candidate.

### L2 Tasks

L2 is for synthesis, review drafts, and medium-risk reasoning.

L2 may write review artifacts, draft reports, candidate plans, and non-final project notes. L2 must not make final strategic decisions, final pricing decisions, final formal memory promotions, or high-risk external commitments.

L2 should escalate to L3 when strategic, production, pricing, customer commitment, release, or formal memory promotion risk appears.

### L3 Tasks

L3 is for strategic reasoning, formal conclusion candidates, pricing recommendations, key customer plans, high-risk drafts, architecture decisions, complex review, and release-risk analysis.

L3 may create formal memory candidates and decision candidates. Final formal commit, overwrite, destructive repository operation, external publication, pricing commitment, live production action, or key customer commitment still requires Owner approval where applicable.

### Critical Tasks

Critical tasks require at least L3 and explicit Owner approval for finalization or external action.

Critical tasks include live production action, destructive repository action, formal memory overwrite, external publication, pricing commitment, and key customer commitment.

## Local Manual Agents

Local agents may be manually switched by Owner according to the task card `model_tier`.

Local manual agents may use a stable instance identity such as `dev.codex.local` or `dev.claude.local`, with the selected model tier recorded on the task card and in the completion report. If a local task needs a tier-specific identity, use the agent instance policy naming convention, such as `dev.codex.local.l2`.

Local manual switching does not grant extra write authority. Write authority is still bounded by task card, context bundle, repository policy, and Owner approval requirements.

## Remote 24h Agents

Remote 24h agents may expose multiple persistent instances, each bound to one model tier.

Examples:

- `info.hermes.cloud.l1`
- `info.hermes.cloud.l2`
- `info.hermes.cloud.l3`
- `biz.lobster.cloud.l1`
- `biz.lobster.cloud.l2`
- `biz.lobster.cloud.l3`

Remote tasks should route to the lowest safe tier that satisfies the task card and risk policy. Remote 24h instances must not self-upgrade final authority; escalation creates or hands off to a higher-tier instance or Owner review path.

## Formal Memory Candidate Writes

Formal memory candidate writes require L3 unless Owner explicitly defines a narrower exception in a task card.

L1 must not write formal memory directly. L2 may prepare review artifacts or non-final drafts but must not create final strategic decisions. L3 may create formal memory candidates, decision candidates, and stage archive drafts, but final formal commit or overwrite requires Owner approval where applicable.

## Owner Approval Requirements

Owner approval is required for:

- formal memory commit or overwrite
- external publication
- pricing commitment
- key customer commitment
- live deployment or production action
- destructive repository action
- any critical-risk task finalization

Owner approval may also be required by task card, context bundle, write target, or instance policy even when model tier would otherwise be sufficient.

## Escalation Behavior

Escalation should preserve the original task context and explain why the current tier is insufficient.

Common escalation paths:

- L1 to L2 for medium-risk synthesis or source conflict
- L1 to L3 for formal memory candidates, customer commitments, or high-risk ambiguity
- L2 to L3 for strategy, production, pricing, release, formal memory promotion, or Owner-requested high confidence
- Any tier to Owner when approval is required or policy is ambiguous

Escalation does not mutate queue state by itself in this task. Automated matching and claim behavior are future AIPOS-48 work.

## Examples

### Hourly Information Collection

```yaml
model_tier: L1
agent_instance: info.hermes.cloud.l1
schedule: hourly
write_target: 4_inbox/cloud.hermes.info/
formal_memory_write: false
escalation_to: info.hermes.cloud.l2
```

### Daily Intelligence Synthesis

```yaml
model_tier: L2
agent_instance: info.hermes.cloud.l2
schedule: daily
write_target: 2_projects/example/reports/
formal_memory_write: false
escalation_to: info.hermes.cloud.l3
```

### Strategic Customer Plan

```yaml
model_tier: L3
agent_instance: biz.lobster.cloud.l3
risk_level: high
write_target: 2_projects/example/strategy/
formal_memory_write: candidate_only
owner_approval_required: true
```

### Local Development Review

```yaml
model_tier: L2
agent_instance: dev.codex.local
runtime: local_manual
write_target: review report
formal_memory_write: false
escalation_to: Owner
```

## Forbidden Patterns

- Treating model tier as permanently fixed by role.
- Treating all remote tasks as L3.
- Treating all local tasks as L1 or L2.
- Allowing L1 to write formal memory directly.
- Allowing L2 to make final strategic, pricing, or customer commitments.
- Treating L3 as sufficient for external publication without Owner approval.
- Implementing task matching, claim competition, or session lease behavior in this policy task.
