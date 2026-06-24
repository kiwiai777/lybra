# AIPOS-208 — TUI AI-authoring first screen (DG-8) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-23
- task-id: AIPOS-208 (proposed)
- epic: v1.0 Scope B — backlog (2) TUI AI-authoring;依据 = **DG-8**(设计 §7 R1 定调)+ live 证的 **AIPOS-206** copilot(DL-20260623-09/-12)
- discipline: 纯 client 侧;不改 gate;不回退任何 AIPOS-206 不变量;不实现至获批。

---

## §1 背景 + 目标

DG-8:把已 live 证的 copilot 起草打磨成**产品首屏** ——「启动 TUI → 一句话『你想下达什么任务』→(多轮)收敛成一张**结构良好的任务卡** → Owner 批 → 经 gate 发布」。给 Owner 用 agent 的熟悉感,但**只读焊死、批准后换人执行**(R1:起草者≠确认者≠执行者)。

**N4/N5 教训(本片要根治)**:dogfood 里自由文本 LLM 输出不可靠(间歇拒绝、需把 schema 塞进 prompt)。本片把**卡的结构做成代码保证**:LLM 供语义,copilot.py 供结构 + 校验,产出**必是 gated-publishable + claimable 的卡**,而非自由文本。

---

## §2 范围(建议核心)

1. **首屏入口体验**:启动 TUI(配了 LLM 时)**直接进 chat-to-task**,自然语言下达任务;Shift+Tab 仍可切 observe/confirm。未配 LLM → 回落 observe(现状)。
2. **★ 任务卡 schema/模板质量(关键)**:copilot 把自然语言转成**通过 `draft_validator` 校验**的卡。**结构由代码保证**:
   - LLM 在受约束 prompt 下产出**语义字段**(title / body / task_mode / priority / output_target / project / assigned_to 等)。
   - `copilot.py` 用模板补齐必填字段、装配 frontmatter+body、**在内存对装配结果跑 `draft_validator` 校验**(只读校验,不写盘),不通过则修复/重试或把缺口回报 Owner。
   - 产出 `DraftProposal.content` = **conformant 卡**;落档仍是 Owner proceed(AIPOS-206 land + AIPOS-204 publish)。
3. **context_bundle 关联**(承"存 vs 送";`context_bundle` 本就是必填字段):copilot **只读建议**一个合法 bundle —— 读现有队列任务的 `context_bundle`(经 read-tool)+ 可选 `lybra_context_pack_build` 预览 executor 将拿到的 context-pack;Owner proceed 时可改;**copilot 不凭空造**。
   - **无匹配 bundle 的优雅 fallback(Owner 加固)**:若现有 bundle 集中无合适项,copilot **不凭空造、也不卡死 validate** —— 把 `context_bundle` 缺口**明示给 Owner**(DraftProposal 带 `needs_bundle`/blocking_reasons),由 Owner 在 proceed 时指定 bundle ref。卡在 Owner 指定前保持"待补 bundle"状态(可预览、不可发布)。
4. **(候选,建议含最小版)多轮 draft 收敛**:对话式打磨卡再发布(`memory.l3_chat` 已支持多轮);每轮趋向发布前仍守 **R6-b 起草前重读 L0(RF-5)**。重型收敛 UX 标 candidate。

### §2.5 已核实的卡字段契约(待决 d → 落定,非假设)

来源 `tools/aipos_cli/draft_validator.py`:
- **必填 12**:`task_id, title, project, assigned_to, context_bundle, task_mode, priority, status, created_by, needs_owner, output_target, artifact_policy`。
- **建议 7**(利于 claim/管线):`agent_instance, model_tier, task_type, polling_mode, claim_policy, report_mode, recurrence`。
- **禁现(运行时字段不得入草稿)**:`claim_id, claimed_by, claimed_at, active_session_id, last_session_id, completed_by, completed_at, blocked_by, blocked_at`。
- **其它**:`task_id` 格式 `^[A-Za-z0-9][A-Za-z0-9._-]*$`;**文件名 = slug(task_id)**;**body 必填**。
- 模板默认值(copilot 填):`status: pending`、`created_by: copilot`、`needs_owner: false`、`artifact_policy: formal_write`、建议字段给 claimable 默认(如 `claim_policy: assigned_agent_only`、`task_type: one_shot`)。task_id 由 copilot 提议(Owner 可改),slug 落档时对齐文件名。

---

## §3 必须保持的 AIPOS-206 不变量(不得回退)

- copilot **结构性只读**(`copilot` 角色 scopes [])、**回路零文件写**(`copilot.py` 不 import 写 helper;装配+校验全在内存)。
- 唯一真相出口 = **DRAFT 数据 → Owner proceed → gate publish**(AIPOS-204,confirmer=owner)。
- **copilot 侧 ★A1**:copilot 凭据调 `*_confirm`/`draft_publish` → SCOPE_DENIED。
- 记忆 **L0–L3 + 三纪律**(compact 不碰 L0 / 起草前重读 L0 / chat non-truth)。
- **按项目 session(单项目)**。
- LLM 裸 HTTP、secrets fingerprint-only、egress 披露不变。

