# MCP Gate Setup

Lybra's gate is a local-first MCP server started by the Owner. Connecting to it does not claim work,
return work, launch a worker, dispatch audit, or finalize anything.

## Who talks to the gate

| Party | Gate access |
|---|---|
| **Owner** | Starts the gate (`lybra serve start`), signs envelopes (`lybra envelope mint --confirm`, or on the Board's envelope page `/owner/envelopes` from an Owner login — AIPOS-F172), enrolls the advisor (`lybra roles enroll-deliver`). |
| **Advisor** | Drives every card through product commands — `lybra loop --task-id <card>` derives and runs claim / return ingest / audit dispatch / verdict ingest / finalize / close with the advisor's own credential. The advisor never hand-writes gate calls. Progress of a running (or backgrounded) loop is read with `lybra loop status [--task-id <card>]` on the governance-root machine (over ssh is fine) — never by tailing or grepping raw logs. After a card closes, the next card to drive is the first line of `lybra next` (project scan: the highest-priority pending card whose `depends_on` are all satisfied by gate-born records, declared in `schema/card.schema.json` `dependency_gate`). In a multi-repo project, `lybra next` / `lybra brief` / `lybra loop status` / `lybra needs-owner` take the same `--lane <repo name>` filter (declared once in `schema/verbs.schema.json` `lane_view`); repeat it for a sub-project that spans several repos (`--lane <repo a> --lane <repo b>`); without it, `brief` and `needs-owner` group their output by lane. |
| **Executor / auditor workstations** | **None.** A workstation is opened with `/go` (or launched by the advisor's `lybra loop` when the Owner's envelope authorizes it with `--launch-harness`), commits on the card branch and writes its report to the project's declared location; the advisor's `lybra loop` picks the artifact up. Workstations hold no claim / return / confirm scope. |

**Owner decisions (AIPOS-F168).** When `lybra loop status` says `owner_needed`, write down what the Owner must decide with `lybra loop note --task-id <card> --ask <question> --options <key=label> … --recommend <advice> --confirm` (preview without `--confirm`). The Owner sees every pending decision of every registered project in one place — `lybra owner inbox` (or the board page `/owner/inbox`, read-only) — and rules with `lybra owner decide --item <OI-…> --choice <key> --relayed-by <advisor instance|owner> --owner-words <the Owner's words> [--note <text>] --confirm`, which writes an ordinary `owner_decision_record` (recording who relayed the ruling and the Owner's words) and lands it. When the Owner rules verbally, the advisor runs that command with `--relayed-by <its own instance>` (AIPOS-F170). `lybra owner inbox --stale-before <date>` lists old items per project with suggested batch rulings (`--items <OI-…,OI-…>`); projects whose `project.json` says `status: paused` (`lybra project set-meta --status paused`) or `status: test` (test / probe projects, AIPOS-F181; also hidden on the board pages until the page switch "show test projects") are hidden unless `--include-paused`. The advisor does not wait for a relayed message: the next `lybra loop status --task-id <card>` shows `advisor_fix(owner_ruled)` with the ruling and the resume command.

**Asking for an envelope (AIPOS-F180).** When the loop stops with `no_envelope` or `envelope_renew`, the inbox already carries a suggested authorization (driver, the card's task mode and repo, quota counted in business cards (AIPOS-F192: one card counts once when claimed; its return, audit, rework, merge and close are not counted again, so the suggestion is the card's own need of 1 unless project.json grant_request.max_tasks says otherwise), expiry, card title as the reason) and the Owner signs it on the board with one click ("Sign as suggested" on `/owner/inbox` or `/owner/envelopes`). If the suggestion is not what the card needs, write it down: `lybra loop note --task-id <card> --grant-mode <task_mode> --grant-repos <repo,…> --grant-max-tasks <N> --grant-expires <YYYY-MM-DD> --grant-reason <one sentence> --confirm` (any subset; `--ask` is optional then). Once the envelope is signed, the request disappears from the inbox and your next loop run continues.

`lybra loop` keeps one run record per invocation under the project's declared `paths.loop_runs_root` (default
`5_tasks/records/loop_runs/<card>/`): steps, the harness process it launched (pid / pgid / workstation), when that process last
produced output and of what kind (tool name / assistant turn / end — never the text or tool arguments), and why the run ended
(declared in `schema/verbs.schema.json` `lybra_loop.run_record`). The loop's human-readable output is also written to a log next to
the record; its path is printed on the loop's first line and by `lybra loop status`. `lybra loop status` reports running / stalled /
launch_dead / loop_dead / ended from that record plus a local liveness probe; the stall threshold is declared, not chosen by the
caller. Run records are runtime observation, not part of a card's governance landing scope.

