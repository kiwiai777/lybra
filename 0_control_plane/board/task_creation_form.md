# Task Creation Form

## Purpose

Board v0 should offer a forum-style task creation flow that feels like creating a post, then converts that post into a standard Markdown task card.

## Form Model

The planner starts with a post-like draft:

- title
- body
- expected output
- constraints
- notes

Then the board applies structured selectors and defaults to produce a dispatchable task object.

Publishing writes the task into `5_tasks/queue/pending/`.

## Form Fields

The form should include:

- Title
- Body / Task Description
- Project
- Task Type
- Assignee
- Agent Instance
- Context Bundle
- Task Mode
- Model Tier
- Priority
- Schedule Hint
- Recurrence
- Due At
- Expires At
- Needs Owner
- Output Target
- Artifact Policy
- Artifact Links
- Memory References
- Expected Output
- Constraints
- Notes

## Required Selectors

Required selectors for creating a dispatchable task:

- `task_id`
- `title`
- `assigned_to`
- `context_bundle`
- `task_mode`
- `task_class`
- `complexity_note`
- `priority`
- `status`
- `created_by`
- `needs_owner`
- `output_target`
- `artifact_policy`

Required or defaultable fields:

- `task_type`: default `one_shot`
- `polling_mode`: default `agent_polling`
- `claim_policy`: default `assigned_agent_only`
- `report_mode`: default `forum_reply`
- `recurrence`: default `none`

Board UI should request project, but schema allows omission for cross-project or random work. It should expose `task_class: simple | complex`, default missing class to effective `simple`, and allow an optional advisory `complexity_note`.

## Optional Selectors

Optional selectors:

- `agent_instance`
- `model_tier`
- `orchestration`
- `planner_agent`
- `planner_model_tier`
- `planner_assignment_scope`
- `project`
- `schedule_hint`
- `due_at`
- `expires_at`
- `artifact_links`
- `memory_refs`
- `depends_on`
- `risk_level`
- `approval_required`
- `owner_review_required`
- `write_target`
- `report_target`

Clarifications:

- `agent_instance` is optional in schema but recommended for remote 24h agents.
- `model_tier` may default from context bundle but should be explicit for risky tasks.
- `schedule_hint` and `recurrence` are protocol fields, not automation implementation.
- `artifact_links` should link to real artifacts outside AI Project OS.
- `memory_refs` should point to formal memory entry points or context bundles.

## Optional Planner Selector

Planner is task-scoped and optional. Normal tasks do not require planner fields, and normal task creation should not ask for planner by default.

The planner selector should be hidden or collapsed for ordinary task creation. It should be shown when:

- `orchestration.enabled == true`
- `task_type == orchestration_parent`
- Owner explicitly chooses an orchestrated parent task template

Recommended planner selector fields:

```yaml
planner_agent:
planner_model_tier:
planner_assignment_scope:
```

Planner selector rules:

- ordinary task: hide or collapse planner fields
- orchestrated parent task: show planner fields
- `planner_required: true`: require `planner_agent` and `planner_model_tier`
- `planner_required: false`: allow no planner
- `planner_model_tier` should enforce L3/L4 for planning decisions
- normal tasks do not require planner_agent

Missing orchestration is equivalent to orchestration disabled.

## Forum-style Posting Flow

Recommended flow:

1. Planner creates a post-like task draft.
2. Board asks for required selectors.
3. Board offers recommended selectors such as `project`, `agent_instance`, `model_tier`, and `schedule_hint`.
4. Board applies defaults for omitted defaultable fields.
5. Board renders the final Markdown task card preview.
6. Planner publishes the task to `5_tasks/queue/pending/`.

For orchestrated parent tasks, the Board may show planner selectors before preview, but this is future UI behavior. AIPOS-51 additionally allows Owner to create a fuzzy parent requirement and assign an L3 planner such as `dev_codex` or `dev_claude`. Current AIPOS docs do not implement a Board UI or automatic planner assignment.

## Output Shape

The created task should be a Markdown file with:

- YAML frontmatter matching `5_tasks/task_schema.md`
- Markdown body sections such as task description, expected output, constraints, and notes

## Validation Rules

Before publish, Board v0 should check:

- required selectors present
- frontmatter values compatible with task schema
- `assigned_to` and `agent_instance` are not contradictory
- `status` is `pending` on creation
- `output_target` is present
- `context_bundle` is present
- missing planner fields only when `orchestration.enabled == true` and `planner_required == true`
- planner_model_tier is L3/L4 when planner planning decisions are required
- orchestrated parent requirements include a forum_thread_ref or visible control-plane equivalent

If any required selector is missing, the task should not publish as a dispatchable task and should surface in Needs Owner or draft review.
