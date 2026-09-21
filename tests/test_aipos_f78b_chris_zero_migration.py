"""AIPOS-F78B — chris 形零迁移收口三件 + 授权执行实撞(件④) + F73E 顺手实撞(件⑤)夹具(靶场分根: tmp 治理根, schema 从产品根读)。

件① 卡查找单一实现 task_loader.find_task_card: 扫 queue 各状态目录按 frontmatter task_id 精确匹配, 文件名不限(chris 形带 slug /
    lybra 形 <id>.md 各一), 多义=拒; next / ingest --dry-run / loop 都按此找到卡; 所有 `<id>.md` 拼接查找删除。
件② project.json paths.finalize_mode(internal|external): external 时 N4 PASS 后不派生 lybra finalize, 等 FINALIZE 卡 Return
    (ID 规则 <ID>-FINALIZE / 卡面 finalize_task_id, 一处声明 transitions artifact_ingest.finalization), ingest 读其 frontmatter
    (merge_commit/remote_ref/deploy_status)铸 finalization 记录(同一 writer), 随后 close 证据自填; loop 一条链 verdict→completed。
件③ 驱动方一阶段: return/verdict/close 的 MCP dry_run 与 CLI 薄壳在 autonomy_mode=PreAuthorized + owner_autonomy_policy 信封
    (agent_or_role 覆盖驱动方实例/角色名/角色类, allowed_verbs 读 verbs.schema lybra_loop.envelope)匹配时一阶段落记录, 不索 owner_confirm;
    无信封 exit 5。
件④ a) roles register 调既有 lybra_roles_register(dry_run/confirm 死对删除) b) owner_decision 按 workspace_root 落盘
    c) enroll-list 与门注册表口径统一(记录 governance_root)。
件⑤ a) transitions N6 location=写侧真值 close_ b) finalize actor==claimer c) F28 活体用例只比指纹 d) 缺 claim 记录出口 = lybra state repair。
靶场/替身复用 F73D/F73E/F78 夹具(禁第二份靶场)。
"""
from __future__ import annotations

import inspect
import io
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import (  # noqa: E402  — 靶场/替身唯一来源
    AUDITOR,
    DRIVER,
    EXEC,
    POLICY,
    GateDouble,
    _claim_record,
    _fm,
    _policy,
    _ts,
    _write,
)
from test_aipos_f73e_ledger_identity import _closable_root, _evidence  # noqa: E402
from test_aipos_f78_engine_agnostic import (  # noqa: E402
    CHRIS_EXEC,
    _branch_with_commit,
    _connection,
    _init_product_repo,
    _return_text,
)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import INGEST_EXIT_REJECTED, ingest_task_artifact, validate_task_artifact  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.next_resolver import derive_next_step, execute_derived_action, finalize_task_id_for  # noqa: E402
from tools.aipos_cli.record_writer import CLOSURE_ID_PREFIX  # noqa: E402
from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_by_id, find_task_card  # noqa: E402
from tools.aipos_cli.workspace_config import project_paths  # noqa: E402

TASK = "HBJ-F78B-1"
SLUG_FILE = "hbj-f78b-1-mac-module-validation.md"  # chris 形: 文件名带标题 slug
FIN = f"{TASK}-FINALIZE"
SHA_M = "d" * 40
PROJECT = "chris-huibojin"


# ---------------------------------------------------------------------------
# 靶场(chris 形: slug 文件名, Return 在 records/returns, 裁决在 records/audit_verdicts, 可声明 finalize_mode)
# ---------------------------------------------------------------------------

