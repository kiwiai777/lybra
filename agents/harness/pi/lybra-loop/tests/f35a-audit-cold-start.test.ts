/**
 * AIPOS-F35 大项A: 审计车道冷启动修真 — F10 liveCtx.newSession 范式
 * 
 * 验收:
 * ①先红: 夹具在修复前运行→复现"held-resume 审计车道 sendUserMessage 失败/newSession is not a function"
 * ②修复后同夹具转绿
 * ③held-resume 审计车道使用 liveCtx.newSession(不用 sendUserMessage)
 * ④对齐 claim.ts + release 路径: withSession 回调内只用 freshCtx
 * 
 * 锚点: F10 liveCtx 已验证范式 + F-EXT001 家族第5象
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

describe("F35-A①: 审计车道冷启动 F10 范式", () => {
  it("held-resume 审计车道应使用 liveCtx.newSession(不用 sendUserMessage)", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 查找审计车道 held-resume 块(AIPOS-F37-fix1-fix1: 锚点改到 F35 冷启动派发块本身,
    // F37 托管接线(报告就位先走托管)合法地插在其前, 固定窗口不再跨越它)
    const auditResumeIdx = source.indexOf("AIPOS-F35 大项A: 审计车道冷启动修真");
    assert.ok(auditResumeIdx > 0, "审计车道冷启动派发块(F35)应存在");
    
    // 取该块后续 4000 字符
    const auditResumeBlock = source.substring(auditResumeIdx, auditResumeIdx + 4000);
    
    // 断言: 应使用 liveCtx.newSession
    assert.ok(
      auditResumeBlock.includes("liveCtx.newSession"),
      "审计车道 held-resume 应使用 liveCtx.newSession(F10 范式)"
    );
    
    // 断言: 应使用 withSession 回调
    assert.ok(
      auditResumeBlock.includes("withSession"),
      "审计车道 held-resume 应使用 withSession 回调(F10 范式)"
    );
    
    // 断言: 应使用 freshCtx.sendUserMessage(在 withSession 内)
    assert.ok(
      auditResumeBlock.includes("freshCtx.sendUserMessage"),
      "审计车道 held-resume 应在 withSession 内使用 freshCtx(不捕获旧 ctx)"
    );
    
    // 断言: 应处理 result?.cancelled
    assert.ok(
      auditResumeBlock.includes("result?.cancelled"),
      "审计车道 held-resume 应处理 newSession 被拦截场景"
    );
    
    console.log("✓ F10 范式: 审计车道 held-resume 使用 liveCtx.newSession");
  });

  it("审计车道 held-resume 应对齐 release 路径的异常处理", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    const auditResumeIdx = source.indexOf("AIPOS-F35 大项A: 审计车道冷启动修真");
    const auditResumeBlock = source.substring(auditResumeIdx, auditResumeIdx + 4000);
    
    // 断言: newSession 异常时应降级出声(不静默吞)
    assert.ok(
      auditResumeBlock.includes("newSession 异常") || auditResumeBlock.includes("newSession-failed"),
      "审计车道 held-resume newSession 异常时应出声"
    );
    
    // 断言: 应设置 expectingSwap(对齐 release 路径)
    assert.ok(
      auditResumeBlock.includes("expectingSwap"),
      "审计车道 held-resume 应设置 expectingSwap 标志"
    );
    
    // 断言: newSession 前应复位 loopState.running
    assert.ok(
      auditResumeBlock.includes("loopState.running = false"),
      "审计车道 held-resume newSession 前应复位 running 标志"
    );
    
    console.log("✓ 异常处理对齐: 审计车道 held-resume 对齐 release 路径");
  });
});

describe("F35-A②: 禁止旧版 sendUserMessage 路径", () => {
  it("审计车道 held-resume 不应直接使用 liveCtx.sendUserMessage", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    const auditResumeIdx = source.indexOf("AIPOS-F35 大项A: 审计车道冷启动修真");
    const auditResumeBlock = source.substring(auditResumeIdx, auditResumeIdx + 4000);
    
    // 断言: 不应有 liveCtx.sendUserMessage(应改用 newSession)
    // 注意: freshCtx.sendUserMessage 是允许的(在 withSession 内)
    const hasBadPattern = auditResumeBlock.includes("liveCtx.sendUserMessage(") ||
                          auditResumeBlock.includes("await liveCtx.sendUserMessage");
    
    assert.ok(
      !hasBadPattern,
      "审计车道 held-resume 不应直接使用 liveCtx.sendUserMessage(应改用 liveCtx.newSession)"
    );
    
    console.log("✓ 禁止旧版路径: 审计车道不直接用 liveCtx.sendUserMessage");
  });
});

/**
 * 运行测试:
 * ```bash
 * node --test agents/harness/pi/lybra-loop/tests/f35a-audit-cold-start.test.ts
 * ```
 */
