# Combined Planner/Executor Mode and Owner Decision Gate Policy

## Purpose

AIPOS-53 defines how one agent instance may combine planner, executor, and finalize-operator work while preserving independent audit authority and non-delegable Owner decision authority.

This policy is protocol documentation only. It does not implement a planner loop runtime, queue polling runtime, draft writer, publish automation, forum backend, CLI command, Web UI behavior, database, server, deployment configuration, or any new runtime service.

## Core Governance Rule

AIPOS keeps three independent governance authorities:

```text
execution authority -> planner / executor / finalize operator
audit authority     -> independent auditor
owner authority     -> human Owner decision
```

Combined planner/executor mode may colocate planning and execution inside the same execution authority. It must not combine execution authority with independent audit authority or Owner decision authority.

## Combined Planner/Executor Definition

A combined planner/executor is a concrete agent instance that may operate in more than one execution mode for a scoped task or parent requirement.

Recommended declaration:

```yaml
agent_instance: dev.generic.local.001
logical_agent: dev_codex
runtime_profile: local_process_workspace
governance_authority: execution
allowed_modes:
  - planner
  - executor
  - finalize_operator
forbidden_modes:
  - independent_auditor
  - owner_decider
```

The engineering-specific `planner/coder` case is one instance of this broader planner/executor policy. Non-code task modes, such as documentation, research, operations, sales support, presentation production, image production, or material production, may use the same governance pattern when the task card selects the appropriate `task_mode`.

## Allowed Execution Modes

An execution authority agent may act as:

- `planner`: clarify a requirement, inspect repo state, propose a plan, draft subtasks, recommend assignment, and prepare audit handoff.
- `executor`: perform task-scoped implementation or production work, including code, documentation, research, content, operational, or other task-mode-specific output.
- `finalize_operator`: run the internal finalize gate after independent audit PASS and Owner gates are clear.

These modes are task-scoped. They do not create permanent global planning, execution, audit, GitHub, OS, or Owner authority.

`task_class` is orthogonal to `task_mode`. Complex-class work preserves the full governed closed loop even when planner and executor authority are combined.

## Audit Separation

The auditor must be independent from the execution authority for work that requires audit.

Rules:

- A combined planner/executor must not audit its own work.
- The configured independent auditor remains separate from any combined planner/executor authority.
- Internal subagents of the same runtime are internal delegation only and do not count as independent auditors.
- A different agent/runtime may serve as auditor only when it is explicitly declared as independent and Owner-approved.
- Audit PASS is required before finalize for tasks whose policy or Owner direction requires audit.

## Owner Decision Gate

Owner decision authority is human, separate, and non-delegable.

The combined planner/executor must pause and request Owner decision before continuing when any of these forks appears:

- architecture route split
- scope expansion
- risk escalation
- new runtime, service, database, deployment, or credential boundary
- security or credential boundary change
- audit boundary change
- workflow mode change
- model tier or agent authority expansion
- turning protocol into implementation
- skipping reviewer, audit, or finalize gate
- paid resource or external service requirement
- data loss or irreversible action risk
- ambiguous assignment, reviewer, auditor, dependency, or publish authority

The agent may recommend options, tradeoffs, and a default path. The Owner decides the fork.

## Internal Subagent Delegation

Internal subagent delegation may be used to reduce work latency or organize execution, but it stays inside the same execution authority unless Owner explicitly approves a separate runtime identity.

Internal subagents may:

- gather bounded context
- implement a scoped part of the task
- check consistency for the execution agent
- produce draft recommendations

Internal subagents must not:

- become the independent auditor for the parent agent's work
- act as Owner decider
- expand scope without Owner approval
- bypass task claim, session lease, audit, or finalize policy

The parent execution agent remains responsible for the delegated output.

## Relationship To AIPOS-52

AIPOS-52 remains the planner subtask draft and publish flow.

Combined planner/executor mode does not rewrite that flow. If a planner creates subtasks from a parent requirement, those subtasks remain drafts until the AIPOS-52 publish preconditions pass and an approved writer path publishes them to the pending queue.

A combined planner/executor may execute a task directly only when the current task card, claim/session policy, and Owner decision gates authorize that execution. It must not use combined mode to bypass draft/publish, dispatch matching, task claim, session lease binding, reviewer separation, independent audit, or finalize gates.

## Forum Visibility Rule

Key steps must be visible through the forum/control-plane record or an equivalent visible workflow report:

- requirement received
- planner/executor mode assignment
- plan proposed
- Owner decision requested
- Owner decision recorded
- subtask draft created
- draft publish requested
- draft published or rejected
- executor claimed or began authorized task work
- validation result reported
- audit handoff prepared
- audit result recorded
- repair requested or completed
- finalize gate completed
- parent requirement completed, cancelled, blocked, or superseded

AIPOS-53 defines these visibility expectations as protocol. It does not implement a forum backend or event writer.

## Finalize Gate

Finalize remains an internal execution-authority checklist after audit PASS.

The finalize operator may commit and push only when:

- independent audit returned PASS
- required validation passes on the authoritative validation host
- `git status` and `git diff --name-only` are inspected on the authoritative git host
- forbidden path check passes
- staged files exactly match audited files
- documentation alignment is complete
- no Owner decision gate is pending
- no unreviewed material changes were added after audit

Finalize must not broaden scope, add unreviewed files, skip documentation alignment, or proceed before audit PASS.

## Non-Code Task Compatibility

AIPOS is not limited to code engineering tasks. Combined planner/executor mode applies to any task mode allowed by the selected role instance and task card.

Examples:

- documentation planning and documentation edits
- intelligence research and synthesis
- operations planning and operational content production
- sales support and customer-facing material production
- presentation, image, video, or article production

Non-code tasks still preserve the same governance boundaries: no self-audit when independent audit is required, Owner decision for critical forks, visible handoff, and task-scoped execution authority.

## Non-Goals

AIPOS-53 does not implement:

- planner loop runtime
- queue polling runtime
- subtask draft writer
- draft publish automation
- forum backend
- records event writer
- CLI command changes
- Web UI behavior changes
- database
- server
- deployment configuration
- new runtime services
