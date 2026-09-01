"""
AIPOS-F70: 裁决绑精确产物测试

验收清单:
① 先红后绿·复现"审过相邻提交当审过当前产物"
② 新裁决缺artifact_subject→verdict dry_run BLOCK
③ 精确匹配路径零回归:X被裁决覆盖时finalize/deploy正常放行
④ 存量legacy裁决→警告放行且输出含legacy标注
⑤ 三处同源断言:grep证实字段清单仅verbs.schema一处
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.aipos_cli.board_adapter import _build_audit_verdict_preview
from tools.aipos_cli.deployment_authorization import find_gate_pass_verdict_for_task
from tools.aipos_cli.finalize import check_task_can_finalize
from tools.aipos_cli.record_writer import build_mcp_audit_verdict_record_markdown
from tools.schema_constants import Verdict


class TestArtifactSubjectFieldSource:
    """验收⑤: 三处同源断言 — artifact_subject字段清单只声明在verbs.schema一处"""

    def test_artifact_subject_single_source_in_verbs_schema(self):
        """grep证实artifact_subject字段定义只在verbs.schema.json"""
        schema_path = Path(__file__).parent.parent / "schema" / "verbs.schema.json"
        assert schema_path.exists(), "verbs.schema.json不存在"

        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)

        # 验证artifact_subject在lybra_audit_verdict_dry_run中定义
        verb_def = schema["verbs"]["lybra_audit_verdict_dry_run"]
        verb_params = verb_def["parameters"]["properties"]
        assert "artifact_subject" in verb_params, "verbs.schema中未定义artifact_subject"

        # 验证字段结构
        artifact_def = verb_params["artifact_subject"]
        assert artifact_def["type"] == "object"
        required_fields = {"repository", "commit_sha", "tree_hash"}
        assert set(artifact_def["properties"].keys()) >= required_fields

    def test_no_artifact_subject_schema_duplication(self):
        """验证artifact_subject字段定义没有在其他schema文件重复"""
        # 允许的文件:verbs.schema.json定义,transitions.schema.json可引用
        allowed_files = {"verbs.schema.json", "transitions.schema.json"}
        
        schema_dir = Path(__file__).parent.parent / "schema"
        for schema_file in schema_dir.glob("*.json"):
            if schema_file.name not in allowed_files:
                with open(schema_file, encoding="utf-8") as f:
                    content = f.read()
                    # 如果包含artifact_subject的字段定义(properties),视为重复
                    if '"artifact_subject"' in content and '"properties"' in content:
                        # 进一步检查是否是完整定义(有type/properties)
                        data = json.loads(content)
                        # 递归查找artifact_subject定义
                        if self._find_artifact_subject_definition(data):
                            pytest.fail(
                                f"artifact_subject字段定义在{schema_file.name}中重复,违反单一源原则"
                            )

    def _find_artifact_subject_definition(self, obj, path=""):
        """递归查找artifact_subject的完整定义(非引用)"""
        if isinstance(obj, dict):
            if "artifact_subject" in obj and isinstance(obj["artifact_subject"], dict):
                art_obj = obj["artifact_subject"]
                # 完整定义:有type和properties
                if "type" in art_obj and "properties" in art_obj:
                    return True
            for key, value in obj.items():
                if self._find_artifact_subject_definition(value, f"{path}.{key}"):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if self._find_artifact_subject_definition(item, path):
                    return True
        return False


class TestVerdictDryRunFailClosed:
    """验收②: 新裁决缺artifact_subject→BLOCK(fail-closed)"""

    def test_code_task_missing_artifact_subject_blocks(self):
        """task_mode=code的被审卡缺artifact_subject→BLOCK
        
        AIPOS-F70-fix1: 将 skip 改为单元测试,用 _build_audit_verdict_preview 直接测试校验逻辑。
        完整的两跳集成测试在 test_aipos_f70_fix1_snapshot_stable.py 覆盖。
        """
        # 使用 _build_audit_verdict_preview 的最小化 mock 测试校验逻辑
        # 这个测试验证: task_mode=code 且缺 artifact_subject 时,blocking_reasons 包含 AIPOS-F70 错误
        # 实际的两跳流程(dry_run → confirm)在 test_aipos_f70_fix1 中测试
        from unittest.mock import MagicMock, patch
        from tools.aipos_cli.board_adapter import _build_audit_verdict_preview
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            # 创建最小结构
            (repo_root / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "returns" / "TASK-X").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "publishes").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "sessions" / "AUDIT-X").mkdir(parents=True)
            (repo_root / "0_control_plane" / "agent_profiles").mkdir(parents=True)
            
            # 被审任务(code 模式)
            task_x = repo_root / "5_tasks" / "queue" / "claimed" / "TASK-X.md"
            task_x.write_text(
                "---\ntask_id: TASK-X\nstatus: claimed\ntask_mode: code\n---\n# X\n",
                encoding="utf-8"
            )
            
            # 审计任务
            audit_x = repo_root / "5_tasks" / "queue" / "claimed" / "AUDIT-X.md"
            audit_x.write_text(
                "---\ntask_id: AUDIT-X\nstatus: claimed\nreviewed_task_id: TASK-X\n"
                "created_by: gate_derivation\nreviewed_executor_instance: exec.test\n---\n# Audit\n",
                encoding="utf-8"
            )
            
            # 支撑记录
            (repo_root / "5_tasks" / "records" / "returns" / "TASK-X" / "return_x.md").write_text(
                "---\nrecord_type: return_record\ncanonical_agent_instance: exec.test\n---\n# R\n",
                encoding="utf-8"
            )
            (repo_root / "5_tasks" / "records" / "publishes" / "publish_AUDIT-X.md").write_text(
                "---\nrecord_type: publish_record\npublish_id: publish_AUDIT-X\n---\n# P\n",
                encoding="utf-8"
            )
            (repo_root / "5_tasks" / "records" / "sessions" / "AUDIT-X" / "session_x.md").write_text(
                "---\nrecord_type: session_record\nsession_id: session_x\nevent_count: 0\n---\n# S\n",
                encoding="utf-8"
            )
            (repo_root / "0_control_plane" / "agent_profiles" / "profiles.yaml").write_text(
                "agents:\n  - id: exec.test\n    role: executor\n  - id: audit.test\n    role: auditor\n",
                encoding="utf-8"
            )
            
            # 调用 preview(缺 artifact_subject)
            result = _build_audit_verdict_preview(
                audit_task_id="AUDIT-X",
                audit_task_path=None,
                reviewed_task_id="TASK-X",
                actor="audit.test",
                agent_instance="audit.test",
                owner_policy_ref="pol_test",
                audit_claim_id=None,
                audit_session_id="session_x",
                audit_dispatch_record_ref="publish_AUDIT-X",
                reviewed_return_record_ref="return_x",
                verdict_value="PASS",
                findings_summary="Good",
                evidence_refs=["task_cards/TASK-X/evidence.md"],
                recommended_next_action=None,
                owner_waiver_ref=None,
                repo_root=repo_root,
                dry_run=True,
                artifact_subject=None,  # 故意缺失
            )
            
            # 验证: BLOCK + AIPOS-F70 错误
            blocking_reasons = result.get("blocking_reasons", [])
            assert any("AIPOS-F70" in r and "artifact_subject" in r for r in blocking_reasons), (
                f"task_mode=code 缺 artifact_subject 应 BLOCK,实际 blocking_reasons: {blocking_reasons}"
            )

    def test_code_task_with_artifact_subject_passes_validation(self):
        """task_mode=code提供artifact_subject→通过校验
        
        AIPOS-F70-fix1: 将 skip 改为单元测试。
        """
        from tools.aipos_cli.board_adapter import _build_audit_verdict_preview
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            # 创建最小结构(复用上一个测试的逻辑)
            (repo_root / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "returns" / "TASK-Y").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "publishes").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "sessions" / "AUDIT-Y").mkdir(parents=True)
            (repo_root / "0_control_plane" / "agent_profiles").mkdir(parents=True)
            
            task_y = repo_root / "5_tasks" / "queue" / "claimed" / "TASK-Y.md"
            task_y.write_text(
                "---\ntask_id: TASK-Y\nstatus: claimed\ntask_mode: code\n---\n# Y\n",
                encoding="utf-8"
            )
            
            audit_y = repo_root / "5_tasks" / "queue" / "claimed" / "AUDIT-Y.md"
            audit_y.write_text(
                "---\ntask_id: AUDIT-Y\nstatus: claimed\nreviewed_task_id: TASK-Y\n"
                "created_by: gate_derivation\nreviewed_executor_instance: exec.test\n---\n# Audit\n",
                encoding="utf-8"
            )
            
            (repo_root / "5_tasks" / "records" / "returns" / "TASK-Y" / "return_y.md").write_text(
                "---\nrecord_type: return_record\ncanonical_agent_instance: exec.test\n---\n# R\n",
                encoding="utf-8"
            )
            (repo_root / "5_tasks" / "records" / "publishes" / "publish_AUDIT-Y.md").write_text(
                "---\nrecord_type: publish_record\npublish_id: publish_AUDIT-Y\n---\n# P\n",
                encoding="utf-8"
            )
            (repo_root / "5_tasks" / "records" / "sessions" / "AUDIT-Y" / "session_y.md").write_text(
                "---\nrecord_type: session_record\nsession_id: session_y\nevent_count: 0\n---\n# S\n",
                encoding="utf-8"
            )
            (repo_root / "0_control_plane" / "agent_profiles" / "profiles.yaml").write_text(
                "agents:\n  - id: exec.test\n    role: executor\n  - id: audit.test\n    role: auditor\n",
                encoding="utf-8"
            )
            
            # 调用 preview(提供 artifact_subject)
            result = _build_audit_verdict_preview(
                audit_task_id="AUDIT-Y",
                audit_task_path=None,
                reviewed_task_id="TASK-Y",
                actor="audit.test",
                agent_instance="audit.test",
                owner_policy_ref="pol_test",
                audit_claim_id=None,
                audit_session_id="session_y",
                audit_dispatch_record_ref="publish_AUDIT-Y",
                reviewed_return_record_ref="return_y",
                verdict_value="PASS",
                findings_summary="Good",
                evidence_refs=["task_cards/TASK-Y/evidence.md"],
                recommended_next_action=None,
                owner_waiver_ref=None,
                repo_root=repo_root,
                dry_run=True,
                artifact_subject={
                    "repository": "test-repo",
                    "commit_sha": "a" * 40,
                    "tree_hash": "b" * 40,
                },
            )
            
            # 验证: 没有 AIPOS-F70 artifact_subject 相关 blocking_reasons
            blocking_reasons = result.get("blocking_reasons", [])
            artifact_blocks = [r for r in blocking_reasons if "AIPOS-F70" in r and "artifact_subject" in r]
            assert not artifact_blocks, (
                f"task_mode=code 提供 artifact_subject 不应 BLOCK,实际 artifact_subject blocking: {artifact_blocks}"
            )

    def test_invalid_commit_sha_format_blocks(self):
        """commit_sha格式不合法→BLOCK
        
        AIPOS-F70-fix1: 将 skip 改为单元测试。
        """
        from tools.aipos_cli.board_adapter import _build_audit_verdict_preview
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            # 创建最小结构
            (repo_root / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "returns" / "TASK-Z").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "publishes").mkdir(parents=True)
            (repo_root / "5_tasks" / "records" / "sessions" / "AUDIT-Z").mkdir(parents=True)
            (repo_root / "0_control_plane" / "agent_profiles").mkdir(parents=True)
            
            task_z = repo_root / "5_tasks" / "queue" / "claimed" / "TASK-Z.md"
            task_z.write_text(
                "---\ntask_id: TASK-Z\nstatus: claimed\ntask_mode: code\n---\n# Z\n",
                encoding="utf-8"
            )
            
            audit_z = repo_root / "5_tasks" / "queue" / "claimed" / "AUDIT-Z.md"
            audit_z.write_text(
                "---\ntask_id: AUDIT-Z\nstatus: claimed\nreviewed_task_id: TASK-Z\n"
                "created_by: gate_derivation\nreviewed_executor_instance: exec.test\n---\n# Audit\n",
                encoding="utf-8"
            )
            
            (repo_root / "5_tasks" / "records" / "returns" / "TASK-Z" / "return_z.md").write_text(
                "---\nrecord_type: return_record\ncanonical_agent_instance: exec.test\n---\n# R\n",
                encoding="utf-8"
            )
            (repo_root / "5_tasks" / "records" / "publishes" / "publish_AUDIT-Z.md").write_text(
                "---\nrecord_type: publish_record\npublish_id: publish_AUDIT-Z\n---\n# P\n",
                encoding="utf-8"
            )
            (repo_root / "5_tasks" / "records" / "sessions" / "AUDIT-Z" / "session_z.md").write_text(
                "---\nrecord_type: session_record\nsession_id: session_z\nevent_count: 0\n---\n# S\n",
                encoding="utf-8"
            )
            (repo_root / "0_control_plane" / "agent_profiles" / "profiles.yaml").write_text(
                "agents:\n  - id: exec.test\n    role: executor\n  - id: audit.test\n    role: auditor\n",
                encoding="utf-8"
            )
            
            # 调用 preview(提供无效 commit_sha 格式)
            result = _build_audit_verdict_preview(
                audit_task_id="AUDIT-Z",
                audit_task_path=None,
                reviewed_task_id="TASK-Z",
                actor="audit.test",
                agent_instance="audit.test",
                owner_policy_ref="pol_test",
                audit_claim_id=None,
                audit_session_id="session_z",
                audit_dispatch_record_ref="publish_AUDIT-Z",
                reviewed_return_record_ref="return_z",
                verdict_value="PASS",
                findings_summary="Good",
                evidence_refs=["task_cards/TASK-Z/evidence.md"],
                recommended_next_action=None,
                owner_waiver_ref=None,
                repo_root=repo_root,
                dry_run=True,
                artifact_subject={
                    "repository": "test-repo",
                    "commit_sha": "invalid_sha",  # 无效格式
                    "tree_hash": "b" * 40,
                },
            )
            
            # 验证: BLOCK + commit_sha 格式错误
            blocking_reasons = result.get("blocking_reasons", [])
            assert any("commit_sha" in r and "格式错误" in r for r in blocking_reasons), (
                f"commit_sha 格式错误应 BLOCK,实际 blocking_reasons: {blocking_reasons}"
            )


class TestPreciseCommitMatching:
    """验收③: 精确匹配路径零回归 — X被裁决覆盖时finalize/deploy正常放行"""

    def test_exact_commit_match_passes(self):
        """裁决commit_sha与required_commit_sha精确匹配→found=True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov_root = Path(tmpdir)
            verdicts_dir = gov_root / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST"
            verdicts_dir.mkdir(parents=True)
            
            # 创建包含artifact_subject的裁决
            commit_sha = "a" * 40
            verdict_content = f"""---
record_type: audit_verdict_record
verdict_id: verdict_test_001
verdict: PASS
verdict_at: '2026-09-01T12:00:00Z'
artifact_subject:
  repository: lybra
  commit_sha: {commit_sha}
  tree_hash: {'b' * 40}
---
# Test Verdict
"""
            verdict_file = verdicts_dir / "verdict_test_001.md"
            verdict_file.write_text(verdict_content, encoding="utf-8")
            
            result = find_gate_pass_verdict_for_task(
                "AIPOS-TEST", gov_root, required_commit_sha=commit_sha
            )
            
            assert result["found"] is True
            assert result["verdict"] == "PASS"
            assert result["is_legacy_verdict"] is False

    def test_commit_mismatch_blocks(self):
        """裁决commit_sha与required_commit_sha不匹配→found=False并出声"""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov_root = Path(tmpdir)
            verdicts_dir = gov_root / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST"
            verdicts_dir.mkdir(parents=True)
            
            verdict_commit = "a" * 40
            required_commit = "b" * 40
            
            verdict_content = f"""---
record_type: audit_verdict_record
verdict_id: verdict_test_002
verdict: PASS
verdict_at: '2026-09-01T12:00:00Z'
artifact_subject:
  repository: lybra
  commit_sha: {verdict_commit}
  tree_hash: {'c' * 40}
---
# Test Verdict
"""
            verdict_file = verdicts_dir / "verdict_test_002.md"
            verdict_file.write_text(verdict_content, encoding="utf-8")
            
            result = find_gate_pass_verdict_for_task(
                "AIPOS-TEST", gov_root, required_commit_sha=required_commit
            )
            
            assert result["found"] is False
            assert "不匹配" in result["reason"] or "已变化" in result["reason"]
            assert "复审" in result["reason"]


