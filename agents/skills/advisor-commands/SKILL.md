---
name: advisor-commands
description: 顾问动词手册——所有产品命令的参数/示例/何时用,标注已退役手搓片段
version: 1.0.0
role: advisor
---

# advisor-commands — 顾问动词手册

**角色**:advisor(顾问)专用。这是你的**命令快查表**,所有 lybra 产品命令的参数、示例、使用时机。

## 为什么(长期有效)

**问题源**:历史顾问靠手搓 `GateClient(...).call_tool(...)` 片段直呼 gate 动词(ADVISOR-COMMANDS.md 9处),
参数易漂移、缺参报错不友好、每次压缩后要翻文档重拼。产品命令(`lybra` CLI)参数由 schema 驱动,
缺参自报错含可抄示例,但命令多了顾问记不全何时用哪个。

**长期有效性**:产品命令集随 loop 演进持续增长;顾问作为第一交互面,需要稳定的命令索引;
本 skill = 命令快查表 + 退役债标注,随产品命令上线同步更新。

---

## 工作流阶段表 (AIPOS-F73C件④)

顾问在各阶段的职责与可用命令：

| 阶段 | 职责 | 主要命令 |
|------|------|----------|
| **N0 出卡** | 起草与发卡 | `lybra draft create/publish`, `lybra queue amend/withdraw` |
| **N1 认领** | 监督认领流程 | `lybra next --run` (产品执行), `lybra my-tasks` 查询 |
| **N2 执行** | 监督进度 | 无直接干预 (executor 写 RETURN.md) |
| **N3 交回** | 监督交回流程 | `lybra next --run` (产品执行) |
| **N4 审计** | 审非代码卡 | `lybra audit-verdict` (顾问自审), `lybra audit dispatch` (派审) |
| **返工** | 追加返工节 | `lybra queue rework --confirm` (AIPOS-F75, F73C件⑤) |
| **N5 finalize** | 监督交付上线 | `lybra next --run` (产品执行) |
| **N6 收账** | 编年史+决策记录 | `lybra generate-backlog-entry`, `lybra owner-decision` |

**关键原则**:
- **认领/交回/finalize 由产品执行** (`lybra next --run`)，顾问不手搓门动词。
- **返工节只能通过 `lybra queue rework` 追加**，禁手写卡面 rework_rounds 字段。
- **next-step 导航**：用 `lybra next-step --task-id <ID>` 查询当前状态与下一步动词。

---

## 命令分类索引

### 🎯 发卡与改卡(N0 出卡)

#### `lybra draft create`
**何时用**:新建任务草稿。
```bash
lybra draft create --task-id AIPOS-XXX --title "..." --project lybra
# 生成 5_tasks/drafts/aipos-xxx.md
```

#### `lybra draft publish`
**何时用**:发布草稿到 pending 队列(N0)。
```bash
lybra draft publish --task-id AIPOS-XXX --actor advisor.lybra.kiwiai-dev
```
**自动校验**:schema 校验(必填/拼错字段)、N0 容量 lint(交付大项>3 会 WARN)。

#### `lybra queue amend`
**何时用**:改卡(如 `needs_owner: true → false` 解放行阻塞)。
```bash
lybra queue amend --task-id AIPOS-XXX --field needs_owner --value false \
  --reason "PreAuthorized release" --actor advisor.lybra.kiwiai-dev
```

#### `lybra queue withdraw`
**何时用**:撤卡(malformed/方向错误/重复发卡)。
```bash
lybra queue withdraw --task-id AIPOS-XXX --reason "duplicate" \
  --actor advisor.lybra.kiwiai-dev
```

#### `lybra queue rework`
**何时用**:追加返工节到卡面(FAIL 裁决后,点杀清单+验收标准)(顾问职责).
```bash
lybra queue rework --task-id AIPOS-XXX --actor advisor.lybra.kiwiai-dev \
  --verdict-ref "task_cards/AIPOS-XXX/audit_report.md" \
  --focus-items '["fix item 1", "fix item 2"]' \
  --acceptance-criteria '["criterion 1", "criterion 2"]' \
  --connection-json ~/.lybra/connection.json --confirm
```
**约束**:
- 只能由 advisor 调用(需 queue_rework scope);
- 必须带 `--confirm` (两阶段门动词);
- rework_rounds 字段禁手写,只能通过本命令追加。

