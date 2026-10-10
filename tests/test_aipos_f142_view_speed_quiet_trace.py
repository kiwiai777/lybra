"""AIPOS-F142 — 视图提速与去噪。

靶场 = 临时治理根(自造人肉期样本, 项目 hbjfx; 规模读 verbs.schema lane_view.performance.fixture: 300 张卡含 270 冻结、三 lane、
若干 loop 运行记录 / 信封 / 认领记录)。禁真门/真治理根/真工位/真 pi。复用 F133 / F141 靶场与 CLI 包装(_hcard / _cli / _show)、
F78C 骨架(_gov_skeleton / _product_repo)、F66B sync 靶场(rig / FakeGate)、F138 信封诊断取样(_trace_sink), 禁第二份靶场。

件① 剖析与提速(既有唯一实现内): 一次视图调用全程一个只读作用域 task_loader.task_card_lookup_scope(可重入) —— 队列索引一趟、
    冻结清单重放一次(legacy_baseline.frozen_tasks)、信封已放行计数一趟(autonomy_policy.count_preauthorized_claims), 经
    lookup_scope_memo; 作用域外(门写动作)每次现算。结案取下一张只扫可当选状态(next_resolver.NEXT_CARD_STATES)。
    loop / next 先于全量校验报告分派。判据与结果不变(同一夹具前后结论一致由各既有卡夹具 + 本文件结论断言钉住)。
件② 去噪: [ENVELOPE_TRACE] 开关只在发射处 autonomy_policy.trace_envelope 一处判定(envelope_trace_enabled), 声明 verbs.schema
    envelope_trace; CLI 缺省关, --verbose(挂载口 attach_verbose_flags)/ 调试环境变量开; 非 CLI 进程(门服务)照旧留痕。
    只收调试轨迹, 信封判定结果与拒因原因链不变。
件③ 性能回归夹具: 阈值读声明 lane_view.performance, 断言 阈值 × ci_slack_factor; 另以「全量读盘趟数」断言(与机器快慢无关)防 O(卡数²) 回退。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write  # noqa: E402  — 靶场/替身唯一来源
from test_aipos_f78_engine_agnostic import DRIVER  # noqa: E402
from test_aipos_f78c_card_repo import _gov_skeleton, _product_repo  # noqa: E402
from test_aipos_f133_lane_view_dependencies import _cli, _hcard, _show  # noqa: E402
from test_aipos_f138_remote_charter_pull import _trace_sink  # noqa: E402
from test_aipos_f66b_project_scoped_distribution import EXEC, rig  # noqa: E402,F401  — rig = F66B sync 靶场 fixture

TRACE = "[ENVELOPE_TRACE]"


def _perf() -> dict:
    from tools.schema_loader import load_schema

    return load_schema("verbs")["lane_view"]["performance"]


def _trace_decl() -> dict:
    from tools.aipos_cli.autonomy_policy import envelope_trace_declaration

    return envelope_trace_declaration()


# ===========================================================================
# 靶场: 300 卡人肉期样本(270 冻结 / 三 lane / loop 运行记录 / 信封 + 认领记录)
# ===========================================================================

_ACTIVE_STATES = ("pending", "claimed", "blocked")


@pytest.fixture
def big(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown
    from tools.aipos_cli.legacy_baseline import write_freeze_entry
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    fx = _perf()["fixture"]
    lanes = list(fx["lanes"])
    root = _gov_skeleton(tmp_path / "gov-hbjfx", monkeypatch)
    for sub in ("stage_archive", "governance/decision_log"):
        (root / sub).mkdir(parents=True)
    repos = {name: _product_repo(tmp_path / f"repo-{name}") for name in lanes}
    _write(root / "project.json", json.dumps({"project": "hbjfx", "config_version": 1,
                                              "repos": {"default": lanes[0], "items": {k: str(v) for k, v in repos.items()}}}))
    total, frozen_n = int(fx["cards"]), int(fx["frozen"])
    active = total - frozen_n
    cards: list[dict] = []
    for i in range(total):
        tid = f"HBJ-{i:04d}"
        lane = lanes[(i // len(_ACTIVE_STATES)) % len(lanes)]  # 与队列状态错开: 每个 lane 都有 pending/claimed/blocked
        if i < active:  # 在办: pending / claimed / blocked 轮转(pending 的一半带 needs_owner)
            queue = _ACTIVE_STATES[i % len(_ACTIVE_STATES)]
            extra = {"priority": ("high", "medium", "low")[i % 3]}
            if queue == "pending" and i % 2 == 0:
                extra["needs_owner"] = True
        else:  # 人肉期历史卡: 多为 completed 无门记录, 少量滞留在办目录(冻结前在视图里冒出来的那类)
            queue = "completed" if i % 10 else ("claimed", "blocked", "pending")[i % 3]
            extra = {"status": "completed", "needs_owner": bool(i % 7 == 0)}
        _hcard(root, tid, queue, lane_repo=lane, **extra)
        cards.append({"task_id": tid, "queue_state": queue, "card_status": extra.get("status", queue), "lane": lane})
    plan = {"action": "freeze", "selected": [c for c in cards[active:]]}
    write_freeze_entry(root, plan, reason="人肉期存量(F142 性能夹具)", actor=DRIVER)
    for n in range(int(fx["policies"])):
        pid = f"pol_hbj_{n:02d}"
        _write(root / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
            policy_id=pid, agent_or_role=DRIVER, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
            max_tasks=1000, owner_approval_ref="f142-fixture", task_selector_project="hbjfx",
            task_selector_lane_repo=lanes[n % len(lanes)]))
    for n in range(int(fx["claim_records"])):
        tid = cards[active + (n % frozen_n)]["task_id"]
        _write(root / "5_tasks" / "records" / "claims" / tid / f"claim_{tid}_20260101_{n:06d}_{DRIVER}.md",
               _fm({"record_type": "claim_record", "event_type": "claim", "claim_id": f"claim_{tid}_{n}", "task_id": tid,
                    "agent_instance": DRIVER, "actor": DRIVER, "autonomy_mode": "PreAuthorized",
                    "owner_policy_ref": f"pol_hbj_{n % int(fx['policies']):02d}", "claimed_at": "2026-01-01T00:00:00Z"}))
    # loop 运行记录: 一张在办卡已结案(completed → 结案取下一张), 其余未结束(运行中)
    contract = load_loop_contract()
    ended_id = next(c["task_id"] for c in cards[:active] if c["queue_state"] == "claimed")
    recorders = []
    for c in [c for c in cards[:active] if c["task_id"] != ended_id][: int(fx["loop_runs"]) - 1]:
        recorders.append(LoopRunRecorder(root, c["task_id"], driver=DRIVER, contract=contract, warn=lambda _m: None))
    done = LoopRunRecorder(root, ended_id, driver=DRIVER, contract=contract, warn=lambda _m: None)
    done.end(outcome="completed", exit_code=0, reason="completed", message=f"{ended_id} 已结案(夹具)")
    try:
        yield {"root": root, "cards": cards, "active": active, "ended_id": ended_id, "lanes": lanes}
    finally:
        for rec in recorders:
            rec.close_log()


def _timed(argv: list[str]) -> tuple[float, int, str, str]:
    start = time.perf_counter()
    rc, out, err = _cli(argv)
    return time.perf_counter() - start, rc, out, err


def _within(label: str, seconds: float, threshold: float) -> None:
    slack = float(_perf()["ci_slack_factor"])
    _show(f"件③ {label}: {seconds:.2f}s(声明阈值 {threshold:g}s, CI 断言 ≤ {threshold * slack:g}s)")
    assert seconds <= threshold * slack, f"{label} {seconds:.2f}s 超出 声明阈值 {threshold}s × ci_slack_factor {slack}"


# ===========================================================================
# 件③ 性能回归夹具: 单卡 status / 全量 status / next / needs-owner / brief 在声明阈值内
# ===========================================================================

def test_item3_single_card_status_fast_and_takes_next(big):
    perf = _perf()
    root, ended = big["root"], big["ended_id"]
    seconds, rc, out, err = _timed(["loop", "status", "--task-id", ended, "--workspace-root", str(root)])
    _show(f"件③ loop status --task-id {ended}(已结束)原文:\n{out}")
    assert rc == 0, err
    assert "[ended]" in out and "顾问下一动作: card_done_take_next(completed)" in out
    assert "下一张可推进卡: HBJ-" in out  # 结案取下一张仍走 F133 出口(只扫可当选状态)
    assert TRACE not in out + err
    _within(f"单卡 loop status(已结束, {perf['fixture']['cards']} 卡治理根)", seconds, float(perf["single_card_status_seconds"]))


def test_item3_full_views_fast(big):
    perf = _perf()
    root = big["root"]
    limit = float(perf["full_view_seconds"])
    for label, argv in (("全量 loop status", ["loop", "status", "--workspace-root", str(root)]),
                        ("lybra next 项目扫描", ["next", "--workspace-root", str(root)]),
                        ("lybra needs-owner --lane web", ["--workspace-root", str(root), "needs-owner", "--lane", "web"]),
                        ("lybra brief", ["brief", "--workspace-root", str(root)])):
        seconds, rc, out, err = _timed(argv)
        _show(f"件③ {label} 原文(前 12 行):\n" + "\n".join(out.splitlines()[:12]))
        assert rc == 0, (label, err)
        assert TRACE not in out + err, label
        if label in ("lybra next 项目扫描", "lybra needs-owner --lane web"):
            assert "冻结 " in out and "张未列" in out, (label, out[-400:])  # F141 口径不变: 冻结卡不列, 汇总行给张数
        _within(f"{label}({perf['fixture']['cards']} 卡 / {perf['fixture']['frozen']} 冻结)", seconds, limit)


def test_item3_one_pass_per_view_call_not_per_card(big, monkeypatch):
    """与机器快慢无关的回退防线: 一次视图调用内, 队列 frontmatter 索引只建一趟(每文件至多读一次)、冻结清单只重放一次、
    认领记录只数一趟——原实现逐卡各一趟(lybra 1000+ 卡 / chris 269 冻结 = 70 秒)。"""
    from tools.aipos_cli import autonomy_policy, legacy_baseline, task_loader

    calls = {"index": 0, "replay": 0, "claims": 0}
    orig_index, orig_replay, orig_claims = (task_loader._frontmatter_task_id, legacy_baseline._replay_manifest,
                                            autonomy_policy._preauthorized_claim_counts)

    def index(path):
        calls["index"] += 1
        return orig_index(path)

    def replay(root):
        calls["replay"] += 1
        return orig_replay(root)

    def claims(root):
        calls["claims"] += 1
        return orig_claims(root)

    monkeypatch.setattr(task_loader, "_frontmatter_task_id", index)
    monkeypatch.setattr(legacy_baseline, "_replay_manifest", replay)
    monkeypatch.setattr(autonomy_policy, "_preauthorized_claim_counts", claims)
    root = big["root"]
    queue_files = len(task_loader.iter_queue_task_paths(root))
    for label, argv in (("next", ["next", "--workspace-root", str(root)]),
                        ("loop status --task-id", ["loop", "status", "--task-id", big["ended_id"], "--workspace-root", str(root)]),
                        ("loop status", ["loop", "status", "--workspace-root", str(root)])):
        for key in calls:
            calls[key] = 0
        rc, out, err = _cli(argv)
        _show(f"件③ {label}: 队列文件 {queue_files} 个; 索引读卡 {calls['index']} 次, 冻结清单重放 {calls['replay']} 次, "
              f"认领记录计数 {calls['claims']} 趟")
        assert rc == 0, (label, err)
        assert calls["index"] <= queue_files, (label, calls)
        assert calls["replay"] <= 1 and calls["claims"] <= 1, (label, calls)


def test_item3_scope_memo_only_inside_read_scope(tmp_path, monkeypatch):
    """作用域外(门写动作 / 逐次调用)每次现算; 作用域内同根同键只算一次; 可重入 = 共用外层; 根不同 = 各自; compute 抛错不缓存。"""
    from tools.aipos_cli.task_loader import lookup_scope_memo, task_card_lookup_scope

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    n = {"v": 0}

    def compute():
        n["v"] += 1
        return n["v"]

    assert lookup_scope_memo(a, "k", compute) == 1 and lookup_scope_memo(a, "k", compute) == 2  # 作用域外: 每次现算
    with task_card_lookup_scope(a):
        assert lookup_scope_memo(a, "k", compute) == 3
        with task_card_lookup_scope(a):  # 可重入: 共用外层缓存
            assert lookup_scope_memo(a, "k", compute) == 3
        assert lookup_scope_memo(b, "k", compute) == 4  # 根不同: 不共用
    with task_card_lookup_scope(a):  # 新作用域: 不跨调用缓存
        assert lookup_scope_memo(a, "k", compute) == 5

    def boom():
        raise ValueError("清单坏")

    with task_card_lookup_scope(a):
        for _ in range(2):  # fail-closed: 每次都再抛, 不缓存成「无」
            with pytest.raises(ValueError):
                lookup_scope_memo(a, "bad", boom)


def test_item3_next_states_only_same_next_card_as_full_scan(big):
    """结案取下一张只扫 NEXT_CARD_STATES: 与全扫的 pick_next_card 结果相同(各 lane)。"""
    from tools.aipos_cli.next_resolver import NEXT_CARD_STATES, pick_next_card, scan_project

    assert NEXT_CARD_STATES == ("pending",)
    for lane in [None, *big["lanes"]]:
        full = pick_next_card(scan_project(big["root"], lane=lane))
        narrow = pick_next_card(scan_project(big["root"], lane=lane, next_states_only=True))
        assert (full or {}).get("task_id") == (narrow or {}).get("task_id"), lane
        assert full is not None and full["current_state"] in NEXT_CARD_STATES


# ===========================================================================
# 件② 去噪: sync / next / loop status 缺省无 [ENVELOPE_TRACE], --verbose 有; 判定与拒因不变
# ===========================================================================

def test_item2_sync_default_quiet_verbose_traces(rig):
    """F66B sync 靶场(临时工位父根 + 假门, 非真实工位): sync 按生效信封校正 role#owner_policy_ref 时逐谓词判信封。"""
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    gov = rig["gov_l"]
    _write(gov / "5_tasks" / "policies" / "pol_f142_sync.md", build_autonomy_policy_markdown(
        policy_id="pol_f142_sync", agent_or_role=EXEC, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=5, owner_approval_ref="f142-fixture", task_selector_task_mode="code"))
    argv = ["sync", "--harness-root", str(rig["parent"]), "--workspace-root", str(gov), "--json"]
    with _trace_sink() as quiet:
        rc0, out0, err0 = _cli(argv)
    with _trace_sink() as loud:
        rc1, out1, err1 = _cli([*argv, "--verbose"])
    trace_lines = [line for line in loud.getvalue().splitlines() if line.startswith(TRACE)]
    _show(f"件② lybra sync(缺省)exit {rc0}: 信封诊断 {len([l for l in quiet.getvalue().splitlines() if l.startswith(TRACE)])} 行; "
          f"stderr 含 {TRACE}: {TRACE in err0}")
    _show(f"件② lybra sync --verbose exit {rc1}: 信封诊断 {len(trace_lines)} 行, 首行原文:\n{trace_lines[0] if trace_lines else '(无)'}")
    assert rc0 == 0 and rc1 == 0, (err0, err1)
    assert TRACE not in quiet.getvalue() + out0 + err0
    assert trace_lines and all(json.loads(line.split(" ", 1)[1]).get("phase") == "match_claim_envelope" for line in trace_lines)
    corr0 = [w["result"]["owner_policy_correction"] for w in json.loads(out0)["workstations"] if w.get("status") == "synced"]
    assert corr0 and corr0[0]["derived"] == "pol_f142_sync"  # 判定结果不变: 信封照样匹配并校正