class TestLegacyVerdictCompatibility:
    """验收④: 存量legacy裁决→警告放行且输出含legacy标注"""

    def test_legacy_verdict_without_artifact_subject_passes_with_warning(self):
        """无artifact_subject的存量裁决→found=True且is_legacy_verdict=True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov_root = Path(tmpdir)
            verdicts_dir = gov_root / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-LEGACY"
            verdicts_dir.mkdir(parents=True)
            
            # 创建不含artifact_subject的裁决(legacy)
            verdict_content = """---
record_type: audit_verdict_record
verdict_id: verdict_legacy_001
verdict: PASS
verdict_at: '2026-08-01T12:00:00Z'
---
# Legacy Verdict (无artifact_subject)
"""
            verdict_file = verdicts_dir / "verdict_legacy_001.md"
            verdict_file.write_text(verdict_content, encoding="utf-8")
            
            result = find_gate_pass_verdict_for_task(
                "AIPOS-LEGACY", gov_root, required_commit_sha="a" * 40
            )
            
            assert result["found"] is True
            assert result["is_legacy_verdict"] is True
            assert "legacy" in result["reason"].lower()

    def test_finalize_with_legacy_verdict_shows_warning(self):
        """finalize调用legacy裁决→can_finalize=True且is_legacy_verdict=True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov_root = Path(tmpdir)
            verdicts_dir = gov_root / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-LEGACY"
            verdicts_dir.mkdir(parents=True)
            
            verdict_content = """---
record_type: audit_verdict_record
verdict_id: verdict_legacy_002
verdict: PASS
verdict_at: '2026-08-01T12:00:00Z'
---
# Legacy Verdict
"""
            verdict_file = verdicts_dir / "verdict_legacy_002.md"
            verdict_file.write_text(verdict_content, encoding="utf-8")
            
            result = check_task_can_finalize(
                "AIPOS-LEGACY", gov_root, commit_sha="a" * 40
            )
            
            assert result["can_finalize"] is True
            assert result["is_legacy_verdict"] is True


