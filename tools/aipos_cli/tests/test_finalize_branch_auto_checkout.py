"""AIPOS-F11 大项A: auto_checkout 声明驱动 — 卡分支整合的自动切回 main 回归夹具。

覆盖验收 ①②④(仓停在卡分支+树干净 → 自动切回 main; 树脏 → halt+脏文件+next_step;
声明开关置 false → 停下喊人)与 F11 关键回归(卡分支已合并进 main 时, 即使 HEAD 停在
卡分支也判"已合并"跳过, 不误触发二次合并)。
"""

import subprocess
from pathlib import Path

import pytest

from tools.aipos_cli.finalize import (
    _DEFAULT_BRANCH_INTEGRATION,
    _integrate_card_branch,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def _make_card_branch(repo: Path, task_id: str) -> None:
    _git(repo, "checkout", "-qb", f"card/{task_id}")
    (repo / f"{task_id}.txt").write_text("work\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", f"{task_id}: work")


def _bi(**overrides) -> dict:
    """深拷贝默认声明(含 auto_checkout_next_step), 允许逐项覆盖。"""
    bi = dict(_DEFAULT_BRANCH_INTEGRATION)
    bi["auto_checkout_next_step"] = dict(_DEFAULT_BRANCH_INTEGRATION["auto_checkout_next_step"])
    bi.update(overrides)
    return bi


def _cur_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def test_auto_checkout_switches_to_main_and_merges(tmp_path):
    """验收①: 仓停在卡分支+树干净 → 自动切回 main 并合并 (零人肉)。"""
    repo = _make_repo(tmp_path)
    _make_card_branch(repo, "AIPOS-F11")
    assert _cur_branch(repo) == "card/AIPOS-F11"

    ops: list[str] = []
    r = _integrate_card_branch(
        task_id="AIPOS-F11",
        verdict_id="v123",
        workspace_root=repo,
        governance_root=tmp_path,
        dry_run=False,
        operations=ops,
        branch_integration=_bi(),
    )

    assert r["action"] == "merged"
    assert r["blocked"] is False
    assert _cur_branch(repo) == "main"
    assert any("已自动切回 main" in o for o in ops)
    assert "Merge card/AIPOS-F11" in _git(repo, "log", "--oneline", "main").stdout


def test_dirty_tree_blocks_with_files_and_next_step(tmp_path):
    """验收②: 卡分支+树脏 → halt, 输出含脏文件与 next_step。"""
    repo = _make_repo(tmp_path)
    _make_card_branch(repo, "AIPOS-F11")
    (repo / "dirty.txt").write_text("uncommitted\n")

    ops: list[str] = []
    r = _integrate_card_branch(
        task_id="AIPOS-F11",
        verdict_id="v123",
        workspace_root=repo,
        governance_root=tmp_path,
        dry_run=False,
        operations=ops,
        branch_integration=_bi(),
    )

    assert r["action"] == "blocked_not_clean"
    assert r["blocked"] is True
    assert "dirty.txt" in r["message"]
    assert "下一步" in r["message"]
    # 仍停在卡分支(未切走, 未合并)
    assert _cur_branch(repo) == "card/AIPOS-F11"
    assert "Merge card/AIPOS-F11" not in _git(repo, "log", "--oneline", "main").stdout


def test_auto_checkout_disabled_blocks(tmp_path):
    """验收④: 声明开关置 false → 行为回退为停下喊人。"""
    repo = _make_repo(tmp_path)
    _make_card_branch(repo, "AIPOS-F11")

    ops: list[str] = []
    r = _integrate_card_branch(
        task_id="AIPOS-F11",
        verdict_id="v123",
        workspace_root=repo,
        governance_root=tmp_path,
        dry_run=False,
        operations=ops,
        branch_integration=_bi(auto_checkout=False),
    )

    assert r["action"] == "blocked_auto_checkout_disabled"
    assert r["blocked"] is True
    assert "auto_checkout=false" in r["message"]
    assert "下一步" in r["message"]
    assert _cur_branch(repo) == "card/AIPOS-F11"


def test_already_merged_skips_even_when_head_on_card_branch(tmp_path):
    """F11 关键回归: 卡分支已合并进 main 时, 即使 HEAD 停在卡分支, 也判"已合并"跳过。

    修复前 merged 判定查 HEAD(卡分支对自身恒"已合并"), 会误把"已合并"当成"已合并"之外
    的路径; 修复后显式查 main, 此场景正确走 skip 而非二次 auto_checkout+merge。
    """
    repo = _make_repo(tmp_path)
    _make_card_branch(repo, "AIPOS-F11")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-m", "Merge card/AIPOS-F11", "card/AIPOS-F11")
    _git(repo, "checkout", "-q", "card/AIPOS-F11")  # HEAD 停在已合并的卡分支

    ops: list[str] = []
    r = _integrate_card_branch(
        task_id="AIPOS-F11",
        verdict_id="v123",
        workspace_root=repo,
        governance_root=tmp_path,
        dry_run=False,
        operations=ops,
        branch_integration=_bi(),
    )

    assert r["action"] == "skipped_already_merged"
    assert r["blocked"] is False


def test_finalize_task_auto_checkout_before_branch_check(tmp_path):
    """验收①端到端: finalize_task 在卡分支+树干净下自动切回 main, 不因部署分支强制拦下。

    修复前 auto_checkout 只在 _integrate_card_branch(部署分支强制之后), 卡分支上 --push 会先
    被 check_deployment_branch 拦下; 修复后 _ensure_on_main_branch 提前到分支强制之前执行。
    """
    from unittest.mock import patch
    from tools.aipos_cli import finalize as F

    repo = _make_repo(tmp_path)
    _make_card_branch(repo, "AIPOS-F11")  # 停在卡分支, 树干净
    gov_root = tmp_path / "gov"  # 治理仓与产品仓分离(避开 R6A 单根硬拒)
    gov_root.mkdir()

    with patch.object(F, "check_task_can_finalize", return_value={
        "can_finalize": True, "task_id": "AIPOS-F11", "verdict": "PASS",
        "verdict_record_path": None, "verdict_id": "v1", "reason": "ok",
    }), patch.object(F, "check_stage_archive_gate", return_value={
        "passed": True, "message": "Stage gate OK", "stage_archive_dir": None,
        "snapshot_count": 1, "path_key": "stage_archive",
    }), patch.object(F, "_check_deployment_integrity", return_value={
        "integrity_ok": True, "current_commit": "x", "head_commit": "x",
        "provenance": None, "missing_commits": [], "message": "ok",
    }), patch.object(F, "_git_local_origin_synced", return_value=False):
        result = F.finalize_task(
            task_id="AIPOS-F11", actor="t", workspace_root=repo, governance_root=gov_root,
            dry_run=False, push=False, deploy=False,
        )

    # 不得被部署分支强制拦下(BLOCK 且信息含 branch check failed)
    assert not (result["verdict"] == "BLOCK" and "branch check" in (result["message"] or "").lower())
    # 已自动切回 main 并合并
    assert _cur_branch(repo) == "main"
    assert "Merge card/AIPOS-F11" in _git(repo, "log", "--oneline", "main").stdout
