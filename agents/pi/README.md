# Lybra Pi Harness 工具包

本目录包含 Lybra 项目为 Pi 编码代理 harness 提供的工具集，由 gate 分发器按角色类别下发到目标机。

## 来源

迁移自 `kiwiai-pi` 仓库（AIPOS-R3，2024-08-12）：
- `lybra-loop/` ← `kiwiai-pi/_shared/extensions/lybra-loop/` (5个TS文件 + tests)
- `skills/` ← `kiwiai-pi/_shared/skills/` (7个lybra专属skills)

## 结构

```
agents/pi/
├── lybra-loop/          # Loop 连接器与引擎
│   ├── gate-client.ts   # Gate MCP 客户端
│   ├── loop-context.ts  # LoopContext 解析
│   ├── loop-decisions.ts# 决策逻辑
│   ├── loop-engine.ts   # 主引擎
│   ├── lybra-loop.ts    # Pi 扩展入口
│   └── tests/           # 单元测试
└── skills/              # Lybra 专属技能包
    ├── block-and-report/
    ├── chunked-io/
    ├── finalize-slice/
    ├── task-closure-loop/
    ├── write-return/
    ├── audit-independent-evidence/
    └── truth-first-drafting/
```

## 分发机制

- **版本**：工具包版本 = 产品仓 commit hash
- **分发动词**：`lybra roles fetch-tools` 或 enroll 时自动下发
- **落点**：目标机 harness 挂载点（如 `kiwiai-pi/lybra-executor/.pi/`）
- **更新**：版本协商 — agent 连门带版本，gate 判过旧自动下发

## 所有权

这些代码属于 Lybra 项目。改动 = 产品仓出卡执行，不得直接在 harness 侧修改。

## 历史

- 2024-08-12: AIPOS-R3 集中迁移，实现 LOOP-REDESIGN v2 §4/§6 "一机制一实现+gate统一分发"
