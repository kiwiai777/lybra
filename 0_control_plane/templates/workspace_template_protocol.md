# Workspace Template Protocol

## Purpose

AIPOS-125 defines the protocol boundary for Lybra workspace templates.

Workspace templates are local-first project workflow skeletons. They help an Owner create a predictable file tree for a new project or client workflow without hand-assembling project docs, task queue directories, draft directories, records directories, and example task patterns every time.

AIPOS-125 is protocol-only. It does not create bundled templates, create a `templates/` directory, implement `workspace init`, add controlled execute operations, change validators, add CLI commands, add Board UI, add MCP tools, write private workspace files, or fetch remote template content.

## Positioning

Lybra workspace templates are:

- project workflow skeletons
- local product repo assets when bundled
- plain files and markdown
- rendered through explicit placeholder substitution
- written only after a dry-run preview and Owner-confirmed controlled execute flow in a future implementation task

Lybra workspace templates are not:

- agent-company teams
- model/vendor presets
- marketplace packages
- remote recipes
- dependency graphs
- plugin installers
- credential bundles
- runtime launchers
- hidden workspace writers

## Template Location

Future bundled templates may live under:

```text
templates/<template_name>/
```

This location is relative to the Lybra product repo root.

The directory is reserved by this protocol but not created by AIPOS-125.

Official template names reserved for future implementation:

```text
blank
consulting-engagement
software-development
```

Reserved names do not create template files and do not imply implementation.

## Template Unit

One template represents one project workflow skeleton.

The template may include:

- project documentation skeletons
- queue/draft/record directory skeletons
- example task cards
- example external intake metadata fields from AIPOS-107
- placeholder README files for otherwise-empty directories
- deliverable placeholder directories

The template must not include:

- private project data
- Owner hostnames or paths
- real client data
- credentials
- generated runtime state
- SessionStore entries
- orchestration logs from real runs
- concrete agent deployment names
- model/vendor-specific worker assignments

## Template Manifest

Each template must include:

```text
manifest.md
```

The manifest is markdown with a YAML frontmatter descriptor.

Descriptor shape:

```yaml
---
template_id: consulting-engagement
template_version: 1
template_status: bundled
template_kind: workspace_project_skeleton
display_name: Consulting Engagement
description: Local project workflow skeleton for an SMB service engagement.
required_variables:
  - project_id
optional_variables:
  - client_id
  - client_name
  - source_tag
  - external_ref
output_policy:
  output_must_be_absent_or_empty: true
  overwrite_existing_files: false
  remote_fetch_allowed: false
controlled_execute:
  dry_run_required: true
  confirm_required: true
---
```

Manifest rules:

- `template_id` must match the directory name.
- `template_id` must use lowercase kebab-case.
- `template_version` is an integer.
- `template_kind` must be `workspace_project_skeleton`.
- `required_variables` and `optional_variables` are declarative inputs only.
- `output_policy.remote_fetch_allowed` must be false for bundled templates.
- `controlled_execute.dry_run_required` and `controlled_execute.confirm_required` must be true.

## File Tree Contract

Template content is ordinary files under the template directory, excluding `manifest.md`.

Recommended future layout:

```text
templates/<template_name>/
  manifest.md
  tree/
    README.md
    2_projects/{{ project_id }}/README.md
    2_projects/{{ project_id }}/decision_log.md
    2_projects/{{ project_id }}/project_status.md
    2_projects/{{ project_id }}/roadmap.md
    5_tasks/queue/pending/.keep
    5_tasks/queue/claimed/.keep
    5_tasks/queue/completed/.keep
    5_tasks/queue/blocked/.keep
    5_tasks/drafts/.keep
    5_tasks/records/.keep
```

`tree/` is the render root. A future implementation should render every file and directory under `tree/` into the requested output path.

AIPOS-125 does not create this directory.

## Placeholder Syntax

Placeholders use:

```text
{{ variable_name }}
```

Rules:

- variable names use lowercase snake_case
- whitespace around the variable name is allowed
- placeholders may appear in file content
- placeholders may appear in relative path segments
- unknown placeholders block dry-run
- missing required variables block dry-run
- unresolved placeholders block confirm
- placeholder values are strings in the MVP protocol

Reserved syntax:

```text
{{ variable_name | filter }}
{% control_block %}
${ variable_name }
```

Filters, control blocks, expression evaluation, shell expansion, and environment-variable interpolation are not part of the MVP protocol.

## MVP Variable Semantics

MVP defaults:

```yaml
project_id:
  required: true
  role: primary workspace project directory name
  validation: lowercase letters, numbers, dash, underscore
client_id:
  required: false
  role: business/client tag when the workflow represents a client
  default: project_id only if the implementation explicitly documents the default
client_name:
  required: false
  role: human-readable label
source_tag:
  required: false
  role: AIPOS-107 compatible source metadata
external_ref:
  required: false
  role: AIPOS-107 compatible external reference metadata
```

`project_id` and `client_id` are not universally the same concept. A consulting template may choose to default `client_id` to `project_id`, but that behavior must be visible in the dry-run preview.

