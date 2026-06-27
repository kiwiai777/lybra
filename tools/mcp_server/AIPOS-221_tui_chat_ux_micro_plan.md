# AIPOS-221 — TUI plan-chat UX redo (codex/claude-code parity) — IMPLEMENTATION micro-plan

**status: draft**
**authority: NONE** (DRAFT for Owner 复核; no code change until "批准实现")

A **pure client-UX redo** of the Lybra TUI plan-chat surface to match the codex / claude-code
plan-chat feel — natural-language-first input, `/`-command palette with autocomplete, async "working"
feedback, and explicit next-step guidance — **with the only deviations being welded read-only +
`/command` routing**. Closes F-rg-tui-chat, F-rg-tui-proceed, F-rg-tui-cjk, F-rg-tui-shifttab. Not a
reinvention: the target is the same hand-feel users already know from codex/claude-code.

## ★ Red lines (welded — must NOT regress; verified in §Verification)
- **Pure client-UX layer.** Accountability logic is untouched, not one line: copilot stays read-only
  (`scopes: []`), the loop is zero-file-write, truth path is **DRAFT → Owner → gate publish**, ★A1
  holds (executor/copilot cannot `*_confirm`/`draft_publish`), identity is **canonical-only +
  fail-closed** (AIPOS-219), the gate core stays **zero-dep**.
- **Confirm stays Owner out-of-band.** The TUI/copilot never holds the owner token and cannot publish
  or confirm; `/proceed` only **lands a draft + stages a publish dry-run** — it never publishes.
- **No new third-party dependency.** Only the already-present **Textual**; autocomplete, workers,
  scrolling, spinner all use **Textual built-ins**. Dependency isolation unchanged: **only `app.py`
  imports textual**; `copilot.py` / `state.py` / gate stay textual-free and (for copilot) sync +
  unchanged.

## Scope / files
- `tools/lybra_tui/app.py` — the entire redo lives here (sole textual importer).
- `tools/lybra_tui/state.py` — minor: a `set_mode(name)` helper for `/mode` (keep `toggle_mode`); no
  accountability change.
- **NOT changed:** `copilot.py` (the LLM call is wrapped in a Textual thread worker — `copilot` stays
  sync and accountability-identical), `presentation.py`, gate, scopes, identity, records.
- Tests: `tools/lybra_tui/tests/test_tui_app.py` (extend — see §Verification); the structural guards
  (206/207/218 + ACCEPTANCE) must stay green unchanged.

## The 7 must-haves → implementation
1. **NL-first input (F-rg-tui-chat).** Bottom `Input` is the chat box. On submit: if the value starts
   with `/` → command; otherwise the whole line (spaces + CJK intact) is the **task intent** sent to
   the copilot. No more `draft <text>` prefix. The conversation scroll area shows the user's NL turn,
   the copilot's response (the rendered card / needs-bundle / blocking reasons), and system messages —
   codex/claude-code style message stream.
2. **Command autocomplete (Textual built-in).** Typing `/` surfaces a live-filtered command menu: an
   `OptionList` overlay (mounted above the input) filtered on the current `/token`, navigable with
   ↑/↓ and Enter to insert; plus `Input(suggester=SuggestFromList([...]))` for inline ghost-text
   completion (→/Tab to accept). Both are Textual core — no `textual-autocomplete` package.
3. **`/help`.** Renders every command with a one-line description (table in the conversation area).
4. **Async "working" feedback (the blocking bug).** Today `copilot.draft_task_card` →
   `LLMClient.complete` runs **synchronously on the event loop** → the UI freezes ("打完回车没反应").
   Fix: run it in a **Textual thread worker** (`@work(thread=True)` / `run_worker`); on submit, append
   the user turn + show a **spinner** (Textual `LoadingIndicator`, or a `set_interval` animated
   Static) immediately; the worker calls the sync copilot off-loop and posts the result back via
   `self.call_from_thread(...)` / a custom `Message`. Event loop never blocks; the spinner clears when
   the result (or error) arrives. `copilot.py` unchanged (sync), just invoked off-loop.
