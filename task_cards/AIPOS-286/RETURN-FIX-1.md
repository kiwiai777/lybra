---
task_id: AIPOS-286F1
return_status: completed
executor: exec.lybra.kiwiai-dev
returned_at: 2026-07-31T09:50:00Z
model_used: kiwiai/claude-sonnet-5
tokens_input: 32907
tokens_output: ~2500
---

# AIPOS-286F1 执行交付报告 — 跨机感知全量重落（事故恢复）

## 实现摘要

按 FIX-1 卡要求完成 286 原卡 S1-S4 全量重落（278F2 事故后抢救）：

1. ✅ **S1: runtime-status API 注入服务端主机名 + IP**（app.py 三处修改）
2. ✅ **S2: 提示词新增第 0 步**（同机确认 + 连通性检测，不通即 block-and-report）
3. ✅ **S3: 向导页 step-1 加 SSH 连通性提醒**（橙色警示框）
4. ✅ **S4: i18n 双语**（zh/en）+ HTTP 契约测试 8 条全过

**铁律遵守**：
- ✅ 改文件前重读盘上当前版本（read → edit，无旧读整写）
- ✅ 279F1 内容完整保留（MCP 片段/CLI 自举段/QUICKSTART 跨机节）
- ✅ 仅 web/ 域一张在途卡（本卡），无冲突

## 改动文件清单

### 后端 (Python)

**1. web/board/app.py**（3 处修改）
- L7: 添加 `import socket`
- L684 前：新增 `_get_server_location_info()` 辅助函数
  * 获取 `socket.gethostname()` + 首选出口 IP（dummy socket connect）
  * 优雅降级：任一失败返回 `None`
  * 返回结构：`{hostname, ip, note}` 或 `None`
- L706（`_get_runtime_status_route` 函数内）：注入 `data["server_location"]` 字段

**API 响应示例**（实测本机）：
```json
{
  "ok": true,
  "data": {
    "server_location": {
      "hostname": "kiwi-dev",
      "ip": "192.168.1.123",
      "note": "AIPOS-286: Advisor agents should verify same-machine before connecting..."
    },
    "endpoints": {...},
    ...
  }
}
```

### 前端 (HTML + JS)

**2. web/board/static/project-detail.html**（4 处修改）

**修改 1**: L1379-1397（`renderOnboardingGuide` 函数）
- 从 runtime-status API 提取 `server_location` (hostname + IP)
- 新增变量：`serverHostname`, `serverIP`（默认 "(未获取)"）

**修改 2**: L1410-1480（`advisorPrompt` 模板字符串）
- 新增「Lybra 服务端位置（AIPOS-286）」段：
  ```
  Lybra 服务端位置（AIPOS-286）：
  - 主机名：${serverHostname}
  - IP 地址：${serverIP}
  ```
- 新增「第 0 步：同机确认与连通性检测（AIPOS-286 强制前置）」区块（约 50 行）：
  * **1. 确认同机/跨机**：对比 `hostname` 和 `hostname -I` 与服务端信息
  * **2. 连通性检测**：
    - 方式 A — HTTP 健康检查（推荐）：`curl -v ${gateURL}/health`
    - 方式 B — 文件真相面检测（需 SSH）：`ls ${workspaceRoot}/5_tasks/queue`
  * **不通过怎么办**：立即停止，block-and-report 给 Owner，说明位置/现象/需要 SSH 配置
  * **绝不带病接线**：连不通 gate 时强行配置 MCP 会导致后续所有操作静默失败
  * **3. 通过后再继续**：进入下方「零安装接入」配置 MCP 连接
- 第 0 步放在「零安装接入」段之前（顺序前置）
- 保留 279F1 的 MCP 片段（Claude Desktop/Code/Pi）与 CLI 自举段（标准第二步）

**修改 3**: L841-847（step-1 向导区块）
- 新增 `#ssh-reminder` 橙色警示框（在介绍段落与提示词代码块之间）：
  ```html
  <p id="ssh-reminder" style="margin-top: 0.75rem; padding: 0.75rem; 
     background: rgba(217,119,6,0.1); border-left: 3px solid #d97706; 
     border-radius: 4px; font-size: 13px; line-height: 1.6;">
    <strong data-i18n="onboarding.ssh_reminder">⚠️ 跨机接入提醒：</strong>
    <span data-i18n="onboarding.ssh_reminder_text">如果你的顾问 agent 与 Lybra 服务端不在同一台机器，请先配置好 SSH 连通性（或网络路由），确保 agent 能访问服务端后再继续。提示词中包含第 0 步检查指引。</span>
  </p>
  ```

