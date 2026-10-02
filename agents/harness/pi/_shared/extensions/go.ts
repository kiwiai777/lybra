/**
 * AIPOS-F73B件③: 工位无参一词命令 /go
 *
 * 功能: 启动只问产品「我这个实例名下已认领的卡是哪张」，取工作树与报告落点，
 *       向模型发开工提示（只含工作树、报告落点、卡路径）。
 *
 * AIPOS-F86 件①: 工作树 / 报告落点 / 卡路径一律只读 `lybra my-tasks --json` 输出的
 *   worktree_path / report_path / card_path（产品侧唯一推导: next_resolver.card_workstation_view,
 *   与认领建树同一函数）；本扩展不做任何路径拼接。工作树不存在 / 不可推导时只转述产品给出的拒因，
 *   文案零门动词（认领由驱动方完成）。
 *
 * 源码母本住产品仓 agents/harness/pi/_shared/extensions/，由 lybra sync 分发到工位。
 *
 * 租约/心跳/调度一概不做。单次查询，无循环，无常驻。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** 工作树尚未建立时的统一文案（零门动词: 不教工位认领或推进）。 */
export const WORKTREE_PENDING_TEXT =
  "工作树尚未建立:认领由驱动方完成, 请等待驱动方完成认领后再 /go;若持续存在按 block-and-report 上报";

export type GoPlan =
  | { kind: "none"; message: string }
  | { kind: "refused"; taskId: string; message: string }
  | { kind: "kickoff"; taskId: string; worktreePath: string; reportPath: string; cardPath: string; kickoff: string };

function refusalText(refusal: unknown): string {
  if (refusal && typeof refusal === "object") {
    const r = refusal as { code?: unknown; reason?: unknown };
    const code = typeof r.code === "string" ? r.code : "UNKNOWN";
    const reason = typeof r.reason === "string" ? r.reason : "";
    return reason ? `${code}: ${reason}` : code;
  }
  return "产品未给出拒因字段";
}

/**
 * 纯函数: 由 `lybra my-tasks --json` 的输出决定 /go 的结果（无 I/O, 夹具可直接调用）。
 * 只读产品字段; 字段缺失 = 拒（fail-closed）, 不在本地补推。
 */
export function planGo(myTasksData: unknown): GoPlan {
  const tasks = (myTasksData && typeof myTasksData === "object" && Array.isArray((myTasksData as any).tasks))
    ? ((myTasksData as any).tasks as any[])
    : null;
  if (tasks === null) {
    return { kind: "none", message: "my-tasks 输出缺 tasks 数组(产品输出形变), 无法开工; 按 block-and-report 上报" };
  }
  const claimedTasks = tasks.filter((t: any) => t && t.queue_state === "claimed");
  if (claimedTasks.length === 0) {
    return { kind: "none", message: "无已认领卡，等待任务分配..." };
  }

  // 取第一张（如果有多张，优先级逻辑由产品定）
  const task = claimedTasks[0];
  const taskId = String(task.task_id || "");

  if (task.worktree_exists !== true || typeof task.worktree_path !== "string" || !task.worktree_path) {
    const notCreated = task.worktree_refusal && task.worktree_refusal.code === "WORKTREE_NOT_CREATED";
    const detail = notCreated ? "" : `\n产品拒因: ${refusalText(task.worktree_refusal)}`;
    const where = typeof task.worktree_path === "string" && task.worktree_path ? `(${task.worktree_path})` : "";
    return { kind: "refused", taskId, message: `${taskId} ${WORKTREE_PENDING_TEXT}${where}${detail}` };
  }
  if (typeof task.report_path !== "string" || !task.report_path) {
    return {
      kind: "refused",
      taskId,
      message: `${taskId} 报告落点不可推导, 无法开工; 按 block-and-report 上报\n产品拒因: ${refusalText(task.report_refusal)}`,
    };
  }
  if (typeof task.card_path !== "string" || !task.card_path) {
    return { kind: "refused", taskId, message: `${taskId} my-tasks 未给出 card_path, 无法开工; 按 block-and-report 上报` };
  }

  const kickoff = `已认领任务卡 ${taskId}。

工作树路径: ${task.worktree_path}
报告落点: ${task.report_path}
任务卡路径: ${task.card_path}

按你的 AGENTS.md 执行，完成后写报告到报告落点。`;
  return {
    kind: "kickoff",
    taskId,
    worktreePath: task.worktree_path,
    reportPath: task.report_path,
    cardPath: task.card_path,
    kickoff,
  };
}

