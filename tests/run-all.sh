#!/usr/bin/env bash
# run-all —— 产品仓常驻夹具总入口(门交回检查读的位置声明 = lybra 治理根 project.json test_contract.runall_path, 产品代码零写死)。
# AIPOS-F109 件④(gap #40): 自动发现——按项目 test_contract.test_file_globs(与门「本卡测试文件」判据同一判定
#   workspace_config.is_test_file)发现产品仓内全部测试文件并逐个执行(执行器 tools/aipos_cli/runall_discovery.py)。
#   新测试文件零登记: 并行的卡不再往本文件追加登记块 → 合并零冲突; 原逐卡登记块与 F111 一行式 run_pytest 约定退役。
# 本文件末尾 `# lybra-runall:` 声明行(声明 config.schema test_contract.runall_directives; 唯一解析
#   workspace_config.runall_directives, 门交回检查 TEST_NOT_IN_RUNALL 共用):
#   discover                     本清单自动发现
#   exclude <目标> <理由>         不执行(执行器逐条打印, 不静默); 文件级排除的测试文件 = 门视为未登记
#   known-failure <目标> [理由]   执行, 已知存量失败被容忍; 转绿 / 节点不存在 = 红, 须删该行
#   目标 = 相对产品仓根的文件路径, 或 pytest 节点 <文件>::<节点>。存量失败(gap #59)只减不增: 这两类行只许删, 新增行审计必查。
# 夹具隔离: 执行器给每个测试子进程临时 HOME(PYTHONUSERBASE 钉回原用户 site)并清 LYBRA_*/AIPOS_* 环境变量,
#   测试不得借真实 ~/.lybra 解析到真实治理根/工位/门凭据。
# AIPOS-F116 防护夹具(执行器内建, 每文件前后各查一次, 变/漏则该文件判红): 真实治理根守卫(home 根下各项目 governance/ 与 5_tasks/
#   git status + 关键日志 md5, 只读) + 孤儿进程守卫(测试结束后仍存活的测试后代进程 SIGKILL); 各测试目录 conftest.py 会话层同款 HOME 隔离。
# 依赖: Python 3 + pytest; Node ≥ 22(TS 夹具类型剥离, 纯 node 内置模块, 无需 npm install)。
# 输出: 每文件「── <文件> ──」段 + `✓ <文件> PASS` / `✗ <文件> FAIL` 行(与原清单同式样, 基线 grep 不变)。
set -u
# 本脚本在 <产品仓根>/tests/ 下: 先 cd 到产品仓根, 之后一切相对路径与 REPO_ROOT 都以此为准(与调用方 cwd、相对/绝对调用方式无关; 承 F66B)。
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
echo "========================================================"
echo " Lybra 产品仓 run-all 常驻夹具(自动发现)"
echo "========================================================"
overall=0

# AIPOS-F95: 假 harness 辅助脚本(被 F95 夹具按 launch 模板拉起; 非测试文件, 不在发现集)须可编译
echo
echo "── tests/fake_harness.py (F95 假 harness 辅助脚本可编译) ────────────────────────────────────────────────────"
if python3 -m py_compile "$REPO_ROOT/tests/fake_harness.py"; then
  echo "✓ tests/fake_harness.py PASS"
else
  echo "✗ tests/fake_harness.py FAIL"
  overall=1
fi

if ! PYTHONPATH="$REPO_ROOT" python3 -m tools.aipos_cli.runall_discovery --runall tests/run-all.sh; then
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
exit "$overall"

