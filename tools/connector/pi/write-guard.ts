/**
 * write-guard —— pi 专属薄胶水扩展(AIPOS-CONN-1 spike)。
 *
 * 把"未认领就写"在 harness 层当场拦下(edit/write/bash 执行前,不是 push 后)。
 * 检查点真相委托给 shared/claim-check(读 Lybra session 记录);本文件只做:
 *   1. tool_call 拦截 → 判写类 → 查活跃认领 → 放行(记写账)/ 阻断;
 *   2. /connector-bind 命令:把当前 pi session 显式绑定到一条 active claim。
 *
 * 安装(不改 pi 核心、可插拔):
 *   pi -e tools/connector/pi/write-guard.ts        # 临时加载
 *   或放进 .pi/extensions/ 经项目信任后自动发现
 *
 * 配置(env,均有默认):
 *   LYBRA_WORKSPACE_ROOT  gate workspace 根(默认 ~/ai-project-os/2_projects/lybra)
 *   LYBRA_AGENT_INSTANCE  本实例名(校验 claim 归属;默认 exec.lybra.kiwiai-dev)
 *   PI_CONNECTOR_DIR      binding + 写账目录(默认 ~/.pi/agent/connector)
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { getActiveClaim } from "../claim-check.ts";
import { appendWriteOp, type WriteOpEntry } from "../write-ledger.ts";

const HOME = process.env.HOME || "/tmp";
const WORKSPACE = process.env.LYBRA_WORKSPACE_ROOT || `${HOME}/ai-project-os/2_projects/lybra`;
const AGENT = process.env.LYBRA_AGENT_INSTANCE || "exec.lybra.kiwiai-dev";
const CONNECTOR_DIR = process.env.PI_CONNECTOR_DIR || `${HOME}/.pi/agent/connector`;
const BINDINGS_DIR = join(CONNECTOR_DIR, "bindings");
const LEDGER = join(CONNECTOR_DIR, "write-ledger.jsonl");

// bash 写操作的启发式模式(常见写命令;诚实标注:非穷举,见 SPIKE-REPORT)。
const BASH_WRITE = [
  /\bgit\s+(commit|add|push|merge|rebase|cherry-pick|reset|checkout)\b/i,
  /(?:^|\s)>{1,2}\s*\S/, // > / >> 重定向写文件(printf "x" > f 也命中)
  /\b(mv|cp|rm|rmdir|mkdir|install|tee|dd|patch)\b/i,
  /\b(curl|wget)\b.*\s-[oO]\s/i, // curl -o / wget -O 写文件
  /\bsed\s+(-i|--in-place)\b/i,
  /\bawk\s+-i\b/i,
  /<<[-]?\s*\w+/, // heredoc(含写文件)
];

function isWriteBash(cmd: string): boolean {
  return BASH_WRITE.some((p) => p.test(cmd));
}

function sessionKey(ctx: any): string {
  const f = ctx?.sessionManager?.getSessionFile?.();
  if (!f) return "ephemeral";
  return String(f).split("/").pop()!.replace(/[^\w.-]/g, "_") || "ephemeral";
}

function readBinding(key: string): { taskId?: string; claimId?: string } {
  const p = join(BINDINGS_DIR, `${key}.json`);
  try {
    return JSON.parse(readFileSync(p, "utf-8"));
  } catch {
    return {};
  }
}

function writeBinding(key: string, data: Record<string, unknown>): void {
  mkdirSync(BINDINGS_DIR, { recursive: true });
  writeFileSync(join(BINDINGS_DIR, `${key}.json`), JSON.stringify(data, null, 2));
}

/** 检查点核心:当前 pi session 是否绑定了活跃认领。返回放行/阻断决策。 */
function checkpoint(ctx: any): { allow: boolean; taskId?: string; reason: string; claimPath?: string } {
  const key = sessionKey(ctx);
  const { taskId } = readBinding(key);
  if (!taskId) {
    return { allow: false, reason: `no active claim for pi session "${key}" — bind via /connector-bind <task_id> first` };
  }
  const claim = getActiveClaim(WORKSPACE, taskId, AGENT);
  if (claim.active) {
    return { allow: true, taskId, reason: `active claim ${claim.claimId}`, claimPath: claim.sessionRecordPath };
  }
  return { allow: false, taskId, reason: claim.reason || `claim not active for ${taskId}`, claimPath: claim.sessionRecordPath };
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event: any, ctx: any) => {
    const tool = event.toolName as string;
    let writeTarget: string | undefined;
    if (tool === "write" || tool === "edit") {
      writeTarget = event.input?.path as string;
    } else if (tool === "bash") {
      const cmd = String(event.input?.command ?? "");
      if (!isWriteBash(cmd)) return undefined; // 非写类 bash 放行
      writeTarget = cmd.slice(0, 120);
    } else {
      return undefined; // read/grep 等只读工具不拦
    }

    const cp = checkpoint(ctx);
    const entry: WriteOpEntry = {
      ts: new Date().toISOString(),
      tool,
      decision: cp.allow ? "allow" : "block",
      taskId: cp.taskId,
      piSession: sessionKey(ctx),
      target: writeTarget,
      reason: cp.reason,
    };
    appendWriteOp(LEDGER, entry);

    if (!cp.allow) {
      if (ctx?.hasUI) ctx.ui.notify(`🛑 write blocked: ${cp.reason}`, "warning");
      return { block: true, reason: cp.reason };
    }
    return undefined; // 放行
  });

  pi.registerCommand("connector-bind", {
    description: "把当前 pi session 绑定到一条 Lybra 活跃认领(AIPOS-CONN-1 spike)。用法:/connector-bind <task_id>",
    handler: async (args: string, ctx: any) => {
      const taskId = (args || "").trim();
      if (!taskId) {
        ctx.ui.notify("用法:/connector-bind <task_id>", "warn");
        return;
      }
      const claim = getActiveClaim(WORKSPACE, taskId, AGENT);
      if (!claim.active) {
        ctx.ui.notify(`绑定失败:${claim.reason || `${taskId} 无活跃认领`}`, "error");
        return;
      }
      writeBinding(sessionKey(ctx), { taskId, claimId: claim.claimId, boundAt: new Date().toISOString(), piSession: sessionKey(ctx) });
      ctx.ui.notify(`✅ 已绑定 ${taskId}(claim ${claim.claimId})\n依据:${claim.sessionRecordPath}`, "info");
    },
  });

  pi.registerCommand("connector-unbind", {
    description: "解除当前 pi session 的认领绑定(AIPOS-CONN-1 spike)。",
    handler: async (_args: string, ctx: any) => {
      const key = sessionKey(ctx);
      writeBinding(key, { unboundAt: new Date().toISOString() });
      ctx.ui.notify(`已解除 pi session "${key}" 的绑定`, "info");
    },
  });
}
