# Memory Capture Protocol

## 核心目的
Memory Capture 是 Agent 任务执行后的结构化沉淀格式，旨在将零散的执行过程转化为可被其他角色利用的正式记忆。

## 捕获格式
```markdown
# Memory Capture

Date:
Source Agent:
Role:
Environment:
Project:
Domain:

## Summary
核心成果的简要描述。

## Decisions
在执行过程中做出的关键决策及理由。

## Signals
捕获到的外部信号、竞品动态或市场机会。

## Action Items
由此衍生的待办事项。

## Affected Projects
受到此任务影响的其他项目。

## Suggested Memory Updates
建议更新到 Shared Memory 或 MEMORY.md 的具体内容。
```

## 处理流程
- **防污染**：严禁 Agent 直接将临时 Chain-of-Thought 或冗余日志写入正式记忆。
- **整理者审核**：所有进入正式 Shared Memory 的内容必须经过 Owner 或指定 Planner 的审核。
