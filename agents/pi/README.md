# agents/pi/ —— 已迁移(指路 README,不留双源)

> AIPOS-C4B 大项C:本目录下的 skills/charters 已迁入单一源新布局(旧门循环扩展随 AIPOS-F91 退役删除, 推进改由顾问 `lybra loop`)。
> **此目录不再承载任何母本内容**,仅保留本指路文件。

## 旧路径 → 新路径

| 旧路径(已迁移) | 新路径(单一源) |
|---|---|
| `agents/pi/skills/<name>/` | [`agents/skills/<name>/`](../skills/) |
| `agents/pi/charters/<role>/AGENTS.md` | [`agents/roles/<role>/AGENTS.md`](../roles/) |
| 旧门循环扩展目录 | (已退役删除, AIPOS-F91; pi 工位扩展母本见 [`agents/harness/pi/`](../harness/pi/), 工位开工 = 手工 `/go` 或经信封授权由 `lybra loop` 拉起) |

## 为什么迁移

- `pi` 是执行体 harness 名,不应混放全角色资产(顾问/审计/执行体的 skill+charters)。
- 源侧单一源:skills = 角色无关内容;roles = 宪章 + 装配清单;harness/pi = pi 适配物。
- 分发器按 (role, harness) 组装工位分发物,母本只此一处,不留双源。

## 单一源权威

- skills 母本:[`agents/skills/`](../skills/)
- 角色契约母本:[`agents/roles/`](../roles/)
- pi 适配物(工位扩展 go.ts):[`agents/harness/pi/`](../harness/pi/)
- 分发规格:[`schema/distribution.schema.json`](../../schema/distribution.schema.json)
