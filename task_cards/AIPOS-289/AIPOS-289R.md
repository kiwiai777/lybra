---
task_id: AIPOS-289R
title: '审计:AIPOS-289 结案督察治理账校验'
project: lybra
status: pending
task_mode: audit
task_class: simple
priority: high
created_by: exec.lybra.kiwiai-dev
assigned_to: auditor.lybra.kiwiai-dev
agent_instance: auditor.lybra.kiwiai-dev
context_bundle: auditor.lybra.kiwiai-dev
needs_owner: false
audit_scope: implementation
reviewed_task_id: AIPOS-289
parent_task_id: AIPOS-289
artifact_policy: formal_write
output_target: task_cards/AIPOS-289/
---
# 审计任务：AIPOS-289 结案督察

## 审计范围
**实现类审计**（AIPOS-289: 结案督察 close 动词扩展治理账机器校验）

## 被审计交付
- **交付路径**: `~/projects/lybra/task_cards/AIPOS-289/`
- **主卡 RETURN**: `RETURN.md`
- **代码修改**:
  - `tools/aipos_cli/board_adapter.py` (close_task 治理账校验逻辑)
  - `tools/aipos_cli/record_writer.py` (闭包记录 warnings 支持)
  - `tools/aipos_cli/records.py` (warnings 字段加载修复)
  - `tools/aipos_cli/tests/test_queue_close.py` (3 个新测试)
  - `templates/*/tree/stage_archive/` (3 个模板新增目录)

## 验收断言（来自卡面 S1-S4）
### S1: decision_log 扫描
- [ ] close 动词扫描 `governance/decision_log` 双名（.md 或 / 目录）
- [ ] 缺 task_id 条目时，closure 记录 warnings 写 "decision_log 缺 <ID> 账"
- [ ] WARN 不拦截 close（append-only）

### S2: stage_archive 鲜度
- [ ] close 时若 stage_archive/ 最新文件 mtime 超阈（默认 30 天）→ 超龄 WARN
- [ ] 阈值可配（代码可见默认值）
- [ ] stage_archive/ 不存在也 WARN

### S3: init 模板补 stage_archive/
- [ ] blank/consulting-engagement/software-development 三模板均补齐 stage_archive/ 目录
- [ ] 含 README.md 说明（AIPOS-153 引用对齐）

### S4: 测试 + 零回归
- [ ] 缺账 WARN 测试：test_missing_decision_log_entry_warns
- [ ] 超龄 WARN 测试：test_stale_stage_archive_warns
- [ ] 有账无 WARN 测试：test_valid_governance_no_warnings
- [ ] 既有 close 流程零回归（全部通过）

## 红线校验
- [ ] **车道边界**: 只改 `tools/aipos_cli/` 与 `templates/`，未越界
- [ ] **治理仓只读**: 未写 `~/ai-project-os`
- [ ] **督察不代笔**: 只读校验 + WARN，不代写治理内容，无定时器
- [ ] **不碰护栏**: 未改 `_shared/` 或角色目录（除 contrib/<卡号>/ 明确授权外）
- [ ] **不自 commit**: 无 git commit/push（除非卡明确授权 finalize）

## 审计检查清单
### 代码实现审查
- [ ] decision_log 扫描逻辑正确（词边界匹配，非子串）
- [ ] stage_archive 鲜度计算正确（mtime vs 当前时间，天数换算）
- [ ] warnings 字段正确传递到闭包记录（frontmatter + body）
- [ ] records.py 加载时正确合并 frontmatter warnings
- [ ] 无硬编码路径/魔数（阈值有明确默认值）

### 测试覆盖审查
- [ ] 三个新测试正确设置 governance 结构（decision_log/ + stage_archive/）
- [ ] 测试断言同时检查 response warnings 和 closure record warnings
- [ ] 零回归测试通过（既有 10 个 close 测试 + 3 个新增）

### 模板完整性审查
- [ ] 三个模板均添加 stage_archive/ 目录
- [ ] README.md 内容符合 AIPOS-153 定位（stage transition records）

### 文档完整性审查
- [ ] RETURN.md 如实汇报（实际完成 vs 卡面 S1-S4）
- [ ] RETURN.md 含实际模型与 token 自报
- [ ] 自产审计卡本身（AIPOS-289R）结构完整

## 审计执行指引
1. **代码审查**: 阅读 5 个修改文件，确认逻辑符合 S1-S2 规范
2. **运行测试**: `pytest tools/aipos_cli/tests/test_queue_close.py -v`
3. **零回归**: `pytest tools/aipos_cli/tests/ -k "close" -v`
4. **模板检查**: 确认三模板 stage_archive/ 目录存在且含 README
5. **红线对账**: 确认未越车道、未写治理仓、未碰护栏

## 预期审计结果
- **PASS**: S1-S4 全部实现，测试通过，红线无违反 → 可 finalize
- **NEEDS_REVISION**: 发现逻辑缺陷/测试覆盖不足/红线违反 → 返工修复
- **BLOCK**: 严重越界/破坏既有功能 → 拒收

## 审计交付要求
审计完成后，auditor 在 `task_cards/AIPOS-289/` 写入：
- `AUDIT_VERDICT.md`（verdict: PASS/NEEDS_REVISION/BLOCK + 详细理由）
- 若 PASS，附 finalize 建议（commit message、是否需 Owner 复核等）