> 注:`copilot.py` 可 import `draft_validator`(**只读校验器**,非写 helper;不在 206 审计禁列 draft_writer/board_adapter/publish_draft/execute_dry_run 内)——这正是"结构由代码保证"的手段。

---

## §4 设计(client 侧,复用 206/205/204)

- `copilot.py` 增**结构化起草**:`draft_task_card(intent, ...) -> DraftProposal`,内部 (a) RF-5 重读 L0;(b) LLM 受约束产语义字段(可要求 JSON 输出便于解析);(c) 模板装配 conformant 卡;(d) `draft_validator` 内存校验 → conformant 才返回,否则带 blocking_reasons 回 Owner。**零文件写**。
- `app.py`(tui lane):首屏默认 chat-to-task(配 LLM 时);输入即下达任务;展示 conformant 卡预览 + `proceed <slug>` 走 Owner land+publish(复用 AIPOS-206)。
- `__main__.py`:首屏模式选择(LLM 配置在 → copilot 首屏;否则 observe)。
- **不改 gate / scope(197)/confirmer(199)/204**;无新依赖(LLM 仍裸 urllib;`draft_validator` 已在树内、stdlib)。

---

## §5 明确不做

改 gate(纯 client 侧)、web fetch(206b)、多项目/跨项目(R2)、decision_log 目录化(R5)、LLM digest(R6 defer)、executor 执行、AI 自动 confirm/publish(永不)。

---

## §6 待决项(Owner 拍)

- **(a) context_bundle 关联方式**:① copilot 只读建议(读现有 bundle + context_pack 预览,Owner 可改)②Owner 手选 ③v1.0 留默认。**建议 ①**(copilot 只读建议 + Owner proceed 时确认/改;不凭空造)。
- **(b) 首屏默认**:① 配 LLM 即 chat-to-task 首屏(Shift+Tab 仍切三模式)②保留 Shift+Tab 才进 copilot。**建议 ①**(DG-8 首屏目标;未配 LLM 回落 observe)。
- **(c) 多轮收敛**:① 本片含最小多轮 refine ②全缓。**建议 ①最小版**(memory 已支持;重型 UX 标 candidate)。
- **(d) 卡字段集**:**已核实落定**(§2.5,源 draft_validator)——非假设。请确认采用该契约。

---

## §7 测试(结构经 FakeLLM,质量留 live)

- **T1 卡 conformance(★)**:FakeLLM 供语义 → copilot 产卡 → `draft_validator.validate` 无 blocking_reasons;且能过 `draft_publish_dry_run`(真 rotate owner 凭据,承 AIPOS-207)。
- **T2 ★A1 回归**:copilot 凭据 `*_confirm`/`draft_publish` → SCOPE_DENIED。
- **T3 copilot 零文件写回归**:`draft_task_card` 任意输入零 fs 写;不 import 写 helper(draft_validator 例外,只读)。
- **T4 起草前重读 L0(RF-5)保持**:`draft_task_card` 前有一次 read-tool 重读;truth 进 LLM 输入。
- **T5 context_bundle 关联落卡**:产卡含合法 `context_bundle`(来自只读建议),非空、在现有 bundle 集内或 Owner 指定。
- **T6 禁现字段**:产卡不含 FORBIDDEN_RUNTIME_FIELDS;slug=文件名;task_id 合法。
- **T7 多轮(若含)**:第二轮 refine 仍 RF-5 重读 L0;chat 标 non-truth。
- **T8 依赖隔离/全量**:copilot.py 无 textual、无第三方 SDK;`tools/` 全绿。
- 真 LLM 起草**质量**留 (8) 验收脚本或小 live 检;**本片证结构**。

---

## §8 cc glm 审计点

1. **卡 conformance 由代码保证**:copilot 产卡过 draft_validator + draft_publish_dry_run(真凭据);非靠 LLM 自觉。
2. **206 不变量不回退**:只读(scopes [])、零文件写、DRAFT→Owner→gate、★A1、L0–L3 三纪律、单项目 —— 逐条仍绿。
3. **context_bundle 只读建议**:copilot 不凭空造 bundle;落卡字段合法;来源可溯(read-tool)。
4. **字段契约对齐**:必填 12 / 禁现字段 / slug — 与 draft_validator 一致(非另立一套)。
5. **纯 client 侧**:gate/197/199/204 未改;draft_validator 为只读校验非写 helper;无新依赖;全量绿。

---

## §9 与后续片分界

- 本片(AIPOS-208)= chat-to-task 首屏 + 卡 conformance(代码保证)+ context_bundle 只读建议 +(最小)多轮。
- 不含:web fetch(206b)、多项目/跨项目(R2)、decision_log 目录化(R5)、LLM digest(R6)、executor 执行、真 LLM 质量验收(留 (8))。

---

> **DRAFT 结束。** 待你复核(尤其 §2.5 字段集已核实、§2.3/§6a context_bundle 关联、§3 206 不变量不回退)+ 拍 §6 a/b/c → Owner 批 → 实现 + §7 测 → cc glm 审计(§8)→ 你抽查 → finalize。不实现至获批。
