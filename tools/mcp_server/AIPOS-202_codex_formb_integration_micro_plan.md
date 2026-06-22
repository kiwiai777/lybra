# AIPOS-202 — codex 真 Form-B 接入实证 (片二) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不执行;本稿仅供复核
- date: 2026-06-22
- task-id: AIPOS-202 (proposed;待 board 正式分配)
- epic: v1.0 Scope B — Form-B 非-Claude 接入(DL-20260622-01,DG-7 / 维度 4)
- slice: **片二(真接入实证)**;片一 = AIPOS-201 gate streamable-HTTP 传输(DONE, DL-20260622-02)
- discipline: 这是一次**受治 live 实证**,不是码改任务。无产品码改动(若闭环遇阻,登记 finding 分流,绝不硬改产品码)。不碰首跑/rerun 证据现场(`~/lybra-191b-workspace`、`~/lybra-191b-rerun-workspace`)。raw token/key 仅 fingerprint,Owner 带外出 confirm,executor 不自供。

---

## §1 目标 + 诚实定界

**目标**:真实 codex(`codex-cli 0.141.0`,ChatGPT 订阅)经 **Form B**(自有 agent 直连 gate,**非沙箱**)完成一次受治闭环 = v1.0 **维度 4「≥1 真非-Claude 接入」达成证据**(DL-20260622-01;不降级为纸面)。

**诚实定界**:
- 这是 **Form B**(codex 在用户侧主机直接跑,直连 L1 Gate + L3 兜底)。**codex 入 Wall(Form A,容器隔离)仍是 v1.1 / E2,不在本片。**
- 自治档 = **Supervised**(每门 Owner 带外 confirm)。Delegated/Standing 不在本片。
- 本片证「非-Claude agent 能经受治 gate 跑闭环 + ★A1 在其上同样成立」,**不**证异构双 harness 互审(那是 v1.1)。

---

## §2 接入方式(片一已支持)

- `codex mcp add lybra --url http://<gate-host>:<port>/mcp --bearer-token-env-var LYBRA_EXEC_TOKEN`
  —— streamable-HTTP 直连(AIPOS-201 已支持:initialize+session+tools/call 经 Bearer)。
- **executor token 经 env var**:raw 不上命令行、不进 `config.toml`(片一探查确认天然 fingerprint-only)。Owner token **绝不**进 codex 配置(codex 只持 executor scope)。
- gate bind loopback / 桥接网关 IP(非 0.0.0.0);Owner 显式 `lybra serve`(非 daemon)。

---

## §3 ★第一步验证(执行前必做,决定闭环形态)

**开放问题**:Form B **无 Wall** → 无容器 `/scratch` mount。196a(scratch→truth)摄取在 Form B 下走的是 gate 的 **approved-scratch-root** 路径(`LYBRA_APPROVED_SCRATCH_ROOT`)+ `queue_return` 引用,**还是**依赖 Wall projection?

- 第一步用一次 **dry-run 级**验证(无真 LLM 调用即可):确认 gate 在 Form-B 形态下,`queue_return` 能对一个**主机上 approved-scratch-root 内的 artifact** 做 196a 摄取(codex 在主机写,不经容器)。
- 若证 196a 需要 Wall projection 才能摄取 → 登记 finding,闭环改为「codex 产 artifact + queue_return 引用既有 truth-adjacent 路径」或把摄取降级为 L3 检测验证,**不硬改产品码**(分流到后续片)。
- 该验证结果写入执行报告首节。

---

## §4 Workspace + connection(建议:新建,不复用 rerun 证据现场)

**建议 = 新建一个 Form-B workspace**(如 `~/lybra-formb-workspace`),`lybra serve rotate` 铸新 3 角色 connection:
- `executor`(scopes: `queue_claim`, `queue_return` —— **无 `owner_confirm`**)→ 给 codex。
- `owner`(scope: `owner_confirm`)→ Owner 带外持有。
- (可选)`auditor` —— 本片不必。

**为何不复用 191B-rerun workspace**:rerun 证据(`~/lybra-191b-rerun-workspace/5_tasks/records/...AIPOS-191B-RERUN-01`)是冻结证据,**不得污染**。可借鉴其 connection 结构与 confirm-wrapper 套路,但**在新 workspace 铸新 token**,证据现场零接触。connection.json 0600,token 仅 fingerprint 入记录。

---

## §5 闭环节点(每门 Owner 带外 confirm)

