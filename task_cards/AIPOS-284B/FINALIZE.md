# FINALIZE 卡:AIPOS-284B watch --expect 修复 — 收编推产品仓

- **授权链**:AUDIT-REPORT-AIPOS-284BR = PASS_WITH_NOTES(F-284B-1 并案确认,单独走环);
  卡无 owner_verify。v4 预授权签发。
- **范围(精确 pathspec,出卡实测,禁 add -A)**:
  tools/aipos_cli/agent_watch_fs.py, tools/aipos_cli/tests/test_agent_watch_fs.py,
  docs/agent_watch_exit_codes.md, task_cards/AIPOS-284B/(-f 因 gitignore)
- **在途排除(禁入 commit)**:web/board/app.py, web/board/static/i18n.js,
  web/board/static/overview.css, web/board/static/overview.html(=AIPOS-277 候审)。
- **步骤**:diff 抽查属 284B→commit(含审计出处+F-284B-1 已知缺陷注记)→push→
  FINALIZE-RETURN.md(hash+push 证据)。遇阻写 BLOCK 停。
