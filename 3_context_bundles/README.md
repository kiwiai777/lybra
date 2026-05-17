# Context Bundles

## What is Context Bundle

A Context Bundle is a startup context package that provides boundaries, required files, memory access rules, and completion expectations for an agent instance.

## What Context Bundle is NOT

- A Context Bundle is NOT a fixed, permanent job description.
- A Context Bundle is NOT a hard limitation that freezes an agent into a narrow role.
- A Context Bundle is NOT an automation scheduler.

## Relation to:

- **Role Instance**: The Context Bundle scopes the environment and identity of the role instance but does not freeze its capabilities.
- **Task Mode**: The Context Bundle defines `allowed_task_modes` (derived from the role registry's `can_act_as`), but the actual `task_mode` used is dynamically set by the task card.
- **Model Tier**: The Context Bundle outlines `allowed_model_tiers` and `preferred_model_tiers`, but the specific model tier (L1/L2/L3) is chosen based on task risk and requirements.
- **Agent Instance**: The Context Bundle can explicitly map to a concrete `agent_instance` (e.g., `info.hermes.cloud.l1`), particularly for remote 24h agents.

## Usage Flow

1. Context Bundle is selected before task start to provide startup context.
2. Task card defines the actual `task_mode` to be used for the current execution.
3. Model tier is selected based on task risk and escalation requirements.
4. Agent executes within the boundaries of the Context Bundle and reports back to the specified `output_target`.
