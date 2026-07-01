---
id: AIPOS-237
slice: G
title: CJK direct input + chat copy (v1.0 top priority — Lybra bug, not a terminal doc)
status: draft
authority: NONE
task_class: simple
phase: micro-plan DRAFT (root cause + PRECISE fix CONFIRMED via §1e; ready for Owner approval → implement)
r_reinforcements_folded:
  - "§1e precise probe FIRST — drop only REPORT_ASSOCIATED_TEXT, keep DISAMBIGUATE (preserve Shift+Enter); fall back to full kitty-disable"
  - "R1 verify honesty — CJK fix is driver-layer (no headless Pilot); assert launch path applies the fix before any textual import; copy Pilot retained"
  - "R2 newline tradeoff follows §1e (precise→Shift+Enter kept; fallback→canonical Ctrl+J, Shift+Enter loss documented); O3 verifies newline + shift+tab/escape no-regression"
  - "R3 value-stable but depends on Textual linux_driver private constant names (existence + result asserts, rename → fail-loud); macOS via linux_driver covered, Windows out of scope; O3 on the npm-installed prefix; /copy via OSC 52 needs the terminal's clipboard-write permission (iTerm2 setting)"
  - "R(2nd) anti-silent-failure: assert constant names exist pre-patch + result values post-patch; R1 launch assert = result values not 'executed'"
parent: release-gate O3 findings F-o3-12a (CJK cannot be typed) + F-o3-12b (chat cannot be copied)
symbols_read_from: main @ b625f8d (post-AIPOS-236); textual 8.2.7
---

# AIPOS-237 Slice G — CJK direct input + chat copy

> **Nature.** DRAFT only. authority: NONE. No product code, no commit. Output = this file + the
> §1 investigation + the Owner-run isolation harness (`~/cjk-isolation-test.py`) → R direction-audit
> → Owner approves the fix → implement → `cc glm` → R re-checks → **Owner O3 real-hardware confirm
> (type CJK / copy chat)** → finalize.

---

## §0 Thesis + premise (overturns AIPOS-222's wrong conclusion)

Release-gate O3 surfaced two v1.0-blocking, top-priority Lybra bugs:
- **F-o3-12a** — CJK cannot be typed directly into the TUI (the Owner must compose elsewhere + paste).
- **F-o3-12b** — chat content cannot be selected / copied.

**★ Premise (AIPOS-222 FALSIFIED):** AIPOS-222 concluded "CJK direct typing not working = terminal/
IME problem, document it." **Counter-evidence:** Claude Code types CJK directly in the *same*
terminal the Owner uses. Terminal is fine ⇒ this is a **Lybra/Textual-side bug, therefore fixable.**
This slice fixes it as a Lybra bug; it does **not** accept a "special-terminal requirement" doc.

---

## §1 Investigation FIRST (find the real culprit — evidence, not assumption)

