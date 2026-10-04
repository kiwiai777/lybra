/**
 * AIPOS-F93 件①(TS 侧): /go 开工提示的报告必填字段只读 my-tasks next_card.report_required_frontmatter 原样列出。headless 夹具。
 *
 *  A. 静态断言: go.ts 不含声明里的任何必填键名(transitions.schema artifact_ingest.<return|verdict>.required_frontmatter)——
 *     不自写清单; AIPOS-F95 件①: 读 next_card.kickoff(产品渲染的开工提示全文), 不自拼字段行。
 *  B. 给定 next_card(kickoff 含产品给出的被审 tip/tree 实值)→ 原样发送(逐字节)。
 *  C. kickoff 缺 / 空 / 非串 → refused(fail-closed); 字段清单形变的拒因在产品 render_kickoff(Python 夹具 F95)。
 *  D. 给定 my-tasks JSON 文件(argv[2], Python 夹具由真实 my-tasks 产出)→ 打印 planGo 结果 JSON(供 Python 夹具断言)。
 *
 * 跑法: `node tests/ts/f93-go-report-fields.test.ts [my-tasks.json]`
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { planGo } from "../../agents/harness/pi/_shared/extensions/go.ts";

const given = process.argv[2];
if (given) {
  // D. 给定 my-tasks JSON → planGo 结果(原样输出, 断言在 Python 夹具)
  console.log(JSON.stringify(planGo(JSON.parse(readFileSync(given, "utf-8")))));
  process.exit(0);
}

let failures = 0;
function check(name: string, ok: boolean, detail = "") {
  console.log(`${ok ? "✓" : "✗"} ${name}${!ok && detail ? `\n    ${detail}` : ""}`);
  if (!ok) failures++;
}

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const src = readFileSync(join(ROOT, "agents", "harness", "pi", "_shared", "extensions", "go.ts"), "utf-8");
const ingest = JSON.parse(readFileSync(join(ROOT, "schema", "transitions.schema.json"), "utf-8")).artifact_ingest;
const declared: string[] = [...ingest.return.required_frontmatter, ...ingest.verdict.required_frontmatter];

// ---------------- A. 静态断言 ----------------
check("A1 声明非空(return + verdict 必填键)", declared.length >= 2, JSON.stringify(declared));
for (const key of declared) {
  check(`A2 go.ts 不含必填键名 ${key}(不自写清单)`, !src.includes(key));
}
// AIPOS-F95 件①: 报告必填字段随开工提示全文由产品渲染(next_resolver.render_kickoff), go.ts 只读 next_card.kickoff, 不再自拼字段行
check("A3 go.ts 读产品字段 next_card.kickoff, 不自拼报告必填字段行", src.includes("card.kickoff") && !src.includes("报告 frontmatter 必填")
  && !src.includes("reportFieldLines"));

// ---------------- B. 原样发送(含被审 tip/tree 实值) ----------------
const SHA = "0123456789abcdef0123456789abcdef01234567";
const TREE = "89abcdef0123456789abcdef0123456789abcdef";
const contract = [
  { key: "verdict", hint: "裁决值, PASS / PASS_WITH_NOTES / FAIL / BLOCK 之一", value: null },
  { key: "commit_sha", hint: `被审分支 card/AIPOS-Y tip 的完整 40 位 sha; 产品给出: card/AIPOS-Y tip = ${SHA}, tree = ${TREE}(取证工作树 HEAD), 照抄`, value: SHA },
];
// 产品输出形样本(next_resolver.render_kickoff 按 verbs.schema my-tasks 条目 kickoff 声明渲染)
const KICKOFF = "已认领任务卡 AIPOS-YR。\n\n工作树路径: /r/.worktrees/AIPOS-YR\n报告落点: /g/task_cards/AIPOS-YR/RETURN.md\n"
  + "任务卡路径: /g/5_tasks/queue/claimed/aipos-yr.md\n报告 frontmatter 必填(产品给出; 缺任一项或仍为占位, 报告被拒收):\n"
  + `- verdict: (${contract[0].hint})\n- commit_sha: ${SHA}(${contract[1].hint})\n\n按你的 AGENTS.md 执行，完成后写报告到报告落点。`;
const card = {
  task_id: "AIPOS-YR", card_path: "/g/5_tasks/queue/claimed/aipos-yr.md", worktree_path: "/r/.worktrees/AIPOS-YR",
  report_path: "/g/task_cards/AIPOS-YR/RETURN.md", report_required_frontmatter: contract, claimed_at: "2026-10-03T00:00:00Z",
  kickoff: KICKOFF,
};
const planB = planGo({ tasks: [], next_card: card, next_card_excluded: [] });
check("B1 kickoff", planB.kind === "kickoff", JSON.stringify(planB));
if (planB.kind === "kickoff") {
  check("B2 kickoff = 产品 next_card.kickoff 原样(逐字节, 含报告必填字段行)", planB.kickoff === KICKOFF, planB.kickoff);
  check("B3 含被审 tip 与 tree 实值(产品给出, 原样带过)", planB.kickoff.includes(`- commit_sha: ${SHA}(`) && planB.kickoff.includes(TREE), planB.kickoff);
  console.log("---- kickoff 原文 ----\n" + planB.kickoff + "\n----------------------");
}

// ---------------- C. fail-closed ----------------
const { kickoff: _drop, ...noKickoff } = card;
check("C1 next_card 缺 kickoff → refused", planGo({ tasks: [], next_card: noKickoff, next_card_excluded: [] }).kind === "refused");
check("C2 kickoff 空串/空白 → refused", planGo({ tasks: [], next_card: { ...card, kickoff: "  " }, next_card_excluded: [] }).kind === "refused");
check("C3 kickoff 非串 → refused", planGo({ tasks: [], next_card: { ...card, kickoff: ["x"] }, next_card_excluded: [] }).kind === "refused");
const c1 = planGo({ tasks: [], next_card: noKickoff, next_card_excluded: [] });
if (c1.kind === "refused") {
  check("C5 拒因文案零门动词", !/lybra next|next --run|lybra_\w+|queue claim/.test(c1.message), c1.message);
}

console.log(failures === 0 ? "\n✓ AIPOS-F93 /go 报告必填字段夹具全部通过" : `\n✗ ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);
