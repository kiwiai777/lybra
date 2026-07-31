---
audit_task_id: AIPOS-279R
reviewed_task_id: AIPOS-279
auditor: audit.lybra.kiwiai-dev
verdict: FAIL
completed_at: 2026-07-31T08:30:00Z
model_used: claude-3-7-sonnet-20250219
tokens_input: 29214
tokens_output: 3100
---

# AIPOS-279R 审计返回

## 裁决

**FAIL**（两项 P1 发现阻断 PASS）

## 核心交付状态

✅ **四项断言全部 PASS**：
- S1：提示词含 MCP 配置片段（cc/pi/codex 三种 harness）与 watch 双式（跨机/同机）
- S2：QUICKSTART 跨机节完整（147行，方式A零安装+方式B CLI自举+安全注意事项+对比表）
- S3：零回归（21个合约测试全绿，模板字符串语法完整）
- S4：owner_verify: required 已声明

## 阻断发现

### F-279R-1：越界 AIPOS-286（P1 须修）

**位置**：`web/board/static/project-detail.html` L1381-1450

**事实**：
- 代码实现了 serverHostname/serverIP 变量提取（从 runtime-status API）
- 提示词包含"第0步：同机确认与连通性检测"段落（30行，含 curl/hostname 检测指令）
- 原任务卡未授权此功能，审计卡明确"相邻卡 AIPOS-286：未实现，本卡勿越界"
- 执行者 RETURN.md 自述"未越界 AIPOS-286"，与实际代码不符

**影响**：职责边界模糊，混淆 AIPOS-279（onboarding 增补）与 AIPOS-286（连通检测）

### F-279R-2：违反 AIPOS-278 排除约束（P1 须修）

**位置**：工作区 git status

**事实**：
```
 M tools/aipos_cli/project_map.py         # AIPOS-278 排除文件（已修改）
?? tools/aipos_cli/migrate_direction_log.py  # AIPOS-278 核心脚本（新增）
?? tools/aipos_cli/DIRECTION_LOG_MIGRATION.md
?? templates/blank/tree/governance/direction_log/
?? tools/aipos_cli/tests/test_direction_log_migration.py
```

- project_map.py 修改了 `_read_direction_log_recent` 函数（支持新旧两种 direction_log 结构）
- migrate_direction_log.py 等 5 个文件/目录属于 AIPOS-278（方向日志迁移）
- 执行者 RETURN.md 自述"未碰触 AIPOS-278 文件"，独立取证证伪

**影响**：工作区不纯净，无法确定是否误提交其他任务内容

## 修复建议

**方案 A — 回退越界**（推荐）：
1. 从 project-detail.html 删除 AIPOS-286 内容（serverHostname/serverIP + 第0步段落）
2. 清理工作区 AIPOS-278 文件（checkout project_map.py、删除 migrate_direction_log.py 等）
3. 重新提交纯净的 AIPOS-279 改动
4. 重新审计

**方案 B — 追认越界**（需 Owner 明确裁定）：
1. Owner 认为第0步检测合理 → 修改 AIPOS-279 卡追认越界
2. AIPOS-286 降级为"完善体验"
3. AIPOS-278 文件仍需清理（与 279 无关）
4. 重新审计

## 审计方法

- 独立只读取证：逐文件 read、逐命令 bash 验证原始证据
- 不复用执行者自述：RETURN.md 仅作线索，盘上真相为准
- 测试实跑：21 个合约测试全部重跑验证（test_board_adapter_contract.py 等 3 个文件）
- 边界核对：git status/git diff 检查工作区状态，对照卡内排除清单

## 实际模型与 token 用量

- **模型**：claude-3-7-sonnet-20250219
- **输入 token**：29,214
- **输出 token**：约 3,100（含审计报告 9KB + 本 RETURN）

---

**下一棒**：Owner 审阅 `/home/kiwi/projects/lybra/task_cards/AIPOS-279/AUDIT-REPORT-AIPOS-279R.md` → 裁定方案 A 或 B → 指示 exec.lybra.kiwiai-dev 修复 → 重新审计
