# Memory Layers & Principles

## 记忆解耦原则
- **Agent 本地记忆**：各 AI / Agent 自己的私有上下文（Context）归各自所有，不进行全局同步。
- **共享工作记忆（Shared Memory）**：AI Project OS 管理跨项目、跨 Agent 的核心成果、决策和信号。
- **User Model**：Cortex 暂时只负责 user model 的精准维护。

## 记忆流转路径
1. **产生**：Agent 完成任务并产出 Memory Capture。
2. **缓冲**：Memory Capture 进入对应 Agent 的 `4_inbox/`。
3. **整理**：由 Owner 或 Planner 整理后，进入正式 Shared Memory 或项目文档。

## 业务域划分
共享记忆按业务域分层，包括：开发协作、工程状态、环境规则、市场、竞品、行业信号、营销实验、销售线索等。

## 访问机制
不是所有 Agent 读取所有记忆，而是根据角色（Role）和任务（Task）生成特定的 **Context Bundle**。

## Formal Memory Entry Points

The formal memory entry points for AI Project OS Control Plane v0.1 are:

- `0_control_plane/memory/`
- `1_shared_memory/`
- `4_inbox/`

A root-level `MEMORY.md`, if present historically or used by some agents, is only a compatibility or temporary entry point. It is not the formal long-term memory contract for Control Plane v0.1.

## SessionStore Protocol

AIPOS-92 defines the future Lybra SessionStore schema and credential boundary in:

- `0_control_plane/memory/sessionstore_schema_credential_boundary_protocol.md`

SessionStore remains protocol-only in AIPOS-92. It does not create directories, implement writers, add indexes, add credentials, or replace file-authoritative shared memory and project records.
