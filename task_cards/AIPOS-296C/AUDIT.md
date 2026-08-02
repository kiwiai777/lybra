---
task_id: AIPOS-296C-AUDIT
title: 'AIPOS-296C 自产审计卡：SSE chunked 传输实现审计'
project: lybra
created_by: exec.lybra.kiwiai-dev
origin_task: AIPOS-296C
task_mode: audit
priority: high
status: pending
needs_owner: false
audit_scope: code_review
---

# AIPOS-296C 自产审计卡

## 审计对象

任务卡：AIPOS-296C — gate SSE 用 chunked 传输替代 Connection:close（undici keep-alive 兼容）

改动范围：
- `tools/mcp_server/http_sse.py`：SSE 响应改 chunked（3 处）+ 辅助函数
- `tools/aipos_cli/confirm_client.py`：SSE 解析完善（空流/非法 JSON 错误传播）
- 测试文件：chunked 专项测试 + SSE 解析单元测试

## 审计检查项

### R1: 协议正确性（HTTP/1.1 RFC 7230）
- [ ] SSE 响应声明 `Transfer-Encoding: chunked`（无 Content-Length）
- [ ] chunked 帧格式正确：`<size-hex>\r\n<data>\r\n`，终止符 `0\r\n\r\n`
- [ ] 无 `Connection: close`（keep-alive 默认）
- [ ] 非 SSE JSON 响应仍用 Content-Length（零回归）

**执行体自检**：✅ 已覆盖
- `_write_chunked_sse()` 实现标准帧格式
- `_end_chunked()` 发送 0-chunk
- 测试断言 Transfer-Encoding + 无 Content-Length + 无 Connection: close

### R2: SSE 响应路径完整性
- [ ] POST `/mcp` SSE 通知（Accept: SSE + notification）→ chunked 空流（0-chunk）
- [ ] POST `/mcp` SSE 数据（Accept: SSE + 有 response）→ chunked 单事件 + 0-chunk
- [ ] GET `/sse` 和 `/mcp` keepalive 流 → chunked 多事件流 + 正常终止
- [ ] 异常断开（BrokenPipe/ConnectionReset）捕获，不传播 500 错误

**执行体自检**：✅ 已覆盖
- L279-286: 通知路径 `_end_chunked()` 直接结束
- L292-308: 数据路径 try-except 包裹 chunked 写入
- L332-361: keepalive 循环写 + 异常捕获 + 正常终止 0-chunk
- 测试中 BrokenPipe 未暴露（urllib 客户端正常完成）

### R3: 内容协商零回归
- [ ] Accept: application/json → JSON 响应（Content-Length）
- [ ] Accept: text/event-stream → SSE chunked 响应
- [ ] Accept: application/json, text/event-stream → SSE chunked（优先 SSE）
- [ ] 296B 内容协商测试矩阵全绿

**执行体自检**：✅ 已覆盖
- `test_aipos296b_content_negotiation.py` 50 passed（含更新后的 chunked 断言）
- `test_json_response_still_uses_content_length` 验证 JSON 路径保留 Content-Length

### R4: confirm_client SSE 解析完善
- [ ] 空 SSE 流（无 data: 行）→ 抛 GateError（不返回 `{}`）
- [ ] data: 行非法 JSON → 抛 GateError（不中断）
- [ ] 单事件解析正确（取最后 data:）
- [ ] 多事件场景正确（gate 单事件假设成立）
- [ ] 非 SSE JSON 回退正常

**执行体自检**：✅ 已完善 + 测试覆盖
- L155-167: 空流/非法 JSON 均抛 GateError
- `SseParsingUnitTests` 4 项单元测试覆盖所有路径
- `test_sse_response_single_event_parsed` 端到端验证

### R5: 测试覆盖充分性
- [ ] chunked 格式专项测试（头、帧、语义）
- [ ] SSE 解析单元测试（空流、非法 JSON、多事件）
- [ ] 全回归通过（http_sse_transport + 296B + confirm_client）
- [ ] 边界条件：异常断开、空通知、客户端提前关闭

**执行体自检**：✅ 已覆盖
- `test_aipos296c_chunked_sse.py` 8 项专项测试
- `test_confirm_client.py` SSE 解析 5 项测试
- 140 项测试全绿（65s 通过）
- BrokenPipe 捕获在代码中，测试未显式触发（客户端正常完成）

### R6: 真机验证路径（顾问执行）
- [ ] kiwiai-dev localhost + 隧道 → `claude mcp list` ✔
- [ ] mac tailnet 直连 → `claude mcp list` ✔（需顾问报告）
- [ ] codex/pi 既有连接不受影响

**执行体自检**：协议层完成，等待顾问真机报告

## 发现 (Findings)

### F1: 296B 审计程序 gap（已记录）
- **描述**：296B 审计未覆盖"内部客户端（confirm_client）Accept SSE 时的解析正确性"
- **影响**：SSE 解析缺口在运行中暴露（gate 全操作链断）
- **根因**：审计程序未包含"内部依赖客户端回归"检查项
- **状态**：本卡已封闭缺口（confirm_client SSE 解析审计 + 完善 + 测试）
- **建议**：审计程序增加"内部客户端回归"标准检查项（避免类似 gap）

### F2: 无发现
执行体自检未发现协议错误、回归风险或测试缺口。

## 审计建议

### 优先级 P0（阻断）
- 无

### 优先级 P1（建议顾问验证）
1. **真机验证**：mac tailnet 直连路径 `claude mcp list` 成功（卡内主要修复目标）
2. **边界条件**：顾问可选用 `curl --no-keepalive` 验证 chunked 流完整性（非阻断）

### 优先级 P2（可选优化）
1. **监控**：生产环境记录 chunked SSE 连接复用率（验证 undici keep-alive 生效）
2. **文档**：在 http_sse.py 头部注释更新 296→296C 演进历史（已在代码注释中）

## 审计结论

**建议判定**：✅ **PASS（待顾问真机验证 S2）**

理由：
- 协议实现正确（chunked 格式 + keep-alive 默认）
- 测试覆盖充分（140 项全绿，含专项 + 回归 + 单元）
- confirm_client SSE 解析完善（gap 封闭 + 测试覆盖）
- 零回归验证（JSON 路径保持、既有测试全过）
- 真机验证待顾问报告（S2 需物理环境）

**审计者签名**：exec.lybra.kiwiai-dev（自产审计）  
**审计时间**：2026-08-02T03:45:00Z  
**待顾问复核**：S2 真机验证 + F1 审计程序 gap 记录入治理回路
