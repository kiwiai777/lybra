---
name: lybra-executor
description: Lybra 接活模式(executor)。Use when the user says "lybra on"(不带斜杠——进入接活模式:轮询 Lybra 有无可认领任务)、"lybra off"(退出接活模式),或让你去 Lybra 领任务/接活/看有没有活。Lybra is the connected party — you (the agent) pull; Lybra never pushes, schedules, or wakes you.
---

# lybra-executor — 接活循环(agent 侧)

你是连接 Lybra 的 executor agent。**Lybra 永远是被连接方**:所有询问都是你发起的
无状态 pull;Lybra 不推送、不定时、不唤醒你,也**从不记录你在线与否**(它的
`/agents` 视图只显示"as recorded — not live" 的已记录快照)。

**触发语是 `lybra on` / `lybra off`(纯文本,不带前导斜杠)**——不是 `/lybra on`。
Claude Code 的斜杠命令严格按已注册命令名解析,本 skill 的可调用名是它的目录名
`lybra-executor`(即 `/lybra-executor` 可用),但没有单独注册 `lybra` 这个命令名;
打 `/lybra on` 会被当成未知斜杠命令直接报错,不会退回到这份 skill 的自然语言匹配。
纯文本 `lybra on` 才能命中本 skill 的 description 匹配。

## 前提(一次性配置)

1. Owner 已在本机跑 `lybra serve`(gate,非 daemon,Owner 显式启停)。
2. 你有 executor 角色 token:环境变量 `LYBRA_MCP_TOKEN` 或 connection.json
   (默认 `~/.lybra/local/connection.json`;token 永远不出现在命令行/日志里)。
   (**F-249-o3-1**:token 环境变量名统一为 `LYBRA_MCP_TOKEN`——三角色都用这个名,本会话
   设 executor token 值;旧文档的 `LYBRA_EXEC_TOKEN` 已废。)
3. 你知道自己的 actor 名(与任务卡 `assigned_to`/`agent_instance` 对得上——
   任务必须先由 Owner 指派给你,你才可认领)。
4. 自检:下面命令能返回三态之一即通:

```bash
lybra agent fetch --gate-url http://127.0.0.1:7118 \
  --connection-json ~/.lybra/local/connection.json \
  --role executor --actor <你的 actor 名>
```

## 工具自检(先于一切;fetch 前先确认 MCP 挂载好)

`lybra agent fetch|watch` 只是"找活"的瘦客户端命令——真正**认领/return/confirm**
走的是 Lybra 的 MCP 工具(`lybra_queue_claim_dry_run` 等)。如果上面的自检命令能跑通
(fetch 成功返回三态之一),但你在自己的工具列表里**看不到 `lybra_queue_*` 系列 MCP
工具**,说明 gate 还没挂到你的 MCP 客户端——自行完成挂载:

```bash
# executor token 放进 LYBRA_MCP_TOKEN 环境变量(不落命令行);url 是位置参数,bearer 走 --header
export LYBRA_MCP_TOKEN="$(<读出 executor 角色 token>)"
claude mcp add lybra --transport http http://127.0.0.1:7118/mcp \
  --header 'Authorization: Bearer ${LYBRA_MCP_TOKEN}'
```

(token 本体放进 `LYBRA_MCP_TOKEN` 环境变量,不要粘贴进命令行参数或聊天记录里;`--header`
用单引号,让 `${LYBRA_MCP_TOKEN}` 存为字面量、由 cc 运行时展开。)
挂载后**自检**:确认工具列表里出现 `lybra_queue_return`(或任一 `lybra_queue_*`)——
看到了才算真正接通,再继续下面的 `lybra on` 流程。

## `lybra on`(进入接活模式;纯文本,不带斜杠)

1. 前台跑有界轮询(命中或到 max-wait 自动退出;这是你会话里的进程,不是驻留服务):

```bash
lybra agent watch --gate-url http://127.0.0.1:7118 \
  --connection-json ~/.lybra/local/connection.json \
  --role executor --actor <你> --interval 60 --max-wait 1800
```

2. 按输出的三态行动(每态自带下一步引导):
   - **"你已持有 <task>"** → 一 session 一 task:先完成/return 手头任务,不接新活。
   - **可认领列表** → 注意:**列表是建议,门才是真相**(这只是按 assigned_to 匹配的
     咨询性预过滤;认领资格最终由 gate 校验)。向用户复述任务、确认后走 supervised
     claim:调 `lybra_queue_claim_dry_run`(actor=你,agent_instance=你的 canonical
     实例,autonomy_mode=Supervised,**必带 active_session_id=<当前会话标识>**)→
     把 dry-run 结果报给 Owner,由 Owner 在 TUI `/confirm`(OOB)放行。
     **你永远不能自己 confirm——executor token 没有那个 scope,SCOPE_DENIED 是结构,
     不是故障。**
   - **"暂无可认领"** → watch 会继续等;超时退出后告知用户,询问是否重进。
3. claim 成功 → 本会话绑定该任务直到 return/complete;期间不再跑 watch、不接新活。

## 任务间上下文卫生(Owner 明裁,必须遵守)

**return/complete 完成一单之后、领下一单之前,先清任务上下文**——在 Claude Code 里
就是跑 `/clear`(其它 harness 用等价的新会话/清上下文动作)。一单一净上下文:上一单
的推理残留不得带进下一单。Lybra 管不到你的记忆,不会也无法强制这一条——fetch 列新
任务时会附提示行,但**执行靠你自觉,这是纪律不是机制**。

## `lybra off`(离线;纯文本,不带斜杠)

停止跑 watch 即离线。没有任何注销动作、没有任何状态要清——Lybra 从不把你的在线/
离线当成真相记录。

## 诚实红线(不可越)

- 你只 pull;循环宿主是你这侧的前台有界进程(自启 + 前台 + 有界,缺一不可)。
- 提示不等于认领:watch/fetch 只读;claim/return 全走 dry-run → Owner OOB confirm。
- 一 session 一 task;claim 必带 active_session_id;任务间 /clear。
- 你无法也不应尝试绕过任何 SCOPE_DENIED——那是 ★A1 结构,不是权限申请入口。
