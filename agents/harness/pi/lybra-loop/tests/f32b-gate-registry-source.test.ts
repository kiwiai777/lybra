/**
 * AIPOS-F32B 专项测试 —— 自定义角色注册表单源归位: 门注册表(与凭据同源)。
 *
 * 病根(第六次纸绿): F32 修法方向对(class 匹配)但 custom_roles 读
 * <workspace>/project.json(chris 工作区为空 {})且加参数由调用方喂——
 * 角色→class 真相断成"注册表/project.json/参数"三处; 顾问预演③原样 BLOCK。
 * 角色是门级概念: class 真相只在门注册表(connection.json tokens,
 * 与凭据 projects 归属同源、与 F26C 分发类展开同一加载函数)一处。
 *
 * 夹具层(活体经 bin, 工作树 bin/lybra):
 *  A. project.json 反向假注册表(hbj-coder→auditor)不生效——门注册表赢
 *     (bin 活体证明 project.json 变体已死);
 *  B. 门注册表改 hbj-coder class→匹配跟随(翻转 BLOCK/还原绿, 验完还原);
 *  C. 源级断言: custom_roles.py 零 project.json 读取面 + 门注册表统一加载器
 *     在位 + 分发与信封解析同模块来源(单源);
 *  D. 源级断言: policy_resolver 生产入口无注册表参数(参数仅测试注入)。
 *
 * 跑法: node tests/f32b-gate-registry-source.test.ts (依赖 python3 + bin/lybra)
 */
import { readFileSync, writeFileSync, mkdirSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", "..", "..", "..");
const binLybra = join(repoRoot, "bin", "lybra");

const NOTES: string[] = [];
let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean, note?: string) {
  checks.push([name, ok]);
  if (!ok) failures++;
  if (note) NOTES.push(note);
  console.log(`${ok ? "✓" : "✗"} ${name}${note ? ` (${note})` : ""}`);
}

// 真 chris-huibojin 信封的逐形拷贝(2026-08-23 快照, 只留判定字段; expires 拉长防时效)
const POLICY_CODER = `---
record_type: owner_autonomy_policy
policy_id: pol_chris_coder_1
mode: PreAuthorized
status: active
approved_by_owner: true
owner_approval_ref: dec_pol_chris_coder_1
active_from: '2026-08-23T00:00:00Z'
expires_at: '2099-09-30T00:00:00Z'
agent_or_role: hbj-coder.chris-huibojin.kiwiai-dev
task_selector_task_mode: code
task_selector_project: chris-huibojin
task_selector_task_ids: []
max_tasks: 30
---
# Owner Autonomy Policy: pol_chris_coder_1 (F32B TS fixture copy)
`;

const DRAFT = `---
task_id: HBJ-F32B-TS-1
title: HBJ-F32B TS 夹具——门注册表单源信封解析(经bin)
project: chris-huibojin
status: pending
assigned_to: hbj-coder.chris-huibojin.kiwiai-dev
agent_instance: hbj-coder.chris-huibojin.kiwiai-dev
context_bundle: hbj-coder.chris-huibojin.kiwiai-dev
task_mode: code
task_class: simple
priority: high
created_by: advisor.chris-huibojin.kiwiai-dev
needs_owner: false
output_target: tests/(夹具)
artifact_policy: formal_write
audit: required
audit_by: hbj-auditor.chris-huibojin.kiwiai-dev
claim_policy: assigned_agent_only
model_tier: default
task_type: one_shot
polling_mode: agent_polling
report_mode: separate_doc
---
# HBJ-F32B TS 夹具(门注册表单源)

draft publish --dry-run 只认门注册表(class 真相唯一来源)。
`;

/** chris 形门拓扑夹具: home/{lybra-fx(门凭据库: hbj-coder→executor), chris-fx(发卡工作区)} */
function makeGateHome(root: string): { ws: string; registry: string } {
  const home = join(root, "gate-home");
  const ws = join(home, "chris-huibojin-fx");
  const regWs = join(home, "lybra-fx");
  for (const w of [ws, regWs]) {
    mkdirSync(join(w, "5_tasks", "queue"), { recursive: true });
    mkdirSync(join(w, ".lybra"), { recursive: true });
    writeFileSync(
      join(w, "project.json"),
      JSON.stringify(
        {
          code_repo: `/tmp/nonexistent/${w === ws ? "chris-huibojin-fx" : "lybra-fx"}`,
          config_version: 1,
          project: w === ws ? "chris-huibojin-fx" : "lybra-fx",
          registered_at: "2026-08-10T00:00:00Z",
          registered_by: "kiwi",
        },
        null,
        2,
      ) + "\n",
    );
  }
  mkdirSync(join(ws, "5_tasks", "policies"), { recursive: true });
  mkdirSync(join(ws, "5_tasks", "drafts"), { recursive: true });
  writeFileSync(join(ws, "5_tasks", "policies", "pol_chris_coder_1.md"), POLICY_CODER);
  writeFileSync(join(ws, "5_tasks", "drafts", "hbj-f32b-ts-1.md"), DRAFT);
  writeFileSync(
    join(ws, ".lybra", "connection.json"),
    JSON.stringify(
      { config_version: 1, mcp: { rpc_url: "http://127.0.0.1:7999/mcp" }, tokens: [] },
      null,
      2,
    ) + "\n",
  );
  const registry = join(regWs, ".lybra", "connection.json");
  writeFileSync(
    registry,
    JSON.stringify(
      {
        config_version: 1,
        tokens: [
          {
            agent_instance: "hbj-coder.chris-huibojin.kiwiai-dev",
            fingerprint: "sha256:f32bts0001",
            projects: ["chris-huibojin-fx"],
            projects_enforced: true,
            role: "hbj-coder",
            role_class: "executor",
            scopes: ["queue_claim", "queue_return", "task_progress"],
            token: "fx-synthetic-token-hbj-coder-f32bts",
            token_ref: "svc-hbj-coder",
          },
        ],
      },
      null,
      2,
    ) + "\n",
  );
  return { ws, registry };
}

