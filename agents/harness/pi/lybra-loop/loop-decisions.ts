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
// AIPOS-F4 大项B/C: 严重级→级别 单一映射(渲染层只引此函数, 禁现场定级)
// 与 schema/transitions.schema.json severity_semantics.mapping 对齐:
//   auto_recoverable → warn;needs_human / bug → error;未知/缺省 → error(保守)。
// ---------------------------------------------------------------------------

export type NotifyLevel = "info" | "warn" | "error";

export const SEVERITY_LEVELS: Record<string, NotifyLevel> = {
  auto_recoverable: "warn",
  needs_human: "error",
  bug: "error",
};

export const DEFAULT_SEVERITY = "needs_human";

export function severityToLevel(severity: string | null | undefined): NotifyLevel {
  if (!severity) return "error";
  return SEVERITY_LEVELS[severity] ?? "error";
}

/**
 * 把 gate 应答里的拒因(blocking_reasons/errors/message)序列化为可读文本。
 * 修复 [object Object]: 拒绝理由可能是字符串数组, 也可能是 {category,message,details}
 * 对象数组(如 _teaching_error 的 errors) —— 逐项取 message/字符串化, 不再裸 join。
 */
export function stringifyReasons(reasons: unknown): string {
  if (reasons == null) return "";
  if (typeof reasons === "string") return reasons;
  if (Array.isArray(reasons)) {
    const parts = reasons.map((r) => {
      if (typeof r === "string") return r;
      if (r && typeof r === "object") {
        const o = r as AnyDict;
        if (typeof o.message === "string") return o.message;
        if (typeof o.error === "string") return o.error;
        try {
          return JSON.stringify(o);
        } catch {
          return "";
        }
      }
      return String(r);
    });
    return parts.filter(Boolean).join("; ");
  }
  if (typeof reasons === "object") {
    try {
      return JSON.stringify(reasons);
    } catch {
      return String(reasons);
    }
  }
  return String(reasons);
}

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
  | { action: "already-held"; taskId: string; reason: string }
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

  // 2. 技术拒绝/失败(含 PreAuthorized 请求但任务不再可 claim 的 BLOCK)⇒ 先做状态机幂等识别, 再立停
  if (isError || verdict === "BLOCK") {
    const reasons =
      (resp as { blocking_reasons?: unknown }).blocking_reasons ||
      (resp as { owner_confirmation_reasons?: unknown }).owner_confirmation_reasons ||
      [];
    // AIPOS-F38 大项C: 状态机 BLOCK 幂等识别 — 门拒因里出现“已认领/状态不匹配”类
    // (期望 pending 实为 claimed)不是故障, 是门在说“这张卡已是 claimed”。识别为
    // already-held 交调用方按持有者分流(本工位→继续执行;他人→跳过出声),
    // 禁以“应答语义不明”停循环。真门 blocking_reasons 为字符串数组
    // (2026-08-24 实捕), 匹配器兼容 string|object 两形态。
    const reasonText = (r: unknown): string =>
      typeof r === "string" ? r : String((r as AnyDict | null)?.message || "");
    const alreadyClaimed =
      Array.isArray(reasons) &&
      reasons.some(
        (r) => reasonText(r).includes("claimed") || reasonText(r).includes("已被认领"),
      );
    if (alreadyClaimed) {
      return {
        action: "already-held",
        taskId,
        reason: `状态机 BLOCK=已认领(幂等识别): ${stringifyReasons(reasons)}`,
      };
    }
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
// AIPOS-F16: 余热收尾决策(纯) — 额度尽后循环不整停, 只停新领卡。
// ---------------------------------------------------------------------------

export type CooldownPlan =
  | { action: "terminal-stop"; reason: string }
  | { action: "wait"; voiceLine: string; nextMs: number };

/**
 * AIPOS-F16 余热步进:额度用尽(released>=maxN)后每轮 tick 只判在途卡。
 *  • 在途卡(claimed_by=本工位且未收口)= 0 → 终停, 停语带路(/lybra on N)。
 *  • 仍有在途卡 → 继续余热等待(interval 秒后再收)。
 */
export function planCooldownStep(
  inFlightTaskIds: string[],
  released: number,
  maxN: number,
  intervalSec: number,
): CooldownPlan {
  if (inFlightTaskIds.length === 0) {
    return {
      action: "terminal-stop",
      reason: `额度用尽(${released}/${maxN})且在途卡全部收口`,
    };
  }
  return {
    action: "wait",
    voiceLine: `余热: 在途卡 ${inFlightTaskIds.length} 张(${inFlightTaskIds.join(",")}), ${intervalSec}s 后再收`,
    nextMs: Math.max(1, Math.round(intervalSec * 1000)),
  };
}

// ---------------------------------------------------------------------------
// 纯文本生成
// ---------------------------------------------------------------------------

/** 冷启动 kickoff(措辞对齐 _shared/extensions/claim.ts;token/密钥永不出现)。
 * F-EXT001-7(FIX3):断言钉 — kickoff 引用的路径必须是放行后位置(claimed),不含 pending。
 */
/**
 * AIPOS-F43 大项C: 纪律注入全路径覆盖.
 * The single-source discipline injection text (F41 注入 writer).
 * All three delivery paths (cold start / resume / re-deliver) must carry this exact text.
 */
export const DISCIPLINE_INJECTION = [
  `最后一步必须是 gate 提交,没有门记录=没做完。`,
  `盘上已有 RETURN.md 骨架(连接器投递时自动落盘)——读它即知身在何卡、欠什么。`,
].join("\n");

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
    DISCIPLINE_INJECTION,  // AIPOS-F43 大项C: 携带纪律注入(F41 单源)
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

// ---------------------------------------------------------------------------
// AIPOS-F43 大项A: 投递即落 RETURN.md 骨架
// ---------------------------------------------------------------------------

/**
 * Parse YAML frontmatter from card markdown.
 * Returns empty object if no frontmatter found.
 */
export function parseCardFrontmatter(cardMarkdown: string): AnyDict {
  const trimmed = cardMarkdown.trimStart();
  if (!trimmed.startsWith("---")) return {};
  const endIdx = trimmed.indexOf("---", 3);
  if (endIdx === -1) return {};
  
  const yamlText = trimmed.slice(3, endIdx);
  const result: AnyDict = {};
  
  // Simple YAML parser for frontmatter (key: value format)
  for (const line of yamlText.split("\n")) {
    const colonIdx = line.indexOf(":");
    if (colonIdx === -1) continue;
    const key = line.slice(0, colonIdx).trim();
    const value = line.slice(colonIdx + 1).trim();
    if (key) result[key] = value;
  }
  
  return result;
}

/**
 * Extract acceptance section from card markdown.
 * Looks for "## 验收" or "## 审计对象" headings.
 */
export function extractAcceptanceSection(cardMarkdown: string): string {
  const lines = cardMarkdown.split("\n");
  let inSection = false;
  const acceptanceLines: string[] = [];
  
  for (const line of lines) {
    if (line.match(/^##\s+(验收|审计对象)/)) {
      inSection = true;
      acceptanceLines.push(line);
      continue;
    }
    if (inSection) {
      if (line.match(/^##\s+/)) {
        // Hit next section, stop
        break;
      }
      acceptanceLines.push(line);
    }
  }
  
  return acceptanceLines.join("\n").trim();
}

/**
 * Render RETURN.md skeleton from card markdown.
 * AIPOS-F43 大项A: 内容单源=卡面(frontmatter + 验收节).
 * Supports both executor cards (交付报告) and audit cards (审计报告).
 */
export function renderReturnSkeleton(cardMarkdown: string, taskId: string): string {
  const fm = parseCardFrontmatter(cardMarkdown);
  const taskMode = String(fm.task_mode || "code");
  const acceptanceSection = extractAcceptanceSection(cardMarkdown);
  
  if (taskMode === "audit") {
    // Audit card skeleton
    return [
      `# ${taskId} 审计报告`,
      ``,
      `## 审计裁决`,
      ``,
      `verdict: (PASS / FAIL / BLOCK)`,
      ``,
      `## 一句话结论`,
      ``,
      `(待填写)`,
      ``,
      `## 验收清单`,
      ``,
      acceptanceSection || "(无验收清单)",
      ``,
      `---`,
      `(骨架由连接器投递时自动落盘,内容单源=卡面。审计体完成后填写上方各节。)`,
    ].join("\n");
  } else {
    // Executor card skeleton
    return [
      `# ${taskId} 交付报告`,
      ``,
      `## 一句话结论`,
      ``,
      `(待填写)`,
      ``,
      `## 状态`,
      ``,
      `IN_PROGRESS`,
      ``,
      `## 验收清单`,
      ``,
      acceptanceSection || "(无验收清单)",
      ``,
      `---`,
      `(骨架由连接器投递时自动落盘,内容单源=卡面。执行体完成后填写上方各节。)`,
    ].join("\n");
  }
}

/**
 * Parse RETURN.md status line.
 * Returns the status value or null if not found.
 */
export function parseReturnStatus(returnContent: string): string | null {
  const lines = returnContent.split("\n");
  let inStatusSection = false;
  
  for (const line of lines) {
    if (line.match(/^##\s+状态/)) {
      inStatusSection = true;
      continue;
    }
    if (inStatusSection) {
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith("#")) {
        return trimmed;
      }
    }
  }
  
  return null;
}

// ---------------------------------------------------------------------------
// AIPOS-F43 大项B: 空闲带路(held + IN_PROGRESS → 具体指引)
// ---------------------------------------------------------------------------

/**
 * Build held guidance message with specific context.
 * AIPOS-F43 大项B: 出声含卡ID/报告路径/剩余验收项 + 纪律注入.
 */
export function buildHeldGuidance(taskId: string, returnPath: string, acceptanceText: string): string {
  return [
    `复工提醒:你的工位持有卡 ${taskId},盘上已有 RETURN.md 骨架(状态:IN_PROGRESS)。`,
    ``,
    `报告路径:${returnPath}`,
    ``,
    `剩余验收项:`,
    acceptanceText || "(无验收清单)",
    ``,
    DISCIPLINE_INJECTION,
  ].join("\n");
}
