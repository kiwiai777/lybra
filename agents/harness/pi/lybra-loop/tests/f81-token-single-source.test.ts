/**
 * AIPOS-F81 件②: pi 侧 token 解析同口径 —— headless 夹具。
 *
 * 靶场 = 2026-09-24 真实现场同形: connection.json tokens=[retired 旧 token, 新 token]。
 *  ① resolveIdentity / loadConfig / resolveToken 均取新 token (只比指纹);
 *  ② 全 retired → fail-closed: resolveIdentity token.value=null + error 带重签出口 (禁 env 顶替),
 *     loadConfig 抛 ConfigError 带出口, resolveToken 抛 TokenAllRetiredError;
 *  ③ 无命中仍按声明落 env 兜底; instance 匹配先于 role 匹配。
 * 纪律: token 永不上屏 —— 输出与断言名只带 sha256 指纹。
 *
 * 跑法: `node tests/f81-token-single-source.test.ts`
 */

import { loadConfig, ConfigError } from "../gate-client.ts";
import {
  ConnectionResolver,
  TOKEN_REENROLL_EXIT,
  TokenAllRetiredError,
} from "../loop-context.ts";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash, randomBytes } from "node:crypto";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}
const fp = (t: string | null | undefined) =>
  t ? "sha256:" + createHash("sha256").update(t, "utf-8").digest("hex").slice(0, 12) : "(none)";
const tok = (label: string) => `f81-${label}-${randomBytes(8).toString("hex")}`;

const INSTANCE = "exec.lybra.kiwiai-dev";
const originalCwd = process.cwd();
const root = mkdtempSync(join(tmpdir(), "lybra-f81-"));
const govRoot = join(root, "gov");
mkdirSync(govRoot, { recursive: true });
const baseEnv = { HOME: process.env.HOME };

function makeStation(name: string, tokens: object[]) {
  const dir = join(root, name);
  mkdirSync(join(dir, ".lybra"), { recursive: true });
  writeFileSync(
    join(dir, ".lybra", "role"),
    JSON.stringify({ role: "executor", instance: INSTANCE, owner_policy_ref: "pol_lybra_dev_9" }),
  );
  writeFileSync(
    join(dir, ".lybra", "connection.json"),
    JSON.stringify({ config_version: 1, workspace_root: govRoot, mcp: { rpc_url: "http://127.0.0.1:7118/mcp" }, tokens }),
  );
  return dir;
}
const retired = { retired: true, retired_at: "2026-09-24T03:00:00Z", retired_reason: "superseded by re-enrollment" };

