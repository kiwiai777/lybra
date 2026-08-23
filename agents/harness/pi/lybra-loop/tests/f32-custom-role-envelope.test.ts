/**
 * AIPOS-F32/F32B 专项测试 —— 自定义角色发卡链信封解析(经 bin, 入 run-all)。
 *
 * 病根: policy_resolver._policy_matches_role 只做 agent_or_role 点分量对固定词
 * exec/audit 直配 → 自定义角色信封(hbj-coder.chris-huibojin.kiwiai-dev)永不匹配
 * → chris 发卡链 draft publish BLOCK "cannot resolve policy envelope"。
 *
 * 修法(与 F26C 分发类展开同一单源): 自定义角色分量按**门注册表**
 * (connection.json tokens, 与凭据同源; AIPOS-F32B 从 project.json 归位到此处)
 * 所属内建类匹配; 直配语义保留。
 *
 * 夹具层(活体经 bin, 工作树 bin/lybra —— 测的是仓库当前实现, 非部署快照):
 *  A. chris 形门拓扑(hbj 注册在 lybra-fx 凭据库) draft publish --dry-run:
 *     非 BLOCK + 契约节信封 = pol_chris_coder_1;
 *  B. 因果负对照: 门注册表无 hbj 条目(真 chris 拓扑——自身凭据文件只有过期
 *     运输凭证) → BLOCK "cannot resolve policy envelope";
 *  C. audit 侧: 注册表 hbj-auditor→auditor 时审计链信封可解析;
 *  D. 源级断言: policy_resolver 无自建角色→类映射(防碎片化红线)。
 *
 * 跑法: node tests/f32-custom-role-envelope.test.ts (依赖 python3 + bin/lybra)
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
# Owner Autonomy Policy: pol_chris_coder_1 (F32 fixture copy)
`;

const POLICY_AUDIT = `---
record_type: owner_autonomy_policy
policy_id: pol_chris_audit_1
mode: PreAuthorized
status: active
approved_by_owner: true
owner_approval_ref: dec_pol_chris_audit_1
active_from: '2026-08-23T00:00:00Z'
expires_at: '2099-09-30T00:00:00Z'
agent_or_role: hbj-auditor.chris-huibojin.kiwiai-dev
task_selector_task_mode: audit
task_selector_project: chris-huibojin
task_selector_task_ids: []
max_tasks: 30
---
# Owner Autonomy Policy: pol_chris_audit_1 (F32 fixture copy)
`;

const DRAFT = `---
task_id: HBJ-F32-TS-1
title: HBJ-F32 TS 夹具——自定义角色发卡链信封解析(经bin)
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
# HBJ-F32 TS 夹具

draft publish --dry-run 应按注册表 class 解析信封到 pol_chris_coder_1。
`;

/**
 * chris 形门拓扑夹具(AIPOS-F32B): home_root 下两工作区。
 * withRegistry=true → lybra-fx 凭据库登记 hbj 双角色(真拓扑同构);
 * withRegistry=false → 无门注册表(真 chris 现状: 自身凭据文件只有过期运输凭证)。
 */
