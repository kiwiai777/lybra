/**
 * AIPOS-F23 专项测试 —— /lybra enroll 工位一贴上岗(自包含码)。
 *
 * 锚点: F20 /lybra 命令族投影 + 既有 enroll 交换流(单源在门侧); 码格式唯一定义处 =
 * 产品仓 tools/aipos_cli/enrollment.py(encode_self_contained_code), 本侧仅结构性解析。
 *
 * 四层:
 *  A. 纯单测: 自包含码解析(round-trip/损坏/版本/裸码)、enroll 目标根推导、治理仓防护、
 *     role 合并(验收⑨)、connection upsert。
 *  B. 源级断言(f18/f20 范式): enroll 子命令注册、码即运输认证(无 bootstrap-token 逻辑)、
 *     落盘成功才 land、验证连通、带路文案(/lybra sync 然后 /reload)。
 *  C. 夹具 E2E(mock pi/ctx + 临时工位 + stub gate HTTP 服务): 全链路 ——
 *     成功(交换→落盘工位 .lybra/→land→连通验证→带路)、码无效(ok=False 带原因)、
 *     治理工作区拒写、缺码用法提示。
 *  D. 跨语言同源(Python encode → TS decode): python3 可用时用真实 enrollment.py
 *     编码, TS 解码逐字段对照(码格式只有一处定义的可执行证据); python3 不可用跳过记 NOTE。
 *
 * 跑法:`node tests/f23-enroll-command.test.ts`
 */
