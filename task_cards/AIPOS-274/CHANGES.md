# AIPOS-274 变更清单

## 修改的文件

### 1. tools/aipos_cli/draft_validator.py
**变更**: 添加可选 Owner 核验字段常量
- **位置**: L30-35（在 RECOMMENDED_FIELDS 之后）
- **内容**: 新增 `OPTIONAL_OWNER_VERIFY_FIELDS` 列表，包含 `owner_verify_checklist` 和 `owner_verify_preview`
- **理由**: 让 draft validator 接受新字段，不报 blocking 错误（旧卡无字段兼容）

### 2. tools/aipos_cli/verify_bench.py
**变更**: 支持人话清单字段 + 正文段落解析 + 预览路由
- **位置 1**: L25 添加 `_CHECKLIST_HEADINGS` 常量元组
- **位置 2**: L88-131 新增 `_extract_owner_verify_checklist()` 函数
  - 从正文解析"Owner 核验单"段落（支持标题变体）
  - 提取有序列表、无序列表、非列表行（回退兜底）
- **位置 3**: L265-275 `get_verify_bench()` 字段优先逻辑
  - metadata `owner_verify_checklist` 优先
  - 退而调 `_extract_owner_verify_checklist()` 从正文解析
  - 再退而用 `acceptance_assertions` 兜底
  - 输出新字段 `owner_verify_checklist`, `owner_verify_preview` 给前端
- **位置 4**: L300-302 警告逻辑调整
  - 既无清单也无断言时才警告（之前只要无断言就警告）

### 3. web/board/static/project-detail.html
**变更**: 核验站前端改版（人话清单置顶 + 预览内嵌 + 断言折叠）
- **位置 1**: L1557-1690 `vbStationCard()` 函数改版
  - 人话清单（`owner_verify_checklist`）置顶显示，标签"你要验证的是"
  - 预览 iframe：如有 `owner_verify_preview`，内嵌 480px iframe（sandbox 沙箱）
  - 技术细节折叠：`<details>` 块包裹断言、三环证据、操作按钮，summary "技术细节 (验收断言 + 证据 + 操作)"
- **位置 2**: L1728-1766 `vbPreviewCard()` 函数改版
  - 人话清单优先显示 → 断言回退
  - 预览 iframe 支持（同站点卡）

## 新增的文件

### 1. task_cards/AIPOS-274/test_verify_bench.py
- 单元测试：验证正文解析逻辑（清单提取、标题变体、回退兼容）
- 状态: 全部通过 ✓

### 2. task_cards/AIPOS-274/DEMO-CARD.md
- 演示卡：展示新字段用法（metadata 带 `owner_verify_checklist` 和 `owner_verify_preview`）
- 状态: validator 验证通过（verdict=WARN，仅推荐字段缺失）

### 3. task_cards/AIPOS-274/RETURN.md
- 本次执行返回记录

### 4. task_cards/AIPOS-274/CHANGES.md
- 本文件（变更清单）

## 验证结果

- ✓ 既有测试全部通过（draft 相关 47 项 PASSED）
- ✓ 新增单元测试通过（正文解析逻辑）
- ✓ validator 接受新字段不报错
- ✓ 演示卡验证通过（无 blocking reasons）
- ✓ 站点卫生账两笔已并入处置：
  - 已闭环任务按钮问题由 `_closure_unit_finalized` 自动解决
  - 正文段"Owner 核验单"兼容已实现

## 零依赖声明

- 无新增外部依赖
- 无修改治理仓文件
- 无修改 kiwiai-pi 护栏与扩展（只读）
- 车道内实现（web/board + tools/aipos_cli + task_cards/AIPOS-274/）
