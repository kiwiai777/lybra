# AIPOS-204 — Gated draft_publish + confirmer attribution (F-c4 fix) micro-plan (DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-22
- task-id: AIPOS-204 (proposed;待 board 分配)
- epic: v1.0 Scope B — backlog item (3) F-c4 (DL-20260622-01 / DG-9);DG-8 的隐含前提
- discipline: 复用 AIPOS-197(owner_confirm scope)+ AIPOS-199(confirmer 留痕),**不改其语义**;不碰 claim/return/196a/L3/Wall;不做 TUI 骨架 / AI 起草(各后续片)。不改码至获批。

---

## §1 背景 + 缺口(已锚代码)

- **F-c4**(首个 191B N4 暴露、在册;本轮 Form-B dogfood 又印证):`draft_publish` 路径**未经 OWNER_CONFIRMED 门控**,发布记录 `published_by: "unknown"`(`tools/aipos_cli/draft_writer.py:143 render_publish_record`)—— 「谁批准了发布」的 provenance 黑洞,与问责论点冲突。
- **现状两点**(只读确认):
  1. **MCP gate 无任何 publish 面**(`tools/mcp_server/tools.py` 无 `lybra_draft_publish*`)——发布只能走 CLI。
  2. CLI `board_adapter.publish_draft` 走 controlled-execute dry-run;真正写盘 = `draft_writer.publish_draft` + `render_publish_record`(写 `publish_record`,但 `published_by` 缺 confirmer 身份、无 owner_confirm 门)。
- **DG-8 隐含前提**:AI 起草产物经 gate 发布 → 发布面必须可问责(confirmer 留痕),否则 AI 起草 + 无问责发布 = 放大黑洞。
- **L3 关联**:authority scanner 的 `PUBLISH_PROVENANCE_PRESENT`(pending 任务 VALID)依赖匹配的 publish 记录;本轮 dogfood 的旁路 pending 任务被判 `QUARANTINED`(无 publish provenance)。门控发布写规范 publish 记录 → 合法 pending 任务 VALID,黑洞与该 L3 缺口一并收口。

---

## §2 目标

给 `draft_publish` 加 **Owner 门控 + confirmer 留痕**,与已证的 **claim/return** 同构地可问责:发布 = `dry-run → OWNER_CONFIRMED → confirm`,confirm 要求 `owner_confirm` scope(executor 结构性不能自发布),发布记录写 confirmer 身份。

---

## §3 设计(镜像 197/199,加法)

### MCP 门控 publish 面(新增)
- `lybra_draft_publish_dry_run`:校验 draft + 预览 pending 写入 + publish 记录;**零写**;返回 `dry_run_token` + snapshot_hash(复用 controlled-execute dry-run 机制)。**可见性 scope** = 发布/起草类(scope 词表已有 `draft_publish` operation),非 owner-only —— 起草方(planner/AI 起草经手)可预览。
- `lybra_draft_publish_confirm`:**要求 `owner_confirm` scope(AIPOS-197 门,executor 结构性 SCOPE_DENIED)** + `OWNER_CONFIRMED` literal + **replay** dry-run 的身份参数(actor/owner_policy_ref 等,镜像 RF-4 解);写 pending 任务 + publish 记录。

### 发布记录写 confirmer(解 `published_by: unknown`)
- `render_publish_record` 增 confirmer 字段:`confirmer_role` / `confirmer_token_ref` / `confirmer_token_fingerprint` + **§9 占位**(`gate_signature` / `authority_seal` / `signature_key_ref` / `signed_payload_hash` / `signed_at`)——**完全镜像 claim/return 记录**(AIPOS-199 模式)。`published_by` 保留(兼容)但不再是唯一身份来源;confirmer_* 为权威发布者身份。
- confirmer 来自 **server-side capability**(`request_capability_scope` 注入的 role/token_ref/fingerprint),与 199 同源;raw token 绝不入记录。

### 复用点(不改语义)
- AIPOS-197 scope 门(`owner_confirm` 结构性拒绝 executor)——原样复用,confirm 工具挂 owner_confirm。
- AIPOS-199 confirmer 留痕机制(capability → 记录 confirmer_* + §9 占位)——原样复用,扩到 publish 记录。
- controlled-execute dry-run/confirm 地板、snapshot revalidation、TTL——原样复用。
- `draft_writer.publish_draft` 写盘逻辑——扩 confirmer 入参,不改写盘语义。

