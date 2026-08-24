/**
 * AIPOS-F22(+fix1) 顾问侧接入全绿 — 可重放夹具
 *
 * 覆盖审计 FAIL 清单(F-F22-1..4 的可机器重放形态):
 *  A. P0 回归: 薄壳工厂导入错误(response_render→renderer)先红后绿可重放
 *  B. 薄壳工厂单实现: 四动词 --confirm 全路由工厂, /lybra claim 同一门动词(含 .call→callTool 回归)
 *  C. 经 bin E2E: stub gate HTTP + 临时 connection.json —— queue claim --confirm /
 *     task-progress --confirm / audit-verdict --confirm 全链路真跑(修复前 ModuleNotFoundError, 修复后转绿)
 *  D. /lybra claim 工位自救(mock pi + stub gate + env 身份声明自解析)
 *  E. enroll 落点守卫(经 python 真代码 + roles 注册表单源): planner/advisor 允许治理仓,
 *     executor/auditor 拒绝(负夹具 F23⑧), 工位路径放行
 *
 * 锚点: F22 大项A(F23⑧ 守卫按角色类) + 大项B(C1 四投影单源 + F33 薄壳收编) + 大项C(F20 命令族)
 *
 * 跑法:`node tests/f22-advisor-onboarding.test.ts`(或经 run-all.sh)
 */
