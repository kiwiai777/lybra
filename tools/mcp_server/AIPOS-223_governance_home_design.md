---
id: AIPOS-223
title: Governance Home — truth-root layout refactor + project dimension
status: draft
authority: NONE
date: 2026-06-27
supersedes: none
red_lines:
  - controlled-execute / AIPOS-197/199/204 semantics frozen
  - "★A1 scope split (executor/copilot cannot owner_confirm / draft_publish)"
  - canonical-only fail-closed identity; executor != auditor independence
  - zero-dep (stdlib-only gate core; only lybra_tui/app.py may import textual)
---

# AIPOS-223 — Governance Home (design DRAFT, authority: NONE)

> This is a design DRAFT for the Owner's ritual review. Nothing here is implemented.
> It does not edit code. Sections marked **OPEN ITEM** are reserved for Owner ruling.

## 0. Thesis and scope boundary

**Governance truth must survive destruction of the project code repo, for one project
and for many.** Today the truth root is conflated with a single project: governance docs
are hardcoded under `2_projects/lybra/` (`board_adapter.py:76-80`,
`external_intake_writer.py:135`) and `5_tasks/` is a single flat layer directly under the
truth root with no project dimension (`records.py:234`, `draft_validator.py`,
`orchestration_event_writer.py`, `external_intake_writer.py:12`, `acceptance/v1_acceptance.py`).

This slice refactors **only** two things:

1. The **truth-root layout** — move truth into a home (`~/.lybra/projects`) separate from
   any code repo, and give `5_tasks/` + governance docs + stage archive a **per-project**
   dimension.
2. The **project dimension on the gate** — a project axis layered on top of the existing
   operation scopes, so a token authorized for project A cannot act on project B.

Explicitly **out of scope**: any change to controlled-execute, ★A1, identity fail-closed,
or the addition of any third-party dependency. See §"Red lines preserved".

---

## 1. Governance home layout

### 1.1 Current state (verified)

- Truth root is resolved by `tools/aipos_cli/workspace_config.py:resolve_workspace_root()`
  and `tools/aipos_cli/task_loader.py:find_repo_root()`. The marker for "this is a truth
  root" is `5_tasks/queue` (`workspace_config.py:16 has_workspace_queue`).
- `5_tasks/{queue,records,drafts,orchestration}` lives **directly** under that root.
- Governance docs are hardcoded per-the-single-project: `board_adapter.py:76-80`
  `GOVERNANCE_FILES = {decision_log: 2_projects/lybra/decision_log.md, ...}`, surfaced by
  `get_governance()` (`board_adapter.py:467-502`) which hardcodes `"project": "lybra"`.
- `external_intake_writer.py:134-135 _external_project_exists()` checks
  `repo_root / "2_projects" / client_tag`.
- There is no `stage_archive` concept in the resolver today (it is referenced only by the
  finalize/archive memory ritual, not by gate code).

### 1.2 Proposed home model

Introduce a **home root** (the survivable truth container) that holds one subtree per
**project**. The home root defaults to `~/.lybra/projects` and is overridable. A single
project's subtree is what `resolve_workspace_root()` returns as today's "workspace root" —
so the entire downstream code path (records, drafts, orchestration, queue) is unchanged
*relative to the resolved project root*. The refactor is: **what gets resolved** becomes
`<home>/<project>` instead of a repo-embedded directory, and the governance docs +
stage_archive move **inside** that per-project root instead of `2_projects/<name>/`.

Concretely:

- `5_tasks/{queue,records,drafts,orchestration}` stay exactly where they are *relative to
  the project root* — no module that references `5_tasks/...` needs to change its relative
  path. They simply now sit under `<home>/<project>/` rather than a repo dir.
- Governance docs move from `2_projects/<name>/{decision_log,project_status,roadmap}.md`
  to `<home>/<project>/governance/{decision_log*,project_status.md,roadmap.md}` (see §6
  for the decision_log file-vs-directory OPEN ITEM).
- `stage_archive/` becomes a first-class per-project directory under the project root.

This makes "destroy the code repo" lossless: the home root is a wholly separate path tree.

### 1.3 Touch-points to remove the hardcoded project path

- `board_adapter.py:76-80` `GOVERNANCE_FILES` — replace the literal `2_projects/lybra/...`
  map with paths resolved from the **active project root** + a `governance/` subdir. The
  map keys (`decision_log`, `project_status`, `roadmap`) stay; the values become
  project-relative (`governance/decision_log.md` etc., or a directory for decision_log per §6).
