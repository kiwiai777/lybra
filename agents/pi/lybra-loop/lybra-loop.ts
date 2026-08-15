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
 *   • 一卡一会话:claim 放行 → ctx.newSession 冷启动(同 _shared/extensions/claim.ts)。
 *   • 跨 session 状态:模块级 loopState(ESM 缓存跨 session 替换存活)+ agent_settled 续跑。
 *   • F-EXT001-4(FIX1):tick 机制 — **直接函数调用**,不经 sendUserMessage/命令路由
 *     (pi 的 sendUserMessage 永不触发命令,只落文本给模型;定时器/生命周期钩子直接调 doTick,
 *     零 LLM 参与、零上下文污染;/lybra-tick 命令仅作手动触发入口)。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadConfig, ConfigError, GateMcpClient } from "./gate-client.ts";
import { buildKickoff } from "./loop-decisions.ts";
import { executeTick, freshState, Logger, type LoopState } from "./loop-engine.ts";

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

// F-EXT001-6(FIX2): 默认日志路径迁移到 Lybra 产品仓任务卡目录(旧 contrib 路径已废弃)
const LOG_PATH_DEFAULT = `${process.env.HOME || ""}/projects/lybra/task_cards/LYBRA-EXT-001/loop.log`;

function clearTimer() {
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
}

function scheduleNextTick(pi: ExtensionAPI, ctx: any, delayMs: number) {
  clearTimer();
  pendingTimer = setTimeout(() => {
    pendingTimer = null;
    if (!loopState.on) return; // 期间被 off
    // F-EXT001-4(FIX1):直接调用 tick 函数,不经 sendUserMessage(永不触发命令)
    doTick(pi, ctx).catch((e) => {
      // F-EXT001-8(FIX4):不再静默吞错,落日志
      currentLogger?.warn("scheduleNextTick-doTick-error", {
        error: e instanceof Error ? e.message : String(e),
      });
    });
  }, delayMs);
}

/**
 * AIPOS-R6I 靶②: 检查是否有 PASS/PASS_WITH_NOTES 裁决，如有则自动 finalize+close。
 * 包含存量收敛: 启动时扫描已有 PASS 裁决但未 finalize 的卡自动补收。
 */
