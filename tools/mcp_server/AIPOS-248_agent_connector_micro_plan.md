# AIPOS-248 — Slice 连接器:agent 侧那半张脸(`/lybra on|off` 接活循环)(微片)

- **Status**: APPROVED(R 方向审计 PASS + 六钩裁定 + R-a/R-b/R-c 折入,Owner 2026-07-10
  "折入后视为 Owner APPROVED,进实现")
- **Authority**: 实现授权(不 commit——收口后候 cc 增量审计 → Owner O3 → finalize 授权)
- **Parent 裁定**:
  - **A**(Owner 2026-07-07,v1.0-REQUIRED):agent 连接器进 v1.0。形态 = agent 侧循环,
    agent 自己开关:`/lybra on` = 接活模式(自动询问 Lybra 有无可认领任务),`/lybra off` =
    离线。**Lybra 永远是被连接方**。
  - **B**(Owner 2026-07-09):交付形态 = homerail 式 `skills/` 目录
    (`skills/lybra-executor/SKILL.md`,带"何时使用"frontmatter,可软链进
    `~/.claude/skills` 与 `~/.codex/skills`;README 一句"把这份 SKILL 给你的 agent")。
  - **C**(Owner 自动化哲学,评审判据):自动化是追求,前提可控可审——每个设计点问两条:
    ①归因仍清晰(agent 拉、行为可归属)?②判断仍集中 Owner 门?
- **队列位置**:连接器(本片)→ planner 角色片 → W 终审(届时按新 hookup 方式重写)。
- **定位**:纯增量——1 个角色无关瘦客户端子命令 + 1 份 SKILL.md 交付物;
  gate/tools.py/state.py/校验语义**零改动**;零新增服务端状态。

---

## §0 真实现状台账(executed-✓,全部 file:line 落地核实)

| # | 事实 | 出处 |
|---|---|---|
| 1 | 服务角色 5 个:`executor`(scopes `["queue_claim","queue_return"]`)/`owner`/`owner-dispatch`/`auditor`/`copilot`(scopes `[]`) | `tools/aipos_cli/service_mode.py:41-78` |
| 2 | 只读工具默认暴露、无需 scope(`READ_ONLY_NOTICE`);**"有哪些任务可认领"的读面 = `lybra_queue_list`**(→ 后端 `get_queue` → `validate_tasks` 全量报告),每 task 带 `queue_state`(pending/claimed/completed/blocked)+ `metadata`(`assigned_to`/`agent_instance`/`claimed_by`/`status`/`needs_owner`) | `tools/mcp_server/tools.py:32,1730-1737,806-808`;`tools/aipos_cli/board_adapter.py:424-434` |
| 3 | **无 `claimable` 布尔字段**——可认领性是客户端派生的(既有先例:`GateClient.list_confirm_gates` 由 `queue_state=="pending"` 派生) | `tools/aipos_cli/confirm_client.py:173-189` |
| 4 | supervised claim 链路(不动):`lybra_queue_claim_dry_run`(required: `actor`,`agent_instance`,`autonomy_mode="Supervised"`,`owner_policy_ref`;可选 `active_session_id` 等)→ `lybra_queue_claim_confirm`(需 `queue_claim`+`owner_confirm` 双 scope + 字面量 `OWNER_CONFIRMED`)。executor token 无 `owner_confirm` → **结构性不能自确认(★A1)** | `tools.py:1941-1988,1026,1036,1079-1084,1109-1129` |
| 5 | "actor 必须匹配 assigned_to/agent_instance/claimed_by 才可认领"由 validator 强制(不匹配 → blocking → dry-run verdict BLOCK) | `tools/aipos_cli/validator.py:357-374`;`tools/aipos_cli/queue_mutation.py:571-589` |
| 6 | **现成角色无关瘦客户端已存在**:`GateClient(base_url, token)`(streamable-HTTP `/mcp`,`Authorization: Bearer`,`Mcp-Session-Id`)+ `load_owner_token(connection_json/role/token_env)`(token 不上 argv、fingerprint-only)+ `queue_tasks()`(经 `lybra_queue_list`,不读文件)。TUI 的 `TuiSession` 即其薄封装 | `tools/aipos_cli/confirm_client.py:118-238,62-83,167-171`;`tools/lybra_tui/state.py:34-58` |
| 7 | Form B 接入现状(codex):`codex mcp add lybra --url <gate>/mcp --bearer-token-env-var LYBRA_MCP_TOKEN`;教程 `docs/mcp-agent-setup.md`(整篇);README 无 skills 先例,**仓库内无任何 SKILL.md/skills/ 目录**(交付物全新) | `AIPOS-201_...md:133`;`AIPOS-202_...md:26-32,58-63`;`docs/mcp-agent-setup.md:1-74`;find 实测零命中 |
| 8 | `lybra` CLI 接线点:npm `bin/lybra` → `tools/aipos_cli/aipos_cli.py`;新子命令 = `add_parser`(:698 起)+ `main()` 派发分支(:1015-1690)两处 | `bin/lybra:21`;`aipos_cli.py:698,1015-1692` |
| 9 | **"一 session 一 task"在册**:policy "claim one task per session";claimed 任务必须有 `claim_id/claimed_by/claimed_at/active_session_id`(validator 强制);claim 落盘 session record(`record_type: session_record`,含 `session_id/task_id/actor/claim_id`) | `0_control_plane/tasks/task_session_policy.md:172`;`task_session_lease_runtime_binding_policy.md:20`;`validator.py:399-447`;`record_writer.py:347-379,464-505` |
| 10 | **禁 liveness-as-truth 的既有先例(红线 2 同源)**:/agents = "as recorded — Lybra does not track live presence",无 polling/heartbeat/auto-refresh,测试固化 | `tools/lybra_tui/agents_view.py:1-29`;`app.py:905-919`;`agent_profiles.py:132`;`test_agents_view.py:112,126-127` |
| 11 | 交付形态机制实证:cc 侧 `~/.claude/skills/<name>/SKILL.md`(frontmatter `name`+`description` 含 "Use when...",本机 checkauditor/checktask 即此形态、真实触发可用);codex 侧 `~/.codex/skills/` 存在且系统技能同为 `<name>/SKILL.md` 形态(skill-installer 等 5 个实测在盘);codex 无 `~/.codex/prompts` | 本机 `ls`/`head` 实测 |