- `board_adapter.py:467-502` `get_governance()` — stop hardcoding `"project": "lybra"` /
  `"project_root": "2_projects/lybra"`; report the active project and its resolved root.
- `external_intake_writer.py:134-135 _external_project_exists()` — stop probing
  `2_projects/<client_tag>`; a project "exists" iff `<home>/<client_tag>/5_tasks/queue`
  exists (the same `has_workspace_queue` marker, lifted to the home).

### 1.4 Resolution: home root + active project → concrete paths

New resolver inputs (precedence, highest first):

1. `--workspace-root` / `--home-root` explicit flag (back-compat: `--workspace-root` keeps
   meaning "the resolved project root" so existing invocations that point straight at a
   project subtree still work).
2. `LYBRA_HOME_ROOT` env (new) for the home; `AIPOS_WORKSPACE_ROOT` (existing) keeps
   meaning "resolved project root" for full back-compat.
3. `.lybra/config.json` — extend `default_workspace_config()` (`workspace_config.py:83-95`)
   with a `home_root` and an `active_project` field (see §1.5 schema).
4. Upward search for the `5_tasks/queue` marker (back-compat: a bare project subtree).

Active **project** selection (highest first):
`--project` flag → `LYBRA_ACTIVE_PROJECT` env → `.lybra/config.json active_project` →
single-project fallback (if exactly one project subtree exists under home, use it;
if zero or many, fail closed with a teaching error).

See §"Resolution algorithm" for the full spec including fail-closed cases.

### 1.5 Proposed config schema additions (back-compat, additive)

