# agents/pi/charters/ — 角色契约母本库

**设计权威**: LOOP-REDESIGN v2 §4 角色契约入分发

## 目的

此目录存放各角色的**契约母本**(AGENTS.md 等)。契约 = 角色的职责边界、红线、工作方式。

## 分发机制(AIPOS-CONN-LOOP-1 §4)

- **母本 = 产品仓单一源**:契约文件住此处,与 `schema/roles.schema.json` 联动。
- **工位副本 = 分发落点**:各 harness 工位(如 `~/projects/kiwiai-pi/lybra-executor/`)的 `AGENTS.md` 由分发器写入,**不入 git**。
- **版本追踪**:工位副本携带 `.version-{role}` manifest,记录源 commit。
- **修订流程**:修改契约 = 产品仓一张卡 → 审计通过 → 分发 → 各工位同步更新。

## 目录结构

```
charters/
├── README.md           # 本文件
├── executor/
│   └── AGENTS.md       # executor 契约母本
├── auditor/
│   └── AGENTS.md       # auditor 契约母本
└── advisor/
    └── AGENTS.md       # advisor 契约母本(如需)
```

## 与 roles.schema.json 的关联

`schema/roles.schema.json` 定义角色的:
- `scopes`: 权限范围
- `tool_package`: 分发的工具/技能
- `naming.prefix`: 实例命名前缀

`charters/` 定义角色的:
- 职责边界
- 红线约束
- 工作流程

两者共同构成角色的完整定义。

## 分发规格

契约分发由 `schema/distribution.schema.json` 声明,`tools/distribute_tools.py` 执行:

```json
{
  "distribution_id": "executor-charter",
  "kind": "charter",
  "source": {"repo": "lybra", "path": "agents/pi/charters/executor/AGENTS.md"},
  "target": {"harness": "pi", "relative_path": "AGENTS.md"},
  "applies_to_roles": ["executor"],
  "gitignore": true
}
```

## 生成物不入库(AIPOS-R3 教训)

工位副本是**派生物**,gitignore 不提交:
- 入库 = 第二源 → 必然漂移
- 单一源 = 此处母本
- 工位副本 = 按需重生成,版本以 manifest 为准

## kaia-* 项目

**kaia-kb / kaia-agency 等非 lybra 项目的契约不在此处**,仍在各自项目维护。
本分发机制仅管理 lybra 项目角色(executor/auditor/advisor)。
