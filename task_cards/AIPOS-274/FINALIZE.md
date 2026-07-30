# FINALIZE 卡:AIPOS-274(+274B)核验体验 P1 —— 收编推产品仓

- **执行角色**: lybra-executor(pi,经队列卡 AIPOS-274FZ 派入)
- **授权链**:审计 AUDIT-REPORT-AIPOS-274R.md = PASS_WITH_NOTES;Owner 网页核验
  records/owner_verifications/AIPOS-274/verify_AIPOS-274_20260730T135201.md
  (decision: approve, decided_via: web_session)——v4 预授权,顾问签发本卡。
- **范围(精确 pathspec,禁 add -A)**:
  - `web/`(274 主体 + B + FIX-1..4:人话清单/预览/按钮/站卫生/envelope 对齐/
    按钮外置/vb.* i18n)
  - `tools/aipos_cli/owner_truth_view.py`(total_tasks 修复)
  - `web/board/tests/`(含 HTTP 层契约测试)
  - `task_cards/AIPOS-274/`(全部工件)
- **归拢**:杂目录 `task_cards/AIPOS-274F2/` 的 RETURN-FIX-2 移入
  `task_cards/AIPOS-274/RETURN-FIX-2.md`(如未在)后删除杂目录,一并入 commit。
- **步骤**:1)git status 核对车道外无夹带;2)归拢;3)commit(信息含 274/274B/F1-F4
  系列摘要+审计+Owner 网页核验出处);4)push 产品仓;5)写 FINALIZE-RETURN.md
  (含 commit hash + push 证据原文)。
- 红线:只动上述 pathspec;在途排除:无(275 未开工)。遇阻写 BLOCK 即停。
