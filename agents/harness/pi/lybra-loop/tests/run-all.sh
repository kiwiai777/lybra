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
  "tests/f57-onboarding-guide.test.ts"
  "tests/f60-held-skeleton-dead-code.test.ts"
  "tests/f60-fix1-settle-skeleton.test.ts"
  "tests/f62-deadlock-root-cause.test.ts"
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

# AIPOS-F49: N3交回自检门四条判据(夹具入常驻/改动面在界内/有测试/RETURN非骨架)
echo
echo "── tests/test_aipos_f49_criterion_1_test_in_runall.py (判据① 夹具入常驻) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f49_criterion_1_test_in_runall.py"; then
  echo "✓ tests/test_aipos_f49_criterion_1_test_in_runall.py PASS"
else
  echo "✗ tests/test_aipos_f49_criterion_1_test_in_runall.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f49_criterion_2_changes_in_scope.py (判据② 改动面在界内) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f49_criterion_2_changes_in_scope.py"; then
  echo "✓ tests/test_aipos_f49_criterion_2_changes_in_scope.py PASS"
else
  echo "✗ tests/test_aipos_f49_criterion_2_changes_in_scope.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f49_criterion_3_has_tests.py (判据③ 有测试) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f49_criterion_3_has_tests.py"; then
  echo "✓ tests/test_aipos_f49_criterion_3_has_tests.py PASS"
else
  echo "✗ tests/test_aipos_f49_criterion_3_has_tests.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f49_criterion_4_return_not_skeleton.py (判据④ RETURN非骨架) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f49_criterion_4_return_not_skeleton.py"; then
  echo "✓ tests/test_aipos_f49_criterion_4_return_not_skeleton.py PASS"
else
  echo "✗ tests/test_aipos_f49_criterion_4_return_not_skeleton.py FAIL"
  overall=1
fi

# AIPOS-F49-fix1: owner_confirmation_token 强制放行机制（Owner底线：Lybra永不阻塞项目）
echo
echo "── tests/test_aipos_f49_fix1_owner_waiver.py (Owner放行机制) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f49_fix1_owner_waiver.py"; then
  echo "✓ tests/test_aipos_f49_fix1_owner_waiver.py PASS"
else
  echo "✗ tests/test_aipos_f49_fix1_owner_waiver.py FAIL"
  overall=1
fi

# AIPOS-F49-fix1-fix1: 修复 owner_confirmation_token 数据流断裂（注入到 mcp_return_metadata）
echo
echo "── tests/test_aipos_f49_fix1_fix1_dataflow.py (数据流贯通) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f49_fix1_fix1_dataflow.py"; then
  echo "✓ tests/test_aipos_f49_fix1_fix1_dataflow.py PASS"
else
  echo "✗ tests/test_aipos_f49_fix1_fix1_dataflow.py FAIL"
  overall=1
fi

# AIPOS-F44D-A: CLI角色解析不写死——自定义角色项目可用(chris迁移直接阻塞)
echo
echo "── tests/test_aipos_f44d_a_role_resolution_redgreen.py (先红后绿) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f44d_a_role_resolution_redgreen.py"; then
  echo "✓ tests/test_aipos_f44d_a_role_resolution_redgreen.py PASS"
else
  echo "✗ tests/test_aipos_f44d_a_role_resolution_redgreen.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f44d_a_role_resolution_negative.py (负夹具) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f44d_a_role_resolution_negative.py"; then
  echo "✓ tests/test_aipos_f44d_a_role_resolution_negative.py PASS"
else
  echo "✗ tests/test_aipos_f44d_a_role_resolution_negative.py FAIL"
  overall=1
fi

# AIPOS-F49-fix1-fix1-fix1: 修复 UnboundLocalError - data.get() 前向引用
echo
echo "── tests/test_aipos_f49_fix1_fix1_fix1_dataflow_fix.py ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f49_fix1_fix1_fix1_dataflow_fix.py"; then
  echo "✓ tests/test_aipos_f49_fix1_fix1_fix1_dataflow_fix.py PASS"
else
  echo "✗ tests/test_aipos_f49_fix1_fix1_fix1_dataflow_fix.py FAIL"
  overall=1
fi

# AIPOS-F50: 凭据 projects 域按治理根推导 + queue_list 口径统一
echo
echo "── tests/test_aipos_f50_projects_derivation.py ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f50_projects_derivation.py"; then
  echo "✓ tests/test_aipos_f50_projects_derivation.py PASS"
else
  echo "✗ tests/test_aipos_f50_projects_derivation.py FAIL"
  overall=1
fi

