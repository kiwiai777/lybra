/**
 * gate-client —— Lybra gate 的 Streamable-HTTP MCP 客户端(agent 侧 pull)。
 *
 * 与 tools/aipos_cli/confirm_client.py:GateClient 同协议(JSON-RPC over HTTP /mcp),
 * 用 node:http/https 直连,**不 shell 调用 Python CLI**(理由见 DESIGN.md §连接器面)。
 *
 * 红线对齐 agent_connector.py:
 *  • 唯一读面 = queue_list(lybra_queue_list);claim 走 claim_dry_run。
 *  • 本模块不含任何 confirm 调用(confirm 是 Owner 动作,executor token 没有 owner_confirm scope)。
 *  • token 进程内使用,永不回显;日志只出 fingerprint(sha256 前 12 位)。
 *
 * transport 可注入(测试传 mock,生产传 node http)。
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { ConnectionResolver } from "./loop-context.ts";

export type AnyDict = Record<string, unknown>;

const ACCEPT_STREAMABLE = "application/json, text/event-stream";
const SESSION_HEADER = "Mcp-Session-Id";
const PROTOCOL_VERSION = "2025-03-26";

/** token 的非秘密指纹(永不输出原始 token)。对齐 confirm_client.token_fingerprint。 */
export function tokenFingerprint(token: string): string {
  if (!token) return "(none)";
  return "sha256:" + createHash("sha256").update(token).digest("hex").slice(0, 12);
}

// ---------------------------------------------------------------------------
// transport:可注入的 HTTP POST。生产用 node http/https;测试传 mock。
// ---------------------------------------------------------------------------

export type Transport = (
  url: string,
  body: string,
  headers: Record<string, string>,
  timeoutMs: number,
) => Promise<{ status: number; sessionId: string | null; text: string; contentType: string }>;

function nodeTransport(
  url: string,
  body: string,
  headers: Record<string, string>,
  timeoutMs: number,
): Promise<{ status: number; sessionId: string | null; text: string; contentType: string }> {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === "https:" ? https : http;
    // AIPOS-R6H: 禁用环境代理（trust_env=False 同义），防止代理劫持 gate 直连
    const agent = lib === https ? new https.Agent({ proxy: false as any }) : new http.Agent({ proxy: false as any });
    const req = lib.request(
      url,
      { 
        method: "POST", 
        headers: { ...headers, "Content-Length": Buffer.byteLength(body) },
        agent, // 使用禁用代理的 agent
      },
      (res) => {
        let text = "";
        res.setEncoding("utf-8");
        res.on("data", (c: string) => (text += c));
        res.on("end", () => {
          const sessionId = res.headers[SESSION_HEADER.toLowerCase()] ?? null;
          const contentType = String(res.headers["content-type"] ?? "");
          resolve({ status: res.statusCode ?? 0, sessionId: sessionId ? String(sessionId) : null, text, contentType });
        });
      },
    );
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error(`gate timeout after ${timeoutMs}ms`)));
    req.write(body);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// SSE de-frame:解析 MCP-over-SSE 的 `data:` 帧,取出 JSON-RPC payload。
// AIPOS-364:gate 返回 text/event-stream,格式 `data: {json}\n\n`。
// 兼容多行 data(每个 `data:` 行拼接,空行分隔事件)和 comment(`:` 开头)。
// ---------------------------------------------------------------------------

/**
 * 从 SSE 文本中提取第一个含 JSON 的 data 事件并解析为对象。
 * SSE 规范:https://html.spec.whatwg.org/multipage/server-sent-events.html
 *  - `:` 开头 = comment(忽略)
 *  - `data:` 开头 = 数据行(多行 data 用 \n 拼接)
 *  - 空行 = 事件边界(派发已累积的 data)
 *  - `event:` / `id:` / `retry:` = 其他字段(本场景只需 data)
 */
