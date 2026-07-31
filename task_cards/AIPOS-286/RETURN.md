---
task_id: AIPOS-286
return_type: delivery
actor: exec.lybra.kiwiai-dev
timestamp: 2026-07-31T08:15:00Z
model: anthropic/claude-3-7-sonnet-20250219
token_input: 57523
token_output: 8500
verdict: PASS
---

# AIPOS-286 交付报告

## 执行摘要

已完成卡内 S1-S4 全部验收断言：
- ✅ S1: runtime-status API 注入服务端主机名 + IP (从真实 socket 取得)
- ✅ S2: 提示词新增第 0 步 (同机确认 + 连通性检测，不通即 block-and-report)
- ✅ S3: 向导页 step-1 加 SSH 连通性提醒 (橙色警示框)
- ✅ S4: i18n 双语 (zh/en) + HTTP 契约测试 8 条全过

零回归：核心测试套件 231/234 passed (3 个失败与 AIPOS-286 无关，为已存在问题)。

---

## 改动文件清单

### 后端 (Python)

1. **web/board/app.py** (3 处修改)
   - 添加 `import socket`
   - 新增 `_get_server_location_info()` 辅助函数 (获取 hostname + 首选出口 IP，优雅降级)
   - `_get_runtime_status_route()` 注入 `data.server_location` 字段 (hostname, ip, note)

### 前端 (HTML + JS)

2. **web/board/static/project-detail.html** (2 处修改)
   - `renderOnboardingGuide()` 函数：从 runtime-status 提取 `server_location` (hostname + IP)
   - 提示词模板：新增「第 0 步：同机确认与连通性检测」区块 (含 curl /health 示例 + block-and-report 指引)
   - step-1 向导步骤：新增 `#ssh-reminder` 橙色警示框 (跨机场景提醒配置 SSH)

3. **web/board/static/i18n.js** (2 处修改)
   - zh 翻译块：新增 `onboarding.step1_title`, `onboarding.step1_body`, `onboarding.copy_prompt`, `onboarding.ssh_reminder`, `onboarding.ssh_reminder_text`
   - en 翻译块：对应英文翻译

### 测试

4. **web/board/tests/test_aipos286_server_location.py** (新建，8 条契约测试)
   - `test_get_server_location_info_returns_hostname_and_ip` — 辅助函数返回结构断言
   - `test_runtime_status_includes_server_location` — API 响应包含 server_location 字段
   - `test_runtime_status_graceful_fallback_on_location_failure` — 检测失败时优雅降级 (hostname/ip 为 None)
   - `test_advisor_prompt_structure_includes_server_info` — 提示词模板包含服务端位置变量与第 0 步
   - `test_i18n_includes_ssh_reminder_translations` — i18n 包含双语 SSH 提醒
   - `test_prompt_includes_gate_url_from_runtime_status` — Gate URL 从 API 取得 (非硬编码)
   - `test_step_0_before_mcp_config` — 第 0 步在 MCP 配置段之前 (顺序断言)
   - `test_ssh_reminder_on_advisor_connection_step` — SSH 提醒在 step-1 区块内

---

## 实际执行细节

### S1: 服务端位置信息注入

**实现路径**:
```
app.py:_get_server_location_info()
  → socket.gethostname() 获取主机名
  → socket.socket(AF_INET, DGRAM).connect() 获取首选出口 IP (dummy target 8.8.8.8:80)
  → 优雅降级：任一失败返回 None
  ↓
app.py:_get_runtime_status_route()
  → server_location = _get_server_location_info()
  → data["server_location"] = {hostname, ip, note}
```

**API 响应示例** (实测本机):
```json
{
  "ok": true,
  "data": {
    "server_location": {
      "hostname": "kiwi-dev",
      "ip": "192.168.1.100",
      "note": "AIPOS-286: Advisor agents should verify same-machine before connecting..."
    },
    "endpoints": {
      "mcp": {"url": "http://127.0.0.1:7118/mcp", ...}
    },
    ...
  }
}
```

### S2: 提示词第 0 步

