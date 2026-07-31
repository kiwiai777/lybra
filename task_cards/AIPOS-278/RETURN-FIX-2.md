# RETURN: AIPOS-278 FIX-2 — 产品面统一命名 decision_log

**任务卡**: `~/projects/lybra/task_cards/AIPOS-278/FIX-2.md`  
**执行者**: lybra-executor (kiwiai-dev)  
**完成时间**: 2026-07-31T17:30Z

---

## 执行结果

**状态**: ✅ 完成  
**断言**: S1-S4 全部满足

### S1: init 模板生成 decision_log/ ✅

- **操作**: 重命名 `templates/blank/tree/governance/direction_log/` → `decision_log/`
- **验证**: 目录存在，旧名已移除
- **影响**: 新项目执行 `lybra workspace init` 时生成 `governance/decision_log/`

### S2: 解析器双名兼容读（decision_log 优先）✅

- **修改文件**: `tools/aipos_cli/project_map.py`
- **实现逻辑**:
  ```python
  # 优先读 decision_log/，不存在则回退到 direction_log/
  dl_dir = governance_dir / "decision_log"
  if not dl_dir.is_dir():
      dl_dir = governance_dir / "direction_log"
  ```
- **验证**:
  - 同时存在两目录 → 读 decision_log/ 内容（优先级测试通过）
  - 仅 direction_log/ → 正常读取（向后兼容测试通过）
  - 仅 decision_log/ → 正常读取
  - 两者都不存在 → 返回空列表（graceful）

### S3: 迁移工具默认目标 decision_log/ ✅

- **修改文件**: `tools/aipos_cli/migrate_direction_log.py`
- **变更**:
  - 文件头注释：`governance/decision_log/` 为默认目标
  - `--target-base` 帮助文本：提到 `decision_log`
  - 默认逻辑：从源文件路径推断 `governance/`，目标为 `governance/decision_log/`
  - `--target-base` 参数仍可覆盖（保留灵活性）
- **验证**: `--help` 输出包含 `decision_log` 字样

### S4: 测试绿 + 文档清理 ✅

- **测试结果**:
  ```
  test_workspace_templates.py: 12 passed, 3 subtests passed
  test_direction_log_migration.py: 10 passed
  test_board_adapter.py: 24 passed
  ```
  总计 46 个相关测试全部通过。

- **兼容性测试**（手动验证）:
  - decision_log 优先读取 ✅
  - direction_log 回退兼容 ✅
  - 双目录并存时 decision_log 胜出 ✅
  - 空目录优雅处理 ✅

- **文档清理**:
  - 面向用户文档（`docs/v1_disclosure.md` 等）原本引用的是 `decision_log`，无需修改
  - 内部代码注释更新为 `decision_log`（迁移工具 docstring）
  - 模板目录说明文件更新（`.gitkeep` 内容从 "Direction Log" → "Decision Log"）

---

## 修改清单

### 产品代码

1. **tools/aipos_cli/project_map.py** (修改)
   - 新增常量 `DECISION_LOG_REL = "governance/decision_log"`
   - 保留 `DIRECTION_LOG_REL` 作为向后兼容别名
   - `_read_direction_log_recent()` 实现双名兼容读（decision_log 优先）

2. **tools/aipos_cli/migrate_direction_log.py** (新建)
   - 迁移工具：单月文件 → 目录结构
   - 默认目标：`governance/decision_log/`
   - `--target-base` 可覆盖
   - 用户可见字符串统一为 `decision_log`

3. **tools/aipos_cli/tests/test_direction_log_migration.py** (新建)
   - 10 个测试覆盖解析、迁移、新旧结构读取

4. **tools/aipos_cli/DIRECTION_LOG_MIGRATION.md** (新建)
   - 迁移工具使用文档

### 模板

5. **templates/blank/tree/governance/decision_log/** (重命名自 direction_log/)
   - 目录结构：`decision_log/.gitkeep`
   - `.gitkeep` 内容更新：标题、示例路径、说明文字统一为 `decision_log`

---

## 测试输出摘要

```
=== S1: 验证 init 模板生成 decision_log/ ===
✓ decision_log/ 目录存在
✓ 旧 direction_log/ 已移除

=== S2: 验证解析器双名兼容（decision_log 优先）===
✓ decision_log 优先读取
✓ 兼容 direction_log 读取

=== S3: 验证迁移工具默认目标 decision_log/ ===
✓ 迁移工具帮助文本提到 decision_log

=== S4: 验证测试套件全绿 ===
46 passed, 3 subtests passed in 0.19s

=== 全部验证通过 ===
```

---

## 向后兼容性保证

1. **现有工作区不受影响**：
   - 旧工作区的 `governance/direction_log/` 继续正常读取
   - 解析器自动回退，无需手动迁移

2. **新旧混用场景**：
   - 同时存在两目录时，`decision_log/` 优先
   - 防止冲突，提供明确行为

3. **迁移路径清晰**：
   - `migrate_direction_log.py` 工具可选使用
   - 默认目标 `decision_log/`，`--target-base` 灵活覆盖
   - 原文件重命名为 `.archived`，零数据丢失

---

## 遵守的约束

- ✅ 车道严守：仅修改 `tools/aipos_cli/` 与 `templates/blank/`
- ✅ 在途避让：未碰 `web/`（任务 279/286 在途）
- ✅ 只读治理仓：未写入 `~/ai-project-os`
- ✅ 测试前置：所有修改通过测试验证
- ✅ 断言对账：S1-S4 逐条核验

---

## 实际使用模型与 token

**模型**: Claude 3.5 Sonnet (anthropic/claude-3-5-sonnet-20241022)  
**Token 用量**（自报）:
- Input: ~36,700 tokens
- Output: ~2,800 tokens
- Total: ~39,500 tokens

（注：实际用量以会话日志为准，此处为执行期间观察值）

---

## 备注

- 迁移工具 `migrate_direction_log.py` 为新建文件（git 未追踪），建议后续正式收编时 `git add`
- 测试文件 `test_direction_log_migration.py` 同为新建，已覆盖新旧结构解析
- 产品文档（`docs/v1_disclosure.md` 等）原本就是 `decision_log`，无需改动
- Owner 裁定的"双名兼容读"已实现，旧工作区零迁移负担