def test_item2_next_default_quiet_verbose_and_env_debug(big):
    root = str(big["root"])
    with _trace_sink() as quiet:
        rc0, out0, err0 = _cli(["next", "--workspace-root", root])
    with _trace_sink() as loud:
        rc1, out1, err1 = _cli(["next", "--workspace-root", root, "--verbose"])
    os.environ[_trace_decl()["debug_env"]] = "1"
    try:
        with _trace_sink() as env_on:
            rc2, out2, _err2 = _cli(["next", "--workspace-root", root])
    finally:
        os.environ.pop(_trace_decl()["debug_env"], None)
    count = lambda buf: len([l for l in buf.getvalue().splitlines() if l.startswith(TRACE)])  # noqa: E731
    _show(f"件② lybra next: 缺省 {count(quiet)} 行 / --verbose {count(loud)} 行 / {_trace_decl()['debug_env']}=1 {count(env_on)} 行")
    assert rc0 == rc1 == rc2 == 0 and out0 == out1 == out2  # 只开关调试轨迹, 推导结论一字不差
    assert count(quiet) == 0 and count(loud) > 0 and count(env_on) == count(loud)


def test_item2_single_switch_semantics_and_gate_keeps_trace():
    """唯一开关: 非 CLI 进程(门服务, 无 CLI 决定)= 声明 non_cli_default_enabled(留痕); CLI 缺省 = cli_default_enabled(关);
    in-process 嵌套 CLI 调用沿用外层; 判定结果与拒因原因链与开关无关。"""
    from tools.aipos_cli import autonomy_policy as ap

    decl = _trace_decl()
    assert decl["cli_default_enabled"] is False and decl["non_cli_default_enabled"] is True
    assert ap.envelope_trace_enabled() is True  # 测试进程未经 CLI 入口 = 门侧口径
    with ap.cli_envelope_trace(False):
        assert ap.envelope_trace_enabled() is False
        with ap.cli_envelope_trace(False):  # loop 驱动器 in-process 执行派生命令: 沿用外层
            assert ap.envelope_trace_enabled() is False
    with ap.cli_envelope_trace(True):
        with ap.cli_envelope_trace(False):
            assert ap.envelope_trace_enabled() is True
    assert ap.envelope_trace_enabled() is True  # 区段外恢复

    policy = {"policy_id": "pol_x", "mode": "PreAuthorized", "status": "active", "approved_by_owner": True,
              "active_from": "2026-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z", "agent_or_role": "someone-else",
              "task_selector_task_mode": "code", "max_tasks": 3}
    from datetime import datetime, timezone

    kw = dict(policy=policy, task_id="T-1", task_mode="code", project="p", agent_instance="me", actor="me",
              now=datetime(2026, 10, 10, tzinfo=timezone.utc), released_count=0)
    with _trace_sink() as loud:
        on = ap.match_claim_envelope(**kw)
    with ap.cli_envelope_trace(False), _trace_sink() as quiet:
        off = ap.match_claim_envelope(**kw)
    assert on == off and on[0] is False and "not covered" in on[1] and on[2] == ap.ENVELOPE_ERROR_AGENT_NOT_COVERED  # 拒因不丢
    assert TRACE in loud.getvalue() and quiet.getvalue() == ""