def _chris_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, finalize_mode: str | None = None, git: bool = True) -> Path:
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    root = tmp_path / "gov-chris"
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events", "owner_decisions"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True)
    (root / "5_tasks" / "policies").mkdir()
    (root / "task_cards").mkdir()
    (root / "governance" / "decision_log").mkdir(parents=True)
    (root / "governance" / "stage_archives").mkdir(parents=True)
    _write(root / "governance" / "decision_log" / "2026-09.md", "# 2026-09\n")
    _write(root / "governance" / "stage_archives" / "2026-09-21_stage.md", "# stage\n")
    code_repo = _init_product_repo(tmp_path / "product-chris") if git else (tmp_path / "product-chris")
    code_repo.mkdir(exist_ok=True)
    paths = {"return_root": "5_tasks/records/returns", "verdict_root": "5_tasks/records/audit_verdicts",
             "queue_root": "5_tasks/queue", "task_cards_root": "task_cards"}
    if finalize_mode:
        paths["finalize_mode"] = finalize_mode
    _write(root / "project.json", json.dumps({"project": PROJECT, "code_repo": str(code_repo), "config_version": 1, "paths": paths}))
    _write(root / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    return root


def _slug_card(gov: Path, task_id: str, queue: str, filename: str, *, assigned: str = CHRIS_EXEC, task_mode: str = "code",
               extra: dict | None = None) -> Path:
    meta = {"task_id": task_id, "harness": "pi", "title": f"{task_id} chris shape", "project": PROJECT, "task_mode": task_mode,
            "assigned_to": assigned, "agent_instance": assigned, "status": queue, "audit": "required", "audit_by": AUDITOR,
            "priority": "high", "created_by": DRIVER, "needs_owner": False, "context_bundle": "t", "artifact_policy": "formal_write",
            "output_target": "Modules/"}
    if queue == "claimed":  # 门认领后的卡面字段(close_task 判据读它们)
        meta.update({"claimed_by": assigned, "claimed_at": "'2026-09-21T00:00:00Z'", "claim_id": f"claim_{task_id}_x",
                     "active_session_id": f"session_{task_id}_x"})
    meta.update(extra or {})
    path = gov / "5_tasks" / "queue" / queue / filename
    _write(path, _fm(meta, f"# {task_id}\n\n## 目标\n按卡执行。\n"))
    return path


def _chris_policy(gov: Path, *, agent_or_role: str) -> None:
    """chris 真实信封形(pol_chris_loop_1): agent_or_role=自定义角色名, task_selector_project=项目, task_mode 任意。"""
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{POLICY}.md", build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role=agent_or_role, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="envelope-test", task_selector_task_mode="", task_selector_project=PROJECT))


def _n4_pass_records(gov: Path, task_id: str, executor: str) -> None:
    """执行卡推到 N4 PASS(无 finalization): return/dispatch/verdict(PASS) 记录齐, 审计卡在 completed。"""
    rec = gov / "5_tasks" / "records"
    _write(rec / "returns" / task_id / f"return_{task_id}_{_ts()}_{executor}.md",
           _fm({"record_type": "return_record", "task_id": task_id, "return_id": f"return_{task_id}_x", "agent_instance": executor,
                "returned_at": "2026-09-21T01:00:00Z", "return_status": "returned"}))
    _write(rec / "audit_dispatches" / f"{task_id}R" / "dispatch_x.md",
           _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": task_id, "dispatched_at": "2026-09-21T02:00:00Z"}))
    _write(rec / "audit_verdicts" / task_id / "verdict_x.md",
           _fm({"record_type": "audit_verdict_record", "verdict_id": f"verdict_{task_id}_x", "verdict": "PASS",
                "reviewed_task_id": task_id, "auditor_instance": AUDITOR, "verdict_at": "2026-09-21T03:00:00Z"}))
    _slug_card(gov, f"{task_id}R", "completed", f"{task_id.lower()}r-independent-audit.md", assigned=AUDITOR, task_mode="audit",
               extra={"reviewed_task_id": task_id})


def _finalize_return(gov: Path, fin_id: str, meta: dict) -> Path:
    path = gov / "5_tasks" / "records" / "returns" / fin_id / "return-hbj-advisor-20260921T150000Z.md"
    _write(path, _fm({"task_id": fin_id, **meta}, f"# {fin_id} Return\n\n## 一句话结论\n已合入并推送。\n"))
    return path


def _payload(res: dict) -> dict:
    """门动词结果: MCP 包装({content, structuredContent, isError}) → 取 structuredContent; 裸 dict 原样。"""
    return res.get("structuredContent") if isinstance(res, dict) and isinstance(res.get("structuredContent"), dict) else res


def _error_code(res: dict) -> str:
    res = _payload(res)
    code = str(res.get("error_code") or "")
    if not code:
        errs = res.get("errors") or []
        code = str(errs[0].get("category") or "") if errs and isinstance(errs[0], dict) else ""
    return code


# ---------------------------------------------------------------------------
# 件① 卡查找单一实现
# ---------------------------------------------------------------------------

def test_f78b_item1_find_task_card_by_frontmatter_both_filename_shapes_and_ambiguity(tmp_path, monkeypatch):
    gov = _chris_gov(tmp_path, monkeypatch, git=False)
    slug = _slug_card(gov, TASK, "claimed", SLUG_FILE)
    plain = _slug_card(gov, "AIPOS-F78B-L", "pending", "aipos-f78b-l.md", assigned=EXEC)
    assert find_task_card(gov, TASK) == (slug, "claimed")  # chris 形: 文件名 ≠ <id>.md
    assert find_task_card(gov, "AIPOS-F78B-L") == (plain, "pending")  # lybra 形
    assert find_task_card(gov, "HBJ-NOPE") == (None, None)
    assert find_task_card(gov, TASK, states=("pending",)) == (None, None)
    task, matches = find_task_by_id(TASK, gov)  # 字典形同一规则
    assert task and task["path"].endswith(SLUG_FILE) and len(matches) == 1
    # 坏 YAML 卡(queue repair 场景): 仍按 frontmatter task_id 行匹配, 不按文件名
    bad = gov / "5_tasks" / "queue" / "blocked" / "hbj-f78b-bad-some-slug.md"
    _write(bad, "---\ntask_id: HBJ-F78B-BAD\ntitle: [unclosed\n---\n# x\n")
    assert find_task_card(gov, "HBJ-F78B-BAD") == (bad, "blocked")
    # 多义 = 拒(推导核不可推导点名两份文件)
    _slug_card(gov, TASK, "pending", "hbj-f78b-1-duplicate.md")
    with pytest.raises(AmbiguousTaskCard):
        find_task_card(gov, TASK)
    d = derive_next_step(TASK, gov)
    assert d["derivable"] is False and d["current_state"] == "ambiguous" and "duplicate" in d["missing_records"][0]
    chk = validate_task_artifact(TASK, gov)
    assert chk["ok"] is False and chk["category"] == "TASK_AMBIGUOUS"


def test_f78b_item1_next_ingest_loop_all_find_slug_card(tmp_path, monkeypatch):
    """验收①: chris 形靶场 next / ingest --dry-run / loop 均按 frontmatter 找到卡(原 TASK_NOT_FOUND)。"""
    from tools.aipos_cli.aipos_cli import main

    gov = _chris_gov(tmp_path, monkeypatch)
    _slug_card(gov, TASK, "claimed", SLUG_FILE)
    _claim_record(gov, TASK, CHRIS_EXEC)
    _chris_policy(gov, agent_or_role=DRIVER)
    code_repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    sha, tree = _branch_with_commit(code_repo, TASK, rel="Modules/x.swift")
    _write(gov / "5_tasks" / "records" / "returns" / TASK / "return-kiwiaiops-20260921T132500Z.md", _return_text(TASK, sha, tree))
    # next(推导核)
    d = derive_next_step(TASK, gov)
    assert d["derivable"] and d["verb"] == "lybra_queue_return_dry_run" and d["current_state"] == "claimed", d
    assert f"--actor {CHRIS_EXEC}" in d["command"] and "return-kiwiaiops-20260921T132500Z.md" in d["command"]
    assert "--autonomy-mode PreAuthorized" in d["command"] and f"--owner-policy-ref {POLICY}" in d["command"]  # 件③ 驱动方信封在
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    rc_next = main(["next", "--task-id", TASK, "--workspace-root", str(gov), "--json"])
    rc_ingest = main(["artifact", "ingest", "--task-id", TASK, "--workspace-root", str(gov), "--dry-run", "--json"])
    monkeypatch.setattr(sys, "stdout", sys.__stdout__)
    out = buf.getvalue()
    assert rc_next == 0 and '"derivable": true' in out and "找不到任务卡" not in out and "TASK_NOT_FOUND" not in out, out
    assert rc_ingest == 0 and '"category": "DRY_RUN"' in out and "lybra queue return" in out, out
    assert "token" not in out.lower()
    # loop: 卡找到 → 第一步 return 经门(替身), 第二步派审; 不再 TASK_NOT_FOUND
    gate = GateDouble(gov)
    lo = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=lo, execute=gate, interval=0.02, max_wait=1, max_steps=2)
    assert [c[0] for c in gate.calls] == ["return", "dispatch"], (gate.calls, lo.getvalue())
    assert "找不到任务卡" not in lo.getvalue() and res.exit_code == exit_code_for(load_loop_contract(), "wait_timeout")


def test_f78b_item1_single_lookup_no_filename_concatenation_left():
    """Δ=-1: `<id>.md` 文件名拼接这条隐式约定删除——查找口全部经 task_loader.find_task_card。"""
    lookup_sites = [
        "tools/aipos_cli/next_resolver.py", "tools/aipos_cli/artifact_ingest.py", "tools/aipos_cli/loop_driver.py",
        "tools/aipos_cli/queue_mutation.py", "tools/aipos_cli/flow_description.py", "tools/aipos_cli/state_lint.py",
        "tools/aipos_cli/deployment_authorization.py", "tools/aipos_cli/advisor_pump.py", "tools/turn_advancer/state_reader.py",
        "tools/aipos_cli/finalize.py", "tools/aipos_cli/card_render.py", "tools/aipos_cli/board_adapter.py",
    ]
    pattern = re.compile(r'f"\{(task_id|tid|card_id|audit_id)(\.lower\(\))?\}\.md"')
    for rel in lookup_sites:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not pattern.search(text), f"{rel}: 仍按 <id>.md 文件名拼接查找"
    loader = (REPO_ROOT / "tools/aipos_cli/task_loader.py").read_text(encoding="utf-8")
    assert loader.count("def find_task_card(") == 1 and loader.count("def find_task_card_matches(") == 1
    for rel in lookup_sites:
        if rel.endswith(("card_render.py", "loop_driver.py", "artifact_ingest.py")):
            continue  # 经 next_resolver._find_task_in_queue(下方断言其为 find_task_card 的调用点)
        assert "find_task_card" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel
    src = inspect.getsource(nr._find_task_in_queue)
    assert "find_task_card(" in src and "queue_root /" not in src and ".lower()" not in src


# ---------------------------------------------------------------------------
# 件② finalize_mode: external
# ---------------------------------------------------------------------------

def test_f78b_item2_declarations_single_source_and_internal_default(tmp_path, monkeypatch):
    text = (REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8")
    transitions = json.loads(text)
    fin = transitions["artifact_ingest"]["finalization"]
    assert fin["finalize_task_id"] == {"card_field": "finalize_task_id", "suffix": "-FINALIZE", "rule": fin["finalize_task_id"]["rule"]}
    assert fin["required_frontmatter"] == ["merge_commit", "remote_ref", "deploy_status"] and fin["root_key"] == "return_root"
    assert text.count('"finalize_task_id": {') == 1, "FINALIZE 卡 ID 规则只声明一处"
    assert transitions["nodes"]["N5"]["finalize_mode"]["values"] == ["internal", "external"]
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    decl = config["configuration_sources"]["project_json"]["schema"]["paths"]["schema"]["finalize_mode"]
    assert decl["default"] == "internal" and decl["enum"] == ["internal", "external"]
    # 缺省 internal = 现行(推导核仍派生 lybra finalize)
    gov = _chris_gov(tmp_path, monkeypatch, git=False)
    assert project_paths(gov)["finalize_mode"] == "internal"
    _slug_card(gov, TASK, "claimed", SLUG_FILE)
    _claim_record(gov, TASK, CHRIS_EXEC)
    _n4_pass_records(gov, TASK, CHRIS_EXEC)
    d = derive_next_step(TASK, gov)
    assert d["derivable"] and d["verb"] == "lybra_finalize" and d["command"].startswith("lybra finalize "), d
    # 值域外 = fail-closed
    project = json.loads((gov / "project.json").read_text())
    project["paths"]["finalize_mode"] = "remote"
    (gov / "project.json").write_text(json.dumps(project), encoding="utf-8")
    with pytest.raises(ValueError, match="finalize_mode"):
        project_paths(gov)
    # ID 规则: 卡面声明优先, 缺省 <ID>-FINALIZE
    assert finalize_task_id_for(TASK, {}) == FIN
    assert finalize_task_id_for(TASK, {"finalize_task_id": "HBJ-FIN-9"}) == "HBJ-FIN-9"


def test_f78b_item2_external_chain_verdict_await_finalize_return_ingest_record_close(tmp_path, monkeypatch):
    """验收②: external 项目 verdict PASS → 等 FINALIZE Return → ingest 铸 finalization 记录 → close 证据自填。"""
    gov = _chris_gov(tmp_path, monkeypatch, finalize_mode="external", git=False)
    card = _slug_card(gov, TASK, "claimed", SLUG_FILE)
    _claim_record(gov, TASK, CHRIS_EXEC)
    _chris_policy(gov, agent_or_role=DRIVER)
    _n4_pass_records(gov, TASK, CHRIS_EXEC)
    assert project_paths(gov)["finalize_mode"] == "external"
    assert finalize_task_id_for(TASK, nr._read_frontmatter(card)) == FIN
    # ① 无 FINALIZE Return: 不派生 lybra finalize, await_artifact(kind=finalize_return)
    d = derive_next_step(TASK, gov)
    assert d["derivable"] is False and d["verb"] == "lybra_finalize" and d["command"] == "", d
    assert d["action"] == {"type": "await_artifact", "card": FIN, "kind": "finalize_return"}
    assert FIN in d["missing_records"][0] and "5_tasks/records/returns" in d["missing_records"][0]
    chk = validate_task_artifact(TASK, gov)
    assert chk["kind"] == "finalization" and chk["category"] == "INGEST_RETURN_MISSING" and chk["exit_code"] == INGEST_EXIT_REJECTED
    # ② Return 落盘但缺 remote_ref → artifact_invalid 硬停(loop exit 4 不空等)
    ret = _finalize_return(gov, FIN, {"merge_commit": SHA_M, "deploy_status": "deployed"})
    d2 = derive_next_step(TASK, gov)
    assert d2["action"]["type"] == "artifact_invalid" and d2["action"]["card"] == FIN and any("remote_ref" in m for m in d2["missing_records"]), d2
    assert validate_task_artifact(TASK, gov)["category"] == "INGEST_FRONTMATTER_MISSING"
    # deploy_status 不在 N5 值域 → 同样硬停
    _finalize_return(gov, FIN, {"merge_commit": SHA_M, "remote_ref": "origin/main", "deploy_status": "shipped"})
    d3 = derive_next_step(TASK, gov)
    assert d3["action"]["type"] == "artifact_invalid" and any("deploy_status" in m for m in d3["missing_records"]), d3
    assert validate_task_artifact(TASK, gov)["category"] == "INGEST_DEPLOY_STATUS_INVALID"
    # ③ 合规 → 派生 artifact ingest(action_type=finalize, verb lybra_finalize)
    _finalize_return(gov, FIN, {"merge_commit": SHA_M, "remote_ref": f"origin/main@{SHA_M}", "deploy_status": "not_attempted"})
    d4 = derive_next_step(TASK, gov)
    assert d4["derivable"] and d4["verb"] == "lybra_finalize", d4
    assert d4["command"] == f"lybra artifact ingest --task-id {TASK} --workspace-root {gov}"
    assert nr._action_type_for_command(d4["command"]) == "finalize"
    dry = ingest_task_artifact(TASK, gov, dry_run=True)
    assert dry["ok"] and dry["kind"] == "finalization" and dry["category"] == "DRY_RUN" and dry["path"] == str(ret), dry
    assert not list((gov / "5_tasks" / "records" / "finalizations").glob("*/*.md"))
    res = ingest_task_artifact(TASK, gov)
    assert res["ok"] and res["category"] == "OK" and res["exit_code"] == 0, res
    fins = list((gov / "5_tasks" / "records" / "finalizations" / TASK).glob("finalization_*.md"))
    assert len(fins) == 1, fins
    fm = nr._read_frontmatter(fins[0])
    assert fm["record_type"] == "finalization_record" and fm["merge_commit"] == SHA_M and fm["commit"] == SHA_M
    assert fm["remote_ref"] == f"origin/main@{SHA_M}" and fm["deploy_status"] == "not_attempted" and fm["deployed"] is False
    assert fm["actor"] == CHRIS_EXEC and fm["authorization_type"] == "verdict_ref" and fm["authorization_ref"] == f"verdict_{TASK}_x"
    assert fm["finalize_return_ref"] == f"5_tasks/records/returns/{FIN}/return-hbj-advisor-20260921T150000Z.md"
    # ④ 随后 close 证据自填(finalize_commit_hash = merge_commit), 驱动方信封在 → 一阶段形
    d5 = derive_next_step(TASK, gov)
    assert d5["derivable"] and d5["verb"] == "lybra_queue_close_dry_run", d5
    assert f'"finalize_commit_hash": "{SHA_M}"' in d5["command"] and f"--actor {CHRIS_EXEC}" in d5["command"]
    assert "--confirm --autonomy-mode PreAuthorized" in d5["command"] and f"--owner-policy-ref {POLICY}" in d5["command"]
    # 幂等: finalization 已在 → 产物入口不再当 finalization 读, 也不重铸
    again = ingest_task_artifact(TASK, gov, dry_run=True)
    assert again["ok"] is False and again["kind"] == "return", again
    assert len(list((gov / "5_tasks" / "records" / "finalizations" / TASK).glob("finalization_*.md"))) == 1


class _ExternalGate:
    """external 链的门侧替身: finalize 步 = 真产品 artifact ingest(进程内, 同一 writer); close 步 = 真产品 close_task。"""

    def __init__(self, gov: Path):
        self.gov = gov
        self.calls: list[tuple[str, str]] = []

    def __call__(self, derivation: dict, workspace_root: Path, connection_json=None) -> dict:
        from tools.aipos_cli.board_adapter import close_task

        cmd = derivation["command"]
        action = nr._action_type_for_command(cmd)
        card = derivation["task_id"]
        self.calls.append((action, card))
        if action == "finalize":
            assert "artifact ingest" in cmd and "lybra finalize" not in cmd
            res = ingest_task_artifact(card, self.gov)
            return {"ok": bool(res["ok"]), "action_type": action, "message": res["message"], "command": cmd,
                    "exit_code": res["exit_code"], "output": res["output"]}
        if action == "close":
            argv = shlex.split(cmd)
            evidence = json.loads(argv[argv.index("--closure-evidence") + 1])
            res = close_task(task_id=card, actor=argv[argv.index("--actor") + 1], closure_evidence=evidence, dry_run=False,
                             repo_root=self.gov, submitted_by=DRIVER)
            return {"ok": bool(res.get("ok")) and res.get("verdict") != "BLOCK", "action_type": action, "message": str(res.get("verdict")),
                    "command": cmd, "exit_code": 0 if res.get("ok") else 1, "output": json.dumps(res.get("blocking_reasons") or [])}
        raise AssertionError(f"unexpected action {action}: {cmd}")


def test_f78b_item2_external_chain_loop_waits_finalize_return_then_completed(tmp_path, monkeypatch):
    gov = _chris_gov(tmp_path, monkeypatch, finalize_mode="external", git=False)
    _slug_card(gov, TASK, "claimed", SLUG_FILE)
    _claim_record(gov, TASK, CHRIS_EXEC)
    _chris_policy(gov, agent_or_role=DRIVER)
    _n4_pass_records(gov, TASK, CHRIS_EXEC)
    gate = _ExternalGate(gov)
    out = io.StringIO()

    def external_finalizer() -> None:  # chris 侧 FINALIZE 卡执行体: loop 等待期间落 Return
        time.sleep(0.15)
        _finalize_return(gov, FIN, {"merge_commit": SHA_M, "remote_ref": f"origin/main@{SHA_M}", "deploy_status": "deployed"})

    t = threading.Thread(target=external_finalizer, daemon=True)
    t.start()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=gate, interval=0.03, max_wait=8, max_steps=10)
    t.join(timeout=10)
    text = out.getvalue()
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert [c[0] for c in gate.calls] == ["finalize", "close"], gate.calls
    waits = [s for s in res.steps if s.kind == "wait"]
    assert waits and waits[0].card == FIN and any(f"5_tasks/records/returns/{FIN}/return-*.md" == a for a in waits[0].artifacts), waits
    assert "lybra finalize " not in text
    rec = gov / "5_tasks" / "records"
    assert list((rec / "finalizations" / TASK).glob("finalization_*.md")) and list((rec / "closures" / TASK).glob(f"{CLOSURE_ID_PREFIX}_*.md"))
    assert (gov / "5_tasks" / "queue" / "completed" / SLUG_FILE).is_file()  # 目录/文件名零改动, 只是位置
    # 等待超时: 出口点名 FINALIZE 卡产物(exit 3)
    gov2 = _chris_gov(tmp_path / "second", monkeypatch, finalize_mode="external", git=False)
    _slug_card(gov2, TASK, "claimed", SLUG_FILE)
    _claim_record(gov2, TASK, CHRIS_EXEC)
    _chris_policy(gov2, agent_or_role=DRIVER)
    _n4_pass_records(gov2, TASK, CHRIS_EXEC)
    res2 = run_loop(TASK, gov2, actor=DRIVER, out=io.StringIO(), execute=_ExternalGate(gov2), interval=0.02, max_wait=0.15, max_steps=3)
    assert res2.exit_code == exit_code_for(load_loop_contract(), "wait_timeout") and FIN in res2.message, res2


# ---------------------------------------------------------------------------
# 件③ 驱动方一阶段
# ---------------------------------------------------------------------------

def test_f78b_item3_gate_driver_envelope_matcher_no_envelope_vs_envelope(tmp_path, monkeypatch):
    from tools.mcp_server import tools as gate

    gov = _chris_gov(tmp_path, monkeypatch, git=False)
    _slug_card(gov, TASK, "claimed", SLUG_FILE)
    _slug_card(gov, "HBJ-F78B-P", "pending", "hbj-f78b-p-next-card.md")
    cap = {"role": "hbj-advisor", "role_class": "advisor", "agent_instance": DRIVER, "token_ref": "t", "expires_at": "2999-01-01T00:00:00Z"}
    monkeypatch.setattr(gate, "_capability_token", lambda: cap)
    # 无信封: 每个动词都拒, 出口 = envelope_guards 声明
    for verb in ("return", "verdict", "close"):
        pid, err = gate._match_driver_envelope(gov, owner_policy_ref=POLICY, task_id=TASK, verb=verb)
        assert pid is None and _error_code(err) == "ENVELOPE_POLICY_NOT_FOUND", (verb, err)
    matched, _s, _e, _q = gate._match_claim_envelope(gov, owner_policy_ref=POLICY, task_id="HBJ-F78B-P", task_path=None,
                                                    canonical_agent_instance=CHRIS_EXEC, actor=CHRIS_EXEC)
    assert matched is None
    # 有信封(chris 真实形: agent_or_role=自定义角色名 hbj-advisor, task_selector_project): 四动词各放行
    _chris_policy(gov, agent_or_role="hbj-advisor")
    for verb in ("return", "verdict", "close"):
        pid, err = gate._match_driver_envelope(gov, owner_policy_ref=POLICY, task_id=TASK, verb=verb)
        assert pid == POLICY and err is None, (verb, err)
    matched, _s, _e, _q = gate._match_claim_envelope(gov, owner_policy_ref=POLICY, task_id="HBJ-F78B-P", task_path=None,
                                                    canonical_agent_instance=CHRIS_EXEC, actor=CHRIS_EXEC)
    assert matched == POLICY
    # allowed_verbs 读 verbs.schema lybra_loop.envelope(唯一声明); 集合外动词拒
    pid, err = gate._match_driver_envelope(gov, owner_policy_ref=POLICY, task_id=TASK, verb="publish")
    assert pid is None and _error_code(err) == "ENVELOPE_VERB_NOT_ALLOWED"
    assert gate._loop_envelope_allowed_verbs() == set(load_loop_contract()["envelope"]["allowed_verbs"])
    # 非驱动方 token(执行体)不得走一阶段
    monkeypatch.setattr(gate, "_capability_token", lambda: {**cap, "role": "hbj-coder", "role_class": "executor", "agent_instance": CHRIS_EXEC})
    pid, err = gate._match_driver_envelope(gov, owner_policy_ref=POLICY, task_id=TASK, verb="return")
    assert pid is None and _error_code(err) == "ENVELOPE_DRIVER_ONLY"


def test_f78b_item3_gate_close_one_stage_lands_closure_without_owner_confirm_and_no_envelope_blocks(tmp_path, monkeypatch):
    from tools.mcp_server import tools as gate

    task_id = "AIPOS-F78B-C1"
    root = _closable_root(tmp_path, task_id)
    cap = {"role": "advisor", "role_class": "advisor", "agent_instance": DRIVER, "token_ref": "t", "expires_at": "2999-01-01T00:00:00Z"}
    monkeypatch.setattr(gate, "_capability_token", lambda: cap)
    monkeypatch.setattr(gate, "_capability_has_scope", lambda scope: False)  # 驱动方 token 无 queue_close scope(chris 实撞形)
    args = {"task_id": task_id, "actor": EXEC, "closure_evidence": _evidence(task_id), "autonomy_mode": "PreAuthorized",
            "owner_policy_ref": POLICY, "workspace_root": str(root)}
    # 无信封 → BLOCK(ENVELOPE_*), 不落记录(CLI 薄壳据此 exit 5)
    denied = gate.lybra_queue_close_dry_run(args)
    assert _error_code(denied) == "ENVELOPE_POLICY_NOT_FOUND", denied
    assert not list((root / "5_tasks" / "records" / "closures").glob("*/*.md"))
    # Supervised 无 scope 仍拒(存量判据不变)
    sup = _payload(gate.lybra_queue_close_dry_run({**args, "autonomy_mode": "Supervised"}))
    assert sup.get("ok") is False and "queue_close" in json.dumps(sup)
    # 有信封 → 一阶段落记录, 不索 owner_confirm; actor=认领实例, submitted_by=驱动方
    _policy(root)
    landed = _payload(gate.lybra_queue_close_dry_run(args))
    assert landed.get("ok") and landed.get("preauthorized_release") is True and landed.get("owner_confirmation_required") is False, landed
    assert landed["autonomy_mode"] == "PreAuthorized" and landed["owner_policy_ref"] == POLICY
    closures = list((root / "5_tasks" / "records" / "closures" / task_id).glob("*.md"))
    assert len(closures) == 1 and closures[0].name.startswith(f"{CLOSURE_ID_PREFIX}_{task_id}_"), closures
    fm = nr._read_frontmatter(closures[0])
    assert fm["actor"] == EXEC and fm["submitted_by"] == DRIVER
    assert (root / "5_tasks" / "queue" / "completed" / f"{task_id.lower()}.md").is_file()


def test_f78b_item3_gate_claim_and_return_one_stage_land_records_via_driver_token(tmp_path, monkeypatch):
    """claim + return 经门一阶段(真门动词处理器, 驱动方 token, 信封覆盖驱动方实例): 两条记录各落。"""
    from test_aipos_f78_engine_agnostic import _card as _lybra_card, _make_gov
    from tools.mcp_server import tools as gate

    task_id = "F78B-RET-1"
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _lybra_card(gov, task_id, "pending")
    _policy(gov)
    cap = {"role": "advisor", "role_class": "advisor", "agent_instance": DRIVER, "token_ref": "t", "expires_at": "2999-01-01T00:00:00Z"}
    monkeypatch.setattr(gate, "_capability_token", lambda: cap)
    claimed = _payload(gate.lybra_queue_claim_dry_run({"task_id": task_id, "actor": EXEC, "agent_instance": EXEC, "autonomy_mode": "PreAuthorized",
                                                       "owner_policy_ref": POLICY, "workspace_root": str(gov)}))
    assert claimed.get("preauthorized_release") is True, claimed
    assert list((gov / "5_tasks" / "records" / "claims" / task_id).glob("claim_*.md"))
    assert derive_next_step(task_id, gov)["current_state"] == "claimed"
    # 执行体产物: 分支有提交 + Return 落盘(门的 return 判据: 分支存在/completion_report_ref/artifact_refs)
    code_repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    sha, tree = _branch_with_commit(code_repo, task_id, rel="tests/test_f78b_x.py")  # code 卡门判据: 分支含测试改动
    _write(gov / "task_cards" / task_id / "RETURN.md", _return_text(task_id, sha, tree))
    monkeypatch.setattr(gate, "_capability_has_scope", lambda scope: False)  # return: 驱动方无 queue_return scope 亦放行(信封即授权)
    return_args = {
        "task_id": task_id, "actor": EXEC, "agent_instance": EXEC, "autonomy_mode": "PreAuthorized", "owner_policy_ref": POLICY,
        "result_summary": "done", "completion_report_ref": f"task_cards/{task_id}/RETURN.md",
        "artifact_refs": [f"task_cards/{task_id}/RETURN.md"], "workspace_root": str(gov)}
    returned = _payload(gate.lybra_queue_return_dry_run(return_args))
    assert returned.get("preauthorized_release") is True and returned.get("owner_confirmation_required") is False, returned
    ret_records = list((gov / "5_tasks" / "records" / "returns" / task_id).glob("return_*.md"))
    assert len(ret_records) == 1, returned
    fm = nr._read_frontmatter(ret_records[0])
    assert EXEC in (fm.get("actor"), fm.get("canonical_agent_instance"), fm.get("agent_instance")), fm  # actor=认领实例
    assert fm.get("submitted_by") == DRIVER and fm.get("confirmer_role") == "autonomy_policy:PreAuthorized", fm  # 提交身份=驱动方, 放行者=信封
    after = derive_next_step(task_id, gov)  # 记录已落 → 推导核前进(真门 return 会顺手派审, 故 N2 或 N3)
    assert after["current_node"] in ("return", "audit_dispatch") and after["current_state"] == "claimed", after
    # 无信封: 同一 return 调用 exit 路径 = BLOCK ENVELOPE_*(不回落 Supervised)
    (gov / "5_tasks" / "policies" / f"{POLICY}.md").unlink()
    denied = gate.lybra_queue_return_dry_run(return_args)
    assert _error_code(denied) == "ENVELOPE_POLICY_NOT_FOUND"
    assert len(list((gov / "5_tasks" / "records" / "returns" / task_id).glob("return_*.md"))) == 1


def test_f78b_item3_shell_factory_one_stage_exit5_without_envelope_and_lands_with(tmp_path):
    """CLI 薄壳(两阶段工厂)一阶段: 信封拒 → exit 5; 门回落 Supervised 预览(带 dry_run_token)→ exit 5 不谎报; 放行 → exit 0 只调一次。"""
    from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb

    conn = _connection(tmp_path, ["advisor"], bound={"advisor": DRIVER})
    no_env = exit_code_for(load_loop_contract(), "no_envelope")
    cases = [
        ("lybra_queue_return", {"task_id": "X", "actor": EXEC, "agent_instance": EXEC, "autonomy_mode": "PreAuthorized", "owner_policy_ref": POLICY, "result_summary": "s"}),
        ("lybra_audit_verdict", {"audit_task_id": "XR", "reviewed_task_id": "X", "actor": AUDITOR, "agent_instance": AUDITOR, "autonomy_mode": "PreAuthorized", "owner_policy_ref": POLICY, "verdict": "PASS"}),
        ("lybra_queue_close", {"task_id": "X", "actor": EXEC, "closure_evidence": {"verdict_ref": "v"}, "autonomy_mode": "PreAuthorized", "owner_policy_ref": POLICY}),
    ]
    for verb, args in cases:
        for resp, expected in (
            ({"verdict": "BLOCK", "error_code": "ENVELOPE_POLICY_NOT_FOUND", "errors": [{"category": "ENVELOPE_POLICY_NOT_FOUND"}]}, no_env),
            ({"verdict": "WARN", "dry_run_token": "drt_x", "owner_confirmation_required": True}, no_env),
            ({"verdict": "PASS", "ok": True, "preauthorized_release": True}, 0),
        ):
            with patch("tools.aipos_cli.two_phase_shell_factory.GateClient") as gc:
                client = MagicMock()
                gc.return_value = client
                client.call_tool.return_value = resp
                code, _ = execute_two_phase_verb(verb_base=verb, args_dict=args, connection_json_path=str(conn), role="advisor")
            assert code == expected, (verb, resp, code)
            assert client.call_tool.call_count == 1 and client.call_tool.call_args[0][0] == f"{verb}_dry_run"


def test_f78b_item3_next_run_ledger_verbs_without_envelope_exit5(tmp_path, monkeypatch):
    gov = _chris_gov(tmp_path, monkeypatch, git=False)
    _slug_card(gov, TASK, "claimed", SLUG_FILE)
    no_env = exit_code_for(load_loop_contract(), "no_envelope")
    # close(无产物入口校验)Supervised 形 → exit 5; return/verdict 的产物校验在前(F78 声明序), 其拒因仍是 INGEST_*(exit 4)
    cmd = f"lybra queue close --task-id {TASK} --actor {CHRIS_EXEC} --closure-evidence '{{}}'"
    res = execute_derived_action({"derivable": True, "task_id": TASK, "current_node": "finalize", "command": cmd}, gov, None)
    assert res["ok"] is False and res["exit_code"] == no_env and "lybra envelope mint" in res["output"], res
    vcmd = f"lybra audit-verdict --reviewed-task-id {TASK} --audit-task-id {TASK}R --actor {AUDITOR} --confirm --agent-instance {AUDITOR} --verdict PASS"
    vres = execute_derived_action({"derivable": True, "task_id": f"{TASK}R", "current_node": "audit_verdict", "command": vcmd}, gov, None)
    assert vres["ok"] is False and vres["exit_code"] == INGEST_EXIT_REJECTED and "产物入口校验拒" in vres["message"], vres
    # PreAuthorized 形 → 放行到薄壳执行(subprocess 替身)
    calls: list = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "ok", ""))
    cmd = f"lybra queue close --task-id {TASK} --actor {CHRIS_EXEC} --closure-evidence '{{}}' --confirm --autonomy-mode PreAuthorized --owner-policy-ref {POLICY}"
    res = execute_derived_action({"derivable": True, "task_id": TASK, "current_node": "finalize", "command": cmd}, gov, None)
    assert res["ok"] and calls and "--autonomy-mode" in calls[0], res


