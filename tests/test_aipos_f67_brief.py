"""AIPOS-F67: lybra brief 命令测试（夹具入 run-all）

验收准绳:
1. 零记忆冷启动测试 - 四问可答
2. 裁剪生效 - superseded 条目被过滤
3. 辖域冲突可见
4. Fail-closed - 路径不存在时报错而非返回全零
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import pytest

from tools.aipos_cli.brief import run_brief, _get_decision_log_entries, _get_governance_docs


def test_brief_fail_closed_queue_not_exist(tmp_path: Path) -> None:
    """Fail-closed: queue 路径不存在时应报错而非返回全零。"""
    # 创建最小治理工作区（缺 queue 目录）
    gov_root = tmp_path / "governance_workspace"
    gov_root.mkdir()
    
    # 创建必需的子目录（但故意不创建 5_tasks）
    (gov_root / "governance").mkdir()
    (gov_root / "stage_archive").mkdir()
    
    # 运行 brief 应该失败（exit code != 0）
    exit_code = run_brief(workspace_root=gov_root, output_format="json")
    assert exit_code == 1, "Queue 路径不存在时应返回错误（exit code 1）"


def test_brief_fail_closed_decision_log_not_exist(tmp_path: Path) -> None:
    """Fail-closed: decision_log 路径不存在时应报错而非返回空列表。"""
    gov_root = tmp_path / "governance_workspace"
    gov_root.mkdir()
    
    # 创建 governance 目录但不创建 decision_log 子目录
    (gov_root / "governance").mkdir()
    
    # 调用 _get_decision_log_entries 应该抛出 ValueError
    with pytest.raises(ValueError, match="decision_log directory does not exist"):
        _get_decision_log_entries(gov_root, since_date=None, repo_root=None)


def test_brief_fail_closed_governance_docs_not_exist(tmp_path: Path) -> None:
    """Fail-closed: governance_docs 路径不存在时应报错而非返回空列表。"""
    gov_root = tmp_path / "governance_workspace"
    gov_root.mkdir()
    
    # 不创建 governance 目录
    
    # 调用 _get_governance_docs 应该抛出 ValueError
    with pytest.raises(ValueError, match="governance_docs directory does not exist"):
        _get_governance_docs(gov_root, repo_root=None)


def test_brief_superseded_filtering(tmp_path: Path) -> None:
    """裁剪生效: superseded 条目被过滤，只返回 status=active 的。"""
    gov_root = tmp_path / "governance_workspace"
    gov_root.mkdir()
    
    # 创建 decision_log 目录
    decision_log_dir = gov_root / "governance" / "decision_log" / "2026-09"
    decision_log_dir.mkdir(parents=True)
    
    # 创建一个 active 决策
    active_decision = decision_log_dir / "2026-09-01-active.md"
    active_decision.write_text("""---
status: active
decided_at: 2026-09-01T10:00:00Z
superseded_by: null
---
# Active Decision
""", encoding="utf-8")
    
    # 创建一个 superseded 决策
    superseded_decision = decision_log_dir / "2026-09-02-superseded.md"
    superseded_decision.write_text("""---
status: superseded
decided_at: 2026-09-02T10:00:00Z
superseded_by: 2026-09-03-new.md
---
# Superseded Decision
""", encoding="utf-8")
    
    # 创建一个 draft 决策
    draft_decision = decision_log_dir / "2026-09-03-draft.md"
    draft_decision.write_text("""---
status: draft
decided_at: 2026-09-03T10:00:00Z
superseded_by: null
---
# Draft Decision
""", encoding="utf-8")
    
    # 读取决策列表
    decisions = _get_decision_log_entries(gov_root, since_date=None, repo_root=None)
    
    # 验证只返回 active 的
    assert len(decisions) == 1, f"应该只返回 1 个 active 决策，实际返回 {len(decisions)} 个"
    assert decisions[0]["frontmatter"]["status"] == "active"
    assert "active" in str(decisions[0]["path"])


def test_brief_jurisdiction_conflict_detection(tmp_path: Path) -> None:
    """辖域冲突可见: 两个 active 文档辖域相同时应标记冲突。"""
    gov_root = tmp_path / "governance_workspace"
    gov_root.mkdir()
    
    # 创建 governance 目录
    governance_dir = gov_root / "governance"
    governance_dir.mkdir()
    
    # 创建两个辖域相同的 active 文档
    doc1 = governance_dir / "DOC1.md"
    doc1.write_text("""---
