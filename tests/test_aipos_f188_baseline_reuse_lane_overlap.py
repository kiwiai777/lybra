"""AIPOS-F188 — 执行体复用 main 侧合并后回归基线(regression baseline 三态 + kickoff 验证节)+ 同仓车道重叠串行提示。

靶场全部临时目录(临时治理根 + 临时产品仓 + 本用例自起的夹具进程); 不碰真实治理根、真实工位、生产门与 pi。

件① `lybra regression baseline` 三态: recorded(可复用)/ pending(该 sha 合并后回归在跑: 开始时刻 / 时限 / 预计最迟完成 / --wait 命令)/
   self_run_required(无记录 / 记录不可用 / 进程已不在); `--wait <秒>` 有界等待: 回归进程落结论 = recorded(concluded), 到点仍在跑 =
   self_run_required(still_running), 进程不落结论就退出 = self_run_required(process_gone); 退出码与 --json 字段读 verbs.schema 声明。
件② `lybra kickoff` 验证节: 本分支一侧必跑全量, main 侧先查 regression baseline(命令同一渲染); 开工稿生成时的三态现状一行
   (措辞读 verbs.schema lybra_kickoff.texts.baseline_state_<态>, 判定只引用 audit_baseline); 执行体 / 审计体章程口径同步。
件③ 车道重叠: draft publish 与 loop 起跑(认领前)对在途卡同仓且 lane.paths 前缀相交给警告(只提示不拒, verdict 不变); 豁免前缀
   (缺省 tests/ docs/)不报; 不重叠不报; loop status 显示; 运行记录 lane_overlaps; 判定一个函数(发卡与 loop 共用)。
"""
from __future__ import annotations

import io
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f90_loop_one_stage import AUDIT, POLICY, TASK, rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _git, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f95_harness_launch import _land as _land_ws, _workstation  # noqa: E402
from test_aipos_f149_test_audit_hygiene import _cli as _subcli, _range, _record  # noqa: E402  — 三态靶场与记录 writer 同源
from test_aipos_f187_kickoff_worktree_cleanup import _cli, _land_subagent, _update_project  # noqa: E402
from tools.aipos_cli import post_merge_regression as pmr  # noqa: E402
from tools.aipos_cli.loop_run_record import process_fingerprint  # noqa: E402

BASELINE_VERB = "lybra_regression_baseline"
OTHER = "PROBE-F188-OTH"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _sleeper(seconds: float, *, then_copy: tuple[Path, Path] | None = None) -> subprocess.Popen:
    """本用例自起的「合并后回归进程」替身: 睡 seconds 秒; then_copy=(结论记录源, 进行中记录) = 醒来把结论写进同一记录后退出(与真实
    async 回归「结论落定时同一记录更新」同形)。调用方 finally 里 kill + wait(只清本用例自起的进程)。"""
    code = "import shutil, sys, time\ntime.sleep(float(sys.argv[1]))\nif len(sys.argv) > 2:\n    shutil.copyfile(sys.argv[2], sys.argv[3])\n"
    argv = [sys.executable, "-c", code, str(seconds)] + ([str(p) for p in then_copy] if then_copy else [])
    return subprocess.Popen(argv)