interface PublishResult {
  verdict: string;
  blocking_reasons: string[];
  rendered_markdown?: string;
}

function runPublishDryRun(ws: string): PublishResult {
  const proc = spawnSync(
    binLybra,
    ["--workspace-root", ws, "draft", "publish", "--path", "5_tasks/drafts/hbj-f32b-ts-1.md", "--dry-run", "--json"],
    { cwd: ws, encoding: "utf-8", timeout: 120_000 },
  );
  if (proc.error) throw proc.error;
  const out = (proc.stdout || "").trim();
  if (!out) {
    throw new Error(`bin/lybra 无 stdout (status=${proc.status} stderr=${(proc.stderr || "").slice(0, 300)})`);
  }
  return JSON.parse(out) as PublishResult;
}

function flipRegistryClass(registry: string, newClass: string) {
  const data = JSON.parse(readFileSync(registry, "utf-8"));
  for (const t of data.tokens) {
    if (t.role === "hbj-coder") t.role_class = newClass;
  }
  writeFileSync(registry, JSON.stringify(data, null, 2) + "\n");
}

// ===========================================================================
// A/B. 活体经 bin(工作树实现)
// ===========================================================================
{
  const fx = mkdtempSync(join(tmpdir(), "f32b-source-"));
  try {
    // --- A. project.json 反向假注册表不生效(门注册表赢) ---
    try {
      const { ws } = makeGateHome(fx);
      const pj = join(ws, "project.json");
      const project = JSON.parse(readFileSync(pj, "utf-8"));
      project.custom_roles = { "hbj-coder": { class: "auditor" } }; // 反向假注册表
      writeFileSync(pj, JSON.stringify(project, null, 2) + "\n");
      const r = runPublishDryRun(ws);
      check(
        "A: project.json 假注册表(hbj-coder→auditor)不生效, 门注册表赢 → 非BLOCK",
        r.verdict !== "BLOCK" && (r.rendered_markdown || "").includes("pol_chris_coder_1"),
        `verdict=${r.verdict} blocking=${JSON.stringify(r.blocking_reasons)}`,
      );
    } catch (e) {
      check("A: project.json 假注册表(hbj-coder→auditor)不生效, 门注册表赢 → 非BLOCK", false, String(e));
    }

    // --- B. 门注册表改 class → 匹配跟随(翻转 BLOCK / 还原绿, 验完还原) ---
    try {
      const { ws, registry } = makeGateHome(fx);
      flipRegistryClass(registry, "auditor"); // 翻转: hbj-coder 改挂 auditor
      const flipped = runPublishDryRun(ws);
      const flippedBlocked = (flipped.blocking_reasons || []).some((b) =>
        String(b).includes("cannot resolve policy envelope"),
      );
      flipRegistryClass(registry, "executor"); // 还原
      const restored = runPublishDryRun(ws);
      check(
        "B: 门注册表改 class → 匹配跟随(翻转BLOCK信封墙/还原绿, 验完还原)",
        flipped.verdict === "BLOCK" && flippedBlocked && restored.verdict !== "BLOCK",
        `flipped=${flipped.verdict} restored=${restored.verdict}`,
      );
    } catch (e) {
      check("B: 门注册表改 class → 匹配跟随(翻转BLOCK信封墙/还原绿, 验完还原)", false, String(e));
    }
  } finally {
    rmSync(fx, { recursive: true, force: true });
  }
}

// ===========================================================================
// C/D. 源级断言 —— 单源铁律(角色→class 真相只在门注册表一处)
// ===========================================================================
{
  const crSrc = readFileSync(join(repoRoot, "tools", "aipos_cli", "custom_roles.py"), "utf-8");
  const prSrc = readFileSync(join(repoRoot, "tools", "aipos_cli", "policy_resolver.py"), "utf-8");
  const dtSrc = readFileSync(join(repoRoot, "tools", "distribute_tools.py"), "utf-8");

  check(
    "C: custom_roles.py 零 project.json 读取面(project.json 分支已删)",
    !crSrc.includes("read_project_json") && !crSrc.includes("project_json_path"),
  );
  check(
    "C: 门注册表统一加载器在位(与凭据同源: load_unified_service_role_registry)",
    crSrc.includes("load_unified_service_role_registry") &&
      crSrc.includes("connection.json"),
  );
  check(
    "C: F26C 分发与本处读同一模块来源(distribute 与 policy_resolver 均 custom_roles)",
    dtSrc.includes("from tools.aipos_cli.custom_roles import resolve_role_to_class") &&
      prSrc.includes("from tools.aipos_cli.custom_roles import load_custom_roles"),
  );
  check(
    "D: 信封解析生产入口(find_active_policy)无注册表参数(参数仅测试注入)",
    /def find_active_policy\(\s*workspace_root[^)]*\)/.test(prSrc) &&
      !/def find_active_policy\([^)]*custom_roles/.test(prSrc),
  );
}

// ===========================================================================
console.log("");
if (NOTES.length) {
  console.log("NOTES:");
  for (const n of NOTES) console.log(`  - ${n}`);
}
console.log(`f32b-gate-registry-source: ${checks.length - failures}/${checks.length} checks passed`);
if (failures > 0) {
  console.error(`FAILED: ${failures} check(s) failed`);
  process.exit(1);
}
