"""AIPOS-F135 — Lybra 接管外部项目合入: 同仓 finalize 串行、无 remote 推送如实标注、内联 git push 收归唯一实现、按仓声明不部署。

依据(10-08 只读调研): ① 同仓合入无互斥(内部只有 on-main / 脏树 / 冲突守卫; 外部路线 N4→N5 不查同仓——两 lane 共用一仓时只能靠人工
开「窗口」); ② finalize 脏树支内联 `git push` 不走 _git_push(第二路径); ③ 无 remote 仓按「已同步」静默收尾, 结果不标推送不适用;
④ 无显式「本仓不部署」声明。

各件并入点(唯一实现, 禁第二路径):
  件① 内部: finalize._repo_merge_lock(锁 = workspace_config.exclusive_flock, 与 project.json 写路径同一锁实现), 合并前取锁;
      外部: next_resolver._external_repo_turn_blocker(仓键 machine_zone.card_lane_key), N4→N5 推导返回 await_repo_turn。
  件② finalize._git_push(唯一推送) + finalize._push_not_applicable_reason(唯一适用性判据, 主路径与续做同口径)。
  件③ deploy_gate.deploy_mechanism_present(唯一读 project.json repos.no_deploy 声明)。

靶场 = 临时产品仓(无 remote / 临时 bare 远端)+ 临时治理根, 部署桩只在临时产品仓; 绝不动真实 .deploy / 真实产品仓 / 真实治理根。
卡号用靶场自造前缀 RNG- / HBJ-F135-(证明不写死项目卡号前缀)。
"""
from __future__ import annotations

import ast
import io
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f120_finalize_resumable import DEPLOY_STUB  # noqa: E402  — 靶场部署桩唯一来源
from test_aipos_f130_finalize_unlock import EXEC, _card_branch, _git, _gov, _product, _verdict, _write  # noqa: E402  — 靶场唯一来源
from tools.aipos_cli import finalize as fz  # noqa: E402
from tools.aipos_cli.finalization_record import existing_finalization_records  # noqa: E402
from tools.aipos_cli.runall_discovery import repo_files  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402


def _show(msg: str) -> None:
    print(msg)


def _record_text(gov: Path, task: str) -> str:
    records = existing_finalization_records(gov, task)
    assert len(records) == 1, records
    return records[0].read_text(encoding="utf-8")


# 部署桩: RANGE_HOLD_STARTED / RANGE_HOLD_GO 两个 FIFO 给出时, 先报「已到部署」再等放行(持锁窗口可控, 无 sleep 轮询); 否则同 F120 桩
HOLD_DEPLOY_STUB = ("#!/usr/bin/env bash\n"
                    "if [ -n \"${RANGE_HOLD_STARTED:-}\" ]; then echo started > \"$RANGE_HOLD_STARTED\"; read -r _go < \"$RANGE_HOLD_GO\"; fi\n"
                    + DEPLOY_STUB.split("\n", 1)[1])

_SUBPROC = """
import json, sys
from pathlib import Path
sys.path.insert(0, {repo!r})
from tools.aipos_cli import finalize as fz
r = fz.finalize_task({task!r}, {actor!r}, Path({product!r}), governance_root=Path({gov!r}), push=True, deploy=True)
print(json.dumps({{k: r.get(k) for k in ("verdict", "category", "message", "push_status", "pushed", "deployed")}}, ensure_ascii=False))
"""


# ---------------------------------------------------------------------------
# 件① 内部路线: 仓级合入锁
# ---------------------------------------------------------------------------

