"""AIPOS-C3C — N5 branch_integration 声明驱动的卡分支整合测试.

覆盖:
  1. 归属解析器 _task_id_from_commit_subject 读同一份声明 + feat/fix/chore 家族兼容
  2. _integrate_card_branch: merge --no-ff (信息含裁决号, 分支保留)
  3. 冲突 → 中止出声列文件, main 无半合并残留
  4. 无分支 → 跳过出声
  5. dry-run 显示合并计划
  6. 声明生效性: 改 branch_pattern → 寻找行为跟随
  7. 声明可加载 + 关键键存在 (transitions.schema N5.branch_integration)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.aipos_cli.deployment_authorization import (
    _branch_pattern_regex,
    _task_id_from_commit_subject,
)
from tools.aipos_cli.finalize import (
    _branch_name_for_task,
    _integrate_card_branch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_BRANCH_INTEGRATION = {
    "branch_pattern": "card/{task_id}",
    "merge_strategy": "no-ff",
    "merge_message_format": "Merge {branch}: {summary} ({verdict_id})",
}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    """一个带 main 分支与初始提交的临时 git 仓."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@test.local")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    return repo


def _make_branch(repo: Path, branch: str, message: str, file: str, content: str) -> None:
    """从 main 建分支并提交一个文件, 再切回 main."""
    _git(repo, "checkout", "-q", "-b", branch)
    (repo / file).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    _git(repo, "checkout", "-q", "main")


# ---------------------------------------------------------------------------
# 1. 归属解析器
# ---------------------------------------------------------------------------

def test_task_id_from_commit_subject_conventional_family():
    assert _task_id_from_commit_subject("feat(AIPOS-C3): 大项A", repo_root=REPO_ROOT) == "AIPOS-C3"
    assert _task_id_from_commit_subject("fix(AIPOS-C3B): FIX轮", repo_root=REPO_ROOT) == "AIPOS-C3B"
    assert _task_id_from_commit_subject("chore(AIPOS-C4): 清理", repo_root=REPO_ROOT) == "AIPOS-C4"
    assert _task_id_from_commit_subject("docs(AIPOS-C1): 手册", repo_root=REPO_ROOT) == "AIPOS-C1"


def test_task_id_from_commit_subject_bare_prefix():
    assert _task_id_from_commit_subject("AIPOS-C2: 身份配置单一真相", repo_root=REPO_ROOT) == "AIPOS-C2"


def test_task_id_from_commit_subject_merge_message_declaration():
    subject = "Merge card/AIPOS-A1: 顾问产生侧工具化 (PASS_WITH_NOTES verdict_AIPOS-A1_20260819_143222)"
    assert _task_id_from_commit_subject(subject, repo_root=REPO_ROOT) == "AIPOS-A1"
    # 旧格式 (含引号) 也因 branch_pattern 声明被捕获
    assert _task_id_from_commit_subject("Merge branch 'card/AIPOS-C3B'", repo_root=REPO_ROOT) == "AIPOS-C3B"


def test_task_id_from_commit_subject_no_id():
    assert _task_id_from_commit_subject("Initial commit", repo_root=REPO_ROOT) is None


# ---------------------------------------------------------------------------
# 2. 声明派生
# ---------------------------------------------------------------------------

def test_branch_pattern_regex_reads_declaration():
    regex = _branch_pattern_regex(REPO_ROOT)
    assert regex is not None
    import re
    m = re.search(regex, "Merge card/AIPOS-C3C: summary (verdict_X)")
    assert m and m.group(1) == "AIPOS-C3C"


def test_branch_name_for_task():
    assert _branch_name_for_task("card/{task_id}", "AIPOS-C3C") == "card/AIPOS-C3C"
    assert _branch_name_for_task("card2/{task_id}", "AIPOS-C3C") == "card2/AIPOS-C3C"


# ---------------------------------------------------------------------------
# 3. 整合主流程
# ---------------------------------------------------------------------------

