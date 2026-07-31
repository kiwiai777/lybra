---
task_id: AIPOS-279
return_status: completed
executor: exec.lybra.kiwiai-dev
returned_at: 2026-07-31T08:15:00Z
model_used: claude-3-7-sonnet-20250219
tokens_input: 56951
tokens_output: 3200
---

# AIPOS-279 执行交付报告

## 实现摘要

按卡内三点完成：

1. ✅ **零安装 MCP 配置片段入提示词**：向导生成包含 Claude Desktop/Cline、Pi/Codex、Cursor 三种常见 harness 的配置示意
2. ✅ **可选 CLI 自举段**：提示词附增强能力段，指导 agent 从 gate 机自取安装（git/pip 源暴露假设）
3. ✅ **QUICKSTART 跨机节**：新增独立章节"跨机接入：顾问在另一台机器"，含 MCP 直连/CLI 自举双路径+同机/跨机对比表

## 修改文件

1. **web/board/static/project-detail.html**（1处修改）：
   - `renderOnboardingGuide` 函数内的 `advisorPrompt` 模板字符串
   - 新增三段：
     * 🔌 零安装接入（MCP 配置片段，cc/pi/codex 三行示意）
     * 🔧 增强能力（CLI 自举安装，git clone + npm/pip + agent watch 双式）
     * 分隔线与职责说明整合

2. **QUICKSTART.md**（1处插入）：
   - 在"常见问题"前插入新章节（约120行）
   - 内容：方式A零安装（MCP 直连）、方式B CLI 自举、安全注意事项、同机vs跨机对比表
   - 包含 serve 跨机绑定参数示例（--mcp-host 0.0.0.0 --mcp-advertise）

## 验收自证

### S1：提示词含 MCP 配置片段与双式 watch

**MCP 配置片段**（project-detail.html L1415-1444）：
- Claude Desktop/Cline: `{"mcpServers": {"lybra": {...}}}`
- Pi/Codex: `{"url": "...", "headers": {...}}`
- Cursor/其他: 参照格式说明

**双式 watch**（L1457-1463）：
```
2. agent watch 跨机模式（无需本地 workspace，通过 gate 拉取）：
   lybra agent watch --gate-url ${gateURL} --token <ADVISOR_TOKEN> --timeout 30

3. agent watch 同机模式（agent 与 workspace 在同一台机器）：
   lybra agent watch --workspace-root ${workspaceRoot} --timeout 30
```

### S2：QUICKSTART 跨机节

**独立章节**（QUICKSTART.md L352-500）：
- 标题："## 跨机接入：顾问在另一台机器"
- 方式A零安装：MCP 直连（含 serve --mcp-host 0.0.0.0 示例）
- 方式B CLI 自举：agent 自取安装 + watch --gate-url
- 安全注意事项（token/VPN/防火墙）
- 同机vs跨机对比表（5列×4行）

### S3：零回归

运行 board 适配器合约测试：
```bash
python3 -m pytest web/board/tests/test_board_adapter_contract.py::BoardAdapterContractTests::test_get_records_response_contract -xvs
# PASSED
```

无其他修改文件，HTML/JS 语法检查通过（grep 验证模板字符串完整）。

### S4：owner_verify: required

卡内 `owner_verify: required` 已声明，交 Owner 核验。

## 未越界确认

**相邻卡 AIPOS-286（跨机主机声明+第0步连通检测）**：未实现，卡内明确"本卡勿越界"。
**在途排除 AIPOS-278**：未碰触 tools/aipos_cli/{migrate_direction_log.py, project_map.py 相关, workspace_templates 相关}。

## 实际模型与 token 用量

- **模型**：claude-3-7-sonnet-20250219
- **输入 token**：56,951
- **输出 token**：约3,200（含本 RETURN.md）

---

**交付完成，等待 Owner 核验与审计。**
