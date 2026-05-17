role_instance: biz.openclaw.cloud
agent_instance: biz.openclaw.cloud.l1
environment: remote_tencent_cloud

description: standard operational processing

allowed_task_modes:
  - lead_processor
  - content_drafter

preferred_model_tiers:
  - L1

allowed_model_tiers:
  - L1
  - L2

memory_access:
  - external_sources

output_target:
  - 4_inbox/cloud.biz/

escalation_rules:
  - if customer objection → escalate_to L2

constraints:
  - no external sending