def test_integrate_merges_no_ff_and_preserves_branch(git_repo):
    _make_branch(git_repo, "card/AIPOS-C3C", "feat(AIPOS-C3C): implementation", "impl.txt", "impl\n")

    ops: list[str] = []
    res = _integrate_card_branch(
        "AIPOS-C3C", "verdict_AIPOS-C3C_2026_1", git_repo, git_repo,
        dry_run=False, operations=ops, branch_integration=_BRANCH_INTEGRATION,
    )

    assert res["action"] == "merged"
    assert res["blocked"] is False

    # merge commit 信息含裁决号 + 卡号 (归属保证)
    log = _git(git_repo, "log", "--format=%s", "-1").stdout.strip()
    assert "card/AIPOS-C3C" in log
    assert "verdict_AIPOS-C3C_2026_1" in log

    # 分支保留不删除
    assert _git(git_repo, "rev-parse", "--verify", "refs/heads/card/AIPOS-C3C").returncode == 0


def test_integrate_conflict_blocks_and_lists_files(git_repo):
    (git_repo / "f.txt").write_text("base\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "add f.txt")
    # 分支改 f.txt
    _git(git_repo, "checkout", "-q", "-b", "card/AIPOS-C3C")
    (git_repo / "f.txt").write_text("branch\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "feat(AIPOS-C3C): branch change")
    _git(git_repo, "checkout", "-q", "main")
    # main 也改 f.txt → 冲突
    (git_repo / "f.txt").write_text("main\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "main change")

    ops: list[str] = []
    res = _integrate_card_branch(
        "AIPOS-C3C", "verdict_X", git_repo, git_repo,
        dry_run=False, operations=ops, branch_integration=_BRANCH_INTEGRATION,
    )

    assert res["action"] == "blocked_conflict"
    assert res["blocked"] is True
    assert "f.txt" in res["conflict_files"]

    # main 无半合并残留: 工作树干净 + 内容回滚
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert (git_repo / "f.txt").read_text().strip() == "main"


def test_integrate_no_branch_skips(git_repo):
    ops: list[str] = []
    res = _integrate_card_branch(
        "AIPOS-NOPE", "verdict_X", git_repo, git_repo,
        dry_run=False, operations=ops, branch_integration=_BRANCH_INTEGRATION,
    )
    assert res["action"] == "skipped_no_branch"
    assert res["blocked"] is False
    assert "跳过整合" in res["message"]


def test_integrate_dry_run_shows_plan(git_repo):
    _make_branch(git_repo, "card/AIPOS-C3C", "feat(AIPOS-C3C): implementation", "impl.txt", "impl\n")

    ops: list[str] = []
    res = _integrate_card_branch(
        "AIPOS-C3C", "verdict_AIPOS-C3C_2026_1", git_repo, git_repo,
        dry_run=True, operations=ops, branch_integration=_BRANCH_INTEGRATION,
    )
    assert res["action"] == "merged"
    assert "DRY-RUN" in res["message"]
    assert "card/AIPOS-C3C" in res["message"]
    # dry-run 不真正合并
    log = _git(git_repo, "log", "--format=%s", "-1").stdout.strip()
    assert "Merge card/AIPOS-C3C" not in log


def test_integrate_follows_declaration_pattern(git_repo):
    """声明生效性: 改 branch_pattern → 寻找行为跟随."""
    _make_branch(git_repo, "card/AIPOS-C3C", "feat(AIPOS-C3C): implementation", "impl.txt", "impl\n")

    ops: list[str] = []
    custom = {**_BRANCH_INTEGRATION, "branch_pattern": "card2/{task_id}"}
    res = _integrate_card_branch(
        "AIPOS-C3C", "verdict_X", git_repo, git_repo,
        dry_run=False, operations=ops, branch_integration=custom,
    )
    # 声明说 card2/, 但只有 card/ 分支 → 找不到 → 跳过
    assert res["branch_name"] == "card2/AIPOS-C3C"
    assert res["action"] == "skipped_no_branch"


# ---------------------------------------------------------------------------
# 7. 声明可加载 (transitions.schema N5.branch_integration)
# ---------------------------------------------------------------------------

def test_branch_integration_declaration_loadable():
    from tools.schema_loader import get_branch_integration
    bi = get_branch_integration(REPO_ROOT)
    assert bi["branch_pattern"] == "card/{task_id}"
    assert bi["merge_strategy"] == "no-ff"
    assert "{branch}" in bi["merge_message_format"]
    assert "{verdict_id}" in bi["merge_message_format"]
    assert bi["conflict_policy"]["action"] == "halt_and_report"
    assert bi["conflict_policy"]["auto_resolve"] is False
    assert bi["missing_branch_policy"]["action"] == "skip_and_report"
    assert bi["branch_retention"] == "preserve"
