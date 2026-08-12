/**
 * gate-client 测试 —— config 读取(含红线:token 不回显、缺项即停)+ JSON-RPC(mock transport)。
 * 跑法:`node tests/gate-client.test.ts`。
 */
import {
  loadConfig,
  ConfigError,
  GateMcpClient,
  GateError,
  tokenFingerprint,
  parseSsePayload,
  type Transport,
} from "../gate-client.ts";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

// --- loadConfig:必需项缺失即停(不猜) ---

function expectConfigError(env: NodeJS.ProcessEnv, label: string, needle: string) {
  try {
    loadConfig(env);
    check(`${label}:应抛 ConfigError 却没抛`, false);
  } catch (e) {
    const ok = e instanceof ConfigError && String(e).includes(needle);
    check(`${label}:抛 ConfigError 含 "${needle}"`, ok);
  }
}

expectConfigError({}, "缺 actor", "LYBRA_ACTOR");
expectConfigError({ LYBRA_ACTOR: "me" }, "缺 token 与 connection", "token");
expectConfigError(
  { LYBRA_ACTOR: "me", LYBRA_MCP_TOKEN: "x" },
  "缺 ownerPolicyRef",
  "LYBRA_OWNER_POLICY_REF",
);
expectConfigError(
  { LYBRA_ACTOR: "me", LYBRA_MCP_TOKEN: "x", LYBRA_OWNER_POLICY_REF: "p" },
  "缺 workspaceRoot",
  "LYBRA_WORKSPACE_ROOT",
);

// 正常组装
const ok = loadConfig({
  LYBRA_ACTOR: "me",
  LYBRA_MCP_TOKEN: "secret-token",
  LYBRA_OWNER_POLICY_REF: "pol-1",
  LYBRA_WORKSPACE_ROOT: "/root",
  LYBRA_AGENT_INSTANCE: "inst-1",
});
check("loadConfig:gateUrl 默认", ok.gateUrl === "http://127.0.0.1:7118");
check("loadConfig:actor", ok.actor === "me");
check("loadConfig:agentInstance 默认=actor", ok.agentInstance === "inst-1");
check("loadConfig:ownerPolicyRef", ok.ownerPolicyRef === "pol-1");
check("loadConfig:interval 默认 60", ok.intervalSec === 60);

// interval 下限 30
try {
  loadConfig({
    LYBRA_ACTOR: "me",
    LYBRA_MCP_TOKEN: "x",
    LYBRA_OWNER_POLICY_REF: "p",
    LYBRA_WORKSPACE_ROOT: "/r",
    LYBRA_LOOP_INTERVAL: "10",
  });
  check("interval<30 应拒绝", false);
} catch {
  check("interval<30 拒绝", true);
}

// --- connection.json token 读取 + 安全(不回显)---
const tmp = mkdtempSync(join(tmpdir(), "lybra-ext-"));
const connPath = join(tmp, "connection.json");
writeFileSync(
  connPath,
  JSON.stringify({
    tokens: [
      { role: "owner", token: "owner-secret" },
      { role: "executor", token: "exec-secret-xyz" },
    ],
  }),
);
const cfgFromConn = loadConfig({
  LYBRA_ACTOR: "me",
  LYBRA_CONNECTION_JSON: connPath,
  LYBRA_ROLE: "executor",
  LYBRA_OWNER_POLICY_REF: "p",
  LYBRA_WORKSPACE_ROOT: "/r",
});
check("connection.json:读出 executor token", cfgFromConn.token === "exec-secret-xyz");
check("fingerprint 不是原始 token", tokenFingerprint(cfgFromConn.token).startsWith("sha256:"));
check("fingerprint 与 token 不同", tokenFingerprint(cfgFromConn.token) !== cfgFromConn.token);
check("fingerprint 定长前缀", tokenFingerprint("abc").length === "sha256:".length + 12);
check("空 token fingerprint", tokenFingerprint("") === "(none)");

// 缺 role 的 token
try {
  loadConfig({
    LYBRA_ACTOR: "me",
    LYBRA_CONNECTION_JSON: connPath,
    LYBRA_ROLE: "planner",
    LYBRA_OWNER_POLICY_REF: "p",
    LYBRA_WORKSPACE_ROOT: "/r",
  });
  check("connection.json 缺 role 应拒绝", false);
} catch (e) {
  check("connection.json 缺 role 拒绝", e instanceof ConfigError);
}

// --- GateMcpClient:mock transport,验 JSON-RPC 协议 ---
let lastBody: any = null;
let lastHeaders: Record<string, string> = {};
const mockTransport: Transport = async (_url, body, headers) => {
  lastBody = JSON.parse(body);
  lastHeaders = headers;
  // 模拟 gate 对 tools/call lybra_queue_list 的应答(SSE 格式——AIPOS-364:gate 实际返回 text/event-stream)
  if (lastBody.method === "tools/call" && lastBody.params.name === "lybra_queue_list") {
    const json = JSON.stringify({
      jsonrpc: "2.0",
      id: lastBody.id,
      result: {
        structuredContent: {
          data: { tasks: [{ task_id: "T1", queue_state: "pending", metadata: { assigned_to: "me" } }] },
        },
      },
    });
    return {
      status: 200,
      sessionId: "sess-1",
      text: `data: ${json}\n\n`,
      contentType: "text/event-stream; charset=utf-8",
    };
  }
  if (lastBody.method === "tools/call" && lastBody.params.name === "lybra_queue_claim_dry_run") {
    const json = JSON.stringify({
      jsonrpc: "2.0",
      id: lastBody.id,
      result: { structuredContent: { autonomy_mode: "PreAuthorized", owner_confirmation_required: false, preauthorized_release: true } },
    });
    return {
      status: 200,
      sessionId: "sess-1",
      text: `data: ${json}\n\n`,
      contentType: "text/event-stream; charset=utf-8",
    };
  }
  if (lastBody.method === "initialize") {
    const json = JSON.stringify({ jsonrpc: "2.0", id: lastBody.id, result: {} });
    return { status: 200, sessionId: "sess-init", text: `data: ${json}\n\n`, contentType: "text/event-stream; charset=utf-8" };
  }
  const json = JSON.stringify({ jsonrpc: "2.0", id: lastBody.id, result: {} });
  return { status: 200, sessionId: null, text: `data: ${json}\n\n`, contentType: "text/event-stream; charset=utf-8" };
};

