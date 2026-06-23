# AIPOS-206-dogfood — Planning Copilot live run plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不执行;本稿仅供复核
- date: 2026-06-23
- task-id: AIPOS-206-dogfood (governed live empirical; not a code change)
- epic: v1.0 Scope B — Planning Copilot (DG-11) 真 LLM 链路实证;实现 = AIPOS-206(commit `898d4d4`,AIPOS-206R PASS)
- discipline: **受治 live 实证,非码改。** 无产品码改动;遇阻登记 finding 分流,**不硬改 gate/copilot**;Supervised 每门 Owner 带外 confirm;copilot 结构性只读(scopes []);裸 HTTP fingerprint-only;无 daemon/web fetch(206b);手动 finalize。证据现场零接触。

---

## §0 ★ 第一步:LLM 请求格式验证(执行前必做,先于任何 DRAFT)

**要证的 delta**:AIPOS-206 单测用 `FakeLLM`;本片唯一新增风险面 = `copilot.py` 的**真 LLM 链路**。最大未知 = `copilot.py` 的裸 HTTP 契约 vs `xchai.xyz` 端点实际 API。

**copilot.py 当前契约(基准,来自 commit 898d4d4)**:
- 请求:`POST {base_url}/chat/completions`,headers `Authorization: Bearer <key>` + `Content-Type: application/json`,body `{"model": <model>, "messages": [{"role","content"}...]}`(= **OpenAI-compatible chat completions**)。
- 响应解析:`choices[0].message.content`。
- 端点(Owner 持有):`base_url = xchai.xyz`(具体 path 待 Owner 确认,如 `https://xchai.xyz/v1`)/ `model = sonnet-4.6` / key fp `sha256:5c75db387b52`。

**最小连通探针(一次调用,可不产 DRAFT)**:
- 用 copilot.py **本体** `LLMClient(LLMConfig(base_url, api_key=<env>, model)).complete([{"role":"user","content":"ping"}])`,key 经 env 注入(`LLM_KEY_ENV`),raw 绝不入命令行/日志。
- **判定树**:
  - **匹配**(返回非空 content 字符串)→ OpenAI-compat 成立,记入报告 §0,继续 N1。
  - **不匹配**(404/400/鉴权外的格式错 / 响应无 `choices`,如返回 Anthropic `content[].text` 形)→ **登记 finding `F-cop-fmt-1`**,记录端点实际期望格式(Anthropic `/v1/messages` 还是别的 OpenAI path),**定适配形态留作独立修复片**(如 copilot.py 增 provider 适配层 / base_url path 调整),**本片到此停在 §0,不硬改、不在 live run 里改产品码**。
  - **环境失败**(代理不稳、超时、5xx、key 失效)→ 按**环境项**记(Owner 已知 key/代理可能不稳),**非产品 finding**;可重试或择机重跑,不计入 ★A1/纪律判定。
- 探针结果(匹配/不匹配/环境)写报告**首节 §0**。N1+ 仅在 §0 = 匹配时进行。

---

## §1 Workspace 与角色(新场,证据现场零接触)

- **新建 `~/lybra-copilot-workspace`**(全新)。**绝不碰** `~/lybra-191b-workspace`、`~/lybra-191b-rerun-workspace`、`~/lybra-formb-workspace`。
- `lybra serve rotate` 铸三角色:**executor**(queue_claim/return)、**owner**(+owner_confirm/draft_publish)、**copilot**(`scopes: []`,AIPOS-206 新角色)。`serve` loopback(127.0.0.1)。
- `connection.json` 权限 **0600**;所有 token / LLM key **仅 fingerprint** 入报告,raw 不落盘日志/不入 argv。
- **token 持有分工(红线)**:
  - Owner 持 **owner token**,带外跑所有 OWNER_CONFIRMED 门(N0 种子发布、N4 发布);cc **不碰 owner token、不跑 confirm 脚本**。
  - cc 持 **executor**(N5 可选)+ **copilot**(只读,scopes [])token + **LLM key**(经 env,fingerprint-only)。LLM key = 纯 egress 凭据(不能改 Lybra 真相),与 owner token 风险面正交,故 cc 可持以驱动 copilot 起草。

---

## §2 闭环节点(每门 Owner 带外 confirm)

