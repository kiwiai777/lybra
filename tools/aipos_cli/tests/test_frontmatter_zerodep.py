"""AIPOS-218 WS5 / AIPOS-F98 — bare-python frontmatter parity and fail-closed test.

With the ``yaml`` module blocked via a sys.meta_path finder, asserts that the zero-dependency
fallback parser (the path the product takes when PyYAML is absent) produces the same result as
yaml.safe_load for:

- real records from the actual emitters (return w/ artifact_refs, audit-verdict
  w/ evidence_refs+bool, publish — the published card carries the two-level ``lane`` map);
- a real task card (examples/sample_workspace/.../sample_task.md);
- (the former templates/*/manifest.md corpus was deleted with templates/ in AIPOS-F105; its shapes —
  indented scalar list S7 and depth-1 nested map with bools S9 — stay covered by the shape inventory below);
- the AIPOS-F98 shape inventory (map-in-map, list-in-map, seq-of-maps, empty ``[]``/``{}``,
  quoted / multi-line quoted scalars, YAML 1.1 plain-scalar resolution, comments) and
  yaml.safe_dump / stdlib-emitter output of product-shaped metadata with hostile values.

Adversarial values: colon in value, ISO timestamp, ``#``, brackets/braces,
embedded quote, leading/trailing whitespace, empty vs null, flat list w/ colon
item, empty list, ``True-Name`` stays string, int, depth-1 nested map.

AIPOS-F100: the subset also covers block scalars ``|``/``>`` (clip/strip/keep, explicit indentation), one-line
flow sequences/mappings of scalars and multi-line plain scalars (hand-written governance history), and the
record_writer stdlib emitter writes every inventory shape back as block YAML (shape table, both directions).

AIPOS-F98 fail-closed: structures outside the supported subset raise
FrontmatterUnsupportedError (key path + line) and parse_markdown_frontmatter returns an empty
mapping plus a warning — never a key silently parsed to None; product readers surface it.

yaml.safe_load is the reference oracle: these tests require PyYAML in the TEST environment
(the product runtime does not) and fail — never skip — when it is missing.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_TASK = REPO_ROOT / "examples" / "sample_workspace" / "5_tasks" / "queue" / "pending" / "sample_task.md"

# Capture yaml.safe_load BEFORE blocking it.
try:
    import yaml as _real_yaml
    _HAS_YAML = True
except ImportError:
    _real_yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


class _BlockYaml:
    """sys.meta_path finder that raises ImportError for the yaml module."""

    def find_spec(self, name: str, path=None, target=None):  # type: ignore[override]
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("yaml blocked for AIPOS-218 WS5 bare-python test")
        return None


def _extract_frontmatter_text(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return ""


def _yaml_baseline(fm_text: str) -> dict:
    if not _HAS_YAML or _real_yaml is None:
        return {}
    result = _real_yaml.safe_load(fm_text)
    return result if isinstance(result, dict) else {}


def _run_with_yaml_blocked(fn):
    """Execute fn() with yaml import blocked; restore sys.modules state after."""
    blocker = _BlockYaml()
    # Remove yaml from sys.modules so the module under test re-imports it (and fails).
    saved = sys.modules.pop("yaml", None)
    sys.meta_path.insert(0, blocker)
    # Also remove the frontmatter module from sys.modules so it re-evaluates `yaml = None`.
    # Actually frontmatter.py does `try: import yaml` at module level, so if it's already
    # imported we need to patch the module attribute directly.
    import tools.aipos_cli.frontmatter as _fm_mod
    saved_yaml_attr = _fm_mod.yaml
    _fm_mod.yaml = None  # type: ignore[assignment]
    try:
        return fn()
    finally:
        sys.meta_path.remove(blocker)
        if saved is not None:
            sys.modules["yaml"] = saved
        _fm_mod.yaml = saved_yaml_attr


def _require_oracle(test: unittest.TestCase) -> None:
    """yaml.safe_load is the parity oracle; a missing oracle is a FAILURE (never a skip / vacuous pass)."""
    test.assertTrue(_HAS_YAML, "PyYAML is required in the test environment as the yaml.safe_load parity oracle")


def _real_yaml_or_empty(fm_text: str) -> dict:
    """Keys yaml.safe_load gives (empty when it fails) — used to assert no key appears as None that YAML lacks."""
    try:
        loaded = _real_yaml.safe_load(fm_text) if _real_yaml is not None else None
    except (_real_yaml.YAMLError, ValueError):  # oracle failure (incl. bad timestamp) means "no keys"
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_markdown(md_text: str):
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    return parse_markdown_frontmatter(md_text)


class FrontmatterZerodepParityTests(unittest.TestCase):
    """Parity: fallback_parse == yaml.safe_load for every real-world sample."""

    def setUp(self) -> None:
        _require_oracle(self)

    def _assert_parity(self, label: str, md_text: str) -> None:
        fm_text = _extract_frontmatter_text(md_text)
        if not fm_text:
            self.fail(f"{label}: no frontmatter found")

        baseline = _yaml_baseline(fm_text)

        def _parse_fallback():
            from tools.aipos_cli.frontmatter import _fallback_parse
            return _fallback_parse(fm_text)

        data, warnings = _run_with_yaml_blocked(_parse_fallback)

        self.assertEqual(
            data,
            baseline,
            f"{label}: fallback parse differs from yaml.safe_load baseline.\n"
            f"  baseline: {baseline}\n  fallback: {data}",
        )
        # Warnings must be empty for writer-emitted content.
        self.assertEqual(warnings, [], f"{label}: unexpected fallback warnings: {warnings}")

    def test_sample_task_card(self) -> None:
        self.assertTrue(SAMPLE_TASK.exists(), f"sample_task.md not found at {SAMPLE_TASK}")
        self._assert_parity("sample_task_card", SAMPLE_TASK.read_text(encoding="utf-8"))

    def test_return_record_with_artifact_refs(self) -> None:
        from tools.aipos_cli.record_writer import build_mcp_return_record_markdown
        md = build_mcp_return_record_markdown(
            task_id="AIPOS-WS5-001",
            task_path="5_tasks/queue/claimed/aipos-ws5-001.md",
            actor="agent-01",
            canonical_agent_instance="agent-01",
            owner_policy_ref="DL-20260625-01",
            return_id="return-ws5-001",
            claim_id="claim-ws5-001",
            session_id="session-ws5-001",
            returned_at="2026-06-25T00:01:00Z",
            result_summary="Fix: completed the colon-value task",
            artifact_refs=["5_tasks/records/returns/r1.md", "docs/out #2.md"],
            completion_report_ref="5_tasks/records/returns/ws5/completion.md",
        )
        self._assert_parity("return_record_with_artifacts", md)

    def test_audit_verdict_record_with_bool_and_evidence(self) -> None:
        from tools.aipos_cli.record_writer import build_mcp_audit_verdict_record_markdown
        md = build_mcp_audit_verdict_record_markdown(
            verdict_id="verdict-ws5-001",
            verdict="PASS",
            reviewed_task_id="AIPOS-WS5-001",
            reviewed_task_path="5_tasks/queue/completed/aipos-ws5-001.md",
            reviewed_return_record_ref="5_tasks/records/returns/r1.md",
            audit_dispatch_record_ref="5_tasks/records/audit_dispatches/d1.md",
            audit_task_id="AIPOS-WS5-AUDIT",
            audit_task_path="5_tasks/queue/completed/aipos-ws5-audit.md",
            audit_claim_id="claim-ws5-002",
            audit_session_id="session-ws5-002",
            reviewed_executor_instance="agent-01",
            auditor_instance="agent-02",
            actor="agent-02",
            canonical_agent_instance="agent-02",
            owner_policy_ref="DL-20260625-01",
            verdict_at="2026-06-25T00:03:00Z",
            findings_summary="No issues found: all tests green.",
            evidence_refs=["5_tasks/records/sessions/s2.md"],
            recommended_next_action="finalize",
        )
        self._assert_parity("audit_verdict_with_bool_and_evidence", md)

    def test_publish_record(self) -> None:
        import tempfile
        from tools.aipos_cli.draft_writer import create_draft, publish_draft

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo_root = Path(td.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (repo_root / "5_tasks" / "queue" / state).mkdir(parents=True)
        # AIPOS-343: active policies so contract section can resolve envelopes
        policies_dir = repo_root / "5_tasks" / "policies"
        policies_dir.mkdir(parents=True, exist_ok=True)
        (policies_dir / "pol_lybra_dev_7.md").write_text(
            "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\n---\n# Dev\n",
            encoding="utf-8",
        )
        metadata = {
            "task_id": "AIPOS-WS5-PUB",
            "title": "WS5 publish parity test",
            "project": "lybra",
            # AIPOS-F102 件②: 卡角色类不可解析 = 拒发布; 夹具用注册表角色的实例名(原 dev_claude/agent-01 无角色类)
            "assigned_to": "exec.lybra.test",
            "agent_instance": "exec.lybra.test",
            "context_bundle": "default",
            "task_mode": "code",
            "priority": "medium",
            "status": "pending",
            "created_by": "tester",
            "needs_owner": False,
            "artifact_policy": "formal_write",
            "model_tier": "L2",
            "output_target": "tools/",
        }
        draft = create_draft(repo_root, metadata, "WS5 test body.")
        self.assertNotEqual(draft.get("verdict"), "BLOCK", draft)
        self.assertTrue(draft.get("wrote"), draft)
        draft_path = repo_root / draft["target_path"]
        result = publish_draft(repo_root, draft_path, actor="tester", dry_run=False)
        self.assertNotEqual(result.get("verdict"), "BLOCK", result)
        task_path = repo_root / result["target_path"]
        md = task_path.read_text(encoding="utf-8")
        self._assert_parity("publish_record", md)
        # AIPOS-F98: the published card carries the two-level lane map; without PyYAML it must survive intact
        data, _body, warnings = _run_with_yaml_blocked(lambda: _parse_markdown(md))
        self.assertEqual(warnings, [])
        self.assertIsInstance(data.get("lane"), dict, data.get("lane"))
        self.assertEqual(data["lane"].get("paths"), ["tools/"])
        # AIPOS-F102 件②: 卡角色类可解析(exec 实例 → executor), lane.roles 派生为 [executor](空列表形状由逐形状夹具覆盖)
        self.assertEqual(data["lane"].get("roles"), ["executor"])


class FrontmatterZerodepAdversarialTests(unittest.TestCase):
    """Adversarial value coverage: colon, #, brackets, quote, whitespace, int, bool string."""

    def setUp(self) -> None:
        _require_oracle(self)

    def _parse(self, fm_text: str):
        def _go():
            from tools.aipos_cli.frontmatter import _fallback_parse
            return _fallback_parse(fm_text)
        return _run_with_yaml_blocked(_go)

    def _both(self, fm_text: str):
        """Return (fallback_data, yaml_data) — yaml_data is {} if not available."""
        data, warnings = self._parse(fm_text)
        return data, _yaml_baseline(fm_text), warnings

    def test_colon_in_quoted_value(self) -> None:
        fm = "title: 'Fix: thing'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("title"), "Fix: thing")
        self.assertEqual(data, baseline)

    def test_iso_timestamp_quoted(self) -> None:
        fm = "created_at: '2026-06-25T12:00:00Z'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("created_at"), "2026-06-25T12:00:00Z")
        self.assertEqual(data, baseline)

    def test_hash_in_quoted_value(self) -> None:
        fm = "ref: 'docs/output #2.md'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("ref"), "docs/output #2.md")
        self.assertEqual(data, baseline)

    def test_embedded_single_quote(self) -> None:
        fm = "name: 'o''brien'"
        data, _ = self._parse(fm)
        self.assertEqual(data.get("name"), "o'brien")

    def test_bool_coercion(self) -> None:
        fm = "enabled: true\ndisabled: false"
        data, baseline, _ = self._both(fm)
        self.assertIs(data.get("enabled"), True)
        self.assertIs(data.get("disabled"), False)
        self.assertEqual(data, baseline)

    def test_true_name_stays_string(self) -> None:
        fm = "key: True-Name"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("key"), "True-Name")
        self.assertEqual(data, baseline)

    def test_int_coercion(self) -> None:
        fm = "event_count: 42"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("event_count"), 42)
        self.assertEqual(data, baseline)

    def test_empty_vs_null(self) -> None:
        fm = "empty_key:"
        data, baseline, _ = self._both(fm)
        self.assertIsNone(data.get("empty_key"))
        self.assertEqual(data, baseline)

    def test_empty_list(self) -> None:
        fm = "refs: []"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("refs"), [])
        self.assertEqual(data, baseline)

    def test_flat_list_with_colon_item(self) -> None:
        fm = "refs:\n  - 'Fix: item'\n  - plain"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("refs"), ["Fix: item", "plain"])
        self.assertEqual(data, baseline)

    def test_depth_1_nested_map(self) -> None:
        fm = "output_policy:\n  overwrite_existing_files: false\n  remote_fetch_allowed: false"
        data, baseline, warnings = self._both(fm)
        self.assertEqual(
            data.get("output_policy"),
            {"overwrite_existing_files": False, "remote_fetch_allowed": False},
        )
        self.assertEqual(warnings, [])
        self.assertEqual(data, baseline)

    def test_leading_trailing_whitespace_in_value(self) -> None:
        fm = "title:   spaced value   "
        data, _ = self._parse(fm)
        # scalar strips surrounding whitespace
        self.assertEqual(data.get("title"), "spaced value")

    def test_brackets_braces_in_quoted_value(self) -> None:
        fm = "spec: '[a] {b}'"
        data, baseline, _ = self._both(fm)
        self.assertEqual(data.get("spec"), "[a] {b}")
        self.assertEqual(data, baseline)


