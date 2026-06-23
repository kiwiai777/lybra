# AIPOS-206 — Planning Copilot (DG-11) micro-plan (DRAFT, rev.2)

- status: draft
- authority: NONE — 未经 Owner 终批不实现;本稿(rev.2)按 Owner 复核意见定稿,交复核者再抽查
- date: 2026-06-23
- task-id: AIPOS-206 (proposed;待 board 分配)
- epic: v1.0 Scope B — Planning Copilot (DG-11);设计 = `design/Lybra_TUI_planning_copilot_设计决策_v0.md`(含 §7 R1–R6 / DL-20260623-08)
- discipline: copilot = **Owner 侧只读顾问**。**无 write/confirm/publish scope、无 truth-写路径、无任何文件写路径**;唯一真相出口 = DRAFT 数据 → Owner → gate confirm 发布(经 AIPOS-204)。不改 gate/scope(197)/confirmer(199)/204;不实现至获批。
- rev.2 改动:按 Owner 复核 —— ① DRAFT-write 边界结构红线(copilot 回路零文件写);② §7-1 凭据=read-only `copilot` 角色(已核实 read-tool scope 需求);③ LLM 裸 HTTP + egress 披露;④ 跨项目开窗整体移入 R2 片;⑤ web fetch 拆出 AIPOS-206b。§7 四项已拍板(见下)。

---

## §1 背景 + 目标

DG-11(+ R1 定调):Planning Copilot = 「**plan mode**,但只读结构焊死、批准后换角色执行 —— **起草者 ≠ 确认者 ≠ 执行者**」。本片给已建成的 **AIPOS-205 TUI 骨架**加 **copilot 模式**(Shift+Tab 预留位)+ LLM + 只读工具集 + **copilot 侧 ★A1**(端到端兜底)+ 按项目 session(R4,**单项目**)+ 文件记忆模型 L0–L3 + 三纪律(R6)。**v1.0 单项目**(Model 2 多项目 = R2 另片;跨项目只读开窗随 R2)。

---

## §2 范围(v1.0,单项目)

1. **copilot 模式(TUI)**:在 AIPOS-205 `TuiSession.MODES` 加第三模式 `copilot`(Shift+Tab 循环 observe → confirm → copilot)。模式状态栏显「copilot · read-only · scopes [无写]」。
2. **LLM 接入(裸 HTTP)**:copilot 用一把 LLM key(= DG-8 起草 key 同源)。**裸 HTTP**(零/轻依赖,守 gate 核心零依赖);`base_url` + `key` **可配**;**key fingerprint-only**,raw **绝不入 prompt / 日志 / 记录 / 上下文持久化**。对话式多轮(R4 内单项目)。**egress 披露(写入本 micro-plan + 运行时对 Owner 可见)**:copilot 规划会把 **workspace 内容(hydrate 的 L0/L1 片段、对话)发给所配置的外部 LLM 提供方**;这是规划功能的固有出口,Owner 配置即知情同意,与「真相写出口」正交。
3. **只读工具集(零写)**:
   - gate read-tool(`queue_list`/`task_preview`/`validate`/context-pack/state recovery)——经 AIPOS-205 `TuiSession.observe`,**不直读 truth 文件路径**;
   - **本地读文档 / 图片做调研**(workspace 内只读 + 看图)= **本片核心**。
   - **外部 web fetch 不在本片** —— 拆出独立片 **AIPOS-206b**(egress + 不受信外部内容 = prompt-injection 面,单列审查),见 §9。
4. **唯一真相出口(★DRAFT-write 边界 = 结构红线)**:
   - copilot 的 **LLM 回路(`copilot.py`)只返回 DRAFT 数据(内存对象/字符串),自身不写任何文件、无 write scope、无文件写路径**。
   - DRAFT 的落地与发布**全部由 TUI 层在同一个 Owner「proceed」动作内完成**(**零增 Owner 操作**):Owner 在 copilot 面板按 proceed → TUI(a) 若留档则把 DRAFT 写 `5_tasks/drafts/`(本项目;此写由 **TUI/Owner 动作**发起,非 copilot 回路)→(b) 喂 AIPOS-204 `lybra_draft_publish_dry_run` →(c) 同动作内 `lybra_draft_publish_confirm`(confirmer=owner)。**copilot 自己既发不了、也写不了文件**。
