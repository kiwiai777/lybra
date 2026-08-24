/**
 * AIPOS-F38 大项C 夹具: 状态机 BLOCK 幂等识别 — 先红后绿(经 bin 入 run-all)
 *
 * 病灶(2026-08-24 实撞): 对已持有卡再 claim, 真门返回状态机类 BLOCK
 * (blocking_reasons = 纯字符串数组, 实捕原文), 循环当陌生 BLOCK 立停 → 工位堵死。
 *
 * 本夹具四腿:
 *  - 红  = F38 前(main) decideClaimDryRun: 真门字符串 reasons → stop-block(循环停) ✗
 *  - 绿1 = F38 后(本卡): 同应答 → already-held(识别为幂等, 不停循环) ✓
 *  - 绿2 = 引擎分流(executeTick 真跑): 本工位持有→停语带"已持有 <ID>"(复用 held 复工网);
 *          他人持有→跳过出声(日志 claim-skip-other-holder)并继续试下一张(循环不停)
 *  - 回归 = 无关 BLOCK(任务不存在)两版都必须 stop-block(防误识别扩面);
 *          手动路径匹配器兼容 string|object 两形态
 *
 * 跑法: node tests/f38c-claim-idempotent.test.ts (或经 run-all.sh 常驻)
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, writeFileSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { decideClaimDryRun, type AnyDict } from "../loop-decisions.ts";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (exists(join(dir, "package.json")) && exists(join(dir, "agents"))) return dir;
    dir = join(dir, "..");
  }
  return process.cwd();
}
function exists(p: string): boolean {
  return existsSync(p);
}
const PROJECT_ROOT = findProjectRoot();
const DECISIONS_REL = "agents/harness/pi/lybra-loop/loop-decisions.ts";
const ENGINE_REL = "agents/harness/pi/lybra-loop/loop-engine.ts";
const LOOP_REL = "agents/harness/pi/lybra-loop/lybra-loop.ts";
const ACTOR = "exec.lybra.kiwiai-dev";

function gitOut(args: string[]): string {
  const r = spawnSync("git", ["-C", PROJECT_ROOT, ...args], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(`git ${args.join(" ")} 失败: ${r.stderr}`);
  return r.stdout;
}

// 真门实捕(2026-08-24, executor 对已持有卡再 claim 的 dry_run 返回, blocking_reasons 原样):
const REAL_GATE_ALREADY_CLAIMED = [
  "Invalid transition for claim: expected source state pending, found claimed",
  "Directory/status mismatch blocks claim: expected frontmatter status pending",
  "Target file already exists: 5_tasks/queue/claimed/aipos-f38.md",
];
const UNRELATED_BLOCK = { isError: true, verdict: "BLOCK", blocking_reasons: ["Task not found in pending queue: TEST-NOPE-404"] };

async function loadPreF38Decide(): Promise<(resp: AnyDict | null, taskId: string) => AnyDict> {
  // F38 前版(main)纯决策模块 → 落临时文件动态 import(Node 22 类型剥离可跨路径 import)
  const tmp = join(PROJECT_ROOT, ".tmp-f38-pre-decisions.ts");
  writeFileSync(tmp, gitOut(["show", `main:${DECISIONS_REL}`]));
  const mod = await import(tmp);
  rmSync(tmp, { force: true });
  return mod.decideClaimDryRun;
}

// —— 引擎真跑: stub client 按 task_id 脚本化 claim 应答, logger 捕获分流证据 ——
async function runEngine(
  tasks: AnyDict[],
  claimResponses: Record<string, AnyDict>,
): Promise<{ outcome: AnyDict; logs: Array<{ level: string; action: string; detail: AnyDict }> }> {
  const { executeTick } = await import(`../${"loop-engine.ts"}`);
  const logs: Array<{ level: string; action: string; detail: AnyDict }> = [];
  const logger = {
    info: (action: string, detail: AnyDict) => logs.push({ level: "INFO", action, detail }),
    warn: (action: string, detail: AnyDict) => logs.push({ level: "WARN", action, detail }),
    error: (action: string, detail: AnyDict) => logs.push({ level: "ERROR", action, detail }),
  };
  const client = {
    queueTasks: async () => tasks,
    callTool: async (verb: string, args: AnyDict) => {
      if (verb === "lybra_task_preview") {
        return { data: { rendered_card_markdown: `---\ntask_id: ${args.task_id}\nstatus: claimed\n---\n# ${args.task_id}`, referenced_files_content: [] } };
      }
      if (typeof args?.task_id === "string" && args.task_id in claimResponses) return claimResponses[args.task_id];
      throw new Error(`stub: 未脚本化的 task ${args?.task_id}`);
    },
  };
  const state = { released: 0, maxN: 5, triedTaskIds: new Set<string>(), cycleStartMs: Date.now() };
  const outcome = await executeTick({
    client, state, logger,
    actor: ACTOR, workspaceRoot: "/tmp/f38c-ws", intervalSec: 1, maxWaitSec: 60,
  } as AnyDict);
  return { outcome, logs };
}

function pendingCard(id: string, claimedBy: string): AnyDict {
  // 撞门竞态形态: 拉取时 pending 且 assigned_to=我(可匹配认领), claim 时已被 claimed_by 持有
  return { task_id: id, queue_state: "pending", metadata: { task_id: id, assigned_to: ACTOR, claimed_by: claimedBy } };
}
function alreadyClaimedResp(): AnyDict {
  return { isError: true, verdict: "BLOCK", blocking_reasons: REAL_GATE_ALREADY_CLAIMED };
}

describe("F38-大项C 状态机 BLOCK 幂等识别 — 先红后绿", () => {
  it("红: F38 前(main)对真门字符串 reasons → stop-block(循环停)", async () => {
    const pre = await loadPreF38Decide();
    const dec = pre({ isError: true, verdict: "BLOCK", blocking_reasons: REAL_GATE_ALREADY_CLAIMED }, "TEST-F38C");
    console.log(`[RED] F38前 decideClaimDryRun → action=${(dec as AnyDict).action}, reason="${String((dec as AnyDict).reason).slice(0, 50)}..."`);
    assert.strictEqual((dec as AnyDict).action, "stop-block", "F38 前: 已持有卡被当陌生 BLOCK → 循环立停");
    console.log("[RED 判定] 修复前=红: 工位被状态机 BLOCK 堵死 ✗");
  });

  it("绿1: F38 后同应答 → already-held(string|object 双形态)", () => {
    const decStr = decideClaimDryRun({ isError: true, verdict: "BLOCK", blocking_reasons: REAL_GATE_ALREADY_CLAIMED }, "TEST-F38C");
    console.log(`[GREEN1] 真门字符串 reasons → action=${decStr.action}`);
    assert.strictEqual(decStr.action, "already-held", "字符串形态(真门): 识别为已认领");
    const decObj = decideClaimDryRun({
      isError: true, verdict: "BLOCK",
      blocking_reasons: [{ message: "Invalid transition for claim: expected source state pending, found claimed" }],
    }, "TEST-F38C");
    assert.strictEqual(decObj.action, "already-held", "对象形态: 识别为已认领");
    console.log("[GREEN1 判定] 状态机 BLOCK=幂等成功, 不停循环 ✓");
  });

  it("绿2a: 引擎分流 — 本工位持有 → 停语带『已持有 <ID>』复用 held 复工网", async () => {
    const { outcome, logs } = await runEngine(
      [pendingCard("TEST-F38C-HELD", ACTOR)],
      { "TEST-F38C-HELD": alreadyClaimedResp() },
    );
    const resumeLog = logs.find((l) => l.action === "claim-already-held-resume");
    console.log(`[GREEN2a] outcome.kind=${outcome.kind}, reason="${String(outcome.reason).slice(0, 42)}..."`);
    console.log(`[GREEN2a] 日志: ${resumeLog?.action} holder=${resumeLog?.detail?.holder}`);
    assert.ok(resumeLog, "引擎记 claim-already-held-resume");
    assert.strictEqual(outcome.kind, "stop");
    assert.match(String(outcome.reason), /已持有\s+TEST-F38C-HELD/, "停语格式=held 复工网捕获格式(继续执行, 非死停)");
    console.log("[GREEN2a 判定] 本工位已持有→复用复工网继续 ✓");
  });

  it("绿2b: 引擎分流 — 他人持有 → 跳过出声并继续试下一张(循环不停)", async () => {
    const { outcome, logs } = await runEngine(
      [pendingCard("TEST-F38C-OTHER", "audit.lybra.kiwiai-dev"), pendingCard("TEST-F38C-NEXT", ACTOR)],
      {
        "TEST-F38C-OTHER": alreadyClaimedResp(),
        "TEST-F38C-NEXT": { verdict: "ALLOW", isError: false, autonomy_mode: "PreAuthorized", preauthorized_release: true, owner_confirmation_required: false },
      },
    );
    const skipLog = logs.find((l) => l.action === "claim-skip-other-holder");
    console.log(`[GREEN2b] outcome.kind=${outcome.kind}, task=${(outcome as AnyDict).task?.task_id}`);
    console.log(`[GREEN2b] 日志: ${skipLog?.action} holder=${skipLog?.detail?.holder}`);
    assert.ok(skipLog, "引擎记 claim-skip-other-holder(跳过并出声)");
    assert.strictEqual(skipLog?.detail?.holder, "audit.lybra.kiwiai-dev");
    assert.strictEqual(outcome.kind, "release", "跳过他人卡后继续认领下一张 → 循环不停");
    assert.strictEqual((outcome as AnyDict).task?.task_id, "TEST-F38C-NEXT");
    console.log("[GREEN2b 判定] 他人持有→跳过出声, 循环继续 ✓");
  });

  it("回归: 无关 BLOCK 两版都必须 stop-block(防误识别扩面)", async () => {
    const pre = await loadPreF38Decide();
    const decPre = pre(UNRELATED_BLOCK, "TEST-NOPE-404");
    const decPost = decideClaimDryRun(UNRELATED_BLOCK, "TEST-NOPE-404");
    console.log(`[回归] F38前=${(decPre as AnyDict).action}, F38后=${decPost.action}`);
    assert.strictEqual((decPre as AnyDict).action, "stop-block", "F38 前: 无关 BLOCK 立停");
    assert.strictEqual(decPost.action, "stop-block", "F38 后: 无关 BLOCK 仍立停(只幂等状态机类)");
    console.log("[回归判定] 该停就停, 未扩面 ✓");
  });

  it("回归: 手动 /lybra claim 路径匹配器兼容 string|object 且按持有者分流", () => {
    const head = readFileSync(join(PROJECT_ROOT, LOOP_REL), "utf-8");
    const start = head.indexOf('callTool("lybra_queue_claim_dry_run"');
    const block = head.slice(start, head.indexOf("const dryRunToken", start));
    const stringForm = /typeof\s+r\s*===?\s*"string"/.test(block);
    const holderSplit = block.includes("claim-skip-other-holder") && block.includes("claim-already-held");
    const pre = gitOut(["show", `main:${LOOP_REL}`]);
    const preStart = pre.indexOf('callTool("lybra_queue_claim_dry_run"');
    const preBlock = pre.slice(preStart, pre.indexOf("const dryRunToken", preStart));
    const preStringForm = /typeof\s+r\s*===?\s*"string"/.test(preBlock);
    console.log(`[回归/手动路径] F38前 stringForm=${preStringForm} → F38后 stringForm=${stringForm}, 持有者分流=${holderSplit}`);
    assert.strictEqual(preStringForm, false, "F38 前: 手动路径只查对象形态(真门字符串不识别)");
    assert.ok(stringForm && holderSplit, "F38 后: 双形态识别 + 本工位继续/他人跳过分流");
    console.log("[回归/手动路径 判定] 幂等识别覆盖自动+手动两路 ✓");
  });
});
