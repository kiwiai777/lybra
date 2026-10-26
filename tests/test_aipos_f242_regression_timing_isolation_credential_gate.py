"""AIPOS-F242 — 收尾补漏: 合并后回归两跑各给足时限 / F225 第③项用例与本机部署状态和时序无关 / 凭据判据下沉门侧 / 章程事前提示。

依据(10-26/27 lybra 本机实测):
① AIPOS-F240 合并后回归: 合并后一跑 1698s 全过, 随后「合并前基线一跑(无记录基线)」只拿到单次时限 2251s 的余量 553s 就被终止(status=timeout)。
② AIPOS-F238 合并后回归记 F225 第③项新增失败; 部署快照 7d89f39 / e202849 / 0206b8d / b6f3c1f 单跑该用例都在第 309 行红(auto_reaudit.eligible 为 True)。
③ AIPOS-F239 凭据判据只在产物入口(artifact ingest), 门 MCP 直调交回 / 裁决不经此判; 章程无「报告不写明文凭据」事前提示。

件① 合并后一跑与合并前基线一跑各用单次全量时限(full_run_timeout 唯一推导), 记录分列 merged_timeout / baseline_timeout / total_timeout_seconds
    与依据; 基线已有记录 = 只跑一跑(行为不变); 进行中记录的预计最迟完成 / 排队等待上限按总上限。
件② 根因两条(均为夹具读到本机真实状态, 产品判定本身按声明读本进程运行代码, 不改):
    (a) loop status / 收件箱第二档判定现值缺省读本进程运行代码(next_resolver.current_env → DeployGuard.for_running_process →
        deploy_runtime.running_code): 从 .deploy/releases/<名> 跑 = 该快照 VERSION 提交 ≠ 夹具提交 → 判「部署已变」(第 309 行红);
    (b) 放行记录 released_at 与下一轮裁决 verdict_at 都是秒精度, 夹具跑得快常落同一秒, 界判据「裁决严格晚于放行」→ 新裁决被算在界前 →
        推导核又派 R4 → 夹具 watch 走执行体分支 git commit 无改动退出 1(F238 合并后回归那一红, 本机单跑约 1/4 概率)。
    修法 = 夹具钉死: f202.pin_host_deploy(注入部署身份)+ f202.separate_auto_release_second(注入时钟)。
件③ 门侧交回 / 裁决写入唯一口(board_adapter._build_return_preview / _build_audit_verdict_preview)同核 credential_scan(经
    artifact_ingest.gate_credential_blocking), CLI 与 MCP 直调同码同文, 拒因不回显值; 随请求提交的 result_summary / return_body /
    findings_summary 同扫。
件④ 执行 / 审计章程母本占位 {{credential_rule}} = artifact_ingest.credential_charter_rule(文案唯一声明 credential_guard.texts.charter_rule)。

靶场 = 临时 git 仓 / 临时治理根 / F90 进程内门(禁真门 / 真治理根 / 真工位 / 真 pi / 真部署目录)。夹具「凭据」= 运行时拼出的明显假值。
"""
from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f162_runtime_evidence_supplementary_audit as f162  # noqa: E402  — 审计报告助手唯一来源
import test_aipos_f202_owner_reaudit as f202  # noqa: E402  — 连续 BLOCK 构件 + F242 夹具钉死助手
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from fake_harness import fake_secret  # noqa: E402
from test_aipos_f163_runtime_evidence_gate_side import _to_audit_await  # noqa: E402
from test_aipos_f179_long_run_resources_block import _block_watch, _status  # noqa: E402
from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli import deploy_runtime as dr  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import post_merge_regression as pmr  # noqa: E402
from tools.aipos_cli.clock import iso_z  # noqa: E402

CRED = "INGEST_CREDENTIAL_IN_ARTIFACT"
SECRET = fake_secret()


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


# ===========================================================================
# 件① 合并后回归: 两跑各给足单次全量时限
# ===========================================================================

