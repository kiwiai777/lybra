# AIPOS-201 — Gate 原生 Streamable-HTTP 传输支持 (micro-plan, DRAFT)

- status: draft
- authority: NONE — 未经 Owner 批准不实现;本稿仅供复核
- date: 2026-06-22
- task-id: AIPOS-201 (proposed;待 board 正式分配)
- epic: v1.0 Scope B — Form-B 非-Claude 接入(DL-20260622-01,DG-7/维度 4)
- slice: **片一(传输层)**;片二 = codex 经 Form B 直连 gate 跑 claim/return 实证(分界见 §9)
- scope-discipline: 只加传输层;复用现有 scope(AIPOS-197)/confirmer(AIPOS-199)/controlled-execute/L3 Detection/L2 Wall,**一律不改语义**。不改产品码以外文件,不碰证据现场,raw token 仅 fingerprint。

---

## §1 背景 + 为什么需要

- v1.0 Scope B 要求 **≥1 个真实非-Claude agent 经 Form B 接入 gate 跑 claim/return**(维度 4,不降级为纸面)。候选客户端 = WSL 已装 `codex-cli 0.141.0`(ChatGPT 订阅)。
- 探查结论(上一轮只读):codex 原生支持 `codex mcp add <NAME> --url <URL> --bearer-token-env-var <ENV>` —— **streamable HTTP MCP server + 从 env 读 Bearer**。形态判定 = (a) 可直连。
- **唯一 go/no-go 残留** = gate 的 serve-http 端当前**不是** streamable-HTTP:
  - 现状(AIPOS-123,见 §2)= 自定义「`POST /mcp` 单次 JSON-RPC 响应 + `GET /sse` 纯 keepalive」,`protocolVersion: 2024-11-05`。
  - 这既非 MCP 2024-11-05 标准的 HTTP+SSE 传输,也非 2025-03-26 的 Streamable-HTTP。codex 的 `--url` 期望后者。
- 故 **片一 = 为 gate 增加一个原生 streamable-HTTP 传输面**,使标准 streamable-HTTP client(含 codex)能 `initialize` + `tools/list` + `tools/call` 经 Bearer 连通,**且不触碰任何上层语义**。

---

## §2 现状(精确,带代码锚点)

来源:`tools/mcp_server/http_sse.py`、`tools/mcp_server/server.py`。

| 面 | 现状 | 锚点 |
|---|---|---|
| `POST /mcp` | 鉴权 → 读 JSON-RPC 对象 → `handle_request` → **单个 application/json 响应**;notification 返回 202。无 session、无 SSE-on-POST。 | `http_sse.py:190-224` |
| `GET /sse` | 鉴权 → `text/event-stream`,**只发 `event: ping` keepalive**,从不承载 JSON-RPC 消息。 | `http_sse.py:226-254` |
| JSON-RPC 核心 | `initialize`/`tools/list`/`tools/call`/`ping`;`protocolVersion = "2024-11-05"`。 | `server.py:35-82` |
| 鉴权 | `_authorize()`:单 token(`LYBRA_MCP_TOKEN`)或 service-role registry(`connection.json`);Bearer 前缀校验。 | `http_sse.py:171-188, 70-130` |
| **scope/confirmer 接入点** | Bearer → `_service_role_capability` 解析 capability(role/operations/fingerprint)→ `request_capability_scope(capability)` 包裹 `handle_request` → **AIPOS-197 scope gate + AIPOS-199 confirmer 留痕**。 | `http_sse.py:143-157, 184-188` |
| 传输入口 | `serve-http` 子命令;`--service-connection-json` 启用 registry 模式。 | `server.py:117-150` |

**关键不变量**:所有授权/scope/confirmer 逻辑都在 `_rpc_response` 之前(Bearer→capability)与 `handle_request` 之内(语义)。**新传输面只要复用 `request_capability_scope(capability) + handle_request(message)` 这一对,就 0 语义改动**地继承 197/199/controlled-execute/L3/Wall。

---

## §3 差距:streamable-HTTP 要求 vs 现状

MCP Streamable-HTTP(2025-03-26)相对现状的增量(逐条标「现状/需新增」):

1. **单端点 GET+POST**:client 对同一 URL POST JSON-RPC,并可 GET 开 SSE 收 server→client 消息。— 现状 POST `/mcp` 在、GET `/sse` 仅 keepalive。
2. **POST 响应内容协商**:client 发 `Accept: application/json, text/event-stream`;server 可回 `application/json`(单响应,**现状已支持**)**或** `text/event-stream`(把响应 + 中途消息以 SSE 帧发回)。— **SSE-on-POST 需新增(若 codex 强制要求)**。
3. **`Mcp-Session-Id`**:server 在 `initialize` 响应头签发,client 后续请求回带。— **需新增(若 codex 强制要求)**。
4. **`MCP-Protocol-Version` 头协商** + `initialize` 返回 ≥ `2025-03-26`。— **需新增/条件返回**。
5. **`DELETE`(显式结束 session)**:可选。— 视 §4 第一步探查结果决定是否实现。