## §1 硬红线(Owner 四条,验收逐条查 + 两条派生)
1. **Lybra 只交付端点 + 瘦客户端,绝不交付常驻 daemon 去驱动 agent**——循环宿主 = agent
   侧进程(agent 在自己 shell 里起的前台客户端进程),不是 Lybra;gate 仍是 Owner 显式
   `lybra serve`(非 daemon,AIPOS-202 既有边界)。
2. **每次询问 = 无状态 pull**("有没有我可认领的任务?"= 一次全新 `lybra_queue_list`);
   Lybra **不得**把"agent 在线/离线"记录或展示为真相(禁 liveness-as-truth / 心跳)——
   与 §0-10 /agents "as recorded" 先例同源,反向测试见 §5。
3. **on 可自动 fetch + 提示,claim 仍是 agent 主动行为**:supervised claim 照旧
   dry-run → Owner OOB confirm(§0-4 链路字节不动);Lybra 永不 push/定时/唤醒
   (服务端零 timer/零回调/零推送面)。
4. **瘦客户端角色无关**:planner 片(只读真相 + draft-submit 第三 token 角色)复用同一
   客户端,只换 token/scope——接口不写死 executor(参数化 role + 过滤谓词;见 §4-S1)。
5. (派生)`tools.py`/`state.py`/gate 校验/scope 语义零 diff;**新增读面清单 = 空**
   (Q2 落地核实现有 `lybra_queue_list` 够用,§3)。
6. (派生)**on/off 不是任何一侧的持久状态**:不落盘、不注册、不展示——"on"只是 agent
   会话正在跑 watch/按轮 fetch 这个行为本身;off = 停止该行为。Lybra 侧无感知。

## §2 架构(循环宿主 / 端点边界)

```
┌────────────────────── agent 侧(循环宿主)──────────────────────┐
│  cc / codex 会话                                               │
│  ┌───────────────────────────────────────────────┐            │
│  │ SKILL.md(教程面,Q1):/lybra on|off 的行为契约 │            │
│  └──────────────┬────────────────────────────────┘            │
│                 │ agent 在自己 shell 里起前台客户端进程         │
│                 ▼                                              │
│  lybra agent fetch|watch(实现面,Q1;瘦客户端,角色无关)       │
│   · fetch = 一次无状态 pull;watch = 客户端侧有界循环          │
│     (interval/max-wait/退避全在客户端,Q2;命中即退出)         │
│   · 只读、只提示,永不 claim                                   │
└─────────────────┬──────────────────────────────────────────────┘
                  │ streamable-HTTP POST /mcp
                  │ Authorization: Bearer <role token>(env/connection.json)
                  ▼
┌────────────────── Lybra 侧(被连接方,端点)────────────────────┐
│  lybra serve(Owner 显式启动,非 daemon)                       │
│  · 读面:lybra_queue_list(现有,零新增)── 无状态应答          │
│  · 写门:claim dry-run → Owner OOB confirm(现有 ★A1,字节不动)│
│  · 零 liveness 状态/零 timer/零推送                            │
└────────────────────────────────────────────────────────────────┘
```
认领动作(agent 主动,SKILL.md 教):现有链路原样——dry-run(带 `active_session_id`)→
Owner 在 TUI `/confirm`(OOB)→ 记录落盘(claim record + session record,§0-9)。

## §3 开放子问题答案(附落地证据)

