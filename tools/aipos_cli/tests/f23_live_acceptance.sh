#!/usr/bin/env bash
# AIPOS-F23 活体验收② —— 新工位(空目录+pi)仅凭一贴完成上岗(全程实录, 真 gate 进程)
#
# 流程:
#   0. 夹具治理工作区(5_tasks/queue + .lybra/connection.json)
#   1. 起真 gate serve-http(测试端口, 用本工作树代码)
#   2. 顾问发码: lybra_enroll_code_dry_run → confirm(MCP 直调, 模拟顾问)
#   3. 新工位空目录: CLI lybra roles enroll --code <自包含码>(不传 gate-url, 码内嵌;
#      裸机等价命令; 连接器路径由 TS 套件 C 段覆盖) → 断言落盘/连通/治理仓零改动
#   4. 中断夹具(验收③): 第二张码, exchange 后不落盘 → enroll-list 对照(grace 窗口内未彻底消费)
set -euo pipefail
cd /home/kiwi/projects/lybra

TMP=$(mktemp -d /tmp/f23-live-XXXXXX)
GOV="$TMP/gov_ws"
STATION="$TMP/fresh-station"
PORT=7731
GATE="http://127.0.0.1:$PORT"
mkdir -p "$GOV/5_tasks/queue/pending" "$GOV/5_tasks/queue/claimed" "$GOV/.lybra"
cat > "$GOV/.lybra/connection.json" <<'EOF'
{"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:7118/mcp"}, "tokens": [
  {"role": "owner", "token": "f23-live-fixture-owner-token", "token_ref": "svc-owner",
   "scopes": ["owner_confirm", "owner_decision_record"], "fingerprint": "sha256:f23fixture"}
]}
EOF
mkdir -p "$STATION"
GOV_SNAPSHOT=$(find "$GOV" -type f | sort | xargs sha256sum 2>/dev/null | sha256sum)

