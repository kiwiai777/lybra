#!/usr/bin/env node
/**
 * AIPOS-R6O 活体验收测试: loadConfig 必须从 .lybra/ 自发现配置,忽略带毒 env
 */

import { loadConfig } from './gate-client.ts';

console.log("=== AIPOS-R6O 活体验收测试 ① ===");
console.log("带毒 env: LYBRA_OWNER_POLICY_REF=pol_lybra_dev_1 (过期信封), LYBRA_ACTOR 未设置");
console.log("");

// 模拟带毒 env
const testEnv = {
  LYBRA_OWNER_POLICY_REF: "pol_lybra_dev_1",  // 过期信封
  LYBRA_WORKSPACE_ROOT: "/home/kiwi/ai-project-os/2_projects/lybra",
  // LYBRA_ACTOR 故意不设置
};

try {
  const config = loadConfig(testEnv);
  
  console.log("✅ loadConfig 成功");
  console.log("");
  console.log("配置结果:");
  console.log(`  身份 (actor): ${config.actor}`);
  console.log(`  实例 (agentInstance): ${config.agentInstance}`);
  console.log(`  信封 (ownerPolicyRef): ${config.ownerPolicyRef}`);
  console.log(`  角色 (role): ${config.role}`);
  console.log(`  gate URL: ${config.gateUrl}`);
  console.log(`  workspace: ${config.workspaceRoot}`);
  console.log(`  token fingerprint: ${config.token ? 'sha256:' + require('crypto').createHash('sha256').update(config.token).digest('hex').slice(0, 12) : '(none)'}`);
  console.log("");
  
  // 验收判据
  const expectedActor = "exec.lybra.kiwiai-dev";
  const expectedPolicy = "pol_lybra_dev_9";
  
  let pass = true;
  if (config.actor !== expectedActor) {
    console.log(`❌ 身份错误: 期望 ${expectedActor}, 实际 ${config.actor}`);
    pass = false;
  } else {
    console.log(`✅ 身份正确: ${config.actor} (来自 .lybra/role)`);
  }
  
  if (config.ownerPolicyRef !== expectedPolicy) {
    console.log(`❌ 信封错误: 期望 ${expectedPolicy}, 实际 ${config.ownerPolicyRef}`);
    pass = false;
  } else {
    console.log(`✅ 信封正确: ${config.ownerPolicyRef} (来自 .lybra/role, 未被 env 毒劫持)`);
  }
  
  if (pass) {
    console.log("");
    console.log("🎉 验收通过: 带毒 env 下自发现胜出");
  } else {
    console.log("");
    console.log("❌ 验收失败");
    process.exit(1);
  }
  
} catch (error) {
  console.log(`❌ loadConfig 抛出异常: ${error.message}`);
  console.log("");
  console.log("详细错误:");
  console.log(error);
  process.exit(1);
}
