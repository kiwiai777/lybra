# AIPOS-244 — TUI Owner confirm 面 (F-243-1 (b), v1.0 gate)

- **Status**: DRAFT
- **Authority**: NONE (DRAFT 无权威,等 Owner 批准)
- **Parent**: F-243-1 (Owner has no convenient confirm surface)
- **Owner ruling**: curl 路径完全不可用,本片进 v1.0; W 的 Step 4 等它

## §0 Context

### What & Why

**本质**: TUI 的**第一个真相写入口**。TUI 本就以 owner token 连接(`--role owner`),本片**接线既有 confirm 机器** + **加 NL 壳** + **文档化**。

**既有机器**(N5 实证):
- `confirm_client.py`: `ConfirmClient.confirm(preview)`,含 `replay_args` 机制(保证 Owner 自发 dry-run 与其 confirm replay 逐字节一致)
- 隐藏在 `__main__.py`,未产品化到 TUI

**本片 = 三件事**:
1. **TUI 接线**: `/confirm` 命令调既有 `session.confirm_gate(preview)`
2. **NL 壳**: 自然语言问句(任务号+给谁+操作) + 是/yes//confirm 三肯定
3. **文档化**: TUI 不再只读,disclosure 更新

**Why now**: 
- F-243-1 (Agent Quickstart 调研)发现:Owner 无顺手的 confirm 面(TUI `/gates` 只读,只能 curl/自配 MCP)。
- Owner ruling: curl 路径**完全不可用**(非 friction,是 blocker),本片进 v1.0。
- W (Agent Quickstart) 的 Step 4 等本片 finalize 后改写为 `/gates` + `/confirm`。

**Gate 侧零改动**: scope/dry_run_token/revalidation 全部已在 gate 强制(AIPOS-197, AIPOS-170)。本片**不新建 dry-run/confirm 逻辑**,纯接线。

**写面标准**: 虽是客户端,但因是 TUI **首个写入口**,DRAFT 按**写面标准审**(完整性 / 错误响亮 / 权限边界自证)。

---

## §1 Requirements

### R1 — 既有机制接线(不新建 dry-run/confirm 逻辑)

**调用既有 API**:
- `session.preview_gate(gates[n])` → 取 preview(既有)
- `session.confirm_gate(preview)` → 调 `ConfirmClient.confirm(preview)`(既有,含 `replay_args` 机制)

**不新建逻辑**: TUI 不组装 dry-run/confirm 参数,不直接调 MCP 工具,**只接线既有 confirm 机器**。

**"逐字节一致"的正确断言对象**: Owner 自发 dry-run 与其 confirm replay 之间(confirm_client 的 `replay_args` 机制已保证) — **Pilot (ii) 对准这个改写**。

---

### R2 — Actor 从哪来(新设计点,必须处理)

**问题**: `claim_args_from_task` 从任务 metadata(`assigned_to`)取 actor/agent_instance;copilot 造的任务可能**无 assigned_to**。

**要求**:
- NL 对话里要显示**将归因给谁**(如"归因给 agent-01")
- **缺 assigned_to 时 → 问 Owner 要**(如"归因给哪个 agent? 输入 actor 名")
- **不许静默用空值或默认值**

**Pilot 加**: 无 assigned_to 任务 → 必须先问 actor,Owner 输入后才能发射。

---

### R3 — v1.0 真流程(agent 不调 claim dry-run)

**v1.0 真流程**:
```
agent 读队列/task_preview → 聊天里说要认领哪个 → Owner TUI /confirm(claim) → agent 干活 → 说完成 → Owner /confirm(return)
```

**关键**: **agent 不需要自己调 claim dry-run**(Owner 在 TUI 内发起 claim)。

**连锁改 W**: W Step 3 (agent canonical prompt) 相应改写 — agent 的 MCP 面主要是**读(lybra_queue_list / lybra_task_preview)+ 将来 return 语义**,不调 claim dry-run。

---

### R4 — 显式确认,永不默认-yes

**交互序列**:
1. `/confirm <n>`(或 `/gates` 后直接 `/confirm`,单个 gate 时默认指它)
2. 展示 preview:
   - operation (claim / return / publish)
   - task_id
   - **归因给谁**(actor/agent_instance,从 assigned_to 或问 Owner 要)
   - review_checklist(如有)
