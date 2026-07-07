# AIPOS-245 — Slice F′: Owner 侧引导与连续性打磨(含全量异常路径)

- **Status**: DRAFT(R 方向 PASS,已折 R-1..R-4)
- **Authority**: NONE(DRAFT only,不 commit / 不 push / 不改 truth,等 Owner 批)
- **Parent**: 吸收 O3 findings F-o3-15 / F-o3-9 / F-o3-10;衔接 AIPOS-244(TUI confirm 面,已 commit 81a5c62)
- **R 折入**:R-1(error_code 权威来源逐面枚举)/ R-2(A4 改稿链路已落地读码=两步,单句重出草稿登记 finding F-245-1 上报)/ R-3(claim 归属显示 canonical id)/ R-4(穿透真接线测试覆盖 ≥1 成功 + ≥1 失败分支)。
- **定位**: 纯呈现/引导层 — 让 Owner 每一步(成功或失败)都知道"我在环的哪、下一步敲什么"。**只改提示与呈现,不改任何行为。**

---

## §0 硬红线(验收逐条查)

1. **只碰呈现/引导层** — 绝不改 gate / 确认 / claim / 校验 语义与行为。
2. **提示更明确 ≠ 替 Owner 预填答案** — default-yes 教训热着(AIPOS-244 R1 被 R-REJECT);
   任何新提示不得把肯定词/答案预置进输入或默认发射。
3. **每处提示自检两条全局原则**:
   - **P-A**:该步必须有自然语言的"你在哪 / 下一步敲什么"提示(**成功与失败分支都要**)。
   - **P-B**:确认双通道 —— 自然语言指令 与 `/cmd` 二者都认(等价)。已由 AIPOS-244 的
     `是/yes//confirm` 三等价 + `proceed`/回复 yes 建立;F′ 只**沿用**,不新建通道。
4. **executed-✓ 台账** — 每处改动先落地核实真实代码现状再写(见 §3 引用行号);不落地任何
   没执行过的命令/语法。发现问题登记 finding,不当场热补。
5. **异常引导只对应真实可达失败态** — 严禁虚构不存在的错误提示;枚举中若发现某分支不可达/已坏,
   登记 finding,不用假提示糊。
6. **写面测试至少一条穿透真接线** — session-mock 太高会掩盖接线断裂(F-244-2 教训)。

---

## §1 真实现状台账(executed-✓,§3 逐条引用)

已落地读码核实(`tools/lybra_tui/app.py` @ 本片基线):

| 面 | 现状文案 | 行号 |
|---|---|---|
| 首屏 Connected | "Connected. Type what you want to do in plain language...try `/help`." | app.py:399 |
| `/gates` 列表 | `[i] {op} {task_id}` — **只用 op+task_id,未取 title/归因** | app.py:766-776 |
| `/confirm` 成功 | `Confirmed. Writes: {kind} {path}` 或 `Confirmed: {op} {task_id}` | app.py:869-875 |
| `/confirm` 失败 | `Confirm failed: {error_code}: {message}`(错误面已响亮,Slice D) | app.py:860-863 |
| `/audit` verdict | `{task_id}: {verdict}{suffix}` — **verdict=FAIL 无引导** | app.py:918 |
| copilot draft 渲染 | `_copilot_msg` → `_markdown()`(**已 markdown!** F-o3-15 部分已闭) | app.py:424, 1307 |
| draft 后引导 | `✓ Conformant. Next: review...`/needs-bundle/not-publishable | app.py:1309-1317 |
| `/proceed` 落盘后 | `✓ Landed {rel}.` + `Publish dry-run staged... NOT published.` | app.py:1354-1358 |
| gate 行数据 | `list_confirm_gates` 返回 `{op, task_id, task}`,`task` 含 metadata + title | confirm_client.py:179-189 |