### Q1 — `/lybra on|off` 落在哪个面:**并存,CLI 是实现、slash 是教程**(证据 §0-8/11)
- **实现面 = `lybra` CLI 新子命令组**(接线点 §0-8 实证):agent 在 shell 里跑
  `lybra agent fetch ...`(单次)/ `lybra agent watch ...`(有界循环)。理由:cc/codex
  都能跑 shell;CLI 进程由 agent 会话起、随会话终——循环宿主天然在 agent 侧(红线 1)。
- **slash 面 = SKILL.md 教程**:Lybra 无法向 cc/codex 注入 slash 命令(落地核实:cc 的
  自定义命令面 = `~/.claude/skills/<name>/SKILL.md`,frontmatter `description` 写
  "Use when the user says /lybra on …" 即可被 `/lybra-executor` 或自然语言触发——本机
  checkauditor 同型实证;codex 同吃 `~/.codex/skills/<name>/SKILL.md`,系统技能实测在盘;
  codex 无 prompts 目录)。SKILL.md 教 agent:听到 `/lybra on` → 起 watch;`/lybra off` →
  停;拿到任务 → 走 supervised claim。**两面各司其职,无第三态**。

### Q2 — 轮询间隔宿主与配置 + 读面核实:**间隔只活在客户端;读面零新增**(证据 §0-2/3/6)
- **宿主**:间隔/上限/退避全部是 `lybra agent watch` 的客户端参数——服务端零配置零感知:
  - `--interval`(默认 60s,**下限 15s 硬 floor**——保护 gate,低于报错不悄悄抬高);
  - `--max-wait`(默认 30min,到点无任务 → 干净退出码 0 + "no task" 输出,agent 决定是否
    重进——循环有界,不是常驻);
  - 连接失败退避:指数 ×2 至 5min 封顶,成功即复位;每次失败如实打印(不静默重试)。
- **读面核实**:`lybra_queue_list` 已携带判定所需全部字段(`queue_state` +
  `metadata.assigned_to/agent_instance/claimed_by`,§0-2)。"可认领(对我)"= 客户端派生
  谓词:`queue_state == "pending"` **且** actor ∈ {assigned_to, agent_instance}(镜像
  validator.py:357-374 的匹配集合,仅作**预过滤提示**——强制仍在 gate 的 dry-run 校验,
  客户端谓词错了最多多问一次、绝不会绕过门)。派生式过滤有既有先例(§0-3)。
  **→ 新增读面清单:空。**
- 每次 pull = 全新 `queue_tasks()` 调用(复用 §0-6 的 `GateClient`),无 session 粘性
  状态(`Mcp-Session-Id` 是 MCP 传输握手,非业务状态;watch 每轮可重建连接)。

### Q3 — 会话↔任务绑定在自动 fetch 下的呈现(证据 §0-9)
- **机制侧**:fetch/watch 的输出分三态,把在册纪律变成第一眼提示:
  1. 我已持有 claimed 任务(`queue_state=="claimed"` 且 claimed_by/agent_instance = 我)
     → 输出"**你已持有 <task_id> —— 一 session 一 task,先 return/complete 再接新活**",
     并**不列出**其它可认领任务(抑制诱惑,纪律前置);
  2. 无持有 + 有可认领 → 列出(task_id/title/assigned_to),并提示"认领时带
     `active_session_id`(当前会话标识),claim 后本会话绑定该任务";
  3. 无持有 + 无可认领 → "no task"(watch 继续等)。
- **SKILL.md 教学侧**(骨架 §4-S2):明写四条——①一次只认领一个,claim dry-run 必带
  `active_session_id`;②持有期间 `/lybra on` 不再接新活(fetch 会拦);③return/complete
  后才恢复接活;**④(R-b,Owner 明裁 v1.0)任务间上下文卫生:return/complete 完成后、
  领下一单前,先清任务上下文(cc = `/clear`)——一单一净上下文;Lybra 不强制(管不到
  agent 记忆),fetch 列新任务时附提示行**。validator 的 `active_session_id` 强制(§0-9)
  是兜底真相,SKILL 只是把它教在前面。
- **(R-c)三态输出各自带 P-A 引导**:态1 末行"→ 先 return/complete(工具:queue_return
  dry-run → Owner confirm),然后 /clear 再回来";态2 末行"→ 确认后走 claim dry-run(带
  active_session_id);列表是建议,门才是真相;若你刚完成上一单,先 /clear";态3 末行
  "→ 暂无可认领;watch 会继续等(上限 max-wait),或 /lybra off 离线"。

## §4 Scope(4 条)

### S1 — `lybra agent` 子命令组(瘦客户端,角色无关)
- `tools/aipos_cli/aipos_cli.py`:`agent` 子命令组(与现有 `agents`——渲染 profiles——
  语义区分,命名候 R 裁定,备选 `connect`):
  - `lybra agent fetch --gate-url <u> [--connection-json <p> | --token-env <E>] --role executor --actor <name> [--json]`:一次无状态 pull → 三态输出(§3-Q3);
  - `lybra agent watch <同上> --interval 60 --max-wait 1800`:客户端有界循环,命中即打印
    并退出(exit 0),超时退出(exit 0 + no task),中断干净退出;