| 节点 | 动作 | 期望 |
|---|---|---|
| N0 | 新 Form-B workspace + `lybra serve rotate`(3 角色)+ `lybra serve`(loopback)。Owner 发布一张低风险 docs 任务(经 gate, OWNER_CONFIRMED)。`codex mcp add lybra`(executor token via env)。 | gate up;task pending;codex 配好。 |
| N1 | codex `initialize` + `tools/list`(本 workspace 复证片一)。 | session 签发;codex 看到 executor-scope 工具(claim/return dry-run+confirm)。 |
| N2 | codex(executor)`queue_claim_dry_run`。 | dry_run_token;零写。 |
| **N3 ★** | codex(executor)`queue_claim_confirm` —— **自确认探针**。 | **SCOPE_DENIED**(非-Claude 侧的 ★A1 复证:executor 结构性不能 confirm)。 |
| N4 | Owner 带外用 **owner token** confirm N2 的 claim。 | claim 落盘;`confirmer_role=owner` + ref + fingerprint(199 在非-Claude 链路成立)。 |
| N5 | codex 产 artifact 写 approved-scratch-root(Form B:主机写,非容器);`queue_return_dry_run` 引用之。 | return dry_run_token;artifact 就位。 |
| N6 | Owner 带外 owner token confirm return。 | return 落盘 + `confirmer_role=owner`。 |
| N7 | 196a 摄取(按 §3 验证形态)→ `workspace_artifacts/...`(sha 匹配)。 | artifact 入 truth。 |
| N8 | L3 authority scan:摄取产物 `VALID`/`effective_truth`;旁路写入负对照 `ORPHAN_INVALID`/`QUARANTINED`。 | L3 在非-Claude 产物上正常裁决。 |

---

## §6 证据清单(执行报告须含,即时复查磁盘)

1. codex 经新传输 `initialize`(session)+ `tools/call`(claim/return)成功 —— 真 codex,非标准 client。
2. **★A1 复证**:N3 codex executor `*_confirm` → SCOPE_DENIED(截图/响应,带正确 literal)。
3. claim + return 记录 `confirmer_role=owner` + token_ref + fingerprint(199 在非-Claude 链路)。
4. artifact 196a 摄取 sha 匹配(或 §3 分流形态的等效证据)。
5. L3 `VALID` + 负对照 `QUARANTINED`。
6. codex transcript(若 Form-B 形态可捕获;非沙箱下可能仅 codex exec 输出)—— 诚实标注捕获边界。
7. 纪律自证:Owner 带外 confirm、executor≠owner、token 仅 fingerprint、证据现场未碰、手动 finalize。

---

## §7 红线 + 关键依赖

- **红线**:Supervised 每门 Owner confirm;executor≠owner;gate 非公网;fingerprint-only;不碰 191B 证据现场;无产品码改动(遇阻登记 finding 分流);手动 finalize。
- **关键依赖(需 Owner 确认)**:
  - codex 订阅可用 + 可跑闭环(片一已验存活 + tools/list;但 N5 需 codex 真做事即真 LLM 调用 —— codex exec 非交互下的 tool-call/审批行为是片一遗留的开放点,见 AIPOS-201 报告。**N3/N5 需 codex 真发起 MCP tool-call**;若 codex exec 非交互模式阻塞 tool-call,改用 codex 交互模式或调整 approval 配置,登记为 finding,不硬改产品 gate)。
  - 临时 LLM/订阅凭据由 Owner 掌握(往期 Owner: 临时 key 不归 cc 管)。

---

## §8 片二 / 后续分界

- **片二(本卡)** = codex Form-B 受治闭环实证 + 证据包 + ★A1 复证。达成 = v1.0 维度 4。
- **不在本片**:codex 入 Wall(Form A)= v1.1/E2;codex↔Claude 异构互审 = v1.1;Delegated/Standing 执行;轻量 SC② adapter 扩展。

---

## §9 cc glm 审计点(执行后)

1. 闭环真经 gate(controlled-execute),非旁路;每门有 Owner confirm 留痕。
2. ★A1 在非-Claude agent(codex)上成立(N3 SCOPE_DENIED 实证)。
3. 199 confirmer 留痕在 codex 链路的 claim+return 记录成立(读盘)。
4. artifact 摄取 + L3 裁决正确;负对照有效。
5. 证据现场(191B)零接触;token 仅 fingerprint,无泄漏;无产品码改动(git diff 空,仅证据/报告)。
6. 诚实定界守住:Form B 非沙箱、Supervised、非异构互审。

---

> **DRAFT 结束。** 待你复核 + 确认关键依赖(codex 订阅可跑闭环、临时凭据归属)。批准后:先做 §3 第一步验证 → 据形态定 N5/N7 → 执行 N0–N8 → 即时复查产出证据包 → cc glm 独立审计 → 你批准 → 手动 finalize。不执行至获批。