export default function (pi: ExtensionAPI) {
  pi.registerCommand("go", {
    description: "查询本实例已认领的任务卡，并以产品给出的工作树/报告落点发送开工提示",
    handler: async (args, ctx) => {
      try {
        // 1. 读取工位配置
        const fs = await import("node:fs");
        const path = await import("node:path");
        const { execFile } = await import("node:child_process");
        const { promisify } = await import("node:util");
        const execFileAsync = promisify(execFile);

        // 2. 从 .lybra/role 读取 instance
        const workstationRoot = process.cwd();

        const roleConfigPath = path.join(workstationRoot, ".lybra", "role");
        if (!fs.existsSync(roleConfigPath)) {
          ctx.ui.notify(".lybra/role not found. Run 'lybra enroll' first.", "error");
          return;
        }

        const roleConfig = JSON.parse(fs.readFileSync(roleConfigPath, "utf-8"));
        const agentInstance = roleConfig.instance;

        if (!agentInstance) {
          ctx.ui.notify("Cannot determine agent instance from .lybra/role", "error");
          return;
        }

        // 3. 从 .lybra/connection.json 读取 workspace_root 和 lybra_bin（token 不读、不显）
        const connectionConfigPath = path.join(workstationRoot, ".lybra", "connection.json");
        if (!fs.existsSync(connectionConfigPath)) {
          ctx.ui.notify(".lybra/connection.json not found. Run 'lybra enroll' first.", "error");
          return;
        }

        const connectionConfig = JSON.parse(fs.readFileSync(connectionConfigPath, "utf-8"));
        const workspaceRoot = connectionConfig.workspace_root;
        const lybraBin = connectionConfig.lybra_bin || "lybra";

        if (!workspaceRoot) {
          ctx.ui.notify("workspace_root not found in .lybra/connection.json", "error");
          return;
        }

        // 4. 查询本实例已认领的卡（lybra my-tasks --actor; 工作树/落点由产品推导并随输出给出）
        let myTasksOutput: string;
        try {
          const result = await execFileAsync(lybraBin, ["my-tasks", "--actor", String(agentInstance), "--json"], {
            cwd: workspaceRoot,
            maxBuffer: 50 * 1024 * 1024,
          });
          myTasksOutput = result.stdout;
        } catch (error: any) {
          ctx.ui.notify(`Failed to query tasks: ${error.message}`, "error");
          return;
        }

        let myTasksData: unknown;
        try {
          myTasksData = JSON.parse(myTasksOutput);
        } catch (error) {
          ctx.ui.notify(`Failed to parse my-tasks output: ${error}`, "error");
          return;
        }

        // 5. 只读产品字段决定开工与否（零拼接、零门动词）
        const plan = planGo(myTasksData);
        if (plan.kind === "none") {
          ctx.ui.notify(plan.message, "info");
          return;
        }
        if (plan.kind === "refused") {
          ctx.ui.notify(plan.message, "error");
          return;
        }

        await ctx.newSession({
          withSession: async (freshCtx) => {
            await freshCtx.sendUserMessage(plan.kickoff);
          }
        });

      } catch (error: any) {
        ctx.ui.notify(`/go command failed: ${error.message}`, "error");
      }
    },
  });
}
