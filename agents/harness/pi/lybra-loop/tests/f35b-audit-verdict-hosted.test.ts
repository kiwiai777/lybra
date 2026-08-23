/**
 * AIPOS-F35 大项B: 审计裁决提交托管 — 复用 F33 骨架,同一门动词
 * 
 * 验收:
 * ①先红: 夹具在修复前运行→复现"审计报告就位但不提交裁决"(TODO 标记)
 * ②修复后同夹具转绿
 * ③托管层调用 lybra_audit_verdict_dry_run + lybra_audit_verdict_confirm(F29大项E补做)
 * ④复用 F33 return 托管骨架: 同一执行函数结构,禁第二实现
 * ⑤报告缺裁决三值 → 出声带路,不瞎提交
 * 
 * 锚点: F33 三层同源执行函数骨架 + F29 大项E 未兑现补做
 */

import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, "package.json")) && existsSync(join(dir, "agents"))) {
      return dir;
    }
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}
const PROJECT_ROOT = findProjectRoot();

describe("F35-B①: 审计裁决动词存在性", () => {
  it("schema 应包含 lybra_audit_verdict_dry_run 动词", () => {
    const schemaPath = join(PROJECT_ROOT, "schema/verbs.schema.json");
    assert.ok(existsSync(schemaPath), "verbs.schema.json 应存在");
    
    const schema = JSON.parse(readFileSync(schemaPath, "utf-8"));
    assert.ok(schema.verbs, "schema 应有 verbs 字段");
    
    const dryRunVerb = schema.verbs.lybra_audit_verdict_dry_run;
    assert.ok(dryRunVerb, "schema 应包含 lybra_audit_verdict_dry_run 动词");
    
    // 验证必填参数
    const required = dryRunVerb.parameters?.required || [];
    assert.ok(
      required.includes("reviewed_task_id"),
      "lybra_audit_verdict_dry_run 应要求 reviewed_task_id"
    );
    assert.ok(
      required.includes("actor"),
      "lybra_audit_verdict_dry_run 应要求 actor"
    );
    assert.ok(
      required.includes("verdict"),
      "lybra_audit_verdict_dry_run 应要求 verdict"
    );
    
    console.log("✓ schema 包含 lybra_audit_verdict_dry_run 动词");
  });

  it("schema 应包含 lybra_audit_verdict_confirm 动词", () => {
    const schemaPath = join(PROJECT_ROOT, "schema/verbs.schema.json");
    const schema = JSON.parse(readFileSync(schemaPath, "utf-8"));
    
    const confirmVerb = schema.verbs.lybra_audit_verdict_confirm;
    assert.ok(confirmVerb, "schema 应包含 lybra_audit_verdict_confirm 动词");
    
    // 验证必填参数
    const required = confirmVerb.parameters?.required || [];
    assert.ok(
      required.includes("dry_run_token"),
      "lybra_audit_verdict_confirm 应要求 dry_run_token"
    );
    assert.ok(
      required.includes("owner_confirmation_token"),
      "lybra_audit_verdict_confirm 应要求 owner_confirmation_token(AIPOS-328 自确认)"
    );
    
    console.log("✓ schema 包含 lybra_audit_verdict_confirm 动词");
  });

  it("REQUIRED_VERBS 应包含审计裁决动词", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找 REQUIRED_VERBS 定义
    const requiredVerbsMatch = source.match(/const REQUIRED_VERBS[^{]*\{([^}]+)\}/s);
    assert.ok(requiredVerbsMatch, "REQUIRED_VERBS 应存在");
    
    const requiredVerbsBlock = requiredVerbsMatch[0];
    assert.ok(
      requiredVerbsBlock.includes("lybra_audit_verdict_dry_run"),
      "REQUIRED_VERBS 应包含 lybra_audit_verdict_dry_run"
    );
    assert.ok(
      requiredVerbsBlock.includes("lybra_audit_verdict_confirm"),
      "REQUIRED_VERBS 应包含 lybra_audit_verdict_confirm"
    );
    
    console.log("✓ REQUIRED_VERBS 包含审计裁决动词");
  });
});

