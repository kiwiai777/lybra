"""AIPOS-F79C: governance_commit push 假阴性热修 — 双向 rev-list 判据 / 临时 linked worktree 整合 / pushed=False 出声 / 目录 pathspec 内删除

靶场分根(硬约束③): tmp 里 bare 远端 + clone A(多项目布局: 2_projects/lybra 为治理根, 2_projects/other
有未提交修改与未跟踪材料)+ clone B(模拟他人先推)。

三态夹具(先红后绿: 本文件对 main 基座跑 = 红, 对 card/AIPOS-F79C 跑 = 绿):
  (a) 远端未前进 + A 他项目脏 → 直接 fast-forward push 成功, 他项目文件原样
  (b) B 先推使远端前进 + A 他项目脏 → 临时 worktree cherry-pick + push 成功, A 本地分支 ff, 他项目文件原样, stash 为空
  (c) 远端前进且同文件冲突 → BLOCK 列冲突文件, 临时 worktree 已清理, 本地 commit 保留
件④: 目录 pathspec 含 1 删 1 改 1 新增(删+新增内容相同 = 队列搬动, 复现 --name-only 折叠重命名的病根)
      → 提交后 git show --name-only 三项齐; mismatch 场景暂存区被清空且 --paths 内预暂存精确还原
件③: pushed=False 一律非零退出码; 文本模式首行 PUSH NOT DONE: <原因>; --json operations 末条 = push 结果
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.aipos_cli.governance_commit import governance_commit
from tools.schema_constants import Verdict

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTOR = "advisor.test.f79c"
GOV_REL = "2_projects/lybra"
OTHER_REL = "2_projects/other"
PUSH_NOT_DONE = "PUSH NOT DONE: "
TMP_PREFIX = "lybra-governance-commit-"


# AIPOS-F79D 件①: governance/*.md 须带 config.schema file_declarations.governance_doc 声明的 frontmatter(四检现在在 governance-commit 内跑)
LEDGER_BASE = "---\nstatus: active\n---\n# ledger\n- row 1\n"

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout


def porcelain(repo: Path, *pathspec: str) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *(pathspec or (".",)))


def head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def remote_tip(remote: Path) -> str:
    return _git(remote, "rev-parse", "refs/heads/main").strip()


def show_names(repo: Path, rev: str = "HEAD") -> set[str]:
    return set(_git(repo, "show", "--name-only", "--no-renames", "--format=", rev).split("\n")) - {""}


def other_project_fingerprint(repo: Path) -> str:
    """他项目「未提交文件原样」判据: status --porcelain 限 other(验收④同款 md5sum 口径)
    + 其中每个未提交/未跟踪文件内容的 sha256(逐字节)。远端提交带来的已跟踪干净文件不在此列(那是 ff 本身)。"""
    listing = porcelain(repo, OTHER_REL)
    h = hashlib.sha256(listing.encode("utf-8"))
    for line in listing.split("\n"):
        if not line:
            continue
        f = repo / line[3:]
        h.update(line[3:].encode("utf-8"))
        h.update(f.read_bytes() if f.is_file() else b"<missing>")
    return h.hexdigest()


def leftover_tmp_worktrees() -> list[str]:
    return sorted(p for p in os.listdir(tempfile.gettempdir()) if p.startswith(TMP_PREFIX))


def worktree_count(repo: Path) -> int:
    return len([line for line in _git(repo, "worktree", "list", "--porcelain").split("\n") if line.startswith("worktree ")])


@pytest.fixture
def rig(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)

    a = tmp_path / "A"
    a.mkdir()
    _git(a, "init", "-q", "-b", "main")
    _git(a, "config", "user.name", "Clone A")
    _git(a, "config", "user.email", "a@example.com")
    _git(a, "remote", "add", "origin", str(remote))

    gov = a / GOV_REL
    other = a / OTHER_REL
    for d in ("governance/decision_log", "5_tasks/queue/pending", "5_tasks/queue/claimed", "task_cards", "stage_archive", "notes"):
        (gov / d).mkdir(parents=True)
    for d in ("governance/decision_log", "5_tasks/queue/claimed", "task_cards", "stage_archive"):
        (gov / d / ".gitkeep").write_text("", encoding="utf-8")
    (gov / "governance" / "LEDGER.md").write_text(LEDGER_BASE, encoding="utf-8")  # AIPOS-F79D: 治理文档须带声明 frontmatter(B② 与 hook 同判据)
    (gov / "5_tasks" / "LEDGER.md").write_text("# task ledger\n- t1\n", encoding="utf-8")
    (gov / "5_tasks" / "queue" / "pending" / "card-1.md").write_text("---\ntask_id: X-1\n---\n# card 1\n", encoding="utf-8")
    (gov / "notes" / "a.md").write_text("# note a\n", encoding="utf-8")
    other.mkdir(parents=True)
    (other / "README.md").write_text("# other project\n", encoding="utf-8")
    (other / "plan.md").write_text("plan v1\n", encoding="utf-8")
    _git(a, "add", "--", "2_projects")
    _git(a, "commit", "-q", "-m", "rig: multi-project layout")
    _git(a, "push", "-q", "-u", "origin", "main")

    # A 的他项目脏: 已跟踪修改 + 未跟踪材料(F69-R2 旧检查正是被这两样卡死)
    (other / "plan.md").write_text("plan v2 (other project, uncommitted)\n", encoding="utf-8")
    (other / "untracked.md").write_text("other project untracked material\n", encoding="utf-8")

    def make_b() -> Path:
        b = tmp_path / "B"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(b)], check=True)
        _git(b, "config", "user.name", "Clone B")
        _git(b, "config", "user.email", "b@example.com")
        return b

    def mock_resolve_governance_path(key, governance_root, repo_root=None):
        path_map = {
            "task_cards": governance_root / "task_cards",
            "decision_log_dir": governance_root / "governance" / "decision_log",
            "stage_archive": governance_root / "stage_archive",
        }
        return path_map.get(key, governance_root / key)

    monkeypatch.setattr("tools.schema_loader.resolve_governance_path", mock_resolve_governance_path)
    return {"a": a, "gov": gov, "other": other, "remote": remote, "make_b": make_b, "schema_root": tmp_path}


def run(rig, **kw):
    kw.setdefault("task_id", None)
    kw.setdefault("actor", ACTOR)
    kw.setdefault("repo_root", rig["schema_root"])
    kw.setdefault("push", True)
    return governance_commit(rig["gov"], **kw)


def touch_ledger(rig, line: str = "- row 2\n") -> None:
    p = rig["gov"] / "governance" / "LEDGER.md"
    p.write_text(p.read_text(encoding="utf-8") + line, encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) 远端未前进 + 他项目脏 → 直接 fast-forward push, 不 rebase, 不做脏树检查, 他项目原样
# ---------------------------------------------------------------------------
def test_state_a_remote_not_advanced_pushes_fast_forward_with_other_project_dirty(rig):
    a, remote = rig["a"], rig["remote"]
    touch_ledger(rig)
    other_before = other_project_fingerprint(a)
    tmp_before = leftover_tmp_worktrees()

    result = run(rig, paths=["governance/LEDGER.md"])

    assert result["verdict"] == Verdict.PASS, result["message"]
    assert result["committed"] is True and result["pushed"] is True
    assert result["commit_hash"] == head(a) == remote_tip(remote)
    ops = result["operations"]
    joined = " ".join(ops)
    assert "remote-only=0 local-only=1" in joined, joined
    assert "fast-forward" in joined and "Rebased" not in joined and "Temp worktree" not in joined
    assert "out-of-scope" not in joined and "Blocked" not in joined
    assert ops[-1].startswith("Push result: pushed"), ops[-1]
    assert result["push_result"] == {"pushed": True, "reason": None}
    assert show_names(a) == {f"{GOV_REL}/governance/LEDGER.md"}
    # 他项目文件原样(已跟踪修改 + 未跟踪材料逐字节不变), 无 stash, 无临时 worktree 残留
    assert other_project_fingerprint(a) == other_before
    assert f" M {OTHER_REL}/plan.md" in porcelain(a) and f"?? {OTHER_REL}/untracked.md" in porcelain(a)
    assert _git(a, "stash", "list") == ""
    assert worktree_count(a) == 1 and leftover_tmp_worktrees() == tmp_before


# ---------------------------------------------------------------------------
# (b) B 先推使远端前进 + 他项目脏 → 临时 worktree cherry-pick + push, 本地分支 ff, 他项目原样, stash 为空
# ---------------------------------------------------------------------------
def test_state_b_remote_advanced_integrates_in_temp_worktree_and_ffs_local_branch(rig):
    a, remote, other = rig["a"], rig["remote"], rig["other"]
    b = rig["make_b"]()
    (b / OTHER_REL / "remote-advanced.md").write_text("B moved the remote on\n", encoding="utf-8")
    _git(b, "add", "--", f"{OTHER_REL}/remote-advanced.md")
    _git(b, "commit", "-q", "-m", "B: other project advanced remote")
    _git(b, "push", "-q")
    b_tip = remote_tip(remote)

    touch_ledger(rig)
    other_before = other_project_fingerprint(a)
    tmp_before = leftover_tmp_worktrees()
    local_before = head(a)

    result = run(rig, paths=["governance/LEDGER.md"])

    assert result["verdict"] == Verdict.PASS, result["message"]
    assert result["committed"] is True and result["pushed"] is True
    ops = result["operations"]
    joined = " ".join(ops)
    assert "remote-only=1 local-only=1" in joined, joined
    assert "Remote has advanced" in joined and "Temp worktree added" in joined
    assert "Cherry-picked 1 commit(s)" in joined and "Pushed to remote from temp worktree" in joined
    assert "Local branch fast-forwarded" in joined and "Temp worktree cleaned: True" in joined
    assert "Rebased" not in joined and "Blocked" not in joined
    assert ops[-1].startswith("Push result: pushed"), ops[-1]
    # 远端 = B 的提交之上一条 cherry-pick 的本卡提交; A 本地分支已跟到远端
    tip = remote_tip(remote)
    assert result["commit_hash"] == tip == head(a) == _git(a, "rev-parse", "origin/main").strip()
    assert _git(a, "rev-parse", f"{tip}^").strip() == b_tip
    assert show_names(a, tip) == {f"{GOV_REL}/governance/LEDGER.md"}
    assert local_before != tip and _git(a, "rev-list", "--count", "origin/main..HEAD").strip() == "0"
    # 共享检出: 远端带来的文件已到位(真 ff), 他项目未提交文件逐字节原样, 无 stash, 无临时 worktree 残留
    assert (other / "remote-advanced.md").read_text(encoding="utf-8") == "B moved the remote on\n"
    assert other_project_fingerprint(a) == other_before
    assert (other / "plan.md").read_text(encoding="utf-8") == "plan v2 (other project, uncommitted)\n"
    assert f" M {OTHER_REL}/plan.md" in porcelain(a) and f"?? {OTHER_REL}/untracked.md" in porcelain(a)
    assert _git(a, "stash", "list") == ""
    assert worktree_count(a) == 1 and leftover_tmp_worktrees() == tmp_before
    assert _git(a, "diff", "--cached", "--name-only") == ""


# ---------------------------------------------------------------------------
# (c) 远端前进且同文件冲突 → BLOCK 列冲突文件, 临时 worktree 已清理, 本地 commit 保留, 现场不动
# ---------------------------------------------------------------------------
def test_state_c_conflict_blocks_lists_files_cleans_worktree_keeps_local_commit(rig):
    a, remote, gov = rig["a"], rig["remote"], rig["gov"]
    b = rig["make_b"]()
    (b / GOV_REL / "governance" / "LEDGER.md").write_text(LEDGER_BASE + "- B's row 2\n", encoding="utf-8")
    _git(b, "add", "--", f"{GOV_REL}/governance/LEDGER.md")
    _git(b, "commit", "-q", "-m", "B: conflicting ledger row")
    _git(b, "push", "-q")
    b_tip = remote_tip(remote)

    touch_ledger(rig, "- A's row 2\n")
    other_before = other_project_fingerprint(a)
    tmp_before = leftover_tmp_worktrees()

    result = run(rig, paths=["governance/LEDGER.md"])

    assert result["verdict"] == Verdict.BLOCK, result["message"]
    assert result["committed"] is True and result["pushed"] is False
    assert result["conflict_files"] == [f"{GOV_REL}/governance/LEDGER.md"]
    assert result["temp_worktree_cleaned"] is True
    assert f"{GOV_REL}/governance/LEDGER.md" in result["message"] and "先落库冲突文件" in result["message"]
    ops = result["operations"]
    assert ops[-1].startswith(PUSH_NOT_DONE) and "cherry-pick conflict" in ops[-1] and "LEDGER.md" in ops[-1], ops[-1]
    assert result["push_result"]["pushed"] is False
    assert "Temp worktree cleaned: True" in " ".join(ops) and "Rebased" not in " ".join(ops)
    # 本地 commit 保留, 远端未动, 共享检出/他项目原样, 临时 worktree 已清理
    assert result["commit_hash"] == head(a)
    assert show_names(a) == {f"{GOV_REL}/governance/LEDGER.md"}
    assert remote_tip(remote) == b_tip
    assert (gov / "governance" / "LEDGER.md").read_text(encoding="utf-8") == LEDGER_BASE + "- A's row 2\n"
    assert other_project_fingerprint(a) == other_before
    assert _git(a, "stash", "list") == ""
    assert worktree_count(a) == 1 and leftover_tmp_worktrees() == tmp_before
    assert _git(a, "diff", "--cached", "--name-only") == ""
    assert not (a / ".git" / "CHERRY_PICK_HEAD").exists() and not (a / ".git" / "rebase-merge").exists()


# ---------------------------------------------------------------------------
# (b') 本地还有更早未推提交 → 一并整合(与 fast-forward push 会带上它们一致), 本地分支跟进
# ---------------------------------------------------------------------------
def test_earlier_unpushed_local_commits_are_integrated_together(rig):
    a, remote = rig["a"], rig["remote"]
    b = rig["make_b"]()
    (b / OTHER_REL / "remote-advanced.md").write_text("B moved the remote on\n", encoding="utf-8")
    _git(b, "add", "--", f"{OTHER_REL}/remote-advanced.md")
    _git(b, "commit", "-q", "-m", "B: advanced")
    _git(b, "push", "-q")

    touch_ledger(rig, "- earlier local row\n")
    earlier = run(rig, push=False, paths=["governance/LEDGER.md"])
    assert earlier["verdict"] == Verdict.PASS and earlier["committed"] is True

    (rig["gov"] / "notes" / "a.md").write_text("# note a\nchanged\n", encoding="utf-8")
    result = run(rig, paths=["notes/a.md"])

    assert result["verdict"] == Verdict.PASS, result["message"]
    assert result["pushed"] is True
    joined = " ".join(result["operations"])
    assert "remote-only=1 local-only=2" in joined and "Cherry-picked 2 commit(s)" in joined
    assert "also carries 1 earlier unpushed commit(s)" in joined and "Local branch fast-forwarded" in joined
    tip = remote_tip(remote)
    assert head(a) == tip
    assert show_names(a, tip) == {f"{GOV_REL}/notes/a.md"}
    assert show_names(a, f"{tip}^") == {f"{GOV_REL}/governance/LEDGER.md"}


# ---------------------------------------------------------------------------
# 件④: 目录 pathspec 含 1 删 1 改 1 新增(删+新增同内容 = 队列搬动) → 提交后 git show 三项齐
# ---------------------------------------------------------------------------
def test_piece4_directory_pathspec_stages_deletion_and_commit_has_all_three(rig):
    a, gov = rig["a"], rig["gov"]
    pending = gov / "5_tasks" / "queue" / "pending" / "card-1.md"
    claimed = gov / "5_tasks" / "queue" / "claimed" / "card-1.md"
    claimed.write_text(pending.read_text(encoding="utf-8"), encoding="utf-8")   # 新增(同内容 → git 视为重命名)
    pending.unlink()                                                             # 删
    pending.parent.rmdir()
    (gov / "5_tasks" / "LEDGER.md").write_text("# task ledger\n- t1\n- t2\n", encoding="utf-8")  # 改
    assert f" D {GOV_REL}/5_tasks/queue/pending/card-1.md" in porcelain(a)

    dry = run(rig, dry_run=True, paths=["5_tasks"])
    assert dry["verdict"] == Verdict.PASS
    assert {f["path"]: f["status"] for f in dry["commit_manifest"]["files"]} == {
        "5_tasks/LEDGER.md": "modified",
        "5_tasks/queue/pending/card-1.md": "deleted",
        "5_tasks/queue/claimed/card-1.md": "untracked_selected",
    }

    result = run(rig, push=False, paths=["5_tasks"])

    assert result["verdict"] == Verdict.PASS, result["message"]
    assert result["committed"] is True
    assert "git add -A -- 5_tasks" in " ".join(result["operations"]) and "add -A -- ." not in " ".join(result["operations"])
    assert show_names(a) == {
        f"{GOV_REL}/5_tasks/LEDGER.md",
        f"{GOV_REL}/5_tasks/queue/pending/card-1.md",
        f"{GOV_REL}/5_tasks/queue/claimed/card-1.md",
    }
    assert _git(a, "diff", "--cached", "--name-only") == ""
    assert f"{GOV_REL}/5_tasks" not in porcelain(a)


# ---------------------------------------------------------------------------
# 件④: mismatch 时清掉本次暂存(不留半成品), --paths 内预暂存条目精确还原
# ---------------------------------------------------------------------------
def test_piece4_manifest_mismatch_unstages_this_run_and_restores_prestaged(rig, monkeypatch):
    import tools.aipos_cli.governance_commit as gc
    a, gov = rig["a"], rig["gov"]
    # --paths 内的预暂存: 暂存内容 X, 工作树随后改成 Y(还原必须还原成 X 而非重新 add 成 Y)
    pre = gov / "5_tasks" / "pre.md"
    pre.write_text("X\n", encoding="utf-8")
    _git(a, "add", "--", f"{GOV_REL}/5_tasks/pre.md")
    pre.write_text("Y\n", encoding="utf-8")
    (gov / "5_tasks" / "LEDGER.md").write_text("# task ledger\n- t1\n- t2\n", encoding="utf-8")
    (gov / "5_tasks" / "queue" / "pending" / "card-1.md").unlink()
    cached_before = _git(a, "diff", "--cached", "--name-only", "--no-renames")
    assert cached_before == f"{GOV_REL}/5_tasks/pre.md\n"

    real_collect = gc.collect_commit_manifest

    def dropping_collect(governance_root, selected_paths):
        m = real_collect(governance_root, selected_paths)
        m["files"] = [f for f in m["files"] if not f["path"].endswith("LEDGER.md")]  # 模拟检查与暂存之间现场变化
        return m

    monkeypatch.setattr(gc, "collect_commit_manifest", dropping_collect)
    head_before = head(a)

    result = run(rig, push=False, paths=["5_tasks"])

    assert result["verdict"] == Verdict.BLOCK, (result["message"], result["operations"])
    assert result["committed"] is False and head(a) == head_before
    joined = " ".join(result["operations"])
    assert "MANIFEST MISMATCH before commit: +1 unexpected staged, -0 missing" in joined, joined
    assert "Unstaged this run's staging (git reset -- 5_tasks)" in joined
    assert "Restored 1 pre-staged" in joined
    # 暂存区只剩原来的预暂存条目, 且内容精确为 X(不是工作树的 Y); 删除与修改回到未暂存状态
    assert _git(a, "diff", "--cached", "--name-only", "--no-renames") == cached_before
    assert _git(a, "show", f":{GOV_REL}/5_tasks/pre.md") == "X\n"
    assert pre.read_text(encoding="utf-8") == "Y\n"
    st = porcelain(a)
    assert f" M {GOV_REL}/5_tasks/LEDGER.md" in st and f" D {GOV_REL}/5_tasks/queue/pending/card-1.md" in st
    assert f"AM {GOV_REL}/5_tasks/pre.md" in st


# ---------------------------------------------------------------------------
# 件③: 无待收内容但本地有未推提交 → 不再谎报「已最新」, BLOCK + PUSH NOT DONE
# ---------------------------------------------------------------------------
def test_piece3_noop_with_unpushed_local_commits_is_loud(rig):
    a = rig["a"]
    touch_ledger(rig)
    first = run(rig, push=False, paths=["governance/LEDGER.md"])
    assert first["verdict"] == Verdict.PASS and first["committed"] is True
    assert first["push_requested"] is False and first["push_result"] is None

    result = run(rig, paths=["governance/LEDGER.md"])
    assert result["verdict"] == Verdict.BLOCK and result["pushed"] is False
    assert result["unpushed_local_commits"] == 1
    assert result["operations"][-1].startswith(PUSH_NOT_DONE), result["operations"]
    assert "并未「已最新」" in result["message"]

    # 真正无事可推(本地无未推提交) → 仍是 F7 no-op 档 PASS/EXIT=0
    _git(a, "push", "-q")
    clean = run(rig, paths=["governance/LEDGER.md"])
    assert clean["verdict"] == Verdict.PASS and clean["committed"] is False and clean["severity"] == "info"
    assert clean["push_result"] == {"pushed": False, "reason": "nothing to push (no changes, no unpushed local commits)"}


# ---------------------------------------------------------------------------
# 件③ CLI: pushed=False → 退出码 1, 文本首行 PUSH NOT DONE: <原因>; --json operations 末条 = push 结果
# ---------------------------------------------------------------------------
def _cli(rig, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "governance-commit",
         "--governance-root", str(rig["gov"]), "--workspace-root", str(REPO_ROOT),
         "--actor", ACTOR, *extra],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )


def test_piece3_cli_push_not_done_first_line_and_nonzero_exit(rig, monkeypatch):
    a, remote = rig["a"], rig["remote"]
    monkeypatch.setenv("LYBRA_SCHEMA_DIR", str(REPO_ROOT / "schema"))
    b = rig["make_b"]()
    (b / GOV_REL / "governance" / "LEDGER.md").write_text(LEDGER_BASE + "- B's row 2\n", encoding="utf-8")
    _git(b, "add", "--", f"{GOV_REL}/governance/LEDGER.md")
    _git(b, "commit", "-q", "-m", "B: conflicting ledger row")
    _git(b, "push", "-q")
    touch_ledger(rig, "- A's row 2\n")

    proc = _cli(rig, "--paths", "governance/LEDGER.md")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    first_line = proc.stdout.split("\n", 1)[0]
    assert first_line.startswith(PUSH_NOT_DONE) and "cherry-pick conflict" in first_line, proc.stdout
    assert head(a) != remote_tip(remote)  # 本地 commit 保留, 远端未动

    # 同一现场 --json(本地 commit 已在, 再跑 = 无待收内容但本地有未推提交 → BLOCK, 末条 PUSH NOT DONE)
    proc2 = _cli(rig, "--json", "--paths", "governance/LEDGER.md")
    assert proc2.returncode == 1, proc2.stdout + proc2.stderr
    payload = json.loads(proc2.stdout)
    assert payload["verdict"] == "BLOCK" and payload["pushed"] is False and payload["unpushed_local_commits"] == 1
    assert payload["operations"][-1].startswith(PUSH_NOT_DONE), payload["operations"]
    assert payload["push_result"]["pushed"] is False


def test_piece3_cli_json_success_last_operation_is_push_result(rig, monkeypatch):
    a, remote = rig["a"], rig["remote"]
    monkeypatch.setenv("LYBRA_SCHEMA_DIR", str(REPO_ROOT / "schema"))
    touch_ledger(rig)

    proc = _cli(rig, "--json", "--paths", "governance/LEDGER.md")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "PASS" and payload["pushed"] is True and payload["push_requested"] is True
    assert payload["operations"][-1].startswith("Push result: pushed"), payload["operations"]
    assert payload["commit_hash"] == remote_tip(remote) == head(a)
    assert f" M {OTHER_REL}/plan.md" in porcelain(a) and f"?? {OTHER_REL}/untracked.md" in porcelain(a)

    # --no-push: 未请求 push, PASS/EXIT=0, 不出 PUSH NOT DONE
    touch_ledger(rig, "- row 3\n")
    proc3 = _cli(rig, "--no-push", "--paths", "governance/LEDGER.md")
    assert proc3.returncode == 0, proc3.stdout + proc3.stderr
    assert PUSH_NOT_DONE not in proc3.stdout and "已 commit,但未 push" in proc3.stdout