def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *argv], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def _merge_repo(tmp_path: Path, sleep_seconds: float) -> tuple[Path, str, str]:
    """临时产品仓: 测试清单每跑一次耗时 sleep_seconds; main 上 merge --no-ff 一个卡分支。返回 (仓, 合并提交, 合并前提交)。"""
    repo = tmp_path / "prod"
    repo.mkdir()
    _write(repo / "tests" / "run-all.sh", f"#!/usr/bin/env bash\nsleep {sleep_seconds}\necho '✓ tests/test_fixture.py PASS'\n"
                                          "echo 'ALL TEST FILES PASS'\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "card/X")
    _write(repo / "README.md", "x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "card")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge card/X", "card/X")
    return repo, _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^1")


def _gov(tmp_path: Path) -> Path:
    from test_aipos_f78_engine_agnostic import _make_gov

    mp = pytest.MonkeyPatch()
    try:
        return _make_gov(tmp_path, mp, shape="lybra")
    finally:
        mp.undo()


def test_item1_baseline_run_gets_its_own_full_timeout(tmp_path: Path) -> None:
    """无记录基线: 两跑各 3s 时限、每跑约 2s——两跑合计超过一份时限仍都跑完(修前基线一跑只拿到余量 ≈1s 必超时)。"""
    gov = _gov(tmp_path)
    repo, merge, pre = _merge_repo(tmp_path, 2)
    res = pmr.execute_check(governance_root=gov, repo_root=repo, merge_commit=merge, runall_path="tests/run-all.sh", timeout_seconds=3,
                            log_dir=tmp_path)
    shown = {k: res.get(k) for k in ("status", "timeout_seconds", "merged_timeout", "baseline_timeout", "total_timeout_seconds",
                                     "merged_seconds", "baseline_seconds", "baseline_source", "timeout_split_basis", "error")}
    _show("[件①·无记录基线(两跑)] " + json.dumps(shown, ensure_ascii=False))
    assert res["status"] == "pass", res
    assert (res["merged_timeout"], res["baseline_timeout"], res["total_timeout_seconds"]) == (3, 3, 6)
    assert res["baseline_source"] == f"run:{pre}"
    assert res["merged_seconds"] + res["baseline_seconds"] > 3, "两跑合计超过一份时限, 基线一跑仍有自己的整份时限"
    assert "各用单次全量时限 3s" in res["timeout_split_basis"] and "总上限 6s = 两跑之和" in res["timeout_split_basis"]
    assert pmr.summary_line(res).startswith("合并后回归: 无新增失败")


def test_item1_recorded_baseline_runs_once_behavior_unchanged(tmp_path: Path) -> None:
    """合并前 main 已有完整记录基线 = 只跑合并后一跑(总上限 = 单次时限, 行为不变)。"""
    gov = _gov(tmp_path)
    repo, merge, pre = _merge_repo(tmp_path, 0.1)
    pmr.write_event_record(gov, "AIPOS-T242B", "advisor.lybra.test", {
        "task_id": "AIPOS-T242B", "runall_path": "tests/run-all.sh", "merge_commit": pre, "status": "pass", "merged_complete": True,
        "merged_failures": [], "merged_seconds": 1.0})
    plan = pmr.planned_total_timeout(gov, repo, merge, 3)
    res = pmr.execute_check(governance_root=gov, repo_root=repo, merge_commit=merge, runall_path="tests/run-all.sh", timeout_seconds=3,
                            log_dir=tmp_path)
    _show(f"[件①·有记录基线(一跑)] 预计 {plan}; 结论 status={res['status']} merged_timeout={res.get('merged_timeout')} "
          f"baseline_timeout={res.get('baseline_timeout')} total={res.get('total_timeout_seconds')} 基线来源 {res.get('baseline_source')}")
    assert plan["runs"] == 1 and plan["total_timeout_seconds"] == 3
    assert res["status"] == "pass" and str(res["baseline_source"]).startswith("record:")
    assert res["merged_timeout"] == 3 and "baseline_timeout" not in res and res["total_timeout_seconds"] == 3
    assert "合并前基线取已记录基线时不另跑" in res["timeout_split_basis"]
    assert pmr.planned_total_timeout(gov, repo, pre, 3)["runs"] == 2, "合并父无记录基线 = 预计两跑"


def test_item1_running_record_deadline_and_queue_limit_use_total(tmp_path: Path) -> None:
    """进行中记录: 预计最迟完成按总上限(两跑之和); 旧记录(无 total_timeout_seconds)仍按 timeout_seconds。"""
    gov = _gov(tmp_path)
    started = iso_z()
    fp = lrr.process_fingerprint(os.getpid())
    for task, commit, extra in (("AIPOS-T242N", "e" * 40, {"total_timeout_seconds": 4502}), ("AIPOS-T242O", "f" * 40, {})):
        pmr.write_event_record(gov, task, "advisor.lybra.test", {
            "task_id": task, "runall_path": "tests/run-all.sh", "merge_commit": commit, "status": "running", "execution": "async",
            "async_pid": os.getpid(), "process_fingerprint": fp, "host": socket.gethostname(), "started_at": started,
            "timeout_seconds": 2251, **extra})
    new = pmr.audit_baseline(gov, "e" * 40)
    old = pmr.audit_baseline(gov, "f" * 40)
    text = pmr.render_audit_baseline({**new, "sha_source": "--sha"})
    _show(f"[件①·在跑基线(两跑总上限)]\n{text}\n[旧记录] pending={old['pending']}")
    assert new["status"] == "pending" and new["pending"]["timeout_seconds"] == 4502 and new["pending"]["per_run_timeout_seconds"] == 2251
    assert "时限 4502s(单次全量 2251s × 跑数)" in text
    assert old["pending"]["timeout_seconds"] == 2251, "旧记录缺 total_timeout_seconds = 按 timeout_seconds(兼容)"
    rows = {r["task_id"]: r for r in pmr.regression_states(gov)}
    assert rows["AIPOS-T242N"]["total_timeout_seconds"] == 4502 and rows["AIPOS-T242O"]["total_timeout_seconds"] == 2251


# ===========================================================================
# 件② F225 第③项: 本机部署状态 / 时序都不影响结果
# ===========================================================================

def _fake_snapshot(base: Path, commit: str) -> Path:
    """模拟「本机从部署快照运行」: <base>/.deploy/releases/<名>(VERSION git_commit = commit)+ current 链接(只在临时目录)。"""
    rel = base / ".deploy" / "releases" / f"20261026_000000-{commit[:7]}"
    rel.mkdir(parents=True)
    _write(rel / "VERSION", f"release: {rel.name}\ngit_commit: {commit}\n")
    (base / ".deploy" / "current").symlink_to(rel)
    return rel


def test_item2_root_cause_host_deploy_state_and_pinned_fixture_is_deterministic(rig, tmp_path, monkeypatch):  # noqa: F811
    """同一靶场(连续 2 次 BLOCK, loop 跑在夹具部署 COMMIT_OLD), 本机运行代码三种状态: 工作树 / 部署快照 ≠ 夹具提交(7d89f39 式)/
    部署快照 = 夹具提交。不钉: loop status 第二档判定随本机状态翻转(根因原文); 钉住(f202.pin_host_deploy): 三种状态结果一致。"""
    f202.separate_auto_release_second(monkeypatch)
    f202._two_blocks(rig)
    hosts = {"worktree": None, "deploy_other": "7d89f39" + "0" * 33, "deploy_same": f202.COMMIT_OLD}
    unpinned: dict[str, tuple] = {}
    pinned: dict[str, tuple] = {}
    for name, commit in hosts.items():
        with monkeypatch.context() as mp:
            # 本机运行代码根显式模拟(不取跑本测试那台机的真实代码根——本测试自身也可能从部署快照跑): 工作树 = 非快照目录
            root = _fake_snapshot(tmp_path / name, commit) if commit is not None else (tmp_path / name / "worktree")
            root.mkdir(parents=True, exist_ok=True)
            mp.setattr(dr, "_code_root", lambda root=root: root)
            running = dr.running_code()
            _text, status = _status(rig.gov, f202.TASK)
            na = status["next_action"]
            unpinned[name] = (running.mode, running.commit, na["action"], na["auto_reaudit"]["eligible"])
            _show(f"[件②·不钉·本机 {name}] running_code mode={running.mode} commit={running.commit} → "
                  f"next_action={na['action']} eligible={na['auto_reaudit']['eligible']} — {na['detail'][:160]}")
            with monkeypatch.context() as mp2:
                f202.pin_host_deploy(mp2, f202.COMMIT_OLD)
                _text, status = _status(rig.gov, f202.TASK)
                na = status["next_action"]
                pinned[name] = (na["action"], na["auto_reaudit"]["eligible"], "部署提交与验证根声明都未变化" in na["detail"])
                _show(f"[件②·钉住·本机 {name}] next_action={na['action']} eligible={na['auto_reaudit']['eligible']} — {na['detail'][:160]}")
    # 根因: 本机从别的部署快照跑 = 判「部署已变」(修前第 309 行红的那一态)
    assert unpinned["deploy_other"][:2] == ("deploy", hosts["deploy_other"]) and unpinned["deploy_other"][3] is True
    assert unpinned["worktree"][3] is False and unpinned["deploy_same"][3] is False
    assert set(pinned.values()) == {("advisor_fix", False, True)}, pinned


def test_item2_auto_release_strictly_between_verdict_seconds(rig, monkeypatch):  # noqa: F811
    """注入时钟后: 第二档放行 released_at 严格晚于上一笔 BLOCK 裁决、下一轮裁决严格晚于放行(秒精度), R3 BLOCK 后照常 audit_blocked。"""
    f202.pin_host_deploy(monkeypatch)
    f202.separate_auto_release_second(monkeypatch)
    f202._two_blocks(rig)
    res, text = f202._loop(rig, _block_watch(rig, {f"{f202.TASK}R3": "BLOCK"}), f202.COMMIT_NEW)
    assert res.reason == "audit_blocked" and "auto_reaudit: 第二档自动重新派审" in text, text
    from tools.aipos_cli import owner_inbox as oi

    rel = oi.auto_release_records(rig.gov, f202.TASK)[-1]
    verdicts = {str(fm.get("audit_task_id")): str(fm.get("verdict_at")) for _p, fm in nr._regular_verdicts(rig.gov, f202.TASK)}
    _show(f"[件②·时序] R2 裁决 {verdicts[f'{f202.TASK}R2']} < 放行 {rel['released_at']} < R3 裁决 {verdicts[f'{f202.TASK}R3']}")
    assert verdicts[f"{f202.TASK}R2"] < str(rel["released_at"]) < verdicts[f"{f202.TASK}R3"]
    assert nr.audit_block_state(rig.gov, f202.TASK)["consecutive"] == 1


# ===========================================================================
# 件③ 门侧交回 / 裁决写入口同核凭据判据(CLI 与 MCP 直调同码同文)
# ===========================================================================

def _gate_call(name: str, arguments: dict) -> dict:
    """门 MCP 直调(不经 CLI / artifact ingest): 驱动方能力范围内直接调门动词处理器。"""
    from tools.mcp_server import tools as gate

    with gate.request_capability_scope(f90.CAP):
        res = getattr(gate, name)(dict(arguments))
    return res["structuredContent"] if isinstance(res, dict) and isinstance(res.get("structuredContent"), dict) else res


def _reasons(payload: dict) -> str:
    return "\n".join(str(r) for r in payload.get("blocking_reasons") or [])


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)  # 靶场 in_process_run: 进程内真产品 CLI


