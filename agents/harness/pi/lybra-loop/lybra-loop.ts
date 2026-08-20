/**
 * lybra-loop —— pi 扩展本体:让 lybra-executor 自动【发起】领卡循环。
 *
 * 命令(命名对齐 _shared/LYBRA-NAMING.md + 产品 skills/lybra-executor/SKILL.md):
 *   /lybra on [maxN]   启动循环(默认 max 1 张,防失控)
 *   /lybra off         停止循环
 *   /lybra status      查看状态
 *   /lybra-tick        (手动)立即执行一轮 tick;自动链不依赖命令路由
 *
 * 它只做"发起",不做"授权"(红线):claim 放行与否永远由 gate 判定(AIPOS-250 信封)——
 *   • 信封内(PreAuthorized)→ gate 一阶段落盘 claim → 扩展冷启动执行
 *   • 信封外(Supervised,owner_confirmation_required=true)→ 跳过记录,绝不自动 confirm
 *   • BLOCK/失败 → 循环立停
 *
 * 机制要点(详见 DESIGN.md):
 *   • 连接器面:node http 直连 gate /mcp(gate-client.ts),不 shell 调 Python CLI。
 *   • gate 被动:轮询宿主全在 agent 侧(非阻塞 followUp 链),gate 不推送/不唤醒。
 *   • 一卡一会话:claim 放行 → liveCtx.newSession 冷启动(同 _shared/extensions/claim.ts)。
 *   • 跨 session 状态:模块级 loopState(ESM 缓存跨 session 替换存活)+ agent_settled 续跑。
 *   • F-EXT001-4(FIX1):tick 机制 — **直接函数调用**,不经 sendUserMessage/命令路由
 *     (pi 的 sendUserMessage 永不触发命令,只落文本给模型;定时器/生命周期钩子直接调 doTick,
 *     零 LLM 参与、零上下文污染;/lybra-tick 命令仅作手动触发入口)。
 *   • AIPOS-F10(ctx 生命周期修真):全部 ctx 消费点统一到 claim.ts 范式 ——
 *     模块级 liveCtx/livePi(每次 session 装载/事件/命令入口刷新),定时器/钩子绝不闭包
 *     捕获 ctx(那些在 session 替换后变 stale 对象)。三病象根治:
 *     ①冷启动 newSession 摔 → 定时器用 liveCtx(由 session_start 刷新)
 *     ②/reload 后 stale → reload 清定时器,session_start 用新 ctx 重调度
 *     ③复工 ctx.reply 摔 → 改用 sendUserMessage(对齐 claim.ts);session_start 取第二参数 ctx
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadConfig, ConfigError, GateMcpClient, loadVerbCatalog, validateRequiredVerbs, type LoopConfig } from "./gate-client.ts";
import { buildKickoff, stringifyReasons, severityToLevel } from "./loop-decisions.ts";
import { executeTick, freshState, Logger, type LoopState } from "./loop-engine.ts";
// AIPOS-C4B 大项B: 版本信号 — 本地版本戳读取 + 清单比对
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// 模块级:跨 session 替换存活(ESM 模块缓存,同进程唯一)。对齐 claim.ts 的 pendingModel 原理。
let loopState: LoopState = freshState();
let currentGateUrl = "";
let currentRole = "";
let currentActor = "";
let currentTokenFp = "(none)";
let currentClient: GateMcpClient | null = null;
let currentLogger: Logger | null = null;
let pendingTimer: ReturnType<typeof setTimeout> | null = null;
// release 触发的 newSession 是循环自驱的,session_shutdown 不应据此停循环(区别于用户 /new)。
let expectingSwap = false;
// AIPOS-CONN-LOOP-2 ①: 跟踪当前执行的任务ID和worktree路径，用于自动return
let currentTaskId: string | null = null;
let currentWorktreePath: string | null = null;

// AIPOS-F10: 活 ctx/pi 引用 — 每次 session 装载/命令入口/事件回调时刷新。
// 定时器/钩子绝不闭包捕获 ctx(那些在 session 替换后变 stale 对象,调用即抛)。
// 对齐 claim.ts 范式:只用 withSession 回调的 freshCtx 或 session_start 的新 ctx。
let livePi: ExtensionAPI | null = null;
let liveCtx: any = null;

// F-EXT001-6(FIX2): 默认日志路径迁移到 Lybra 产品仓任务卡目录(旧 contrib 路径已废弃)
const LOG_PATH_DEFAULT = `${process.env.HOME || ""}/projects/lybra/task_cards/LYBRA-EXT-001/loop.log`;

// AIPOS-R6R: 连接器依赖的 verb + 必填参数清单(单一声明, 启动时对 schema 校验)。
// 动词名/参数名只此一处声明, 不再逐动词手写方法; 校验读 schema/verbs.schema.json。
const REQUIRED_VERBS: Record<string, string[]> = {
  lybra_queue_list: [],
  lybra_task_preview: [],
  lybra_queue_claim_dry_run: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
  lybra_queue_return_dry_run: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
  lybra_queue_return_confirm: ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
  lybra_queue_close_dry_run: ["task_id", "actor", "closure_evidence"],
  lybra_queue_close_confirm: ["task_id", "actor", "closure_evidence"],
};

/**
 * AIPOS-C2 大项C: 来源自曝横幅 —— 对每个关键值打印取自哪一层。
 * env 兜底命中 / env 被降级 → 标 ⚠ (2026-08-18 若有此横幅, 毒 env 案第一张截图即破)。
 */
function buildProvenanceBanner(config: LoopConfig): string {
  const order = ["role", "actor", "agent_instance", "owner_policy_ref", "workspace_root", "gate_url", "token"];
  const lines: string[] = [];
  for (const key of order) {
    const p = config.provenance?.[key];
    if (!p) continue;
    let src = p.source;
    if (p.viaEnv) src = `${src} ⚠兜底`;
    if (p.envDowngraded) src = `${src} ⚠env被降级`;
    lines.push(`  ${key}=${p.value} (${src})`);
  }
  return lines.join("\n");
}

/**
 * AIPOS-C4B 大项B: 版本信号。
 *
 * 连接器版本戳 = 分发器生成的源 commit 短哈希(写在 _distributed/.version-{role}
 * 的 version 字段), 取代顾问手工注入的 dist-2026xxxx 戳(已退役)。
 * 连接器模块位于 _distributed/extensions/lybra-loop/, 上溯三级即 _distributed/。
 */
function distRoot(): string {
  try {
    return dirname(dirname(dirname(fileURLToPath(import.meta.url))));
  } catch {
    return process.cwd();
  }
}

function readLocalVersion(role: string): string | null {
  try {
    const p = join(distRoot(), `.version-${role}`);
    if (!existsSync(p)) return null;
    const data = JSON.parse(readFileSync(p, "utf-8"));
    const v = data && typeof data === "object" ? (data as { version?: unknown }).version : null;
    return typeof v === "string" && v ? v : null;
  } catch {
    return null;
  }
}

function buildVersionLine(config: LoopConfig): string {
  const local = readLocalVersion(config.role);
  return `lybra-loop 版本: ${local ?? "(无版本戳 — 请 lybra sync)"}`;
}

/**
 * AIPOS-C4B 大项B: 清单比对(提示级, 绝不拒跑)。
 * 落后 → behind=true, 出声"落后, 请 lybra sync + /reload"; 不落后 → null。
 * gate 无此动词 / 连接失败 → error 非空, 但循环照跑。
 */
async function checkManifestFreshness(
  client: GateMcpClient | null,
  config: LoopConfig,
): Promise<{ behind: boolean; local: string | null; remote: string | null; error?: string }> {
  const local = readLocalVersion(config.role);
  if (!client) return { behind: false, local, remote: null };
  try {
    const m = await client.callTool("lybra_distribution_manifest", {});
    const remote = typeof (m as { product_commit?: unknown }).product_commit === "string"
      ? (m as { product_commit: string }).product_commit
      : null;
    return { behind: !!(local && remote && local !== remote), local, remote };
  } catch (e) {
    return { behind: false, local, remote: null, error: e instanceof Error ? e.message : String(e) };
  }
}

