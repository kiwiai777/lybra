# RETURN — AIPOS-284B

**任务卡**: AIPOS-284B  
**执行者**: exec.lybra.kiwiai-dev  
**状态**: 完成  
**完成时间**: 2026-07-31

## 执行摘要

按 FINDING-F-284-1.md 修向段落，完成 `agent watch --expect` 任意工作区 glob 支持 + 不可匹配模式布防告警 + 文档观察面说明 + 卡外目录 fixture 四出口测试。四出口零回归（52/52 测试通过）。

## 实施内容

### 1. 核心修复：`agent_watch_fs.py`

**新增函数**：
- `_extract_static_prefix(pattern)`: 提取 glob 模式的静态前缀目录（通配符之前的部分），用于限定 walk 范围
- `_validate_expect_pattern(workspace_root, pattern)`: 布防时验证模式合法性，拒绝：
  - 绝对路径（`/...`）
  - `..` 转义
  - 静态前缀不存在且无法创建的模式

**修改 `check_expect_patterns`**（85 行修改）：
- **观察面扩展**：从仅扫描 `_WATCH_SUBTREES`（`5_tasks/queue`, `5_tasks/records`）改为支持整个 workspace-root 内的任意相对 glob
- **walk 范围优化**：限定为 glob 静态前缀目录，避免全树扫描
  - 示例：`task_cards/AIPOS-284/*.md` → 仅 walk `task_cards/AIPOS-284/`
  - 精确路径（无通配符）直接 stat 检查，不 walk
- **零性能回归**：白名单内模式（`5_tasks/**`）walk 范围不变

**修改 `run_fs_watch`**（8 行新增）：
- 启动时对所有 `--expect` 模式调用 `_validate_expect_pattern`
- 不合法模式打印错误到 stderr，返回 EXIT_TIMEOUT（exit 2）

### 2. 文档更新：`docs/agent_watch_exit_codes.md`

新增 **"Observation Surface"** 章节（20 行），明确说明：
- **Diff detection**（`changed` 输出）：仅 `5_tasks/queue/**` + `5_tasks/records/**`
- **Expect matching**（`expect_satisfied` 输出）：整个 workspace-root，walk 范围 = 静态前缀
- **Stall detection**：with run-log 看 run-log mtime；without run-log 看 queue+records

更新 `--expect` 参数说明：
- 增加观察面范围和 walk 范围说明
- 增加模式验证规则说明
- 增加卡外目录使用示例（`task_cards/AIPOS-284/RETURN.md`）

### 3. 测试新增：`tests/test_agent_watch_fs.py`

**新增测试类 `FsWatchF2841OutsideWatchSubtreesTests`**（172 行，9 个测试用例）：

**四出口测试**（卡外目录 `task_cards/` fixture）：
1. `test_expect_match_outside_watch_subtrees_exit0`: exit 0，expect 满足
2. `test_expect_timeout_outside_watch_subtrees_exit2`: exit 2，超时无匹配
3. `test_expect_end_no_product_outside_watch_subtrees_exit3`: exit 3，结束无产物
4. `test_expect_stall_outside_watch_subtrees_exit4`: exit 4，静默停滞

**walk 范围验证**：
5. `test_expect_glob_walk_scope_is_static_prefix`: 验证 walk 仅限静态前缀，不扫全树

**模式验证测试**：
6. `test_expect_pattern_validation_rejects_absolute_path`: 拒绝绝对路径
7. `test_expect_pattern_validation_rejects_dotdot_escape`: 拒绝 `..` 转义
8. `test_expect_pattern_validation_rejects_nonexistent_unreachable_prefix`: 拒绝无法创建的前缀
9. `test_expect_pattern_validation_allows_nonexistent_but_creatable_prefix`: 允许尚未存在但可创建的前缀

## 测试结果

```bash
$ python3 -m pytest tools/aipos_cli/tests/test_agent_watch_fs.py -v
============================= 52 passed in 19.12s ==============================
```

**零回归**：
- 43 个原有测试全部通过（v1 diff/snapshot/loop/signal/CLI/红线/v2 四出口）
- 9 个新增 F-284-1 测试全部通过

**红线验证通过**：
- `test_module_is_stdlib_only_zero_new_deps`: 零新依赖
- `test_snapshot_is_read_only`: 只读，无写操作
- `test_module_is_gate_free`: gate 零改动
- `test_agent_connector_module_is_unchanged_zero_regression`: 候选⑤模块字节级不变

## 文件变更

```
 docs/agent_watch_exit_codes.md               |  20 +++-
 tools/aipos_cli/agent_watch_fs.py            |  85 ++++++++++++--
 tools/aipos_cli/tests/test_agent_watch_fs.py | 172 ++++++++++++++++++++++
 3 files changed, 267 insertions(+), 10 deletions(-)
```

**修改文件**：
- `tools/aipos_cli/agent_watch_fs.py`: 核心修复
- `tools/aipos_cli/tests/test_agent_watch_fs.py`: 新增 F-284-1 测试套件
- `docs/agent_watch_exit_codes.md`: 观察面文档

**无修改**：
- `tools/aipos_cli/agent_connector.py`: 候选⑤字节级不变（零回归验证通过）
- `tools/aipos_cli/aipos_cli.py`: CLI 入口无需改动（`--expect` 参数已存在）

## 修复验证

**F-284-1 问题复现与修复**：
- **修复前**：`--expect task_cards/AIPOS-284/RETURN.md` 永不匹配（仅扫描白名单）
- **修复后**：正确匹配，exit 0，输出 `{"expect_satisfied": ["task_cards/AIPOS-284/RETURN.md"]}`

**布防时告警**：
- 绝对路径 `/absolute/path/file.md` → stderr 错误，exit 2
- 转义路径 `../escape/file.md` → stderr 错误，exit 2
- 不可达前缀 `blocker.txt/impossible/*.md`（blocker.txt 是文件） → stderr 错误，exit 2

**walk 范围优化验证**：
- `task_cards/AIPOS-284/*.md` 仅 walk `task_cards/AIPOS-284/`，不触碰 `task_cards/OTHER/`
- 测试用 OTHER 目录 decoy 文件验证隔离性

## 实际使用的模型与 token 用量（自报）

**模型**: Claude 3.5 Sonnet (Anthropic)  
**Token 用量估算**:
- Input: ~42,000 tokens（读取代码、测试、文档、任务卡）
- Output: ~4,500 tokens（代码修改、测试编写、文档更新、本 RETURN）
- **Total**: ~46,500 tokens

## 下一步

自产审计卡已就位 `task_cards/AIPOS-284B/AUDIT-284B.md`，等待 auditor 独立审计。
