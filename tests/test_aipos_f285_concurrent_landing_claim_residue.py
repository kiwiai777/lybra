"""AIPOS-F285 — 共享治理仓并发落账不互锁 + 认领超时半成品有出路。

靶场一律临时: 临时 git 仓(两项目共用, 2_projects/alpha + 2_projects/beta)+ 临时远端裸仓 + 临时产品仓; 不连生产门, 不碰真实治理仓 /
真实产品仓 / 真实工位。夹具子进程(index.lock 持有者)经 tools/aipos_cli/tests/fixture_procs.py 助手起停(整组回收)。

件① 并发落账: 项目 A(alpha)写真相命令落账留下暂存未提交(11-03 chris roadmap add-milestone 实撞形)时, 项目 B(beta)写后落账与 N6 卡范围
   落账照常提交推送, A 的暂存原文(git diff --cached)前后逐字相同; 两边路径重叠(本次路径内有暂存版 ≠ 工作区版)= 拒 + 出口, 照出口处理后
   重跑即落账; 整根模式根内暂存仍拒; 写索引遇 index.lock 竞争有界重试后照常落账。
件② 写真相命令自动落账失败(钩子拒)→ 本命令 add 的路径撤出暂存区、工作区保留、他人暂存不动、输出重试落账命令; 撤回本身失败 = 只撤本次
   路径的手工出口, 照做即复原。
件③ 认领半成品(门认领客户端超时 / 建树被杀, 卡仍 pending): 0 提交卡分支 + 干净树 = 幂等续用; 半建树(管理目录无 index、带锁)= 重建;
   只有分支 = 续用分支; 有提交 / 分支被他处占用 / 有未提交改动 = 拒 + 出口(队列与记录零变更); 清理命令 --task-id 预演 / 确认清掉残留
   (删树 + 删 0 提交分支), 批量清理同判。
件⑤ en 新文案零 CJK; 声明: 建树时限进 config.schema 且小于认领客户端等待上限; 判定 / 解析唯一实现。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402
from test_aipos_f184_queue_landing_supersede import EXEC, _card, rig  # noqa: E402,F401  (认领靶场 = F184 同一夹具, 禁第二套)
from tools.aipos_cli import governance_commit as gc  # noqa: E402
from tools.aipos_cli.tests import fixture_procs  # noqa: E402

ACTOR_A = "advisor-a.alpha.test"
ACTOR_B = "advisor.beta.test"
CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")


def _show(line: str) -> None:
    sys.__stdout__.write(line + "\n")
    sys.__stdout__.flush()


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd), check=check, capture_output=True,
                          text=True).stdout


# ---------------------------------------------------------------------------
# 件①② 靶场: 一个 git 仓两项目治理根(多项目共仓 = 产品常态)
# ---------------------------------------------------------------------------

@pytest.fixture
def shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("LYBRA_LANG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # 语言解析不读真实 home 配置
    repo = tmp_path / "shared"
    roots = {}
    for name in ("alpha", "beta"):
        root = repo / "2_projects" / name
        _write(root / "governance" / "roadmap.yaml", f"project: {name}\nmilestones: []\n")
        _write(root / "governance" / "decision_log" / ".gitkeep", "")
        _write(root / "governance" / "NOTES.md", "---\nstatus: active\n---\n# notes\n")
        roots[name] = root
    remote = init_governance_repo(repo)
    return {"repo": repo, "alpha": roots["alpha"], "beta": roots["beta"], "remote": remote}


def _a_leaves_staging(shared: dict) -> str:
    """项目 A 写真相命令(路线图 add-milestone)写完, 自动落账前被打断: 改动留在暂存区未提交(11-03 chris 现场形)。返回暂存原文。"""
    alpha, repo = shared["alpha"], shared["repo"]
    _write(alpha / "governance" / "roadmap.yaml", "project: alpha\nmilestones:\n- id: lantu-media-smbcloud\n")
    _write(alpha / "governance" / "decision_log" / "2026-11" / "2026-11-03-roadmap-add-milestone-auto-pointer.md",
           "---\nstatus: active\ndecision_type: auto_pointer\n---\n## pointer\n")
    _git(repo, "add", "--", "2_projects/alpha/governance")
    return _git(repo, "diff", "--cached")


def _land_b(shared: dict, rel: str = "governance/NOTES.md", text: str = "---\nstatus: active\n---\n# notes\n- beta 新增一行\n") -> dict:
    _write(shared["beta"] / rel, text)
    return gc.land_written_paths(shared["beta"], [rel], ACTOR_B, command="roadmap_writers", subcommand="add-milestone",
                                 target="m1", project="beta")


def _block_hook(repo: Path) -> Path:
    flag = repo / ".git" / "f285-block"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text('#!/bin/sh\nif [ -f "%s" ]; then echo "blocked by F285 fixture hook" >&2; exit 1; fi\nexit 0\n' % flag, encoding="utf-8")
    hook.chmod(0o755)
    flag.write_text("", encoding="utf-8")
    return flag


def test_item1_other_project_staging_kept_and_b_lands(shared: dict) -> None:
    repo = shared["repo"]
    cached_before = _a_leaves_staging(shared)
    names_before = _git(repo, "diff", "--cached", "--name-only")
    _show(f"[件①·A 暂存未提交 git diff --cached --name-only(前)]\n{names_before}")

    landing = _land_b(shared)
    _show(f"[件①·B 写后落账] {landing['line']}\n  pre_staged_kept={landing['pre_staged_kept']}")
    assert landing["landed"] and landing["committed"] and landing["pushed"], landing
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["2_projects/beta/governance/NOTES.md"]
    assert _git(repo, "rev-parse", "HEAD") == _git(shared["remote"], "rev-parse", "main")
    cached_after = _git(repo, "diff", "--cached")
    _show(f"[件①·A 暂存 git diff --cached --name-only(后)]\n{_git(repo, 'diff', '--cached', '--name-only')}")
    assert cached_after == cached_before, "A 的暂存须原样保留(原文逐字相同)"
    assert landing["pre_staged_kept"] == sorted(names_before.split())

    # N6 卡范围落账同一实现: B 的另一批路径(卡面 / 台账)照常, A 暂存仍原样
    _write(shared["beta"] / "task_cards" / "BETA-1" / "RETURN.md", "---\nstatus: active\n---\n# RETURN\n")
    n6 = gc.governance_commit(shared["beta"], None, ACTOR_B, paths=["task_cards/BETA-1"])
    _show(f"[件①·B 第二批落账] verdict={n6['verdict']} committed={n6['committed']} pushed={n6['pushed']} "
          f"kept_line={n6.get('pre_staged_kept_line')}")
    assert n6["verdict"] == "PASS" and n6["pushed"] is True
    assert _git(repo, "diff", "--cached") == cached_before


def test_item1_overlap_rejects_with_exit_then_lands(shared: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, beta = shared["repo"], shared["beta"]
    # 他人(同项目另一写命令)暂存了 roadmap.yaml 的一版, 随后本命令把工作区写成另一版 = 两边路径重叠
    _write(beta / "governance" / "roadmap.yaml", "project: beta\nmilestones:\n- id: other-writer\n")
    _git(repo, "add", "--", "2_projects/beta/governance/roadmap.yaml")
    staged = _git(repo, "diff", "--cached")
    head = _git(repo, "rev-parse", "HEAD")
    landing = _land_b(shared, "governance/roadmap.yaml", "project: beta\nmilestones:\n- id: other-writer\n- id: mine\n")
    _show(f"[件①·重叠拒] exit={landing['exit_code']}\n{landing['line']}")
    assert not landing["landed"] and landing["exit_code"] == 3
    assert "本次落账路径与他人暂存重叠" in landing["line"] and "restore --staged" in landing["line"]
    assert _git(repo, "diff", "--cached") == staged and _git(repo, "rev-parse", "HEAD") == head, "拒 = 现场不动"
    assert "index_restored" not in {k for k, v in landing.items() if v is not None}, "重叠在暂存前即拒, 无撤回"

    # 同一现场 en 用户语言: 拒因零 CJK
    monkeypatch.setenv("LYBRA_LANG", "en")
    en = gc.governance_commit(beta, None, ACTOR_B, paths=["governance/roadmap.yaml"])
    _show(f"[件①·重叠拒 en]\n{en['message']}")
    assert en["verdict"] == "BLOCK" and en["pre_staged_overlap"] == ["2_projects/beta/governance/roadmap.yaml"]
    assert not CJK.search(en["message"]), en["message"]
    monkeypatch.delenv("LYBRA_LANG")

    # 照出口: 确认是本命令的残留 → 只撤这些路径的暂存(工作区保留)→ 重跑同一落账即成
    exit_cmd = re.search(r"git -C (\S+) restore --staged -- (.+?), ", landing["line"])
    assert exit_cmd, landing["line"]
    subprocess.run(["git", "-C", exit_cmd.group(1), "restore", "--staged", "--", *exit_cmd.group(2).split()], check=True)
    again = gc.land_written_paths(beta, ["governance/roadmap.yaml"], ACTOR_B, command="roadmap_writers", subcommand="add-milestone",
                                  target="m1", project="beta")
    _show(f"[件①·照出口后重跑] {again['line']}")
    assert again["landed"] and "- id: mine" in _git(repo, "show", "HEAD:2_projects/beta/governance/roadmap.yaml")

    # 整根模式(无 --paths): 根内暂存仍拒; 根外(他项目)暂存不再挡
    _write(beta / "governance" / "NOTES.md", "---\nstatus: active\n---\n# notes\n- staged by someone\n")
    _git(repo, "add", "--", "2_projects/beta/governance/NOTES.md")
    whole = gc.governance_commit(beta, None, ACTOR_B, push=False)
    _show(f"[件①·整根模式根内暂存] verdict={whole['verdict']}\n{whole['message']}")
    assert whole["verdict"] == "BLOCK" and "治理根内有已暂存但未提交的文件" in whole["message"]


def test_item1_index_lock_contention_retried_then_lands(shared: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = shared["repo"]
    cached_before = _a_leaves_staging(shared)
    real = gc.concurrent_landing_declaration()
    monkeypatch.setattr(gc, "concurrent_landing_declaration",
                        lambda: {**real, "index_lock_retry": {**real["index_lock_retry"], "attempts": 60, "interval_ms": 100}})
    lock = repo / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    holder = [sys.executable, "-c", f"import os, time; time.sleep(0.8); os.remove({str(lock)!r})"]
    with fixture_procs.running(holder) as proc:
        landing = _land_b(shared)
        proc.wait(timeout=30)
    _show(f"[件①·index.lock 竞争重试] {landing['line']}")
    assert landing["landed"] and not lock.exists()
    assert _git(repo, "diff", "--cached") == cached_before


# ---------------------------------------------------------------------------
# 件② 自动落账失败不留暂存残留
# ---------------------------------------------------------------------------

def test_item2_landing_failure_unstages_own_paths_keeps_worktree(shared: dict) -> None:
    repo, beta = shared["repo"], shared["beta"]
    cached_before = _a_leaves_staging(shared)
    flag = _block_hook(repo)
    landing = _land_b(shared)
    _show(f"[件②·钩子拒] exit={landing['exit_code']} index_restored={landing['index_restored']}\n{landing['line']}")
    assert not landing["landed"] and landing["exit_code"] == 3 and landing["index_restored"] is True
    assert "暂存: 本命令 add 的路径已撤出暂存区(工作区改动保留); 重试落账: lybra governance-commit" in landing["line"]
    assert _git(repo, "diff", "--cached") == cached_before, "只撤本命令路径, 他人暂存不动"
    assert "- beta 新增一行" in (beta / "governance" / "NOTES.md").read_text(encoding="utf-8"), "工作区保留"
    assert _git(repo, "status", "--porcelain", "--", "2_projects/beta").startswith(" M "), "本命令路径回到未暂存"
    flag.unlink()
    remedy = landing["remedy"]
    from tools.aipos_cli.aipos_cli import main
    import shlex

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(shlex.split(remedy)[1:])
    _show(f"[件②·照重试落账命令] $ {remedy}\nrc={rc}")
    assert rc == 0 and _git(repo, "rev-parse", "HEAD") == _git(shared["remote"], "rev-parse", "main")
    assert _git(repo, "diff", "--cached") == cached_before


def test_item2_unstage_failure_gives_manual_exit(shared: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = shared["repo"]
    cached_before = _a_leaves_staging(shared)
    _block_hook(repo)
    real = gc._git_index_write

    def reset_fails(args, cwd, **kw):
        if args[:1] == ["reset"]:
            raise subprocess.CalledProcessError(128, ["git", *args], stderr="fatal: Unable to create '.git/index.lock': File exists.")
        return real(args, cwd, **kw)

    monkeypatch.setattr(gc, "_git_index_write", reset_fails)
    landing = _land_b(shared)
    _show(f"[件②·撤回失败] index_restored={landing['index_restored']}\n{landing['line']}\n  unstage_command={landing['unstage_command']}")
    assert landing["index_restored"] is False and "未能撤出暂存区" in landing["line"]
    assert "2_projects/beta/governance/NOTES.md" in _git(repo, "diff", "--cached", "--name-only")
    monkeypatch.setattr(gc, "_git_index_write", real)
    subprocess.run(["bash", "-c", landing["unstage_command"]], check=True)
    assert _git(repo, "diff", "--cached") == cached_before, "照手工出口只撤本次路径, 他人暂存不动"


# ---------------------------------------------------------------------------
# 件③ 认领半成品(F184 同一靶场; 认领 = 门认领核心 mutate_queue_task claim)
# ---------------------------------------------------------------------------

TASK3 = "PROJ-285"


def _claim(gov: Path, task_id: str = TASK3) -> dict:
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    return mutate_queue_task(gov, "claim", task_id=task_id, actor=EXEC, dry_run=False, profiles=load_agent_profiles(gov), with_records=True)


def _loc(gov: Path, task_id: str = TASK3) -> tuple[Path, Path]:
    from tools.aipos_cli.next_resolver import card_worktree_location
    from tools.aipos_cli.task_loader import find_task_card
    from tools.aipos_cli.frontmatter import require_frontmatter

    path, _state = find_task_card(gov, task_id)
    return card_worktree_location(gov, task_id, require_frontmatter(path)[0])


def _gate_side_residue(gov: Path, *, shape: str, task_id: str = TASK3) -> tuple[Path, Path]:
    """门侧已建出的半成品(卡仍 pending, 无认领记录): branch = 只有 0 提交卡分支; clean = 分支 + 建完的树; incomplete = 检出被杀的半建树
    (管理目录无 index、带「初始化」锁 = LANTU-BC3 现场形)。"""
    code_repo, worktree = _loc(gov, task_id)
    _git(code_repo, "branch", f"card/{task_id}", "main")
    if shape != "branch":
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _git(code_repo, "worktree", "add", str(worktree), f"card/{task_id}")
    if shape == "incomplete":
        admin = Path(_git(worktree, "rev-parse", "--git-dir").strip())
        (admin / "index").unlink()
        (admin / "locked").write_text("初始化", encoding="utf-8")
    return code_repo, worktree


def _pending(gov: Path, task_id: str = TASK3) -> bool:
    return (gov / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md").is_file()


def _claim_records(gov: Path, task_id: str = TASK3) -> list[Path]:
    return sorted((gov / "5_tasks" / "records" / "claims" / task_id).glob("claim_*.md"))


@pytest.mark.parametrize("shape, kind, needle", [
    ("clean", "reuse", "续用此前认领尝试留下的卡工作树"),
    ("incomplete", "rebuild", "未建完(检出被中断), 已删掉半建树并在卡分支"),
    ("branch", "reuse", "续用此前认领尝试留下的卡分支"),
])
def test_item3_reclaim_after_timeout_is_idempotent(rig: dict, shape: str, kind: str, needle: str) -> None:
    gov = rig["gov"]
    _card(gov, TASK3, "pending")
    code_repo, worktree = _gate_side_residue(gov, shape=shape)
    before = _git(code_repo, "worktree", "list", "--porcelain")
    _show(f"[件③·{shape} 认领前 git worktree list]\n{before}")
    res = _claim(gov)
    _show(f"[件③·{shape} 重跑认领] verdict={res['verdict']} worktree_claim_residue={res.get('worktree_claim_residue')}\n"
          f"  {res.get('worktree_line')}\n  blocking={res.get('blocking_reasons')}")
    assert res["verdict"] != "BLOCK" and res.get("moved"), res.get("blocking_reasons")
    assert res["worktree_claim_residue"] == kind and needle in res["worktree_line"]
    assert not _pending(gov) and _claim_records(gov)
    admin = Path(_git(worktree, "rev-parse", "--git-dir").strip())
    assert (admin / "index").is_file() and not (admin / "locked").exists(), "续用 / 重建后是建完、无锁的卡分支工作树"
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == f"card/{TASK3}"
    assert _git(worktree, "status", "--porcelain") == ""


@pytest.mark.parametrize("setup, needle", [
    ("commits", "已有 1 个基线 main 之外的提交"),
    ("occupied", "已检出在"),
    ("dirty", "个未提交改动, 不续用"),
])
def test_item3_reclaim_refused_with_exit_queue_unchanged(rig: dict, setup: str, needle: str) -> None:
    gov = rig["gov"]
    _card(gov, TASK3, "pending")
    code_repo, worktree = _loc(gov)
    if setup == "commits":
        _gate_side_residue(gov, shape="clean")
        _write(worktree / "someone.txt", "someone else's work\n")
        _git(worktree, "add", "someone.txt")
        _git(worktree, "-c", "user.name=o", "-c", "user.email=o@o", "commit", "-q", "-m", "other's commit")
    elif setup == "occupied":
        _git(code_repo, "branch", f"card/{TASK3}", "main")
        _git(code_repo, "worktree", "add", str(code_repo.parent / "elsewhere"), f"card/{TASK3}")
    else:
        _gate_side_residue(gov, shape="clean")
        _write(worktree / "wip.txt", "uncommitted\n")
    res = _claim(gov)
    reason = " ".join(res.get("blocking_reasons") or [])
    _show(f"[件③·拒 {setup}] verdict={res['verdict']}\n  {reason}")
    assert res["verdict"] == "BLOCK" and "WORKTREE_CREATE_FAILED" in reason and needle in reason and "出口" in reason
    assert _pending(gov) and not _claim_records(gov), "拒 = 队列与记录零变更"


def _cleanup(gov: Path, *argv: str) -> tuple[int, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(["--workspace-root", str(gov), "project", "cleanup-worktrees", *argv])
    return rc, out.getvalue() + err.getvalue()


def test_item3_cleanup_preview_then_confirm_removes_only_this_cards_residue(rig: dict) -> None:
    gov = rig["gov"]
    _card(gov, TASK3, "pending")
    code_repo, worktree = _gate_side_residue(gov, shape="incomplete")
    _card(gov, "PROJ-OTHER", "pending")
    _other_repo, other_tree = _gate_side_residue(gov, shape="clean", task_id="PROJ-OTHER")
    listing = _git(code_repo, "worktree", "list", "--porcelain")
    branches = _git(code_repo, "branch", "--list", "card/*")

    rc, out = _cleanup(gov, "--task-id", TASK3)
    _show(f"[件③·清理预演 --task-id] rc={rc}\n{out}")
    assert rc == 0 and "认领残留(可清)" in out and "工作树未建完" in out and f"--task-id {TASK3} --confirm" in out
    assert _git(code_repo, "worktree", "list", "--porcelain") == listing and _git(code_repo, "branch", "--list", "card/*") == branches, "预演零写入"

    rc, out = _cleanup(gov, "--task-id", TASK3, "--confirm")
    _show(f"[件③·清理确认 --task-id] rc={rc}\n{out}")
    assert rc == 0 and "已清认领残留" in out
    assert not worktree.exists() and f"card/{TASK3}" not in _git(code_repo, "branch", "--list", "card/*")
    assert other_tree.is_dir() and "card/PROJ-OTHER" in _git(code_repo, "branch", "--list", "card/*"), "只清本卡残留"

    # 清后重跑认领 = 正常新建(从 main)
    res = _claim(gov)
    assert res["verdict"] != "BLOCK" and not res.get("worktree_claim_residue"), res.get("blocking_reasons")


def test_item3_withdrawn_card_residue_listed_by_batch_cleanup(rig: dict) -> None:
    """LANTU-BC3 现场形: 撤卡后半建树(无 index、带锁)被旧判据判「脏不碰」; 现批量清理判为认领残留, 确认即清。"""
    gov = rig["gov"]
    _card(gov, TASK3, "withdrawn")
    code_repo, worktree = _gate_side_residue(gov, shape="incomplete")
    rc, out = _cleanup(gov)
    _show(f"[件③·批量预演] rc={rc}\n{out}")
    assert rc == 0 and "认领残留(可清)" in out and "脏(不碰)" not in out
    rc, out = _cleanup(gov, "--confirm")
    _show(f"[件③·批量确认] rc={rc}\n{out}")
    assert rc == 0 and not worktree.exists() and f"card/{TASK3}" not in _git(code_repo, "branch", "--list")


def test_item3_claimed_before_or_with_commits_is_not_residue(rig: dict) -> None:
    """有门生认领记录(返工 / 重开)= 原路径, 不判残留; 有提交的分支 = 不可清(走原判据)。"""
    from tools.aipos_cli.worktree_cleanup import judge_claim_residue

    gov = rig["gov"]
    _card(gov, TASK3, "pending")
    code_repo, worktree = _gate_side_residue(gov, shape="clean")
    _write(gov / "5_tasks" / "records" / "claims" / TASK3 / f"claim_{TASK3}_x_{EXEC}.md",
           "---\nrecord_type: claim_record\nclaim_id: claim_x\nclaimed_at: '2026-11-01T00:00:00Z'\n---\n# claim\n")
    judged = judge_claim_residue(gov, TASK3, code_repo=code_repo, worktree=worktree, branch=f"card/{TASK3}", base_ref="main")
    assert judged["kind"] == "none"
    rc, out = _cleanup(gov, "--task-id", TASK3)
    _show(f"[件③·已认领过 = 不判残留] rc={rc}\n{out}")
    assert "认领残留" not in out and "在途(留)" in out


# ---------------------------------------------------------------------------
# 件⑤ 双语 / 声明 / 单一实现
# ---------------------------------------------------------------------------

def test_item5_en_texts_zero_cjk_and_bilingual_blocks() -> None:
    from tools.aipos_cli.i18n import placeholder_problems
    from tools.schema_loader import code_repo_schema_root, load_schema

    verbs = load_schema("verbs", code_repo_schema_root())["verbs"]
    blocks = {"concurrent_landing.texts": verbs["lybra_governance_commit"]["concurrent_landing"]["texts"],
              "claim_residue_texts": verbs["lybra_project_cleanup_worktrees"]["claim_residue_texts"]}
    for name, block in blocks.items():
        assert sorted(block) == ["en", "zh"] and set(block["en"]) == set(block["zh"]), name
        assert placeholder_problems(block) == [], name
        bad = {k: v for k, v in block["en"].items() if CJK.search(v)}
        _show(f"[件⑤·{name}] en {len(block['en'])} 条, 含 CJK {len(bad)} 条")
        assert not bad, bad


def test_item5_declarations_and_single_implementations() -> None:
    from tools.aipos_cli.next_resolver import worktree_add_timeout_seconds
    from tools.aipos_cli.two_phase_shell_factory import client_timeout_seconds

    add = worktree_add_timeout_seconds()
    claim_wait = min(client_timeout_seconds("lybra_queue_claim_dry_run"), client_timeout_seconds("lybra_queue_claim_confirm"))
    _show(f"[件⑤·时限] 建树 {add}s < 认领客户端等待 {claim_wait:g}s(verbs.schema stage_contract.client_timeout_ms, 唯一声明处)")
    assert add == 30 and add < claim_wait
    nr = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    ensure = nr[nr.index("def _ensure_worktree("):nr.index("def worktree_add_timeout_seconds(")]
    assert "timeout=30" not in ensure and ensure.count("timeout=add_timeout") == 2
    assert "judge_claim_residue(" in ensure
    sources = {p.relative_to(REPO_ROOT).as_posix(): p.read_text(encoding="utf-8") for p in (REPO_ROOT / "tools").rglob("*.py")
               if "/tests/" not in p.as_posix()}
    defs = [k for k, v in sources.items() if "def judge_claim_residue(" in v]
    porcelain = [k for k, v in sources.items() if '"worktree", "list", "--porcelain"' in v]
    _show(f"[件⑤·单一实现] judge_claim_residue 定义于 {defs}; worktree list --porcelain 解析于 {porcelain}")
    assert defs == ["tools/aipos_cli/worktree_cleanup.py"]
    assert sources["tools/aipos_cli/worktree_cleanup.py"].count('"worktree", "list", "--porcelain"') == 1, "解析唯一 worktree_entries"
    gc_src = sources["tools/aipos_cli/governance_commit.py"]
    assert "except Exception" not in gc_src[gc_src.index("def concurrent_landing_declaration("):]
