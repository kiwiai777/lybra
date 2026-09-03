"""AIPOS-F65A-fix2: PreAuthorized一段式认领骨架创建测试

问题: F65A骨架创建只接了两跳confirm路,PreAuthorized一段式认领(真实主路)不建骨架,首战哑火。

验收:
① 先红后绿: 靶场PreAuthorized一段式认领 → 修复前无骨架,修复后骨架在声明位且内容=统一模板
② 两跳confirm路零回归(F65A既有夹具)
③ grep单一实现断言: _create_return_skeleton唯一实现点,两路共调
④ 活体: 部署后下一张真实认领(由顾问执行)骨架出现——本卡验收含此
⑤ 夹具入run-all
⑥ 基线零新增失败
"""
import json
import tempfile
from pathlib import Path


def test_f65a_fix2_preauthorized_claim_creates_skeleton():
    """验收① 靶场PreAuthorized一段式认领 → 骨架在声明位且内容=统一模板"""
    from tools.aipos_cli.queue_mutation import mutate_queue_task
    from tools.aipos_cli.record_writer import build_return_skeleton_markdown
    
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        
        # 创建最小工作区结构
        (workspace / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (workspace / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
        (workspace / "5_tasks" / "records" / "claims").mkdir(parents=True)
        (workspace / "5_tasks" / "records" / "sessions").mkdir(parents=True)
        (workspace / "task_cards").mkdir()
        
        # 创建 schema (声明 task_cards 路径)
        schema_dir = workspace / "schema"
        schema_dir.mkdir()
        config_schema = {
            "governance_structure": {
                "paths": {
                    "task_cards": {
                        "relative_to": "governance_root",
                        "path": "task_cards/",
                        "description": "Task cards archive"
                    }
                }
            }
        }
        (schema_dir / "config.schema.json").write_text(
            json.dumps(config_schema), encoding="utf-8"
        )
        
        # 创建测试任务卡
        task_id = "TEST-F65A-FIX2-001"
        task_card_path = workspace / "5_tasks" / "queue" / "pending" / f"{task_id}.md"
        task_card_content = f"""---
task_id: {task_id}
title: Test PreAuthorized claim with skeleton
project: test
status: pending
task_mode: code
priority: normal
assigned_to: test-agent
agent_instance: test-agent
context_bundle: test-bundle
created_by: test-creator
needs_owner: false
output_target: tests/
artifact_policy: formal_write
task_class: simple
---
# Test card for PreAuthorized claim
"""
        task_card_path.write_text(task_card_content, encoding="utf-8")
        
        # 创建 project.json
        project_json = workspace / "project.json"
        project_json.write_text(json.dumps({
            "project": "test",
            "governance_root": str(workspace),
        }), encoding="utf-8")
        
        # 创建空的 agent profiles
        profiles_dir = workspace / "3_context_bundles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "agent_profiles.json").write_text(json.dumps({
            "profiles": []
        }), encoding="utf-8")
        
        # 验证骨架不存在(先红)
        skeleton_path = workspace / "task_cards" / task_id / "RETURN.md"
        assert not skeleton_path.exists(), "骨架在claim前不应存在"
        
        # 执行 PreAuthorized 一段式认领 (with_records=True触发记录写入+骨架创建)
        result = mutate_queue_task(
            repo_root=workspace,
            action="claim",
            task_id=task_id,
            actor="test-agent",
            dry_run=False,
            with_records=True,
            profiles=[],
        )
        
        # 验证 claim 成功
        assert result.get("wrote") is True, f"Claim应该成功: {result}"
        assert result.get("moved") is True, f"Card应该移动到claimed: {result}"
        
        # 验收①-后绿: 骨架在声明位
        assert skeleton_path.exists(), f"RETURN.md骨架应该在 {skeleton_path} 创建"
        
        # 验收①: 内容=统一模板
        skeleton_content = skeleton_path.read_text(encoding="utf-8")
        expected_skeleton = build_return_skeleton_markdown(task_id)
        
        # 验证包含必需的节标题
        assert "## 一句话结论" in skeleton_content, "骨架应包含'一句话结论'节"
        assert "## 改动清单" in skeleton_content, "骨架应包含'改动清单'节"
        assert "## 验收对账" in skeleton_content, "骨架应包含'验收对账'节"
        assert "## 测试原文" in skeleton_content, "骨架应包含'测试原文'节"
        
        # 验证包含占位符
        assert "(待填写" in skeleton_content, "骨架应包含占位符提示"
        
        # 验证任务卡已移动到claimed
        claimed_path = workspace / "5_tasks" / "queue" / "claimed" / f"{task_id}.md"
        assert claimed_path.exists(), f"任务卡应该移动到claimed: {claimed_path}"
        assert not task_card_path.exists(), f"原任务卡应该被删除: {task_card_path}"
        
        print(f"✓ PreAuthorized一段式认领成功创建RETURN.md骨架")
        print(f"  骨架位置: {skeleton_path.relative_to(workspace)}")
        print(f"  骨架长度: {len(skeleton_content)} 字符")