def test_item2_switch_declared_once_and_flags_mounted_from_declaration():
    """开关只在发射处一处: 无调用方改 logger.disabled / 自挂 --verbose; 声明所列命令都挂上同一 --verbose(dest=verbose)。"""
    import argparse

    from tools.aipos_cli.aipos_cli import build_parser

    decl = _trace_decl()
    sources = {p.name: p.read_text(encoding="utf-8") for p in (REPO_ROOT / "tools" / "aipos_cli").glob("*.py")}
    assert not [n for n, s in sources.items() if "_ENVELOPE_LOGGER.disabled" in s or 'logging.getLogger("lybra.envelope").disabled' in s]
    assert not [n for n, s in sources.items() if n != "autonomy_policy.py" and "envelope_trace_output(" in s]
    assert '"--verbose"' not in sources["aipos_cli.py"] and '"--verbose"' not in sources["loop_run_record.py"]
    assert sources["autonomy_policy.py"].count("def trace_envelope(") == 1
    parser = build_parser()
    for path in decl["verbose_commands"]:
        sub = parser
        for name in path.split():
            sub = next(a.choices[name] for a in sub._actions if isinstance(a, argparse._SubParsersAction) and name in a.choices)
        flags = [a for a in sub._actions if decl["verbose_flag"] in a.option_strings]
        assert len(flags) == 1 and flags[0].dest == "verbose", path
    assert "sync" in decl["verbose_commands"] and "next" in decl["verbose_commands"] and "queue claim" in decl["verbose_commands"]
