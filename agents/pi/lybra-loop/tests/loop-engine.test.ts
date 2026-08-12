/**
 * loop-engine 测试 —— executeTick 循环决策流(红线最集中的地方,headless 全覆盖)。
 * 覆盖:held 停 / none 等待 / 信封内放行 / 信封外跳过→全跳过停 / BLOCK 立停 / fetch 失败立停 /
 *      triedTaskIds 去重(不扫射)/ 卡无 path 立停。
 * 跑法:`node tests/loop-engine.test.ts`。
 */
import { executeTick, freshState, Logger, buildClaimArgs, type GateReadFace, type TickContext } from "../loop-engine.ts";
import { mkdtempSync, rmSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { AnyDict } from "../loop-decisions.ts";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

const tmp = mkdtempSync(join(tmpdir(), "lybra-engine-"));
const logPath = join(tmp, "loop.log");
const logger = new Logger(logPath);
function mkCtx(client: GateReadFace, state = freshState()): TickContext {
  return {
    client,
    actor: "me",
    agentInstance: "me",
    ownerPolicyRef: "pol-1",
    workspaceRoot: "/ws",
    activeSessionId: "sess-x",
    state,
    logger,
  };
}
function mockClient(tasks: AnyDict[], claimRespByTask: Record<string, AnyDict>): GateReadFace {
  return {
    async queueTasks() {
      return tasks;
    },
    async claimDryRun(args: AnyDict) {
      return claimRespByTask[String(args.task_id)];
    },
  };
}
function pending(id: string, extra: Record<string, unknown> = {}) {
  return { task_id: id, queue_state: "pending", path: `2_projects/lybra/5_tasks/queue/pending/${id}.md`, metadata: { assigned_to: "me", ...extra } };
}
function claimed(id: string, by: string) {
  return { task_id: id, queue_state: "claimed", metadata: { claimed_by: by } };
}

// 1. held → stop(一卡一会话)
let r = await executeTick(mkCtx(mockClient([claimed("HELD", "me")], {})));
check("held → stop", r.kind === "stop" && r.reason.includes("HELD"));

// 2. none(空)→ wait
r = await executeTick(mkCtx(mockClient([], {})));
check("空队列 → wait", r.kind === "wait");

// 3. none(pending 但不匹配 actor)→ wait
r = await executeTick(mkCtx(mockClient([pending("T", { assigned_to: "other" })], {})));
check("不匹配 actor → wait", r.kind === "wait");

// 4. 信封内放行 → release + cardAbsPath(F-EXT001-7:放行后路径=claimed)
r = await executeTick(
  mkCtx(
    mockClient([pending("T")], {
      T: { autonomy_mode: "PreAuthorized", owner_confirmation_required: false, preauthorized_release: true, owner_policy_ref: "pol-1" },
    }),
  ),
);
check("信封内 → release", r.kind === "release" && r.task.task_id === "T");
// F-EXT001-7(FIX3):放行后路径推导为 claimed 位置
check("release cardAbsPath=claimed", r.kind === "release" && r.cardAbsPath === "/ws/5_tasks/queue/claimed/T.md");
check("cardAbsPath 不含 pending", r.kind === "release" && !r.cardAbsPath.includes("queue/pending/"));

// 5. 信封外(Supervised)单张 → 全跳过 → stop
r = await executeTick(
  mkCtx(
    mockClient([pending("T")], {
      T: { autonomy_mode: "Supervised", owner_confirmation_required: true, dry_run_token: "tk" },
    }),
  ),
);
check("单张信封外 → stop", r.kind === "stop" && r.reason.includes("信封外"));

// 6. 混合:第一张信封外(跳过),第二张信封内(放行)
r = await executeTick(
  mkCtx(
    mockClient([pending("OUT"), pending("IN")], {
      OUT: { autonomy_mode: "Supervised", owner_confirmation_required: true, dry_run_token: "tk1" },
      IN: { autonomy_mode: "PreAuthorized", owner_confirmation_required: false, preauthorized_release: true },
    }),
  ),
);
check("混合:跳过信封外、放行信封内", r.kind === "release" && r.task.task_id === "IN");
// F-EXT001-7(FIX3):放行后路径为 claimed
check("混合放行:cardAbsPath=claimed", r.kind === "release" && r.cardAbsPath === "/ws/5_tasks/queue/claimed/IN.md");

// 7. BLOCK → 立停(不跳过继续)
r = await executeTick(
  mkCtx(
    mockClient([pending("BAD"), pending("GOOD")], {
      BAD: { verdict: "BLOCK", isError: true, blocking_reasons: ["task not claimable"] },
      GOOD: { autonomy_mode: "PreAuthorized", owner_confirmation_required: false, preauthorized_release: true },
    }),
  ),
);
check("BLOCK → stop(立停,不试 GOOD)", r.kind === "stop" && r.reason.includes("BLOCK"));

// 8. claimDryRun 抛错 → stop
const throwingClient: GateReadFace = {
  async queueTasks() {
    return [pending("T")];
  },
  async claimDryRun() {
    throw new Error("connection reset");
  },
};
r = await executeTick(mkCtx(throwingClient));
check("claimDryRun 抛错 → stop", r.kind === "stop" && r.reason.includes("connection reset"));

// 9. queueTasks 抛错 → stop
const fetchThrow: GateReadFace = {
  async queueTasks() {
    throw new Error("gate down");
  },
  async claimDryRun() {
    return {};
  },
};
r = await executeTick(mkCtx(fetchThrow));
check("queueTasks 抛错 → stop", r.kind === "stop" && r.reason.includes("gate down"));

// 10. triedTaskIds 去重:第二 tick 不再 dry-run 已试过的信封外卡 → 全信封外 stop
const state = freshState();
const c = mockClient([pending("OUT")], { OUT: { autonomy_mode: "Supervised", owner_confirmation_required: true, dry_run_token: "tk" } });
await executeTick(mkCtx(c, state)); // 第一轮:试 OUT,跳过
r = await executeTick(mkCtx(c, state)); // 第二轮:OUT 已 tried → fresh 空 + claimable 非空 → stop-all-envelope
check("triedTaskIds 去重:第二轮 stop-all-envelope", r.kind === "stop" && r.reason.includes("信封外"));
check("triedTaskIds 记录了 OUT", state.triedTaskIds.has("OUT"));

// F-EXT001-7(FIX3):卡无 path 测试已废弃(放行后路径直接用 task_id 推导,不依赖 task.path)

// 12. buildClaimArgs:PreAuthorized + active_session_id + 不含 token
const args = buildClaimArgs(pending("T", { agent_instance: "inst-9" }), mkCtx(mockClient([], {})));
check("claimArgs autonomy=PreAuthorized", args.autonomy_mode === "PreAuthorized");
check("claimArgs active_session_id", args.active_session_id === "sess-x");
check("claimArgs owner_policy_ref", args.owner_policy_ref === "pol-1");
check("claimArgs agent_instance 取 metadata", args.agent_instance === "inst-9");
check("claimArgs 无 token 字样", !/token|bearer|secret/i.test(JSON.stringify(args)));
// F-EXT001-5(FIX2):钉死选择器恰为一个(task_id),不含 task_path(gate 要求二选一)
check("claimArgs 含 task_id", "task_id" in args && args.task_id === "T");
check("claimArgs 不含 task_path", !("task_path" in args));
const selectorCount = ("task_id" in args ? 1 : 0) + ("task_path" in args ? 1 : 0);
check("claimArgs 恰含一个选择器", selectorCount === 1);

// --- Logger:写入 + 轮转 ---
logger.info("test-event", { foo: "bar" });
const content = readFileSync(logPath, "utf-8");
check("Logger 写了 JSON 行", content.includes("test-event") && content.includes("foo"));
check("Logger 含 fetched 等动作", /fetched|claim-decision|stop/.test(content));
check("Logger 不含 token", !/secret|bearer/i.test(content));

// 轮转:写超大内容触发 rename
writeFileSync(logPath, "x".repeat(3 * 1024 * 1024)); // 3MB > 2MB 阈值
logger.info("after-rotate", {});
check("轮转:产生 loop.log.1", existsSync(`${logPath}.1`));
const after = readFileSync(logPath, "utf-8");
check("轮转后新文件含 after-rotate", after.includes("after-rotate"));

rmSync(tmp, { recursive: true, force: true });

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