Credentials are never typed or pasted, and enrollment codes are not handed around either: on the governance-root machine,
`lybra roles enroll-deliver --role <role> --instance <instance> --governance-root <governance root> --workspace <workstation or
governance root> …` issues a one-time code and redeems it in the same process (the code is never printed, written to a file or put
on any command line; with `--ssh <target>` it reaches the remote enrollment over ssh stdin only). The redemption writes a local
`.lybra/connection.json` (`0600`). Like every `lybra roles` write command (`enroll-code`, `enroll-deliver`, `register`,
`remove`), it previews by default — role, instance, governance root, expiry, target and the covering envelope, no code issued,
nothing written — and only `--confirm` executes (declared in `schema/verbs.schema.json` `two_phase_protocol.roles_writers`).
Before any code is issued or redeemed, a workstation-class enrollment is checked for an active PreAuthorized envelope covering
the instance or role; without one it is refused with the `lybra envelope mint` exit, the code is not consumed and no credential
changes (`ENROLL_ENVELOPE_MISSING`). The per-project steps are printed by `lybra onboarding guide <project>`.

The advisor session may be Claude Code (default) or Codex. `lybra onboarding guide <project> --advisor-harness codex
--advisor-host <session host>` prints the advisor's enrollment as `lybra roles enroll-deliver … --harness codex [--harness-dir <dir>]
[--harness-host <host>]`: the advisor credential lands in the governance root's `.lybra/` (run on the governance-root machine,
also when the Codex session is on another machine), `.lybra/role` records `harness: {kind: codex, dir, host}` as given (a
directory on another host is not checked locally), and no `.pi` wiring or `.claude/skills` delivery happens. A Codex session on
another machine gets the advisor charter by pull (Lybra never pushes): at session start it runs
`ssh <governance-root host> 'cd <governance root> && lybra charter --role advisor --instance <instance>'`, which prints the
rendered charter (same renderer as `lybra sync`; no credentials in the output). `lybra sync` and step 5 of the guide print
that command; the host is read from project.json `workstations.<instance>.gate_ssh_alias` (`lybra project set-workstation`).
The printed pull commands can be copied and run as they are (AIPOS-F244): the `lybra` inside the ssh command is the absolute
path of the stable `lybra` entry on the governance-root machine (the entry installed by `tools/lybra-deploy install-entry`, which
survives deploys; never a path under `.deploy/releases/<version>`), because a non-login ssh command does not load `~/.profile`
and so has no `~/.local/bin` on its `PATH`. While the host is undeclared the command carries the `<治理根主机>` placeholder and the
same line prints the declaration command to run on the governance-root machine, with this machine's hostname as the candidate
alias; `lybra roles redeclare` / `enroll-deliver` print the same hint once when they register a session on another host.

A Claude Code advisor session may also run on another machine (AIPOS-F243; for example Claude Code on a Mac that has the
test devices attached over USB): `lybra onboarding guide <project> --advisor-host <session host> --advisor-dir <session
directory on that host>` enrolls it with `--harness claude-code --harness-dir <dir> --harness-host <host>`; `.lybra/role`
records `harness: {kind: claude-code, dir, host}` (the directory is not checked locally) and nothing lands on the
governance-root machine. `lybra sync` then prints two pull lines with the relative landing paths, run from the session
directory at session start: the charter, fetched with `lybra charter … --body-only` over ssh and written to
`.claude/rules/lybra-advisor.md` (via a `.tmp` file and a rename), and the skills, `ssh <governance-root host> 'cd <governance
root> && lybra skills --role advisor --instance <instance>' | tar -xf -`, which unpacks `.claude/skills/<skill>/…`. Both use
the same renderer and the same manifest builder as a local `lybra sync`, so the landed files are byte-identical to a local
delivery. `lybra skills` writes a tar stream and refuses to write it to a terminal.
Legal `--harness`
values are the keys of `schema/distribution.schema.json` `harness_semantics.kinds`; an unknown value is refused with that
list before any code is issued. Registering a Codex advisor does not extend the Owner-confirm surface (see
`docs/v1_disclosure.md` row 14).

