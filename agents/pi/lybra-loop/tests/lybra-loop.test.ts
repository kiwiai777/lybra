/**
 * lybra-loop 本体测试 —— factory 注册 + 命令路由 + 配置/连接红线(headless 可测部分)。
 *
 * 用 mock pi/ctx 直接调 factory,验:
 *  • factory 加载不抛、注册了 lybra/lybra-tick 命令 + 三个事件 handler。
 *  • /lybra on 的配置红线:缺 actor/policy/workspaceRoot ⇒ 报错不启动。
 *  • /lybra on 的连接自检:gate 不可达 ⇒ 报错不启动。
 *  • maxN 解析:0/非整数/非数字 ⇒ 报错;默认 1。
 *  • /lybra off(未运行)、/lybra status、未知子命令 ⇒ 正确反馈。
 *
 * on 的"成功放行+冷启动"路径需真 gate + newSession,无法 headless ⇒ 见 TEST-EVIDENCE 眼验剧本。
 * 跑法:`node tests/lybra-loop.test.ts`。
 */

const NOTES: string[] = [];
let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

// --- mock pi:记录 handler 注册 + sendUserMessage 调用 ---
function makeMockPi() {
  const handlers: Record<string, Function> = {};
  const commands: Record<string, { description: string; handler: Function }> = {};
  const sent: string[] = [];
  return {
    api: {
      on(evt: string, h: Function) {
        handlers[evt] = h;
      },
      registerCommand(name: string, opts: { description: string; handler: Function }) {
        commands[name] = opts;
      },
      sendUserMessage(content: string, _opts?: unknown) {
        sent.push(content);
      },
    } as any,
    handlers,
    commands,
    sent,
  };
}

// --- mock ctx ---
function makeMockCtx() {
  const notifies: Array<{ m: string; l?: string }> = [];
  return {
    ctx: {
      ui: {
        notify: (m: string, l?: string) => notifies.push({ m, l }),
      },
      sessionManager: { getSessionId: () => "test-sess" },
    } as any,
    notifies,
  };
}

const { default: factory } = await import("../lybra-loop.ts");
const { api, handlers, commands, sent } = makeMockPi();

// 执行 factory(模拟 pi 加载)
let factoryThrew = false;
try {
  factory(api);
} catch (e) {
  factoryThrew = true;
  NOTES.push(`factory 抛错:${e}`);
}
check("factory 加载不抛", !factoryThrew);
check("注册了 /lybra 命令", !!commands.lybra);
check("注册了 /lybra-tick 命令", !!commands.lybra && !!commands["lybra-tick"]);
check("注册了 agent_settled handler", typeof handlers["agent_settled"] === "function");
check("注册了 session_shutdown handler", typeof handlers["session_shutdown"] === "function");
check("注册了 session_start handler", typeof handlers["session_start"] === "function");

// 保存/恢复 env
const SAVED_ENV = { ...process.env };
function setEnv(env: Record<string, string | undefined>) {
  // 彻底清当前 process.env 里所有 LYBRA_*(不只快照里的,避免 case 间残留)
  for (const k of Object.keys(process.env)) if (k.startsWith("LYBRA_")) delete process.env[k];
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
}
function clearLybraEnv() {
  setEnv({});
}

// --- /lybra(无参)= status ---
{
  clearLybraEnv();
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("", ctx);
  check("/lybra 无参 → status notify", notifies.length === 1 && notifies[0].m.includes("lybra-loop 状态"));
}

// --- /lybra status ---
{
  clearLybraEnv();
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("status", ctx);
  check("/lybra status → notify 状态", notifies[0].m.includes("运行中"));
  check("/lybra status → 含 已放行 计数", notifies[0].m.includes("已放行"));
}

// --- /lybra off(未运行)---
{
  clearLybraEnv();
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("off", ctx);
  check("/lybra off 未运行 → 提示未运行", notifies[0].m.includes("未在运行"));
}

// --- /lybra 未知子命令 → 用法 ---
{
  clearLybraEnv();
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("foo", ctx);
  check("/lybra foo → 用法提示", notifies[0].m.includes("用法"));
}

