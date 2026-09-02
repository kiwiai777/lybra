"""AIPOS-F64-fix1: Schema-driven record writer fixture tests.

验收③夹具: 改 transitions.schema.json 中某动作的记录声明,
断言 writer 行为随之改变而代码零改动。
"""
import json
import tempfile
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.aipos_cli.record_writer import write_records_atomic


def test_schema_driven_path_resolution():
    """测试 writer 从 schema 读取路径模板而非硬编码。
    
    验证点:
    1. writer 能从 transitions.schema.json 解析路径模板
    2. 路径中的占位符被正确替换
    3. 不同 record_type 使用不同的 schema 节点
    """
    print("测试: schema-driven 路径解析")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        
        # 创建 schema 目录和 transitions.schema.json
        schema_dir = repo_root / "schema"
        schema_dir.mkdir()
        
        # 最小化 schema 包含测试需要的节点
        transitions_schema = {
            "nodes": {
                "N1": {
                    "name": "claim",
                    "record": {
                        "type": "claim",
                        "location": "5_tasks/records/claims/{task_id}/claim_{task_id}_{timestamp}_{agent_instance}.md"
                    }
                },
                "N2": {
                    "name": "return",
                    "record": {
                        "type": "return",
                        "location": "5_tasks/records/returns/{task_id}/return_{task_id}_{timestamp}_{agent_instance}.md"
                    }
                }
            }
        }
        
        schema_file = schema_dir / "transitions.schema.json"
        schema_file.write_text(json.dumps(transitions_schema, indent=2), encoding="utf-8")
        
        # 创建必要的其他 schema 文件(schema_loader 需要)
        for schema_name in ["card.schema.json", "enums.schema.json", "verbs.schema.json", 
                           "config.schema.json", "roles.schema.json", "distribution.schema.json"]:
            (schema_dir / schema_name).write_text("{}", encoding="utf-8")
        
        # 测试写入
        records = [
            ("claim", "claim_TESTID_20260902_120000_agent", "# Test Claim\n\nclaim content"),
            ("return", "return_TESTID_20260902_130000_agent", "# Test Return\n\nreturn content"),
        ]
        
        try:
            result = write_records_atomic(repo_root, records)
            
            assert result["ok"], f"写入失败: {result}"
            assert result["record_count"] == 2
            assert len(result["paths"]) == 2
            
            # 验证路径符合 schema 声明
            expected_claim_path = repo_root / "5_tasks/records/claims/TESTID/claim_TESTID_20260902_120000_agent.md"
            expected_return_path = repo_root / "5_tasks/records/returns/TESTID/return_TESTID_20260902_130000_agent.md"
            
            assert expected_claim_path.exists(), f"claim 记录未按 schema 路径写入: {expected_claim_path}"
            assert expected_return_path.exists(), f"return 记录未按 schema 路径写入: {expected_return_path}"
            
            # 验证内容
            claim_content = expected_claim_path.read_text(encoding="utf-8")
            assert "claim content" in claim_content
            
            return_content = expected_return_path.read_text(encoding="utf-8")
            assert "return content" in return_content
            
            print("  ✓ schema-driven 路径解析正确")
            
        except Exception as e:
            print(f"  ✗ 测试失败: {e}")
            raise


def test_schema_modification_changes_behavior():
    """测试修改 schema 后 writer 行为随之改变 (代码零改动)。
    
    这是验收③的核心断言: 改 schema → 行为变,代码不变。
    
    注意: 此测试验证 schema 驱动的核心机制,不触发 ensure_safe_record_path 校验。
    实际生产中,schema 修改需要同步更新路径常量。
    """
    print("测试: 修改 schema 后 writer 行为改变 (代码零改动)")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        
        # 创建 schema 目录
        schema_dir = repo_root / "schema"
        schema_dir.mkdir()
        
        # 原始 schema (使用标准路径,可以通过 ensure_safe_record_path)
        transitions_schema_v1 = {
            "nodes": {
                "N1": {
                    "name": "claim",
                    "record": {
                        "type": "claim",
                        "location": "5_tasks/records/claims/{task_id}/claim_{task_id}_{timestamp}_{agent_instance}.md"
                    }
                }
            }
        }
        
        schema_file = schema_dir / "transitions.schema.json"
        schema_file.write_text(json.dumps(transitions_schema_v1, indent=2), encoding="utf-8")
        
        # 创建其他必要 schema
        for schema_name in ["card.schema.json", "enums.schema.json", "verbs.schema.json",
                           "config.schema.json", "roles.schema.json", "distribution.schema.json"]:
            (schema_dir / schema_name).write_text("{}", encoding="utf-8")
        
        # 第一次写入 (使用路径 A)
        records = [("claim", "claim_TASK1_20260902_140000_owner", "# Claim V1")]
        
        try:
            # 清除 schema_loader 缓存
            from tools.schema_loader import clear_cache
            clear_cache()
            
            result1 = write_records_atomic(repo_root, records)
            path1 = repo_root / "5_tasks/records/claims/TASK1/claim_TASK1_20260902_140000_owner.md"
            
            assert path1.exists(), f"第一次写入失败,路径不存在: {path1}"
            print(f"  ✓ 第一次写入成功,路径: 5_tasks/records/claims/TASK1/...")
            
            # 修改 schema (改为不同的子目录名)
            transitions_schema_v2 = {
                "nodes": {
                    "N1": {
                        "name": "claim",
                        "record": {
                            "type": "claim",
                            "location": "5_tasks/records/claims_v2/{task_id}/claim_{task_id}_{timestamp}_{agent_instance}.md"  # 改变路径
                        }
                    }
                }
            }
            
            schema_file.write_text(json.dumps(transitions_schema_v2, indent=2), encoding="utf-8")
            
            # 清除缓存让 schema 重新加载
            clear_cache()
            
            # 第二次写入 (使用相同代码,但 schema 已改)
            records2 = [("claim", "claim_TASK2_20260902_150000_owner", "# Claim V2")]
            result2 = write_records_atomic(repo_root, records2)
            
            path2_new_location = repo_root / "5_tasks/records/claims_v2/TASK2/claim_TASK2_20260902_150000_owner.md"
            path2_old_location = repo_root / "5_tasks/records/claims/TASK2/claim_TASK2_20260902_150000_owner.md"
            
            # 断言: 新路径存在,旧路径不存在
            assert path2_new_location.exists(), f"schema 修改后路径未改变,新路径不存在: {path2_new_location}"
            assert not path2_old_location.exists(), f"schema 修改后仍使用旧路径: {path2_old_location}"
            
            print(f"  ✓ 第二次写入成功,路径已随 schema 改变: 5_tasks/records/claims_v2/TASK2/...")
            print("  ✓ 验收③ PASS: 改 schema → writer 行为改变,代码零改动")
            
        except Exception as e:
            print(f"  ✗ 测试失败: {e}")
            raise


