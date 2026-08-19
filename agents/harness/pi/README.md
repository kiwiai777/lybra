# agents/harness/pi/ — pi harness 适配物(单一源)

**设计权威**: LOOP-REDESIGN v2 §4;AIPOS-C4B 大项C 目录重排。

本目录存放 **pi 编码代理 harness 的适配物**——连接器 lybra-loop 归此。
与 `agents/skills/`(角色无关 skill 内容)与 `agents/roles/`(角色契约)分开:
- skills 是角色无关内容;roles 是角色契约;**harness/pi 是引擎适配**。

## 结构

```
harness/pi/
└── lybra-loop/          # Loop 连接器与引擎(pi 扩展本体)
    ├── gate-client.ts   # Gate MCP 客户端
    ├── loop-context.ts  # LoopContext 解析
    ├── loop-decisions.ts# 决策逻辑
    ├── loop-engine.ts   # 主引擎
    ├── lybra-loop.ts    # Pi 扩展入口
    └── tests/           # 单元测试
```

## 分发

分发器按 (role, harness) 组装:本目录的连接器 + `agents/roles/<role>/` 契约 +
`agents/skills/`(按 roles.schema.json 装配清单)组装成工位 `_distributed/` 分发物。

工位挂载点(kiwiai-pi 侧薄挂载,由分发器写入)指向 `_distributed/extensions/lybra-loop/`。

## 所有权

这些代码属于 Lybra 项目。改动 = 产品仓出卡执行,不得直接在 harness 侧修改。
