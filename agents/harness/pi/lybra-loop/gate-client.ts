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
import { readFileSync, existsSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
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
// 动词 catalog:verb 名/参数 shape/两阶段语义的单一源 = schema/verbs.schema.json。
// 连接器不再逐动词手写方法/参数名:verb 名由调用方传入,参数 shape 读 schema。
// ---------------------------------------------------------------------------

export interface VerbParamSchema {
  type?: "string" | "integer" | "boolean" | "array" | "object";
  description?: string;
  $enum?: string;
  properties?: Record<string, VerbParamSchema>;
  items?: { type?: string };
}

export interface VerbDefinition {
  description?: string;
  phase?: "single" | "dry_run" | "confirm";
  base_verb?: string;
  confirm_verb?: string;
  dry_run_verb?: string;
  confirm_via?: "dry_run_token" | "replay_args";
  required_scope?: string | null;
  parameters?: {
    type?: string;
    properties?: Record<string, VerbParamSchema>;
    required?: string[];
    additionalProperties?: boolean;
  };
}

export interface VerbCatalog {
  schema_version?: string;
  verbs: Record<string, VerbDefinition>;
}

/** 定位 schema 目录(env 优先 → 模块目录向上找 schema/ → cwd 向上找 → 默认产品仓)。 */
function findSchemaJson(fileName: string, schemaDir?: string): string {
  const candidates: string[] = [];
  if (schemaDir) candidates.push(schemaDir);
  const envDir = process.env.LYBRA_SCHEMA_DIR?.trim();
  if (envDir) candidates.push(envDir);
  try {
    candidates.push(dirname(fileURLToPath(import.meta.url)));
  } catch {
    // import.meta.url 不可用(罕见)→ 跳过
  }
  candidates.push(process.cwd());
  for (const start of candidates) {
    let dir = start;
    for (let i = 0; i < 10; i++) {
      const f = join(dir, "schema", fileName);
      if (existsSync(f)) return f;
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  const home = process.env.HOME || "";
  if (home) {
    const f = join(home, "projects", "lybra", "schema", fileName);
    if (existsSync(f)) return f;
  }
  throw new ConfigError(`未找到 ${fileName}(设 LYBRA_SCHEMA_DIR 指向 schema 目录, 或从 lybra 产品仓运行)`);
}

function findSchemaFile(schemaDir?: string): string {
  return findSchemaJson("verbs.schema.json", schemaDir);
}

/**
 * AIPOS-C2 大项A: 加载 config.schema.json —— 身份配置域的唯一真相。
 * loadConfig 依赖它: gate_url 唯一允许的 schema 缺省 (urls.gate_local) 从这里出。
 */
export function loadConfigSchema(): { urls?: { gate_local?: string } } {
  const file = findSchemaJson("config.schema.json");
  let raw: string;
  try {
    raw = readFileSync(file, "utf-8");
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new ConfigError(`读 config.schema.json 失败(${file}): ${msg}`);
  }
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new ConfigError(`config.schema.json 非合法 JSON(${file}): ${msg}`);
  }
  if (!data || typeof data !== "object") {
    throw new ConfigError(`config.schema.json 非对象(${file})`);
  }
  return data as { urls?: { gate_local?: string } };
}

/** 加载 verb catalog(启动即读 schema;schema 缺/坏 → ConfigError)。 */
export function loadVerbCatalog(schemaDir?: string): VerbCatalog {
  const file = findSchemaFile(schemaDir);
  let raw: string;
  try {
    raw = readFileSync(file, "utf-8");
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new ConfigError(`读 verbs.schema.json 失败(${file}): ${msg}`);
  }
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new ConfigError(`verbs.schema.json 非合法 JSON(${file}): ${msg}`);
  }
  if (!data || typeof data !== "object" || !(data as { verbs?: unknown }).verbs || typeof (data as { verbs: unknown }).verbs !== "object") {
    throw new ConfigError(`verbs.schema.json 缺 verbs 表(${file})`);
  }
  return data as VerbCatalog;
}

/**
 * 加载期校验:连接器依赖的每个动词及其必填参数必须在 schema 中存在。
 * schema 缺动词 / 改错必填参数名 → 启动即抛 ConfigError(禁运行时才炸)。
 */
export function validateRequiredVerbs(catalog: VerbCatalog, required: Record<string, string[]>): void {
  const verbs = catalog.verbs;
  for (const [verbName, params] of Object.entries(required)) {
    const verb = verbs[verbName];
    if (!verb) {
      throw new ConfigError(`verbs.schema 缺动词 ${verbName}(连接器依赖,启动即停)`);
    }
    const props = verb.parameters?.properties ?? {};
    for (const p of params) {
      if (!(p in props)) {
        throw new ConfigError(`verbs.schema 动词 ${verbName} 缺必填参数 ${p}(连接器依赖,启动即停)`);
      }
    }
  }
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
  private _verbs: VerbCatalog | null = null;
  private readonly _schemaDir: string | null;
  readonly baseUrl: string;
  readonly token: string;

  constructor(
    baseUrl: string,
    token: string,
    opts: { transport?: Transport; timeoutMs?: number; verbs?: VerbCatalog; schemaDir?: string } = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
    this._timeoutMs = opts.timeoutMs ?? 10000;
    this._transport = opts.transport ?? nodeTransport;
    this._verbs = opts.verbs ?? null;
    this._schemaDir = opts.schemaDir ?? null;
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

  /** 通用调用器:按 schema 校验参数后发 tools/call,返回 structuredContent。 */
  async callTool(name: string, args: AnyDict): Promise<AnyDict> {
    const catalog = this._verbs ?? (this._verbs = loadVerbCatalog(this._schemaDir ?? undefined));
    const verb = catalog.verbs[name];
    if (!verb) {
      throw new GateError(`verb 未在 schema 中定义: ${name}`);
    }
    const params = verb.parameters ?? {};
    const properties = (params.properties ?? {}) as Record<string, VerbParamSchema>;
    const required = params.required ?? [];
    const missing = required.filter((p) => args[p] === undefined || args[p] === null);
    if (missing.length) {
      throw new GateError(`verb ${name} 缺必填参数: ${missing.join(", ")}`);
    }
    const unknown = Object.keys(args).filter((k) => !(k in properties));
    if (unknown.length) {
      throw new GateError(`verb ${name} 含 schema 未定义参数: ${unknown.join(", ")}`);
    }
    for (const [k, v] of Object.entries(args)) {
      if (v === undefined || v === null) continue;
      const spec = properties[k];
      if (!spec) continue;
      const t = spec.type;
      if (t === "object" && (typeof v !== "object" || Array.isArray(v))) {
        throw new GateError(`verb ${name} 参数 ${k} 需 object, 实为 ${Array.isArray(v) ? "array" : typeof v}`);
      }
      if (t === "array" && !Array.isArray(v)) {
        throw new GateError(`verb ${name} 参数 ${k} 需 array, 实为 ${typeof v}`);
      }
      if (t === "integer" && typeof v !== "number") {
        throw new GateError(`verb ${name} 参数 ${k} 需 integer, 实为 ${typeof v}`);
      }
      if (t === "boolean" && typeof v !== "boolean") {
        throw new GateError(`verb ${name} 参数 ${k} 需 boolean, 实为 ${typeof v}`);
      }
      if (t === "string" && typeof v !== "string") {
        throw new GateError(`verb ${name} 参数 ${k} 需 string, 实为 ${typeof v}`);
      }
    }
    const result = await this._rpc("tools/call", { name, arguments: args });
    if (!result || typeof result !== "object") {
      throw new GateError(`${name} 返回无 result`);
    }
    const structured = (result as { structuredContent?: unknown }).structuredContent;
    if (!structured || typeof structured !== "object") {
      throw new GateError(`${name} 返回无 structuredContent`);
    }
    return structured as AnyDict;
  }

  /** 唯一读面(对齐 agent_connector:queue_tasks = lybra_queue_list)。走 schema 通用调用器。 */
  async queueTasks(): Promise<AnyDict[]> {
    const structured = await this.callTool("lybra_queue_list", {});
    const data = (structured as { data?: unknown }).data;
    const tasks = data && typeof data === "object" ? (data as { tasks?: unknown }).tasks : undefined;
    return Array.isArray(tasks) ? (tasks as AnyDict[]) : [];
  }
}

// ---------------------------------------------------------------------------
// 配置读取:env + connection.json(SKILL.md:LYBRA_MCP_TOKEN 或 ~/.lybra/local/connection.json)
// ---------------------------------------------------------------------------

export interface ProvenanceEntry {
  key: string;
  value: string;
  source: string;
  viaEnv: boolean;         // env 兜底命中 (横幅标 ⚠)
  envDowngraded: boolean;  // env 有值但被更高层压过 (横幅标 ⚠)
}

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
  timeoutMs: number; // AIPOS-R8B 大项C②: 超时预算可配置
  provenance: Record<string, ProvenanceEntry>; // AIPOS-C2 大项C: 每个键的来源自曝
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
 * 每个身份/连接键找过的层 (config.schema#identity_resolution 声明). 用于出声并停的报错。
 */
const RESOLUTION_LAYERS: Record<string, string> = {
  workspace_root: "显式参数 → 工位 .lybra/connection.json(workspace_root) → env:LYBRA_WORKSPACE_ROOT",
  role: "显式参数 → 工位 .lybra/role(role) → env:LYBRA_ROLE",
  actor: "显式参数 → 工位 .lybra/role(instance) → .lybra/actor → env:LYBRA_ACTOR",
  agent_instance: "显式参数 → 工位 .lybra/role(instance) → env:LYBRA_AGENT_INSTANCE",
  owner_policy_ref: "显式参数 → 工位 .lybra/role(owner_policy_ref) → .lybra/policy → env:LYBRA_OWNER_POLICY_REF",
  gate_url: "显式参数 → 工位 .lybra/connection.json(mcp.rpc_url) → env:LYBRA_GATE_URL → schema:urls.gate_local",
  token: "显式参数 → 工位 .lybra/connection.json(tokens, instance/role 匹配) → env:LYBRA_TOKEN",
};

/**
 * 从 env 组装配置。必需项缺失 ⇒ ConfigError(本扩展绝不猜 actor/policy/role 等)。
 *
 * AIPOS-C2 大项A: 解析优先级由 config.schema#identity_resolution 声明 (显式 → 工位 .lybra → env 仅兜底),
 * loader 照声明执行, 删除全部硬编码缺省 (尤其 `|| "executor"`)。
 * 任何必填键解析不到 = 出声并停 (报缺哪个键 + 找过哪几层), 禁静默缺省。
 * 同时自曝每个键的来源 (provenance), 供 /lybra on 启动横幅打印 (大项C)。
 */
export function loadConfig(env: NodeJS.ProcessEnv): LoopConfig {
  // config.schema 是身份配置域唯一真相: gate_url 唯一 schema 缺省 (urls.gate_local) 从这里出。
  const schema = loadConfigSchema();
  const schemaGateUrl = schema.urls?.gate_local;

  const resolved = ConnectionResolver.resolveIdentity({ env, schemaGateUrl });

  // 出声并停: 必填键解析不到 → ConfigError (报缺哪个键 + 找过哪几层)。顺序对齐旧实现。
  const required = [
    resolved.workspaceRoot,
    resolved.role,
    resolved.actor,
    resolved.agentInstance,
    resolved.ownerPolicyRef,
    resolved.gateUrl,
    resolved.token,
  ];
  for (const r of required) {
    if (!r.value) {
      throw new ConfigError(
        `身份配置缺键 ${r.key}: ${RESOLUTION_LAYERS[r.key] ?? "显式参数/工位 .lybra/env"} 均未解析到 (来源=${r.source})。` +
        `禁止静默缺省。声明见 config.schema#identity_resolution.keys.${r.key}。`
      );
    }
  }

  const gateUrl = resolved.gateUrl.value!.replace(/\/mcp$/, "");
  const token = resolved.token.value!;

  const prov = (r: typeof resolved.role, displayValue: string): ProvenanceEntry => ({
    key: r.key,
    value: displayValue,
    source: r.source,
    viaEnv: r.viaEnv,
    envDowngraded: r.envDowngraded,
  });

  return {
    gateUrl,
    token,
    role: resolved.role.value!,
    actor: resolved.actor.value!,
    agentInstance: resolved.agentInstance.value!,
    intervalSec: envInt(env, "LYBRA_LOOP_INTERVAL", 60, 30),
    maxWaitSec: envInt(env, "LYBRA_LOOP_MAX_WAIT", 1800, 1),
    workspaceRoot: resolved.workspaceRoot.value!,
    ownerPolicyRef: resolved.ownerPolicyRef.value!,
    // AIPOS-R8B 大项C②: 超时预算可配,默认 30s(实测 claim 4.07s, 留足余量)
    timeoutMs: envInt(env, "LYBRA_GATE_TIMEOUT_MS", 30000, 5000),
    provenance: {
      workspace_root: prov(resolved.workspaceRoot, resolved.workspaceRoot.value!),
      role: prov(resolved.role, resolved.role.value!),
      actor: prov(resolved.actor, resolved.actor.value!),
      agent_instance: prov(resolved.agentInstance, resolved.agentInstance.value!),
      owner_policy_ref: prov(resolved.ownerPolicyRef, resolved.ownerPolicyRef.value!),
      gate_url: prov(resolved.gateUrl, resolved.gateUrl.value!),
      token: prov(resolved.token, tokenFingerprint(token)), // token 永不回显, 只出 fingerprint
    },
  };
}
