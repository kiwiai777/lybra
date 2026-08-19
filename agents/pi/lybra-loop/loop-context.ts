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

import { readFileSync, existsSync, realpathSync } from "node:fs";
import { join, parse } from "node:path";

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

/**
 * AIPOS-C2 大项C: 单个身份/连接键的解析结果 + 来源自曝 (provenance)。
 * source 取值: explicit / .lybra/role / .lybra/actor / .lybra/policy /
 *              .lybra/connection.json / env:<VAR> / schema:<ref> / unresolved。
 */
export interface ResolvedKey {
  key: string;
  value: string | null;
  source: string;
  viaEnv: boolean;         // 值最终取自 env 兜底 (横幅标 ⚠)
  envDowngraded: boolean;  // env 有值但被更高层压过 (横幅标 ⚠)
}

export interface IdentityResolution {
  role: ResolvedKey;
  actor: ResolvedKey;
  agentInstance: ResolvedKey;
  ownerPolicyRef: ResolvedKey;
  token: ResolvedKey;
  workspaceRoot: ResolvedKey;
  gateUrl: ResolvedKey;
}

export interface IdentityResolutionOptions {
  env?: Record<string, string | undefined>;
  explicit?: {
    role?: string;
    actor?: string;
    agentInstance?: string;
    ownerPolicyRef?: string;
    token?: string;
    workspaceRoot?: string;
    gateUrl?: string;
  };
  schemaGateUrl?: string; // config.schema urls.gate_local (gate_url 唯一 schema 缺省)
}

