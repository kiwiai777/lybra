/**
 * AIPOS-F16 专项测试 —— 余热收尾(额度只管新领卡, 收账/复工送完在途卡再停)。
 *
 * 三层:
 *  A. 纯单测:executeTick cooldown 门(不领新卡/复工网可达)、planCooldownStep、findInFlightCards。
 *  B. 源级断言(f11 范式):旧"达到 maxN 即整停"已除, 余热出声/终停带路/off 即停都在。
 *  C. 夹具 E2E(mock gate + mock pi + 临时 workspace):验收①②③ 全弧线 headless 复演。
 * 跑法:`node tests/f16-cooldown.test.ts`
 */
import { executeTick, freshState, Logger, type GateReadFace, type TickContext } from "../loop-engine.ts";
import { planCooldownStep } from "../loop-decisions.ts";
import { findInFlightCards } from "../lybra-loop.ts";
import { mkdtempSync, rmSync, readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { AnyDict } from "../loop-decisions.ts";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}
async function waitFor(desc: string, cond: () => boolean, timeoutMs = 10000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (cond()) return true;
    await new Promise((r) => setTimeout(r, 100));
  }
  check(`等待超时: ${desc}`, false);
  return false;
}

// ===========================================================================
// A. 纯单测
// ===========================================================================
const tmpA = mkdtempSync(join(tmpdir(), "f16-unit-"));
const loggerA = new Logger(join(tmpA, "loop.log"));
function mkCtx(client: GateReadFace, state = freshState()): TickContext {
  return { client, actor: "me", agentInstance: "me", ownerPolicyRef: "pol-1", workspaceRoot: tmpA, activeSessionId: "s", state, logger: loggerA };
}
/** 记录 claim 调用的 mock client */
function mockClientCounting(tasks: AnyDict[], claimRespByTask: Record<string, AnyDict>) {
  const claimCalls: string[] = [];
  const client: GateReadFace = {
    async queueTasks() { return tasks; },
    async callTool(name: string, args: AnyDict) {
      if (name === "lybra_queue_claim_dry_run") {
        claimCalls.push(String(args.task_id));
        return claimRespByTask[String(args.task_id)];
      }
      if (name === "lybra_task_preview") return { data: { rendered_card_markdown: "# c\n" } };
      return {};
    },
  };
  return { client, claimCalls };
}
function pending(id: string, extra: Record<string, unknown> = {}) {
  return { task_id: id, queue_state: "pending", path: `5_tasks/queue/pending/${id}.md`, metadata: { assigned_to: "me", ...extra } };
}
function claimed(id: string, by: string, extra: Record<string, unknown> = {}) {
  return { task_id: id, queue_state: "claimed", metadata: { claimed_by: by, ...extra } };
}

// A1. 额度尽 + 有可领 pending 卡 → cooldown, 且绝不 claim(验收②核心)
{
  const { client, claimCalls } = mockClientCounting([pending("T2")], {
    T2: { autonomy_mode: "PreAuthorized", owner_confirmation_required: false, preauthorized_release: true },
  });
  const state = freshState();
  state.released = 1; state.maxN = 1;
  const r = await executeTick(mkCtx(client, state));
  check("A1 额度尽+pending → cooldown", r.kind === "cooldown");
  check("A1 不发起 claim dry-run", claimCalls.length === 0);
}
// A2. 额度尽 + held(未归还的本工位卡)→ 复工网可达(既有 held stop 路径)
{
  const { client } = mockClientCounting([claimed("H1", "me")], {});
  const state = freshState();
  state.released = 1; state.maxN = 1;
  const r = await executeTick(mkCtx(client, state));
  check("A2 额度尽+held → stop 已持有(复工网可达)", r.kind === "stop" && r.reason.includes("已持有 H1"));
}
// A3. 额度尽 + 空队列 → cooldown(不是 wait, 不吃 maxWait 超时)
{
  const { client } = mockClientCounting([], {});
  const state = freshState();
  state.released = 2; state.maxN = 2;
  const r = await executeTick(mkCtx(client, state));
  check("A3 额度尽+空队列 → cooldown", r.kind === "cooldown");
}
// A4. 额度未尽 → 照常领卡(回归:cooldown 门不拦正常额度)
{
  const { client, claimCalls } = mockClientCounting([pending("T9")], {
    T9: { autonomy_mode: "PreAuthorized", owner_confirmation_required: false, preauthorized_release: true },
  });
  const r = await executeTick(mkCtx(client));
  check("A4 额度未尽 → release 照常", r.kind === "release" && r.task.task_id === "T9" && claimCalls.length === 1);
}
// A5. planCooldownStep:无在途 → 终停带路; 有在途 → 余热等待
{
  const stop = planCooldownStep([], 1, 1, 30);
  check("A5 终停判定", stop.action === "terminal-stop" && stop.reason.includes("额度用尽(1/1)") && stop.reason.includes("在途卡全部收口"));
  const wait = planCooldownStep(["T1", "T2"], 2, 2, 30);
  check("A5 等待判定", wait.action === "wait" && wait.voiceLine.includes("余热") && wait.voiceLine.includes("2 张") && wait.nextMs === 30000);
}
// A6. findInFlightCards:只数本工位(claimed_by 匹配), 卡号读 frontmatter
{
  const ws = mkdtempSync(join(tmpdir(), "f16-ws-"));
  const claimedDir = join(ws, "5_tasks/queue/claimed");
  mkdirSync(claimedDir, { recursive: true });
  writeFileSync(join(claimedDir, "a.md"), "---\ntask_id: T-OWN\nclaimed_by: me\n---\nbody\n");
  writeFileSync(join(claimedDir, "b.md"), "---\ntask_id: T-OTHER\nclaimed_by: audit.someone\n---\nbody\n");
  writeFileSync(join(claimedDir, "c.md"), "---\ntask_id: T-NOBODY\n---\nbody\n");
  const fs = await import("node:fs");
  const path = await import("node:path");
  const ids = findInFlightCards(fs, path, ws, "me");
  check("A6 只数本工位在途卡", ids.length === 1 && ids[0] === "T-OWN");
  const none = findInFlightCards(fs, path, join(ws, "no-such"), "me");
  check("A6 无 claimed 目录 → 空", none.length === 0);
  rmSync(ws, { recursive: true, force: true });
}

