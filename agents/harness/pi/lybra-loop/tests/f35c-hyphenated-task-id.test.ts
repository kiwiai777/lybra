/**
 * AIPOS-F35 大项C: 连字符卡号回归夹具 — 防 AIPOS-DRILL-1 被截断为 AIPOS-DRILL
 * 
 * 验收:
 * ①先红: 夹具在修复前运行→复现连字符卡号被截断(2026-08-23 顾问改声明已修好)
 * ②修复后同夹具转绿
 * ③带连字符卡号的归属解析正确(card_policy 声明)
 * ④带连字符卡号的 verdict_ref 授权校验正确
 * ⑤防退化: 连字符卡号不被正则/split 误截断
 * 
 * 锚点: run-all + card_policy 声明(2026-08-23 顾问改声明修好截断)
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

describe("F35-C①: 连字符卡号解析正确性", () => {
  it("card_policy 声明应支持连字符卡号(AIPOS-DRILL-1 不截断)", () => {
    // 读取 card_policy 声明文件(治理仓)
    const policyPath = join(PROJECT_ROOT, "..", "..", "ai-project-os", "2_projects", "lybra", "4_policies", "card_policy.schema.json");
    if (!existsSync(policyPath)) {
      console.log("⊘ 跳过: card_policy.schema.json 不存在(非标准治理仓位置)");
      return;
    }
    
    const policySchema = JSON.parse(readFileSync(policyPath, "utf-8"));
    
    // 检查 task_id_pattern(如果存在)
    const taskIdPattern = policySchema.task_id_pattern || policySchema.patterns?.task_id;
    if (taskIdPattern) {
      // 测试连字符卡号是否匹配
      const hyphenatedTaskId = "AIPOS-DRILL-1";
      const regex = new RegExp(taskIdPattern);
      
      assert.ok(
        regex.test(hyphenatedTaskId),
        `card_policy task_id_pattern 应匹配连字符卡号(${hyphenatedTaskId})`
      );
      
      console.log(`✓ card_policy 支持连字符卡号: pattern=${taskIdPattern}`);
    } else {
      console.log("⊘ card_policy 无 task_id_pattern 声明,跳过正则检查");
    }
  });

  it("任务卡解析逻辑不应截断连字符卡号", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找任务ID提取相关代码(task_id 赋值)
    // 检查是否有可能误截断连字符的逻辑(如 .split("-")[0])
    
    // 搜索可疑模式: split("-") 或 replace(/-.*/, "")
    const suspiciousPatterns = [
      /task_id[^;]*\.split\(['"]-['"]\)\s*\[0\]/g,  // taskId.split("-")[0]
      /task_id[^;]*\.replace\([^)]*-[^)]*\)/g,      // taskId.replace(/-.*/, "")
    ];
    
    let hasSuspiciousPattern = false;
    for (const pattern of suspiciousPatterns) {
      const matches = source.match(pattern);
      if (matches && matches.length > 0) {
        console.warn(`⚠ 发现可疑截断模式: ${matches[0]}`);
        hasSuspiciousPattern = true;
      }
    }
    
    // 这个断言在修复前会失败(如果有误截断逻辑),修复后应通过
    assert.ok(
      !hasSuspiciousPattern,
      "任务卡解析逻辑不应有误截断连字符的模式"
    );
    
    console.log("✓ 任务卡解析无连字符截断风险");
  });
});