export class ConnectionResolver {
  /**
   * 自动发现 .lybra/ 目录
   * AIPOS-R6Q 靶①: 从会话 cwd 向上查找工位 .lybra (不从治理仓 workspaceRoot 找)
   * 确保同机多角色各自锚定自己的工位,不会混成同一身份
   */
  static discoverLybraDir(startDir?: string): string | null {
    let currentDir = startDir ? realpathSync(startDir) : process.cwd();
    const root = parse(currentDir).root;

    while (currentDir !== root) {
      const lybraDir = join(currentDir, ".lybra");
      if (existsSync(lybraDir)) {
        return lybraDir;
      }
      // 向上一级
      const parentDir = join(currentDir, "..");
      currentDir = realpathSync(parentDir);
      if (currentDir === root) break;
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
   * AIPOS-R6Q 靶①: 自发现从 cwd 向上查找工位 .lybra
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

    // 自动发现 .lybra/ (优先级高于env) - 从 cwd 向上查找
    const lybraDir = this.discoverLybraDir();
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
   * AIPOS-R6Q 靶①: 自发现从 cwd 向上查找工位 .lybra
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

    // 自动发现 .lybra/connection.json (优先级高于env) - 从 cwd 向上查找
    const lybraDir = this.discoverLybraDir();
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
   * AIPOS-R6Q 靶①: 自发现从 cwd 向上查找工位 .lybra
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

    // 自动发现 .lybra/role (JSON格式含instance) - 从 cwd 向上查找
    const lybraDir = this.discoverLybraDir();
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
   * AIPOS-R6Q 靶①: 自发现从 cwd 向上查找工位 .lybra
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

    // 自动发现 .lybra/role (JSON格式含owner_policy_ref) - 从 cwd 向上查找
    const lybraDir = this.discoverLybraDir();
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
   * Precedence: 显式参数 → .lybra/connection.json (workspace_root) → env仅覆盖
   * AIPOS-R6P 靶③: **允许治理仓** (ai-project-os),不做路径校验
   * AIPOS-R6Q 靶②: 真实现(工位 .lybra/connection.json → 项目配置 → env覆盖)
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

    // 从工位 .lybra/connection.json 读取 workspace_root
    const lybraDir = this.discoverLybraDir();
    if (lybraDir) {
      try {
        const config = this.loadConnectionConfig(lybraDir);
        const workspaceRoot = config.workspace_root;
        if (workspaceRoot) {
          return workspaceRoot;
        }
      } catch {
        // 自发现失败, 继续
      }
    }

    // env 覆盖 (最低优先级)
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
   * AIPOS-R6Q 靶②: 真实现(工位 .lybra/connection.json → 项目配置 → env覆盖)
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

    // 从工位 .lybra/connection.json 读取 workspace_root (但拒绝治理仓)
    const lybraDir = this.discoverLybraDir();
    if (lybraDir) {
      try {
        const config = this.loadConnectionConfig(lybraDir);
        const workspaceRoot = config.workspace_root;
        if (workspaceRoot) {
          // 校验不是治理仓
          if (workspaceRoot.includes("ai-project-os")) {
            // 治理仓路径跳过,不抛错(因为可能是审计工位等合法治理仓工位)
            // 继续尝试 env
          } else {
            return workspaceRoot;
          }
        }
      } catch {
        // 自发现失败, 继续
      }
    }

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

  /**
   * AIPOS-C2 大项A/C: 一次性解析全部身份/连接键, 带来源自曝 (provenance)。
   *
   * 声明权威: schema/config.schema.json#identity_resolution (config.schema 是身份配置域唯一真相)。
   * 总序: 显式参数 → 工位 .lybra (role 文件 + connection.json) → env (仅兜底)。
   *
   * 铁律:
   *  • role/actor/agent_instance/owner_policy_ref 从同一次 .lybra/role 加载 (同源, 不劈叉)。
   *  • 无静默缺省 —— 解析不到 value=null, 由调用方 (loadConfig) 出声并停。
   *  • env 兜底命中 / env 被降级 → viaEnv / envDowngraded 置位, 横幅标 ⚠。
   */
  static resolveIdentity(opts: IdentityResolutionOptions = {}): IdentityResolution {
    const env = opts.env ?? process.env;
    const ex = opts.explicit ?? {};
    const schemaGateUrl = (opts.schemaGateUrl ?? "http://127.0.0.1:7118").replace(/\/mcp$/, "");

    const mk = (key: string): ResolvedKey => ({
      key,
      value: null,
      source: "unresolved",
      viaEnv: false,
      envDowngraded: false,
    });

    const role = mk("role");
    const actor = mk("actor");
    const agentInstance = mk("agent_instance");
    const ownerPolicyRef = mk("owner_policy_ref");
    const token = mk("token");
    const workspaceRoot = mk("workspace_root");
    const gateUrl = mk("gate_url");

    // 一次自发现 + 一次加载 (工位 .lybra): role/actor/instance/policy 同源于此。
    const lybraDir = this.discoverLybraDir();
    let roleData: { role?: string; instance?: string; owner_policy_ref?: string } | null = null;
    let conn: ConnectionConfig | null = null;
    let actorText: string | null = null;
    let policyText: string | null = null;
    if (lybraDir) {
      roleData = this.loadRoleFile(lybraDir);
      try {
        conn = this.loadConnectionConfig(lybraDir);
      } catch {
        conn = null;
      }
      try {
        const actorFile = join(lybraDir, "actor");
        if (existsSync(actorFile)) actorText = readFileSync(actorFile, "utf-8").trim() || null;
      } catch {
        actorText = null;
      }
      try {
        const policyFile = join(lybraDir, "policy");
        if (existsSync(policyFile)) policyText = readFileSync(policyFile, "utf-8").trim() || null;
      } catch {
        policyText = null;
      }
    }

    const envRole = env.LYBRA_ROLE?.trim();
    const envActor = env.LYBRA_ACTOR?.trim();
    const envInstance = env.LYBRA_AGENT_INSTANCE?.trim();
    const envPolicy = env.LYBRA_OWNER_POLICY_REF?.trim();
    const envToken = env.LYBRA_TOKEN?.trim();
    const envRoot = env.LYBRA_WORKSPACE_ROOT?.trim();
    const envGateUrl = env.LYBRA_GATE_URL?.trim();

    // --- role: 显式 → .lybra/role.role → env (无缺省) ---
    if (ex.role) {
      role.value = ex.role; role.source = "explicit"; role.envDowngraded = !!envRole;
    } else if (roleData?.role) {
      role.value = roleData.role; role.source = ".lybra/role"; role.envDowngraded = !!envRole;
    } else if (envRole) {
      role.value = envRole; role.source = "env:LYBRA_ROLE"; role.viaEnv = true;
    }

    // --- actor: 显式 → .lybra/role.instance → .lybra/actor → env ---
    if (ex.actor) {
      actor.value = ex.actor; actor.source = "explicit"; actor.envDowngraded = !!envActor;
    } else if (roleData?.instance) {
      actor.value = roleData.instance; actor.source = ".lybra/role"; actor.envDowngraded = !!envActor;
    } else if (actorText) {
      actor.value = actorText; actor.source = ".lybra/actor"; actor.envDowngraded = !!envActor;
    } else if (envActor) {
      actor.value = envActor; actor.source = "env:LYBRA_ACTOR"; actor.viaEnv = true;
    }

    // --- agent_instance: 显式 → .lybra/role.instance → env LYBRA_AGENT_INSTANCE → 回退 actor(同一身份名) ---
    if (ex.agentInstance) {
      agentInstance.value = ex.agentInstance; agentInstance.source = "explicit"; agentInstance.envDowngraded = !!envInstance;
    } else if (roleData?.instance) {
      agentInstance.value = roleData.instance; agentInstance.source = ".lybra/role"; agentInstance.envDowngraded = !!envInstance;
    } else if (envInstance) {
      agentInstance.value = envInstance; agentInstance.source = "env:LYBRA_AGENT_INSTANCE"; agentInstance.viaEnv = true;
    } else if (actor.value) {
      agentInstance.value = actor.value;
      agentInstance.source = actor.source;
      agentInstance.viaEnv = actor.viaEnv;
      agentInstance.envDowngraded = actor.envDowngraded;
    }

    // --- owner_policy_ref: 显式 → .lybra/role.owner_policy_ref → .lybra/policy → env ---
    if (ex.ownerPolicyRef) {
      ownerPolicyRef.value = ex.ownerPolicyRef; ownerPolicyRef.source = "explicit"; ownerPolicyRef.envDowngraded = !!envPolicy;
    } else if (roleData?.owner_policy_ref) {
      ownerPolicyRef.value = roleData.owner_policy_ref; ownerPolicyRef.source = ".lybra/role"; ownerPolicyRef.envDowngraded = !!envPolicy;
    } else if (policyText) {
      ownerPolicyRef.value = policyText; ownerPolicyRef.source = ".lybra/policy"; ownerPolicyRef.envDowngraded = !!envPolicy;
    } else if (envPolicy) {
      ownerPolicyRef.value = envPolicy; ownerPolicyRef.source = "env:LYBRA_OWNER_POLICY_REF"; ownerPolicyRef.viaEnv = true;
    }

    // --- workspace_root: 显式 → .lybra/connection.json.workspace_root → env ---
    const connRoot = conn?.workspace_root;
    if (ex.workspaceRoot) {
      workspaceRoot.value = ex.workspaceRoot; workspaceRoot.source = "explicit"; workspaceRoot.envDowngraded = !!envRoot;
    } else if (connRoot) {
      workspaceRoot.value = connRoot; workspaceRoot.source = ".lybra/connection.json"; workspaceRoot.envDowngraded = !!envRoot;
    } else if (envRoot) {
      workspaceRoot.value = envRoot; workspaceRoot.source = "env:LYBRA_WORKSPACE_ROOT"; workspaceRoot.viaEnv = true;
    }

    // --- gate_url: 显式 → .lybra/connection.json.mcp.rpc_url → env → schema 缺省 (urls.gate_local) ---
    const connGateUrl = conn?.mcp?.rpc_url;
    if (ex.gateUrl) {
      gateUrl.value = ex.gateUrl; gateUrl.source = "explicit"; gateUrl.envDowngraded = !!envGateUrl;
    } else if (connGateUrl) {
      gateUrl.value = connGateUrl; gateUrl.source = ".lybra/connection.json"; gateUrl.envDowngraded = !!envGateUrl;
    } else if (envGateUrl) {
      gateUrl.value = envGateUrl; gateUrl.source = "env:LYBRA_GATE_URL"; gateUrl.viaEnv = true;
    } else {
      gateUrl.value = schemaGateUrl; gateUrl.source = "schema:urls.gate_local";
    }

    // --- token: 显式 → .lybra/connection.json.tokens (instance 匹配 → role 匹配) → env ---
    if (ex.token) {
      token.value = ex.token; token.source = "explicit"; token.envDowngraded = !!envToken;
    } else {
      const tokens = conn?.tokens;
      const ai = agentInstance.value;
      const rl = role.value;
      let matched: string | null = null;
      if (Array.isArray(tokens)) {
        if (ai) {
          for (const t of tokens) {
            if (t.agent_instance === ai && t.token) { matched = t.token; break; }
          }
        }
        if (!matched && rl) {
          for (const t of tokens) {
            if (t.role === rl && t.token) { matched = t.token; break; }
          }
        }
      }
      if (matched) {
        token.value = matched; token.source = ".lybra/connection.json"; token.envDowngraded = !!envToken;
      } else if (envToken) {
        token.value = envToken; token.source = "env:LYBRA_TOKEN"; token.viaEnv = true;
      }
    }

    return { role, actor, agentInstance, ownerPolicyRef, token, workspaceRoot, gateUrl };
  }

  /**
   * AIPOS-C2: 解析 role (与 actor 同源)。无静默缺省 —— 解析不到返回 null, 由调用方出声并停。
   * Precedence: 显式 → .lybra/role.role → env:LYBRA_ROLE (仅兜底)。
   */
  static resolveRole(opts: {
    env?: Record<string, string | undefined>;
    explicitRole?: string;
  }): string | null {
    return this.resolveIdentity({ env: opts.env, explicit: { role: opts.explicitRole } }).role.value;
  }
}
