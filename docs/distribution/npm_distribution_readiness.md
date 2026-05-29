# npm Distribution Readiness / Packaging Protocol

## Status

Protocol for a future npm package implementation. This document does not implement npm packaging and does not approve `npm publish`.

## Current Product Shape

Lybra is currently a Python-first product repository:

- CLI entrypoint: `python3 tools/aipos_cli/aipos_cli.py ...`
- CLI module form: `python3 -m tools.aipos_cli.aipos_cli ...`
- MCP server entrypoint: `python3 -m tools.mcp_server ...`
- Sandbox runtime entrypoint: `python3 -m tools.sandbox_runtime ...`
- Board server entrypoint: `python3 web/board/app.py ...`
- Bundled workspace templates live under `templates/`.

The repository currently has no npm package metadata, no npm binary wrapper, no `.npmignore`, no npm `files` allowlist, and no finalized public license file.

## Distribution Decision

The first npm distribution should be a command distribution, not a JavaScript library API.

The npm package should install a `lybra` command that delegates to the bundled Python implementation. The package exists to make local installation and command discovery easier for users who already have Node/npm workflows, while preserving Lybra's Python implementation and file-authoritative workspace model.

## Package Name Gate

Before implementation, Owner must choose the package name:

- Preferred unscoped name if available and approved: `lybra`
- Safer scoped fallback: `@kiwiai/lybra`

The implementation task must verify registry availability or ownership before any publish attempt. If the chosen package name is unavailable, stop for Owner decision instead of selecting an alternate automatically.

## Version Gate

The first npm package should be pre-1.0 unless Owner explicitly decides otherwise.

Recommended initial version shape:

```text
0.1.0
```

If registry publication is delayed after implementation, the local package version may still be prepared but must not be published without Owner approval.

## License Gate

The product README currently says `License: TBD by Owner`.

No public npm publish should happen until Owner selects a license and the product repo contains an appropriate license file and package metadata license field. Package implementation may prepare a local package only if the package metadata clearly avoids claiming a finalized public license.

## Required Implementation Files

A future npm implementation slice should add the minimum packaging surface:

- `package.json`
- `bin/lybra` or equivalent executable wrapper
- `.npmignore` or a `package.json` `files` allowlist
- README install section
- package smoke tests or documented smoke commands

The wrapper should:

- Locate the package root reliably from the installed binary.
- Invoke Python without shell interpolation.
- Prefer `python3`, with a clear error if Python is missing.
- Preserve CLI arguments exactly.
- Exit with the same status code as the Python command.
- Avoid network access, telemetry, background services, credential reads, and automatic workspace mutation.

## Required Package Contents

The npm package must include only product-distribution content needed for local operation:

- `tools/`
- `web/`
- `templates/`
- `0_control_plane/`
- `3_context_bundles/`
- `docs/`
- `config/`
- `examples/`
- `README.md`
- selected package metadata and wrapper files

The package must exclude:

- `.git/`
- `.codex/`
- `task_cards/`
- `__pycache__/`
- `*.pyc`
- `.DS_Store`
- `._*`
- `.env`
- private workspace data
- runtime workspaces under `~/.lybra/`
- generated caches and test artifacts

## README Install Contract

The future README install section should distinguish three paths:

1. Source checkout for contributors:

```bash
git clone <repo-url>
cd lybra
python3 -m unittest discover -s tools/aipos_cli/tests
python3 -m unittest discover -s web/board/tests
```

2. Local npm package smoke after implementation:

```bash
npm pack
npm install -g ./lybra-<version>.tgz
lybra --help
```

3. Public npm install only after Owner-approved publish:

```bash
npm install -g <package-name>
lybra --help
```

The README must state that Lybra needs Python available on the user's PATH and that workspace state is selected with `AIPOS_WORKSPACE_ROOT` or `--repo-root` where applicable.

## Publish Readiness Gates

Before any `npm publish`, all gates must pass:

- Owner has approved package name.
- Owner has approved license.
- `package.json` has correct package name, version, bin, files/license metadata, and repository metadata.
- `npm pack --dry-run` shows no private workspace data, `.git`, `.codex`, `task_cards`, `__pycache__`, `*.pyc`, `.DS_Store`, `._*`, or `.env`.
- Installing the packed tarball into a clean temporary environment exposes `lybra`.
- `lybra --help` exits successfully.
- `lybra workspace init --dry-run ... --json` exits successfully against a temporary output path.
- Existing Python CLI tests pass.
- Existing Board tests pass.
- Independent audit returns PASS.
- Owner explicitly approves registry publish after audit.

## Out of Scope For First npm Implementation

The first npm implementation should not:

- Rewrite Lybra in JavaScript.
- Add a hosted service.
- Add remote registry/template fetch behavior.
- Add auto-update behavior.
- Install services, daemons, shell startup hooks, or background agents.
- Mint credentials or read secrets.
- Start Board, MCP, Docker, or runtime processes automatically after install.
- Publish without a separate Owner approval gate.

## Rollback

If packaging implementation fails local smoke tests:

- Do not publish.
- Keep product source behavior unchanged.
- Remove or revise packaging files in a follow-up audited slice.
- Record the failure in the private Lybra project management docs.
