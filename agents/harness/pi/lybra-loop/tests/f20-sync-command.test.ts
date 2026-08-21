/**
 * AIPOS-F20 专项测试 —— /lybra sync 薄壳投影既有 lybra sync CLI(工位拉新不出 pi)。
 *
 * 锚点: 连接器 /lybra 命令族 + 既有 lybra sync CLI(C4B 建, 分发单源), Δ=0 薄壳投影。
 *
 * 四层:
 *  A. 纯单测: harness root 推导(.lybra → 工位根)、bin 解析三层(声明键优先/缺省探测/均不可得→null)、
 *     stdout 尾行提取。
 *  B. 源级断言(f18 范式): sync 子命令存在且走子进程(execFileSync sync --harness-root)、
 *     薄壳红线(禁第二遍实现同步逻辑 — 无 fetch/写文件循环)、尾行入 voice persistent=true、
 *     成功后 /reload 提示、bin 不可得出声带路(known-debt)、F18 文案已更新。
 *  C. 夹具 E2E(mock pi/ctx + 临时工位 + stub bin 脚本): 全链路跑通 ——
 *     成功(输出透传+尾行 voice+appendEntry 持久+/reload 提示)、失败(stderr 透传+如实失败)、
 *     bin 不可得(出声带路)、已最新(stub 输出 0 files 原样透传不误伤)。
 *  D. 真实 CLI 狗粮(可选, 需本机 bin + gate 在线): 用真实 resolveLybraBin 解析的 bin 对
 *     临时工位真跑一次 sync, 验证薄壳投影对真 CLI 成立(bin/gate 不可得时跳过并记 NOTE,
 *     不影响套件判定 —— 真狗粮实录见任务卡 RETURN)。
 *
 * 跑法:`node tests/f20-sync-command.test.ts`
 */
import {
  resolveSyncHarnessRoot,
  resolveLybraBin,
  extractSyncTailLine,
} from "../lybra-loop.ts";
import { ConnectionResolver } from "../loop-context.ts";
import { readFileSync, writeFileSync, mkdirSync, rmSync, mkdtempSync, existsSync, chmodSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const NOTES: string[] = [];
let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean, note?: string) {
  checks.push([name, ok]);
  if (!ok) failures++;
  if (note) NOTES.push(note);
}

const fs = await import("node:fs");
const path = await import("node:path");
const loopSrc = readFileSync(new URL("../lybra-loop.ts", import.meta.url), "utf8");

// ===========================================================================
// A. 纯单测
// ===========================================================================
{
  // --- harness root 推导: .lybra 所在目录 = 工位根 ---
  check("A: resolveSyncHarnessRoot(.lybra 路径) → 其父目录", resolveSyncHarnessRoot("/ws/root/.lybra") === "/ws/root");
  check("A: resolveSyncHarnessRoot(null) → null(发现失败如实返回)", resolveSyncHarnessRoot(null) === null);

  // --- stdout 尾行提取 ---
  check(
    "A: extractSyncTailLine 取最后一个非空行",
    extractSyncTailLine("sync ok · role=executor\n  distributions checked: 4, files fetched: 0\n  下一步: /reload 让新扩展/技能生效\n") === "下一步: /reload 让新扩展/技能生效",
  );
  check("A: extractSyncTailLine 空输出 → 空串", extractSyncTailLine("") === "");
  check("A: extractSyncTailLine 尾部空行/空白不计", extractSyncTailLine("only-line\n\n  \n") === "only-line");
}