5. **Next-step guidance (F-rg-tui-proceed).** Every step ends with an explicit "what happened + what's
   next" system line: after a draft → "✓ conformant — review the card, then `/proceed` to land it +
   stage the publish dry-run (the Owner confirms out of band)"; after `/proceed` → "✓ Landed
   `5_tasks/drafts/…`. Publish **dry-run staged** — **NOT published yet**; confirm out of band with
   the owner token to publish."; after needs-bundle → the existing-bundles hint + `/proceed
   bundle=<ref>`. No state is ever ambiguous about whether truth changed.
6. **CJK input (F-rg-tui-cjk).** Ensure the `Input` accepts CJK / wide chars (no `restrict`/`type`
   filter that drops them) and the conversation renderer accounts for East-Asian width (Textual/Rich
   measure wide chars natively). Add an explicit CJK acceptance check; if the root cause is the host
   terminal/IME rather than Textual, document it in the runbook + disclosure (don't fake a pass).
7. **No Shift+Tab dependency (F-rg-tui-shifttab).** Mode switching is available via `/mode
   [observe|confirm|copilot]` (and the existing Shift+Tab kept as a convenience); launching with an
   LLM config still starts in copilot mode, so the NL box is usable immediately with no chord.

## Proposed `/command` set (cc proposal for §Owner ruling)
| command | does |
|---|---|
| `/help` | list all commands + descriptions |
| `/proceed [bundle=<ref>]` | land the pending card under `5_tasks/drafts/` + stage a publish **dry-run** (never publishes; Owner confirms OOB) |
| `/queue` | observe the queue summary (read-only gate read-tool) |
| `/validate` | run the validator (read-only) |
| `/confirm` | list pending confirm gates (read-only view; actual confirm is OOB) |
| `/mode [observe\|confirm\|copilot]` | switch mode explicitly (replaces Shift+Tab reliance) |
| `/audit [task_id]` | audit retrospection — show records / L3 authority verdict for a task (read-only) |
| `/clear` | clear the conversation scroll area |
| `/quit` | quit (also `q` / Ctrl+C) |
Default (no leading `/`) = natural-language intent → copilot draft.

## Textual implementation path (built-ins only)
- **Layout:** `Header` · `Static#banner` (.brand) · `VerticalScroll#conversation` (1fr; mounts
  message widgets — `Static`/`Markdown` bubbles for user / copilot / system) · `Static#status`
  (mode/scopes) · `LoadingIndicator#working` (hidden unless a worker runs) · `Input#cmd` (bottom) ·
  `Footer`.
- **Autocomplete:** `OptionList` overlay filtered on the `/token`; `Input.suggester =
  SuggestFromList([...])`. Key handling via `on_key` / `on_input_changed`.
- **Worker:** `@work(thread=True, exclusive=True, group="copilot")` wrapping the sync copilot call;
  post results via a custom `Message` or `call_from_thread`. Spinner toggled around the worker.
- **Messages:** a small append helper that mounts a styled `Static`/`Markdown` into `#conversation`
  and scrolls to end (codex/claude-code transcript feel).

## Verification
1. **Structural invariants — ZERO regression (automated):**
   - `python -m tools.acceptance.v1_acceptance` → `ACCEPTANCE: PASS` (★A1 / zero-write / RF-5 /
     presentation / zero-dep + correctness probe unchanged).
   - 206 copilot ★A1 + zero-write + RF-5, 207 scope reachability, 218 frontmatter/identity guards →
     all green, unchanged. Full suite (bare + system + tui lanes) green.
   - `test_tui_app.py` extended: the `run_tui → build_app` smoke still passes; NEW tests — NL submit
     routes to copilot (worker mocked), `/`-prefixed routes to the command handler, `/proceed` lands a
     draft + stages a dry-run but performs **no publish** (assert no publish record / nothing in
     `queue/pending`), and the copilot session is still constructed with `role="copilot"` / `scopes: []`
     (read-only welded). Worker path covered without real network (mock the LLM completer).
2. **Manual O3 (Owner, on the redone TUI) — UX must GENUINELY meet the bar (not mechanical):** NL
   input (type a sentence, no prefix) → card; `/` shows autocomplete; `/help` lists commands; Enter
   shows an immediate working indicator (no freeze); each step prints clear next-step guidance;
   `/proceed` clearly states "not published — confirm OOB"; **CJK input works** (or is documented if
   terminal-bound).

## Sequencing / non-goals
this DRAFT → Owner 复核 → approve → implement → cc glm audit (focus: red lines not regressed; 7
must-haves covered; LLM call truly async/off-loop; `/proceed` never publishes) → Owner O3 re-verify →
macOS Track-2 → publish.
NOT here: any accountability/gate/identity change; new third-party deps; auto-publish from the TUI;
Form A Wall; AIPOS-206b / R2 / R5; npm publish.

## Open items for Owner ruling
- The `/command` set above (add/remove/rename any).
- Autocomplete style: OptionList overlay **and** inline ghost-suggester, or just one.
- `/audit` scope for v1.0 (full records view vs a compact L3-verdict line).
- CJK: if the blocker is the host terminal/IME (not Textual), is documenting it acceptable for v1.0
  (vs. holding the slice)?
