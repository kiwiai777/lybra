"""AIPOS-249 — planner token role + draft_submit write surface (gate-side, real HTTP gate).

Red lines pinned (card §1):
- 红线2: planner token (scopes=[draft_submit]) is structurally SCOPE_DENIED on
  claim/return/confirm/publish/audit — and leaves ZERO records on those attempts.
- 红线1: draft_submit lands ONLY under 5_tasks/drafts/ — the path is DRAFTS_DIR +
  draft_slug(task_id) (constant dir + regex-locked slug); a caller passes no path and
  cannot escape drafts/ (`..`, absolute, separators are slugged away).
- 红线2 (publish gate): a planner draft is a PROPOSAL — landing it into truth
  (drafts -> queue/pending = draft_publish) is SCOPE_DENIED for the planner AND requires
  owner_confirm, so only the Owner can publish. draft_submit confirm itself needs NO
  owner_confirm (a draft is not truth).
- R-2: lybra_task_preview surfaces existing_audit_verdicts (+ existing_returns) so the
  planner can read audit outcomes for round-end scoring via a read-only tool.
- R-4: a task in the drafts zone is NOT claimable — only queue/pending is. Proven with a
  claim-scoped (executor) token, so it's a STRUCTURAL fact, not a scope denial.
"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import unittest

from tools.aipos_cli.confirm_client import GateClient
from tools.aipos_cli.records import load_records
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server


def _registry() -> dict[str, dict[str, Any]]:
    return {
        "planner-secret": {
            "role": "planner",
            "token_ref": "svc-planner",
            "scopes": ["draft_submit"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:plfp249",
        },
        "owner-secret": {
            "role": "owner",
            "token_ref": "svc-owner",
            "scopes": ["queue_claim", "queue_return", "owner_confirm", "draft_publish"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:ownfp249",
        },
        "executor-secret": {
            "role": "executor",
            "token_ref": "svc-executor",
            "scopes": ["queue_claim", "queue_return"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:exfp249",
        },
    }


def _draft_frontmatter(task_id: str = "AIPOS-PLTEST") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": "Planner-drafted card",
        "project": "lybra",
        "assigned_to": "exec.cc",
        "context_bundle": "exec.cc",
        "task_mode": "code",
        "priority": "medium",
        "status": "pending",
        "created_by": "planner",
        "needs_owner": False,
        "output_target": "docs/",
        "artifact_policy": "formal_write",
    }


class PlannerRoleGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @contextmanager
    def gate(self) -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST,
            port=0,
            token="",
            keepalive_seconds=0.01,
            max_keepalive_events=1,
            service_role_registry=_registry(),
        )
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown()
                thread.join(timeout=2)
                httpd.server_close()

    def _tree_files(self) -> set[str]:
        return {str(p.relative_to(self.repo_root)) for p in self.repo_root.rglob("*") if p.is_file()}

    # --- 只读可用: planner (no scope) reads truth ---

    def test_planner_can_use_read_tools(self) -> None:
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            for tool in ("lybra_queue_list", "lybra_project_status", "lybra_validate"):
                result = planner.call_tool(tool, {})
                self.assertTrue(result.get("ok"), f"{tool} should be readable by planner: {result}")

    # --- 红线1 + 免门: draft_submit lands under drafts/, no owner_confirm needed ---

    def test_planner_draft_submit_lands_in_drafts_zone(self) -> None:
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            dry = planner.call_tool(
                "lybra_draft_submit_dry_run",
                {"frontmatter": _draft_frontmatter(), "body": "Do the thing.", "actor": "planner"},
            )
            self.assertTrue(dry.get("dry_run_token"), dry)
            self.assertTrue(str(dry.get("data", {}).get("target_path", "")).startswith("5_tasks/drafts/"))
            confirm = planner.call_tool(
                "lybra_draft_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "planner"}
            )
            self.assertTrue(confirm.get("ok"), confirm)
        drafts = [f for f in self._tree_files() if f.startswith("5_tasks/drafts/")]
        self.assertEqual(len(drafts), 1, f"exactly one draft landed under drafts/: {drafts}")
        self.assertTrue(drafts[0].endswith(".md"))

    def test_draft_submit_path_cannot_escape_drafts(self) -> None:
        """红线1: a task_id carrying path-escape chars is slugged — the draft NEVER lands
        outside 5_tasks/drafts/ (draft_slug regex-locks it; caller passes no path field)."""
        before = self._tree_files()
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            for evil in ("../../etc/passwd", "/abs/escape", "AIPOS/../../x"):
                fm = _draft_frontmatter(task_id=evil)
                dry = planner.call_tool(
                    "lybra_draft_submit_dry_run", {"frontmatter": fm, "body": "x", "actor": "planner"}
                )
                target = str(dry.get("data", {}).get("target_path") or "")
                if target:  # a slug was derivable → it MUST be inside drafts/
                    self.assertTrue(
                        target.startswith("5_tasks/drafts/") and ".." not in target,
                        f"escape attempt {evil!r} produced out-of-drafts target {target!r}",
                    )
        # nothing landed outside drafts/ (dry-runs only; and even a confirm is slug-locked)
        after = self._tree_files()
        new_outside = {f for f in (after - before) if not f.startswith("5_tasks/drafts/")}
        self.assertEqual(new_outside, set(), f"draft_submit wrote outside drafts/: {new_outside}")

    # --- 红线2: planner is SCOPE_DENIED on every non-draft-submit write, ZERO records ---

    def test_planner_denied_on_all_gated_writes_zero_records(self) -> None:
        denials = [
            ("lybra_queue_claim_dry_run", {"task_id": "X", "actor": "planner", "agent_instance": "planner", "autonomy_mode": "Supervised", "owner_policy_ref": "op"}),
            ("lybra_queue_claim_confirm", {"dry_run_token": "t", "actor": "planner"}),
            ("lybra_queue_return_dry_run", {"actor": "planner"}),
            ("lybra_queue_return_confirm", {"dry_run_token": "t", "actor": "planner"}),
            ("lybra_draft_publish_dry_run", {"path": "5_tasks/drafts/x.md", "actor": "planner"}),
            ("lybra_draft_publish_confirm", {"dry_run_token": "t", "owner_confirmation_token": "OWNER_CONFIRMED", "actor": "planner"}),
            ("lybra_audit_dispatch_dry_run", {"actor": "planner"}),
            ("lybra_audit_verdict_dry_run", {"actor": "planner"}),
            ("lybra_owner_decision_record_dry_run", {"actor": "planner"}),
        ]
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            for tool, arg in denials:
                result = planner.call_tool(tool, arg)
                self.assertEqual(result.get("error_code"), "SCOPE_DENIED", f"{tool} must be SCOPE_DENIED: {result}")
        records_dir = self.repo_root / "5_tasks" / "records"
        wrote = list(records_dir.rglob("*.md")) if records_dir.exists() else []
        self.assertEqual(wrote, [], f"denied writes must leave ZERO records: {wrote}")

    # --- 红线2 (publish gate): planner submits a draft but CANNOT publish it; Owner can ---

    def test_planner_cannot_publish_own_draft_owner_can(self) -> None:
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            dry = planner.call_tool(
                "lybra_draft_submit_dry_run",
                {"frontmatter": _draft_frontmatter("AIPOS-PUBTEST"), "body": "b", "actor": "planner"},
            )
            planner.call_tool("lybra_draft_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "planner"})
            draft_path = dry["data"]["target_path"]
            # planner tries to publish its own draft → SCOPE_DENIED (no draft_publish scope)
            denied = planner.call_tool("lybra_draft_publish_dry_run", {"path": draft_path, "actor": "planner"})
            self.assertEqual(denied.get("error_code"), "SCOPE_DENIED")
            # Owner CAN publish it (draft_publish + owner_confirm) — the gate stays with the Owner
            owner = GateClient(url, "owner-secret")
            owner.initialize()
            owner_dry = owner.call_tool("lybra_draft_publish_dry_run", {"path": draft_path, "actor": "owner"})
            self.assertTrue(owner_dry.get("dry_run_token"), owner_dry)

    # --- R-2: task_preview surfaces audit verdicts for planner scoring ---

    def test_task_preview_surfaces_audit_verdicts(self) -> None:
        task_id = "AIPOS-AUDREAD"
        (self.repo_root / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md").write_text(
            "\n".join(
                ["---", f"task_id: {task_id}", "title: Audit-read test", "project: lybra",
                 "assigned_to: exec.cc", "context_bundle: exec.cc", "task_mode: code", "priority: medium",
                 "status: pending", "created_by: t", "needs_owner: false", "output_target: docs/",
                 "artifact_policy: formal_write", "---", "body"]
            ),
            encoding="utf-8",
        )
        av_dir = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / task_id
        av_dir.mkdir(parents=True, exist_ok=True)
        (av_dir / "verdict_1.md").write_text(
            "\n".join(
                ["---", "record_type: audit_verdict", f"task_id: {task_id}", "verdict: PASS",
                 "auditor: aud.cc", "created_at: '2026-07-12T00:00:00Z'", "---", "looks good"]
            ),
            encoding="utf-8",
        )
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            preview = planner.call_tool("lybra_task_preview", {"task_id": task_id})
        data = preview.get("data") if isinstance(preview.get("data"), dict) else preview
        self.assertIn("existing_audit_verdicts", data, "R-2: preview must surface audit verdicts")
        self.assertIn("existing_returns", data)
        verdicts = data["existing_audit_verdicts"]
        self.assertTrue(verdicts, "the seeded audit verdict must be visible to the planner")

    # --- R-4: a drafts-zone task is NOT claimable (only queue/pending is) ---

    def test_r4_drafts_zone_task_is_not_claimable(self) -> None:
        """R-4 structural pin: prove with a CLAIM-scoped (executor) token — even with claim
        authority, a task sitting in the drafts zone cannot be claimed, because claim only
        resolves tasks from 5_tasks/queue/. A draft must be Owner-published into the queue
        first. This is structural (queue vs drafts), not a scope denial."""
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            dry = planner.call_tool(
                "lybra_draft_submit_dry_run",
                {"frontmatter": _draft_frontmatter("AIPOS-DRAFTONLY"), "body": "b", "actor": "planner"},
            )
            planner.call_tool("lybra_draft_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "planner"})
            # executor (HAS queue_claim) tries to claim the drafts-only task by id → not claimable
            executor = GateClient(url, "executor-secret")
            executor.initialize()
            claim = executor.call_tool(
                "lybra_queue_claim_dry_run",
                {"task_id": "AIPOS-DRAFTONLY", "actor": "exec.cc", "agent_instance": "exec.cc",
                 "autonomy_mode": "Supervised", "owner_policy_ref": "owner_policy:supervised"},
            )
            self.assertNotEqual(claim.get("verdict"), "PASS", "a drafts-zone task must not be claimable")
            self.assertFalse(claim.get("ok") and claim.get("verdict") == "PASS")
        # and the draft is still in drafts/, never moved into the queue by the claim attempt
        self.assertTrue(any(f.startswith("5_tasks/drafts/") for f in self._tree_files()))
        claimed = list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md"))
        self.assertEqual(claimed, [], "no draft leaked into the claimed queue")


class PlannerSkillDeliverableTests(unittest.TestCase):
    """AIPOS-249 S3 交付物钉:两份 SKILL 的继承标记(带 src)、R-1 自足、R-3/F-06、无 confirm scope。"""

    _REPO = Path(__file__).resolve().parents[3]
    _PILLARS = (
        "task-card-discipline",
        "two-role",
        "owner-gate",
        "role-contract",
        "advisor-duties",
        "capability-scoring-rubric",
        "scoreboard-reporting",
    )

    def _skill(self, name: str) -> str:
        path = self._REPO / "skills" / name / "SKILL.md"
        self.assertTrue(path.is_file(), f"missing {path}")
        return path.read_text(encoding="utf-8")

    def test_both_skills_inherit_all_seven_pillars_with_src_tag(self) -> None:
        """R 钩4 便宜钉:每个继承段都带 `src=lybra-method@<commit>` 标记(防漏标)。"""
        import re

        for skill in ("owner-console", "lybra-planner"):
            text = self._skill(skill)
            opens = re.findall(r"<!--\s*lybra:planner-inherit\s+pillar=([\w-]+)\s+src=(lybra-method@\S+)\s*-->", text)
            found_pillars = {p for p, _ in opens}
            for pillar in self._PILLARS:
                self.assertIn(pillar, found_pillars, f"{skill}: pillar {pillar} missing or lacks src= tag")
            # every open tag carries a non-empty src, and closes are balanced
            for _, src in opens:
                self.assertTrue(src.startswith("lybra-method@") and len(src) > len("lybra-method@"), src)
            self.assertEqual(
                text.count("<!-- lybra:planner-inherit"), text.count("<!-- /lybra:planner-inherit -->"),
                f"{skill}: unbalanced inherit markers",
            )

    def test_owner_console_is_self_sufficient_R1(self) -> None:
        """R-1:单装 owner-console 必须无缝含全部顾问职责——建项引导/共创轮/评分呈报。"""
        text = self._skill("owner-console")
        for needle in ("协作模式共创", "PROJECT_SPEC", "轮末评分呈报", "人话叙述", "出卡顺序"):
            self.assertIn(needle, text, f"owner-console missing self-sufficient advisor content: {needle}")

    def test_owner_console_f06_and_R3_codex_disclosure(self) -> None:
        text = self._skill("owner-console")
        self.assertIn("owner_confirm", text)
        self.assertIn("免审白名单", text, "F-06: must forbid owner_confirm on the auto-approve allowlist")
        self.assertIn("Claude Code", text, "R-3: confirm support must be declared Claude-Code-only")
        self.assertIn("codex", text, "R-3: must disclose codex confirm is unverified")

    def test_lybra_planner_declares_no_confirm_scope(self) -> None:
        text = self._skill("lybra-planner")
        self.assertIn("SCOPE_DENIED", text)
        self.assertIn("draft_submit", text)
        # the planner must NOT claim to be a confirm/publish surface
        self.assertNotIn("你亲手点批准", text)

    def test_both_skills_declare_zero_autonomy_R5(self) -> None:
        for skill in ("owner-console", "lybra-planner"):
            text = self._skill(skill)
            self.assertIn("autonomy_mode", text)
            self.assertIn("Supervised", text)

    # --- O3 收口轮 findings 回归钉 ---

    def test_o3_2_owner_console_installs_ask_permission_snippet(self) -> None:
        """F-249-o3-2(CRITICAL):owner-console 一次性配置必含 ask 档结构片段——三个 owner
        确认工具钉进 permissions.ask(结构第一道防线,弹窗不可被 allow 静音)。"""
        text = self._skill("owner-console")
        self.assertIn('"permissions"', text)
        self.assertIn('"ask"', text)
        for tool in (
            "mcp__lybra__lybra_draft_publish_confirm",
            "mcp__lybra__lybra_queue_claim_confirm",
            "mcp__lybra__lybra_queue_return_confirm",
        ):
            self.assertIn(tool, text, f"ask snippet missing {tool}")
        # ask-over-allow semantics + prose-as-second-line disclosed
        self.assertIn("先于", text)  # ask 先于 allow
        self.assertIn("第二道", text)  # prose 降为第二道防线

    def test_o3_4_both_skills_open_with_settling_round(self) -> None:
        """F-249-o3-4:两份建项引导以「第 0 轮治理区安家」开场(默认 ~/.lybra/projects/)。"""
        for skill in ("owner-console", "lybra-planner"):
            text = self._skill(skill)
            self.assertIn("治理区安家", text, f"{skill} missing 第0轮安家")
            self.assertIn("~/.lybra/projects/", text)
            self.assertIn("治理区未定", text, f"{skill} missing 开工前置门")

    def test_o3_1_token_env_var_unified(self) -> None:
        """F-249-o3-1:三份 SKILL token 环境变量名统一 LYBRA_MCP_TOKEN;正确 mcp add 语法
        (--header,非不存在的 --bearer-token-env-var)。"""
        for skill in ("owner-console", "lybra-planner", "lybra-executor"):
            text = self._skill(skill)
            self.assertIn("LYBRA_MCP_TOKEN", text, f"{skill} missing unified token name")
            self.assertNotIn("--bearer-token-env-var", text, f"{skill} uses nonexistent mcp-add flag")

    def test_o3_3_no_jargon(self) -> None:
        """F-249-o3-3:黑话清除——owner-console/lybra-planner 无"判型"/"内置原语"。"""
        for skill in ("owner-console", "lybra-planner"):
            text = self._skill(skill)
            self.assertNotIn("判型", text, f"{skill} still uses jargon 判型")
            self.assertNotIn("内置原语", text, f"{skill} still uses jargon 内置原语")

    def test_disclosure_and_readme_reference_planner(self) -> None:
        disclosure = (self._REPO / "docs" / "v1_disclosure.md").read_text(encoding="utf-8")
        self.assertIn("Planner role", disclosure)
        self.assertIn("draft_submit", disclosure)
        readme = (self._REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("skills/owner-console", readme)
        self.assertIn("skills/lybra-planner", readme)


if __name__ == "__main__":
    unittest.main()
