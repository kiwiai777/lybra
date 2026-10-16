"""AIPOS-F175 — 验收证据与 Owner 抽查: 验收标准五态推导(未验证 / 自动验证通过 / 待 Owner 抽查 / Owner 已确认 / 不符)、
产物(截图 / 输出)约定与展示、抽查结论经顾问命令记录、不符自动起草修复卡挂同一标准。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; `lybra project new` 建靶场项目, 治理根在仓内子目录)。路线图经产品命令
`lybra roadmap add-milestone / add-criterion --confirm` 写; 卡 / 门裁决记录 / 审计报告按声明落点造; 卡面 artifacts 声明的产物文件放进
卡产物落点(roadmap.card_artifact_dir = card.schema card_artifacts.dir); Owner 抽查经 `lybra roadmap verify` 写(真记录 + 真落账)。
项目名 / 实例名 / 卡号均为靶场值(产品不写死)。看板 = 测试进程内线程起的 ThreadingHTTPServer(测试客户端 urllib), 用完即 shutdown。
全程不读真实凭据、不碰真实治理根、不对真实门写、不起子进程(git 除外: 靶场仓落账)。

验收(卡面 ★验收): ① 五态各一原文 + 缺产物停未验证并说明原文 ② 收件箱 owner_check 项原文、verify confirm / mismatch 预演 / 确认 /
落账原文、mismatch 自动起草修复卡挂同一标准原文、缺 --relayed-by / --owner-words 拒原文 ③ 看板路线图页五态与产物缩略 / 摘录
(测试客户端 HTML)原文 ④ 章程渲染含新条款原文; 另: 声明 / 单一实现不变量。
"""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  (single source of rig helpers)
from test_aipos_f171_roadmap_board_readonly import _cli, _git, _tree_digest  # noqa: E402

PROJECT = "f175-probe"              # rig project name (not a real project)
ADVISOR = f"advisor.{PROJECT}.h1"
EXEC = f"exec.{PROJECT}.h1"
AUDIT = f"audit.{PROJECT}.h1"
RELAY = ("--relayed-by", ADVISOR, "--owner-words", "夹具: 首页看过了, 符合")
#: 1x1 PNG(靶场截图)
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8ffff3f0005fe02fea7d6a4c80000000049454e44ae426082")


def _show(text: str) -> None:
    print(text, flush=True)


def _iso(delta_days: float) -> str:
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=delta_days))


def _card(gov: Path, state: str, task_id: str, *, criteria: list[str], **extra) -> Path:
    from tools.aipos_cli.record_writer import render_markdown
    from tools.aipos_cli.task_loader import queue_root_for

    meta = {"task_id": task_id, "title": f"{task_id} probe", "project": PROJECT, "status": state, "task_mode": "code",
            "assigned_to": EXEC, "agent_instance": EXEC, "context_bundle": EXEC, "priority": "high", "output_target": "src/",
            "artifact_policy": "formal_write", "created_by": ADVISOR, "created_at": _iso(-5), "milestone": "M1", "criteria": criteria,
            **extra}
    path = queue_root_for(gov) / state / f"{task_id.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(meta, f"# {task_id}\n"), encoding="utf-8")
    return path


def _verdict(gov: Path, task_id: str, *, with_evidence: bool = False) -> str:
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix, render_markdown

    decl = runtime_evidence_declaration()
    report = "---\nverdict: PASS\n---\n# audit\n\n## 一句话结论\n通过\n"
    if with_evidence:
        report += (f"\n## {decl['section_heading']}\n\n```{decl['block_language']}\n"
                   '[{"command": "pytest -q", "worktree": "/w", "exit_code": 0, "log_excerpt": "1 passed"}]\n```\n')
    ref = f"out/audit/{task_id}R/RETURN.md"
    (gov / ref).parent.mkdir(parents=True, exist_ok=True)
    (gov / ref).write_text(report, encoding="utf-8")
    directory = record_dir(gov, "audit_verdicts", task_id)
    directory.mkdir(parents=True, exist_ok=True)
    vid = f"{record_file_prefix('audit_verdicts')}_{task_id}_probe"
    (directory / f"{vid}.md").write_text(render_markdown(
        {"record_type": "audit_verdict_record", "verdict_id": vid, "verdict": "PASS", "reviewed_task_id": task_id,
         "audit_task_id": f"{task_id}R", "verdict_at": _iso(-1), "evidence_refs": [ref]}, "# verdict\n"), encoding="utf-8")
    return vid


