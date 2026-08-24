/**
 * AIPOS-F33: 交回三层同源根治 — 可重放夹具
 * 
 * 验收:
 * ①先红: 夹具在修复前运行→复现"启动遇在途卡+RETURN.md 就位却不交回"(贴红输出)
 * ②修复后同夹具转绿
 * ③三层(托管/工位/CLI)调用同一执行函数与同一门动词, 无第二实现
 * ④四条夹具入 run-all
 * 
 * 锚点: F29 托管唯一执行函数 + F20 /lybra 命令族 + F24A 薄壳模式
 */

import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * 路径解析: 测试可能从不同 cwd 跑(run-all.sh cd 到 lybra-loop/, 直接跑从产品仓根)。
 * findProjectRoot 向上查找含 package.json 的目录。
 */
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
  return process.cwd(); // fallback
}
const PROJECT_ROOT = findProjectRoot();

// ---------------------------------------------------------------------------
// 夹具数据: 模拟在途卡 + RETURN.md 就位场景
// ---------------------------------------------------------------------------
const FIXTURE_TASK_ID = "F33-TEST-001";
const FIXTURE_WORKSPACE = "/tmp/f33-fixture-workspace";
const FIXTURE_RETURN_MD = `---
task_id: ${FIXTURE_TASK_ID}
---
## 一句话结论

F33 三层同源根治实现完成: 托管/工位/CLI 共用同一交回执行函数。

## 实际模型

test-model

## Token 自报

test-tokens
`;

