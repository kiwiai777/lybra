"""AIPOS-F237 — 长任务时限跟实测走。

件① 写入口 `lybra project set-test-contract`: 唯一实现 workspace_config.set_project_test_contract(update_project_json 唯一写路径,
   写前以唯一读取口 project_test_contract 校验将写内容 = TEST_CONTRACT_INVALID 同判); 预演给 diff 与单次全量时限推导的写前 / 写后原文
   (post_merge_regression.full_run_timeout 唯一推导); --confirm 写并经 F159 同一提交口落账; 两阶段登记 verbs.schema project_json_writers。
件② 等交回上限随长任务推导: loop_driver.loop_wait_limit 未显式给时取 max(原缺省, full_runs_wait_floor) —— 全量次数 = 卡分支 tip 1 次 +
   main 侧基线(已记录可复用 = 0 次, 其余 = 1 次)× 单次全量时限(full_run_timeout)+ 余量; 依据写 loop 首行与运行记录 wait_limit。
   开工稿时限句同读 full_run_timeout。
件③ 到点不杀活人: 拉起的进程到上限仍存活且近期有事件 / 本卡 run-long 在跑 = 续等(launches[].wait_extensions), 无活动 = 照旧终止,
   到硬上限 = 终止(launches[].wait_extension_end)。判定唯一 loop_driver.wait_extension_decision。

靶场: F90 loop 全链靶场(rig)/ F95 拉起靶场(lrig, 假 harness)/ F189 开工稿靶场; 临时治理根与临时 git 仓, 不连生产门, 不读写真实治理根。
"""
from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 全链靶场
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import POLICY, TASK, rig  # noqa: E402,F401  — pytest 夹具
from test_aipos_f95_harness_launch import POLICY_LAUNCH, lrig  # noqa: E402,F401  — pytest 夹具
from tools.aipos_cli import loop_driver  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import post_merge_regression as pmr  # noqa: E402
from tools.aipos_cli.clock import iso_z, utc_now  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract, run_loop  # noqa: E402

RUNALL = "tests/run-all.sh"
OWNER_REF = "Owner 10-26: 回归时限按近 5 次全量实测推导(夹具)"


def _show(text: str) -> None:
    print(text, flush=True)


def _cli(*argv: str) -> tuple[int, str, str]:
    import contextlib

    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


def _project(gov: Path) -> dict:
    return json.loads((gov / "project.json").read_text(encoding="utf-8"))


