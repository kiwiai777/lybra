"""AIPOS-F88 — 族 A-1 工作树与根的识别定位单源三件(靶场 = F73D/F78/F78C/F86 既有靶场, 禁第二份)。

件① 卡工作树一套实现: 门认领(queue_mutation claim, 门 controlled-execute 同核)建树委托 next_resolver._ensure_worktree
    (落点 card_worktree_location, 分支 card_branch_name 读 N5 branch_pattern 声明); WorktreeManager 建树第二实现退役为委托;
    建树失败 = worktree_created=False + worktree_error 明确拒因, 不再吞成 warning。
    先红活证据(顾问 2026-10-02 真实门认领 F88 时 warning 原文, 认领结果仍 ok):
      Worktree creation failed: BLOCKED: WorktreeManager cannot operate on governance repo (/home/…/2_projects/lybra). …
件② 治理仓/项目根识别一个结构判据: workspace_config.has_workspace_queue(声明的队列根是目录; established=True 另要求
    project.json)为唯一实现, enroll_client / enroll_deliver / mcp 注册表 / home 扫描委托之; TS 镜像 loop-context.ts
    ConnectionResolver.isGovernanceWorkspace 同判据; 路径子串判定清零(含 ai-project-os 的产品仓不误判, 不含的治理仓照认)。
件③ 根路径语义分域(承接 F65B): workspace_config.governance_workspace_root / product_repo_root 两命名函数各一处实现;
    写死产品仓 / 治理根 / home 根全部改调二者或 resolve_home_root; bash 侧(lybra-deploy / governance-pre-commit)读产品 CLI
    `lybra workspace roots` 输出。F71 首打即炸场景(换机器: HOME 下无写死产品仓, 治理根不在 lybra 布局)先红后绿。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import EXEC, _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _card, _git, _init_product_repo  # noqa: E402
from test_aipos_f78c_card_repo import _dual_gov, _gov_skeleton, _single_gov  # noqa: E402
from test_aipos_f86_workstation_kickoff import _my_tasks, _task  # noqa: E402
from test_aipos_f87_fragmentation_ratchet import product_files, scan_d_path_substring  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.agent_profiles import load_agent_profiles  # noqa: E402
from tools.aipos_cli.queue_mutation import mutate_queue_task  # noqa: E402
from tools.aipos_cli.renderer import render_queue_mutation_text  # noqa: E402
from tools.aipos_cli.workspace_config import (  # noqa: E402
    CardRepoUnresolved,
    governance_workspace_root,
    has_workspace_queue,
    product_repo_root,
    resolve_home_root,
)
from tools.schema_loader import SchemaLoadError, code_repo_schema_root, get_branch_integration, load_schema  # noqa: E402

LOOP_CONTEXT_TS = REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop" / "loop-context.ts"
WRITE_GUARD_TS = REPO_ROOT / "tools" / "connector" / "pi" / "write-guard.ts"
LYBRA_DEPLOY = REPO_ROOT / "tools" / "lybra-deploy"
PRE_COMMIT_HOOK = REPO_ROOT / "tools" / "hooks" / "governance-pre-commit"
LANE_PREFIXES = (
    "tools/aipos_cli/", "tools/worktree_manager.py", "tools/mcp_server/", "tools/turn_advancer/", "tools/schema_loader.py",
    "tools/lybra-deploy", "tools/hooks/governance-pre-commit", "tools/inject_governance_status_headers.py",
    "tools/connector/pi/write-guard.ts", "agents/harness/pi/",
)
# 写死根路径(产品仓 / 治理根 / home 根 / lybra 目录布局)式样: 复查报告 H3/H4 grep 的路径形(项目名字面另族)
ROOT_LITERAL_RE = re.compile(
    r"ai-project-os/|/ai-project-os|2_projects/|[\"']2_projects[\"']|projects/lybra|[\"']projects[\"']\s*[,/]\s*[\"']lybra[\"']|/home/kiwi"
)


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _isolated_env(home: Path, **extra: str) -> dict[str, str]:
    """换机器形环境: HOME 指向空目录(无 ~/.lybra、无 HOME 下产品仓), 清掉一切根相关 env。"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("AIPOS_WORKSPACE_ROOT", "LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_WORKSPACE_ROOT",
                        "LYBRA_GOVERNANCE_ROOT", "LYBRA_CONNECTION_JSON", "LYBRA_SCHEMA_DIR")}
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.update(extra)
    return env


