# Adapter Invocation Model

## Purpose

This document defines how a future local Board adapter should invoke backend capabilities.

## Preferred Invocation Order

The adapter should resolve backend execution in this order:

1. stable Python module function
2. structured compatibility wrapper inside the adapter
3. CLI subprocess JSON invocation as a fallback

The adapter should never treat human-readable terminal text as authoritative state.

## AIPOS-36 MVP Status

AIPOS-36 implements step 1 only:

- stable Python module function path is used for the MVP
- CLI fallback remains future-only
- execute mutations remain blocked by default even though dry-run mutation previews are implemented

## Module Boundary

When `module` mode is available, the adapter must:

- call stable Python functions only
- do not call `argparse` entrypoint for business logic
- do not mutate global state
- return structured objects
- raise typed or categorized exceptions where possible
- maintain same behavior as CLI

Preferred module contract shape:

```python
result = backend_service.queue_claim(
    task_id="EXAMPLE-001",
    path=None,
    actor="dev.codex.local",
    agent_instance="dev.codex.local.cc",
    runtime_profile="cc",
    dry_run=True,
    with_records=False,
)
```

Return expectations:

- structured dict-like result or dataclass-like object
- explicit planned and performed write lists
- explicit warnings and blocking reasons
- explicit actor match result when relevant

## CLI Subprocess Boundary

When `cli_subprocess` mode is used, the adapter must enforce:

- no shell=True
- no `shell=True`
- explicit argv list only
- repo root fixed
- timeout required
- capture stdout and stderr
- JSON mode required for authoritative operations
- human text output non-authoritative
- stderr stored as diagnostic only
- non-zero exit maps to `ADAPTER_INVOCATION_ERROR` or backend category if JSON error is available

Example invocation model:

```text
[
  "python3",
  "tools/aipos_cli/aipos_cli.py",
  "queue",
  "claim",
  "--task-id",
  "EXAMPLE-001",
  "--actor",
  "dev.codex.local",
  "--dry-run",
  "--json",
]
```

## Hybrid Transition Model

`hybrid` mode is the recommended transition path.

Rules:

- module path is tried first for endpoints with stable service functions
- CLI JSON fallback is allowed for endpoints not yet exposed as stable modules
- response envelope must be identical regardless of invocation path
- adapter logs or diagnostics may record which path was used, but the contract should not depend on it

## Request Translation

The adapter must translate request fields consistently:

- `task_id` and `path` are mutually exclusive selectors unless an endpoint explicitly supports both
- `actor`, `agent_instance`, and `runtime_profile` are passed through unchanged
- `with_records` is omitted unless true or explicitly required
- `dry_run` and owner-confirmation fields are always explicit for controlled mutations

## Backend Parity Rule

Module and CLI paths must preserve the same semantics:

- same task resolution rules
- same safety checks
- same dry-run behavior
- same owner confirmation gating
- same record collision behavior
- same write boundary

If parity cannot be maintained, the adapter must return `BACKEND_CONTRACT_MISMATCH` rather than silently executing a weaker path.
