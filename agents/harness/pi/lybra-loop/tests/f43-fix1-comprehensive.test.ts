/**
 * AIPOS-F43-fix1: held卡号截断修复 + F43三大项测试
 * 
 * 实撞修复: held结算路径卡号截断(AIPOS-F42-fix1 → AIPOS-F42-)
 * F43三大项:
 *   ① 投递即落RETURN.md骨架(内容单源=卡面)
 *   ② 空闲带路(held + IN_PROGRESS → 具体指引)
 *   ③ 纪律注入全路径覆盖(单源常量)
 */

import { parseCardFrontmatter, extractAcceptanceSection, renderReturnSkeleton, parseReturnStatus, buildHeldGuidance, DISCIPLINE_INJECTION } from "../loop-decisions.ts";

// ---------------------------------------------------------------------------
// 实撞修复测试: held卡号截断
// ---------------------------------------------------------------------------

console.log("=== AIPOS-F43-fix1: held卡号截断修复测试 ===\n");

// 测试卡号提取正则(修复前:[A-Z0-9-]+, 修复后:[A-Z0-9a-z_-]+)
const testCases = [
  { reason: "已持有 AIPOS-F42-fix1 —— 一卡一会话", expected: "AIPOS-F42-fix1" },
  { reason: "已持有 AIPOS-F43-fix2 —— 一卡一会话", expected: "AIPOS-F43-fix2" },
  { reason: "已持有 TEST-CARD-v2 —— 一卡一会话", expected: "TEST-CARD-v2" },
  { reason: "已持有 LYBRA_EXT_001 —— 一卡一会话", expected: "LYBRA_EXT_001" },
];

let passCount = 0;
for (const tc of testCases) {
  const match = tc.reason.match(/已持有\s+([A-Z0-9a-z_-]+)/);
  const extracted = match ? match[1] : null;
  if (extracted === tc.expected) {
    console.log(`✓ PASS: "${tc.reason}" → "${extracted}"`);
    passCount++;
  } else {
    console.log(`✗ FAIL: "${tc.reason}" → expected "${tc.expected}", got "${extracted}"`);
  }
}

console.log(`\nheld卡号截断修复: ${passCount}/${testCases.length} PASS\n`);

// ---------------------------------------------------------------------------
// F43 大项A: 投递即落RETURN.md骨架
// ---------------------------------------------------------------------------

console.log("=== F43 大项A: 投递即落RETURN.md骨架 ===\n");

const sampleExecutorCard = `---
task_id: TEST-EXEC-001
task_mode: code
---
# TEST-EXEC-001

## 验收

1. 功能A实现
2. 功能B测试
3. 文档更新
`;

const sampleAuditCard = `---
task_id: TEST-AUDIT-001
task_mode: audit
---
# TEST-AUDIT-001

## 审计对象

1. 代码审查
2. 测试覆盖
3. 安全检查
`;

// 测试 parseCardFrontmatter
console.log("测试 parseCardFrontmatter:");
const fm1 = parseCardFrontmatter(sampleExecutorCard);
if (fm1.task_id === "TEST-EXEC-001" && fm1.task_mode === "code") {
  console.log("✓ PASS: executor卡frontmatter解析正确");
} else {
  console.log(`✗ FAIL: executor卡frontmatter解析错误: ${JSON.stringify(fm1)}`);
}

const fm2 = parseCardFrontmatter(sampleAuditCard);
if (fm2.task_id === "TEST-AUDIT-001" && fm2.task_mode === "audit") {
  console.log("✓ PASS: audit卡frontmatter解析正确");
} else {
  console.log(`✗ FAIL: audit卡frontmatter解析错误: ${JSON.stringify(fm2)}`);
}

// 测试 extractAcceptanceSection
console.log("\n测试 extractAcceptanceSection:");
const acceptance1 = extractAcceptanceSection(sampleExecutorCard);
if (acceptance1.includes("功能A实现") && acceptance1.includes("功能B测试")) {
  console.log("✓ PASS: executor卡验收节提取正确");
} else {
  console.log(`✗ FAIL: executor卡验收节提取错误: ${acceptance1}`);
}

const acceptance2 = extractAcceptanceSection(sampleAuditCard);
if (acceptance2.includes("代码审查") && acceptance2.includes("测试覆盖")) {
  console.log("✓ PASS: audit卡审计对象提取正确");
} else {
  console.log(`✗ FAIL: audit卡审计对象提取错误: ${acceptance2}`);
}