def _cli(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", *argv], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=120)


def _claim(gov: Path, task_id: str) -> dict:
    """门认领核(门 controlled-execute 与 file-CLI 同调 mutate_queue_task(dry_run=False))。"""
    return mutate_queue_task(gov, "claim", task_id=task_id, actor=EXEC, dry_run=False,
                             profiles=load_agent_profiles(gov), with_records=False)


def _preview_warnings(gov: Path, task_id: str) -> list[str]:
    preview = mutate_queue_task(gov, "claim", task_id=task_id, actor=EXEC, dry_run=True,
                                profiles=load_agent_profiles(gov), with_records=False)
    return list(preview["warnings"])


# ===========================================================================
# 件① 卡工作树一套实现
# ===========================================================================

def test_item1_gate_claim_single_repo_tree_equals_my_tasks_zero_warning(tmp_path, monkeypatch, capsys):
    """lybra 形单仓(project.json code_repo, 治理根无 .lybra/config.json; 路径同真实现场含 ai-project-os):
    门认领建树落点 = card_worktree_location = my-tasks.worktree_path; 认领 warnings 与预览逐条相同(建树零 warning)。"""
    base = tmp_path / "ai-project-os" / "2_projects"
    gov, repo = _single_gov(base, monkeypatch)
    assert not (gov / ".lybra" / "config.json").exists()
    task_id = "AIPOS-F88A"
    _card(gov, task_id, "pending")
    preview_warnings = _preview_warnings(gov, task_id)

    result = _claim(gov, task_id)
    _show("[件①·单仓] 门认领结果: " + json.dumps({k: result.get(k) for k in (
        "verdict", "worktree_created", "worktree_path", "worktree_branch", "worktree_error", "warnings")}, ensure_ascii=False))
    expected_repo, expected_tree = nr.card_worktree_location(gov, task_id)
    assert result["wrote"] and result["worktree_created"] is True and "worktree_error" not in result
    assert result["worktree_path"] == str(expected_tree) == str(repo / ".worktrees" / task_id) and expected_repo == repo
    assert result["worktree_branch"] == get_branch_integration()["branch_pattern"].replace("{task_id}", task_id)
    assert _git(Path(result["worktree_path"]), "rev-parse", "--abbrev-ref", "HEAD") == result["worktree_branch"]
    assert result["warnings"] == preview_warnings, "认领不得多出任何 warning(原 WorktreeManager 失败吞成 warning)"
    assert not [w for w in result["warnings"] if "orktree" in w]
    card_fm = nr._read_frontmatter(gov / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md")
    assert card_fm["active_worktree_path"] == result["worktree_path"] and card_fm["active_worktree_branch"] == result["worktree_branch"]

    view = _task(_my_tasks(gov, EXEC, capsys), task_id)
    _show(f"[件①·单仓] my-tasks.worktree_path={view['worktree_path']} exists={view['worktree_exists']}")
    assert view["worktree_path"] == result["worktree_path"] and view["worktree_exists"] is True
    assert "Worktree:" in render_queue_mutation_text(result)


def test_item1_gate_claim_dual_repo_lane_repo_lands_on_declared_repo(tmp_path, monkeypatch, capsys):
    """F78C 双仓: lane.repo=b 的 code 卡门认领 → repo-b/.worktrees/<ID>(不落默认仓 a、不落治理根) = my-tasks.worktree_path。"""
    gov, repos = _dual_gov(tmp_path, monkeypatch)
    task_id = "AIPOS-F88B"
    _card(gov, task_id, "pending", extra={"lane": {"repo": "b", "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}})
    result = _claim(gov, task_id)
    _show(f"[件①·双仓] worktree_path={result.get('worktree_path')} error={result.get('worktree_error')}")
    assert result["worktree_created"] is True
    assert result["worktree_path"] == str(repos["b"] / ".worktrees" / task_id) == str(nr.card_worktree_location(gov, task_id)[1])
    assert not (repos["a"] / ".worktrees" / task_id).exists() and not (gov / ".worktrees").exists()
    assert _task(_my_tasks(gov, EXEC, capsys), task_id)["worktree_path"] == result["worktree_path"]


def test_item1_gate_claim_tree_failure_is_explicit_refusal_not_warning(tmp_path, monkeypatch):
    """建树失败(声明的产品仓不是 git 仓)= worktree_created=False + worktree_error 拒因原文; warnings 不含建树文案; 文本出口单列 ✗。"""
    gov = _gov_skeleton(tmp_path / "gov-nogit", monkeypatch)
    not_git = tmp_path / "plain-dir"
    not_git.mkdir()
    _write(gov / "project.json", json.dumps({"project": "lybra", "code_repo": str(not_git), "config_version": 1}))
    task_id = "AIPOS-F88C"
    _card(gov, task_id, "pending")
    result = _claim(gov, task_id)
    _show(f"[件①·建树失败] worktree_error={result.get('worktree_error')}")
    assert result["wrote"] is True and result["worktree_created"] is False
    assert "不是 git 仓根" in result["worktree_error"] and str(not_git) in result["worktree_error"]
    assert not [w for w in result["warnings"] if "orktree" in w], result["warnings"]
    assert "✗ 卡工作树未建立(认领已落盘)" in render_queue_mutation_text(result)


def test_item1_worktree_manager_build_path_retired_to_delegation(tmp_path, monkeypatch):
    """WorktreeManager 建树入口全部委托 next_resolver 单源(落点/分支/建树同一实现); 源码不再自带 git worktree add /
    路径小写 / 分支写死 / 治理根 .lybra/config.json 直读; 门认领路径不再引用 WorktreeManager。"""
    from tools.worktree_manager import WorktreeManager

    gov, repo = _single_gov(tmp_path, monkeypatch)
    task_id = "AIPOS-F88D"
    _card(gov, task_id, "claimed")
    wm = WorktreeManager.from_workspace_config(gov)
    assert wm.code_repo == repo.resolve()
    assert wm.worktree_path_for_task(task_id) == nr.card_worktree_location(gov, task_id)[1].resolve()
    assert wm.branch_name_for_task(task_id) == nr.card_branch_name(task_id)
    path, branch = wm.create_worktree(task_id)
    assert path == (repo / ".worktrees" / task_id).resolve() and branch == f"card/{task_id}"
    with pytest.raises(ValueError):
        wm.create_worktree(task_id, base_branch="dev")
    src = (REPO_ROOT / "tools" / "worktree_manager.py").read_text(encoding="utf-8")
    assert "'worktree', 'add'" not in src and ".lower()" not in src and 'f"card/{' not in src and "'config.json'" not in src
    assert "ai-project-os" not in src
    qm = (REPO_ROOT / "tools" / "aipos_cli" / "queue_mutation.py").read_text(encoding="utf-8")
    assert "worktree_manager import" not in qm and "WorktreeManager(" not in qm and "WorktreeManager.from" not in qm.replace("原 WorktreeManager.from", "")
    assert "_ensure_worktree(repo_root, task_id_val, card_frontmatter=" in qm


# ===========================================================================
# 件② 治理仓/项目根识别 = 一个结构判据
# ===========================================================================

def _structure_fixtures(tmp_path: Path) -> dict[str, tuple[Path, bool, bool]]:
    """{名: (路径, 治理工作区?, 已建项目?)}——判据只看结构, 与路径名无关。"""
    gov_anywhere = tmp_path / "anywhere" / "gov"            # 换机器形: 路径不含 ai-project-os
    (gov_anywhere / "5_tasks" / "queue").mkdir(parents=True)
    _write(gov_anywhere / "project.json", json.dumps({"project": "p"}))
    product_aipos = tmp_path / "ai-project-os" / "2_projects" / "product"   # 路径含 ai-project-os 的产品仓
    product_aipos.mkdir(parents=True)
    _init_product_repo(product_aipos)
    custom = tmp_path / "custom-queue"                       # project.json 声明自有队列根
    (custom / "work" / "q").mkdir(parents=True)
    _write(custom / "project.json", json.dumps({"project": "c", "paths": {"queue_root": "work/q"}}))
    bare = tmp_path / "bare"                                 # 只有队列(legacy 裸子树), 无 project.json
    (bare / "5_tasks" / "queue").mkdir(parents=True)
    queue_file = tmp_path / "queue-is-file"                  # 队列根是文件 ≠ 目录
    (queue_file / "5_tasks").mkdir(parents=True)
    (queue_file / "5_tasks" / "queue").write_text("x", encoding="utf-8")
    return {
        "gov_anywhere": (gov_anywhere, True, True),
        "product_under_ai_project_os": (product_aipos, False, False),
        "custom_queue_root": (custom, True, True),
        "bare_queue": (bare, True, False),
        "queue_is_file": (queue_file, False, False),
    }


def test_item2_structural_predicate_ignores_path_names(tmp_path):
    from tools.aipos_cli.enroll_client import is_governance_workspace
    from tools.aipos_cli.enroll_deliver import validate_workspace_root

    for name, (path, is_gov, established) in _structure_fixtures(tmp_path).items():
        got = (has_workspace_queue(path), has_workspace_queue(path, established=True), is_governance_workspace(path))
        _show(f"[件②] {name}: path={path} has_workspace_queue={got[0]} established={got[1]} is_governance_workspace={got[2]}")
        assert got == (is_gov, established, is_gov), name
        if is_gov:
            with pytest.raises(ValueError):
                validate_workspace_root(str(path), "executor")
        else:
            validate_workspace_root(str(path), "executor")  # 路径含 ai-project-os 的产品仓放行
        validate_workspace_root(str(path), "advisor")


def test_item2_ts_mirror_same_verdicts_as_python(tmp_path):
    """TS 镜像(loop-context.ts isGovernanceWorkspace, 队列根缺省经 declaredQueueRootDefault 读 config.schema)与 Python 唯一实现逐例同判。"""
    node = shutil.which("node")
    assert node, "node 不在 PATH(TS 夹具需 Node ≥ 22)"
    fixtures = _structure_fixtures(tmp_path)
    paths = {name: str(path) for name, (path, _g, _e) in fixtures.items()}
    script = (
        f"import {{ ConnectionResolver }} from {json.dumps(LOOP_CONTEXT_TS.as_uri())};\n"
        "import { readFileSync } from 'node:fs';\n"
        f"const schema = JSON.parse(readFileSync({json.dumps(str(REPO_ROOT / 'schema' / 'config.schema.json'))}, 'utf-8'));\n"
        "const dflt = ConnectionResolver.declaredQueueRootDefault(schema);\n"
        f"const paths = {json.dumps(paths)};\n"
        "const out = {}; for (const [k, p] of Object.entries(paths)) out[k] = ConnectionResolver.isGovernanceWorkspace(p, dflt);\n"
        "let threw = false; try { ConnectionResolver.resolveCodeRepo({ explicitRoot: paths.gov_anywhere, queueRootDefault: dflt }); } catch { threw = true; }\n"
        "out.__code_repo_rejects_gov = threw;\n"
        "out.__code_repo_accepts_product = ConnectionResolver.resolveCodeRepo({ explicitRoot: paths.product_under_ai_project_os, queueRootDefault: dflt });\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    ts = json.loads(proc.stdout)
    _show(f"[件②·TS] {json.dumps(ts, ensure_ascii=False)}")
    for name, (path, is_gov, _e) in fixtures.items():
        assert ts[name] is is_gov is has_workspace_queue(path), name
    assert ts["__code_repo_rejects_gov"] is True and ts["__code_repo_accepts_product"] == paths["product_under_ai_project_os"]


def test_item2_path_substring_predicates_cleared_and_m22_delegated():
    """d 类路径子串判定全仓清零(棘轮 d 基线随之清空); M22 识别逐一委托唯一判据。"""
    hits = scan_d_path_substring(product_files())
    assert hits == [], hits
    baseline = json.loads((REPO_ROOT / "tests" / "f87_fragmentation_baseline.json").read_text(encoding="utf-8"))
    assert baseline["invariants"]["d"]["entries"] == [] and baseline["invariants"]["d"]["count"] == 0
    wc = (REPO_ROOT / "tools" / "aipos_cli" / "workspace_config.py").read_text(encoding="utf-8")
    assert wc.count('(child / "project.json").exists()') == 0 and wc.count("has_workspace_queue(root, established=True)") == 1
    tools_src = (REPO_ROOT / "tools" / "mcp_server" / "tools.py").read_text(encoding="utf-8")
    assert "has_workspace_queue(child, established=True)" in tools_src
    enroll_src = (REPO_ROOT / "tools" / "aipos_cli" / "enroll_client.py").read_text(encoding="utf-8")
    assert '(target / "5_tasks" / "queue").is_dir()' not in enroll_src and "return has_workspace_queue(target)" in enroll_src
    loader = (REPO_ROOT / "tools" / "aipos_cli" / "task_loader.py").read_text(encoding="utf-8")
    assert "return has_workspace_queue(path)" in loader


# ===========================================================================
# 件③ 根路径语义分域
# ===========================================================================

def _home_with_global_config(tmp_path: Path, project: str = "proj") -> tuple[Path, Path]:
    """全局 ~/.lybra/config.json 声明 home_root + active_project(治理根不在 lybra 目录布局下)。"""
    home = tmp_path / "home"
    projects = tmp_path / "data" / "projects"
    gov = projects / project
    (gov / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
    _write(gov / "project.json", json.dumps({"project": project, "code_repo": str(REPO_ROOT), "config_version": 1}))
    _write(home / ".lybra" / "config.json", json.dumps({"config_version": 2, "home_root": str(projects), "active_project": project}))
    return home, gov


def test_item3_governance_workspace_root_ladder_and_no_layout_fallback(tmp_path, monkeypatch):
    for key in ("AIPOS_WORKSPACE_ROOT", "LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_WORKSPACE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    gov = tmp_path / "anywhere" / "gov"
    (gov / "5_tasks" / "queue").mkdir(parents=True)
    _write(gov / "project.json", json.dumps({"project": "p"}))
    # 1 显式(验证结构)
    assert governance_workspace_root(gov) == gov.resolve()
    with pytest.raises(FileNotFoundError):
        governance_workspace_root(tmp_path)
    # 2 工位 connection.json 声明(governance_root 优先于 workspace_root)
    station = tmp_path / "station" / "sub"
    station.mkdir(parents=True)
    _write(tmp_path / "station" / ".lybra" / "connection.json",
           json.dumps({"governance_root": str(gov), "workspace_root": str(tmp_path)}))
    assert governance_workspace_root(start=station) == gov.resolve()
    _write(tmp_path / "bad-station" / ".lybra" / "connection.json", json.dumps({"governance_root": str(tmp_path / "nope")}))
    with pytest.raises(FileNotFoundError, match="connection.json 声明的治理根"):
        governance_workspace_root(start=tmp_path / "bad-station")
    # 3 结构识别(向上队列结构)
    assert governance_workspace_root(start=gov / "5_tasks" / "queue") == gov.resolve()
    # 禁按 lybra 布局回退: HOME 下恰好摆着旧回落布局, 但无任何声明/结构 → 拒, 不猜
    home = tmp_path / "home"
    (home / "ai-project-os" / "2_projects" / "lybra" / "5_tasks" / "queue").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(FileNotFoundError, match="治理工作区根不可解析"):
        governance_workspace_root(start=outside, env={"HOME": str(home)})


def test_item3_product_repo_root_two_sources_and_home_root_single_default(tmp_path, monkeypatch):
    assert product_repo_root() == code_repo_schema_root() == REPO_ROOT
    gov, repo = _single_gov(tmp_path, monkeypatch)
    assert product_repo_root(gov) == repo
    dual, repos = _dual_gov(tmp_path, monkeypatch)
    assert product_repo_root(dual, {"task_id": "X", "lane": {"repo": "b"}}) == repos["b"]
    bare = _gov_skeleton(tmp_path / "gov-undeclared", monkeypatch)
    _write(bare / "project.json", json.dumps({"project": "u", "config_version": 1}))
    with pytest.raises(CardRepoUnresolved):
        product_repo_root(bare, allow_governance_root=False)
    # home 根缺省唯一 = resolve_home_root(http_sse 不再自带缺省)
    assert resolve_home_root(env={"HOME": str(tmp_path / "h")}) == tmp_path / "h" / ".lybra" / "projects"
    http_sse = (REPO_ROOT / "tools" / "mcp_server" / "http_sse.py").read_text(encoding="utf-8")
    assert "home_root = resolve_home_root()" in http_sse and "'2_projects'" not in http_sse


def _code_literal_hits(rel: str) -> list[str]:
    """一文件内「可执行代码」里的写死根路径字面(注释 / 文档串不算解析点)。"""
    text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
    hits: list[str] = []
    if rel.endswith(".py"):
        doc_lines: set[int] = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                doc_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING and tok.start[0] not in doc_lines and ROOT_LITERAL_RE.search(tok.string):
                hits.append(f"{rel}:{tok.start[0]}: {tok.string[:120]}")
        return hits
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", "/*")):
            continue
        code = re.split(r"\s(?://|#)\s", line)[0]  # 行尾注释(前有空白)不算代码
        if ROOT_LITERAL_RE.search(code):
            hits.append(f"{rel}:{lineno}: {stripped[:120]}")
    return hits


def test_item3_no_hardcoded_root_literals_in_lane_code():
    """验收③: 车道内产品代码(可执行部分)写死根路径清零——仅两命名函数内部与声明缺省可定根(本式样零命中)。"""
    lane_files = [f for f in product_files() if f.startswith(LANE_PREFIXES)]
    assert len(lane_files) > 50
    hits = [h for rel in lane_files for h in _code_literal_hits(rel)]
    _show(f"[件③] 车道内文件 {len(lane_files)} 个, 可执行写死根路径命中 {len(hits)}")
    assert hits == [], "\n".join(hits)


def test_item3_f71_first_hit_scenario_change_machine_any_cwd(tmp_path, monkeypatch):
    """F71 首打即炸(治理仓根找 schema / 写死产品仓找 schema)换机器复现: HOME 无写死产品仓、无 ~/.lybra, 治理根不在 lybra 布局。
    next 在任意 cwd 正常推导; governance list-declarations / governance-commit 的 schema 根 = 运行代码所在仓(main 上后者首打即炸);
    把治理根当 schema 根 = SchemaLoadError(双义的病理, product_repo_root 不再给出此值)。"""
    home = tmp_path / "home"
    home.mkdir()
    gov = _gov_skeleton(tmp_path / "work" / "gov", monkeypatch)
    _write(gov / "project.json", json.dumps({"project": "lybra", "code_repo": str(REPO_ROOT), "config_version": 1}))
    task_id = "AIPOS-F88F"
    _card(gov, task_id, "claimed")
    env = _isolated_env(home)
    with pytest.raises(SchemaLoadError):
        load_schema("config", gov)  # 病理复现: 治理根当 schema 根
    assert product_repo_root() / "schema" == REPO_ROOT / "schema"
    for cwd in (Path("/"), gov, gov / "5_tasks" / "queue" / "claimed", home, REPO_ROOT, tmp_path):
        proc = _cli(["next", "--task-id", task_id, "--workspace-root", str(gov), "--json"], cwd=cwd, env=env)
        assert proc.returncode in (0, 1), (cwd, proc.stderr[-800:])  # 1 = 正常推导结论「不可推导」(claimed 无产物), 非崩溃
        data = json.loads(proc.stdout)
        _show(f"[件③·F71] cwd={cwd} → current_state={data.get('current_state')} node={data.get('current_node')} derivable={data.get('derivable')}")
        assert data["task_id"] == task_id and data["current_state"] == "claimed"
        assert "SchemaLoadError" not in proc.stderr and "Schema file not found" not in proc.stderr
    decl = _cli(["governance", "list-declarations", "--json"], cwd=gov, env=env)
    assert decl.returncode == 0 and json.loads(decl.stdout)["declarations"], decl.stderr
    commit = _cli(["governance-commit", "--actor", "advisor.lybra.test", "--governance-root", str(gov), "--dry-run", "--json"], cwd=gov, env=env)
    _show(f"[件③·F71] governance-commit --dry-run rc={commit.returncode} stderr_tail={commit.stderr.strip()[-200:]!r}")
    assert "Cannot locate product repo for schema resolution" not in commit.stderr


def test_item3_workspace_roots_cli_from_any_cwd(tmp_path):
    home, gov = _home_with_global_config(tmp_path)
    env = _isolated_env(home)
    for cwd in (Path("/"), tmp_path, gov / "5_tasks"):
        proc = _cli(["workspace", "roots", "--json"], cwd=cwd, env=env)
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        _show(f"[件③·roots] cwd={cwd} → {json.dumps(data, ensure_ascii=False)}")
        assert data["governance_root"] == str(gov.resolve()) and data["product_repo"] == str(REPO_ROOT)
        assert data["code_repo"] == str(REPO_ROOT) and data["schema_dir"] == str(REPO_ROOT / "schema")
        assert data["home_root"] == str((tmp_path / "data" / "projects").resolve()) and data["errors"] == {}
    bare_home = tmp_path / "bare-home"
    bare_home.mkdir()
    proc = _cli(["workspace", "roots", "--field", "governance_root"], cwd=Path("/"), env=_isolated_env(bare_home))
    assert proc.returncode == 1 and "治理工作区根不可解析" in proc.stderr and proc.stdout == ""
    ok = _cli(["workspace", "roots", "--field", "schema_dir"], cwd=Path("/"), env=_isolated_env(bare_home))
    assert ok.returncode == 0 and ok.stdout.strip() == str(REPO_ROOT / "schema")


def test_item3_lybra_deploy_roots_range_never_deploys(tmp_path):
    """lybra-deploy 靶场(只跑只读 roots 子命令, 禁部署): 产品仓 = 脚本所在 git 仓主检出(克隆靶场仓), 治理根读 `lybra workspace
    roots`, connection.json 随治理根; 无声明 = 拒(带出口)。"""
    sha = _git(REPO_ROOT, "rev-parse", "HEAD")
    clone = tmp_path / "range-product"
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(REPO_ROOT), str(clone)], check=True, capture_output=True)
    _git(clone, "checkout", "-q", "--detach", sha)
    script = clone / "tools" / "lybra-deploy"
    src = script.read_text(encoding="utf-8")
    assert "/home/" not in src and "2_projects" not in src, "靶场仓须为本卡提交后的脚本(先提交再跑夹具)"
    home, gov = _home_with_global_config(tmp_path)
    env = _isolated_env(home)
    env.pop("PYTHONPATH")
    proc = subprocess.run(["bash", str(script), "roots"], cwd="/", env=env, capture_output=True, text=True, timeout=120)
    _show(f"[件③·deploy 靶场] rc={proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode == 0, proc.stderr
    assert f"product_repo:     {clone}" in proc.stdout
    assert f"governance_root:  {gov.resolve()}" in proc.stdout
    assert f"connection_json:  {gov.resolve()}/.lybra/connection.json" in proc.stdout
    explicit = subprocess.run(["bash", str(script), "roots", "--governance-root", str(tmp_path / "x")], cwd="/", env=env,
                              capture_output=True, text=True, timeout=120)
    assert explicit.returncode == 0 and f"governance_root:  {tmp_path / 'x'}" in explicit.stdout
    bare_home = tmp_path / "bare-home"
    bare_home.mkdir()
    env2 = _isolated_env(bare_home)
    env2.pop("PYTHONPATH")
    denied = subprocess.run(["bash", str(script), "roots"], cwd="/", env=env2, capture_output=True, text=True, timeout=120)
    assert denied.returncode == 1 and "Cannot resolve governance root" in denied.stderr
    assert not (clone / ".deploy").exists(), "roots 只读: 不得生成发布目录"


def test_item3_governance_pre_commit_reads_product_cli_for_schema(tmp_path):
    """治理仓 pre-commit 钩子: LYBRA_SCHEMA_DIR 未设时读 `lybra workspace roots --field schema_dir`; PATH 无 lybra = 拒绝放行(带出口)。"""
    gov_repo = tmp_path / "gov-repo"
    gov_repo.mkdir()
    _git(gov_repo, "init", "-q", "-b", "main")
    _git(gov_repo, "commit", "-q", "--allow-empty", "-m", "init")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "lybra"
    shim.write_text(f"#!/usr/bin/env bash\nPYTHONPATH={REPO_ROOT} exec {sys.executable} -m tools.aipos_cli.aipos_cli \"$@\"\n", encoding="utf-8")
    shim.chmod(0o755)
    base_path = "/usr/bin:/bin"
    env = _isolated_env(tmp_path / "home", PATH=f"{bindir}:{base_path}")
    ok = subprocess.run(["bash", str(PRE_COMMIT_HOOK)], cwd=str(gov_repo), env=env, capture_output=True, text=True, timeout=120)
    _show(f"[件③·pre-commit] 有 lybra CLI: rc={ok.returncode} out={(ok.stdout + ok.stderr).strip()[-300:]!r}")
    assert "无法定位 schema 目录" not in ok.stdout and "config.schema not found" not in ok.stdout
    assert ok.returncode == 0, ok.stdout + ok.stderr
    env_no = _isolated_env(tmp_path / "home", PATH=base_path)
    denied = subprocess.run(["bash", str(PRE_COMMIT_HOOK)], cwd=str(gov_repo), env=env_no, capture_output=True, text=True, timeout=120)
    assert denied.returncode == 1 and "无法定位 schema 目录" in denied.stdout


def test_item3_write_guard_root_from_declaration_or_fail_closed(tmp_path):
    """write-guard.ts: 治理根走 TS 单源 resolveGateWorkspace; 无 env 无工位声明 = 写一律阻断并给出口(不再回落写死根)。"""
    node = shutil.which("node")
    assert node, "node 不在 PATH"
    connector = tmp_path / "connector"
    (connector / "bindings").mkdir(parents=True)
    (connector / "bindings" / "ephemeral.json").write_text(json.dumps({"taskId": "AIPOS-F88W"}), encoding="utf-8")
    script = (
        f"const mod = await import({json.dumps(WRITE_GUARD_TS.as_uri())});\n"
        "const handlers = {}; const pi = { on: (ev, fn) => { handlers[ev] = fn; }, registerCommand: () => {} };\n"
        "mod.default(pi);\n"
        "const r = await handlers.tool_call({ toolName: 'write', input: { path: 'x.txt' } }, {});\n"
        "process.stdout.write(JSON.stringify(r ?? null));\n"
    )

    def run(extra: dict[str, str], cwd: Path) -> dict:
        env = _isolated_env(tmp_path / "home", PI_CONNECTOR_DIR=str(connector), **extra)
        proc = subprocess.run([node, "--input-type=module", "-e", script], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    outside = tmp_path / "outside"
    outside.mkdir()
    unresolved = run({}, outside)
    _show(f"[件③·write-guard] 无声明: {json.dumps(unresolved, ensure_ascii=False)}")
    assert unresolved["block"] is True and "治理工作区根不可解析" in unresolved["reason"]
    gov = tmp_path / "gov"
    (gov / "5_tasks" / "queue").mkdir(parents=True)
    declared = run({"LYBRA_WORKSPACE_ROOT": str(gov)}, outside)
    assert declared["block"] is True and "治理工作区根不可解析" not in declared["reason"] and "AIPOS-F88W" in declared["reason"]
