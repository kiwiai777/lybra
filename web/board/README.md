# AIPOS Board Web UI (Local)

This directory provides the owner-facing Board UI for local development and owner-private dogfood.

## Scope

- local development and owner-private dogfood only
- default localhost binding
- private remote access only through Owner-approved SSH tunnel, private network, or Zero Trust boundary
- not production
- read integration with `tools.aipos_cli.board_adapter`
- controlled execute UI limited to AIPOS-38 `queue_claim`, AIPOS-56 `draft_create`, AIPOS-57 `draft_publish`, AIPOS-80 `orchestration_event_append`, and AIPOS-80 `planner_iteration_append`
- parent requirement entry limited to preview-only metadata rendering
- planner tick / event log entry limited to preview-only metadata rendering
- planner draft review limited to review-only publish readiness checks
- forum event persistence review limited to review-only writer readiness checks
- orchestration summary preview limited to read-only AIPOS-68 reconstruction output
- orchestration timeline view limited to read-only append-only event and iteration logs
- planner draft review desk limited to read-only Owner review of planner-created draft subtasks
- Owner decision gate limited to read-only review of decision requests
- approved planner draft publish limited to existing controlled `draft_publish` dry-run and confirm
- context pack preview limited to read-only AIPOS-78 builder output surfaced through the AIPOS-79 Board panel
- private dogfood deployment limited to AIPOS-84 owner-only access boundaries

## Run Locally

From repository root:

```bash
python3 web/board/app.py --host 127.0.0.1 --port 8765
```

Open:

- `http://127.0.0.1:8765/`

## Owner-Private Remote Dogfood

AIPOS-84 documents private remote dogfood without changing the default bind address.

The remote Board process should still bind to `127.0.0.1` and be reached through an Owner-approved private channel such as SSH local port forwarding:

```bash
ssh -N -L 8765:127.0.0.1:8765 owner@private-host
```

Deployment templates live under `config/deployment/` and are examples only. AIPOS-84 does not install or enable services.

## Available Endpoints

- `GET /api/health`
- `GET /api/queue`
- `GET /api/needs-owner`
- `GET /api/validate`
- `GET /api/agents`
- `GET /api/drafts`
- `GET /api/records` (optional summary endpoint)
- `GET /api/task?task_id=<TASK_ID>`
- `GET /api/task?path=<repo_relative_task_path>`
- `GET /api/preview?task_id=<TASK_ID>&actor=<ACTOR>`
- `GET /api/preview?path=<repo_relative_task_path>&actor=<ACTOR>`
- `GET /api/orchestration-summary?orchestration_id=<ORCHESTRATION_ID>`
- `GET /api/orchestration/summary?orchestration_id=<ORCHESTRATION_ID>` (read-only dogfood alias)
- `GET /api/orchestration-timeline?orchestration_id=<ORCHESTRATION_ID>`
- `GET /api/orchestration/timeline?orchestration_id=<ORCHESTRATION_ID>` (read-only dogfood alias)
- `GET /api/planner-loop/mvp?orchestration_id=<ORCHESTRATION_ID>&actor=<ACTOR>`
- `GET /api/context-pack/preview?task_id=<TASK_ID>`
- `GET /api/context-pack/preview?path=<repo_relative_task_path>`
- `GET /api/context-pack/preview?orchestration_id=<ORCHESTRATION_ID>`
- `GET /api/planner-drafts/review`
- `GET /api/owner-decisions/review`
- `POST /api/owner-decision/resolve/review`
- `POST /api/parent-requirement/preview`
- `POST /api/planner-tick/preview`
- `POST /api/planner-tick/manual-flow/preview`
- `POST /api/planner-draft/review`
- `POST /api/planner-draft/publish/dry-run`
- `POST /api/forum-event/review`
- `POST /api/execute/dry-run`
- `POST /api/execute/confirm`

Controlled execute POST routes are local-only and limited by the existing backend controlled execute contract. AIPOS-55 exposes `queue_claim`; AIPOS-56 adds `draft_create`; AIPOS-57 adds `draft_publish`; AIPOS-77 adds backend/API-gated `orchestration_event_append` and `planner_iteration_append`; AIPOS-80 exposes those two append operations through a visible controlled UI.