---

## §4 CLI 旁路定界(DG-9)

- 现有 **ungated CLI draft publish 保留**,按 DG-9 标 **disclosed-deferred**(打包对外文档明示「CLI publish 不带 confirmer 门控,供本地受信操作;问责发布走 gated 面」)。
- 本片**聚焦 gated(MCP/TUI)publish surface** = TUI / AI 起草(DG-8)的发布通道。
- **建议(待 Owner 拍板)**:CLI 保留但 disclosed-deferred,**不**在本片给 CLI 加门控(避免双路径维护 + 范围膨胀);gated MCP 面作为问责发布主通道。若 Owner 要求 CLI 同时门控 → 单列后续片。

---

## §5 范围红线

- 复用 197/199,**不改其语义**;不改 claim/return/196a/L3/Wall/service_mode。
- 不做 TUI 骨架(AIPOS-203 client 收编进面板 = 后续片);不做 AI 起草(DG-8 主体 = 后续片)。
- 不实现 §9 真签名(占位已镜像;per-op nonce/签名仍 disclosed-deferred)。
- raw token/key 仅 fingerprint;gate 非公网姿态不变。

---

## §6 端到端测试(吸取 RF-5 教训:经真 confirm 路径读盘,非仅单测直调)

- **T1 发布侧 ★A1**:executor-scope token 调 `lybra_draft_publish_confirm` → **SCOPE_DENIED**(结构性不能自发布);且无 pending 写入 / 无 publish 记录。
- **T2 owner 发布留痕**:owner-scope token `draft_publish_dry_run → confirm` → **读盘** publish 记录断言 `confirmer_role=owner` + `confirmer_token_ref` + `confirmer_token_fingerprint` + §9 占位存在;pending 任务落盘。
- **T3 published_by 不再 unknown**:同 T2,publish 记录 confirmer_* 非空(对照旧 `published_by: unknown`)。
- **T4 L3 联动**:T2 发布后的 pending 任务经 authority scanner = `VALID`(PUBLISH_PROVENANCE_PRESENT),对照无 publish 记录的旁路任务 = QUARANTINED。
- **T5 dry-run 零写 + snapshot revalidate**:dry-run 不写盘;confirm 用过期/不匹配 token → 结构化错误。
- **T6 回归**:claim/return confirm(★A1 + 199)不变;全量 `tools/` 绿(基线 409 + 本片新增)。

---

## §7 cc glm 审计点

1. **发布门控真结构性**:executor 经 publish_confirm → SCOPE_DENIED(发布侧 ★A1),非仅文案;且零写。
2. **confirmer 端到端落盘**:经真 confirm 路径读盘 publish 记录 confirmer_role=owner + ref + fingerprint + §9 占位(RF-5 教训:不接受仅单测直调)。
3. **复用 197/199 无语义漂移**:scope 门 / confirmer 机制未被改坏;claim/return 回归绿。
4. **published_by 黑洞已解**:confirmer_* 为权威身份;raw token 不入记录。
5. **CLI 旁路 disclosure 写明**(DG-9);本片未擅自给 CLI 加门控(或按 Owner 拍板)。
6. **无其它 gate 面回归**:196a/L3/Wall/service_mode 未触;全量 `tools/` 绿。

---

## §8 诚实定界

- 本片 = **gated publish surface + confirmer 留痕**(F-c4 在 MCP/TUI 面的根治)。
- **CLI 旁路 disclosed-deferred**(DG-9),非本片关闭。
- TUI 骨架 / 把 AIPOS-203 confirm client 扩到 publish / AI 起草(DG-8 主体)= 各后续片。
- §9 真签名仍 deferred(占位镜像)。

---

> **DRAFT 结束。** 待你复核 + 拍板:(a) §4 CLI 旁路处置(建议 = 保留 + disclosed-deferred,不在本片门控 CLI);(b) publish_dry_run 可见性 scope(建议复用词表 `draft_publish` operation,confirm 挂 owner_confirm)。批准后:实现 + §6 测试 → cc glm 独立审计(§7)→ 你抽查 → 批准 → finalize。不实现至获批。
