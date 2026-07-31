# RETURN-FIX-3 — AIPOS-288F3

**任务卡**: AIPOS-288F3 (FIX-3: P0 整页崩溃修复+占位双语+API面契约测试)  
**执行人**: exec.lybra.kiwiai-dev  
**完成时间**: 2026-07-31  
**状态**: ✅ 完成

---

## 实际完成内容

按卡内 FIX-3.md 字面 a-d 顺序完成：

### a) API 错调修复（两处）
**病根**: `project-detail.html` L1063/L1112 调用 `i18n.currentLang()`，但 `i18n.js` 实际导出 `getCurrentLang()`，导致 P0 整页崩溃 `"i18n.currentLang is not a function"`。

**修复**:
- `project-detail.html` L1063: `i18nField()` 函数内改为 `i18n.getCurrentLang()`
- `project-detail.html` L1112: workers 阵容逻辑内改为 `i18n.getCurrentLang()`

**验证**: 全文件 grep 确认无第三处 `currentLang()` 调用。

### b) 占位符双语反向修正
**病根**: FIX-1 修反了方向，硬编码英文占位文本（如 `"Loading project detail..."`），但 i18n.js 默认语言是 `zh`（L529: `let currentLang = localStorage.getItem('lybra_lang') || 'zh'`），导致 zh 模式下加载闪现英文。

**修复**: 5 处占位符改为 zh 后备文本（与字典键一致）：
- `detail.loading`: `"Loading project detail..."` → `"加载项目详情..."`
- `onboarding.loading` (1处): `"Loading..."` → `"加载中..."`
- `tc.loading` (3处): `"Loading..."` → `"加载中..."`

**依据**: 卡内要求"禁硬编码任一语言"，后备文本必须匹配 `currentLang` 初始值（zh）。

### c) API 面契约测试
**目标**: 防止再次错调（静态扫描所有 `i18n.<method>(` 调用，断言 `method` 在导出清单内）。

**交付**: `tests/i18n-api-contract.test.js`（Node.js 脚本）
- 从 `i18n.js` 提取 `window.i18n = { ... }` 导出清单（`t`, `switchLanguage`, `getCurrentLang`, `createLanguageSwitcher`）
- 扫描 `web/board/static/` 下所有 `.js`/`.html` 文件
- 发现非法调用则退出码 1，打印违规位置

**测试结果**:
```
✅ All i18n.<method>() calls match exported API
   Verified 3 distinct method(s) across 204 call site(s)
```

### d) 双语烟测（JS 无崩溃断言）
**目标**: zh/en 两态下页面 JS 无 ReferenceError/TypeError 类崩溃。

**环境约束**: ubuntu26.04 不支持 Playwright 浏览器（`ERROR: Playwright does not support chromium on ubuntu26.04-x64`）。

**降级方案**（按"现有测试基建可达的最强形式"）：
- 静态验证: `tests/i18n-bilingual-stability.test.js`
  - 确认 `currentLang()` 全部改为 `getCurrentLang()`（✅ 3处）
  - 确认占位符 i18n 键存在于 zh/en 字典（✅ 3键）
  - 确认占位符后备文本匹配 zh 字典（✅ 全部通过）
- 文档化手工烟测步骤（启动服务器 → 浏览器控制台检查 → zh/en 切换无错）

**测试结果**:
```
✅ Static verification PASSED
   Manual smoke test required (documented above)
```

---

## 修改文件清单

### 产品代码
- `web/board/static/project-detail.html`（2 处 API 错调修复 + 5 处占位符后备文本修正）

### 测试代码（新增）
- `tests/i18n-api-contract.test.js`（API 面契约测试，204 调用点验证通过）
- `tests/i18n-bilingual-stability.test.js`（双语稳定性静态验证 + 手工烟测文档）

---

## 测试结果

### 自动化测试
```bash
# API 契约测试
$ node tests/i18n-api-contract.test.js
✅ All i18n.<method>() calls match exported API
   Verified 3 distinct method(s) across 204 call site(s)

# 双语稳定性静态验证
$ node tests/i18n-bilingual-stability.test.js
✅ Static verification PASSED
   1️⃣  getCurrentLang() API fixes: PASS (3 instances)
   2️⃣  Placeholder i18n keys exist: PASS (3 keys in zh/en)
   3️⃣  Placeholder fallback text: PASS (all match zh default)
   4️⃣  Manual smoke test documented (Playwright unavailable)
```

### 手工烟测（待 Owner 确认）
由于环境限制无法自动化，已文档化测试步骤：
1. 启动服务器: `cd ~/projects/lybra && python3 -m web.board.app`
2. 浏览器打开: `http://127.0.0.1:7117/project/lybra`
3. **ZH 模式**: 控制台无 ReferenceError/TypeError，加载占位显示"加载项目详情..."
4. **EN 模式**: `localStorage.setItem("lybra_lang", "en")` + 刷新，控制台无错，占位显示"Loading project detail..."
5. 断言: 无 `"i18n.currentLang is not a function"` 或类似崩溃

---

## 问题与限制

1. **环境约束**: ubuntu26.04 不支持 Playwright 浏览器安装，无法完成全自动端到端烟测。已降级为静态验证 + 文档化手工测试（符合卡内"按现有测试基建可达的最强形式"要求）。

2. **diff 噪音**: `git diff` 显示 `project-detail.html` 还有其他 i18n 相关改动（如 `data-i18n` 键补充），这些是之前修复（FIX-1/FIX-2）的残留，不在本卡 FIX-3 范围，但不影响本次修复目标。

3. **测试覆盖**: API 契约测试覆盖所有 `.js`/`.html` 静态调用点（204 处），但不覆盖运行时动态调用（如 `eval` 或字符串拼接）。现有代码库无此类模式。

---

## 交付物位置

- **产品修复**: `~/projects/lybra/web/board/static/project-detail.html`
- **测试代码**: `~/projects/lybra/tests/`
  - `i18n-api-contract.test.js`
  - `i18n-bilingual-stability.test.js`
- **本报告**: `~/projects/lybra/task_cards/AIPOS-288/RETURN-FIX-3.md`

---

## 模型与 Token 用量

- **模型**: `claude-sonnet-5` (via `$PI_MODEL`)
- **自报 token 用量**: 约 26,000 input tokens（冷启动读卡 + 代码读取 + 测试验证）
- **输出**: 约 4,000 tokens（代码修改 + 测试代码 + 本报告）

---

## 建议后续

1. **Owner 确认**: 按本报告"手工烟测"章节步骤，在真实浏览器中验证 zh/en 两态下页面无崩溃。
2. **CI 集成**: 将 `tests/i18n-api-contract.test.js` 和 `tests/i18n-bilingual-stability.test.js` 加入 CI 流程（静态验证部分可自动化）。
3. **环境升级**: 如需端到端烟测自动化，需迁移到支持 Playwright 的操作系统（如 ubuntu22.04/24.04）。

---

**执行者签名**: exec.lybra.kiwiai-dev  
**完成时间**: 2026-07-31T13:30:00Z