def _reap(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _running(gov: Path, task: str, name: str, sha: str, pid: int, *, started_at: str = "2026-10-18T08:28:36Z") -> Path:
    _record(gov, "events", task, name, {"task_id": task, "merge_commit": sha, "status": "running", "execution": "async",
                                        "timeout_seconds": 1500, "async_pid": pid, "host": socket.gethostname(),
                                        "started_at": started_at, "process_fingerprint": process_fingerprint(pid),
                                        "async_log": f"/fixture/{task}.log"})
    return gov / "5_tasks" / "records" / "events" / task / name


def _concluded_text(gov: Path, task: str, sha: str, failures: list[str]) -> str:
    from tools.aipos_cli.record_writer import render_markdown

    meta = {"record_type": "task_progress_event", "post_merge_regression": {
        "task_id": task, "merge_commit": sha, "status": "pass", "execution": "async", "merged_complete": True,
        "merged_failures": failures, "merged_seconds": 3.0, "runall_path": "tests/run-all.sh"}}
    return render_markdown(meta, "# rig\n", ["record_type", "post_merge_regression"])


# ===========================================================================
# 件① regression baseline 三态 + --wait
# ===========================================================================

def test_item1_three_states_raw_and_json(tmp_path):
    gov, _repo, head = _range(tmp_path)
    base = ["--workspace-root", str(gov), "regression", "baseline", "--sha", head]
    # 无记录
    res = _subcli(base)
    _show(f"---- 件① 无记录 原文 rc={res.returncode} ----\n{res.stdout}")
    assert res.returncode == 0 and "结论: 须自跑 main 侧" in res.stdout and f"无 merge_commit = {head}" in res.stdout
    # 在跑(进程在)= pending
    proc = _sleeper(120)
    try:
        record = _running(gov, "PRJ-1", "post_merge_regression_PRJ-1_1.md", head, proc.pid)
        res = _subcli(base)
        _show(f"---- 件① 回归在跑 原文 rc={res.returncode} ----\n{res.stdout}")
        assert res.returncode == 0 and "结论: 合并后回归在跑(该 sha 的记录基线尚未落定, 可等)" in res.stdout
        assert "开始 2026-10-18T08:28:36Z, 时限 1500s, 预计最迟 2026-10-18T08:53:36Z 完成" in res.stdout
        assert f"--sha {head} --wait <秒>` 有界等待——到点仍未完成 = 须自跑 main 侧" in res.stdout
        js = json.loads(_subcli(base + ["--json"]).stdout)
        assert js["status"] == pmr.AUDIT_BASELINE_PENDING and js["pending"]["pid"] == proc.pid
        assert js["pending"]["deadline"] == "2026-10-18T08:53:36Z" and js["pending"]["timeout_seconds"] == 1500
        assert js["pending"]["record"] == "5_tasks/records/events/PRJ-1/post_merge_regression_PRJ-1_1.md"
        assert js["wait_command"].endswith(f"regression baseline --sha {head} --wait <秒>")
    finally:
        _reap(proc)
    # 进程已不在 = incomplete → self_run_required(原因写明, 给补跑命令)
    js = json.loads(_subcli(base + ["--json"]).stdout)
    _show("---- 件① 进行中记录而进程已不在(--json reasons)----\n" + "\n".join(js["reasons"]))
    assert js["status"] == pmr.AUDIT_BASELINE_SELF_RUN
    assert any("不在跑(incomplete)" in r and "补跑: lybra regression run --sha" in r for r in js["reasons"])
    # 落了完整结论 = recorded
    record.write_text(_concluded_text(gov, "PRJ-1", head, ["tests/test_x.py"]), encoding="utf-8")
    res = _subcli(base)
    _show(f"---- 件① 已记录 原文 rc={res.returncode} ----\n{res.stdout}")
    assert res.returncode == 0 and "结论: 已记录, 可复用" in res.stdout and "tests/test_x.py" in res.stdout


def test_item1_wait_concluded_still_running_and_process_gone(tmp_path):
    gov, _repo, head = _range(tmp_path)
    base = ["--workspace-root", str(gov), "regression", "baseline", "--sha", head]
    # ① 等待中回归进程落结论 = recorded(concluded)
    src = tmp_path / "concluded.md"
    src.write_text(_concluded_text(gov, "PRJ-2", head, []), encoding="utf-8")
    record = gov / "5_tasks" / "records" / "events" / "PRJ-2" / "post_merge_regression_PRJ-2_1.md"
    proc = _sleeper(1.5, then_copy=(src, record))
    try:
        _running(gov, "PRJ-2", record.name, head, proc.pid)
        res = _subcli(base + ["--wait", "20"])
        _show(f"---- 件① --wait 20(等待中落结论)原文 rc={res.returncode} ----\n{res.stdout}")
        assert res.returncode == 0 and "结论: 已记录, 可复用" in res.stdout and "(--wait 20s: concluded, 实等" in res.stdout
    finally:
        _reap(proc)
    # ② 到点仍在跑 = self_run_required(still_running)
    gov2, _r2, head2 = _range(tmp_path, "rangetwo")
    proc = _sleeper(120)
    try:
        _running(gov2, "PRJ-3", "post_merge_regression_PRJ-3_1.md", head2, proc.pid)
        res = _subcli(["--workspace-root", str(gov2), "regression", "baseline", "--sha", head2, "--wait", "1"])
        _show(f"---- 件① --wait 1(到点仍在跑)原文 rc={res.returncode} ----\n{res.stdout}")
        assert res.returncode == 0 and "结论: 须自跑 main 侧" in res.stdout and "--wait 1s 到点仍在跑" in res.stdout
        assert "(--wait 1s: still_running" in res.stdout
        js = json.loads(_subcli(["--workspace-root", str(gov2), "regression", "baseline", "--sha", head2, "--wait", "1", "--json"]).stdout)
        assert js["status"] == pmr.AUDIT_BASELINE_SELF_RUN and js["waited"]["outcome"] == "still_running" and "pending" not in js
    finally:
        _reap(proc)
    # ③ 回归进程不落结论就退出 = 醒来 self_run_required(process_gone)
    gov3, _r3, head3 = _range(tmp_path, "rangethree")
    proc = _sleeper(1.0)
    try:
        _running(gov3, "PRJ-4", "post_merge_regression_PRJ-4_1.md", head3, proc.pid)
        js = json.loads(_subcli(["--workspace-root", str(gov3), "regression", "baseline", "--sha", head3, "--wait", "20", "--json"]).stdout)
        _show(f"[件① --wait 20(进程不落结论退出)] status={js['status']} waited={js['waited']}")
        assert js["status"] == pmr.AUDIT_BASELINE_SELF_RUN and js["waited"]["outcome"] == "process_gone" and js["waited"]["waited_seconds"] < 20
    finally:
        _reap(proc)
    # 非 pending 不等待; 越界 = 退出码 failed
    js = json.loads(_subcli(["--workspace-root", str(gov3), "regression", "baseline", "--sha", head3, "--wait", "5", "--json"]).stdout)
    assert js["waited"] == {"requested_seconds": 5.0, "waited_seconds": 0, "outcome": "not_pending"}
    res = _subcli(["--workspace-root", str(gov3), "regression", "baseline", "--sha", head3, "--wait", "99999"])
    _show(f"[件① --wait 越界] rc={res.returncode} {res.stderr.strip()}")
    assert res.returncode == pmr.baseline_declaration()["exit_codes"]["failed"]["code"] and "wait.max_seconds" in res.stderr


def test_item1_declaration_and_single_judgement():
    from tools.schema_loader import load_schema

    decl = load_schema("verbs")["verbs"][BASELINE_VERB]
    assert set(decl["states"]) == {"recorded", "pending", "self_run_required"} and decl["cli_command"] == "lybra regression baseline"
    assert {k: v["code"] for k, v in decl["exit_codes"].items()} == {"ok": 0, "failed": 1, "usage": 2}
    assert set(decl["json_fields"]) >= {"common", "recorded", "pending", "self_run_required", "wait"}
    src = (REPO_ROOT / "tools" / "aipos_cli" / "post_merge_regression.py").read_text(encoding="utf-8")
    assert src.count("def audit_baseline(") == 1 and "regression_states(Path(governance_root), sha=commit)" in src
    # 等待只经唯一等待原语(run_fs_watch), 不进 finalize 合并后预载闭包
    from tools.aipos_cli.finalize import _post_merge_import_closure

    closure = _post_merge_import_closure()
    assert "tools.aipos_cli.loop_driver" not in closure and "tools.aipos_cli.agent_watch_fs" not in closure
    rec = (REPO_ROOT / "tools" / "aipos_cli" / "loop_run_record.py").read_text(encoding="utf-8")
    body = rec.split("def wait_regression_baseline(")[1].split("\ndef ")[0]
    assert "run_fs_watch(" in body and "time.sleep" not in body


# ===========================================================================
# 件② kickoff 验证节三态 + 章程口径
# ===========================================================================

def _shape(rig, tmp_path: Path, *, other_lane: list[str] | None = None) -> None:
    """F187 lybra 形(子 agent 执行者 + pi 审计工位 + run-all 声明), 另可加一张同仓在途卡(件③ loop 起跑提示)。"""
    gov = rig.gov
    _land_subagent(gov, EXEC, project="lybra", harness="claude-code", host=socket.gethostname())
    ws = _workstation(rig.home, gov, "probe-auditor", "auditor", AUDITOR)
    _land_ws(gov, AUDITOR, "auditor", ws)
    _update_project(gov, paths={"scratch_root": str(tmp_path / "scratch-root")}, test_contract={"runall_path": "tests/run-all.sh"})
    _write(rig.code_repo / "tests" / "run-all.sh", "#!/usr/bin/env bash\n# lybra-runall: discover\n")
    _git(rig.code_repo, "add", "tests/run-all.sh")
    _git(rig.code_repo, "commit", "-q", "-m", "run-all")
    f90._card(gov, TASK, "pending", lane={"repo": str(rig.code_repo), "paths": ["tests/", "tools/"]})
    if other_lane is not None:
        f90._card(gov, OTHER, "claimed", lane={"repo": str(rig.code_repo), "paths": other_lane})
    init_governance_repo(gov)


def _verify_section(text: str) -> str:
    return text.split("## 验证", 1)[1].split("## 交回", 1)[0]


def test_item2_kickoff_verify_three_states_executor_and_auditor(rig, tmp_path):
    from tools.aipos_cli.loop_driver import run_loop

    _shape(rig, tmp_path)
    gov, product = rig.gov, rig.code_repo
    seen: dict = {}
    events = gov / "5_tasks" / "records" / "events" / "BASE-1"

    def watch(args, expect_ready):
        patterns = " ".join(args.expect)
        head = _git(product, "rev-parse", "main")
        if f"/{AUDIT}/" in patterns or patterns.startswith(f"task_cards/{AUDIT}"):
            _record(gov, "events", "BASE-1", "post_merge_regression_BASE-1_1.md",
                    {"task_id": "BASE-1", "merge_commit": head, "status": "pass", "merged_complete": True,
                     "merged_failures": ["tests/test_legacy.py"]})
            seen["auditor"] = _cli("kickoff", "--task-id", AUDIT, "--workspace-root", str(gov))
            for p in events.glob("*.md"):
                p.unlink()
            f90._auditor_work(rig)
        else:
            seen["self_run"] = _cli("kickoff", "--task-id", TASK, "--workspace-root", str(gov))
            proc = _sleeper(120)
            try:
                _running(gov, "BASE-1", "post_merge_regression_BASE-1_1.md", head, proc.pid)
                seen["pending"] = _cli("kickoff", "--task-id", TASK, "--workspace-root", str(gov))
            finally:
                _reap(proc)
            (events / "post_merge_regression_BASE-1_1.md").write_text(_concluded_text(gov, "BASE-1", head, []), encoding="utf-8")
            seen["recorded"] = _cli("kickoff", "--task-id", TASK, "--workspace-root", str(gov))
            seen["json"] = _cli("kickoff", "--task-id", TASK, "--workspace-root", str(gov), "--json")
            for p in events.glob("*.md"):
                p.unlink()
            seen["head"] = head
            f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    res = run_loop(TASK, gov, actor=DRIVER, policy_id=POLICY, out=io.StringIO(), watch=watch, interval=0.05, max_wait=5, max_steps=30)
    assert res.exit_code == 0 and res.outcome == "completed", res.message
    head = seen["head"]
    cmd = f"`lybra --workspace-root {gov} regression baseline --repo-root {product}`"
    rule = (f"- 两边基线: 本分支最终 tip 必须独立跑一次全量, 附计数 grep(✓ / ✗ 行数); main 侧先查合并后回归记录基线 {cmd}(三态, 只读): "
            "已记录, 可复用 = 直接引用该记录(报告写记录路径与失败集合), 不自跑; 合并后回归在跑 = 先做卡, 交回前再查一次")  # AIPOS-F212: 标签 = state_labels
    expect = {
        "self_run": f"  main 侧基线现状(开工稿生成时查, sha {head}): 须自跑(无 merge_commit = {head} 的合并后回归记录",
        "pending": (f"  main 侧基线现状(开工稿生成时查, sha {head}): 合并后回归在跑(卡 BASE-1 起于 2026-10-18T08:28:36Z, 时限 1500s, "
                    "预计最迟 2026-10-18T08:53:36Z 完成)——先做卡, 交回前再查(或加 --wait <秒>)"),
        "recorded": (f"  main 侧基线现状(开工稿生成时查, sha {head}): 已记录, 可复用(记录 5_tasks/records/events/BASE-1/"
                     "post_merge_regression_BASE-1_1.md; 失败集合 0 条)——main 侧直接引用该记录, 不自跑"),
    }
    for state, needle in expect.items():
        rc, brief, err = seen[state]
        section = _verify_section(brief)
        _show(f"---- 件② kickoff 验证节({state})原文 ----\n{section.strip()}")
        assert rc == 0, err
        assert rule in section and needle in section, (state, section)
    data = json.loads(seen["json"][1])
    assert data["sources"]["baseline_status"] == "recorded" and data["values"]["baseline_sha"] == head
    rc, abrief, err = seen["auditor"]
    section = _verify_section(abrief)
    _show(f"---- 件② 审计体 kickoff 验证节(已记录)原文 ----\n{section.strip()}")
    assert rc == 0, err
    assert "- 独立全量基线: 被审分支 tip(取证工作树)必须独立跑一次全量" in section and cmd in section
    assert "已记录, 可复用(记录 5_tasks/records/events/BASE-1/post_merge_regression_BASE-1_1.md; 失败集合 1 条)" in section


def test_item2_charters_and_texts_single_source(tmp_path, monkeypatch):
    from test_aipos_f99_audit_runall_baseline import AUDITOR_MASTER, _auditor_ctx, _baseline_section, _make_gov
    from tools.aipos_cli.charter_render import render_charter
    from tools.aipos_cli.subagent_kickoff import kickoff_contract

    gov = _make_gov(tmp_path, monkeypatch, project="otherproj", test_contract={"runall_path": "ci/all.sh"})
    ctx = _auditor_ctx(tmp_path, gov, "otherproj")
    section = _baseline_section(render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), ctx))
    assert "输出「合并后回归在跑」" in section and "`--wait <秒>`" in section and f"`{pmr.audit_baseline_command(gov)}`" in section
    exec_rendered = render_charter((REPO_ROOT / "agents" / "roles" / "executor" / "AGENTS.md").read_text(encoding="utf-8"), ctx)
    bullet = exec_rendered.split("- **两边基线(AIPOS-F188)**", 1)[1].split("\n- **", 1)[0]
    _show(f"---- 件② 执行体章程渲染(他项目形)·两边基线条 ----\n- **两边基线(AIPOS-F188)**{bullet}")
    for needle in (f"`{pmr.audit_baseline_command(gov)}`", "「已记录, 可复用」", "「合并后回归在跑」", "「须自跑」", "**不手翻记录文件**",
                   "**必须自己完整跑一次全量**", "git merge main"):
        assert needle in bullet, needle
    texts = kickoff_contract()["texts"]
    assert {"baseline_state_recorded", "baseline_state_pending", "baseline_state_self_run_required", "baseline_state_unavailable"} <= set(texts)
    src = (REPO_ROOT / "tools" / "aipos_cli" / "subagent_kickoff.py").read_text(encoding="utf-8")
    for phrase in ("已记录, 可复用", "回归在跑", "须自跑("):
        assert phrase not in src, phrase  # 措辞只在声明
    assert "pmr.audit_baseline(" in src and "lookup_recorded_baseline" not in src  # 只引用唯一判定