async function tryAutoFinalizeOnPassVerdict(ctx: any): Promise<boolean> {
  if (!currentClient || !currentLogger) return false;
  
  let config;
  try {
    config = loadConfig(process.env);
  } catch {
    return false;
  }
  
  const fs = await import("node:fs");
  const path = await import("node:path");
  
  // 扫描所有有 PASS 裁决的任务
  const verdictsRoot = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts");
  if (!fs.existsSync(verdictsRoot)) return false;
  
  const taskDirs = fs.readdirSync(verdictsRoot, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name);
  
  for (const taskId of taskDirs) {
    const verdictDir = path.join(verdictsRoot, taskId);
    const verdictFiles = fs.readdirSync(verdictDir)
      .filter((f) => f.startsWith("verdict_") && f.endsWith(".md"))
      .sort();
    
    if (verdictFiles.length === 0) continue;
    
    // 读取最新裁决
    const latestVerdictFile = path.join(verdictDir, verdictFiles[verdictFiles.length - 1]);
    const verdictContent = fs.readFileSync(latestVerdictFile, "utf-8");
    
    // 从 frontmatter 提取 verdict
    const verdictMatch = verdictContent.match(/^verdict:\s*([A-Z_]+)$/m);
    if (!verdictMatch) continue;
    
    const verdict = verdictMatch[1].trim();
    if (verdict !== "PASS" && verdict !== "PASS_WITH_NOTES") continue;
    
    // 检查是否已 finalize (5_tasks/records/finalize/<task_id>/)
    const finalizeDir = path.join(config.workspaceRoot, "5_tasks/records/finalize", taskId);
    if (fs.existsSync(finalizeDir)) continue;
    
    // 检查任务是否仍在 claimed 状态（未 close）
    const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${taskId.toLowerCase()}.md`);
    if (!fs.existsSync(taskCardPath)) continue;
    
    // 自动 finalize
    currentLogger.info("auto-finalize-start", {
      task_id: taskId,
      verdict,
      verdict_file: latestVerdictFile,
    });
    
    try {
      const { execSync } = await import("node:child_process");
      
      // AIPOS-R6L 第三轮修复(a): 读取 project.json 的 code_repo，显式传产品仓根（禁用 cwd 猜）
      let codeRepo = path.join(config.workspaceRoot, "../../../lybra"); // fallback
      try {
        const projectJsonPath = path.join(config.workspaceRoot, "project.json");
        if (fs.existsSync(projectJsonPath)) {
          const projectJson = JSON.parse(fs.readFileSync(projectJsonPath, "utf-8"));
          if (projectJson.code_repo) {
            codeRepo = projectJson.code_repo;
          }
        }
      } catch (e) {
        currentLogger.warn("project-json-parse-failed", { error: String(e) });
      }
      
      // AIPOS-R6L 大项A②: 部署bin绝对路径（禁裸命令赌PATH）
      const lybraBin = path.join(codeRepo, ".deploy/current/bin/lybra");
      const finalizeCmd = `${lybraBin} --workspace-root ${config.workspaceRoot} finalize --task-id ${taskId} --actor ${config.actor} --push --deploy`;
      const finalizeOutput = execSync(finalizeCmd, {
        cwd: codeRepo,
        encoding: "utf-8",
        stdio: "pipe",
      });
      
      currentLogger.info("auto-finalize-success", {
        task_id: taskId,
        output: finalizeOutput.slice(0, 500),
      });
      
      // AIPOS-R6L 大项A②: close走MCP queue_close（CLI无此子命令）
      const closeResp = await currentClient.queueClose({
        task_id: taskId,
        actor: config.actor,
        closure_evidence: `Auto-finalized after ${verdict}`,
      });
      
      currentLogger.info("auto-close-success", {
        task_id: taskId,
        close_response: closeResp,
      });
      
      ctx.ui?.notify?.(`自动 finalize+close 任务 ${taskId} (${verdict})`, "info");
      
      return true;
    } catch (e) {
      const errMsg = `自动finalize失败: ${taskId} - ${e instanceof Error ? e.message : String(e)}`;
      currentLogger.error("auto-finalize-failed", {
        task_id: taskId,
        error: e instanceof Error ? e.message : String(e),
      });
      // AIPOS-R6L 大项A②: 失败必出声（ctx.ui.notify，禁只进日志）
      ctx.ui?.notify?.(errMsg, "error");
      // 失败不停循环，继续处理其他任务
    }
  }
  
  return false;
}

/**
 * AIPOS-CONN-LOOP-2 ①: 检查是否有 completed 事件，如有则自动 return。
 * 完成判定 = task-progress completed 事件（既有动词兼职完成信号）。
 * return 素材自动组装 = result_summary(取 completed 事件 summary) + artifact_refs(取卡分支 commit 文件清单)。
 */
async function tryAutoReturn(ctx: any): Promise<boolean> {
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
    const dryRunResp = await currentClient.returnDryRun({
      task_id: currentTaskId,
      actor: config.actor,
      agent_instance: config.agentInstance,
      autonomy_mode: "Supervised",
      owner_policy_ref: config.ownerPolicyRef,
      result_summary: summary || "Task completed (auto-return)",
      artifact_refs: artifactRefs.length > 0 ? artifactRefs : undefined,
      active_session_id: getSessionId(ctx),
    });
    
    const dryRunToken = dryRunResp.dry_run_token;
    if (!dryRunToken) {
      currentLogger.error("auto-return-no-token", { task_id: currentTaskId, response: dryRunResp });
      return false;
    }
    
    // AIPOS-328: executor 自确认 return (owner_confirmation_token = OWNER_CONFIRMED 字面量)
    const confirmResp = await currentClient.returnConfirm({
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
    
    ctx.ui?.notify?.(`自动归还任务 ${currentTaskId}`, "info");
    
    // 清空当前任务信息
    currentTaskId = null;
    currentWorktreePath = null;
    
    return true;
  } catch (e) {
    currentLogger.error("auto-return-failed", {
      task_id: currentTaskId,
      error: e instanceof Error ? e.message : String(e),
    });
    return false;
  }
}

function stopLoop(
  ctx: { ui?: { notify?: (m: string, l?: string) => void } },
  reason: string,
  level: "info" | "warn" | "error" = "warn",
) {
  loopState.on = false;
  loopState.stoppedReason = reason;
  clearTimer();
  currentLogger?.write(level === "error" ? "ERROR" : level === "warn" ? "WARN" : "INFO", "loop-stopped", { reason });
  ctx.ui?.notify?.(`lybra 循环停止:${reason}`, level);
}

function getSessionId(ctx: any): string {
  try {
    const id = ctx?.sessionManager?.getSessionId?.();
    return id ? String(id) : "lybra-loop-session";
  } catch {
    return "lybra-loop-session";
  }
}

// F-EXT001-4(FIX1):tick 核心函数,直接调用(零 LLM 参与,零上下文污染)
async function doTick(pi: ExtensionAPI, ctx: any): Promise<void> {
  if (!loopState.on) return;
  if (!currentClient || !currentLogger) {
    stopLoop(ctx, "内部状态缺失(client/logger),请重新 /lybra on", "error");
    return;
  }
  let config;
  try {
    config = loadConfig(process.env);
  } catch (e) {
    stopLoop(ctx, `配置失效:${e instanceof Error ? e.message : String(e)}`, "error");
    return;
  }
  loopState.running = true;
  try {
    const outcome = await executeTick({
      client: currentClient,
      actor: config.actor,
      agentInstance: config.agentInstance,
      ownerPolicyRef: config.ownerPolicyRef,
      workspaceRoot: config.workspaceRoot,
      activeSessionId: getSessionId(ctx),
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
      ctx.ui.notify(
        `放行 ${outcome.task.task_id}(PreAuthorized)→ 冷启动执行 [${loopState.released}/${loopState.maxN}]`,
        "info",
      );
      // F-EXT001-8(FIX4):running 标志复位前置到 newSession 之前,确保任何路径(含 stale 异常)下可达
      loopState.running = false;
      expectingSwap = true;
      const kickoff = buildKickoff(outcome.cardAbsPath);
      try {
        const result = await ctx.newSession({
          withSession: async (freshCtx) => {
            await freshCtx.sendUserMessage(kickoff);
          },
        });
        if (result?.cancelled) {
          expectingSwap = false;
          stopLoop(ctx, "newSession 被拦截(循环停)", "warn");
        }
      } catch (e) {
        // F-EXT001-8(FIX4):newSession 异常(stale ctx 等)不再静默吞掉,落日志
        expectingSwap = false;
        currentLogger.warn("release-newSession-error", {
          task_id: outcome.task.task_id,
          error: e instanceof Error ? e.message : String(e),
        });
        stopLoop(ctx, `newSession 异常:${e instanceof Error ? e.message : String(e)}`, "error");
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
          ctx.ui?.notify?.(`检测到 ${heldTaskId} 已completed，执行自动return`, "info");
          
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
          const returned = await tryAutoReturn(ctx);
          if (returned) {
            // return成功，继续轮询下一张卡
            if (loopState.released < loopState.maxN) {
              loopState.running = false; // 确保复位
              scheduleNextTick(pi, ctx, 1000); // 1秒后再拉
            }
            return;
          } else {
            // return失败，记录错误但不停循环
            currentLogger.warn("held-auto-return-failed", { task_id: heldTaskId });
            ctx.ui?.notify?.(`自动return ${heldTaskId} 失败，请手动处理`, "warn");
          }
        }
      }
      
      // 没有completed事件，或return失败，按原逻辑stop
      stopLoop(ctx, outcome.reason, "warn");
      return;
    }

    // wait:轮询
    const elapsed = Date.now() - (loopState as { cycleStartMs: number }).cycleStartMs;
    if (elapsed >= config.maxWaitSec * 1000) {
      stopLoop(ctx, `轮询超时(${config.maxWaitSec}s)无信封内可认领卡`, "info");
      return;
    }
    const remain = config.maxWaitSec * 1000 - elapsed;
    const nextMs = Math.min(config.intervalSec * 1000, remain);
    // AIPOS-R6L 大项A③: 非有限值兜底 - 防配置错误导致 NaN 空转
    if (!Number.isFinite(nextMs) || nextMs <= 0) {
      const msg = `轮询间隔非法(nextMs=${nextMs}, intervalSec=${config.intervalSec}) - 停止循环`;
      currentLogger.error("invalid-poll-interval", { nextMs, intervalSec: config.intervalSec, remain });
      ctx.ui?.notify?.(msg, "error");
      stopLoop(ctx, msg, "error");
      return;
    }
    currentLogger.info("wait-poll", { reason: outcome.reason, nextMs });
    // AIPOS-R6I 靶③: 轮询结果可见 - 打印等待原因
    ctx.ui?.notify?.(`轮询: ${outcome.reason}，${Math.round(nextMs / 1000)}s 后再拉`, "info");
    // F-EXT001-8(FIX4):wait 路径复位 running,防定时器触发前 running 一直 true 拦截其他入口
    loopState.running = false;
    scheduleNextTick(pi, ctx, nextMs);
  } catch (e) {
    stopLoop(ctx, `tick 异常:${e instanceof Error ? e.message : String(e)}`, "error");
  } finally {
    // F-EXT001-8(FIX4):release 路径已前置复位 running;finally 保底兜所有其他路径
    loopState.running = false;
  }
}

export default function (pi: ExtensionAPI) {
  // --- 续跑:maxN>1 时,新 session 的卡执行完(settle)→ 直接调 tick ---
  pi.on("agent_settled", async (_event, ctx) => {
    if (!loopState.on) return;
    
    // AIPOS-R6I 靶②: 检查是否有 PASS 裁决，如有则自动 finalize+close
    await tryAutoFinalizeOnPassVerdict(ctx).catch((e) => {
      const errMsg = `agent_settled auto-finalize 错误: ${e instanceof Error ? e.message : String(e)}`;
      currentLogger?.warn("agent_settled-auto-finalize-error", {
        error: e instanceof Error ? e.message : String(e),
      });
      // AIPOS-R6L 第三轮修复(b): 失败必出声
      ctx.ui?.notify?.(errMsg, "error");
    });
    
    // AIPOS-CONN-LOOP-2 ①: 检查是否有 completed 事件，如有则自动 return
    if (currentTaskId) {
      const returned = await tryAutoReturn(ctx);
      if (returned) {
        // return 成功后，如果还未达到 maxN，继续轮询下一张卡
        if (loopState.released < loopState.maxN) {
          if (!loopState.running) {
            doTick(pi, ctx).catch((e) => {
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
      stopLoop(ctx, `达到 maxN(${loopState.maxN}),已放行 ${loopState.released} 张`, "info");
      return;
    }
    if (loopState.running) return; // 防重入
    // F-EXT001-4(FIX1):直接调用,不经 sendUserMessage
    doTick(pi, ctx).catch((e) => {
      // F-EXT001-8(FIX4):不再静默吞错,落日志
      currentLogger?.warn("agent_settled-doTick-error", {
        error: e instanceof Error ? e.message : String(e),
      });
    });
  });

  // --- session 生命周期:reload 不断;用户 /new|resume|fork|quit 停;循环自驱 newSession 不停 ---
  pi.on("session_shutdown", async (event, ctx) => {
    if (expectingSwap) {
      // 循环自己触发的 newSession:清 flag + 定时器,但保留循环状态供新 session 续跑
      expectingSwap = false;
      clearTimer();
      return;
    }
    if (event.reason === "reload") return; // /reload:循环继续(session_start 再续)
    clearTimer();
    if (loopState.on) stopLoop(ctx, `session ${event.reason}(循环中断)`, "warn");
  });

  pi.on("session_start", async (event) => {
    // F-EXT001-8(FIX4):双保险机制 — reload 时续跑 + 自驱 newSession(expectingSwap)时也续跑,防 agent_settled 单点失效
    if (loopState.on && (event.reason === "reload" || expectingSwap)) {
      if (expectingSwap) {
        currentLogger?.info("session_start-swap-resume", { reason: event.reason });
        expectingSwap = false; // 清 flag,防重复触发
      }
      if (loopState.running) return; // 防重入(agent_settled 可能先到)
      // F-EXT001-4(FIX1):直接调用,不经 sendUserMessage
      doTick(pi, event as any).catch((e) => {
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
      const parts = String(args || "").trim().split(/\s+/);
      const sub = parts[0] || "status";

      if (sub === "off") {
        if (!loopState.on) {
          ctx.ui.notify("lybra 循环未在运行", "info");
          return;
        }
        stopLoop(ctx, "用户 /lybra off", "info");
        return;
      }

      if (sub === "status") {
        const fp = currentTokenFp;
        ctx.ui.notify(
          [
            `lybra-loop 状态:`,
            `  运行中: ${loopState.on ? "是" : "否"}`,
            `  已放行: ${loopState.released}/${loopState.maxN}`,
            `  停止原因: ${loopState.stoppedReason || "(无)"}`,
            `  gate: ${currentGateUrl || "(未配置)"}  role: ${currentRole || "-"}  actor: ${currentActor || "-"}`,
            `  token: ${fp}`,
          ].join("\n"),
          "info",
        );
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
        let config;
        try {
          config = loadConfig(process.env);
        } catch (e) {
          ctx.ui.notify(`配置错误,循环未启动:${e instanceof Error ? e.message : String(e)}`, "error");
          return;
        }
        currentGateUrl = config.gateUrl;
        currentRole = config.role;
        currentActor = config.actor;

        // gate 连通性自检(initialize)
        const client = new GateMcpClient(config.gateUrl, config.token);
        try {
          await client.initialize();
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
        const queueInfo = await client.queueTasks().catch(() => []);
        const queueCount = queueInfo.length;
        const nextPollSec = config.intervalSec;
        
        ctx.ui.notify(
          [
            `lybra on: 已连 gate · 身份 ${config.role} · 信封 ${config.ownerPolicyRef} · 队列 ${queueCount} 张 · ${nextPollSec}s 后再拉`,
            `  启动自动领卡循环 (maxN=${maxN}, interval=${config.intervalSec}s, maxWait=${config.maxWaitSec}s)`,
            `  只放行信封内(PreAuthorized)卡; 信封外跳过; BLOCK/失败立停`,
            `  /lybra off 可停; /lybra status 查看状态`,
          ].join("\n"),
          "info",
        );
        
        // AIPOS-R6I 靶②: 存量收敛 - 启动时扫描已有 PASS 裁决但未 finalize 的卡自动补收
        tryAutoFinalizeOnPassVerdict(ctx).catch((e) => {
          const errMsg = `启动时 auto-finalize 错误: ${e instanceof Error ? e.message : String(e)}`;
          currentLogger.warn("startup-auto-finalize-error", {
            error: e instanceof Error ? e.message : String(e),
          });
          // AIPOS-R6L 第三轮修复(b): 失败必出声
          ctx.ui?.notify?.(errMsg, "error");
        });
        
        // F-EXT001-4(FIX1):非阻塞,直接调用第一轮 tick(不经 sendUserMessage)
        doTick(pi, ctx).catch((e) => {
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
      // 直接调用 doTick,与自动链同路径
      await doTick(pi, ctx);
    },
  });
}
