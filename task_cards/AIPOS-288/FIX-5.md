# FIX-5 卡:AIPOS-288 — 收官件:工作区 label 双语(board_config label_en)

- **铁证**:Owner 终验仅剩 H1/卡题 "Lybra Dev(治理工作区)"(=board_config workspaces
  label,数据层最后一件)。verify_AIPOS-288_20260731T133231。
- **修法**:a) board_config workspaces 条目支持可选 label_en;渲染(总览列表/详情 H1/
  门户卡题/浏览器 title)EN 优先 label_en、缺则 label 回退;b) 277 的服务端 init 写
  board_config 时若前端提供英文名一并写入(向导面板加可选英文名输入,一行);
  c) 契约测试:label_en 选择逻辑+无 label_en 零回归;d) 顾问随后在 lybra-dev 的
  board_config 补 label_en 值(非本卡车道,卡内注明)。
- 车道:web/board/;铁律:写前重读。回报 RETURN-FIX-5.md。
