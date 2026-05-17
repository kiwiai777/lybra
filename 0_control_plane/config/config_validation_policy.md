# Config Validation Policy

## Purpose

This policy defines how AI Project OS validates configuration files (YAML, JSON, TOML, etc.) to prevent syntax errors that could break Control Plane operations.

## Scope

- Applies to all Control Plane configuration files:
  - `0_control_plane/agents/agent_registry.yaml`
  - `0_control_plane/agents/model_tier_registry.yaml`
  - `0_control_plane/roles/role_registry.yaml`
  - `0_control_plane/environments/environment_registry.yaml`
- Does NOT apply to project-specific config files unless explicitly adopted by Owner.

## Validation Layers

### 1. YAML Syntax

Files must be syntactically valid YAML.

### 2. Schema Compliance

Files must adhere to defined schemas (e.g., `role_registry_schema.md`).

### 3. Readability

Files must be human-readable and maintainable.

## Validation Workflow

### 1. Pre-Commit Validation

Before committing any config file changes:

- Run YAML parser validation
- Run yamllint with policy config
- Fix any blocking errors
- Document non-blocking warnings

### 2. Post-Commit Validation

If commits are made without validation:
- Future tasks may fail due to invalid config
- Rollback may be required

## Failure Handling

### Blocking Errors

**Action: STOP. Do not commit.**

If any of these occur:
- YAML syntax error
- Schema violation
- Parser crash
- Structure corruption

**Process:**
1. Fix the error.
2. Re-run validation.
3. Only commit when validation passes.

### Non-Blocking Warnings

**Action: WARN in completion report.**

Acceptable warnings include:
- Line length (if readable)
- Trailing whitespace
- Unused keys (if not part of schema)
- Comment-only lines

**Process:**
1. Document the warning in task execution.
2. Commit with warning.
3. Track for future cleanup.

## Configurability Principle

Validation rules must be configurable and editable.

- Do NOT hardcode validation rules in scripts.
- Store validation rules in policy files.
- Use `yamllint` config for linting behavior.
- Allow Owner to adjust rules per project or environment.

## yamllint Configuration

Create: `0_control_plane/config/yamllint_config.yaml`

Must include:

extends: default

rules:
  line-length:
    max: 120
    level: warning

  document-start:
    present: true
    level: warning

  indentation:
    spaces: 2
    level: warning

  trailing-spaces:
    level: warning

  comments:
    level: disable

Important:

- Validation rules must be configurable via this file.
- Do NOT embed rules in scripts.
- Line length limit helps readability while being flexible.

## Configurability Principle

All validation rules must be stored in config files, not hardcoded in scripts or task cards.

This ensures:
- Rules can be adjusted without code changes.
- Different projects/environments may have different standards.
- Owner can tune rules without touching automation.

## Validation Workflow

### Step 1: Edit Config

Edit files to fix errors.

### Step 2: Validate

Run validation command.

### Step 3: Commit

Commit with message like:

```
docs(control-plane): fix validation issues in [file]
```

### Step 4: Review

If lint warnings exist, decide:
- Accept (if minor)
- Fix (if affects clarity)

## Examples

### Example 1: Syntax Error

**Error:**
```
yaml.parser.ParserError: while parsing a block mapping
  in "<unicode string>", line 1, column 1:
    expected <block end>, but found '-'
```

**Action:** STOP. Fix YAML syntax.

### Example 2: Lint Warning

**Warning:**
```
0_control_plane/roles/role_registry.yaml:1:1: warning: line too long (131 > 120 characters)
```

**Action:** Document in completion report. OK to commit.

### Example 3: Schema Violation

**Error:**
```
Required field 'id' missing in role entry
```

**Action:** STOP. Add missing field.

## Configurability Requirements

Validation policy must satisfy:

1. Rules are configurable.
2. Rules are stored in config files.
3. No hardcoding in scripts.
4. Owner can adjust rules without code changes.
5. yamllint config uses `extends: default`.
6. Line length limits are reasonable.
7. Document-start enforcement is optional (warning level).

## Failure Handling

If validation cannot run (e.g., yamllint not available):

- Use Python YAML parser as fallback.
- Report limitation in completion report.
- Do NOT skip validation entirely.

## Integration with Other Policies

This policy works with:

- **Role Instance Policy**: Config changes may require updating role registries.
- **Model Routing Policy**: Config changes may affect how agents are routed.
- **Agent Instance Policy**: Config changes may affect which agent instances are active.

Any config change that affects these policies should be validated and committed together.
