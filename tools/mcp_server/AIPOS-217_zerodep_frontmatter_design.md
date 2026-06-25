# AIPOS-217 — Zero-dependency frontmatter fidelity (F-rg-2) — DESIGN micro-plan

**status: draft**
**authority: NONE**

Finding: F-rg-2 (with F-rg-3 folded in). This is a DESIGN DRAFT only. No implementation,
no product code/test/README/pyproject/fallback edits are made here. The Owner reviews this
DRAFT and rules on the open items (§6) before any implementation slice begins.

---

## 0. The defect, restated and reproduced

Lybra claims the gate core has **zero Python runtime dependencies**
(`README.md:37` "gate core ... has zero Python runtime dependencies"; `pyproject.toml:15`
`dependencies = []`, reinforced by the `[tool.lybra]` comment and `pyproject.toml:7-8`
"the gate core has ZERO runtime Python dependencies").

But six modules import PyYAML opportunistically via `try: import yaml / except: yaml = None`:
`tools/aipos_cli/frontmatter.py:5-8`, `agent_profiles.py`, `custom_agent_profiles.py`,
`context_pack_builder.py:15`, `orchestration_summary_preview.py:8`,
`orchestration_timeline_preview.py:8`.

When PyYAML is **present** (the dev runner has it), `parse_markdown_frontmatter`
(`frontmatter.py:84-89`) routes through `yaml.safe_load` and everything is correct. When
PyYAML is **absent** (a real npm end-user on a bare system python — exactly the audience the
"npm install and just run" promise targets), it falls back to `_fallback_parse`
(`frontmatter.py:25-63`), which is **lossy on the very frontmatter Lybra itself emits**.

I verified this in a subprocess with `yaml` blocked via a `sys.meta_path` finder, rendering
with the real `record_writer.render_markdown` and reading back with the real
`parse_markdown_frontmatter`. **Two distinct FLAT-core defects** (not one):

**Defect A — quotes are never stripped.** `_parse_scalar` (`frontmatter.py:11-22`) returns
`text` verbatim. The writers single-quote any value containing `:` `#` `[` `]` `{` `}`
newline, or with leading/trailing whitespace (`record_writer.py:94-95`,
`draft_writer.py:85-86`). So a title `Fix: thing` is emitted as `title: 'Fix: thing'` and read
back as the literal string `"'Fix: thing'"` — quotes and all. Round-trip corrupts every
quoted scalar.

```
emitted:  title: 'Fix: thing'
parsed:   title == "'Fix: thing'"   (PyYAML would give: "Fix: thing")
```

**Defect B — flat lists are silently dropped entirely.** A list is emitted as a bare header
line `artifact_refs:` (empty scalar) followed by `- item` lines (`record_writer.py:105-108`,
`draft_writer.py:96-99`). In `_fallback_parse`, the header line hits line 60-61:
`parsed_value = _parse_scalar("")` → `None`, then `metadata[key] = None`. Although line 62
does set `current_list_key`, the subsequent `- ` items hit line 43 — `metadata[key]` is now
`None`, not a `list` — so **every list item is rejected with a warning** and the key stays
`None`. `artifact_refs`, `evidence_refs` are wiped out.

```
emitted:  artifact_refs:
          - 'a: b'
          - c
parsed:   artifact_refs == None   + 2 warnings "list item found after scalar"
```

The AIPOS-214 R0 "zero-dep" finding was **insufficient** because the existing isolation probe
(`tools/acceptance/v1_acceptance.py:34-52`) only blocks `textual` and only asserts that
imports *succeed* (the `try/except` swallows the missing `yaml` and imports fine). It never
exercised no-PyYAML **correctness**, so both defects above passed the gate. ~42 tests fail in
a clean venv without PyYAML and pass once it is installed — the classic "dev runner happened
to have it" mask.

---

## 1. Precise characterization of Lybra's emitted/read frontmatter subset

Split into **FLAT-core** (fallback-served accountability surface) vs **NESTED** (PyYAML-only
advanced surface). Evidence by file:line.

### 1a. FLAT-core surface — task cards + publish/claim/return/session/audit records

These are written by hand-rolled emitters (NOT PyYAML), all sharing the same scalar/list
shape and quoting rule, and read back through `parse_markdown_frontmatter` → fallback.