# AIPOS-F50-fix1: governance_root 回落修复
echo
echo "── tests/test_aipos_f50_fix1_governance_root_fallback.py ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f50_fix1_governance_root_fallback.py"; then
  echo "✓ tests/test_aipos_f50_fix1_governance_root_fallback.py PASS"
else
  echo "✗ tests/test_aipos_f50_fix1_governance_root_fallback.py FAIL"
  overall=1
fi

# AIPOS-F52: 两层回落根治 (CLI 传完整自包含码 + workspace_root→project 从 project.json 读取)
echo
echo "── tests/test_aipos_f52_two_layer_fallback_fix.py ────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f52_two_layer_fallback_fix.py"; then
  echo "✓ tests/test_aipos_f52_two_layer_fallback_fix.py PASS"
else
  echo "✗ tests/test_aipos_f52_two_layer_fallback_fix.py FAIL"
  overall=1
fi

# AIPOS-F53: 修复轮承接判定 (fix链末端裁决覆盖原卡commit)
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

# AIPOS-F54: 新工位一条命令配齐(enroll 落可启动最小集: .pi接线+owner_policy_ref+lybra_bin)
echo
echo "── tests/test_aipos_f54.py (工位可启动最小集) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f54.py"; then
  echo "✓ tests/test_aipos_f54.py PASS"
else
  echo "✗ tests/test_aipos_f54.py FAIL"
  overall=1
fi

# AIPOS-F54-fix1: 可启动最小集补齐 lybra_bin + workspace_root 单源校正
echo
echo "── tools/aipos_cli/tests/test_aipos_f54_fix1.py (lybra_bin+workspace_root 补齐) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f54_fix1.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos_f54_fix1.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos_f54_fix1.py FAIL"
  overall=1
fi

# AIPOS-F57: 从0接新项目全流程固化(一条命令上岗+接入skill随分发下发)
echo
echo "── tools/aipos_cli/tests/test_aipos_f57_onboarding.py (从0接新项目全流程) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f57_onboarding.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos_f57_onboarding.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos_f57_onboarding.py FAIL"
  overall=1
fi

# AIPOS-F55: 门记录加载加缓存与增量(正确性三红线+性能先红后绿)
echo
echo "── tests/test_aipos_f55.py (记录缓存与增量) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f55.py"; then
  echo "✓ tests/test_aipos_f55.py PASS"
else
  echo "✗ tests/test_aipos_f55.py FAIL"
  overall=1
fi

# AIPOS-F56: 空闲带路出一行可复制指令(Owner 唤醒行)
echo
echo "── tests/test_aipos_f56_wakeup_line.py (空闲唤醒行) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f56_wakeup_line.py"; then
  echo "✓ tests/test_aipos_f56_wakeup_line.py PASS"
else
  echo "✗ tests/test_aipos_f56_wakeup_line.py FAIL"
  overall=1
fi

# AIPOS-F58: 工位私有状态自我保护(git exclude 登记, 防 `git stash -u` 连坐抹凭据)
echo
echo "── tools/aipos_cli/tests/test_aipos_f58_git_exclude.py (工位 git exclude 保护) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f58_git_exclude.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos_f58_git_exclude.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos_f58_git_exclude.py FAIL"
  overall=1
fi

# AIPOS-F59: token 选取按 (role, 项目域)、旧条目留痕退场
echo
echo "── tests/test_token_resolver.py (token resolver 统一实现) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_token_resolver.py" -v --tb=short; then
  echo "✓ tests/test_token_resolver.py PASS"
else
  echo "✗ tests/test_token_resolver.py FAIL"
  overall=1
fi

# AIPOS-F46: 写卡序列化全量收敛(毒字段夹具+末道自检+grep断言)
echo
echo "── tests/test_aipos_f46_serialization_convergence.py (写卡序列化全量收敛) ──────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f46_serialization_convergence.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f46_serialization_convergence.py PASS"
else
  echo "✗ tests/test_aipos_f46_serialization_convergence.py FAIL"
  overall=1
fi

# AIPOS-F51: 自检门豁免出口修真——dry_run阶段即可豁免+越界拒收给出可执行出口
echo
echo "── tests/test_aipos_f51_self_check_waiver_dry_run.py (自检门豁免出口修真) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f51_self_check_waiver_dry_run.py"; then
  echo "✓ tests/test_aipos_f51_self_check_waiver_dry_run.py PASS"
else
  echo "✗ tests/test_aipos_f51_self_check_waiver_dry_run.py FAIL"
  overall=1
fi

# AIPOS-F61: 收尾原子化与结算状态一次读齐
echo
echo "── tests/test_aipos_f61_settle_atomicity.py (收尾原子化) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f61_settle_atomicity.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f61_settle_atomicity.py PASS"
else
  echo "✗ tests/test_aipos_f61_settle_atomicity.py FAIL"
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
