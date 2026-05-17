role_instance: info.hermes.cloud
agent_instance: info.hermes.cloud.l1
environment: remote_tencent_cloud

description: low-cost intelligence collection

allowed_task_modes:
  - intelligence_collector
  - summarizer
  - classifier

preferred_model_tiers:
  - L1

allowed_model_tiers:
  - L1

memory_access:
  - external_sources

output_target:
  - 4_inbox/cloud.hermes.info/

escalation_rules:
  - if entity match missing → escalate_to L2

constraints:
  - no formal memory write
