# Board v0 Design

## Purpose

Board v0 is the first visual / console-facing task board for AI Project OS.

It provides a file-driven control console over the existing repository structure used for tasks, reports, shared memory, and context bundles.

## Positioning

Board v0 is a:

- file-driven control console
- visual / console layer over AI Project OS queue, inbox, shared memory, and context bundles

Board v0 is not a:

- database-backed app
- chat app
- agent runtime
- RAG interface

## Source of Truth

Board v0 reads repository files as the source of truth.

It does not introduce a separate database or hidden state layer.

Operational state comes from:

- queue directory location
- task frontmatter
- report files and inbox entries
- shared memory updates

## Core Capabilities

Board v0 only performs:

- read
- render
- create
- move
- annotate
- link

It should not execute tasks directly.

## Scope

Board v0 should support:

- Task Queue
- My Tasks
- Activity Feed
- Needs Owner
- forum-style task creation
- selector-based task publishing
- file-to-view mapping
- local agent manual trigger path

## Non-Goals

Board v0 does not implement:

- database
- auth / permission system
- real-time chat
- RAG search
- complex notification system
- automatic recurring scheduler
- production web app
- agent execution runtime

## State Model

Board v0 visualizes formal queue state stored in:

- `5_tasks/queue/pending/`
- `5_tasks/queue/claimed/`
- `5_tasks/queue/completed/`
- `5_tasks/queue/blocked/`

Tasks are assigned by `assigned_to` and may optionally prefer a concrete `agent_instance`.

Task mode is selected per task card, not permanently locked to a role.

Model tier is selected per task, not permanently locked to a role.

## Reporting Boundary

Board v0 may link to reports, inbox items, and promoted memory.

Temporary completion and review reports do not enter Git by default unless Owner explicitly promotes them.

Board v0 therefore treats reports as references and review surfaces, not necessarily as permanent repository artifacts.

## Forum / Console Role

Board v0 acts like a forum-style task console:

- a planner writes a post-like task draft
- selectors turn it into a valid task object
- the task is published into the pending queue
- agents poll and claim work
- reports return to the forum / console or inbox

This keeps planning and execution connected without turning the board into a chat system or runtime engine.

## Future Extension Path

Board v0 should remain compatible with future enhancements such as:

- static or semi-static web rendering
- CLI dashboard rendering
- board filters and search
- recurring run generation helpers
- local run hooks

These future extensions should continue to read the existing file structure rather than requiring a database migration.