# AIPOS-F98 件①: shape inventory — every frontmatter shape the product's writers emit (card.schema fields /
# runtime_fields, transitions record declarations, record_writer.render_markdown output via yaml.safe_dump
# default_flow_style=False or the stdlib emitter) plus the hand-written forms found in a real governance root.
# Each entry: (shape id, frontmatter text). Fallback result must equal yaml.safe_load, with no exception.
SHAPE_INVENTORY: list[tuple[str, str]] = [
    ("S1 top-level plain scalars (str/int/float/bool/null/empty)",
     "task_id: AIPOS-F98\nround: 2\nratio: 1.5\nneeds_owner: false\nowner_verify: true\nclaim_id: null\nnote:\nsha: 1e5\n"
     "short: 0123\ntilde: ~\nyes_word: yes\nweird_case: tRUE\nnum_us: 1_000"),
    ("S2 single-quoted scalars ('' escape, colon, #, brackets, leading digit/date)",
     "title: 'Fix: thing #2 [a] {b}'\nname: 'o''brien'\ncreated_at: '2026-10-04T10:19:01Z'\nlit_null: 'null'\nlit_int: '42'\nempty: ''"),
    ("S3 double-quoted scalars (stdlib emitter json.dumps form, full escape set)",
     'a: "中文: 值"\nb: "tab\\tnl\\ncr\\rbs\\\\q\\"sl\\/bell\\a"\nc: "\\u00e9\\x41\\U0001F600"\nd: ""'),
    ("S4 multi-line quoted scalars (safe_dump single-quoted with line breaks; double-quoted escaped break)",
     "summary: 'line one\n\n  line two\n\n\n  line three'\nfolded: 'a\n  b'\ndq: \"x \\\n  y\"\nafter: z"),
    ("S5 unquoted timestamps (hand-written cards/RETURN)",
     "d: 2026-10-04\ndt: 2026-10-04T10:19:01Z\ndto: 2026-10-04 10:19:01.25 +08:00\nnot_ts: 2026-1-4"),
    ("S6 top-level block list of scalars, indentless (safe_dump style)",
     "governance_refs:\n- '★依据: x'\n- plain item\n- 'Fix: item'\nnext: 1"),
    ("S7 block list of scalars, indented (manifest style) + null item",
     "refs:\n  - a\n  - 'b: c'\n  -\nafter: 1"),
    ("S8 empty flow collections [] / {}",
     "artifact_refs: []\nreported_tokens: {}\nspaced: [ ]\nspaced_map: { }"),
    ("S9 map in map, depth 1 (template output_policy / reported_tokens)",
     "output_policy:\n  overwrite_existing_files: false\n  remote_fetch_allowed: false\n  limit: 3\nnext: x"),
    ("S10 map containing list and empty list — card lane (the AIPOS-F98 defect shape)",
     "harness: claude-code\nlane:\n  repo: /home/u/projects/lybra\n  paths:\n  - tools/aipos_cli/frontmatter.py\n  - tests/\n  roles:\n  - executor\nowner_verify: true"),
    ("S11 lane with empty roles [] (publish output) and indented list form",
     "lane:\n  repo: /tmp/x\n  paths:\n    - tools/\n  roles: []\nharness: pi"),
    ("S12 sequence of mappings with nested list (card rework_rounds)",
     "rework_rounds:\n- round: 1\n  verdict_ref: 5_tasks/records/audit_verdicts/x.md\n  created_at: '2026-10-04T10:00:00Z'\n"
     "  focus_items:\n  - 'item: one'\n  - item two\n  acceptance_criteria: all green\n- round: 2\n  focus_items: []\n"
     "  cleared_at: null\nstatus: claimed"),
    ("S13 deeper nesting (map/seq/map depth 3) and compact nested sequence",
     "a:\n  b:\n    c:\n    - d: 1\n      e:\n      - f\n    - - g\n      - h\nz: end"),
    ("S14 comments (full-line, indented, trailing) and blank lines",
     "# header\na: b # trailing\n\n   # indented comment\nc: 'q' # after quoted\nd:\n  # inside block\n  - x # item comment\n"),
    ("S15 quoted keys and non-string plain keys (YAML resolution)",
     "'quoted key': 1\n\"dq key\": 2\n1: int key\nnull_like: ~"),
    ("S16 whole frontmatter indented (top-level mapping at indent 2)",
     "  a: 1\n  b:\n  - c"),
    ("S17 empty frontmatter / comment-only",
     "# nothing here"),
    # AIPOS-F100 件③: hand-written history files in a real governance root (block scalars, one-line flow
    # collections of scalars, multi-line plain scalars) — same result as yaml.safe_load, no longer refused
    ("S18 literal block scalar | (clip) / |- (strip) / |+ (keep), trailing comment header, more-indented and blank inner lines",
     "a: |\n  line one\n    indented\n\n  # not a comment\nb: |-\n  stripped\n\nc: |+ # keep\n  kept\n\n\nd: end"),
    ("S19 folded block scalar > / >- with paragraph breaks and more-indented lines; explicit indentation indicator |2",
     "f: >\n  one\n  two\n\n  three\n    four\n  five\ng: >-\n  folded\n  strip\nh: |2\n    two extra\n  base\ni: end"),
    ("S20 block scalars inside sequences and compact mappings (hand-written drafts: owner_verify_checklist / rework focus)",
     "owner_verify_checklist: |\n  - 守护在跑\n  - 额度尽 BLOCK\nrework_rounds:\n- round: 1\n  acceptance_criteria: >\n    all\n    green\n  focus_items:\n  - |\n    item\n- round: 2\nz: 1"),
    ("S21 block scalar at end of frontmatter (no final line break) and empty block scalar before the next key",
     "empty: |\nnext: x\nlast: |\n  tail line"),
    ("S22 one-line flow sequence of scalars (plain / quoted / resolved), trailing comma, comment",
     "severities: [P2, P2, P2]\nmixed: [1, 2.5, true, null, ~, 2026-10-04, '', 'a, b', \"c\\td\", a:b, http://x, -x] # c\nspaced: [ x , y y ,]\nnested_in_seq:\n- [a, b]\n- c"),
    ("S23 one-line flow mapping of scalars (hand-written reported_tokens), quoted keys, key without value",
     "reported_tokens: {input: 75793, output: 15000, total: 90793}\nq: {'k': v, \"x y\": 2, solo}\nlane:\n  meta: {a: 1}"),
    ("S24 multi-line plain scalars: map value, sequence items (hand-written governance_refs), value on its own line below the key, blank-line paragraph",
     "title: first part\n  second part\ngovernance_refs:\n  - 蓝本=lybra-dev-auditor(行为契约:watch→领卡→\n    BLOCK 于额度尽)\n  - single\nbelow:\n  own line\n  continued\n\n  new paragraph\nz: 1"),
]

