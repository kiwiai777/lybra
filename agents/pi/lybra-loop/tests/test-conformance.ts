/**
 * AIPOS-R1 Conformance 测试 (TS 侧)
 * 
 * 读取 schema/conformance/loop_context_fixtures.json 夹具,
 * 验证 TS ConnectionResolver 解析结果与 expected 一致。
 * 
 * 与 Python 测试 (tools/test_aipos_r1_conformance.py) 读同一夹具,
 * 确保两边解析逻辑同构 (一机制一实现红线)。
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ConnectionResolver, type ConnectionConfig, type TokenData } from "./loop-context";

interface Fixture {
  name: string;
  description: string;
  input: {
    connection_json: ConnectionConfig;
    token_data: TokenData;
    env: Record<string, string>;
    explicit_args: Record<string, string>;
  };
  expected: {
    gate_url: string;
    token: string;
    project_scope: string | null;
    instance_scope: string | null;
  };
}

interface FixturesFile {
  schema_version: string;
  description: string;
  fixtures: Fixture[];
}

function loadFixtures(productRepoRoot: string): Fixture[] {
  const fixturesPath = join(productRepoRoot, "schema/conformance/loop_context_fixtures.json");
  const data = JSON.parse(readFileSync(fixturesPath, "utf-8")) as FixturesFile;
  return data.fixtures;
}

// 模拟 .lybra/connection.json 文件系统
// (测试中不实际写文件, 而是 mock readFileSync)
const originalReadFileSync = readFileSync;
let mockConnectionJson: ConnectionConfig | null = null;

function mockFs(connectionJson: ConnectionConfig) {
  mockConnectionJson = connectionJson;
}

function unmockFs() {
  mockConnectionJson = null;
}

// Override readFileSync for testing
const Module = require("module");
const originalRequire = Module.prototype.require;
Module.prototype.require = function (id: string) {
  if (id === "node:fs" || id === "fs") {
    const fs = originalRequire.apply(this, arguments);
    return {
      ...fs,
      readFileSync: (path: string, encoding?: string) => {
        if (mockConnectionJson && path.endsWith("connection.json")) {
          return JSON.stringify(mockConnectionJson);
        }
        return originalReadFileSync(path, encoding as BufferEncoding);
      },
      existsSync: (path: string) => {
        if (mockConnectionJson && (path.endsWith(".lybra") || path.endsWith("connection.json"))) {
          return true;
        }
        return fs.existsSync(path);
      }
    };
  }
  return originalRequire.apply(this, arguments);
};

function runConformanceTests(productRepoRoot: string): boolean {
  const fixtures = loadFixtures(productRepoRoot);
  let passed = 0;
  let failed = 0;

  console.log("=" + "=".repeat(70));
  console.log("AIPOS-R1 Conformance 测试 (TS 侧)");
  console.log("=" + "=".repeat(70));
  console.log(`加载 ${fixtures.length} 个夹具\n`);

  for (const fixture of fixtures) {
    console.log(`[${fixture.name}] ${fixture.description}`);
    
    try {
      // Mock .lybra/connection.json
      mockFs(fixture.input.connection_json);
      
      const workspaceRoot = fixture.input.connection_json.workspace_root || "/tmp";
      const env = fixture.input.env;
      const explicitArgs = fixture.input.explicit_args;
      const tokenData = fixture.input.token_data;

      // 解析 gate URL
      const gateUrl = ConnectionResolver.resolveGateUrl({
        workspaceRoot,
        env,
        explicitUrl: explicitArgs.gate_url
      });

      // 解析 token
      const token = ConnectionResolver.resolveToken({
        workspaceRoot,
        role: tokenData.role,
        agentInstance: tokenData.agent_instance,
        env,
        explicitToken: explicitArgs.token
      });

      // 解析 project scope
      const projectScope = ConnectionResolver.resolveProjectScope({
        tokenData,
        explicitProject: explicitArgs.project
      });

      // 解析 instance scope
      const instanceScope = tokenData.agent_instance || null;

      // 验证结果
      const errors: string[] = [];
      
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

      unmockFs();
    } catch (e) {
      console.log(`  ✗ FAIL: ${e instanceof Error ? e.message : String(e)}\n`);
      failed++;
      unmockFs();
    }
  }

  console.log("=" + "=".repeat(70));
  console.log(`结果: ${passed} passed, ${failed} failed`);
  console.log("=" + "=".repeat(70));

  return failed === 0;
}

// CLI entry point
if (require.main === module) {
  const productRepoRoot = process.argv[2] || "/home/kiwi/projects/lybra";
  const success = runConformanceTests(productRepoRoot);
  process.exit(success ? 0 : 1);
}

export { runConformanceTests };
