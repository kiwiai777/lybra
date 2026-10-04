#!/usr/bin/env bash
# run-all —— 产品仓常驻夹具总入口(Python 夹具 + 仍在用的 TS 夹具)。
# AIPOS-F91: 自退役的 agents/harness/pi/lybra-loop/tests/ 迁出到 tests/run-all.sh(AIPOS-F93: 门交回检查读的位置声明 = lybra 治理根 project.json test_contract.runall_path,
#   产品代码零写死); 仍在用的 TS 夹具迁至 tests/ts/。lybra-loop 扩展本体及其专属夹具随退役删除。
# 依赖:Node ≥ 22(类型剥离);无需 npm install(纯 node + node: 内置模块)。
set -u
# 本脚本在 <产品仓根>/tests/ 下: 先 cd 到产品仓根, 之后一切相对路径与 REPO_ROOT 都以此为准(与调用方 cwd、相对/绝对调用方式无关; 承 F66B)。
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
echo "========================================================"
echo " Lybra 产品仓 run-all 常驻夹具"
echo "========================================================"
declare -a files=(
  "tests/ts/f32-custom-role-envelope.test.ts"
  "tests/ts/f32b-gate-registry-source.test.ts"
  "tests/ts/f37b-credential-copy-redgreen.test.ts"
  "tests/ts/f38a-derivation-validation.test.ts"
  "tests/ts/f22b-yaml-serialization.test.ts"
  "tests/ts/f86-go-kickoff.test.ts"
  "tests/ts/f87-go-next-card.test.ts"
  "tests/ts/f93-go-report-fields.test.ts"
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

# 验证章程硬规矩分发与手册单一真相源一致(Δ=0,既有 Python 测试)
echo
echo "── tests/test_aipos_f41_hard_rules.py (分发一致性) ──────────────────────────────────────────"
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

echo
echo "── tests/test_aipos_f66_project_resolution_convergence.py ────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f66_project_resolution_convergence.py" -v; then
  echo "✓ tests/test_aipos_f66_project_resolution_convergence.py PASS"
else
  echo "✗ tests/test_aipos_f66_project_resolution_convergence.py FAIL"
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


# AIPOS-F65A: 报告链止血三件(claim建骨架+return校验落位+双目录消灭)
echo
echo "── tests/test_aipos_f65a_return_skeleton.py (报告链止血三件) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f65a_return_skeleton.py"; then
  echo "✓ tests/test_aipos_f65a_return_skeleton.py PASS"
else
  echo "✗ tests/test_aipos_f65a_return_skeleton.py FAIL"
  overall=1
fi

# AIPOS-F65A-fix2: PreAuthorized一段式认领骨架创建(主路接入)
echo
echo "── tests/test_aipos_f65a_fix2_preauthorized_skeleton.py (PreAuthorized一段式骨架) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f65a_fix2_preauthorized_skeleton.py"; then
  echo "✓ tests/test_aipos_f65a_fix2_preauthorized_skeleton.py PASS"
else
  echo "✗ tests/test_aipos_f65a_fix2_preauthorized_skeleton.py FAIL"
  overall=1
fi

# AIPOS-F65C: 卫生杂项三件(坏frontmatter修复·封存连接器剔除·未知子命令出声·占位符检测改进)
echo
echo "── tests/test_f65c_frontmatter_repair.py (件① 坏frontmatter修复通路) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_f65c_frontmatter_repair.py"; then
  echo "✓ tests/test_f65c_frontmatter_repair.py PASS"
else
  echo "✗ tests/test_f65c_frontmatter_repair.py FAIL"
  overall=1
fi

echo
echo "── tests/test_f65c_unknown_subcommand.py (件③ 未知子命令出声) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_f65c_unknown_subcommand.py"; then
  echo "✓ tests/test_f65c_unknown_subcommand.py PASS"
else
  echo "✗ tests/test_f65c_unknown_subcommand.py FAIL"
  overall=1
fi

echo
echo "── tests/test_f65c_placeholder_detection.py (件④ 占位符检测改进) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_f65c_placeholder_detection.py"; then
  echo "✓ tests/test_f65c_placeholder_detection.py PASS"
else
  echo "✗ tests/test_f65c_placeholder_detection.py FAIL"
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

# AIPOS-F63: fail-closed普查与改造—统一必填校验,占位符检测,空证据拒收,needs_owner执行
echo
echo "── tests/test_aipos_f63_fail_closed.py (fail-closed普查与改造) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f63_fail_closed.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f63_fail_closed.py PASS"
else
  echo "✗ tests/test_aipos_f63_fail_closed.py FAIL"
  overall=1
fi

echo
# AIPOS-F72: 派审幂等判据修真—dispatch链指向废卡/零裁决时不得挡复派
echo "── tests/test_aipos_f72_dispatch_chain_validity.py (派审幂等判据修真) ──────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f72_dispatch_chain_validity.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f72_dispatch_chain_validity.py PASS"
else
  echo "✗ tests/test_aipos_f72_dispatch_chain_validity.py FAIL"
  overall=1
fi

echo
# AIPOS-F70: 裁决绑精确产物—artifact_subject带commit_sha/tree_hash,finalize/deploy精确SHA核对
echo "── tests/test_aipos_f70_artifact_binding.py (裁决绑精确产物) ──────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f70_artifact_binding.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f70_artifact_binding.py PASS"
else
  echo "✗ tests/test_aipos_f70_artifact_binding.py FAIL"
  overall=1
fi

echo
# AIPOS-F70-fix1: 裁决干运行快照稳定性修复—排除易变timestamp/verdict_id,对齐queue_return机制
echo "── tests/test_aipos_f70_fix1_snapshot_stable.py (裁决快照稳定性) ──────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f70_fix1_snapshot_stable.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f70_fix1_snapshot_stable.py PASS"
else
  echo "✗ tests/test_aipos_f70_fix1_snapshot_stable.py FAIL"
  overall=1
fi

echo
# AIPOS-F70-fix2: finalize比对对象修正—审的是卡分支,比对对象=卡分支tip而非main HEAD
echo "── tests/test_aipos_f70_fix2_verdict_comparison_target.py (比对对象修正) ──────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f70_fix2_verdict_comparison_target.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f70_fix2_verdict_comparison_target.py PASS"
else
  echo "✗ tests/test_aipos_f70_fix2_verdict_comparison_target.py FAIL"
  overall=1
fi

echo
# AIPOS-F71: next单一推导实现—合并turn-advancer+next-step,schema单一读取口,审计裁决推导
echo "── tests/test_aipos_f71_next_command.py (next单一推导实现) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f71_next_command.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f71_next_command.py PASS"
else
  echo "✗ tests/test_aipos_f71_next_command.py FAIL"  overall=1
fi

echo
# AIPOS-F64: 单一记录写入器—收敛到record_writer.py,派生口卡形统一(simple+audit:none)
echo "── tests/test_f64_unified_writer.py (单一记录写入器) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_f64_unified_writer.py"; then
  echo "✓ tests/test_f64_unified_writer.py PASS"
else
  echo "✗ tests/test_f64_unified_writer.py FAIL"
  overall=1
fi

echo "── tests/test_f64_derivation_consistency.py (派生口卡形一致性) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_f64_derivation_consistency.py"; then
  echo "✓ tests/test_f64_derivation_consistency.py PASS"
else
  echo "✗ tests/test_f64_derivation_consistency.py FAIL"
  overall=1
fi

echo "── tests/test_f64_fix1_schema_driven.py (schema声明驱动) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_f64_fix1_schema_driven.py"; then
  echo "✓ tests/test_f64_fix1_schema_driven.py PASS"
else
  echo "✗ tests/test_f64_fix1_schema_driven.py FAIL"
  overall=1
fi

# AIPOS-F68: 任务卡机器生成区——schema已定的一律机器派生
echo
echo "── tools/aipos_cli/tests/test_machine_zone.py (机器区派生与校验) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_machine_zone.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_machine_zone.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_machine_zone.py FAIL"
  overall=1
fi

echo
echo "── tools/aipos_cli/tests/test_machine_zone_integration.py (机器区集成测试) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_machine_zone_integration.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_machine_zone_integration.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_machine_zone_integration.py FAIL"
  overall=1
fi

# AIPOS-F67: lybra brief命令实现(零新解析器+fail-closed+冷启动四问)
echo
echo "── tests/test_aipos_f67_brief.py (lybra brief冷启动简报) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f67_brief.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f67_brief.py PASS"
else
  echo "✗ tests/test_aipos_f67_brief.py FAIL"
  overall=1
fi

# AIPOS-F69: 顾问侧治理落库唯一口——governance_commit解绑卡号+并发安全+push后校验
echo
echo "── tests/test_governance_commit_f69.py (治理落库唯一口) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_governance_commit_f69.py" -v --tb=short; then
  echo "✓ tests/test_governance_commit_f69.py PASS"
else
  echo "✗ tests/test_governance_commit_f69.py FAIL"
  overall=1
fi

# AIPOS-F79: governance_commit 精确批次提交——显式 --paths 白名单+dry-run 清单不动现场+正式提交只含选定文件+R6M 护栏照旧
echo
echo "── tests/test_governance_commit_f79.py (治理落库精确批次 --paths) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_governance_commit_f79.py" -v --tb=short; then
  echo "✓ tests/test_governance_commit_f79.py PASS"
else
  echo "✗ tests/test_governance_commit_f79.py FAIL"
  overall=1
fi

# AIPOS-F79C: governance_commit push 假阴性热修——双向 rev-list 判据+远端未前进直接 fast-forward+临时 linked worktree cherry-pick 不碰他人未提交文件+pushed=False 出声+目录 pathspec 内删除暂存
echo
echo "── tests/test_governance_commit_f79c.py (治理落库 push 判据/临时 worktree 整合/出声/目录删除) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_governance_commit_f79c.py" -v --tb=short; then
  echo "✓ tests/test_governance_commit_f79c.py PASS"
else
  echo "✗ tests/test_governance_commit_f79c.py FAIL"
  overall=1
fi

# AIPOS-F74: 交回自检判据⑤分支合规+存量卡机器区重生成+deploy空区间授权洞
echo
echo "── tools/aipos_cli/tests/test_aipos_f74.py (判据⑤分支合规+机器区重生成+deploy授权) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f74.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos_f74.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos_f74.py FAIL"
  overall=1
fi

# AIPOS-F66C: sync按部署声明prune+工位仓git隔离纪律分发
echo
echo "── tools/aipos_cli/tests/test_c4b_distribution.py::TestSyncPruning + tests/test_f66c_worktree_isolation.py (sync prune+worktree隔离) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_c4b_distribution.py::TestSyncPruning" "$REPO_ROOT/tests/test_f66c_worktree_isolation.py" -v --tb=short; then
  echo "✓ F66C tests PASS"
else
  echo "✗ F66C tests FAIL"
  overall=1
fi

# AIPOS-F76: 机器区补完——create/publish/regen三口共用同一纪律段派生函数+regen按卡阶段派生禁重置draft标记
echo
echo "── tools/aipos_cli/tests/test_aipos_f76_integration.py (工作纪律section三口一函数) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f76_integration.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos_f76_integration.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos_f76_integration.py FAIL"
  overall=1
fi

# AIPOS-F75: FAIL后修复通路单一化——自动fix派生默认关闭+返工节进卡面+章程与next联动
echo
echo "── tools/aipos_cli/tests/test_aipos_f75_rework_path_unification.py (FAIL修复通路单一化) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f75_rework_path_unification.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos_f75_rework_path_unification.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos_f75_rework_path_unification.py FAIL"
  overall=1
fi

# AIPOS-F73: 执行体零门——next --run机器扣扳机+卡面去门链+token scope收紧(移交F73C)
echo
echo "── tests/test_aipos_f73_executor_zero_gate.py (执行体零门) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f73_executor_zero_gate.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f73_executor_zero_gate.py PASS"
else
  echo "✗ tests/test_aipos_f73_executor_zero_gate.py FAIL"
  overall=1
fi

# AIPOS-F73B: 执行体零门·第二刀——认领腿(前置零N3→N6+件①②③)
echo
echo "── tests/test_aipos_f73b_pre0_n3_to_n6_chain.py (F73B前置零N3→N6链路) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f73b_pre0_n3_to_n6_chain.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f73b_pre0_n3_to_n6_chain.py PASS"
else
  echo "✗ tests/test_aipos_f73b_pre0_n3_to_n6_chain.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f73b_item1_claim_with_role_token.py (F73B件①认领) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f73b_item1_claim_with_role_token.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f73b_item1_claim_with_role_token.py PASS"
else
  echo "✗ tests/test_aipos_f73b_item1_claim_with_role_token.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f73b_item2_preauth_protocol.py (F73B件②PreAuthorized协议) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f73b_item2_preauth_protocol.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f73b_item2_preauth_protocol.py PASS"
else
  echo "✗ tests/test_aipos_f73b_item2_preauth_protocol.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f73c_zero_gate_closeout.py (F73C 零门收口:角色判据/scope声明/rework CLI) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f73c_zero_gate_closeout.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f73c_zero_gate_closeout.py PASS"
else
  echo "✗ tests/test_aipos_f73c_zero_gate_closeout.py FAIL"
  overall=1
fi

# AIPOS-F78: 引擎无关——前置零①–⑨(驱动方 token/驱动方身份/close 三字段/判据读分支/lane 受限 amend/世系含 withdrawn+dev_override/
# JSON 键级合并+复审/门动词精确校验/PASS 卡承接) + 件①–④(harness/lane 声明·单一渲染器·artifact ingest·落点读项目声明)
echo
echo "── tests/test_aipos_f78_engine_agnostic.py (F78 引擎无关: 前置零①–⑨ + 件①–④, lybra 形/chris 形靶场) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f78_engine_agnostic.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f78_engine_agnostic.py PASS"
else
  echo "✗ tests/test_aipos_f78_engine_agnostic.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f32_custom_role_envelope.py (F32 自定义角色信封(F73C 夹具声明 manual_gate_mode)) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f32_custom_role_envelope.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f32_custom_role_envelope.py PASS"
else
  echo "✗ tests/test_aipos_f32_custom_role_envelope.py FAIL"
  overall=1
fi

echo
echo "── tests/test_aipos_f32b_gate_registry_source.py (F32B 门注册表单源(F73C 夹具声明 manual_gate_mode)) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f32b_gate_registry_source.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f32b_gate_registry_source.py PASS"
else
  echo "✗ tests/test_aipos_f32b_gate_registry_source.py FAIL"
  overall=1
fi

echo
echo "── tools/aipos_cli/tests/test_aipos352_custom_roles.py (352 自定义角色 scope 解析(F73C 改为新声明)) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos352_custom_roles.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos352_custom_roles.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos352_custom_roles.py FAIL"
  overall=1
fi

echo
echo "── tools/aipos_cli/tests/test_confirm_client.py (confirm_client(F73C executor 零 confirm scope)) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_confirm_client.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_confirm_client.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_confirm_client.py FAIL"
  overall=1
fi

# AIPOS-F73D: 顾问侧驱动器 lybra loop(四出口/信封/parser 夹具/前置一二三)+ 本卡改动的存量夹具(agent watch expect_ready; F71/F73B 已在上方登记)
echo
echo "── tests/test_aipos_f73d_loop_driver.py (F73D lybra loop 驱动器) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f73d_loop_driver.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f73d_loop_driver.py PASS"
else
  echo "✗ tests/test_aipos_f73d_loop_driver.py FAIL"
  overall=1
fi

# AIPOS-F73E: loop 账务动词身份定案(token=驱动方, actor=该卡认领实例; 门记 submitted_by)+ 改写断言的存量夹具(F73D/F78 上方已登记)
echo
echo "── tests/test_aipos_f73e_ledger_identity.py (F73E 账务动词身份: actor=认领实例/submitted_by=驱动方) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f73e_ledger_identity.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f73e_ledger_identity.py PASS"
else
  echo "✗ tests/test_aipos_f73e_ledger_identity.py FAIL"
  overall=1
fi

# AIPOS-F78B: chris 形零迁移收口(卡按 frontmatter 查找/finalize_mode external/驱动方一阶段)+ 件④授权执行实撞 + 件⑤ F73E 顺手实撞
echo
echo "── tests/test_aipos_f78b_chris_zero_migration.py (F78B chris 形零迁移: find_task_card/external finalize/驱动方一阶段/件④⑤) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f78b_chris_zero_migration.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f78b_chris_zero_migration.py PASS"
else
  echo "✗ tests/test_aipos_f78b_chris_zero_migration.py FAIL"
  overall=1
fi

# AIPOS-F78B 件⑤c: F28 自定义角色凭据持久化夹具入常驻; 活工位用例(名含 real_gate, 只比指纹)按基线口径 -k 排除
echo
echo "── tests/test_aipos_f28_custom_role_credential_persistence.py (F28 自定义角色凭据持久化(件⑤c 活工位用例 -k 排除)) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f28_custom_role_credential_persistence.py" -v --tb=short -k "not real_gate"; then
  echo "✓ tests/test_aipos_f28_custom_role_credential_persistence.py PASS"
else
  echo "✗ tests/test_aipos_f28_custom_role_credential_persistence.py FAIL"
  overall=1
fi

# AIPOS-F78C: 多仓项目·卡声明仓贯通(project.json repos 清单 + 卡 lane.repo 发布校验 / 单一解析 resolve_card_repo / 双仓靶场全链 + 一卡一仓拒 + 单仓回归 + chris 形停点)
echo
echo "── tests/test_aipos_f78c_card_repo.py (F78C 多仓项目: repos 清单声明/resolve_card_repo 贯通/双仓全链/LANE_REPO_UNDECLARED+INGEST_REPO_MISMATCH) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f78c_card_repo.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f78c_card_repo.py PASS"
else
  echo "✗ tests/test_aipos_f78c_card_repo.py FAIL"
  overall=1
fi

# AIPOS-F79D: 治理仓提交门·护栏缺口四件(四检单源模块 hook/CLI 共用·dry-run=正式拒因·工作区根按文件归属·hook 拒后清暂存·session 记录只落声明位+lint RECORD_EMPTY+repair 重铸)
echo
echo "── tests/test_aipos_f79d_commit_gate_guardrails.py (F79D 提交门四件: 四检单源/双工作区 ws_prefix/hook 拒后 index 还原/session 声明位+lint+repair) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f79d_commit_gate_guardrails.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f79d_commit_gate_guardrails.py PASS"
else
  echo "✗ tests/test_aipos_f79d_commit_gate_guardrails.py FAIL"
  overall=1
fi

# AIPOS-F66B: 多项目接入固化(件① 分发按工位项目归属过滤+章程=声明渲染物(seed_only 退役)·件② 护栏读声明 write_boundary 三级+读取口·件③ 审计卡报告落点文案单源)
echo
echo "── tests/test_aipos_f66b_project_scoped_distribution.py (F66B 多项目接入固化: 工位项目过滤/章程渲染/write_boundary/审计报告落点单源) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f66b_project_scoped_distribution.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f66b_project_scoped_distribution.py PASS"
else
  echo "✗ tests/test_aipos_f66b_project_scoped_distribution.py FAIL"
  overall=1
fi

# AIPOS-F66B 随件改动的存量夹具入常驻(N3 判据① 改过的 test 文件须在清单): F27 章程分发语义改写为渲染物(seed_only 退役, 临时 home 自起门子进程, 不碰真实工位/真门); aipos338 审计卡指令落点断言改为审计卡 ID 目录
echo
echo "── tests/test_aipos_f27_regression.py (F27 分发与落盘两案·F66B 改写: charter=声明渲染物/enroll cwd 落盘/connection 三全/无人陪跑 E2E) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f27_regression.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f27_regression.py PASS"
else
  echo "✗ tests/test_aipos_f27_regression.py FAIL"
  overall=1
fi

echo
echo "── tools/aipos_cli/tests/test_aipos338_audit_derivation.py (AIPOS-338 审计派生指令·F66B 报告落点=审计卡 ID 目录·F80 零门/manual 保留) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos338_audit_derivation.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos338_audit_derivation.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos338_audit_derivation.py FAIL"
  overall=1
fi

echo
echo "── tools/aipos_cli/tests/test_agent_watch_fs.py (agent watch 哨兵(F73D expect_ready 谓词零回归)) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_agent_watch_fs.py" -v --tb=short -k "not test_module_is_stdlib_only_zero_new_deps"; then
  echo "✓ tools/aipos_cli/tests/test_agent_watch_fs.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_agent_watch_fs.py FAIL"
  overall=1
fi

# AIPOS-F80: 章程与卡模板零门收口两件(件① 派生审计卡零门: 唯一判据 card_carries_gate_contract_section·manual_gate_mode 例外·regen 去节; 件② 三份章程母本占位化 + lybra/chris 双上下文渲染)
echo
echo "── tests/test_aipos_f80_zero_gate_charter.py (F80 审计卡零门/唯一判据/regen 去节/母本零项目字面/双上下文渲染) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f80_zero_gate_charter.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f80_zero_gate_charter.py PASS"
else
  echo "✗ tests/test_aipos_f80_zero_gate_charter.py FAIL"
  overall=1
fi

# AIPOS-F80 随件改动的存量夹具入常驻(改过的 test 文件须在清单): F12 派生审计卡断言改为零门交付纪律节(门领地纪律节仅 manual_gate_mode 项目)
echo
echo "── tools/aipos_cli/tests/test_aipos_f12_gate_territory.py (F12 门领地纪律节构造·F80 派生审计卡零门) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f12_gate_territory.py" -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_aipos_f12_gate_territory.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_aipos_f12_gate_territory.py FAIL"
  overall=1
fi

# AIPOS-F81: token 解析单源(loop_context.ConnectionResolver 委托 token_resolver: instance→role·排除 retired·全 retired fail-closed 带重签出口; pi loop-context.ts 及其 TS 夹具随 lybra-loop 扩展 AIPOS-F91 退役删除, Python↔TS 同构锁解除)
echo
echo "── tests/test_aipos_f81_token_single_source.py (F81 [retired,new] 客户端取新 token/全 retired fail-closed/声明单源) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f81_token_single_source.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f81_token_single_source.py PASS"
else
  echo "✗ tests/test_aipos_f81_token_single_source.py FAIL"
  overall=1
fi

# AIPOS-F82: 工位接入收口三件(共享落点 prune 按全角色声明并集·finalize-slice 退出执行体分发/enroll 接线走声明·章程种子走 charter_render/车道外 token 取值归单源+config.schema 声明 retired)
echo
echo "── tests/test_aipos_f82_workstation_intake.py + tools/aipos_cli/tests/test_c4b_distribution.py + tools/test_aipos_r1_conformance.py (F82 A→B→A→B 稳态/enroll 无悬空·渲染种子/[retired,new] 三处取新·conformance retired 形态) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f82_workstation_intake.py" "$REPO_ROOT/tools/aipos_cli/tests/test_c4b_distribution.py" "$REPO_ROOT/tools/test_aipos_r1_conformance.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f82_workstation_intake.py PASS"
else
  echo "✗ tests/test_aipos_f82_workstation_intake.py FAIL"
  overall=1
fi

# AIPOS-F83: 技能母本零门三件(分发技能正文去门动作与项目字面·finalize-slice/audit-card-template 退役/工具包单源·tool_package 与 lybra-loop 退役/sync 回收工位 .pi 悬空挂载)
echo
echo "── tests/test_aipos_f83_skills_zero_gate.py (F83 分发技能 grep 零命中/tool_package 盘点归类/.pi 悬空链回收·声明内暂缺不删·非本项目跳过·再 sync 稳态) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f83_skills_zero_gate.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f83_skills_zero_gate.py PASS"
else
  echo "✗ tests/test_aipos_f83_skills_zero_gate.py FAIL"
  overall=1
fi

# AIPOS-F84: 技能与装配遗留清理三件(顾问侧技能项目无关通用示例·命令示例 argparse 可解析/card-policy-author 退役/schema_loader 死函数·README 单源·rules 审计卡文案)
echo
echo "── tests/test_aipos_f84_advisor_skills_generic.py (F84 顾问侧技能 grep 零命中·占位说明·示例 argparse 解析/card-policy-author 零引用/死函数零调用·README 单源=distribution.schema·审计卡产品派生文案) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f84_advisor_skills_generic.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f84_advisor_skills_generic.py PASS"
else
  echo "✗ tests/test_aipos_f84_advisor_skills_generic.py FAIL"
  overall=1
fi

# AIPOS-F85: 接入向导与顾问技能通用化三件(onboarding guide 第 5/6 步零门可跑·guide 命令 argparse 不变量/斜杠命令由分发声明推导·truth-navigator 去项目专有治理文件名·advisor-commands 过渡期段去重)
echo
echo "── tests/test_aipos_f85_onboarding_guide_generic.py (F85 guide 每条 lybra 命令 build_parser 解析·斜杠命令∈分发声明·退役命令零出现/truth-navigator grep 零命中·治理结构键已声明/过渡期段唯一且为当前事实) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f85_onboarding_guide_generic.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f85_onboarding_guide_generic.py PASS"
else
  echo "✗ tests/test_aipos_f85_onboarding_guide_generic.py FAIL"
  overall=1
fi

# AIPOS-F86: pi 工位开工面三件(my-tasks 输出工作树/报告落点·产品单源推导, go.ts 只读不推、报错零门·章程「单一真相源」指向 COMMANDS.md·分发声明更正; TS 夹具 tests/ts/f86-go-kickoff 在上方 files 清单; f57 TS 夹具随 lybra-loop 扩展 AIPOS-F91 退役删除)
echo
echo "── tests/test_aipos_f86_workstation_kickoff.py (F86 三类卡 my-tasks 字段=claim 实际建树·拒因非空串·planGo kickoff 含产品字段/章程 grep ADVISOR-COMMANDS 零命中·渲染无残留/minimum_bootable_set 零门路径·夹具入 run-all) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f86_workstation_kickoff.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f86_workstation_kickoff.py PASS"
else
  echo "✗ tests/test_aipos_f86_workstation_kickoff.py FAIL"
  overall=1
fi

# AIPOS-F87: 卡面 YAML 根治三件(写入侧单源安全序列化+写后回读·lint FRONTMATTER_INVALID 与 repair 保值规整·my-tasks next_card 产品选卡; TS 夹具 f87-go-next-card 在上方 files 清单)+件④ 防碎片化不变量棘轮(基线 tests/f87_fragmentation_baseline.json)
echo
echo "── tests/test_aipos_f87_card_yaml_root_fix.py + tests/test_aipos_f87_fragmentation_ratchet.py (F87 交回怪值逐字还原·记录侧手拼退役·写后回读拒写/lint 点名坏卡·repair 逐字节保值·unresolved 拒改/next_card 选卡与原因列表/棘轮只减不增) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f87_card_yaml_root_fix.py" "$REPO_ROOT/tests/test_aipos_f87_fragmentation_ratchet.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f87_card_yaml_root_fix.py PASS"
else
  echo "✗ tests/test_aipos_f87_card_yaml_root_fix.py FAIL"
  overall=1
fi

# AIPOS-F88: 族 A-1 工作树与根的识别定位单源三件(门认领建树委托 _ensure_worktree·失败=worktree_error 不吞 warning/治理仓识别唯一结构判据 has_workspace_queue·Python↔TS 同判/根路径语义分域 governance_workspace_root·product_repo_root·lybra workspace roots·deploy/pre-commit 读 CLI; f22 TS 夹具 E 段改结构判据已在上方 files 清单; F78B item3 随件① 改为门认领建树后在工作树提交, 已在上方 F78B 块)
echo
echo "── tests/test_aipos_f88_root_single_source.py (F88 门认领落点=my-tasks·零 warning·双仓 lane.repo·建树失败显式拒因·WorktreeManager 委托/结构判据不看路径名·TS 镜像同判·子串判定清零/两命名函数·禁布局回退·车道内写死根清零·F71 换机器任意 cwd·roots CLI·deploy 只读靶场·pre-commit 读 CLI·write-guard fail-closed) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f88_root_single_source.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f88_root_single_source.py PASS"
else
  echo "✗ tests/test_aipos_f88_root_single_source.py FAIL"
  overall=1
fi

# AIPOS-F90: 推进流程收到产品命令三件(认领经 lybra loop 一段式·建树失败即拒认领·超时声明化+回读不报假失败/裁决经 artifact ingest 自动入门·模型字段取自会话记录·审计报告快照进 records/开工只走 /go·拒非本人在办/已结案/产物已交·顾问技能零手写门接口); 另跑本卡改过夹具的认领节点(门认领写入前建树: 靶场根改为带 main 提交的 git 仓)
echo
echo "── tests/test_aipos_f90_loop_one_stage.py (F90 靶场 pending→completed 只经 lybra loop·真门动词处理器经产品 CLI 薄壳·记录链完整/建树失败拒认领队列不变/缺陷①②③先红后绿/ingest 绑 tip·会话记录模型·自报不一致标出·快照复原/go 与卡号冷启动拒已结案·非本人·未认领·产物已交/技能 grep 零命中) + 认领节点 ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f90_loop_one_stage.py" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_controlled_execute.py::ControlledExecuteTests::test_execute_queue_claim_moves_pending_to_claimed" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_controlled_execute.py::ControlledExecuteTests::test_owner_confirmation_required_when_needs_owner" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_board_adapter_execute_integration.py::BoardAdapterExecuteIntegrationTests::test_queue_claim_flow_and_failures" \
    -v --tb=short; then
  echo "✓ tests/test_aipos_f90_loop_one_stage.py PASS"
else
  echo "✗ tests/test_aipos_f90_loop_one_stage.py FAIL"
  overall=1
fi

# AIPOS-F91: 退役老子系统不变量(templates 零退役子命令且项目无关/CLI 拒退役子命令/.pi 挂载回收三态: 声明内保留·分发区外不碰·分发区内未声明回收/run-all 位置单源) + 随语义改动的 F83 挂载回收用例
echo
echo "── tests/test_aipos_f91_retirement_invariants.py (F91 退役不变量 + G3 挂载回收三态) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f91_retirement_invariants.py" \
    "$REPO_ROOT/tests/test_aipos_f83_skills_zero_gate.py::test_item3_declared_missing_kept_foreign_untouched_extension_reclaimed" \
    -v --tb=short; then
  echo "✓ tests/test_aipos_f91_retirement_invariants.py PASS"
else
  echo "✗ tests/test_aipos_f91_retirement_invariants.py FAIL"
  overall=1
fi

# AIPOS-F89 件①: 落点与项目键只一份声明(H9 交回/裁决/台账落点只读 project.json paths·M8 队列根只读 task_loader.queue_root_for·M14 project.json 键全部声明、collaboration_profile 单读取口单缺省) + 随语义改动的 machine_zone 用例
echo
echo "── tests/test_aipos_f89_project_declaration_single_source.py (F89 件① probe 形四根全非缺省全程落声明位·门侧零写死·第二份声明已删·M14 键声明) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f89_project_declaration_single_source.py" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_machine_zone.py" \
    -v --tb=short; then
  echo "✓ tests/test_aipos_f89_project_declaration_single_source.py PASS"
else
  echo "✗ tests/test_aipos_f89_project_declaration_single_source.py FAIL"
  overall=1
fi

# AIPOS-F89 件②: 治理文档名不再是产品契约(M17: config.schema 去 governance_docs.files / paths.foundation_backlog·close 编年史与硬规矩来源读 project.json paths 可选声明, 未声明跳过+warning·新项目 close 不 BLOCK·章程「单一真相源」占位化) + 随语义改动的 F86/F85/F78/queue_close 用例
echo
echo "── tests/test_aipos_f89_governance_docs_contract.py (F89 件② 新项目无 lybra 文档 close 不 BLOCK·硬规矩来源缺省行为·新项目章程/模板/技能零 lybra 文档名) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f89_governance_docs_contract.py" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_queue_close.py" \
    -v --tb=short; then
  echo "✓ tests/test_aipos_f89_governance_docs_contract.py PASS"
else
  echo "✗ tests/test_aipos_f89_governance_docs_contract.py FAIL"
  overall=1
fi

# AIPOS-F89 件③: 审计卡开工面(a 认领审计卡建被审 tip 只读 detached 取证工作树·/go 选中审计卡 b my-tasks --workstation 产品解析身份·go.ts 只读 lybra_bin 无缺省 c 报告完成判据声明化·空模板不算产物 d 报告快照包记录头过护栏 B④) + 随语义改动的 F73D/F73E/F86/F90 用例
echo
echo "── tests/test_aipos_f89_audit_kickoff_surface.py (F89 件③ 取证工作树·--workstation·go.ts 只读 lybra_bin·完成判据三态·快照记录头过 B④) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f89_audit_kickoff_surface.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f89_audit_kickoff_surface.py PASS"
else
  echo "✗ tests/test_aipos_f89_audit_kickoff_surface.py FAIL"
  overall=1
fi

# AIPOS-F92: 接入向导可走通三件(① envelope mint --confirm 经门 owner_decision_record envelope 路径真实落盘·Owner 凭据跨项目
# ② 单门 home 根九步 guide·Owner 一次性动作 2 条·顾问 enroll 到治理根 + 技能交付到 Claude Code 会话目录·project set-repos
# ③ project new 写首份阶段快照·首次 finalize 不被拦) + 隔离靶场(临时 HOME/home 根/两产品仓/自起临时门)按 guide 走到首卡结案
# + 随语义改动的 F57/F73D/F84/F91 用例
echo
echo "── tests/test_aipos_f92_onboarding_walkthrough.py (F92 信封经门落盘·Owner 跨项目·九步 guide 靶场首卡结案·首份阶段快照·工作树根 exclude·部署适用性·审计卡终态 lint) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f92_onboarding_walkthrough.py" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_aipos_f57_onboarding.py" \
    -v --tb=short; then
  echo "✓ tests/test_aipos_f92_onboarding_walkthrough.py PASS"
else
  echo "✗ tests/test_aipos_f92_onboarding_walkthrough.py FAIL"
  overall=1
fi

# AIPOS-F93: 报告契约与交回约定声明化三件(① 报告必填字段由 transitions artifact_ingest 声明单源渲染进派生审计卡/执行卡落点句·
# my-tasks next_card.report_required_frontmatter(go.ts 原样列出, 审计卡带被审 tip/tree)·认领模板·章程, return 必填去 model, F92R 原样报告仍拒
# ② 注册码交付文案 onboarding.enroll_delivery 单源(门/CLI/向导) ③ 交回检查测试约定读 project.json test_contract, 未声明跳过+warning
# ④ governance-commit 正式提交子进程带 LYBRA_SCHEMA_DIR, PATH 无 lybra 的最小环境经真钩子通过)
# (随语义改动的 F49 判据①③ 用例已在上方 F49 块; F78/F78C/F87/F23 用例随各自块)
echo
echo "── tests/test_aipos_f93_report_contract_declared.py (F93 五处同源·F92R 原样仍拒·注册码文案单源·test_contract 声明化 lybra 形/probe 形·governance-commit 无 lybra PATH 过钩子) ────────────────────────────────────────────────────"
# F23 只登记本卡改动的用例节点(该文件另有 main 上即红的存量失败, 不整文件入清单; 承 F90 先例)
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f93_report_contract_declared.py" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_f23_enroll.py::TestMcpVerbs::test_two_phase_dry_run_confirm" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_f23_enroll.py::TestMcpVerbs::test_legacy_verb_delegates_to_same_implementation" \
    -v --tb=short; then
  echo "✓ tests/test_aipos_f93_report_contract_declared.py PASS"
else
  echo "✗ tests/test_aipos_f93_report_contract_declared.py FAIL"
  overall=1
fi

# AIPOS-F94: 真相落账三件(① loop 结案后自动 N6 落账: 推导核派生 lybra governance-commit --task-id(task 范围精确提交, 幂等,
# 他人暂存/护栏拒 = exit 2 不动他人暂存), close next_step 改产品命令 ② 接入向导第 1/2 步之后各 governance-commit --paths 本步产物,
# 建项目 decision_log 桩带声明 frontmatter, 顾问技能写明落账规则 ③ state lint GOVERNANCE_UNCOMMITTED(未跟踪/未提交/未推送), 全量 lint 一趟索引)
# (随语义改动的 F73D/F73E/F78B/F78C/F90/F92/F44A/F69 用例已在各自块: 走到结案的 loop 靶场治理根改为临时 git 仓 + 远端裸仓)
echo
echo "── tests/test_aipos_f94_governance_landing.py (F94 loop 结案自动落账·他人暂存拒·向导两步落账·lint 未落账三态·lookup 一趟索引) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f94_governance_landing.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f94_governance_landing.py PASS"
else
  echo "✗ tests/test_aipos_f94_governance_landing.py FAIL"
  overall=1
fi

# AIPOS-F95: 外部执行体/审计体自动拉起(① 授权信封下 loop 按声明模板在工位拉起假 harness, 执行体+审计体走到 completed, 一行式进度
# ② 无授权/--no-launch/无模板/定位不到/身份不符 → 手工 /go ③ 挂起超时/早退/SIGTERM 杀进程组 ④ kickoff 与 /go 逐字节同 ⑤ 信封 Boundary
# 由声明渲染 ⑥ session Events 拉起事件 ⑥b land 事件带 host, 跨机退回手工); TS 侧 f86/f87/f93 改为 go.ts 只原样发送 next_card.kickoff
echo
echo "── tests/test_aipos_f95_harness_launch.py (F95 loop 授权拉起 harness·进程组生命周期·开工提示单源·信封 Boundary·工位 host) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f95_harness_launch.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f95_harness_launch.py PASS"
else
  echo "✗ tests/test_aipos_f95_harness_launch.py FAIL"
  overall=1
fi
# AIPOS-F95: 假 harness 辅助脚本(被上节夹具按 launch 模板拉起)须可编译
echo
echo "── tests/fake_harness.py (F95 假 harness 辅助脚本可编译) ────────────────────────────────────────────────────"
if python3 -m py_compile "$REPO_ROOT/tests/fake_harness.py"; then
  echo "✓ tests/fake_harness.py PASS"
else
  echo "✗ tests/fake_harness.py FAIL"
  overall=1
fi

# AIPOS-F97: 交回检查「测试文件」判据单源且读声明(① workspace_config.card_test_files 唯一判据, TEST_NOT_IN_RUNALL / NO_TESTS 共用,
# 改动集带状态 git diff --name-status, 删除不计·重命名取新路径, 车道检查缺省形不变 ② config.schema test_contract.test_file_globs
# 缺省在 schema、项目可覆盖 ③ 靶场: 删除/名含 test 的文档/辅助脚本不算, 新增/修改未登记仍拒, 只删不加 NO_TESTS, 覆盖生效, 缺省覆盖本仓现有测试)
echo
echo "── tests/test_aipos_f97_test_file_criterion.py (F97 测试文件判据单源·删除不计·式样读声明·缺省覆盖本仓实测) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f97_test_file_criterion.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f97_test_file_criterion.py PASS"
else
  echo "✗ tests/test_aipos_f97_test_file_criterion.py FAIL"
  overall=1
fi

# AIPOS-F96: 文档与设计稿清理(碎片化 N3/N4/N5)件④——「产品文档不教退役做法」只减不增棘轮
# (扫描 docs/、README.md、QUICKSTART.md、agents/**/*.md、templates/**; 式样声明在夹具常量, 基线 tests/f96_docs_retired_practice_baseline.json, docs/ 零容忍)
echo
echo "── tests/test_aipos_f96_docs_retired_practice_ratchet.py (F96 文档退役做法棘轮·新增命中红/基线残留红·docs/ 零容忍·式样不误报现行做法) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f96_docs_retired_practice_ratchet.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f96_docs_retired_practice_ratchet.py PASS"
else
  echo "✗ tests/test_aipos_f96_docs_retired_practice_ratchet.py FAIL"
  overall=1
fi

# AIPOS-F98: 零依赖兜底解析器(无 PyYAML 时 frontmatter 唯一读取口的兜底路径)覆盖产品写出的全部形状且与 yaml.safe_load 逐形状相同
# (两层 lane / seq-of-maps rework_rounds / 空 []{} / 引号与多行引号标量 / YAML 1.1 标量解析), 不支持结构 fail-closed(键路径+行号, 不再静默置 None);
# 整文件登记 tools/aipos_cli/tests/test_frontmatter_zerodep.py(此前从未入 run-all) + 随语义改动的 F87 坏卡靶场用例(lane.repo 指向靶场已声明仓)
echo
echo "── tools/aipos_cli/tests/test_frontmatter_zerodep.py (F98 屏蔽 yaml 逐形状 = safe_load·发布卡两层 lane 不丢·safe_dump/stdlib 写出物回读·拒绝原文带键路径与行号·YAML 错误只丢坏键且有无 PyYAML 同果·读取口告警被 task_loader/lint 接住) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tools/aipos_cli/tests/test_frontmatter_zerodep.py" \
    "$REPO_ROOT/tests/test_aipos_f87_card_yaml_root_fix.py::test_item3_none_selectable_gives_reasons_and_go_relays_verbatim" \
    -v --tb=short; then
  echo "✓ tools/aipos_cli/tests/test_frontmatter_zerodep.py PASS"
else
  echo "✗ tools/aipos_cli/tests/test_frontmatter_zerodep.py FAIL"
  overall=1
fi

# AIPOS-F99: 审计章程「独立全量基线」(① 母本硬规矩: 被审 tip 与 main 各独立跑项目声明测试清单、同一 grep 式样计数附 diff、
# 新增失败=FAIL、worktree --detach 建 main 副本、计数异常先串行重跑、未声明跳过并注明 ② charter_render 唯一键表新增 runall_path,
# 读 workspace_config.project_test_contract, 未声明=明确文字、形坏 fail-closed ③ 审计技能引用章程 ④ 靶场 lybra 实值/未声明两种渲染无残留占位)
echo
echo "── tests/test_aipos_f99_audit_runall_baseline.py (F99 审计独立全量基线·清单位置读声明渲染·未声明明确文字·形坏拒渲染) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f99_audit_runall_baseline.py" -v --tb=short; then
  echo "✓ tests/test_aipos_f99_audit_runall_baseline.py PASS"
else
  echo "✗ tests/test_aipos_f99_audit_runall_baseline.py FAIL"
  overall=1
fi

# AIPOS-F108: 卡字段与分支声明单源(族 C-d: M9/M18/H6)——① card.schema 声明落盘键(frontmatter_order 投影字段序 / fields.default 投影缺省值,
# 修复卡去写死实例名缺省) ② 分支名 / 基线读 transitions N5.branch_integration(branch_pattern / base_branch), 产品代码零写死, 改声明靶场跟随
# ③ 草稿 project 缺省读治理根 project.json#project, 缺则拒; 随改动登记 finalize 分支整合 / 自动切回 / 卡号归属解析夹具
echo
echo "── tests/test_aipos_f108_card_field_branch_single_source.py (F108 落盘键全声明·字段序缺省值读 schema·分支名基线零写死且改声明跟随·草稿项目读 project.json 缺则拒) ────────────────────────────────────────────────────"
if PYTHONPATH="$REPO_ROOT" python3 -m pytest "$REPO_ROOT/tests/test_aipos_f108_card_field_branch_single_source.py" \
    "$REPO_ROOT/tests/test_finalize_branch_integration.py" \
    "$REPO_ROOT/tools/aipos_cli/tests/test_finalize_branch_auto_checkout.py" \
    "$REPO_ROOT/tests/test_aipos_f5_task_id_pattern.py" \
    -v --tb=short; then
  echo "✓ tests/test_aipos_f108_card_field_branch_single_source.py PASS"
else
  echo "✗ tests/test_aipos_f108_card_field_branch_single_source.py FAIL"
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