class TestAdjacentCommitBugFix:
    """验收①: 先红后绿·复现"审过相邻提交当审过当前产物"漏洞→修复后精确核对"""

    def test_adjacent_commit_not_covered_by_verdict(self):
        """
        复现漏洞:commit X获PASS后追加commit Y,修复前放行,修复后拒绝
        
        场景:
        - commit X(aaaa...)获得PASS裁决
        - commit Y(bbbb...)是后续提交,未被审计
        - finalize/deploy尝试用X的裁决覆盖Y→应拒绝
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            gov_root = Path(tmpdir)
            verdicts_dir = gov_root / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST"
            verdicts_dir.mkdir(parents=True)
            
            commit_x = "a" * 40
            commit_y = "b" * 40
            
            # 只有commit X的裁决
            verdict_content = f"""---
record_type: audit_verdict_record
verdict_id: verdict_commit_x
verdict: PASS
verdict_at: '2026-09-01T12:00:00Z'
artifact_subject:
  repository: lybra
  commit_sha: {commit_x}
  tree_hash: {'c' * 40}
---
# Verdict for commit X
"""
            verdict_file = verdicts_dir / "verdict_commit_x.md"
            verdict_file.write_text(verdict_content, encoding="utf-8")
            
            # 尝试用commit X的裁决覆盖commit Y→应拒绝
            result = find_gate_pass_verdict_for_task(
                "AIPOS-TEST", gov_root, required_commit_sha=commit_y
            )
            
            assert result["found"] is False, "commit Y未被审计,不应放行"
            assert "不匹配" in result["reason"] or "已变化" in result["reason"]

    def test_exact_commit_covered_by_verdict_passes(self):
        """绿:commit X被裁决精确覆盖→正常放行"""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov_root = Path(tmpdir)
            verdicts_dir = gov_root / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST"
            verdicts_dir.mkdir(parents=True)
            
            commit_x = "a" * 40
            
            verdict_content = f"""---