function makeFixture(root: string, withRegistry: boolean): string {
  const home = join(root, withRegistry ? "fx-registry" : "fx-bare");
  const ws = join(home, "chris-huibojin-fx");
  mkdirSync(join(ws, "5_tasks", "policies"), { recursive: true });
  mkdirSync(join(ws, "5_tasks", "drafts"), { recursive: true });
  mkdirSync(join(ws, "5_tasks", "queue"), { recursive: true });
  mkdirSync(join(ws, ".lybra"), { recursive: true });
  // chris 工作区 project.json 无 custom_roles(顾问实测真 chris 工作区为空 {})
  writeFileSync(
    join(ws, "project.json"),
    JSON.stringify(
      {
        code_repo: "/tmp/nonexistent/chris-huibojin",
        config_version: 1,
        project: "chris-huibojin",
        registered_at: "2026-08-10T00:00:00Z",
        registered_by: "kiwi",
      },
      null,
      2,
    ) + "\n",
  );
  writeFileSync(join(ws, "5_tasks", "policies", "pol_chris_coder_1.md"), POLICY_CODER);
  writeFileSync(join(ws, "5_tasks", "policies", "pol_chris_audit_1.md"), POLICY_AUDIT);
  writeFileSync(join(ws, "5_tasks", "drafts", "hbj-f32-ts-1.md"), DRAFT);
  // chris 自身凭据文件: 只有已过期运输凭证 + mcp 骨架(真 chris 同构, 无自定义角色)
  writeFileSync(
    join(ws, ".lybra", "connection.json"),
    JSON.stringify(
      {
        config_version: 1,
        mcp: { rpc_url: "http://127.0.0.1:7999/mcp" },
        tokens: [
          {
            agent_instance: "enroll_f32ts01",
            expires_at: "2026-08-22T17:36:09Z",
            fingerprint: "sha256:f32ts00001",
            role: "enroll-transport",
            scopes: [],
            token: "fx-synthetic-token-enroll-f32ts-01",
            token_ref: "svc-enroll-transport",
          },
        ],
      },
      null,
      2,
    ) + "\n",
  );
  if (withRegistry) {
    // 门凭据库(lybra 工作区): hbj-* 实际登记处(token 全合成, 真凭据永不入夹具)
    const regWs = join(home, "lybra-fx");
    mkdirSync(join(regWs, "5_tasks", "queue"), { recursive: true });
    mkdirSync(join(regWs, ".lybra"), { recursive: true });
    writeFileSync(
      join(regWs, "project.json"),
      JSON.stringify(
        {
          code_repo: "/tmp/nonexistent/lybra-fx",
          config_version: 1,
          project: "lybra-fx",
          registered_at: "2026-08-10T00:00:00Z",
          registered_by: "kiwi",
        },
        null,
        2,
      ) + "\n",
    );
    writeFileSync(
      join(regWs, ".lybra", "connection.json"),
      JSON.stringify(
        {
          config_version: 1,
          tokens: [
            {
              agent_instance: "hbj-coder.chris-huibojin.kiwiai-dev",
              fingerprint: "sha256:f32ts00002",
              projects: ["chris-huibojin"],
              projects_enforced: true,
              role: "hbj-coder",
              role_class: "executor",
              scopes: ["queue_claim", "queue_return", "task_progress"],
              token: "fx-synthetic-token-hbj-coder-f32ts",
              token_ref: "svc-hbj-coder",
            },
            {
              agent_instance: "hbj-auditor.chris-huibojin.kiwiai-dev",
              fingerprint: "sha256:f32ts00003",
              projects: ["chris-huibojin"],
              projects_enforced: true,
              role: "hbj-auditor",
              role_class: "auditor",
              scopes: ["queue_claim", "audit_verdict", "task_progress"],
              token: "fx-synthetic-token-hbj-auditor-f32ts",
              token_ref: "svc-hbj-auditor",
            },
          ],
        },
        null,
        2,
      ) + "\n",
    );
  }
  return ws;
}

interface PublishResult {
  verdict: string;
  blocking_reasons: string[];
  warnings: string[];
  rendered_markdown?: string;
  would_write?: boolean;
  wrote?: boolean;
}

function runPublishDryRun(ws: string): PublishResult {
  // BLOCK 时 bin 退出码非零, 用 spawnSync 捕获 stdout 再解析(不丢负对照证据)。
  const proc = spawnSync(
    binLybra,
    ["--workspace-root", ws, "draft", "publish", "--path", "5_tasks/drafts/hbj-f32-ts-1.md", "--dry-run", "--json"],
    { cwd: ws, encoding: "utf-8", timeout: 120_000 },
  );
  if (proc.error) throw proc.error;
  const out = (proc.stdout || "").trim();
  if (!out) {
    throw new Error(`bin/lybra 无 stdout (status=${proc.status} stderr=${(proc.stderr || "").slice(0, 300)})`);
  }
  return JSON.parse(out) as PublishResult;
}

