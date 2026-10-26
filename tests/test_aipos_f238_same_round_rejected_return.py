"""AIPOS-F238 — 交回入门被拒时顾问补正要点续接原执行体(10-26 chris 理想 HBLX-85 实撞)。

实撞: pi 执行的 config 卡交回两次被产物入口拒(INGEST_CODE_FILE_IN_SLOT / INGEST_PROGRAM_IN_NON_CODE_CARD), loop 按 F236 同会话续接 1/1 次后
以 return_rejected 结束。顾问复核报告又发现两处须同轮纠正(把网页登录当成维护接口验证; 报告正文含测试凭据), 想按 F230 同轮补正交代要点,
`mark-concluded --outcome same_round` 预演却拒 SAME_ROUND_NO_RETURN(产物入口拒收不落门交回); loop 续接 pi 的修产物指令只含拒因。

件① 同轮补正接受「入门被拒的交回」为所审对象(复核记录 reviewed_rejection_ref + 复核时刻产物指纹 / 报告 sha256); 两者都没有才拒。
件② 推导 await_rework(原文给拒因 + 顾问要点); loop 对 pi 卡同会话续接原执行体(指令 = 修产物指令 + 要点 + 验收, 一条声明模板), 不占 F236
    修产物额度; 会话不可续接 = 结束原文给可派发的同一条完整指令。
件③ 重交不放宽: 仍被程序判据拒 = 新拒收记录(可再次同轮补正); 改对后入门 = 派审, 审计卡带复审说理(顾问要点)。
件④ next 在 return_rejected 状态的出口全集含同轮补正。

靶场 = AIPOS-F90 / F196 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 假 pi / 假 harness)。禁真门 / 真治理根 / 真工位 /
真 chris / 真 pi。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402  — LANTU-DEMO1 形态 config 卡构件
import test_aipos_f236_return_rejection_exits as f236  # noqa: E402  — 交回拒收构件
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f169_pi_model_declaration import _real_pi_template, install_fake_pi  # noqa: E402
from test_aipos_f196_validation_to_pi import POLICY_V, vrig  # noqa: E402,F401  — pi 靶场夹具
from test_aipos_f197_noncode_pi_completion import _gov_lane, _real_finalize_for  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli import artifact_ingest as ai  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402

CARD = f221.CARD
PROG = "INGEST_PROGRAM_IN_NON_CODE_CARD"
POINTS = ["报告正文含测试凭据, 须脱敏后重写", "验证对象错: 把网页登录当成维护接口验证, 改为验证维护接口"]
CRITERIA = ["报告正文无明文凭据", "验证段落写维护接口请求与返回"]
PI_CARD = "PROBE-F238-P"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _mark(gov: Path, card: str, *extra: str) -> tuple[int, dict, str, str]:
    rc, out, err = _cli(["--workspace-root", str(gov), "mark-concluded", "--task-id", card, "--actor", DRIVER, "--report-path",
                         f"task_cards/{card}/RETURN.md", "--outcome", "same_round", "--focus-items", json.dumps(POINTS, ensure_ascii=False),
                         "--acceptance-criteria", json.dumps(CRITERIA, ensure_ascii=False), "--json", *extra])
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = {}
    return rc, payload, out, err


def _ingest(gov: Path, card: str) -> tuple[int, str]:
    rc, out, err = _cli(["artifact", "ingest", "--task-id", card, "--kind", "return", "--workspace-root", str(gov)])
    return rc, out + err


# ===========================================================================
# 件① 写口: 无交回也无拒收 = 仍拒(新措辞); 交回被拒后 = 以拒收记录为所审对象(预演 / 落写); 同一拒收不重复打回
# 件④ next 在 return_rejected 状态的出口全集含同轮补正
# 件② 推导 await_rework(拒因 + 要点); 件⑤ 子 agent 卡 loop = 结束原文给可派发完整指令(被拒报告不入门)
# 件③ 重交仍被拒 = 新拒收记录(复核已消费, 可再次同轮补正); 改对后入门 = 门交回(同轮补正已消费)
# ===========================================================================

def test_item1_item4_item2_item5_item3_subagent_config_card(rig):  # noqa: F811
    f236._config_card(rig)
    init_governance_repo(rig.gov)
    from tools.aipos_cli.board_adapter import mark_concluded_task

    none = mark_concluded_task(task_id=CARD, actor=DRIVER, report_path=f"task_cards/{CARD}/RETURN.md", dry_run=True, repo_root=rig.gov,
                               outcome="same_round", focus_items=json.dumps(POINTS, ensure_ascii=False))
    _show("---- 件① 无门交回也无拒收记录: same_round 预演原文 ----\n" + json.dumps(none.get("errors"), ensure_ascii=False))
    assert not none["ok"] and "SAME_ROUND_NO_RETURN" in json.dumps(none, ensure_ascii=False)
    assert "无门交回也无交回拒收记录" in json.dumps(none, ensure_ascii=False)

    f221._report(rig, "v1")
    _program(rig.gov / "task_cards" / CARD / "artifacts" / "demo-launcher")
    rc, text = _ingest(rig.gov, CARD)
    assert rc != 0 and PROG in text
    rejection = ai.return_rejection_records(rig.gov, CARD)[-1]
    assert rejection["rejection_id"] == f"return_rejected_{CARD}_1"

    # 件④ next 出口全集含同轮补正(带顾问要点续接原执行体)
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "next", "--task-id", CARD])
    exits_text = out[out.index("Exits("):]
    _show(f"---- 件④ lybra next --task-id {CARD}(return_rejected)出口全集原文 exit {rc} ----\n{exits_text}")
    assert "[same_round_fix] 同轮补正(带顾问要点续接原执行体)" in exits_text and "--outcome same_round" in exits_text
    assert "--dry-run(预演通过后去掉 --dry-run 落写), 然后原命令续跑 lybra loop" in exits_text
    assert "另有顾问要点(入门判据外须同轮纠正的问题)= 走 [same_round_fix] 同轮补正" in exits_text
    keys = [e["key"] for e in nr.derive_next_step(CARD, rig.gov)["legal_exits"]["exits"]]
    assert keys == ["fix_resubmit", "same_round_fix", "new_subject", "withdraw", "change_scope"]

    # 件① 预演: 所审对象 = 拒收记录(rejection_id + 指纹), 零写入
    rc, dry, out, err = _mark(rig.gov, CARD, "--dry-run")
    _show(f"---- 件① same_round --dry-run(所审对象 = 拒收记录)exit {rc} ----\n"
          + json.dumps(dry.get("data", {}).get("review_record"), ensure_ascii=False, indent=1)[:2500] + err)
    assert rc == 0 and dry["ok"], out + err
    rr = dry["data"]["review_record"]
    assert rr["subject"] == "rejection" and rr["reviewed_rejection_ref"] == rejection["rejection_id"]
    assert rr["frontmatter"]["reviewed_return_record_ref"] is None
    assert rr["frontmatter"]["reviewed_fingerprint"] == ai.return_fingerprint(rig.gov, CARD, rejection["files"]) == rejection["fingerprint"]
    assert nr.same_round_reviews(rig.gov, CARD) == [], "预演零写入"
    # 落写
    rc, done, out, err = _mark(rig.gov, CARD)
    path = rig.gov / done["data"]["review_record"]["path"]
    _show(f"---- 件① same_round 落写 exit {rc}: 复核记录 {done['data']['review_record']['path']} ----\n" + path.read_text(encoding="utf-8"))
    assert rc == 0 and done["ok"] and "入门被拒的交回 return_rejected_" in path.read_text(encoding="utf-8")
    assert not (nr._read_frontmatter(nr._find_task_in_queue(rig.gov, CARD)[0]).get("rework_rounds") or []), "同轮补正不登记返工轮"
    again = mark_concluded_task(task_id=CARD, actor=DRIVER, dry_run=True, repo_root=rig.gov, outcome="same_round", focus_items='["x"]')
    _show("[件① 同一拒收记录重复同轮补正] " + json.dumps(again.get("errors"), ensure_ascii=False))
    assert not again["ok"] and "SAME_ROUND_ALREADY_PENDING" in json.dumps(again, ensure_ascii=False)

    # 件② 推导: 等执行体带拒因 + 顾问要点补正(不给交回命令, 被拒报告不入门)
    d = nr.derive_next_step(CARD, rig.gov)
    _show("---- 件② 同轮补正后 lybra next 原文 ----\n" + nr.format_output(d))
    assert not d["derivable"] and d["command"] == "" and d["action"]["type"] == "await_rework"
    assert d["action"]["rejection_id"] == rejection["rejection_id"] and d["current_node"] == "same_round"
    assert PROG in d["suggested_action"] and all(p in d["suggested_action"] for p in POINTS) and "判据不放宽" in d["suggested_action"]

    # 件⑤ 子 agent 卡: loop 不执行交回命令, 以 return_rejected 结束, 结束原文给可派发的同一条完整指令
    calls = len([c for c in rig.cli if c[1:3] == ["artifact", "ingest"]])
    res, text = f221._loop(rig, [], lambda r: None)
    _show(f"---- 件⑤ 子 agent 卡同轮补正后 loop exit {res.exit_code} reason {res.reason} ----\n{res.message}")
    assert res.reason == "return_rejected" and res.outcome == "not_derivable"
    assert "同轮补正指令(会话不可续接时顾问派给本卡执行实例): 你的交回 " in res.message
    assert f"被产物入口拒收 {PROG}" in res.message and all(p in res.message for p in POINTS + CRITERIA)
    assert "合法出口(return_rejected × governance_outputs" in res.message
    assert len([c for c in rig.cli if c[1:3] == ["artifact", "ingest"]]) == calls, "被拒报告不入门(不执行交回命令)"
    fixes = f236._fixes(rig.gov)
    assert [(f["outcome"], f.get("kind"), f.get("review_id")) for f in fixes] == [("not_resumable", "same_round", d["action"]["same_round"])]

    # 件③ 执行体按要点改写报告但程序仍在 = 照常经产物入口重交, 仍被拒 = 新拒收记录(复核已消费, 可再次同轮补正)
    report = rig.gov / "task_cards" / CARD / "RETURN.md"
    report.write_text(report.read_text(encoding="utf-8") + "\n## 同轮补正\n凭据已脱敏; 验证维护接口。\n", encoding="utf-8")
    d = nr.derive_next_step(CARD, rig.gov)
    assert d["derivable"] and d["command"].startswith(f"lybra artifact ingest --task-id {CARD} --kind return"), d
    rc, text = _ingest(rig.gov, CARD)
    second = ai.return_rejection_records(rig.gov, CARD)[-1]
    _show(f"---- 件③ 补正后重交仍被程序判据拒 exit {rc}: 新拒收记录 {Path(second['_path']).name} ----\n"
          + Path(second["_path"]).read_text(encoding="utf-8")[:1500])
    assert rc != 0 and second["rejection_id"] == f"return_rejected_{CARD}_2" and second["category"] == PROG
    assert nr.same_round_state(rig.gov, CARD, None) is None, "又落新拒收记录 = 本次复核已消费"
    assert nr.derive_next_step(CARD, rig.gov)["action"]["type"] == "return_rejected"
    redo = mark_concluded_task(task_id=CARD, actor=DRIVER, dry_run=True, repo_root=rig.gov, outcome="same_round", focus_items='["再补一处"]')
    assert redo["ok"] and redo["data"]["review_record"]["reviewed_rejection_ref"] == second["rejection_id"], redo
    # 改对后入门 = 门交回(同轮补正已消费, 回常规推进)
    (rig.gov / "task_cards" / CARD / "artifacts" / "demo-launcher").unlink()
    rc, text = _ingest(rig.gov, CARD)
    _show(f"[件③ 程序移出后重交] exit {rc}")
    assert rc == 0, text
    assert nr._read_task_records(rig.gov, CARD)["latest_return"] and nr.same_round_state(rig.gov, CARD, None) is None


def _program(path: Path) -> Path:
    return f236._program(path, 400)


def test_item1_gate_return_older_than_rejection_is_rejection_subject(rig):  # noqa: F811
    """门最新交回早于最新拒收记录 = 所审对象是拒收记录; 门交回晚于拒收 = 所审对象是门交回(行为同 F230)。"""
    f236._config_card(rig)
    f221._report(rig, "v1")
    rec_dir = rig.gov / "5_tasks" / "records" / "returns" / CARD
    _write(rec_dir / f"return_{CARD}_old.md", f78._fm({"record_type": "return_record", "return_id": f"return_{CARD}_old", "task_id": CARD,
                                                       "returned_at": "2026-01-01T00:00:00Z"}))
    ai.write_return_rejection(rig.gov, CARD, category=PROG, reasons=["夹具"], return_path=f"task_cards/{CARD}/RETURN.md")
    got = nr.same_round_rejection_subject(rig.gov, CARD)
    _show(f"[件① 门交回 2026-01-01 早于拒收] 所审对象 = {got and got['rejection_id']}")
    assert got is not None and got["rejection_id"] == f"return_rejected_{CARD}_1"
    _write(rec_dir / f"return_{CARD}_new.md", f78._fm({"record_type": "return_record", "return_id": f"return_{CARD}_new", "task_id": CARD,
                                                       "returned_at": "2999-01-01T00:00:00Z"}))
    assert nr.same_round_rejection_subject(rig.gov, CARD) is None


# ===========================================================================
# 件②③ pi 卡(HBLX-85 形态): 首跑交回被拒 → F236 续接未修 → return_rejected; 顾问同轮补正 → 续跑 loop 同会话续接(拒因 + 要点 + 验收)
# → 重交仍被程序判据拒(新拒收记录)→ F236 修产物续接(额度未被同轮补正占用)→ 入门 → 派审(审计卡带复审说理)→ 结案
# ===========================================================================

def _pi_config_card(gov: Path) -> Path:
    return f78._card(gov, PI_CARD, "pending", assigned=EXEC, task_mode="config",
                     extra={"harness": "pi", "audit": "required", "audit_by": AUDITOR, "output_target": f"task_cards/{PI_CARD}/artifacts/",
                            "lane": _gov_lane(PI_CARD)})


def _pi_fixes(gov: Path) -> list[list[dict]]:
    paths = sorted((gov / "5_tasks" / "records" / "loop_runs" / PI_CARD).glob("looprun_*.md"))
    return [list(nr._read_frontmatter(p).get("report_fixes") or []) for p in paths]


def test_item2_item3_pi_card_same_session_resume_then_admitted_and_audited(vrig, monkeypatch):
    gov = vrig.gov
    _real_pi_template(monkeypatch)
    install_fake_pi(vrig.home / "fakepi", monkeypatch)
    monkeypatch.setattr(f90, "_finalize_double", _real_finalize_for({PI_CARD}))
    _pi_config_card(gov)
    init_governance_repo(gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "docs_same_round")
    monkeypatch.setenv("FAKE_AUDIT_MODE", "work")
    monkeypatch.setenv("FAKE_SAME_ROUND_EXPECT", "|".join(POINTS + CRITERIA))
    policy = f"{POLICY_V}_config"

    out = io.StringIO()
    res = run_loop(PI_CARD, gov, actor=DRIVER, policy_id=policy, out=out, interval=0.05, max_wait=60, max_steps=20)
    _show(f"---- 首跑(HBLX-85 形态): loop exit {res.exit_code} reason {res.reason} ----\n{res.message[:1200]}")
    assert res.reason == "return_rejected" and "续接后报告未改写" in res.message
    assert [(f["outcome"], f.get("kind")) for f in _pi_fixes(gov)[-1]] == [("not_rewritten", None)]
    first = ai.return_rejection_records(gov, PI_CARD)
    assert [r["category"] for r in first] == [PROG]

    rc, done, mout, merr = _mark(gov, PI_CARD)
    assert rc == 0 and done["ok"], mout + merr
    review_id = done["data"]["review_record"]["review_id"]

    out = io.StringIO()
    res = run_loop(PI_CARD, gov, actor=DRIVER, policy_id=policy, out=out, interval=0.05, max_wait=60, max_steps=40)
    text = out.getvalue()
    lines = [ln for ln in text.splitlines() if any(k in ln for k in ("同轮补正", "report_fix:", "交回被产物入口拒收", "run return", "exit ", "done:"))]
    _show(f"---- 续跑 loop(同轮补正续接 → 重交仍被拒 → 修产物续接 → 入门 → 派审 → 结案)exit {res.exit_code} ----\n" + "\n".join(lines))

    # 件② 同一会话 id 续接(首次拉起 → F236 续接 1 → 同轮补正续接 2 → F236 续接 3)
    log = vrig.log
    first_argv = json.loads((log / f"{PI_CARD}.argv.json").read_text(encoding="utf-8"))
    sid = first_argv[first_argv.index("--session-id") + 1]
    resumed = [json.loads((log / f"{PI_CARD}.argv.resume{k}.json").read_text(encoding="utf-8")) for k in (1, 2, 3)]
    _show(f"[件② 会话 id] 首次拉起 {sid}; 续接 1/2/3 = {[a[a.index('--session-id') + 1] for a in resumed]}")
    assert all(a[a.index("--session-id") + 1] == sid for a in resumed)
    prompt = (log / f"{PI_CARD}.resume2.prompt").read_text(encoding="utf-8")
    _show(f"---- 件② 同轮补正续接指令原文(resume2) ----\n{prompt}")
    assert prompt.startswith("你的交回 ") and f"被产物入口拒收 {PROG}" in prompt and f"顾问复核 {review_id} 同轮补正" in prompt
    assert all(p in prompt for p in POINTS + CRITERIA)
    assert "改完即停" in prompt and "判据不放宽" in prompt
    fixes = _pi_fixes(gov)[-1]
    _show("[件② 本运行 report_fixes] " + json.dumps([{k: f.get(k) for k in ("outcome", "kind", "review_id", "kinds")} for f in fixes],
                                                    ensure_ascii=False))
    assert [(f["outcome"], f.get("kind"), f.get("review_id")) for f in fixes] == [("resumed", "same_round", review_id), ("resumed", None, None)]
    assert any("续接同轮补正 1/1" in ln for ln in lines) and any("续接修产物 1/1" in ln for ln in lines), "同轮补正不占修产物额度"

    # 件③ 重交仍被程序判据拒 = 新拒收记录; 改对后入门 → 派审, 审计卡带复审说理
    rejections = ai.return_rejection_records(gov, PI_CARD)
    _show(f"---- 件③ 重交仍被拒的新拒收记录 {Path(rejections[-1]['_path']).name} ----\n"
          + Path(rejections[-1]["_path"]).read_text(encoding="utf-8")[:1200])
    assert [r["rejection_id"] for r in rejections] == [f"return_rejected_{PI_CARD}_1", f"return_rejected_{PI_CARD}_2"]
    report = gov / "task_cards" / PI_CARD / "RETURN.md"
    assert "fixture-pass-0000" not in report.read_text(encoding="utf-8") and "维护接口" in report.read_text(encoding="utf-8")
    assert f90._records(gov, "returns", PI_CARD), "改对后入门 = 门落交回"
    audit_card = next(p for q in ("pending", "claimed", "completed") for p in (gov / "5_tasks" / "queue" / q).glob(f"{PI_CARD.lower()}r.md"))
    body = audit_card.read_text(encoding="utf-8")
    section = body[body.index("## 复审说理(AIPOS-F238 同轮补正)"):]
    _show(f"---- 件③ 审计卡 {audit_card.name} 复审说理原文 ----\n" + section[:900])
    assert f"顾问复核 `{review_id}` 同轮补正" in section and all(f"- {p}" in section for p in POINTS)
    assert "★复审说理(AIPOS-F238 同轮补正)" in body
    # 审计 pi 开工稿 = 指向该审计卡(准绳与复审说理在卡面, 与 F230 reaudit_ref 同一落点)
    kickoff = (log / f"{PI_CARD}R.kickoff").read_text(encoding="utf-8")
    _show(f"---- 件③ 审计 pi 开工稿原文(复审说理在其指向的审计卡) ----\n{kickoff}")
    assert f"任务卡路径: {gov / '5_tasks' / 'queue' / 'claimed' / audit_card.name}" in kickoff
    assert res.exit_code == 0 and res.outcome == "completed", text[-2000:]


# ===========================================================================
# 声明 fail-closed + run-all 发现
# ===========================================================================

def test_f238_declarations_fail_closed(monkeypatch):
    from tools.aipos_cli import loop_driver as ld

    real = nr._transition_node

    def broken(node_id):
        node = json.loads(json.dumps(real(node_id)))
        if node_id == "N4":
            node["same_round"]["rejection"]["texts"].pop("await_suggested")
        return node

    monkeypatch.setattr(nr, "_transition_node", broken)
    with pytest.raises(SchemaLoadError) as exc:
        nr.same_round_declaration()
    _show(f"[声明 fail-closed] {exc.value}")
    assert "same_round.rejection.texts" in str(exc.value)
    monkeypatch.setattr(nr, "_transition_node", real)
    contract = ld.load_loop_contract()
    bad = json.loads(json.dumps(contract))
    bad["launch"]["report_fix"]["executor"]["same_round"]["prompt"] = "no placeholders"
    with pytest.raises(SchemaLoadError):
        ld.same_round_fix_declaration(bad)
    del bad["launch"]["report_fix"]["executor"]["same_round"]
    with pytest.raises(SchemaLoadError):
        ld.same_round_fix_declaration(bad)


def test_f238_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f238_same_round_rejected_return.py"
    assert rel in discover_test_files([rel], default_test_contract())
