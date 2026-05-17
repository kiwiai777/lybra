# Heterogeneous Agent Role Catalog Expansion Protocol

## Purpose

AIPOS-97 defines the protocol boundary for Lybra's heterogeneous agent role catalog.

The catalog is product-oriented and vendor-neutral. It describes functional worker role templates: what kind of work a role performs, which capabilities it needs, how it escalates, what it may read or write, and what output shape it produces.

AIPOS-97 is protocol-only. It does not create concrete role template YAML files, modify concrete agent instances, generate profiles automatically, change runtime authority gates, add matching code, add backend routes, add Web UI controls, launch agents, or enable autonomous planner runtime.

## Strategic Source

AIPOS-97 implements the agent role catalog direction recorded in `DL-20260515-06` Decision 8:

```text
Lybra agent profile catalog uses a two-layer architecture.
Template Layer is vendor-neutral and functional.
Instance Layer remains the deployment/project registry.
Coordinator is a fixed Planner protocol contract and does not enter the Worker catalog.
Worker catalog targets 8-12 functional categories, each with 3-5 role templates.
Template names must not include vendor, tool, product, environment, or host names.
Template metadata is declarative and does not override AIPOS-47, AIPOS-48, or AIPOS-50.
Template evolution uses explicit supersession.
```

## Layer Model

### Template Layer

Template Layer is the catalog itself.

Recommended future path:

```text
3_context_bundles/roles/
```

Template files are not created by AIPOS-97. A later task may create template files under this directory after independent audit.

Templates must use generic functional names. They may describe:

- allowed task modes
- preferred and allowed model tier ranges
- write scopes
- escalation rules
- memory access
- output target
- advisory provider preference classes
- sandbox runtime compatibility classes
- host category compatibility
- supersession metadata

Templates must not bind a concrete model, provider, host, credential, runtime process, sandbox instance, or agent session.

### Instance Layer

Instance Layer remains the existing agent capability registry and runtime profile layer.

Instance profiles may bind concrete deployment facts:

- concrete logical agent
- concrete LLM or model family
- runtime profile
- UI host
- execution host
- repo host
- validation host
- git host
- ssh target
- current availability
- concrete capabilities
- current model tier
- write scopes available to that instance

The instance registry may reference a role template, but the template does not grant claim authority by itself.

### Separation Rule

Template Layer and Instance Layer must remain separate.

Rules:

- catalog templates must not be named after concrete instances
- concrete instance names must not be copied into catalog template names
- registry entries must not be treated as reusable product templates
- template compatibility is a matching input, not a permission grant
- runtime eligibility still depends on AIPOS-47 model routing, AIPOS-48 matching, AIPOS-50 session lease binding, review/audit separation, and Owner gates

## Coordinator Contract

Planner is the single coordinator contract.

Planner is not a Worker catalog template.

Coordinator invariants:

- each orchestration has at most one active coordinator
- planner assignment follows `planner_orchestrator_protocol.md`
- planner continuity follows AIPOS-64 where applicable
- planner loop behavior follows AIPOS-53 and AIPOS-54 boundaries
- planner may recommend, draft, route, summarize, and pause for Owner decisions
- planner must not self-execute implementation work under the coordinator role
- planner must not self-audit
- planner must not bypass Owner Decision Gates

This separation preserves audit independence. The actor that decomposes and routes work must not be treated as one ordinary worker template that can also execute and approve its own plan.

## Naming Rules

Template names must be functional role-action nouns.

Template names must not contain:

- LLM vendor names
- model family names
- tool names
- product names
- environment names
- host names
- deployment names
- personal or organization-specific aliases

Forbidden naming examples include names containing:

```text
claude
codex
glm
gemini
qwen
gpt
opus
sonnet
haiku
cc
claude_code
codex_mac
wsl
cloud_24h
local
private_host_label
```

Allowed naming style examples:

```text
feature_coder
code_reviewer
security_auditor
technical_writer
source_researcher
roadmap_planner
```

## Template Descriptor Schema

Future role templates should use this descriptor shape:

```yaml
role_template_id:
role_template_version:
role_template_status: proposed
category:
display_name:
description:
allowed_task_modes: []
preferred_model_tiers: []
allowed_model_tiers: []
model_tier_range:
  min:
  max:
llm_provider_preference: any
sandbox_runtime_compatibility:
  - local_process
  - container
  - microvm
  - managed_remote
host_category:
  - local
  - cloud_persistent
  - ephemeral
  - hybrid
capabilities_required: []
capabilities_recommended: []
write_scopes: []
read_scopes: []
memory_access:
output_target:
escalation_rules: []
review_requirements:
audit_requirements:
owner_gate_triggers: []
forbidden_modes: []
separation_rules: []
supersession:
  status: live
  supersedes:
  superseded_by:
  rationale:
references:
  source_protocol:
```

Allowed `llm_provider_preference` values are advisory abstractions:

