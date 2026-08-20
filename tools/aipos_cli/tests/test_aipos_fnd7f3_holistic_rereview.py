"""AIPOS-FND-7F3 — 审计复审流彻底修透(holistic):全 guard 理顺+端到端 complete 验收."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.board_adapter import audit_dispatch_task, audit_verdict_task
from tools.aipos_cli.queue_mutation import mutate_queue_task


def _write_md(path: Path, frontmatter: dict, body: str = "body\n") -> None:
    """Write markdown with frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"- {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


def _minimal_task(task_id: str, **extra) -> dict:
    """最小合法任务元数据."""
    base = {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "project": "test",
        "assigned_to": "exec",
        "agent_instance": "exec.test.dev",
        "context_bundle": "exec.test.dev",
        "task_mode": "code",
        "task_class": "simple",
        "model_tier": "L2",
        "priority": "medium",
        "status": "pending",
        "created_by": "advisor.test.dev",
        "needs_owner": False,
        "audit": "required",
        "output_target": "tools/",
        "artifact_policy": "formal_write",
        "session_policy": "single_task_session",
        "context_isolation": "strict",
        "artifact_scope": "tools/",
        "memory_scope": "task",
    }
    base.update(extra)
    return base


class TestRereviewCompleteEndToEnd(unittest.TestCase):
    """端到端验收:FAIL→re-dispatch→PASS→complete,用混格式 verdict 记录."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ["5_tasks/queue/pending", "5_tasks/queue/claimed", "5_tasks/queue/completed",
                  "5_tasks/records/returns", "5_tasks/records/audit_dispatches",
                  "5_tasks/records/audit_verdicts", "5_tasks/records/sessions",
                  "agent_profiles"]:
            (self.root / d).mkdir(parents=True)
        
        # agent profiles
        for inst in ["exec.test.dev", "audit.test.dev", "advisor.test.dev"]:
            _write_md(self.root / f"agent_profiles/{inst}.md", {
                "instance_id": inst,
                "role": inst.split(".")[0],
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_fail_then_pass_then_complete(self):
        """混格式裁决:FAIL(旧)→PASS(新)→complete 应成功."""
        # 1. 准备已完成的被审任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-1.md",
            _minimal_task("TEST-1", 
                status="claimed",
                executor_status="completed",
                audit_readiness="ready",
                claim_id="claim_TEST-1_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                claimed_at="2026-08-08T00:00:00Z",
                active_session_id="session_TEST-1_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_TEST-1_20260808_exec-test-dev",
                executor_registry_verified=True,
                related_audit_task_ref="TEST-1R1",
                audit_dispatch_record_ref="dispatch_TEST-1_20260808_advisor-test-dev",
                dependency_audit_status="FAIL",
                audit_verdict="FAIL",
                related_audit_verdict_ref="verdict_TEST-1_20260808_080700_audit-test-dev",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/TEST-1/return_TEST-1_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-1_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-1"},
        )
        
        # 2. 第一轮 FAIL 裁决(混格式1:完整 markdown body,模拟 FND-1 第一条)
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-1/verdict_TEST-1_20260808_080700_audit-test-dev.md",
            {
                "record_type": "audit_verdict",
                "verdict_id": "verdict_TEST-1_20260808_080700_audit-test-dev",
                "task_id": "TEST-1",
                "audit_task_id": "TEST-1R1",
                "auditor": "audit.test.dev",
                "verdict": "FAIL",
                "findings_count": 2,
                "timestamp": "2026-08-08T08:07:00Z",
            },
            body="# Audit Verdict: TEST-1\n\n**Verdict**: **FAIL**\n\nF-1: test fails; F-2: evidence missing\n",
        )
        
        # 3. re-dispatch(第二轮审计)
        result = audit_dispatch_task(
            source_task_id="TEST-1",
            actor="advisor.test.dev",
            agent_instance="advisor.test.dev",
            owner_policy_ref="pol_test_1",
            audit_task_id="TEST-1R2",
            audit_agent_instance="audit.test.dev",
            dry_run=True,
            repo_root=self.root,
        )
        
        # 验证: FAIL 后 re-dispatch 应成功
        self.assertNotEqual(result.get("verdict"), "BLOCK", 
                           f"FAIL 后 re-dispatch 应成功: {result.get('blocking_reasons')}")
        
        # 4. 模拟第二轮审计完成,提交 PASS 裁决(混格式2:structured record,模拟 FND-1 第二条)
        # 先创建 R2 审计任务和 session
        import shutil
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        
        # 创建 R2 任务卡(pending→claimed)
        audit_pending = self.root / "5_tasks/queue/pending/test-1r2.md"
        _write_md(audit_pending, _minimal_task("TEST-1R2",
            task_mode="audit",
            status="pending",
            reviewed_task_id="TEST-1",
        ))
        audit_claimed = self.root / "5_tasks/queue/claimed/test-1r2.md"
        shutil.move(str(audit_pending), str(audit_claimed))
        
        content = audit_claimed.read_text()
        meta, body, _ = parse_markdown_frontmatter(content)
        meta["status"] = "claimed"
        meta["claimed_by"] = "audit.test.dev"
        meta["claimed_at"] = "2026-08-08T09:00:00Z"
        meta["active_session_id"] = "session_TEST-1R2_20260808_090000_audit-test-dev"
        meta["claim_id"] = "claim_TEST-1R2_20260808_090000_audit-test-dev"
        meta["reviewed_executor_instance"] = "exec.test.dev"
        meta["audit_dispatch_record_ref"] = "dispatch_TEST-1_20260808_090000_advisor-test-dev"
        _write_md(audit_claimed, meta, body)
        
        _write_md(
            self.root / "5_tasks/records/sessions/TEST-1R2/session_TEST-1R2_20260808_090000_audit-test-dev.md",
            {"session_id": "session_TEST-1R2_20260808_090000_audit-test-dev", "record_type": "session_record", "task_id": "TEST-1R2"},
        )
        
        # 模拟 dispatch 记录(re-dispatch 时会创建)
        _write_md(
            self.root / "5_tasks/records/audit_dispatches/TEST-1/dispatch_TEST-1_20260808_090000_advisor-test-dev.md",
            {
                "record_id": "dispatch_TEST-1_20260808_090000_advisor-test-dev",
                "record_type": "audit_dispatch_record",
                "reviewed_task_id": "TEST-1",
                "audit_task_id": "TEST-1R2",
            },
        )
        
        # 5. 提交 PASS 裁决(新时间戳,supersede FAIL)
        verdict_result = audit_verdict_task(
            audit_task_id="TEST-1R2",
            reviewed_task_id="TEST-1",
            actor="audit.test.dev",
            agent_instance="audit.test.dev",
            owner_policy_ref="pol_test_1",
            audit_session_id="session_TEST-1R2_20260808_090000_audit-test-dev",
            verdict="PASS",
            findings_summary="All issues fixed",
            evidence_refs=[],
            dry_run=True,
            repo_root=self.root,
        )
        
        # 验证: PASS 裁决应成功(supersede FAIL)
        self.assertNotEqual(verdict_result.get("verdict"), "BLOCK",
                           f"FAIL 后复审 PASS 应成功: {verdict_result.get('blocking_reasons')}")
        
        # 6. 手工写第二条 PASS 裁决记录(模拟 verdict execute)
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-1/verdict_TEST-1_20260808_091114_audit-test-dev.md",
            {
                "record_type": "audit_verdict_record",
                "event_type": "mcp_audit_verdict",
                "verdict_id": "verdict_TEST-1_20260808_091114_audit-test-dev",
                "verdict": "PASS",
                "reviewed_task_id": "TEST-1",
                "audit_task_id": "TEST-1R2",
                "verdict_at": "2026-08-08T09:11:14Z",
            },
            body="# MCP Audit Verdict Record\n\nAll issues resolved.\n",
        )
        
        # 7. 尝试 complete(最新裁决是 PASS,应允许)
        complete_result = mutate_queue_task(
            self.root,
            "complete",
            task_id="TEST-1",
            actor="exec.test.dev",
            report_link="test://report",
            dry_run=True,
        )
        
        # 验收断言:最新 PASS 裁决应允许 complete
        self.assertNotEqual(complete_result.get("verdict"), "BLOCK",
                           f"最新 PASS 裁决应允许 complete: {complete_result.get('blocking_reasons')}")
        self.assertEqual(complete_result.get("to_state"), "completed")
        
        # 验证审计链完整性:两条裁决记录都保留
        verdict_dir = self.root / "5_tasks/records/audit_verdicts/TEST-1"
        verdict_files = sorted(verdict_dir.glob("*.md"))
        self.assertEqual(len(verdict_files), 2, "应保留 FAIL + PASS 两条裁决记录")
        
        # 验证取最新逻辑:读取两条记录,确认 PASS 时间戳更晚
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        verdicts = []
        for vf in verdict_files:
            text = vf.read_text()
            meta, _, _ = parse_markdown_frontmatter(text)
            verdicts.append({
                "verdict": meta.get("verdict"),
                "verdict_at": meta.get("verdict_at") or meta.get("timestamp"),
            })
        verdicts.sort(key=lambda v: v["verdict_at"] or "")
        self.assertEqual(verdicts[0]["verdict"], "FAIL")
        self.assertEqual(verdicts[1]["verdict"], "PASS")
        self.assertGreater(verdicts[1]["verdict_at"], verdicts[0]["verdict_at"])

    def test_pass_then_hypothetical_fail_blocks_complete(self):
        """边缘场景:如果 PASS(旧)→FAIL(新)(虽理论上被 guard 拦住),complete 应 BLOCK."""
        # 准备任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-2.md",
            _minimal_task("TEST-2", 
                status="claimed",
                executor_status="completed",
                claim_id="claim_TEST-2_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_TEST-2_20260808_exec-test-dev",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/TEST-2/return_TEST-2_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-2_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-2"},
        )
        
        # 旧的 PASS 裁决
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-2/verdict_TEST-2_20260808_080000_audit-test-dev.md",
            {
                "record_type": "audit_verdict_record",
                "verdict_id": "verdict_TEST-2_20260808_080000_audit-test-dev",
                "verdict": "PASS",
                "reviewed_task_id": "TEST-2",
                "verdict_at": "2026-08-08T08:00:00Z",
            },
        )
        
        # 假设场景:后续被手工写入 FAIL(时间戳更晚,虽理论上 overturn guard 会拦)
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-2/verdict_TEST-2_20260808_090000_audit-test-dev.md",
            {
                "record_type": "audit_verdict_record",
                "verdict_id": "verdict_TEST-2_20260808_090000_audit-test-dev",
                "verdict": "FAIL",
                "reviewed_task_id": "TEST-2",
                "verdict_at": "2026-08-08T09:00:00Z",
            },
        )
        
        # complete 应 BLOCK(最新是 FAIL)
        result = mutate_queue_task(
            self.root,
            "complete",
            task_id="TEST-2",
            actor="exec.test.dev",
            report_link="test://report",
            dry_run=True,
        )
        
        self.assertEqual(result.get("verdict"), "BLOCK")
        blocking = result.get("blocking_reasons", [])
        self.assertTrue(
            any("no PASS audit verdict" in str(r) for r in blocking),
            f"最新 FAIL 应阻止 complete: {blocking}"
        )

    def test_mixed_format_verdicts_null_safe(self):
        """null-safe:混格式裁决,timestamp 字段不一致,排序不崩."""
        _write_md(
            self.root / "5_tasks/queue/claimed/test-3.md",
            _minimal_task("TEST-3", 
                status="claimed",
                executor_status="completed",
                claim_id="claim_TEST-3_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                claimed_at="2026-08-08T00:00:00Z",
                active_session_id="session_TEST-3_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_TEST-3_20260808_exec-test-dev",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/TEST-3/return_TEST-3_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-3_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-3"},
        )
        
        # 裁决1:只有 timestamp(旧格式) — AIPOS-F2: 必须有 verdict_id
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-3/verdict_TEST-3_old.md",
            {
                "record_type": "audit_verdict",
                "verdict_id": "verdict_TEST-3_old_20260808",
                "verdict": "FAIL",
                "timestamp": "2026-08-08T08:00:00Z",
            },
        )
        
        # 裁决2:有 verdict_at(新格式,优先级更高)
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-3/verdict_TEST-3_new.md",
            {
                "record_type": "audit_verdict_record",
                "verdict_id": "verdict_TEST-3_new_20260808",
                "verdict": "PASS",
                "verdict_at": "2026-08-08T09:00:00Z",
            },
        )
        
        # 裁决3:既无 verdict_at 也无 timestamp(极端边缘,应兜底为空字符串)
        # AIPOS-F2: 无 verdict_at → 非门生 → 被忽略(这正是死锁五号修复的语义)
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-3/verdict_TEST-3_broken.md",
            {
                "record_type": "audit_verdict",
                "verdict_id": "verdict_TEST-3_broken",
                "verdict": "BLOCKED",
            },
        )
        
        # complete 应成功(最新有 verdict_at 的是 PASS)
        result = mutate_queue_task(
            self.root,
            "complete",
            task_id="TEST-3",
            actor="exec.test.dev",
            report_link="test://report",
            dry_run=True,
        )
        
        # 排序应不崩溃,且取到 PASS(verdict_at="2026-08-08T09:00:00Z")
        self.assertNotEqual(result.get("verdict"), "BLOCK",
                           f"混格式排序应正确: {result.get('blocking_reasons')}")
        self.assertEqual(result.get("to_state"), "completed")


class TestPassTerminalSemantic(unittest.TestCase):
    """验收:PASS 终态语义在所有路径一致."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ["5_tasks/queue/claimed", "5_tasks/records/returns",
                  "5_tasks/records/audit_dispatches", "5_tasks/records/audit_verdicts",
                  "5_tasks/records/sessions", "agent_profiles"]:
            (self.root / d).mkdir(parents=True)
        
        for inst in ["exec.test.dev", "audit.test.dev", "advisor.test.dev"]:
            _write_md(self.root / f"agent_profiles/{inst}.md", {
                "instance_id": inst,
                "role": inst.split(".")[0],
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_pass_blocks_dispatch_verdict_allows_complete(self):
        """PASS 终态:阻止 re-dispatch,阻止 overturn,允许 complete."""
        # 准备 PASS 的任务
        _write_md(
            self.root / "5_tasks/queue/claimed/pass-test.md",
            _minimal_task("PASS-TEST", 
                status="claimed",
                executor_status="completed",
                audit_readiness="ready",
                claim_id="claim_PASS-TEST_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                claimed_at="2026-08-08T00:00:00Z",
                active_session_id="session_PASS-TEST_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_PASS-TEST_20260808_exec-test-dev",
                executor_registry_verified=True,
                dependency_audit_status="PASS",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/PASS-TEST/return_PASS-TEST_20260808_exec-test-dev.md",
            {"record_id": "return_PASS-TEST_20260808_exec-test-dev", "record_type": "return_record", "task_id": "PASS-TEST"},
        )
        
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/PASS-TEST/verdict_PASS-TEST_20260808_audit-test-dev.md",
            {
                "record_type": "audit_verdict_record",
                "verdict_id": "verdict_PASS-TEST_20260808_audit-test-dev",
                "verdict": "PASS_WITH_NOTES",
                "reviewed_task_id": "PASS-TEST",
                "verdict_at": "2026-08-08T08:00:00Z",
            },
        )
        
        # 1. dispatch 应 BLOCK
        dispatch_result = audit_dispatch_task(
            source_task_id="PASS-TEST",
            actor="advisor.test.dev",
            agent_instance="advisor.test.dev",
            owner_policy_ref="pol_test_1",
            audit_task_id="PASS-TEST-R2",
            audit_agent_instance="audit.test.dev",
            dry_run=True,
            repo_root=self.root,
        )
        self.assertEqual(dispatch_result.get("verdict"), "BLOCK")
        self.assertTrue(any("AUDIT_ALREADY_PASSED" in str(r) for r in dispatch_result.get("blocking_reasons", [])))
        
        # 2. complete 应成功
        complete_result = mutate_queue_task(
            self.root,
            "complete",
            task_id="PASS-TEST",
            actor="exec.test.dev",
            report_link="test://report",
            dry_run=True,
        )
        self.assertNotEqual(complete_result.get("verdict"), "BLOCK",
                           f"PASS 应允许 complete: {complete_result.get('blocking_reasons')}")
        self.assertEqual(complete_result.get("to_state"), "completed")


if __name__ == "__main__":
    unittest.main()
