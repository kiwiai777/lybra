---
name: lybra-advisor
description: AIPOS-F73件⑤ 顾问单一 skill：阶段→命令映射表。顾问只负责调用产品固化命令，不直接过门、不手工组装门参数。
---

# lybra-advisor — 顾问单一 skill (阶段→命令表)

**AIPOS-F73件⑤**: 执行体零门、审计体零门 → 顾问职责收为**调用产品固化命令**。
每个阶段该跑哪条命令，由本 skill 声明，**唯一真相源**。

## 原则

- **顾问不过门**：认领、交回、派审、裁决、finalize、close 全由产品做（lybra next --run 或其他固化命令）。
- **不组装参数**：顾问不读 connection.json、不拼 --actor/--agent-instance，参数由产品从记录/连接文件推导。
- **不手写循环**：`lybra next --run` 单步即退，连续推进由顾问外部循环调用（cron/控制面接管后零人工）。

## 阶段→命令映射表

### 1. 出卡阶段

```bash
# 创建草稿
lybra draft create --title "<标题>" --output-target "<路径>" --task-mode code --dry-run
lybra draft create --title "<标题>" --output-target "<路径>" --task-mode code --actor advisor.lybra.kiwiai-dev

# 发布到队列
lybra draft publish <草稿路径> --actor advisor.lybra.kiwiai-dev --dry-run
lybra draft publish <草稿路径> --actor advisor.lybra.kiwiai-dev
```

### 2. 推进阶段（核心：lybra next --run）

```bash
# 项目级扫描（查看所有活跃任务的下一步）
lybra next

# 单卡推导下一步（不执行）
lybra next --task-id <ID>

# 单卡推导并执行（机器扣扳机，单步即退）
lybra next --run --task-id <ID>

# 连续推进（顾问外部循环，产品不循环）
while lybra next --run --task-id <ID>; do
    echo "Step completed, checking next..."
done
```

**推导→执行覆盖**（lybra next --run 自动处理）：

- **pending → claim**：认领 + 建/复用 worktree (`card/<ID>`)
- **claimed + RETURN.md + 分支有提交 → return**：交回工作
- **returned + 审计卡存在 → dispatch**：派审
- **审计卡 claimed + VERDICT 报告 → verdict**：提交裁决（含 artifact_subject 绑定）
- **verdict PASS → finalize + close**：finalize --push --deploy → close

### 3. 漂移收编

```bash
# 标记已完成但未走完整流程的任务为 concluded
lybra mark-concluded --task-id <ID> --actor advisor.lybra.kiwiai-dev --report-path <报告路径> --dry-run
lybra mark-concluded --task-id <ID> --actor advisor.lybra.kiwiai-dev --report-path <报告路径>
```

### 4. 治理收尾

```bash
# 提交治理档（校验四件→commit→push）
lybra governance-commit --task-id <ID> --actor advisor.lybra.kiwiai-dev --dry-run
lybra governance-commit --task-id <ID> --actor advisor.lybra.kiwiai-dev

# 批量治理更新（无 task-id）
lybra governance-commit --actor advisor.lybra.kiwiai-dev --message "治理档批量更新"
```

## 退役四连（AIPOS-F73 前的手工门操作，已废弃）

以下片段**禁止使用**（门动词已从 executor/auditor token scope 删除）：

```bash
# ❌ 废弃：顾问手工认领（现在用 lybra next --run）
lybra_queue_claim_dry_run ...

# ❌ 废弃：顾问手工交回（现在用 lybra next --run）
lybra_queue_return_dry_run ...

# ❌ 废弃：顾问手工派审（现在用 lybra next --run）
lybra_audit_dispatch_dry_run ...

# ❌ 废弃：顾问手工裁决（现在用 lybra next --run）
lybra_audit_verdict_dry_run ...
```

**替代方案**：一律用 `lybra next --run --task-id <ID>`，产品推导出哪个阶段就执行哪个动作。

## 常见场景

### 场景1：新卡从出到收

```bash
# 1. 创建并发布
lybra draft create --title "AIPOS-XXX" --output-target "tools/" --task-mode code --actor advisor.lybra.kiwiai-dev
lybra draft publish <草稿路径> --actor advisor.lybra.kiwiai-dev

# 2. 启动执行体会话（人工/控制面）
# 执行体在会话中 `/claim` 读取已认领的卡并开工

# 3. 顾问监控推进（执行体完成后）
lybra next --run --task-id AIPOS-XXX  # claim (if pending)
lybra next --run --task-id AIPOS-XXX  # return (after RETURN.md)
lybra next --run --task-id AIPOS-XXX  # dispatch (派审)

# 4. 启动审计体会话
# 审计体在会话中 `/claim` 读取审计卡并开工

# 5. 顾问继续推进（审计体完成后）
lybra next --run --task-id AIPOS-XXXR  # verdict (after VERDICT)
lybra next --run --task-id AIPOS-XXX   # finalize + close (if PASS)

# 6. 治理收尾
lybra governance-commit --task-id AIPOS-XXX --actor advisor.lybra.kiwiai-dev
```

### 场景2：卡遇阻（BLOCK 报告）

```bash
# 推导会返回 not derivable + 缺失记录/建议
lybra next --task-id AIPOS-XXX
# 输出: Missing: RETURN.md 工作产物
# Suggested: 等待执行体完成工作并生成 RETURN.md

# 顾问介入：
# - 检查 task_cards/AIPOS-XXX/BLOCK-*.md
# - 决定 reopen / 修订卡 / 换执行体
```

### 场景3：批量推进（控制面接管后）

```bash
# cron / 控制面驱动器循环调用
for task in $(lybra next --json | jq -r '.[] | select(.derivable) | .task_id'); do
    lybra next --run --task-id "$task" || echo "Task $task failed"
done
```

## 红线

- **禁直接调用 lybra_* 门动词**：顾问 token 有这些 scope（draft_publish/audit_dispatch 等），但**只能通过产品固化命令调用**，不得手工组装参数。
- **禁读 connection.json 拼参数**：身份参数（actor/agent_instance/policy_ref）由产品从记录/连接文件推导，顾问不插手。
- **禁在产品命令外实现循环**：`lybra next --run` 单步即退是设计特性（防连接器病根），连续推进由顾问外部循环。

## 版本

- **v1.0** (AIPOS-F73): 初版，退役手工门操作，收敛为命令表。