```text
any
cost_optimized
latency_optimized
privacy_optimized
quality_optimized
offline_capable
```

Allowed `sandbox_runtime_compatibility` values:

```text
local_process
container
microvm
managed_remote
```

Allowed `host_category` values:

```text
local
cloud_persistent
ephemeral
hybrid
```

Allowed `role_template_status` values:

```text
proposed
live
superseded
archived
blocked
```

## Baseline Worker Categories

AIPOS-97 defines 12 baseline worker categories with 48 candidate template names.

The list is normative as a catalog planning baseline, but AIPOS-97 does not create concrete YAML files for these templates.

### code

- `feature_coder`
- `refactor_coder`
- `integration_coder`
- `api_coder`

### research

- `source_researcher`
- `market_researcher`
- `technical_researcher`
- `evidence_synthesizer`

### documentation

- `technical_writer`
- `api_documenter`
- `runbook_writer`
- `changelog_writer`

### design

- `product_designer`
- `interaction_designer`
- `visual_designer`
- `design_reviewer`

### content

- `content_writer`
- `editorial_planner`
- `script_writer`
- `content_editor`

### reporting

- `status_reporter`
- `metrics_analyst`
- `incident_reporter`
- `executive_summarizer`

### planning

Planning category templates are worker templates for scoped sprint, release, roadmap, and dependency planning. They are not the Coordinator Planner contract.

- `roadmap_planner`
- `sprint_planner`
- `release_planner`
- `dependency_planner`

### audit_review

- `code_reviewer`
- `security_auditor`
- `compliance_reviewer`
- `risk_reviewer`

### testing_qa

- `test_designer`
- `test_automation_builder`
- `regression_tester`
- `accessibility_tester`

### operations

- `deployment_planner`
- `runbook_operator`
- `monitoring_reviewer`
- `rollback_planner`

### business_intelligence

- `data_analyst`
- `competitive_analyst`
- `forecast_reviewer`
- `pricing_researcher`

### owner_support

- `inbox_triager`
- `decision_preparer`
- `meeting_brief_writer`
- `followup_tracker`

## Category Boundaries

Categories intentionally excluded from the baseline catalog:

- self-learning trajectory roles
- automatic skill-recommendation roles
- automatic consolidation roles
- forgetting-curve memory-manager roles
- marketplace publication roles
- cross-organization federation sync roles
- automatic judge or router roles
- graph-reasoning optimizer roles
- model fine-tuning trainer roles

These exclusions preserve Lybra's audit-first, file-authoritative, Owner-gated governance model.

## Relationship To Runtime Authority

Role templates do not grant authority.

A task remains claimable or executable only when normal gates pass:

- AIPOS-47 model routing
- AIPOS-48 dispatch matching and task claim
- AIPOS-50 session lease and runtime binding
- AIPOS-52 draft publish gates when applicable
- AIPOS-53 Owner Decision Gates
- AIPOS-54 planner loop boundaries when applicable
- AIPOS-77 controlled persistence gates when applicable
- AIPOS-94 autonomy tier constraints when applicable
- independent review/audit separation
- Owner approval where required

If a template and an instance disagree, the stricter matching and governance rule wins.

## Relationship To Context Bundles

Role templates extend the context bundle concept with reusable role metadata.

The role catalog may later become a structured library used by context pack creation, task matching previews, and Board review UI. AIPOS-97 does not implement those integrations.

`3_context_bundles/base_schema.md` declares generic optional fields that role templates may use. Those fields remain declarative and advisory unless a later matching implementation uses them under Owner-approved rules.

## Supersession

Template evolution must use explicit supersession:

- do not silently rewrite a live role template in a way that changes authority
- mark old template as `superseded` when replacing it
- record `supersedes`, `superseded_by`, and `rationale`
- preserve auditability for task cards or context packs that referenced the old template

Supersession does not delete historical references.

## Future Implementation Notes

Future implementation tasks may add:

- concrete template YAML files under `3_context_bundles/roles/`
- validation for naming rules
- Board preview for template/instance matching
- context pack integration
- task matching advisory views
- template supersession tooling

Each implementation must pass independent audit before finalize.

## Non-Goals

AIPOS-97 does not implement:

- concrete role template YAML files
- automatic profile generation
- role marketplace
- IPFS distribution
- federation
- cross-organization profile sync
- graph neural network skill recommendation
- self-learning trajectory service
- automatic consolidation
- automatic routing
- automatic judge behavior
- model fine-tuning
- changes to concrete agent instances
- changes to the existing agent registry values
- runtime authority changes
- matching engine changes
- task claim behavior
- session lease behavior
- backend routes
- Web UI controls
- CLI commands
- MCP tools
- sandbox runtime launch
- SessionStore writes
- database
- auth/RBAC
- deployment configuration
- public endpoint behavior
- autonomous planner runtime
- git automation
- automatic commit or push
- automatic finalize
- self-audit
