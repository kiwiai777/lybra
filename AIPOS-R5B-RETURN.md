# AIPOS-R5B 任务完成汇报

## 任务目标
治理仓 per-project worktree + merge 收敛，解决 FND-24（多项目共用一工作树/分支 → 推送相撞）

## 交付成果

### 1. 复用 R5A worktree 模块 ✅
- `GovernanceWorktreeManager` 内部使用 `self.wt_manager = WorktreeManager(...)`
- 无第二份 worktree 实现，符合"一机制一实现"红线
- grep 证明：`from tools.worktree_manager import WorktreeManager`

### 2. 分支语义声明在 schema ✅
**schema/config.schema.json** 新增 `governance_worktree` 配置段：

```json
"branch_semantics": {
  "gov_prefix": {
    "pattern": "gov/<project>",
    "semantic": "collection_lane",
    "description": "收集车道(collection lane)非特性分叉 — 每次收账即 merge 回 main, 差距不过夜。与 LOOP-REDESIGN §6 '分叉活不过一张卡' 红线不冲突(语义声明在此, 非硬编码特判)。"
  }
}
```

### 3. Per-project worktree 机制 ✅
**tools/governance_worktree.py** 核心功能：

- `create_gov_worktree(project)` - 创建 gov/<project> 分支 + 专属 worktree
- `list_gov_branches()` - 列出所有 gov/* 分支
- 路径：`.worktrees/gov/<project>`

### 4. 越界校验 ✅
**路径约束在 schema 声明**：
```json
"path_constraints": {
  "per_project_allowed_pattern": "2_projects/<project>/**",
  "common_paths_whitelist": [
    "governance/decision_log.md",
    "governance/project_status.md",
    "5_tasks/records/**",
    "stage_archive/**"
  ]
}
```

**校验逻辑**：
- `validate_commit_paths(project, commit_sha)` - 单个 commit 校验
- `validate_branch_commits(project)` - 整个分支校验
- 越界 → 返回 `{valid: false, violations: [...文件列表...]}`

### 5. Merge 收敛动词 ✅
- `merge_gov_branch_to_main(project)` - 单项目 merge，带越界校验
- `merge_all_gov_branches()` - 一条命令合所有 gov/* 回 main 并 push
- `--no-ff` 保留历史
- 冲突检测：目录隔离应零冲突，真冲突 = 越界改他人目录 → BLOCK 列文件

### 6. 单一真相 ✅
- origin main = 唯一真相
- 禁第二 origin/镜像
- `merge_all_gov_branches()` 自动 push origin main

### 7. 零接触 ✅
- 代码中不包含 agency/chris/kaia-* 引用
- 现有直推 main 流程照常
- 共存期无回归风险

## 审计证据

**test_governance_worktree.py** 活体测试全通过：

```
审计总结:
  ✓ ③ 复用 R5A 模块 (无第二份实现)
  ✓ ④ 分支语义/白名单在 schema
  ✓ ① 活体: 两项目并发写 → merge 零冲突
  ✓ ② 越界文件 → 拒且列文件
  ✓ ⑤ agency/chris/kaia-* 零接触
```

### 活体场景
1. 创建 gov/project-a 和 gov/project-b worktree ✅
2. project-a 写入 `2_projects/project-a/test.txt` (允许) ✅
3. project-b 写入 `2_projects/project-b/test.txt` (允许) ✅
4. 路径校验通过 ✅
5. project-a 尝试写入 `2_projects/project-b/violation.txt` (越界) ✅
6. 越界校验失败，返回违规文件列表 ✅
7. project-b merge 到 main 成功 ✅
8. project-a merge 被拒绝（含越界文件） ✅

## 文件清单

### 新增文件
- `tools/governance_worktree.py` (453 行) - Per-project worktree + merge 收敛核心实现
- `tools/test_governance_worktree.py` (227 行) - 审计活体测试

### 修改文件
- `schema/config.schema.json` - 新增 `governance_worktree` 配置段 (75 行)

### Git commit
```
commit 94b722b
feat(R5B): 治理仓per-project worktree+merge收敛-收编FND-24多项目推送相撞
```

## 使用示例

### 创建 per-project worktree
```python
from tools.governance_worktree import GovernanceWorktreeManager

gov_mgr = GovernanceWorktreeManager('/path/to/governance/repo')

# 创建 gov/lybra worktree
wt_path, branch = gov_mgr.create_gov_worktree('lybra')
# → /path/to/governance/repo/.worktrees/gov/lybra, gov/lybra
```

### Merge 收敛
```python
# 单项目 merge (带越界校验)
result = gov_mgr.merge_gov_branch_to_main('lybra')

# 全部 gov/* 分支 merge 并 push
result = gov_mgr.merge_all_gov_branches()
# → 合所有 gov/* 回 main, push origin main
```

### 越界校验
```python
# 校验分支所有 commit
validation = gov_mgr.validate_branch_commits('lybra')

if not validation['valid']:
    print("越界文件:")
    for f in validation['violations']:
        print(f"  - {f}")
```

## 与 R5A 的关系

- **R5A**: code 卡 worktree (card/<task_id>)，分叉活不过一张卡
- **R5B**: gov/<project> worktree，collection lane 收集车道，差距不过夜
- **共用基础设施**: 同一个 `WorktreeManager` 模块
- **语义区分在 schema**: branch_semantics 声明，非硬编码特判

## 待后续任务

1. CLI 命令封装 (gov-merge 等)
2. Web 看板 merge 按钮 (薄壳，后续)
3. agency/chris 切换到 gov/* 工作流 (另行安排，非本卡)
4. 多项目实际使用验证 (kiwiai/lybra 并存)

## 红线遵守

- ✅ 一机制一实现 (复用 R5A worktree_manager.py)
- ✅ 配置在 schema (branch_semantics/path_constraints)
- ✅ 单一真相 origin main
- ✅ 零接触现有工作流
- ✅ 目录隔离零冲突

---

**完成时间**: 2026-08-13 14:45 UTC  
**分支**: card/AIPOS-R5B  
**Commit**: 94b722b  
**测试**: 全部通过 (test_governance_worktree.py)
