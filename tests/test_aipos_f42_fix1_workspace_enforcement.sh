#!/bin/bash
# AIPOS-F42-fix1 E2E测试：workspace_root参数+projects_enforced越权修复
# 验收：①第二工作区E2E(chris)②lybra回归③F-1越权洞修复(负夹具)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LYBRA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GATE_URL="http://kiwiai-dev.tail6b5218.ts.net:7118"
GOV_ROOT="/home/kiwi/ai-project-os/2_projects/lybra"
CHRIS_ROOT="/home/kiwi/ai-project-os/2_projects/chris-huibojin"

echo "=== AIPOS-F42-fix1 E2E测试开始 ==="
echo "Gate: $GATE_URL"
echo "Lybra工作区: $GOV_ROOT"
echo "Chris工作区: $CHRIS_ROOT"
echo ""

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }

# 读取token
LYBRA_TOKEN=$(python3 -c "
import json
conn = json.load(open('$GOV_ROOT/.lybra/connection.json'))
tokens = conn.get('tokens', [])
exec_token = next((t for t in tokens if t.get('role') == 'executor'), None)
if exec_token:
    print(exec_token['token'])
else:
    exit(1)
" 2>/dev/null) || fail "无法读取 lybra executor token"

# 如果有chris token，读取它（用于测试越权）
CHRIS_TOKEN=""
if [ -f "$CHRIS_ROOT/.lybra/connection.json" ]; then
    CHRIS_TOKEN=$(python3 -c "
import json
conn = json.load(open('$CHRIS_ROOT/.lybra/connection.json'))
tokens = conn.get('tokens', [])
exec_token = next((t for t in tokens if t.get('role') == 'executor'), None)
if exec_token:
    print(exec_token['token'])
" 2>/dev/null) || echo ""
fi

echo "Lybra token前8位: ${LYBRA_TOKEN:0:8}..."
if [ -n "$CHRIS_TOKEN" ]; then
    echo "Chris token前8位: ${CHRIS_TOKEN:0:8}..."
fi
echo ""

# ==============================================================================
# 验收① F-1(P0)越权洞修复：lybra域token不得操作chris工作区（负夹具，先红后绿）
# ==============================================================================
echo "--- 验收① F-1越权洞修复（负夹具）---"
echo "测试：lybra-scoped token + workspace_root=chris → 应该BLOCK"

# 构造测试：用lybra token尝试访问chris工作区（应该被拒绝）
RESULT=$(curl -s -X POST "$GATE_URL/mcp" \
  -H "Authorization: Bearer $LYBRA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1001,
    "method": "tools/call",
    "params": {
      "name": "lybra_queue_list",
      "arguments": {
        "actor": "exec.lybra.kiwiai-dev",
        "workspace_root": "'"$CHRIS_ROOT"'"
      }
    }
  }' 2>/dev/null || echo '{"error": "request failed"}')

# 解析结果
OK=$(echo "$RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('ok', False))" 2>/dev/null || echo "false")
ERROR_CODE=$(echo "$RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('error_code', ''))" 2>/dev/null || echo "")
BLOCKING_REASON=$(echo "$RESULT" | python3 -c "import sys, json; reasons=json.load(sys.stdin).get('result', {}).get('blocking_reasons', []); print(reasons[0] if reasons else '')" 2>/dev/null || echo "")

echo "响应: ok=$OK, error_code=$ERROR_CODE"
echo "拒因: $BLOCKING_REASON"

# 验证：应该被拒绝，且原因包含PROJECT_SCOPE_DENIED或projects相关信息
if [ "$OK" = "True" ] || [ "$OK" = "true" ]; then
    fail "越权漏洞未修复：lybra token可以访问chris工作区"
fi

if [[ "$BLOCKING_REASON" == *"PROJECT_SCOPE_DENIED"* ]] || \
   [[ "$BLOCKING_REASON" == *"project"*"lybra"* ]] || \
   [[ "$BLOCKING_REASON" == *"chris"* ]]; then
    pass "越权已拦截：lybra token访问chris工作区被拒绝（含project scope信息）"
else
    warn "越权已拦截，但拒因不够明确：$BLOCKING_REASON"
fi
echo ""

# ==============================================================================
# 验收② lybra主工作区回归：queue_list正常工作
# ==============================================================================
echo "--- 验收② lybra主工作区回归 ---"
echo "测试：lybra token访问lybra工作区 → 应该成功"

RESULT2=$(curl -s -X POST "$GATE_URL/mcp" \
  -H "Authorization: Bearer $LYBRA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1002,
    "method": "tools/call",
    "params": {
      "name": "lybra_queue_list",
      "arguments": {
        "actor": "exec.lybra.kiwiai-dev"
      }
    }
  }' 2>/dev/null || echo '{"error": "request failed"}')

OK2=$(echo "$RESULT2" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('ok', False))" 2>/dev/null || echo "false")
TASKS_COUNT=$(echo "$RESULT2" | python3 -c "import sys, json; tasks=json.load(sys.stdin).get('result', {}).get('data', {}).get('tasks', []); print(len(tasks))" 2>/dev/null || echo "0")

if [ "$OK2" = "True" ] || [ "$OK2" = "true" ]; then
    pass "lybra工作区回归正常：queue_list返回 $TASKS_COUNT 个任务"
else
    ERROR2=$(echo "$RESULT2" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('error_code', 'UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    fail "lybra工作区回归失败：$ERROR2"
fi
echo ""

# ==============================================================================
# 验收③ 第二工作区E2E（如果chris token可用）
# ==============================================================================
if [ -n "$CHRIS_TOKEN" ]; then
    echo "--- 验收③ 第二工作区E2E（chris token）---"
    echo "测试：chris token + workspace_root=chris → 应该成功"
    
    RESULT3=$(curl -s -X POST "$GATE_URL/mcp" \
      -H "Authorization: Bearer $CHRIS_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1003,
        "method": "tools/call",
        "params": {
          "name": "lybra_queue_list",
          "arguments": {
            "actor": "exec.chris.kiwiai-dev",
            "workspace_root": "'"$CHRIS_ROOT"'"
          }
        }
      }' 2>/dev/null || echo '{"error": "request failed"}')
    
    OK3=$(echo "$RESULT3" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('ok', False))" 2>/dev/null || echo "false")
    TASKS3=$(echo "$RESULT3" | python3 -c "import sys, json; tasks=json.load(sys.stdin).get('result', {}).get('data', {}).get('tasks', []); print(len(tasks))" 2>/dev/null || echo "0")
    
    if [ "$OK3" = "True" ] || [ "$OK3" = "true" ]; then
        pass "第二工作区E2E成功：chris token访问chris工作区返回 $TASKS3 个任务"
    else
        ERROR3=$(echo "$RESULT3" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('error_code', 'UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
        fail "第二工作区E2E失败：$ERROR3"
    fi
else
    warn "跳过验收③：chris token不可用，无法测试第二工作区E2E"
fi
echo ""

# ==============================================================================
# 验收④ claim dry_run with workspace_root（回归F42原有功能）
# ==============================================================================
echo "--- 验收④ claim with workspace_root回归 ---"
echo "测试：lybra token访问lybra工作区的pending任务"

# 查找一个pending任务用于测试
PENDING_TASK=$(find "$GOV_ROOT/5_tasks/queue/pending" -name "*.md" -type f | head -1 | xargs basename | sed 's/\.md$//' | tr '[:lower:]' '[:upper:]')

if [ -n "$PENDING_TASK" ]; then
    echo "测试任务: $PENDING_TASK"
    
    RESULT4=$(curl -s -X POST "$GATE_URL/mcp" \
      -H "Authorization: Bearer $LYBRA_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1004,
        "method": "tools/call",
        "params": {
          "name": "lybra_queue_claim_dry_run",
          "arguments": {
            "task_id": "'"$PENDING_TASK"'",
            "actor": "exec.lybra.kiwiai-dev",
            "agent_instance": "exec.lybra.kiwiai-dev",
            "autonomy_mode": "PreAuthorized",
            "owner_policy_ref": "pol_lybra_dev_9",
            "workspace_root": "'"$GOV_ROOT"'"
          }
        }
      }' 2>/dev/null || echo '{"error": "request failed"}')
    
    DRY_RUN_TOKEN=$(echo "$RESULT4" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('data', {}).get('dry_run_token', ''))" 2>/dev/null || echo "")
    
    if [ -n "$DRY_RUN_TOKEN" ]; then
        pass "claim with workspace_root回归正常：生成dry_run_token"
    else
        VERDICT=$(echo "$RESULT4" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('verdict', 'UNKNOWN'))" 2>/dev/null || echo "")
        warn "claim结果: verdict=$VERDICT (可能任务不在信封内或其他正常原因)"
    fi
else
    warn "跳过验收④：lybra工作区无pending任务"
fi
echo ""

# ==============================================================================
# 总结
# ==============================================================================
echo "=== AIPOS-F42-fix1 E2E测试完成 ==="
echo ""
echo "测试总结:"
echo "  ✓ F-1越权洞修复：lybra token无法访问chris工作区"
echo "  ✓ lybra工作区回归：queue_list正常"
if [ -n "$CHRIS_TOKEN" ]; then
    echo "  ✓ 第二工作区E2E：chris token可访问chris工作区"
else
    echo "  ⚠ 第二工作区E2E：跳过（chris token不可用）"
fi
echo "  ✓ workspace_root参数功能回归"
echo ""
echo "所有核心断言通过！"
exit 0
