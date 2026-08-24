/**
 * AIPOS-F38 大项B 夹具: 已交回待收账的卡不占名额 — 先红后绿(经 bin 入 run-all)
 *
 * 病灶(2026-08-24 实撞): 工位累积多张已交回未收账卡(F37 系列 executor_status=completed
 * 仍留 claimed/ 等审计), findInFlightCards 全数计入在途 → 启动 held 检查反复对已交回卡
 * 发起托管交回、余热永不终停、工位被"一卡一会话"堵死。
 *
 * 谓词对齐: 已交回 = executor_status=completed 或 audit_readiness=ready
 * (与 gate already_returned / loop-decisions isReturned 同一谓词); 收账仍由 sweep 负责。
 *
 *  - 红  = F38 前(main)真函数源: 已交回卡计入在途(3 张, 含 2 张已交回)
 *  - 绿  = F38 后(本卡)真函数源: 同一目录只剩 1 张未交回卡占名额
 *  - 活体 = 对真实治理工作区(只读)跑 F38 后真函数: 本工位在途=0(三张已交回),
 *          循环可领新卡(清账前提实证)
 *
 * 跑法: node tests/f38b-inflight-quota.test.ts (或经 run-all.sh 常驻)
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, writeFileSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import * as realFs from "node:fs";
import * as realPath from "node:path";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, "package.json")) && existsSync(join(dir, "agents"))) return dir;
    dir = join(dir, "..");
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

// —— 从真实源码提取 findInFlightCards + 依赖 extractFrontmatterField, 原样重放(零副本语义) ——
function extractFn(src: string, name: string): string {
  const start = src.indexOf(`function ${name}(`);
  assert.ok(start > 0, `${name} 应存在于源码`);
  let depth = 0, i = src.indexOf("{", start);
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error(`${name} 提取失败`);
}
async function buildFinder(src: string): Promise<(fs: any, path: any, root: string, inst: string) => string[]> {
  // 提取真实源码函数体 → 落临时 .ts 动态 import(Node 22 类型剥离), 零副本语义重放
  const code = `${extractFn(src, "extractFrontmatterField")}\n${extractFn(src, "findInFlightCards")}\nexport { findInFlightCards, extractFrontmatterField };`;
  const tmp = join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/tests/.tmp-f38b-finder.ts");
  writeFileSync(tmp, code);
  try {
    const mod = await import(`${tmp}?t=${Date.now()}`);
    return mod.findInFlightCards;
  } finally {
    rmSync(tmp, { force: true });
  }
}

const ACTOR = "exec.lybra.kiwiai-dev";
const OTHER = "audit.lybra.kiwiai-dev";

function card(frontmatter: string): string {
  return `---\n${frontmatter}\n---\n# card\n`;
}

// —— 夹具工作区: 4 张卡 = 2 已交回(两种谓词形态) + 1 我未交回 + 1 他人卡 ——
function buildTempWs(): string {
  const ws = join(PROJECT_ROOT, ".tmp-f38b-ws");
  rmSync(ws, { recursive: true, force: true });
  const claimed = join(ws, "5_tasks", "queue", "claimed");
  mkdirSync(claimed, { recursive: true });
  writeFileSync(join(claimed, "aipos-x1.md"), card(
    `task_id: AIPOS-X1\nstatus: claimed\nclaimed_by: ${ACTOR}\nexecutor_status: completed\naudit_readiness: ready`));
  writeFileSync(join(claimed, "aipos-x2.md"), card(
    `task_id: AIPOS-X2\nstatus: claimed\nclaimed_by: ${ACTOR}\naudit_readiness: ready`));
  writeFileSync(join(claimed, "aipos-x3.md"), card(
    `task_id: AIPOS-X3\nstatus: claimed\nclaimed_by: ${ACTOR}`));
  writeFileSync(join(claimed, "aipos-x4.md"), card(
    `task_id: AIPOS-X4\nstatus: claimed\nclaimed_by: ${OTHER}`));
  return ws;
}

// 真实治理工作区(只读)
const REAL_WS = "/home/kiwi/ai-project-os/2_projects/lybra";

describe("F38-大项B 已交回不占名额 — 先红后绿", () => {
  it("红: F38 前(main)真函数 — 已交回卡仍计入在途(占名额)", async () => {
    const preSrc = gitOut(["show", `main:${LOOP_REL}`]);
    const finder = await buildFinder(preSrc);
    const ws = buildTempWs();
    const inFlight = finder(realFs, realPath, ws, ACTOR);
    console.log(`[RED] F38前 findInFlightCards(${ACTOR}) = [${inFlight.join(", ")}] (4卡工作区: X1/X2已交回, X3我未交回, X4他人)`);
    assert.deepStrictEqual(inFlight.sort(), ["AIPOS-X1", "AIPOS-X2", "AIPOS-X3"], "F38 前: 已交回的 X1/X2 也占名额");
    console.log("[RED 判定] 修复前=红: 已交回待收账卡堵死工位(启动反复交回/余热不终停) ✗");
    rmSync(ws, { recursive: true, force: true });
  });

  it("绿: F38 后真函数 — 只有未交回卡占名额; 他人卡仍不算", async () => {
    const headSrc = readFileSync(join(PROJECT_ROOT, LOOP_REL), "utf-8");
    const finder = await buildFinder(headSrc);
    const ws = buildTempWs();
    const inFlight = finder(realFs, realPath, ws, ACTOR);
    console.log(`[GREEN] F38后 findInFlightCards(${ACTOR}) = [${inFlight.join(", ")}]`);
    assert.deepStrictEqual(inFlight, ["AIPOS-X3"], "只数未交回的 X3; X1/X2 已交回不占, X4 他人不算");
    console.log("[GREEN 判定] 已交回待收账的卡不占名额(收账归 sweep), 工位可领新卡 ✓");
    rmSync(ws, { recursive: true, force: true });
  });

  it("活体: 真实治理工作区(只读) — 本工位三张已交回卡在途=0, 循环可领新卡", async () => {
    const headSrc = readFileSync(join(PROJECT_ROOT, LOOP_REL), "utf-8");
    const finder = await buildFinder(headSrc);
    const inFlight = finder(realFs, realPath, REAL_WS, ACTOR);
    const mine = realFs.readdirSync(join(REAL_WS, "5_tasks", "queue", "claimed"))
      .filter((f: string) => f.endsWith(".md"))
      .map((f: string) => realFs.readFileSync(join(REAL_WS, "5_tasks", "queue", "claimed", f), "utf-8"))
      .filter((c: string) => c.includes(`claimed_by: ${ACTOR}`))
      .map((c: string) => {
        const id = c.match(/task_id:\s*(\S+)/)?.[1] || "?";
        const ret = /executor_status:\s*completed/.test(c) || /audit_readiness:\s*ready/.test(c);
        return `${id}${ret ? "(已交回)" : "(未交回)"}`;
      });
    console.log(`[活体] 真实工作区本工位名下 claimed 卡: [${mine.join(", ")}] → findInFlightCards = [${inFlight.join(", ")}]`);
    // 当前时点: F37 系列三张均已交回(executor_status=completed) → 在途应为 0
    // (若未来存在未交回卡, 本断言以"每个在途卡都确实未交回"为准 — 谓词正确性而非盘上时点)
    for (const id of inFlight) {
      const c = realFs.readFileSync(join(REAL_WS, "5_tasks", "queue", "claimed", `${id.toLowerCase()}.md`), "utf-8");
      assert.ok(!/executor_status:\s*completed/.test(c) && !/audit_readiness:\s*ready/.test(c),
        `在途卡 ${id} 必须确实未交回(已交回卡占名额=回归)`);
    }
    console.log("[活体 判定] 已交回卡全部出集 — 清账前提成立(部署后循环即可自领新卡) ✓");
  });
});
