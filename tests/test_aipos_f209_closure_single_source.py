"""AIPOS-F209 — 结案口径单源: task_complexity.closure_state 是「是否结案 / 结案种类 / 结论」唯一判定(F208 P0-1 / P3-1, gap #218)。

靶场一律临时 HOME + 临时治理根(git 仓 + 临时远端裸仓)+ 临时产品仓; 卡 / 记录按声明落点自造(人肉期接入样本: 顾问复核结案的 docs /
config / code 卡, 产品不写死项目 ID / 路径 / 卡号前缀); 写命令走真实产品实现(CLI main 本进程执行 / MCP 处理函数)。不读真实凭据、
不碰真实治理根、不对真实门写、不起 pi。

件① 读者统一(原 6 个读者各自判 → 全部改读 closure_state):
    state lint(conclusion 卡不报断层; 结论 BLOCK 只给信息级提示; 无任何结案形式的 completed 卡照报断层)、
    结案后补落账 governance_commit.reland_after_write 与 loop 收尾(顾问复核结案卡的 loop 运行记录终态补落账; 原命令重跑 loop 自动落账)、
    开工拒绝 next_resolver.kickoff_refusal、补充审计派审、路线图核对(pass 且非独立审计标准 = 可满足; 须独立审计的标准 = 明确提示)。
件② 入口统一: MCP lybra_mark_concluded 与 CLI mark-concluded 同收 outcome、同一写路径、同一结果; 值域只在一处声明;
    mark-concluded 结案触发工作树清理(trigger = conclude, 唯一实现 worktree_cleanup.cleanup_after)。
件④ 清理: loop_driver._has_closure / _closure_kind 私判删除, 过期「唯一实现」指针改正。
件③(棘轮)产品代码中「是否结案」私判(读 latest_closure / task_closures 键、按 closures 记录目录判、queue_state 与 "completed" 比较)
    只许在 closure_state 内; 现存命中登记 tests/f209_closure_judgment_baseline.jsonl, 只减不增。
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from ratchet_baseline import read_entries  # noqa: E402
from tools.aipos_cli.runall_discovery import repo_files  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402

import test_aipos_f207_closure_correct as f207  # noqa: E402  — 靶场骨架(home 根 + 路线图 + 远端)唯一来源
from test_aipos_f207_closure_correct import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f184_queue_landing_supersede import rig as qrig  # noqa: E402,F401  — 治理仓 + 产品仓(工作树清理)
from test_aipos_f184_queue_landing_supersede import DRIVER as Q_DRIVER, EXEC as Q_EXEC  # noqa: E402
from test_aipos_f184_queue_landing_supersede import _card as _q_card, _commit_all  # noqa: E402
from test_aipos_f94_governance_landing import rig as lrig  # noqa: E402,F401  — 治理仓 + 远端(loop 重跑落账)
from test_aipos_f94_governance_landing import DRIVER as L_DRIVER, EXEC as L_EXEC, GateDouble, _policy  # noqa: E402
from test_aipos_f94_governance_landing import _card as _l_card, _git as _l_git  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "f209_closure_judgment_baseline.jsonl"
SINGLE_SOURCE = "tools/aipos_cli/task_complexity.py"
_RECORD_KEYS = {"latest_closure", "task_closures"}
_RECORD_DIR_CALLS = {"record_dir", "_record_dir", "gate_record_files"}
_CLOSURE_KIND = "closures"
_TERMINAL = "completed"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _conclusion_fields(path: Path) -> dict:
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.task_complexity import dependency_gate_declaration

    decl = dependency_gate_declaration()["conclusion"]
    fm, _body = require_frontmatter(path)
    return {k: fm.get(k) for k in (decl["marker_field"], decl["outcome_field"], decl["note_field"], "closed_by", "status")}


def _mcp(gov: Path, args: dict) -> dict:
    """MCP lybra_mark_concluded 处理函数(门同一 handler 表); 夹具 token 只带 queue_close(声明 required_scope), 治理根 = 靶场根。"""
    from tools.mcp_server import tools as gate

    token = json.dumps({"operations": ["queue_close"], "token_ref": "f209-fixture", "expires_at": "2999-01-01T00:00:00Z"})
    with patch.dict("os.environ", {"LYBRA_CAPABILITY_TOKEN": token}), patch("tools.mcp_server.tools._repo_root", return_value=gov):
        return gate.TOOL_HANDLERS["lybra_mark_concluded"](args)["structuredContent"]


def _lint(gov: Path, task_id: str) -> tuple[int, str, dict]:
    rc, out, err = f207._cli("--workspace-root", str(gov), "state", "lint", "--task-id", task_id)
    from tools.aipos_cli.state_lint import run_state_lint

    return rc, out + err, run_state_lint(gov, task_id)


# ===========================================================================
# 件① + 件②: 顾问复核结案卡被体检 / 补落账 / 开工 / 补审 同等认可; MCP 与 CLI 同收 outcome
# ===========================================================================

def test_item1_2_lint_reland_kickoff_supplementary_and_mcp_cli_outcome_agree(rig):
    from tools.aipos_cli.governance_commit import reland_after_write, task_landing
    from tools.aipos_cli.next_resolver import kickoff_refusal
    from tools.aipos_cli.task_complexity import closure_state

    gov, home = rig["gov"], rig["home"]
    for tid in ("CFG-P", "CFG-B", "CFG-M", "CFG-X", "CFG-Y"):
        f207._card(gov, "claimed", tid)
    f207._card(gov, "completed", "CFG-L")  # 负对照: completed/ 但无任何结案形式(断层存量)
    ret = gov / "task_cards" / "CFG-P" / "RETURN.md"  # 执行体交回的报告(顾问复核的对象; N6 收账清单的台账归档件)
    ret.parent.mkdir(parents=True, exist_ok=True)
    ret.write_text("# RETURN\n\n## 一句话结论\n配置已按卡面落齐\n", encoding="utf-8")
    f207._commit(home, "fixture: cards")

    # 件② 两入口同收 outcome: CLI --outcome block 与 MCP outcome=block → 同一卡面字段与同一结案口径
    rc, out, err = f207._cli("--workspace-root", str(gov), "mark-concluded", "--task-id", "CFG-B", "--actor", f207.ADVISOR,
                             "--conclusion-note", "顾问复核: 当时缺候选绑定, 另出承接卡", "--outcome", "block")
    cli = json.loads(out)
    mcp = _mcp(gov, {"task_id": "CFG-M", "actor": f207.ADVISOR, "conclusion_note": "顾问复核: 当时缺候选绑定, 另出承接卡",
                     "outcome": "block", "dry_run": False})
    _show(f"[件② CLI] rc={rc} outcome={cli['data']['outcome']} warnings={cli['warnings']} worktree_cleanup.line={cli['data']['worktree_cleanup']['line']}")
    _show(f"[件② MCP] ok={mcp['ok']} surface={mcp['surface']} outcome={mcp['data']['outcome']} warnings={mcp['warnings']} "
          f"worktree_cleanup.line={mcp['data']['worktree_cleanup']['line']}")
    assert rc == 0 and cli["ok"] and mcp["ok"] and cli["data"]["outcome"] == mcp["data"]["outcome"] == "block"
    queue = gov / "5_tasks" / "queue" / "completed"
    fields_cli, fields_mcp = _conclusion_fields(queue / "cfg-b.md"), _conclusion_fields(queue / "cfg-m.md")
    _show(f"[件② 卡面] CLI={fields_cli}\n           MCP={fields_mcp}")
    assert fields_cli == fields_mcp and fields_cli["conclusion_outcome"] == "block"
    st_b, st_m = closure_state(gov, "CFG-B"), closure_state(gov, "CFG-M")
    assert (st_b["kind"], st_b["outcome"], st_b["concluded"]) == (st_m["kind"], st_m["outcome"], st_m["concluded"]) == ("conclusion", "block", True)
    # 缺省 outcome: 两入口同提示; 值不在声明: 两入口都拒(CLI = argparse choices, MCP = 同一写路径校验)
    cli_default = json.loads(f207._cli("--workspace-root", str(gov), "mark-concluded", "--task-id", "CFG-X", "--actor", f207.ADVISOR,
                                       "--conclusion-note", "ok", "--dry-run")[1])
    mcp_default = _mcp(gov, {"task_id": "CFG-Y", "actor": f207.ADVISOR, "conclusion_note": "ok", "dry_run": True})
    assert cli_default["warnings"] == mcp_default["warnings"] and cli_default["data"]["outcome"] == mcp_default["data"]["outcome"] == "pass"
    bad_cli = f207._cli("--workspace-root", str(gov), "mark-concluded", "--task-id", "CFG-X", "--actor", f207.ADVISOR,
                        "--conclusion-note", "ok", "--outcome", "maybe", "--dry-run")
    bad_mcp = _mcp(gov, {"task_id": "CFG-Y", "actor": f207.ADVISOR, "conclusion_note": "ok", "outcome": "maybe", "dry_run": True})
    _show(f"[件② 值不在声明] CLI rc={bad_cli[0]} {bad_cli[2].strip().splitlines()[-1]}\n                 MCP ok={bad_mcp['ok']} "
          f"{bad_mcp.get('errors') or bad_mcp.get('blocking_reasons') or bad_mcp.get('message')}")
    assert bad_cli[0] == 2 and "invalid choice: 'maybe'" in bad_cli[2]
    assert bad_mcp["ok"] is False and "只认 ['pass', 'block']" in json.dumps(bad_mcp, ensure_ascii=False)

    rc, out, err = f207._cli("--workspace-root", str(gov), "mark-concluded", "--task-id", "CFG-P", "--actor", f207.ADVISOR,
                             "--conclusion-note", "顾问复核: 产出清单逐项核对通过", "--outcome", "pass")
    assert rc == 0, out + err

    # 件① state lint: conclusion(pass / block)不报断层; BLOCK 只给信息级提示; 负对照照报断层
    for tid in ("CFG-P", "CFG-B", "CFG-L"):
        rc, text, res = _lint(gov, tid)
        _show(f"[件① state lint {tid}] rc={rc}\n" + text.rstrip())
        errors = [i for i in res["issues"] if i["severity"] == "ERROR"]
        if tid == "CFG-L":
            assert rc == 1 and any("卡在 completed/ 但无 closure 记录(断层)" in i["message"] for i in errors)
        else:
            assert not errors, errors
            assert not any("断层" in i["message"] for i in res["issues"])
    assert [n["code"] for n in _lint(gov, "CFG-B")[2]["notes"]] == ["CONCLUDED_BLOCK"] and _lint(gov, "CFG-P")[2]["notes"] == []

    # 件① 结案后补落账: loop 运行记录终态(顾问复核结案卡)由 reland_after_write 补齐(原判「未结案」跳过 = chris LANTU-ENV3 形)
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    run = LoopRunRecorder(gov, "CFG-P", driver=f207.ADVISOR, contract=load_loop_contract(), warn=lambda _w: None)
    run.end(outcome="completed", exit_code=0, reason="completed", message="已结案(顾问复核结案 mark-concluded)")
    assert not task_landing(gov, "CFG-P")["landed"]
    reland = reland_after_write(gov, "CFG-P", f207.ADVISOR, what="loop 运行记录终态")
    _show(f"[件① 补落账] {reland['line']}")
    assert reland["committed"] and reland["pushed"] and task_landing(gov, "CFG-P")["landed"]
    rc, text, res = _lint(gov, "CFG-P")
    _show(f"[件① 补落账后 state lint CFG-P] rc={rc} {text.strip()}")
    assert rc == 0 and res["issues"] == []
    never = reland_after_write(gov, "CFG-X", f207.ADVISOR, what="loop 运行记录终态")  # 未结案 = 不提交(由 N6 带上)
    assert never["committed"] is False and "CFG-X 未结案" in never["line"]

    # 件① 开工拒绝: 顾问复核结案 = 已结案(带结案口径与结论)
    refusal = kickoff_refusal(gov, "CFG-B", f207.EXEC, queue_state="completed")
    _show(f"[件① kickoff_refusal CFG-B] {refusal}")
    assert refusal["code"] == "CONCLUDED" and "结案口径 conclusion; 结论 block" in refusal["reason"]

    # 件① 补充审计派审: 顾问复核结案卡 = 已结案(不再 SUPPLEMENTARY_SOURCE_NOT_CONCLUDED); 在办卡照拒
    def dispatch(tid: str) -> dict:
        rc, out, err = f207._cli("--workspace-root", str(gov), "audit", "dispatch", "--supplementary", "--task-id", tid,
                                 "--actor", f207.ADVISOR, "--reason", "顾问复核结案的结论有争议, 补独立审计")
        return json.loads(out)

    concluded, open_card = dispatch("CFG-P"), dispatch("CFG-X")
    _show(f"[件① 补审派审 CFG-P(顾问复核结案)] blocking={concluded['blocking_reasons']}")
    _show(f"[件① 补审派审 CFG-X(在办)] blocking={open_card['blocking_reasons']}")
    assert not any(r.startswith("SUPPLEMENTARY_SOURCE_NOT_CONCLUDED") for r in concluded["blocking_reasons"])
    assert any(r.startswith("SUPPLEMENTARY_SOURCE_NOT_CONCLUDED") for r in open_card["blocking_reasons"])


# ===========================================================================
# 件①: 路线图核对按结论与标准 verify 方式判
# ===========================================================================

def test_item1_roadmap_conclusion_cards_by_outcome_and_verify(rig):
    from tools.aipos_cli.roadmap import card_artifact_dir

    gov, home = rig["gov"], rig["home"]
    ws = ("--workspace-root", str(gov))
    for cid, verify in (("C-AUTO", "auto"), ("C-BLK", "artifact")):
        rc, out, err = f207._cli("roadmap", "add-criterion", *ws, "--milestone", "M1", "--id", cid, "--statement", f"{cid} 标准",
                                 "--verify", verify, "--confirm", "--actor", f207.ADVISOR)
        assert rc == 0, out + err
    for tid, cid in (("RV-A", "C-SRC"), ("RV-U", "C-AUTO"), ("RV-B", "C-BLK")):
        f207._card(gov, "claimed", tid, milestone="M1", criteria=[cid],
                   artifacts=[{"criterion": cid, "path_glob": "review.md", "kind": "report"}])
        art = card_artifact_dir(gov, tid) / "review.md"
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_text("# review\n顾问复核产物\n", encoding="utf-8")
    f207._commit(home, "fixture: roadmap cards")
    for tid, outcome in (("RV-A", "pass"), ("RV-U", "pass"), ("RV-B", "block")):
        rc, out, err = f207._cli(*ws, "mark-concluded", "--task-id", tid, "--actor", f207.ADVISOR, "--conclusion-note",
                                 f"顾问复核 {tid}", "--outcome", outcome)
        assert rc == 0, out + err
    rows = {cid: f207._criterion(gov, cid) for cid in ("C-SRC", "C-AUTO", "C-BLK")}
    for cid, row in rows.items():
        _show(f"[件① 路线图 {cid}] state={row['state']} satisfied={row['satisfied']} closed_cards={row['closed_cards']} "
              f"basis={row['basis']} reason={row['reason']}")
    # artifact 标准: 结论 pass + 产物齐 = 自动验证通过(依据写明顾问复核结案)
    assert rows["C-SRC"]["state"] == "auto_passed" and rows["C-SRC"]["satisfied"] and "RV-A 顾问复核结案(结论 pass" in rows["C-SRC"]["basis"]
    # auto 标准须独立审计: 明确提示补充审计(不静默)
    assert rows["C-AUTO"]["state"] == "unverified" and not rows["C-AUTO"]["satisfied"] and rows["C-AUTO"]["closed_cards"] == ["RV-U"]
    assert "RV-U 为顾问复核结案(结论 pass, 无独立审计 PASS 裁决), 标准 C-AUTO 的核对方式 auto 须独立审计 PASS" in rows["C-AUTO"]["reason"]
    assert "audit dispatch --supplementary --task-id RV-U" in rows["C-AUTO"]["reason"]
    # 结论 BLOCK: 不算验证通过
    assert rows["C-BLK"]["state"] == "unverified" and "RV-B 为顾问复核结案, 结论 block" in rows["C-BLK"]["reason"]


# ===========================================================================
# 件② mark-concluded 结案触发工作树清理(trigger = conclude)
# ===========================================================================

def test_item2_mark_concluded_cleans_card_worktree_keeps_branch(qrig):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    from test_aipos_f184_queue_landing_supersede import _cli as q_cli, _git as q_git

    repo, gov = qrig["repo"], qrig["gov"]
    product = Path(json.loads((gov / "project.json").read_text(encoding="utf-8"))["code_repo"])
    _q_card(gov, "PROJ-K1", "pending")  # 写产品仓的卡(认领建卡工作树), 顾问复核结案
    _commit_all(repo, "publish")
    claimed = mutate_queue_task(gov, "claim", task_id="PROJ-K1", actor=Q_EXEC, dry_run=False, profiles=load_agent_profiles(gov),
                                with_records=True)
    worktree = Path(claimed["worktree_path"])
    assert worktree.is_dir(), claimed
    tip = q_git(product, "rev-parse", "card/PROJ-K1")
    _commit_all(repo, "claimed")
    rc, out, err = q_cli(gov, "mark-concluded", "--task-id", "PROJ-K1", "--actor", Q_DRIVER, "--conclusion-note", "顾问复核: 配置已核对",
                         "--outcome", "pass")
    data = json.loads(out)["data"]
    _show(f"[件② conclude 清理] rc={rc} worktree_cleanup={json.dumps(data['worktree_cleanup'], ensure_ascii=False)}")
    assert rc == 0 and data["worktree_cleanup"]["trigger"] == "conclude" and data["worktree_cleanup"]["ok"]
    assert data["worktree_cleanup"]["removed"] == [str(worktree)] and not worktree.exists()
    assert q_git(product, "rev-parse", "card/PROJ-K1") == tip  # 分支保留
    events = sorted((gov / "5_tasks" / "records" / "events" / "PROJ-K1").glob("worktree_cleanup_*.md"))
    _show(f"[件② conclude 清理记录] {[p.name for p in events]}")
    assert len(events) == 1 and "trigger: conclude" in events[0].read_text(encoding="utf-8")


def test_item2_cleanup_trigger_must_be_declared(tmp_path):
    from tools.aipos_cli.worktree_cleanup import cleanup_after, cleanup_declaration

    decl = cleanup_declaration()
    assert "conclude" in decl["triggers"] and set(decl["closing_triggers"]) == {"close", "conclude"}
    out = cleanup_after(tmp_path, "X-1", trigger="undeclared", actor="a")
    _show(f"[件② 未声明 trigger] ok={out['ok']} error={out['error']}")
    assert out["ok"] is False and "不在 transitions.schema queue_mutations.worktree_cleanup.triggers" in out["error"]


# ===========================================================================
# 件①: 原命令重跑 loop = 顾问复核结案卡的未落账运行记录自动补齐(advisor_notice 所述出口)
# ===========================================================================

def test_item1_rerun_loop_lands_unlanded_run_record_of_concluded_card(lrig):
    from tools.aipos_cli.governance_commit import task_landing
    from tools.aipos_cli.loop_driver import load_loop_contract, run_loop
    from tools.aipos_cli.loop_run_record import LoopRunRecorder
    from tools.aipos_cli.task_complexity import dependency_gate_declaration

    gov, repo = lrig["gov"], lrig["repo"]
    decl = dependency_gate_declaration()["conclusion"]
    tid = "AIPOS-F209L"
    _l_card(gov, tid, "completed", assigned=L_EXEC,
            extra={decl["marker_field"]: decl["marker_value"], decl["outcome_field"]: "pass", decl["note_field"]: "顾问复核通过",
                   "closed_by": "conclusion_marker"})
    ret = gov / "task_cards" / tid / "RETURN.md"
    ret.parent.mkdir(parents=True, exist_ok=True)
    ret.write_text("# RETURN\n\n## 一句话结论\n文档已落齐\n", encoding="utf-8")
    _policy(gov)
    _l_git(repo, "add", "-A", "--", "2_projects/gov/5_tasks/queue", "2_projects/gov/5_tasks/policies", f"2_projects/gov/task_cards/{tid}")
    _l_git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fixture: concluded card")
    # 早先一次 loop 结束时运行记录终态未落账(F209 前 reland 判「未结案」跳过; chris LANTU-ENV3 / ENV3B 形)
    earlier = LoopRunRecorder(gov, tid, driver=L_DRIVER, contract=load_loop_contract(), warn=lambda _w: None)
    earlier.end(outcome="completed", exit_code=0, reason="completed", message="已结案(顾问复核结案 mark-concluded)")
    assert not task_landing(gov, tid)["landed"]
    out = io.StringIO()
    res = run_loop(tid, gov, actor=L_DRIVER, out=out, execute=GateDouble(gov), interval=0.01, max_wait=1, max_steps=5)
    text = out.getvalue()
    _show(f"[件① 原命令重跑 loop] exit={res.exit_code} outcome={res.outcome}\n" + text.rstrip())
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert "补落账:" in text and "committed=True" in text
    landing = task_landing(gov, tid)
    assert landing["landed"], landing
    status = _l_git(repo, "status", "--porcelain", "-uall", "--", f"2_projects/gov/5_tasks/records/loop_runs/{tid}")
    _show(f"[件① 重跑后 loop_runs/{tid} git status] {status!r}")
    assert status == ""


# ===========================================================================
# 件④ + 单源: 私判删除、声明一处
# ===========================================================================

def test_item4_private_judgments_removed_and_declarations_single_source():
    from tools.aipos_cli.task_complexity import conclusion_outcomes, dependency_gate_declaration
    from tools.schema_loader import code_repo_schema_root, load_schema

    loop_src = (REPO_ROOT / "tools/aipos_cli/loop_driver.py").read_text(encoding="utf-8")
    assert "def _has_closure(" not in loop_src and "def _closure_kind(" not in loop_src
    for rel in ("tools", "schema", "docs", "agents"):
        for path in (REPO_ROOT / rel).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".json", ".md"} and "tests" not in path.parts:
                text = path.read_text(encoding="utf-8")  # 定义 / 调用 / 「唯一实现」指针都不再有(说明删除的注释除外)
                assert "_has_closure(" not in text and "loop_driver._has_closure" not in text, path
    gate = dependency_gate_declaration()["conclusion"]
    card_fields = load_schema("card", code_repo_schema_root())["fields"]
    verbs = load_schema("verbs", code_repo_schema_root())["verbs"]
    assert "enum" not in card_fields[gate["outcome_field"]] and "enum" not in verbs["lybra_mark_concluded"]["parameters"]["properties"]["outcome"]
    assert conclusion_outcomes() == tuple(gate["outcomes"])
    assert "MCP 面未接" not in json.dumps(verbs["lybra_mark_concluded"], ensure_ascii=False)
    ver = verbs["lybra_roadmap"]["status"]["verification"]
    assert ver["independent_audit"] == ["auto"] and "conclusion_passed" in ver["basis"]
    # 六个读者都调唯一判定
    readers = {"tools/aipos_cli/state_lint.py": "closure_state(governance_root, task_id, located=",
               "tools/aipos_cli/governance_commit.py": "closure_state(root, task_id)[\"kind\"]",
               "tools/aipos_cli/loop_driver.py": "closure_state(governance_root, task_id)[\"kind\"]",
               "tools/aipos_cli/next_resolver.py": "closure = closure_state(workspace_root, task_id)",
               "tools/aipos_cli/board_adapter.py": "source_closure = closure_state(repo_root, source_task_id_for_verdict)",
               "tools/aipos_cli/roadmap_drift.py": "closure_state(root, task_id, located=located)"}
    for rel, call in readers.items():
        assert call in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel
    assert (REPO_ROOT / SINGLE_SOURCE).read_text(encoding="utf-8").count("def closure_state(") == 1


# ===========================================================================
# 件③ 棘轮: 「是否结案」私判只许在 closure_state(只减不增)
# ===========================================================================

def fingerprint(line: str) -> str:
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:12]


def scoped_product_files(repo_root: Path = REPO_ROOT) -> list[str]:
    """扫描面 = 产品仓文件清单中的 .py 产品源码, 排除 run-all 自动发现的测试文件、conftest.py、路径含 tests/ 段的文件(与 F132 / F133
    棘轮同一口径)与唯一实现本体 task_complexity.py。"""
    files = repo_files(repo_root)
    tests = set(discover_test_files(files, default_test_contract()))
    out = []
    for rel in files:
        parts = rel.split("/")
        if not rel.endswith(".py") or rel in tests or parts[-1] == "conftest.py" or "tests" in parts[:-1] or rel == SINGLE_SOURCE:
            continue
        out.append(rel)
    return sorted(out)


def _call_name(func: ast.AST) -> str:
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _is_str(node: ast.AST, values: set[str] | str) -> bool:
    wanted = {values} if isinstance(values, str) else values
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in wanted


def _hit_nodes(tree: ast.Module) -> list[ast.AST]:
    """三类私判式样:
    ① 读门记录汇总的结案键: `<x>.get("latest_closure" | "task_closures" …)` / `<x>["latest_closure" | "task_closures"]`(Load);
    ② 按 closures 记录目录判: record_dir / _record_dir / gate_record_files 带字面量 "closures" 参数;
    ③ 队列态 / 记录推导态与 "completed" 的 == / != 比较(另一侧源码含 queue 或 record_state)。"""
    hits: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name == "get" and node.args and _is_str(node.args[0], _RECORD_KEYS):
                hits.append(node)
            elif name in _RECORD_DIR_CALLS and any(_is_str(a, _CLOSURE_KIND) for a in node.args):
                hits.append(node)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_str(node.slice, _RECORD_KEYS):
            hits.append(node)
        elif isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if (any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops) and any(_is_str(s, _TERMINAL) for s in sides)):
                other = " ".join(ast.unparse(s) for s in sides if not _is_str(s, _TERMINAL))
                if "queue" in other or "record_state" in other:
                    hits.append(node)
    return hits


def _enclosing(tree: ast.Module) -> dict[int, str]:
    out: dict[int, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else name
            if hasattr(child, "lineno"):
                out.setdefault(child.lineno, child_name)
            visit(child, child_name)

    visit(tree, "<module>")
    return out


def scan_source(rel: str, text: str) -> list[dict[str, str]]:
    tree = ast.parse(text)
    lines = text.splitlines()
    funcs = _enclosing(tree)
    seen: set[int] = set()
    hits: list[dict[str, str]] = []
    for node in sorted(_hit_nodes(tree), key=lambda n: (n.lineno, n.col_offset)):
        if node.lineno in seen:  # 一行一条(同行多式样只记一次)
            continue
        seen.add(node.lineno)
        line = lines[node.lineno - 1]
        hits.append({"file": rel, "func": funcs.get(node.lineno, "<module>"), "fp": fingerprint(line), "text": line.strip()[:160]})
    return hits


def scan_repo(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for rel in scoped_product_files(repo_root):
        # 解码 / 语法失败 = 抛(fail-closed: 扫描面内的源码读不了不得当作「无命中」放过)
        hits.extend(scan_source(rel, (repo_root / rel).read_text(encoding="utf-8")))
    return hits


def _key(entry: dict[str, object]) -> tuple[str, str, str]:
    return (str(entry["file"]), str(entry["func"]), str(entry["fp"]))


def test_ratchet_baseline_well_formed():
    for entry in read_entries(BASELINE_PATH):
        assert set(entry) == {"file", "func", "fp", "text", "reason"}, entry
        assert str(entry["reason"]).strip(), entry


def test_ratchet_no_new_closure_judgment_outside_closure_state_and_baseline_only_shrinks():
    hits = scan_repo()
    baseline = read_entries(BASELINE_PATH)
    current, allowed = Counter(_key(h) for h in hits), Counter(_key(e) for e in baseline)
    new, stale = current - allowed, allowed - current
    _show(f"[件③ 棘轮] 扫描面 {len(scoped_product_files())} 个产品源文件; 现存「是否结案」私判式样命中 {sum(current.values())} 处; "
          f"基线 {len(baseline)} 条")
    for hit in hits:
        _show(f"  命中 {hit['file']}::{hit['func']} {hit['text']}")
    assert not new, ("产品源码新增「是否结案」私判(改调 task_complexity.closure_state: kind / outcome / concluded): "
                     + "; ".join(f"{h['file']}::{h['func']} {h['text']}" for h in hits if _key(h) in new))
    assert not stale, f"基线条目已不存在(已修 / 已删)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {sorted(stale)}"


def test_ratchet_pattern_catches_known_shapes_and_spares_benign():
    bad = '''
def kickoff(root, tid, queue_state, records):
    if queue_state == "completed" or records.get("latest_closure"):
        return True
    closures = records["task_closures"]
    return bool(gate_record_files(record_dir(root, "closures", tid), "closures")) or source.get("queue_state") != "completed"
'''
    texts = [h["text"] for h in scan_source("x.py", bad)]
    assert texts == ['if queue_state == "completed" or records.get("latest_closure"):', 'closures = records["task_closures"]',
                     'return bool(gate_record_files(record_dir(root, "closures", tid), "closures")) or source.get("queue_state") != "completed"'], texts
    clean = '''
def ok(root, tid):
    state = closure_state(root, tid)
    result = {"latest_closure": None}
    result["latest_closure"] = 1
    status = "completed" if state["concluded"] else "open"
    return event_type == "completed" or metadata.get("executor_status") == "completed"
'''
    assert scan_source("y.py", clean) == []


def test_f209_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import is_test_file

    assert is_test_file("tests/test_aipos_f209_closure_single_source.py", default_test_contract())
