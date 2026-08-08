"""AIPOS-FND-7F1 — 审计流支持同卡 FAIL 后复审改判(FAIL 非终态,PASS 终态)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.board_adapter import audit_dispatch_task, audit_verdict_task


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
        "context_bundle": "exec.test",
        "task_mode": "code",
        "model_tier": "L2",
        "priority": "medium",
        "status": "pending",
        "created_by": "advisor.test.dev",
        "needs_owner": False,
        "output_target": "tools/",
        "artifact_policy": "formal_write",
        "session_policy": "single_task_session",
        "context_isolation": "strict",
        "artifact_scope": "tools/",
        "memory_scope": "task",
    }
    base.update(extra)
    return base


class TestFailRereviewAllowed(unittest.TestCase):
    """验收1: FAIL 后可以 re-dispatch 和复审改判为 PASS."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ["5_tasks/queue/pending", "5_tasks/queue/claimed", 
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

    def test_fail_verdict_allows_redispatch_and_pass(self):
        """FAIL 裁决后,可以 re-dispatch 并改判为 PASS."""
        # 1. 准备已完成任务
        _write_md(
            self.root / "5_tasks/queue/claimed/fnd-1.md",
            _minimal_task("FND-1", 
                status="claimed",
                executor_status="completed",
                audit_readiness="ready",
                agent_instance="exec.test.dev",
                claim_id="claim_FND-1_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                claimed_at="2026-08-08T00:00:00Z",
                active_session_id="session_FND-1_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_FND-1_20260808_exec-test-dev",
                executor_registry_verified=True,
                related_audit_task_ref="FND-1R1",
                audit_dispatch_record_ref="dispatch_FND-1_20260808_advisor-test-dev",
                dependency_audit_status="FAIL",
                audit_verdict="FAIL",
                related_audit_verdict_ref="verdict_FND-1_20260808_audit-test-dev",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/FND-1/return_FND-1_20260808_exec-test-dev.md",
            {"record_id": "return_FND-1_20260808_exec-test-dev", "record_type": "return_record", "task_id": "FND-1"},
        )
        
        # 2. 第一轮审计任务
        _write_md(
            self.root / "5_tasks/queue/claimed/fnd-1r1.md",
            _minimal_task("FND-1R1",
                task_mode="audit",
                status="claimed",
                reviewed_task_id="FND-1",
                active_session_id="session_FND-1R1_20260808_audit-test-dev",
                claim_id="claim_FND-1R1_20260808_audit-test-dev",
                claimed_by="audit.test.dev",
                agent_instance="audit.test.dev",
                audit_dispatch_record_ref="dispatch_FND-1_20260808_advisor-test-dev",
                audit_verdict="FAIL",
                related_audit_verdict_ref="verdict_FND-1_20260808_audit-test-dev",
            ),
        )
        
        # 3. 第一轮 dispatch 记录
        _write_md(
            self.root / "5_tasks/records/audit_dispatches/FND-1/dispatch_FND-1_20260808_advisor-test-dev.md",
            {
                "record_id": "dispatch_FND-1_20260808_advisor-test-dev",
                "record_type": "audit_dispatch_record",
                "reviewed_task_id": "FND-1",
                "audit_task_id": "FND-1R1",
            },
        )
        
        # 4. 第一轮 FAIL 裁决记录
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/FND-1/verdict_FND-1_20260808_audit-test-dev.md",
            {
                "record_id": "verdict_FND-1_20260808_audit-test-dev",
                "record_type": "audit_verdict_record",
                "reviewed_task_id": "FND-1",
                "audit_task_id": "FND-1R1",
                "verdict": "FAIL",
                "verdict_at": "2026-08-08T01:00:00Z",
            },
        )
        
        # 5. 现在尝试 re-dispatch(第二轮审计)
        result = audit_dispatch_task(
            source_task_id="FND-1",
            actor="advisor.test.dev",
            agent_instance="advisor.test.dev",
            owner_policy_ref="pol_test_1",
            audit_task_id="FND-1R2",
            audit_agent_instance="audit.test.dev",
            dry_run=False,
            repo_root=self.root,
        )
        
        # 验证: 应该成功(FAIL 是非终态)
        self.assertNotEqual(result.get("verdict"), "BLOCK", 
                           f"FAIL 后 re-dispatch 应成功: {result.get('blocking_reasons')}")
        self.assertTrue(result.get("ok"))
        
        # FND-1R2 审计任务应创建
        audit_task = self.root / "5_tasks/queue/pending/fnd-1r2.md"
        self.assertTrue(audit_task.exists(), "FND-1R2 审计任务应创建")
        
        # 6. 读取新的 dispatch 记录
        dispatch_dir = self.root / "5_tasks/records/audit_dispatches/FND-1"
        dispatch_files = sorted(dispatch_dir.glob("*.md"))
        self.assertEqual(len(dispatch_files), 2, "应有 2 个 dispatch 记录")
        new_dispatch_id = dispatch_files[1].stem
        
        # 7. 移动 FND-1R2 到 claimed 并补全字段
        import shutil
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        
        pending_path = self.root / "5_tasks/queue/pending/fnd-1r2.md"
        claimed_path = self.root / "5_tasks/queue/claimed/fnd-1r2.md"
        shutil.move(str(pending_path), str(claimed_path))
        
        content = claimed_path.read_text()
        meta, body, _ = parse_markdown_frontmatter(content)
        meta["status"] = "claimed"
        meta["claimed_by"] = "audit.test.dev"
        meta["claimed_at"] = "2026-08-08T02:00:00Z"
        meta["active_session_id"] = "session_FND-1R2_20260808_audit-test-dev"
        meta["claim_id"] = "claim_FND-1R2_20260808_audit-test-dev"
        meta["audit_dispatch_record_ref"] = new_dispatch_id
        meta["reviewed_executor_instance"] = "exec.test.dev"
        _write_md(claimed_path, meta, body)
        
        _write_md(
            self.root / "5_tasks/records/sessions/FND-1R2/session_FND-1R2_20260808_audit-test-dev.md",
            {"session_id": "session_FND-1R2_20260808_audit-test-dev", "record_type": "session_record", "task_id": "FND-1R2"},
        )
        
        # 8. 提交第二轮 PASS 裁决
        verdict_result = audit_verdict_task(
            audit_task_id="FND-1R2",
            reviewed_task_id="FND-1",
            actor="audit.test.dev",
            agent_instance="audit.test.dev",
            owner_policy_ref="pol_test_1",
            verdict="PASS",
            findings_summary="Fixed, now good",
            evidence_refs=[],
            dry_run=False,
            repo_root=self.root,
        )
        
        # 验证: PASS 应成功(FAIL 可以改判)
        self.assertNotEqual(verdict_result.get("verdict"), "BLOCK",
                           f"FAIL 后复审 PASS 应成功: {verdict_result.get('blocking_reasons')}")
        self.assertTrue(verdict_result.get("ok"))
        
        # 9. 验证审计链保留(两条 verdict 记录)
        verdict_dir = self.root / "5_tasks/records/audit_verdicts/FND-1"
        verdict_files = list(verdict_dir.glob("*.md"))
        self.assertEqual(len(verdict_files), 2, "应保留 2 条裁决记录(FAIL + PASS)")