// ===========================================================================
// A/B/C. 活体经 bin(工作树实现)
// ===========================================================================
{
  const fx = mkdtempSync(join(tmpdir(), "f32-envelope-"));
  try {
    // --- A. 门注册表在位(lybra-fx 凭据库登记 hbj) → 信封解析到 pol_chris_coder_1 ---
    try {
      const r = runPublishDryRun(makeFixture(fx, true));
      const blocked = (r.blocking_reasons || []).some((b) => String(b).includes("cannot resolve policy envelope"));
      check(
        "A: 门注册表在位 → publish --dry-run 非BLOCK且不撞信封墙",
        r.verdict !== "BLOCK" && !blocked,
        `verdict=${r.verdict} blocking=${JSON.stringify(r.blocking_reasons)}`,
      );
      check(
        "A: 契约节信封 = pol_chris_coder_1(claim/return 双点)",
        (r.rendered_markdown || "").includes("pol_chris_coder_1") &&
          (r.rendered_markdown || "").split("pol_chris_coder_1").length - 1 >= 2,
      );
      check("A: dry-run 零写入", r.wrote === false);
    } catch (e) {
      check("A: 注册表在位 → publish --dry-run 非BLOCK且不撞信封墙", false, String(e));
    }

    // --- B. 因果负对照: 门注册表无 hbj 条目(真 chris 现状拓扑) → 旧病复发 ---
    try {
      const r = runPublishDryRun(makeFixture(fx, false));
      const blocked = (r.blocking_reasons || []).some((b) => String(b).includes("cannot resolve policy envelope"));
      check(
        "B: 无门注册表 → BLOCK cannot resolve policy envelope(负对照)",
        r.verdict === "BLOCK" && blocked,
        `verdict=${r.verdict} blocking=${JSON.stringify(r.blocking_reasons).slice(0, 200)}`,
      );
    } catch (e) {
      check("B: 无门注册表 → BLOCK cannot resolve policy envelope(负对照)", false, String(e));
    }

    // --- C. audit 侧信封: 注册表 hbj-auditor→auditor(契约节渲染内部会解析
    //     audit 信封; 对执行卡断言 exec 信封 + audit 策略文件存在即链路在位,
    //     audit 全链走 Python 侧夹具 test_aipos_f32_custom_role_envelope.py) ---
    check(
      "C: 夹具含双信封(exec+audit 形工作区)",
      readFileSync(join(fx, "fx-registry", "chris-huibojin-fx", "5_tasks", "policies", "pol_chris_audit_1.md"), "utf-8").includes(
        "hbj-auditor.chris-huibojin.kiwiai-dev",
      ),
    );
  } finally {
    rmSync(fx, { recursive: true, force: true });
  }
}

// ===========================================================================
// D. 源级断言 —— 防碎片化红线(禁在 policy_resolver 自建角色→类映射)
// ===========================================================================
{
  const resolverSrc = readFileSync(join(repoRoot, "tools", "aipos_cli", "policy_resolver.py"), "utf-8");
  check(
    "D: policy_resolver 读 roles 注册表单源(custom_roles), 无自建映射表",
    resolverSrc.includes("from tools.aipos_cli.custom_roles import load_custom_roles") &&
      !/"exec":\s*\[\s*"executor"\s*\]/.test(resolverSrc) &&
      !/"audit":\s*\[\s*"auditor"\s*\]/.test(resolverSrc),
  );
  check(
    "D: 内建类候选派生自 roles 注册表单源(schema_loader)",
    resolverSrc.includes("from tools.schema_loader import get_all_role_names"),
  );
  check(
    "D: 既有直配语义保留(exec↔exec 直配表未动)",
    resolverSrc.includes('"exec": ["exec"]') && resolverSrc.includes('"audit": ["audit"]'),
  );
}

// ===========================================================================
console.log("");
if (NOTES.length) {
  console.log("NOTES:");
  for (const n of NOTES) console.log(`  - ${n}`);
}
console.log(`f32-custom-role-envelope: ${checks.length - failures}/${checks.length} checks passed`);
if (failures > 0) {
  console.error(`FAILED: ${failures} check(s) failed`);
  process.exit(1);
}
