# Backend Implementation Readiness

## Purpose

This document defines what is ready to implement next in the AI Project OS backend.

AIPOS-28 is a cutline task. It does not implement writer code.

## Completed Foundation

The following foundation is already in place:

- read-only queue renderer
- my-tasks view
- needs-owner view
- validator `--json`
- task detail renderer
- preview renderer
- records reader
- agent runtime profiles
- alias matching
- availability visibility
- records regression tests
- validator records summary
- planner/orchestration protocol docs
- orchestration state/index/log schemas

## Ready To Implement Now

The next implementation-ready areas are:

- safe draft writer
- draft validation
- publish draft to pending
- task_id generation and collision prevention
- file path safety
- dry-run output
- JSON output
- owner-reviewable diff output

## Not Ready Yet

The following remain later work:

- claim/block/complete mutation
- session/claim record writer
- orchestration record writer
- planner runtime
- scheduler
- quota polling
- runtime status provider fetching
- web UI
- database
- multi-agent auto-execution

## Next Implementation Sequence

Recommended next sequence:

```text
AIPOS-29: Safe Task Draft Writer CLI MVP
AIPOS-30: Draft Validation and Publish-to-Pending CLI
AIPOS-31: Queue Mutation CLI for Claim/Block/Complete
AIPOS-32: Session and Claim Record Writer
```

The cutline is explicit: current protocol coverage is sufficient, and backend implementation should resume now.
