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

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';

interface RoleConfig {
  role: string;
  instance: string;
}

interface ConnectionConfig {
  workspace_root: string;
  lybra_bin: string;
}

interface MyTasksResult {
  task_id: string;
  current_state: string;
  worktree_path?: string;
}

export function activate(context: vscode.ExtensionContext) {
  const goCommand = vscode.commands.registerCommand('lybra.go', async () => {
    try {
      // 1. 读取工位配置
      const workstationRoot = getWorkstationRoot();
      if (!workstationRoot) {
        vscode.window.showErrorMessage('Cannot determine workstation root (.lybra not found)');
        return;
      }

      // 2. 从 .lybra/role 读取 instance
      const roleConfigPath = path.join(workstationRoot, '.lybra', 'role');
      if (!fs.existsSync(roleConfigPath)) {
        vscode.window.showErrorMessage('.lybra/role not found. Run "lybra enroll" first.');
        return;
      }

      const roleConfig: RoleConfig = JSON.parse(fs.readFileSync(roleConfigPath, 'utf-8'));
      const agentInstance = roleConfig.instance;
      if (!agentInstance) {
        vscode.window.showErrorMessage('Cannot determine agent instance from .lybra/role');
        return;
      }

      // 3. 从 .lybra/connection.json 读取 workspace_root 和 lybra_bin
      const connectionConfigPath = path.join(workstationRoot, '.lybra', 'connection.json');
      if (!fs.existsSync(connectionConfigPath)) {
        vscode.window.showErrorMessage('.lybra/connection.json not found. Run "lybra enroll" first.');
        return;
      }

      const connectionConfig: ConnectionConfig = JSON.parse(fs.readFileSync(connectionConfigPath, 'utf-8'));
      const workspaceRoot = connectionConfig.workspace_root;
      const lybraBin = connectionConfig.lybra_bin;

      if (!workspaceRoot || !lybraBin) {
        vscode.window.showErrorMessage('workspace_root or lybra_bin not found in .lybra/connection.json');
        return;
      }

      if (!fs.existsSync(lybraBin)) {
        vscode.window.showErrorMessage(`lybra_bin not found or not executable: ${lybraBin}`);
        return;
      }

      // 4. 查询本实例已认领的卡（使用 lybra my-tasks）
      const myTasksCommand = `${lybraBin} my-tasks --instance ${agentInstance} --json`;
      let myTasksOutput: string;
      try {
        myTasksOutput = execSync(myTasksCommand, { encoding: 'utf-8', cwd: workspaceRoot });
      } catch (error: any) {
        vscode.window.showErrorMessage(`Failed to query tasks: ${error.message}`);
        return;
      }

      // 解析 my-tasks 输出（假设返回 JSON 数组）
      let myTasks: MyTasksResult[];
      try {
        myTasks = JSON.parse(myTasksOutput);
      } catch (error) {
        vscode.window.showErrorMessage(`Failed to parse my-tasks output: ${error}`);
        return;
      }

      // 过滤 claimed 状态的卡
      const claimedTasks = myTasks.filter(t => t.current_state === 'claimed');
      if (claimedTasks.length === 0) {
        vscode.window.showInformationMessage(`No claimed task for instance ${agentInstance}. Waiting for task assignment...`);
        return;
      }

      // 取第一张（如果有多张，优先级逻辑由产品定）
      const task = claimedTasks[0];
      const taskId = task.task_id;

      // 5. 推导 worktree 路径（按 distribution.schema 声明的 card/<ID> 模式）
      const worktreePath = path.join(workspaceRoot, 'card', taskId);
      if (!fs.existsSync(worktreePath)) {
        vscode.window.showErrorMessage(`Worktree not found at ${worktreePath}. Run "lybra next --run" to claim and create worktree.`);
        return;
      }

      // 6. 推导报告落点（治理仓 task_cards/<ID>/）
      const reportDir = path.join(workspaceRoot, 'task_cards', taskId);
      if (!fs.existsSync(reportDir)) {
        fs.mkdirSync(reportDir, { recursive: true });
      }

      // 7. 切换工作目录到 worktree（VSCode workspace）
      const worktreeUri = vscode.Uri.file(worktreePath);
      await vscode.commands.executeCommand('vscode.openFolder', worktreeUri, false);

      // 8. 向模型发开工提示（通过 Chat API 或显示信息）
      const roleName = roleConfig.role || 'worker';
      const message = `
=== ${roleName.charAt(0).toUpperCase() + roleName.slice(1)} workstation ready ===
Task ID: ${taskId}
Worktree: ${worktreePath}
Report directory: ${reportDir}

Ready to work. Write your RETURN.md to ${reportDir}/RETURN.md when done.
==================================
      `.trim();

      vscode.window.showInformationMessage(message);

      // 可选：自动打开任务卡文件
      const taskCardPath = path.join(workspaceRoot, '5_tasks', 'queue', 'claimed', `${taskId.toLowerCase()}.md`);
      if (fs.existsSync(taskCardPath)) {
        const taskCardUri = vscode.Uri.file(taskCardPath);
        await vscode.workspace.openTextDocument(taskCardUri);
        await vscode.window.showTextDocument(taskCardUri);
      }

    } catch (error: any) {
      vscode.window.showErrorMessage(`/go command failed: ${error.message}`);
    }
  });

  context.subscriptions.push(goCommand);
}

function getWorkstationRoot(): string | null {
  // 从当前工作目录向上查找 .lybra 目录
  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (!workspaceFolders || workspaceFolders.length === 0) {
    return null;
  }

  let currentDir = workspaceFolders[0].uri.fsPath;
  while (currentDir !== path.dirname(currentDir)) {
    const lybraDir = path.join(currentDir, '.lybra');
    if (fs.existsSync(lybraDir)) {
      return currentDir;
    }
    currentDir = path.dirname(currentDir);
  }

  return null;
}

export function deactivate() {}