import { readFileSync, existsSync, mkdirSync, rmSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { execFileSync, spawnSync, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const NOTES: string[] = [];
let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean, note?: string) {
  checks.push([name, ok]);
  if (!ok) failures++;
  if (note) NOTES.push(note);
}

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = join(TEST_DIR, "..", "..", "..", "..", ".."); // 产品仓根(tests→lybra-loop→pi→harness→agents→根)
const FACTORY = join(PROJECT_ROOT, "tools/aipos_cli/two_phase_shell_factory.py");
const CLI_SRC = join(PROJECT_ROOT, "tools/aipos_cli/aipos_cli.py");
const LOOP_SRC = join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts");
const ENROLL_SRC = join(PROJECT_ROOT, "tools/aipos_cli/enroll_deliver.py");
const factorySrc = readFileSync(FACTORY, "utf8");
const cliSrc = readFileSync(CLI_SRC, "utf8");
const loopSrc = readFileSync(LOOP_SRC, "utf8");

const hasPython = (() => {
  const r = spawnSync("python3", ["-c", "print(1)"], { encoding: "utf8" });
  return r.status === 0;
})();

// ===========================================================================
// A. P0 回归: F-F22-1 导入错误先红后绿可重放
// ===========================================================================
{
  check(
    "A1: 工厂导入 renderer(非 response_render) — F-F22-1 修复后",
    /^from tools\.aipos_cli\.renderer import render_json$/m.test(factorySrc) &&
      !factorySrc.includes("response_render"),
  );
  check(
    "A2: tools/aipos_cli 全目录无 response_render 残留",
    !cliSrc.includes("response_render") &&
      !readFileSync(join(PROJECT_ROOT, "tools/aipos_cli/enroll_deliver.py"), "utf8").includes("response_render"),
  );
  if (hasPython) {
    // 审计取证命令原文复现(修复前: ModuleNotFoundError → status≠0)
    const r = spawnSync(
      "python3",
      ["-c", "import sys; sys.path.insert(0, '.'); from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb, execute_single_phase_via_gate; print('OK')"],
      { cwd: PROJECT_ROOT, encoding: "utf8", timeout: 30000 },
    );
    check("A3: Python 活体导入工厂两函数(审计复现命令, 修复前先红)", r.status === 0 && r.stdout.trim() === "OK", r.stderr.slice(0, 200));
  } else {
    NOTES.push("A3: python3 不可用, 跳过活体导入(记 NOTE)");
  }
}

// ===========================================================================
// B. 薄壳工厂单实现(原卡验收③ + 防碎片化红线①③)
// ===========================================================================
{
  const verbs = JSON.parse(readFileSync(join(PROJECT_ROOT, "schema/verbs.schema.json"), "utf8")).verbs;
  for (const v of ["lybra_queue_claim_dry_run", "lybra_queue_claim_confirm", "lybra_queue_return_dry_run", "lybra_queue_return_confirm", "lybra_audit_verdict_dry_run", "lybra_audit_verdict_confirm", "lybra_task_progress"]) {
    check(`B1: verbs.schema 注册 ${v}`, Boolean(verbs[v]));
  }

  // 四动词 --confirm 分支全部路由工厂: 逐分支计数
  const factoryCalls = (cliSrc.match(/from tools\.aipos_cli\.two_phase_shell_factory import execute_two_phase_verb|from tools\.aipos_cli\.two_phase_shell_factory import execute_single_phase_via_gate/g) || []).length;
  const uses = (cliSrc.match(/execute_two_phase_verb\(|execute_single_phase_via_gate\(/g) || []).length;
  check(
    "B2: 四动词 --confirm 分支全部路由工厂(claim/return/audit-verdict→两阶段, task-progress→单阶段), 无手写第二实现",
    factoryCalls >= 2 && uses >= 4,
    `imports=${factoryCalls} calls=${uses}`,
  );

  // /lybra claim: 同一门动词 + 修复 .call→callTool 回归(修复前 TypeError→'认领异常')
  const claimIdx = loopSrc.indexOf('if (sub === "claim")');
  const claimBlock = loopSrc.slice(claimIdx, loopSrc.indexOf('if (sub === "return")', claimIdx));
  check(
    "B3: /lybra claim 调 lybra_queue_claim_dry_run + confirm(同一门动词, 与 loop-on 同源)",
    claimBlock.includes('"lybra_queue_claim_dry_run"') && claimBlock.includes('"lybra_queue_claim_confirm"'),
  );
  check(
    "B4: /lybra claim 用 callTool(非不存在的 .call — 运行时断裂回归)",
    !/currentClient!\.call\(/.test(claimBlock) && claimBlock.includes("callTool("),
  );
  check(
    "B5: /lybra claim 身份自解析(loadConfig 单源, 参数不自带名单)",
    claimBlock.includes("loadConfig(process.env)") && claimBlock.includes("config.actor") && claimBlock.includes("config.ownerPolicyRef"),
  );
}

// ===========================================================================
// C. 经 bin E2E: stub gate + 临时 connection.json — 四动词 --confirm 真跑
// ===========================================================================
interface StubCall { name: string; args: Record<string, unknown>; bearer: string }
function makeStubGate() {
  const calls: StubCall[] = [];
  const state = { blockClaim: false };
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const msg = JSON.parse(body);
      const method = msg?.method || "";
      const name = msg?.params?.name || "";
      const send = (payload: unknown) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { structuredContent: payload } }));
      };
      if (method === "initialize") {
        send({ protocolVersion: "2025-03-26", ok: true });
        return;
      }
      calls.push({ name, args: msg?.params?.arguments || {}, bearer: String(req.headers.authorization || "").replace(/^Bearer /, "") });
      if (name === "lybra_queue_claim_dry_run") {
        if (state.blockClaim) {
          send({ verdict: "BLOCK", blocking_reasons: ["fixture: task not claimable"] });
          return;
        }
        send({ ok: true, verdict: "OK", dry_run_token: "dryrun_f22_fixture", dry_run_snapshot_hash: "abc" });
        return;
      }
      if (name === "lybra_queue_claim_confirm") {
        send({ ok: true, claim_id: "claim_f22_fixture", task_id: "F22-FIX-001" });
        return;
      }
      if (name === "lybra_task_progress") {
        send({ ok: true, event_type: "started", task_id: "F22-FIX-001" });
        return;
      }
      if (name === "lybra_audit_verdict_dry_run") {
        send({ ok: true, verdict: "OK", dry_run_token: "dryrun_f22_verdict" });
        return;
      }
      if (name === "lybra_audit_verdict_confirm") {
        send({ ok: true, verdict_id: "verdict_f22_fixture" });
        return;
      }
      send({ ok: false, message: `unknown tool ${name}` });
    });
  });
  return { server, calls, state };
}

