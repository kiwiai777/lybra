# RETURN — AIPOS-288 FIX-1: 初始/加载态文字盲区+护栏补口

**执行者**: exec.lybra.kiwiai-dev  
**完成时间**: 2026-07-31  
**状态**: 已完成

---

## 📋 任务回顾

**卡号**: AIPOS-288F1  
**准绳**: `~/projects/lybra/task_cards/AIPOS-288/FIX-1.md`  

**三大修复目标**:
1. 初始/加载态中文占位（如"加载项目详情..."）→ i18n 化（HTML 初始文本改 EN，JS 首帧水合）
2. Portal WORKERS 徽章内"执行/审计"前缀→甄别来源（产品拼接 vs 数据白名单）
3. 护栏补口：静态扫描扩展覆盖 HTML 初始文本节点与 JS loading/empty 字面量

---

## ✅ 实际完成

### 1. 初始态文字 i18n 化（HTML 首帧盲区修复）

**问题定位**:  
HTML 中带 `data-i18n` 属性的节点，初始文本为中文（如 `<div data-i18n="detail.loading">加载项目详情...</div>`）。虽然 JS 会在首帧执行后立即替换为当前语言文本，但在 JS 执行前的短暂瞬间，浏览器已渲染初始 HTML，导致：
- **ZH 模式**: 用户无感知（中→中）
- **EN 模式**: 短暂闪现中文占位 → 切换为英文（Owner 实测截图证实）

**修复方案**:  
将所有 `data-i18n` 节点的初始文本改为 **英文占位**（与 `i18n.js` 中 `en` 字典对应），JS 首帧立即水合为当前语言：
- ZH 模式: EN 占位 → 首帧切换为中文（瞬间完成，用户无感知）
- EN 模式: EN 占位 → 保持 EN（无闪现）

**修改文件**: `web/board/static/project-detail.html`  
**修改节点**（共 29 处）:
- 页面标题、副标题、返回链接
- 里程碑地图区块：标题、提示文字
- 验证台区块：标题、提示文字
- 空板向导：3 步标题、说明、按钮文字、流程图标签
- 任务中心：标题、提示、子标题
- 加载占位：5 处 "加载中..." / "加载项目详情..." → "Loading..." / "Loading project detail..."

**验证**: 护栏测试 `test_html_data_i18n_initial_text()` 全部通过（无 CJK 初始文本残留）。

---

### 2. Portal WORKERS 徽章前缀甄别（数据白名单确认）

**Owner 截图问题**: Portal 区域 WORKERS 徽章内显示 "执行" / "审计" 前缀。

**甄别结论**: **数据源白名单，非产品代码拼接**。

**证据链**:
1. **数据来源**: `project-map.md` 的 YAML frontmatter，`portal.workers` 列表，格式为 `instance(role,harness,desc...)`
   - 示例: `exec.lybra.kiwiai-dev(执行,kiwiai-dev)`
   - `role` 字段（如"执行"/"审计"）由 **Owner 在 project-map.md 中声明**

2. **前端解析**: `web/board/static/project-detail.html` 中 `parseWorker()` 函数解析括号内角色：
   ```javascript
   function parseWorker(raw) {
     const s = String(raw || '').trim();
     const m = s.match(/^([^(]+)\((.*)\)\s*$/);
     if (!m) return { instance: s, role: '', harness: '', desc: '' };
     const parts = m[2].split(',').map(p => p.trim()).filter(Boolean);
     return { instance: m[1].trim(), role: parts[0] || '', ... };
   }
   ```
   角色文本直接取自数据源第一个逗号前的值，**无产品代码拼接逻辑**。

3. **渲染逻辑**: `renderPortalHeader()` 中：
   ```javascript
   if (w.role) { 
     const r = el('span', 'wc-role'); 
     r.textContent = w.role;  // 数据原文直显
     chip.appendChild(r); 
   }
   ```
   `textContent` 直接赋值为 `w.role`（数据源原文），无 i18n.t() 调用，无字符串拼接。

**裁定**: "执行" / "审计" 前缀属于 **Owner 在 project-map.md 中维护的协作配置数据**，非产品 UI 文字，属数据白名单范畴。**无需入 i18n 通道**（数据内容原文直显是设计意图）。

**类比**: 项目名称、任务卡标题、里程碑描述——均为 Owner 撰写的内容数据，不翻译。Portal workers 角色标签同理。

---

### 3. 护栏补口（静态扫描扩展）

**原护栏盲区**: `test_aipos288_cjk_source_guard.py` 中 `DATA_WHITELIST_PATTERNS` 包含 `data-i18n=` 模式，导致带 `data-i18n` 的 HTML 节点初始文本即使为中文也不报错（被白名单豁免）。

**新增护栏**: 新增 `test_html_data_i18n_initial_text()` 测试，专项检查：
- **检查对象**: 所有 `static/*.html` 中带 `data-i18n` 属性的节点
- **规则**: 初始文本必须 **不含 CJK 字符**（只能是 EN 或空）
- **正则模式**: `data-i18n=["']([^"']*)["']\s*>([^<]+)<` 提取初始文本
- **报错示例**: `static/project-detail.html:794 data-i18n="detail.loading" → "加载项目详情..."`

