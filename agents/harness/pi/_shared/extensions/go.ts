/**
 * AIPOS-F73B件③: 工位无参一词命令 /go
 *
 * 功能: 启动只问产品「我这个实例名下已认领的卡是哪张」，取工作树与报告落点，
 *       向模型发开工提示（只含工作树、报告落点、卡路径）。
 *
 * AIPOS-F86 件①: 工作树 / 报告落点 / 卡路径一律只读 `lybra my-tasks --json` 输出的
 *   worktree_path / report_path / card_path（产品侧唯一推导: next_resolver.card_workstation_view,
 *   与认领建树同一函数）；本扩展不做任何路径拼接。
 * AIPOS-F87 件③: 开工哪张卡也由产品给出（my-tasks 的 next_card）; 无可开工卡时原样转述产品给的
 *   next_card_excluded 原因列表, 文案零门动词（认领由驱动方完成）。
 * AIPOS-F90 件③: 开工只走 /go。`/go <卡号|卡路径>`(被贴卡号冷启动时)把指向原样交给产品核验
 *   (`lybra my-tasks --task-id <指向>`): 非 claimed / 非本实例 / 已结案 / 产物已交 → 产品给拒因, 本扩展原样转述并拒开工;
 *   核验判据唯一在产品 next_resolver.kickoff_refusal, 本扩展不判。
 *
 * AIPOS-F93 件①: 报告必填 frontmatter 只读 next_card.report_required_frontmatter(产品按声明 transitions artifact_ingest
 *   单源渲染, 审计卡附被审 tip/tree 实值)原样列进开工提示; 本扩展不自写字段清单, 字段缺/形变 = 拒开工(fail-closed)。
 * AIPOS-F89 件③b(Owner 2026-10-03 裁定 A2): 本扩展不再读 `.lybra/role`, 也不读 connection.json 的 workspace_root —— 工位身份
 *   (实例 / 治理根)由产品经 `lybra my-tasks --workstation <工位目录>` 解析(charter_render.workstation_identity 唯一实现);
 *   本扩展只读 connection.json 的 `lybra_bin` 一个字段, 读不到即报错(无缺省 / 不退回 PATH)。
 *
 * 源码母本住产品仓 agents/harness/pi/_shared/extensions/，由 lybra sync 分发到工位。
 *
 * 租约/心跳/调度一概不做。单次查询，无循环，无常驻。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export type GoPlan =
  | { kind: "none"; message: string }
  | { kind: "refused"; taskId: string; message: string }
  | { kind: "kickoff"; taskId: string; worktreePath: string; reportPath: string; cardPath: string; kickoff: string };

/**
 * AIPOS-F93 件①: 产品给出的报告必填字段逐项原样成行(无 I/O)。每项须为 {key: 非空串, hint: 串, value: 串|null};
 * 列表空/任一项形变 = null(调用方拒开工)。
 */
export function reportFieldLines(contract: unknown): string[] | null {
  if (!Array.isArray(contract) || contract.length === 0) {
    return null;
  }
  const lines: string[] = [];
  for (const item of contract) {
    if (!item || typeof item !== "object") {
      return null;
    }
    const r = item as { key?: unknown; hint?: unknown; value?: unknown };
    if (typeof r.key !== "string" || !r.key || typeof r.hint !== "string") {
      return null;
    }
    if (r.value !== null && r.value !== undefined && typeof r.value !== "string") {
      return null;
    }
    lines.push(typeof r.value === "string" && r.value ? `- ${r.key}: ${r.value}(${r.hint})` : `- ${r.key}: (${r.hint})`);
  }
  return lines;
}

function excludedText(item: unknown): string {
  if (item && typeof item === "object") {
    const r = item as { task_id?: unknown; code?: unknown; reason?: unknown };
    const taskId = typeof r.task_id === "string" ? r.task_id : "(未知卡)";
    const code = typeof r.code === "string" ? r.code : "UNKNOWN";
    const reason = typeof r.reason === "string" ? r.reason : "";
    return reason ? `- ${taskId} ${code}: ${reason}` : `- ${taskId} ${code}`;
  }
  return "- 产品未给出拒因字段";
}

/**
 * 纯函数: 由 `lybra my-tasks --json` 的输出决定 /go 的结果（无 I/O, 夹具可直接调用）。
 *
 * AIPOS-F87 件③: 开工哪张卡由产品给出（my-tasks 的 next_card, 判据唯一声明在产品 next_resolver.NEXT_CARD_RULE）;
 * 本函数不挑卡、不排序、不在本地补推。next_card 为空时把产品给的 next_card_excluded 原样提示。
 * 字段缺失 = 拒（fail-closed）。
 */
/**
 * AIPOS-F90 件③ + F89 件③b: /go 的 my-tasks 参数(纯函数, 夹具可直接调用): 只传工位目录, 身份与治理根由产品解析;
 * 有指向(卡号/卡路径)则交产品核验这一张。
 */
export function myTasksArgv(workstationRoot: string, rawArgs: unknown): string[] {
  const argv = ["my-tasks", "--workstation", String(workstationRoot), "--json"];
  const ref = typeof rawArgs === "string" ? rawArgs.trim() : "";
  if (ref) {
    argv.push("--task-id", ref);
  }
  return argv;
}

