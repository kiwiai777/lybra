# Lybra Project Structure File Schema

> AIPOS-293 — 存量项目上车:项目结构文件 schema

## 概述

Lybra 项目结构文件 (`lybra-project.yaml`) 是一个版本化的 YAML 文档，用于捕获一个工作区的完整结构元数据。它支持两个核心动词:

- **export**: 从现有工作区生成结构文件
- **import**: 按结构文件创建骨架工作区 + 迁移清单

## Schema (v1)

```yaml
# Lybra project structure file
# Schema version: 1
# Generated: 2026-08-01T00:00:00Z

schema_version: 1                  # 必填:int, 当前=1
project_name: my-project           # 必填:str, 非空
description: "项目描述（中文）"      # 可选:str, 从 README 首段提取
description_en: "Project desc"     # 可选:str
code_repos:                        # 可选:list[str], 代码仓库地址
  - "git@github.com:org/repo.git"
registered_at: "2026-01-01T00:00:00Z"  # 可选:str, ISO 时间
registered_by: advisor.lybra           # 可选:str
governance_files:                  # 可选:dict, 治理文件映射
  decision_log: governance/decision_log.md
  project_status: governance/project_status.md
  roadmap: governance/roadmap.md
  project_map: governance/project-map.md
roles:                             # 可选:list[dict], 角色声明
  - file: governance/AGENTS.md
    kind: role_charter
doc_manifest:                      # 可选:list[dict], 文档清单
  - source_path: governance/decision_log.md
    target_path: governance/decision_log.md
    kind: governance               # governance|task|archive|artifact|general
queue_summary:                     # 可选:dict, 队列统计
  pending: 5
  claimed: 2
  completed: 10
  blocked: 1
exported_at: "2026-08-01T00:00:00Z"    # export 自动生成
export_source: "/home/user/projects/my-project"  # export 自动生成
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `schema_version` | int | ✅ | Schema 版本号，当前只支持 `1` |
| `project_name` | str | ✅ | 项目名称，非空字符串 |
| `description` | str | — | 项目描述（中文优先），从 README.md 首段提取 |
| `description_en` | str | — | 英文描述 |
| `code_repos` | list[str] | — | 关联的代码仓库地址列表 |
| `registered_at` | str | — | 注册时间 (ISO 8601) |
| `registered_by` | str | — | 注册者 |
| `governance_files` | dict | — | 治理文件映射：canonical key → 相对路径 |
| `roles` | list[dict] | — | 角色声明列表，每项含 `file` 和 `kind` |
| `doc_manifest` | list[dict] | — | 文档清单，每项含 `source_path`/`target_path`/`kind` |
| `queue_summary` | dict | — | 队列状态统计 (pending/claimed/completed/blocked) |
| `exported_at` | str | — | export 自动生成的时间戳 |
| `export_source` | str | — | export 自动生成的源工作区路径 |

## 红线

1. **零凭据**: 结构文件绝不包含任何凭据值 (token/secret/password/api_key 等)
2. **import 不删文件**: import 只创建骨架和迁移清单，绝不删除用户文件
3. **非空保护**: import 拒绝在非空且非 Lybra 工作区的目录中执行
4. **幂等**: 重复 import 到同一目录是安全的 (跳过已存在文件)

## CLI 用法

```bash
# 导出
lybra project export [workspace_root] [--output FILE] [--project-name NAME]

# 导入
lybra project import <structure_file> <output_root> [--dry-run] [--actor NAME]

# 向导
# 在 Lybra Overview 面板 → 新建项目 → Option C: 导入已有项目
```

## 向导流程 (S4)

1. 用户在 Overview 面板点击「新增项目」
2. 选择 Option C: 导入已有项目
3. 输入现有工作区路径 → 点击「预览结构」
4. 查看项目名/文档数/治理文件等摘要
5. 输入新项目名称 → 点击「确认导入」
6. 系统自动创建骨架 + 迁移清单 + 注册到看板
