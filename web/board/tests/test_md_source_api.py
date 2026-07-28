"""AIPOS-263 — md 原文侧栏:零依赖安全渲染 + 路径白名单(route-level)。

覆盖验收断言:
- S1 任意任务卡右侧抽屉渲染其队列卡 md(标题/列表/代码块正确呈现);
- S2 记录条目可开原记录;
- S3 越界路径请求被拒(../ 与绝对路径夹具);
- S4 XSS 夹具(卡内嵌 <script>/onerror)渲染后无脚本执行(转义断言);
- S5 零回归(选择子校验、只读、路由已挂载)。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web.board.app import _api_routes, dispatch_api_request


WEB_ROOT = Path(__file__).resolve().parents[1]


class MarkdownSourceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryWorkspace()
        self.repo_root = self.temp_dir.path
        self.routes = _api_routes(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ---- helpers ---------------------------------------------------------
    def get(self, query: str):
        return dispatch_api_request(method="GET", path=f"/api/markdown-source?{query}", routes=self.routes)

    def data_paths(self) -> list[str]:
        values: list[str] = []
        for sub in ("5_tasks/queue", "5_tasks/records"):
            root = self.repo_root / sub
            if root.exists():
                values.extend(p.relative_to(self.repo_root).as_posix() for p in root.rglob("*"))
        return sorted(values)

    # ---- S1: task card renders subset (headings / list / code block) -----
    def test_s1_task_card_renders_subset_by_task_id(self) -> None:
        status, data = self.get("task_id=AIPOS-DEMO")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["operation"], "get_markdown_source")
        self.assertEqual(data["summary"]["kind"], "queue")
        html = data["data"]["rendered_html"]
        self.assertIn("<h1>演示卡片标题</h1>", html)
        self.assertIn("<ul><li>第一步</li><li>第二步</li></ul>", html)
        self.assertIn("<pre><code class=\"language-python\">print(", html)
        self.assertIn("<strong>加粗</strong>", html)
        # frontmatter surfaced + folded flag present (collapsed client-side)
        self.assertTrue(data["data"]["has_frontmatter"])
        self.assertIn("task_id: AIPOS-DEMO", data["data"]["frontmatter"])

    def test_s1b_explicit_path_mode_renders_same_card(self) -> None:
        status, data = self.get("path=5_tasks/queue/pending/aipos-demo.md")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["data"]["path"], "5_tasks/queue/pending/aipos-demo.md")
        self.assertIn("<h1>演示卡片标题</h1>", data["data"]["rendered_html"])

    # ---- S2: record entry opens original record md ----------------------
    def test_s2_record_resolves_by_record_id(self) -> None:
        status, data = self.get("record_id=verdict_demo1")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["summary"]["kind"], "records")
        self.assertTrue(data["data"]["path"].startswith("5_tasks/records/"))
        self.assertIn("<h2>审计结论</h2>", data["data"]["rendered_html"])

    def test_s2b_record_disambiguates_by_task_id(self) -> None:
        # same record_id appears under two tasks → task_id picks the right one.
        status, data = self.get("record_id=dup_id&task_id=TASK-A")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertIn("TASK-A", data["data"]["path"])

    # ---- S3: path whitelist (traversal / absolute / out-of-workspace) ---
    def test_s3_rejects_dotdot_traversal(self) -> None:
        status, data = self.get("path=5_tasks/queue/pending/../../../etc/passwd")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        cats = {e["category"] for e in data["errors"]}
        self.assertIn("PATH_UNSAFE", cats)

    def test_s3b_rejects_absolute_path(self) -> None:
        status, data = self.get("path=/etc/passwd")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        cats = {e["category"] for e in data["errors"]}
        self.assertIn("PATH_UNSAFE", cats)

    def test_s3c_rejects_non_whitelisted_workspace_path(self) -> None:
        (self.repo_root / "0_control_plane" / "agents").mkdir(parents=True, exist_ok=True)
        secret = self.repo_root / "0_control_plane" / "agents" / "secret.md"
        secret.write_text("# secret\n", encoding="utf-8")
        status, data = self.get("path=0_control_plane/agents/secret.md")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        cats = {e["category"] for e in data["errors"]}
        self.assertIn("PATH_UNSAFE", cats)

    def test_s3d_rejects_symlink_escape(self) -> None:
        target = self.temp_dir.make_outside_file("outside.md", "# outside\n")
        link_dir = self.repo_root / "5_tasks" / "queue" / "pending"
        (link_dir / "link.md").symlink_to(target)
        status, data = self.get("path=5_tasks/queue/pending/link.md")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])

    # ---- S4: XSS fixture escapes <script> / onerror ---------------------
    def test_s4_xss_fixture_no_live_script_or_handler(self) -> None:
        status, data = self.get("task_id=XSS-DEMO")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        html = data["data"]["rendered_html"]
        # No live script / img / iframe / svg tags may survive rendering.
        for tag in ("<script", "<img", "<iframe", "<svg", "<b>", "<object"):
            self.assertNotIn(tag, html, f"live tag leaked: {tag}")
        # The dangerous payload survives only as escaped, inert text.
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("onerror=alert(1)", html)  # inert text inside escaped tag
        # No executable event-handler attribute is produced.
        self.assertNotIn("<img src", html)

    # ---- S5: selector validation + read-only + route wired --------------
    def test_s5_selector_requires_exactly_one(self) -> None:
        status, data = self.get("")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        cats = {e["category"] for e in data["errors"]}
        self.assertIn("VALIDATION_ERROR", cats)

    def test_s5b_two_selectors_rejected(self) -> None:
        status, data = self.get("task_id=AIPOS-DEMO&path=5_tasks/queue/pending/aipos-demo.md")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])

    def test_s5c_unknown_task_id_clean_not_found(self) -> None:
        status, data = self.get("task_id=NOPE")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        cats = {e["category"] for e in data["errors"]}
        self.assertIn("NOT_FOUND", cats)

    def test_s5d_route_is_read_only(self) -> None:
        before = self.data_paths()
        self.get("task_id=AIPOS-DEMO")
        self.get("record_id=verdict_demo1")
        after = self.data_paths()
        self.assertEqual(before, after)

    def test_s5e_route_registered_and_gettable(self) -> None:
        # The route must be present in the GET route table (drawer fetches it).
        self.assertIn("/api/markdown-source", self.routes)

    def test_s5f_post_rejected(self) -> None:
        status, data = dispatch_api_request(method="POST", path="/api/markdown-source", routes=self.routes)
        self.assertEqual(status, 405)
        self.assertEqual(data.get("error"), "METHOD_NOT_ALLOWED")


class TemporaryWorkspace:
    """Build a minimal governance-workspace fixture under a temp dir."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.path / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.path / "5_tasks" / "records" / "audit_verdicts").mkdir(parents=True, exist_ok=True)
        self._write_demo_task()
        self._write_xss_task()
        self._write_records()

    def _write(self, rel: str, text: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _write_demo_task(self) -> None:
        self._write(
            "5_tasks/queue/pending/aipos-demo.md",
            "---\n"
            "task_id: AIPOS-DEMO\n"
            "title: 演示卡片\n"
            "status: pending\n"
            "---\n\n"
            "# 演示卡片标题\n\n"
            "正文含 **加粗** 与 `行内代码`。\n\n"
            "## 步骤\n\n"
            "- 第一步\n"
            "- 第二步\n\n"
            "```python\n"
            "print('hello')\n"
            "```\n",
        )

    def _write_xss_task(self) -> None:
        self._write(
            "5_tasks/queue/pending/xss-demo.md",
            "---\n"
            "task_id: XSS-DEMO\n"
            "title: XSS 夹具\n"
            "---\n\n"
            "# 卡片 <script>alert('xss')</script>\n\n"
            "![img](x) <img src=x onerror=alert(1)> 这里是 <b>bold</b> 尝试。\n",
        )

    def _write_records(self) -> None:
        self._write(
            "5_tasks/records/audit_verdicts/AIPOS-DEMO/verdict_demo1.md",
            "---\n"
            "record_type: audit_verdict_record\n"
            "verdict_id: verdict_demo1\n"
            "task_id: AIPOS-DEMO\n"
            "verdict: PASS\n"
            "---\n\n"
            "## 审计结论\n\n"
            "独立证据复核 PASS。\n",
        )
        # duplicate record_id under two tasks (disambiguation fixture)
        for tid, fname in (("TASK-A", "a.md"), ("TASK-B", "b.md")):
            self._write(
                f"5_tasks/records/audit_verdicts/{tid}/{fname}",
                "---\n"
                "record_type: audit_verdict_record\n"
                "verdict_id: dup_id\n"
                f"task_id: {tid}\n"
                "verdict: PASS\n"
                "---\n\n"
                f"## {tid} 结论\n",
            )

    def make_outside_file(self, name: str, content: str) -> Path:
        outside = Path(self.tmp.name) / "_outside" / name
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text(content, encoding="utf-8")
        return outside

    def cleanup(self) -> None:
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
