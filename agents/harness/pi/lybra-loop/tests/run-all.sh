#!/usr/bin/env bash
# run-all —— 跑 contrib/LYBRA-EXT-001 的全部 headless 测试。
# 依赖:Node ≥ 22(类型剥离);无需 npm install(纯 node + node: 内置模块)。
set -u
cd "$(dirname "$0")/.."
echo "========================================================"
echo " LYBRA-EXT-001 headless 测试套件"
echo "========================================================"
declare -a files=(
  "tests/loop-decisions.test.ts"
  "tests/gate-client.test.ts"
  "tests/c2-identity-resolution.test.ts"
  "tests/loop-engine.test.ts"
  "tests/lybra-loop.test.ts"
  "tests/tick-mechanism.test.ts"
  "tests/f8-running-flag.test.ts"
  "tests/verbs-conformance.test.ts"
  "tests/f11-runtime-reset.test.ts"
  "tests/f12-gate-territory.test.ts"
  "tests/f17-derivation-homology.test.ts"
  "tests/f16-cooldown.test.ts"
  "tests/f15b-voice-persistence.test.ts"
  "tests/f18-version-stamp-voice.test.ts"
  "tests/f19-watermark.test.ts"
  "tests/f20-sync-command.test.ts"
  "tests/f22b-yaml-serialization.test.ts"
  "tests/f23-enroll-command.test.ts"
  "tests/f24a-enroll-guardrails.test.ts"
  "tests/f29b-hosted-return.test.ts"
  "tests/f32-custom-role-envelope.test.ts"
  "tests/f32b-gate-registry-source.test.ts"
  "tests/f33-return-homology.test.ts"
  "tests/f35a-audit-cold-start.test.ts"
  "tests/f35b-audit-verdict-hosted.test.ts"
  "tests/f35c-hyphenated-task-id.test.ts"
  "tests/f36-first-tick-cold-start.test.ts"
  "tests/f37a-held-resume-redgreen.test.ts"
  "tests/f37b-credential-copy-redgreen.test.ts"
  "tests/f37c-claim-idempotent-redgreen.test.ts"
  "tests/f38a-derivation-validation.test.ts"
  "tests/f38b-inflight-quota.test.ts"
  "tests/f38c-claim-idempotent.test.ts"
  "tests/f22-advisor-onboarding.test.ts"
)
overall=0
for f in "${files[@]}"; do
  echo
  echo "── $f ──────────────────────────────────────────"
  if node "$f"; then
    echo "✓ $f PASS"
  else
    echo "✗ $f FAIL"
    overall=1
  fi
done

# ── AIPOS-F41B: 分发一致性夹具(经 bin 入常驻) ──────────────────
# AIPOS-F43-fix1: held卡号截断修复 + F43三大项
echo
echo "── tests/f43-fix1-comprehensive.test.ts ───────────────────────────────────────────────────"
if node tests/f43-fix1-comprehensive.test.ts > /dev/null 2>&1; then
  echo "✓ tests/f43-fix1-comprehensive.test.ts PASS"
else
  echo "✗ tests/f43-fix1-comprehensive.test.ts FAIL"
  overall=1
fi


# 验证章程硬规矩分发与手册单一真相源一致(Δ=0,既有 Python 测试)
echo
echo "── tests/test_aipos_f41_hard_rules.py (分发一致性) ──────────────────────────────────────────"
REPO_ROOT="$(cd "$(dirname "$0")/../../../../.." && pwd)"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f41_hard_rules.py"; then
  echo "✓ tests/test_aipos_f41_hard_rules.py PASS"
else
  echo "✗ tests/test_aipos_f41_hard_rules.py FAIL"
  overall=1
fi

# AIPOS-F44A: 门应答开口三项(额度告知+报错带路+N6待办)
echo
echo "── tests/test_aipos_f44a_response_opening.py (门应答开口三项) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f44a_response_opening.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f44a_response_opening.py PASS"
else
  echo "✗ tests/test_aipos_f44a_response_opening.py FAIL"
  overall=1
fi

# AIPOS-F47: 裁决提交会话绑定放宽(F34 同款)
echo
echo "── tests/test_aipos_f47_verdict_session_drift.py (裁决会话放宽) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f47_verdict_session_drift.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f47_verdict_session_drift.py PASS"
else
  echo "✗ tests/test_aipos_f47_verdict_session_drift.py FAIL"
  overall=1
fi

# AIPOS-F44B-fix1: 派生与部署语义三项(级联终局判+修复轮承接+裁决提交解析病)
echo
echo "── tests/test_aipos_f44b_cascade_terminal.py (级联终局判) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f44b_cascade_terminal.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f44b_cascade_terminal.py PASS"
else
  echo "✗ tests/test_aipos_f44b_cascade_terminal.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f44b_fix_chain_inheritance.py (修复轮承接) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f44b_fix_chain_inheritance.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f44b_fix_chain_inheritance.py PASS"
else
  echo "✗ tests/test_aipos_f44b_fix_chain_inheritance.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f44b_verdict_dispatch_ref.py (裁决提交解析病) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f44b_verdict_dispatch_ref.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f44b_verdict_dispatch_ref.py PASS"
else
  echo "✗ tests/test_aipos_f44b_verdict_dispatch_ref.py FAIL"
  overall=1
fi

# AIPOS-F44C: 连接器六项(status文案+骨架验收+复工去重+输出分级+读报单文件+轮次判定)
echo
echo "── tests/test_aipos_f44c_status_wording.py (status文案说人话) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f44c_status_wording.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f44c_status_wording.py PASS"
else
  echo "✗ tests/test_aipos_f44c_status_wording.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f44c_skeleton_acceptance.py (骨架验收清单) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f44c_skeleton_acceptance.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f44c_skeleton_acceptance.py PASS"
else
  echo "✗ tests/test_aipos_f44c_skeleton_acceptance.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f44c_resume_dedup.py (复工提醒去重) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f44c_resume_dedup.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f44c_resume_dedup.py PASS"
else
  echo "✗ tests/test_aipos_f44c_resume_dedup.py FAIL"
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