5. **按项目 session(R4,单项目)**:copilot session 锚单项目;DRAFT 只落本项目。**v1.0 = 单项目 session + 结构性拒绝任何跨项目写**。**跨项目只读「开窗」整体移入 R2 多项目片**(本片不做半个窗)。
6. **文件记忆模型 L0–L3(R6)**:**L0** 真相文件(workspace truth)/ **L1** 派生 INDEX(只读派生)/ **L2** 按需 hydrate(进 copilot 上下文)/ **L3** compact 仅 chat。**三纪律(焊死)**:(a) compact 不碰 L0;(b) **起草前重读真相本体**([RF-5]:DRAFT 生成前必重读 L0 相关 truth,不靠陈旧上下文);(c) 持久化 chat 标 **non-truth**。**LLM digest defer**(本片不做 LLM 摘要派生)。

---

## §3 ★ copilot 侧 ★A1(结构性只读,可验证)+ 凭据(已核实)

- **凭据形态(§7-1 拍板 = read-only `copilot` 角色)**:`service_mode.ROLE_SPECS` 增一条 `{"role": "copilot", "token_ref": "svc-copilot", "scopes": []}`,`lybra serve rotate` 随之铸出 copilot Bearer。
- **read-tool scope 需求已核实(非假设)**:`tools/mcp_server/tools.py:30` —— *"Lybra MCP exposes read tools by default. Write tools are visible only with scoped capability."* read-tool **无需任何 scope**;故 **`scopes: []` 既足以读、又对所有写/确认/发布 op 结构性 SCOPE_DENIED**(`queue_claim`/`queue_return`/`owner_confirm`/`draft_publish`/`audit_dispatch`/`audit_verdict` 全不在集内)。显式角色 > 「无 token」:可审计、留痕、★A1 干净。
- **强制审计项(进端到端测试)**:用 **copilot 实际凭据(svc-copilot Bearer)**调一次 `lybra_draft_publish_confirm` / `lybra_queue_claim_confirm` → **期望 SCOPE_DENIED** —— 与 executor ★A1 **同形**,回归兜底,非仅设计声明。

---

## §4 设计(client 层,复用 205/203;DRAFT-write 边界结构化)

- copilot 逻辑落 **`tools/lybra_tui/`**:Textual UI 在 `app.py`;**copilot 纯逻辑入新 `copilot.py`,零 textual,可 core-lane 测**。
- copilot 用一个**只读 GateClient**(AIPOS-203,svc-copilot Bearer,`scopes: []`);观测复用 `TuiSession.observe`;**绝不**持有/调用 write/confirm/publish(它没那 scope;任何 confirm/publish 调用 → 结构性 SCOPE_DENIED)。
- **★DRAFT-write 边界**:`copilot.py` 的接口**只返回 DRAFT 数据对象**(不接受文件路径、不 import 任何写 helper、不碰 fs)。**落 `drafts/` 与喂 204 dry_run→confirm 由 TUI 层在 Owner proceed 动作里做**(复用 AIPOS-205 confirm 面板 + AIPOS-204 publish)。这把「AI 起草」与「真相写/发布」在**代码结构层**隔开,而非仅靠 scope 一道。
- LLM 调用封装在 `copilot.py`(裸 HTTP;`base_url`/`key` 可配;key fingerprint-only;raw 不入日志/记录/上下文持久化)。
- **依赖隔离延续**:裸 HTTP 走 stdlib `urllib`(同 203 的 `ProxyHandler({})` 口径),**不引第三方 SDK**;gate 核心零依赖不变。

---

## §5 范围红线 / 明确不做

- **红线**:copilot 无 write/confirm/publish scope、**无任何文件写路径**(`copilot.py` 回路零 fs 写);唯一出口 = DRAFT 数据→Owner proceed→TUI 落档+gate publish;起草前重读 L0(R6-b);compact 不碰 L0;chat 标 non-truth;secrets 仅 fingerprint;状态经 read-tool 不直读 truth;无 daemon/调度/agent 执行。
- **明确不做**:**外部 web fetch(→ AIPOS-206b)**、**跨项目只读开窗 + 多项目(→ R2 片)**、**decision_log 目录迁移(→ R5 片)**、**AI 起草发布链主体(DG-8 另片)**、web client(v1.1)、LLM digest(R6 defer)、executor 执行(copilot 只起草)。

---

## §6 端到端测试(RF-5 教训:结构性断言进 e2e)

