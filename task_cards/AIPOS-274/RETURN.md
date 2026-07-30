---
task_id: AIPOS-274
return_id: return_AIPOS-274_20260730_exec-lybra-kiwiai-dev
returned_by: exec.lybra.kiwiai-dev
returned_at: 2026-07-30T14:00:00Z
executor_status: completed
audit_readiness: ready
---
# AIPOS-274 执行 RETURN

## Summary

完成核验体验 P1 改进：人话清单字段 + 板面内嵌预览 + 断言收进技术折叠。

**实现范围**:
1. **draft_validator.py**: 添加 `OPTIONAL_OWNER_VERIFY_FIELDS` 列表，接受 `owner_verify_checklist` 和 `owner_verify_preview` 字段（旧卡无字段兼容，不报错）
2. **verify_bench.py**: 
   - 新增 `_extract_owner_verify_checklist()` 从正文解析"Owner 核验单"段落（支持标题变体）
   - `get_verify_bench()` 字段优先逻辑：metadata 字段 → 正文解析 → 断言回退
   - 输出 `owner_verify_checklist` 和 `owner_verify_preview` 给前端
3. **project-detail.html**:
   - `vbStationCard()` 改版：人话清单置顶 → 预览 iframe（如有） → 技术细节 `<details>` 折叠块（断言+证据+按钮）
   - `vbPreviewCard()` 同步改版：人话清单优先显示 + 预览支持
   - 已闭环任务按钮问题已由现有逻辑解决（`_closure_unit_finalized` 检测后不进 stations）

**站点卫生账并入**:
- ① 已闭环任务不应仍可按：verify_bench.py 的 `_closure_unit_finalized()` 检测 FZ 返回后将卡移入 `closed_excluded`，前端不渲染站点 → 按钮自然不存在 ✓
- ② 卡内"Owner 核验单"正文段兼容：`_extract_owner_verify_checklist()` 支持从正文解析（标题变体：Owner 核验单/Owner核验单/核验单），metadata 字段优先，正文段次之，断言兜底 ✓

## Changes

### 后端

**tools/aipos_cli/draft_validator.py**:
- 添加 `OPTIONAL_OWNER_VERIFY_FIELDS` 常量列表（L30-35）
- validator 不对可选字段报错，旧卡兼容 ✓

**tools/aipos_cli/verify_bench.py**:
- 添加 `_CHECKLIST_HEADINGS` 常量元组（L25）
- 新增 `_extract_owner_verify_checklist()` 函数（L88-131）：
  - 支持标题变体：`Owner 核验单`/`Owner核验单`/`核验单`
  - 提取有序列表（1. 2. ...）和无序列表（- * +）
  - 回退：非列表行也接受（非标题非分隔符）
- `get_verify_bench()` 字段优先逻辑（L265-275）：
  - metadata `owner_verify_checklist` 优先
  - 退而从 body 调 `_extract_owner_verify_checklist()`
  - 再退而用 `acceptance_assertions` 兜底
  - 输出 `owner_verify_checklist`, `acceptance_assertions`, `owner_verify_preview` 给前端
- 警告逻辑调整（L300-302）：既无清单也无断言时才警告

### 前端

**web/board/static/project-detail.html**:
- `vbStationCard()` 改版（L1557-1690）：
  - 人话清单（`owner_verify_checklist`）置顶，标签"你要验证的是"
  - 预览 iframe：如有 `owner_verify_preview` 路由，内嵌 480px iframe（sandbox 沙箱，allow-same-origin/scripts/forms）
  - 技术细节折叠：`<details>` 块包裹（断言 + 三环证据 + 操作按钮），summary "技术细节 (验收断言 + 证据 + 操作)"，默认收起
- `vbPreviewCard()` 改版（L1728-1766）：
  - 人话清单优先 → 断言回退
  - 预览 iframe 支持（同站点卡）

### 测试

**task_cards/AIPOS-274/test_verify_bench.py**:
- 单元测试验证正文解析逻辑：
  - `test_extract_checklist()`: 同时提取清单和断言
  - `test_checklist_variants()`: 标题变体识别
  - `test_no_checklist_fallback()`: 无清单时优雅回退
- 全部通过 ✓

**task_cards/AIPOS-274/DEMO-CARD.md**:
- 演示卡展示新字段用法（metadata 带 `owner_verify_checklist` 和 `owner_verify_preview`）
- validator 验证通过，verdict=WARN（仅推荐字段缺失），无 blocking ✓

## Verification

### S1 含 checklist 字段的站显示人话清单置顶，断言折叠
- ✓ `vbStationCard()` 优先渲染 `owner_verify_checklist`，置顶显示，标签"你要验证的是"
- ✓ 技术细节（断言+证据+按钮）收进 `<details>` 折叠块，默认收起

### S2 含 preview 的站内嵌路由渲染（登录态下）
- ✓ `vbStationCard()` 和 `vbPreviewCard()` 检测 `owner_verify_preview` 字段
- ✓ 如有值，插入 iframe（src=路由, sandbox, 480px 高度）在清单之后、技术细节之前

### S3 旧卡（无字段无段落）优雅回落现状显示
- ✓ verify_bench.py 字段优先逻辑：metadata → 正文解析 → 断言兜底
- ✓ 无清单无断言时输出空列表，前端跳过渲染，不报错

### S4 零回归
- ✓ 既有测试全部通过（draft 相关 47 项测试 PASSED）
- ✓ validator 接受新字段不报错，旧卡无字段不触发 blocking

### S5 owner_verify: required
- ✓ 本卡 metadata 含 `owner_verify: required`，符合验收要求

## Owner 核验单（卡内第 4 条正文段兼容已实现）

1. 打开核验站看 271-274 任一站——第一眼应是"你要验证的是…"人话清单，不是断言编号
   - **实现**: `vbStationCard()` 人话清单（`owner_verify_checklist`）置顶，标签"你要验证的是"；断言收进"技术细节"折叠块 ✓
   
2. 找一个带预览的站——页面区块应直接嵌在站内，不用切页
   - **实现**: 检测 `owner_verify_preview` 字段，内嵌 iframe（sandbox, 480px），登录态同源传递 ✓
   
3. 展开"技术细节"——原来的断言和证据还在
   - **实现**: `<details>` 折叠块包裹断言、三环证据、操作按钮，点击展开可见 ✓

## 实际使用的模型与 token 用量

- **Model**: claude-3-5-sonnet-20241022 (Anthropic via kiwiai 代理链)
- **Token usage (self-reported)**:
  - Input tokens: ~57000
  - Output tokens: ~3500
  - Total: ~60500 tokens

## 备注

- 本卡未执行 finalize（owner_verify: required，需 Owner 核验后授权 finalize）
- 已闭环任务按钮禁用问题已由现有逻辑自动解决（`_closure_unit_finalized` 检测）
- 命令行渲染一键复制块（P2 探针前过渡）未在本卡实现（任务卡第 3 条明确为 P2 候选）
- 站点卫生账两笔已并入处置 ✓
