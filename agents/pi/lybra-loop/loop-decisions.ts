/**
 * loop-decisions —— lybra-loop 的纯逻辑核心(红线编码在此,零 IO,零 pi 依赖)
 *
 * 这里集中三件事,全部可 headless 单测:
 *  1. classifyTasks —— 三态分类(复刻 agent_connector.classify:持有者跟 claimed_by)。
 *  2. decideClaimDryRun —— claim dry-run 决策门。**红线"发起≠授权"的代码就在这里**:
 *     `owner_confirmation_required === true` ⇒ 绝不自动 confirm(不绕),只跳过/停。
 *  3. kickoff / logLine —— 文本生成(纯)。
 *
 * 不 import 任何 pi / node 模块,保证 `node tests/loop-decisions.test.ts` 能直接跑。
 */

export const AUTONOMY_PREAUTHORIZED = "PreAuthorized";
export const AUTONOMY_SUPERVISED = "Supervised";

export type AnyDict = Record<string, unknown>;

// ---------------------------------------------------------------------------
// 三态分类 —— 与 tools/aipos_cli/agent_connector.py:classify / _is_holder 对齐
// ---------------------------------------------------------------------------

export type ClassifyState = "held" | "claimable" | "none";

export interface ClassifyResult {
  state: ClassifyState;
  /** 该 actor 已持有(claimed 且 claimed_by 匹配)的任务。held 前置:一 session 一 task。 */
  held: AnyDict[];
  /** pending 且 advisory actor-match(assigned_to/agent_instance/claimed_by 命中其一)。 */
  claimable: AnyDict[];
}

function taskMeta(task: AnyDict): AnyDict {
  const m = (task as { metadata?: unknown }).metadata;
  return m && typeof m === "object" ? (m as AnyDict) : {};
}

/** Advisory actor-match(复刻 agent_connector.actor_matches):{assigned_to, agent_instance, claimed_by}。 */
export function actorMatches(task: AnyDict, actor: string): boolean {
  const m = taskMeta(task);
  return actor === m.assigned_to || actor === m.agent_instance || actor === m.claimed_by;
}

/** 持有者判定(F-248-o3-1:跟 claimed_by,不跟 assigned_to/agent_instance)。 */
export function isHolder(task: AnyDict, actor: string): boolean {
  const m = taskMeta(task);
  return !!m.claimed_by && actor === m.claimed_by;
}

/** 已归还判定:return 结算后卡留在 claimed/ 等审计,归还标志是这两个字段
 *  (与 gate confirm_client 的 already_returned 判定同一谓词)。 */
export function isReturned(task: AnyDict): boolean {
  const m = taskMeta(task);
  return m.executor_status === "completed" || m.audit_readiness === "ready";
}

export function classifyTasks(tasks: AnyDict[], actor: string): ClassifyResult {
  const list = Array.isArray(tasks) ? tasks : [];
  const held = list.filter(
    (t) =>
      String((t as { queue_state?: unknown }).queue_state || "") === "claimed" &&
      isHolder(t, actor) &&
      !isReturned(t),
  );
  const claimable = list.filter(
    (t) => String((t as { queue_state?: unknown }).queue_state || "") === "pending" && actorMatches(t, actor),
  );
  const state: ClassifyState = held.length > 0 ? "held" : claimable.length > 0 ? "claimable" : "none";
  return { state, held, claimable };
}

// ---------------------------------------------------------------------------
// claim dry-run 决策门 —— 红线"发起≠授权"的直接编码
// ---------------------------------------------------------------------------
// gate 应答契约(源码核实:tools/mcp_server/tools.py):
//   • PreAuthorized 放行(_preauthorized_claim_autorelease):gate 一阶段落盘 claim,
//     structuredContent = { autonomy_mode:"PreAuthorized", owner_confirmation_required:false,
//                           preauthorized_release:true, ok:true, isError:false }
//   • Supervised 回落(_decorate_queue_claim_dry_run):预览不落盘,
//     structuredContent = { autonomy_mode:"Supervised", owner_confirmation_required:true,
//                           dry_run_token:<id>, owner_confirmation_reasons:[...] }
//   • BLOCK / 不可 claim:isError:true(或 verdict:"BLOCK")。
//
// 映射到卡规格 §4 停止条件:
//   release           → gate 已放行 ⇒ 冷启动执行该卡
//   skip-envelope     → 信封外(Supervised)⇒ 跳过这张、记录、不绕(规格:"信封外→跳过并记录")
//   stop-block        → 技术拒绝/失败 ⇒ 循环立停(规格:"任一卡 BLOCK/失败→循环立停,不跳过继续")
//   stop-error        → 无应答/协议错 ⇒ 循环立停
// ---------------------------------------------------------------------------

export type ClaimDecision =
  | { action: "release"; taskId: string; policyId?: string; reason: string }
  | { action: "skip-envelope"; taskId: string; reason: string }
  | { action: "stop-block"; taskId?: string; reason: string }
  | { action: "stop-error"; reason: string };