class TestPassVerdictTerminal(unittest.TestCase):
    """验收2: PASS 是终态,不可翻案."""

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

    def test_pass_verdict_blocks_redispatch(self):
        """PASS 裁决后,不能 re-dispatch."""
        # 1. 准备已 PASS 的任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-pass.md",
            _minimal_task("TEST-PASS", 
                status="claimed",
                executor_status="completed",
                audit_readiness="ready",
                agent_instance="exec.test.dev",
                claim_id="claim_TEST-PASS_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_TEST-PASS_20260808_exec-test-dev",
                executor_registry_verified=True,
                related_audit_task_ref="TEST-PASS-R1",
                audit_dispatch_record_ref="dispatch_TEST-PASS_20260808_advisor-test-dev",
                dependency_audit_status="PASS",
                audit_status="PASS",
                audit_verdict="PASS",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/TEST-PASS/return_TEST-PASS_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-PASS_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-PASS"},
        )
        
        # PASS 裁决记录
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-PASS/verdict_TEST-PASS_20260808_audit-test-dev.md",
            {
                "record_id": "verdict_TEST-PASS_20260808_audit-test-dev",
                "record_type": "audit_verdict_record",
                "reviewed_task_id": "TEST-PASS",
                "verdict": "PASS",
                "verdict_at": "2026-08-08T01:00:00Z",
            },
        )
        
        # 2. 尝试 re-dispatch
        result = audit_dispatch_task(
            source_task_id="TEST-PASS",
            actor="advisor.test.dev",
            agent_instance="advisor.test.dev",
            owner_policy_ref="pol_test_1",
            audit_task_id="TEST-PASS-R2",
            audit_agent_instance="audit.test.dev",
            dry_run=True,
            repo_root=self.root,
        )
        
        # 验证: 应该 BLOCK(PASS 是终态)
        self.assertEqual(result.get("verdict"), "BLOCK")
        blocking = result.get("blocking_reasons", [])
        self.assertTrue(
            any("AUDIT_ALREADY_PASSED" in str(r) for r in blocking),
            f"PASS 后不应允许 re-dispatch: {blocking}"
        )

    def test_pass_verdict_blocks_overturn(self):
        """PASS 裁决后,即使有新审计任务也不能改判."""
        # 这个场景理论上不应该发生(因为 re-dispatch 会被 BLOCK),
        # 但从 verdict 提交层面也应该有防护
        
        # 1. 准备已 PASS 的被审任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-pass2.md",
            _minimal_task("TEST-PASS2",
                status="claimed",
                executor_status="completed",
                return_record_ref="return_TEST-PASS2_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/TEST-PASS2/return_TEST-PASS2_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-PASS2_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-PASS2"},
        )
        
        # PASS 裁决记录
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-PASS2/verdict_TEST-PASS2_20260808_audit-test-dev.md",
            {
                "record_id": "verdict_TEST-PASS2_20260808_audit-test-dev",
                "record_type": "audit_verdict_record",
                "reviewed_task_id": "TEST-PASS2",
                "verdict": "PASS",
                "verdict_at": "2026-08-08T01:00:00Z",
            },
        )
        
        # 2. 假设绕过 dispatch 检查,手工创建第二轮审计任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-pass2-r2.md",
            _minimal_task("TEST-PASS2-R2",
                task_mode="audit",
                status="claimed",
                reviewed_task_id="TEST-PASS2",
                active_session_id="session_TEST-PASS2-R2_20260808_audit-test-dev",
                claim_id="claim_TEST-PASS2-R2_20260808_audit-test-dev",
                claimed_by="audit.test.dev",
                agent_instance="audit.test.dev",
                reviewed_executor_instance="exec.test.dev",
                audit_dispatch_record_ref="dispatch_TEST-PASS2_20260808_advisor-test-dev",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/audit_dispatches/TEST-PASS2/dispatch_TEST-PASS2_20260808_advisor-test-dev.md",
            {
                "record_id": "dispatch_TEST-PASS2_20260808_advisor-test-dev",
                "record_type": "audit_dispatch_record",
                "reviewed_task_id": "TEST-PASS2",
            },
        )
        
        _write_md(
            self.root / "5_tasks/records/sessions/TEST-PASS2-R2/session_TEST-PASS2-R2_20260808_audit-test-dev.md",
            {"session_id": "session_TEST-PASS2-R2_20260808_audit-test-dev", "record_type": "session_record", "task_id": "TEST-PASS2-R2"},
        )
        
        # 3. 尝试提交 FAIL 裁决(试图翻案)
        result = audit_verdict_task(
            audit_task_id="TEST-PASS2-R2",
            reviewed_task_id="TEST-PASS2",
            actor="audit.test.dev",
            agent_instance="audit.test.dev",
            owner_policy_ref="pol_test_1",
            verdict="FAIL",
            findings_summary="Overturn attempt",
            evidence_refs=[],
            dry_run=True,
            repo_root=self.root,
        )
        
        # 验证: 应该 BLOCK(PASS 不可翻案)
        self.assertEqual(result.get("verdict"), "BLOCK")
        blocking = result.get("blocking_reasons", [])
        self.assertTrue(
            any("cannot overturn PASS" in str(r) for r in blocking),
            f"PASS 不可翻案: {blocking}"
        )


