/**
 * lybra-loop —— pi 扩展本体:让 lybra-executor 自动【发起】领卡循环。
 *
 * 命令(命名对齐 _shared/LYBRA-NAMING.md + 产品 skills/lybra-executor/SKILL.md):
 *   /lybra on [maxN]   启动循环(默认 max 1 张,防失控)
 *   /lybra off         停止循环
 *   /lybra status      查看状态
 *   /lybra sync        拉新分发(AIPOS-F20: 薄壳投影既有 lybra sync CLI, 成功后 /reload 生效)
 *   /lybra enroll <码>  工位一贴上岗(AIPOS-F23: 自包含码→交换→落盘工位 .lybra/→land→连通验证,
 *                        接着 /lybra sync 然后 /reload; 裸机等价: lybra roles enroll --code <码>)
 *   /lybra-tick        (手动)立即执行一轮 tick;自动链不依赖命令路由
 *
 * 它只做"发起",不做"授权"(红线):claim 放行与否永远由 gate 判定(AIPOS-250 信封)——
 *   • 信封内(PreAuthorized)→ gate 一阶段落盘 claim → 扩展冷启动执行
 *   • 信封外(Supervised,owner_confirmation_required=true)→ 跳过记录,绝不自动 confirm
 *   • BLOCK/失败 → 循环立停
 *
 * 机制要点(详见 DESIGN.md):
 *   • 连接器面:node http 直连 gate /mcp(gate-client.ts),不 shell 调 Python CLI。
 *   • gate 被动:轮询宿主全在 agent 侧(非阻塞 followUp 链),gate 不推送/不唤醒。
 *   • 一卡一会话:claim 放行 → liveCtx.newSession 冷启动(同 _shared/extensions/claim.ts)。
 *   • 跨 session 状态:模块级 loopState(ESM 缓存跨 session 替换存活)+ agent_settled 续跑。
 *   • F-EXT001-4(FIX1):tick 机制 — **直接函数调用**,不经 sendUserMessage/命令路由
 *     (pi 的 sendUserMessage 永不触发命令,只落文本给模型;定时器/生命周期钩子直接调 doTick,
 *     零 LLM 参与、零上下文污染;/lybra-tick 命令仅作手动触发入口)。
 *   • AIPOS-F10(ctx 生命周期修真):全部 ctx 消费点统一到 claim.ts 范式 ——
 *     模块级 liveCtx/livePi(每次 session 装载/事件/命令入口刷新),定时器/钩子绝不闭包
 *     捕获 ctx(那些在 session 替换后变 stale 对象)。三病象根治:
 *     ①冷启动 newSession 摔 → 定时器用 liveCtx(由 session_start 刷新)
 *     ②/reload 后 stale → reload 清定时器,session_start 用新 ctx 重调度
 *     ③复工 ctx.reply 摔 → 改用 sendUserMessage(对齐 claim.ts);session_start 取第二参数 ctx
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadConfig, loadConfigSchema, ConfigError, GateMcpClient, loadVerbCatalog, validateRequiredVerbs, type LoopConfig, type GateTerritoryDeclaration, type ConfigSchemaShape } from "./gate-client.ts";
import { ConnectionResolver } from "./loop-context.ts";
import { buildKickoff, stringifyReasons, severityToLevel, planCooldownStep } from "./loop-decisions.ts";
import { executeTick, freshState, Logger, type LoopState } from "./loop-engine.ts";
// AIPOS-C4B 大项B: 版本信号 — 本地版本戳读取 + 清单比对
import { readFileSync, existsSync, appendFileSync, writeFileSync, mkdirSync, statfsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// AIPOS-F15B: pi-tui 组件懒加载 — 扩展被 pi 装载时可解析(经 pi 模块链),
// headless 测试环境解析失败返回空对象(调用方 try/catch 兜底)。
// 用 createRequire 而非顶层 import: 顶层值导入会让无 pi-tui 的测试环境直接崩。
let _piTui: typeof import("@earendil-works/pi-tui") | null | undefined;
function piTui(): typeof import("@earendil-works/pi-tui") {
  if (_piTui === undefined) {
    try {
      _piTui = createRequire(import.meta.url)("@earendil-works/pi-tui") as typeof import("@earendil-works/pi-tui");
    } catch {
      _piTui = null;
    }
  }
  if (!_piTui) throw new Error("pi-tui not resolvable (headless env)");
  return _piTui;
}

// 模块级:跨 session 替换存活(ESM 模块缓存,同进程唯一)。对齐 claim.ts 的 pendingModel 原理。
let loopState: LoopState = freshState();
let currentGateUrl = "";
let currentRole = "";
let currentActor = "";
let currentTokenFp = "(none)";
let currentClient: GateMcpClient | null = null;
let currentLogger: Logger | null = null;
let pendingTimer: ReturnType<typeof setTimeout> | null = null;
// release 触发的 newSession 是循环自驱的,session_shutdown 不应据此停循环(区别于用户 /new)。
let expectingSwap = false;
// AIPOS-CONN-LOOP-2 ①: 跟踪当前执行的任务ID和worktree路径，用于自动return
let currentTaskId: string | null = null;
let currentWorktreePath: string | null = null;
// AIPOS-F16: 余热转入声门门 — 额度尽转余热的出声只发一次(转入时刻), 后续余热 tick 不重复刷屏。
// 模式本身不入状态机: 余热 ≡ loopState.on && released>=maxN(现算现用)。
let cooldownAnnounced = false;

// ---------------------------------------------------------------------------
// AIPOS-F19: 工位水位自检 — 磁盘/tmpfs 越阈出声带路(只喊不代删, 决策留人)
// ---------------------------------------------------------------------------
// 声明单源 = config.schema#watermark(阈值/路径/周期/话术素材全在 schema, 禁写死)。
// 三处检查: ①loop-on 启动自检 ②status 面板常驻"水位"行(各路径 used%/free)
//           ③周期 tick 低频复查(每 N tick, N=schema#watermark.periodic_tick_interval)。
// 出声走 voice() 单出口(F15), persistent=true(F15B 双写 journal);
// 降噪: 同路径同级别不重复喊(状态变化才再喊), 状态模块级跨 session 存活。
// 本卡明确不做: 自动清理任何文件(只喊不动手)、跨项目文件操作、自动停循环。
// ---------------------------------------------------------------------------

export type WatermarkLevel = "ok" | "warn" | "critical";
export type WatermarkSource = "startup" | "tick" | "status";

export interface WatermarkReading {
  path: string;
  label: string;
  usedPercent: number; // df 口径: (blocks-bavail)/blocks*100
  freeBytes: number;
  totalBytes: number;
  level: WatermarkLevel;
  error?: string; // statfs 失败时置位(level 判 ok 不出声)
}

export interface WatermarkCheckConfig {
  warnPercent: number;
  criticalPercent: number;
  checkPaths: Array<{ path: string; label: string }>;
  periodicTickInterval: number;
  clearableItems: string;
  nextStepTemplate: string;
  criticalExtraLine: string;
}

/** 模块级: 水位降噪状态(同路径同级别不重复喊)+ tick 计数(周期复查)。跨 session 存活。 */
const watermarkLastLevels = new Map<string, WatermarkLevel>();
let watermarkTickCounter = 0;

/**
 * 读 config.schema#watermark → 检查配置。
 * 声明缺失/不完整/非法 → null(检查跳过 + warn 日志, 禁写死兜底 —— 阈值跟随声明,
 * 改声明即改行为; 声明坏了宁可不出声也不拿写死值误报)。
 */
export function parseWatermarkConfig(schema: ConfigSchemaShape | null | undefined): WatermarkCheckConfig | null {
  const w = schema?.watermark;
  if (!w || typeof w !== "object") return null;
  const warnPercent = Number(w.thresholds?.warn_percent);
  const criticalPercent = Number(w.thresholds?.critical_percent);
  if (!Number.isFinite(warnPercent) || !Number.isFinite(criticalPercent)) return null;
  if (warnPercent < 0 || warnPercent > 100 || criticalPercent < 0 || criticalPercent > 100) return null;
  if (criticalPercent < warnPercent) return null;
  const checkPaths = Array.isArray(w.check_paths)
    ? w.check_paths
        .filter((p) => p && typeof (p as { path?: unknown }).path === "string" && (p as { path: string }).path)
        .map((p) => ({ path: String((p as { path: string }).path), label: String((p as { label?: unknown }).label || (p as { path: string }).path) }))
    : [];
  if (checkPaths.length === 0) return null;
  const intervalRaw = Number(w.periodic_tick_interval);
  if (!Number.isFinite(intervalRaw) || intervalRaw < 1) return null;
  const clearableItems = typeof w.clearable_items === "string" ? w.clearable_items.trim() : "";
  const nextStepTemplate = typeof w.next_step_template === "string" ? w.next_step_template.trim() : "";
  const criticalExtraLine = typeof w.critical_extra_line === "string" ? w.critical_extra_line.trim() : "";
  if (!clearableItems || !nextStepTemplate || !criticalExtraLine) return null;
  return {
    warnPercent,
    criticalPercent,
    checkPaths,
    periodicTickInterval: Math.floor(intervalRaw),
    clearableItems,
    nextStepTemplate,
    criticalExtraLine,
  };
}

/** {workspace_root} 占位符解析(其余路径按字面)。声明: config.schema#watermark.check_paths_note。 */
export function resolveWatermarkPaths(
  checkPaths: Array<{ path: string; label: string }>,
  workspaceRoot: string,
): Array<{ path: string; label: string }> {
  return checkPaths.map(({ path, label }) => ({
    path: path.includes("{workspace_root}") ? path.split("{workspace_root}").join(workspaceRoot) : path,
    label,
  }));
}

/** 纯: usage% → 级别(越 critical 优先于 warn)。 */
export function computeWatermarkLevel(usedPercent: number, warnPercent: number, criticalPercent: number): WatermarkLevel {
  if (usedPercent >= criticalPercent) return "critical";
  if (usedPercent >= warnPercent) return "warn";
  return "ok";
}

/** statfs 读盘(df 口径, statfsFn 可注入便于单测)。失败 → error 置位。 */
export function readDiskUsage(
  statfsFn: (p: string) => { bsize: number; blocks: number; bavail: number },
  path: string,
): { usedPercent: number; freeBytes: number; totalBytes: number; error?: undefined } | { error: string } {
  try {
    const st = statfsFn(path);
    if (!st || !Number.isFinite(st.bsize) || !Number.isFinite(st.blocks) || st.blocks <= 0) {
      return { error: `statfs 返回非法(${path})` };
    }
    const totalBytes = st.blocks * st.bsize;
    const freeBytes = (st.bavail ?? 0) * st.bsize;
    const usedPercent = ((st.blocks - (st.bavail ?? 0)) / st.blocks) * 100;
    return { usedPercent, freeBytes, totalBytes };
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
}

function fmtWatermarkGB(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)}GB`;
}

/** status 面板水位行: 各路径 used%/free + 级别标记(常驻, 不受降噪限制 — 查询非告警)。 */
export function buildWatermarkStatusLine(readings: WatermarkReading[]): string {
  if (readings.length === 0) return "(无读数)";
  return readings
    .map((r) => {
      if (r.error) return `${r.label}(${r.path}) 读取失败`;
      const mark = r.level === "critical" ? "🔴" : r.level === "warn" ? "⚠" : "";
      return `${r.label}(${r.path}) ${r.usedPercent.toFixed(1)}%${mark ? ` ${mark}${r.level === "critical" ? "危急" : "警告"}` : ""} 剩 ${fmtWatermarkGB(r.freeBytes)}`;
    })
    .join(" | ");
}

/** 越阈出声文本(warn/critical)。素材全来自 schema 声明(next_step/可清项/危急句)。 */
export function buildWatermarkVoiceMessage(r: WatermarkReading, cfg: WatermarkCheckConfig): string {
  const nextStep = cfg.nextStepTemplate.split("{clearable_items}").join(cfg.clearableItems);
  const head = `[水位${r.level === "critical" ? "危急" : "警告"}] ${r.path} 已用 ${r.usedPercent.toFixed(1)}% (剩 ${fmtWatermarkGB(r.freeBytes)})`;
  if (r.level === "critical") {
    return `${head} — ${cfg.criticalExtraLine} | next_step: ${nextStep}`;
  }
  return `${head} — next_step: ${nextStep}`;
}

/** 降噪判定: 越阈且级别变化才出声(同路径同级别不重复喊; 恢复 ok 不出声 — 正常水位零噪音)。 */
export function watermarkShouldVoice(path: string, level: WatermarkLevel, last: Map<string, WatermarkLevel>): boolean {
  if (level === "ok") return false;
  return last.get(path) !== level;
}

/**
 * 跑一次水位检查: statfs 各声明路径 → 级别 → 降噪出声(voice persistent)+ 日志。返回 readings。
 * 三处检查点统一入口: startup / tick / status(source 仅入日志与降噪语义相同)。
 * 只读不动手: 不清理任何文件、不停循环(决策留人)。
 */
function runWatermarkCheck(workspaceRoot: string, source: WatermarkSource): { readings: WatermarkReading[]; declared: boolean } {
  let schema: ConfigSchemaShape;
  try {
    schema = loadConfigSchema();
  } catch (e) {
    currentLogger?.warn("watermark-schema-load-failed", { source, error: e instanceof Error ? e.message : String(e) });
    return { readings: [], declared: false };
  }
  const cfg = parseWatermarkConfig(schema);
  if (!cfg) {
    currentLogger?.warn("watermark-not-declared", { source, note: "config.schema 缺/坏 watermark 声明, 水位检查跳过(禁写死兜底)" });
    return { readings: [], declared: false };
  }
  const readings: WatermarkReading[] = [];
  for (const { path, label } of resolveWatermarkPaths(cfg.checkPaths, workspaceRoot)) {
    const usage = readDiskUsage(statfsSync, path);
    if ("error" in usage && usage.error) {
      readings.push({ path, label, usedPercent: 0, freeBytes: 0, totalBytes: 0, level: "ok", error: usage.error });
      currentLogger?.warn("watermark-statfs-failed", { source, path, error: usage.error });
      continue;
    }
    const u = usage as { usedPercent: number; freeBytes: number; totalBytes: number };
    const level = computeWatermarkLevel(u.usedPercent, cfg.warnPercent, cfg.criticalPercent);
    const reading: WatermarkReading = { path, label, usedPercent: u.usedPercent, freeBytes: u.freeBytes, totalBytes: u.totalBytes, level };
    readings.push(reading);
    const prev = watermarkLastLevels.get(path) ?? "(none)";
    if (watermarkShouldVoice(path, level, watermarkLastLevels)) {
      currentLogger?.info("watermark-alert", {
        source,
        path,
        used_percent: Number(u.usedPercent.toFixed(1)),
        free_gb: Number((u.freeBytes / 1024 ** 3).toFixed(1)),
        level,
        prev,
      });
      // AIPOS-F19: 越阈出声走 voice() 单出口(F15), persistent=true(F15B 双写 journal)
      voice(buildWatermarkVoiceMessage(reading, cfg), level === "critical" ? "error" : "warn", true);
    } else if (level !== "ok") {
      currentLogger?.info("watermark-alert-suppressed", { source, path, level, prev, reason: "同路径同级别不重复喊" });
    }
    if (prev !== level) {
      currentLogger?.info("watermark-level-change", { source, path, from: prev, to: level });
    }
    watermarkLastLevels.set(path, level);
  }
  return { readings, declared: true };
}

/** 周期复查门: tick 计数对 schema 声明的 interval 取模(schema 读失败 → 本轮不查, 下轮再试)。 */
function isWatermarkTickDue(): boolean {
  try {
    const cfg = parseWatermarkConfig(loadConfigSchema());
    return !!cfg && watermarkTickCounter % cfg.periodicTickInterval === 0;
  } catch {
    return false;
  }
}

// AIPOS-F10: 活 ctx/pi 引用 — 每次 session 装载/命令入口/事件回调时刷新。
// 定时器/钩子绝不闭包捕获 ctx(那些在 session 替换后变 stale 对象,调用即抛)。
// 对齐 claim.ts 范式:只用 withSession 回调的 freshCtx 或 session_start 的新 ctx。
let livePi: ExtensionAPI | null = null;
let liveCtx: any = null;

// AIPOS-F13 大项B: 出声缓冲 — liveCtx 未就绪时话术入缓冲,就绪后补发,防丢话。
interface VoiceMessage {
  text: string;
  level: "info" | "warn" | "error";
  timestamp: number;
  persistent?: boolean; // AIPOS-F15B: 关键事件标记
}
const voiceBuffer: VoiceMessage[] = [];
const VOICE_BUFFER_MAX = 50; // 防刷屏

// AIPOS-F15B: 出声持久化 — 关键事件(收账/复工/终停/异常BLOCK)写入 voice journal,
// Owner 事后回看对话记录/journal 文件即可,不用盯住 20 秒。
// 双写通道:①pi.appendEntry(会话持久,不入 LLM 上下文)+②voice-journal.md(文件兜底)。
const VOICE_JOURNAL_MAX_ENTRIES = 200; // 文件内最多保留条数(轮转)

/**
 * 统一出声函数 — 内聚所有上屏调用点,liveCtx 未就绪时入缓冲。
 * AIPOS-F15 大项A: 每次调用必落 voice-attempt 日志(outcome+level+text_head),
 * 审计/顾问从此可从日志证明每句话的去向。
 * outcome ∈ direct(句柄好直接上屏) / buffered(句柄未就绪入缓冲) /
 *           no-handle(句柄作废丢弃,WARN) / dropped(缓冲超限丢弃)。
 * 禁另造第二条出声路 —— 所有上屏文本必过此函数。
 *
 * AIPOS-F15B: persistent=true 时双写 — notify(即时)+ 会话持久 entry + journal 文件。
 * 关键事件(收账/复工/终停/异常BLOCK)置 true;轮询心跳等噪音置 false(只 notify)。
 */
function voice(text: string, level: "info" | "warn" | "error" = "info", persistent: boolean = false): void {
  const textHead = text.slice(0, 40);
  if (liveCtx?.ui?.notify) {
    // liveCtx 就绪 → 即时上屏
    try {
      liveCtx.ui.notify(text, level);
      currentLogger?.info("voice-attempt", { outcome: "direct", level, text_head: textHead, persistent });
    } catch (e) {
      // notify 自身抛错 → 句柄作废(冷启动/reload 后 stale 对象)
      currentLogger?.warn("voice-attempt", {
        outcome: "no-handle",
        level,
        text_head: textHead,
        persistent,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  } else {
    // liveCtx 未就绪 → 入缓冲
    voiceBuffer.push({ text, level, timestamp: Date.now(), persistent });
    currentLogger?.info("voice-attempt", { outcome: "buffered", level, text_head: textHead, persistent });
    if (voiceBuffer.length > VOICE_BUFFER_MAX) {
      voiceBuffer.shift(); // 丢最旧
      currentLogger?.warn("voice-attempt", { outcome: "dropped", level: "warn", text_head: "(oldest)" });
    }
  }
  // AIPOS-F15B: persistent=true → 双写持久通道(notify 之上额外落盘)
  if (persistent) {
    persistVoiceEntry(text, level);
  }
}

/**
 * AIPOS-F15B: 持久化关键事件 — 双写通道:
 * ① livePi.appendEntry("lybra-voice", ...) — 会话持久,不入 LLM 上下文,配合 renderer 在对话记录可见。
 * ② voice-journal.md 文件追加 — 跨会话兜底(appendEntry 随 session 走,journal 文件独立存活)。
 * 任一通道失败不影响另一通道(日志记 warn,不抛)。
 */
function persistVoiceEntry(text: string, level: "info" | "warn" | "error"): void {
  const ts = new Date().toISOString();
  // 通道①: pi 会话持久 entry(不入 LLM 上下文)
  try {
    livePi?.appendEntry("lybra-voice", { text, level, timestamp: ts });
    currentLogger?.info("voice-persist-entry", { level, text_head: text.slice(0, 40) });
  } catch (e) {
    currentLogger?.warn("voice-persist-entry-failed", {
      level,
      text_head: text.slice(0, 40),
      error: e instanceof Error ? e.message : String(e),
    });
  }
  // 通道②: voice-journal.md 文件追加(跨会话兜底)
  try {
    const journalPath = getVoiceJournalPath();
    if (journalPath) {
      const levelTag = level === "error" ? "🔴" : level === "warn" ? "🟡" : "🟢";
      const line = `- \`${ts}\` ${levelTag} [${level}] ${text}\n`;
      if (!existsSync(journalPath)) {
        mkdirSync(dirname(journalPath), { recursive: true });
        writeFileSync(journalPath, `# Lybra Voice Journal (关键事件持久记录)\n\n> 收账/复工/终停/异常 双写于此;轮询心跳不入。\n> 最近 ${VOICE_JOURNAL_MAX_ENTRIES} 条保留。\n\n`, "utf-8");
      }
      appendFileSync(journalPath, line, "utf-8");
      // 轮转:超过上限时只保留最后 N 条
      rotateVoiceJournal(journalPath);
    }
  } catch (e) {
    currentLogger?.warn("voice-persist-journal-failed", {
      level,
      text_head: text.slice(0, 40),
      error: e instanceof Error ? e.message : String(e),
    });
  }
}

