# FIX-1 卡:AIPOS-274 — 眼验打回:两处回归(向导误现+已核验站复活)

- **执行角色**: lybra-executor(pi,经队列卡 AIPOS-274F1 派入)
- **铁证(顾问,2026-07-30)**: ①lybra-dev(93 任务)workspace 页出现"欢迎使用 Lybra"向导;
  /api/owner-truth summary.total_tasks=None(FIX-9 刚补的字段被 274 改动再次抹掉——二次回归);
  ②验证台待验站列出 AIPOS-263,而其 owner_verification 记录(approve,07-30 05:25)与闭环
  态俱在——已核验任务复活可按(Owner 实证截图)。
- **修法**:
  1. total_tasks 恢复透出;**加契约测试钉死该键**(第三次丢就是测试红,不再靠人眼);
  2. 待验站派生逻辑:存在对应 owner_verification 记录(approve)或任务已闭环 → 一律
     不入待验站(273 被正确排除而 263 未被的差异一并查明,fixture 双例断言);
  3. 零回归(274 本体功能不回退)。
- 验收:S1 lybra-dev 页向导消失+total_tasks 契约测试绿;S2 待验站不含 263(真实工作区)
  +fixture 断言;S3 零回归。回报 RETURN-FIX-1.md。