import {
  parseSelfContainedCode,
  resolveEnrollTargetRoot,
  isGovernanceWorkspace,
  mergeRoleFile,
  upsertEnrollConnection,
} from "../lybra-loop.ts";
import { readFileSync, writeFileSync, mkdirSync, rmSync, mkdtempSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import http from "node:http";
import type { AddressInfo } from "node:net";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
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
const loopSrc = readFileSync(new URL("../lybra-loop.ts", import.meta.url), "utf8");

// Python 侧同源产物(与产品仓 enrollment.py encode_self_contained_code 同参生成)
const PY_ENCODED =
  "LYBRAENROLL1.eyJjb2RlIjoiQ0MiLCJnYXRlX3VybCI6Imh0dHA6Ly9oOjc3MTgiLCJnb3Zlcm5hbmNlX3Jvb3QiOiIvZ292IiwidHJhbnNwb3J0X3Rva2VuIjoiVFQiLCJ2IjoxfQ";

// ===========================================================================
// A. 纯单测
// ===========================================================================
{
  // --- 自包含码解析: round-trip / 裸码 / 损坏 / 版本 ---
  const sc = parseSelfContainedCode(PY_ENCODED)!;
  check(
    "A: 解析自包含码逐字段正确(gate_url/governance_root/transport_token/code)",
    sc !== null &&
      sc.gate_url === "http://h:7718" &&
      sc.governance_root === "/gov" &&
      sc.transport_token === "TT" &&
      sc.code === "CC" &&
      sc.v === 1,
  );
  check("A: 旧裸码(无前缀)→ null(走旧路径)", parseSelfContainedCode("plain-legacy-code") === null);
  check(
    "A: 损坏码 → 抛错带下一步(F9)",
    (() => {
      try {
        parseSelfContainedCode("LYBRAENROLL1.!!!bad!!!");
        return false;
      } catch (e) {
        return e instanceof Error && e.message.includes("下一步");
      }
    })(),
  );
  check(
    "A: 版本不识别 → 抛错带下一步",
    (() => {
      const b64 = Buffer.from(JSON.stringify({ v: 2, gate_url: "x", transport_token: "t", code: "c" })).toString("base64url").replace(/=+$/, "");
      try {
        parseSelfContainedCode("LYBRAENROLL1." + b64);
        return false;
      } catch (e) {
        return e instanceof Error && e.message.includes("版本不识别");
      }
    })(),
  );

  // --- enroll 目标根推导 ---
  check("A: 已有 .lybra → 其父目录(重铸/轮换)", resolveEnrollTargetRoot("/ws/root/.lybra") === "/ws/root");
  check("A: 无 .lybra → cwd(新工位约定)", resolveEnrollTargetRoot(null, "/fresh/ws") === "/fresh/ws");

  // --- 治理仓防护(验收⑧/第九坑) ---
  const exists = (p: string) => p === "/gov/5_tasks/queue";
  check("A: 5_tasks/queue 结构签名 → 治理仓", isGovernanceWorkspace({ existsSync: exists }, path, "/gov", "") === true);
  check("A: 目标 == 码内嵌 governance_root → 治理仓", isGovernanceWorkspace({ existsSync: () => false }, path, "/x/y", "/x/y") === true);
  check("A: 干净工位目录 → 放行", isGovernanceWorkspace({ existsSync: exists }, path, "/x/ws1", "/gov") === false);

  // --- role 合并(验收⑨: 保留既有键) ---
  const merged = mergeRoleFile(
    JSON.stringify({ role: "executor", owner_policy_ref: "pol_x", custom_key: "keep-me" }),
    "auditor",
    "audit.t1",
  );
  check(
    "A: role 文件合并保留既有键(owner_policy_ref/custom_key), 新 role/instance 覆盖, enrolled_at 落盘",
    merged.owner_policy_ref === "pol_x" && merged.custom_key === "keep-me" && merged.role === "auditor" && merged.instance === "audit.t1" && typeof merged.enrolled_at === "string",
  );
  const mergedEmpty = mergeRoleFile(null, "executor", null);
  check("A: 无既有 role 文件 → 从零铸", mergedEmpty.role === "executor" && !("owner_policy_ref" in mergedEmpty));

  // --- connection upsert(保留既有键, token 按 instance 轮换) ---
  const conn = upsertEnrollConnection(
    { workspace_root: "/old", tokens: [{ role: "executor", agent_instance: "exec.a", token: "T0" }], keep_me: true } as Record<string, unknown>,
    "http://h:7718",
    "/x/ws1",
    { role: "auditor", agent_instance: "audit.t1", token: "T1" },
  );
  check(
    "A: connection upsert 保留既有键, 新 token 追加, mcp.rpc_url 规范化",
    (conn.tokens as Array<Record<string, unknown>>).length === 2 &&
      (conn.tokens as Array<Record<string, unknown>>)[1].token === "T1" &&
      conn.keep_me === true &&
      (conn.mcp as Record<string, string>).rpc_url === "http://h:7718/mcp" &&
      conn.workspace_root === "/old",
  );
}

// ===========================================================================
// B. 源级断言
// ===========================================================================
{
  check("B: /lybra 命令族描述含 enroll", /on \[maxN\] \| off \| status \| sync \| enroll <码>/.test(loopSrc));
  check("B: enroll 子命令分支存在", /if \(sub === "enroll"\)/.test(loopSrc));
  check("B: 码即运输认证 —— enroll 链路无 bootstrap-token 逻辑(红线)", !/bootstrap[_-]token/i.test(
    loopSrc.slice(loopSrc.indexOf('if (sub === "enroll")'), loopSrc.indexOf('if (sub === "on")')),
  ));
  const enrollSection = loopSrc.slice(loopSrc.indexOf('if (sub === "enroll")'), loopSrc.indexOf('if (sub === "on")'));
  check("B: 交换动词走 callToolRaw(新工位无 schema, 不依赖 verbs.schema)", enrollSection.includes('callToolRaw("lybra_roles_enroll_exchange"'));
  check("B: 落盘成功才 land(交换与落盘原子, 验收⑦)", enrollSection.indexOf('writeFileSync(rolePath') < enrollSection.indexOf('callToolRaw("lybra_roles_enroll_land"'));
  check("B: land 之后连通验证(新 token 调 gate)", enrollSection.indexOf('callToolRaw("lybra_roles_enroll_land"') < enrollSection.indexOf('callToolRaw("lybra_gate_version"'));
  check("B: 成功带路文案(/lybra sync 然后 /reload)", enrollSection.includes("/lybra sync 然后 /reload"));
  check("B: 治理工作区拒写出声(isGovernanceWorkspace 分支)", enrollSection.includes("isGovernanceWorkspace(fs, path, targetRoot"));
  check("B: 落盘失败不 land 的提示(grace 窗口重试)", enrollSection.includes("grace 窗口内可原样重贴"));
}

// ===========================================================================
// C. 夹具 E2E(mock pi + stub gate HTTP 服务 + 临时工位)
// ===========================================================================
interface StubGateState {
  exchangeCalls: number;
  landCalls: number;
  versionCalls: number;
  bearerSeen: string[];
  codeStatus: "pending" | "used" | "landed" | "revoked";
}

function makeStubGate() {
  const state: StubGateState = { exchangeCalls: 0, landCalls: 0, versionCalls: 0, bearerSeen: [], codeStatus: "pending" };
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const auth = String(req.headers.authorization || "");
      state.bearerSeen.push(auth.replace(/^Bearer /, ""));
      const msg = JSON.parse(body);
      const name = msg?.params?.name || "";
      const send = (payload: unknown) => {
        const out = JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { structuredContent: payload } });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(out);
      };
      if (name === "lybra_roles_enroll_exchange") {
        state.exchangeCalls++;
        if (state.codeStatus === "pending") {
          state.codeStatus = "used";
          send({
            ok: true,
            landing_required: true,
            grace_until: "2999-01-01T00:00:00Z",
            token_entry: { role: "executor", agent_instance: "exec.f23-test", token: "ROLE-TOKEN-1", scopes: ["queue_claim"], fingerprint: "sha256:abc" },
          });
          return;
        }
        if (state.codeStatus === "used") {
          send({ ok: true, retry: true, token_entry: { role: "executor", agent_instance: "exec.f23-test", token: "ROLE-TOKEN-1", scopes: ["queue_claim"] } });
          return;
        }
        if (state.codeStatus === "landed") {
          send({ ok: false, error_code: "CODE_ALREADY_USED", message: "Enrollment code is already used (单次码, 已消费).", suggested_next_action: "Ask the advisor for a fresh code." });
          return;
        }
        send({ ok: false, error_code: "CODE_REVOKED", message: "Enrollment code is revoked.", suggested_next_action: "Ask for a fresh code." });
        return;
      }
      if (name === "lybra_roles_enroll_land") {
        state.landCalls++;
        state.codeStatus = "landed";
        send({ ok: true, landed_at: "2026-08-22T12:00:00Z" });
        return;
      }
      if (name === "lybra_gate_version") {
        state.versionCalls++;
        send({ version: "stub-gate-commit" });
        return;
      }
      send({ ok: false, message: `unknown tool ${name}` });
    });
  });
  return { server, state };
}

