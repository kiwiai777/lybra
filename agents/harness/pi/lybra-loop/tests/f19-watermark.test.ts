/**
 * AIPOS-F19 专项测试 —— 工位水位自检(磁盘/tmpfs 越阈出声带路, 只喊不代删)。
 *
 * 三层:
 *  A. 纯单测:阈值边界、schema 解析(缺/坏声明→null, 阈值跟随声明)、占位符解析、
 *     statfs 口径(注入 mock)、出声文本(warn/critical 素材全来自声明)、降噪判定、status 行。
 *  B. 源级断言(f16 范式):三处检查点在、出声 persistent、只读不动手(无清理调用)、
 *     schema 声明齐全、禁写死(代码无裸阈值)。
 *  C. 夹具 E2E(mock gate + mock pi + 临时 workspace + LYBRA_SCHEMA_DIR 热切换):
 *     正常水位零噪音 → 调低阈值后 周期 tick/status/启动 三处出声且带 next_step →
 *     journal 留痕 → 同状态不重复喊 → critical 分支 → 阈值改声明跟随(验完还原)。
 * 跑法:`node tests/f19-watermark.test.ts`
 */
import {
  parseWatermarkConfig,
  resolveWatermarkPaths,
  computeWatermarkLevel,
  readDiskUsage,
  buildWatermarkStatusLine,
  buildWatermarkVoiceMessage,
  watermarkShouldVoice,
  type WatermarkReading,
} from "../lybra-loop.ts";
import type { ConfigSchemaShape } from "../gate-client.ts";
import { mkdtempSync, rmSync, readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { AnyDict } from "../loop-decisions.ts";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}
async function waitFor(desc: string, cond: () => boolean, timeoutMs = 10000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (cond()) return true;
    await new Promise((r) => setTimeout(r, 100));
  }
  check(`等待超时: ${desc}`, false);
  return false;
}

// ===========================================================================
// A. 纯单测
// ===========================================================================

// --- 完整声明夹具(与产品 schema 同构, 改值即改行为 = 阈值跟随声明) ---
function fullSchema(warn: number, critical: number, interval = 1): ConfigSchemaShape {
  return {
    watermark: {
      thresholds: { warn_percent: warn, critical_percent: critical },
      check_paths: [
        { path: "{workspace_root}", label: "workspace" },
        { path: "/tmp", label: "tmp" },
      ],
      periodic_tick_interval: interval,
      clearable_items: "夹具可清项A、夹具可清项B",
      next_step_template: "非 lybra 数据勿自行清理, 通报占用方; lybra 可清项={clearable_items}",
      critical_extra_line: "写入随时失败, 建议暂停领新卡",
    },
  };
}

// A1. 阈值边界: < warn = ok; == warn = warn; warn..critical = warn; == critical = critical; > = critical
{
  check("A1 低于 warn → ok", computeWatermarkLevel(79.9, 80, 90) === "ok");
  check("A1 等于 warn → warn(越=≥)", computeWatermarkLevel(80, 80, 90) === "warn");
  check("A1 warn 与 critical 之间 → warn", computeWatermarkLevel(89.9, 80, 90) === "warn");
  check("A1 等于 critical → critical", computeWatermarkLevel(90, 80, 90) === "critical");
  check("A1 超过 critical → critical", computeWatermarkLevel(99.9, 80, 90) === "critical");
  check("A1 混合路径级别独立判定", computeWatermarkLevel(5, 80, 90) === "ok" && computeWatermarkLevel(95, 80, 90) === "critical");
}