---

### 🚀 派工与监督

#### `lybra audit dispatch`
**何时用**:派审(手动指定审计者,或 FIX 打回后复审)。
```bash
lybra audit dispatch --task-id AIPOS-XXX \
  --auditor-instance auditor.lybra.kiwiai-dev \
  --actor advisor.lybra.kiwiai-dev
```
**生成**:dispatch 记录 + 带 `reviewed_task_id` 的 R 卡。

#### `lybra next-step`
**何时用**:查询任务当前状态 → 下一步动词+完整参数(AIPOS-R7A 大项C)。
```bash
lybra next-step --task-id AIPOS-XXX
```
**输出**:当前状态、下一步动词、完整命令、触发者、授权语义(由 transitions.schema 生成,禁口述)。

**对外陈述序列必须由它生成**(LOOP-REDESIGN §4.5 A13)——口述 = 记忆叙述 = 漏步漂移。

---

### ⚖️ 审非代码卡(N4 分路)

#### `lybra audit-verdict`
**何时用**:你审非代码卡(docs/governance/config)时提交裁决。
```bash
lybra audit-verdict --task-id AIPOS-XXX --verdict PASS \
  --actor advisor.lybra.kiwiai-dev --summary "..."
```
**禁止**:手写裁决文件(必经 gate MCP 落 `5_tasks/records/audit_verdicts/`,归因在案)。
**禁止**:审自己执行的卡(升级 Owner 或独立审计)。

---

### 🔐 信封与仲裁(Owner 决策)

#### `lybra envelope mint`
**何时用**:签发 PreAuthorized 信封(授权 executor 自认领额度)。
```bash
lybra envelope mint --policy-id pol_lybra_dev_9 \
  --agent-or-role exec.lybra.kiwiai-dev --max-tasks 60 \
  --expires-at 2026-09-01T00:00:00Z \
  --decision-summary "Q3 envelope" --actor owner
```

#### `lybra envelope revoke`
**何时用**:吊销信封(紧急情况/额度滥用)。
```bash
lybra envelope revoke --policy-id pol_lybra_dev_9 \
  --revocation-reason "Emergency stop" --actor owner
```

#### `lybra envelope renew`
**何时用**:续额/延期已有信封。
```bash
lybra envelope renew --policy-id pol_lybra_dev_9 \
  --add-tasks 30 --new-expiry 2026-10-01T00:00:00Z \
  --decision-summary "Q3 extension" --actor owner
```

#### `lybra owner-decision`
**何时用**:记录 Owner 仲裁/豁免/政策变更决策。
```bash
lybra owner-decision --decision-id arb-2026-08-16-01 \
  --decision-type arbitration --task-id AIPOS-XXX \
  --decision-summary "Approve despite FAIL: emergency hotfix" \
  --actor owner
```
**典型场景**:审计争议、FIX 打回超 2 轮升级、紧急豁免。

---

### 📦 角色供给(enroll-deliver)

#### `lybra roles enroll`
**何时用**:为本机/当前角色初始化工位(凭据+配置+工具包)。
```bash
lybra roles enroll --role executor --project lybra --machine kiwiai-dev
```
**生成**:`.lybra/` 配置(connection.json/role/policy)、工具包、skills。

#### `lybra roles enroll-code`
**何时用**:为跨机角色生成一次性注册码(未来:enroll-deliver 跨机形态)。
```bash
lybra roles enroll-code --role auditor --project lybra --max-uses 1
```

---

### 📊 收账与治理(N6)