## Several advisors on one project

One governance root may be driven by more than one advisor instance — for example a main advisor and an OTA advisor that
only pushes cards of the OTA product repo. Each advisor gets its own credential and its own envelope; nothing is shared or
overwritten:

**Session directories (AIPOS-F253).** An advisor session directory decides where its charter lands
(`.claude/rules/lybra-advisor.md` for Claude Code, `AGENTS.md` for Codex) and, for Claude Code, which auto-memory it uses, so two
advisors on the same host must not share one. The default for a session on the governance-root machine is the project's
governance root itself (as resolved for the command: the guide uses `--home-root > LYBRA_HOME_ROOT > ~/.lybra/config.json
home_root > default` plus the project name; `roles redeclare` / enroll use the governance root they run against — no path is
hard-coded). When another advisor on that host is already registered at the governance root, the default is a per-instance
sub-path inside it, declared once in `schema/distribution.schema.json` `harness_semantics.session_dir.secondary_subpath`
(`.advisor-sessions/<instance>`); the product creates it on `--confirm` together with a `.gitignore` that hides the whole
directory, so rendered charters and skills there never enter governance landing, and `lybra` run from it still resolves the
governance root upward. An explicit `--dir` / `--harness-dir` / `--advisor-dir` always wins, but every entry point (`roles
redeclare`, `roles enroll` / `enroll-deliver`, `onboarding guide`, and `lybra sync` before rendering) refuses a directory another
advisor on the same host already registered when the two would collide (same charter landing, or both Claude Code memory) —
`SESSION_DIR_TAKEN`, naming the occupying instance and printing the same command with another directory. Sessions on different
hosts never collide; a Claude Code and a Codex session in one directory do not collide either. `lybra roles list` prints every
advisor's `harness@host:dir` and marks existing collisions.

**Default by tier (AIPOS-F254).** The default is not first-come-first-served: the governance root belongs to the project-level
advisor. Tier is judged once (`enrollment.session_tier`, declared in `harness_semantics.session_dir.tiers`) from the registered
role name only: a built-in role (`advisor`, `planner` — the role is its own class) is the project-level advisor; a custom role
that resolves to the advisor class (`advisor-lantu`, `advisor-ota`, …) is a sub-project advisor. A sub-project advisor's default
is always its declared sub-path, even when the governance root is free. A project-level advisor's default is the governance root;
a second project-level advisor on the same host still gets its sub-path (F253 behaviour). If the root is held by a sub-project
advisor, the product does not silently fall back to a sub-path: `roles redeclare` / `roles enroll` / `enroll-deliver` / `onboarding
guide` refuse with `SESSION_DIR_TAKEN`, name the occupant, and print the swap: first `lybra roles redeclare --workspace-root <root>
--instance <occupant> --actor <occupant> --dir <root>/.advisor-sessions/<occupant>` (directory-only change, second tier; preview,
then `--confirm`), then the original command again. `lybra roles list` tags each advisor with its tier and flags a sub-project
advisor at the governance root, or a project-level advisor kept out of it by one, each with the swap commands; any other
non-default directory is only labelled user-specified, never a violation. `lybra sync --harness-root <governance root>`
renders the charter and skills for every advisor session registered on this host — the top-level instance of `.lybra/role` and
each other instance slot whose session directory is local — each into its own directory with its own credential and its own
local manifest key (`.version-<role>@<instance>` for slots), so no advisor needs `lybra charter --body-only` / `lybra skills` by
hand.

