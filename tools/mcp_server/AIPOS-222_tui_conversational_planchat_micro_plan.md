# AIPOS-222 — TUI conversational plan-chat (NL chat → consent → draft) + inline thinking + CJK — micro-plan

**status: draft**
**authority: NONE** (DRAFT for Owner 复核; no code change until "批准实现")

Builds on AIPOS-221's infra (async worker, `/commands`, OptionList autocomplete, conversation scroll,
`/proceed`-no-publish — all cc-glm-passed). Reworks the **interaction model** to what the Owner
actually wants: a real conversation first, then a card **only on the Owner's consent**. Finalized
**together with AIPOS-221** (221 held).

## What changes (the interaction model)
**Today (221):** every NL line → immediately a task-card draft on screen. **Wrong.**
**222 (target, codex/claude-code feel):**
1. Owner types NL → Lybra **answers in natural language** (conversation), with an **inline "thinking"
   indicator** under the turn while it works.
2. At the end of the answer, Lybra **offers**: "Generate a project-init draft from this? — `/draft`
   (or reply 'yes')."
3. **Only on consent** → Lybra generates the conformant task card on screen (using the accumulated
   conversation as context).
4. Then the existing `/proceed` (land + publish **dry-run**, Owner confirms OOB) flow.
Alternative (kept): `/draft` at any time turns the prior discussion into a card.

## Owner rulings folded in
- **copilot gets a read-only `chat()` method** (Owner-approved). Accountability invariants welded —
  see red lines. cc glm extra-audits that chat is **zero-write / zero-confirm**.
- **221 held**, finalized with 222.

## ★ Red lines (welded — must NOT regress)
- **copilot.py changes ONLY add a read-only conversational `chat()`** — it uses the **same read-only
  gate read-tools** as `draft()` (rehydrate truth, send to LLM, record chat turn in memory) and
  **writes no file, calls no write/confirm/publish tool, holds no owner token**. `scopes: []` stays.
  No change to `draft_task_card` accountability, `_conformance`, the DRAFT→Owner→gate path, ★A1,
  canonical-only/fail-closed identity, or zero-dep.
- **TUI never publishes/confirms.** `/proceed` still lands + stages a publish **dry-run** only; confirm
  stays Owner OOB; the TUI holds no owner token.
- **Dependency isolation unchanged:** only `app.py` imports textual; `copilot.py` stays textual-free
  and (for the LLM call) sync, invoked off-loop by the TUI worker. **No new third-party dependency.**

## Scope / files
- `tools/lybra_tui/copilot.py` — **add `chat(intent) -> ChatReply`** (read-only: rehydrate truth →
  LLM with a conversational system prompt → record the turn in `CopilotMemory` (truth=False) → return
  the NL answer). Pure data; zero file write; no write/confirm tool. `draft_task_card` reused for the
  consent step (it already pulls `memory.l3_chat` context). No accountability change.
- `tools/lybra_tui/app.py` — conversational flow: NL → `chat()` worker → render the NL answer + the
  "generate draft? `/draft`/yes" offer; on consent (`/draft` or an affirmative reply) → `draft_task_card`
  worker → render the card + `/proceed` guidance. **Inline thinking indicator** (codex style) replaces
  the separate spinner.
- `tools/lybra_tui/state.py` — only if a tiny conversational-substate flag is needed (no accountability change).
- Tests: `test_tui_app.py` (conversational routing + consent→card + thinking indicator + 221 red lines
  intact); `test_copilot.py` (chat is read-only / zero-write / records memory / no write tool import).
- Docs: README / runbook note the conversational flow + CJK terminal requirement (claims ⊆ disclosure).

## Session / context management (Owner-raised — required with conversational chat)
Conversational chat accumulates turns, so the LLM egress (truth snapshot + chat) would grow unbounded
without management. **Reuse the existing primitive — `CopilotMemory.compact(keep_last=N)`** — which is
already R6-disciplined: it **trims L3 chat ONLY and never touches L0 truth / L1 index**, and chat
turns are `truth=False`. So compaction is accountability-safe by construction (no truth is ever lost
or rewritten; RF-5 re-reads truth from the gate before any draft regardless of chat state).
- **Auto-compact:** after each chat turn, if `len(memory.l3_chat) > THRESHOLD` (e.g. keep_last≈20),
  call `memory.compact(keep_last=THRESHOLD)`. Bounds context size + LLM egress.
- **Surface it (codex/claude-code style):** a subtle system line in the conversation when a compaction
  happens ("· earlier turns compacted to keep context small") so the Owner knows context was trimmed —
  never silent.
- **No accountability impact:** truth (L0) is never compacted; the card-generation step
  (`draft_task_card`) re-reads truth via read-tools (RF-5) and uses the retained recent chat as
  context. cc glm audits that compact only ever assigns `l3_chat` (truth untouched).