**关键发现(写进 §2 改动点)**:
- **F-o3-15 draft markdown 已闭合**:`_copilot_msg` 早已走 `_markdown()`(app.py:424),draft 内容
  已 markdown 渲染。F′ **不重做 draft 渲染**,只补 F-o3-9/10 的 NL 改稿引导(见 Scope A #4)。
- **`/gates` 行数据现成**:`task` dict 已含 `title`/`assigned_to`,只是渲染没取 —— 纯呈现补齐,零行为改动。

---

## §2 Scope A:顺利路径(happy path)逐条

> 每条:改动点 / 触及文件 / 真实现状引用 / 测试策略(标穿透真接线的那条)。

### A1 — claim 成功后引导
- **改动点**:`_execute_pending_confirm` 成功分支(app.py:869-875),`op=="claim"` 时追加一句:
  `→ 已批给 {actor}。通知 agent 开工,完成后回 /gates 看 return gate 再 /confirm。`
- **R-3(canonical 身份红线)**:`{actor}` 必须是记录里的 **canonical 标识**(即确认时经 pending
  机制记录、gate 校验通过的那个 actor 字符串,如 `agent-01`),**不派生/不美化友好名**(别名是另一片)。
  取值 = `_pending_confirm["actor"]`(gate 已校 actor==canonical,直接透传,零派生)。
- **文件**:app.py(`_execute_pending_confirm`)
- **现状**:当前只打 `Confirmed. Writes:...`,无"下一步"(违 P-A)。
- **测试**:Pilot — claim 成功 → 输出含"通知 agent"+"return"+透传的 canonical actor;**穿透真接线**那条见 §4。

### A2 — return 成功后引导
- **改动点**:同 `_execute_pending_confirm` 成功分支,`op=="return"` 时追加:
  `→ 任务已 RETURNED。下一步 /audit {task_id} 看判定。`
- **文件**:app.py
- **现状**:同 A1,无下一步。
- **测试**:Pilot — return 成功 → 输出含 `/audit {task_id}`。

### A3 — `/gates` 列表带标题 + 归因 + 确认提示
- **改动点**:`_cmd_gates`(app.py:766-776)行渲染从 `[i] {op} {task_id}` 改为
  `[i] {op} {task_id} — {title}(归因 {assigned_to})  用 /confirm {i} 确认`;
  `title`/`assigned_to` 从 `g["task"]` 取(数据现成),缺失时回落"(无标题)"/"(未归因)"。
- **文件**:app.py
- **现状**:app.py:772-775 只用 op+task_id;`g["task"]` 已含字段(confirm_client.py:184/188)。
- **红线自检**:纯呈现,不改 `list_confirm_gates` 逻辑;缺字段回落**不预填**(P-2)。
- **测试**:Pilot — gates 行含 title + 归因 + `/confirm 0`。

### A4 — copilot draft 后 NL 改稿引导(F-o3-9/10)—— R-2 重写,不开空头支票
- **R-2 落地读码结论(executed-✓)**:draft 之后的 NL 输入(app.py:682)走 `_submit_intent`
  → `_chat_worker` → `copilot.chat()`,**是聊天回合,不是"单句即重出草稿"**。真实重出草稿链路是**两步**:
  1. Owner 说"优先级改成 high" → `chat()` 回答 + 重新 arm `_pending_offer`(`_render_chat` :1302-1303)。
  2. Owner 再回 `yes`/`是`/`/draft` → `_consent_to_draft` → `_draft_worker`(`draft_task_card`
     从 l3_chat 拉上一句改稿意图,**会纳入**)重出草稿。
- **裁定(守 F′ 纯呈现边界,不偷造能力)**:A4 提示措辞**对齐真实两步链路**,**不承诺**"单句即重出":
  `满意就 /proceed;想改就说哪里改(如"优先级改成 high"),我先答复,你回 yes 我据此重出草稿。`
- **finding 登记(超 F′ 边界,上报不做)**:**F-245-1** — "Owner 单句改稿意图直接触发重出草稿
  (免二次 yes)"是**行为改动**(需在 draft-pending 态把 NL 输入路由到 `_draft_worker` 而非
  `chat()`),超出 F′ 纯呈现层;登记为独立候选 slice,F′ **不实现**。
