/**
 * AIPOS-R1: LoopContext + ConnectionResolver (TS 实现)
 * 
 * 设计权威: DESIGN v2 §3
 * 
 * LoopContext: 解析一次贯穿动词的不可变上下文
 * ConnectionResolver: 连接→token解析器 (precedence: 显式 → env → 自发现)
 * 
 * 与 tools/loop_context.py 同构, 以 schema/conformance/ 夹具锁定一致性。
 */

import { readFileSync, existsSync, realpathSync } from "node:fs";
import { join, parse } from "node:path";
import { createHash } from "node:crypto";

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
    projects?: string[];
    retired?: boolean;
  }>;
}

/**
 * AIPOS-F81: connection.json tokens[] 条目字段名。
 * 来源: tools/aipos_cli/token_resolver.py::TOKEN_ENTRY_FIELDS (产品无 schema 声明这些字段, Python 侧为唯一声明;
 * 本常量逐键同名同值, tests/test_aipos_f81_token_single_source.py 比对锁定, 禁在 TS 侧另改)。
 */
export const TOKEN_ENTRY_FIELDS = {
  token: "token",
  role: "role",
  agent_instance: "agent_instance",
  projects: "projects",
  retired: "retired",
} as const;

/** AIPOS-F81: 全 retired 拒因出口。来源: tools/aipos_cli/token_resolver.py::TOKEN_REENROLL_EXIT (同构夹具锁定)。 */
export const TOKEN_REENROLL_EXIT = "lybra roles enroll --code <码> --workspace <工位根>";

/** AIPOS-F81: token 解析失败基类 (对应 Python token_resolver.TokenResolutionError)。 */
export class TokenResolutionError extends Error {}
/** 无条目命中选择器 —— 调用方可落下一层 (env 兜底)。对应 Python TokenNotFoundError。 */
export class TokenNotFoundError extends TokenResolutionError {}
/** 命中条目全部 retired —— fail-closed, 禁落下一层, 拒因带重签出口。对应 Python TokenAllRetiredError。 */
export class TokenAllRetiredError extends TokenResolutionError {}

function entryFingerprint(token: unknown): string {
  const t = typeof token === "string" ? token : "";
  if (!t) return "(none)";
  return "sha256:" + createHash("sha256").update(t, "utf-8").digest("hex").slice(0, 12);
}

/**
 * AIPOS-F81: 从 tokens[] 挑一条条目 —— 与 Python tools/aipos_cli/token_resolver.py::select_token_entry 同一判据
 * (TS 侧只移植连接器用到的 instance/role 两阶段, 无 project/any_role 选择器):
 *  1. agent_instance 匹配 (最具体) → role 匹配;
 *  2. 每阶段只取可用条目: 非 retired 且 token 非空; 首个有候选的阶段返回第一条;
 *  3. 都没有: 无命中 = TokenNotFoundError; 命中全 retired = TokenAllRetiredError (带重签出口);
 *     其余 (token 空) = TokenResolutionError。拒因只带指纹, 永不带 token 值。
 */
