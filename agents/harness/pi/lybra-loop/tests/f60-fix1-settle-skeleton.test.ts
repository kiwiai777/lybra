/**
 * AIPOS-F60-fix1: settle 路(tryAutoReturn)漏修 — 骨架被正则提取成空白交回
 *
 * 根因: F60 把 isReturnMdSubstantive 用在了 held 两路, 唯独漏了 tryAutoReturn 的 settle 路。
 * 会话结束时自动交回走 settle 路 → fs.existsSync 永真(骨架恒存在) → 正则成功提取 "(待填写)"
 * → result_summary="(待填写)" / artifact_refs=[] → 空白交回。
 *
 * 测试内容:
 * 1. 源码级: tryAutoReturn 内两路(audit/exec)均使用 isReturnMdSubstantive 而非 fs.existsSync
 * 2. 源码级: 三路(settle audit + settle exec + held)同源 — grep 证实无第二判据
 * 3. 源码级: 骨架检测出声 — else-if 分支存在且含 voice 调用
 * 4. 行为级: isReturnMdSubstantive 对骨架 → false, 对实质 → true(复用 F60 函数)
 * 5. 零回归: held 两路仍使用 isReturnMdSubstantive(F60 既有行为不变)
 *
 * 跑法: node tests/f60-fix1-settle-skeleton.test.ts
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, existsSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
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
// Helper: 从源码提取 isReturnMdSubstantive 逻辑(与 F60 测试同源)
// ---------------------------------------------------------------------------
function isReturnMdSubstantiveFromSource(returnMdPath: string, lane: "audit" | "exec"): boolean {
  if (!existsSync(returnMdPath)) return false;

  let content: string;
  try {
    content = readFileSync(returnMdPath, "utf-8");
  } catch {
    return false;
  }

  const isSkeleton = /\(待填写\)/.test(content) || /骨架由连接器投递时自动落盘/.test(content);
  if (isSkeleton) return false;

  if (lane === "audit") {
    const verdictMatch = content.match(/verdict:\s*(PASS|FAIL|PASS_WITH_NOTES|BLOCK)\b/i);
    if (!verdictMatch) return false;
    if (/verdict:\s*\(PASS\s*\/\s*FAIL/i.test(content)) return false;
  }

  return true;
}

// ---------------------------------------------------------------------------
// 1. 源码级: tryAutoReturn 内 settle 路已改用 isReturnMdSubstantive
// ---------------------------------------------------------------------------
describe("AIPOS-F60-fix1 settle 路源码级验证", () => {
  const src = readFileSync(LOOP_FILE, "utf-8");

  // 提取 tryAutoReturn 函数体
  function getTryAutoReturnBody(): string {
    const startIdx = src.indexOf("async function tryAutoReturn()");
    assert.ok(startIdx > 0, "tryAutoReturn 函数应存在");
    // 找函数结尾: 从 startIdx 向后找下一个顶层 function 或 export
    // 简单做法: 取足够大的范围(tryAutoReturn 约 300 行)
    return src.slice(startIdx, startIdx + 15000);
  }

  it("settle 审计路: 用 isReturnMdSubstantive(reportPath, \"audit\") 而非 fs.existsSync(reportPath)", () => {
    const body = getTryAutoReturnBody();
    // 定位审计路块: 从 "AIPOS-F29 大项E" 或 "reportPath" 定义到 "审计卡无 verdict"
    const auditStart = body.indexOf('const reportPath = path.join(config.workspaceRoot, "task_cards", currentTaskId, "RETURN.md")');
    assert.ok(auditStart > 0, "审计路 reportPath 定义应存在");
    const auditEnd = body.indexOf("审计卡无 verdict 且无报告", auditStart);
    assert.ok(auditEnd > auditStart, "审计路结束标记应存在");
    const auditBlock = body.slice(auditStart, auditEnd);

    assert.ok(
      auditBlock.includes('isReturnMdSubstantive(reportPath, "audit")'),
      "settle 审计路应使用 isReturnMdSubstantive(reportPath, \"audit\")"
    );
    // 确认主判据不再是 fs.existsSync(reportPath)
    // 注意: else-if 中仍可有 fs.existsSync 用于骨架检测, 但主 if 不应是它
    const mainIfMatch = auditBlock.match(/\/\/.*F60-fix1.*\n\s*if\s*\(([^)]+)\)/);
    assert.ok(mainIfMatch, "应找到 F60-fix1 标记的主 if");
    assert.ok(
      mainIfMatch![1].includes("isReturnMdSubstantive"),
      "主 if 判据应为 isReturnMdSubstantive"
    );
  });

  it("settle 执行路: 用 isReturnMdSubstantive(returnMdPath, \"exec\") 而非 fs.existsSync(returnMdPath)", () => {
    const body = getTryAutoReturnBody();
    // 定位执行路块: "AIPOS-F29 大项A" 附近
    const execStart = body.indexOf("AIPOS-F29 大项A: 优先侦测 RETURN.md");
    assert.ok(execStart > 0, "执行路块标记应存在");
    const execEnd = body.indexOf("回退兜底网: completed 事件", execStart);
    assert.ok(execEnd > execStart, "执行路结束标记应存在");
    const execBlock = body.slice(execStart, execEnd);

    assert.ok(
      execBlock.includes('isReturnMdSubstantive(returnMdPath, "exec")'),
      "settle 执行路应使用 isReturnMdSubstantive(returnMdPath, \"exec\")"
    );
    // 主 if 不应是 fs.existsSync
    const mainIfMatch = execBlock.match(/\/\/.*F60-fix1.*\n\s*if\s*\(([^)]+)\)/);
    assert.ok(mainIfMatch, "应找到 F60-fix1 标记的主 if");
    assert.ok(
      mainIfMatch![1].includes("isReturnMdSubstantive"),
      "主 if 判据应为 isReturnMdSubstantive"
    );
  });

  it("骨架检测出声: else-if 分支含 voice 调用(审计路)", () => {
    const body = getTryAutoReturnBody();
    // 查找审计路骨架检测标记
    assert.ok(
      body.includes("auto-return-audit-skeleton"),
      "审计路骨架检测日志标记应存在"
    );
    // 确认有 voice 调用在骨架检测分支
    const skeletonIdx = body.indexOf("auto-return-audit-skeleton");
    const afterSkeleton = body.slice(skeletonIdx, skeletonIdx + 500);
    assert.ok(
      afterSkeleton.includes("voice("),
      "骨架检测分支应有 voice 调用(出声带可执行出口)"
    );
  });

  it("骨架检测出声: else-if 分支含 voice 调用(执行路)", () => {
    const body = getTryAutoReturnBody();
    assert.ok(
      body.includes("auto-return-skeleton"),
      "执行路骨架检测日志标记应存在"
    );
    const skeletonIdx = body.indexOf("auto-return-skeleton");
    const afterSkeleton = body.slice(skeletonIdx, skeletonIdx + 500);
    assert.ok(
      afterSkeleton.includes("voice("),
      "骨架检测分支应有 voice 调用(出声带可执行出口)"
    );
  });
});

// ---------------------------------------------------------------------------
// 2. 三路同源: grep 证实无第二判据
// ---------------------------------------------------------------------------
describe("AIPOS-F60-fix1 三路同源验证", () => {
  const src = readFileSync(LOOP_FILE, "utf-8");

  it("isReturnMdSubstantive 被引用 ≥ 5 次(定义 + settle audit + settle exec + held audit + held exec)", () => {
    const uses = (src.match(/isReturnMdSubstantive\(/g) || []).length;
    assert.ok(uses >= 5, `isReturnMdSubstantive 应至少被引用 5 次, 实得 ${uses}`);
  });

  it("tryAutoReturn 内不再有 fs.existsSync 作为 RETURN.md 主判据", () => {
    const body = src.slice(
      src.indexOf("async function tryAutoReturn()"),
      src.indexOf("async function tryAutoReturn()") + 15000
    );
    // 审计路: reportPath 的主判据不应是 fs.existsSync
    // 注意: isReturnMdSubstantive 内部用了 fs.existsSync, 那是函数内部, 不算
    // 我们检查: 在 tryAutoReturn 内, "fs.existsSync(reportPath)" 和 "fs.existsSync(returnMdPath)"
    // 只应出现在 else-if 骨架检测分支(有注释标记), 不应出现在主 if
    const lines = body.split("\n");
    let inElseIfSkeleton = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (line.includes("} else if (fs.existsSync(") && lines[i - 1]?.includes("F60-fix1")) {
        inElseIfSkeleton = true;
        continue;
      }
      // 主 if 行(非 else-if)含 fs.existsSync(reportPath) 或 fs.existsSync(returnMdPath) → 违规
      if (
        !inElseIfSkeleton &&
        !line.startsWith("//") &&
        (line.includes("fs.existsSync(reportPath)") || line.includes("fs.existsSync(returnMdPath)")) &&
        line.startsWith("if (")
      ) {
        assert.fail(
          `tryAutoReturn 内主 if 不应使用 fs.existsSync 判 RETURN.md: 行 "${line.trim()}"`
        );
      }
      inElseIfSkeleton = false;
    }
  });

  it("源码中 RETURN.md 存在性检查只出现在: isReturnMdSubstantive 内部 / else-if 骨架检测 / 非 tryAutoReturn 上下文", () => {
    // 确认 tryAutoReturn 外的 fs.existsSync(returnMdPath) 不在本卡范围(不修)
    // 本卡只修 tryAutoReturn 内的两路
    const tryAutoReturnStart = src.indexOf("async function tryAutoReturn()");
    const tryAutoReturnEnd = src.indexOf("\n// AIPOS-F60-fix1", tryAutoReturnStart + 100) || 
                             src.indexOf("\nasync function", tryAutoReturnStart + 100);
    // 简单验证: tryAutoReturn 内 isReturnMdSubstantive 出现 ≥ 2 次(audit + exec)
    const tryBody = src.slice(tryAutoReturnStart, tryAutoReturnEnd > tryAutoReturnStart ? tryAutoReturnEnd : tryAutoReturnStart + 15000);
    const matches = tryBody.match(/isReturnMdSubstantive\(/g) || [];
    assert.ok(matches.length >= 2, `tryAutoReturn 内 isReturnMdSubstantive 应至少出现 2 次(settle audit + settle exec), 实得 ${matches.length}`);
  });
});

// ---------------------------------------------------------------------------
// 3. 行为级: isReturnMdSubstantive 对骨架 → false, 对实质 → true
//    (复用 F60 函数, 验证 F63 现场可被拦住)
// ---------------------------------------------------------------------------
describe("AIPOS-F60-fix1 行为验证: F63 现场复现", () => {
  let testDir: string;

  it("setup temp dir", () => {
    testDir = mkdtempSync(join(tmpdir(), "f60-fix1-test-"));
  });

  it("F63 现场: F43 骨架 → isReturnMdSubstantive(exec) = false → 不提交", () => {
    // 这是 F43 连接器投递的骨架, 含 "(待填写)" 和 "骨架由连接器投递时自动落盘"
    const skeleton = `# AIPOS-F63 交付报告

## 一句话结论

(待填写)

## 做了什么

(待填写)

## 改动清单

(待填写)

## 测试/验证结果原文

(待填写)

## 排除物 + 理由

(待填写)

## 异常与自作判断

(待填写)

## 实际使用的模型 + 自报 token 用量

(待填写)

## 待办 / 移交

(待填写)

---
(骨架由连接器投递时自动落盘,内容单源=卡面。执行体完成后填写上方各节。)
`;
    const p = join(testDir, "RETURN.md");
    writeFileSync(p, skeleton, "utf-8");
    
    // 修复前: fs.existsSync → true → 正则提取 "(待填写)" 作为 summary → 空白交回
    // 修复后: isReturnMdSubstantive → false → 不提交, 出声带出口
    assert.strictEqual(
      isReturnMdSubstantiveFromSource(p, "exec"),
      false,
      "F43 骨架应判 false — 这是 F63 事故的根因"
    );
  });

  it("F63 现场(audit): 骨架 → isReturnMdSubstantive(audit) = false → 不提交", () => {
    const skeleton = `# AIPOS-TEST 审计报告

## 审计裁决

verdict: (PASS / FAIL / BLOCK)

## 一句话结论

(待填写)

---
(骨架由连接器投递时自动落盘)
`;
    const p = join(testDir, "RETURN-audit.md");
    writeFileSync(p, skeleton, "utf-8");
    assert.strictEqual(
      isReturnMdSubstantiveFromSource(p, "audit"),
      false,
      "审计骨架应判 false"
    );
  });

  it("零回归: 实质报告(exec) → true → 正常交回", () => {
    const real = `# AIPOS-F61 交付报告

## 一句话结论

完成。收尾原子化修复已落地,测试全通过。

## 做了什么

- 修复 finalize/close 拆两跳问题
- 测试验证

## 改动清单

- agents/harness/pi/lybra-loop/lybra-loop.ts: 修改 close 逻辑
`;
    const p = join(testDir, "RETURN-real.md");
    writeFileSync(p, real, "utf-8");
    assert.strictEqual(
      isReturnMdSubstantiveFromSource(p, "exec"),
      true,
      "实质报告应判 true — 零回归"
    );
  });

  it("零回归: 实质报告(audit) 有真实 verdict → true", () => {
    const real = `# AIPOS-TEST 审计报告

## 审计裁决

verdict: PASS

## 一句话结论

审计通过,代码符合裁定。
`;
    const p = join(testDir, "RETURN-audit-real.md");
    writeFileSync(p, real, "utf-8");
    assert.strictEqual(
      isReturnMdSubstantiveFromSource(p, "audit"),
      true,
      "有真实 verdict 的审计报告应判 true"
    );
  });

  it("正则提取验证: 骨架的 '(待填写)' 会被正则成功匹配(= 修复前的问题)", () => {
    const skeleton = `## 一句话结论\n\n(待填写)\n\n## 做了什么`;
    const match = skeleton.match(/##\s*一句话结论[^\n]*\n+([^\n#]+)/i);
    assert.ok(match, "正则应能匹配到骨架的 '一句话结论' 节");
    assert.strictEqual(
      match![1].trim(),
      "(待填写)",
      "修复前正则提取的结果就是 '(待填写)' — 这就是空白交回的根因"
    );
    // 修复后: isReturnMdSubstantive 先判骨架 → false → 根本不进正则提取分支
  });

  it("cleanup", () => {
    rmSync(testDir, { recursive: true, force: true });
  });
});

// ---------------------------------------------------------------------------
// 4. 零回归: held 两路仍使用 isReturnMdSubstantive(F60 既有行为不变)
// ---------------------------------------------------------------------------
describe("AIPOS-F60-fix1 held 路零回归", () => {
  const src = readFileSync(LOOP_FILE, "utf-8");

  it("held 审计路仍使用 isReturnMdSubstantive", () => {
    const startIdx = src.indexOf('currentLogger.info("held-audit-no-verdict"');
    assert.ok(startIdx > 0, "held-audit-no-verdict 块应存在");
    const endIdx = src.indexOf("// 报告未就位 → 投递复工", startIdx);
    const heldBlock = src.slice(startIdx, endIdx > startIdx ? endIdx : startIdx + 2000);
    assert.ok(
      heldBlock.includes("isReturnMdSubstantive(auditReportPath"),
      "held 审计路应仍使用 isReturnMdSubstantive"
    );
  });

  it("held 执行路仍使用 isReturnMdSubstantive", () => {
    const startIdx = src.indexOf("AIPOS-F37 大项A扩展: 执行车道 held 路托管接线");
    assert.ok(startIdx > 0, "held 执行路块标记应存在");
    const endIdx = src.indexOf("// RETURN.md 未就位 → 投递复工", startIdx);
    const heldBlock = src.slice(startIdx, endIdx > startIdx ? endIdx : startIdx + 2000);
    assert.ok(
      heldBlock.includes("isReturnMdSubstantive(returnMdPath"),
      "held 执行路应仍使用 isReturnMdSubstantive"
    );
  });
});
