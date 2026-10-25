"""AIPOS-F219 — 返工重交回须晚于「最新返工轮登记」而非仅晚于裁决(10-23 chris HBLX-31 实撞, F218 引入)。

依据: HBLX-31(docs, 治理根产出)rework_rounds 两轮都针对同一裁决(第 1 轮 03:48:40Z, 第 2 轮 08:57:00Z); 交回报告改于 05:31:24Z(第 1 轮
期间, 门未重交回)。F218 的 non_branch_re_return 只以裁决时刻为界 → `lybra next` 把第 1 轮期间重写的旧报告误判为「已按第 2 轮返工重写交回」
(verdict_stale), 顾问此刻重跑 loop 会把旧报告当第 2 轮送审。

件① 时间界单源 next_resolver.rework_return_bound = max(裁决时刻, 该裁决最新返工轮 created_at)(读不出 created_at = 回落裁决时刻并在提示点名;
    声明 transitions nodes.N4.rework_return_bound): non_branch_re_return(门交回记录 / 报告文件两路)与 verdict_staleness 卡分支分支
    (tip 提交时刻)都与这一个界比; await_rework 提示点名「第 n 轮于 <时刻> 登记, 等其后的新交回」。
件② 复审说理按交付主体措辞(F218R F-1): audit_derivation 派 R(n+1) 时固定候选 / 治理根产出读 nodes.N4.non_branch_rework.by_subject
    reaudit_ref / reaudit_note, 卡分支措辞不变。

靶场 = F90 进程内门 + loop 全链靶场(临时治理根 + 临时产品仓 + 临时 HOME; 禁真门 / 真治理根 / 真工位 / 起 pi), 卡面自造人肉期 HBLX-31
形态。同一测试进程内无法真等数小时, 故第 2 轮登记时刻以门同一写入口(queue_mutation.build_rework_round 的 iso_z)取「裁决 +120s」,
两轮期间的报告改写 / 分支提交时刻分别取「裁决 +60s」与「裁决 +180s」。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f155_loop_chain_gaps as f155  # noqa: E402  — 卡分支返工靶场(禁第二份)
import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402  — 固定候选 / 治理根产出靶场(禁第二份)
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f218_candidate_rework_recovery import vrig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import queue_mutation  # noqa: E402

_show, _pick, _quiet = f218._show, f218._pick, f218._quiet
GOV, VAL, TASK = f218.GOV, f218.VAL, f155.TASK
FOCUS2 = ["第 2 轮: 只修正文结论与证据归类, 不再在线补查"]
CRITERIA2 = ["更新交回晚于本次正式返工"]
NEXT_KEYS = ("derivable", "current_node", "triggered_by", "missing_records", "suggested_action", "notes", "action")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _verdict_moment(gov: Path, task_id: str) -> datetime:
    path, fm = nr._regular_verdicts(gov, task_id)[0]
    return nr._verdict_moment(path, fm).replace(microsecond=0)


def _touch(path: Path, moment: datetime) -> None:
    os.utime(path, (moment.timestamp(), moment.timestamp()))


def _register_round(gov: Path, task_id: str, verdict_ref: str, *, at: datetime | None, monkeypatch) -> None:
    """顾问照 next 给出的 queue rework 登记返工轮(预演 + --confirm, 门同一写入口); at = 该轮登记时刻(None = 门当前时刻)。"""
    argv = ["--workspace-root", str(gov), "queue", "rework", "--task-id", task_id, "--actor", DRIVER, "--verdict-ref", verdict_ref,
            "--focus-items", json.dumps(FOCUS2, ensure_ascii=False), "--acceptance-criteria", json.dumps(CRITERIA2, ensure_ascii=False)]
    with monkeypatch.context() as m:
        if at is not None:
            m.setattr(queue_mutation, "iso_z", lambda: _iso(at))
        assert _cli(argv)[0] == 0
        rc, out, err = _cli(argv + ["--confirm"])
    assert rc == 0, out + err


def _rounds(gov: Path, task_id: str) -> list[dict]:
    return f218._card_fm(gov, task_id).get("rework_rounds") or []


def _audit_card(gov: Path, audit_id: str) -> tuple[list[str], str]:
    path, _q = nr._find_task_in_queue(gov, audit_id)
    text = path.read_text(encoding="utf-8")
    return list(nr._read_frontmatter(path).get("governance_refs") or []), text


# ===========================================================================
# ① 治理根产出卡(HBLX-31 形态): 同一裁决两轮返工, 报告在两轮之间重写 → 等第 2 轮; 第 2 轮后重写 → verdict_stale → R2(④ 治理根产出说理)
# ===========================================================================

def test_item1_hblx31_shape_round2_waits_then_rewrite_dispatches_r2(rig, monkeypatch):  # noqa: F811
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    monkeypatch.setattr(f90, "_finalize_double", f218._real_finalize)
    _write(rig.gov / "5_tasks" / "policies" / f"{f218.POLICY_D}.md", build_autonomy_policy_markdown(
        policy_id=f218.POLICY_D, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f219", task_selector_task_mode="docs", launch_harnesses=["pi"]))
    f78._card(rig.gov, GOV, "pending", task_mode="docs",
              extra={"harness": "claude-code", "audit": "required", "audit_by": AUDITOR, "output_target": f"task_cards/{GOV}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{GOV}/artifacts/"], "roles": ["executor"]}})
    init_governance_repo(rig.gov)
    seen: list[str] = []
    res, text = f218._gov_loop(rig, seen, lambda r: f218._gov_return(r, "v1"), {f"{GOV}R": ("FAIL", "FAIL — 凭据失效归因无证据")})
    assert seen == ["executor", f"auditor:{GOV}R:FAIL"], text
    verdict_id = str(nr._read_task_records(rig.gov, GOV)["latest_verdict"]["verdict_id"])
    t0 = _verdict_moment(rig.gov, GOV)
    _register_round(rig.gov, GOV, verdict_id, at=None, monkeypatch=monkeypatch)  # 第 1 轮(门当前时刻, 晚于裁决)
    report = f218._gov_return(rig, "v1.5(第 1 轮期间重写, 门未重交回)")
    _touch(report, t0 + timedelta(seconds=60))
    r1 = nr.derive_next_step(GOV, rig.gov)
    _show("---- ① 第 1 轮期间重写报告后 lybra next 原文(第 1 轮下该改写算返工交回)----\n" + _pick(r1, "current_node", "notes"))
    assert r1["current_node"] == "verdict_stale"
    _register_round(rig.gov, GOV, verdict_id, at=t0 + timedelta(seconds=120), monkeypatch=monkeypatch)  # 第 2 轮, 同一裁决
    rounds = _rounds(rig.gov, GOV)
    _show(f"[① 卡面 rework_rounds] {json.dumps([{k: r.get(k) for k in ('round', 'verdict_ref', 'created_at')} for r in rounds], ensure_ascii=False)}")
    assert [r["round"] for r in rounds] == [1, 2] and {r["verdict_ref"] for r in rounds} == {verdict_id}
    round2_at = str(rounds[1]["created_at"])

    wait = nr.derive_next_step(GOV, rig.gov)
    _show("---- ① 第 2 轮登记后 lybra next 原文(报告改于第 1 轮期间)----\n" + _pick(wait, *NEXT_KEYS))
    assert wait["action"] == {"type": "await_rework", "card": GOV, "round": 2}, "第 1 轮期间重写的旧报告不算第 2 轮交回"
    said = wait["missing_records"][0] + wait["suggested_action"] + wait["notes"]
    assert f"第 2 轮于 {round2_at} 登记, 等其后的新交回" in said
    assert f"改于 {_iso(t0 + timedelta(seconds=60))}, 早于第 2 轮登记, 不算本轮交回" in wait["notes"]
    assert nr.verdict_staleness(rig.gov, GOV, f218._card_fm(rig.gov, GOV)) is None
    status_text, _status = _gov_status(rig.gov)
    _show("---- ① 第 2 轮登记后 lybra loop status 原文 ----\n" + status_text)

    report = f218._gov_return(rig, "v2(第 2 轮: 撤回无据归因)")
    _touch(report, t0 + timedelta(seconds=180))
    stale = nr.derive_next_step(GOV, rig.gov)
    _show("---- ① 第 2 轮登记后再重写报告 lybra next 原文 ----\n" + _pick(stale, *NEXT_KEYS[:-1]))
    assert stale["current_node"] == "verdict_stale" and stale["verdict_stale"]["rework_round"]["round"] == 2
    assert f"第 2 轮返工修正产出并重写交回(交回报告 {report} 改于 {_iso(t0 + timedelta(seconds=180))})" in stale["notes"]

    seen2: list[str] = []
    res2, text2 = f218._gov_loop(rig, seen2, lambda r: seen2.append("executor-unexpected"), {f"{GOV}R2": ("PASS", "逐条复核通过")})
    _show(f"---- ① loop 续跑(第 2 轮重交回 → R2 → 结案)exit {res2.exit_code} ----\n" + _quiet(text2))
    assert res2.exit_code == 0 and seen2 == [f"auditor:{GOV}R2:PASS"], text2
    assert f"run return @ verdict_stale/claimed ({GOV})" in text2

    refs, card = _audit_card(rig.gov, f"{GOV}R2")
    note = [g for g in refs if "复审说理" in g]
    _show(f"---- ④ R2 卡复审说理(治理根产出)原文 ----\n{json.dumps(note, ensure_ascii=False, indent=1)}\n"
          + card[card.index("## 复审说理"):card.index("## References")])
    assert len(note) == 1 and "交付主体 = 治理根产出清单" in note[0] and f"上一轮 {GOV}R 的裁决 {verdict_id}(FAIL)" in note[0]
    assert "第 2 轮返工" in note[0] and "卡分支" not in note[0] and "tip" not in note[0]
    assert "## 复审说理(AIPOS-F219 返工重交回, 治理根产出)" in card and "卡分支  " not in card


def _gov_status(gov: Path) -> tuple[str, dict]:
    _rc, text, err = _cli(["loop", "status", "--task-id", GOV, "--workspace-root", str(gov)])
    _rcj, raw, _e = _cli(["loop", "status", "--task-id", GOV, "--workspace-root", str(gov), "--json"])
    return text + err, json.loads(raw)


# ===========================================================================
# ② 固定候选卡同形态(④ 固定候选说理)
# ===========================================================================

def test_item2_candidate_round2_ignores_round1_rewrite(vrig, monkeypatch):  # noqa: F811
    rig = vrig
    d, _status_text = f218._block_then_rework(rig)
    verdict_id = d["action"]["verdict_id"]
    t0 = _verdict_moment(rig.gov, VAL)
    _register_round(rig.gov, VAL, verdict_id, at=None, monkeypatch=monkeypatch)
    _touch(f218._return(rig, "BLOCK — 第 1 轮重新执行仍缺 context.json"), t0 + timedelta(seconds=60))
    assert nr.derive_next_step(VAL, rig.gov)["current_node"] == "verdict_stale"
    _register_round(rig.gov, VAL, verdict_id, at=t0 + timedelta(seconds=120), monkeypatch=monkeypatch)
    round2_at = str(_rounds(rig.gov, VAL)[1]["created_at"])
    wait = nr.derive_next_step(VAL, rig.gov)
    _show("---- ② 固定候选卡: 第 2 轮登记后 lybra next 原文(报告改于第 1 轮期间)----\n" + _pick(wait, *NEXT_KEYS))
    assert wait["action"] == {"type": "await_rework", "card": VAL, "round": 2}
    assert f"第 2 轮于 {round2_at} 登记, 等其后的新交回" in wait["suggested_action"]
    assert f"同一固定候选 {rig.candidate}" in wait["suggested_action"]

    _touch(f218._return(rig, "PASS — 第 2 轮在同一固定候选上重新执行, 71 项全过"), t0 + timedelta(seconds=180))
    stale = nr.derive_next_step(VAL, rig.gov)
    _show("---- ② 固定候选卡: 第 2 轮后重写 lybra next 原文 ----\n" + _pick(stale, "current_node", "notes"))
    assert stale["current_node"] == "verdict_stale" and stale["verdict_stale"]["rework_round"]["round"] == 2
    seen: list[str] = []
    res, text = f218._loop(rig, f218._watch(rig, seen, executor=lambda r: seen.append("executor-unexpected"),
                                            verdicts={f"{VAL}R2": ("PASS", "逐条复核通过")}))
    _show(f"---- ② loop 续跑(R2 → 结案)exit {res.exit_code} ----\n" + _quiet(text))
    assert res.exit_code == 0 and seen == [f"auditor:{VAL}R2:PASS"], text
    refs, card = _audit_card(rig.gov, f"{VAL}R2")
    note = [g for g in refs if "复审说理" in g]
    _show(f"---- ④ R2 卡复审说理(固定候选)原文 ----\n{json.dumps(note, ensure_ascii=False, indent=1)}\n"
          + card[card.index("## 复审说理"):card.index("## References")])
    assert len(note) == 1 and "交付主体 = 固定候选" in note[0] and f"同一固定候选 {rig.candidate}" in note[0]
    assert "已前进到" not in note[0] and "## 复审说理(AIPOS-F219 返工重交回, 固定候选)" in card


# ===========================================================================
# ③ 卡分支卡: 同一裁决两轮返工, 第 1 轮期间的分支提交不算第 2 轮(④ 卡分支说理不变); F155 / F218 既有返工测试回归见各自文件
# ===========================================================================

def _commit_at(worktree: Path, message: str, when: datetime) -> tuple[str, str]:
    name = f"tests/test_rework_{int(when.timestamp())}.py"
    _write(worktree / name, "def test_edge():\n    assert True\n")
    _git(worktree, "add", name)
    env = {**os.environ, "GIT_COMMITTER_DATE": f"@{int(when.timestamp())} +0000", "GIT_AUTHOR_DATE": f"@{int(when.timestamp())} +0000"}
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message], cwd=str(worktree), env=env,
                   check=True, capture_output=True, text=True)
    return _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")


def _branch_return(rig, sha: str, tree: str, conclusion: str) -> None:
    path = rig.gov / "task_cards" / TASK / "RETURN.md"
    _write(path, _fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{TASK}", "model": f90.SELF_EXEC_MODEL},
                     f"# RETURN — {TASK}\n\n## 一句话结论\n{conclusion}\n\n## 改动清单\n- tests\n"))


def test_item3_card_branch_round1_commit_not_round2(rig, monkeypatch):  # noqa: F811
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    seen: list[str] = []
    res, text = f155._loop(rig, f155._watch(rig, {f"{TASK}R": "FAIL"}, seen))
    assert seen == ["executor", f"auditor:{TASK}R:FAIL"], text
    verdict_id = str(nr._read_task_records(rig.gov, TASK)["latest_verdict"]["verdict_id"])
    t0 = _verdict_moment(rig.gov, TASK)
    _register_round(rig.gov, TASK, verdict_id, at=None, monkeypatch=monkeypatch)
    _code_repo, worktree = nr.card_worktree_location(rig.gov, TASK)
    sha1, tree1 = _commit_at(worktree, f"{TASK}: 返工第 1 轮", t0 + timedelta(seconds=60))
    _branch_return(rig, sha1, tree1, "按第 1 轮返工节修复")
    assert nr.derive_next_step(TASK, rig.gov)["current_node"] == "verdict_stale", "第 1 轮下该提交算返工"
    _register_round(rig.gov, TASK, verdict_id, at=t0 + timedelta(seconds=120), monkeypatch=monkeypatch)
    round2_at = str(_rounds(rig.gov, TASK)[1]["created_at"])
    wait = nr.derive_next_step(TASK, rig.gov)
    _show("---- ③ 卡分支卡: 第 2 轮登记后 lybra next 原文(tip 提交于第 1 轮期间)----\n" + _pick(wait, *NEXT_KEYS))
    assert wait["action"] == {"type": "await_rework", "card": TASK, "round": 2}, "第 1 轮期间的分支提交不算第 2 轮返工"
    assert f"第 2 轮于 {round2_at} 登记, 等其后的新交回" in wait["suggested_action"]
    assert nr.verdict_staleness(rig.gov, TASK, f218._card_fm(rig.gov, TASK)) is None

    sha2, tree2 = _commit_at(worktree, f"{TASK}: 返工第 2 轮", t0 + timedelta(seconds=180))
    _branch_return(rig, sha2, tree2, "按第 2 轮返工节修复")
    stale = nr.derive_next_step(TASK, rig.gov)
    _show("---- ③ 卡分支卡: 第 2 轮后新提交 lybra next 原文 ----\n" + _pick(stale, "current_node", "notes"))
    assert stale["current_node"] == "verdict_stale" and stale["verdict_stale"]["tip"] == sha2
    assert stale["verdict_stale"]["rework_round"]["round"] == 2
    seen2: list[str] = []
    res2, text2 = f155._loop(rig, f155._watch(rig, {}, seen2, executor=lambda r: seen2.append("executor-unexpected")))
    _show(f"---- ③ loop 续跑(R2 → 结案)exit {res2.exit_code} ----\n" + _quiet(text2))
    assert res2.exit_code == 0 and seen2 == [f"auditor:{TASK}R2:PASS"], text2
    refs, card = _audit_card(rig.gov, f"{TASK}R2")
    note = [g for g in refs if "复审说理" in g]
    _show(f"---- ④ R2 卡复审说理(卡分支)原文 ----\n{json.dumps(note, ensure_ascii=False, indent=1)}")
    assert len(note) == 1 and f"卡分支 card/{TASK} 已前进到 {sha2[:12]}" in note[0]


# ===========================================================================
# 件① 时间界单源 + 回落; 声明 fail-closed
# ===========================================================================

def test_bound_single_source_fallback_and_declaration():
    verdict_fm = {"verdict_at": "2026-10-23T03:41:28Z"}
    later = nr.rework_return_bound(Path("/nonexistent"), verdict_fm, {"round": 2, "created_at": "2026-10-23T08:57:00Z"})
    assert _iso(later["at"]) == "2026-10-23T08:57:00Z" and later["fallback"] is False
    assert nr.rework_bound_note(later) == ("第 2 轮于 2026-10-23T08:57:00Z 登记, 等其后的新交回"
                                           "(早于该轮登记的门交回记录 / 报告改写 / 卡分支提交不算本轮)")
    earlier = nr.rework_return_bound(Path("/nonexistent"), verdict_fm, {"round": 1, "created_at": "2026-10-23T03:00:00Z"})
    assert _iso(earlier["at"]) == "2026-10-23T03:41:28Z", "登记早于裁决(补登)= 界取裁决时刻"
    for raw in ("", "坏时刻"):
        fb = nr.rework_return_bound(Path("/nonexistent"), verdict_fm, {"round": 2, "created_at": raw})
        assert fb["fallback"] is True and _iso(fb["at"]) == "2026-10-23T03:41:28Z"
        _show(f"[件① 回落提示 created_at={raw!r}] {nr.rework_bound_note(fb)}")
        assert "登记时刻读不出" in nr.rework_bound_note(fb) and "回落裁决时刻 2026-10-23T03:41:28Z" in nr.rework_bound_note(fb)
    assert nr.rework_return_bound(Path("/nonexistent"), verdict_fm, None)["fallback"] is False
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    import re

    # AIPOS-F221 件②: 第 4 处调用 = 快车道复核打回返工态(next_resolver.fast_lane_rework_state, 同一时间界, 锚 = 顾问复核记录)
    # AIPOS-F230 件④: 第 5 处调用 = 同轮补正态(next_resolver.same_round_state, 同一时间界, 锚 = 同轮补正复核记录, 无返工轮)
    assert len(re.findall(r"(?<![.\w])rework_return_bound\(", src)) == 6, \
        "定义 1 + non_branch_re_return / 卡分支 verdict_staleness / 等待提示 / 快车道返工态 / 同轮补正态 5 处调用"
    body = src[src.index("def non_branch_re_return("):src.index("def _non_branch_verdict_staleness(")]
    assert "_verdict_moment(" not in body and "rework_return_bound(Path(verdict_path), verdict_fm, rework)" in body, "两路同一时间界"
    assert src.count("non_branch_stale_texts(stale)") == 1, "推导核说理只一处构建(审计卡派生另由 audit_derivation 调同一函数)"
    ad = (REPO_ROOT / "tools" / "aipos_cli" / "audit_derivation.py").read_text(encoding="utf-8")
    assert ad.count("non_branch_stale_texts(stale)") == 1
    decl = nr.rework_return_bound_declaration()
    assert set(decl["texts"]) >= {"registered", "fallback", "earlier_ignored"}
    nb = nr.non_branch_rework_declaration()
    assert all({"reaudit_ref", "reaudit_note"} <= set(v) for v in nb["by_subject"].values())


def test_bound_declaration_fail_closed(monkeypatch):
    from tools.schema_loader import SchemaLoadError

    real = nr._transition_node

    def broken(name):
        node = dict(real(name))
        if name == "N4":
            node.pop("rework_return_bound", None)
        return node

    monkeypatch.setattr(nr, "_transition_node", broken)
    with pytest.raises(SchemaLoadError, match="rework_return_bound"):
        nr.rework_return_bound_declaration()


def test_f219_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f219_rework_round_bound.py"
    assert rel in discover_test_files([rel], default_test_contract())