# hostile string values a writer may be handed; each must survive safe_dump → fallback and stdlib emitter → fallback
HOSTILE_STRINGS = [
    "Fix: colon", "#hash", "a #b", "[x]", "{y}", "'single'", '"double"', "o'brien", "  lead", "trail  ",
    "multi\nline", "blank\n\nlines", " both \n ", "tab\there", "yes", "No", "on", "null", "~", "", "True-Name",
    "1e5", "1.5e5", "0x1F", "017", "08", "1_000", "1:30", ".inf", "-.5", "2026-10-04", "2026-10-04T10:19:01Z",
    "中文：值", "emoji 😀", "- dash", "? q", ": c", "@at", "`tick", "%pct", "&amp", "*star", "!bang", "|pipe", ">gt",
    "a: b: c", "\\back\\slash", "ctrl\x01char", "nel\x85x", "\u2028sep",
]


class FrontmatterZerodepShapeInventoryTests(unittest.TestCase):
    """AIPOS-F98 件①: per-shape parity fallback == yaml.safe_load, and writer output round-trips."""

    def setUp(self) -> None:
        _require_oracle(self)

    def _fallback(self, fm_text: str):
        def _go():
            from tools.aipos_cli.frontmatter import _fallback_parse
            return _fallback_parse(fm_text)
        return _run_with_yaml_blocked(_go)

    def test_every_inventory_shape_matches_safe_load(self) -> None:
        for shape_id, fm_text in SHAPE_INVENTORY:
            with self.subTest(shape=shape_id):
                expected = _real_yaml.safe_load(fm_text) or {}
                data, warnings = self._fallback(fm_text)
                self.assertEqual(data, expected, f"{shape_id}\n  safe_load: {expected!r}\n  fallback:  {data!r}")
                self.assertEqual(warnings, [])

    def test_two_level_lane_is_never_none(self) -> None:
        """The reported defect: lane: {repo, paths: [..], roles: []} parsed to None + warning without PyYAML."""
        fm = "lane:\n  repo: /tmp/x\n  paths:\n  - tools/\n  roles: []\nharness: pi"
        data, warnings = self._fallback(fm)
        self.assertEqual(data, {"lane": {"repo": "/tmp/x", "paths": ["tools/"], "roles": []}, "harness": "pi"})
        self.assertEqual(warnings, [])

    def test_safe_dump_of_product_shaped_metadata_roundtrips(self) -> None:
        """record_writer's PyYAML path (safe_dump sort_keys=False, allow_unicode, block style, no wrapping)."""
        for value in HOSTILE_STRINGS:
            meta = {
                "task_id": "AIPOS-X",
                "title": value,
                "governance_refs": [value, "plain"],
                "lane": {"repo": value, "paths": [value, "tests/"], "roles": []},
                "rework_rounds": [{"round": 1, "focus_items": [value], "acceptance_criteria": value}],
                "reported_tokens": {},
                "needs_owner": False,
            }
            fm_text = _real_yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000000)
            with self.subTest(value=value):
                data, warnings = self._fallback(fm_text)
                # oracle = safe_load (PyYAML itself folds a raw NEL \x85 into a space; the writer's
                # read-back check refuses such values — parity with the oracle is what is asserted here)
                self.assertEqual(data, _real_yaml.safe_load(fm_text))
                self.assertEqual(warnings, [])

    def test_stdlib_emitter_output_roundtrips(self) -> None:
        """record_writer's no-PyYAML emitter (json.dumps double-quoted scalars, flat lists, depth-1 maps)."""
        import tools.aipos_cli.record_writer as rw

        saved = rw.yaml
        rw.yaml = None  # type: ignore[assignment]
        try:
            for value in HOSTILE_STRINGS:
                meta = {"title": value, "refs": [value, "x"], "policy": {"note": value, "flag": True, "n": 3}, "empty": []}
                fm_text = rw._dump_frontmatter_yaml(meta)
                with self.subTest(value=value):
                    data, warnings = self._fallback(fm_text)
                    # oracle = safe_load (PyYAML itself folds a raw NEL \x85 into a space; the writer's
                    # read-back check refuses such values — parity with the oracle is what is asserted here)
                    self.assertEqual(data, _real_yaml.safe_load(fm_text))
                    self.assertEqual(warnings, [])
        finally:
            rw.yaml = saved


