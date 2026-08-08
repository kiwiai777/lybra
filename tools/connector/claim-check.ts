/**
 * claim-check —— Lybra connector 的"活跃认领"判定(harness 无关核心)。
 *
 * 真相 = 文件即真相:读 `<workspace>/5_tasks/records/sessions/<TASK>/` 下的 session 记录,
 * 取最新一条,判其 frontmatter 的 `session_status` 是否为 "claimed"(已认领、未归还)。
 * 这是 executor 写操作检查点的唯一放行依据;无 pi / 无 node:http 依赖。
 *
 * 设计要点:
 *  - 只读、纯 fs;失败不猜(返回 active:false + error),由调用方决定(BLOCK 还是放行)。
 *  - frontmatter 极简解析(--- 围栏 + `key: value`),不引 yaml 依赖(harness 无关 + 薄)。
 *  - canonical_agent_instance 用于可选校验:claim 必须是"我"这个实例发起的才算数。
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

export interface ActiveClaim {
  active: boolean; // 是否存在"已认领未归还"的 session 记录
  taskId: string;
  sessionStatus?: string; // claimed | returned | audit_verdict | ...
  claimId?: string;
  agentInstance?: string;
  sessionRecordPath?: string; // 判定依据文件(审计可见)
  reason?: string; // active=false 时的原因(无记录/非 claimed/解析失败)
}

/** 极简 frontmatter 解析:取首对 `---` 围栏内的 `key: value`。 */
function parseFrontmatter(text: string): Record<string, string> {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return {};
  const out: Record<string, string> = {};
  for (const line of m[1].split(/\r?\n/)) {
    const i = line.indexOf(":");
    if (i < 0) continue;
    const k = line.slice(0, i).trim();
    const v = line.slice(i + 1).trim().replace(/^['"]|['"]$/g, "");
    if (k) out[k] = v;
  }
  return out;
}

/** 扫描某 task 的 session 记录目录,返回最新(按 mtime)的 .md 路径与解析结果。 */
export function getActiveClaim(
  workspaceRoot: string,
  taskId: string,
  expectedAgentInstance?: string,
): ActiveClaim {
  const dir = join(workspaceRoot, "5_tasks", "records", "sessions", taskId);
  let files: string[];
  try {
    files = readdirSync(dir).filter((f) => f.endsWith(".md"));
  } catch {
    return { active: false, taskId, reason: `no session records dir for ${taskId}` };
  }
  if (files.length === 0) {
    return { active: false, taskId, reason: `no session records for ${taskId}` };
  }
  // 取 mtime 最新的一条作为判定依据
  let latest = files[0];
  let latestMtime = 0;
  for (const f of files) {
    const m = statSync(join(dir, f)).mtimeMs;
    if (m > latestMtime) {
      latestMtime = m;
      latest = f;
    }
  }
  const path = join(dir, latest);
  let fm: Record<string, string>;
  try {
    fm = parseFrontmatter(readFileSync(path, "utf-8"));
  } catch (e) {
    return {
      active: false,
      taskId,
      sessionRecordPath: path,
      reason: `failed to read ${latest}: ${e instanceof Error ? e.message : String(e)}`,
    };
  }
  const status = fm.session_status;
  const claim: ActiveClaim = {
    active: status === "claimed",
    taskId,
    sessionStatus: status,
    claimId: fm.claim_id,
    agentInstance: fm.canonical_agent_instance || fm.actor,
    sessionRecordPath: path,
  };
  if (!claim.active) {
    claim.reason = `session_status=${status || "(missing)"} (need "claimed")`;
  } else if (expectedAgentInstance && claim.agentInstance && claim.agentInstance !== expectedAgentInstance) {
    claim.active = false;
    claim.reason = `claim owned by ${claim.agentInstance}, not ${expectedAgentInstance}`;
  }
  return claim;
}