const client = new GateMcpClient("http://gate:7118/", "my-token", { transport: mockTransport });
await client.initialize();
check("initialize 发了 protocolVersion", lastBody?.method === "initialize");
check("Authorization header 带 Bearer", (lastHeaders.Authorization || "").startsWith("Bearer my-token"));
check("Accept 头对", lastHeaders.Accept === "application/json, text/event-stream");

// SSE 错误响应 → GateError(非 JSON 的 SSE data)
const badSseTransport: Transport = async () => ({
  status: 200,
  sessionId: null,
  text: "data: not-json\n\n",
  contentType: "text/event-stream",
});
const badSseClient = new GateMcpClient("http://gate", "t", { transport: badSseTransport });
let badSseThrew = false;
try {
  await badSseClient.queueTasks();
} catch (e) {
  badSseThrew = e instanceof GateError && String(e).includes("unparseable");
}
check("SSE 非 JSON data → GateError 含 unparseable", badSseThrew);

const tasks = await client.queueTasks();
check("queueTasks 解析出 tasks", Array.isArray(tasks) && tasks.length === 1 && tasks[0].task_id === "T1");
check("queueTasks 调了 lybra_queue_list", lastBody?.params?.name === "lybra_queue_list");

const claim = await client.claimDryRun({ task_id: "T1", actor: "me" });
check("claimDryRun 返回 structuredContent", (claim as any).preauthorized_release === true);

// --- parseSsePayload:单元(AIPOS-364)---

// 标准单事件
const sse1 = parseSsePayload('data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n');
check("parseSsePayload:标准单事件", (sse1 as any).jsonrpc === "2.0");

// 带 keepalive comment
const sse2 = parseSsePayload(': keepalive\ndata: {"a":1}\n\n');
check("parseSsePayload:忽略 comment", (sse2 as any).a === 1);

// 多行 data(SSE 规范:多个 data: 行用 \n 拼接)
const sse3 = parseSsePayload('data: {"x":\ndata: 1}\n\n');
check("parseSsePayload:多行 data 拼接", (sse3 as any).x === 1);

// 多个事件取第一个 JSON
const sse4 = parseSsePayload('data: {"first":true}\n\ndata: {"second":true}\n\n');
check("parseSsePayload:取第一个有效 JSON", (sse4 as any).first === true);

// data: 后有空格(SSE 规范去前导单空格)
const sse5 = parseSsePayload('data: {"spaced":true}\n\n');
check("parseSsePayload:data 后空格", (sse5 as any).spaced === true);

// 无 data 事件 → 抛错
let sseThrew = false;
try {
  parseSsePayload(": keepalive\n\n");
} catch {
  sseThrew = true;
}
check("parseSsePayload:无 data 事件抛错", sseThrew);

// 末尾无空行但有 data
const sse6 = parseSsePayload('data: {"no-trailing-newline":true}');
check("parseSsePayload:末尾无空行", (sse6 as any)["no-trailing-newline"] === true);

// --- JSON 直返(content-type=application/json 路径仍可用)---
const jsonTransport: Transport = async (_u, body) => {
  const b = JSON.parse(body);
  return {
    status: 200,
    sessionId: null,
    text: JSON.stringify({ jsonrpc: "2.0", id: b.id, result: { ok: true } }),
    contentType: "application/json",
  };
};
const jsonClient = new GateMcpClient("http://gate", "t", { transport: jsonTransport });
await jsonClient.initialize();
check("JSON content-type 直解仍可用", true);

// HTTP 错误 → GateError
const errTransport: Transport = async () => ({ status: 500, sessionId: null, text: "boom", contentType: "text/plain" });
const errClient = new GateMcpClient("http://gate", "t", { transport: errTransport });
let threw = false;
try {
  await errClient.queueTasks();
} catch (e) {
  threw = e instanceof GateError;
}
check("HTTP 500 → GateError", threw);

// JSON-RPC error → GateError
const rpcErrTransport: Transport = async (_u, body) => {
  const b = JSON.parse(body);
  return {
    status: 200,
    sessionId: null,
    text: `data: ${JSON.stringify({ jsonrpc: "2.0", id: b.id, error: { code: -1, message: "scope denied" } })}\n\n`,
    contentType: "text/event-stream",
  };
};
const rpcErrClient = new GateMcpClient("http://gate", "t", { transport: rpcErrTransport });
let rpcThrew = false;
try {
  await rpcErrClient.queueTasks();
} catch (e) {
  rpcThrew = e instanceof GateError && String(e).includes("scope denied");
}
check("JSON-RPC error → GateError 含 message", rpcThrew);

rmSync(tmp, { recursive: true, force: true });

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