def _last_call(verb: str) -> dict:
    return next(dict(a) for n, a in reversed(f90.InProcessGate.log) if n == verb)


def test_item3_return_via_mcp_direct_and_cli_rejected_without_value(rig):  # noqa: F811
    task = "PROBE-F242-RET"
    f90._card(rig.gov, task, "pending")
    init_governance_repo(rig.gov)
    f162._loop(rig, task, lambda args, expect_ready, deploy_guard=None: 2, max_wait=1)
    assert nr._find_task_in_queue(rig.gov, task)[1] == "claimed"
    f90._executor_work(rig, task)
    report = rig.gov / "task_cards" / task / "RETURN.md"
    clean = report.read_text(encoding="utf-8")
    report.write_text(clean + f"\n## 验证\n- 登录验证: db_password: {SECRET}\n", encoding="utf-8")
    command = str(nr.derive_next_step(task, rig.gov).get("shell_command") or "")
    assert command.startswith("lybra queue return ") and " --confirm" in command, command

    cli = _run(shlex.split(command))  # 手敲 / 派生的交回命令(不经 artifact ingest)
    cli_text = cli.stdout + cli.stderr
    _show(f"[件③·交回·CLI 直提 rc={cli.returncode}]\n{cli_text}")
    assert cli.returncode != 0 and CRED in cli_text and SECRET not in cli_text
    args = _last_call("lybra_queue_return_dry_run")
    direct = _gate_call("lybra_queue_return_dry_run", args)
    direct_text = _reasons(direct)
    _show(f"[件③·交回·门 MCP 直调] verdict={direct.get('verdict')}\n{direct_text}")
    assert direct.get("verdict") == "BLOCK" and CRED in direct_text and SECRET not in json.dumps(direct, ensure_ascii=False)
    hit = next(line for line in direct_text.splitlines() if CRED in line)
    assert hit in cli_text, "CLI 与 MCP 直调同码同文"
    assert f"task_cards/{task}/RETURN.md 第 " in hit and "键值式口令" in hit
    assert not f90._records(rig.gov, "returns", task), "拒收期间零交回记录"

    report.write_text(clean, encoding="utf-8")
    summary = _gate_call("lybra_queue_return_dry_run", {**args, "result_summary": f"完成; redis-cli --password {SECRET} ping 通过"})
    _show(f"[件③·交回·MCP 直调 result_summary 带凭据] verdict={summary.get('verdict')}\n{_reasons(summary)}")
    assert summary.get("verdict") == "BLOCK" and "queue_return.result_summary 第 1 行" in _reasons(summary)
    assert SECRET not in _reasons(summary), "拒因不回显值(预演回显的是调用方自己提交的参数)"
    assert not f90._records(rig.gov, "returns", task)
    ok = _gate_call("lybra_queue_return_dry_run", args)
    _show(f"[件③·交回·干净报告 MCP 直调] verdict={ok.get('verdict')} blocking={ok.get('blocking_reasons')}")
    assert CRED not in _reasons(ok) and ok.get("verdict") != "BLOCK", ok


