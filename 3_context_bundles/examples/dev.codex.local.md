role_instance: dev.codex.local
environment: local_wsl_ubuntu
description: local engineering agent

allowed_task_modes:
  - coder
  - reviewer
  - auditor
  - refactorer
  - doc_updater

preferred_model_tiers:
  - L2
  - L3

allowed_model_tiers:
  - L1
  - L2
  - L3

memory_access:
  - 2_projects/
  - 1_shared_memory/development/

output_target:
  - repository

escalation_rules:
  - if high risk → use L3
