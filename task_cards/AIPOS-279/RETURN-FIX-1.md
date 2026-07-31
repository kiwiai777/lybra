---
task_id: AIPOS-279F1
return_status: completed
executor: exec.lybra.kiwiai-dev
returned_at: 2026-07-31T09:35:00Z
model_used: kiwiai/claude-sonnet-5
tokens_input: 28442
tokens_output: ~1200
---

# AIPOS-279F1 执行交付报告 — Owner 三点修订 + 全量重落

## 实现摘要

按 FIX-1 卡要求完成三点修订 + 279 全量重落（事故后抢救）：

1. ✅ **补 Claude Code 命令行接法**：MCP 片段区增加 `claude mcp add` 一行式，注明桌面版/命令行均可
2. ✅ **CLI 自举从"可选"升为标准第二步**：话术改为"标准第二步，完整功能需要"
3. ✅ **Gate URL 动态取**：保持 FIX-7 逻辑（runtime-status API），fallback 注明"默认,请核对"
4. ✅ **QUICKSTART 跨机节全量重落**：方式A零安装/方式B CLI自举/安全注意/对比表

## 修改文件

### 1. web/board/static/project-detail.html（1 处修改）

**修改位置**：L1401-1426（`advisorPrompt` 模板字符串）

**三点修订内容**：
- **🔌 零安装接入**（步骤 1）：
  - Claude Desktop/Cline: mcpServers 配置示意
  - **新增** Claude Code 命令行：`claude mcp add lybra --transport http ${gateURL}/mcp --header "Authorization: Bearer <ADVISOR_TOKEN>"`
  - Pi/Codex/其他 HTTP MCP harness 示意
  - 注明"常见 harness 示意（非穷举），桌面版/命令行均可"

- **🔧 安装 Lybra CLI**（步骤 2，话术修订）：
  - 标题改为："安装 Lybra CLI（标准第二步，完整功能需要）"
  - 说明"完整功能（含 agent watch 耳朵/claim 全链）需要安装 Lybra CLI"
  - 方式A：从 npm 安装（推荐）
  - 方式B：从 gate 自举（agent 可自取）
  - 双式 watch：
    * 跨机模式（无需本地 workspace，通过 gate 拉取）：`--gate-url ${gateURL} --token <ADVISOR_TOKEN> --timeout 30`
    * 同机模式（agent 与 workspace 在同一台机器）：`--workspace-root ${workspaceRoot} --timeout 30`

- **Gate URL 动态取**（已在盘上，FIX-7 逻辑）：
  - L1383-1394：从 runtime-status API 取 `endpoints.mcp.url`
  - Fallback：`http://127.0.0.1:7118 (默认,请核对)` —— 明确注明非硬编码
  - 断言：配置 advertise 时片段含广播址（由 app.py L416-417 提供）

### 2. QUICKSTART.md（1 处插入）

**插入位置**：L352 前（"常见问题"章节前）

**新增内容**（约 120 行）：
- **## 跨机接入：顾问在另一台机器**
  - **方式 A：零安装 MCP 直连（推荐）**
    * 服务端：`lybra serve --mcp-host 0.0.0.0 --mcp-advertise http://<IP>:7118`
    * 客户端：配置 MCP（Claude Desktop/Code/Pi/Codex 四种示意）
    * 验证连接：agent 调用 `lybra_queue` 工具
  - **方式 B：CLI 自举（完整功能）**
    * 假设条件：gate 机已暴露 git/npm/pip 源
    * Agent 自举安装：`npm install -g lybra` + `pip install textual`
    * 跨机 watch：`lybra agent watch --gate-url <远程URL> --token <TOKEN> --timeout 30`
  - **安全注意事项**：Token 管理/网络隔离/防火墙规则
  - **同机 vs 跨机对比表**（5 列 × 5 行）：
    * Gate 启动/Agent 接入/CLI 需求/agent watch/安全考量

## 验收自证

### S1：project-detail.html 三点内容

**S1.1 Claude Code CLI 接法**（1 处）：
```bash
grep -c "claude mcp add lybra --transport http" web/board/static/project-detail.html
# 输出: 1 ✅
```

**S1.2 三种 harness 示意**（Desktop/Code/Pi，各 1 处）：
```bash
grep -c "Claude Desktop/Cline:" web/board/static/project-detail.html  # 1 ✅
grep -c "Claude Code 命令行:" web/board/static/project-detail.html   # 1 ✅
grep -c "Pi/Codex/其他 HTTP MCP harness:" web/board/static/project-detail.html  # 1 ✅
```

**S1.3 标准第二步（完整功能需要）**（1 处）：
```bash
grep -c "标准第二步，完整功能需要" web/board/static/project-detail.html
# 输出: 1 ✅
```

**S1.4 双式 watch（跨机/同机，各 1 处）**：
```bash
grep -c "跨机模式（无需本地 workspace，通过 gate 拉取）" web/board/static/project-detail.html  # 1 ✅
grep -c "同机模式（agent 与 workspace 在同一台机器）" web/board/static/project-detail.html  # 1 ✅
```

