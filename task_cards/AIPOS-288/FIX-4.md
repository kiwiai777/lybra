# FIX-4 卡:AIPOS-288 — 链根修复:data-i18n 机制从未实现(只活在注释里)

- **铁证(顾问定位)**:i18n.js 注释宣称支持 data-i18n="key",但全文件无
  querySelectorAll('[data-i18n]')/无 apply 函数——机制不存在;凡依赖 data-i18n 的
  元素永远显示 HTML 硬编码中文。此为三轮"改了还漏"的唯一链根
  (Owner 打回 verify_AIPOS-288_20260731T132133 及前两轮同因)。
- **修法**:
  a) i18n.js 实现 applyTranslations(root=document):扫 [data-i18n] 置 textContent=
     t(key);另支持 [data-i18n-attr="placeholder:key"] 类属性形式(如现有用到);
  b) 触发点:DOMContentLoaded 一次 + setLang() 内每次切换后 + 暴露给动态插入节点
     的调用点(渲染函数插入含 data-i18n 的节点后调 applyTranslations(容器));
  c) 契约测试:i18n.js 含 applier 实现断言;全站每个 data-i18n 引用的 key 在
     zh/en 两字典齐(缺=红);
  d) 复核:EN 首帧含"加载项目详情"场景=0(静态可断言:HTML 硬编码文本允许保留为
     no-JS 后备,但 apply 时机保证 JS 可用时首帧即替换)。
- 车道:web/board/static/;铁律:写前重读。回报 RETURN-FIX-4.md。
