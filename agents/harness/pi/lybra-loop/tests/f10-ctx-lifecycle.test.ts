/**
 * AIPOS-F10 ctx 生命周期修真 —— 三病象回归夹具。
 *
 * 三病象(均实弹):
 *  ①2026-08-19 C2R 冷启动:`ctx.newSession is not a function`(定时器持 stale ctx)
 *  ②2026-08-19 /reload 后定时 tick:`extension ctx is stale after session replacement`
 *  ③2026-08-20 复工网首弹:`ctx.reply is not a function`(session_start 传 event 非 ctx)
 *
 * 验证策略:源码断言(无法 headless 跑真 pi session,但可锁定代码结构不变)。
 *
 * 跑法:`node tests/f10-ctx-lifecycle.test.ts`。
 */

import { readFileSync } from "node:fs";

const NOTES: string[] = [];
let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

const loopSrc = readFileSync(new URL("../lybra-loop.ts", import.meta.url), "utf8");

// ---------------------------------------------------------------------------
// 夹具①:冷启动 newSession — 定时器不捕获 ctx
// 病根:scheduleNextTick 闭包捕获 ctx → 定时器触发时 ctx 已 stale → newSession 摔
// 修复:scheduleNextTick 不接收 ctx 参数;doTick 从 liveCtx 取
// ---------------------------------------------------------------------------
{
  // 断言:scheduleNextTick 签名不含 ctx 参数
  const sigMatch = loopSrc.match(/function scheduleNextTick\(([^)]*)\)/);
  const sigArgs = sigMatch ? sigMatch[1] : "";
  check("病象①:scheduleNextTick 签名不含 ctx 参数", !sigArgs.includes("ctx"));
  check("病象①:scheduleNextTick 签名不含 pi 参数", !sigArgs.includes("pi"));

  // 断言:doTick 签名不含 ctx 参数
  const doTickMatch = loopSrc.match(/async function doTick\(([^)]*)\)/);
  const doTickArgs = doTickMatch ? doTickMatch[1] : "";
  check("病象①:doTick 签名不含 ctx 参数", !doTickArgs.includes("ctx"));

  // 断言:存在 liveCtx 模块级变量
  check("病象①:存在 liveCtx 模块级变量", loopSrc.includes("let liveCtx:"));
  check("病象①:存在 livePi 模块级变量", loopSrc.includes("let livePi:"));

  // 断言:newSession 使用 liveCtx 而非闭包 ctx
  check("病象①:newSession 调用使用 liveCtx", loopSrc.includes("liveCtx.newSession("));
}

// ---------------------------------------------------------------------------
// 夹具②:/reload 后循环继续 tick 不 stale
// 病根:session_shutdown 对 reload 不清定时器 → 旧定时器持 stale ctx/pi
// 修复:reload 也清定时器;session_start 用新 ctx 重调度
// ---------------------------------------------------------------------------
{
  // 断言:session_shutdown 中 reload 路径包含 clearTimer
  const shutdownStart = loopSrc.indexOf('pi.on("session_shutdown"');
  const shutdownEnd = loopSrc.indexOf('pi.on("session_start"', shutdownStart);
  const shutdownBlock = loopSrc.slice(shutdownStart, shutdownEnd > shutdownStart ? shutdownEnd : shutdownStart + 2000);

  // reload 路径:先 clearTimer 再 return
  const reloadIdx = shutdownBlock.indexOf('"reload"');
  const clearTimerBeforeReload = shutdownBlock.slice(0, reloadIdx + 200).includes("clearTimer()");
  check("病象②:session_shutdown reload 路径含 clearTimer", clearTimerBeforeReload);

  // 断言:session_start 捕获第二参数 ctx
  const sessionStartMatch = loopSrc.match(/pi\.on\("session_start",\s*async\s*\(([^)]*)\)/);
  const sessionStartArgs = sessionStartMatch ? sessionStartMatch[1] : "";
  check("病象②:session_start 捕获 ctx 第二参数", sessionStartArgs.split(",").length >= 2);

  // 断言:session_start 内刷新 liveCtx
  const sessionStartStart = loopSrc.indexOf('pi.on("session_start"');
  const sessionStartEnd = loopSrc.indexOf("// ---", sessionStartStart + 100);
  const sessionStartBlock = loopSrc.slice(sessionStartStart, sessionStartEnd > sessionStartStart ? sessionStartEnd : sessionStartStart + 1500);
  check("病象②:session_start 内刷新 liveCtx", sessionStartBlock.includes("liveCtx = ctx"));

  // 断言:export default function 入口设置 livePi
  const exportStart = loopSrc.indexOf("export default function");
  const exportBlock = loopSrc.slice(exportStart, exportStart + 500);
  check("病象②:export default 入口设置 livePi", exportBlock.includes("livePi = pi"));
}