# ===========================================================================
# 件③ 车道重叠: publish / loop 起跑 / loop status / 豁免 / 不重叠
# ===========================================================================

def _overlap_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    code = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    lane = lambda *paths: {"lane": {"repo": str(code), "paths": list(paths)}}  # noqa: E731
    f78._card(gov, "OTH-1", "claimed", extra=lane("tools/aipos_cli/cli_cmds/", "tests/"))    # 代码段重叠 + tests/(豁免)
    f78._card(gov, "OTH-2", "claimed", extra=lane("docs/", "tests/"))                         # 只在豁免前缀重叠
    f78._card(gov, "OTH-3", "claimed", extra=lane("web/"))                                    # 不重叠
    f78._card(gov, "OTH-4", "pending", extra=lane("tools/"))                                  # 未在途(pending)
    f78._card(gov, "OTH-5", "completed", extra=lane("tools/"))                                # 已结案
    return gov, code


def _publish(gov: Path, task_id: str, output_target: str) -> dict:
    from tools.aipos_cli.draft_writer import create_draft, publish_draft

    meta = {"task_id": task_id, "title": "t", "project": "lybra", "assigned_to": EXEC, "context_bundle": "t", "task_mode": "code",
            "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False, "output_target": output_target,
            "artifact_policy": "formal_write"}
    created = create_draft(gov, meta, "## Goal\n\nx\n", dry_run=False)
    assert created["verdict"] in ("PASS", "WARN"), created
    return publish_draft(gov, created["target_path"], dry_run=True)


