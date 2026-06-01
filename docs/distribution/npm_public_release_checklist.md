# npm Public Release Checklist

## Status

Release checklist for `lybra@0.2.0`. This document does not itself authorize `npm publish`.

## Registry Target

- Package: `lybra`
- Version: `0.2.0`
- Access: public
- Tag: `latest`
- Registry: `https://registry.npmjs.org/`

Registry preflight on 2026-05-30 showed:

- Current latest: `0.0.1`
- Maintainer: `kiwiai777 <kiwi.w.ai@gmail.com>`
- Description: `Lybra - placeholder. Real package coming soon.`

## Required Gates

Before publishing:

- Product package metadata names `lybra`.
- Product package version is `0.2.0`.
- Product package bin is `lybra`.
- Product package license is `Apache-2.0`.
- Product package is not marked `private`.
- Python is documented as a runtime prerequisite.
- `npm pack --dry-run` passes.
- Packed tarball includes `LICENSE`.
- Packed tarball excludes `.git`, `.codex`, `task_cards`, `__pycache__`, `.DS_Store`, `._*`, `.env`, `node_modules`, `*.pyc`, `*.tgz`, generated caches, private workspace data, and runtime workspace data.
- Packed tarball can be installed into a clean temporary prefix.
- Installed `lybra --help` succeeds.
- Installed `lybra workspace init --dry-run --json` succeeds.
- CLI tests pass.
- Board tests pass.
- Independent audit returns PASS.
- Owner explicitly approves the final publish command after audit PASS.

## Publish Command

Use a temporary npm config file. Do not commit tokens and do not print token values.

```bash
read -s NPM_TOKEN
export NPM_CONFIG_USERCONFIG=/tmp/lybra-npmrc
printf "//registry.npmjs.org/:_authToken=${NPM_TOKEN}\n" > "$NPM_CONFIG_USERCONFIG"

npm whoami --registry=https://registry.npmjs.org/
npm publish --access public --tag latest --registry=https://registry.npmjs.org/

rm -f "$NPM_CONFIG_USERCONFIG"
unset NPM_TOKEN NPM_CONFIG_USERCONFIG
```

## Post-Publish Verification

```bash
npm view lybra version dist-tags --registry=https://registry.npmjs.org/
npm install --global --prefix /tmp/lybra-npm-public-smoke lybra@0.2.0
/tmp/lybra-npm-public-smoke/bin/lybra --help
```