// ===========================================================================
// B. 源级断言(f11 范式)
// ===========================================================================
{
  const src = readFileSync(join(import.meta.dirname || ".", "../lybra-loop.ts"), "utf8");
  const engineSrc = readFileSync(join(import.meta.dirname || ".", "../loop-engine.ts"), "utf8");
  const decisionsSrc = readFileSync(join(import.meta.dirname || ".", "../loop-decisions.ts"), "utf8");
  check("B1 旧『达到 maxN 即整停』已除", !src.includes("stopLoop(`达到 maxN"));
  check("B2 cooldown-enter 日志在", src.includes('"cooldown-enter"'));
  check("B3 余热转入出声(额度已用完/余热收尾中)", src.includes("额度已用完(") && src.includes("余热收尾中"));
  check("B4 终停停语(在途卡全部收口, 单源在 planCooldownStep)", decisionsSrc.includes("在途卡全部收口"));
  check("B5 停语带路(如需继续领卡请 /lybra on N)", src.includes("如需继续领卡请 /lybra on N"));
  check("B6 /lybra off 即时停保留", src.includes('stopLoop("用户 /lybra off", "info")'));
  check("B7 status 可见余热模式", src.includes("模式: 余热收尾中"));
  check("B8 出声全走 voice()/stopLoop 单出口(F15)", src.includes("voice(`下一步: 如需继续领卡请 /lybra on N`"));
  check("B9 cooldown 门在 claim 循环之前(engine)", engineSrc.indexOf("cooldown-no-claim") < engineSrc.indexOf("lybra_queue_claim_dry_run"));
  check("B10 TickOutcome 含 cooldown", engineSrc.includes('| { kind: "cooldown"; reason: string }'));
}

