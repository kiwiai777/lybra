/**
 * AIPOS-R6R Conformance 测试 (TS 侧) —— 锁定 verbs.schema.json 的动词契约。
 *
 * 与 Python 测试 (tools/test_aipos_r6r_verbs_conformance.py) 读同一份 schema,
 * 断言同一份预期契约(verb 名 / 必填参数 / 两阶段语义 / 关键参数 shape)。
 * 若 schema 漂移(缺动词、改错参数名、两阶段语义变), 两侧同时失败 —— 契约单一源。
 *
 * 跑法:`node tests/verbs-conformance.test.ts`。
 */

import { loadVerbCatalog, type VerbCatalog } from "../gate-client.ts";

let failures = 0;
const checks: Array<[string, boolean]> = [];
function check(name: string, ok: boolean) {
  checks.push([name, ok]);
  if (!ok) failures++;
}

// 连接器依赖的预期契约(AIPOS-R6R)。与 Python 侧同源同断言。
interface ExpectedVerb {
  phase: "single" | "dry_run" | "confirm";
  confirm?: string; // dry_run → confirm 配对
  required: string[];
}
const EXPECTED: Record<string, ExpectedVerb> = {
  lybra_queue_list: { phase: "single", required: [] },
  lybra_task_preview: { phase: "single", required: [] },
  lybra_return_content: { phase: "single", required: ["task_id"] },
  lybra_queue_claim_dry_run: { phase: "dry_run", confirm: "lybra_queue_claim_confirm", required: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"] },
  lybra_queue_claim_confirm: { phase: "confirm", required: ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"] },
  lybra_queue_return_dry_run: { phase: "dry_run", confirm: "lybra_queue_return_confirm", required: ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"] },
  lybra_queue_return_confirm: { phase: "confirm", required: ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"] },
  lybra_queue_close_dry_run: { phase: "dry_run", confirm: "lybra_queue_close_confirm", required: ["task_id", "actor", "closure_evidence"] },
  lybra_queue_close_confirm: { phase: "confirm", required: ["task_id", "actor", "closure_evidence"] },
  lybra_task_progress: { phase: "single", required: ["task_id", "event_type", "actor"] },
  lybra_bench_audit_submit_dry_run: { phase: "dry_run", required: ["task_id", "actor", "conclusion"] },
};

let catalog: VerbCatalog | null = null;
try {
  catalog = loadVerbCatalog();
} catch (e) {
  check(`loadVerbCatalog 加载成功(${e instanceof Error ? e.message : e})`, false);
}

if (catalog) {
  const verbs = catalog.verbs;
  for (const [name, exp] of Object.entries(EXPECTED)) {
    const verb = verbs[name];
    check(`verb 存在: ${name}`, !!verb);
    if (!verb) continue;
    check(`verb phase 对: ${name} (${exp.phase})`, verb.phase === exp.phase);
    if (exp.confirm) {
      check(`verb 两阶段配对: ${name} → ${exp.confirm}`, verb.confirm_verb === exp.confirm);
    }
    const required = verb.parameters?.required ?? [];
    check(`verb 必填参数对: ${name}`, JSON.stringify([...required].sort()) === JSON.stringify([...exp.required].sort()));
  }

  // 关键参数 shape(根因③:closure_evidence 是对象, 非扁平字符串)
  const closeEvidence = verbs.lybra_queue_close_dry_run?.parameters?.properties?.closure_evidence;
  check("closure_evidence 是 object(非 string)", closeEvidence?.type === "object");
  check("closure_evidence 含 finalize_commit_hash", closeEvidence?.properties?.finalize_commit_hash?.type === "string");

  // close_confirm 不接收 dry_run_token(根因:close 两阶段语义=重放参数, 非 dry_run_token)
  check("close_confirm 无 dry_run_token 参数", !("dry_run_token" in (verbs.lybra_queue_close_confirm?.parameters?.properties ?? {})));

  // task_progress 用 event_type, 非 status(根因:参数名错)
  check("task_progress 用 event_type(非 status)", "event_type" in (verbs.lybra_task_progress?.parameters?.properties ?? {}));

  // 两阶段语义:claim/return confirm 用 dry_run_token;close confirm 用重放参数
  check("claim confirm_via=dry_run_token", verbs.lybra_queue_claim_dry_run?.confirm_via === "dry_run_token");
  check("close confirm_via=replay_args", verbs.lybra_queue_close_dry_run?.confirm_via === "replay_args");
}

for (const [name, ok] of checks) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(failures === 0 ? `\nALL ${checks.length} PASS` : `\n${failures}/${checks.length} FAILED`);
process.exit(failures === 0 ? 0 : 1);
