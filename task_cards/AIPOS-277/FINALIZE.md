# FINALIZE 卡:AIPOS-277(+F1+F2)总览"+"新增项目 — 收编推产品仓

- **授权链**:AUDIT-REPORT-AIPOS-277R = PASS(F-277-1 P2 随卡记档);Owner 核验:
  网页打回 verify_AIPOS-277_20260731T065131(面板强制弹出)→F1(Esc 补齐)+F2(真因:
  CSS display:flex 覆盖 hidden,补 [hidden] 规则)→**Owner 会话确认通过(2026-07-31,
  附 onboarding 页截图;网页通过记录未落盘,按会话确认收编,advisor-proxy 口径)**。
- **范围(精确 pathspec)**:web/board/app.py, web/board/static/i18n.js,
  web/board/static/overview.css, web/board/static/overview.html,
  task_cards/AIPOS-277/(-f)
- 步骤:diff 抽查属 277 系→commit(授权链出处入信息)→push→FINALIZE-RETURN.md。