function clearTimer() {
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
}

// AIPOS-F10:定时器不捕获 ctx/pi — 每次 tick 从 liveCtx/livePi 取当前活引用。
// 根治 /reload 后定时器持 stale ctx 导致的 "extension ctx is stale" 静死。
function scheduleNextTick(delayMs: number) {
  clearTimer();
  pendingTimer = setTimeout(() => {
    pendingTimer = null;
    if (!loopState.on) return; // 期间被 off
    // F-EXT001-4(FIX1):直接调用 tick 函数,不经 sendUserMessage(永不触发命令)
    doTick().catch((e) => {
      // F-EXT001-8(FIX4):不再静默吞错,落日志
      currentLogger?.warn("scheduleNextTick-doTick-error", {
        error: e instanceof Error ? e.message : String(e),
      });
    });
  }, delayMs);
}

/**
 * AIPOS-C3B 大项D③: sweep 候选集反转 — 从 queue/claimed(0-2 张)反查裁决,
 * 替代遍历 151 裁决目录。出声=一行汇总+仅异常逐条。
 *
 * AIPOS-C3B 大项B①: 裁决选取按 frontmatter 时间戳取最新, 禁按文件名排序。
 * AIPOS-C3B 大项B②: 只认门生记录(具备 record_type/verdict_id/verdict_at 机器特征),
 *   手写文件忽略+warn。
 */

/** 从 frontmatter 文本提取简单 YAML 标量值(不引完整 YAML 解析器) */
function extractFrontmatterField(content: string, field: string): string | null {
  // Match both `field: value` and `field: 'value'` / `field: "value"`
  const re = new RegExp(`^${field}:\\s*['"]?([^'"\\n]*)['"]?\\s*$`, "m");
  const m = content.match(re);
  return m ? m[1].trim() : null;
}

/**
 * AIPOS-C3B 大项B②: 检查裁决文件是否具备门生标记(record_type + verdict_id + verdict_at)。
 * 手写文件(缺少机器特征)返回 false。
 */
function isGateBornVerdict(content: string): { authentic: boolean; reason?: string } {
  const recordType = extractFrontmatterField(content, "record_type");
  const verdictId = extractFrontmatterField(content, "verdict_id");
  const verdictAt = extractFrontmatterField(content, "verdict_at");
  if (!recordType || !verdictId || !verdictAt) {
    return { authentic: false, reason: `缺少门生标记(record_type=${!!recordType}, verdict_id=${!!verdictId}, verdict_at=${!!verdictAt})` };
  }
  if (recordType !== "audit_verdict_record") {
    return { authentic: false, reason: `record_type=${recordType}(预期 audit_verdict_record)` };
  }
  return { authentic: true };
}

/**
 * AIPOS-C3B 大项B①: 从裁决目录中按 frontmatter verdict_at 时间戳选取最新门生裁决。
 * 禁按文件名排序。手写文件(缺门生标记)忽略+warn。
 */
function selectLatestGateVerdict(
  fs: any,
  verdictDir: string,
  logger: Logger | null,
  taskId: string,
): { verdict: string; verdictAt: string; verdictId: string; filePath: string } | null {
  let files: string[];
  try {
    files = fs.readdirSync(verdictDir)
      .filter((f: string) => f.endsWith(".md"));
  } catch {
    return null;
  }
  if (files.length === 0) return null;

  const candidates: { verdict: string; verdictAt: string; verdictId: string; filePath: string }[] = [];
  const rejected: string[] = [];

  for (const f of files) {
    const filePath = `${verdictDir}/${f}`;
    let content: string;
    try {
      content = fs.readFileSync(filePath, "utf-8");
    } catch {
      rejected.push(`${f}: 读取失败`);
      continue;
    }
    const auth = isGateBornVerdict(content);
    if (!auth.authentic) {
      rejected.push(`${f}: ${auth.reason}`);
      continue;
    }
    const verdictVal = extractFrontmatterField(content, "verdict") || "";
    const verdictAt = extractFrontmatterField(content, "verdict_at") || "";
    const verdictId = extractFrontmatterField(content, "verdict_id") || f.replace(/\.md$/, "");
    if (!verdictVal) {
      rejected.push(`${f}: verdict 字段缺失`);
      continue;
    }
    candidates.push({ verdict: verdictVal.toUpperCase(), verdictAt, verdictId, filePath });
  }

  if (rejected.length > 0) {
    logger?.warn("sweep-rejected-verdicts", { task_id: taskId, rejected });
  }
  if (candidates.length === 0) return null;

  // AIPOS-C3B 大项B①: 按 verdict_at 时间戳取最新(禁按文件名排序)
  candidates.sort((a, b) => (a.verdictAt > b.verdictAt ? -1 : a.verdictAt < b.verdictAt ? 1 : 0));
  return candidates[0];
}

// AIPOS-C3D 大项A: sweep 防重入标志 — 启动存量收敛与每轮 tick 可能并发触发同一 sweep,
// 双 finalize 会引发 deploy 竞态;单线程下 check+set 无 await 间隔, 原子生效。
let sweepInFlight = false;

// AIPOS-F10:不接收 ctx — 内部从 liveCtx 取活引用。
async function tryAutoFinalizeOnPassVerdict(): Promise<boolean> {
  if (sweepInFlight) {
    currentLogger?.info("sweep-skip-inflight", {});
    return false;
  }
  sweepInFlight = true;
  let result = false;
  try {
    result = await tryAutoFinalizeOnPassVerdictCore();
  } catch (e) {
    // 保底: core 内部应自洽不抛, 意外抛了也落日志并复位标志(不静默吞)
    currentLogger?.warn("sweep-unexpected-error", {
      error: e instanceof Error ? e.message : String(e),
    });
  }
  sweepInFlight = false;
  return result;
}

