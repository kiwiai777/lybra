/**
 * claim-check + write-ledger 单测(harness 无关逻辑,确定性)。
 * 跑法:`node tools/connector/test/claim-check.test.ts`(Node ≥22 类型剥离,无需 npm install)。
 */
import { getActiveClaim } from "../claim-check.ts";
import { appendWriteOp, readWriteOps } from "../write-ledger.ts";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let failures = 0;
function check(name: string, ok: boolean) {
  console.log(`${ok ? "✓" : "✗"} ${name}`);
  if (!ok) failures++;
}

const WS = mkdtempSync(join(tmpdir(), "conn-spike-"));
const sessionsDir = (t: string) => join(WS, "5_tasks", "records", "sessions", t);
function writeSessionRecord(taskId: string, status: string, agent = "exec.lybra.kiwiai-dev") {
  mkdirSync(sessionsDir(taskId), { recursive: true });
  writeFileSync(
    join(sessionsDir(taskId), `session_${taskId}.md`),
    `---\nrecord_type: session_record\nsession_id: session_${taskId}\ntask_id: ${taskId}\ncanonical_agent_instance: ${agent}\nclaim_id: claim_${taskId}\nsession_status: ${status}\n---\n# body\n`,
  );
}

// 1. claimed → active
writeSessionRecord("TASK-A", "claimed");
const a = getActiveClaim(WS, "TASK-A");
check("claimed task is active", a.active === true && a.claimId === "claim_TASK-A");

// 2. returned → not active
writeSessionRecord("TASK-B", "returned");
const b = getActiveClaim(WS, "TASK-B");
check("returned task is NOT active", b.active === false && /returned/.test(b.reason || ""));

// 3. no records → not active
const c = getActiveClaim(WS, "TASK-NONE");
check("missing task is NOT active", c.active === false && /no session records/.test(c.reason || ""));

// 4. agent mismatch → not active (even if claimed)
writeSessionRecord("TASK-C", "claimed", "some.other-agent");
const d = getActiveClaim(WS, "TASK-C", "exec.lybra.kiwiai-dev");
check("claimed but wrong owner is NOT active", d.active === false && /some.other-agent/.test(d.reason || ""));

// 5. agent match → active
writeSessionRecord("TASK-D", "claimed", "exec.lybra.kiwiai-dev");
const e = getActiveClaim(WS, "TASK-D", "exec.lybra.kiwiai-dev");
check("claimed + matching owner is active", e.active === true);

// 6. ledger append + read
const ledger = join(WS, "ledger.jsonl");
appendWriteOp(ledger, { ts: "t1", tool: "write", decision: "allow", taskId: "TASK-A", target: "/a" });
appendWriteOp(ledger, { ts: "t2", tool: "edit", decision: "block", target: "/b", reason: "no claim" });
const ops = readWriteOps(ledger);
check("ledger has 2 entries", ops.length === 2);
check("ledger entry[0] allow TASK-A", ops[0]?.decision === "allow" && ops[0]?.taskId === "TASK-A");
check("ledger entry[1] block", ops[1]?.decision === "block" && ops[1]?.reason === "no claim");

rmSync(WS, { recursive: true, force: true });
console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