def test_item3_verdict_via_mcp_direct_and_cli_rejected_without_value(rig):  # noqa: F811
    task = "PROBE-F242-VD"  # 卡号不以 R 结尾(R 后缀 = 审计卡号形)
    audit_id = _to_audit_await(rig, task)
    report = f162._report(rig, audit_id, "PASS", reviewed=task)
    clean = report.read_text(encoding="utf-8")
    command = str(nr.derive_next_step(audit_id, rig.gov).get("shell_command") or "")
    assert command.startswith("lybra audit-verdict ") and " --confirm" in command, command
    report.write_text(clean + f"\n## 取证\n- 门探活: curl -H 'Authorization: Bearer {SECRET}{SECRET}' http://127.0.0.1:1/x\n", encoding="utf-8")

    cli = _run(shlex.split(command))
    cli_text = cli.stdout + cli.stderr
    _show(f"[件③·裁决·CLI 直提 rc={cli.returncode}]\n{cli_text}")
    assert cli.returncode != 0 and CRED in cli_text and SECRET not in cli_text
    args = _last_call("lybra_audit_verdict_dry_run")
    direct = _gate_call("lybra_audit_verdict_dry_run", args)
    direct_text = _reasons(direct)
    _show(f"[件③·裁决·门 MCP 直调] verdict={direct.get('verdict')}\n{direct_text}")
    # 拒因不含值; 预演的计划写入(审计报告快照 rendered_markdown)是调用方自己提交的报告原文, BLOCK = 不落盘(下方零裁决记录)
    assert direct.get("verdict") == "BLOCK" and CRED in direct_text and SECRET not in direct_text
    hit = next(line for line in direct_text.splitlines() if CRED in line)
    assert hit in cli_text and f"task_cards/{audit_id}/" in hit and "Bearer" in hit, hit
    assert not f90._records(rig.gov, "audit_verdicts", task), "拒收期间零裁决记录"

    report.write_text(clean, encoding="utf-8")
    summary = _gate_call("lybra_audit_verdict_dry_run", {**args, "findings_summary": f"复核通过; 连接串 mysql://root:{SECRET}@db.local/app"})
    _show(f"[件③·裁决·MCP 直调 findings_summary 带凭据] verdict={summary.get('verdict')}\n{_reasons(summary)}")
    assert summary.get("verdict") == "BLOCK" and "audit_verdict.findings_summary 第 1 行" in _reasons(summary)
    assert SECRET not in _reasons(summary), "拒因不回显值(预演回显的是调用方自己提交的参数)"
    assert not f90._records(rig.gov, "audit_verdicts", task)
    ok = _gate_call("lybra_audit_verdict_dry_run", args)
    _show(f"[件③·裁决·干净报告 MCP 直调] verdict={ok.get('verdict')} blocking={ok.get('blocking_reasons')}")
    assert CRED not in _reasons(ok) and ok.get("verdict") != "BLOCK", ok


