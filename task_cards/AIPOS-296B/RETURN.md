---
task_id: AIPOS-296B
status: completed
completed_at: 2026-08-01T13:15:00Z
executor: exec.lybra.kiwiai-dev
model_used: anthropic/claude-3-7-sonnet-20250219
estimated_input_tokens: 42000
estimated_output_tokens: 3500
---

# AIPOS-296B 执行汇报

## 任务摘要
在 gate POST /mcp 端点实现 HTTP 内容协商：Accept 含 `text/event-stream` 时按 SSE 单事件返回，
否则维持 `application/json`。修复 Claude Code 客户端连接失败（M4 blocker#2）。

## 实现路径
根因：`tools/mcp_server/http_sse.py` do_POST L259-269 恒返回 application/json，
Claude Code Streamable-HTTP 客户端要求 SSE 响应。

### 核心修改
**文件**: `tools/mcp_server/http_sse.py` do_POST (L259-314)

1. **内容协商逻辑**:
   - 读取 `Accept` 头，检测 `text/event-stream`
   - `wants_sse = "text/event-stream" in accept_header`

2. **initialize 会话处理**:
   - 提前生成 session_id（初始化成功时）
   - 两径（JSON/SSE）都发送 `Mcp-Session-Id` 头

3. **notification 路径**（response is None）:
   - SSE 径：202 + `text/event-stream` + 空流 + `Connection: close`
   - JSON 径：202 + `application/json` + `{"ok": true, "notification": true}`

4. **正常响应路径**:
   - SSE 径：200 + `text/event-stream` + `data: <JSON>\n\n` + `Connection: close` + `Content-Length`
   - JSON 径：200 + `application/json` + JSON 体（codex/pi 零回归）

5. **HTTP/1.1 边界正确性**:
   - SSE 响应显式 `Connection: close`（与 AIPOS-296 GET SSE 同法）
   - 非 chunked 流必须明确传输边界，undici 客户端要求

### 测试矩阵（验收断言 S1-S4）
**新建**: `tools/mcp_server/tests/test_aipos296b_content_negotiation.py`

- ✅ **S1 内容协商**: Accept 两值 × initialize/tool call/notification = 6 用例全通
- ✅ **S2 notification 语义**: 两径都保持（SSE 空流 202、JSON ACCEPTED 202）
- ✅ **S3 边界正确性**: SSE 响应 `Connection: close` 头存在且正确
- ✅ **S4 Mcp-Session-Id**: initialize 时两径都发送会话 ID

**零回归验证**:
- `test_http_sse_transport.py`: 23/23 passed（所有既有 Streamable-HTTP 测试）
- `test_aipos296_http11_upgrade.py`: 7/7 passed（AIPOS-296 GET SSE 边界测试）
- 测试辅助函数 `post_rpc_full` 增强：智能解析 SSE/JSON 响应（兼容老测试）

## 变更清单
```
tools/mcp_server/http_sse.py                            # 核心实现（+58 行，内容协商）
tools/mcp_server/tests/test_http_sse_transport.py      # 测试辅助强化（SSE 智能解析）
tools/mcp_server/tests/test_aipos296b_content_negotiation.py  # 新增单测（9 用例）
```

## 协议级断言（S3 真机对照前置）
本机无 Claude Code 客户端，按卡内指引以协议级断言交付：

1. **SSE 单事件格式符合 MCP Streamable HTTP 契约**:
   - 响应头 `Content-Type: text/event-stream; charset=utf-8`
   - 响应体 `data: <JSON-RPC>\n\n`（单事件）
   - 边界 `Connection: close`（HTTP/1.1 流终止语义）

2. **与官方 SDK server 行为对照**:
   - MCP Streamable HTTP 规范要求 POST 响应可按 SSE 返回（Accept 协商）
   - 实现符合 undici 客户端预期（Claude Code 使用的底层库）
   - 边界处理与 AIPOS-296 GET SSE 流（已验 mac 编排者 curl 全通）保持一致

3. **零回归路径**:
   - 不含 `text/event-stream` 的 Accept → 仍收 application/json
   - 所有既有客户端（codex/pi/curl JSON）保持不变

## 终验检查项（owner_verify）
卡内要求 mac 编排者侧终验：

- [ ] `claude mcp add --transport http` 连接 lybra-agency
- [ ] `claude mcp list` 显示 ✓ connected + 7 工具
- [ ] codex/pi 等既有 Streamable-HTTP 客户端照常（不回归）
- [ ] 无 `Accept: text/event-stream` 的老客户端仍收 application/json

本侧协议级断言已完成，实际 Claude Code 客户端连通性需 mac 侧验证。

## 实际使用模型
- Provider: anthropic
- Model: claude-3-7-sonnet-20250219
- Estimated input tokens: ~42,000
- Estimated output tokens: ~3,500
- 上下文使用：21% (42k/200k)

## 下一步
等待 mac 侧 owner_verify 终验结果。若 Claude Code 连通，则此 M4 blocker#2 解除。
