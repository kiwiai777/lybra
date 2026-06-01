# GitHub Release Draft - v0.2.0

Drafted for Owner review. The actual GitHub Release publication remains an Owner action.

## Tag plan

- Target tag: `v0.2.0`
- Tag target: the commit that contains the `0.2.0` package version bump and the release-note alignment in this branch
- Policy: npm version, git tag, and GitHub Release version must match exactly

## Summary

Lybra `0.2.0` is the first release line that reflects the current public-facing shape of the project:

- Board default port is `7117`
- MCP HTTP/SSE default port is `7118`
- the README now presents Lybra as a local-first, file-authoritative accountability harness
- GitHub Pages content is split into a branded landing page and a getting-started page
- AI-assisted task authoring is available in both fixture-only and live BYO-LLM CLI forms
- custom agent identity now uses opaque IDs plus explicit provenance and editable display names
- task complexity classes are in place, with enforced independent audit for complex work
- the npm package name is `lybra`, with Apache-2.0 licensing and a CLI wrapper that delegates to Python

## Highlights

### Default port alignment

- Board now defaults to `7117`
- MCP HTTP/SSE now defaults to `7118`
- docs, runbooks, and the Playwright visual config were aligned to the new defaults

### AI-assisted task authoring

- fixture-only CLI authoring remains available for deterministic drafting and validation
- live BYO-LLM CLI authoring is isolated from the fixture path
- credential handling stays explicit and environment-driven
- raw prompt and raw response content remain out of the filesystem by default

### Identity and governance

- custom agent identities are opaque and presentation is separated from authority
- provenance is explicit and auditable
- complex tasks keep the independent-audit requirement intact

### Packaging and install

- npm package: `lybra`
- package license: `Apache-2.0`
- CLI wrapper delegates to the Python implementation
- source checkout, local npm smoke, and public npm install paths are documented

## Notes for the release post

The following work remains in development and should stay in `Unreleased` or later release notes:

- custom-profile Board UI
- trace-native audit
- state staleness and provenance hardening
- adaptive gate intensity

## Release date

- `2026-06-01`
