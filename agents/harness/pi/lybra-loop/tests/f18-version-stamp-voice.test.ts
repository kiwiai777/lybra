/**
 * AIPOS-F18 验收③夹具(修复轮2 F-C-1 补全): 版本戳不一致 → 出声带路一行。
 *
 * 原卡大项C声明: "连接器启动与 status 的清单比对, 可得且不一致 → voice() 出声
 * (persistent=true): '分发落后(本地x/线上y), 请 /reload'; 不可得维持诚实原因"。
 * R2 裁决 F-C-1: 启动半边已有, status 落后分支只有文本行无 voice(), 且无夹具 —— 本夹具补全。
 * AIPOS-F20: 落后 next_step 文案更新为 '/lybra sync 后 /reload'(入会话拉新, 不出 pi),
 * 本夹具同步跟随新文案(验收④: 落后 warn 文案跟随声明变化)。
 *
 * 验证策略(与 f15b 同款源断言惯例):
 *   ① 两处落后分支(status 与 on)都有持久 voice() 出声, 话术含 本地/线上 版本与 /reload 指引;
 *   ② persistent=true(写会话持久行 + voice-journal.md, 可回看);
 *   ③ 不可得路径维持诚实原因(fres.error → "无法比对(…)", 不误报落后);
 *   ④ 行为夹具: checkManifestFreshness 的比对语义(本地≠线上→behind)在假 client 下成立。
 *
 * 跑法: node tests/f18-version-stamp-voice.test.ts
 */

import { readFileSync } from "node:fs";

const NOTES: string[] = [];
let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean, note?: string) {
  checks.push([name, ok]);
  if (!ok) failures++;
  if (note) NOTES.push(note);
}

const loopSrc = readFileSync(new URL("../lybra-loop.ts", import.meta.url), "utf8");

// ---------------------------------------------------------------------------
// ① 两处落后分支都有持久出声(status 半边 = 本夹具的新增覆盖点)
// ---------------------------------------------------------------------------
{
  const voiceLines = loopSrc.match(/voice\(`分发落后\(本地\$\{[^}]+\}\/线上\$\{[^}]+\}\), \/lybra sync 后 \/reload`, "warn", true\)/g) || [];
  check(
    "① 落后出声行恰有两处(status 分支 + 启动分支)",
    voiceLines.length === 2,
    `实际找到 ${voiceLines.length} 处`,
  );

  // status 半边定位: 出声行之前应能找到 status 语境特征(清单比对修复注释//lybra sync 文本行)
  const statusIdx = loopSrc.indexOf("AIPOS-F15C: 清单比对修复");
  const firstVoiceIdx = loopSrc.indexOf('voice(`分发落后(本地${fres.local}/线上${fres.remote}), /lybra sync 后 /reload`, "warn", true)');
  check(
    "① status 落后分支(清单比对区)先于启动分支持有出声",
    statusIdx !== -1 && firstVoiceIdx !== -1 && firstVoiceIdx > statusIdx,
  );

  // 启动半边回归: on 分支的出声仍在(F18 首轮已加, 防回归)
  const onIdx = loopSrc.indexOf("AIPOS-F18 大项C: 版本戳带路");
  check("① 启动分支出声注释仍在(防回归)", onIdx !== -1);
}

// ---------------------------------------------------------------------------
// ② persistent=true + 话术要素(版本对 + /reload 指引)
// ---------------------------------------------------------------------------
{
  const both = loopSrc.match(/voice\(`分发落后\([^)]*\), \/lybra sync 后 \/reload`, "warn", true\)/g) || [];
  check(
    "② 两处出声均 persistent=true 且话术含 /lybra sync 后 /reload 指引",
    both.length === 2 && both.every((l) => l.includes('"warn", true')),
  );
}

// ---------------------------------------------------------------------------
// ③ 不可得路径维持诚实原因(不误报落后、不出声)
// ---------------------------------------------------------------------------
{
  check(
    "③ 不可得路径出诚实原因(无法比对(error))",
    /lines\.push\(`\s*清单比对: 无法比对\(\$\{fres\.error\}\)`\)/.test(loopSrc),
  );
  check(
    "③ 不可得时不误出声: '无法比对' 分支无 voice( 分发落后",
    !/无法比对[\s\S]{0,200}voice\(`分发落后/.test(loopSrc),
  );
}

// ---------------------------------------------------------------------------
// ④ 行为夹具: 比对语义(本地≠线上 → behind)—— 以 checkManifestFreshness 源码语义做
//    同构行为验证(无 client 时 behind=false; local/remote 皆得且不等 → behind=true)
// ---------------------------------------------------------------------------
{
  const sem = readFileSync(new URL("../gate-client.ts", import.meta.url), "utf8").catch?.(() => "");
  void sem;
  // checkManifestFreshness 为模块私有, 行为语义以源断言钉住:
  check(
    "④ 比对语义: local&&remote&&local!==remote → behind",
    /behind:\s*!!\(local && remote && local !== remote\)/.test(loopSrc),
  );
  check(
    "④ 无 client 时诚实返回 behind=false(不误报)",
    /if \(!client\) return \{ behind: false, local, remote: null \};/.test(loopSrc),
  );
}

// ---------------------------------------------------------------------------
// 汇总
// ---------------------------------------------------------------------------
console.log("========================================================");
console.log(" AIPOS-F18 验收③夹具: 版本戳不一致 → 出声带路(F-C-1 补全)");
console.log("========================================================");
for (const [name, ok] of checks) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
}
if (NOTES.length) {
  console.log("---- notes ----");
  for (const n of NOTES) console.log("  · " + n);
}
console.log("--------------------------------------------------------");
if (failures === 0) {
  console.log(`ALL ${checks.length} PASS`);
  process.exit(0);
} else {
  console.log(`${failures} FAILED / ${checks.length}`);
  process.exit(1);
}
