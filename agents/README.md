# agents/ — Lybra 角色资产单一源(AIPOS-C4B 大项C 目录重排)

**设计权威**: LOOP-REDESIGN v2 §4;AIPOS-C4B 大项C。

分发物母本分三处,按职责分离(源侧单一源):

| 目录 | 内容 | 角色相关性 |
|---|---|---|
| `agents/skills/<name>/` | skill 母本(角色无关内容) | 无(按 distribution.schema.json 装配清单分发) |
| `agents/roles/<role>/` | 角色契约 AGENTS.md(宪章) | 按角色 |
| `agents/harness/pi/` | pi harness 适配物(声明分发的扩展, 如 `_shared/extensions/go.ts`) | 按 harness |

## 装配清单(该角色装哪些 skill)

单一真相 = [`schema/distribution.schema.json`](../schema/distribution.schema.json) 的
`distributions[]`(每条目 `applies_to_roles` + skills 条目 `filter.include`)。分发器
`tools/distribute_tools.py` 与清单构建器 `tools/distribution_manifest.py`(`build_role_manifest`)
均只读它,按 (role, harness) 组装。roles.schema.json 不再承载装配清单(AIPOS-F83 件②
`tool_package` 退役, 只留 `tool_package_retired` 指向说明;AIPOS-F84 件③ 删除其零调用读取函数)。

## 分发

- **母本 = 此处**;工位副本 = 分发落点(`_distributed/` + charter AGENTS.md),生成物不入库。
- **版本戳** = 源 commit 短哈希,写在 `.version-{role}` 的 `version` 字段。
- **工位拉取** = `lybra sync`(工位发起 pull,gater 被动);落点与 shim 不变。
- 旧路径 `agents/pi/` 只留指路 README,不留双源。

## 历史

- 2026-08-12 AIPOS-R3:从 kiwiai-pi 迁移到 `agents/pi/`(5 TS + skills + tests)。
- 2026-08-19 AIPOS-C4B:重排为 skills / roles / harness 三源,消"pi 混放全角色资产"。
