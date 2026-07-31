# FIX-2 卡:AIPOS-288 — 门户/地图声明字段双语化(数据层的正规解法)

- **铁证**:Owner 网页打回 2026-07-31(288 仅剩门户声明卡区中文)。该区文字源自
  project-map.md(portal.description/collab_mode/topology/workers 注记/advisor 注记、
  current、milestones[].title)=声明数据,硬译越界,正解=schema 双语。
- **修法**:
  a) project-map schema 增可选 `_en` 变体字段:portal.description_en/collab_mode_en/
     topology_en/advisor_note_en、workers 条目注记 _en、current_en、
     milestones[].title_en(向后兼容:无 _en 字段照旧原文);
  b) 板面渲染 EN 模式优先取 _en,缺则原文回退(不留空);zh 模式一律原文;
  c) 解析器+HTTP 契约测试:双语字段选择逻辑各一断言;旧地图(无 _en)零回归;
  d) 文档:project-map 模板注明 _en 用法一行。
- **分工**:产品侧=本卡;lybra-dev 地图英文内容由顾问随后补(治理仓,非本卡车道)。
- 车道:web/board/+tools/aipos_cli/project_map.py;铁律:写前重读。回报 RETURN-FIX-2.md。