3. **用自然语言问一句**(说人话):
   - claim: "确认把 AIPOS-999 批给 agent-01 (claim) 吗?"
   - return: "确认接受 agent-01 的 AIPOS-999 return 吗?"
   - publish: "确认发布草稿 draft-xyz.md 到队列吗?"
4. **接受的肯定输入**(三者等价):
   - `是`
   - `yes`
   - 再次输入 `/confirm`
5. **其余一切输入 = 不确认**(永不默认-yes):
   - 空回车
   - Esc
   - 任何别的话
   - → 取消并说明,不发射

**沿 TUI 既有纪律**:proceed / `/project switch` 都要求显式输入,无默认-yes。

---

### R5 — 诚实呈现(Slice D 纪律)

### R5 — 诚实呈现(Slice D 纪律)

**Gate 返回原样 surface**:
- **成功**:展示写了什么(`planned_writes` 落实 / 任务状态变更 / confirmer 记录)
- **失败**:响亮展示 `error_code` + `message`(如 `SCOPE_DENIED` / `STALE_DRY_RUN` / `OWNER_CONFIRMATION_REQUIRED`),**绝不吞**

**无成功装假象**:与 Slice D (AIPOS-242 ROUND 2) 同纪律,gate 返回 `{ok: False, error_code, message}` → 前置检查,不走成功渲染。

---

### R6 — 权限边界自证

**非 owner 角色的 session**(如 copilot token,`scopes: []`)调 `/confirm`:
- Gate 返回 `SCOPE_DENIED`(缺 `owner_confirm` / `queue_claim` / `draft_publish`)
- TUI 响亮展示 `SCOPE_DENIED` + message
- **顺手就是 ★A1 族的活证**(copilot 结构上调不动 confirm)

**测试路径**:Pilot 注入 copilot session 调 `/confirm` → 输出含 `SCOPE_DENIED`,无成功文案。

---

### R7 — 披露更新

**disclosure 相应行改写**:
- 旧: "TUI 是只读+草稿面(no write/confirm/publish scope)"
- 新: "TUI 持有 owner-token 的**显式确认面**;每次确认经完整 preview + checklist + 显式肯定(是/yes//confirm);无批量确认、无自动确认、无默认-yes"
- claims ⊆ disclosure(与 gate scope/revalidation 强制对齐)

---

## §2 Design

### 2.1 既有机制(confirm_client, N5 实证)

**文件**: `tools/aipos_cli/confirm_client.py`

**核心 API**:
- `ConfirmClient.confirm(preview)`: 执行 confirm,含 `replay_args` 机制(保证 Owner 自发 dry-run 与其 confirm replay 逐字节一致)
- `claim_args_from_task(task_path)`: 从任务 metadata(`assigned_to`)提取 actor/agent_instance

**现状**: 隐藏在 `__main__.py`,未产品化到 TUI。

**本片**: TUI 接线(通过 `GateSession` 封装)。

---

### 2.2 `/confirm` 命令

**语法**:
```
/confirm [<n>]
```
- `<n>`: gate 索引(0-based,对应 `/gates` 列表第 n 个)
- 缺省 `<n>`:单个 gate 时默认指它;多个 gate → 提示指定索引

**交互流程**:
1. 读 `session.preview_gate(gates[n])` → 取 preview
2. **提取 actor/agent_instance**(R-2):
   - 从 preview 的 task metadata(`assigned_to`) 读
   - **缺失 → 问 Owner 要**:"归因给哪个 agent? 输入 actor 名"
   - Owner 输入后,填充到 confirm args
3. 展示 preview(operation / task_id / actor)
4. 自然语言问句(任务号+给谁+什么操作)
5. 等用户输入:
   - `是` / `yes` / `/confirm` → 发射
   - 空回车 / Esc / 其他 → 取消
6. 发射:`session.confirm_gate(preview, actor=actor)` → 调既有 `ConfirmClient.confirm()`
7. Gate 返回:
   - `{ok: True, ...}` → 展示成功(planned_writes / 状态变更)
   - `{ok: False, error_code, message}` → 响亮展示错误,无成功文案

---

### 2.3 Session API(新增封装)