try {
  // --- ① [retired, new] → 新 token ---
  {
    const oldTok = tok("old");
    const newTok = tok("new");
    const dir = makeStation("rotated", [
      { role: "executor", agent_instance: INSTANCE, token: oldTok, projects: ["lybra"], ...retired },
      { role: "executor", agent_instance: INSTANCE, token: newTok, projects: ["lybra"] },
    ]);
    process.chdir(dir);
    const ident = ConnectionResolver.resolveIdentity({ env: baseEnv });
    console.log(`① resolveIdentity → ${fp(ident.token.value)} (old=${fp(oldTok)} new=${fp(newTok)})`);
    check(`① resolveIdentity 取新 token ${fp(newTok)}`, fp(ident.token.value) === fp(newTok));
    check("① resolveIdentity 来源 .lybra/connection.json 且无 error", ident.token.source === ".lybra/connection.json" && !ident.token.error);
    try {
      const c = loadConfig(baseEnv);
      console.log(`① loadConfig → ${fp(c.token)} provenance=${c.provenance.token.value}`);
      check(`① loadConfig(gate-client) 取新 token ${fp(newTok)}`, fp(c.token) === fp(newTok));
      check("① provenance 只出指纹", c.provenance.token.value === fp(newTok));
    } catch (e) {
      check(`① loadConfig 不抛 (${e instanceof Error ? e.constructor.name : "?"})`, false);
    }
    const byInst = ConnectionResolver.resolveToken({ agentInstance: INSTANCE, env: baseEnv });
    const byRole = ConnectionResolver.resolveToken({ role: "executor", env: baseEnv });
    check(`① resolveToken(instance) 取新 token ${fp(newTok)}`, fp(byInst) === fp(newTok));
    check(`① resolveToken(role) 取新 token ${fp(newTok)}`, fp(byRole) === fp(newTok));
  }

  // --- ② 全 retired → fail-closed 带出口, 禁 env 顶替 ---
  {
    const a = tok("a");
    const b = tok("b");
    const envTok = tok("env");
    const dir = makeStation("all-retired", [
      { role: "executor", agent_instance: INSTANCE, token: a, ...retired },
      { role: "executor", agent_instance: INSTANCE, token: b, ...retired },
    ]);
    process.chdir(dir);
    const ident = ConnectionResolver.resolveIdentity({ env: { ...baseEnv, LYBRA_TOKEN: envTok } });
    const err = ident.token.error ?? "";
    console.log(`② resolveIdentity 全 retired: value=${ident.token.value === null ? "null" : fp(ident.token.value)} error=${err}`);
    check("② resolveIdentity value=null (禁 env 静默顶替)", ident.token.value === null && ident.token.source === "unresolved");
    check("② error 带重签出口", err.includes(TOKEN_REENROLL_EXIT) && err.includes("lybra roles enroll"));
    check("② error 列指纹且不含 token 值", err.includes(fp(a)) && err.includes(fp(b)) && !err.includes(a) && !err.includes(b));
    try {
      loadConfig({ ...baseEnv, LYBRA_TOKEN: envTok });
      check("② loadConfig 应抛 ConfigError", false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.log(`② loadConfig 拒因: ${msg}`);
      check("② loadConfig 抛 ConfigError", e instanceof ConfigError);
      check("② ConfigError 带重签出口且不含 token 值", msg.includes("lybra roles enroll") && !msg.includes(a) && !msg.includes(b) && !msg.includes(envTok));
    }
    try {
      ConnectionResolver.resolveToken({ role: "executor", agentInstance: INSTANCE, env: { ...baseEnv, LYBRA_TOKEN: envTok } });
      check("② resolveToken 应抛 TokenAllRetiredError", false);
    } catch (e) {
      check("② resolveToken 抛 TokenAllRetiredError", e instanceof TokenAllRetiredError);
    }
  }

  // --- ③ instance 先于 role; 无命中仍落 env 兜底 ---
  {
    const other = tok("other");
    const mineNew = tok("mine-new");
    const dir = makeStation("instance-first", [
      { role: "executor", agent_instance: "exec.lybra.other-host", token: other },
      { role: "executor", agent_instance: INSTANCE, token: tok("mine-old"), ...retired },
      { role: "executor", agent_instance: INSTANCE, token: mineNew },
    ]);
    process.chdir(dir);
    const ident = ConnectionResolver.resolveIdentity({ env: baseEnv });
    check(`③ instance 匹配先于 role 匹配 → ${fp(mineNew)}`, fp(ident.token.value) === fp(mineNew));

    const envTok = tok("env");
    const dir2 = makeStation("no-match", [{ role: "auditor", agent_instance: "audit.lybra.kiwiai-dev", token: tok("aud") }]);
    process.chdir(dir2);
    const ident2 = ConnectionResolver.resolveIdentity({ env: { ...baseEnv, LYBRA_TOKEN: envTok } });
    check("③ 无命中 → env:LYBRA_TOKEN 兜底 (声明优先级不变)", fp(ident2.token.value) === fp(envTok) && ident2.token.source === "env:LYBRA_TOKEN");
    const rt = ConnectionResolver.resolveToken({ role: "executor", env: { ...baseEnv, LYBRA_TOKEN: envTok } });
    check("③ resolveToken 无命中 → env 兜底", fp(rt) === fp(envTok));
  }
} finally {
  process.chdir(originalCwd);
  rmSync(root, { recursive: true, force: true });
}

for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures} FAIL / ${checks.length}`);
process.exit(failures === 0 ? 0 : 1);
