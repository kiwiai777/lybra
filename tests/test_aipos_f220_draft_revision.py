"""AIPOS-F220 — 未发布草稿的正式修订入口: `lybra draft create --from-json <修正后输入> --replace [--dry-run]`。

实撞(chris HBLX-40V, 10-23): 依赖卡草稿漏 dependency_condition → `draft publish --dry-run` BLOCK; 修正输入后再 draft create = 「Duplicate task_id
already exists」, queue amend 只认已入队卡 = 「No task found」——产品没有修订未发布草稿的入口。

唯一并入点: 准入判定 draft_writer.draft_revision_state(撞号扫描与 create 同一 find_task_id_collisions; 发布记录读 records.load_records publishes 组);
写入与校验 = create_draft 同一函数(replace=True 只多「旧稿字节原样留档 + 允许覆盖本卡未发布草稿」); 留档根 config.schema
governance_structure.paths.draft_history; 文案 / 命名声明 transitions.schema nodes.N0.draft_revision。

靶场 = AIPOS-F207 靶场(临时 HOME + 临时 home 根 git 仓; `lybra project new` 建靶场项目; 人肉期接入样本 = 卡 / 门记录按声明落点造), CLI main
本进程执行。项目名 / 实例名 / 卡号均为靶场值。全程不读真实凭据、不碰真实治理根、不对真实门写、不起 pi。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f207_closure_correct import ADVISOR, AUDIT, EXEC, PROJECT, _card, _cli, _commit, _iso, _record, rig  # noqa: E402,F401

SRC = "LX-40"     # 依赖的来源卡(已结案)
CARD = "LX-40V"   # 依赖卡(complex): 首稿漏 dependency_condition
DRAFT_REL = "5_tasks/drafts/lx-40v.md"


def _show(text: str) -> None:
    print(text, flush=True)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frontmatter(**extra) -> dict:
    fm = {"task_id": CARD, "title": f"{CARD} probe", "project": PROJECT, "task_mode": "docs", "task_class": "complex",
          "priority": "high", "created_by": ADVISOR, "needs_owner": False, "artifact_policy": "formal_write",
          "output_target": f"task_cards/{CARD}/artifacts/", "depends_on": [SRC], "harness": "claude-code",
          "lane": {"repo": "governance_root", "paths": [f"task_cards/{CARD}/"], "roles": ["executor"]},
          "assigned_to": EXEC, "agent_instance": EXEC, "context_bundle": EXEC, "planner_agent": ADVISOR, "reviewer": AUDIT,
          "audit_by": AUDIT, "audit": "required", "milestone": "M1", "criteria": ["C-SRC"]}
    fm.update(extra)
    return fm


def _input(path: Path, fm: dict, body: str = f"# {CARD}\n\n## 目标\n夹具正文\n") -> Path:
    path.write_text(json.dumps({"frontmatter": fm, "body": body}, ensure_ascii=False), encoding="utf-8")
    return path


def _run(ws: tuple, *argv: str) -> tuple[int, str]:
    rc, out, err = _cli(*ws, *argv)
    return rc, f"exit {rc}\n{out}{err}"


@pytest.fixture
def drig(rig, tmp_path):  # noqa: F811
    """F207 靶场 + 已结案来源卡 + 首稿(漏 dependency_condition)已起草。"""
    gov, ws = rig["gov"], rig["ws"]
    _card(gov, "completed", SRC, completed_at=_iso(-1))
    _record(gov, "closures", SRC, {"record_type": "closure", "closure_id": f"close_{SRC}_probe", "task_id": SRC, "actor": EXEC,
                                   "closed_at": _iso(-1)})
    _commit(rig["home"], "fixture: closed source card")
    inp = _input(tmp_path / "lx-40v-input.json", _frontmatter())
    rc, text = _run(ws, "draft", "create", "--from-json", str(inp), "--actor", ADVISOR)
    _show(f"---- 首稿 draft create(漏 dependency_condition)----\n{text}")
    assert rc == 0, text
    return {**rig, "inp": inp, "draft": gov / DRAFT_REL}


def _history_dir(gov: Path) -> Path:
    from tools.aipos_cli.draft_writer import draft_history_dir

    return draft_history_dir(gov, CARD)


# ===========================================================================
# 验收① HBLX-40V 形态复现 → --replace 预演 / 确认 → 留档字节同旧稿 → 发布通过
# ===========================================================================

def test_acceptance1_hblx40v_shape_revise_then_publish(drig):
    gov, ws, draft = drig["gov"], drig["ws"], drig["draft"]
    rc, text = _run(ws, "draft", "publish", "--path", DRAFT_REL, "--actor", ADVISOR, "--dry-run")
    _show(f"---- ① 首稿 draft publish --dry-run ----\n{text}")
    assert rc == 1 and "Complex-class dependent task requires dependency_condition" in text
    _input(drig["inp"], _frontmatter(dependency_condition="closure"))
    rc, text = _run(ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR)
    _show(f"---- ① 修正输入后 draft create(不带 --replace)----\n{text}")
    assert rc == 1 and f"Duplicate task_id already exists: {CARD}" in text  # 撞号仍拒(原拒因不变)
    assert f"DRAFT_REVISION_AVAILABLE: 同 ID 草稿 {DRAFT_REL} 尚未发布" in text and "draft create --from-json <修正后输入> --replace" in text
    rc, text = _run(ws, "queue", "amend", "--task-id", CARD, "--actor", ADVISOR, "--amendments", '{"dependency_condition": "closure"}',
                    "--amendment-reason", "fix", "--dry-run")
    _show(f"---- ① queue amend(未入队)----\n{text}")
    assert rc == 1 and f"No task found for task_id: {CARD}" in text and "DRAFT_REVISION_AVAILABLE" in text

    old_bytes, old_sha = draft.read_bytes(), _sha(draft)
    rc, text = _run(ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR, "--replace", "--dry-run")
    _show(f"---- ① draft create --replace --dry-run ----\n{text}")
    assert rc == 0 and "would_write: True" in text
    assert f"修订差异(旧稿 {DRAFT_REL} → 新稿):" in text and '+ dependency_condition: "closure"' in text
    assert "旧稿留档(将写): 5_tasks/draft_history/lx-40v/" in text
    assert draft.read_bytes() == old_bytes and not _history_dir(gov).exists()  # 预演零写入

    rc, out, err = _cli(*ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR, "--replace", "--json")
    _show(f"---- ① draft create --replace(确认, JSON)exit {rc} ----\n{out}{err}")
    data = json.loads(out)
    assert rc == 0 and data["wrote"] is True and data["replace"] is True
    history = gov / data["revision"]["history_path"]
    assert history.parent == _history_dir(gov) and history.is_file()
    assert history.read_bytes() == old_bytes and _sha(history) == old_sha == data["revision"]["old_sha256"]  # 字节同旧稿
    _show(f"[①] 留档 {data['revision']['history_path']} sha256={_sha(history)} = 旧稿 sha256 {old_sha}")
    from tools.aipos_cli.frontmatter import require_frontmatter

    old_meta = require_frontmatter(history)[0]
    new_meta = require_frontmatter(draft)[0]
    assert new_meta["dependency_condition"] == "closure" and "dependency_condition" not in old_meta
    assert new_meta["draft_created_at"] == old_meta["draft_created_at"] and new_meta["draft_created_by"] == old_meta["draft_created_by"]
    assert [w["kind"] for w in data["planned_writes"]] == ["create", "replace"]

    from tools.aipos_cli.draft_validator import find_task_id_collisions, list_drafts

    assert find_task_id_collisions(gov, CARD) == [DRAFT_REL]  # 留档不算撞号
    assert [d["path"] for d in list_drafts(gov)["drafts"] if d["task_id"] == CARD] == [DRAFT_REL]  # 留档不是草稿

    rc, text = _run(ws, "draft", "publish", "--path", DRAFT_REL, "--actor", ADVISOR, "--dry-run")
    _show(f"---- ① 修订后 draft publish --dry-run ----\n{text}")
    assert rc == 0 and "Blocking Reasons:" not in text, text
    rc, text = _run(ws, "draft", "publish", "--path", DRAFT_REL, "--actor", ADVISOR)
    _show(f"---- ① 修订后 draft publish ----\n{text}")
    from tools.aipos_cli.task_loader import find_task_card

    assert rc == 0 and find_task_card(gov, CARD)[1] == "pending", text


def test_acceptance1_history_append_only_second_revision(drig):
    """第二次修订另起一份留档, 首份留档字节不变(只追加)。"""
    gov, ws, draft = drig["gov"], drig["ws"], drig["draft"]
    first_bytes = draft.read_bytes()
    _input(drig["inp"], _frontmatter(dependency_condition="closure"))
    rc, out, _err = _cli(*ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR, "--replace", "--json")
    first = gov / json.loads(out)["revision"]["history_path"]
    second_bytes = draft.read_bytes()
    _input(drig["inp"], _frontmatter(dependency_condition="closure", priority="medium"))
    rc2, out2, _err2 = _cli(*ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR, "--replace", "--json")
    second = gov / json.loads(out2)["revision"]["history_path"]
    files = sorted(p.name for p in _history_dir(gov).iterdir())
    _show(f"[①] 两次修订留档: {files}")
    assert rc == rc2 == 0 and first != second and len(files) == 2
    assert first.read_bytes() == first_bytes and second.read_bytes() == second_bytes


# ===========================================================================
# 验收② 已发布 / 已入队 → --replace 拒并给 queue amend 出口原文, 零写入
# ===========================================================================

def test_acceptance2_published_draft_replace_refused(drig):
    gov, ws, draft = drig["gov"], drig["ws"], drig["draft"]
    _input(drig["inp"], _frontmatter(dependency_condition="closure"))
    assert _cli(*ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR, "--replace")[0] == 0
    history_before = sorted(p.name for p in _history_dir(gov).iterdir())
    rc, text = _run(ws, "draft", "publish", "--path", DRAFT_REL, "--actor", ADVISOR)
    assert rc == 0, text
    before = draft.read_bytes()
    _input(drig["inp"], _frontmatter(dependency_condition="closure", priority="medium"))
    for extra in (("--dry-run",), ()):
        rc, text = _run(ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR, "--replace", *extra)
        _show(f"---- ② 已发布卡 draft create --replace {' '.join(extra)} ----\n{text}")
        assert rc == 1 and f"DRAFT_REVISION_PUBLISHED: 卡 {CARD} 已发布或已入队" in text
        assert f"lybra --workspace-root {gov} queue amend --task-id {CARD} --actor <你的顾问实例> --amendments '<JSON>'" in text
        assert "队列 5_tasks/queue/pending/lx-40v.md" in text and f"发布记录 5_tasks/records/publishes/{CARD}/publish_lx-40v.md" in text
    assert draft.read_bytes() == before and sorted(p.name for p in _history_dir(gov).iterdir()) == history_before  # 零写入
    rc, text = _run(ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR)
    _show(f"---- ② 已发布卡 draft create(不带 --replace)----\n{text}")
    assert rc == 1 and "Duplicate task_id already exists" in text and "DRAFT_REVISION_PUBLISHED" in text and "queue amend" in text


def test_acceptance2_queued_without_publish_record_refused(rig, tmp_path):  # noqa: F811
    """人肉期接入样本: 卡已在队列(无发布记录, 如手工入队的存量卡)且草稿目录仍有同 ID 草稿 → --replace 拒(已入队)。"""
    gov, ws = rig["gov"], rig["ws"]
    inp = _input(tmp_path / "in.json", _frontmatter(dependency_condition="closure", depends_on=[], task_class="standard"))
    assert _cli(*ws, "draft", "create", "--from-json", str(inp), "--actor", ADVISOR)[0] == 0
    _card(gov, "claimed", CARD, claimed_by=EXEC, claim_id=f"claim_{CARD}_x")
    rc, text = _run(ws, "draft", "create", "--from-json", str(inp), "--actor", ADVISOR, "--replace", "--dry-run")
    _show(f"---- ② 已入队(claimed, 无发布记录)draft create --replace --dry-run ----\n{text}")
    assert rc == 1 and "DRAFT_REVISION_PUBLISHED" in text and "队列 5_tasks/queue/claimed/lx-40v.md" in text and "发布记录" not in text
    assert not _history_dir(gov).exists()


# ===========================================================================
# 验收③ 无同 ID 草稿时 --replace = 拒(DRAFT_REVISION_NO_DRAFT), 零写入; 其他不可修订形
# ===========================================================================

def test_acceptance3_replace_without_draft_refused(rig, tmp_path):  # noqa: F811
    gov, ws = rig["gov"], rig["ws"]
    inp = _input(tmp_path / "in.json", _frontmatter(dependency_condition="closure"))
    for extra in (("--dry-run",), ()):
        rc, text = _run(ws, "draft", "create", "--from-json", str(inp), "--actor", ADVISOR, "--replace", *extra)
        _show(f"---- ③ 无草稿 draft create --replace {' '.join(extra)} ----\n{text}")
        assert rc == 1 and f"DRAFT_REVISION_NO_DRAFT: --replace 只修订已存在的未发布草稿, 草稿落点 {DRAFT_REL} 不存在" in text
    assert not (gov / DRAFT_REL).exists() and not _history_dir(gov).exists()
    rc, text = _run(ws, "draft", "create", "--from-json", str(inp), "--actor", ADVISOR)  # 不带 --replace = 照常新建
    assert rc == 0 and (gov / DRAFT_REL).is_file(), text


def test_replace_refused_when_other_same_id_draft_or_mismatch(drig, tmp_path):
    from tools.aipos_cli.draft_writer import draft_revision_state

    gov, ws, draft = drig["gov"], drig["ws"], drig["draft"]
    extra = gov / "5_tasks" / "drafts" / "external_intake" / "x.md"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
    rc, text = _run(ws, "draft", "create", "--from-json", str(drig["inp"]), "--actor", ADVISOR, "--replace", "--dry-run")
    _show(f"---- 同 ID 另有草稿 --replace ----\n{text}")
    assert rc == 1 and "DRAFT_REVISION_AMBIGUOUS" in text and "5_tasks/drafts/external_intake/x.md" in text
    extra.unlink()
    # 落点文件 task_id 与输入不同(slug 相同): 不覆盖
    draft.write_text(draft.read_text(encoding="utf-8").replace(f"task_id: {CARD}", "task_id: LX.40V"), encoding="utf-8")
    state = draft_revision_state(gov, CARD)
    assert state["state"] == "mismatch" and state["existing_task_id"] == "LX.40V"


# ===========================================================================
# 声明与单一实现
# ===========================================================================

def test_declarations_single_source():
    from tools.aipos_cli.draft_writer import draft_revision_declaration
    from tools.schema_loader import load_schema

    decl = draft_revision_declaration()
    paths = load_schema("config")["governance_structure"]["paths"]
    assert decl["history_path_key"] in paths and paths[decl["history_path_key"]]["relative_to"] == "tasks_root"
    assert decl["cli_flag"] == "--replace" and set(decl["texts"]) >= {"duplicate_hint", "amend_hint", "no_draft", "diff_header"}
    src = (REPO_ROOT / "tools" / "aipos_cli" / "draft_writer.py").read_text(encoding="utf-8")
    # 修订与起草同一写入函数: CLI 只调 create_draft(replace=...), 无第二个草稿写入口
    cli = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    assert "create_draft(repo_root, metadata, body, dry_run=args.dry_run, replace=args.replace)" in cli
    assert src.count("def create_draft(") == 1 and "def revise_draft" not in src