**新增方法**(`tools/lybra_tui/state.py` 或 `app.py`):
```python
def preview_gate(self, gate: dict) -> dict:
    """取 gate preview(既有逻辑封装)."""
    return gate.get("confirmation_preview", {})

def confirm_gate(self, preview: dict, actor: str | None = None) -> dict:
    """调既有 ConfirmClient.confirm(),含 replay_args 机制."""
    # 调 confirm_client.ConfirmClient.confirm(preview, actor=actor)
    # 返回 gate response
    pass
```

---

## §3 Implementation

### 3.1 Files

**产品**(1 文件):
- `tools/lybra_tui/app.py`: `/confirm` 命令 + `_cmd_confirm()` + actor 提取/问询 + NL 问句 + 肯定词检查

**测试**(1 文件,Pilot 五路):
- `tools/lybra_tui/tests/test_tui_app.py`: `TuiOwnerConfirmTests`
  - (i) 无肯定词不发射(空回车 / 别的话 → 不调 gate)
  - (ii) **是/yes//confirm 逐字节一致**(三种肯定各测 → confirm replay_args 与 Owner 自发 dry-run 逐字节一致,confirm_client 机制保证)
  - (iii) gate denied → 响亮(error_code 可见,无成功文案)
  - (iv) 非 owner session → SCOPE_DENIED 可见
  - **(v) 无 assigned_to 任务** → 必须先问 actor,Owner 输入后才发射

**披露**(1 文件):
- `docs/v1_disclosure.md`: 更新 TUI 行(持有 owner-token 显式确认面,confirm 机器既有)

---

### 3.2 Pseudo-code (`app.py`,核心逻辑)

```python
def _cmd_confirm(self, index: int | None = None) -> None:
    """Execute a pending confirm gate (Owner-only, explicit confirmation)."""
    gates = self._session.confirm_gates()
    if not gates:
        self._pre("No pending confirm gates.")
        return
    
    # 1. 确定 gate 索引
    if index is None:
        if len(gates) == 1:
            index = 0
        else:
            self._pre(f"{len(gates)} pending gates. Use /confirm <n> to specify.")
            return
    
    gate = gates[index]
    preview = self._session.preview_gate(gate)
    
    # 2. 提取 actor/agent_instance(R-2 新设计点)
    operation = preview.get("operation", "unknown")
    task_id = preview.get("task", {}).get("task_id", "unknown")
    actor = preview.get("actor", {}).get("actor") or preview.get("task", ).get("assigned_to")
    
    if not actor:
        # 缺失 assigned_to → 问 Owner 要
        self._pre(f"任务 {task_id} 无 assigned_to。归因给哪个 agent? 输入 actor 名:")
        # 伪代码:等用户输入 actor
        # actor = self._wait_for_input()
        # if not actor:
        #     self._pre("已取消(未输入 actor).")
        #     return
        pass
    
    # 3. 展示 preview + 自然语言问句
    self._pre(f"Preview: {operation} {task_id}")
    self._pre(f"归因给: {actor}")
    
    if operation == "queue_claim":
        question = f"确认把 {task_id} 批给 {actor} (claim) 吗?"
    elif operation == "queue_return":
        question = f"确认接受 {actor} 的 {task_id} return 吗?"
    else:
        question = f"确认执行 {operation} {task_id} 吗?"
    
    self._pre(question)
    self._pre("输入 是 / yes / /confirm 确认; Esc 或回车取消.")
    
    # 4. 等用户输入(伪代码)
    # user_input = self._wait_for_input()
    # if user_input.strip().lower() not in ["是", "yes", "/confirm"]:
    #     self._pre("已取消.")
    #     return
    
    # 5. 发射(调既有 confirm_client 机器)
    result = self._session.confirm_gate(preview, actor=actor)
    
    # 6. 诚实呈现(前置错误面检查,Slice D 纪律)
    err = _observe_error_face(result)
    if err:
        self._pre(f"Confirm failed: {err}")
        return
    
    # 成功:展示写了什么
    self._pre(f"Confirmed: {operation} {task_id}")
```

---

## §4 Red Lines

