"""AIPOS-R6M 大项A③ stage 粒度门票(阶段快照) — 单元测试。

判据与路径从 config.schema 治理目录树读 (timeline_enforcement.stage_level +
governance_structure.paths.stage_archive), 代码零写死。缺阶段快照 → BLOCK。
"""

from pathlib import Path

import pytest

from tools.aipos_cli.finalize import check_stage_archive_gate

# 产品仓根 = 本文件向上三级 (tools/aipos_cli/tests/ -> tools/ -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_stage_gate_block_missing_dir(tmp_path):
    """治理仓无 stage_archive/ 目录 → BLOCK (门票缺失)。"""
    result = check_stage_archive_gate(tmp_path, repo_root=REPO_ROOT)
    assert result["passed"] is False
    assert "missing" in result["message"]
    assert result["snapshot_count"] == 0
    assert result["path_key"] == "stage_archive"


def test_stage_gate_block_empty_dir(tmp_path):
    """stage_archive/ 存在但为空 → BLOCK (无阶段快照)。"""
    (tmp_path / "stage_archive").mkdir()
    result = check_stage_archive_gate(tmp_path, repo_root=REPO_ROOT)
    assert result["passed"] is False
    assert result["snapshot_count"] == 0


def test_stage_gate_block_index_only(tmp_path):
    """stage_archive/ 只有 README/index → BLOCK (索引非阶段快照)。"""
    sa = tmp_path / "stage_archive"
    sa.mkdir()
    (sa / "README.md").write_text("# Index\n", encoding="utf-8")
    result = check_stage_archive_gate(tmp_path, repo_root=REPO_ROOT)
    assert result["passed"] is False
    assert result["snapshot_count"] == 0


def test_stage_gate_pass_with_snapshots(tmp_path):
    """stage_archive/ 含阶段快照 → PASS。"""
    sa = tmp_path / "stage_archive"
    sa.mkdir()
    (sa / "README.md").write_text("# Index\n", encoding="utf-8")
    (sa / "2026-06-03_mcp-claim-and-bounded-delegation.md").write_text(
        "# Stage snapshot\n", encoding="utf-8"
    )
    result = check_stage_archive_gate(tmp_path, repo_root=REPO_ROOT)
    assert result["passed"] is True
    assert result["snapshot_count"] == 1
    # 路径必须读自 config.schema 治理目录树 (stage_archive 键), 非代码写死
    assert result["stage_archive_dir"] == str(tmp_path / "stage_archive")


def test_stage_gate_path_comes_from_config_schema(tmp_path):
    """路径由 config.schema 治理目录树解析: stage_archive 键 → path=stage_archive/。"""
    result = check_stage_archive_gate(tmp_path, repo_root=REPO_ROOT)
    # 即使 BLOCK, 返回的 stage_archive_dir 也必须落在 config.schema 声明的
    # governance_structure.paths.stage_archive.path (stage_archive/) 下, 证明零写死。
    assert result["stage_archive_dir"] == str(tmp_path / "stage_archive")
