/**
 * AIPOS-R1 Conformance 测试 (JS 版本, 无需 TypeScript)
 */

const fs = require("fs");
const path = require("path");

// 简化版 ConnectionResolver (从 loop-context.ts 翻译)
class ConnectionResolver {
  static discoverLybraDir(workspaceRoot) {
    const lybraDir = path.join(workspaceRoot, ".lybra");
    return fs.existsSync(lybraDir) ? lybraDir : null;
  }

  static loadConnectionConfig(lybraDir) {
    const connectionFile = path.join(lybraDir, "connection.json");
    if (!fs.existsSync(connectionFile)) {
      throw new Error(`connection.json not found in ${lybraDir}`);
    }
    const data = JSON.parse(fs.readFileSync(connectionFile, "utf-8"));
    return data;
  }

  static resolveGateUrl(opts) {
    const env = opts.env || {};
    
    if (opts.explicitUrl) {
      return opts.explicitUrl;
    }
    
    const envUrl = (env.LYBRA_GATE_URL || "").trim();
    if (envUrl) {
      return envUrl;
    }
    
    if (opts.workspaceRoot && opts.mockConnectionJson) {
      const rpcUrl = opts.mockConnectionJson.mcp?.rpc_url;
      if (rpcUrl) {
        return rpcUrl;
      }
    }
    
    return "http://127.0.0.1:7118/mcp";
  }

  static resolveToken(opts) {
    const env = opts.env || {};
    
    if (opts.explicitToken) {
      return opts.explicitToken;
    }
    
    const envToken = (env.LYBRA_TOKEN || "").trim();
    if (envToken) {
      return envToken;
    }
    
    if (opts.workspaceRoot && opts.mockConnectionJson) {
      const tokens = opts.mockConnectionJson.tokens;
      if (Array.isArray(tokens)) {
        if (opts.agentInstance) {
          for (const tokenEntry of tokens) {
            if (tokenEntry.agent_instance === opts.agentInstance) {
              const token = tokenEntry.token;
              if (token) {
                return token;
              }
            }
          }
        }
        
        if (opts.role) {
          for (const tokenEntry of tokens) {
            if (tokenEntry.role === opts.role) {
              const token = tokenEntry.token;
              if (token) {
                return token;
              }
            }
          }
        }
      }
    }
    
    throw new Error(
      `Cannot resolve token for role=${opts.role}, agentInstance=${opts.agentInstance}`
    );
  }

  static resolveProjectScope(opts) {
    const tokenData = opts.tokenData;
    const explicitProject = opts.explicitProject;
    const projects = tokenData.projects;
    
    if (!projects || !Array.isArray(projects)) {
      return null;
    }
    
    if (explicitProject) {
      return explicitProject;
    }
    
    if (projects.length === 1) {
      return projects[0];
    }
    
    if (projects.length > 1) {
      const defaultProject = tokenData.default_project;
      if (defaultProject) {
        return defaultProject;
      }
    }
    
    return null;
  }
}

function loadFixtures(productRepoRoot) {
  const fixturesPath = path.join(productRepoRoot, "schema/conformance/loop_context_fixtures.json");
  const data = JSON.parse(fs.readFileSync(fixturesPath, "utf-8"));
  return data.fixtures;
}

function runConformanceTests(productRepoRoot) {
  const fixtures = loadFixtures(productRepoRoot);
  let passed = 0;
  let failed = 0;

  console.log("=".repeat(72));
  console.log("AIPOS-R1 Conformance 测试 (JS 侧)");
  console.log("=".repeat(72));
  console.log(`加载 ${fixtures.length} 个夹具\n`);

  for (const fixture of fixtures) {
    console.log(`[${fixture.name}] ${fixture.description}`);
    
    try {
      const connectionJson = fixture.input.connection_json;
      const tokenData = fixture.input.token_data;
      const env = fixture.input.env;
      const explicitArgs = fixture.input.explicit_args;

      const workspaceRoot = connectionJson.workspace_root || "/tmp";

      // 解析 gate URL
      const gateUrl = ConnectionResolver.resolveGateUrl({
        workspaceRoot,
        env,
        explicitUrl: explicitArgs.gate_url,
        mockConnectionJson: connectionJson
      });

      // 解析 token
      const token = ConnectionResolver.resolveToken({
        workspaceRoot,
        role: tokenData.role,
        agentInstance: tokenData.agent_instance,
        env,
        explicitToken: explicitArgs.token,
        mockConnectionJson: connectionJson
      });

      // 解析 project scope
      const projectScope = ConnectionResolver.resolveProjectScope({
        tokenData,
        explicitProject: explicitArgs.project
      });

      // 解析 instance scope
      const instanceScope = tokenData.agent_instance || null;

      // 验证结果
      const errors = [];
      
      if (gateUrl !== fixture.expected.gate_url) {
        errors.push(`  gate_url: got "${gateUrl}", expected "${fixture.expected.gate_url}"`);
      }
      
      if (token !== fixture.expected.token) {
        errors.push(`  token: got "${token}", expected "${fixture.expected.token}"`);
      }
      
      if (projectScope !== fixture.expected.project_scope) {
        errors.push(`  project_scope: got "${projectScope}", expected "${fixture.expected.project_scope}"`);
      }
      
      if (instanceScope !== fixture.expected.instance_scope) {
        errors.push(`  instance_scope: got "${instanceScope}", expected "${fixture.expected.instance_scope}"`);
      }

      if (errors.length === 0) {
        console.log("  ✓ PASS\n");
        passed++;
      } else {
        console.log("  ✗ FAIL");
        errors.forEach(err => console.log(err));
        console.log();
        failed++;
      }
    } catch (e) {
      console.log(`  ✗ FAIL: ${e.message}\n`);
      failed++;
    }
  }

  console.log("=".repeat(72));
  console.log(`结果: ${passed} passed, ${failed} failed`);
  console.log("=".repeat(72));

  return failed === 0;
}

// CLI entry point
const productRepoRoot = process.argv[2] || "/home/kiwi/projects/lybra";
const success = runConformanceTests(productRepoRoot);
process.exit(success ? 0 : 1);