- **文件**:app.py(`_NEXT_AFTER_DRAFT` 常量 @ :137-140)
- **现状**:当前只说 review + `/proceed`,无改稿引导(F-o3-9/10 未闭)。
- **红线自检**:纯文案;所述两步链路**全部已存在**(`_submit_intent`/`_consent_to_draft`,不新建行为)。
- **测试**:Pilot — draft conformant → 输出含"你回 yes 我据此重出草稿"(措辞不承诺单句即重出)。

### A5 — `/proceed` 之后引导(现状已基本达标,微调)
- **改动点**:app.py:1355-1358 已有 `✓ Landed` + `Publish dry-run staged... NOT published`。
  P-A 微调:追加 `→ 用 owner token OOB 确认发布(TUI 不持发布权)。` 使"下一步"显式。
- **文件**:app.py
- **现状**:已说 "Confirm out of band with the owner token" — **基本达标**,仅措辞对齐"下一步"。
- **测试**:Pilot — proceed 后输出含 "OOB" + "NOT published"。

### A6 — 首屏 Connected 后全环导览
- **改动点**:app.py:399 的 Connected 文案追加一句全环导览:
  `本环:发任务(说需求→/proceed)→ agent 认领 → 你 /gates+/confirm → /audit 看判定。`
- **文件**:app.py
- **现状**:app.py:400-403 只说"plain language / `/help`",无全环地图(违 P-A 起点)。
- **测试**:Pilot — 首屏输出含全环导览关键词。

---

## §3 Scope B:全量异常路径引导(枚举清单)

> 先落地读码枚举 loop 各面「Owner 可达的失败/BLOCK/FAIL 分支」。
> 每条:分支 / 真实触发点 / 拟提示(P-A:发生什么 + 下一步)。措辞对齐真实原因,不美化、不预填。

### B-0 error_code 权威来源(R-1:可审计,非"我 grep 全了"的断言)
**枚举锚定 `tools.py` 三个集中错误工厂 + gate 内联抛点,不靠 app.py 侧 grep 猜:**
- `_queue_claim_error(code, msg, next)` @ tools.py:378 — claim 面全部失败码
- `_queue_return_error(code, msg, next)` @ tools.py:387 — return 面全部失败码
- `_audit_error(code, msg, next)` @ tools.py:396 — audit 面全部失败码
- gate 内联 BLOCK 细分 @ tools.py:709-719(INDEPENDENCE_FAILED / MISSING_RETURN_RECORD /
  MISSING_AUDIT_DISPATCH_RECORD / MISSING_AUDIT_SESSION_RECORD / AUDIT_ACTION_BLOCKED)
- 项目门 `PROJECT_SCOPE_DENIED` @ tools.py:256;scope 门 `SCOPE_DENIED` @ tools.py:223

**实现前动作(executed-✓)**:落地对上述工厂**逐个调用点**列出 error_code,产出「每面失败码
全集」;R 可拿 §B-1 逐面清单 vs `grep -n '_queue_claim_error\|_queue_return_error\|_audit_error'
tools.py` 输出对账。**注**:`tools.py` 无集中 enum 常量(错误码是工厂调用现场的字符串字面量),
故权威来源 = 工厂调用点全集,而非单一 enum。

### B-1 逐面失败分支清单(R-1:8 面各给"清单"或"本面无用户可达失败")

