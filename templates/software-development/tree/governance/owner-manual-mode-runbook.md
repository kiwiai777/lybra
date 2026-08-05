# Owner Manual-Mode Runbook

Manual mode is a first-class fallback: when automation (the pump) misbehaves, you
(the Owner) drive the loop by hand. **派工降级,门一个不少** — dispatch degrades to
manual, but every gate still runs. This runbook is your view of that loop.

> 与自动模式的关系:同一张卡走自动(`lybra pump run`)和手动(你贴 `/claim`)产生的
> 记录形状一致(字段/落位/事件),审计与账本不区分来源。模式开关只约束**泵**:
> `manual` 关自动派工,`auto` 时手动贴卡依然有效。

## 切换开关(AIPOS-338 S5)

真相在记录,不在对话。切换走产品开关,自然语言只是触发方式。

```bash
# 看当前模式(只读)
lybra project dispatch-mode show --home-root <home>

# 切到手动(关自动派工;Owner-only;切换留痕)
lybra project dispatch-mode set --mode manual --reason "pump 连续派工失败,手动接管"

# 切回自动
lybra project dispatch-mode set --mode auto --reason "pump 恢复"
```

- **何时降级**:自动派工连续失败(同一卡多次 `pump run` 起不来/撞车)。切不切由你确认,
  顾问/产品只**提议**。
- **何时切回**:故障排除后(如 gate 恢复、连接修复),手动走通一张卡验证后切回。
- `manual` 态下 `lybra pump run` 被拒并明确提示当前模式(防泵与你同派一卡撞车);
  `auto` 态下你手动 `/claim` **照常有效**。即 manual 的语义是"关自动派工",不是"开手动权限"
  (手动权限恒在)。

## 手动模式完整回合

每张卡的契约(`## 【认领与交回】`节)随发布自动生成 —— 动词全名/信封/判据/BLOCK 落位
都在卡里,你拿到就能直接贴给 pi。

### 1. 贴执行卡

把执行卡的 `## 【认领与交回】`节(或整卡)贴给 executor pi,它走认领→执行→交回。
- 看什么信号:`5_tasks/records/claims/<ID>/claim_*.md`(认领落地)、
  `5_tasks/records/returns/<ID>/return_*.md`(交回落地)。
- 出问题找谁:认领没落地 → 卡内信息不足(找顾问补);agent 跑偏 → 看 RETURN,打 fix 卡。

### 2. 看结果

读 executor 的 RETURN(落 `task_cards/<ID>/RETURN.md` 与工作区 return 记录)。

### 3. 贴审计卡(代码分支)

代码任务交回后,gate 派生 `<ID>R` 审计卡,**自带审计准绳与取证要求**。把它贴给 auditor pi。
- 看什么信号:`5_tasks/records/audit_verdicts/<被审卡ID>/verdict_*.md`(裁决)。
- PASS → 进 finalize;FAIL → 打 fix 卡回 executor(有界修复循环,默认 2 轮)。
- 代码+部署卡:R 卡另附**部署门提醒** —— 审计 PASS ≠ 可部署,部署确认属你
  (`owner_verify: required` 的不可逆确认,判断在你;仅生产级部署触发,开发环回部署不触发)。

### 4. 贴 fix 卡(如 FAIL)

审计 FAIL → 顾问出 fix 卡(复用原卡验收断言 + F-* 清单)→ 你贴给 executor → 复审。

### 5. finalize 卡

审计 PASS(且 Owner 真人核验,若卡声明 `owner_verify: required`)→ 顾问出 finalize 卡 →
你贴给 executor 推产品仓。实现批准(审计)与发布批准(finalize)是两个门,永不合并。

## 三分支各自的 Owner 视角(S6)

分支由项目的 `collaboration_profile` × 任务字段决定(单源:flow_description)。

- **代码(无部署)**:认领 → 进度 → 交回 → 派审 → 审计裁决 → 结案。你在审计报告处等。
- **代码(有部署)**:同上 + 部署门提醒。审计 PASS 后,**部署确认**是你的不可逆确认门。
- **非代码**:交回后走**验证台 bench 审计**(ring2 证据清单 + ring3 你的眼验)——
  **没有审计报告可等**。你在验证台核证据(部署健康/配置 diff/内容产出/调研结论)并按。
  > ⚠️ bench 动词尚未实现时:非代码卡显式标注"暂走 Owner 眼验 + 记录";
  > bench 落地后零改动自动启用。

## 异常处理速查

| 现象 | 动作 |
|---|---|
| 认领没落地 | 卡内连接信息不足 → 找顾问补 `## 【认领与交回】`节 |
| pump run 被拒(提示 manual) | 当前 manual 态;手动贴卡,或确认后切回 auto |
| 审计死锁(2 轮 FAIL 仍不过) | 停,升级顾问仲裁(不空转) |
| 看不到当前模式 | `lybra project dispatch-mode show` 或看板只读呈现 |
