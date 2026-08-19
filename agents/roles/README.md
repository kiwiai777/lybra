# agents/roles/ — 角色契约母本库 + 装配清单(单一源)

**设计权威**: LOOP-REDESIGN v2 §4 角色契约入分发;AIPOS-C4B 大项C 目录重排。

## 目的

此目录存放各角色的**契约母本**(AGENTS.md)。契约 = 角色的职责边界、红线、工作方式。

## 目录结构

```
roles/
├── README.md           # 本文件
├── executor/
│   └── AGENTS.md       # executor 契约母本
├── auditor/
│   └── AGENTS.md       # auditor 契约母本
└── advisor/
    └── AGENTS.md       # advisor 契约母本
```

## 装配清单(该角色装哪些 skill)= roles.schema.json 单一源

「该角色装哪些 skill 的装配清单」的**唯一真相**在
[`schema/roles.schema.json`](../../schema/roles.schema.json) 的
`roles[].tool_package.skills`(extensions 同理)。分发器
`tools/distribute_tools.py` 经 `schema_loader.get_role_tool_package()` 读取它,
按 (role, harness) 组装出工位分发物。

> 红线(一机制一实现):装配清单**不**在本目录再复制一份——复制 = 第二源 = 漂移。
> 改「某角色装哪些 skill」= 改 roles.schema.json 一条数据,分发器零代码改动。

## 分发机制

- **母本 = 产品仓单一源**:契约文件住此处,与 `schema/roles.schema.json` 联动。
- **工位副本 = 分发落点**:各 harness 工位(如 `~/projects/kiwiai-pi/lybra-executor/`)的 `AGENTS.md` 由分发器写入,**不入 git**。
- **版本追踪**:工位副本携带 `.version-{role}` manifest,记录源 commit + 内容哈希。
- **修订流程**:修改契约 = 产品仓一张卡 → 审计通过 → 分发 → 各工位 sync 同步更新。

## 与 roles.schema.json 的关联

`schema/roles.schema.json` 定义角色的:
- `scopes`: 权限范围
- `tool_package`: 分发的工具/技能(装配清单单一源)
- `naming.prefix`: 实例命名前缀

`roles/` 定义角色的:
- 职责边界
- 红线约束
- 工作流程

两者共同构成角色的完整定义。

## 生成物不入库(AIPOS-R3 教训)

工位副本是**派生物**,gitignore 不提交:入库 = 第二源 → 必然漂移;
单一源 = 此处母本;工位副本 = 按需重生成,版本以 manifest 为准。

## kaia-* 项目

**kaia-kb / kaia-agency 等非 lybra 项目的契约不在此处**,仍在各自项目维护。
本分发机制仅管理 lybra 项目角色(executor/auditor/advisor)。
