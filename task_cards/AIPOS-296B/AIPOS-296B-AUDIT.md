---
task_id: AIPOS-296B-AUDIT
title: 'AIPOS-296B 自产审计卡 — POST /mcp 内容协商实现'
project: lybra
task_mode: audit
task_class: simple
priority: high
status: pending
created_by: exec.lybra.kiwiai-dev
parent_task: AIPOS-296B
audit_type: self_generated
needs_owner: false
assigned_to: auditor.lybra.kiwiai-dev
---

# AIPOS-296B 自产审计卡

## 实现概述
executor 在 `tools/mcp_server/http_sse.py` do_POST 实现 Accept 头内容协商：
- 含 `text/event-stream` → SSE 单事件响应（`data: <JSON>\n\n`）
- 纯 `application/json` 或缺失 → JSON 响应（零回归）

## 审计要点

### A1. 核心契约（卡内 S1-S4 验收断言）
- [ ] **内容协商正确性**: Accept 含 text/event-stream 时响应 `Content-Type: text/event-stream`
- [ ] **SSE 格式合规**: 响应体 `data: <JSON-RPC>\n\n`（单事件），JSON 可解析
- [ ] **边界正确性**: SSE 响应含 `Connection: close`（HTTP/1.1 非 chunked 流边界）
- [ ] **零回归保证**: 不含 text/event-stream 的 Accept → 仍收 application/json（codex/pi 路径）
- [ ] **notification 语义**: response is None 时两径正确处理（SSE 空流 202、JSON ACCEPTED 202）
- [ ] **Mcp-Session-Id**: initialize 成功时两径都发送会话 ID 头

### A2. 实现安全（边界/错误处理）
- [ ] Accept 头缺失/格式异常不崩溃（默认 JSON 路径）
- [ ] SSE 径的 JSON 序列化失败有兜底（与 JSON 径错误处理一致）
- [ ] notification 径（response is None）无内存泄漏（空流立即关闭）
- [ ] session_id 生成失败不阻断响应（可选头）

### A3. 测试覆盖（新增单测 + 零回归）
- [ ] `test_aipos296b_content_negotiation.py` 9 用例全过（Accept 两值 × 3 场景 + 边界断言）
- [ ] 既有 `test_http_sse_transport.py` 23 用例全通（Streamable-HTTP 零回归）
- [ ] `test_aipos296_http11_upgrade.py` 7 用例全通（AIPOS-296 GET SSE 边界回归）
- [ ] 测试辅助 `post_rpc_full` 智能解析 SSE/JSON（无老测试改动）

### A4. 代码质量
- [ ] AIPOS-296B 注释清晰标注变更点与理由
- [ ] 内容协商逻辑与 session_id 生成解耦（单一职责）
- [ ] SSE/JSON 两径代码重复度低（_json_response 复用）
- [ ] 无硬编码魔法值（使用 HTTPStatus 枚举、SESSION_HEADER 常量）

### A5. 协议级正确性（S3 真机对照前置）
- [ ] SSE 单事件格式符合 MCP Streamable HTTP 规范（Accept 协商 → POST 响应可为 SSE）
- [ ] `Connection: close` 语义与 AIPOS-296 GET SSE 流保持一致（undici 客户端要求）
- [ ] JSON-RPC 响应结构在 SSE 信封内完整（id/result/error 字段不丢失）

### A6. 回归风险检查
- [ ] AIPOS-201 Streamable-HTTP 既有行为不变（session 跟踪、DELETE 端点）
- [ ] 老 Accept 头（application/json 或 */*）仍收 JSON（无破坏性变更）
- [ ] 错误响应（401/404/400）未受内容协商影响（仍为 JSON structured error）

## 审计指引
1. **代码审查**: 读 `http_sse.py` L259-314 内容协商逻辑，确认边界处理完整
2. **单测验证**: 跑 `test_aipos296b_content_negotiation.py` + 既有 http_sse 测试套件，确认零回归
3. **协议对照**: 对比 MCP Streamable HTTP 规范与实现的 SSE 格式（Accept → POST SSE 响应）
4. **边界检查**: 验证 `Connection: close` 存在（curl -v 可见、测试断言覆盖）

## 预期审计结果
- **PASS 条件**: A1-A6 全勾选 + 单测全过 + 协议级断言成立
- **CONDITIONAL_PASS**: A1-A5 过 + A6 有轻微代码质量瑕疵（需修复卡）
- **FAIL**: A1-A3 任一未满足（核心契约/安全/测试覆盖缺失）

## 终验依赖
mac 侧 owner_verify（`claude mcp list` 显示 lybra-agency ✓ connected）是产品级验收，
与本审计卡独立。审计关注协议实现正确性，终验关注真实客户端连通性。

## 审计交付
auditor 跑完审计后写 `AIPOS-296B-AUDIT-VERDICT.md` 到本目录，包含：
- 审计结果（PASS/CONDITIONAL_PASS/FAIL）
- A1-A6 检查项逐项结论
- 发现的问题清单（若有）
- 修复建议（若 CONDITIONAL_PASS）