/** voice-journal.md 路径(与 loop.log 同目录)。 */
function getVoiceJournalPath(): string | null {
  try {
    const logPath = process.env.LYBRA_LOOP_LOG || LOG_PATH_DEFAULT;
    return `${dirname(logPath)}/voice-journal.md`;
  } catch {
    return null;
  }
}

/** 轮转 voice journal:超过 VOICE_JOURNAL_MAX_ENTRIES 条时只保留最后 N 条。 */
function rotateVoiceJournal(journalPath: string): void {
  try {
    if (!existsSync(journalPath)) return;
    const content = readFileSync(journalPath, "utf-8");
    const lines = content.split("\n");
    // 找 header 结束位置(第一个 `- ` 行之前)
    let headerEnd = 0;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].startsWith("- `")) { headerEnd = i; break; }
    }
    if (headerEnd === 0) return; // 无条目
    const header = lines.slice(0, headerEnd).join("\n");
    const entries = lines.slice(headerEnd).filter((l) => l.startsWith("- `"));
    if (entries.length <= VOICE_JOURNAL_MAX_ENTRIES) return;
    const kept = entries.slice(-VOICE_JOURNAL_MAX_ENTRIES);
    writeFileSync(journalPath, `${header}\n${kept.join("\n")}\n`, "utf-8");
  } catch {
    // 轮转失败不影响主流程
  }
}

/**
 * AIPOS-F15B: 读 voice journal 最近 N 条(供 /lybra status 显示)。
 */
function readVoiceJournalRecent(n: number = 10): string[] {
  try {
    const journalPath = getVoiceJournalPath();
    if (!journalPath) return [];
    if (!existsSync(journalPath)) return [];
    const content = readFileSync(journalPath, "utf-8");
    const lines = content.split("\n").filter((l) => l.startsWith("- `"));
    return lines.slice(-n);
  } catch {
    return [];
  }
}

/**
 * session_start 时刷新 liveCtx 后按序补发缓冲话术。
 * AIPOS-F15 大项A: 每条补发必落 voice-attempt(outcome=flushed/no-handle)。
 * AIPOS-F15B: 补发时 persistent 标记的话术也走双写(补发=延迟的 persistent 事件)。
 */
function flushVoiceBuffer(): void {
  if (voiceBuffer.length === 0) return;
  if (!liveCtx?.ui?.notify) {
    currentLogger?.warn("voice-attempt", {
      outcome: "no-handle",
      level: "warn",
      text_head: `(flush-skip ${voiceBuffer.length} msgs, no ctx)`,
    });
    return;
  }
  const count = voiceBuffer.length;
  currentLogger?.info("voice-flush-start", { count });
  while (voiceBuffer.length > 0) {
    const msg = voiceBuffer.shift()!;
    try {
      liveCtx.ui.notify(msg.text, msg.level);
      currentLogger?.info("voice-attempt", { outcome: "flushed", level: msg.level, text_head: msg.text.slice(0, 40), persistent: msg.persistent || false });
      // AIPOS-F15B: 补发时也走持久化(延迟的 persistent 事件)
      if (msg.persistent) {
        persistVoiceEntry(msg.text, msg.level);
      }
    } catch (e) {
      currentLogger?.warn("voice-attempt", {
        outcome: "no-handle",
        level: msg.level,
        text_head: msg.text.slice(0, 40),
        persistent: msg.persistent || false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }
  currentLogger?.info("voice-flush-done", { count });
}

// F-EXT001-6(FIX2): 默认日志路径迁移到 Lybra 产品仓任务卡目录(旧 contrib 路径已废弃)
const LOG_PATH_DEFAULT = `${process.env.HOME || ""}/projects/lybra/task_cards/LYBRA-EXT-001/loop.log`;

// AIPOS-R6R: 连接器依赖的 verb + 必填参数清单(单一声明, 启动时对 schema 校验)。
// 动词名/参数名只此一处声明, 不再逐动词手写方法; 校验读 schema/verbs.schema.json。
const REQUIRED_VERBS: Record<string, string[]> = {
  lybra_queue_list: [],
  lybra_task_preview: [],
  lybra_queue_claim_dry_run: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
  lybra_queue_return_dry_run: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
  lybra_queue_return_confirm: ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
  lybra_queue_close_dry_run: ["task_id", "actor", "closure_evidence"],
  lybra_queue_close_confirm: ["task_id", "actor", "closure_evidence"],
  // AIPOS-F35 大项B: 审计裁决托管动词(F29大项E补做)
  lybra_audit_verdict_dry_run: ["reviewed_task_id", "actor", "agent_instance", "owner_policy_ref", "verdict"],
  lybra_audit_verdict_confirm: ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
};

/**
 * AIPOS-C2 大项C: 来源自曝横幅 —— 对每个关键值打印取自哪一层。
 * env 兜底命中 / env 被降级 → 标 ⚠ (2026-08-18 若有此横幅, 毒 env 案第一张截图即破)。
 */
function buildProvenanceBanner(config: LoopConfig): string {
  const order = ["role", "actor", "agent_instance", "owner_policy_ref", "workspace_root", "gate_url", "token"];
  const lines: string[] = [];
  for (const key of order) {
    const p = config.provenance?.[key];
    if (!p) continue;
    let src = p.source;
    if (p.viaEnv) src = `${src} ⚠兜底`;
    if (p.envDowngraded) src = `${src} ⚠env被降级`;
    lines.push(`  ${key}=${p.value} (${src})`);
  }
  return lines.join("\n");
}

/**
 * AIPOS-C4B 大项B: 版本信号。
 *
 * 连接器版本戳 = 分发器生成的源 commit 短哈希(写在 _distributed/.version-{role}
 * 的 version 字段), 取代顾问手工注入的 dist-2026xxxx 戳(已退役)。
 * 连接器模块位于 _distributed/extensions/lybra-loop/, 上溯三级即 _distributed/。
 */
function distRoot(): string {
  try {
    return dirname(dirname(dirname(fileURLToPath(import.meta.url))));
  } catch {
    return process.cwd();
  }
}

function readLocalVersion(role: string): string | null {
  try {
    const p = join(distRoot(), `.version-${role}`);
    if (!existsSync(p)) return null;
    const data = JSON.parse(readFileSync(p, "utf-8"));
    const v = data && typeof data === "object" ? (data as { version?: unknown }).version : null;
    return typeof v === "string" && v ? v : null;
  } catch {
    return null;
  }
}

function buildVersionLine(config: LoopConfig): string {
  const local = readLocalVersion(config.role);
  // AIPOS-F20: 无版本戳带路改指 /lybra sync(入会话, 不再要求切 shell)
  return `lybra-loop 版本: ${local ?? "(无版本戳 — 请 /lybra sync)"}`;
}

/**
 * AIPOS-C4B 大项B: 清单比对(提示级, 绝不拒跑)。
 * 落后 → behind=true, 出声"落后, /lybra sync 后 /reload"(AIPOS-F20 文案更新); 不落后 → null。
 * gate 无此动词 / 连接失败 → error 非空, 但循环照跑。
 */
async function checkManifestFreshness(
  client: GateMcpClient | null,
  config: LoopConfig,
): Promise<{ behind: boolean; local: string | null; remote: string | null; error?: string }> {
  const local = readLocalVersion(config.role);
  if (!client) return { behind: false, local, remote: null };
  try {
    const m = await client.callTool("lybra_distribution_manifest", {});
    const remote = typeof (m as { product_commit?: unknown }).product_commit === "string"
      ? (m as { product_commit: string }).product_commit
      : null;
    return { behind: !!(local && remote && local !== remote), local, remote };
  } catch (e) {
    return { behind: false, local, remote: null, error: e instanceof Error ? e.message : String(e) };
  }
}

function clearTimer() {
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
}

// ---------------------------------------------------------------------------
// AIPOS-F20: /lybra sync —— 薄壳投影既有 lybra sync CLI(工位拉新不出 pi)
// ---------------------------------------------------------------------------
// 锚点: 连接器 /lybra 命令族(on/off/status 既有)+ 既有 lybra sync CLI(C4B 建, 分发单源)。
// Δ=0 薄壳: 子进程调用本机 lybra CLI 的 sync, --harness-root=自身工位根(从身份声明推得);
// 禁在连接器里第二遍实现同步逻辑(CLI 单源不动)。
// CLI bin 路径来源入声明不写死: 优先 .lybra/connection.json 声明键 lybra_bin,
// 缺省探测 <code_repo>/.deploy/current/bin/lybra(与 sweep finalize 同源), 均不可得 →
// 出声带路("本机未装 lybra CLI, 远程工位分发通道见 known-debt")并如实失败。
// 本卡明确不做: 远程工位网络分发通道(known-debt 记明, 迁移后议);
// /reload 自动化(pi 能力边界, 只提示不代按)。
// ---------------------------------------------------------------------------

/** AIPOS-F20: 自身 harness root(工作站根 = 含 .lybra/ 的目录, 身份声明单源)。发现失败 → null。 */
export function resolveSyncHarnessRoot(lybraDir: string | null): string | null {
  return lybraDir ? dirname(lybraDir) : null;
}

export interface LybraBinResolution {
  bin: string | null;
  source: string;
  tried: string[];
}

/**
 * AIPOS-F20: lybra CLI bin 路径解析(声明键优先, 缺省探测, 均不可得 → null)。
 * 层级: ① .lybra/connection.json 可选声明键 lybra_bin(文件存在才用)
 *      ② workspace project.json#code_repo → <code_repo>/.deploy/current/bin/lybra
 *      ③ 缺省 <workspaceRoot>/../../../lybra/.deploy/current/bin/lybra(与 sweep finalize 同款)
 * workspaceRoot 未显式给时从 connection.json#workspace_root 补读(单次读同一声明文件)。
 */
export function resolveLybraBin(
  fs: { existsSync(p: string): boolean; readFileSync(p: string, enc: string): string },
  path: { join(...s: string[]): string },
  opts: { workspaceRoot?: string; lybraDir: string | null },
): LybraBinResolution {
  const tried: string[] = [];
  // ① 声明键(connection.json#lybra_bin, 可选; 顺带补读 workspace_root)
  let conn: { lybra_bin?: unknown; workspace_root?: unknown } | null = null;
  if (opts.lybraDir) {
    try {
      const connPath = path.join(opts.lybraDir, "connection.json");
      if (fs.existsSync(connPath)) {
        conn = JSON.parse(fs.readFileSync(connPath, "utf-8")) as { lybra_bin?: unknown; workspace_root?: unknown };
      }
    } catch {
      conn = null; // 读/解析失败 → 走缺省探测层
    }
  }
  const declared = typeof conn?.lybra_bin === "string" ? conn.lybra_bin.trim() : "";
  if (declared) {
    if (fs.existsSync(declared)) {
      return { bin: declared, source: "声明键 connection.json#lybra_bin", tried };
    }
    tried.push(`声明键 lybra_bin=${declared}(文件不存在)`);
  }
  // ② project.json#code_repo(sweep finalize 同源)
  let workspaceRoot = opts.workspaceRoot || "";
  if (!workspaceRoot && typeof conn?.workspace_root === "string" && conn.workspace_root) {
    workspaceRoot = conn.workspace_root;
  }
  let codeRepo = "";
  let codeRepoSource = "";
  try {
    const pjPath = path.join(workspaceRoot, "project.json");
    if (fs.existsSync(pjPath)) {
      const pj = JSON.parse(fs.readFileSync(pjPath, "utf-8")) as { code_repo?: unknown };
      if (typeof pj?.code_repo === "string" && pj.code_repo) {
        codeRepo = pj.code_repo;
        codeRepoSource = "project.json#code_repo";
      }
    }
  } catch {
    // project.json 读/解析失败 → 用缺省
  }
  // AIPOS-F25 大项C: code_repo 未注册时才拼缺省路径，禁 lybra/lybra 双叠
  // (chris 实撞: code_repo="~/projects/lybra" 时拼 workspace/../../../lybra 成双叠)
  if (!codeRepo) {
    // 仅当 project.json 不存在或无 code_repo 声明时，才拼缺省相对路径
    codeRepo = path.join(workspaceRoot, "../../../lybra");
    codeRepoSource = "缺省 workspace/../../../lybra";
  }
  // ③ 探测点: <code_repo>/.deploy/current/bin/lybra
  const probe = path.join(codeRepo, ".deploy/current/bin/lybra");
  if (fs.existsSync(probe)) {
    return { bin: probe, source: `探测 ${codeRepoSource} → .deploy/current/bin/lybra`, tried };
  }
  tried.push(probe);
  return { bin: null, source: "(均不可得)", tried };
}

/** AIPOS-F20: sync stdout 尾行(最后一个非空行, 供 voice 透传)。 */
export function extractSyncTailLine(stdout: string): string {
  const lines = String(stdout || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  return lines.length > 0 ? lines[lines.length - 1] : "";
}

// ---------------------------------------------------------------------------
// AIPOS-F23: /lybra enroll —— 工位一贴上岗(自包含码解析→交换→落盘→land→连通验证)
// ---------------------------------------------------------------------------
// 锚点: F20 /lybra 命令族投影 + 既有 enroll_client 交换流(交换/TTL/撤销面沿用, 单源在门侧)。
// 码格式唯一定义处 = 产品仓 tools/aipos_cli/enrollment.py(encode_self_contained_code);
// 本侧只做结构性解析(前缀 + base64url + JSON 字段抽取), 配方不入连接器(禁第二份发码/码格式逻辑)。
// bootstrap-token 要求已删除: 码内嵌零 scope 运输凭证即 transport 认证(码即运输认证)。
// 落盘只有一个目标: 本工位 .lybra/(治理工作区拒写, 第九坑防护)。
// ---------------------------------------------------------------------------

/**
 * F24A 大項C: enroll 产品侧故障带路(禁裸干)。
 * 治 2026-08-22 现场: 新工位无章程 agent 拿到 401 即钻产品源码谋划重启 gate(serve stop)。
 * 凡属产品侧/门侧故障类的 enroll 失败(交换拒/门不可达/返回残缺/连通验证不过),
 * 文案必附本带路: 禁自行诊断修复, 报告顾问即可。用法类错误(码格式/用法)不附, 各自有 teaching。
 */
const ENROLL_PRODUCT_FAULT_GUIDE =
  "此为产品侧故障, 与你无关, 禁自行诊断修复门/服务/部署(禁 serve stop/重启 gate/改配置/考古产品源码), 把上面报错原文报告顾问即可。";

/** F23: 自包含码前缀(与产品仓 enrollment.py SELF_CONTAINED_CODE_PREFIX 同源同值)。 */
const ENROLL_CODE_PREFIX = "LYBRAENROLL1.";

export interface SelfContainedCode {
  v: number;
  gate_url: string;
  governance_root: string;
  transport_token: string;
  code: string;
}

/** F23: base64url → string(无 padding 容错, 等价 Python base64.urlsafe_b64decode)。 */
function base64UrlDecode(input: string): string {
  // JS 的 (-len)%4 带符号(与 Python 不同), 用 (4 - len%4) % 4 保持非负
  const pad = (4 - (input.length % 4)) % 4;
  const b64 = input + "=".repeat(pad);
  return Buffer.from(b64, "base64url").toString("utf-8");
}

/**
 * F23: 解析自包含码。非自包含码(旧裸码)返回 null(调用方走带 --gate-url 的旧路径)。
 * 损坏/版本不识别 → 抛错(带原因与下一步, F9)。
 */
export function parseSelfContainedCode(text: string): SelfContainedCode | null {
  const s = String(text || "").trim();
  if (!s.startsWith(ENROLL_CODE_PREFIX)) return null;
  const b64 = s.slice(ENROLL_CODE_PREFIX.length);
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(base64UrlDecode(b64)) as Record<string, unknown>;
  } catch {
    throw new Error(
      "自包含码损坏(无法解码 base64/JSON)\n下一步: 向顾问重新发码(lybra_enroll_code_dry_run/confirm), 整条原样转贴。",
    );
  }
  if (payload.v !== 1) {
    throw new Error(
      `自包含码版本不识别(v=${String(payload.v)})\n下一步: 工位连接器版本过旧 —— 请更新连接器后重贴; 或向顾问重新发码。`,
    );
  }
  const gateUrl = String(payload.gate_url || "").trim();
  const transportToken = String(payload.transport_token || "").trim();
  const code = String(payload.code || "").trim();
  if (!gateUrl || !transportToken || !code) {
    throw new Error(
      "自包含码缺必填字段(gate_url/transport_token/code)\n下一步: 向顾问重新发码后整条原样转贴。",
    );
  }
  return {
    v: 1,
    gate_url: gateUrl,
    governance_root: String(payload.governance_root || ""),
    transport_token: transportToken,
    code,
  };
}

/** F23: enroll 目标工位根 —— 已有 .lybra 则其父(重铸/轮换); 否则 pi 会话 cwd(新工位约定: 从工位根启动 pi)。 */
export function resolveEnrollTargetRoot(lybraDir: string | null, cwd?: string): string {
  if (lybraDir) return dirname(lybraDir);
  return cwd || process.cwd();
}

/** F23: 治理工作区防护(验收⑧/第九坑) —— 目标 == 码内嵌 governance_root 或含 5_tasks/queue 结构签名 → 拒写。 */
export function isGovernanceWorkspace(
  fs: { existsSync(p: string): boolean },
  path: { join(...s: string[]): string },
  targetRoot: string,
  governanceRoot: string,
): boolean {
  if (governanceRoot && resolveComparable(governanceRoot) === resolveComparable(targetRoot)) return true;
  return fs.existsSync(path.join(targetRoot, "5_tasks", "queue"));
}

function resolveComparable(p: string): string {
  const norm = p.replace(/\/+$/, "");
  return norm.endsWith("/.lybra") ? norm.slice(0, -"/.lybra".length) : norm;
}

/** F23: 合并写 .lybra/role(验收⑨: 保留既有键 owner_policy_ref 等, 禁整文件覆盖)。 */
export function mergeRoleFile(
  existingContent: string | null,
  role: string,
  instance: string | null,
): Record<string, unknown> {
  let data: Record<string, unknown> = {};
  if (existingContent) {
    try {
      const parsed = JSON.parse(existingContent);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        data = { ...(parsed as Record<string, unknown>) };
      }
    } catch {
      // 旧纯文本格式(单行 role)或损坏 → 从空开始(不保留无法解析的内容)
    }
  }
  data.role = role;
  if (instance) data.instance = instance;
  data.enrolled_at = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  return data;
}

/** F23: connection.json 合并(保留既有键; tokens 按 agent_instance/role upsert)。 */
export function upsertEnrollConnection(
  existing: Record<string, unknown> | null,
  gateUrl: string,
  workspaceRoot: string,
  tokenEntry: Record<string, unknown>,
): Record<string, unknown> {
  const data: Record<string, unknown> = existing ? { ...existing } : {};
  const agentInstance = typeof tokenEntry.agent_instance === "string" ? tokenEntry.agent_instance : null;
  const role = typeof tokenEntry.role === "string" ? tokenEntry.role : "";
  const tokens = Array.isArray(data.tokens) ? [...(data.tokens as unknown[])] : [];
  let matchedIdx = -1;
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i] as Record<string, unknown>;
    if (agentInstance && t.agent_instance === agentInstance) {
      matchedIdx = i;
      break;
    }
    if (!agentInstance && !t.agent_instance && t.role === role) {
      matchedIdx = i;
      break;
    }
  }
  if (matchedIdx >= 0) tokens[matchedIdx] = tokenEntry;
  else tokens.push(tokenEntry);
  data.tokens = tokens;
  // mcp.rpc_url = 规范化 gate 地址(与 Python 端 normalize_gate_url_for_same_host 同源: loopback 化由工位侧网络环境决定, 此处直存码内嵌地址)
  const normalized = gateUrl.replace(/\/+$/, "");
  const mcpUrl = normalized.endsWith("/mcp") ? normalized : `${normalized}/mcp`;
  data.mcp = { ...(typeof data.mcp === "object" && data.mcp ? (data.mcp as Record<string, unknown>) : {}), rpc_url: mcpUrl };
  if (!data.workspace_root) data.workspace_root = workspaceRoot;
  if (!("config_version" in data)) data.config_version = 1;
  return data;
}

