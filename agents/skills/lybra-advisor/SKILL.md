---
name: lybra-advisor
description: AIPOS-F73件⑤ 顾问单一 skill — 阶段→产品命令表（取代手搓片段与认领交回写法）
version: 1.0.0
role: advisor
---

# lybra-advisor — 顾问推进命令表（AIPOS-F73件⑤）

**职责单一**：每个阶段该调哪条产品固化命令。

**原则**：
1. **禁手搓 gate 动词**：所有门操作经产品命令（`lybra` CLI）执行，禁 `GateClient.call_tool(...)`
2. **禁口述下一步**：推进由 `lybra next --run` 驱动，禁记忆叙述
3. **推进循环在顾问外部**：本命令不循环，循环由调用方负责

---

## 阶段 → 命令表

### 📝 出卡阶段

**新建草稿**:
```bash
lybra draft create --task-id <ID> --title "..." --project lybra \
  --assigned-to <角色> --task-mode <code|docs|governance|config> \
  --actor advisor.lybra.kiwiai-dev
```

**发布到 pending**:
```bash
lybra draft publish --task-id <ID> --actor advisor.lybra.kiwiai-dev
```

**改卡（如解除阻塞）**:
```bash
lybra queue amend --task-id <ID> --field <字段名> --value <新值> \
  --reason "..." --actor advisor.lybra.kiwiai-dev
```

---

### 🔄 推进阶段（N1-N6）

**单一入口**：
```bash
lybra next --run --task-id <ID>
```

**作用**：
- 推导当前状态 → 下一步动作
- 立即执行（认领/交回/派审/裁决/finalize/close）
- **单步即退，禁循环**（循环由顾问外部调用）

**推进类型**：
- pending → claim（执行体认领）
- claimed + RETURN.md → return（执行体交回）
- returned + 审计卡 → audit dispatch（派审）
- 审计卡 claimed + VERDICT → verdict（提交裁决）
- verdict PASS → finalize + close（结案）

**典型循环**（顾问外部）:
```bash
while true; do
  lybra next --run --task-id <ID> || break
  sleep 5
done
```

---

### 📊 治理收尾

**漂移收编**（结案后治理提交）:
```bash
lybra mark-concluded --task-id <ID> --actor advisor.lybra.kiwiai-dev
```

**治理 commit**（批量收账）:
```bash
lybra governance-commit --batch <批次标识> --actor advisor.lybra.kiwiai-dev
```

---

## 退役四连（AIPOS-F73件⑤）

以下写法**已退役**，被 `lybra next --run` 统一取代：

1. **COMMANDS.md 手搓 gate 动词片段**：
   - `GateClient.call_tool("lybra_queue_claim_dry_run", ...)` → 退役
   - `GateClient.call_tool("lybra_queue_return_dry_run", ...)` → 退役
   - 所有门动词手搓 → 走产品命令

2. **卡面「认领与交回」写法**：
   - executor/auditor 卡面不再渲染门契约节
   - 认领/交回由产品执行，模型不再直接调门

3. **顾问代按交回**：
   - 旧：顾问读 RETURN.md 后代执行体按 return
   - 新：`lybra next --run` 检测到 RETURN.md 自动执行 return

4. **口述下一步**：
   - 旧：顾问记忆叙述"下一步做 X"
   - 新：`lybra next --run` 从推导核派生命令并执行

---

## 何时需要人工介入

`lybra next --run` 返回 `action_type: manual` 时：
- 审计 FAIL 需返工
- blocked 事件需排查
- Owner verify 需人工确认

这些场景仍需顾问判断和协调。

---

## 参考

- **AIPOS-F73**：执行体零门 — 卡面去门链、next --run 机器扣扳机
- **transitions.schema.json**：状态机转移表（推导核单一源）
- **verbs.schema.json**：门动词参数定义（产品命令的单一源）