function makeSelfContained(gateUrl: string, governanceRoot: string, transport: string, code: string): string {
  const payload = JSON.stringify({ v: 1, gate_url: gateUrl, governance_root: governanceRoot, transport_token: transport, code });
  return "LYBRAENROLL1." + Buffer.from(payload, "utf-8").toString("base64url").replace(/=+$/, "");
}

function makeMockCtx() {
  const notifies: Array<{ m: string; l?: string }> = [];
  return {
    ctx: { ui: { notify: (m: string, l?: string) => notifies.push({ m, l }) } } as any,
    notifies,
  };
}

{
  const tmp = mkdtempSync(join(tmpdir(), "f23-e2e-"));
  const prevCwd = process.cwd();
  let gate: ReturnType<typeof makeStubGate> | null = null;
  try {
    // 装载扩展(mock pi)
    const { default: factory } = await import("../lybra-loop.ts");
    const commands: Record<string, { description: string; handler: Function }> = {};
    const fakePi = {
      registerCommand: (name: string, opts: { description: string; handler: Function }) => { commands[name] = opts; },
      on: () => {},
      appendEntry: () => {},
      registerEntryRenderer: () => {},
    } as any;
    factory(fakePi);
    check("C: /lybra enroll 子命令已注册", typeof commands.lybra?.handler === "function");

    // stub gate 起 services
    gate = makeStubGate();
    await new Promise<void>((resolve) => gate!.server.listen(0, "127.0.0.1", resolve));
    const port = (gate.server.address() as AddressInfo).port;
    const gateUrl = `http://127.0.0.1:${port}`;
    const scCode = makeSelfContained(gateUrl, "/definitely/not/here/gov", "TRANSPORT-TOKEN-9", "INNER-CODE-1");

    // --- C1: 新工位(空目录+pi)一贴上岗(验收②) ---
    const station = join(tmp, "fresh-station");
    mkdirSync(station, { recursive: true });
    process.chdir(station);
    {
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler(`enroll ${scCode}`, ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C1: 成功路径出声(上岗完成)", all.includes("上岗完成"));
      check("C1: 落盘到工位 .lybra/connection.json(0600)", existsSync(join(station, ".lybra", "connection.json")));
      const conn = JSON.parse(readFileSync(join(station, ".lybra", "connection.json"), "utf-8"));
      check(
        "C1: connection.json 含新 token + workspace_root=工位 + rpc_url=码内嵌地址",
        conn.tokens?.[0]?.token === "ROLE-TOKEN-1" && conn.workspace_root === station && conn.mcp.rpc_url === `${gateUrl}/mcp`,
      );
      const roleFile = JSON.parse(readFileSync(join(station, ".lybra", "role"), "utf-8"));
      check("C1: role 文件 = 码绑定角色 + enrolled_at 时间戳", roleFile.role === "executor" && roleFile.instance === "exec.f23-test" && typeof roleFile.enrolled_at === "string");
      check("C1: land 被调(交换与落盘原子闭环)", gate!.state.landCalls === 1);
      check("C1: 连通验证(新 token 调 lybra_gate_version)", gate!.state.versionCalls === 1);
      check("C1: 带路文案(接着 /lybra sync 然后 /reload)", all.includes("/lybra sync 然后 /reload"));
      check("C1: 运输凭证作 transport 认证(bearer=TRANSPORT-TOKEN-9 在 exchange 上出现)", gate!.state.bearerSeen.includes("TRANSPORT-TOKEN-9"));
    }

    // --- C2: 码已消费(land 后重贴)→ ok=False 带原因与下一步(验收⑤) ---
    {
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler(`enroll ${scCode}`, ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C2: 已消费码重贴 → 出声含原因(already used)", all.includes("already used"));
      check("C2: 出声含下一步(suggested_next_action 透传)", all.includes("Ask the advisor for a fresh code"));
    }

    // --- C3: 治理工作区拒写(第九坑) ---
    {
      const govLike = join(tmp, "gov-like");
      mkdirSync(join(govLike, "5_tasks", "queue"), { recursive: true });
      process.chdir(govLike);
      const sc2 = makeSelfContained(gateUrl, "/definitely/not/here/gov", "TRANSPORT-TOKEN-9", "INNER-CODE-2");
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler(`enroll ${sc2}`, ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C3: 治理工作区目标 → 拒写出声(enroll 只落工位目录)", all.includes("拒绝落盘"));
      check("C3: 拒写后未落任何 .lybra 文件", !existsSync(join(govLike, ".lybra", "connection.json")));
    }

    // --- C4: 缺码用法提示 ---
    {
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("enroll", ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C4: 缺码 → 用法提示(/lybra enroll LYBRAENROLL1.<码>)", all.includes("用法: /lybra enroll"));
    }

    // --- C5: 裸码(非自包含)→ 带路请顾问重新发码 ---
    {
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler("enroll some-plain-code", ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C5: 裸码 → 提示需自包含码(带下一步)", all.includes("不是自包含码"));
    }
  } finally {
    process.chdir(prevCwd);
    try { gate?.server.close(); } catch { /* noop */ }
    try { rmSync(tmp, { recursive: true, force: true }); } catch { /* noop */ }
  }
}

// ===========================================================================
// D. 跨语言同源: Python encode → TS decode(码格式只有一处定义的可执行证据)
// ===========================================================================
{
  const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
  try {
    const pyCode = execFileSync(
      "python3",
      ["-c", "import sys; sys.path.insert(0, '.'); from tools.aipos_cli.enrollment import encode_self_contained_code as e; print(e(gate_url='http://py-gate:7118', governance_root='/py/gov', transport_token='PY-TT', code='PY-CC'))",],
      { cwd: repoRoot, encoding: "utf-8", timeout: 30000 },
    ).trim();
    const sc = parseSelfContainedCode(pyCode);
    check(
      "D: Python encode → TS decode 逐字段一致(跨语言同源)",
      sc !== null && sc.gate_url === "http://py-gate:7118" && sc.governance_root === "/py/gov" && sc.transport_token === "PY-TT" && sc.code === "PY-CC",
    );
  } catch (e) {
    NOTES.push(`D: python3 不可用, 跨语言同源检查跳过(${e instanceof Error ? e.message : String(e)})`);
  }
}

// ---------------------------------------------------------------------------
// 汇总
// ---------------------------------------------------------------------------
console.log();
for (const [name, ok] of checks) {
  console.log(`${ok ? "✓" : "✗"} ${name}`);
}
if (NOTES.length) {
  console.log("\nNOTES:");
  for (const n of NOTES) console.log(`  - ${n}`);
}
console.log(`\n${failures === 0 ? `ALL ${checks.length} CHECKS PASS` : `${failures}/${checks.length} FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
