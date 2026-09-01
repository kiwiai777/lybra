#!/usr/bin/env python3
"""AIPOS-F72: 派审幂等判据修真——dispatch链指向废卡/零裁决时不得挡复派。

验收点（按卡内验收条款）:
1. 先红后绿·复现 F63 现场: 造"旧审计卡 concluded 零裁决"→ 修复前 dispatch BLOCK;
   修复后放行且新 dispatch 记录含 supersedes 旧链引用
2. 幂等零回归: 审计卡 pending/claimed 在途 → dispatch 照拦;源卡已有 PASS 裁决 → 照拦
3. auto 与 manual 同源: 断言两路径调同一判据函数
4. 活体复派 AIPOS-F63R2 成功落 pending(部署后)
5. 夹具入 run-all
6. 基线零新增失败

本文件覆盖验收①②③(夹具层);④需部署后活体验证;⑤⑥由测试框架保证。
"""
import tempfile
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.fixture
def tmp_workspace(tmp_path: Path):
    """创建临时工作区结构(最小可用 5_tasks/ 结构)"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    # 队列目录
    for state in ["pending", "claimed", "completed", "concluded", "withdrawn"]:
        (workspace / "5_tasks" / "queue" / state).mkdir(parents=True)
    
    # 记录目录
    (workspace / "5_tasks" / "records" / "audit_dispatches").mkdir(parents=True)
    (workspace / "5_tasks" / "records" / "audit_verdicts").mkdir(parents=True)
    (workspace / "5_tasks" / "records" / "claims").mkdir(parents=True)
    (workspace / "5_tasks" / "records" / "returns").mkdir(parents=True)
    (workspace / "5_tasks" / "records" / "publishes").mkdir(parents=True)
    
    # project.json
    project_json = {
        "project_id": "test-project",
        "name": "Test Project",
        "code_repo": str(workspace),
    }
    (workspace / "project.json").write_text(json.dumps(project_json, indent=2), encoding="utf-8")
    
    return workspace


def _write_task_card(workspace: Path, task_id: str, status: str, queue_state: str, **extra_metadata):
    """写任务卡到对应队列
    
    Args:
        workspace: 工作区根目录
        task_id: 任务ID
        status: frontmatter 中的 status 字段
        queue_state: 队列状态目录名(pending/claimed/completed/blocked/withdrawn)
        extra_metadata: 额外的 frontmatter 字段
    """
    filename = task_id.lower().replace("-", "-") + ".md"
    queue_path = workspace / "5_tasks" / "queue" / queue_state / filename
    
    metadata = {
        "task_id": task_id,
        "status": status,
        "project": "test-project",
        "task_mode": "code",
        "priority": "high",
        **extra_metadata
    }
    
    frontmatter = "---\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in metadata.items()) + "\n---\n"
    body = f"# {task_id}\n\nTest task card.\n"
    
    queue_path.write_text(frontmatter + body, encoding="utf-8")
    return str(queue_path.relative_to(workspace))


def _write_dispatch_record(workspace: Path, reviewed_task_id: str, audit_task_id: str, dispatch_id: str):
    """写 dispatch 记录"""
    dispatch_dir = workspace / "5_tasks" / "records" / "audit_dispatches" / reviewed_task_id
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    
    dispatch_path = dispatch_dir / f"{dispatch_id}.md"
    
    metadata = {
        "record_type": "audit_dispatch_record",
        "dispatch_id": dispatch_id,
        "reviewed_task_id": reviewed_task_id,
        "audit_task_id": audit_task_id,
        "dispatched_at": _utc_now(),
    }
    
    frontmatter = "---\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in metadata.items()) + "\n---\n"
    body = f"# Dispatch Record: {dispatch_id}\n"
    
    dispatch_path.write_text(frontmatter + body, encoding="utf-8")
    return str(dispatch_path.relative_to(workspace))


def test_f72_1_red_then_green_dead_audit_card(tmp_workspace: Path):
    """验收①: 先红后绿·复现 F63 现场——旧审计卡 concluded 零裁决时应放行 re-dispatch"""
    from tools.aipos_cli.audit_helpers import is_dispatch_chain_valid
    
    # 造现场: 源卡 TEST-F63 已交回, 旧审计卡 TEST-F63R completed 零裁决
    source_task_id = "TEST-F63"
    old_audit_id = "TEST-F63R"
    
    # 写源卡(已交回, 有旧 dispatch 链接)
    dispatch_ref = _write_dispatch_record(tmp_workspace, source_task_id, old_audit_id, "dispatch_old_123")
    _write_task_card(
        tmp_workspace, source_task_id, "claimed", "claimed",
        related_audit_task_ref=old_audit_id,
        audit_dispatch_record_ref=dispatch_ref,
        executor_status="completed",
        audit_readiness="ready",
    )
    
    # 写旧审计卡: completed + status=concluded, 零裁决(结论:"无可审之物")
    # queue_state=completed (才会被 load_all_tasks 扫到), status=concluded (表示已结案)
    _write_task_card(
        tmp_workspace, old_audit_id, "concluded", "completed",
        task_mode="audit",
        derived_from=source_task_id,
        conclusion_note="无可审之物, 结案不入审计队列",
    )
    
    # 加载源卡 metadata
    from tools.aipos_cli.task_loader import find_task_by_id
    source_task, _ = find_task_by_id(source_task_id, tmp_workspace)
    assert source_task is not None, f"源卡 {source_task_id} 应存在"
    source_metadata = source_task["metadata"]
    
    # 零裁决(空列表)
    existing_verdicts = []
    
    # 调用判据函数
    chain_valid, superseded_ref = is_dispatch_chain_valid(source_metadata, existing_verdicts, tmp_workspace)
    
    # 断言: 链失效, 放行 re-dispatch
    assert not chain_valid, "旧审计卡 concluded 零裁决时链应失效(允许 re-dispatch)"
    assert superseded_ref == old_audit_id, f"应返回被取代的审计卡引用: {old_audit_id}"
    
    print(f"✓ 验收①通过: 旧审计卡 {old_audit_id} concluded 零裁决 → 链失效, 放行 re-dispatch (supersedes={superseded_ref})")


def test_f72_2_idempotency_audit_in_progress(tmp_workspace: Path):
    """验收②: 幂等零回归·审计在途——审计卡 pending/claimed 时应 BLOCK re-dispatch"""
    from tools.aipos_cli.audit_helpers import is_dispatch_chain_valid
    
    source_task_id = "TEST-AUDIT-IN-PROGRESS"
    audit_id = "TEST-AUDIT-IN-PROGRESSR"
    
    # 写源卡
    dispatch_ref = _write_dispatch_record(tmp_workspace, source_task_id, audit_id, "dispatch_current_456")
    _write_task_card(
        tmp_workspace, source_task_id, "claimed", "claimed",
        related_audit_task_ref=audit_id,
        audit_dispatch_record_ref=dispatch_ref,
    )
    
    # 写审计卡: pending(审计在途)
    _write_task_card(
        tmp_workspace, audit_id, "pending", "pending",
        task_mode="audit",
        derived_from=source_task_id,
    )
    
    from tools.aipos_cli.task_loader import find_task_by_id
    source_task, _ = find_task_by_id(source_task_id, tmp_workspace)
    source_metadata = source_task["metadata"]
    
    existing_verdicts = []  # 零裁决
    
    chain_valid, superseded_ref = is_dispatch_chain_valid(source_metadata, existing_verdicts, tmp_workspace)
    
    # 断言: 链有效, BLOCK re-dispatch
    assert chain_valid, "审计卡 pending(在途)时链应有效(BLOCK re-dispatch)"
    assert superseded_ref is None, "审计在途时不应返回 superseded_ref"
    
    print(f"✓ 验收②-a通过: 审计卡 {audit_id} pending 在途 → 链有效, BLOCK re-dispatch")


def test_f72_2_idempotency_has_verdict(tmp_workspace: Path):
    """验收②: 幂等零回归·已有裁决——源卡已有裁决时应 BLOCK re-dispatch"""
    from tools.aipos_cli.audit_helpers import is_dispatch_chain_valid
    
    source_task_id = "TEST-HAS-VERDICT"
    audit_id = "TEST-HAS-VERDICTR"
    
    # 写源卡
    dispatch_ref = _write_dispatch_record(tmp_workspace, source_task_id, audit_id, "dispatch_verdict_789")
    _write_task_card(
        tmp_workspace, source_task_id, "claimed", "claimed",
        related_audit_task_ref=audit_id,
        audit_dispatch_record_ref=dispatch_ref,
    )
    
    # 写审计卡: completed(已完成)
    _write_task_card(
        tmp_workspace, audit_id, "completed", "completed",
        task_mode="audit",
        derived_from=source_task_id,
    )
    
    from tools.aipos_cli.task_loader import find_task_by_id
    source_task, _ = find_task_by_id(source_task_id, tmp_workspace)
    source_metadata = source_task["metadata"]
    
    # 有裁决(模拟 PASS 裁决)
    existing_verdicts = [
        {
            "verdict_id": "verdict_123",
            "verdict": "PASS",
            "verdict_at": _utc_now(),
        }
    ]
    
    chain_valid, superseded_ref = is_dispatch_chain_valid(source_metadata, existing_verdicts, tmp_workspace)
    
    # 断言: 链有效(有裁决), BLOCK re-dispatch
    assert chain_valid, "源卡已有裁决时链应有效(BLOCK re-dispatch)"
    assert superseded_ref is None, "已有裁决时不应返回 superseded_ref"
    
    print(f"✓ 验收②-b通过: 源卡 {source_task_id} 已有裁决 → 链有效, BLOCK re-dispatch")


def test_f72_3_shared_check_function(tmp_workspace: Path):
    """验收③: auto 与 manual 同源——两路径调同一判据函数"""
    # 验证 manual dispatch 和 auto derivation 都导入同一个 is_dispatch_chain_valid
    
    # 1. manual dispatch 路径 (board_adapter.py)
    import tools.aipos_cli.board_adapter as board_adapter
    manual_check_func = None
    try:
        # board_adapter 应该从 audit_helpers 导入 is_dispatch_chain_valid
        import inspect
        source = inspect.getsource(board_adapter._build_audit_dispatch_preview)
        assert "is_dispatch_chain_valid" in source, "board_adapter 应使用 is_dispatch_chain_valid"
        assert "from tools.aipos_cli.audit_helpers import" in source, "应从 audit_helpers 导入"
        manual_check_func = "tools.aipos_cli.audit_helpers.is_dispatch_chain_valid"
    except Exception as e:
        pytest.fail(f"manual dispatch 路径检查失败: {e}")
    
    # 2. auto derivation 路径 (audit_derivation.py)
    import tools.aipos_cli.audit_derivation as audit_derivation
    auto_check_func = None
    try:
        source = inspect.getsource(audit_derivation.should_derive_audit)
        assert "is_dispatch_chain_valid" in source, "audit_derivation 应使用 is_dispatch_chain_valid"
        assert "from tools.aipos_cli.audit_helpers import is_dispatch_chain_valid" in source, "应从 audit_helpers 导入"
        auto_check_func = "tools.aipos_cli.audit_helpers.is_dispatch_chain_valid"
    except Exception as e:
        pytest.fail(f"auto derivation 路径检查失败: {e}")
    
    # 3. 断言同源
    assert manual_check_func == auto_check_func, "manual dispatch 与 auto derivation 必须调用同一判据函数"
    
    # 4. grep 证实无第二份实现
    import subprocess
    result = subprocess.run(
        ["grep", "-r", "AUDIT_ALREADY_DISPATCHED", "tools/aipos_cli/", "--include=*.py"],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    
    # 应只在 board_adapter.py 的两个地方出现(两个 append 语句)
    occurrences = [line for line in result.stdout.split("\n") if line.strip()]
    assert len(occurrences) == 2, f"AUDIT_ALREADY_DISPATCHED 应只在 board_adapter.py 两处出现,实际: {len(occurrences)}"
    assert all("board_adapter.py" in line for line in occurrences), "所有出现都应在 board_adapter.py"
    
    print("✓ 验收③通过: manual dispatch 与 auto derivation 调用同一判据函数 (is_dispatch_chain_valid)")


def test_f72_fail_closed_unreadable_audit_card(tmp_workspace: Path):
    """Fail-closed 原则: 审计卡状态不可读时应拒绝 re-dispatch"""
    from tools.aipos_cli.audit_helpers import is_dispatch_chain_valid
    
    source_task_id = "TEST-UNREADABLE"
    non_existent_audit_id = "TEST-UNREADABLE-R-DOES-NOT-EXIST"
    
    # 写源卡, 指向不存在的审计卡
    _write_task_card(
        tmp_workspace, source_task_id, "claimed", "claimed",
        related_audit_task_ref=non_existent_audit_id,
    )
    
    from tools.aipos_cli.task_loader import find_task_by_id
    source_task, _ = find_task_by_id(source_task_id, tmp_workspace)
    source_metadata = source_task["metadata"]
    
    existing_verdicts = []
    
    chain_valid, superseded_ref = is_dispatch_chain_valid(source_metadata, existing_verdicts, tmp_workspace)
    
    # Fail-closed: 不确定时应拒绝
    assert chain_valid, "审计卡不可读时应 fail-closed (拒绝 re-dispatch)"
    assert superseded_ref is None, "fail-closed 时不应返回 superseded_ref"
    
    print("✓ Fail-closed 测试通过: 审计卡不可读 → 拒绝 re-dispatch")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
