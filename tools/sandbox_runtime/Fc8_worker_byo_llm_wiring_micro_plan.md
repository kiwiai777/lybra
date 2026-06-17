# F-c8 Slice Micro-Plan — confined worker BYO-LLM wiring (base_url + model + config dir)

Status: PLAN ONLY. No product code is changed in this step. Implement only after
review + Owner approval. Raw LLM key stays fingerprint-only (never argv/report/log/
git); base_url and model are non-secret and may appear in argv/report.

## Trigger

F-c8 (run plan §6): `confined_worker.build_docker_argv` injects only
`--env ANTHROPIC_API_KEY` and runs `claude -p …` with no `--model`. A BYO-LLM
proxy credential that serves one model at a custom base_url cannot be used, so a
Round-1 real run is blocked.

## Connectivity conclusion (slice-spec input, re-verified)

Key fingerprint `sha256:5c75db387b52` (value never shown).

- **base_url that works:** raw API at `https://xchai.xyz/v1/messages`; for the
  claude CLI set `ANTHROPIC_BASE_URL=https://xchai.xyz` (no `/v1`; the CLI appends
  `/v1/messages`).
- **auth header:** `x-api-key` (i.e. `ANTHROPIC_API_KEY`). `Authorization: Bearer`
  (`ANTHROPIC_AUTH_TOKEN`) routes to a group with no sonnet channel → HTTP 500.
  Owner directive: use `ANTHROPIC_API_KEY` only.
- **claude -p against the proxy:** replies "OK" (exit 0) with
  `ANTHROPIC_BASE_URL=https://xchai.xyz` + `ANTHROPIC_API_KEY` + `--model claude-sonnet-4-6`.
- **exact model id:** `claude-sonnet-4-6` (echoed verbatim in the 200 responses).
- **proxy reliability:** intermittent — 4/5 calls HTTP 200; one transient 500
  (`分组 aws-pro 下模型 claude-sonnet-4-6 的可用渠道不存在（retry）`) and one 30s
  timeout. Self-heals on retry; claude CLI's built-in retries should absorb most.
  Operational caveat for Step 4 (re-run on transient failure); not a slice change.

## Scope (strict)

1. `confined_worker.py`: add **`ANTHROPIC_BASE_URL` passthrough** — optional; when
   unset, omit it (default direct connection unchanged).
2. **Model selection** — configurable `ANTHROPIC_MODEL` env and/or `claude --model`.
3. **Writable config dir** — inject `--env CLAUDE_CONFIG_DIR=<tmpfs path>` in
   `build_docker_argv`; rebuild the image WITHOUT the `HOME=/tmp` / `CLAUDE_CONFIG_DIR`
   stopgap so the image stays clean (the tool owns this wiring).
4. **Auth header variant** — per connectivity result, keep `ANTHROPIC_API_KEY`
   (x-api-key) as the default; optionally pass through `ANTHROPIC_AUTH_TOKEN`
   (Bearer) only when explicitly selected. Both remain env passthrough (value never
   in argv).
5. **Explicitly NOT doing:** no change to `_FORBIDDEN_NETWORKS`, the non-public
   gate constraint, truth/.lybra/product non-mounting, the projection secret scan,
   scratch permissions, teardown, L3, or 196a. No egress allowlist. Nothing outside
   the 191B worker wiring.

## Change list (precise)

`tools/sandbox_runtime/confined_worker.py`:
- New constants: `ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"`,
  `ANTHROPIC_MODEL_ENV = "ANTHROPIC_MODEL"`, `ANTHROPIC_AUTH_TOKEN_ENV =
  "ANTHROPIC_AUTH_TOKEN"`, `CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"`,
  `CLAUDE_CONFIG_DIR_VALUE = "/tmp/.claude"`.
- `ConfinedWorkerRequest`: add optional fields `anthropic_base_url: str|None`,
  `model: str|None`, `auth_mode: "api_key"|"auth_token" = "api_key"`.
- `build_docker_argv`:
  - inject `--env CLAUDE_CONFIG_DIR=/tmp/.claude` (always; tmpfs-backed).
  - if `anthropic_base_url` set → `-e ANTHROPIC_BASE_URL=<value>` (non-secret, inline).
  - credential passthrough: `--env ANTHROPIC_API_KEY` (default) OR
    `--env ANTHROPIC_AUTH_TOKEN` when `auth_mode == "auth_token"` (passthrough; no value in argv).
  - if `model` set → append `--model <model>` to the claude command; optionally also
    `-e ANTHROPIC_MODEL=<model>` for harness env compatibility.
- `build_worker_report`: add a non-secret `llm` block: `base_url`, `model`,
  `auth_mode`, `config_dir`, plus the existing key fingerprint. Never the key value.
- CLI (`build_parser`/`build_request_from_args`): add `--anthropic-base-url`,
  `--model`, `--auth-mode {api_key,auth_token}`; read the key from the tool env as
  today; surface base_url/model in the report.

`tools/sandbox_runtime/Dockerfile` (build dir): remove the `ENV HOME=/tmp` /
`ENV CLAUDE_CONFIG_DIR=/tmp/.claude` stopgap lines (the tool now injects
`CLAUDE_CONFIG_DIR`; `HOME` handled via tool env if needed). Keep node + claude
install only.

## Tests (docker-free unit tests in `tests/test_confined_worker.py`)

- argv contains `-e ANTHROPIC_BASE_URL=<url>` when set; absent when unset.
- claude command contains `--model claude-sonnet-4-6` when set; `ANTHROPIC_MODEL`
  env present when configured.
- argv contains `--env CLAUDE_CONFIG_DIR=/tmp/.claude`.
- default auth → `--env ANTHROPIC_API_KEY` (passthrough, no value); `auth_mode=auth_token`
  → `--env ANTHROPIC_AUTH_TOKEN`, and `ANTHROPIC_API_KEY` not injected.
- worker report `llm` block carries base_url/model/auth_mode/config_dir and only a
  key fingerprint; the serialized report and argv contain no raw key.
- regression: existing argv hardening / mount / scratch / projection-scan / teardown
  tests still pass unchanged.

## Image rebuild

- Edit the build-dir Dockerfile (drop the ENV stopgaps), `docker build -t
  lybra/claude-worker:191b .` (bump tag if Owner prefers), record image id +
  `claude --version`. The tool injecting `CLAUDE_CONFIG_DIR=/tmp/.claude` + tmpfs
  /tmp gives claude a writable config dir under the read-only rootfs.

## Regression

- `PYTHONPATH=$PWD python -m pytest tools/sandbox_runtime/tests/ -q` and full
  `tools/` suite (current baseline 358) must stay green.

## cc glm audit points

- Safety envelope unchanged: `_FORBIDDEN_NETWORKS`, non-public gate, no
  truth/.lybra/product mounts, projection secret scan, scratch 0777-run-dir only,
  teardown + host token unlink — all intact.
- Raw key never in argv, report, or logs (fingerprint only); base_url/model are
  non-secret and correct.
- `auth_mode` default is `api_key` (x-api-key); Bearer only when explicitly chosen.
- No scope creep beyond the four wiring points; CLAUDE_CONFIG_DIR points only at
  the tmpfs path; image has no baked secrets.
- Real-container spot check (auditor): with base_url+model+key, the worker's claude
  reaches the proxy and selects sonnet-4-6, while rootfs stays read-only and truth
  unmounted.

## Loop

connectivity + this micro-plan → review → Owner approval → implement → full tests →
cc glm audit → Owner approval → finalize → rebuild image → resume 191B Step 4
(dry-run argv → real run).