def test_item3_publish_warns_overlap_only_inflight_non_exempt(tmp_path, monkeypatch):
    from tools.aipos_cli.renderer import render_draft_result_text

    gov, code = _overlap_gov(tmp_path, monkeypatch)
    res = _publish(gov, "NEW-1", "tools/aipos_cli/, tests/, docs/")
    lines = [w for w in res["warnings"] if "车道重叠" in w]
    _show("---- 件③ draft publish --dry-run 原文(Warnings 段)----\n"
          + "\n".join(l for l in render_draft_result_text(res).splitlines() if l.startswith(("Verdict", "Warnings", "- 车道")) ))
    assert res["verdict"] != "BLOCK" and [o["other"] for o in res["lane_overlap"]["overlaps"]] == ["OTH-1"]
    assert lines == [f"车道重叠: 本卡 NEW-1 与在途卡 OTH-1 (claimed) 同仓 {code.resolve()} 重叠路径 tools/aipos_cli/cli_cmds/——建议: "
                     "等 OTH-1 合入后再起跑; 或起跑但交回前先 git merge main 解冲突再做最终基线"]
    assert res["lane_overlap"]["exempt"] == {"exempt_path_prefixes": ["tests/", "docs/"], "source": "default"}
    baseline_verdict = _publish(gov, "NEW-0", "web2/")
    _show(f"[件③ 不重叠] warnings 中车道重叠行 = {[w for w in baseline_verdict['warnings'] if '车道重叠' in w]}")
    assert not baseline_verdict["lane_overlap"]["overlaps"] and baseline_verdict["verdict"] == res["verdict"]  # 只提示不改 verdict
    # 只在豁免前缀重叠 = 不报
    only_exempt = _publish(gov, "NEW-2", "tests/, docs/")
    _show(f"[件③ 豁免路径不报] 重叠={only_exempt['lane_overlap']['overlaps']} 豁免={only_exempt['lane_overlap']['exempt']}")
    assert only_exempt["lane_overlap"]["overlaps"] == []
    # 项目声明空豁免 = tests/ docs/ 也报; 形坏 = 一行点名, 不影响发布判定
    _update_project(gov, lane_overlap={"exempt_path_prefixes": []})
    noexempt = _publish(gov, "NEW-3", "tests/, docs/")
    assert sorted(o["other"] for o in noexempt["lane_overlap"]["overlaps"]) == ["OTH-1", "OTH-2"]
    assert [o["paths"] for o in noexempt["lane_overlap"]["overlaps"] if o["other"] == "OTH-2"] == [["tests/", "docs/"]]
    _update_project(gov, lane_overlap={"exempt_path_prefixes": "tests/"})
    bad = _publish(gov, "NEW-4", "tools/")
    _show(f"[件③ 豁免声明形坏] {bad['lane_overlap']['notes']}")
    assert bad["verdict"] != "BLOCK" and bad["lane_overlap"]["notes"][0].startswith("车道重叠提示未判: LANE_OVERLAP_INVALID")


