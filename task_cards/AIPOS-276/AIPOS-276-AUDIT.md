---
task_id: AIPOS-276-AUDIT
title: 审计 AIPOS-276 地图防陈旧结构化实施
project: lybra
assigned_to: auditor.lybra.kiwiai-dev
agent_instance: auditor.lybra.kiwiai-dev
context_bundle: audit
task_mode: code_review
task_class: simple
priority: high
status: pending
created_by: exec.lybra.kiwiai-dev
needs_owner: false
output_target: task_cards/AIPOS-276/
artifact_policy: audit_record
reviewed_task_id: AIPOS-276
reviewed_return_record_ref: task_cards/AIPOS-276/RETURN.md
audit_scope: implementation
---

# AIPOS-276-AUDIT — 审计地图防陈旧结构化

## 审计目标

验证 AIPOS-276 实施的三个核心机制：
1. in_flight 段废弃 + 兼容读（warnings 触发正确）
2. publish 门鲜度督察（>3天触发 PROJECT_MAP_STALE warning）
3. 板面红标显示（>7天超龄红色 badge）

## 审计检查点

### A1: in_flight 废弃机制

**断言**:
- [ ] `project_map.py` 读取旧地图（含 in_flight）时产生 deprecation warning
- [ ] 返回的 `data.in_flight` 始终为空列表 `[]`
- [ ] `verdict` 为 `WARN`（有 warning 时）
- [ ] 其他字段（next/horizon/milestones）正常解析

**验证方式**: 运行 `test_aipos276.py::test_s1_old_map_compat`

### A2: publish 鲜度督察

**断言**:
- [ ] 地图 updated 早于最近 return >3天 → publish dry_run 产生 `PROJECT_MAP_STALE` warning
- [ ] warning 格式包含地图日期和最近收编日期
- [ ] 不阻断 publish（verdict 仍可为 PASS/WARN，不变 BLOCK）
- [ ] 无地图/无 updated/无 return 时优雅降级（不产生警告）

**验证方式**: 
- 运行 `test_aipos276.py::test_s2_stale_map_publish_warn`
- 运行 `test_aipos276.py::test_s3_no_map_graceful`
- 运行 `test_aipos276.py::test_s4_fresh_map_no_warn`

### A3: 板面红标显示

**断言**:
- [ ] CSS 定义 `.map-updated-badge.stale` 样式（红底红字 pill）
- [ ] JS 计算 `daysSince` 并在 >7 天时应用 stale class
- [ ] 红标文案为"地图已 N 天未更新"
- [ ] 非超龄情况显示常规灰色 badge
- [ ] 无 updated 字段时 badge 隐藏（不显示）

**验证方式**: 
- 代码审查 `web/board/static/project-detail.html`
- Owner 真机验证：修改工作区 project-map.md 的 updated 为 14 天前，刷新板面观察红标

### A4: 零回归检查

**断言**:
- [ ] 无地图的工作区不受影响（available=false 路径保持）
- [ ] 现有 project_map API 调用方无破坏性变更
- [ ] draft_writer 鲜度检查异常不崩溃（graceful degradation）

**验证方式**: 单元测试 S3 + 代码审查 try-except 包裹

### A5: 代码质量

**断言**:
- [ ] 新增函数 `_check_project_map_staleness` 有文档注释
- [ ] 错误处理：解析失败/文件不存在时静默降级
- [ ] 无硬编码魔数（3天/7天有注释说明来源）
- [ ] 符合项目代码风格（类型注解、命名规范）

**验证方式**: 代码审查

## 审计命令

```bash
cd ~/projects/lybra

# 运行单元测试
python3 task_cards/AIPOS-276/test_aipos276.py

# 检查修改的文件
git diff tools/aipos_cli/project_map.py
git diff tools/aipos_cli/draft_writer.py  
git diff web/board/static/project-detail.html

# 可选：真机验证红标（需要有 project-map.md 的工作区）
# 1. 修改 governance/project-map.md 的 updated: 为 14 天前
# 2. 启动 board: lybra board
# 3. 访问 http://localhost:7117/workspace/0 观察红标
```

## 预期产出

审计报告 `task_cards/AIPOS-276/AUDIT-VERDICT.md`，包含：
- 各检查点通过/失败状态
- 发现的问题（如有）
- 修复建议（如需要）
- 最终 verdict: PASS / FAIL / NEEDS_OWNER

## 审计边界

- **不审计**: 板面前端交互细节（点击行为、弹窗内容）——只验证红标显示逻辑
- **不审计**: 地图解析器完整性——只验证 in_flight 相关逻辑
- **审计重点**: 三个核心机制的契约正确性 + 零回归保证

## 参考

- 原始任务卡: `/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-276.md`
- 实施 RETURN: `task_cards/AIPOS-276/RETURN.md`
- 单元测试: `task_cards/AIPOS-276/test_aipos276.py`
