"""AIPOS-F120 — finalize 可续: 合并与部署已完成但 finalization 记录未落时, 重跑补记录走完结案; 合并后同进程不混用新旧代码。

实撞(10-06 F109): `lybra finalize --push --deploy` merge --no-ff 已提交 main、部署已完成, finalize 随后失败、记录未写;
重跑「分支已合并 → 跳过整合」+「F61: 无实际合并动作, 跳过 finalization 记录」→ 记录永远补不上, 推导核反复派生 finalize。

定因(靶场复现, 见 RETURN): `lybra` 是 editable 安装, 代码源 = 产品仓工作树; finalize 的 merge 在进程运行中把代码源换成新代码,
合并后首次懒加载的 finalization_record(新版)向已加载的旧版 record_writer 要新符号 record_dir → ImportError(不在捕获集内, 进程崩)。

覆盖:
件① 代码源 = 产品仓、卡分支改了合并后要用的模块(制造新旧不兼容) → 全新进程跑 finalize: 合并 + 推送 + 部署 + 记录全链 PASS
    (合并前预载 + 合并后导入闸; 闸在 = 清单闭合); 合并后试图加载未预载的产品模块 → 闸拒, finalize 收成 FAIL + 续跑出口(不以 traceback 退出)。
件② 中途失败(合并 + 部署后记录写失败)→ FAIL 带出口 → 推导核仍派 finalize → 重跑续跑: 识别本卡合并提交补写记录
    (merge_commit = 合并提交, deploy_status 依部署记录), 不重复合并/部署 → 推导核派 close → close 结案(验收①全链);
    找不到 / 不唯一的合并提交 → BLOCK 带出口, 不写记录(验收②, F61 禁写错误 commit 证据守住)。
件③ loop: finalize 子进程失败时 stderr 关键行进 step.message 与 step.output(分段, 不再与 stdout 粘连)→ loop JSON 可取证。
"""
from __future__ import annotations

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

from test_aipos_f73d_loop_driver import (  # noqa: E402  — 靶场唯一来源(禁第二份靶场)
    EXEC,
    TASK,
    _card,
    _claim_record,
    _fm,
    _policy,
    _write,
    gov,  # noqa: F401  — pytest fixture re-export
)
from tools.aipos_cli import finalize as fz  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.board_adapter import close_task  # noqa: E402
from tools.aipos_cli.finalization_record import existing_finalization_records  # noqa: E402
from tools.aipos_cli.next_resolver import derive_next_step  # noqa: E402

VERDICT_ID = f"verdict_{TASK}_20261006_094147_audit-range"

