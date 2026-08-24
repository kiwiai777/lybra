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
echo
echo "========================================================"
if [ "$overall" -eq 0 ]; then
  echo " ALL TEST FILES PASS"
else
  echo " SOME TESTS FAILED"
fi
echo "========================================================"
exit $overall