export function parseSsePayload(text: string): unknown {
  const lines = text.split("\n");
  let dataBuffer = "";
  const events: string[] = [];

  for (const line of lines) {
    if (line === "") {
      // 空行 = 事件边界;如果已累积 data,派发一个事件
      if (dataBuffer) {
        events.push(dataBuffer);
        dataBuffer = "";
      }
      continue;
    }
    if (line.startsWith(":")) {
      // comment,忽略(keepalive 等)
      continue;
    }
    if (line.startsWith("data:")) {
      const value = line.slice(5);
      // SSE 规范:如果 value 以空格开头,去掉前导空格(单个)
      const payload = value.startsWith(" ") ? value.slice(1) : value;
      dataBuffer += (dataBuffer ? "\n" : "") + payload;
    }
    // event: / id: / retry: 等其它字段在本场景不需要处理
  }
  // 末尾无空行但有 data 的情况
  if (dataBuffer) {
    events.push(dataBuffer);
  }

  if (events.length === 0) {
    throw new Error("SSE stream contained no data events");
  }

  // 取第一个含有效 JSON 的事件(MCP 单请求-单响应场景)
  for (const eventData of events) {
    const trimmed = eventData.trim();
    if (!trimmed) continue;
    try {
      return JSON.parse(trimmed);
    } catch {
      // 不是 JSON,可能是 keepalive 或非 JSON-RPC 事件;继续尝试下一个
    }
  }
  throw new Error(`SSE data events contained no valid JSON: ${events[0]?.slice(0, 200) ?? "(empty)"}`);
}

// ---------------------------------------------------------------------------
// GateMcpClient
// ---------------------------------------------------------------------------

export class GateError extends Error {}

export class GateMcpClient {
  private _sessionId: string | null = null;
  private _nextId = 0;
  private readonly _timeoutMs: number;
  private readonly _transport: Transport;
  readonly baseUrl: string;
  readonly token: string;

  constructor(
    baseUrl: string,
    token: string,
    opts: { transport?: Transport; timeoutMs?: number } = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
    this._timeoutMs = opts.timeoutMs ?? 10000;
    this._transport = opts.transport ?? nodeTransport;
  }

  get tokenFingerprint(): string {
    return tokenFingerprint(this.token);
  }