def test_item1_internal_two_cards_same_repo_concurrent_one_wins_other_waits_then_succeeds(tmp_path: Path, monkeypatch):
    """验收①: 临时产品仓(无 remote)+ 临时治理根, 两卡同仓并发 finalize: 先者持锁合入中(停在部署桩), 后者 BLOCK「同仓合入进行中」;
    先者完成后后者重跑成功。"""
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo, deploy_stub=HOLD_DEPLOY_STUB)
    a, b = "RNG-351", "RNG-352"
    _verdict(gov, a, _card_branch(repo, a))
    _verdict(gov, b, _card_branch(repo, b), stamp="20261008_020000")
    started, go = tmp_path / "hold-started.fifo", tmp_path / "hold-go.fifo"
    os.mkfifo(started)
    os.mkfifo(go)
    started_fd = os.open(started, os.O_RDWR)  # 读写打开: 永不因无对端阻塞; select 只在桩写入后就绪
    go_fd = os.open(go, os.O_RDWR)  # 放行数据在本 fd 关闭前留在管道里
    env = {**os.environ, "LYBRA_WORKSPACE_ROOT": str(gov), "RANGE_HOLD_STARTED": str(started), "RANGE_HOLD_GO": str(go)}
    proc = subprocess.Popen([sys.executable, "-c", _SUBPROC.format(repo=str(REPO_ROOT), task=a, actor=EXEC, product=str(repo), gov=str(gov))],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _w, _x = select.select([started_fd], [], [], 120)
        assert ready, f"先者 finalize 未到部署桩(持锁窗口): {proc.poll()}"
        assert os.read(started_fd, 64).startswith(b"started")
        lock_file = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "lybra-finalize.lock"
        holder = lock_file.read_text(encoding="utf-8")
        _show(f"[件①·持锁方自述] {lock_file}: {holder}")
        assert json.loads(holder)["task_id"] == a and json.loads(holder)["pid"] == proc.pid

        head_during = _git(repo, "rev-parse", "HEAD")
        blocked = fz.finalize_task(b, EXEC, repo, governance_root=gov, push=True, deploy=True)
        _show(f"[件①·后者在先者合入中] verdict={blocked['verdict']} category={blocked.get('category')}\n  message: {blocked['message']}")
        assert blocked["verdict"] == "BLOCK" and blocked["category"] == "REPO_MERGE_IN_PROGRESS" and blocked.get("retryable") is True
        assert "同仓合入进行中" in blocked["message"] and a in blocked["message"] and "未合并、未改仓" in blocked["message"]
        assert _git(repo, "rev-parse", "HEAD") == head_during and existing_finalization_records(gov, b) == []

        os.write(go_fd, b"go\n")
        out, err = proc.communicate(timeout=180)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(started_fd)
        os.close(go_fd)
    first = json.loads(out.strip().splitlines()[-1])
    _show(f"[件①·先者完成] {first}")
    assert first["verdict"] == "PASS" and first["deployed"] is True, err[-2000:]
    assert lock_file.read_text(encoding="utf-8") == "", "释放前清空持锁自述"

    after = fz.finalize_task(b, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show(f"[件①·先者完成后后者重跑] verdict={after['verdict']} push_status={after.get('push_status')}\n  message: {after['message']}")
    assert after["verdict"] == "PASS" and after["deployed"] is True, after["message"]
    log = _git(repo, "log", "--first-parent", "--format=%s", "main")
    _show(f"[件①·main 合并序]\n{log}")
    assert log.index(f"card/{b}") < log.index(f"card/{a}")  # 新在前: A 先合、B 后合
    assert existing_finalization_records(gov, a) and existing_finalization_records(gov, b)


def test_item1_lock_shared_across_worktrees_busy_names_holder_and_dry_run_not_locked(tmp_path: Path):
    """锁在 git common-dir: 同仓另一工作树上的 finalize 同样拿不到锁; 拿不到 = RepoMergeBusy 点名持锁方; dry-run 不取锁。"""
    repo = tmp_path / "product"
    _product(repo, deploy_stub=None)
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    bi = fz._load_branch_integration()
    with fz._repo_merge_lock(repo, "RNG-360", bi) as lock_path:
        assert lock_path.parent == Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        with pytest.raises(fz.RepoMergeBusy) as busy:
            with fz._repo_merge_lock(wt, "RNG-361", bi):
                pass
        _show(f"[件①·另一工作树取锁] {busy.value}")
        assert "RNG-360" in busy.value.holder and str(os.getpid()) in busy.value.holder
    with fz._repo_merge_lock(wt, "RNG-361", bi):  # 释放后可取
        pass
    (tmp_path / "not-a-repo").mkdir()
    with pytest.raises(fz.RepoMergeLockError, match="git common-dir"):  # 无法建锁 = 拒(不无锁合并)
        with fz._repo_merge_lock(tmp_path / "not-a-repo", "RNG-362", bi):
            pass
    with pytest.raises(fz.RepoMergeLockError, match="repo_merge_lock"):
        with fz._repo_merge_lock(repo, "RNG-363", {"branch_pattern": "card/{task_id}", "base_branch": "main"}):
            pass


# ---------------------------------------------------------------------------
# 件① 外部路线: N4→N5 同仓互斥
# ---------------------------------------------------------------------------

def _external_rig(tmp_path: Path, monkeypatch):
    from test_aipos_f73d_loop_driver import _claim_record
    from test_aipos_f78_engine_agnostic import CHRIS_EXEC
    from test_aipos_f78b_chris_zero_migration import _chris_gov, _n4_pass_records, _slug_card

    gov = _chris_gov(tmp_path, monkeypatch, finalize_mode="external", git=False)
    web, lib = tmp_path / "product-chris", tmp_path / "product-lib"
    lib.mkdir()
    project = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    project["repos"] = {"default": "web", "items": {"web": str(web), "lib": str(lib)}}
    (gov / "project.json").write_text(json.dumps(project), encoding="utf-8")
    cards = {"HBJ-F135-A": "web", "HBJ-F135-B": "web", "HBJ-F135-C": "lib"}
    for task, lane in cards.items():
        _slug_card(gov, task, "claimed", f"{task.lower()}-slug.md", extra={"lane": {"repo": lane}})
        _claim_record(gov, task, CHRIS_EXEC)
        _n4_pass_records(gov, task, CHRIS_EXEC)
    return gov


def test_item1_external_same_repo_pass_pending_other_card_awaits_turn(tmp_path: Path, monkeypatch):
    """验收①(外部): 同 lane 仓 A、B 都 PASS 未落 finalization——A 先(裁决同时按卡号), B 推导 await_repo_turn 点名 A、不派 FINALIZE;
    不同仓 C 不受影响; A 的 FINALIZE Return 落 → B 仍等; A ingest 铸 finalization 记录 → B 轮到(await FINALIZE Return)。"""
    from test_aipos_f78b_chris_zero_migration import _finalize_return
    from tools.aipos_cli.artifact_ingest import ingest_task_artifact
    from tools.aipos_cli.next_resolver import derive_next_step

    gov = _external_rig(tmp_path, monkeypatch)
    da, db, dc = (derive_next_step(t, gov) for t in ("HBJ-F135-A", "HBJ-F135-B", "HBJ-F135-C"))
    _show(f"[件①·外部 A] action={da.get('action')}\n[件①·外部 B] action={db.get('action')}\n  notes: {db['notes']}\n"
          f"  suggested: {db['suggested_action']}\n[件①·外部 C(他仓)] action={dc.get('action')}")
    assert da["action"] == {"type": "await_artifact", "card": "HBJ-F135-A-FINALIZE", "kind": "finalize_return"}
    assert db["derivable"] is False and db["action"] == {"type": "await_repo_turn", "card": "HBJ-F135-A", "lane": "web"}
    assert "同仓 lane web 的卡 HBJ-F135-A" in db["notes"] and "FINALIZE" in db["suggested_action"] and db["command"] == ""
    assert dc["action"] == {"type": "await_artifact", "card": "HBJ-F135-C-FINALIZE", "kind": "finalize_return"}

    sha = "e" * 40
    _finalize_return(gov, "HBJ-F135-A-FINALIZE", {"merge_commit": sha, "remote_ref": f"origin/main@{sha}", "deploy_status": "not_applicable"})
    db2 = derive_next_step("HBJ-F135-B", gov)
    assert db2["action"]["type"] == "await_repo_turn" and "FINALIZE Return 已落" in db2["notes"], db2
    res = ingest_task_artifact("HBJ-F135-A", gov)
    assert res["ok"], res
    db3 = derive_next_step("HBJ-F135-B", gov)
    _show(f"[件①·A 落 finalization 后 B] action={db3.get('action')}")
    assert db3["action"] == {"type": "await_artifact", "card": "HBJ-F135-B-FINALIZE", "kind": "finalize_return"}


def test_item1_external_loop_waits_blocker_finalization_then_moves_on(tmp_path: Path, monkeypatch):
    """loop: B 等 A 的 finalization 记录(不拉起、不派 FINALIZE); A 记录落盘即醒, 转等 B 自己的 FINALIZE Return(超时 exit 3 点名)。"""
    from tools.aipos_cli.artifact_ingest import ingest_task_artifact
    from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop
    from test_aipos_f78b_chris_zero_migration import _ExternalGate, _chris_policy, _finalize_return
    from test_aipos_f73d_loop_driver import DRIVER

    gov = _external_rig(tmp_path, monkeypatch)
    _chris_policy(gov, agent_or_role=DRIVER)
    sha = "f" * 40

    def finish_a() -> None:  # 外部 A 合入完成: Return 落 + ingest 铸 finalization 记录
        time.sleep(0.2)
        _finalize_return(gov, "HBJ-F135-A-FINALIZE", {"merge_commit": sha, "remote_ref": f"origin/main@{sha}", "deploy_status": "not_applicable"})
        ingest_task_artifact("HBJ-F135-A", gov)

    t = threading.Thread(target=finish_a, daemon=True)
    t.start()
    out = io.StringIO()
    res = run_loop("HBJ-F135-B", gov, actor=DRIVER, out=out, execute=_ExternalGate(gov), interval=0.03, max_wait=1.5, max_steps=3)
    t.join(timeout=10)
    text = out.getvalue()
    _show(f"[件①·外部 loop] exit={res.exit_code}\n{text}")
    waits = [s for s in res.steps if s.kind == "wait"]
    assert waits[0].card == "HBJ-F135-A" and waits[0].artifacts == ["5_tasks/records/finalizations/HBJ-F135-A/finalization_*.md"]
    assert waits[0].message == "产物就绪" and waits[1].card == "HBJ-F135-B-FINALIZE"
    assert res.exit_code == exit_code_for(load_loop_contract(), "wait_timeout") and "HBJ-F135-B-FINALIZE" in res.message


def test_item1_external_unresolvable_own_lane_is_not_derivable(tmp_path: Path, monkeypatch):
    from tools.aipos_cli.next_resolver import derive_next_step

    gov = _external_rig(tmp_path, monkeypatch)
    card = next((gov / "5_tasks" / "queue" / "claimed").glob("hbj-f135-b-*.md"))
    card.write_text(card.read_text(encoding="utf-8").replace("'repo': 'web'", "'repo': 'nowhere'"), encoding="utf-8")
    d = derive_next_step("HBJ-F135-B", gov)
    _show(f"[件①·外部 lane 不可解析] {d['notes']} | {d['missing_records']}")
    assert d["derivable"] is False and "LANE_REPO_UNDECLARED" in d["notes"] and "action" not in d


# ---------------------------------------------------------------------------
# 件② 推送单源与如实标注
# ---------------------------------------------------------------------------

def test_item2_no_remote_internal_finalize_push_not_applicable_in_result_and_record(tmp_path: Path, monkeypatch):
    """验收②: 无 remote 内部 finalize(带 --push)= push_status=not_applicable(结果、operations、finalization 记录同值, 依据写明)。"""
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo, deploy_stub=None)
    task = "RNG-371"
    _verdict(gov, task, _card_branch(repo, task))
    r = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    text = _record_text(gov, task)
    _show(f"[件②·无 remote] verdict={r['verdict']} push_status={r.get('push_status')}\n  message: {r['message']}\n"
          + "\n".join(f"  op: {op}" for op in r["operations"] if "推送" in op or "push_status" in op) + f"\n[件②·记录]\n{text}")
    assert r["verdict"] == "PASS" and r["push_status"] == "not_applicable" and r["pushed"] is False
    assert "push_status: not_applicable" in text and "无 origin 远端, 推送不适用" in text
    assert any("push_status=not_applicable" in op for op in r["operations"])


def test_item2_remote_present_pushed_and_resume_same_semantics(tmp_path: Path, monkeypatch):
    """有远端: push_status=pushed; 续做路径(F130)同口径: 记录已落后接上远端且本地领先 → 续做推送, 追加记录 push_status=pushed;
    远端跟踪分支缺(有 origin 无 origin/main)= not_applicable(同一判据 _push_not_applicable_reason)。"""
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo, origin = tmp_path / "product", tmp_path / "origin.git"
    _product(repo, origin=origin, deploy_stub=None)
    task = "RNG-372"
    _verdict(gov, task, _card_branch(repo, task))
    r = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True)
    _show(f"[件②·有远端] verdict={r['verdict']} push_status={r.get('push_status')}")
    assert r["verdict"] == "PASS" and r["push_status"] == "pushed" and "push_status: pushed" in _record_text(gov, task)
    assert _git(repo, "rev-parse", "origin/main") == _git(repo, "rev-parse", "HEAD")

    lone = tmp_path / "lone"
    _product(lone, deploy_stub=None)
    assert "无 origin 远端" in fz._push_not_applicable_reason(lone, "main")
    _git(lone, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    assert fz._push_not_applicable_reason(lone, "main") == "无远端跟踪分支 origin/main, 推送不适用"
    assert fz._push_pending(lone, "main", _git(lone, "rev-parse", "HEAD"))["not_applicable"] is True


def test_item2_inline_push_removed_single_push_implementation():
    """验收②: finalize 内联 git push 已删——产品源码中 ["git", "push", …] 只在 finalize._git_push 一处; 推送前适用性判据唯一。"""
    hits = scan_git_push(REPO_ROOT)
    _show(f"[件②·git push 字面] {hits}")
    assert hits == [("tools/aipos_cli/finalize.py", "_git_push")], hits
    src = (REPO_ROOT / "tools/aipos_cli/finalize.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert src.count("def _push_not_applicable_reason(") == 1
    for name in ("_push_pending", "_finalize_task_impl"):
        assert "_push_not_applicable_reason(" in ast.unparse(fn[name]), name
    assert "_git_push(workspace_root)" in ast.unparse(fn["_finalize_task_impl"]) and "_git_push(workspace_root)" in ast.unparse(fn["_resume_finalization"])
    assert "timeout=30" not in ast.unparse(fn["_finalize_task_impl"])


# ---------------------------------------------------------------------------
# 件③ 按仓不部署声明
# ---------------------------------------------------------------------------

def test_item3_no_deploy_declaration_before_after(tmp_path: Path, monkeypatch):
    """验收③: 同一有部署机制的产品仓——未声明 = 部署(deploy_status=deployed, 行为不变); 声明 repos.no_deploy 后 = 不调部署脚本,
    deploy_status=not_applicable 并写明依据。声明只经 set-repos 写入口(唯一写路径), 仓名不在 items = REPOS_CONFLICT 零写入。"""
    from tools.aipos_cli.deploy_gate import deploy_mechanism_present
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, set_project_repos

    gov = _gov(tmp_path / "home" / "rng")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    (gov / "project.json").write_text(json.dumps({"project": "rng", "config_version": 1}), encoding="utf-8")
    repo = tmp_path / "product"
    _product(repo)  # 带部署桩 = 有部署机制
    before = "RNG-381"
    _verdict(gov, before, _card_branch(repo, before))
    r1 = fz.finalize_task(before, EXEC, repo, governance_root=gov, push=True, deploy=True)
    t1 = _record_text(gov, before)
    _show(f"[件③·声明前] verdict={r1['verdict']} deployed={r1['deployed']} judgement={deploy_mechanism_present(repo, gov)}\n{t1}")
    assert r1["verdict"] == "PASS" and r1["deployed"] is True and "deploy_status: deployed" in t1

    with pytest.raises(CardRepoUnresolved, match="no_deploy"):
        set_project_repos(gov.parent, "rng", {"web": str(repo)}, default="web", no_deploy=["ghost"])
    out = set_project_repos(gov.parent, "rng", {"web": str(repo)}, default="web", no_deploy=["web"])
    assert out["written"] and out["no_deploy"] == ["web"]
    _show(f"[件③·set-repos --no-deploy web diff]\n{out['diff']}")
    deployed_before = (repo / ".deploy" / "current").resolve()
    after = "RNG-382"
    _verdict(gov, after, _card_branch(repo, after))
    r2 = fz.finalize_task(after, EXEC, repo, governance_root=gov, push=True, deploy=True)
    t2 = _record_text(gov, after)
    _show(f"[件③·声明后] verdict={r2['verdict']} deployed={r2['deployed']}\n  message: {r2['message']}\n{t2}")
    assert r2["verdict"] == "PASS" and r2["deployed"] is False
    assert "deploy_status: not_applicable" in t2 and "repos.no_deploy 声明仓 web" in t2 and "deployed: false" in t2
    assert (repo / ".deploy" / "current").resolve() == deployed_before, "声明不部署后不调部署脚本"
    assert any("部署不适用" in op and "not_applicable" in op for op in r2["operations"])

    # 声明不合(手改成不在 items 的仓名)= 合并前即拒, 不留半成品合并
    project = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    project["repos"]["no_deploy"] = ["ghost"]
    (gov / "project.json").write_text(json.dumps(project), encoding="utf-8")
    bad = "RNG-383"
    _verdict(gov, bad, _card_branch(repo, bad))
    head = _git(repo, "rev-parse", "HEAD")
    r3 = fz.finalize_task(bad, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show(f"[件③·声明不合] {r3['verdict']}: {r3['message']}")
    assert r3["verdict"] == "BLOCK" and "REPOS_CONFLICT" in r3["message"] and _git(repo, "rev-parse", "HEAD") == head


def test_item3_declarations_append_only_and_cli_flag(tmp_path: Path):
    t = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    n5 = t["nodes"]["N5"]
    assert n5["record"]["deploy_status"]["values"][-1] == "not_applicable"
    assert n5["record"]["push_status"]["values"] == ["pushed", "already_synced", "not_applicable", "not_requested"]
    assert n5["branch_integration"]["repo_merge_lock"]["busy_category"] == "REPO_MERGE_IN_PROGRESS"
    assert n5["finalize_mode"]["repo_serial"]["action_type"] == "await_repo_turn"
    c = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    nd = c["configuration_sources"]["project_json"]["schema"]["repos"]["schema"]["no_deploy"]
    assert nd["default"] == [] and nd["required"] is False
    from tools.aipos_cli.finalization_record import build_finalization_record

    with pytest.raises(ValueError, match="push_status"):
        build_finalization_record(task_id="RNG-1", actor="a", commit="c" * 40, authorization_type="verdict_ref",
                                  authorization_ref="v", push_status="sometimes")
    help_text = subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", "project", "set-repos", "--help"], cwd=REPO_ROOT,
                               capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}).stdout
    assert "--no-deploy" in help_text


# ---------------------------------------------------------------------------
# 不变量夹具(防回潮, 零容忍): 产品源码 git push 只在 finalize._git_push; fcntl.flock 只在 workspace_config.exclusive_flock;
# no_deploy 声明只在 project_repos(解析)与 deploy_mechanism_present(判定)读取
# 扫描面 = 产品仓文件清单中的 .py 产品源码, 排除 run-all 发现的测试文件、conftest.py、路径含 tests/ 目录段的文件(与 F132/F133 棘轮同口径)
# ---------------------------------------------------------------------------

def _scoped_product_files(repo_root: Path) -> list[str]:
    files = repo_files(repo_root)
    tests = set(discover_test_files(files, default_test_contract()))
    return sorted(rel for rel in files if rel.endswith(".py") and rel not in tests and rel.split("/")[-1] != "conftest.py"
                  and "tests" not in rel.split("/")[:-1])


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    out: dict[int, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
            if hasattr(child, "lineno"):
                out.setdefault(child.lineno, child_name)
            visit(child, child_name)

    visit(tree, "<module>")
    return out


def _scan(repo_root: Path, match) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for rel in _scoped_product_files(repo_root):
        tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"))  # 读不了/语法坏 = 抛(fail-closed)
        funcs = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if match(node):
                hits.append((rel, funcs.get(node.lineno, "<module>")))
    return sorted(set(hits))


def _is_git_push_list(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    vals = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
    return bool(vals) and vals[0] == "git" and "push" in vals[1:]


def scan_git_push(repo_root: Path) -> list[tuple[str, str]]:
    return _scan(repo_root, _is_git_push_list)


def test_invariant_flock_and_no_deploy_single_reader():
    flock = _scan(REPO_ROOT, lambda n: isinstance(n, ast.Attribute) and n.attr == "flock"
                  and isinstance(n.value, ast.Name) and n.value.id == "fcntl")
    _show(f"[不变量·fcntl.flock] {flock}")
    assert flock == [("tools/aipos_cli/workspace_config.py", "exclusive_flock")], flock
    readers = _scan(REPO_ROOT, lambda n: isinstance(n, ast.Constant) and n.value == "no_deploy")
    _show(f"[不变量·no_deploy 字面] {readers}")
    # 解析 = project_repos(唯一读取口); 写 = set_project_repos(唯一写入口) 与其 CLI 薄壳; 判定 = deploy_mechanism_present(唯一消费方)
    # (aipos_cli: build_parser 的 --no-deploy dest 与 main 的 set-repos 薄壳输出; workspace_config._mutate = set_project_repos 内写闭包)
    assert readers == [("tools/aipos_cli/aipos_cli.py", "build_parser"), ("tools/aipos_cli/aipos_cli.py", "main"),
                       ("tools/aipos_cli/deploy_gate.py", "deploy_mechanism_present"),
                       ("tools/aipos_cli/workspace_config.py", "_mutate"), ("tools/aipos_cli/workspace_config.py", "project_repos")], readers
    src = (REPO_ROOT / "tools/aipos_cli/deploy_gate.py").read_text(encoding="utf-8")
    assert 'repos.get("no_deploy")' in src and src.count("project_repos(") == 1


def test_invariant_pattern_catches_shapes():
    tree = ast.parse('subprocess.run(["git", "push"], cwd=x)\nsubprocess.run(["git", "-C", r, "push"])\nrun(["git", "status"])\n')
    found = [n for n in ast.walk(tree) if _is_git_push_list(n)]
    assert len(found) == 2  # ["git", "push"] 与 ["git", "-C", <仓>, "push"] 均命中; git status 不命中
