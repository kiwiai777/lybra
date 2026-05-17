# Codex Primary CWD Cutover Policy

## Purpose

AIPOS-98D defines when Codex should treat the Lybra product repository as the primary working directory while continuing to read and write private workspace data from the Owner workspace.

This policy prevents the product repository and private workspace from becoming competing sources of truth.

## Canonical Roots After Cutover

```text
product repo root:      /path/to/lybra
private workspace root: /path/to/private-workspace
project management:     /path/to/private-workspace/2_projects/lybra
```

Concrete Owner-local paths should be recorded in private project management docs, not in the public product repo.

The product repo is the canonical home for reusable Lybra code, generic protocols, public docs, examples, config examples, and product tests.

The private workspace remains the canonical home for Owner project management data, live tasks, drafts, records, orchestration logs, private context packs, private runtime state, and private reports.

## Cutover Preconditions

Codex may switch its primary cwd to `/path/to/lybra` only after all of the following are true:

1. The local product repo exists at `/path/to/lybra`.
2. The public remote `kiwiai777/lybra` has the sanitized initial product commit.
3. `AIPOS_WORKSPACE_ROOT=/path/to/private-workspace` works from the product repo for CLI validation, queue, agents, and records commands.
4. Board server startup can resolve the private workspace with either `AIPOS_WORKSPACE_ROOT` or `--repo-root`.
5. `task_cards/` in the product repo is ignored and remains a local-only mirror.
6. No private workspace directories are present in the product repo root.
7. No Owner host labels, home paths, private project names, secrets, `.DS_Store`, AppleDouble files, or backup files are staged for the product repo.
8. The private workspace remains intact and continues to own `2_projects/lybra`.
9. Owner explicitly approves the working-directory cutover.

## Default Command Pattern After Cutover

After cutover, product implementation commands should run from `/path/to/lybra`:

```bash
cd /path/to/lybra
export AIPOS_WORKSPACE_ROOT=/path/to/private-workspace
python3 -m unittest discover -s tools/aipos_cli/tests
python3 -m unittest discover -s web/board/tests
python3 tools/aipos_cli/aipos_cli.py validate --json
```

When running the Board server:

```bash
cd /path/to/lybra
AIPOS_WORKSPACE_ROOT=/path/to/private-workspace python3 web/board/app.py
```

or:

```bash
cd /path/to/lybra
python3 web/board/app.py --repo-root /path/to/private-workspace
```

## Operation Routing

Product repo operations:

- edit reusable CLI/backend code
- edit reusable Web/Board code
- edit generic Control Plane protocols
- edit generic docs, config examples, and sample workspace
- run product tests
- commit and push Lybra product changes to `kiwiai777/lybra`

Private workspace operations:

- edit `2_projects/lybra/project_status.md`
- edit `2_projects/lybra/roadmap.md`
- edit `2_projects/lybra/decision_log.md`
- edit task cards and audit handoffs
- edit live `5_tasks/` queues, drafts, records, and orchestration logs
- store dogfood reports and private operational notes
- preserve Owner-specific agent/runtime/deployment state

## Cross-Repo Change Discipline

Some future work may require coordinated changes in both repositories. In that case:

- keep product changes in `/path/to/lybra`
- keep project-management changes in `/path/to/private-workspace`
- report both git statuses separately
- commit each repository separately
- never push either repository without explicit Owner approval when the action is externally visible
- never copy private workspace data into the product repo to make a test pass

## Compatibility Shim Boundary

`AIPOS_WORKSPACE_ROOT` is a compatibility shim for reading workspace data from a separate root. It does not grant write authority, queue authority, records authority, deployment authority, credentials, audit approval, Owner approval, or git authority.

Explicit `repo_root` / `--repo-root` behavior remains valid. The legacy same-root mode remains valid for sample workspaces and transitional use.

## Rollback

If cutover causes confusion or validation failure:

1. Stop using `/path/to/lybra` as the primary cwd.
2. Return Codex primary cwd to `/path/to/private-workspace`.
3. Keep `/path/to/lybra` as an extracted product repo for inspection.
4. Do not delete the private workspace.
5. If needed, reset or amend only product-repo changes that are known to be part of the failed cutover attempt.
6. Record the rollback reason in the private workspace project management docs.

## Non-Goals

AIPOS-98D does not:

- rename `/path/to/private-workspace`
- move `2_projects/lybra` into the product repo
- move live tasks, drafts, records, orchestration logs, or private context packs into the product repo
- enable MCP server implementation
- enable sandbox runtime implementation
- enable autonomous planner runtime
- add controlled execute operations
- add backend routes, writers, queue mutation, records writing, auth/RBAC, database, deployment, public endpoints, or credentials
- change AIPOS-89 dogfood scope
- change AIPOS-96 MCP protocol scope
- change AIPOS-90 sandbox provider order
