<p align="center">
  <img src="docs/assets/lybra-banner.png" alt="Lybra — the accountability harness for AI agents" width="880">
</p>

<p align="center">
  <b>The accountability harness for AI agents.</b><br/>
  面向 AI Agent 的治理型 harness —— 文件为真相源，Owner 掌握每一道决策闸门，每一步皆可审计、可复现。
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/lybra"><img alt="npm" src="https://img.shields.io/npm/v/lybra?color=0D5A3B&amp;label=npm"></a>
  <img alt="node" src="https://img.shields.io/node/v/lybra?color=0D5A3B">
  <img alt="license" src="https://img.shields.io/github/license/kiwiai777/lybra?color=0D5A3B">
  <img alt="status" src="https://img.shields.io/badge/status-early%20access-1A7A52">
</p>

---

## What is Lybra

Lybra is a **local-first, file-authoritative control plane** for AI agents — a harness that puts **governance, verification, and observability first**.

Most agent platforms optimize for **autonomy**. Lybra optimizes for **accountability**.

- **Files are the single source of truth** — state lives in durable files, not in compressed conversation memory. State outlives the model.
- **The owner holds every decision gate** — architecture, risk, and scope decisions are non-delegable.
- **Independent audit is enforced** — an executor can never audit its own work.
- **Agents come to Lybra (MCP-native)** — no autonomous runtime, no heartbeat polling.

> The model isn't the bottleneck. The harness is.

## Why it matters

Enterprises need AI they can hold accountable — not a polished demo. If you can't assign responsibility, reproduce a result, or audit a decision, you can't put an agent into real business. Lybra moves AI agents **from demos to accountable, repeatable work**.

## How it works

Three peer surfaces share one permission-and-audit backend:

| Surface | Role |
|---------|------|
| **CLI** | command-line operation |
| **Board** | local dashboard for review |
| **MCP** | any agent connects in |

Every write to the workspace is forced through one path — no shortcuts:

```
dry-run  →  confirm  →  Owner Decision Gate  →  independent audit  →  written to files
```

## Where Lybra sits — ETCLOVG

Against the seven-layer harness model (Execution · Tooling · Context · Lifecycle · Observability · Verification · Governance), Lybra is **deliberately heavy on Governance, Verification, and Observability**, and **deliberately does not do autonomous Execution or Lifecycle orchestration**. That's a stance, not a gap.

See [`docs/positioning/etclovg_self_assessment.md`](docs/positioning/etclovg_self_assessment.md).

## Install

```bash
npm install -g lybra
lybra --help
```

Lybra needs Node.js 18+ and Python 3 available on `PATH`.

For source checkout contributors:

```bash
git clone <repo-url>
cd lybra
python3 -m unittest discover -s tools/aipos_cli/tests
python3 -m unittest discover -s web/board/tests
```

Workspace commands auto-discover `.lybra/config.json` or `5_tasks/queue` from the current directory upward. Explicit flags and `AIPOS_WORKSPACE_ROOT` still override discovery.

## Quick start

```bash
npm install -g lybra
lybra init ./my-workspace --project-id my_project
cd ./my-workspace
lybra board
```

Board starts on `http://127.0.0.1:7117` by default.

For MCP:

```bash
export NO_PROXY=127.0.0.1,localhost,::1
export LYBRA_MCP_TOKEN="<set-your-token>"
export LYBRA_CAPABILITY_TOKEN='<json-capability-token>'
lybra mcp
lybra mcp-config
```

MCP HTTP/SSE starts on `http://127.0.0.1:7118` by default. `lybra mcp-config` prints endpoint and environment references for an agent without printing raw token values.
Set `NO_PROXY` when your shell has `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY` configured, so local loopback MCP traffic is not intercepted by a proxy.

## The closed loop

For complex work, every task runs the same accountable loop:

```
Plan  →  Execute  →  Independent Audit  →  Fix / Finalize
```

The executor and the auditor must be **different parties**; nothing is finalized without an audit pass. Simple tasks close in a single step — but still leave an auditable trail.

## Contributing

Changes that affect workflow gates, persistence boundaries, default ports, release surfaces, or audit rules should stay within the Owner decision flow and be validated before finalize. Keep product changes file-authoritative and narrowly scoped.

## About

Built by **KIWIAI**.

Lybra helps teams turn AI from a personal trial tool into a **manageable, reusable, traceable, and continuously improvable** enterprise-grade system.

## License

Apache-2.0. See [LICENSE](LICENSE).