def test_f78b_item3_cli_parsers_declare_one_stage_args():
    from tools.aipos_cli.aipos_cli import build_parser

    p = build_parser()
    a = p.parse_args(["queue", "return", "--task-id", "X", "--actor", EXEC, "--agent-instance", EXEC, "--result-summary", "s",
                      "--owner-policy-ref", POLICY, "--confirm", "--autonomy-mode", "PreAuthorized"])
    assert a.autonomy_mode == "PreAuthorized"
    b = p.parse_args(["audit-verdict", "--reviewed-task-id", "X", "--verdict", "PASS", "--confirm", "--autonomy-mode", "PreAuthorized"])
    assert b.autonomy_mode == "PreAuthorized"
    c = p.parse_args(["queue", "close", "--task-id", "X", "--actor", EXEC, "--closure-evidence", "{}", "--confirm",
                      "--autonomy-mode", "PreAuthorized", "--owner-policy-ref", POLICY, "--connection-json", "c.json"])
    assert c.confirm and c.autonomy_mode == "PreAuthorized" and c.owner_policy_ref == POLICY
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    for name in ("lybra_queue_close_dry_run", "lybra_queue_close_confirm"):
        props = verbs[name]["parameters"]["properties"]
        assert "autonomy_mode" in props and "owner_policy_ref" in props, name


