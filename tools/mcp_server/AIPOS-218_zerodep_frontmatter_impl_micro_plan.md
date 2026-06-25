# AIPOS-218 — Zero-dependency frontmatter fidelity (F-rg-2 + F-rg-3) — IMPLEMENTATION micro-plan (rev.2)

**status: draft**
**authority: NONE**

rev.2 incorporates the Owner's "Option 1 + guardrails" ruling after a discovery during rev.1
implementation: the AIPOS-217 design under-characterized the surface — **`lybra init` reads
nested-map template manifests** (a core path), not just FLAT records. rev.2 also folds in the
mandated **read-point audit** (no more bare-python blind spots).

## Status of work already done (rev.1, kept)
- **WS1a + WS2 DONE & proven:** `frontmatter._fallback_parse`/`_parse_scalar` now faithfully parse the
  FLAT subset (Defect A: unquote single/double; Defect B: flat lists; ruling 4: int/float/bool/null
  coercion only on unquoted tokens) and the writers emit `key: []` for empty lists. Verified: a real
  return record round-trips **byte-equal to PyYAML**, zero warnings, with `yaml` blocked. (Records,
  cards, board reads, validator, loader, authority, queue, context-pack all pass on bare python.)
- Remaining bare-python failures after WS1a/WS2: 28 — all NESTED (8 template manifests [CORE init],
  16 custom-profiles/instance-identity, ~4 orchestration/planner). This drove the Owner ruling.

## ★ Read-point audit (mandated — the "no more blind spots" gate)
Every frontmatter / YAML read point, classified. Source: `grep parse_markdown_frontmatter` +
`grep yaml.safe_load` over `tools/` (excl. tests).

### FLAT-core — served by the WS1a fallback, zero-dep (✓ pass bare python)
| reader | file:line | shape |
|---|---|---|
| draft validator | `draft_validator.py:127` | task card (flat) |
| task loader | `task_loader.py:76` | task card (flat) |
| authority scanner (L3) | `authority_scanner.py:288` | task/record (flat) |
| records reader | `records.py:65,186` | records (flat) |
| queue mutation | `queue_mutation.py:165` | task card (flat) |
| session record load | `record_writer.py:728` | record (flat) |
| board adapter | `board_adapter.py:1639,1996,2310,2315` | cards/audit/reviewed records (flat) |

### NESTED-MAP core — `lybra init` (the discovered gap) → WS1b bounded-nested, zero-dep
| reader | file:line | shape |
|---|---|---|
| template manifest | `workspace_templates.py:65` (→`_load_manifest:61`) | frontmatter w/ **1-level nested maps** (`output_policy:`, `controlled_execute:`) + **scalar lists** (`required_variables:`, `optional_variables:`) |