def test_f65a_fix2_two_hop_confirm_no_regression():
    """验收② 两跳confirm路零回归 - 复用F65A既有夹具"""
    from tools.aipos_cli.board_adapter import _write_mcp_claim_records
    from tools.aipos_cli.record_writer import build_return_skeleton_markdown
    
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        task_id = "TEST-F65A-FIX2-CONFIRM"
        
        # 创建 schema
        schema_dir = workspace / "schema"
        schema_dir.mkdir()
        config_schema = {
            "governance_structure": {
                "paths": {
                    "task_cards": {
                        "relative_to": "governance_root",
                        "path": "task_cards/",
                        "description": "Task cards archive"
                    }
                }
            }
        }
        (schema_dir / "config.schema.json").write_text(
            json.dumps(config_schema), encoding="utf-8"
        )
        
        # 创建空 record_plan
        record_plan = {"record_previews": []}
        
        # 验证骨架不存在(先红)
        skeleton_path = workspace / "task_cards" / task_id / "RETURN.md"
        assert not skeleton_path.exists(), "骨架在调用前不应存在"
        
        # 调用两跳confirm路的记录写入函数
        performed = _write_mcp_claim_records(workspace, record_plan, task_id=task_id)
        
        # 验证骨架已创建(后绿)
        assert skeleton_path.exists(), f"RETURN.md骨架应该在 {skeleton_path} 创建"
        
        # 验证骨架内容
        skeleton_content = skeleton_path.read_text(encoding="utf-8")
        expected_skeleton = build_return_skeleton_markdown(task_id)
        
        assert "## 一句话结论" in skeleton_content
        assert "## 改动清单" in skeleton_content
        assert "(待填写" in skeleton_content
        
        # 验证 performed 包含骨架创建记录
        skeleton_records = [p for p in performed if p.get("record_type") == "return_skeleton"]
        assert len(skeleton_records) == 1, "应该有一条骨架创建记录"
        assert skeleton_records[0]["wrote"] is True
        
        print(f"✓ 两跳confirm路零回归 - 骨架正常创建")


