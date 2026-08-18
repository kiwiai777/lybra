# Skill: card-policy-author

**Purpose**: Guide advisor + Owner to define/modify project-specific card validation rules, then generate the declaration file and place it in the project governance repo.

**Scope**: Product-side skill. Content is project-agnostic — it reads the project's own schema and registry, never hardcodes project-specific words.

**Owner decision required**: This skill's final installation directory is pending Owner decision. The card (AIPOS-R8C) proposes:
- `agents/skills/<name>/` — role-agnostic content (single source)
- `agents/roles/<role>/` — charters + which skills each role loads
- `agents/harness/<pi|cc|codex>/` — installation location & format adaptation

**This card delivers**: skill content only. Distribution is out of scope (归门前卡C 的分发器).

---

## When to use

- When a project wants to add/modify card validation rules (e.g., require certain fields on task cards)
- When onboarding a new project and defining its initial card policy
- When reviewing or auditing existing card policy rules

## Prerequisites

- The project must have a governance repo with a `card_policy.json` (or path configured in `config.schema.json`'s `multi_project_support.project_registry.structure.card_policy`)
- The project must have schema files under `schema/` in the product repo (for `values_from` resolution)

## Procedure

### Step 1: List current card_policy declaration

Read the project's current `card_policy.json` from its governance root:

```
<workspace>/card_policy.json
```

If no file exists, the project has no custom card validation rules (zero-invasion: default behavior).

Display the current rules in human-readable form:
- For each rule: field name, required (yes/no), value source (values_from or literal values), message

### Step 2: Guide rule definition

For each rule the advisor/Owner wants to add or modify, collect:

1. **field** (string): The card field name this rule applies to
   - Must be a valid frontmatter field name (alphanumeric + underscores)
   - Can be a field already in `card.schema.json` OR a project-specific field not in the product schema

2. **required** (boolean): Whether the field must be present on every card

3. **Value constraint** (choose one or both):
   - **values_from** (string): Dynamic value source. Syntax: `<schema_file>#<json_path>[+<schema_file>#<json_path>...]`
     - `<schema_file>`: filename under `schema/` (e.g., `transitions.schema.json`)
     - `<json_path>`: dot-separated path. Use `[]` for array iteration.
       - Example: `main_flow.nodes[].node_id` → extracts node_id from each element
       - Example: `cross_cutting` → extracts keys of the cross_cutting object
     - Multiple sources joined with `+` (union)
   - **values** (array): Literal allowed values (e.g., `["simple", "complex"]`)

4. **message** (string): Human-readable violation message. Shown when a card violates this rule.
   - Should explain what's wrong and hint at how to fix it

### Step 3: Generate declaration file

Assemble the rules into a JSON declaration file:

```json
{
  "schema_version": "1.0.0",
  "description": "<project> project-specific card validation rules (AIPOS-R8C)",
  "rules": [
    {
      "field": "<field_name>",
      "required": true/false,
      "values_from": "<schema_file>#<path>",
      "message": "<violation message>"
    }
  ]
}
```

Write to `<governance_root>/card_policy.json`.

### Step 4: Verify

Run validation against a test card to confirm the rules work:

```python
from tools.card_policy_loader import evaluate_card_policy_rules

blocking, warnings = evaluate_card_policy_rules(
    {"task_id": "TEST", "title": "Test", ...},  # test metadata
    governance_root="<governance_root>",
)
print("blocking:", blocking)
```

### Step 5: Explain activation chain

Rules take effect through a three-level chain:
1. **deploy**: The declaration file must be in the governance repo (committed/pushed)
2. **distribute**: The gate/CLI reads the declaration on each validation call (no restart needed for file changes)
3. **session**: Each validation call loads the current file content (live reload)

Note: If the declaration references `values_from` pointing to schema files, those schema files must be accessible via the product repo's `schema/` directory.

## Acceptance demonstration (fictional project)

Let's demonstrate using this skill to define a rule for a fictional project "acme-corp":

### Fictional project setup
- Project: acme-corp
- Governance root: `/tmp/acme-corp-gov/`
- Schema: has `workflow.schema.json` with stages: `["intake", "review", "approved", "shipped"]`

### Step 1: No existing card_policy
```
$ cat /tmp/acme-corp-gov/card_policy.json
→ File not found (no custom rules)
```

### Step 2: Define a rule
Advisor + Owner agree:
- Field: `workflow_stage`
- Required: true
- Values from: `workflow.schema.json#stages`
- Message: "workflow_stage is required; must be a valid workflow stage"

### Step 3: Generate declaration
```json
{
  "schema_version": "1.0.0",
  "description": "acme-corp project card validation rules",
  "rules": [
    {
      "field": "workflow_stage",
      "required": true,
      "values_from": "workflow.schema.json#stages",
      "message": "workflow_stage is required; must be a valid workflow stage"
    }
  ]
}
```

Written to `/tmp/acme-corp-gov/card_policy.json`.

### Step 4: Verify
```python
# Missing workflow_stage → BLOCK
evaluate_card_policy_rules({"task_id": "T1"}, governance_root="/tmp/acme-corp-gov")
# → blocking: ["workflow_stage is required; must be a valid workflow stage"]

# Valid value → PASS
evaluate_card_policy_rules({"task_id": "T1", "workflow_stage": "intake"}, governance_root="/tmp/acme-corp-gov")
# → blocking: []

# Invalid value → BLOCK
evaluate_card_policy_rules({"task_id": "T1", "workflow_stage": "bogus"}, governance_root="/tmp/acme-corp-gov")
# → blocking: ["...invalid value(s): ['bogus']; allowed: ['approved', 'intake', 'review', 'shipped']"]
```

### Step 5: Activation
The rule is live immediately — next validation call reads the file.

---

## Key principles

1. **Product is semantics-blind**: The product executor doesn't know what `anchor_refs` or `workflow_stage` means. It only checks "field exists + value in allowed set".
2. **Rules belong to the project**: The declaration file lives in the project's governance repo, not the product repo.
3. **Zero-invasion**: No card_policy file = no extra checks. Existing projects are unaffected.
4. **Schema is the source**: `values_from` resolves against the product's schema files. If the schema changes, allowed values update automatically.
5. **Owner decides**: The skill presents options and generates files, but the Owner (with advisor) decides what rules to define.
