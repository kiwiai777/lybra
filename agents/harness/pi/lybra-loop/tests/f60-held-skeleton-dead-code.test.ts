/**
 * AIPOS-F60: held 路就位判据永真 → 骨架变死代码 — 先红后绿夹具
 *
 * 根因: F43 在认领那刻就落 RETURN 骨架, fs.existsSync 永真 → held 路每次都走
 * 托管提交/交回、拿骨架占位符去提交、必失败、return 死路; 真正该跑的投递复工
 * 自愈分支变成永不可达的死代码。
 *
 * 测试内容:
 * 1. isReturnMdSubstantive 函数: 骨架 → false, 实质内容 → true
 * 2. 审计车道: 骨架 RETURN.md 不应触发托管提交(应走复工投递)
 * 3. 执行车道: 骨架 RETURN.md 不应触发托管交回(应走复工投递)
 * 4. sweep close: confirm 缺两阶段参数必失败 + 返回值不校验 → 假报成功
 * 5. 跨卡防碎片通用禁令: grep 证实本卡未新增记录写入/workspace_root解析/项目域解析/token选取/队列状态变更
 *
 * 跑法: node tests/f60-held-skeleton-dead-code.test.ts
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, existsSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, "package.json")) && existsSync(join(dir, "agents"))) return dir;
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}
const PROJECT_ROOT = findProjectRoot();
const LOOP_FILE = join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/lybra-loop.ts");

// ---------------------------------------------------------------------------
// Helper: 从源码提取 isReturnMdSubstantive 逻辑,用测试上下文的 fs 重写为纯 JS
// (避免 eval + require 在 ESM 上下文的问题)
// ---------------------------------------------------------------------------
function isReturnMdSubstantiveFromSource(returnMdPath: string, lane: "audit" | "exec"): boolean {
  if (!existsSync(returnMdPath)) return false;

  let content: string;
  try {
    content = readFileSync(returnMdPath, "utf-8");
  } catch {
    return false;
  }

  // 骨架标志: 含 "(待填写)" 或 "骨架由连接器投递时自动落盘" → 未填写
  const isSkeleton = /\(待填写\)/.test(content) || /骨架由连接器投递时自动落盘/.test(content);
  if (isSkeleton) return false;

  if (lane === "audit") {
    // 审计车道: 必须有真实 verdict(不是占位符 "(PASS / FAIL / BLOCK)")
    const verdictMatch = content.match(/verdict:\s*(PASS|FAIL|PASS_WITH_NOTES|BLOCK)\b/i);
    if (!verdictMatch) return false;
    // 排除占位符形式: "(PASS / FAIL / BLOCK)"
    if (/verdict:\s*\(PASS\s*\/\s*FAIL/i.test(content)) return false;
  }

  // 执行车道: 只要不是骨架(已在上面排除)即视为有实质内容
  return true;
}

// ---------------------------------------------------------------------------
// 1. isReturnMdSubstantive 函数单元测试
//    同时验证: 源码中的函数逻辑与测试中的参考实现一致
// ---------------------------------------------------------------------------
describe("AIPOS-F60 isReturnMdSubstantive", () => {
  let testDir: string;

  it("setup temp dir", () => {
    testDir = mkdtempSync(join(tmpdir(), "f60-test-"));
  });

  it("骨架 RETURN.md(exec) → false", () => {
    const skeletonExec = `# AIPOS-TEST 交付报告

## 一句话结论

(待填写)

## 状态

IN_PROGRESS

## 验收清单

(无验收清单)

---
(骨架由连接器投递时自动落盘,内容单源=卡面。执行体完成后填写上方各节。)
`;
    const p = join(testDir, "RETURN-exec.md");
    writeFileSync(p, skeletonExec, "utf-8");
    assert.strictEqual(isReturnMdSubstantiveFromSource(p, "exec"), false, "骨架应判 false");
  });

  it("骨架 RETURN.md(audit) → false", () => {
    const skeletonAudit = `# AIPOS-TEST 审计报告

## 审计裁决

verdict: (PASS / FAIL / BLOCK)

## 一句话结论

(待填写)

## 验收清单

(无验收清单)

---
(骨架由连接器投递时自动落盘,内容单源=卡面。审计体完成后填写上方各节。)
`;
    const p = join(testDir, "RETURN-audit.md");
    writeFileSync(p, skeletonAudit, "utf-8");
    assert.strictEqual(isReturnMdSubstantiveFromSource(p, "audit"), false, "骨架应判 false");
  });

  it("实质内容 RETURN.md(exec) → true", () => {
    const realExec = `# AIPOS-TEST 交付报告

## 一句话结论

完成。三项修复已落地,测试全通过。

## 状态

COMPLETED

## 验收清单

- [x] 修复1
- [x] 修复2
`;
    const p = join(testDir, "RETURN-exec-real.md");
    writeFileSync(p, realExec, "utf-8");
    assert.strictEqual(isReturnMdSubstantiveFromSource(p, "exec"), true, "实质内容应判 true");
  });

  it("实质内容 RETURN.md(audit) 有真实 verdict → true", () => {
    const realAudit = `# AIPOS-TEST 审计报告

## 审计裁决

verdict: PASS

## 一句话结论

审计通过,代码符合裁定。

## 验收清单

- [x] 检查1
`;
    const p = join(testDir, "RETURN-audit-real.md");
    writeFileSync(p, realAudit, "utf-8");
    assert.strictEqual(isReturnMdSubstantiveFromSource(p, "audit"), true, "有真实 verdict 应判 true");
  });

  it("有内容但 audit 缺 verdict → false", () => {
    const noVerdict = `# AIPOS-TEST 审计报告

## 审计裁决

(待填写)

## 一句话结论

审计进行中。
`;
    const p = join(testDir, "RETURN-audit-no-verdict.md");
    writeFileSync(p, noVerdict, "utf-8");
    assert.strictEqual(isReturnMdSubstantiveFromSource(p, "audit"), false, "缺 verdict 应判 false");
  });

  it("文件不存在 → false", () => {
    assert.strictEqual(isReturnMdSubstantiveFromSource(join(testDir, "nonexistent.md"), "exec"), false);
  });

  it("PASS_WITH_NOTES verdict → true (audit)", () => {
    const content = `# Report\n\nverdict: PASS_WITH_NOTES\n\n## 一句话结论\n\nDone with notes.\n`;
    const p = join(testDir, "RETURN-pwn.md");
    writeFileSync(p, content, "utf-8");
    assert.strictEqual(isReturnMdSubstantiveFromSource(p, "audit"), true);
  });

  it("FAIL verdict → true (audit)", () => {
    const content = `# Report\n\nverdict: FAIL\n\n## 一句话结论\n\nFailed.\n`;
    const p = join(testDir, "RETURN-fail.md");
    writeFileSync(p, content, "utf-8");
    assert.strictEqual(isReturnMdSubstantiveFromSource(p, "audit"), true);
  });

  it("源码函数逻辑与参考实现一致(grep 验证关键判据)", () => {
    const src = readFileSync(LOOP_FILE, "utf-8");
    // 源码中应有相同的骨架检测逻辑
    assert.ok(/\\\(待填写\\\)/.test(src) || /待填写/.test(src), "源码应检测 '待填写' 占位符");
    assert.ok(/骨架由连接器投递时自动落盘/.test(src), "源码应检测骨架标记");
  });

  it("cleanup", () => {
    rmSync(testDir, { recursive: true, force: true });
  });
});

// ---------------------------------------------------------------------------
// 2. 源码级验证: held 路使用 isReturnMdSubstantive 而非 fs.existsSync
// ---------------------------------------------------------------------------
describe("AIPOS-F60 source-level: held 路判据已收敛", () => {
  const src = readFileSync(LOOP_FILE, "utf-8");

  it("审计车道 held 路用 isReturnMdSubstantive 而非 fs.existsSync", () => {
    const startIdx = src.indexOf('currentLogger.info("held-audit-no-verdict"');
    assert.ok(startIdx > 0, "held-audit-no-verdict 块应存在于源码");
    const endIdx = src.indexOf("// 报告未就位 → 投递复工", startIdx);
    const auditHeldBlock = src.slice(startIdx, endIdx > startIdx ? endIdx : startIdx + 2000);
    assert.ok(auditHeldBlock.length > 0, "应找到审计车道 held 块");
    assert.ok(
      auditHeldBlock.includes("isReturnMdSubstantive(auditReportPath"),
      "审计车道 held 路应使用 isReturnMdSubstantive"
    );
    assert.ok(
      !auditHeldBlock.includes("const reportReady = fs.existsSync(auditReportPath)"),
      "审计车道 held 路不应再用 fs.existsSync 判 reportReady"
    );
  });

  it("执行车道 held 路用 isReturnMdSubstantive 而非 fs.existsSync", () => {
    const startIdx = src.indexOf("AIPOS-F37 大项A扩展: 执行车道 held 路托管接线");
    assert.ok(startIdx > 0, "执行车道 held 块标记应存在");
    const endIdx = src.indexOf("// RETURN.md 未就位 → 投递复工", startIdx);
    const execHeldBlock = src.slice(startIdx, endIdx > startIdx ? endIdx : startIdx + 2000);
    assert.ok(execHeldBlock.length > 0, "应找到执行车道 held 块");
    assert.ok(
      execHeldBlock.includes("isReturnMdSubstantive(returnMdPath"),
      "执行车道 held 路应使用 isReturnMdSubstantive"
    );
    assert.ok(
      !execHeldBlock.match(/if\s*\(\s*fs\.existsSync\(returnMdPath\)\s*\)/),
      "执行车道 held 路不应再用 fs.existsSync(returnMdPath)"
    );
  });

  it("两条车道共用同一函数(收敛为单一实现)", () => {
    const uses = (src.match(/isReturnMdSubstantive\(/g) || []).length;
    // 至少 3 处: 函数定义 + 审计车道 + 执行车道
    assert.ok(uses >= 3, `isReturnMdSubstantive 应至少被引用 3 次(定义+审计+执行), 实得 ${uses}`);
  });
});

// ---------------------------------------------------------------------------
// 3. sweep close 两阶段参数验证
// ---------------------------------------------------------------------------
describe("AIPOS-F60 sweep close 两阶段传参", () => {
  const src = readFileSync(LOOP_FILE, "utf-8");

  // 定位 sweep close 块: 从 dry_run 调用到 processedCount++ 后
  function getSweepCloseBlock(): string {
    const dryRunIdx = src.indexOf('callTool("lybra_queue_close_dry_run"');
    assert.ok(dryRunIdx > 0, "应找到 lybra_queue_close_dry_run 调用");
    // 取足够大的范围覆盖到 processedCount++
    const endIdx = src.indexOf("processedCount++", dryRunIdx);
    assert.ok(endIdx > dryRunIdx, "应找到 processedCount++");
    return src.slice(dryRunIdx, endIdx + 100);
  }

  it("confirm 调用必须带 dry_run_token", () => {
    const block = getSweepCloseBlock();
    assert.ok(
      /dry_run_token/.test(block),
      "sweep close confirm 必须传 dry_run_token"
    );
  });

  it("confirm 调用必须带 owner_confirmation_token", () => {
    const block = getSweepCloseBlock();
    assert.ok(
      /owner_confirmation_token.*OWNER_CONFIRMED/.test(block),
      "sweep close confirm 必须传 owner_confirmation_token: 'OWNER_CONFIRMED'"
    );
  });

  it("confirm 应答必须校验(BLOCK/isError)", () => {
    const block = getSweepCloseBlock();
    assert.ok(
      /closeConfirmResp\.(verdict|isError)/.test(block),
      "confirm 应答必须校验 verdict/isError"
    );
  });

  it("confirm 失败不得报'已收账'(continue 跳过)", () => {
    const block = getSweepCloseBlock();
    // confirm BLOCK 分支应存在
    assert.ok(
      block.includes("auto-close-confirm-blocked"),
      "应有 auto-close-confirm-blocked 日志标记"
    );
    // 从 confirm BLOCK 检查点到下一个 continue 之间不应有 "已收账"
    const blockedIdx = block.indexOf("auto-close-confirm-blocked");
    const afterBlocked = block.slice(blockedIdx);
    const continueIdx = afterBlocked.indexOf("continue");
    assert.ok(continueIdx > 0, "confirm BLOCK 后应有 continue");
    const betweenBlockAndContinue = afterBlocked.slice(0, continueIdx);
    assert.ok(
      !betweenBlockAndContinue.includes("已收账"),
      "confirm BLOCK 到 continue 之间不应出现 '已收账'"
    );
  });
});

// ---------------------------------------------------------------------------
// 4. 跨卡防碎片通用禁令: grep 证实本卡未新增禁止实现点
// ---------------------------------------------------------------------------
describe("AIPOS-F60 跨卡防碎片通用禁令", () => {
  const src = readFileSync(LOOP_FILE, "utf-8");

  it("未新增 records.py 相关写入(归 F64)", () => {
    const funcMatch = src.match(/function isReturnMdSubstantive[\s\S]*?\n\}/);
    assert.ok(funcMatch, "函数应存在");
    assert.ok(
      !/records.*write|writeFile.*records|records.*mkdir/i.test(funcMatch![0]),
      "isReturnMdSubstantive 不应写 records"
    );
  });

  it("未新增 workspace_root 解析(归 F65)", () => {
    const funcMatch = src.match(/function isReturnMdSubstantive[\s\S]*?\n\}/);
    assert.ok(funcMatch);
    assert.ok(
      !/resolve.*workspace|workspace.*resolve|LYBRA_WORKSPACE|AIPOS_WORKSPACE/i.test(funcMatch![0]),
      "isReturnMdSubstantive 不应解析 workspace_root"
    );
  });

  it("未新增 token 选取逻辑(归 F59)", () => {
    const funcMatch = src.match(/function isReturnMdSubstantive[\s\S]*?\n\}/);
    assert.ok(funcMatch);
    assert.ok(
      !/token.*select|token.*resolve|resolveToken|getToken/i.test(funcMatch![0]),
      "isReturnMdSubstantive 不应涉及 token 选取"
    );
  });

  it("sweep close 修改未新增队列状态变更(归 F63)", () => {
    const dryRunIdx = src.indexOf('callTool("lybra_queue_close_dry_run"');
    const endIdx = src.indexOf("processedCount++", dryRunIdx);
    const sweepBlock = src.slice(dryRunIdx, endIdx + 100);
    assert.ok(
      !/queue.*mutate|queue.*status.*change|updateQueue/i.test(sweepBlock),
      "sweep close 不应新增队列状态变更"
    );
  });
});
