"""AIPOS-F173 — 路线图偏差推导与 CLI: 里程碑进度、游离卡、停滞、无卡覆盖标准、反复返工、逾期、路线图近期变更;
`lybra roadmap status`; 严重偏差进 Owner 收件箱(roadmap_drift 类别)。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; `lybra project new` 建靶场项目, 治理根在仓内子目录)。
路线图经产品命令 `lybra roadmap add-milestone / add-criterion --confirm` 写(真指针 + 真落账); 卡 / 门裁决记录 / 审计报告 /
loop 运行记录按声明落点造(记录落点 record_writer.record_dir, 文件名前缀 record_file_prefix, 运行记录 = loop 自身的 LoopRunRecorder)。
两个 lane(core / web)、多里程碑多标准; 时刻相对「现在」造, 不依赖墙钟日期。项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
全程不读真实凭据、不碰真实治理根、不对真实门写、不起进程。

验收(卡面 ★验收): ① 进度数值原文、六类偏差信号各一原文、阈值改配置后结果变化原文 ② `roadmap status` 文本与 --json 原文、
无 roadmap 项目提示原文 ③ 收件箱 roadmap_drift 项原文且同一偏差不重复原文 ⑥ 声明 / 单一实现不变量。
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 靶场构件唯一来源
from test_aipos_f171_roadmap_board_readonly import _cli, _git, _tree_digest  # noqa: E402

PROJECT = "f173-probe"              # 靶场项目名(非真实项目)
ADVISOR = f"advisor.{PROJECT}.h1"
OTHER_ADVISOR = f"advisor.{PROJECT}.h2"
RELAY = ("--relayed-by", "owner", "--owner-words", "夹具: Owner 本人裁决")


def _show(text: str) -> None:
    print(text, flush=True)


def _iso(delta_days: float) -> str:
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=delta_days))


def _day(delta_days: int) -> str:
    from tools.aipos_cli.clock import utc_now

    return (utc_now() + timedelta(days=delta_days)).date().isoformat()


def _card(gov: Path, state: str, task_id: str, *, created_days_ago: float, **extra) -> Path:
    from tools.aipos_cli.record_writer import render_markdown
    from tools.aipos_cli.task_loader import queue_root_for

    meta = {"task_id": task_id, "title": f"{task_id} probe", "project": PROJECT, "status": state, "task_mode": "code",
            "created_by": extra.pop("created_by", ADVISOR), "created_at": _iso(-created_days_ago), **extra}
    path = queue_root_for(gov) / state / f"{task_id.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(meta, f"# {task_id}\n"), encoding="utf-8")
    return path


def _verdict(gov: Path, task_id: str, verdict: str, *, report_text: str | None, days_ago: float) -> None:
    """门裁决记录(声明落点 audit_verdicts/<被审卡>/<前缀>_*.md)+ 其 evidence_refs 指向的审计报告。"""
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix, render_markdown

    ref = f"out/audit/{task_id}R/RETURN.md"
    if report_text is not None:
        (gov / ref).parent.mkdir(parents=True, exist_ok=True)
        (gov / ref).write_text(report_text, encoding="utf-8")
    directory = record_dir(gov, "audit_verdicts", task_id)
    directory.mkdir(parents=True, exist_ok=True)
    vid = f"{record_file_prefix('audit_verdicts')}_{task_id}_probe"
    (directory / f"{vid}.md").write_text(render_markdown(
        {"record_type": "audit_verdict_record", "verdict_id": vid, "verdict": verdict, "reviewed_task_id": task_id,
         "audit_task_id": f"{task_id}R", "verdict_at": _iso(-days_ago), "evidence_refs": [ref]}, "# verdict\n"), encoding="utf-8")


def _report(with_evidence: bool) -> str:
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration

    decl = runtime_evidence_declaration()
    body = "---\nverdict: PASS\n---\n# audit\n\n## 一句话结论\n通过\n"
    if with_evidence:
        body += (f"\n## {decl['section_heading']}\n\n```{decl['block_language']}\n"
                 '[{"command": "pytest -q", "worktree": "/w", "exit_code": 0, "log_excerpt": "1 passed"}]\n```\n')
    return body


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """home 根 = 治理仓(带 origin 裸仓); 治理根 = <home>/<PROJECT>; 路线图经产品命令写(真指针 + 落账), 卡与记录按声明落点造并提交推送。"""
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
        ("add-milestone", "--id", "M1", "--lane", "core", "--title", "核心链路可交付", "--status", "active", "--target-date", _day(-3)),
        ("add-criterion", "--milestone", "M1", "--id", "C1", "--statement", "接口单测全绿", "--verify", "auto"),
        ("add-criterion", "--milestone", "M1", "--id", "C2", "--statement", "回归脚本全绿", "--verify", "auto"),
        ("add-criterion", "--milestone", "M1", "--id", "C3", "--statement", "导出文件可下载", "--verify", "artifact"),
        ("add-criterion", "--milestone", "M1", "--id", "C4", "--statement", "错误码文档齐", "--verify", "auto"),
        ("add-milestone", "--id", "M2", "--lane", "core", "--title", "核心骨架", "--status", "done"),
        ("add-criterion", "--milestone", "M2", "--id", "C5", "--statement", "骨架可运行", "--verify", "auto"),
        ("add-milestone", "--id", "W1", "--lane", "web", "--title", "页面可浏览", "--status", "active", "--target-date", _day(30)),
        ("add-criterion", "--milestone", "W1", "--id", "W1C1", "--statement", "Owner 看过首页", "--verify", "owner_check"),
        ("add-criterion", "--milestone", "W1", "--id", "W1C2", "--statement", "首页渲染测试绿", "--verify", "auto"),
        ("add-milestone", "--id", "W2", "--lane", "web", "--title", "页面可编辑"),
        ("add-criterion", "--milestone", "W2", "--id", "W2C1", "--statement", "编辑保存成功", "--verify", "auto"),
    ]
    for argv in roadmap:
        rc, out, err = _cli("roadmap", argv[0], *ws, *argv[1:], "--confirm", "--actor", ADVISOR)
        assert rc == 0, f"{argv}\n{out}{err}"
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration

    evidence = {runtime_evidence_declaration()["card_field"]: {"required": True, "commands": ["pytest -q"]}}
    # core: K-1 结案 PASS 有运行证据节 → C1 满足; K-2 结案 PASS 但报告缺证据节 + 返工 2 轮 → C2 未满足 + 反复返工;
    #       K-3 结案 PASS, C3 verify=artifact 但卡面未声明产物 → 未验证(AIPOS-F175 五态); K-4 结案 PASS → M2.C5 满足; C4 无卡 → 无卡覆盖
    _card(gov, "completed", "K-1", created_days_ago=10, milestone="M1", criteria=["C1"], completed_at=_iso(-2), **evidence)
    _verdict(gov, "K-1", "PASS", report_text=_report(True), days_ago=2)
    rounds = [{"round": 1, "verdict_ref": "v1", "created_at": _iso(-6)}, {"round": 2, "verdict_ref": "v2", "created_at": _iso(-5)}]
    _card(gov, "completed", "K-2", created_days_ago=10, milestone="M1", criteria=["C2"], completed_at=_iso(-2), rework_rounds=rounds,
          **evidence)
    _verdict(gov, "K-2", "PASS_WITH_NOTES", report_text=_report(False), days_ago=2)
    _card(gov, "completed", "K-3", created_days_ago=10, milestone="M1", criteria=["C3"], completed_at=_iso(-2))
    _verdict(gov, "K-3", "PASS", report_text=_report(False), days_ago=2)
    _card(gov, "completed", "K-4", created_days_ago=40, milestone="M2", criteria=["C5"], completed_at=_iso(-30))
    _verdict(gov, "K-4", "PASS", report_text=_report(False), days_ago=30)
    # web: K-5 在途 20 天无推进 → W1 停滞(出卡人 = 另一顾问实例: 收件箱「告诉哪个顾问」); K-6 在途挂 planned 的 W2 → 状态不一致
    _card(gov, "claimed", "K-5", created_days_ago=20, milestone="W1", criteria=["W1C1", "W1C2"], claimed_at=_iso(-20),
          created_by=OTHER_ADVISOR)
    _card(gov, "claimed", "K-6", created_days_ago=0, milestone="W2", criteria=["W2C1"], claimed_at=_iso(-1))
    # 游离卡: K-7 / K-8 / K-9 近期出卡未挂; 审计卡 K-1R 不计入占比; 撤回卡 K-10 不计
    # AIPOS-F181: 只算建路线图之后出的卡(K-1…K-5 早于路线图 = 历史, 不计)→ 窗内 4 张(K-6 挂接 + K-7/8/9 游离)中 3 张 = 75% > 30%
    for tid in ("K-7", "K-8", "K-9"):
        _card(gov, "pending", tid, created_days_ago=0)
    _card(gov, "completed", "K-1R", created_days_ago=0, task_mode="audit", reviewed_task_id="K-1")
    _card(gov, "withdrawn", "K-10", created_days_ago=0)
    _git(hroot, "add", "-A")
    _git(hroot, "commit", "-q", "-m", "fixture: cards + gate records")
    _git(hroot, "push", "-q", "origin", "main")
    return {"home": hroot, "gov": gov, "ws": ws}


def _status(rig, *extra: str) -> tuple[int, str, str]:
    return _cli("roadmap", "status", *rig["ws"], *extra)


def _set_thresholds(gov: Path, **values) -> None:
    from tools.aipos_cli.workspace_config import project_json_path

    path = project_json_path(gov)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["roadmap_drift"] = values
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ===========================================================================
# ① 进度数值 + 六类偏差信号 + 阈值改配置
# ===========================================================================

def test_item1_progress_and_six_signals(rig):
    from tools.aipos_cli.roadmap_drift import roadmap_status

    view = roadmap_status(rig["gov"], project=PROJECT)
    by_id = {m["id"]: m for m in view["milestones"]}
    progress = {k: (m["progress"]["met"], m["progress"]["total"]) for k, m in by_id.items()}
    _show(f"[①·进度] {json.dumps(progress)} 合计 {view['totals']}")
    assert progress == {"M1": (1, 4), "M2": (1, 1), "W1": (0, 2), "W2": (0, 1)}
    crit = {c["id"]: c for m in view["milestones"] for c in m["criteria"]}
    for cid in ("C1", "C2", "C3", "C4", "C5", "W1C1", "W1C2", "W2C1"):
        _show(f"[①·标准] {cid} satisfied={crit[cid]['satisfied']} reason={crit[cid]['reason']}")
    assert crit["C1"]["satisfied"] and crit["C5"]["satisfied"]
    assert "无运行证据节" in crit["C2"]["reason"] and crit["C4"]["reason"] == "无挂接卡"
    # AIPOS-F175: artifact 标准的挂接卡未声明产物 = 停在未验证并写缺什么(原「待 F175」占位退役)
    assert crit["C3"]["state"] == "unverified" and "卡面 artifacts 未声明标准 C3 的产物" in crit["C3"]["reason"]
    assert "未结案" in crit["W1C1"]["reason"]
    assert by_id["W2"]["mismatch_codes"] == ["planned_moving"] and by_id["M2"]["mismatch"] == []
    kinds = {}
    for sig in view["signals"]:
        kinds.setdefault(sig["kind"], []).append(sig)
        _show(f"[①·信号] {sig['kind']} [{sig['severity']}] {sig['reason']}\n          建议: {sig['advice']}")
    assert set(kinds) == {"unlinked_ratio", "stalled", "uncovered", "rework", "overdue", "roadmap_changed"}
    assert [s["milestone"] for s in kinds["overdue"]] == ["M1"] and [s["milestone"] for s in kinds["stalled"]] == ["W1"]
    assert [s["criterion"] for s in kinds["uncovered"]] == ["C4"] and [s["criterion"] for s in kinds["rework"]] == ["C2"]
    ratio = kinds["unlinked_ratio"][0]
    assert ratio["facts"]["total"] == 4 and ratio["facts"]["unlinked"] == 3, ratio  # audit / withdrawn / pre-roadmap cards not counted (F181)
    assert kinds["roadmap_changed"][0]["facts"]["count"] == 12 and kinds["roadmap_changed"][0]["facts"]["last_by"] == ADVISOR
    assert {s["kind"] for s in view["signals"] if s["severity"] == "inbox"} == {"overdue", "stalled", "uncovered"}
    assert kinds["stalled"][0]["advisor"] == OTHER_ADVISOR and kinds["uncovered"][0]["advisor"] == ADVISOR
    assert view["errors"] == []


def test_item1_thresholds_from_project_json_change_result(rig):
    from tools.aipos_cli.roadmap_drift import roadmap_status
    from tools.aipos_cli.workspace_config import RoadmapDriftThresholdError

    before = sorted(s["kind"] for s in roadmap_status(rig["gov"])["signals"])
    _set_thresholds(rig["gov"], stall_days=30, rework_rounds=3, unlinked_ratio=0.8, change_window_days=1)  # F181: ratio now 75%
    view = roadmap_status(rig["gov"])
    after = sorted(s["kind"] for s in view["signals"])
    _show(f"[①·阈值] 改前信号 {before}\n[①·阈值] project.json roadmap_drift = stall_days 30 / rework_rounds 3 / unlinked_ratio 0.8 "
          f"→ 改后信号 {after}; 阈值 {view['thresholds']}")
    assert "stalled" in before and "rework" in before and "unlinked_ratio" in before
    assert after == ["overdue", "roadmap_changed", "uncovered"]
    assert view["thresholds"]["_source"]["stall_days"] == "project.json" and view["thresholds"]["_source"]["unlinked_window_days"] == "default"
    _set_thresholds(rig["gov"], stall_days=0, bogus=1)
    with pytest.raises(RoadmapDriftThresholdError) as exc:
        roadmap_status(rig["gov"])
    _show(f"[①·阈值不合] {exc.value}")
    assert "ROADMAP_DRIFT_INVALID" in str(exc.value) and "bogus" in str(exc.value) and "stall_days" in str(exc.value)


def test_item1_loop_run_record_counts_as_progress(rig):
    """停滞判定读 loop 运行记录时刻(loop 自身的记录器落一次运行 = 有推进): W1 不再停滞。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder
    from tools.aipos_cli.roadmap_drift import roadmap_status

    rec = LoopRunRecorder(rig["gov"], "K-5", driver=OTHER_ADVISOR, contract=load_loop_contract(), warn=_show)
    rec.end(outcome="gate_rejected", exit_code=2, reason="gate_rejected", message="fixture run")
    view = roadmap_status(rig["gov"])
    w1 = next(m for m in view["milestones"] if m["id"] == "W1")
    _show(f"[①·运行记录] K-5 落一次 loop 运行后 W1 最近推进 {w1['last_activity']}; 信号 {sorted(s['kind'] for s in view['signals'])}")
    assert "stalled" not in {s["kind"] for s in view["signals"]}