**修改 4**: L837, L841, L847（step-1 HTML 元素）
- 标题/正文/按钮添加 `data-i18n` 属性（支持语言切换）

### 前端 (i18n)

**3. web/board/static/i18n.js**（2 处修改）

**修改 1**: zh 翻译块（L217-218 前插入）
```javascript
// Onboarding guide (AIPOS-286)
'onboarding.step1_title': '连接你的顾问 Agent',
'onboarding.step1_body': '复制下方定制接入提示词，粘贴给 Claude / Codex / 任意 AI agent...',
'onboarding.copy_prompt': '📋 一键复制接入提示词',
'onboarding.ssh_reminder': '⚠️ 跨机接入提醒：',
'onboarding.ssh_reminder_text': '如果你的顾问 agent 与 Lybra 服务端不在同一台机器，请先配置好 SSH 连通性（或网络路由），确保 agent 能访问服务端后再继续。提示词中包含第 0 步检查指引。',
```

**修改 2**: en 翻译块（L431-432 前插入）
```javascript
// Onboarding guide (AIPOS-286)
'onboarding.step1_title': 'Connect Your Advisor Agent',
'onboarding.step1_body': 'Copy the customized onboarding prompt below and paste it to Claude / Codex / any AI agent...',
'onboarding.copy_prompt': '📋 Copy Onboarding Prompt',
'onboarding.ssh_reminder': '⚠️ Cross-Machine Setup:',
'onboarding.ssh_reminder_text': 'If your advisor agent and Lybra server are on different machines, please configure SSH connectivity (or network routing) first to ensure the agent can reach the server before proceeding. The prompt includes step-0 connectivity checks.',
```

### 测试

**4. web/board/tests/test_aipos286_server_location.py**（新建，8 条契约测试）
- `test_get_server_location_info_returns_hostname_and_ip` — 辅助函数返回结构断言
- `test_runtime_status_includes_server_location` — API 响应包含 server_location 字段
- `test_runtime_status_graceful_fallback_on_location_failure` — 检测失败时优雅降级
- `test_advisor_prompt_structure_includes_server_info` — 提示词模板包含服务端位置变量与第 0 步
- `test_i18n_includes_ssh_reminder_translations` — i18n 包含双语 SSH 提醒
- `test_prompt_includes_gate_url_from_runtime_status` — Gate URL 从 API 取得（非硬编码）
- `test_step_0_before_mcp_config` — 第 0 步在 MCP 配置段之前（顺序断言）
- `test_ssh_reminder_on_advisor_connection_step` — SSH 提醒在 step-1 区块内

## 契约测试结果

```bash
$ python3 -m pytest web/board/tests/test_aipos286_server_location.py -v

test_get_server_location_info_returns_hostname_and_ip PASSED
test_runtime_status_includes_server_location PASSED
test_runtime_status_graceful_fallback_on_location_failure PASSED
test_advisor_prompt_structure_includes_server_info PASSED
test_i18n_includes_ssh_reminder_translations PASSED
test_prompt_includes_gate_url_from_runtime_status PASSED
test_step_0_before_mcp_config PASSED
test_ssh_reminder_on_advisor_connection_step PASSED

============================== 8 passed in 0.08s ==============================
```

**覆盖范围**:
- 后端 API 契约（server_location 字段存在性、结构、优雅降级）
- 前端提示词模板（服务端变量、第 0 步内容、顺序）
- 前端 UI 元素（SSH 提醒位置、i18n 标记）
- i18n 完整性（中英翻译键存在性、内容断言）

## 回归测试

```bash
$ python3 -m pytest web/board/tests/ -q

231 passed, 3 failed in 39.29s
```

**3 个失败与 286 原 RETURN 一致**（已存在问题）：
1. `test_local_read_api.py::test_governance_route_handles_missing_files_as_warn`
2. `test_local_read_api.py::test_governance_route_reads_lybra_project_docs_without_writing`
3. `test_project_map_and_verify_bench.py::test_project_map_schema_and_nested_parse`

**核心测试全过**:
- `test_board_adapter_contract.py` — 11 passed（adapter 接口契约）
- `test_aipos286_server_location.py` — 8 passed（本卡契约）

## 279F1 内容保留验证

**已确认 279F1 改动完整保留**（禁碰承诺兑现）：
```bash
grep -c "claude mcp add lybra" project-detail.html               # 1 ✅
grep -c "标准第二步，完整功能需要" project-detail.html          # 1 ✅
grep -c "## 跨机接入：顾问在另一台机器" QUICKSTART.md           # 1 ✅
```

