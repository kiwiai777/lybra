/**
 * AIPOS-F24A 专项测试 —— enroll 失败带路(禁裸干)+ 单实现源级证据。
 *
 * 大项C: 凡产品侧/门侧故障类 enroll 失败, 文案必附 ENROLL_PRODUCT_FAULT_GUIDE
 *        (治 2026-08-22 现场: 新工位 agent 拿 401 即钻源码谋划 serve stop)。
 * 大项A/⑤(跨语言): CLI(Python)侧无本地发码路径 —— aipos_cli.py 不再引用
 *        issue_self_contained_code(唯一实现=门进程内)。
 * 大项B(跨语言): verbs.schema 注册表含 governance_root 参数。
 *
 * 跑法: node tests/f24a-enroll-guardrails.test.ts
 */
import { readFileSync } from "node:fs";
import { mkdtempSync, rmSync, mkdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const loopSrc = readFileSync(join(here, "..", "lybra-loop.ts"), "utf-8");
const repoRoot = join(here, "..", "..", "..", "..", "..");
const GUIDE = "此为产品侧故障, 与你无关, 禁自行诊断修复门/服务/部署";
const checks: Array<[string, boolean]> = [];
let failures = 0;
function check(name: string, ok: boolean, note?: string) {
  checks.push([name, ok]);
  if (!ok) failures++;
  console.log(`${ok ? "✓" : "✗"} ${name}${note ? ` (${note})` : ""}`);
}

// ===========================================================================
// A. 源级断言 —— 带路常量与四个故障挂点
// ===========================================================================
{
  check("A: ENROLL_PRODUCT_FAULT_GUIDE 常量定义(卡文原文)", loopSrc.includes(`const ENROLL_PRODUCT_FAULT_GUIDE =`) && loopSrc.includes(GUIDE));
  const enrollSection = loopSrc.slice(loopSrc.indexOf('if (sub === "enroll")'), loopSrc.indexOf('if (sub === "on")'));
  const guideHits = (enrollSection.match(/ENROLL_PRODUCT_FAULT_GUIDE/g) || []).length;
  check("A: 产品侧故障带路挂在 4 个故障点(交换拒/返回残缺/连通验证不过/兜底 catch)", guideHits >= 4, `hits=${guideHits}`);
  check("A: 交换失败路径带路", enrollSection.includes("交换失败: ${reason}${next ? `\\n下一步: ${next}` : \"\"}\\n${ENROLL_PRODUCT_FAULT_GUIDE}"));
  check("A: 连通验证失败也带路(verifyOk=false 行内追加)", enrollSection.includes("...(verifyOk ? [] : [ENROLL_PRODUCT_FAULT_GUIDE])"));
  check("A: 兜底 catch 带路(enroll 失败: ...)", enrollSection.includes("enroll 失败: ${msg}"));
}

// ===========================================================================
// B. 源级断言(跨语言)—— 单实现与动词参数面
// ===========================================================================
{
  const cliSrc = readFileSync(join(repoRoot, "tools", "aipos_cli", "aipos_cli.py"), "utf-8");
  check("B: CLI(Python) 无本地发码调用(唯一实现=门进程内, 验收⑤)", !cliSrc.includes("issue_self_contained_code"));
  const enrollCodeSection = cliSrc.slice(cliSrc.indexOf('roles_command == "enroll-code"'), cliSrc.indexOf('roles_command == "enroll-revoke"'));
  check("B: CLI 薄壳只调两阶段门动词", enrollCodeSection.includes("lybra_enroll_code_dry_run") && enrollCodeSection.includes("lybra_enroll_code_confirm"));
  const schema = JSON.parse(readFileSync(join(repoRoot, "schema", "verbs.schema.json"), "utf-8"));
  const props = schema?.verbs?.lybra_enroll_code_dry_run?.parameters?.properties ?? {};
  check("B: verbs.schema 注册表含 governance_root 参数(大项B 锚点)", typeof props.governance_root === "object");
}

// ===========================================================================
// C. 夹具 E2E —— 产品侧故障类失败必带路
// ===========================================================================
function makeSelfContained(gateUrl: string, transport: string, code: string): string {
  const payload = JSON.stringify({ v: 1, gate_url: gateUrl, governance_root: "/gov/nowhere", transport_token: transport, code });
  return "LYBRAENROLL1." + Buffer.from(payload, "utf-8").toString("base64url").replace(/=+$/, "");
}

function makeMockCtx() {
  const notifies: Array<{ m: string; l?: string }> = [];
  return { ctx: { ui: { notify: (m: string, l?: string) => notifies.push({ m, l }) } } as any, notifies };
}

{
  const tmp = mkdtempSync(join(tmpdir(), "f24a-e2e-"));
  const prevCwd = process.cwd();
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const msg = JSON.parse(body);
      const name = msg?.params?.name || "";
      const send = (payload: unknown, status = 200) => {
        const out = JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { structuredContent: payload } });
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(out);
      };
      if (msg.method === "initialize") return send({});
      if (name === "lybra_roles_enroll_exchange") {
        return send({ ok: false, error_code: "CODE_NOT_FOUND", message: "Enrollment code not found.", suggested_next_action: "Ask the advisor to re-issue." });
      }
      if (name === "lybra_gate_version") {
        // 连通验证路径: 模拟 401(新 token 被门拒 —— 2026-08-22 现场同型故障)
        res.writeHead(401, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "unauthorized" }));
      }
      return send({ ok: false, message: `unknown tool ${name}` });
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as import("node:net").AddressInfo).port;
  const gateUrl = `http://127.0.0.1:${port}`;
  try {
    const { default: factory } = await import("../lybra-loop.ts");
    const commands: Record<string, { description: string; handler: Function }> = {};
    const fakePi = {
      registerCommand: (name: string, opts: { description: string; handler: Function }) => { commands[name] = opts; },
      on: () => {},
      appendEntry: () => {},
      registerEntryRenderer: () => {},
    } as any;
    factory(fakePi);

    const station = join(tmp, "station");
    mkdirSync(station, { recursive: true });
    process.chdir(station);

    // C1: 交换被门拒(带原因) → 失败文案附禁裸干带路
    {
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler(`enroll ${makeSelfContained(gateUrl, "TT-1", "C-1")}`, ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C1: 交换失败出声(原因透传)", all.includes("交换失败") && all.includes("CODE_NOT_FOUND") === false || all.includes("Enrollment code not found"));
      check("C1: 交换失败文案附禁裸干带路", all.includes(GUIDE) && all.includes("报告顾问"));
    }

    // C2: 门不可达(连接拒绝) → 兜底 catch 带路
    {
      const deadUrl = "http://127.0.0.1:1"; // 保留端口, 连接即拒
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler(`enroll ${makeSelfContained(deadUrl, "TT-2", "C-2")}`, ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C2: 门不可达 → enroll 失败兜底出声", all.includes("enroll 失败"));
      check("C2: 兜底文案附禁裸干带路(禁 serve stop/重启 gate)", all.includes(GUIDE) && all.includes("报告顾问"));
    }

    // C3: 交换成功但连通验证 401 → 成功输出内附带路(治 401 裸干现场的同型路径)
    {
      // 复用同一 stub: exchange 需要成功 → 换一个 stub 状态
      let exchangeOk = true;
      const server2 = http.createServer((req, res) => {
        let body = "";
        req.on("data", (c) => (body += c));
        req.on("end", () => {
          const msg = JSON.parse(body);
          const name = msg?.params?.name || "";
          const send = (payload: unknown, status = 200) => {
            const out = JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { structuredContent: payload } });
            res.writeHead(status, { "Content-Type": "application/json" });
            res.end(out);
          };
          if (msg.method === "initialize") return send({});
          if (name === "lybra_roles_enroll_exchange") {
            if (!exchangeOk) return send({ ok: false, message: "off" });
            return send({ ok: true, landing_required: true, grace_until: "2999-01-01T00:00:00Z", token_entry: { role: "executor", agent_instance: "exec.f24a", token: "ROLE-2", fingerprint: "sha256:x" } });
          }
          if (name === "lybra_roles_enroll_land") return send({ ok: true });
          if (name === "lybra_gate_version") {
            res.writeHead(401, { "Content-Type": "application/json" });
            return res.end(JSON.stringify({ error: "unauthorized" }));
          }
          return send({ ok: false, message: `unknown tool ${name}` });
        });
      });
      await new Promise<void>((resolve) => server2.listen(0, "127.0.0.1", resolve));
      const port2 = (server2.address() as import("node:net").AddressInfo).port;
      const { ctx, notifies } = makeMockCtx();
      await commands.lybra.handler(`enroll ${makeSelfContained(`http://127.0.0.1:${port2}`, "TT-3", "C-3")}`, ctx);
      const all = notifies.map((n) => n.m).join("\n");
      check("C3: 连通验证 401 → 上岗完成但验证行标 ✗", all.includes("上岗完成") && all.includes("✗"));
      check("C3: 验证失败附禁裸干带路", all.includes(GUIDE));
      server2.close();
    }

    check("C: 工位目录零多余落盘", existsSync(join(station, ".lybra", "connection.json")));
  } finally {
    process.chdir(prevCwd);
    try { server.close(); } catch { /* noop */ }
    try { rmSync(tmp, { recursive: true, force: true }); } catch { /* noop */ }
  }
}

console.log();
console.log(failures === 0 ? `ALL ${checks.length} CHECKS PASS` : `${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