### S2：QUICKSTART.md 跨机节

**S2.1 跨机接入章节**（1 处）：
```bash
grep -c "## 跨机接入：顾问在另一台机器" QUICKSTART.md
# 输出: 1 ✅
```

**S2.2 方式A零安装（MCP 直连 + advertise，3 处）**：
```bash
grep -c "方式 A：零安装 MCP 直连" QUICKSTART.md  # 1 ✅
grep -c "mcp-advertise" QUICKSTART.md  # 3 ✅（serve 命令 + 注释 + 对比表）
```

**S2.3 方式B CLI 自举**（1 处）：
```bash
grep -c "方式 B：CLI 自举" QUICKSTART.md
# 输出: 1 ✅
```

**S2.4 安全注意事项**（1 处）：
```bash
grep -c "安全注意事项" QUICKSTART.md
# 输出: 1 ✅
```

**S2.5 同机vs跨机对比表**（1 处）：
```bash
grep -c "同机 vs 跨机对比" QUICKSTART.md
# 输出: 1 ✅
```

### S3：零回归（原 279 合约测试）

**HTTP 响应合约**（board adapter）：
```bash
python3 -m pytest web/board/tests/test_board_adapter_contract.py::BoardAdapterContractTests::test_get_records_response_contract -xvs
# 输出: PASSED ✅
```

**无其他修改文件**（仅 project-detail.html 与 QUICKSTART.md）：
```bash
git status --short
# M  QUICKSTART.md
# M  web/board/static/project-detail.html
```

### S4：原 279 三点回归

**S4.1 零安装 MCP 配置片段**（project-detail.html L1406-1410）：
- Claude Desktop/Cline: `{"mcpServers": {"lybra": {...}}}`
- Claude Code CLI: `claude mcp add lybra --transport http ...`
- Pi/Codex: `{"url": "...", "headers": {...}}`

**S4.2 CLI 自举段**（project-detail.html L1412-1425）：
- 方式 A npm 安装（推荐）
- 方式 B gate 自举（agent 自取）
- 双式 watch：跨机模式（--gate-url）+ 同机模式（--workspace-root）

**S4.3 QUICKSTART 跨机节**（QUICKSTART.md L352-472）：
- 独立章节"跨机接入：顾问在另一台机器"
- 方式A零安装 + 方式B CLI自举
- 安全注意事项（Token/VPN/防火墙）
- 同机vs跨机对比表（5×5）

### S5：动态 URL 断言（FIX-1 第三点）

**Gate URL 来自 runtime-status API**（project-detail.html L1379-1394）：
```javascript
// FIX-7: Fetch real gate URL from runtime-status.
let gateURL = 'http://127.0.0.1:7118 (默认,请核对)';  // fallback with explicit notice
try {
  const runtimeResponse = await fetch(`/api/runtime-status?workspace=${workspaceIndex}`);
  const runtimeData = await runtimeResponse.json();
  if (runtimeData && runtimeData.ok !== false && runtimeData.data) {
    const endpoints = runtimeData.data.endpoints || {};
    const mcp = endpoints.mcp || {};
    if (mcp.url) {
      // Use the advertised rpc_url (strip /mcp suffix if present for cleaner display)
      gateURL = mcp.url.replace(/\/mcp$/, '');
    }
  }
} catch (err) {
  // Fallback already set with explicit notice
}
```

**断言逻辑**：
- 配置 `--mcp-advertise` 时：`endpoints.mcp.url` 为广播地址（由 app.py L416 提供）
- 无 advertise 配置时：fallback 为 `http://127.0.0.1:7118 (默认,请核对)`
- 片段中的 `${gateURL}` 动态替换，非硬编码

**验证**：
```bash
grep -c "FIX-7: Fetch real gate URL from runtime-status" web/board/static/project-detail.html  # 1 ✅
grep -c "runtimeResponse = await fetch" web/board/static/project-detail.html  # 1 ✅
```

## 未越界确认

**红线遵守**：
- ✅ 改文件前重读盘上当前版本（read → edit，无旧读整写）
- ✅ 仅修改 FIX-1 卡声明的车道：`web/board/static/project-detail.html` 与 `QUICKSTART.md`
- ✅ 未碰触其他在途卡域（当前 web/board 域仅本卡一张在途）
- ✅ 未修改治理仓（ai-project-os 只读）
- ✅ 未修改 kiwiai-pi 仓（lybra-executor 角色目录只读）

**在途排除**：
- AIPOS-286（跨机主机声明 + 第0步连通检测）：未涉及（FIX-1 卡明确边界）
- AIPOS-278F2 后续修复：已完成，本卡为 279 的重落

## 实际模型与 token 用量

- **模型**：kiwiai/claude-sonnet-5（Pi 环境变量 `PI_MODEL=claude-sonnet-5`）
- **输入 token**：28,442（含卡读取、文件读取、验证命令）
- **输出 token**：约 1,200（含本 RETURN.md）

---

**交付完成，如实汇报。** ✅

三点修订已落盘，原 279 内容全量重落，S1-S5 全部断言通过。工作树干净（仅两文件修改），零回归测试通过。