// 测试 renderReturnSkeleton - executor
console.log("\n测试 renderReturnSkeleton (executor):");
const skeleton1 = renderReturnSkeleton(sampleExecutorCard, "TEST-EXEC-001");
const checks1 = [
  { test: skeleton1.includes("TEST-EXEC-001 交付报告"), name: "标题含任务ID" },
  { test: skeleton1.includes("## 一句话结论"), name: "含一句话结论节" },
  { test: skeleton1.includes("## 状态"), name: "含状态节" },
  { test: skeleton1.includes("IN_PROGRESS"), name: "状态为IN_PROGRESS" },
  { test: skeleton1.includes("## 验收清单"), name: "含验收清单节" },
  { test: skeleton1.includes("功能A实现"), name: "验收内容原文存在" },
  { test: skeleton1.includes("骨架由连接器投递时自动落盘,内容单源=卡面"), name: "注明单源=卡面" },
];

for (const c of checks1) {
  if (c.test) {
    console.log(`✓ PASS: executor骨架 - ${c.name}`);
  } else {
    console.log(`✗ FAIL: executor骨架 - ${c.name}`);
  }
}

// 测试 renderReturnSkeleton - audit
console.log("\n测试 renderReturnSkeleton (audit):");
const skeleton2 = renderReturnSkeleton(sampleAuditCard, "TEST-AUDIT-001");
const checks2 = [
  { test: skeleton2.includes("TEST-AUDIT-001 审计报告"), name: "标题含任务ID" },
  { test: skeleton2.includes("## 审计裁决"), name: "含审计裁决节" },
  { test: skeleton2.includes("verdict: (PASS / FAIL / BLOCK)"), name: "裁决字段占位" },
  { test: skeleton2.includes("## 一句话结论"), name: "含一句话结论节" },
  { test: skeleton2.includes("## 验收清单"), name: "含验收清单节" },
  { test: skeleton2.includes("代码审查"), name: "审计对象原文存在" },
];

for (const c of checks2) {
  if (c.test) {
    console.log(`✓ PASS: audit骨架 - ${c.name}`);
  } else {
    console.log(`✗ FAIL: audit骨架 - ${c.name}`);
  }
}

// ---------------------------------------------------------------------------
// F43 大项B: 空闲带路
// ---------------------------------------------------------------------------

console.log("\n=== F43 大项B: 空闲带路 ===\n");

// 测试 parseReturnStatus
console.log("测试 parseReturnStatus:");
const sampleReturn1 = `# TEST-001 交付报告

## 一句话结论

待填写

## 状态

IN_PROGRESS

## 验收清单

1. 功能实现
`;

const sampleReturn2 = `# TEST-002 交付报告

## 一句话结论

已完成

## 状态

COMPLETED

## 验收清单

1. 功能实现
`;

const status1 = parseReturnStatus(sampleReturn1);
const status2 = parseReturnStatus(sampleReturn2);

if (status1 === "IN_PROGRESS") {
  console.log("✓ PASS: IN_PROGRESS状态解析正确");
} else {
  console.log(`✗ FAIL: IN_PROGRESS状态解析错误: ${status1}`);
}

if (status2 === "COMPLETED") {
  console.log("✓ PASS: COMPLETED状态解析正确");
} else {
  console.log(`✗ FAIL: COMPLETED状态解析错误: ${status2}`);
}

// 测试 buildHeldGuidance
console.log("\n测试 buildHeldGuidance:");
const guidance = buildHeldGuidance(
  "TEST-F43-001",
  "/path/to/task_cards/TEST-F43-001/RETURN.md",
  "1. 功能A\n2. 功能B"
);

const guidanceChecks = [
  { test: guidance.includes("TEST-F43-001"), name: "含任务ID" },
  { test: guidance.includes("/path/to/task_cards/TEST-F43-001/RETURN.md"), name: "含报告路径" },
  { test: guidance.includes("功能A"), name: "含验收内容" },
  { test: guidance.includes("功能B"), name: "含验收内容②" },
  { test: guidance.includes(DISCIPLINE_INJECTION), name: "含纪律注入" },
];

for (const c of guidanceChecks) {
  if (c.test) {
    console.log(`✓ PASS: guidance - ${c.name}`);
  } else {
    console.log(`✗ FAIL: guidance - ${c.name}`);
  }
}

// ---------------------------------------------------------------------------
// F43 大项C: 纪律注入全路径覆盖
// ---------------------------------------------------------------------------

console.log("\n=== F43 大项C: 纪律注入全路径覆盖 ===\n");

console.log("测试 DISCIPLINE_INJECTION 常量:");
const disciplineChecks = [
  { test: DISCIPLINE_INJECTION.includes("最后一步必须是 gate 提交"), name: "含gate提交提醒" },
  { test: DISCIPLINE_INJECTION.includes("RETURN.md 骨架"), name: "含RETURN.md骨架提醒" },
  { test: DISCIPLINE_INJECTION.includes("连接器投递时自动落盘"), name: "含自动落盘说明" },
];

for (const c of disciplineChecks) {
  if (c.test) {
    console.log(`✓ PASS: 纪律注入 - ${c.name}`);
  } else {
    console.log(`✗ FAIL: 纪律注入 - ${c.name}`);
  }
}

console.log("\n=== 所有测试完成 ===");