AIPOS-59 adds parent requirement preview. It is preview-only and does not create parent requirement records, orchestration files, forum events, task cards, dry-run tokens, or executable operations.

AIPOS-60 adds planner tick / event log preview. It is preview-only and does not write planner iterations, orchestration events, forum events, task cards, drafts, queue files, records, memory, dry-run tokens, or executable operations.

AIPOS-74 adds a preview-only manual planner tick flow backed by `/api/planner-tick/manual-flow/preview`. It aggregates the planner tick preview with orchestration summary, timeline, and Owner decision context, surfaces critical Owner forks, and returns no execute token or writer confirmation. It does not append planner iterations, append forum/orchestration events, mutate queues/drafts/records, launch a planner runtime, poll autonomously, post to a forum backend, or perform git operations.

AIPOS-61 adds planner draft review. It is review-only and checks planner draft metadata, Owner gates, reviewer/auditor separation, publish-target compatibility, and existing `draft_publish` dry-run compatibility. It does not create dry-run tokens, confirm publish, write drafts, write pending queue files, write forum events, or expand controlled execute.

AIPOS-63 adds forum event persistence review. It is review-only and checks future append-only `orchestration_events.md` persistence readiness. It does not create dry-run tokens, confirm writes, write orchestration files, post to a forum backend, or expand controlled execute.

AIPOS-69 adds orchestration summary preview. It is read-only and renders AIPOS-68 reconstruction output for status, current iteration, subtask counts, Owner attention, source refs, conflicts, and rebuild notes. It does not write summary state, create dry-run tokens, confirm writes, mutate queues, launch planner runtimes, or expand controlled execute.

AIPOS-70 adds orchestration timeline read UI. It reads append-only `planner_iterations.md` and `orchestration_events.md`, renders a chronological timeline, highlights Owner decision and blocking items, and preserves source refs. It does not add event writer UI, iteration writer UI, forum backend, planner runtime, autonomous polling, dry-run tokens, or controlled mutation.

AIPOS-71 adds a planner draft review desk. It reads planner-created drafts, surfaces assignment, reviewer, auditor, dependencies, Owner gates, missing metadata, and publish readiness for mobile-friendly Owner review. It does not add draft publish mutation, queue mutation, dry-run tokens, confirm buttons, planner runtime, or autonomous execution.

AIPOS-72 adds an Owner decision gate review desk. It reads needs-owner queue tasks and append-only orchestration timelines, classifies decision requests across architecture, scope, risk, security, model-tier, authority, audit-boundary, publish/finalize, and long-term direction categories, and renders mobile-friendly review cards. It does not resolve decisions, write Owner decisions, create dry-run tokens, confirm approvals, mutate queues or drafts, launch planner runtimes, or continue planning autonomously.

AIPOS-73 adds an approved planner draft publish UI. It requires planner draft review PASS, a clear Owner decision gate, existing `draft_publish` dry-run token/hash revalidation, actor match, and an explicit second confirmation before using `/api/execute/confirm`. It does not add generic orchestration writes, automatic publish, automatic claim, planner runtime launch, commit/push/finalize automation, or new backend writer primitives.

AIPOS-77 adds controlled backend/API persistence gates for existing append-only orchestration event and planner iteration writers. They require dry-run token, snapshot revalidation, actor match, explicit Owner confirmation, and writer-level expected-hash validation. It does not add visible persistence buttons, automatic planner runtime behavior, summary state writers, forum backend posting, or git automation.

AIPOS-78 adds a read-only Context Pack preview API. It can summarize a task or orchestration with context bundle, source refs, governance, and disabled capability flags. It returns no dry-run token, writes no files, calls no external RAG/search provider, and executes no agents.

AIPOS-79 adds the Context Pack Board Preview Panel. It is responsive for review, supports task id, task path, and orchestration id sources, renders safety flags and source refs, and remains read-only with no confirm button or dry-run token.

AIPOS-80 adds a visible Planner Loop Persistence panel for the two AIPOS-77 append-only controlled operations. It can load the latest manual planner tick preview's planner iteration or event payload into a JSON review box, run `/api/execute/dry-run`, and then require an explicit Owner confirmation checkbox before `/api/execute/confirm`. It does not launch a planner runtime, poll autonomously, run agents, publish drafts, claim tasks, write summary state, post to a forum backend, or automate git.