# ===========================================================================
# ② CLI 文本 / --json / --lane / 无路线图提示
# ===========================================================================

def test_item2_cli_text_json_lane_and_read_only(rig):
    before = _tree_digest(rig["home"])
    rc, out, err = _status(rig)
    _show(f"---- ② lybra roadmap status 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "路线图状态 f173-probe" in out and "进度 [##--------] 1/4" in out
    assert "[严重·进 Owner 收件箱] 里程碑逾期" in out and "[提示] 游离卡占比高" in out and "下一步建议:" in out
    assert "⚠ 声明 planned 但已有挂接卡在推进" in out
    rc, out, err = _status(rig, "--lane", "web")
    _show(f"---- ② roadmap status --lane web 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "[web] W1" in out and "[core]" not in out and "里程碑 2 个" in out
    rc, out, err = _status(rig, "--json")
    data = json.loads(out)
    _show(f"---- ② roadmap status --json(节选)exit {rc} ----\n" + json.dumps(
        {"milestones": [{k: m[k] for k in ("id", "lane", "status", "progress", "mismatch")} for m in data["milestones"]],
         "signals": [{k: s[k] for k in ("kind", "severity", "milestone", "criterion", "anchor")} for s in data["signals"]],
         "next_steps": data["next_steps"]}, ensure_ascii=False, indent=1))
    assert rc == 0 and data["exists"] and data["totals"] == {"met": 2, "total": 8}
    assert _tree_digest(rig["home"]) == before, "status 只读: 零写入"


def test_item2_no_roadmap_project_hint(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "AIPOS_WORKSPACE_ROOT", "LYBRA_WORKSPACE_ROOT", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", "bare-probe", "--home-root", str(hroot), "--actor", ADVISOR)
    assert rc == 0, out + err
    rc, out, err = _cli("roadmap", "status", "--workspace-root", str(hroot / "bare-probe"))
    _show(f"---- ② 无路线图项目 roadmap status 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "未声明路线图" in out and "add-milestone" in out and err == ""
    from tools.aipos_cli.owner_inbox import collect_inbox

    inbox = collect_inbox(hroot)
    assert inbox["errors"] == [] and not [i for i in inbox["items"] if i["category"] == "roadmap_drift"]


# ===========================================================================
# ③ Owner 收件箱 roadmap_drift: 出现、不重复、已裁决不再出现
# ===========================================================================

def test_item3_inbox_roadmap_drift_items_no_repeat(rig):
    from tools.aipos_cli.owner_inbox import collect_inbox, render_inbox_text

    first = collect_inbox(rig["home"])
    drift = [i for i in first["items"] if i["category"] == "roadmap_drift"]
    text = render_inbox_text(first)
    _show(f"---- ③ lybra owner inbox(roadmap_drift)原文 ----\n{text}")
    assert sorted(i["subject"] for i in drift) == ["标准 C4 无卡覆盖", "里程碑 M1 逾期", "里程碑 W1 停滞"]
    stalled = next(i for i in drift if i["subject"] == "里程碑 W1 停滞")
    assert stalled["source"] == OTHER_ADVISOR and "lybra roadmap set" in stalled["recommend"] and "路线图偏差" in text
    assert [o["key"] for o in stalled["options"]] == ["add_card", "replan", "acknowledge"]
    second = collect_inbox(rig["home"])
    again = [i for i in second["items"] if i["category"] == "roadmap_drift"]
    _show(f"[③·重算] 第一次 {sorted(i['item_id'] for i in drift)} / 第二次 {sorted(i['item_id'] for i in again)}")
    assert sorted(i["item_id"] for i in again) == sorted(i["item_id"] for i in drift) and len(again) == 3

    uncovered = next(i for i in drift if i["subject"] == "标准 C4 无卡覆盖")
    rc, out, err = _cli("owner", "decide", "--item", uncovered["item_id"], "--choice", "acknowledge", "--home-root", str(rig["home"]),
                        "--confirm", *RELAY)
    _show(f"---- ③ owner decide acknowledge --confirm 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0, out + err
    third = [i for i in collect_inbox(rig["home"])["items"] if i["category"] == "roadmap_drift"]
    _show(f"[③·已裁决不再出现] {sorted(i['subject'] for i in third)}")
    assert sorted(i["subject"] for i in third) == ["里程碑 M1 逾期", "里程碑 W1 停滞"]
    # 真相变了(改目标日)= 新事项; 改到未来 = 逾期自行解除
    rc, out, err = _cli("roadmap", "set", *rig["ws"], "--id", "M1", "--field", "target_date", "--value", _day(10), "--confirm",
                        "--actor", ADVISOR)
    assert rc == 0, out + err
    fourth = [i for i in collect_inbox(rig["home"])["items"] if i["category"] == "roadmap_drift"]
    _show(f"[③·改目标日后] {sorted(i['subject'] for i in fourth)}")
    assert sorted(i["subject"] for i in fourth) == ["里程碑 W1 停滞"]


def test_item3_unreadable_roadmap_named_in_inbox_errors(rig):
    from tools.aipos_cli.owner_inbox import collect_inbox
    from tools.aipos_cli.roadmap import roadmap_file

    roadmap_file(rig["gov"]).write_text("milestones:\n  - id: M1\n    bogus: 1\n", encoding="utf-8")
    inbox = collect_inbox(rig["home"])
    errs = [e for e in inbox["errors"] if e["source"] == "路线图偏差"]
    _show(f"[③·路线图读不出] {errs}")
    assert errs and "ROADMAP_INVALID" in errs[0]["error"]
    rc, out, err = _cli("roadmap", "status", *rig["ws"])
    _show(f"[③·status 读不出] exit {rc} {err.strip()}")
    assert rc == 1 and "ROADMAP_INVALID" in err


# ===========================================================================
# ⑥ 声明 / 单一实现不变量
# ===========================================================================

def test_declarations_and_single_implementation():
    from tools.aipos_cli import owner_inbox, roadmap_drift
    from tools.aipos_cli.workspace_config import roadmap_drift_thresholds
    from tools.schema_loader import load_schema

    decl = roadmap_drift.status_declaration()
    assert set(decl["signals"]) == set(roadmap_drift.SIGNAL_KINDS)
    inbox_kinds = {k for k, v in decl["signals"].items() if v["severity"] == "inbox"}
    cat = owner_inbox.inbox_declaration()["categories"]["roadmap_drift"]
    assert inbox_kinds == set(cat["rank_by_kind"]) == set(cat["subject_by_kind"]) == {"overdue", "stalled", "uncovered"}
    assert "roadmap_drift" in owner_inbox.CATEGORIES
    pj = load_schema("config")["configuration_sources"]["project_json"]["schema"]["roadmap_drift"]
    defaults = {k: v["default"] for k, v in pj["schema"].items()}
    got = roadmap_drift_thresholds(Path("/nonexistent-f173"), project={})
    assert {k: v for k, v in got.items() if k != "_source"} == defaults
    src = Path(roadmap_drift.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]
    # 游离判定复用 F171 唯一函数(card_links → card_link_status); 门记录读 next_resolver 唯一读取; 不写盘
    assert "card_links(" in body and "_read_task_records(" in body and "pointer_index(" in body
    assert "write_text(" not in body and "subprocess" not in body and "open(" not in body
    for literal in ("/home/", "2_projects", "chris", "kiwiai"):
        assert literal not in body, f"产品代码写死 {literal!r}"
    # 收件箱 / 看板 / CLI 同读一个推导函数
    from tools.aipos_cli import aipos_cli, board_adapter

    assert "roadmap_drift.roadmap_status(" in Path(aipos_cli.__file__).read_text(encoding="utf-8")
    assert "roadmap_status(resolved_root" in Path(board_adapter.__file__).read_text(encoding="utf-8")
    assert "roadmap_status(root, project=project)" in Path(owner_inbox.__file__).read_text(encoding="utf-8")


def test_board_adapter_same_data(rig):
    from tools.aipos_cli.board_adapter import get_roadmap_status
    from tools.aipos_cli.roadmap_drift import json_view, roadmap_status

    resp = get_roadmap_status(rig["gov"], lane="core")
    _show(f"[看板适配器] ok={resp['ok']} summary={resp['summary']}")
    assert resp["ok"] and resp["summary"]["milestones"] == 2
    direct = json_view(roadmap_status(rig["gov"], lane="core"))
    assert [m["progress"] for m in resp["data"]["milestones"]] == [m["progress"] for m in direct["milestones"]]
