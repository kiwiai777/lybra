# AIPOS-274 测试检查清单

## 验收断言（S1-S5）

### S1 含 checklist 字段（或正文核验单段）的站显示人话清单置顶，断言折叠
- [x] **后端**: verify_bench.py 字段优先逻辑
  - metadata `owner_verify_checklist` 优先
  - 退而调 `_extract_owner_verify_checklist()` 从正文解析"Owner 核验单"段
  - 再退而用 `acceptance_assertions` 兜底
- [x] **前端**: vbStationCard() 改版
  - 人话清单置顶，标签"你要验证的是"
  - 技术细节（断言+证据+按钮）收进 `<details>` 折叠块
- [x] **验证**: test_verify_bench.py 单元测试通过 ✓

### S2 含 preview 的站内嵌路由渲染（登录态下）
- [x] **后端**: verify_bench.py 输出 `owner_verify_preview` 字段给前端
- [x] **前端**: vbStationCard() 和 vbPreviewCard() 检测 preview 字段
  - 如有值，插入 iframe（src=路由, sandbox, 480px 高度）
  - sandbox 属性：allow-same-origin allow-scripts allow-forms
- [x] **验证**: DEMO-CARD.md 示例带 `owner_verify_preview: /workspace/0`

### S3 旧卡（无字段无段落）优雅回落现状显示
- [x] **后端**: 三层回退机制
  1. metadata `owner_verify_checklist` 字段
  2. 正文 `_extract_owner_verify_checklist()` 解析
  3. `acceptance_assertions` 兜底
- [x] **前端**: 无清单时优雅跳过渲染，不报错
- [x] **验证**: test_no_checklist_fallback() 测试通过 ✓

### S4 零回归
- [x] **既有测试**: 47 项 draft 相关测试全部 PASSED
- [x] **validator 兼容**: 接受新字段不报 blocking 错误
- [x] **HTML 结构**: 语法验证通过 ✓

### S5 owner_verify: required
- [x] 本卡 metadata 含 `owner_verify: required`
- [x] RETURN.md 注明"未 finalize（需 Owner 核验后授权）"

## Owner 核验单（人话清单）

### 1. 打开核验站看 271-274 任一站——第一眼应是"你要验证的是…"人话清单
- [x] vbStationCard() 人话清单置顶，标签"你要验证的是"
- [x] 技术断言收进"技术细节"折叠块，默认收起
- [x] 前端渲染顺序：卡头 → 人话清单 → [预览] → 技术细节折叠

### 2. 找一个带预览的站——页面区块应直接嵌在站内
- [x] 检测 `owner_verify_preview` 字段
- [x] 内嵌 iframe（480px 高度，sandbox 沙箱）
- [x] 登录态同源传递（allow-same-origin）

### 3. 展开"技术细节"——原来的断言和证据还在
- [x] `<details>` 折叠块包裹
- [x] summary: "技术细节 (验收断言 + 证据 + 操作)"
- [x] 内容：断言列表 + 三环证据（machine/audit/fix）+ 操作按钮

## 站点卫生账（任务卡额外要求）

### ① 已闭环任务按钮禁用/移除
- [x] **现有逻辑解决**: `_closure_unit_finalized()` 检测 FZ 返回
- [x] 已闭环卡移入 `closed_excluded`，不进 stations
- [x] 前端不渲染站点 → 按钮自然不存在 ✓

### ② 卡内"Owner 核验单"正文段解析兼容（274 原卡第 4 条）
- [x] `_extract_owner_verify_checklist()` 从正文解析
- [x] 支持标题变体：Owner 核验单 / Owner核验单 / 核验单
- [x] metadata 字段优先，正文段次之，断言兜底
- [x] test_checklist_variants() 测试通过 ✓

## 实现完整性

### 后端（tools/aipos_cli/）
- [x] draft_validator.py: OPTIONAL_OWNER_VERIFY_FIELDS 常量
- [x] verify_bench.py: _CHECKLIST_HEADINGS 常量
- [x] verify_bench.py: _extract_owner_verify_checklist() 函数
- [x] verify_bench.py: get_verify_bench() 字段优先逻辑
- [x] verify_bench.py: 警告逻辑调整（既无清单也无断言才警告）

### 前端（web/board/static/）
- [x] project-detail.html: vbStationCard() 改版（3 段式布局）
- [x] project-detail.html: vbPreviewCard() 改版（清单优先 + 预览）
- [x] HTML 结构合法（验证通过）

### 测试与文档
- [x] task_cards/AIPOS-274/test_verify_bench.py（单元测试）
- [x] task_cards/AIPOS-274/DEMO-CARD.md（演示卡）
- [x] task_cards/AIPOS-274/RETURN.md（返回记录）
- [x] task_cards/AIPOS-274/CHANGES.md（变更清单）
- [x] task_cards/AIPOS-274/TEST-CHECKLIST.md（本文件）

## 红线遵守

- [x] 车道内实现（web/board + tools/aipos_cli + task_cards/AIPOS-274/）
- [x] 治理仓只读（未修改 ~/ai-project-os）
- [x] kiwiai-pi 只读（未修改 ~/projects/kiwiai-pi/lybra-executor/）
- [x] 零新增依赖
- [x] 旧卡兼容（无字段不报错）
- [x] 凭据不涉及（本卡无凭据操作）
- [x] 未 commit/push（owner_verify: required，需 Owner 核验后授权 finalize）

## 待 Owner 核验

本卡已完成实现与自测，符合验收断言 S1-S5 + Owner 核验单 1-3 条 + 站点卫生账 ①②。
所有代码已就位，测试通过，等待 Owner 真机核验后授权 finalize。

**核验方式**（按 Owner 核验单 1-3 条）:
1. 启动 board，进入验证台区域
2. 找到带 `owner_verify_checklist` 字段的卡（或有"Owner 核验单"正文段的卡）
3. 展开站点，验证：
   - 第一眼看到人话清单"你要验证的是…"
   - 有预览路由的，页面内嵌显示
   - 点"技术细节"，断言和证据都在

**实际模型与 token 用量**:
- Model: claude-3-5-sonnet-20241022 (Anthropic via kiwiai)
- Input: ~61000 tokens
- Output: ~4000 tokens
- Total: ~65000 tokens