/**
 * AIPOS-F11 大项B: 从子进程异常提取 stderr 末尾若干行。
 * execSync 抛错时 message 只有 "Command failed: ...", stderr 全在 e.stderr;
 * 而 finalize CLI 的诊断正文(Message/Operations)实际落在 stdout。
 * 故 stderr 优先、stdout 补充 —— 纯管道, 只取文本, 不改任何级别判断。
 */
function subprocessFailureTail(err: unknown, maxLines = 12): string {
  const obj = (err ?? {}) as { stderr?: unknown; stdout?: unknown };
  const parts: string[] = [];
  for (const key of ["stderr", "stdout"] as const) {
    const v = obj[key];
    let text = "";
    if (typeof v === "string") {
      text = v;
    } else if (v && typeof v === "object" && typeof (v as { toString?: () => string }).toString === "function") {
      text = String(v);
    }
    if (text) {
      const tail = text.replace(/\r\n/g, "\n").split("\n").slice(-maxLines).join("\n");
      parts.push(`[${key}] ${tail}`);
    }
  }
  return parts.join("\n");
}

// AIPOS-F10:定时器不捕获 ctx/pi — 每次 tick 从 liveCtx/livePi 取当前活引用。
// 根治 /reload 后定时器持 stale ctx 导致的 "extension ctx is stale" 静死。
function scheduleNextTick(delayMs: number) {
  clearTimer();
  pendingTimer = setTimeout(() => {
    pendingTimer = null;
    if (!loopState.on) return; // 期间被 off
    // F-EXT001-4(FIX1):直接调用 tick 函数,不经 sendUserMessage(永不触发命令)
    doTick().catch((e) => {
      // F-EXT001-8(FIX4):不再静默吞错,落日志
      currentLogger?.warn("scheduleNextTick-doTick-error", {
        error: e instanceof Error ? e.message : String(e),
      });
    });
  }, delayMs);
}

/**
 * AIPOS-C3B 大项D③: sweep 候选集反转 — 从 queue/claimed(0-2 张)反查裁决,
 * 替代遍历 151 裁决目录。出声=一行汇总+仅异常逐条。
 *
 * AIPOS-C3B 大项B①: 裁决选取按 frontmatter 时间戳取最新, 禁按文件名排序。
 * AIPOS-C3B 大项B②: 只认门生记录(具备 record_type/verdict_id/verdict_at 机器特征),
 *   手写文件忽略+warn。
 */

/** 从 frontmatter 文本提取简单 YAML 标量值(不引完整 YAML 解析器) */
export function extractFrontmatterField(content: string, field: string): string | null {
  // Match both `field: value` and `field: 'value'` / `field: "value"`
  const re = new RegExp(`^${field}:\\s*['"]?([^'"\\n]*)['"]?\\s*$`, "m");
  const m = content.match(re);
  return m ? m[1].trim() : null;
}

/**
 * AIPOS-C3B 大项B②: 检查裁决文件是否具备门生标记(record_type + verdict_id + verdict_at)。
 * 手写文件(缺少机器特征)返回 false。
 */
export function isGateBornVerdict(content: string): { authentic: boolean; reason?: string } {
  // AIPOS-F12 大项A/B: 与 Python 单源 audit_helpers.is_gate_born_verdict_metadata 同规则
  // (record_type 以 'audit_verdict' 开头 + verdict_id 以 'verdict_' 开头 + verdict_at 非空),
  // 不再使用更严的 record_type === 'audit_verdict_record'(第二定义, 会与门内判定不一致)。
  const recordType = extractFrontmatterField(content, "record_type");
  const verdictId = extractFrontmatterField(content, "verdict_id");
  const verdictAt = extractFrontmatterField(content, "verdict_at");
  if (!recordType || !verdictId || !verdictAt) {
    return { authentic: false, reason: `缺少门生标记(record_type=${!!recordType}, verdict_id=${!!verdictId}, verdict_at=${!!verdictAt})` };
  }
  if (!recordType.startsWith("audit_verdict")) {
    return { authentic: false, reason: `record_type=${recordType}(预期 audit_verdict* 家族)` };
  }
  if (!verdictId.startsWith("verdict_")) {
    return { authentic: false, reason: `verdict_id=${verdictId}(预期 verdict_* 命名)` };
  }
  return { authentic: true };
}

/**
 * AIPOS-C3B 大项B①: 从裁决目录中按 frontmatter verdict_at 时间戳选取最新门生裁决。
 * 禁按文件名排序。手写文件(缺门生标记)忽略+warn。
 */
function selectLatestGateVerdict(
  fs: any,
  verdictDir: string,
  logger: Logger | null,
  taskId: string,
): { verdict: string; verdictAt: string; verdictId: string; filePath: string } | null {
  let files: string[];
  try {
    files = fs.readdirSync(verdictDir)
      .filter((f: string) => f.endsWith(".md"));
  } catch {
    return null;
  }
  if (files.length === 0) return null;

  const candidates: { verdict: string; verdictAt: string; verdictId: string; filePath: string }[] = [];
  const rejected: string[] = [];

  for (const f of files) {
    const filePath = `${verdictDir}/${f}`;
    let content: string;
    try {
      content = fs.readFileSync(filePath, "utf-8");
    } catch {
      rejected.push(`${f}: 读取失败`);
      continue;
    }
    const auth = isGateBornVerdict(content);
    if (!auth.authentic) {
      rejected.push(`${f}: ${auth.reason}`);
      continue;
    }
    const verdictVal = extractFrontmatterField(content, "verdict") || "";
    const verdictAt = extractFrontmatterField(content, "verdict_at") || "";
    const verdictId = extractFrontmatterField(content, "verdict_id") || f.replace(/\.md$/, "");
    if (!verdictVal) {
      rejected.push(`${f}: verdict 字段缺失`);
      continue;
    }
    candidates.push({ verdict: verdictVal.toUpperCase(), verdictAt, verdictId, filePath });
  }

  if (rejected.length > 0) {
    logger?.warn("sweep-rejected-verdicts", { task_id: taskId, rejected });
  }
  if (candidates.length === 0) return null;

  // AIPOS-C3B 大项B①: 按 verdict_at 时间戳取最新(禁按文件名排序)
  candidates.sort((a, b) => (a.verdictAt > b.verdictAt ? -1 : a.verdictAt < b.verdictAt ? 1 : 0));
  return candidates[0];
}

// ---------------------------------------------------------------------------
// AIPOS-F12 大项B: 门领地保护路径声明读取 + sweep 自动隔离(消毒)
// ---------------------------------------------------------------------------

/** 从 config.schema governance_structure.gate_territory 读保护路径声明(单源, 禁硬编码)。 */
export function readGateTerritoryDeclaration(): GateTerritoryDeclaration {
  const schema = loadConfigSchema();
  const territory = schema?.governance_structure?.gate_territory;
  if (!territory) {
    return { protected_paths: [], quarantine_dir: "", superseded_suffix: ".superseded" };
  }
  return {
    protected_paths: Array.isArray(territory.protected_paths) ? territory.protected_paths : [],
    quarantine_dir: territory.quarantine_dir || "governance/quarantine/",
    superseded_suffix: territory.superseded_suffix || ".superseded",
    quarantine_policy: territory.quarantine_policy,
    era_exemption: territory.era_exemption,
  };
}

/**
 * AIPOS-F14 大项C: 构建活动卡集合(queue/claimed 下的 task_id)。
 * 与收账同源(C3B):只扫活动卡对应的裁决目录, 不遍历全量 records/。
 * 返回 Set<string> of task_ids (目录名 = task_id 的规范化形式)。
 */
export function buildActiveTaskIdSet(
  fs: any,
  path: any,
  workspaceRoot: string,
): Set<string> {
  const activeIds = new Set<string>();
  const claimedDir = path.join(workspaceRoot, "5_tasks", "queue", "claimed");
  if (!fs.existsSync(claimedDir)) return activeIds;
  let files: string[];
  try {
    files = fs.readdirSync(claimedDir).filter((f: string) => f.endsWith(".md"));
  } catch {
    return activeIds;
  }
  for (const f of files) {
    const filePath = path.join(claimedDir, f);
    let content: string;
    try {
      content = fs.readFileSync(filePath, "utf-8");
    } catch {
      continue;
    }
    const taskId = extractFrontmatterField(content, "task_id");
    if (taskId) {
      // 目录名 = task_id 的小写连字符形式(与 board_adapter._task_filename_for 一致)
      activeIds.add(taskId);
      // 也加规范化目录名形式
      const normalized = taskId.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
      activeIds.add(normalized);
    }
  }
  return activeIds;
}

/**
 * AIPOS-F12 大项B + AIPOS-F14 大项C: 扫描门领地保护路径内的非门生裁决文件, 自动移 quarantine。
 * AIPOS-F14 大项C: 候选集改为只扫活动卡(queue/claimed)对应的裁决目录, 与收账同源(C3B)。
 * 全量 records/ 追溯扫描退役(不再遍历所有裁决目录)。
 * 门生判定用 isGateBornVerdict(与门内 F2 同规则, 大项A 同一单源)。
 * 拿不准(有 verdict_ 机器标记但未知 record_type)只出声不动文件(禁误伤)。
 */
export function quarantineHandWrittenVerdicts(
  fs: any,
  path: any,
  config: LoopConfig,
  logger: Logger | null,
): { quarantined: number; emittedUncertain: number } {
  const territory = readGateTerritoryDeclaration();
  const protectedPaths = (territory?.protected_paths ?? []).filter((p) => typeof p === "string" && p.length > 0);
  if (protectedPaths.length === 0) {
    logger?.info("quarantine-skip-no-declaration", {});
    return { quarantined: 0, emittedUncertain: 0 };
  }
  const quarantineDir = (territory?.quarantine_dir || "governance/quarantine/").replace(/\/+$/, "");
  const suffix = territory?.superseded_suffix || ".superseded";
  const workspaceRoot = config.workspaceRoot.replace(/\/+$/, "");
  let quarantined = 0;
  let emittedUncertain = 0;

  // AIPOS-F14 大项C: 只扫活动卡对应的裁决目录(与收账同源 C3B)
  const activeTaskIds = buildActiveTaskIdSet(fs, path, workspaceRoot);
  logger?.info("quarantine-active-task-set", { size: activeTaskIds.size });

  for (const protectedRel of protectedPaths) {
    const protectedAbs = path.join(workspaceRoot, protectedRel);
    const verdictsRoot = path.join(protectedAbs, "audit_verdicts");
    if (!fs.existsSync(verdictsRoot)) continue;
    let taskDirs: string[];
    try {
      taskDirs = fs.readdirSync(verdictsRoot);
    } catch {
      continue;
    }
    for (const taskDir of taskDirs) {
      // AIPOS-F14 大项C: 只扫活动卡目录, 非活动卡目录出声跳过
      if (!activeTaskIds.has(taskDir)) {
        logger?.info("quarantine-skip-inactive-task", { task_dir: taskDir, reason: "非活动卡(queue/claimed)目录, 不扫" });
        continue;
      }
      const dir = path.join(verdictsRoot, taskDir);
      let stat;
      try {
        stat = fs.statSync(dir);
      } catch {
        continue;
      }
      if (!stat.isDirectory()) continue;
      let files: string[];
      try {
        files = fs.readdirSync(dir);
      } catch {
        continue;
      }
      for (const f of files) {
        if (!f.endsWith(".md")) continue;
        const filePath = path.join(dir, f);
        let content: string;
        try {
          content = fs.readFileSync(filePath, "utf-8");
        } catch {
          continue;
        }
        const auth = isGateBornVerdict(content);
        if (auth.authentic) continue; // 门生记录, 零误伤
        const verdictId = extractFrontmatterField(content, "verdict_id");
        if (verdictId && verdictId.startsWith("verdict_")) {
          // 拿不准: 有机器标记但未知 record_type → 只出声不动
          logger?.warn("quarantine-uncertain-skip", { file: filePath, reason: "有 verdict_id 机器标记但非门生, 拿不准只出声不动" });
          voice(`sweep 隔离跳过(拿不准): ${filePath} — 有机器标记但非门生, 只出声不动`, "warn", false);
          emittedUncertain++;
          continue;
        }
        // 明确手写(缺门生标记)→ 隔离
        const targetDir = path.join(workspaceRoot, quarantineDir);
        const targetPath = path.join(targetDir, `${f}${suffix}`);
        try {
          fs.mkdirSync(targetDir, { recursive: true });
          fs.renameSync(filePath, targetPath);
        } catch (e) {
          logger?.warn("quarantine-move-failed", { file: filePath, error: e instanceof Error ? e.message : String(e) });
          continue;
        }
        const readmePath = path.join(targetDir, "README.md");
        const line = `- \`${f}${suffix}\` — AIPOS-F12 sweep 自动隔离(${new Date().toISOString()}): 非门生裁决手写件, 原路径 ${filePath}。裁决由门落盘, 勿手写进 records/。`;
        try {
          if (fs.existsSync(readmePath)) {
            fs.appendFileSync(readmePath, line + "\n", "utf-8");
          } else {
            fs.writeFileSync(readmePath, `---\nstatus: active\ndecided_at: '${new Date().toISOString()}'\nsuperseded_by: null\n---\n# 手写残稿隔离区\n\n${line}\n`, "utf-8");
          }
        } catch (e) {
          logger?.warn("quarantine-readme-failed", { error: e instanceof Error ? e.message : String(e) });
        }
        const nextStep = `已隔离到 ${targetPath}; 提醒该工位: 裁决由门落盘, 勿手写进 records/`;
        logger?.warn("quarantine-hand-written", { file: filePath, moved_to: targetPath, next_step: nextStep });
        voice(`sweep 隔离手写件: ${filePath} → ${targetPath}; ${nextStep}`, "warn", false);
        quarantined++;
      }
    }
  }
  return { quarantined, emittedUncertain };
}

// ---------------------------------------------------------------------------
// AIPOS-F12 大项C: pi 工具层写拦截(条件项) — 命中门领地保护路径的写操作直接拒
// ---------------------------------------------------------------------------

let writeGuardCfg: { workspaceRoot: string; protectedPaths: string[] } | null = null;

function refreshWriteGuard(): void {
  try {
    const config = loadConfig(process.env);
    const territory = readGateTerritoryDeclaration();
    const protectedPaths = (territory?.protected_paths ?? []).filter((p) => typeof p === "string" && p.length > 0);
    writeGuardCfg = { workspaceRoot: config.workspaceRoot.replace(/\/+$/, ""), protectedPaths };
  } catch {
    writeGuardCfg = null;
  }
}

/** 判断一个绝对/相对写目标是否落在门领地保护路径内。 */
export function isProtectedWriteTarget(target: string, wsRoot: string, protectedPaths: string[]): boolean {
  const p = target.trim();
  if (!p) return false;
  const abs = p.startsWith("/") ? p : `${wsRoot}/${p.replace(/^\.?\//, "")}`;
  const normalized = abs.replace(/\/+$/, "");
  for (const rel of protectedPaths) {
    const protAbs = `${wsRoot}/${rel}`.replace(/\/+$/, "");
    if (normalized === protAbs || normalized.startsWith(protAbs + "/")) return true;
  }
  return false;
}

/** 从工具调用取写目标(write/edit 有 path; bash 只拦明确的写重定向进保护路径)。 */
export function extractWriteTargets(toolName: string, input: any): string[] {
  const name = String(toolName || "");
  const targets: string[] = [];
  if (name === "write" || name === "edit") {
    const p = input?.path ?? input?.file_path;
    if (typeof p === "string" && p) targets.push(p);
    return targets;
  }
  if (name === "bash") {
    const cmd = typeof input?.command === "string" ? input.command : "";
    // 只抓 `>`, `>>`, `2>`, `tee` 的写目标(保守, 不做完整 shell 解析)。
    const re = /(?:2?>>?|tee(?: -a)?)\s+([^\s;&|]+)/g;
    let m;
    while ((m = re.exec(cmd)) !== null) {
      targets.push(m[1]);
    }
    return targets;
  }
  return targets;
}

/**
 * AIPOS-F16: 本工位在途卡清点 — 扫 queue/claimed(既有 sweep 候选集), 只数
 * frontmatter claimed_by == 本工位实例 且卡号读自 frontmatter(F17: 禁从文件名猜)的卡。
 * 审计位等他人工位的卡不算(它们的收口归各自工位); close 后卡离开 claimed, 天然出集。
 */
