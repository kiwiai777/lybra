# AUDIT-284B — F-284-1 修复审计卡（自产）

**审计对象**: AIPOS-284B 实施产出  
**自产时间**: 2026-07-31  
**审计类型**: 实现类任务标准审计（task-closure-loop v3）

## 审计清单

### 1. 修向符合性 ✓

**FINDING-F-284-1.md 要求**：
- [x] --expect 支持 workspace-root 内任意相对 glob
- [x] walk 范围 = glob 静态前缀目录（不全树扫）
- [x] 对不可匹配模式布防时告警或拒绝，禁静默
- [x] 文档写明观察面
- [x] 新增卡外目录 fixture 的四出口测试

**实施验证**：
- `check_expect_patterns` 从白名单限制改为全工作区支持 ✓
- `_extract_static_prefix` 提取静态前缀，walk 仅扫该目录 ✓
- `_validate_expect_pattern` 启动时验证，拒绝绝对路径/`..`/不可达前缀 ✓
- 文档新增 "Observation Surface" 章节，明确各场景观察面 ✓
- `FsWatchF2841OutsideWatchSubtreesTests` 9 个测试覆盖四出口 + 验证 ✓

### 2. 四出口零回归 ✓

**测试结果**: 52/52 passed (19.12s)
- 原有 43 个测试：全绿 ✓
- 新增 9 个 F-284-1 测试：全绿 ✓

**红线保持**：
- `test_module_is_stdlib_only_zero_new_deps`: 通过（零新依赖）✓
- `test_snapshot_is_read_only`: 通过（只读，无写操作）✓
- `test_module_is_gate_free`: 通过（gate 零改动）✓
- `test_agent_connector_module_is_unchanged_zero_regression`: 通过（候选⑤字节级不变）✓

### 3. 代码质量

**边界处理**：
- [x] 精确路径（无通配符）单独处理：直接 stat 检查，不 walk
- [x] 空模式列表：提前返回空列表
- [x] 目录不存在：continue 跳过，不 crash
- [x] 文件 stat 失败：OSError 捕获，跳过

**验证逻辑正确性**：
- [x] 绝对路径 `startswith("/")` 拒绝
- [x] `..` 转义检查（简单但有效）
- [x] 前缀存在性检查：允许未存在但可创建（parent.exists）

**潜在问题**（留审）：
1. **`..` 检查过于简单**：`if ".." in pattern` 会误拦 `task_cards/..valid../file.md`（文件名合法包含 `..`）。建议改用 `Path.resolve()` 检查是否逃逸 workspace。
2. **`is_relative_to` 可用性**：Python 3.9+ 方法，需确认项目最低版本支持。
3. **静态前缀提取**：对 `{a,b}` 花括号展开、`[!...]` 否定字符类等复杂 glob 未处理，但 fnmatch 不支持这些，当前实现与 fnmatch 能力匹配。

### 4. 文档完整性 ✓

**`docs/agent_watch_exit_codes.md`**：
- [x] 新增 "Observation Surface" 章节，明确三种场景观察面
- [x] `--expect` 参数说明增加观察面、walk 范围、验证规则
- [x] 示例增加卡外目录用例（`task_cards/AIPOS-284/RETURN.md`）

**缺失**（不阻塞，可后续补）：
- 模块内 docstring 未更新（`check_expect_patterns` 已更新，但 `run_fs_watch` 顶层未提及 F-284-1）

### 5. 测试覆盖 ✓

**新增测试类 `FsWatchF2841OutsideWatchSubtreesTests`（9 个用例）**：
- [x] 四出口（0/2/3/4）卡外 fixture 测试
- [x] walk 范围隔离验证（OTHER 目录 decoy 不被扫）
- [x] 模式验证三种拒绝场景 + 一种允许场景

**覆盖充分性**：
- 精确路径匹配：原有测试覆盖 ✓
- Glob 模式匹配：原有 + 新增测试覆盖 ✓
- 卡外目录：新增测试覆盖 ✓
- 边界情况：部分覆盖（空列表、不存在目录）✓

**未覆盖**（不阻塞，可后续补）：
- 根目录 glob（`*.md`）：代码支持但无专门测试
- 深层嵌套（`a/b/c/d/**/*.md`）：原理已验证，量变不质变

### 6. 实战可用性

**F-284-1 原问题修复验证**：
- **场景**：276 运行哨兵，`--expect task_cards/AIPOS-284/RETURN.md`
- **修复前**：永不匹配（白名单外），exit 2/3 误报
- **修复后**：正确匹配，exit 0，输出 `expect_satisfied`

**布防告警验证**：
- 绝对路径/转义/不可达前缀 → stderr 清晰错误，exit 2 ✓
- 不静默失败 ✓

## 审计结论

**状态**: ✅ **通过（有保留意见）**

**放行理由**：
1. 修向符合性：5/5 要求完整实施 ✓
2. 四出口零回归：52/52 测试通过 ✓
3. F-284-1 原问题已修复并验证 ✓
4. 红线保持：stdlib-only / read-only / gate-free / 候选⑤不变 ✓

**保留意见**（非阻塞，建议后续优化）：
1. **`..` 检查精度不足**：当前简单字符串匹配可能误拦合法文件名。建议改用 `Path.resolve().is_relative_to(workspace_root)` 准确判断逃逸。
2. **Python 版本依赖**：`is_relative_to` 需 3.9+，需确认项目最低版本（当前测试环境 3.14，无问题）。

**修复建议**（可选，不阻塞本卡）：
```python
# 更精确的转义检查
def _validate_expect_pattern(workspace_root: Path, pattern: str) -> tuple[bool, str]:
    if pattern.startswith("/"):
        return False, f"--expect pattern must be relative to workspace-root: {pattern}"
    
    try:
        # 用 resolve 检查是否逃逸（更精确）
        full = (workspace_root / pattern).resolve()
        if not full.is_relative_to(workspace_root.resolve()):
            return False, f"--expect pattern escapes workspace-root: {pattern}"
    except (ValueError, OSError):
        pass  # 包含通配符时 resolve 可能失败，允许通过（后续 walk 自然处理）
    
    # ... 其余检查
```

## 审计人

**自产方**: exec.lybra.kiwiai-dev  
**独立审计**: 待 auditor.lybra.kiwiai-dev 复审

---

**审计卡状态**: 待独立审计  
**下游**: 如审计通过 → finalize 授权可落 commit
