# Orchestration Backend Readiness Cutline

## Purpose

This file defines the cutline after AIPOS-27 and is consolidated by AIPOS-28.

AIPOS design/protocol expansion is sufficient for the next backend implementation phase. Do not keep adding orchestration protocol docs before building a safe writer MVP unless a real blocker is found.

After AIPOS-28, the next task should be Safe Task Draft Writer CLI MVP.

## Ready For Implementation

The following are ready for backend implementation planning:

- read-only CLI already exists
- safe task draft writer can be implemented next
- draft validation can be implemented next
- draft publish to pending can be implemented next
- controlled queue mutation can be implemented after writer
- session and claim record writer can be implemented after mutation policy
- orchestration record writer can come later

Planner runtime is explicitly not next.

## Still Protocol-Only

The following remain protocol-only:

- planner autonomous loop
- quota polling
- runtime status provider fetching
- web UI
- scheduler
- automatic handoff
- multi-agent auto-execution

## Recommended Next Sequence

Recommended next implementation sequence:

```text
AIPOS-28: Backend Implementation Readiness Cutline and Safe Writer Scope
AIPOS-29: Safe Task Draft Writer CLI MVP
AIPOS-30: Draft Validation and Publish-to-Pending CLI
AIPOS-31: Queue Mutation CLI for Claim/Block/Complete
AIPOS-32: Session and Claim Record Writer
```

If Owner decides the cutline is already sufficient, AIPOS-28 may be reduced to a short readiness/finalize task or skipped.

Do not add more orchestration protocol tasks unless a concrete implementation blocker appears.

In short: do not add more orchestration protocol expansion before safe writer implementation unless a concrete blocker is found.
