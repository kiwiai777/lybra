# AIPOS-203 — Interactive confirm command (gate client, F-c7 根治) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-22
- task-id: AIPOS-203 (proposed;待 board 分配)
- epic: v1.0 Scope B — backlog item (4) TUI confirm panel 的前置 (DL-20260622-01 / DG-10 / 维度 6)
- slice: **(4b)** CLI 交互式 confirm client(F-c7 根治 + 预演 TUI-as-client)。TUI 骨架 + 收编进面板 = 后续片。
- discipline: 纯 client 端封装。**不改 gate / scope(197)/ confirmer(199)/ controlled-execute 语义**。不碰证据现场;raw token 仅 fingerprint;Owner token 仍 Owner 持有。

---

## §1 背景 + 为什么

F-c7(confirm 人机工程脆弱)在 **191B-rerun** 与 **AIPOS-202 Form-B** 两次都被迫手写 token-free wrapper(`~/confirm_rerun_*.sh`、`~/formb_confirm.sh`)印证其必要:换行/裸贴 token/TTL 窗口/RF-4 漏参反复踩坑。本片做一个**交互式 confirm 命令(gate client)**根治它,并**预演 TUI-as-client 模型**(架构决策补记 2026-06-22:前端=client over gate server,状态经 gate read-tool)。

---

## §2 形态(架构对齐)

- = gate 的 **MCP client**:owner-token 作 Bearer,经 **AIPOS-201 streamable-HTTP** 调 `*_confirm`。
- **不嵌 `board_adapter` / gate 逻辑**;不复制 scope/confirmer/controlled-execute —— 全部仍由 gate server 端执行。client 只负责「读门 + 呈现 + 采集 Owner 确认 + 发请求」。
- 状态读取**经 gate read-tool**(`lybra_queue_list` / `lybra_task_preview` / `lybra_validate` / state recovery),**不直读本地文件** —— 为未来 web client 可扩展(架构补记)。

---

## §3 根治 F-c7 的具体项(逐条对应踩过的坑)

| F-c7 坑(出处) | 本片解 |
|---|---|
| 待确认门不可见(每次手查队列) | **列出待确认门**:经 gate read-tool 找有 dry_run 待 confirm 的 claim/return,并显示 **dry-run 预览**(snapshot 摘要)。 |
| 10 分钟 TTL 窗口过期(191B 多次重取) | **TTL 倒计时 + 临近过期提示** + **一键刷新 dry-run**(client 重发 dry-run 取新 token)。 |
| owner token 裸贴命令行 / 断行 / 泄漏(191B、Form-B) | owner token **内部持有/读取**(从 connection.json 或 env,运行时),**绝不在命令行裸贴**;raw token **仅 fingerprint** 入任何输出/日志。 |
| RF-4 漏 actor/agent_instance/owner_policy_ref → BLOCK | **自动 replay** dry-run 的 `actor` / `agent_instance` / `owner_policy_ref` 三参到 confirm。 |
| executor 自供 confirm 风险 | 确认动作 = **Owner 显式**(`y/N` + 输入 owner-confirmation literal);executor/cc 不自供;client 不持有也不代填 Owner 确认。 |

---

## §4 设计(client-only)

- 新 CLI 模块 **= `tools/aipos_cli/`(Owner 已定 2026-06-22)**,独立模块;不进 board_adapter。
- 输入:gate URL + owner-token 来源(connection.json 路径 / env var,二选一;raw 不入命令行)。
- 流程:
  1. `initialize`(streamable-HTTP)+ 经 read-tool 列**待确认门**(claim/return 有 pending dry-run preview)。
  2. 选一门 → 显示 dry-run 预览 + **TTL 倒计时**;过期→**刷新**(重发 dry-run)。
  3. Owner `y` + 输入 confirmation literal → client 组 `*_confirm`(**自动带回三参** + dry_run_token)→ 经 owner Bearer 发 gate。
  4. 显示 gate 响应(confirmer 留痕摘要),raw token fingerprint-only。
- **不做**:TUI 骨架(后续片);AI 起草 / 发布门控(分别后续片);任何 gate / scope / confirmer 改动。

---

## §5 范围红线

- 不改 gate、scope(197)、confirmer(199)、controlled-execute、196a、L3、Wall、service_mode。
- 纯 client:所有授权/scope/confirmer/落盘仍由 gate server 端;client 不旁路。
- owner token 不裸贴、不入日志、仅 fingerprint;Owner 仍持有(client 读取≠cc 持有;若 cc 跑测试用 executor-scope only)。
- 守架构补记:client over gate,状态经 read-tool,不直读文件,不新增 daemon。

---

## §6 诚实定界

- 这是 **CLI 交互式 confirm client**(F-c7 根治)。**TUI 骨架 + 把它收编进 confirm 面板 = 后续片**(backlog item 4 主体)。
- owner token 仍 **Owner 持有**;本 client 让 Owner 确认更安全/省心,但不改「confirm 需 Owner」这一结构。
- 不证多 agent / 不碰发布门控(F-c4 是另片)。

---

## §7 测试计划

对一个起着的 gate（serve-http + service connection）：
- **T1 列门(只读)**:executor-scope token 列待确认门 = 只读成功(不能 confirm)。
- **T2 owner confirm 闭环**:owner-scope token 完成 claim + return confirm；**读盘断言 `confirmer_role=owner`** + fingerprint。
- **T3 TTL**:制造过期 dry_run_token → client 提示过期 → 刷新取新 token → confirm 成功。
- **T4 三参 replay**:confirm 自动带回 actor/agent_instance/owner_policy_ref(对照:漏参会 BLOCK 的反证)。
- **T5 无泄漏**:raw owner token 不出现在任何 client 输出/日志(仅 fingerprint);命令行无裸 token。
- **T6 状态经 read-tool**:列门 / 预览走 gate read-tool,不直读文件(注入式断言或 mock gate)。

---

## §8 cc glm 审计点

1. **纯 client**:不旁路 gate / scope(197)/ confirmer(199)/ controlled-execute —— confirm 真经 gate server。
2. **owner token 不泄漏**:仅 fingerprint;无裸贴;不入日志/记录。
3. **F-c7 各坑确有对应解**(列门 / TTL+刷新 / 三参 replay / Owner 显式确认),逐条实测。
4. **状态经 gate read-tool**(不直读文件)—— 守架构补记、web 可扩展。
5. 红线:无 gate 改动(git diff 限 client 模块 + 测试);executor 不自供 confirm;不新增 daemon。

---

> **micro-plan 经 Owner 复核 PASS（2026-06-22）;模块落点 = `tools/aipos_cli/`。** 下一步:实现 client + §7 测试 → cc glm 独立审计(§8)→ 你抽查 → 批准 → finalize（连同架构补记 + roadmap）。不实现至获批。