// AIPOS-F10:不接收 ctx — 内部从 liveCtx 取活引用。
async function tryAutoFinalizeOnPassVerdictCore(): Promise<boolean> {
  if (!currentClient || !currentLogger) {
    currentLogger?.warn("sweep-no-client", { reason: "currentClient or currentLogger not initialized" });
    liveCtx?.ui?.notify?.("sweep 跳过:客户端或日志器未初始化", "warn");
    return false;
  }

  let config;
  try {
    config = loadConfig(process.env);
  } catch (e) {
    const msg = `sweep 配置加载失败: ${e instanceof Error ? e.message : String(e)}`;
    currentLogger.warn("sweep-config-failed", { error: String(e) });
    liveCtx?.ui?.notify?.(msg, "error");
    return false;
  }

  const fs = await import("node:fs");
  const path = await import("node:path");

  // AIPOS-R6S 大项A②: sweep 执行范围按角色能力判定(roles.schema scopes)。
  if (config.role !== "executor") {
    const msg = `sweep 跳过: 当前角色 ${config.role || "?"} 不具 finalize/close 能力(roles.schema scopes), 不跑 finalize`;
    currentLogger.info("sweep-skip-role", { role: config.role });
    liveCtx?.ui?.notify?.(msg, "info");
    return false;
  }

  // AIPOS-C3B 大项D③: 候选集反转 — 从 queue/claimed 反查裁决(替代遍历 151 裁决目录)
  const claimedDir = path.join(config.workspaceRoot, "5_tasks/queue/claimed");
  if (!fs.existsSync(claimedDir)) {
    currentLogger.info("sweep-no-claimed-dir", {});
    liveCtx?.ui?.notify?.("sweep: claimed 队列为空", "info");
    return false;
  }

  const claimedCards = fs.readdirSync(claimedDir)
    .filter((f: string) => f.endsWith(".md"));

  if (claimedCards.length === 0) {
    currentLogger.info("sweep-no-claimed-cards", {});
    liveCtx?.ui?.notify?.("sweep: 无 claimed 卡", "info");
    return false;
  }

  currentLogger.info("sweep-start", { claimed_count: claimedCards.length });

  let processedCount = 0;
  const anomalies: { task_id: string; reason: string }[] = [];

  for (const cardFile of claimedCards) {
    // 从卡文件名提取 task_id
    const taskId = cardFile.replace(/\.md$/i, "").toUpperCase();
    const cardPath = path.join(claimedDir, cardFile);

    // 反查裁决目录
    const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", taskId);
    if (!fs.existsSync(verdictDir)) {
      // 无裁决目录 = 还在执行中,不是异常
      continue;
    }

    // AIPOS-C3B 大项B①②: 按时间戳选最新门生裁决
    const latest = selectLatestGateVerdict(fs, verdictDir, currentLogger, taskId);
    if (!latest) {
      anomalies.push({ task_id: taskId, reason: "裁决目录存在但无门生裁决" });
      continue;
    }
    if (latest.verdict !== "PASS" && latest.verdict !== "PASS_WITH_NOTES") {
      // 非 PASS 裁决 = 还在审计循环中,不是异常
      continue;
    }

    // 检查是否已 close
    const closureDir = path.join(config.workspaceRoot, "5_tasks/records/closures", taskId);
    if (fs.existsSync(closureDir)) {
      const closureFiles = fs.readdirSync(closureDir).filter((f: string) => f.startsWith("closure_") && f.endsWith(".md"));
      if (closureFiles.length > 0) continue; // 已结案,跳过
    }

    // 候选卡: 有 PASS 裁决 + 未 close → 自动 finalize+close
    currentLogger.info("auto-finalize-start", { task_id: taskId, verdict: latest.verdict, verdict_at: latest.verdictAt });
    liveCtx?.ui?.notify?.(`sweep 候选: ${taskId} (${latest.verdict})`, "info");

    try {
      const { execSync } = await import("node:child_process");

      let codeRepo = path.join(config.workspaceRoot, "../../../lybra");
      try {
        const projectJsonPath = path.join(config.workspaceRoot, "project.json");
        if (fs.existsSync(projectJsonPath)) {
          const projectJson = JSON.parse(fs.readFileSync(projectJsonPath, "utf-8"));
          if (projectJson.code_repo) codeRepo = projectJson.code_repo;
        }
      } catch (e) {
        currentLogger.warn("project-json-parse-failed", { error: String(e) });
      }

      const lybraBin = path.join(codeRepo, ".deploy/current/bin/lybra");
      const finalizeCmd = `${lybraBin} --workspace-root ${codeRepo} finalize --task-id ${taskId} --actor ${config.actor} --push --deploy`;
      const finalizeOutput = execSync(finalizeCmd, { cwd: codeRepo, encoding: "utf-8", stdio: "pipe" });

      currentLogger.info("auto-finalize-success", { task_id: taskId, output: finalizeOutput.slice(0, 500) });

      let finalizeCommitHash = "";
      try {
        finalizeCommitHash = execSync("git rev-parse HEAD", { cwd: codeRepo, encoding: "utf-8" }).trim();
      } catch (e) {
        currentLogger.warn("auto-close-get-hash-failed", { task_id: taskId, error: String(e) });
      }
      const closeArgs = {
        task_id: taskId,
        actor: config.actor,
        closure_evidence: finalizeCommitHash
          ? { finalize_commit_hash: finalizeCommitHash }
          : { finalize_return_ref: `finalize_${taskId}` },
      };

      const closeDryResp = await currentClient.callTool("lybra_queue_close_dry_run", closeArgs);
      if (closeDryResp.verdict === "BLOCK" || closeDryResp.isError === true) {
        const detail = closeDryResp.blocking_reasons || closeDryResp.errors || closeDryResp.message || JSON.stringify(closeDryResp);
        const msg = `auto-close dry_run BLOCK: ${taskId} - ${JSON.stringify(detail)}`;
        currentLogger.error("auto-close-blocked", { task_id: taskId, response: closeDryResp });
        liveCtx?.ui?.notify?.(msg, "error");
        anomalies.push({ task_id: taskId, reason: "close dry_run BLOCK" });
        continue;
      }

      await currentClient.callTool("lybra_queue_close_confirm", closeArgs);
      currentLogger.info("auto-close-success", { task_id: taskId });
      // AIPOS-F4 大项C: 收账成功升为可见 info 一行(合并 <hash>/部署 <hash>/close)。
      const settleHash = finalizeCommitHash ? finalizeCommitHash.slice(0, 8) : "?";
      liveCtx?.ui?.notify?.(`已收账 ${taskId}: 合并 ${settleHash}/部署 ${settleHash}/close`, "info");
      processedCount++;
    } catch (e) {
      const rawErr = e instanceof Error ? e.message : String(e);
      // AIPOS-F4 大项B/C: 脏树/可重试类失败属 auto_recoverable → warn + 下一步(等下一轮); 其余 → error。
      const isDirtyTree = /工作树不干净|dirty|uncommitted|changes not staged|working tree/i.test(rawErr);
      const level = isDirtyTree ? "warn" : "error";
      const nextStep = isDirtyTree ? " 下一步: 等下一轮 sweep 再试(脏树自动让路)" : "";
      const errMsg = `sweep finalize 失败: ${taskId} - ${rawErr}${nextStep}`;
      currentLogger.error("auto-finalize-failed", { task_id: taskId, error: rawErr, isDirtyTree });
      liveCtx?.ui?.notify?.(errMsg, level);
      anomalies.push({ task_id: taskId, reason: isDirtyTree ? "脏树让路等下一轮" : "finalize 失败" });
    }
  }

  // AIPOS-C3B 大项D③: 出声=一行汇总+仅异常逐条
  const total = claimedCards.length;
  const summaryMsg = anomalies.length > 0
    ? `sweep: ${total} claimed, 收 ${processedCount} 张, ${anomalies.length} 异常(${anomalies.map(a => a.task_id).join(",")})`
    : processedCount > 0
      ? `sweep: ${total} claimed, 收 ${processedCount} 张`
      : `sweep: ${total} claimed, 无需处理`;
  currentLogger.info("sweep-complete", { processed: processedCount, claimed: total, anomalies });
  liveCtx?.ui?.notify?.(summaryMsg, "info");

  return processedCount > 0;
}

/**
 * AIPOS-CONN-LOOP-2 ①: 检查是否有 completed 事件，如有则自动 return。
 * 完成判定 = task-progress completed 事件（既有动词兼职完成信号）。
 * return 素材自动组装 = result_summary(取 completed 事件 summary) + artifact_refs(取卡分支 commit 文件清单)。
 */