- 实现 = 复用 `GateClient` + `load_owner_token`(§0-6,token 不上 argv、fingerprint-only
  纪律继承);**角色无关**:`--role` 直通 connection.json 角色表/env,过滤谓词按 role 参数化
  (executor 谓词见 §3-Q2;planner 片换谓词/换 token,客户端 API 不含 "executor" 字样);
- **永不 claim**:fetch/watch 无任何写工具调用路径(测试钉死,§5)。

### S2 — `skills/lybra-executor/SKILL.md` + README 一句
- 仓库新增 `skills/lybra-executor/SKILL.md`(骨架):

```markdown
---
name: lybra-executor
description: Lybra 接活模式(executor)。Use when the user says /lybra on(进入接活模式:
  轮询 Lybra 有无可认领任务)、/lybra off(退出接活模式),或让你去 Lybra 领任务/接活。
  Lybra is the connected party — you (the agent) pull; Lybra never pushes or wakes you.
---
# lybra-executor — 接活循环(agent 侧)

前提(一次性):Owner 已 `lybra serve`;你有 executor token(env `LYBRA_EXEC_TOKEN`
或 connection.json)。verify: `lybra agent fetch --gate-url ... --role executor
--actor <你的 agent 名>` 能返回三态之一。

## /lybra on
1. 跑 `lybra agent watch --gate-url ... --role executor --actor <你> --interval 60
   --max-wait 1800`(前台;命中/超时自动退出)。
2. 输出"你已持有 <task>" → 遵守一 session 一 task:先完成/return 手头任务,不接新活。
3. 输出可认领任务 → 向用户复述任务,确认后走 supervised claim:
   `lybra_queue_claim_dry_run`(actor=你,agent_instance=你的 canonical 实例,
   autonomy_mode=Supervised,active_session_id=<当前会话标识>)→ 把 dry-run 结果报给
   Owner,由 Owner 在 TUI /confirm(OOB)。你**永远不能**自己 confirm(没有那个 scope)。
4. claim 成功 → 本会话绑定该任务直到 return/complete;期间不再跑 watch。
5. 超时无任务 → 告知用户,询问是否继续等(重跑 watch)。

## /lybra off
停止跑 watch 即离线。没有任何注销动作——Lybra 从不记录你在线与否(as recorded, not live)。

## 诚实红线
- 你只 pull;Lybra 不 push、不定时、不唤醒你。
- claim/return 全走 dry-run → Owner confirm;SCOPE_DENIED 是结构,不是故障。
- 一 session 一 task;active_session_id 必带。
```

- 软链交付:README "Scope & limits/Quick start" 加一句"**把这份 SKILL 给你的 agent**:
  `ln -s $(pwd)/skills/lybra-executor ~/.claude/skills/lybra-executor`(codex:
  `~/.codex/skills/`)";`docs/mcp-agent-setup.md` 交叉引用一行。

### S3 — 测试(见 §5)
### S4 — 披露
- `docs/v1_disclosure.md` 新 row:连接器是 pull-only + 客户端循环;Lybra 不记录 agent
  在线状态(as recorded 先例同源);on/off 非持久状态;间隔/退避是客户端参数;
  **(钩1)一行区分:`/agents` = 已记录快照,`agent watch` = 客户端循环**;
  **(钩3)客户端可认领列表是咨询性预过滤(有疑偏宽),gate 门才是真相**;
  **(R-b)任务间上下文卫生靠 SKILL 教学 + 客户端提示,Lybra 不强制(管不到 agent 记忆)**。

## §5 测试策略(含红线 2 反向测试;RED 纪律照旧)
- **S1 fetch 三态**(mock GateClient.queue_tasks):①claimed-by-me → 纪律提示 + 不列新任务
  (**RED 设计**:对"直接列出全部 pending"的朴素实现红);②pending+actor 匹配 → 列出;
  ③无 → no task。actor 匹配集合与 validator.py:361-365 镜像(注释对账)。
- **S1 watch 有界性**(注入 fake sleeper/clock,零真实等待):interval floor(<15s 报错)、
  max-wait 到点退出、失败退避 ×2 封顶 5min + 成功复位、命中即退出;**循环体内零写工具
  调用**(spy 断言 call_tool 只见 `lybra_queue_list`)。
- **红线 2 反向测试(服务端零 liveness 落盘)**:起真 gate(复用 transport 测试基建),
  同一 token 连续 N 次 fetch → 断言:①workspace 树 **字节不变**(全树 hash 前后对账——
  正内容断言,非 proxy);②`lybra_queue_list` 应答中**无** online/presence/heartbeat/
  last_seen 字段(键名负向断言);③gate 进程无新增 timer(读码钉:新增代码 grep 无
  set_interval/Timer/threading.Timer)。
- **红线 4 角色无关钉**:客户端 API 以 `--role planner` 构造同样能走到 queue_tasks
  (token 校验层报错与否属 gate,客户端不含角色白名单);grep 钉:`agent fetch/watch`
  实现文件无 "executor" 硬编码(谓词表除外)。
