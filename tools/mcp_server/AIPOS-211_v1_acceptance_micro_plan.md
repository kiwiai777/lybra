# AIPOS-211 — v1.0 acceptance (two-layer: auto gate + manual runbook) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-24
- task-id: AIPOS-211 (proposed)
- epic: v1.0 Scope B — backlog (8) acceptance script;固化 AIPOS-205…210 + dogfood 全链为可回归验收门
- discipline: **纯验收物**(不改 gate/copilot/任何逻辑);**脚本不持 owner token、不自 confirm**(守 ★A1);不实现至获批。

---

## §1 背景 + 张力(先解)

Supervised 下 gate confirm = **Owner 带外**(★A1:executor/copilot/任何脚本结构上不能 owner_confirm)。故验收**不可全自动 confirm**——验收脚本若持 owner token 自 confirm,就破了整个问责模型。

**解法 = 两层**:
- **(a) 自动回归门**:机器可跑、**无需任何 confirm** 的结构不变量集,一条命令复现。
- **(b) 手动 release runbook**:install → serve → tui → 真 LLM 起草 → **Owner 带外 confirm** 发布 → executor claim(+Owner confirm)→ L3 VALID 的人工走查,**每道 Owner 闸显式列**;真 LLM 起草**质量**在此人工评估(承 208 留尾)。

---

## §2 范围

### §2.1 (a) 自动回归门(无 confirm,真凭据,聚合既有不变量)

落一个**验收聚合器**(`tools/acceptance/v1_acceptance.py`,**零 textual、零第三方**),一条命令产 `ACCEPTANCE: PASS/FAIL` + 逐项清单。**不重造逻辑** —— 调既有测 + 加结构检查:

1. **全量 tools/ 绿(core lane,无 textual)**:跑 `unittest discover`,断言全绿(承现状 469/1 skip)。
2. **依赖隔离**:仅 `tools/lybra_tui/app.py` import textual(grep);**gate 核心零依赖可 import 可跑**——**(Owner 加固,关乎别变纸面)在子进程里模拟 textual 缺席**(meta_path finder 拦截 `import textual`),再 import `tools.mcp_server.*` + 起停一个 in-proc gate。这样 `ACCEPTANCE: PASS` 真代表隔离成立,而非"恰好在装了 textual 的 dev 环境跑过了"(RF-5:别让断言因错误原因通过)。子进程退码非 0 → item 2 FAIL。
3. **scope reachability(承 207)**:跑 `test_scope_reachability`——真 serve-rotate 凭据下每 scope 可达或登记豁免;EXEMPT 经路径 B 实证。
4. **卡 conformance(承 208,真凭据非手搓)**:跑 `test_ai_authoring`——copilot 产卡过**真** `draft_publish_dry_run`(真 rotate owner 凭据)。
5. **copilot ★A1 + 零文件写 + RF-5(承 206)**:跑 `test_copilot`——copilot 凭据 `*_confirm` SCOPE_DENIED + 零写、draft 前重读 L0。
6. **presentation 不变量(承 210)**:跑 `test_presentation`——单 token、降级、零 textual。

> 全部**无 confirm、无 owner token、无外部网络**(卡 conformance 用 in-proc gate + 真 rotate 凭据,不调外部 LLM)。**真 LLM 质量不在自动门**(主观,留 runbook)。聚合器**只读式跑测 + 断言**,不写产品真相、不碰证据现场。

### §2.2 (b) 手动 release runbook(每道 Owner 闸显式)

落 `docs/v1_acceptance_runbook.md` —— 人工走查清单,跑在**一次性临时 workspace**(可弃,仿 dogfood;**不碰** 191b/formb/copilot 证据现场):

