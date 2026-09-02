"""AIPOS-F70-fix2 — finalize 比对对象修正测试

实证(2026-09-02 活体, F71 finalize 首撞):
  `finalize --task-id AIPOS-F71` BLOCK:「裁决 commit_sha 不匹配. 裁决覆盖: b54638f2, 
  要求: c4228c96」——b54638f2 是**待合并的 card/AIPOS-F71 分支顶端**(裁决正确绑定之), 
  c4228c96 是**当前 main HEAD**(F70-fix1 R4 合并提交, 与 F71 无关)。

核心缺陷:
  审的是卡分支产物, finalize 却要求裁决覆盖 main 现状 → 多卡流水线下 main 必然在
  "某卡审计"与"该卡 finalize"之间被他卡推进 → **一切 finalize 永久死锁**。

修复:
  比对对象 = 待整合的卡分支顶端 commit (裁决绑的正是它); main HEAD 与他卡提交不在
  本卡裁决义务内 (他们有各自裁决, 由 deploy 的逐 commit 覆盖校验兜)。

测试策略:
  1. **先红后绿·复现当日现场**: 造"卡A审计后 main 被卡B推进"→ 修复前 finalize 卡A BLOCK; 
     修复后 finalize 卡A 成功
  2. 真不匹配仍拦: 裁决绑 X 而分支 tip 为 Y(Y≠X)→ BLOCK 零回归
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.aipos_cli.finalize import finalize_task
from tools.schema_constants import Verdict


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def scenario_repo(tmp_path):
    """复现多卡流水线场景的 git 仓。
    
    场景:
      1. main 初始提交
      2. 卡A (AIPOS-F70-A) 从 main 建分支并提交
      3. 审计员审卡A, 裁决绑定卡A分支 tip (commit_sha_A)
      4. 卡B 直提 main (模拟他卡推进 main)
      5. finalize 卡A → 应成功 (审的是卡A分支, 与 main 被他卡推进无关)
    """
    repo = tmp_path / "repo"
    gov = tmp_path / "gov"
    repo.mkdir()
    gov.mkdir()
    
    # 治理仓结构
    (gov / "card_policy.json").write_text(
        json.dumps({"schema_version": "1.0.0", "task_id_pattern": "AIPOS-[A-Z0-9-]+"}),
        encoding="utf-8",
    )
    verdicts_root = gov / "5_tasks" / "records" / "audit_verdicts"
    verdicts_root.mkdir(parents=True)
    
    # 产品仓 git 初始化
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@test.local")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    
    # .deploy/current (模拟部署状态 = main HEAD)
    deploy_dir = repo / ".deploy" / "current"
    deploy_dir.mkdir(parents=True)
    main_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (deploy_dir / "VERSION").write_text(f"git_commit: {main_head}\n", encoding="utf-8")
    
    return {"repo": repo, "gov": gov, "verdicts_root": verdicts_root}


def _make_card_branch(repo: Path, task_id: str, message: str) -> str:
    """从 main 建卡分支并提交, 返回分支 tip SHA (不切回 main)."""
    _git(repo, "checkout", "-q", "-b", f"card/{task_id}")
    (repo / f"{task_id}.txt").write_text(f"{task_id} implementation\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return tip


def _write_gate_verdict(verdicts_root: Path, task_id: str, commit_sha: str, verdict: str = "PASS") -> None:
    """写门生裁决记录 (具备机器特征: record_type + verdict_id + verdict_at + artifact_subject.commit_sha)."""
    task_dir = verdicts_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    verdict_id = f"verdict_{task_id}_20260902_100000_auditor"
    verdict_file = task_dir / f"{verdict_id}.md"
    content = f"""---
record_type: audit_verdict_record
verdict_id: {verdict_id}
verdict_at: '2026-09-02T10:00:00Z'
reviewed_task_id: {task_id}
verdict: {verdict}
artifact_subject:
  commit_sha: {commit_sha}
  tree_hash: dummy_tree
---
# Audit Verdict: {verdict}

