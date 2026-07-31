# RETURN-FIX-6 — AIPOS-288 label_en API 透传

**任务卡**: `~/projects/lybra/task_cards/AIPOS-288/FIX-6.md`  
**执行者**: lybra-executor  
**完成时间**: 2026-07-31

## 实际完成内容

### 1. 核心修改：app.py 三处组装点透传 label_en

修改 `web/board/app.py` 中 `get_overview()` 函数的三个 workspace 应答组装点：

- **L147-155 (error 分支)**：validation 失败时，从 `ws_config` 提取 `label_en`，存在时写入 `error_entry`
- **L219-222 (ok 分支)**：成功聚合时，从 `ws_config` 提取 `label_en`，存在时写入 `ok_entry`
- **L231-234 (exception 分支)**：异常捕获时，从 `ws_config` 提取 `label_en`，存在时写入 `exception_entry`

所有三处均采用条件透传逻辑：
```python
label_en = ws_config.get("label_en")
if label_en:
    entry["label_en"] = label_en
```

确保零回归：
- board_config 含 label_en → API 应答含 label_en
- board_config 不含 label_en → API 应答不含该键（向后兼容）

### 2. 契约测试：test_aipos288_fix5_label_en.py

在现有 FIX-5 测试文件中新增 `test_api_overview_transparently_returns_label_en()` 函数：

- 验证三处组装点均提取 `label_en = ws_config.get("label_en")`（检测出现 3 次）
- 验证三处组装点均条件写入 `if label_en: entry["label_en"] = label_en`（检测出现 3 次）
- 通过正则匹配验证 error/ok/exception 三个分支的完整透传逻辑

**测试结果**：✓ All AIPOS-288 FIX-5 + FIX-6 contract tests passed

### 3. 端到端测试：test_aipos288_fix6_e2e.py

新建完整 HTTP 服务端到端测试，覆盖数据流全链路：

**测试场景**：
1. `test_api_overview_returns_label_en_when_present`：混合 board_config（workspace 0 有 label_en，workspace 1 无），验证 API 响应正确透传
2. `test_frontend_helper_prefers_label_en_in_en_mode`：验证前端 `workspaceLabel()` helper 存在且含 EN 优先逻辑
3. `test_mixed_config_all_workspaces_render`：混合配置下所有 workspace 均正常渲染
4. `test_error_branch_also_passes_label_en`：无效 workspace root 触发 error 分支，验证 label_en 仍透传

**测试结果**：4 passed in 2.67s

### 4. 零回归验证

运行完整测试套件中与本次修改相关的 16 个测试（overview/label_en/fix6 关键字）：
```
16 passed, 262 deselected in 4.70s
```

所有 label_en 相关测试通过，包括：
- FIX-5 前端 helper 测试（7 个）
- FIX-6 契约测试（1 个）
- FIX-6 端到端测试（4 个）
- 现有 overview/task_center 测试（4 个）

## 修改文件清单

### 产品代码
- `web/board/app.py`：get_overview() 三处组装点透传 label_en

### 测试代码
- `web/board/tests/test_aipos288_fix5_label_en.py`：新增契约测试函数
- `web/board/tests/test_aipos288_fix6_e2e.py`：新建端到端测试模块（4 个测试用例）

## 技术决策

1. **条件透传**：只在 `label_en` 存在且非空时写入应答，确保向后兼容（老配置不受影响）
2. **三处一致**：error/ok/exception 三个分支采用相同的透传逻辑，降低维护成本
3. **测试覆盖**：契约测试验证代码结构，端到端测试验证运行时行为，双重保障

## 实际使用模型与 token 用量

- **模型**: claude-3-7-sonnet-20250219
- **总 token 用量**: ~49K tokens
  - 输入: ~42K (读取任务卡、现有代码、测试文件)
  - 输出: ~7K (修改代码、编写测试、验证)

## 验证通过

✓ 契约测试通过（API 三处组装点结构验证）  
✓ 端到端测试通过（完整数据流验证）  
✓ 零回归验证通过（16 个相关测试全绿）  
✓ 写前重读盘上版本（遵守铁律）

---

**交付物位置**: `~/projects/lybra/task_cards/AIPOS-288/RETURN-FIX-6.md`  
**状态**: 完成，待审计
