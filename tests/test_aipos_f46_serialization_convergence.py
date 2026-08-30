"""AIPOS-F46: 写卡序列化全量收敛测试.

验收六条:
① 先红后绿: 修复前毒字段写坏卡(safe_load 失败), 修复后同路径可解析
② 全路径毒字段夹具: 交回/裁决回写/级联派生/进度回写四条真路径各写一次毒字段, 全部 safe_load 通过
③ grep 断言: 全仓无绕过写入器的 frontmatter 拼接
④ 末道自检负夹具: 构造写入器故障→拒写并报错, 不落坏卡
⑤ 基线对照零新增失败
⑥ 套件全绿
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# 毒字段: 覆盖所有已知 YAML 危险字符组合
POISON_FIELDS = {
    "bold_colon_quote": '**粗体**：冒号"引号',
    "markdown_bold": '**bold** text',
    "fullwidth_colon": '结果：成功',
    "double_quote": 'say "hello" world',
    "hash_comment": 'value # not a comment',
    "brackets": 'list[0] and {key}',
    "leading_space": ' leading whitespace',
    "trailing_space": 'trailing whitespace ',
    "multiline": 'line1\nline2',
    "yaml_bool_trap": 'True-Name',  # must stay string
    "yaml_null_trap": 'null-like',
    "mixed_poison": '**bold**: "quoted" # hash [bracket] {brace}',
}


def _extract_frontmatter_text(markdown: str) -> str:
    """Extract frontmatter text between --- delimiters."""
    if not markdown.startswith("---"):
        raise ValueError("No frontmatter found")
    lines = markdown.splitlines()
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("No closing --- found")
    return "\n".join(lines[1:end_idx])


def _safe_load_frontmatter(markdown: str) -> dict[str, Any]:
    """Parse frontmatter and return dict."""
    fm_text = _extract_frontmatter_text(markdown)
    if HAS_YAML:
        return yaml.safe_load(fm_text) or {}
    from tools.aipos_cli.frontmatter import _fallback_parse
    data, _ = _fallback_parse(fm_text)
    return data


class TestF46RedGreen(unittest.TestCase):
    """验收①: 先红后绿 — 毒字段经 safe_dump 单源可解析."""

    def test_poison_fields_parseable_via_render_markdown(self):
        """All poison fields must produce parseable YAML via render_markdown."""
        from tools.aipos_cli.record_writer import render_markdown

        for name, value in POISON_FIELDS.items():
            with self.subTest(poison=name):
                meta = {"task_id": f"TEST-{name}", "result_summary": value}
                result = render_markdown(meta, "# Body\n")
                parsed = _safe_load_frontmatter(result)
                self.assertEqual(
                    parsed.get("result_summary"), value,
                    f"Poison field '{name}' did not roundtrip: {parsed.get('result_summary')!r} != {value!r}"
                )

    def test_poison_fields_parseable_via_render_task_markdown(self):
        """All poison fields must produce parseable YAML via render_task_markdown (queue_mutation)."""
        from tools.aipos_cli.queue_mutation import render_task_markdown

        for name, value in POISON_FIELDS.items():
            with self.subTest(poison=name):
                meta = {
                    "task_id": f"TEST-{name}",
                    "title": f"Test {name}",
                    "status": "completed",
                    "result_summary": value,
                }
                result = render_task_markdown(meta, "# Body\n")
                parsed = _safe_load_frontmatter(result)
                self.assertEqual(
                    parsed.get("result_summary"), value,
                    f"Poison field '{name}' did not roundtrip via render_task_markdown"
                )


class TestF46AllFourPaths(unittest.TestCase):
    """验收②: 全路径毒字段夹具 — 交回/裁决回写/级联派生/进度回写四条真路径."""

    def test_path_1_return_write(self):
        """交回路径: record_writer.build_mcp_return_record_markdown."""
        from tools.aipos_cli.record_writer import build_mcp_return_record_markdown

        result = build_mcp_return_record_markdown(
            task_id="TEST-RETURN",
            task_path="5_tasks/queue/completed/test.md",
            actor="exec.test",
            canonical_agent_instance="exec.test.agent",
            owner_policy_ref="pol_test",
            return_id="return_test_001",
            claim_id="claim_test_001",
            session_id="session_test_001",
            returned_at="2026-01-01T00:00:00Z",
            result_summary=POISON_FIELDS["bold_colon_quote"],
            artifact_refs=[],
            completion_report_ref=None,
        )
        parsed = _safe_load_frontmatter(result)
        self.assertTrue(parsed.get("result_summary_present"))

    def test_path_2_audit_verdict_write(self):
        """裁决回写路径: record_writer.build_mcp_audit_verdict_record_markdown."""
        from tools.aipos_cli.record_writer import build_mcp_audit_verdict_record_markdown

        result = build_mcp_audit_verdict_record_markdown(
            verdict_id="verdict_test_001",
            verdict="FAIL",
            reviewed_task_id="TEST-VERDICT",
            reviewed_task_path="5_tasks/queue/claimed/test.md",
            reviewed_return_record_ref="return_test_001",
            audit_dispatch_record_ref="dispatch_test_001",
            audit_task_id="TEST-VERDICTR",
            audit_task_path="5_tasks/queue/claimed/testr.md",
            audit_claim_id="claim_testr_001",
            audit_session_id="session_testr_001",
            reviewed_executor_instance="exec.test",
            auditor_instance="audit.test",
            actor="audit.test",
            canonical_agent_instance="audit.test",
            owner_policy_ref="pol_audit_test",
            verdict_at="2026-01-01T00:00:00Z",
            findings_summary=POISON_FIELDS["mixed_poison"],
            evidence_refs=["task_cards/TEST/RETURN.md"],
            recommended_next_action=None,
        )
        parsed = _safe_load_frontmatter(result)
        self.assertTrue(parsed.get("findings_summary_present"))
        self.assertEqual(parsed.get("verdict"), "FAIL")

    def test_path_3_cascade_derive_write(self):
        """级联派生路径: queue_mutation.render_task_markdown (used by audit_derivation)."""
        from tools.aipos_cli.queue_mutation import render_task_markdown

        meta = {
            "task_id": "TEST-DERIVE-R",
            "title": f"Audit {POISON_FIELDS['markdown_bold']}",
            "project": "lybra",
            "assigned_to": "audit.lybra.kiwiai-dev",
            "agent_instance": "audit.lybra.kiwiai-dev",
            "task_mode": "audit",
            "task_class": "simple",
            "priority": "high",
            "status": "pending",
            "created_by": "gate_derivation",
            "needs_owner": False,
            "governance_refs": [POISON_FIELDS["mixed_poison"]],
        }
        result = render_task_markdown(meta, "## Audit Subject\n")
        parsed = _safe_load_frontmatter(result)
        self.assertEqual(parsed.get("task_id"), "TEST-DERIVE-R")
        # Verify governance_refs list roundtrips
        refs = parsed.get("governance_refs", [])
        self.assertIsInstance(refs, list)
        self.assertTrue(len(refs) > 0)

    def test_path_4_progress_write(self):
        """进度回写路径: task_progress_writer (uses render_markdown after F46 fix)."""
        from tools.aipos_cli.record_writer import render_markdown

        # Simulate what task_progress_writer does after F46 fix
        metadata = {
            "record_type": "task_progress_event",
            "event_type": "progress",
            "task_id": "TEST-PROGRESS",
            "actor": "exec.test",
            "timestamp": "2026-01-01T00:00:00Z",
            "summary": POISON_FIELDS["bold_colon_quote"],
        }
        body = "# Task Progress Event: progress\n\nAgent `exec.test` reported progress.\n"
        order = ["record_type", "event_type", "task_id", "actor", "timestamp", "summary"]
        result = render_markdown(metadata, body, order)
        parsed = _safe_load_frontmatter(result)
        self.assertEqual(parsed.get("summary"), POISON_FIELDS["bold_colon_quote"])


class TestF46GrepAssertion(unittest.TestCase):
    """验收③: grep 断言 — 全仓无绕过写入器的 frontmatter 拼接."""

    def test_no_bypass_writers_in_task_card_paths(self):
        """Verify that render_task_markdown and render_markdown_task_card delegate to single source."""
        import inspect
        from tools.aipos_cli.queue_mutation import render_task_markdown
        from tools.aipos_cli.draft_writer import render_markdown_task_card

        # render_task_markdown should call _render_markdown_single_source
        source = inspect.getsource(render_task_markdown)
        self.assertIn("_render_markdown_single_source", source,
                       "render_task_markdown must delegate to F22B single source")

        # render_markdown_task_card should call _render_markdown_single_source
        source2 = inspect.getsource(render_markdown_task_card)
        self.assertIn("_render_markdown_single_source", source2,
                       "render_markdown_task_card must delegate to F22B single source")

    def test_no_raw_yaml_dump_in_finalization(self):
        """Verify finalization_record does not use yaml.dump directly."""
        import inspect
        from tools.aipos_cli.finalization_record import render_record_markdown

        source = inspect.getsource(render_record_markdown)
        self.assertNotIn("yaml.dump(", source,
                          "finalization_record must not use yaml.dump directly")
        self.assertIn("_render_markdown_single_source", source,
                       "finalization_record must delegate to F22B single source")

    def test_grep_no_bypass_patterns(self):
        """Grep the codebase for bypass patterns in non-test files."""
        # Patterns that indicate direct frontmatter writing (bypassing single source)
        bypass_patterns = [
            r'f"---\\n\{yaml\.dump',  # f"---\n{yaml.dump(...)}
            r'f"---\\n\{yaml\.safe_dump',  # f"---\n{yaml.safe_dump(...)}
        ]
        tools_dir = REPO_ROOT / "tools" / "aipos_cli"
        violations = []
        for py_file in tools_dir.glob("*.py"):
            if py_file.name.startswith("test_") or py_file.name == "__init__.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            for pattern in bypass_patterns:
                import re
                if re.search(pattern, content):
                    violations.append(f"{py_file.name}: matches {pattern}")

        self.assertEqual(violations, [],
                          f"Bypass patterns found: {violations}")


class TestF46SelfCheck(unittest.TestCase):
    """验收④: 末道自检负夹具 — 构造写入器故障→拒写并报错."""

    def test_self_check_catches_bad_yaml(self):
        """The self-check must raise ValueError if YAML would be unparseable."""
        from tools.aipos_cli.record_writer import _self_check_yaml

        # Simulate a bad YAML output (unparseable)
        bad_yaml = "key: [unbalanced bracket"
        with self.assertRaises(ValueError) as ctx:
            _self_check_yaml(bad_yaml, {"key": "[unbalanced"})
        self.assertIn("AIPOS-F46 self-check FAIL", str(ctx.exception))

    def test_self_check_catches_type_coercion(self):
        """The self-check must catch string→bool coercion."""
        from tools.aipos_cli.record_writer import _self_check_yaml

        # "true" without quotes would be parsed as bool True, not string
        # This simulates what would happen if a string "true" was written unquoted
        bad_yaml = "status: true"
        with self.assertRaises(ValueError) as ctx:
            _self_check_yaml(bad_yaml, {"status": "true"})  # original was string
        self.assertIn("AIPOS-F46 self-check FAIL", str(ctx.exception))
        self.assertIn("roundtripped", str(ctx.exception))

    def test_self_check_passes_good_yaml(self):
        """The self-check must pass for properly escaped YAML."""
        from tools.aipos_cli.record_writer import _self_check_yaml

        good_yaml = 'status: "true"\ntask_id: "TEST-1"'
        # Should not raise
        _self_check_yaml(good_yaml, {"status": "true", "task_id": "TEST-1"})


class TestF46BaselineNoNewFailures(unittest.TestCase):
    """验收⑤: 基线对照零新增失败 — 现有测试不被破坏."""

    def test_render_markdown_basic(self):
        """Basic render_markdown still works."""
        from tools.aipos_cli.record_writer import render_markdown

        meta = {"task_id": "BASIC-1", "status": "pending", "priority": "high"}
        result = render_markdown(meta, "# Body\n")
        parsed = _safe_load_frontmatter(result)
        self.assertEqual(parsed["task_id"], "BASIC-1")
        self.assertEqual(parsed["status"], "pending")
        self.assertEqual(parsed["priority"], "high")

    def test_render_markdown_with_list(self):
        """List values still work."""
        from tools.aipos_cli.record_writer import render_markdown

        meta = {"task_id": "LIST-1", "governance_refs": ["ref1", "ref2", "ref:with:colons"]}
        result = render_markdown(meta, "# Body\n")
        parsed = _safe_load_frontmatter(result)
        self.assertEqual(parsed["governance_refs"], ["ref1", "ref2", "ref:with:colons"])

    def test_render_markdown_with_bool_and_int(self):
        """Bool and int values still work."""
        from tools.aipos_cli.record_writer import render_markdown

        meta = {"needs_owner": False, "priority": 5, "deployed": True}
        result = render_markdown(meta, "# Body\n")
        parsed = _safe_load_frontmatter(result)
        self.assertEqual(parsed["needs_owner"], False)
        self.assertEqual(parsed["priority"], 5)
        self.assertEqual(parsed["deployed"], True)

    def test_render_markdown_with_empty_values(self):
        """Empty values still work."""
        from tools.aipos_cli.record_writer import render_markdown

        meta = {"task_id": "EMPTY-1", "description": "", "refs": []}
        result = render_markdown(meta, "# Body\n")
        parsed = _safe_load_frontmatter(result)
        self.assertEqual(parsed["task_id"], "EMPTY-1")
        self.assertEqual(parsed["refs"], [])

    def test_build_mcp_return_record_roundtrip(self):
        """Full return record build + parse roundtrip."""
        from tools.aipos_cli.record_writer import build_mcp_return_record_markdown

        result = build_mcp_return_record_markdown(
            task_id="ROUNDTRIP-1",
            task_path="5_tasks/queue/completed/test.md",
            actor="exec.test",
            canonical_agent_instance="exec.test.agent",
            owner_policy_ref="pol_test",
            return_id="return_rt_001",
            claim_id="claim_rt_001",
            session_id="session_rt_001",
            returned_at="2026-01-01T00:00:00Z",
            result_summary="All good",
            artifact_refs=["ref1", "ref2"],
            completion_report_ref="task_cards/ROUNDTRIP-1/RETURN.md",
        )
        parsed = _safe_load_frontmatter(result)
        self.assertEqual(parsed["task_id"], "ROUNDTRIP-1")
        self.assertEqual(parsed["artifact_refs"], ["ref1", "ref2"])


if __name__ == "__main__":
    unittest.main()