def _artifact(gov: Path, task_id: str, name: str, data: bytes) -> Path:
    from tools.aipos_cli.roadmap import card_artifact_dir

    path = card_artifact_dir(gov, task_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _commit(home: Path, message: str) -> None:
    _git(home, "add", "-A")
    _git(home, "commit", "-q", "-m", message)
    _git(home, "push", "-q", "origin", "main")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", PROJECT, "--home-root", str(hroot), "--actor", ADVISOR)
    assert rc == 0, out + err
    init_governance_repo(hroot)
    gov = hroot / PROJECT
    ws = ("--workspace-root", str(gov))
    roadmap = [
        ("add-milestone", "--id", "M1", "--lane", "web", "--title", "页面可交付", "--status", "active"),
        ("add-criterion", "--milestone", "M1", "--id", "C1", "--statement", "接口单测全绿", "--verify", "auto"),
        ("add-criterion", "--milestone", "M1", "--id", "C2", "--statement", "导出文件可下载", "--verify", "artifact"),
        ("add-criterion", "--milestone", "M1", "--id", "C3", "--statement", "导出页有截图", "--verify", "artifact"),
        ("add-criterion", "--milestone", "M1", "--id", "C4", "--statement", "首页符合设计", "--verify", "owner_check"),
        ("add-criterion", "--milestone", "M1", "--id", "C5", "--statement", "列表页符合设计", "--verify", "owner_check"),
        ("add-criterion", "--milestone", "M1", "--id", "C6", "--statement", "设置页符合设计", "--verify", "owner_check"),
    ]
    for argv in roadmap:
        rc, out, err = _cli("roadmap", argv[0], *ws, *argv[1:], "--confirm", "--actor", ADVISOR)
        assert rc == 0, f"{argv}\n{out}{err}"
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration

    evidence = {runtime_evidence_declaration()["card_field"]: {"required": True, "commands": ["pytest -q"]}}
    # C1 auto: K-1 结案 PASS + 运行证据节 → 自动验证通过
    _card(gov, "completed", "K-1", criteria=["C1"], completed_at=_iso(-1), **evidence)
    _verdict(gov, "K-1", with_evidence=True)
    # C2 artifact: K-2 声明 export*.csv 且落点有文件 → 自动验证通过
    _card(gov, "completed", "K-2", criteria=["C2"], completed_at=_iso(-1),
          artifacts=[{"criterion": "C2", "path_glob": "export*.csv", "kind": "output"}])
    _verdict(gov, "K-2")
    _artifact(gov, "K-2", "export-1.csv", "id,name\n1,alpha\n".encode("utf-8"))
    # C3 artifact: K-3 声明 export.png 但落点无文件 → 停在未验证, 原因写缺什么
    _card(gov, "completed", "K-3", criteria=["C3"], completed_at=_iso(-1),
          artifacts=[{"criterion": "C3", "path_glob": "export.png", "kind": "screenshot"}])
    _verdict(gov, "K-3")
    # C4 owner_check: K-4 截图 + 步骤输出齐 → 待 Owner 抽查(后经 verify confirm → Owner 已确认)
    _card(gov, "completed", "K-4", criteria=["C4"], completed_at=_iso(-1),
          artifacts=[{"criterion": "C4", "path_glob": "home.png", "kind": "screenshot"},
                     {"criterion": "C4", "path_glob": "steps.txt", "kind": "output"}])
    _verdict(gov, "K-4")
    _artifact(gov, "K-4", "home.png", PNG)
    _artifact(gov, "K-4", "steps.txt", "1. 打开首页\n2. 看顶部导航与卡片布局\n".encode("utf-8"))
    # 落点里有但卡面未声明 → 看板取图拒(404)
    _artifact(gov, "K-4", "secret.png", PNG)
    # C5 owner_check: K-5 截图齐 → 待 Owner 抽查(后经 verify mismatch → 不符 + 修复卡草稿)
    _card(gov, "completed", "K-5", criteria=["C5"], completed_at=_iso(-1),
          artifacts=[{"criterion": "C5", "path_glob": "list.png", "kind": "screenshot"}])
    _verdict(gov, "K-5")
    _artifact(gov, "K-5", "list.png", PNG)
    # C6 owner_check: K-6 卡面未声明产物 → 未验证(owner_check 须附截图)
    _card(gov, "completed", "K-6", criteria=["C6"], completed_at=_iso(-1))
    _verdict(gov, "K-6")
    _commit(hroot, "fixture: cards + gate records + artifacts")
    return {"home": hroot, "gov": gov, "ws": ws, "tmp": tmp_path}


def _status(gov: Path) -> dict[str, dict]:
    from tools.aipos_cli.roadmap_drift import roadmap_status

    view = roadmap_status(gov, project=PROJECT)
    assert view["errors"] == [], view["errors"]
    return {c["id"]: c for m in view["milestones"] for c in m["criteria"]}


def _verify(rig, *argv: str) -> tuple[int, str, str]:
    return _cli("roadmap", "verify", *rig["ws"], *argv)


# ===========================================================================
# ① 五态各一原文 + 缺产物停未验证
# ===========================================================================

def test_item1_five_states_and_missing_artifacts(rig):
    crit = _status(rig["gov"])
    for cid in ("C1", "C2", "C3", "C4", "C5", "C6"):
        c = crit[cid]
        _show(f"[①·{cid} {c['verify']}] state={c['state']}({c['state_label']}) satisfied={c['satisfied']}\n"
              f"        reason={c['reason']}\n        basis={c['basis']}")
    assert crit["C1"]["state"] == "auto_passed" and crit["C1"]["satisfied"] and "运行证据见 out/audit/K-1R/RETURN.md" in crit["C1"]["basis"]
    assert crit["C2"]["state"] == "auto_passed" and crit["C2"]["satisfied"]
    assert "产物 task_cards/K-2/artifacts/export-1.csv" in crit["C2"]["basis"]
    assert crit["C3"]["state"] == "unverified" and not crit["C3"]["satisfied"]
    assert "卡 K-3 声明的产物不在落点 task_cards/K-3/artifacts: [screenshot] export.png" == crit["C3"]["reason"]
    assert crit["C6"]["state"] == "unverified" and "卡面 artifacts 未声明标准 C6 的产物" in crit["C6"]["reason"]
    assert crit["C4"]["state"] == "owner_pending" and crit["C4"]["verify_target"] == "K-4" and not crit["C4"]["satisfied"]
    assert "等 Owner 看产物后抽查" in crit["C4"]["reason"]
    art = crit["C4"]["evidence"][0]["artifacts"]
    _show(f"[①·C4 产物解析] {json.dumps(art, ensure_ascii=False)}")
    assert [f["name"] for e in art["entries"] for f in e["files"]] == ["home.png", "steps.txt"]
    assert art["entries"][1]["files"][0]["excerpt"].startswith("1. 打开首页")

    # Owner 已确认 / 不符(经顾问命令记录)
    rc, out, err = _verify(rig, "--criterion", "C4", "--result", "confirm", *RELAY, "--confirm", "--actor", "owner")
    assert rc == 0, out + err
    rc, out, err = _verify(rig, "--criterion", "C5", "--result", "mismatch", "--relayed-by", ADVISOR, "--owner-words",
                           "列表页分页位置不对", "--note", "分页应在右下", "--confirm", "--actor", "owner")
    assert rc == 0, out + err
    crit = _status(rig["gov"])
    for cid in ("C4", "C5"):
        c = crit[cid]
        _show(f"[①·{cid} 记录后] state={c['state']}({c['state_label']}) satisfied={c['satisfied']}\n        basis={c['basis']}\n"
              f"        verifications={json.dumps(c['verifications'], ensure_ascii=False)}")
    assert crit["C4"]["state"] == "owner_confirmed" and crit["C4"]["satisfied"] and crit["C4"]["met_cards"] == ["K-4"]
    assert "Owner 判符合" in crit["C4"]["basis"] and f"经 {ADVISOR} 转达" in crit["C4"]["basis"] and "首页看过了, 符合" in crit["C4"]["basis"]
    assert crit["C5"]["state"] == "mismatch" and not crit["C5"]["satisfied"]
    assert crit["C5"]["fix_cards"] == [{"task_id": "K-5-oc1", "queue_state": None, "draft": True, "text": "K-5-oc1(草稿待发布)"}]
    assert "修复卡 K-5-oc1(草稿待发布)" in crit["C5"]["reason"]
    rc, out, err = _cli("roadmap", "status", *rig["ws"])
    _show(f"---- ① lybra roadmap status 原文(记录后) exit {rc} ----\n{out}{err}")
    assert rc == 0 and "✓ C4 [owner_check] 首页符合设计 — Owner 已确认(卡 K-4)" in out
    assert "· C5 [owner_check] 列表页符合设计 — 不符:" in out and "· C3 [artifact] 导出页有截图 — 未验证: 卡 K-3 声明的产物不在落点" in out
    assert "产物 K-4 [screenshot] home.png: task_cards/K-4/artifacts/home.png" in out
    assert "产物 K-3 [screenshot] export.png: (落点 task_cards/K-3/artifacts 无匹配文件)" in out
    # C1 C2 C4 满足
    assert "进度 [#####-----] 3/6" in out


def test_item1_fix_card_closed_returns_to_owner_pending(rig):
    """状态变化可追溯: 不符 → 修复卡(挂同一标准、引用抽查记录)结案审计通过 + 产物齐 → 回到待 Owner 抽查(复查), 历史记录不丢。"""
    rc, out, err = _verify(rig, "--criterion", "C5", "--result", "mismatch", "--relayed-by", ADVISOR, "--owner-words", "分页不对",
                           "--confirm", "--actor", "owner")
    assert rc == 0, out + err
    from tools.aipos_cli.draft_validator import expected_draft_relative_path
    from tools.aipos_cli.frontmatter import require_frontmatter

    meta, body = require_frontmatter(rig["gov"] / expected_draft_relative_path("K-5-oc1"))
    # 修复卡由顾问发布、执行、审计通过后结案(靶场直接按落点造结案卡 + 裁决 + 新截图)
    meta = {k: v for k, v in meta.items() if not k.startswith("draft_")}
    _card(rig["gov"], "completed", "K-5-oc1", criteria=meta["criteria"], completed_at=_iso(0),
          **{k: meta[k] for k in ("owner_check_ref", "artifacts")})
    _verdict(rig["gov"], "K-5-oc1")
    _artifact(rig["gov"], "K-5-oc1", "list.png", PNG)
    crit = _status(rig["gov"])
    c5 = crit["C5"]
    _show(f"[①·修复卡结案后 C5] state={c5['state']}({c5['state_label']}) target={c5['verify_target']}\n        basis={c5['basis']}\n"
          f"        历史 {[(v['task_id'], v['result'], v['decided_at']) for v in c5['verifications']]}")
    assert c5["state"] == "owner_pending" and c5["verify_target"] == "K-5-oc1" and "上次判不符后修复卡 K-5-oc1 已自动验证通过" in c5["basis"]
    assert [v["result"] for v in c5["verifications"]] == ["mismatch"]
    rc, out, err = _verify(rig, "--criterion", "C5", "--result", "confirm", "--relayed-by", ADVISOR, "--owner-words", "这回对了",
                           "--confirm", "--actor", "owner")
    assert rc == 0, out + err
    c5 = _status(rig["gov"])["C5"]
    _show(f"[①·复查后 C5] state={c5['state_label']} 历史 {[(v['task_id'], v['result']) for v in c5['verifications']]}")
    assert c5["state"] == "owner_confirmed" and [(v["task_id"], v["result"]) for v in c5["verifications"]] == [("K-5", "mismatch"),
                                                                                                             ("K-5-oc1", "confirm")]


# ===========================================================================
# ② 收件箱 owner_check + verify 两阶段 + 拒因
# ===========================================================================

def test_item2_inbox_owner_check_items_and_decide_redirect(rig):
    from tools.aipos_cli.owner_inbox import collect_inbox, render_inbox_text

    inbox = collect_inbox(rig["home"])
    items = [i for i in inbox["items"] if i["category"] == "owner_check"]
    text = render_inbox_text(inbox)
    _show(f"---- ② lybra owner inbox(owner_check)原文 ----\n{text}")
    assert sorted(i["subject"] for i in items) == ["标准 C4 待 Owner 抽查", "标准 C5 待 Owner 抽查"]
    c4 = next(i for i in items if i["subject"] == "标准 C4 待 Owner 抽查")
    assert c4["source"] == ADVISOR and "K-4[screenshot] task_cards/K-4/artifacts/home.png" in c4["ask"]
    assert "K-4[output] task_cards/K-4/artifacts/steps.txt" in c4["ask"]
    assert f"Owner 看完产物告诉 {ADVISOR}: 「C4 符合」或「C4 不符: <哪里不对>」" == c4["recommend"]
    assert "lybra roadmap verify --workspace-root" in c4["decide_line"] and "--criterion C4" in c4["decide_line"]
    assert c4["board_url"] == f"/owner/roadmap?project={PROJECT}&milestone=M1"
    # 未验证不进收件箱
    assert not [i for i in inbox["items"] if i["category"] == "owner_check" and "C6" in i["subject"]]
    rc, out, err = _cli("owner", "decide", "--item", c4["item_id"], "--choice", "owner_confirm", "--home-root", str(rig["home"]),
                        "--relayed-by", ADVISOR, "--owner-words", "符合", "--confirm")
    _show(f"---- ② 对 owner_check 事项敲 owner decide(拒, 指路 roadmap verify)exit {rc} ----\n{out}{err}")
    assert rc == 2 and "不经 owner decide 裁决" in err and "lybra roadmap verify" in err
    # 记录后事项自行消失
    rc, out, err = _verify(rig, "--criterion", "C4", "--result", "confirm", *RELAY, "--confirm", "--actor", "owner")
    assert rc == 0, out + err
    left = sorted(i["subject"] for i in collect_inbox(rig["home"])["items"] if i["category"] == "owner_check")
    _show(f"[②·C4 记录后收件箱 owner_check] {left}")
    assert left == ["标准 C5 待 Owner 抽查"]


def test_item2_verify_preview_confirm_and_landing(rig):
    before = _tree_digest(rig["home"])
    rc, out, err = _verify(rig, "--criterion", "C4", "--result", "confirm", *RELAY)
    _show(f"---- ② roadmap verify confirm 预演原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "roadmap verify C4: " in out and "Owner 抽查记录(将写): 5_tasks/records/owner_verifications/K-4/verify_K-4_" in out
    assert "标准状态: 待 Owner 抽查 → Owner 已确认" in out
    assert _tree_digest(rig["home"]) == before, "预演零写入"
    rc, out, err = _verify(rig, "--criterion", "C4", "--result", "confirm", *RELAY, "--confirm", "--actor", "owner")
    _show(f"---- ② roadmap verify confirm --confirm 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "Owner 抽查记录: 5_tasks/records/owner_verifications/K-4/verify_K-4_" in out
    assert "落账: verdict=PASS committed=True pushed=True" in out
    rec_path = next((rig["gov"] / "5_tasks" / "records" / "owner_verifications" / "K-4").glob("verify_K-4_*.md"))
    record = rec_path.read_text(encoding="utf-8")
    _show(f"---- ② 写出的 owner_verifications 记录原文 ----\n{record}")
    for line in ("record_type: owner_verification", "decision: approve", "decided_by: owner", f"workspace: {PROJECT}", "criterion: C4",
                 "result: confirm", f"relayed_by: {ADVISOR}", "owner_words: '夹具: 首页看过了, 符合'"):
        assert line in record, line
    log = _git(rig["home"], "log", "-1", "--format=%s%n%b", "--name-only")
    _show(f"---- ② 落账提交原文 ----\n{log}")
    assert "路线图变更落账 verify C4 f175-probe" in log and "Command: lybra roadmap verify --confirm" in log
    assert _git(rig["home"], "status", "--porcelain") == ""


def test_item2_mismatch_drafts_fix_card_on_same_criterion(rig):
    before = _tree_digest(rig["home"])
    argv = ("--criterion", "C5", "--result", "mismatch", "--relayed-by", ADVISOR, "--owner-words", "列表页分页位置不对",
            "--note", "分页应在右下")
    rc, out, err = _verify(rig, *argv)
    _show(f"---- ② roadmap verify mismatch 预演原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "修复卡草稿(将写): K-5-oc1 → 5_tasks/drafts/" in out and "标准状态: 待 Owner 抽查 → 不符" in out
    assert _tree_digest(rig["home"]) == before, "预演零写入"
    rc, out, err = _verify(rig, *argv, "--confirm", "--actor", "owner")
    _show(f"---- ② roadmap verify mismatch --confirm 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "修复卡草稿: K-5-oc1" in out and "draft publish --path 5_tasks/drafts/" in out
    from tools.aipos_cli.draft_validator import expected_draft_relative_path, validate_draft_file
    from tools.aipos_cli.frontmatter import require_frontmatter

    draft = rig["gov"] / expected_draft_relative_path("K-5-oc1")
    meta, body = require_frontmatter(draft)
    _show(f"---- ② 自动起草的修复卡草稿原文 ----\n{draft.read_text(encoding='utf-8')}")
    rec = next((rig["gov"] / "5_tasks" / "records" / "owner_verifications" / "K-5").glob("verify_K-5_*.md"))
    assert meta["milestone"] == "M1" and meta["criteria"] == ["C5"] and meta["owner_check_ref"] == rec.stem
    assert meta["created_by"] == ADVISOR and meta["assigned_to"] == EXEC
    assert meta["artifacts"] == [{"criterion": "C5", "path_glob": "list.png", "kind": "screenshot"}]
    assert "列表页分页位置不对" in body and "分页应在右下" in body and rec.relative_to(rig["gov"]).as_posix() in body
    record = rec.read_text(encoding="utf-8")
    assert "result: mismatch" in record and "decision: reject" in record and "fix_task_id: K-5-oc1" in record
    checked = validate_draft_file(rig["gov"], draft.relative_to(rig["gov"]).as_posix())
    _show(f"[②·草稿校验(顾问发布前预演同一判据)] verdict={checked['verdict']} blocking={checked['blocking_reasons']}")
    assert checked["verdict"] != "BLOCK", checked
    log = _git(rig["home"], "log", "-1", "--name-only", "--format=%s")
    _show(f"---- ② 落账提交(记录 + 草稿)原文 ----\n{log}")
    assert rec.relative_to(rig["home"]).as_posix() in log and draft.relative_to(rig["home"]).as_posix() in log


def test_item2_verify_refusals(rig):
    before = _tree_digest(rig["home"])
    rc, out, err = _verify(rig, "--criterion", "C4", "--result", "confirm", "--owner-words", "符合", "--confirm")
    _show(f"---- ② 缺 --relayed-by 拒原文 exit {rc} ----\n{out}{err}")
    assert rc == 2 and "--relayed-by" in err
    rc, out, err = _verify(rig, "--criterion", "C4", "--result", "confirm", "--relayed-by", ADVISOR, "--confirm")
    _show(f"---- ② 缺 --owner-words 拒原文 exit {rc} ----\n{out}{err}")
    assert rc == 2 and "--owner-words" in err
    rc, out, err = _verify(rig, "--criterion", "C1", "--result", "confirm", *RELAY, "--confirm")
    _show(f"---- ② auto 标准走抽查 拒原文 exit {rc} ----\n{err}")
    assert rc == 1 and "VERIFY_NOT_OWNER_CHECK" in err
    rc, out, err = _verify(rig, "--criterion", "C6", "--result", "confirm", *RELAY, "--confirm")
    _show(f"---- ② 未到待抽查(缺产物)拒原文 exit {rc} ----\n{err}")
    assert rc == 1 and "VERIFY_NOT_READY" in err and "未声明标准 C6 的产物" in err
    rc, out, err = _verify(rig, "--criterion", "C4", "--result", "confirm", *RELAY, "--card", "K-1", "--confirm")
    _show(f"---- ② --card 非该标准已通过卡 拒原文 exit {rc} ----\n{err}")
    assert rc == 1 and "VERIFY_CARD_INVALID" in err
    rc, out, err = _verify(rig, "--criterion", "ZZ", "--result", "confirm", *RELAY, "--confirm")
    assert rc == 1 and "VERIFY_CRITERION_UNKNOWN" in err
    assert _tree_digest(rig["home"]) == before, "被拒零写入"


def test_item2_artifact_declaration_refused_at_draft_and_amend(rig):
    from tools.aipos_cli.board_adapter import amend_task
    from tools.aipos_cli.draft_writer import create_draft

    meta = {"task_id": "K-9", "title": "probe", "project": PROJECT, "assigned_to": EXEC, "agent_instance": EXEC, "context_bundle": EXEC,
            "task_mode": "code", "priority": "high", "created_by": ADVISOR, "output_target": "src/", "artifact_policy": "formal_write",
            "milestone": "M1", "criteria": ["C4"],
            "artifacts": [{"criterion": "C5", "path_glob": "../x.png", "kind": "photo"}, {"criterion": "C4", "path_glob": "/etc/x"}]}
    res = create_draft(rig["gov"], dict(meta), "# K-9\n", dry_run=True)
    reasons = [r for r in res["blocking_reasons"] if "CARD_ARTIFACTS_INVALID" in r]
    _show(f"---- ② 卡稿 artifacts 不合 出卡拒原文 ----\n{reasons}")
    assert res["verdict"] == "BLOCK" and len(reasons) == 1
    for part in ("criterion 'C5' 不在本卡 criteria", "不得含 ..", "kind='photo' 不在值域", "artifacts[1] 缺 'kind'", "禁绝对路径"):
        assert part in reasons[0], part
    _card(rig["gov"], "pending", "K-8", criteria=["C4"])
    resp = amend_task(task_id="K-8", actor=ADVISOR, amendments={"artifacts": [{"criterion": "C9", "path_glob": "a.png", "kind": "screenshot"}]},
                      amendment_reason="probe", dry_run=True, repo_root=rig["gov"])
    _show(f"---- ② amend 卡面 artifacts 不合 拒原文 ---- {resp['blocking_reasons']}")
    assert resp["verdict"] == "BLOCK" and "CARD_ARTIFACTS_INVALID" in resp["blocking_reasons"][0]
    assert "criterion 'C9' 不在本卡 criteria" in resp["blocking_reasons"][0]


# ===========================================================================
# ③ 看板路线图页: 五态 + 产物缩略图 / 摘录(测试客户端 HTML)
# ===========================================================================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def board(rig):
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'], token_ref='owner-fixture')}"
    serve_root = rig["tmp"] / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=serve_root, home_root=rig["home"], session_store=store, connection_paths=[],
        board_audit_log_path=rig["tmp"] / "board-runtime" / "audit.jsonl"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**rig, "base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie}
    finally:
        server.shutdown()
        server.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def _request(b, path: str, method: str = "GET", *, cookie: bool = True) -> tuple[int, bytes, dict]:
    data = b"{}" if method != "GET" else None
    req = urllib.request.Request(b["base"] + path, data=data, method=method, headers={"Content-Type": "application/json"})
    if cookie:
        req.add_header("Cookie", b["cookie"])
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def _criterion_block(page: str, cid: str) -> str:
    return re.search(rf'<article class="vb-station rp-criterion[^"]*" data-criterion="{cid}".*?</article>', page, re.S).group(0)


def test_item3_board_roadmap_page_states_and_artifacts(board):
    import html as _html

    status, raw, _h = _request(board, f"/owner/roadmap?project={PROJECT}&milestone=M1")
    page = raw.decode("utf-8")
    assert status == 200
    states = dict(re.findall(r'<article class="vb-station rp-criterion[^"]*" data-criterion="(\w+)".*?data-state="(\w+)"', page, re.S))
    _show(f"[③·各标准 data-state] {states}")
    assert states == {"C1": "auto_passed", "C2": "auto_passed", "C3": "unverified", "C4": "owner_pending", "C5": "owner_pending",
                      "C6": "unverified"}
    c4 = _criterion_block(page, "C4")
    _show(f"---- ③ 标准 C4(待 Owner 抽查: 缩略图 + 摘录 + 告诉顾问 + 命令)HTML 原文 ----\n{c4}")
    plain = _html.unescape(c4)
    assert "验证状态:待 Owner 抽查" in plain
    img = f'/owner/artifact?project={PROJECT}&amp;task=K-4&amp;file=home.png'
    assert f'<img src="{img}" alt="task_cards/K-4/artifacts/home.png" loading="lazy">' in c4
    assert '<pre class="rp-artifact-excerpt">1. 打开首页\n2. 看顶部导航与卡片布局\n</pre>' in c4
    assert f"告诉 {ADVISOR}:看完标准 C4 的产物后,告诉我「C4 符合」或「C4 不符:哪里不对」" in plain
    assert "<code>lybra roadmap verify --workspace-root " in c4 and "--criterion C4 --result confirm|mismatch" in plain
    c3 = _html.unescape(_criterion_block(page, "C3"))
    assert "验证状态:未验证" in c3 and "落点 task_cards/K-3/artifacts 里没有匹配的文件" in c3 and "未满足:卡 K-3 声明的产物不在落点" in c3
    c2 = _html.unescape(_criterion_block(page, "C2"))
    assert "验证状态:自动验证通过" in c2 and "id,name\n1,alpha" in c2
    # 缩略图取图(只读 GET): 声明且在落点内的图 = 200 image/png; 未声明 / 越界 / 文本 / 未知项目 = 404
    status, body, headers = _request(board, img.replace("&amp;", "&"))
    _show(f"[③·取图 home.png] status={status} type={headers.get('Content-Type')} bytes={len(body)} nosniff={headers.get('X-Content-Type-Options')}")
    assert status == 200 and body == PNG and headers.get("Content-Type") == "image/png" and headers.get("X-Content-Type-Options") == "nosniff"
    for bad in (f"/owner/artifact?project={PROJECT}&task=K-4&file=secret.png",
                f"/owner/artifact?project={PROJECT}&task=K-4&file=../../K-5/artifacts/list.png",
                f"/owner/artifact?project={PROJECT}&task=K-4&file=steps.txt",
                f"/owner/artifact?project=nope&task=K-4&file=home.png"):
        status, body, _h = _request(board, bad)
        _show(f"[③·取图拒] {bad} → {status}")
        assert status == 404
    status, body, _h = _request(board, img.replace("&amp;", "&"), cookie=False)
    # 未登录 = 跳登录(与三页同一鉴权闸门)
    assert status == 302
    status, body, _h = _request(board, img.replace("&amp;", "&"), method="POST")
    _show(f"[③·取图 POST] {status} {body.decode('utf-8')[:160]}")
    assert status == 405

    # 记录后: Owner 已确认 / 不符 + 修复卡在页上可见
    rc, out, err = _verify(board, "--criterion", "C4", "--result", "confirm", *RELAY, "--confirm", "--actor", "owner")
    assert rc == 0, out + err
    rc, out, err = _verify(board, "--criterion", "C5", "--result", "mismatch", "--relayed-by", ADVISOR, "--owner-words", "分页不对",
                           "--confirm", "--actor", "owner")
    assert rc == 0, out + err
    status, raw, _h = _request(board, f"/owner/roadmap?project={PROJECT}&milestone=M1")
    page = raw.decode("utf-8")
    c4, c5 = _criterion_block(page, "C4"), _criterion_block(page, "C5")
    _show(f"---- ③ 记录后 标准 C4 HTML 原文 ----\n{c4}\n---- ③ 记录后 标准 C5 HTML 原文 ----\n{c5}")
    assert 'data-state="owner_confirmed"' in c4 and 'data-result="confirm"' in c4
    assert f"Owner 抽查:符合 · " in _html.unescape(c4) and f"经 {ADVISOR} 转达 · 原话「夹具: 首页看过了, 符合」" in _html.unescape(c4)
    assert 'data-state="mismatch"' in c5 and 'data-fix="K-5-oc1"' in c5 and "修复卡:K-5-oc1(草稿待发布)" in _html.unescape(c5)
    assert "rp-tell" not in c4 and "<form" not in page and "<button" not in page
    status, raw, _h = _request(board, f"/owner/roadmap?project={PROJECT}&milestone=M1&lang=en")
    en = _html.unescape(_criterion_block(raw.decode("utf-8"), "C4"))
    _show(f"[③·英文 C4 文本] {re.sub(r'<[^>]+>', ' ', en)[:300]}")
    assert "Verification: confirmed by Owner" in en and "Owner spot check: 符合" in en


# ===========================================================================
# ④ 章程渲染含新条款
# ===========================================================================

def test_item4_charters_render_new_clauses(rig):
    from tools.aipos_cli import charter_render as cr
    from tools.aipos_cli.workspace_config import project_paths

    return_root = str(project_paths(rig["gov"])["return_root"])
    for role, instance, marker in (("advisor", ADVISOR, "**验收产物与 Owner 抽查(AIPOS-F175)**"),
                                   ("auditor", AUDIT, "**卡面声明了验收产物(artifacts)**"),
                                   ("executor", EXEC, "**卡面声明了验收产物(artifacts)**")):
        text = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        rendered = cr.render_charter(text, cr.charter_render_context(rig["gov"], identity={
            "instance": instance, "project": PROJECT, "role": role, "harness_root": str(rig["gov"])}))
        lines = rendered.splitlines()
        start = next(i for i, ln in enumerate(lines) if marker in ln)
        end = next((j for j in range(start + 1, len(lines)) if re.match(r"\s*(- |\*\*|\d+\. )", lines[j]) or not lines[j].strip()), len(lines))
        clause = "\n".join(lines[start:end])
        _show(f"---- ④ 渲染后 {role} 章程新条款原文 ----\n{clause}")
        assert "{{" not in clause and f"{return_root}/<" in clause
        if role == "advisor":
            assert f"roadmap verify --criterion <标准id> --result confirm|mismatch --relayed-by {instance}" in clause
            assert f"lybra --workspace-root {rig['gov']} roadmap verify" in clause and '"artifacts": [' in clause
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    assert "#### `lybra roadmap verify`" in skill and "CARD_ARTIFACTS_INVALID" in skill


# ===========================================================================
# 声明 / 单一实现不变量
# ===========================================================================

def test_declarations_and_single_implementation(tmp_path):
    from tools.aipos_cli import owner_inbox, roadmap, roadmap_drift
    from tools.aipos_cli.owner_verification_writer import RESULT_DECISIONS, build_owner_verification_record
    from tools.schema_loader import load_schema

    decl = roadmap_drift.status_declaration()
    ver = decl["verification"]
    assert tuple(ver["states"]) == roadmap_drift.VERIFICATION_STATES
    assert list(ver["states"].values()) == ["未验证", "自动验证通过", "待 Owner 抽查", "Owner 已确认", "不符"]
    assert "verify_pending" not in decl
    vdecl = roadmap.verify_declaration()
    assert {k: v["decision"] for k, v in vdecl["results"].items()} == RESULT_DECISIONS
    assert "owner_check" in owner_inbox.CATEGORIES and "decide_redirect" in owner_inbox.inbox_declaration()["categories"]["owner_check"]
    two_phase = load_schema("verbs")["two_phase_protocol"]["roadmap_writers"]["commands"]
    assert tuple(two_phase) == roadmap.SUBCOMMANDS and "verify" in roadmap.SUBCOMMANDS
    art = roadmap.artifacts_declaration()
    assert art["field"] == "artifacts" and art["kinds"] == ["screenshot", "output", "report"]
    card = load_schema("card")
    assert "artifacts" in card["fields"] and "owner_check_ref" in card["fields"] and "artifacts" in card["advisor_zone"]["fields"]
    # 推导保持只读(五态并入 F173 唯一推导, 不写盘不起进程); 状态判定只在 _criterion_verification 一处
    src = Path(roadmap_drift.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert "write_text(" not in src and "subprocess" not in src and "open(" not in src
    assert src.count("def _criterion_verification(") == 1 and src.count("_criterion_verification(") == 2
    for literal in ("/home/", "2_projects", "chris", "kiwiai"):
        assert literal not in src, literal
    rsrc = Path(roadmap.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert rsrc.count("build_owner_verification_record(") == 2 and rsrc.count("create_draft(") == 1 and "land_written_paths(" in rsrc
    # 抽查记录不另立存储: 只经既有 writer + 既有落点; append-only 撞名拒写
    res = build_owner_verification_record(tmp_path, {"task_id": "K-1", "decision": "approve", "decided_via": "cli", "criterion": "C1",
                                                     "result": "confirm", "relayed_by": "a"}, dry_run=True)
    assert res["verdict"] == "BLOCK" and "Missing required field: owner_words" in res["blocking_reasons"]
    res = build_owner_verification_record(tmp_path, {"task_id": "K-1", "decision": "approve", "decided_via": "cli", "criterion": "C1",
                                                     "result": "mismatch", "relayed_by": "a", "owner_words": "w"}, dry_run=True)
    assert res["verdict"] == "BLOCK" and any("does not match decision" in r for r in res["blocking_reasons"])
    ok = build_owner_verification_record(tmp_path, {"task_id": "K-1", "decision": "approve", "decided_via": "cli"}, dry_run=False)
    assert ok["wrote"]
    again = build_owner_verification_record(tmp_path, {"task_id": "K-1", "decision": "approve", "decided_via": "cli"}, dry_run=False)
    if again["target_path"] == ok["target_path"]:
        assert again["verdict"] == "BLOCK" and not again["wrote"]
    # 看板只渲染: 渲染模块不读文件、不另判状态; 取图判定唯一在 roadmap.artifact_file
    render = (REPO_ROOT / "web" / "board" / "readonly_pages.py").read_text(encoding="utf-8")
    pages = (REPO_ROOT / "tools" / "aipos_cli" / "board_pages.py").read_text(encoding="utf-8")
    assert "artifact_file(" in pages and "artifact_file(" not in render and "card_artifacts(" not in render
    import importlib.util

    spec = importlib.util.spec_from_file_location("f175_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    assert guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / "readonly_pages.py") == []