def _with_writer_yaml(enabled: bool, fn):
    """record_writer + frontmatter both with (enabled) or without PyYAML (blocked: sys.meta_path finder + module attrs)."""
    import tools.aipos_cli.record_writer as rw

    if enabled:
        return fn()
    saved = rw.yaml
    rw.yaml = None  # type: ignore[assignment]
    try:
        return _run_with_yaml_blocked(fn)
    finally:
        rw.yaml = saved


class StdlibWriterShapeTableTests(unittest.TestCase):
    """AIPOS-F100 件①: the record_writer stdlib emitter (PyYAML absent) writes every SHAPE_INVENTORY shape as block YAML
    and the same table drives both directions:
      read → write → read: shape text --fallback--> value --render_markdown(stdlib)--> text --fallback--> same value;
      write → read: value --render_markdown(stdlib)--> text, read back by the fallback AND by yaml.safe_load (oracle) =
      value; the safe_dump path's output reads back to the same value (render_markdown's own write-back check passes
      on both paths, i.e. nothing is refused)."""

    def setUp(self) -> None:
        _require_oracle(self)

    def _render(self, value: dict, *, with_yaml: bool) -> str:
        import tools.aipos_cli.record_writer as rw

        # explicit order = the value's own key order (render_markdown sorts unlisted keys; S15 mixes int/str keys)
        return _with_writer_yaml(with_yaml, lambda: rw.render_markdown(value, "body", order=list(value)))

    def test_every_inventory_shape_round_trips_through_both_writers(self) -> None:
        import tools.aipos_cli.record_writer as rw

        for shape_id, fm_text in SHAPE_INVENTORY:
            with self.subTest(shape=shape_id):
                read_back, warnings = _run_with_yaml_blocked(lambda: __import__(
                    "tools.aipos_cli.frontmatter", fromlist=["_fallback_parse"])._fallback_parse(fm_text))
                self.assertEqual(warnings, [])
                expected = rw._normalize_value(read_back)  # writer contract: date/datetime are written as ISO strings
                stdlib_md = self._render(read_back, with_yaml=False)
                dump_md = self._render(read_back, with_yaml=True)
                stdlib_fm = _extract_frontmatter_text(stdlib_md)
                data, _body, w = _run_with_yaml_blocked(lambda: _parse_markdown(stdlib_md))
                self.assertEqual(w, [], stdlib_md)
                self.assertEqual(data, expected, f"{shape_id}\n{stdlib_md}")
                self.assertEqual(_real_yaml.safe_load(stdlib_fm) or {}, expected, f"oracle on stdlib output\n{stdlib_md}")
                data2, _b2, w2 = _run_with_yaml_blocked(lambda: _parse_markdown(dump_md))
                self.assertEqual((data2, w2), (expected, []), f"fallback on safe_dump output\n{dump_md}")
                if read_back:  # non-empty: the stdlib text is block YAML, never a stringified container
                    self.assertNotIn("\"[", stdlib_fm)
                    self.assertNotIn("\"{", stdlib_fm)

    def test_product_shaped_hostile_values_never_refused_without_pyyaml(self) -> None:
        """F98 gap #1: lane.paths / rework_rounds were stringified and refused by the write-back check."""
        import tools.aipos_cli.record_writer as rw

        for value in HOSTILE_STRINGS:
            meta = {
                "task_id": "AIPOS-X", "title": value, "governance_refs": [value, "plain"],
                "lane": {"repo": value, "paths": [value, "tests/"], "roles": []},
                "rework_rounds": [{"round": 1, "focus_items": [value], "acceptance_criteria": value, "nested": {"k": [value]}}],
                "reported_tokens": {}, "needs_owner": False, "matrix": [[value, 1], []], "ratio": 1e16, "neg": -0.5,
                value or "empty-key": value,
            }
            with self.subTest(value=value):
                md = self._render(meta, with_yaml=False)
                data, _body, warnings = _run_with_yaml_blocked(lambda: _parse_markdown(md))
                self.assertEqual((data, warnings), (meta, []))
                self.assertEqual(_real_yaml.safe_load(_extract_frontmatter_text(md)), meta)

    def test_stdlib_writer_refuses_types_it_cannot_write(self) -> None:
        import tools.aipos_cli.record_writer as rw

        for bad in ((1, 2), {1, 2}, b"raw", object()):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(ValueError):
                    self._render({"k": bad}, with_yaml=False)
        with self.assertRaises(ValueError):
            _with_writer_yaml(False, lambda: rw._dump_frontmatter_yaml({("tuple", "key"): 1}))

    def test_publish_without_pyyaml_lands_the_card(self) -> None:
        """F98 gap #1 reproduction: create_draft + publish_draft with PyYAML blocked for reader and writer."""
        import tempfile
        from tools.aipos_cli.draft_writer import create_draft, publish_draft

        cards = {}
        for with_yaml in (False, True):
            td = tempfile.TemporaryDirectory()
            self.addCleanup(td.cleanup)
            root = Path(td.name)
            for state in ("pending", "claimed", "completed", "blocked"):
                (root / "5_tasks" / "queue" / state).mkdir(parents=True)
            (root / "5_tasks" / "policies").mkdir(parents=True)
            (root / "5_tasks" / "policies" / "pol_lybra_dev_7.md").write_text(
                "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\n---\n# Dev\n", encoding="utf-8")
            meta = {"task_id": "AIPOS-F100-PUB", "title": "零依赖发布: 冒号 #号", "project": "lybra", "assigned_to": "dev_claude",
                    "agent_instance": "agent-01", "context_bundle": "default", "task_mode": "code", "priority": "medium",
                    "status": "pending", "created_by": "tester", "needs_owner": False, "artifact_policy": "formal_write",
                    "model_tier": "L2", "output_target": "tools/", "governance_refs": ["★依据: x", "Fix: 'q'"]}

            def _go(root=root, meta=meta):
                draft = create_draft(root, meta, "零依赖发布正文。")
                self.assertTrue(draft.get("wrote"), draft)
                return publish_draft(root, root / draft["target_path"], actor="tester", dry_run=False)

            result = _with_writer_yaml(with_yaml, _go)
            self.assertNotEqual(result.get("verdict"), "BLOCK", result)
            md = (root / result["target_path"]).read_text(encoding="utf-8")
            data, _body, warnings = _run_with_yaml_blocked(lambda: _parse_markdown(md))
            self.assertEqual(warnings, [])
            cards[with_yaml] = {k: v for k, v in data.items() if not k.endswith(("_at", "_sha256"))}
        self.assertEqual(cards[False]["lane"]["paths"], ["tools/"])
        lane_false, lane_true = cards[False].pop("lane"), cards[True].pop("lane")
        self.assertEqual({k: v for k, v in lane_false.items() if k != "repo"}, {k: v for k, v in lane_true.items() if k != "repo"})
        self.assertEqual(cards[False], cards[True])