#### `lybra generate-backlog-entry`
**何时用**:生成卡编年史条目(每卡必落 FOUNDATION-BACKLOG.md)。
```bash
cd /home/kiwi/projects/lybra
./tools/generate_backlog_entry.py --task-id AIPOS-XXX
```
**输出**:可追加到 FOUNDATION-BACKLOG.md 的 markdown 段。

**N6 收账固化清单**(LOOP-REDESIGN §2 N6):
1. FOUNDATION-BACKLOG.md 本卡条目(工具生成)
2. decision_log 指针(如有 Owner 裁定/仲裁/信封授权与吊销)
3. stage_archive 快照(阶段关账时)
4. 治理仓 push(push 是节点一部分,不 push = 没收口)

---

## 已退役片段(ADVISOR-COMMANDS.md 手搓债)

**状态**:以下手搓 `GateClient` 片段标注为**已退役**(LOOP-REDESIGN §4.5 A12),
只作底层参考,**实操必走产品命令**(上面列出的 `lybra` CLI)。

### 退役原因
1. **参数易漂移**:手拼 JSON 字典,卡头字段改名/枚举值变更时没编译期检查。
2. **缺参不友好**:报错只说 `missing key`,不说哪些参数必填、合法值域。
3. **压缩后重拼**:上下文压缩丢失手搓片段,顾问要翻文档重写。
4. **产品命令优势**:参数由 verbs.schema 驱动,缺参自报可抄示例,与 gate 同版本同 deploy。


| 原手搓操作 | 替代产品命令 | 退役日期 |
|-----------|------------|---------|
| 手搓 GateClient 片段 | `lybra draft publish/queue claim/return/amend` | AIPOS-F73C |
| 手写 owner_decisions/*.md | `lybra envelope mint/revoke/renew` | AIPOS-R7A |
| 手写 owner_decisions/*.md | `lybra owner-decision` | AIPOS-R7A |
| 手写 audit_verdicts/*.md | `lybra audit-verdict` | 已上线 |
| 手搓 dispatch 记录 | `lybra audit dispatch` | 已上线 |
| 口述"下一步做 X" | `lybra next-step` | AIPOS-R7A |

**过渡期豁免**:在所有产品命令上线前,ADVISOR-COMMANDS.md 的手搓片段暂保留作底层参考;
本卡(AIPOS-F73C)交付后,手搓片段全退役,只保留命令快查表(本 skill)。
**过渡期豁免**:在所有产品命令上线前,ADVISOR-COMMANDS.md 的手搓片段暂保留作底层参考;
本卡(AIPOS-R7A)交付后,手搓片段全退役,只保留命令快查表(本 skill)。

---

## 常见反模式(禁止)

| 反模式 | 为什么禁 | 正确做法 |
|--------|---------|---------|
| 手搓 `GateClient` 片段 | 过渡债,参数易漂移 | 用 `lybra` 产品命令 |
| 口述「下一步做 X」 | 记忆叙述 = 漏步 | `lybra next-step` 生成 |
| 手写裁决文件 | 绕过 gate 归因 | `lybra audit-verdict` |
| 贴稿写 `<task_id>` | 接收方冷启动不知道 | 写真实 ID `AIPOS-R7A` |
| 缺参数自己猜 | 猜错 = 撞墙 | 命令缺参自报,照抄 |

---

## 本 skill 维护

- **目标读者**:advisor(顾问)快查命令用。
- **同步触发**:产品命令新增/改参时,同步更新本 skill 对应章节。
- **退役标注**:历史手搓片段不删除,标注为"已退役"+替代命令+退役日期(审计追溯)。
- **分发**:由 tools/distribute_tools.py 按角色分发到顾问工位;executor/auditor 工位不装此 skill。

---

## 参考

- **LOOP-REDESIGN v2 §4.5**:顾问侧固化点表 A1..A13(动词包+next-step 导航)。
- **verbs.schema.json**:所有 gate 动词的参数定义(产品命令的单一源)。
- **transitions.schema.json**:状态机转移表(next-step 的单一源)。
- **ADVISOR-COMMANDS.md**:底层 gate 动词参考(过渡期保留,手搓片段已退役)。
