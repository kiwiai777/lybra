"""AIPOS-F252 — 受控发布面在没有 card_policy.json 的项目上不可用 + 阻塞交回被审计 PASS 后当成完成结案。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi / 任何真实生产主机): 复用 F251 夹具骨架(临时治理根 webdemo, 卡号形 WEB-<n>; 模拟主机 = 本机临时目录 +
PATH 最前的假 ssh), 但治理根**不放** card_policy.json(人肉期接入项目样本: kiwiai-web / chris / agency 同形, 在临时根自造)。
验收原文经 _show 打到 stdout(RETURN 证据)。
  件① 卡号形状声明: release plan 缺声明拒且出口带声明命令原文; 声明命令正反例(非法正则 / 不匹配现有卡号拒, 正确值写入并落账); 之后 F251 端到端
       (plan → apply → verify)通过; project upgrade 预演列出缺项; onboarding guide 给出命令。
  件② 阻塞交回: Return outcome=blocked → loop 推导不结案、派生 block 步、卡进 blocked + 收件箱事项 + 续跑出口; 审计 PASS 不产生 close;
       门侧 close 兜底拒; 旧交回(无 outcome)行为不变; outcome 值不合 = 入口拒。
  件③ 已误结案卡接续: completed 卡经 queue reopen 回到可续跑(新一轮只看本轮记录、旧报告归档), 再跑通发布链; completed 卡顾问异议 note 进收件箱。
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f251_release_surface as f251  # noqa: E402  — 发布面夹具(假 ssh / 模拟主机 / 声明 / 授权修订)
from test_aipos_f251_release_surface import rig as f251_rig  # noqa: E402,F401  — 夹具复用(pytest 按名注入)

_show = f251._show
_cli = f251._cli
PATTERN = "WEB-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"


@pytest.fixture()
def rig(f251_rig):
    """F251 靶场去掉 card_policy.json(= 人肉期接入项目: 无卡号形状声明)。"""
    r = f251_rig
    (r["gov"] / "card_policy.json").unlink()
    f78._git(r["gov"], "add", "-A")
    f78._git(r["gov"], "commit", "-qm", "fixture: 人肉期样本(无 card_policy.json)")
    return r


def _cp(r: dict, *argv: str) -> tuple[int, str, str]:
    return _cli("project", "card-policy", *argv, "--workspace-root", r["gov"])


# ===========================================================================
# 件① 卡号形状声明可补
# ===========================================================================

def test_item1_release_plan_without_declaration_names_exit_command(rig):
    r = rig
    assert f251._declare_remote(r)[0] == 0
    rc, out, err = f251._plan(r)
    _show(f"---- 件① 无 card_policy.json 时 release plan exit {rc} ----\n{out}{err}")
    assert rc == 1 and "项目未声明 task_id_pattern" in err and "C2 原则" in err
    assert f"lybra --workspace-root {r['gov']} project card-policy set-task-id-pattern --pattern" in err
    assert "WEB-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*" in err and "仅建议" in err  # 建议值来自队列现有卡号前缀, 须显式确认
    assert not (r["gov"] / "card_policy.json").exists()  # 无内置默认: 报错不写任何声明


def test_item1_declare_command_refusals_then_write_and_landing(rig):
    r = rig
    gov = r["gov"]
    rc, out, _err = _cp(r, "show")
    _show(f"---- 件① card-policy show(未声明)exit {rc} ----\n{out}")
    assert rc == 0 and "未声明 task_id_pattern" in out and PATTERN in out and "set-task-id-pattern" in out
    bad = {
        "非法正则": ("WEB-[0-9", "TASK_ID_PATTERN_INVALID"),
        "含捕获组": ("(WEB)-[0-9]+", "TASK_ID_PATTERN_INVALID"),
        "含锚点": ("^WEB-[0-9]+$", "TASK_ID_PATTERN_INVALID"),
        "匹配空串": ("(?:WEB-[0-9]+)?", "TASK_ID_PATTERN_INVALID"),
        "不匹配现有卡号(审计轮 WEB-3R)": ("WEB-[0-9]+", "TASK_ID_PATTERN_MISMATCH"),
        "空值": (" ", "TASK_ID_PATTERN_EMPTY"),
        "占位未替换": ("<卡号正则>", "TASK_ID_PATTERN_EMPTY"),
    }
    for what, (value, code) in bad.items():
        rc, out, err = _cp(r, "set-task-id-pattern", "--pattern", value, "--confirm", "--actor", f251.ADVISOR)
        _show(f"[件① 拒·{what}] exit {rc}: {err.strip()}")
        assert rc == 1 and code in err, (what, err)
        assert not (gov / "card_policy.json").exists()
    assert "WEB-3R" in _cp(r, "set-task-id-pattern", "--pattern", "WEB-[0-9]+")[2]  # 拒因列出不匹配卡号
    rc, out, err = _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR)
    _show(f"---- 件① set-task-id-pattern 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and "+  \"task_id_pattern\"" in out and not (gov / "card_policy.json").exists()
    rc, out, err = _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR, "--confirm")
    _show(f"---- 件① set-task-id-pattern --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and "已写入" in out and "落账: verdict=PASS" in out
    data = json.loads((gov / "card_policy.json").read_text(encoding="utf-8"))
    assert data["task_id_pattern"] == PATTERN and data["rules"] == []
    log = f78._git(gov, "log", "-1", "--format=%s")
    assert "卡号形状声明落账 set-task-id-pattern" in log, log
    assert f78._git(gov, "status", "--porcelain", "--", "card_policy.json") == ""
    from tools.card_policy_loader import get_task_id_pattern

    assert get_task_id_pattern(gov) == PATTERN  # 现行唯一读口认得
    # 现有声明文件: 只改 task_id_pattern 一键, 其余键原样
    data["rules"] = [{"field": "anchor_refs", "required": False, "values": ["N1"]}]
    (gov / "card_policy.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rc, out, _err = _cp(r, "set-task-id-pattern", "--pattern", "WEB-[A-Z0-9]+", "--actor", f251.ADVISOR, "--confirm")
    after = json.loads((gov / "card_policy.json").read_text(encoding="utf-8"))
    _show(f"[件① 改现有声明] exit {rc}; rules 保留={after['rules'] == data['rules']}; 值={after['task_id_pattern']}")
    assert rc == 0 and after["rules"] == data["rules"] and after["task_id_pattern"] == "WEB-[A-Z0-9]+"


def test_item1_after_declaration_f251_end_to_end_passes(rig):
    r = rig
    assert _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR, "--confirm")[0] == 0
    assert f251._declare_remote(r)[0] == 0
    f251._auth_revision(r, action="grant", hosts=["devbox", f251.HOST], zones=["production"])
    before = f251._tree_state(r["prod"])
    rc, out, err = f251._plan(r, "--tree", r["tree"])
    _show(f"---- 件① 声明后 release plan exit {rc} ----\n{out}{err}")
    assert rc == 0 and "授权: 就绪" in out
    digest = f251._plan_hash(out)
    rc, out, err = f251._rel(r, "apply", "--task-id", f251.CARD, "--target", f251.TARGET, "--plan-hash", digest, "--actor", f251.EXEC)
    _show(f"---- 件① release apply exit {rc} ----\n{out}{err}")
    assert rc == 0 and "APPLY_OK" in out
    after = f251._tree_state(r["prod"])
    assert {k: v for k, v in after.items() if k in before} == before
    rc, out, err = f251._rel(r, "verify", "--task-id", f251.CARD, "--target", f251.TARGET)
    _show(f"---- 件① release verify exit {rc} ----\n{out}{err}")
    assert rc == 0 and "VERIFY_OK" in out


def test_item1_upgrade_preview_and_onboarding_list_the_step(rig, tmp_path):
    from tools.aipos_cli.onboarding import generate_onboarding_guide, legacy_onboarding_steps
    from tools.aipos_cli.project_upgrade import derive_guidance

    r = rig
    guidance = derive_guidance(r["gov"], project=f251.PROJECT)
    items = [i for g in guidance["groups"] for i in g["items"] if i["key"] == "task_id_pattern"]
    _show("---- 件① project upgrade 预演待补项 ----\n" + "\n".join(f"{i['why']} | {i['say']} | {i['commands']}" for i in items))
    assert items and any("project card-policy set-task-id-pattern" in c for c in items[0]["commands"])
    rc, out, err = _cli("project", "upgrade", "--workspace-root", r["gov"])
    _show(f"---- 件① lybra project upgrade(预演)exit {rc} ----\n" + "\n".join(l for l in (out + err).splitlines() if "task_id_pattern" in l or "card-policy" in l))
    assert "card-policy set-task-id-pattern" in out + err
    assert _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR, "--confirm")[0] == 0
    assert not [i for g in derive_guidance(r["gov"], project=f251.PROJECT)["groups"] for i in g["items"] if i["key"] == "task_id_pattern"]
    guide = generate_onboarding_guide("probe-cp", home_root=str(tmp_path / "h"), actor=f251.ADVISOR, repos=[f"app={tmp_path}/app"])
    step9 = next(s for s in guide["steps"] if s["step_number"] == 9)["command"]
    lines = [ln for ln in step9.splitlines() if "card-policy" in ln]
    _show("---- 件① onboarding guide 第 9 步(首卡前)卡号形状行 ----\n" + "\n".join(lines))
    assert step9.index("card-policy") < step9.index("draft create")
    assert any(ln.startswith("lybra --workspace-root") and "set-task-id-pattern" in ln for ln in lines)
    from tools.aipos_cli.aipos_cli import build_parser

    for ln in lines:
        if ln.startswith("lybra "):
            args = build_parser().parse_args(shlex.split(ln)[1:])
            assert args.command == "project" and args.cp_command in ("show", "set-task-id-pattern")
    legacy = legacy_onboarding_steps(f251.PROJECT, str(r["gov"]), home_root=str(tmp_path / "h"), actor=f251.ADVISOR, driver_policy="pol")
    assert "card-policy set-task-id-pattern" in legacy[0]["command"]
    rc, out, err = _cli("project", "new", "probe-new", "--home-root", tmp_path / "h2", "--actor", f251.ADVISOR)
    hint = [ln for ln in out.splitlines() if "card-policy" in ln]
    _show(f"[件① project new 下一步] exit {rc}: {hint}")
    assert rc == 0 and hint and "set-task-id-pattern --pattern '<卡号正则>'" in hint[0]
    assert not (tmp_path / "h2" / "probe-new" / "card_policy.json").exists()  # 不内置默认


# ===========================================================================
# 件② 阻塞交回不得当完成结案(F90 靶场: 进程内门 + 进程内产品 CLI, 全程只经 lybra loop)
# ===========================================================================

import contextlib  # noqa: E402
import io  # noqa: E402

import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f90_loop_one_stage import rig as f90_rig  # noqa: E402,F401  — 夹具复用
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

TASK, AUDIT = f90.TASK, f90.AUDIT


def _blocked_report(gov: Path, task_id: str, *, outcome: str | None = "blocked") -> Path:
    """执行体按卡停线: 交回报告 outcome=blocked(三键照填卡分支 tip; 正文写阻塞原因)。"""
    code_repo, worktree = nr.card_worktree_location(gov, task_id)
    sha, tree = f78._git(worktree, "rev-parse", "HEAD"), f78._git(worktree, "rev-parse", "HEAD^{tree}")
    meta = {"commit_sha": sha, "tree_hash": tree, "branch": f"card/{task_id}"}
    if outcome is not None:
        meta["outcome"] = outcome
    ret = gov / "task_cards" / task_id / "RETURN.md"
    f78._write(ret, f78._fm(meta, f"# RETURN — {task_id}\n\n## 一句话结论\nBLOCK: 项目未声明 task_id_pattern, release plan 退出 1, 生产零变更。\n\n"
                                  "## 阻塞原因\n- 前置缺失: card_policy.json\n"))
    return ret


def _watch_blocked(r, seen: list[str], *, commit: bool = False):
    def watch(args, expect_ready):
        seen.append(" ".join(args.expect))
        if commit:
            f90._executor_work(r)  # 有提交的阻塞交回(如 KIWIWEB-003 的阻塞收据)
        _blocked_report(r.gov, TASK)
        return 0 if expect_ready([]) else 3

    return watch


def _inbox(gov: Path) -> list[dict]:
    from tools.aipos_cli.owner_inbox import collect_project_inbox

    got = collect_project_inbox(gov)
    assert not got["errors"], got["errors"]
    return got["items"]


@pytest.mark.parametrize("commit", [False, True], ids=["no-commit", "with-commit"])
def test_item2_blocked_return_loop_blocks_card_inbox_and_resume_exit(f90_rig, commit):
    r = f90_rig
    f90._card(r.gov, TASK, "pending")
    init_governance_repo(r.gov)
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=out, watch=_watch_blocked(r, [], commit=commit),
                   interval=0.05, max_wait=5, max_steps=20)
    text = out.getvalue()
    _show(f"---- 件② 阻塞交回({'有' if commit else '无'}提交)loop exit {res.exit_code} outcome {res.outcome} reason {res.reason} ----\n{text}")
    assert res.outcome == "not_derivable" and res.reason == "return_blocked", text
    assert [s.action_type for s in res.steps if s.kind == "execute" and s.ok] == ["claim", "return", "block"]
    latest = nr._read_task_records(r.gov, TASK)["latest_return"]
    assert latest["return_outcome"] == "blocked"  # 门交回记录带报告声明的结果
    for kind, card in (("audit_dispatches", AUDIT), ("audit_verdicts", TASK), ("finalizations", TASK), ("closures", TASK)):
        assert not f90._records(r.gov, kind, card), f"阻塞交回不得派审 / 合入 / 结案: {kind}"
    card = r.gov / "5_tasks" / "queue" / "blocked" / f"{TASK.lower()}.md"
    fm = nr._read_frontmatter(card)
    _show(f"[件② 卡面] status={fm['status']} needs_owner={fm['needs_owner']} owner_reason={fm.get('owner_reason')}\n"
          f"  blocked_reason={fm.get('blocked_reason') or fm.get('block_reason')}")
    assert fm["status"] == "blocked" and fm["needs_owner"] is True and fm["owner_reason"] == "beyond_authority"
    landed = f78._git(r.gov, "log", "-1", "--format=%s%n%b", "--", f"5_tasks/queue/blocked/{TASK.lower()}.md")
    _show(f"[件② block 落账] {landed.splitlines()[0] if landed else '(未落账)'}")
    assert landed and f78._git(r.gov, "status", "--porcelain", "--", "5_tasks/queue/blocked", "5_tasks/queue/claimed") == ""
    d = nr.derive_next_step(TASK, r.gov)
    _show(f"[件② 推导] {d['current_state']} action={d['action']}\n  missing={d['missing_records']}\n  出口={d['suggested_action']}")
    assert d["action"]["type"] == "return_blocked" and f"queue reopen --task-id {TASK} --actor {f78.EXEC}" in d["suggested_action"]
    assert f"lybra loop --task-id {TASK}" in d["suggested_action"]
    items = [i for i in _inbox(r.gov) if i.get("task_id") == TASK]
    _show("---- 件② Owner 收件箱 ----\n" + "\n".join(f"{i['item_id']} [{i['category']}] {i['subject']}: {i['ask']}" for i in items))
    assert [i["category"] for i in items] == ["needs_owner_card"]  # 复用既有事项, 同卡不重复列 loop 事项
    assert "queue reopen" in items[0]["ask"] and "交回声明阻塞" in items[0]["ask"]
    rc, sout, _err = f90_cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(r.gov), "--json"])
    na = json.loads(sout)["next_action"]
    _show(f"[件② loop status] action={na['action']} reason={na['reason']}")
    assert na["action"] == "owner_needed" and na["reason"] == "return_blocked"


def f90_cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


def _record(gov: Path, kind: str, task_id: str, name: str, meta: dict) -> Path:
    path = gov / "5_tasks" / "records" / kind / task_id / f"{name}.md"
    f78._write(path, f78._fm(meta, f"# {name}\n"))
    return path


def _claimed_with_return(gov: Path, *, outcome: str | None) -> None:
    f90._card(gov, TASK, "claimed")
    f78._claim_record(gov, TASK, f78.EXEC)
    meta = {"record_type": "return_record", "event_type": "mcp_queue_return", "return_id": f"return_{TASK}_20261030_010000_x",
            "task_id": TASK, "returned_at": "'2026-10-30T01:00:00Z'"}
    if outcome:
        meta["return_outcome"] = outcome
    _record(gov, "returns", TASK, f"return_{TASK}_20261030_010000_x", meta)


def test_item2_audit_pass_on_blocked_return_never_closes_and_gate_close_refuses(f90_rig):
    from tools.aipos_cli.board_adapter import close_task

    r = f90_rig
    _claimed_with_return(r.gov, outcome="blocked")
    vid = f"verdict_{TASK}_20261030_020000_audit"
    _record(r.gov, "audit_dispatches", AUDIT, f"dispatch_{AUDIT}_20261030_015000_x",
            {"record_type": "audit_dispatch_record", "dispatch_id": f"dispatch_{AUDIT}_x", "audit_task_id": AUDIT, "reviewed_task_id": TASK})
    _record(r.gov, "audit_verdicts", TASK, vid, {"record_type": "audit_verdict_record", "event_type": "mcp_audit_verdict", "verdict_id": vid,
                                                "verdict_at": "'2026-10-30T02:00:00Z'", "reviewed_task_id": TASK, "audit_task_id": AUDIT,
                                                "verdict": "PASS"})
    from tools.aipos_cli.finalization_record import write_finalization_record

    write_finalization_record(governance_root=r.gov, task_id=TASK, actor=f78.DRIVER, commit="a" * 40, merge_commit="a" * 40,
                              authorization_type="verdict_ref", authorization_ref=vid, deployed=False, deploy_status="not_deployed",
                              remote_ref="origin/main")
    d = nr.derive_next_step(TASK, r.gov)
    _show(f"[件② 审计 PASS(核实阻塞属实)+ 已有合入记录] 推导: node={d['current_node']} verb={d['verb']}\n  command={d['command']}")
    assert "queue block" in d["command"] and "queue close" not in d["command"] and d["verb"] == "lybra_queue_block"
    got = close_task(task_id=TASK, actor=f78.EXEC, closure_evidence={"finalize_commit_hash": "a" * 40}, dry_run=False, repo_root=r.gov)
    _show(f"[件② 门侧 close 兜底] ok={got.get('ok')} category={got.get('category') or (got.get('error') or {}).get('category')}\n"
          f"  {got.get('message') or (got.get('error') or {}).get('message')}")
    assert json.dumps(got, ensure_ascii=False).count("RETURN_OUTCOME_BLOCKED") >= 1
    assert (r.gov / "5_tasks" / "queue" / "claimed" / f"{TASK.lower()}.md").is_file() and not f90._records(r.gov, "closures", TASK)


def test_item2_compat_legacy_return_without_outcome_and_invalid_value(f90_rig):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.board_adapter import close_task

    r = f90_rig
    _claimed_with_return(r.gov, outcome=None)  # 存量交回记录(无 return_outcome)= done
    d = nr.derive_next_step(TASK, r.gov)
    _show(f"[件② 兼容: 旧交回无 outcome] 推导 verb={d['verb']} command={d['command'][:120]}")
    assert nr.return_record_outcome(nr._read_task_records(r.gov, TASK)["latest_return"]) == "done"
    assert "queue block" not in d["command"] and d["verb"] != "lybra_queue_block"
    got = close_task(task_id=TASK, actor=f78.EXEC, closure_evidence={"finalize_commit_hash": "a" * 40}, dry_run=True, repo_root=r.gov)
    assert "RETURN_OUTCOME_BLOCKED" not in json.dumps(got, ensure_ascii=False)
    # outcome 值不合 = 产物入口拒(不猜、不回落 done)
    f78._write(r.gov / "task_cards" / TASK / "RETURN.md", f78._fm({"commit_sha": "ab" * 20, "tree_hash": "cd" * 20, "branch": f"card/{TASK}",
                                                                   "outcome": "partial"}, "# R\n\n## 一句话结论\n半成品。\n"))
    check = validate_task_artifact(TASK, r.gov)
    _show(f"[件② outcome 值不合] category={check.get('category')}: {check.get('reasons')}")
    assert check["category"] == "INGEST_RETURN_OUTCOME_INVALID" and "partial" in check["reasons"][0]
    assert nr.report_return_outcome({}) == ("done", False, None) and nr.report_return_outcome({"outcome": "blocked"}) == ("blocked", True, None)


# ===========================================================================
# 件③ 已误结案卡的合法接续: completed 卡顾问异议 note 进收件箱; reopen 开新一轮 → loop 照常跑通到再次结案
# ===========================================================================

def test_item3_completed_card_conflict_note_listed_then_reopen_runs_new_round(f90_rig):
    r = f90_rig
    f90._card(r.gov, TASK, "pending")
    init_governance_repo(r.gov)
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=io.StringIO(), watch=f90._agent_watch(r, []),
                   interval=0.05, max_wait=5, max_steps=30)
    assert res.outcome == "completed", res.message
    # 顾问异议: 审计结论与正文冲突(第一档 advisor_audit_conflict)—— 卡已 completed 仍进 Owner 收件箱
    argv = ["loop", "note", "--task-id", TASK, "--workspace-root", str(r.gov), "--actor", f78.DRIVER, "--owner-reason", "advisor_audit_conflict",
            "--ask", "审计 PASS 但正文是 BLOCK 裁决成立, 生产未部署; 是否 reopen 续跑"]
    rc, out, err = f90_cli(argv)
    _show(f"---- 件③ completed 卡 loop note 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "NOT_LISTED" not in out + err
    rc, out, err = f90_cli([*argv, "--confirm"])
    _show(f"---- 件③ completed 卡 loop note --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and "已进 Owner 收件箱" in out
    items = [i for i in _inbox(r.gov) if i.get("task_id") == TASK]
    assert [i["category"] for i in items] == ["loop_owner_needed"] and "BLOCK 裁决成立" in items[0]["ask"]
    rc, _o, err = f90_cli(["loop", "note", "--task-id", TASK, "--workspace-root", str(r.gov), "--actor", f78.DRIVER,
                           "--owner-reason", "rescope", "--ask", "别的事由"])
    assert rc == 5 and "NOT_LISTED" in err  # 其他事由对已结案卡照旧不列
    # reopen(既有 queue reopen)= 开新一轮: 轮界记录 + 旧交回报告归档 + 落账
    old_report = (r.gov / "task_cards" / TASK / "RETURN.md").read_text(encoding="utf-8")
    rc, out, err = f90_cli(["queue", "reopen", "--task-id", TASK, "--actor", f78.EXEC, "--reason", "Owner 裁决: 阻塞已补齐, 开新一轮续跑",
                            "--workspace-root", str(r.gov)])
    _show(f"---- 件③ queue reopen exit {rc} ----\n{out[-1500:]}{err}")
    assert rc == 0, out + err
    archived = r.gov / "task_cards" / TASK / "rounds" / "round-1" / "RETURN.md"
    assert archived.read_text(encoding="utf-8") == old_report and not (r.gov / "task_cards" / TASK / "RETURN.md").exists()
    events = sorted((r.gov / "5_tasks" / "records" / "events" / TASK).glob("reopen_round_*.md"))
    assert len(events) == 1 and nr.round_floor(r.gov, TASK) is not None
    assert f78._git(r.gov, "status", "--porcelain", "--", "task_cards", "5_tasks") == ""  # 轮界 + 归档随 reopen 落账
    fm = nr._read_frontmatter(r.gov / "5_tasks" / "queue" / "pending" / f"{TASK.lower()}.md")
    recs = nr._read_task_records(r.gov, TASK)
    _show(f"[件③ 新一轮] round={fm['round']} 轮界={recs['round_floor']} 本轮记录: return={recs['latest_return']} closure={recs['latest_closure']}")
    assert fm["round"] == 2 and recs["latest_return"] is None and recs["latest_closure"] is None
    d = nr.derive_next_step(TASK, r.gov)
    assert d["derivable"] and "queue claim" in d["command"]
    # 原命令续跑 loop: 新一轮 claim → 交回 → 复审轮 R2 → 裁决 → 合入 → 结案, 全程只经产品命令
    seen: list[str] = []
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=out, watch=_watch_round2(r, seen), interval=0.05, max_wait=5,
                   max_steps=30)
    _show(f"---- 件③ reopen 后续跑 loop exit {res.exit_code} outcome {res.outcome} ----\n{out.getvalue()[-3000:]}")
    assert res.outcome == "completed", out.getvalue()
    assert len(f90._records(r.gov, "closures", TASK)) == 2 and len(f90._records(r.gov, "returns", TASK)) == 2
    assert (r.gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    assert f"{TASK}R2" in " ".join(s.card for s in res.steps), [s.card for s in res.steps]


def _watch_round2(r, seen: list[str]):
    def watch(args, expect_ready):
        patterns = " ".join(args.expect)
        seen.append(patterns)
        if f"/{TASK}R2/" in patterns or f"task_cards/{TASK}R2" in patterns:
            tip = f78._git(r.code_repo, "rev-parse", f"card/{TASK}")
            report = r.gov / "task_cards" / f"{TASK}R2" / "audit_report.md"
            f78._write(report, f78._fm({"task_id": f"{TASK}R2", "reviewed_task_id": TASK, "verdict": "PASS", "commit_sha": tip},
                                       "# 审计报告\n\n## 一句话结论\n第二轮复核通过。\n"))
        else:
            _code, worktree = nr.card_worktree_location(r.gov, TASK)
            f78._git(worktree, "merge", "-q", "--no-edit", "main")  # 旧轮卡分支已合入 main: 新一轮先合入最新 main(开工稿同一纪律)
            f78._write(worktree / "tests" / "round2.txt", "round 2\n")
            f78._git(worktree, "add", "-A")
            f78._git(worktree, "commit", "-q", "-m", f"{TASK}: round 2")
            sha, tree = f78._git(worktree, "rev-parse", "HEAD"), f78._git(worktree, "rev-parse", "HEAD^{tree}")
            f78._write(r.gov / "task_cards" / TASK / "RETURN.md", f78._fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{TASK}",
                                                                         "outcome": "done"},
                                                                        f"# RETURN — {TASK}\n\n## 一句话结论\n第二轮完成。\n"))
        return 0 if expect_ready([]) else 3

    return watch


def test_item3_kiwiweb003_shape_completed_blocked_card_reopen_then_release_chain(rig):
    """KIWIWEB-003 形态回放(临时根自造): 旧语义下阻塞交回(无 outcome)被审计 PASS 后合入阻塞收据并结案 completed、生产零变更;
    件① 声明卡号形状 → reopen 开新一轮(旧报告归档, 旧记录不拦)→ 新一轮认领 → 受控发布链 plan → apply → verify 通过。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.finalization_record import write_finalization_record
    from tools.aipos_cli.record_writer import build_runtime_id

    r = rig
    gov, card_id = r["gov"], f251.CARD
    # —— 旧轮: 阻塞交回 + 审计 PASS(核实阻塞属实)+ 合入阻塞收据 + 结案(F252 前的误结案)——
    f78._write(gov / "task_cards" / card_id / "RETURN.md", f78._fm({"commit_sha": "ab" * 20, "tree_hash": "cd" * 20, "branch": f"card/{card_id}"},
                                                                 "# RETURN\n\n## 一句话结论\nBLOCK: release plan 退出 1(项目未声明 task_id_pattern), 生产零变更。\n"))
    _record(gov, "returns", card_id, f"return_{card_id}_20261030_010000_x", {"record_type": "return_record", "return_id": f"return_{card_id}_old",
                                                                             "task_id": card_id})
    vid = f"verdict_{card_id}_20261030_020000_audit"
    _record(gov, "audit_verdicts", card_id, vid, {"record_type": "audit_verdict_record", "event_type": "mcp_audit_verdict", "verdict_id": vid,
                                                 "verdict_at": "'2026-10-30T02:00:00Z'", "reviewed_task_id": card_id, "audit_task_id": f251.AUDIT_CARD,
                                                 "verdict": "PASS"})
    write_finalization_record(governance_root=gov, task_id=card_id, actor=f251.EXEC, commit="e" * 40, merge_commit="e" * 40,
                              authorization_type="verdict_ref", authorization_ref=vid, deployed=False, deploy_status="not_deployed",
                              remote_ref="origin/main")
    _record(gov, "closures", card_id, f"close_{card_id}_20261030_025711_x", {"record_type": "closure_record", "closure_id": f"close_{card_id}_old",
                                                                            "task_id": card_id})
    src = gov / "5_tasks" / "queue" / "claimed" / f"{card_id.lower()}.md"
    text = src.read_text(encoding="utf-8").replace("status: claimed", f"status: completed\ncompleted_by: {f251.EXEC}\n"
                                                                        "completed_at: '2026-10-30T02:57:11Z'")
    (gov / "5_tasks" / "queue" / "completed" / src.name).write_text(text, encoding="utf-8")
    src.unlink()
    f78._git(gov, "add", "-A")
    f78._git(gov, "commit", "-qm", "fixture: KIWIWEB-003 形态误结案")
    rc, out, err = f251._rel(r, "verify", "--task-id", card_id, "--target", f251.TARGET)
    _show(f"[件③ 旧轮] 卡 completed, release verify exit {rc}: {(out + err).strip().splitlines()[-1]}")
    # —— 件①: 卡号形状(KIWIWEB-[0-9]+ 类正则在有审计轮卡号时被拒, 用建议值)——
    assert _cp(r, "set-task-id-pattern", "--pattern", "WEB-[0-9]+", "--actor", f251.ADVISOR, "--confirm")[0] == 1
    assert _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR, "--confirm")[0] == 0
    # —— 件③: reopen(--actor = 本卡执行实例, 既有搬卡执行器判卡归属)——
    rc, out, err = _cli("queue", "reopen", "--task-id", card_id, "--actor", f251.EXEC, "--reason", "Owner 裁决: 卡号形状已声明, 开新一轮续跑发布",
                        "--workspace-root", gov)
    _show(f"---- 件③ reopen exit {rc} ----\n{out[-600:]}{err}")
    assert rc == 0, out + err
    assert (gov / "task_cards" / card_id / "rounds" / "round-1" / "RETURN.md").is_file()
    recs = nr._read_task_records(gov, card_id)
    assert recs["latest_return"] is None and recs["latest_verdict"] is None and recs["latest_closure"] is None
    from tools.aipos_cli.finalization_record import existing_finalization_records

    assert existing_finalization_records(gov, card_id) == []  # 旧轮合入记录不拦新一轮
    # —— 新一轮认领(门铸认领记录同形; 授权引用 = 项目授权)——
    pending = gov / "5_tasks" / "queue" / "pending" / f"{card_id.lower()}.md"
    claimed = gov / "5_tasks" / "queue" / "claimed" / pending.name
    claimed.write_text(pending.read_text(encoding="utf-8").replace("status: pending", "status: claimed"), encoding="utf-8")
    pending.unlink()
    now = iso_z()
    cid = build_runtime_id("claim", card_id, now, f251.EXEC)
    f78._write(gov / "5_tasks" / "records" / "claims" / card_id / f"{cid}.md",
               f78._fm({"record_type": "claim_record", "event_type": "mcp_queue_claim", "claim_id": cid, "task_id": card_id, "actor": f251.EXEC,
                        "agent_instance": f251.EXEC, "owner_policy_ref": f251.AUTH, "claimed_at": f"'{now}'"}))
    d = nr.derive_next_step(card_id, gov)
    _show(f"[件③ 新一轮推导] node={d['current_node']} state={d['current_state']} notes={d.get('notes')}")
    assert d["current_state"] == "claimed" and "queue close" not in str(d.get("command"))
    # —— 发布链 ——
    assert f251._declare_remote(r)[0] == 0
    f251._auth_revision(r, action="grant", hosts=["devbox", f251.HOST], zones=["production"])
    rc, out, err = f251._plan(r, "--tree", r["tree"])
    _show(f"---- 件③ reopen 后 release plan exit {rc} ----\n{out}{err}")
    assert rc == 0 and "授权: 就绪" in out
    rc, out, err = f251._rel(r, "apply", "--task-id", card_id, "--target", f251.TARGET, "--plan-hash", f251._plan_hash(out), "--actor", f251.EXEC)
    _show(f"---- 件③ release apply exit {rc} ----\n{out}{err}")
    assert rc == 0 and "APPLY_OK" in out
    rc, out, err = f251._rel(r, "verify", "--task-id", card_id, "--target", f251.TARGET)
    _show(f"---- 件③ release verify exit {rc} ----\n{out}{err}")
    assert rc == 0 and "VERIFY_OK" in out


def test_f252_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f252_card_policy_blocked_return.py", default_test_contract())
