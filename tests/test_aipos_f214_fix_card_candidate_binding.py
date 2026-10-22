"""AIPOS-F214 验收①(F208 P1-7 实撞面): `lybra roadmap verify --result mismatch` 给验证卡(task_mode=validation, 固定候选)出的修复卡
带候选绑定(validation_source_task / validation_candidate)并经 `lybra draft publish` 发布成功。

改前: 路线图修复卡承继表 verbs lybra_roadmap.verify.fix_card.inherit_fields 只有 15 项(缺 validation_source_task / validation_candidate /
artifact_scope / owner_verify / resources), 修复卡缺候选绑定 → 被 VALIDATION_SUBJECT_MISSING 拒(F180C 起修复卡起草与发布同一判据,
起草即拒: VERIFY_FIX_DRAFT_BLOCKED, 抽查记录照写、修复卡待出)。第二个用例把承继表临时换回改前 15 项(内存, 不写盘)复现该拒因原文作
对照; 第一个用例只用 CLI 与既有靶场夹具, 可原样在改前产品树上跑(改前原文见 RETURN)。

靶场 = AIPOS-F207 靶场(临时 HOME + 临时 home 根 git 仓 + 临时远端裸仓; `lybra project new` 建靶场项目; 人肉期接入样本 = 卡 / 门记录
按声明落点造), 另建临时产品仓作 code_repo(`lybra project set-repo --confirm`), 来源卡 = 已结案代码卡, 验证卡 = 已结案固定候选卡(挂
owner_check 标准, 截图产物齐, 门裁决 PASS)。项目名 / 实例名 / 卡号均为靶场值。全程不读真实凭据、不碰真实治理根、不对真实门写、不起 pi。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f175_verification_owner_check import PNG, _artifact, _verdict  # noqa: E402  (靶场造物唯一来源)
from test_aipos_f207_closure_correct import ADVISOR, PROJECT, _card, _cli, _commit, _iso, rig  # noqa: E402,F401  (F207 靶场复用)

SRC = "LT-SRC1"      # 来源卡: 已结案代码卡(产出候选)
VAL = "LT-VAL1"      # 验证卡: 固定候选, 挂 owner_check 标准
CRIT = "C-UI"
#: 改前(F214 之前)路线图修复卡承继表 = verbs lybra_roadmap.verify.fix_card.inherit_fields 原 15 项
PRE_F214_FIX_CARD_FIELDS = ["project", "assigned_to", "agent_instance", "context_bundle", "task_mode", "task_class", "priority",
                            "output_target", "artifact_policy", "harness", "lane", "audit", "audit_by", "audit_runtime_evidence", "artifacts"]


def _show(text: str) -> None:
    print(text, flush=True)


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def vrig(rig):  # noqa: F811
    """F207 靶场 + 临时产品仓(code_repo)+ owner_check 标准 + 已结案来源卡 / 验证卡。"""
    home, gov, ws = rig["home"], rig["gov"], rig["ws"]
    app = home.parent / "app"
    app.mkdir()
    _git(app, "init", "-q", "-b", "main")
    _git(app, "config", "user.email", "probe@example.invalid")
    _git(app, "config", "user.name", "probe")
    (app / "feature.txt").write_text("candidate\n", encoding="utf-8")
    _git(app, "add", "feature.txt")
    _git(app, "commit", "-q", "-m", "candidate")
    sha = _git(app, "rev-parse", "HEAD")
    rc, out, err = _cli("project", "set-repo", PROJECT, "--home-root", str(home), "--code-repo", str(app), "--confirm", "--actor", ADVISOR)
    assert rc == 0, out + err
    rc, out, err = _cli("roadmap", "add-criterion", *ws, "--milestone", "M1", "--id", CRIT, "--statement", "列表页在模拟器上符合设计",
                        "--verify", "owner_check", "--confirm", "--actor", ADVISOR)
    assert rc == 0, out + err
    code_lane = {"lane": {"repo": str(app), "paths": ["app/"], "roles": ["executor"]}, "task_mode": "code", "output_target": "app/"}
    _card(gov, "completed", SRC, completed_at=_iso(-2), **code_lane)
    _card(gov, "completed", VAL, completed_at=_iso(-1), task_mode="validation", harness="pi", milestone="M1", criteria=[CRIT],
          lane={"repo": str(app), "paths": [f"task_cards/{VAL}/artifacts/"], "roles": ["executor"]},
          output_target=f"task_cards/{VAL}/artifacts/", validation_source_task=SRC, validation_candidate=sha,
          artifacts=[{"criterion": CRIT, "path_glob": "list.png", "kind": "screenshot"}], audit_by=f"audit.{gov.name}.h1")
    _verdict(gov, VAL)
    _artifact(gov, VAL, "list.png", PNG)
    _commit(home, "fixture: code repo + validation card awaiting owner check")
    return {**rig, "app": app, "sha": sha}


def _mismatch_and_publish(vrig) -> tuple[int, str, str, dict | None, str | None]:
    """verify mismatch --confirm(起草修复卡)→ 草稿在 = draft publish 预演 → 发布; 返回 (verify 退出码, verify 原文, publish 原文,
    草稿卡面 | None, 发布后所在队列 | None)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.next_resolver import _find_task_in_queue

    gov, ws = vrig["gov"], vrig["ws"]
    rc, out, err = _cli("roadmap", "verify", *ws, "--criterion", CRIT, "--result", "mismatch", "--relayed-by", ADVISOR,
                        "--owner-words", "夹具: 列表页分页位置不对", "--confirm", "--actor", "owner")
    verify = f"exit {rc}\n{out}{err}"
    fix_id = f"{VAL}-oc1"
    draft = gov / "5_tasks" / "drafts" / f"{fix_id.lower()}.md"
    if not draft.is_file():
        return rc, verify, "(无草稿, 未发布)", None, _find_task_in_queue(gov, fix_id)[1]
    meta, _body = require_frontmatter(draft)
    rel = draft.relative_to(gov).as_posix()
    rc1, out1, err1 = _cli(*ws, "draft", "publish", "--path", rel, "--actor", ADVISOR, "--dry-run")
    rc2, out2, err2 = _cli(*ws, "draft", "publish", "--path", rel, "--actor", ADVISOR)
    publish = f"[预演] exit {rc1}\n{out1}{err1}\n[发布] exit {rc2}\n{out2}{err2}"
    return rc, verify, publish, meta, _find_task_in_queue(gov, fix_id)[1]


