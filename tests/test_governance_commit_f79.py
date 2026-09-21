"""AIPOS-F79: governance_commit 精确批次提交 — 显式 --paths 白名单 / dry-run 清单不动现场 / 正式提交只含选定文件

靶场分根(硬约束③): tmp 里一个 git 仓, 内含
  2_projects/gov/    ← 治理根(governance/ 5_tasks/ task_cards/ stage_archive/ notes/)
  2_projects/other/  ← 他项目
  gov/history/*      ← 若干未跟踪历史材料(绝不可被夹带)
  gov/governance/no-frontmatter.md ← 缺 frontmatter 的治理文档(R6M 护栏靶)
并安装产品仓的 tools/hooks/governance-pre-commit 为 .git/hooks/pre-commit(件④ 护栏照旧)。

验收(先红后绿: 本文件对 main 基座跑 = 红, 对 card/AIPOS-F79 跑 = 绿):
① --paths 选 2 文件提交后 git show --name-only HEAD == 2 文件; 未跟踪材料仍未跟踪; 他项目 status 不变
② --dry-run 输出清单(modified/added/deleted/untracked_selected)且 git status --porcelain 前后逐字相同
③ 预暂存在 --paths 外 → 拒并列出; 路径越根/他项目/整根/二选一 → 拒
④ R6M 护栏对选定文件仍拦(缺 frontmatter 的 governance/*.md 被拒), 补齐后同一命令通过
⑤ 无 --paths 的 dry-run 列出 add -A 范围并 warning 标出 untracked 数量
⑦ 本文件入 run-all.sh
附: --paths-file 与 --paths 同一实现; push 走 F79C 双向判据(fast-forward / 临时 worktree cherry-pick); CLI --dry-run --json 出清单
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.aipos_cli.governance_commit import (
    MANIFEST_ADDED,
    MANIFEST_DELETED,
    MANIFEST_MODIFIED,
    MANIFEST_UNTRACKED,
    collect_commit_manifest,
    governance_commit,
    resolve_commit_paths,
)
from tools.schema_constants import Verdict

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SRC = REPO_ROOT / "tools" / "hooks" / "governance-pre-commit"
ACTOR = "advisor.test.f79"

GOV_REL = "2_projects/gov"
OTHER_REL = "2_projects/other"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd), check=check, capture_output=True, text=True,
    ).stdout


def porcelain(repo: Path, *pathspec: str) -> str:
    """整仓(或限定 pathspec)的 git status --porcelain -uall 原文(用于逐字对照)。"""
    return _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *(pathspec or (".",)))


def cached(repo: Path) -> str:
    return _git(repo, "diff", "--cached", "--name-only")


def head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def show_names(repo: Path) -> set[str]:
    return set(_git(repo, "show", "--name-only", "--format=", "HEAD").split("\n")) - {""}


def scene_snapshot(repo: Path) -> dict[str, str]:
    return {"porcelain": porcelain(repo), "cached": cached(repo), "head": head(repo)}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """靶场: 共用 git 仓 + 治理根子目录 + 他项目子目录 + 未跟踪历史材料 + R6M 钩子。"""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=str(remote), check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Rig User")
    _git(repo, "config", "user.email", "rig@example.com")
    _git(repo, "remote", "add", "origin", str(remote))

    gov = repo / GOV_REL
    other = repo / OTHER_REL
    for d in ("governance/decision_log", "5_tasks/queue", "task_cards", "stage_archive", "notes"):
        (gov / d).mkdir(parents=True)
    (gov / "governance" / "POLICY.md").write_text("---\nstatus: active\n---\n# Policy\n", encoding="utf-8")
    for d in ("governance/decision_log", "5_tasks/queue", "task_cards", "stage_archive"):
        (gov / d / ".gitkeep").write_text("", encoding="utf-8")
    for n in ("a", "b", "c"):
        (gov / "notes" / f"{n}.md").write_text(f"# note {n}\n", encoding="utf-8")
    other.mkdir(parents=True)
    (other / "README.md").write_text("# other project\n", encoding="utf-8")
    (other / "plan.md").write_text("plan v1\n", encoding="utf-8")

    _git(repo, "add", "--", "2_projects")
    _git(repo, "commit", "-q", "-m", "rig: initial governance + other project")
    _git(repo, "push", "-q", "-u", "origin", "main")

    # 件④: 安装产品仓的 R6M/A1/F29 治理 pre-commit 钩子, 声明源指向产品仓 schema
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    shutil.copy(HOOK_SRC, hooks_dir / "pre-commit")
    os.chmod(hooks_dir / "pre-commit", 0o755)
    monkeypatch.setenv("LYBRA_SCHEMA_DIR", str(REPO_ROOT / "schema"))

    # 弄脏现场
    (gov / "notes" / "a.md").write_text("# note a\nchanged\n", encoding="utf-8")   # 选定
    (gov / "notes" / "b.md").write_text("# note b\nchanged\n", encoding="utf-8")   # 选定
    (gov / "notes" / "c.md").write_text("# note c\nchanged\n", encoding="utf-8")   # 未选定(已跟踪)
    (gov / "history").mkdir()
    (gov / "history" / "old-1.md").write_text("historical material 1\n", encoding="utf-8")   # 未跟踪历史
    (gov / "history" / "old-2.txt").write_text("historical material 2\n", encoding="utf-8")  # 未跟踪历史
    (gov / "governance" / "no-frontmatter.md").write_text("# no frontmatter here\n", encoding="utf-8")
    (other / "plan.md").write_text("plan v2 (other project, uncommitted)\n", encoding="utf-8")
    (other / "untracked.md").write_text("other project untracked\n", encoding="utf-8")

    # 同 F69: mock 治理路径解析, 免依赖真实 schema 的项目声明
    def mock_resolve_governance_path(key, governance_root, repo_root=None):
        path_map = {
            "task_cards": governance_root / "task_cards",
            "decision_log_dir": governance_root / "governance" / "decision_log",
            "stage_archive": governance_root / "stage_archive",
        }
        return path_map.get(key, governance_root / key)

    monkeypatch.setattr("tools.schema_loader.resolve_governance_path", mock_resolve_governance_path)

    return {"repo": repo, "gov": gov, "other": other, "remote": remote, "schema_root": tmp_path}


def run(rig, **kw):
    kw.setdefault("task_id", None)
    kw.setdefault("actor", ACTOR)
    kw.setdefault("repo_root", rig["schema_root"])
    kw.setdefault("push", False)
    return governance_commit(rig["gov"], **kw)


# ---------------------------------------------------------------------------
# 验收①: --paths 选 2 文件提交后 git show --name-only == 2 文件; 未选/未跟踪/他项目原样
# ---------------------------------------------------------------------------
def test_acceptance_1_paths_commit_contains_only_selected_files(rig):
    repo = rig["repo"]
    other_before = porcelain(repo, OTHER_REL)
    head_before = head(repo)

    result = run(rig, paths=["notes/a.md", "notes/b.md"])

    assert result["verdict"] == Verdict.PASS, result["message"]
    assert result["committed"] is True and result["commit_hash"] != head_before
    assert result["selected_paths"] == ["notes/a.md", "notes/b.md"]

    committed = show_names(repo)
    assert committed == {f"{GOV_REL}/notes/a.md", f"{GOV_REL}/notes/b.md"}, committed
    assert committed == {f["repo_path"] for f in result["commit_manifest"]["files"]}
    assert result["commit_manifest"]["counts"] == {
        MANIFEST_MODIFIED: 2, MANIFEST_ADDED: 0, MANIFEST_DELETED: 0, MANIFEST_UNTRACKED: 0, "total": 2,
    }
    ops = " ".join(result["operations"])
    # AIPOS-F79C 件④: 暂存改为 git add -A -- <paths>(-A 限定在白名单内, 目录 pathspec 内的删除也暂存);
    # 守卫改为「绝不出现整根 add -A -- .」
    assert "add -A -- ." not in ops, ops
    assert "git add -A -- notes/a.md notes/b.md" in ops
    assert "git show --name-only HEAD == manifest" in ops

    after = porcelain(repo)
    assert f"?? {GOV_REL}/history/old-1.md" in after
    assert f"?? {GOV_REL}/history/old-2.txt" in after
    assert f"?? {GOV_REL}/governance/no-frontmatter.md" in after
    assert f" M {GOV_REL}/notes/c.md" in after
    assert f"{GOV_REL}/notes/a.md" not in after and f"{GOV_REL}/notes/b.md" not in after
    assert porcelain(repo, OTHER_REL) == other_before
    assert cached(repo) == ""


# ---------------------------------------------------------------------------
# 验收②: --dry-run 输出四类清单, git status --porcelain 前后逐字相同(不 add/不 reset/不 stash)
# ---------------------------------------------------------------------------
def test_acceptance_2_dry_run_lists_manifest_and_scene_is_untouched(rig):
    repo, gov = rig["repo"], rig["gov"]
    # 四类: modified(a.md) / added(预暂存新文件, 在 --paths 内) / deleted(c.md) / untracked_selected(history/old-1.md)
    (gov / "notes" / "new.md").write_text("# new\n", encoding="utf-8")
    _git(repo, "add", "--", f"{GOV_REL}/notes/new.md")
    (gov / "notes" / "c.md").unlink()

    before = scene_snapshot(repo)
    result = run(rig, dry_run=True, paths=["notes", "history/old-1.md"])
    after = scene_snapshot(repo)

    assert after == before, "dry-run 改动了现场"
    assert result["verdict"] == Verdict.PASS, result["message"]
    assert result["dry_run"] is True and result["committed"] is False and result["commit_hash"] is None

    manifest = result["commit_manifest"]
    assert manifest["mode"] == "paths"
    assert manifest["ws_prefix"] == f"{GOV_REL}/"
    by_path = {f["path"]: f["status"] for f in manifest["files"]}
    assert by_path == {
        "notes/a.md": MANIFEST_MODIFIED,
        "notes/b.md": MANIFEST_MODIFIED,
        "notes/c.md": MANIFEST_DELETED,
        "notes/new.md": MANIFEST_ADDED,
        "history/old-1.md": MANIFEST_UNTRACKED,
    }, by_path
    # 未选定的历史材料/他项目/缺 frontmatter 文档不在清单
    assert "history/old-2.txt" not in by_path
    assert not any(f["repo_path"].startswith(OTHER_REL) for f in manifest["files"])
    assert "governance/no-frontmatter.md" not in by_path
    assert manifest["counts"]["total"] == 5
    assert "Pre-staged within --paths: 1 file(s) accepted" in " ".join(result["operations"])
    assert "notes/new.md" in result["message"] and "untracked_selected" in result["message"]


# ---------------------------------------------------------------------------
# 验收③: 预暂存在 --paths 外 → 拒并列出; 路径越根/他项目/整根/二选一 → 拒
# ---------------------------------------------------------------------------
def test_acceptance_3a_prestaged_outside_paths_is_rejected_and_listed(rig):
    repo = rig["repo"]
    _git(repo, "add", "--", f"{OTHER_REL}/plan.md")          # 他项目内的预暂存
    _git(repo, "add", "--", f"{GOV_REL}/notes/c.md")         # 治理根内但 --paths 外的预暂存
    before = scene_snapshot(repo)

    result = run(rig, paths=["notes/a.md", "notes/b.md"])

    assert result["verdict"] == Verdict.BLOCK
    assert result["committed"] is False
    assert sorted(result["pre_staged_outside"]) == [f"{GOV_REL}/notes/c.md", f"{OTHER_REL}/plan.md"]
    assert f"{OTHER_REL}/plan.md" in result["message"] and f"{GOV_REL}/notes/c.md" in result["message"]
    assert "--paths 白名单之外" in result["message"]
    assert scene_snapshot(repo) == before, "拒绝路径不得改动现场"

    # 同一现场, dry-run 也必须如实预演 BLOCK(不是先 PASS 再正式撞墙)
    dry = run(rig, dry_run=True, paths=["notes/a.md", "notes/b.md"])
    assert dry["verdict"] == Verdict.BLOCK and dry["dry_run"] is True
    assert scene_snapshot(repo) == before

    # 把预暂存文件纳入 --paths 后 = 可接受并进清单
    ok = run(rig, dry_run=True, paths=["notes/a.md", "notes/b.md", "notes/c.md"])
    assert ok["verdict"] == Verdict.BLOCK  # other/plan.md 仍在白名单外(他项目), 依旧拒
    assert ok["pre_staged_outside"] == [f"{OTHER_REL}/plan.md"]


@pytest.mark.parametrize(
    "kw, needle",
    [
        ({"paths": ["../other/plan.md"]}, "越出治理根"),
        ({"paths": ["notes/../../other/plan.md"]}, "越出治理根"),
        ({"paths": ["<ABS_OTHER>/plan.md"]}, "越出治理根"),
        ({"paths": ["/etc/hostname"]}, "越出治理根"),
        ({"paths": ["."]}, "指向治理根本身"),
        ({"paths": ["notes/a.md", ""]}, "空路径"),
        ({"paths": ["notes/a.md"], "paths_file": "<PF_OK>"}, "二选一"),
        ({"paths_file": "<PF_MISSING>"}, "不可读"),
        ({"paths_file": "<PF_EMPTY>"}, "为空"),
        ({"paths_file": "<PF_OUTSIDE>"}, "越出治理根"),
    ],
)
def test_acceptance_3b_paths_outside_root_or_invalid_are_rejected(rig, tmp_path, kw, needle):
    repo, other = rig["repo"], rig["other"]
    pf_ok = tmp_path / "ok.txt"; pf_ok.write_text("notes/a.md\n", encoding="utf-8")
    pf_empty = tmp_path / "empty.txt"; pf_empty.write_text("# only a comment\n\n", encoding="utf-8")
    pf_outside = tmp_path / "outside.txt"; pf_outside.write_text("notes/a.md\n../other/plan.md\n", encoding="utf-8")
    subst = {
        "<ABS_OTHER>": str(other), "<PF_OK>": str(pf_ok), "<PF_MISSING>": str(tmp_path / "nope.txt"),
        "<PF_EMPTY>": str(pf_empty), "<PF_OUTSIDE>": str(pf_outside),
    }
    kw = json.loads(json.dumps(kw))
    for k, v in list(kw.items()):
        if isinstance(v, list):
            kw[k] = [subst.get(x, x).replace("<ABS_OTHER>", str(other)) for x in v]
        elif isinstance(v, str):
            kw[k] = subst.get(v, v)

    before = scene_snapshot(repo)
    result = run(rig, **kw)
    assert result["verdict"] == Verdict.BLOCK, result
    assert result["committed"] is False and result["commit_hash"] is None
    assert needle in result["message"], result["message"]
    assert result["rejected_paths"], result
    assert scene_snapshot(repo) == before


def test_resolve_commit_paths_symlink_escaping_root_is_rejected(rig):
    gov, other = rig["gov"], rig["other"]
    (gov / "link-out").symlink_to(other)
    sel = resolve_commit_paths(gov, ["link-out/plan.md"], None)
    assert sel["rejected"] and "越出治理根" in sel["rejected"][0]["reason"]


# ---------------------------------------------------------------------------
# 验收④: R6M 护栏对选定文件仍拦(缺 frontmatter 的 governance/*.md 被拒); 补齐后同一命令通过
# ---------------------------------------------------------------------------
def test_acceptance_4_r6m_guard_still_blocks_selected_governance_doc(rig):
    repo, gov = rig["repo"], rig["gov"]
    head_before = head(repo)
    paths = ["governance/no-frontmatter.md", "notes/a.md"]

    # 红: 缺 frontmatter → pre-commit 钩子拒 → FAIL, 无提交, 另一选定文件也未被单独提交(原子)
    result = run(rig, paths=paths)
    assert result["verdict"] == Verdict.FAIL, result
    assert result["committed"] is False and head(repo) == head_before
    assert "frontmatter" in result["message"].lower(), result["message"]
    assert f"{GOV_REL}/governance/no-frontmatter.md" in result["message"]
    after = porcelain(repo)
    assert f"?? {GOV_REL}/history/old-1.md" in after and f"?? {GOV_REL}/history/old-2.txt" in after
    assert f" M {GOV_REL}/notes/c.md" in after

    # 绿: 补齐 frontmatter, 同一命令 → 通过, 且提交内只有选定的 2 文件
    (gov / "governance" / "no-frontmatter.md").write_text(
        "---\nstatus: active\n---\n# now with frontmatter\n", encoding="utf-8"
    )
    result2 = run(rig, paths=paths)
    assert result2["verdict"] == Verdict.PASS, result2["message"]
    assert show_names(repo) == {f"{GOV_REL}/governance/no-frontmatter.md", f"{GOV_REL}/notes/a.md"}
    assert f"?? {GOV_REL}/history/old-1.md" in porcelain(repo)


# ---------------------------------------------------------------------------
# 验收⑤: 无 --paths 的 dry-run 列出整根 add -A 范围, warning 标出 untracked 数量, 现场不动
# ---------------------------------------------------------------------------
def test_acceptance_5_dry_run_without_paths_lists_whole_root_and_warns_untracked(rig):
    repo = rig["repo"]
    before = scene_snapshot(repo)

    result = run(rig, dry_run=True)

    assert scene_snapshot(repo) == before
    assert result["verdict"] == Verdict.PASS and result["committed"] is False
    assert result["selected_paths"] is None
    manifest = result["commit_manifest"]
    assert manifest["mode"] == "whole_root" and manifest["pathspec"] == ["."]
    by_path = {f["path"]: f["status"] for f in manifest["files"]}
    assert by_path == {
        "notes/a.md": MANIFEST_MODIFIED,
        "notes/b.md": MANIFEST_MODIFIED,
        "notes/c.md": MANIFEST_MODIFIED,
        "history/old-1.md": MANIFEST_UNTRACKED,
        "history/old-2.txt": MANIFEST_UNTRACKED,
        "governance/no-frontmatter.md": MANIFEST_UNTRACKED,
    }, by_path
    assert not any(f["repo_path"].startswith(OTHER_REL) for f in manifest["files"])
    assert manifest["counts"][MANIFEST_UNTRACKED] == 3
    warnings = "\n".join(manifest["warnings"])
    assert "WARNING" in warnings and "3 个未跟踪文件" in warnings and "--paths" in warnings
    assert "3 个未跟踪文件" in result["message"]


# ---------------------------------------------------------------------------
# 件①: --paths-file 与 --paths 同一实现(清单逐条相同)
# ---------------------------------------------------------------------------
def test_paths_file_is_same_implementation_as_paths(rig, tmp_path):
    pf = tmp_path / "batch.txt"
    pf.write_text("# 本批次\nnotes/a.md\n\n  notes/b.md  \nnotes/a.md\n", encoding="utf-8")
    via_file = run(rig, dry_run=True, paths_file=pf)
    via_args = run(rig, dry_run=True, paths=["notes/a.md", "notes/b.md"])
    assert via_file["verdict"] == via_args["verdict"] == Verdict.PASS
    assert via_file["selected_paths"] == via_args["selected_paths"] == ["notes/a.md", "notes/b.md"]
    assert via_file["commit_manifest"]["files"] == via_args["commit_manifest"]["files"]


# ---------------------------------------------------------------------------
# 件③: push 走既有 F69 流程(fetch→rebase→push→远端校验), 提交仍只含选定文件
# ---------------------------------------------------------------------------
def test_push_with_paths_keeps_f69_rebase_and_verification(rig, tmp_path):
    repo, other, remote = rig["repo"], rig["other"], rig["remote"]
    # F69-R2 的 rebase 前脏树检查只放行未跟踪文件: 把未选定的已跟踪改动还原(保留未跟踪历史材料)
    _git(repo, "checkout", "--", f"{GOV_REL}/notes/c.md", f"{OTHER_REL}/plan.md")

    clone = tmp_path / "other_clone"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(clone)], check=True)
    _git(clone, "config", "user.name", "Other")
    _git(clone, "config", "user.email", "other@example.com")
    (clone / OTHER_REL / "remote-advanced.md").write_text("remote moved on\n", encoding="utf-8")
    _git(clone, "add", "--", f"{OTHER_REL}/remote-advanced.md")
    _git(clone, "commit", "-q", "-m", "other project advanced remote")
    _git(clone, "push", "-q")

    result = run(rig, push=True, paths=["notes/a.md", "notes/b.md"])

    assert result["verdict"] == Verdict.PASS, result["message"]
    assert result["committed"] is True and result["pushed"] is True
    ops = " ".join(result["operations"])
    # AIPOS-F79C 件②: 远端前进时不再原地 rebase, 改为临时 linked worktree cherry-pick + push + 本地分支跟进
    assert "Fetched from remote" in ops and "Remote has advanced" in ops and "Verified commit" in ops
    assert "Cherry-picked 1 commit(s)" in ops and "Local branch fast-forwarded" in ops and "Temp worktree cleaned: True" in ops
    assert "Rebased" not in ops and _git(repo, "stash", "list") == ""
    committed = set(_git(repo, "show", "--name-only", "--format=", result["commit_hash"]).split("\n")) - {""}
    assert committed == {f"{GOV_REL}/notes/a.md", f"{GOV_REL}/notes/b.md"}
    assert _git(repo, "branch", "-r", "--contains", result["commit_hash"]).strip()
    after = porcelain(repo)
    assert f"?? {GOV_REL}/history/old-1.md" in after and f"?? {OTHER_REL}/untracked.md" in after


# ---------------------------------------------------------------------------
# CLI 薄壳: lybra governance-commit --dry-run --json --paths 出清单, 现场不动
# ---------------------------------------------------------------------------
def test_cli_dry_run_json_emits_manifest(rig):
    repo, gov = rig["repo"], rig["gov"]
    before = scene_snapshot(repo)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "governance-commit",
         "--governance-root", str(gov), "--workspace-root", str(REPO_ROOT),
         "--actor", ACTOR, "--dry-run", "--json", "--paths", "notes/a.md", "--paths", "history/old-1.md"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "PASS" and payload["dry_run"] is True and payload["committed"] is False
    assert payload["selected_paths"] == ["notes/a.md", "history/old-1.md"]
    assert {f["path"]: f["status"] for f in payload["commit_manifest"]["files"]} == {
        "notes/a.md": MANIFEST_MODIFIED, "history/old-1.md": MANIFEST_UNTRACKED,
    }
    assert scene_snapshot(repo) == before

    # 越根 → 退出码 1 + BLOCK
    proc2 = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "governance-commit",
         "--governance-root", str(gov), "--workspace-root", str(REPO_ROOT),
         "--actor", ACTOR, "--dry-run", "--json", "--paths", "../other/plan.md"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )
    assert proc2.returncode == 1
    assert json.loads(proc2.stdout)["verdict"] == "BLOCK"
    assert scene_snapshot(repo) == before


def test_collect_commit_manifest_is_read_only(rig):
    repo = rig["repo"]
    before = scene_snapshot(repo)
    m = collect_commit_manifest(rig["gov"], ["notes"])
    assert scene_snapshot(repo) == before
    assert [f["path"] for f in m["files"]] == ["notes/a.md", "notes/b.md", "notes/c.md"]
