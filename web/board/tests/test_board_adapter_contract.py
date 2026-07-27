"""AIPOS-255: Contract tests to prevent board UI / adapter interface drift.

These tests pin the exact keys that the board UI reads from board_adapter responses.
Any rename on either side must update both the adapter AND these tests, making drift visible.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.board_adapter import get_owner_truth_view, get_queue, get_records
from tools.aipos_cli.task_loader import load_all_tasks
from tools.aipos_cli.validator import validate_tasks
from tools.aipos_cli.records import load_records
from tools.aipos_cli.owner_truth_view import (
    _extract_purpose,
    _humanize_summary,
    PURPOSE_MAX_CHARS,
    top_level_state,
    badge_label_for,
)


class BoardAdapterContractTests(unittest.TestCase):
    """Contract tests: board UI depends on these exact keys from board_adapter."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "sessions").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "returns").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_queue_summary_provides_queue_state_counts(self) -> None:
        """AIPOS-255 F-BOARD-1: board UI (app.py:125-129, project-detail.html:369-372)
        reads summary keys 'pending', 'claimed', 'blocked', 'completed'.
        
        Validator must return these keys in summary, not just verdict counts.
        """
        # Create fixture tasks in different states
        (self.repo_root / "5_tasks" / "queue" / "pending" / "task-1.md").write_text(
            "---\ntask_id: TASK-1\ntitle: Pending Task\nstatus: pending\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-2.md").write_text(
            "---\ntask_id: TASK-2\ntitle: Claimed Task\nstatus: claimed\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "blocked" / "task-3.md").write_text(
            "---\ntask_id: TASK-3\ntitle: Blocked Task\nstatus: blocked\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "completed" / "task-4.md").write_text(
            "---\ntask_id: TASK-4\ntitle: Completed Task\nstatus: completed\n---\n",
            encoding="utf-8",
        )

        # Call get_queue (used by board /api/queue endpoint)
        response = get_queue(repo_root=self.repo_root)

        # Assert board UI contract
        self.assertTrue(response["ok"])
        summary = response["data"]["summary"]
        
        # Board UI reads these exact keys (app.py:128-129)
        self.assertIn("pending", summary, "Board UI reads summary['pending']")
        self.assertIn("claimed", summary, "Board UI reads summary['claimed']")
        self.assertIn("blocked", summary, "Board UI reads summary['blocked']")
        self.assertIn("completed", summary, "Board UI reads summary['completed']")
        
        # Verify counts match fixtures
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["claimed"], 1)
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["completed"], 1)

    def test_validator_validate_tasks_provides_queue_state_counts(self) -> None:
        """AIPOS-255 F-BOARD-1: validate_tasks (used by get_queue) must include
        queue_state counts in summary, not just verdict counts.
        """
        # Create fixture
        (self.repo_root / "5_tasks" / "queue" / "pending" / "task-p.md").write_text(
            "---\ntask_id: TASK-P\ntitle: Pending\nstatus: pending\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-c.md").write_text(
            "---\ntask_id: TASK-C\ntitle: Claimed\nstatus: claimed\n---\n",
            encoding="utf-8",
        )

        tasks = load_all_tasks(self.repo_root)
        report = validate_tasks(tasks)

        # Validator contract: must provide queue_state counts
        summary = report["summary"]
        self.assertIn("pending", summary)
        self.assertIn("claimed", summary)
        self.assertIn("blocked", summary)
        self.assertIn("completed", summary)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["claimed"], 1)

    def test_records_expose_actor_field_for_timeline(self) -> None:
        """AIPOS-255 F-BOARD-2: board UI (project-detail.html:602) reads
        record.actor for timeline rendering. Records must expose 'actor' field
        in session/return/audit records.
        """
        # Create session record with actor
        session_dir = self.repo_root / "5_tasks" / "records" / "sessions" / "TASK-S"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session_123.md").write_text(
            "---\n"
            "record_type: session_record\n"
            "session_id: session_123\n"
            "task_id: TASK-S\n"
            "actor: exec.lybra.test\n"
            "created_at: '2026-01-01T00:00:00Z'\n"
            "---\n",
            encoding="utf-8",
        )

        # Create return record with actor
        return_dir = self.repo_root / "5_tasks" / "records" / "returns" / "TASK-R"
        return_dir.mkdir(parents=True, exist_ok=True)
        (return_dir / "return_456.md").write_text(
            "---\n"
            "record_type: return_record\n"
            "return_id: return_456\n"
            "task_id: TASK-R\n"
            "actor: exec.lybra.test\n"
            "returned_at: '2026-01-01T01:00:00Z'\n"
            "---\n",
            encoding="utf-8",
        )

        records = load_records(self.repo_root)

        # Assert actor is exposed in session records
        sessions = records["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertIn("actor", sessions[0], "Session records must expose 'actor' field")
        self.assertEqual(sessions[0]["actor"], "exec.lybra.test")

        # Assert actor is exposed in return records
        returns = records["returns"]
        self.assertEqual(len(returns), 1)
        self.assertIn("actor", returns[0], "Return records must expose 'actor' field")
        self.assertEqual(returns[0]["actor"], "exec.lybra.test")

    def test_get_records_response_contract(self) -> None:
        """AIPOS-255 F-BOARD-2: get_records (used by /api/records endpoint)
        must return records with actor field for timeline UI.
        """
        session_dir = self.repo_root / "5_tasks" / "records" / "sessions" / "TASK-X"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session_xyz.md").write_text(
            "---\n"
            "record_type: session_record\n"
            "session_id: session_xyz\n"
            "task_id: TASK-X\n"
            "actor: auditor.lybra.test\n"
            "---\n",
            encoding="utf-8",
        )

        response = get_records(repo_root=self.repo_root)

        self.assertTrue(response["ok"])
        sessions = response["data"]["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertIn("actor", sessions[0], "Board timeline reads record.actor")
        self.assertEqual(sessions[0]["actor"], "auditor.lybra.test")


    def test_owner_truth_view_pinned_record_field_keys(self) -> None:
        """AIPOS-260 S4: the Owner truth read surface depends on these exact records
        field names: record_type, result_summary, findings_summary, verdict.
        Pin the keys so any rename drifts visibly."""
        # Task card with a real first-paragraph purpose.
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-c.md").write_text(
            "---\n"
            "task_id: TASK-C\n"
            "title: Serve address passthrough\n"
            "status: claimed\n"
            "---\n"
            "# TASK-C — cross-host onboarding\n\n"
            "Gate/board must bind a host so cross-machine agents can reach it.\n",
            encoding="utf-8",
        )

        def _write(rel: str, frontmatter: str, body: str = "") -> None:
            path = self.repo_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\n" + frontmatter + "\n---\n" + body, encoding="utf-8")

        _write(
            "5_tasks/records/publishes/TASK-C/publish_c1.md",
            "record_type: publish_record\npublish_id: publish_c1\ntask_id: TASK-C\n"
            "actor: owner\npublished_at: '2026-07-01T00:00:00Z'\n",
        )
        _write(
            "5_tasks/records/claims/TASK-C/claim_c1.md",
            "record_type: claim_record\nclaim_id: claim_c1\ntask_id: TASK-C\n"
            "actor: exec.lybra.test\nclaimed_at: '2026-07-01T00:05:00Z'\n",
        )
        _write(
            "5_tasks/records/returns/TASK-C/return_c1.md",
            "record_type: return_record\nreturn_id: return_c1\ntask_id: TASK-C\n"
            "actor: exec.lybra.test\nreturned_at: '2026-07-01T00:20:00Z'\n"
            "result_summary_present: true\n",
            "# MCP Return Record: return_c1\n\n## Summary\n\n"
            "- Result summary: bind address passthrough complete, tests green.\n",
        )
        _write(
            "5_tasks/records/audit_dispatches/TASK-C/dispatch_c1.md",
            "record_type: audit_dispatch_record\ndispatch_id: dispatch_c1\n"
            "reviewed_task_id: TASK-C\nactor: audit.lybra.test\n"
            "dispatched_at: '2026-07-01T00:30:00Z'\n",
        )
        _write(
            "5_tasks/records/audit_verdicts/TASK-C/verdict_c1.md",
            "record_type: audit_verdict_record\nverdict_id: verdict_c1\nverdict: PASS\n"
            "reviewed_task_id: TASK-C\nactor: audit.lybra.test\n"
            "verdict_at: '2026-07-01T00:40:00Z'\nfindings_summary_present: true\n",
            "# MCP Audit Verdict Record: verdict_c1\n\n## Summary\n\n"
            "- Findings summary: PASS, independent evidence captured.\n",
        )

        response = get_owner_truth_view(repo_root=self.repo_root)

        self.assertTrue(response["ok"], response)
        data = response["data"]

        # S4: pinned records field keys declared by the surface.
        for key in ("record_type", "result_summary", "findings_summary", "verdict"):
            self.assertIn(key, data["record_field_keys"], f"record_field_keys must pin {key}")

        task_c = next(t for t in data["tasks"] if t["task_id"] == "TASK-C")
        # Task row contract keys.
        for key in ("task_id", "title", "purpose", "path", "queue_state", "true_stage", "stage_label", "verdict", "timeline"):
            self.assertIn(key, task_c, f"task row must expose {key}")
        self.assertEqual(task_c["title"], "Serve address passthrough")
        self.assertIn("bind", task_c["purpose"].lower())
        # Verdict recorded -> true_stage verdict_pass (display-layer derivation only).
        self.assertEqual(task_c["true_stage"], "verdict_pass")
        self.assertEqual(task_c["verdict"], "PASS")

        # Timeline event contract keys; return carries result_summary, verdict carries findings_summary.
        by_type = {ev["record_type"]: ev for ev in task_c["timeline"]}
        self.assertEqual(set(by_type), {"publish", "claim", "return", "audit_dispatch", "audit_verdict"})
        for ev in task_c["timeline"]:
            for key in ("record_type", "record_id", "actor", "timestamp", "verb", "summary", "verdict"):
                self.assertIn(key, ev, f"timeline event must carry {key}")
        self.assertEqual(by_type["return"]["result_summary"], "bind address passthrough complete, tests green.")
        self.assertEqual(by_type["audit_verdict"]["findings_summary"], "PASS, independent evidence captured.")
        self.assertEqual(by_type["audit_verdict"]["verdict"], "PASS")
        self.assertEqual(by_type["return"]["verb"], "交付了")
        self.assertEqual(by_type["audit_verdict"]["verb"], "判决")

        # Activity feed surfaces every record_type with a verb + summary.
        feed_types = {ev["record_type"] for ev in data["activity_feed"]}
        self.assertIn("return", feed_types)
        self.assertIn("audit_verdict", feed_types)

    def test_aipos261_closure_units_and_human_phrasing_contract(self) -> None:
        """AIPOS-261 S4: the truth surface groups tasks into closure units and emits
        human-worded events. Pin the new keys: closure_units shape, timeline event
        role/phrase, and the agent_info self-reported bundle on return events."""
        # Main card + audit (R) card + fix (F1) card, sharing the AIPOS-261X family.
        for name, extra in (
            ("task-x.md", ""),
            ("task-xr.md", "\nderived_from: AIPOS-261X\nreviewed_task_id: AIPOS-261X\ntask_mode: audit"),
            ("task-xf1.md", ""),
        ):
            tid = {"task-x.md": "AIPOS-261X", "task-xr.md": "AIPOS-261XR", "task-xf1.md": "AIPOS-261XF1"}[name]
            (self.repo_root / "5_tasks" / "queue" / "claimed" / name).write_text(
                "---\n"
                f"task_id: {tid}\n"
                f"title: Fam {tid}\n"
                "status: claimed\n"
                + extra + "\n"
                "---\n# " + tid + "\n\nFamily purpose line.\n",
                encoding="utf-8",
            )

        def _write(rel: str, frontmatter: str, body: str = "") -> None:
            path = self.repo_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\n" + frontmatter + "\n---\n" + body, encoding="utf-8")

        # main delivery + return carrying agent_runtime
        _write(
            "5_tasks/records/returns/AIPOS-261X/return_x.md",
            "record_type: return_record\nreturn_id: return_x\ntask_id: AIPOS-261X\n"
            "actor: exec.lybra.test\nsession_id: session_x\n"
            "returned_at: '2026-07-28T00:20:00Z'\nresult_summary_present: true\n"
            "agent_runtime:\n"
            "  harness: pi\n"
            "  model_self_reported: provider/sonnet-5\n"
            "  tokens_in: 12345\n"
            "  tokens_out: 678\n",
            "# Return\n\n- Result summary: done with the family.\n",
        )
        # session for duration derivation
        _write(
            "5_tasks/records/sessions/AIPOS-261X/session_x.md",
            "record_type: session_record\nsession_id: session_x\ntask_id: AIPOS-261X\n"
            "actor: exec.lybra.test\ncreated_at: '2026-07-28T00:00:00Z'\n"
            "updated_at: '2026-07-28T00:20:00Z'\n",
        )
        # final PASS verdict against the main card
        _write(
            "5_tasks/records/audit_verdicts/AIPOS-261X/verdict_x.md",
            "record_type: audit_verdict_record\nverdict_id: verdict_x\nverdict: PASS\n"
            "reviewed_task_id: AIPOS-261X\nactor: audit.lybra.test\n"
            "verdict_at: '2026-07-28T00:40:00Z'\n",
            "# Verdict\n\n- Findings summary: PASS, independent.\n",
        )

        response = get_owner_truth_view(repo_root=self.repo_root)
        self.assertTrue(response["ok"], response)
        data = response["data"]

        # closure_units present and grouped the family under one root.
        self.assertIn("closure_units", data)
        # FIX-2 F-261-3: three-state overview keys present (additive).
        self.assertEqual(data["top_level_labels"], {"published": "已发布", "executing": "执行中", "closed": "已闭环"})
        self.assertEqual(data["top_level_order"], ["published", "executing", "closed"])
        self.assertIsInstance(data["top_level_counts"], dict)
        units = data["closure_units"]
        roots = {u["root_task_id"] for u in units}
        self.assertIn("AIPOS-261X", roots)
        unit = next(u for u in units if u["root_task_id"] == "AIPOS-261X")
        # Unit contract keys.
        for key in (
            "root_task_id", "title", "purpose", "true_stage", "stage_label",
            "stage_chain", "stage_chain_steps", "members", "timeline",
            "audit_rounds", "fix_rounds", "verdict_chain", "final_verdict",
        ):
            self.assertIn(key, unit, f"closure unit must expose {key}")
        # The three family members collapsed into one unit.
        member_ids = {m["task_id"] for m in unit["members"]}
        self.assertEqual(member_ids, {"AIPOS-261X", "AIPOS-261XR", "AIPOS-261XF1"})
        kinds = {m["task_id"]: m["member_kind"] for m in unit["members"]}
        self.assertEqual(kinds["AIPOS-261X"], "main")
        self.assertEqual(kinds["AIPOS-261XR"], "audit")
        self.assertEqual(kinds["AIPOS-261XF1"], "fix")
        # Face is a human stage chain naming the fix round + final verdict.
        self.assertIn("修复", unit["stage_chain"])
        self.assertIn("PASS", unit["stage_chain"])
        # FIX-2 F-261-3: three-state top-level bucket + composed badge on the unit.
        # Main card has a PASS verdict (no completed dossier) => verdict_pass =>
        # top-level 执行中, badge retains the sub-state.
        self.assertEqual(unit["true_stage"], "verdict_pass")
        self.assertEqual(unit["top_level_state"], "executing")
        self.assertEqual(unit["badge_label"], "执行中 · 判决 PASS")
        # And on the main task row.
        tasks = {t["task_id"]: t for t in data["tasks"]}
        self.assertEqual(tasks["AIPOS-261X"]["top_level_state"], "executing")
        self.assertEqual(tasks["AIPOS-261X"]["badge_label"], "执行中 · 判决 PASS")

        # Timeline event carries additive role + phrase (verb preserved).
        ret_ev = next(ev for ev in unit["timeline"] if ev["record_type"] == "return")
        self.assertEqual(ret_ev["role"], "执行者")
        self.assertEqual(ret_ev["verb"], "交付了")  # unchanged (contract pin)
        self.assertIn("交付", ret_ev["phrase"])
        vd_ev = next(ev for ev in unit["timeline"] if ev["record_type"] == "audit_verdict")
        self.assertEqual(vd_ev["role"], "审计员")
        self.assertIn("PASS", vd_ev["phrase"])

        # agent_info on the return event: pinned sub-keys, self-reported flag, duration.
        self.assertIn("agent_info", ret_ev)
        info = ret_ev["agent_info"]
        for key in ("role", "instance", "harness", "model_self_reported", "tokens_in", "tokens_out", "duration_seconds", "self_reported"):
            self.assertIn(key, info, f"agent_info must expose {key}")
        self.assertEqual(info["harness"], "pi")
        self.assertEqual(info["model_self_reported"], "provider/sonnet-5")
        self.assertEqual(info["tokens_in"], 12345)
        self.assertEqual(info["tokens_out"], 678)
        self.assertTrue(info["self_reported"])  # model/tokens are self-reported
        self.assertEqual(info["duration_seconds"], 1200)  # 00:00 -> 00:20

    def test_aipos261f1_purpose_and_summary_speak_human_no_markdown(self) -> None:
        """AIPOS-261 FIX-1 (Owner eye-verify打回): the display layer speaks 人话.

        F-261-1 — purpose line: prefer the title's colon suffix, else the body's
        first sentence with markdown/backticks/numbering stripped; cap at 60 字 +
        ellipsis. Sampled cards must show ZERO 裸露 backtick/hash/asterisk.
        F-261-2 — timeline/feed summary: markdown stripped + English tech terms
        translated (publish→发布 / claim→领取 / PreAuthorized→自动放行 …). The
        recorded source file is NEVER modified (记录原文不动)."""
        # --- F-261-1: five fixture cards mirroring real markdown-heavy patterns. ---
        cards = [
            # title with a short tag prefix before the colon -> suffix wins.
            ("c1.md", "C1", "FIX-1 打回轮:任务摘要再通俗化",
             "# Heading\n\n1. **项目地图**:渲染 `governance/project-map.md`(schema)\n"),
            # Owner evidence case: no-colon title, body line carries backticks + **.
            ("c2.md", "C2", "项目地图(治理声明文件驱动)",
             "1. **项目地图**:渲染治理声明文件 `governance/project-map.md`(schema:frontmatter)\n"),
            # long single-sentence body -> truncated + ellipsis.
            ("c3.md", "C3", "派生器修账",
             "这是一个用来测试截断逻辑的非常非常非常长的目的句子它必须超过六十个字符的长度限制这样才能触发截断并且追加省略号同时保证不出现反引号井号星号等裸露符号。"),
            # title colon with an over-long suffix -> suffix truncated.
            ("c4.md", "C4", "tag:" + ("超长" * 40), "正文不参与因为 title 有冒号。\n"),
            # body with a heading + bullet list (first substantive line = bullet).
            ("c5.md", "C5", "普通任务", "## 小标题\n\n- 列表项 `code` 加 **强调**\n"),
        ]
        for name, tid, title, body in cards:
            (self.repo_root / "5_tasks" / "queue" / "claimed" / name).write_text(
                "---\n" f"task_id: {tid}\ntitle: {title}\nstatus: claimed\n" "---\n" + body,
                encoding="utf-8",
            )

        response = get_owner_truth_view(repo_root=self.repo_root)
        self.assertTrue(response["ok"], response)
        units = {u["root_task_id"]: u for u in response["data"]["closure_units"]}

        # S1 / page assertion: every sampled purpose is free of 裸露 markdown.
        for tid in ("C1", "C2", "C3", "C4", "C5"):
            purpose = units[tid]["purpose"]
            self.assertIsNotNone(purpose, f"{tid} purpose must not be None")
            for ch in ("`", "#", "*"):
                self.assertNotIn(ch, purpose, f"{tid} purpose must not bare {ch!r}: {purpose!r}")

        # Priority 1 — title colon suffix wins over body.
        self.assertEqual(units["C1"]["purpose"], "任务摘要再通俗化")
        # Body fallback strips backticks + ** + numbering (no exact-value fragility).
        self.assertNotIn("`", units["C2"]["purpose"])
        self.assertIn("项目地图", units["C2"]["purpose"])
        # Long body truncated to the cap + ellipsis.
        self.assertGreater(len(units["C3"]["purpose"]), PURPOSE_MAX_CHARS)  # source > cap
        self.assertLessEqual(len(units["C3"]["purpose"]), PURPOSE_MAX_CHARS + 1)  # + ellipsis
        self.assertTrue(units["C3"]["purpose"].endswith("…"))
        # Long colon suffix also truncated.
        self.assertLessEqual(len(units["C4"]["purpose"]), PURPOSE_MAX_CHARS + 1)
        self.assertTrue(units["C4"]["purpose"].endswith("…"))
        # Heading skipped, bullet/code/bold stripped.
        self.assertEqual(units["C5"]["purpose"], "列表项 code 加 强调")

        # --- F-261-2: timeline/feed summary humanized, source record untouched. ---
        claim_path = self.repo_root / "5_tasks" / "records" / "claims" / "C1" / "claim_x.md"
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        raw_summary_line = (
            "Task `C1` was claimed by `exec.lybra.test` through the "
            "PreAuthorized MCP claim envelope."
        )
        claim_path.write_text(
            "---\nrecord_type: claim_record\nevent_type: mcp_queue_claim\n"
            "claim_id: claim_x\ntask_id: C1\nactor: exec.lybra.test\n"
            "claimed_at: '2026-07-28T01:00:00Z'\n---\n" + raw_summary_line + "\n",
            encoding="utf-8",
        )

        response2 = get_owner_truth_view(repo_root=self.repo_root)
        unit = next(u for u in response2["data"]["closure_units"] if u["root_task_id"] == "C1")
        claim_ev = next(ev for ev in unit["timeline"] if ev["record_type"] == "claim")
        summary = claim_ev["summary"]
        # S2: zero 裸露 markdown in the display summary.
        for ch in ("`", "#", "*"):
            self.assertNotIn(ch, summary, f"summary must not bare {ch!r}: {summary!r}")
        # Glossary applied: PreAuthorized -> 自动放行, claim -> 领取.
        self.assertNotIn("PreAuthorized", summary)
        self.assertIn("自动放行", summary)
        self.assertIn("领取", summary)
        # The activity-feed event is humanized by the same builder.
        feed_ev = next(
            ev for ev in response2["data"]["activity_feed"]
            if ev.get("record_type") == "claim" and ev.get("task_id") == "C1"
        )
        for ch in ("`", "#", "*"):
            self.assertNotIn(ch, feed_ev["summary"] or "")
        # 记录原文不动: the source file still carries the original markdown + terms.
        source = claim_path.read_text(encoding="utf-8")
        self.assertIn("`C1`", source)
        self.assertIn("PreAuthorized", source)
        self.assertIn("claim envelope", source)

        # --- glossary table pinned to the card's exact mapping. ---
        self.assertEqual(_humanize_summary("dry_run then finalize"), "预检 then 收编")
        self.assertEqual(_humanize_summary("audit verdict"), "审计 判决")
        self.assertEqual(_humanize_summary("publish claim return"), "发布 领取 交付")
        self.assertIsNone(_humanize_summary(None))
        # purpose helper: title-colon priority + body fallback + markdown strip.
        self.assertEqual(_extract_purpose("FIX-2:再通俗化", "ignored body"), "再通俗化")
        self.assertNotIn("`", _extract_purpose(None, "1. **x**: do `code` now") or "")

    def test_aipos261f2_three_state_pills_and_dossier_closed_signal(self) -> None:
        """FIX-2 (Owner 三态裁定 2026-07-28):
        F-261-3 — overview pills collapse fine-grained stages to three top-level
        states (已发布/执行中/已闭环); each row/unit carries top_level_state + a
        composed badge_label (执行中 · 判决 PASS), sub-state retained.
        F-261-4 — an archived dossier dir 5_tasks/queue/completed/<ID>/ is a
        closed signal that the records-only verdict path misses (judged PASS but
        still shown as 判决 PASS before incorporation)."""
        def _task(name: str, tid: str, status: str = "claimed", body: str = "") -> None:
            (self.repo_root / "5_tasks" / "queue" / status / name).write_text(
                "---\n" f"task_id: {tid}\n" f"title: {tid} title\n" f"status: {status}\n"
                "---\n# " + tid + "\n\n" + body,
                encoding="utf-8",
            )

        def _rec(rel: str, frontmatter: str, body: str = "") -> None:
            path = self.repo_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\n" + frontmatter + "---\n" + body, encoding="utf-8")

        # F2PUB: pending, no records -> published -> 已发布.
        _task("f2pub.md", "TASK-F2PUB", status="pending")
        # F2EXE: claimed + claim record -> executing -> 执行中.
        _task("f2exe.md", "TASK-F2EXE")
        _rec(
            "5_tasks/records/claims/TASK-F2EXE/claim.md",
            "record_type: claim_record\nclaim_id: cl\ntask_id: TASK-F2EXE\n"
            "actor: exec.test\nclaimed_at: '2026-07-28T01:00:00Z'\n",
        )
        # F2DEL: claimed + return -> delivered -> 执行中 · 已交付待审.
        _task("f2del.md", "TASK-F2DEL")
        _rec(
            "5_tasks/records/returns/TASK-F2DEL/return.md",
            "record_type: return_record\nreturn_id: rt\ntask_id: TASK-F2DEL\n"
            "actor: exec.test\nreturned_at: '2026-07-28T02:00:00Z'\n",
        )
        # F2VP & F2DOSSIER: both claimed + identical PASS verdict; F2DOSSIER also
        # has an archived dossier dir (F-261-4 incorporated signal).
        for tid in ("TASK-F2VP", "TASK-F2DOSSIER"):
            _task(tid.lower() + ".md", tid)
            _rec(
                f"5_tasks/records/audit_verdicts/{tid}/verdict.md",
                "record_type: audit_verdict_record\nverdict_id: vd\nverdict: PASS\n"
                f"reviewed_task_id: {tid}\nactor: audit.test\n"
                "verdict_at: '2026-07-28T03:00:00Z'\n",
            )
        # The incorporated dossier dir (F-261-4): dir exists => closed.
        (self.repo_root / "5_tasks" / "queue" / "completed" / "TASK-F2DOSSIER").mkdir(
            parents=True, exist_ok=True
        )

        response = get_owner_truth_view(repo_root=self.repo_root)
        self.assertTrue(response["ok"], response)
        data = response["data"]
        rows = {t["task_id"]: t for t in data["tasks"]}

        # F-261-4: identical records, different fate — dossier dir flips F2DOSSIER
        # to closed while F2VP stays verdict_pass (判决 PASS).
        self.assertEqual(rows["TASK-F2VP"]["true_stage"], "verdict_pass")
        self.assertEqual(rows["TASK-F2DOSSIER"]["true_stage"], "closed")

        # F-261-3: top_level_state + composed badge_label per row.
        expect = {
            "TASK-F2PUB": ("published", "已发布"),
            "TASK-F2EXE": ("executing", "执行中"),
            "TASK-F2DEL": ("executing", "执行中 · 已交付待审"),
            "TASK-F2VP": ("executing", "执行中 · 判决 PASS"),
            "TASK-F2DOSSIER": ("closed", "已闭环"),
        }
        for tid, (tl, badge) in expect.items():
            self.assertEqual(rows[tid]["top_level_state"], tl, tid)
            self.assertEqual(rows[tid]["badge_label"], badge, tid)
        # Fine-grained sub-state badge color key is retained (not removed).
        self.assertEqual(rows["TASK-F2VP"]["stage_label"], "判决 PASS")

        # F-261-3: top_level_counts collapse to exactly three buckets, sum == total.
        tlc = data["top_level_counts"]
        self.assertEqual(set(tlc.keys()), {"published", "executing", "closed"})
        self.assertEqual(sum(tlc.values()), len(data["tasks"]))
        self.assertEqual(tlc["published"], 1)   # F2PUB
        self.assertEqual(tlc["executing"], 3)   # F2EXE + F2DEL + F2VP
        self.assertEqual(tlc["closed"], 1)       # F2DOSSIER (dossier signal)
        self.assertEqual(response["summary"]["top_level_counts"], tlc)

        # Helpers pinned directly (pure functions).
        self.assertEqual(top_level_state("delivered"), "executing")
        self.assertEqual(top_level_state("verdict_fail"), "executing")
        self.assertEqual(top_level_state("pending"), "published")
        self.assertEqual(top_level_state("blocked"), "published")
        self.assertEqual(top_level_state(None), "published")
        self.assertEqual(badge_label_for("verdict_fail", "executing", "判决 FAIL"), "执行中 · 判决 FAIL")
        self.assertEqual(badge_label_for("executing", "executing", "执行中"), "执行中")
        self.assertEqual(badge_label_for("published", "published", "已发布"), "已发布")


if __name__ == "__main__":
    unittest.main()