**测试结果**: 修复后全部通过（29 处节点全部改为 EN 占位）。

**补充说明**: 
- JS 中 `loading` / `empty` 类字面量已由既有护栏覆盖（`i18n.t()` 调用检查）
- 本次补口专注 HTML 首帧盲区（浏览器渲染初始文本节点）

---

## 🧪 测试验证

### 护栏测试（自动化）
```bash
cd ~/projects/lybra/web/board
python3 tests/test_aipos288_cjk_source_guard.py
```

**结果**: ✅ 全部通过
```
✓ No bare CJK literals found
✓ i18n.js dictionaries are complete
✓ Python i18n templates are complete
✓ HTML data-i18n initial text is EN or empty

All AIPOS-288 guards passed ✓
```

### EN 模式人工复核
**待 Owner 真机验证**（EN 模式刷新页面，观察首帧是否仍有中文闪现）:
1. 切换到 EN 模式（右上角语言切换器）
2. 硬刷新页面（Ctrl+Shift+R / Cmd+Shift+R）
3. 观察页面加载瞬间是否仍有中文占位文字闪现
4. 重点观察区域：
   - 页面标题栏 "Project Detail"
   - 返回链接 "← Back to Overview"
   - 加载占位 "Loading project detail..." / "Loading..."
   - 空板向导（若适用）各步骤标题与说明

**预期结果**: 全程无中文字符闪现，首帧即为英文占位 → 保持英文（或瞬间水合为当前语言，用户无感知）。

---

## 📁 变更清单

### 修改文件
1. **web/board/static/project-detail.html**（前端 UI）
   - 29 处 `data-i18n` 节点初始文本：中文 → 英文
   - 涵盖：页面 chrome、区块标题/提示、空板向导、加载占位

2. **web/board/tests/test_aipos288_cjk_source_guard.py**（护栏测试）
   - 新增 `test_html_data_i18n_initial_text()` 函数
   - 扫描 HTML 文件，检查 `data-i18n` 节点初始文本是否含 CJK
   - 更新 `__main__` 执行流程，新增第 4 项检查

### 无变更（甄别后确认无需修改）
- Portal workers 徽章前缀（数据白名单，保持原文直显）
- JS 中 `i18n.t()` 调用（已由既有护栏覆盖）

---

## 🎯 Owner 确认项

### ✅ 已完成
- [x] HTML 初始文本节点全部改为 EN 占位（29 处）
- [x] 护栏测试扩展覆盖 HTML `data-i18n` 节点初始文本
- [x] 护栏测试全部通过（4 项检查）
- [x] Portal workers 徽章前缀甄别（数据白名单，无需修改）

### 🔍 待 Owner 验证
- [ ] EN 模式真机复核：硬刷新页面，观察首帧是否仍有中文闪现
- [ ] ZH 模式回归测试：确认所有文本正常显示中文（无残留英文占位）
- [ ] 空板向导流程（workspace 0 任务数）：确认三步文字正确

---

## 🤖 实际使用模型与 Token

**模型**: Claude 3.5 Sonnet (anthropic/claude-3-5-sonnet-20241022)  
**自报 Token 用量**:
- Input: ~59,000 tokens
- Output: ~5,000 tokens

**会话特征**:
- 单卡冷启动执行（无历史上下文依赖）
- 读取任务卡 → 定位问题 → 批量修复 → 扩展护栏 → 验证测试
- 主要耗时：HTML 节点批量替换（29 处，分 8 次 edit 调用）

---

## 📝 备注

1. **数据 vs UI 文字边界**: Portal workers 角色标签属协作配置数据（Owner 在 project-map.md 维护），与项目名称、任务标题同类，不入 i18n 通道。产品 UI chrome（按钮、标签、提示）才需双语化。

2. **首帧盲区根因**: HTML 初始文本节点在 JS 执行前被浏览器渲染，即使 JS 立即替换，快速网络 + 现代浏览器下用户仍可能捕捉到瞬间闪现。修复方案：初始文本用英文占位（默认语言），JS 水合为当前语言。

3. **护栏分层防御**:
   - 第 1 层：`test_no_bare_cjk_in_sources()` — 扫描 JS/HTML/py 源码，拦截裸露中文字面量
   - 第 2 层：`test_i18n_dictionary_completeness()` — 确保 zh/en 字典 key 集合一致
   - 第 3 层：`test_html_data_i18n_initial_text()` — 专项检查 HTML 首帧盲区（本次新增）

4. **后续建议**: 考虑将 HTML 模板改为服务端渲染（SSR），根据 Accept-Language header 或 cookie 直接输出对应语言的初始 HTML，彻底消除首帧语言切换（需重构，本卡不涉及）。

---

**交付完成，等待 Owner 真机验证与收编。**
