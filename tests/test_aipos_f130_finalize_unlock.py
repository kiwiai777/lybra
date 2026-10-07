"""AIPOS-F130 — 收尾链路解锁(gap #105, F129 finalize 死锁)。

实撞: Owner 授权 dev_override 部署 dbab322(F127 推送 500 后续做漏推漏部署 + 单裁决覆盖校验拒合并部署)之后, F129 及其后所有卡
finalize 全局锁死——
  ① finalize 完整性检查未传治理根 → dev_override 基线的两条出口(Owner 授权记录 / 世系)永远不可达;
  ② 世系以当前 commit 找精确裁决, finalize 的 merge --no-ff 提交从不被裁决绑定(裁决绑卡分支 tip) → 对合并提交恒不成立;
  ③ 部署 --verdict-ref 只认单裁决覆盖区间, 区间跨多张各自 PASS 的卡即拒, 与 finalize 完整性判据不一;
  ④ 续做路径在 finalization 已落/推送部署未完成时报成功、不重试推送与部署。

靶场 = 临时产品仓 + 临时治理根 + 假部署目录(部署脚本桩 / PATH 桩 systemctl·rsync), 绝不动真实 .deploy 与生产门。
卡号形状用靶场自造声明 RNG-…(证明不写死项目卡号前缀)。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f120_finalize_resumable import DEPLOY_STUB  # noqa: E402  — 靶场部署桩唯一来源
from tools.aipos_cli import finalize as fz  # noqa: E402
from tools.aipos_cli.deployment_authorization import (  # noqa: E402
    check_commit_interval_coverage,
    check_verdict_ref_authorization,
    commit_pass_coverage,
)
from tools.aipos_cli.finalization_record import existing_finalization_records  # noqa: E402

PATTERN = "RNG-[0-9A-Z]+"
EXEC = "exec.range"


def _show(msg: str) -> None:
    print(msg)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gov(root: Path) -> Path:
    for sub in ("pending", "claimed", "completed"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True, exist_ok=True)
    _write(root / "card_policy.json", json.dumps({"schema_version": "1.0.0", "task_id_pattern": PATTERN}))
    _write(root / "stage_archive" / "S1.md", "# S1\n")
    return root


def _verdict(gov: Path, task_id: str, tip: str | None, *, verdict: str = "PASS", stamp: str = "20261008_010000") -> str:
    vid = f"verdict_{task_id}_{stamp}_audit-range"
    subject = f"artifact_subject:\n  commit_sha: {tip}\n  tree_hash: dummy\n" if tip else ""
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / task_id / f"{vid}.md",
           "---\nrecord_type: audit_verdict_record\nevent_type: mcp_audit_verdict\n"
           f"verdict_id: {vid}\nverdict_at: '2026-10-08T01:00:00Z'\nreviewed_task_id: {task_id}\n"
           f"verdict: {verdict}\nauditor_instance: audit.range\n{subject}---\n# verdict\n")
    return vid


def _product(repo: Path, *, origin: Path | None = None, deploy_stub: str | None = DEPLOY_STUB) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "range")
    _git(repo, "config", "user.email", "range@range.local")
    _write(repo / ".gitignore", ".deploy/\n__pycache__/\n")
    _write(repo / "base.txt", "base\n")
    shutil.copytree(REPO_ROOT / "schema", repo / "schema", ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    if deploy_stub is not None:
        _write(repo / "tools" / "lybra-deploy", deploy_stub)
        (repo / "tools" / "lybra-deploy").chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    if origin is not None:
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
        _git(repo, "remote", "add", "origin", str(origin))
        _git(repo, "push", "-q", "-u", "origin", "main")


def _card_branch(repo: Path, task_id: str, n: int = 1) -> str:
    _git(repo, "checkout", "-q", "-b", f"card/{task_id}")
    for i in range(n):
        _write(repo / f"{task_id}-{i}.txt", f"impl {i}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"{task_id}: 件{i + 1}")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return tip


def _merge(repo: Path, task_id: str, vid: str) -> str:
    _git(repo, "merge", "-q", "--no-ff", f"card/{task_id}", "-m", f"Merge card/{task_id}: 靶场 ({vid})")
    return _git(repo, "rev-parse", "HEAD")


def _fake_deploy(repo: Path, commit: str, provenance: str) -> None:
    """假部署目录(只在临时产品仓): .deploy/current → releases/r-<commit>/VERSION。"""
    rel = repo / ".deploy" / "releases" / f"r-{commit[:8]}"
    _write(rel / "VERSION", f"git_commit: {commit}\ndeployment_provenance: {provenance}\nauthorization_type: {provenance}\n")
    link = repo / ".deploy" / "current"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(rel)


def _owner_record(gov: Path, commit: str) -> None:
    _write(gov / "5_tasks" / "records" / "owner_decisions" / "dec_range_override.md",
           "---\nrecord_type: owner_decision_record\ndecision_id: dec_range_override\ndecided_at: '2026-10-08T00:00:00Z'\n"
           "decision_status: approved\n---\n"
           f"# Owner 授权 dev_override 部署基线 {commit} 作为 finalize 起点\n")


# ---------------------------------------------------------------------------
# 件① finalize 完整性检查传治理根(调用方全量排查, 禁默认 None 静默)
# ---------------------------------------------------------------------------

def test_item1_all_callers_pass_governance_root_and_none_is_fail_closed(tmp_path: Path):
    calls = []
    for path in sorted((REPO_ROOT / "tools").rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "_check_deployment_integrity":
                calls.append((path.relative_to(REPO_ROOT).as_posix(), node.lineno, len(node.args) + len(node.keywords)))
    _show(f"[件①·调用方] {calls}")
    assert calls and all(n == 2 for _f, _l, n in calls), calls
    import inspect

    param = inspect.signature(fz._check_deployment_integrity).parameters["governance_root"]
    assert param.default is inspect.Parameter.empty, "governance_root 禁默认值(禁默认 None 静默)"
    repo = tmp_path / "p"
    _product(repo)
    _fake_deploy(repo, "0" * 40, "audited")
    res = fz._check_deployment_integrity(repo, None)
    _show(f"[件①·None] {res['message']}")
    assert res["integrity_ok"] is False and "governance_root" in res["message"] and "出口" in res["message"]


def test_item1_dev_override_with_owner_record_passes_loudly_without_record_refuses(tmp_path: Path, monkeypatch, capsys):
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo, origin=tmp_path / "origin.git")
    _write(repo / "hotfix.txt", "unaudited\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "hotfix without card id")
    _git(repo, "push", "-q")
    base = _git(repo, "rev-parse", "HEAD")
    _fake_deploy(repo, base, "dev_override")
    task = "RNG-101"
    tip = _card_branch(repo, task)
    _verdict(gov, task, tip)

    blocked = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show(f"[件①·无记录无世系] {blocked['verdict']}: {blocked['message']}")
    assert blocked["verdict"] == "BLOCK" and "dev_override" in blocked["message"] and "无 Owner 授权记录" in blocked["message"]
    assert _git(repo, "rev-parse", "HEAD") == base  # 未合并

    _owner_record(gov, base)
    integrity = fz._check_deployment_integrity(repo, gov)
    _show(f"[件①·Owner 记录] {integrity['message']}")
    assert integrity["integrity_ok"] is True and "dec_range_override.md" in integrity["message"] and "已认可" in integrity["message"]
    assert "已认可" in capsys.readouterr().err  # 出声(stderr Note)

    done = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件①·finalize 放行] " + done["verdict"] + "\n  " + "\n  ".join(done["operations"]))
    assert done["verdict"] == "PASS", done["message"]
    assert any("已认可" in op and "dec_range_override.md" in op for op in done["operations"])
    merge = _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", f"{merge}^2") == tip
    assert f"git_commit: {merge}" in (repo / ".deploy" / "current" / "VERSION").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 件② 世系认 merge 提交(第二父 = 该卡门生 PASS 裁决绑定的分支 tip)
# ---------------------------------------------------------------------------

def test_item2_lineage_recognizes_finalize_merge_commit_and_unlocks_next_finalize(tmp_path: Path, monkeypatch):
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo, origin=tmp_path / "origin.git")
    prev = "RNG-128"
    prev_tip = _card_branch(repo, prev)
    vid = _verdict(gov, prev, prev_tip)
    merge_prev = _merge(repo, prev, vid)
    _git(repo, "push", "-q")
    _fake_deploy(repo, merge_prev, "dev_override")  # 覆盖部署 = 前一张卡的合并提交(dbab322 同形)

    ok, why = fz._dev_override_base_authorized(repo, gov, merge_prev)
    _show(f"[件②·合并提交世系] {ok}: {why}")
    assert ok is True and "第二父" in why and vid in why
    cov = commit_pass_coverage(repo, gov, merge_prev)
    assert cov["covered"] and cov["how"] == "merge" and cov["task_id"] == prev

    nxt = "RNG-129"
    tip = _card_branch(repo, nxt)
    _verdict(gov, nxt, tip)
    res = fz.finalize_task(nxt, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件②·下一张卡 finalize] " + res["verdict"] + "\n  " + "\n  ".join(res["operations"]))
    assert res["verdict"] == "PASS", res["message"]
    assert any("世系" in op and "第二父" in op for op in res["operations"])
    assert len(existing_finalization_records(gov, nxt)) == 1


def test_item2_merge_commit_not_bound_to_verdict_tip_still_refused(tmp_path: Path):
    gov = _gov(tmp_path / "gov")
    repo = tmp_path / "product"
    _product(repo)
    task = "RNG-130"
    audited_tip = _card_branch(repo, task)
    vid = _verdict(gov, task, audited_tip)
    _git(repo, "checkout", "-q", f"card/{task}")
    _write(repo / "after-audit.txt", "changed after audit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{task}: 审后改动")
    _git(repo, "checkout", "-q", "main")
    merge = _merge(repo, task, vid)
    ok, why = fz._dev_override_base_authorized(repo, gov, merge)
    _show(f"[件②·第二父≠裁决绑定] {ok}: {why}")
    assert ok is False and "须复审" in why
    _fake_deploy(repo, merge, "dev_override")
    integrity = fz._check_deployment_integrity(repo, gov)
    assert integrity["integrity_ok"] is False and "dev_override" in integrity["message"]


def test_item2_single_implementation_reused():
    src = Path(fz.__file__).read_text(encoding="utf-8")
    body = src.split("def _dev_override_base_authorized", 1)[1].split("\nclass ", 1)[0]
    assert "commit_pass_coverage" in body and "_task_id_from_commit_subject" not in body
    assert "find_gate_pass_verdict_for_task" not in body  # 解析/裁决查找只在 deployment_authorization 一处


# ---------------------------------------------------------------------------
# 件③ 部署覆盖判据单源: --verdict-ref 用与 finalize 完整性同一个区间覆盖实现(多卡并集)
# ---------------------------------------------------------------------------

def _two_card_interval(tmp_path: Path) -> tuple[Path, Path, str, str, dict[str, str]]:
    """部署基线之后两张各自 PASS 的卡(F127 多提交 + F128, F128 实撞同形)。"""
    gov = _gov(tmp_path / "gov")
    repo = tmp_path / "product"
    _product(repo)
    base = _git(repo, "rev-parse", "HEAD")
    a, b = "RNG-127", "RNG-128"
    tip_a = _card_branch(repo, a, n=4)
    va = _verdict(gov, a, tip_a)
    _merge(repo, a, va)
    tip_b = _card_branch(repo, b, n=1)
    vb = _verdict(gov, b, tip_b)
    head = _merge(repo, b, vb)
    _verdict(gov, "RNG-999", base, stamp="20261008_020000")  # 区间外的 PASS 卡
    return gov, repo, base, head, {"a": va, "b": vb, "z": "verdict_RNG-999_20261008_020000_audit-range"}


def test_item3_multi_card_union_authorized_and_same_as_finalize_integrity(tmp_path: Path):
    gov, repo, base, head, v = _two_card_interval(tmp_path)
    commits = _git(repo, "log", "--format=%H", f"{base}..{head}").split()
    assert len(commits) == 7  # 4 + 合并 + 1 + 合并(F128 实撞: 7 提交跨两卡)
    auth = check_verdict_ref_authorization(v["b"], gov, commits, repo)
    _show(f"[件③·多卡并集] {auth['authorized']}: {auth['message']}\n  covering={auth['covering_verdicts']}")
    assert auth["authorized"] is True and set(auth["covering_verdicts"]) == {v["a"], v["b"]}
    assert {c["how"] for c in auth["covered_commits"]} == {"exact", "ancestor", "merge"}
    interval = check_commit_interval_coverage(repo, gov, base, head)
    assert interval["coverage_ok"] is True and interval["verdicts"] == auth["covering_verdicts"]  # 同一实现同一结论
    also_a = check_verdict_ref_authorization(v["a"], gov, commits, repo)
    assert also_a["authorized"] is True  # 并集内任一裁决可授权

    foreign = check_verdict_ref_authorization(v["z"], gov, commits, repo)
    _show(f"[件③·并集外裁决] {foreign['authorized']}: {foreign['message']}")
    assert foreign["authorized"] is False and "并集" in foreign["message"] and "跨卡挪用" in foreign["message"]

    _write(repo / "stray.txt", "x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "stray change without card")
    head2 = _git(repo, "rev-parse", "HEAD")
    commits2 = _git(repo, "log", "--format=%H", f"{base}..{head2}").split()
    refused = check_verdict_ref_authorization(v["b"], gov, commits2, repo)
    _show(f"[件③·有未审提交] {refused['authorized']}: {refused['message']}\n  uncovered={refused['uncovered_commits']}")
    assert refused["authorized"] is False and len(refused["uncovered_commits"]) == 1 and "无 task_id" in refused["uncovered_commits"][0]
    assert "dev-override" in refused["message"]
    assert check_commit_interval_coverage(repo, gov, base, head2)["coverage_ok"] is False


def test_item3_deploy_gate_and_deployment_record_list_all_covering_verdicts(tmp_path: Path, monkeypatch):
    from tools.aipos_cli import deployment_record
    from tools.aipos_cli.deploy_gate import invoke_lybra_deploy

    gov, repo, base, head, v = _two_card_interval(tmp_path)
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    _fake_deploy(repo, base, "audited")
    res = invoke_lybra_deploy(repo, verdict_ref=v["b"], actor=EXEC, governance_root=gov)
    _show(f"[件③·deploy_gate] success={res['success']} {res['stdout'].strip()} {res['stderr'].strip()[:300]}")
    assert res["success"] is True  # 原单裁决判据: 5 个 RNG-127 提交「跨卡挪用」拒

    rc = deployment_record.main(["--governance-root", str(gov), "--commit", head, "--actor", EXEC,
                                 "--verdict-ref", v["b"], "--coverage-base", base, "--repo-root", str(repo)])
    assert rc == 0
    recs = [r for r in (gov / "5_tasks" / "records" / "deployments").glob(f"deployment_*_{head[:8]}.md")
            if "coverage_interval" in r.read_text(encoding="utf-8")]
    assert len(recs) == 1
    rec = recs[0]
    text = rec.read_text(encoding="utf-8")
    _show(f"[件③·部署记录] {rec.name}\n{text}")
    assert f"coverage_interval: {base}..{head}" in text and v["a"] in text and v["b"] in text
    rc_bad = deployment_record.main(["--governance-root", str(gov), "--commit", head, "--verdict-ref", v["z"],
                                     "--coverage-base", base, "--repo-root", str(repo), "--check-only"])
    assert rc_bad == 2


def _deploy_range_clone(tmp_path: Path) -> Path:
    """lybra-deploy 靶场仓 = 本卡提交后的产品仓克隆(脚本以自身所在仓为产品仓), 分支 main, 假部署目录在克隆内。"""
    sha = _git(REPO_ROOT, "rev-parse", "HEAD")
    clone = tmp_path / "deploy-range"
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(REPO_ROOT), str(clone)], check=True, capture_output=True)
    _git(clone, "checkout", "-q", "-B", "main", sha)
    _git(clone, "config", "user.name", "range")
    _git(clone, "config", "user.email", "range@range.local")
    assert "--coverage-base" in (clone / "tools" / "lybra-deploy").read_text(encoding="utf-8"), "须先提交本卡脚本再跑夹具"
    return clone


def test_item3_lybra_deploy_script_runs_same_coverage_check_before_touching_releases(tmp_path: Path):
    clone = _deploy_range_clone(tmp_path)
    gov = _gov(tmp_path / "gov")
    base = _git(clone, "rev-parse", "HEAD")
    _fake_deploy(clone, base, "audited")
    a, b = "RNG-201", "RNG-202"
    tip_a = _card_branch(clone, a, n=2)
    va = _verdict(gov, a, tip_a)
    _merge(clone, a, va)
    tip_b = _card_branch(clone, b)
    vb = _verdict(gov, b, tip_b)
    _merge(clone, b, vb)
    vz = _verdict(gov, "RNG-299", base, stamp="20261008_030000")

    stubs = tmp_path / "stubs"
    marker = tmp_path / "stub-called.txt"
    for name in ("systemctl", "rsync"):  # 桩: 绝不碰真实门服务/真实同步
        _write(stubs / name, f"#!/usr/bin/env bash\necho {name} \"$@\" >> {marker}\nexit 97\n")
        (stubs / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ.get('PATH', '')}"}
    env.pop("PYTHONPATH", None)
    script = clone / "tools" / "lybra-deploy"

    refused = subprocess.run(["bash", str(script), "deploy", "--verdict-ref", vz, "--governance-root", str(gov), "--actor", EXEC],
                             cwd=str(clone), env=env, capture_output=True, text=True, timeout=300)
    _show(f"[件③·lybra-deploy 拒] rc={refused.returncode}\n{refused.stdout}{refused.stderr}")
    assert refused.returncode == 1 and "DEPLOY BLOCKED: verdict_ref 区间覆盖授权未过" in refused.stderr and "并集" in refused.stderr
    assert not marker.exists()
    assert sorted(p.name for p in (clone / ".deploy" / "releases").iterdir()) == [f"r-{base[:8]}"]

    passed = subprocess.run(["bash", str(script), "deploy", "--verdict-ref", vb, "--governance-root", str(gov), "--actor", EXEC],
                            cwd=str(clone), env=env, capture_output=True, text=True, timeout=300)
    _show(f"[件③·lybra-deploy 过] rc={passed.returncode}\n{passed.stdout}{passed.stderr}")
    assert "✓ Coverage check" in passed.stdout and va in passed.stdout and vb in passed.stdout
    assert marker.read_text(encoding="utf-8").startswith("rsync")  # 覆盖校验过后才进入快照步(桩止步, 不部署)


# ---------------------------------------------------------------------------
# 件④ 续做补完推送/部署, 如实写回, 不报成功而跳过
# ---------------------------------------------------------------------------

def _reject_pushes(origin: Path, on: bool) -> None:
    hook = origin / "hooks" / "pre-receive"
    if on:
        _write(hook, "#!/usr/bin/env bash\necho 'remote: 500 Internal Server Error (range)' >&2\nexit 1\n")
        hook.chmod(0o755)
    elif hook.exists():
        hook.unlink()


def test_item4_push_failed_then_resume_pushes_deploys_and_writes_record(tmp_path: Path, monkeypatch):
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo, origin = tmp_path / "product", tmp_path / "origin.git"
    _product(repo, origin=origin)
    task = "RNG-127"
    tip = _card_branch(repo, task, n=2)
    _verdict(gov, task, tip)

    _reject_pushes(origin, True)
    r1 = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件④·首跑推送失败] " + r1["verdict"] + ": " + r1["message"])
    merge = _git(repo, "rev-parse", "HEAD")
    assert r1["verdict"] == "FAIL" and "Push failed" in r1["message"]
    assert _git(repo, "rev-parse", f"{merge}^2") == tip and _git(repo, "rev-parse", "origin/main") != merge
    assert existing_finalization_records(gov, task) == [] and not (repo / ".deploy").exists()

    no_push = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=False, deploy=True)
    _show("[件④·续做未带 --push] " + no_push["verdict"] + ": " + no_push["message"])
    assert no_push["verdict"] == "BLOCK" and "--push" in no_push["message"] and "领先远端" in no_push["message"]
    assert existing_finalization_records(gov, task) == []

    still_down = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件④·续做推送仍失败] " + still_down["verdict"] + ": " + still_down["message"])
    assert still_down["verdict"] == "FAIL" and still_down["category"] == "FINALIZE_RESUME_PUSH_FAILED"
    assert existing_finalization_records(gov, task) == [] and not (repo / ".deploy").exists()

    _reject_pushes(origin, False)
    dry = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True, dry_run=True)
    assert dry["verdict"] == "PASS" and any("DRY-RUN: 续做将推送" in op for op in dry["operations"])
    assert any("DRY-RUN: 续做将部署" in op for op in dry["operations"]) and existing_finalization_records(gov, task) == []

    r2 = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件④·续做补完] " + r2["verdict"] + ": " + r2["message"] + "\n  " + "\n  ".join(r2["operations"]))
    assert r2["verdict"] == "PASS" and r2["resumed"] is True and r2["pushed"] is True and r2["deployed"] is True
    assert "推送/部署" in r2["message"]
    assert _git(repo, "rev-parse", "origin/main") == merge and _git(repo, "rev-parse", "HEAD") == merge
    assert f"git_commit: {merge}" in (repo / ".deploy" / "current" / "VERSION").read_text(encoding="utf-8")
    records = existing_finalization_records(gov, task)
    assert len(records) == 1
    text = records[0].read_text(encoding="utf-8")
    _show(f"[件④·记录] {records[0].name}\n{text}")
    assert f"merge_commit: {merge}" in text and "deploy_status: deployed" in text

    r3 = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    assert not r3.get("resumed") and len(existing_finalization_records(gov, task)) == 1, r3["message"]


def _later_clock(monkeypatch) -> None:
    """续做与首跑在同一秒内: 记录落盘名按秒, 只追加拒覆盖——续做取时拨后(真实场景两次 finalize 相隔远大于 1 秒)。"""
    from tools.aipos_cli import clock

    monkeypatch.setattr(clock, "iso_z", lambda *a, **k: "2099-01-01T00:00:00Z")


# 部署桩: 环境变量 RANGE_DEPLOY_FAIL 非空 = 模拟部署失败(不改仓内文件, 工作树保持干净), 否则同 F120 桩
SWITCHABLE_DEPLOY_STUB = ("#!/usr/bin/env bash\n"
                          "if [ -n \"${RANGE_DEPLOY_FAIL:-}\" ]; then echo '[range-deploy] simulated failure' >&2; exit 1; fi\n"
                          + DEPLOY_STUB.split("\n", 1)[1])


def test_item4_record_exists_but_deploy_or_push_incomplete_resume_appends_record(tmp_path: Path, monkeypatch):
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo, origin = tmp_path / "product", tmp_path / "origin.git"
    _product(repo, origin=origin, deploy_stub=SWITCHABLE_DEPLOY_STUB)
    task = "RNG-140"
    tip = _card_branch(repo, task)
    _verdict(gov, task, tip)
    monkeypatch.setenv("RANGE_DEPLOY_FAIL", "1")

    r1 = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件④·首跑部署失败] " + r1["verdict"] + ": " + r1["message"])
    merge = _git(repo, "rev-parse", "HEAD")
    assert r1["verdict"] == "FAIL" and r1["pushed"] is True
    first = existing_finalization_records(gov, task)
    assert len(first) == 1 and "deploy_status: deploy_failed" in first[0].read_text(encoding="utf-8")

    monkeypatch.delenv("RANGE_DEPLOY_FAIL")  # 部署原因修复
    _later_clock(monkeypatch)
    # 续做: 记录已有, 部署未含本卡合并提交 → 续做部署 + 追加记录(只追加, 旧记录不改)
    r2 = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件④·记录已有·续做部署] " + r2["verdict"] + ": " + r2["message"] + "\n  " + "\n  ".join(r2["operations"]))
    assert r2["verdict"] == "PASS" and r2["resumed"] is True and r2["deployed"] is True, r2["message"]
    records = existing_finalization_records(gov, task)
    assert len(records) == 2 and records[1] == first[0]
    assert "deploy_status: deploy_failed" in first[0].read_text(encoding="utf-8")
    newest = records[0].read_text(encoding="utf-8")
    assert "deploy_status: deployed" in newest and f"merge_commit: {merge}" in newest


def test_item4_record_exists_push_pending_resume_pushes_and_appends(tmp_path: Path, monkeypatch):
    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo, origin = tmp_path / "product", tmp_path / "origin.git"
    _product(repo)  # 首跑无远端: 推送不适用, 合并 + 部署 + 记录
    task = "RNG-141"
    tip = _card_branch(repo, task)
    _verdict(gov, task, tip)
    r1 = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    assert r1["verdict"] == "PASS", r1["message"]
    merge = _git(repo, "rev-parse", "HEAD")
    # 之后接上远端, 远端只有合并前的 main(本地 main 领先远端)
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", f"{merge}^1:refs/heads/main")
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "branch", "-q", "--set-upstream-to=origin/main", "main")

    _later_clock(monkeypatch)
    r2 = fz.finalize_task(task, EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("[件④·记录已有·续做推送] " + r2["verdict"] + ": " + r2["message"])
    assert r2["verdict"] == "PASS" and r2["resumed"] is True and r2["pushed"] is True, r2["message"]
    assert _git(repo, "rev-parse", "origin/main") == merge
    records = existing_finalization_records(gov, task)
    assert len(records) == 2 and "deploy_status: deployed" in records[0].read_text(encoding="utf-8")


def test_item4_finalization_record_append_only_never_overwrites(tmp_path: Path, monkeypatch):
    from tools.aipos_cli import clock

    gov = _gov(tmp_path / "gov")
    monkeypatch.setattr(clock, "iso_z", lambda *a, **k: "2099-01-01T00:00:00Z")
    ops: list[str] = []
    first = fz._ensure_finalization_record(gov, "RNG-150", EXEC, "a" * 40, "verdict_x", False, ops, deploy_status="deploy_failed")
    with pytest.raises(fz.FinalizationRecordError, match="只追加不覆盖"):
        fz._ensure_finalization_record(gov, "RNG-150", EXEC, "a" * 40, "verdict_x", True, ops, deploy_status="deployed")
    assert "deploy_status: deploy_failed" in (gov / first["path"]).read_text(encoding="utf-8")  # 旧记录未被覆盖