export function findInFlightCards(
  fs: any,
  path: any,
  workspaceRoot: string,
  agentInstance: string,
): string[] {
  const claimedDir = path.join(workspaceRoot, "5_tasks", "queue", "claimed");
  let files: string[];
  try {
    files = fs.readdirSync(claimedDir).filter((f: string) => f.endsWith(".md"));
  } catch {
    return [];
  }
  const ids: string[] = [];
  for (const f of files) {
    let content: string;
    try {
      content = fs.readFileSync(path.join(claimedDir, f), "utf-8");
    } catch {
      continue;
    }
    const claimedBy = extractFrontmatterField(content, "claimed_by");
    if (!claimedBy || claimedBy !== agentInstance) continue; // 非本工位(如审计位)不算在途
    const taskId = extractFrontmatterField(content, "task_id");
    if (!taskId) continue; // AIPOS-F17: 卡号读 frontmatter, 缺则跳过(禁猜文件名)
    ids.push(taskId);
  }
  return ids;
}

// AIPOS-C3D 大项A: sweep 防重入标志 — 启动存量收敛与每轮 tick 可能并发触发同一 sweep,
// 双 finalize 会引发 deploy 竞态;单线程下 check+set 无 await 间隔, 原子生效。
let sweepInFlight = false;

// AIPOS-F10:不接收 ctx — 内部从 liveCtx 取活引用。
async function tryAutoFinalizeOnPassVerdict(): Promise<boolean> {
  if (sweepInFlight) {
    currentLogger?.info("sweep-skip-inflight", {});
    return false;
  }
  sweepInFlight = true;
  let result = false;
  try {
    result = await tryAutoFinalizeOnPassVerdictCore();
  } catch (e) {
    // 保底: core 内部应自洽不抛, 意外抛了也落日志并复位标志(不静默吞)
    currentLogger?.warn("sweep-unexpected-error", {
      error: e instanceof Error ? e.message : String(e),
    });
  }
  sweepInFlight = false;
  return result;
}

// AIPOS-F10:不接收 ctx — 内部从 liveCtx 取活引用。
async function tryAutoFinalizeOnPassVerdictCore(): Promise<boolean> {
  if (!currentClient || !currentLogger) {
    currentLogger?.warn("sweep-no-client", { reason: "currentClient or currentLogger not initialized" });
    voice("sweep 跳过:客户端或日志器未初始化", "warn", false);
    return false;
  }

  let config;
  try {
    config = loadConfig(process.env);
  } catch (e) {
    const msg = `sweep 配置加载失败: ${e instanceof Error ? e.message : String(e)}`;
    currentLogger.warn("sweep-config-failed", { error: String(e) });
    voice(msg, "error", true);
    return false;
  }

  const fs = await import("node:fs");
  const path = await import("node:path");

  // AIPOS-R6S 大项A②: sweep 执行范围按角色能力判定(roles.schema scopes)。
  if (config.role !== "executor") {
    const msg = `sweep 跳过: 当前角色 ${config.role || "?"} 不具 finalize/close 能力(roles.schema scopes), 不跑 finalize`;
    currentLogger.info("sweep-skip-role", { role: config.role });
    voice(msg, "info", false);
    return false;
  }

  // AIPOS-F12 大项B: sweep 自动隔离 — 扫描门领地保护路径内非门生文件移 quarantine。
  // 放在 claimed 队列检查之前, 即使队列为空也每轮消毒(禁误伤: 门生零动, 拿不准只出声)。
  try {
    const q = quarantineHandWrittenVerdicts(fs, path, config, currentLogger);
    if (q.quarantined > 0 || q.emittedUncertain > 0) {
      currentLogger.info("quarantine-done", { quarantined: q.quarantined, uncertain: q.emittedUncertain });
    }
  } catch (e) {
    currentLogger?.warn("quarantine-error", { error: e instanceof Error ? e.message : String(e) });
  }

  // AIPOS-C3B 大项D③: 候选集反转 — 从 queue/claimed 反查裁决(替代遍历 151 裁决目录)
  const claimedDir = path.join(config.workspaceRoot, "5_tasks/queue/claimed");
  if (!fs.existsSync(claimedDir)) {
    currentLogger.info("sweep-no-claimed-dir", {});
    voice("sweep: claimed 队列为空", "info", false);
    return false;
  }

  const claimedCards = fs.readdirSync(claimedDir)
    .filter((f: string) => f.endsWith(".md"));

  if (claimedCards.length === 0) {
    currentLogger.info("sweep-no-claimed-cards", {});
    voice("sweep: 无 claimed 卡", "info", false);
    return false;
  }

  currentLogger.info("sweep-start", { claimed_count: claimedCards.length });

  let processedCount = 0;
  const anomalies: { task_id: string; reason: string }[] = [];

  for (const cardFile of claimedCards) {
    // AIPOS-F17 大项B: task_id 从卡 frontmatter 读, 文件名仅作目录索引, 禁 toUpperCase 猜测。
    const cardPath = path.join(claimedDir, cardFile);
    let taskId: string;
    try {
      const cardContent = fs.readFileSync(cardPath, "utf-8");
      const fmTaskId = extractFrontmatterField(cardContent, "task_id");
      if (!fmTaskId) {
        currentLogger?.warn("sweep-card-no-frontmatter-task-id", { cardFile });
        voice(`sweep 跳过: ${cardFile} 无 frontmatter task_id`, "warn", false);
        continue;
      }
      taskId = fmTaskId;
    } catch (e) {
      currentLogger?.warn("sweep-card-read-failed", { cardFile, error: e instanceof Error ? e.message : String(e) });
      continue;
    }

    // 反查裁决目录
    const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", taskId);
    if (!fs.existsSync(verdictDir)) {
      // AIPOS-F17 大项C: 无裁决目录 = 等待中, 出声一行(终结全静默)。
      voice(`${taskId}: 等待裁决落库中 (next_step: 裁决落库后自动收账, 无需操作)`, "info", false);
      continue;
    }

    // AIPOS-C3B 大项B①②: 按时间戳选最新门生裁决
    const latest = selectLatestGateVerdict(fs, verdictDir, currentLogger, taskId);
    if (!latest) {
      anomalies.push({ task_id: taskId, reason: "裁决目录存在但无门生裁决" });
      continue;
    }
    if (latest.verdict !== "PASS" && latest.verdict !== "PASS_WITH_NOTES") {
      // 非 PASS 裁决 = 还在审计循环中,不是异常
      continue;
    }

    // 检查是否已 close
    const closureDir = path.join(config.workspaceRoot, "5_tasks/records/closures", taskId);
    if (fs.existsSync(closureDir)) {
      const closureFiles = fs.readdirSync(closureDir).filter((f: string) => f.startsWith("closure_") && f.endsWith(".md"));
      if (closureFiles.length > 0) continue; // 已结案,跳过
    }

    // 候选卡: 有 PASS 裁决 + 未 close → 自动 finalize+close
    currentLogger.info("auto-finalize-start", { task_id: taskId, verdict: latest.verdict, verdict_at: latest.verdictAt });
    voice(`sweep 候选: ${taskId} (${latest.verdict})`, "info", false);

    try {
      const { execSync } = await import("node:child_process");

      let codeRepo = path.join(config.workspaceRoot, "../../../lybra");
      try {
        const projectJsonPath = path.join(config.workspaceRoot, "project.json");
        if (fs.existsSync(projectJsonPath)) {
          const projectJson = JSON.parse(fs.readFileSync(projectJsonPath, "utf-8"));
          if (projectJson.code_repo) codeRepo = projectJson.code_repo;
        }
      } catch (e) {
        currentLogger.warn("project-json-parse-failed", { error: String(e) });
      }

      const lybraBin = path.join(codeRepo, ".deploy/current/bin/lybra");
      const finalizeCmd = `${lybraBin} --workspace-root ${codeRepo} finalize --task-id ${taskId} --actor ${config.actor} --push --deploy`;
      const finalizeOutput = execSync(finalizeCmd, { cwd: codeRepo, encoding: "utf-8", stdio: "pipe" });

      currentLogger.info("auto-finalize-success", { task_id: taskId, output: finalizeOutput.slice(0, 500) });

      let finalizeCommitHash = "";
      try {
        finalizeCommitHash = execSync("git rev-parse HEAD", { cwd: codeRepo, encoding: "utf-8" }).trim();
      } catch (e) {
        currentLogger.warn("auto-close-get-hash-failed", { task_id: taskId, error: String(e) });
      }
      const closeArgs = {
        task_id: taskId,
        actor: config.actor,
        closure_evidence: finalizeCommitHash
          ? { finalize_commit_hash: finalizeCommitHash }
          : { finalize_return_ref: `finalize_${taskId}` },
      };

      const closeDryResp = await currentClient.callTool("lybra_queue_close_dry_run", closeArgs);
      if (closeDryResp.verdict === "BLOCK" || closeDryResp.isError === true) {
        const detail = closeDryResp.blocking_reasons || closeDryResp.errors || closeDryResp.message || JSON.stringify(closeDryResp);
        const msg = `auto-close dry_run BLOCK: ${taskId} - ${JSON.stringify(detail)}`;
        currentLogger.error("auto-close-blocked", { task_id: taskId, response: closeDryResp });
        voice(msg, "error", true);
        anomalies.push({ task_id: taskId, reason: "close dry_run BLOCK" });
        continue;
      }

      await currentClient.callTool("lybra_queue_close_confirm", closeArgs);
      currentLogger.info("auto-close-success", { task_id: taskId });
      // AIPOS-F4 大项C: 收账成功升为可见 info 一行(合并 <hash>/部署 <hash>/close)。
      const settleHash = finalizeCommitHash ? finalizeCommitHash.slice(0, 8) : "?";
      voice(`已收账 ${taskId}: 合并 ${settleHash}/部署 ${settleHash}/close`, "info", true);
      processedCount++;
    } catch (e) {
      const rawErr = e instanceof Error ? e.message : String(e);
      // AIPOS-F11 大项B: stderr/stdout 透传 — execSync 失败 message 只有 "Command failed" 一句,
      // 诊断正文全在 e.stderr/e.stdout。末尾若干行并入错误输出与 loop 日志 detail
      // (纯管道, 不改任何级别判断 — 级别仍由 F4 声明管)。
      const failureTail = subprocessFailureTail(e, 12);
      // AIPOS-F4 大项B/C: 脏树/可重试类失败属 auto_recoverable → warn + 下一步(等下一轮); 其余 → error。
      const isDirtyTree = /工作树不干净|dirty|uncommitted|changes not staged|working tree/i.test(rawErr);
      const level = isDirtyTree ? "warn" : "error";
      const nextStep = isDirtyTree ? " 下一步: 等下一轮 sweep 再试(脏树自动让路)" : "";
      const tailBlock = failureTail ? `\n  子进程输出尾行:\n${failureTail}` : "";
      const errMsg = `sweep finalize 失败: ${taskId} - ${rawErr}${tailBlock}${nextStep}`;
      currentLogger.error("auto-finalize-failed", { task_id: taskId, error: rawErr, failure_tail: failureTail, isDirtyTree });
      voice(errMsg, level, true);
      anomalies.push({ task_id: taskId, reason: isDirtyTree ? "脏树让路等下一轮" : "finalize 失败" });
    }
  }

  // AIPOS-C3B 大项D③: 出声=一行汇总+仅异常逐条
  const total = claimedCards.length;
  const summaryMsg = anomalies.length > 0
    ? `sweep: ${total} claimed, 收 ${processedCount} 张, ${anomalies.length} 异常(${anomalies.map(a => a.task_id).join(",")})`
    : processedCount > 0
      ? `sweep: ${total} claimed, 收 ${processedCount} 张`
      : `sweep: ${total} claimed, 无需处理`;
  currentLogger.info("sweep-complete", { processed: processedCount, claimed: total, anomalies });
  voice(summaryMsg, "info", false);

  return processedCount > 0;
}

/**
 * AIPOS-CONN-LOOP-2 ①: 检查是否有 completed 事件，如有则自动 return。
 * 完成判定 = task-progress completed 事件（既有动词兼职完成信号）。
 * return 素材自动组装 = result_summary(取 completed 事件 summary) + artifact_refs(取卡分支 commit 文件清单)。
 */
