/**
 * AIPOS-F15 验收夹具:出声可证 — 上屏尝试必留痕 + 屏幕句柄修真 + 单出口
 *
 * 验证策略:源码断言(无法 headless 跑真 pi session,但可锁定代码结构不变)。
 *
 * 大项A: voice() 每次调用必落 voice-attempt 日志(outcome+level+text_head)
 *   - outcome ∈ direct / buffered / flushed / dropped / no-handle
 *   - text_head = 文案前 40 字
 *
 * 大项B: 句柄修真 — liveCtx 统一在 session_start/command/agent_settled 入口刷新
 *   - 所有上屏走 voice() 单出口,禁另造第二条出声路(liveCtx.ui.notify 只出现在 voice/flush 内)
 *   - session_start 刷新 ctx 后调 flushVoiceBuffer
 *
 * 跑法: node tests/f15-voice-trace.test.ts (或 deno run)
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
// 大项A: voice-attempt 日志 — 每次 voice() 调用必落 outcome
// ---------------------------------------------------------------------------
{
  // 断言: voice 函数内存在 voice-attempt 日志
  const voiceFnMatch = loopSrc.match(/function voice\([\s\S]*?\n\}/);
  const voiceFn = voiceFnMatch ? voiceFnMatch[0] : "";
  check("大项A: voice() 内含 voice-attempt 日志", voiceFn.includes("voice-attempt"));

  // 断言: voice() 内 direct 路径有 outcome: "direct"
  check("大项A: voice() 含 outcome: \"direct\" 路径", /outcome:\s*"direct"/.test(loopSrc));

  // 断言: voice() 内 buffered 路径有 outcome: "buffered"
  check("大项A: voice() 含 outcome: \"buffered\" 路径", /outcome:\s*"buffered"/.test(loopSrc));

  // 断言: voice() 内 no-handle 路径有 outcome: "no-handle"
  check("大项A: voice() 含 outcome: \"no-handle\" 路径(句柄作废)", /outcome:\s*"no-handle"/.test(loopSrc));

  // 断言: voice() 内 dropped 路径有 outcome: "dropped"
  check("大项A: voice() 含 outcome: \"dropped\" 路径(缓冲超限)", /outcome:\s*"dropped"/.test(loopSrc));

  // 断言: flushVoiceBuffer 内含 voice-attempt 日志
  const flushFnMatch = loopSrc.match(/function flushVoiceBuffer\([\s\S]*?\n\}/);
  const flushFn = flushFnMatch ? flushFnMatch[0] : "";
  check("大项A: flushVoiceBuffer() 内含 voice-attempt 日志", flushFn.includes("voice-attempt"));

  // 断言: flushVoiceBuffer 内含 outcome: "flushed"
  check("大项A: flushVoiceBuffer() 含 outcome: \"flushed\" 路径", /outcome:\s*"flushed"/.test(loopSrc));

  // 断言: voice-attempt 日志含 text_head 字段(前 40 字)
  check("大项A: voice-attempt 含 text_head 字段", /text_head:/.test(loopSrc));

  // 断言: text_head 截取 40 字
  check("大项A: text_head 截取 40 字", /\.slice\(0,\s*40\)/.test(loopSrc));
}

// ---------------------------------------------------------------------------
// 大项B: 单出口 — liveCtx.ui.notify 只出现在 voice() 和 flushVoiceBuffer() 内
// ---------------------------------------------------------------------------
{
  // 找出所有 liveCtx.ui.notify 调用行(非注释)
  const lines = loopSrc.split("\n");
  const directCalls: { lineNum: number; text: string }[] = [];
  let inVoiceFn = false;
  let inFlushFn = false;
  let braceDepth = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // 跳过注释行
    if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) continue;

    // 跟踪 voice() 和 flushVoiceBuffer() 函数体
    if (/function voice\(/.test(line)) { inVoiceFn = true; braceDepth = 0; }
    if (/function flushVoiceBuffer\(/.test(line)) { inFlushFn = true; braceDepth = 0; }

    if (inVoiceFn || inFlushFn) {
      braceDepth += (line.match(/{/g) || []).length;
      braceDepth -= (line.match(/}/g) || []).length;
      if (braceDepth <= 0 && line.includes("}")) {
        inVoiceFn = false;
        inFlushFn = false;
      }
    }

    // 检查 liveCtx.ui.notify 调用
    if (/liveCtx\.ui\.notify\(/.test(line) || /liveCtx\?\.ui\?\.notify/.test(line)) {
      if (!inVoiceFn && !inFlushFn) {
        directCalls.push({ lineNum: i + 1, text: trimmed });
      }
    }
  }

  check("大项B: liveCtx.ui.notify 只出现在 voice()/flushVoiceBuffer() 内(单出口)", directCalls.length === 0);
  if (directCalls.length > 0) {
    NOTES.push(`发现直接 liveCtx.ui.notify 调用(绕过 voice()):\n${directCalls.map(d => `  L${d.lineNum}: ${d.text}`).join("\n")}`);
  }
}

// ---------------------------------------------------------------------------
// 大项B: 句柄刷新 — session_start 刷新 liveCtx 后调 flushVoiceBuffer
// ---------------------------------------------------------------------------
{
  // 断言: session_start 内第一行刷新 liveCtx
  const sessionStartIdx = loopSrc.indexOf('pi.on("session_start"');
  const sessionStartBlock = loopSrc.slice(sessionStartIdx, sessionStartIdx + 500);
  check("大项B: session_start 首行刷新 liveCtx", /session_start[\s\S]{0,200}liveCtx\s*=\s*ctx/.test(loopSrc));

  // 断言: session_start 调 flushVoiceBuffer
  check("大项B: session_start 调 flushVoiceBuffer", sessionStartBlock.includes("flushVoiceBuffer()"));

  // 断言: agent_settled 刷新 liveCtx
  const agentSettledIdx = loopSrc.indexOf('pi.on("agent_settled"');
  const agentSettledBlock = loopSrc.slice(agentSettledIdx, agentSettledIdx + 300);
  check("大项B: agent_settled 刷新 liveCtx", agentSettledBlock.includes("liveCtx = ctx"));

  // 断言: 命令入口刷新 liveCtx
  check("大项B: 命令 handler 刷新 liveCtx", /handler:\s*async\s*\([^)]*\)\s*=>\s*{[\s\S]{0,100}liveCtx\s*=\s*ctx/.test(loopSrc));
}

// ---------------------------------------------------------------------------
// 附加: 旧日志键名已清理(voice-buffered/voice-notify-failed/voice-flush-item-failed)
// ---------------------------------------------------------------------------
{
  // voice-buffered → 被 voice-attempt {outcome:"buffered"} 替代
  // 但 voice-buffered 可能在注释中出现,只查非注释行
  const lines = loopSrc.split("\n");
  const oldKeys = lines.filter((l) => {
    const trimmed = l.trim();
    if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return false;
    return /"voice-buffered"|"voice-notify-failed"|"voice-flush-item-failed"|"voice-flush-skip-no-ctx"|"voice-buffer-overflow"/.test(l);
  });
  check("附加: 旧日志键名已清理(voice-buffered 等)", oldKeys.length === 0);
  if (oldKeys.length > 0) {
    NOTES.push(`旧日志键名仍在:\n${oldKeys.join("\n")}`);
  }
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