AIPOS-81 polishes the Planner Loop Persistence result flow. It adds a handoff card for dry-run and confirm results, clears stale dry-run state after successful confirm, and can manually refresh existing read-only orchestration timeline, orchestration summary, context pack preview, and planner loop control desk panels for the affected orchestration. It does not add new operations, writers, automation, runtime launch, polling, forum backend posting, or git automation.

AIPOS-82 adds phone-width Owner review path polish. It adds local page shortcuts for queue, needs-owner, decisions, timeline, planner drafts, records, and agents; improves mobile tap targets and sticky review navigation; and keeps raw JSON panels scrollable. It does not add backend routes, writers, new controlled execute operations, autonomous planner runtime, deployment, auth/RBAC, database, or git operations.

## Intentionally Absent Endpoints

Most mutation endpoints are intentionally absent.

Still intentionally absent after AIPOS-57:

- queue block, complete, reopen UI
- draft writer/publish mutation routes outside controlled execute
- parent requirement writer
- orchestration writer outside approved append-only controlled execute
- forum backend
- planner iteration writer outside approved append-only controlled execute
- orchestration event writer outside approved append-only controlled execute
- forum event persistence writer
- orchestration summary state writer
- planner draft writer
- planner draft publish automation outside controlled execute
- records write UI
- orchestration write UI
- planner runtime launch UI
- runtime launch UI
- git commit/push UI

## UI Behavior

The page renders:

- Header / System Status
- Task Queue Summary
- Needs Owner Summary
- Validation Summary
- Agents Summary
- Records Summary
- Drafts Summary
- Task Detail
- Task Preview
- Needs Owner Detail
- Validation Detail
- Records Detail
- Agents Detail
- Controlled Execute
- Draft Create
- Draft Publish
- Planner Draft Review
- Context Pack Preview
- Parent Requirement
- Planner Tick
- Planner Loop Persistence
- Forum Event Review
- Orchestration Summary Preview
- Orchestration Timeline
- Planner Draft Review Desk
- Owner Decision Gate
- Approved Planner Draft Publish
- Adapter Response Debug Panel

## Notes

