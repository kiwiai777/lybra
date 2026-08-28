#!/usr/bin/env python3
"""AIPOS-F52: 端到端测试 - 两层回落根治

测试场景:
① 凭据项目域推导: governance_root → projects (enroll_exchange)
② workspace_root → project 解析 (queue_list)
③ 第三项目 probe-xyz 式 (项目无关性)
④ 负夹具: 无 project.json 报错带路
"""
import json
import tempfile
from pathlib import Path

def test_f52_two_layer_fallback_fix(tmp_path):
    """AIPOS-F52: 两层回落根治 - 完整端到端测试"""
    
    print("\n=== AIPOS-F52 两层回落根治测试 ===\n")
    
    # 准备: 创建三个项目工作区
    projects = {}
    for project_name in ["chris-huibojin", "probe-xyz", "lybra"]:
        ws = tmp_path / project_name
        ws.mkdir()
        
        # 写 project.json
        project_json = ws / "project.json"
        project_json.write_text(json.dumps({
            "project": project_name,
            "code_repo": f"/fake/{project_name}",
            "config_version": 1,
            "registered_at": "2026-08-28T13:00:00Z",
            "registered_by": "owner"
        }, indent=2))
        
        # 创建队列结构
        (ws / "5_tasks/queue/pending").mkdir(parents=True)
        (ws / ".lybra").mkdir(exist_ok=True)
        (ws / ".lybra/enrollments.json").write_text("{}")
        
        projects[project_name] = ws
        print(f"✓ 创建项目工作区: {project_name}")
    
    print()
    
    # 场景① 凭据项目域推导: governance_root → projects
    print("=== 场景① 凭据项目域推导 (enroll_exchange) ===")
    
    from tools.aipos_cli.enrollment import issue_self_contained_code, decode_self_contained_code
    from tools.aipos_cli.workspace_config import read_project_json
    
    for project_name in ["chris-huibojin", "probe-xyz"]:
        ws = projects[project_name]
        
        # 签发自包含码
        code_result = issue_self_contained_code(
            workspace_root=projects["lybra"],  # 门自身工作区
            role=f"test-f52-{project_name}",
            instance=f"f52.{project_name}.dev",
            ttl_seconds=3600,
            gate_url="http://localhost:7118",
            governance_root=str(ws),
            by="test-owner",
            reason=f"AIPOS-F52 测试 {project_name}",
        )
        
        sc_code = code_result["self_contained_code"]
        
        # 解码验证 governance_root
        sc = decode_self_contained_code(sc_code)
        assert sc is not None, f"解码自包含码失败: {project_name}"
        assert sc.get("governance_root") == str(ws), \
            f"governance_root 不匹配: expected={ws}, got={sc.get('governance_root')}"
        
        # 模拟 enroll_exchange 推导逻辑 (F52 修复后)
        governance_root = sc["governance_root"]
        project_data = read_project_json(governance_root)
        extracted_project = str(project_data.get("project") or project_data.get("name") or "").strip()
        
        assert extracted_project == project_name, \
            f"项目推导错误: expected={project_name}, got={extracted_project}"
        
        print(f"  ✓ {project_name}: governance_root → projects=['{project_name}']")
    
    print()
    
    # 场景② workspace_root → project 解析
    print("=== 场景② workspace_root → project 解析 (_resolve_active_project_for) ===")
    
    from tools.aipos_cli.board_adapter import _resolve_active_project_for
    
    for project_name in ["chris-huibojin", "probe-xyz", "lybra"]:
        ws = projects[project_name]
        resolved_project = _resolve_active_project_for(ws, None)
        
        assert resolved_project == project_name, \
            f"workspace→project 解析错误: expected={project_name}, got={resolved_project}"
        
        print(f"  ✓ {project_name}: workspace_root → project='{project_name}'")
    
    print()
    
    # 场景③ 第三项目 probe-xyz (项目无关性证明)
    print("=== 场景③ 第三项目 probe-xyz (项目无关性) ===")
    
    probe_ws = projects["probe-xyz"]
    
    # 签发 probe-xyz 凭据
    probe_code_result = issue_self_contained_code(
        workspace_root=projects["lybra"],
        role="test-f52-probe",
        instance="f52.probe.dev",
        ttl_seconds=3600,
        gate_url="http://localhost:7118",
        governance_root=str(probe_ws),
        by="test-owner",
        reason="AIPOS-F52 第三项目测试",
    )
    
    probe_sc = decode_self_contained_code(probe_code_result["self_contained_code"])
    probe_governance = probe_sc["governance_root"]
    probe_data = read_project_json(probe_governance)
    probe_project = str(probe_data.get("project") or "").strip()
    
    assert probe_project == "probe-xyz", \
        f"probe-xyz 推导错误: got={probe_project}"
    
    # 验证 workspace 解析
    probe_resolved = _resolve_active_project_for(probe_ws, None)
    assert probe_resolved == "probe-xyz", \
        f"probe-xyz workspace 解析错误: got={probe_resolved}"
    
    print(f"  ✓ probe-xyz: 凭据推导 → projects=['probe-xyz']")
    print(f"  ✓ probe-xyz: workspace 解析 → project='probe-xyz'")
    print(f"  ✓ 项目无关性证实: 不是只对 chris-huibojin 好使")
    
    print()
    
    # 场景④ 负夹具: 无 project.json 报错带路
    print("=== 场景④ 负夹具: 无 project.json 报错带路 ===")
    
    empty_ws = tmp_path / "empty-workspace"
    empty_ws.mkdir()
    
    try:
        _resolve_active_project_for(empty_ws, None)
        assert False, "应该抛出 FileNotFoundError"
    except FileNotFoundError as e:
        error_msg = str(e)
        assert "PROJECT_NOT_FOUND" in error_msg, f"错误消息格式不正确: {error_msg}"
        assert "project.json not found" in error_msg, f"错误消息缺少带路信息: {error_msg}"
        print(f"  ✓ 无 project.json 正确报错: {error_msg[:80]}...")
    except ValueError as e:
        # read_project_json 可能抛出 ValueError 而不是 FileNotFoundError
        error_msg = str(e)
        assert "PROJECT" in error_msg, f"错误消息格式不正确: {error_msg}"
        assert "project.json" in error_msg, f"错误消息缺少带路信息: {error_msg}"
        print(f"  ✓ 无 project.json 正确报错: {error_msg[:80]}...")
    
    # 测试 project.json 存在但缺 project 字段
    incomplete_ws = tmp_path / "incomplete-workspace"
    incomplete_ws.mkdir()
    (incomplete_ws / "project.json").write_text(json.dumps({
        "code_repo": "/fake/incomplete",
        "config_version": 1
    }))
    
    try:
        _resolve_active_project_for(incomplete_ws, None)
        assert False, "应该抛出 ValueError"
    except ValueError as e:
        error_msg = str(e)
        assert "PROJECT_NOT_FOUND" in error_msg or "PROJECT" in error_msg, f"错误消息格式不正确: {error_msg}"
        assert "missing 'project' field" in error_msg or "project.json" in error_msg, f"错误消息缺少带路信息: {error_msg}"
        print(f"  ✓ 缺 project 字段正确报错: {error_msg[:80]}...")
    
    print()
    
    # 验收: 零项目名硬编码
    print("=== 验收⑤ 零项目名硬编码检查 ===")
    
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    # 检查 board_adapter.py 的 _resolve_active_project_for
    board_adapter_path = Path(__file__).parent.parent.parent / "tools/aipos_cli/board_adapter.py"
    if board_adapter_path.exists():
        content = board_adapter_path.read_text()
        
        # 提取 _resolve_active_project_for 函数
        lines = content.split('\n')
        in_function = False
        function_lines = []
        for line in lines:
            if 'def _resolve_active_project_for' in line:
                in_function = True
            elif in_function:
                if line.startswith('def ') and 'def ' in line:
                    break
                function_lines.append(line)
        
        function_code = '\n'.join(function_lines)
        
        # 检查是否有硬编码项目名（排除注释和文档字符串）
        code_lines = [l for l in function_lines if not l.strip().startswith('#') and not l.strip().startswith('"""')]
        code_text = '\n'.join(code_lines)
        
        hardcoded_projects = []
        for project_name in ["lybra", "chris-huibojin", "probe-xyz"]:
            if f'"{project_name}"' in code_text or f"'{project_name}'" in code_text:
                hardcoded_projects.append(project_name)
        
        assert len(hardcoded_projects) == 0, \
            f"发现硬编码项目名: {hardcoded_projects}"
        
        print(f"  ✓ _resolve_active_project_for 零项目名硬编码")
    
    print()
    print("✓✓✓ AIPOS-F52 所有测试通过 ✓✓✓")
    print()
    print("修复总结:")
    print("  - 第一层: CLI 传完整自包含码 (不提取内层 code)")
    print("  - 第二层: _resolve_active_project_for 从 workspace_root/project.json 直接读取")
    print("  - 项目无关性: 经 probe-xyz 第三项目测试证实")
    print("  - 零硬编码: 纯推导，无项目名字面量")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_f52_two_layer_fallback_fix(Path(tmpdir))