**Session settings (AIPOS-F255).** Claude Code loads `.claude/rules` and CLAUDE.md files from every directory above the session
directory, and keeps auto memory per git repository (all subdirectories and worktrees of one repo share one `MEMORY.md`), so a
sub-path advisor would also load the project-level advisor's charter and every advisor under one governance repo would share one
memory. `lybra sync` therefore renders, into each local Claude Code session directory, the settings file declared in
`schema/distribution.schema.json` `harness_semantics.session_settings` (`.claude/settings.local.json`) with two official keys:
`claudeMdExcludes` lists the absolute path of every Lybra charter landing in every ancestor directory (the session's own charter
and ordinary CLAUDE.md / AGENTS.md files are not excluded), and `autoMemoryDirectory` points at the instance's own memory directory
(`memory.directory`, `~/.lybra/advisor-memory/<project>/<instance>`). A new session (no local manifest yet) is isolated by default;
an existing session is never switched silently (its old memory would become unreachable): `lybra roles list` shows, per local
Claude Code session, any ancestor charter it would still load and whom it shares memory with, and `lybra roles memory
--workspace-root <root> --instance <instance> --mode isolated|shared` previews the switch (target directory, current sharers,
advice for the old memory, which is never moved or deleted) and writes only with `--confirm`. A `.claude/.gitignore` lists exactly
the rendered files (charter, skills, the settings file, itself), so they disappear from `git status` while files you create under
`.claude/` stay visible; a settings or ignore file you wrote yourself is never overwritten — sync prints the keys/lines to merge.
Sessions on another host are not covered (their files live on that host).

1. **Enroll each advisor with its own instance.** The Owner enrolls each advisor on the governance-root machine, always with
   `--instance` (`lybra roles … enroll-deliver --role <advisor role> --instance <prefix>.<project>.<host> --governance-root
   <governance root> --workspace <governance root> …`, preview first, then `--confirm`); an advisor- or planner-class role
   without `--instance` is refused before anything is written. The governance root's
   `.lybra/connection.json` then holds one credential per instance (re-enrolling one instance retires only that instance's old
   credential), and `.lybra/role` keeps one record per instance under `instances.<instance>` (declared in
   `schema/config.schema.json` `configuration_sources.role.schema.instances`).
   **Project standing grant first (AIPOS-F224 / F232).** A project normally needs a single Owner signature instead of per-advisor
   envelopes: `lybra project grant sign --workspace-root <governance root> --grant-lane <repo>:<mode>[,<mode>…]:<advisor instance>
   --host <host> [--launch-harness pi] --max-tasks <total cards> --expires-at <ISO>` (no `--confirm` = local preview, zero writes;
   `--confirm --connection-json <Owner connection.json>` signs it through the gate). `lybra project grant show` lists it with usage
   and notices; `grant extend` widens it, `grant revoke` withdraws it, `grant draft --from-envelopes` merges in-use envelopes into one
   draft. Inside the grant `lybra loop` is covered with no envelope; at 80% of the total or 3 days before expiry `lybra loop status`,
   `lybra brief` and the Owner inbox each show one notice line with the `grant extend` command (not blocking); out of bounds = the
   loop stops with `authorization_out_of_bound` and the inbox offers "extend the project grant". A brand-new project with neither a
   grant nor an envelope directory is told to sign a grant (not "envelope directory missing"). Envelopes below keep working for
   projects that have not migrated.
