# FINALIZE 卡:web 域四卡系联合收编(279+F1 / 286+F1F2F3 / 287 / 288+F1-F6)

- **授权链**:
  279:277R..279R PASS(Round2)+verify_AIPOS-279_20260731T095542(approve);
  286:verify_AIPOS-286_20260731T115832(approve);
  287:verify_AIPOS-287_20260731T102545(approve,自指验收);
  288:verify_AIPOS-288_20260731T140713(approve,六轮 FIX 链全案在卡目录)。
- **范围(精确 pathspec,出卡实测)**:QUICKSTART.md, tools/aipos_cli/project_map.py,
  tools/aipos_cli/verify_bench.py, web/board/app.py, web/board/auth_otc.py,
  web/board/static/i18n.js, web/board/static/overview.html,
  web/board/static/project-detail.html, tests/i18n-api-contract.test.js,
  tests/i18n-bilingual-stability.test.js, web/board/tests/test_aipos286_fix2_i18n_channel.py,
  web/board/tests/test_aipos286_server_location.py, web/board/tests/test_aipos287_audit_none_station.py,
  web/board/tests/test_aipos288_cjk_source_guard.py, web/board/tests/test_aipos288_fix4_applier.py,
  web/board/tests/test_aipos288_fix5_label_en.py, web/board/tests/test_aipos288_fix6_e2e.py,
  task_cards/AIPOS-279/ task_cards/AIPOS-286/ task_cards/AIPOS-287/ task_cards/AIPOS-288/(-f)
- **在途排除(禁入,=284D 执行中)**:tools/aipos_cli/agent_watch_fs.py,
  tools/aipos_cli/aipos_cli.py, tools/aipos_cli/tests/test_agent_watch_fs.py。
- 步骤:写前重读→diff 抽查四系归属→commit(信息含四链出处)→push→FINALIZE-RETURN.md。
  禁 add -A。