### SEQUENCES-OF-MAPPING — PyYAML-only; **READ = loud warn+empty (degrade), WRITE = raise**
| reader/writer | file:line | shape | path | policy |
|---|---|---|---|---|
| profile docs (```yaml) | `agent_profiles.py:242` (`_load_profiles_from_docs`) | `instances:` = **list of maps** w/ nested lists | **GATE** (`load_agent_profiles`→`tools.py:351`); **ships by default** (`0_control_plane/agents/dev_claude_runtime_profiles.md`) | **degrade** (warn+empty) — a raise here breaks EVERY bare-python gate op |
| custom registry | `agent_profiles.py:208`; `custom_agent_profiles.py:92` (`load_custom_registry`) | `profiles[].instances[]` | gate + CLI; default-absent | **degrade** (warn+empty; absent→empty no-warn) |
| custom registry write | `custom_agent_profiles.py:_yaml_text` (~:315) | sequences-of-mappings | feature write | **raise** (already) — no silent corruption |
| orchestration summary | `orchestration_summary_preview.py:46` | list of maps | standalone preview CLI | warn+empty, surfaced visibly |
| orchestration timeline | `orchestration_timeline_preview.py:45` | list of maps | standalone preview CLI | warn+empty, surfaced visibly |
| non-frontmatter context bundle | `context_pack_builder.py:54` | whole-file YAML | opt-in context-pack (frontmatter bundles go via WS1) | warn+empty |

**Audit conclusion:** exactly **two** core-reachable nested surfaces — template manifests (→ WS1b
bounded-nested, zero-dep) and the default-shipped profile docs (→ degrade, gate must not break). No
third blind spot: all FLAT-core readers pass on bare python today.

## ★ WS3 refinement flagged for 复核 (deviation from ruling 2 literal, justified by the audit)
Ruling 2 said "read-present-but-unparseable = **raise**". The audit shows the sequences-of-mapping
**readers sit on the gate path with default-shipped data** (`dev_claude_runtime_profiles.md` always
exists), so a hard raise on read would break **every** bare-python gate operation — violating the
"no gate semantics change" red line. **Refinement: WRITE = raise; READ = loud structured warning +
empty (never silent-no-signal); direct preview/edit CLIs surface that warning visibly / non-zero.**
"Loud" on reads is satisfied by a surfaced warning, not a crash. Please confirm in 复核.

## Owner rulings (rev.2)
1. **Freeze the contract** — bundled manifests + records/cards use only: scalar / bool / int / float /
   empty / quoted scalar / scalar list / **bounded nested map (≤2 levels)**; **no sequences-of-mappings,
   no over-depth**. Static guard test; over-bound = red.
2. **NESTED-loud only for sequences-of-mappings** (custom-profile instances, orchestration) per the
   WS3 refinement above. Manifest nested-maps go through WS1b zero-dep, NOT PyYAML-loud.
3. **Strip single + double quotes** (WS1a, done).
4. **Type coercion** int/bool/null/float on unquoted tokens only (WS1a, done).
5. **Empty list → `key: []`** (WS2, done).
6. **Honest wording**: core (init + task/record I/O) zero-dep + correct; advanced sequence features
   (custom-profile instances, orchestration preview) require PyYAML and fail loudly without it.

## Red lines
- No gate **semantics** change. **FLAT + bounded-nested(manifest) parity vs PyYAML** (type + value).
- Sequences-of-mapping: **write raises; read is loud (warn+empty), never silent**. Gate never hard-fails on read.

## Workstreams (rev.2)

### WS1b — extend the fallback to bounded nested maps + scalar lists (NEW, for manifests)
Extend `_fallback_parse` to an indentation-aware parser supporting **exactly** the manifest subset:
- top-level + nested **maps to depth ≤ 2** (`key:` header with empty scalar, followed by more-indented
  `subkey: scalar` lines → a dict);
- **scalar lists** at top level and one level deep (`key:` + indented `- scalar`);
- scalars/bools/ints/floats/null/quoted as in WS1a.
- **Reject (do not silently mis-parse) sequences-of-mappings** (a `- ` item that is itself a `key:`
  map) and depth > 2 → return a warning + leave that value absent, so such a shape never silently
  corrupts (the contract test §WS4 guarantees bundled files never contain it).
- Parity: for the manifest subset the no-PyYAML parse must equal `yaml.safe_load`.

### WS3 — sequences-of-mapping policy (refined; writes raise, reads loud-degrade)
- **Writes** (`custom_agent_profiles._yaml_text`, any nested writer): raise a clear "PyYAML required"
  error if `yaml is None` (already present for custom profiles; verify no other nested writer silently
  proceeds).
- **Reads** (`_load_profiles_from_docs`, `_load_profiles_from_registry`, `load_custom_registry`,
  context-bundle whole-file, orchestration previews): attach a **clear structured warning** and return
  empty — never silent. Today `_load_profiles_from_docs` returns `[]` with NO warning and
  `_load_profiles_from_registry` returns `[],[]` — fix both to carry the warning so the degraded state
  is loud and traceable. Ensure gate callers (`tools.py:351`, board) keep operating with empty
  profiles (they already do — confirmed bare-python).
- **Direct preview/edit CLIs** (orchestration summary/timeline, custom-profile edit): surface the
  warning as visible output / appropriate non-zero so a user invoking the nested feature without
  PyYAML gets a clear message.

### WS4 — freeze the emitted contract + writer/manifest guard (ruling 1)
- `test_writer_flat_contract.py`: drive the real card + publish/claim/return/audit writers; assert every
  emitted frontmatter line ∈ {scalar | bool | int | float | empty | quoted scalar | `key: []` |
  `key:`+`- scalar` block}. No maps, no nested sequences, no block scalars.
- `test_manifest_contract.py` (NEW): statically parse **every bundled `templates/*/manifest.md`** and
  assert it uses only {the above} + **bounded nested maps (≤2 levels) of scalars/scalar-lists**, and
  **no sequences-of-mappings / no depth>2**. A future bundled file exceeding the subset goes red
  (forces an explicit decision, never a silent bare-python break).

### WS5 — bare-python round-trip corpus + PyYAML parity (the make-or-break; extended to manifests)
`test_frontmatter_zerodep.py`: with `yaml` blocked via a `sys.meta_path` finder, assert
`parse == yaml.safe_load` (captured baseline) for:
- real records from the actual emitters (`build_mcp_return_record_markdown` w/ `artifact_refs`,
  `build_mcp_audit_verdict_record_markdown` w/ `evidence_refs`+bool, a publish record);
- a real task card (`examples/sample_workspace/.../sample_task.md`);
- **every real bundled `templates/*/manifest.md`** (nested maps + scalar lists) — parity incl. nested.
Adversarial values: colon (`'Fix: thing'`, ISO timestamp), `#`, brackets/braces, embedded quote
(`o'brien`), leading/trailing whitespace, empty vs null, flat list w/ colon item, empty list,
`True-Name` stays string, int (`event_count`), and a bounded nested map. Assert warnings empty for all.

### WS6 — acceptance probe: block ALL third-party + correctness (closes AIPOS-214 R0; extended to manifest)
Generalize `v1_acceptance.py:34-52`: replace `_BlockTextual` with a finder allowing only stdlib
(`sys.stdlib_module_names` + builtins) + the repo `tools` package, blocking everything else (incl.
`yaml`). Then assert frontmatter **correctness** in-probe: (a) a rendered record round-trips
losslessly; (b) **a real bundled manifest parses equal to its PyYAML baseline**; (c) `lybra init`
succeeds end-to-end with all third-party blocked. Keep the gate boot/stop check under the block. Exit
non-zero on any mismatch. This makes "R0 zero-dep" mean bare-python **correctness**, permanently.

### WS7 — F-rg-3 (order-independent isolation test)
`test_state_module_does_not_import_textual`: assert in a **fresh subprocess** that importing
`tools.lybra_tui.state` alone does not import textual — not by inspecting the polluted global
`sys.modules`.

### WS8 — honest zero-dep wording (ruling 6)
- README + `docs/v1_disclosure.md`: state the gate **core (init + task/record I/O) is zero-dep and
  correct on bare python**; **advanced sequence features (custom-profile instances, orchestration
  previews) require PyYAML and fail loudly without it**. Keep claims ⊆ disclosure (AIPOS-213 guard
  must still pass). The unconditional "zero Python runtime dependencies" line gets this qualification.

## Verification
```bash
cd /home/kiwi/lybra
PYTHONPATH=$PWD python -m unittest tools.aipos_cli.tests.test_frontmatter_zerodep tools.aipos_cli.tests.test_writer_flat_contract tools.aipos_cli.tests.test_manifest_contract -v
PYTHONPATH=$PWD python -m unittest discover -s tools -p "test_*.py"          # full suite green
PYTHONPATH=$PWD python -m tools.acceptance.v1_acceptance                     # ACCEPTANCE: PASS (blocks all third-party + asserts correctness + init)
# HEADLINE PROOF — bare venv (NO PyYAML, NO textual):
python3 -m venv /tmp/v0 && PYTHONPATH=$PWD /tmp/v0/bin/python -m unittest discover -s tools -p "test_*.py"
#   must be GREEN incl. init + manifest; custom-profile/orchestration tests assert the LOUD warn/raise (not silent empty)
```

## cc glm audit focus
Bare-python **correctness** genuinely tested incl. **init + manifest**; FLAT + bounded-nested **parity
vs PyYAML**; writer + manifest contract guards real; sequences-of-mapping **writes raise / reads loud
(warn+empty) never silent**, and **gate never hard-fails on read**; acceptance probe blocks ALL
third-party + asserts correctness + init; F-rg-3 order-independent; docs honest (claims ⊆ disclosure);
no gate-semantics drift; the read-point audit is complete (no third blind spot).

## Sequencing / non-goals
this DRAFT (rev.2) → Owner 复核 → approve → implement → cc glm audit → Owner spot-check → finalize →
re-run release gate on bare python (correctness) + TUI O3 → npm publish (separate Owner authorization).
NOT here: npm publish; AIPOS-206b / R2 / R5; CI wiring; release-gate cleanup.

## Open for 复核
- Confirm the **WS3 refinement** (reads degrade loud-warn+empty incl. gate-path sequences-of-mapping;
  only writes raise) — the one deviation from ruling 2's literal "read=raise", forced by the audit
  (default-shipped sequences-of-mapping on the gate path).
- Confirm the **bounded-nested depth = 2** is sufficient (manifests use 1 level; 2 gives headroom).
