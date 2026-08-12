/**
 * AIPOS-R1: LoopContext + ConnectionResolver (TS 实现)
 * 
 * 设计权威: LOOP-REDESIGN v2 §3
 * 
 * LoopContext: 解析一次贯穿动词的不可变上下文
 * ConnectionResolver: 连接→token解析器 (precedence: 显式 → env → 自发现)
 * 
 * 与 tools/loop_context.py 同构, 以 schema/conformance/ 夹具锁定一致性。
 */

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

export interface LoopContext {
  project: string;
  instance: string;  // agent_instance
  workspaceRoot: string;
  codeRepo: string | null;
  gateUrl: string;
  token: string;
  policy?: string;
  taskState?: string;
  worktree?: string;
}

export interface ConnectionConfig {
  mode?: string;
  workspace_root?: string;
  mcp?: {
    rpc_url?: string;
    advertise_host?: string;
    host?: string;
    port?: number;
  };
  tokens?: Array<{
    role?: string;
    agent_instance?: string;
    token?: string;
    scopes?: string[];
    fingerprint?: string;
    token_ref?: string;
  }>;
}

export interface TokenData {
  role?: string;
  agent_instance?: string;
  projects?: string[];
  default_project?: string;
}

export class ConnectionResolver {
  /**
   * 自动发现 .lybra/ 目录
   */
  static discoverLybraDir(workspaceRoot: string): string | null {
    const lybraDir = join(workspaceRoot, ".lybra");
    if (existsSync(lybraDir)) {
      return lybraDir;
    }
    return null;
  }

  /**
   * 加载 connection.json
   */
  static loadConnectionConfig(lybraDir: string): ConnectionConfig {
    const connectionFile = join(lybraDir, "connection.json");
    if (!existsSync(connectionFile)) {
      throw new Error(`connection.json not found in ${lybraDir}`);
    }

    try {
      const data = JSON.parse(readFileSync(connectionFile, "utf-8"));
      return data as ConnectionConfig;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      throw new Error(`Invalid JSON in ${connectionFile}: ${msg}`);
    }
  }

  /**
   * 解析 gate URL
   * Precedence: 显式参数 → env → .lybra/ 自发现
   */
  static resolveGateUrl(opts: {
    workspaceRoot?: string;
    env?: Record<string, string | undefined>;
    explicitUrl?: string;
  }): string {
    const env = opts.env ?? process.env;

    // 显式参数 (最高优先级)
    if (opts.explicitUrl) {
      return opts.explicitUrl;
    }

    // 环境变量覆盖
    const envUrl = env.LYBRA_GATE_URL?.trim();
    if (envUrl) {
      return envUrl;
    }

    // 自动发现 .lybra/
    if (opts.workspaceRoot) {
      const lybraDir = this.discoverLybraDir(opts.workspaceRoot);
      if (lybraDir) {
        try {
          const config = this.loadConnectionConfig(lybraDir);
          const rpcUrl = config.mcp?.rpc_url;
          if (rpcUrl) {
            return rpcUrl;
          }
        } catch {
          // 自发现失败, 继续fallback
        }
      }
    }

    // 默认 fallback
    return "http://127.0.0.1:7118/mcp";
  }

  /**
   * 解析 token
   * Precedence: 显式参数 → env → .lybra/ 自发现 (按 agent_instance 或 role 匹配)
   */
  static resolveToken(opts: {
    workspaceRoot?: string;
    role?: string;
    agentInstance?: string;
    env?: Record<string, string | undefined>;
    explicitToken?: string;
  }): string {
    const env = opts.env ?? process.env;

    // 显式参数 (最高优先级)
    if (opts.explicitToken) {
      return opts.explicitToken;
    }

    // 环境变量覆盖
    const envToken = env.LYBRA_TOKEN?.trim();
    if (envToken) {
      return envToken;
    }

    // 自动发现 .lybra/connection.json
    if (opts.workspaceRoot) {
      const lybraDir = this.discoverLybraDir(opts.workspaceRoot);
      if (lybraDir) {
        try {
          const config = this.loadConnectionConfig(lybraDir);
          const tokens = config.tokens;
          if (!Array.isArray(tokens)) {
            throw new Error("tokens must be an array");
          }

          // 按 agent_instance 匹配 (最具体)
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

          // 按 role 匹配
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
        } catch {
          // 自发现失败, 继续fallback
        }
      }
    }

    throw new Error(
      `Cannot resolve token for role=${opts.role}, agentInstance=${opts.agentInstance}. ` +
      "Provide explicit token, set LYBRA_TOKEN env, or ensure .lybra/connection.json exists."
    );
  }

  /**
   * 从 token data 推断 project scope
   * 单项目 token → 该项目
   * 多项目 token → explicit project 参数 > default_project > null (调用者自行推断 active project)
   */
  static resolveProjectScope(opts: {
    tokenData: TokenData;
    explicitProject?: string;
  }): string | null {
    const { tokenData, explicitProject } = opts;
    const projects = tokenData.projects;

    if (!projects || !Array.isArray(projects)) {
      return null;
    }

    // 显式 project 参数 (最高优先级)
    if (explicitProject) {
      return explicitProject;
    }

    // 单项目 token: 自动推断
    if (projects.length === 1) {
      return projects[0];
    }

    // 多项目 token: 使用 default_project
    if (projects.length > 1) {
      const defaultProject = tokenData.default_project;
      if (defaultProject) {
        return defaultProject;
      }
    }

    // 多项目且无 default: 返回 null, 调用者需推断 active project
    return null;
  }
}