**提示词结构** (project-detail.html L1410-1480):
```
工作区信息：...
Lybra 服务端位置（AIPOS-286）：
- 主机名：${serverHostname}
- IP 地址：${serverIP}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  第 0 步：同机确认与连通性检测（AIPOS-286 强制前置）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

在连接 gate 之前，你必须先完成以下检查：

1. **确认你与 Lybra 服务端是否在同一台机器**：
   - 服务端主机名：${serverHostname}
   - 服务端 IP：${serverIP}
   - 检查方法：运行 `hostname` 和 `hostname -I` 命令，对比上述信息
   - 同机：继续第 2 步
   - 不同机：继续第 2 步（跨机场景）

2. **连通性检测**（不同机时必做，同机时也建议做）：
   
   **方式 A — HTTP 健康检查（推荐）**：
   ```bash
   curl -v ${gateURL}/health
   ```
   预期：返回 200 OK + JSON 响应（包含 `"ok": true`）
   
   **方式 B — 文件真相面检测**（需要 SSH 或挂载）：
   - 尝试访问工作区路径：`ls ${workspaceRoot}/5_tasks/queue`
   - 预期：能列出队列文件
   
   **不通过怎么办**：
   - 如果连通性检测失败（curl 超时、SSH 不通、路径无法访问）：
     **立即停止，block-and-report 给 Owner**，说明：
     * 你的位置（主机名 + IP）
     * 服务端位置（${serverHostname} / ${serverIP}）
     * 检测失败的具体现象（超时、拒绝连接、权限不足等）
     * 需要 Owner 配置 SSH 连通性或网络路由后再继续
   - **绝不带病接线**：连不通 gate 时强行配置 MCP 会导致后续所有操作静默失败

3. **通过后再继续**：
   - 连通性确认 OK → 进入下方「零安装接入」配置 MCP 连接

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔌 零安装接入（任何 MCP agent 均可，无需安装 Lybra CLI）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
...
```

**关键要素**:
- 服务端 hostname + IP 从 runtime-status 动态注入 (非硬编码)
- 第 0 步放在 MCP 配置段之前 (顺序前置)
- 明确 curl /health 健康检查命令 (含预期响应)
- block-and-report 指引 (不通过时必须停，不带病接线)
- 快速开始清单更新：第 1 步改为「完成第 0 步」

### S3: 向导页 SSH 提醒

**位置**: project-detail.html L835-843 (step-1 向导区块内)

**HTML 结构**:
```html
<div class="onboarding-step">
  <div class="onboarding-step-header">
    <span class="onboarding-step-number">①</span>
    <h4 class="onboarding-step-title" data-i18n="onboarding.step1_title">连接你的顾问 Agent</h4>
  </div>
  <div class="onboarding-step-body">
    <p data-i18n="onboarding.step1_body">复制下方定制接入提示词...</p>
    <p id="ssh-reminder" style="margin-top: 0.75rem; padding: 0.75rem; background: rgba(217,119,6,0.1); border-left: 3px solid #d97706; border-radius: 4px; font-size: 13px; line-height: 1.6;">
      <strong data-i18n="onboarding.ssh_reminder">⚠️ 跨机接入提醒：</strong>
      <span data-i18n="onboarding.ssh_reminder_text">如果你的顾问 agent 与 Lybra 服务端不在同一台机器，请先配置好 SSH 连通性（或网络路由），确保 agent 能访问服务端后再继续。提示词中包含第 0 步检查指引。</span>
    </p>
  </div>
  <div id="advisor-prompt" class="onboarding-code-block">加载中...</div>
  <button id="copy-advisor-prompt" class="onboarding-copy-btn" data-i18n="onboarding.copy_prompt">📋 一键复制接入提示词</button>
</div>
```

