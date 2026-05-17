# Private Workspace Migration Checklist

## Before Migration

- Confirm AIPOS-82 mobile Owner review polish is finalized.
- Confirm AIPOS-83 extraction docs and sample config are audited.
- Confirm no cloud deployment is running from the old mixed repo as the long-term production source.
- Confirm the Lybra product repo exists or is ready to be created.
- Confirm current mixed repo validation passes.
- Confirm AIPOS-84P project docs cutover has either not started or is inside an approved cutover window.

## Product Repo Setup

- Create `~/lybra`.
- Copy or move product code according to `standalone_repo_extraction_plan.md`.
- Include generic `tools/`, `web/`, `0_control_plane/`, `docs/`, `examples/`, `config/`, tests, and README.
- Exclude Owner private workspace data.
- Add `config/aipos.example.yaml`.
- Verify product tests can run without real workspace data by using sample workspace fixtures.

## Workspace Setup

- Keep workspace root name unchanged for now:

```text
~/ai-project-os
```

- Legacy project docs path before AIPOS-84P:

```text
2_projects/ai-project-os
```

- Current canonical project docs path after AIPOS-84P:

```text
2_projects/lybra
```

## Project Docs Rename Plan

Files migrated from `2_projects/ai-project-os/` to `2_projects/lybra/` by AIPOS-84P:

- `project_status.md`
- `roadmap.md`
- `decision_log.md`
- `README.md`
- `stage_archives/`
- any other project-scoped docs under the current project directory

AIPOS-83 did not perform this rename. AIPOS-84P scopes and validates the cutover before private remote production deployment.

## Cutover Checklist

- Pick a short cutover window.
- Freeze writes to `2_projects/ai-project-os`.
- Move project docs to `2_projects/lybra`.
- Update path references in docs, task cards, records, orchestration logs, validation docs, stage archive links, decision log links, and project status links.
- Update CLI/Board project lookup config to `2_projects/lybra`.
- Run validation from the product repo against the private workspace.
- Confirm no duplicate source of truth remains.

## Private Deployment Prep

AIPOS-84 may begin only after:

- product/workspace boundary is accepted
- workspace root abstraction is accepted
- `2_projects/lybra` cutover is accepted
- rollback plan is clear
- Owner confirms private remote access boundary

## Rollback Checklist

- Stop the private Lybra service.
- Point runtime back to the current mixed repo.
- Restore `2_projects/ai-project-os` as canonical if `2_projects/lybra` cutover fails.
- Do not continue writes to both project paths.
- Document rollback in decision log before retrying migration.