Writers / renderers:
- `draft_writer.render_markdown_task_card` (`draft_writer.py:106-120`) + `_yaml_scalar`
  (`draft_writer.py:73-87`); field order `FRONTMATTER_ORDER` (`draft_writer.py:37-66`).
- `draft_writer.render_publish_record` → `_record_frontmatter` (`draft_writer.py:90-103`,
  ordered list `draft_writer.py:178-198`).
- `record_writer.render_markdown` (`record_writer.py:99-112`) + `_yaml_scalar`
  (`record_writer.py:82-96`); per-record orders `record_writer.py:115-303`
  (claim/session/return/audit-dispatch/audit-verdict, plus the MCP variants).

Readers: `record_writer.load_session_record` (`record_writer.py:723-726`);
publish/draft read via `read_draft_markdown` → `parse_markdown_frontmatter`;
`records.py:7` imports the same parser.

Field SHAPES present in FLAT-core (with evidence):
- **Plain scalars** (string/no special char): `record_type`, `task_id`, `actor`,
  `session_id`, `from_state`, `to_state`. e.g. real on-disk card
  `examples/sample_workspace/5_tasks/queue/pending/sample_task.md:2-24` — 23 plain scalar
  fields, no quoting needed.
- **Booleans**: `needs_owner: false` (sample card line 13), `active_lease_written: False`
  (`record_writer.py:441`), `independence_distinct_instance: True` (`record_writer.py:612`).
  Emitted lowercase (`_yaml_scalar` lines 83-84); read back by `_parse_scalar:16-19`.
- **Integers**: `event_count: 1` (`record_writer.py:364`), emitted via `str(int)`
  (`_yaml_scalar` line 89-90). NOTE: `_parse_scalar` does NOT re-coerce to int — returns the
  string `"1"`. `update_session_record_markdown` (`record_writer.py:744-748`) defensively
  `int()`-casts on read, so this is tolerated today but is a latent typing gap (see §6).
- **Empty / null**: emitted as `key: ` (`_yaml_scalar` line 87-88 returns `""`), e.g. the
  unset confirmer/signature placeholders (`record_writer.py:391-395`). Read back as `None`
  (`_parse_scalar:13-14`).
- **Quoted strings** — quoted *specifically* because the value contains `:` `#` `[` `]`
  `{` `}` newline OR has leading/trailing whitespace (`record_writer.py:94`,
  `draft_writer.py:85`). Single-quote style with `'` doubled. Real triggers in this codebase:
  - `task_path` / `task_path`-like refs containing `/` — actually `/` is NOT a trigger, so
    paths stay unquoted (verified: `task_path: 5_tasks/queue/pending/x.md` unquoted). Quoting
    fires on **titles with colons** (`title: 'Fix: thing'`), timestamps are colon-free
    (`2026-06-25T...Z` uses `:`? — yes `created_at` ISO timestamps contain `:` →
    **timestamps ARE quoted**: `created_at: '2026-06-25T12:00:00Z'`). This is the highest-
    frequency quoted field on the accountability core and the one Defect A corrupts on every
    record.
  - free-text summaries / result lines that may contain `:` or `#`.
- **Flat lists** (sequence of scalars, never nested): `artifact_refs`
  (`record_writer.py:545`, return record), `evidence_refs` (`record_writer.py:691`, audit
  verdict). Emitted as bare header + `- item` lines (`record_writer.py:105-108`). Defect B
  drops these.
- **Multiline**: NONE in FLAT-core. The body is markdown after the closing `---`, not a YAML
  block scalar. No `|`/`>` block scalars are emitted by either writer. (Important: this means
  the fallback does NOT need block-scalar support for the accountability core.)

FLAT-core conclusion: the emitted subset is **scalars + booleans + ints + empty + single-
quoted scalars + flat scalar lists**. No maps, no nested sequences, no block scalars. This is
a small, pinnable contract (see §6 open item).

### 1b. NESTED surface — PyYAML-only, no usable fallback

These emit/read **sequences-of-mappings** and whole-file YAML and have NO hand-rolled fallback;
without PyYAML they silently return empty, which cascades into downstream IndexError/KeyError.

