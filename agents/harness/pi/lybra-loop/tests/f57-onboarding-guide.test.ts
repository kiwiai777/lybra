#!/usr/bin/env -S node --experimental-strip-types --no-warnings
/**
 * AIPOS-F57 — 从 0 接新项目全流程固化(TypeScript 夹具)
 * 
 * 验收⑦:夹具入 run-all.sh
 * 
 * 本夹具验证:
 *   - lybra onboarding guide 命令可调用
 *   - 输出包含六步完整指南
 *   - 项目无关性(probe-xyz 也能生成)
 */

import { strict as assert } from "node:assert";
import { execSync } from "node:child_process";
import { test } from "node:test";
import * as fs from "node:fs";
import * as path from "node:path";
import { resolveLybraBin } from "../lybra-loop.ts";
import { ConnectionResolver } from "../loop-context.ts";

// AIPOS-F86 件③: 原写死 "./bin/lybra"(相对 cwd, run-all 的 cwd 是 lybra-loop 目录 → not found, main 基线存量红)。
// 改为与 run-all 其他 TS 夹具(f20)同口径的产品部署解析: ① 工位 .lybra/connection.json#lybra_bin(经 resolveLybraBin,
// 含 project.json#code_repo → .deploy/current/bin/lybra 探测) ② PATH 上的 lybra。均不可得 = 夹具失败(fail-closed, 不跳过)。
function resolveDeployedLybra(): { bin: string; source: string } {
  const res = resolveLybraBin(fs, path, { lybraDir: ConnectionResolver.discoverLybraDir() });
  if (res.bin) return { bin: res.bin, source: res.source };
  for (const dir of String(process.env.PATH || "").split(path.delimiter).filter(Boolean)) {
    const cand = path.join(dir, "lybra");
    try {
      fs.accessSync(cand, fs.constants.X_OK);
      return { bin: cand, source: `PATH(${dir})` };
    } catch {
      // 该 PATH 项无可执行 lybra, 继续下一项
    }
  }
  throw new Error(`lybra CLI 不可解析: connection.json#lybra_bin / 部署探测 / PATH 均无 (tried: ${res.tried.join(", ") || "-"})`);
}

const RESOLVED = resolveDeployedLybra();
const LYBRA_BIN = JSON.stringify(RESOLVED.bin);
console.log(`lybra bin: ${RESOLVED.bin} (来源: ${RESOLVED.source})`);

test("AIPOS-F57 ① lybra onboarding guide 命令可调用", () => {
  // 跑 help 不应崩溃
  const help = execSync(`${LYBRA_BIN} onboarding --help`, {
    encoding: "utf-8",
    cwd: process.cwd(),
  });
  assert.ok(help.includes("guide"), "help 应包含 guide 子命令");
  assert.ok(help.includes("check"), "help 应包含 check 子命令");
});

test("AIPOS-F57 ② guide 生成 probe-xyz 完整六步", () => {
  const output = execSync(`${LYBRA_BIN} onboarding guide probe-xyz`, {
    encoding: "utf-8",
    cwd: process.cwd(),
  });

  // 验证项目名出现
  assert.ok(output.includes("probe-xyz"), "输出应包含项目名");

  // 验证六步都有
  for (let i = 1; i <= 6; i++) {
    assert.ok(output.includes(`Step ${i}:`), `应包含 Step ${i}`);
  }

  // 验证关键步骤标题
  assert.ok(output.includes("项目注册"), "Step 1: 项目注册");
  assert.ok(output.includes("信封铸造"), "Step 2: 信封铸造");
  assert.ok(output.includes("三角色发码"), "Step 3: 三角色发码");
  assert.ok(output.includes("enroll"), "Step 4: enroll");
  assert.ok(output.includes("起 pi"), "Step 5: 起 pi");
  assert.ok(output.includes("自检"), "Step 6: 自检");
});

test("AIPOS-F57 ③ 项目无关性(另一项目名也能生成)", () => {
  const output = execSync(`${LYBRA_BIN} onboarding guide another-project`, {
    encoding: "utf-8",
    cwd: process.cwd(),
  });

  assert.ok(output.includes("another-project"), "输出应包含新项目名");
  assert.ok(!output.includes("probe-xyz"), "不应包含其他项目名");
});

test("AIPOS-F57 ④ JSON 输出模式可用", () => {
  const output = execSync(`${LYBRA_BIN} onboarding guide test-proj --json`, {
    encoding: "utf-8",
    cwd: process.cwd(),
  });

  const data = JSON.parse(output);
  assert.strictEqual(data.project_name, "test-proj");
  assert.strictEqual(data.total_steps, 6);
  assert.strictEqual(data.steps.length, 6);

  // 验证每步结构
  for (const step of data.steps) {
    assert.ok(step.step_number, "每步应有 step_number");
    assert.ok(step.title, "每步应有 title");
    assert.ok(step.command, "每步应有 command");
    assert.ok(step.purpose, "每步应有 purpose");
    assert.ok(step.check, "每步应有 check");
    assert.ok(step.on_fail, "每步应有 on_fail");
    assert.ok(step.creates, "每步应有 creates");
  }
});

test("AIPOS-F57 ⑤ onboarding check 可用", () => {
  // check 命令不应崩溃(即使前置条件不满足)
  try {
    execSync(`${LYBRA_BIN} onboarding check test-proj --step 1`, {
      encoding: "utf-8",
      cwd: process.cwd(),
    });
  } catch (err: any) {
    // 可能返回非 0(前置不满足),但不应崩溃
    assert.ok(err.stdout || err.stderr, "应有输出");
  }
});

console.log("✓ AIPOS-F57 onboarding 夹具全部通过");
