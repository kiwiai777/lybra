/**
 * loop-decisions 纯逻辑测试 —— 红线"发起≠授权"的回归钉。
 * 跑法:`node tests/loop-decisions.test.ts`(Node 22 类型剥离)。
 */
import {
  classifyTasks,
  decideClaimDryRun,
  buildKickoff,
  resolveCardPath,
  logLine,
  actorMatches,
  isHolder,
} from "../loop-decisions.ts";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}
function eq(name: string, actual: unknown, expected: unknown) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  check(`${name} (got ${JSON.stringify(actual)})`, ok);
}

function task(id: string, state: string, meta: Record<string, string> = {}) {
  return { task_id: id, queue_state: state, metadata: meta };
}

// --- classifyTasks:三态 + held 抑制 ---

eq("none:空队列", classifyTasks([], "me").state, "none");
eq("none:pending 但 assigned 给别人", classifyTasks([task("T", "pending", { assigned_to: "other" })], "me").state, "none");
eq(
  "claimable:pending + assigned_to 匹配",
  classifyTasks([task("T", "pending", { assigned_to: "me" })], "me").state,
  "claimable",
);
eq(
  "claimable:pending + agent_instance 匹配",
  classifyTasks([task("T", "pending", { agent_instance: "me" })], "me").state,
  "claimable",
);
eq(
  "held:claimed + claimed_by 匹配",
  classifyTasks([task("T", "claimed", { claimed_by: "me" })], "me").state,
  "held",
);

// F-248-o3-1:持有者跟 claimed_by,不跟 assigned_to
const mismatched = task("T", "claimed", { assigned_to: "dave", agent_instance: "carol", claimed_by: "carol" });
eq("F-248-o3-1:dave 仅 assigned_to,非持有者", classifyTasks([mismatched], "dave").state, "none");
eq("F-248-o3-1:carol 是 claimed_by,持有者", classifyTasks([mismatched], "carol").state, "held");

// held 抑制新任务(一 session 一 task 前置)
const heldResult = classifyTasks(
  [task("HELD", "claimed", { claimed_by: "me" }), task("NEW", "pending", { assigned_to: "me" })],
  "me",
);
eq("held 抑制:state=held", heldResult.state, "held");
eq("held 抑制:held 列表含 HELD", heldResult.held.map((t) => t.task_id), ["HELD"]);
eq("held 抑制:claimable 仍被分类(供上层决定)", heldResult.claimable.map((t) => t.task_id), ["NEW"]);

// F-LOOP-1:已归还的卡(return 后留 claimed/ 等审计)不算 held,不再堵新领
// 归还谓词与 gate confirm_client already_returned 一致:executor_status/audit_readiness
eq(
  "F-LOOP-1:executor_status=completed 不算 held",
  classifyTasks([task("DONE", "claimed", { claimed_by: "me", executor_status: "completed" })], "me").state,
  "none",
);
eq(
  "F-LOOP-1:audit_readiness=ready 不算 held",
  classifyTasks([task("DONE", "claimed", { claimed_by: "me", audit_readiness: "ready" })], "me").state,
  "none",
);
eq(
  "F-LOOP-1:已归还卡 + pending 新卡 → claimable 放行",
  classifyTasks(
    [
      task("DONE", "claimed", { claimed_by: "me", executor_status: "completed" }),
      task("NEW", "pending", { assigned_to: "me" }),
    ],
    "me",
  ).state,
  "claimable",
);
eq(
  "F-LOOP-1:未归还卡仍 held(回归)",
  classifyTasks([task("WIP", "claimed", { claimed_by: "me" })], "me").state,
  "held",
);

// --- decideClaimDryRun:红线决策门 ---

// 1. 无应答 → stop-error(立停)
eq("无应答 → stop-error", decideClaimDryRun(null, "T").action, "stop-error");
eq("undefined 应答 → stop-error", decideClaimDryRun(undefined, "T").action, "stop-error");