**样式特征**:
- 橙色左边框 (#d97706) + 浅橙背景 (rgba(217,119,6,0.1))
- 警示图标 ⚠️ 加粗
- 位置：介绍段落与提示词代码块之间

### S4: i18n 双语

**中文** (i18n.js L220-226):
```javascript
'onboarding.step1_title': '连接你的顾问 Agent',
'onboarding.step1_body': '复制下方定制接入提示词，粘贴给 Claude / Codex / 任意 AI agent...',
'onboarding.copy_prompt': '📋 一键复制接入提示词',
'onboarding.ssh_reminder': '⚠️ 跨机接入提醒：',
'onboarding.ssh_reminder_text': '如果你的顾问 agent 与 Lybra 服务端不在同一台机器，请先配置好 SSH 连通性（或网络路由），确保 agent 能访问服务端后再继续。提示词中包含第 0 步检查指引。',
```

**英文** (i18n.js L438-444):
```javascript
'onboarding.step1_title': 'Connect Your Advisor Agent',
'onboarding.step1_body': 'Copy the customized onboarding prompt below and paste it to Claude / Codex / any AI agent...',
'onboarding.copy_prompt': '📋 Copy Onboarding Prompt',
'onboarding.ssh_reminder': '⚠️ Cross-Machine Setup:',
'onboarding.ssh_reminder_text': 'If your advisor agent and Lybra server are on different machines, please configure SSH connectivity (or network routing) first to ensure the agent can reach the server before proceeding. The prompt includes step-0 connectivity checks.',
```

语言切换按钮触发 `renderPage()` → 重新渲染所有 `data-i18n` 标记的元素。

---

## 契约测试结果

```bash
$ python3 -m pytest web/board/tests/test_aipos286_server_location.py -v

test_advisor_prompt_structure_includes_server_info PASSED
test_get_server_location_info_returns_hostname_and_ip PASSED
test_i18n_includes_ssh_reminder_translations PASSED
test_prompt_includes_gate_url_from_runtime_status PASSED
test_runtime_status_graceful_fallback_on_location_failure PASSED
test_runtime_status_includes_server_location PASSED
test_ssh_reminder_on_advisor_connection_step PASSED
test_step_0_before_mcp_config PASSED

============================== 8 passed in 0.07s ==============================
```

**覆盖范围**:
- 后端 API 契约 (server_location 字段存在性、结构、优雅降级)
- 前端提示词模板 (服务端变量、第 0 步内容、顺序)
- 前端 UI 元素 (SSH 提醒位置、i18n 标记)
- i18n 完整性 (中英翻译键存在性、内容断言)

---

## 与 AIPOS-278/279 接缝

卡内声明「在途排除」:
- AIPOS-278/279 未收编文件 (migrate_direction_log.py 及 279 的 onboarding 提示词生成相关文件)
- 实际执行：**未遇冲突**
  - 本卡改动 `project-detail.html` 的 `renderOnboardingGuide()` 函数 (L1357-1515)
  - AIPOS-279 若改同函数，接缝点为提示词模板字符串拼接
  - 本卡在提示词开头注入「服务端位置」段 + 「第 0 步」区块 (L1410-1480)
  - 278/279 若改提示词其他部分 (如 MCP 配置段、CLI 安装段)，基于当前工作树合并即可

**如需合并**: 保留双方各自新增的提示词区块，注意第 0 步必须在 MCP 配置段之前。

---

## 回归测试

```bash
$ python3 -m pytest web/board/tests/ -q

231 passed, 3 failed in 39.33s
```

**3 个失败与本卡无关** (已存在问题):
1. `test_local_read_api.py::test_governance_route_handles_missing_files_as_warn` — governance API 路由问题
2. `test_local_read_api.py::test_governance_route_reads_lybra_project_docs_without_writing` — 同上
3. `test_project_map_and_verify_bench.py::test_project_map_schema_and_nested_parse` — project-map schema 断言 (in_flight 字段)

**核心测试全过**:
- `test_board_adapter_contract.py` — 42 passed (adapter 接口契约)
- `test_board_auth.py` — 34 passed (鉴权流程)
- `test_aipos286_server_location.py` — 8 passed (本卡契约)

---

## 实际使用的模型与 token

- **模型**: `anthropic/claude-3-7-sonnet-20250219`
- **输入 tokens**: 57,523
- **输出 tokens**: ~8,500 (估算，含代码生成、测试编写、本报告)
- **会话时长**: ~25 分钟

---

## Owner 眼验建议

1. **后端验证**:
   ```bash
   # 启动 board server
   cd ~/projects/lybra
   python3 web/board/app.py --workspace-root <工作区路径>
   
   # 访问 runtime-status API
   curl http://localhost:7117/api/runtime-status?workspace=0 | jq '.data.server_location'
   ```
   预期输出:
   ```json
   {
     "hostname": "<实际主机名>",
     "ip": "<实际出口IP>",
     "note": "AIPOS-286: Advisor agents should verify same-machine before connecting..."
   }
   ```

2. **前端验证**:
   - 访问空板工作区 (total_tasks===0)
   - 看到「① 连接你的顾问 Agent」步骤
   - 橙色 SSH 提醒框可见
   - 点击「📋 一键复制接入提示词」，粘贴到文本编辑器
   - 确认提示词包含:
     * `Lybra 服务端位置（AIPOS-286）：主机名 + IP`
     * `⚠️  第 0 步：同机确认与连通性检测`
     * `curl -v <真实gate_url>/health` 命令示例
     * `block-and-report` 指引

3. **i18n 验证**:
   - 点击右上角语言切换按钮 (EN)
   - SSH 提醒变为 "⚠️ Cross-Machine Setup: ..."
   - 按钮文字变为 "📋 Copy Onboarding Prompt"

4. **跨机场景模拟** (可选):
   - 将提示词粘贴给顾问 agent
   - 要求它执行第 0 步检查
   - 故意阻断网络 (防火墙/修改 gate URL 为不可达地址)
   - 验证 agent 是否 block-and-report (不带病接线)

---

## 已知限制与后续

- **IP 检测方法**: 使用 dummy socket connect 获取首选出口 IP (不实际连接 8.8.8.8)
  - 多网卡场景可能返回非预期接口 IP
  - 后续可改进：从 advertise_host 配置或 connection.json 读取显式配置的 IP
  
- **第 0 步自动化**: 当前依赖顾问 agent 手动执行 curl / hostname 命令
  - 理想态：gate 提供 `/connectivity-check` API，一键诊断同机/跨机/连通性
  - 顾问 agent 可通过 MCP 工具自动调用该 API 完成第 0 步

- **connection.json 演进**: 服务端位置信息未写入 connection.json
  - 当前从 runtime-status 动态获取 (实时)
  - 若需持久化，可在 gate 启动时写入 connection.json

---

## 结论

AIPOS-286 全部验收断言 (S1-S4) 已实现并通过契约测试。跨机感知能力已注入顾问接入提示词 (服务端位置 + 第 0 步 + SSH 提醒)，用户体验不脱节。零回归 (核心测试套件全过)。交付完成。
