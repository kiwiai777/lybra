/**
 * AIPOS-F73B件③: 工位无参一词命令 /go
 * 
 * 功能: 启动只问产品「我这个实例名下已认领的卡是哪张」，取工作树与报告落点，
 *       向模型发开工提示（只含工作树、报告落点、卡路径）。
 * 
 * 源码母本住产品仓 agents/harness/pi/_shared/extensions/，由 lybra sync 分发到工位。
 * 
 * 租约/心跳/调度一概不做。单次查询，无循环，无常驻。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.registerCommand("go", {
    description: "查询本实例已认领的任务卡，切换到工作树并发送开工提示",
    handler: async (args, ctx) => {
      try {
        // 1. 读取工位配置
        const fs = await import("node:fs");
        const path = await import("node:path");
        const { exec } = await import("node:child_process");
        const { promisify } = await import("node:util");
        const execAsync = promisify(exec);

        // 2. 从 .lybra/role 读取 instance
        const home = process.env.HOME || "";
        const workstationRoot = process.cwd();
        
        const roleConfigPath = path.join(workstationRoot, ".lybra", "role");
        if (!fs.existsSync(roleConfigPath)) {
          ctx.ui.notify(".lybra/role not found. Run 'lybra enroll' first.", "error");
          return;
        }

        const roleConfigText = fs.readFileSync(roleConfigPath, "utf-8");
        const roleConfig = JSON.parse(roleConfigText);
        const agentInstance = roleConfig.instance;
        
        if (!agentInstance) {
          ctx.ui.notify("Cannot determine agent instance from .lybra/role", "error");
          return;
        }

        // 3. 从 .lybra/connection.json 读取 workspace_root 和 lybra_bin
        const connectionConfigPath = path.join(workstationRoot, ".lybra", "connection.json");
        if (!fs.existsSync(connectionConfigPath)) {
          ctx.ui.notify(".lybra/connection.json not found. Run 'lybra enroll' first.", "error");
          return;
        }

        const connectionConfigText = fs.readFileSync(connectionConfigPath, "utf-8");
        const connectionConfig = JSON.parse(connectionConfigText);
        const workspaceRoot = connectionConfig.workspace_root;
        const lybraBin = connectionConfig.lybra_bin || "lybra";

        if (!workspaceRoot) {
          ctx.ui.notify("workspace_root not found in .lybra/connection.json", "error");
          return;
        }

        // 4. 查询本实例已认领的卡（使用 lybra my-tasks --actor）
        const myTasksCommand = `${lybraBin} my-tasks --actor ${agentInstance} --json`;
        let myTasksOutput: string;
        try {
          const result = await execAsync(myTasksCommand, { cwd: workspaceRoot, maxBuffer: 50 * 1024 * 1024 });
          myTasksOutput = result.stdout;
        } catch (error: any) {
          ctx.ui.notify(`Failed to query tasks: ${error.message}`, "error");
          return;
        }

        // 解析 my-tasks 输出（返回对象含 tasks 数组）
        let myTasksData: any;
        try {
          myTasksData = JSON.parse(myTasksOutput);
        } catch (error) {
          ctx.ui.notify(`Failed to parse my-tasks output: ${error}`, "error");
          return;
        }

        // 从 tasks 数组过滤 claimed 状态的卡
        const tasks = myTasksData.tasks || [];
        const claimedTasks = tasks.filter((t: any) => t.queue_state === "claimed");
        
        if (claimedTasks.length === 0) {
          ctx.ui.notify(`无已认领卡，等待任务分配...`, "info");
          return;
        }

        // 取第一张（如果有多张，优先级逻辑由产品定）
        const task = claimedTasks[0];
        const taskId = task.task_id;
        const taskPath = path.join(workspaceRoot, task.path);

        // 5. 推导 worktree 路径（按 card/<ID> 模式）
        const worktreePath = path.join(workspaceRoot, "card", taskId);
        if (!fs.existsSync(worktreePath)) {
          ctx.ui.notify(
            `Worktree not found at ${worktreePath}. Run "lybra next --run" to claim and create worktree.`,
            "error"
          );
          return;
        }

        // 6. 推导报告落点（治理仓 task_cards/<ID>/）
        const reportDir = path.join(workspaceRoot, "task_cards", taskId);
        const reportPath = path.join(reportDir, "RETURN.md");

        // 7. 向模型发送开工提示（不包含任何门动词）
        const roleName = roleConfig.role || "worker";
        const kickoff = `已认领任务卡 ${taskId}。

工作树路径: ${worktreePath}
报告落点: ${reportPath}
任务卡路径: ${taskPath}

按你的 AGENTS.md 执行，完成后写 RETURN.md 到报告落点。`;

        await ctx.sendUserMessage(kickoff);

      } catch (error: any) {
        ctx.ui.notify(`/go command failed: ${error.message}`, "error");
      }
    },
  });
}