// ===========================================================================
// C. 夹具 E2E —— 验收①②③ 全弧线(mock gate + mock pi + 临时 workspace)
// ===========================================================================
const NOTES: string[] = [];
if (process.env.F16_SKIP_E2E) {
  NOTES.push("E2E 被 F16_SKIP_E2E 跳过");
} else {
  const { createServer } = await import("node:http");

  // --- mock gate:内存队列 + 文件副作用模拟(close → 卡离 claimed 进 completed) ---
  const gateState = {
    tasks: [] as AnyDict[],
    claims: [] as string[],
    closes: [] as string[],
  };
  function markReturned(id: string) {
    const t = gateState.tasks.find((x) => x.task_id === id);
    if (t) (t.metadata as AnyDict).executor_status = "completed";
  }
  const gateServer = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      let msg: AnyDict;
      try { msg = JSON.parse(body); } catch { res.writeHead(400); res.end(); return; }
      const send = (result: unknown) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", id: (msg as AnyDict).id ?? null, result }));
      };
      if (msg.method === "initialize") return send({ protocolVersion: "2025-03-26" });
      if (msg.method !== "tools/call") return send({});
      const name = String(msg.params?.name ?? "");
      const args = (msg.params?.arguments ?? {}) as AnyDict;
      if (name === "lybra_queue_list") {
        return send({ structuredContent: { data: { tasks: gateState.tasks } } });
      }
      if (name === "lybra_queue_claim_dry_run") {
        gateState.claims.push(String(args.task_id));
        const t = gateState.tasks.find((x) => x.task_id === args.task_id);
        if (t && t.queue_state === "pending") {
          t.queue_state = "claimed";
          (t.metadata as AnyDict).claimed_by = args.agent_instance ?? "me";
        }
        return send({
          structuredContent: {
            autonomy_mode: "PreAuthorized", owner_confirmation_required: false,
            preauthorized_release: true, ok: true, owner_policy_ref: args.owner_policy_ref,
          },
        });
      }
      if (name === "lybra_task_preview") {
        const id = String(args.task_id);
        return send({
          structuredContent: {
            data: {
              rendered_card_markdown:
                `---\ntask_id: ${id}\ntask_mode: code\nclaimed_by: ${args.actor}\nstatus: claimed\n---\n\n# ${id} 夹具卡\n`,
            },
          },
        });
      }
      if (name === "lybra_queue_return_dry_run") {
        return send({ structuredContent: { ok: true, dry_run_token: `drt-${gateState.returns ?? 0}` } });
      }
      if (name === "lybra_queue_return_confirm") {
        return send({ structuredContent: { ok: true, verdict: "ok" } });
      }
      if (name === "lybra_queue_close_dry_run") {
        return send({ structuredContent: { ok: true, verdict: "ok" } });
      }
      if (name === "lybra_queue_close_confirm") {
        gateState.closes.push(String(args.task_id));
        // 模拟门侧副作用:close 后卡离 claimed 进 completed
        try {
          const id = String(args.task_id).toLowerCase();
          const ws = process.env.LYBRA_WORKSPACE_ROOT!;
          const fs = await import("node:fs");
          const from = join(ws, "5_tasks/queue/claimed", `${id}.md`);
          const toDir = join(ws, "5_tasks/queue/completed");
          if (fs.existsSync(from)) {
            fs.mkdirSync(toDir, { recursive: true });
            fs.renameSync(from, join(toDir, `${id}.md`));
          }
        } catch { /* 夹具尽力而为 */ }
        return send({ structuredContent: { ok: true, verdict: "ok" } });
      }
      if (name === "lybra_distribution_manifest") {
        return send({ structuredContent: { product_commit: "fixture" } });
      }
      return send({ structuredContent: { ok: true } });
    });
  });
  await new Promise<void>((resolve) => gateServer.listen(0, "127.0.0.1", resolve));
  const gatePort = (gateServer.address() as { port: number }).port;

  // --- mock pi + 工厂 ---
  function makeMockPi() {
    const handlers: Record<string, Function> = {};
    const commands: Record<string, { description: string; handler: Function }> = {};
    return {
      api: {
        on(evt: string, h: Function) { handlers[evt] = h; },
        registerCommand(n: string, o: { description: string; handler: Function }) { commands[n] = o; },
      } as any,
      handlers, commands,
    };
  }
  const { default: factory } = await import("../lybra-loop.ts");
  const mp = makeMockPi();
  factory(mp.api);

  const allNotifies: Array<{ m: string; l?: string }> = [];
  function makeCtx() {
    const notifies: Array<{ m: string; l?: string }> = [];
    const ctx = {
      ui: { notify: (m: string, l?: string) => { notifies.push({ m, l }); allNotifies.push({ m, l }); } },
      sessionManager: { getSessionId: () => "f16-e2e-sess" },
      newSession: async (_opts: unknown) => ({ cancelled: false }),
      sendUserMessage: async (_t: string) => {},
    };
    return { ctx, notifies };
  }

  // --- 夹具 workspace ---
  const cleanCwd = mkdtempSync(join(tmpdir(), "f16-clean-cwd-"));
  process.chdir(cleanCwd); // 隔离 .lybra 自发现
  const ws = mkdtempSync(join(tmpdir(), "f16-e2e-ws-"));
  for (const d of ["5_tasks/queue/pending", "5_tasks/queue/claimed", "5_tasks/queue/completed", "5_tasks/records/events", "5_tasks/records/returns", "5_tasks/records/audit_verdicts"]) {
    mkdirSync(join(ws, d), { recursive: true });
  }
  // project.json 指向假 code_repo + finalize 桩(防真 finalize)
  const fakeRepo = mkdtempSync(join(tmpdir(), "f16-fake-repo-"));
  const binDir = join(fakeRepo, ".deploy/current/bin");
  mkdirSync(binDir, { recursive: true });
  writeFileSync(join(binDir, "lybra"), "#!/bin/sh\necho 'finalize stub ok'\n", { mode: 0o755 });
  writeFileSync(join(ws, "project.json"), JSON.stringify({ code_repo: fakeRepo }));
  const logPath = join(ws, "loop.log");

  const SAVED_ENV = { ...process.env };
  for (const k of Object.keys(process.env)) if (k.startsWith("LYBRA_")) delete process.env[k];
  process.env.LYBRA_WORKSPACE_ROOT = ws;
  process.env.LYBRA_ROLE = "executor";
  process.env.LYBRA_ACTOR = "me";
  process.env.LYBRA_AGENT_INSTANCE = "me";
  process.env.LYBRA_OWNER_POLICY_REF = "pol-f16";
  process.env.LYBRA_TOKEN = "fixture-token";
  process.env.LYBRA_GATE_URL = `http://127.0.0.1:${gatePort}`;
  process.env.LYBRA_LOOP_LOG = logPath;
  process.env.LYBRA_LOOP_INTERVAL = "30";
  process.env.LYBRA_LOOP_MAX_WAIT = "1";

  function addPending(id: string) {
    gateState.tasks.push({ task_id: id, queue_state: "pending", path: `5_tasks/queue/pending/${id}.md`, metadata: { assigned_to: "me" } });
  }
  /** 模拟真实 executor 的收尾:completed 事件 + return 记录 + 门侧 executor_status=completed */
  function executorSelfReturn(id: string) {
    mkdirSync(join(ws, `5_tasks/records/events/${id}`), { recursive: true });
    writeFileSync(join(ws, `5_tasks/records/events/${id}/completed_20260821_080000.md`), "---\nevent_type: completed\nsummary: 夹具完成\n---\n");
    mkdirSync(join(ws, `5_tasks/records/returns/${id}`), { recursive: true });
    writeFileSync(join(ws, `5_tasks/records/returns/${id}/return_${id}_20260821_080100.md`), "---\nrecord_type: return_record\n---\n夹具交回\n");
    markReturned(id);
  }
  function writePassVerdict(id: string) {
    const dir = join(ws, `5_tasks/records/audit_verdicts/${id}`);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, `verdict_${id}_20260821_080500.md`),
      "---\nrecord_type: audit_verdict_record\nverdict_id: verdict_" + id + "_20260821_080500\nverdict_at: 2026-08-21T08:05:00Z\nverdict: PASS\n---\n夹具裁决\n");
  }
  const settle = async () => { await mp.handlers["agent_settled"]({}, makeCtx().ctx); };
  const hasNotify = (substr: string) => allNotifies.some((n) => n.m.includes(substr));
  const logText = () => { try { return readFileSync(logPath, "utf-8"); } catch { return ""; } };

  // ---------- 验收①:on 1 → 领卡执行交回 → 余热 → 裁决落库零人肉收账 → 终停带路 ----------
  addPending("T1");
  {
    const { ctx } = makeCtx();
    await mp.commands.lybra.handler("on 1", ctx);
    await waitFor("放行 T1", () => hasNotify("放行 T1"));
    check("C1 on 1 放行 T1", hasNotify("放行 T1(PreAuthorized)→ 冷启动执行 [1/1]"));
    check("C1 卡已材料化到 claimed", existsSync(join(ws, "5_tasks/queue/claimed/t1.md")));
    executorSelfReturn("T1");
    await settle();
    await waitFor("余热出声", () => hasNotify("余热收尾中"));
    check("C1 额度尽转余热出声(额度已用完/余热收尾中)", hasNotify("额度已用完(1/1)") && hasNotify("余热收尾中"));
    await waitFor("余热在途清点出声", () => hasNotify("余热: 在途卡 1 张(T1)"));
    check("C1 余热在途清点出声", hasNotify("余热: 在途卡 1 张(T1)"));
    check("C1 等待裁决出声(F15 活体行)", hasNotify("T1: 等待裁决落库中"));
    check("C1 循环未整停", !hasNotify("lybra 循环停止:达到 maxN"));
    const st = makeCtx();
    await mp.commands.lybra.handler("status", st.ctx);
    check("C1 status 显示余热模式", st.notifies.some((n) => n.m.includes("运行中: 是") && n.m.includes("模式: 余热收尾中")));
    writePassVerdict("T1");
    await settle();
    await waitFor("已收账 T1", () => hasNotify("已收账 T1"));
    check("C1 裁决落库后零人肉自动收账(sweep 候选+已收账)", hasNotify("sweep 候选: T1") && hasNotify("已收账 T1"));
    await settle(); // 再驱动一轮余热 tick(文件状态已稳, 终停判定确定性到达)
    await waitFor("终停", () => hasNotify("在途卡全部收口"));
    check("C1 收口后才终停且停语带路", hasNotify("lybra 循环停止:额度用尽(1/1)且在途卡全部收口") && hasNotify("下一步: 如需继续领卡请 /lybra on N"));
    const st2 = makeCtx();
    await mp.commands.lybra.handler("status", st2.ctx);
    check("C1 status 终停态", st2.notifies.some((n) => n.m.includes("运行中: 否") && n.m.includes("在途卡全部收口")));
    check("C1 close 走了门(一次)", gateState.closes.filter((c) => c === "T1").length === 1);
  }

  // ---------- 验收②:余热期间第二张 pending 卡不被领; 终停后 on N 才领 ----------
  addPending("T2");
  {
    const { ctx } = makeCtx();
    await mp.commands.lybra.handler("on 1", ctx);
    await waitFor("放行 T2", () => hasNotify("放行 T2"));
    executorSelfReturn("T2");
    await settle();
    await waitFor("T2 余热", () => allNotifies.some((n) => n.m.includes("余热: 在途卡 1 张(T2)")));
    const claimsBefore = gateState.claims.length;
    addPending("T3"); // 余热期间放第二张 pending 卡
    await settle();
    await waitFor("cooldown-no-claim 日志", () => logText().includes("cooldown-no-claim"));
    check("C2 余热期间新 pending 卡不被领", !gateState.claims.includes("T3") && !hasNotify("放行 T3"));
    check("C2 不吃额度也不误报拒(无 gate BLOCK)", !hasNotify("gate BLOCK"));
    writePassVerdict("T2");
    await settle();
    await waitFor("已收账 T2", () => hasNotify("已收账 T2"));
    await settle(); // 再驱动一轮余热 tick → 在途归零 → 终停
    await waitFor("终停2", () => hasNotify("在途卡全部收口") && logText().includes("loop-stopped"));
    check("C2 T2 收口后终停", gateState.closes.includes("T2"));
    // 终停后 on N 才领 T3
    const before = gateState.claims.length;
    const { ctx: ctx2 } = makeCtx();
    await mp.commands.lybra.handler("on 1", ctx2);
    await waitFor("放行 T3", () => hasNotify("放行 T3"));
    check("C2 终停后 on 1 才领 T3", gateState.claims.includes("T3") && gateState.claims.length === before + 1);
    // 注: 循环保持运行(T3 在途) — 验收③接着这条余热弧走
  }

  // ---------- 验收③:余热期间 /lybra off 即时停 ----------
  {
    // 场景②尾波: T3 已放行但未交回(处于 held) — 先交回再进余热
    executorSelfReturn("T3");
    await settle();
    await waitFor("T3 余热", () => allNotifies.some((n) => n.m.includes("余热: 在途卡 1 张(T3)")));
    const st0 = makeCtx();
    await mp.commands.lybra.handler("status", st0.ctx);
    check("C3 前置: 余热中(T3 在途)", st0.notifies.some((n) => n.m.includes("运行中: 是") && n.m.includes("模式: 余热收尾中")));
    const { ctx: offCtx, notifies: offNotifies } = makeCtx();
    await mp.commands.lybra.handler("off", offCtx);
    check("C3 /lybra off 余热期间即停", offNotifies.some((n) => n.m.includes("lybra 循环停止:用户 /lybra off")));
    const st = makeCtx();
    await mp.commands.lybra.handler("status", st.ctx);
    check("C3 off 后 status 停态", st.notifies.some((n) => n.m.includes("运行中: 否")));
  }

  // ---------- E2E 日志证据(贴 RETURN 用, stdout 全量输出) ----------
  NOTES.push("=== E2E loop.log 全量(贴 RETURN 证据) ===");
  for (const line of logText().trim().split("\n")) NOTES.push(line);

  // 清理
  await new Promise<void>((r) => gateServer.close(() => r()));
  for (const k of Object.keys(process.env)) if (k.startsWith("LYBRA_")) delete process.env[k];
  for (const [k, v] of Object.entries(SAVED_ENV)) if (k.startsWith("LYBRA_")) process.env[k] = v;
  rmSync(ws, { recursive: true, force: true });
  rmSync(fakeRepo, { recursive: true, force: true });
  rmSync(cleanCwd, { recursive: true, force: true });
}

rmSync(tmpA, { recursive: true, force: true });

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
if (NOTES.length) {
  console.log("\n--- NOTES ---");
  for (const n of NOTES) console.log(n);
}
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
