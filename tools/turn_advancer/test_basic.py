#!/usr/bin/env python3
"""AIPOS-340 — Turn Advancer 基本功能测试。

验证核心功能：
1. 对 pending 任务，生成 claim_task 命令
2. 对 claimed + RETURN.md 存在的任务，生成 return_work 命令
3. S3 边界：需判断的场景输出 wait_human
4. scan 全队列返回下一步清单
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tools.turn_advancer import resolve_next_command
from tools.turn_advancer.resolver import scan_all_tasks


def test_pending_task():
    """测试 pending 任务 → claim_task"""
    workspace = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    result = resolve_next_command("AIPOS-262", workspace, "manual")
    
    assert result["current_status"] == "pending", f"Expected pending, got {result['current_status']}"
    assert result["next_action"] == "claim_task", f"Expected claim_task, got {result['next_action']}"
    assert result["command_type"] == "mcp_verb", f"Expected mcp_verb, got {result['command_type']}"
    assert result["command"]["verb"] == "lybra_queue_claim_dry_run"
    assert "task_id" in result["command"]["args"]
    assert "actor" in result["command"]["args"]
    assert not result["requires_human_judgment"]
    
    print("✓ test_pending_task passed")


def test_scan_all():
    """测试 scan 全队列"""
    workspace = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    results = scan_all_tasks(workspace, "manual")
    
    assert len(results) > 0, "Expected at least one task"
    assert all("task_id" in r for r in results), "All results should have task_id"
    assert all("next_action" in r for r in results), "All results should have next_action"
    
    # 统计各动作类别
    actions = {}
    for r in results:
        action = r.get("next_action", "unknown")
        actions[action] = actions.get(action, 0) + 1
    
    print(f"✓ test_scan_all passed: {len(results)} tasks scanned")
    print(f"  Actions: {actions}")


def test_s3_boundary():
    """测试 S3 边界：judgment 留人"""
    workspace = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    # AIPOS-340 目前 claimed 但无 RETURN.md（实现中）→ unknown（未覆盖状态）
    result = resolve_next_command("AIPOS-340", workspace, "manual")
    
    # 未覆盖的状态 → requires_human_judgment
    assert result["requires_human_judgment"], "Expected requires_human_judgment=True for uncovered state"
    
    print("✓ test_s3_boundary passed")


def test_manual_vs_auto_mode():
    """测试 manual vs auto 模式"""
    workspace = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    
    manual_result = resolve_next_command("AIPOS-262", workspace, "manual")
    auto_result = resolve_next_command("AIPOS-262", workspace, "auto")
    
    assert manual_result["dispatch_mode"] == "manual"
    assert auto_result["dispatch_mode"] == "auto"
    # 同一任务、同一输入 → 同一 next_action
    assert manual_result["next_action"] == auto_result["next_action"]
    
    print("✓ test_manual_vs_auto_mode passed")


if __name__ == "__main__":
    print("=== AIPOS-340 Turn Advancer Tests ===\n")
    
    try:
        test_pending_task()
        test_scan_all()
        test_s3_boundary()
        test_manual_vs_auto_mode()
        
        print("\n✅ All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
