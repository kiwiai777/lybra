# Lybra Standalone Repository Extraction Plan

## Purpose

AIPOS-83 defines the minimal extraction plan required before private remote production dogfood.

This is not full SaaS productization. It does not introduce multi-tenancy, public signup, billing, hosted onboarding, marketplace behavior, auth/RBAC, database migration, or public cloud execution surfaces.

## Naming Boundary

The future product/core repository name is `lybra`.

The Owner private workspace root may remain `~/ai-project-os` during the private deployment transition. AIPOS-84P performs the project-scoped management directory cutover from `2_projects/ai-project-os` to `2_projects/lybra` before private remote production deployment, avoiding two long-term project identities.

The three names must stay distinct:

```text
workspace root name: ~/ai-project-os
project management directory: 2_projects/lybra
product repo root: ~/lybra
```

`AIPOS` may remain the architecture/history name. `Lybra` is the future product name and standalone repo label.

## Target Layout

```text
~/lybra/
  tools/
  web/
  0_control_plane/
  docs/
  examples/
  sample_workspace/
  config/
  README.md

~/ai-project-os/
  2_projects/lybra/
  5_tasks/
  0_control_plane/
  private records
  private orchestration logs
  private context packs
```

## Product Repository Contents

The Lybra product repository should contain:

- `tools/aipos_cli/`
- `web/board/`
- generic adapter and API layers
- generic schemas
- generic protocol documentation
- generic control-plane templates
- docs for install, run, extraction, private deployment, and development
- `config/aipos.example.yaml`
- `examples/sample_workspace/`
- tests

The product repository must not contain Owner private project data, private paths, private agent endpoints, real task queue data, real records, real orchestration logs, private context packs, or private deployment secrets.

## Private Workspace Contents

The Owner private workspace keeps:

- `2_projects/lybra/`
- `5_tasks/queue/`
- `5_tasks/drafts/`
- `5_tasks/records/`
- `5_tasks/orchestration/`
- real project status, roadmap, decision log, and stage archives
- real records and claim/session data
- real context packs
- workspace-specific control-plane config
- private agent runtime state
- private workflow paths

`2_projects/lybra/` is the current canonical project docs path after AIPOS-84P. `2_projects/ai-project-os/` is the legacy path and must not remain a competing source of truth.

## Minimal Extraction Sequence

1. Define product/workspace boundaries in docs.
2. Add sample workspace and example config.
3. Keep current mixed repo behavior compatible.
4. Add or plan `AIPOS_WORKSPACE_ROOT` and `AIPOS_REPO_ROOT` support.
5. Audit hardcoded data-root assumptions.
6. Preserve the AIPOS-84P project docs cutover from `2_projects/ai-project-os` to `2_projects/lybra`.
7. Create the Lybra product repo.
8. Point Lybra dev and Board runtime at the private workspace.
9. Run current validation against the private workspace through the product repo.
10. Only then proceed to private remote production deployment.

## Answers Required by AIPOS-83

1. New product repo contents: generic tools, web UI, schemas, protocols, templates, examples, sample workspace, config examples, tests, and docs.
2. Private workspace contents: Owner project data, queues, drafts, records, orchestration, context packs, project docs, private runtime state, and private config.
3. Files excluded from product repo: real `2_projects/`, real `5_tasks/`, private records, private orchestration logs, real context packs, private agent endpoints, private workflow paths, secrets, and deployment state.
4. `0_control_plane` split: generic schemas/protocols/templates may move to product repo; private agent config, private paths, and workflow profiles remain workspace data or examples.
5. Workspace root lookup: product runtime should use `AIPOS_WORKSPACE_ROOT` or config, defaulting to current repo layout during transition.
6. Private production: deploy `~/lybra` and mount or point to `~/ai-project-os` as workspace.
7. Cloud agent endpoint: agents connect to the private Lybra Board/API endpoint and operate on the configured workspace root.
8. Avoiding duplicate AIPOS: do minimal extraction before deployment; do not deploy current mixed repo as the long-term cloud source of truth.
9. Migration: introduce product repo, add workspace config, validate external workspace layout, and preserve the completed `2_projects/lybra` project docs cutover.
10. Rollback: keep current mixed repo layout functional until the Lybra product repo validates against the private workspace; revert config to in-repo workspace layout if needed.

## AIPOS-83 Non-Goals

- no direct rename of `~/ai-project-os`
- no further project docs rename beyond the AIPOS-84P cutover
- no full SaaS productization
- no cloud deployment
- no auth/RBAC/database
- no autonomous planner runtime
- no automatic git push
- no self-audit