- **claim**(`_queue_claim_error` 调用点):ACTOR_REQUIRED / OWNER_POLICY_REF_REQUIRED /
  INSTANCE_REQUIRED / INSTANCE_MISMATCH / OWNER_POLICY_MISMATCH / DRY_RUN_REQUIRED /
  OWNER_CONFIRMATION_REQUIRED / CONFIRM_ARGUMENTS_REQUIRED / AMBIGUOUS_LEGACY_INSTANCE
  + 门:SCOPE_DENIED / PROJECT_SCOPE_DENIED。**TUI 可达子集**见下表(B1–B6);其余(如
  DRY_RUN_REQUIRED / CONFIRM_ARGUMENTS_REQUIRED)由 TUI 内部按流程保证不空传 → **登记为
  "TUI 流程不可达"**(见 B-枚举纪律),不造假提示。
- **return**(`_queue_return_error` 调用点):与 claim 同构(ACTOR_REQUIRED /
  OWNER_POLICY_REF_REQUIRED / INSTANCE_* / OWNER_POLICY_MISMATCH / DRY_RUN_REQUIRED /
  OWNER_CONFIRMATION_REQUIRED / CONFIRM_ARGUMENTS_REQUIRED)+ MISSING_RETURN_RECORD(:713)。
- **audit**(`_audit_error` + gate 内联):verdict=FAIL/REQUEST_CHANGES(读面,非 error_code)
  + INDEPENDENCE_FAILED / MISSING_AUDIT_DISPATCH_RECORD / MISSING_AUDIT_SESSION_RECORD /
  AUDIT_ACTION_BLOCKED / OWNER_POLICY_MISMATCH / INSTANCE_MISMATCH。
- **proceed**(app.py 本地分支,非 gate):无 pending 草稿 / workspace_root 未知 /
  草稿未 conformant / needs_bundle — 均为**呈现层本地态**,无 gate error_code。
- **gates**(`/gates`):读面,无失败 error_code;仅"无 pending gate"空态(B12)。
- **confirm**(`/confirm`):失败码 = claim/return 门透传(SCOPE_DENIED / PROJECT_SCOPE_DENIED /
  STALE_DRY_RUN / SNAPSHOT_MISMATCH / INSTANCE_MISMATCH)+ 本地(无 gate / 索引越界)。
  **注**:STALE_DRY_RUN / SNAPSHOT_MISMATCH 需实测确认 tools.py 真抛(下方 finding 纪律)。
- **mode**(`/mode`):本地 ValueError(未知模式,B14),无 gate。
- **draft**(`/draft`):本地"非 copilot 模式"(B15),无 gate。

### B-2 枚举清单(TUI 可达失败态 → 拟提示)

