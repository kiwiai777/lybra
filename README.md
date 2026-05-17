# Lybra

Lybra is a local-first, file-authoritative agent workbench extracted from the AI Project OS control-plane work.

The product repository contains reusable code, generic protocols, example configuration, tests, and a sample workspace. Private project data should live in a separate workspace repository or directory.

## Repository Layout

```text
tools/                 CLI and backend adapter code
web/                   Local dashboard / Board UI
0_control_plane/       Generic protocol and governance documents
3_context_bundles/     Generic role/context bundle templates and schemas
docs/                  Product, extraction, and deployment documentation
config/                Example configuration
examples/              Non-private sample workspace data
```

## Workspace Root

Lybra reads task, record, orchestration, and project data from a workspace root. The product repo itself does not need to contain private workspace data.

Set `AIPOS_WORKSPACE_ROOT` when running from the product repo against a separate workspace:

```bash
export AIPOS_WORKSPACE_ROOT=/path/to/private/workspace
python3 tools/aipos_cli/aipos_cli.py validate --json
python3 web/board/app.py
```

The Board server also accepts an explicit workspace path:

```bash
python3 web/board/app.py --repo-root /path/to/private/workspace
```

Without `AIPOS_WORKSPACE_ROOT` or `--repo-root`, CLI commands preserve the legacy behavior of searching upward from the current directory for `5_tasks/queue`.

## Public Repo Boundary

Do not commit private workspace data, secrets, local task cards, generated caches, or operating-system metadata. Local task-card mirrors may exist under `task_cards/`, but that directory is ignored by Git.
