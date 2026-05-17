# AI Project OS Control Plane

## 定位
AI Project OS 是多 Agent、多环境、多项目、多业务职能的总控层。

## 核心原则
- **总控仓库**：Control Plane 不替代各项目自己的 workflow。
- **职责解耦**：Control Plane 不替代 Cortex 的 user model。
- **资源管理**：Control Plane 管理角色（Roles）、环境（Environments）、共享记忆（Shared Memory）、完成回报（Completion Reports）、上下文包（Context Bundles）以及可视化看板（Board）。
- **本地规范**：`~/ai-project-os` 是唯一的 Canonical Local Path。
- **本地私有**：`task_cards/` 是本地任务卡目录，受 `.gitignore` 保护，不进入 Git 仓库。

## Environment Policy Entry Points

Canonical cross-project environment policies live under:

- `0_control_plane/environments/`

The shared Kiwiai production server directory policy is:

- `0_control_plane/environments/server_directory_policy.md`

The provider-agnostic sandbox runtime abstraction protocol is:

- `0_control_plane/environments/sandbox_runtime_abstraction_protocol.md`

Project-specific repositories should reference Control Plane environment policies instead of redefining shared server root layout rules.

## Memory Entry Contract

The formal memory entry points for AI Project OS Control Plane v0.1 are:

- `0_control_plane/memory/`
- `1_shared_memory/`
- `4_inbox/`

A root-level `MEMORY.md`, if present historically or used by some agents, is only a compatibility or temporary entry point. It is not the formal long-term memory contract for Control Plane v0.1.
