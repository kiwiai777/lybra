# AIPOS-207 — scope reachability systematic fix (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-23
- task-id: AIPOS-207 (proposed)
- epic: v1.0 Scope B — gate-hardening / test-fidelity;源 = AIPOS-206 dogfood finding **F-cop-204scope-1**(DL-20260623-10)
- discipline: 不改 197/199/204 语义;只调 `service_mode.ROLE_SPECS` + 加测;依赖隔离不变;不碰其它 gate 面。不实现至获批。

---

## §1 背景 + 根因

AIPOS-206 dogfood 烧出 **F-cop-204scope-1**:`tools.py` 引用 **8** 个 scope,`ROLE_SPECS` 仅授 **5**;`draft_publish` / `intake_submit` / `owner_decision_record` **无 mintable 角色持有**。AIPOS-204 单测用**手搓 registry**(给 owner 临时加 draft_publish)假性 PASS,**从未测真 serve-rotate 凭据路径** —— 这是 **RF-5 在 scope 注册层重演**(测试用陈旧/手造前提,不验真凭据)。

**两条 capability 注入路径(已查实)**:
- **(A) service-role**(serve-rotate):`http_sse._request_capability` 在 `service_role_registry` 非空时**恒**用 registry 设 `REQUEST_CAPABILITY`;`tools._capability_token()` **不回落 env**。→ service 模式只认 `ROLE_SPECS` scopes。
- **(B) capability-token env**:`service_role_registry is None`(legacy stdio/HTTP)时,`tools._capability_token()` 读 `LYBRA_CAPABILITY_TOKEN`(AIPOS-109);operator 自铸,任意 scope。`mcp-config` 正发 `capability_token_env=LYBRA_CAPABILITY_TOKEN`。

---

## §1.1 逐 scope reachability 审计(8 个)

| # | scope | 引用工具 | 持有路径 | 处置 |
|---|---|---|---|---|
| 1 | `queue_claim` | claim dry/confirm | (A) executor/owner/auditor | ✅ 可达 |
| 2 | `queue_return` | return dry/confirm | (A) executor/owner | ✅ 可达 |
| 3 | `owner_confirm` | claim/return/publish confirm | (A) owner | ✅ 可达 |
| 4 | `audit_dispatch` | audit dispatch | (A) owner-dispatch | ✅ 可达 |
| 5 | `audit_verdict` | audit verdict | (A) auditor | ✅ 可达 |
| 6 | `draft_publish` | AIPOS-204 publish dry/confirm | **(A) 漏授** | 🔴 **修:补 owner 角色**(§2) |
| 7 | `intake_submit` | AIPOS-109 intake dry/confirm | **(B) 有意** | ✅ **登记豁免**(§8 据) |
| 8 | `owner_decision_record` | AIPOS-113 owner-decision dry/confirm | **(B) 有意** | ✅ **登记豁免**(§8 据) |

---

## §2 修复:`draft_publish` 补到 owner 角色(仅 owner)

- `ROLE_SPECS` 的 `owner` 角色 `scopes` 增 `draft_publish` → `[queue_claim, queue_return, owner_confirm, draft_publish]`。
- **DG-11 依据**:Owner 在 proceed 动作里做 `draft_publish_dry_run` + `draft_publish_confirm`;owner 角色本就持 `owner_confirm`,补 `draft_publish` 后两 scope 齐 → publish confirm 成立。
- **只补 owner**;**不补** executor / copilot(copilot 必须留 `scopes: []`)。

---

## §3 保 AIPOS-204 双 scope 结构 + ★A1 不削弱

- `draft_publish_confirm` 仍要求 `draft_publish` **且** `owner_confirm`(tools.py:844/850 不动)。
- **★A1 回归断言(真凭据)**:executor / copilot 真 serve-rotate 凭据调 `draft_publish_dry_run` / `draft_publish_confirm` 仍 **SCOPE_DENIED**(executor 无 draft_publish;copilot 无任何写 scope)。
- publisher-only(若未来某角色仅 draft_publish 无 owner_confirm)confirm 仍 SCOPE_DENIED —— 双 scope 规则保持。

---

## §4 ★ standing reachability 回归测(防复发,核心)

**test-fidelity 修复**:堵假性 PASS,以后任何新 gate 工具加 scope 但忘授角色 → 这条测试**红**。

1. **真 serve-rotate 凭据(禁手搓 registry)**:测试用 `service_mode.build_connection_config` / 真 rotate 铸出的 registry(非测试内手写 dict)。
2. **scope 枚举自 tools.py**:程序化收集 tools.py 全部 `*_SCOPE` 常量(单一真相,新增自动纳入),得 `REQUIRED_SCOPES`。
3. **可达并集**:`ROTATE_UNION` = 真 rotate 各角色 scopes 并集。
4. **登记豁免集**:显式 `CAPABILITY_TOKEN_EXEMPT = {"intake_submit", "owner_decision_record"}`(§8 据;带注释指明路径 B + AIPOS-109/113)。
5. **断言**:`REQUIRED_SCOPES ⊆ ROTATE_UNION ∪ CAPABILITY_TOKEN_EXEMPT`。任何新 scope 既不授角色又不登记豁免 → 失败,报出该 scope 名。
6. **豁免正向测(遍历整集,坐实非纸面 — Owner 加固)**:**遍历整个 `CAPABILITY_TOKEN_EXEMPT` 集**(非只测两个写死名),对**每个**豁免 scope 起一个 **`service_role_registry=None` + `LYBRA_CAPABILITY_TOKEN`(含该 scope)** 的 gate,断言其 dry-run **不** SCOPE_DENIED(路径 B 真可达);缺 scope → SCOPE_DENIED。→ 「登记豁免」必须附带「路径 B 真可达」之证;EXEMPT 再不能被当作掩盖未来漏授的逃生舱(懒人塞名进 EXEMPT 会因无可用路径 B 而红)。需要 scope→dry-run-tool 的映射表(随 tools.py 维护)。
7. **draft_publish 正向(真凭据)**:owner 真 rotate 凭据 `draft_publish_dry_run` 成立(非 SCOPE_DENIED)。