# AIPOS-F98 件②: what the zero-dependency parser does not parse is never guessed and never a key silently None.
#   - valid YAML outside the subset → FrontmatterUnsupportedError(yaml_invalid=False); parse_markdown_frontmatter
#     returns {} + one warning (no fields at all: a partial mapping would silently diverge from the YAML);
#   - a YAML error (yaml.safe_load fails too) → FrontmatterUnsupportedError(yaml_invalid=True); parse_markdown_frontmatter
#     drops only the broken top-level entry (absent, not None) + a warning naming key and file line — exactly what the
#     PyYAML-present path returns for the same text (PyYAML fails, the same parser salvages), so lint FRONTMATTER_INVALID
#     and my-tasks still attribute the bad card.
# Each entry: (case, frontmatter text, key path, frontmatter line, yaml_invalid, salvaged mapping or None = whole refusal).
REJECTION_CASES: list[tuple[str, str, str, int, bool, dict | None]] = [
    # AIPOS-F100: one-line flow collections of scalars and block scalars are supported now (S18–S23); R1–R4 / R7
    # name the constructs that stay outside the subset
    ("R1 nested flow collection inside lane", "task_id: T\nlane:\n  repo: /tmp/x\n  paths: [[tools/], tests/]\nharness: pi",
     "lane.paths", 4, False, None),
    ("R2 flow mapping with a nested flow value", "task_id: T\nlane: {repo: [/tmp/x]}", "lane", 2, False, None),
    ("R3 flow sequence continued on the next line", "title: x\nbody: [a,\n  b]", "body", 2, False, None),
    ("R4 mapping pair inside a flow sequence (seq-of-maps)", "rework_rounds:\n- round: 1\n  focus_items: [a: b]",
     "rework_rounds[0].focus_items", 3, False, None),
    ("R5 anchor", "a: &anc 1", "a", 1, False, None),
    ("R6 tag", "a: !!str 1", "a", 1, False, None),
    ("R7 block scalar indicator on its own line below the key", "body:\n  |\n  text", "body", 2, False, None),
    ("R8 merge key", "<<: {}", "", 1, False, None),
    ("R9 complex key", "? a\n: b", "", 1, False, None),
    ("R10 sequence document", "- a\n- b", "", 1, False, None),
    ("R11 tab inside a comment", "a: 1 #\tnote", "a", 1, False, None),
    ("R12 alias without anchor (F87 real bad-card pattern)", "task_id: T\nresult_summary: **完成**: x\nstatus: claimed",
     "result_summary", 2, True, {"task_id": "T", "status": "claimed"}),
    ("R13 mapping value inside plain scalar", "lane:\n  repo: a: b\nharness: pi", "lane.repo", 2, True, {"harness": "pi"}),
    ("R14 unexpected indentation (stray line drops the entry before it)", "a:\n    b: 1\n  c: 2\nd: 3", "a", 3, True, {"d": 3}),
    ("R15 tab indentation", "lane:\n\trepo: x\nh: 1", "lane", 2, True, {"h": 1}),
    ("R16 unterminated quoted scalar", "a: 1\nb: 'open", "b", 2, True, {"a": 1}),
    ("R17 unknown double-quoted escape", 'a: "bad \\q"\nb: 1', "a", 1, True, {"b": 1}),
    ("R18 text after quoted scalar", "a: 'x' y\nb: 1", "a", 1, True, {"b": 1}),
    ("R19 list item where a key is expected", "lane:\n  repo: x\n  - y\nh: 1", "lane", 3, True, {"h": 1}),
    ("R20 invalid timestamp", "d: 2026-13-01\ne: 1", "d", 1, True, {"e": 1}),
    ("R21 non-printable character", "a: 1\nb: x\x01y", "", 2, True, None),
]


