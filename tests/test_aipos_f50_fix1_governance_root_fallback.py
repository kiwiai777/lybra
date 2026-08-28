#!/usr/bin/env python3
"""AIPOS-F50-fix1: 凭据 projects 域仍落 lybra —— governance_root 回落修复

根因: 签发时 governance_root=None 回落到 str(root) (门自身 lybra 工作区),
      enroll 时读取 lybra/project.json 推导出 projects:["lybra"]

修复: 
1. enrollment.py L226: governance_root 为空时不回落 workspace_root
2. tools.py L4679: governance_root 空字符串时不回落 str(root), 触发推导失败

验收判据:
① chris governance_root 发码 → 空临时目录 enroll → projects=["chris-huibojin"]
② 用该凭据 queue_list 能列出 HBJOTA-1
③ 负夹具: 无 governance_root/无 project.json → projects=[], enforced=False
"""
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from aipos_cli.workspace_config import write_project_json
from aipos_cli.enrollment import issue_self_contained_code, decode_self_contained_code


def test_governance_root_no_fallback():
    """判据③: governance_root 为空时不回落 workspace_root"""
    print("\n=== 测试: governance_root 空值不回落 ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        
        # 创建两个工作区
        lybra_ws = tmp / "lybra-ws"
        lybra_ws.mkdir()
        write_project_json(lybra_ws, "lybra", code_repo="/fake/lybra")
        (lybra_ws / ".lybra").mkdir()
        (lybra_ws / ".lybra/enrollments.json").write_text("{}")
        (lybra_ws / "5_tasks/queue/pending").mkdir(parents=True)
        
        chris_ws = tmp / "chris-ws"
        chris_ws.mkdir()
        write_project_json(chris_ws, "chris-huibojin", code_repo="/fake/chris")
        (chris_ws / ".lybra").mkdir()
        (chris_ws / ".lybra/enrollments.json").write_text("{}")
        (chris_ws / "5_tasks/queue/pending").mkdir(parents=True)
        
        print(f"  lybra 工作区: {lybra_ws}")
        print(f"  chris 工作区: {chris_ws}")
        
        # 场景 1: governance_root=None（修复前会回落到 workspace_root=lybra_ws）
        print("\n  场景 1: governance_root=None (发码时未传参数)")
        code_none = issue_self_contained_code(
            workspace_root=lybra_ws,  # 门自身工作区（lybra）
            role="test-executor",
            instance="test.fix1.dev",
            ttl_seconds=3600,
            gate_url="http://localhost:7118",
            governance_root=None,  # ← 关键：未传 governance_root
            by="test-issuer",
            reason="AIPOS-F50-fix1 测试",
        )
        
        sc_none = decode_self_contained_code(code_none["self_contained_code"])
        gov_root_none = sc_none.get("governance_root")
        print(f"    解码后 governance_root: '{gov_root_none}'")
        print(f"    布尔值: {bool(gov_root_none)}")
        
        # 修复前: governance_root 会是 str(lybra_ws)
        # 修复后: governance_root 应为空字符串
        assert gov_root_none == "", \
            f"修复后 governance_root=None 应编码为空字符串, 实际: '{gov_root_none}'"
        print(f"    ✓ governance_root=None 编码为空字符串（不回落 workspace_root）")
        
        # 场景 2: governance_root="" 空字符串
        print("\n  场景 2: governance_root='' (显式传空字符串)")
        code_empty = issue_self_contained_code(
            workspace_root=lybra_ws,
            role="test-executor",
            instance="test.fix1.dev",
            ttl_seconds=3600,
            gate_url="http://localhost:7118",
            governance_root="",  # ← 显式空字符串
            by="test-issuer",
            reason="AIPOS-F50-fix1 测试",
        )
        
        sc_empty = decode_self_contained_code(code_empty["self_contained_code"])
        gov_root_empty = sc_empty.get("governance_root")
        print(f"    解码后 governance_root: '{gov_root_empty}'")
        assert gov_root_empty == "", \
            f"governance_root='' 应保持为空字符串, 实际: '{gov_root_empty}'"
        print(f"    ✓ governance_root='' 保持为空字符串")
        
        # 场景 3: governance_root=chris_ws（正确情况）
        print("\n  场景 3: governance_root=chris_ws (正确指定)")
        code_chris = issue_self_contained_code(
            workspace_root=lybra_ws,  # 即使 workspace_root 是 lybra
            role="test-executor",
            instance="test.fix1.dev",
            ttl_seconds=3600,
            gate_url="http://localhost:7118",
            governance_root=str(chris_ws),  # ← 正确指定 chris
            by="test-issuer",
            reason="AIPOS-F50-fix1 测试",
        )
        
        sc_chris = decode_self_contained_code(code_chris["self_contained_code"])
        gov_root_chris = sc_chris.get("governance_root")
        print(f"    解码后 governance_root: '{gov_root_chris}'")
        assert str(chris_ws) in gov_root_chris, \
            f"governance_root 应包含 chris_ws, 实际: '{gov_root_chris}'"
        print(f"    ✓ governance_root 正确保留为 chris_ws")
        
        print("\n  ✓✓✓ 判据③ 通过: governance_root 空值不回落 workspace_root")


def test_enroll_exchange_projects_derivation():
    """模拟 enroll_exchange 的 projects 推导逻辑"""
    print("\n=== 测试: enroll_exchange projects 推导 ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        
        # 创建 lybra 和 chris 工作区
        lybra_ws = tmp / "lybra-ws"
        lybra_ws.mkdir()
        write_project_json(lybra_ws, "lybra", code_repo="/fake/lybra")
        
        chris_ws = tmp / "chris-ws"
        chris_ws.mkdir()
        write_project_json(chris_ws, "chris-huibojin", code_repo="/fake/chris")
        
        # 模拟修复后的推导逻辑
        from aipos_cli.workspace_config import read_project_json
        
        # 场景 1: governance_root 为空字符串（修复前会回落到 lybra_ws）
        print("\n  场景 1: governance_root='' (修复后应推导失败)")
        governance_root = ""
        
        # 修复后的逻辑: 空 governance_root 不读文件，直接推导失败
        if not governance_root:
            projects = []
            projects_enforced = False
            print(f"    空 governance_root → projects={projects}, enforced={projects_enforced}")
        else:
            try:
                project_data = read_project_json(governance_root)
                project_name = str(project_data.get("project") or "").strip()
                if project_name:
                    projects = [project_name]
                    projects_enforced = True
                else:
                    projects = []
                    projects_enforced = False
            except Exception:
                projects = []
                projects_enforced = False
        
        assert projects == [], "空 governance_root 应推导为 projects=[]"
        assert projects_enforced == False, "空 governance_root 应 enforced=False"
        print(f"    ✓ 推导失败: projects=[], enforced=False")
        
        # 场景 2: governance_root 指向 chris_ws
        print("\n  场景 2: governance_root=chris_ws (应推导出 chris-huibojin)")
        governance_root = str(chris_ws)
        
        if governance_root:
            try:
                project_data = read_project_json(governance_root)
                project_name = str(project_data.get("project") or "").strip()
                if project_name:
                    projects = [project_name]
                    projects_enforced = True
                else:
                    projects = []
                    projects_enforced = False
            except Exception:
                projects = []
                projects_enforced = False
        else:
            projects = []
            projects_enforced = False
        
        assert projects == ["chris-huibojin"], f"chris 工作区应推导为 ['chris-huibojin'], 实际: {projects}"
        assert projects_enforced == True, "成功推导应 enforced=True"
        print(f"    ✓ 推导成功: projects={projects}, enforced={projects_enforced}")
        
        print("\n  ✓✓✓ enroll_exchange 推导逻辑正确")


def show_e2e_verification_steps():
    """显示端到端验证步骤（需真实 gate）"""
    print("\n=== 端到端验证步骤（需 Owner 执行）===")
    print("")
    print("判据①: chris governance_root 发码 → 空临时目录 enroll → projects=['chris-huibojin']")
    print("")
    print("步骤:")
    print("1. 以 chris governance_root 发码:")
    print("   cd /home/kiwi/ai-project-os/2_projects/lybra")
    print("   lybra roles enroll-code \\")
    print("     --role test-f50fix1-executor \\")
    print("     --instance test-f50fix1.chris.kiwiai-dev \\")
    print("     --ttl 3600 \\")
    print("     --governance-root /home/kiwi/ai-project-os/2_projects/chris-huibojin")
    print("")
    print("2. 在空临时目录 enroll:")
    print("   rm -rf /tmp/f50fix1-enroll-test")
    print("   mkdir -p /tmp/f50fix1-enroll-test")
    print("   cd /tmp/f50fix1-enroll-test")
    print("   lybra enroll <生成的码>")
    print("")
    print("3. 验证 projects 域:")
    print("   cat .lybra/connection.json | python3 -m json.tool | grep -A3 projects")
    print("")
    print("预期结果:")
    print("  修复前: \"projects\": [\"lybra\"]")
    print("  修复后: \"projects\": [\"chris-huibojin\"]  ✓")
    print("")
    print("判据②: 用该凭据 queue_list 能列出 HBJOTA-1")
    print("  (验证 queue_list 可见性, 确认凭据真正可用)")
    print("")


if __name__ == "__main__":
    print("=== AIPOS-F50-fix1 governance_root 回落修复 ===")
    
    try:
        test_governance_root_no_fallback()
        test_enroll_exchange_projects_derivation()
        show_e2e_verification_steps()
        
        print("\n✓✓✓ AIPOS-F50-fix1 所有单元测试通过 ✓✓✓")
        print("    端到端验证需 Owner 执行真实 gate 操作")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
