"""AIPOS-F259 — 部署覆盖认审计区间 + 撤卡预演提示 supersede --carry-branch。

实撞(10-31, 治理 LEDGER F256/F256B): F256 交回后撤卡(withdraw)重发为 F256B, F256B 分支复用 F256 的 4 个提交(提交信息带 F256 卡号);
F256B 审计 PASS, 审计区间(F256B 分支相对 main)含这 4 个提交; 合入后部署被拒——覆盖判据按提交信息卡号归属已撤的 F256(无裁决、无承接),
审计员实际审过的提交判据却不认, 只能 Owner dev_override。

靶场 = 临时治理根(f78._make_gov)+ 临时产品仓 + 假部署目录(只在临时产品仓); 卡号形状用靶场自造声明 PROBE-…(不写死项目卡号前缀)。
验收(卡面 ★验收):
 ① 10-31 形态复现: 卡 A 两个提交 → 撤回 → 卡 B 分支含 A 提交 + 自身提交 → B PASS 合入; 交回记录无基线(= 修前判据)部署授权拒(原文),
    交回绑定带基线(本卡写入点)后过(原文, 覆盖明细 how=audited_range, 预演逐条「提交 C 经 B 审计区间覆盖」); finalize 完整性预演同
 ② 反例: 直接提交到 main / 审计后追加到分支 / 跨卡挪用(拿 B 裁决覆盖与 B 区间无关的提交)/ 基线之内的旧提交 / 未合入卡 仍拒
 ③ 撤卡预演: 有提交的卡原文含 supersede --carry-branch 提示; 无提交 / 无分支的卡无提示; 撤卡本身不被拦
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 临时治理根 / 卡 / git 夹具唯一来源
from tools.aipos_cli import finalize as fz  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.board_adapter import withdraw_task  # noqa: E402
from tools.aipos_cli.deployment_authorization import (  # noqa: E402
    audited_range_declaration,
    check_verdict_ref_authorization,
    commit_pass_coverage,
    commits_to_deploy,
)

PATTERN = "PROBE-[0-9A-Z]+"
A, B, Z = "PROBE-F259A", "PROBE-F259B", "PROBE-F259Z"
_git, _write = f78._git, f78._write


def _show(msg: str) -> None:
    print(msg, flush=True)


def _commit(repo: Path, rel: str, subject: str) -> str:
    _write(repo / rel, f"{subject}\n")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    _write(gov / "card_policy.json", json.dumps({"schema_version": "1.0.0", "task_id_pattern": PATTERN}))
    repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    # 归属解析读产品仓 schema(lybra 形产品仓自带 schema/; 与 F130 靶场同法拷入, 不入提交)
    shutil.copytree(REPO_ROOT / "schema", repo / "schema", ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    _write(repo / ".git" / "info" / "exclude", "schema/\n.deploy/\n")
    return gov, repo


def _return_record(gov: Path, task_id: str, subject: dict) -> str:
    rid = f"return_{task_id}_20261101_000000_exec-lybra-test"
    meta = {"record_type": "return_record", "event_type": "mcp_queue_return", "return_id": rid, "task_id": task_id,
            "returned_at": "2026-11-01T00:00:00Z", "artifact_subject": subject}
    _write(gov / "5_tasks" / "records" / "returns" / task_id / f"{rid}.md", f78._fm(meta, f"# return {task_id}\n"))
    return rid


def _verdict(gov: Path, task_id: str, tip: str, return_id: str | None, *, stamp: str = "20261101_010000") -> str:
    vid = f"verdict_{task_id}_{stamp}_audit-lybra-test"
    meta = {"record_type": "audit_verdict_record", "event_type": "mcp_audit_verdict", "verdict_id": vid, "verdict": "PASS",
            "reviewed_task_id": task_id, "verdict_at": "2026-11-01T01:00:00Z", "auditor_instance": f78.AUDITOR,
            "artifact_subject": {"repository": "product", "commit_sha": tip, "tree_hash": "0" * 40}}
    if return_id:
        meta["reviewed_return_record_ref"] = return_id
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / task_id / f"{vid}.md", f78._fm(meta, f"# verdict {task_id}\n"))
    return vid


def _fake_deploy(repo: Path, commit: str) -> None:
    rel = repo / ".deploy" / "releases" / f"r-{commit[:8]}"
    _write(rel / "VERSION", f"git_commit: {commit}\ndeployment_provenance: audited\nauthorization_type: verdict_ref\n")
    link = repo / ".deploy" / "current"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(rel)


def _scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, record_base: bool = True, merge: bool = True):
    """10-31 形态: main 先有他卡提交(部署点)→ 卡 A 两个提交 → A 撤回(无承接)→ 卡 B 分支从 A tip 建、加自身提交 → B 交回(门绑定)
    → B PASS → 合入 main(merge --no-ff)。record_base=False = 交回记录无基线(修前判据同形)。"""
    gov, repo = _rig(tmp_path, monkeypatch)
    old = _commit(repo, "early.txt", f"{A}: 早于 B 基线已在 main 的旧提交(无裁决)")
    deployed = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", f"card/{A}")
    a1 = _commit(repo, "a1.txt", f"{A} 件①: 实现")
    a2 = _commit(repo, "a2.txt", f"{A} 件②: 实现")
    f78._card(gov, A, "withdrawn", extra={"withdrawal_reason": "撤卡重发 B(未写承接)"})
    _git(repo, "checkout", "-q", "-b", f"card/{B}")
    b1 = _commit(repo, "b1.txt", f"{B} 件⑤: 补在途过渡")
    _git(repo, "checkout", "-q", "main")
    card_b = f78._card(gov, B, "claimed")
    subject = nr.return_binding_subject(gov, B, nr._read_frontmatter(card_b))
    if not record_base:
        subject.pop(audited_range_declaration()["base_key"], None)
    rid = _return_record(gov, B, subject)
    vid = _verdict(gov, B, b1, rid)
    merge_commit = None
    if merge:
        _git(repo, "merge", "-q", "--no-ff", f"card/{B}", "-m", f"Merge card/{B}: 靶场 ({vid})")
        merge_commit = _git(repo, "rev-parse", "HEAD")
    return {"gov": gov, "repo": repo, "old": old, "deployed": deployed, "a1": a1, "a2": a2, "b1": b1, "vid": vid, "rid": rid,
            "subject": subject, "merge": merge_commit}


# ---------------------------------------------------------------------------
# 写入点: 门交回绑定附交回时基线(新键 base_commit, 声明 N5.guards.commit_coverage.audited_range.base_key)
# ---------------------------------------------------------------------------

def test_return_binding_writes_base_commit_declared_key(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch, merge=False)
    key = audited_range_declaration()["base_key"]
    _show(f"[写入点] return_binding_subject({B}) = {json.dumps(s['subject'], ensure_ascii=False)}")
    assert key == "base_commit"
    assert s["subject"]["commit_sha"] == s["b1"] and s["subject"][key] == s["deployed"]
    assert s["subject"][key] == _git(s["repo"], "merge-base", "main", s["b1"])


# ---------------------------------------------------------------------------
# ① 10-31 形态: 修前(交回记录无基线)拒 → 修后(带基线)过, 覆盖明细标 audited_range, 预演逐条列
# ---------------------------------------------------------------------------

def test_item1_withdrawn_reuse_without_base_refused(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch, record_base=False)
    commits = commits_to_deploy(s["repo"], s["deployed"], s["merge"])
    auth = check_verdict_ref_authorization(s["vid"], s["gov"], commits, s["repo"])
    _show(f"[①修前同形·交回记录无基线] 部署授权原文:\n{auth['message']}\n未覆盖: {auth['uncovered_commits']}")
    assert not auth["authorized"]
    uncovered = "\n".join(auth["uncovered_commits"])
    assert s["a1"][:8] in uncovered and s["a2"][:8] in uncovered
    assert "无 base_commit" in uncovered and "基线推不出" in uncovered  # 拒因列出未认依据(fail-closed, 不静默)


def test_item1_withdrawn_reuse_covered_by_audited_range(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch)
    commits = commits_to_deploy(s["repo"], s["deployed"], s["merge"])
    auth = check_verdict_ref_authorization(s["vid"], s["gov"], commits, s["repo"])
    _show(f"[①修后·部署授权原文]\n{auth['message']}")
    assert auth["authorized"], auth
    hows = {c["commit"]: c["how"] for c in auth["covered_commits"]}
    assert hows[s["a1"]] == "audited_range" and hows[s["a2"]] == "audited_range"
    assert hows[s["b1"]] == "exact" and hows[s["merge"]] == "merge"
    for sha in (s["a1"], s["a2"]):
        assert f"提交 {sha[:8]} 经 {B} 审计区间覆盖" in auth["message"]
    assert auth["covering_verdicts"] == [s["vid"]]
    # 单提交判据(release plan / dev_override 基线同一实现)
    one = commit_pass_coverage(s["repo"], s["gov"], s["a1"])
    assert one["covered"] and one["how"] == "audited_range" and one["covering_task_id"] == B and one["task_id"] == A
    assert one["audited_range"] == {"base": s["deployed"], "bound": s["b1"], "return_id": s["rid"]}


def test_item1_finalize_integrity_preview_lists_audited_range(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch)
    _fake_deploy(s["repo"], s["deployed"])
    integrity = fz._check_deployment_integrity(s["repo"], s["gov"])
    _show(f"[①修后·finalize 完整性预演原文]\n{integrity['message']}")
    assert integrity["integrity_ok"], integrity
    assert f"提交 {s['a1'][:8]} 经 {B} 审计区间覆盖" in integrity["message"]


def test_item1_deploy_check_only_cli_output(tmp_path, monkeypatch):
    """lybra-deploy 的覆盖预检 = `python3 -m tools.aipos_cli.deployment_record --check-only`(同一实现), 原文含审计区间明细。"""
    s = _scenario(tmp_path, monkeypatch)
    proc = subprocess.run([sys.executable, "-m", "tools.aipos_cli.deployment_record", "--governance-root", str(s["gov"]),
                           "--commit", s["merge"], "--verdict-ref", s["vid"], "--coverage-base", s["deployed"],
                           "--repo-root", str(s["repo"]), "--check-only"],
                          cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
    _show(f"[①修后·deployment_record --check-only 原文] rc={proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["authorized"] is True and "审计区间覆盖" in proc.stdout


# ---------------------------------------------------------------------------
# ② 反例仍拒
# ---------------------------------------------------------------------------

def test_item2_direct_main_commit_still_refused(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch)
    direct = _commit(s["repo"], "direct.txt", f"{A}: 直接提交到 main")
    nocard = _commit(s["repo"], "direct2.txt", "hotfix: 无卡号直接提交到 main")
    auth = check_verdict_ref_authorization(s["vid"], s["gov"], commits_to_deploy(s["repo"], s["deployed"], nocard), s["repo"])
    _show(f"[②直接提交到 main] 部署授权原文:\n{auth['message']}\n未覆盖: {auth['uncovered_commits']}")
    assert not auth["authorized"]
    uncovered = "\n".join(auth["uncovered_commits"])
    assert direct[:8] in uncovered and nocard[:8] in uncovered
    assert s["a1"][:8] not in uncovered  # 区间内的复用提交照认


def test_item2_commit_appended_after_audit_still_refused(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch)
    _git(s["repo"], "checkout", "-q", f"card/{B}")
    late = _commit(s["repo"], "late.txt", f"{A}: 审计后追加到分支")
    _git(s["repo"], "checkout", "-q", "main")
    _git(s["repo"], "merge", "-q", "--no-ff", f"card/{B}", "-m", f"Merge card/{B}: 审计后追加")
    cov = commit_pass_coverage(s["repo"], s["gov"], late)
    _show(f"[②审计后追加] {cov['reason']}")
    assert not cov["covered"] and cov["how"] is None


def test_item2_cross_card_misuse_still_refused(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch)
    _git(s["repo"], "checkout", "-q", "-b", f"card/{Z}", s["deployed"])
    z1 = _commit(s["repo"], "z1.txt", f"{Z}: 与 B 区间无关的提交")
    _git(s["repo"], "checkout", "-q", "main")
    _git(s["repo"], "merge", "-q", "--no-ff", f"card/{Z}", "-m", f"Merge card/{Z}: 未审")
    auth = check_verdict_ref_authorization(s["vid"], s["gov"], [z1], s["repo"])
    _show(f"[②跨卡挪用 B 裁决覆盖 {Z}] 部署授权原文:\n{auth['message']}")
    assert not auth["authorized"] and z1[:8] in "\n".join(auth["uncovered_commits"])


def test_item2_commit_inside_baseline_not_in_range(tmp_path, monkeypatch):
    """早于 B 基线、已在 main 的旧提交(带 A 卡号)是 B tip 的祖先, 但在 B 交回时基线之内 = 不在审计区间, 不认。"""
    s = _scenario(tmp_path, monkeypatch)
    cov = commit_pass_coverage(s["repo"], s["gov"], s["old"])
    _show(f"[②基线之内] {cov['reason']}")
    assert not cov["covered"]


def test_item2_unmerged_card_range_not_recognized(tmp_path, monkeypatch):
    s = _scenario(tmp_path, monkeypatch, merge=False)
    cov = commit_pass_coverage(s["repo"], s["gov"], s["a1"])
    _show(f"[②未合入卡] {cov['reason']}")
    assert not cov["covered"] and "未合入" in cov["reason"]


def test_item2_return_record_binding_mismatch_not_recognized(tmp_path, monkeypatch):
    """裁决所审交回记录绑定 ≠ 裁决绑定(或裁决不引用交回记录)= 基线推不出, 不认。"""
    s = _scenario(tmp_path, monkeypatch)
    for path in (s["gov"] / "5_tasks" / "records" / "audit_verdicts" / B).glob("*.md"):
        path.unlink()
    _verdict(s["gov"], B, s["b1"], None)
    cov = commit_pass_coverage(s["repo"], s["gov"], s["a1"])
    _show(f"[②裁决不引用交回记录] {cov['reason']}")
    assert not cov["covered"] and "基线推不出" in cov["reason"]


# ---------------------------------------------------------------------------
# ③ 撤卡预演: 有提交 / 无提交 / 无分支
# ---------------------------------------------------------------------------

CLAIMED = {"claim_id": f"claim_{A}_20261101_000000_exec-lybra-test", "claimed_by": f78.EXEC, "claimed_at": "2026-11-01T00:00:00Z",
           "active_session_id": f"session_{A}_20261101_000000_exec-lybra-test"}


def _withdraw_preview(gov: Path, task_id: str) -> dict:
    return withdraw_task(task_id=task_id, actor=f78.DRIVER, reason="靶场撤卡预演", dry_run=True, repo_root=gov)


def test_item3_withdraw_preview_hints_supersede_when_branch_has_commits(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    f78._card(gov, A, "claimed", extra=CLAIMED)
    _git(repo, "checkout", "-q", "-b", f"card/{A}")
    _commit(repo, "a1.txt", f"{A} 件①")
    _commit(repo, "a2.txt", f"{A} 件②")
    _git(repo, "checkout", "-q", "main")
    res = _withdraw_preview(gov, A)
    _show(f"[③有提交·撤卡预演原文]\n{json.dumps(res, ensure_ascii=False, indent=1)}")
    assert res["ok"] and res["verdict"] != "BLOCK"  # 只提示, 不拦截撤卡
    hint = res["data"]["branch_commits"]
    assert hint["count"] == 2 and hint["branch"] == f"card/{A}"
    line = next(w for w in res["warnings"] if "supersede" in w)
    assert f"lybra queue supersede --task-id {A} --by <新卡>" in line and "--carry-branch" in line and "2 个提交" in line
    assert "复用提交日后部署覆盖会断" in line


def test_item3_withdraw_preview_no_hint_without_commits(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    f78._card(gov, A, "claimed", extra=CLAIMED)
    _git(repo, "branch", f"card/{A}")  # 有分支, 相对基线 0 个提交
    f78._card(gov, Z, "pending")  # 无分支
    for task_id in (A, Z):
        res = _withdraw_preview(gov, task_id)
        _show(f"[③无提交 / 无分支·撤卡预演原文 {task_id}]\n{json.dumps(res, ensure_ascii=False, indent=1)}")
        assert res["ok"] and res["data"]["branch_commits"] is None
        assert not any("supersede" in w for w in res["warnings"])


# ---------------------------------------------------------------------------
# 声明 / 单一实现 / 不吞异常
# ---------------------------------------------------------------------------

def test_declarations_and_single_implementation():
    transitions = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    decl = transitions["nodes"]["N5"]["guards"]["commit_coverage"]["audited_range"]
    assert decl["how"] == "audited_range" and decl["base_key"] == "base_commit"
    assert "{commit}" in decl["texts"]["covered"] and "{covering_task_id}" in decl["texts"]["covered"]
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    texts = verbs["verbs"]["lybra_queue_withdraw_dry_run"]["texts"]
    assert "--carry-branch" in texts["branch_commits"] and "{count}" in texts["branch_commits"]
    # 覆盖判据唯一实现: 审计区间只在 deployment_authorization 内判, 调用方不另写
    for path in sorted((REPO_ROOT / "tools").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name != "deployment_authorization.py":
            assert "def audited_range_coverage" not in text and "def commit_pass_coverage" not in text, path
    src = (REPO_ROOT / "tools" / "aipos_cli" / "deployment_authorization.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "audited_range_coverage")
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            assert not (isinstance(node.type, ast.Name) and node.type.id == "Exception"), "禁 except Exception"
            assert not all(isinstance(b, ast.Pass) for b in node.body), "禁吞异常"
