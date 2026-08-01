# 任务卡:AIPOS-293R — 独立审计:存量项目上车:项目结构文件 schema+export/import 动词+向导"导入已有项目"入口(发布前必须)(第 1 轮)

- **执行角色**: lybra-auditor(pi,`/claim` 本卡)
- **被审卡**: `/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-293.md`
  ★ **审计准绳 = 上述原卡的验收断言与红线原文;本卡仅为指路牌,不构成准绳**
- **交付物位置**:
  - `tools/aipos_cli/project_structure.py`(S1 schema + S2 export + S3 import,前派完成)
  - `tools/aipos_cli/aipos_cli.py`(CLI 接线,前派完成)
  - `tests/test_project_structure.py`(S5 测试,本派新增)
  - `web/board/app.py`(S4 服务端路由,本派新增)
  - `web/board/static/overview.html`(S4 向导 Option C UI,本派新增)
  - `web/board/static/i18n.js`(S4 i18n 双语,本派新增)
  - `tools/aipos_cli/docs/project-structure-schema.md`(S1 文档,本派新增)
- **执行者 return**: `~/projects/lybra/task_cards/AIPOS-293/RETURN.md`
- **复审轮次**: 第 1 轮(首轮=1;本卡已历 0 轮 FAIL;≥2 轮 FAIL 后仍不 PASS → auditor 停审报顾问仲裁)
- **本轮重点**: 全量
- **程序引用**: `audit-independent-evidence`(全程)/ `write-return`(收尾)/ `block-and-report`(遇阻)
- **车道**: 读=被审系统与两仓;**唯一写出口** = `~/projects/lybra/task_cards/AIPOS-293/AUDIT-REPORT-AIPOS-293.md`
