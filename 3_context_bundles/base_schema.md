# Context Bundle Schema v0.1

## Required Fields

- role_instance
- environment
- description
- allowed_task_modes
- preferred_model_tiers
- allowed_model_tiers
- memory_access
- output_target
- escalation_rules

## Optional Fields

- agent_instance
- role_template_id
- role_template_ref
- role_template_version
- category
- model_tier_range
- llm_provider_preference
- sandbox_runtime_compatibility
- host_category
- capabilities_required
- capabilities_recommended
- read_scopes
- write_scopes
- review_requirements
- audit_requirements
- owner_gate_triggers
- forbidden_modes
- separation_rules
- supersession
- schedule_hint
- input_sources
- constraints

## Rules

- task_mode is set by task card
- model tier is selected per task
- bundle does not lock role
- bundle defines boundaries, not decisions
- role template fields are declarative catalog metadata only
- role template fields do not grant runtime, claim, write, audit, or Owner authority
- concrete agent instances remain in the agent capability registry, not in role templates
- role template names must be functional and vendor-neutral
- template evolution uses explicit supersession rather than silent replacement
