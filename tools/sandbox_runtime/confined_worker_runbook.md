# AIPOS-196b Confined Worker Runbook (real live run + §14 adversarial audit)

This runbook is for the Owner and the independent auditor (cc glm). The executor
delivered the tool, docker-free unit tests, and dry-run report; the **real
container live run and the AIPOS-195 §14 adversarial evidence are produced here**,
not self-run by the executor.

Tool: `tools/sandbox_runtime/confined_worker.py`. It is shell-free, one-shot, and
ephemeral. It does not schedule, poll, heartbeat, daemonize, or auto-restart.

## 0. Prerequisites

- `docker` available; a worker image that contains the Claude Code CLI (`claude`)
  and Node. Build/select it explicitly; the tool uses `--pull never`.
- A Lybra service-mode `connection.json` with role tokens (executor / auditor /
  owner-dispatch). Default location: `.lybra/local/connection.json`.
- `ANTHROPIC_API_KEY` exported in the operator shell (passed to docker by env
  passthrough; its value never appears in argv, projection, scratch, or report).

## 1. Dedicated bridge network (network posture)

```bash
docker network create --driver bridge lybra-worker-net
GATE_IP=$(docker network inspect lybra-worker-net -f '{{ (index .IPAM.Config 0).Gateway }}')
echo "gate ip = $GATE_IP"   # host-private, e.g. 172.18.0.1 — NOT public
```

Only the worker attaches to this network. NAT gives the worker egress to the
Anthropic API; the gate is reachable at the bridge gateway IP. The gate must not
bind a public interface.

## 2. Start the worker-facing MCP gate (non-public)

`serve-http --host` does not force loopback (the loopback-only guard lives only in
`service_mode.start_report`), so bind the gate directly to the bridge gateway IP
without changing service_mode:

```bash
PYTHONPATH=$PWD python -m tools.mcp_server serve-http \
  --host "$GATE_IP" --port 7118 \
  --service-connection-json .lybra/local/connection.json
```

Verify it is NOT listening on a public address (only on `$GATE_IP`):

```bash
ss -ltnp | grep 7118   # expect bind on $GATE_IP:7118, not 0.0.0.0:7118
```

## 3. Approved scratch root (aligns with AIPOS-196a)

```bash
export LYBRA_APPROVED_SCRATCH_ROOT=/srv/lybra/scratch   # outside repo, .lybra, product
mkdir -p "$LYBRA_APPROVED_SCRATCH_ROOT"
```

The host scratch dir bind-mounts to the container `/scratch`. The agent passes the
HOST scratch path (injected as `LYBRA_SCRATCH_HOST_DIR`) to `queue_return`; the
host gate validates it inside `LYBRA_APPROVED_SCRATCH_ROOT` and ingests it.

## 4. Dry-run inspection, then real run

```bash
# Inspect the exact docker argv + non-secret report (writes no secrets):
PYTHONPATH=$PWD python -m tools.sandbox_runtime.confined_worker \
  --image lybra/claude-worker:local \
  --task-id AIPOS-XXX \
  --connection-json .lybra/local/connection.json \
  --approved-scratch-root "$LYBRA_APPROVED_SCRATCH_ROOT" \
  --network lybra-worker-net --gate-url "http://$GATE_IP:7118/mcp" --gate-ip "$GATE_IP" \
  --tmp-root /srv/lybra/worker_tmp \
  --prompt "Claim AIPOS-XXX, do the bounded work, write outputs to /scratch, then queue_return with scratch_dir=$LYBRA_SCRATCH_HOST_DIR." \
  --dry-run

# Real one-shot run (drop --dry-run):
PYTHONPATH=$PWD python -m tools.sandbox_runtime.confined_worker \
  --image lybra/claude-worker:local --task-id AIPOS-XXX \
  --connection-json .lybra/local/connection.json \
  --approved-scratch-root "$LYBRA_APPROVED_SCRATCH_ROOT" \
  --network lybra-worker-net --gate-url "http://$GATE_IP:7118/mcp" --gate-ip "$GATE_IP" \
  --tmp-root /srv/lybra/worker_tmp \
  --prompt "..."
```

The worker report is non-secret (fingerprints only). Capture it as evidence.

## 5. AIPOS-195 §14 adversarial checks (real paths)

Run the probe inside a container with the SAME mounts/network but an interactive
entrypoint, e.g.:

```bash
RUN_ID=cw_probe_$(date +%s)
mkdir -p "$LYBRA_APPROVED_SCRATCH_ROOT/$RUN_ID"
docker run --rm -it \
  --network lybra-worker-net --pull never \
  --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 --read-only --tmpfs /tmp \
  --mount type=bind,source=$LYBRA_APPROVED_SCRATCH_ROOT/$RUN_ID,target=/scratch \
  --mount type=bind,source=/srv/lybra/worker_tmp/$RUN_ID.json,target=/etc/lybra/mcp.json,readonly \
  -e NO_PROXY=127.0.0.1,localhost,::1,$GATE_IP \
  -e GATE_URL="http://$GATE_IP:7118/mcp" \
  --mount type=bind,source=$PWD/tools/sandbox_runtime/probe_confined_worker.sh,target=/probe.sh,readonly \
  lybra/claude-worker:local bash /probe.sh
```

Expected results (all must hold):

- Writing `5_tasks/queue/**`, `5_tasks/records/**`, `.lybra/local/**`, the product
  repo, and host fs paths → **fail** (paths are not mounted; rootfs is read-only).
- `/scratch` is the only writable location.
- The executor Bearer token calls `queue_claim` / `queue_return` successfully, but
  `audit_verdict` / `audit_dispatch` → **SCOPE_DENIED**.
- The only successful Lybra truth mutation path is the MCP gate.
- Scratch artifacts become durable only after an Owner-confirmed `queue_return`
  ingests them into `workspace_artifacts/<task>/<return_id>/`.
- No raw role token and no LLM key appears in the projection, the worker report,
  records, or git-tracked files.
- The container reaches the gate and the LLM under this posture, and the gate is
  not listening on a public address.

## 6. Teardown verification

```bash
docker ps -a | grep cw_   # expect no leftover confined-worker containers (--rm)
ls -l /srv/lybra/worker_tmp/<run_id>.json   # expect: No such file (host token file removed)
```

The worker report's `teardown` block must show `token_file_removed: true` and
`verified: true`. `--rm` removes only the container; the host token file is
explicitly unlinked by the tool.

## Notes

- Egress allowlist / DLP is deferred (AIPOS-195 §8). This slice delivers the
  filesystem + credential wall plus a host-private gate posture, not egress
  control.
- This runbook does not change AIPOS-101 `local_docker.py`, AIPOS-196a
  `artifact_ingest.py`, Layer 3 detection, or service_mode's loopback guard.