- **Custom agent profile registry** — `profiles[].instances[]` (sequence of maps, each with a
  nested instances sequence of maps). Writer: `custom_agent_profiles._yaml_text`
  (`custom_agent_profiles.py:314-317`, `yaml.safe_dump(..., sort_keys=False)`); it already
  **raises** `ValueError("PyYAML is required to write custom agent profiles")` when `yaml`
  is None (line 315-316) — good precedent. Readers: `load_custom_registry`
  (`custom_agent_profiles.py:85-100`) returns `_empty_registry()` + a warning string when
  `yaml is None` (line 89-90); `agent_profiles._load_profiles_from_registry`
  (`agent_profiles.py:203-223`) returns `[], []` silently when `yaml is None` (line 205-206);
  `agent_profiles._load_profiles_from_docs` (`agent_profiles.py:230-232`) and
  `_extract_yaml_blocks` (`agent_profiles.py:226-227`) parse ```yaml fenced blocks.
- **Context bundles** — whole-file YAML fallback in
  `context_pack_builder._parse_bundle_text` (`context_pack_builder.py:48-60`): tries
  frontmatter first, then `yaml.safe_load(text)` for non-frontmatter bundles (line 52-54).
  When `yaml is None` and the bundle is not frontmatter, returns `{}` silently.
- **Orchestration previews** — `orchestration_summary_preview.py:43-46` and
  `orchestration_timeline_preview.py:42-45` both guard `if yaml is None:` then
  `yaml.safe_load`. These parse orchestration logs (nested).

NESTED conclusion: degradation today is **inconsistent** — one path raises (registry write),
others silently return empty (registry read, profiles, context bundles, previews). Silent
empty is the dangerous mode: it produces wrong-but-not-erroring accountability data.

---

## 2. Option comparison

| Option | Effort | Risk | Zero-dep preserved? | Blast radius |
|---|---|---|---|---|
| ① Faithful stdlib fallback (RECOMMENDED) | Low–Med | Low | YES (true npm-install-and-run) | `frontmatter.py` only for FLAT-core; explicit error policy for NESTED |
| ② Vendor a tiny YAML parser | Med–High | Med | YES (technically) | new vendored module + license/maintenance burden |
| ③ Declare PyYAML a core dependency | Low | Low-Med | **NO — destroys the property** | README + pyproject + disclosure rewrite |

### ① Faithful stdlib fallback — RECOMMENDED

Fix `_fallback_parse` / `_parse_scalar` to correctly handle **exactly the subset Lybra emits**
(§1a), no more:
- **Defect A**: in `_parse_scalar`, after the bool/null checks, detect a single-quoted scalar
  (`text` starts and ends with `'`), strip the outer quotes and un-double `''`→`'`. (Double-
  quoted is not emitted by the writers, but cheap to also handle for forward-safety — Owner
  ruling §6.) Leave plain scalars untouched.
- **Defect B**: fix the list handling so a `key:` header with an empty scalar followed by
  `- ` items produces a list. Concretely: when `parsed_value is None` AND `key` is being set
  as a potential list header, do not stomp it with `None` if list items follow — either
  initialize `metadata[key] = []` lazily on the first `- ` item (preferred: keep the empty-
  scalar-as-None semantics for genuine empties, and on the first list item under
  `current_list_key`, replace a `None` value with `[]`), so the line-43 "found after scalar"
  rejection no longer fires for the writers' own output.
- **NESTED policy**: replace silent-empty degradation with a **loud, correct error**:
  "PyYAML required for this feature" (mirror the existing raise at
  `custom_agent_profiles.py:315-316`). The accountability core stays zero-dep and correct;
  advanced/nested features degrade **safely and visibly** rather than returning corrupt-empty
  data. (Exact per-call-site policy — raise vs. structured warning surfaced to the Owner —
  is an open item §6, because some readers are on read paths where a hard raise would break
  listing of FLAT-core records that happen to coexist with an absent registry.)

Effort: small, localized to `frontmatter.py` for the core fix; the NESTED policy is a handful
of guarded return sites. Risk: low — fully covered by the new round-trip corpus (§3) and the
hardened gate (§4). This is the only option that keeps the README/pyproject claim **true**.

### ② Vendor a tiny YAML parser (no external dep)

Bundle a minimal pure-python YAML subset parser inside `tools/`. Pros: handles NESTED too
without PyYAML. Cons: (a) effort to vet/maintain correctness on the nested registry shape;
(b) licensing — must pick a permissively-licensed minimal parser and carry its notice;
(c) ongoing maintenance and a second YAML code path to keep in sync with PyYAML semantics
(divergence risk re-introduces exactly this class of bug). Not recommended unless the Owner
decides the NESTED surfaces must also be zero-dep (see §6).

### ③ Declare PyYAML a core dependency — NOT recommended

**This DESTROYS the "npm install and just run" property.** Today an npm end-user runs
`npm install -g lybra` and the gate core works on a bare system python with no pip step. Add
PyYAML to `pyproject.toml:15` `dependencies` and a Python user now needs a `pip install` for
the gate core too — the core value proposition (`README.md:37-39`) is broken. If the Owner
nonetheless chooses this, it **forces an honest rewrite** of the zero-dep claim in:
`README.md:37`, `pyproject.toml:15` + the `pyproject.toml:7-8` and `:22-23` comments, and
`docs/v1_disclosure.md` (verify exact lines at implementation time). Recommend AGAINST.

**Recommendation: ① for FLAT-core; loud "PyYAML required" error for NESTED.** This makes the
zero-dep claim true for the accountability core (which is what the claim is really about) while
nested advanced features degrade visibly instead of silently corrupting.

---

## 3. Bare-python round-trip test corpus

New test module (no product-test edits in this DRAFT; this specifies it). Run with PyYAML
**blocked** via a `sys.meta_path` finder (pattern from `v1_acceptance.py:34-41`). Assertion:
for every real sample, `parse(render(parse(sample))) ==` the PyYAML-parsed truth, with no
value lost or mutated.

Corpus sources (real, not synthetic):
1. Real task card: `examples/sample_workspace/5_tasks/queue/pending/sample_task.md`
   (23 scalar/bool fields).
2. Real records produced by the actual writers — generate via
   `record_writer.build_mcp_return_record_markdown` (has `artifact_refs` flat list) and
   `build_mcp_audit_verdict_record_markdown` (has `evidence_refs` flat list +
   `independence_distinct_instance` bool), plus a publish record from
   `draft_writer.render_publish_record`. These are the real emitters, so the corpus tracks
   the real contract.

Adversarial values to inject (each must survive round-trip byte-for-byte after parse):
- a value with a colon: `title: 'Fix: thing'` and an ISO timestamp `created_at:
  '2026-06-25T12:00:00Z'` (Defect A — the common case, every record has a timestamp).
- a value with `#`: `note: 'count #1'`.
- a value with `[` / `]` / `{` / `}`.
- a value with an embedded single-quote: `actor: "o'brien"` → emitted `'o''brien'` → must
  read back `o'brien` (un-doubling).