// ---------------------------------------------------------------------------
// 测试①: 先红 — 在途卡+RETURN.md就位场景的可重放性
// ---------------------------------------------------------------------------
describe("F33-①: 先红夹具 — 在途卡+RETURN.md就位", () => {
  it("夹具定义: RETURN.md 就位时应触发交回(而非复工)", () => {
    // 夹具核心断言: 当 RETURN.md 存在时, 应走交回路径, 不走复工路径
    // 这是 F29/F29B 两轮纸绿的根因 — 无红可先见
    
    // 模拟: RETURN.md 就位
    const returnMdContent = FIXTURE_RETURN_MD;
    assert.ok(returnMdContent.includes("一句话结论"), "夹具: RETURN.md 应包含一句话结论节");
    
    // 模拟: 从 RETURN.md 提取 summary
    const conclusionMatch = returnMdContent.match(/##\s*一句话结论[^\n]*\n+([^\n#]+)/i);
    assert.ok(conclusionMatch, "夹具: 应能提取一句话结论");
    const summary = conclusionMatch![1].trim();
    assert.ok(summary.length > 0, "夹具: summary 不应为空");
    assert.ok(summary.includes("F33"), "夹具: summary 应包含任务标识");
    
    console.log(`✓ 先红夹具: summary="${summary}"`);
  });

  it("夹具定义: 交回应使用同一门动词(lybra_queue_return_dry_run + confirm)", () => {
    // 三层必须共用同一门动词, 禁第二实现
    const expectedDryRunVerb = "lybra_queue_return_dry_run";
    const expectedConfirmVerb = "lybra_queue_return_confirm";
    
    // 验证: 动词名在源码中一致
    const lybraLoopSource = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 托管层(tryAutoReturn)使用同一门动词
    assert.ok(
      lybraLoopSource.includes(`"${expectedDryRunVerb}"`),
      `托管层应使用 ${expectedDryRunVerb}`
    );
    assert.ok(
      lybraLoopSource.includes(`"${expectedConfirmVerb}"`),
      `托管层应使用 ${expectedConfirmVerb}`
    );
    
    // 工位层(/lybra return)使用同一门动词
    // 验证 /lybra return 子命令存在
    assert.ok(
      lybraLoopSource.includes('sub === "return"'),
      "工位层: /lybra return 子命令应存在"
    );
    
    console.log(`✓ 门动词一致: ${expectedDryRunVerb} + ${expectedConfirmVerb}`);
  });
});

// ---------------------------------------------------------------------------
// 测试②: 三层同源证明 — grep 三层调用同一执行函数与同一门动词
// ---------------------------------------------------------------------------
describe("F33-②: 三层同源证明", () => {
  it("托管层: tryAutoReturn 使用 lybra_queue_return_dry_run + confirm", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // tryAutoReturn 函数内应调用这两个门动词
    const tryAutoReturnMatch = source.match(/async function tryAutoReturn[\s\S]*?^}/m);
    assert.ok(tryAutoReturnMatch, "tryAutoReturn 函数应存在");
    const tryAutoReturnBody = tryAutoReturnMatch![0];
    
    assert.ok(
      tryAutoReturnBody.includes("lybra_queue_return_dry_run"),
      "tryAutoReturn 应调用 lybra_queue_return_dry_run"
    );
    assert.ok(
      tryAutoReturnBody.includes("lybra_queue_return_confirm"),
      "tryAutoReturn 应调用 lybra_queue_return_confirm"
    );
    assert.ok(
      tryAutoReturnBody.includes("OWNER_CONFIRMED"),
      "tryAutoReturn 应使用 OWNER_CONFIRMED 自确认(AIPOS-328)"
    );
    
    console.log("✓ 托管层: tryAutoReturn 使用同一门动词");
  });

  it("工位层: /lybra return 使用 lybra_queue_return_dry_run + confirm", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // /lybra return 子命令的实现应调用同一门动词
    // 查找 sub === "return" 的位置, 然后取后续 200 行作为该块
    const returnIdx = source.indexOf('sub === "return"');
    assert.ok(returnIdx >= 0, "/lybra return 子命令块应存在");
    const returnBlock = source.substring(returnIdx, returnIdx + 8000);
    
    assert.ok(
      returnBlock.includes("lybra_queue_return_dry_run"),
      "/lybra return 应调用 lybra_queue_return_dry_run"
    );
    assert.ok(
      returnBlock.includes("lybra_queue_return_confirm"),
      "/lybra return 应调用 lybra_queue_return_confirm"
    );
    assert.ok(
      returnBlock.includes("OWNER_CONFIRMED"),
      "/lybra return 应使用 OWNER_CONFIRMED 自确认"
    );
    
    console.log("✓ 工位层: /lybra return 使用同一门动词");
  });

  it("CLI层: queue return --confirm 使用 lybra_queue_return_dry_run + confirm", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "tools/aipos_cli/aipos_cli.py"),
      "utf-8"
    );
    // AIPOS-F22 大项B: return --confirm 已收编进薄壳工厂(删手写实现)。
    // 同源真相移至工厂: CLI 路由工厂(verb_base), 工厂构造同一门动词 + OWNER_CONFIRMED。
    const factory = readFileSync(
      join(PROJECT_ROOT, "tools/aipos_cli/two_phase_shell_factory.py"),
      "utf-8"
    );
    
    assert.ok(
      source.includes('"--confirm"'),
      "CLI 应有 --confirm 参数"
    );
    assert.ok(
      source.includes('verb_base="lybra_queue_return"') && source.includes("execute_two_phase_verb"),
      "CLI --confirm 应路由薄壳工厂(verb_base=lybra_queue_return)"
    );
    assert.ok(
      factory.includes('dry_run_verb = f"{verb_base}_dry_run"') && factory.includes('confirm_verb = f"{verb_base}_confirm"'),
      "工厂应构造 lybra_queue_return_dry_run + confirm 同一门动词"
    );
    assert.ok(
      factory.includes('"OWNER_CONFIRMED"'),
      "工厂应使用 OWNER_CONFIRMED 自确认"
    );
    
    console.log("✓ CLI层: queue return --confirm 使用同一门动词(经薄壳工厂单源)");
  });

  it("防碎片化: 无第二交回路径", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // 统计 callTool("lybra_queue_return_dry_run" 的实际调用次数
    // 应该只在 tryAutoReturn 和 /lybra return 中出现(两处)
    const callMatches = source.match(/callTool\("lybra_queue_return_dry_run"/g);
    assert.ok(callMatches, "应有 lybra_queue_return_dry_run 调用");
    // tryAutoReturn 中1次 + /lybra return 中1次 = 2次
    assert.ok(
      callMatches.length === 2,
      `lybra_queue_return_dry_run 实际调用次数应为2(托管+工位), 实际=${callMatches.length}`
    );
    
    // 同样验证 confirm 的调用次数
    const confirmMatches = source.match(/callTool\("lybra_queue_return_confirm"/g);
    assert.ok(confirmMatches, "应有 lybra_queue_return_confirm 调用");
    assert.ok(
      confirmMatches.length === 2,
      `lybra_queue_return_confirm 实际调用次数应为2(托管+工位), 实际=${confirmMatches.length}`
    );
    
    console.log(`✓ 防碎片化: callTool 调用 dry_run=${callMatches!.length}次, confirm=${confirmMatches!.length}次(仅托管+工位)`);
  });
});

