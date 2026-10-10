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
4. **Each advisor reads its own charter.** `lybra charter --role advisor --instance <advisor instance>` (run in the
   governance root, or over ssh from the session's machine) renders the charter from that instance's own record in
   `.lybra/role` (`instances.<instance>`: its role, harness kind and host), so the advisor enrolled first is not refused after a
   second one enrolls. An instance that has no record there is refused with the list of instances recorded in that governance
   root. Without `--instance` the command keeps using the most recently enrolled instance, and workstations (one record, no
   `instances`) behave as before. Every reader of `.lybra/role` that needs a given instance's record goes through one function
   (`tools/aipos_cli/enroll_client.py` `read_role_record`, next to the writer).
5. **Read the views the same way `lybra next` does.** `lybra next` (project scan), `lybra brief`, `lybra loop status` and
   `lybra needs-owner` show the same set of cards (one entry point, `tools/aipos_cli/machine_zone.py` `visible_cards`, declared in
   `schema/verbs.schema.json` `lane_view.visible_cards`):
   - Cards frozen as history (`lybra project freeze-legacy`) are left out by default; a summary line gives how many were left
     out. `--include-frozen` lists them too, read-only and marked `[frozen]`. If the freeze list cannot be read, nothing is
     hidden and the summary line names the error.
   - With `--lane <repo>` (repeatable), only cards whose lane resolves to one of the given repos are listed. Cards whose lane
     cannot be resolved are not mixed in: they are counted in a separate group at the end ("未归 lane N 张", with their card IDs).
     Give such a card a `lane.repo` from `project.json` `repos.items`, or freeze it. Without `--lane`, output is grouped by
     lane as before, and the unresolved group is still listed.
   - `lybra loop status --task-id <card>` names one card explicitly, so its run is shown even when the card is frozen (marked
     `[frozen]`).

6. **Run commands in the project's governance root, or name it.** When you run `lybra` commands — over ssh included — `cd` to
   the project's governance root (the directory holding `project.json` and the queue) first, or pass
   `--workspace-root <governance root>`. The flag works both before the subcommand (`lybra --workspace-root <root> needs-owner`)
   and after it (`lybra needs-owner --workspace-root <root>`) with the same meaning; giving both with different roots is refused
   (`GOVERNANCE_ROOT_CONFLICT`). Without the flag the governance root containing the current directory is used. Outside any
   governance root and without the flag, the command is refused (`GOVERNANCE_ROOT_UNRESOLVED`, naming both ways out) — it never
   falls back to the home's active project, so one advisor cannot silently read another project's queue. `lybra next`,
   `lybra needs-owner`, `lybra brief`, `lybra loop status`, `lybra state lint` and `lybra queue` print the resolved project and
   where it came from as their first line (on stderr with `--json`, so stdout stays pure JSON). Read and write commands share one
   resolver (`tools/aipos_cli/workspace_config.py` `resolve_governance_root`; order, labels and the list of commands that take
   the subcommand-level flag are declared once in `schema/verbs.schema.json` `governance_root_resolution`).

A project with a single advisor needs none of this: `--actor` stays optional and existing envelopes keep working unchanged.

## Execution mode (who does the work)

By default the advisor hands execution work (code, configuration, …) to a sub-agent of its own session (Claude Code or
Codex); an Owner-approved list of task modes may instead go to a pi executor workstation, and audits are always done by an
independent pi auditor workstation (AIPOS-F143).

1. **Enroll the sub-agent executor.** The advisor issues an executor code with `--instance <exec prefix>.<project>.<advisor
   session host>`; it is redeemed into the governance root:
   `lybra roles enroll --code … --workspace <governance root> --executor-mode subagent --harness <claude-code|codex>
   [--harness-host <advisor session host>] --verify`. The credential lands in the governance root's `.lybra/connection.json`
   (no `.lybra/role` change, no `.pi`, no workstation); the land event records `mode=subagent harness=<kind>`. Without
   `--executor-mode subagent`, executor and auditor credentials are still refused in a governance root. The mode names and
   rules are declared in `schema/roles.schema.json` `executor_modes`; `lybra onboarding guide <project> --executor-mode subagent`
   prints these steps.
2. **Declare the execution mode.** `lybra project set-execution --subagent-executor <instance> --auditor <pi auditor>
   [--pi-executor <pi executor> --pi-allowed-task-mode <task_mode> …]` (preview by default, `--confirm` writes
   `project.json` `execution`; declared in `schema/config.schema.json`). Every instance must already be enrolled in the
   project, and the auditor and pi executor must have a workstation. Without this section nothing changes.
3. **Draft and publish.** With the section declared, `lybra draft create` fills a missing `assigned_to` / `harness` /
   `audit_by` from it (a card goes to the pi executor only when the draft asks for `harness: pi` and its task mode is in the
   approved list). `lybra draft publish` refuses a card whose `assigned_to` or auditor is not enrolled in the project (or, for
   `harness: pi` and auditors, has no workstation) and lists the enrolled instances.
4. **The sub-agent reads its charter and works from the card path.** On the governance-root host,
   `lybra charter --role executor --instance <sub-agent instance>` prints the executor charter for the session kind recorded at
   enrollment (`executor-charter-claude-code` / `executor-charter-codex` in `schema/distribution.schema.json`). It is rendered
   from the same single master `agents/roles/executor/AGENTS.md` as the pi charter; the master marks the few lines that
   differ by enrollment mode, and the renderer keeps only the lines for this instance's mode (pi workstations still start
   with `/go`; a sub-agent is handed the card path by the advisor, reads the card, writes its report and stops). Identity
   comes from the land event (role / mode / harness, `enrollment.instance_enrollment`); nothing is written for the sub-agent.
   While `lybra loop` waits for the sub-agent's report it does not try to start anything, and prints the declared hint
   (`schema/verbs.schema.json` `lybra_loop.launch.manual_hint.subagent`): hand the card to a sub-agent, with the card
   material from `lybra my-tasks --actor <instance> --task-id <card>`. `lybra project check-workstation` and
   `my-tasks --workstation` / `--remote-workstation` point a sub-agent instance to the same command instead.

### Card and Return rules every project follows (AIPOS-F148)

- **Multi-repo projects must name the repo.** When `project.json` declares two or more repos (`repos.items`), `lybra draft
  publish` refuses a card whose draft has no `lane.repo` (`LANE_REPO_REQUIRED`, the refusal lists the repo names); it no
  longer falls back to `repos.default`. Single-repo projects are unchanged.
- **Where the Return summary lives is declared.** A Return counts as handed in once its one-line summary can be read from
  the declared sources (`schema/transitions.schema.json` `artifact_ingest.return.summary_source`; default: the
  `一句话结论` section, exactly as before). A project whose Returns carry the summary elsewhere declares it with
  `lybra project set-return-summary [--frontmatter-key result_summary] [--section-marker <marker>]` (preview by default,
  `--confirm` writes `project.json` `return_summary_source`). If a Return has its required frontmatter filled but none of
  the sources yields a summary, `lybra loop` stops with the sources it checked and how to fix it instead of waiting.
- **Gate authorization needs a full instance name.** Audit verdicts and rework rounds are accepted only for an
  `agent_instance` that matches the instance-name template (`<prefix>.<project>.<host>`); a bare role name such as
  `auditor` is refused (`INSTANCE_NOT_CANONICAL`).

`lybra loop` starts its sub-commands with the same Python interpreter and code tree it runs from, so it does not need
`lybra` on `PATH` (for example when started over ssh); a sub-command that fails is reported with its error text.

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