1. **Gate/scope/★A1/双根/zero-dep 零改动**:gate 侧不动,TUI 接线既有 confirm_client。
2. **Confirm 记录语义不变**:confirmer 照旧由 gate 记(role/token_ref/fingerprint)。
3. **无批量确认、无自动确认、无默认-yes**:每次 `/confirm` 针对单个 gate,显式肯定词必须。
4. **Copilot 模式不暴露 `/confirm` 的可用假象**:copilot session 调 `/confirm` → gate SCOPE_DENIED 响亮,不伪装可用。
5. **不新建 dry-run/confirm 逻辑**:TUI 只接线既有机器,不组装参数。

---

## §5 Verify

### Pilot 五路(正向真相)

| 路 | 验证点 | 预期 |
|---|---|---|
| **(i)** | 无肯定词不发射 | 空回车 / 别的话 → 取消,**不调 confirm_client**(mock 未被调) |
| **(ii)** | 是/yes//confirm 逐字节一致 | 三种肯定各测 → confirm replay_args 与 Owner 自发 dry-run 逐字节一致(**confirm_client 机制保证**,非 TUI 组装) |
| **(iii)** | 错误响亮 | 注入 denied(`{ok: False, error_code: "SCOPE_DENIED", message: "..."}`) → 输出含 `SCOPE_DENIED`,**无成功文案** |
| **(iv)** | 非 owner session DENIED | copilot token session 调 `/confirm` → gate 返回 `SCOPE_DENIED`,TUI 响亮展示 |
| **(v)** | 无 assigned_to 任务 | 必须先问 actor,Owner 输入后才发射(缺 actor → 不发射) |

---

### O3 实机(Owner 亲跑,整环 + W 预演)

**Walkthrough**(v1.0 真流程,R-3):
1. Agent(cc 扮)读队列(`lybra_queue_list`)→ 聊天里说"我要认领 AIPOS-999"
2. Owner TUI `/confirm`(claim)→ 展示 preview + NL 问句
3. Owner 输入 `是` → 任务 ACTIVE
4. Agent 读任务卡(`lybra_task_preview`)→ 执行 → 写产出
5. Agent 聊天里说"AIPOS-999 完成了"
6. Owner TUI `/confirm`(return)→ 输入 `yes` → 任务 RETURNED
7. **Owner 全程不碰 curl / 不出 TUI** ✓

**关键**(R-3): **agent 不需要自己调 claim dry-run**(Owner 在 TUI 内发起 claim)。

**验收**:整环跑通,Owner 无需 curl。

---

## §6 Disclosure Update

**文件**: `docs/v1_disclosure.md`

**旧行**(约 row 7 或类似):
> TUI 是只读+草稿面(no write/confirm/publish scope)

**新行**:
> TUI 持有 owner-token 的**显式确认面**(`/confirm` 命令);每次确认经完整 preview + 显式肯定(是/yes//confirm 三选一);无批量确认、无自动确认、无默认-yes。Confirm 机器既有(confirm_client, N5 实证),本片 = 接线 + NL 壳 + 文档化。Confirm 记录语义由 gate 强制(confirmer role/token_ref/fingerprint);scope/dry_run_token/revalidation 全部 gate 侧结构强制(AIPOS-197, AIPOS-170)。

**Claims ⊆ disclosure**:与 gate 强制对齐。

---

## §7 Rollout & Dependencies

### Rollout

1. **DRAFT → R 审** → Owner 批
2. **实现**(产品 1 + 测试 1 + 披露 1)
3. **cc glm**(Pilot 五路)
4. **R 复核**
5. **Owner O3**(实机整环,W 预演)
6. **Finalize**

### W (Agent Quickstart) 依赖

**W Step 3 改写**(R-3 连锁):
- 旧: agent 调 `lybra_queue_claim_dry_run` → Owner confirm → agent claim_confirm
- 新: **agent 读队列 + 说要认领 → Owner TUI `/confirm`(claim)** → agent 干活 → 说完成 → Owner TUI `/confirm`(return)
- agent 的 MCP 面主要是**读**(lybra_queue_list / lybra_task_preview)+ **将来 return 语义**,不调 claim dry-run

**W Step 4 改写**(本片 finalize 后):
- 从 "curl + owner token MCP" → "TUI `/gates` + `/confirm`"
- M11 一并折入(任何 W DRAFT 审过程中的其他修正)
- Owner docs-only walkthrough 才能全程在 TUI 内完成(不碰 curl,真正"用户能跑通")

---

**Status**: DRAFT(折入 R-mech/R-2/R-3),回 R 一眼即交 Owner 批。