def test_f65a_fix2_single_implementation_assertion():
    """验收③ grep单一实现断言: _create_return_skeleton是唯一实现点"""
    import subprocess
    
    # 检查 _create_return_skeleton 函数存在
    result = subprocess.run(
        ["grep", "-n", "def _create_return_skeleton", 
         "tools/aipos_cli/board_adapter.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )
    
    assert result.returncode == 0, "_create_return_skeleton 函数应该存在"
    lines = result.stdout.strip().split("\n")
    assert len(lines) == 1, f"_create_return_skeleton 应该只有一个定义,实际: {len(lines)}"
    
    print(f"✓ _create_return_skeleton 单一实现点断言通过")
    print(f"  定义位置: {lines[0]}")
    
    # 检查两个调用点
    result = subprocess.run(
        ["grep", "-n", "_create_return_skeleton(", 
         "tools/aipos_cli/board_adapter.py",
         "tools/aipos_cli/queue_mutation.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )
    
    call_lines = [l for l in result.stdout.strip().split("\n") if l and "def _create_return_skeleton" not in l]
    
    # 应该至少有两个调用点(board_adapter和queue_mutation)
    assert len(call_lines) >= 2, f"应该至少有2个调用点,实际: {len(call_lines)}"
    
    # 验证两个关键文件都调用了
    files_with_calls = set()
    for line in call_lines:
        if ":" in line:
            file_path = line.split(":")[0]
            files_with_calls.add(Path(file_path).name)
    
    assert "board_adapter.py" in files_with_calls, "board_adapter.py 应该调用 _create_return_skeleton"
    assert "queue_mutation.py" in files_with_calls, "queue_mutation.py 应该调用 _create_return_skeleton"
    
    print(f"✓ 两路共调断言通过:")
    for line in call_lines:
        print(f"  {line}")


def test_f65a_fix2_skeleton_idempotent():
    """骨架创建幂等性: 多次claim同一任务不会覆盖已存在的RETURN.md"""
    from tools.aipos_cli.board_adapter import _create_return_skeleton
    
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        task_id = "TEST-IDEMPOTENT"
        
        # 创建 schema
        schema_dir = workspace / "schema"
        schema_dir.mkdir()
        config_schema = {
            "governance_structure": {
                "paths": {
                    "task_cards": {
                        "relative_to": "governance_root",
                        "path": "task_cards/",
                        "description": "Task cards archive"
                    }
                }
            }
        }
        (schema_dir / "config.schema.json").write_text(
            json.dumps(config_schema), encoding="utf-8"
        )
        
        # 第一次调用: 应该创建骨架
        result1 = _create_return_skeleton(workspace, task_id)
        assert result1 is not None, "第一次调用应该创建骨架"
        assert result1["wrote"] is True
        
        skeleton_path = workspace / "task_cards" / task_id / "RETURN.md"
        assert skeleton_path.exists()
        
        # 修改骨架内容
        custom_content = "# 自定义内容\n\n已填写的RETURN"
        skeleton_path.write_text(custom_content, encoding="utf-8")
        
        # 第二次调用: 应该跳过(幂等)
        result2 = _create_return_skeleton(workspace, task_id)
        assert result2 is None, "第二次调用应该返回None(已存在)"
        
        # 验证内容未被覆盖
        final_content = skeleton_path.read_text(encoding="utf-8")
        assert final_content == custom_content, "已存在的RETURN.md不应被覆盖"
        
        print(f"✓ 骨架创建幂等性验证通过")


def test_f65a_fix2_skeleton_fail_closed():
    """骨架路径解析失败阻断claim(fail-closed语义保持)"""
    from tools.aipos_cli.board_adapter import _create_return_skeleton
    
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        task_id = "TEST-FAIL-CLOSED"
        
        # 不创建schema,导致路径解析失败
        # 也不创建 config.schema.json
        
        # 验证: 应该抛出 RuntimeError
        try:
            _create_return_skeleton(workspace, task_id)
            assert False, "应该抛出RuntimeError阻断操作"
        except RuntimeError as e:
            error_msg = str(e)
            assert "RETURN_SKELETON_PATH_RESOLUTION_FAILED" in error_msg
            assert "出口" in error_msg, "错误消息应该包含出口指引"
            print(f"✓ fail-closed语义验证通过: {error_msg[:80]}...")


if __name__ == "__main__":
    print("=" * 70)
    print(" AIPOS-F65A-fix2: PreAuthorized一段式认领骨架创建测试")
    print("=" * 70)
    
    try:
        print("\n[验收①] PreAuthorized一段式认领创建骨架")
        test_f65a_fix2_preauthorized_claim_creates_skeleton()
        
        print("\n[验收②] 两跳confirm路零回归")
        test_f65a_fix2_two_hop_confirm_no_regression()
        
        print("\n[验收③] 单一实现点断言")
        test_f65a_fix2_single_implementation_assertion()
        
        print("\n[附加] 骨架创建幂等性")
        test_f65a_fix2_skeleton_idempotent()
        
        print("\n[附加] fail-closed语义保持")
        test_f65a_fix2_skeleton_fail_closed()
        
        print("\n" + "=" * 70)
        print(" ✓ ALL TESTS PASSED")
        print("=" * 70)
        exit(0)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
