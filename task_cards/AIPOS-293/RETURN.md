# RETURN — AIPOS-293

## 一句话结论

**完成**。S1-S5 全部交付:S1 schema + 文档,S2 export,S3 import,S4 向导 Option C + i18n 双语,S5 测试 73 项全过。接续前派半成品,未推倒重来。

## 做了什么

1. **读现状**:确认前派已完成 S1(schema 常量/validate/emit/parse/credential scan)、S2(export + CLI 接线)、S3(import + 非空保护/幂等/迁移清单 + CLI 接线)
2. **S5 测试**:写 `tests/test_project_structure.py`,10 组测试 73 项断言——schema 校验/凭据安全/往返/非空保护/幂等/YAML roundtrip/迁移清单/契约/辅助函数/真实 lybra 导出
3. **S4 向导 Option C**:
   - `web/board/app.py`:新增 `/api/project-structure/preview` + `/api/project-structure/import` 两个 POST 路由
   - `web/board/static/overview.html`:在新增项目 modal 加 Option C(输入路径→预览结构→确认导入)
   - `web/board/static/i18n.js`:zh-CN + en 双语各 13 条新 key
4. **S1 文档**:写 `tools/aipos_cli/docs/project-structure-schema.md`(schema 一页文档)
5. **实测**:对 lybra 自身跑 export→import dry-run,1017 文档/162KB YAML/零凭据

## 改动清单

| 文件 | 性质 |
|------|------|
| `tests/test_project_structure.py` | **新增** — S5 测试套件(73 项断言) |
| `web/board/app.py` | **追加** — 2 个 POST 路由(preview + import) |
| `web/board/static/overview.html` | **追加** — Option C HTML + JS + i18n 接线 |
| `web/board/static/i18n.js` | **追加** — zh/en 各 13 条 i18n key |
| `tools/aipos_cli/docs/project-structure-schema.md` | **新增** — schema 一页文档 |
| `task_cards/AIPOS-293/RETURN.md` | **新增** — 本回报 |
| `task_cards/AIPOS-293/AUDIT-AIPOS-293.md` | **新增** — 自产审计卡 |

## 测试/验证结果原文

```
$ python3 tests/test_project_structure.py
============================================================
AIPOS-293: Project Structure Schema + Export/Import Tests
============================================================

[T1] Schema validation
[T2] Credential safety (zero credential values)
[T3] Export-import roundtrip
[T4] Non-empty directory protection
[T5] Idempotent re-import
[T6] YAML emit-parse roundtrip
[T7] Migration checklist generation
[T8] Contract test (return shape)
[T9] _dir_is_empty_or_absent helper
[T10] Export on real lybra workspace
  SKIP: no project.json at repo root

Results: 73 passed, 0 failed
============================================================
```

真实 lybra 导出实测:
```
$ python3 -c "from tools.aipos_cli.project_structure import export_project_to_yaml; ..."
{
  "ok": true,
  "output_path": "/tmp/lybra-project.yaml",
  "yaml_byte_size": 162842,
  "doc_count": 1017
}
```

真实 lybra 导入 dry-run:
```
{
  "ok": true,
  "dry_run": true,
  "project_name": "lybra",
  "planned_files": ["project.json", ".lybra/.gitignore", ".lybra/config.json", "governance/decision_log.md", "migration-checklist.md", "README.md"],
  "migration_item_count": 1017,
  "verdict": "PASS"
}
```

凭据扫描:
```
$ grep -i "token\|secret\|password\|api_key\|private_key" /tmp/lybra-project.yaml
(仅文件路径名中出现,如 capability_token_scope.md;无实际凭据值)
```

## 排除物 + 理由

- **未 commit/push**:卡未授权 finalize
- **未改 `project_structure.py`**:前派实现完整,无需修改
- **未改 `aipos_cli.py`**:前派 CLI 接线完整,无需修改
- **T10 skip**:lybra 根目录无 `project.json`,export 仍能工作(用目录名作项目名)

## 异常与自作判断

- 无偏离卡内 S1-S5 范围
- Option C 的向导流程按卡内描述:①输入路径②预览(=export 生成器起草结构文件)③确认(=import 落地)
- 向导文案走 i18n 双语(与 AIPOS-288 通道一致)

## 实际使用的模型 + 自报 token 用量

```
model=kiwiai/qwen3.7-plus, tokens≈in:80k/out:25k (估计)
```

(注:pi 底栏模型名 = `qwen3.7-plus`,provider = `kiwiai`)

## 待办 / 移交

- Owner 验收:向导 Option C 实际 UI 演示(需启动 board server)
- Owner 验收:对 lybra 跑一次完整 export→import→上看板
- 审计:自产审计卡已就位

下一棒:auditor 跑 → /claim /home/kiwi/projects/lybra/task_cards/AIPOS-293/AUDIT-AIPOS-293.md
