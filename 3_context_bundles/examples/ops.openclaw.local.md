role_instance: ops.openclaw.local
environment: local_wsl_ubuntu
description: local operations agent

allowed_task_modes:
  - system_operator
  - configuration_operator
  - documentation_syncer
  - content_operator

preferred_model_tiers:
  - L1
  - L2

allowed_model_tiers:
  - L1
  - L2
  - L3

memory_access:
  - 0_control_plane/
  - 3_context_bundles/

output_target:
  - repository
  - task_cards/

escalation_rules:
  - if governance risk → use L3

constraints:
  - no direct push
