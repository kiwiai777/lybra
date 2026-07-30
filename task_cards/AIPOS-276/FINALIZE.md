# FINALIZE 卡:AIPOS-276(+FIX-1)地图防陈旧结构化 — 收编推产品仓

- **授权链**:AUDIT-REPORT-AIPOS-276R 两轮(Round1 FAIL→FIX-1→Round2 PASS);
  Owner 网页核验 verify_AIPOS-276_20260730T174134(approve;此前一轮打回=地图远期
  内容过期,已由顾问修账 5e918b8 处置)。v4 预授权,顾问签发。
- **范围(精确 pathspec,出卡实测,禁 add -A)**:
  tools/aipos_cli/draft_writer.py, tools/aipos_cli/project_map.py,
  web/board/static/project-detail.html, task_cards/AIPOS-276/(-f 因 gitignore)
- **步骤**:逐文件 diff 抽查属 276 系→commit(含两轮审计+Owner 核验出处)→push→
  写 FINALIZE-RETURN.md(hash+push 证据)。遇阻写 BLOCK 停。
