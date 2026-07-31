# FINALIZE 卡:AIPOS-278(+F1+F2)决策日志目录化 — 收编推产品仓

- **授权链**:AUDIT-REPORT-AIPOS-278R 两轮(Round1 FAIL→仲裁+顾问真迁移→Round2
  PASS_WITH_NOTES);Owner 网页打回(命名)→F2(decision_log 统一)→Owner 网页通过
  verify_AIPOS-278_20260731T092415(approve)。
- **范围(精确 pathspec,出卡实测)**:tools/aipos_cli/project_map.py,
  tools/aipos_cli/migrate_direction_log.py, tools/aipos_cli/DIRECTION_LOG_MIGRATION.md,
  tools/aipos_cli/tests/test_direction_log_migration.py,
  templates/blank/tree/governance/decision_log/, task_cards/AIPOS-278/(-f)
- **排除**:web/board/tests/test_aipos286_server_location.py(=286 系)。
- **红线(事故后新增)**:逐文件先读盘上当前版本再操作;只 git add 上列 pathspec。
- 步骤:diff 抽查→commit(含两轮审计+Owner 网页核验出处)→push→FINALIZE-RETURN.md。
