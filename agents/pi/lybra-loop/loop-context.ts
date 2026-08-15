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
   * 加载 .lybra/role 文件 (JSON 或纯文本兼容)
   */
  static loadRoleFile(lybraDir: string): { role: string; instance?: string; owner_policy_ref?: string } | null {
    const roleFile = join(lybraDir, "role");
    if (!existsSync(roleFile)) {
      return null;
    }

    try {
      const content = readFileSync(roleFile, "utf-8").trim();
      // 尝试 JSON 格式 (新格式)
      if (content.startsWith("{")) {
        const data = JSON.parse(content);
        return data as { role: string; instance?: string; owner_policy_ref?: string };
      }
      // 纯文本格式 (旧格式,仅 role)
      return { role: content };
    } catch {
      return null;
    }
  }

  /**
   * 解析 gate URL
   * Precedence: 显式参数 → .lybra/自发现 → env仅覆盖
   * AIPOS-R6H: env 降为最低优先级,消除env注入病
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

    // 自动发现 .lybra/ (优先级高于env)
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
          // 自发现失败, 继续
        }
      }
    }

    // 环境变量覆盖 (最低优先级)
    const envUrl = env.LYBRA_GATE_URL?.trim();
    if (envUrl) {
      return envUrl;
    }

    // 默认 fallback
    return "http://127.0.0.1:7118/mcp";
  }

  /**
   * 解析 token
   * Precedence: 显式参数 → .lybra/自发现 → env仅覆盖
   * AIPOS-R6H: env 降为最低优先级
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

    // 自动发现 .lybra/connection.json (优先级高于env)
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
          // 自发现失败, 继续
        }
      }
    }

    // 环境变量覆盖 (最低优先级)
    const envToken = env.LYBRA_TOKEN?.trim();
    if (envToken) {
      return envToken;
    }

    throw new Error(
      `Cannot resolve token for role=${opts.role}, agentInstance=${opts.agentInstance}. ` +
      "Provide explicit token, set LYBRA_TOKEN env, or ensure .lybra/connection.json exists."
    );
  }

  /**
   * 解析 actor (agent_instance)
   * Precedence: 显式参数 → .lybra/role文件 → .lybra/actor文件 → env仅覆盖
   */
  static resolveActor(opts: {
    workspaceRoot?: string;
    env?: Record<string, string | undefined>;
    explicitActor?: string;
  }): string | null {
    const env = opts.env ?? process.env;

    // 显式参数
    if (opts.explicitActor) {
      return opts.explicitActor;
    }

    // 自动发现 .lybra/role (JSON格式含instance)
    if (opts.workspaceRoot) {
      const lybraDir = this.discoverLybraDir(opts.workspaceRoot);
      if (lybraDir) {
        const roleData = this.loadRoleFile(lybraDir);
        if (roleData?.instance) {
          return roleData.instance;
        }

        // fallback: .lybra/actor (纯文本)
        const actorFile = join(lybraDir, "actor");
        if (existsSync(actorFile)) {
          try {
            return readFileSync(actorFile, "utf-8").trim();
          } catch {
            // ignore
          }
        }
      }
    }

    // env 覆盖 (最低优先级)
    const envActor = env.LYBRA_ACTOR?.trim();
    if (envActor) {
      return envActor;
    }

    return null;
  }

  /**
   * 解析 owner_policy_ref
   * Precedence: 显式参数 → .lybra/role文件 → .lybra/policy文件 → env仅覆盖
   */
  static resolveOwnerPolicyRef(opts: {
    workspaceRoot?: string;
    env?: Record<string, string | undefined>;
    explicitPolicy?: string;
  }): string | null {
    const env = opts.env ?? process.env;

    // 显式参数
    if (opts.explicitPolicy) {
      return opts.explicitPolicy;
    }

    // 自动发现 .lybra/role (JSON格式含owner_policy_ref)
    if (opts.workspaceRoot) {
      const lybraDir = this.discoverLybraDir(opts.workspaceRoot);
      if (lybraDir) {
        const roleData = this.loadRoleFile(lybraDir);
        if (roleData?.owner_policy_ref) {
          return roleData.owner_policy_ref;
        }

        // fallback: .lybra/policy (纯文本)
        const policyFile = join(lybraDir, "policy");
        if (existsSync(policyFile)) {
          try {
            return readFileSync(policyFile, "utf-8").trim();
          } catch {
            // ignore
          }
        }
      }
    }

    // env 覆盖 (最低优先级)
    const envPolicy = env.LYBRA_OWNER_POLICY_REF?.trim();
    if (envPolicy) {
      return envPolicy;
    }

    return null;
  }

  /**
   * 解析 gate workspace (治理工作区语义)
   * 用途: loop/gate/queue/records 操作 — 队列、任务卡、records 都在治理工作区
   * Precedence: 显式参数 → .lybra/connection.json → env仅覆盖
   * AIPOS-R6P 靶③: **允许治理仓** (ai-project-os),不做路径校验
   */
  static resolveGateWorkspace(opts: {
    env?: Record<string, string | undefined>;
    explicitRoot?: string;
  }): string | null {
    const env = opts.env ?? process.env;

    // 显式参数
    if (opts.explicitRoot) {
      return opts.explicitRoot;
    }

    // TODO: 如果当前目录下有 .lybra/connection.json 且含 workspace_root,用它
    // 当前简化实现:仅从env读取

    // env 覆盖
    const envRoot = env.LYBRA_WORKSPACE_ROOT?.trim();
    if (envRoot) {
      return envRoot;
    }

    return null;
  }

  /**
   * 解析 code repo (产品仓语义)
   * 用途: finalize/worktree/git 操作 — 需要产品仓路径,不能是治理仓
   * Precedence: 显式参数 → .lybra/connection.json → env仅覆盖
   * AIPOS-R6H + R6P 靶③: **拒绝治理仓** (ai-project-os)
   */
  static resolveCodeRepo(opts: {
    env?: Record<string, string | undefined>;
    explicitRoot?: string;
  }): string | null {
    const env = opts.env ?? process.env;

    // 显式参数
    if (opts.explicitRoot) {
      // 校验不是治理仓
      if (opts.explicitRoot.includes("ai-project-os")) {
        throw new Error(
          `code repo cannot be governance repo (ai-project-os): ${opts.explicitRoot}. ` +
          "Use product repo path for finalize/worktree operations."
        );
      }
      return opts.explicitRoot;
    }

    // TODO: 如果当前目录下有 .lybra/connection.json 且含 workspace_root,用它
    // 当前简化实现:仅从env读取

    // env 覆盖
    const envRoot = env.LYBRA_WORKSPACE_ROOT?.trim();
    if (envRoot) {
      // AIPOS-R6H: 校验不是治理仓 (治理仓路径通常含 ai-project-os)
      if (envRoot.includes("ai-project-os")) {
        throw new Error(
          `code repo cannot be governance repo (ai-project-os): ${envRoot}. ` +
          "Use product repo path for finalize/worktree operations."
        );
      }
      return envRoot;
    }

    return null;
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
