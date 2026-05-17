# Workspace Data Layer Policy

## Runtime Roots

Lybra must distinguish product code from workspace data.

```text
AIPOS_REPO_ROOT=/path/to/lybra
AIPOS_WORKSPACE_ROOT=/path/to/private/workspace
```

During transition, both may point at the current mixed repository. Future private production should run product code from `~/lybra` while reading private workspace data from `~/ai-project-os`.

## Default Compatibility

Existing local behavior remains compatible:

```text
repo root == workspace root == ~/ai-project-os
```

AIPOS-84P completed the project management directory cutover from `2_projects/ai-project-os` to `2_projects/lybra`. The workspace root name remains unchanged.

## Config Example

```yaml
workspace_root: /home/owner/ai-project-os
product_root: /home/owner/lybra
data_dirs:
  projects: 2_projects
  tasks: 5_tasks
  queue: 5_tasks/queue
  drafts: 5_tasks/drafts
  records: 5_tasks/records
  orchestration: 5_tasks/orchestration
  context_packs: context_packs
project_docs:
  current_canonical: 2_projects/lybra
  legacy_canonical: 2_projects/ai-project-os
```

## Product Data Boundary

Product repository content:

- CLI and Board source code
- generic adapter/API contracts
- schemas and validation logic
- generic protocol docs
- templates and examples
- sample workspace
- tests
- config examples

Workspace repository content:

- Owner project docs
- live task queues and drafts
- records and session/claim data
- orchestration logs
- context packs
- private agent registry/runtime config
- private workflow profiles
- private deployment paths and runtime state

## Project Docs Boundary

Current canonical path after AIPOS-84P:

```text
2_projects/lybra/
```

Legacy path before AIPOS-84P:

```text
2_projects/ai-project-os/
```

Project-scoped docs include:

- `project_status.md`
- `roadmap.md`
- `decision_log.md`
- `stage_archives/`
- project-specific README files
- other project-scoped documentation

The migration must not leave both paths as long-term sources of truth.

## Reference Types To Update During Migration

- hardcoded paths
- CLI project lookup assumptions
- Board project lookup assumptions
- docs references
- task card references
- records references
- orchestration references
- validation commands
- stage archive paths
- decision log links
- project status links

## `0_control_plane` Split

Generic product candidates:

- schemas
- protocol docs
- generic task/dispatch/board policies
- generic orchestration policies
- generic templates
- example registries

Workspace-private candidates:

- real agent registry values
- private agent endpoints
- private runtime profiles
- private workflow profiles
- private workspace paths
- environment-specific config
- credentials or references to credentials

Ambiguous files must be copied into product only as examples or templates until audited.

## Private Production Rule

AIPOS-84 must not deploy the current mixed repo as the long-term production shape. It should deploy product code from Lybra and point it at the private workspace.

## Rollback

Rollback is config-first:

1. stop private service
2. point runtime back to current mixed repo layout
3. restore `2_projects/ai-project-os` from git if the cutover fails validation
4. do not keep `2_projects/ai-project-os` and `2_projects/lybra` as competing sources of truth