2. **Sign one envelope per advisor, limited to its lane.** `lybra envelope mint --confirm --policy-id <id> --agent-or-role
   <advisor instance> --task-mode code --lane-repo <repo name> …` — `--lane-repo` takes a repo name from the project's
   `project.json` `repos.items` (the path of `code_repo` when the project declares no repos list); a value that does not resolve
   is refused. Such an envelope only covers cards whose `lane.repo` resolves to that repo; a card of another lane is refused with
   `ENVELOPE_SELECTOR_LANE_REPO_MISMATCH` (`schema/transitions.schema.json` `envelope_guards.selector_lane_repo_mismatch`). An
   envelope without `--lane-repo` covers every lane, as before. A successful `--confirm` also commits and pushes the records the
   gate wrote for it (AIPOS-F158); if that landing fails the envelope is still in force, and the command prints the
   `lybra governance-commit --paths …` line to re-run instead of signing again.

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
   Commands an advisor signs by hand name the signer the same way: a rework round after a FAIL verdict is
   `lybra queue rework --task-id <card> --actor <advisor instance> --verdict-ref <verdict record id> --focus-items '["…"]'
   --acceptance-criteria '["…"]' --confirm` (without `--confirm` it only previews the round it would append and
   pre-checks the signer — nothing is written; AIPOS-F155), and `lybra queue adopt` takes `--actor` likewise. The command signs with that
   instance's own credential and sends it to the gate as `agent_instance`; an instance with no credential in the governance
   root is refused (the refusal lists the enrolled instances), and it never falls back to the first advisor credential in the
   file. Other commands that pick a credential by role refuse when two or more instances hold that role, unless the instance
   is known (`lybra loop --actor <instance>`, or `LYBRA_DRIVER_INSTANCE=<instance>` when typing a command by hand).
   An advisor that issues enrollment codes names itself as the signer too: `lybra roles … enroll-deliver … --actor <advisor
   instance>` and `lybra roles … enroll-code … --actor <advisor instance>` take that instance's own credential (AIPOS-F157);
   without `--actor` they pick by `--token-role` (default advisor, then owner) and refuse when two or more instances hold that
   role. `lybra audit-verdict` (non-code cards) takes the credential of `--agent-instance` (or `--actor`) the same way.
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

1. **Enroll the sub-agent executor.** The advisor enrolls it into the governance root with `--instance <exec
   prefix>.<project>.<advisor session host>`:
   `lybra roles enroll-deliver --role executor --instance … --governance-root <governance root> --workspace <governance root>
   --executor-mode subagent --harness <claude-code|codex> [--harness-host <advisor session host>] …` (preview, then `--confirm`). The credential lands in the governance root's `.lybra/connection.json`
   (no `.lybra/role` change, no `.pi`, no workstation); the land event records `mode=subagent harness=<kind>`. Without
   `--executor-mode subagent`, executor and auditor credentials are still refused in a governance root. The mode names and
   rules are declared in `schema/roles.schema.json` `executor_modes`; `lybra onboarding guide <project> --executor-mode subagent`
   prints these steps.
2. **Declare the execution mode.** `lybra project set-execution --subagent-executor <instance> --auditor <pi auditor>
   [--pi-executor <pi executor> --pi-allowed-task-mode <task_mode> …]` (preview by default, `--confirm` writes
   `project.json` `execution` and commits and pushes it through the governance commit path, commit identity `--actor`;
   declared in `schema/config.schema.json`). Every instance must already be enrolled in the
   project, and the auditor and pi executor must have a workstation. Without this section nothing changes.
3. **Draft and publish.** With the section declared, `lybra draft create` fills a missing `assigned_to` / `harness` /
   `audit_by` from it (a card goes to the pi executor only when the draft asks for `harness: pi` and its task mode is in the
   approved list). `lybra draft publish` refuses a card whose `assigned_to` or auditor is not enrolled in the project (or, for
   `harness: pi` and auditors, has no workstation) and lists the enrolled instances. Pass `--actor <your advisor instance>`
   to both commands: the publish record keeps that instance as the card's owner (on a repo shared by several sub-projects,
   `lybra next --actor` and the loop's "take the next card" only offer the owner's cards, and a lane-limited advisor's loop
   refuses another advisor's card). It is required when the governance root has more than one advisor instance.
   While a sub-agent works, a loop that times out waiting for its return is routine (`lybra loop status` says `advisor_fix`):
   let the sub-agent finish, then rerun the same loop command.
