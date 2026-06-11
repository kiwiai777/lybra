# AIPOS-196b Micro-Plan — Layer 2 Confined local_docker Worker (Claude Code)

## Step 0 Inventory (resumed task)

Previous executor exited before writing any 196b file (root-owned
`tools/sandbox_runtime/` blocked writes; Owner restored ownership to kiwi:kiwi).
Working tree clean; only the AIPOS-101 files existed. Implemented fresh from the
approved design. AIPOS-101 `local_docker.py` and AIPOS-196a `artifact_ingest.py`
are NOT modified.

## Goal

Confine a full-capability agent (Claude Code) in a one-shot Docker worker whose
only consequential write path into Lybra truth is the MCP gate. Reuse AIPOS-196a
gate ingestion of scratch artifacts. Protocol:
`0_control_plane/environments/confined_autonomous_worker_boundary_protocol.md`
(AIPOS-195 §5/§6/§8/§13/§14).

## Locked Decisions

1. Network: dedicated user-defined docker bridge; `serve-http` bound to that
   network's gateway IP (host-private, non-public) with NAT egress for the LLM;
   only the worker attaches. `serve-http --host` does not force loopback (the
   guard lives only in `service_mode.start_report`), so service_mode is untouched.
   Container sets `NO_PROXY=127.0.0.1,localhost,::1,<gate-ip>`.
2. Delivery: tool + docker-free unit tests + dry-run report + adversarial probe
   script + runbook. Real container live run and §14 adversarial evidence are
   executed by the independent auditor (cc glm), not self-run as evidence.
3. Token delivery: dedicated controlled temp file `<run_id>.json` (0600),
   read-only mounted at `/etc/lybra/mcp.json`; report shows fingerprint only.

## Files (all under tools/sandbox_runtime/)

- `confined_worker.py` — shell-free docker argv, mount topology, network posture,
  credential injection, MCP client config generation, non-secret worker report;
  `--dry-run` (argv/report only) and real run + teardown verification.
- `tests/test_confined_worker.py` — argv/mount/env/boundary unit tests + dry-run
  report assertions (no docker required).
- `confined_worker_runbook.md` — real live run + §14 adversarial probe steps.
- `probe_confined_worker.sh` — in-container adversarial probes.

## Docker argv (approved)

```
docker run --rm --network <dedicated-bridge> --pull never
  --security-opt no-new-privileges --cap-drop ALL --pids-limit 512 --read-only --tmpfs /tmp
  --mount type=bind,source=<host_projection>,target=/projection,readonly
  --mount type=bind,source=<approved_scratch>/<run_id>,target=/scratch
  --mount type=bind,source=<host_mcp_cfg>/<run_id>.json,target=/etc/lybra/mcp.json,readonly
  --env ANTHROPIC_API_KEY                 # passthrough from tool env; value NOT in argv
  -e LYBRA_SCRATCH_HOST_DIR=<approved_scratch>/<run_id>
  -e NO_PROXY=127.0.0.1,localhost,::1,<gate-ip>
  --workdir /projection <image>
  claude -p "<prompt>" --mcp-config /etc/lybra/mcp.json --allowedTools "mcp__lybra__*"
```

Never mounted: `5_tasks/**`, `.lybra/**`, product repo, host fs. No docker.sock,
no `--privileged`, no `--pid=host`, no root escalation. Truth writes fail because
those paths are simply not mounted.

## Scratch path mapping (aligns with 196a)

Host `<approved_scratch>/<run_id>` bind-mounts to container `/scratch` (same
bytes). The agent passes the HOST path to `queue_return` via the injected
`LYBRA_SCRATCH_HOST_DIR` env; the gate reads it on the host, validates it inside
`LYBRA_APPROVED_SCRATCH_ROOT`, and ingests into
`workspace_artifacts/<task>/<return_id>/`. 196a unchanged.

## Projection

Built from `build_context_pack_preview` (writes_enabled:false) into a host temp
`/projection`, v0-minimal (task card + declared-input summary). After rendering,
the projection dir is scanned against the real values of ALL THREE role tokens in
`connection.json` plus the LLM key; any hit aborts. Excludes
`.lybra/local/connection.json`, records, other tasks' queue, product repo.

## Mandated hardening (Owner review)

- Token-file cleanup: `--rm` removes only the container, NOT the host
  `<run_id>.json`. The file lives in a controlled temp dir (not scratch, not
  projection), mode 0600; teardown explicitly host-unlinks it; the worker report
  verifies removal.
- `ANTHROPIC_API_KEY` and the Bearer token never enter image/projection/scratch/
  records/git/log; report shows `secret_fingerprint` only. The key is passed by
  docker env passthrough (`--env NAME`), so its value is not in argv either.

## Test / adversarial matrix

Unit (no docker, CI):
- argv contains no-new-privileges / cap-drop ALL / read-only / pull never /
  pids-limit; contains NO docker.sock / privileged / pid=host / root.
- mounts: projection ro, scratch the only writable bind and == approved-root
  subdir, no truth/.lybra/product/host bind sources (no bind source inside repo).
- credentials: argv carries no raw key (passthrough form); projection + report
  contain none of the three role tokens nor the LLM key; report shows fingerprints.
- scratch mapping: `LYBRA_SCRATCH_HOST_DIR` inside approved root; mcp.json Bearer
  equals executor token; mcp config file is 0600 and outside scratch/projection.
- dry-run report has all fields; teardown marked planned; no secret files written.

Real adversarial (runbook, cc glm): write 5_tasks/queue, 5_tasks/records,
.lybra/local, product repo, host fs -> all fail; executor token -> audit tools
SCOPE_DENIED; only successful truth write = MCP queue_claim/queue_return; scratch
becomes durable only after Owner-confirmed return ingestion; projection/records/
report/git carry no raw token / LLM key; container reaches gate+LLM and gate is
non-public; `docker ps -a` confirms teardown; host token file removed.

## Red lines

Ephemeral one-shot, explicit launch; no scheduler/polling/heartbeat/daemon/
auto-restart; no egress allowlist/DLP (deferred); no finalize writer / planner
autonomy; no Layer 3 change; no 196a change; no AIPOS-101 MVP change; no history
rewrite.

## Progress

- [x] Step 0 inventory
- [x] micro-plan
- [x] confined_worker.py
- [x] tests/test_confined_worker.py (17 tests)
- [x] confined_worker_runbook.md
- [x] probe_confined_worker.sh
- [x] docker-free unit tests PASS (17 new; sandbox_runtime 23; full tools/ suite 355)
- [ ] hand to cc glm adversarial audit (next)
