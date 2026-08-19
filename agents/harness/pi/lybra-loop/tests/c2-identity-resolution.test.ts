/**
 * AIPOS-C2 身份配置单一真相 —— headless 验收测试。
 *
 * 覆盖任务卡验收 ① ② ③ ⑤ (④ enroll 铸全在 Python 侧 tools/test_aipos_c2_enroll.py):
 *  ① 审计工位形态 (无 LYBRA_* env, .lybra/role=auditor) → loadConfig 解析 role=auditor (不缺省 executor)
 *  ② 毒 env (全套 LYBRA_*) + 正确 .lybra → .lybra 胜出, 被降级的 env 键 provenance.envDowngraded=true
 *  ③ 删掉 role 文件且无 env → 出声并停报缺键 role, 不缺省 executor
 *  ⑤ role 与 actor 来源一致性 (同一 .lybra/role, provenance.source 相同)
 *
 * 跑法: `node tests/c2-identity-resolution.test.ts`
 */

import { loadConfig, ConfigError } from "../gate-client.ts";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

const originalCwd = process.cwd();
const root = mkdtempSync(join(tmpdir(), "lybra-c2-"));

function makeStation(name: string, roleFile: object | null, connection: object) {
  const dir = join(root, name);
  mkdirSync(join(dir, ".lybra"), { recursive: true });
  if (roleFile !== null) {
    writeFileSync(join(dir, ".lybra", "role"), JSON.stringify(roleFile));
  }
  writeFileSync(join(dir, ".lybra", "connection.json"), JSON.stringify(connection));
  return dir;
}

const stationBase = join(root, "station");
mkdirSync(stationBase, { recursive: true });

// --- 验收 ①: 审计工位形态 → role=auditor ---
{
  const dir = makeStation(
    "auditor",
    { role: "auditor", instance: "audit.lybra.test", owner_policy_ref: "pol_lybra_dev_9" },
    {
      config_version: 1,
      workspace_root: stationBase,
      mcp: { rpc_url: "http://127.0.0.1:7118/mcp" },
      tokens: [{ role: "auditor", agent_instance: "audit.lybra.test", token: "audit-secret" }],
    },
  );
  process.chdir(dir);
  try {
    const c = loadConfig({ HOME: process.env.HOME });
    check("① 审计工位 role=auditor (非 executor)", c.role === "auditor");
    check("① 审计工位 actor=audit.lybra.test", c.actor === "audit.lybra.test");
    check("① role 来源 .lybra/role", c.provenance.role.source === ".lybra/role");
    // sweep 守卫依据: role != executor → 不跑 finalize (对应 tryAutoFinalizeOnPassVerdict)
    check("① sweep 守卫: 非 executor 不具 finalize 能力", c.role !== "executor");
  } catch (e) {
    check(`① 审计工位 loadConfig 不抛 (${e})`, false);
  }
}

// --- 验收 ②: 毒 env + 正确 .lybra → .lybra 胜出, env 被降级标 ⚠ ---
{
  const dir = makeStation(
    "poison",
    { role: "executor", instance: "exec.lybra.test", owner_policy_ref: "pol_lybra_dev_9" },
    {
      config_version: 1,
      workspace_root: stationBase,
      mcp: { rpc_url: "http://127.0.0.1:7118/mcp" },
      tokens: [{ role: "executor", agent_instance: "exec.lybra.test", token: "exec-good-token" }],
    },
  );
  process.chdir(dir);
  const poisonEnv = {
    HOME: process.env.HOME,
    LYBRA_ROLE: "owner",
    LYBRA_ACTOR: "owner.lybra.poison",
    LYBRA_AGENT_INSTANCE: "owner.lybra.poison",
    LYBRA_OWNER_POLICY_REF: "pol_poison",
    LYBRA_TOKEN: "poison-token",
    LYBRA_WORKSPACE_ROOT: "/poison/root",
    LYBRA_GATE_URL: "http://poison:9999/mcp",
  };
  try {
    const c = loadConfig(poisonEnv);
    check("② 毒 env 下 role 仍 executor (.lybra 胜出)", c.role === "executor");
    check("② 毒 env 下 actor 仍 exec.lybra.test", c.actor === "exec.lybra.test");
    check("② 毒 env 下 policy 仍 pol_lybra_dev_9", c.ownerPolicyRef === "pol_lybra_dev_9");
    check("② 毒 env 下 token 未被劫持", c.provenance.token.source === ".lybra/connection.json");
    check("② role envDowngraded=true (横幅标 ⚠)", c.provenance.role.envDowngraded === true);
    check("② actor envDowngraded=true", c.provenance.actor.envDowngraded === true);
    check("② policy envDowngraded=true", c.provenance.owner_policy_ref.envDowngraded === true);
    check("② gate_url envDowngraded=true", c.provenance.gate_url.envDowngraded === true);
  } catch (e) {
    check(`② 毒 env loadConfig 不抛 (${e})`, false);
  }
}

// --- 验收 ③: 删掉 role 文件且无 env → 出声并停报缺键 role ---
{
  const dir = makeStation(
    "norole",
    null, // 无 role 文件
    {
      config_version: 1,
      workspace_root: stationBase,
      mcp: { rpc_url: "http://127.0.0.1:7118/mcp" },
      tokens: [{ role: "executor", agent_instance: "exec.lybra.test", token: "exec-secret" }],
    },
  );
  process.chdir(dir);
  try {
    loadConfig({ HOME: process.env.HOME });
    check("③ 删 role 文件且无 env → 应抛 ConfigError 却没抛", false);
  } catch (e) {
    const isCfg = e instanceof ConfigError;
    const msg = String(e);
    check("③ 删 role 文件 → ConfigError", isCfg);
    check("③ 报缺键 role", msg.includes("缺键 role"));
    check("③ 报找过哪几层 (env:LYBRA_ROLE)", msg.includes("LYBRA_ROLE"));
    check("③ 不缺省 executor (错误信息里无 executor 冒充)", !msg.includes("executor"));
  }
}

// --- 验收 ⑤: role 与 actor 来源一致性 ---
{
  const dir = makeStation(
    "consistency",
    { role: "executor", instance: "exec.lybra.test", owner_policy_ref: "pol_lybra_dev_9" },
    {
      config_version: 1,
      workspace_root: stationBase,
      mcp: { rpc_url: "http://127.0.0.1:7118/mcp" },
      tokens: [{ role: "executor", agent_instance: "exec.lybra.test", token: "exec-secret" }],
    },
  );
  process.chdir(dir);
  try {
    const c = loadConfig({ HOME: process.env.HOME });
    check("⑤ role 与 actor 同源 (.lybra/role)", c.provenance.role.source === ".lybra/role" && c.provenance.actor.source === ".lybra/role");
    check("⑤ role 与 actor 源头字段相同", c.provenance.role.source === c.provenance.actor.source);
    check("⑤ agent_instance 同源", c.provenance.agent_instance.source === c.provenance.role.source);
    check("⑤ owner_policy_ref 同源", c.provenance.owner_policy_ref.source === c.provenance.role.source);
  } catch (e) {
    check(`⑤ 一致性 loadConfig 不抛 (${e})`, false);
  }
}

// 恢复 cwd 并清理
process.chdir(originalCwd);
rmSync(root, { recursive: true, force: true });

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