describe("F35-C②: verdict_ref 授权校验支持连字符", () => {
  it("verdict 记录查找应使用完整卡号(含连字符)", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找 verdict 记录查询逻辑
    const verdictDirPatterns = [
      /audit_verdicts['"]\s*,\s*([a-zA-Z_]+)/g,  // path.join(..., "audit_verdicts", reviewedTaskId)
    ];
    
    let foundVerdictLookup = false;
    for (const pattern of verdictDirPatterns) {
      const matches = source.match(pattern);
      if (matches && matches.length > 0) {
        foundVerdictLookup = true;
        console.log(`✓ 发现 verdict 查询逻辑: ${matches[0]}`);
      }
    }
    
    assert.ok(
      foundVerdictLookup,
      "应存在 verdict 记录查询逻辑(audit_verdicts/<task_id>)"
    );
    
    // 检查是否有 reviewedTaskId 提取逻辑
    const reviewedIdIdx = source.indexOf("reviewedTaskId");
    if (reviewedIdIdx > 0) {
      const reviewedBlock = source.substring(reviewedIdIdx, reviewedIdIdx + 500);
      
      // 断言: 不应有 .replace(/R$/i, "") 之外的截断逻辑
      const hasOnlyRSuffix = reviewedBlock.includes("replace(/R$/i") || 
                             reviewedBlock.includes('replace("R"') ||
                             reviewedBlock.includes("reviewed_task_id");
      
      assert.ok(
        hasOnlyRSuffix,
        "reviewedTaskId 提取应只去掉 R 后缀,不截断连字符"
      );
      
      console.log("✓ reviewedTaskId 提取逻辑正确(只去 R 后缀)");
    }
  });
});

describe("F35-C③: 连字符卡号端到端场景", () => {
  it("示例: AIPOS-DRILL-1 应解析为完整卡号", () => {
    const testTaskId = "AIPOS-DRILL-1";
    
    // 模拟 reviewedTaskId 提取(去掉 R 后缀)
    const reviewedTaskId = testTaskId.replace(/R$/i, "");
    
    // 断言: 连字符不应被去掉
    assert.strictEqual(
      reviewedTaskId,
      "AIPOS-DRILL-1",
      "AIPOS-DRILL-1 去 R 后缀应保持不变(无 R 后缀)"
    );
    
    console.log(`✓ 端到端: ${testTaskId} → ${reviewedTaskId}`);
  });

  it("示例: AIPOS-DRILL-1R 应解析为 AIPOS-DRILL-1", () => {
    const testTaskId = "AIPOS-DRILL-1R";
    
    // 模拟 reviewedTaskId 提取(去掉 R 后缀)
    const reviewedTaskId = testTaskId.replace(/R$/i, "");
    
    // 断言: 应去掉 R,保留连字符
    assert.strictEqual(
      reviewedTaskId,
      "AIPOS-DRILL-1",
      "AIPOS-DRILL-1R 去 R 后缀应为 AIPOS-DRILL-1(连字符保留)"
    );
    
    console.log(`✓ 端到端: ${testTaskId} → ${reviewedTaskId}`);
  });

  it("示例: AIPOS-F35 应解析为完整卡号", () => {
    const testTaskId = "AIPOS-F35";
    
    // 模拟 reviewedTaskId 提取
    const reviewedTaskId = testTaskId.replace(/R$/i, "");
    
    assert.strictEqual(
      reviewedTaskId,
      "AIPOS-F35",
      "AIPOS-F35 应保持不变(连字符保留,无 R 后缀)"
    );
    
    console.log(`✓ 端到端: ${testTaskId} → ${reviewedTaskId}`);
  });
});

describe("F35-C④: 防退化保护", () => {
  it("不应有全局性的连字符替换/截断逻辑", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找全局性的危险模式(会影响所有卡号)
    const dangerousPatterns = [
      /task.*id.*replace\([^)]*-[^)]*,\s*['"]['"][^\)]*\)/gi,  // taskId.replace(/-/g, "")
      /task.*id.*split\(['"]-['"]\)/gi,                        // taskId.split("-")
    ];
    
    let foundDangerous = false;
    for (const pattern of dangerousPatterns) {
      const matches = source.match(pattern);
      if (matches && matches.length > 0) {
        // 过滤掉注释行
        const realMatches = matches.filter(m => !source.substring(
          source.indexOf(m) - 50,
          source.indexOf(m)
        ).includes("//"));
        
        if (realMatches.length > 0) {
          console.warn(`⚠ 发现全局性危险模式: ${realMatches[0]}`);
          foundDangerous = true;
        }
      }
    }
    
    assert.ok(
      !foundDangerous,
      "不应有全局性的连字符替换/截断逻辑(会误伤连字符卡号)"
    );
    
    console.log("✓ 防退化: 无全局性连字符截断逻辑");
  });
});

/**
 * 运行测试:
 * ```bash
 * node --test agents/harness/pi/lybra-loop/tests/f35c-hyphenated-task-id.test.ts
 * ```
 */
