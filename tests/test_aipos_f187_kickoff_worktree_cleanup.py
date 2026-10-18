"""AIPOS-F187 — 子 agent 开工稿由产品生成(`lybra kickoff`)+ 结案 / 撤回后自动清理卡与审计工作树 + 存量 `lybra project cleanup-worktrees`。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi):
  - lybra 形: AIPOS-F90 rig(临时治理根 + 进程内门 + 临时产品仓), 子 agent 执行者(claude-code, 本机会话)经接入日志 land 事件登记,
    审计者为本机 pi 工位(F95 夹具同一 writer); 全程只经 `lybra loop`(认领 → 等交回 → 派审 → 等审计 → 裁决 → finalize 替身 → 结案 → 落账)。
  - chris 形: 临时治理根(chris 落点声明 + 多仓 repos.items + 跨机工位声明), Codex 子 agent 执行者会话在他机(land host ≠ 本机);
    认领走门认领核心 mutate_queue_task claim(与门受控执行同一实现)。
  - 清理: AIPOS-F184 rig(临时治理仓 + 临时远端裸仓 + 临时产品仓), 撤回 / 撤换走 CLI(与顾问同一入口, 写后自动落账)。

件① kickoff: lybra / chris 形各出一份开工稿原文(工作树 / 基线 sha / 报告落点与必填字段 / 禁写清单 / 验证要求 / 草稿目录 / 残留进程检查);
   审计卡出审计体开工稿; --role 不符 / 未认领 / 草稿目录落治理根 = 拒。
件② loop 子 agent 等待提示与 loop status 下一动作指向 `lybra kickoff --task-id <卡>`; 顾问章程 / 技能手册 / README 用 kickoff。
件③ 结案后卡与 R 工作树自动删除、分支仍在; 脏树保留并报告; withdraw / supersede 后清理; carried_from 引用保留; 清理失败不影响结案。
件④ cleanup-worktrees 预演 / 确认(跨两仓; 删 / 脏 / 在途 / 承继 / 不认识)。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f90_loop_one_stage import AUDIT, POLICY, TASK, rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _git, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f95_harness_launch import _land as _land_ws, _workstation  # noqa: E402  — pi 工位 / land 事件同一 writer
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

KICKOFF = "lybra_kickoff"
CLEANUP = "lybra_project_cleanup_worktrees"
RESIDUAL = "pytest|run-all|until grep|fixture-serve"
RATCHET = "tests/*_baseline.jsonl 只减不增(含 CJK 守卫)"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    text = "".join(ln for ln in out.getvalue().splitlines(keepends=True) if not ln.startswith("治理根: "))
    return int(rc or 0), text, err.getvalue()


def _land_subagent(gov: Path, instance: str, *, project: str, harness: str, host: str) -> None:
    """子 agent 执行者接入 land 事件(产品唯一渲染 _trail_line + 唯一写口 _write_trail_line; detail = subagent_land_detail)。"""
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path, subagent_land_detail

    _write_trail_line(enrollment_trail_path(gov), _trail_line(
        action="land", code_id=f"enroll_fixture_{instance}", role="executor", instance=instance, project=project, by="(agent-enroll)",
        reason=subagent_land_detail(host=host, harness=harness, mode="subagent", files=["connection.json"])))


def _update_project(gov: Path, **changes) -> None:
    path = gov / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sibling_project(parent: Path, name: str) -> Path:
    """同 home 根下另一已建项目(project.json + 队列根), 开工稿「禁写其他项目治理根」的取证对象。"""
    other = parent / name
    for sub in ("pending", "claimed"):
        (other / "5_tasks" / "queue" / sub).mkdir(parents=True, exist_ok=True)
    _write(other / "project.json", json.dumps({"project": name, "config_version": 1}))
    return other


# ===========================================================================
# 件① + 件② + 件③: lybra 形全链(loop)——两份开工稿原文、等待提示、结案后自动清理
# ===========================================================================

def _lybra_shape(rig, tmp_path: Path) -> Path:
    gov = rig.gov
    _land_subagent(gov, EXEC, project="lybra", harness="claude-code", host=socket.gethostname())
    ws = _workstation(rig.home, gov, "probe-auditor", "auditor", AUDITOR)
    _land_ws(gov, AUDITOR, "auditor", ws)
    scratch_root = tmp_path / "scratch-root"
    _update_project(gov, paths={"scratch_root": str(scratch_root)},
                    test_contract={"runall_path": "tests/run-all.sh", "ratchet": RATCHET, "residual_processes": RESIDUAL})
    _write(rig.code_repo / "tests" / "run-all.sh", "#!/usr/bin/env bash\n# lybra-runall: discover\n")
    _git(rig.code_repo, "add", "tests/run-all.sh")
    _git(rig.code_repo, "commit", "-q", "-m", "run-all")
    _sibling_project(gov.parent, "other-project")
    f90._card(gov, TASK, "pending", lane={"repo": str(rig.code_repo), "paths": ["tests/", "tools/"]})
    init_governance_repo(gov)
    return scratch_root


def _kickoff_watch(rig, seen: dict, *, before_executor=None):
    def watch(args, expect_ready):
        patterns = " ".join(args.expect)
        if f"/{AUDIT}/" in patterns or patterns.startswith(f"task_cards/{AUDIT}"):
            seen["auditor"] = _cli("kickoff", "--task-id", AUDIT, "--workspace-root", str(rig.gov))
            seen["auditor_role_mismatch"] = _cli("kickoff", "--task-id", AUDIT, "--role", "executor", "--workspace-root", str(rig.gov))
            seen["status_audit"] = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov))
            f90._auditor_work(rig)
        else:
            seen["executor"] = _cli("kickoff", "--task-id", TASK, "--workspace-root", str(rig.gov))
            seen["executor_json"] = _cli("kickoff", "--task-id", TASK, "--workspace-root", str(rig.gov), "--json")
            seen["status_exec"] = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov))
            if before_executor:
                before_executor()
            f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    return watch


def test_item1_2_3_lybra_shape_kickoff_hint_and_close_cleanup(rig, tmp_path):
    from tools.aipos_cli.loop_driver import run_loop

    scratch_root = _lybra_shape(rig, tmp_path)
    gov, product = rig.gov, rig.code_repo
    main_before = _git(product, "rev-parse", "main")  # 认领时卡分支起点(结案后 main 已合入卡分支)
    seen: dict = {}
    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, policy_id=POLICY, out=out, watch=_kickoff_watch(rig, seen), interval=0.05, max_wait=5,
                   max_steps=30)
    text = out.getvalue()
    assert res.exit_code == 0 and res.outcome == "completed", text

    # ---- 件② loop 子 agent 等待提示 + loop status 下一动作 ----
    manual = next(ln for ln in text.splitlines() if "manual:" in ln and "子 agent" in ln)
    _show(f"---- 件② loop 等子 agent 交回的提示原文 ----\n{manual}")
    assert f"开工稿 = `lybra kickoff --task-id {TASK}` 的输出" in manual and "my-tasks --actor" not in manual
    rc, status, err = seen["status_exec"]
    na = next(ln for ln in status.splitlines() if "顾问下一动作" in ln)
    _show(f"---- 件② loop status 顾问下一动作原文(等执行体) ----\n{na}")
    assert "continue_wait(manual_kickoff)" in na and "开工稿用 lybra kickoff 输出" in na and f"lybra kickoff --task-id {TASK}" in na

    # ---- 件① 执行体开工稿原文(lybra 形) ----
    rc, brief, err = seen["executor"]
    _show(f"---- 件① lybra kickoff --task-id {TASK}(lybra 形, 执行体)exit {rc} ----\n{brief}")
    assert rc == declared_exit_code(KICKOFF, "ok") == 0, err
    worktree = f90.nr.card_worktree_location(gov, TASK)[1]
    data = json.loads(seen["executor_json"][1])
    base_sha = data["values"]["base_sha"]
    assert re.fullmatch(r"[0-9a-f]{40}", base_sha) and base_sha == main_before
    scratch = scratch_root / TASK.lower()
    for needle in (
        f"你是 lybra 项目的执行体(实例 {EXEC}), 负责完成卡 {TASK}",
        f"1. 读章程: `cd {gov} && lybra charter --role executor --instance {EXEC}`",
        f"2. 读卡面全文: `{gov / '5_tasks' / 'queue' / 'claimed' / (TASK.lower() + '.md')}`",
        f"3. 工作树(认领时已建): `{worktree}`, 分支 card/{TASK}(基于 main {base_sha}; 当前 tip {base_sha})",
        "车道(lane.paths): `tests/`, `tools/`",
        f"- 禁写治理根 `{gov}`(唯一例外: 本卡报告落点 `{gov / 'task_cards' / TASK}` 及其下 artifacts/、run_long/)",
        f"- 禁写其他项目治理根(只读取证允许): `{gov.parent / 'other-project'}`",
        "禁启动 pi, 禁非预演的 sync / enroll / deploy",
        "- no_retry_after_deny:", "- no_privilege_escalation:",
        f"草稿 / 日志写到 `{scratch}`(自建; 来源 project.json paths.scratch_root)",
        f"- 全量命令: `bash tests/run-all.sh 2>&1 | tee {scratch}/runall-<tip|main>.log`",
        f"--task-id {TASK} --name <运行名> --cwd {worktree} -- '<验证命令>'",
        "- 两边基线: 本分支最终 tip 必须独立跑一次全量", "main 侧先查合并后回归记录基线 `lybra --workspace-root",
        f"- 棘轮: {RATCHET}",
        f"- 交回前跑 `pgrep -af \"{RESIDUAL}\"`",
        f"- 报告写到 `{gov / 'task_cards' / TASK / 'RETURN.md'}`",
        f"  - branch: (卡分支名 card/{TASK})", "  - commit_sha: (", "  - tree_hash: (",
        "- 写完即止, 不运行任何门动词",
        "最后给顾问一段简短总结: tip sha、改动要点、测试计数、已知局限。",
    ):
        assert needle in brief, f"开工稿缺 {needle!r}"
    assert "fixture-not-a-secret" not in brief and "token" not in brief.lower().replace("token 永不回显", "")
    assert "{" not in re.sub(r"\^\{tree\}", "", brief)  # 无未渲染占位
    assert "会话所在机" not in brief  # 本机会话 = 无 remote_note
    assert seen["executor_json"][0] == 0 and data["role"] == "executor" and data["instance"] == EXEC

    # ---- 件① 审计体开工稿原文 + --role 不符拒 ----
    rc, abrief, err = seen["auditor"]
    _show(f"---- 件① lybra kickoff --task-id {AUDIT}(按卡判为审计体)exit {rc} ----\n{abrief}")
    assert rc == 0, err
    tip = _git(product, "rev-parse", f"card/{TASK}")
    audit_tree = f90.nr.card_worktree_location(gov, AUDIT)[1]
    for needle in (f"你是 lybra 项目的审计体(实例 {AUDITOR}), 负责审计卡 {AUDIT}(被审卡 {TASK})",
                   f"取证工作树(认领时已建, 只读, detached 于被审分支 tip): `{audit_tree}`, 被审分支 card/{TASK}(tip {tip}",
                   "- 产品仓只读", f"  - commit_sha: {tip}", "独立全量基线", f"- 禁写工位 `{rig.home / 'workstations' / 'probe-auditor'}`"):
        assert needle in abrief, f"审计开工稿缺 {needle!r}"
    rc, _o, err = seen["auditor_role_mismatch"]
    _show(f"[件① --role 不符] exit {rc}: {err.strip()}")
    assert rc == declared_exit_code(KICKOFF, "refused") and "KICKOFF_ROLE_MISMATCH" in err and f"--role auditor" in err

    # ---- 件③ 结案后自动清理: 卡与 R 工作树删除、分支仍在、清理记录随卡落账 ----
    listed = _git(product, "worktree", "list")
    _show(f"---- 件③ 结案后 git worktree list ----\n{listed}\n[件③ 分支] card/{TASK} = {_git(product, 'rev-parse', f'card/{TASK}')}")
    assert not worktree.exists() and not audit_tree.exists() and str(worktree) not in listed and str(audit_tree) not in listed
    assert _git(product, "rev-parse", f"card/{TASK}") == tip  # 分支保留
    records = sorted((gov / "5_tasks" / "records" / "events" / TASK).glob("worktree_cleanup_*.md"))
    assert len(records) == 1, records
    record = records[0].read_text(encoding="utf-8")
    _show(f"---- 件③ 清理记录 {records[0].name} ----\n{record}")
    assert "trigger: close" in record and str(worktree) in record and str(audit_tree) in record
    assert _git(gov, "status", "--porcelain", "--", str(records[0])) == ""  # 已随卡落账
    assert "worktree_cleanup:" not in text  # 无保留的树 = loop 不出保留行


def test_item3_close_cleanup_failure_and_dirty_do_not_block_closure(rig, tmp_path, monkeypatch):
    """清理失败(删不掉)/ 脏树: 结案照常完成并落账; loop 出「保留」一行(路径 + 原因 / 改动计数)并进运行记录 worktree_cleanups。"""
    from tools.aipos_cli import worktree_cleanup as wc
    from tools.aipos_cli.loop_driver import run_loop
    from tools.aipos_cli.loop_run_record import loop_runs_dir

    _lybra_shape(rig, tmp_path)
    gov, product = rig.gov, rig.code_repo
    monkeypatch.setattr(wc, "remove_worktree", lambda code_repo, worktree: "fatal: simulated remove failure")
    seen: dict = {}

    def dirty():  # 执行体在卡工作树留一个未跟踪草稿(不提交)
        _write(f90.nr.card_worktree_location(gov, TASK)[1] / "notes" / "Plan.md", "draft\n")

    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, policy_id=POLICY, out=out, watch=_kickoff_watch(rig, seen, before_executor=dirty),
                   interval=0.05, max_wait=5, max_steps=30)
    text = out.getvalue()
    residual = next(ln for ln in text.splitlines() if "worktree_cleanup:" in ln)
    _show(f"---- 件③ 脏树 + 删失败: loop 保留行原文 ----\n{residual}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    worktree, audit_tree = f90.nr.card_worktree_location(gov, TASK)[1], f90.nr.card_worktree_location(gov, AUDIT)[1]
    assert worktree.is_dir() and audit_tree.is_dir()
    # 保留行 = 结案后只读重判: 脏树报改动计数; 删失败的树仍干净 = 「尚未清理」并给存量命令(失败原文在清理记录)
    assert f"{worktree}(未提交改动 1 个, 不碰)" in residual and f"{audit_tree}(已终态且干净, 尚未清理(lybra project cleanup-worktrees --confirm))" in residual
    record = next((gov / "5_tasks" / "records" / "events" / TASK).glob("worktree_cleanup_*.md")).read_text(encoding="utf-8")
    _show(f"---- 件③ 清理记录(删失败 + 脏)----\n{record}")
    assert f"{audit_tree} — 删失败: fatal: simulated remove failure" in record and f"{worktree} — 未提交改动 1 个, 不碰" in record
    meta_files = sorted(loop_runs_dir(gov, TASK).glob("looprun_*.md"))
    meta = f90.nr._read_frontmatter(meta_files[-1])
    _show(f"[件③ 运行记录 worktree_cleanups] {json.dumps(meta.get('worktree_cleanups'), ensure_ascii=False)}")
    assert meta["worktree_cleanups"][0]["card"] == TASK and "未提交改动 1 个" in meta["worktree_cleanups"][0]["detail"]
    assert (gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    rc, status, err = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(gov))
    na = next(ln for ln in status.splitlines() if "顾问下一动作" in ln)
    _show(f"---- 件③ 结案后 loop status 顾问下一动作原文(保留的树报路径与改动计数) ----\n{na}")
    assert "card_done_take_next" in na and f"{worktree}(未提交改动 1 个, 不碰)" in na


# ===========================================================================
# 件①: chris 形(多仓 repos.items + Codex 子 agent 在他机)
# ===========================================================================

CHRIS = "chris-demo"
CHRIS_EXEC = f"exec.{CHRIS}.advmac"
CHRIS_CARD = "HBX-7"


def _chris_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    gov = f78._make_gov(tmp_path, monkeypatch, shape="chris", project=CHRIS)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    web = f78._init_product_repo(tmp_path / "repos" / "web6")
    app = f78._init_product_repo(tmp_path / "repos" / "android-app")
    _update_project(gov, code_repo=str(web), repos={"default": "web6", "items": {"web6": str(web), "android-app": str(app)}},
                    workstations={CHRIS_EXEC: {"gate_ssh_alias": "gate-dev", "material_access": "经 ssh gate-dev 读写治理根与产品仓"}})
    (gov / "5_tasks" / "records" / "sessions").mkdir(parents=True, exist_ok=True)
    _land_subagent(gov, CHRIS_EXEC, project=CHRIS, harness="codex", host="advmac")
    f78._card(gov, CHRIS_CARD, "pending", assigned=CHRIS_EXEC,
              extra={"project": CHRIS, "harness": "codex", "lane": {"repo": "android-app", "paths": ["app/", "Scripts/"]}})
    return {"gov": gov, "web": web, "app": app}


def test_item1_chris_shape_multi_repo_codex_remote_kickoff(tmp_path, monkeypatch):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    r = _chris_rig(tmp_path, monkeypatch)
    gov, app = r["gov"], r["app"]
    rc, _o, err = _cli("kickoff", "--task-id", CHRIS_CARD, "--workspace-root", str(gov))
    _show(f"[件① chris 未认领] exit {rc}: {err.strip()}")
    assert rc == declared_exit_code(KICKOFF, "refused") and "KICKOFF_NOT_STARTABLE" in err and "NOT_CLAIMED" in err
    claimed = mutate_queue_task(gov, "claim", task_id=CHRIS_CARD, actor=CHRIS_EXEC, dry_run=False, profiles=load_agent_profiles(gov),
                                with_records=True)
    assert claimed.get("moved"), claimed
    worktree = Path(claimed["worktree_path"])
    assert worktree.parent == app / ".worktrees"
    rc, brief, err = _cli("kickoff", "--task-id", CHRIS_CARD, "--workspace-root", str(gov))
    _show(f"---- 件① lybra kickoff --task-id {CHRIS_CARD}(chris 形: 多仓 + Codex 子 agent 在他机)exit {rc} ----\n{brief}")
    assert rc == 0, err
    base_sha = _git(app, "merge-base", "main", f"card/{CHRIS_CARD}")
    for needle in (
        f"你是 {CHRIS} 项目的执行体(实例 {CHRIS_EXEC}), 负责完成卡 {CHRIS_CARD}",
        f"1. 读章程: `ssh gate-dev 'cd {gov} && lybra charter --role executor --instance {CHRIS_EXEC}'`",
        f"3. 工作树(认领时已建): `{worktree}`, 分支 card/{CHRIS_CARD}(基于 main {base_sha}",
        f"产品仓: android-app = `{app}`(project.json repos.items, 卡 lane.repo); 车道(lane.paths): `app/`, `Scripts/`",
        f"注意: 你的会话所在机 advmac ≠ 治理根所在机 {socket.gethostname()}",
        "经 ssh 访问(ssh 别名 gate-dev)",
        f"- 报告写到 `{gov / '5_tasks' / 'records' / 'returns' / CHRIS_CARD / 'RETURN.md'}`",
        f"本卡报告落点 `{gov / '5_tasks' / 'records' / 'returns' / CHRIS_CARD}`",
        "- 全量命令: 本项目未声明全量测试清单(project.json test_contract.runall_path)",
        "- 棘轮: 本项目未声明棘轮(project.json test_contract.ratchet)",
        "- 交回前跑 `pgrep -af \"pytest|run-all|until grep\"`",
        "门交互硬规矩以章程为准(",
    ):
        assert needle in brief, f"chris 开工稿缺 {needle!r}"
    default_scratch = Path(__import__("tempfile").gettempdir()) / "lybra-scratch" / CHRIS / CHRIS_CARD.lower()
    assert f"草稿 / 日志写到 `{default_scratch}`(自建; 来源 未声明 paths.scratch_root, 取系统临时目录" in brief

    # 草稿目录落治理根内 = 拒(fail-closed), 出口 = set-paths
    _update_project(gov, paths={**json.loads((gov / "project.json").read_text())["paths"], "scratch_root": "scratch"})
    rc, _o, err = _cli("kickoff", "--task-id", CHRIS_CARD, "--workspace-root", str(gov))
    _show(f"[件① 草稿目录落治理根] exit {rc}: {err.strip()}")
    assert rc == declared_exit_code(KICKOFF, "refused") and "KICKOFF_SCRATCH_IN_GOVERNANCE" in err
    assert f"lybra project set-paths {CHRIS} --key scratch_root" in err


def test_item1_governance_only_card_kickoff_without_worktree(tmp_path, monkeypatch):
    """只写治理根的卡(task_mode 不写产品仓, 无 lane.repo; AIPOS-F178 判据)不建树: 开工稿给「不建卡工作树」一行与治理根报告落点,
    无工作树 / 分支 / 基线行(占位为空 = 行省略), 其余各节照出。"""
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    r = _chris_rig(tmp_path, monkeypatch)
    gov = r["gov"]
    f78._card(gov, "HBX-G1", "pending", assigned=CHRIS_EXEC, task_mode="governance", extra={"project": CHRIS, "harness": "codex"})
    claimed = mutate_queue_task(gov, "claim", task_id="HBX-G1", actor=CHRIS_EXEC, dry_run=False, profiles=load_agent_profiles(gov),
                                with_records=True)
    assert claimed.get("moved") and not claimed.get("worktree_created"), claimed
    rc, brief, err = _cli("kickoff", "--task-id", "HBX-G1", "--workspace-root", str(gov))
    _show(f"---- 件① 只写治理根的卡 kickoff exit {rc} ----\n{brief}")
    assert rc == 0, err
    assert "3. 本卡不建卡工作树 / 卡分支: 本卡只写治理根" in brief and "工作树(认领时已建)" not in brief and "产品仓:" not in brief
    assert f"- 报告写到 `{gov / '5_tasks' / 'records' / 'returns' / 'HBX-G1' / 'RETURN.md'}`" in brief


# ===========================================================================
# 件③④: 撤回 / 撤换后清理、承继保留、存量批量(F184 rig)
# ===========================================================================

from test_aipos_f184_queue_landing_supersede import _card as _q_card, _commit_all, rig as qrig  # noqa: E402,F401
from test_aipos_f184_queue_landing_supersede import DRIVER as Q_DRIVER, EXEC as Q_EXEC  # noqa: E402


def _q_claim(gov: Path, task_id: str) -> Path:
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    claimed = mutate_queue_task(gov, "claim", task_id=task_id, actor=Q_EXEC, dry_run=False, profiles=load_agent_profiles(gov), with_records=True)
    assert claimed.get("moved"), claimed
    return Path(claimed["worktree_path"])


def _q_product(gov: Path) -> Path:
    return Path(json.loads((gov / "project.json").read_text(encoding="utf-8"))["code_repo"])


def _commit_in(worktree: Path, rel: str, msg: str) -> str:
    _write(worktree / rel, f"{msg}\n")
    _git(worktree, "add", "--", rel)
    _git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return _git(worktree, "rev-parse", "HEAD")


def test_item3_withdraw_cleans_clean_tree_keeps_branch_and_lands_record(qrig):
    repo, gov = qrig["repo"], qrig["gov"]
    product = _q_product(gov)
    _q_card(gov, "PROJ-W1", "pending")
    _commit_all(repo, "publish")
    worktree = _q_claim(gov, "PROJ-W1")
    tip = _commit_in(worktree, "app/w.py", "work")
    _commit_all(repo, "claimed")
    rc, out, err = _cli("--workspace-root", str(gov), "queue", "withdraw", "--task-id", "PROJ-W1", "--actor", Q_DRIVER, "--reason", "方向变了")
    data = json.loads(out[:out.rindex("}") + 1])["data"]
    _show(f"[件③ withdraw] rc={rc} worktree_cleanup.line = {data['worktree_cleanup']['line']}")
    assert rc == 0, (out, err)
    assert data["worktree_cleanup"]["removed"] == [str(worktree)] and not worktree.exists()
    assert _git(product, "rev-parse", "card/PROJ-W1") == tip
    landed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    _show(f"[件③ withdraw 落账] {landed}")
    assert any("/5_tasks/records/events/PROJ-W1/worktree_cleanup_" in f for f in landed), landed


def test_item3_supersede_cleans_original_and_carry_branch_keeps_until_claim(qrig):
    repo, gov = qrig["repo"], qrig["gov"]
    product = _q_product(gov)
    # 撤换(不承继): 原卡树删
    _q_card(gov, "PROJ-S1", "pending")
    _q_card(gov, "PROJ-S1B", "pending")
    _commit_all(repo, "publish")
    tree_s1 = _q_claim(gov, "PROJ-S1")
    _commit_in(tree_s1, "app/s.py", "s1")
    _commit_all(repo, "claimed")
    rc, out, err = _cli("--workspace-root", str(gov), "queue", "supersede", "--task-id", "PROJ-S1", "--by", "PROJ-S1B", "--actor", Q_DRIVER,
                        "--owner-words", "改目标", "--confirm", "--json")
    res = json.loads(out[:out.rindex("}") + 1])
    _show(f"[件③ supersede] rc={rc} {res['worktree_cleanup']['line']}")
    assert rc == 0, (out, err)
    assert not tree_s1.exists() and _git(product, "rev-parse", "--verify", "card/PROJ-S1")
    assert any("worktree_cleanup_" in w for w in res["written"])

    # 撤换承继(--carry-branch): 新卡 pending 记 carried_from → 原卡树保留(承继引用), 原卡分支保留
    _q_card(gov, "PROJ-C1", "pending")
    _q_card(gov, "PROJ-C1B", "pending")
    _commit_all(repo, "publish carry")
    tree_c1 = _q_claim(gov, "PROJ-C1")
    tip = _commit_in(tree_c1, "app/c.py", "c1")
    _commit_all(repo, "claimed carry")
    rc, out, err = _cli("--workspace-root", str(gov), "queue", "supersede", "--task-id", "PROJ-C1", "--by", "PROJ-C1B", "--actor", Q_DRIVER,
                        "--owner-words", "契约更正", "--carry-branch", "--confirm", "--json")
    res = json.loads(out[:out.rindex("}") + 1])
    line = res["worktree_cleanup"]["line"]
    _show(f"[件③ supersede --carry-branch] rc={rc} {line}")
    assert rc == 0, (out, err)
    assert tree_c1.is_dir() and f"{tree_c1}(被 PROJ-C1B 承继引用)" in line

    rc, pre, err = _cli("project", "cleanup-worktrees", "--workspace-root", str(gov))
    _show(f"---- 件④ 承继卡认领前 cleanup-worktrees 预演 exit {rc} ----\n{pre}")
    assert f"承继引用(留) {tree_c1}(PROJ-C1; 被 PROJ-C1B 的 carried_from 引用, 待其认领)" in pre
    _q_claim(gov, "PROJ-C1B")  # 承继卡认领(卡分支从原卡 tip 建)= 原卡树不再受保护
    rc, pre, err = _cli("project", "cleanup-worktrees", "--workspace-root", str(gov))
    _show(f"---- 件④ 承继卡认领后 cleanup-worktrees 预演 exit {rc} ----\n{pre}")
    assert f"  删 {tree_c1}(PROJ-C1 withdrawn, 干净; 检出 card/PROJ-C1, 分支保留)" in pre
    assert f"在途(留) {tree_c1.parent / 'PROJ-C1B'}(PROJ-C1B claimed)" in pre
    assert _git(product, "rev-parse", "card/PROJ-C1") == tip


def test_item4_cleanup_worktrees_across_repos_preview_confirm(qrig, tmp_path):
    from tools.aipos_cli.task_loader import find_task_card

    repo, gov = qrig["repo"], qrig["gov"]
    product = _q_product(gov)
    second = f78._init_product_repo(tmp_path / "second")
    _update_project(gov, repos={"default": "main-repo", "items": {"main-repo": str(product), "second": str(second)}})
    for task_id, lane in (("PROJ-A", "main-repo"), ("PROJ-B", "second"), ("PROJ-D", "second"), ("PROJ-F", "main-repo")):
        _q_card(gov, task_id, "pending", extra={"lane": {"repo": lane, "paths": ["app/"]}})
    _commit_all(repo, "publish")
    trees = {t: _q_claim(gov, t) for t in ("PROJ-A", "PROJ-B", "PROJ-D", "PROJ-F")}
    for t in ("PROJ-A", "PROJ-B", "PROJ-D"):
        _commit_in(trees[t], "app/x.py", t)
    _write(trees["PROJ-D"] / "Plan.md", "untracked draft\n")  # 脏
    for t in ("PROJ-A", "PROJ-B", "PROJ-D"):  # 终态(结案)——直接搬队列目录模拟, 判据只看队列位置
        path, _s = find_task_card(gov, t)
        path.rename(gov / "5_tasks" / "queue" / "completed" / path.name)
    _git(second, "worktree", "add", "-q", "-b", "scratch-x", str(second / ".worktrees" / "NOT-A-CARD"))
    rc, pre, err = _cli("project", "cleanup-worktrees", "--workspace-root", str(gov))
    _show(f"---- 件④ lybra project cleanup-worktrees(预演, 两仓)exit {rc} ----\n{pre}")
    assert rc == declared_exit_code(CLEANUP, "ok") == 0, err
    assert f"仓 main-repo: 工作树根 {product / '.worktrees'}, 卡工作树 2 个" in pre and f"仓 second: 工作树根 {second / '.worktrees'}, 卡工作树 3 个" in pre
    assert f"  删 {trees['PROJ-A']}(PROJ-A completed, 干净; 检出 card/PROJ-A, 分支保留)" in pre
    assert f"  删 {trees['PROJ-B']}(PROJ-B completed, 干净; 检出 card/PROJ-B, 分支保留)" in pre
    assert f"  脏(不碰) {trees['PROJ-D']}(PROJ-D completed): 未提交改动 1 个" in pre
    assert f"  在途(留) {trees['PROJ-F']}(PROJ-F claimed)" in pre
    assert f"  不认识(留) {second / '.worktrees' / 'NOT-A-CARD'}: 目录名查不到卡" in pre
    assert "合计: 删 2 / 脏 1 / 在途 1 / 承继 0 / 不认识 1 / 读不出 0 / 失败 0" in pre and "--confirm" in pre
    assert all(t.is_dir() for t in trees.values())  # 预演零写入
    rc, done, err = _cli("project", "cleanup-worktrees", "--workspace-root", str(gov), "--confirm")
    _show(f"---- 件④ lybra project cleanup-worktrees --confirm exit {rc} ----\n{done}")
    assert rc == 0, err
    assert not trees["PROJ-A"].exists() and not trees["PROJ-B"].exists()
    assert trees["PROJ-D"].is_dir() and trees["PROJ-F"].is_dir() and (second / ".worktrees" / "NOT-A-CARD").is_dir()
    assert _git(product, "rev-parse", "--verify", "card/PROJ-A") and _git(second, "rev-parse", "--verify", "card/PROJ-B")
    assert f"  已删 {trees['PROJ-A']}(PROJ-A completed; 检出 card/PROJ-A, 分支保留)" in done
    rc, _o, err = _cli("project", "cleanup-worktrees", "--workspace-root", str(gov), "--dry-run", "--confirm")
    assert rc == 2  # 互斥(argparse)


# ===========================================================================
# 防碎片化 / 声明单源 / 文档
# ===========================================================================

def test_declarations_single_source_and_no_second_rule_text():
    from tools.aipos_cli.subagent_kickoff import kickoff_contract
    from tools.aipos_cli.workspace_config import PROJECT_PATH_KEYS, TEST_CONTRACT_KEYS, governance_root_declaration
    from tools.aipos_cli.worktree_cleanup import cleanup_declaration, cleanup_texts
    from tools.schema_loader import load_schema

    contract = kickoff_contract()
    decl = cleanup_declaration()
    assert set(decl["terminal_states"]) == {"completed", "withdrawn"} and decl["carry_protect_states"] == ("pending",)
    assert cleanup_texts()["auto_line"]
    assert "scratch_root" in PROJECT_PATH_KEYS and {"ratchet", "residual_processes"} <= set(TEST_CONTRACT_KEYS)
    cmds = governance_root_declaration()["commands"]
    assert "kickoff" in cmds and "project cleanup-worktrees" in cmds
    verbs = load_schema("verbs")["verbs"]
    assert "lybra kickoff --task-id {card}" in verbs["lybra_loop"]["launch"]["manual_hint"]["subagent"]
    assert "lybra kickoff" in verbs["lybra_loop_status"]["next_action"]["by_execution_mode"]["modes"]["subagent"]["kickoff_detail"]
    # 开工稿句子只在声明: 代码里不出现声明文案的特征句(第二份规则文本)
    src = (REPO_ROOT / "tools" / "aipos_cli" / "subagent_kickoff.py").read_text(encoding="utf-8")
    for phrase in ("两边基线", "写完即止", "禁写治理根", "pgrep -af", "最终 tip", "禁启动 pi"):
        assert phrase not in src, phrase
        assert any(phrase in t for t in contract["texts"].values()), phrase
    clean_src = (REPO_ROOT / "tools" / "aipos_cli" / "worktree_cleanup.py").read_text(encoding="utf-8")
    for phrase in ("脏(不碰)", "承继引用(留)", "不认识(留)", "尚未清理"):
        assert phrase not in clean_src, phrase
    # 删除工作树只在唯一实现(既有 finalize 回归 / 治理提交只删各自临时树); 未提交改动计数唯一实现(F186 承继判定同读)
    hits = {p.name for p in (REPO_ROOT / "tools" / "aipos_cli").glob("*.py")
            if '"worktree", "remove"' in p.read_text(encoding="utf-8")}
    _show(f"[单源] git worktree remove 调用文件: {sorted(hits)}")
    assert hits <= {"worktree_cleanup.py", "post_merge_regression.py", "governance_commit.py"} and "worktree_cleanup.py" in hits
    porcelain = {p.name for p in (REPO_ROOT / "tools" / "aipos_cli").glob("*.py")
                 if '"git", "-C", str(worktree), "status", "--porcelain"' in p.read_text(encoding="utf-8")}  # 卡工作树未提交计数
    assert porcelain == {"worktree_cleanup.py"}, porcelain
    qm = (REPO_ROOT / "tools" / "aipos_cli" / "queue_mutation.py").read_text(encoding="utf-8")
    assert "uncommitted_count(worktree)" in qm


def test_cleanup_after_never_raises_on_declaration_failure(tmp_path, monkeypatch):
    from tools.aipos_cli import worktree_cleanup as wc
    from tools.schema_loader import SchemaLoadError

    def boom():
        raise SchemaLoadError("simulated missing declaration")

    monkeypatch.setattr(wc, "cleanup_declaration", boom)
    out = wc.cleanup_after(tmp_path, "X-1", trigger="close", actor="a")
    _show(f"[件③ 声明坏不抛] {out['line']}")
    assert out["ok"] is False and "simulated missing declaration" in out["line"] and "不影响close本身" in out["line"]


def test_docs_use_kickoff_and_cleanup():
    advisor = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "tools" / "aipos_cli" / "README.md").read_text(encoding="utf-8")
    for doc in (advisor, skill, readme):
        assert "lybra kickoff --task-id" in doc and "cleanup-worktrees" in doc
    assert "顾问按开工材料派本卡子 agent" not in skill and "按开工材料补派" not in skill
    assert "开工稿 = `lybra kickoff --task-id <卡ID> --workspace-root {{governance_root}}`" in advisor


def test_f187_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f187_kickoff_worktree_cleanup.py", default_test_contract())