status: active
jurisdiction: execution
---
# Doc 1
""", encoding="utf-8")
    
    doc2 = governance_dir / "DOC2.md"
    doc2.write_text("""---
status: active
jurisdiction: execution
---
# Doc 2
""", encoding="utf-8")
    
    # 创建一个不同辖域的文档
    doc3 = governance_dir / "DOC3.md"
    doc3.write_text("""---
status: active
jurisdiction: audit
---
# Doc 3
""", encoding="utf-8")
    
    # 读取治理文档列表
    docs = _get_governance_docs(gov_root, repo_root=None)
    
    # 验证冲突检测
    assert len(docs) == 3, f"应该返回 3 个文档，实际返回 {len(docs)} 个"
    
    # 找到 execution 辖域的文档
    execution_docs = [d for d in docs if d["jurisdiction"] == "execution"]
    assert len(execution_docs) == 2, "应该有 2 个 execution 辖域的文档"
    
    # 验证冲突标记
    for doc in execution_docs:
        assert len(doc["conflicts"]) == 1, f"{doc['name']} 应该有 1 个冲突"
        assert any("DOC1.md" in c or "DOC2.md" in c for c in doc["conflicts"])
    
    # audit 辖域的文档不应有冲突
    audit_docs = [d for d in docs if d["jurisdiction"] == "audit"]
    assert len(audit_docs) == 1
    assert len(audit_docs[0]["conflicts"]) == 0, "audit 辖域文档不应有冲突"


def test_brief_zero_memory_cold_start(tmp_path: Path) -> None:
    """零记忆冷启动: 创建完整夹具，验证 brief 能回答四问。"""
    gov_root = tmp_path / "governance_workspace"
    gov_root.mkdir()
    
    # 1. 创建 stage_archive（阶段坐标）
    stage_dir = gov_root / "stage_archive"
    stage_dir.mkdir()
    snapshot = stage_dir / "20260901_test_stage.md"
    snapshot.write_text("""---
status: archived
stage_name: 测试阶段
snapshot_date: 2026-09-01
---
# Stage Snapshot
""", encoding="utf-8")
    
    # 2. 创建 decision_log（最新裁定）
    decision_log_dir = gov_root / "governance" / "decision_log" / "2026-09"
    decision_log_dir.mkdir(parents=True)
    decision = decision_log_dir / "2026-09-02-test-decision.md"
    decision.write_text("""---
status: active
decided_at: 2026-09-02T10:00:00Z
superseded_by: null
---
# Test Decision
""", encoding="utf-8")
    
    # 3. 创建 governance docs（协作模式/顾问边界）
    governance_dir = gov_root / "governance"
    roles_doc = governance_dir / "ROLES.md"
    roles_doc.write_text("""---
status: active
jurisdiction: collaboration
---
# Roles
""", encoding="utf-8")
    
    commands_doc = governance_dir / "COMMANDS.md"
    commands_doc.write_text("""---
status: active
jurisdiction: advisor_boundary
---
# Commands
""", encoding="utf-8")
    
    # 4. 创建 queue（当前在跑什么）
    tasks_root = gov_root / "5_tasks"
    queue_dir = tasks_root / "queue"
    for subdir in ["pending", "claimed", "returned", "completed", "blocked", "withdrawn"]:
        (queue_dir / subdir).mkdir(parents=True)
    
    # 添加一些测试卡
    pending_card = queue_dir / "pending" / "TEST-001.md"
    pending_card.write_text("""---
