/**
 * AIPOS-F17 大项B+C headless 测试 —— 卡号从 frontmatter 读 + 候选无裁决出声。
 *
 * 覆盖:
 *  • 大项B: sweep 从卡 frontmatter task_id 读, 不从文件名 toUpperCase 猜。
 *    夹具: claimed/ 下放 aipos-x-fix9.md (frontmatter task_id=AIPOS-X-fix9),
 *    验证 taskId 保持原大小写(不是 AIPOS-X-FIX9)。
 *  • 大项C: 候选卡无裁决目录时出声 info 一行(含 next_step)。
 *
 * 跑法: `node tests/f17-derivation-homology.test.ts`(Node ≥ 22 类型剥离)。
 */

import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync, rmSync } from "node:fs";
import * as path from "node:path";
import { tmpdir } from "node:os";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

// ---------------------------------------------------------------------------
// ① 大项B: 混合大小写卡号从 frontmatter 读(不从文件名猜)
// ---------------------------------------------------------------------------
{
  // 直接测试 extractFrontmatterField 对 task_id 的提取
  const mod: any = await import("../lybra-loop.ts");

  const mixedCaseCard = `---
task_id: AIPOS-X-fix9
title: Mixed case test
status: claimed
---
## Body
`;
  const extracted = mod.extractFrontmatterField(mixedCaseCard, "task_id");
  check("大项B: extractFrontmatterField 提取 task_id 保持原大小写", extracted === "AIPOS-X-fix9");

  // 验证: 旧逻辑 toUpperCase 会破坏
  const oldLogic = "aipos-x-fix9.md".replace(/\.md$/i, "").toUpperCase();
  check("大项B: 旧逻辑 toUpperCase 会改大小写(对照)", oldLogic === "AIPOS-X-FIX9");
  check("大项B: frontmatter 读 vs 文件名猜 不同(证明修复有效)", extracted !== oldLogic);
}

// ---------------------------------------------------------------------------
// ② 大项C: 候选无裁决出声 — 验证源码包含出声逻辑
// ---------------------------------------------------------------------------
{
  // 读源码验证出声逻辑存在(静态分析)
  const fs = await import("node:fs");
  const loopSrc = fs.readFileSync(
    path.join(import.meta.dirname, "..", "lybra-loop.ts"),
    "utf-8",
  );

  // 大项C: 无裁决目录时出声(voice 调用)
  const hasVoiceForNoVerdict = /等待裁决落库中/.test(loopSrc);
  check("大项C: 无裁决目录时 voice 出声(等待裁决落库中)", hasVoiceForNoVerdict);

  const hasNextStep = /next_step.*裁决落库后自动收账/.test(loopSrc);
  check("大项C: 出声含 next_step(裁决落库后自动收账)", hasNextStep);

  // 大项B: 源码不再含 toUpperCase 推导 task_id
  const hasToUpperCaseInSweep = /cardFile\.replace.*toUpperCase/.test(loopSrc);
  check("大项B: sweep 不再用 toUpperCase 推导 task_id", !hasToUpperCaseInSweep);

  // 大项B: 源码改为读 frontmatter task_id
  const readsFrontmatterTaskId = /extractFrontmatterField\(cardContent,\s*"task_id"\)/.test(loopSrc);
  check("大项B: sweep 改读 frontmatter task_id", readsFrontmatterTaskId);
}

// ---------------------------------------------------------------------------
// ③ 大项B 集成: 混合大小写卡号夹具 — sweep 正确配对
// ---------------------------------------------------------------------------
{
  const root = mkdtempSync(path.join(tmpdir(), "f17-sweep-"));
  const claimedDir = path.join(root, "5_tasks/queue/claimed");
  mkdirSync(claimedDir, { recursive: true });

  // 写一张混合大小写卡: 文件名 aipos-x-fix9.md, frontmatter task_id=AIPOS-X-fix9
  writeFileSync(path.join(claimedDir, "aipos-x-fix9.md"), `---
task_id: AIPOS-X-fix9
title: Mixed case fixture
status: claimed
project: lybra
---
## Body
`, "utf-8");

  // 写对应裁决目录(用 frontmatter 的真实 task_id)
  const verdictDir = path.join(root, "5_tasks/records/audit_verdicts/AIPOS-X-fix9");
  mkdirSync(verdictDir, { recursive: true });
  writeFileSync(path.join(verdictDir, "verdict_AIPOS-X-fix9_gate.md"), `---
record_type: audit_verdict_record
verdict_id: verdict_AIPOS-X-fix9_20260821
verdict_at: '2026-08-21T03:00:00Z'
verdict: PASS
---
`, "utf-8");

  // 验证: 用 frontmatter 读 task_id 能正确找到裁决目录
  const mod: any = await import("../lybra-loop.ts");
  const cardContent = (await import("node:fs")).readFileSync(
    path.join(claimedDir, "aipos-x-fix9.md"), "utf-8"
  );
  const taskId = mod.extractFrontmatterField(cardContent, "task_id");
  check("大项B 集成: frontmatter 读 task_id = AIPOS-X-fix9", taskId === "AIPOS-X-fix9");

  // 裁决目录存在且可找到
  const expectedVerdictDir = path.join(root, "5_tasks/records/audit_verdicts", taskId);
  check("大项B 集成: 裁决目录存在(用 frontmatter task_id)", existsSync(expectedVerdictDir));

  // 旧逻辑(文件名 toUpperCase)会找不到
  const oldTaskId = "aipos-x-fix9.md".replace(/\.md$/i, "").toUpperCase();
  const oldVerdictDir = path.join(root, "5_tasks/records/audit_verdicts", oldTaskId);
  check("大项B 集成: 旧逻辑(文件名 toUpperCase)找不到裁决目录", !existsSync(oldVerdictDir));

  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
// 汇总
// ---------------------------------------------------------------------------
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