async function runBin(args: string[], extraEnv: Record<string, string> = {}) {
  // 异步 spawn: 同步 spawnSync 会阻塞事件循环, stub gate 无法应答
  return await new Promise<{ status: number | null; stdout: string; stderr: string }>((resolve) => {
    const child = spawn(join(PROJECT_ROOT, "bin/lybra"), args, {
      cwd: PROJECT_ROOT,
      timeout: 60000,
      env: { ...process.env, ...extraEnv },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("error", (e) => resolve({ status: -1, stdout, stderr: String(e) }));
    child.on("close", (code) => resolve({ status: code, stdout, stderr }));
  });
}

{
  const tmp = mkdtempSync(join(tmpdir(), "f22-e2e-"));
  const gate = makeStubGate();
  try {
    gate.server.listen(0, "127.0.0.1");
    await new Promise<void>((r) => gate.server.once("listening", () => r()));
    const port = (gate.server.address() as AddressInfo).port;
    const connPath = join(tmp, "connection.json");
    writeFileSync(
      connPath,
      JSON.stringify({
        workspace_root: tmp,
        mcp: { rpc_url: `http://127.0.0.1:${port}/mcp` },
        tokens: [
          { role: "executor", agent_instance: "exec.f22-test", token: "EXEC-TOKEN-1" },
          { role: "auditor", agent_instance: "audit.f22-test", token: "AUDIT-TOKEN-1" },
        ],
      }),
    );

    // --- C1: queue claim --confirm(两阶段经 bin, 修复前 ModuleNotFoundError 先红) ---
    {
      const r = await runBin([
        "queue", "claim", "--confirm",
        "--task-id", "F22-FIX-001",
        "--actor", "exec.f22-test",
        "--agent-instance", "exec.f22-test",
        "--autonomy-mode", "Supervised",
        "--owner-policy-ref", "pol_fixture_1",
        "--connection-json", connPath,
      ]);
      const dry = gate.calls.find((c) => c.name === "lybra_queue_claim_dry_run");
      const conf = gate.calls.find((c) => c.name === "lybra_queue_claim_confirm");
      check(
        "C1a: queue claim --confirm 经 bin 全链路退出码 0(两阶段走通)",
        r.status === 0,
        `stderr=${(r.stderr || "").slice(0, 300)}`,
      );
      check(
        "C1b: dry_run 参数按注册表解析(task_id/actor/autonomy_mode/owner_policy_ref)",
        Boolean(dry) && dry!.args.task_id === "F22-FIX-001" && dry!.args.actor === "exec.f22-test" && dry!.args.autonomy_mode === "Supervised" && dry!.args.owner_policy_ref === "pol_fixture_1",
      );
      check(
        "C1c: confirm 重放 dry_run_token + OWNER_CONFIRMED 自确认(AIPOS-328)",
        Boolean(conf) && conf!.args.dry_run_token === "dryrun_f22_fixture" && conf!.args.owner_confirmation_token === "OWNER_CONFIRMED",
      );
      check("C1d: executor token 按 role 从 connection.json 加载(bearer)", dry?.bearer === "EXEC-TOKEN-1");
    }

    // --- C2: task-progress --confirm(单阶段经 gate) ---
    {
      gate.calls.length = 0;
      const r = await runBin([
        "task-progress", "--confirm",
        "--task-id", "F22-FIX-001",
        "--actor", "exec.f22-test",
        "--agent-instance", "exec.f22-test",
        "--event-type", "started",
        "--summary", "f22 fixture",
        "--connection-json", connPath,
        "--json",
      ]);
      const prog = gate.calls.find((c) => c.name === "lybra_task_progress");
      check("C2a: task-progress --confirm 经 bin 退出码 0", r.status === 0, `stderr=${(r.stderr || "").slice(0, 300)}`);
      check(
        "C2b: 单阶段动词走 gate MCP(task_id/event_type/actor)",
        Boolean(prog) && prog!.args.task_id === "F22-FIX-001" && prog!.args.event_type === "started" && prog!.args.actor === "exec.f22-test",
      );
    }

    // --- C3: audit-verdict --confirm(两阶段, auditor role token) ---
    {
      gate.calls.length = 0;
      const r = await runBin([
        "audit-verdict", "--confirm",
        "--reviewed-task-id", "F22-FIX-000",
        "--audit-task-id", "F22-FIX-000R",
        "--actor", "audit.f22-test",
        "--agent-instance", "audit.f22-test",
        "--verdict", "PASS",
        "--owner-policy-ref", "pol_fixture_audit",
        "--connection-json", connPath,
        "--json",
      ]);
      const dry = gate.calls.find((c) => c.name === "lybra_audit_verdict_dry_run");
      const conf = gate.calls.find((c) => c.name === "lybra_audit_verdict_confirm");
      check("C3a: audit-verdict --confirm 经 bin 退出码 0", r.status === 0, `stderr=${(r.stderr || "").slice(0, 300)}`);
      check(
        "C3b: 裁决 dry_run 参数(reviewed_task_id/verdict/audit_task_id)",
        Boolean(dry) && dry!.args.reviewed_task_id === "F22-FIX-000" && dry!.args.verdict === "PASS" && dry!.args.audit_task_id === "F22-FIX-000R",
      );
      check(
        "C3c: 裁决 confirm 补专有参数(audit_task_id/reviewed_task_id)",
        Boolean(conf) && conf!.args.audit_task_id === "F22-FIX-000R" && conf!.args.reviewed_task_id === "F22-FIX-000",
      );
      check("C3d: auditor token 按 role 加载(bearer)", dry?.bearer === "AUDIT-TOKEN-1");
    }

    // --- C4: BLOCK 路径(dry_run 被拒 → 退出码 1, 不误报成功) ---
    {
      gate.state.blockClaim = true;
      gate.calls.length = 0;
      const r = await runBin([
        "queue", "claim", "--confirm",
        "--task-id", "F22-FIX-001",
        "--actor", "exec.f22-test",
        "--connection-json", connPath,
      ]);
      check("C4: dry_run BLOCK → 退出码 1 且不 confirm(拒因透传)", r.status === 1 && !gate.calls.some((c) => c.name === "lybra_queue_claim_confirm"), `status=${r.status} stderr=${(r.stderr || "").slice(0, 200)}`);
      gate.state.blockClaim = false;
    }
  } finally {
    try { gate.server.close(); } catch { /* noop */ }
    try { rmSync(tmp, { recursive: true, force: true }); } catch { /* noop */ }
  }
}

// ===========================================================================
// D. /lybra claim 工位自救(mock pi + stub gate + env 身份声明自解析)
// ===========================================================================
{
  const tmp = mkdtempSync(join(tmpdir(), "f22-claim-"));
  const prevCwd = process.cwd();
  const gate = makeStubGate();
  try {
    gate.server.listen(0, "127.0.0.1");
    await new Promise<void>((r) => gate.server.once("listening", () => r()));
    const port = (gate.server.address() as AddressInfo).port;

    const { default: factory } = await import("../lybra-loop.ts");
    const commands: Record<string, { description: string; handler: Function }> = {};
    const fakePi = {
      registerCommand: (name: string, opts: { description: string; handler: Function }) => { commands[name] = opts; },
      on: () => {},
      appendEntry: () => {},
      registerEntryRenderer: () => {},
    } as any;
    factory(fakePi);
    check("D1: /lybra claim 子命令已注册(F20 命令族)", typeof commands.lybra?.handler === "function");

    const makeMockCtx = () => {
      const notifies: Array<{ m: string; l?: string }> = [];
      return { ctx: { ui: { notify: (m: string, l?: string) => notifies.push({ m, l }) } } as any, notifies };
    };

    // 身份全从 env 自解析(C2 单源): 工位身份声明
    const prevEnv = { ...process.env };
    process.env.LYBRA_WORKSPACE_ROOT = tmp;
    process.env.LYBRA_ROLE = "executor";
    process.env.LYBRA_ACTOR = "exec.f22-test";
    process.env.LYBRA_AGENT_INSTANCE = "exec.f22-test";
    process.env.LYBRA_OWNER_POLICY_REF = "pol_fixture_1";
    process.env.LYBRA_GATE_URL = `http://127.0.0.1:${port}`;
    process.env.LYBRA_TOKEN = "EXEC-TOKEN-1";
    process.env.LYBRA_SCHEMA_DIR = join(PROJECT_ROOT, "schema");
    process.chdir(tmp);

    // --- D3: BLOCK 路径(同一 stub 切行为: 模块级 currentClient 会复用首连) ---
    {
      gate.state.blockClaim = true;
      gate.calls.length = 0;
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("claim F22-BLOCKED-1", ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("D3: 认领被 BLOCK → 出声含原因, 不 confirm", all.includes("认领被 BLOCK") && all.includes("fixture: task not claimable"), all.slice(0, 300));
      check("D3b: BLOCK 后未发 confirm", !gate.calls.some((c) => c.name === "lybra_queue_claim_confirm"));
      gate.state.blockClaim = false;
    }

    // --- D2: 成功路径(身份从 env 自解析) ---
    {
      gate.calls.length = 0;
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("claim F22-FIX-001", ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("D2a: /lybra claim 成功出声(✓ 认领成功 + claim_id)", all.includes("✓ 认领成功") && all.includes("claim_f22_fixture"), all.slice(0, 300));
      const dry = gate.calls.find((c) => c.name === "lybra_queue_claim_dry_run");
      const conf = gate.calls.find((c) => c.name === "lybra_queue_claim_confirm");
      check(
        "D2b: 身份从 env 自解析(actor/owner_policy_ref 未手传)",
        Boolean(dry) && dry!.args.actor === "exec.f22-test" && dry!.args.owner_policy_ref === "pol_fixture_1",
      );
      check("D2c: 两阶段同一门动词(dry_run→confirm+OWNER_CONFIRMED)", Boolean(conf) && conf!.args.dry_run_token === "dryrun_f22_fixture" && conf!.args.owner_confirmation_token === "OWNER_CONFIRMED");
      check("D2d: token 从身份声明解析(bearer)", dry?.bearer === "EXEC-TOKEN-1");
    }

    // --- D4: 缺 task id 用法提示 ---
    {
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("claim", ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("D4: 缺 TASK-ID → 用法提示", all.includes("用法: /lybra claim <TASK-ID>"), all.slice(0, 200));
    }
    Object.assign(process.env, prevEnv);
  } finally {
    process.chdir(prevCwd);
    try { gate.server.close(); } catch { /* noop */ }
    try { rmSync(tmp, { recursive: true, force: true }); } catch { /* noop */ }
  }
}

// ===========================================================================
// E. enroll 落点守卫(经 python 真代码 + roles 注册表单源)
// ===========================================================================
{
  const enrollSrc = readFileSync(ENROLL_SRC, "utf8");
  check(
    "E0: 角色类真相只从注册表读(custom_roles.resolve_role_to_class), 守卫内无自建角色名单",
    enrollSrc.includes("resolve_role_to_class") && !/role_class\s*=\s*\{/.test(enrollSrc.split("def validate_workspace_root")[0].split("def _get_role_class")[1] || ""),
  );
  if (hasPython) {
    const probe = (root: string, role: string) => {
      const r = execFileSync(
        "python3",
        ["-c", `import sys; sys.path.insert(0, '.')
from tools.aipos_cli.enroll_deliver import validate_workspace_root
try:
    validate_workspace_root(${JSON.stringify(root)}, ${JSON.stringify(role)})
    print("ALLOW")
except ValueError:
    print("REJECT")`],
        { cwd: PROJECT_ROOT, encoding: "utf8", timeout: 30000 },
      ).trim();
      return r;
    };
    const govRoot = "/tmp/ai-project-os-fixture/2_projects/x"; // 治理样式路径(守卫判据: ai-project-os in path)
    check("E1: planner + 治理工作区 → 放行(大项A)", probe(govRoot, "planner") === "ALLOW");
    check("E2: advisor + 治理工作区 → 放行", probe(govRoot, "advisor") === "ALLOW");
    check("E3: executor + 治理工作区 → 拒绝(负夹具, F23⑧ 防线不减)", probe(govRoot, "executor") === "REJECT");
    check("E4: auditor + 治理工作区 → 拒绝", probe(govRoot, "auditor") === "REJECT");
    check("E5: executor + 工位目录 → 放行", probe("/tmp/f22-station-fixture", "executor") === "ALLOW");
  } else {
    NOTES.push("E1-E5: python3 不可用, 跳过守卫活体(记 NOTE)");
  }
}

// ===========================================================================
// 汇总
// ===========================================================================
{
  console.log("\n===== F22 顾问侧接入全绿夹具 =====");
  for (const [name, ok] of checks) console.log(`${ok ? "✓" : "✗"} ${name}`);
  if (NOTES.length) {
    console.log("\n--- NOTES ---");
    for (const n of NOTES) console.log(`· ${n}`);
  }
  console.log(`\n${checks.filter((c) => c[1]).length}/${checks.length} checks passed`);
  if (failures > 0) {
    console.error(`FAIL: ${failures} check(s) failed`);
    process.exit(1);
  }
  console.log("ALL PASS");
}
