"""AIPOS-F178 — non-code card worktree: one criterion (card_needs_worktree) for claim / kickoff / write boundary / finalize,
and one repair for a claimed card that is missing its worktree (queue repair + loop).

Real case 10-16 (chris LANTU-HW1): task_mode=docs + lane.repo=<product repo>; the gate claimed it but built no worktree
(card_needs_worktree only said True for code), while card render / my-tasks / write_boundary pointed at <repo>/.worktrees/<ID>.

item 1  criterion: enums.schema task_mode.product_repo_write (writes_product_repo per value; explicit lane.repo resolving to a
        repo other than the governance root = writes; audit follows the reviewed card); claim builds the tree for a docs card
        with lane.repo; a governance-root-only docs card builds none and its kickoff / my-tasks / write boundary name the
        governance-root report location; code cards unchanged.
item 2  consistency: my-tasks / card render / write_boundary / finalize all ask card_needs_worktree first.
item 3  repair: `lybra queue repair --task-id <ID> --actor <inst>` preview by default, --confirm builds (idempotent), same
        function used by the loop after claim and before waiting (run record worktree_repairs; failure = worktree_repair_failed).
item 4  finalize: a docs card that writes the product repo merges through the same internal path as a code card after PASS.
Rigs reuse F73D / F78 / F78C / F130 builders (no second rig).
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

from test_aipos_f73d_loop_driver import AUDITOR, DRIVER, EXEC, _claim_record, _fm, _write  # noqa: E402
from test_aipos_f78_engine_agnostic import _branch_with_commit, _card, _git  # noqa: E402
from test_aipos_f78c_card_repo import _chris_shape_gov, _dual_gov, _single_gov  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.agent_profiles import load_agent_profiles  # noqa: E402
from tools.aipos_cli.queue_mutation import mutate_queue_task  # noqa: E402
from tools.aipos_cli.task_loader import queue_root_for  # noqa: E402
from tools.aipos_cli.workspace_config import default_lane_repo  # noqa: E402

DOCS = "AIPOS-F178D"       # docs card writing product repo b (lane.repo = b)
GOVDOCS = "AIPOS-F178G"    # docs card writing only the governance root (no lane)
CODE = "AIPOS-F178C"       # code card (regression)
POL = "pol_f178_docs"


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _lane(repo: str, paths: list[str] | None = None) -> dict:
    return {"lane": {"repo": repo, "paths": paths or ["docs/"], "roles": ["executor"]}}


def _claim(gov: Path, task_id: str, actor: str = EXEC) -> dict:
    """Gate claim core (gate controlled-execute and file CLI call the same mutate_queue_task, with records)."""
    return mutate_queue_task(gov, "claim", task_id=task_id, actor=actor, dry_run=False,
                             profiles=load_agent_profiles(gov), with_records=True)


def _cli(argv: list[str], capsys: pytest.CaptureFixture) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    capsys.readouterr()
    rc = main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def _view(gov: Path, task_id: str) -> dict:
    path, _q = nr._find_task_in_queue(gov, task_id)
    return nr.card_workstation_view(gov, task_id, nr._read_frontmatter(path))


# ===========================================================================
# item 1: one criterion, declared once
# ===========================================================================

def test_item1_declaration_and_single_criterion(tmp_path, monkeypatch):
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]
    flags = {v["value"]: v["writes_product_repo"] for v in enums["values"]}
    _show(f"[item1 declaration] writes_product_repo={flags} single_implementation={enums['product_repo_write']['single_implementation']}")
    # AIPOS-F196: 值域只追加(validation = 固定候选验证, 不写产品仓)
    assert flags == {"code": True, "docs": False, "governance": False, "config": False, "audit": False, "validation": False}
    assert enums["product_repo_write"]["single_implementation"] == "tools/aipos_cli/next_resolver.card_needs_worktree"

    gov, repos = _dual_gov(tmp_path, monkeypatch)
    cases = {
        "code, no lane": {"task_mode": "code"},
        "docs, no lane (governance root only)": {"task_mode": "docs"},
        "docs, lane.repo=b": {"task_mode": "docs", **_lane("b")},
        "config, lane.repo=<abs path a>": {"task_mode": "config", **_lane(str(repos["a"]))},
        "unknown mode, no lane": {"task_mode": "config_and_code"},
        "docs, lane.repo undeclared (fail-closed)": {"task_mode": "docs", **_lane("zz")},
    }
    got = {name: nr.card_needs_worktree(gov, {"task_id": "T", **fm}) for name, fm in cases.items()}
    _show("[item1 card_needs_worktree] " + json.dumps(got, ensure_ascii=False))
    assert got == {"code, no lane": True, "docs, no lane (governance root only)": False, "docs, lane.repo=b": True,
                   "config, lane.repo=<abs path a>": True, "unknown mode, no lane": False, "docs, lane.repo undeclared (fail-closed)": True}
    # project whose only declared "repo" is the governance root itself (chris shape before repos): publish derives
    # lane.repo = governance root, which is not a product repo -> no tree for non-code; code keeps building as before
    chris = _chris_shape_gov(tmp_path, monkeypatch)
    derived = default_lane_repo(chris)
    shape = {mode: nr.card_needs_worktree(chris, {"task_id": "T", "task_mode": mode, **_lane(derived)}) for mode in ("docs", "code")}
    _show(f"[item1 lane.repo = governance root ({derived})] {shape}")
    assert Path(derived) == chris and shape == {"docs": False, "code": True}
    # audit card follows the reviewed card
    _card(gov, DOCS, "claimed", task_mode="docs", extra=_lane("b"))
    _card(gov, GOVDOCS, "claimed", task_mode="docs")
    audit_of = {r: nr.card_needs_worktree(gov, {"task_id": f"{r}R", "task_mode": "audit", "reviewed_task_id": r}) for r in (DOCS, GOVDOCS)}
    _show(f"[item1 audit follows reviewed] {audit_of}")
    assert audit_of == {DOCS: True, GOVDOCS: False}

    # every consumer asks the one criterion (no second judgement by task_mode == "code")
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    body = src.split("def card_needs_worktree(")[1].split("\ndef ")[0]
    assert '== "code"' not in body and "card_writes_product_repo" in body
    for rel, needle in (("card_render.py", "card_needs_worktree("), ("write_boundary.py", "card_needs_worktree("),
                        ("finalize.py", "_card_needs_worktree("), ("queue_mutation.py", "card_needs_worktree(")):
        assert needle in (REPO_ROOT / "tools" / "aipos_cli" / rel).read_text(encoding="utf-8"), rel
    assert src.count("def _ensure_worktree(") == 1 and src.count("def repair_missing_worktree(") == 1


def test_item1_claim_docs_card_with_lane_repo_builds_worktree(tmp_path, monkeypatch, capsys):
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _card(gov, DOCS, "pending", task_mode="docs", extra=_lane("b"))
    result = _claim(gov, DOCS)
    _show("[item1 docs+lane.repo claim] " + json.dumps({k: result.get(k) for k in (
        "verdict", "wrote", "worktree_created", "worktree_path", "worktree_branch", "blocking_reasons")}, ensure_ascii=False))
    assert result["wrote"] and result.get("worktree_created") is True
    worktree = repos["b"] / ".worktrees" / DOCS
    assert Path(result["worktree_path"]) == worktree and worktree.is_dir()
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == f"card/{DOCS}"
    view = _view(gov, DOCS)
    _show("[item1 docs+lane.repo my-tasks view] " + json.dumps({k: view.get(k) for k in (
        "worktree_required", "worktree_path", "worktree_exists", "worktree_refusal", "report_path")}, ensure_ascii=False))
    assert view["worktree_required"] is True and view["worktree_exists"] is True and view["worktree_refusal"] is None
    rc, out, err = _cli(["--workspace-root", str(gov), "card", "render", "--task-id", DOCS, "--harness", "pi"], capsys)
    _show(f"[item1 docs+lane.repo card render pi] rc={rc}\n{out}")
    assert rc == 0, err
    assert out.splitlines()[0] == f"工作树: {worktree} (分支 card/{DOCS})"


def test_item1_governance_only_docs_card_no_tree_and_kickoff_names_governance_root(tmp_path, monkeypatch, capsys):
    from tools.aipos_cli.write_boundary import check_access

    gov, repo = _single_gov(tmp_path, monkeypatch)
    _card(gov, GOVDOCS, "pending", task_mode="docs")
    result = _claim(gov, GOVDOCS)
    _show("[item1 governance-only docs claim] " + json.dumps({k: result.get(k) for k in (
        "verdict", "wrote", "worktree_created", "worktree_path")}, ensure_ascii=False))
    assert result["wrote"] and "worktree_created" not in result and not (repo / ".worktrees" / GOVDOCS).exists()
    report = nr.card_report_path(gov, GOVDOCS)
    view = _view(gov, GOVDOCS)
    _show("[item1 governance-only my-tasks view] " + json.dumps({k: view.get(k) for k in (
        "worktree_required", "worktree_path", "worktree_exists", "worktree_refusal", "report_path")}, ensure_ascii=False))
    assert view["worktree_required"] is False and view["worktree_path"] is None
    assert view["worktree_refusal"]["code"] == "WORKTREE_NOT_REQUIRED" and str(report) in view["worktree_refusal"]["reason"]
    assert ".worktrees" not in json.dumps(view, ensure_ascii=False)
    selected = nr.select_next_card([{**view, "task_id": GOVDOCS, "queue_state": "claimed", "kickoff_refusal": None}])
    _show(f"[item1 governance-only next_card_excluded] {selected['next_card_excluded']}")
    assert selected["next_card"] is None and selected["next_card_excluded"][0]["code"] == "WORKTREE_NOT_REQUIRED"

    rc, out, err = _cli(["--workspace-root", str(gov), "card", "render", "--task-id", GOVDOCS, "--harness", "pi"], capsys)
    _show(f"[item1 governance-only card render pi] rc={rc}\n{out}")
    assert rc == 0, err
    first = out.splitlines()[0]
    assert first.startswith("工作树: 不建(本卡只写治理根") and str(report) in first and ".worktrees" not in out
    rc, out, err = _cli(["--workspace-root", str(gov), "card", "render", "--task-id", GOVDOCS, "--harness", "claude-code", "--stdout"], capsys)
    assert rc == 0 and "- 工作树: 不建(本卡只写治理根" in out and "- 产品仓: 不改" in out and "提交到分支" not in out, err
    rc, _out, err = _cli(["--workspace-root", str(gov), "card", "render", "--task-id", GOVDOCS, "--harness", "codex"], capsys)
    _show(f"[item1 governance-only card render codex (writes into worktree)] rc={rc} stderr={err.strip()}")
    assert rc == 1 and "不建(本卡只写治理根" in err and "--out-dir" in err

    refused = check_access(gov, role="executor", path=repo / "docs" / "x.md", level="mutate", task_id=GOVDOCS)
    _show(f"[item1 governance-only write_boundary] {refused['code']}: {refused['reason']}")
    assert refused["allowed"] is False and refused["code"] == "CARD_NO_WORKTREE" and str(report) in refused["reason"]


def test_item1_code_card_unchanged(tmp_path, monkeypatch, capsys):
    from tools.aipos_cli.write_boundary import check_access

    gov, repo = _single_gov(tmp_path, monkeypatch)
    _card(gov, CODE, "pending", extra=_lane(str(repo), ["tools/aipos_cli/"]))
    result = _claim(gov, CODE)
    worktree = repo / ".worktrees" / CODE
    _show("[item1 code claim] " + json.dumps({k: result.get(k) for k in ("verdict", "worktree_created", "worktree_path")}, ensure_ascii=False))
    assert result["worktree_created"] is True and Path(result["worktree_path"]) == worktree
    view = _view(gov, CODE)
    assert view["worktree_required"] is True and view["worktree_path"] == str(worktree) and view["worktree_exists"] is True
    rc, out, _err = _cli(["--workspace-root", str(gov), "card", "render", "--task-id", CODE, "--harness", "pi"], capsys)
    assert rc == 0 and out.splitlines()[0] == f"工作树: {worktree} (分支 card/{CODE})"
    ok = check_access(gov, role="executor", path=worktree / "tools" / "aipos_cli" / "x.py", level="mutate", task_id=CODE)
    main_checkout = check_access(gov, role="executor", path=repo / "tools" / "aipos_cli" / "x.py", level="mutate", task_id=CODE)
    _show(f"[item1 code write_boundary] worktree={ok['code']} main checkout={main_checkout['code']}")
    assert ok["allowed"] and main_checkout["code"] == "NOT_CARD_WORKTREE"


# ===========================================================================
# item 3: repair — queue repair preview / confirm / idempotent; loop after claim and before waiting
# ===========================================================================

def _claimed_without_tree(gov: Path, task_id: str, **card_kw) -> None:
    """Pre-F178 shape: card already claimed (claim record present) but no worktree was built."""
    _card(gov, task_id, "claimed", **card_kw)
    _claim_record(gov, task_id, EXEC)


def test_item3_queue_repair_preview_confirm_idempotent(tmp_path, monkeypatch, capsys):
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _claimed_without_tree(gov, DOCS, task_mode="docs", extra=_lane("b"))
    worktree = repos["b"] / ".worktrees" / DOCS
    base = ["--workspace-root", str(gov), "queue", "repair", "--task-id", DOCS, "--actor", DRIVER, "--json"]

    rc, out, err = _cli(base, capsys)
    preview = json.loads(out)
    _show(f"[item3 repair preview] rc={rc} {json.dumps(preview['worktree_repair'], ensure_ascii=False)}")
    assert rc == 0 and preview["verdict"] == "OK" and preview["worktree_repair"]["action"] == "would_create"
    assert preview["worktree_repair"]["worktree_path"] == str(worktree) and not worktree.exists()
    assert "--confirm" in preview["worktree_repair"]["message"]
    rc, out, _err = _cli([*base, "--dry-run", "--confirm"], capsys)
    assert rc == 0 and json.loads(out)["worktree_repair"]["action"] == "would_create" and not worktree.exists()  # --dry-run wins

    rc, out, err = _cli([*base, "--confirm"], capsys)
    done = json.loads(out)
    _show(f"[item3 repair --confirm] rc={rc} {json.dumps(done['worktree_repair'], ensure_ascii=False)}")
    assert rc == 0 and done["worktree_repair"]["action"] == "created" and worktree.is_dir()
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == f"card/{DOCS}"
    record = gov / done["worktree_repair"]["repair_record"]
    assert record.is_file() and record.parent == gov / "5_tasks" / "records" / "events" / DOCS
    _show(f"[item3 repair record] {record.relative_to(gov)}\n{record.read_text(encoding='utf-8')}")

    rc, out, _err = _cli([*base, "--confirm"], capsys)
    again = json.loads(out)
    _show(f"[item3 repair --confirm again] rc={rc} {again['worktree_repair']['message']}")
    assert rc == 0 and again["worktree_repair"]["action"] == "none" and "幂等" in again["worktree_repair"]["message"]
    assert len(list(record.parent.glob("event_worktree_repair_*.md"))) == 1
    assert _view(gov, DOCS)["worktree_exists"] is True

    # governance-root-only claimed card / pending card: not applicable, zero writes
    _claimed_without_tree(gov, GOVDOCS, task_mode="docs")
    rc, out, _err = _cli(["--workspace-root", str(gov), "queue", "repair", "--task-id", GOVDOCS, "--actor", DRIVER, "--json", "--confirm"], capsys)
    na = json.loads(out)["worktree_repair"]
    _show(f"[item3 repair governance-only] {na['message']}")
    assert rc == 0 and na["applicable"] is False and na["action"] == "none" and "只写治理根" in na["message"]


def test_item3_queue_repair_failure_blocks_with_reason(tmp_path, monkeypatch, capsys):
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _claimed_without_tree(gov, DOCS, task_mode="docs", extra=_lane("b"))
    occupied = repos["b"] / ".worktrees"
    occupied.mkdir()
    (occupied / DOCS).write_text("not a dir\n", encoding="utf-8")  # landing path taken by a plain file
    rc, out, _err = _cli(["--workspace-root", str(gov), "queue", "repair", "--task-id", DOCS, "--actor", DRIVER, "--json", "--confirm"], capsys)
    res = json.loads(out)
    _show(f"[item3 repair failure] rc={rc} verdict={res['verdict']} reasons={res.get('blocking_reasons')}")
    assert rc == 1 and res["verdict"] == "BLOCK" and res["blocking_reasons"]


def _policy(gov: Path, task_mode: str = "docs") -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{POL}.md", build_autonomy_policy_markdown(
        policy_id=POL, agent_or_role=DRIVER, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="test-envelope", task_selector_task_mode=task_mode))


def _timeout_watch(args, expect_ready, **_kw):
    return 3  # no workstation simulated: the wait times out with the declared exit code


def test_item3_loop_repairs_claimed_card_missing_tree_before_waiting(tmp_path, monkeypatch):
    from tools.aipos_cli.loop_driver import run_loop
    from tools.aipos_cli.frontmatter import require_frontmatter

    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _policy(gov)
    _claimed_without_tree(gov, DOCS, task_mode="docs", extra=_lane("b"))
    worktree = repos["b"] / ".worktrees" / DOCS
    out = io.StringIO()
    res = run_loop(DOCS, gov, actor=DRIVER, out=out, watch=_timeout_watch, max_wait=1, interval=0.05, max_steps=3, no_launch=True)
    text = out.getvalue()
    _show(f"[item3 loop auto repair] exit={res.exit_code} reason={res.reason}\n{text}")
    assert "worktree_repair: 已补建卡工作树" in text and worktree.is_dir()
    assert res.reason == "wait_timeout" and res.steps[0].kind == "wait"  # repaired, then waited as before
    meta, _body = require_frontmatter(Path(res.run_record))
    _show(f"[item3 loop run record worktree_repairs] {json.dumps(meta['worktree_repairs'], ensure_ascii=False)}")
    assert [r["outcome"] for r in meta["worktree_repairs"]] == ["created"] and meta["worktree_repairs"][0]["card"] == DOCS
    assert list((gov / "5_tasks" / "records" / "events" / DOCS).glob("event_worktree_repair_*.md"))

    # second run: tree present -> no repair entry
    out2 = io.StringIO()
    res2 = run_loop(DOCS, gov, actor=DRIVER, out=out2, watch=_timeout_watch, max_wait=1, interval=0.05, max_steps=3, no_launch=True)
    meta2, _ = require_frontmatter(Path(res2.run_record))
    assert "worktree_repair" not in out2.getvalue() and "worktree_repairs" not in meta2


def test_item3_loop_repair_failure_ends_with_declared_reason_and_command(tmp_path, monkeypatch):
    from tools.aipos_cli.loop_driver import run_loop
    from tools.aipos_cli.loop_run_record import loop_status
    from tools.aipos_cli.frontmatter import require_frontmatter

    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _policy(gov)
    _claimed_without_tree(gov, DOCS, task_mode="docs", extra=_lane("b"))
    (repos["b"] / ".worktrees").mkdir()
    (repos["b"] / ".worktrees" / DOCS).write_text("occupied\n", encoding="utf-8")
    out = io.StringIO()
    res = run_loop(DOCS, gov, actor=DRIVER, out=out, watch=_timeout_watch, max_wait=1, interval=0.05, max_steps=3, no_launch=True)
    _show(f"[item3 loop repair failure] exit={res.exit_code} reason={res.reason}\n{out.getvalue()}")
    assert res.exit_code == 4 and res.reason == "worktree_repair_failed"
    assert f"lybra queue repair --task-id {DOCS} --actor {DRIVER} --workspace-root {gov}" in res.message
    meta, _ = require_frontmatter(Path(res.run_record))
    assert meta["end_reason"] == "worktree_repair_failed" and meta["worktree_repairs"][0]["outcome"] == "failed"
    action = loop_status(gov, DOCS)["next_action"]
    _show(f"[item3 loop status next_action] {json.dumps(action, ensure_ascii=False)}")
    assert action["action"] == "advisor_fix" and "lybra queue repair --task-id" in action["hint"]


def test_item3_post_claim_check_repairs_when_gate_built_no_tree(tmp_path, monkeypatch):
    """Loop claim step: the gate reports success but left no worktree (e.g. a pre-F178 gate deployment) -> same repair."""
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _card(gov, DOCS, "pending", task_mode="docs", extra=_lane("b"))

    def fake_gate(command, action, governance_root=None):
        src = queue_root_for(gov) / "pending" / f"{DOCS.lower()}.md"
        text = src.read_text(encoding="utf-8").replace("status: pending", "status: claimed")
        _write(queue_root_for(gov) / "claimed" / src.name, text)
        src.unlink()
        _claim_record(gov, DOCS, EXEC)
        return {"ok": True, "exit_code": 0, "output": "claimed (no worktree)"}

    monkeypatch.setattr(nr, "_run_product_command", fake_gate)
    res = nr._execute_claim_with_role_token(derivation={"task_id": DOCS, "command": "lybra queue claim --confirm"}, workspace_root=gov)
    _show(f"[item3 post-claim repair] ok={res['ok']} message={res['message']}\n{res['output']}")
    assert res["ok"] and res["worktree_path"] == str(repos["b"] / ".worktrees" / DOCS) and "Worktree repair: 已补建" in res["output"]


# ===========================================================================
# item 4: finalize — docs card writing the product repo merges like a code card after PASS
# ===========================================================================

def test_item4_docs_card_pass_finalize_internal_merge_no_deploy(tmp_path, monkeypatch):
    from test_aipos_f130_finalize_unlock import _card_branch, _gov, _product, _verdict
    from tools.aipos_cli import finalize as fz
    from tools.aipos_cli.workspace_config import set_project_repos

    gov = _gov(tmp_path / "home" / "rng")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    (gov / "project.json").write_text(json.dumps({"project": "rng", "config_version": 1}), encoding="utf-8")
    repo = tmp_path / "product"
    _product(repo)
    assert set_project_repos(gov.parent, "rng", {"web": str(repo)}, default="web", no_deploy=["web"])["written"]
    task = "RNG-178"
    _write(queue_root_for(gov) / "claimed" / "rng-178.md", _fm({
        "task_id": task, "title": "docs card writing web", "project": "rng", "task_mode": "docs", "status": "claimed",
        "assigned_to": "exec.range", "agent_instance": "exec.range", "output_target": "docs/", "audit": "required",
        **_lane("web")}, "# docs\n"))
    assert nr.card_needs_worktree(gov, {**nr._read_frontmatter(queue_root_for(gov) / "claimed" / "rng-178.md"), "task_id": task})
    tip = _card_branch(repo, task)
    _verdict(gov, task, tip)
    r = fz.finalize_task(task, "exec.range", repo, governance_root=gov, push=True, deploy=True)
    merged = _git(repo, "log", "-1", "--format=%s", "main")
    _show(f"[item4 docs finalize] verdict={r['verdict']} deployed={r['deployed']} main tip subject={merged!r}\n  message: {r['message']}\n"
          + "\n".join(f"  op: {op}" for op in r["operations"] if "Branch integration" in op or "merge" in op.lower() or "部署" in op))
    assert r["verdict"] == "PASS" and r["deployed"] is False and merged.startswith(f"Merge card/{task}")
    assert _git(repo, "merge-base", "--is-ancestor", tip, "main") == ""

    # missing branch: docs card that writes the repo = hard BLOCK (same as code); governance-only docs card = skipped
    ops: list[str] = []
    blocked = fz._integrate_card_branch("RNG-179", "v", repo, gov, False, ops, task_mode="docs", output_target=None, needs_worktree=True)
    skipped = fz._integrate_card_branch("RNG-180", "v", repo, gov, False, [], task_mode="docs", output_target=None, needs_worktree=False)
    _show(f"[item4 missing branch] writes-repo docs: {blocked['action']} / governance-only docs: {skipped['action']}")
    assert blocked["blocked"] is True and blocked["action"] == "blocked_branch_not_found"
    assert skipped["blocked"] is False and skipped["action"] == "skipped_no_branch"


def test_item4_audit_binds_tip_for_docs_card_writing_repo(tmp_path, monkeypatch):
    """The audit verdict / staleness tip reader follows the same criterion (docs card writing a repo has a card branch)."""
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _card(gov, DOCS, "claimed", task_mode="docs", extra=_lane("b"))
    _card(gov, GOVDOCS, "claimed", task_mode="docs")
    sha, _tree = _branch_with_commit(repos["b"], DOCS, "docs/x.md")
    tip = nr._card_branch_tip(gov, DOCS, nr._read_frontmatter(queue_root_for(gov) / "claimed" / f"{DOCS.lower()}.md"))
    none = nr._card_branch_tip(gov, GOVDOCS, nr._read_frontmatter(queue_root_for(gov) / "claimed" / f"{GOVDOCS.lower()}.md"))
    _show(f"[item4 card branch tip] docs+lane.repo={tip and tip[1]['commit_sha']} governance-only={none}")
    assert tip is not None and tip[1]["commit_sha"] == sha and none is None


def test_item4_audit_card_of_docs_card_writing_repo_gets_forensic_tree(tmp_path, monkeypatch):
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _card(gov, DOCS, "claimed", task_mode="docs", extra=_lane("b"))
    sha, _tree = _branch_with_commit(repos["b"], DOCS, "docs/x.md")
    _card(gov, f"{DOCS}R", "pending", assigned=AUDITOR, task_mode="audit",
          extra={"reviewed_task_id": DOCS, "derived_from": DOCS, "audit": "none"})
    result = _claim(gov, f"{DOCS}R", actor=AUDITOR)
    _show("[item4 audit claim of docs card] " + json.dumps({k: result.get(k) for k in (
        "worktree_created", "worktree_path", "worktree_detached", "worktree_commit")}, ensure_ascii=False))
    assert result["worktree_created"] is True and result["worktree_detached"] is True and result["worktree_commit"] == sha
    assert Path(result["worktree_path"]) == repos["b"] / ".worktrees" / f"{DOCS}R"

