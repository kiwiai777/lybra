"""AIPOS-F78C — 多仓项目·卡声明仓贯通三件夹具(靶场分根: tmp 治理根 + 双产品仓, schema 从产品根读)。

件① 仓清单声明+发布校验: project.json `repos` {default, items} 声明在 config.schema project_json.schema.repos 一处;
    code_repo 降为兼容别名(= items[default], 冲突 REPOS_CONFLICT); 卡 lane.repo(仓名/绝对路径)须解析到清单内一项,
    否则 LANE_REPO_UNDECLARED 拒发布; 缺 lane.repo 派生 repos.default 仓名(有清单)/code_repo 路径(无清单)。
件② 单一解析函数 workspace_config.resolve_card_repo: worktree 落点/ingest 核 tip/finalize --workspace-root/裁决 artifact_subject/
    审计卡取证锚点/交回判据/claim 上下文/card render 全部改调; 隐式读 code_repo 的第二读法删除。
件③ 双仓靶场全链: 两张卡各 lane.repo 指一仓 → claim 建 worktree 落对仓 .worktrees/<ID> → 交回核对仓 tip → 裁决绑对仓 tip →
    finalize 合对仓 → close 证据取对仓 merge_commit, run_loop 到 completed; 第三张卡指未声明仓 → 拒 LANE_REPO_UNDECLARED;
    Return 自述 repo ≠ 卡 lane.repo → INGEST_REPO_MISMATCH; 单仓(无 repos 段)回归零感知; chris 形(code_repo=治理根非 git 仓)
    → ingest 停在 LANE_REPO_UNDECLARED 声明缺失出口而非崩溃。
靶场/替身复用 F73D/F78 夹具(禁第二份靶场)。
"""
from __future__ import annotations

import io
import json
import re
import shlex
import sys
from pathlib import Path

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
from test_aipos_f78_engine_agnostic import _card, _git, _init_product_repo  # noqa: E402
from tools.aipos_cli import finalize as fz  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import INGEST_EXIT_REJECTED, validate_task_artifact  # noqa: E402
from tools.aipos_cli.card_render import build_intent_model  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402
from tools.aipos_cli.machine_zone import derive_intent_declarations  # noqa: E402
from tools.aipos_cli.next_resolver import _ensure_worktree, derive_next_step  # noqa: E402
from tools.aipos_cli.record_writer import CLOSURE_ID_PREFIX  # noqa: E402
from tools.aipos_cli.workspace_config import (  # noqa: E402
    CardRepoUnresolved,
    default_lane_repo,
    project_repos,
    resolve_card_repo,
)

TASK_A = "AIPOS-F78CA"
TASK_B = "AIPOS-F78CB"
TASK_C = "AIPOS-F78CC"


# ---------------------------------------------------------------------------
# 靶场: 治理根 + 双产品仓(a, b), 单仓根(无 repos 段), chris 形根(code_repo=治理根, 非 git 仓)
# ---------------------------------------------------------------------------