// AIPOS-F10:不接收 ctx — 内部从 liveCtx 取活引用。
async function tryAutoReturn(): Promise<boolean> {
  if (!currentTaskId || !currentClient || !currentLogger) return false;
  
  const fs = await import("node:fs");
  const path = await import("node:path");
  
  // 检查是否有 completed 事件文件
  let config;
  try {
    config = loadConfig(process.env);
  } catch {
    return false;
  }

  // AIPOS-F7 大项A: 兜底网读节点完成判据 — auto-return 发起前,按被持卡对应节点
  // (从卡 task_mode 判定 N2-return 或 N4-verdict)读 transitions.schema 声明的完成记录:
  // 该记录已存在 → 卡已完成 → 网静默歇手。禁 role==auditor 特例判断,一切以节点声明为准。
  // N2(execution)=return 存在即歇;N4(audit)=verdict 存在即歇。
  const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${currentTaskId.toLowerCase()}.md`);
  let isAuditCard = false;
  let cardContent = "";
  if (fs.existsSync(taskCardPath)) {
    try {
      cardContent = fs.readFileSync(taskCardPath, "utf-8");
      isAuditCard = /task_mode:\s*audit/i.test(cardContent) || /created_by:\s*gate_derivation/i.test(cardContent);
    } catch {
      // ignore read error, fall through to existing logic
    }
  }

  if (isAuditCard) {
    // N4 完成判据:verdict 记录已落库 → 审计卡已完成 → 兜底网静默歇手
    const reviewedMatch = cardContent.match(/reviewed_task_id:\s*['"]?([^'"\n]+)['"]?/i);
    const reviewedTaskId = reviewedMatch ? reviewedMatch[1].trim() : currentTaskId.replace(/R$/i, "");
    const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", reviewedTaskId);
    if (fs.existsSync(verdictDir)) {
      const vFiles = fs.readdirSync(verdictDir).filter((f: string) => f.endsWith(".md"));
      if (vFiles.length > 0) {
        currentLogger.info("auto-return-skip-audit-verdict-exists", { task_id: currentTaskId, reviewed_task_id: reviewedTaskId, verdict_count: vFiles.length });
        return false;
      }
    }
    // 审计卡无 verdict → 还在执行中,不走 auto-return(审计卡完成判据=N4,不走 N2 return)
    currentLogger.info("auto-return-skip-audit-no-verdict-yet", { task_id: currentTaskId, reviewed_task_id: reviewedTaskId });
    return false;
  }

  // N2 完成判据:return 记录已落库 → 执行卡已完成 → 兜底网静默歇手
  // AIPOS-F4 大项D: 兜底网让路 — auto-return 发起前先查 returns/ 已有记录 → 静默跳过。
  // 本任务已有 return 记录(上轮已交回)则不再重复发起, 也不出声(避免红错刷屏)。
  const returnsDir = path.join(config.workspaceRoot, "5_tasks/records/returns", currentTaskId);
  if (fs.existsSync(returnsDir)) {
    const returnFiles = fs.readdirSync(returnsDir).filter((f) => f.startsWith("return_") && f.endsWith(".md"));
    if (returnFiles.length > 0) {
      currentLogger.info("auto-return-skip-already-returned", { task_id: currentTaskId, count: returnFiles.length });
      return false;
    }
  }

  const eventsDir = path.join(config.workspaceRoot, "5_tasks/records/events", currentTaskId);
  if (!fs.existsSync(eventsDir)) return false;
  
  // 查找 completed_*.md 文件
  const files = fs.readdirSync(eventsDir);
  const completedFiles = files.filter((f) => f.startsWith("completed_") && f.endsWith(".md"));
  if (completedFiles.length === 0) return false;
  
  // 读取最新的 completed 事件
  completedFiles.sort();
  const latestCompleted = path.join(eventsDir, completedFiles[completedFiles.length - 1]);
  const eventContent = fs.readFileSync(latestCompleted, "utf-8");
  
  // 从 frontmatter 提取 summary
  let summary = "";
  const summaryMatch = eventContent.match(/^summary:\s*['"]?([^'"\n]+)['"]?$/m);
  if (summaryMatch) {
    summary = summaryMatch[1].trim();
  }
  
  // 从 worktree git log 提取 artifact_refs（最近一次 commit 的文件列表）
  let artifactRefs: string[] = [];
  if (currentWorktreePath && fs.existsSync(currentWorktreePath)) {
    try {
      const { execSync } = await import("node:child_process");
      // 获取最近一次 commit 的文件列表
      const gitOutput = execSync("git diff-tree --no-commit-id --name-only -r HEAD", {
        cwd: currentWorktreePath,
        encoding: "utf-8",
      });
      artifactRefs = gitOutput.trim().split("\n").filter((f) => f.trim());
    } catch (e) {
      currentLogger.warn("auto-return-git-failed", {
        task_id: currentTaskId,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }
  
  // 调用 return_dry_run
  currentLogger.info("auto-return-start", {
    task_id: currentTaskId,
    summary: summary || "(no summary)",
    artifact_count: artifactRefs.length,
  });
  
  try {
    const dryRunResp = await currentClient.callTool("lybra_queue_return_dry_run", {
      task_id: currentTaskId,
      actor: config.actor,
      agent_instance: config.agentInstance,
      autonomy_mode: "Supervised",
      owner_policy_ref: config.ownerPolicyRef,
      result_summary: summary || "Task completed (auto-return)",
      artifact_refs: artifactRefs.length > 0 ? artifactRefs : undefined,
      active_session_id: getSessionId(),
    });
    
    // AIPOS-F4 大项C/D: dry_run 被拒时拒因上屏(声明→级别映射, 禁现场定级)。
    // [object Object] 修复: 用 stringifyReasons 逐项取 message, 不再裸 join 对象数组。
    // 兜底网撞会话绑定(SESSION_MISMATCH)属 auto_recoverable → 降 info(非红错), 附"下一步"。
    if (dryRunResp.verdict === "BLOCK" || dryRunResp.isError === true) {
      const reasons = dryRunResp.blocking_reasons || dryRunResp.errors || [];
      const reasonText = stringifyReasons(reasons);
      const isSessionMismatch = reasonText.includes("SESSION_MISMATCH");
      const severity = (dryRunResp as any).severity || (isSessionMismatch ? "auto_recoverable" : "needs_human");
      const level = severityToLevel(severity);
      const onScreenMsg = `auto-return ${currentTaskId} 被拒: ${reasonText}`;
      currentLogger.error("auto-return-blocked", { task_id: currentTaskId, reasons, isSessionMismatch, severity, level });
      liveCtx?.ui?.notify?.(onScreenMsg, level);
      if (isSessionMismatch) {
        liveCtx?.ui?.notify?.(`下一步: 任务已由本工位交回(returns 已有记录), 无需处理`, "info");
      }
      return false;
    }

    const dryRunToken = dryRunResp.dry_run_token;
    if (!dryRunToken) {
      const msg = `auto-return ${currentTaskId}: 无 dry_run_token(响应异常)`;
      currentLogger.error("auto-return-no-token", { task_id: currentTaskId, response: dryRunResp });
      liveCtx?.ui?.notify?.(msg, "error");
      return false;
    }
    
    // AIPOS-328: executor 自确认 return (owner_confirmation_token = OWNER_CONFIRMED 字面量)
    const confirmResp = await currentClient.callTool("lybra_queue_return_confirm", {
      dry_run_token: String(dryRunToken),
      actor: config.actor,
      agent_instance: config.agentInstance,
      owner_policy_ref: config.ownerPolicyRef,
      owner_confirmation_token: "OWNER_CONFIRMED",
    });
    
    currentLogger.info("auto-return-success", {
      task_id: currentTaskId,
      verdict: confirmResp.verdict || confirmResp.ok ? "ok" : "unknown",
    });
    
    liveCtx?.ui?.notify?.(`自动归还任务 ${currentTaskId}`, "info");
    
    // 清空当前任务信息
    currentTaskId = null;
    currentWorktreePath = null;
    
    return true;
  } catch (e) {
    const errMsg = e instanceof Error ? e.message : String(e);
    const isSessionMismatch = errMsg.includes("SESSION_MISMATCH");
    currentLogger.error("auto-return-failed", {
      task_id: currentTaskId,
      error: errMsg,
    });
    // AIPOS-F4 大项D: 拒因上屏 — SESSION_MISMATCH 属 auto_recoverable → info, 其余 → error
    liveCtx?.ui?.notify?.(`auto-return ${currentTaskId} 失败: ${errMsg}`, isSessionMismatch ? "info" : "error");
    if (isSessionMismatch) {
      liveCtx?.ui?.notify?.(`下一步: 任务已由本工位交回(returns 已有记录), 无需处理`, "info");
    }
    return false;
  }
}

// AIPOS-F10:stopLoop 不接收 ctx 参数 — 从 liveCtx 取活引用。
function stopLoop(
  reason: string,
  level: "info" | "warn" | "error" = "warn",
) {
  loopState.on = false;
  loopState.stoppedReason = reason;
  clearTimer();
  currentLogger?.write(level === "error" ? "ERROR" : level === "warn" ? "WARN" : "INFO", "loop-stopped", { reason });
  liveCtx?.ui?.notify?.(`lybra 循环停止:${reason}`, level);
}

// AIPOS-F10:getSessionId 从 liveCtx 取,不接收外部 ctx 参数。
function getSessionId(): string {
  try {
    const id = liveCtx?.sessionManager?.getSessionId?.();
    return id ? String(id) : "lybra-loop-session";
  } catch {
    return "lybra-loop-session";
  }
}

// AIPOS-F10:doTick 不接收 pi/ctx 参数 — 从 livePi/liveCtx 取当前活引用。
// 根治三病象:①定时器闭包 stale ctx(newSession 摔) ②/reload 后 stale ctx ③session_start 传 event 非 ctx(reply 摔)。
async function doTick(): Promise<void> {
  if (!loopState.on) return;
  if (!currentClient || !currentLogger) {
    stopLoop("内部状态缺失(client/logger),请重新 /lybra on", "error");
    return;
  }
  if (!liveCtx) {
    stopLoop("liveCtx 未初始化(无活会话),请重新 /lybra on", "error");
    return;
  }
  let config;
  try {
    config = loadConfig(process.env);
  } catch (e) {
    stopLoop(`配置失效:${e instanceof Error ? e.message : String(e)}`, "error");
    return;
  }
  loopState.running = true;
  try {
    // AIPOS-C3D 大项A: 轮询内周期 sweep — 每轮 tick 前尝试一次收账。
    // 已在跑卡(currentTaskId 非空 = released 未 settle)期间不 sweep, 避免干扰当前会话。
    if (!currentTaskId) {
      await tryAutoFinalizeOnPassVerdict().catch((e) => {
        currentLogger?.warn("tick-sweep-error", {
          error: e instanceof Error ? e.message : String(e),
        });
      });
    }
    const outcome = await executeTick({
      client: currentClient,
      actor: config.actor,
      agentInstance: config.agentInstance,
      ownerPolicyRef: config.ownerPolicyRef,
      workspaceRoot: config.workspaceRoot,
      activeSessionId: getSessionId(),
      state: loopState,
      logger: currentLogger,
    });

    if (outcome.kind === "release") {
      loopState.released += 1;
      // AIPOS-CONN-LOOP-2 ①: 记录当前任务信息，用于自动return
      currentTaskId = String(outcome.task.task_id);
      // worktree路径从任务元数据中获取
      const taskMeta = (outcome.task as { metadata?: unknown }).metadata;
      const meta = taskMeta && typeof taskMeta === "object" ? (taskMeta as Record<string, unknown>) : {};
      currentWorktreePath = String(meta.active_worktree_path || "") || null;
      currentLogger.info("release", {
        task_id: outcome.task.task_id,
        card: outcome.cardAbsPath,
        policy: outcome.policyId || "?",
        released: loopState.released,
        maxN: loopState.maxN,
        worktree: currentWorktreePath,
      });
      if (loopState.released >= loopState.maxN) {
        currentLogger.info("release-last", { task_id: outcome.task.task_id });
      }
      liveCtx.ui.notify(
        `放行 ${outcome.task.task_id}(PreAuthorized)→ 冷启动执行 [${loopState.released}/${loopState.maxN}]`,
        "info",
      );
      // F-EXT001-8(FIX4):running 标志复位前置到 newSession 之前,确保任何路径(含 stale 异常)下可达
      loopState.running = false;
      expectingSwap = true;
      const kickoff = buildKickoff(outcome.cardAbsPath);
      try {
        // AIPOS-F10:用 liveCtx.newSession(当前活 ctx),对齐 claim.ts 范式:
        // withSession 回调里只用 freshCtx,绝不捕获旧 ctx。
        const result = await liveCtx.newSession({
          withSession: async (freshCtx) => {
            await freshCtx.sendUserMessage(kickoff);
          },
        });
        if (result?.cancelled) {
          expectingSwap = false;
          stopLoop("newSession 被拦截(循环停)", "warn");
        }
      } catch (e) {
        // F-EXT001-8(FIX4):newSession 异常(stale ctx 等)不再静默吞掉,落日志
        expectingSwap = false;
        currentLogger.warn("release-newSession-error", {
          task_id: outcome.task.task_id,
          error: e instanceof Error ? e.message : String(e),
        });
        // AIPOS-F10:降级出声(禁裸抛) — newSession 失败说明 ctx 能力缺失,提示用户手动操作
        const errMsg = e instanceof Error ? e.message : String(e);
        stopLoop(`newSession 异常:${errMsg}`, "error");
        liveCtx?.ui?.notify?.(`下一步: 请在 Pi 对话框手动 /claim 任务卡`, "info");
      }
      return; // session 已替换;续跑靠新 session 的 agent_settled + session_start 双保险
    }

    if (outcome.kind === "stop") {
      // AIPOS-R6L 大项A①: held分支先查completed事件，有→走return，没有→才stop
      // 冷会话可接管：以records为准，不依赖内存currentTaskId
      // 从outcome.reason提取task_id（格式："已持有 TASK-ID — ..."）
      const heldMatch = outcome.reason.match(/已持有\s+([A-Z0-9-]+)/);
      if (heldMatch) {
        const heldTaskId = heldMatch[1];
        const fs = await import("node:fs");
        const path = await import("node:path");
        
        // 检查是否有 completed 事件
        const eventsDir = path.join(config.workspaceRoot, "5_tasks/records/events", heldTaskId);
        let hasCompleted = false;
        if (fs.existsSync(eventsDir)) {
          const files = fs.readdirSync(eventsDir);
          hasCompleted = files.some((f) => f.startsWith("completed_") && f.endsWith(".md"));
        }
        
        if (hasCompleted) {
          // 有completed事件，走return流程
          currentLogger.info("held-with-completed", { task_id: heldTaskId });
          liveCtx?.ui?.notify?.(`检测到 ${heldTaskId} 已completed，执行自动return`, "info");
          
          // 设置 currentTaskId 供tryAutoReturn使用
          currentTaskId = heldTaskId;
          
          // 查找 worktree 路径（从任务卡读取）
          const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${heldTaskId.toLowerCase()}.md`);
          if (fs.existsSync(taskCardPath)) {
            try {
              const cardContent = fs.readFileSync(taskCardPath, "utf-8");
              const worktreeMatch = cardContent.match(/active_worktree_path:\s*['"]?([^'"\n]+)['"]?/i);
              if (worktreeMatch) {
                currentWorktreePath = worktreeMatch[1].trim();
              }
            } catch (e) {
              currentLogger.warn("held-read-card-failed", { task_id: heldTaskId, error: String(e) });
            }
          }
          
          // 调用 tryAutoReturn
          const returned = await tryAutoReturn();
          if (returned) {
            // return成功，继续轮询下一张卡
            if (loopState.released < loopState.maxN) {
              loopState.running = false; // 确保复位
              scheduleNextTick(1000); // 1秒后再拉
            }
            return;
          } else {
            // return失败，记录错误但不停循环
            currentLogger.warn("held-auto-return-failed", { task_id: heldTaskId });
            liveCtx?.ui?.notify?.(`自动return ${heldTaskId} 失败，请手动处理`, "warn");
          }
        } else {
          // AIPOS-C3B 大项D①: held-resume 罩审计车道
          // 审计卡(task_mode=audit)的完成判据 = verdict 记录是否落库(N4)
          // 审计卡 claimed + 报告在 + 无 verdict → 复工提示"只差提交裁决"
          const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${heldTaskId.toLowerCase()}.md`);
          let isAuditCard = false;
          let cardContent = "";
          if (fs.existsSync(taskCardPath)) {
            try {
              cardContent = fs.readFileSync(taskCardPath, "utf-8");
              isAuditCard = /task_mode:\s*audit/i.test(cardContent) || /created_by:\s*gate_derivation/i.test(cardContent);
            } catch {
              // ignore
            }
          }

          if (isAuditCard) {
            // 审计车道: 检查 verdict 记录是否已落库
            const reviewedMatch = cardContent.match(/reviewed_task_id:\s*['"]?([^'"\n]+)['"]?/i);
            const reviewedTaskId = reviewedMatch ? reviewedMatch[1].trim() : heldTaskId.replace(/R$/i, "");
            const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", reviewedTaskId);
            let hasVerdict = false;
            if (fs.existsSync(verdictDir)) {
              const vFiles = fs.readdirSync(verdictDir).filter((f: string) => f.endsWith(".md"));
              hasVerdict = vFiles.length > 0;
            }
            if (!hasVerdict) {
              currentLogger.info("held-audit-no-verdict", { task_id: heldTaskId, reviewed_task_id: reviewedTaskId });
              liveCtx?.ui?.notify?.(`复工：${heldTaskId} 是审计卡，只差提交裁决(verdict 未落库)`, "info");
              // AIPOS-F10:投递卡内容让 auditor 继续提交裁决。
              // 用 sendUserMessage(对齐 claim.ts 范式),不用 ctx.reply(不存在的方法)。
              if (cardContent) {
                try {
                  await liveCtx.sendUserMessage(
                    `# 复工任务(审计车道): ${heldTaskId}\n\n审计卡 claimed + 无 verdict 记录 → 只差提交裁决。\n\n${cardContent}`,
                  );
                } catch (sendErr) {
                  // AIPOS-F10:能力缺失时降级出声,禁裸抛
                  currentLogger.warn("held-audit-sendUserMessage-failed", {
                    task_id: heldTaskId,
                    error: sendErr instanceof Error ? sendErr.message : String(sendErr),
                  });
                  liveCtx?.ui?.notify?.(`复工投递失败: ${sendErr instanceof Error ? sendErr.message : String(sendErr)}`, "warn");
                  liveCtx?.ui?.notify?.(`下一步: 请在 Pi 对话框手动提交裁决`, "info");
                }
                currentTaskId = heldTaskId;
                stopLoop(`已复工审计卡 ${heldTaskId}，等待提交裁决`, "info");
                return;
              }
            } else {
              currentLogger.info("held-audit-has-verdict", { task_id: heldTaskId });
              liveCtx?.ui?.notify?.(`审计卡 ${heldTaskId} 已有 verdict，无需复工`, "info");
            }
          }

          // AIPOS-R8B 大项C①: held 且无 completed → 投递卡正文复工（会话中断后自动续做）
          currentLogger.info("held-resume", { task_id: heldTaskId });
          liveCtx?.ui?.notify?.(`复工：继续执行 ${heldTaskId}`, "info");
          
          // 读取任务卡正文并投递
          if (fs.existsSync(taskCardPath)) {
            try {
              if (!cardContent) cardContent = fs.readFileSync(taskCardPath, "utf-8");
              // AIPOS-C3D 大项B: 复工附带裁决 — 若该卡存在门生裁决(N4 声明位置), 随卡正文投递最新裁决全文
              let resumeText = `# 复工任务: ${heldTaskId}\n\n${cardContent}`;
              const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", heldTaskId);
              if (fs.existsSync(verdictDir)) {
                const latest = selectLatestGateVerdict(fs, verdictDir, currentLogger, heldTaskId);
                if (latest) {
                  let verdictFull = "";
                  try {
                    verdictFull = fs.readFileSync(latest.filePath, "utf-8");
                  } catch {
                    verdictFull = `(读取裁决文件失败: ${latest.filePath})`;
                  }
                  const kindLabel =
                    latest.verdict === "FAIL"
                      ? "FAIL 整改复工"
                      : latest.verdict === "PASS" || latest.verdict === "PASS_WITH_NOTES"
                        ? "PASS 待收尾"
                        : `裁决 ${latest.verdict}`;
                  resumeText =
                    `# 复工任务: ${heldTaskId}(此为 ${kindLabel})\n\n` +
                    `> 带最新门生裁决全文(${latest.verdictId}, verdict_at=${latest.verdictAt})。\n` +
                    `> findings 以顾问核定为准, 有驳回顾问会另行说明。\n\n` +
                    `--- 最新门生裁决全文 ---\n\n${verdictFull}\n\n` +
                    `--- 任务卡正文 ---\n\n${cardContent}`;
                  currentLogger.info("held-resume-with-verdict", {
                    task_id: heldTaskId,
                    verdict: latest.verdict,
                    verdict_id: latest.verdictId,
                  });
                }
              }
              // AIPOS-F10:投递卡内容到当前会话。用 sendUserMessage(对齐 claim.ts 范式),
              // 不用 ctx.reply(不存在的方法 —— 病象③的根因)。
              try {
                await liveCtx.sendUserMessage(resumeText);
              } catch (sendErr) {
                // AIPOS-F10:能力缺失时降级出声,禁裸抛
                currentLogger.warn("held-resume-sendUserMessage-failed", {
                  task_id: heldTaskId,
                  error: sendErr instanceof Error ? sendErr.message : String(sendErr),
                });
                liveCtx?.ui?.notify?.(`复工投递失败: ${sendErr instanceof Error ? sendErr.message : String(sendErr)}`, "warn");
                liveCtx?.ui?.notify?.(`下一步: 请在 Pi 对话框手动 /claim 任务卡`, "info");
              }
              
              // 设置 currentTaskId 供后续使用
              currentTaskId = heldTaskId;
              
              // 提取 worktree 路径
              const worktreeMatch = cardContent.match(/active_worktree_path:\s*['"]?([^'"\n]+)['"]?/i);
              if (worktreeMatch) {
                currentWorktreePath = worktreeMatch[1].trim();
              }
              
              // 停止循环，让执行体在当前会话继续工作
              stopLoop(`已复工 ${heldTaskId}，继续在当前会话执行`, "info");
              return;
            } catch (e) {
              currentLogger.error("held-resume-failed", { task_id: heldTaskId, error: String(e) });
              liveCtx?.ui?.notify?.(`复工失败: ${e instanceof Error ? e.message : String(e)}`, "error");
            }
          } else {
            currentLogger.warn("held-resume-no-card", { task_id: heldTaskId, expected_path: taskCardPath });
            liveCtx?.ui?.notify?.(`任务卡不存在: ${taskCardPath}`, "warn");
          }
        }
      }
      
      // 没有completed事件，或return失败，按原逻辑stop
      stopLoop(outcome.reason, "warn");
      return;
    }

    // wait:轮询
    const elapsed = Date.now() - (loopState as { cycleStartMs: number }).cycleStartMs;
    if (elapsed >= config.maxWaitSec * 1000) {
      stopLoop(`轮询超时(${config.maxWaitSec}s)无信封内可认领卡`, "info");
      return;
    }
    const remain = config.maxWaitSec * 1000 - elapsed;
    const nextMs = Math.min(config.intervalSec * 1000, remain);
    // AIPOS-R6L 大项A③: 非有限值兜底 - 防配置错误导致 NaN 空转
    if (!Number.isFinite(nextMs) || nextMs <= 0) {
      const msg = `轮询间隔非法(nextMs=${nextMs}, intervalSec=${config.intervalSec}) - 停止循环`;
      currentLogger.error("invalid-poll-interval", { nextMs, intervalSec: config.intervalSec, remain });
      liveCtx?.ui?.notify?.(msg, "error");
      stopLoop(msg, "error");
      return;
    }
    currentLogger.info("wait-poll", { reason: outcome.reason, nextMs });
    // AIPOS-R6I 靶③: 轮询结果可见 - 打印等待原因
    liveCtx?.ui?.notify?.(`轮询: ${outcome.reason}，${Math.round(nextMs / 1000)}s 后再拉`, "info");
    // F-EXT001-8(FIX4):wait 路径复位 running,防定时器触发前 running 一直 true 拦截其他入口
    loopState.running = false;
    scheduleNextTick(nextMs);
  } catch (e) {
    stopLoop(`tick 异常:${e instanceof Error ? e.message : String(e)}`, "error");
  } finally {
    // F-EXT001-8(FIX4):release 路径已前置复位 running;finally 保底兜所有其他路径
    loopState.running = false;
  }
}

