# AIPOS-212 — v1.0 disclosure ledger (RF-3 / §9 / disclosed-deferred catalog) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-24
- task-id: AIPOS-212 (proposed)
- epic: v1.0 Scope B — backlog (6) RF-3/§9 disclosure;**纯文档/诚实账本,无码改**
- discipline: 不改任何码/gate;每项必带"安全依据"(纪律或结构),不掩盖、不淡化;与既有 decision_log / finalize 报告一致(交叉引用 DL/AIPOS 编号)。不实现至获批。

---

## §1 目标

把 v1.0 **全部 disclosed-deferred / discipline-held** 项系统编目成一份**诚实披露文档**,每项写清:
**(1) 是什么 deferred · (2) 为何 v1.0 安全[靠什么结构或纪律守着] · (3) 计划何时做 · (4) 交叉引用[DL/AIPOS]**。
供 (7) README 的"范围与限制"段引用,确保**不过度宣称**。这是诚实账本,不是营销。

---

## §2 范围

落 `docs/v1_disclosure.md`(产品仓;README 可引用)。**结构化表/条目**,每项四列:`项 | Deferred 内容 | 为何 v1.0 安全(结构/纪律) | 计划 + 交叉引用`。

### §2.1 必覆盖条目(每项"为何安全"非淡化,附引用)

1. **RF-3 — orchestrator 可读 owner token**:**deferred** = orchestrator 与 owner token 的**结构性隔离**。v1.0 安全靠**纪律 + 部署面**:gate loopback、connection.json 0600、orchestrator 由 Owner 本地跑;且 ★A1(AIPOS-197)使确认权与执行权结构分离、confirmer 归属留痕(AIPOS-199)。**诚实标注**:这一条目前是**纪律守**而非结构守 → 结构隔离 deferred。计划:独立结构隔离片。
2. **§9 — per-op Owner nonce / gate 签名**:占位字段已写(`gate_signature`/`authority_seal`/`signature_key_ref`/`signed_payload_hash`/`signed_at`,AIPOS-204),**真密码学签名 deferred**。v1.0 安全靠:OWNER_CONFIRMED + snapshot 重校验 + confirmer 归属(role/token_ref/fingerprint,AIPOS-199);签名是**附加硬化**,非问责前提。引用 AIPOS-204/199。
3. **DG-9 — CLI draft publish 无 gate**:disclosed-deferred;**问责通道 = gated MCP/TUI publish**(AIPOS-204,confirmer=owner)。v1.0 安全:CLI publish 仅受信本地 operator 操作;可问责发布面是 gated 那条。引用 DL-20260622-05。
4. **AIPOS-207 §8 — intake_submit / owner_decision_record 仅路径 B 可达**:operator stdio + `LYBRA_CAPABILITY_TOKEN`,**有意豁免** service 角色(AIPOS-109/113 设计)。v1.0 安全:有意注入路径、已实证可达,且 standing reachability 测(AIPOS-207)守"可达或登记豁免"防复发。引用 DL-20260623-11。
5. **网络 egress**:v1.0 **不强制 egress 限制**;copilot 规划会把 **workspace 内容发往所配外部 LLM 提供方**(已披露固有行为)。v1.0 安全:copilot 结构性只读(无 truth-写),egress 是规划功能固有出口、Owner 配置即知情同意;与"真相写出口"正交。引用 DL-20260623-09/-12 + AIPOS-206 §2.2。
6. **自治档**:v1.0 **仅 Supervised**;Delegated/Standing **deferred**。v1.0 安全:每个 mutate 经 Owner confirm(★A1,AIPOS-197);更高自治档未开 = 无未问责执行面。引用 AIPOS-197 / DG-7。
7. **Form A(Wall)**:v1.0 **仅 Claude**(`confined_worker` 硬编码 `claude -p` / **F-candidate-2**);其它 harness 入 Wall = **v1.1/E2**。v1.0 安全:Wall 范围明确单一、不宣称异构 Wall。引用 DG-7。
8. **异构双 harness 互审 = v1.1**;**R2 多项目 / R5 decision_log 目录化 / LLM digest / AIPOS-206b web fetch** = **方向已批、deferred**。v1.0 安全:均未实现、未在范围内宣称;Form B 经 gate 接入已证(AIPOS-202)但互审 deferred。引用 DL-20260623-08(R2/R5/R6)/ DL-20260623-10(206b)/ DG-7。
9. **LLM key**:临时 egress 凭据(Owner 已知 follow-up:轮换)。v1.0 安全:仅 env 注入、fingerprint-only、不入 argv/connection.json/git;临时性已披露。引用 DL-20260623-10/-12。