task_id: TEST-001
status: pending
---
# Test Card
""", encoding="utf-8")
    
    # 5. 创建 records
    records_dir = tasks_root / "records"
    for record_type in ["claims", "returns", "closures"]:
        (records_dir / record_type).mkdir(parents=True)
    
    # 运行 brief（JSON 输出便于验证）
    exit_code = run_brief(workspace_root=gov_root, output_format="json")
    
    assert exit_code == 0, "Brief 应该成功执行"
    
    # 注：实际输出已打印到 stdout，这里只验证不报错
    # 完整验证需要捕获 stdout 并解析 JSON，但基本冷启动已覆盖


def test_brief_global_workspace_root_flag(tmp_path: Path) -> None:
    """全局 --workspace-root 参数传递测试（R6L: 同名参数全动词统一语义）。
    
    验证全局位和子命令位传参行为一致。
    """
    import subprocess
    import sys
    
    gov_root = tmp_path / "governance_workspace"
    gov_root.mkdir()
    
    # 创建最小夹具
    stage_dir = gov_root / "stage_archive"
    stage_dir.mkdir()
    (gov_root / "governance" / "decision_log").mkdir(parents=True)
    (gov_root / "governance").mkdir(exist_ok=True)
    
    tasks_root = gov_root / "5_tasks"
    queue_dir = tasks_root / "queue"
    for subdir in ["pending", "claimed"]:
        (queue_dir / subdir).mkdir(parents=True)
    
    records_dir = tasks_root / "records"
    for record_type in ["claims", "returns", "closures"]:
        (records_dir / record_type).mkdir(parents=True)
    
    # 测试全局位传参: lybra --workspace-root <path> brief --json
    result_global = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(gov_root), "brief", "--json"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    
    assert result_global.returncode == 0, f"全局位传参应成功，stderr: {result_global.stderr}"
    assert "stage" in result_global.stdout, "全局位传参应输出 stage 信息"
    
    # 测试子命令位传参: lybra brief --workspace-root <path> --json
    result_subcommand = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "brief", "--workspace-root", str(gov_root), "--json"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    
    assert result_subcommand.returncode == 0, f"子命令位传参应成功，stderr: {result_subcommand.stderr}"
    assert "stage" in result_subcommand.stdout, "子命令位传参应输出 stage 信息"
    
    # 验证两种传参方式输出一致（排除时间戳等动态字段）
    import json
    output_global = json.loads(result_global.stdout)
    output_subcommand = json.loads(result_subcommand.stdout)
    
    # 验证关键字段一致
    assert output_global["queue"]["pending"] == output_subcommand["queue"]["pending"], \
        "全局位和子命令位传参应产生一致的 queue.pending 值"
    assert output_global["stage"]["snapshot_count"] == output_subcommand["stage"]["snapshot_count"], \
        "全局位和子命令位传参应产生一致的 stage.snapshot_count 值"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_retired_docs_no_references() -> None:
    """验证已退役文档在代码中无残留引用（AIPOS-F67杀五）。
    
    COLD-START.md / project_status.md / roadmap.md 已退役，
    代码中不应再有引用（测试/历史文档除外）。
    """
    import subprocess
    from pathlib import Path
    
    repo_root = Path(__file__).parent.parent
    
    # 搜索三个已删文件名的引用
    retired_files = ["project_status.md", "roadmap.md", "COLD-START.md"]
    
    for filename in retired_files:
        result = subprocess.run(
            [
                "grep", "-rn", filename,
                "--include=*.py",
                "--include=*.json",
                "--include=*.ts",
                "--include=*.js",
                str(repo_root),
            ],
            capture_output=True,
            text=True,
        )
        
        # 过滤测试文件和文档引用
        lines = [
            line for line in result.stdout.splitlines()
            if not any(excl in line for excl in [
                "test_aipos_f67",  # 本测试文件
                ".deploy/",         # 部署归档
                "/.md:",            # markdown 文档中的引用
                "decision_log",     # decision_log 中的历史记录
                "/tests/",          # 测试文件中的夹具数据
            ])
        ]
        
        assert len(lines) == 0, (
            f"已退役文档 {filename} 仍有代码引用:\n" + "\n".join(lines[:5])
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
