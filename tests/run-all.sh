#!/usr/bin/env bash
# run-all.sh —— 跑产品仓 tests/ 目录下的全部 Python 测试
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "========================================================"
echo " Lybra 产品仓 Python 测试套件"
echo "========================================================"
echo "REPO_ROOT: $REPO_ROOT"
echo

overall=0

# AIPOS-F53: 修复轮承接判定 (fix链 + 结案-承接)
echo
echo "── tests/test_aipos_f53_fix_chain_lineage.py ─────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f53_fix_chain_lineage.py"; then
  echo "✓ tests/test_aipos_f53_fix_chain_lineage.py PASS"
else
  echo "✗ tests/test_aipos_f53_fix_chain_lineage.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f53_continuation_lineage.py ──────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f53_continuation_lineage.py"; then
  echo "✓ tests/test_aipos_f53_continuation_lineage.py PASS"
else
  echo "✗ tests/test_aipos_f53_continuation_lineage.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f53_orphan_rejection.py ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f53_orphan_rejection.py"; then
  echo "✓ tests/test_aipos_f53_orphan_rejection.py PASS"
else
  echo "✗ tests/test_aipos_f53_orphan_rejection.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f53_real_world_replay.py ─────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f53_real_world_replay.py"; then
  echo "✓ tests/test_aipos_f53_real_world_replay.py PASS"
else
  echo "✗ tests/test_aipos_f53_real_world_replay.py FAIL"
  overall=1
fi

echo
echo "========================================================"
if [ "$overall" -eq 0 ]; then
  echo " ALL TEST FILES PASS"
else
  echo " SOME TESTS FAILED"
fi
echo "========================================================"
exit $overall