  private async _rpc(method: string, params: AnyDict | null): Promise<AnyDict | null> {
    this._nextId += 1;
    const body = JSON.stringify({ jsonrpc: "2.0", id: this._nextId, method, params: params ?? {} });
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
      Accept: ACCEPT_STREAMABLE,
      "Content-Type": "application/json",
    };
    if (this._sessionId) headers[SESSION_HEADER] = this._sessionId;
    const resp = await this._transport(`${this.baseUrl}/mcp`, body, headers, this._timeoutMs);
    if (resp.status >= 400) {
      throw new GateError(`gate HTTP ${resp.status}: ${resp.text.slice(0, 200)}`);
    }
    if (resp.sessionId) this._sessionId = resp.sessionId;
    // AIPOS-364:gate 可能返回 text/event-stream(MCP-over-SSE)或 application/json。
    // 按 Content-Type 选择解析路径;SSE 需 de-frame `data:` 取 JSON-RPC payload。
    let payload: unknown;
    const ct = (resp.contentType || "").toLowerCase();
    try {
      if (ct.includes("text/event-stream")) {
        payload = parseSsePayload(resp.text);
      } else {
        payload = JSON.parse(resp.text);
      }
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      throw new GateError(`gate returned unparseable response (${ct || "no content-type"}): ${detail}; raw: ${resp.text.slice(0, 200)}`);
    }
    if (payload && typeof payload === "object" && (payload as { error?: unknown }).error) {
      const msg = (payload as { error: { message?: unknown } }).error.message;
      throw new GateError(String(msg ?? (payload as { error: unknown }).error));
    }
    return payload && typeof payload === "object" ? ((payload as { result?: unknown }).result as AnyDict) ?? null : null;
  }

  async initialize(): Promise<void> {
    await this._rpc("initialize", { protocolVersion: PROTOCOL_VERSION });
  }

  /** 唯一读面(对齐 agent_connector:queue_tasks = lybra_queue_list)。 */
  async queueTasks(): Promise<AnyDict[]> {
    const result = await this._rpc("tools/call", { name: "lybra_queue_list", arguments: {} });
    if (!result || typeof result !== "object") return [];
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") return [];
    const data = (structured as { data?: unknown }).data;
    const tasks = data && typeof data === "object" ? (data as { tasks?: unknown }).tasks : undefined;
    return Array.isArray(tasks) ? (tasks as AnyDict[]) : [];
  }

  /** claim dry-run(对齐 confirm_client._CLAIM_DRY_RUN)。返回 structuredContent。 */
  async claimDryRun(args: AnyDict): Promise<AnyDict> {
    const result = await this._rpc("tools/call", { name: "lybra_queue_claim_dry_run", arguments: args });
    if (!result || typeof result !== "object") {
      throw new GateError("claim_dry_run returned no result");
    }
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") {
      throw new GateError("claim_dry_run returned no structuredContent");
    }
    return structured as AnyDict;
  }

  /** AIPOS-363F1: task preview(取完整卡 markdown,include_body=true)。返回 structuredContent。 */
  async taskPreview(args: AnyDict): Promise<AnyDict> {
    const result = await this._rpc("tools/call", { name: "lybra_task_preview", arguments: args });
    if (!result || typeof result !== "object") {
      throw new GateError("task_preview returned no result");
    }
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") {
      throw new GateError("task_preview returned no structuredContent");
    }
    return structured as AnyDict;
  }

  /** AIPOS-CONN-LOOP-2 ①: queue_return dry-run。返回 structuredContent。 */
  async returnDryRun(args: AnyDict): Promise<AnyDict> {
    const result = await this._rpc("tools/call", { name: "lybra_queue_return_dry_run", arguments: args });
    if (!result || typeof result !== "object") {
      throw new GateError("queue_return_dry_run returned no result");
    }
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") {
      throw new GateError("queue_return_dry_run returned no structuredContent");
    }
    return structured as AnyDict;
  }

  /** AIPOS-CONN-LOOP-2 ①: queue_return confirm (executor自确认,328机制)。返回 structuredContent。 */
  async returnConfirm(args: AnyDict): Promise<AnyDict> {
    const result = await this._rpc("tools/call", { name: "lybra_queue_return_confirm", arguments: args });
    if (!result || typeof result !== "object") {
      throw new GateError("queue_return_confirm returned no result");
    }
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") {
      throw new GateError("queue_return_confirm returned no structuredContent");
    }
    return structured as AnyDict;
  }

  /** AIPOS-R6P 靶①: queue_close dry-run。返回 structuredContent。 */
  async queueCloseDryRun(args: AnyDict): Promise<AnyDict> {
    const result = await this._rpc("tools/call", { name: "lybra_queue_close_dry_run", arguments: args });
    if (!result || typeof result !== "object") {
      throw new GateError("queue_close_dry_run returned no result");
    }
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") {
      throw new GateError("queue_close_dry_run returned no structuredContent");
    }
    return structured as AnyDict;
  }

  /** AIPOS-R6P 靶①: queue_close confirm。返回 structuredContent。 */
  async queueCloseConfirm(args: AnyDict): Promise<AnyDict> {
    const result = await this._rpc("tools/call", { name: "lybra_queue_close_confirm", arguments: args });
    if (!result || typeof result !== "object") {
      throw new GateError("queue_close_confirm returned no result");
    }
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") {
      throw new GateError("queue_close_confirm returned no structuredContent");
    }
    return structured as AnyDict;
  }
}

// ---------------------------------------------------------------------------
// 配置读取:env + connection.json(SKILL.md:LYBRA_MCP_TOKEN 或 ~/.lybra/local/connection.json)
// ---------------------------------------------------------------------------