- **Future (NOT v1.0, note only):** an LLM-summary "compact" (digest of trimmed turns into a memo)
  is the disclosed-deferred LLM-digest item — keep the simple keep-last trim for v1.0.

## Inline "thinking" indicator (#3, the codex/claude-code line)
While a worker runs, show an **inline line directly under the Owner's NL turn** in the conversation
area: `· <verb>… (<n>s · still thinking)` — an animated verb + elapsed seconds, updated via Textual
`set_interval` (~1s tick), cleared/replaced when the answer arrives. (Effort/"medium effort" wording
optional — Owner ruling.) Implemented in app.py with Textual built-ins (a `Static` line + a timer),
no third-party, no event-loop block (the LLM call is still in the `@work(thread=True)` worker).

## Input history recall (↑/↓) — Owner-raised; codex/claude-code parity
Textual's `Input` has **no** shell-style history by default → implement it (pure client-UX, app.py +
Textual built-ins, accountability-neutral):
- Maintain an in-memory **submitted-input history** list (NL turns + `/commands`); on submit, append.
- **↑ / ↓** recall previous / next entries into the box when the input is focused **and the `/`
  autocomplete dropdown is NOT open** (when the dropdown IS open, ↑/↓ navigate the dropdown — clear
  precedence rule). A draft-in-progress line is preserved when stepping past the newest entry.
- **Scope:** session-local (in-memory; no persistence to disk in v1.0 — keeps it client-only, no truth
  write, no secret-bearing history file). A persisted history file is a possible later nicety, noted
  not built.
- Test: after several submits, ↑ recalls the last input into the box; ↑/↓ cycle; with the autocomplete
  open, ↑/↓ go to the dropdown (no conflict).

## CJK (#1) — resolution per ruling ④ (diagnosed: not Lybra/Textual)
- **Locale clean** (`LANG=en_US.UTF-8`, stdin utf-8); the Pilot test proves Textual's `Input` accepts
  CJK + spaces; the chat `Input` has no `restrict`/numeric `type`. So the blocker is **IME→terminal
  delivery**, not Lybra.
- **Isolation step (Owner):** *paste* Chinese into the box vs IME-type it. Paste works + type fails →
  confirmed terminal IME (ruling ④ → **prove a CJK-capable terminal types Chinese** + document the
  requirement). Paste *also* fails → app/textual driver issue → investigate Textual's input driver.
- v1.0 outcome: **document the terminal requirement honestly** (a CJK-capable terminal/IME — Windows
  Terminal / iTerm2 / Terminal.app), with the proof that a standard terminal works; **never silently
  "no CJK".** If the paste-diagnostic shows an app-side cause, fix it in app.py (still client-only).

## Verification
1. **Structural zero-regression (automated):** `ACCEPTANCE: PASS`; 206/207/218 anchors + README↔
   disclosure guard unchanged; full suite on BARE / SYSTEM / TUI lanes green. **New copilot test:**
   `chat()` writes no file (workspace hash unchanged), imports no write helper, records a memory turn,
   and the session stays `role="copilot"` / `scopes: []`. **Session mgmt:** a test that after many
   chat turns `l3_chat` is bounded to `keep_last` and **L0 truth is byte-identical before/after compact**
   (truth never trimmed). **221 red lines intact:** `/proceed` still never publishes; only app.py
   imports textual; copilot has no new write/confirm path.
2. **Owner O3 re-verify (the real bar):** NL → **NL answer** (not an instant card); inline thinking
   indicator under the turn; the "generate draft?" offer; consent → card; `/proceed` says "not
   published"; CJK per the diagnostic.

## Sequencing / non-goals
this DRAFT → Owner 复核 → approve → implement → cc glm audit (focus: copilot `chat()` is read-only/
zero-write/zero-confirm; 221 red lines intact; conversational flow; inline thinking; CJK honest) →
Owner O3 re-verify → **finalize 221+222 together** → re-pack tarball → macOS Track-2 → publish.
NOT here: any accountability/gate/identity/scope change; auto-publish from TUI; new third-party deps;
Form A Wall; AIPOS-206b / R2 / R5; npm publish.

## Open items for Owner ruling
- The consent trigger: `/draft` only, or also a bare affirmative reply ("yes"/"是")? (Recommend both:
  `/draft` always works; an affirmative reply right after an offer also triggers it.)
- Thinking-line wording: include an "effort" hint (codex-style) or just "· thinking… (Ns)"?
- CJK: if the paste-diagnostic confirms terminal-IME, is documenting the terminal requirement (with a
  proven-good terminal) acceptable for v1.0?
