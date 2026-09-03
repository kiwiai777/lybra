/**
 * lybra-loop 本体测试 —— factory 注册 + 降级回声行为(AIPOS-F65C 件②)。
 *
 * AIPOS-F65C 件②: lybra-loop 已封存,/lybra 任意子命令均返回降级提示(引导用户使用 CLI)。
 * 旧循环行为(on/off/status/maxN/配置红线/连接自检)已退役,对应测试已删除。
 *
 * 用 mock pi/ctx 直接调 factory,验:
 *  • factory 加载不抛、注册了 lybra/lybra-tick 命令 + 三个事件 handler。
 *  • /lybra <任意子命令> → 封存提示文案(引导使用 lybra CLI)。
 *
 * 跑法:`node tests/lybra-loop.test.ts`。
 */

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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
// 隔离 .lybra 自发现:loadConfig 从 cwd 向上找 .lybra,测试需在无 .lybra 祖先的干净目录跑,
// 否则会找到真 ~/projects/lybra/.lybra 配置使 env 断言失效(AIPOS-R6Q 行为)。
const originalCwd = process.cwd();
const cleanCwd = mkdtempSync(join(tmpdir(), "lybra-loop-clean-cwd-"));
process.chdir(cleanCwd);
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

// --- AIPOS-F65C 件②: /lybra 任意子命令 → 封存提示 ---
const deprecationTestCases = [
  { args: "", desc: "/lybra 无参" },
  { args: "on", desc: "/lybra on" },
  { args: "off", desc: "/lybra off" },
  { args: "status", desc: "/lybra status" },
  { args: "sync", desc: "/lybra sync" },
  { args: "enroll test-code", desc: "/lybra enroll" },
  { args: "unknown", desc: "/lybra unknown" },
];

for (const { args, desc } of deprecationTestCases) {
  clearLybraEnv();
  const { ctx, notifies } = makeMockCtx();
  await commands.lybra.handler(args, ctx);
  
  const hasDeprecationNotice = notifies.some(n => 
    n.m.includes("已封存") || n.m.includes("AIPOS-F65C")
  );
  const hasCLIGuidance = notifies.some(n => 
    n.m.includes("lybra CLI") || n.m.includes("lybra next") || n.m.includes("lybra queue")
  );
  const isWarnLevel = notifies.some(n => n.l === "warn");
  
  check(`${desc} → 封存提示`, hasDeprecationNotice);
  check(`${desc} → CLI 引导`, hasCLIGuidance);
  check(`${desc} → warn 级别`, isWarnLevel);
}

// 恢复 env
clearLybraEnv();
for (const [k, v] of Object.entries(SAVED_ENV)) if (k.startsWith("LYBRA_")) process.env[k] = v;
// 恢复 cwd 并清理隔离目录
process.chdir(originalCwd);
rmSync(cleanCwd, { recursive: true, force: true });

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
if (NOTES.length) {
  console.log("\n--- NOTES ---");
  for (const n of NOTES) console.log(`  • ${n}`);
}

// 记录删除的测试
console.log("\n--- AIPOS-F65C 件② 删除的测试(已死循环行为)---");
const deletedTests = [
  "/lybra 无参 → status notify (旧 status 逻辑已死)",
  "/lybra status → notify 状态 (旧 status 逻辑已死)",
  "/lybra status → 含 已放行 计数 (旧 status 逻辑已死)",
  "/lybra off 未运行 → 提示未运行 (旧 off 逻辑已死)",
  "/lybra foo → 用法提示 (已改为封存提示)",
  "/lybra on 0 → maxN 无效 (旧 maxN 解析已死)",
  "/lybra on abc → maxN 无效 (旧 maxN 解析已死)",
  "on 缺 workspaceRoot → 配置错误 (旧 on 配置红线已死)",
  "on 缺 role → 配置错误 (旧 on 配置红线已死)",
  "on 缺 actor → 配置错误 (旧 on 配置红线已死)",
  "on 缺 ownerPolicyRef → 配置错误 (旧 on 配置红线已死)",
  "on 缺 token → 配置错误 (旧 on 配置红线已死)",
  "on gate 不可达 → 连接错误 (旧 on 连接自检已死)",
  "on gate 不可达 → 提示 lybra serve (旧 on 连接自检已死)",
  "on gate 不可达 → error 级别 (旧 on 连接自检已死)",
  "on gate 不可达 → 没有 sendUserMessage (旧 on 连接自检已死)",
];
console.log(`共删除 ${deletedTests.length} 个测试,原因:测试已死的循环行为(on/off/status/maxN/配置红线/连接自检)`);
for (const t of deletedTests) console.log(`  • ${t}`);

console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
