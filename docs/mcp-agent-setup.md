# MCP Gate Setup

Lybra's gate is a local-first MCP server started by the Owner. Connecting to it does not claim work,
return work, launch a worker, dispatch audit, or finalize anything.

## Who talks to the gate

| Party | Gate access |
|---|---|
| **Owner** | Starts the gate (`lybra serve start`), signs envelopes (`lybra envelope mint --confirm`), issues enrollment codes. |
| **Advisor** | Drives every card through product commands — `lybra loop --task-id <card>` derives and runs claim / return ingest / audit dispatch / verdict ingest / finalize / close with the advisor's own credential. The advisor never hand-writes gate calls. Progress of a running (or backgrounded) loop is read with `lybra loop status [--task-id <card>]` on the governance-root machine (over ssh is fine) — never by tailing or grepping raw logs. After a card closes, the next card to drive is the first line of `lybra next` (project scan: the highest-priority pending card whose `depends_on` are all satisfied by gate-born records, declared in `schema/card.schema.json` `dependency_gate`). In a multi-repo project, `lybra next` / `lybra brief` / `lybra loop status` / `lybra needs-owner` take the same `--lane <repo name>` filter (declared once in `schema/verbs.schema.json` `lane_view`); repeat it for a sub-project that spans several repos (`--lane <repo a> --lane <repo b>`); without it, `brief` and `needs-owner` group their output by lane. |
| **Executor / auditor workstations** | **None.** A workstation is opened with `/go` (or launched by the advisor's `lybra loop` when the Owner's envelope authorizes it with `--launch-harness`), commits on the card branch and writes its report to the project's declared location; the advisor's `lybra loop` picks the artifact up. Workstations hold no claim / return / confirm scope. |

`lybra loop` keeps one run record per invocation under the project's declared `paths.loop_runs_root` (default
`5_tasks/records/loop_runs/<card>/`): steps, the harness process it launched (pid / pgid / workstation), when that process last
produced output and of what kind (tool name / assistant turn / end — never the text or tool arguments), and why the run ended
(declared in `schema/verbs.schema.json` `lybra_loop.run_record`). The loop's human-readable output is also written to a log next to
the record; its path is printed on the loop's first line and by `lybra loop status`. `lybra loop status` reports running / stalled /
launch_dead / loop_dead / ended from that record plus a local liveness probe; the stall threshold is declared, not chosen by the
caller. Run records are runtime observation, not part of a card's governance landing scope.

Credentials are never typed or pasted: the advisor and each workstation redeem a one-time enrollment
code (`lybra roles enroll --code …`), which writes a local `.lybra/connection.json` (`0600`). The
per-project steps are printed by `lybra onboarding guide <project>`.