// ---------------------------------------------------------------------------
// 夹具③:复工 ctx.reply 摔 → 改用 sendUserMessage
// 病根:session_start 传 event as any 当 ctx → ctx.reply 不存在
// 修复:①session_start 取第二参数 ctx ②ctx.reply 改为 sendUserMessage ③能力缺失降级出声
// ---------------------------------------------------------------------------
{
  // 断言:源码不含 ctx.reply 调用(reply 不是 pi API 方法)
  // 注意:注释中的 ctx.reply 不算(如"不用 ctx.reply")
  const lines = loopSrc.split("\n");
  const replyCalls = lines.filter((l) => {
    const trimmed = l.trim();
    // 跳过注释行
    if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return false;
    return /\.reply\(/.test(l);
  });
  check("病象③:源码不含 .reply() 调用(非注释行)", replyCalls.length === 0);
  if (replyCalls.length > 0) {
    NOTES.push(`发现 .reply() 调用:\n${replyCalls.join("\n")}`);
  }

  // 断言:复工路径使用 sendUserMessage
  check("病象③:复工路径使用 sendUserMessage", loopSrc.includes("liveCtx.sendUserMessage("));

  // 断言:sendUserMessage 调用有 try-catch 包裹(降级出声,禁裸抛)
  // 检查 sendUserMessage 附近是否有 catch
  const sendIdx = loopSrc.indexOf("liveCtx.sendUserMessage(");
  if (sendIdx > 0) {
    const surrounding = loopSrc.slice(Math.max(0, sendIdx - 200), sendIdx + 500);
    check("病象③:sendUserMessage 有 try-catch 包裹(降级出声)", surrounding.includes("try {") && surrounding.includes("catch (sendErr)"));
  } else {
    check("病象③:sendUserMessage 有 try-catch 包裹(降级出声)", false);
  }

  // 断言:session_start 不再用 event as any(排除注释行)
  const eventAsAnyLines = lines.filter((l) => {
    const trimmed = l.trim();
    if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return false;
    return l.includes("event as any");
  });
  check("病象③:session_start 不用 event as any(非注释行)", eventAsAnyLines.length === 0);
}

// ---------------------------------------------------------------------------
// 附加:stopLoop / tryAutoReturn / tryAutoFinalizeOnPassVerdict 签名不含 ctx
// ---------------------------------------------------------------------------
{
  check("stopLoop 签名不含 ctx", /function stopLoop\(\s*\n?\s*reason/.test(loopSrc));
  check("tryAutoReturn 签名不含 ctx", /async function tryAutoReturn\(\)/.test(loopSrc));
  check("tryAutoFinalizeOnPassVerdict 签名不含 ctx", /async function tryAutoFinalizeOnPassVerdict\(\)/.test(loopSrc));
  check("getSessionId 签名不含 ctx", /function getSessionId\(\)/.test(loopSrc));
}

// ---------------------------------------------------------------------------
// 汇总
// ---------------------------------------------------------------------------
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
if (NOTES.length) {
  console.log("\n--- NOTES ---");
  for (const n of NOTES) console.log(`  • ${n}`);
}
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