Reviewed commit: {commit_sha}
"""
    verdict_file.write_text(content, encoding="utf-8")


def test_f70_fix2_red_main_advanced_blocks_before_fix(scenario_repo):
    """先红: 复现当日现场 — 卡A审计后 main 被卡B推进 → 修复前 finalize 卡A BLOCK。
    
    场景:
      1. 卡A (AIPOS-F70-A) 分支提交, 审计 PASS (裁决绑 card/AIPOS-F70-A tip = commit_A)
      2. 卡B 直提 main (main HEAD 变为 commit_B ≠ commit_A)
      3. finalize --task-id AIPOS-F70-A:
         - 修复前: 拿 main HEAD (commit_B) 比裁决 (commit_A) → BLOCK "不匹配"
         - 修复后: 拿 card/AIPOS-F70-A tip (commit_A) 比裁决 (commit_A) → PASS
    
    本测试验证修复后行为 (绿), 通过检查操作日志确认用卡分支 tip 而非 main HEAD。
    """
    repo = scenario_repo["repo"]
    gov = scenario_repo["gov"]
    verdicts_root = scenario_repo["verdicts_root"]
    
    # 1. 卡A 分支提交
    commit_A = _make_card_branch(repo, "AIPOS-F70-A", "feat(AIPOS-F70-A): 卡A实现")
    _git(repo, "checkout", "-q", "main")
    
    # 2. 审计 PASS (裁决绑 commit_A = card/AIPOS-F70-A tip)
    _write_gate_verdict(verdicts_root, "AIPOS-F70-A", commit_A, "PASS")
    
    # 3. 卡B 直提 main (推进 main HEAD)
    (repo / "card_B.txt").write_text("卡B实现\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(AIPOS-F70-B): 卡B直提main")
    commit_B = _git(repo, "rev-parse", "HEAD").stdout.strip()
    
    # 卡B 也写个裁决 (模拟他卡有自己的裁决)
    _write_gate_verdict(verdicts_root, "AIPOS-F70-B", commit_B, "PASS")
    
    # 4. finalize 卡A (修复后应成功: 用卡A分支 tip 比对, 而非 main HEAD)
    result = finalize_task(
        task_id="AIPOS-F70-A",
        actor="test_finalize",
        workspace_root=repo,
        governance_root=gov,
        dry_run=True,  # dry-run 只验证逻辑, 不真合并
        push=False,
        deploy=False,
    )
    
    # 验收: 修复后成功
    assert result["can_finalize"] is True, f"Expected can_finalize=True, got {result.get('message', result)}"
    
    # 验证操作日志: 必须提到"卡分支 tip"而非"main HEAD"
    ops_text = " ".join(result["operations"])
    assert "F70-fix2" in ops_text, "修复标记必须出现在操作日志"
    assert "card/AIPOS-F70-A" in ops_text, "卡分支名必须出现"
    assert commit_A[:8] in ops_text, "卡分支 tip (commit_A) 必须出现"
    
    # 进一步验证: 日志中应说明 main HEAD ≠ 卡分支 tip
    assert commit_B[:8] in ops_text, "main HEAD (commit_B) 也应出现在日志中对比"


def test_f70_fix2_green_mismatch_still_blocks(scenario_repo):
    """绿: 真不匹配仍拦 — 裁决绑 X 而分支 tip 为 Y(Y≠X)→ BLOCK 零回归。
    
    场景:
      1. 卡A 分支 commit_X, 审计 PASS (裁决绑 commit_X)
      2. 卡A 分支又 amend 成 commit_Y (产物变化, 裁决失效)
      3. finalize 卡A → BLOCK "裁决不匹配" (commit_Y ≠ commit_X)
    """
    repo = scenario_repo["repo"]
    gov = scenario_repo["gov"]
    verdicts_root = scenario_repo["verdicts_root"]
    
    # 1. 卡A 分支 commit_X
    commit_X = _make_card_branch(repo, "AIPOS-F70-C", "feat(AIPOS-F70-C): 版本X")
    
    # 2. 审计 PASS (裁决绑 commit_X)
    _write_gate_verdict(verdicts_root, "AIPOS-F70-C", commit_X, "PASS")
    
    # 3. 分支又改了 (产物变化)
    (repo / "AIPOS-F70-C.txt").write_text("版本Y (改动)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "--no-edit")
    commit_Y = _git(repo, "rev-parse", "HEAD").stdout.strip()
    
    assert commit_X != commit_Y, "amend 后 commit 必须变化"
    
    # 切回 main (finalize 入口前置: 确保在 main 分支)
    _git(repo, "checkout", "-q", "main")
    
    # 4. finalize 卡A → BLOCK (commit_Y ≠ commit_X, 裁决失效)
    result = finalize_task(
        task_id="AIPOS-F70-C",
        actor="test_finalize",
        workspace_root=repo,
        governance_root=gov,
        dry_run=True,
        push=False,
        deploy=False,
    )
    
    # 验收: 真不匹配必须拦下
    assert result["can_finalize"] is False, "裁决不匹配必须 BLOCK"
    assert "不匹配" in result["message"] or "mismatch" in result["message"].lower(), \
        f"BLOCK 原因必须说明不匹配: {result['message']}"
    
    # 验证日志: 提到两个不同的 commit
    ops_text = " ".join(result["operations"])
    assert commit_X[:8] in ops_text or commit_Y[:8] in ops_text, \
        "日志必须提到冲突的 commit (裁决覆盖的 vs 当前分支 tip)"


def test_f70_fix2_branch_already_merged_uses_main_head(scenario_repo):
    """分支已合并场景: 分支 tip 已是 main 祖先 → 用 main HEAD 作为核对对象 (向后兼容)。
    
    场景:
      1. 卡A 分支提交 commit_A, 审计 PASS (裁决绑 commit_A)
      2. 卡A 分支合并进 main (commit_A 成为 main 祖先)
      3. finalize 卡A → 应成功 (分支已合并, 核对对象从 artifact_subject 推导)
    """
    repo = scenario_repo["repo"]
    gov = scenario_repo["gov"]
    verdicts_root = scenario_repo["verdicts_root"]
    
    # 1. 卡A 分支提交
    commit_A = _make_card_branch(repo, "AIPOS-F70-D", "feat(AIPOS-F70-D): 卡D实现")
    
    # 2. 审计 PASS (裁决绑 commit_A)
    _write_gate_verdict(verdicts_root, "AIPOS-F70-D", commit_A, "PASS")
    
    # 3. 手动合并进 main (模拟已合并场景)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "card/AIPOS-F70-D", "-m", "Merge card/AIPOS-F70-D")
    
    # 4. finalize 卡D → 应跳过整合 (分支已合并)
    result = finalize_task(
        task_id="AIPOS-F70-D",
        actor="test_finalize",
        workspace_root=repo,
        governance_root=gov,
        dry_run=True,
        push=False,
        deploy=False,
    )
    
    # 验收: 已合并的分支 finalize 应成功 (跳过整合, 但裁决核对仍执行)
    assert result["can_finalize"] is True, f"已合并分支 finalize 应成功: {result.get('message', result)}" 
    
    # 验证操作日志: 提到"已合并"或"跳过整合"
    ops_text = " ".join(result["operations"])
    assert "已合并" in ops_text or "跳过整合" in ops_text or "skipped" in ops_text.lower(), \
        "已合并分支应跳过整合"


def test_f70_fix2_no_branch_non_code_task_uses_main_head(scenario_repo):
    """无分支的非代码任务: 用 main HEAD 作为核对对象 (向后兼容历史卡)。
    
    场景:
      1. 非代码任务 (无 output_target 或 task_mode != code) 直提 main
      2. 审计 PASS (裁决绑 main HEAD)
      3. finalize → 应成功 (无卡分支, 用 main HEAD)
    """
    repo = scenario_repo["repo"]
    gov = scenario_repo["gov"]
    verdicts_root = scenario_repo["verdicts_root"]
    
    # 1. 直提 main (模拟非代码任务)
    (repo / "doc.txt").write_text("纯文档任务\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs(AIPOS-F70-E): 纯文档任务")
    commit_E = _git(repo, "rev-parse", "HEAD").stdout.strip()
    
    # 2. 审计 PASS (裁决绑 main HEAD = commit_E)
    _write_gate_verdict(verdicts_root, "AIPOS-F70-E", commit_E, "PASS")
    
    # 3. finalize → 应成功 (无卡分支, 用 main HEAD)
    result = finalize_task(
        task_id="AIPOS-F70-E",
        actor="test_finalize",
        workspace_root=repo,
        governance_root=gov,
        dry_run=True,
        push=False,
        deploy=False,
    )
    
    # 验收: 无分支任务 finalize 应成功
    assert result["can_finalize"] is True, f"无分支任务 finalize 应成功: {result.get('message', result)}"
    
    # 验证操作日志: 提到"无卡分支"或"历史卡"
    ops_text = " ".join(result["operations"])
    assert "无卡分支" in ops_text or "历史卡" in ops_text or "非代码卡" in ops_text, \
        "无分支场景应有相应日志"
