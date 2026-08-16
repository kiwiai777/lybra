/**
 * gate-client 测试 —— config 读取(含红线:token 不回显、缺项即停)+ JSON-RPC(mock transport)
 * + schema 驱动的通用调用器(AIPOS-R6R:callTool 参数校验 / loadVerbCatalog / validateRequiredVerbs)。
 * 跑法:`node tests/gate-client.test.ts`。
 */
import {
  loadConfig,
  ConfigError,
  GateMcpClient,
  GateError,
  tokenFingerprint,
  parseSsePayload,
  loadVerbCatalog,
  validateRequiredVerbs,
  type Transport,
  type VerbCatalog,
} from "../gate-client.ts";
import { writeFileSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

// --- loadConfig:必需项缺失即停(不猜) ---
// 隔离 .lybra 自发现:loadConfig 从 cwd 向上找 .lybra,测试需在无 .lybra 祖先的干净目录跑,
// 否则会找到真 ~/projects/lybra/.lybra 配置使 env 断言失效(AIPOS-R6Q 行为)。
const originalCwd = process.cwd();
const cleanCwd = mkdtempSync(join(tmpdir(), "lybra-clean-cwd-"));
process.chdir(cleanCwd);

function expectConfigError(env: NodeJS.ProcessEnv, label: string, needle: string) {
  try {
    loadConfig(env);
    check(`${label}:应抛 ConfigError 却没抛`, false);
  } catch (e) {
    const ok = e instanceof ConfigError && String(e).includes(needle);
    check(`${label}:抛 ConfigError 含 "${needle}"`, ok);
  }
}

// loadConfig 校验顺序:workspaceRoot → actor → ownerPolicyRef → token(缺更前的项先抛)。
expectConfigError({}, "缺 workspaceRoot", "LYBRA_WORKSPACE_ROOT");
expectConfigError({ LYBRA_WORKSPACE_ROOT: "/r" }, "缺 actor", "LYBRA_ACTOR");
expectConfigError(
  { LYBRA_WORKSPACE_ROOT: "/r", LYBRA_ACTOR: "me" },
  "缺 ownerPolicyRef",
  "LYBRA_OWNER_POLICY_REF",
);
expectConfigError(
  { LYBRA_WORKSPACE_ROOT: "/r", LYBRA_ACTOR: "me", LYBRA_OWNER_POLICY_REF: "p" },
  "缺 token",
  "token",
);

// 正常组装
const ok = loadConfig({
  LYBRA_ACTOR: "me",
  LYBRA_TOKEN: "secret-token",
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
    LYBRA_TOKEN: "x",
    LYBRA_OWNER_POLICY_REF: "p",
    LYBRA_WORKSPACE_ROOT: "/r",
    LYBRA_LOOP_INTERVAL: "10",
  });
  check("interval<30 应拒绝", false);
} catch {
  check("interval<30 拒绝", true);
}

