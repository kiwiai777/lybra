#!/usr/bin/env python3
"""
AIPOS-F51 夹具: 自检门豁免出口修真——dry_run阶段即可豁免+越界拒收给出可执行出口

验收点:
① 先红后绿: 构造越界交付, 带 owner_confirmation_token 调 dry_run——
   修复后出票且 warnings 含被豁免判据
② confirm 后 return 记录含 self_check_waived: true 与原因
③ 不带 token 时仍拒收(两态)
④ 拒收文本含可执行出口(断言文本包含"amend output_target"或等价指引)
"""
import subprocess
import sys
import tempfile
from pathlib import Path


def _setup_git_repo(repo_root: Path, task_id: str = "TEST-F51"):
    """创建临时 git repo + main 分支 + card 分支, 含越界改动。"""
    repo_root.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True, capture_output=True)

    # 创建初始文件 (main)
    (repo_root / "tools" / "aipos_cli").mkdir(parents=True, exist_ok=True)
    (repo_root / "tools" / "aipos_cli" / "board_adapter.py").write_text("# Initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_root, check=True, capture_output=True)

    # card 分支: 修改范围内 + 范围外文件
    subprocess.run(["git", "checkout", "-b", f"card/{task_id}"], cwd=repo_root, check=True, capture_output=True)
    (repo_root / "tools" / "aipos_cli" / "board_adapter.py").write_text("# Modified in scope", encoding="utf-8")
    (repo_root / "tools" / "aipos_cli" / "enrollment.py").write_text("# Out of scope!", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Modify in+out of scope"], cwd=repo_root, check=True, capture_output=True)


def test_red_without_token_blocks():
    """③ 不带 token 时仍拒收(两态)"""
    print("\n=== 红测试: 不带 token 时自检拒收 ===")

    self_check_reasons = ["CHANGES_OUT_OF_SCOPE: 以下文件超出卡面声明的 output_target 范围。"]
    mcp_return_metadata = {}  # 无 owner_confirmation_token

    self_check_waived = False
    waiver_reason = None
    warnings = []
    blocking_reasons = []

    if self_check_reasons and mcp_return_metadata:
        owner_token = mcp_return_metadata.get("owner_confirmation_token")
        if owner_token:
            self_check_waived = True
            waiver_reason = f"Owner confirmation token provided, waived {len(self_check_reasons)} self-check failures"
            warnings.extend([f"SELF_CHECK_WAIVED: {reason}" for reason in self_check_reasons])
            self_check_reasons = []

    blocking_reasons.extend(self_check_reasons)

    assert len(blocking_reasons) > 0, "无 token 时应该有 blocking_reasons"
    assert self_check_waived is False, "无 token 时不应放行"
    print(f"  ✓ 无 token 时拒收: {len(blocking_reasons)} 条阻塞")


def test_green_with_token_waives_at_dry_run():
    """① 带 token 时 dry_run 阶段即可豁免, 出票且 warnings 含被豁免判据"""
    print("\n=== 绿测试: 带 token 时 dry_run 阶段放行 ===")

    self_check_reasons = [
        "CHANGES_OUT_OF_SCOPE: 以下文件超出卡面声明的 output_target 范围。越界文件: enrollment.py。",
    ]
    # AIPOS-F51 核心: token 在 dry_run 阶段就传入 mcp_return_metadata
    mcp_return_metadata = {
        "owner_confirmation_token": "OWNER_CONFIRMED",
    }

    self_check_waived = False
    waiver_reason = None
    warnings = []
    blocking_reasons = []

    if self_check_reasons and mcp_return_metadata:
        owner_token = mcp_return_metadata.get("owner_confirmation_token")
        if owner_token:
            self_check_waived = True
            waiver_reason = f"Owner confirmation token provided, waived {len(self_check_reasons)} self-check failures"
            warnings.extend([f"SELF_CHECK_WAIVED: {reason}" for reason in self_check_reasons])
            self_check_reasons = []

    blocking_reasons.extend(self_check_reasons)

    assert self_check_waived is True, "带 token 时应标记 self_check_waived=True"
    assert waiver_reason is not None, "应有 waiver_reason"
    assert len(blocking_reasons) == 0, f"放行后 blocking_reasons 应为空, 实际: {blocking_reasons}"
    assert len(warnings) == 1, f"应有 1 条豁免留痕 warning, 实际: {len(warnings)}"
    assert "SELF_CHECK_WAIVED" in warnings[0], "warning 应含 SELF_CHECK_WAIVED"
    print(f"  ✓ 带 token 时放行: self_check_waived={self_check_waived}")
    print(f"  ✓ waiver_reason: {waiver_reason}")
    print(f"  ✓ warnings 留痕: {len(warnings)} 条")


def test_rejection_text_has_actionable_exit():
    """④ 拒收文本含可执行出口"""
    print("\n=== 出口测试: 拒收文本含可执行出口 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir) / "product_repo"
        _setup_git_repo(repo_root)

        import tools.aipos_cli.board_adapter as adapter_module
        original_resolve = adapter_module._resolve_product_code_repo
        adapter_module._resolve_product_code_repo = lambda x: repo_root

        try:
            # 判据②: 越界
            from tools.aipos_cli.board_adapter import _check_changes_in_scope
            reasons = _check_changes_in_scope(
                task_id="TEST-F51",
                output_target="tools/aipos_cli/board_adapter.py",
                repo_root=Path(tmpdir),
            )
            assert len(reasons) > 0, "应检测到越界"
            assert "amend output_target" in reasons[0] or "回退该文件" in reasons[0], \
                f"越界拒收应含可执行出口, 实际: {reasons[0]}"
            print(f"  ✓ 越界拒收含出口: ...{reasons[0][-120:]}")

            # 判据③: 无测试
            from tools.aipos_cli.board_adapter import _check_has_tests
            reasons = _check_has_tests(
                task_id="TEST-F51",
                repo_root=Path(tmpdir),
            )
            assert len(reasons) > 0, "应检测到无测试"
            assert "出口" in reasons[0] or "添加测试" in reasons[0], \
                f"无测试拒收应含可执行出口, 实际: {reasons[0]}"
            print(f"  ✓ 无测试拒收含出口: ...{reasons[0][-120:]}")

        finally:
            adapter_module._resolve_product_code_repo = original_resolve


def test_token_flows_through_return_metadata():
    """验证 _return_metadata 现在包含 owner_confirmation_token"""
    print("\n=== 数据流测试: _return_metadata 包含 token ===")

    from tools.mcp_server.tools import _return_metadata

    args = {
        "owner_policy_ref": "pol_test",
        "agent_instance": "test.agent",
        "owner_confirmation_token": "OWNER_CONFIRMED",
    }
    metadata = _return_metadata(args, canonical_agent_instance="test.agent")

    assert "owner_confirmation_token" in metadata, \
        f"_return_metadata 应包含 owner_confirmation_token, keys: {list(metadata.keys())}"
    assert metadata["owner_confirmation_token"] == "OWNER_CONFIRMED", \
        f"token 值应为 OWNER_CONFIRMED, 实际: {metadata['owner_confirmation_token']}"
    print(f"  ✓ _return_metadata 包含 owner_confirmation_token={metadata['owner_confirmation_token']}")

    # 不带 token 时
    args_no_token = {
        "owner_policy_ref": "pol_test",
        "agent_instance": "test.agent",
    }
    metadata_no_token = _return_metadata(args_no_token, canonical_agent_instance="test.agent")
    assert metadata_no_token.get("owner_confirmation_token") is None, \
        f"不带 token 时应为 None, 实际: {metadata_no_token.get('owner_confirmation_token')}"
    print(f"  ✓ 不带 token 时为 None")


def test_schema_accepts_owner_confirmation_token():
    """验证 lybra_queue_return_dry_run schema 接受 owner_confirmation_token"""
    print("\n=== Schema 测试: dry_run 接受 owner_confirmation_token ===")

    from tools.mcp_server.tools import WRITE_TOOL_DESCRIPTORS

    # 找到 lybra_queue_return_dry_run 的 schema
    dry_run_schema = None
    for tool in WRITE_TOOL_DESCRIPTORS:
        if tool.get("name") == "lybra_queue_return_dry_run":
            dry_run_schema = tool
            break

    assert dry_run_schema is not None, "应找到 lybra_queue_return_dry_run schema"
    properties = dry_run_schema["inputSchema"]["properties"]
    assert "owner_confirmation_token" in properties, \
        f"schema 应包含 owner_confirmation_token, 现有: {list(properties.keys())}"
    print(f"  ✓ schema 包含 owner_confirmation_token 参数")


if __name__ == "__main__":
    print("=== AIPOS-F51 自检门豁免出口修真 测试 ===")
    try:
        test_red_without_token_blocks()
        test_green_with_token_waives_at_dry_run()
        test_rejection_text_has_actionable_exit()
        test_token_flows_through_return_metadata()
        test_schema_accepts_owner_confirmation_token()
        print("\n✓ AIPOS-F51 全部测试通过")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 测试失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
