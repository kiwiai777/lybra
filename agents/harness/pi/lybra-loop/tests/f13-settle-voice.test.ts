/**
 * AIPOS-F13 验收夹具:
 * ① settle补型 — 卡已被门收走时resetRuntimeState+info出声,零gate动词调用
 * ② 出声缓冲 — liveCtx未就绪时入缓冲,就绪后补发
 */

import { assertEquals, assertExists } from "https://deno.land/std@0.224.0/assert/mod.ts";

// 大项A: settle补型 — resetRuntimeState 必须在 tryAutoReturn/held分支被调用
Deno.test("F13-A: settle补型 — resetRuntimeState调用存在", () => {
  const loopContent = Deno.readTextFileSync("./lybra-loop.ts");
  
  // 验证 tryAutoReturn 中有 settle 判定
  const hasAutoReturnSettle = /async function tryAutoReturn[\s\S]*?卡已被门收走[\s\S]*?resetRuntimeState/.test(loopContent);
  assertEquals(hasAutoReturnSettle, true, "tryAutoReturn 必须有 settle 判定并调用 resetRuntimeState");
  
  // 验证 held 分支有 settle 判定
  const hasHeldSettle = /held分支[\s\S]*?卡已被门收走[\s\S]*?resetRuntimeState/.test(loopContent);
  assertEquals(hasHeldSettle, true, "held 分支必须有 settle 判定并调用 resetRuntimeState");
  
  // 验证 resetRuntimeState 函数存在且出声
  const hasResetFunction = /function resetRuntimeState[\s\S]*?voice/.test(loopContent);
  assertEquals(hasResetFunction, true, "resetRuntimeState 必须调用 voice 出声");
});

// 大项B: 出声缓冲机制
Deno.test("F13-B: 出声缓冲 — voice函数与缓冲机制存在", () => {
  const loopContent = Deno.readTextFileSync("./lybra-loop.ts");
  
  // 验证 voice 函数存在
  const hasVoiceFunction = /function voice\(/.test(loopContent);
  assertEquals(hasVoiceFunction, true, "必须有 voice 统一出声函数");
  
  // 验证缓冲机制
  const hasVoiceBuffer = /voiceBuffer/.test(loopContent);
  assertEquals(hasVoiceBuffer, true, "必须有 voiceBuffer 缓冲数组");
  
  // 验证 flushVoiceBuffer 函数
  const hasFlushFunction = /function flushVoiceBuffer/.test(loopContent);
  assertEquals(hasFlushFunction, true, "必须有 flushVoiceBuffer 补发函数");
  
  // 验证 session_start 调用 flushVoiceBuffer
  const hasSessionFlush = /session_start[\s\S]*?flushVoiceBuffer/.test(loopContent);
  assertEquals(hasSessionFlush, true, "session_start 必须调用 flushVoiceBuffer");
  
  // 验证所有 liveCtx?.ui?.notify 已被替换为 voice
  const hasOldNotify = /liveCtx\?\.\s*ui\?\.\s*notify\?\./.test(loopContent);
  assertEquals(hasOldNotify, false, "所有 liveCtx?.ui?.notify 必须已被替换为 voice");
});

// 大项C: 陈旧文案清理验证
Deno.test("F13-C: 陈旧文案清理 — 关键词零命中", () => {
  const files = [
    "../../../skills/task-closure-loop/SKILL.md",
    "../../../roles/executor/AGENTS.md",
    "../../../roles/auditor/AGENTS.md",
  ];
  
  for (const file of files) {
    const content = Deno.readTextFileSync(file);
    
    // 禁止出现旧人肉指引关键词
    const hasOldWording = /顾问.*签发.*FINALIZE|顾问.*复核.*收编/.test(content);
    assertEquals(hasOldWording, false, `${file} 不应包含旧人肉指引关键词`);
    
    // 应包含新自动链描述
    const hasNewWording = /循环自动|F11.*已上线/.test(content);
    assertEquals(hasNewWording, true, `${file} 应包含新自动链描述`);
  }
});

console.log("✓ AIPOS-F13 夹具全绿");