// --- maxN 解析 ---
{
  clearLybraEnv();
  setEnv({ LYBRA_ACTOR: "me", LYBRA_MCP_TOKEN: "x", LYBRA_OWNER_POLICY_REF: "p", LYBRA_WORKSPACE_ROOT: "/r", LYBRA_GATE_URL: "http://127.0.0.1:1" });
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("on 0", ctx);
  check("/lybra on 0 → maxN 无效", notifies[0].m.includes("maxN 无效"));
}
{
  setEnv({ LYBRA_ACTOR: "me", LYBRA_MCP_TOKEN: "x", LYBRA_OWNER_POLICY_REF: "p", LYBRA_WORKSPACE_ROOT: "/r", LYBRA_GATE_URL: "http://127.0.0.1:1" });
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("on abc", ctx);
  check("/lybra on abc → maxN 无效", notifies[0].m.includes("maxN 无效"));
}

// --- on 配置红线:缺 actor ---
{
  clearLybraEnv();
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("on", ctx);
  check("on 缺 actor → 配置错误", notifies[0].m.includes("配置错误") && notifies[0].m.includes("LYBRA_ACTOR"));
  check("on 缺 actor → 未启动(notify 是 error)", notifies[0].l === "error");
}
// --- on 配置红线:缺 ownerPolicyRef ---
{
  setEnv({ LYBRA_ACTOR: "me", LYBRA_MCP_TOKEN: "x" });
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("on", ctx);
  check("on 缺 ownerPolicyRef → 配置错误含 LYBRA_OWNER_POLICY_REF", notifies[0].m.includes("LYBRA_OWNER_POLICY_REF"));
}
// --- on 配置红线:缺 workspaceRoot ---
{
  setEnv({ LYBRA_ACTOR: "me", LYBRA_MCP_TOKEN: "x", LYBRA_OWNER_POLICY_REF: "p" });
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("on", ctx);
  check("on 缺 workspaceRoot → 配置错误含 LYBRA_WORKSPACE_ROOT", notifies[0].m.includes("LYBRA_WORKSPACE_ROOT"));
}

// --- on 连接自检:gate 不可达(端口1)⇒ 报错不启动 ---
{
  setEnv({
    LYBRA_ACTOR: "me",
    LYBRA_MCP_TOKEN: "secret-tok",
    LYBRA_OWNER_POLICY_REF: "p",
    LYBRA_WORKSPACE_ROOT: "/r",
    LYBRA_GATE_URL: "http://127.0.0.1:1",
  });
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler("on", ctx);
  const msg = notifies[0]?.m || "";
  check("on gate 不可达 → 连接错误", msg.includes("gate 连接失败"));
  check("on gate 不可达 → 提示 lybra serve", msg.includes("lybra serve"));
  check("on gate 不可达 → error 级别", notifies[0].l === "error");
  // 没有调用 sendUserMessage(因为没启动;F-EXT001-4:FIX1 后 tick 不经 sendUserMessage)
  check("on gate 不可达 → 没有 sendUserMessage 调用", sent.length === 0);
}

// --- on 成功路径无法 headless(需真 gate + newSession),记 NOTE ---
NOTES.push("on 成功(配置齐 + gate 可达 + 信封内卡)→ 冷启动 newSession:需真 gate + pi session,无法 headless。见 TEST-EVIDENCE 眼验剧本。");
NOTES.push("agent_settled 续跑 / 轮询定时器链(F-EXT001-4:FIX1 直接调 doTick) / session_shutdown expectingSwap:依赖 pi 事件循环 + 真 session 替换,无法 headless。见 TEST-EVIDENCE。");

// 恢复 env
clearLybraEnv();
for (const [k, v] of Object.entries(SAVED_ENV)) if (k.startsWith("LYBRA_")) process.env[k] = v;

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
if (NOTES.length) {
  console.log("\n--- NOTES(不可 headless 测的,见 TEST-EVIDENCE)---");
  for (const n of NOTES) console.log(`  • ${n}`);
}
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
