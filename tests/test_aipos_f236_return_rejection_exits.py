"""AIPOS-F236 — 交回被门拒后有出口(10-26 chris 岚图 LANTU-CLDEP1 实撞)。

实撞: task_mode=config 的 pi 卡按卡面把 lantu-contracts 固定提交 bb2f5fb 以 git archive 导出到本 run 内只读目录 contracts/, 交回被
F230 程序守卫拒 INGEST_PROGRAM_IN_NON_CODE_CARD(contracts/tools/p1/validate.py 309 行等——他仓已入库代码的只读副本); 拒收只写在
loop 运行记录, `lybra next` 仍给同一条必拒的交回命令, 拒因出口只写原则不给命令, 顾问看不到撤回 / 移走后重交。

件① 输入副本不算交付: 命中程序判据的文件内容 git blob 已在本项目声明的仓(lane 仓 + repos.items + code_repo)= 输入副本, 入门不拒并列
    注记(路径 + 来源仓 + blob); 仓中不存在的 blob(本卡新写 / 改过)照旧拒。
件② 拒收入真相: 真交回被拒落交回拒收记录(records/events/<卡>/return_rejected_*); next 读到「最近一次被拒且报告与产物自拒后未变」时
    不再给交回命令, 改给合法出口全集(修后重交 / 换新主体 / 撤卡 / 改范围; 撤卡附门侧真实判定); 产物改后恢复给交回命令。
件③ 拒因文案: 程序守卫拒因出口 = 合法出口命令原文(含撤回预演命令); pi 卡 loop 同会话续接执行体按拒因修一次(F204 同一机制)。
件④ 回放 LANTU-CLDEP1 形态: 他仓固定提交 git archive 导出的只读 contracts/ = 输入副本, 交回可入门(夹具自造, 不碰真 chris)。

靶场 = AIPOS-F90 / F196 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 假 pi / 假 harness)。禁真门 / 真治理根 / 真工位 /
真 chris / 真 pi。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402  — 卡面读取构件
import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402  — LANTU-DEMO1 形态 config 卡构件
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f169_pi_model_declaration import _real_pi_template, install_fake_pi  # noqa: E402
from test_aipos_f196_validation_to_pi import vrig  # noqa: E402,F401  — pi 靶场夹具
from test_aipos_f197_noncode_pi_completion import POL_DOCS, _claim, _docs_policy, _gov_card  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli import artifact_ingest as ai  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402

CARD = f221.CARD   # 只写治理根的 config 卡(LANTU-DEMO1 / LANTU-CLDEP1 形态)
PROG = "INGEST_PROGRAM_IN_NON_CODE_CARD"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *argv], check=True, capture_output=True, text=True).stdout.strip()


def _config_policy(gov: Path) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{f221.POLICY}.md", build_autonomy_policy_markdown(
        policy_id=f221.POLICY, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f236", task_selector_task_mode="config", launch_harnesses=["pi"]))


def _config_card(rig, queue: str = "claimed", **extra) -> Path:
    """config 卡(只写治理根, 子 agent 执行, 快车道); claimed = 经队列真认领(claim 记录 + 报告骨架), 与门同一写路径。"""
    fields = {"harness": "claude-code", "audit": "none", "output_target": f"task_cards/{CARD}/artifacts/",
              "lane": {"repo": "governance_root", "paths": [f"task_cards/{CARD}/artifacts/"], "roles": ["executor"]}}
    fields.update(extra)
    _config_policy(rig.gov)
    path = f78._card(rig.gov, CARD, "pending", task_mode="config", extra=fields)
    if queue == "claimed":
        _claim(rig.gov, CARD)
        path, _q = nr._find_task_in_queue(rig.gov, CARD)
    return Path(path)


def _program(path: Path, lines: int = 300, *, executable: bool = True) -> Path:
    _write(path, "#!/usr/bin/env python3\n" + "".join(f"print('step {i}')\n" for i in range(lines - 1)))
    if executable:
        path.chmod(0o755)
    return path


def _contracts_repo(base: Path) -> tuple[Path, str]:
    """他仓(lantu-contracts 形态): 固定提交含 tools/p1/validate.py(309 行)与 tools/p1/task-storage/verify.py(276 行)。"""
    repo = base / "lantu-contracts"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _write(repo / "README.md", "# lantu-contracts\n")
    _write(repo / "tools" / "p1" / "validate.py", "".join(f"CHECK_{i} = {i}\n" for i in range(309)))
    _write(repo / "tools" / "p1" / "task-storage" / "verify.py", "#!/usr/bin/env python3\n" + "".join(f"V{i} = {i}\n" for i in range(275)))
    _write(repo / "migrations" / "001.sql", "create table t(id int);\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "contracts")
    return repo, _git(repo, "rev-parse", "--short", "HEAD")


def _declare_repos(gov: Path, code_repo: Path, contracts: Path) -> None:
    """project.json repos 清单(多仓项目形态, 与 chris 同构): default = 产品仓, items 另含他仓 lantu-contracts。"""
    pj = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    pj["repos"] = {"default": "product", "items": {"product": str(code_repo), "lantu-contracts": str(contracts)}}
    _write(gov / "project.json", json.dumps(pj))


def _archive(repo: Path, commit: str, dest: Path) -> None:
    """卡面要求: 他仓固定提交以 git archive 导出到本 run 内只读目录(与 LANTU-CLDEP1 同一做法)。"""
    dest.mkdir(parents=True)
    tar = subprocess.run(["git", "-C", str(repo), "archive", commit], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
    for path in sorted(dest.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)


def _run_dir(monkeypatch, run_dir: Path) -> None:
    """本卡在本机的验证根 run 目录(F230 测试同一替身: project_validation_roots / run_directory)。"""
    from tools.aipos_cli import validation_root

    entry = {"task_id": CARD, "host": validation_root.local_host_label(), "base": str(run_dir.parent.parent), "runs": [{"run_id": "run-1"}]}
    real_dir = validation_root.run_directory
    monkeypatch.setattr(validation_root, "project_validation_roots", lambda root, **kw: [entry])
    monkeypatch.setattr(validation_root, "run_directory", lambda e, rid: str(run_dir) if e is entry else real_dir(e, rid))


def _unlock(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)


# ===========================================================================
# 件① 输入副本不算交付; 仓中不存在的 300 行新程序照旧拒
# ===========================================================================

def test_item1_input_copy_passes_new_program_refused(rig, tmp_path, monkeypatch):  # noqa: F811
    contracts, commit = _contracts_repo(tmp_path / "repos")
    _declare_repos(rig.gov, rig.code_repo, contracts)
    _config_card(rig)
    init_governance_repo(rig.gov)
    f221._report(rig, "v1")
    run_dir = tmp_path / "vr" / CARD / "run-1"
    _archive(contracts, commit, run_dir / "contracts")
    _run_dir(monkeypatch, run_dir)
    try:
        got = ai.validate_task_artifact(CARD, rig.gov)
        _show(f"---- 件① 输入副本(他仓 {commit} git archive 导出)交回入门 ok={got['ok']} category={got['category']} ----\n"
              + "\n".join(got.get("notes") or []))
        assert got["ok"], got
        copies = {Path(c["path"]).name: c for c in got["input_copies"]}
        assert set(copies) == {"validate.py", "verify.py"}
        assert copies["validate.py"]["repo"] == "lantu-contracts" and copies["validate.py"]["lines"] == 309
        assert copies["validate.py"]["blob"] == _git(contracts, "rev-parse", f"{commit}:tools/p1/validate.py")
        assert any("输入副本(他仓已入库代码的只读副本" in n and "来源仓 lantu-contracts blob " in n for n in got["notes"])
        rc, out, err = _cli(["artifact", "ingest", "--task-id", CARD, "--kind", "return", "--workspace-root", str(rig.gov), "--dry-run"])
        _show(f"---- 件① lybra artifact ingest --dry-run exit {rc} ----\n{out}{err}")
        assert rc == 0 and "note: 输入副本" in out
        # 仓中不存在的 300 行新程序(本卡写的)= 照旧拒; 拒因列新程序, 输入副本另列注记
        _program(run_dir / "deploy-helper.py", 300, executable=False)
        got = ai.validate_task_artifact(CARD, rig.gov)
        _show(f"---- 件① 另含仓中不存在的 300 行新程序: ok={got['ok']} category={got['category']} ----\n" + "\n".join(got["reasons"]))
        assert not got["ok"] and got["category"] == PROG
        assert [Path(h["path"]).name for h in got["program_files"]] == ["deploy-helper.py"]
        assert "deploy-helper.py(300 行)" in got["reasons"][0] and "validate.py" not in got["reasons"][0]
        assert any("输入副本" in r and "validate.py" in r for r in got["reasons"][1:])
        (run_dir / "deploy-helper.py").unlink()
        # 改过的副本(blob 不在任何声明的仓)= 不再是副本, 照旧算程序
        target = run_dir / "contracts" / "tools" / "p1" / "validate.py"
        target.chmod(0o644)
        target.write_text(target.read_text(encoding="utf-8") + "EXTRA = 1\n", encoding="utf-8")
        got = ai.validate_task_artifact(CARD, rig.gov)
        _show(f"[件① 改过的副本] ok={got['ok']} program_files={[Path(h['path']).name for h in got.get('program_files') or []]}")
        assert not got["ok"] and [Path(h["path"]).name for h in got["program_files"]] == ["validate.py"]
        # 声明缺 input_copy = 不豁免(行为同 F230)
        target.write_text("".join(f"CHECK_{i} = {i}\n" for i in range(309)), encoding="utf-8")
        real = ai.program_guard_declaration

        def no_copy():
            decl = dict(real() or {})
            decl.pop("input_copy", None)
            return decl

        monkeypatch.setattr(ai, "program_guard_declaration", no_copy)
        scan = ai.program_scan(rig.gov, CARD, f218._card_fm(rig.gov, CARD), {})
        _show(f"[件① 声明缺 input_copy = 行为同 F230] programs={[Path(h['path']).name for h in scan['programs']]}")
        assert sorted(Path(h["path"]).name for h in scan["programs"]) == ["validate.py", "verify.py"] and not scan["input_copies"]
    finally:
        _unlock(run_dir)


def test_item1_repo_check_fail_closed(rig, tmp_path):  # noqa: F811
    """来源仓不在盘上 / 非 git 仓 = 该仓不作来源(fail-closed), 原因进 repo_errors 与拒因。"""
    ghost = tmp_path / "ghost-repo"
    ghost.mkdir()
    _declare_repos(rig.gov, rig.code_repo, ghost)
    blob = ai.git_blob_id(b"x\n")
    got = ai.repo_blob_sources(rig.gov, {"task_id": CARD, "lane": {"repo": "governance_root"}}, [blob])
    _show(f"[件① 仓核对 fail-closed] {got}")
    assert got["found"] == {} and any(e["repo"] == "lantu-contracts" for e in got["errors"])
    hashed = subprocess.run(["git", "hash-object", "--stdin"], input=b"x\n", check=True, capture_output=True).stdout.decode().strip()
    assert blob == hashed, "blob id 与 git hash-object 同算"


# ===========================================================================
# 件② 拒收入真相 + next 出口全集; 件③ 拒因出口命令原文
# ===========================================================================

def test_item2_item3_rejection_record_next_gives_exits_not_same_command(rig, monkeypatch):  # noqa: F811
    _config_card(rig)
    init_governance_repo(rig.gov)
    f221._report(rig, "v1")
    before = nr.derive_next_step(CARD, rig.gov)
    assert before["derivable"] and before["command"].startswith(f"lybra artifact ingest --task-id {CARD} --kind return"), before
    launcher = _program(rig.gov / "task_cards" / CARD / "artifacts" / "demo-launcher", 1400)
    rc, out, err = _cli(["artifact", "ingest", "--task-id", CARD, "--kind", "return", "--workspace-root", str(rig.gov)])
    _show(f"---- 件②③ 真交回被拒: lybra artifact ingest exit {rc} ----\n{out}{err}")
    assert rc != 0 and PROG in out + err
    # 件③ 拒因出口 = 合法出口命令原文(含撤回预演命令 + 门侧真实判定), 不再只写原则
    assert f"queue withdraw --task-id {CARD} --actor <你的顾问实例>" in err and "--dry-run" in err
    assert f"queue supersede --task-id {CARD} --by <新卡号>" in err and "改走代码卡" in err and "task_mode=code" in err
    assert "门侧真实判定(queue withdraw 同源" in err and "本卡在 claimed, 撤回门不要求 Owner 裁决引用" in err
    # 件② 拒收入真相: 一条交回拒收记录
    records = ai.return_rejection_records(rig.gov, CARD)
    assert len(records) == 1, records
    rec = records[0]
    _show(f"---- 件② 交回拒收记录 {Path(rec['_path']).relative_to(rig.gov)} ----\n" + Path(rec["_path"]).read_text(encoding="utf-8")[:1200])
    assert (rec["event_type"], rec["category"], rec["rejection_id"]) == ("return_rejected", PROG, f"return_rejected_{CARD}_1")
    assert rec["return_path"] == f"task_cards/{CARD}/RETURN.md" and rec["fingerprint"] == ai.return_fingerprint(rig.gov, CARD, rec["files"])
    # next 不再给同一交回命令, 改给出口全集原文
    d = nr.derive_next_step(CARD, rig.gov)
    text = nr.format_output(d)
    _show("---- 件② 拒后 lybra next(报告与产物未变)文本原文 ----\n" + text)
    assert not d["derivable"] and d["command"] == "" and d["action"]["type"] == "return_rejected"
    assert "需执行 return 动词" not in text and "不再给交回命令" in d["suggested_action"]
    ex = d["legal_exits"]
    assert ex["state"] == "return_rejected" and [e["key"] for e in ex["exits"]] == ["fix_resubmit", "new_subject", "withdraw", "change_scope"]
    assert ex["recommended"] == "new_subject", "配置卡交付本卡程序: 推荐改走代码卡(evidence_overrides.return_rejected_program)"
    resubmit = ex["exits"][0]["command"]
    assert "子 agent 卡(claude-code): 顾问把拒因原文派给执行子 agent" in resubmit
    assert f"lybra artifact ingest --task-id {CARD} --kind return --workspace-root {rig.gov}" in resubmit
    withdraw = ex["exits"][2]
    assert withdraw["owner_tier1"] == "withdraw" and "门不要求 Owner 裁决引用" in withdraw["gate"]
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "next", "--task-id", CARD])
    _show(f"---- 件② CLI lybra next --task-id {CARD} exit {rc} ----\n{out}{err}")
    assert "Exits(return_rejected × governance_outputs" in out and "[withdraw] 撤卡" in out and "[fix_resubmit] 修产物后重交" in out
    # 撤卡出口的「须不须 Owner」与 queue withdraw 实际判定同源: 预演确实放行(claimed 不要求 Owner 裁决引用)
    rc, wout, werr = _cli(["--workspace-root", str(rig.gov), "queue", "withdraw", "--task-id", CARD, "--actor", DRIVER,
                           "--reason", "夹具: 预演核对", "--dry-run", "--json"])
    _show(f"---- 件② 撤卡出口对照: queue withdraw --dry-run exit {rc} ----\n{wout[:800]}{werr}")
    assert rc == 0 and "owner_ruling" not in (wout + werr).lower().replace("owner_ruling_ref", "")
    # 产物改后(程序移出报告落点)= next 恢复给交回命令
    moved = launcher.read_bytes()
    launcher.unlink()
    d = nr.derive_next_step(CARD, rig.gov)
    _show(f"[件② 程序移出后 next] derivable={d['derivable']} command={d['command']}")
    assert d["derivable"] and d["command"].startswith(f"lybra artifact ingest --task-id {CARD} --kind return")
    # 原样放回 = 指纹回到拒收时 = 又不给交回命令(同一判据, 确定性)
    _write(launcher, moved.decode("utf-8"))
    launcher.chmod(0o755)
    assert nr.derive_next_step(CARD, rig.gov)["action"]["type"] == "return_rejected"
    # 指纹未变但此刻入门校验通过(产品升级 / 仓已入库)= 照常给交回命令并说明
    monkeypatch.setattr(ai, "program_scan", lambda *a, **k: {"programs": [], "input_copies": [], "repo_errors": []})
    d = nr.derive_next_step(CARD, rig.gov)
    _show(f"[件② 拒后未变而此刻校验通过] derivable={d['derivable']} notes={d['notes']}")
    assert d["derivable"] and "此刻产物入口校验已通过(产品 / 仓已变), 照常交回" in d["notes"]


def test_item2_dry_run_and_not_at_node_do_not_record(rig):  # noqa: F811
    _config_card(rig)
    init_governance_repo(rig.gov)
    f221._report(rig, "v1")
    _program(rig.gov / "task_cards" / CARD / "artifacts" / "demo-launcher", 400)
    rc, _out, _err = _cli(["artifact", "ingest", "--task-id", CARD, "--kind", "return", "--workspace-root", str(rig.gov), "--dry-run"])
    assert rc != 0 and ai.return_rejection_records(rig.gov, CARD) == [], "预演不落拒收记录"
    rc, _out, _err = _cli(["artifact", "ingest", "--task-id", CARD, "--kind", "verdict", "--workspace-root", str(rig.gov)])
    assert rc != 0 and ai.return_rejection_records(rig.gov, CARD) == [], "入门节点不符(kind 不符)不是对产物的拒收"


# ===========================================================================
# 件②③ loop: 子 agent 卡被拒 = 以 return_rejected 结束(advisor_fix), 结束原文附修产物指令 + 出口全集; 重跑不再执行交回
# ===========================================================================

def test_item3_loop_subagent_card_ends_with_fix_instruction_and_exits(rig):  # noqa: F811
    _config_card(rig, "pending")
    init_governance_repo(rig.gov)

    def executor(r):
        f221._report(r, "v1")
        _program(r.gov / "task_cards" / CARD / "artifacts" / "demo-launcher", 400)

    res, text = f221._loop(rig, [], executor)
    _show(f"---- 件②③ 子 agent config 卡交回被拒: loop exit {res.exit_code} reason {res.reason} ----\n{text[-2500:]}")
    assert res.reason == "return_rejected" and res.outcome == "not_derivable"
    assert "修产物指令(会话不可续接时顾问派给本卡执行实例): 你的交回 " in res.message and PROG in res.message
    assert "合法出口(return_rejected × governance_outputs" in res.message and "queue withdraw --task-id" in res.message
    assert len(ai.return_rejection_records(rig.gov, CARD)) == 1
    rc, out, err = _cli(["loop", "status", "--task-id", CARD, "--workspace-root", str(rig.gov), "--json"])
    na = json.loads(out)["next_action"]
    _show(f"---- 件② loop status next_action ----\naction={na['action']} reason={na['reason']}\nhint:\n{na['hint']}")
    assert na["action"] == "advisor_fix" and na["reason"] == "return_rejected"
    assert na["legal_exits"]["state"] == "return_rejected" and "[withdraw] 撤卡" in na["hint"]
    # 原命令重跑 loop: 推导核判拒后未变 = 不再执行同一交回命令(无新拒收记录), 同一结束原因
    calls = len([c for c in rig.cli if c[1:3] == ["artifact", "ingest"]])
    res2, text2 = f221._loop(rig, [], lambda r: None)
    _show(f"---- 件② 重跑 loop exit {res2.exit_code} reason {res2.reason} ----\n{text2[-1200:]}")
    assert res2.reason == "return_rejected" and len([c for c in rig.cli if c[1:3] == ["artifact", "ingest"]]) == calls
    assert len(ai.return_rejection_records(rig.gov, CARD)) == 1


# ===========================================================================
# 件③ pi 卡: loop 同会话续接执行体按拒因修一次 → 重新入门 → 照常推进; 修不好 = return_rejected
# ===========================================================================

def _pi_docs(vrig, monkeypatch, mode: str):
    _real_pi_template(monkeypatch)
    install_fake_pi(vrig.home / "fakepi", monkeypatch)
    _docs_policy(vrig.gov)
    _gov_card(vrig.gov, CARD)
    init_governance_repo(vrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", mode)
    out = io.StringIO()
    res = run_loop(CARD, vrig.gov, actor=DRIVER, policy_id=POL_DOCS, out=out, interval=0.05, max_wait=60, max_steps=20)
    return res, out.getvalue()


def _fixes(gov: Path) -> list[dict]:
    paths = sorted((gov / "5_tasks" / "records" / "loop_runs" / CARD).glob("looprun_*.md"))
    return [f for p in paths for f in nr._read_frontmatter(p).get("report_fixes") or []]


def test_item3_pi_card_resumed_once_fixes_artifacts(vrig, monkeypatch):
    res, text = _pi_docs(vrig, monkeypatch, "docs_program_once")
    lines = [ln for ln in text.splitlines() if any(k in ln for k in ("report_fix:", "交回被产物入口拒收", "run return", "] ok:", "exit "))]
    _show(f"---- 件③ pi 卡交回被拒 → 同会话续接修产物 → 重新入门: loop exit {res.exit_code} ----\n" + "\n".join(lines))
    prompt = (vrig.log / f"{CARD}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[件③ 续接修产物指令原文] {prompt[:1500]}")
    assert prompt.startswith("你的交回 ") and f"被产物入口拒收 {PROG}" in prompt and "移出或删除" in prompt and "BLOCK" in prompt
    fixes = _fixes(vrig.gov)
    assert [(f["role"], f["outcome"], f["kinds"]) for f in fixes] == [("executor", "resumed", [PROG])], fixes
    assert any("续接修产物 1/1" in ln for ln in lines)
    assert not (vrig.gov / "task_cards" / CARD / "artifacts" / "demo-launcher").exists()
    assert f90_returns(vrig.gov), "修后重新入门, 门落交回记录"
    assert res.outcome == "not_derivable" and "只写治理根" in res.message, res.message


def f90_returns(gov: Path) -> list[Path]:
    directory = gov / "5_tasks" / "records" / "returns" / CARD
    return sorted(directory.glob("*.md")) if directory.is_dir() else []


def test_item3_pi_card_not_fixed_ends_return_rejected(vrig, monkeypatch):
    res, text = _pi_docs(vrig, monkeypatch, "docs_program")
    _show(f"---- 件③ pi 卡续接后未改写: loop exit {res.exit_code} reason {res.reason} ----\n{res.message[:2000]}")
    assert res.reason == "return_rejected" and "续接后报告未改写" in res.message
    assert [(f["outcome"], f["kinds"]) for f in _fixes(vrig.gov)] == [("not_rewritten", [PROG])]
    assert not f90_returns(vrig.gov)


# ===========================================================================
# 件④ 回放 LANTU-CLDEP1 形态(夹具自造: 他仓固定提交 git archive → run-1/contracts 只读, chris 同构多仓 project.json)
# ===========================================================================

def test_item4_replay_cldep1_shape_input_copies_return_admitted(rig, tmp_path, monkeypatch):  # noqa: F811
    contracts, commit = _contracts_repo(tmp_path / "repos")
    _declare_repos(rig.gov, rig.code_repo, contracts)
    _config_card(rig, artifacts=[{"criterion": "closed-loop-deploy-boot", "path_glob": "deploy-summary.md", "kind": "report"}])
    init_governance_repo(rig.gov)
    slot = rig.gov / "task_cards" / CARD
    _write(slot / "artifacts" / "deploy-summary.md", f"# 部署摘要\n\ncontracts = lantu-contracts@{commit} git archive(只读)\n")
    _write(slot / "RETURN.md", f78._fm({"outputs": [f"task_cards/{CARD}/artifacts/deploy-summary.md"], "model": "fixture"},
                                        f"# RETURN — {CARD}\n\n## 一句话结论\n闭环环境首次部署完成(夹具)。\n"))
    run_dir = tmp_path / "vr" / CARD / "run-1"
    _archive(contracts, commit, run_dir / "contracts")
    _write(run_dir / "env" / "my.cnf", "[mysqld]\n")
    _run_dir(monkeypatch, run_dir)
    try:
        before = ai.program_guard_declaration()
        assert before and "input_copy" in before
        got = ai.validate_task_artifact(CARD, rig.gov)
        _show(f"---- 件④ 回放 LANTU-CLDEP1 形态: 交回入门 ok={got['ok']} category={got['category']} ----\n" + "\n".join(got.get("notes") or []))
        assert got["ok"] and sorted(Path(c["path"]).name for c in got["input_copies"]) == ["validate.py", "verify.py"]
        rc, out, err = _cli(["artifact", "ingest", "--task-id", CARD, "--kind", "return", "--workspace-root", str(rig.gov)])
        _show(f"---- 件④ 真交回 exit {rc} ----\n{out}{err}")
        assert rc == 0, out + err
        copies = ai.return_rejection_records(rig.gov, CARD, event_type="return_input_copies")
        assert len(copies) == 1 and len(copies[0]["files"]) == 2 and "来源仓 lantu-contracts" in copies[0]["reasons"][0]
        _show(f"[件④ 输入副本事件记录(审计可见)] {Path(copies[0]['_path']).relative_to(rig.gov)}: {copies[0]['reasons'][0]}")
        assert ai.return_rejection_records(rig.gov, CARD) == []
    finally:
        _unlock(run_dir)


def test_f236_declarations_fail_closed(monkeypatch):
    from tools.schema_loader import SchemaLoadError

    real = nr._artifact_ingest_declaration

    def broken():
        decl = json.loads(json.dumps(real()))
        decl["return"]["rejection_record"]["texts"].pop("suggested")
        decl["return"]["program_guard"]["input_copy"]["texts"].pop("note")
        return decl

    monkeypatch.setattr(nr, "_artifact_ingest_declaration", broken)
    with pytest.raises(SchemaLoadError) as exc:
        ai.return_rejected_declaration()
    assert "rejection_record" in str(exc.value)
    with pytest.raises(SchemaLoadError) as exc2:
        ai.program_guard_declaration()
    assert "input_copy" in str(exc2.value)


def test_item2_gate_side_rejection_keeps_return_command(rig):  # noqa: F811
    """门侧拒(产物入口校验通过而门 queue_return 拒, category = shell_rejected_category)同落拒收记录, 但门侧状态可在产物不变时改变:
    next 照常给交回命令并说明, 不改判 return_rejected(F150 例行门拒修法照旧)。"""
    _config_card(rig)
    init_governance_repo(rig.gov)
    f221._report(rig, "v1")
    shell = str(ai.return_rejected_declaration()["shell_rejected_category"])
    ai.write_return_rejection(rig.gov, CARD, category=shell, reasons=["夹具: 门侧拒"], return_path=f"task_cards/{CARD}/RETURN.md")
    d = nr.derive_next_step(CARD, rig.gov)
    _show(f"[件② 门侧拒后 next] derivable={d['derivable']} notes={d['notes']}")
    assert d["derivable"] and d["command"].startswith(f"lybra artifact ingest --task-id {CARD} --kind return")
    assert f"上次交回被门侧拒 return_rejected_{CARD}_1({shell}" in d["notes"] and "legal_exits" not in d


def test_f236_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f236_return_rejection_exits.py"
    assert rel in discover_test_files([rel], default_test_contract())