// A2. parseWatermarkConfig:合法 → 解析值全跟随声明; 缺/坏 → null(禁写死兜底)
{
  const cfg = parseWatermarkConfig(fullSchema(75, 95, 5))!;
  check("A2 合法声明解析(warn/critical/interval 跟随)", cfg !== null && cfg.warnPercent === 75 && cfg.criticalPercent === 95 && cfg.periodicTickInterval === 5);
  check("A2 check_paths 跟随声明", cfg.checkPaths.length === 2 && cfg.checkPaths[0].path === "{workspace_root}" && cfg.checkPaths[1].path === "/tmp");
  check("A2 缺 watermark 节 → null", parseWatermarkConfig({}) === null);
  check("A2 缺 thresholds → null", parseWatermarkConfig({ watermark: { check_paths: [{ path: "/tmp" }] } }) === null);
  check("A2 阈值非法(负) → null", parseWatermarkConfig(fullSchema(-1, 90)) === null);
  check("A2 阈值非法(>100) → null", parseWatermarkConfig(fullSchema(80, 101)) === null);
  check("A2 critical < warn → null", parseWatermarkConfig(fullSchema(90, 80)) === null);
  check("A2 缺 check_paths → null", parseWatermarkConfig({ watermark: { thresholds: { warn_percent: 80, critical_percent: 90 } } }) === null);
  check("A2 check_paths 空数组 → null", parseWatermarkConfig({ watermark: { ...fullSchema(80, 90).watermark!, check_paths: [] } }) === null);
  check("A2 缺 periodic_tick_interval → null", parseWatermarkConfig({ watermark: { ...fullSchema(80, 90).watermark!, periodic_tick_interval: undefined } }) === null);
  check("A2 interval=0 → null", parseWatermarkConfig(fullSchema(80, 90, 0)) === null);
  check("A2 缺话术素材(clearable_items) → null", parseWatermarkConfig({ watermark: { ...fullSchema(80, 90).watermark!, clearable_items: "" } }) === null);
  check("A2 缺 next_step_template → null", parseWatermarkConfig({ watermark: { ...fullSchema(80, 90).watermark!, next_step_template: "" } }) === null);
  check("A2 缺 critical_extra_line → null", parseWatermarkConfig({ watermark: { ...fullSchema(80, 90).watermark!, critical_extra_line: "" } }) === null);
}

// A3. 占位符解析:{workspace_root} 替换, 字面路径不动
{
  const resolved = resolveWatermarkPaths(
    [
      { path: "{workspace_root}", label: "workspace" },
      { path: "/tmp", label: "tmp" },
      { path: "/data/{workspace_root}/sub", label: "嵌套" },
    ],
    "/ws/root",
  );
  check("A3 占位符替换", resolved[0].path === "/ws/root");
  check("A3 字面路径不动", resolved[1].path === "/tmp");
  check("A3 嵌套占位符全替换", resolved[2].path === "/data//ws/root/sub");
}

// A4. readDiskUsage:df 口径 (blocks-bavail)/blocks*100; 失败 → error
{
  const mock = (p: string) => {
    if (p === "/bad") throw new Error("ENOENT");
    return { bsize: 4096, blocks: 1000, bavail: 250 }; // used 75%
  };
  const ok = readDiskUsage(mock, "/ok") as { usedPercent: number; freeBytes: number; totalBytes: number };
  check("A4 df 口径 used%", Math.abs(ok.usedPercent - 75) < 1e-9);
  check("A4 free/total 字节", ok.freeBytes === 250 * 4096 && ok.totalBytes === 1000 * 4096);
  const bad = readDiskUsage(mock, "/bad");
  check("A4 statfs 失败 → error 置位", "error" in bad && !!(bad as { error?: string }).error);
  const zero = readDiskUsage(() => ({ bsize: 4096, blocks: 0, bavail: 0 }), "/zero");
  check("A4 blocks=0 → error(防除零)", "error" in zero && !!(zero as { error?: string }).error);
}

// A5. 出声文本:路径+水位+next_step; critical 追加声明句; 素材全来自声明
{
  const cfg = parseWatermarkConfig(fullSchema(80, 90))!;
  const r: WatermarkReading = { path: "/tmp", label: "tmp", usedPercent: 85.3, freeBytes: 2.4 * 1024 ** 3, totalBytes: 16 * 1024 ** 3, level: "warn" };
  const warnMsg = buildWatermarkVoiceMessage(r, cfg);
  check("A5 warn 含路径", warnMsg.includes("/tmp"));
  check("A5 warn 含水位", warnMsg.includes("85.3%"));
  check("A5 warn 含 next_step 指引(勿自清/通报)", warnMsg.includes("非 lybra 数据勿自行清理") && warnMsg.includes("通报占用方"));
  check("A5 warn 可清项来自声明", warnMsg.includes("夹具可清项A"));
  check("A5 warn 不含 critical 句", !warnMsg.includes("写入随时失败"));
  const rc: WatermarkReading = { ...r, level: "critical", usedPercent: 93.1 };
  const critMsg = buildWatermarkVoiceMessage(rc, cfg);
  check("A5 critical 追加声明句", critMsg.includes("写入随时失败, 建议暂停领新卡"));
  check("A5 critical 也带 next_step", critMsg.includes("next_step"));
}

