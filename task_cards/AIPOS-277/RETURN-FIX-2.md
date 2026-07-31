# RETURN — AIPOS-277F2:FIX-2 modal-mask CSS 真因修复

**任务卡**: `~/projects/lybra/task_cards/AIPOS-277/FIX-2.md`  
**执行角色**: lybra-executor  
**状态**: ✅ 完成  
**时间**: 2026-07-31

---

## 执行摘要

按卡内字面要求，在 `web/board/static/overview.css` 的 `.modal-mask` 规则后增补
`.modal-mask[hidden]{display:none;}` 规则，修复真因（`display:flex` 覆盖 hidden 属性
导致面板永远可见）。

## 实施细节

### 代码变更

**文件**: `web/board/static/overview.css`  
**位置**: L313-324  
**修改**: 在现有 `.modal-mask{display:flex}` 规则（L313-321）之后增补：

```css
.modal-mask[hidden] {
  display: none;
}
```

### CSS Specificity 论证（渲染层取证）

- **基线规则** `.modal-mask` (L313):
  - Specificity: (0,1,0) — 单类选择器
  - 声明: `display: flex;`
  
- **覆盖规则** `.modal-mask[hidden]` (L323):
  - Specificity: (0,1,1) — 单类 + 属性选择器
  - 声明: `display: none;`
  - **层叠优先级**: (0,1,1) > (0,1,0)，且声明顺序靠后 → 当元素同时满足
    `.modal-mask` 和 `[hidden]` 时，`display:none` 生效。

- **UA样式对比**:  
  浏览器 `[hidden]{display:none}` 为 (0,0,1)，被 `.modal-mask` (0,1,0) 覆盖 → 旧bug。
  新增 (0,1,1) 规则明确赢过两者。

### 规则存在性断言

```bash
$ grep -n "\.modal-mask\[hidden\]" web/board/static/overview.css
323:.modal-mask[hidden] {
```

✅ 规则已落地，specificity 论证充分。

---

## 回归风险评估

**零回归预期**: 
- 未触及 JS 逻辑（`hidden` 属性切换由 overview.html 脚本控制，不变）
- 未触及其他 CSS 规则
- `.modal-mask[hidden]` 仅在元素携带 `hidden` 属性时生效，打开状态（无 `hidden`）
  仍走原 `display:flex` 路径

**人工复核点**（卡要求注明）:  
因环境未能启动真浏览器验证，建议人工确认：
1. 页面加载时面板默认隐藏（`<div class="modal-mask" hidden>`）
2. 点击"添加新项目"按钮，面板显示（JS 移除 `hidden`）
3. 点击关闭按钮 / 点击遮罩，面板消失（JS 设回 `hidden`）

---

## 实际资源消耗

- **模型**: anthropic/claude-3-5-sonnet-20241022  
- **Token 用量**(自报):
  - Input: ~12.7k
  - Output: ~1.1k
  - Total: ~13.8k
- **耗时**: <2分钟

---

## 交付物

- 修改文件: `web/board/static/overview.css` (1处增补，2行新增)
- 本报告: `task_cards/AIPOS-277/RETURN-FIX-2.md`

**修复完整度**: ✅ 卡内要求全满足（CSS 规则 + specificity 论证 + 人工复核点标注）  
**ready for**: 顾问审计 / 人工浏览器复核