---

## §5 范围红线 / 明确不做

- **红线**:不改 AIPOS-197/199/204 **语义**(scope 常量、双 scope 闸、confirmer 归属不动);只调 `ROLE_SPECS`(owner +draft_publish)+ 加测;copilot 留 `scopes: []`;executor 不得 draft_publish;依赖隔离不变(无新依赖);不碰其它 gate 面 / Wall / Layer-3。
- **明确不做**:把 intake/owner_decision 提升为 service 角色(它们有意走路径 B,本片不扩面);新增任何角色;改 capability-token env 机制;web/copilot/206b。

---

## §6 测试

- **T1 standing reachability(★)**:真 rotate 凭据;`REQUIRED_SCOPES ⊆ ROTATE_UNION ∪ EXEMPT`;新增未授/未豁免 scope → 红。
- **T2 draft_publish 可达(真凭据)**:owner rotate 凭据 `draft_publish_dry_run` 非 SCOPE_DENIED。
- **T3 ★A1 不削弱(真凭据)**:executor / copilot rotate 凭据 `draft_publish_dry_run`+`draft_publish_confirm` → SCOPE_DENIED。
- **T4 双 scope 保持**:仅 draft_publish 无 owner_confirm 的 capability → confirm SCOPE_DENIED(可经路径 B 构造 publisher-only token 验证)。
- **T5 豁免正向(遍历整集)**:**对 `CAPABILITY_TOKEN_EXEMPT` 的每个 scope**,路径 B(registry=None + env token)下其 dry-run 非 SCOPE_DENIED;缺 scope → SCOPE_DENIED。EXEMPT 不能掩盖未来漏授(无可用路径 B 的名塞进 EXEMPT → 红)。
- **T6 全量回归**:`tools/` 全绿(含既有 204/197/199 测不回归);依赖隔离(无 textual)保持。

---

## §7 cc glm 审计点

1. **三死 scope 处置明确**:draft_publish 补 owner(有 DG-11 据);intake/owner_decision 登记豁免(有 AIPOS-109/113 + 路径 B 据,§8),无模糊。
2. **draft_publish 只补 owner**:executor/copilot 未获;copilot 仍 `scopes: []`。
3. **★A1 经真凭据回归仍 SCOPE_DENIED**:executor/copilot rotate 凭据调 publish dry/confirm 被拒(非手搓)。
4. **standing 测用真 serve-rotate 凭据**(禁手搓 registry);scope 自 tools.py 枚举;新增漏授会红。**EXEMPT 非逃生舱**:豁免正向测遍历整个 EXEMPT 集,逐个证路径 B 真可达(懒人塞名 → 红)。
5. **204 双 scope + 语义不变**:confirm 仍需 owner_confirm;197/199/204 常量与闸未动。
6. **全量 tools/ 绿**;无新依赖;未碰其它 gate 面。

---

## §8 intake_submit / owner_decision_record 豁免依据(查实)

- **intake_submit**:AIPOS-109 首个 **stdio MCP 受控写工具对**(`lybra_intake_submit_dry_run/confirm`),设计走 **stdio MCP + `LYBRA_CAPABILITY_TOKEN`** —— operator/外部 intake 集成自铸 capability token,非 service 角色。协议 `lybra_mcp_server_protocol.md` §AIPOS-109 载明。
- **owner_decision_record**:AIPOS-113 stdio MCP 受控写工具对(AIPOS-112 backend),同路径 B。
- **机制坐实**:`tools._capability_token()` 在 `REQUEST_CAPABILITY` 未设(`service_role_registry is None`)时读 env;`mcp-config` 发 `capability_token_env=LYBRA_CAPABILITY_TOKEN`。
- **结论**:二者**非死 scope**,有**有意注入路径(B)**,与 DG-11 service-mode 客户端流无关;本片**登记豁免 + 正向测坐实**,不提升为 service 角色(避免无谓扩面)。**若**未来要在 HTTP service-mode 暴露外部 intake,再单议(本片不做)。

---

## §9 与后续片分界

- 本片(AIPOS-207)= scope reachability 审计 + draft_publish 补 owner + standing 回归测。
- **回 dogfood**:AIPOS-207 finalize 后,补跑 AIPOS-206 dogfood 的 **gated N0/N4/N5**(种子+发布+executor 接力)闭合全环。
- 不含:intake/owner_decision 的 service-mode 暴露(若需,另议);R2/R5/206b/DG-8。

---

> **DRAFT 结束。** 待你复核(尤其 §8 intake/owner_decision 处置依据 + §4 standing 测用真 serve-rotate 凭据)→ Owner 批 → 实现 + §6 测 → cc glm 审计(§7)→ 你抽查 → finalize → 回 dogfood 补跑 gated N0/N4/N5。不实现至获批。
