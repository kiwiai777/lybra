# FIX-3 卡:AIPOS-286 — 全产品文字标准语言切换扫尾(Owner 打回)

- **铁证**:Owner 2026-07-31 会话+网页打回:欢迎页(🎉欢迎使用 Lybra/三步走)、
  跨机接入提醒橙框、向导三步各区块说明,切英文后仍中文;裁定=**全产品化文字
  都走标准语言切换**。另:截图现英文文案 bug "You are the Advisor for the
  unnamed workspace workspace."(重复词+未取工作区名)。
- **修法**:
  a) 全页扫尾:onboarding 向导(欢迎头/步骤卡/跨机提醒/闭环链路图文字)、
     总览新增项目面板、以及 grep 全 static+app.py 兜底扫出的一切硬编码中文
     用户可见串,全部入 i18n 通道(F2 建的服务端模板或前端字典,按各自渲染层归位);
  b) 修 "unnamed workspace workspace":取真实工作区名,缺省文案不重复 workspace;
  c) **全页级硬断言**:en 模式渲染整页(向导页/总览/项目详情)CJK 零残留
     (白名单:记录原文/任务卡内容/专名);游离文案侦测基线收紧到零豁免或列明清单;
  d) zh 模式与现状等价。
- 车道:web/board/;铁律:写前重读盘上版本。回报 RETURN-FIX-3.md。
