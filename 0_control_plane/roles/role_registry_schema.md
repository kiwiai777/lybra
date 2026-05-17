# Role Registry Schema & Rules

## 核心理念
Role Registry 是 AI Project OS 的**配置化数据层**，用于定义和管理所有参与项目的角色实例。

## 变更规则
1. **非破坏性更新**：优先使用 `status: inactive` 来停用角色，不建议直接从 Git 历史中彻底删除角色记录。
2. **配置化驱动**：Registry 是可配置的数据，不是硬编码。工具应通过读取此文件来获取上下文。
3. **多对多关系**：同一个 Actor（如 OpenClaw）可以承载多个 Role Instance（如 Con龙虾 和 Biz龙虾）。
4. **动态身份**：对于 Claude Code 和 Codex 等研发工具，其具体身份（Coder/Reviewer）由当次任务卡决定，Registry 仅定义其能力范围。

## 环境与状态管理
- **云端 Agent**：默认支持 24h 连续运行，适合长期调研和业务运营。
- **本地 Agent**：受 Owner 作息和本地机器状态影响，必须支持 `requires_resume_on_exit: true` 以实现上下文衔接。

## 字段定义
- `id`: 唯一标识符。
- `display_name`: 人类可读名称。
- `status`: 状态 (`active` / `inactive`)。
- `actor`: 物理/平台 Agent 类型。
- `availability`: 可用性 (`24h` / `non_24h`)。
- `environment`: 运行环境。
- `primary_role`: 核心职责。
- `can_act_as`: 能力列表。
- `requires_resume_on_exit`: 退出时是否需要生成 Resume 信息。

## Primary Role vs Can Act As vs Task Mode
- **primary_role**: 默认主责，不是硬限制。
- **can_act_as`: 允许的能力集合，定义角色实例的能力边界。
- **task_mode**: 通常不由 registry 永久存储，而是由每次 task card 指定。
- **role instance**: 不是僵化的岗位，而是可以切换 task_mode 的灵活执行实体。
- **role registry**: 是可配置数据，不是硬编码的组织结构。

## Configurability and Evolution
- **角色实例是可配置且可进化的**。Owner 可以新增、停用、改名、调整职责、扩展或收缩能力。
- **preferred deactivation**: 使用 `status: inactive` 而非硬删除。
- **hard deletion**: 应稀少且需 Owner 授权。
- **Task Mode Policy**: 详见 `task_mode_policy.md`，本 schema 仅定义数据结构。
