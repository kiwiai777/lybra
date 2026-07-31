---
task_id: AIPOS-288F4
return_type: delivery
status: completed
executor: exec.lybra.kiwiai-dev
returned_at: 2026-07-31T13:45:00Z
model: claude-sonnet-5
tokens_in: 27406
tokens_out: ~2500
duration: ~15min
---

# RETURN: AIPOS-288 FIX-4 — data-i18n applier 机制实现完成

## 执行摘要

已完成 FIX-4 卡要求的 a-d 四项，链根修复：data-i18n 机制从"只活在注释里"到完整实现。

## 实施细节

### a) applyTranslations() 实现

**文件**: `web/board/static/i18n.js`

新增 `applyTranslations(root = document)` 函数，实现：
- 扫描 `[data-i18n]` 属性，置 `textContent = t(key)`
- 扫描 `[data-i18n-attr]` 属性，支持 `"attrName:key"` 形式（如 `placeholder:key`）
- 支持多属性语法 `"placeholder:key1;title:key2"`（用分号分隔）
- 可传入 `root` 参数，支持局部容器应用（动态插入节点场景）

### b) 三触发点集成

1. **DOMContentLoaded**: 页面加载时自动应用一次
   ```javascript
   document.addEventListener('DOMContentLoaded', () => {
     applyTranslations();
   });
   ```

2. **switchLanguage()**: 语言切换后重新应用
   - 在 `switchLanguage()` 函数末尾追加 `applyTranslations()` 调用
   - renderPage() 或 reload 之后再次应用，确保全覆盖

3. **window.i18n 暴露**: 供动态渲染调用
   ```javascript
   window.i18n = {
     t,
     applyTranslations,  // 新增导出
     switchLanguage,
     getCurrentLang,
     createLanguageSwitcher
   };
   ```

### c) 契约测试

**新文件**: `web/board/tests/test_aipos288_fix4_applier.py`

实现5项断言：
1. `test_applier_function_exists`: 验证 applyTranslations 函数存在，包含 querySelectorAll('[data-i18n]') 和 setAttribute 逻辑
2. `test_applier_trigger_on_domcontentloaded`: 验证 DOMContentLoaded 监听器调用 applyTranslations
3. `test_applier_trigger_on_language_switch`: 验证 switchLanguage 内调用 applyTranslations
4. `test_applier_exported_in_window_api`: 验证 window.i18n 导出包含 applyTranslations
5. `test_all_data_i18n_keys_exist_in_dictionaries`: 验证所有 HTML 中引用的 data-i18n key 在 zh/en 字典齐全

**测试结果**: ✓ 全部通过

```
Running AIPOS-288 FIX-4 applier contract tests...
✓ Applier function exists
✓ Applier triggered on DOMContentLoaded
✓ Applier triggered on language switch
✓ Applier exported in window.i18n
✓ All data-i18n keys in dictionaries

All AIPOS-288 FIX-4 applier tests passed ✓
```

### d) EN 首帧复核

**问题定位**: 5 处 data-i18n 节点的 HTML 初始文本仍为中文，导致 EN 模式首帧闪现中文

**修复**: `web/board/static/project-detail.html`
- Line 794: `data-i18n="detail.loading"` 初始文本改为 "Loading project detail..."
- Line 851: `data-i18n="onboarding.loading"` 初始文本改为 "Loading..."
- Line 903, 905, 907: 3处 `data-i18n="tc.loading"` 初始文本改为 "Loading..."

**验证**: 运行 `test_aipos288_cjk_source_guard.py` 中的 `test_html_data_i18n_initial_text` 断言

```
✓ HTML data-i18n initial text is EN or empty
```

现在 EN 首帧不含"加载项目详情"等中文，HTML 硬编码文本保留为 EN 作为 no-JS 后备，JS 可用时首帧即替换。

## 测试结果

运行全量 AIPOS-288 守卫测试：

```bash
$ cd /home/kiwi/projects/lybra/web/board && python3 tests/test_aipos288_cjk_source_guard.py
Running AIPOS-288 CJK source guard...
✓ No bare CJK literals found
✓ i18n.js dictionaries are complete
✓ Python i18n templates are complete
✓ HTML data-i18n initial text is EN or empty

All AIPOS-288 guards passed ✓
```

运行 FIX-4 applier 契约测试：

```bash
$ cd /home/kiwi/projects/lybra/web/board && python3 tests/test_aipos288_fix4_applier.py
Running AIPOS-288 FIX-4 applier contract tests...
✓ Applier function exists
✓ Applier triggered on DOMContentLoaded
✓ Applier triggered on language switch
✓ Applier exported in window.i18n
✓ All data-i18n keys in dictionaries

All AIPOS-288 FIX-4 applier tests passed ✓
```

## 改动文件清单

1. `web/board/static/i18n.js` (修改)
   - 新增 `applyTranslations(root)` 函数 (~30 行)
   - `switchLanguage()` 末尾追加 `applyTranslations()` 调用
   - DOMContentLoaded 监听器调用 `applyTranslations()`
   - window.i18n 导出新增 `applyTranslations`

2. `web/board/static/project-detail.html` (修改)
   - 5 处 data-i18n 节点初始文本从中文改为 EN

3. `web/board/tests/test_aipos288_fix4_applier.py` (新建)
   - 完整的 applier 契约测试套件

## 链根状态

三轮"改了还漏"的唯一链根已修复：
- ❌ **修前**: data-i18n 机制不存在，HTML 声明 data-i18n 但 JS 从未应用 → 永远显示硬编码中文
- ✅ **修后**: applyTranslations 完整实现 + 三触发点覆盖 + 契约测试锁死 + EN 首帧无中文

**静态断言**: 
- 所有 data-i18n 引用的 key 在 zh/en 字典齐全（测试保证）
- HTML 初始文本为 EN 或空（测试保证）
- applier 机制存在且触发（测试保证）

**运行时保证**: 
- DOMContentLoaded 时首帧应用
- 语言切换时重新应用
- 动态内容可调用 `window.i18n.applyTranslations(容器)` 局部应用

## 能力账本自报

- **模型**: claude-sonnet-5
- **Token 输入**: 27,406
- **Token 输出**: ~2,500（估算）
- **耗时**: ~15 分钟
- **会话**: 单次冷启动完成，无断点

## 建议

无。实现完整，测试覆盖充分，链根已根除。
