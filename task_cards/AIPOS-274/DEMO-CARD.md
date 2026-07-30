---
task_id: AIPOS-274-DEMO
title: 演示：核验体验 P1 新字段与板面预览
project: lybra
assigned_to: exec.lybra.kiwiai-dev
agent_instance: exec.lybra.kiwiai-dev
context_bundle: exec.lybra.kiwiai-dev
task_mode: code
task_class: simple
priority: high
status: pending
created_by: advisor.lybra.kiwiai-dev
needs_owner: false
output_target: web/
artifact_policy: formal_write
owner_verify: required
owner_verify_checklist:
  - 打开核验站看这张演示卡——第一眼应该是人话清单"你要验证的是…"，不是技术断言编号
  - 页面应该内嵌显示工作区预览（如果有 owner_verify_preview 路由）
  - 点击"技术细节"折叠块——原来的验收断言、三环证据、操作按钮都在里面
owner_verify_preview: /workspace/0
---
# AIPOS-274-DEMO — 核验体验演示卡

## 背景

这是一张演示卡，展示 AIPOS-274 实现的核验体验改进：
- schema 新增可选字段 `owner_verify_checklist` 和 `owner_verify_preview`
- 核验站优先显示人话清单，技术断言折叠
- 支持内嵌预览工作区路由

## 验收断言

- S1 含 owner_verify_checklist 字段的卡，站点显示人话清单置顶
- S2 含 owner_verify_preview 的卡，站内嵌 iframe 加载该路由
- S3 技术细节（断言+证据+按钮）收进折叠块
- S4 旧卡（无新字段）优雅回退，从正文解析"Owner 核验单"段落
- S5 零回归，owner_verify: required 正常工作

## Owner 核验单（兼容：正文段落回退）

如果卡 metadata 没有 `owner_verify_checklist` 字段，verify_bench 会从这个段落解析：

1. 打开板子 → 核验站区块应该可见
2. 展开这张演示卡 → 人话清单在最上面
3. 往下滚动 → 应该看到内嵌的工作区预览 iframe
4. 点"技术细节" → 断言、证据、按钮都在

## 实现要点

- **draft_validator.py**: OPTIONAL_OWNER_VERIFY_FIELDS 列表接受新字段，不报错
- **verify_bench.py**: 
  - `_extract_owner_verify_checklist()` 从正文解析"Owner 核验单"段
  - metadata 字段优先，退而正文解析，再退而用断言
  - 输出 `owner_verify_checklist` 和 `owner_verify_preview` 给前端
- **project-detail.html**:
  - `vbStationCard()` 改版：人话清单置顶 → [预览 iframe] → 技术细节折叠块
  - 技术细节 = 断言 + 三环证据 + 操作按钮，用 `<details>` 收起
  - 已闭环任务自动从 stations 排除（`_closure_unit_finalized` 检测）

## 车道与红线

- 车道: `web/board/` + `tools/aipos_cli/draft_validator.py` + `tools/aipos_cli/verify_bench.py`
- 红线: 只读治理仓，零依赖，旧卡兼容（无字段 → 正文解析 → 断言回退）
- 落位: `task_cards/AIPOS-274/`