4. **The sub-agent reads its charter and works from the card path.** On the governance-root host,
   `lybra charter --role executor --instance <sub-agent instance>` prints the executor charter for the session kind recorded at
   enrollment (`executor-charter-claude-code` / `executor-charter-codex` in `schema/distribution.schema.json`). It is rendered
   from the same single master `agents/roles/executor/AGENTS.md` as the pi charter; the master marks the few lines that
   differ by enrollment mode, and the renderer keeps only the lines for this instance's mode (pi workstations still start
   with `/go`; a sub-agent is handed the card path by the advisor, reads the card, writes its report and stops). Identity
   comes from the land event (role / mode / harness, `enrollment.instance_enrollment`); nothing is written for the sub-agent.
   While `lybra loop` waits for the sub-agent's report it does not try to start anything, and prints the declared hint
   (`schema/verbs.schema.json` `lybra_loop.launch.manual_hint.subagent`): hand the card to a sub-agent, with the kickoff
   brief printed by `lybra kickoff --task-id <card>` (AIPOS-F187: identity, charter command, work tree / branch / base sha,
   report location and required frontmatter, no-write list, verification requirements; the advisor may append card-specific
   notes after it). `lybra my-tasks --actor <instance> --task-id <card>` still gives the raw card material. `lybra project check-workstation` and
   `my-tasks --workstation` / `--remote-workstation` point a sub-agent instance to the same command instead.
5. **Launched pi cannot escalate privileges (AIPOS-F165).** Every pi that `lybra loop` starts (executor or auditor) runs with
   the kernel `no_new_privs` flag set just before exec, so `sudo` and other setuid programs cannot gain root. It is on by
   default; `project.json` `launch_guard` can turn it off only together with an Owner approval reference
   (`{"no_new_privs": false, "owner_approval_ref": "<decision or envelope id>"}`; without the reference the loop refuses to
   launch). Where the flag cannot be set (non-Linux gate, or a remote workstation started over ssh) the launch goes ahead and
   the session event and loop run record say so (`privilege_guard: degraded`). `lybra project check-workstation` prints the
   state (`applied` / `disabled` / `degraded`). Declared in `schema/config.schema.json` (`launch_guard`) and
   `schema/verbs.schema.json` (`lybra_loop.launch.privilege_guard`).

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

## Workstation charters are rendered by the product (AIPOS-F164)

A workstation's charter (`AGENTS.md` in the workstation root) is produced by the product: `lybra sync` renders it from the
role's charter master plus the project's declarations and records three fingerprints (master / declarations / rendered file)
in the workstation's local manifest. A hand-written charter, or one that was never synced, is *unrendered*; one whose master or
project declarations changed since the last sync is *stale*. There is one check and one fix, and every project uses the same
commands on the governance-root machine:

- `lybra project check-workstation <project> --instance <instance> --harness pi` adds a **charter freshness** line for a local
  workstation. It runs the same check as `lybra sync --dry-run` and writes nothing: up to date = ✓; unrendered / stale /
  missing / not checkable (for example the gate is unreachable) = ✗ with the fix,
  `lybra sync --harness-root <workstation> --workspace-root <governance root>` (preview it with `--dry-run` first).
- `lybra loop` checks the charter of the workstation it is about to start pi in (executor or auditor), every time. Up to date =
  it starts pi. Not up to date = it runs `lybra sync` for that workstation only, checks again and then starts pi; the run
  record (`charter_checks`) and the run log get one line each. If the sync fails, pi is not started and the run ends with
  `charter_sync_failed`; a project that sets `"charter_auto_sync": false` in `project.json` gets no automatic sync and the
  run ends with `charter_not_fresh` instead. For both, `lybra loop status` says `advisor_fix`: run the sync command from the
  message, then rerun the same loop command (the same reason repeating on the same card escalates to `owner_needed`).
- Remote workstations (on another machine) are not checked from the gate machine; run `lybra sync --dry-run` there.

## Moving a workstation out of a shared checkout

A pi workstation (auditor or executor) that sits inside a git checkout shared with other projects is exposed to every
checkout / stash / pull anyone runs there. `lybra project relocate-workstation` moves it to a location of its own; any project
uses the same command, run on the governance-root machine (an advisor on another machine runs it over ssh). Only an instance
enrolled in this project can be moved; any other instance is refused with nothing written (`RELOCATE_NOT_THIS_PROJECT`).