def _set_project(gov: Path, data: dict) -> None:
    (gov / "project.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _test_contract(gov: Path, **pmr_keys) -> None:
    data = _project(gov)
    data["test_contract"] = {"runall_path": RUNALL, "require_tests": True, "ratchet": "棘轮夹具句",
                             "post_merge_regression": {"mode": "warn", "execution": "async", "timeout_seconds": 1500, **pmr_keys}}
    _set_project(gov, data)


def _samples(gov: Path, seconds: list[float]) -> None:
    """近期全量实测样本(合并后一跑完整; 新→旧按写入先后)。"""
    for i, value in enumerate(seconds):
        pmr.write_event_record(gov, f"AIPOS-T237S{i}", DRIVER, {
            "task_id": f"AIPOS-T237S{i}", "runall_path": RUNALL, "merge_commit": f"{i:x}" * 40, "status": "pass",
            "merged_complete": True, "merged_failures": [], "merged_seconds": value})


def _md5(path: Path) -> str:
    import hashlib

    return hashlib.md5(path.read_bytes()).hexdigest()


# ===========================================================================
# 件① set-test-contract: 预演差异 + 推导时限原文 / 确认写入并落账 / 非法值拒
# ===========================================================================

def test_item1_set_test_contract_preview_confirm_landing_and_refusals(rig):
    gov = rig.gov
    _test_contract(gov)
    _samples(gov, [1400.0, 1480.0, 1460.0])
    init_governance_repo(gov)  # 盘上现有文件(含 test_contract 与样本)作基线提交
    pj = gov / "project.json"
    base = ["project", "set-test-contract", "--workspace-root", str(gov)]
    before = _md5(pj)

    rc, out, err = _cli(*base, "--timeout-recent-runs", "5", "--timeout-factor", "1.5", "--serialize", "--owner-ref", OWNER_REF)
    _show(f"---- 件① set-test-contract 预演 rc={rc} ----\n{out}{err}")
    assert rc == 0 and _md5(pj) == before, err
    assert ": 预览(未写; 加 --confirm 写入)" in out.splitlines()[1]
    assert "test_contract.post_merge_regression.timeout_recent_runs = 5" in out and "test_contract.post_merge_regression.serialize = True" in out
    assert "写前 时限 1500s = 声明 timeout_seconds(未开推导: timeout_recent_runs=0)" in out
    assert "写后 时限 2190s = max(声明下限 1500s, ceil(1.5 × 近 3 次全量实测中位数 1460s))" in out  # ceil(1.5 × 1460) = 2190
    assert '+      "timeout_recent_runs": 5,' in out and '+    "owner_ref": "' + OWNER_REF + '",' in out

    rc, out, err = _cli(*base, "--timeout-recent-runs", "5", "--timeout-factor", "1.5", "--serialize", "--owner-ref", OWNER_REF,
                        "--confirm", "--actor", DRIVER)
    _show(f"---- 件① set-test-contract --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and ": 已写入" in out.splitlines()[1] and "落账" in out
    tc = _project(gov)["test_contract"]
    assert tc["post_merge_regression"] == {"mode": "warn", "execution": "async", "timeout_seconds": 1500, "timeout_recent_runs": 5,
                                           "timeout_factor": 1.5, "serialize": True}
    assert tc["owner_ref"] == OWNER_REF and tc["runall_path"] == RUNALL and tc["ratchet"] == "棘轮夹具句"  # 其余键原样
    log = subprocess.run(["git", "-C", str(gov), "log", "-1", "--format=%s%n%b", "--name-only"], capture_output=True, text=True).stdout
    _show(f"[件①] 落账提交原文:\n{log}")
    assert "chore(governance): 项目配置落账 set-test-contract" in log and "Command: lybra project set-test-contract --confirm" in log
    assert subprocess.run(["git", "-C", str(gov), "status", "--porcelain", "project.json"], capture_output=True, text=True).stdout == ""
    policy = pmr.resolve_policy(gov, Path(_project(gov)["code_repo"]))  # 合并后回归同读推导
    assert policy["timeout_seconds"] == 2190 and policy["serialize"] is True

    written = _md5(pj)
    refusals = [
        (["--timeout-factor", "0.5", "--owner-ref", OWNER_REF], "TEST_CONTRACT_INVALID", "须为 ≥1 的数"),
        (["--timeout-recent-runs", "-1", "--owner-ref", OWNER_REF], "TEST_CONTRACT_INVALID", "须为 ≥0 整数"),
        (["--timeout-seconds", "0", "--owner-ref", OWNER_REF], "TEST_CONTRACT_INVALID", "须为正整数"),
        (["--mode", "block", "--execution", "async", "--owner-ref", OWNER_REF], "TEST_CONTRACT_INVALID", "mode=block 不可配 execution=async"),
        (["--mode", "loud", "--owner-ref", OWNER_REF], "TEST_CONTRACT_INVALID", "不在声明值域"),
        (["--owner-ref", OWNER_REF], "TEST_CONTRACT_NOTHING_TO_SET", "没有给出任何"),
        (["--timeout-factor", "2", "--owner-ref", "  "], "TEST_CONTRACT_OWNER_REF_REQUIRED", "非空单行"),
    ]
    for extra, code, needle in refusals:
        for phase in ([], ["--confirm"]):
            rc, out, err = _cli(*base, *extra, *phase)
            _show(f"[件① 拒 {' '.join(extra + phase)}] rc={rc} {err.strip()}")
            assert rc == 1 and f"Error: {code}(project.json 未改动)" in err and needle in err, err
            assert _md5(pj) == written


def test_item1_registered_in_two_phase_family_and_landing():
    from tools.aipos_cli.workspace_config import PMR_KEYS, test_contract_writer_declaration
    from tools.schema_loader import load_schema

    verbs = load_schema("verbs")
    assert "set-test-contract" in verbs["two_phase_protocol"]["project_json_writers"]["commands"]
    assert "set-test-contract" in verbs["verbs"]["lybra_governance_commit"]["post_write_landing"]["commands"]["project_writers"]["subcommands"]
    assert set(test_contract_writer_declaration()["keys"]) == set(PMR_KEYS)
    config = load_schema("config")["configuration_sources"]["project_json"]["schema"]["test_contract"]["schema"]
    assert config["owner_ref"]["type"] == "string" and "default" not in config["owner_ref"]  # 新键: 无缺省 = 行为不变
    # 唯一写路径: set_project_test_contract 经 update_project_json, 推导经 full_run_timeout(禁第二实现)
    src = (REPO_ROOT / "tools" / "aipos_cli" / "workspace_config.py").read_text(encoding="utf-8")
    body = src[src.index("def set_project_test_contract("):src.index("\n\n\n", src.index("def set_project_test_contract("))]
    assert "update_project_json(" in body and "full_run_timeout(" in body and "derive_timeout(" not in body
    pm = (REPO_ROOT / "tools" / "aipos_cli" / "post_merge_regression.py").read_text(encoding="utf-8")
    policy = pm[pm.index("def resolve_policy("):pm.index("def full_run_timeout(")]
    assert "full_run_timeout(" in policy and "derive_timeout(" not in policy


# ===========================================================================
# 件② 等交回上限推导: 审计卡两边基线 vs 可复用基线 / 未声明清单不抬 / 显式最优先
# ===========================================================================

def _loop_until_wait(gov: Path, **kw) -> tuple[object, str, list[float]]:
    seen: list[float] = []

    def watch(args, expect_ready, **_kw):
        seen.append(float(args.timeout))
        return 3

    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, watch=watch, interval=0.05, max_steps=10, **kw)
    return res, out.getvalue(), seen


def _main_sha(rig) -> str:
    return subprocess.run(["git", "-C", str(rig.code_repo), "rev-parse", "main"], capture_output=True, text=True, check=True).stdout.strip()


def test_item2_wait_limit_derived_two_sides_vs_reusable_baseline(rig):
    gov = rig.gov
    contract = load_loop_contract()
    decl = contract["wait_limits"]
    margin = float(decl["full_runs"]["margin_seconds"])
    _test_contract(gov, timeout_recent_runs=5, timeout_factor=1.5)
    _samples(gov, [2000.0, 2100.0, 2200.0])  # 中位数 2100 × 1.5 = 3150 > 声明 1500
    f90._card(gov, TASK, "pending", harness="pi")  # pi 拉起模式: 原缺省 1800
    init_governance_repo(gov)

    # main 侧无记录基线 = 须自跑 → 2 次全量
    res, out, seen = _loop_until_wait(gov, policy_id=POLICY)
    first = next(ln for ln in out.splitlines() if "max_wait=" in ln)
    _show(f"---- 件② 两边基线(main 须自跑)loop 首行原文 ----\n{first}")
    assert seen == [2 * 3150 + margin], seen
    assert f"max_wait={2 * 3150 + margin:g}s(等交回上限: 按全量推导抬高(原 1800s 来自 缺省(verbs.schema lybra_loop.parameters.max_wait.default))" in first
    assert f"= 2 次全量 × 单次 3150s + 余量 {margin:g}s(全量次数: 卡分支 tip 1 次 + main 侧基线 1 次——main " in first
    assert "单次: 时限 3150s = max(声明下限 1500s, ceil(1.5 × 近 3 次全量实测中位数 2100s))" in first
    meta = lrr.read_run(sorted((gov / "5_tasks/records/loop_runs" / TASK).glob("looprun_*.md"))[-1],
                        lrr.run_record_declaration(contract))
    _show(f"[件②] 运行记录 wait_limit: {json.dumps(meta['wait_limit'], ensure_ascii=False)}")
    assert meta["wait_limit"]["seconds"] == 2 * 3150 + margin and meta["wait_limit"]["full_runs"]["runs"] == 2
    assert meta["wait_limit"]["full_runs"]["baseline_status"] == pmr.AUDIT_BASELINE_SELF_RUN

    # main 侧已记录可复用(合并后集合完整的记录, merge_commit = main HEAD)→ 1 次全量
    sha = _main_sha(rig)
    pmr.write_event_record(gov, "AIPOS-T237B", DRIVER, {"task_id": "AIPOS-T237B", "runall_path": RUNALL, "merge_commit": sha,
                                                        "status": "pass", "merged_complete": True, "merged_failures": [],
                                                        "merged_seconds": 2100.0})
    floor = loop_driver.full_runs_wait_floor(gov, {"task_id": TASK}, decl)
    _show(f"[件②] 可复用基线推导原文: {floor['basis']}")
    assert floor["baseline_status"] == pmr.AUDIT_BASELINE_RECORDED and floor["runs"] == 1
    assert floor["full_run_seconds"] == 3150 and floor["seconds"] == 3150 + margin  # 新样本(2100)加入后中位数仍 2100
    assert f"main 侧基线 0 次——main {sha[:12]}" in floor["basis"]
    seconds, source, _mode, got = loop_driver.loop_wait_limit(gov, TASK, {"task_id": TASK, "harness": "pi"}, contract=contract,
                                                              explicit=None)
    assert seconds == 3150 + margin and got["runs"] == 1 and "按全量推导抬高" in source

    # 显式 --max-wait 最优先(不推导); 推导值不高于原缺省 = 原来源不变
    seconds, source, _mode, got = loop_driver.loop_wait_limit(gov, TASK, {"task_id": TASK}, contract=contract, explicit=7)
    assert seconds == 7 and source == decl["messages"]["source_explicit"] and got is None
    _test_contract(gov)  # 未开推导: 单次 = 声明 1500(可复用基线 1 次)
    seconds, source, _mode, got = loop_driver.loop_wait_limit(gov, TASK, {"task_id": TASK}, contract=contract, explicit=None)
    assert got["full_run_seconds"] == 1500 and seconds == max(1800.0, 1500 + margin)


def test_item2_no_runall_declared_keeps_default_and_unavailable_is_named(rig, monkeypatch):
    gov = rig.gov
    contract = load_loop_contract()
    decl = contract["wait_limits"]
    data = _project(gov)
    data.pop("test_contract", None)
    _set_project(gov, data)
    seconds, source, _mode, got = loop_driver.loop_wait_limit(gov, TASK, {"task_id": TASK}, contract=contract, explicit=None)
    _show(f"[件②] 未声明 runall_path: {seconds:g}s — {source}; 推导 {got}")
    assert seconds == 1800 and got["runs"] == 0 and "未声明 runall_path" in got["basis"]
    data["test_contract"] = {"runall_path": RUNALL, "post_merge_regression": {"timeout_factor": 0.5}}  # 声明坏 = 推导不可得
    _set_project(gov, data)
    seconds, source, _mode, got = loop_driver.loop_wait_limit(gov, TASK, {"task_id": TASK}, contract=contract, explicit=None)
    _show(f"[件②] 声明坏: {seconds:g}s — {source}")
    assert seconds == 1800 and got is None and decl["messages"]["full_runs_unavailable"].split("{")[0] in source
    assert "TEST_CONTRACT_INVALID" in source


def test_item2_kickoff_timing_reads_same_derivation(tmp_path, monkeypatch):
    from test_aipos_f187_kickoff_worktree_cleanup import _update_project
    from test_aipos_f189_small_sweep import _kickoff_rig, _runall_line

    gov = _kickoff_rig(tmp_path, monkeypatch, card="HBX-237", harness="claude-code")
    line, data = _runall_line(gov, "HBX-237")
    _show(f"---- 件② 开工稿全量句(未开推导, 行为不变)原文 ----\n{line}")
    assert "`1500` 秒(来源 " in line and "推导" not in line
    _samples(gov, [300.0, 320.0, 340.0])
    pj = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    _update_project(gov, test_contract={**pj["test_contract"], "post_merge_regression": {
        "timeout_seconds": 100, "timeout_recent_runs": 5, "timeout_factor": 1.5}})
    line, data = _runall_line(gov, "HBX-237")
    _show(f"---- 件② 开工稿全量句(推导: 中位数 320 × 1.5 = 480)原文 ----\n{line}")
    assert data["sources"]["runall_timing"] == "sync"  # 480 ≤ claude-code 单次工具时限 600 = 同步(按推导值判)
    assert "`480` 秒(按近期全量实测推导" in line and "时限 480s = max(声明下限 100s, ceil(1.5 × 近 3 次全量实测中位数 320s))" in line
    full = pmr.full_run_timeout(gov, Path(pj["code_repo"]))
    assert full["timeout_seconds"] == 480 and full["derived"] is True  # 与 loop 等交回上限同一推导


# ===========================================================================
# 件③ 到点不杀活人: 判定单元 + 拉起靶场(续等 → 硬上限终止 / 无活动照旧终止)
# ===========================================================================

def _decl() -> dict:
    return loop_driver.wait_limits_declaration(load_loop_contract())


def test_item3_extension_decision_activity_run_long_idle_hard_cap(tmp_path, monkeypatch):
    from tools.aipos_cli import run_long

    decl = _decl()
    window = float(decl["extension"]["activity_window_seconds"])
    now = utc_now().replace(microsecond=0)  # iso_z 秒精度: 取整秒免差 1s
    hard = now + timedelta(seconds=3000)
    monkeypatch.setattr(run_long, "run_status", lambda gov, card, name=None: [])
    got = loop_driver.wait_extension_decision(tmp_path, "AIPOS-X", decl=decl, last_event_at=iso_z(now - timedelta(seconds=60)), now=now,
                                              hard_deadline=hard, max_wait=1500)
    _show(f"[件③ 近期有事件] {got}")
    assert got["extend"] and got["seconds"] == float(decl["extension"]["step_seconds"]) and "距今 60s" in got["reason"]
    got = loop_driver.wait_extension_decision(tmp_path, "AIPOS-X", decl=decl, last_event_at=iso_z(now - timedelta(seconds=window + 60)),
                                              now=now, hard_deadline=hard, max_wait=1500)
    _show(f"[件③ 无事件无 run-long] {got['line']}")
    assert not got["extend"] and got["kind"] == "idle" and got["line"].startswith("到上限时无活动, 不续等")
    monkeypatch.setattr(run_long, "run_status", lambda gov, card, name=None: [{"name": "runall-main", "state": run_long.STATE_RUNNING},
                                                                              {"name": "runall-tip", "state": run_long.STATE_FINISHED}])
    got = loop_driver.wait_extension_decision(tmp_path, "AIPOS-X", decl=decl, last_event_at=iso_z(now - timedelta(seconds=window + 60)),
                                              now=now, hard_deadline=now + timedelta(seconds=100), max_wait=1500)
    _show(f"[件③ 静默但 run-long 在跑] {got}")
    assert got["extend"] and got["seconds"] == 100 and "本卡 run-long 仍在跑 ['runall-main']" in got["reason"]
    got = loop_driver.wait_extension_decision(tmp_path, "AIPOS-X", decl=decl, last_event_at=iso_z(now), now=now,
                                              hard_deadline=now + timedelta(seconds=0.5), max_wait=1500, slack=1.0)
    _show(f"[件③ 到硬上限] {got['line']}")
    assert not got["extend"] and got["kind"] == "hard_cap"
    assert got["line"].startswith(f"已到硬上限 {1500 * float(decl['extension']['hard_cap_factor']):g}s")

    def broken(gov, card, name=None):
        raise run_long.RunLongError("队列中找不到卡")

    monkeypatch.setattr(run_long, "run_status", broken)
    got = loop_driver.wait_extension_decision(tmp_path, "AIPOS-X", decl=decl, last_event_at=None, now=now, hard_deadline=hard, max_wait=1)
    assert got["kind"] == "idle" and "run-long 状态读不出: RunLongError: 队列中找不到卡" in got["reason"]  # 不吞


def _launch_records(gov: Path) -> list[dict]:
    path = sorted((gov / "5_tasks/records/loop_runs" / TASK).glob("looprun_*.md"))[-1]
    return lrr.read_run(path, lrr.run_record_declaration(load_loop_contract()))["launches"]


def test_item3_active_launch_extended_then_hard_cap_terminates(lrig, monkeypatch):
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang")  # 吐事件后挂起: 到上限时最近事件在窗口内 = 仍在干活
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=3, max_steps=5)
    text = out.getvalue()
    _show("---- 件③ 到上限仍有事件 → 续等 → 硬上限终止 输出原文 ----\n" + text)
    factor = float(_decl()["extension"]["hard_cap_factor"])
    assert "] extend: 到等交回上限仍在干活, 续等 " in text and "(第 1 次, 硬上限 6s 内): 进程存活, 最近事件 " in text
    assert f"] extend_end: 已到硬上限 {3 * factor:g}s(上限 3s × {factor:g}), 不再续等" in text
    assert res.exit_code == 3 and "已终止拉起的 pi 进程组" in res.message and "已到硬上限" in res.message, res.message
    launch = _launch_records(lrig.gov)[-1]
    _show(f"[件③] 运行记录 wait_extensions={launch.get('wait_extensions')} wait_extension_end={launch.get('wait_extension_end')}")
    assert launch["outcome"] == "timeout" and launch["wait_extensions"][0]["count"] == 1
    assert "进程存活, 最近事件" in launch["wait_extensions"][0]["reason"] and launch["wait_extension_end"]["kind"] == "hard_cap"
    assert not f95._group_alive(int((lrig.log / f"{TASK}.pgid").read_text()))


def test_item3_idle_launch_terminated_at_limit_as_before(lrig, monkeypatch):
    real = loop_driver.load_loop_contract

    def idle_contract(repo_root=None):
        contract = copy.deepcopy(real(repo_root))
        contract["wait_limits"]["extension"]["activity_window_seconds"] = 0.5  # 窗口缩短: 挂起后静默 > 窗口 = 无活动
        return contract

    monkeypatch.setattr(loop_driver, "load_loop_contract", idle_contract)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang")
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=3, max_steps=5)
    text = out.getvalue()
    _show("---- 件③ 到上限无活动 → 照旧终止 输出原文 ----\n" + text)
    assert "] extend: " not in text and "] extend_end: 到上限时无活动, 不续等: 进程存活但距最近事件 " in text
    assert res.exit_code == 3 and "等待产物超时" in res.message and "已终止拉起的 pi 进程组" in res.message
    launch = _launch_records(lrig.gov)[-1]
    assert "wait_extensions" not in launch and launch["wait_extension_end"]["kind"] == "idle"
    assert not f95._group_alive(int((lrig.log / f"{TASK}.pgid").read_text()))
