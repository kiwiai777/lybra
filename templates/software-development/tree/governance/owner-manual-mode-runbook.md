# Owner Manual-Mode Runbook

Manual mode is a first-class fallback: when automated advancing misbehaves, you (the Owner)
step through the loop yourself — **with the same product commands**. **派工降级,门一个不少** —
dispatch degrades to manual, but every gate still runs. This runbook is your view of that loop.

> 与自动推进的关系:一张卡由驱动方 `lybra loop` 推进(认领→交回→派审→裁决→finalize→结案),
> 工位(执行/审计)只敲 `/go` 开工、只写产物。手动模式下仍走同一组产品命令, 只是由你逐步发起、
> 逐步核对;产生的记录形状一致(字段/落位/事件), 审计与账本不区分来源。
> 任何一步都不贴卡给工位、不让工位自己认领或交回, 也不手写直呼门接口的脚本。

## 切换开关(AIPOS-338 S5)

真相在记录,不在对话。切换走产品开关,自然语言只是触发方式。

```bash
# 看当前模式(只读)
lybra project dispatch-mode show --home-root <home>

# 切到手动(关自动派工;Owner-only;切换留痕)
lybra project dispatch-mode set --mode manual --reason "自动推进连续失败,手动接管"

# 切回自动
lybra project dispatch-mode set --mode auto --reason "故障已排除"
```

- **何时降级**:自动推进连续失败(同一卡多次推进不动/撞车)。切不切由你确认,顾问/产品只**提议**。
- **何时切回**:故障排除后(如 gate 恢复、连接修复),手动走通一张卡验证后切回。
- manual 的语义是"关自动派工",不是"开手动权限";两种模式下推进都只用产品命令。

## 手动模式完整回合

### 1. 看下一步(只读)

```bash
lybra next --task-id <卡ID>
```

产品推导当前节点与下一条命令;你据此决定是否执行。

### 2. 推进一步

```bash
lybra loop --task-id <卡ID> --envelope <驱动信封> --actor <驱动方实例> --max-steps 1
```

- 认领:信封一段式, 门内建卡工作树;**建树失败即拒认领**(队列不变)。
- 看什么信号:`5_tasks/records/claims/<ID>/claim_*.md`(认领落地)。

### 3. 工位开工

让执行工位敲 `/go`:产品选卡(`lybra my-tasks` 的 next_card)并给出工作树与报告落点。
工位只干活、只写报告, 不认领、不交回。

### 4. 交回与审计

执行体的报告落盘后, 再推进一步(同第 2 步命令):产品经 `lybra artifact ingest` 把交回入门,
并派生 `<ID>R` 审计卡。审计工位同样敲 `/go`, 只写审计报告;再推进一步即裁决入门。
- 看什么信号:`5_tasks/records/returns/<ID>/return_*.md`(交回)、
  `5_tasks/records/audit_verdicts/<被审卡ID>/verdict_*.md`(裁决)。
- PASS → 进 finalize;FAIL → 打 fix 卡回执行(有界修复循环,默认 2 轮)。
- 代码+部署卡:审计 PASS ≠ 可部署,部署确认属你
  (`owner_verify: required` 的不可逆确认,判断在你;仅生产级部署触发,开发环回部署不触发)。

### 5. 等待

等产物只用 `lybra agent watch --workspace-root <工作区> --expect <路径>`(有界、前台);
不写 `until`/`sleep` 轮询。

### 6. finalize 与结案

审计 PASS(且 Owner 真人核验,若卡声明 `owner_verify: required`)→ 继续推进至 finalize 与结案。
实现批准(审计)与发布批准(finalize)是两个门,永不合并。

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
| 认领没落地 | `lybra next --task-id <ID>` 看拒因(信封不覆盖/建树失败等);找顾问补信封或修卡 |
| 某步推进报失败 | 先看该步门生记录是否已落(已落即继续, 不重复执行);未落按拒因处理, 记产品缺口 |
| 审计死锁(2 轮 FAIL 仍不过) | 停,升级顾问仲裁(不空转) |
| 看不到当前模式 | `lybra project dispatch-mode show` 或看板只读呈现 |

---

**Document revision:** AIPOS-338 S5; AIPOS-F91 (退役的泵与贴卡认领改为现行 `lybra loop` + `/go`)