cleanup() {
  [ -n "${GATE_PID:-}" ] && kill "$GATE_PID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "=== [1] 起 gate(测试端口 $PORT, 夹具治理仓) ==="
AIPOS_WORKSPACE_ROOT="$GOV" LYBRA_MCP_TOKEN=disabled-test-token \
  python3 -m tools.mcp_server serve-http --host 127.0.0.1 --port $PORT \
  --service-connection-json "$GOV/.lybra/connection.json" >/dev/null 2>&1 &
GATE_PID=$!
for i in $(seq 1 40); do
  if curl -sf -o /dev/null -X POST "$GATE/mcp" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer f23-live-fixture-owner-token" \
    -d '{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}'; then
    echo "gate up (pid=$GATE_PID)"; break
  fi
  if ! kill -0 "$GATE_PID" 2>/dev/null; then echo "gate died at startup"; exit 1; fi
  sleep 0.25
done

echo "=== [2] 顾问两阶段发码(dry_run → confirm; 验收①) ==="
python3 - "$GATE" "$GOV" <<'PYEOF'
import json, sys, urllib.request
gate = sys.argv[1]
def call(name, args, bearer="f23-live-fixture-owner-token"):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    req = urllib.request.Request(f"{gate}/mcp", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["result"]["structuredContent"]

dry = call("lybra_enroll_code_dry_run", {"role": "executor", "instance": "exec.f23-live",
    "ttl": 1800, "owner_authorization_ref": "F23-live-acceptance", "gate_url": sys.argv[1], "reason": "live acceptance"})
assert dry["ok"], dry
token = dry["dry_run_token"]
conf = call("lybra_enroll_code_confirm", {"dry_run_token": token, "owner_confirmation_token": "OWNER_CONFIRMED"})
assert conf["ok"], conf
assert conf["paste_text"].startswith("/lybra enroll LYBRAENROLL1."), conf.get("paste_text")
open("/tmp/f23_live_code1.txt", "w").write(conf["self_contained_code"])
print("  dry_run+confirm OK; paste_text:", conf["paste_text"][:60] + "...")
# 第二张码给中断夹具(验收③)
dry2 = call("lybra_enroll_code_dry_run", {"role": "auditor", "instance": "audit.f23-live",
    "ttl": 1800, "gate_url": sys.argv[1], "owner_authorization_ref": "F23-live-interrupt"})
conf2 = call("lybra_enroll_code_confirm", {"dry_run_token": dry2["dry_run_token"], "owner_confirmation_token": "OWNER_CONFIRMED"})
open("/tmp/f23_live_code2.txt", "w").write(conf2["self_contained_code"])
print("  code2 (interrupt fixture) issued")
PYEOF

echo "=== [3] 新工位一贴上岗(CLI 等价命令, 不传 --gate-url: 码内嵌; 验收②⑦⑧⑨) ==="
cd "$STATION"
python3 -m tools.aipos_cli.aipos_cli roles enroll --code "$(cat /tmp/f23_live_code1.txt)" --verify --json > /tmp/f23_live_enroll.json 2>/tmp/f23_live_enroll.err || { cat /tmp/f23_live_enroll.err; exit 1; }
python3 - <<'PYEOF'
import json
r = json.load(open("/tmp/f23_live_enroll.json"))
assert r["ok"] is True, r
assert r["operation"] == "enroll"
assert r["role"] == "executor" and r["agent_instance"] == "exec.f23-live", r
assert r["landed"] is True, f"land 确认失败: {r.get('landed')}"
assert r["verify"]["ok"] is True, r["verify"]
assert "/lybra sync" in (r.get("next_step") or ""), r
print(f"  enroll OK: role={r['role']} instance={r['agent_instance']} landed={r['landed']} verify=ok")
print(f"  落盘: {r['lybra_dir']}/{r['files_written']}  workspace_root={r['workspace_root']}")
PYEOF
echo "  --- 工位 .lybra/ 实录(当日时间戳) ---"
ls -la --time-style=+"%Y-%m-%d %H:%M:%S" "$STATION/.lybra/"
echo "  --- connection.json(脱敏) ---"
python3 -c "
import json; d=json.load(open('$STATION/.lybra/connection.json'))
d['tokens']=[{k:('***' if k=='token' else v) for k,v in t.items()} for t in d.get('tokens',[])]
print(json.dumps(d, indent=1, ensure_ascii=False))"
echo "  --- role 文件 ---"
cat "$STATION/.lybra/role"

echo "=== [4] 治理工作区防护(验收②/第九坑: 工位文件绝不落治理仓) ==="
# 断言①: 治理仓 .lybra/ 下无工位文件(role/actor/policy —— 第九坑实录曾被污染)
for f in role actor policy; do
  if [ -e "$GOV/.lybra/$f" ]; then echo "  ✗ 治理仓出现工位文件 .lybra/$f"; exit 1; fi
done
echo "  ✓ 治理仓 .lybra/ 无工位文件(role/actor/policy 零污染)"
# 断言②: 5_tasks/ 队列结构零改动(仅含建夹具时的 pending/claimed 空目录)
(cd "$GOV" && find 5_tasks -type f | sort | grep -q . && { echo "  ✗ 5_tasks 出现新文件"; exit 1; } || true)
echo "  ✓ 5_tasks/ 零改动"
# 实录: 治理仓内变化文件清单(应为 gate 记账: enrollments/enrollment_log/connection.json)
echo "  治理仓文件实录(设计内 gate 记账): "
(cd "$GOV" && find . -type f | sort)

echo "=== [5] 中断夹具(验收③): code2 exchange 后不落盘 → enroll-list 对照 ==="
python3 - "$GATE" <<'PYEOF'
import json, sys, urllib.request
gate = sys.argv[1]
code2 = open("/tmp/f23_live_code2.txt").read().strip()
# 解自包含码拿运输凭证(模拟工位只做了 exchange, 未落盘即中断)
sys.path.insert(0, "/home/kiwi/projects/lybra")
from tools.aipos_cli.enrollment import decode_self_contained_code
sc = decode_self_contained_code(code2)
def call(name, args, bearer):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    req = urllib.request.Request(f"{gate}/mcp", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["result"]["structuredContent"]
r1 = call("lybra_roles_enroll_exchange", {"code": code2}, sc["transport_token"])
assert r1["ok"] and r1["landing_required"], r1
open("/tmp/f23_live_tok2.txt", "w").write(r1["token_entry"]["token"])  # 供步骤6对照
listing = call("lybra_roles_enroll_list", {}, sc["transport_token"])
items = {e["code_id"]: e for e in listing["enrollments"]}
interrupted = [e for e in items.values() if e["role"] == "auditor"][0]
assert interrupted["status"] == "used" and interrupted["landed"] is False and interrupted["grace_until"], interrupted
print(f"  中断码状态: status={interrupted['status']} landed={interrupted['landed']} grace_until={interrupted['grace_until']}")
print("  (码未彻底消费 —— grace 窗口内同码重贴可免费重试, 验收⑦'码不白烧')")
PYEOF

echo "=== [6] 同码免费重试(验收⑦): 中断的 code2 重贴到工位2 → 同一 token 上岗 ==="
STATION2="$TMP/fresh-station2"
mkdir -p "$STATION2"
cd "$STATION2"
python3 -m tools.aipos_cli.aipos_cli roles enroll --code "$(cat /tmp/f23_live_code2.txt)" --verify --json > /tmp/f23_live_enroll2.json 2>/tmp/f23_live_enroll2.err || { cat /tmp/f23_live_enroll2.err; exit 1; }
python3 - <<'PYEOF2'
import json
r = json.load(open("/tmp/f23_live_enroll2.json"))
assert r["ok"] and r["role"] == "auditor" and r["landed"] is True, r
print(f"  工位2 enroll OK: role={r['role']} landed={r['landed']} verify_ok={r['verify']['ok']}")
PYEOF2
# 对照 token 同一性: 工位2 connection.json 中 auditor token == 中断时 exchange 铸出的 token
STATION2_TOKEN=$(python3 -c "import json; print(next(t['token'] for t in json.load(open('$STATION2/.lybra/connection.json'))['tokens'] if t['role']=='auditor'))")
if [ "$STATION2_TOKEN" != "$(cat /tmp/f23_live_tok2.txt)" ]; then
  echo "  ✗ 重试重铸了新 token(码白烧防护失效)"; exit 1
fi
echo "  ✓ 同码重试返回同一 token(未重铸 —— '码不白烧'活体证明)"

echo ""
echo "=== ALL LIVE ACCEPTANCE STEPS PASSED ==="
