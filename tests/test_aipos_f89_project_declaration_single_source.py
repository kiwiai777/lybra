"""AIPOS-F89 件① — 落点与项目键只有一份声明(H9 / M8 / M14)。靶场复用 F73D/F78/F78C/F86/F88 既有构件(禁第二份靶场构造逻辑)。

H9  交回/裁决/台账落点只读 project.json paths(workspace_config.project_paths 唯一读取口): config.schema
    governance_structure.paths.task_cards(第二份声明)删除; 门侧写死(board_adapter 交回材料校验 / return_body 落盘、
    tools.py lybra_return_content、finalize 报告展示与 CARD.md、governance_commit 台账检查、machine_zone 纪律段报告落点、
    transitions N2.artifact.location / N4.audit_report.location_candidates 与 loop 两个 *_artifact_patterns)全部改走声明推导。
M8  队列根只读 task_loader.queue_root_for(一处): config.schema governance_structure.paths.queue 与 queue_mutation.QUEUE_ROOT /
    draft_validator.QUEUE_DIR 删除; 路径构造处逐类改调(处置表见 RETURN)。
M14 project.json 被读取的键全部在 config.schema configuration_sources.project_json.schema 声明; collaboration_profile 双读取口
    双缺省收一(workspace_config.get_collaboration_profile, 缺省读声明 default)。

验收①: 非默认 paths 的靶场项目(probe 形: 队列 / 交回 / 裁决 / 台账四个根都不是缺省)认领→建树→骨架→交回→入门判据→
等待落点→台账检查全程落在声明位; 门侧代码零写死(静态扫描)。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import DRIVER, EXEC, _fm, _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _git, _init_product_repo, _return_text  # noqa: E402
from test_aipos_f78c_card_repo import _executor_commits_in_worktree  # noqa: E402
from test_aipos_f86_workstation_kickoff import _my_tasks, _task  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.agent_profiles import load_agent_profiles  # noqa: E402
from tools.aipos_cli.queue_mutation import mutate_queue_task  # noqa: E402
from tools.aipos_cli.task_loader import queue_root_for, queue_state_ref  # noqa: E402
from tools.aipos_cli.workspace_config import project_paths  # noqa: E402
from tools.schema_loader import load_schema  # noqa: E402

PROBE_PATHS = {
    "queue_root": "work/queue",
    "return_root": "out/returns",
    "verdict_root": "out/verdicts",
    "task_cards_root": "out/ledger",
}
LANE_CODE = ("tools/aipos_cli", "tools/mcp_server", "web/board")
LANE_FILES = ("tools/schema_loader.py", "tools/worktree_manager.py")


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _probe_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, project: str = "lybra") -> tuple[Path, Path]:
    """probe 形治理根: project.json paths 四个根全部非缺省; 目录一律按声明(queue_root_for / project_paths)建, 测试侧零写死。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    gov = tmp_path / "gov-probe"
    repo = _init_product_repo(tmp_path / "repo-probe")
    _write(gov / "project.json", json.dumps({"project": project, "code_repo": str(repo), "config_version": 1, "paths": PROBE_PATHS}))
    for state in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (queue_root_for(gov) / state).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events", "sessions"):
        (gov / "5_tasks" / "records" / sub).mkdir(parents=True)
    _write(gov / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    return gov, repo


def _probe_card(gov: Path, task_id: str, state: str, *, extra: dict | None = None) -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} probe", "project": "lybra", "task_mode": "code", "harness": "pi",
            "assigned_to": EXEC, "agent_instance": EXEC, "status": state, "audit": "required", "audit_by": "audit.lybra.test",
            "output_target": "tools/aipos_cli/, tests/", "priority": "high", "created_by": DRIVER, "needs_owner": False,
            "context_bundle": "t", "artifact_policy": "formal_write",
            "lane": {"paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}}
    meta.update(extra or {})
    path = queue_root_for(gov) / state / f"{task_id.lower()}.md"
    _write(path, _fm(meta, f"# {task_id}\n\n## Goal\n做完。\n\n## 验收\n- 夹具绿\n"))
    return path


# ===========================================================================
# 验收①: probe 形项目全程落在声明位
# ===========================================================================

def test_item1_probe_shape_claim_return_verdict_ledger_queue_all_on_declared_roots(tmp_path, monkeypatch, capsys):
    gov, repo = _probe_gov(tmp_path, monkeypatch)
    task_id = "AIPOS-F89P"
    _probe_card(gov, task_id, "pending")

    # 队列根: 唯一读取口
    assert queue_root_for(gov) == gov / "work" / "queue"
    assert queue_state_ref(gov, "pending") == "work/queue/pending/"
    from tools.aipos_cli.machine_zone import derive_machine_zone_fields

    assert derive_machine_zone_fields({"task_id": task_id, "created_by": DRIVER}, REPO_ROOT,
                                     governance_root=gov)["draft_publish_target"] == "work/queue/pending/"
    from tools.aipos_cli.draft_validator import expected_pending_relative_path

    assert expected_pending_relative_path(task_id, gov) == f"work/queue/pending/{task_id.lower()}.md"

    # 门认领核(真 mutate_queue_task, 带记录): 卡迁到声明队列 claimed/ + 建树 + 骨架落声明 return_root
    result = mutate_queue_task(gov, "claim", task_id=task_id, actor=EXEC, dry_run=False,
                               profiles=load_agent_profiles(gov), with_records=True)
    _show("[①·认领] " + json.dumps({k: result.get(k) for k in ("verdict", "target_path", "worktree_path", "skeleton_path",
                                                                   "blocking_reasons")}, ensure_ascii=False))
    assert result["wrote"] and result["target_path"] == f"work/queue/claimed/{task_id.lower()}.md"
    assert result["skeleton_path"] == f"out/returns/{task_id}/RETURN.md"
    assert (gov / "out" / "returns" / task_id / "RETURN.md").is_file()
    assert not (gov / "task_cards").exists() and not (gov / "5_tasks" / "queue").exists()

    # 开工面: my-tasks 报告落点 / 卡路径都在声明位
    view = _task(_my_tasks(gov, EXEC, capsys), task_id)
    _show(f"[①·my-tasks] card_path={view['card_path']} report_path={view['report_path']}")
    assert view["report_path"] == str(gov / "out" / "returns" / task_id / "RETURN.md")
    assert view["card_path"] == str((gov / "work" / "queue" / "claimed" / f"{task_id.lower()}.md").resolve())

    # 交回: 执行体在工作树提交 + 写 Return(声明位默认文件) → 入门判据按声明找到
    worktree = Path(result["worktree_path"])
    sha, tree = _executor_commits_in_worktree(worktree, task_id)
    default_file = nr.default_return_file(gov, task_id)
    assert default_file == gov / "out" / "returns" / task_id / "RETURN.md"
    _write(default_file, _return_text(task_id, sha, tree))
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    check = validate_task_artifact(task_id, gov)
    _show(f"[①·ingest 判据] {check.get('category')} path={check.get('path')}")
    assert check["ok"] and check["path"] == str(default_file), check

    # 门侧交回材料校验 / return_body 落盘读同一声明; 写死旧位被拒
    from tools.aipos_cli.board_adapter import _return_body_rel, _validate_return_artifact_refs

    assert _return_body_rel(gov, task_id) == f"out/returns/{task_id}/RETURN.md"
    assert _validate_return_artifact_refs([f"out/returns/{task_id}/RETURN.md"], None, task_id, gov) == []
    wrong = _validate_return_artifact_refs([f"task_cards/{task_id}/RETURN.md"], None, task_id, gov)
    _show(f"[①·旧位被拒] {wrong}")
    assert wrong and wrong[0].startswith("RETURN_ARTIFACT_WRONG_LOCATION") and f"out/returns/{task_id}/" in wrong[0]

    # 裁决落点: loop 等待审计报告读 verdict_root(原 N4 location_candidates 写死 task_cards 已删)
    watch_root, patterns = nr.auditor_artifact_watch(gov, f"{task_id}R")
    _show(f"[①·裁决等待] root={watch_root} patterns={patterns}")
    assert watch_root == gov and patterns and all(p.startswith(f"out/verdicts/{task_id}R/") for p in patterns)
    assert nr.verdict_artifact_dir(gov, f"{task_id}R") == gov / "out" / "verdicts" / f"{task_id}R"
    _, exec_patterns = nr.executor_artifact_watch(gov, task_id)
    assert all(p.startswith(f"out/returns/{task_id}/") for p in exec_patterns)

    # 台账: governance_commit 收账清单 / finalize 摘要读 task_cards_root
    from tools.aipos_cli.governance_commit import check_governance_completeness

    ledger = check_governance_completeness(gov, task_id)
    _show(f"[①·台账] missing={ledger['missing']}")
    assert any(m.startswith(f"out/ledger/{task_id}/") for m in ledger["missing"]), ledger["missing"]
    _write(gov / "out" / "ledger" / task_id / "RETURN.md", "# archived\n")
    ledger = check_governance_completeness(gov, task_id)
    assert ledger["details"]["task_cards"]["path"] == str(gov / "out" / "ledger" / task_id)

    # 纪律段报告落点 / state lint 队列扫描 / watch 子树 / 看板只读路由白名单: 同一声明
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
    from tools.aipos_cli.state_lint import _list_all_task_ids
    from tools.aipos_cli.agent_watch_fs import snapshot
    from web.board.md_source import allowed_roots

    section = derive_machine_zone_纪律段(task_id, {"task_mode": "code"}, gov)
    assert f"out/returns/{task_id}/RETURN.md" in section and "task_cards" not in section
    assert task_id in _list_all_task_ids(gov)
    assert any(k.startswith("work/queue/claimed/") for k in snapshot(gov))
    assert allowed_roots(gov) == ("work/queue", "5_tasks/records")


def test_item1_audit_report_display_reads_verdict_root(tmp_path, monkeypatch):
    """finalize 报告展示(只展示不判)读声明 verdict_root(治理根内), 原写死 <产品仓>/task_cards/<ID>/AUDIT-REPORT-*.md 退役。"""
    from tools.aipos_cli.finalize import _report_frontmatter_verdict_for_display

    gov, _repo = _probe_gov(tmp_path, monkeypatch)
    report = gov / "out" / "verdicts" / "AIPOS-F89DR" / "RETURN.md"
    _write(report, _fm({"verdict": "PASS", "commit_sha": "a" * 40}, "# audit\n"))
    shown = _report_frontmatter_verdict_for_display(gov, "AIPOS-F89D")
    assert shown == {"report_path": str(report), "report_verdict": "PASS"}


# ===========================================================================
# 第二份声明已删 + 门侧零写死(静态)
# ===========================================================================

def test_item1_second_declarations_removed_from_schemas():
    config = load_schema("config")
    gs_paths = config["governance_structure"]["paths"]
    assert "queue" not in gs_paths and "task_cards" not in gs_paths, sorted(gs_paths)
    transitions = load_schema("transitions")
    assert "location" not in transitions["nodes"]["N2"]["artifact"]
    assert "location_candidates" not in transitions["nodes"]["N4"]["audit_report"]
    raw = (REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8")
    assert "5_tasks/queue" not in raw and "task_cards/{" not in raw
    from tools.aipos_cli import draft_validator, loop_driver, queue_mutation

    for mod, attr in ((queue_mutation, "QUEUE_ROOT"), (queue_mutation, "QUEUE_STATE_DIRS"), (draft_validator, "QUEUE_DIR"),
                      (draft_validator, "PENDING_QUEUE_DIR"), (loop_driver, "executor_artifact_patterns"),
                      (loop_driver, "auditor_artifact_patterns")):
        assert not hasattr(mod, attr), f"{mod.__name__}.{attr} 仍在(第二份声明/写死)"


HARDCODE_RE = re.compile(
    r'"5_tasks"\s*/\s*"queue"'                      # 路径构造: root / "5_tasks" / "queue"
    r'|Path\(\s*"5_tasks/queue'                     # 常量: Path("5_tasks/queue")
    r'|f"5_tasks/queue/'                            # f-string 拼接
    r'|==\s*"5_tasks/queue/pending/"'               # 比较写死
    r'|setdefault\("draft_publish_target",\s*"'     # 发布落点写死
    r'|"draft_publish_target":\s*"5_tasks'          # 发布落点写死
    r'|/\s*"task_cards"'                            # 路径构造: root / "task_cards"
    r'|f"task_cards/\{'                             # f-string 拼接 task_cards/{ID}
    r'|resolve_governance_path\(\s*"task_cards"'    # 第二份声明读取
    r'|get_governance_path\(\s*"queue"'             # 第二份声明读取
    r'|_with_relative\(\s*"queue"'                  # 第二份声明读取
)


def _lane_code_files() -> list[Path]:
    files: list[Path] = []
    for top in LANE_CODE:
        files.extend(p for p in (REPO_ROOT / top).rglob("*.py") if "tests" not in p.relative_to(REPO_ROOT).parts)
    files.extend(REPO_ROOT / f for f in LANE_FILES)
    return sorted(files)


def test_item1_gate_code_zero_hardcoded_landing_or_queue_root():
    hits: list[str] = []
    for path in _lane_code_files():
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if HARDCODE_RE.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{no}: {line.strip()}")
    _show(f"[①·静态] 扫描 {len(_lane_code_files())} 个车道代码文件, 写死落点/队列根命中: {hits or 0}")
    assert hits == []


def test_item1_queue_root_reader_defaults_and_declared(tmp_path, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    default = load_schema("config")["configuration_sources"]["project_json"]["schema"]["paths"]["schema"]["queue_root"]["default"]
    assert queue_root_for(bare) == bare / default  # 无 project.json = 声明 default(不另写一份)
    gov, _ = _probe_gov(tmp_path, monkeypatch)
    assert queue_root_for(gov) == Path(project_paths(gov)["queue_root"]) == gov / "work" / "queue"


# ===========================================================================
# M14: project.json 键全部声明; collaboration_profile 单读取口单缺省
# ===========================================================================

READ_KEY_RE = re.compile(
    r'(?:project_json|project_data|read_project_json\([^()]*\))\.get\(\s*"([A-Za-z_]+)"'
    r'|"([A-Za-z_]+)"\s+in\s+project_json\b'
)


def test_item1_m14_every_read_project_json_key_is_declared():
    declared = set(load_schema("config")["configuration_sources"]["project_json"]["schema"])
    read: dict[str, list[str]] = {}
    for path in _lane_code_files() + [REPO_ROOT / "tools" / "project_resolution.py"]:
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in READ_KEY_RE.finditer(line):
                key = m.group(1) or m.group(2)
                read.setdefault(key, []).append(f"{path.relative_to(REPO_ROOT)}:{no}")
    # workspace_config.project_paths 内以局部名 project 读 paths 与顶层 manual_gate_mode
    src = (REPO_ROOT / "tools" / "aipos_cli" / "workspace_config.py").read_text(encoding="utf-8")
    for key in re.findall(r'project\.get\(\s*"([A-Za-z_]+)"', src):
        read.setdefault(key, []).append("tools/aipos_cli/workspace_config.py(project_paths)")
    _show("[①·M14] 产品读取的 project.json 键: " + json.dumps({k: len(v) for k, v in sorted(read.items())}, ensure_ascii=False))
    _show(f"[①·M14] 声明键: {sorted(declared)}")
    undeclared = {k: v for k, v in read.items() if k not in declared}
    assert not undeclared, undeclared
    for key in ("naming_profile", "collaboration_profile", "dispatch_mode", "manual_gate_mode", "custom_roles", "card_policy"):
        assert key in declared, key


def test_item1_m14_collaboration_profile_single_reader_single_default(tmp_path):
    from tools.aipos_cli import flow_description
    from tools.aipos_cli.workspace_config import default_collaboration_profile, get_collaboration_profile

    decl = load_schema("config")["configuration_sources"]["project_json"]["schema"]["collaboration_profile"]["default"]
    assert default_collaboration_profile() == decl
    root = tmp_path / "p"
    _write(root / "project.json", json.dumps({"project": "p", "collaboration_profile": {"deploy_gate_enabled": True}}))
    merged = get_collaboration_profile(root)
    assert merged == {**decl, "deploy_gate_enabled": True}
    assert flow_description.resolve_collaboration_profile(root / "project.json") == merged
    assert flow_description.resolve_collaboration_profile(tmp_path / "nope" / "project.json") == decl
    src = Path(flow_description.__file__).read_text(encoding="utf-8")
    body = src[src.index("def resolve_collaboration_profile"):src.index("def resolve_gate_chain")]
    assert "default_audit_mode" not in body and "json.loads" not in body, "flow_description 第二份缺省/第二读取口未删"


def test_item1_fixture_registered_in_runall():
    from tools.aipos_cli.board_adapter import RUNALL_RELATIVE_PATH

    text = (REPO_ROOT / RUNALL_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "tests/test_aipos_f89_project_declaration_single_source.py" in text