The advisor session may be Claude Code (default) or Codex. `lybra onboarding guide <project> --advisor-harness codex
--advisor-host <session host>` prints the advisor's enrollment as `lybra roles enroll … --harness codex [--harness-dir <dir>]
[--harness-host <host>]`: the advisor credential lands in the governance root's `.lybra/` (run on the governance-root machine;
a Codex session on another machine runs it over ssh), `.lybra/role` records `harness: {kind: codex, dir, host}` as given (a
directory on another host is not checked locally), and no `.pi` wiring or `.claude/skills` delivery happens. A Codex session on
another machine gets the advisor charter by pull (Lybra never pushes): at session start it runs
`ssh <governance-root host> 'cd <governance root> && lybra charter --role advisor --instance <instance>'`, which prints the
rendered charter (same renderer as `lybra sync`; no credentials in the output). `lybra sync` and step 5 of the guide print
that command; the host is read from project.json `workstations.<instance>.gate_ssh_alias` (`lybra project set-workstation`).
Legal `--harness`
values are the keys of `schema/distribution.schema.json` `harness_semantics.kinds`; an unknown value is refused with that
list before the code is redeemed. Registering a Codex advisor does not extend the Owner-confirm surface (see
`docs/v1_disclosure.md` row 14).

## Several advisors on one project

One governance root may be driven by more than one advisor instance — for example a main advisor and an OTA advisor that
only pushes cards of the OTA product repo. Each advisor gets its own credential and its own envelope; nothing is shared or
overwritten:

1. **Enroll each advisor with its own instance.** The Owner issues one code per advisor, always with `--instance`
   (`lybra roles … enroll-code --role <advisor role> --instance <prefix>.<project>.<host>`); a code for an advisor- or
   planner-class role without `--instance` is refused before anything is written. Each advisor redeems its code on the
   governance-root machine (`lybra roles enroll --code … --workspace <governance root> --verify`). The governance root's
   `.lybra/connection.json` then holds one credential per instance (re-enrolling one instance retires only that instance's old
   credential), and `.lybra/role` keeps one record per instance under `instances.<instance>` (declared in
   `schema/config.schema.json` `configuration_sources.role.schema.instances`).
2. **Sign one envelope per advisor, limited to its lane.** `lybra envelope mint --confirm --policy-id <id> --agent-or-role
   <advisor instance> --task-mode code --lane-repo <repo name> …` — `--lane-repo` takes a repo name from the project's
   `project.json` `repos.items` (the path of `code_repo` when the project declares no repos list); a value that does not resolve
   is refused. Such an envelope only covers cards whose `lane.repo` resolves to that repo; a card of another lane is refused with
   `ENVELOPE_SELECTOR_LANE_REPO_MISMATCH` (`schema/transitions.schema.json` `envelope_guards.selector_lane_repo_mismatch`). An
   envelope without `--lane-repo` covers every lane, as before.

   **Sub-projects that span several repos.** Write the advisor's whole repo set: repeat the flag
   (`--lane-repo <repo a> --lane-repo <repo b> …`; values are not split on commas). A repo shared by two sub-projects may appear
   in both advisors' sets — each advisor can then drive that repo's cards, and merges into the same repo are queued automatically
   (AIPOS-F135). Every name in the set must resolve, otherwise the mint is refused naming that repo. The envelope stores the set as
   a list; an envelope minted earlier with a single repo keeps working unchanged (read as a one-repo set). A card is covered when
   its `lane.repo` resolves to any repo in the set; a refusal lists the whole set. Read progress for the same set with
   `--lane <repo a> --lane <repo b>` on `lybra next` / `lybra brief` / `lybra loop status` / `lybra needs-owner`.
3. **Drive with an explicit actor.** `lybra loop --task-id <card> --actor <advisor instance>`. The loop uses only that
   instance's own credential for every gate call (it never falls back to another advisor's credential or to the first one in the
   file), and refuses to start when the instance has no credential in the governance root. With two or more advisor instances
   enrolled, running `lybra loop` without `--actor` is refused (the driver cannot be guessed).

A project with a single advisor needs none of this: `--actor` stays optional and existing envelopes keep working unchanged.

## Start the gate

```bash
lybra serve --workspace-root <gate workspace> start   # Board (7117) + MCP gate (7118), foreground
lybra serve status                                      # redacted status
```

MCP clients that need a raw connection use `http://127.0.0.1:7118/mcp` with HTTP Bearer auth;
`lybra mcp-config` prints a redacted client configuration.

## Diagnostics

```bash
lybra mcp doctor
lybra mcp doctor --json
```

The doctor command prints only redacted SHA-256 fingerprints. It never prints raw token values.

- `SCOPE_DENIED`: the connection is authenticated, but its role lacks the operation (expected for
  workstations on every gate verb).
- `STALE_DRY_RUN`: the dry-run token is unknown to this gate process or expired; re-run the step
  (`lybra loop` re-derives it).
- `INCOMPATIBLE_DRY_RUN`: the token belongs to a different operation or surface.
- `OWNER_CONFIRMATION_REQUIRED`: the operation is Owner-only (for example signing an envelope); the
  Owner runs it with the Owner credential.

## Executors never pull work

Executors do not look for, claim or return work themselves (the earlier agent-side pull connector was
removed in AIPOS-F103). Workstations are opened with `/go` or launched by an authorized `lybra loop`;
claiming is done by the advisor's `lybra loop` under an Owner-signed envelope.
