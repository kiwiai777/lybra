# FIX-6 卡:AIPOS-288 — label_en 服务端透传(FIX-5 漏了 API 半边,顾问已定位到行)

- **铁证**:verify_AIPOS-288_20260731T135722(同处仍中文)。顾问定位:app.py 组装
  workspace 应答仅取 label/root(L138 起,组装点约 L147/L205/L218),label_en 服务端
  即被丢弃,前端 helper(已就位)拿不到。
- **修法**:上述全部组装点透传 label_en(ws_config.get("label_en") 存在才带);
  契约测试:board_config 含 label_en 时 API 应答含之;不含时应答无该键或空,零回归;
  已有前端测试补一条端到端(fixture config→API→helper 选择)。
- 车道:web/board/app.py+tests;铁律:写前重读。回报 RETURN-FIX-6.md。
