#!/usr/bin/env python3
"""AIPOS-F52: 测试 enroll_exchange 打点输出

直接调用 enroll_exchange 逻辑，捕获 stderr 打点日志
"""
import sys
import os
import tempfile
from pathlib import Path

# 添加 lybra 到路径
sys.path.insert(0, "/home/kiwi/projects/lybra/.deploy/current")
os.chdir("/home/kiwi/projects/lybra/.deploy/current")

from tools.aipos_cli.enrollment import issue_self_contained_code, decode_self_contained_code
from tools.aipos_cli.workspace_config import write_project_json

print("=== AIPOS-F52 打点测试 ===\n")

# 创建临时 chris 工作区
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    
    # Chris 工作区
    chris_ws = tmp / "chris-huibojin"
    chris_ws.mkdir()
    write_project_json(chris_ws, "chris-huibojin", code_repo="/fake/chris")
    (chris_ws / ".lybra").mkdir()
    (chris_ws / ".lybra/enrollments.json").write_text("{}")
    (chris_ws / "5_tasks/queue/pending").mkdir(parents=True)
    
    print(f"Chris 工作区: {chris_ws}")
    print(f"  project.json: {(chris_ws / 'project.json').read_text()}\n")
    
    # Lybra 工作区（模拟门自身）
    lybra_ws = tmp / "lybra-ws"
    lybra_ws.mkdir()
    write_project_json(lybra_ws, "lybra", code_repo="/fake/lybra")
    (lybra_ws / ".lybra").mkdir()
    (lybra_ws / ".lybra/enrollments.json").write_text("{}")
    (lybra_ws / "5_tasks/queue/pending").mkdir(parents=True)
    
    print(f"Lybra 工作区 (门自身): {lybra_ws}\n")
    
    # 签发自包含码（指定 chris governance_root）
    print("=== 签发自包含码 ===")
    print(f"  workspace_root={lybra_ws}")
    print(f"  governance_root={chris_ws}\n")
    
    code_result = issue_self_contained_code(
        workspace_root=lybra_ws,
        role="test-f52-probe",
        instance="probe-f52.chris.dev",
        ttl_seconds=3600,
        gate_url="http://localhost:7118",
        governance_root=str(chris_ws),
        by="test-owner",
        reason="AIPOS-F52 打点测试",
    )
    
    sc_code = code_result["self_contained_code"]
    print(f"自包含码: {sc_code[:60]}...\n")
    
    # 解码验证
    print("=== 解码验证 ===")
    sc = decode_self_contained_code(sc_code)
    print(f"  governance_root: '{sc.get('governance_root')}'")
    print(f"  Expected: '{chris_ws}'")
    print(f"  Match: {sc.get('governance_root') == str(chris_ws)}\n")
    
    # 现在模拟 enroll_exchange 的推导逻辑
    print("=== 模拟 enroll_exchange 推导 ===")
    print(f"  sc is not None: {sc is not None}")
    print(f"  sc.get('governance_root'): '{sc.get('governance_root')}'")
    print(f"  bool(sc.get('governance_root')): {bool(sc.get('governance_root'))}")
    
    if sc is not None and sc.get("governance_root"):
        governance_root = sc["governance_root"]
        print(f"  → Branch: sc has governance_root")
    else:
        if sc is None:
            governance_root = str(lybra_ws)
            print(f"  → Branch: sc is None, fallback")
        else:
            governance_root = ""
            print(f"  → Branch: empty governance_root")
    
    print(f"  governance_root 最终值: '{governance_root}'\n")
    
    # 读取 project.json
    from tools.aipos_cli.workspace_config import read_project_json
    try:
        project_data = read_project_json(governance_root)
        print(f"  read_project_json 返回: {project_data}")
        project_name = str(project_data.get("project") or project_data.get("name") or "").strip()
        print(f"  project_name: '{project_name}'")
        
        if project_name:
            projects = [project_name]
            projects_enforced = True
        else:
            projects = []
            projects_enforced = False
        
        print(f"  → projects={projects}, enforced={projects_enforced}")
    except Exception as e:
        print(f"  Exception: {e}")
        projects = []
        projects_enforced = False
        print(f"  → projects=[], enforced=False")
    
    print("\n=== 结论 ===")
    expected = ["chris-huibojin"]
    if projects == expected:
        print(f"✓ 推导正确: {projects}")
    else:
        print(f"✗ 推导错误: got {projects}, expected {expected}")
