"""AIPOS-F123 — 接入既有项目②: 在途卡收编(queue adopt 经门补铸 claim、绑定既有分支) + project set-paths 写入口。

靶场(禁真门/真治理根/pi/chris 治理根): tmp 治理根 + tmp 产品仓 + tmp HOME, 人肉期样本全部临时自造。门 = 真门动词处理器
(tools.mcp_server.tools, 进程内, 驱动方 token 能力)经真产品 CLI 薄壳(aipos_cli.main → two_phase_shell_factory)调用;
夹具只替换 HTTP 传输(InProcessGate, 骨架唯一来源 test_aipos_f90_loop_one_stage, 禁第二份)。

件① `lybra queue adopt`: claimed 目录、无门生 claim 记录的卡 → 门补铸 claim 记录(adopted_from=legacy_manual, 绑定既有分支与 tip)、
     写卡面运行时字段、按 card_worktree_location 建卡工作树; 收编后推导核照常推导(等交回 → 交回 → 派审); 手写 Return 缺必填
     frontmatter 时拒因原文带补法。拒: 缺意图必填(一次列全)/ 分支不存在 / 已有门生 claim / 非 claimed / 卡分支分叉 /
     建树失败 / 信封不覆盖。
件② `lybra project set-paths`: 按 config.schema project_json.paths 声明校验, dry-run 给 diff, confirm 经 project.json 唯一写路径
     (与 set-repos 同一 update_project_json)。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import POLICY, InProcessGate, _card, _records, rig  # noqa: E402,F401  — 靶场唯一来源
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402

TASK = "PROBE-F123-1"
LEGACY_BRANCH = "legacy/f123-hand-work"
LEGACY_RUNTIME = {"claimed_by": "human.operator", "claimed_at": "2026-08-01T00:00:00Z", "claim_id": "hand-claim-1"}


def _show(line: str) -> None:
    print(line, flush=True)


def _cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _declare_paths(rig) -> None:
    """AIPOS-F124 件②: 接入顺序 set-paths → freeze-legacy → adopt —— 收编前经产品写入口显式声明落点(值 = 本靶场真实落点 task_cards)。"""
    from tools.aipos_cli.workspace_config import set_project_paths

    set_project_paths(rig.gov, [("return_root", "task_cards")], dry_run=False)


def _legacy_card(rig, task_id: str = TASK, *, queue: str = "claimed", drop: tuple[str, ...] = (), **extra) -> Path:
    """人肉期在途卡: 在 claimed 目录, 卡面带人肉期运行时字段(格式不合门生 id), 无任何门生记录(落点已先声明, AIPOS-F124 件②)。"""
    _declare_paths(rig)
    path = _card(rig.gov, task_id, queue, **{**LEGACY_RUNTIME, **extra})
    if drop:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines(keepends=True)
                 if not any(ln.startswith(f"{key}:") for key in drop)]
        path.write_text("".join(lines), encoding="utf-8")
    return path


def _legacy_branch(code_repo: Path, name: str = LEGACY_BRANCH) -> str:
    """人肉期产物分支(不按 card/<ID> 命名): 一条提交, 不检出(留在 main)。"""
    _git(code_repo, "checkout", "-q", "-b", name)
    _write(code_repo / "tools" / "aipos_cli" / "legacy_work.py", "# hand work\n")
    _git(code_repo, "add", "tools/aipos_cli/legacy_work.py")
    _git(code_repo, "commit", "-q", "-m", "hand-era work")
    tip = _git(code_repo, "rev-parse", "HEAD")
    _git(code_repo, "checkout", "-q", "main")
    return tip


def _adopt(task_id: str, branch: str, *, confirm: bool, policy: str = POLICY, json_out: bool = False) -> tuple[int, str, str]:
    argv = ["queue", "adopt", "--task-id", task_id, "--branch", branch, "--actor", DRIVER, "--owner-policy-ref", policy,
            "--confirm" if confirm else "--dry-run"] + (["--json"] if json_out else [])
    return _cli(argv)


def _snapshot(rig, task_id: str = TASK) -> dict:
    """零写入判据: 卡文件字节 + 记录目录 + 卡工作树 + 卡分支。"""
    card = next((rig.gov / "5_tasks" / "queue").glob(f"*/{task_id.lower()}.md"))
    records = sorted(str(p.relative_to(rig.gov)) for p in (rig.gov / "5_tasks" / "records").rglob("*.md"))
    _code, worktree = nr.card_worktree_location(rig.gov, task_id)
    branches = _git(rig.code_repo, "branch", "--list", nr.card_branch_name(task_id))
    return {"card": card.read_bytes(), "records": records, "worktree": worktree.exists(), "card_branch": branches}


# ===========================================================================
# 件① 收编: dry-run / confirm 原文, 门生 claim 记录原文, 收编后推导到等交回 → 交回 → 派审
# ===========================================================================

def test_item1_adopt_dry_run_then_confirm_mints_claim_and_loop_derives_to_dispatch(rig):
    card = _legacy_card(rig)
    tip = _legacy_branch(rig.code_repo)
    before = nr.derive_next_step(TASK, rig.gov)
    _show(f"[件①·收编前推导] action={before.get('action')} missing={before.get('missing_records')}\n  出口: {before.get('suggested_action')}")
    assert before["derivable"] is False

    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=False)
    _show(f"[件①·adopt --dry-run] rc={rc}\n{out}{err}")
    assert rc == 0, out + err
    assert "预览(零写入" in out and f"{LEGACY_BRANCH}@{tip} → 卡分支 card/{TASK}" in out and f"信封: 覆盖 {POLICY}" in out
    assert _snapshot(rig) == snap, "dry-run 必须零写入"
    calls = [(n, a) for n, a in InProcessGate.log if n == "lybra_queue_adopt_dry_run"]
    assert calls and calls[-1][1].get("preview_only") is True

    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True)
    _show(f"[件①·adopt --confirm] rc={rc}\n{out}{err}")
    assert rc == 0, out + err
    assert "已收编" in out

    claims = _records(rig.gov, "claims", TASK)
    assert len(claims) == 1
    claim_text = claims[0].read_text(encoding="utf-8")
    _show(f"[件①·门生 claim 记录原文] {claims[0].relative_to(rig.gov)}\n{claim_text}")
    claim_fm = nr._read_frontmatter(claims[0])
    assert claim_fm["adopted_from"] == "legacy_manual" and claim_fm["adopted_branch"] == LEGACY_BRANCH
    assert claim_fm["adopted_branch_tip"] == tip and claim_fm["card_branch"] == f"card/{TASK}"
    assert claim_fm["adopted_by"] == DRIVER and claim_fm["actor"] == EXEC and claim_fm["canonical_agent_instance"] == EXEC
    assert claim_fm["autonomy_mode"] == "PreAuthorized" and claim_fm["owner_policy_ref"] == POLICY
    assert claim_fm["event_type"] == "mcp_queue_adopt" and claim_fm["from_state"] == "claimed" and claim_fm["to_state"] == "claimed"
    assert claim_fm["confirmer_token_ref"] == POLICY and claim_fm["submitted_by"] == DRIVER
    assert claim_fm["legacy_card_runtime"] == LEGACY_RUNTIME
    assert _records(rig.gov, "sessions", TASK), "session 记录与 claim 同批落"

    card_fm = nr._read_frontmatter(card)
    _show(f"[件①·卡面运行时字段] { {k: card_fm.get(k) for k in ('status', 'claimed_by', 'claimed_at', 'claim_id', 'active_session_id', 'active_worktree_branch', 'active_worktree_path')} }")
    assert card_fm["status"] == "claimed" and card_fm["claimed_by"] == EXEC
    assert card_fm["claim_id"] == claim_fm["claim_id"] and card_fm["active_session_id"] == claim_fm["session_id"]
    _code, worktree = nr.card_worktree_location(rig.gov, TASK)
    assert card_fm["active_worktree_path"] == str(worktree) and card_fm["active_worktree_branch"] == f"card/{TASK}"
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == f"card/{TASK}"
    assert _git(worktree, "rev-parse", "HEAD") == tip, "卡分支建在既有分支 tip 上"
    assert (rig.gov / "task_cards" / TASK / "RETURN.md").is_file(), "报告骨架与认领同一实现"

    # 收编后: 推导核照常推导 —— 执行体在办(等交回), 开工面 = 门建的卡工作树
    waiting = nr.derive_next_step(TASK, rig.gov)
    _show(f"[件①·收编后推导(等交回)] triggered_by={waiting.get('triggered_by')} node={waiting.get('current_node')}/{waiting.get('current_state')} "
          f"action={waiting.get('action')}\n  {waiting.get('suggested_action')}")
    assert waiting.get("triggered_by") == "executor" and (waiting.get("action") or {}).get("type") != "record_missing"
    assert nr.kickoff_refusal(rig.gov, TASK, EXEC, queue_state="claimed", card_frontmatter=card_fm) is None

    # lybra loop --task-id(驱动方唯一推进命令)对收编卡: 等执行体交回(watch 期间无产物 → exit 3, 非 exit 4 硬停)
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=_watch_nothing(seen := []),
                   interval=0.05, max_wait=1, max_steps=5)
    _show(f"[件①·收编后 lybra loop(等交回)] exit={res.exit_code} outcome={res.outcome}\n{out.getvalue()}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "wait_timeout") and seen == ["executor"], (res.exit_code, seen)

    # 执行体在卡工作树续作并交回 → 同一条 lybra loop: 交回(产物入口) → 派审 → 认领审计卡 → 等审计报告
    _write(worktree / "tests" / "test_probe_f123.py", "def test_ok():\n    assert True\n")
    _git(worktree, "add", "-A", "tests")
    _git(worktree, "commit", "-q", "-m", f"{TASK}: finish")
    sha, tree = _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")
    _write(rig.gov / "task_cards" / TASK / "RETURN.md", _fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{TASK}"},
                                                             f"# RETURN — {TASK}\n\n## 一句话结论\n收编后续作完成。\n\n## 改动清单\n- tests\n"))
    out = io.StringIO()
    seen = []
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=POLICY, out=out, watch=_watch_nothing(seen),
                   interval=0.05, max_wait=1, max_steps=8)
    _show(f"[件①·交回后 lybra loop(交回→派审→等审计)] exit={res.exit_code} steps={[(s.action_type, s.card) for s in res.steps]}\n{out.getvalue()}")
    _show(f"[件①·记录链] returns={[p.name for p in _records(rig.gov, 'returns', TASK)]} "
          f"audit_dispatches={[p.name for p in _records(rig.gov, 'audit_dispatches', f'{TASK}R')]}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "wait_timeout") and seen == ["auditor"], (res.exit_code, seen)
    assert _records(rig.gov, "returns", TASK) and _records(rig.gov, "audit_dispatches", f"{TASK}R")
    after = nr.derive_next_step(TASK, rig.gov)
    assert after["current_node"] == "audit_dispatch" and after["triggered_by"] == "auditor"


def _watch_nothing(seen: list[str]):
    """watch 注入: 等待期间不落任何产物(记录在等谁), 就绪判据仍是产品推导核 → 超时 exit 3。"""

    def watch(args, expect_ready):
        seen.append("auditor" if f"/{TASK}R/" in " ".join(args.expect) or f"{TASK}R/" in " ".join(args.expect) else "executor")
        return 0 if expect_ready([]) else 3

    return watch


def test_item1_handwritten_return_missing_fields_hint_names_how_to_fill(rig):
    """人肉期手写 Return(缺 commit_sha/tree_hash/branch): 收编后推导 = artifact_invalid, 拒因原文逐项给补法(报告必填契约单源);
    照补法补齐后推导 = 交回步。"""
    _legacy_card(rig)
    tip = _legacy_branch(rig.code_repo)
    ret_path = rig.gov / "task_cards" / TASK / "RETURN.md"
    _write(ret_path, f"# RETURN — {TASK}\n\n## 一句话结论\n人肉期已做完。\n\n## 改动清单\n- tools/aipos_cli/legacy_work.py\n")
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True)
    assert rc == 0, out + err
    assert ret_path.read_text(encoding="utf-8").startswith(f"# RETURN — {TASK}"), "既有手写 Return 不被骨架覆盖"
    stop = nr.derive_next_step(TASK, rig.gov)
    _show(f"[件①·手写 Return 拒因原文] action={stop.get('action')}\n  missing={stop.get('missing_records')}\n  {stop.get('suggested_action')}")
    assert (stop.get("action") or {}).get("type") == "artifact_invalid"
    for needle in ("commit_sha = 卡分支 card/", f"git rev-parse card/{TASK}", "tree_hash = 该 commit 的 tree", f"branch = 卡分支名 card/{TASK}"):
        assert needle in stop["suggested_action"], needle
    tree = _git(rig.code_repo, "rev-parse", f"{tip}^{{tree}}")
    card_tip = _git(rig.code_repo, "rev-parse", f"card/{TASK}")
    assert card_tip == tip
    _write(ret_path, _fm({"commit_sha": card_tip, "tree_hash": tree, "branch": f"card/{TASK}"}, ret_path.read_text(encoding="utf-8")))
    with nr.driver_scope(actor=DRIVER, policy_id=POLICY):
        ready = nr.derive_next_step(TASK, rig.gov)
    _show(f"[件①·按补法补齐后] {ready.get('command')}")
    assert ready["derivable"] and "--kind return" in ready["command"]


def test_item1_adopt_with_card_named_branch_reuses_it(rig):
    """既有分支本就叫卡分支名: 直接复用(不另建分支), 工作树检出该分支。"""
    _legacy_card(rig)
    tip = _legacy_branch(rig.code_repo, f"card/{TASK}")
    rc, out, err = _adopt(TASK, f"card/{TASK}", confirm=True)
    _show(f"[件①·同名分支收编] rc={rc}\n{out}{err}")
    assert rc == 0, out + err
    _code, worktree = nr.card_worktree_location(rig.gov, TASK)
    assert _git(worktree, "rev-parse", "HEAD") == tip


# ===========================================================================
# 件① 拒绝原文: 缺必填(一次列全)/ 分支不存在 / 已有 claim / 非 claimed / 分叉 / 建树失败 / 信封不覆盖 —— 全部零写入
# ===========================================================================

def test_item1_refusals_are_complete_and_write_nothing(rig):
    tip = _legacy_branch(rig.code_repo)

    # 缺意图必填: 一次列全(validator.REQUIRED_FIELDS), 不代填
    _legacy_card(rig, drop=("title", "context_bundle", "priority"))
    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True)
    _show(f"[件①·拒·缺必填] rc={rc}\n{out}{err}")
    assert rc == 1 and "ADOPT_CARD_INTENT_INCOMPLETE" in out and "缺 3 项: title, context_bundle, priority" in out
    assert _snapshot(rig) == snap
    for p in (rig.gov / "5_tasks" / "queue").glob("*/*.md"):
        p.unlink()

    # 分支不存在于 lane.repo
    _legacy_card(rig)
    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, "no/such-branch", confirm=True)
    _show(f"[件①·拒·分支不存在] rc={rc}\n{out}{err}")
    assert rc == 1 and "ADOPT_BRANCH_NOT_FOUND" in out and "no/such-branch" in out and str(rig.code_repo) in out
    assert _snapshot(rig) == snap

    # 卡分支已存在且 tip 不同(绑定不唯一)
    _git(rig.code_repo, "branch", f"card/{TASK}", "main")
    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=False)
    _show(f"[件①·拒·卡分支分叉] rc={rc}\n{out}{err}")
    assert rc == 1 and "ADOPT_CARD_BRANCH_DIVERGED" in out and tip in out
    assert _snapshot(rig) == snap
    _git(rig.code_repo, "branch", "-D", f"card/{TASK}")

    # 信封不覆盖(policy 不存在): --confirm 经门一段式被拒 = exit 5(无信封出口), 零写入
    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True, policy="pol_absent")
    _show(f"[件①·拒·信封不覆盖] rc={rc}\n{out}{err}")
    assert rc == exit_code_for(load_loop_contract(), "no_envelope") and "ENVELOPE_POLICY_NOT_FOUND" in (out + err)
    assert _snapshot(rig) == snap

    # 已收编 = 已有门生 claim 记录 → 再收编拒
    assert _adopt(TASK, LEGACY_BRANCH, confirm=True)[0] == 0
    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True)
    _show(f"[件①·拒·已有 claim] rc={rc}\n{out}{err}")
    assert rc == 1 and "ADOPT_CLAIM_EXISTS" in out and f"claims/{TASK}/" in out
    assert _snapshot(rig) == snap


def test_item1_refuses_pending_card_and_worktree_failure_leaves_card_untouched(rig):
    _legacy_branch(rig.code_repo)
    pending = "PROBE-F123-2"
    _card(rig.gov, pending, "pending")
    rc, out, err = _adopt(pending, LEGACY_BRANCH, confirm=False)
    _show(f"[件①·拒·非 claimed] rc={rc}\n{out}{err}")
    assert rc == 1 and "ADOPT_NOT_CLAIMED" in out

    # 建树失败: 既有分支名即卡分支名且已在产品仓主工作区检出(git worktree add 拒) → ADOPT_WORKTREE_FAILED, 卡面/记录零变更
    _legacy_card(rig)
    _git(rig.code_repo, "branch", f"card/{TASK}", LEGACY_BRANCH)
    _git(rig.code_repo, "checkout", "-q", f"card/{TASK}")
    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, f"card/{TASK}", confirm=True)
    _show(f"[件①·拒·建树失败] rc={rc}\n{out}{err}")
    assert rc == 1 and "ADOPT_WORKTREE_FAILED" in (out + err)
    assert _snapshot(rig) == snap
    _git(rig.code_repo, "checkout", "-q", "main")


def test_item1_gate_tool_requires_envelope_mode_and_declares_claim_family():
    """门工具: 非 preview 只认 PreAuthorized(ADOPT_ENVELOPE_REQUIRED, 文案读声明); verbs/transitions 同声明 claim 动词族。"""
    from tools.mcp_server import tools as gate
    from tools.schema_loader import load_schema

    cap = {"role": "advisor", "role_class": "advisor", "agent_instance": DRIVER, "token_ref": "fixture-ref", "expires_at": "2999-01-01T00:00:00Z"}
    with gate.request_capability_scope(cap):
        res = gate.lybra_queue_adopt_dry_run({"task_id": TASK, "branch": "x", "actor": DRIVER, "owner_policy_ref": POLICY,
                                              "autonomy_mode": "Supervised"})
    payload = res["structuredContent"]
    _show(f"[件①·门·Supervised 拒] {payload['error_code']}: {payload['message']}")
    assert payload["error_code"] == "ADOPT_ENVELOPE_REQUIRED" and "一段式信封" in payload["message"]
    verbs = load_schema("verbs")["verbs"]
    transitions = load_schema("transitions")
    adoption = transitions["nodes"]["N1"]["adoption"]
    assert verbs["lybra_queue_adopt_dry_run"]["verb_family"] == adoption["verb_family"] == "claim"
    assert "claim" in verbs["lybra_loop"]["envelope"]["allowed_verbs"]
    assert transitions["queue_mutations"]["transitions"]["adopt"]["from_states"] == ["claimed"]
    assert verbs["lybra_queue_adopt_dry_run"]["required_scope"] == verbs["lybra_queue_claim_dry_run"]["required_scope"]
    assert "lybra_queue_adopt_dry_run" in gate.TOOL_HANDLERS


def test_item1_single_implementations_reused():
    """复用唯一实现: 收编建树 = _ensure_worktree, 卡面 = _prepare_claim(transition_engine), 校验 = validator.REQUIRED_FIELDS /
    validate_single_task, 记录 = _mcp_claim_record_plan / _write_mcp_claim_records; 无第二建树/第二记录 writer。"""
    qm = (REPO_ROOT / "tools/aipos_cli/queue_mutation.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(qm).body if isinstance(n, ast.FunctionDef) and n.name == "adopt_queue_task")
    called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert {"_ensure_worktree", "_prepare_claim", "validate_single_task", "card_worktree_location", "card_branch_name"} <= called, called
    assert "git" not in {c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    ba = (REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(ba).body if isinstance(n, ast.FunctionDef) and n.name == "adopt_task")
    called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert {"adopt_queue_task", "_mcp_claim_record_plan", "_write_mcp_claim_records"} <= called, called


# ===========================================================================
# 件② project set-paths: 正反例原文与 project.json diff; 与 set-repos 同一写路径与锁
# ===========================================================================

def _set_paths(rig, pairs: list[tuple[str, str]], *, confirm: bool) -> tuple[int, str, str]:
    argv = ["project", "set-paths", rig.gov.name, "--home-root", str(rig.gov.parent)]
    for key, value in pairs:
        argv += ["--key", key, "--value", value]
    return _cli(argv + (["--confirm"] if confirm else ["--dry-run"]))


def test_item2_set_paths_dry_run_diff_then_confirm_and_reader_sees_it(rig):
    from tools.aipos_cli.workspace_config import project_paths

    pj = rig.gov / "project.json"
    original = pj.read_bytes()
    pairs = [("finalize_mode", "external"), ("return_root", "5_tasks/records/returns"), ("manual_gate_mode", "true")]
    rc, out, err = _set_paths(rig, pairs, confirm=False)
    _show(f"[件②·set-paths --dry-run] rc={rc}\n{out}{err}")
    assert rc == 0 and "预览(未写" in out and '+    "finalize_mode": "external"' in out and "(写入后)" in out
    assert pj.read_bytes() == original, "dry-run 零写入"
    rc, out, err = _set_paths(rig, pairs, confirm=True)
    _show(f"[件②·set-paths --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and "已写入" in out
    data = json.loads(pj.read_text(encoding="utf-8"))
    _show(f"[件②·写后 project.json] {json.dumps(data, ensure_ascii=False, sort_keys=True)}")
    assert data["paths"] == {"finalize_mode": "external", "return_root": "5_tasks/records/returns", "manual_gate_mode": True}
    assert data["project"] == "lybra" and data["code_repo"] == str(rig.code_repo), "其余键原样保留"
    paths = project_paths(rig.gov)
    assert paths["finalize_mode"] == "external" and paths["manual_gate_mode"] is True
    assert paths["return_root"] == rig.gov / "5_tasks" / "records" / "returns"
    rc, out, _err = _set_paths(rig, [("finalize_mode", "external")], confirm=True)
    assert rc == 0 and "无改动" in out


def test_item2_set_paths_rejections_list_all_and_leave_project_json(rig):
    pj = rig.gov / "project.json"
    original = pj.read_bytes()
    cases = [
        ([("verdict_dir", "x"), ("queue_rot", "y")], "PATHS_KEY_UNKNOWN", ["verdict_dir", "queue_rot", "可写键:"]),
        ([("finalize_mode", "remote"), ("manual_gate_mode", "yes"), ("return_root", "")], "PATHS_VALUE_INVALID",
         ["finalize_mode='remote' 不在声明值域 ['internal', 'external']", "manual_gate_mode='yes' 须为 true 或 false", "return_root 的值须为非空单行串"]),
        ([("queue_root", "a"), ("queue_root", "b")], "PATHS_VALUE_INVALID", ["queue_root 重复给出"]),
    ]
    for pairs, code, needles in cases:
        rc, out, err = _set_paths(rig, pairs, confirm=True)
        _show(f"[件②·拒 {code}] rc={rc}\n{out}{err}")
        assert rc == 1 and code in err and "project.json 未改动" in err
        for needle in needles:
            assert needle in err, needle
        assert pj.read_bytes() == original
    rc, out, err = _cli(["project", "set-paths", rig.gov.name, "--home-root", str(rig.gov.parent), "--key", "queue_root", "--confirm",
                         "--key", "return_root", "--value", "x"])
    assert rc == 2 and "成对" in err and pj.read_bytes() == original


def test_item2_single_project_json_writer_with_lock_and_restore(rig, monkeypatch):
    """set-repos / set-workstation / set-paths 同一写路径 update_project_json(锁 + 写后复核, 不合 = 还原原文); 模块内无第二处写 project.json。"""
    from tools.aipos_cli import workspace_config as wc

    src = (REPO_ROOT / "tools/aipos_cli/workspace_config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for name in ("set_project_repos", "set_project_workstation", "set_project_paths"):
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "update_project_json" in calls, name
        assert "write_text" not in ast.unparse(fn), name
    writer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "update_project_json")
    # AIPOS-F135 件①: 锁收为唯一实现 workspace_config.exclusive_flock(fcntl.flock LOCK_EX), project.json 写路径经它加锁
    assert "exclusive_flock(handle)" in ast.unparse(writer)
    locker = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "exclusive_flock")
    assert "fcntl.flock" in ast.unparse(locker) and "LOCK_EX" in ast.unparse(locker)

    pj = rig.gov / "project.json"
    original = pj.read_text(encoding="utf-8")

    def _boom(_root, project=None):
        if project is None:  # AIPOS-F125: verify 写前以 project=<将写入内容> 预检; 本例只让写后复核失败, 测还原原文
            raise ValueError("verify failed")

    with pytest.raises(ValueError, match="verify failed"):
        wc.update_project_json(rig.gov, lambda d: d.update({"paths": {"queue_root": "zzz"}}), verify=_boom)
    assert pj.read_text(encoding="utf-8") == original, "复核不过 = 还原原文"

    # set-repos 回归: 经同一写路径, 仍按 project_repos 复核
    declared = wc.set_project_repos(rig.gov.parent, rig.gov.name, {"main": str(rig.code_repo)}, default="main")
    assert declared["default"] == "main" and json.loads(pj.read_text(encoding="utf-8"))["repos"]["items"]["main"] == str(rig.code_repo)
    with pytest.raises(wc.CardRepoUnresolved):
        wc.set_project_repos(rig.gov.parent, rig.gov.name, {"main": "relative/path"}, default="main")
    assert json.loads(pj.read_text(encoding="utf-8"))["repos"]["items"]["main"] == str(rig.code_repo)


# ===========================================================================
# 件③ 接入向导「接入既有人肉项目」一节: 顺序(AIPOS-F124 件②纠正)落点声明 → 冻结 → 收编; 本卡命令占位换值后过 build_parser
# ===========================================================================

def test_item3_onboarding_guide_legacy_section_order_and_commands_parse(tmp_path):
    import shlex

    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.onboarding import format_guide_text, generate_onboarding_guide

    guide = generate_onboarding_guide("probe_proj", home_root=str(tmp_path), code_repo=str(tmp_path / "repo"), host_segment="h")
    legacy = guide["legacy_onboarding"]
    text = format_guide_text(guide)
    _show("[件③·接入向导节]\n" + text[text.index("═══ 附: 接入既有人肉项目"):])
    assert [item["order"] for item in legacy] == [1, 2, 3]
    assert [w for item in legacy for w in ("freeze-legacy", "queue adopt", "set-paths") if w in item["command"]] == \
        ["set-paths", "freeze-legacy", "queue adopt"], "顺序 = 落点声明 → 冻结 → 收编(AIPOS-F124 件②)"
    subs = {"<TASK_ID>": "PROJ-7", "<LEGACY_BRANCH>": "feature/x", "<PATHS_KEY>": "finalize_mode", "<PATHS_VALUE>": "external",
            "<FREEZE_REASON>": "migration"}
    parsed = []
    for item in (legacy[0], legacy[2]):
        for line in item["command"].split("\n"):
            if not line.startswith("lybra ") or line.startswith("lybra project set-meta "):  # set-meta 行另由 F127 夹具核(随声明出现)
                continue
            for k, v in subs.items():
                line = line.replace(k, v)
            parsed.append(build_parser().parse_args(shlex.split(line)[1:]))
    assert [(a.command, getattr(a, "queue_command", None) or getattr(a, "project_command", None)) for a in parsed] == [
        ("project", "set-paths"), ("project", "set-paths"), ("queue", "adopt"), ("queue", "adopt"), ("loop", None)]
    assert parsed[1].confirm and not parsed[0].confirm and parsed[3].confirm and not parsed[2].confirm
    assert "probe_proj" not in json.dumps([k for k in guide if k != "project_name"])  # 无写死项目键
