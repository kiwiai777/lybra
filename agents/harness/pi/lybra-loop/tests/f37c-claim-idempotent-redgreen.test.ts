/**
 * AIPOS-F37(-fix1-fix1) 增补: 重复认领幂等 — 先红后绿夹具(经 bin 入 run-all)
 *
 * 场景(重复认领): 对已持有卡再 claim, 门 dry_run 返回 already-claimed 形态 BLOCK。
 * stub 门用的 reasons 是【真门原文】2026-08-24 实捕(executor 对本卡重复 claim 的
 * lybra_queue_claim_dry_run 返回 blocking_reasons 原样, 仅任务号泛化):
 *   "Invalid transition for claim: expected source state pending, found claimed"  ← 字符串形态!
 *   "Directory/status mismatch blocks claim: expected frontmatter status pending"
 *   "Target file already exists: 5_tasks/queue/claimed/<id>.md"
 *
 *  - 红1 = F37 前版: claim 块无 already-claimed 识别 → "认领被 BLOCK" 停循环
 *  - 红2 = F37 后/本轮修复前: 有识别但匹配器只查 r.message(对象形态), 真门返回字符串形态
 *         → 识别不生效, 仍停循环(本夹具活体揭出, 本轮代码修复)
 *  - 绿  = 本轮修复后: 匹配器兼容 string|object 形态 → "卡已持有,继续执行", 循环继续
 *  - 回归 = 无关 BLOCK(如任务不存在)在任何版本都必须停(防误识别扩面)
 *
 * 跑法: node tests/f37c-claim-idempotent-redgreen.test.ts (或经 run-all.sh 常驻)
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, "package.json")) && existsSync(join(dir, "agents"))) return dir;
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}
const PROJECT_ROOT = findProjectRoot();
const LOOP_REL = "agents/harness/pi/lybra-loop/lybra-loop.ts";

function gitOut(args: string[]): string {
  const r = spawnSync("git", ["-C", PROJECT_ROOT, ...args], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(`git ${args.join(" ")} 失败: ${r.stderr}`);
  return r.stdout;
}
function f37Commit(): string {
  const h = gitOut(["log", "--format=%H", "--grep=AIPOS-F37: 审计车道零人肉三合一", "-1"]).trim();
  assert.ok(h.length === 40, "应能定位 F37 引入 commit");
  return h;
}

// —— claim BLOCK 处理块提取(红绿两侧同一把刀) ——
function extractClaimBlock(src: string): string {
  const start = src.indexOf('callTool("lybra_queue_claim_dry_run"');
  assert.ok(start > 0, "claim dry_run 调用应存在于源码");
  const end = src.indexOf("const dryRunToken = dryResp.dry_run_token;", start);
  assert.ok(end > start, "claim 块尾标记应存在");
  return src.slice(start, end);
}

// —— 匹配器能力探测: 从真实源码块读出“能识别哪种形态的 reason” ——
interface MatcherCapability { present: boolean; objectForm: boolean; stringForm: boolean }
function matcherCapability(src: string): MatcherCapability {
  const block = extractClaimBlock(src);
  return {
    present: block.includes("claim-already-held"),
    objectForm: /r\?*\.message/.test(block),
    stringForm: /typeof\s+r\s*===?\s*"string"/.test(block),
  };
}

// —— 重放: 用真门返回的 reasons 跑识别判定 + 后续循环行为 ——
interface ReplayOutcome { recognized: boolean; loopContinued: boolean; currentTaskId: string | null; voice: string }
function replayClaimBlock(src: string, reasons: unknown[], taskId: string): ReplayOutcome {
  const cap = matcherCapability(src);
  const recognized = reasons.some((r) => {
    if (typeof r === "string") {
      // 真门形态: 纯字符串 blocking_reasons; 只有 stringForm 匹配器能识别
      return cap.present && cap.stringForm && r.includes("claimed");
    }
    const obj = r as { message?: string; error_code?: string };
    return cap.present && cap.objectForm &&
      ((obj.message || "").includes("claimed") || (obj.message || "").includes("已被认领") ||
        (obj.error_code || "") === "INVALID_STATE_TRANSITION");
  });
  if (recognized) {
    // 代码事实: voice "卡已持有,继续执行" + currentTaskId = taskId + return(循环继续)
    return { recognized: true, loopContinued: true, currentTaskId: taskId, voice: `卡 ${taskId} 已持有(claimed),继续执行` };
  }
  // 代码事实: "认领被 BLOCK" → return(循环停), currentTaskId 不设置
  return { recognized: false, loopContinued: false, currentTaskId: null, voice: `认领被 BLOCK: ${JSON.stringify(reasons)}` };
}

// 真门实捕: 2026-08-24 executor 对已持有卡再 claim 的 blocking_reasons 原文(字符串数组)
const REAL_GATE_ALREADY_CLAIMED_REASONS = [
  "Invalid transition for claim: expected source state pending, found claimed",
  "Directory/status mismatch blocks claim: expected frontmatter status pending",
  "Target file already exists: 5_tasks/queue/claimed/aipos-f37-fix1-fix1.md",
];
const UNRELATED_BLOCK_REASONS = ["Task not found in pending queue: TEST-NOPE-404"];

describe("F37-增补 重复认领幂等 — 先红后绿(真门返回形态)", () => {
  it("红1: F37 前版无识别 → 真门 already-claimed BLOCK 直接停循环", () => {
    const pre = gitOut(["show", `${f37Commit()}^:${LOOP_REL}`]);
    const cap = matcherCapability(pre);
    console.log(`[RED1 源据] F37 前版 claim 块: present=${cap.present} objectForm=${cap.objectForm} stringForm=${cap.stringForm}`);
    const r = replayClaimBlock(pre, REAL_GATE_ALREADY_CLAIMED_REASONS, "TEST-F37C");
    console.log(`[RED1] 认出已持有=${r.recognized}, 循环继续=${r.loopContinued}, voice="${r.voice.slice(0, 60)}..."`);
    assert.strictEqual(cap.present, false, "F37 前版: 无 claim-already-held 识别");
    assert.strictEqual(r.recognized, false, "F37 前版: 不识别 → 停");
    assert.strictEqual(r.loopContinued, false, "F37 前版: 循环停");
    console.log("[RED1 判定] 修复前=红: 已持有卡被当陌生 BLOCK, 循环停 ✗");
  });

  it("红2: F37 后/本轮修复前 — 识别存在但只查 r.message(对象形态), 真门字符串 reasons 不生效", () => {
    const head = readFileSync(join(PROJECT_ROOT, LOOP_REL), "utf-8");
    const cap = matcherCapability(head);
    console.log(`[RED2 源据] 当前 claim 块: present=${cap.present} objectForm=${cap.objectForm} stringForm=${cap.stringForm}`);
    const r = replayClaimBlock(head, REAL_GATE_ALREADY_CLAIMED_REASONS, "TEST-F37C");
    console.log(`[RED2] 真门字符串 reasons → 认出已持有=${r.recognized}, 循环继续=${r.loopContinued}`);
    if (!cap.stringForm) {
      assert.strictEqual(r.recognized, false, "本轮修复前: 字符串形态不识别 → 仍停(活体揭出)");
      assert.strictEqual(r.loopContinued, false, "本轮修复前: 循环仍停");
      console.log("[RED2 判定] 增补对真门形态不生效=红: 匹配器缺 string 分支 ✗");
    } else {
      assert.strictEqual(r.recognized, true, "已修复: stringForm 在源码中, 字符串形态识别");
      console.log("[GREEN2 判定] 匹配器已兼容 string 形态 ✓(本用例即常驻回归)");
    }
  });

  it("绿: 匹配器兼容 string|object 两形态 → 已持有继续执行(对象形态回归同守)", () => {
    const head = readFileSync(join(PROJECT_ROOT, LOOP_REL), "utf-8");
    const cap = matcherCapability(head);
    assert.ok(cap.present && cap.objectForm && cap.stringForm, "HEAD 应同时具备识别存在/对象形态/字符串形态");
    const rStr = replayClaimBlock(head, REAL_GATE_ALREADY_CLAIMED_REASONS, "TEST-F37C");
    console.log(`[GREEN] 真门字符串 reasons → 认出=${rStr.recognized}, voice="${rStr.voice}"`);
    assert.strictEqual(rStr.recognized, true, "字符串形态: 识别为已持有");
    assert.strictEqual(rStr.loopContinued, true, "字符串形态: 循环继续");
    assert.strictEqual(rStr.currentTaskId, "TEST-F37C", "currentTaskId 已设置(执行可续)");
    const objReasons = [{ message: "Invalid transition for claim: expected source state pending, found claimed", error_code: "INVALID_STATE_TRANSITION" }];
    const rObj = replayClaimBlock(head, objReasons, "TEST-F37C");
    assert.strictEqual(rObj.recognized, true, "对象形态(设计原文): 识别为已持有(回归)");
    console.log("[GREEN 判定] 重复认领幂等修复后=绿: string|object 双形态识别, 循环继续 ✓");
  });

  it("回归: 无关 BLOCK(任务不存在)必须停 — 防误识别扩面", () => {
    const pre = gitOut(["show", `${f37Commit()}^:${LOOP_REL}`]);
    const head = readFileSync(join(PROJECT_ROOT, LOOP_REL), "utf-8");
    for (const [label, src] of [["F37前版", pre], ["HEAD", head]] as const) {
      const r = replayClaimBlock(src, UNRELATED_BLOCK_REASONS, "TEST-NOPE-404");
      console.log(`[回归/${label}] 无关 BLOCK → 停=${!r.loopContinued}`);
      assert.strictEqual(r.recognized, false, `${label}: 不误识别`);
      assert.strictEqual(r.loopContinued, false, `${label}: 该停就停`);
    }
    console.log("[回归判定] 误识别防扩面成立 ✓");
  });
});
