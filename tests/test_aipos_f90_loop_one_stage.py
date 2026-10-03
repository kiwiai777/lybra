"""AIPOS-F90 — 推进流程收到产品命令三件(靶场分根: tmp 治理根 + tmp 产品仓 + tmp HOME, schema 从产品根读; 禁真门/真治理根/pi)。

件① 认领经 `lybra loop` 一段式走通、建树失败即拒认领(顾问 10-03 活体实测三缺陷逐条先红后绿):
    缺陷① loop 收到 --envelope 却派生不带 --owner-policy-ref 的认领命令 → 门拒 OWNER_POLICY_REF_REQUIRED
    缺陷② 请求 PreAuthorized, 门拒因却称 Supervised
    缺陷③ GateClient 写死 10s 超时, 门侧已认领成功却报失败(假失败)
件② 裁决经 artifact ingest 自动入门 + 模型字段由产品从会话记录填写 + 审计报告快照进 records
件③ 开工只走 /go(my-tasks next_card / --task-id 核验), 拒非本人在办/已结案/产物已交之卡; 顾问技能删手写门接口示范

靶场门 = 真门动词处理器(tools.mcp_server.tools, 进程内, 驱动方 token 能力)经真产品 CLI 薄壳(aipos_cli.main → two_phase_shell_factory)
调用: 夹具只替换「HTTP 传输」(InProcessGate)与「派生命令子进程」(进程内跑 aipos_cli.main); finalize 步 merge/push/deploy 落在
产品仓所在机, 靶场以同一 writer(finalization_record.write_finalization_record)替身(禁在靶场跑 deploy)。agent 步由 watch 注入
模拟执行体/审计体落产物(不起会话、不 sleep 自旋)。驱动方全程只调 run_loop(=`lybra loop` 实现)。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _fm, _git, _write  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.confirm_client import GateTimeout  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402

POLICY = "pol_probe_loop_1"
TASK = "PROBE-F90-1"
AUDIT = f"{TASK}R"
CAP = {"role": "advisor", "role_class": "advisor", "agent_instance": DRIVER, "token_ref": "fixture-ref",
       "expires_at": "2999-01-01T00:00:00Z"}
SELF_EXEC_MODEL = "claude-opus-5-5"
SELF_AUDIT_MODEL = "claude-sonnet-5"  # 审计体自报(与会话记录不一致 → 标 model_mismatch)
RUNTIME_AUDIT_MODEL = "qwen3.7-plus"


def _show(line: str) -> None:
    print(line, flush=True)


# ---------------------------------------------------------------------------
# 靶场: 进程内门 + 进程内产品 CLI
# ---------------------------------------------------------------------------

class InProcessGate:
    """GateClient 的进程内传输替身: call_tool → 真门动词处理器(驱动方 token 能力)。slow[verb]=N: 门执行后仍超时 N 次(假失败);
    drop[verb]=N: 未到门即超时 N 次(真失败)。"""

    log: list[tuple[str, dict]] = []
    slow: dict[str, int] = {}
    drop: dict[str, int] = {}

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        assert token and "fixture" in token  # token 只在进程内, 永不上屏
        self.timeout = timeout

    def initialize(self) -> dict:
        return {}

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        from tools.mcp_server import tools as gate

        if InProcessGate.drop.get(name):
            InProcessGate.drop[name] -= 1
            raise GateTimeout(f"simulated: request for {name} never reached the gate (timed out)")
        InProcessGate.log.append((name, dict(arguments)))
        with gate.request_capability_scope(CAP):
            res = getattr(gate, name)(dict(arguments))
        payload = res["structuredContent"] if isinstance(res, dict) and isinstance(res.get("structuredContent"), dict) else res
        if InProcessGate.slow.get(name):
            InProcessGate.slow[name] -= 1
            raise GateTimeout(f"simulated: gate did not answer within {timeout}s ({name}) — 门侧已处理")
        return payload


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AIPOS_WORKSPACE_ROOT", str(gov))
    _write(gov / ".lybra" / "connection.json", json.dumps({
        "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
        "tokens": [{"role": "advisor", "role_class": "advisor", "agent_instance": DRIVER, "token": "fixture-not-a-secret", "scopes": []}],
    }))
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    # 与真实驱动信封同形: agent_or_role=advisor, task_selector_task_mode=code(审计卡经 envelope_subject 按被审卡判)
    _write(gov / "5_tasks" / "policies" / f"{POLICY}.md", build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope", task_selector_task_mode="code"))
    code_repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])

    InProcessGate.log, InProcessGate.slow, InProcessGate.drop = [], {}, {}
    from tools.aipos_cli import two_phase_shell_factory

    monkeypatch.setattr(two_phase_shell_factory, "GateClient", InProcessGate)
    cli_calls: list[list[str]] = []
    real_run = subprocess.run

    def in_process_run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "lybra":
            cli_calls.append(list(argv))
            if argv[1] == "finalize":  # 产品仓所在机的 merge/push/deploy: 同一 writer 替身, 禁在靶场 deploy
                return _finalize_double(gov, code_repo, list(argv))
            from tools.aipos_cli.aipos_cli import main

            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = main(list(argv[1:]))
                except SystemExit as exc:
                    rc = exc.code if isinstance(exc.code, int) else 1
            return subprocess.CompletedProcess(argv, int(rc or 0), out.getvalue(), err.getvalue())
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", in_process_run)
    return SimpleNamespace(gov=gov, code_repo=code_repo, home=home, cli=cli_calls, real_run=real_run)


def _finalize_double(gov: Path, code_repo: Path, argv: list[str]) -> subprocess.CompletedProcess:
    from tools.aipos_cli.finalization_record import write_finalization_record

    task_id = argv[argv.index("--task-id") + 1]
    actor = argv[argv.index("--actor") + 1]
    verdict_id = str((nr._read_task_records(gov, task_id).get("latest_verdict") or {}).get("verdict_id") or "")
    _git(code_repo, "merge", "-q", "--no-ff", "-m", f"Merge card/{task_id}", f"card/{task_id}")
    merge = _git(code_repo, "rev-parse", "main")
    written = write_finalization_record(governance_root=gov, task_id=task_id, actor=actor, commit=merge, merge_commit=merge,
                                        authorization_type="verdict_ref", authorization_ref=verdict_id, deployed=True,
                                        deploy_status="deployed", remote_ref="origin/main")
    return subprocess.CompletedProcess(argv, 0 if written.get("ok") else 1, f"finalized (double) {merge}", "")


def _card(gov: Path, task_id: str, queue: str, *, harness: str = "claude-code", **extra) -> Path:
    return f78._card(gov, task_id, queue, extra={"harness": harness, **extra})


def _claude_session(home: Path, target: Path, model: str, name: str = "sess-exec.jsonl") -> Path:
    path = home / ".claude" / "projects" / "-fixture-advisor" / "fixture" / "subagents" / name
    entry = {"type": "assistant", "cwd": str(target.parent), "message": {"model": model, "content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": str(target), "content": "..."}}]}}
    _write(path, json.dumps({"type": "user", "message": {"role": "user", "content": "go"}}) + "\n" + json.dumps(entry) + "\n")
    return path


def _pi_session(home: Path, workstation: Path, target: Path, model: str, name: str = "sess-audit.jsonl") -> Path:
    path = home / ".pi" / "agent" / "sessions" / ("--" + str(workstation).strip("/").replace("/", "-") + "--") / name
    lines = [
        {"type": "session", "version": 3, "id": "fixture", "cwd": str(workstation)},
        {"type": "model_change", "provider": "kiwiai", "modelId": model},
        {"type": "message", "message": {"role": "assistant", "provider": "kiwiai", "model": model, "content": [
            {"type": "toolCall", "id": "c1", "name": "write", "arguments": {"path": str(target), "content": "..."}}]}},
    ]
    _write(path, "\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def _executor_work(rig: SimpleNamespace, task_id: str = TASK) -> None:
    """执行体: 在门建好的卡工作树提交 + 把 Return 落到声明落点(自报模型) + Claude Code 会话记录(运行时模型)。"""
    code_repo, worktree = nr.card_worktree_location(rig.gov, task_id)
    assert worktree.is_dir(), f"门认领应已建卡工作树 {worktree}"
    _write(worktree / "tests" / f"test_{task_id.lower().replace('-', '_')}.py", "def test_ok():\n    assert True\n")
    _git(worktree, "add", "-A", "tests")
    _git(worktree, "commit", "-q", "-m", f"{task_id}: work")
    sha, tree = _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")
    ret = rig.gov / "task_cards" / task_id / "RETURN.md"
    _write(ret, _fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{task_id}", "model": SELF_EXEC_MODEL},
                    f"# RETURN — {task_id}\n\n## 一句话结论\n完成。\n\n## 改动清单\n- tests\n"))
    _claude_session(rig.home, ret, "claude-opus-5-5")


def _auditor_work(rig: SimpleNamespace, task_id: str = TASK) -> Path:
    """审计体: 审计报告(verdict + commit_sha=被审分支 tip, 自报模型) + pi 工位会话记录(运行时模型与自报不同)。"""
    tip = _git(rig.code_repo, "rev-parse", f"card/{task_id}")
    report = rig.gov / "task_cards" / f"{task_id}R" / "audit_report.md"
    _write(report, _fm({"task_id": f"{task_id}R", "reviewed_task_id": task_id, "verdict": "PASS", "commit_sha": tip,
                        "model": SELF_AUDIT_MODEL}, "# 审计报告\n\n## 一句话结论\n逐条复核通过。\n\n## 证据\n- 夹具绿\n"))
    _pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, RUNTIME_AUDIT_MODEL)
    return report


def _agent_watch(rig: SimpleNamespace, seen: list[str]):
    """watch 注入: 等待期间由模拟 agent 落产物(执行体/审计体), 就绪判据仍是产品推导核(expect_ready)。"""

    def watch(args, expect_ready):
        patterns = " ".join(args.expect)
        if f"/{AUDIT}/" in patterns or patterns.startswith(f"task_cards/{AUDIT}"):
            seen.append("auditor")
            _auditor_work(rig)
        else:
            seen.append("executor")
            # 执行体开工面 = my-tasks next_card(与 /go 同一产品输出): 落点 = 门建树落点
            view = _my_tasks(rig.gov, EXEC)
            seen.append(f"next_card={view['next_card'] and view['next_card']['task_id']}:{view['next_card'] and view['next_card']['worktree_path']}")
            _executor_work(rig)
        return 0 if expect_ready([]) else 3

    return watch


def _my_tasks(gov: Path, actor: str, ref: str | None = None) -> dict:
    from tools.aipos_cli.aipos_cli import main

    buf = io.StringIO()
    argv = ["my-tasks", "--actor", actor, "--json"] + (["--task-id", ref] if ref else [])
    with contextlib.redirect_stdout(buf):
        assert main(argv) == 0
    return json.loads(buf.getvalue())


def _records(gov: Path, kind: str, card: str) -> list[Path]:
    d = gov / "5_tasks" / "records" / kind / card
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _cli_verbs(rig: SimpleNamespace) -> list[str]:
    out = []
    for argv in rig.cli:
        joined = " ".join(argv[:3])
        tid = argv[argv.index("--task-id") + 1] if "--task-id" in argv else (argv[argv.index("--source-task-id") + 1] if "--source-task-id" in argv else "")
        out.append(f"{joined} {tid}".strip())
    return out


# ===========================================================================
# 件① 认领经 loop 一段式 —— 全程只经 `lybra loop`, pending → completed
# ===========================================================================

def test_item1_loop_pending_to_completed_only_product_commands_record_chain(rig):
    _card(rig.gov, TASK, "pending")
    out = io.StringIO()
    seen: list[str] = []
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=_agent_watch(rig, seen),
                   interval=0.05, max_wait=5, max_steps=30)
    text = out.getvalue()
    _show(text)
    _show(f"[件①·全链] 产品 CLI 调用序: {_cli_verbs(rig)}")
    _show(f"[件①·全链] 门动词调用序: {[n for n, _a in InProcessGate.log]}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    # 缺陷①: 派生的认领命令带 loop 的信封(两张卡: 执行卡 + 审计卡)
    claims = [s for s in res.steps if s.action_type == "claim"]
    assert len(claims) == 2 and all(f"--owner-policy-ref {POLICY}" in s.command and "--autonomy-mode PreAuthorized" in s.command for s in claims), claims
    assert [s.card for s in claims] == [TASK, AUDIT]
    # 门侧: 认领一阶段放行(PreAuthorized + 信封), 审计卡按被审卡判信封(envelope_subject)
    claim_args = [a for n, a in InProcessGate.log if n == "lybra_queue_claim_dry_run"]
    assert [a["task_id"] for a in claim_args] == [TASK, AUDIT] and all(a["owner_policy_ref"] == POLICY for a in claim_args)
    claim_fm = nr._read_frontmatter(_records(rig.gov, "claims", TASK)[0])
    assert claim_fm.get("owner_policy_ref") == POLICY and claim_fm.get("autonomy_mode") == "PreAuthorized", claim_fm
    # 认领同一步建树: 落点 = my-tasks.worktree_path(执行体开工时读到的 next_card)
    _code, worktree = nr.card_worktree_location(rig.gov, TASK)
    assert f"next_card={TASK}:{worktree}" in seen, seen
    # 驱动方只经产品命令: 派生命令全是 `lybra ...`, return/verdict 走产物入口
    verbs = _cli_verbs(rig)
    assert verbs[0] == f"lybra queue claim {TASK}" and f"lybra queue claim {AUDIT}" in verbs
    assert any(v.startswith("lybra queue return") for v in verbs) and any(v.startswith("lybra audit-verdict") for v in verbs)
    assert verbs[-1] == f"lybra queue close {TASK}"
    rec_dirs = sorted(str(p.relative_to(rig.gov / "5_tasks" / "records")) for p in (rig.gov / "5_tasks" / "records").glob("*/*") if p.is_dir())
    _show(f"[件①·全链] 记录目录: {rec_dirs}")
    for s in res.steps:
        if s.action_type in ("return", "verdict"):
            assert s.command.startswith("lybra artifact ingest") and f"--kind {s.action_type}" in s.command, s.command
    # 记录链完整: claim/return/dispatch/verdict/finalization/closure
    for kind, card in (("claims", TASK), ("claims", AUDIT), ("returns", TASK), ("audit_dispatches", AUDIT),
                       ("audit_verdicts", TASK), ("finalizations", TASK), ("closures", TASK)):
        assert _records(rig.gov, kind, card), f"缺记录 {kind}/{card}"
    assert (rig.gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    assert "token" not in text.lower().replace("token 永不上屏", "") and "fixture-not-a-secret" not in text


def test_item1_worktree_failure_refuses_claim_queue_unchanged(rig):
    """建树失败(声明的产品仓不是 git 仓) = 门拒认领: loop exit 2 带拒因原文, 卡仍 pending, 零 claim 记录, 不留无工作树的 claimed 卡。"""
    not_git = rig.gov.parent / "plain-dir"
    not_git.mkdir()
    _write(rig.gov / "project.json", json.dumps({"project": "lybra", "code_repo": str(not_git), "config_version": 1}))
    _card(rig.gov, TASK, "pending")
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=_agent_watch(rig, []), interval=0.05, max_wait=2)
    _show(out.getvalue())
    assert res.exit_code == exit_code_for(load_loop_contract(), "gate_rejected"), out.getvalue()
    assert "WORKTREE_CREATE_FAILED" in res.message and "不是 git 仓根" in res.message, res.message
    assert (rig.gov / "5_tasks" / "queue" / "pending" / f"{TASK.lower()}.md").is_file()
    assert not (rig.gov / "5_tasks" / "queue" / "claimed" / f"{TASK.lower()}.md").exists()
    assert not _records(rig.gov, "claims", TASK) and not (rig.gov / "5_tasks" / "records" / "sessions" / TASK).exists()
    assert len([n for n, _a in InProcessGate.log if n == "lybra_queue_claim_dry_run"]) == 1  # 不重试


def test_item1_defect1_red_derivation_without_scope_drops_envelope_green_with_loop_scope(rig):
    """缺陷① 原文复现: 工位声明的驱动方 ≠ loop --actor 时, 未贯穿 scope 的推导派生 Supervised 形(无 owner_policy_ref);
    loop 贯穿 --actor/--envelope 后同一推导核派生 PreAuthorized + 信封。"""
    _card(rig.gov, TASK, "pending")
    _write(rig.gov / ".lybra" / "role", json.dumps({"role": "advisor", "instance": "advisor.other.instance"}))
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(rig.gov / "5_tasks" / "policies" / f"{POLICY}.md", build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role=DRIVER, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope", task_selector_task_mode="code"))
    bare = nr.derive_next_step(TASK, rig.gov)
    _show(f"[缺陷①·无 scope] {bare['command']}")
    assert "--owner-policy-ref" not in bare["command"] and "--autonomy-mode Supervised" in bare["command"]
    with nr.driver_scope(actor=DRIVER, policy_id=POLICY):
        scoped = nr.derive_next_step(TASK, rig.gov)
    _show(f"[缺陷①·loop scope] {scoped['command']}")
    assert f"--owner-policy-ref {POLICY}" in scoped["command"] and "--autonomy-mode PreAuthorized" in scoped["command"]
    # 执行体对 Supervised 形的认领 fail-closed exit 5(带申领出口), 不发到门
    res = nr.execute_derived_action(bare, rig.gov, None)
    assert res["exit_code"] == exit_code_for(load_loop_contract(), "no_envelope") and "lybra envelope mint" in res["output"]
    assert not InProcessGate.log


def test_item1_defect2_gate_refusal_reports_requested_mode_and_cli_prechecks(rig, capsys):
    """缺陷②: 请求 PreAuthorized 缺 owner_policy_ref → 门拒因如实报 PreAuthorized(原文案写死 Supervised); CLI 本地先拒 exit 5。"""
    from tools.aipos_cli.aipos_cli import main
    from tools.mcp_server import tools as gate

    _card(rig.gov, TASK, "pending")
    with gate.request_capability_scope(CAP):
        res = gate.lybra_queue_claim_dry_run({"task_id": TASK, "actor": EXEC, "agent_instance": EXEC, "autonomy_mode": "PreAuthorized",
                                              "workspace_root": str(rig.gov)})
    payload = res["structuredContent"]
    message = json.dumps(payload, ensure_ascii=False)
    _show(f"[缺陷②·门] {payload.get('error_code')} {payload.get('message') or payload.get('errors')}")
    assert "OWNER_POLICY_REF_REQUIRED" in message and "requested autonomy_mode=PreAuthorized" in message
    assert "Supervised MCP queue_claim" not in message
    with gate.request_capability_scope(CAP):
        sup = gate.lybra_queue_claim_dry_run({"task_id": TASK, "actor": EXEC, "agent_instance": EXEC, "autonomy_mode": "Supervised",
                                              "workspace_root": str(rig.gov)})
    assert "requested autonomy_mode=Supervised" in json.dumps(sup["structuredContent"], ensure_ascii=False)
    rc = main(["queue", "claim", "--task-id", TASK, "--actor", EXEC, "--agent-instance", EXEC, "--confirm",
               "--autonomy-mode", "PreAuthorized", "--connection-json", str(rig.gov / ".lybra" / "connection.json")])
    err = capsys.readouterr().err
    _show(f"[缺陷②·CLI] rc={rc} {err.strip()}")
    assert rc == exit_code_for(load_loop_contract(), "no_envelope") and "--owner-policy-ref" in err
    assert not InProcessGate.log  # 本地即拒, 没发到门
    src = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    assert "pol_lybra_dev_9" not in src  # 写死的缺省信封 id 退役


def test_item1_defect3_timeout_declared_not_hardcoded():
    """缺陷③(一): 门请求超时读声明——缺省 = config.schema timeouts.gate_mcp_request_ms; 认领 dry_run/confirm 声明 client_timeout_ms
    覆盖门内同步建树; loop 子进程上限 step_timeout_seconds ≥ initialize + 两阶段最大超时×2 + 回读(不变量)。"""
    from tools.aipos_cli.confirm_client import GateClient, default_gate_timeout_seconds
    from tools.aipos_cli.two_phase_shell_factory import client_timeout_seconds

    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    default_s = config["timeouts"]["gate_mcp_request_ms"] / 1000
    assert default_gate_timeout_seconds() == default_s and GateClient("http://x", "t")._timeout == default_s
    claim_s = client_timeout_seconds("lybra_queue_claim_dry_run")
    assert claim_s and claim_s >= 120 and client_timeout_seconds("lybra_queue_claim_confirm") == claim_s
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    declared = [v.get("stage_contract", {}).get("client_timeout_ms", 0) / 1000 for v in verbs.values() if isinstance(v, dict)]
    step = nr.loop_step_timeout_seconds()
    _show(f"[缺陷③·声明] default={default_s}s claim={claim_s}s step={step}s max_verb={max(declared)}s")
    assert step >= default_s + 2 * max(declared) + default_s
    src = (REPO_ROOT / "tools" / "aipos_cli" / "confirm_client.py").read_text(encoding="utf-8")
    assert "timeout: float = 10.0" not in src
    nr_src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert "timeout=120" not in nr_src and ">120s" not in nr_src


def test_item1_defect3_false_failure_readback_landed_is_success_no_repeat(rig):
    """缺陷③(二) 先红后绿: 门侧已认领但客户端等应答超时 → 薄壳按声明回读(lybra_queue_list)= 已由本实例认领 → 成功;
    loop 继续推进, 同一认领只执行一次。"""
    _card(rig.gov, TASK, "pending")
    InProcessGate.slow["lybra_queue_claim_dry_run"] = 1
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=lambda args, expect_ready: 3,
                   interval=0.05, max_wait=0.2, max_steps=3)
    text = out.getvalue()
    _show(text)
    names = [n for n, _a in InProcessGate.log]
    _show(f"[缺陷③·回读] 门调用序 {names}")
    assert names.count("lybra_queue_claim_dry_run") == 1 and "lybra_queue_list" in names
    claim_step = [s for s in res.steps if s.action_type == "claim"][0]
    assert claim_step.ok and "landed" in claim_step.output and "等门应答超时后回读确认门侧已落" in claim_step.output, claim_step.output
    assert res.outcome == "wait_timeout"  # 认领后进入等执行体产物(watch 替身不落产物) = 继续推进, 未重复认领
    assert (rig.gov / "5_tasks" / "queue" / "claimed" / f"{TASK.lower()}.md").is_file()


def test_item1_defect3_timeout_not_landed_fails_loudly_and_loop_never_repeats(rig):
    """未到门即超时(真失败)→ 回读 = 门侧未落 → 薄壳出声失败带出口(lybra next 核实, 禁重复提交); loop exit 2 不重试。"""
    _card(rig.gov, TASK, "pending")
    InProcessGate.drop["lybra_queue_claim_dry_run"] = 1
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=lambda args, expect_ready: 3, interval=0.05, max_wait=0.2)
    _show(out.getvalue())
    assert res.exit_code == exit_code_for(load_loop_contract(), "gate_rejected")
    assert "门侧未落" in res.message and "lybra next --task-id" in res.message and "禁直接重复提交" in res.message, res.message
    assert [n for n, _a in InProcessGate.log].count("lybra_queue_claim_dry_run") == 0
    assert (rig.gov / "5_tasks" / "queue" / "pending" / f"{TASK.lower()}.md").is_file()


def test_item1_loop_readback_record_landed_despite_client_failure_continues_once(rig):
    """loop 层兜底: 执行端报失败但该步门生记录已新落(任何原因的假失败)→ 记为已落继续, 绝不重复执行同一步。"""
    _card(rig.gov, TASK, "pending")
    executed: list[str] = []

    def flaky_execute(derivation, root, conn=None):
        result = nr.execute_derived_action(derivation, root, conn)
        executed.append(derivation["command"])
        if len(executed) == 1:
            return {**result, "ok": False, "exit_code": 1, "message": "simulated client-side failure after gate wrote"}
        return result

    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, execute=flaky_execute,
                   watch=lambda args, expect_ready: 3, interval=0.05, max_wait=0.2, max_steps=3)
    _show(out.getvalue())
    assert len(executed) == 1 and res.steps[0].ok and "门侧已落, 不重复执行" in res.steps[0].message, res.steps[0]
    assert res.outcome == "wait_timeout"


# ===========================================================================
# 件② 裁决经 ingest 自动入门 + 模型字段产品填写 + 审计报告快照
# ===========================================================================

def test_item2_verdict_via_ingest_binds_tip_model_from_session_mismatch_flagged_snapshot_restorable(rig):
    _card(rig.gov, TASK, "pending")
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=_agent_watch(rig, []), interval=0.05, max_wait=5,
                   max_steps=30)
    assert res.exit_code == 0, out.getvalue()
    # 交回记录: 运行时模型取自 Claude Code 会话记录(= 自报, 不标不一致)
    ret_fm = nr._read_frontmatter(_records(rig.gov, "returns", TASK)[0])
    _show(f"[件②·return] actual_model={ret_fm.get('actual_model')} agent_runtime={ret_fm.get('agent_runtime')}")
    assert ret_fm["agent_runtime"]["model"] == "claude-opus-5-5" and ret_fm["agent_runtime"]["harness"] == "claude-code"
    assert ret_fm["agent_runtime"]["model_mismatch"] is False and ret_fm["actual_model"] == "claude-opus-5-5"
    # 裁决记录: 绑被审分支 tip; 模型取自 pi 会话记录(≠ 自报 → 标 model_mismatch); 报告全文快照进 records
    verdict_files = [p for p in _records(rig.gov, "audit_verdicts", TASK) if p.name.startswith("verdict_")]
    vfm = nr._read_frontmatter(verdict_files[0])
    _show(f"[件②·verdict] artifact_subject={vfm.get('artifact_subject')} agent_runtime={vfm.get('agent_runtime')} "
          f"snapshot={vfm.get('report_snapshot_ref')}")
    tip = _git(rig.code_repo, "rev-parse", f"card/{TASK}")
    assert vfm["artifact_subject"]["commit_sha"] == tip
    rt = vfm["agent_runtime"]
    assert rt["model"] == RUNTIME_AUDIT_MODEL and rt["model_self_reported"] == SELF_AUDIT_MODEL and rt["model_mismatch"] is True
    assert rt["harness"] == "pi" and rt["model_source"].startswith("pi:sess-audit.jsonl")
    assert vfm["findings_summary_present"] is True and vfm["evidence_refs"] == [f"task_cards/{AUDIT}/audit_report.md"]
    report = rig.gov / vfm["report_source_ref"]
    snapshot = rig.gov / vfm["report_snapshot_ref"]
    original = report.read_bytes()
    # AIPOS-F89 件③d: 快照 = 记录头(record_type 等, 过护栏 B④)+ 原文逐字节; 复原经 unwrap_report_snapshot(去头 + 核 sha256)
    from tools.aipos_cli.board_adapter import unwrap_report_snapshot

    snap_fm = nr._read_frontmatter(snapshot)
    assert snapshot.is_file() and snap_fm["record_type"] == "audit_report_snapshot" and snapshot.read_bytes().endswith(original)
    assert unwrap_report_snapshot(snapshot.read_text(encoding="utf-8")).encode("utf-8") == original
    assert hashlib.sha256(original).hexdigest() == vfm["report_snapshot_sha256"] == snap_fm["report_sha256"]
    assert "report_snapshots" in vfm["report_snapshot_ref"] and snapshot.parent.parent == verdict_files[0].parent
    # 事故复演: 报告被覆盖 → 从快照逐字节复原
    report.write_text("---\nverdict: FAIL\n---\n# 重审覆盖\n", encoding="utf-8")
    report.write_text(unwrap_report_snapshot(snapshot.read_text(encoding="utf-8")), encoding="utf-8")
    assert hashlib.sha256(report.read_bytes()).hexdigest() == vfm["report_snapshot_sha256"]
    _show("[件②·快照] 覆盖后从快照复原, sha256 一致")
    # 快照不被当作裁决记录(记录目录直扫只见 verdict_*)
    latest = nr._read_task_records(rig.gov, TASK)["latest_verdict"]
    assert latest and latest.get("record_type") == "audit_verdict_record"


def test_item2_runtime_model_locator_declared_missing_value_never_trusts_self_report(rig):
    from tools.aipos_cli.artifact_ingest import runtime_model_bundle

    decl = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))["intent_face"]["harness"]["session_record_locators"]
    assert decl["missing_value"] == "未声明会话记录" and {"pi", "claude-code"} <= set(decl)
    report = rig.gov / "task_cards" / "X-1" / "RETURN.md"
    _write(report, "x")
    # 无会话记录 → missing_value(不采信自报)
    b0 = runtime_model_bundle(rig.gov, writer_task_id="X-1", writer_fm={"harness": "pi", "task_mode": "code"}, artifact_path=report,
                              self_reported="gpt-9")
    _show(f"[件②·定位器] 无会话记录: {b0}")
    assert b0["model"] == "未声明会话记录" and b0["model_source"] == "未声明会话记录" and b0["model_self_reported"] == "gpt-9"
    assert b0["model_mismatch"] is False
    # codex 未声明定位器 → missing_value
    b1 = runtime_model_bundle(rig.gov, writer_task_id="X-1", writer_fm={"harness": "codex"}, artifact_path=report, self_reported="")
    assert b1["model"] == "未声明会话记录" and "model_self_reported" not in b1
    # pi 会话记录(只读写入该报告的条目; 别的报告的条目不算)
    other = rig.gov / "task_cards" / "X-2" / "RETURN.md"
    _pi_session(rig.home, rig.home / "ws", other, "other-model", name="other.jsonl")
    _pi_session(rig.home, rig.home / "ws", report, "kiwiai/qwen3.7-plus", name="mine.jsonl")
    b2 = runtime_model_bundle(rig.gov, writer_task_id="X-1", writer_fm={"task_mode": "audit"}, artifact_path=report,
                              self_reported="qwen3.7-plus")
    _show(f"[件②·定位器] pi: {b2}")
    assert b2["harness"] == "pi" and b2["model"] == "kiwiai/qwen3.7-plus" and b2["model_source"] == "pi:mine.jsonl"
    assert b2["model_mismatch"] is False  # provider 前缀等价
    # claude-code 会话记录
    _claude_session(rig.home, report, "claude-opus-5-5", name="cc.jsonl")
    b3 = runtime_model_bundle(rig.gov, writer_task_id="X-1", writer_fm={"harness": "claude-code"}, artifact_path=report,
                              self_reported="claude-sonnet-5")
    assert b3["model"] == "claude-opus-5-5" and b3["model_mismatch"] is True


def test_item2_ingest_kind_mismatch_rejected_and_cli_parses(rig):
    from tools.aipos_cli.artifact_ingest import ingest_task_artifact
    from tools.aipos_cli.loop_driver import check_command_parses

    _card(rig.gov, TASK, "claimed")
    f78._claim_record(rig.gov, TASK, EXEC)
    res = ingest_task_artifact(TASK, rig.gov, kind="verdict")
    assert res["category"] == "INGEST_KIND_MISMATCH" and res["exit_code"] == 4, res
    assert check_command_parses(nr.ingest_command(TASK, "return", rig.gov, None))[0]
    assert nr._action_type_for_command(nr.ingest_command(TASK, "verdict", rig.gov, None)) == "verdict"
    assert nr._action_type_for_command(f"lybra artifact ingest --task-id {TASK} --workspace-root {rig.gov}") == "finalize"


# ===========================================================================
# 件③ 开工只走 /go: 拒非本人在办 / 已结案 / 产物已交
# ===========================================================================

def _kickoff_rig(rig) -> None:
    from tools.aipos_cli.next_resolver import _ensure_worktree

    _card(rig.gov, TASK, "claimed", claimed_by=EXEC)
    f78._claim_record(rig.gov, TASK, EXEC)
    assert _ensure_worktree(rig.gov, TASK)["ok"]
    _card(rig.gov, "PROBE-F90-DONE", "completed", claimed_by=EXEC)
    _card(rig.gov, "PROBE-F90-PEND", "pending")
    _card(rig.gov, "PROBE-F90-OTHER", "claimed", assigned="exec.other.instance", claimed_by="exec.other.instance")
    f78._claim_record(rig.gov, "PROBE-F90-OTHER", "exec.other.instance")


def test_item3_go_and_cold_start_refuse_not_mine_concluded_not_claimed(rig):
    _kickoff_rig(rig)
    ok = _my_tasks(rig.gov, EXEC)
    _show(f"[件③·/go] next_card={ok['next_card']} excluded={ok['next_card_excluded']}")
    assert ok["next_card"]["task_id"] == TASK
    cases = {"PROBE-F90-DONE": "CONCLUDED", "PROBE-F90-PEND": "NOT_CLAIMED", "PROBE-F90-OTHER": "NOT_MINE", "NO-SUCH-CARD": "NOT_FOUND"}
    for ref, code in cases.items():
        view = _my_tasks(rig.gov, EXEC, ref)
        _show(f"[件③·/go {ref}] next_card={view['next_card']} excluded={view['next_card_excluded']}")
        assert view["next_card"] is None and view["next_card_excluded"][0]["code"] == code, view
        assert view["next_card_excluded"][0]["reason"] and "lybra_" not in view["next_card_excluded"][0]["reason"]
    # 以卡路径冷启动(同一判据): 已结案卡路径 → CONCLUDED; 本人在办卡路径 → 放行
    done_path = rig.gov / "5_tasks" / "queue" / "completed" / "probe-f90-done.md"
    assert _my_tasks(rig.gov, EXEC, str(done_path))["next_card_excluded"][0]["code"] == "CONCLUDED"
    mine_path = rig.gov / "5_tasks" / "queue" / "claimed" / f"{TASK.lower()}.md"
    assert _my_tasks(rig.gov, EXEC, str(mine_path))["next_card"]["task_id"] == TASK
    # 产物已交(Return 落盘待入门) → /go 不再开工(防覆盖)
    code_repo, worktree = nr.card_worktree_location(rig.gov, TASK)
    _write(worktree / "tests" / "t.py", "x\n")
    _git(worktree, "add", "tests/t.py")
    _git(worktree, "commit", "-q", "-m", "w")
    sha, tree = _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")
    _write(rig.gov / "task_cards" / TASK / "RETURN.md", f78._return_text(TASK, sha, tree))
    submitted = _my_tasks(rig.gov, EXEC)
    _show(f"[件③·产物已交] {submitted['next_card_excluded']}")
    assert submitted["next_card"] is None and submitted["next_card_excluded"][0]["code"] == "ARTIFACT_SUBMITTED"


def test_item3_auditor_go_refuses_concluded_audit_card_no_reaudit(rig):
    """10-02 事故复演: 审计会话被贴已结案 R 卡号 → /go <R卡号> 拒(CONCLUDED); 本人在办但报告已落盘 → ARTIFACT_SUBMITTED。"""
    _card(rig.gov, TASK, "claimed")
    f78._claim_record(rig.gov, TASK, EXEC)
    _card(rig.gov, AUDIT, "completed", assigned=AUDITOR, task_mode="audit", reviewed_task_id=TASK, claimed_by=AUDITOR)
    done = _my_tasks(rig.gov, AUDITOR, AUDIT)
    _show(f"[件③·审计 已结案] {done['next_card_excluded']}")
    assert done["next_card"] is None and done["next_card_excluded"][0]["code"] == "CONCLUDED"
    (rig.gov / "5_tasks" / "queue" / "completed" / f"{AUDIT.lower()}.md").unlink()
    _card(rig.gov, AUDIT, "claimed", assigned=AUDITOR, task_mode="audit", reviewed_task_id=TASK, claimed_by=AUDITOR)
    f78._claim_record(rig.gov, AUDIT, AUDITOR)
    _write(rig.gov / "task_cards" / AUDIT / "audit_report.md", _fm({"reviewed_task_id": TASK, "verdict": "PASS", "commit_sha": "a" * 40}, "# r\n"))
    view = _my_tasks(rig.gov, AUDITOR, AUDIT)
    _show(f"[件③·审计 报告已落] {view['next_card_excluded']}")
    assert view["next_card"] is None and view["next_card_excluded"][0]["code"] == "ARTIFACT_SUBMITTED"


def test_item3_go_ts_passes_ref_to_product_and_relays_refusal(tmp_path):
    """go.ts: `/go <卡号>` 把指向原样交给产品(my-tasks --task-id), 不自判; 产品拒因原样转述(refused)。"""
    go = REPO_ROOT / "agents" / "harness" / "pi" / "_shared" / "extensions" / "go.ts"
    script = tmp_path / "go-check.ts"
    script.write_text(
        f'import {{ myTasksArgv, planGo }} from "{go}";\n'
        'const out = {\n'
        '  bare: myTasksArgv("exec.x", ""),\n'
        '  ref: myTasksArgv("exec.x", "  PROBE-F90R  "),\n'
        '  refused: planGo({tasks: [], next_card: null, next_card_excluded: [{task_id: "PROBE-F90R", code: "CONCLUDED", reason: "卡已结案"}]}),\n'
        '};\n'
        'console.log(JSON.stringify(out));\n',
        encoding="utf-8",
    )
    proc = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    _show(f"[件③·go.ts] {proc.stdout.strip()} {proc.stderr.strip()[:200]}")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    # AIPOS-F89 件③b: /go 只传工位目录(身份/治理根由产品 workstation_identity 解析), 不再传实例名
    assert data["bare"] == ["my-tasks", "--workstation", "exec.x", "--json"]
    assert data["ref"] == ["my-tasks", "--workstation", "exec.x", "--json", "--task-id", "PROBE-F90R"]
    assert data["refused"]["kind"] == "refused" and "PROBE-F90R CONCLUDED: 卡已结案" in data["refused"]["message"]
    src = go.read_text(encoding="utf-8")
    assert "kickoff_refusal" in src and "myTasksArgv(workstationRoot, args)" in src


def test_item3_advisor_skill_and_charters_zero_manual_gate_and_go_only():
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    hits = re.findall(r"GateClient|call_tool|OWNER_CONFIRMED", skill)
    _show(f"[件③·技能] advisor-commands grep GateClient|call_tool|OWNER_CONFIRMED = {len(hits)}")
    assert hits == []
    assert "lybra loop --task-id" in skill and "`/go`" in skill
    for role in ("executor", "auditor", "advisor"):
        text = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert "/claim" not in text and "GateClient" not in text and "call_tool" not in text, role
        assert "/go" in text, role
    for role in ("executor", "auditor"):
        text = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert "不接受贴卡号/卡路径冷启动" in text, role


def test_f90_fixture_registered_in_runall_and_no_swallowed_exceptions():
    # AIPOS-F93 件③: lybra 产品仓自己的夹具清单, 夹具自定位(门侧位置声明 = 治理根 project.json test_contract.runall_path)
    runall = (Path(__file__).resolve().parent / "run-all.sh").read_text(encoding="utf-8")
    assert "tests/test_aipos_f90_loop_one_stage.py" in runall
    for rel in ("tools/aipos_cli/loop_driver.py", "tools/aipos_cli/two_phase_shell_factory.py", "tools/aipos_cli/artifact_ingest.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"except Exception:\s*\n\s*pass", src), rel