- leading/trailing whitespace value: `'  spaced  '`.
- empty string vs. null distinction: `confirmer_role: ` → `None`.
- a flat list with a colon-bearing item: `artifact_refs:` / `- 'a: b'` / `- c` (Defect B).
- an empty list (header with zero items) — define expected: `None` vs `[]` (Owner ruling §6).
- a boolean that is NOT a bool-looking string: `actor: True-Name` must stay a string, not
  parse to `True` (guard against over-eager bool coercion).

Test must assert the WARNINGS list is empty for all writer-emitted samples (today Defect B
floods warnings).

---

## 4. Acceptance-gate hardening (closes the AIPOS-214 R0 gap permanently)

Generalize the isolation probe in `tools/acceptance/v1_acceptance.py:34-52`:

1. **Block ALL third-party modules, not just textual.** Replace `_BlockTextual` with a
   `sys.meta_path` finder that allows ONLY (a) stdlib modules and (b) the repo's own `tools`
   package, and blocks everything else — crucially `yaml`. Implementation note for the slice:
   detect stdlib via `sys.stdlib_module_names` (3.10+, matches `requires-python`) plus
   builtin names; allow names starting with `tools` / `tools.`; raise `ImportError` for the
   rest. This proves the gate core genuinely runs with no third-party deps present, not merely
   that imports *succeed* through a try/except.

