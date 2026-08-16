/**
 * loop-engine —— 循环状态机 + Logger + executeTick(纯循环逻辑,不 import pi)。
 *
 * executeTick 是"一轮拉取+决策"的核心:注入 GateReadFace(mock 友好),返回 TickOutcome,
 * 由 lybra-loop.ts 的 command handler 根据 outcome 决定冷启动(newSession)/继续轮询/停止。
 * 这样红线决策流可 headless 全覆盖,pi API 留在本体(无法 headless 测,见 TEST-EVIDENCE)。
 */

import { appendFileSync, existsSync, renameSync, statSync } from "node:fs";
import { logLine, classifyTasks, decideClaimDryRun, resolveCardPath, type AnyDict } from "./loop-decisions.ts";

/** executeTick 需要的 gate 读面(GateMcpClient 实现它;测试传 mock)。
 * AIPOS-R6R: 读面收缩为唯一读 + 通用调用器(verb 名由调用方传入, 参数 shape 读 schema)。
 */
export interface GateReadFace {
  queueTasks(): Promise<AnyDict[]>;
  callTool(name: string, args: AnyDict): Promise<AnyDict>;
}

// ---------------------------------------------------------------------------
// 循环状态(模块级,跨 session 替换存活——ESM 模块缓存,同进程唯一)
// ---------------------------------------------------------------------------

export interface LoopState {
  on: boolean;
  running: boolean; // 防重入:当前是否有 tick 在跑
  released: number; // 已放行冷启动的卡数
  maxN: number;
  triedTaskIds: Set<string>; // 本 on 周期已 dry-run 过的(避免对同一批信封外卡反复扫射)
  stoppedReason: string | null;
  startedAt: string | null;
}

export function freshState(): LoopState {
  return { on: false, running: false, released: 0, maxN: 1, triedTaskIds: new Set(), stoppedReason: null, startedAt: null };
}

// ---------------------------------------------------------------------------
// Logger:append-only JSON-lines + 简单大小轮转(>maxBytes → .log.1,新开)
// ---------------------------------------------------------------------------

export class Logger {
  readonly path: string;
  readonly maxBytes: number;

  constructor(path: string, maxBytes = 2 * 1024 * 1024) {
    this.path = path;
    this.maxBytes = maxBytes;
  }

  write(level: "INFO" | "WARN" | "ERROR", action: string, detail: Record<string, unknown>): void {
    try {
      if (existsSync(this.path) && statSync(this.path).size > this.maxBytes) {
        renameSync(this.path, `${this.path}.1`); // 覆盖旧 .1(common rotation)
      }
      appendFileSync(this.path, logLine(level, action, detail) + "\n", "utf-8");
    } catch {
      // 日志失败绝不影响循环主路径(日志只是辅助,真相在 gate)
    }
  }
  info(action: string, detail: Record<string, unknown>) {
    this.write("INFO", action, detail);
  }
  warn(action: string, detail: Record<string, unknown>) {
    this.write("WARN", action, detail);
  }
  error(action: string, detail: Record<string, unknown>) {
    this.write("ERROR", action, detail);
  }
}

// ---------------------------------------------------------------------------
// executeTick:一轮拉取 + 决策。红线决策流的核心。
// ---------------------------------------------------------------------------

export type TickOutcome =
  | { kind: "release"; task: AnyDict; cardAbsPath: string; policyId?: string }
  | { kind: "stop"; reason: string }
  | { kind: "wait"; reason: string };

export interface TickContext {
  client: GateReadFace;
  actor: string;
  agentInstance: string;
  ownerPolicyRef: string;
  workspaceRoot: string;
  activeSessionId: string;
  state: LoopState;
  logger: Logger;
}

/** claim_dry_run 参数(对齐 confirm_client.claim_args_from_task + AIPOS-250 PreAuthorized)。
 * F-EXT001-2(FIX1):对 task_id/metadata 加 null 护栏(缺字段 → 返回 null,调用者按 stop-error 处理)。
 * F-EXT001-5(FIX2):移除 task_path 字段,选择器只留 task_id(gate _normalize_selector 要求二选一)。
 */
export function buildClaimArgs(task: AnyDict, ctx: TickContext): AnyDict | null {
  // F-EXT001-2(FIX1)+F-EXT001-5(FIX2):护栏前置,缺 task_id → 返回 null
  const taskId = task.task_id;
  if (taskId == null) {
    return null; // 缺必需字段:调用者处理为 stop-error
  }
  const m = (task as { metadata?: AnyDict }).metadata;
  // metadata 缺失或非对象时用空对象(metadata 只读辅助字段)
  const meta = m && typeof m === "object" ? (m as AnyDict) : {};
  const instance = String(meta.agent_instance || ctx.agentInstance);
  return {
    task_id: String(taskId), // 显式转字符串,避免 undefined 泄漏
    // F-EXT001-5(FIX2):移除 task_path,gate 选择器要求 task_id/task_path 二选一
    actor: instance,
    agent_instance: instance,
    autonomy_mode: "PreAuthorized",
    owner_policy_ref: ctx.ownerPolicyRef,
    runtime_profile: String(meta.runtime_profile || "cc"),
    active_session_id: ctx.activeSessionId,
    context_bundle_ack: "ack",
    with_records: true,
    claim_reason: "lybra-loop auto-release (PreAuthorized envelope)",
  };
}