export function decideClaimDryRun(resp: AnyDict | null | undefined, taskId: string): ClaimDecision {
  // 1. 无应答(网络/协议)⇒ 立停
  if (!resp || typeof resp !== "object") {
    return { action: "stop-error", reason: "dry-run 无 structuredContent 应答(协议/网络错误)" };
  }
  const verdict = String((resp as { verdict?: unknown }).verdict || "");
  const isError = (resp as { isError?: unknown }).isError === true;
  const ownerConfirmationRequired = (resp as { owner_confirmation_required?: unknown }).owner_confirmation_required === true;
  const preauthorized =
    (resp as { preauthorized_release?: unknown }).preauthorized_release === true ||
    (resp as { autonomy_mode?: unknown }).autonomy_mode === AUTONOMY_PREAUTHORIZED;

  // 2. 技术拒绝/失败(含 PreAuthorized 请求但任务不再可 claim 的 BLOCK)⇒ 立停
  if (isError || verdict === "BLOCK") {
    const reasons =
      (resp as { blocking_reasons?: unknown }).blocking_reasons ||
      (resp as { owner_confirmation_reasons?: unknown }).owner_confirmation_reasons ||
      [];
    const detail = Array.isArray(reasons) && reasons.length ? JSON.stringify(reasons) : verdict || "no reasons";
    return { action: "stop-block", taskId, reason: `gate BLOCK(isError=${isError}): ${detail}` };
  }

  // 3. PreAuthorized 放行(gate 已落盘 claim)⇒ 冷启动
  if (!ownerConfirmationRequired && preauthorized) {
    return {
      action: "release",
      taskId,
      policyId: (resp as { owner_policy_ref?: unknown }).owner_policy_ref as string | undefined,
      reason: `PreAuthorized 放行(policy=${(resp as { owner_policy_ref?: unknown }).owner_policy_ref || "?"})`,
    };
  }

  // 4. 信封外 / Supervised(需 Owner confirm)⇒ 跳过,绝不自动 confirm(红线)
  if (ownerConfirmationRequired) {
    return {
      action: "skip-envelope",
      taskId,
      reason: `信封外(Supervised,owner_confirmation_required=true,dry_run_token=${
        (resp as { dry_run_token?: unknown }).dry_run_token || "?"
      });需 Owner confirm,本循环不绕`,
    };
  }

  // 5. 语义不明(既没放行也没要求 confirm,又不是 BLOCK)⇒ 保守立停
  return {
    action: "stop-block",
    taskId,
    reason: `应答语义不明(无 preauthorized 也无 owner_confirmation_required):autonomy=${
      (resp as { autonomy_mode?: unknown }).autonomy_mode || "?"
    } verdict=${verdict || "?"}`,
  };
}

// ---------------------------------------------------------------------------
// 纯文本生成
// ---------------------------------------------------------------------------

/** 冷启动 kickoff(措辞对齐 _shared/extensions/claim.ts;token/密钥永不出现)。
 * F-EXT001-7(FIX3):断言钉 — kickoff 引用的路径必须是放行后位置(claimed),不含 pending。
 */
export function buildKickoff(cardAbsPath: string): string {
  // F-EXT001-7(FIX3):断言钉 — 放行后路径必须在 claimed,不在 pending
  if (cardAbsPath.includes("queue/pending/")) {
    throw new Error(`[F-EXT001-7 断言钉] kickoff 路径含 queue/pending/(放行前路径),违反约束:${cardAbsPath}`);
  }
  return [
    `冷启动(lybra-loop 自动放行)。你的角色与红线见已加载的 AGENTS.md。`,
    `执行已为你认领(PreAuthorized 放行)的任务卡:${cardAbsPath}`,
    `按你 AGENTS.md 里的知识入口去读这张卡并独立执行,一切以卡+知识入口为准,不依赖任何历史上下文。`,
    `遇护栏拦截即说明并停,不绕过。`,
    `最后一步必须是 gate 提交, 没有门记录=没做完。`,
  ].join("\n");
}

/** 卡相对 path → 绝对路径(gate queue_list 的 task.path 相对 workspaceRoot)。 */
export function resolveCardPath(taskPath: unknown, workspaceRoot: string): string | null {
  const p = typeof taskPath === "string" ? taskPath.trim() : "";
  if (!p) return null;
  // 已经是绝对路径就直接用;否则拼 workspaceRoot。
  if (p.startsWith("/")) return p;
  const root = workspaceRoot.replace(/\/+$/, "");
  return `${root}/${p.replace(/^\.?\//, "")}`;
}

/** 一行审计日志(JSON-lines,便于 grep/jq)。 */
export function logLine(level: "INFO" | "WARN" | "ERROR", action: string, detail: Record<string, unknown>): string {
  const ts = new Date().toISOString();
  return JSON.stringify({ ts, level, action, detail });
}
