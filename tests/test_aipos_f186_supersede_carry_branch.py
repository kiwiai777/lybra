"""AIPOS-F186 — 撤换承继分支: `lybra queue supersede --carry-branch`(gap #190, chris 理想 HBLX-16/17 实撞)。

靶场 = F184 同一夹具(临时治理仓 + 临时远端裸仓 + 临时产品仓; 不连生产门, 不碰真实治理仓 / 真实产品仓); 认领走门认领核心
mutate_queue_task claim(与门受控执行 / 文件 CLI 同一实现, 建树 = next_resolver._ensure_worktree 唯一建树实现)。

件① 预演列原卡分支名 / tip / 工作树路径 / 未提交改动计数(零写入); 确认 = 新卡卡面记 carried_from {task_id, branch, tip}(经 amend_task,
   修订记录随同落账), 原卡 withdrawn。
件② 新卡认领: 卡分支从原卡分支 tip 建(git log / merge-base 证明), 原卡分支保留; 认领输出「承继自 …」; my-tasks / card render 显示承继来源 +
   先合入当前 main 的命令。拒: 原卡工作树有未提交改动 / 原卡分支 tip 变化后认领 / 新卡已认领 / 原卡无卡分支。
件③ 不带 --carry-branch 行为与 F184 一致; 审计差异基线(base...卡分支)含承继提交; 结案记录写 carried_from。
件④ 文档: 顾问章程 / 命令手册 / README 的「在途卡契约需更正」一节。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f184_queue_landing_supersede import (  # noqa: E402,F401  (rig = F184 同一夹具, 禁第二套靶场)
    DRIVER, EXEC, GOV_REL, _card, _cli, _commit_all, _git, _head_files, rig,
)
from tools.aipos_cli.agent_profiles import load_agent_profiles  # noqa: E402
from tools.aipos_cli.frontmatter import require_frontmatter  # noqa: E402
from tools.aipos_cli.queue_mutation import mutate_queue_task  # noqa: E402

ORIG = "PROJ-16"
NEW = "PROJ-16B"
WORDS = "验证条款自相矛盾, 出更正卡承继原卡分支"


def _show(line: str) -> None:
    sys.__stdout__.write(line + "\n")
    sys.__stdout__.flush()


def _claim(gov: Path, task_id: str) -> dict:
    return mutate_queue_task(gov, "claim", task_id=task_id, actor=EXEC, dry_run=False, profiles=load_agent_profiles(gov), with_records=True)


def _product(gov: Path) -> Path:
    return Path(json.loads((gov / "project.json").read_text(encoding="utf-8"))["code_repo"])


def _commit_in(worktree: Path, rel: str, text: str, msg: str) -> str:
    (worktree / rel).parent.mkdir(parents=True, exist_ok=True)
    (worktree / rel).write_text(text, encoding="utf-8")
    _git(worktree, "add", "--", rel)
    _git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return _git(worktree, "rev-parse", "HEAD")


def _queue_card(gov: Path, task_id: str) -> tuple[dict, Path]:
    from tools.aipos_cli.task_loader import find_task_card

    path, _state = find_task_card(gov, task_id)
    assert path is not None, task_id
    return require_frontmatter(path)[0], path


def _supersede(gov: Path, *extra: str) -> tuple[int, str, str]:
    return _cli(gov, "queue", "supersede", "--task-id", ORIG, "--by", NEW, "--actor", DRIVER, "--owner-words", WORDS, *extra)


def _orig_claimed_with_commit(rig: dict) -> tuple[Path, Path, str]:
    """原卡认领(门认领建树)→ 执行体在原卡分支提交一笔 → 出新卡(pending)。返回 (产品仓, 原卡工作树, 原卡分支 tip)。"""
    gov = rig["gov"]
    _card(gov, ORIG, "pending")
    _commit_all(rig["repo"], f"publish {ORIG}")
    claimed = _claim(gov, ORIG)
    assert claimed.get("moved"), claimed
    worktree = Path(claimed["worktree_path"])
    tip = _commit_in(worktree, "app/feature.py", "VALUE = 16\n", f"feat({ORIG}): 原卡已做的工作")
    _card(gov, NEW, "pending")
    _commit_all(rig["repo"], f"claimed {ORIG} + publish {NEW}")
    return _product(gov), worktree, tip


# ---------------------------------------------------------------------------
# 件① + 件②: 全链路
# ---------------------------------------------------------------------------

def test_item1_item2_carry_branch_full_flow(rig: dict) -> None:
    from tools.aipos_cli.aipos_cli import main as cli_main  # noqa: F401  (CLI 解析器同一实现)
    from tools.aipos_cli.board_adapter import _card_branch_changed_files
    from tools.aipos_cli.card_render import render_card
    from tools.aipos_cli.next_resolver import card_workstation_view
    from tools.aipos_cli.renderer import render_queue_mutation_text

    repo, gov = rig["repo"], rig["gov"]
    product, orig_tree, tip = _orig_claimed_with_commit(rig)
    _commit_in(product, "app/main_moved.py", "X = 1\n", "main 前进(别的卡已合入)")  # main 在原卡分出后前进
    main_tip = _git(product, "rev-parse", "main")
    head_before = _git(repo, "rev-parse", "HEAD")

    rc, out, err = _supersede(gov, "--carry-branch")
    _show(f"[件①] 预演 rc={rc}:\n{out.strip()}")
    assert rc == 0, (out, err)
    assert out.startswith(f"预演: {ORIG} [claimed] → withdrawn, superseded_by={NEW}")
    assert (f"承继(--carry-branch): 原卡 {ORIG} 分支 card/{ORIG}@{tip}, 工作树 {orig_tree}, 未提交改动 0 个; "
            f"新卡 {NEW} 被认领时卡分支 card/{NEW} 从该 tip 建(不从 main 拉), 原卡分支保留") in out
    new_fm, _ = _queue_card(gov, NEW)
    assert "carried_from" not in new_fm and _git(repo, "rev-parse", "HEAD") == head_before  # 预演零写入
    assert (gov / "5_tasks" / "queue" / "claimed" / f"{ORIG.lower()}.md").is_file()

    rc, out, err = _supersede(gov, "--carry-branch", "--confirm")
    _show(f"[件①] 确认 rc={rc}:\n{out.strip()}")
    assert rc == 0, (out, err)
    new_fm, new_path = _queue_card(gov, NEW)
    _show(f"[件①] 新卡卡面 carried_from = {json.dumps(new_fm.get('carried_from'), ensure_ascii=False)} ({new_path.parent.name})")
    assert new_fm["carried_from"] == {"task_id": ORIG, "branch": f"card/{ORIG}", "tip": tip} and new_path.parent.name == "pending"
    assert f"已记承继: 新卡 {NEW} 卡面 carried_from = {ORIG} card/{ORIG}@{tip}(修订记录 amendment_" in out
    orig_fm, orig_path = _queue_card(gov, ORIG)
    assert orig_path.parent.name == "withdrawn" and orig_fm["superseded_by"] == NEW
    landed = sorted(_head_files(repo))
    _show(f"[件①] 落账 {_git(repo, 'log', '-1', '--format=%s')}: {landed}")
    assert _git(repo, "log", "-1", "--format=%s") == f"chore(governance): 队列写落账 supersede {ORIG}"
    assert f"{GOV_REL}/5_tasks/queue/pending/{NEW.lower()}.md" in landed
    assert any(f"/records/amendments/{NEW}/" in f for f in landed), landed
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")

    claimed = _claim(gov, NEW)
    _show(f"[件②] 新卡认领: moved={claimed.get('moved')} worktree={claimed.get('worktree_path')} "
          f"line={claimed.get('worktree_carried_line')!r}")
    assert claimed.get("moved") and claimed["worktree_carried_line"] == f"承继自 {ORIG} card/{ORIG}@{tip}"
    text = render_queue_mutation_text(claimed)
    _show("[件②] 认领输出原文:\n" + "\n".join(ln for ln in text.splitlines() if "Worktree" in ln or "承继" in ln))
    assert f"  承继自 {ORIG} card/{ORIG}@{tip}" in text
    log = _git(product, "log", "--format=%H %s", f"card/{NEW}")
    _show(f"[件②] git log card/{NEW}:\n{log}")
    assert f"{tip} feat({ORIG}): 原卡已做的工作" in log
    assert _git(product, "merge-base", f"card/{NEW}", f"card/{ORIG}") == tip == _git(product, "rev-parse", f"card/{NEW}")
    assert _git(product, "rev-parse", f"card/{ORIG}") == tip and orig_tree.is_dir()  # 原卡分支 / 工作树保留
    assert (Path(claimed["worktree_path"]) / "app" / "feature.py").read_text(encoding="utf-8") == "VALUE = 16\n"

    new_fm, _ = _queue_card(gov, NEW)
    view = card_workstation_view(gov, NEW, new_fm)
    _show(f"[件②] my-tasks 开工面 carried_line = {view['carried_line']}")
    assert view["carried_from"] == {"task_id": ORIG, "branch": f"card/{ORIG}", "tip": tip}
    assert view["carried_line"] == (f"承继自 {ORIG} 分支 card/{ORIG}@{tip}(原卡已做的工作在本卡分支上); "
                                    f"开工先把当前 main 合入本分支: git checkout card/{NEW} && git merge main")
    rc, out, err = _cli(gov, "my-tasks", "--actor", EXEC, "--json")
    rows = [t for t in json.loads(out)["tasks"] if t.get("task_id") == NEW]
    assert rc == 0 and rows and rows[0]["carried_line"] == view["carried_line"], (out, err)
    rc, out, _ = _cli(gov, "my-tasks", "--actor", EXEC)
    assert f"  carried_from: {view['carried_line']}" in out
    rendered = render_card(NEW, gov, harness="claude-code")["files"]["CLAUDE.md"]
    assert f"(分支 `card/{NEW}`; {view['carried_line']})" in rendered

    # 件③ 审计 / 合入差异基线 = main...卡分支(含承继提交, 不含 main 上别人的改动)
    changed = _card_branch_changed_files(product, NEW)
    _show(f"[件③] git diff main...card/{NEW} = {changed} (main={main_tip[:8]})")
    assert changed == ["app/feature.py"]


# ---------------------------------------------------------------------------
# 件②: 拒绝
# ---------------------------------------------------------------------------

def test_item2_dirty_original_worktree_refused(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _product_repo, orig_tree, tip = _orig_claimed_with_commit(rig)
    (orig_tree / "app" / "feature.py").write_text("VALUE = 17\n", encoding="utf-8")  # 已跟踪文件改动(未提交)
    (orig_tree / "app" / "wip.py").write_text("WIP = True\n", encoding="utf-8")       # 未跟踪
    head = _git(repo, "rev-parse", "HEAD")
    for mode in ((), ("--confirm",)):
        rc, out, err = _supersede(gov, "--carry-branch", *mode)
        _show(f"[件②] 原卡工作树未提交 {mode or '预演'} rc={rc}:\n{out.strip()}")
        assert rc == 1 and out.startswith("撤换被拒(零写入)"), (out, err)
        assert (f"原卡 {ORIG} 工作树 {orig_tree} 有 2 个未提交改动, 承继被拒(零写入, 产品不代提交): "
                f"先让执行体在原卡分支 card/{ORIG} 提交, 再重跑本命令") in out
        assert f"原卡 {ORIG} 分支 card/{ORIG}@{tip}, 工作树 {orig_tree}, 未提交改动 2 个" in out
    assert _git(repo, "rev-parse", "HEAD") == head and "carried_from" not in _queue_card(gov, NEW)[0]
    assert _queue_card(gov, ORIG)[1].parent.name == "claimed"
    assert _git(orig_tree, "status", "--porcelain").count("\n") == 1  # 不代提交: 两处改动原样
    decisions = gov / "5_tasks" / "records" / "owner_decisions"
    assert not decisions.exists() or not any(decisions.iterdir())


def test_item2_claim_refused_when_original_tip_moved(rig: dict) -> None:
    gov = rig["gov"]
    product, orig_tree, tip = _orig_claimed_with_commit(rig)
    rc, out, err = _supersede(gov, "--carry-branch", "--confirm")
    assert rc == 0, (out, err)
    moved_tip = _commit_in(orig_tree, "app/later.py", "LATER = 1\n", f"fix({ORIG}): 撤换后原卡分支又前进")
    claimed = _claim(gov, NEW)
    reasons = claimed.get("blocking_reasons") or []
    _show(f"[件②] 原卡分支 tip 变化后认领: verdict={claimed.get('verdict')} moved={claimed.get('moved')}\n  " + "\n  ".join(reasons))
    assert claimed.get("verdict") == "BLOCK" and not claimed.get("moved")
    assert any(r.startswith("WORKTREE_CREATE_FAILED") and
               f"CARRY_TIP_MOVED: 卡面承继 {ORIG} card/{ORIG}@{tip}, 但该分支当前 tip 为 {moved_tip}(与记录不符), 认领被拒" in r
               for r in reasons), reasons
    assert _queue_card(gov, NEW)[1].parent.name == "pending"  # 队列零变更
    assert _git(product, "branch", "--list", f"card/{NEW}") == ""  # 未建新卡分支


def test_item2_successor_claimed_and_no_branch_refused(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _product_repo, _tree, _tip = _orig_claimed_with_commit(rig)
    claimed = _claim(gov, NEW)
    assert claimed.get("moved"), claimed
    _commit_all(repo, f"claimed {NEW}")
    rc, out, err = _supersede(gov, "--carry-branch", "--confirm")
    _show(f"[件②] 新卡已认领 rc={rc}:\n{out.strip()}")
    assert rc == 1 and out.startswith("撤换被拒(零写入)"), (out, err)
    assert f"--carry-branch 要求新卡 {NEW} 在 pending 且未认领(当前 claimed, 已认领 " in out
    assert f"新卡 {NEW} 的卡分支 card/{NEW} 已存在于" in out
    assert _queue_card(gov, ORIG)[1].parent.name == "claimed"

    _card(gov, "PROJ-20", "pending")  # 原卡从未认领 = 无卡分支
    _card(gov, "PROJ-20B", "pending")
    _commit_all(repo, "two pending")
    rc, out, _ = _cli(gov, "queue", "supersede", "--task-id", "PROJ-20", "--by", "PROJ-20B", "--actor", DRIVER, "--owner-words", WORDS,
                      "--carry-branch")
    _show(f"[件②] 原卡无卡分支 rc={rc}: {out.strip()}")
    assert rc == 1 and "原卡 PROJ-20 在产品仓 " in out and "无卡分支 card/PROJ-20, 无可承继的工作(零写入)" in out


# ---------------------------------------------------------------------------
# 件③: 不带 --carry-branch = F184 行为; 结案记录; 审计取证锚点
# ---------------------------------------------------------------------------

def test_item3_without_carry_branch_is_f184_behaviour(rig: dict) -> None:
    gov = rig["gov"]
    product, _tree, tip = _orig_claimed_with_commit(rig)
    rc, out, err = _supersede(gov)
    _show(f"[件③] 不带 --carry-branch 预演 rc={rc}:\n{out.strip()}")
    assert rc == 0 and out.startswith(f"预演: {ORIG} [claimed] → withdrawn, superseded_by={NEW}") and "承继" not in out
    rc, out, err = _supersede(gov, "--confirm")
    _show(f"[件③] 不带 --carry-branch 确认 rc={rc}:\n{out.strip()}")
    assert rc == 0 and out.startswith(f"已撤换: {ORIG} [claimed] → withdrawn, superseded_by={NEW}") and "承继" not in out
    assert "carried_from" not in _queue_card(gov, NEW)[0]
    claimed = _claim(gov, NEW)
    assert claimed.get("moved") and "worktree_carried_line" not in claimed
    main_tip = _git(product, "rev-parse", "main")
    assert _git(product, "rev-parse", f"card/{NEW}") == main_tip and _git(product, "rev-parse", f"card/{ORIG}") == tip
    _show(f"[件③] 新卡分支 = main {main_tip[:8]}(不含原卡提交 {tip[:8]})")


def test_item3_closure_record_and_audit_anchor_carry(rig: dict) -> None:
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.record_writer import build_closure_record_markdown

    carried = {"task_id": ORIG, "branch": f"card/{ORIG}", "tip": "a" * 40}
    md = build_closure_record_markdown(task_id=NEW, task_path=f"5_tasks/queue/claimed/{NEW.lower()}.md", actor=EXEC, closure_id="close_x",
                                       closed_at="2026-10-18T00:00:00Z", closure_evidence={"type": "audit_verdict", "ref": "v"},
                                       carried_from=carried)
    fm, body, _warn = parse_markdown_frontmatter(md)
    _show(f"[件③] 结案记录 carried_from = {fm.get('carried_from')}")
    assert fm["carried_from"] == carried and f"承继自 `{ORIG}` 分支 `card/{ORIG}`@`{'a' * 40}`" in body
    plain = build_closure_record_markdown(task_id=NEW, task_path="p", actor=EXEC, closure_id="close_y", closed_at="2026-10-18T00:00:00Z",
                                          closure_evidence={"type": "t", "ref": "r"})
    assert "carried_from" not in parse_markdown_frontmatter(plain)[0]  # 缺省 = 不写(行为不变)
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    assert "carried_from=closure_carried_from" in src and "closure_carried_from = card_carried_from(" in src  # queue_close 传卡面承继

    anchor = build_forensic_anchor_section(NEW, rig["gov"], {"task_id": NEW, "task_mode": "code", "carried_from": carried})
    line = [ln for ln in anchor.splitlines() if "撤换承继" in ln]
    _show(f"[件③] 审计取证锚点: {line}")
    assert line == [f"- **撤换承继**: 本卡分支承继自 `{ORIG}` 分支 `card/{ORIG}`@`{'a' * 40}`; 差异基线仍为 `main`(上一行三点 diff 含承继提交, 一并审)"]
    assert f"git diff main...card/{NEW}" in anchor
    assert "撤换承继" not in build_forensic_anchor_section(NEW, rig["gov"], {"task_id": NEW, "task_mode": "code"})


def test_item3_card_face_invalid_carried_from_fail_closed() -> None:
    from tools.aipos_cli.next_resolver import card_carried_from

    assert card_carried_from({}) is None and card_carried_from({"carried_from": None}) is None
    for bad in ("card/X", {"task_id": "X", "branch": "card/X"}, {"task_id": "X", "branch": "card/X", "tip": "abc"}):
        with pytest.raises(ValueError):
            card_carried_from({"carried_from": bad})


# ---------------------------------------------------------------------------
# 声明单源 + 件④ 文档
# ---------------------------------------------------------------------------

def test_declarations_single_source() -> None:
    from tools.aipos_cli import queue_mutation
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.schema_loader import code_repo_schema_root, load_schema

    root = code_repo_schema_root()
    field = load_schema("card", root)["fields"]["carried_from"]
    assert field["type"] == "object" and field["required"] is False and "default" not in field and "缺省(不写)= 不承继" in field["description"]
    carry = queue_mutation.supersede_carry_declaration()
    assert carry["card_field"] == "carried_from" and carry["successor_states"] == ("pending",)
    verb = load_schema("verbs", root)["verbs"]["lybra_queue_supersede"]
    assert "carry_branch" in verb["parameters"]["properties"] and set(queue_mutation.SUPERSEDE_CARRY_TEXT_KEYS) <= set(verb["carry_texts"])
    args = build_parser().parse_args(["queue", "supersede", "--task-id", "A", "--by", "B", "--actor", "x", "--owner-words", "w", "--carry-branch"])
    assert args.carry_branch is True
    nr = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    ba = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    # 唯一建树 / 唯一撤换实现 / 唯一 pending 卡修订: 承继只扩展既有函数, 不另起
    assert nr.count("def _ensure_worktree(") == 1 and "git worktree add" not in nr.split("def carried_start_point(")[1].split("\ndef ")[0]
    assert ba.count("def supersede_task(") == 1 and ba.count("def amend_task(") == 1
    assert "amend_task(**carry_amend" in ba


def test_item4_docs_advisor_charter_commands_readme() -> None:
    texts = {rel: (REPO_ROOT / rel).read_text(encoding="utf-8") for rel in (
        "agents/roles/advisor/AGENTS.md", "agents/skills/advisor-commands/SKILL.md", "tools/aipos_cli/README.md")}
    for rel, text in texts.items():
        _show(f"[件④] {rel}: --carry-branch × {text.count('--carry-branch')}")
        assert "--carry-branch" in text and "契约需更正" in text, rel
    skill = texts["agents/skills/advisor-commands/SKILL.md"]
    assert "lybra queue supersede --task-id <原卡> --by <新卡> --actor <你的顾问实例> --owner-words \"<Owner原话摘要>\" --carry-branch" in skill