export interface LoopConfig {
  gateUrl: string;
  token: string; // 进程内使用,调用方负责不回显
  role: string;
  actor: string;
  agentInstance: string;
  intervalSec: number;
  maxWaitSec: number;
  workspaceRoot: string;
  ownerPolicyRef: string;
}

export class ConfigError extends Error {}

function envInt(env: NodeJS.ProcessEnv, key: string, fallback: number, min: number): number {
  const raw = env[key];
  if (raw === undefined || raw === "") return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < min) {
    throw new ConfigError(`${key}=${raw} 无效(需 ≥ ${min} 的数字)`);
  }
  return n;
}



/**
 * 从 env 组装配置。必需项缺失 ⇒ ConfigError(本扩展绝不猜 actor/policy 等)。
 * 
 * AIPOS-R6O 真接线:复用 ConnectionResolver,优先级对齐 Python LoopContext:
 *   显式参数(最高) → .lybra自发现 → env覆盖(最低)
 * 
 * env 表见 DESIGN.md §配置。
 */
export function loadConfig(env: NodeJS.ProcessEnv): LoopConfig {
  // 1. 解析 workspace_root (需要它才能自发现 .lybra/)
  // AIPOS-R6P 靶③: loadConfig 用于 gate/loop,需要治理工作区语义
  const workspaceRoot = ConnectionResolver.resolveGateWorkspace({ env });
  if (!workspaceRoot) {
    throw new ConfigError("LYBRA_WORKSPACE_ROOT 未设置(gate workspace 根,卡 path 相对它拼绝对路径)");
  }

  // 2. 解析 role (用于 token 匹配)
  const role = (env.LYBRA_ROLE || "executor").trim();

  // 3. 解析 actor (显式参数 → .lybra/role → .lybra/actor → env)
  const actor = ConnectionResolver.resolveActor({ workspaceRoot, env });
  if (!actor) {
    throw new ConfigError(
      "LYBRA_ACTOR 未设置(你的 actor 名,需与卡 assigned_to/agent_instance 对得上)。" +
      "设置 LYBRA_ACTOR env,或确保 .lybra/role 文件含 instance 字段。"
    );
  }

  const agentInstance = (env.LYBRA_AGENT_INSTANCE || actor).trim();

  // 4. 解析 owner_policy_ref (显式参数 → .lybra/role → .lybra/policy → env)
  const ownerPolicyRef = ConnectionResolver.resolveOwnerPolicyRef({ workspaceRoot, env });
  if (!ownerPolicyRef) {
    throw new ConfigError(
      "LYBRA_OWNER_POLICY_REF 未设置(PreAuthorized claim 需指向 Owner 签发的信封 policy id;信封外卡会被跳过)。" +
      "设置 LYBRA_OWNER_POLICY_REF env,或确保 .lybra/role 文件含 owner_policy_ref 字段。"
    );
  }

  // 5. 解析 gate_url (显式参数 → .lybra/connection.json → env)
  let gateUrl = ConnectionResolver.resolveGateUrl({ workspaceRoot, env });
  // ConnectionResolver.resolveGateUrl 返回完整 URL 含 /mcp 后缀,需去掉(GateMcpClient.baseUrl 会自己拼)
  gateUrl = gateUrl.replace(/\/mcp$/, "");

  // 6. 解析 token (显式参数 → .lybra/connection.json[role/instance匹配] → env)
  let token: string;
  try {
    token = ConnectionResolver.resolveToken({ workspaceRoot, role, agentInstance, env });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new ConfigError(
      `无 token:${msg}。设 LYBRA_TOKEN env,或确保 .lybra/connection.json 含 role=${role} 的 token。`
    );
  }

  return {
    gateUrl,
    token,
    role,
    actor,
    agentInstance,
    intervalSec: envInt(env, "LYBRA_LOOP_INTERVAL", 60, 30),
    maxWaitSec: envInt(env, "LYBRA_LOOP_MAX_WAIT", 1800, 1),
    workspaceRoot,
    ownerPolicyRef,
  };
}
