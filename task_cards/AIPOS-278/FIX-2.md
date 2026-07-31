# FIX-2 卡:AIPOS-278 — 产品面统一命名 decision_log(Owner 网页打回)

- **铁证**:verify_AIPOS-278_20260731T091510(reject:"建议别改名字还叫decision_log")。
- **裁定落法(顾问已与 Owner 会话对齐)**:产品与 init 模板对外唯一名=
  `governance/decision_log/<YYYY-MM>/...`;解析器兼容读 decision_log/ 与
  direction_log/(旧工作区不炸,decision_log 优先);迁移工具默认目标名 decision_log/,
  文档同步;lybra-dev 自身的 direction_log/ 属历史遗留由顾问在治理仓注记别名,
  产品代码不特判。
- **断言**:S1 init 模板新项目生成 governance/decision_log/;S2 解析器双名兼容测试
  (仅 decision_log/仅 direction_log/两者并存=decision_log 优先);S3 迁移工具默认
  目标+--target-base 仍可覆盖;S4 全套测试绿,产品面 grep 无面向用户文案含
  direction_log。
- 车道:tools/aipos_cli/(project_map/migrate/workspace_templates)+docs;
  回报 RETURN-FIX-2.md。
