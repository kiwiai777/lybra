/**
 * AIPOS-F11 大项B/C 专项断言测试 —— stderr 透传 + 运行态复位单源
 *
 * 大项B: finalize 子进程失败时错误输出/日志 detail 含 stderr 尾行(纯管道, 不改级别判断)。
 * 大项C: currentTaskId/currentWorktreePath 的清零收拢为一个复位函数, 凡 settle 判定成立
 *   (含全部歇手分支) 与 loop-on 启动皆调用; 禁在各分支各写一份清零。
 *
 * 跑法:`node tests/f11-runtime-reset.test.ts`
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

const sourceFile = join(import.meta.dirname || ".", "../lybra-loop.ts");
const source = readFileSync(sourceFile, "utf8");
const lines = source.split("\n");

// ===== 大项B: stderr 透传 =====
{
  check("定义 subprocessFailureTail 函数", /function subprocessFailureTail\(/.test(source));
  check("subprocessFailureTail 覆盖 stderr + stdout", source.includes('[stderr, stdout]') || (source.includes('"stderr"') && source.includes('"stdout"')));

  // auto-finalize 失败 catch 里必须调用 subprocessFailureTail 并写入日志 detail
  const catchIdx = lines.findIndex((l) => l.includes("sweep finalize 失败"));
  check("auto-finalize 失败 catch 存在", catchIdx >= 0);
  if (catchIdx >= 0) {
    const block = lines.slice(catchIdx - 20, catchIdx + 10).join("\n");
    check("catch 内调用 subprocessFailureTail", block.includes("subprocessFailureTail(e"));
    check("日志 detail 含 failure_tail", block.includes("failure_tail"));
    check("错误输出拼入尾行块", block.includes("tailBlock"));
  }
}

// ===== 大项C: 运行态复位单源 =====
{
  check("定义 resetRuntimeState 函数", /function resetRuntimeState\(/.test(source));

  // 歇手分支(审计已裁 / 已交回)与 auto-return 成功路径、loop-on 启动均调用
  check(
    "审计已裁歇手分支调用复位",
    source.includes('resetRuntimeState("审计卡已裁(verdict 已落库)歇手")'),
  );
  check(
    "已交回歇手分支调用复位",
    source.includes('resetRuntimeState("已交回(returns 已有记录)歇手")'),
  );
  check(
    "auto-return 成功路径调用复位",
    source.includes('resetRuntimeState("auto-return 成功")'),
  );
  check(
    "loop-on 启动调用复位",
    source.includes('resetRuntimeState("loop-on 启动复位")'),
  );

  // 禁在各分支各写一份清零: currentTaskId = null 只能出现在 resetRuntimeState 函数体内
  const nullLines: string[] = [];
  let inResetFn = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.includes("function resetRuntimeState(")) inResetFn = true;
    if (inResetFn && /^\}/.test(line)) inResetFn = false;
    if (line.includes("currentTaskId = null")) {
      if (!inResetFn) nullLines.push(`${i + 1}: ${line.trim()}`);
    }
  }
  check(
    "currentTaskId=null 只活在 resetRuntimeState 内(单源)",
    nullLines.length === 0,
  );
  if (nullLines.length > 0) {
    NOTES.push(`越界清零: ${nullLines.join("; ")}`);
  }
}

// ===== 输出 =====
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
for (const n of NOTES) console.log(`NOTE  ${n}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