// A6. 降噪:同路径同级别不重复喊; 级别变化才再喊; ok 永不出声
{
  const last = new Map<string, "ok" | "warn" | "critical">();
  check("A6 首次 warn 出声", watermarkShouldVoice("/tmp", "warn", last) === true);
  last.set("/tmp", "warn");
  check("A6 同级 warn 不重复", watermarkShouldVoice("/tmp", "warn", last) === false);
  check("A6 warn→critical 变化出声", watermarkShouldVoice("/tmp", "critical", last) === true);
  last.set("/tmp", "critical");
  check("A6 critical→warn 变化出声", watermarkShouldVoice("/tmp", "warn", last) === true);
  last.set("/tmp", "warn");
  check("A6 恢复 ok 不出声(正常水位零噪音)", watermarkShouldVoice("/tmp", "ok", last) === false);
  check("A6 无记录时 ok 不出声", watermarkShouldVoice("/other", "ok", last) === false);
  check("A6 路径间互不干扰", watermarkShouldVoice("/other", "warn", last) === true);
}

// A7. status 行:各路径 used%/free; 读取失败标注
{
  const line = buildWatermarkStatusLine([
    { path: "/ws", label: "workspace", usedPercent: 9.1, freeBytes: 1450.1 * 1024 ** 3, totalBytes: 1600 * 1024 ** 3, level: "ok" },
    { path: "/tmp", label: "tmp", usedPercent: 85.3, freeBytes: 2.4 * 1024 ** 3, totalBytes: 16 * 1024 ** 3, level: "warn" },
    { path: "/bad", label: "bad", usedPercent: 0, freeBytes: 0, totalBytes: 0, level: "ok", error: "ENOENT" },
  ]);
  check("A7 各路径 used%", line.includes("9.1%") && line.includes("85.3%"));
  check("A7 各路径 free", line.includes("1450.1GB") && line.includes("2.4GB"));
  check("A7 越阈带警告标记", line.includes("⚠"));
  check("A7 读取失败标注", line.includes("读取失败"));
}

// A8. 真实产品 schema:声明齐全可解析(阈值跟随 schema 改动)
{
  const { loadConfigSchema } = await import("../gate-client.ts");
  const real = loadConfigSchema();
  const cfg = parseWatermarkConfig(real);
  check("A8 产品 schema watermark 可解析", cfg !== null);
  if (cfg) {
    check("A8 默认阈值 80/90 跟随声明", cfg.warnPercent === 80 && cfg.criticalPercent === 90);
    check("A8 周期 10 tick 跟随声明", cfg.periodicTickInterval === 10);
    check("A8 检查路径集 = workspace+/tmp", cfg.checkPaths.some((p) => p.path === "{workspace_root}") && cfg.checkPaths.some((p) => p.path === "/tmp"));
  }
}

