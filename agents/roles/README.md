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
- **工位副本 = 分发落点**:各 harness 工位(说明性示例, 非章程字面: 如 `<工位父根>/lybra-executor/`)的 `AGENTS.md` 由分发器写入,**不入 git**。
- **母本项目无关(AIPOS-F80 件②)**:母本内凡项目/实例/机器/仓路径/落点一律写 `{{key}}` 占位, 键只用
  `tools/aipos_cli/charter_render.py` `charter_render_context()` 声明的渲染上下文键(project / governance_root / code_repo /
  return_root / verdict_root / queue_root / task_cards_root / gate_url / harness_root / harness_parent / role / role_class /
  instance / machine / executor_instance / auditor_instance); 未声明占位 = 渲染拒(fail-closed)。母本禁写任何具体项目字面。
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

## 多项目

母本对所有接入项目通用(executor/auditor/advisor 三类), 由 `lybra sync` 按工位项目声明渲染(AIPOS-F66B 件① / F80 件②)。
项目自定义角色(project.json custom_roles)按其角色类取对应母本。