// --- .lybra/connection.json 自发现 + 安全(不回显)---
const discDir = mkdtempSync(join(tmpdir(), "lybra-disc-"));
mkdirSync(join(discDir, ".lybra"), { recursive: true });
writeFileSync(
  join(discDir, ".lybra", "connection.json"),
  JSON.stringify({
    tokens: [
      { role: "owner", token: "owner-secret" },
      { role: "executor", token: "exec-secret-xyz" },
    ],
  }),
);
process.chdir(discDir);
const cfgFromConn = loadConfig({
  LYBRA_ACTOR: "me",
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
process.chdir(discDir);
try {
  loadConfig({
    LYBRA_ACTOR: "me",
    LYBRA_ROLE: "planner",
    LYBRA_OWNER_POLICY_REF: "p",
    LYBRA_WORKSPACE_ROOT: "/r",
  });
  check("connection.json 缺 role 应拒绝", false);
} catch (e) {
  check("connection.json 缺 role 拒绝", e instanceof ConfigError);
}

// 恢复 cwd 并清理(后续 GateMcpClient / loadVerbCatalog 用 import.meta.url 定位, 不受 cwd 影响)
process.chdir(originalCwd);
rmSync(discDir, { recursive: true, force: true });

// --- GateMcpClient:mock transport,验 JSON-RPC 协议 + schema 校验 ---
let lastBody: any = null;
let lastHeaders: Record<string, string> = {};
const mockTransport: Transport = async (_url, body, headers) => {
  lastBody = JSON.parse(body);
  lastHeaders = headers;
  if (lastBody.method === "initialize") {
    const json = JSON.stringify({ jsonrpc: "2.0", id: lastBody.id, result: {} });
    return { status: 200, sessionId: "sess-init", text: `data: ${json}\n\n`, contentType: "text/event-stream; charset=utf-8" };
  }
  // 模拟 gate 对任意 tools/call 返回 structuredContent(SSE 格式)
  const json = JSON.stringify({
    jsonrpc: "2.0",
    id: lastBody.id,
    result: {
      structuredContent:
        lastBody.params.name === "lybra_queue_list"
          ? { data: { tasks: [{ task_id: "T1", queue_state: "pending", metadata: { assigned_to: "me" } }] } }
          : { ok: true, autonomy_mode: "PreAuthorized", preauthorized_release: true },
    },
  });
  return {
    status: 200,
    sessionId: "sess-1",
    text: `data: ${json}\n\n`,
    contentType: "text/event-stream; charset=utf-8",
  };
};

const mockVerbs: VerbCatalog = {
  verbs: {
    lybra_queue_list: { phase: "single", parameters: { properties: {}, required: [] } },
    lybra_queue_claim_dry_run: {
      phase: "dry_run",
      parameters: {
        properties: {
          task_id: { type: "string" },
          actor: { type: "string" },
          agent_instance: { type: "string" },
          autonomy_mode: { type: "string" },
          owner_policy_ref: { type: "string" },
        },
        required: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
      },
    },
    lybra_queue_close_dry_run: {
      phase: "dry_run",
      parameters: {
        properties: { task_id: { type: "string" }, actor: { type: "string" }, closure_evidence: { type: "object" } },
        required: ["task_id", "actor", "closure_evidence"],
      },
    },
  },
};

const client = new GateMcpClient("http://gate:7118/", "my-token", { transport: mockTransport, verbs: mockVerbs });
await client.initialize();
check("initialize 发了 protocolVersion", lastBody?.method === "initialize");
check("Authorization header 带 Bearer", (lastHeaders.Authorization || "").startsWith("Bearer my-token"));
check("Accept 头对", lastHeaders.Accept === "application/json, text/event-stream");

const tasks = await client.queueTasks();
check("queueTasks 解析出 tasks", Array.isArray(tasks) && tasks.length === 1 && tasks[0].task_id === "T1");
check("queueTasks 调了 lybra_queue_list", lastBody?.params?.name === "lybra_queue_list");

const claim = await client.callTool("lybra_queue_claim_dry_run", {
  task_id: "T1",
  actor: "me",
  agent_instance: "me",
  autonomy_mode: "PreAuthorized",
  owner_policy_ref: "pol-1",
});
check("callTool 返回 structuredContent", (claim as any).preauthorized_release === true);

// --- schema 参数校验 ---
let threw = false;
try {
  await client.callTool("lybra_queue_claim_dry_run", { actor: "me" });
} catch (e) {
  threw = e instanceof GateError && String(e).includes("缺必填参数");
}
check("callTool:缺必填参数 → GateError", threw);

threw = false;
try {
  await client.callTool("lybra_queue_claim_dry_run", {
    actor: "me",
    agent_instance: "me",
    autonomy_mode: "PreAuthorized",
    owner_policy_ref: "pol-1",
    bogus_param: 1,
  });
} catch (e) {
  threw = e instanceof GateError && String(e).includes("未定义参数") && String(e).includes("bogus_param");
}
check("callTool:未定义参数 → GateError 含参数名", threw);

threw = false;
try {
  await client.callTool("lybra_queue_close_dry_run", { task_id: "T", actor: "me", closure_evidence: "not-an-object" });
} catch (e) {
  threw = e instanceof GateError && String(e).includes("需 object");
}
check("callTool:closure_evidence 传字符串 → 类型校验拒绝", threw);

threw = false;
try {
  await client.callTool("lybra_nonexistent_verb", {});
} catch (e) {
  threw = e instanceof GateError && String(e).includes("未在 schema 中定义");
}
check("callTool:未知 verb → GateError", threw);

// SSE 错误响应 → GateError(非 JSON 的 SSE data)
const badSseTransport: Transport = async () => ({
  status: 200,
  sessionId: null,
  text: "data: not-json\n\n",
  contentType: "text/event-stream",
});
const badSseClient = new GateMcpClient("http://gate", "t", { transport: badSseTransport, verbs: mockVerbs });
let badSseThrew = false;
try {
  await badSseClient.queueTasks();
} catch (e) {
  badSseThrew = e instanceof GateError && String(e).includes("unparseable");
}
check("SSE 非 JSON data → GateError 含 unparseable", badSseThrew);

// --- parseSsePayload:单元(AIPOS-364)---

const sse1 = parseSsePayload('data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n');
check("parseSsePayload:标准单事件", (sse1 as any).jsonrpc === "2.0");

const sse2 = parseSsePayload(': keepalive\ndata: {"a":1}\n\n');
check("parseSsePayload:忽略 comment", (sse2 as any).a === 1);

const sse3 = parseSsePayload('data: {"x":\ndata: 1}\n\n');
check("parseSsePayload:多行 data 拼接", (sse3 as any).x === 1);

const sse4 = parseSsePayload('data: {"first":true}\n\ndata: {"second":true}\n\n');
check("parseSsePayload:取第一个有效 JSON", (sse4 as any).first === true);

const sse5 = parseSsePayload('data: {"spaced":true}\n\n');
check("parseSsePayload:data 后空格", (sse5 as any).spaced === true);

let sseThrew = false;
try {
  parseSsePayload(": keepalive\n\n");
} catch {
  sseThrew = true;
}
check("parseSsePayload:无 data 事件抛错", sseThrew);

const sse6 = parseSsePayload('data: {"no-trailing-newline":true}');
check("parseSsePayload:末尾无空行", (sse6 as any)["no-trailing-newline"] === true);

// --- loadVerbCatalog / validateRequiredVerbs:真 schema ---
let catalog: VerbCatalog | null = null;
try {
  catalog = loadVerbCatalog();
  check("loadVerbCatalog 加载成功", !!catalog && typeof catalog.verbs === "object" && Object.keys(catalog.verbs).length > 0);
} catch (e) {
  check(`loadVerbCatalog 加载成功(${e})`, false);
}

if (catalog) {
  check("schema 含 close_confirm", "lybra_queue_close_confirm" in catalog.verbs);
  check("schema 含 task_progress", "lybra_task_progress" in catalog.verbs);
  check("schema task_progress 用 event_type(非 status)", "event_type" in (catalog.verbs.lybra_task_progress?.parameters?.properties ?? {}));
  const closeEvidence = catalog.verbs.lybra_queue_close_dry_run?.parameters?.properties?.closure_evidence;
  check("schema closure_evidence 是 object", closeEvidence?.type === "object");

  // validateRequiredVerbs:合法清单 → 不抛
  let okValid = true;
  try {
    validateRequiredVerbs(catalog, {
      lybra_queue_claim_dry_run: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
      lybra_queue_close_dry_run: ["task_id", "actor", "closure_evidence"],
    });
  } catch {
    okValid = false;
  }
  check("validateRequiredVerbs:合法清单不抛", okValid);

  // 缺动词 → 抛
  let missingVerb = false;
  try {
    validateRequiredVerbs(catalog, { lybra_nonexistent_verb: [] });
  } catch (e) {
    missingVerb = e instanceof ConfigError && String(e).includes("缺动词");
  }
  check("validateRequiredVerbs:缺动词 → ConfigError", missingVerb);

  // 改错必填参数名 → 抛(活体验收②的核心:契约来自 schema)
  let wrongParam = false;
  try {
    validateRequiredVerbs(catalog, { lybra_queue_close_dry_run: ["task_id", "actor", "closure_evidence_ref"] });
  } catch (e) {
    wrongParam = e instanceof ConfigError && String(e).includes("closure_evidence_ref");
  }
  check("validateRequiredVerbs:改错必填参数名 → ConfigError", wrongParam);
}

rmSync(cleanCwd, { recursive: true, force: true });

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
