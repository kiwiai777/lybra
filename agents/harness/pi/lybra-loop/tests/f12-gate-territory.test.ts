/**
 * AIPOS-F12 大项A/B/C headless 测试 —— 门领地机器免疫的纯逻辑部分。
 *
 * 覆盖:
 *  • isGateBornVerdict 与门内 F2 同规则(record_type 前缀 audit_verdict + verdict_id 前缀 verdict_ + verdict_at)。
 *  • quarantineHandWrittenVerdicts: 门生零误伤, 手写自动隔离(.superseded + README 追加), 拿不准只出声不动。
 *  • isProtectedWriteTarget / extractWriteTargets: pi 写拦截目标判定。
 *
 * 跑法: `node tests/f12-gate-territory.test.ts`(Node ≥ 22 类型剥离)。
 */

import { mkdtempSync, readFileSync, existsSync, readdirSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import * as fs from "node:fs";
import * as path from "node:path";
import { tmpdir } from "node:os";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

const mod: any = await import("../lybra-loop.ts");

// ---------------------------------------------------------------------------
// ① isGateBornVerdict 与门内 F2 同规则
// ---------------------------------------------------------------------------
const gateBornContent = `---
record_type: audit_verdict_record
verdict_id: verdict_AIPOS-X_20260820_audit
verdict_at: '2026-08-20T07:00:00Z'
verdict: PASS
---
`;
const migrationContent = `---
record_type: audit_verdict
verdict_id: verdict_AIPOS-X_20260820_audit
verdict_at: '2026-08-20T07:00:00Z'
---
`;
const handWrittenContent = `---
record_type: audit_verdict
verdict: PASS_WITH_NOTES
verdict_at: '2026-08-20T14:10:00Z'
---
`;
check("isGateBornVerdict: audit_verdict_record → authentic", mod.isGateBornVerdict(gateBornContent).authentic === true);
check("isGateBornVerdict: audit_verdict(migration) → authentic(与 F2 前缀一致)", mod.isGateBornVerdict(migrationContent).authentic === true);
check("isGateBornVerdict: 缺 verdict_id 手写 → not authentic", mod.isGateBornVerdict(handWrittenContent).authentic === false);

// ---------------------------------------------------------------------------
// ② quarantineHandWrittenVerdicts: 隔离手写, 零误伤门生, 拿不准只出声
// ---------------------------------------------------------------------------
{
  const root = mkdtempSync(path.join(tmpdir(), "f12-q-"));
  const verdictsDir = path.join(root, "5_tasks/records/audit_verdicts/AIPOS-F12TEST");
  mkdirSync(verdictsDir, { recursive: true });
  writeFileSync(path.join(verdictsDir, "verdict_AIPOS-F12TEST_gate.md"), gateBornContent, "utf-8");
  writeFileSync(path.join(verdictsDir, "handwritten_pass.md"), handWrittenContent, "utf-8");
  // 拿不准: 有 verdict_ 机器标记但未知 record_type → 只出声不动
  writeFileSync(path.join(verdictsDir, "unknown_type.md"), `---
record_type: some_future_type
verdict_id: verdict_AIPOS-F12TEST_20260820_x
verdict_at: '2026-08-20T09:00:00Z'
---
`, "utf-8");

  // AIPOS-F14 大项C: sweep 只扫活动卡(queue/claimed)目录, 测试需建一张活动卡
  const claimedDir = path.join(root, "5_tasks/queue/claimed");
  mkdirSync(claimedDir, { recursive: true });
  writeFileSync(path.join(claimedDir, "aipos-f12test.md"), `---
task_id: AIPOS-F12TEST
status: claimed
---
`, "utf-8");

  const result = mod.quarantineHandWrittenVerdicts(fs, path, { workspaceRoot: root }, null);

  const qdir = path.join(root, "governance/quarantine");
  const qfiles = existsSync(qdir) ? readdirSync(qdir) : [];
  const gateStillThere = existsSync(path.join(verdictsDir, "verdict_AIPOS-F12TEST_gate.md"));
  const handStillThere = existsSync(path.join(verdictsDir, "handwritten_pass.md"));
  const unknownStillThere = existsSync(path.join(verdictsDir, "unknown_type.md"));

  check("quarantine: 手写件被移走", handStillThere === false);
  check("quarantine: 门生件原位不动(零误伤)", gateStillThere === true);
  check("quarantine: 拿不准(未知 record_type 有机器标记)不动", unknownStillThere === true);
  check("quarantine: 隔离区出现 .superseded 文件", qfiles.some((f) => f === "handwritten_pass.md.superseded"));
  check("quarantine: 隔离计数 = 1", result.quarantined === 1);
  check("quarantine: 拿不准计数 = 1", result.emittedUncertain === 1);
  const readme = existsSync(path.join(qdir, "README.md")) ? readFileSync(path.join(qdir, "README.md"), "utf-8") : "";
  check("quarantine: README 追加隔离行", readme.includes("handwritten_pass.md.superseded"));

  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
// ③ pi 写拦截目标判定
// ---------------------------------------------------------------------------
{
  const ws = "/ws";
  const prot = ["5_tasks/records/", "5_tasks/queue/"];
  check("isProtectedWriteTarget: records 命中(相对)", mod.isProtectedWriteTarget("5_tasks/records/audit_verdicts/AIPOS-X/foo.md", ws, prot) === true);
  check("isProtectedWriteTarget: queue 命中(绝对)", mod.isProtectedWriteTarget("/ws/5_tasks/queue/claimed/x.md", ws, prot) === true);
  check("isProtectedWriteTarget: task_cards 不命中", mod.isProtectedWriteTarget("task_cards/AIPOS-X/RETURN.md", ws, prot) === false);

  check("extractWriteTargets: write 取 path", JSON.stringify(mod.extractWriteTargets("write", { path: "5_tasks/records/x.md" })) === JSON.stringify(["5_tasks/records/x.md"]));
  check("extractWriteTargets: edit 取 path", JSON.stringify(mod.extractWriteTargets("edit", { path: "/a/b.md" })) === JSON.stringify(["/a/b.md"]));
  check("extractWriteTargets: bash 抓重定向目标", mod.extractWriteTargets("bash", { command: "cat a > 5_tasks/records/x.md" }).includes("5_tasks/records/x.md"));
  check("extractWriteTargets: bash 读命令不命中", mod.extractWriteTargets("bash", { command: "cat 5_tasks/records/x.md" }).length === 0);
}

// ---------------------------------------------------------------------------
// 汇总
// ---------------------------------------------------------------------------
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
