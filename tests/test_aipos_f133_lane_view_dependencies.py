"""AIPOS-F133 — lane 单源与按 lane 视图 + 依赖满足判据单源并用于「取下一张」。

靶场 = 临时治理根(自造人肉期样本: 项目 hbjfx, 两仓 web/api 各一 lane; 另单仓根)。禁真门/真治理根/真工位/真 pi。
复用 F78C 靶场骨架(_gov_skeleton / _product_repo)与 F78 卡片构造(_card), 禁第二份靶场。

件① lane 键唯一派生 machine_zone.card_lane_key(解析走 workspace_config.resolve_card_repo → repos.items 仓名; 单仓 = 仓路径)
件② 四命令(lybra next 项目扫描 / brief / loop status / needs-owner)同一 --lane 参数(verbs.schema lane_view)与同一过滤函数
    machine_zone.filter_rows_by_lane; 不带 --lane 时 brief 与 needs-owner 按 lane 分组(group_rows_by_lane)
件③ 依赖满足单源 task_complexity.dependencies_satisfied(全部依赖满足才真, 只读门生记录): 门 claim(queue_mutation)、
    validator(dependency_gate 级别)、推导核 pending→claim、scan_project「下一张可推进卡」共用
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write  # noqa: E402  — 靶场/替身唯一来源
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _card  # noqa: E402
from test_aipos_f78c_card_repo import _gov_skeleton, _product_repo  # noqa: E402
from tools.aipos_cli import machine_zone as mz  # noqa: E402
from tools.aipos_cli.task_complexity import dependencies_satisfied, unmet_dependencies  # noqa: E402

PROJECT = "hbjfx"  # 人肉期样本项目(非 lybra; 禁写死项目 ID 的反证)


def _show(line: str) -> None:
    sys.__stdout__.write(line + "\n")
    sys.__stdout__.flush()


def _cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


def _hcard(gov: Path, task_id: str, queue: str, *, lane_repo: str | None = None, **extra) -> Path:
    meta = {"project": PROJECT, **extra}
    if lane_repo is not None:
        meta["lane"] = json.dumps({"repo": lane_repo, "paths": ["src/"], "roles": ["executor"]})
    return _card(gov, task_id, queue, extra=meta)


def _closure(gov: Path, task_id: str) -> Path:
    """被依赖卡的门生结案记录(文件名前缀 = 声明 record_file_prefix("closures"))。"""
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix

    path = record_dir(gov, "closures", task_id) / f"{record_file_prefix('closures')}_{task_id}_20261008_000000_{DRIVER}.md"
    _write(path, _fm({"record_type": "closure_record", "task_id": task_id, "closed_at": "2026-10-08T00:00:00Z"}))
    return path


def _verdict(gov: Path, task_id: str, verdict: str, *, gate_born: bool = True) -> Path:
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix

    meta = {"task_id": task_id, "reviewed_task_id": task_id, "verdict": verdict}
    if gate_born:
        meta.update({"record_type": "audit_verdict", "verdict_id": f"verdict_{task_id}_{verdict}", "verdict_at": "2026-10-08T00:00:00Z"})
    path = record_dir(gov, "audit_verdicts", task_id) / f"{record_file_prefix('audit_verdicts')}_{task_id}_{verdict}_{len(verdict)}.md"
    _write(path, _fm(meta))
    return path


@pytest.fixture
def dual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Path]]:
    """两 lane(两仓)人肉期样本: repos={web, api}, default=web; 卡分布 web×2 / api×2 / 缺 lane.repo(=default web)/ 未声明仓。"""
    gov = _gov_skeleton(tmp_path / "gov-hbjfx", monkeypatch)
    for sub in ("stage_archive", "governance/decision_log"):  # lybra brief 的最小治理档骨架(F67 夹具同形)
        (gov / sub).mkdir(parents=True)
    repos = {"web": _product_repo(tmp_path / "repo-web"), "api": _product_repo(tmp_path / "repo-api")}
    _write(gov / "project.json", json.dumps({"project": PROJECT, "config_version": 1,
                                             "repos": {"default": "web", "items": {k: str(v) for k, v in repos.items()}}}))
    _hcard(gov, "HBJ-W1", "pending", lane_repo="web", priority="medium")
    _hcard(gov, "HBJ-W2", "pending", lane_repo="web", priority="critical", needs_owner=True)
    _hcard(gov, "HBJ-A1", "pending", lane_repo="api", priority="high")
    _hcard(gov, "HBJ-A2", "blocked", lane_repo="api", priority="low", needs_owner=True,
           blocked_by=DRIVER, blocked_at="2026-10-08T00:00:00Z", block_reason="fx")
    _hcard(gov, "HBJ-D1", "pending", priority="low")  # 缺 lane.repo → repos.default(web)
    _hcard(gov, "HBJ-X1", "pending", lane_repo="ghost", priority="high", needs_owner=True)  # 未声明仓 → lane 不可解析
    return gov, repos


# ===========================================================================
# 件① lane 键唯一派生
# ===========================================================================

def test_item1_card_lane_key_single_derivation(dual, tmp_path, monkeypatch):
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    gov, repos = dual
    keys = {}
    for tid in ("HBJ-W1", "HBJ-A1", "HBJ-D1", "HBJ-X1"):
        fm, _ = require_frontmatter(find_task_card(gov, tid)[0])
        keys[tid] = mz.lane_of_card(fm, gov)
    _show("件① lane_of_card: " + json.dumps(keys, ensure_ascii=False))
    assert keys["HBJ-W1"] == {"lane": "web", "lane_error": None}
    assert keys["HBJ-A1"] == {"lane": "api", "lane_error": None}
    assert keys["HBJ-D1"] == {"lane": "web", "lane_error": None}  # 缺 lane.repo = repos.default
    unresolved = mz.lane_view_declaration()["unresolved_lane"]
    assert keys["HBJ-X1"]["lane"] == unresolved and keys["HBJ-X1"]["lane_error"].startswith("LANE_REPO_UNDECLARED")
    # 仓名与绝对路径两种 --lane 写法归一到同一 lane 键; 未声明 lane = 拒(点名可选值)
    assert mz.resolve_lane_filter(gov, "api") == ["api"] == mz.resolve_lane_filter(gov, str(repos["api"]))  # F139: 规范键列表
    with pytest.raises(mz.LaneFilterInvalid) as exc:
        mz.resolve_lane_filter(gov, "ghost")
    _show(f"件① --lane ghost: {exc.value}")
    assert "['web', 'api']" in str(exc.value)

    # 单仓项目(无 repos 段): 唯一 lane = code_repo 路径
    single = _gov_skeleton(tmp_path / "gov-single", monkeypatch)
    repo = _product_repo(tmp_path / "repo-single")
    _write(single / "project.json", json.dumps({"project": PROJECT, "code_repo": str(repo), "config_version": 1}))
    assert mz.card_lane_key({"task_id": "S-1"}, single) == str(repo) == mz.resolve_lane_filter(single, str(repo))[0]


# ===========================================================================
# 件② 四命令 --lane 过滤 + 分组(原文)
# ===========================================================================

def test_item2_next_scan_lane_filter(dual):
    gov, _ = dual
    rc, out, err = _cli(["next", "--workspace-root", str(gov), "--lane", "api"])
    _show("件② lybra next --lane api:\n" + out)
    assert rc == 0, err
    listed, aside = out.split("未归 lane", 1)  # AIPOS-F141 件②: 不可解析 lane 不混入, 末尾单独一组(计数 + 卡号)
    assert "HBJ-A1" in listed and "HBJ-A2" in listed and "HBJ-X1" not in listed and aside.startswith(" 1 张") and "HBJ-X1" in aside
    assert "HBJ-W1" not in out and "HBJ-W2" not in out and "HBJ-D1" not in out
    rc, out_all, _ = _cli(["next", "--workspace-root", str(gov), "--json"])
    rows = json.loads(out_all)
    assert {r["task_id"]: r["lane"] for r in rows} == {
        "HBJ-W2": "web", "HBJ-A1": "api", "HBJ-W1": "web", "HBJ-D1": "web",
        "HBJ-X1": mz.lane_view_declaration()["unresolved_lane"], "HBJ-A2": "api"}
    rc, _, err = _cli(["next", "--workspace-root", str(gov), "--lane", "ghost"])
    assert rc == mz.lane_view_declaration()["invalid_lane_exit_code"] and "可选" in err


def test_item2_brief_groups_by_lane_and_filters(dual):
    gov, _ = dual
    rc, out, err = _cli(["brief", "--workspace-root", str(gov)])
    assert "【3. 当前在跑什么】" in out, (rc, out, err)
    section = out.split("【3. 当前在跑什么】", 1)[1].split("【4.", 1)[0]
    _show("件② lybra brief(无 --lane, 按 lane 分组)【3】:" + section)
    assert rc == 0, err
    assert "- web: pending=3 claimed=0 completed=0 blocked=0 withdrawn=0" in section
    assert "- api: pending=1 claimed=0 completed=0 blocked=1 withdrawn=0" in section
    assert f"- {mz.lane_view_declaration()['unresolved_lane']}: pending=1" in section
    assert "lane 不可解析: HBJ-X1: LANE_REPO_UNDECLARED" in section
    rc, out, err = _cli(["brief", "--workspace-root", str(gov), "--lane", "api", "--json"])
    queue = json.loads(out)["queue"]
    _show("件② lybra brief --lane api --json queue: " + json.dumps({k: queue[k] for k in ("pending", "blocked", "lane_filter")}
                                                                   | {"lanes": list(queue["lanes"])}, ensure_ascii=False))
    assert rc == 0, err
    # AIPOS-F141 件②: 只计 api; 不可解析 lane 的卡不混入, 单独成「未归 lane」组
    assert queue["lane_filter"] == ["api"] and queue["pending"] == 1 and queue["blocked"] == 1
    assert list(queue["lanes"]) == ["api"] and [u["task_id"] for u in queue["unresolved_lane"]] == ["HBJ-X1"]


def test_item2_needs_owner_groups_by_lane_and_filters(dual):
    gov, _ = dual
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner"])
    _show("件② lybra needs-owner(无 --lane, 按 lane 分组):\n" + out)
    assert rc == 0, err
    unresolved = mz.lane_view_declaration()["unresolved_lane"]
    assert out.index("== lane web") < out.index("HBJ-W2") < out.index("== lane api") < out.index("HBJ-A2") \
        < out.index(f"== lane {unresolved}") < out.index("HBJ-X1")
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner", "--lane", "web"])
    _show("件② lybra needs-owner --lane web:\n" + out)
    listed = out.split("未归 lane", 1)[0]  # AIPOS-F141 件②: 不可解析 lane 不混入, 末尾单独计数
    assert rc == 0 and "HBJ-W2" in listed and "HBJ-X1" not in listed and "HBJ-A2" not in out and "== lane" not in out
    assert "未归 lane 1 张" in out and "HBJ-X1" in out.split("未归 lane", 1)[1]
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner", "--json"])
    payload = json.loads(out)
    assert {k: sorted(v) for k, v in payload["lanes"].items()} == {"web": ["HBJ-W2"], "api": ["HBJ-A2"], unresolved: ["HBJ-X1"]}


def test_item2_loop_status_lane_filter(dual):
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    gov, _ = dual
    recorders = [LoopRunRecorder(gov, tid, driver=DRIVER, contract=load_loop_contract(), warn=lambda _m: None)
                 for tid in ("HBJ-W1", "HBJ-A1")]
    try:
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), "--lane", "api"])
        _show("件② lybra loop status --lane api:\n" + out)
        assert rc == 0, err
        assert "卡 HBJ-A1  lane api" in out and "HBJ-W1" not in out
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), "--json"])
        runs = json.loads(out)["runs"]
        assert sorted((r["task_id"], r["lane"]) for r in runs) == [("HBJ-A1", "api"), ("HBJ-W1", "web")]
        rc, _, err = _cli(["loop", "status", "--workspace-root", str(gov), "--lane", "ghost"])
        assert rc == mz.lane_view_declaration()["invalid_lane_exit_code"] and "可选" in err
    finally:
        for rec in recorders:
            rec.close_log()


def test_item2_lane_flag_declared_once_in_verbs_schema():
    from tools.schema_loader import load_schema

    decl = load_schema("verbs")["lane_view"]
    assert decl["commands"] == ["lybra next", "lybra brief", "lybra loop status", "lybra needs-owner"]
    assert load_schema("verbs")["verbs"]["lybra_loop_status"]["parameters"]["properties"]["lane"]["type"] == "array"  # F139: --lane 可重复(仓集合)
    src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    assert src.count("add_lane_argument(") == 4 and '"--lane"' not in src  # 四处同一声明投影, 不手写旗标


# ===========================================================================
# 件③ 依赖满足单源: A 依赖 B, B 未结案 → A 不可认领且不在「下一张」; B 结案 → A 成为下一张(原文)
# ===========================================================================

def test_item3_dependency_blocks_claim_and_next_until_closure(dual):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.next_resolver import derive_next_step, pick_next_card, scan_project
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    gov, _ = dual
    # A(critical, 依赖 B) 优先级高于 lane api 里其余 pending 卡; B 已在 claimed(在途, 未结案)
    _hcard(gov, "HBJ-A9", "pending", lane_repo="api", priority="critical", depends_on=["HBJ-B9"], task_mode="docs")
    _hcard(gov, "HBJ-B9", "claimed", lane_repo="api", priority="medium", task_mode="docs",
           claim_id="claim_HBJ-B9_20261008_000000_x", claimed_by=EXEC, claimed_at="2026-10-08T00:00:00Z",
           active_session_id="session_HBJ-B9_20261008_000000_x")
    fm_a = {"task_id": "HBJ-A9", "depends_on": ["HBJ-B9"]}
    assert unmet_dependencies(fm_a, gov) and not dependencies_satisfied(fm_a, gov)

    step = derive_next_step("HBJ-A9", gov)
    _show("件③ B 未结案 next --task-id A: " + json.dumps({k: step.get(k) for k in ("derivable", "current_state", "missing_records",
                                                                                   "action")}, ensure_ascii=False))
    assert step["derivable"] is False and step["action"]["type"] == "dependencies_unmet"
    claim = mutate_queue_task(gov, "claim", task_id="HBJ-A9", actor=EXEC, dry_run=False,
                              profiles=load_agent_profiles(gov), with_records=True)
    _show("件③ B 未结案 门 claim A: " + json.dumps({k: claim.get(k) for k in ("verdict", "wrote", "error_code", "blocking_reasons")},
                                                 ensure_ascii=False))
    assert claim.get("wrote") is not True and claim["error_code"] == "DEPENDENCY_UNMET"
    assert any(r.startswith("DEPENDENCY_UNMET: 依赖 HBJ-B9 未满足 closure") for r in claim["blocking_reasons"])
    assert (gov / "5_tasks" / "queue" / "pending" / "hbj-a9.md").is_file()
    rows = scan_project(gov, lane="api")
    nxt = pick_next_card(rows)
    _show(f"件③ B 未结案 下一张(lane api): {nxt and nxt['task_id']}")
    assert nxt is not None and nxt["task_id"] == "HBJ-A1"  # A 依赖未满足不入选, 取 lane 内可推导最高优先级
    rc, out, _ = _cli(["next", "--workspace-root", str(gov), "--lane", "api"])
    _show("件③ B 未结案 lybra next --lane api(首行):\n" + "\n".join(out.splitlines()[:4]))
    assert out.splitlines()[1].startswith("下一张可推进卡: HBJ-A1 lane=api priority=high")

    # B 结案(门生结案记录落盘)→ A 依赖满足: 可认领 + 成为下一张(critical 高于 A1 high)
    _closure(gov, "HBJ-B9")
    assert dependencies_satisfied(fm_a, gov)
    rows = scan_project(gov, lane="api")
    assert rows[0]["task_id"] == "HBJ-A9" and rows[0]["next_card"] is True
    rc, out, _ = _cli(["next", "--workspace-root", str(gov), "--lane", "api"])
    _show("件③ B 结案后 lybra next --lane api(首行):\n" + "\n".join(out.splitlines()[:4]))
    assert out.splitlines()[1].startswith("下一张可推进卡: HBJ-A9 lane=api priority=critical")
    claim = mutate_queue_task(gov, "claim", task_id="HBJ-A9", actor=EXEC, dry_run=False,
                              profiles=load_agent_profiles(gov), with_records=True)
    _show("件③ B 结案后 门 claim A: " + json.dumps({k: claim.get(k) for k in ("verdict", "wrote", "blocking_reasons")}, ensure_ascii=False))
    assert claim.get("wrote") is True and (gov / "5_tasks" / "queue" / "claimed" / "hbj-a9.md").is_file()


def test_item3_all_vs_any_semantics_and_records_only(dual):
    """全部/任一对照: 两依赖只一个 PASS = 不满足(原 audit_pass 任一 PASS 即放行); 卡面自报 PASS 不算(原无记录退回自报)。"""
    from tools.aipos_cli.task_complexity import validate_task_complexity

    gov, _ = dual
    fm = {"task_id": "HBJ-C1", "task_class": "complex", "planner_agent": DRIVER, "reviewer": "r.x.y", "audit_by": "a.x.y",
          "assigned_to": EXEC, "depends_on": ["HBJ-P1", "HBJ-P2"], "dependency_condition": "audit_pass",
          "dependency_audit_status": "PASS"}  # 卡面自报 PASS: 不再采信
    _verdict(gov, "HBJ-P1", "PASS")
    one_pass = validate_task_complexity(fm, enforce_dependency_gate=True, governance_root=gov)["blocking_reasons"]
    _show("件③ 两依赖一个 PASS(新=全部): " + json.dumps(one_pass, ensure_ascii=False))
    assert len(one_pass) == 1 and one_pass[0].startswith("DEPENDENCY_UNMET: 依赖 HBJ-P2 未满足 audit_pass")
    _verdict(gov, "HBJ-P2", "PASS", gate_born=False)  # 手写裁决不算门生
    assert validate_task_complexity(fm, enforce_dependency_gate=True, governance_root=gov)["blocking_reasons"] == one_pass
    _verdict(gov, "HBJ-P2", "FAIL")
    assert not dependencies_satisfied(fm, gov)
    _verdict(gov, "HBJ-P2", "PASS")
    assert validate_task_complexity(fm, enforce_dependency_gate=True, governance_root=gov)["blocking_reasons"] == []
    # 无治理根 = 读不到记录 = 拒(fail-closed), 不退回卡面自报
    no_root = validate_task_complexity(fm, enforce_dependency_gate=True)["blocking_reasons"]
    assert no_root and no_root[0].startswith("DEPENDENCY_UNVERIFIABLE")
    # 条件值域读声明; 缺 dependency_condition = 声明缺省(closure)
    assert unmet_dependencies({"depends_on": ["HBJ-P1"]}, gov)[0].startswith("DEPENDENCY_UNMET: 依赖 HBJ-P1 未满足 closure")
    bad = unmet_dependencies({"depends_on": ["HBJ-P1"], "dependency_condition": "owner_approved"}, gov)
    assert bad[0].startswith("DEPENDENCY_CONDITION_INVALID")
    # 无依赖 = 满足
    assert dependencies_satisfied({"task_id": "Z"}, gov) and dependencies_satisfied({"task_id": "Z"}, None)


def test_item3_withdraw_not_gated_by_dependencies(dual):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    gov, _ = dual
    _hcard(gov, "HBJ-A8", "pending", lane_repo="api", depends_on=["HBJ-NOPE"], task_mode="docs")
    res = mutate_queue_task(gov, "withdraw", task_id="HBJ-A8", actor=DRIVER, reason="fx", dry_run=True,
                            profiles=load_agent_profiles(gov))
    assert not [r for r in res["blocking_reasons"] if "DEPENDENCY" in r], res["blocking_reasons"]