### 1a. CJK — the Lybra app-widget layer is NOT the culprit (proven)
- **Code read.** The custom key chain does not intercept character keys: `PromptArea._on_key`
  (`app.py:158-188`) special-cases only `enter` / `ctrl+j` / `shift+enter` / `up` / `down`, then
  `await super()._on_key(event)` (TextArea's default = insert `event.character`). `App.on_key`
  (`app.py:483-501`) acts only on `up`/`down`; it never stops a character event. No `restrict` /
  `type` filter exists on the prompt.
- **Textual semantics.** `events.Key(key="中", character="中").is_printable` = **True** → TextArea's
  default handler inserts it.
- **Pilot proof (run now).** Building the real app, focusing the prompt, and posting CJK Key events
  `中文你好` → the prompt buffer becomes exactly `中文你好` (`CJK_INSERTED = True`). **So the Lybra
  widget chain accepts + inserts CJK when it arrives as printable Key events.**
- **Therefore** the remaining variable is the **terminal → Textual IME input decoding** (below Lybra).
  The Owner's isolation test decides the fix locus.

### 1b. ★ CJK isolation harness — Owner-run RESULTS (bare Textual, same terminal)
`~/cjk-isolation-test.py` — a **bare** Textual app (plain `TextArea`, NO Lybra code). Owner ran it:

| | test | result |
|---|---|---|
| **A** | type CJK directly (IME) | **NO — does not enter** |
| **B** | paste CJK | **YES — works** |
| **C** | drag-select the transcript | **NO — no highlight** |
| **D** | Ctrl+C copy (Ctrl+C is FREE here) | **NO — no copy** |

**Conclusion (evidence, overturns my earlier §1c partial guess):** A + C + D fail in **bare Textual**
— so both defects are in the **Textual↔terminal input layer, NOT Lybra's widgets** (1a already
proved Lybra's key chain inserts CJK Key events *when Textual emits them*; and the bare app with a
FREE Ctrl+C + default `ALLOW_SELECT` still cannot select/copy, so the copy defect is **not merely**
Lybra's Ctrl+C rebinding). Only **paste** (bracketed-paste → `events.Paste`) is delivered — Textual's
key-input (IME) and mouse (selection) paths are not reaching Textual in this terminal, while Claude
Code (Ink, raw stdin) works there.

### 1c. What IS fixable in Lybra despite the Textual input-layer limitation
- **COPY — fixable now (bypasses the broken mouse-select).** `App.copy_to_clipboard(text)` writes
  **OSC 52** directly to the driver (`\x1b]52;c;<base64>\a`) — **independent of mouse selection**. So
  a Lybra **`/copy [N]`** command can copy the last N chat turns to the system clipboard without any
  drag-select. (Also free `Ctrl+C` — drop `app.py:260` `ctrl+c→quit`, keep quit on `Ctrl+Q` / `/quit`
  — so it stops fighting copy, though OSC 52 does not require it.)
- **CJK typing — NOT a Lybra-widget bug (bare repro).** Direct IME typing does not reach Textual in
  this terminal; Lybra cannot synthesize Key events it never receives.

### 1d. ★ Deep-dive (Owner chose it) — diagnostic RESULTS + the real root cause
- **Environment:** iTerm2 (macOS) → SSH → remote WSL. Remote locale is UTF-8 (`LANG=en_US.UTF-8`,
  Python stdin `utf-8`) — decoding locale is NOT the cause.
- **Diagnostic results (`~/cjk-diagnostic.py`, Owner-run):** typing 中文 produces a `KEY` event with
  **`character=''` (empty)** on **both 8.2.7 AND 8.2.8** — so the 8.2.8 CSI-u-colon fix does **NOT**
  fix it (that hypothesis is **falsified**). Mouse **does** work (`MOUSE_DRAG` rows fire).
- **Root cause (grounded in Textual source):** Textual's Linux driver enables the **Kitty keyboard
  protocol** with `KITTY_REPORT_ASSOCIATED_TEXT` (`linux_driver.py:285-292`, gated by
  `if not constants.DISABLE_KITTY_KEY`). iTerm2 then reports the IME-composed CJK as a CSI-u sequence
  whose *associated text* Textual parses to an **empty `character`** — so the CJK is received but
  dropped. (Mouse is unaffected → the channel is alive; only key associated-text parsing fails.)
- **Fix hypothesis (grounded, Owner-testable):** **`TEXTUAL_DISABLE_KITTY_KEY=1`** makes Textual NOT
  request the kitty protocol (`linux_driver.py:285` skips the `\x1b[>…u` push), so iTerm2 falls back
  to sending CJK as **legacy UTF-8** — which Textual parses as a normal printable char. This is a
  **pure Lybra-side fix**: set the env var in the TUI launcher **before textual is imported** (its
  `constants.py` reads it at import). It fixes the bug, not a terminal doc.
- **Decisive experiment — CONFIRMED (Owner-run).**
  `TEXTUAL_DISABLE_KITTY_KEY=1 ~/o3-textual-venv/bin/python ~/cjk-diagnostic.py` + typing 中文 →
  `KEY character='中'` **appears.** Root cause + fix are confirmed: disabling the kitty keyboard
  protocol restores CJK direct input. The fix = set `TEXTUAL_DISABLE_KITTY_KEY=1` at TUI launch
  (before textual import).
- **Tradeoff to verify in O3:** disabling the kitty protocol loses enhanced key reporting; confirm
  Shift+Enter / Ctrl+J newline still work (**Ctrl+J** — a real control byte — is the safe fallback if
  Shift+Enter collapses to Enter without kitty).
- **Copy (C/D):** mouse events DO arrive (drag logs), but the reliable, terminal-independent copy is
  still the **`/copy` (OSC 52)** command (1c).

### 1e. ★ Precise probe FIRST (R decision) — fix CJK WITHOUT losing Shift+Enter
Owner accepts the full kitty-disable as a **fallback**, but wants a precise alternative tried first.
- **Hypothesis:** of the enabled kitty flags `DISAMBIGUATE(1) | REPORT_ALL_KEYS(8) |
  REPORT_ASSOCIATED_TEXT(16)` (`linux_driver.py:30-34`, current enable = `\x1b[>25u`), only
  **`REPORT_ASSOCIATED_TEXT` breaks CJK** (its CSI-u associated-text → empty character), while
  **`DISAMBIGUATE` is what distinguishes Shift+Enter**. So enabling with associated-text **dropped**
  may fix CJK (printable text falls back to plain UTF-8) AND keep Shift+Enter.
- **Client-side override (does NOT modify the Textual package):** rewrite the `linux_driver` module
  constants before the driver runs so the enable flag excludes associated-text — verified: setting
  `KITTY_REPORT_ASSOCIATED_TEXT=0` (and choosing `DISAMBIGUATE` ± `REPORT_ALL_KEYS`) makes the driver
  emit `\x1b[>1u` or `\x1b[>9u`.
- **Owner probe (`~/cjk-precise-probe.py`, both flags):** `KFLAG=1` (DISAMBIGUATE only) and `KFLAG=9`
  (DISAMBIGUATE|REPORT_ALL_KEYS), each in iTerm2 — report for each: **(a)** does 中文 log
  `character='中'`? **(b)** is Shift+Enter a DISTINCT key from Enter? *(Note: `REPORT_ALL_KEYS(8)`
  routes printable keys through CSI-u, so it may re-break CJK — hence testing `KFLAG=1` too.)*
- **Decision rule (R):** if a flag gives **both** (a)+(b), is clean, and is Owner-double-confirmed →
  adopt precise; else fall back to `TEXTUAL_DISABLE_KITTY_KEY=1`.
- **★ RESULT — Owner-confirmed on iTerm2→SSH→WSL:**
  - **`KFLAG=1` (DISAMBIGUATE only, `\x1b[>1u`):** (a) 中文 → `KEY character='中'` ✅ **and** (b)
    Shift+Enter → `key='shift+enter'` (distinct from `enter`/`'\r'`) ✅ — **both pass.**
  - `KFLAG=9` (adds REPORT_ALL_KEYS): (a) CJK **broken** (printable keys routed through CSI-u); (b)
    Shift+Enter still distinct. → rejected.
  - **Decision = PRECISE path, `KFLAG=1`.** Enable the kitty protocol with **DISAMBIGUATE only**
    (drop `REPORT_ALL_KEYS` + `REPORT_ASSOCIATED_TEXT`): CJK direct input works **and Shift+Enter is
    preserved**. The override is a small, well-scoped module-constant rewrite (the driver's own
    published constants), double-confirmed on real hardware → clean enough. **The fallback is NOT
    needed.**

### 1c. Copy — root cause identified (Textual 8.2.7 has native selection; Lybra steals Ctrl+C)
- Textual 8.2.7 ships **native text selection + clipboard**: `App.ALLOW_SELECT = True` (default),
  `Static.ALLOW_SELECT` / `Markdown.ALLOW_SELECT = True` (Lybra renders the transcript as `Static` /
  `Markdown`), plus `App.copy_to_clipboard` / `clipboard` / `clear_selection`. Textual's own default
  bindings are `ctrl+q` (quit) **and** `ctrl+c`.
- **Lybra rebinds `ctrl+c → quit`** (`app.py:260` `Binding("ctrl+c", "quit", "Quit")`), which
  **overrides Textual's selection-copy affordance** — pressing Ctrl+C after selecting quits instead
  of copying. This is the prime F-o3-12b cause.
- Fix direction: **free Ctrl+C for copy** — drop the `ctrl+c→quit` binding and keep quit on `Ctrl+Q`
  (Textual's default quit; also `/quit`), so Textual's native select-then-Ctrl+C copy works. Confirm
  drag-selection highlights the transcript (ALLOW_SELECT default True; verify no override disables it).

---

## §2 Fix (revised per the Owner-run isolation results)

- **COPY — real fix (O3-revised): run with mouse capture OFF (`run(mouse=False)`).** O3 showed the
  deeper truth: the Owner cannot copy **any** terminal text because Textual **captures the mouse**
  (`\x1b[?1000h` …), which steals iTerm2's native selection. **Root cause = mouse capture.** Claude
  Code (Ink) never captures the mouse — that is *why* it keeps native selection/scrollback/copy in
  the same iTerm2. So Lybra runs the app with **`mouse=False`** (Textual's official `App.run` param →
  the driver's `_enable_mouse_support` early-returns): iTerm2 native **selection + scrollback +
  Cmd/Ctrl+C copy of any text** all work — **Claude-Code parity**. Owner-confirmed on real hardware.
  Trade: Textual's in-app mouse (click/scroll) is off; the `/` menu is keyboard-navigable, so nothing
  the Owner uses is lost.
  - **A first draft added a `/copy [N]` (OSC 52) command + freed Ctrl+C — both DROPPED (Owner call):**
    once native selection works, `/copy` is redundant *and* misleading (it printed "copied" but the
    OSC 52 write needs a separate terminal clipboard-access setting, so it could silently not land).
    So the copy fix is **exactly `run(mouse=False)`** and nothing else; the `ctrl+c→quit` binding is
    left unchanged (native copy is Cmd+C, which iTerm2 handles locally and never reaches the app).
- **CJK typing — PRECISE path (chosen; §1e Owner-confirmed):** at TUI launch, before the app runs,
  override the `linux_driver` kitty enable so it emits **DISAMBIGUATE only** (`\x1b[>1u`) — set the
  driver's `KITTY_REPORT_ALL_KEYS` and `KITTY_REPORT_ASSOCIATED_TEXT` module constants to `0`, leaving
  `KITTY_DISAMBIGUATE_ESCAPE_CODES`. Result: **CJK types AND Shift+Enter is preserved.**
  - **★ Anti-silent-failure guard (R):** BEFORE the patch, **assert the three constant NAMES exist**
    on `textual.drivers.linux_driver` — a missing name (a Textual rename/refactor) **fails LOUD**, it
    is never a silent no-op. AFTER the patch, **assert the result values**
    (`KITTY_REPORT_ASSOCIATED_TEXT == 0`, `KITTY_REPORT_ALL_KEYS == 0`,
    `KITTY_DISAMBIGUATE_ESCAPE_CODES == 1`).
  - **Fragility, disclosed honestly:** the value is stable, but this **depends on Textual
    `linux_driver`'s PRIVATE constant names** — so it is guarded by the existence + result assertions
    above (rename → fail-loud), NOT claimed "version-independent". The Textual package is not
    modified; no dependency bump; no terminal doc. macOS runs through the same `linux_driver` (covered);
    **Windows is out of scope.** (The `TEXTUAL_DISABLE_KITTY_KEY=1` fallback is documented, not used.)

---

## §3 Correct AIPOS-222's disclosure

Remove/replace the "CJK needs a special terminal / documented terminal requirement" claim (it was a
wrong conclusion). The real cause: Textual's kitty-keyboard-protocol `REPORT_ASSOCIATED_TEXT` parsing
dropped IME CJK to an empty character; **Lybra fixes it client-side** by enabling the kitty protocol
with **DISAMBIGUATE only**, so **Lybra itself supports direct CJK input — and Shift+Enter is
preserved**. Update README / `docs/v1_disclosure.md` to say exactly that. **claims ⊆ disclosure** —
claim CJK direct input only once the O3 real-hardware acceptance confirms it.

---

## Red lines

- pure client UX (`app.py` / widgets) · **no accountability-logic change** · copilot read-only /
  scopes `[]` / zero-write unchanged · gate / ★A1 / dual-root / zero-dep untouched · textual + stdlib
  only, **no new dependency** · a freed Ctrl+C must still leave a clear quit path (`Ctrl+Q` / `/quit`).
- DRAFT writes no product code and commits nothing; evidence zero-touch; cc holds no owner token and
  never confirms.

---

## verify = positive truth

- **CJK fix is at the driver/launch layer — a headless Pilot cannot reach the terminal input path
  (R1 honesty).** So the code-level positive truth asserts the **RESULT VALUES**, not merely "it
  ran": after the launch path applies the override, assert on `textual.drivers.linux_driver` that
  `KITTY_REPORT_ASSOCIATED_TEXT == 0` and `KITTY_REPORT_ALL_KEYS == 0` and
  `KITTY_DISAMBIGUATE_ESCAPE_CODES == 1`; and assert the pre-patch **name-existence guard fails loud**
  if any constant is missing (a Textual rename). (No "extend the CJK Pilot to the real fix path"
  claim — that path isn't reachable headless.)
- **CJK landing's ONLY functional criterion = O3 real hardware** (Owner types 中文 into the prompt in
  iTerm2→SSH→WSL, on the **npm-installed prefix**, and it lands).
- **Pilot (copy):** assert `Ctrl+C` is no longer bound to quit AND a quit path remains (`Ctrl+Q` /
  `/quit`); assert the transcript widgets keep `ALLOW_SELECT` true; assert `/copy` invokes
  `copy_to_clipboard` with the expected chat text (the OSC 52 write). Note: `/copy` landing in the
  system clipboard needs the terminal's clipboard-write permission (iTerm2 setting) — an O3 check.
- **Newline (R2, precise path chosen):** assert Shift+Enter still inserts a newline **and** `Ctrl+J`
  is documented as the equivalent (both survive under DISAMBIGUATE-only). **O3 verifies newline +
  `shift+tab` (mode) + `escape` (cancel) — no regression** (the kitty flag reduction could touch key
  reporting).
- existing TUI tests all green; no accountability change; three lanes green + ACCEPTANCE; `git diff`
  = TUI client files (+ docs) only.

---

## Deliverable / flow

DRAFT + §1a–1e investigation (root cause CONFIRMED; R direction-audit folded) → **Owner runs the
§1e precise probe** (`~/cjk-precise-probe.py`, `KFLAG=1` and `KFLAG=9`) → **decision: precise
(Shift+Enter kept) vs fallback (`TEXTUAL_DISABLE_KITTY_KEY=1`, Ctrl+J newline)** → Owner approves →
implement the whole slice (CJK launch fix + `/copy` OSC 52 + free Ctrl+C + correct 222 + the
retained Pilots) → `cc glm` → R re-checks the verdict → **Owner O3 on the npm-installed prefix: types
中文 / copies chat / newline keys** → finalize (F-o3-12a / F-o3-12b CLOSED on that confirmation).