> **定位口径(写入文档抬头)**:v1.0 = "可问责的单-agent 自治闭环 + 任意 MCP agent 经 Form B 接入的问责 gate"(DG-7),**非**"异构问责闭环"。披露文档守住此口径,防过度宣称。

---

## §3 明确不做

改任何码 / gate;(7) README 本体(本片只产被引用的披露文档);新机制;改既有 DL/finalize 报告(只交叉引用,不改写)。

---

## §4 必保

- **每项有"安全依据"(纪律或结构)**,且**诚实区分**"纪律守"vs"结构守"(如 RF-3 标明纪律守、结构 deferred)——不掩盖、不淡化。
- **与既有治理一致**:交叉引用真实 DL/AIPOS 编号;不与 decision_log/finalize 报告冲突。
- **覆盖完整**:§2.1 九类全覆盖;定位口径不过度宣称。
- 无 secrets;纯文档。

---

## §5 待决项(Owner 拍)

- **(a) 文档落点**:① `docs/v1_disclosure.md`(产品仓,README 可引;建议)② 治理仓。**建议 ①**(随产品分发、README 引得到)。
- **(b) 格式**:① 结构化表(项/Deferred/为何安全/计划+引用;建议)② 纯散文。**建议 ①**(可点检、可被 README 引)。
- **(c) 完整性守护**:① 加一条轻量测断言文档含九类条目关键字 + 引用真实 DL 编号(建议,防漏项/防陈旧)② 不加测。**建议 ①**。

---

## §6 测试(若 §5c=①)

- **T1 披露文档存在 + 九类覆盖**:`v1_disclosure.md` 含 RF-3 / §9 / DG-9 / intake·owner_decision 路径B / egress / 自治档(Supervised) / Form A(Claude/F-candidate-2) / 异构互审·R2·R5·206b / LLM key —— 关键字点检。
- **T2 交叉引用真实**:文档引的 DL 编号(DL-20260622-05 / DL-20260623-08/-09/-10/-11/-12)+ AIPOS 编号(197/199/204/206/207)存在且格式正确(存在性点检)。
- **T3 定位口径**:含"Form B"+ 不含"异构问责闭环"过度宣称表述(反向断言)。
- **T4 全量回归**:`tools/` 全绿(纯文档,不影响)。

---

## §7 cc glm 审计点

1. **覆盖完整**:§2.1 九类全在,无漏 disclosed-deferred 项。
2. **每项"为何 v1.0 安全"有据非淡化**:逐项有结构或纪律依据;**诚实区分纪律守 vs 结构守**(RF-3 不伪装成结构守)。
3. **与治理一致**:交叉引用 DL/AIPOS 真实且不冲突;不改写既有报告。
4. **定位口径**:守 DG-7 表述,无过度宣称("异构问责闭环"未出现为自我宣称)。
5. **纯文档**:无码/gate 改动;无 secrets;全量绿。

---

## §8 与后续片分界

- 本片(AIPOS-212)= v1.0 披露账本文档(被 (7) README 引用)。
- 不含:(7) README/npm 本体、AIPOS-206b、R2/R5、任何码改、CI。

---

> **DRAFT 结束。** 待你复核(尤其九类覆盖完整 + 每项"为何 v1.0 安全"有据非淡化 + 诚实区分纪律/结构守)+ 拍 §5 a/b/c → Owner 批 → 实现 + §6 测 → cc glm 审计(§7)→ 你抽查 → finalize。不实现至获批。
