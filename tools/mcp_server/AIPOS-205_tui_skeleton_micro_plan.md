# AIPOS-205 — TUI skeleton (client-over-gate) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-23
- task-id: AIPOS-205 (proposed;待 board 分配)
- epic: v1.0 Scope B — backlog item (4) TUI skeleton (DL-20260622-01 / DG-10 / DG-10 细化)
- baseline: `design/Lybra_TUI_planning_copilot_设计决策_v0.md`(DG-10 细化 = Python/Textual;架构 = client-over-gate;前端架构补记 DL-20260622-04)
- discipline: TUI = 薄 client。**不改 gate / scope(197)/ confirmer(199)/ AIPOS-204**;状态经 gate read-tool(不直读文件);无 daemon;UI 依赖只入 TUI 包。不实现至获批。

---

## §1 背景 + 目标

DG-10(+细化)定:v1.0 UI = **Python/Textual 薄 client over 既有 gate**。AIPOS-203 已交付一个 stdlib confirm client(F-c7 根治);本片把它**收编进一个 TUI 骨架**,并落地 client-over-gate 前端架构(DL-20260622-04):TUI 经 gate read-tool 渲染状态、经模态确认面板走 Owner confirm。**克制的 v1.0 骨架**,不含 copilot/AI/web。

---

## §2 范围(v1.0 骨架,克制)

1. **启动 → 连接**:TUI 启动经 **AIPOS-201 streamable-HTTP(owner-scoped Bearer)** 连一个 **Owner 显式启动的独立 gate**(`lybra serve`)。owner token 内部读取(connection.json by role / env,复用 AIPOS-203 `load_owner_token`),**不上命令行、fingerprint-only**。不自启 gate(Owner 先 `lybra serve`)。
2. **状态行 + 模式 + read-only 指示**:`Shift+Tab` 在「观测 / 确认」模式间切(copilot 模式留 DG-11 片);**状态栏常显** gate URL + token fingerprint + 当前 scope + 是否持 `owner_confirm`(read-only 指示)。
3. **`/`-菜单(经 gate read-tool 观测)**:斜杠命令面板,只经 read-tool:
   - `queue_list`(队列全局)、`task_preview`(单任务)、`validate`、context-pack、state recovery —— 覆盖**任务发布回溯 / 接任务态 / 审计回溯**的只读视图。
   - **不直读文件**(全部经 read-tool);渲染快照,**按需拉**(进视图/Owner 触发),无轮询。
4. **收编 AIPOS-203 confirm client 为模态确认面板**:claim / return / publish(AIPOS-204)三类门的模态确认,**复用 AIPOS-203 GateClient**:TTL 倒计时 + 一键刷新 + 三参 replay(RF-4)+ token 不裸贴(内部持有)+ **Esc / 默认 = 拒绝 / 不提交** + Owner 显式 literal。面板只调 `GateClient.preview/confirm`,不重写确认逻辑。

---

## §3 明确不做(诚实定界)

- **copilot / LLM / AI 起草**:DG-11 / DG-8 = **下一片**(Planning Copilot,含 copilot 侧 ★A1)。本骨架无 LLM、无 copilot 模式实体。
- **web client**:v1.1(本片只 TUI client)。
- **agent 运行 / 流式 model turn**:不跑 agent、不流式;只渲染 gate 状态(client 拉)。
- **daemon / 调度 / 轮询 / 心跳**:无(gate not engine)。
- **不改 gate/scope/confirmer/204**:纯 client 端封装。

---

## §4 ★ 第一待决项:Textual 依赖隔离方案

**现状**:无 pyproject;gate 核心 **stdlib 零依赖**(已核:唯一非 stdlib import 是内部 `from tools`)。Textual 是 v1.0 唯一新增第三方依赖,**绝不能让它成为运行 gate 的前置**。

**方案(建议,待 Owner/我确认)**:
- **(A,建议)** 引入 `pyproject.toml`,`[project.optional-dependencies]` 定 `tui = ["textual"]`:
  - gate 核心安装 **零依赖**;`pip install .[tui]`(或 `lybra[tui]`)才装 Textual。
  - **TUI 包独立**:TUI 代码落 `tools/lybra_tui/`(新包),**唯一** import textual 的地方;`tools/mcp_server` / `tools/aipos_cli`(gate/scope/confirmer/203 client)**永不** import textual。
  - **CI 分装两道**:① gate/core lane(不装 textual)跑 `tools/`(除 TUI 包)全绿 → 证 gate 无 UI 依赖可运行;② tui lane(装 textual)跑 TUI 包测试。
  - TUI 入口对 textual 做**包内 import**(不在任何 gate 模块顶层),`bin/lybra` 增 `tui` 子命令(import 失败→提示 `install lybra[tui]`,gate 仍可独立 serve)。