// ===========================================================================
// B. 源级断言(f16 范式)
// ===========================================================================
{
  const src = readFileSync(join(import.meta.dirname || ".", "../lybra-loop.ts"), "utf8");
  const schemaText = readFileSync(join(import.meta.dirname || ".", "../../../../../schema/config.schema.json"), "utf8");
  const gateClientSrc = readFileSync(join(import.meta.dirname || ".", "../gate-client.ts"), "utf8");

  check("B1 三处检查点在(startup/tick/status)", src.includes('runWatermarkCheck(config.workspaceRoot, "startup")') && src.includes('runWatermarkCheck(config.workspaceRoot, "tick")') && src.includes('runWatermarkCheck(wmConfig.workspaceRoot, "status")'));
  check("B2 周期复查走 isWatermarkTickDue(声明 interval)", src.includes("isWatermarkTickDue()") && src.includes("watermarkTickCounter"));
  check("B3 status 水位行常驻", src.includes("buildWatermarkStatusLine(wmStatus.readings)"));
  check("B4 出声 persistent=true(F15B 双写)", src.includes('voice(buildWatermarkVoiceMessage(reading, cfg), level === "critical" ? "error" : "warn", true)'));
  check("B5 降噪同源(watermarkShouldVoice 单一判定)", src.includes("watermarkShouldVoice(path, level, watermarkLastLevels)"));
  check("B6 只喊不动手: 全文件无 rm/unlink 破坏调用", !/rmSync\(|unlinkSync\(|rmdirSync\(/.test(src));
  check("B7 禁写死: 连接器无裸水位阈值(80/90 只在 schema)", !/(warn|critical)Percent\s*=\s*(80|90)\b/.test(src));
  check("B8 schema 声明齐全(阈值/路径/周期/可清项/话术)", ["warn_percent", "critical_percent", "check_paths", "periodic_tick_interval", "clearable_items", "next_step_template", "critical_extra_line"].every((k) => schemaText.includes(k)));
  check("B9 gate-client 形状声明含 watermark", gateClientSrc.includes("watermark?: WatermarkSchemaSection"));
  check("B10 statfs 失败不炸(try/catch → error reading)", src.includes("watermark-statfs-failed"));
}

// ===========================================================================
// C. 夹具 E2E —— mock gate + mock pi + 临时 workspace + LYBRA_SCHEMA_DIR 热切换
// 验收映射: ①阈值改声明跟随 ②启动/周期/status 出声+journal+同状态不重复
//           ③正常水位零噪音 ④status 水位行常驻
// ===========================================================================
const NOTES: string[] = [];
if (process.env.F19_SKIP_E2E) {
  NOTES.push("E2E 被 F19_SKIP_E2E 跳过");
} else {
  const { createServer } = await import("node:http");

  // --- schema 夹具目录: LYBRA_SCHEMA_DIR 指向 fixture 根, 其 schema/ 子目录放 json ---
  const productSchemaDir = join(import.meta.dirname || ".", "../../../../../schema");
  const schemaFixtureRoot = mkdtempSync(join(tmpdir(), "f19-schema-"));
  const schemaFixtureDir = join(schemaFixtureRoot, "schema");
  mkdirSync(schemaFixtureDir, { recursive: true });
  copyFileSync(join(productSchemaDir, "verbs.schema.json"), join(schemaFixtureDir, "verbs.schema.json"));
  /** 写夹具 config.schema.json(阈值热切换: 改文件即改行为 = 验收①) */
  function writeFixtureSchema(warn: number, critical: number, interval = 1) {
    const real = JSON.parse(readFileSync(join(productSchemaDir, "config.schema.json"), "utf8"));
    real.watermark = {
      ...real.watermark,
      thresholds: { ...real.watermark.thresholds, warn_percent: warn, critical_percent: critical },
      periodic_tick_interval: interval,
    };
    writeFileSync(join(schemaFixtureDir, "config.schema.json"), JSON.stringify(real, null, 2));
  }

  // --- mock gate(空队列即可 — 水位检查在 tick 前置, 不依赖卡) ---
  const gateServer = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      let msg: AnyDict;
      try { msg = JSON.parse(body); } catch { res.writeHead(400); res.end(); return; }
      const send = (result: unknown) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", id: (msg as AnyDict).id ?? null, result }));
      };
      if (msg.method === "initialize") return send({ protocolVersion: "2025-03-26" });
      if (msg.method !== "tools/call") return send({});
      const name = String(msg.params?.name ?? "");
      if (name === "lybra_queue_list") return send({ structuredContent: { data: { tasks: [] } } });
      if (name === "lybra_distribution_manifest") return send({ structuredContent: { product_commit: "fixture" } });
      return send({ structuredContent: { ok: true } });
    });
  });
  await new Promise<void>((resolve) => gateServer.listen(0, "127.0.0.1", resolve));
  const gatePort = (gateServer.address() as { port: number }).port;

  // --- mock pi + 工厂 ---
  function makeMockPi() {
    const handlers: Record<string, Function> = {};
    const commands: Record<string, { description: string; handler: Function }> = {};
    return {
      api: {
        on(evt: string, h: Function) { handlers[evt] = h; },
        registerCommand(n: string, o: { description: string; handler: Function }) { commands[n] = o; },
        registerEntryRenderer: () => {},
        appendEntry: () => {},
      } as any,
      handlers, commands,
    };
  }
  const { default: factory } = await import("../lybra-loop.ts");
  const mp = makeMockPi();
  factory(mp.api);

  const allNotifies: Array<{ m: string; l?: string }> = [];
  function makeCtx() {
    const notifies: Array<{ m: string; l?: string }> = [];
    const ctx = {
      ui: { notify: (m: string, l?: string) => { notifies.push({ m, l }); allNotifies.push({ m, l }); } },
      sessionManager: { getSessionId: () => "f19-e2e-sess" },
      newSession: async (_opts: unknown) => ({ cancelled: false }),
      sendUserMessage: async (_t: string) => {},
    };
    return { ctx, notifies };
  }

  // --- 夹具 workspace + 环境 ---
  const cleanCwd = mkdtempSync(join(tmpdir(), "f19-clean-cwd-"));
  process.chdir(cleanCwd);
  const ws = mkdtempSync(join(tmpdir(), "f19-e2e-ws-"));
  mkdirSync(join(ws, "5_tasks/queue/pending"), { recursive: true });
  mkdirSync(join(ws, "5_tasks/queue/claimed"), { recursive: true });
  writeFileSync(join(ws, "project.json"), JSON.stringify({ code_repo: ws }));
  const logPath = join(ws, "loop.log");
  const journalPath = join(ws, "voice-journal.md");

  const SAVED_ENV = { ...process.env };
  for (const k of Object.keys(process.env)) if (k.startsWith("LYBRA_")) delete process.env[k];
  process.env.LYBRA_WORKSPACE_ROOT = ws;
  process.env.LYBRA_ROLE = "executor";
  process.env.LYBRA_ACTOR = "me";
  process.env.LYBRA_AGENT_INSTANCE = "me";
  process.env.LYBRA_OWNER_POLICY_REF = "pol-f19";
  process.env.LYBRA_TOKEN = "fixture-token";
  process.env.LYBRA_GATE_URL = `http://127.0.0.1:${gatePort}`;
  process.env.LYBRA_LOOP_LOG = logPath;
  process.env.LYBRA_LOOP_INTERVAL = "30";
  process.env.LYBRA_LOOP_MAX_WAIT = "3600";
  process.env.LYBRA_SCHEMA_DIR = schemaFixtureRoot;

  const settle = async () => { await mp.handlers["agent_settled"]({}, makeCtx().ctx); };
  /** doTick 是 fire-and-forget(agent_settled 内不 await), 连续 settle 会撞 running 重入护栏被跳过。
   *  本助手: settle 后等该轮 tick 完成(空队列必落 wait-poll 日志)再返回, 保证时序确定。 */
  const tickDoneCount = () => (logText().match(/"action":"wait-poll"/g) || []).length;
  const settleTick = async (desc: string) => {
    const before = tickDoneCount();
    await settle();
    await waitFor(desc, () => tickDoneCount() > before);
  };
  /** 只数水位告警出声(排除 [水位自检] 横幅 — 那是盘况行不是告警) */
  const wmVoices = () => allNotifies.filter((n) => n.m.includes("[水位警告]") || n.m.includes("[水位危急]"));
  const journalText = () => { try { return readFileSync(journalPath, "utf8"); } catch { return ""; } };
  const logText = () => { try { return readFileSync(logPath, "utf8"); } catch { return ""; } };
  /** log 中 level-change 落到 ok 的累计次数(两路径恢复 = +2) */
  const okCount = () => (logText().match(/"to":"ok"/g) || []).length;
  const statusOf = async () => {
    const { ctx, notifies } = makeCtx();
    await mp.commands.lybra.handler("status", ctx);
    return notifies.map((n) => n.m).join("\n");
  };
  const wmLineOf = (st: string) => st.split("\n").find((l) => l.trim().startsWith("水位:")) || "";

  // 实际盘况(夹具阈值以此校准: 调低于实际水位即触发)
  const fsMod = await import("node:fs");
  const wsUsage = readDiskUsage(fsMod.statfsSync, ws) as { usedPercent: number };
  const tmpUsage = readDiskUsage(fsMod.statfsSync, "/tmp") as { usedPercent: number };
  const minUsage = Math.min(wsUsage.usedPercent, tmpUsage.usedPercent);
  NOTES.push(`夹具盘况: ws=${wsUsage.usedPercent.toFixed(2)}% tmp=${tmpUsage.usedPercent.toFixed(2)}% (critical 夹具阈=${(minUsage / 2).toFixed(3)}%)`);

  try {
    // ---------- 验收③: 正常水位零噪音(阈值 80/90, 实际 <10%) ----------
    writeFixtureSchema(80, 90, 1);
    {
      const { ctx, notifies } = makeCtx();
      await mp.commands.lybra.handler("on 1", ctx);
      await waitFor("首轮 tick 完成(轮询出声)", () => allNotifies.some((n) => n.m.includes("轮询:")));
      check("C1 正常水位零噪音: 无水位告警出声", wmVoices().length === 0);
      check("C1 启动自检横幅可见(盘况一行含两路径)", notifies.some((n) => n.m.includes("[水位自检]") && n.m.includes("workspace(") && n.m.includes("tmp(")));
    }

    // ---------- 验收④: status 水位行常驻 ----------
    {
      const st = await statusOf();
      const wmLine = wmLineOf(st);
      check("C2 status 水位行常驻(各路径 used%/free)", wmLine.includes("workspace(") && wmLine.includes("tmp(/tmp") && /剩 \d+(\.\d+)?GB/.test(wmLine));
      check("C2 正常水位行无告警标记", !wmLine.includes("⚠") && !wmLine.includes("🔴"));
    }

    // ---------- 验收②+①: 周期 tick 出声(阈值热调低 → 状态变化 → 出声带 next_step) ----------
    writeFixtureSchema(0, 90, 1); // warn=0: 任何实际水位(含 0)都越 warn; critical=90 不触
    await settleTick("C3 周期 tick 完成");
    {
      await waitFor("周期 tick 水位出声", () => wmVoices().length > 0);
      const voices = wmVoices();
      check("C3 周期 tick 越阈出声(warn)", voices.some((v) => v.m.includes("[水位警告]")));
      check("C3 出声带路径+水位", voices.some((v) => (v.m.includes(ws) || v.m.includes("/tmp")) && /已用 \d+\.\d+%/.test(v.m)));
      check("C3 出声带 next_step(勿自清/通报/可清项)", voices.some((v) => v.m.includes("非 lybra 数据勿自行清理") && v.m.includes("通报占用方") && v.m.includes("lybra 可清项=")));
      check("C3 journal 留痕(F15B 双写)", journalText().includes("[水位警告]"));
      check("C3 loop.log 留痕(watermark-alert)", logText().includes("watermark-alert"));
    }

    // ---------- 验收②: 同状态不重复喊 ----------
    {
      const before = wmVoices().length;
      await settleTick("C4 第一次 tick(同状态)"); // 再 tick, 阈值/水位未变 → 同路径同级别 → 降噪
      await settleTick("C4 第二次 tick(同状态)");
      check("C4 同状态不重复喊(出声数不增)", wmVoices().length === before);
      check("C4 降噪留痕(watermark-alert-suppressed)", logText().includes("watermark-alert-suppressed"));
    }

    // ---------- 验收①+②: status 处出声(先恢复 ok 再降阈 → 状态变化经 status 触发) ----------
    {
      const okBefore = okCount();
      writeFixtureSchema(80, 90, 1); // 恢复正常 → 下一 tick 状态回 ok(不出声)
      await settleTick("C5 恢复 tick 完成");
      await waitFor("状态恢复 ok(两路径)", () => okCount() >= okBefore + 2);
      writeFixtureSchema(0, 90, 1); // 再降阈 → 经 status 触发状态变化
      const st = await statusOf();
      const wmLine = wmLineOf(st);
      check("C5 status 触发出声(状态变化)", wmVoices().some((v) => v.m.includes("[水位警告]")));
      check("C5 status 行带警告标记", wmLine.includes("⚠"));
    }

    // ---------- critical 分支: 阈值压到实际水位一半以下 → 两路径均 critical ----------
    {
      const okBefore = okCount();
      writeFixtureSchema(80, 90, 1);
      await settleTick("C6 恢复 tick 完成");
      await waitFor("状态恢复 ok(critical 前置)", () => okCount() >= okBefore + 2);
      writeFixtureSchema(0, minUsage / 2, 1); // critical = min/2 → 各盘 used >= critical 恒真
      await settleTick("C6 critical tick 完成");
      await waitFor("critical 出声", () => wmVoices().some((v) => v.m.includes("[水位危急]")));
      const critVoices = wmVoices().filter((v) => v.m.includes("[水位危急]"));
      check("C6 critical 出声并追加声明句", critVoices.some((v) => v.m.includes("写入随时失败, 建议暂停领新卡")));
      check("C6 critical 同样带 next_step", critVoices.some((v) => v.m.includes("next_step: 非 lybra 数据勿自行清理")));
      check("C6 只提醒不自动停(循环未因水位停)", !allNotifies.some((n) => n.m.includes("循环停止") && n.m.includes("水位")));
      const st = await statusOf();
      check("C6 status 行带危急标记", wmLineOf(st).includes("🔴"));
      check("C6 critical 级别 voice 用 error 级", critVoices.some((v) => v.l === "error"));
    }

    // ---------- 验收①+②: 启动自检出声(独立验证检查点①; 验完还原声明) ----------
    {
      const okBefore = okCount();
      writeFixtureSchema(80, 90, 1);
      await settleTick("C7 恢复 tick 完成");
      await waitFor("状态恢复 ok(启动前置)", () => okCount() >= okBefore + 2);
      await mp.commands.lybra.handler("off", makeCtx().ctx);
      writeFixtureSchema(0, 90, 1);
      const { ctx, notifies } = makeCtx();
      await mp.commands.lybra.handler("on 1", ctx);
      await waitFor("启动自检水位出声", () => wmVoices().some((v) => v.m.includes("[水位警告]")));
      check("C7 loop-on 启动自检出声(带 next_step)", wmVoices().some((v) => v.m.includes("[水位警告]") && v.m.includes("next_step")));
      check("C7 启动自检横幅含警告标记", notifies.some((n) => n.m.includes("[水位自检]") && n.m.includes("⚠")));
      // 还原声明(验完还原 — 夹具目录本身即销毁, 此处还原为对齐验收话术)
      writeFixtureSchema(80, 90, 1);
      await mp.commands.lybra.handler("off", makeCtx().ctx);
    }

    // ---------- E2E 日志证据(贴 RETURN 用) ----------
    NOTES.push("=== E2E loop.log 水位相关行(贴 RETURN 证据) ===");
    for (const line of logText().trim().split("\n")) {
      if (line.includes("watermark")) NOTES.push(line);
    }
    NOTES.push("=== E2E voice-journal.md(贴 RETURN 证据) ===");
    for (const line of journalText().trim().split("\n")) NOTES.push(line);
  } finally {
    // 清理(失败路径也不挂进程)
    try { await mp.commands.lybra.handler("off", makeCtx().ctx); } catch { /* ignore */ }
    await new Promise<void>((r) => gateServer.close(() => r()));
    for (const k of Object.keys(process.env)) if (k.startsWith("LYBRA_")) delete process.env[k];
    for (const [k, v] of Object.entries(SAVED_ENV)) if (k.startsWith("LYBRA_")) process.env[k] = v;
    rmSync(ws, { recursive: true, force: true });
    rmSync(schemaFixtureRoot, { recursive: true, force: true });
    rmSync(cleanCwd, { recursive: true, force: true });
  }
}

// --- 汇总 ---
for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
if (NOTES.length) {
  console.log("\n--- NOTES ---");
  for (const n of NOTES) console.log(n);
}
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
