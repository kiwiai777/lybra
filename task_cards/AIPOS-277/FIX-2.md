# FIX-2 卡:AIPOS-277 — 真因修复:CSS display:flex 覆盖 hidden 属性,面板永远可见

- **真因(顾问复核实锤)**: overview.css `.modal-mask{display:flex}` 无条件规则覆盖
  hidden 属性的 UA display:none → 面板常显、close 设 hidden 无效。FIX-1 的静态断言
  (grep 无裸调用/属性存在)全绿但真渲染坏——取证必须到渲染层。
- **修法(字面)**: overview.css 增 `.modal-mask[hidden]{display:none;}`(置于 .modal-mask
  规则之后);或等效"用类切换 display"方案,二选一,禁止内联 style。
- **断言(渲染层)**: 用 python + 简单 DOM/CSS 推演不可靠——上真验:启动本地 serve,
  curl 页面确认结构后,用无头断言(若环境无浏览器,则写死 CSS specificity 论证 +
  `[hidden]` 规则存在性断言,并在 RETURN 注明人工复核点)。零回归:打开/关闭三路仍好。
- 车道:web/board/static/overview.css(唯一);回报 RETURN-FIX-2.md。
