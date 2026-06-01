# AI-Assisted Task Authoring Prompt Template

template_id: ai-assisted-task-authoring
template_version: 1

## Boundary

Convert one natural-language requirement into one structured Lybra task-card proposal.

The proposal is advisory. It must remain reviewable by the Owner and must pass deterministic validation before any file write.

Do not request credentials. Do not bypass Owner review. Do not claim authority. Do not publish, execute, retry, or mutate any state.

## Required Proposal Shape

- Standard Lybra draft frontmatter.
- Task-card body with goal, context, acceptance criteria, and completion-report instructions.
- Triage recommendation with `recommended_task_class`, rationale, confidence, assumptions, missing information, and possible Owner gates.
- Assignment recommendations grounded only in supplied candidates.