## AIPOS-107 Metadata Alignment

Templates that include task cards or external-intake examples may use the finalized AIPOS-107 fields:

```yaml
source_tag:
client_tag:
external_ref:
```

Rules:

- templates may include these fields as optional metadata
- templates must not redefine their meaning
- validators remain authoritative
- missing fields must follow existing optional-field behavior
- template rendering must not silently invent private source tags

For consulting-oriented bundled templates, `client_tag` should be rendered from `client_id` unless Owner approves a different mapping.

## CLI Contract

Future CLI shape:

```bash
lybra workspace init --template <name> --output <path> [--var k=v]
```

Equivalent repository CLI entrypoints may be used if they match existing Lybra CLI conventions.

Required behavior:

- `--template` selects a local bundled template by name
- `--output` selects the target workspace root or project root
- `--var k=v` supplies placeholder values
- dry-run mode previews every planned file and directory
- confirm mode writes only after controlled execute token and snapshot revalidation

The implementation task must define exact command syntax before coding.

## Controlled Execute Contract

Workspace initialization is a write operation. Future implementation must use controlled execute.

Required lifecycle:

1. parse template manifest
2. validate variables
3. render all planned relative paths
4. render all planned file contents
5. check target output path policy
6. return dry-run preview with every planned directory and file
7. return blocking reasons for conflicts or unresolved placeholders
8. issue dry-run token only when execute would be allowed
9. confirm only with dry-run token
10. revalidate snapshot immediately before writing
11. write only the previewed files and directories
12. return structured execute envelope

The future controlled execute operation name should be Owner-approved before implementation. Suggested name:

```text
workspace_init
```

AIPOS-125 does not add this operation to any allowlist.

## Output Path Policy

MVP default:

```yaml
output_must_be_absent_or_empty: true
overwrite_existing_files: false
```

Rules:

- target output path may be absent
- target output path may exist only if it is empty
- existing files block dry-run
- existing directories with files block dry-run
- overwriting files is forbidden
- path traversal outside output root is forbidden
- absolute paths inside template trees are forbidden
- symlink creation is out of scope

Any overwrite, merge, supersession, or repair behavior requires a separate Owner Decision Gate.

## Local Distribution Boundary

Bundled templates are local product repo assets.

The MVP protocol forbids:

- remote template fetch
- marketplace search
- template registry runtime
- dependency resolution
- semantic version solving
- auto-update
- telemetry
- template install from URL
- arbitrary script execution from templates

If Lybra later introduces a separate same-Owner `lybra-templates` repository, that requires a future protocol and audit.

## Official Template Intents

The following names are reserved for future bundled template implementation.

### blank

Minimal workspace skeleton.

Expected intent:

- create core directories
- include a root README
- include placeholder files only where needed to preserve empty directories

### consulting-engagement

SMB service workflow skeleton.

Expected intent:

- project docs skeleton
- decision log skeleton
- project status skeleton
- roadmap skeleton
- sample external-intake-style task or draft using AIPOS-107 fields
- deliverables placeholder directory

### software-development

Software delivery workflow skeleton.

Expected intent:

- task queue state example
- sample development task
- sample review/audit handoff text
- project docs skeleton

AIPOS-125 does not create these templates.

## Security And Privacy

Templates must be safe to publish in the product repo.

Rules:

- no private paths
- no private hostnames
- no real client names
- no real credentials
- no raw external messages
- no tokens
- no hidden binary payloads except explicitly approved static assets
- no scripts that execute during render

Template rendering must not read private workspace data except the output path state needed for dry-run conflict checks.

## Non-Goals

AIPOS-125 does not:

- create `templates/`
- create `manifest.md` files
- create official bundled templates
- implement `workspace init`
- add CLI commands
- add controlled execute operations
- expand allowlists
- modify validators
- modify Board, MCP, sandbox, SessionStore, queue, records, or orchestration code
- write private workspace files
- implement remote template fetching
- create a marketplace
- create a template registry service
- add version dependency resolution
- choose future overwrite or merge behavior

## AIPOS-126 Implementation Notes

AIPOS-126 may implement this protocol only after AIPOS-125 is finalized and Owner approves the implementation boundary.

Recommended AIPOS-126 MVP:

- implement local template discovery
- implement manifest parsing
- implement string placeholder rendering
- implement `workspace_init` dry-run and confirm through controlled execute
- bundle `blank`, `consulting-engagement`, and `software-development`
- test with temporary output directories only
- keep private workspace data out of product templates

AIPOS-126 should not implement remote fetch, marketplace, overwrite, template updates, registry runtime, Board UI, MCP tools, SessionStore writes, orchestration append, queue mutation beyond generated sample files, or deployment behavior.

## Audit Checklist

An independent audit should confirm:

- the task is protocol-only
- no product root `templates/` directory or bundled template files were created
- no implementation code was added
- no controlled execute allowlist changed
- local-only/no-marketplace boundary is explicit
- project workflow skeleton positioning is clear
- AIPOS-107 metadata is referenced without being redefined
- dry-run and confirm requirements are preserved for future writes
- private data is not copied into the product repo
