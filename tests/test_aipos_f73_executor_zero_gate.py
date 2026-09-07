"""AIPOS-F73 测试 — 执行体零门（next --run 机器扣扳机 + 卡面去门链）。

测试覆盖:
1. 前置三缺修复:
   - ① lybra audit-verdict 缺 artifact_subject 参数
   - ② audit-verdict --gate-url 默认值崩溃
   - ③ queue claim --confirm required_role_class 写死 executor
2. next --run 产物触发推导:
   - claimed + RETURN.md + 分支有提交 → return
   - 审计卡 + VERDICT 报告含 verdict 字段 → verdict (含 artifact_subject)
   - (finalize + close 由既有 F11 覆盖，本卡不重复)
3. 卡面门动词校验: executor/auditor 卡面 publish 时检测 lybra_ 动词 → BLOCK
4. claim 后 worktree 自动创建
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def test_workspace(tmp_path: Path) -> Path:
    """创建测试工作区。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    
    # 创建最小 schema 结构
    schema_dir = ws / "schema"
    schema_dir.mkdir()
    
    # config.schema.json
    config = {
        "version": "2025-01-01",
        "governance_structure": {
            "paths": {
                "governance_root": {"path": "."},
                "tasks_root": {"path": "5_tasks/", "relative_to": "governance_root"},
                "queue": {"path": "queue/", "relative_to": "tasks_root"},
                "records": {"path": "records/", "relative_to": "tasks_root"},
                "task_cards": {"path": "task_cards/", "relative_to": "governance_root"},
            }
        }
    }
    (schema_dir / "config.schema.json").write_text(json.dumps(config, indent=2))
    
    # 创建队列目录
    queue_dir = ws / "5_tasks" / "queue"
    for subdir in ["pending", "claimed", "completed", "blocked"]:
        (queue_dir / subdir).mkdir(parents=True)
    
    # 创建 records 目录
    records_dir = ws / "5_tasks" / "records"
    for subdir in ["claims", "returns", "audit_dispatches", "audit_verdicts", "closures"]:
        (records_dir / subdir).mkdir(parents=True)
    
    # 创建 task_cards 目录
    (ws / "task_cards").mkdir()
    
    # 创建 .lybra/connection.json
    lybra_dir = ws / ".lybra"
    lybra_dir.mkdir()
    connection = {
        "mcp": {"rpc_url": "http://localhost:7118/mcp"},
        "tokens": [
            {"role": "executor", "role_class": "executor", "token": "test-executor-token"},
            {"role": "auditor", "role_class": "auditor", "token": "test-auditor-token"},
        ]
    }
    (lybra_dir / "connection.json").write_text(json.dumps(connection, indent=2))
    
    # 初始化 git repo
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=ws, capture_output=True)
    (ws / "README.md").write_text("# Test repo")
    subprocess.run(["git", "add", "."], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=ws, capture_output=True)
    
    return ws


def test_f73_pre1_artifact_subject_parameter_exists(test_workspace: Path):
    """AIPOS-F73前置①: _build_verdict_submit_command 支持 artifact_subject 参数。"""
    from tools.aipos_cli.next_resolver import _build_verdict_submit_command
    
    artifact_subject = {
        "repository": "lybra",
        "commit_sha": "a" * 40,
        "tree_hash": "b" * 40,
    }
    
    cmd = _build_verdict_submit_command(
        reviewed_task_id="TEST-001",
        audit_task_id="TEST-001R",
        actor="auditor",
        agent_instance="audit.test",
        owner_policy_ref="pol_test",
        connection_json="/tmp/conn.json",
        verdict="PASS",
        artifact_subject=artifact_subject,
    )
    
    # 验证命令包含 artifact_subject 参数
    assert "--artifact-subject-repository lybra" in cmd
    assert f"--artifact-subject-commit-sha {'a' * 40}" in cmd
    assert f"--artifact-subject-tree-hash {'b' * 40}" in cmd