- **SKILL.md 钉**:frontmatter 可解析(name/description 存在)、description 含
  "/lybra on";README 含软链一句。
- 四路串行 + `/tmp/.git` 跑前查跑后清;`tools.py`/`state.py` 零 diff 钉。

## §6 O3 走查设计(真实 cc 当 executor 跑全环)
0. 前置:`ln -s .../skills/lybra-executor ~/.claude/skills/`;o3-launch 起 gate;
   fixture 任务 assigned_to = cc 的 actor 名(§0-5:须有 assigned_to 才可认领)。
1. cc 会话说 `/lybra on` → skill 触发 → cc 起 `lybra agent watch`(观察:前台进程,
   Owner 可 ps 核实**无 Lybra 侧新进程**——红线 1 眼验;**钩2 三件套逐项核:agent 自启 ✓
   + 前台 ✓ + 有界[命中/30min 即退]✓,缺一不可**);
2. Owner 放入 fixture 任务 → watch 在 ≤interval 内提示(自动 fetch+提示,红线 3 前半);
3. cc 走 claim dry-run(带 active_session_id)→ **停在 Owner 门**;Owner TUI `/confirm`
   (OOB)→ claim 落盘,records 归因核对(actor/agent_instance/claim_id/active_session_id
   ——哲学问 ①归因清晰);
4. cc 再说 `/lybra on` → fetch 输出"你已持有…一 session 一 task"(Q3 呈现眼验);
4b. **(R-b 卫生眼验)**cc 完成/return 该任务 → `/clear` 清上下文 → 重新 `/lybra on`
   领下一单——SKILL 教学与 fetch 提示行都出现,cc 实际执行了 /clear;
5. `/lybra off` → cc 停 watch;Owner 侧 TUI `/agents` 仍 "as recorded — not live",
   **看不到任何在线/离线痕迹**(红线 2 眼验);工作区 diff = 仅 claim 产生的 records;
6. (哲学问②)全程唯一放行点 = Owner `/confirm`——除此无一步自动越门。

## §7 Owner 自动化哲学对照(判据 C,逐设计点)
| 设计点 | ①归因清晰? | ②判断集中 Owner 门? |
|---|---|---|
| watch 自动 fetch | 每 pull 带 Bearer role token(fingerprint 可稽),无匿名读 | 读面无判断可言(只读) |
| 可认领提示 | 谓词=assigned_to 匹配(Owner 指派即归因起点) | 提示≠认领 |
| claim | actor+agent_instance+active_session_id 三重落盘(§0-9) | dry-run → Owner OOB confirm(★A1 字节不动) |
| on/off | 无状态,无归因对象(行为即状态) | 无门可越 |

## §8 给 R 的钩子
1. 子命令命名:`lybra agent fetch|watch` vs 既有 `agents`(profiles 渲染)是否够区分?
   (备选 `lybra connect fetch|watch`。)
2. watch 的"有界循环"(max-wait 默认 30min,退出后由 agent 决定重进)是否足以满足红线 1
   的"非常驻"?或应更短默认?
3. 客户端"可认领"谓词镜像 validator 匹配集合(§3-Q2)——预过滤提示 vs 单一真相源的边界
   这样划是否可接受(谓词永不绕门,错了只多问)?
4. SKILL.md 单份 executor 角色先行、planner 片再加 `skills/lybra-planner/`,还是本片就
   把 SKILL 骨架参数化为角色模板?(红线 4 只约束客户端,SKILL 是教程层。)
5. 红线 2 反向测试的三断言(树 hash/键名负向/grep 零 timer)是否足以钉死"零 liveness"?
6. `--interval` 15s 硬 floor 与 60s 默认是否合适(gate 是本机 loopback,负载极轻,但
   护栏宁紧勿松)?

## §8.5 R 方向审计折入(PASS,六钩裁定 + 三条折入;Owner 2026-07-10,折入即 APPROVED)

**六钩裁定**:
1. **命名** → ✅ 无硬冲突(CLI 现有子命令无 `agent`;与 TUI `/agents` 仅概念相邻)。
   **保留 `lybra agent watch`**;披露一行区分:`/agents` = 已记录快照,`watch` = 客户端循环。
2. **有界循环 vs 非常驻** → ✅ **非常驻的保证 = agent 自启 + 前台 + 有界 三件套,缺一
   不可,写进验收**(§6 O3 增列);命中即退 + 30min 上限设计确认。
3. **谓词镜像** → ✅ 接受,定性为**咨询性预过滤**:强制永远在 gate。两个要求:
   ①镜像谓词对 **validator fixture 矩阵做一致性钉**(§5 增测);②写明失败方向——
   **谓词过宽 = gate 拒(安全、响亮);过窄 = 静默漏任务(更糟)→ 镜像有疑时偏宽**;
   SKILL 里如实披露"列表是建议,门才是真相"。
