# Runtime Profile Policy

## Purpose

Runtime profiles describe host topology and authoritative execution locations for a concrete agent workflow. A runtime profile says where the agent UI runs, where commands run, where repository state lives, where validation is authoritative, and where git operations are allowed.

A runtime profile is declarative protocol documentation and does not execute commands by itself.

## Generic Mixed-Host Workspace Profile

```yaml
runtime_profile_id: local_process_workspace
agent_ui_host: macos
execution_host: workspace-host
repo_host: workspace-host
validation_host: workspace-host
git_host: workspace-host
connection_method: ssh
ssh_target: workspace-host
canonical_repo_path: /home/owner/workspace
validation_required_on: execution_host
git_operations_allowed_on: git_host
```

- `agent_ui_host` is where the operator-facing agent session runs.
- `execution_host` is where repository commands must execute.
- `repo_host` is where canonical repository state lives.
- `validation_host` is where validation output is authoritative.
- `git_host` is where git status, diff, stage, commit, and push are authoritative.
- `connection_method` and `ssh_target` describe how the operator UI reaches the execution host.
- `canonical_repo_path` identifies the repository path on the execution host.
- `validation_required_on` and `git_operations_allowed_on` bind validation and git operations to declared hosts.

## Authority Boundaries

Runtime profiles do not grant OS permissions, GitHub permissions, repository file-write authority, network authority, credential authority, Owner approval, audit approval, or permission to bypass validation or finalize gates.

A runtime profile must be combined with task scope, AIPOS-48 matching policy, write-scope policy, audit policy, and Owner decisions.

## Relationship To AIPOS-48

Runtime profiles do not replace AIPOS-48 matching. AIPOS-48 decides which concrete agent instances are eligible to match and claim a pending task. Runtime profile fields may be used as matching inputs, but they do not make a task executable and do not grant claim authority by themselves.

## Relationship To Future Session Binding

Runtime profiles do not replace future task session lease or runtime binding. A future session binding policy may reference `execution_host`, `validation_host`, `git_host`, `connection_method`, `ssh_target`, and `canonical_repo_path` when binding a claimed task to a concrete running execution session.

AIPOS-49 defines a mixed-host workflow profile. AIPOS-50 should define task session lease and runtime binding semantics.
