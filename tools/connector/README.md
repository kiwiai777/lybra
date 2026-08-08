# tools/connector — Lybra 连接器(anti-drift)

AIPOS-CONN-1 spike 产物:在 pi harness 层把"未认领就写"当场拦下(edit/write/bash 执行前,
不是 push 后)。真相 = 文件即真相:读 `<workspace>/5_tasks/records/sessions/<TASK>/` 的
`session_status`,经 pi session → active claim 的显式绑定判定放行/阻断。

详见 **[SPIKE-REPORT.md](./SPIKE-REPORT.md)**(覆盖率矩阵、逃逸口、完整连接器可行性)。

## 结构

```
tools/connector/
├── claim-check.ts      # shared:harness 无关 —— 活跃认领判定(纯 fs)
├── write-ledger.ts     # shared:harness 无关 —— 写操作飞行记录(JSONL)
├── pi/
│   └── write-guard.ts  # pi 薄胶水:tool_call 拦截 + /connector-bind 命令
├── test/
│   └── claim-check.test.ts  # shared 单测(node 直跑,8 用例)
└── SPIKE-REPORT.md
```

## 快速使用

```bash
# 1. 跑 shared 单测(Node ≥22 类型剥离,无需 npm install)
node tools/connector/test/claim-check.test.ts

# 2. 认领一张任务卡后(gate 已落地 session_status: claimed),在 pi TUI 绑定:
#    /connector-bind <task_id>
#    之后 write/edit/bash写命令 放行并落写账;未绑定则当场 BLOCK。

# 3. 临时加载扩展验证(不改任何仓):
pi -e tools/connector/pi/write-guard.ts
```

## 配置(env,均有默认)

| env | 默认 | 说明 |
|---|---|---|
| `LYBRA_WORKSPACE_ROOT` | `~/ai-project-os/2_projects/lybra` | gate workspace 根 |
| `LYBRA_AGENT_INSTANCE` | `exec.lybra.kiwiai-dev` | 校验 claim 归属 |
| `PI_CONNECTOR_DIR` | `~/.pi/agent/connector` | binding + 写账目录 |

binding 落 `~/.pi/agent/connector/bindings/<pi-session-key>.json`;
写账落 `~/.pi/agent/connector/write-ledger.jsonl`。

## 边界(诚实)

- **拦得住**:write / edit / bash 显式写命令(git、重定向、mv/cp/rm 等)。
- **拦不住**:`node -e` / `python -c` 等 bash 内任意代码执行(固有逃逸口,需受控写路径闭环)。
- 本 spike 只验"拦截"一环;自动 claim/return 留下一阶段。