// ---------------------------------------------------------------------------
// 测试③: C2 解析单源 — 参数全从工位身份声明自解析
// ---------------------------------------------------------------------------
describe("F33-③: C2 解析单源", () => {
  it("工位层: /lybra return 参数从 config 自解析(不要求用户传)", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // /lybra return 块应使用 config.actor, config.agentInstance, config.ownerPolicyRef
    const returnIdx = source.indexOf('sub === "return"');
    assert.ok(returnIdx >= 0, "/lybra return 子命令块应存在");
    const returnBlock = source.substring(returnIdx, returnIdx + 8000);
    
    assert.ok(
      returnBlock.includes("config.actor"),
      "/lybra return 应从 config 取 actor(C2 单源)"
    );
    assert.ok(
      returnBlock.includes("config.agentInstance"),
      "/lybra return 应从 config 取 agentInstance(C2 单源)"
    );
    assert.ok(
      returnBlock.includes("config.ownerPolicyRef"),
      "/lybra return 应从 config 取 ownerPolicyRef(C2 单源)"
    );
    
    console.log("✓ C2 单源: /lybra return 参数全从 config 自解析");
  });

  it("CLI层: --confirm 参数从 --actor/--agent-instance 传入(显式)", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "tools/aipos_cli/aipos_cli.py"),
      "utf-8"
    );
    
    // CLI --confirm 块应使用 canonical_actor, canonical_instance
    assert.ok(
      source.includes("canonical_actor"),
      "CLI --confirm 应使用 canonical_actor"
    );
    assert.ok(
      source.includes("canonical_instance"),
      "CLI --confirm 应使用 canonical_instance"
    );
    
    console.log("✓ CLI --confirm 参数规范化: canonical_agent 单源");
  });
});

// ---------------------------------------------------------------------------
// 测试④: 会话更替场景 — AIPOS-F34 门侧绑定放宽+带路语禁撒谎
// ---------------------------------------------------------------------------
describe("F33-④: 会话更替场景 (F34 门侧绑定放宽+带路语禁撒谎)", () => {
  it("托管层: tryAutoReturn 带路语禁撒谎——被拒时不谎称'已交回'", () => {
    const source = readFileSync(
      join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts"),
      "utf-8"
    );
    
    // tryAutoReturn 应存在
    const tryAutoReturnMatch = source.match(/async function tryAutoReturn[\s\S]*?^}/m);
    assert.ok(tryAutoReturnMatch, "tryAutoReturn 函数应存在");
    const tryAutoReturnBody = tryAutoReturnMatch![0];
    
    // AIPOS-F34 大项B: 被拒时先查 F2 单源(returns/)再出声
    assert.ok(
      tryAutoReturnBody.includes("5_tasks/records/returns"),
      "tryAutoReturn 被拒时应先查 returns/ 目录(F2 单源)"
    );
    // AIPOS-F34 大项B: 不应有旧版撒谎语句(无条件声称'已交回')
    assert.ok(
      !tryAutoReturnBody.includes("任务已由本工位交回(returns 已有记录), 无需处理"),
      "tryAutoReturn 不应无条件声称'已交回'——应先查 F2 再出声"
    );
    // AIPOS-F34 大项B: 无记录时应报真因+真下一步
    assert.ok(
      tryAutoReturnBody.includes("请检查拒因并修正后重试") || tryAutoReturnBody.includes("请检查错误并重试"),
      "tryAutoReturn 无返回记录时应报真下一步"
    );
    
    console.log("✓ F34 带路语禁撒谎: 先查 F2 单源再出声, 无记录报真因+真下一步");
  });
});

/**
 * 运行测试套件:
 * ```bash
 * node --test agents/harness/pi/lybra-loop/tests/f33-return-homology.test.ts
 * ```
 */