def test_item3_single_criterion_both_gate_entries():
    """判据只此一份(credential_scan); 门两处写入口经 gate_credential_blocking 调用, 不另写判据。"""
    import inspect

    from tools.aipos_cli import artifact_ingest, board_adapter

    for fn in (board_adapter._build_return_preview, board_adapter._build_audit_verdict_preview):
        assert "gate_credential_blocking(" in inspect.getsource(fn), fn.__name__
    assert "credential_refusal(" in inspect.getsource(artifact_ingest.gate_credential_blocking)
    defs = sorted(str(f.relative_to(REPO_ROOT)) for f in (REPO_ROOT / "tools").rglob("*.py")  # 不依赖 .git(部署快照里也能跑)
                  for line in f.read_text(encoding="utf-8").splitlines() if line.startswith(("def credential_hits(", "def credential_scan(")))
    assert defs == ["tools/aipos_cli/artifact_ingest.py"] * 2, defs


# ===========================================================================
# 件④ 章程事前提示(文本声明一处, 执行 / 审计章程渲染)
# ===========================================================================

def test_item4_charters_render_credential_rule_single_declaration(tmp_path, monkeypatch):
    from test_aipos_f80_zero_gate_charter import _identity, _make_gov
    from tools.aipos_cli.artifact_ingest import credential_charter_rule, credential_guard_declaration
    from tools.aipos_cli.charter_render import charter_render_context, charter_size_guard, render_charter

    rule = credential_charter_rule()
    assert rule == credential_guard_declaration()["texts"]["charter_rule"]
    _show(f"---- 件④ 声明原文(transitions artifact_ingest.credential_guard.texts.charter_rule) ----\n{rule}")
    assert "不写明文凭据" in rule and "占位" in rule and "被 git 忽略的私有运行目录" in rule
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    for role, prefix in (("executor", "exec"), ("auditor", "audit")):
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert master.count("{{credential_rule}}") == 1 and "不写明文凭据(口令" not in master, "母本只留占位"
        harness = tmp_path / "pi" / f"lybra-{role}"
        harness.mkdir(parents=True)
        ctx = charter_render_context(gov, identity=_identity(harness, role, f"{prefix}.lybra.hostl", "lybra"), product_commit="x")
        rendered = render_charter(master, ctx)
        line = next(ln for ln in rendered.splitlines() if "AIPOS-F242" in ln)
        _show(f"---- 件④ {role} 渲染后章程行 ----\n{line}")
        assert rule in line and "{{" not in rendered and len(rendered.encode("utf-8")) < charter_size_guard()["max_bytes"]
    advisor = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    assert "{{credential_rule}}" not in advisor
