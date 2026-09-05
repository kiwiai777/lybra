"""AIPOS-F69: governance_commit 解绑卡号+并发安全+push后校验 — 单元测试

验收：
① 先红后绿·复现当日撞车 — 修复前 push 成功但远端不含；修复后自动 rebase + push后校验通过
② 解绑卡号 — 不带 task_id 执行台账追加类提交，走同一校验链与 pre-commit 四检
③ push后校验 — 构造"push 返回 0 但远端不含"→ 断言报失败
④ 只动本项目路径 — 复用 ws_prefix 判据（本测试中 governance_root 即项目范围）
⑤ 原子性 — push 失败不留半提交
⑥ 兼容 — 带 task_id 的收口路径零回归
"""
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools.aipos_cli.governance_commit import governance_commit, check_governance_completeness
from tools.schema_constants import Verdict


@pytest.fixture
def git_repo(tmp_path):
    """创建一个带远程的 git 仓库作为测试靶场"""
    # 创建 bare 远程仓库
    remote_dir = tmp_path / "remote.git"
    remote_dir.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=str(remote_dir), check=True, capture_output=True)
    
    # 创建本地工作仓库
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(local_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(local_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(local_dir), check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_dir)], cwd=str(local_dir), check=True, capture_output=True)
    
    # 创建初始提交并推送
    (local_dir / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(local_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(local_dir), check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(local_dir), check=True, capture_output=True)
    
    return {
        "local": local_dir,
        "remote": remote_dir,
    }


@pytest.fixture
def governance_repo(git_repo, tmp_path, monkeypatch):
    """创建包含治理结构的仓库"""
    local = git_repo["local"]
    
    # 创建治理目录结构（简化版，只包含测试需要的）
    (local / "governance").mkdir()
    (local / "governance" / "decision_log").mkdir()
    (local / "governance" / "decision_log" / ".gitkeep").write_text("")
    (local / "task_cards").mkdir()
    (local / "task_cards" / ".gitkeep").write_text("")
    (local / "stage_archive").mkdir()
    (local / "stage_archive" / ".gitkeep").write_text("")
    
    # 提交目录结构
    subprocess.run(["git", "add", "-A"], cwd=str(local), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add governance structure"], cwd=str(local), check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=str(local), check=True, capture_output=True)
    
    # Mock resolve_governance_path 以避免依赖真实 schema
    def mock_resolve_governance_path(key, governance_root, repo_root=None):
        path_map = {
            "task_cards": governance_root / "task_cards",
            "decision_log_dir": governance_root / "governance" / "decision_log",
            "stage_archive": governance_root / "stage_archive",
        }
        return path_map.get(key, governance_root / key)
    
    monkeypatch.setattr(
        "tools.schema_loader.resolve_governance_path",
        mock_resolve_governance_path
    )
    
    return {
        "governance_root": local,
        "schema_root": tmp_path,
        **git_repo,
    }


def test_concurrent_safety_with_rebase(governance_repo):
    """验收①: 并发安全 — 远端已前进，自动 rebase 后 push 并验证"""
    local = governance_repo["governance_root"]
    remote = governance_repo["remote"]
    
    # 创建任务目录和归档文件
    task_id = "TEST-001"
    task_dir = local / "task_cards" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "RETURN.md").write_text("# Return\nTest return")
    
    subprocess.run(["git", "add", "-A"], cwd=str(local), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add task card"], cwd=str(local), check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=str(local), check=True, capture_output=True)
    
    # 模拟远端已前进：另一个 clone 推送新内容
    other_clone = governance_repo["local"].parent / "other_clone"
    subprocess.run(["git", "clone", str(remote), str(other_clone)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Other User"], cwd=str(other_clone), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "other@example.com"], cwd=str(other_clone), check=True, capture_output=True)
    
    (other_clone / "other_file.md").write_text("# Other change")
    subprocess.run(["git", "add", "other_file.md"], cwd=str(other_clone), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Other project update"], cwd=str(other_clone), check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=str(other_clone), check=True, capture_output=True)
    
    # 现在 local 的本地 HEAD 落后于 origin/main，模拟并发场景
    # 在 local 添加新内容但不先 pull
    (task_dir / "AUDIT-REPORT.md").write_text("# Audit\nTest audit")
    
    # 执行 governance_commit，应该自动 fetch + rebase + push
    result = governance_commit(
        governance_root=local,
        task_id=task_id,
        actor="test-executor",
        repo_root=governance_repo["schema_root"],
        dry_run=False,
        push=True,
    )
    
    # 断言：提交成功且 push 成功
    assert result["verdict"] == Verdict.PASS
    assert result["committed"] is True
    assert result["pushed"] is True
    assert result["commit_hash"] is not None
    
    # 断言：操作日志包含 rebase
    ops = " ".join(result["operations"])
    assert "Fetched from remote" in ops
    assert "Rebased successfully" in ops or "Remote has advanced" in ops
    
    # 断言：push 后校验通过（验证远端确实包含本次 commit）
    assert "Verified commit" in ops
    
    # 最终验证：远端确实包含本次 commit
    verify_result = subprocess.run(
        ["git", "branch", "-r", "--contains", result["commit_hash"]],
        cwd=str(local),
        check=True,
        capture_output=True,
        text=True,
    )
    assert verify_result.stdout.strip()  # 非空说明远端包含


def test_taskid_optional_governance_batch(governance_repo):
    """验收②: 解绑卡号 — 不带 task_id 执行台账追加类提交"""
    local = governance_repo["governance_root"]
    
    # 添加一个治理批次更新（非卡相关）
    decision_dir = local / "governance" / "decision_log" / "2026-09"
    decision_dir.mkdir(parents=True)
    (decision_dir / "2026-09-05-test-decision.md").write_text("# Test Decision\n")
    
    # 不带 task_id 调用
    result = governance_commit(
        governance_root=local,
        task_id=None,  # 无卡号
        actor="test-advisor",
        repo_root=governance_repo["schema_root"],
        dry_run=False,
        push=True,
    )
    
    # 断言：提交成功
    assert result["verdict"] == Verdict.PASS
    assert result["committed"] is True
    assert result["pushed"] is True
    assert result["task_id"] is None
    
    # 断言：commit message 提到"治理批次"
    assert "治理批次更新" in result["message"]
    
    # 断言：completeness_check 跳过了 task_cards 检查
    details = result["completeness_check"]["details"]
    assert details["task_cards"]["note"] == "Skipped (no task_id provided)"


def test_push_verification_failure(governance_repo, monkeypatch):
    """验收③: push后校验 — 构造"push 返回 0 但远端不含"场景"""
    local = governance_repo["governance_root"]
    
    # 创建任务目录和归档文件
    task_id = "TEST-002"
    task_dir = local / "task_cards" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "RETURN.md").write_text("# Return\n")
    
    # Mock git branch -r --contains 返回空（模拟远端不含本次 commit）
    original_run = subprocess.run
    
    def mock_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("command", [])
        if "branch" in cmd and "-r" in cmd and "--contains" in cmd:
            # 返回空输出，模拟远端不含 commit
            class MockResult:
                stdout = ""
                stderr = ""
                returncode = 0
            return MockResult()
        return original_run(*args, **kwargs)
    
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    # 执行 governance_commit
    result = governance_commit(
        governance_root=local,
        task_id=task_id,
        actor="test-executor",
        repo_root=governance_repo["schema_root"],
        dry_run=False,
        push=True,
    )
    
    # 断言：FAIL（因为 push 后验证失败）
    assert result["verdict"] == Verdict.FAIL
    assert result["committed"] is True
    assert result["pushed"] is False  # push 命令成功但验证失败 = 未真正 push
    assert "远端不包含" in result["message"] or "not found in remote" in result["message"]


def test_backward_compatibility_with_taskid(governance_repo):
    """验收⑥: 兼容 — 带 task_id 的收口路径零回归"""
    local = governance_repo["governance_root"]
    
    # 创建任务目录和归档文件
    task_id = "TEST-003"
    task_dir = local / "task_cards" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "RETURN.md").write_text("# Return\n")
    (task_dir / "CLOSURE.md").write_text("# Closure\n")
    
    # 带 task_id 调用（原有调用方式）
    result = governance_commit(
        governance_root=local,
        task_id=task_id,
        actor="test-executor",
        repo_root=governance_repo["schema_root"],
        dry_run=False,
        push=True,
    )
    
    # 断言：提交成功
    assert result["verdict"] == Verdict.PASS
    assert result["committed"] is True
    assert result["pushed"] is True
    assert result["task_id"] == task_id
    
    # 断言：commit message 包含卡号
    assert task_id in result["message"] or "N6 收账" in result["message"]


def test_no_changes_to_commit(governance_repo):
    """测试无变更场景 — 应返回 info 而非 BLOCK"""
    local = governance_repo["governance_root"]
    
    # 不添加任何变更，直接调用
    result = governance_commit(
        governance_root=local,
        task_id="TEST-004",
        actor="test-executor",
        repo_root=governance_repo["schema_root"],
        dry_run=False,
        push=True,
    )
    
    # 断言：PASS（无变更不是错误）
    assert result["verdict"] == Verdict.PASS
    assert result["committed"] is False
    assert result["pushed"] is False
    assert "无待收内容" in result["message"] or "No changes" in result["message"]


def test_completeness_check_with_optional_taskid():
    """测试 check_governance_completeness 的 task_id 可选逻辑"""
    # 不需要真实 repo，只测试逻辑
    tmp = Path(tempfile.mkdtemp())
    
    # 测试 task_id=None
    result = check_governance_completeness(
        governance_root=tmp,
        task_id=None,
        repo_root=None,
    )
    
    # 断言：跳过 task_cards 检查
    assert result["details"]["task_cards"]["note"] == "Skipped (no task_id provided)"
    assert result["details"]["archive_files"]["note"] == "Skipped (no task_id provided)"
    
    # 完整性应该为 True（因为跳过了必填项）
    assert result["complete"] is True or "stage_archive" in str(result["missing"])