`default_workspace_config()` gains (all optional; absence = today's behavior):

```json
{
  "config_version": 2,
  "home_root": "~/.lybra/projects",
  "active_project": "lybra",
  "workspace_root": ".",
  "projects": {
    "lybra":        { "code_repo": "/home/kiwi/lybra" },
    "ai-project-os":{ "code_repo": "/home/kiwi/ai-project-os" }
  }
}
```

`config_version` bumps to `2`; a `config_version: 1` file (no `home_root`) resolves exactly
as today (project root == the dir containing `5_tasks/queue`). The `projects.<name>.code_repo`
mapping is the subject of §2.

---

## 2. Project ↔ code-repo relationship (★key separation)

### 2.1 The two roots

- **Project (truth) root**: `<home>/<project>/` — decision log, task records, drafts,
  stage archive, queue. Survivable.
- **Code repo**: where the executor does the actual engineering work. Ephemeral /
  reconstructable. Today this is conflated with the truth root; here they are distinct.

### 2.2 How a project registers its code-repo path

Proposal: a per-project mapping stored in the **home-level** config, NOT in the code repo
(`<home>/<project>/project.json`, or the `projects` map in §1.5). Schema:

```json
{ "project": "lybra", "code_repo": "/home/kiwi/lybra", "registered_at": "...", "registered_by_token_ref": "svc-owner" }
```

- **Who writes it / under what authority**: writing `project.json` is *governance scaffold
  creation* and therefore an **Owner-authority** operation (consistent with the existing
  ruling that the executor must never build truth structure — see §4). The executor never
  writes this file; it only *reads* the mapping to know where its code repo is.
- It lives in the home so it survives code-repo destruction, and so re-registration after a
  repo move is one Owner edit.

### 2.3 The 196a ingestion boundary crossing (read against current code)

`artifact_ingest.py` today copies **from** an Owner-approved scratch root **into**
`workspace_artifacts/<task_id>/<return_id>/` under `repo_root` (`WORKSPACE_ARTIFACT_ROOT`,
line 22; `plan_scratch_ingestion` line 74; `perform_scratch_ingestion` line 212). Crucially,
**ingestion already crosses a boundary**: scratch (outside truth) → `workspace_artifacts/`
(inside truth). It refuses any source under truth prefixes `("5_tasks", ".lybra")`
(`_TRUTH_PREFIXES`, line 25) and any symlink/`..` escape.

The home refactor changes the *destination* repo_root from "the code repo" to "the project
truth root". The design crossing:

- `workspace_artifacts/` becomes a per-project directory **under the project truth root**
  (`<home>/<project>/workspace_artifacts/...`). This is correct: ingested artifacts are
  truth and must survive code-repo loss.
- The executor's *produced* files live in the **code repo** (or the approved scratch root).
  The scratch root is already supplied out-of-band via `LYBRA_APPROVED_SCRATCH_ROOT`
  (`artifact_ingest.py:23`) and is independent of `repo_root` — so the existing approved-root
  guard already models "code-side output → home truth" without modification.
- `_TRUTH_PREFIXES` must additionally guard the **home root** itself (so a scratch root can
  never be inside `<home>`), and `_within_truth` (line 67) should be evaluated against the
  project truth root, which it already is once `repo_root` == project root. **No relaxation**
  of the existing guards; one prefix-set extension to cover the home.
- `artifact_refs` recorded in truth stay project-truth-relative (e.g.
  `workspace_artifacts/<task_id>/...`), exactly as today, so records remain self-contained
  in the home and contain **no** code-repo absolute paths. This is the explicit
  "code-repo output → home truth" crossing: the only path that leaves the code repo is the
  ephemeral scratch source, which is content-hashed and copied, never referenced.

**Net**: the 196a contract is preserved; the only change is that its `repo_root` is now the
project truth root, and the truth-prefix guard set grows to include the home. No new
operation class, no new dependency.

---

## 3. Gate serves home root + per-project scope dimension

### 3.1 What exists today

- The capability token is built in `http_sse.py:136-145` from a service-role registry
  entry: `{token_ref, role, operations, expires_at, fingerprint, source}`. **There is no
  project field.** It is minted by `tools/aipos_cli/service_mode.py:_role_token_entry()` (line 224)
  from `ROLE_SPECS` (line 31), which carry `role`, `token_ref`, `scopes` — no project.
- Scope enforcement is purely operation-based: `tools.py:_capability_has_scope()` (line 192)
  checks `operations` contains the op + token_ref present + not expired. ★A1 lives here:
  executor/copilot tokens simply lack `owner_confirm`/`draft_publish` in `operations`.
- A `projects` field *already exists* in payload-level capability_scope for intake /
  owner_decision (`external_intake_writer.py:101-106`, `owner_decision_writer.py:202-206`),
  but it is **not** a gate-token dimension and is not enforced on read/claim tools.

### 3.2 Proposed project dimension (new axis, orthogonal to operation scopes)

Add a `projects` list to the capability token — the set of project names the token may act
on. This is **separate from** `operations` and does **not** touch ★A1: a token still cannot
`owner_confirm` unless `owner_confirm ∈ operations`; the project axis only further narrows
*which project* an already-authorized operation may touch.

Token encoding (additive to `http_sse.py:136-145`):

```json
{ "token_ref": "...", "role": "...", "operations": [...],
  "projects": ["lybra"],          // NEW axis
  "expires_at": "...", "fingerprint": "...", "source": "service_v0" }
```

### 3.3 `serve rotate` carries it

`ROLE_SPECS` (`tools/aipos_cli/service_mode.py:31`) and `_role_token_entry()` (line 224) gain
a `projects` field per minted token. v1.0 default: `serve rotate --project <name>` mints role
tokens scoped to exactly that one project. The home/connection config (`build_connection_config`,
line 235) records `workspace_root` as the **home root**; each token entry additionally
carries its `projects`. Multi-project rotation (a token spanning several projects) is a
v1.1 affordance — for v1.0, **one project per rotation** keeps the model crisp and the blast
radius minimal. `redacted_connection()` (line 288) must surface `projects` so
`serve status` shows the project axis.

### 3.4 Per-tool enforcement (fail-closed)

Add `_capability_in_project(project)` next to `_capability_has_scope()` in `tools.py`:

- Returns True iff `project` is non-empty AND `project ∈ token.projects` AND the token is
  otherwise valid (token_ref present, not expired).
- **Fail-closed**: if the request does not resolve a concrete active project, or the token's
  `projects` is absent/empty, or the requested project ∉ `projects` → a teaching
  `PROJECT_SCOPE_DENIED` error. A token authorized for project A calling a tool resolved to
  project B is denied here, *before* any operation runs.

Every tool resolves the active project (from request args `project` / connection config /
single-project fallback — see §"Resolution algorithm") and then:

- **Read tools** (`lybra_queue_list`, `lybra_validate`, `lybra_task_preview`,
  `lybra_context_pack_build`): filter to the active project's root and require
  `_capability_in_project(active)`. Read tools stay exposed-by-default for *operations*
  (READ_ONLY_NOTICE) but are now **project-fenced**.
- **Write/confirm tools** (`queue_claim`, `queue_return`, `draft_publish`, `audit_*`,
  `intake_submit`, `owner_decision_record`): the existing operation-scope gate is unchanged;
  an **additional** `_capability_in_project(active)` check is layered first. Order:
  project gate → operation-scope gate (★A1) → controlled-execute. None of the later gates
  are modified.

**Back-compat**: a `config_version: 1` connection with no `projects` on tokens, against a
single-project home, resolves the single project and treats a token with absent `projects`
as project-unset → fail-closed unless the deployment is the legacy single-project mode. The
legacy bridge: when home has exactly one project AND the token predates the project axis
(`projects` absent), treat it as scoped to that one project (a narrow, documented
back-compat path; see OPEN ITEM on whether to keep it or require re-rotation).

---

## 4. Project lifecycle (R3): who creates the scaffold

### 4.1 The tension

- R3 wants "first gated publish lazily creates the project scaffold" (ergonomic).
- Existing ruling: **the executor must NEVER build governance structure** — only
  Owner-authority operations create truth scaffold. The publish gate's *confirm* is already
  Owner-only (`draft_publish` confirm additionally requires `owner_confirm`,
  `tools.py:867-890`, ★A1). So a *gated publish confirm* is, by construction, an
  Owner-authority moment.

### 4.2 Resolution (recommended)

Allow lazy-create **only on the Owner-confirmed leg** of a gated operation, never on a
dry-run and never on an executor-held path:

- The dry-run leg (executor-reachable) **must not** create any project directory; if the
  target project root is missing, dry-run returns a teaching `PROJECT_NOT_ESTABLISHED`
  error pointing to `/project new`.
- The confirm leg of `draft_publish` (which already requires `owner_confirm`) MAY lazily
  scaffold the project root (`5_tasks/...`, `governance/`, `stage_archive/`) as part of the
  Owner-confirmed write, because that leg is provably Owner authority. This satisfies R3's
  ergonomics without violating the "executor never builds truth" ruling.
- Also provide an explicit `/project new <name>` (Owner, TUI) that scaffolds eagerly and
  writes `project.json` (§2.2). This is the clean, auditable path and the one we recommend
  as the **primary** flow; lazy-create-on-Owner-confirm is the convenience fallback.

### 4.3 OPEN ITEM (Owner ruling)

Choose: (a) **explicit `/project new` only** (most conservative — every project root has an
auditable creation moment, no implicit scaffolding); or (b) **explicit `/project new` +
lazy-create permitted on the Owner-confirmed publish leg** (recommended for ergonomics).
Both keep the executor structurally unable to create truth. Owner decides whether implicit
scaffolding (even when provably Owner-authority) is acceptable, or whether project creation
must always be a deliberate, separate act.

---

## 5. Project switch + session (R4)

### 5.1 Active project tracking

The active project is **client/session state**, not a global mutation:

- TUI `/project <name>` sets the active project on the `TuiSession` (mirrors how `/mode`
  sets `session.mode` in pure client state — `state.py:89-96 set_mode`). No scope or
  accountability change; it only re-targets subsequent read/observe/confirm calls and is
  passed as the request-level `project` argument to gate tools.
- The gate enforces that the chosen project ∈ token.projects (§3.4); switching to a project
  the token is not scoped for fails closed at the gate, not silently in the client.
- Default active project on connect: connection config `active_project`, else the
  single-project fallback.

### 5.2 Per-project copilot session/memory namespacing

The copilot already takes a `project` parameter and stamps it into its system prompt
(`copilot.py:273`, `:320 "project: {self.project}"`) and accepts an optional
`CopilotMemory`. Design:

- The plan-chat session is **bound to the active project**: switching projects starts a
  fresh copilot session bound to the new project (a new `Copilot(..., project=<new>, ...)`),
  so chat history and any `CopilotMemory` are **namespaced per project** and never bleed
  across projects.
- Copilot memory, if persisted, is keyed by project (e.g. under the project truth root,
  read-only to the copilot — the copilot has no write scope, so persistence is an
  Owner/TUI-side concern, consistent with `land_draft` being an Owner action,
  `state.py:145-152`).
- DRAFTs land per-project: `land_draft` already requires `5_tasks/drafts/` and is documented
  "(per-project, R4)" (`state.py:147-148`) — under the home model that resolves to
  `<home>/<active-project>/5_tasks/drafts/`.

v1.0 = switch + per-project session. **Cross-project planning (one session reasoning over
multiple projects) is deferred to v1.1** and is explicitly out of scope here.

---

## 6. decision_log directory-ization (R5) — OPEN ITEM, Owner decides

We are already moving the governance docs (§1.3), so this is the natural moment to consider
converting decision_log from a single `decision_log.md` to a directory. **We do not pick;
we present both.**

- **Option A — ride along now**: `governance/decision_log/DL-<id>_<slug>.md` + an
  `INDEX.md`. Trade-offs: + avoids a second migration later; + per-entry records are
  git-/audit-friendly and append-only (no whole-file rewrites); + aligns with the existing
  records pattern (`5_tasks/records/<type>/<task>/<id>.md`). − larger blast radius this
  slice (every reader/writer of decision_log + `get_governance` excerpt logic at
  `board_adapter.py:123-142` must learn the directory shape); − more to audit in one slice.
- **Option B — keep independent**: leave `governance/decision_log.md` as a single file now,
  do the directory conversion as its own later slice. Trade-offs: + smallest blast radius;
  + decouples the risky layout-refactor from a format change. − a second migration touches
  governance paths again later; − single-file decision_log keeps growing and is rewrite-heavy.

**OPEN ITEM**: Owner rules A vs B. (If A, it folds into Slice 2 below; if B, the design's
`governance/decision_log.md` single-file path stands and a separate ticket is filed.)

---

## 7. Establish vs migrate

v1.0 is a NEW product; priority is establishing the model cleanly, not heavy migration tooling.

### 7.1 Current single hardcoded workspace

`2_projects/lybra/` is the only project today, and `2_projects/` is empty in the working
tree (truth currently resolves via the live home/workspace, not committed repo dirs). This
makes establishment cheap.

### 7.2 Recommended (lightest correct path)

**One-time move into `~/.lybra/projects/lybra/`, then freeze the old shape:**

1. Establish the home: create `~/.lybra/projects/` (Owner action, via `/project new lybra`
   or `serve rotate --project lybra` scaffolding).
2. Move the existing project's truth (`5_tasks/`, governance docs) into
   `~/.lybra/projects/lybra/`, governance docs landing under `governance/` (§1.3).
3. Write `project.json` mapping `lybra → /home/kiwi/lybra` (§2.2).
4. Keep `config_version: 1` resolution working (back-compat) so any not-yet-migrated caller
   still functions; new projects only ever use the home model.

This is a **move + register**, not a migration framework. No automated multi-project
migrator is built for v1.0. New projects are born in the home; the one legacy project is
relocated once.

### 7.3 OPEN ITEM (Owner ruling)

Confirm **move-into-home** (recommended) vs **freeze-in-place + home-only-for-new** (leave
`lybra` where it is, only new projects get the home). Move-into-home is recommended because
it makes the survivability thesis true for *every* project including the flagship; freeze
leaves the flagship project still conflated with its code repo.

---

## 8. Two-root separation + home persistence (REFINEMENT — Owner-ruled 2026-06-27)

> Refined after the Slice-2 truth-layout mapping surfaced (a) the home had no durability story
> and (b) the existing governance repo `ai-project-os` is a multi-project + framework repo, so a
> single "home = its own new repo" model did not fit. The full ruling supersedes §7.3 and the
> first draft of this section.

### 8.1 Two roots — Lybra runtime state is kept OUT of the truth repo

- **`~/.lybra/` — Lybra runtime root (fixed, global; never enters a user truth repo):**
  - `~/.lybra/config.json` — `{config_version, home_root, active_project}`. No secrets; points
    at the truth home and names the active project.
  - `~/.lybra/local/connection.json` — role tokens (`0600`, fingerprint-only). Here so tokens
    are never committed into a truth repo.
- **`LYBRA_HOME_ROOT` — truth home (default `~/.lybra/projects`, overridable):** only
  project-related truth; per project `<project>/{governance/, 5_tasks/, stage_archive/,
  workspace_artifacts/, project.json}`. **No `.lybra/` inside the home.** `project.json` lives
  in the home (committable with the project truth; no secret).
- **Resolution:** home_root = `LYBRA_HOME_ROOT` env → `~/.lybra/config.json .home_root` →
  default `~/.lybra/projects`. active_project = `--project` → `LYBRA_ACTIVE_PROJECT` env →
  `~/.lybra/config.json .active_project` → single-project fallback (marker = `5_tasks/queue` AND
  `project.json`) → fail-closed.
- **Legacy v1 untouched (M1):** `AIPOS_WORKSPACE_ROOT` + in-workspace `.lybra/config.json`
  upward search stay byte-identical; the two-root model activates only on the new signals
  (`LYBRA_HOME_ROOT` env OR `~/.lybra/config.json` with `home_root`).

### 8.2 Home persistence = git-backed via one of THREE topologies

The home should be git-versioned + remote-backed, but the shape is **not single** — the product
supports three:
- **A — workspace-repo:** the whole home (`LYBRA_HOME_ROOT`) is one git repo (all projects in it).
- **B — per-project-repo:** each `<project>/` is its own git repo.
- **C — external-existing-repo:** the home lives **inside an existing repo** the user already
  manages (e.g. `LYBRA_HOME_ROOT=~/ai-project-os/2_projects`). Lybra adds no repo; the user
  commits/pushes via their existing flow. This is the **Owner dogfood** (one governance remote,
  no new repo; the gate sees only the marker-bearing project).

### 8.3 `lybra home git-init` — topology-aware, one-shot, Owner-invoked

- **★ Refuses if the target is already inside a git repo** (no nested repos) — which is exactly
  what makes topology C safe (it declines inside `ai-project-os`).
- Granularity: workspace-level (topology A) or `--project <name>` (topology B).
- One shot; transparently prints the commands; **no remote config, no push** (prints the push
  commands); no background/scheduler/auto. Owner action, not a copilot capability (the copilot's
  read-only / scopes-`[]` boundary is untouched). Docs: claims ⊆ disclosure.

### 8.4 Migration of the flagship is an Owner deploy step (reversible), not code

Code provides only the tools (scaffold, resolver, `lybra project new`, `git-init`). The actual
relocation is Owner-executed and reversible (topology C): confirm root `5_tasks` is lybra-only →
non-destructive copy of `lybra` truth into the home layout under `ai-project-os/2_projects/lybra`
→ verify the gate reads all truth from the home → only then switch `~/.lybra/config.json` + freeze
(not delete) the old flat location → commit via the normal `ai-project-os` flow with a manually
reviewed precise diff (other 8 projects + framework preserved; never force/overwrite). See the
Slice 2 micro-plan (AIPOS-226) for the exact sequence.

---

## Proposed home layout (ASCII tree)

```
~/.lybra/projects/                         # HOME ROOT (survivable; LYBRA_HOME_ROOT overridable)
├── lybra/                                   # PROJECT ROOT  (== today's "workspace_root")
│   ├── project.json                         # { project, code_repo, registered_by_token_ref }   (§2.2, Owner-authored)
│   ├── governance/
│   │   ├── decision_log.md                  # Option B   ── OR ──
│   │   ├── decision_log/                    # Option A (R5 OPEN ITEM)
│   │   │   ├── INDEX.md
│   │   │   └── DL-001_<slug>.md
│   │   ├── project_status.md
│   │   └── roadmap.md
│   ├── stage_archive/                       # per-project, first-class
│   ├── workspace_artifacts/                 # 196a ingestion destination (truth, survivable)
│   │   └── <task_id>/<return_id>/...
│   └── 5_tasks/                             # UNCHANGED relative layout
│       ├── queue/{pending,claimed,completed,blocked}
│       ├── records/{sessions,claims,publishes,returns,audit_dispatches,audit_verdicts,owner_decisions}
│       ├── drafts/{,external_intake}
│       └── orchestration/<id>/...
├── ai-project-os/                           # a second project — same shape
│   └── ...
└── .lybra/
    ├── config.json                          # config_version 2: home_root, active_project, projects{}
    └── local/connection.json                # service tokens, each carrying `projects` (§3.3)
```

Code repos live elsewhere and are referenced only by `project.json.code_repo`:
```
/home/kiwi/lybra/            (code repo — ephemeral; not truth)
/home/kiwi/ai-project-os/    (code repo)
```

---

## Resolution algorithm (spec)

```
resolve(home, project) :=

# --- HOME ROOT ---
home_root :=
  1. explicit --home-root / --workspace-root flag           (flag wins; --workspace-root
                                                              keeps legacy "= project root")
  2. LYBRA_HOME_ROOT env                                     (new)
  3. AIPOS_WORKSPACE_ROOT env                                (legacy = project root, back-compat)
  4. .lybra/config.json .home_root                          (config_version >= 2)
  5. ~/.lybra/projects                                      (default)
  6. upward search for 5_tasks/queue                         (legacy bare project subtree)

# --- ACTIVE PROJECT ---
active_project :=
  1. --project flag
  2. LYBRA_ACTIVE_PROJECT env
  3. request-arg `project` (gate tool call)
  4. .lybra/config.json .active_project
  5. single-project fallback: exactly one <home>/*/5_tasks/queue exists → that one
  6. else FAIL-CLOSED  (PROJECT_AMBIGUOUS or PROJECT_NOT_ESTABLISHED)

# --- CONCRETE PATHS ---
project_root := home_root / active_project
assert (project_root / "5_tasks" / "queue").exists()   # has_workspace_queue marker, lifted
  else FAIL-CLOSED PROJECT_NOT_ESTABLISHED (dry-run/read) ; lazy-scaffold only on Owner-confirm (§4)

queue        := project_root / 5_tasks/queue
records      := project_root / 5_tasks/records
drafts       := project_root / 5_tasks/drafts
orchestration:= project_root / 5_tasks/orchestration
governance   := project_root / governance/{decision_log[.md|/], project_status.md, roadmap.md}
stage_archive:= project_root / stage_archive
artifacts    := project_root / workspace_artifacts        # 196a destination

# --- BACK-COMPAT (config_version 1 / no home) ---
if no home model present:
    project_root := legacy resolve_workspace_root()        # dir containing 5_tasks/queue
    governance   := legacy 2_projects/<name>/... ONLY for a still-unmigrated project
```

**Fail-closed cases** (all return teaching errors, never a silent default):
- home unresolved → `HOME_NOT_RESOLVED`.
- project unresolved / zero or many candidates with no explicit selection → `PROJECT_AMBIGUOUS`.
- resolved project root missing the `5_tasks/queue` marker → `PROJECT_NOT_ESTABLISHED`.
- (gate) token not scoped for the resolved project → `PROJECT_SCOPE_DENIED` (§3.4).

---

## Project scope dimension (spec)

```
TOKEN (http_sse.py:136-145, minted by tools/aipos_cli/service_mode.py):
  { token_ref, role, operations:[...], projects:[...], expires_at, fingerprint, source }

MINT (serve rotate --project <p>):  ROLE_SPECS entry → _role_token_entry adds projects:[p]
ECHO (serve status):                redacted_connection surfaces `projects`

ENFORCE (tools.py, per call):
  active := resolve active_project (Resolution algorithm)
  if active is None:                       -> PROJECT_AMBIGUOUS (fail-closed)
  if not _capability_in_project(active):   -> PROJECT_SCOPE_DENIED (fail-closed)
        _capability_in_project(p) := p and (p in token.projects) and token valid&unexpired
  # then, UNCHANGED:
  operation-scope gate (_capability_has_scope)   # ★A1 untouched
  controlled-execute (dry_run→token→revalidate→OWNER_CONFIRMED→execute)  # untouched

ORDER:  project gate  →  operation-scope gate (★A1)  →  controlled-execute
  (The project gate only ever NARROWS. It can never grant an operation the token lacks,
   so ★A1 and the executor/auditor/owner split are structurally preserved.)

BACK-COMPAT:  single-project home + token without `projects` → treated as scoped to the
  one project (documented legacy bridge; OPEN ITEM: keep vs require re-rotation).
```

---

## Open items for Owner ruling

1. **R5 decision_log directory-ization** (§6): Option A (ride along, directory + INDEX now)
   vs Option B (single file now, separate later slice). *No recommendation forced.*
2. **R3 project creation** (§4.3): (a) explicit `/project new` only vs (b) explicit
   `/project new` + lazy-create on the Owner-confirmed publish leg. Recommendation: (b),
   but Owner decides whether any implicit scaffolding is acceptable.
3. **Migrate vs freeze** (§7.3): move flagship `lybra` into the home (recommended) vs
   freeze-in-place and home-only-for-new-projects.
4. **Legacy token bridge** (§3.4): keep the "single-project home + project-less token →
   scoped to the one project" back-compat path, or require `serve rotate` re-mint before
   any project-fenced tool works (stricter, cleaner).
5. **Assumption — config_version bump**: I assumed bumping `.lybra/config.json` to
   `config_version: 2` with additive optional fields and full v1 back-compat. Confirm the
   versioning approach.
6. **Assumption — `project.json` location**: I assumed per-project `project.json` in the
   project root (plus an optional `projects{}` mirror in home config). Confirm where the
   project→code_repo mapping should live and whether duplication is wanted.
7. **Assumption — `workspace_artifacts/` moves under the project truth root**: confirm
   ingested artifacts are truth (survivable) and belong in the home, not the code repo.

---

## Proposed implementation slicing (dependency order; design only, no code)

Each slice is independently auditable and dogfood-able. No code is written here.

- **Slice 0 — Resolution core (no behavior change yet).** Introduce home-root + active-project
  resolution in `workspace_config.py` (additive, `config_version: 2`, full v1 back-compat),
  with the §"Resolution algorithm" precedence and fail-closed errors. Dogfood: resolver unit
  tests + `find_repo_root` still returns the legacy root for a v1 config. **Depends on:** nothing.

- **Slice 1 — Governance docs de-hardcode.** Replace `board_adapter.py:76-80 GOVERNANCE_FILES`
  and `get_governance()` project hardcoding (line 467-502) with project-root-relative
  `governance/` resolution; replace `external_intake_writer.py:134-135 _external_project_exists`
  with the home `has_workspace_queue` marker. Dogfood: `get_governance` reports the active
  project + resolved root. **Depends on:** Slice 0.

- **Slice 2 — Per-project truth move + establish.** `/project new <name>` (Owner) scaffolds a
  project root and writes `project.json`; one-time move of `lybra` into the home (§7).
  (If R5 = Option A, the decision_log directory shape is built here.) Dogfood: a fresh project
  scaffolds and round-trips a draft→queue. **Depends on:** Slices 0–1.

- **Slice 3 — 196a ingestion repoint.** Point `artifact_ingest.py` `repo_root` at the project
  truth root; extend `_TRUTH_PREFIXES` to cover the home; keep all existing guards. Dogfood:
  a scratch→`workspace_artifacts/` ingestion under the home with the existing symlink/escape
  tests green. **Depends on:** Slices 0, 2.

- **Slice 4 — Project token dimension (mint + echo).** Add `projects` to `ROLE_SPECS` /
  `_role_token_entry` / `build_connection_config` / `redacted_connection`; `serve rotate
  --project`. No enforcement yet. Dogfood: `serve status` shows the project axis.
  **Depends on:** Slice 0.

- **Slice 5 — Project token enforcement (gate).** Add `_capability_in_project` + per-tool
  project gate in `tools.py`, ordered before the operation-scope gate. Read tools project-fence
  + filter; write/confirm tools add the project gate. Fail-closed `PROJECT_SCOPE_DENIED`.
  Dogfood: a token scoped to A is denied on B; ★A1 reachability tests unchanged.
  **Depends on:** Slices 0, 4.

- **Slice 6 — TUI `/project` switch + per-project copilot session.** `TuiSession` active-project
  state (mirroring `/mode`); `/project <name>`; copilot session rebinds per project; DRAFTs
  land under the active project. Dogfood: switch projects, confirm chat/memory namespacing and
  gate-side denial when switching outside token scope. **Depends on:** Slices 0, 5.

(R3 lazy-create-on-Owner-confirm, if Owner picks §4 option (b), rides in Slice 2/5; otherwise
omitted.)

---

## Red lines preserved

- **controlled-execute / AIPOS-197/199/204**: untouched. The project gate is layered *before*
  the operation-scope gate and controlled-execute; the dry_run→token→revalidate(snapshot_hash)
  →OWNER_CONFIRMED→execute sequence is unchanged. The only ingestion change is `repo_root`
  pointing at the project truth root (§2.3); guards are extended, never relaxed.
- **★A1 (executor/copilot cannot owner_confirm / draft_publish)**: untouched. The project axis
  only *narrows* which project an already-authorized operation may touch; it can never add an
  operation to `operations`. Executor/copilot tokens still structurally lack
  `owner_confirm`/`draft_publish` (`tools.py:_capability_has_scope`, `tools/aipos_cli/service_mode.py ROLE_SPECS`).
- **Canonical-only fail-closed identity; executor != auditor**: untouched. No change to
  `registry_available()` gating, canonical resolution, or auditor independence. Project gate is
  identity-orthogonal.
- **zero-dep**: every proposed change is stdlib-only (path/JSON resolution, dict fields, file
  moves). No new third-party import; only `lybra_tui/app.py` continues to touch textual. The
  acceptance third-party-import probe stays green.
- **Scope of refactor**: only the truth-root layout + the project dimension change. No new
  controlled-execute operation class, no engine/runtime, no scheduler.
```
