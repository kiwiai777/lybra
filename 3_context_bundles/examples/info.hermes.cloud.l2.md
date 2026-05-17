role_instance: info.hermes.cloud
agent_instance: info.hermes.cloud.l2
environment: remote_tencent_cloud

description: daily intelligence synthesis

allowed_task_modes:
  - intelligence_synthesizer
  - report_drafter

preferred_model_tiers:
  - L2

allowed_model_tiers:
  - L1
  - L2
  - L3

memory_access:
  - 4_inbox/cloud.hermes.info/

output_target:
  - draft_reports

escalation_rules:
  - escalate_to L3 if conflict
