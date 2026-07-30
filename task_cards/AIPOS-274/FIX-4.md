# FIX-4 卡:AIPOS-274 — 微修:vb.* i18n 键裸奔(打回确认/取消/状态文案未翻译)

- **执行角色**: lybra-executor(pi,经队列卡 AIPOS-274F4 派入)
- **铁证**: Owner 截图——按钮/状态显示 vb.reject.confirm / vb.reject.cancel /
  vb.action.rejected / vb.success.rejected 等原始键。
- **修法(字面)**: vb.* 命名空间全部键补入 i18n 字典(zh/en 双语,人话:确认打回/取消/
  已打回/已通过等);全站扫一遍其他裸键(grep 断言:渲染文案零 vb.* 形态);测试断言。
- 验收:S1 打回流程全程人话文案;S2 zh/en 双语齐;S3 零回归。回报 RETURN-FIX-4.md。