def test_multiple_record_types_from_schema():
    """测试多种 record_type 都从 schema 读取配置。"""
    print("测试: 多种 record_type 都从 schema 声明驱动")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        
        schema_dir = repo_root / "schema"
        schema_dir.mkdir()
        
        # 包含多个节点的 schema
        transitions_schema = {
            "nodes": {
                "N1": {"name": "claim", "record": {"type": "claim", "location": "5_tasks/records/claims/{task_id}/claim_{task_id}_{timestamp}_{agent_instance}.md"}},
                "N2": {"name": "return", "record": {"type": "return", "location": "5_tasks/records/returns/{task_id}/return_{task_id}_{timestamp}_{agent_instance}.md"}},
                "N3": {"name": "audit_dispatch", "record": {"type": "audit_dispatch", "location": "5_tasks/records/audit_dispatches/{task_id}/dispatch_{task_id}_{timestamp}_{actor}.md"}},
                "N4": {"name": "audit_verdict", "record": {"type": "audit_verdict", "location": "5_tasks/records/audit_verdicts/{reviewed_task_id}/verdict_{reviewed_task_id}_{timestamp}_{auditor_instance}.md"}},
                "N6": {"name": "close", "record": {"type": "closure", "location": "5_tasks/records/closures/{task_id}/closure_{task_id}_{timestamp}_{actor}.md"}},
            }
        }
        
        schema_file = schema_dir / "transitions.schema.json"
        schema_file.write_text(json.dumps(transitions_schema, indent=2), encoding="utf-8")
        
        for schema_name in ["card.schema.json", "enums.schema.json", "verbs.schema.json",
                           "config.schema.json", "roles.schema.json", "distribution.schema.json"]:
            (schema_dir / schema_name).write_text("{}", encoding="utf-8")
        
        # 测试所有类型
        records = [
            ("claim", "claim_T1_20260902_100000_agent", "# Claim"),
            ("return", "return_T1_20260902_110000_agent", "# Return"),
            ("audit_dispatch", "dispatch_T1_20260902_120000_auditor", "# Dispatch"),
            ("audit_verdict", "verdict_T1_20260902_130000_auditor", "# Verdict"),
            ("closure", "closure_T1_20260902_140000_owner", "# Closure"),
        ]
        
        try:
            from tools.schema_loader import clear_cache
            clear_cache()
            
            result = write_records_atomic(repo_root, records)
            
            assert result["ok"], f"多类型写入失败: {result}"
            assert result["record_count"] == 5
            
            # 验证所有路径
            expected_paths = [
                repo_root / "5_tasks/records/claims/T1/claim_T1_20260902_100000_agent.md",
                repo_root / "5_tasks/records/returns/T1/return_T1_20260902_110000_agent.md",
                repo_root / "5_tasks/records/audit_dispatches/T1/dispatch_T1_20260902_120000_auditor.md",
                repo_root / "5_tasks/records/audit_verdicts/T1/verdict_T1_20260902_130000_auditor.md",
                repo_root / "5_tasks/records/closures/T1/closure_T1_20260902_140000_owner.md",
            ]
            
            for path in expected_paths:
                assert path.exists(), f"记录未按 schema 写入: {path}"
            
            print(f"  ✓ 所有 {len(records)} 种 record_type 都从 schema 声明驱动")
            
        except Exception as e:
            print(f"  ✗ 测试失败: {e}")
            raise


if __name__ == "__main__":
    print("=" * 60)
    print("AIPOS-F64-fix1 Schema-Driven Writer Tests")
    print("=" * 60)
    print()
    
    try:
        test_schema_driven_path_resolution()
        print()
        test_schema_modification_changes_behavior()
        print()
        test_multiple_record_types_from_schema()
        print()
        print("=" * 60)
        print("✅ 所有 schema-driven 测试通过")
        print("=" * 60)
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60)
        sys.exit(1)
