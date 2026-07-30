# AIPOS-274 执行摘要

## 任务概述
核验体验 P1 改进：人话核验单字段 + 板面类交付内嵌预览 + 断言收进技术折叠。

## 执行状态
✅ **已完成** - 所有验收断言 S1-S5 满足，Owner 核验单 3 条实现，站点卫生账 2 笔并入处置。

## 核心成果

### 1. Schema 扩展（加法式，旧卡兼容）
- `owner_verify_checklist`: 可选字段，人话步骤列表
- `owner_verify_preview`: 可选字段，板内路由（如 /workspace/0）
- draft validator 接受新字段不报错 ✓

### 2. 后端逻辑（verify_bench.py）
- **三层回退机制**:
  1. metadata `owner_verify_checklist` 字段（优先）
  2. 正文"Owner 核验单"段落解析（兼容）
  3. `acceptance_assertions` 断言回退（兜底）
- **正文解析**: 支持标题变体（Owner 核验单/Owner核验单/核验单）
- **输出扩展**: 新增 `owner_verify_checklist` 和 `owner_verify_preview` 字段给前端

### 3. 前端改版（project-detail.html）
- **核验站新布局**（三段式）:
  1. **人话清单置顶** - "你要验证的是…"标签，清晰易读
  2. **预览内嵌**（可选）- iframe 加载板内路由（480px，sandbox 沙箱）
  3. **技术细节折叠** - `<details>` 块包裹断言、三环证据、操作按钮
- **进行中预览** - 同步改版，清单优先显示

### 4. 站点卫生账处置
- ✅ **已闭环任务按钮**: 现有逻辑 `_closure_unit_finalized()` 自动排除，不渲染站点
- ✅ **正文段兼容**: `_extract_owner_verify_checklist()` 解析"Owner 核验单"段落

## 变更范围

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `tools/aipos_cli/draft_validator.py` | 添加常量 | OPTIONAL_OWNER_VERIFY_FIELDS 列表 |
| `tools/aipos_cli/verify_bench.py` | 新增函数 + 逻辑改版 | 正文解析 + 字段优先 + 输出扩展 |
| `web/board/static/project-detail.html` | 函数改版 | vbStationCard/vbPreviewCard 三段式布局 |
| `task_cards/AIPOS-274/` | 新增交付物 | 测试、演示卡、文档 |

## 验证结果

| 项目 | 结果 |
|------|------|
| 验收断言 S1-S5 | ✅ 全部满足 |
| Owner 核验单 1-3 条 | ✅ 全部实现 |
| 站点卫生账 ①② | ✅ 并入处置 |
| 既有测试（47 项） | ✅ 全部 PASSED |
| 新增单元测试 | ✅ 全部通过 |
| HTML 结构验证 | ✅ 合法 |
| validator 兼容 | ✅ 新字段不报错 |
| 零依赖声明 | ✅ 无新增依赖 |
| 红线遵守 | ✅ 车道内实现，治理仓只读 |

## 交付物清单

```
task_cards/AIPOS-274/
├── RETURN.md              # 返回记录（含 Summary + Changes + Verification）
├── CHANGES.md             # 变更清单（3 个修改文件 + 4 个新增文件）
├── TEST-CHECKLIST.md      # 测试检查清单（S1-S5 + Owner 核验单 + 卫生账）
├── SUMMARY.md             # 本文件（执行摘要）
├── test_verify_bench.py   # 单元测试（正文解析逻辑）
└── DEMO-CARD.md           # 演示卡（新字段用法示例）
```

## 待 Owner 核验

本卡 `owner_verify: required`，已完成实现与自测，等待 Owner 真机核验：

**核验步骤**（按 Owner 核验单 1-3 条）:
1. 启动 board 进入验证台区域
2. 展开任一核验站（271-274 或本卡演示）
3. 验证：
   - ✓ 第一眼看到人话清单"你要验证的是…"（不是断言编号）
   - ✓ 有预览路由的，页面内嵌显示（不用切页）
   - ✓ 点"技术细节"折叠块，断言和证据都在

核验通过后，Owner 授权 finalize 收编。

## 实际使用的模型与 token 用量

- **Model**: claude-3-5-sonnet-20241022 (Anthropic via kiwiai 代理链)
- **Token usage** (self-reported):
  - Input tokens: ~62500
  - Output tokens: ~4200
  - Total: ~66700 tokens

---

**执行者**: exec.lybra.kiwiai-dev  
**完成时间**: 2026-07-30T14:00:00Z  
**状态**: completed, audit_readiness: ready, owner_verify: required