4. **SKILL 形态** → ✅ 现在只写 executor 单份;planner 片就在下一站,共用段落到那时再抽
   (**rule of two 正好触发**),不预先抽象。
5. **零 liveness 断言** → ✅ 三断言够,**再加一条便宜的正面探针:队列不变时,连续两次
   fetch 的响应字节一致**(无状态的正面证明)(§5 增测)。
6. **interval** → ✅ 60s 默认 / 15s floor / 30min cap 对本地单用户合理;**不加 jitter,
   注明原因(单用户无 thundering herd)**(代码注释 + 卡)。

**折入三条**:
- **R-a**:queue_list 字段证据(§0-2)换成**真实执行的响应样本入卡**(实现期起真 gate
  采样,见 §9 实现记录)。
- **R-b(Owner 2026-07-10 明裁 v1.0 需求,DRAFT 漏项)**:**任务间上下文卫生**——
  `/lybra on` 常驻真 agent 在 return/complete 完成后、下次 fetch/claim 前**须清任务上下文
  (cc = `/clear`)**。落点 = SKILL.md 教学 + return 后客户端提示(fetch 列可认领任务时
  附卫生提示行);**Lybra 不强制**(管不到 agent 记忆,如实披露)。并进 Q3。
- **R-c**:watch/fetch 循环的**三态输出各自带 P-A 引导**(在册原则,顺手自检:每态输出
  末尾一行"你在哪/下一步敲什么")。

## §9 实现记录(2026-07-10)

### 改动清单(不 commit,候审计 + O3)
- **新增 `tools/aipos_cli/agent_connector.py`**(纯客户端,stdlib + 复用 confirm_client):
  `actor_matches`(validator 匹配集合的咨询性镜像,偏宽)/ `classify`(三态,held 抑制新单)/
  `render`(R-c P-A + R-b 卫生提示)/ `fetch_once`(唯一工具通道 = `GateClient.queue_tasks()`)/
  `run_watch`(前台有界:15s floor 报错不抬高、max-wait 到点净退、失败退避 ×2 封顶 300s
  成功复位、失败如实打印、全败到界 exit 2;**无 jitter——单用户本地 gate 无 thundering
  herd,加 jitter 反而难审计**,R 钩6)。
- `tools/aipos_cli/aipos_cli.py`:`agent` 子命令组接线两处(parser + main 早段派发,
  零 workspace 依赖);与既有 `agents` 的区分注释 + 披露(钩1)。
- **新增 `skills/lybra-executor/SKILL.md`**(B 裁定形态):frontmatter description 含
  "/lybra on" 触发语;教学含 supervised claim 全链、一 session 一 task、**R-b 任务间
  /clear 卫生(明写"纪律不是机制,Lybra 管不到 agent 记忆")**、"列表是建议,门才是真相"
  (钩3)、as-recorded、SCOPE_DENIED 是结构。
- `README.md` "Hook up an agent" 段(软链一句,cc + codex 双路径);
  `docs/mcp-agent-setup.md` 头部交叉引用;`docs/v1_disclosure.md` **row 13**
  (pull-only/on-off 非状态/卫生 discipline-held 如实标注/钩1 区分行/钩3 咨询性)。
- **零改动**:`tools.py`/`state.py`/`validator.py`/gate 语义/`lybra_tui`(TUI 路 182 不变)。
  **新增读面 = 0**(Q2 核实兑现)。

### R-a — 真实执行的 queue_list 响应样本(fixture gate + executor token 实测)
每 task 顶层键(全集实录):`actor_match, agent_instance, assigned_to, authority_findings,
authority_verdict, blocking_reasons, body, claimed_by, classification_warnings,
complexity_note, effective_task_class, effective_truth, frontmatter_status, metadata,
model_tier, needs_owner, needs_owner_reasons, parse_errors, path, queue_state,
recommended_action, record_links, record_ref_checks, records, repo_root, status,
status_consistent, task_class, task_class_explicit, task_id, task_mode, title, verdict,
warnings, workflow_suggestion`。节选:
```json
{"task_id": "AIPOS-CONN-FETCH", "queue_state": "pending", "frontmatter_status": "pending",
 "verdict": "PASS", "metadata(节选)": {"assigned_to": "cc-exec-01",
 "agent_instance": "cc-exec-01", "status": "pending", "needs_owner": false,
 "session_policy": "single_task_session"}}
```
注记(如实):`assigned_to`/`agent_instance`/`claimed_by` 在顶层与 metadata **双份存在**;
镜像谓词读 metadata 侧(与 validator.py:361-365 同源),一致性由 §5 矩阵钉对真 validator
锁定。

