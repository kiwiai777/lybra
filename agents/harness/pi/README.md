# agents/harness/pi/ — pi harness 适配物(单一源)

**设计权威**: LOOP-REDESIGN v2 §4;AIPOS-C4B 大项C 目录重排。

本目录存放 **pi 编码代理 harness 的适配物**(工位扩展母本)。
与 `agents/skills/`(角色无关 skill 内容)与 `agents/roles/`(角色契约)分开:
- skills 是角色无关内容;roles 是角色契约;**harness/pi 是引擎适配**。

## 结构

```
harness/pi/
└── _shared/
    └── extensions/
        └── go.ts        # 工位 /go: 只读 `lybra my-tasks` 输出开工(夹具在产品仓 tests/ts/)
```

工位只有 `/go` 一个扩展; 推进卡片由顾问 `lybra loop` 完成(AIPOS-F91 起工位侧不再有门循环扩展),
TS 夹具在产品仓 `tests/ts/`, 经 `tests/run-all.sh` 常驻。

## 分发

分发清单唯一真相 = `schema/distribution.schema.json`(applies_to_roles + filter, 按角色类展开);
`lybra sync` 把本目录扩展母本分发到工位, enroll 按同一声明接线 `.pi/extensions/`。

## 所有权

这些代码属于 Lybra 项目。改动 = 产品仓出卡执行,不得直接在 harness 侧修改。