| # | 分支 | 真实触发点(error_code / 状态) | 拟提示(P-A) |
|---|---|---|---|
| B1 | claim:任务无 assigned_to | `_cmd_confirm` R-2 分支(app.py:805,已有问 actor) | **已达标**(AIPOS-244 R-2);F′ 补一句"或先给任务加 assigned_to 再领"引导。 |
| B2 | claim/return:SCOPE_DENIED | gate `_scope_denied_result_for`(tools.py) → `_observe_error_face` | "确认被拒:此 token 缺 owner_confirm 权。→ 用 owner-role 连接(--role owner)重试。" |
| B3 | claim/return:PROJECT_SCOPE_DENIED | gate 项目门(tools.py) | "确认被拒:token 项目范围不含当前 active project。→ /project switch <name> 或换 token。" |
| B4 | confirm:STALE_DRY_RUN | gate dry-run token 过期/未知(tools.py,**待实测确认真抛**) | "dry-run 已过期。→ 重新 /gates 取最新 gate 再 /confirm。" |
| B5 | confirm:SNAPSHOT_MISMATCH | gate revalidation 快照变了(tools.py:367,SNAPSHOT_MISMATCH 确有) | "任务状态在你确认前已变(他人动过)。→ /gates 重看,确认无误再 /confirm。" |
| B6 | confirm:INSTANCE_MISMATCH | claim actor≠canonical(tools.py) | "归因 actor 与任务登记的实例不符。→ 核对 agent 名后重试。" |
| B7 | audit:verdict=FAIL/REQUEST_CHANGES | `observe("task").data.verdict`(app.py:914-918) | "判定 FAIL:审计未通过。→ 看 L3 记录的 blocking 原因,退回执行者修。" |
| B8 | audit:任务不存在/无记录 | `observe("task")` 空 data / error 面(app.py:906) | **已响亮**(error 面);F′ 补"→ 核对 task_id 拼写或 /queue 看在不在"。 |
| B9 | proceed:草稿未 conformant | `_copilot_proceed` blocking 分支(app.py:1345-1346) | **已达标**(已列 blocking + 下一步);F′ 仅对齐措辞。 |
| B10 | proceed:无 pending 草稿 | app.py:1334 | **已达标**("Type a task first");P-A 微调。 |
| B11 | proceed:workspace_root 未知 | app.py:1337 | "无法落盘:workspace 未知。→ 重启 lybra tui 时带 --workspace-root。" |
| B12 | /confirm:无 pending gate | `_cmd_confirm`(app.py:782) | **已达标**("No pending confirm gates");P-A 补"→ 先让 agent claim,或 /queue 看队列"。 |
| B13 | /confirm:索引越界 | app.py:793 | **已达标**("Invalid gate index");保留。 |
| B14 | /mode:未知模式 | `_cmd_mode` ValueError(app.py:889→739) | 现状 `Error: {exc}`(裸异常)。补:"未知模式。→ /mode [observe\|confirm\|copilot]。" |
| B15 | /draft:非 copilot 模式 | app.py:1111 | **已达标**("Use /mode copilot");保留。 |

**枚举纪律**:
- B1/B8/B9/B10/B12/B13/B15 **已达标或接近** → F′ 仅按 P-A 对齐"下一步"措辞,不重写。
- B2–B7 是真失败态,当前呈现丢弃了 gate 自带的 `suggested_next_action`(见下 R-5 取向)→ 透传 + 叠环位置。
- B14 当前裸异常 `Error: {exc}` → 按 P-A 补(本地态,无 gate teaching)。
- **finding 占位**:枚举中若实测某 error_code 不可达(如 gate 已改),登记 F-245-* 不糊假提示。

### B 实现取向:透传 gate 自带 teaching + 只叠"环位置"(R-5 定稿,取代原映射表)
**executed-✓ 落地读码结论**:
- gate 的 `_teaching_error`(tools.py:131)顶层已带 `message` + `suggested_next_action` + `doc_ref`
  (:144-146);所有 claim/return/audit 失败码经此工厂发出,**gate 已内置"怎么修"**。
- 但 TUI 两处 `_observe_error_face`(模块 app.py:54、实例 app.py:877)**当前都只取 `code: msg`,
  丢弃了 `suggested_next_action`** —— gate 教的下一步被 TUI 吞了。

**R-5 取向(不平行复刻原因文案)**:
1. **改 `_observe_error_face`**(两处同步):失败时追加 gate 自带 `suggested_next_action`(若有),
   呈现为 `{code}: {msg}\n  → {suggested_next_action}`。**原因文案 100% 来自 gate,TUI 不复刻。**
   `suggested_next_action` 缺失 → 只显示 `{code}: {msg}`(不虚构)。
2. **TUI 只叠"环位置"一层**:按呈现面(confirm/claim/return/audit)追加一句**纯环位置**提示
   (如 confirm 失败尾部 `↳ 你在 confirm 环;/gates 重看待确认项`),**不重述失败原因**。
   环位置话术是 TUI 呈现层职责(gate 不知道调用者在 TUI 哪个面),与 gate 的 `suggested_next_action`
   正交、不重叠。
3. **废弃原 `_ERROR_NEXT_STEP` 映射表**:那是"按 error_code 平行复刻原因→下一步",正是 R-5 禁止的
   重复。删除,改由 gate 透传承担原因/修复,TUI 只补环位置。