- **N0 种子**:新 workspace + serve rotate(3 角色)+ serve。**Owner 带外**经 gate 发布一张低风险 **docs 任务**作种(`draft_publish_dry_run` → Owner owner-token `draft_publish_confirm` OWNER_CONFIRMED)。证 gate 活、有真相可被 copilot 读。
- **N1 连接**:TUI copilot 模式(Shift+Tab observe→confirm→copilot),**copilot Bearer(scopes [])** 连接;状态栏显 `copilot · read-only · scopes []`。`scope_basis.role=copilot / scopes=[]` 即时核。
- **N2 ★A1(关键,真 LLM session 在场下复证)**:copilot 凭据调 `lybra_queue_claim_confirm` / `lybra_draft_publish_confirm` → **SCOPE_DENIED**;读盘断言**零写**(无新 claim/publish 记录)。这是单测 T1 在**真 LLM 链路**上的现场复证。
- **N3 起草(真 LLM)**:一句自然语言 → copilot 经**真 LLM** 起草任务卡。**即时核(磁盘/进程)**:
  - (a) **起草前经 read-tool 重读 L0**:`draft` 前有一次 `rehydrate_truth`(queue/task 经 read-tool),且**该 L0 真相确进了发往 LLM 的 prompt**(抓发送 payload 比对种子任务内容,RF-5 真发生而非纸面)。
  - (b) **DRAFT 作数据返回,copilot 零文件写**:draft 期间对 workspace 做 **fs diff = 空**(`DraftProposal` 在内存,未落任何文件)。
  - (c) **secrets fingerprint-only**:LLM key / token raw **不入** 日志 / prompt 持久化 / 报告;仅 fp。
  - **egress 披露兑现**:确认 workspace 内容(种子任务真相)**确发往 LLM** —— 这是**已披露行为**(micro-plan §2.2),记入证据(证披露=事实,非泄漏)。
- **N4 发布**:Owner 审 DRAFT → **同一 proceed 动作内** TUI 落 `5_tasks/drafts/` + `draft_publish_dry_run` → **Owner 带外 owner token confirm** OWNER_CONFIRMED → 任务落盘;**读盘断言 `confirmer_role=owner`**(零增 Owner 操作 = 落档+dry_run 在 proceed 里、仅 confirm 一步带外)。
- **N5(可选,可标 deferred)**:executor 认领 copilot 起草的任务跑通(196a 摄取 → L3 VALID),证 copilot-authored 任务全链流转。**非核心**;环境/时间受限可标 deferred 不影响本片判定。

---

## §3 证据清单(即时复查磁盘,fingerprint-only)

1. **§0 格式验证结果**(匹配 / 不匹配+F-cop-fmt-1 / 环境项)。
2. **N2 ★A1**:真 LLM 链路在场下 copilot 凭据 *_confirm → SCOPE_DENIED + 零写记录(读盘)。
3. **N3 真 LLM 起草**:起草前 read-tool 重读 L0 且 L0 进 prompt(payload 比对)+ draft 期 fs diff 空。
4. **egress 披露兑现**:workspace 内容确发往 LLM(= 已披露行为)。
5. **N4 confirmer=owner**:发布记录 metadata `confirmer_role=owner`(读盘)。
6. **secrets**:LLM key / token 全程仅 fingerprint(grep 报告/日志无 raw)。
7. **纪律自证**:copilot≠owner≠executor、Supervised 每门 confirm、单项目、无 web fetch(206b)、证据现场零接触、手动 finalize。

---

## §4 红线

Supervised 每门 Owner confirm;copilot 结构性只读(scopes []);**无产品码改动**(遇阻登记 finding 分流,§0 不匹配即停);裸 HTTP fingerprint-only;无 daemon / web fetch(206b 另片);cc 不碰 owner token / 不跑 confirm 脚本;证据现场(三 evidence workspace)零接触;手动 finalize(不自 finalize)。

---

## §5 cc glm 审计点

1. **真 LLM 链路 ★A1 成立**:copilot 凭据(scopes [])→ *_confirm/draft_publish → SCOPE_DENIED + 零写(在真 LLM session 在场下)。
2. **copilot 零文件写**:N3 draft 期 fs diff 空;DRAFT 作数据返回。
3. **起草前重读 L0 真发生(非纸面)**:rehydrate_truth 经 read-tool 在 draft 前;L0 真相进了 LLM prompt(payload 证据)。
4. **confirmer=owner**:N4 发布记录读盘 confirmer_role=owner。
5. **secrets 无泄漏**:全程仅 fingerprint(key/token raw 不在任何证据/日志/报告)。
6. **证据现场未碰**:三 evidence workspace 无任何读写。
7. **无产品码 diff**:本片仅产 **证据 + 报告**(+ 若 §0 不匹配则 F-cop-fmt-1 finding);`git diff` 产品码 = 空。

---

## §6 Finding 分流协议

- **F-cop-fmt-1(若 §0 不匹配)**:记录端点实际 API 形态 + copilot.py 适配缺口 → 独立修复片(如 provider 适配层 / base_url path),**不在本 live run 内改码**。
- **环境项**(代理/key/超时/5xx):记环境,非产品 finding,不计判定。
- **真产品 finding**(若 N2–N4 任一受治不变量破)：登记 + 停 + 回报,**不硬改**。

---

## §7 明确不做

web fetch(AIPOS-206b)、多项目 / 跨项目开窗(R2)、decision_log 迁移(R5)、AI 起草发布主体(DG-8)、LLM digest(R6)、任何产品码改动(§0 不匹配走 finding 而非 in-run patch)、自 finalize。

---

> **DRAFT 结束。** 待你复核(尤其 §0 格式验证 + N2/N3 即时核法)→ Owner 批 → 执行 → 即时复查证据 → cc glm 审计(§5)→ Owner 抽查 → 手动 finalize。**不执行至 run plan 获批。**
