# RETURN-FIX-4 — AIPOS-274 vb.* i18n 键裸奔补翻译

## 1. 结论

**完成。** 9 个 `vb.*` 缺失 i18n 键全部补入 zh/en 双语字典；全站扫描确认渲染文案零裸键。

## 2. 做了什么

1. 从 `project-detail.html` 提取所有 `vb.*` 键引用（31 个唯一键）。
2. 与 `i18n.js` 已定义键比对，定位 9 个缺失键。
3. 在 `i18n.js` 的 zh 和 en 两个字典段各补入 9 条翻译。
4. 全量扫描 `web/` 下所有 `.js` / `.html` 文件中 `i18n.t('...')` 调用，确认零裸键。
5. Node.js 运行时验证：zh 和 en 双语下 9 个新键全部正确翻译，无回退到键名本身。

## 3. 改动清单

| 文件 | 改动 |
|------|------|
| `~/projects/lybra/web/board/static/i18n.js` | zh 段 +9 键；en 段 +9 键（共 18 条新增） |

**新增键明细（zh / en）：**

| 键 | zh | en |
|----|----|----|
| `vb.action.processing` | 处理中... | Processing... |
| `vb.action.verified` | 已核验 | Verified |
| `vb.action.rejected` | 已打回 | Rejected |
| `vb.reject.confirm` | 确认打回 | Confirm reject |
| `vb.reject.cancel` | 取消 | Cancel |
| `vb.reject.reason_required` | 请输入打回理由 | Please enter a rejection reason |
| `vb.reject.reason_placeholder` | 请输入打回理由... | Enter rejection reason... |
| `vb.success.approved` | 已通过核验，记录已写入 | Approved, record written |
| `vb.success.rejected` | 已打回，记录已写入 | Rejected, record written |

## 4. 测试/验证结果原文

### 4.1 键覆盖完整性（零缺失断言）

```
$ comm -23 <(used_keys | LC_ALL=C sort) <(defined_keys | LC_ALL=C sort)
（空输出 = 零缺失）
```

### 4.2 zh 双语翻译验证（Node.js 运行时）

```
OK: vb.action.processing => 处理中...
OK: vb.action.verified => 已核验
OK: vb.action.rejected => 已打回
OK: vb.reject.confirm => 确认打回
OK: vb.reject.cancel => 取消
OK: vb.reject.reason_required => 请输入打回理由
OK: vb.reject.reason_placeholder => 请输入打回理由...
OK: vb.success.approved => 已通过核验，记录已写入
OK: vb.success.rejected => 已打回，记录已写入

All 9 new keys translate correctly (zh).
```

### 4.3 en 双语翻译验证（Node.js 运行时）

```
OK: vb.action.processing => Processing...
OK: vb.action.verified => Verified
OK: vb.action.rejected => Rejected
OK: vb.reject.confirm => Confirm reject
OK: vb.reject.cancel => Cancel
OK: vb.reject.reason_required => Please enter a rejection reason
OK: vb.reject.reason_placeholder => Enter rejection reason...
OK: vb.success.approved => Approved, record written
OK: vb.success.rejected => Rejected, record written

All 9 new keys translate correctly (en).
```

## 5. 排除物

无。卡要求全部执行。

## 6. 异常与自作判断

无偏离。改动严格限于 `i18n.js` 字典新增，未触碰 HTML 模板或 JS 逻辑。

## 7. 实际模型 + token 自报

`model=kiwiai/qwen3.7-plus, tokens≈15k/3k`（单会话冷启动，含读卡+扫描+编辑+验证）

## 8. 待办 / 移交

- 顾问/Owner：验收 S1 打回流程全程人话文案、S2 zh/en 双语齐、S3 零回归。
- 本卡未授权 commit/push，工作区改动待授权后 finalize。

下一棒:auditor 跑 → `/claim /home/kiwi/projects/lybra/task_cards/AIPOS-274/AUDIT-FIX-4.md`（审计卡待顾问落笔）
