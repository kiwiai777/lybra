# Role Instance and Task Mode Policy

## Purpose

This policy defines the difference between a **role instance**, a **primary role**, an **allowed capability set**, and a **task mode** in AI Project OS Control Plane v0.1.

The goal is to prevent role instances from being accidentally frozen into narrow, permanent jobs. Role instances are flexible execution entities that can take on different task modes across different tasks.

## Core Rule

**A role instance is not a fixed permanent job.**

- `primary_role` is the default orientation, not a hard limit.
- `can_act_as` defines the allowed capability boundary.
- `task_mode` is selected by each **task card**.
- `task_class` independently selects simple or complex workflow governance.
- The same agent may perform different modes across different tasks.
- **Context Bundle** provides startup context and boundaries, not a fixed job description.

Task cards must specify the current task mode when role instance is flexible.

## Combined Planner/Executor Compatibility

AIPOS-53 allows a flexible role instance to combine planner and executor work for a scoped task or parent requirement when the task card, role registry, capability profile, model tier, and Owner policy allow it.

This is not limited to coding. The selected `task_mode` determines whether the execution work is code, documentation, research, operations, content, sales support, presentation production, image production, video production, or another approved mode.

Combined planner/executor mode does not collapse governance authorities. The same instance may plan and execute, but it must not self-audit, act as Owner decider, or pass critical forks without Owner approval.

## Definitions

### Role Instance

A role instance is a specific, named execution entity tied to an actor and environment.

Examples:
- `dev.codex.local` is a local engineering role instance.
- `dev.claude_code.local` is a local engineering role instance.
- `ops.openclaw.local` is a local OpenClaw execution role instance.
- `biz.openclaw.cloud` is a cloud OpenClaw role instance.
- `info.hermes.cloud` is a cloud Hermes role instance.

A role instance is **not** permanently assigned as a single narrow job (e.g., "auditor only" or "coder only").

### Primary Role

`primary_role` is the default main responsibility of a role instance.

It does **not** hard-limit what the instance can do in any single task.

### Can Act As

`can_act_as` is the allowed capability set.

It defines the boundary of what modes a role instance may take on.

### Task Mode

`task_mode` is the specific operational mode selected by a task card for a particular task.

`task_mode` does not select closed-loop rigor. `task_class: simple | complex` is orthogonal, defaults to effective `simple` when omitted, and controls whether the full planner, independent audit, repair/re-audit, and PASS-before-finalize loop is required.

For flexible roles, the task card must explicitly state the mode used, such as:
- `Task Mode Used: documentation_syncer`
- `Task Mode Used: coder`
- `Task Mode Used: reviewer`
- `Task Mode Used: auditor`

If the required task mode is not in the role instance's `can_act_as`, the agent should stop and ask the Owner whether to extend the registry.

### Task-Card-Selected Role

The task card selects a specific **role instance** (e.g., `dev.codex.local`) to execute a task.

The task card also selects a **task mode** from the role's `can_act_as` for that specific task.

### Context Bundle

A Context Bundle is a startup context package.

It includes role identity, environment, required files, memory access rules, task boundaries, and completion expectations.

A Context Bundle is **not** a permanent job assignment.

## Configurability and Evolution

Role instances are configurable and evolvable.

The Owner may:
- Add new role instances.
- Deactivate existing role instances (preferred: `status: inactive`).
- Rename display names.
- Change primary roles.
- Expand `can_act_as`.
- Reduce `can_act_as`.
- Move a role instance between environments.
- Split one role instance into multiple role instances.
- Merge responsibilities when appropriate.
- Introduce new agents.
- Retire old agents.

The preferred way to retire a role is `status: inactive`.

Hard deletion should be rare and Owner-approved.

## Policy

### 1. Role Instances Are Not Fixed Jobs

A role instance is a flexible execution entity, not a permanent job slot.

**Dev_codex is not permanently assigned as auditor.**
**Dev_cc is not permanently assigned as coder.**
**Con龙虾 is not limited to configuration management.**

Cloud roles may also switch task modes within their capability boundaries.

### 2. Task Mode Is Selected Per Task Card

For each task, the task card must explicitly specify the task mode for flexible roles.

Example:
```
Task Mode Used: documentation_syncer
```