# ---- 声明行(只减不增; 一行一条, 按目标排序, 便于并行卡各删不同行时 git 行级合并) ----
# lybra-runall: discover
# lybra-runall: exclude tests/playwright/board.visual.spec.js 需 Playwright 浏览器(npm run test:visual), 非常驻夹具环境
# lybra-runall: exclude tools/test_aipos_r2_enroll.py 写死真实治理根+生产门 7118 发码/换证, 每跑一次往真实 enrollment_log 写 test.enroll.aipos-r2 行(gap #34/#70 污染源); 车道外(tools/)待改临时靶场或退役(AIPOS-F116 由 known-failure 转此)
# ---- 已知存量失败(gap #59; AIPOS-F109 盘点: main bf7623c 隔离 HOME 下同败; 只许删——转绿即红, 删行) ----
# lybra-runall: known-failure task_cards/AIPOS-276/test_aipos276.py::test_s2_stale_map_publish_warn
# lybra-runall: known-failure task_cards/AIPOS-276/test_fix1.py::test_f276_1_warnings_in_publish_record
# lybra-runall: known-failure tests/i18n-bilingual-stability.test.js
# lybra-runall: known-failure tests/test_aipos_316_guards.py::test_internal_modules_reject_direct_invocation
# lybra-runall: known-failure tests/test_aipos_316_guards.py::test_rotate_blocks_on_lost_bindings
# lybra-runall: known-failure tests/test_aipos_343_contract_section_no_silent_swallow.py::TestContractSectionErrorPropagation::test_no_policies_causes_block_not_silent_omission
# lybra-runall: known-failure tests/test_aipos_343_contract_section_no_silent_swallow.py::TestLiveAgencyWorkspace::test_agency_contract_section_renders_with_pol_agency_1
# lybra-runall: known-failure tests/test_aipos_343_contract_section_no_silent_swallow.py::TestLybraRegression::test_lybra_publish_still_appends_section
# lybra-runall: known-failure tests/test_aipos_343_contract_section_no_silent_swallow.py::TestSelectorEmptySemantics::test_empty_task_mode_matches_any_task_type
# lybra-runall: known-failure tests/test_aipos_f44d_a_role_resolution_redgreen.py::test_red_hardcoded_executor
# lybra-runall: known-failure tests/test_finalize.py::test_check_deployment_integrity_drift
# lybra-runall: known-failure tests/test_finalize.py::test_check_task_can_finalize_fail_verdict
# lybra-runall: known-failure tests/test_finalize.py::test_check_task_can_finalize_no_verdict_files
# lybra-runall: known-failure tests/test_finalize.py::test_check_task_can_finalize_no_verdicts_dir
# lybra-runall: known-failure tests/test_finalize.py::test_check_task_can_finalize_rejects_handwritten_markdown
# lybra-runall: known-failure tests/test_finalize.py::test_finalize_task_clean_working_tree
# lybra-runall: known-failure tests/test_finalize.py::test_finalize_task_commits_changes
# lybra-runall: known-failure tests/test_finalize.py::test_finalize_task_deployment_integrity_fail
# lybra-runall: known-failure tests/test_finalize.py::test_finalize_task_dry_run
# lybra-runall: known-failure tests/test_finalize.py::test_finalize_task_no_gate_verdict_blocked
# lybra-runall: known-failure tests/test_finalize_integration.py::test_finalize_no_changes_to_commit
# lybra-runall: known-failure tests/test_finalize_integration.py::test_finalize_workflow_dry_run
# lybra-runall: known-failure tests/test_finalize_integration.py::test_finalize_workflow_pass_task
# lybra-runall: known-failure tools/aipos_cli/tests/test_agent_watch_fs.py::FsWatchRedLineTests::test_module_is_stdlib_only_zero_new_deps
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_cli_draft_and_confirm
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_cli_live_draft_and_confirm
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_live_adapter_preview_and_confirm_writes_standard_draft_and_provenance
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_manual_retry_relationship_is_recorded
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_preview_writes_nothing_then_confirm_writes_standard_draft_and_provenance
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_provenance_is_non_secret_sidecar_without_raw_prompt_or_response
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_stale_preview_blocks_confirm
# lybra-runall: known-failure tools/aipos_cli/tests/test_ai_assisted_authoring.py::AiAssistedAuthoringTests::test_wrong_owner_token_blocks_confirm
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos338_gate_contract_section.py::TestPublishAppendsSection::test_new_publish_includes_contract_section
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos338_gate_contract_section.py::TestRegistryRenameAutoFollows::test_rename_flows_through_resolver
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos348.py::Aipos348AuditGateTests::test_complete_allowed_when_audit_required_with_pass_verdict
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos348.py::Aipos348CLIIntegrationTests::test_cli_complete_allowed_with_pass_verdict
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos348.py::Aipos348CLIIntegrationTests::test_cli_complete_blocked_by_audit_gate
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos348.py::Aipos348ReopenCompletedTests::test_reopen_blocked_still_works
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos348.py::Aipos348ReopenCompletedTests::test_reopen_completed_to_pending_writes_correct_fields
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos352_custom_roles.py::TestBuiltinRolesZeroRegression::test_builtin_scopes_unchanged
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos352_custom_roles.py::TestBuiltinRolesZeroRegression::test_role_specs_unchanged
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos_fnd7f1_fail_rereview.py::TestFailRereviewAllowed::test_fail_verdict_allows_redispatch_and_pass
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos_fnd7f1_fail_rereview.py::TestPassVerdictTerminal::test_pass_verdict_blocks_overturn
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos_fnd7f1_fail_rereview.py::TestRequestChangesRereviewAllowed::test_request_changes_allows_redispatch
# lybra-runall: known-failure tools/aipos_cli/tests/test_aipos_fnd7f3_holistic_rereview.py::TestRereviewCompleteEndToEnd::test_fail_then_pass_then_complete
# lybra-runall: known-failure tools/aipos_cli/tests/test_authority_scanner.py::AuthorityScannerTests::test_fs_injected_pending_orphan_is_quarantined_without_dosing_valid_task
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::BoardAdapterTests::test_adapter_module_does_not_require_subprocess
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::BoardAdapterTests::test_queue_claim_dry_run_returns_envelope
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::GovernanceResolutionTests::test_explicit_valid_workspace_used_directly_not_ancestor
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::GovernanceResolutionTests::test_home_layout
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::GovernanceResolutionTests::test_home_layout_positive_truth_identity_and_content
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::GovernanceResolutionTests::test_legacy_2projects_no_longer_resolved
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::GovernanceResolutionTests::test_neither_layout_fails_loud_governance_not_found
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::GovernanceResolutionTests::test_real_ambiguity_fails_closed
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter.py::GovernanceResolutionTests::test_stray_2projects_ignored_home_used
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter_execute_integration.py::BoardAdapterExecuteIntegrationTests::test_draft_publish_flow_and_failures
# lybra-runall: known-failure tools/aipos_cli/tests/test_board_adapter_execute_integration.py::BoardAdapterExecuteIntegrationTests::test_return_derive_backref_body_survives
# lybra-runall: known-failure tools/aipos_cli/tests/test_confirm_client.py::ConfirmClientTests::test_claim_args_from_task_drive_a_real_confirm
# lybra-runall: known-failure tools/aipos_cli/tests/test_confirm_client.py::ConfirmClientTests::test_confirm_auto_replays_dry_run_identity_args
# lybra-runall: known-failure tools/aipos_cli/tests/test_confirm_client.py::ConfirmClientTests::test_confirm_blocks_when_replay_arg_omitted
# lybra-runall: known-failure tools/aipos_cli/tests/test_confirm_client.py::ConfirmClientTests::test_lists_confirm_gates_via_read_tool
# lybra-runall: known-failure tools/aipos_cli/tests/test_confirm_client.py::ConfirmClientTests::test_owner_preview_and_confirm_records_confirmer_on_disk
# lybra-runall: known-failure tools/aipos_cli/tests/test_confirm_client.py::ConfirmClientTests::test_refresh_issues_a_new_token
# lybra-runall: known-failure tools/aipos_cli/tests/test_confirm_client.py::ConfirmClientTests::test_sse_response_single_event_parsed
# lybra-runall: known-failure tools/aipos_cli/tests/test_controlled_execute.py::ControlledExecuteTests::test_execute_draft_publish_writes_pending_and_publish_record_and_keeps_source
# lybra-runall: known-failure tools/aipos_cli/tests/test_derived_audit_verdict.py::DerivedAuditVerdictTests::test_derived_verdict_confirm_writes_record_and_attributes
# lybra-runall: known-failure tools/aipos_cli/tests/test_derived_audit_verdict.py::DerivedAuditVerdictTests::test_derived_verdict_pass_accepts_publish_provenance
# lybra-runall: known-failure tools/aipos_cli/tests/test_derived_audit_verdict.py::DerivedAuditVerdictTests::test_dispatched_verdict_zero_regression
# lybra-runall: known-failure tools/aipos_cli/tests/test_draft_writer.py::DraftWriterTests::test_publish_dry_run_writes_nothing
# lybra-runall: known-failure tools/aipos_cli/tests/test_draft_writer.py::DraftWriterTests::test_publish_external_intake_draft_converts_to_execution_handoff
# lybra-runall: known-failure tools/aipos_cli/tests/test_draft_writer.py::DraftWriterTests::test_publish_json_output_is_valid_for_dry_run_and_write
# lybra-runall: known-failure tools/aipos_cli/tests/test_draft_writer.py::DraftWriterTests::test_publish_writes_pending_file_and_publish_record_under_temp_repo
# lybra-runall: known-failure tools/aipos_cli/tests/test_external_intake_writer.py::ExternalIntakeWriterTests::test_intake_submit_dry_run_and_confirm_writes_external_intake_draft_only
# lybra-runall: known-failure tools/aipos_cli/tests/test_external_intake_writer.py::ExternalIntakeWriterTests::test_list_drafts_includes_nested_external_intake_draft
# lybra-runall: known-failure tools/aipos_cli/tests/test_f23_enroll.py::TestIssueSelfContainedCode::test_issue_end_to_end
# lybra-runall: known-failure tools/aipos_cli/tests/test_f23_enroll.py::TestRoleFileMergeAndGuards::test_enroll_auditor_refused_in_governance_workspace
# lybra-runall: known-failure tools/aipos_cli/tests/test_f23_enroll.py::TestRoleFileMergeAndGuards::test_enroll_refuses_governance_target
# lybra-runall: known-failure tools/aipos_cli/tests/test_f24a_enroll_thin_shell.py::TestGovernanceRootRegistry::test_default_falls_back_to_gate_root
# lybra-runall: known-failure tools/aipos_cli/tests/test_finalize_auto_deploy.py::test_finalize_auto_deploy_gate_side_changes
# lybra-runall: known-failure tools/aipos_cli/tests/test_finalize_auto_deploy.py::test_finalize_deploy_failure_does_not_block_finalize
# lybra-runall: known-failure tools/aipos_cli/tests/test_finalize_auto_deploy.py::test_finalize_no_drift_no_deploy
# lybra-runall: known-failure tools/aipos_cli/tests/test_finalize_auto_deploy.py::test_finalize_skip_deploy_cli_side_only
# lybra-runall: known-failure tools/aipos_cli/tests/test_fnd1_cli_commands.py::TestNewCLICommands::test_bench_audit_parsing
# lybra-runall: known-failure tools/aipos_cli/tests/test_fnd1_cli_commands.py::TestNewCLICommands::test_converge_parsing
# lybra-runall: known-failure tools/aipos_cli/tests/test_fnd1_cli_commands.py::TestNewCLICommands::test_mark_concluded_parsing
# lybra-runall: known-failure tools/aipos_cli/tests/test_fnd1_cli_commands.py::TestNewCLICommands::test_owner_verify_parsing
# lybra-runall: known-failure tools/aipos_cli/tests/test_fnd1_cli_commands.py::TestNewCLICommands::test_task_progress_parsing
# lybra-runall: known-failure tools/aipos_cli/tests/test_planner_loop_mvp.py::PlannerLoopMvpTests::test_loop_preview_recommends_controlled_publish_without_writing
# lybra-runall: known-failure tools/aipos_cli/tests/test_queue_mutation.py::QueueMutationTests::test_block_claimed_to_blocked_writes_required_fields
# lybra-runall: known-failure tools/aipos_cli/tests/test_queue_mutation.py::QueueMutationTests::test_claim_pending_to_claimed_writes_runtime_fields
# lybra-runall: known-failure tools/aipos_cli/tests/test_queue_mutation.py::QueueMutationTests::test_complete_claimed_to_completed_writes_required_fields
# lybra-runall: known-failure tools/aipos_cli/tests/test_queue_mutation.py::QueueMutationTests::test_complete_clears_stale_owner_attention_flags
# lybra-runall: known-failure tools/aipos_cli/tests/test_queue_mutation.py::QueueMutationTests::test_json_output_is_valid_for_all_mutations
# lybra-runall: known-failure tools/aipos_cli/tests/test_queue_mutation.py::QueueMutationTests::test_reopen_blocked_to_pending_writes_required_fields
# lybra-runall: known-failure tools/aipos_cli/tests/test_record_writer.py::RecordWriterTests::test_block_with_records_updates_existing_session_record
# lybra-runall: known-failure tools/aipos_cli/tests/test_record_writer.py::RecordWriterTests::test_claim_with_records_creates_claim_log_and_session_record
# lybra-runall: known-failure tools/aipos_cli/tests/test_record_writer.py::RecordWriterTests::test_complete_with_records_updates_existing_session_record
# lybra-runall: known-failure tools/aipos_cli/tests/test_record_writer.py::RecordWriterTests::test_non_dry_run_json_shape_is_record_reader_compatible
# lybra-runall: known-failure tools/aipos_cli/tests/test_record_writer.py::RecordWriterTests::test_reopen_with_records_updates_session_when_reference_exists
# lybra-runall: known-failure tools/aipos_cli/tests/test_service_mode.py::ConnectionLocationTests::test_scopes_unchanged_after_location_move
# lybra-runall: known-failure tools/aipos_cli/tests/test_service_mode.py::SelectiveRotationTests::test_selective_rotate_on_fresh_workspace
# lybra-runall: known-failure tools/aipos_cli/tests/test_service_mode.py::ServiceModeTests::test_serve_stop_kills_without_home_root_or_project
# lybra-runall: known-failure tools/aipos_cli/tests/test_task_complexity.py::TaskComplexityTests::test_complex_dependent_audit_task_can_publish_when_audit_ready
# lybra-runall: known-failure tools/aipos_cli/tests/test_task_complexity.py::TaskComplexityTests::test_complex_dependent_draft_can_exist_but_cannot_publish_before_audit_pass
# lybra-runall: known-failure tools/aipos_cli/tests/test_token_rotation.py::TestRotateExecute::test_full_rotation_backup_record_no_plaintext
# lybra-runall: known-failure tools/aipos_cli/tests/test_writer_flat_contract.py::WriterFlatContractTests::test_publish_record_is_flat
# lybra-runall: known-failure tools/lybra_tui/tests/test_ai_authoring.py::AiAuthoringTests::test_card_conformant_and_publishable
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_chat_auto_compacts_l3_but_l0_truth_byte_identical
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_chat_records_user_and_assistant_turns_non_truth
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_chat_returns_nl_answer_and_writes_no_file
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_chat_session_stays_copilot_role_scopes_empty
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_chat_surfaces_read_only_usage_telemetry_and_writes_nothing
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_chat_usage_is_none_when_provider_omits_it
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_chat_uses_read_only_truth_rehydrate_egress
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_copilot_credential_confirm_and_publish_scope_denied
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_copilot_scopes_empty_read_works_writes_denied
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_copilot_token_fingerprint_only
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_draft_rereads_truth_and_sends_it_egress
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_draft_returns_data_and_writes_nothing
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_owner_land_draft_rejects_non_drafts_path
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_owner_land_draft_writes_under_drafts
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_persisted_chat_is_non_truth
# lybra-runall: known-failure tools/lybra_tui/tests/test_copilot.py::CopilotTests::test_toggle_mode_three_cycle
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_connect_status_line_no_token_leak
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_empty_literal_is_rejected_not_submitted
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_executor_confirm_is_scope_denied
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_executor_session_has_no_owner_confirm
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_observe_queue_via_read_tool
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_owner_confirm_claim_records_confirmer
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_owner_confirm_publish_records_confirmer
# lybra-runall: known-failure tools/lybra_tui/tests/test_tui_state.py::TuiStateTests::test_toggle_mode_cycles_observe_confirm_copilot
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos204_gated_publish.py::Aipos204GatedPublishTests::test_owner_can_still_publish_with_owner_confirm
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos204_gated_publish.py::Aipos204GatedPublishTests::test_publish_dry_run_is_zero_write_and_no_owner_gate
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos204_gated_publish.py::Aipos204GatedPublishTests::test_published_pending_task_l3_valid
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos204_gated_publish.py::Aipos204GatedPublishTests::test_publisher_only_token_can_self_confirm_publish
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos294_multiproject_routing.py::UnifiedRegistryLoadingTests::test_explicit_projects_respected_not_overwritten
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos294_multiproject_routing.py::UnifiedRegistryLoadingTests::test_home_level_without_projects_defaults_to_wildcard
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos294_multiproject_routing.py::UnifiedRegistryLoadingTests::test_missing_projects_defaults_to_source_project
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos294_multiproject_routing.py::UnifiedRegistryLoadingTests::test_token_collision_warns_keeps_first
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos294_multiproject_routing.py::UnifiedRegistryLoadingTests::test_token_values_never_modified
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos294_multiproject_routing.py::UnifiedRegistryLoadingTests::test_unified_load_combines_home_and_projects
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_aipos201_claim_confirm_records_confirmer_through_streamable
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_aipos201_scope_denied_through_streamable_handshake
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_confirm_without_dry_run_token_returns_structured_error
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_intake_submit_dry_run_over_http_writes_nothing
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_service_mode_expired_role_token_is_rejected_at_transport_boundary
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_service_mode_scope_basis_is_server_side_and_redacted
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_service_mode_single_endpoint_uses_bearer_role_scope_registry
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::ContentNegotiationTests::test_service_mode_wrong_role_tool_calls_are_denied
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_aipos201_claim_confirm_records_confirmer_through_streamable
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_aipos201_scope_denied_through_streamable_handshake
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_confirm_without_dry_run_token_returns_structured_error
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_intake_submit_dry_run_over_http_writes_nothing
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_service_mode_expired_role_token_is_rejected_at_transport_boundary
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_service_mode_scope_basis_is_server_side_and_redacted
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_service_mode_single_endpoint_uses_bearer_role_scope_registry
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296b_content_negotiation.py::HttpSseTransportTests::test_service_mode_wrong_role_tool_calls_are_denied
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_aipos201_claim_confirm_records_confirmer_through_streamable
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_aipos201_scope_denied_through_streamable_handshake
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_confirm_without_dry_run_token_returns_structured_error
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_intake_submit_dry_run_over_http_writes_nothing
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_service_mode_expired_role_token_is_rejected_at_transport_boundary
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_service_mode_scope_basis_is_server_side_and_redacted
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_service_mode_single_endpoint_uses_bearer_role_scope_registry
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::ChunkedSseTests::test_service_mode_wrong_role_tool_calls_are_denied
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_aipos201_claim_confirm_records_confirmer_through_streamable
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_aipos201_scope_denied_through_streamable_handshake
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_confirm_without_dry_run_token_returns_structured_error
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_intake_submit_dry_run_over_http_writes_nothing
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_service_mode_expired_role_token_is_rejected_at_transport_boundary
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_service_mode_scope_basis_is_server_side_and_redacted
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_service_mode_single_endpoint_uses_bearer_role_scope_registry
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos296c_chunked_sse.py::HttpSseTransportTests::test_service_mode_wrong_role_tool_calls_are_denied
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos330_verb_contract.py::TestScopeRoleMap::test_scope_role_map_populated
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos330_verb_contract.py::TestScopeRoleMap::test_who_holds_scope
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::BackwardCompatTests::test_old_token_with_role_gets_current_scopes
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::GetRoleScopesTests::test_custom_role_resolves_via_class
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::GetRoleScopesTests::test_known_roles
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::LiveRoleSpecsTests::test_role_scope_removal_immediate
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::ScopeBasisEchoTests::test_scope_basis_no_minted_when_identical
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::ScopeBasisEchoTests::test_scope_basis_shows_resolved_scopes
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::ToolsListVisibilityTests::test_executor_sees_current_tools
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::ToolsListVisibilityTests::test_planner_sees_draft_tools
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::ZeroWideningTests::test_auditor_scopes_unchanged
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::ZeroWideningTests::test_executor_scopes_unchanged
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos347_scope_at_call_time.py::ZeroWideningTests::test_planner_scopes_unchanged
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos363_envelope_custom_role.py::EnvelopeCustomRoleGateTests::test_custom_role_envelope_auto_releases
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos363_envelope_custom_role.py::EnvelopeCustomRoleGateTests::test_different_role_not_covered_falls_back
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos363_envelope_custom_role.py::MatchClaimEnvelopeCustomRoleTests::test_builtin_role_name_matches
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos363_envelope_custom_role.py::MatchClaimEnvelopeCustomRoleTests::test_custom_role_matches_when_instance_differs
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos363_envelope_custom_role.py::MatchClaimEnvelopeCustomRoleTests::test_instance_match_still_works_without_role
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos363_envelope_custom_role.py::MatchClaimEnvelopeCustomRoleTests::test_role_class_is_not_auto_matched
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos363_envelope_custom_role.py::MatchClaimEnvelopeCustomRoleTests::test_uncovered_role_still_falls_back
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos366_claim_before_body.py::ClaimBeforeBodyTests::test_include_body_allowed_with_claim
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos366_claim_before_body.py::ClaimBeforeBodyTests::test_include_body_denied_for_different_actor
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos366_claim_before_body.py::ClaimBeforeBodyTests::test_include_body_denied_without_claim
# lybra-runall: known-failure tools/mcp_server/tests/test_aipos366_claim_before_body.py::ClaimBeforeBodyTests::test_metadata_allowed_without_claim
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_250b_identity_mismatch_falls_back_supervised
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_250b_no_binding_falls_back_supervised
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_250b_token_bound_identity_match_auto_releases
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_254_auditor_bound_identity_match_auto_releases
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_254_auditor_identity_mismatch_falls_back
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_254_auditor_no_binding_falls_back
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_executor_cannot_arm_policy_scope_denied
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_expired_policy_falls_back
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_forbidden_fields_still_unsupported
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_forged_policy_ref_falls_back_and_no_self_confirm
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_in_envelope_claim_auto_releases_and_records_preauthorized
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_max_tasks_count_bound
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_model_token_land_in_claim_record
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_out_of_envelope_falls_back_to_supervised
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_policy_grant_requires_owner_confirm_token
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_return_rejects_preauthorized_mode
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeGateTests::test_revoked_policy_falls_back
# lybra-runall: known-failure tools/mcp_server/tests/test_autonomy_preauth_envelope.py::PreAuthEnvelopeRealRotateEndToEndTests::test_real_rotate_owner_arms_envelope_executor_auto_releases
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_aipos201_claim_confirm_records_confirmer_through_streamable
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_aipos201_scope_denied_through_streamable_handshake
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_confirm_without_dry_run_token_returns_structured_error
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_intake_submit_dry_run_over_http_writes_nothing
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_service_mode_expired_role_token_is_rejected_at_transport_boundary
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_service_mode_scope_basis_is_server_side_and_redacted
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_service_mode_single_endpoint_uses_bearer_role_scope_registry
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::HttpSseTransportTests::test_service_mode_wrong_role_tool_calls_are_denied
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::ServiceRoleRegistryProjectsTests::test_registry_carries_projects_when_minted
# lybra-runall: known-failure tools/mcp_server/tests/test_http_sse_transport.py::ServiceRoleRegistryProjectsTests::test_registry_without_projects_stays_byte_identical
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos197_claim_confirm_denied_without_owner_confirm_scope
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos197_return_confirm_records_confirmer_attribution
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos199_claim_confirm_records_confirmer_attribution
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos253_return_confirm_derives_audit_task_with_publish_record
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos253_return_confirm_idempotency_no_duplicate_derivation
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos253_return_confirm_respects_audit_none_opt_out
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos261_return_records_optional_agent_runtime_bundle
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos261_return_without_agent_runtime_is_zero_break
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos265f1_audit_verdict_persists_agent_runtime_to_record
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos320_return_body_absent_zero_regression
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos320_return_body_written_to_return_md_on_confirm
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos320_return_content_auditor_scope_works
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos320_return_content_not_found_errors
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos320_return_content_path_escape_blocked
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos320_return_content_reads_body
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos320_return_content_requires_queue_claim_scope
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos328_return_confirm_allowed_with_queue_return_scope_only
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos_f34_impostor_return_still_blocked
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_aipos_f34_session_drift_no_longer_blocks_return
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_dispatch_blocks_registry_unverified_executor_real_path
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_dispatch_blocks_same_executor_auditor
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_dispatch_confirm_requires_owner_confirmation_then_creates_pending_audit_task
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_dispatch_dry_run_requires_scope_and_supervised_mode
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_dispatch_passes_when_both_registry_verified_real_path
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_task_claim_blocks_same_executor_instance
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_verdict_blocks_same_executor_auditor
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_audit_verdict_confirm_requires_owner_confirmation_then_records_pass_without_finalize
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_confirm_expired_token_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_confirm_missing_token_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_confirm_snapshot_mismatch_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_dry_run_invalid_source_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_intake_submit_dry_run_and_confirm_happy_path
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_owner_decision_confirm_expired_token_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_owner_decision_confirm_missing_token_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_owner_decision_confirm_snapshot_mismatch_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_owner_decision_missing_evidence_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_owner_decision_record_dry_run_and_confirm_happy_path
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_owner_decision_scope_denied_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_claim_blocks_wrong_specific_instance_and_forbidden_fields
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_claim_confirm_rejects_non_mcp_dry_run_token
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_claim_confirm_requires_owner_confirmation_then_moves_claim_only
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_claim_dry_run_is_zero_write_and_owner_confirmed
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_claim_dry_run_requires_scope_and_supervised_mode
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_blocks_invalid_audit_readiness
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_blocks_invalid_executor_status
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_blocks_missing_evidence
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_blocks_wrong_claimant_and_forbidden_fields
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_confirm_blocks_when_claimed_task_changes_after_dry_run
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_confirm_rejects_non_mcp_dry_run_token
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_confirm_requires_owner_confirmation_then_updates_claimed_task_only
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_confirm_reuses_planned_timestamp_for_stable_snapshot
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_dry_run_is_zero_write_and_has_confirmation_preview
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_dry_run_requires_scope_and_supervised_mode
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_dry_run_snapshot_ignores_generated_return_timestamp
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_ingests_scratch_artifact_into_workspace
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_scratch_content_swap_blocks_confirm
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_scratch_parent_escape_blocked
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_scratch_requires_approved_root
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_scratch_requires_owner_confirmation
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_queue_return_scratch_symlink_escape_blocked
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_scope_denied_returns_structured_teaching_error
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_task_preview_include_body_returns_body_markdown
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_tools_list_contains_only_mvp_read_tools
# lybra-runall: known-failure tools/mcp_server/tests/test_mcp_tools.py::McpToolTests::test_tools_list_scope_gates_intake_write_tools
# lybra-runall: known-failure tools/mcp_server/tests/test_planner_role.py::PlannerRoleGateTests::test_draft_submit_path_cannot_escape_drafts
# lybra-runall: known-failure tools/mcp_server/tests/test_planner_role.py::PlannerRoleGateTests::test_planner_can_publish_own_draft
# lybra-runall: known-failure tools/mcp_server/tests/test_planner_role.py::PlannerRoleGateTests::test_planner_can_use_read_tools
# lybra-runall: known-failure tools/mcp_server/tests/test_planner_role.py::PlannerRoleGateTests::test_planner_denied_on_all_gated_writes_zero_records
# lybra-runall: known-failure tools/mcp_server/tests/test_planner_role.py::PlannerRoleGateTests::test_planner_draft_submit_lands_in_drafts_zone
# lybra-runall: known-failure tools/mcp_server/tests/test_planner_role.py::PlannerRoleGateTests::test_r4_drafts_zone_task_is_not_claimable
# lybra-runall: known-failure tools/mcp_server/tests/test_planner_role.py::PlannerRoleGateTests::test_task_preview_surfaces_audit_verdicts
# lybra-runall: known-failure tools/mcp_server/tests/test_project_status_tool.py::ProjectStatusToolTests::test_payload_reports_gate_view_and_writes_nothing
# lybra-runall: known-failure tools/mcp_server/tests/test_project_status_tool.py::ProjectStatusToolTests::test_resolution_failure_is_reported_not_crashed
# lybra-runall: known-failure tools/mcp_server/tests/test_scope_reachability.py::ScopeReachabilityTests::test_amend_and_withdraw_reachable_before_318
# lybra-runall: known-failure tools/mcp_server/tests/test_scope_reachability.py::ScopeReachabilityTests::test_every_scope_reachable_or_exempt
# lybra-runall: known-failure tools/mcp_server/tests/test_scope_reachability.py::ScopeReachabilityTests::test_every_scoped_verb_reachable_by_some_role
# lybra-runall: known-failure tools/mcp_server/tests/test_token_project_enforcement.py::ProjectEnforcementTests::test_all_tool_handlers_are_project_gated_no_exemptions
# lybra-runall: known-failure tools/mcp_server/tests/test_token_projects_gate_inert.py::TokenProjectsGateInertTests::test_flip_case_projects_mismatch_still_allows
# lybra-runall: known-failure tools/mcp_server/tests/test_token_projects_gate_inert.py::TokenProjectsGateInertTests::test_scope_decision_identical_with_and_without_projects
# lybra-runall: known-failure tools/test_aipos_c1_surface_consistency.py
# lybra-runall: known-failure tools/test_schema_loader.py::test_load_schemas
# lybra-runall: known-failure tools/test_schema_unify.py::test_all_enum_refs_resolve
# lybra-runall: known-failure tools/test_schema_unify.py::test_no_residual_enum_literals
# lybra-runall: known-failure tools/test_schema_unify.py::test_resolve_field_enum
# lybra-runall: known-failure web/board/tests/test_ai_authoring_api.py::AiAuthoringApiTests::test_confirm_requires_owner_confirmation_and_matching_actor
# lybra-runall: known-failure web/board/tests/test_ai_authoring_api.py::AiAuthoringApiTests::test_live_preview_then_confirm_writes_standard_draft_and_non_secret_sidecar
# lybra-runall: known-failure web/board/tests/test_ai_authoring_api.py::AiAuthoringApiTests::test_manual_retry_is_explicit_and_persists_retry_relationship
# lybra-runall: known-failure web/board/tests/test_ai_authoring_api.py::AiAuthoringApiTests::test_preview_then_confirm_writes_standard_draft_and_sidecar_only
# lybra-runall: known-failure web/board/tests/test_ai_authoring_api.py::AiAuthoringApiTests::test_stale_and_expired_previews_block_with_zero_writes
# lybra-runall: known-failure web/board/tests/test_aipos288_cjk_source_guard.py::test_no_bare_cjk_in_sources
# lybra-runall: known-failure web/board/tests/test_board_adapter_contract.py::BoardAdapterContractTests::test_aipos261f2_three_state_pills_and_dossier_closed_signal
# lybra-runall: known-failure web/board/tests/test_controlled_execute_api.py::ControlledExecuteApiTests::test_execute_confirm_moves_claim_after_valid_dry_run
# lybra-runall: known-failure web/board/tests/test_controlled_execute_api.py::ControlledExecuteApiTests::test_execute_confirm_publishes_draft_after_valid_dry_run
# lybra-runall: known-failure web/board/tests/test_controlled_execute_api.py::ControlledExecuteApiTests::test_execute_dry_run_draft_publish_returns_token_without_writing
# lybra-runall: known-failure web/board/tests/test_four_area_i18n.py::FourAreaI18nTests::test_four_area_static_headings_have_i18n_ids_and_zh_default
# lybra-runall: known-failure web/board/tests/test_four_area_i18n.py::FourAreaI18nTests::test_record_content_is_not_translated
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_approved_planner_draft_publish_blocks_owner_gate_without_token
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_approved_planner_draft_publish_confirm_uses_existing_controlled_execute
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_approved_planner_draft_publish_dry_run_returns_token_without_writing
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_governance_route_handles_missing_files_as_warn
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_governance_route_reads_lybra_project_docs_without_writing
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_planner_draft_review_returns_publish_readiness_without_writing
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_planner_draft_review_routes_owner_gate_to_needs_owner
# lybra-runall: known-failure web/board/tests/test_local_read_api.py::LocalReadApiTests::test_planner_drafts_review_route_lists_metadata_without_writing
# lybra-runall: known-failure web/board/tests/test_project_map_and_verify_bench.py::ProjectMapContractTests::test_project_map_schema_and_nested_parse
# lybra-runall: known-failure web/board/tests/test_project_map_and_verify_bench.py::ProjectMapContractTests::test_workspace_route_serves_milestone_map_section