# ---------------------------------------------------------------------------
# 件④ 授权执行实撞
# ---------------------------------------------------------------------------

def test_f78b_item4a_roles_register_single_verb_cli_shell_and_workspace_root(tmp_path, monkeypatch):
    from tools.mcp_server import tools as gate

    assert "lybra_roles_register" in gate.TOOL_HANDLERS
    assert "lybra_roles_register_dry_run" not in gate.TOOL_HANDLERS and not hasattr(gate, "lybra_roles_register_dry_run")
    assert not hasattr(gate, "lybra_roles_register_confirm") and not hasattr(gate, "_ROLES_REGISTER_DRY_RUNS")
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    assert "lybra_roles_register" in verbs and "lybra_roles_register_dry_run" not in verbs
    cli_src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    assert 'call_tool("lybra_roles_register", {' in cli_src and "lybra_roles_register_dry_run" not in cli_src
    # 动词按 workspace_root 落盘(非 token 默认工作区)
    gov = _chris_gov(tmp_path, monkeypatch, git=False)
    monkeypatch.setattr(gate, "_capability_token", lambda: {"role": "advisor", "token_ref": "t", "expires_at": "2999-01-01T00:00:00Z"})
    monkeypatch.setattr(gate, "_repo_root", lambda: tmp_path / "elsewhere")
    res = _payload(gate.lybra_roles_register({"name": "hbj-advisor", "builtin_class": "advisor", "owner_authorization_ref": "DEC-1",
                                              "workspace_root": str(gov), "actor": "cli:roles-register"}))
    assert res.get("ok") and res["custom_roles"] == {"hbj-advisor": {"class": "advisor"}}, res
    registry = json.loads((gov / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    assert any(t.get("role") == "hbj-advisor" and t.get("role_class") == "advisor" for t in registry["tokens"])
    assert not (tmp_path / "elsewhere").exists()


def test_f78b_item4b_owner_decision_record_lands_in_workspace_root_not_token_default(tmp_path, monkeypatch):
    from tools.mcp_server import tools as gate

    gov = _chris_gov(tmp_path, monkeypatch, git=False)
    other = tmp_path / "token-default"
    (other / "5_tasks" / "records").mkdir(parents=True)
    monkeypatch.setattr(gate, "_repo_root", lambda: other)
    monkeypatch.setattr(gate, "_capability_token", lambda: {"role": "owner", "token_ref": "t", "expires_at": "2999-01-01T00:00:00Z"})
    monkeypatch.setattr(gate, "_owner_decision_scope_allowed", lambda: True)
    monkeypatch.setattr(gate, "_owner_confirm_scope_allowed", lambda: True)
    payload = {
        "workspace_root": str(gov), "actor": "owner",
        "decision_id": "DEC-F78B-1", "decision_type": "autonomy_policy_grant", "decision": "approved",
        "rationale": "arm loop envelope for chris", "decided_at": "2026-09-21T00:00:00Z",
        "autonomy_policy": {"policy_id": "pol_chris_loop_t", "agent_or_role": "hbj-advisor", "active_from": "2026-01-01T00:00:00Z",
                            "expires_at": "2999-01-01T00:00:00Z", "max_tasks": 20, "task_selector": {"project": PROJECT}},
    }
    dry = _payload(gate.lybra_owner_decision_record_dry_run(payload))
    assert dry.get("ok") and dry.get("verdict") != "BLOCK", dry
    token = dry.get("dry_run_token") or dry.get("dry_run_id")
    done = _payload(gate.lybra_owner_decision_record_confirm({"dry_run_token": token, "actor": "owner", "owner_confirmation_token": "OWNER_CONFIRMED",
                                                              "workspace_root": str(gov)}))
    assert done.get("ok"), done
    assert (gov / "5_tasks" / "policies" / "pol_chris_loop_t.md").is_file()
    assert list((gov / "5_tasks" / "records" / "owner_decisions").rglob("*.md"))
    assert not list((other / "5_tasks").rglob("*.md")), "禁写进 token 默认工作区"


def test_f78b_item4c_enroll_list_reads_gate_registry_with_governance_root(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import issue_self_contained_code, list_enrollment_codes
    from tools.mcp_server import tools as gate

    registry_root = tmp_path / "gate-root"
    (registry_root / ".lybra").mkdir(parents=True)
    (registry_root / ".lybra" / "connection.json").write_text(json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": []}), encoding="utf-8")
    chris = tmp_path / "chris"
    (chris / ".lybra").mkdir(parents=True)
    issued = issue_self_contained_code(registry_root, role="hbj-coder", instance="hbj-coder.chris.mac", ttl_seconds=600,
                                       gate_url="http://127.0.0.1:1", governance_root=str(chris), by="DEC-1", reason="t")
    assert issued["ok"]
    codes = list_enrollment_codes(registry_root, include_code=False)
    assert codes and codes[0]["governance_root"] == str(chris) and "code" not in codes[0]
    monkeypatch.setattr(gate, "_repo_root", lambda: registry_root)
    listed = _payload(gate.lybra_roles_enroll_list({"governance_root": str(chris)}))
    assert listed["ok"] and [c["code_id"] for c in listed["enrollments"]] == [issued["code_id"]], listed
    assert _payload(gate.lybra_roles_enroll_list({"governance_root": str(tmp_path / "other")}))["enrollments"] == []
    assert "code" not in json.dumps(listed["enrollments"][0]) or listed["enrollments"][0].get("code") is None
    # CLI enroll-list = 门动词薄壳(不再读本地 <workspace>/.lybra/enrollments.json 当真相)
    cli_src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    start = cli_src.index('args.roles_command == "enroll-list"')
    seg = cli_src[start:cli_src.index('args.roles_command == "enroll":', start)]
    assert 'call_tool("lybra_roles_enroll_list"' in seg and "list_enrollment_codes(workspace_root" not in seg


# ---------------------------------------------------------------------------
# 件⑤ F73E 顺手实撞
# ---------------------------------------------------------------------------

def test_f78b_item5a_n6_closure_location_declares_write_side_prefix():
    transitions = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    location = transitions["nodes"]["N6"]["record"]["location"]
    assert Path(location).name.startswith(f"{CLOSURE_ID_PREFIX}_"), location
    assert "closure_{task_id}" not in location


def test_f78b_item5b_finalize_requires_actor_equals_claimer(tmp_path):
    from tools.aipos_cli.finalize import finalize_task

    task_id = "AIPOS-F78B-FIN"
    gov = _closable_root(tmp_path, task_id)
    product = _init_product_repo(tmp_path / "product")
    blocked = finalize_task(task_id, DRIVER, product, governance_root=gov, dry_run=True)
    assert blocked["verdict"] == "BLOCK" and blocked.get("category") == "ACTOR_MISMATCH" and EXEC in blocked["message"], blocked
    allowed = finalize_task(task_id, EXEC, product, governance_root=gov, dry_run=True)
    assert allowed.get("category") != "ACTOR_MISMATCH" and "ACTOR_MISMATCH" not in " ".join(allowed["operations"]), allowed


def test_f78b_item5c_f28_live_case_compares_fingerprints_only_and_is_marked_real_gate():
    src = (REPO_ROOT / "tests/test_aipos_f28_custom_role_credential_persistence.py").read_text(encoding="utf-8")
    assert "def test_live_real_gate_chris_workstation_tokens_survive_restart" in src
    assert "def test_chris_workstation_tokens_survive_restart" not in src
    live = src[src.index("def test_live_real_gate_chris_workstation_tokens_survive_restart"):]
    assert "_token_fingerprint(" in live and "{t}" not in live, "断言消息禁带整条 token 记录"


def test_f78b_item5d_missing_claim_record_exit_points_to_state_repair():
    d = nr._not_derivable_no_claim(TASK, node="claim", state="claimed", verb="lybra_queue_return_dry_run", triggered_by="executor", notes="n")
    assert d["suggested_action"].startswith(f"lybra state repair --task-id {TASK}") and d["action"]["type"] == "record_missing"


def test_f78b_fixture_registered_in_runall_and_no_swallowed_exceptions():
    runall = (REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop" / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "tests/test_aipos_f78b_chris_zero_migration.py" in runall
    for rel in ("tools/aipos_cli/task_loader.py", "tools/aipos_cli/next_resolver.py", "tools/aipos_cli/artifact_ingest.py",
                "tools/aipos_cli/loop_driver.py", "tools/aipos_cli/two_phase_shell_factory.py", "tools/aipos_cli/finalization_record.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"except Exception:\s*\n\s*pass", text), rel
