"""AIPOS-F193 — 落账路径清单容忍「从未入库且已不存在」的旧路径(chris 理想 HBLX-20 撤换落账失败实撞)。

形态(人肉期接入项目): 卡文件发布 / 认领后直到 N6 结案才入库 → 原卡 claimed/<id>.md 从未被 git 跟踪; 队列写命令把它挪走后,
落账清单仍列「迁移前路径」, 旧实现对它执行 git add → `fatal: pathspec ... did not match any files`, 整次落账失败。

靶场 = F184 同一夹具(临时治理仓 + 临时远端裸仓 + 临时产品仓; 不连生产门, 不碰真实治理仓 / 真实产品仓)。落账走真实产品实现
(governance_commit 唯一落账实现; CLI main 本进程执行)。

件① 落账容忍: 工作树不存在且 git 未跟踪(索引与 HEAD 均无)的条目从暂存与提交 pathspec 剔除, 结果列出「已跳过 N 个从未入库且已不存在
   的路径」; 剔除后为空 = no-op PASS; 已跟踪且被删除的照常作删除提交。
件② 补账可重跑: 失败时输出的补账命令(governance-commit --paths 同一清单)修复后直接成功。
件③ 回归覆盖: 未入库卡 supersede(含 --carry-branch)/ withdraw / block / reopen 落账成功且提交内容正确; 已入库卡删除迁移不回归。
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f184_queue_landing_supersede import (  # noqa: E402,F401  (rig = F184 同一夹具, 禁第二套靶场)
    DRIVER, EXEC, GOV_REL, _card, _cli, _commit_all, _git, _head_files, _session, rig,
)
from test_aipos_f186_supersede_carry_branch import _claim, _commit_in  # noqa: E402
from tools.aipos_cli import governance_commit as gc  # noqa: E402
from tools.aipos_cli.clock import iso_z  # noqa: E402

SKIP_MARK = "已跳过"


def _show(line: str) -> None:
    sys.__stdout__.write(line + "\n")
    sys.__stdout__.flush()


def _tracked(repo: Path, rel: str) -> bool:
    return bool(_git(repo, "ls-files", "--", f"{GOV_REL}/{rel}"))


def _landing_line(out: str, err: str) -> str:
    return next((ln for ln in (out + "\n" + err).splitlines() if ln.startswith("落账") or "落账未完成" in ln), "")


# ---------------------------------------------------------------------------
# 件① 落账容忍(唯一落账实现 governance_commit)
# ---------------------------------------------------------------------------

def test_item1_mixed_paths_skip_vanished_untracked_keep_tracked_deletion(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    tracked = _card(gov, "PROJ-T1", "pending")
    _commit_all(repo, "publish PROJ-T1")
    tracked.unlink()  # 已跟踪且被删除 → 照常作删除提交
    _card(gov, "PROJ-T1", "withdrawn")
    paths = ["5_tasks/queue/pending/proj-t1.md", "5_tasks/queue/withdrawn/proj-t1.md",
             "5_tasks/queue/claimed/proj-u1.md"]  # 最后一条: 从未入库且已不存在
    dry = gc.governance_commit(gov, None, DRIVER, dry_run=True, paths=paths)
    _show(f"[件①] dry-run verdict={dry['verdict']}\n{dry['message']}")
    assert dry["verdict"] == "PASS" and dry["skipped_vanished_untracked"] == ["5_tasks/queue/claimed/proj-u1.md"]
    assert f"{SKIP_MARK} 1 个从未入库且已不存在的路径" in dry["message"]
    result = gc.governance_commit(gov, None, DRIVER, paths=paths)
    _show(f"[件①] 落账 verdict={result['verdict']} committed={result['committed']} pushed={result['pushed']}")
    _show("[件①] " + "\n[件①] ".join(op for op in result["operations"] if SKIP_MARK in op or "Staged" in op))
    stat = _git(repo, "show", "--stat", "--format=%s", "HEAD")
    _show(f"[件①] git show --stat HEAD:\n{stat}")
    assert result["verdict"] == "PASS" and result["committed"] and result["pushed"], result["message"]
    assert _head_files(repo) == {f"{GOV_REL}/5_tasks/queue/pending/proj-t1.md", f"{GOV_REL}/5_tasks/queue/withdrawn/proj-t1.md"}
    assert not _tracked(repo, "5_tasks/queue/pending/proj-t1.md")  # 删除已提交
    assert result["skipped_vanished_untracked"] == ["5_tasks/queue/claimed/proj-u1.md"]
    assert any(op.startswith(f"{SKIP_MARK} 1 个从未入库且已不存在的路径") for op in result["operations"])


def test_item1_all_vanished_untracked_is_noop_pass(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    head = _git(repo, "rev-parse", "HEAD")
    paths = ["5_tasks/queue/claimed/proj-u2.md", "5_tasks/queue/blocked/proj-u2.md"]
    result = gc.governance_commit(gov, None, DRIVER, paths=paths)
    _show(f"[件①] 全部剔除: verdict={result['verdict']} committed={result['committed']} message={result['message']}")
    assert result["verdict"] == "PASS" and not result["committed"] and _git(repo, "rev-parse", "HEAD") == head
    assert result["skipped_vanished_untracked"] == sorted(paths)
    landing = gc.land_written_paths(gov, paths, DRIVER, command="queue_writers", subcommand="withdraw", task_id="PROJ-U2")
    _show(f"[件①] 写后落账行(全部剔除): {landing['line']}")
    assert landing["landed"] and f"{SKIP_MARK} 2 个从未入库且已不存在的路径" in landing["line"]


def test_item1_staged_deletion_not_re_added(rig: dict) -> None:
    """已跟踪、已 git rm(索引与工作树均无, HEAD 有)= 已跟踪删除: 不再 git add(会同样报 pathspec), 照常作删除提交。"""
    repo, gov = rig["repo"], rig["gov"]
    _card(gov, "PROJ-T3", "pending")
    _commit_all(repo, "publish PROJ-T3")
    _git(repo, "rm", "-q", "--", f"{GOV_REL}/5_tasks/queue/pending/proj-t3.md")
    _card(gov, "PROJ-T3", "withdrawn")
    paths = ["5_tasks/queue/pending/proj-t3.md", "5_tasks/queue/withdrawn/proj-t3.md"]
    result = gc.governance_commit(gov, None, DRIVER, paths=paths)
    _show(f"[件①] 预暂存删除: verdict={result['verdict']} files={sorted(_head_files(repo))}")
    assert result["verdict"] == "PASS" and result["committed"], result["message"]
    assert _head_files(repo) == {f"{GOV_REL}/{p}" for p in paths} and not result.get("skipped_vanished_untracked")


# ---------------------------------------------------------------------------
# 件③ 队列写族: 未入库卡(chris 形)迁移落账
# ---------------------------------------------------------------------------

def test_item3_supersede_carry_branch_on_never_committed_card(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    orig, new = "PROJ-20", "PROJ-20B"
    _card(gov, orig, "pending")  # 发布后未入库(人肉期)
    claimed = _claim(gov, orig)
    assert claimed.get("moved"), claimed
    tip = _commit_in(Path(claimed["worktree_path"]), "app/feature.py", "VALUE = 20\n", f"feat({orig}): 原卡已做的工作")
    _card(gov, new, "pending")  # 新卡同样未入库
    assert not _tracked(repo, f"5_tasks/queue/claimed/{orig.lower()}.md")
    head = _git(repo, "rev-parse", "HEAD")
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", orig, "--by", new, "--actor", DRIVER, "--owner-words",
                        "验证条款自相矛盾, 出更正卡承继原卡分支", "--carry-branch", "--confirm")
    _show(f"[件③] supersede --carry-branch(原卡从未入库) rc={rc}\n{out.strip()}\n{err.strip()}")
    assert rc == 0, (out, err)
    assert _git(repo, "rev-parse", "HEAD") != head and _git(repo, "log", "-1", "--format=%s") == f"chore(governance): 队列写落账 supersede {orig}"
    files = sorted(_head_files(repo))
    _show(f"[件③] git show --stat HEAD:\n{_git(repo, 'show', '--stat', '--format=%s', 'HEAD')}")
    assert f"{GOV_REL}/5_tasks/queue/withdrawn/{orig.lower()}.md" in files
    assert f"{GOV_REL}/5_tasks/queue/pending/{new.lower()}.md" in files
    assert any("/records/owner_decisions/" in f for f in files) and any(f"/records/amendments/{new}/" in f for f in files), files
    assert f"{GOV_REL}/5_tasks/queue/claimed/{orig.lower()}.md" not in files
    assert f"{SKIP_MARK} " in _landing_line(out, err) and "从未入库且已不存在" in _landing_line(out, err)
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")
    assert not _git(repo, "status", "--porcelain", "--", f"{GOV_REL}/5_tasks/queue/withdrawn", f"{GOV_REL}/5_tasks/queue/pending")
    assert tip  # 承继 tip 存在(卡面断言由 F186 覆盖)


def test_item3_withdraw_never_committed_pending_card(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _card(gov, "PROJ-21", "pending")
    rc, out, err = _cli(gov, "queue", "withdraw", "--task-id", "PROJ-21", "--actor", DRIVER, "--reason", "方向变了")
    _show(f"[件③] withdraw(从未入库) rc={rc} 落账行: {_landing_line(out, err)}")
    assert rc == 0, (out, err)
    assert _head_files(repo) == {f"{GOV_REL}/5_tasks/queue/withdrawn/proj-21.md"}
    assert f"{SKIP_MARK} 1 个从未入库且已不存在的路径" in _landing_line(out, err)


def test_item3_block_then_reopen_never_committed_claimed_card(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    sid = "session_PROJ-22_x"
    _card(gov, "PROJ-22", "claimed", extra={"claimed_by": EXEC, "claimed_at": iso_z(), "claim_id": "claim_PROJ-22_x",
                                           "active_session_id": sid})
    _session(gov, "PROJ-22", sid)
    rc, out, err = _cli(gov, "queue", "block", "--task-id", "PROJ-22", "--actor", EXEC, "--reason", "等 Owner", "--with-records")
    files = sorted(_head_files(repo))
    _show(f"[件③] block(从未入库) rc={rc} 落账行: {_landing_line(out, err)}; 文件={files}")
    assert rc == 0, (out, err)
    assert f"{GOV_REL}/5_tasks/queue/blocked/proj-22.md" in files and f"{GOV_REL}/5_tasks/queue/claimed/proj-22.md" not in files
    assert any("/records/sessions/" in f for f in files), files
    # reopen: blocked/proj-22.md 此时已入库(block 落账提交) → 已跟踪删除照常提交
    rc, out, err = _cli(gov, "queue", "reopen", "--task-id", "PROJ-22", "--actor", EXEC, "--reason", "Owner 已定")
    files = sorted(_head_files(repo))
    _show(f"[件③] reopen rc={rc} 落账行: {_landing_line(out, err)}; 文件={files}")
    assert rc == 0, (out, err)
    assert {f"{GOV_REL}/5_tasks/queue/blocked/proj-22.md", f"{GOV_REL}/5_tasks/queue/pending/proj-22.md"} <= set(files)
    assert not _tracked(repo, "5_tasks/queue/blocked/proj-22.md")
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")


# ---------------------------------------------------------------------------
# 件② 补账可重跑: 失败后现场(写入已生效、未入库)用补账命令直接重跑
# ---------------------------------------------------------------------------

def test_item2_remedy_command_rerun_succeeds(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    src = _card(gov, "PROJ-23", "claimed")  # 从未入库
    dst = gov / "5_tasks" / "queue" / "withdrawn" / "proj-23.md"
    src.rename(dst)  # 写命令已生效、落账未完成的现场(chris HBLX-20 形)
    paths = ["5_tasks/queue/claimed/proj-23.md", "5_tasks/queue/withdrawn/proj-23.md"]
    remedy = gc.governance_commit_paths_command(paths, DRIVER, gov)
    argv = shlex.split(remedy)
    cli_head = shlex.split(gc.governance_commit_cli())
    assert argv[:len(cli_head)] == cli_head
    rc, out, err = _cli(gov, *argv[len(cli_head) - 1:])
    _show(f"[件②] 补账命令: {remedy}\n[件②] rc={rc}\n{out.strip()}")
    assert rc == 0, (out, err)
    assert _head_files(repo) == {f"{GOV_REL}/5_tasks/queue/withdrawn/proj-23.md"}
    assert f"{SKIP_MARK} 1 个从未入库且已不存在的路径" in out
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")
