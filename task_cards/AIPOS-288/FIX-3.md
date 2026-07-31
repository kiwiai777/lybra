# FIX-3 卡:AIPOS-288 — P0:整页 JS 崩溃(currentLang API 错调)+占位反向

- **铁证**:Owner 截图 2026-07-31:"Failed to load: i18n.currentLang is not a function"
  整页瘫痪无法核验;且 zh 模式加载占位显示英文"Loading project detail..."。
- **病根(顾问已定位)**:project-detail.html L1063/L1112 调 i18n.currentLang(),
  i18n.js 实际导出 getCurrentLang()(L561);占位符 F1 修反了方向(硬编码英文)。
- **修法**:a) 两处改用 getCurrentLang()(全文件 grep 确认无第三处);
  b) 加载/初始占位一律 data-i18n 键(zh=加载中类,en=Loading 类),禁硬编码任一语言;
  c) **API 面契约测试**:静态扫描所有 `i18n.<name>(` 调用,断言 name 在 i18n.js
  导出面清单内(防再次错调);d) 双语烟测:zh/en 两态下页面 JS 无 ReferenceError/
  TypeError 类崩溃断言(按现有测试基建可达的最强形式)。
- 车道:web/board/static/;铁律:写前重读。回报 RETURN-FIX-3.md。