- Mutation UI is limited to local controlled `queue_claim`, `draft_create`, `draft_publish`, `orchestration_event_append`, and `planner_iteration_append` dry-run and execute confirmation.
- Deployment configuration is limited to AIPOS-84 owner-private examples under `config/deployment/`.
- Default bind address is `127.0.0.1`.
- AIPOS-43 adds read-only Needs Owner detail and Validation detail panels.
- AIPOS-43 does not add mutation or execute UI.
- AIPOS-43 implementation changes are intentionally left uncommitted until audit/finalize approval.
- AIPOS-44 adds read-only interaction polish: panel refresh controls, loading/error/empty states, debug expand/collapse, actor input persistence, and clearer row selection.
- AIPOS-44 does not add mutation or execute UI.
- AIPOS-44 implementation changes are intentionally left uncommitted until audit/finalize approval.
- AIPOS-45 adds read-only Records Detail and Agents Detail panels using existing `/api/records` and `/api/agents`.
- Records Detail summarizes session records and claim logs when present, and shows an explicit empty state when no records exist.
- Agents Detail summarizes agent profiles and runtime instances; runtime command and args are displayed only as inert text.
- AIPOS-45 does not add mutation, execute, deployment, background polling, or runtime execution UI.
- AIPOS-45 implementation changes are intentionally left uncommitted until audit/finalize approval.
- AIPOS-55 adds local controlled execute confirmation for `queue_claim` only.
- AIPOS-55 requires a dry-run token, execute-time backend revalidation, matching actor, and optional Owner confirmation token when the backend requires Owner confirmation.
- AIPOS-55 does not add queue block/complete/reopen UI, draft create/publish UI, records writing, orchestration writing, background polling, runtime execution, deployment, auth/RBAC, or git operations.
- AIPOS-56 adds local controlled draft creation for `draft_create` only.
- AIPOS-56 requires a dry-run preview before creating a draft and displays rendered markdown plus planned writes.
- AIPOS-56 does not add draft publish UI, queue block/complete/reopen UI, records writing, orchestration writing, background polling, runtime execution, deployment, auth/RBAC, or git operations.
- AIPOS-57 adds local controlled draft publishing for `draft_publish` only.
- AIPOS-57 requires a dry-run preview before publishing a draft and displays target path, rendered markdown, planned writes, warnings, and blocking reasons.
- AIPOS-57 does not add queue block/complete/reopen UI, records writing, orchestration writing, background polling, runtime execution, deployment, auth/RBAC, or git operations.
- AIPOS-59 adds local parent requirement preview for Planner loop UI entry.
- AIPOS-59 requires title, Owner goal, forum thread reference, and L3/L4 planner tier; it displays requirement and planner-loop metadata without writing files.
- AIPOS-59 does not add parent requirement writer, orchestration writer, forum backend, planner runtime launch, queue polling, background execution, records writing, deployment, auth/RBAC, database, or git operations.
- AIPOS-60 adds local planner tick and event log preview for forum-visible Planner loop reporting.
- AIPOS-60 requires orchestration id, parent task id, forum thread reference, L3/L4 planner tier, planner verdict, decision reason, and next expected action; it displays planner iteration, visible report, and event log preview metadata without writing files.
- AIPOS-60 does not add planner iteration writer, orchestration event writer, forum backend, planner runtime launch, queue polling, autonomous execution, controlled execute expansion, records writing, deployment, auth/RBAC, database, or git operations.
- AIPOS-61 adds local planner draft review for AIPOS-52 publish readiness.
- AIPOS-61 requires a draft path under `5_tasks/drafts/`, displays planner draft preconditions and a review-only handoff into the existing Draft Publish dry-run flow when eligible.
- AIPOS-61 does not add a planner draft writer, publish automation outside controlled execute, queue block/complete/reopen UI, records writing, orchestration writing, forum backend, planner runtime launch, queue polling, autonomous execution, deployment, auth/RBAC, database, or git operations.
- AIPOS-63 adds local forum event persistence review for future append-only orchestration event writer readiness.
- AIPOS-63 requires orchestration id, allowed event type, allowed severity, actor, source, forum thread reference, and summary; it displays a future append plan and precondition checks without writing files.
- AIPOS-63 does not add a forum event writer, orchestration writer, forum backend, network posting, controlled execute expansion, planner runtime launch, queue polling, autonomous execution, deployment, auth/RBAC, database, or git operations.
- AIPOS-69 adds a read-only orchestration summary preview panel backed by `/api/orchestration-summary`.
- AIPOS-69 renders status, current iteration, subtask counts, Owner attention, source refs, conflicts, and rebuild notes with safe text rendering and responsive review cards.
- AIPOS-69 does not add summary state writing, execute tokens, queue mutation, planner runtime launch, autonomous polling, deployment, auth/RBAC, database, or git operations.
- AIPOS-70 adds a read-only orchestration timeline panel backed by `/api/orchestration-timeline`.
- AIPOS-70 timeline entries are rendered with safe text rendering and responsive cards for phone-width review.
- AIPOS-70 does not add event writing, iteration writing, forum backend, planner runtime launch, autonomous polling, controlled mutation, deployment, auth/RBAC, database, or git operations.

- AIPOS-71 adds a read-only planner draft review desk backed by `/api/planner-drafts/review`.
- AIPOS-71 review cards are responsive for phone-width review and keep publish/execute disabled in the new desk.
- AIPOS-71 does not add draft publish mutation, queue mutation, controlled execute expansion, planner runtime, autonomous polling, deployment, auth/RBAC, database, or git operations.

- AIPOS-72 adds a read-only Owner decision gate desk backed by `/api/owner-decisions/review`.
- AIPOS-72 decision cards are responsive for phone-width review and keep decision resolution disabled.
- AIPOS-72 does not add Owner decision writing, approval mutation, queue/draft mutation, planner runtime continuation, autonomous polling, deployment, auth/RBAC, database, or git operations.

- AIPOS-73 adds a controlled approved planner draft publish panel backed by `/api/planner-draft/publish/dry-run` and existing `/api/execute/confirm`.
- AIPOS-73 requires explicit second confirmation and keeps ambiguous Owner-gated planner drafts blocked.
- AIPOS-73 does not add generic orchestration writes, automatic publish, automatic claim, planner runtime, commit/push/finalize automation, deployment, auth/RBAC, database, or git operations.