/**
 * AIPOS-F89 件③b: 读 connection.json 的 lybra_bin(唯一读取的工位字段)。文件缺 / 非 JSON / 字段缺或空 = 拒(无 "lybra" 缺省)。
 * fs/path 由调用方注入(夹具可直接调用)。token 等其余字段一概不取。
 */
export function readLybraBin(
  fs: { existsSync(p: string): boolean; readFileSync(p: string, enc: "utf-8"): string },
  connectionPath: string,
): { ok: true; bin: string } | { ok: false; message: string } {
  if (!fs.existsSync(connectionPath)) {
    return { ok: false, message: `${connectionPath} 不存在: 工位未 enroll, 无法定位产品 CLI; 按 block-and-report 上报` };
  }
  let data: unknown;
  try {
    data = JSON.parse(fs.readFileSync(connectionPath, "utf-8"));
  } catch (error) {
    return { ok: false, message: `${connectionPath} 不是合法 JSON(${error}); 按 block-and-report 上报` };
  }
  const bin = data && typeof data === "object" ? (data as Record<string, unknown>).lybra_bin : undefined;
  if (typeof bin !== "string" || !bin.trim()) {
    return { ok: false, message: `${connectionPath} 缺 lybra_bin(enroll 写入的产品 CLI 部署位), 无法调用产品; 按 block-and-report 上报` };
  }
  return { ok: true, bin: bin.trim() };
}

export function planGo(myTasksData: unknown): GoPlan {
  const data = (myTasksData && typeof myTasksData === "object") ? (myTasksData as Record<string, unknown>) : null;
  if (data === null || !Array.isArray(data.tasks)) {
    return { kind: "none", message: "my-tasks 输出缺 tasks 数组(产品输出形变), 无法开工; 按 block-and-report 上报" };
  }
  if (!("next_card" in data) || !Array.isArray(data.next_card_excluded)) {
    return {
      kind: "refused",
      taskId: "",
      message: "my-tasks 输出缺 next_card / next_card_excluded(产品输出形变或 CLI 未部署到位), 无法开工; 按 block-and-report 上报",
    };
  }
  const card = data.next_card as Record<string, unknown> | null;
  if (card === null) {
    const excluded = data.next_card_excluded as unknown[];
    if (excluded.length === 0) {
      return { kind: "none", message: "无已认领卡，等待任务分配..." };
    }
    return {
      kind: "refused",
      taskId: "",
      message: `无可开工卡(产品选卡结论), 各卡不入选原因:\n${excluded.map(excludedText).join("\n")}`,
    };
  }
  const taskId = typeof card.task_id === "string" ? card.task_id : "";
  const worktreePath = card.worktree_path;
  const reportPath = card.report_path;
  const cardPath = card.card_path;
  if (!taskId || typeof worktreePath !== "string" || !worktreePath || typeof reportPath !== "string" || !reportPath
    || typeof cardPath !== "string" || !cardPath) {
    return {
      kind: "refused",
      taskId,
      message: `${taskId || "(未知卡)"} next_card 字段不全(task_id/worktree_path/report_path/card_path), 无法开工; 按 block-and-report 上报`,
    };
  }
  const fieldLines = reportFieldLines(card.report_required_frontmatter);
  if (fieldLines === null) {
    return {
      kind: "refused",
      taskId,
      message: `${taskId} next_card 缺报告必填字段(report_required_frontmatter, 产品输出形变或 CLI 未部署到位), 无法开工; 按 block-and-report 上报`,
    };
  }

  const kickoff = `已认领任务卡 ${taskId}。

工作树路径: ${worktreePath}
报告落点: ${reportPath}
任务卡路径: ${cardPath}
报告 frontmatter 必填(产品给出; 缺任一项或仍为占位, 报告被拒收):
${fieldLines.join("\n")}

按你的 AGENTS.md 执行，完成后写报告到报告落点。`;
  return { kind: "kickoff", taskId, worktreePath, reportPath, cardPath, kickoff };
}

export default function (pi: ExtensionAPI) {
  pi.registerCommand("go", {
    description: "查询本实例已认领的任务卡，并以产品给出的工作树/报告落点发送开工提示",
    handler: async (args, ctx) => {
      try {
        // 1. 工位目录 = 会话 cwd(身份文件由产品读, 本扩展不读 .lybra/role)
        const fs = await import("node:fs");
        const path = await import("node:path");
        const { execFile } = await import("node:child_process");
        const { promisify } = await import("node:util");
        const execFileAsync = promisify(execFile);
        const workstationRoot = process.cwd();

        // 2. 只读 connection.json 的 lybra_bin 一个字段(token 不读、不显); 读不到即报错, 无缺省
        const lybraBin = readLybraBin(fs, path.join(workstationRoot, ".lybra", "connection.json"));
        if (!lybraBin.ok) {
          ctx.ui.notify(lybraBin.message, "error");
          return;
        }

        // 3. 问产品: 本工位(实例/治理根由产品解析)该开工哪张卡(--task-id <指向> 时只核验这一张)
        let myTasksOutput: string;
        try {
          const result = await execFileAsync(lybraBin.bin, myTasksArgv(workstationRoot, args), {
            cwd: workstationRoot,
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

        // 4. 只读产品字段决定开工与否（零拼接、零门动词）
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