# 靶场部署桩: 绝不跑真实 lybra-deploy(它会重启生产门)。桩 = 写 .deploy/current/VERSION + 门生形部署记录(落 LYBRA_WORKSPACE_ROOT)
DEPLOY_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="$(git rev-parse --show-toplevel)"
    HEAD="$(git rev-parse HEAD)"
    REL="$ROOT/.deploy/releases/r-$HEAD"
    mkdir -p "$REL"
    printf 'git_commit: %s\\ndeployment_provenance: audited\\nauthorization_type: verdict_ref\\n' "$HEAD" > "$REL/VERSION"
    ln -sfn "$REL" "$ROOT/.deploy/current"
    if [ -n "${LYBRA_WORKSPACE_ROOT:-}" ]; then
      DEP="$LYBRA_WORKSPACE_ROOT/5_tasks/records/deployments"
      mkdir -p "$DEP"
      printf -- '---\\nrecord_type: deployment_record\\noperation: deploy\\ncommit: %s\\ncommit_short: %s\\n---\\n# Deployment\\n' \\
        "$HEAD" "${HEAD:0:8}" > "$DEP/deployment_20261006_094220_${HEAD:0:8}.md"
    fi
    echo "[range-deploy] deployed $HEAD"
    """)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _init_product(repo: Path, *, deploy_stub: bool = True, with_origin: Path | None = None) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "range")
    _git(repo, "config", "user.email", "range@range.local")
    (repo / ".gitignore").write_text(".deploy/\n__pycache__/\n", encoding="utf-8")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    # 产品仓同 lybra 产品仓带 schema/(部署授权的归属解析读产品仓 schema 的卡号/分支声明)
    shutil.copytree(REPO_ROOT / "schema", repo / "schema", ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    if deploy_stub:
        stub = repo / "tools" / "lybra-deploy"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(DEPLOY_STUB, encoding="utf-8")
        stub.chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    if with_origin is not None:
        subprocess.run(["git", "init", "-q", "--bare", str(with_origin)], check=True, capture_output=True)
        _git(repo, "remote", "add", "origin", str(with_origin))
        _git(repo, "push", "-q", "-u", "origin", "main")


def _card_branch(repo: Path, task_id: str, files: dict[str, str] | None = None) -> str:
    _git(repo, "checkout", "-q", "-b", f"card/{task_id}")
    for rel, content in (files or {f"{task_id}.txt": "impl\n"}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"feat({task_id}): impl")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return tip


def _gov_for_finalize(gov_root: Path, task_id: str, tip: str) -> None:
    """治理根补齐 finalize 前置: 门生 PASS 裁决(artifact_subject 绑卡分支 tip)、阶段快照、卡号形状声明。"""
    _write(gov_root / "card_policy.json", json.dumps({"schema_version": "1.0.0", "task_id_pattern": "AIPOS-[A-Z0-9]+"}))
    _write(gov_root / "stage_archive" / "S1.md", "# S1\n")
    _write(gov_root / "5_tasks" / "records" / "audit_verdicts" / task_id / f"{VERDICT_ID}.md",
           "---\nrecord_type: audit_verdict_record\nevent_type: mcp_audit_verdict\n"
           f"verdict_id: {VERDICT_ID}\nverdict_at: '2026-10-06T09:41:47Z'\nreviewed_task_id: {task_id}\n"
           f"verdict: PASS_WITH_NOTES\nauditor_instance: audit.lybra.test\nartifact_subject:\n  commit_sha: {tip}\n  tree_hash: dummy\n---\n# verdict\n")


# ---------------------------------------------------------------------------
# 件① 代码源 = 产品仓工作树, 合并改写代码源: 全新进程跑 finalize 全链
# ---------------------------------------------------------------------------

_DRIVER = textwrap.dedent("""\
    import json, sys
    code_root, gov, product = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, code_root)
    import tools
    assert tools.__file__.startswith(code_root), tools.__file__
    from pathlib import Path
    from tools.aipos_cli.finalize import finalize_task
    r = finalize_task(sys.argv[4], "exec.range", Path(product), governance_root=Path(gov), push=True, deploy=sys.argv[5] == "deploy")
    # 已加载模块对象里出现 F120_PROBE = 本进程加载过卡分支合进来的新版(盘上文件此时已是新版, 只看已加载对象)
    late = sorted(m for m in sys.modules if m.startswith("tools.") and hasattr(sys.modules[m], "F120_PROBE"))
    print(json.dumps({"verdict": str(r["verdict"]), "message": r["message"], "operations": r["operations"],
                      "probe_modules_loaded": late}, ensure_ascii=False))
    """)


def _copy_code_into(product: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "test_*.py")
    shutil.copytree(REPO_ROOT / "tools", product / "tools", ignore=ignore, dirs_exist_ok=True)
    shutil.copytree(REPO_ROOT / "schema", product / "schema", ignore=ignore, dirs_exist_ok=True)
    (product / "tools" / "lybra-deploy").write_text(DEPLOY_STUB, encoding="utf-8")  # 覆盖真实部署脚本(禁重启生产门)
    (product / "tools" / "lybra-deploy").chmod(0o755)


@pytest.mark.parametrize("mode", ["deploy", "no-deploy-mechanism"])
def test_f120_item1_merge_rewrites_code_source_finalize_completes_without_mixing(tmp_path: Path, mode: str):
    task = "AIPOS-F120T"
    gov_root = tmp_path / "gov"
    for sub in ("pending", "claimed", "completed"):
        (gov_root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    product = tmp_path / "product"
    _init_product(product, deploy_stub=False, with_origin=tmp_path / "origin.git")
    _copy_code_into(product)
    if mode == "no-deploy-mechanism":
        (product / "tools" / "lybra-deploy").unlink()
    _git(product, "add", "-A")
    _git(product, "commit", "-q", "-m", "code source = product repo working tree (editable install)")
    _git(product, "push", "-q", "origin", "main")
    # 卡分支制造 F109 同形的新旧不兼容: 新 finalization_record 向 record_writer 要只在新版才有的符号
    fr = (product / "tools" / "aipos_cli" / "finalization_record.py").read_text(encoding="utf-8")
    rw = (product / "tools" / "aipos_cli" / "record_writer.py").read_text(encoding="utf-8")
    tip = _card_branch(product, task, {
        "tools/aipos_cli/record_writer.py": rw + "\nF120_PROBE = 'new'\n",
        "tools/aipos_cli/finalization_record.py": fr.replace(
            "from tools.aipos_cli.clock import iso_z\n",
            "from tools.aipos_cli.clock import iso_z\nfrom tools.aipos_cli.record_writer import F120_PROBE  # noqa: F401\n", 1),
    })
    _gov_for_finalize(gov_root, task, tip)
    env = {**os.environ, "LYBRA_WORKSPACE_ROOT": str(gov_root)}
    env.pop("LYBRA_CONNECTION_JSON", None)
    proc = subprocess.run([sys.executable, "-c", _DRIVER, str(product), str(gov_root), str(product), task,
                           "deploy" if mode == "deploy" else "plain"],
                          cwd=str(gov_root), env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    ops = "\n".join(out["operations"])
    head = _git(product, "rev-parse", "HEAD")
    # 合并确实改写了代码源(工作树已是新版), 而本进程从未加载新版模块
    assert "F120_PROBE" in (product / "tools" / "aipos_cli" / "finalization_record.py").read_text(encoding="utf-8")
    assert out["probe_modules_loaded"] == [], out
    assert out["verdict"] == "PASS", f"{out['message']}\n{ops}\n{proc.stderr}"
    assert "AIPOS-F120: 合并前已预载" in ops
    assert _git(product, "rev-parse", f"{head}^2") == tip  # merge --no-ff 绑本卡 tip
    records = existing_finalization_records(gov_root, task)
    assert len(records) == 1, ops
    text = records[0].read_text(encoding="utf-8")
    assert f"merge_commit: {head}" in text
    if mode == "deploy":
        assert "deploy_status: deployed" in text
        assert f"git_commit: {head}" in (product / ".deploy" / "current" / "VERSION").read_text(encoding="utf-8")
    else:
        assert "deploy_status: skipped" in text
    assert _git(product, "rev-parse", "origin/main") == head  # 推送完成


def test_f120_item1_post_merge_unpreloaded_import_is_blocked_and_finalize_fails_with_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    ops: list[str] = []
    gov_root = tmp_path / "gov"
    (gov_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    fz._preload_post_merge(gov_root, "AIPOS-F120T", ops)
    try:
        assert any(isinstance(f, fz._PostMergeImportGuard) for f in sys.meta_path)
        with pytest.raises(fz._PostMergeImportBlocked, match="未预载的产品模块 tools.aipos_cli._f120_never_preloaded"):
            __import__("tools.aipos_cli._f120_never_preloaded")
        import json as _stdlib  # noqa: F401  — 闸只拦产品包, 不拦标准库
    finally:
        fz._remove_post_merge_guard()
    assert not any(isinstance(f, fz._PostMergeImportGuard) for f in sys.meta_path)

    def interrupted(*a, operations, **k):
        operations.append("merged + deployed")
        fz._preload_post_merge(gov_root, "AIPOS-F120T", operations)
        __import__("tools.aipos_cli._f120_never_preloaded")

    monkeypatch.setattr(fz, "_finalize_task_impl", interrupted)
    result = fz.finalize_task("AIPOS-F120T", EXEC, tmp_path, governance_root=gov_root, push=True, deploy=True)
    assert result["verdict"] == "FAIL" and result["category"] == "FINALIZE_INTERRUPTED_AFTER_MERGE"
    assert "续跑" in result["message"] and "不重复合并、不重复部署" in result["message"]
    assert "merged + deployed" in result["operations"]
    assert not any(isinstance(f, fz._PostMergeImportGuard) for f in sys.meta_path)  # 外壳 finally 拆闸


# ---------------------------------------------------------------------------
# 件② + 验收①: 中途失败 → 续跑补记录 → close 全链
# ---------------------------------------------------------------------------

def _n4_state(gov_root: Path, task_id: str, tip: str) -> None:
    # 卡面齐门 close 必填(queue_mutation complete 校验), 认领实例 = EXEC(finalize/close actor==claimer)
    _card(gov_root, task_id, "claimed", assigned=EXEC, extra={
        "claimed_by": EXEC, "claimed_at": "'2026-10-06T08:00:00Z'", "claim_id": f"claim_{task_id}_x",
        "active_session_id": f"session_{task_id}_x", "context_bundle": EXEC, "priority": "high",
        "created_by": "advisor.lybra.test", "needs_owner": False, "output_target": "tools/", "artifact_policy": "formal_write"})
    _claim_record(gov_root, task_id, EXEC)
    rec = gov_root / "5_tasks" / "records"
    _write(rec / "returns" / task_id / f"return_{task_id}_20261006_090000_{EXEC}.md",
           _fm({"record_type": "return", "task_id": task_id, "return_id": f"return_{task_id}_x", "agent_instance": EXEC,
                "returned_at": "2026-10-06T09:00:00Z", "return_status": "returned"}))
    _write(rec / "audit_dispatches" / f"{task_id}R" / "dispatch_x.md",
           _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": task_id, "dispatched_at": "2026-10-06T09:10:00Z"}))
    _gov_for_finalize(gov_root, task_id, tip)


def test_f120_acceptance1_interrupted_finalize_then_resume_then_close(gov: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    product = Path(json.loads((gov / "project.json").read_text(encoding="utf-8"))["code_repo"])
    _init_product(product, with_origin=product.parent / "origin.git")
    tip = _card_branch(product, TASK)
    _n4_state(gov, TASK, tip)
    d0 = derive_next_step(TASK, gov)
    assert d0["derivable"] and d0["verb"] == "lybra_finalize", d0

    # 中途失败: 合并 + 推送 + 部署之后记录写失败(记录落点被同名文件占住)
    blocker = gov / "5_tasks" / "records" / "finalizations" / TASK
    blocker.write_text("occupied\n", encoding="utf-8")
    r1 = fz.finalize_task(TASK, EXEC, product, governance_root=gov, push=True, deploy=True)
    print("[run1]", r1["verdict"], r1["message"], *r1["operations"], sep="\n  ")
    merge_commit = _git(product, "rev-parse", "HEAD")
    assert r1["verdict"] == "FAIL" and r1["category"] == "FINALIZE_INTERRUPTED_AFTER_MERGE", r1["message"]
    assert _git(product, "rev-parse", f"{merge_commit}^2") == tip and _git(product, "rev-parse", "origin/main") == merge_commit
    assert f"git_commit: {merge_commit}" in (product / ".deploy" / "current" / "VERSION").read_text(encoding="utf-8")
    deployments = sorted((gov / "5_tasks" / "records" / "deployments").glob("deployment_*.md"))
    assert len(deployments) == 1
    assert existing_finalization_records(gov, TASK) == []
    d1 = derive_next_step(TASK, gov)
    assert d1["verb"] == "lybra_finalize", d1  # 记录缺 = 推导核仍派 finalize(F109 卡住的现场)
    blocker.unlink()

    # 续跑判定(dry-run): 识别合并提交, 给出将补写的记录, 不写
    dry = fz.finalize_task(TASK, EXEC, product, governance_root=gov, push=True, deploy=True, dry_run=True)
    print("[dry-run]", dry["verdict"], dry["message"], *dry["operations"], sep="\n  ")
    assert dry["verdict"] == "PASS" and dry["resumed"] is True and dry["finalization_record"]["wrote"] is False
    assert dry["finalization_record"]["frontmatter"]["merge_commit"] == merge_commit
    assert existing_finalization_records(gov, TASK) == []

    # 续跑补记录: 不重复合并、不重复部署
    releases_before = sorted(p.name for p in (product / ".deploy" / "releases").iterdir())
    r2 = fz.finalize_task(TASK, EXEC, product, governance_root=gov, push=True, deploy=True)
    print("[run2]", r2["verdict"], r2["message"], *r2["operations"], sep="\n  ")
    assert r2["verdict"] == "PASS" and r2["resumed"] is True, r2["message"]
    assert _git(product, "rev-parse", "HEAD") == merge_commit  # 无新合并
    assert sorted(p.name for p in (product / ".deploy" / "releases").iterdir()) == releases_before  # 无新部署
    assert sorted((gov / "5_tasks" / "records" / "deployments").glob("deployment_*.md")) == deployments
    records = existing_finalization_records(gov, TASK)
    assert len(records) == 1
    fm_text = records[0].read_text(encoding="utf-8")
    print("[record]", records[0].name, fm_text, sep="\n")
    assert f"merge_commit: {merge_commit}" in fm_text and "deploy_status: deployed" in fm_text
    assert f"deployment_record_ref: {deployments[0].stem}" in fm_text and f"authorization_ref: {VERDICT_ID}" in fm_text

    # 推导核 N5→N6: 派 close(closure_evidence.finalize_commit_hash = 合并提交) → close 结案
    d2 = derive_next_step(TASK, gov)
    print("[derive]", d2["verb"], d2["command"], sep="\n  ")
    assert d2["derivable"] and d2["verb"] == "lybra_queue_close_dry_run", d2
    assert merge_commit in d2["command"]
    evidence = json.loads(d2["command"].split("--closure-evidence '", 1)[1].split("'", 1)[0])
    closed = close_task(task_id=TASK, actor=EXEC, closure_evidence=evidence, dry_run=False, repo_root=gov)
    print("[close]", json.dumps(closed, ensure_ascii=False, default=str)[:1500])
    assert closed.get("ok") is True, closed
    assert (gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()

    # 已有记录 → 重跑不再续跑补写(不产生第二条记录)
    r3 = fz.finalize_task(TASK, EXEC, product, governance_root=gov, push=True, deploy=True)
    assert not r3.get("resumed") and len(existing_finalization_records(gov, TASK)) == 1, r3["message"]


# ---------------------------------------------------------------------------
# 验收②: 找不到 / 不唯一的合并提交 → 拒(F61 禁写错误 commit 证据)
# ---------------------------------------------------------------------------

def _merged_range(tmp_path: Path, task_id: str) -> tuple[Path, Path, str]:
    gov_root = tmp_path / "gov"
    for sub in ("pending", "claimed", "completed"):
        (gov_root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    product = tmp_path / "product"
    _init_product(product)
    tip = _card_branch(product, task_id)
    _gov_for_finalize(gov_root, task_id, tip)
    return gov_root, product, tip


def test_f120_acceptance2_no_bound_merge_commit_refuses_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    task = "AIPOS-F120N"
    gov_root, product, tip = _merged_range(tmp_path, task)
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov_root))
    _git(product, "merge", "-q", "--ff-only", f"card/{task}")  # 快进合并: 无合并提交可绑定
    r = fz.finalize_task(task, EXEC, product, governance_root=gov_root, push=False, deploy=False)
    print("[ff]", r["verdict"], r["message"], sep="\n  ")
    assert r["verdict"] == "BLOCK" and r["resumed"] is True
    assert "无绑定本卡的合并提交" in r["message"] and "禁写错误 commit 证据" in r["message"] and "出口" in r["message"]
    assert existing_finalization_records(gov_root, task) == []


def test_f120_acceptance2_ambiguous_merge_commits_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    task = "AIPOS-F120A"
    gov_root, product, tip = _merged_range(tmp_path, task)
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov_root))
    _git(product, "merge", "-q", "--no-ff", f"card/{task}", "-m", f"Merge card/{task}: x ({VERDICT_ID})")
    _git(product, "checkout", "-q", "-b", "other")
    (product / "other.txt").write_text("o\n", encoding="utf-8")
    _git(product, "add", "-A")
    _git(product, "commit", "-q", "-m", "other")
    _git(product, "checkout", "-q", "main")
    _git(product, "merge", "-q", "--no-ff", "other", "-m", f"Merge other (mentions {VERDICT_ID})")
    found = fz.find_card_merge_commit(product, f"card/{task}", "main", VERDICT_ID, tip)
    assert found["found"] is False and len(found["candidates"]) == 2, found
    r = fz.finalize_task(task, EXEC, product, governance_root=gov_root, push=False, deploy=False)
    print("[ambiguous]", r["verdict"], r["message"], sep="\n  ")
    assert r["verdict"] == "BLOCK" and "不唯一" in r["message"]
    assert existing_finalization_records(gov_root, task) == []


def test_f120_item2_merge_found_by_artifact_binding_without_verdict_id_in_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    task = "AIPOS-F120B"
    gov_root, product, tip = _merged_range(tmp_path, task)
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov_root))
    _git(product, "merge", "-q", "--no-ff", f"card/{task}", "-m", f"Merge card/{task} by hand")
    merge_commit = _git(product, "rev-parse", "HEAD")
    found = fz.find_card_merge_commit(product, f"card/{task}", "main", VERDICT_ID, tip)
    assert found["found"] and found["merge_commit"] == merge_commit and "第二父" in found["reason"], found
    r = fz.finalize_task(task, EXEC, product, governance_root=gov_root, push=False, deploy=False)
    assert r["verdict"] == "PASS" and r["merge_commit"] == merge_commit, r["message"]
    # 有部署机制但无部署记录、当前部署不含合并提交 → 如实 not_attempted, 续跑不部署
    text = existing_finalization_records(gov_root, task)[0].read_text(encoding="utf-8")
    assert "deploy_status: not_attempted" in text and not (product / ".deploy").exists()


# ---------------------------------------------------------------------------
# 件③: finalize 子进程失败时 stderr 关键行进 loop JSON 的 step.message / step.output
# ---------------------------------------------------------------------------

def test_f120_item3_failed_step_stderr_key_lines_reach_loop_json(gov: Path, tmp_path: Path):
    from tools.aipos_cli.loop_driver import run_loop

    product = Path(json.loads((gov / "project.json").read_text(encoding="utf-8"))["code_repo"])
    _init_product(product)
    tip = _card_branch(product, TASK)
    _n4_state(gov, TASK, tip)
    _policy(gov)
    crash = tmp_path / "crash.py"
    crash.write_text("print('=== Finalize Result ===')\n"
                     "raise ImportError(\"cannot import name 'record_dir' from 'tools.aipos_cli.record_writer'\")\n",
                     encoding="utf-8")
    seen: list[str] = []

    def execute(derivation, governance_root, connection_json=None):
        seen.append(derivation["command"])
        return nr._run_product_command(f"{sys.executable} {crash}", nr._action_type_for_command(derivation["command"]))

    import io

    res = run_loop(TASK, gov, actor="advisor.lybra.test", out=io.StringIO(), execute=execute, interval=0.01, max_wait=1, max_steps=3)
    payload = json.loads(json.dumps(res.to_dict(), ensure_ascii=False))
    print(json.dumps(payload["steps"][-1], ensure_ascii=False, indent=1))
    assert seen and seen[0].startswith("lybra finalize")
    step = payload["steps"][-1]
    assert payload["outcome"] == "gate_rejected" and step["action_type"] == "finalize" and step["ok"] is False
    key = "ImportError: cannot import name 'record_dir' from 'tools.aipos_cli.record_writer'"
    assert step["message"] == f"finalize 失败: {key}"
    assert "=== Finalize Result ===\n[stderr]\nTraceback" in step["output"] and key in step["output"]
    assert key in payload["message"]


def test_f120_item3_stderr_key_lines_extraction():
    tb = "Traceback (most recent call last):\n  File \"x\", line 1\nValueError: boom\n"
    assert nr.stderr_key_lines(tb) == ["ValueError: boom"]
    assert nr.stderr_key_lines("Warning: a\nError: b\n✗ c\n") == ["Error: b", "✗ c"]
    assert nr.stderr_key_lines("plain last line\n\n") == ["plain last line"]
    assert nr.stderr_key_lines("") == []