- AIPOS-74 adds a preview-only manual planner tick flow panel backed by `/api/planner-tick/manual-flow/preview`.
- AIPOS-74 is mobile-responsive for review, stops on critical Owner forks, and keeps append writers, queue mutation, planner runtime launch, autonomous polling, forum backend, and git operations disabled.

- AIPOS-75 adds a semi-automated Planner Loop Control Desk backed by `/api/planner-loop/mvp` and `aipos_cli.py orchestration loop preview`.
- AIPOS-75 coordinates a single safe next step across summary, timeline, Owner gates, planner drafts, and existing controlled publish handoff. It returns no execute token and does not write, launch runtimes, poll, run agents, auto-publish, auto-claim, self-audit, commit, push, or finalize.

- AIPOS-76 adds an Owner Decision Resolution Review panel backed by `/api/owner-decision/resolve/review`.
- AIPOS-76 validates Owner decision evidence and previews an `owner_decision_recorded` append plan, but returns no execute token and does not persist the decision, append events, post to a forum backend, continue planner runtime, or mutate queues/drafts/records.

- AIPOS-77 adds backend/API controlled execute support for `orchestration_event_append` and `planner_iteration_append`.
- AIPOS-77 requires dry-run token, actor match, revalidation, explicit Owner confirmation, and writer expected-hash validation before append-only persistence.
- AIPOS-77 does not add visible Web UI persistence buttons, planner runtime launch, automatic polling, automatic agent execution, automatic publish/claim, summary state writers, forum backend posting, database/deployment/auth changes, or git operations.

- AIPOS-78 adds a read-only Context Pack preview route backed by `/api/context-pack/preview` and `aipos_cli.py context-pack preview`.
- AIPOS-78 does not add context pack writers, task mutation, queue movement, records writing, orchestration appends, external RAG calls, Cortex replacement behavior, agent execution, or git operations.

- AIPOS-79 adds a read-only Context Pack Preview panel backed by `/api/context-pack/preview`.
- AIPOS-79 renders pack identity, task/bundle/orchestration refs, disabled capability flags, warnings, and Owner-gate reasons using safe text rendering and responsive cards.
- AIPOS-79 does not add context pack writers, controlled mutation, dry-run tokens, confirm buttons, external RAG/search calls, Cortex replacement behavior, agent execution, deployment/auth/database changes, or git operations.
- AIPOS-80 adds a controlled Planner Loop Persistence panel for `orchestration_event_append` and `planner_iteration_append`.
- AIPOS-80 requires a dry-run token, snapshot revalidation, actor match, explicit Owner confirmation checkbox, and existing writer-level expected-hash validation before append-only persistence.
- AIPOS-80 does not add planner runtime launch, autonomous polling, agent execution, automatic publish, automatic claim, summary state writers, forum backend posting, deployment/auth/database changes, or git operations.
- AIPOS-81 adds Planner Loop Persistence result and handoff polish.
- AIPOS-81 shows planned/performed writes and handoff refresh targets, and refreshes only existing read-only panels.
- AIPOS-81 does not add new controlled execute operations, backend writer primitives, planner runtime launch, autonomous polling, agent execution, automatic publish/claim, summary state writers, forum backend posting, deployment/auth/database changes, or git operations.
- AIPOS-82 adds Mobile Owner Review Path Polish.
- AIPOS-82 adds Owner review shortcuts and phone-width tap target/readability improvements for review panels.
- AIPOS-82 does not implement the deferred Planner Loop Persistence Handoff to Owner Decision Resolution candidate, add backend routes, add writers, expand controlled execute, launch runtimes, poll autonomously, or add deployment/auth/database/git operations.
- AIPOS-87 adds read-only dogfood aliases `/api/orchestration/summary` and `/api/orchestration/timeline`, plus health readiness metadata for the AIPOS-86 first remote dogfood checklist.
- AIPOS-87 does not add live remote agent connection, credentials, deployment changes, MCP, public endpoints, queue mutation, writers, controlled execute expansion, planner runtime launch, polling, database/auth changes, or git operations.