// --- bin 解析三层(临时夹具) ---
{
  const tmp = mkdtempSync(join(tmpdir(), "f20-bin-"));
  try {
    // 夹具: 工位 .lybra/connection.json + 治理 workspace/project.json{code_repo} + 假 bin
    const ws = join(tmp, "ws");
    const gov = join(tmp, "gov");
    const prod = join(tmp, "prod");
    mkdirSync(join(ws, ".lybra"), { recursive: true });
    mkdirSync(gov, { recursive: true });
    mkdirSync(join(prod, ".deploy/current/bin"), { recursive: true });
    const probedBin = join(prod, ".deploy/current/bin/lybra");
    writeFileSync(probedBin, "#!/bin/sh\necho stub\n", "utf-8");
    chmodSync(probedBin, 0o755);
    const declaredBin = join(tmp, "declared-lybra");
    writeFileSync(declaredBin, "#!/bin/sh\necho declared\n", "utf-8");
    chmodSync(declaredBin, 0o755);
    writeFileSync(join(gov, "project.json"), JSON.stringify({ code_repo: prod }), "utf-8");

    // ① 声明键优先(存在才用)
    writeFileSync(
      join(ws, ".lybra", "connection.json"),
      JSON.stringify({ lybra_bin: declaredBin, workspace_root: gov }),
      "utf-8",
    );
    let r = resolveLybraBin(fs, path, { lybraDir: join(ws, ".lybra") });
    check("A: bin 解析①声明键 lybra_bin 优先", r.bin === declaredBin && r.source.includes("lybra_bin"));

    // ①' 声明键指向不存在的文件 → 落到缺省探测(如实记录 tried)
    writeFileSync(
      join(ws, ".lybra", "connection.json"),
      JSON.stringify({ lybra_bin: join(tmp, "no-such-bin"), workspace_root: gov }),
      "utf-8",
    );
    r = resolveLybraBin(fs, path, { lybraDir: join(ws, ".lybra") });
    check(
      "A: bin 解析①'声明键文件不存在 → 缺省探测接管且 tried 留痕",
      r.bin === probedBin && r.tried.length === 1 && r.tried[0].includes("no-such-bin"),
    );

    // ② project.json#code_repo 探测(无声明键)
    writeFileSync(
      join(ws, ".lybra", "connection.json"),
      JSON.stringify({ workspace_root: gov }),
      "utf-8",
    );
    r = resolveLybraBin(fs, path, { lybraDir: join(ws, ".lybra") });
    check("A: bin 解析② code_repo → .deploy/current/bin/lybra", r.bin === probedBin && r.source.includes("project.json#code_repo"));

    // ②' 显式 workspaceRoot 参数优先于 connection.json 补读
    r = resolveLybraBin(fs, path, { workspaceRoot: gov, lybraDir: join(ws, ".lybra") });
    check("A: bin 解析②' 显式 workspaceRoot 生效", r.bin === probedBin);

    // ③ 均不可得 → null + tried 记录探测点(空 gov 无 project.json, 缺省探测也不命中)
    const emptyGov = join(tmp, "empty-gov");
    mkdirSync(emptyGov, { recursive: true });
    writeFileSync(
      join(ws, ".lybra", "connection.json"),
      JSON.stringify({ workspace_root: emptyGov }),
      "utf-8",
    );
    r = resolveLybraBin(fs, path, { lybraDir: join(ws, ".lybra") });
    check(
      "A: bin 解析③ 均不可得 → null(如实失败, tried 记探测点)",
      r.bin === null && r.tried.length >= 1 && r.tried.some((t) => t.endsWith(".deploy/current/bin/lybra")),
    );
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

// ===========================================================================
// B. 源级断言(f18 范式)
// ===========================================================================
{
  // ① 薄壳投影: 子进程调用既有 CLI, 参数即 sync --harness-root
  check(
    "B: 薄壳投影 — execFileSync(bin, [\"sync\", \"--harness-root\", harnessRoot])",
    /execFileSync\(binRes\.bin, \["sync", "--harness-root", harnessRoot\]/.test(loopSrc),
  );
  // ② 禁第二遍实现同步逻辑: sync 处理块内无 fetch/http 调用、无逐文件写循环(只透传)
  const syncBlock = loopSrc.slice(loopSrc.indexOf('if (sub === "sync")'), loopSrc.indexOf('if (sub === "on")'));
  check(
    "B: 薄壳红线 — sync 块内无 fetch/http(第二遍实现同步逻辑)",
    !/fetch\(|http\.request|https\.request/.test(syncBlock),
  );
  check(
    "B: 薄壳红线 — sync 块内不写分发文件(输出只透传上屏/voice)",
    !/writeFileSync|appendFileSync/.test(syncBlock),
  );
  // ③ 尾行入 voice(persistent=true)
  check(
    "B: stdout 尾行入 voice persistent=true",
    /const tail = extractSyncTailLine\(String\(stdout\)\);[\s\S]{0,120}voice\(tail, "info", true\)/.test(syncBlock),
  );
  // ④ 成功后提示 /reload(不做 /reload 自动化)
  check(
    "B: 成功后提示 请 /reload 生效",
    /voice\("sync 完成: 请 \/reload 生效", "info", true\)/.test(syncBlock),
  );
  check(
    "B: 不做 /reload 自动化 — sync 块内无 sendUserMessage/sendKey 等触发 reload",
    !/sendUserMessage|reloadCommand|\/reload['"]\s*,/.test(syncBlock),
  );
  // ⑤ bin 不可得 → 出声带路(known-debt)+ 如实失败(error 级)
  check(
    "B: bin 不可得出声带路(远程工位分发通道见 known-debt)",
    /本机未装 lybra CLI[\s\S]{0,200}known-debt/.test(syncBlock) && /voice\(msg, "error", true\)/.test(syncBlock),
  );
  // ⑥ 失败路径: 子进程输出尾行透传(复用 subprocessFailureTail)+ error 级如实失败
  check(
    "B: 失败透传子进程输出尾行 + error 如实失败",
    /subprocessFailureTail\(e, 8\)[\s\S]{0,300}voice\(msg, "error", true\)/.test(syncBlock),
  );
  // ⑦ F18 文案已更新(两处 voice + 两处 notify 文本行)
  check(
    "B: F18 落后 warn 文案 = /lybra sync 后 /reload(voice 两处)",
    (loopSrc.match(/voice\(`分发落后\(本地\$\{[^}]+\}\/线上\$\{[^}]+\}\), \/lybra sync 后 \/reload`, "warn", true\)/g) || []).length === 2,
  );
  check(
    "B: F18 落后 notify 文案 = /lybra sync 后 /reload(两处)",
    (loopSrc.match(/— \/lybra sync 后 \/reload`/g) || []).length === 2,
  );
  check(
    "B: 旧文案已退役(不再出现 请 lybra sync + /reload)",
    !loopSrc.includes("请 lybra sync + /reload"),
  );
  // ⑧ 用法/描述含 sync
  check(
    "B: 命令用法串含 /lybra sync",
    loopSrc.includes("用法:/lybra on [maxN] | /lybra off | /lybra status | /lybra sync"),
  );
}

// ===========================================================================
// C. 夹具 E2E(mock pi/ctx + 临时工位 + stub bin 脚本)
// ===========================================================================
{
  const tmp = mkdtempSync(join(tmpdir(), "f20-e2e-"));
  const logPath = join(tmp, "loop.log");
  process.env.LYBRA_LOOP_LOG = logPath; // 隔离 voice-journal 写入
  const savedCwd = process.cwd();
  const savedEnv = { ...process.env };
  try {
    // 夹具布局: ws(工位, cwd)/.lybra/{role, connection.json(lybra_bin=stub)};
    // gov(治理 workspace) 有 project.json{code_repo=prod}; prod 有探测位 stub bin(备用层)。
    const ws = join(tmp, "ws");
    const gov = join(tmp, "gov");
    const prod = join(tmp, "prod");
    mkdirSync(join(ws, ".lybra"), { recursive: true });
    mkdirSync(gov, { recursive: true });
    mkdirSync(join(prod, ".deploy/current/bin"), { recursive: true });
    writeFileSync(join(ws, ".lybra", "role"), JSON.stringify({ role: "executor", instance: "exec.test", owner_policy_ref: "pol_t" }), "utf-8");
    writeFileSync(join(gov, "project.json"), JSON.stringify({ code_repo: prod }), "utf-8");

    // stub bin: 记录 argv 供断言(每行一参); 成功态输出与真实 CLI 同构
    const argvFile = join(tmp, "argv.txt");
    const stubOk = join(tmp, "stub-ok");
    writeFileSync(
      stubOk,
      [
        "#!/bin/sh",
        `for a in "$@"; do echo "$a" >> ${JSON.stringify(argvFile)}; done`,
        `echo 'sync ok · role=executor · product_commit=deadbeef'`,
        `echo '  harness: '$2`,
        `echo '  distributions checked: 4, files fetched: 0'`,
        `echo '  下一步: /reload 让新扩展/技能生效'`,
        `exit 0`,
      ].join("\n"),
      "utf-8",
    );
    chmodSync(stubOk, 0o755);
    const stubFail = join(tmp, "stub-fail");
    writeFileSync(
      stubFail,
      ["#!/bin/sh", `echo 'sync failed: gate unreachable' >&2`, `exit 3`].join("\n"),
      "utf-8",
    );
    chmodSync(stubFail, 0o755);
    // 探测位 stub(供无声明键场景)
    const probedBin = join(prod, ".deploy/current/bin/lybra");
    writeFileSync(probedBin, `#!/bin/sh\nexec ${JSON.stringify(stubOk)} "$@"\n`, "utf-8");
    chmodSync(probedBin, 0o755);

    // 装载扩展(mock pi 记录 appendEntry → 验证持久通道)
    const { default: factory } = await import("../lybra-loop.ts");
    const entries: Array<{ type: string; data: any }> = [];
    const commands: Record<string, { description: string; handler: Function }> = {};
    const fakePi = {
      registerCommand: (name: string, opts: { description: string; handler: Function }) => { commands[name] = opts; },
      on: () => {},
      appendEntry: (type: string, data: any) => entries.push({ type, data }),
      registerEntryRenderer: () => {},
    } as any;
    factory(fakePi);

    function makeMockCtx() {
      const notifies: Array<{ m: string; l?: string }> = [];
      return {
        ctx: { ui: { notify: (m: string, l?: string) => notifies.push({ m, l }) }, sessionManager: { getSessionId: () => "f20-sess" } } as any,
        notifies,
      };
    }

    // --- C1: 声明键 bin + 成功路径(输出透传+尾行 voice+持久 entry+/reload 提示+argv 正确) ---
    writeFileSync(join(ws, ".lybra", "connection.json"), JSON.stringify({ lybra_bin: stubOk, workspace_root: gov }), "utf-8");
    process.chdir(ws); // discoverLybraDir 从 cwd 向上找 .lybra
    {
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("sync", ctx);
      // argv 薄壳投影: sync --harness-root <ws>(每行一参)
      const argv = readFileSync(argvFile, "utf-8").trim().split("\n");
      check("C1: 子进程 argv = [sync, --harness-root, 工位根]", JSON.stringify(argv) === JSON.stringify(["sync", "--harness-root", ws]));
      // 前置行(bin 来源自曝)
      check("C1: 前置行透传 bin 与 harness root", notifies.some((n) => n.m.includes("sync --harness-root") && n.m.includes(stubOk)));
      // 原样透传全文
      check("C1: 输出原样透传(全文上屏)", notifies.some((n) => n.m.includes("sync ok · role=executor") && n.m.includes("files fetched: 0")));
      // 尾行入 voice persistent → appendEntry 持久通道
      check(
        "C1: stdout 尾行入持久 entry(lybra-voice)",
        entries.some((e) => e.type === "lybra-voice" && String(e.data?.text || "").includes("下一步: /reload 让新扩展/技能生效")),
      );
      // 成功后 /reload 提示(voice persistent)
      check(
        "C1: 成功后提示 请 /reload 生效(持久 entry)",
        entries.some((e) => e.type === "lybra-voice" && String(e.data?.text || "").includes("sync 完成: 请 /reload 生效")),
      );
      // 无 error 级 notify(成功不误伤)
      check("C1: 成功路径无 error 级 notify", !notifies.some((n) => n.l === "error"));
    }

    // --- C2: 已最新(files fetched: 0)原样透传, 不误伤 ---
    {
      entries.length = 0;
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("sync", ctx);
      check(
        "C2: 已最新 → 透传 'files fetched: 0' 不误伤(无 error)",
        notifies.some((n) => n.m.includes("files fetched: 0")) && !notifies.some((n) => n.l === "error"),
      );
    }

    // --- C3: bin 失败路径(stderr 透传 + error 如实失败) ---
    writeFileSync(join(ws, ".lybra", "connection.json"), JSON.stringify({ lybra_bin: stubFail, workspace_root: gov }), "utf-8");
    {
      entries.length = 0;
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("sync", ctx);
      const err = notifies.find((n) => n.l === "error");
      check("C3: 失败 → error 级 notify 含子进程输出尾行", !!err && err.m.includes("sync failed: gate unreachable"));
      check("C3: 失败 → 持久 entry(error)", entries.some((e) => e.type === "lybra-voice" && e.data?.level === "error"));
    }

    // --- C4: bin 不可得(声明键指向不存在 + 探测点不命中)→ 出声带路如实失败 ---
    writeFileSync(
      join(ws, ".lybra", "connection.json"),
      JSON.stringify({ lybra_bin: join(tmp, "no-such"), workspace_root: join(tmp, "empty-gov") }),
      "utf-8",
    );
    mkdirSync(join(tmp, "empty-gov"), { recursive: true });
    {
      entries.length = 0;
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("sync", ctx);
      const err = notifies.find((n) => n.l === "error");
      check(
        "C4: bin 不可得 → 出声带路(本机未装 lybra CLI + known-debt)",
        !!err && err.m.includes("本机未装 lybra CLI") && err.m.includes("known-debt"),
      );
      check("C4: bin 不可得 → 如实失败不调子进程(无 [sync] 前置行)", !notifies.some((n) => n.m.startsWith("[sync]")));
      check("C4: bin 不可得 → 持久 entry(error)", entries.some((e) => e.type === "lybra-voice" && e.data?.level === "error"));
    }

    // --- C5: 无声明键 → 缺省探测层接管(code_repo → .deploy/current/bin/lybra) ---
    writeFileSync(join(ws, ".lybra", "connection.json"), JSON.stringify({ workspace_root: gov }), "utf-8");
    {
      entries.length = 0;
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("sync", ctx);
      check(
        "C5: 无声明键 → 探测位 bin 接管(来源自曝 project.json#code_repo)",
        notifies.some((n) => n.m.includes("[sync]") && n.m.includes("project.json#code_repo")),
      );
      check("C5: 探测位 stub 成功透传", notifies.some((n) => n.m.includes("sync ok · role=executor")));
    }

    // --- C6: 无 .lybra(身份声明缺失)→ 无法推得 harness root, 如实失败 ---
    {
      entries.length = 0;
      const noLybraDir = join(tmp, "bare");
      mkdirSync(noLybraDir, { recursive: true });
      process.chdir(noLybraDir);
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("sync", ctx);
      const err = notifies.find((n) => n.l === "error");
      check("C6: 无 .lybra → 如实失败(未发现工位 .lybra)", !!err && err.m.includes("未发现工位 .lybra"));
    }
  } finally {
    process.chdir(savedCwd);
    for (const k of Object.keys(process.env)) if (k.startsWith("LYBRA_")) delete process.env[k];
    Object.assign(process.env, savedEnv);
    rmSync(tmp, { recursive: true, force: true });
  }
}

// ===========================================================================
// D. 真实 CLI 狗粮(可选: 本机 bin + gate 在线才跑; 跳过记 NOTE 不判失败)
//    完整真狗粮实录(真工位 pi 内 /lybra sync)见任务卡 RETURN —— 验收①⑤证据。
// ===========================================================================
{
  const tmp = mkdtempSync(join(tmpdir(), "f20-dogfood-"));
  const savedCwd = process.cwd();
  try {
    // 用与连接器同源的发现逻辑找真 bin(工位 .lybra 优先, 找不到再试仓内探测点)
    const lybraDir = ConnectionResolver.discoverLybraDir();
    const realBinRes = resolveLybraBin(fs, path, { lybraDir });
    let bin = realBinRes.bin;
    if (!bin) {
      // 仓内探测点(测试进程从产品仓 tests/ 目录起跑时命中)
      const repoProbe = join(dirname(dirname(dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url))))))), ".deploy/current/bin/lybra");
      if (existsSync(repoProbe)) bin = repoProbe;
    }
    const gateUp = await (async () => {
      const net = await import("node:net");
      return await new Promise<boolean>((resolve) => {
        const s = net.createConnection({ host: "127.0.0.1", port: 7118, timeout: 1500 });
        s.on("connect", () => { s.destroy(); resolve(true); });
        s.on("error", () => resolve(false));
        s.on("timeout", () => { s.destroy(); resolve(false); });
      });
    })();
    if (!bin || !gateUp) {
      NOTES.push(`D: 真实 CLI 狗粮跳过(bin=${bin ? "有" : "无"}, gate=${gateUp ? "up" : "down"}) — 真狗粮实录见 RETURN`);
    } else {
      // 临时工位 + 真 CLI 全链路: exit 0 + 尾行提取 + /reload 提示构造。
      // 凭据红线: 不读/不显/不硬编码 token —— 把产品仓 .lybra/connection.json
      // 字节级 opaque 拷入临时工位(CLI 自解析 token), 用完随 tmp 一并删除。
      const ws = join(tmp, "ws");
      mkdirSync(join(ws, ".lybra"), { recursive: true });
      writeFileSync(join(ws, ".lybra", "role"), JSON.stringify({ role: "executor", instance: "exec.test", owner_policy_ref: "pol_t" }), "utf-8");
      const repoRoot = dirname(dirname(dirname(dirname(bin)))); // bin → current → .deploy → 仓根
      copyFileSync(join(repoRoot, ".lybra", "connection.json"), join(ws, ".lybra", "connection.json"));
      const { execFileSync } = await import("node:child_process");
      // 身份/token 由 CLI 从临时工位 .lybra 自解析(测试不经手凭据);
      // --harness-root 指向临时工位, 分布/charter/manifest 全落 tmp(零外溢)。
      const stdout = execFileSync(bin, ["sync", "--harness-root", ws], { encoding: "utf-8", stdio: "pipe", timeout: 180000 });
      check("D: 真 CLI sync exit 0 且输出含 sync ok", stdout.includes("sync ok"));
      const tail = extractSyncTailLine(stdout);
      check("D: 尾行 = /reload 指引", tail.includes("/reload"));
      check("D: 分布落盘可证(临时工位 _distributed 生成)", existsSync(join(tmp, "_distributed")));
    }
  } catch (e) {
    check("D: 真实 CLI 狗粮异常", false, String(e instanceof Error ? e.message : e));
  } finally {
    process.chdir(savedCwd);
    rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// 汇总
// ---------------------------------------------------------------------------
console.log("========================================================");
console.log(" AIPOS-F20 夹具: /lybra sync 薄壳投影既有 sync CLI");
console.log("========================================================");
for (const [name, ok] of checks) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
}
if (NOTES.length) {
  console.log("---- notes ----");
  for (const n of NOTES) console.log("  · " + n);
}
console.log("--------------------------------------------------------");
if (failures === 0) {
  console.log(`ALL ${checks.length} PASS`);
  process.exit(0);
} else {
  console.log(`${failures} FAILED / ${checks.length}`);
  process.exit(1);
}