This applies to all flexible roles: Dev_codex, Dev_cc, Con龙虾, Biz龙虾, Info爱马仕.

### 3. Role Registry Is Configurable Data

The role registry is not a hardcoded organization structure.

It is data that can be updated by the Owner:
- New roles can be added.
- Existing roles can be adjusted.
- Roles can be split or merged.
- Roles can be moved between environments.

### 4. Context Bundle Is Startup Context

A Context Bundle provides startup information and boundaries.

It does **not** assign a fixed job description.

For flexible roles, the same Context Bundle may be reused across coder, reviewer, auditor, tester, refactorer, content, operation, research, or sales tasks.

### 5. This Task Mode Policy Does Not Freeze Future Design

This policy prevents role freezing.

It does **not** freeze:
- Future role additions.
- Future capability expansions.
- Future organizational changes.
- Future tool or workflow integrations.

## Examples

### Dev_codex

Dev_codex is a local engineering role instance.

It is **not permanently assigned as auditor**.

It may act as **coder, reviewer, auditor, tester, refactorer, or documentation updater** depending on the task card.

### Dev_cc

Dev_cc is a local engineering role instance.

It is **not permanently assigned as coder**.

It may act as **coder, reviewer, auditor, refactorer, tester, or documentation updater** depending on the task card.

### Con龙虾

Con龙虾 is a local OpenClaw execution role instance.

It is **not limited to configuration management**.

It may act as **system operator, configuration operator, documentation syncer, agent upgrade operator, model quota monitor, non-code content operator, article operator, PPT operator, image operator, video operator, material production operator, or operational content producer** depending on the task card.

### Cloud OpenClaw / Biz龙虾

Biz龙虾 is a cloud OpenClaw role instance.

It is primarily used for **operations and sales implementation**, but actual task mode is selected by the task card.

It may support **operations, sales execution, campaign support, customer-facing content, and business implementation tasks** within Owner-approved boundaries.

### Cloud Hermes / Info爱马仕

Info爱马仕 is a cloud Hermes role instance.

It is primarily used for **intelligence, research, presales, aftersales, and long-running analysis**, but actual task mode is selected by the task card.

It may support **data intelligence, competitive research, customer research, presales analysis, aftersales analysis, and long-term monitoring tasks** within Owner-approved boundaries.

## Task Card Requirements

Task cards for flexible roles must include:
- Executor:
- Role Instance:
- Task Mode Used:
- Environment:
- Availability:
- Requires Resume:

For flexible roles, task cards should explicitly state the selected task mode, such as:
- Task Mode Used: documentation_syncer
- Task Mode Used: coder
- Task Mode Used: reviewer
- Task Mode Used: auditor
- Task Mode Used: article_operator
- Task Mode Used: ppt_operator
- Task Mode Used: intelligence_researcher
- Task Mode Used: sales_operator

The task card selects a task mode from the role's `can_act_as`.

If the needed task mode is not in `can_act_as`, the agent should stop and ask the Owner whether to extend the registry.

## Context Bundle Requirements

A Context Bundle should include:
- Role instance ID and display name.
- Environment.
- `task_mode: set_by_task_card` (or equivalent wording).
- Required files.
- Memory access rules.
- Task boundaries.
- Completion expectations.

Future bundle files should avoid titles like:
- Dev_codex Audit Bundle
- Con龙虾 Config Manager Bundle

unless the bundle is explicitly task-specific.

Preferred naming should be role-instance based:
- dev.codex.local.md
- dev.claude_code.local.md
- ops.openclaw.local.md
- cloud.hermes.info.md

Task-specific bundles may exist later, but they must be labeled clearly as task-specific.

## Forbidden Patterns

The following patterns are explicitly forbidden:
- Dev_codex = auditor only
- Dev_cc = coder only
- Con龙虾 = configuration only
- Biz龙虾 = sales only
- Info爱马仕 = intelligence only
- Context Bundle = permanent job description
- Primary Role = hard limit
- Current registry = permanent organization structure
- Temporary report = formal memory by default

## Completion / Reporting Requirements

Completion Reports should record the actual task mode used.

For flexible roles, the task card must specify the mode, and the completion report should confirm it.

Temporary completion/review reports do not enter Git by default, per the Reporting Policy.
