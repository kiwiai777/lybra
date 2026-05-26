---
template_id: software-development
template_version: 1
template_status: bundled
template_kind: workspace_project_skeleton
display_name: Software Development
description: Local workflow skeleton for a software delivery project.
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

# Software Development Template

Software delivery workflow skeleton with a sample task and audit handoff notes.
