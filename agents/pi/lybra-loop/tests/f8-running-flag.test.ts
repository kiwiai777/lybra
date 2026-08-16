/**
 * F-EXT001-8 专项断言测试 —— running 标志复位 + 空 catch 清除 + 双保险机制
 *
 * 验收断言(LYBRA-EXT-001-FIX4 卡):
 *  1. release 路径: loopState.running 在 newSession 之前复位(任何路径含 stale 异常下可达)
 *  2. 空 catch 全部清除:代码中 `\.catch\(\(\) => \{\}\)` grep 零命中
 *  3. 双保险机制: session_start 在 expectingSwap 语境下也续跑(防 agent_settled 单点失效)
 *
 * 跑法:`node tests/f8-running-flag.test.ts`
 */

import { readFileSync } from "fs";
import { join } from "path";

const NOTES: string[] = [];
let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

// 读取源码
const sourceFile = join(import.meta.dirname || ".", "../lybra-loop.ts");
const source = readFileSync(sourceFile, "utf8");
const lines = source.split("\n");

// ===== 断言 1: release 路径 running 复位在 newSession 之前 =====
{
  // 找 outcome.kind === "release" 块的开始行
  const releaseBlockStart = lines.findIndex((l) => l.includes('if (outcome.kind === "release")'));
  check("找到 release 块", releaseBlockStart >= 0);

  if (releaseBlockStart >= 0) {
    // 找 newSession 调用行
    let newSessionLine = -1;
    let runningResetLine = -1;
    for (let i = releaseBlockStart; i < lines.length; i++) {
      if (lines[i].includes("await ctx.newSession(") || lines[i].includes("await ctx.newSession({")) {
        newSessionLine = i;
        break;
      }
    }
    // 找 running = false 在 release 块内的第一次出现
    for (let i = releaseBlockStart; i < lines.length; i++) {
      if (lines[i].includes("loopState.running = false")) {
        runningResetLine = i;
        break;
      }
      // 若遇到下一个 if (outcome.kind,说明已出 release 块
      if (i > releaseBlockStart && lines[i].match(/^\s*if \(outcome\.kind/)) break;
    }

    check("release 块找到 newSession 调用", newSessionLine >= 0);
    check("release 块找到 running=false", runningResetLine >= 0);
    if (newSessionLine >= 0 && runningResetLine >= 0) {
      check(
        "running 复位在 newSession 之前(确保 stale 异常时可达)",
        runningResetLine < newSessionLine,
      );
      NOTES.push(
        `running=false 在第 ${runningResetLine + 1} 行, newSession 在第 ${newSessionLine + 1} 行`,
      );
    }

    // 验证 newSession 被 try-catch 包裹(捕获 stale 异常不静默吞)
    let tryCatchFound = false;
    for (let i = runningResetLine; i < newSessionLine + 10 && i < lines.length; i++) {
      if (lines[i].includes("try {")) {
        tryCatchFound = true;
        break;
      }
    }
    check("release 路径 newSession 有 try-catch 包裹", tryCatchFound);

    // 验证 catch 块落日志(不是空 catch)
    let catchLogsError = false;
    for (let i = newSessionLine; i < newSessionLine + 20 && i < lines.length; i++) {
      if (lines[i].includes("} catch (e)") || lines[i].includes("} catch(e)")) {
        // 找 catch 块内是否有 logger.warn
        for (let j = i; j < i + 10 && j < lines.length; j++) {
          if (lines[j].includes("logger.warn") || lines[j].includes("currentLogger.warn")) {
            catchLogsError = true;
            break;
          }
        }
        break;
      }
    }
    check("release newSession catch 块落日志(非静默吞错)", catchLogsError);
  }
}

// ===== 断言 2: 空 catch 全部清除 =====
{
  // 搜索 `.catch(() => {})` 或 `.catch(() => { })` 等空 catch 模式
  const emptyCatchPattern = /\.catch\s*\(\s*\(\)\s*=>\s*\{\s*\}\s*\)/g;
  const emptyCatchMatches = source.match(emptyCatchPattern);
  check("代码中不存在空 catch 模式", !emptyCatchMatches || emptyCatchMatches.length === 0);
  if (emptyCatchMatches && emptyCatchMatches.length > 0) {
    NOTES.push(`找到 ${emptyCatchMatches.length} 处空 catch:${emptyCatchMatches.join(", ")}`);
  }

  // 验证所有 .catch 都有日志记录(逐处取后窗检查, 兼容多行 catch 体与模板字符串)
  const catchCalls = source.match(/\.catch\s*\(/g);
  if (catchCalls) {
    let allCatchHaveLogging = true;
    let idx = 0;
    while ((idx = source.indexOf(".catch(", idx)) !== -1) {
      const window = source.slice(idx, idx + 400);
      // 豁免非关键的"失败即返回空数组"回退(启动横幅取队列数, 失败不阻断)
      if (window.startsWith(".catch(() => [])")) {
        idx += 7;
        continue;
      }
      if (!window.includes("logger") && !window.includes("currentLogger")) {
        allCatchHaveLogging = false;
        NOTES.push(`发现 catch 块未落日志:${window.slice(0, 100).replace(/\n/g, " ")}`);
      }
      idx += 7;
    }
    check("所有 catch 块都有日志记录", allCatchHaveLogging);
  }
}

// ===== 断言 3: 双保险机制 - session_start 处理 expectingSwap =====
{
  // 找 session_start 事件 handler
  const sessionStartIdx = lines.findIndex((l) => l.includes('pi.on("session_start"'));
  check("找到 session_start handler", sessionStartIdx >= 0);

  if (sessionStartIdx >= 0) {
    // 在 handler 内找条件判断,应该包含 expectingSwap
    let expectingSwapCheck = false;
    let reloadCheck = false;
    for (let i = sessionStartIdx; i < sessionStartIdx + 20 && i < lines.length; i++) {
      const line = lines[i];
      if (line.includes("expectingSwap")) expectingSwapCheck = true;
      if (line.includes('event.reason === "reload"')) reloadCheck = true;
    }
    check("session_start 检查 expectingSwap(双保险)", expectingSwapCheck);
    check("session_start 保留 reload 处理", reloadCheck);

    // 验证双保险:条件应该是 OR 组合(reload 或 expectingSwap)
    let orCondition = false;
    for (let i = sessionStartIdx; i < sessionStartIdx + 20 && i < lines.length; i++) {
      const line = lines[i];
      if (
        (line.includes("reload") || line.includes("expectingSwap")) &&
        (line.includes("||") || line.includes("&&"))
      ) {
        if (line.includes("||")) orCondition = true;
      }
    }
    check("session_start 双保险用 OR 连接(reload 或 expectingSwap)", orCondition);

    // 验证 expectingSwap flag 被复位
    let expectingSwapReset = false;
    for (let i = sessionStartIdx; i < sessionStartIdx + 25 && i < lines.length; i++) {
      if (lines[i].includes("expectingSwap = false")) {
        expectingSwapReset = true;
        break;
      }
    }
    check("session_start 复位 expectingSwap flag(防重复触发)", expectingSwapReset);

    // 验证有防重入检查(loopState.running)
    let reentrantGuard = false;
    for (let i = sessionStartIdx; i < sessionStartIdx + 25 && i < lines.length; i++) {
      if (lines[i].includes("loopState.running")) {
        reentrantGuard = true;
        break;
      }
    }
    check("session_start 有防重入检查(loopState.running)", reentrantGuard);
  }
}

// ===== 断言 4: agent_settled 续跑逻辑保留 =====
{
  const agentSettledIdx = lines.findIndex((l) => l.includes('pi.on("agent_settled"'));
  check("找到 agent_settled handler", agentSettledIdx >= 0);

  if (agentSettledIdx >= 0) {
    let hasRunningCheck = false;
    let hasDoTickCall = false;
    for (let i = agentSettledIdx; i < lines.length; i++) {
      if (i > agentSettledIdx && lines[i].includes('pi.on("session_shutdown"')) break;
      if (lines[i].includes("loopState.running")) hasRunningCheck = true;
      if (lines[i].includes("doTick(")) hasDoTickCall = true;
    }
    check("agent_settled 保留防重入检查", hasRunningCheck);
    check("agent_settled 调用 doTick 续跑", hasDoTickCall);
  }
}

// ===== 断言 5: wait 路径 running 复位 =====
{
  // wait 路径应在 scheduleNextTick 之前复位 running
  const waitPollIdx = lines.findIndex((l) => l.includes('currentLogger.info("wait-poll"'));
  check("找到 wait-poll 日志点", waitPollIdx >= 0);

  if (waitPollIdx >= 0) {
    let scheduleIdx = -1;
    let runningResetIdx = -1;
    // 只搜索到 catch 或 finally 之前（不跨越块边界）
    for (let i = waitPollIdx; i < waitPollIdx + 10 && i < lines.length; i++) {
      const line = lines[i];
      // 遇到 catch 或 finally 停止搜索
      if (line.includes("} catch") || line.includes("} finally")) break;
      if (line.includes("scheduleNextTick")) scheduleIdx = i;
      if (line.includes("loopState.running = false")) runningResetIdx = i;
    }
    if (scheduleIdx >= 0 && runningResetIdx >= 0) {
      check("wait 路径 running 在 scheduleNextTick 前复位", runningResetIdx < scheduleIdx);
      NOTES.push(
        `wait 路径: running=false 在第 ${runningResetIdx + 1} 行, scheduleNextTick 在第 ${scheduleIdx + 1} 行`,
      );
    } else {
      check("wait 路径 running 在 scheduleNextTick 前复位", false);
      if (scheduleIdx < 0) NOTES.push("wait 路径未找到 scheduleNextTick");
      if (runningResetIdx < 0) NOTES.push("wait 路径未找到 running=false");
    }
  }
}

// ===== 断言 6: finally 块保底兜其他路径 =====
{
  const finallyIdx = lines.findIndex((l) => l.trim() === "} finally {");
  check("doTick 有 finally 块", finallyIdx >= 0);
  if (finallyIdx >= 0) {
    let finallyHasReset = false;
    for (let i = finallyIdx; i < finallyIdx + 5 && i < lines.length; i++) {
      if (lines[i].includes("loopState.running = false")) {
        finallyHasReset = true;
        break;
      }
    }
    check("finally 块保底复位 running", finallyHasReset);
  }
}

// ===== 补充:F-8 相关注释存在性检查 =====
{
  const f8CommentCount = (source.match(/F-EXT001-8\(FIX4\)/g) || []).length;
  check("代码含 F-EXT001-8(FIX4) 修复注释(至少 3 处)", f8CommentCount >= 3);
  NOTES.push(`F-EXT001-8(FIX4) 注释出现 ${f8CommentCount} 次`);
}

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
if (NOTES.length) {
  console.log("\n--- NOTES ---");
  for (const n of NOTES) console.log(`  • ${n}`);
}
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