def test_item3_loop_start_warns_records_and_status_shows(rig, tmp_path):
    from tools.aipos_cli.loop_driver import run_loop
    from tools.aipos_cli.loop_run_record import loop_runs_dir

    _shape(rig, tmp_path, other_lane=["tools/aipos_cli/", "tests/"])
    gov = rig.gov
    seen: dict = {}

    def watch(args, expect_ready):
        patterns = " ".join(args.expect)
        if f"/{AUDIT}/" in patterns or patterns.startswith(f"task_cards/{AUDIT}"):
            f90._auditor_work(rig)
        else:
            seen["status"] = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(gov))
            f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, policy_id=POLICY, out=out, watch=watch, interval=0.05, max_wait=5, max_steps=30)
    text = out.getvalue()
    assert res.exit_code == 0 and res.outcome == "completed", text
    lines = [ln for ln in text.splitlines() if "lane_overlap:" in ln]
    claim_at = next(i for i, ln in enumerate(text.splitlines()) if " run claim @ " in ln)
    _show("---- 件③ loop 起跑(认领前)警告原文 ----\n" + "\n".join(lines))
    want = (f"lane_overlap: 车道重叠: 本卡 {TASK} 与在途卡 {OTHER} (claimed) 同仓 {rig.code_repo.resolve()} 重叠路径 tools/aipos_cli/——建议: "
            f"等 {OTHER} 合入后再起跑; 或起跑但交回前先 git merge main 解冲突再做最终基线")
    assert len(lines) == 1 and want in lines[0] and text.splitlines().index(lines[0]) < claim_at  # tests/ 豁免不报; 认领前
    meta = f90.nr._read_frontmatter(sorted(loop_runs_dir(gov, TASK).glob("looprun_*.md"))[-1])
    assert [(o["card"], o["other"], o["paths"]) for o in meta["lane_overlaps"]] == [(TASK, OTHER, ["tools/aipos_cli/"])]
    rc, status, _err = seen["status"]
    shown = [ln for ln in status.splitlines() if "车道重叠" in ln]
    _show("---- 件③ loop status 显示原文 ----\n" + "\n".join(shown))
    assert rc in (0, 3) and shown and shown[0].strip().startswith(f"起跑时 车道重叠: 本卡 {TASK} 与在途卡 {OTHER}")