- **T1 copilot 侧 ★A1(关键)**:svc-copilot 凭据调 `lybra_queue_claim_confirm` / `lybra_draft_publish_confirm` → **SCOPE_DENIED**,零写(对真/mock gate)。
- **T1b ★DRAFT-write 无写(关键)**:`copilot.py` 回路在**任何输入下不写任何文件**(断言:copilot 凭据/回路无 fs 写副作用;接口返回 DRAFT 数据而非写盘;不 import 写 helper)。
- **T2 只读边界 + scope 核实**:断言 svc-copilot(`scopes: []`)下 read-tool **可见**、write/confirm/publish **不可见**(坐实 §7-1 非假设);copilot 工具集仅 read-tool + 本地读文档/图;无直读 truth 文件路径(经 read-tool);**无 web fetch 调用面**。
- **T3 DRAFT 出口**:DRAFT 落 `5_tasks/drafts/` 由 **TUI/Owner 动作**发起;发布须经 Owner→AIPOS-204(copilot 不自发)——断言 copilot 路径不写 pending/truth/drafts。
- **T4 记忆三纪律**:(a) compact 操作不改 L0(断言 L0 文件不变);(b) **起草前重读 L0**(DRAFT 生成前有一次 truth 重读;可注入式断言);(c) 持久化 chat 标 non-truth(标记字段)。
- **T5 按项目 session(单项目)**:DRAFT 只落本项目;**任何跨项目写被结构拒绝**(跨项目只读开窗本片不实现,故无开窗路径)。
- **T6 secrets**:LLM key / 任何 token 仅 fingerprint,不入 copilot 上下文持久化 / 日志 / prompt。
- **T7 依赖/隔离**:copilot 纯逻辑(`copilot.py`)core-lane 可测(无 textual、无第三方 SDK);全量回归绿。

---

## §7 复核拍板(rev.2 = 已决,非待决)

1. **copilot 凭据形态** → **read-only `copilot` 角色**(`ROLE_SPECS` 加 `scopes: []`)。已核实 read-tool 无需 scope(tools.py:30),空 scope 足以读且对写/确认/发布 SCOPE_DENIED。**实现时再以测试坐实** read-tool 在空 scope 下确实可见(T2)。
2. **LLM 接入** → **裸 HTTP**(stdlib urllib,无 SDK);`base_url`/`key` 可配、fingerprint-only、raw 不入 prompt/log;**egress 披露已写入 §2.2**。
3. **跨项目只读开窗** → **整体移入 R2 多项目片**,本片**不做半个窗**;v1.0 = 单项目 session + 结构性拒绝跨项目写(§2.5/§9)。
4. **web 调研** → **拆分**:本地读文档/图片入本片核心(§2.3);**外部 web fetch → 新片 AIPOS-206b**(egress + prompt-injection 面单列审查,§9);§5 明确「web fetch 不在本片」。

---

## §8 cc glm 审计点

1. **copilot 侧 ★A1**(强制,端到端):svc-copilot 凭据 → *_confirm/draft_publish → SCOPE_DENIED + 零写(T1)。
2. **★DRAFT-write 无写**(强制):`copilot.py` 回路任何输入下零文件写、无写 helper import、返回 DRAFT 数据(T1b)。
3. **read-tool scope 核实**:断言 svc-copilot(`scopes: []`)下 read-tool 可见、write/confirm/publish 不可见(T2;坐实 §7-1 非假设)。
4. **结构性只读**:copilot 无 write/confirm/publish scope/工具;唯一出口 DRAFT 数据→Owner→gate;不旁路;无 web fetch 面。
5. **记忆三纪律**:compact 不碰 L0、起草前重读 L0、chat 标 non-truth —— 逐条实测(T4)。
6. **依赖隔离延续**:LLM 裸 HTTP(无 SDK)、UI 依赖只入 client/extra,gate 核心零依赖;core-lane 绿。
7. **范围诚实**:无外部 web fetch(206b)/无跨项目开窗+多项目(R2)/无 decision_log 迁移(R5)/无 AI 起草发布主体(DG-8);单项目 session。
8. secrets 仅 fingerprint(含 prompt 不含 raw);状态经 read-tool;无 agent 执行/daemon;egress 披露在档。

---

## §9 与后续片分界

- 本片(AIPOS-206)= **Planning Copilot v1.0 单项目**(模式 + LLM 裸 HTTP + 只读工具[含本地读文档/图] + ★A1 + ★DRAFT-write 边界 + 按项目 session + 记忆 L0–L3)。
- **AIPOS-206b** = 外部 **web fetch 调研**(egress + 不受信外部内容 = prompt-injection 面,单列安全审查)。
- **R2 片** = 多项目(scope 项目维度 + read-tool 项目过滤)+ **跨项目只读开窗**。
- **R5 片** = decision_log 目录化迁移。
- **DG-8 另片** = AI 起草发布链主体;**web client** = v1.1;**LLM digest** = R6 defer。

---

> **DRAFT(rev.2)结束。** 待复核者抽查(尤其 §2.4/§4 ★DRAFT-write 边界、§3/§7-1 read-tool scope 核实、§6 T1/T1b/T2)→ Owner 终批 → 实现 + §6 测试 → cc glm 独立审计(§8,含 ★A1 + DRAFT-write 无写 + read-tool scope 核实)→ Owner 抽查 → finalize。不实现至修订稿经复核者再核 + Owner 终批。
