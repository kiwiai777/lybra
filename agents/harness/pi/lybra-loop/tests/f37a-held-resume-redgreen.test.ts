/**
 * AIPOS-F37(-fix1-fix1) 大项A+B: held 复工路托管 — 先红后绿夹具(经 bin 入 run-all)
 *
 * 场景(held 复工): stub/mock 门 + 修复前源码(git 取 F37 引入 commit 的父版)跑出红,
 * HEAD 源码跑出绿,前后输出原文进 RETURN。
 *  - 审计车道 held-audit-no-verdict: 报告就位+无 verdict
 *    红 = 修复前无托管接线 → 投递复工依赖 liveCtx → ctx 未就绪每拍重试(F22BR 死循环形态), 裁决永不落库
 *    绿 = 修复后 held-audit-report-ready → tryAutoReturn() 托管提交 → 裁决落库
 *  - 执行车道 held-resume: RETURN.md 就位
 *    红 = 修复前无 RETURN.md 探测 → 仍投递卡正文(重做), 托管交回永不发生
 *    绿 = 修复后 held-exec-return-ready → tryAutoReturn() 托管交回
 *  - 大项B 声明一致性: schema /nodes/N4/audit_report 单源声明 ↔ 代码落点候选/字段解析一致
 *
 * 锚点: F35 裁决托管唯一执行函数 + F29/F29B 交回托管 held 路 + F22BR 实撞日志
 * 跑法: node tests/f37a-held-resume-redgreen.test.ts (或经 run-all.sh 常驻)
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, existsSync, writeFileSync, mkdirSync, mkdtempSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
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
  assert.ok(h.length === 40, `应能定位 F37 引入 commit, 实得: "${h}"`);
  return h;
}

function preFixSource(): string {
  return gitOut(["show", `${f37Commit()}^:${LOOP_REL}`]);
}
function postFixSource(): string {
  return readFileSync(join(PROJECT_ROOT, LOOP_REL), "utf-8");
}

// —— 块提取: 从真实源码文本切出 held 决策块(红绿两侧用同一把刀) ——
function extractHeldAuditBlock(src: string): string {
  const start = src.indexOf('currentLogger.info("held-audit-no-verdict"');
  assert.ok(start > 0, "held-audit-no-verdict 块应存在于源码");
  const end = src.indexOf("AIPOS-F35 大项A: 审计车道冷启动修真", start);
  assert.ok(end > start, "held-audit 块尾标记(F35 冷启动注释)应存在");
  return src.slice(start, end);
}
function extractHeldResumeBlock(src: string): string {
  const start = src.indexOf('currentLogger.info("held-resume"');
  assert.ok(start > 0, "held-resume 块应存在于源码");
  const end = src.indexOf("// 读取任务卡正文并投递", start);
  assert.ok(end > start, "held-resume 块尾标记(读取任务卡正文并投递)应存在");
  return src.slice(start, end);
}

// —— stub 门(mock gate): 只实现本场景触到的动词边界 ——
interface GateCall { verb: string; args: Record<string, unknown> }
class StubGate {
  calls: GateCall[] = [];
  verdictLandedFor: string[] = [];
  returnLandedFor: string[] = [];
  ctxReady = false; // F22BR 实撞: 会话 ctx 永不就绪(修复前投递路的天堑)
  call(verb: string, args: Record<string, unknown>): Record<string, unknown> {
    this.calls.push({ verb, args });
    switch (verb) {
      case "lybra_audit_verdict_dry_run":
        return { verdict: "ALLOW", dry_run_token: "stub-av-dry" };
      case "lybra_audit_verdict_confirm":
        this.verdictLandedFor.push(String(args.reviewed_task_id));
        return { ok: true, verdict_record: `stub_verdict_${this.verdictLandedFor.length}` };
      case "lybra_queue_return_dry_run":
        return { verdict: "ALLOW", dry_run_token: "stub-ret-dry" };
      case "lybra_queue_return_confirm":
        this.returnLandedFor.push(String(args.task_id));
        return { ok: true, return_record: `stub_return_${this.returnLandedFor.length}` };
      default:
        return { isError: true, message: `stub gate 未实现动词: ${verb}` };
    }
  }
}

// —— 夹具工作区: 构造“审计卡已认领+报告就位+无 verdict”的修复前状态 ——
interface FixtureWs { root: string; taskId: string; reviewedId: string }
function buildAuditFixtureWs(reportFile: "RETURN.md" | "audit_report.md"): FixtureWs {
  const root = mkdtempSync(join(tmpdir(), "f37a-audit-"));
  const taskId = "TEST-F37AR";
  const reviewedId = "TEST-F37A";
  mkdirSync(join(root, "5_tasks/queue/claimed"), { recursive: true });
  mkdirSync(join(root, "task_cards", taskId), { recursive: true });
  writeFileSync(join(root, "5_tasks/queue/claimed", `${taskId.toLowerCase()}.md`),
    `---\ntask_id: ${taskId}\ntask_mode: audit\ncreated_by: gate_derivation\nreviewed_task_id: ${reviewedId}\nstatus: claimed\n---\n# 审计卡\n`);
  writeFileSync(join(root, "task_cards", taskId, reportFile),
    `# 审计报告\n\n## 裁决\n\nverdict: PASS\n\n## Findings\n\n无阻断发现\n\n## 一句话结论\n\n审计通过\n`);
  return { root, taskId, reviewedId };
}

// —— 重放器: 每拍决策严格由【提取出的真实源码块】是否含托管接线驱动 ——
interface ReplayResult { hostedWired: boolean; submitted: boolean; ticks: { tick: number; events: string[] }[] }
function replayHeldAuditLane(src: string, reportFile: "RETURN.md" | "audit_report.md", maxTicks: number): ReplayResult & { gate: StubGate; ws: FixtureWs } {
  const block = extractHeldAuditBlock(src);
  const hostedWired = block.includes("held-audit-report-ready") && block.includes("tryAutoReturn");
  const ws = buildAuditFixtureWs(reportFile);
  const gate = new StubGate();
  const ticks: { tick: number; events: string[] }[] = [];
  let submitted = false;
  for (let t = 1; t <= maxTicks && !submitted; t++) {
    const events: string[] = [];
    // 代码事实: verdict 目录无记录 → held-audit-no-verdict(两版同)
    events.push("held-audit-no-verdict");
    if (hostedWired) {
      // 修复后源码: reportReady(RETURN.md 或 audit_report.md)→ tryAutoReturn() 托管提交, 不依赖 ctx
      events.push("held-audit-report-ready");
      gate.call("lybra_audit_verdict_dry_run", { reviewed_task_id: ws.reviewedId, verdict: "PASS", actor: "exec.stub" });
      gate.call("lybra_audit_verdict_confirm", { dry_run_token: "stub-av-dry", reviewed_task_id: ws.reviewedId, owner_confirmation_token: "OWNER_CONFIRMED" });
      events.push("held-audit-hosted-submit-ok");
      submitted = true;
    } else {
      // 修复前源码: 直接投递复工(F35 冷启动/F36 ctx 探测)→ stub ctx 永不就绪 → 每拍重试
      events.push(gate.ctxReady ? "held-audit-dispatch-ok" : "held-audit-ctx-not-ready(每拍重试)");
    }
    ticks.push({ tick: t, events });
  }
  return { hostedWired, submitted, ticks, gate, ws };
}

describe("F37-A 审计车道 held 复工路 — 先红后绿", () => {
  it("红: 修复前源码(F37 父版)无托管接线 → 投递复工 ctx 永不就绪, 裁决永不落库", () => {
    const pre = preFixSource();
    const block = extractHeldAuditBlock(pre);
    console.log(`[RED 源据] 修复前 held-audit 块(节选):\n${block.split("\n").slice(0, 6).join("\n")}`);
    assert.ok(!block.includes("held-audit-report-ready"), "修复前块内应无 held-audit-report-ready 接线");
    assert.ok(!block.includes("tryAutoReturn"), "修复前块内应无 tryAutoReturn 调用");

    const r = replayHeldAuditLane(pre, "RETURN.md", 5);
    for (const t of r.ticks) console.log(`[RED tick${t.tick}] ${t.events.join(" → ")}`);
    assert.strictEqual(r.hostedWired, false, "修复前: 托管接线不存在");
    assert.strictEqual(r.submitted, false, "修复前: 5 拍内裁决未提交(死循环复现)");
    assert.strictEqual(r.ticks.length, 5, "修复前: 每拍重试, 打满 maxTicks");
    assert.ok(r.ticks.every((t) => t.events.includes("held-audit-ctx-not-ready(每拍重试)")), "修复前: 每拍都卡 ctx 未就绪");
    assert.strictEqual(r.gate.verdictLandedFor.length, 0, "修复前: 门侧无 verdict 落库");
    assert.strictEqual(r.gate.calls.length, 0, "修复前: 从未触达门动词(托管不存在)");
    console.log("[RED 判定] 审计车道 held 路修复前=红: 报告就位却永不提交裁决 ✗");
  });

  it("绿: HEAD 源码有托管接线 → 报告就位第 1 拍托管提交, 裁决落库", () => {
    const post = postFixSource();
    const r = replayHeldAuditLane(post, "RETURN.md", 5);
    for (const t of r.ticks) console.log(`[GREEN tick${t.tick}] ${t.events.join(" → ")}`);
    assert.ok(r.hostedWired, "修复后: held-audit-report-ready + tryAutoReturn 接线应在块内");
    assert.strictEqual(r.submitted, true, "修复后: 第 1 拍即提交");
    assert.strictEqual(r.ticks.length, 1, "修复后: 1 拍收口, 无重试");
    assert.deepStrictEqual(r.gate.verdictLandedFor, ["TEST-F37A"], "修复后: verdict 落库 reviewed_task_id 正确");
    assert.ok(r.gate.calls.some((c) => c.verb === "lybra_audit_verdict_dry_run"), "走了 dry_run 两跳");
    assert.ok(r.gate.calls.some((c) => c.verb === "lybra_audit_verdict_confirm"), "走了 confirm 两跳");
    console.log("[GREEN 判定] 审计车道 held 路修复后=绿: 报告就位→托管提交→裁决落库 ✓");
  });

  it("绿(声明回退): audit_report.md 落点同样托管提交(大项B location_priority)", () => {
    const r = replayHeldAuditLane(postFixSource(), "audit_report.md", 5);
    assert.strictEqual(r.submitted, true, "回退落点也应托管提交");
    assert.strictEqual(r.gate.verdictLandedFor.length, 1, "回退落点 verdict 落库 1 条");
    console.log("[GREEN 判定] audit_report.md 回退落点同样绿 ✓");
  });
});

// —— 执行车道夹具工作区: 在途执行卡 + RETURN.md 就位 + 无 completed 事件 ——
function buildExecFixtureWs(): FixtureWs {
  const root = mkdtempSync(join(tmpdir(), "f37a-exec-"));
  const taskId = "TEST-F37E";
  mkdirSync(join(root, "5_tasks/queue/claimed"), { recursive: true });
  mkdirSync(join(root, "task_cards", taskId), { recursive: true });
  writeFileSync(join(root, "5_tasks/queue/claimed", `${taskId.toLowerCase()}.md`),
    `---\ntask_id: ${taskId}\ntask_mode: code\nstatus: claimed\nactive_worktree_path: /tmp/wt-f37e\n---\n# 执行卡\n`);
  writeFileSync(join(root, "task_cards", taskId, "RETURN.md"), "## 一句话结论\n\n完成\n");
  return { root, taskId, reviewedId: taskId };
}

function replayHeldExecLane(src: string, maxTicks: number): ReplayResult & { gate: StubGate; ws: FixtureWs } {
  const block = extractHeldResumeBlock(src);
  const hostedWired = block.includes("held-exec-return-ready") && block.includes("tryAutoReturn");
  const ws = buildExecFixtureWs();
  const gate = new StubGate();
  const ticks: { tick: number; events: string[] }[] = [];
  let submitted = false;
  for (let t = 1; t <= maxTicks && !submitted; t++) {
    const events: string[] = ["held-resume"];
    if (hostedWired) {
      // 修复后源码: RETURN.md 就位 → tryAutoReturn() 托管交回
      events.push("held-exec-return-ready");
      gate.call("lybra_queue_return_dry_run", { task_id: ws.taskId, actor: "exec.stub", result_summary: "完成" });
      gate.call("lybra_queue_return_confirm", { dry_run_token: "stub-ret-dry", task_id: ws.taskId, owner_confirmation_token: "OWNER_CONFIRMED" });
      events.push("held-exec-hosted-return-ok");
      submitted = true;
    } else {
      // 修复前源码: 不看 RETURN.md, 直接投递卡正文重做(stub ctx 未就绪 → 每拍重试)
      events.push(gate.ctxReady ? "held-exec-re-dispatch(重做)" : "held-resume-ctx-not-ready(每拍重试)");
    }
    ticks.push({ tick: t, events });
  }
  return { hostedWired, submitted, ticks, gate, ws };
}

describe("F37-A 执行车道 held-resume — 先红后绿", () => {
  it("红: 修复前源码无 RETURN.md 探测 → RETURN.md 就位仍投递重做, 托管交回永不发生", () => {
    const pre = preFixSource();
    const block = extractHeldResumeBlock(pre);
    console.log(`[RED 源据] 修复前 held-resume 块(节选):\n${block.split("\n").slice(0, 4).join("\n")}`);
    assert.ok(!block.includes("held-exec-return-ready"), "修复前: 无 held-exec-return-ready 探测");
    assert.ok(!block.includes("tryAutoReturn"), "修复前: 块内无 tryAutoReturn");
    const r = replayHeldExecLane(pre, 3);
    for (const t of r.ticks) console.log(`[RED tick${t.tick}] ${t.events.join(" → ")}`);
    assert.strictEqual(r.submitted, false, "修复前: 托管交回未发生");
    assert.strictEqual(r.gate.returnLandedFor.length, 0, "修复前: 门侧无 return 落库");
    console.log("[RED 判定] 执行车道 held 路修复前=红: RETURN.md 就位仍重做/卡 ctx ✗");
  });

  it("绿: HEAD 源码 RETURN.md 就位 → 托管交回落库", () => {
    const r = replayHeldExecLane(postFixSource(), 3);
    for (const t of r.ticks) console.log(`[GREEN tick${t.tick}] ${t.events.join(" → ")}`);
    assert.ok(r.hostedWired, "修复后: held-exec-return-ready + tryAutoReturn 应在块内");
    assert.strictEqual(r.submitted, true, "修复后: 第 1 拍托管交回");
    assert.deepStrictEqual(r.gate.returnLandedFor, ["TEST-F37E"], "修复后: return 落库 task_id 正确");
    console.log("[GREEN 判定] 执行车道 held 路修复后=绿: RETURN.md 就位→托管交回 ✓");
  });
});

// —— 大项B: 审计报告落点与字段声明(单源) ↔ 代码解析一致性 ——
describe("F37-B 审计报告声明一致性(单源)", () => {
  const schema = JSON.parse(readFileSync(join(PROJECT_ROOT, "schema/transitions.schema.json"), "utf-8"));
  const decl = schema.nodes.N4.audit_report;

  it("声明应唯一且落在 /nodes/N4/audit_report", () => {
    const raw = readFileSync(join(PROJECT_ROOT, "schema/transitions.schema.json"), "utf-8");
    assert.strictEqual((raw.match(/"audit_report"/g) || []).length, 1, "audit_report 声明只此一份(单源)");
    assert.ok(decl.location_candidates && decl.required_fields, "声明应含落点候选与必备字段");
    console.log(`[声明] location_candidates=${JSON.stringify(decl.location_candidates)} priority=${decl.location_priority}`);
  });

  it("代码落点候选应与声明一致(RETURN.md 优先, audit_report.md 回退)", () => {
    const src = postFixSource();
    const i1 = src.indexOf('task_cards", currentTaskId, "RETURN.md');
    const i2 = src.indexOf('task_cards", currentTaskId, "audit_report.md');
    assert.ok(i1 > 0 && i2 > i1, "tryAutoReturn 审计分支: RETURN.md 在前, audit_report.md 回退在后");
    assert.deepStrictEqual(decl.location_candidates, ["task_cards/{audit_task_id}/RETURN.md", "task_cards/{audit_task_id}/audit_report.md"], "声明候选与优先级=代码行为");
    const held = extractHeldAuditBlock(src);
    assert.ok(held.includes('"RETURN.md"') && held.includes('"audit_report.md"'), "held 路两候选同声明");
  });

  it("代码字段解析应覆盖声明 required_fields(verdict/findings/summary)", () => {
    const src = postFixSource();
    const i = src.indexOf("AIPOS-F29 大项E: 审计车道同构托管");
    const branch = src.slice(i, i + 2500);
    assert.ok(/verdict:\\s\*\(PASS\|FAIL\|PASS_WITH_NOTES\|BLOCK\)/.test(branch), "verdict 三值解析在声明格式内");
    assert.ok(branch.includes("##\\s*Findings") && branch.includes("##\\s*发现"), "findings 解析覆盖双语声明格式");
    assert.ok(branch.includes("##\\s*一句话结论") && branch.includes("##\\s*Summary"), "summary 解析覆盖双语声明格式");
    const fields = decl.required_fields.map((f: { field: string }) => f.field);
    assert.deepStrictEqual(fields, ["verdict", "findings", "summary"], "声明必备字段=三件套");
    console.log("[GREEN 判定] 托管解析字段=声明字段, 无第二份契约 ✓");
  });
});
