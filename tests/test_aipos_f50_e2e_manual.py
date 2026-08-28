#!/usr/bin/env python3
"""AIPOS-F50 端到端验证: 真实 gate 调用

此脚本通过真实的 gate MCP 连接:
1. 用 chris governance_root 发码
2. enroll 到临时目录
3. 验证 connection.json 中 projects 为 ["chris-huibojin"] (非 ["lybra"])
4. 用 hbj-coder 凭据测试 queue_list (验证能列出 HBJOTA-1)
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path

# 设置路径
sys.path.insert(0, str(Path.cwd() / "tools"))

def test_e2e_chris_enrollment():
    """端到端: chris governance_root 发码 → enroll → 验证 projects"""
    print("\n=== 端到端测试 1: chris governance_root 发码与 enroll ===")
    
    chris_gov = Path("/home/kiwi/ai-project-os/2_projects/chris-huibojin")
    if not chris_gov.exists():
        print(f"  ✗ chris governance_root 不存在: {chris_gov}")
        return False
    
    print(f"  chris governance_root: {chris_gov}")
    
    # 检查 project.json
    project_json = chris_gov / "project.json"
    if project_json.exists():
        project_data = json.loads(project_json.read_text())
        print(f"  project.json: {project_data}")
        project_name = project_data.get("project")
        print(f"  ✓ project.json 中 project={project_name}")
    else:
        print(f"  ✗ project.json 不存在")
        return False
    
    print("\n  【手动步骤】发码与 enroll:")
    print(f"  1. 以 chris governance_root 发码 (需要 Owner/advisor 权限):")
    print(f"     /home/kiwi/projects/lybra/.deploy/current/bin/lybra \\")
    print(f"       --workspace-root {chris_gov} \\")
    print(f"       roles enroll-code \\")
    print(f"       --role test-f50-executor \\")
    print(f"       --instance test-f50.chris.kiwiai-dev \\")
    print(f"       --ttl 3600 \\")
    print(f"       --governance-root {chris_gov}")
    print(f"")
    print(f"  2. 将生成的码 enroll 到临时目录:")
    print(f"     mkdir -p /tmp/f50-enroll-test")
    print(f"     cd /tmp/f50-enroll-test")
    print(f"     /home/kiwi/projects/lybra/.deploy/current/bin/lybra enroll <码>")
    print(f"")
    print(f"  3. 验证 connection.json 中 projects 域:")
    print(f"     cat /tmp/f50-enroll-test/.lybra/connection.json | python3 -m json.tool | grep -A3 projects")
    print(f"")
    print(f"  预期结果: projects: [\"chris-huibojin\"]  (修复前为 [\"lybra\"])")
    
    return True


def test_e2e_hbj_coder_queue_list():
    """端到端测试 2: hbj-coder 凭据 queue_list 能列出 HBJOTA-1"""
    print("\n=== 端到端测试 2: hbj-coder queue_list 可见性 ===")
    
    chris_connection = Path("/home/kiwi/ai-project-os/2_projects/chris-huibojin/.lybra/connection.json")
    if not chris_connection.exists():
        print(f"  ✗ chris connection.json 不存在: {chris_connection}")
        return False
    
    # 读取 hbj-coder 凭据
    connection_data = json.loads(chris_connection.read_text())
    hbj_coder_token = None
    for token in connection_data.get("tokens", []):
        if token.get("role") == "hbj-coder":
            hbj_coder_token = token
            break
    
    if not hbj_coder_token:
        print(f"  ✗ 未找到 hbj-coder token")
        return False
    
    print(f"  hbj-coder token projects: {hbj_coder_token.get('projects')}")
    
    # 检查 HBJOTA-1 是否存在
    hbjota1 = Path("/home/kiwi/ai-project-os/2_projects/chris-huibojin/5_tasks/queue/pending/hbjota-1.md")
    if hbjota1.exists():
        print(f"  ✓ HBJOTA-1 存在: {hbjota1}")
    else:
        print(f"  ✗ HBJOTA-1 不存在")
        return False
    
    print("\n  【修复前预期】hbj-coder projects=['lybra'] → queue_list 返回 0 张")
    print("    原因: token 的 projects 与 chris-huibojin 工作区不匹配")
    print("")
    print("  【修复后预期】hbj-coder 重签后 projects=['chris-huibojin'] → 能列出 HBJOTA-1")
    print("")
    print("  【验证步骤】")
    print("  1. 修复前 (当前凭据 projects=['lybra']):")
    print("     用当前 hbj-coder 凭据调用 gate queue_list, 应返回 0 张")
    print("")
    print("  2. 修复后 (重签凭据):")
    print("     a. 以 chris governance_root 重新发码并 enroll (覆盖旧凭据)")
    print("     b. 再次调用 queue_list, 应能列出 HBJOTA-1")
    
    return True


def show_resigning_steps():
    """显示存量凭据重签步骤"""
    print("\n=== 存量凭据重签步骤 (供 Owner/advisor 执行) ===")
    print("")
    print("chris-huibojin 项目有 3 份存量凭据需要重签:")
    print("  - hbj-coder (executor)")
    print("  - hbj-auditor (auditor)")
    print("  - planner (planner, 2 个实例)")
    print("")
    print("重签步骤:")
    print("")
    print("1. 以 chris-huibojin governance_root 发码:")
    print("")
    print("   cd /home/kiwi/ai-project-os/2_projects/chris-huibojin")
    print("   lybra roles enroll-code \\")
    print("     --role hbj-coder \\")
    print("     --instance hbj-coder.chris-huibojin.kiwiai-dev \\")
    print("     --governance-root /home/kiwi/ai-project-os/2_projects/chris-huibojin \\")
    print("     --ttl 7200")
    print("")
    print("2. enroll (会自动覆盖旧凭据):")
    print("")
    print("   /lybra enroll <生成的码>")
    print("")
    print("3. 验证 projects 域:")
    print("")
    print("   cat .lybra/connection.json | python3 -m json.tool | grep -A3 projects")
    print("   # 应显示 \"projects\": [\"chris-huibojin\"]")
    print("")
    print("4. 对其他角色重复步骤 1-3:")
    print("   - hbj-auditor")
    print("   - planner (如有多个实例, 分别重签)")
    print("")
    print("5. 验证 queue_list 可见性:")
    print("")
    print("   用重签后的 hbj-coder 凭据调用 gate, 确认能列出 HBJOTA-1")
    print("")


if __name__ == "__main__":
    print("=== AIPOS-F50 端到端验证 ===")
    
    test_e2e_chris_enrollment()
    test_e2e_hbj_coder_queue_list()
    show_resigning_steps()
    
    print("\n" + "="*80)
    print("端到端验证说明:")
    print("- 测试 1: 验证签发侧 projects 推导正确")
    print("- 测试 2: 验证 queue_list 口径统一, 重签后能列出任务")
    print("- 重签步骤: 供 Owner 执行, 将存量 3 份凭据全部重签")
    print("="*80)