**接缝点**（project-detail.html L1379-1480）：
- 279F1 落地：MCP 片段（Claude Desktop/Code/Pi）+ CLI 自举段（跨机/同机 watch）
- 286F1 落地：服务端位置注入（L1379-1397）+ 第 0 步区块（L1410-1480）
- **顺序**：服务端位置 → 第 0 步 → 零安装接入（279F1 MCP 片段）→ 安装 CLI（279F1 自举段）
- **无冲突**：286 在提示词开头注入，279 的 MCP/CLI 段落完整保留

## 工作树状态

```bash
$ git status --short
 M QUICKSTART.md
 M web/board/app.py
 M web/board/static/i18n.js
 M web/board/static/project-detail.html
?? web/board/tests/test_aipos286_server_location.py
```

**说明**：
- `QUICKSTART.md` 修改为 279F1 落地（跨机节），286 不涉及
- 其余 4 个文件为 286F1 改动
- 无意外修改，车道干净

## 实际使用的模型与 token

- **模型**: `kiwiai/claude-sonnet-5`（Pi 环境变量 `PI_MODEL=claude-sonnet-5`）
- **输入 tokens**: 32,907（含卡读取、文件读取、验证命令）
- **输出 tokens**: 约 2,500（含代码生成、测试编写、本报告）

## 未越界确认

**红线遵守**：
- ✅ 改文件前重读盘上当前版本（read → edit，无旧读整写）
- ✅ 仅修改卡声明车道：`web/board/` 域（app.py, static/project-detail.html, static/i18n.js, tests/）
- ✅ 279F1 内容完整保留（MCP 片段/CLI 自举段/QUICKSTART 跨机节）
- ✅ 未碰触治理仓（ai-project-os 只读）
- ✅ 未碰触 kiwiai-pi 仓（lybra-executor 角色目录只读）

**在途排除**：
- AIPOS-279F1 已完成（09:35:00Z），其改动为本卡禁碰区域，已验证保留
- web/board 域当前仅本卡一张在途，无冲突

## Owner 眼验建议

**1. 后端验证**：
```bash
cd ~/projects/lybra
python3 web/board/app.py --workspace-root <工作区路径>

# 访问 runtime-status API
curl http://localhost:7117/api/runtime-status?workspace=0 | jq '.data.server_location'
```
预期输出：
```json
{
  "hostname": "<实际主机名>",
  "ip": "<实际出口IP>",
  "note": "AIPOS-286: Advisor agents should verify same-machine before connecting..."
}
```

**2. 前端验证**：
- 访问空板工作区（total_tasks===0）
- 看到「① 连接你的顾问 Agent」步骤
- 橙色 SSH 提醒框可见
- 点击「📋 一键复制接入提示词」，粘贴到文本编辑器
- 确认提示词包含：
  * `Lybra 服务端位置（AIPOS-286）：主机名 + IP`
  * `⚠️  第 0 步：同机确认与连通性检测`
  * `curl -v <真实gate_url>/health` 命令示例
  * `block-and-report` 指引
  * 279F1 内容（Claude Code CLI 接法、标准第二步话术）

**3. i18n 验证**：
- 点击右上角语言切换按钮（EN）
- SSH 提醒变为 "⚠️ Cross-Machine Setup: ..."
- 按钮文字变为 "📋 Copy Onboarding Prompt"

**4. 跨机场景模拟**（可选）：
- 将提示词粘贴给顾问 agent
- 要求它执行第 0 步检查
- 故意阻断网络（防火墙/修改 gate URL 为不可达地址）
- 验证 agent 是否 block-and-report（不带病接线）

## 已知限制与后续

- **IP 检测方法**: 使用 dummy socket connect 获取首选出口 IP（不实际连接 8.8.8.8）
  - 多网卡场景可能返回非预期接口 IP
  - 后续可改进：从 advertise_host 配置或 connection.json 读取显式配置的 IP

- **第 0 步自动化**: 当前依赖顾问 agent 手动执行 curl / hostname 命令
  - 理想态：gate 提供 `/connectivity-check` API，一键诊断同机/跨机/连通性
  - 顾问 agent 可通过 MCP 工具自动调用该 API 完成第 0 步

- **connection.json 演进**: 服务端位置信息未写入 connection.json
  - 当前从 runtime-status 动态获取（实时）
  - 若需持久化，可在 gate 启动时写入 connection.json

---

**交付完成，如实汇报。** ✅

AIPOS-286 全部验收断言（S1-S4）已实现并通过契约测试。跨机感知能力已注入顾问接入提示词（服务端位置 + 第 0 步 + SSH 提醒），用户体验不脱节。279F1 内容完整保留（零覆盖）。零回归（核心测试套件全过）。