class FrontmatterZerodepFailClosedTests(unittest.TestCase):
    """AIPOS-F98 件②: fail-closed rejection with key path + line, and product readers surface it."""

    def test_fallback_raises_with_key_path_line_and_class(self) -> None:
        from tools.aipos_cli.frontmatter import FrontmatterUnsupportedError, _fallback_parse

        for case, fm_text, key_path, line_no, yaml_invalid, _salvaged in REJECTION_CASES:
            with self.subTest(case=case):
                with self.assertRaises(FrontmatterUnsupportedError) as ctx:
                    _run_with_yaml_blocked(lambda: _fallback_parse(fm_text))
                self.assertEqual(ctx.exception.key_path, key_path, str(ctx.exception))
                self.assertEqual(ctx.exception.line_no, line_no, str(ctx.exception))
                self.assertIs(ctx.exception.yaml_invalid, yaml_invalid, str(ctx.exception))

    def test_yaml_invalid_class_agrees_with_oracle(self) -> None:
        """yaml_invalid=True only where yaml.safe_load fails too (salvage never hides a value YAML would give)."""
        _require_oracle(self)
        for case, fm_text, _key, _line, yaml_invalid, _salvaged in REJECTION_CASES:
            with self.subTest(case=case):
                try:
                    _real_yaml.safe_load(fm_text)
                    oracle_failed = False
                except (_real_yaml.YAMLError, ValueError):  # YAML error, incl. ValueError from a bad timestamp
                    oracle_failed = True
                if yaml_invalid:
                    self.assertTrue(oracle_failed, f"{case}: classified as YAML error but yaml.safe_load accepts it")
                else:
                    self.assertFalse(oracle_failed, f"{case}: valid-YAML case expected")

    def test_parse_markdown_refuses_or_drops_only_the_broken_entry(self) -> None:
        for case, fm_text, key_path, line_no, _yaml_invalid, salvaged in REJECTION_CASES:
            md = f"---\n{fm_text}\n---\nbody\n"
            with self.subTest(case=case):
                data, body, warnings = _run_with_yaml_blocked(lambda: _parse_markdown(md))
                self.assertEqual(body, "body")
                self.assertTrue(warnings, "the rejection must reach the caller as a warning")
                self.assertIn(f"file line {line_no + 1}", warnings[0])
                self.assertIn(f"key {key_path or '<root>'}", warnings[0])
                if salvaged is None:
                    self.assertEqual(data, {}, "fail-closed: no fields for valid-but-unsupported YAML")
                    self.assertEqual(len(warnings), 1, warnings)
                    self.assertIn("fail-closed, no fields returned", warnings[0])
                else:
                    self.assertEqual(data, salvaged)
                    self.assertIn("Frontmatter key dropped (YAML error", warnings[0])
                    top_key = key_path.split(".")[0].split("[")[0]
                    if top_key:
                        self.assertNotIn(top_key, data, "the broken entry is absent, never None")
                self.assertNotIn(None, [v for k, v in data.items() if k not in _real_yaml_or_empty(fm_text)])

    def test_yaml_present_and_absent_agree_on_yaml_errors(self) -> None:
        """For a document PyYAML rejects, both paths return the same salvaged mapping (PyYAML warning first)."""
        _require_oracle(self)
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

        for case, fm_text, _key, _line, yaml_invalid, salvaged in REJECTION_CASES:
            if not yaml_invalid:
                continue
            md = f"---\n{fm_text}\n---\nbody\n"
            with self.subTest(case=case):
                with_yaml, _b1, w1 = parse_markdown_frontmatter(md)
                without_yaml, _b2, w2 = _run_with_yaml_blocked(lambda: _parse_markdown(md))
                self.assertEqual(with_yaml, without_yaml)
                self.assertEqual(with_yaml, {} if salvaged is None else salvaged)
                self.assertTrue(w1[0].startswith("PyYAML parse failed"), w1)
                self.assertEqual(w1[1:], w2)

    def test_task_loader_surfaces_rejection(self) -> None:
        import tempfile
        from tools.aipos_cli.task_loader import load_task_file

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo_root = Path(td.name)
        card = repo_root / "5_tasks" / "queue" / "claimed" / "aipos-bad.md"
        card.parent.mkdir(parents=True)
        card.write_text("---\ntask_id: AIPOS-BAD\nlane:\n  repo: /tmp/x\n  paths: [[a], b]\n---\nbody\n", encoding="utf-8")
        task = _run_with_yaml_blocked(lambda: load_task_file(card, repo_root))
        self.assertTrue(task["parse_errors"], task)
        self.assertIn("lane.paths", task["parse_errors"][0])

    def test_state_lint_frontmatter_invalid_criterion_fires(self) -> None:
        from tools.aipos_cli.state_lint import card_frontmatter_warnings

        bad = "---\ntask_id: AIPOS-BAD\nlane:\n  repo: /tmp/x\n  paths: [[a], b]\n---\nbody\n"
        good = "---\ntask_id: AIPOS-OK\nlane:\n  repo: /tmp/x\n  paths:\n  - a\n  roles: []\n---\nbody\n"
        self.assertTrue(_run_with_yaml_blocked(lambda: card_frontmatter_warnings(bad)))
        self.assertEqual(_run_with_yaml_blocked(lambda: card_frontmatter_warnings(good)), [])

    def test_writer_self_check_fails_closed_without_pyyaml(self) -> None:
        """record_writer._self_check_yaml (no-PyYAML branch) turns a rejection into a refusal to write."""
        import tools.aipos_cli.record_writer as rw

        saved = rw.yaml
        rw.yaml = None  # type: ignore[assignment]
        try:
            with self.assertRaises(ValueError):
                _run_with_yaml_blocked(lambda: rw._self_check_yaml("a: [[x], y]", {"a": [["x"], "y"]}))
        finally:
            rw.yaml = saved


if __name__ == "__main__":
    unittest.main()
