#!/usr/bin/env bash
# AIPOS-196b in-container adversarial probe (run BY the auditor, cc glm, inside a
# confined worker container with the real mounts/network). Verifies the Layer 2
# boundary holds: truth is not writable, only /scratch is writable, and the
# executor token cannot reach auditor-only MCP tools.
#
# This script never prints raw secrets. It reads the Bearer token from the
# mounted MCP config only to exercise scope denial; it does not echo it.
#
# Exit 0 = all boundary checks held. Exit 1 = a boundary hole was found.

set -u

PASS=0
FAIL=0
ok()   { echo "PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

# A write that MUST fail (path not mounted / read-only rootfs).
expect_write_fails() {
  local target="$1" label="$2"
  if ( : > "$target" ) 2>/dev/null; then
    bad "$label: write unexpectedly SUCCEEDED at $target"
    rm -f "$target" 2>/dev/null || true
  else
    ok "$label: write correctly blocked at $target"
  fi
}

echo "== AIPOS-196b confined worker boundary probe =="

# 1. Lybra truth / control-plane / product / host paths must NOT be writable.
expect_write_fails "/workspace/5_tasks/queue/pending/probe_orphan.md" "5_tasks/queue not writable"
expect_write_fails "/workspace/5_tasks/records/probe_orphan.md"       "5_tasks/records not writable"
expect_write_fails "/workspace/.lybra/local/probe.json"              ".lybra/local not writable"
expect_write_fails "/workspace/probe_product_repo.txt"               "product repo not writable"
expect_write_fails "/etc/lybra_probe_hostfs.txt"                      "host fs (/etc) not writable"
expect_write_fails "/probe_rootfs.txt"                                "root fs read-only"

# 2. /scratch MUST be the writable location.
if ( echo "scratch-write-ok" > /scratch/.probe_write ) 2>/dev/null; then
  ok "/scratch is writable (expected)"
  rm -f /scratch/.probe_write 2>/dev/null || true
else
  bad "/scratch is NOT writable (unexpected)"
fi

# 3. MCP gate scope: executor token must be denied auditor-only tools.
GATE_URL="${GATE_URL:-}"
if [ -z "$GATE_URL" ] && [ -r /etc/lybra/mcp.json ]; then
  GATE_URL=$(grep -o 'http[s]\?://[^"]*' /etc/lybra/mcp.json | head -n1)
fi
BEARER=""
if [ -r /etc/lybra/mcp.json ]; then
  BEARER=$(grep -o 'Bearer [^"]*' /etc/lybra/mcp.json | head -n1 | sed 's/^Bearer //')
fi

if command -v curl >/dev/null 2>&1 && [ -n "$GATE_URL" ] && [ -n "$BEARER" ]; then
  req='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lybra_audit_verdict_dry_run","arguments":{}}}'
  resp=$(curl -s -H "Authorization: Bearer ${BEARER}" -H "Content-Type: application/json" \
              --noproxy '*' -X POST "$GATE_URL" -d "$req" 2>/dev/null)
  if echo "$resp" | grep -qiE 'scope|denied|not available|capability'; then
    ok "executor token denied auditor-only tool (audit_verdict)"
  else
    bad "executor token NOT clearly denied auditor tool; response: $resp"
  fi

  # Reachability: a known executor-scoped tool should at least be recognized.
  req2='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
  resp2=$(curl -s -H "Authorization: Bearer ${BEARER}" -H "Content-Type: application/json" \
               --noproxy '*' -X POST "$GATE_URL" -d "$req2" 2>/dev/null)
  if echo "$resp2" | grep -q 'lybra_queue_return'; then
    ok "gate reachable; executor sees queue_return tool"
  else
    bad "gate not reachable or executor scope missing queue_return; response: $resp2"
  fi
else
  echo "SKIP: curl/GATE_URL/Bearer unavailable; run MCP scope checks via the harness instead."
fi

# 4. No raw secret leaked into the projection.
if [ -d /projection ]; then
  if [ -n "$BEARER" ] && grep -rqF "$BEARER" /projection 2>/dev/null; then
    bad "raw Bearer token found inside /projection"
  else
    ok "no raw Bearer token in /projection"
  fi
fi

echo "== probe summary: PASS=$PASS FAIL=$FAIL =="
[ "$FAIL" -eq 0 ]