- **(B,备选)** 不引 pyproject,靠目录约定 + 运行时 lazy import 守隔离 —— 较弱(无 CI 强制的依赖边界)。**不建议**。

**待拍板**:☐ A(pyproject extra + tools/lybra_tui/ 包 + CI 双道) / ☐ B / ☐ 其它。**TUI 代码包落点**(`tools/lybra_tui/` vs `tools/aipos_cli/tui/`)一并示意。

---

## §5 红线

- TUI = **薄 client,不嵌 gate 逻辑**(不 import board_adapter 写路径 / 不复制 scope/confirmer/controlled-execute);确认只经 AIPOS-203 `GateClient`(它只经 JSON-RPC tools/call)。
- **状态经 gate read-tool**,不直读文件;全局可见非后门;file-authoritative 不变。
- **无 daemon / 调度 / 轮询**(gate not engine);TUI 退出不杀 gate;`lybra serve` 由 Owner 管。
- **不改** gate / scope(197)/ confirmer(199)/ AIPOS-204 / 196a / L3 / Wall。
- **依赖只入 TUI 包**;gate 核心 stdlib 零依赖;CI 能分装。
- owner token 内部持有、fingerprint-only;**Esc/默认=拒绝**(永不默认通过);executor 不自供 confirm。

---

## §6 测试思路

- **T1 连通**:对真/mock gate,TUI client 层 `initialize` + 状态栏取 scope/fingerprint(token 不泄漏)。
- **T2 /-菜单经 read-tool**:queue_list/task_preview/validate 经 read-tool 渲染;断言无直读文件路径(注入式/mock gate)。
- **T3 confirm 面板复用 203**:owner-scope 经面板 claim/return/publish confirm → 读盘 confirmer_role=owner(复用 203/204 已证路径)。
- **T4 ★A1 回归(关键)**:executor-scope 经面板 confirm → **SCOPE_DENIED**(executor ★A1);(为 DG-11 片预留 copilot 侧 ★A1 同形)。
- **T5 Esc/默认=拒绝**:模态默认不提交;无 literal/取消 → 不确认。
- **T6 依赖隔离**:gate/core lane(无 textual)跑 `tools/`(除 TUI 包)全绿;TUI 包测试在 tui lane。
- 渲染/交互层尽量薄,核心逻辑下沉到可单测的纯函数(状态映射 / 菜单动作 → GateClient 调用)。

---

## §7 cc glm 审计点

1. **纯 client 不旁路**:TUI 只经 read-tool + AIPOS-203 GateClient;无 gate 逻辑复制/旁路;git diff 不含 gate 改动。
2. **依赖隔离**:textual 只在 TUI 包;gate/core lane 无 textual 可跑(CI 证);pyproject extra 正确。
3. **不跑 agent / 无 daemon**:无调度/轮询/流式;按需拉。
4. **Esc/默认=拒绝**:模态默认拒绝,Owner 显式确认才提交。
5. **状态经 read-tool**(不直读文件);token fingerprint-only 不泄漏;★A1 经面板回归绿。
6. **范围诚实**:无 copilot/LLM/web/agent;不改 197/199/204。

---

## §8 与后续片分界

- 本片(AIPOS-205)= **TUI 骨架**(连接 + 观测 + 确认面板收编 203)。
- **下一片** = **Planning Copilot(DG-11)**:在 TUI 加 copilot 模式 + LLM + 只读工具集 + copilot 侧 ★A1 强制审计。
- web client = v1.1;AI 起草发布链由 DG-8/DG-11 片承接(经 AIPOS-204 gated publish)。

---

> **DRAFT 结束。** 待你复核 + 拍 §4(依赖隔离方案 A/B + TUI 包落点)。批准后:实现骨架 + §6 测试 → cc glm 独立审计(§7)→ 你抽查 → 批准 → finalize。不实现至获批。