// 2. PreAuthorized 放行 → release(冷启动)
eq(
  "PreAuthorized 放行 → release",
  decideClaimDryRun(
    {
      autonomy_mode: "PreAuthorized",
      owner_confirmation_required: false,
      preauthorized_release: true,
      ok: true,
      isError: false,
      owner_policy_ref: "pol-1",
    },
    "T",
  ).action,
  "release",
);

// 3. Supervised 信封外 → skip-envelope(跳过,绝不 confirm)
const supervised = decideClaimDryRun(
  { autonomy_mode: "Supervised", owner_confirmation_required: true, dry_run_token: "tk-1", isError: false },
  "T",
);
eq("Supervised → skip-envelope", supervised.action, "skip-envelope");
check("skip-envelope reason 含 dry_run_token", JSON.stringify(supervised).includes("tk-1"));
check("skip-envelope reason 说明不绕", JSON.stringify(supervised).includes("不绕"));

// 4. BLOCK / isError → stop-block(立停)
eq("isError=true → stop-block", decideClaimDryRun({ isError: true, verdict: "BLOCK" }, "T").action, "stop-block");
eq("verdict=BLOCK → stop-block", decideClaimDryRun({ verdict: "BLOCK", isError: false }, "T").action, "stop-block");
eq(
  "PreAuthorized 请求但任务不可 claim(BLOCK)→ stop-block",
  decideClaimDryRun({ autonomy_mode: "PreAuthorized", isError: true, verdict: "BLOCK" }, "T").action,
  "stop-block",
);

// 5. 语义不明 → 保守 stop-block
eq(
  "既没放行也没要 confirm → stop-block",
  decideClaimDryRun({ autonomy_mode: "???", isError: false }, "T").action,
  "stop-block",
);

// --- actorMatches / isHolder 单元 ---
check("actorMatches:assigned_to 命中", actorMatches(task("T", "pending", { assigned_to: "me" }), "me"));
check("isHolder:assigned_to 不算持有", !isHolder(task("T", "claimed", { assigned_to: "me" }), "me"));
check("isHolder:claimed_b 才算持有", isHolder(task("T", "claimed", { claimed_by: "me" }), "me"));

// --- 文本生成 ---
check("kickoff 含卡绝对路径", buildKickoff("/abs/card.md").includes("/abs/card.md"));
check("kickoff 含冷启动指令", buildKickoff("/x").includes("冷启动"));
check("kickoff 不含 token 字样", !/token|bearer|secret/i.test(buildKickoff("/x")));
// F-EXT001-7(FIX3):断言钉 — kickoff 不含 pending 路径(对旧实现红)
let kickoffErr = null;
try {
  buildKickoff("/ws/5_tasks/queue/pending/T.md");
} catch (e) {
  kickoffErr = e;
}
check("F-EXT001-7 断言钉:pending 路径抛错", kickoffErr instanceof Error && kickoffErr.message.includes("queue/pending/"));
// 放行后路径(claimed)应可通过
check("claimed 路径通过断言", buildKickoff("/ws/5_tasks/queue/claimed/T.md").includes("/ws/5_tasks/queue/claimed/T.md"));
eq("resolveCardPath:相对路径拼接", resolveCardPath("a/b.md", "/root"), "/root/a/b.md");
eq("resolveCardPath:绝对路径直通", resolveCardPath("/abs/b.md", "/root"), "/abs/b.md");
eq("resolveCardPath:空 → null", resolveCardPath("", "/root"), null);
eq("resolveCardPath:去尾斜杠", resolveCardPath("a/b.md", "/root/"), "/root/a/b.md");

// --- 日志行 ---
const line = logLine("INFO", "release", { task_id: "T", policy: "p1" });
const parsed = JSON.parse(line);
check("logLine 是合法 JSON", typeof parsed === "object");
check("logLine 含 ts/level/action", parsed.ts && parsed.level === "INFO" && parsed.action === "release");
check("logLine 不含 token", !/token|bearer|secret/i.test(line));

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
