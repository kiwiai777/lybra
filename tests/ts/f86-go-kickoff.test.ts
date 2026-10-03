/**
 * AIPOS-F86 件①(TS 侧): 工位 /go 只读产品输出 —— headless 夹具。
 *
 *  A. 静态断言: go.ts 不含工作树/报告落点的自行拼接(`"card"` 字面、`task_cards`、path.join(workspaceRoot …)),
 *     不含 `lybra next` / 门动词(lybra_*), 且确实经 `my-tasks` 取数。
 *  B. 给定 my-tasks JSON(产品 card_workstation_view 字段形): kickoff 文本含该 worktree_path / report_path / card_path 原值。
 *  C. 工作树尚未建立(WORKTREE_NOT_CREATED)→ 拒, 文案 = 「工作树尚未建立…等待驱动方完成认领…block-and-report」, 零门动词;
 *     不可推导(LANE_REPO_UNDECLARED 等)→ 拒并转述产品拒因; 报告落点不可推导 → 拒; 无 claimed → 等待; tasks 缺 → 拒。
 *
 * AIPOS-F87 件③: 选卡改由产品给出(my-tasks 的 next_card / next_card_excluded), 本夹具 B/C 段的输入随之改为产品输出形;
 *   go.ts 自带的「工作树尚未建立」文案常量退役, 改为原样转述产品拒因(断言随之改为产品原文)。
 *
 * 跑法: `node tests/ts/f86-go-kickoff.test.ts`
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { planGo } from "../../agents/harness/pi/_shared/extensions/go.ts";  // AIPOS-F91: 夹具迁至 tests/ts/

let failures = 0;
function check(name: string, ok: boolean, detail = "") {
  console.log(`${ok ? "✓" : "✗"} ${name}${!ok && detail ? `\n    ${detail}` : ""}`);
  if (!ok) failures++;
}

const GO_TS = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "agents", "harness", "pi", "_shared", "extensions", "go.ts");
const src = readFileSync(GO_TS, "utf-8");
const GATE_TEXT_RE = /lybra next|next --run|lybra_\w+|queue claim|queue return|\bclaim\b(?! *由)/;

// ---------------- A. 静态断言 ----------------
check('A1 go.ts 不含 "card" 字面(原 <治理根>/card/<ID> 拼接)', !src.includes('"card"'));
check("A2 go.ts 不含 task_cards(原 <治理根>/task_cards/<ID>/RETURN.md 拼接)", !src.includes("task_cards"));
check("A3 go.ts 不含 path.join(workspaceRoot, …) 任何拼接", !/path\.join\(\s*workspaceRoot/.test(src));
check("A4 go.ts 不含 `lybra next`", !src.includes("lybra next"));
check("A5 go.ts 不含门动词名 lybra_*", !/lybra_\w+/.test(src.replace(/lybra_bin/g, "")));
check("A6 go.ts 经 my-tasks 取数", src.includes('"my-tasks"') && src.includes('"--json"'));

// ---------------- B. kickoff 只用产品字段 ----------------
const WT = "/srv/repos/product-b/.worktrees/AIPOS-X1";
const RP = "/srv/gov/task_cards/AIPOS-X1/RETURN.md";
const CP = "/srv/gov/5_tasks/queue/claimed/aipos-x1-some-slug.md";
const ready = {
  task_id: "AIPOS-X1",
  queue_state: "claimed",
  path: "5_tasks/queue/claimed/aipos-x1-some-slug.md",
  card_path: CP,
  worktree_path: WT,
  worktree_exists: true,
  worktree_refusal: null,
  report_path: RP,
  report_refusal: null,
};
const pending = { task_id: "AIPOS-X0", queue_state: "pending", path: "5_tasks/queue/pending/aipos-x0.md" };
const nextOf = (t: typeof ready) => ({ task_id: t.task_id, card_path: t.card_path, worktree_path: t.worktree_path, report_path: t.report_path, claimed_at: "2026-10-02T00:00:00Z" });
const planB = planGo({ scope: "my_tasks", tasks: [pending, ready], next_card: nextOf(ready), next_card_excluded: [] });
check("B1 有 claimed 卡且工作树就绪 → kickoff", planB.kind === "kickoff", JSON.stringify(planB));
if (planB.kind === "kickoff") {
  check("B2 kickoff 含产品给出的 worktree_path 原值", planB.kickoff.includes(`工作树路径: ${WT}`), planB.kickoff);
  check("B3 kickoff 含产品给出的 report_path 原值", planB.kickoff.includes(`报告落点: ${RP}`), planB.kickoff);
  check("B4 kickoff 含产品给出的 card_path 原值(不再自拼 workspace_root + path)", planB.kickoff.includes(`任务卡路径: ${CP}`), planB.kickoff);
  check("B5 kickoff 零门动词", !GATE_TEXT_RE.test(planB.kickoff), planB.kickoff);
  console.log("---- kickoff 原文 ----\n" + planB.kickoff + "\n----------------------");
}

// ---------------- C. 拒因与等待 ----------------
const NOT_CREATED_REASON = `工作树 ${WT} 尚未建立: 认领由驱动方完成并建树, 工位等待驱动方完成认领; 持续存在按 block-and-report 上报`;
const notCreated = {
  ...ready,
  worktree_exists: false,
  worktree_refusal: { code: "WORKTREE_NOT_CREATED", reason: NOT_CREATED_REASON },
};
const planC1 = planGo({ tasks: [notCreated], next_card: null,
  next_card_excluded: [{ task_id: "AIPOS-X1", code: "WORKTREE_NOT_CREATED", reason: NOT_CREATED_REASON }] });
check("C1 工作树尚未建立 → refused", planC1.kind === "refused", JSON.stringify(planC1));
if (planC1.kind === "refused") {
  check("C2 文案 = 产品拒因原文(工作树尚未建立 / 认领由驱动方完成 / block-and-report)", planC1.message.includes(NOT_CREATED_REASON)
    && planC1.message.includes("WORKTREE_NOT_CREATED"), planC1.message);
  check("C3 文案零门动词(无 lybra next / next --run / lybra_*)", !GATE_TEXT_RE.test(planC1.message), planC1.message);
  console.log("---- 工作树尚未建立 文案原文 ----\n" + planC1.message + "\n-------------------------------");
}
const unresolved = {
  ...ready,
  worktree_path: null,
  worktree_exists: false,
  worktree_refusal: { code: "LANE_REPO_UNDECLARED", reason: "卡 AIPOS-X1 lane.repo='c' 不在项目仓清单内" },
};
const planC4 = planGo({ tasks: [unresolved], next_card: null, next_card_excluded: [{ task_id: "AIPOS-X1", code: "LANE_REPO_UNDECLARED",
  reason: "工作树不可推导: 卡 AIPOS-X1 lane.repo='c' 不在项目仓清单内; 按 block-and-report 上报" }] });
check("C4 工作树不可推导 → refused 且转述产品拒因 code", planC4.kind === "refused" && planC4.message.includes("LANE_REPO_UNDECLARED")
  && planC4.message.includes("工作树不可推导")
  && !GATE_TEXT_RE.test(planC4.message), JSON.stringify(planC4));
const noReport = { ...ready, report_path: null, report_refusal: { code: "REPORT_LOCATION_UNDECLARED", reason: "x" } };
const planC5 = planGo({ tasks: [noReport], next_card: null, next_card_excluded: [{ task_id: "AIPOS-X1", code: "REPORT_LOCATION_UNDECLARED",
  reason: "报告落点不可推导: x; 按 block-and-report 上报" }] });
check("C5 报告落点不可推导 → refused 且带产品拒因", planC5.kind === "refused" && planC5.message.includes("REPORT_LOCATION_UNDECLARED"),
  JSON.stringify(planC5));
const legacy = { task_id: "AIPOS-X1", queue_state: "claimed", path: "5_tasks/queue/claimed/aipos-x1.md" };
const planC6 = planGo({ tasks: [legacy] });
check("C6 旧产品输出(无开工面字段)→ refused, 不在本地补推", planC6.kind === "refused", JSON.stringify(planC6));
check("C7 无 claimed 卡 → none(等待分配)", planGo({ tasks: [pending], next_card: null, next_card_excluded: [] }).kind === "none");
check("C8 tasks 缺 → none 且提示上报(不崩)", planGo({}).kind === "none" && planGo(null).kind === "none");

console.log(failures === 0 ? "\n✓ AIPOS-F86 /go 只读产品输出夹具全部通过" : `\n✗ ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);