def test_item3_loop_no_overlap_no_line(rig, tmp_path):
    from tools.aipos_cli.loop_driver import run_loop

    _shape(rig, tmp_path, other_lane=["web/", "tests/"])  # 只在豁免前缀 tests/ 与本卡相交
    out = io.StringIO()

    def watch(args, expect_ready):
        if AUDIT in " ".join(args.expect):
            f90._auditor_work(rig)
        else:
            f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=watch, interval=0.05, max_wait=5, max_steps=30)
    assert res.exit_code == 0, out.getvalue()
    _show(f"[件③ loop 不重叠 / 只豁免路径相交] lane_overlap 行数 = {out.getvalue().count('lane_overlap:')}")
    assert "lane_overlap:" not in out.getvalue()


def test_item3_single_judgement_and_declarations():
    from tools.schema_loader import load_schema

    call = re.compile(r"(?<![\w.])lane_overlaps\(")
    hits = {p.name for p in (REPO_ROOT / "tools").rglob("*.py") if "def lane_overlaps(" in p.read_text(encoding="utf-8")}
    assert hits == {"lane_overlap.py"}
    callers = {p.name for p in (REPO_ROOT / "tools").rglob("*.py") if call.search(p.read_text(encoding="utf-8"))} - {"lane_overlap.py"}
    assert callers == {"draft_writer.py", "loop_driver.py"}  # 发卡与 loop 共用一个函数
    verbs = load_schema("verbs")
    assert verbs["lane_overlap"]["inflight_states"] == ["claimed"] and "lane_overlaps" in verbs["verbs"]["lybra_loop"]["run_record"]
    decl = load_schema("config")["configuration_sources"]["project_json"]["schema"]["lane_overlap"]
    assert decl["schema"]["exempt_path_prefixes"]["default"] == ["tests/", "docs/"] and decl["schema"]["exempt_path_prefixes"]["default_reason"]
    src = (REPO_ROOT / "tools" / "aipos_cli" / "lane_overlap.py").read_text(encoding="utf-8")
    assert "合入后再起跑" not in src and "未判:" not in src  # 文案只在声明(texts.warning / texts.invalid)


def test_fixture_registered_in_runall():
    from tools.aipos_cli.runall_discovery import repo_files
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    found = discover_test_files(repo_files(REPO_ROOT), {**default_test_contract(), "runall_path": "tests/run-all.sh"})
    assert "tests/test_aipos_f188_baseline_reuse_lane_overlap.py" in found