### RED 纪律(新增模块的变体)
本片为全新模块,无"修前行为"可跑红;按 §5 RED 设计对 **held 抑制钉做变异实跑**:把
`classify` 换成"持有中仍列出全部 pending"的朴素变体 → 钉红,原文:
```
AssertionError: 'claimable' != 'held' … FAILED (failures=1)
```
(断言差异首行 `- claimable / + held`;真实现复原后同钉绿。)开发中途另有两枚**真实红**
如实入册:①validator fixture 缺 `parse_errors` 键 → KeyError(fixture 补齐);②结构性
只读钉误伤 P-A 引导文案里的工具名(教学非调用)→ 钉改为"模块零 `.call_tool(`/
`.preview(`/`.confirm(` + 唯一通道 `.queue_tasks()`"(更强:写路径源码层面不存在)。

### 测试(18 钉全绿)
三态 4(含 held 抑制 + R-c P-A 全态钉)/ 谓词矩阵一致性 1(8 组合对真 `validate_single_task`,
不窄于 gate)/ JSON slim 1 / watch 5(floor 报错、首拉命中、有界超时、退避倍增复位、
封顶 + 全败响亮退出;fake sleeper 零真实等待)/ 结构钉 2(零写零 timer、角色无关 ast 扫描
非 docstring 字符串)/ 真 gate 2(**红线2 三断言 + 钩5 正面探针**:3 次 pull 全树 hash
字节不变 + 载荷两两逐字节一致 + 无 liveness 词汇;三态对真 gate)/ 交付物 3(SKILL
frontmatter+教学七要素、README/setup 引用、披露 row)。

### Gate(实测,串行,F-245-env-1 纪律)
- 四路:**BARE 787 OK(106 skip)/ SYSTEM 787 OK / TUI 182 OK / ACCEPTANCE PASS**
  (787 = 247 收口基线 769 + 18;skip 106 不变——连接器钉零 textual 依赖,BARE 全跑;
  TUI 182 不变 = lybra_tui 零改动交叉证);跑前/跑后 `/tmp/.git` 净。
- 红线复核:`tools.py`/`state.py` 零 diff;循环宿主 agent 侧(watch 前台有界);
  服务端零新增状态/timer/读面;claim 链路字节不动。

**状态:实现收口,未 commit——候 cc 增量审计 → Owner O3 走查(§6,真 cc 跑
on→fetch→claim→off + R-b 卫生眼验)→ Owner 授权 finalize → planner 角色片 → W 终审。**

## §10 O3 收口轮(2026-07-11;O3 判定 = 连接器范围 PASS,四 finding 折入后 finalize)

### O3 实录(判定原文要义)
七检查点全过:watch 三件套(自启+前台+有界)/ held 抑制 / claim 停门 / 零 liveness /
off 干净;executor 为 **glm-5.2 的 cc**,在**毒夹具(O3-FX-4 divergence)+ 无 MCP 双重
逆境**下零越界。四件收口(先诊断后改,按 Owner 纪律):

### F-248-o3-1(先取证——确认为真 bug,已修)
**现象**:O3 实测 dave(仅 `assigned_to`)被判"已持有"O3-FX-4,实际 `claimed_by`/
`agent_instance`=carol。**读码结论**:`queue_mutation.py:203`(`_prepare_claim`)claim 时
**只写 `claimed_by = actor`**——这是已认领任务持有者的唯一地面真相;gate 自己的持有者
判定(`validator.py:420-427`)也**只认 `claimed_by`**,current_actor 不匹配即
**BLOCK**"Task is claimed by another actor"(硬拒,非 needs_owner 级别的软告警)。
**确认:真 bug**——`classify()` 此前对 held/claimable 两态复用同一宽谓词
`actor_matches`(含 assigned_to),把"预授权"和"实际持有"混为一谈。
**修复**:新增 `_is_holder(task, actor)`,**held 态专用**,只认 `claimed_by`;
`actor_matches`(assigned_to/agent_instance/claimed_by 宽 OR)保留给 claimable
(pending)态不变。**RED 复现**:错配 fixture(assigned_to=dave, claimed_by=
agent_instance=carol)→ 修前 dave 被误判 held,carol 反而不是;修后 dave→none,
carol→held。新钉:`test_o3_1_held_follows_claimed_by_not_assigned_to`。

### F-248-o3-3(cc 斜杠命名空间落地核实——已修)
**落地核实**(WebFetch 官方文档 `code.claude.com/docs/en/slash-commands`):"Custom
commands have been merged into skills. A file at `.claude/commands/deploy.md` and a
skill at `.claude/skills/deploy/SKILL.md` both create `/deploy`"——**skill 的可调用
命令名 = 其目录 basename**。本 skill 目录名是 `lybra-executor` → 可调用名是
`/lybra-executor`,**没有单独注册 `lybra` 这个命令**。cc 的斜杠解析器严格按已注册
命令名匹配,打 `/lybra on` 会被当**未知斜杠命令直接报错**,**不会**退回自然语言
skill-匹配。
**项目级 commands 文件核实结论**:`.claude/commands/<name>.md` 是**项目级**
(仅在该目录是 cc 的 CWD 时生效)——但 executor agent 的 CWD 是**它自己的目标项目**,
不是 Lybra 仓,放一份进 `skills/lybra-executor/` 之外的位置**对实际用例无效**
(不会被任何 executor 的 CWD 自动发现)。**决定:不追加项目级 commands 文件**,
只改教学(已有的用户级 `~/.claude/skills/` 软链 = 唯一可移植的正确交付形态)。
**修复**:SKILL.md frontmatter + 正文 + README + `docs/mcp-agent-setup.md` 全部改教
**纯文本 `lybra on`/`lybra off`(不带斜杠)**,并在 SKILL.md 里显式解释"为什么不带
斜杠"(教反例,帮 agent 理解而非死记)。Q1 答案(§3)按此更正:"并存"仍成立,但
"slash"那一半的实际落地形态是**自然语言短语**,不是真斜杠命令。