4. gate 返回**零改动**;confirmer/校验语义不变;两处 `_observe_error_face` 行为对齐(消除 :54 与
   :877 的分叉)。

---

## §4 测试策略(写面标准,至少一条穿透真接线)

- **Pilot(session-mock 层)**:A1–A6 + B7/B14 各一条,断言输出含引导关键词、**不含预填答案**。
- **★ 穿透真接线那条(防 F-244-2 掩盖;R-4:覆盖 1 成功 + 1 失败)**:
  mock 降到 **GateClient 层**(`create_autospec` 锁签名),session 用 **REAL TuiSession**,
  真实走 `/confirm 0 → 是 → _execute_pending_confirm → session.confirm_gate → GateClient.confirm`。
  - **失败分支** `test_f245_teaching_and_loop_reach_real_confirm_wiring_fail`:GateClient 返回真实
    `_teaching_error` 形状(`ok:False` + `error_code:SCOPE_DENIED` + `message` + `suggested_next_action`),
    断言:① 响亮含 `SCOPE_DENIED` ② **透传了 gate 自带的 `suggested_next_action` 原文**(R-5:原因来自
    gate,不是 TUI 复刻)③ **叠加了 confirm 环位置提示** ④ 无成功文案。
  - **成功分支** `test_f245_success_guidance_reaches_real_confirm_wiring`:GateClient 返回成功
    (`ok:True` + `planned_writes`),op=claim,断言:① 出现 `Confirmed` ② **追加 A1 的下一步引导**
    (含"通知 agent"+"return")③ actor 是透传的 canonical(R-3)。
  - 二者都证明引导文案经**真实 session→GateClient 接线**发出,不是 session-mock 假绿。
- **R-5 透传的负向断言**:构造一个 gate 返回**无** `suggested_next_action` 的失败,断言 TUI
  **不虚构**下一步(只有 `code: msg` + 环位置),证明 TUI 未平行复刻原因文案。
- **default-yes 回归钉**:保留 AIPOS-244 的"空回车/其他 → 零调用"断言不动;F′ 新提示
  **不得**引入任何自动发射(测试断言引导文案出现 ≠ gate 被调)。
- **四路**:BARE(TUI 测试 skip)/ SYSTEM / TUI / ACCEPTANCE 全绿。

---

## §5 明确不在 F′ 内(登记,不做)

- **别名**:`/alias` 命令 + claim 时"可改别名"提示 → 独立 slice(canonical id 不变,当写面对待)。
  F′ **不提别名**。
- **agent 侧连接器 / `/lybra on|off`** → 独立 slice(walkthrough 后定形)。
- **draft 渲染重做**:F-o3-15 markdown **已闭合**(app.py:424 已 `_markdown`);F′ 不动渲染,只补
  F-o3-9/10 改稿引导话术(A4)。

---

## §6 交付物 & 流程

- 本 DRAFT(§2 Scope A 逐条 + §3 Scope B 全枚举 + §4 测试策略)→ **R 方向审计**。
- R PASS + Owner 批 → 实现(纯 app.py 呈现层 + 测试)→ cc glm → R 复核 → Owner O3 → finalize。
- **不 commit / 不 push / 不改 truth**(authority: NONE)。

## §7 给 R 的钩子(方向审计点)
1. Scope A/B 是否**全在呈现层**(有无任何一处触碰 gate/claim/校验行为)?
2. B 枚举是否**真实可达**(error_code 映射 vs tools.py 实际抛点)、有无虚构分支?
3. F-o3-15 "已闭合"判断是否成立(draft 已 markdown),F′ 不重做是否正确?
4. `/gates` 取 `task.title/assigned_to` 是否纯呈现(数据现成、缺失回落不预填)?
5. §4 穿透真接线那条设计是否够(GateClient autospec + REAL session)防 F-244-2 掩盖?
6. B14 `/mode` 裸异常改引导、B11 workspace 提示 —— 是否属"真实可达失败态"?