export function selectTokenEntry(
  tokens: unknown[],
  opts: { role?: string | null; agentInstance?: string | null; source?: string },
): Record<string, unknown> {
  const F = TOKEN_ENTRY_FIELDS;
  const source = opts.source ?? "connection.json";
  const stages: Array<[string, (e: Record<string, unknown>) => boolean]> = [];
  if (opts.agentInstance) stages.push([`agent_instance='${opts.agentInstance}'`, (e) => e[F.agent_instance] === opts.agentInstance]);
  if (opts.role) stages.push([`role='${opts.role}'`, (e) => e[F.role] === opts.role]);
  const selector = stages.map(([label]) => label).join(" → ") || "(no selector)";
  if (stages.length === 0) {
    throw new TokenNotFoundError(`No token selector (role/agent_instance) given for ${source}`);
  }
  const matched: Array<Record<string, unknown>> = [];
  for (const [, pred] of stages) {
    const hits = tokens.filter(
      (e): e is Record<string, unknown> => typeof e === "object" && e !== null && !Array.isArray(e) && pred(e as Record<string, unknown>),
    );
    for (const e of hits) if (!matched.includes(e)) matched.push(e);
    const usable = hits.filter((e) => !e[F.retired] && typeof e[F.token] === "string" && (e[F.token] as string).trim() !== "");
    if (usable.length > 0) return usable[0];
  }
  if (matched.length === 0) {
    throw new TokenNotFoundError(`No token found for ${selector} in ${source}`);
  }
  if (matched.every((e) => !!e[F.retired])) {
    const fps = matched.map((e) => entryFingerprint(e[F.token])).join(", ");
    throw new TokenAllRetiredError(
      `All ${matched.length} token entr${matched.length === 1 ? "y" : "ies"} for ${selector} in ${source} ` +
      `are retired (fingerprints: ${fps}) — no active credential. ` +
      `出口: 重签凭据 \`${TOKEN_REENROLL_EXIT}\` (Owner 签发新 enrollment 码)`,
    );
  }
  throw new TokenResolutionError(`Token entry for ${selector} exists in ${source} but has no token value`);
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
  error?: string;          // AIPOS-F81: 工位层 fail-closed 拒因 (如命中 token 全 retired, 带重签出口); 不含 token 值
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
    // AIPOS-F81: 挑选走 selectTokenEntry (与 Python token_resolver 同判据: instance → role, 排除 retired);
    // 无命中 (TokenNotFoundError) 才落 env 兜底; 命中全 retired / 文件坏 = 抛出 (fail-closed, 拒因带重签出口)。
    let workstationMiss = ".lybra/connection.json not found";
    const lybraDir = this.discoverLybraDir();
    if (lybraDir && existsSync(join(lybraDir, "connection.json"))) {
      const config = this.loadConnectionConfig(lybraDir);
      const tokens = config.tokens;
      if (!Array.isArray(tokens)) {
        throw new TokenResolutionError(`connection.json at ${join(lybraDir, "connection.json")} has no tokens list`);
      }
      try {
        const entry = selectTokenEntry(tokens, {
          role: opts.role,
          agentInstance: opts.agentInstance,
          source: join(lybraDir, "connection.json"),
        });
        return String(entry[TOKEN_ENTRY_FIELDS.token]).trim();
      } catch (e) {
        if (!(e instanceof TokenNotFoundError)) throw e;
        workstationMiss = e.message; // 本层无命中: 按声明优先级落到 env 兜底
      }
    }

    // 环境变量覆盖 (最低优先级)
    const envToken = env.LYBRA_TOKEN?.trim();
    if (envToken) {
      return envToken;
    }

    throw new Error(
      `Cannot resolve token for role=${opts.role}, agentInstance=${opts.agentInstance} (${workstationMiss}). ` +
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

    // --- token: 显式 → .lybra/connection.json.tokens (instance 匹配 → role 匹配, 排除 retired) → env ---
    if (ex.token) {
      token.value = ex.token; token.source = "explicit"; token.envDowngraded = !!envToken;
    } else {
      // AIPOS-F81: 挑选走 selectTokenEntry (与 Python token_resolver 同判据: instance → role, 排除 retired)。
      // 无命中才落 env 兜底; 命中全 retired = value null + error (带重签出口), 禁 env 静默顶替 (fail-closed)。
      const tokens = conn?.tokens;
      let matched: string | null = null;
      let tokenError: string | null = null;
      if (Array.isArray(tokens)) {
        try {
          const entry = selectTokenEntry(tokens, {
            role: role.value,
            agentInstance: agentInstance.value,
            source: lybraDir ? join(lybraDir, "connection.json") : ".lybra/connection.json",
          });
          matched = String(entry[TOKEN_ENTRY_FIELDS.token]);
        } catch (e) {
          if (e instanceof TokenNotFoundError) matched = null;
          else if (e instanceof TokenResolutionError) tokenError = e.message;
          else throw e;
        }
      }
      if (matched) {
        token.value = matched; token.source = ".lybra/connection.json"; token.envDowngraded = !!envToken;
      } else if (tokenError) {
        token.error = tokenError; token.envDowngraded = !!envToken;
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
