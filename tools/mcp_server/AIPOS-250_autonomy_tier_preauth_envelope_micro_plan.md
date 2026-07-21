# AIPOS-250 — Slice 自动化档位:预授权信封第一档(claim 预授权)+ 能力账本字段(片)

- **Status**: APPROVED(R 方向审计 PASS + 红线八条已守 + R-1/R-2 折入 §8;Owner "折入后
  视为 APPROVED,进实现")
- **Authority**: 实现授权(不 commit——收口后候 cc 增量审计 → Owner O3 → finalize)
- **定位**:北极星"可问责全自动"的第一块地基。Owner **事先亲手确认一段有界自治信封**,
  信封内的 claim 自动放行、免逐单 confirm;顺路补"实际模型 + 自报 token"记录字段(喂能力
  账本)。**只做 CLAIM 一档**;return/publish/audit confirm 维持逐单不碰。
- **Parent 裁定(治理仓 direction_log 2026-07-15 三条,已读)**:
  - **北极星**:全自动 ≠ 无人——人手前移、颗粒变粗、但永远在且每步有账。
  - **两道门**:门 A = harness 工具审批(cc 自己的,非 Lybra);门 B = Lybra Owner 确认
    (publish/claim/return confirm)。本片批量化的是**门 B 的 claim 档**。
  - **★ 预授权 ≠ 委托(生死线)**:委托 = Owner 把"按门"的权给 agent、运行时 agent 自己按
    → 破 F-06(刚经 O3 焊死);预授权 = Owner **事先亲手按一次**确认有界信封,运行时是
    gate **执行已授权策略**、不是模型重新判断;人手那一按移到授权时刻、从未消失。
  - **三永不(全托管可问责,本片先守)**:①顾问/agent 永不写审计判定;②永不在信封外 confirm
    (越界弹回 Owner);③每动作归因(哪 agent/什么模型/哪条策略/Owner 何时授权,全落账)。

---

## §0 真实现状台账(executed-✓,全部 file:line 落地核实)

| # | 事实 | 出处 |
|---|---|---|
| 1 | **`owner_policy_ref` = 自由字符串标签**,只做①非空校验②confirm 一致性比对(防篡改锚点),**不指向文件、无格式校验**。confirm 传入必须与 dry-run 落值逐字相等(`OWNER_POLICY_MISMATCH`);它**不决定**是否需 owner 确认(那由 `owner_confirmation_required` flag 决定) | `tools.py:1095-1101,1222-1229,547`;实际值形态 `confirm_client.py:300`("owner_policy:supervised") / `v1_acceptance.py:85`("DL-20260625-01") |
| 2 | **confirm 分级支点** = `validate_owner_confirmation(*, required, owner_confirmation_token)`:**`if not required: return True`**(免确认);否则强制 token==`OWNER_CONFIRMED` | `controlled_execute.py:208-213` |
| 3 | **claim 现被无条件强制确认**:dry_run 侧 `owner_confirmation_required_override=True`(硬编码)→ 穿过 `claim_task` 写进 dry-run plan → confirm 侧从 plan 读回 `owner_required=True` | `tools.py:1143`;`board_adapter.py:3062,3088-3092` |
| 4 | **claim confirm 的 A 层两道独立门**:①scope 门 `if not _owner_confirm_scope_allowed(): SCOPE_DENIED`(executor 无 owner_confirm scope = ★A1 结构钉);②字面量门 `if owner_confirmation_token != OWNER_CONFIRMED: OWNER_CONFIRMATION_REQUIRED`。**这两道独立于 B 层**——信封免确认必须同时处理三处,否则 A 层先拦 | `tools.py:1163-1164,1173-1179` |
| 5 | **`autonomy_mode` 硬编码 "Supervised" 单枚举**:enum 校验 `!= "Supervised" → INVALID_AUTONOMY_MODE`(文案明写 "Delegated and Standing remain behind separate Owner gates");schema `enum:["Supervised"]`;多处输出硬编码回填(不回读入参) | `tools.py:1086-1091,2043`;回填 `:487,518,544,1247,1266`;`record_writer.py:427,481,535` |
| 6 | **策略工件 shape 已在册(protocol-only,未实现)**:`owner_autonomy_policy`(`policy_id/policy_version/mode/status/approved_by_owner/owner_approval_ref/active_from/expires_at/max_tasks/task_classes/projects/capabilities/write_scopes/...`)+ Supervised fallback("缺失/歧义/过期→Supervised")+ 不可下放的 Owner escalation floor | `0_control_plane/orchestration/mcp_agent_task_claim_autonomy_dial_protocol.md:339-373,250,252-263`(AIPOS-164) |
| 7 | **FORBIDDEN 护栏(现实现主动拒收预授权语义)**:`FORBIDDEN_QUEUE_CLAIM_FIELDS` 含 `delegated_policy/standing_policy/auto_pick/auto_select/background_worker/batch/policy_budget` → `UNSUPPORTED_QUEUE_CLAIM_FIELD` | `tools.py:65-79,1079-1085` |
| 8 | **`autonomy_mode`/`owner_policy_ref` 已在 MCP claim/return record 落盘**(autonomy_mode 硬编码 Supervised);本地 `build_claim_log_markdown` 无此二字段 | `record_writer.py:419-445(claim),527-557(return),145-178/201-238(order)` |
| 9 | **model/token 字段全仓不存在**(grep `actual_model`/`reported_tokens`/`tokens_used` 全空);`model_tier` 是**任务卡**字段(要求档位,非实际模型),不在 records。claim/return dry_run schema 均 `additionalProperties:False` + 黑名单 → **必须新增显式入参** | grep 实测;`queue_mutation.py:51`;`tools.py:2036-2053,2084-2105` |
| 10 | **248 claimable 提示硬编码"待 Owner confirm"**:`render()` claimable 分支 = "走 claim_dry_run → 把 dry-run 报 Owner,由 Owner OOB confirm——你不能自行 confirm";classify 三态无"信封内/外"区分 | `agent_connector.py:151-160,99-125`;`skills/lybra-executor/SKILL.md:66-72` |
| 11 | **owner_decision_record 链路已存在**(OWNER_DECISION_SCOPE + dry_run/confirm,owner_confirm 门)——Owner 决策落盘的既有机制,可作"信封授权=一次 owner_confirm"的复用/借鉴载体 | `tools.py:918-949`(owner_decision handlers);§0-4 owner_confirm 门同族 |
| 12 | disclosure 现 14 行,末行 #14(:30 planner 行);新增 model/token 披露 = #15 | `docs/v1_disclosure.md:30,32` |

## §1 生死红线(Owner 六条,验收逐条查)
1. **预授权 ≠ 委托**:授权信封本身 = 一次 `owner_confirm`(人手按一次,落盘策略工件);
   运行时 = gate **执行已授权策略**(匹配活跃信封),**不是模型重新判断**;agent/顾问永不
   "替 Owner 按门"——运行时无任何 agent 侧"confirm 动作",放行是 gate 结构判定。
2. **信封有界 + 可回溯 + 可撤销**:策略工件含 `{agent/角色 + task 集合(task_id 集/
   task_mode/项目类)+ expires_at}`;每次自动放行的记录 `owner_policy_ref` **指回**是哪条
   已授权策略 → 可审计:"此 claim 自动放行,因策略 P,Owner 于 T 亲手确认"。撤销 = 策略
   status→revoked(或删),即时回落 Supervised。
3. **信封外 = 回落 Supervised**:匹配失败/过期/撤销 → `owner_confirmation_required=True`,
   逐单 owner_confirm,**绝不静默放行**(fallback 硬默认,同 AIPOS-164 §250)。
4. **只做 CLAIM 一档**:return/publish/audit confirm 维持现状逐单(它们是问责/接受时刻,
   独立审计不可自动旁路——各自的档是后续片);本片**不碰**它们的 `owner_confirmation_
   required`。
5. **autonomy_mode 真枚举**(≥ `Supervised | PreAuthorized`):自动放行记录
   `autonomy_mode=PreAuthorized` 且带 `owner_policy_ref`(指向信封)。
6. **model/token 字段如实披露**:`actual_model` + `reported_tokens` = **agent 自报,非 gate
   测量**;gate 不臆测、不校验真伪,只记录 + 归因(disclosure #15 明写 reported≠measured)。

## §2 架构(策略工件 + 匹配判定 + 枚举/字段)

```
┌── 授权时刻(一次,人手)──────────────────────────────────────────────┐
│ Owner 在 owner-console 起草信封策略(owner_autonomy_policy 工件):     │
│   { policy_id, agent/role, task_selector{task_mode|project|task_ids},│
│     active_from, expires_at, status:active }                         │
│ → 经 owner_decision_record confirm(owner_confirm 门,harness ask 弹窗)│
│   落盘策略工件 + 一条 owner 决策记录(owner_approval_ref 指向它)      │
│   ★ 这一按 = 唯一的人手确认,预授权非委托的"门"                       │
└──────────────────────────────────┬──────────────────────────────────┘
                                    │ 策略工件落治理区(gate 可读)
┌── 运行时(自动,gate 结构判定,无 agent 按门)────────────────────────┐
│ executor claim dry_run(autonomy_mode=PreAuthorized, owner_policy_ref │
│   =<policy_id>)                                                      │
│ → gate 匹配判定(§Q2):task ∈ 某活跃信封 且 actor/role 匹配 且未过期? │
│   ├─ 匹配 → owner_confirmation_required=FALSE → 自动放行落盘          │
│   │        记录 autonomy_mode=PreAuthorized + owner_policy_ref 回指   │
│   └─ 不匹配/过期/撤销 → 回落 Supervised(required=TRUE,逐单 confirm)  │
└──────────────────────────────────────────────────────────────────────┘
```
**顺带**:claim/return dry_run 新增 `actual_model`/`reported_tokens` 入参 → 落 MCP claim/
return record(喂能力账本)。

## §3 开放子问题答案(附落地证据)

### Q1 — owner_policy_ref 现状 + 策略工件(§0-1/6/11)
- **现状**:自由字符串标签,无工件、无格式校验(§0-1)。
- **需新策略工件**(不能只靠自由标签承载信封):**`owner_autonomy_policy`**,shape 照
  AIPOS-164 已在册协议(§0-6)取**最小子集**(第一档只需 claim 信封边界):
  ```yaml
  policy_id: pol_<...>            # owner_policy_ref 指向它
  mode: PreAuthorized
  status: active                  # active | expired | revoked
  approved_by_owner: true
  owner_approval_ref: <owner_decision record ref>   # 回指授权那次 owner_confirm
  active_from / expires_at: <ISO>
  agent_or_role: <actor 或 role>  # 信封覆盖谁
  task_selector: { task_mode?: <..>, project?: <..>, task_ids?: [..] }  # 覆盖哪些任务
  ```
- **存哪**:治理区(运行时真相,gate 可读),如 `5_tasks/policies/<policy_id>.md`(与
  queue/records 同树);**授权 = 一次 owner_confirm**——复用 owner_decision_record 链路
  (§0-11):Owner 用 owner-console 起草策略 → `lybra_owner_decision_record_confirm`
  (OWNER_DECISION_SCOPE + owner_confirm,harness ask 弹窗人手按)→ 落盘策略工件 + 决策记录。
- **撤销/到期**:`status→revoked` 或 `expires_at` 过期 → gate 匹配时视为无效 → 回落
  Supervised(红线3)。撤销本身也应是一次 owner 动作(owner_decision,留痕)。
- **给 R 钩子**:策略工件是**新写工具**(lybra_policy_grant?)还是**复用 owner_decision_
  record**(策略作为决策记录的一种 payload)?推荐复用(零新 scope,owner_confirm 门现成);
  R 裁是否够表达 + 撤销语义。

### Q2 — 信封匹配判定点 + 谓词(§0-2/3/4)
- **判定点**:claim dry_run handler(`tools.py:1143` 现无条件 `override=True`)——改为
  **先算信封匹配**:gate 读活跃策略工件,判 `(task 落在某 policy.task_selector) 且
  (actor/role ∈ policy.agent_or_role) 且 (now ∈ [active_from, expires_at]) 且
  (status==active)`。匹配 → `owner_confirmation_required_override=False` + autonomy_mode
  记 PreAuthorized + owner_policy_ref=policy_id;否则 True(Supervised)。
- **A 层三门同步**(§0-4,关键):信封放行必须让 claim confirm 的 A 层两门也认信封——
  **推荐结构**:信封内 claim confirm 的授权基础 = **gate 独立复验策略工件**(approved_by_
  owner + 未过期 + task 匹配),该复验**替代** owner_confirm scope 门 + 字面量门(不是给
  executor 开 owner_confirm scope,而是"出示 Owner 已签发的信封凭证",gate 验证凭证)。
  → executor **永远没有** owner_confirm 能力(★A1 不破);它只是携 owner_policy_ref 走
  已授权信封路径,gate 是放行的执行者。**这是"预授权非委托"的结构落点**(红线1)。
- **匹配谓词失败方向(偏安全)**:**过宽 = 不该自动的自动了 = 危险**(信封漏进了不该覆盖
  的 task);**过窄 = 该自动的没自动 = 回落 Supervised 逐单**(慢但安全)。**有疑偏窄**——
  匹配用**严格 AND**(agent 且 task_selector 且时间窗全中才放行),任一不确定即回落
  Supervised。task_selector 用精确集合(task_ids)或明确类(task_mode/project),不用模糊
  通配。
- **给 R 钩子**:匹配复验放 gate 哪一层(dry_run 算一次 + confirm 复验一次防 TOCTOU)?
  过期/撤销的边界时刻(dry_run 时活跃、confirm 时刚过期)→ 复验必须在 confirm 侧重算(偏窄)。

### Q3 — model/token 通道 + 落盘 + 披露(§0-8/9/12)
- **来源通道**:agent **自报**,走 claim/return **dry_run 新增入参**(schema
  `additionalProperties:False`,必须显式加 property):claim `tools.py:2038-2050` +
  return `tools.py:2086-2103` 各加 `actual_model`(string)、`reported_tokens`(integer)。
  handler `_claim_metadata`/`_return_metadata` 读取透传;客户端注入点
  `confirm_client.py:244-258/261-277`。
- **落盘点**:`build_mcp_claim_record_markdown`(`record_writer.py:419-445` metadata +
  `:145-178` order)、`build_mcp_return_record_markdown`(`:527-557` + `:201-238` order)
  各加两字段。
- **披露口径**(红线6):disclosure **#15**:"actual_model / reported_tokens are
  **agent-reported, not gate-measured** — the gate records and attributes them but does
  NOT verify their truthfulness(no way to measure an external agent's model/token use);
  they feed the capability ledger as `reported`, never as ground-truth measurement."
- gate **不校验真伪**:不因 actual_model 与 model_tier 不符而 BLOCK(只记录 + 归因)。

### Q4 — 与 248 连接器接口(§0-10)
- **现状**:agent_connector claimable 分支硬编码"待 Owner confirm"(`:151-160`)。
- **本片改动(最小)**:claimable 提示改中性 + 告知两种可能——"若此任务在你的预授权信封内,
  claim 将**自动放行**(记录归因到策略);否则**待 Owner 确认**。以 claim dry_run 的 gate
  应答为准。" **agent 侧不预判信封**(信封是 gate 真相,agent_connector 只读、不读策略
  工件)——权威判定在 claim 时 gate 给(dry_run 应答含 autonomy_mode=PreAuthorized/
  Supervised)。
- **SKILL 教学点**:lybra-executor SKILL claim 流程加一句——"信封内任务:claim dry_run
  会自动放行(无需报 Owner),记录会标 PreAuthorized + 指回策略;信封外:照旧 dry-run →
  Owner OOB confirm。是否在信封内由 gate 判定,你不自行假设。"
- **给 R 钩子**:要不要让 queue_list/task_preview surface"此 task 对当前 token 是否
  PreAuthorized"的派生字段(让 agent 提前知道)?本片建议**不做**(保持 agent 只读、
  信封判定集中在 claim gate;派生读面属 v1.1 看板),R 裁。

## §4 Scope(结构落法,分批)

### S1 — autonomy_mode 真枚举
enum 校验 `tools.py:1086`(+return 1284/audit 1518 保持只 Supervised,本片不放开它们)、
schema `:2043`(claim 加 `PreAuthorized`)、输出回填改为**回读入参**(不再硬编码);
record_writer 硬编码改为参数透传。**return/audit 仍只 Supervised**(红线4)。

### S2 — 策略工件 + 授权(Q1)
`owner_autonomy_policy` 工件(治理区 `5_tasks/policies/`)+ 授权经 owner_decision_record
confirm(owner_confirm 门);新 reader(gate 读活跃策略);撤销/到期 = status/expires_at。

### S3 — claim 信封匹配 + 免确认(Q2)
claim dry_run:算信封匹配 → `owner_confirmation_required_override` 动态(True/False);
claim confirm:gate 复验信封凭证替代 A 层两门(executor 无 owner_confirm scope 仍不破);
记录 autonomy_mode=PreAuthorized + owner_policy_ref。**匹配严格 AND、有疑回落 Supervised**。

### S4 — model/token 字段(Q3)
claim/return dry_run schema + handler + MCP record writer 各加两字段;disclosure #15。

### S5 — 248 提示 + SKILL(Q4)
agent_connector claimable 提示中性化 + lybra-executor SKILL claim 流程加信封说明。

## §5 测试清单(RED 纪律;真 gate 真 token)
1. **信封内 claim 自动放行**:授权一个覆盖 task 的活跃策略 → executor claim(PreAuthorized)
   **无 owner_confirm 自动落盘**;记录断 `autonomy_mode==PreAuthorized` 且
   `owner_policy_ref==<policy_id>`(正内容断言,非 proxy)。
2. **信封外回落 Supervised**:task 不在任何信封 → claim 仍需 owner_confirm(未确认 →
   `OWNER_CONFIRMATION_REQUIRED`/`SCOPE_DENIED`);**绝不自动放行**(RED 设计:对"无脑
   PreAuthorized 放行"的朴素实现红)。
3. **策略需 owner_confirm 才生效**:未经 owner_confirm 的策略工件(status!=active 或无
   approved_by_owner)→ claim 不自动放行(回落 Supervised)。
4. **到期/撤销回落**:expires_at 过期 或 status=revoked → claim 回落 Supervised;
   **confirm 侧复验**(dry_run 时活跃、confirm 前撤销 → confirm 仍回落,防 TOCTOU)。
5. **model/token 落盘**:claim/return 带 actual_model+reported_tokens → MCP record 落盘
   两字段;gate 不因与 model_tier 不符而 BLOCK(只记录)。
6. **只 CLAIM 一档**:return/publish/audit confirm 仍逐单 owner_confirm(PreAuthorized 不
   波及它们)——回归钉。
7. **★A1 不破**:executor token 试直接 claim_confirm(无信封 或 伪造 owner_policy_ref 指向
   不存在/未授权策略)→ 仍 SCOPE_DENIED/回落(信封凭证复验挡住伪造)。
8. 四路串行 + `/tmp/.git` 跑前查跑后清;return/audit 现有钉全绿。

## §6 O3 剧本(Owner 全程 owner-console)
1. **定义有界信封**:Owner 说"给 exec 池预授权:今天到期、只覆盖 MP 类卡" → 顾问起草
   owner_autonomy_policy(task_selector={task_mode:MP}, agent_or_role=exec 池, expires_at=
   今日) → **亲手确认策略(harness ask 弹窗,owner_decision confirm 人手按)** → 策略落盘。
2. **发一张信封内卡**(MP 类)→ executor 自动认领(claim dry_run 直接放行,**无逐单确认**)。
3. **查记录归因**:owner-console 人话叙述 → 记录 `autonomy_mode=PreAuthorized`,
   `owner_policy_ref` 指回策略,可读出"因策略 P、Owner 于 T 授权"。
4. **发一张信封外卡**(非 MP 类,或 exec 池外 agent)→ **仍弹逐单 owner_confirm**(回落
   Supervised 眼验,红线3)。
5. (可选)**撤销/到期**:撤销策略 或 等过期 → 再发 MP 卡 → 回落逐单(红线2 可撤销眼验)。

## §7 给 R 的钩子
1. 策略工件:复用 owner_decision_record(推荐,零新 scope)vs 新 lybra_policy_grant 工具?
   撤销语义(status 翻转 vs 删)?策略工件存 `5_tasks/policies/` 是否合适?
2. 信封放行的 A 层结构(Q2):"gate 复验信封凭证替代 owner_confirm scope+字面量门"——这个
   结构是否真守住★A1(executor 永无 owner_confirm 能力,只是走已授权信封路径)?是否需要
   confirm 侧独立复验防 TOCTOU?
3. 匹配谓词偏窄(严格 AND、有疑回落)——task_selector 用集合/明确类、不用通配,可接受?
4. model/token 只在 MCP claim/return record 落(不动本地 build_claim_log_markdown)?
5. Q4 本片不加 queue_list 的 PreAuthorized 派生读面(agent 不预判、以 gate 应答为准)——
   最小边界是否可接受,还是 O3 体验需要 agent 提前知道?
6. return/audit 保持逐单(红线4)——本片枚举只在 claim 放开 PreAuthorized,return/audit 的
   enum 仍锁 Supervised,自洽?
7. 与 planner 片接口(249 §233 登记的开放问题):planner token 是否参与预授权?本片建议
   **只对 executor claim**(planner 只 draft,不 claim),R 裁。

## §8 R 方向审计折入(PASS,红线八条已守 + R-1/R-2;Owner 2026-07-15,折入即 APPROVED)

**R 审结论**:红线八条已守——★A1 防伪造 / gate 侧匹配 / fail-safe 回落 / 策略需
owner_confirm 生效 / TOCTOU 重算 / return-audit 不碰 / model-token 诚实。DRAFT 推荐方案
(复用 owner_decision_record 授权 / gate 复验信封凭证替代 A 层两门 / 匹配严格 AND 偏窄)
获接受。折入两条:

- **R-1 max_tasks 真强制(有界 = 时间界 + 次数界)**:策略工件加 `max_tasks` 字段;gate
  匹配时**计入该策略已自动放行次数**(扫该 `owner_policy_ref` 已落盘的 PreAuthorized claim
  记录数,无状态、可审计),达上限 → 回落 Supervised。加测试:`max_tasks=N`,第 N+1 张
  信封内卡走逐单 owner_confirm。→ 并入 §1 红线2(有界)+ §4-S3 + §5 测试(新钉)。
- **R-2 FORBIDDEN_QUEUE_CLAIM_FIELDS 零解禁**:本片经 `autonomy_mode` + `owner_policy_ref`
  实现预授权,**绝不动那道护栏**(§0-7 的 delegated_policy/standing_policy/auto_pick/
  auto_select/background_worker/batch/policy_budget 仍全部 `UNSUPPORTED_QUEUE_CLAIM_FIELD`)。
  → §4 显式写明 + §5 加一条回归钉(这些字段仍 UNSUPPORTED)。

**折入后红线2 更新**:信封有界 = **时间界(expires_at)+ 次数界(max_tasks)**——达任一界即
回落 Supervised。**§4-S3 更新**:匹配判定 = `task_selector ∧ agent/role ∧ 时间窗 ∧
(已放行数 < max_tasks) ∧ status==active`(严格 AND,任一不满足回落)。

---

## §9 实现记录(executed;不 commit,候增量审计 → Owner O3 → finalize)

**核心架构落地 = 一段式信封放行(Owner 决策已定,实现记录焊死)**:PreAuthorized 的 claim
在 `lybra_queue_claim_dry_run` 匹配信封后,gate **直接落盘**——不返回 dry_run token、不需
confirm 步骤;executor **从不调 `lybra_queue_claim_confirm`**;`lybra_queue_claim_confirm`
路径**完全不变**(永远 owner_confirm scope + OWNER_CONFIRMED,供 Supervised 的 Owner 确认)。
最强 ★A1(executor 永无 confirm 能力)+ 最纯"预授权非委托"(运行时只有 executor 发起、gate
依已授权策略执行,无 agent 按门动作)。**TOCTOU 副作用**:一段式下 match 与 write 在同一次
调用内原子发生,executor 侧无 dry→confirm 窗口——比两段式更强,§Q2/§5-4 的 confirm 复验在
本实现里天然不存在(撤销/到期由 gate 每次 claim 时重读策略即时生效)。

### 落地点(file:line,以最终态为准)
- **新模块 `tools/aipos_cli/autonomy_policy.py`**(纯低层,只依赖 record_writer + frontmatter):
  `build_autonomy_policy_markdown`(FLAT 扁平 frontmatter,task_selector 拆三字段+task_ids 列表)、
  `load_policy`(按 owner_policy_ref 读单条,缺失/畸形/伪造→None=★A1 防伪造)、
  `count_preauthorized_claims`(扫 `5_tasks/records/claims/**` 数 autonomy_mode==PreAuthorized
  且 owner_policy_ref 命中的记录数,无状态可审计=R-1 次数界)、`match_claim_envelope`(严格 AND:
  mode∧status==active∧approved_by_owner∧时间窗∧agent_or_role∧task_selector∧released<max_tasks)。
- **S1 枚举** `tools.py:lybra_queue_claim_dry_run`:enum 放开 `{Supervised, PreAuthorized}`;
  schema `enum:["Supervised","PreAuthorized"]`;record_writer 三 builder 由硬编码改回读入参
  (claim/session 参 `autonomy_mode`;**return 保持硬编码 "Supervised"**=红线4,注释焊死);
  return/audit dry_run 的 enum **未动**(仍只 Supervised)。
- **S2 策略工件 + 授权** `owner_decision_writer.py`:payload 可选 `autonomy_policy` 块→校验+
  计划写 `5_tasks/policies/<policy_id>.md`(status=active/approved_by_owner=true/owner_approval_
  ref=decision_id),原始块回灌 `original_payload` 保证 confirm 重算一致;`tools.py:lybra_owner_
  decision_record_confirm`:plan 带 `autonomy_policy_grant` 时**额外要 owner_confirm scope +
  OWNER_CONFIRMED**(镜像 claim/return/publish confirm)=授权即一次 owner_confirm。
- **S3 匹配+一段式** `tools.py`:`_match_claim_envelope`(load_policy∧load_task_snapshot∧
  queue_state==pending∧count∧match_claim_envelope)、`_preauthorized_claim_autorelease`(以
  required=False 跑 claim dry_run→立即 execute_dry_run 落盘,confirmer=`autonomy_policy:
  PreAuthorized`/policy_id 诚实归因"运行时无 token 按门");不匹配 fall-through 到 Supervised
  预览。**board_adapter execute 重算分支**(:3082)由硬编码 `override=True` 改为读
  `mcp_claim_metadata.owner_confirmation_required`——否则一段式 PreAuthorized 落盘触发假
  SNAPSHOT_MISMATCH(实测踩到并修)。
- **S4 model/token** claim/return dry_run schema 各加 `actual_model:string`+`reported_tokens:
  integer`;`_claim_metadata`/`_return_metadata` 读入参透传;record_writer claim/return builder
  + frontmatter order 各加二字段;board_adapter 两 record_plan + 三调用点透传;gate 不校验真伪。
- **S5 提示** `agent_connector.py` claimable 分支中性化(信封内自动/信封外逐单,以 gate 应答
  为准,agent 不预判)+ `skills/lybra-executor/SKILL.md` claim 流程加信封说明(PreAuthorized
  非委托、不给 confirm 能力)。
- **S5b owner-console SKILL 补漏(O3 前发现)** `skills/owner-console/SKILL.md`:删过时"零
  autonomy/本 v1.0 不实现任何免确认路径"表述(否则顾问读了会拒绝、O3 第1步卡死);新增
  「预授权信封」教学(Owner 说"给某池预授权"→ 顾问起草 owner_autonomy_policy 块过
  `lybra_owner_decision_record_dry_run`→ `..._confirm`(owner_confirm 门,harness ask 弹窗
  人手按)落盘生效 → 信封内自动/外回落 → 撤销 status→revoked);**F-06 补线**:把
  `mcp__lybra__lybra_owner_decision_record_confirm` 钉进 `permissions.ask`(否则给信封落盘那一
  按可能被 allow 静音)+ F-06 双防线注记同步。红线口径守:预授权≠委托、顾问永不替 Owner 按门、
  owner_confirm 永禁免审白名单、信封只放行 claim。
- **披露** `docs/v1_disclosure.md`:#6 由"Supervised only"改为"Supervised + PreAuthorized 信封
  档(CLAIM only)"(诚实更新,return/audit 仍锁);新增 #15(reported≠measured,gate 不校验);
  `tools/acceptance/tests/test_disclosure.py` 类别6 标记随文档改为 `PreAuthorized envelope tier`。

### 测试(`tools/mcp_server/tests/test_autonomy_preauth_envelope.py`,11 gate 钉 + 5 SKILL-delta 钉)
①信封内自动放行+记录断 PreAuthorized/owner_policy_ref(正内容断言) ②信封外回落 Supervised
(RED 设计,对朴素放行红) ③策略需 owner_confirm 才生效(无 token confirm→OWNER_CONFIRMATION_
REQUIRED;executor 起草策略→SCOPE_DENIED) ④到期回落 ⑤撤销(status→revoked)回落 ⑥model/token
落盘 ⑦return 拒 PreAuthorized(红线4 回归钉) ⑧★A1 伪造 owner_policy_ref→回落+executor
claim_confirm→SCOPE_DENIED ⑨R-1 max_tasks=1,第2张走逐单 ⑩R-2 七个 FORBIDDEN 字段仍
UNSUPPORTED。诚实边界:实现先于测试完成(体量),非严格 RED-先跑红;但②/⑦/⑧按"对朴素实现红"
设计,断言正内容非 proxy。

SKILL-delta 5 钉:owner-console 教「预授权信封」(8 关键词正断言)、删过时"不实现任何免确认
路径"(负断言)、`owner_decision_record_confirm` 在 ask 片段(F-06)、预授权≠委托口径、
executor SKILL 信封+无 confirm。

### S6 接线补漏(O3 实测第二次栽在"机制建好但对 Owner 不可达")
- **service_mode.py owner 角色 scopes 加 `owner_decision_record`**:否则 owner token 铸出后
  `lybra_owner_decision_record_dry_run/confirm` 不在 MCP 清单(tools.py 按 scope 暴露),
  owner-console 信封流程整条不可达(O3 第1步卡死)。它本就是 Owner-only + owner_confirm 门的
  写面,归 owner 合理。**AIPOS-207 处置迁移**:`owner_decision_record` 由"path-B-only 豁免"
  移入 owner 角色可达 → 同步改 `test_scope_reachability.py` 的 `CAPABILITY_TOKEN_EXEMPT`
  (剩 `{intake_submit}`)+ 加两正/反钉(owner 可达 / executor·planner·copilot 仍 SCOPE_DENIED)
  + disclosure #4 改写 + `test_service_mode` owner scope 集断言更新。
- **owner-console SKILL「一次性配置」自检**加 `lybra_owner_decision_record_dry_run` 应出现,
  缺则提示重铸 owner token(scope 未含 owner_decision_record)。
- **`~/o3-launch.sh`(Owner 工具,不在仓、不进 finalize pathspec)**:owner token 经 rotate 自动
  带新 scope(读 ROLE_SPECS);新种 `O3-FX-ENV`(pending/claimable/task_mode=code/assigned
  exec.cc.local)= 信封 O3 主体(与 task_mode=docs 的 O3-FX-3 一内一外并排演);夹具精确集
  5→6、整数断言同步。

### S7 证据放宽 + 端到端接线(O3 实测第三次栽在集成缺口:信封起草端到端走不通)
- **★设计裁定=放宽**:owner_decision_record 的 AIPOS-110 `owner_approval_evidence` 是为**带外**
  审批留证;而信封的批准是**带内**——就是 confirm 时 Owner 亲手点的 harness 弹窗。要顾问再手工
  编一段带外证据既冗余、又诱导编造(为正在发生的批准伪造字段)。故 `owner_decision_writer.py`
  加 `_synthesize_policy_grant_record`:autonomy_policy 在场时走**放宽支路**——只需
  `decision_id`+`autonomy_policy`(+可选 summary/decided_by_ref/actor),gate 自动落**如实带内
  证据标记**(`capture_method: harness_owner_confirm`、`evidence_hash` 空)、applies_to/
  approval_scope/capability_scope 由策略派生;**非信封路径 100% 不变**(全 AIPOS-110 严格 schema,
  既有测试零回归)。合成值全 deterministic(时间戳取 policy.active_from,dry↔confirm 快照一致)。
- **SKILL #1 完整可抄 payload**:owner-console「预授权信封」节给**完整 JSON 全例**
  (decision_id+actor+decided_by_ref+autonomy_policy 块),+"为何不用填证据"设计注;confirm 步
  补 `actor` 必须与 dry-run 一致(否则 `execute actor does not match dry-run actor`,实测踩到)。
- **`~/o3-launch.sh`**:`_fx` 加 `$9` owner_policy_ref(`none`=省略);`O3-FX-ENV` 去掉硬编码
  `owner_policy_ref: owner_policy:supervised`(干净可领 code 卡,executor 在 claim args 供 policy_id)。
- **#4 端到端 headless 钉**:`test_real_rotate_owner_arms_envelope_executor_auto_releases`——**真
  serve rotate creds**(非手搭 registry)+ **SKILL 原样最小 payload**:owner 起信封→confirm→
  executor 信封内 claim 自动放行,断磁盘策略工件+claimed 卡俱在。证 Owner 摸得通(不只单测机制)。

### S8 真 bug:MCP inputSchema ↔ writer ↔ SKILL 漂移(O3 第四次,R 直打 gate 定位)
- **根因**:`lybra_owner_decision_record_dry_run` 的 inputSchema ①未声明 `autonomy_policy` 属性
  ②`additionalProperties:False` ③`owner_approval_evidence` 在 required ④描述称 evidence 必填。
  gate **不在服务端校验 inputSchema**(故本片 gate 测试全绿、漏掉),但**真 MCP 客户端(顾问
  harness)会按已发布 schema 校验** → 剥掉未声明的 `autonomy_policy`、逼填 evidence →
  `MISSING_OWNER_APPROVAL_EVIDENCE`。R 用 SKILL 原样 payload 直打 gate(绕 schema)证:
  blocking=[]、grant=true —— 机制对,纯 schema 缺口。
- **修**:tools.py schema 加 `autonomy_policy:{type:object}`;`required` 收缩为仅
  `["decision_id"]`(evidence/applies_to/capability_scope/decision_type 等**不再无条件必填**——
  它们只在非 grant 路径由 writer 的 blocking_reasons 条件强制,schema 不做无条件必填);
  `additionalProperties` 仍 False 但属性表补全;描述改写(evidence 仅非 grant 路径必填,grant
  路径只需 decision_id + autonomy_policy)。
- **★系统性测试缺口修复**:加**穿透已发布 inputSchema** 的钉(`_schema_violations` 零依赖 mini
  校验器,复刻真客户端 required+additionalProperties+type 校验)——断言 SKILL 最小 payload 过
  schema + `autonomy_policy` 已声明 + required==["decision_id"]。防 schema↔writer↔SKILL 三者
  再漂移(本轮正是"单测直调函数/GateClient 绕过 schema"才漏)。
- **夹具**:`~/o3-launch.sh` 给 O3-FX-ENV 种 `5_tasks/records/publishes/O3-FX-ENV/*.md`
  publish provenance(authority_scanner 认 task_id+published_task_ref)→ 由 QUARANTINED 转
  VALID/effective_truth(headless 核 classify_task_authority=VALID),信封生效后真可领。

### 四路串行(跑前/跑后 /tmp/.git 均 clean)
BARE 828 / SYSTEM 828 / TUI 184 / ACCEPTANCE PASS(249 finalize 基线 807/807/184;+21 新钉:
16 信封/SKILL + 2 owner_decision_record 可达 + 1 端到端真-rotate + 2 穿透-schema 一致性)。

## §10 收口边界(未做/留后)
- **只 CLAIM 一档**:return/publish/audit confirm 全维持逐单 owner_confirm,enum 仍锁 Supervised
  (红线4)。它们的档是后续片。
- **撤销无专用工具**:本片撤销=owner 动作翻 status→revoked(gate reader 认;测试以改盘 status
  验 reader 行为)。专用 revoke owner_decision 语义留后(§7-R钩1)。
- **策略 reader 无缓存**:每次 claim 现读磁盘(单用户本地 gate,无性能问题;换取即时撤销/到期)。
- **queue_list 无 PreAuthorized 派生读面**(§Q4/§7-R钩5 本片不做:agent 只读、以 gate 应答为准)。
- **planner 不参与预授权**(§7-R钩7:planner 只 draft 不 claim,PreAuthorized 只对 executor claim)。
