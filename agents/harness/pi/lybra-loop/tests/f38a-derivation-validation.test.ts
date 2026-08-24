/**
 * AIPOS-F38 大项A 夹具: 派生产物必过同一 schema 校验 + 审计身份取注册表实例
 * (经 bin 入 run-all; PY 驱动真跑四个 writer, 非源码 grep)
 *
 * 病灶(2026-08-24 接入实战): 派生 writer 产物缺字段/审计实例抄原卡
 * (F26R2/F22R2/级联卡三次发作) — 产物不可直接认领, 需人肉 amend。
 *
 * 本夹具真跑(git 工作区 = 本卡代码):
 *  - w0  FAIL→fix 派生(derive_repair_card_on_fail): 修复卡字段全(F17 既有), 零 amend
 *  - w1  return→audit 派生(derive_audit_task_on_return): 字段全 + 审计身份=注册表审计实例
 *  - w1s 同 writer 缺字段故意破坏(schema 加假必填) → derived=False + 人话拒因 + 不落卡(拒并出声)
 *  - w2  fix close→级联复审派生(close_task 真跑): 复审卡字段全 + 审计身份=注册表实例
 *        (病灶回钉: 不再承继原卡执行实例)
 *  - w2s 同 writer 破坏 → close 不被坏卡阻断 + governance warning 出声 + 不落坏卡
 *  - w3  audit_dispatch 预览: 常规无 F38 拒因; 破坏 → blocking_reasons 出声(拒并出声)
 *
 * 跑法: node tests/f38a-derivation-validation.test.ts (或经 run-all.sh 常驻)
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { writeFileSync, rmSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, "package.json")) && existsSync(join(dir, "agents"))) return dir;
    dir = join(dir, "..");
  }
  return process.cwd();
}
const PROJECT_ROOT = findProjectRoot();

type Leg = Record<string, unknown>;
function legOf(legs: Leg[], name: string): Leg {
  const l = legs.find((x) => x.case === name);
  assert.ok(l, `腿 ${name} 应存在于驱动输出`);
  return l;
}

function runDriver(): Leg[] {
  const driverPath = join(PROJECT_ROOT, "agents/harness/pi/lybra-loop/tests/.tmp-f38a-driver.py");
  writeFileSync(driverPath, PY_DRIVER);
  try {
    const r = spawnSync("python3", [driverPath], {
      encoding: "utf-8",
      env: { ...process.env, LYBRA_PRODUCT_ROOT: PROJECT_ROOT },
      timeout: 120_000,
    });
    if (r.status !== 0) throw new Error(`python 驱动失败: ${r.stderr?.slice(-800)}`);
    const lines = r.stdout.splitLines ? r.stdout.splitLines() : r.stdout.split("\n");
    const idx = lines.indexOf("F38A_RESULT_JSON");
    assert.ok(idx >= 0, "驱动应输出 F38A_RESULT_JSON 标记");
    return JSON.parse(lines[idx + 1]) as Leg[];
  } finally {
    rmSync(driverPath, { force: true });
  }
}

const PY_DRIVER = String.raw`
import sys, os, json, tempfile
from pathlib import Path

ROOT = Path(os.environ["LYBRA_PRODUCT_ROOT"])
sys.path.insert(0, str(ROOT))
from tools.schema_loader import get_required_card_fields
import tools.schema_loader as SL
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli import audit_derivation as AD
from tools.aipos_cli.board_adapter import close_task, audit_dispatch_task

REQUIRED = get_required_card_fields()
OUT = []
def emit(**kv): OUT.append(kv)

def mkws():
    ws = Path(tempfile.mkdtemp(prefix="f38a-"))
    for sub in ["5_tasks/queue/pending", "5_tasks/queue/claimed", "5_tasks/queue/completed",
                "5_tasks/records/returns", "5_tasks/records/audit_verdicts",
                "5_tasks/records/closures", "5_tasks/records/audit_dispatches"]:
        (ws / sub).mkdir(parents=True, exist_ok=True)
    return ws

def card(**fm):
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items()) + "\n---\n\n# card\n"

BASE = dict(project="lybra", assigned_to="exec.lybra.kiwiai-dev", agent_instance="exec.lybra.kiwiai-dev",
            context_bundle="default", task_mode="code", task_class="simple", priority="high",
            status="claimed", created_by="advisor.lybra.kiwiai-dev", needs_owner="false",
            output_target="tools/", artifact_policy="formal_write",
            claimed_by="exec.lybra.kiwiai-dev")

# ---------- writer ① happy (T1) ----------
ws = mkws()
(ws / "5_tasks/queue/claimed/aipos-f38t1.md").write_text(card(task_id="AIPOS-F38T1", title="T1", **BASE), encoding="utf-8")
meta, _, _ = parse_markdown_frontmatter((ws / "5_tasks/queue/claimed/aipos-f38t1.md").read_text())
r = AD.derive_audit_task_on_return(repo_root=ws, source_task_id="AIPOS-F38T1", source_metadata=meta,
                                   source_path="5_tasks/queue/claimed/aipos-f38t1.md",
                                   return_record_ref="return_f38t1", artifact_refs=[])
acard = ws / "5_tasks/queue/pending/aipos-f38t1r.md"
if acard.exists():
    am, _, _ = parse_markdown_frontmatter(acard.read_text())
    emit(case="w1_happy", derived=r.get("derived"), audit_task_id=r.get("audit_task_id"),
         missing=[f for f in REQUIRED if am.get(f) is None or (isinstance(am.get(f), str) and not am.get(f).strip())], agent_instance=am.get("agent_instance"),
         assigned_to=am.get("assigned_to"), expected_instance=AD._derive_audit_instance("lybra"),
         task_mode=am.get("task_mode"), status=am.get("status"))
else:
    emit(case="w1_happy", derived=r.get("derived"), reason=str(r.get("reason"))[:200], no_card=True)

# ---------- writer ① sabotage (T2) ----------
orig_ad = AD.get_required_card_fields
AD.get_required_card_fields = lambda: orig_ad() + ["f38_sabotage_field"]
(ws / "5_tasks/queue/claimed/aipos-f38t2.md").write_text(card(task_id="AIPOS-F38T2", title="T2", **BASE), encoding="utf-8")
meta2, _, _ = parse_markdown_frontmatter((ws / "5_tasks/queue/claimed/aipos-f38t2.md").read_text())
r2 = AD.derive_audit_task_on_return(repo_root=ws, source_task_id="AIPOS-F38T2", source_metadata=meta2,
                                    source_path="5_tasks/queue/claimed/aipos-f38t2.md",
                                    return_record_ref="return_f38t2", artifact_refs=[])
AD.get_required_card_fields = orig_ad
emit(case="w1_sabotage", derived=r2.get("derived"), reason=str(r2.get("reason"))[:160],
     card_written=(ws / "5_tasks/queue/pending/aipos-f38t2r.md").exists())

# ---------- writer ③ dispatch preview: clean + sabotage (source=T1) ----------
def dispatch_call():
    return audit_dispatch_task(source_task_id="AIPOS-F38T1", actor="audit.lybra.kiwiai-dev",
                               agent_instance="audit.lybra.kiwiai-dev", owner_policy_ref="pol_test",
                               audit_task_id="AIPOS-F38T1RD", audit_agent_instance="audit.lybra.kiwiai-dev",
                               dry_run=True, repo_root=ws)
r3 = dispatch_call()
def reasons_of(resp):
    for k in ("blocking_reasons",):
        if resp.get(k): return resp[k]
    d = resp.get("data") or {}
    return d.get("blocking_reasons") or []
br3 = reasons_of(r3)
emit(case="w3_clean", f38_reason=[x for x in br3 if "AIPOS-F38" in str(x)], other_blocks=[str(x)[:60] for x in br3 if "AIPOS-F38" not in str(x)], verdict=r3.get("verdict"))
orig_sl = SL.get_required_card_fields
SL.get_required_card_fields = lambda: orig_sl() + ["f38_sabotage_field"]
r4 = dispatch_call()
SL.get_required_card_fields = orig_sl
br4 = reasons_of(r4)
emit(case="w3_sabotage", f38_reason=[str(x)[:160] for x in br4 if "AIPOS-F38" in str(x)])

# ---------- writer ② cascade happy (ws2: T3-fix1 close→T3R 复审卡) ----------
def build_cascade_ws(tag):
    w = mkws()
    (w / "5_tasks/queue/completed" / f"aipos-f38{tag}.md").write_text(
        card(task_id=f"AIPOS-F38{tag.upper()}", title=f"T{tag}", **BASE), encoding="utf-8")
    fm = dict(BASE); fm.update(task_id=f"AIPOS-F38{tag.upper()}-fix1", title=f"Fix T{tag}",
                               derived_from_audit_task_id=f"AIPOS-F38{tag.upper()}R",
                               claim_id=f"claim_AIPOS-F38{tag.upper()}-fix1_20260824_000000_exec-lybra-kiwiai-dev",
                               claimed_at="2026-08-24T00:00:00Z",
                               active_session_id=f"session_AIPOS-F38{tag.upper()}-fix1_20260824_000000_exec-lybra-kiwiai-dev")
    (w / "5_tasks/queue/claimed" / f"aipos-f38{tag}-fix1.md").write_text(card(**fm), encoding="utf-8")
    (w / "5_tasks/records/returns" / f"AIPOS-F38{tag.upper()}-fix1").mkdir(parents=True, exist_ok=True)
    (w / "5_tasks/records/returns" / f"AIPOS-F38{tag.upper()}-fix1" / "return_test.md").write_text(
        card(record_type="return_record", return_id="return_test", task_id=f"AIPOS-F38{tag.upper()}-fix1",
             executor_status="completed", returned_at="2026-08-24T00:00:00Z"), encoding="utf-8")
    (w / "5_tasks/records/audit_verdicts" / f"AIPOS-F38{tag.upper()}-fix1").mkdir(parents=True, exist_ok=True)
    (w / "5_tasks/records/audit_verdicts" / f"AIPOS-F38{tag.upper()}-fix1" / "verdict_test.md").write_text(
        card(record_type="audit_verdict_record", event_type="mcp_audit_verdict", verdict_id="verdict_test",
             verdict="PASS", verdict_at="2026-08-24T01:00:00Z", task_id=f"AIPOS-F38{tag.upper()}-fix1"), encoding="utf-8")
    return w

ws2 = build_cascade_ws("t3")
rc = close_task(task_id="AIPOS-F38T3-fix1", actor="exec.lybra.kiwiai-dev",
                closure_evidence={"finalize_commit_hash": "f38test"}, dry_run=False, repo_root=ws2)
reaudit = ws2 / "5_tasks/queue/pending/aipos-f38t3r.md"
if reaudit.exists():
    rm, _, _ = parse_markdown_frontmatter(reaudit.read_text())
    emit(case="w2_happy", ok=rc.get("ok"), audit_task_id=rm.get("task_id"),
         missing=[f for f in REQUIRED if rm.get(f) is None or (isinstance(rm.get(f), str) and not rm.get(f).strip())], agent_instance=rm.get("agent_instance"),
         assigned_to=rm.get("assigned_to"), expected_instance=AD._derive_audit_instance("lybra"),
         task_mode=rm.get("task_mode"), status=rm.get("status"),
         inherited_exec_identity=(rm.get("agent_instance") == "exec.lybra.kiwiai-dev"))
else:
    emit(case="w2_happy", ok=rc.get("ok"), verdict=rc.get("verdict"),
         blocking=[str(x)[:100] for x in reasons_of(rc)], no_card=True)

# ---------- writer ② cascade sabotage (ws3: T4) ----------
ws3 = build_cascade_ws("t4")
SL.get_required_card_fields = lambda: orig_sl() + ["f38_sabotage_field"]
rc2 = close_task(task_id="AIPOS-F38T4-fix1", actor="exec.lybra.kiwiai-dev",
                 closure_evidence={"finalize_commit_hash": "f38test"}, dry_run=False, repo_root=ws3)
SL.get_required_card_fields = orig_sl
w2 = rc2.get("warnings") or (rc2.get("data") or {}).get("warnings") or []
emit(case="w2_sabotage", ok=rc2.get("ok"), all_warns=[str(x)[:200] for x in w2],
     warn=[str(x)[:200] for x in w2 if "AIPOS-F38" in str(x)],
     card_written=(ws3 / "5_tasks/queue/pending/aipos-f38t4r.md").exists())


# ---------- writer ⓪ FAIL→fix 派生 (T0) ----------
ws0 = mkws()
(ws0 / "5_tasks/queue/claimed/aipos-f38t0.md").write_text(card(task_id="AIPOS-F38T0", title="T0", **BASE), encoding="utf-8")
r0 = AD.derive_repair_card_on_fail(governance_root=ws0, reviewed_task_id="AIPOS-F38T0",
                                   audit_task_id="AIPOS-F38T0R", verdict_id="verdict_test0",
                                   fail_reason="F38 夹具: 构造 FAIL", actor="audit.lybra.kiwiai-dev")
pc = ws0 / "5_tasks/queue/pending/aipos-f38t0-fix1.md"
if pc.exists():
    pm, _, _ = parse_markdown_frontmatter(pc.read_text())
    emit(case="w0_fix", derived=r0.get("derived"), repair_task_id=r0.get("repair_task_id"),
         missing=[f for f in REQUIRED if pm.get(f) is None or (isinstance(pm.get(f), str) and not str(pm.get(f)).strip())],
         agent_instance=pm.get("agent_instance"), status=pm.get("status"))
else:
    emit(case="w0_fix", derived=r0.get("derived"), message=str(r0.get("message"))[:160], no_card=True)

print("F38A_RESULT_JSON")
print(json.dumps(OUT, ensure_ascii=False))
`;

describe("F38-大项A 派生产物必过同一校验 — 全 writer 活体", () => {
  const legs = runDriver();

  it("w0: FAIL→fix 派生 — 修复卡字段全(F17 既有), 零 amend", () => {
    const w0 = legOf(legs, "w0_fix");
    console.log(`[w0] derived=${w0.derived} repair=${w0.repair_task_id} missing=${JSON.stringify(w0.missing)} agent_instance=${w0.agent_instance}`);
    assert.strictEqual(w0.derived, true);
    assert.deepStrictEqual(w0.missing, []);
    assert.strictEqual(w0.status, "pending", "修复卡落 pending 可直接认领");
    console.log("[w0 判定] FAIL→fix 派生产物字段全, 零 amend ✓");
  });

  it("w1: return→audit 派生 — 字段全 + 审计身份=注册表审计实例(禁承继原卡)", () => {
    const w1 = legOf(legs, "w1_happy");
    console.log(`[w1] derived=${w1.derived} ${w1.audit_task_id} missing=${JSON.stringify(w1.missing)} agent_instance=${w1.agent_instance} assigned_to=${w1.assigned_to}`);
    assert.strictEqual(w1.derived, true);
    assert.deepStrictEqual(w1.missing, [], "必填全集(schema 单源)一个不缺");
    assert.strictEqual(w1.agent_instance, w1.expected_instance, "审计身份=注册表审计实例");
    assert.notStrictEqual(w1.agent_instance, "exec.lybra.kiwiai-dev", "不承继原卡执行实例");
    assert.strictEqual(w1.assigned_to, "audit_lybra");
    assert.strictEqual(w1.task_mode, "audit");
    assert.strictEqual(w1.status, "pending", "直接可认领, 零 amend");
    console.log("[w1 判定] return 派生审计卡 = 审计实例 + 字段全 ✓");
  });

  it("w1s: 故意缺字段 → 同一校验拒并出声, 不落坏卡", () => {
    const s = legOf(legs, "w1_sabotage");
    console.log(`[w1s] derived=${s.derived} card_written=${s.card_written} reason="${String(s.reason).slice(0, 64)}..."`);
    assert.strictEqual(s.derived, false, "缺字段 → 拒派生");
    assert.match(String(s.reason), /AIPOS-F38 派生校验 FAIL.*缺必填字段/, "人话拒因(出声)");
    assert.strictEqual(s.card_written, false, "不落坏卡");
    console.log("[w1s 判定] writer 产出缺字段被同一校验拒并出声 ✓");
  });

  it("w2: fix close→级联复审派生 — 字段全 + 审计身份=注册表实例(承继病灶回钉)", () => {
    const w2 = legOf(legs, "w2_happy");
    console.log(`[w2] close ok=${w2.ok} ${w2.audit_task_id} missing=${JSON.stringify(w2.missing)} agent_instance=${w2.agent_instance}`);
    assert.strictEqual(w2.ok, true, "close 本身成功");
    assert.deepStrictEqual(w2.missing, []);
    assert.strictEqual(w2.agent_instance, w2.expected_instance, "复审卡审计身份=注册表审计实例");
    assert.notStrictEqual(w2.inherited_exec_identity, true, "病灶回钉: 不再承继原卡执行实例");
    assert.strictEqual(w2.task_mode, "audit");
    assert.strictEqual(w2.status, "pending");
    console.log("[w2 判定] 级联复审卡 = 审计实例 + 字段全, 直接可认领 ✓");
  });

  it("w2s: 级联 writer 破坏 → close 不阻断 + warning 出声 + 不落坏卡", () => {
    const s = legOf(legs, "w2_sabotage");
    console.log(`[w2s] close ok=${s.ok} card_written=${s.card_written}`);
    console.log(`[w2s] warn="${String((s.warn as string[] | undefined)?.[0] || "").slice(0, 80)}..."`);
    assert.strictEqual(s.ok, true, "派生失败不阻断 close(治理警告承载)");
    assert.ok((s.warn as string[] | undefined)?.length, "governance warning 出声(拒因)");
    assert.match(String((s.warn as string[])[0]), /AIPOS-F38 派生校验 FAIL/);
    assert.strictEqual(s.card_written, false, "不落坏卡");
    console.log("[w2s 判定] 级联 writer 不合规即拒并出声 ✓");
  });

  it("w3: audit_dispatch — 常规无 F38 拒因; 破坏 → BLOCK 出声", () => {
    const c = legOf(legs, "w3_clean");
    const s = legOf(legs, "w3_sabotage");
    console.log(`[w3] clean f38_reason=${JSON.stringify(c.f38_reason)} | sabotage f38_reason="${String((s.f38_reason as string[] | undefined)?.[0] || "").slice(0, 60)}..."`);
    assert.deepStrictEqual(c.f38_reason, [], "常规输入不误伤(无 F38 拒因)");
    assert.ok((s.f38_reason as string[] | undefined)?.length, "破坏 → blocking_reasons 含 F38 校验拒因");
    assert.match(String((s.f38_reason as string[])[0]), /AIPOS-F38 派生校验 FAIL/, "拒并出声");
    console.log("[w3 判定] audit_dispatch 同一校验把关 ✓");
  });
});