- **R0 install**:`pip install .[tui]`(装 textual);确认 gate 核心仍可零依赖装(`pip install .`)。
- **R1 serve**:`lybra serve`(serve rotate 铸 executor/owner/copilot;connection.json 0600;token 仅 fingerprint)。
- **R2 tui 首屏**:`lybra tui --llm-base-url … --llm-model … --llm-key-env LYBRA_PLANCHAT_LLM_KEY --project … --workspace-root …` → 进 AIPOS-208 chat-to-task 首屏 + AIPOS-210 banner。
- **R3 真 LLM 起草**:一句话 → copilot 产 conformant 卡。**人工质量评估(§5c 锚点)**。
- **R4 ★ Owner 带外发布**:Owner proceed → 落 drafts/ → `draft_publish_dry_run` → **Owner 用 owner token confirm(OWNER_CONFIRMED)**→ 读盘断言 confirmer_role=owner。**[Owner 闸 #1]**
- **R5 executor 接力**:executor claim dry_run → **Owner 带外 claim confirm**→ claimed。**[Owner 闸 #2]**
- **R6 跑通至 L3 VALID**(承 191B/202;可走最小):return(+**Owner 闸 #3**)→ 196a 摄取 → L3 VALID。
- 每道闸**显式标 [Owner OOB]**;**cc/脚本绝不持 owner token、绝不跑 confirm**(Owner 用既有 `~/copilot_publish.sh`/`~/copilot_claim_confirm.sh` 形态助手,运行时读 token)。

---

## §3 明确不做

改 gate/copilot/任何逻辑(纯验收物);**自动 confirm / 脚本持 owner token**;web(206b);R2/R5;(7) README;(6) 披露;在自动门里调外部 LLM(质量留 runbook 人工)。

---

## §4 必保

- **cc/脚本不碰 owner token**:runbook 所有 confirm 全 Owner 带外;自动门无 confirm。
- **证据现场零接触**(191b/191b-rerun/formb/copilot workspace);runbook 用一次性临时 workspace。
- **依赖隔离**:聚合器零 textual / 零第三方;仅 app.py import textual;gate 核心零依赖可跑。
- **所有不变量**(206 ★A1/零写/RF-5/L0–L3、207 scope、208 conformance、210 presentation)——自动门逐项断言。
- secrets 仅 fingerprint;connection.json 0600;LLM key 仅 env。

---

## §5 待决项(Owner 拍)

- **(a) 自动门承载**:① 独立 `tools/acceptance/v1_acceptance.py` 聚合器(建议;一条命令、清晰验收清单、不臃肿 tools/ 测)② 仅扩 tools/ 测。**建议 ①**(独立验收物,复现性好;聚合既有测不重造)。
- **(b) runbook 形态**:① 文档化通用流程 + 一次性临时 workspace(建议;可弃、零接触证据现场)② 文档化固定流程。**建议 ①**。
- **(c) 真 LLM 质量"通过判据"(主观→checklist 锚点)**:建议锚点——(i) 卡字段语义合理(task_mode/priority/output_target 切合意图);(ii) title 切题、body 可执行;(iii) **不臆造 context_bundle**(取现有或显式 needs_bundle 交 Owner);(iv) 产卡过 `draft_publish_dry_run`(结构已自动保证,质量看语义);(v) 无幻觉字段/无 secrets 入卡。Owner 逐条主观判 pass。

---

## §6 测试(验收物自身)

- **T1 聚合器 smoke**:`v1_acceptance.py` 可跑、汇总既有测结果、产 `ACCEPTANCE: PASS/FAIL` + 逐项;无 owner token、无 confirm、无外部网络(断言)。
- **T2 聚合器零 textual/零第三方**:import 检查;core lane 可跑。
- **T3 runbook 文档存在 + 每道 Owner 闸显式**:`v1_acceptance_runbook.md` 含 R0–R6、三道 [Owner OOB] 闸、"脚本不持 owner token"红线(存在性 + 内容点检)。
- **T4 全量回归**:`tools/` 全绿(含聚合器自身测);依赖隔离保持。

---

## §7 cc glm 审计点

1. **"不可全自动 confirm"被正确处理**:自动门**零 confirm、零 owner token**;confirm 全在 runbook 标 [Owner OOB]。
2. **自动门承 207/208 真凭据**:scope reachability + 卡 conformance 用**真 serve-rotate 凭据**(非手搓 registry);copilot ★A1 真凭据 SCOPE_DENIED。
3. **runbook 每道 Owner 闸显式** + cc 不碰 owner token 红线在档。
4. **纯验收物**:无 gate/copilot/逻辑改动;聚合器零 textual/零第三方;gate 核心零依赖可跑。
5. **证据现场零接触**;一次性临时 workspace;secrets 仅 fingerprint。
6. 全量 tools/ 绿。

---

## §8 与后续片分界

- 本片(AIPOS-211)= 两层验收(自动回归门聚合器 + 手动 release runbook)。
- 不含:(6) RF-3/§9 披露、(7) README/npm、AIPOS-206b、R2、R5、CI 接线(可后续把聚合器挂 CI,本片只产可复现命令)。

---

> **DRAFT 结束。** 待你复核(尤其"不可全自动 confirm"两层解、自动门承 207/208 真凭据非手搓、runbook 每道 Owner 闸显式)+ 拍 §5 a/b/c → Owner 批 → 实现 + §6 测 → cc glm 审计(§7)→ 你抽查 → finalize。不实现至获批。
