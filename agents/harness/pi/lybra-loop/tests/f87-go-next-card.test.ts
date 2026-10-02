/**
 * AIPOS-F87 件③(TS 侧): 开工选卡由产品给出 —— /go 只读 my-tasks 的 next_card, 不自行挑卡。headless 夹具。
 *
 *  A. 静态断言: go.ts 不含 `claimedTasks[0]` / `claimedTasks` / 任何 `[0]` 取首张 / `tasks.filter` 本地筛选; 读 next_card 与 next_card_excluded。
 *  B. next_card 指向列表中非首张的卡 → kickoff 用 next_card(证明不取首张)。
 *  C. next_card=null + 原因列表 → refused, 原因逐条原样转述; 原因列表为空 → none(等待分配); 旧产品输出(无 next_card)→ refused。
 *
 * 跑法: `node tests/f87-go-next-card.test.ts`
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { planGo } from "../../_shared/extensions/go.ts";

let failures = 0;
function check(name: string, ok: boolean, detail = "") {
  console.log(`${ok ? "✓" : "✗"} ${name}${!ok && detail ? `\n    ${detail}` : ""}`);
  if (!ok) failures++;
}

const GO_TS = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "_shared", "extensions", "go.ts");
const src = readFileSync(GO_TS, "utf-8");

// ---------------- A. 静态断言 ----------------
check("A1 go.ts 不含 claimedTasks[0](取首张已删除)", !src.includes("claimedTasks[0]"));
check("A2 go.ts 不含 claimedTasks(无本地已认领卡列表)", !src.includes("claimedTasks"));
check("A3 go.ts 不含任何 [0] 取首元素", !/\[0\]/.test(src));
check("A4 go.ts 不对 tasks 做 filter/sort 本地挑选", !/tasks\s*\.\s*(filter|sort|find)\b/.test(src) && !/\.sort\(/.test(src));
check("A5 go.ts 读产品字段 next_card / next_card_excluded", src.includes("next_card") && src.includes("next_card_excluded"));

// ---------------- B. 用 next_card, 不取首张 ----------------
const older = {
  task_id: "AIPOS-OLD", queue_state: "claimed", card_path: "/g/5_tasks/queue/claimed/aipos-old.md",
  worktree_path: "/r/.worktrees/AIPOS-OLD", worktree_exists: false,
  worktree_refusal: { code: "WORKTREE_NOT_CREATED", reason: "工作树 /r/.worktrees/AIPOS-OLD 尚未建立: 认领由驱动方完成并建树" },
  report_path: "/g/task_cards/AIPOS-OLD/RETURN.md", report_refusal: null,
};
const newer = {
  task_id: "AIPOS-NEW", queue_state: "claimed", card_path: "/g/5_tasks/queue/claimed/aipos-new.md",
  worktree_path: "/r/.worktrees/AIPOS-NEW", worktree_exists: true, worktree_refusal: null,
  report_path: "/g/task_cards/AIPOS-NEW/RETURN.md", report_refusal: null,
};
const planB = planGo({
  tasks: [older, newer],
  next_card: { task_id: "AIPOS-NEW", card_path: newer.card_path, worktree_path: newer.worktree_path, report_path: newer.report_path,
    claimed_at: "2026-10-02T08:00:00Z" },
  next_card_excluded: [{ task_id: "AIPOS-OLD", code: "WORKTREE_NOT_CREATED", reason: older.worktree_refusal.reason }],
});
check("B1 next_card=非首张卡 → kickoff 该卡", planB.kind === "kickoff" && planB.taskId === "AIPOS-NEW", JSON.stringify(planB));
if (planB.kind === "kickoff") {
  check("B2 kickoff 用 next_card 的工作树/报告/卡路径原值", planB.kickoff.includes(`工作树路径: ${newer.worktree_path}`)
    && planB.kickoff.includes(`报告落点: ${newer.report_path}`) && planB.kickoff.includes(`任务卡路径: ${newer.card_path}`), planB.kickoff);
}

// ---------------- C. 无可开工卡 / 等待 / 旧输出 ----------------
const excluded = [
  { task_id: "AIPOS-BAD", code: "FRONTMATTER_INVALID", reason: "卡面 frontmatter 不可解析(PyYAML parse failed), 不能开工; 卡面由顾问经产品规整, 工位按 block-and-report 上报" },
  { task_id: "AIPOS-OLD", code: "WORKTREE_NOT_CREATED", reason: older.worktree_refusal.reason },
];
const planC1 = planGo({ tasks: [older], next_card: null, next_card_excluded: excluded });
check("C1 next_card=null 且有原因 → refused", planC1.kind === "refused", JSON.stringify(planC1));
if (planC1.kind === "refused") {
  check("C2 原因逐条原样转述(task_id + code + reason)",
    excluded.every((e) => planC1.message.includes(`- ${e.task_id} ${e.code}: ${e.reason}`)), planC1.message);
  check("C3 文案零门动词", !/lybra next|next --run|lybra_\w+|queue claim/.test(planC1.message), planC1.message);
  console.log("---- 无可开工卡 原文 ----\n" + planC1.message + "\n------------------------");
}
check("C4 next_card=null 且原因为空 → none(等待分配)", planGo({ tasks: [], next_card: null, next_card_excluded: [] }).kind === "none");
check("C5 旧产品输出(无 next_card 字段)→ refused, 不在本地挑卡", planGo({ tasks: [newer] }).kind === "refused");
check("C6 next_card 字段不全 → refused", planGo({ tasks: [newer], next_card: { task_id: "AIPOS-NEW" }, next_card_excluded: [] }).kind === "refused");

console.log(failures === 0 ? "\n✓ AIPOS-F87 /go 产品选卡夹具全部通过" : `\n✗ ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);
