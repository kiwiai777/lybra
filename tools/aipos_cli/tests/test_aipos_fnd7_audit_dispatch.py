"""AIPOS-FND-7 — 审计派工并进单一大脑,自动建 dispatch 记录,修护栏一致性."""
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


class TestAuditDispatchRecordGeneration(unittest.TestCase):
    """验收1: 派审自动建 audit_dispatch 记录."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "5_tasks/queue/pending").mkdir(parents=True)
        (self.root / "5_tasks/queue/claimed").mkdir(parents=True)
        (self.root / "5_tasks/records/returns").mkdir(parents=True)
        (self.root / "5_tasks/records/audit_dispatches").mkdir(parents=True)
        (self.root / "agent_profiles").mkdir(parents=True)
        
        # 写 agent profiles (AIPOS-219 独立性校验需要)
        for inst in ["exec.test.dev", "audit.test.dev", "advisor.test.dev"]:
            _write_md(self.root / f"agent_profiles/{inst}.md", {
                "instance_id": inst,
                "role": inst.split(".")[0],
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_dispatch_creates_audit_dispatch_record(self):
        """验收1: audit_dispatch_task 自动落 audit_dispatch 记录."""
        # 准备已完成任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-1.md",
            _minimal_task("TEST-1", 
                status="claimed",
                executor_status="completed",
                audit_readiness="ready",
                agent_instance="exec.test.dev",
                claim_id="claim_TEST-1_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                claimed_at="2026-08-08T00:00:00Z",
                active_session_id="session_TEST-1_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
                return_record_ref="return_TEST-1_20260808_exec-test-dev",
                executor_registry_verified=True,
            ),
        )
        
        # return 记录
        _write_md(
            self.root / "5_tasks/records/returns/TEST-1/return_TEST-1_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-1_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-1"},
        )
        
        # 派审
        result = audit_dispatch_task(
            source_task_id="TEST-1",
            actor="advisor.test.dev",
            agent_instance="advisor.test.dev",
            owner_policy_ref="pol_test_1",
            audit_task_id="TEST-1R1",
            audit_agent_instance="audit.test.dev",
            dry_run=False,
            repo_root=self.root,
        )
        
        # 验证
        self.assertNotEqual(result.get("verdict"), "BLOCK", 
                           f"dispatch 应成功: {result.get('blocking_reasons')}")
        self.assertTrue(result.get("ok"))
        
        # audit_dispatch 记录存在
        dispatch_dir = self.root / "5_tasks/records/audit_dispatches/TEST-1"
        self.assertTrue(dispatch_dir.exists(), "dispatch 记录目录应存在")
        dispatch_files = list(dispatch_dir.glob("*.md"))
        self.assertEqual(len(dispatch_files), 1, "应生成 1 个 dispatch 记录")
        
        # 审计任务已创建
        audit_task = self.root / "5_tasks/queue/pending/test-1r1.md"
        self.assertTrue(audit_task.exists(), "审计任务应创建")


class TestVerdictGuardrailConsistency(unittest.TestCase):
    """验收2: 首审与复审护栏一致,都要求 dispatch 记录."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ["5_tasks/queue/claimed", "5_tasks/records/returns", 
                  "5_tasks/records/audit_dispatches", "5_tasks/records/sessions",
                  "agent_profiles"]:
            (self.root / d).mkdir(parents=True)
        
        for inst in ["exec.test.dev", "audit.test.dev"]:
            _write_md(self.root / f"agent_profiles/{inst}.md", {
                "instance_id": inst,
                "role": inst.split(".")[0],
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_audit_requires_dispatch_record(self):
        """首审(非派生任务)要求 dispatch 记录."""
        # 被审任务
        _write_md(
            self.root / "5_tasks/queue/claimed/test-2.md",
            _minimal_task("TEST-2", 
                status="claimed",
                return_record_ref="return_TEST-2_20260808_exec-test-dev",
                executor_completed_by="exec.test.dev",
                claim_id="claim_TEST-2_20260808_exec-test-dev",
                claimed_by="exec.test.dev",
                claimed_at="2026-08-08T00:00:00Z",
                active_session_id="session_TEST-2_20260808_exec-test-dev",
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/returns/TEST-2/return_TEST-2_20260808_exec-test-dev.md",
            {"record_id": "return_TEST-2_20260808_exec-test-dev", "record_type": "return_record", "task_id": "TEST-2"},
        )
        
        # 审计任务(手发,无 dispatch 记录)
        _write_md(
            self.root / "5_tasks/queue/claimed/test-2r1.md",
            _minimal_task("TEST-2R1",
                task_mode="audit",
                status="claimed",
                reviewed_task_id="TEST-2",
                active_session_id="session_TEST-2R1_20260808_audit-test-dev",
                claim_id="claim_TEST-2R1_20260808_audit-test-dev",
                claimed_by="audit.test.dev",
                claimed_at="2026-08-08T01:00:00Z",
                agent_instance="audit.test.dev",
                # 无 audit_dispatch_record_ref
            ),
        )
        
        _write_md(
            self.root / "5_tasks/records/sessions/TEST-2R1/session_TEST-2R1_20260808_audit-test-dev.md",
            {"session_id": "session_TEST-2R1_20260808_audit-test-dev", "record_type": "session_record", "task_id": "TEST-2R1"},
        )
        
        # 尝试提交裁决
        result = audit_verdict_task(
            audit_task_id="TEST-2R1",
            reviewed_task_id="TEST-2",
            actor="audit.test.dev",
            agent_instance="audit.test.dev",
            owner_policy_ref="pol_test_1",
            verdict="PASS",
            findings_summary="All good",
            evidence_refs=[],
            dry_run=True,
            repo_root=self.root,
        )
        
        # 验证: BLOCK,原因是 MISSING_AUDIT_DISPATCH_RECORD
        self.assertEqual(result.get("verdict"), "BLOCK")
        blocking = result.get("blocking_reasons", [])
        self.assertTrue(
            any("MISSING_AUDIT_DISPATCH_RECORD" in str(r) for r in blocking),
            f"首审应要求 dispatch 记录: {blocking}"
        )


if __name__ == "__main__":
    unittest.main()
