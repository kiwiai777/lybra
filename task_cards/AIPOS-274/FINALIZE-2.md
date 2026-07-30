# FINALIZE-2 卡:AIPOS-274 收编补遗 —— 顾问 pathspec 漏列 3 文件

- **性质**:FINALIZE.md 的 pathspec 漏列了 274 系列在 tools/aipos_cli/ 的后端半边
  (顾问出卡失误,执行体按卡排除属正确行为)。本卡补齐,授权链同 FINALIZE.md。
- **执行角色**: lybra-executor(pi,经队列卡 AIPOS-274FZ2 派入)
- **范围(精确 pathspec,禁 add -A)**:
  - `tools/aipos_cli/records.py`(274F1 owner_verification 记录读取)
  - `tools/aipos_cli/verify_bench.py`(274 人话清单/274F1 退站/274F2 summary 镜像)
  - `tools/aipos_cli/draft_validator.py`(274 可选人话字段)
  - `task_cards/AIPOS-274/FINALIZE-2.md` 与本轮 RETURN
- **步骤**:1)git diff 三文件确认全部改动均为 AIPOS-274* 注记范围;发现非 274 改动
  即写 BLOCK 停;2)commit(信息注明"274 收编补遗:顾问 pathspec 漏列");3)push;
  4)写 FINALIZE2-RETURN.md(hash+push 证据)。