def _product_repo(path: Path) -> Path:
    """产品仓: .gitignore 声明 .worktrees/(与真实产品仓同形, finalize 前工作树不算脏)。"""
    repo = _init_product_repo(path)
    _write(repo / ".gitignore", ".worktrees/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "gitignore worktrees")
    return repo


def _gov_skeleton(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events", "owner_decisions"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True)
    (root / "5_tasks" / "policies").mkdir()
    (root / "task_cards").mkdir()
    _write(root / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    return root


def _dual_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, code_repo_alias: bool = True) -> tuple[Path, dict[str, Path]]:
    """双仓项目: repos={a, b}, default=a; code_repo 别名(可选)= a 路径。"""
    root = _gov_skeleton(tmp_path / "gov-dual", monkeypatch)
    repos = {"a": _product_repo(tmp_path / "repo-a"), "b": _product_repo(tmp_path / "repo-b")}
    decl: dict = {"project": "lybra", "config_version": 1,
                  "repos": {"default": "a", "items": {k: str(v) for k, v in repos.items()}}}
    if code_repo_alias:
        decl["code_repo"] = str(repos["a"])
    _write(root / "project.json", json.dumps(decl))
    return root, repos


def _single_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """单仓项目(无 repos 段): 只有 code_repo(现行, 零感知)。"""
    root = _gov_skeleton(tmp_path / "gov-single", monkeypatch)
    repo = _product_repo(tmp_path / "repo-single")
    _write(root / "project.json", json.dumps({"project": "lybra", "code_repo": str(repo), "config_version": 1}))
    return root, repo


def _chris_shape_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """chris 现状形: 无 repos 段, code_repo 指治理根自身(不是 git 仓), 卡无 lane。"""
    root = _gov_skeleton(tmp_path / "gov-chris", monkeypatch)
    _write(root / "project.json", json.dumps({"project": "chris-huibojin", "code_repo": str(root), "config_version": 1}))
    return root


def _executor_commits_in_worktree(worktree: Path, task_id: str) -> tuple[str, str]:
    """执行体: 在工作树提交(含 test 文件, 交回判据同形), 返回 (sha, tree)。"""
    _write(worktree / "tools" / "aipos_cli" / f"{task_id.lower()}.py", f"# {task_id}\n")
    _write(worktree / "tests" / f"test_{task_id.lower()}.py", "def test_x(): pass\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", f"{task_id}: work")
    return _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")


def _return_md(task_id: str, sha: str, tree: str, *, repo: str | None = None) -> str:
    fm = {"commit_sha": sha, "tree_hash": tree, "branch": f"card/{task_id}", "model": "fixture-model"}
    if repo:
        fm["repo"] = repo
    return _fm(fm, f"# RETURN — {task_id}\n\n## 一句话结论\n完成。\n\n## 改动清单\n- x\n")


def _audit_report(gov: Path, task_id: str, sha: str) -> None:
    audit = f"{task_id}R"
    _write(gov / "task_cards" / audit / "audit_report.md",
           _fm({"task_id": audit, "reviewed_task_id": task_id, "verdict": "PASS", "commit_sha": sha,
                "actor": AUDITOR, "agent_instance": AUDITOR}, "# audit\n\n## 一句话结论\nPASS\n"))


class _RepoGate(GateDouble):
    """门侧替身(复用 F73D GateDouble 落记录), 但触仓的步做真事: return=真 ingest 校验(核对仓 tip); verdict=记下派生命令绑的 sha;
    finalize=按派生命令的 --workspace-root 真 merge(finalize._integrate_card_branch), finalization 记录 merge_commit=该仓 main tip;
    close=记下派生证据 finalize_commit_hash。"""

    def __init__(self, gov: Path):
        super().__init__(gov)
        self.seen: dict[tuple[str, str], object] = {}

    def __call__(self, derivation: dict, workspace_root: Path, connection_json=None) -> dict:
        cmd = derivation["command"]
        action = nr._action_type_for_command(cmd)
        card = derivation["task_id"]
        argv = shlex.split(cmd)
        if action == "return":
            chk = validate_task_artifact(card, self.gov)
            assert chk["ok"] and chk["category"] == "OK", chk
            self.seen[("return", card)] = chk["frontmatter"]["commit_sha"]
        elif action == "verdict":
            self.seen[("verdict", card[:-1])] = argv[argv.index("--artifact-subject-commit-sha") + 1]
        elif action == "finalize":
            ws = Path(argv[argv.index("--workspace-root") + 1])
            res = fz._integrate_card_branch(card, "verdict_x", ws, self.gov, False, [],
                                            branch_integration={"branch_pattern": "card/{task_id}"}, task_mode="code")
            assert not res["blocked"] and res["action"] == "merged", res
            merge = _git(ws, "rev-parse", "main")
            _write(self.gov / "5_tasks" / "records" / "finalizations" / card / f"finalization_{_ts()}.md",
                   _fm({"record_type": "finalization_record", "task_id": card, "commit": merge, "merge_commit": merge,
                        "finalized_at": "2026-09-22T04:00:00Z", "deploy_status": "deployed"}))
            self.calls.append((action, card))
            self.seen[("finalize", card)] = (ws, merge)
            return {"ok": True, "action_type": action, "message": "finalize 成功", "command": cmd, "exit_code": 0, "output": "ok"}
        elif action == "close":
            self.seen[("close", card)] = json.loads(argv[argv.index("--closure-evidence") + 1])["finalize_commit_hash"]
        return super().__call__(derivation, workspace_root, connection_json)


# ---------------------------------------------------------------------------
# 件① 声明一处 + 清单解析 + 发布校验
# ---------------------------------------------------------------------------

def test_f78c_item1_declaration_single_source_config_card_transitions():
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    pj = config["configuration_sources"]["project_json"]["schema"]
    repos = pj["repos"]
    assert repos["required"] is False and set(repos["schema"]) == {"default", "items"}
    assert set(repos["reject_codes"]) == {"REPOS_CONFLICT", "LANE_REPO_UNDECLARED", "INGEST_REPO_MISMATCH"}
    assert "兼容别名" in pj["code_repo"]["description"] and "repos.default" in pj["code_repo"]["description"]
    text = (REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8")
    card = json.loads(text)
    assert "一卡一仓" in text and "跨仓改动拆卡" in text and "列表形留待有真实案例" in text
    assert "resolve_card_repo" in json.dumps(card["intent_face"]["lane"]["derive"], ensure_ascii=False)
    trans = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    ret = trans["artifact_ingest"]["return"]
    assert ret["optional_frontmatter"] == ["repo"] and any("INGEST_REPO_MISMATCH" in c for c in ret["checks"])
    # 第二读法删除: 按卡取仓的模块不再各自 read_project_json().get("code_repo"); 唯一解析在 workspace_config
    per_card = ["machine_zone.py", "next_resolver.py", "artifact_ingest.py", "card_render.py", "audit_derivation.py",
                "board_adapter.py", "record_writer.py", "scoped_commit_check.py"]
    for name in per_card:
        src = (REPO_ROOT / "tools" / "aipos_cli" / name).read_text(encoding="utf-8")
        assert 'get("code_repo")' not in src, name
        assert "_resolve_code_repo_root" not in src, name
    mcp = (REPO_ROOT / "tools" / "mcp_server" / "tools.py").read_text(encoding="utf-8")
    assert 'project_json.get("code_repo")' not in mcp and 'project.get("code_repo")' not in mcp  # 只剩注册/set-repo 入参(项目级别名)
    # 仓清单形不写死第二份: 代码读 config.schema 声明(声明缺 = SchemaLoadError)
    wc = (REPO_ROOT / "tools" / "aipos_cli" / "workspace_config.py").read_text(encoding="utf-8")
    assert "_project_repos_declaration" in wc and "except Exception" not in wc.split("def project_repos")[1].split("def _utc_now_iso")[0]


def test_f78c_item1_project_repos_alias_and_conflicts(tmp_path, monkeypatch):
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    decl = project_repos(gov)
    assert decl["declared"] and decl["default"] == "a" and decl["items"] == repos and decl["code_repo"] == repos["a"]
    assert default_lane_repo(gov) == "a"
    # 无 repos 段 = 单仓零感知
    single, repo = _single_gov(tmp_path, monkeypatch)
    s = project_repos(single)
    assert s["declared"] is False and s["items"] == {} and s["code_repo"] == repo and default_lane_repo(single) == str(repo)
    # code_repo ≠ default 路径 → REPOS_CONFLICT
    _write(gov / "project.json", json.dumps({"project": "lybra", "code_repo": str(repos["b"]),
                                             "repos": {"default": "a", "items": {k: str(v) for k, v in repos.items()}}}))
    with pytest.raises(CardRepoUnresolved) as e1:
        project_repos(gov)
    assert e1.value.code == "REPOS_CONFLICT" and "兼容别名" in str(e1.value)
    # default 不在 items / 相对路径 / 形不对 → REPOS_CONFLICT
    for bad in ({"default": "zz", "items": {"a": str(repos["a"])}},
                {"default": "a", "items": {"a": "relative/path"}},
                {"default": "a"},
                ["a"]):
        _write(gov / "project.json", json.dumps({"project": "lybra", "repos": bad}))
        with pytest.raises(CardRepoUnresolved) as e:
            project_repos(gov)
        assert e.value.code == "REPOS_CONFLICT", bad
    # 声明缺 = SchemaLoadError(fail-closed, 不回退写死形)
    from tools.schema_loader import SchemaLoadError

    monkeypatch.setattr("tools.schema_loader.load_schema", lambda *a, **k: {"configuration_sources": {"project_json": {"schema": {}}}})
    with pytest.raises(SchemaLoadError):
        project_repos(gov)


def test_f78c_item1_publish_derives_default_name_and_rejects_undeclared_lane_repo(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft, publish_draft

    gov, repos = _dual_gov(tmp_path, monkeypatch)
    base = {"task_id": "X", "task_mode": "code", "assigned_to": EXEC, "output_target": "tools/aipos_cli/, tests/"}
    # 缺 lane.repo → 派生 repos.default 仓名(有清单写仓名, 不写路径)
    d = derive_intent_declarations(base, gov)
    assert d["blocking_reasons"] == [] and d["lane"]["repo"] == "a" and "lane.repo" in d["derived"], d
    # lane.repo 仓名 / 绝对路径 均可, 校验在清单内
    for ref in ("b", str(repos["b"])):
        ok = derive_intent_declarations({**base, "lane": {"repo": ref, "paths": ["tools/"], "roles": ["executor"]}}, gov)
        assert ok["blocking_reasons"] == [] and ok["lane"]["repo"] == ref, ok
    # 未声明仓 → LANE_REPO_UNDECLARED(禁自由文本路径)
    bad = derive_intent_declarations({**base, "lane": {"repo": str(tmp_path / "repo-c"), "paths": ["tools/"], "roles": ["executor"]}}, gov)
    assert len(bad["blocking_reasons"]) == 1 and bad["blocking_reasons"][0].startswith("LANE_REPO_UNDECLARED"), bad
    assert "['a', 'b']" in bad["blocking_reasons"][0]
    # 单仓项目(无清单): 缺省 = code_repo 路径; lane.repo ≠ code_repo → LANE_REPO_UNDECLARED, 出口指向声明 repos 清单
    single, repo = _single_gov(tmp_path, monkeypatch)
    s = derive_intent_declarations(base, single)
    assert s["blocking_reasons"] == [] and s["lane"]["repo"] == str(repo)
    s_bad = derive_intent_declarations({**base, "lane": {"repo": str(repos["b"]), "paths": ["tools/"], "roles": ["executor"]}}, single)
    assert s_bad["blocking_reasons"][0].startswith("LANE_REPO_UNDECLARED") and "repos" in s_bad["blocking_reasons"][0]
    # 清单冲突 → REPOS_CONFLICT 拒(派生也不放行)
    _write(gov / "project.json", json.dumps({"project": "lybra", "code_repo": str(repos["b"]),
                                             "repos": {"default": "a", "items": {k: str(v) for k, v in repos.items()}}}))
    c = derive_intent_declarations(base, gov)
    assert c["blocking_reasons"][0].startswith("REPOS_CONFLICT"), c
    # 验收②: 第三张卡 lane.repo 指未声明仓 → publish 拒 LANE_REPO_UNDECLARED(真发布口)
    _write(gov / "project.json", json.dumps({"project": "lybra", "repos": {"default": "a", "items": {k: str(v) for k, v in repos.items()}}}))
    meta = {"task_id": TASK_C, "title": "t", "project": "lybra", "assigned_to": EXEC, "context_bundle": "t", "task_mode": "code",
            "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False, "output_target": "tools/aipos_cli/",
            "artifact_policy": "formal_write", "lane": {"repo": str(tmp_path / "repo-c"), "paths": ["tools/aipos_cli/"], "roles": ["executor"]}}
    created = create_draft(gov, meta, "## Goal\n\n第三张卡, 仓未声明。\n", dry_run=False)
    assert created["verdict"] != "BLOCK", created
    published = publish_draft(gov, created["target_path"], dry_run=True)
    reasons = [r for r in published["blocking_reasons"] if r.startswith("LANE_REPO_UNDECLARED")]
    assert published["verdict"] == "BLOCK" and len(reasons) == 1 and "repos.items" in reasons[0], published["blocking_reasons"]
    assert not list((gov / "5_tasks" / "queue" / "pending").glob("*.md"))


# ---------------------------------------------------------------------------
# 件② 单一解析函数
# ---------------------------------------------------------------------------

def test_f78c_item2_resolve_card_repo_chain_and_fail_closed(tmp_path, monkeypatch):
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    assert resolve_card_repo(gov, {"task_id": "T", "lane": {"repo": "b"}}) == repos["b"]
    assert resolve_card_repo(gov, {"task_id": "T", "lane": {"repo": str(repos["b"])}}) == repos["b"]
    assert resolve_card_repo(gov, {"task_id": "T"}) == repos["a"]  # 缺 lane.repo → repos.default
    assert resolve_card_repo(gov, {}) == repos["a"] and resolve_card_repo(gov, None) == repos["a"]
    with pytest.raises(CardRepoUnresolved) as e:
        resolve_card_repo(gov, {"task_id": "T", "lane": {"repo": "c"}})
    assert e.value.code == "LANE_REPO_UNDECLARED" and "卡 T" in str(e.value) and "出口" in str(e.value)
    # 单仓: 缺 lane.repo → code_repo; lane.repo == code_repo 放行; 其它 → LANE_REPO_UNDECLARED
    single, repo = _single_gov(tmp_path, monkeypatch)
    assert resolve_card_repo(single, {"task_id": "T"}) == repo
    assert resolve_card_repo(single, {"task_id": "T", "lane": {"repo": str(repo)}}) == repo
    with pytest.raises(CardRepoUnresolved) as e2:
        resolve_card_repo(single, {"task_id": "T", "lane": {"repo": str(repos["a"])}})
    assert e2.value.code == "LANE_REPO_UNDECLARED"
    # 未注册靶场根(无 project.json)→ 治理根自身; 治理根自身是 git 仓 → 治理根自身
    bare = tmp_path / "bare"
    bare.mkdir()
    assert resolve_card_repo(bare, {}) == bare
    self_repo = _product_repo(tmp_path / "self-repo")
    _write(self_repo / "project.json", json.dumps({"project": "lybra", "config_version": 1}))
    assert resolve_card_repo(self_repo, {"task_id": "T"}) == self_repo
    # 已注册、无任何仓声明、治理根不是 git 仓 → 不猜路径, LANE_REPO_UNDECLARED 带 set-repo 出口
    reg = tmp_path / "registered"
    _write(reg / "project.json", json.dumps({"project": "lybra", "config_version": 1}))
    with pytest.raises(CardRepoUnresolved) as e3:
        resolve_card_repo(reg, {"task_id": "T"})
    assert e3.value.code == "LANE_REPO_UNDECLARED" and "set-repo" in str(e3.value)
    # 声明指向盘上不存在的路径 → REPO_PATH_MISSING
    _write(reg / "project.json", json.dumps({"project": "lybra", "code_repo": str(tmp_path / "nope"), "config_version": 1}))
    with pytest.raises(CardRepoUnresolved) as e4:
        resolve_card_repo(reg, {"task_id": "T"})
    assert e4.value.code == "REPO_PATH_MISSING" and "set-repo" in str(e4.value)


def test_f78c_item2_per_card_readers_all_route_through_resolve_card_repo(tmp_path, monkeypatch):
    """worktree 落点 / card render / finalize 派生 / 审计卡取证锚点 / 交回判据 / claim 上下文: 两张卡各取各仓。"""
    import tools.aipos_cli.board_adapter as adapter
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section

    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _card(gov, TASK_A, "claimed", extra={"lane": {"repo": "a", "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}})
    _card(gov, TASK_B, "claimed", extra={"lane": {"repo": str(repos["b"]), "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}})
    # worktree 落点 = 各自仓 .worktrees/<ID>(config.schema worktree_root 默认 {code_repo}/.worktrees)
    wa, wb = _ensure_worktree(gov, TASK_A), _ensure_worktree(gov, TASK_B)
    assert wa["ok"] and Path(wa["worktree_path"]) == repos["a"] / ".worktrees" / TASK_A, wa
    assert wb["ok"] and Path(wb["worktree_path"]) == repos["b"] / ".worktrees" / TASK_B, wb
    assert _git(repos["a"], "branch", "--list", f"card/{TASK_A}").strip().endswith(f"card/{TASK_A}")
    assert _git(repos["b"], "branch", "--list", f"card/{TASK_B}").strip().endswith(f"card/{TASK_B}")
    assert _git(repos["a"], "branch", "--list", f"card/{TASK_B}") == "" and _git(repos["b"], "branch", "--list", f"card/{TASK_A}") == ""
    assert not (gov / ".worktrees").exists()
    # card render: 开工提示的仓/工作树
    ma, mb = build_intent_model(TASK_A, gov), build_intent_model(TASK_B, gov)
    assert ma["code_repo"] == str(repos["a"]) and ma["worktree"] == wa["worktree_path"]
    assert mb["code_repo"] == str(repos["b"]) and mb["worktree"] == wb["worktree_path"]
    # 审计卡取证锚点: 被审卡声明的仓
    fm_b = nr._read_frontmatter(gov / "5_tasks" / "queue" / "claimed" / f"{TASK_B.lower()}.md")
    assert f"`{repos['b']}`" in build_forensic_anchor_section(TASK_B, gov, fm_b)
    assert f"`{repos['a']}`" in build_forensic_anchor_section(TASK_A, gov, nr._read_frontmatter(gov / "5_tasks" / "queue" / "claimed" / f"{TASK_A.lower()}.md"))
    # 交回判据/复审判定: 按卡取仓(card_frontmatter), 无卡 = 项目缺省仓(存量夹具单参形)
    assert adapter._resolve_product_code_repo(gov) == repos["a"]
    assert adapter._resolve_product_code_repo(gov, fm_b) == repos["b"]
    assert adapter._card_product_repo(gov, fm_b) == repos["b"] and adapter._card_product_repo(gov, None) == repos["a"]
    with pytest.raises(adapter.ProductRepoNotConfigured) as e:
        adapter._resolve_product_code_repo(gov, {"task_id": "Z", "lane": {"repo": "zz"}})
    assert e.value.code == "LANE_REPO_UNDECLARED"
    # 卡 B 分支在仓 b 有提交 → 判据⑤ 分支合规读仓 b(而非缺省仓 a)
    _executor_commits_in_worktree(Path(wb["worktree_path"]), TASK_B)
    assert adapter._check_branch_compliance(task_id=TASK_B, task_metadata=fm_b, repo_root=gov) == []
    assert adapter._check_has_tests(task_id=TASK_B, repo_root=gov, card_frontmatter=fm_b) == []
    # 未声明仓的卡 → 解析失败不猜: worktree/render 均 fail-closed 带出口
    _card(gov, TASK_C, "claimed", extra={"lane": {"repo": "c", "paths": ["tools/"], "roles": ["executor"]}})
    wc = _ensure_worktree(gov, TASK_C)
    assert wc["ok"] is False and "LANE_REPO_UNDECLARED" in wc["message"], wc
    with pytest.raises(ValueError, match="LANE_REPO_UNDECLARED"):
        build_intent_model(TASK_C, gov)


# ---------------------------------------------------------------------------
# 件③ 双仓靶场全链 + 一卡一仓拒 + 单仓回归 + chris 形停点
# ---------------------------------------------------------------------------

def test_f78c_item3_dual_repo_two_cards_full_chain_each_to_its_repo(tmp_path, monkeypatch):
    """验收①: 两张卡各 lane.repo 指一仓, claim→worktree→交回→裁决→finalize→close 全链 run_loop 到 completed, 每步取对仓。"""
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _policy(gov)
    cards = {TASK_A: ("a", repos["a"]), TASK_B: (str(repos["b"]), repos["b"])}
    tips: dict[str, str] = {}
    for task_id, (ref, repo) in cards.items():
        _card(gov, task_id, "claimed", extra={"lane": {"repo": ref, "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}})
        _claim_record(gov, task_id, EXEC)
        wt = _ensure_worktree(gov, task_id)
        assert wt["ok"] and Path(wt["worktree_path"]).parent == repo / ".worktrees", wt
        sha, tree = _executor_commits_in_worktree(Path(wt["worktree_path"]), task_id)
        tips[task_id] = sha
        _write(gov / "task_cards" / task_id / "RETURN.md", _return_md(task_id, sha, tree))
        _audit_report(gov, task_id, sha)  # 审计体报告(裁决绑该仓 tip)
    # 交回产物核对: 各自仓 tip(卡 A 的 sha 在仓 b 不存在, 反之亦然 → 取错仓必拒)
    for task_id, (_ref, repo) in cards.items():
        chk = validate_task_artifact(task_id, gov)
        assert chk["ok"] and chk["category"] == "OK", chk
        other = repos["b"] if repo == repos["a"] else repos["a"]
        assert nr._extract_artifact_subject_from_branch(other, task_id) is None  # 另一仓没有这条分支
    # 全链 run_loop: 每张卡各自到 completed
    results = {}
    for task_id, (_ref, repo) in cards.items():
        gate = _RepoGate(gov)
        res = run_loop(task_id, gov, actor=DRIVER, out=io.StringIO(), execute=gate, interval=0.02, max_wait=8, max_steps=20)
        assert res.exit_code == 0 and res.outcome == "completed", (task_id, res)
        assert [c[0] for c in gate.calls] == ["return", "dispatch", "claim", "verdict", "finalize", "close"], gate.calls
        results[task_id] = gate
        # 每步取对仓: 交回核的 sha = 该仓分支 tip; 裁决绑同一 sha; finalize 合入该仓; close 证据 = 该仓 main tip(merge_commit)
        assert gate.seen[("return", task_id)] == tips[task_id]
        assert gate.seen[("verdict", task_id)] == tips[task_id]
        ws, merge = gate.seen[("finalize", task_id)]
        assert ws == repo and merge == _git(repo, "rev-parse", "main") and merge != tips[task_id]
        assert gate.seen[("close", task_id)] == merge
        assert _git(repo, "branch", "--merged", "main").find(f"card/{task_id}") >= 0
        fin = [s for s in res.steps if s.action_type == "finalize"][0]
        assert f"--workspace-root {repo}" in fin.command and f"--governance-root {gov}" in fin.command and f"--actor {EXEC}" in fin.command
        assert list((gov / "5_tasks" / "records" / "closures" / task_id).glob(f"{CLOSURE_ID_PREFIX}_*.md"))
        assert (gov / "5_tasks" / "queue" / "completed" / f"{task_id.lower()}.md").is_file()
    # 两仓互不串: 仓 a 只合了卡 A, 仓 b 只合了卡 B
    assert TASK_A in _git(repos["a"], "log", "--oneline", "main") and TASK_B not in _git(repos["a"], "log", "--oneline", "main")
    assert TASK_B in _git(repos["b"], "log", "--oneline", "main") and TASK_A not in _git(repos["b"], "log", "--oneline", "main")
    assert results[TASK_A].seen[("finalize", TASK_A)][1] != results[TASK_B].seen[("finalize", TASK_B)][1]


def test_f78c_item3_ingest_rejects_return_repo_mismatch_and_verdict_uses_reviewed_card_repo(tmp_path, monkeypatch):
    """验收②: Return 自述 repo ≠ 卡 lane.repo 解析仓 → INGEST_REPO_MISMATCH; R 卡核被审卡声明仓的 tip。"""
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    _card(gov, TASK_A, "claimed", extra={"lane": {"repo": "a", "paths": ["tools/"], "roles": ["executor"]}})
    _claim_record(gov, TASK_A, EXEC)
    wt = _ensure_worktree(gov, TASK_A)
    sha, tree = _executor_commits_in_worktree(Path(wt["worktree_path"]), TASK_A)
    ret = gov / "task_cards" / TASK_A / "RETURN.md"
    for wrong in ("b", str(repos["b"]), "not-a-repo"):
        _write(ret, _return_md(TASK_A, sha, tree, repo=wrong))
        chk = validate_task_artifact(TASK_A, gov)
        assert chk["ok"] is False and chk["category"] == "INGEST_REPO_MISMATCH" and chk["exit_code"] == INGEST_EXIT_REJECTED, (wrong, chk)
        assert "一卡一仓" in chk["reasons"][0] and str(repos["a"]) in chk["reasons"][0]
    for right in ("a", str(repos["a"]), None):
        _write(ret, _return_md(TASK_A, sha, tree, repo=right))
        assert validate_task_artifact(TASK_A, gov)["ok"], right
    # 推导核派生的 return 命令与 ingest 同一路径: mismatch 时 ingest 主函数拒, 不铸记录
    from tools.aipos_cli.artifact_ingest import ingest_task_artifact

    _write(ret, _return_md(TASK_A, sha, tree, repo="b"))
    res = ingest_task_artifact(TASK_A, gov, dry_run=True)
    assert res["ok"] is False and res["category"] == "INGEST_REPO_MISMATCH", res
    assert not list((gov / "5_tasks" / "records" / "returns").glob("*/*.md"))
    # R 卡: 被审卡 lane.repo=a, 报告 commit_sha=仓 a tip → OK; 被审卡改指仓 b(分支不在 b)→ INGEST_BRANCH_MISSING 且指名仓 b
    _card(gov, f"{TASK_A}R", "claimed", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": TASK_A})
    _audit_report(gov, TASK_A, sha)
    assert validate_task_artifact(f"{TASK_A}R", gov)["ok"]
    _card(gov, TASK_A, "claimed", extra={"lane": {"repo": "b", "paths": ["tools/"], "roles": ["executor"]}})
    r2 = validate_task_artifact(f"{TASK_A}R", gov)
    assert r2["category"] == "INGEST_BRANCH_MISSING" and str(repos["b"]) in r2["reasons"][0], r2


def test_f78c_item3_single_repo_regression_zero_migration(tmp_path, monkeypatch):
    """验收③: 无 repos 段项目: 卡无 lane.repo/lane.repo=code_repo 都走 code_repo, 全链派生与 F73D 同形。"""
    gov, repo = _single_gov(tmp_path, monkeypatch)
    _policy(gov)
    _card(gov, TASK_A, "claimed")  # 无 lane(存量卡形)
    _card(gov, TASK_B, "claimed", extra={"lane": {"repo": str(repo), "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}})
    for task_id in (TASK_A, TASK_B):
        _claim_record(gov, task_id, EXEC)
        wt = _ensure_worktree(gov, task_id)
        assert wt["ok"] and Path(wt["worktree_path"]) == repo / ".worktrees" / task_id, wt
        sha, tree = _executor_commits_in_worktree(Path(wt["worktree_path"]), task_id)
        _write(gov / "task_cards" / task_id / "RETURN.md", _return_md(task_id, sha, tree))
        assert validate_task_artifact(task_id, gov)["ok"]
        assert build_intent_model(task_id, gov)["code_repo"] == str(repo)
        _audit_report(gov, task_id, sha)
        gate = _RepoGate(gov)
        res = run_loop(task_id, gov, actor=DRIVER, out=io.StringIO(), execute=gate, interval=0.02, max_wait=8, max_steps=20)
        assert res.exit_code == 0 and res.outcome == "completed", res
        assert gate.seen[("finalize", task_id)][0] == repo and gate.seen[("close", task_id)] == _git(repo, "rev-parse", "main")
    # project.json 一字未改(零感知): 仍无 repos 段
    assert "repos" not in json.loads((gov / "project.json").read_text(encoding="utf-8"))


def test_f78c_item3_chris_shape_ingest_stops_at_declaration_missing_not_crash(tmp_path, monkeypatch):
    """验收⑤ 靶场镜像: code_repo=治理根(非 git 仓)+卡无 lane → ingest 停在 LANE_REPO_UNDECLARED(声明缺失出口), 不进 git 不崩溃, 目录不动。"""
    import hashlib

    gov = _chris_shape_gov(tmp_path, monkeypatch)
    _card(gov, "HBJ-F78C-1", "claimed", extra={"project": "chris-huibojin"})
    _write(gov / "task_cards" / "HBJ-F78C-1" / "RETURN.md", _return_md("HBJ-F78C-1", "a" * 40, "b" * 40))

    def digest() -> str:
        files = sorted(p for p in (gov / "5_tasks").rglob("*") if p.is_file())
        return hashlib.md5(b"".join(p.read_bytes() + str(p).encode() for p in files)).hexdigest()

    before = digest()
    chk = validate_task_artifact("HBJ-F78C-1", gov)
    assert chk["ok"] is False and chk["category"] == "LANE_REPO_UNDECLARED" and chk["exit_code"] == INGEST_EXIT_REJECTED, chk
    reason = chk["reasons"][0]
    assert "不是 git 仓根" in reason and "lane.repo=未声明" in reason and "repos 清单未声明" in reason and "出口" in reason, reason
    assert digest() == before
    # 推导核同口径: 裁决/finalize 不派生, 出口同一份文案(非崩溃)
    d = derive_next_step("HBJ-F78C-1", gov)
    assert isinstance(d, dict) and "derivable" in d


def test_f78c_fixture_registered_in_runall_and_no_swallowed_exceptions():
    runall = (REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop" / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "tests/test_aipos_f78c_card_repo.py" in runall
    for rel in ("tools/aipos_cli/workspace_config.py", "tools/aipos_cli/artifact_ingest.py", "tools/aipos_cli/machine_zone.py",
                "tools/aipos_cli/card_render.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"except Exception:\s*\n\s*pass", src), rel
    for rel in ("tools/aipos_cli/workspace_config.py", "tools/aipos_cli/artifact_ingest.py", "tools/aipos_cli/next_resolver.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "Bearer" not in src and "load_owner_token" not in src, rel
