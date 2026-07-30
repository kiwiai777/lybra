---
task_id: EXAMPLE-001
title: '示例任务：验证工作区基础设施'
project: {{ project_id }}
assigned_to: exec.{{ project_id }}
task_mode: code
task_class: simple
priority: normal
status: draft
created_by: advisor.{{ project_id }}
needs_owner: false
output_target: docs/
artifact_policy: formal_write
---

# EXAMPLE-001 — 验证工作区基础设施

**这是一张示例任务卡**，用于演示 Lybra 的任务卡结构和工作流。你可以参考它来起草自己的任务卡。

---

## 任务目标

验证本工作区的基础目录结构和配置文件是否完整、可访问，并输出一份验证报告。

---

## 内容

1. **检查目录结构**：
   - `2_projects/{{ project_id }}/`（项目文档目录）
   - `5_tasks/queue/`（任务队列：pending/claimed/blocked/completed）
   - `5_tasks/drafts/`（草稿区）
   - `5_tasks/records/`（执行记录）
   - `governance/`（治理文档：advisor-charter.md, AGENTS.md）

2. **检查配置文件**：
   - `.lybra/config.json`（工作区配置）

3. **输出验证报告**：
   在 `docs/workspace-verification-report.md` 创建报告，列出：
   - 检查的目录/文件清单
   - 每项的状态（存在/缺失/权限问题）
   - 发现的问题（如有）
   - 验证时间戳

---

## 验收断言

- **S1**：所有核心目录（`2_projects/`, `5_tasks/queue/`, `5_tasks/drafts/`, `5_tasks/records/`, `governance/`）存在且可访问。
- **S2**：`.lybra/config.json` 存在且为有效 JSON。
- **S3**：`docs/workspace-verification-report.md` 生成，包含清晰的检查结果。
- **S4**：零回归（不修改现有文件，只新增报告）。

---

## 车道

- **允许读取**：整个工作区（治理仓）
- **允许写入**：`docs/workspace-verification-report.md`（新增文件）
- **禁止修改**：现有配置、任务卡、records

---

## 知识入口

- `governance/advisor-charter.md` — 顾问工作方式说明
- `governance/AGENTS.md` — Executor/Auditor 角色说明
- 本任务卡 frontmatter — 了解任务卡的元数据结构

---

## 实施提示

1. 使用 `ls -la` 或 `find` 检查目录结构
2. 使用 `cat .lybra/config.json | jq .` 验证配置文件为有效 JSON
3. 在 `docs/` 创建 markdown 报告，使用清晰的表格或列表呈现结果
4. 如发现缺失目录，记录在报告中（但不擅自创建）

---

## 为什么这是好示例

- **目标明确**：验证基础设施，输出报告
- **验收可测**：文件存在 = 可验证；报告内容 = 可检查
- **车道清晰**：只读工作区，只写 `docs/` 报告
- **零依赖**：不需要外部工具或服务
- **安全**：不修改现有配置或代码

---

## 下一步

如果你是 Advisor：
1. 根据项目实际需求，修改此示例或创建新任务卡
2. 完成六查（查现状/依赖/车道/知识入口/验收/护栏）
3. 建议 Owner 发布：`lybra draft publish 5_tasks/drafts/example-task.md`

如果你是 Executor：
1. 认领此任务：`lybra claim EXAMPLE-001`
2. 按卡内内容独立执行
3. 如实返回：`lybra return EXAMPLE-001 --outcome completed --summary "..." --details "..."`

如果你是 Owner：
1. 审阅草稿，确认符合意图
2. 发布：`lybra draft publish 5_tasks/drafts/example-task.md`
3. 追踪进度：`lybra owner-truth` 查看 record-derived 真实阶段

---

**开始你的 Lybra 之旅吧！**