def test_acceptance1_fix_card_for_validation_card_carries_binding_and_publishes(vrig):
    rc, verify, publish, meta, queue = _mismatch_and_publish(vrig)
    _show(f"---- 验收① roadmap verify mismatch --confirm 原文 ----\n{verify}")
    _show(f"---- 验收① 修复卡草稿卡面(承继) ----\ntask_mode={(meta or {}).get('task_mode')} validation_source_task="
          f"{(meta or {}).get('validation_source_task')} validation_candidate={(meta or {}).get('validation_candidate')} "
          f"criteria={(meta or {}).get('criteria')}")
    _show(f"---- 验收① draft publish 原文 ----\n{publish}\n[发布后所在队列] {queue}")
    assert rc == 0 and meta is not None, verify
    assert meta["task_mode"] == "validation" and meta["validation_source_task"] == SRC and meta["validation_candidate"] == vrig["sha"]
    assert "VALIDATION_SUBJECT_MISSING" not in verify + publish and queue == "pending", publish


def test_acceptance1_pre_f214_inherit_table_refused_for_comparison(vrig, monkeypatch: pytest.MonkeyPatch):
    """改前对照: 承继表换回 F214 前修复卡 15 项(内存)→ 修复卡缺候选绑定 → 起草即被 VALIDATION_SUBJECT_MISSING 拒(F180C 不连坐:
    抽查记录照写, 修复卡待出, 退出码 1), 修复卡不入队(原文)。"""
    from tools.aipos_cli import roadmap

    real = roadmap.followup_inherit_fields
    monkeypatch.setattr(roadmap, "followup_inherit_fields",
                        lambda template, where: list(PRE_F214_FIX_CARD_FIELDS) if "verify.fix_card" in where else real(template, where))
    rc, verify, publish, meta, queue = _mismatch_and_publish(vrig)
    _show(f"---- 验收①·改前对照(承继表 = 改前 15 项) roadmap verify mismatch --confirm 原文 ----\n{verify}\n{publish}\n"
          f"[修复卡所在队列] {queue}")
    assert rc == 1 and meta is None and queue is None
    assert "VERIFY_FIX_DRAFT_BLOCKED" in verify and "VALIDATION_SUBJECT_MISSING" in verify