describe("F35-B②: 审计裁决托管实现", () => {
  it("托管层应调用 lybra_audit_verdict_dry_run + confirm", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找审计报告托管块(tryAutoReturn 内审计车道)
    const auditReportIdx = source.indexOf("auto-audit-from-report");
    assert.ok(auditReportIdx > 0, "auto-audit-from-report 块应存在");
    
    // 取该块后续 5000 字符(扩大搜索窗口)
    const auditBlock = source.substring(auditReportIdx, auditReportIdx + 5000);
    
    // 断言: 应调用 lybra_audit_verdict_dry_run
    assert.ok(
      auditBlock.includes("lybra_audit_verdict_dry_run"),
      "托管层应调用 lybra_audit_verdict_dry_run"
    );
    
    // 断言: 应调用 lybra_audit_verdict_confirm
    assert.ok(
      auditBlock.includes("lybra_audit_verdict_confirm"),
      "托管层应调用 lybra_audit_verdict_confirm"
    );
    
    // 断言: 应使用 OWNER_CONFIRMED 自确认(AIPOS-328)
    assert.ok(
      auditBlock.includes("OWNER_CONFIRMED"),
      "托管层应使用 OWNER_CONFIRMED 自确认(AIPOS-328)"
    );
    
    console.log("✓ 托管层调用审计裁决动词");
  });

  it("审计裁决托管应复用 F33 return 托管骨架", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    const auditReportIdx = source.indexOf("auto-audit-from-report");
    const auditBlock = source.substring(auditReportIdx, auditReportIdx + 5000);
    
    // 断言: 应有 dry_run + confirm 两阶段(对齐 return 托管)
    const dryRunMatches = auditBlock.match(/callTool\("lybra_audit_verdict_dry_run"/g);
    const confirmMatches = auditBlock.match(/callTool\("lybra_audit_verdict_confirm"/g);
    
    assert.ok(dryRunMatches && dryRunMatches.length >= 1, "应调用 dry_run");
    assert.ok(confirmMatches && confirmMatches.length >= 1, "应调用 confirm");
    
    // 断言: 应检查 dry_run_token
    assert.ok(
      auditBlock.includes("dry_run_token") || auditBlock.includes("dryRunToken"),
      "应检查 dry_run_token(F33 骨架)"
    );
    
    // 断言: 应检查被拒场景(BLOCK/isError)
    assert.ok(
      auditBlock.includes("BLOCK") || auditBlock.includes("blocking_reasons"),
      "应检查被拒场景(F33 骨架)"
    );
    
    // 断言: 应使用 stringifyReasons 处理拒因(对齐 return 托管)
    assert.ok(
      auditBlock.includes("stringifyReasons"),
      "应使用 stringifyReasons 处理拒因(F33 骨架)"
    );
    
    console.log("✓ 审计裁决托管复用 F33 return 托管骨架");
  });

  it("报告缺裁决三值应出声带路,不瞎提交", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找缺裁决检查块
    const missingVerdictIdx = source.indexOf("auto-audit-missing-verdict");
    assert.ok(missingVerdictIdx > 0, "auto-audit-missing-verdict 块应存在");
    
    const missingBlock = source.substring(missingVerdictIdx - 500, missingVerdictIdx + 500);
    
    // 断言: 缺 verdict 时应 return false(不提交)
    assert.ok(
      missingBlock.includes("return false"),
      "缺裁决三值时应 return false(不瞎提交)"
    );
    
    // 断言: 应出声(voice)带路
    assert.ok(
      missingBlock.includes("voice"),
      "缺裁决三值时应出声带路"
    );
    
    console.log("✓ 报告缺裁决三值出声带路,不瞎提交");
  });
});

describe("F35-B③: 防碎片化验证", () => {
  it("审计裁决不应有第二实现路径", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 统计 callTool("lybra_audit_verdict_dry_run" 调用次数
    const callMatches = source.match(/callTool\("lybra_audit_verdict_dry_run"/g);
    
    // 应该只在托管层出现一次(tryAutoReturn 内审计车道)
    assert.ok(
      callMatches && callMatches.length === 1,
      `lybra_audit_verdict_dry_run 应只在托管层调用1次, 实际=${callMatches?.length || 0}`
    );
    
    console.log(`✓ 防碎片化: lybra_audit_verdict_dry_run 调用次数=${callMatches!.length}(仅托管层)`);
  });

  it("不应有 TODO 或临时实现标记", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找审计报告托管块
    const auditReportIdx = source.indexOf("auto-audit-from-report");
    const auditBlock = source.substring(auditReportIdx, auditReportIdx + 3000);
    
    // 断言: 不应有 TODO 标记(已实现)
    const hasTodo = auditBlock.includes("TODO") && auditBlock.includes("实现审计动词调用");
    assert.ok(
      !hasTodo,
      "审计裁决托管块不应有 TODO 标记(应已实现)"
    );
    
    // 断言: 不应有"暂时保持原有行为"等临时语句
    const hasTemporary = auditBlock.includes("暂时保持") || auditBlock.includes("临时");
    assert.ok(
      !hasTemporary,
      "审计裁决托管块不应有临时实现标记"
    );
    
    console.log("✓ 无 TODO/临时标记,审计裁决托管已完整实现");
  });
});

/**
 * 运行测试:
 * ```bash
 * node --test agents/harness/pi/lybra-loop/tests/f35b-audit-verdict-hosted.test.ts
 * ```
 */