record_type: audit_verdict_record
verdict_id: verdict_commit_x_exact
verdict: PASS
verdict_at: '2026-09-01T12:00:00Z'
artifact_subject:
  repository: lybra
  commit_sha: {commit_x}
  tree_hash: {'c' * 40}
---
# Verdict for commit X
"""
            verdict_file = verdicts_dir / "verdict_commit_x_exact.md"
            verdict_file.write_text(verdict_content, encoding="utf-8")
            
            # commit X被裁决精确覆盖→正常放行
            result = find_gate_pass_verdict_for_task(
                "AIPOS-TEST", gov_root, required_commit_sha=commit_x
            )
            
            assert result["found"] is True
            assert result["verdict"] == "PASS"


class TestArtifactSubjectWrittenToRecord:
    """验证artifact_subject正确写入verdict记录"""

    def test_artifact_subject_in_verdict_record_frontmatter(self):
        """build_mcp_audit_verdict_record_markdown写入artifact_subject到frontmatter"""
        artifact_subject = {
            "repository": "lybra",
            "commit_sha": "a" * 40,
            "tree_hash": "b" * 40,
            "package_digest": "sha256:c" * 32,
        }
        
        record = build_mcp_audit_verdict_record_markdown(
            verdict_id="verdict_test_write",
            verdict="PASS",
            reviewed_task_id="AIPOS-TEST",
            reviewed_task_path="/tmp/test.md",
            reviewed_return_record_ref="/tmp/return.md",
            audit_dispatch_record_ref="/tmp/dispatch.md",
            audit_task_id="AIPOS-TEST-AUDIT",
            audit_task_path="/tmp/audit.md",
            audit_claim_id="claim_123",
            audit_session_id="session_123",
            reviewed_executor_instance="executor.test",
            auditor_instance="auditor.test",
            actor="test_actor",
            canonical_agent_instance="test.instance",
            owner_policy_ref="pol_test",
            verdict_at="2026-09-01T12:00:00Z",
            findings_summary="测试",
            evidence_refs=["ref1"],
            recommended_next_action="finalize",
            artifact_subject=artifact_subject,
        )
        
        # 验证artifact_subject在frontmatter中
        assert "artifact_subject:" in record
        assert artifact_subject["commit_sha"] in record
        assert artifact_subject["tree_hash"] in record