class TestRequestChangesRereviewAllowed(unittest.TestCase):
    """验收3: REQUEST_CHANGES 也是非终态,可以复审."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ["5_tasks/queue/pending", "5_tasks/queue/claimed",
                  "5_tasks/records/returns", "5_tasks/records/audit_dispatches",
                  "5_tasks/records/audit_verdicts", "5_tasks/records/sessions",
                  "agent_profiles"]:
            (self.root / d).mkdir(parents=True)
        
        for inst in ["exec.test.dev", "audit.test.dev", "advisor.test.dev"]:
            _write_md(self.root / f"agent_profiles/{inst}.md", {
                "instance_id": inst,
                "role": inst.split(".")[0],
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_request_changes_allows_redispatch(self):
        """REQUEST_CHANGES 裁决后,可以 re-dispatch."""
        # 准备 REQUEST_CHANGES 的任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-rc.md",
            _minimal_task("TEST-RC", 
                status="claimed",
                executor_status="completed",
                audit_readiness="ready",
                agent_instance="exec.test.dev",
                claim_id="claim_TEST-RC_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                claimed_at="2026-08-08T00:00:00Z",
                active_session_id="session_TEST-RC_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_TEST-RC_20260808_exec-test-dev",
                executor_registry_verified=True,
                related_audit_task_ref="TEST-RC-R1",
                audit_dispatch_record_ref="dispatch_TEST-RC_20260808_advisor-test-dev",
                dependency_audit_status="REQUEST_CHANGES",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/TEST-RC/return_TEST-RC_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-RC_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-RC"},
        )
        
        # REQUEST_CHANGES 裁决
        _write_md(
            self.root / "5_tasks/records/audit_verdicts/TEST-RC/verdict_TEST-RC_20260808_audit-test-dev.md",
            {
                "record_id": "verdict_TEST-RC_20260808_audit-test-dev",
                "record_type": "audit_verdict_record",
                "reviewed_task_id": "TEST-RC",
                "verdict": "REQUEST_CHANGES",
                "verdict_at": "2026-08-08T01:00:00Z",
            },
        )
        
        # re-dispatch
        result = audit_dispatch_task(
            source_task_id="TEST-RC",
            actor="advisor.test.dev",
            agent_instance="advisor.test.dev",
            owner_policy_ref="pol_test_1",
            audit_task_id="TEST-RC-R2",
            audit_agent_instance="audit.test.dev",
            dry_run=True,
            repo_root=self.root,
        )
        
        # 验证: 应该成功(REQUEST_CHANGES 是非终态)
        self.assertNotEqual(result.get("verdict"), "BLOCK",
                           f"REQUEST_CHANGES 后 re-dispatch 应成功: {result.get('blocking_reasons')}")


if __name__ == "__main__":
    unittest.main()