### F-248-o3-4(SKILL 补工具自检节——已修,与 o3-3 同一改动批次)
`skills/lybra-executor/SKILL.md` 新增"工具自检"节,插在"前提"与"`lybra on`"之间:
fetch 成功但工具列表看不到 `lybra_queue_*` 系列 MCP 工具时,自行执行
`claude mcp add lybra --url <gate-url>/mcp --bearer-token-env-var LYBRA_EXEC_TOKEN`
(token 走环境变量,不落命令行),挂载后自检 `lybra_queue_return` 等工具出现再继续。

### F-248-o3-2(TUI 顺路最小修——已修)
现象:TUI 裸 `/confirm`(无下标)遇 >1 待确认 gate 时,只打一句"N pending gates.
Use /confirm <n>."——不像 `/gates` 那样展示标题/归因。`/confirm` 是 Ruling 1 保留的
**备用执行面**,理应同型。**修复**:抽出 `_gates_list_lines()` 共享渲染,`_cmd_gates`
与裸 `/confirm` 多 gate 分支复用同一列表;`/confirm` 签名从 `int | None` 扩为
`int | str | None`(向后兼容既有 `_cmd_confirm(0)` 调用点),**量力顺带**支持
`/confirm <claim_id>` 字符串选择(按 return gate 的 `metadata.claim_id` 匹配)。
新钉 2:`test_f248_o3_2_bare_confirm_with_multiple_gates_renders_same_list_as_gates`
+ `test_f248_o3_2_confirm_accepts_claim_id_selector`。

### 夹具侧 F-launch-5/6(工具侧,`~/o3-launch.sh`,仓外,与 F-launch-4 同批)
- **F-launch-6(completion_report 引用悬空)**:TUI/CLI 返回流的默认
  `completion_report_ref`(`confirm_client.py:273` "reports/owner-confirmed-return.md")
  是自由文本元数据,gate 只查安全性不查存在性(`board_adapter.py:1751/1999-2001` 读码
  核实——**这是 gate 的正确设计**,非产品 bug)。但这意味着 O3 真机走一次真实 return
  后,该引用在一次性 disposable home 里指向空气。修:项目 scaffold 后预置真实占位文件
  `reports/owner-confirmed-return.md`,默认引用落到真文件。
- **F-launch-5(残骸卡)**:`seed_fixtures()` 收尾新增完整性断言——`queue/pending` +
  `queue/claimed` 必须**恰好**是 5 个已知夹具文件,多出任何文件(残骸)**响亮 FATAL**
  报出文件名并退出,而非静默放过(呼应本项目 fail-loud 纪律)。standalone 验证:
  正常态零误报;注入一个多余文件后精确抓出。
- 同批追加:`verify in the TUI` 清单加一行 **F-248-o3-1 回归验证**(`lybra agent fetch
  --actor dave.local` 应 none;`--actor carol.local` 应 held O3-FX-4)——把 O3 抓到的
  精确复现夹具直接变成收口验证步骤。

### ROUND Gate(实测,串行;F-245-env-1 纪律)
- 连接器测试 **19/19**(18 + o3-1 新钉);TUI 测试 **184/184**(182 + o3-2 两枚新钉);
  cwd 漂移(`/tmp`,前序调试遗留)导致的首次 discover ImportError 已定位纠正,非产品问题。
- 四路:**BARE 790 OK(108 skip)/ SYSTEM 790 OK / TUI 184 OK / ACCEPTANCE PASS**
  (790 = 787 + 3;skip 108 = 106 + 2[o3-2 的两枚 TUI 钉在 bare 侧 textual 守卫 skip];
  算术对账:+1 core-lane 钉[o3-1,bare+system 双跑]+2 tui-lane 钉[o3-2,system 跑/
  bare skip]);跑前/跑后 `/tmp/.git` 净。
- 红线复核:`tools.py`/`state.py` 零 diff;claim 链路(dry-run→confirm)字节不动;
  `/confirm` 签名扩展向后兼容(既有 `_cmd_confirm(0)` 调用不受影响,184 旧钉为证)。

**ROUND 状态:实现收口,未 commit——候 cc glm 增量审计 → 报 Owner 授权 finalize
(新规,治理仓 3a31c9d:只推产品仓 + 精确 pathspec,治理档由顾问会话落笔)。**