export default function (pi: ExtensionAPI) {
  // AIPOS-F10:每次 session 装载立即刷新 livePi — 定时器/钩子从模块级引用取,不闭包捕获。
  livePi = pi;

  // --- 续跑:maxN>1 时,新 session 的卡执行完(settle)→ 直接调 tick ---
  pi.on("agent_settled", async (_event, ctx) => {
    liveCtx = ctx; // AIPOS-F10:刷新活引用
    if (!loopState.on) return;
    
    // AIPOS-R6I 靶②: 检查是否有 PASS 裁决，如有则自动 finalize+close
    await tryAutoFinalizeOnPassVerdict().catch((e) => {
      const errMsg = `agent_settled auto-finalize 错误: ${e instanceof Error ? e.message : String(e)}`;
      currentLogger?.warn("agent_settled-auto-finalize-error", {
        error: e instanceof Error ? e.message : String(e),
      });
      // AIPOS-R6L 第三轮修复(b): 失败必出声
      liveCtx?.ui?.notify?.(errMsg, "error");
    });
    
    // AIPOS-CONN-LOOP-2 ①: 检查是否有 completed 事件，如有则自动 return
    if (currentTaskId) {
      const returned = await tryAutoReturn();
      if (returned) {
        // return 成功后，如果还未达到 maxN，继续轮询下一张卡
        if (loopState.released < loopState.maxN) {
          if (!loopState.running) {
            doTick().catch((e) => {
              currentLogger?.warn("agent_settled-post-return-tick-error", {
                error: e instanceof Error ? e.message : String(e),
              });
            });
          }
        }
        return;
      }
    }
    
    if (loopState.released >= loopState.maxN) {
      stopLoop(`达到 maxN(${loopState.maxN}),已放行 ${loopState.released} 张`, "info");
      return;
    }
    if (loopState.running) return; // 防重入
    // F-EXT001-4(FIX1):直接调用,不经 sendUserMessage
    doTick().catch((e) => {
      // F-EXT001-8(FIX4):不再静默吞错,落日志
      currentLogger?.warn("agent_settled-doTick-error", {
        error: e instanceof Error ? e.message : String(e),
      });
    });
  });

  // --- session 生命周期:reload 不断;用户 /new|resume|fork|quit 停;循环自驱 newSession 不停 ---
  pi.on("session_shutdown", async (event, ctx) => {
    liveCtx = ctx; // AIPOS-F10:刷新活引用(虽然即将关闭,但 stopLoop 可能用到)
    if (expectingSwap) {
      // 循环自己触发的 newSession:清 flag + 定时器,但保留循环状态供新 session 续跑
      expectingSwap = false;
      clearTimer();
      return;
    }
    // AIPOS-F10:reload 也清定时器 — 旧定时器闭包的 ctx/pi 即将 stale,
    // 由 session_start 的新 ctx 重新调度。根治病象②。
    if (event.reason === "reload") {
      clearTimer();
      return; // /reload:循环继续(session_start 再续)
    }
    clearTimer();
    if (loopState.on) stopLoop(`session ${event.reason}(循环中断)`, "warn");
  });

  // AIPOS-F10:session_start 必须捕获第二参数 ctx(=当前活会话的 ctx)。
  // 旧代码只取 event 然后 event as any 当 ctx 用 —— 病象③的根因。
  pi.on("session_start", async (event, ctx) => {
    liveCtx = ctx; // AIPOS-F10:刷新活引用 — 新 session 的 ctx 替代旧 session 的 stale ctx
    // F-EXT001-8(FIX4):双保险机制 — reload 时续跑 + 自驱 newSession(expectingSwap)时也续跑,防 agent_settled 单点失效
    if (loopState.on && (event.reason === "reload" || expectingSwap)) {
      if (expectingSwap) {
        currentLogger?.info("session_start-swap-resume", { reason: event.reason });
        expectingSwap = false; // 清 flag,防重复触发
      }
      if (loopState.running) return; // 防重入(agent_settled 可能先到)
      // F-EXT001-4(FIX1):直接调用,不经 sendUserMessage
      doTick().catch((e) => {
        // F-EXT001-8(FIX4):不再静默吞错,落日志
        currentLogger?.warn("session_start-doTick-error", {
          reason: event.reason,
          error: e instanceof Error ? e.message : String(e),
        });
      });
    }
  });

  // --- /lybra:on | off | status ---
  pi.registerCommand("lybra", {
    description: "lybra-loop 自动领卡循环:on [maxN] | off | status[v0]",
    handler: async (args, ctx) => {
      liveCtx = ctx; // AIPOS-F10:命令入口刷新活引用
      const parts = String(args || "").trim().split(/\s+/);
      const sub = parts[0] || "status";

      if (sub === "off") {
        if (!loopState.on) {
          ctx.ui.notify("lybra 循环未在运行", "info");
          return;
        }
        stopLoop("用户 /lybra off", "info");
        return;
      }

      if (sub === "status") {
        // AIPOS-C4B 大项B: /lybra status 随时可查 — 版本戳 + 清单比对 + provenance 横幅
        // (复现 C2 横幅被冷启动换屏吞掉 → status 可查, 不依赖 on 瞬间的屏)
        const fp = currentTokenFp;
        const lines: string[] = [
          `lybra-loop 状态:`,
          `  运行中: ${loopState.on ? "是" : "否"}`,
          `  已放行: ${loopState.released}/${loopState.maxN}`,
          `  停止原因: ${loopState.stoppedReason || "(无)"}`,
          `  gate: ${currentGateUrl || "(未配置)"}  role: ${currentRole || "-"}  actor: ${currentActor || "-"}`,
          `  token: ${fp}`,
        ];
        // 版本戳 + provenance + 清单比对(能拿到 config/client 就尽量答)
        try {
          const config = loadConfig(process.env);
          lines.push(buildVersionLine(config));
          lines.push(`[身份来源自曝]\n${buildProvenanceBanner(config)}`);
          const fres = await checkManifestFreshness(currentClient, config);
          if (fres.error) {
            lines.push(`  清单比对: 无法比对(${fres.error})`);
          } else if (fres.behind) {
            lines.push(`  ⚠ 落后: 本地 ${fres.local} vs 线上 ${fres.remote} — 请 lybra sync + /reload`);
          } else {
            lines.push(`  清单比对: 最新(本地 ${fres.local ?? "?"} == 线上 ${fres.remote ?? "?"})`);
          }
        } catch (e) {
          // 配置未就绪时不阻断 status(如 /lybra status 早于 /lybra on)
          lines.push(`  版本/来源: 配置未就绪(${e instanceof Error ? e.message : String(e)})`);
        }
        ctx.ui.notify(lines.join("\n"), "info");
        return;
      }

      if (sub === "on") {
        if (loopState.on) {
          ctx.ui.notify("lybra 循环已在运行,先 /lybra off 再启动", "warn");
          return;
        }
        let maxN = 1;
        if (parts[1]) {
          const n = Number(parts[1]);
          if (!Number.isFinite(n) || n < 1 || !Number.isInteger(n)) {
            ctx.ui.notify(`maxN 无效:${parts[1]}(需正整数)`, "error");
            return;
          }
          maxN = n;
        }

        // 配置(必需项缺失即停,绝不猜 actor/policy)
        // AIPOS-R8B 大项C④: 启动阶段带耗时显示
        const startMs = Date.now();
        ctx.ui?.notify?.("[1/5] 加载配置...", "info");
        
        let config;
        try {
          config = loadConfig(process.env);
          const configMs = Date.now() - startMs;
          ctx.ui?.notify?.(`[1/5] 配置加载完成 (${configMs}ms)`, "info");
        } catch (e) {
          ctx.ui.notify(`配置错误,循环未启动:${e instanceof Error ? e.message : String(e)}`, "error");
          return;
        }
        currentGateUrl = config.gateUrl;
        currentRole = config.role;
        currentActor = config.actor;

        // AIPOS-C2 大项C: 来源自曝横幅 (每个键打印取自哪一层, env 兜底/被降级标 ⚠)
        ctx.ui?.notify?.(
          `[身份来源自曝]\n${buildProvenanceBanner(config)}`,
          "info",
        );

        // AIPOS-C4B 大项B: 启动即报版本戳(分发器生成的源 commit 短哈希)
        ctx.ui?.notify?.(buildVersionLine(config), "info");

        // AIPOS-R6R: 启动即校验 schema(缺动词/改错必填参数名 → 报错不启动)。
        ctx.ui?.notify?.("[2/5] 校验 schema...", "info");
        let verbCatalog;
        try {
          const schemaStartMs = Date.now();
          verbCatalog = loadVerbCatalog();
          validateRequiredVerbs(verbCatalog, REQUIRED_VERBS);
          const schemaMs = Date.now() - schemaStartMs;
          ctx.ui?.notify?.(`[2/5] Schema 校验完成 (${schemaMs}ms)`, "info");
        } catch (e) {
          ctx.ui.notify(
            `schema 校验失败,循环未启动:${e instanceof Error ? e.message : String(e)}`,
            "error",
          );
          return;
        }

        // gate 连通性自检(initialize)
        // AIPOS-R8B 大项C②: 使用配置的超时预算
        ctx.ui?.notify?.(`[3/5] 连接 gate (${config.gateUrl})...`, "info");
        const client = new GateMcpClient(config.gateUrl, config.token, { verbs: verbCatalog, timeoutMs: config.timeoutMs });
        try {
          const gateStartMs = Date.now();
          await client.initialize();
          const gateMs = Date.now() - gateStartMs;
          ctx.ui?.notify?.(`[3/5] Gate 连接成功 (${gateMs}ms, timeout=${config.timeoutMs}ms)`, "info");
        } catch (e) {
          ctx.ui.notify(
            `gate 连接失败(${config.gateUrl}),循环未启动:${e instanceof Error ? e.message : String(e)}\n` +
              `确认 Owner 已 lybra serve、且 ${process.env.LYBRA_CONNECTION_JSON || "~/.lybra/local/connection.json"} 可达`,
            "error",
          );
          return;
        }
        currentTokenFp = client.tokenFingerprint;
        currentClient = client;
        currentLogger = new Logger(process.env.LYBRA_LOOP_LOG || LOG_PATH_DEFAULT);

        loopState = freshState();
        loopState.on = true;
        loopState.maxN = maxN;
        (loopState as { cycleStartMs: number }).cycleStartMs = Date.now();
        currentLogger.info("loop-on", {
          maxN,
          gate: config.gateUrl,
          actor: config.actor,
          interval: config.intervalSec,
          maxWait: config.maxWaitSec,
          token: currentTokenFp,
        });

        // AIPOS-R6I 靶③: loop可感知反馈 - 启动时打印详细信息
        // AIPOS-R8B 大项C④: 带耗时显示
        ctx.ui?.notify?.("[4/5] 查询队列...", "info");
        const queueStartMs = Date.now();
        const queueInfo = await client.queueTasks().catch(() => []);
        const queueMs = Date.now() - queueStartMs;
        const queueCount = queueInfo.length;
        const nextPollSec = config.intervalSec;
        ctx.ui?.notify?.(`[4/5] 队列查询完成 (${queueMs}ms, ${queueCount} 张卡)`, "info");

        // AIPOS-C4B 大项B: loop 启动自检发现落后时出声但不拒跑(提示级, 不做强制门)
        const freshness = await checkManifestFreshness(client, config);
        const onLines: string[] = [
          `lybra on: 已连 gate · 身份 ${config.agentInstance} · 信封 ${config.ownerPolicyRef} · 队列 ${queueCount} 张 · ${nextPollSec}s 后再拉`,
          `  启动自动领卡循环 (maxN=${maxN}, interval=${config.intervalSec}s, maxWait=${config.maxWaitSec}s)`,
          `  只放行信封内(PreAuthorized)卡; 信封外跳过; BLOCK/失败立停`,
          `  /lybra off 可停; /lybra status 查看状态`,
        ];
        if (freshness.error) {
          onLines.push(`  清单比对: 无法比对(${freshness.error})`);
        } else if (freshness.behind) {
          onLines.push(`  ⚠ 落后: 本地 ${freshness.local} vs 线上 ${freshness.remote} — 请 lybra sync + /reload`);
        } else {
          onLines.push(`  清单比对: 最新(本地 ${freshness.local ?? "?"} == 线上 ${freshness.remote ?? "?"})`);
        }
        ctx.ui.notify(onLines.join("\n"), "info");
        
        // AIPOS-R6I 靶②: 存量收敛 - 启动时扫描已有 PASS 裁决但未 finalize 的卡自动补收
        // AIPOS-R8B 大项C④: sweep 带耗时显示
        ctx.ui?.notify?.("[5/5] 存量收敛(sweep)...", "info");
        const sweepStartMs = Date.now();
        tryAutoFinalizeOnPassVerdict().catch((e) => {
          const errMsg = `启动时 auto-finalize 错误: ${e instanceof Error ? e.message : String(e)}`;
          currentLogger.warn("startup-auto-finalize-error", {
            error: e instanceof Error ? e.message : String(e),
          });
          // AIPOS-R6L 第三轮修复(b): 失败必出声
          ctx.ui?.notify?.(errMsg, "error");
        }).finally(() => {
          const sweepMs = Date.now() - sweepStartMs;
          ctx.ui?.notify?.(`[5/5] 存量收敛完成 (${sweepMs}ms)`, "info");
          const totalMs = Date.now() - startMs;
          ctx.ui?.notify?.(`✓ 启动完成，总耗时 ${totalMs}ms`, "info");
        });
        
        // F-EXT001-4(FIX1):非阻塞,直接调用第一轮 tick(不经 sendUserMessage)
        doTick().catch((e) => {
          // F-EXT001-8(FIX4):不再静默吞错,落日志
          currentLogger.warn("lybra-on-first-tick-error", {
            error: e instanceof Error ? e.message : String(e),
          });
        });
        return;
      }

      ctx.ui.notify("用法:/lybra on [maxN] | /lybra off | /lybra status", "warn");
    },
  });

  // --- /lybra-tick:手动触发入口(自动链不依赖它,直接调 doTick)---
  // F-EXT001-4(FIX1):保留命令仅作人工手动触发,自动轮询链不经此路径
  pi.registerCommand("lybra-tick", {
    description: "(手动)立即执行一轮 lybra-loop tick;自动链不依赖命令路由",
    handler: async (_args, ctx) => {
      liveCtx = ctx; // AIPOS-F10:命令入口刷新活引用
      // 直接调用 doTick,与自动链同路径
      await doTick();
    },
  });
}