The workstation location has one declaration: the instance's latest live location event in the project's enrollment log. A
move appends one `relocate` location event (same log, same parser as `land`), so `lybra loop` launches, `lybra project
check-workstation`, `lybra roles enroll-where` and `lybra charter` all read the new location; there is no second path table.

1. Preview (default, nothing written): `lybra project relocate-workstation <project> --instance <instance> --to <new dir>`
   lists the workstation state (credential files by path, mode and size only — never their content), the old and new
   location, the declarations and links affected, and the in-flight checks: an unfinished launch in the project's loop run
   records whose process is still alive in that workstation, a live loop waiting on that instance's artifact, or any local
   process whose working directory is inside the workstation. Without `--to` it prints the suggested location (declared in
   `schema/enums.schema.json` `workstation_transport.relocation.suggested_target`).
2. `--confirm --actor <committer>` is refused when anything is in flight, when the new location is inside any git checkout,
   or when it already exists and is not empty. Otherwise it moves the whole workstation (credentials move with the directory,
   permissions kept), runs `lybra sync` on the new location and requires a steady dry-run (empty plan and prune; the charter is
   re-rendered for the new path), appends the location event, re-reads it, leaves a read-only pointer file in the old directory
   (the directory is kept for rollback) and lands the enrollment log through the governance commit point. A failed sync moves the
   workstation back and leaves the declaration unchanged.
3. `--rollback` (preview, then `--confirm`) moves it back to the location before the latest move and appends a rollback event.

The shared parent's `_distributed/` and `_shared/` stay where they are for the other workstations; the new parent gets its own
from the sync. Files of the moved workstation that the shared checkout tracked show up there as deleted; the command never runs
git in that checkout.

## Changing an enrolled instance's registration (harness / host / working directory)

An advisor that moves to another harness (for example from Codex to Claude Code, on the governance-root machine or on
another machine), another host or another working directory keeps its instance name and credentials; only the registration
changes. Switching between Codex and Claude Code on the same host and directory works both ways, one `redeclare` per switch;
a change of kind does not carry the old session host and directory over, so give `--host` and `--dir` explicitly. Only one
session holds the instance at a time; the new session pulls its charter and skills at start (see the advisor enrollment
section above), and running loops are separate processes on the governance-root machine, unaffected by the switch.
`lybra roles redeclare` is the entry for that; no void-and-re-enroll and no new code.

1. Preview (default, nothing written): `lybra roles redeclare --workspace-root <governance root> --instance <instance>
   [--harness <kind>] [--host <host>] [--dir <working directory>] --actor <instance>` prints the current registration, the
   old → new values, the authorization tier and the location event (or Owner request) it would append.
2. `--confirm` appends one `redeclare` location event to the project's enrollment log (same family and parser as `land` and
   `relocate`; append-only, earlier events kept), updates the instance slot in the governance root's `.lybra/role`, and lands
   the log. The log is the registration truth: when the slot and the log disagree, readers use the log and `lybra state lint`
   reports `ENROLLMENT_REGISTRATION_DRIFT`.
3. Authorization: a change of working directory only, or a new host / harness listed in the project's standing authorization
   (`hosts` / `launch_harnesses`), can be done by the instance itself or a project advisor; the event records
   `authorized_by=<authorization>@r<revision>`. Anything else goes to the Owner inbox (`enrollment_redeclare`, reason
   `beyond_authority`) on `--confirm`; after the Owner approves, rerun the same command with `--confirm`.

Sub-agent cards follow the advisor that drafts or dispatches them: the default `harness` of a sub-agent card is that advisor's
current registration (the shared sub-agent executor's enrollment is only the fallback). When a published card's `harness`
differs from its advisor's current registration, `lybra next` does not derive the claim and `lybra kickoff` refuses with
`KICKOFF_HARNESS_MISMATCH`; both print the `lybra queue amend` command to fix the card (`--restricted` for a claimed card).
`lybra project workstation-plan` and the board workstation page show each advisor's registered harness, host and source event.

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
