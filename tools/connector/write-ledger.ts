/**
 * write-ledger —— 写操作飞行记录(harness 无关)。
 *
 * 每次写类工具调用(write/edit/bash 写命令)被检查点观测到,就追加一行 JSONL。
 * 既记放行也记阻断,供审计回溯"这个 session 都干了什么写操作"。呼应卡的
 * write-op flight recorder 方向。无 pi 依赖:纯 fs append。
 */

import { appendFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";

export type WriteDecision = "allow" | "block";

export interface WriteOpEntry {
  ts: string; // ISO 时间
  tool: string; // write | edit | bash
  decision: WriteDecision;
  taskId?: string; // 绑定的 task(放行时);block 时可能为空
  piSession?: string; // pi session 文件名(身份)
  target?: string; // 写目标(文件路径 / bash 命令摘要,截断)
  reason?: string; // block 原因 / allow 依据
}

/** 追加一条写账。logPath 不存在会自动建父目录。失败静默(账本不能阻断主流程)。 */
export function appendWriteOp(logPath: string, entry: WriteOpEntry): void {
  try {
    mkdirSync(dirname(logPath), { recursive: true });
    appendFileSync(logPath, JSON.stringify(entry) + "\n", "utf-8");
  } catch {
    /* 账本写入失败不影响检查点主流程;生产化时换稳健 sink */
  }
}

/** 读取并解析写账(测试用)。返回全部条目。 */
export function readWriteOps(logPath: string): WriteOpEntry[] {
  let text: string;
  try {
    text = readFileSync(logPath, "utf-8");
  } catch {
    return [];
  }
  const out: WriteOpEntry[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      out.push(JSON.parse(t));
    } catch {
      /* 跳过损坏行 */
    }
  }
  return out;
}
