# FINALIZE 卡:迁移门双卡联合收编(289 结案督察 + 292 审计壳产品化)

- **授权链**:289R PASS_WITH_NOTES+verify_AIPOS-289_20260801T051401(approve);
  292R PASS+verify_AIPOS-292_20260801T051359(approve)。收编即 kiwiaiagency 迁移门开
  (Owner 2026-08-01 裁定)。
- **范围(精确 pathspec,出卡实测)**:
  tools/aipos_cli/aipos_cli.py, tools/aipos_cli/board_adapter.py,
  tools/aipos_cli/record_writer.py, tools/aipos_cli/records.py,
  tools/aipos_cli/tests/test_queue_close.py, tools/aipos_cli/auditor_loop.py,
  tools/aipos_cli/tests/test_auditor_loop.py,
  config/deployment/lybra-auditor.example.service,
  templates/blank/tree/stage_archive/, templates/consulting-engagement/tree/stage_archive/,
  templates/software-development/tree/stage_archive/,
  task_cards/AIPOS-289/ task_cards/AIPOS-292/(-f)
- **排除**:lybra.egg-info/(pip 装置物,禁入;若产品 .gitignore 未含则顺手补一行并入 commit)。
- 步骤:写前重读→diff 抽查两系归属→commit(双链出处)→push→FINALIZE-RETURN.md。禁 add -A。