2. **Add a frontmatter CORRECTNESS assertion** (not just import success). Inside the probe,
   after blocking `yaml`, render a record via the real `record_writer` emitter and assert the
   §3 round-trip fidelity (quoted scalar with colon + flat list both survive). This is the
   assertion the AIPOS-214 R0 check was missing. Exit non-zero on any mismatch.

3. Keep the existing gate-start/stop check (`v1_acceptance.py:46-51`) under the broader
   block, so we also prove the HTTP/SSE gate boots with zero third-party modules.

This makes the "R0 zero-dep" finding mean what it claims: bare-python **correctness**, tested,
permanently.

---

## 5. Fold in F-rg-3 — order-dependent textual-import test

`tools/lybra_tui/tests/test_tui_state.py:79`
`test_state_module_does_not_import_textual` asserts `self.assertNotIn("textual",
sys.modules, ...)` against the **global, already-polluted** `sys.modules`. In a full
textual-lane discover, a sibling test that imports `app` (which imports textual) runs first
and pollutes `sys.modules`, so this test's result is **order-dependent** — it can pass or fail
purely on discovery order, giving a false signal about whether `state` itself pulls textual.

Proposed fix (DESIGN; implement in the slice): assert the property in a **fresh subprocess**
(or a clean importlib check), so it measures "importing `tools.lybra_tui.state` alone does not
import textual" rather than inspecting the polluted global `sys.modules`. Reuse the same
`sys.meta_path` block-finder pattern from §4 / `v1_acceptance.py:34-41`: in a subprocess,
import only `tools.lybra_tui.state` (+ `tools.mcp_server.tools`, `tools.aipos_cli.
confirm_client` as the current test does) and assert `"textual" not in sys.modules` there.
This removes the discovery-order coupling and makes the isolation claim robust.

---

## 6. Open items for Owner ruling

1. **Pin the FLAT-core subset as a permanent emitted-contract?** §1a shows the accountability
   core emits only scalars/bools/ints/empty/single-quoted-scalars/flat-scalar-lists, no maps,
   no block scalars. May we declare this a frozen contract (and add a test that the writers
   never emit a shape outside it), so the stdlib fallback only ever needs to parse this
   subset?
2. **NESTED degradation policy**: is "loud `PyYAML required for this feature` error instead of
   silent empty" acceptable for the registry/profiles/context-bundle/preview readers? And:
   hard `raise` vs. a structured warning surfaced to the Owner — specifically on read paths
   (`agent_profiles.py:205-206`, `load_custom_registry:89-90`) where a hard raise could break
   listing of FLAT-core records when a registry is merely absent. Recommend: raise on
   *write*, structured-warning + empty on *read*-when-file-absent, raise on *read*-when-file-
   present-but-unparseable-without-yaml.
3. **Quote styles**: writers only emit single-quote. Should the fallback also strip double-
   quotes for forward-safety, or stay strictly matched to the emitted contract?
4. **Int coercion**: should `_parse_scalar` re-coerce integer-looking scalars (e.g.
   `event_count`) to `int` to match PyYAML, or keep string + rely on call-site `int()`
   (`record_writer.py:744-748`)? PyYAML returns int; fallback returns str → a latent
   parse-asymmetry. Recommend matching PyYAML (coerce) to keep round-trip type-faithful.
5. **Empty-list representation**: `artifact_refs:` with zero items — should parse to `[]` or
   `None`? Affects `result_summary_present`/list-presence logic downstream.
6. **Zero-dep claim wording**: if Option ① is approved, the claim stays true and README/
   pyproject need NO change — but should we add a one-line clarification that *advanced nested
   features (custom agent profiles, orchestration previews) require PyYAML*? Only if Owner
   wants the boundary documented.

---

## 7. Sequencing / non-goals

Sequence: **design only (this DRAFT) → Owner 复核 → Owner approve → implementation slice →
cc glm audit (focus: bare-python correctness is GENUINELY tested, not import-success) →
re-run the release gate (now with the no-third-party lane from §4) → npm publish.**

NOT in this DRAFT:
- Any implementation (no edits to `frontmatter.py`, writers, tests, README, pyproject,
  disclosure, or the acceptance probe).
- The AIPOS-216 `build_app` fix (separate, in flight).
- npm publish.