> **第一步必做的兼容探查(纳入实现前、不进本 micro-plan 决策)**:用 codex(或标准 streamable-HTTP client)对一个最小回声 server 实测,确认 codex 究竟**强制**要求哪些(尤其 #2 是否接受纯 `application/json`、#3 session-id 是否必需)。这决定实现体量。**若 codex 接受 application/json 单响应 + 仅需 session-id,则实现极薄;若强制 SSE 响应帧,则体量上升** —— 该探查结果写入实现任务首节。

---

## §4 ★第一待决项(Owner 拍板):实现方式

### 选项 A — 官方 MCP Python SDK 的 streamable-HTTP server

- 依赖:引入 `mcp` SDK + 其传递依赖(典型 `starlette`/`anyio`/`pydantic`/可能 `uvicorn`)。
- 体量:server 由当前 **sync `ThreadingHTTPServer`(stdlib,零依赖)** 转/并存 **ASGI(async)** 运行时;需把 Bearer/registry 鉴权 + `request_capability_scope` 桥接成 ASGI middleware。
- 优点:spec 合规(session/SSE/协商由上游维护),随 spec 演进省心。
- 缺点:**供应链与审计面显著扩大**(多个新依赖)、引入 async 运行时与现有 sync 栈并存的复杂度、与 file-authoritative/最小依赖气质相左;桥接 197/199 反而要小心别在 middleware 层漏掉 scope 包裹。

### 选项 B — 手写最小合规 streamable-HTTP(扩展现有 `http_sse.py`)

- 依赖:**零新增**;继续 stdlib `ThreadingHTTPServer`。
- 体量:在现有 handler 上加 Accept 协商、`Mcp-Session-Id` 签发/校验、`protocolVersion` 条件返回,(按 §3 探查)可选加 SSE-on-POST 帧。复用 `_authorize`/`_service_role_capability`/`request_capability_scope`/`handle_request` 原样。
- 优点:零依赖、审计面最小、对 197/199 接入点完全掌控、与现有架构同构。
- 缺点:需自己实现 spec 细节(session/协商/SSE 帧),有细微不合规风险;spec 演进需手工跟。

### 建议(待 Owner 拍板)

**倾向 B(手写最小合规)**,理由:(1) Lybra 红线偏好最小依赖 + 小审计面;(2) 197/199 的 scope/confirmer 接入点已是一对干净函数,手写传输能原样复用、零语义风险;(3) 若 §4 第一步探查证明 codex 接受 `application/json` 单响应 + session-id,则增量极小(估 < `http_sse.py` 现规模的一半)。**若**探查显示 codex 强制 SSE 响应帧且实现/维护成本陡升,**再回退到 A**。
**列为待 Owner 拍板;在 Owner 选定前不进实现。**

---

## §5 设计(并存、加法,不改坏旧路径)

> 以下为**选项 B 假定**下的形态;若 Owner 选 A,设计改为 ASGI app + middleware,语义接入点同。

- **端点策略(次级设计点,Owner 可一并示意)**:
  - B1:**复用 `/mcp`**,按 `Accept` 头与 `Mcp-Session-Id` 做内容协商 —— 不发 streamable 语义的旧 client 仍得 `application/json` 单响应(**严格向后兼容**)。
  - B2:**新增独立路径**(如 `/mcp/stream`)承载 streamable,`/mcp` 原样不动。
  - 倾向 **B1**(单端点对 codex 更自然,且旧 POST 行为在「无 streamable 头」分支完全保留);最终以 §3 探查 + 审计意见定。
- **`GET /sse` 不动**:keepalive 语义保留(AIPOS-123 契约),不复用为消息通道。
- **鉴权/scope/confirmer**:新分支**必须**走同一 `_authorize()` → `_request_capability()` → `request_capability_scope(capability)` 包裹 `handle_request`。**禁止**在新传输面另写鉴权或绕过 scope 包裹。
- **session**:`initialize` 签发 `Mcp-Session-Id`;后续请求校验存在性(语义无状态,session 仅满足传输契约;不引入服务端长状态机以免触 gate-not-engine —— session 表为轻量内存映射,过期即弃)。
- **protocolVersion**:streamable 分支返回 `2025-03-26`(或 codex 协商所需最低版);旧 `/mcp` 单响应分支保留 `2024-11-05`。
- **红线**:无 daemon 化、无调度、无心跳驱动启动;传输面只应答请求,绝不主动驱动 agent。

---

## §6 兼容 / 非回归要求(硬性)

1. 现有 `POST /mcp` 单 JSON-RPC 响应行为**逐字节不变**(对不发 streamable 头的 client)。
2. 现有 `GET /sse` keepalive 行为不变。
3. **Claude 191B 链路回归**:既有 Claude Code × Supervised 经 serve-http 的 claim→/scratch→return 路径仍绿(用现有 191B-rerun 级别的链路验证,或等效集成测试)。
4. **★A1 不变**:executor token 走 `*_confirm` 仍 SCOPE_DENIED。
5. **AIPOS-199 不变**:claim/return confirm-write 仍正确留痕 confirmer。
6. service-mode registry / 单 token 两种鉴权模式在新传输面行为一致。

---

## §7 测试计划(点名现有基线)

新增传输测试入 `tools/mcp_server/tests/test_http_sse_transport.py`(现 17.5K,已含 service-mode/scope 可见性测试 `:226/:257/:310`):

- **T1 新传输连通**:标准 streamable-HTTP client(优先用 codex;不可则用最小自写 streamable client)经 `Authorization: Bearer` 完成 `initialize`(校验 `Mcp-Session-Id` + protocolVersion)→ `tools/list` → 一次只读 `tools/call`,断言成功。
- **T2 旧路径回归**:现有 `POST /mcp` / `GET /sse` 测试**全绿无改动**(回归基线 = 现 `test_http_sse_transport.py` 全量)。
- **T3 鉴权回归**:缺/错 Bearer 在新传输面同样 `MISSING_BEARER_TOKEN`/`INVALID_BEARER_TOKEN`(复用 `_authorization_error` 路径)。
- **T4 scope 回归(★A1)**:executor-scope token 经新传输面 `tools/call queue_claim_confirm` → SCOPE_DENIED(对应 `test_mcp_tools.py:919 test_aipos197_claim_confirm_denied_without_owner_confirm_scope` 的传输层镜像)。
- **T5 confirmer 回归(199)**:owner-scope token 经新传输面完成 claim dry-run→confirm,读盘断言 confirmer 留痕(镜像 `test_mcp_tools.py:974 test_aipos199_claim_confirm_records_confirmer_attribution`)。
- **T6 全量**:`tools/` 套件零回归(基线:full `tools/` 388,见 DL-20260619-03)。
- 验收口径:T1 绿(新)+ T2/T3/T4/T5 绿(回归)+ T6 零回归。

---

## §8 cc glm 审计点

1. **零语义改动核验**:新传输面是否真的只经 `request_capability_scope + handle_request`?有无在传输层旁路 scope/confirmer/controlled-execute?
2. **旧路径逐字节兼容**:`POST /mcp`(无 streamable 头)+ `GET /sse` 行为与改前一致(diff 审 + T2)。
3. **★A1 + 199 经新传输面仍成立**(T4/T5 实测,非仅单测函数级)。
4. **鉴权不退化**:两种鉴权模式 + Bearer 错误码在新面一致;raw token 不入日志(沿用 `log_message` 静默)。
5. **红线**:无 daemon/调度/心跳;session 表不构成长状态机 SoT;依赖(若选 A)供应链审计。
6. **codex 实测证据**(若 T1 用 codex)= 片一与片二的接缝,见 §9。

---

## §9 片一 / 片二 分界

- **片一(本 micro-plan,AIPOS-201)= 传输能力 + 单元/集成测试**:交付物 = gate 能被标准 streamable-HTTP client 连通(initialize+tools/call 经 Bearer),旧路径 + ★A1 + 199 回归绿。**T1 可用最小自写 streamable client 满足**,不强依赖 codex 真账号。
- **片二(后续任务)= codex 经 Form B 的真实接入实证**:用真实 codex(ChatGPT 订阅)`codex mcp add lybra --url <gate> --bearer-token-env-var LYBRA_MCP_TOKEN`,在受治 workspace 跑一次真实 claim→/scratch→return→196a→L3 闭环,Owner 带外 confirm,产出证据包。**这是 v1.0 维度 4「≥1 真非-Claude 接入」的达成证据。**
- **接缝**:片一若 T1 直接用 codex 连通成功,则为片二预先消除传输不确定性;但**片一的验收不被片二阻塞**(片一用标准 client 即可 PASS),片二单独立卡、独立审计。

---

## §10 范围外(本片不做)

- 不实现 AIPOS-193 §9 per-op nonce/签名(DG-9 = disclosed-deferred)。
- 不改 controlled-execute 语义、scope 表、confirmer schema、L3、Wall、service_mode 鉴权策略。
- 不做 OAuth 流(codex 支持但 Bearer 已足;OAuth 推迟)。
- 不动 gate 网络姿态(仍本地/桥接网关,非 0.0.0.0)。
- 不进片二(codex 真接入)。

---

> **DRAFT 结束。** 待你复核 + 拍板 §4 第一待决项(SDK vs 手写)+ 可选示意 §5 端点策略(B1 复用 /mcp vs B2 新路径)。拍板后我据此出实现任务(含 §3 第一步兼容探查)→ 实现 → cc glm 独立审计 → 你批准 → finalize。不实现至获批。
