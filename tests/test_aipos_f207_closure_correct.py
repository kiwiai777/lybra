"""AIPOS-F207 — 结案口径统一与 Owner 纠正已结案卡(gap #213 / #214 / #215 / #216)。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; `lybra project new` 建靶场项目), 卡 / 门记录按声明落点造, 写命令走真实产品实现
(CLI main 本进程执行; 落账 = 真提交 + 推送到临时远端)。项目名 / 实例名 / 卡号均为靶场值(人肉期接入样本: 只写治理根的 docs / config 卡,
产品不写死)。全程不读真实凭据、不碰真实治理根、不对真实门写、不起 pi。

件① mark-concluded --outcome pass 满足依赖、block 不满足并给提示原文; F207 前存量(无 outcome、conclusion_note 以 BLOCK 起头)按 BLOCK 判;
    loop 结案出口与依赖判据同读(补跑顾问复核结案卡 = 已结案)。
件② queue correct 预演 / 确认: 决策记录、争议标记、标准状态退回、承接卡发布与 loop 命令原文; 原 closure / verdict 指纹不变; 依赖该卡的卡
    不再认原结案; 收件箱「已定待转达」话术; 重复纠正 / 非顾问 / 缺原话 拒。
件③ manual_required 而推导核建议 = 顾问复核结案 → loop status advisor_fix(收件箱无该项)。
件④ 打码: 可读连字符编号放行、真随机 40 字符串仍打码; owner-ref 拒因带人话出口。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  (single source of rig helpers)
from test_aipos_f171_roadmap_board_readonly import _cli as _cli_raw, _git  # noqa: E402

PROJECT = "f207-probe"              # rig project name (not a real project)
ADVISOR = f"advisor.{PROJECT}.h1"
EXEC = f"exec.{PROJECT}.h1"
AUDIT = f"audit.{PROJECT}.h1"
WORDS = "夹具: 批准纠正 LX-27 的误结案, 按正式流程重新派人补查 GitLab"


def _cli(*argv: str) -> tuple[int, str, str]:
    """CLI 本进程执行; 去掉治理根解析标注首行(「治理根: 项目 …」, governance_root_resolution.announce_commands)。"""
    rc, out, err = _cli_raw(*argv)
    return rc, "".join(ln for ln in out.splitlines(keepends=True) if not ln.startswith("治理根: ")), err


def _show(text: str) -> None:
    print(text, flush=True)


def _iso(delta_days: float) -> str:
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=delta_days))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _card(gov: Path, state: str, task_id: str, *, body: str | None = None, **extra) -> Path:
    from tools.aipos_cli.record_writer import render_markdown
    from tools.aipos_cli.task_loader import queue_root_for

    meta = {"task_id": task_id, "title": f"{task_id} probe", "project": PROJECT, "status": state, "task_mode": "docs",
            "assigned_to": EXEC, "agent_instance": EXEC, "context_bundle": EXEC, "priority": "high", "harness": "claude-code",
            "lane": {"repo": "governance_root", "paths": [f"task_cards/{task_id}/"], "roles": ["executor"]},
            "output_target": f"task_cards/{task_id}/artifacts/", "artifact_policy": "formal_write", "created_by": ADVISOR,
            "created_at": _iso(-3), **extra}
    path = queue_root_for(gov) / state / f"{task_id.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(meta, body or f"# {task_id}\n\n## 目标\n夹具正文\n"), encoding="utf-8")
    return path


def _record(gov: Path, kind: str, task_id: str, meta: dict) -> Path:
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix, render_markdown

    directory = record_dir(gov, kind, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record_file_prefix(kind)}_{task_id}_probe.md"
    path.write_text(render_markdown(meta, f"# {kind}\n"), encoding="utf-8")
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
    for argv in (("add-milestone", "--id", "M1", "--lane", "web", "--title", "测试版", "--status", "active"),
                 ("add-criterion", "--milestone", "M1", "--id", "C-SRC", "--statement", "资料领取链路完成只读核查", "--verify", "artifact")):
        rc, out, err = _cli("roadmap", argv[0], *ws, *argv[1:], "--confirm", "--actor", ADVISOR)
        assert rc == 0, f"{argv}\n{out}{err}"
    # 发布签发核验用的靶场连接文件(夹具 token, 非真实凭据; 只供 resolve_publish_signer 按实例挑条目)
    conn = gov / ".lybra" / "connection.json"
    conn.parent.mkdir(parents=True, exist_ok=True)
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": [
        {"role": "advisor", "role_class": "advisor", "agent_instance": ADVISOR, "token": "fixture-adv", "token_ref": "tr-adv",
         "scopes": []}]}), encoding="utf-8")
    _commit(hroot, "fixture: roadmap")
    return {"home": hroot, "gov": gov, "ws": ws}


def _closed_docs_card(gov: Path, task_id: str = "LX-27") -> dict[str, Path]:
    """已结案的只写治理根 docs 卡(审计 PASS_WITH_NOTES 后门生结案; 挂 M1 / C-SRC, 产物齐 = 标准自动验证通过)。"""
    from tools.aipos_cli.roadmap import card_artifact_dir

    body = (f"# {task_id}\n\n## 目标与授权\n通过 GitLab 核查平台侧资料领取链路。\n\n## 两项交付与验收\n"
            "1. artifacts/review.md\n\n## 工作纪律\n\n- **分支**: card/X(旧卡机器区, 承接卡须重新派生)\n")
    card = _card(gov, "completed", task_id, body=body, milestone="M1", criteria=["C-SRC"], completed_at=_iso(-1),
                 artifacts=[{"criterion": "C-SRC", "path_glob": "review.md", "kind": "report"}],
                 completion_report_ref=f"task_cards/{task_id}/RETURN.md", audit_by=AUDIT)
    art = card_artifact_dir(gov, task_id) / "review.md"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text("# review\n只查了本地三仓\n", encoding="utf-8")
    (gov / "task_cards" / task_id / "RETURN.md").write_text("# RETURN\n\n## 一句话结论\n完成\n", encoding="utf-8")
    ret = _record(gov, "returns", task_id, {"record_type": "return", "return_id": f"return_{task_id}_probe", "task_id": task_id})
    verdict = _record(gov, "audit_verdicts", task_id, {
        "record_type": "audit_verdict_record", "verdict_id": f"verdict_{task_id}_probe", "verdict": "PASS_WITH_NOTES",
        "reviewed_task_id": task_id, "audit_task_id": f"{task_id}R", "verdict_at": _iso(-1), "evidence_refs": []})
    closure = _record(gov, "closures", task_id, {"record_type": "closure", "closure_id": f"close_{task_id}_probe", "task_id": task_id,
                                                  "actor": EXEC, "closed_at": _iso(-1)})
    return {"card": card, "return": ret, "verdict": verdict, "closure": closure, "artifact": art}


def _criterion(gov: Path, cid: str) -> dict:
    from tools.aipos_cli.roadmap_drift import roadmap_status

    view = roadmap_status(gov, project=PROJECT)
    return next(c for m in view["milestones"] for c in m["criteria"] if c["id"] == cid)


# ===========================================================================
# 件① 顾问复核结案与门生 closure 对依赖判定等价
# ===========================================================================

def test_item1_mark_concluded_outcome_pass_and_block_dependency(rig):
    from tools.aipos_cli.task_complexity import closure_state, unmet_dependencies

    gov, ws = rig["gov"], rig["ws"]
    for tid in ("ENV-P", "ENV-B", "ENV-D"):
        _card(gov, "claimed", tid, claimed_by=EXEC, claim_id=f"claim_{tid}_x")
    _commit(rig["home"], "claimed env cards")
    cases = {"ENV-P": ("--outcome", "pass"), "ENV-B": ("--outcome", "block"), "ENV-D": ()}
    for tid, extra in cases.items():
        note = "BLOCK: 当时产品不支持候选绑定, 另出承接卡" if tid != "ENV-P" else "顾问复核: 产出清单逐项核对通过"
        rc, out, err = _cli(*ws, "mark-concluded", "--task-id", tid, "--actor", ADVISOR, "--conclusion-note", note, *extra)
        assert out.strip(), err
        data = json.loads(out)
        _show(f"[①] mark-concluded {tid} {' '.join(extra) or '(无 --outcome)'} rc={rc} outcome={data['data']['outcome']} "
              f"warnings={data['warnings']}")
        assert rc == 0 and data["data"]["outcome"] == (extra[1] if extra else "pass")
        hinted = any("mark-concluded 未给 --outcome, 按缺省 pass 记结论" in w and "请加 --outcome block" in w for w in data["warnings"])
        assert hinted is (not extra)
    # F207 前存量: 无 conclusion_outcome、conclusion_note 以 BLOCK 起头(chris 岚图 LANTU-ENV3 形)
    _card(gov, "completed", "ENV-L", auto_closed_by="AIPOS-354_mark_concluded", closed_by="conclusion_marker",
          conclusion_note="BLOCK:当时产品不支持环境卡绑定候选(gap #211), 另出 ENV-LB 承接; 未下载依赖。")
    states = {tid: closure_state(gov, tid) for tid in ("ENV-P", "ENV-B", "ENV-D", "ENV-L")}
    for tid, st in states.items():
        _show(f"[①] closure_state {tid}: kind={st['kind']} conclusion={st['conclusion']}")
    assert states["ENV-P"]["conclusion"]["outcome"] == "pass" and states["ENV-P"]["conclusion"]["source"] == "explicit"
    assert states["ENV-B"]["conclusion"] == {"outcome": "block", "source": "explicit", "note": "BLOCK: 当时产品不支持候选绑定, 另出承接卡",
                                             "satisfies": False}
    assert states["ENV-D"]["conclusion"]["outcome"] == "pass" and states["ENV-D"]["conclusion"]["source"] == "explicit"  # 缺省按 pass 记(提示过)
    assert states["ENV-L"]["conclusion"]["outcome"] == "block" and states["ENV-L"]["conclusion"]["source"] == "legacy_note"

    ok = unmet_dependencies({"task_id": "NEXT-1", "depends_on": ["ENV-P"]}, gov)
    _show(f"[①] 依赖 ENV-P(pass)= {ok}")
    assert ok == []
    blocked = unmet_dependencies({"task_id": "NEXT-2", "depends_on": ["ENV-B", "UI-5"], "dependency_condition": "closure"}, gov)
    _show("[①] 依赖 ENV-B(block)+ UI-5(未结案)拒因原文:\n  " + "\n  ".join(blocked))
    assert blocked[0].startswith("DEPENDENCY_CONCLUDED_BLOCK: 依赖卡 ENV-B 以 BLOCK 结案(顾问复核结案 mark-concluded; 结论来源 explicit")
    assert "依赖卡以 BLOCK 结案, 请改依赖为承接卡或移除(queue amend)" in blocked[0]
    assert f"lybra --workspace-root {gov} queue amend --task-id NEXT-2 --actor <你的顾问实例> --amendments '{{\"depends_on\": [\"UI-5\"]}}'" in blocked[0]
    assert blocked[1].startswith("DEPENDENCY_UNMET: 依赖 UI-5 未满足 closure")
    legacy = unmet_dependencies({"task_id": "ENV-LB", "depends_on": ["ENV-L"]}, gov)
    _show(f"[①] 存量 BLOCK 结案(无 outcome)依赖拒因: {legacy}")
    assert legacy and legacy[0].startswith("DEPENDENCY_CONCLUDED_BLOCK: 依赖卡 ENV-L 以 BLOCK 结案") and "结论来源 legacy_note" in legacy[0]
    assert "--amendments '{\"depends_on\": []}'" in legacy[0]

    # loop 结案出口与依赖判据同读: 顾问复核结案 = 已结案(补跑 loop 不再停在「任务已完成, 无下一步」)
    from tools.aipos_cli.loop_driver import _closure_kind

    assert _closure_kind(gov, "ENV-B") == "conclusion" and _closure_kind(gov, "NOPE-1") is None


# ===========================================================================
# 件② Owner 纠正已结案卡
# ===========================================================================

def test_item2_queue_correct_preview_confirm_followup(rig):
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.task_complexity import unmet_dependencies

    gov, ws, home = rig["gov"], rig["ws"], rig["home"]
    files = _closed_docs_card(gov)
    _card(gov, "pending", "LX-28", depends_on=["LX-27"], dependency_condition="closure")
    _commit(home, "closed LX-27 + dependent LX-28")
    before = {k: _sha(p) for k, p in files.items()}
    crit = _criterion(gov, "C-SRC")
    _show(f"[②] 纠正前标准 C-SRC: {crit['state_label']} — {crit['basis']}")
    assert crit["state"] == "auto_passed"
    assert unmet_dependencies({"task_id": "LX-28", "depends_on": ["LX-27"]}, gov) == []

    argv = (*ws, "queue", "correct", "--task-id", "LX-27", "--relayed-by", ADVISOR, "--owner-words", WORDS,
            "--reason", "原报告未查 GitLab, 审计误给 PASS_WITH_NOTES")
    rc, out, err = _cli(*argv)
    _show(f"---- ② queue correct 预演 rc={rc} ----\n{out}{err}")
    assert rc == 0 and out.startswith("预演(零写入): 纠正 LX-27 的结案并出承接卡 LX-27C1")
    assert "Owner 纠正决策(将写): owner-inbox-" in out and "承接卡(将写): LX-27C1" in out and "推进承接卡(顾问续跑): " in out
    assert "路线图标准 C-SRC: 自动验证通过 → (纠正后: 不符·待承接)" in out
    assert not (gov / "5_tasks" / "queue" / "pending" / "lx-27c1.md").exists()
    head = _git(home, "rev-parse", "HEAD")

    rc, out, err = _cli(*argv, "--confirm")
    _show(f"---- ② queue correct 确认 rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    assert out.startswith("已纠正: LX-27 的结案已标争议(原记录不改), 承接卡 LX-27C1 已发布")
    assert "路线图标准 C-SRC: 自动验证通过 → 不符" in out
    assert {k: _sha(p) for k, p in files.items()} == before  # 原 closure / verdict / return / 卡面 / 产物 指纹不变
    # 决策记录(owner decide 同一记录类型)
    decisions = sorted((gov / "5_tasks" / "records" / "owner_decisions").glob("owner-inbox-*.md"))
    dfm, dbody = require_frontmatter(decisions[-1])
    _show(f"[②] 决策记录 {decisions[-1].name}: decision_type={dfm['decision_type']} allowed_next_action={dfm['allowed_next_action']} "
          f"captured_by={dfm['captured_by']} task_id={dfm.get('task_id')}")
    assert dfm["decision_type"] == "owner_inbox_ruling" and dfm["allowed_next_action"] == "closed_card_correction"
    assert dfm["captured_by"] == ADVISOR and f"Owner 原话: {WORDS}" in dbody
    # 争议标记
    from tools.aipos_cli.task_complexity import closure_disputes

    disputes = closure_disputes(gov, "LX-27")
    _show(f"[②] 争议标记: {json.dumps(disputes, ensure_ascii=False)}")
    assert len(disputes) == 1 and disputes[0]["followup_task_id"] == "LX-27C1" and disputes[0]["decision_id"] == dfm["decision_id"]
    recorded = {d["path"]: d["sha256"] for d in disputes[0]["disputed_records"]}
    assert recorded[str(files["closure"].relative_to(gov))] == before["closure"]
    assert recorded[str(files["verdict"].relative_to(gov))] == before["verdict"]
    # 承接卡: 继承车道 / 标准 / 执行方式, 卡专属落点改指承接卡, 正文含 Owner 原话与原卡正文(旧机器区已去)
    pfm, pbody = require_frontmatter(gov / "5_tasks" / "queue" / "pending" / "lx-27c1.md")
    face = {k: pfm.get(k) for k in ("task_mode", "harness", "lane", "output_target", "milestone", "criteria", "owner_check_ref",
                                    "assigned_to", "audit_by", "artifacts", "created_by")}
    _show(f"[②] 承接卡卡面: {json.dumps(face, ensure_ascii=False)}")
    assert face["task_mode"] == "docs" and face["harness"] == "claude-code" and face["created_by"] == ADVISOR
    assert face["lane"]["repo"] == "governance_root" and face["lane"]["paths"] == ["task_cards/LX-27C1/"]
    assert face["output_target"] == "task_cards/LX-27C1/artifacts/" and face["milestone"] == "M1" and face["criteria"] == ["C-SRC"]
    assert face["owner_check_ref"] == dfm["decision_id"] and face["artifacts"] == [{"criterion": "C-SRC", "path_glob": "review.md",
                                                                                       "kind": "report"}]
    assert f"Owner 原话: 「{WORDS}」" in pbody and "原卡正文(承接范围)" in pbody and "通过 GitLab 核查平台侧资料领取链路" in pbody
    assert "card/X(旧卡机器区" not in pbody and pbody.count("## 工作纪律") == 1 and "card/LX-27C1" in pbody
    # 标准状态退回「不符·待承接」(承接卡在队列)
    crit = _criterion(gov, "C-SRC")
    _show(f"[②] 纠正后标准 C-SRC: {crit['state_label']} — {crit['reason']}")
    assert crit["state"] == "mismatch" and not crit["satisfied"]
    assert crit["fix_cards"] == [{"task_id": "LX-27C1", "queue_state": "pending", "draft": False, "text": "LX-27C1(pending)"}]
    assert "Owner 纠正卡 LX-27 的结案(不符·待承接" in crit["reason"] and "承接卡 LX-27C1(pending)" in crit["reason"]
    assert crit["reason"].count("Owner 纠正卡 LX-27 的结案") == 1
    # 依赖该卡的卡不再认原结案
    dep = unmet_dependencies({"task_id": "LX-28", "depends_on": ["LX-27"]}, gov)
    _show(f"[②] 依赖 LX-27 拒因: {dep}")
    assert dep and dep[0].startswith("DEPENDENCY_CLOSURE_DISPUTED: 依赖卡 LX-27 的结案已被 Owner 纠正")
    assert "'{\"depends_on\": [\"LX-27C1\"]}'" in dep[0]
    # 落账: 一次提交, 已推送
    log = _git(home, "log", "-1", "--format=%s")
    changed = _git(home, "show", "--name-only", "--format=", "HEAD").splitlines()
    _show(f"[②] 落账提交: {log}; 文件: {changed}")
    assert log == "chore(governance): 队列写落账 correct LX-27" and _git(home, "rev-parse", "HEAD") != head
    assert _git(home, "rev-parse", "HEAD") == _git(home, "rev-parse", "origin/main")
    assert any("owner_decisions" in f for f in changed) and any("closure_disputed_LX-27_" in f for f in changed)
    assert any(f.endswith("queue/pending/lx-27c1.md") for f in changed)
    # 收件箱「已定待转达」: 承接卡未开跑 = 转达话术
    from tools.aipos_cli.owner_inbox import collect_project_inbox, relay_groups, relay_lines

    inbox = collect_project_inbox(gov)
    relays = [r for r in inbox["relays"] if r.get("choice") == "closed_card_correction"]
    text = "\n".join(line for g in relay_groups(relays) for line in relay_lines(g))
    _show(f"[②] 已定待转达话术:\n{text}")
    assert relays and relays[0]["advisor"] == ADVISOR and "LX-27 → LX-27C1" in text and "loop" in text and "LX-27C1" in relays[0]["advisor_next"]
    assert not any(i.get("task_id") == "LX-27" for i in inbox["items"])

    # 重复纠正 = 拒(零写入)
    rc, out, err = _cli(*argv, "--confirm")
    _show(f"[②] 重复纠正 rc={rc}: {out.strip()}")
    assert rc == 1 and "CORRECT_ALREADY: 卡 LX-27 已纠正过" in out and "承接卡 LX-27C1" in out


def test_item2_queue_correct_refusals(rig):
    gov, ws, home = rig["gov"], rig["ws"], rig["home"]
    _closed_docs_card(gov)
    _card(gov, "pending", "LX-40")
    _commit(home, "closed + pending")
    head = _git(home, "rev-parse", "HEAD")
    rc, out, err = _cli(*ws, "queue", "correct", "--task-id", "LX-27", "--relayed-by", ADVISOR, "--confirm")
    _show(f"[②] 缺 --owner-words rc={rc}: {err.strip()}")
    assert rc == 2 and "--owner-words" in err
    rc, out, err = _cli(*ws, "queue", "correct", "--task-id", "LX-27", "--relayed-by", EXEC, "--owner-words", "x", "--confirm")
    _show(f"[②] 非顾问转达 rc={rc}: {out.strip()}")
    assert rc == 1 and out.startswith("纠正被拒(零写入): ROLE_VIOLATION")
    rc, out, err = _cli(*ws, "queue", "correct", "--task-id", "LX-40", "--relayed-by", ADVISOR, "--owner-words", "x", "--confirm")
    _show(f"[②] 未结案卡 rc={rc}: {out.strip()}")
    assert rc == 1 and "CORRECT_NOT_CLOSED: 卡 LX-40 在 pending" in out and "lybra queue supersede" in out
    assert _git(home, "rev-parse", "HEAD") == head and not (gov / "5_tasks" / "records" / "events" / "LX-27").exists()


# ===========================================================================
# 件③ 顾问复核结案的 manual_required 改判 advisor_fix
# ===========================================================================

def test_item3_manual_required_review_close_is_advisor_fix(rig, monkeypatch):
    from tools.aipos_cli import loop_run_record as lrr
    from tools.aipos_cli import next_resolver

    gov = rig["gov"]
    rules = lrr.next_action_declaration(lrr.run_record_declaration(__import__("tools.aipos_cli.loop_driver", fromlist=["x"])
                                                                  .load_loop_contract()))
    close_cmd = f"lybra --workspace-root {gov} mark-concluded --task-id ENV-3 --actor <你的顾问实例> --report-path task_cards/ENV-3/RETURN.md"
    derived = {"verb": "lybra_mark_concluded", "advisor_command": close_cmd, "suggested_action": "顾问复核产出清单 [a.md] 后结案(先加 --dry-run 预演)"}
    monkeypatch.setattr(next_resolver, "derive_next_step", lambda task_id, root: dict(derived))
    view = {"task_id": "ENV-3", "state": "ended", "end_reason": "manual_required", "end_message": "需人工介入/无法识别动词: …"}
    na = lrr.next_action(view, rules, governance_root=gov)
    _show(f"[③] manual_required + 推导核建议顾问复核结案 → {json.dumps(na, ensure_ascii=False)}")
    assert na["action"] == "advisor_fix" and na["reason"] == "advisor_review_close"
    assert na["hint"].startswith(f"先预演: {close_cmd} --dry-run") and "--outcome block" in na["hint"]
    derived.update(verb="", advisor_command="")  # 推导核此刻不再给该动词 = 照原判
    na = lrr.next_action(view, rules, governance_root=gov)
    _show(f"[③] 对照(推导核已不建议结案)→ {na['action']}({na['reason']})")
    assert na["action"] == "owner_needed" and na["reason"] == "manual_required"


def test_item3_inbox_has_no_review_close_item(rig, monkeypatch):
    """真实只写治理根卡交回后 loop 停在 manual_required: 收件箱无该项, loop status = advisor_fix 原文。"""
    from tools.aipos_cli import next_resolver
    from tools.aipos_cli.loop_run_record import loop_status
    from tools.aipos_cli.owner_inbox import collect_project_inbox
    from tools.aipos_cli.record_writer import render_markdown

    gov = rig["gov"]
    _card(gov, "claimed", "ENV-3", claimed_by=EXEC, claim_id="claim_ENV-3_x", task_mode="config")
    run_dir = gov / "5_tasks" / "records" / "loop_runs" / "ENV-3"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = "looprun_ENV-3_20261021_070256_1"
    (run_dir / f"{run_id}.md").write_text(render_markdown({
        "record_type": "loop_run", "run_id": run_id, "task_id": "ENV-3", "status": "ended", "driver": ADVISOR, "envelope": "pol_x",
        "host": "h", "pid": 1, "process_fingerprint": "starttime:1", "started_at": _iso(-0.1), "updated_at": _iso(-0.05),
        "log_path": str(run_dir / f"{run_id}.log"), "current_step": None, "steps": [], "launches": [], "ended_at": _iso(-0.05),
        "outcome": "not_derivable", "exit_code": 4, "end_reason": "manual_required",
        "end_message": "需人工介入/无法识别动词: 顾问复核产出清单 [x] 后结案(先加 --dry-run 预演) :: # 非代码卡", "end_codes": None,
        "write_errors": 0}, "# run\n"), encoding="utf-8")
    close_cmd = f"lybra --workspace-root {gov} mark-concluded --task-id ENV-3 --actor <你的顾问实例> --report-path task_cards/ENV-3/RETURN.md"
    monkeypatch.setattr(next_resolver, "derive_next_step", lambda task_id, root: {
        "verb": "lybra_mark_concluded", "advisor_command": close_cmd, "suggested_action": "顾问复核产出清单后结案"})
    report = loop_status(gov, "ENV-3")
    na = report["runs"][-1]["next_action"]
    _show(f"[③] loop status ENV-3 下一动作: {json.dumps(na, ensure_ascii=False)}")
    assert na["action"] == "advisor_fix" and na["reason"] == "advisor_review_close"
    inbox = collect_project_inbox(gov)
    _show(f"[③] 收件箱事项卡: {[i.get('task_id') for i in inbox['items']]}; errors={inbox['errors']}")
    assert not any(i.get("task_id") == "ENV-3" for i in inbox["items"])


# ===========================================================================
# 件④ 打码启发
# ===========================================================================

def test_item4_readable_identifier_not_redacted(rig):
    from tools.aipos_cli.harness_launch import redact_progress

    readable = "Owner-20261021-F205-ENV3B-recovery"
    token = secrets.token_urlsafe(30)[:40]
    while not (any(c.isupper() for c in token) and any(c.islower() for c in token) and any(c.isdigit() for c in token)):
        token = secrets.token_urlsafe(30)[:40]
    fixed = "Xk9fA2bQz7LmN4pR8sT1vW3yZ6cD0eG5hJ2kL9mN"
    _show(f"[④] {readable!r} → {redact_progress(readable)!r}")
    _show(f"[④] 随机 40 字符 → {redact_progress(fixed)!r}; 另一随机 → {redact_progress(token)!r}")
    assert redact_progress(readable) == readable
    assert redact_progress(f"owner_ref={readable} 已批准") == f"owner_ref={readable} 已批准"
    assert redact_progress(fixed) == "[已打码]" and redact_progress(token) == "[已打码]"
    assert redact_progress("Authorization: Bearer abc") == "[含凭据字样, 整行已隐去]"
    # owner-ref 等人读字段: 可读编号放行; 被拒时拒因带人话出口
    from tools.aipos_cli.validation_root import _single_line

    assert _single_line(readable, "--owner-ref") == readable
    with pytest.raises(Exception) as exc:
        _single_line(fixed, "--owner-ref")
    _show(f"[④] --owner-ref 随机串拒因: {exc.value}")
    assert "含凭据字样或长不透明串" in str(exc.value) and "带空格的人话" in str(exc.value) and "决策编号" in str(exc.value)


# ===========================================================================
# 声明 / 唯一实现不变量
# ===========================================================================

def test_declarations_single_source():
    from tools.aipos_cli.owner_inbox import inbox_declaration
    from tools.aipos_cli.queue_mutation import correct_declaration, correct_texts
    from tools.aipos_cli.task_complexity import conclusion_outcomes, dependency_gate_declaration
    from tools.schema_loader import code_repo_schema_root, load_schema

    decl = correct_declaration()
    assert decl["decision_choice"] in inbox_declaration()["options"] and correct_texts()["preview_line"]
    assert conclusion_outcomes() == ("pass", "block")
    gate = dependency_gate_declaration()
    assert gate["conditions"]["closure"]["accepts_conclusion"] is True and set(gate["disputes"]["record_kinds"]) <= set(decl["disputed_record_kinds"])
    card_fields = load_schema("card", code_repo_schema_root())["fields"]
    assert gate["conclusion"]["outcome_field"] in card_fields and decl["followup"]["ref_field"] in card_fields
    verbs = load_schema("verbs", code_repo_schema_root())["verbs"]
    assert verbs["lybra_queue_correct"]["cli_command"] == "lybra queue correct"
    assert "correct" in verbs["lybra_governance_commit"]["post_write_landing"]["commands"]["queue_writers"]["subcommands"]
    assert verbs["lybra_queue_correct"]["exit_codes"]["landing_failed"]["code"] == \
        verbs["lybra_governance_commit"]["post_write_landing"]["landing_failed_exit_code"]
    assert verbs["lybra_mark_concluded"]["parameters"]["properties"]["outcome"]["enum"] == list(conclusion_outcomes())
    review = verbs["lybra_loop_status"]["next_action"]["advisor_review_close"]
    assert review["verbs"] == ["lybra_mark_concluded"] and review["end_reasons"] == ["manual_required"]
    # 唯一实现: 修复卡与承接卡同一起草函数; 撤换与纠正同一决策构造
    src = (REPO_ROOT / "tools" / "aipos_cli" / "roadmap.py").read_text(encoding="utf-8")
    assert src.count("def followup_draft_payload(") == 1 and "return followup_draft_payload(" in src
    board = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    assert "followup_draft_payload(root" in board and "queue_ruling_payload(root" in board
    inbox_src = (REPO_ROOT / "tools" / "aipos_cli" / "owner_inbox.py").read_text(encoding="utf-8")
    assert "return queue_ruling_payload(" in inbox_src and inbox_src.count("def queue_ruling_payload(") == 1