export async function executeTick(ctx: TickContext): Promise<TickOutcome> {
  const { client, state, logger } = ctx;
  let tasks: AnyDict[];
  try {
    tasks = await client.queueTasks();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    logger.error("fetch-failed", { error: msg });
    return { kind: "stop", reason: `拉取队列失败(技术错误,循环立停):${msg}` };
  }

  const { state: cls, held, claimable } = classifyTasks(tasks, ctx.actor);
  logger.info("fetched", { state: cls, held: held.length, claimable: claimable.length });

  // 一 session 一 task:已持有 → 停(先 return 手头的)
  if (cls === "held") {
    logger.info("stop-held", { task_ids: held.map((t) => t.task_id) });
    return { kind: "stop", reason: `已持有 ${held.map((t) => t.task_id).join(",")} —— 一卡一会话,先 return 再接新活` };
  }

  // 过滤本周期已试过的(避免对信封外卡反复 dry-run)
  const fresh = claimable.filter((t) => !state.triedTaskIds.has(String(t.task_id)));
  if (fresh.length === 0) {
    if (claimable.length > 0) {
      logger.warn("stop-all-envelope", { task_ids: claimable.map((t) => t.task_id) });
      return { kind: "stop", reason: `队列有 ${claimable.length} 张可认领卡但均已试过且在信封外(需 Owner confirm),本循环不绕` };
    }
    return { kind: "wait", reason: "暂无可认领任务" };
  }

  // 逐张 dry-run(PreAuthorized):放行→冷启动;信封外→跳过;BLOCK/error→立停
  for (const task of fresh) {
    const taskId = String(task.task_id);
    state.triedTaskIds.add(taskId);
    // F-EXT001-2+F-EXT001-5:buildClaimArgs 护栏前置
    const claimArgs = buildClaimArgs(task, ctx);
    if (!claimArgs) {
      logger.error("claim-args-invalid", { task_id: taskId, reason: "缺 task_id 字段" });
      return { kind: "stop", reason: `task ${taskId} 缺必需字段(task_id),无法 claim(循环立停)` };
    }
    let resp: AnyDict;
    try {
      resp = await client.callTool("lybra_queue_claim_dry_run", claimArgs);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logger.error("claim-dryrun-failed", { task_id: taskId, error: msg });
      return { kind: "stop", reason: `claim dry-run 失败(task=${taskId},循环立停):${msg}` };
    }
    const dec = decideClaimDryRun(resp, taskId);
    logger.info("claim-decision", { task_id: taskId, action: dec.action, reason: dec.reason });

    if (dec.action === "release") {
      // AIPOS-363F1: claim 成功后经 gate 取完整卡(frontmatter+body),写本地
      // 消除跨机 ENOENT(Mac 执行体 claim dev-local 卡时卡文件不在本地)
      const claimedDir = `${ctx.workspaceRoot.replace(/\/+$/, "")}/5_tasks/queue/claimed`;
      const cardFile = `${taskId.toLowerCase()}.md`; // gate 落盘文件名为小写
      const cardAbsPath = `${claimedDir}/${cardFile}`;

      // 从 gate 取完整卡并写本地(材料化)
      try {
        const previewResp = await client.callTool("lybra_task_preview", {
          task_id: taskId,
          actor: ctx.actor,
          include_body: true,
        });
        const renderedCard = previewResp?.data?.rendered_card_markdown;
        if (!renderedCard) {
          logger.error("materialize-card-no-content", { task_id: taskId });
          return { kind: "stop", reason: `取卡内容失败(task=${taskId}):gate 未返回 rendered_card_markdown` };
        }
        // 写本地
        const fs = await import("node:fs");
        const path = await import("node:path");
        fs.mkdirSync(path.dirname(cardAbsPath), { recursive: true });
        fs.writeFileSync(cardAbsPath, renderedCard, "utf-8");
        logger.info("materialize-card-ok", { task_id: taskId, path: cardAbsPath });

        // AIPOS-363F2: 材料化卡引用的 governance 文件
        const referencedFiles = previewResp?.data?.referenced_files_content;
        if (referencedFiles && Array.isArray(referencedFiles) && referencedFiles.length > 0) {
          for (const fileSpec of referencedFiles) {
            const filePath = fileSpec.path;
            const content = fileSpec.content;
            const error = fileSpec.error;

            if (error) {
              logger.warn("materialize-ref-file-error", { task_id: taskId, path: filePath, error });
              continue; // 继续处理其他文件，不因单个文件失败而停止
            }

            if (!content) {
              logger.warn("materialize-ref-file-no-content", { task_id: taskId, path: filePath });
              continue;
            }

            // 写到本地对应位置(相对 workspace_root)
            const refAbsPath = path.join(ctx.workspaceRoot, filePath);
            try {
              fs.mkdirSync(path.dirname(refAbsPath), { recursive: true });
              fs.writeFileSync(refAbsPath, content, "utf-8");
              logger.info("materialize-ref-file-ok", { task_id: taskId, path: filePath, abs_path: refAbsPath });
            } catch (writeErr) {
              const writeMsg = writeErr instanceof Error ? writeErr.message : String(writeErr);
              logger.error("materialize-ref-file-write-failed", { task_id: taskId, path: filePath, error: writeMsg });
              // 不停止整个流程，继续处理其他文件
            }
          }
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        logger.error("materialize-card-failed", { task_id: taskId, error: msg });
        return { kind: "stop", reason: `材料化卡失败(task=${taskId}):${msg}` };
      }

      return { kind: "release", task, cardAbsPath, policyId: dec.policyId };
    }
    if (dec.action === "skip-envelope") {
      continue; // 跳过这张,试下一张(不绕)
    }
    // stop-block / stop-error → 立停
    return { kind: "stop", reason: dec.reason };
  }

  // 本批 fresh 全部信封外
  logger.warn("batch-all-envelope", { task_ids: fresh.map((t) => t.task_id) });
  return { kind: "stop", reason: `本批 ${fresh.length} 张卡均在信封外(需 Owner confirm),本循环不绕` };
}