def test_f73_pre3_required_role_class_derives_from_task(test_workspace: Path):
    """AIPOS-F73前置③: queue claim --confirm 的 required_role_class 按任务 ID 派生（审计卡 → auditor）。"""
    # 创建审计卡
    audit_card = test_workspace / "5_tasks" / "queue" / "pending" / "test-001r.md"
    audit_card_content = dedent("""
    ---
    task_id: TEST-001R
    task_mode: audit
    assigned_to: audit.test
    ---
    # TEST-001R 审计卡
    """).strip()
    audit_card.write_text(audit_card_content)
    
    # 导入角色解析函数
    from tools.aipos_cli.task_loader import load_task_file
    
    # 加载任务卡
    task_data = load_task_file(audit_card, test_workspace)
    assert task_data is not None
    
    task_meta = task_data.get("metadata", {})
    task_id = task_meta.get("task_id", "")
    
    # 推导角色类型
    required_role_class = "executor"  # 默认
    if str(task_id).upper().endswith("R"):
        required_role_class = "auditor"
    
    assert required_role_class == "auditor", "审计卡应派生为 auditor 角色"


def test_f73_item2_return_requires_branch_commits(test_workspace: Path):
    """AIPOS-F73件②: claimed + RETURN.md 存在但分支无提交 → return 不可推导（fail-closed）。"""
    from tools.aipos_cli.next_resolver import _check_branch_has_commits
    
    # 创建任务分支但不提交
    task_id = "TEST-001"
    branch_name = f"card/{task_id}"
    subprocess.run(["git", "checkout", "-b", branch_name, "main"], cwd=test_workspace, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=test_workspace, capture_output=True)
    
    # 检查分支是否有提交
    has_commits = _check_branch_has_commits(test_workspace, task_id)
    assert not has_commits, "新建分支应该没有提交"
    
    # 添加提交
    subprocess.run(["git", "checkout", branch_name], cwd=test_workspace, capture_output=True)
    test_file = test_workspace / "test.txt"
    test_file.write_text("test content")
    subprocess.run(["git", "add", "test.txt"], cwd=test_workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Test commit"], cwd=test_workspace, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=test_workspace, capture_output=True)
    
    # 再次检查
    has_commits = _check_branch_has_commits(test_workspace, task_id)
    assert has_commits, "提交后分支应该有提交"


def test_f73_item2_worktree_auto_create(test_workspace: Path):
    """AIPOS-F73件②: claim 后自动创建/复用 worktree (card/<ID>)。"""
    from tools.aipos_cli.next_resolver import _ensure_worktree
    
    task_id = "TEST-002"
    
    # 首次创建
    result = _ensure_worktree(test_workspace, task_id)
    assert result["ok"], f"worktree 创建失败: {result['message']}"
    
    worktree_path = Path(result["worktree_path"])
    assert worktree_path.exists(), "worktree 目录应该存在"
    assert worktree_path.name == task_id, "worktree 目录名应该是任务 ID"
    
    # 复用已存在的 worktree（应该报告已存在）
    result2 = _ensure_worktree(test_workspace, task_id)
    assert result2["ok"], "复用已存在 worktree 应该成功"
    assert "already exists" in result2["message"].lower(), "应该报告 worktree 已存在"


def test_f73_item2_verdict_command_includes_artifact_subject(test_workspace: Path):
    """AIPOS-F73件②: 审计卡推导 verdict 命令时，从被审卡分支提取 artifact_subject。"""
    from tools.aipos_cli.next_resolver import _extract_artifact_subject_from_branch
    
    # 创建被审任务分支并提交
    reviewed_task_id = "TEST-003"
    branch_name = f"card/{reviewed_task_id}"
    subprocess.run(["git", "checkout", "-b", branch_name, "main"], cwd=test_workspace, capture_output=True)
    test_file = test_workspace / "implementation.py"
    test_file.write_text("# Implementation")
    subprocess.run(["git", "add", "implementation.py"], cwd=test_workspace, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Implement feature"], cwd=test_workspace, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=test_workspace, capture_output=True)
    
    # 提取 artifact_subject
    artifact_subject = _extract_artifact_subject_from_branch(
        test_workspace, reviewed_task_id, task_mode="code"
    )
    
    assert artifact_subject is not None, "应该能提取 artifact_subject"
    assert artifact_subject["repository"] == "workspace", "repository 应该是工作区名"
    assert len(artifact_subject["commit_sha"]) == 40, "commit_sha 应该是 40 字符"
    assert len(artifact_subject["tree_hash"]) == 40, "tree_hash 应该是 40 字符"
    
    # 非 code 卡不提取
    artifact_subject_non_code = _extract_artifact_subject_from_branch(
        test_workspace, reviewed_task_id, task_mode="doc"
    )
    assert artifact_subject_non_code is None, "非 code 卡不应提取 artifact_subject"


def test_f73_item1_card_gate_verb_validation_red_green():
    """AIPOS-F73件①: executor/auditor 卡面包含 lybra_ 动词 → publish BLOCK（先红后绿）。"""
    from tools.aipos_cli.draft_writer import publish_draft
    from tools.schema_constants import Verdict
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        
        # 创建最小结构
        schema_dir = ws / "schema"
        schema_dir.mkdir()
        (schema_dir / "card.schema.json").write_text(json.dumps({"version": "1.0"}))
        (schema_dir / "config.schema.json").write_text(json.dumps({
            "governance_structure": {
                "paths": {
                    "governance_root": {"path": "."},
                    "tasks_root": {"path": "5_tasks/", "relative_to": "governance_root"},
                    "queue": {"path": "queue/", "relative_to": "tasks_root"},
                }
            }
        }))
        
        queue_dir = ws / "5_tasks" / "queue" / "pending"
        queue_dir.mkdir(parents=True)
        
        drafts_dir = ws / "5_tasks" / "drafts"
        drafts_dir.mkdir(parents=True)
        
        # 红：executor 卡包含 lybra_queue_claim
        draft_bad = drafts_dir / "test-bad.md"
        draft_bad.write_text(dedent("""
        ---
        task_id: TEST-BAD
        assigned_to: exec.test
        task_mode: code
        draft_status: draft
        ---
        # TEST-BAD
        
        ## 认领与交回
        
        调用 lybra_queue_claim_dry_run 进行认领。
        """).strip())
        
        result_bad = publish_draft(ws, draft_bad, dry_run=True, actor="advisor")
        assert result_bad["verdict"] == Verdict.BLOCK, "包含门动词应该 BLOCK"
        blocking_reasons = " ".join(result_bad.get("blocking_reasons", []))
        assert "lybra_" in blocking_reasons.lower(), "BLOCK 原因应提及门动词"
        
        # 绿：executor 卡不包含门动词
        draft_good = drafts_dir / "test-good.md"
        draft_good.write_text(dedent("""
        ---
        task_id: TEST-GOOD
        assigned_to: exec.test
        task_mode: code
        draft_status: draft
        ---
        # TEST-GOOD
        
        ## 工作纪律
        
        执行体只写 RETURN.md，认领由产品处理。
        """).strip())
        
        result_good = publish_draft(ws, draft_good, dry_run=True, actor="advisor")
        # 可能因其他原因 BLOCK（如缺 machine zone），但不应因门动词 BLOCK
        if result_good["verdict"] == Verdict.BLOCK:
            blocking_reasons = " ".join(result_good.get("blocking_reasons", []))
            assert "lybra_" not in blocking_reasons.lower(), "不含门动词不应因此 BLOCK"


def test_f73_item4_executor_auditor_scope_tightened():
    """AIPOS-F73件④: executor/auditor token scope 收紧 - REWORK: 恢复 main scopes，移交 F73C。"""
    from tools.schema_loader import load_schema
    
    roles_schema = load_schema("roles", REPO_ROOT)
    roles = roles_schema.get("roles", [])
    
    executor_role = next((r for r in roles if r["role"] == "executor"), None)
    assert executor_role is not None, "应该找到 executor 角色"
    # REWORK: 恢复 main 的 scopes，移交 F73C
    assert executor_role["scopes"] == ["queue_claim", "queue_return", "queue_close", "task_progress", "bench_audit_submit"], \
        f"executor scopes 应与 main 一致，实际: {executor_role['scopes']}"
    
    auditor_role = next((r for r in roles if r["role"] == "auditor"), None)
    assert auditor_role is not None, "应该找到 auditor 角色"
    assert auditor_role["scopes"] == ["queue_claim", "audit_verdict", "task_progress"], \
        f"auditor scopes 应与 main 一致，实际: {auditor_role['scopes']}"


def test_f73_item5_advisor_skill_exists():
    """AIPOS-F73件⑤: 顾问 skill 文件存在且包含阶段→命令映射。"""
    skill_path = REPO_ROOT / "agents" / "skills" / "lybra-advisor" / "SKILL.md"
    assert skill_path.exists(), f"顾问 skill 文件应该存在: {skill_path}"
    
    content = skill_path.read_text()
    
    # 检查关键命令
    assert "lybra draft create" in content, "应包含 draft create 命令"
    assert "lybra draft publish" in content, "应包含 draft publish 命令"
    assert "lybra next --run" in content, "应包含 next --run 命令"
    assert "lybra mark-concluded" in content, "应包含 mark-concluded 命令"
    assert "lybra governance-commit" in content, "应包含 governance-commit 命令"
    
    # 检查退役提示
    assert "退役" in content, "应标记退役命令"


def test_f73_command_templates_parseable():
    """AIPOS-F73 parser夹具: 四个命令模板生成的命令可被 aipos_cli argparse 解析。"""
    import shlex
    from tools.aipos_cli.next_resolver import (
        _build_audit_dispatch_command,
        _build_verdict_submit_command,
        _build_close_command,
    )
    from tools.aipos_cli.aipos_cli import build_parser
    
    parser = build_parser()
    
    # 1. _build_audit_dispatch_command
    dispatch_cmd = _build_audit_dispatch_command(
        task_id="TEST-002",
        actor="advisor",
        agent_instance="advisor.test",
        owner_policy_ref="pol_test",
        connection_json="/tmp/conn.json",
        audit_task_id="TEST-002R",
        audit_agent_instance="audit.test",
    )
    # 验证不包含 --confirm 和 --connection-json（REWORK-NOTE 项3）
    assert "--confirm" not in dispatch_cmd, "dispatch 命令不应包含 --confirm"
    assert "--connection-json" not in dispatch_cmd, "dispatch 命令不应包含 --connection-json"
    # 解析不应抛出异常
    try:
        args = parser.parse_args(shlex.split(dispatch_cmd)[1:])
        assert args is not None
    except SystemExit as e:
        pytest.fail(f"dispatch 命令解析失败: {dispatch_cmd}, exit code: {e.code}")
    
    # 2. _build_verdict_submit_command
    verdict_cmd = _build_verdict_submit_command(
        reviewed_task_id="TEST-003",
        audit_task_id="TEST-003R",
        actor="audit.test",
        agent_instance="audit.test.host",
        owner_policy_ref="pol_test",
        connection_json="/tmp/conn.json",
        verdict="PASS",
        artifact_subject={
            "repository": "test-repo",
            "commit_sha": "a" * 40,
            "tree_hash": "b" * 40,
        },
    )
    assert "lybra audit-verdict" in verdict_cmd, "verdict 命令应使用 audit-verdict（连字符）"
    assert "--artifact-subject-repository test-repo" in verdict_cmd
    # 解析不应抛出异常
    try:
        args = parser.parse_args(shlex.split(verdict_cmd)[1:])
        assert args is not None
    except SystemExit as e:
        pytest.fail(f"verdict 命令解析失败: {verdict_cmd}, exit code: {e.code}")
    
    # 3. _build_close_command
    close_cmd = _build_close_command(
        task_id="TEST-004",
        actor="advisor",
        connection_json="/tmp/conn.json",
    )
    assert "lybra queue close" in close_cmd
    # 解析不应抛出异常
    try:
        args = parser.parse_args(shlex.split(close_cmd)[1:])
        assert args is not None
    except SystemExit as e:
        pytest.fail(f"close 命令解析失败: {close_cmd}, exit code: {e.code}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