// AIPOS-F10:不接收 ctx — 内部从 liveCtx 取活引用。
async function tryAutoReturn(): Promise<boolean> {
  if (!currentTaskId || !currentClient || !currentLogger) return false;
  
  const fs = await import("node:fs");
  const path = await import("node:path");
  
  // 检查是否有 completed 事件文件
  let config;
  try {
    config = loadConfig(process.env);
  } catch {
    return false;
  }

  // AIPOS-F13 大项A: settle 补型 — 兔底网先判卡是否仍在 claimed(已被门收走 → settle 歇手)
  const claimedCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${currentTaskId.toLowerCase()}.md`);
  if (!fs.existsSync(claimedCardPath)) {
    // 卡不在 claimed → 已被门收走(completed/archived) → settle 成立
    currentLogger.info("auto-return-settle-card-gone", { task_id: currentTaskId, reason: "卡已被门收编, 无需交回" });
    resetRuntimeState(`${currentTaskId} 已由门收编, 无需交回`);
    return false;
  }

  // AIPOS-F7 大项A: 兜底网读节点完成判据 — auto-return 发起前,按被持卡对应节点
  // (从卡 task_mode 判定 N2-return 或 N4-verdict)读 transitions.schema 声明的完成记录:
  // 该记录已存在 → 卡已完成 → 网静默歇手。禁 role==auditor 特例判断,一切以节点声明为准。
  // N2(execution)=return 存在即歇;N4(audit)=verdict 存在即歇。
  const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${currentTaskId.toLowerCase()}.md`);
  let isAuditCard = false;
  let cardContent = "";
  if (fs.existsSync(taskCardPath)) {
    try {
      cardContent = fs.readFileSync(taskCardPath, "utf-8");
      isAuditCard = /task_mode:\s*audit/i.test(cardContent) || /created_by:\s*gate_derivation/i.test(cardContent);
    } catch {
      // ignore read error, fall through to existing logic
    }
  }

  if (isAuditCard) {
    // N4 完成判据:verdict 记录已落库 → 审计卡已完成 → 兜底网静默歇手
    const reviewedMatch = cardContent.match(/reviewed_task_id:\s*['"]?([^'"\n]+)['"]?/i);
    const reviewedTaskId = reviewedMatch ? reviewedMatch[1].trim() : currentTaskId.replace(/R$/i, "");
    const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", reviewedTaskId);
    if (fs.existsSync(verdictDir)) {
      const vFiles = fs.readdirSync(verdictDir).filter((f: string) => f.endsWith(".md"));
      if (vFiles.length > 0) {
        currentLogger.info("auto-return-skip-audit-verdict-exists", { task_id: currentTaskId, reviewed_task_id: reviewedTaskId, verdict_count: vFiles.length });
        resetRuntimeState("审计卡已裁(verdict 已落库)歇手");
        return false;
      }
    }
    
    // AIPOS-F29 大项E: 审计车道同构托管 — 侦测审计报告(task_cards/<ID>/RETURN.md 或 audit_report.md)
    // 报告就位 → 提取裁决三值(verdict/findings/summary) → 托管 audit_verdict 提交
    const auditReportPath = path.join(config.workspaceRoot, "task_cards", currentTaskId, "RETURN.md");
    const altReportPath = path.join(config.workspaceRoot, "task_cards", currentTaskId, "audit_report.md");
    const reportPath = fs.existsSync(auditReportPath) ? auditReportPath : (fs.existsSync(altReportPath) ? altReportPath : null);
    
    if (reportPath) {
      try {
        const reportContent = fs.readFileSync(reportPath, "utf-8");
        
        // 提取裁决三值: verdict(PASS/FAIL/PASS_WITH_NOTES/BLOCK), findings, summary
        const verdictMatch = reportContent.match(/##\s*裁决[^\n]*\n+\**verdict\**:\s*(PASS|FAIL|PASS_WITH_NOTES|BLOCK)/i) ||
                             reportContent.match(/verdict:\s*(PASS|FAIL|PASS_WITH_NOTES|BLOCK)/i);
        const findingsMatch = reportContent.match(/##\s*Findings[^\n]*\n+([\s\S]+?)(?=##|$)/i) ||
                              reportContent.match(/##\s*发现[^\n]*\n+([\s\S]+?)(?=##|$)/i);
        const summaryMatch = reportContent.match(/##\s*一句话结论[^\n]*\n+([^\n#]+)/i) ||
                             reportContent.match(/##\s*Summary[^\n]*\n+([^\n#]+)/i);
        
        if (!verdictMatch) {
          const msg = `审计报告就位但缺裁决字段(verdict: PASS|FAIL|PASS_WITH_NOTES|BLOCK), 请补充后重试`;
          currentLogger.warn("auto-audit-missing-verdict", { task_id: currentTaskId, report_path: reportPath });
          voice(msg, "warn", true);
          return false;
        }
        
        const verdict = verdictMatch[1].toUpperCase();
        const findings = findingsMatch ? findingsMatch[1].trim() : "";
        const auditSummary = summaryMatch ? summaryMatch[1].trim() : `Audit ${verdict}`;
        
        // AIPOS-F35 大项B: 审计裁决托管(F29大项E补做) — 复用F33托管骨架,同一门动词
        // 审计模型写完报告即停手 → 连接器自动完成 audit_verdict 两跳(dry_run + confirm)
        // 报告缺裁决三值 → 出声带路,不提交
        
        currentLogger.info("auto-audit-from-report", {
          task_id: currentTaskId,
          reviewed_task_id: reviewedTaskId,
          verdict,
          findings_length: findings.length,
          report_path: reportPath,
        });
        
        try {
          // 提取任务卡元数据(audit_claim_id, audit_session_id等)
          const cardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${currentTaskId.toLowerCase()}.md`);
          let auditClaimId: string | undefined;
          let auditSessionId: string | undefined;
          let auditDispatchRef: string | undefined;
          let reviewedReturnRef: string | undefined;
          
          if (fs.existsSync(cardPath)) {
            try {
              const cardContent = fs.readFileSync(cardPath, "utf-8");
              const claimMatch = cardContent.match(/claim_id:\s*['"]*([^'"\n]+)['"]/i);
              const sessionMatch = cardContent.match(/active_session_id:\s*['"]*([^'"\n]+)['"]/i);
              const dispatchMatch = cardContent.match(/audit_dispatch_record_ref:\s*['"]*([^'"\n]+)['"]/i);
              const returnMatch = cardContent.match(/reviewed_return_record_ref:\s*['"]*([^'"\n]+)['"]/i);
              
              if (claimMatch) auditClaimId = claimMatch[1].trim();
              if (sessionMatch) auditSessionId = sessionMatch[1].trim();
              if (dispatchMatch) auditDispatchRef = dispatchMatch[1].trim();
              if (returnMatch) reviewedReturnRef = returnMatch[1].trim();
            } catch (e) {
              currentLogger.warn("auto-audit-read-card-failed", {
                task_id: currentTaskId,
                error: e instanceof Error ? e.message : String(e),
              });
            }
          }
          
          // 调用 lybra_audit_verdict_dry_run(复用F33托管骨架)
          const dryRunResp = await currentClient.callTool("lybra_audit_verdict_dry_run", {
            audit_task_id: currentTaskId,
            reviewed_task_id: reviewedTaskId,
            actor: config.actor,
            agent_instance: config.agentInstance,
            owner_policy_ref: config.ownerPolicyRef,
            audit_claim_id: auditClaimId,
            audit_session_id: auditSessionId || getSessionId(),
            audit_dispatch_record_ref: auditDispatchRef,
            reviewed_return_record_ref: reviewedReturnRef,
            verdict,
            findings_summary: findings || auditSummary,
            evidence_refs: [],
          });
          
          // 检查dry_run响应(对齐return托管的拒因处理)
          if (dryRunResp.verdict === "BLOCK" || dryRunResp.isError === true) {
            const reasons = dryRunResp.blocking_reasons || dryRunResp.errors || [];
            const reasonText = stringifyReasons(reasons);
            const severity = (dryRunResp as any).severity || "needs_human";
            const level = severityToLevel(severity);
            const onScreenMsg = `auto-audit-verdict ${currentTaskId} 被拒: ${reasonText}`;
            currentLogger.error("auto-audit-verdict-blocked", { task_id: currentTaskId, reasons, severity, level });
            voice(onScreenMsg, level, true);
            voice(`下一步: 请检查拒因并修正后重试`, "warn", false);
            return false;
          }
          
          const dryRunToken = dryRunResp.dry_run_token;
          if (!dryRunToken) {
            const msg = `auto-audit-verdict ${currentTaskId}: 无 dry_run_token(响应异常)`;
            currentLogger.error("auto-audit-verdict-no-token", { task_id: currentTaskId, response: dryRunResp });
            voice(msg, "error", true);
            return false;
          }
          
          // 调用 lybra_audit_verdict_confirm(AIPOS-328: auditor自确认)
          const confirmResp = await currentClient.callTool("lybra_audit_verdict_confirm", {
            dry_run_token: String(dryRunToken),
            actor: config.actor,
            agent_instance: config.agentInstance,
            owner_policy_ref: config.ownerPolicyRef,
            owner_confirmation_token: "OWNER_CONFIRMED",
          });
          
          currentLogger.info("auto-audit-verdict-success", {
            task_id: currentTaskId,
            reviewed_task_id: reviewedTaskId,
            verdict,
            confirm_verdict: confirmResp.verdict || confirmResp.ok ? "ok" : "unknown",
          });
          
          voice(`自动提交审计裁决 ${currentTaskId} → ${verdict}`, "info", true);
          
          // 审计裁决成功 → 清空任务信息,歇手
          resetRuntimeState("auto-audit-verdict 成功");
          return true;
          
        } catch (e) {
          const errMsg = e instanceof Error ? e.message : String(e);
          currentLogger.error("auto-audit-verdict-failed", {
            task_id: currentTaskId,
            reviewed_task_id: reviewedTaskId,
            error: errMsg,
          });
          voice(`auto-audit-verdict ${currentTaskId} 失败: ${errMsg}`, "error", true);
          voice(`下一步: 请检查错误并重试`, "warn", false);
          return false;
        }
        
      } catch (e) {
        currentLogger.error("auto-audit-read-report-failed", {
          task_id: currentTaskId,
          error: e instanceof Error ? e.message : String(e),
        });
        return false;
      }
    }
    
    // 审计卡无 verdict 且无报告 → 还在执行中,不走 auto-return(审计卡完成判据=N4,不走 N2 return)
    currentLogger.info("auto-return-skip-audit-no-verdict-yet", { task_id: currentTaskId, reviewed_task_id: reviewedTaskId });
    return false;
  }

  // N2 完成判据:return 记录已落库 → 执行卡已完成 → 兜底网静默歇手
  // AIPOS-F4 大项D: 兜底网让路 — auto-return 发起前先查 returns/ 已有记录 → 静默跳过。
  // 本任务已有 return 记录(上轮已交回)则不再重复发起, 也不出声(避免红错刷屏)。
  const returnsDir = path.join(config.workspaceRoot, "5_tasks/records/returns", currentTaskId);
  if (fs.existsSync(returnsDir)) {
    const returnFiles = fs.readdirSync(returnsDir).filter((f) => f.startsWith("return_") && f.endsWith(".md"));
    if (returnFiles.length > 0) {
      currentLogger.info("auto-return-skip-already-returned", { task_id: currentTaskId, count: returnFiles.length });
      resetRuntimeState("已交回(returns 已有记录)歇手");
      return false;
    }
  }

  // AIPOS-F29 大项A: 优先侦测 RETURN.md(治理工作区 task_cards/<ID>/RETURN.md)
  // 存在 → 从"一句话结论"节提取 summary, 托管交回(模型职责终点=写完 RETURN.md);
  // 不存在 → 回退兜底网(completed 事件)。
  let summary = "";
  let returnMdExists = false;
  const returnMdPath = path.join(config.workspaceRoot, "task_cards", currentTaskId, "RETURN.md");
  
  if (fs.existsSync(returnMdPath)) {
    returnMdExists = true;
    try {
      const returnContent = fs.readFileSync(returnMdPath, "utf-8");
      // 提取"一句话结论"节(## 一句话结论 或 ## 一句话结论/## Summary 等变体)
      const conclusionMatch = returnContent.match(/##\s*一句话结论[^\n]*\n+([^\n#]+)/i) ||
                              returnContent.match(/##\s*Summary[^\n]*\n+([^\n#]+)/i) ||
                              returnContent.match(/##\s*结论[^\n]*\n+([^\n#]+)/i);
      if (conclusionMatch) {
        summary = conclusionMatch[1].trim();
      } else {
        // 缺"一句话结论"节 → 出声带路,不瞎填
        const msg = `RETURN.md 就位但缺"一句话结论"节, 请补充后重试(## 一句话结论)`;
        currentLogger.warn("auto-return-missing-conclusion", { task_id: currentTaskId, return_md_path: returnMdPath });
        voice(msg, "warn", true);
        return false;
      }
      currentLogger.info("auto-return-from-return-md", { task_id: currentTaskId, summary, return_md_path: returnMdPath });
    } catch (e) {
      currentLogger.error("auto-return-read-return-md-failed", {
        task_id: currentTaskId,
        error: e instanceof Error ? e.message : String(e),
      });
      return false;
    }
  } else {
    // 回退兜底网: completed 事件
    const eventsDir = path.join(config.workspaceRoot, "5_tasks/records/events", currentTaskId);
    if (!fs.existsSync(eventsDir)) return false;
    
    // 查找 completed_*.md 文件
    const files = fs.readdirSync(eventsDir);
    const completedFiles = files.filter((f) => f.startsWith("completed_") && f.endsWith(".md"));
    if (completedFiles.length === 0) return false;
    
    // 读取最新的 completed 事件
    completedFiles.sort();
    const latestCompleted = path.join(eventsDir, completedFiles[completedFiles.length - 1]);
    const eventContent = fs.readFileSync(latestCompleted, "utf-8");
    
    // 从 frontmatter 提取 summary
    const summaryMatch = eventContent.match(/^summary:\s*['"]?([^'"\n]+)['"]?$/m);
    if (summaryMatch) {
      summary = summaryMatch[1].trim();
    }
    currentLogger.info("auto-return-from-completed-event", { task_id: currentTaskId, summary });
  }
  
  // 从 worktree git log 提取 artifact_refs（最近一次 commit 的文件列表）
  let artifactRefs: string[] = [];
  if (currentWorktreePath && fs.existsSync(currentWorktreePath)) {
    try {
      const { execSync } = await import("node:child_process");
      // 获取最近一次 commit 的文件列表
      const gitOutput = execSync("git diff-tree --no-commit-id --name-only -r HEAD", {
        cwd: currentWorktreePath,
        encoding: "utf-8",
      });
      artifactRefs = gitOutput.trim().split("\n").filter((f) => f.trim());
    } catch (e) {
      currentLogger.warn("auto-return-git-failed", {
        task_id: currentTaskId,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }
  
  // 调用 return_dry_run
  currentLogger.info("auto-return-start", {
    task_id: currentTaskId,
    summary: summary || "(no summary)",
    artifact_count: artifactRefs.length,
  });
  
  try {
    const dryRunResp = await currentClient.callTool("lybra_queue_return_dry_run", {
      task_id: currentTaskId,
      actor: config.actor,
      agent_instance: config.agentInstance,
      autonomy_mode: "Supervised",
      owner_policy_ref: config.ownerPolicyRef,
      result_summary: summary || "Task completed (auto-return)",
      artifact_refs: artifactRefs.length > 0 ? artifactRefs : undefined,
      active_session_id: getSessionId(),
    });
    
    // AIPOS-F4 大项C/D: dry_run 被拒时拒因上屏(声明→级别映射, 禁现场定级)。
    // [object Object] 修复: 用 stringifyReasons 逐项取 message, 不再裸 join 对象数组。
    // AIPOS-F34 大项B: 带路语禁撒谎——被拒时不声称"已交回", 报真因+真下一步。
    if (dryRunResp.verdict === "BLOCK" || dryRunResp.isError === true) {
      const reasons = dryRunResp.blocking_reasons || dryRunResp.errors || [];
      const reasonText = stringifyReasons(reasons);
      const severity = (dryRunResp as any).severity || "needs_human";
      const level = severityToLevel(severity);
      const onScreenMsg = `auto-return ${currentTaskId} 被拒: ${reasonText}`;
      currentLogger.error("auto-return-blocked", { task_id: currentTaskId, reasons, severity, level });
      voice(onScreenMsg, level, true);
      // AIPOS-F34 大项B: 先查 F2 单源(returns/)再出声, 无记录则报真因+真下一步
      const f2ReturnsDir = path.join(config.workspaceRoot, "5_tasks/records/returns", currentTaskId);
      const hasReturnRecords = fs.existsSync(f2ReturnsDir) &&
        fs.readdirSync(f2ReturnsDir).filter((f) => f.startsWith("return_") && f.endsWith(".md")).length > 0;
      if (hasReturnRecords) {
        voice(`任务已由本工位交回(returns 有记录), 无需处理`, "info", false);
      } else {
        voice(`下一步: 请检查拒因并修正后重试, 或走 /lybra return 手动交回`, "warn", false);
      }
      return false;
    }

    const dryRunToken = dryRunResp.dry_run_token;
    if (!dryRunToken) {
      const msg = `auto-return ${currentTaskId}: 无 dry_run_token(响应异常)`;
      currentLogger.error("auto-return-no-token", { task_id: currentTaskId, response: dryRunResp });
      voice(msg, "error", true);
      return false;
    }
    
    // AIPOS-328: executor 自确认 return (owner_confirmation_token = OWNER_CONFIRMED 字面量)
    const confirmResp = await currentClient.callTool("lybra_queue_return_confirm", {
      dry_run_token: String(dryRunToken),
      actor: config.actor,
      agent_instance: config.agentInstance,
      owner_policy_ref: config.ownerPolicyRef,
      owner_confirmation_token: "OWNER_CONFIRMED",
    });
    
    currentLogger.info("auto-return-success", {
      task_id: currentTaskId,
      verdict: confirmResp.verdict || confirmResp.ok ? "ok" : "unknown",
    });
    
    voice(`自动归还任务 ${currentTaskId}`, "info", true);
    
    // AIPOS-F11 大项C: 清空当前任务信息(单源复位函数)
    resetRuntimeState("auto-return 成功");
    
    return true;
  } catch (e) {
    const errMsg = e instanceof Error ? e.message : String(e);
    currentLogger.error("auto-return-failed", {
      task_id: currentTaskId,
      error: errMsg,
    });
    // AIPOS-F34 大项B: 带路语禁撒谎——失败时报真因+真下一步, 不谎称"已交回"
    voice(`auto-return ${currentTaskId} 失败: ${errMsg}`, "error", true);
    const f2ReturnsDirCatch = path.join(config.workspaceRoot, "5_tasks/records/returns", currentTaskId);
    const hasReturnRecordsCatch = fs.existsSync(f2ReturnsDirCatch) &&
      fs.readdirSync(f2ReturnsDirCatch).filter((f) => f.startsWith("return_") && f.endsWith(".md")).length > 0;
    if (hasReturnRecordsCatch) {
      voice(`任务已由本工位交回(returns 有记录), 无需处理`, "info", false);
    } else {
      voice(`下一步: 请检查错误并重试, 或走 /lybra return 手动交回`, "warn", false);
    }
    return false;
  }
}

// AIPOS-F11 大项C: 运行态复位单源 — currentTaskId/currentWorktreePath 的清零只此一处。
// 凡 settle 判定成立(含全部歇手分支)与 loop-on 启动皆调用; 歇手分支复位时出声卡号+复位原因。
// 禁在各分支各写一份清零(此前只在 auto-return 成功路径一处, 歇手分支不清 → 周期 sweep 永久误判
// "有卡在跑"让路, 收账全停且零出声 —— 2026-08-20 实撞)。
function resetRuntimeState(reason: string): void {
  if (!currentTaskId && !currentWorktreePath) return; // 无残留, 静默(loop-on 启动时常见)
  const runningCard = currentTaskId ?? "(未知卡号)";
  currentTaskId = null;
  currentWorktreePath = null;
  currentLogger?.info("runtime-state-reset", { task_id: runningCard, reason });
  voice(`运行态复位: ${runningCard} — ${reason}`, "info", false);
}

// AIPOS-F10:stopLoop 不接收 ctx 参数 — 从 liveCtx 取活引用。
function stopLoop(
  reason: string,
  level: "info" | "warn" | "error" = "warn",
) {
  loopState.on = false;
  loopState.stoppedReason = reason;
  clearTimer();
  cooldownAnnounced = false; // AIPOS-F16: 循环停即复位余热出声门门(下次 on 重新转入才再出声)
  currentLogger?.write(level === "error" ? "ERROR" : level === "warn" ? "WARN" : "INFO", "loop-stopped", { reason });
  voice(`lybra 循环停止:${reason}`, level, true);
}

// AIPOS-F10:getSessionId 从 liveCtx 取,不接收外部 ctx 参数。
function getSessionId(): string {
  try {
    const id = liveCtx?.sessionManager?.getSessionId?.();
    return id ? String(id) : "lybra-loop-session";
  } catch {
    return "lybra-loop-session";
  }
}

// AIPOS-F10:doTick 不接收 pi/ctx 参数 — 从 livePi/liveCtx 取当前活引用。
// 根治三病象:①定时器闭包 stale ctx(newSession 摔) ②/reload 后 stale ctx ③session_start 传 event 非 ctx(reply 摔)。
async function doTick(): Promise<void> {
  if (!loopState.on) return;
  if (!currentClient || !currentLogger) {
    stopLoop("内部状态缺失(client/logger),请重新 /lybra on", "error");
    return;
  }
  if (!liveCtx) {
    stopLoop("liveCtx 未初始化(无活会话),请重新 /lybra on", "error");
    return;
  }
  let config;
  try {
    config = loadConfig(process.env);
  } catch (e) {
    stopLoop(`配置失效:${e instanceof Error ? e.message : String(e)}`, "error");
    return;
  }
  loopState.running = true;
  try {
    // AIPOS-F19: 周期 tick 低频复查水位(每 N tick, N=schema#watermark.periodic_tick_interval)。
    // 只读只喊: 越阈出声(persistent)带路, 不停循环不代删(决策留人)。
    watermarkTickCounter += 1;
    if (isWatermarkTickDue()) {
      try {
        runWatermarkCheck(config.workspaceRoot, "tick");
      } catch (e) {
        currentLogger?.warn("watermark-tick-error", { error: e instanceof Error ? e.message : String(e) });
      }
    }
    // AIPOS-C3D 大项A: 轮询内周期 sweep — 每轮 tick 前尝试一次收账。
    // 已在跑卡(currentTaskId 非空 = released 未 settle)期间不 sweep, 避免干扰当前会话。
    if (!currentTaskId) {
      await tryAutoFinalizeOnPassVerdict().catch((e) => {
        currentLogger?.warn("tick-sweep-error", {
          error: e instanceof Error ? e.message : String(e),
        });
      });
    }
    const outcome = await executeTick({
      client: currentClient,
      actor: config.actor,
      agentInstance: config.agentInstance,
      ownerPolicyRef: config.ownerPolicyRef,
      workspaceRoot: config.workspaceRoot,
      activeSessionId: getSessionId(),
      state: loopState,
      logger: currentLogger,
    });

    if (outcome.kind === "release") {
      loopState.released += 1;
      // AIPOS-CONN-LOOP-2 ①: 记录当前任务信息，用于自动return
      currentTaskId = String(outcome.task.task_id);
      // worktree路径从任务元数据中获取
      const taskMeta = (outcome.task as { metadata?: unknown }).metadata;
      const meta = taskMeta && typeof taskMeta === "object" ? (taskMeta as Record<string, unknown>) : {};
      currentWorktreePath = String(meta.active_worktree_path || "") || null;
      currentLogger.info("release", {
        task_id: outcome.task.task_id,
        card: outcome.cardAbsPath,
        policy: outcome.policyId || "?",
        released: loopState.released,
        maxN: loopState.maxN,
        worktree: currentWorktreePath,
      });
      if (loopState.released >= loopState.maxN) {
        currentLogger.info("release-last", { task_id: outcome.task.task_id });
      }
      // AIPOS-F15 大项B: 统一走 voice() 单出口,禁另造第二条出声路
      voice(
        `放行 ${outcome.task.task_id}(PreAuthorized)→ 冷启动执行 [${loopState.released}/${loopState.maxN}]`,
        "info",
        true,
      );
      
      // AIPOS-F36 大项A: 投递前判断 ctx 就绪 — liveCtx.newSession 不可用时不投递不停循环,下一 tick 重试
      if (!liveCtx || typeof liveCtx.newSession !== "function") {
        currentLogger.warn("release-ctx-not-ready", {
          task_id: outcome.task.task_id,
          reason: "liveCtx.newSession 不可用(ctx 未就绪)",
        });
        // AIPOS-F36 大项C: 话术与行为一致 — 称重试则必重试(不停循环,下一 tick 重投)
        voice(`放行 ${outcome.task.task_id} 待投递: ctx 未就绪，稍后循环会自动重试`, "warn", false);
        loopState.running = false;
        scheduleNextTick(1000); // 1秒后重试
        return;
      }
      
      // F-EXT001-8(FIX4):running 标志复位前置到 newSession 之前,确保任何路径(含 stale 异常)下可达
      loopState.running = false;
      expectingSwap = true;
      const kickoff = buildKickoff(outcome.cardAbsPath);
      try {
        // AIPOS-F10:用 liveCtx.newSession(当前活 ctx),对齐 claim.ts 范式:
        // withSession 回调里只用 freshCtx,绝不捕获旧 ctx。
        const result = await liveCtx.newSession({
          withSession: async (freshCtx) => {
            await freshCtx.sendUserMessage(kickoff);
          },
        });
        if (result?.cancelled) {
          expectingSwap = false;
          stopLoop("newSession 被拦截(循环停)", "warn");
        }
      } catch (e) {
        // F-EXT001-8(FIX4):newSession 异常(stale ctx 等)不再静默吞掉,落日志
        expectingSwap = false;
        currentLogger.warn("release-newSession-error", {
          task_id: outcome.task.task_id,
          error: e instanceof Error ? e.message : String(e),
        });
        // AIPOS-F36 大项B: ctx 异常属可恢复态 — 不停循环(下一 tick 会重试),只出声
        // AIPOS-F36 大项C: 话术与行为一致
        const errMsg = e instanceof Error ? e.message : String(e);
        voice(`放行 ${outcome.task.task_id} 投递异常: ${errMsg}，稍后循环会自动重试`, "warn", true);
        loopState.running = false;
        scheduleNextTick(2000); // 2秒后重试
      }
      return; // session 已替换或待重试;续跑靠新 session 的 agent_settled + session_start 双保险
    }

    if (outcome.kind === "stop") {
      // AIPOS-R6L 大项A①: held分支先查completed事件，有→走return，没有→才stop
      // 冷会话可接管：以records为准，不依赖内存currentTaskId
      // 从outcome.reason提取task_id（格式："已持有 TASK-ID — ..."）
      const heldMatch = outcome.reason.match(/已持有\s+([A-Z0-9-]+)/);
      if (heldMatch) {
        const heldTaskId = heldMatch[1];
        const fs = await import("node:fs");
        const path = await import("node:path");
        
        // AIPOS-F13 大项A: settle 补型 — held 分支先判卡是否仍在 claimed(已被门收走 → settle 歇手)
        const heldCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${heldTaskId.toLowerCase()}.md`);
        if (!fs.existsSync(heldCardPath)) {
          // 卡不在 claimed → 已被门收走(completed/archived) → settle 成立
          currentLogger.info("held-settle-card-gone", { task_id: heldTaskId, reason: "卡已被门收编, 无需交回" });
          resetRuntimeState(`${heldTaskId} 已由门收编, 无需交回`);
          // settle 后继续轮询下一张卡(如未达 maxN)
          // AIPOS-F16: 额度尽时也不再就此沉默 — 下一 tick 自判余热(executeTick 只判不领)
          loopState.running = false;
          scheduleNextTick(1000);
          return;
        }
        
        // 检查是否有 completed 事件
        const eventsDir = path.join(config.workspaceRoot, "5_tasks/records/events", heldTaskId);
        let hasCompleted = false;
        if (fs.existsSync(eventsDir)) {
          const files = fs.readdirSync(eventsDir);
          hasCompleted = files.some((f) => f.startsWith("completed_") && f.endsWith(".md"));
        }
        
        if (hasCompleted) {
          // 有completed事件，走return流程
          currentLogger.info("held-with-completed", { task_id: heldTaskId });
          voice(`检测到 ${heldTaskId} 已completed，执行自动return`, "info", false);
          
          // 设置 currentTaskId 供tryAutoReturn使用
          currentTaskId = heldTaskId;
          
          // 查找 worktree 路径（从任务卡读取）
          const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${heldTaskId.toLowerCase()}.md`);
          if (fs.existsSync(taskCardPath)) {
            try {
              const cardContent = fs.readFileSync(taskCardPath, "utf-8");
              const worktreeMatch = cardContent.match(/active_worktree_path:\s*['"]?([^'"\n]+)['"]?/i);
              if (worktreeMatch) {
                currentWorktreePath = worktreeMatch[1].trim();
              }
            } catch (e) {
              currentLogger.warn("held-read-card-failed", { task_id: heldTaskId, error: String(e) });
            }
          }
          
          // 调用 tryAutoReturn
          const returned = await tryAutoReturn();
          if (returned) {
            // return成功，继续轮询下一张卡
            // AIPOS-F16: 额度尽时也不再就此沉默 — 下一 tick 自判余热(executeTick 只判不领)
            loopState.running = false; // 确保复位
            scheduleNextTick(1000); // 1秒后再拉
            return;
          } else {
            // return失败，记录错误但不停循环
            currentLogger.warn("held-auto-return-failed", { task_id: heldTaskId });
            voice(`自动return ${heldTaskId} 失败，请手动处理`, "warn", true);
          }
        } else {
          // AIPOS-C3B 大项D①: held-resume 罩审计车道
          // 审计卡(task_mode=audit)的完成判据 = verdict 记录是否落库(N4)
          // 审计卡 claimed + 报告在 + 无 verdict → 复工提示"只差提交裁决"
          const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${heldTaskId.toLowerCase()}.md`);
          let isAuditCard = false;
          let cardContent = "";
          if (fs.existsSync(taskCardPath)) {
            try {
              cardContent = fs.readFileSync(taskCardPath, "utf-8");
              isAuditCard = /task_mode:\s*audit/i.test(cardContent) || /created_by:\s*gate_derivation/i.test(cardContent);
            } catch {
              // ignore
            }
          }

          if (isAuditCard) {
            // 审计车道: 检查 verdict 记录是否已落库
            const reviewedMatch = cardContent.match(/reviewed_task_id:\s*['"]?([^'"\n]+)['"]?/i);
            const reviewedTaskId = reviewedMatch ? reviewedMatch[1].trim() : heldTaskId.replace(/R$/i, "");
            const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", reviewedTaskId);
            let hasVerdict = false;
            if (fs.existsSync(verdictDir)) {
              const vFiles = fs.readdirSync(verdictDir).filter((f: string) => f.endsWith(".md"));
              hasVerdict = vFiles.length > 0;
            }
            if (!hasVerdict) {
              currentLogger.info("held-audit-no-verdict", { task_id: heldTaskId, reviewed_task_id: reviewedTaskId });
              voice(`复工：${heldTaskId} 是审计卡，只差提交裁决(verdict 未落库)`, "info", true);
              // AIPOS-F35 大项A: 审计车道冷启动修真 — 用 liveCtx.newSession (F10 范式),
              // 不用 sendUserMessage(实撞 F34R: "liveCtx.newSession is not a function")。
              // 对齐 claim.ts + release 路径: withSession 回调内只用 freshCtx。
              
              // AIPOS-F36 大项A: 投递前判断 ctx 就绪 — liveCtx.newSession 不可用时不投递不停循环,下一 tick 重试
              if (!liveCtx || typeof liveCtx.newSession !== "function") {
                currentLogger.warn("held-audit-ctx-not-ready", {
                  task_id: heldTaskId,
                  reason: "liveCtx.newSession 不可用(ctx 未就绪)",
                });
                // AIPOS-F36 大项C: 话术与行为一致 — 称重试则必重试(不停循环,下一 tick 重投)
                voice(`复工审计卡 ${heldTaskId} 待投递: ctx 未就绪，稍后循环会自动重试`, "warn", false);
                loopState.running = false;
                scheduleNextTick(1000); // 1秒后重试
                return;
              }
              
              if (cardContent) {
                try {
                  currentTaskId = heldTaskId; // 复工前设置(投递可能异常,但 currentTaskId 需对齐)
                  loopState.running = false; // newSession 前复位 running
                  expectingSwap = true; // 标记预期会话更替
                  const auditKickoff = `# 复工任务(审计车道): ${heldTaskId}\n\n审计卡 claimed + 无 verdict 记录 → 只差提交裁决。\n\n${cardContent}`;
                  const result = await liveCtx.newSession({
                    withSession: async (freshCtx) => {
                      await freshCtx.sendUserMessage(auditKickoff);
                    },
                  });
                  if (result?.cancelled) {
                    expectingSwap = false;
                    stopLoop("newSession 被拦截(审计复工取消)", "warn");
                  } else {
                    // 成功投递 → 循环停,等新 session 的 agent_settled 续跑
                    stopLoop(`已复工审计卡 ${heldTaskId}，等待提交裁决`, "info");
                  }
                  return;
                } catch (newSessionErr) {
                  // AIPOS-F36 大项B: ctx 异常属可恢复态 — 不停循环(下一 tick 会重试),只出声
                  // AIPOS-F36 大项C: 话术与行为一致
                  expectingSwap = false;
                  const errMsg = newSessionErr instanceof Error ? newSessionErr.message : String(newSessionErr);
                  currentLogger.warn("held-audit-newSession-failed", {
                    task_id: heldTaskId,
                    error: errMsg,
                  });
                  voice(`复工审计卡 ${heldTaskId} 投递异常: ${errMsg}，稍后循环会自动重试`, "warn", true);
                  loopState.running = false;
                  scheduleNextTick(2000); // 2秒后重试
                  return;
                }
              }
            } else {
              currentLogger.info("held-audit-has-verdict", { task_id: heldTaskId });
              voice(`审计卡 ${heldTaskId} 已有 verdict，无需复工`, "info", false);
            }
          }

          // AIPOS-R8B 大项C①: held 且无 completed → 投递卡正文复工（会话中断后自动续做）
          currentLogger.info("held-resume", { task_id: heldTaskId });
          voice(`复工：继续执行 ${heldTaskId}`, "info", true);
          
          // 读取任务卡正文并投递
          if (fs.existsSync(taskCardPath)) {
            try {
              if (!cardContent) cardContent = fs.readFileSync(taskCardPath, "utf-8");
              // AIPOS-C3D 大项B: 复工附带裁决 — 若该卡存在门生裁决(N4 声明位置), 随卡正文投递最新裁决全文
              let resumeText = `# 复工任务: ${heldTaskId}\n\n${cardContent}`;
              const verdictDir = path.join(config.workspaceRoot, "5_tasks/records/audit_verdicts", heldTaskId);
              if (fs.existsSync(verdictDir)) {
                const latest = selectLatestGateVerdict(fs, verdictDir, currentLogger, heldTaskId);
                if (latest) {
                  let verdictFull = "";
                  try {
                    verdictFull = fs.readFileSync(latest.filePath, "utf-8");
                  } catch {
                    verdictFull = `(读取裁决文件失败: ${latest.filePath})`;
                  }
                  const kindLabel =
                    latest.verdict === "FAIL"
                      ? "FAIL 整改复工"
                      : latest.verdict === "PASS" || latest.verdict === "PASS_WITH_NOTES"
                        ? "PASS 待收尾"
                        : `裁决 ${latest.verdict}`;
                  resumeText =
                    `# 复工任务: ${heldTaskId}(此为 ${kindLabel})\n\n` +
                    `> 带最新门生裁决全文(${latest.verdictId}, verdict_at=${latest.verdictAt})。\n` +
                    `> findings 以顾问核定为准, 有驳回顾问会另行说明。\n\n` +
                    `--- 最新门生裁决全文 ---\n\n${verdictFull}\n\n` +
                    `--- 任务卡正文 ---\n\n${cardContent}`;
                  currentLogger.info("held-resume-with-verdict", {
                    task_id: heldTaskId,
                    verdict: latest.verdict,
                    verdict_id: latest.verdictId,
                  });
                }
              }
              // AIPOS-F10:投递卡内容到当前会话。用 sendUserMessage(对齐 claim.ts 范式),
              // 不用 ctx.reply(不存在的方法 —— 病象③的根因)。
              // AIPOS-F29B 大项B: 复工投递回归修复 - F26D 修过的投递 API
              // (确保 liveCtx 可用且调用正确的 API)
              
              // AIPOS-F36 大项A: 投递前判断 ctx 就绪 — liveCtx.sendUserMessage 不可用时不投递不停循环,下一 tick 重试
              if (!liveCtx || typeof liveCtx.sendUserMessage !== "function") {
                currentLogger.warn("held-resume-ctx-not-ready", {
                  task_id: heldTaskId,
                  reason: "liveCtx.sendUserMessage 不可用(ctx 未就绪)",
                });
                // AIPOS-F36 大项C: 话术与行为一致 — 称重试则必重试(不停循环,下一 tick 重投)
                voice(`复工 ${heldTaskId} 待投递: ctx 未就绪，稍后循环会自动重试`, "warn", false);
                loopState.running = false;
                scheduleNextTick(1000); // 1秒后重试
                return;
              }
              
              try {
                await liveCtx.sendUserMessage(resumeText);
                // AIPOS-F26 大项E: 投递成功后才设置 currentTaskId/worktree 和 stopLoop("已复工")
                currentTaskId = heldTaskId;
                // 提取 worktree 路径
                const worktreeMatch = cardContent.match(/active_worktree_path:\s*['"]?([^'"\n]+)['"]?/i);
                if (worktreeMatch) {
                  currentWorktreePath = worktreeMatch[1].trim();
                }
                // 停止循环，让执行体在当前会话继续工作
                stopLoop(`已复工 ${heldTaskId}，继续在当前会话执行`, "info");
                return;
              } catch (sendErr) {
                // AIPOS-F36 大项B: ctx 异常属可恢复态 — 不停循环(下一 tick 会重试),只出声
                // AIPOS-F36 大项C: 话术与行为一致
                currentLogger.warn("held-resume-sendUserMessage-failed", {
                  task_id: heldTaskId,
                  error: sendErr instanceof Error ? sendErr.message : String(sendErr),
                });
                voice(`复工 ${heldTaskId} 投递异常: ${sendErr instanceof Error ? sendErr.message : String(sendErr)}，稍后循环会自动重试`, "warn", true);
                loopState.running = false;
                scheduleNextTick(2000); // 2秒后重试
                return;
              }
            } catch (e) {
              currentLogger.error("held-resume-failed", { task_id: heldTaskId, error: String(e) });
              voice(`复工失败: ${e instanceof Error ? e.message : String(e)}`, "error", true);
            }
          } else {
            currentLogger.warn("held-resume-no-card", { task_id: heldTaskId, expected_path: taskCardPath });
            voice(`任务卡不存在: ${taskCardPath}`, "warn", true);
          }
        }
      }
      
      // 没有completed事件，或return失败，按原逻辑stop
      stopLoop(outcome.reason, "warn");
      return;
    }

    // AIPOS-F16 余热 tick 收口: 额度尽(executeTick 只判不领)→ 只剩收尾判定。
    // sweep 收账已在 tick 前跑过(上丈 !currentTaskId 分支), held 复工网在 stop 分支照常可达;
    // 此处只判: queue/claimed 无本工位在途卡 → 终停(停语带路); 否则按 interval 继续余热。
    if (outcome.kind === "cooldown") {
      const fs = await import("node:fs");
      const path = await import("node:path");
      const inFlight = findInFlightCards(fs, path, config.workspaceRoot, config.agentInstance);
      const plan = planCooldownStep(inFlight, loopState.released, loopState.maxN, config.intervalSec);
      currentLogger.info("cooldown-step", { in_flight: inFlight, plan: plan.action, reason: outcome.reason });
      if (plan.action === "terminal-stop") {
        stopLoop(plan.reason, "info");
        voice(`下一步: 如需继续领卡请 /lybra on N`, "info", false);
        return;
      }
      voice(plan.voiceLine, "info", false);
      loopState.running = false;
      scheduleNextTick(plan.nextMs);
      return;
    }

    // wait:轮询
    const elapsed = Date.now() - (loopState as { cycleStartMs: number }).cycleStartMs;
    if (elapsed >= config.maxWaitSec * 1000) {
      stopLoop(`轮询超时(${config.maxWaitSec}s)无信封内可认领卡`, "info");
      return;
    }
    const remain = config.maxWaitSec * 1000 - elapsed;
    const nextMs = Math.min(config.intervalSec * 1000, remain);
    // AIPOS-R6L 大项A③: 非有限值兜底 - 防配置错误导致 NaN 空转
    if (!Number.isFinite(nextMs) || nextMs <= 0) {
      const msg = `轮询间隔非法(nextMs=${nextMs}, intervalSec=${config.intervalSec}) - 停止循环`;
      currentLogger.error("invalid-poll-interval", { nextMs, intervalSec: config.intervalSec, remain });
      voice(msg, "error", true);
      stopLoop(msg, "error");
      return;
    }
    currentLogger.info("wait-poll", { reason: outcome.reason, nextMs });
    // AIPOS-R6I 靶③: 轮询结果可见 - 打印等待原因
    voice(`轮询: ${outcome.reason}，${Math.round(nextMs / 1000)}s 后再拉`, "info", false);
    // F-EXT001-8(FIX4):wait 路径复位 running,防定时器触发前 running 一直 true 拦截其他入口
    loopState.running = false;
    scheduleNextTick(nextMs);
  } catch (e) {
    stopLoop(`tick 异常:${e instanceof Error ? e.message : String(e)}`, "error");
  } finally {
    // F-EXT001-8(FIX4):release 路径已前置复位 running;finally 保底兜所有其他路径
    loopState.running = false;
  }
}

export default function (pi: ExtensionAPI) {
  // AIPOS-F10:每次 session 装载立即刷新 livePi — 定时器/钩子从模块级引用取,不闭包捕获。
  livePi = pi;

  // AIPOS-F15B: 注册 voice journal entry renderer — 关键事件在对话记录中可见(不入 LLM 上下文)。
  // 渲染为对话记录中的一行: 图标 + 时间 + 文本(对齐 pi 官方 entry-renderer 示例的 Box/Text 范式)。
  try {
    pi.registerEntryRenderer("lybra-voice", (entry: any, _opts: any, theme: any) => {
      const data = entry?.data ?? {};
      const levelTag = data.level === "error" ? "🔴" : data.level === "warn" ? "🟡" : "🟢";
      const ts = data.timestamp || "";
      const text = data.text || "";
      // pi-tui 组件(官方范式):经 createRequire 从 pi 的模块链解析;
      // headless 测试环境(无 pi-tui)解析失败 → 返回 null(不渲染但 entry 仍持久存于 session)。
      try {
        const { Box, Text } = piTui();
        const box = new Box(0, 0, (t: string) => (theme?.bg ? theme.bg("customMessageBg", t) : t));
        box.addChild(new Text(`${levelTag} ${ts} ${text}`, 0, 0));
        return box;
      } catch {
        return null as any;
      }
    });
  } catch {
    // renderer 注册失败不影响主流程(journal 文件兜底)
  }

  // AIPOS-F12 大项C: pi 工具层写拦截 — 命中门领地保护路径的 write/edit/bash 写操作直接拒。
  pi.on("tool_call", async (event) => {
    try {
      if (!writeGuardCfg) refreshWriteGuard();
      if (!writeGuardCfg) return; // 配置未就绪, 无法判定保护路径, 不拦(禁误伤)
      const ev = event as any;
      const toolName = String(ev?.toolName || "");
      const targets = extractWriteTargets(toolName, ev?.input);
      if (targets.length === 0) return;
      for (const t of targets) {
        if (isProtectedWriteTarget(t, writeGuardCfg.workspaceRoot, writeGuardCfg.protectedPaths)) {
          const msg = `写保护路径被拒(门领地): ${t} — 报告落 task_cards/<ID>/, 裁决走门, 勿手写进 records/queue/`;
          currentLogger?.warn("protected-write-blocked", { tool: toolName, target: t });
          voice(msg, "warn", true);
          return { block: true, reason: msg };
        }
      }
    } catch {
      // 拦截逻辑自身异常绝不误伤工具执行(安全网自身不扩大)。
    }
  });

  // --- 续跑:maxN>1 时,新 session 的卡执行完(settle)→ 直接调 tick ---
  pi.on("agent_settled", async (_event, ctx) => {
    liveCtx = ctx; // AIPOS-F10:刷新活引用
    if (!loopState.on) return;
    
    // AIPOS-R6I 靶②: 检查是否有 PASS 裁决，如有则自动 finalize+close
    await tryAutoFinalizeOnPassVerdict().catch((e) => {
      const errMsg = `agent_settled auto-finalize 错误: ${e instanceof Error ? e.message : String(e)}`;
      currentLogger?.warn("agent_settled-auto-finalize-error", {
        error: e instanceof Error ? e.message : String(e),
      });
      // AIPOS-R6L 第三轮修复(b): 失败必出声
      voice(errMsg, "error", true);
    });
    
    // AIPOS-CONN-LOOP-2 ①: 检查是否有 completed 事件，如有则自动 return
    if (currentTaskId) {
      const returned = await tryAutoReturn();
      if (returned) {
        // return 成功后照常续跑 — 额度未尽继续领新卡;
        // AIPOS-F16: 额度尽时 doTick 自判余热(executeTick 只判不领), 不再整停循环
        if (!loopState.running) {
          doTick().catch((e) => {
            currentLogger?.warn("agent_settled-post-return-tick-error", {
              error: e instanceof Error ? e.message : String(e),
            });
          });
        }
        return;
      }
    }

    if (loopState.released >= loopState.maxN) {
      // AIPOS-F16 余热(取代旧“达到 maxN 即整停”): 额度只管新领卡, 不陪绑在途卡的收账与复工。
      // 转入余热: 循环不停, 不再领新卡; sweep 收账 + held 复工网照常, 在途卡收口才终停。
      // /lybra off 仍即时停(用户显式优先, off handler 不经此路径)。
      currentLogger?.info("cooldown-enter", { released: loopState.released, maxN: loopState.maxN });
      if (!cooldownAnnounced) {
        cooldownAnnounced = true;
        voice(`额度已用完(${loopState.released}/${loopState.maxN}), 余热收尾中 — 不再领新卡, 在途卡收完即停`, "info", true);
      }
      if (!loopState.running) {
        doTick().catch((e) => {
          currentLogger?.warn("agent_settled-cooldown-tick-error", {
            error: e instanceof Error ? e.message : String(e),
          });
        });
      }
      return;
    }
    if (loopState.running) return; // 防重入
    // F-EXT001-4(FIX1):直接调用,不经 sendUserMessage
    doTick().catch((e) => {
      // F-EXT001-8(FIX4):不再静默吞错,落日志
      currentLogger?.warn("agent_settled-doTick-error", {
        error: e instanceof Error ? e.message : String(e),
      });
    });
  });

  // --- session 生命周期:reload 不断;用户 /new|resume|fork|quit 停;循环自驱 newSession 不停 ---
  pi.on("session_shutdown", async (event, ctx) => {
    liveCtx = ctx; // AIPOS-F10:刷新活引用(虽然即将关闭,但 stopLoop 可能用到)
    if (expectingSwap) {
      // 循环自己触发的 newSession:清 flag + 定时器,但保留循环状态供新 session 续跑
      expectingSwap = false;
      clearTimer();
      return;
    }
    // AIPOS-F10:reload 也清定时器 — 旧定时器闭包的 ctx/pi 即将 stale,
    // 由 session_start 的新 ctx 重新调度。根治病象②。
    if (event.reason === "reload") {
      clearTimer();
      return; // /reload:循环继续(session_start 再续)
    }
    clearTimer();
    if (loopState.on) stopLoop(`session ${event.reason}(循环中断)`, "warn");
  });

  // AIPOS-F10:session_start 必须捕获第二参数 ctx(=当前活会话的 ctx)。
  // 旧代码只取 event 然后 event as any 当 ctx 用 —— 病象③的根因。
  pi.on("session_start", async (event, ctx) => {
    liveCtx = ctx; // AIPOS-F10:刷新活引用 — 新 session 的 ctx 替代旧 session 的 stale ctx
    // AIPOS-F13 大项B: session_start 刷新 ctx 后立即补发缓冲话术
    flushVoiceBuffer();
    // F-EXT001-8(FIX4):双保险机制 — reload 时续跑 + 自驱 newSession(expectingSwap)时也续跑,防 agent_settled 单点失效
    if (loopState.on && (event.reason === "reload" || expectingSwap)) {
      if (expectingSwap) {
        currentLogger?.info("session_start-swap-resume", { reason: event.reason });
        expectingSwap = false; // 清 flag,防重复触发
      }
      if (loopState.running) return; // 防重入(agent_settled 可能先到)
      // F-EXT001-4(FIX1):直接调用,不经 sendUserMessage
      doTick().catch((e) => {
        // F-EXT001-8(FIX4):不再静默吞错,落日志
        currentLogger?.warn("session_start-doTick-error", {
          reason: event.reason,
          error: e instanceof Error ? e.message : String(e),
        });
      });
    }
  });

  // --- /lybra:on | off | status ---
  pi.registerCommand("lybra", {
    description: "lybra-loop 自动领卡循环:on [maxN] | off | status | sync | enroll <码>",
    handler: async (args, ctx) => {
      liveCtx = ctx; // AIPOS-F10:命令入口刷新活引用
      const parts = String(args || "").trim().split(/\s+/);
      const sub = parts[0] || "status";

      if (sub === "off") {
        if (!loopState.on) {
          ctx.ui.notify("lybra 循环未在运行", "info");
          return;
        }
        stopLoop("用户 /lybra off", "info");
        return;
      }

      if (sub === "status") {
        // AIPOS-C4B 大项B: /lybra status 随时可查 — 版本戳 + 清单比对 + provenance 横幅
        // (复现 C2 横幅被冷启动换屏吞掉 → status 可查, 不依赖 on 瞬间的屏)
        const fp = currentTokenFp;
        const lines: string[] = [
          `lybra-loop 状态:`,
          `  运行中: ${loopState.on ? "是" : "否"}`,
          `  已放行: ${loopState.released}/${loopState.maxN}`,
        ];
        // AIPOS-F16: 额度尽且循环仍在跑 = 余热收尾中(不领新卡, 在途卡收完即停)
        if (loopState.on && loopState.released >= loopState.maxN) {
          lines.push(`  模式: 余热收尾中(额度尽, 不领新卡, 在途卡收完即停; /lybra off 可停)`);
        }
        lines.push(`  停止原因: ${loopState.stoppedReason || "(无)"}`);
        lines.push(`  gate: ${currentGateUrl || "(未配置)"}  role: ${currentRole || "-"}  actor: ${currentActor || "-"}`);
        lines.push(`  token: ${fp}`);
        // AIPOS-F15B: 关键事件回看 — voice journal 最近 10 条(收账/复工/终停/异常BLOCK)
        const recentVoice = readVoiceJournalRecent(10);
        if (recentVoice.length > 0) {
          lines.push(`  最近关键事件(voice journal, 最多 10 条):`);
          for (const v of recentVoice) {
            lines.push(`    ${v.replace(/^- /, "")}`);
          }
        } else {
          lines.push(`  最近关键事件: 暂无`);
        }
        // AIPOS-F19: 水位行常驻(检查点②/③) — 各路径 used%/free + 级别标记;
        // 状态变化时同样走降噪出声(与启动/周期同一状态机, 同路径同级别不重复喊)。
        try {
          const wmConfig = loadConfig(process.env);
          const wmStatus = runWatermarkCheck(wmConfig.workspaceRoot, "status");
          if (wmStatus.declared) {
            lines.push(`  水位: ${buildWatermarkStatusLine(wmStatus.readings)}`);
          } else {
            lines.push(`  水位: 未声明(config.schema 缺/坏 watermark 声明, 检查跳过)`);
          }
        } catch (e) {
          lines.push(`  水位: 检查失败(${e instanceof Error ? e.message : String(e)})`);
        }
        // AIPOS-F15C: 在途卡行 — 显示本工位在途卡及其下一步(读门 lybra_gate_guidance)
        try {
          const config = loadConfig(process.env);
          const fs = await import("node:fs");
          const path = await import("node:path");
          const inFlight = findInFlightCards(fs, path, config.workspaceRoot, config.agentInstance);
          if (inFlight.length > 0) {
            lines.push(`  在途卡(${inFlight.length} 张):`);
            for (const taskId of inFlight) {
              let nextStep = "";
              if (currentClient) {
                try {
                  const guidance = await currentClient.callTool("lybra_gate_guidance", {
                    task_id: taskId,
                    role: config.role,
                  });
                  const desc = guidance?.guidance?.description || guidance?.description || "";
                  nextStep = desc ? ` → ${desc}` : "";
                } catch {
                  nextStep = " (无法获取下一步)";
                }
              }
              lines.push(`    ${taskId}${nextStep}`);
            }
          } else {
            lines.push(`  在途卡: 无`);
          }
        } catch (e) {
          lines.push(`  在途卡: 查询失败(${e instanceof Error ? e.message : String(e)})`);
        }
        // 版本戳 + provenance + 清单比对(能拿到 config/client 就尽量答)
        try {
          const config = loadConfig(process.env);
          lines.push(buildVersionLine(config));
          lines.push(`[身份来源自曝]\n${buildProvenanceBanner(config)}`);
          const fres = await checkManifestFreshness(currentClient, config);
          // AIPOS-F15C: 清单比对修复 — remote 为 null 时显示原因而非 "?"
          if (fres.error) {
            lines.push(`  清单比对: 无法比对(${fres.error})`);
          } else if (fres.behind) {
            lines.push(`  ⚠ 落后: 本地 ${fres.local ?? "(无)"} vs 线上 ${fres.remote ?? "(无)"} — /lybra sync 后 /reload`);
            // AIPOS-F18-fix2 F-C-1: status 落后分支同样出声带路(与启动分支同款, persistent=true,
            // 原卡大项C声明覆盖"连接器启动与 status 的清单比对"两侧)
            // AIPOS-F20: next_step 文案改指 /lybra sync(入会话拉新, 不出 pi)
            voice(`分发落后(本地${fres.local}/线上${fres.remote}), /lybra sync 后 /reload`, "warn", true);
          } else {
            // remote 为 null 表示无法从门获取对端版本(如门未部署/网络问题)
            const localV = fres.local ?? "(无本地版本戳)";
            const remoteV = fres.remote;
            if (!remoteV) {
              lines.push(`  清单比对: 本地 ${localV}, 线上版本无法获取(gate 无响应或未部署)`);
            } else {
              lines.push(`  清单比对: 最新(本地 ${localV} == 线上 ${remoteV})`);
            }
          }
        } catch (e) {
          // 配置未就绪时不阻断 status(如 /lybra status 早于 /lybra on)
          lines.push(`  版本/来源: 配置未就绪(${e instanceof Error ? e.message : String(e)})`);
        }
        ctx.ui.notify(lines.join("\n"), "info");
        return;
      }

      if (sub === "sync") {
        // AIPOS-F20: 薄壳投影既有 lybra sync CLI —— 工位拉新不出 pi(同一实现零复制)。
        const fs = await import("node:fs");
        const path = await import("node:path");
        // ① harness root: 身份声明(.lybra 所在目录 = 自身工位根)
        const lybraDir = ConnectionResolver.discoverLybraDir();
        const harnessRoot = resolveSyncHarnessRoot(lybraDir);
        if (!harnessRoot) {
          const msg = "sync 失败: 未发现工位 .lybra(身份声明), 无法推得 harness root — 请从工位根目录启动 pi 后重试";
          currentLogger?.error("sync-no-harness-root", {});
          voice(msg, "error", true);
          ctx.ui.notify(msg, "error");
          return;
        }
        // ② bin 路径: 声明键优先 → 缺省探测(loadConfig 失败不阻断, bin 解析可从 connection.json 补读)
        let workspaceRoot = "";
        try {
          workspaceRoot = loadConfig(process.env).workspaceRoot;
        } catch (e) {
          currentLogger?.warn("sync-config-load-failed", { error: e instanceof Error ? e.message : String(e) });
        }
        const binRes = resolveLybraBin(fs, path, { workspaceRoot, lybraDir });
        if (!binRes.bin) {
          const msg =
            `sync 失败: 本机未装 lybra CLI(bin 均不可得` +
            (binRes.tried.length > 0 ? `, 试过: ${binRes.tried.join("; ")}` : "") +
            `) — 远程工位分发通道见 known-debt`;
          currentLogger?.error("sync-no-bin", { tried: binRes.tried, harness_root: harnessRoot });
          voice(msg, "error", true);
          ctx.ui.notify(msg, "error");
          return;
        }
        // ③ 子进程薄壳投影 + 原样透传(禁在连接器里第二遍实现同步逻辑)
        ctx.ui.notify(`[sync] ${binRes.bin} sync --harness-root ${harnessRoot}\n  (bin 来源: ${binRes.source})`, "info");
        try {
          const { execFileSync } = await import("node:child_process");
          const stdout = execFileSync(binRes.bin, ["sync", "--harness-root", harnessRoot], {
            encoding: "utf-8",
            stdio: "pipe",
            timeout: 180000, // sync 走网络拉取, 预算 3 分钟
          });
          currentLogger?.info("sync-success", { bin: binRes.bin, harness_root: harnessRoot, source: binRes.source });
          // 原样透传输出(全文上屏, 不改写)
          ctx.ui.notify(String(stdout).trim() || "(sync 无输出)", "info");
          // stdout 尾行入 voice(persistent=true)
          const tail = extractSyncTailLine(String(stdout));
          if (tail) voice(tail, "info", true);
          // 成功后提示 /reload 生效(本卡明确不做 /reload 自动化)
          voice("sync 完成: 请 /reload 生效", "info", true);
        } catch (e) {
          const errMsg = e instanceof Error ? e.message : String(e);
          const tail = subprocessFailureTail(e, 8);
          const msg = `sync 失败: ${errMsg}${tail ? `\n  子进程输出尾行:\n${tail}` : ""}`;
          currentLogger?.error("sync-failed", { bin: binRes.bin, harness_root: harnessRoot, error: errMsg, failure_tail: tail });
          voice(msg, "error", true);
          ctx.ui.notify(msg, "error");
        }
        return;
      }

      if (sub === "enroll") {
        // AIPOS-F23: 工位一贴上岗 —— /lybra enroll <自包含码>(F20 命令族同构投影)。
        // 流: 解析码→治理仓防护→交换(码即运输认证, 无 bootstrap)→落盘工位 .lybra/→land→连通验证→带路。
        // 交换与落盘原子(验收⑦): 落盘抛错则不 land, 码留在 grace 窗口可原样重贴。
        const rawCode = parts.slice(1).join("").trim();
        const fs = await import("node:fs");
        const path = await import("node:path");
        const failEnroll = (msg: string) => {
          currentLogger?.error("enroll-failed", { msg });
          voice(msg, "error", true);
          ctx.ui.notify(msg, "error");
        };
        if (!rawCode) {
          failEnroll(
            "用法: /lybra enroll LYBRAENROLL1.<码>(顾问 confirm 输出的整条转贴; 裸机等价: lybra roles enroll --code <码>)",
          );
          return;
        }
        let sc: SelfContainedCode | null = null;
        try {
          sc = parseSelfContainedCode(rawCode);
        } catch (e) {
          failEnroll(e instanceof Error ? e.message : String(e));
          return;
        }
        if (!sc) {
          failEnroll(
            "码不是自包含码(需 LYBRAENROLL1. 前缀)。\n下一步: 请顾问用 lybra_enroll_code_dry_run/confirm 重新发码, 整条 /lybra enroll <码> 转贴过来。",
          );
          return;
        }
        // ① 目标工位根: 已有 .lybra → 其父(重铸); 否则 cwd(新工位约定: 从工位根启动 pi)
        const lybraDir0 = ConnectionResolver.discoverLybraDir();
        const targetRoot = resolveEnrollTargetRoot(lybraDir0);
        if (isGovernanceWorkspace(fs, path, targetRoot, sc.governance_root)) {
          failEnroll(
            `enroll 目标是治理工作区(${targetRoot}), 拒绝落盘 —— enroll 只落工位目录 .lybra/。\n下一步: 在工位目录(pi harness 目录)重新运行 /lybra enroll <同码>(grace 窗口内同码可免费重试)。`,
          );
          return;
        }
        ctx.ui.notify(`[enroll] 解析自包含码 ✓  gate=${sc.gate_url}  目标工位=${targetRoot}/.lybra/`, "info");
        try {
          const { GateMcpClient } = await import("./gate-client.ts");
          // ② 交换: 运输凭证(码内嵌, 零 scope)即 transport 认证
          const transportClient = new GateMcpClient(sc.gate_url, sc.transport_token, { timeoutMs: 30000 });
          const exchange = await transportClient.callToolRaw("lybra_roles_enroll_exchange", { code: sc.code });
          if (!exchange.ok) {
            const reason = String(exchange.message || exchange.error || "unknown");
            const next = String(exchange.suggested_next_action || (exchange.details && exchange.details.suggested_next_action) || "");
            failEnroll(`交换失败: ${reason}${next ? `\n下一步: ${next}` : ""}\n${ENROLL_PRODUCT_FAULT_GUIDE}`);
            return;
          }
          const tokenEntry = (exchange.token_entry || {}) as Record<string, unknown>;
          const role = String(tokenEntry.role || "");
          const instance = typeof tokenEntry.agent_instance === "string" ? tokenEntry.agent_instance : null;
          const roleToken = String(tokenEntry.token || "");
          if (!role || !roleToken) {
            failEnroll(`交换返回缺 token_entry(role/token)—— 请顾问检查 gate 侧日志后重新发码。\n${ENROLL_PRODUCT_FAULT_GUIDE}`);
            return;
          }
          // ③ 落盘工位 .lybra/(合并保留既有键, 验收⑨; 写失败不 land, 码可免费重试, 验收⑦)
          const lybraDir = path.join(targetRoot, ".lybra");
          fs.mkdirSync(lybraDir, { recursive: true, mode: 0o700 });
          const connPath = path.join(lybraDir, "connection.json");
          let existingConn: Record<string, unknown> | null = null;
          if (fs.existsSync(connPath)) {
            try {
              const parsed = JSON.parse(fs.readFileSync(connPath, "utf-8"));
              if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) existingConn = parsed;
            } catch {
              existingConn = null; // 损坏重建
            }
          }
          const rolePath = path.join(lybraDir, "role");
          const existingRole = fs.existsSync(rolePath) ? fs.readFileSync(rolePath, "utf-8") : null;
          const connData = upsertEnrollConnection(existingConn, sc.gate_url, targetRoot, tokenEntry);
          fs.writeFileSync(connPath, JSON.stringify(connData, null, 2) + "\n", { mode: 0o600 });
          fs.chmodSync(connPath, 0o600);
          const roleData = mergeRoleFile(existingRole, role, instance);
          fs.writeFileSync(rolePath, JSON.stringify(roleData, null, 2) + "\n", { mode: 0o644 });
          // ④ land: 落盘成功才关 grace 窗口(失败仅告警, 不阻断)
          let landed = false;
          try {
            const landRes = await transportClient.callToolRaw("lybra_roles_enroll_land", {
              code: sc.code,
              landed_detail: `workstation=${targetRoot} files=connection.json,role`,
            });
            landed = Boolean(landRes.ok);
          } catch (e) {
            currentLogger?.warn("enroll-land-failed", { error: e instanceof Error ? e.message : String(e) });
          }
          // ⑤ 连通验证: 新 token 调一次 gate(lybra_gate_version)
          let verifyOk = false;
          let verifyDetail = "";
          try {
            const verifyClient = new GateMcpClient(sc.gate_url, roleToken, { timeoutMs: 20000 });
            const ver = await verifyClient.callToolRaw("lybra_gate_version", {});
            verifyOk = Boolean(ver.ok) || !("ok" in ver); // gate_version 无 ok 字段也算通(2xx 到达即认证通过)
            verifyDetail = verifyOk ? "新 token 调 lybra_gate_version 通过" : JSON.stringify(ver).slice(0, 160);
          } catch (e) {
            verifyDetail = e instanceof Error ? e.message : String(e);
          }
          const fp = String(tokenEntry.fingerprint || "(unknown)");
          const files = ["connection.json", "role"];
          const lines = [
            `✓ 上岗完成(enroll)`,
            `  Role: ${role}${instance ? `  Instance: ${instance}` : ""}`,
            `  Token fingerprint: ${fp}`,
            `  落盘: ${lybraDir}/${files.map((f) => f).join(", ")}(工位目录, 合并保留既有键)`,
            `  land: ${landed ? "已确认(码彻底消费)" : "⚠ 确认失败(码将由 grace 窗口过期自然消费, 不影响使用)"}`,
            `  连通验证: ${verifyOk ? `✓ ${verifyDetail}` : `✗ ${verifyDetail}`}`,
            ...(verifyOk ? [] : [ENROLL_PRODUCT_FAULT_GUIDE]),
            `下一步: /lybra sync 然后 /reload`,
          ].join("\n");
          currentLogger?.info("enroll-success", { role, instance, target_root: targetRoot, landed, verify_ok: verifyOk });
          voice(`上岗完成 role=${role} 落盘=${lybraDir} — 接着 /lybra sync 然后 /reload`, "info", true);
          ctx.ui.notify(lines, verifyOk ? "info" : "warn");
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          failEnroll(
            `enroll 失败: ${msg}\n(落盘前失败 = 码未消费, grace 窗口内可原样重贴 /lybra enroll <同码> 重试)\n${ENROLL_PRODUCT_FAULT_GUIDE}`,
          );
        }
        return;
      }

      if (sub === "return") {
        // AIPOS-F33 大项B: /lybra return — 工位自救交回(同一执行函数, 同一门动词)
        // 参数全从工位身份声明自解析(C2 解析单源), 模型或用户皆可一条命令完成交回。
        // 实现: 调 lybra_queue_return_dry_run + lybra_queue_return_confirm(与 tryAutoReturn 同一门动词)。
        const fs = await import("node:fs");
        const path = await import("node:path");
        let config;
        try {
          config = loadConfig(process.env);
        } catch (e) {
          const msg = `配置错误, 无法交回: ${e instanceof Error ? e.message : String(e)}`;
          currentLogger?.error("return-config-failed", { error: msg });
          ctx.ui.notify(msg, "error");
          return;
        }
        // 解析 task_id: 命令行参数 > currentTaskId
        const taskArg = parts.slice(1).join(" ").trim();
        const taskId = taskArg || currentTaskId;
        if (!taskId) {
          ctx.ui.notify("用法: /lybra return [TASK-ID]\n  无参数时使用当前在途卡(currentTaskId)", "warn");
          return;
        }
        // 检查卡是否在 claimed
        const claimedCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${taskId.toLowerCase()}.md`);
        if (!fs.existsSync(claimedCardPath)) {
          ctx.ui.notify(`卡 ${taskId} 不在 claimed 状态(已被门收走或从未认领)`, "warn");
          return;
        }
        // 读 RETURN.md 提取 summary
        const returnMdPath = path.join(config.workspaceRoot, "task_cards", taskId, "RETURN.md");
        let summary = "Task completed (workspace return)";
        if (fs.existsSync(returnMdPath)) {
          try {
            const returnContent = fs.readFileSync(returnMdPath, "utf-8");
            const conclusionMatch = returnContent.match(/##\s*一句话结论[^\n]*\n+([^\n#]+)/i) ||
                                    returnContent.match(/##\s*Summary[^\n]*\n+([^\n#]+)/i) ||
                                    returnContent.match(/##\s*结论[^\n]*\n+([^\n#]+)/i);
            if (conclusionMatch) {
              summary = conclusionMatch[1].trim();
            }
          } catch (e) {
            currentLogger?.warn("return-read-return-md-failed", { task_id: taskId, error: String(e) });
          }
        }
        // 从 worktree git log 提取 artifact_refs
        let artifactRefs: string[] = [];
        let worktreePath = "";
        try {
          const cardContent = fs.readFileSync(claimedCardPath, "utf-8");
          const worktreeMatch = cardContent.match(/active_worktree_path:\s*['"]?([^'"\n]+)['"]?/i);
          if (worktreeMatch) {
            worktreePath = worktreeMatch[1].trim();
          }
        } catch { /* ignore */ }
        if (worktreePath && fs.existsSync(worktreePath)) {
          try {
            const { execSync } = await import("node:child_process");
            const gitOutput = execSync("git diff-tree --no-commit-id --name-only -r HEAD", {
              cwd: worktreePath,
              encoding: "utf-8",
            });
            artifactRefs = gitOutput.trim().split("\n").filter((f) => f.trim());
          } catch { /* ignore */ }
        }
        // 确保 gate 连接可用
        if (!currentClient) {
          try {
            const verbCatalog = loadVerbCatalog();
            currentClient = new GateMcpClient(config.gateUrl, config.token, { verbs: verbCatalog, timeoutMs: config.timeoutMs });
            await currentClient.initialize();
            currentTokenFp = currentClient.tokenFingerprint;
          } catch (e) {
            const msg = `gate 连接失败: ${e instanceof Error ? e.message : String(e)}`;
            ctx.ui.notify(msg, "error");
            return;
          }
        }
        ctx.ui.notify(`交回 ${taskId}...`, "info");
        try {
          // 同一门动词: lybra_queue_return_dry_run(与 tryAutoReturn 同源)
          const dryRunResp = await currentClient.callTool("lybra_queue_return_dry_run", {
            task_id: taskId,
            actor: config.actor,
            agent_instance: config.agentInstance,
            autonomy_mode: "Supervised",
            owner_policy_ref: config.ownerPolicyRef,
            result_summary: summary,
            artifact_refs: artifactRefs.length > 0 ? artifactRefs : undefined,
            active_session_id: getSessionId(),
          });
          if (dryRunResp.verdict === "BLOCK" || dryRunResp.isError === true) {
            const reasons = dryRunResp.blocking_reasons || dryRunResp.errors || [];
            const reasonText = stringifyReasons(reasons);
            ctx.ui.notify(`交回被拒: ${reasonText}`, "error");
            return;
          }
          const dryRunToken = dryRunResp.dry_run_token;
          if (!dryRunToken) {
            ctx.ui.notify("交回失败: 无 dry_run_token", "error");
            return;
          }
          // 同一门动词: lybra_queue_return_confirm(AIPOS-328 executor 自确认)
          const confirmResp = await currentClient.callTool("lybra_queue_return_confirm", {
            dry_run_token: String(dryRunToken),
            actor: config.actor,
            agent_instance: config.agentInstance,
            owner_policy_ref: config.ownerPolicyRef,
            owner_confirmation_token: "OWNER_CONFIRMED",
          });
          currentLogger?.info("workspace-return-success", { task_id: taskId });
          voice(`工位交回完成: ${taskId}`, "info", true);
          ctx.ui.notify(`✓ 已交回 ${taskId}`, "info");
          // 如果交回的是当前在途卡, 复位运行态
          if (taskId === currentTaskId) {
            resetRuntimeState("/lybra return 手动交回");
          }
        } catch (e) {
          const msg = `交回失败: ${e instanceof Error ? e.message : String(e)}`;
          currentLogger?.error("workspace-return-failed", { task_id: taskId, error: msg });
          ctx.ui.notify(msg, "error");
        }
        return;
      }

      if (sub === "on") {
        if (loopState.on) {
          ctx.ui.notify("lybra 循环已在运行,先 /lybra off 再启动", "warn");
          return;
        }
        let maxN = 1;
        if (parts[1]) {
          const n = Number(parts[1]);
          if (!Number.isFinite(n) || n < 1 || !Number.isInteger(n)) {
            ctx.ui.notify(`maxN 无效:${parts[1]}(需正整数)`, "error");
            return;
          }
          maxN = n;
        }

        // 配置(必需项缺失即停,绝不猜 actor/policy)
        // AIPOS-R8B 大项C④: 启动阶段带耗时显示
        const startMs = Date.now();
        ctx.ui?.notify?.("[1/5] 加载配置...", "info");
        
        let config;
        try {
          config = loadConfig(process.env);
          const configMs = Date.now() - startMs;
          ctx.ui?.notify?.(`[1/5] 配置加载完成 (${configMs}ms)`, "info");
        } catch (e) {
          ctx.ui.notify(`配置错误,循环未启动:${e instanceof Error ? e.message : String(e)}`, "error");
          return;
        }
        currentGateUrl = config.gateUrl;
        currentRole = config.role;
        currentActor = config.actor;

        // AIPOS-C2 大项C: 来源自曝横幅 (每个键打印取自哪一层, env 兜底/被降级标 ⚠)
        ctx.ui?.notify?.(
          `[身份来源自曝]\n${buildProvenanceBanner(config)}`,
          "info",
        );

        // AIPOS-C4B 大项B: 启动即报版本戳(分发器生成的源 commit 短哈希)
        ctx.ui?.notify?.(buildVersionLine(config), "info");

        // AIPOS-R6R: 启动即校验 schema(缺动词/改错必填参数名 → 报错不启动)。
        ctx.ui?.notify?.("[2/5] 校验 schema...", "info");
        let verbCatalog;
        try {
          const schemaStartMs = Date.now();
          verbCatalog = loadVerbCatalog();
          validateRequiredVerbs(verbCatalog, REQUIRED_VERBS);
          const schemaMs = Date.now() - schemaStartMs;
          ctx.ui?.notify?.(`[2/5] Schema 校验完成 (${schemaMs}ms)`, "info");
        } catch (e) {
          ctx.ui.notify(
            `schema 校验失败,循环未启动:${e instanceof Error ? e.message : String(e)}`,
            "error",
          );
          return;
        }

        // gate 连通性自检(initialize)
        // AIPOS-R8B 大项C②: 使用配置的超时预算
        ctx.ui?.notify?.(`[3/5] 连接 gate (${config.gateUrl})...`, "info");
        const client = new GateMcpClient(config.gateUrl, config.token, { verbs: verbCatalog, timeoutMs: config.timeoutMs });
        try {
          const gateStartMs = Date.now();
          await client.initialize();
          const gateMs = Date.now() - gateStartMs;
          ctx.ui?.notify?.(`[3/5] Gate 连接成功 (${gateMs}ms, timeout=${config.timeoutMs}ms)`, "info");
        } catch (e) {
          ctx.ui.notify(
            `gate 连接失败(${config.gateUrl}),循环未启动:${e instanceof Error ? e.message : String(e)}\n` +
              `确认 Owner 已 lybra serve、且 ${process.env.LYBRA_CONNECTION_JSON || "~/.lybra/local/connection.json"} 可达`,
            "error",
          );
          return;
        }
        currentTokenFp = client.tokenFingerprint;
        currentClient = client;
        currentLogger = new Logger(process.env.LYBRA_LOOP_LOG || LOG_PATH_DEFAULT);

        loopState = freshState();
        loopState.on = true;
        loopState.maxN = maxN;
        cooldownAnnounced = false; // AIPOS-F16: 每次 on 重置余热转入声门门
        (loopState as { cycleStartMs: number }).cycleStartMs = Date.now();
        currentLogger.info("loop-on", {
          maxN,
          gate: config.gateUrl,
          actor: config.actor,
          interval: config.intervalSec,
          maxWait: config.maxWaitSec,
          token: currentTokenFp,
        });

        // AIPOS-F11 大项C: loop-on 启动复位模块级残留(上一轮歇手未清/中断残留),
        // 否则周期 sweep 会误判"有卡在跑"永久让路。
        resetRuntimeState("loop-on 启动复位");

        // AIPOS-F19: 启动自检水位(检查点①/③) — statfs 各声明路径对 schema 阈值,
        // 越阈出声(persistent)带路; 降噪状态不重置(同路径同级别不重复喊)。
        watermarkTickCounter = 0; // 周期复查节奏随循环重启重计
        try {
          const wmStartup = runWatermarkCheck(config.workspaceRoot, "startup");
          if (wmStartup.declared && wmStartup.readings.length > 0) {
            ctx.ui?.notify?.(`[水位自检] ${buildWatermarkStatusLine(wmStartup.readings)}`, "info");
          } else if (!wmStartup.declared) {
            ctx.ui?.notify?.("[水位自检] 跳过(config.schema 缺/坏 watermark 声明, 禁写死兜底)", "warn");
          }
        } catch (e) {
          currentLogger?.warn("watermark-startup-error", { error: e instanceof Error ? e.message : String(e) });
        }

        // AIPOS-R6I 靶③: loop可感知反馈 - 启动时打印详细信息
        // AIPOS-R8B 大项C④: 带耗时显示
        ctx.ui?.notify?.("[4/5] 查询队列...", "info");
        const queueStartMs = Date.now();
        const queueInfo = await client.queueTasks().catch(() => []);
        const queueMs = Date.now() - queueStartMs;
        const queueCount = queueInfo.length;
        const nextPollSec = config.intervalSec;
        ctx.ui?.notify?.(`[4/5] 队列查询完成 (${queueMs}ms, ${queueCount} 张卡)`, "info");

        // AIPOS-C4B 大项B: loop 启动自检发现落后时出声但不拒跑(提示级, 不做强制门)
        const freshness = await checkManifestFreshness(client, config);
        const onLines: string[] = [
          `lybra on: 已连 gate · 身份 ${config.agentInstance} · 信封 ${config.ownerPolicyRef} · 队列 ${queueCount} 张 · ${nextPollSec}s 后再拉`,
          `  启动自动领卡循环 (maxN=${maxN}, interval=${config.intervalSec}s, maxWait=${config.maxWaitSec}s)`,
          `  只放行信封内(PreAuthorized)卡; 信封外跳过; BLOCK/失败立停`,
          `  /lybra off 可停; /lybra status 查看状态`,
        ];
        if (freshness.error) {
          onLines.push(`  清单比对: 无法比对(${freshness.error})`);
        } else if (freshness.behind) {
          onLines.push(`  ⚠ 落后: 本地 ${freshness.local} vs 线上 ${freshness.remote} — /lybra sync 后 /reload`);
          // AIPOS-F18 大项C: 版本戳带路 — 不一致时出声(persistent=true)
          // AIPOS-F20: next_step 文案改指 /lybra sync(入会话拉新, 不出 pi)
          voice(`分发落后(本地${freshness.local}/线上${freshness.remote}), /lybra sync 后 /reload`, "warn", true);
        } else {
          onLines.push(`  清单比对: 最新(本地 ${freshness.local ?? "?"} == 线上 ${freshness.remote ?? "?"})`);
        }
        ctx.ui.notify(onLines.join("\n"), "info");
        
        // AIPOS-R6I 靶②: 存量收敛 - 启动时扫描已有 PASS 裁决但未 finalize 的卡自动补收
        // AIPOS-R8B 大项C④: sweep 带耗时显示
        ctx.ui?.notify?.("[5/5] 存量收敛(sweep)...", "info");
        const sweepStartMs = Date.now();
        await tryAutoFinalizeOnPassVerdict().catch((e) => {
          const errMsg = `启动时 auto-finalize 错误: ${e instanceof Error ? e.message : String(e)}`;
          currentLogger.warn("startup-auto-finalize-error", {
            error: e instanceof Error ? e.message : String(e),
          });
          // AIPOS-R6L 第三轮修复(b): 失败必出声
          ctx.ui?.notify?.(errMsg, "error");
        });
        const sweepMs = Date.now() - sweepStartMs;
        ctx.ui?.notify?.(`[5/5] 存量收敛完成 (${sweepMs}ms)`, "info");

        // AIPOS-F29B 大项A: held-startup 托管接线 - 启动遇在途卡且 RETURN.md 就位→走同一托管函数
        // (F29 托管唯一实现: settle 路已有, 禁复制第二份托管逻辑; 2026-08-23 实撞=reload 后仍投递复工)
        const fs = await import("node:fs");
        const path = await import("node:path");
        const inFlightAtStartup = findInFlightCards(fs, path, config.workspaceRoot, config.agentInstance);
        if (inFlightAtStartup.length > 0) {
          currentLogger?.info("startup-held-check", { in_flight: inFlightAtStartup });
          for (const heldTaskId of inFlightAtStartup) {
            // 检查 RETURN.md 是否就位
            const returnMdPath = path.join(config.workspaceRoot, "task_cards", heldTaskId, "RETURN.md");
            if (fs.existsSync(returnMdPath)) {
              currentLogger?.info("startup-held-return-ready", { task_id: heldTaskId, return_md: returnMdPath });
              voice(`启动检测: ${heldTaskId} RETURN.md 就位, 托管交回`, "info", true);
              // 设置 currentTaskId 供tryAutoReturn 使用
              currentTaskId = heldTaskId;
              // 提取 worktree 路径
              const taskCardPath = path.join(config.workspaceRoot, "5_tasks/queue/claimed", `${heldTaskId.toLowerCase()}.md`);
              if (fs.existsSync(taskCardPath)) {
                try {
                  const cardContent = fs.readFileSync(taskCardPath, "utf-8");
                  const worktreeMatch = cardContent.match(/active_worktree_path:\s*['"]+([^'"\n]+)['"]/i);
                  if (worktreeMatch) {
                    currentWorktreePath = worktreeMatch[1].trim();
                  }
                } catch (e) {
                  currentLogger?.warn("startup-held-read-card-failed", { task_id: heldTaskId, error: String(e) });
                }
              }
              // 调用托管函数（F29 托管唯一实现）
              const returned = await tryAutoReturn();
              if (returned) {
                voice(`启动托管交回成功: ${heldTaskId}`, "info", true);
              } else {
                voice(`启动托管交回失败: ${heldTaskId}, 请检查日志`, "warn", true);
              }
              // 只处理第一张在途卡（一卡一会话原则）
              break;
            } else {
              currentLogger?.info("startup-held-no-return", { task_id: heldTaskId, reason: "RETURN.md 未就位, 继续轮询" });
            }
          }
        }
        
        const totalMs = Date.now() - startMs;
        ctx.ui?.notify?.(`✓ 启动完成，总耗时 ${totalMs}ms`, "info");
        
        // F-EXT001-4(FIX1):非阻塞,直接调用第一轮 tick(不经 sendUserMessage)
        doTick().catch((e) => {
          // F-EXT001-8(FIX4):不再静默吞错,落日志
          currentLogger.warn("lybra-on-first-tick-error", {
            error: e instanceof Error ? e.message : String(e),
          });
        });
        return;
      }

      ctx.ui.notify("用法:/lybra on [maxN] | /lybra off | /lybra status | /lybra sync | /lybra return [TASK-ID]", "warn");
    },
  });

  // --- /lybra-tick:手动触发入口(自动链不依赖它,直接调 doTick)---
  // F-EXT001-4(FIX1):保留命令仅作人工手动触发,自动轮询链不经此路径
  pi.registerCommand("lybra-tick", {
    description: "(手动)立即执行一轮 lybra-loop tick;自动链不依赖命令路由",
    handler: async (_args, ctx) => {
      liveCtx = ctx; // AIPOS-F10:命令入口刷新活引用
      // 直接调用 doTick,与自动链同路径
      await doTick();
    },
  });
}
