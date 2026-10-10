"""AIPOS-F145 — 阶段闸门读项目声明: 阶段档案落点 = project.json paths.stage_archive_root(缺省 = governance_structure.paths.stage_archive)。

实撞: 人肉期接入的项目(阶段档案在 governance/stage_archives/)两张审计 PASS 卡在 Lybra 内部 finalize 均 BLOCK
「Stage gate BLOCK: stage archive dir missing (<治理根>/stage_archive)」——阶段闸门只认产品全局路径, 不读项目声明。

件① 落点声明与读取: 新键进 config.schema project_json.paths(缺省取 governance_structure.paths.stage_archive, 存量行为不变),
      经唯一读取口 workspace_config.project_paths / stage_archive_root; 阶段闸门与一切读写阶段档案的点改走它; set-paths 自动覆盖新键。
件② 拒因更清楚: 闸门拒时附出口(声明 config.schema timeline_enforcement.stage_level.next_step)。
件③ 文档: 接入向导「接入既有人肉项目」第 1 步与命令说明补「声明阶段档案位置」。

靶场 = 临时产品仓 + 临时治理根(自造人肉期样本), 卡号形状 RNG-…(不写死项目卡号前缀)。不读写任何真实治理根。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f130_finalize_unlock import EXEC, _card_branch, _gov, _product, _verdict, _write  # noqa: E402  — 靶场唯一来源
from tools.aipos_cli import finalize as fz  # noqa: E402
from tools.aipos_cli.workspace_config import (  # noqa: E402
    PROJECT_PATH_KEYS,
    STAGE_ARCHIVE_PATH_KEY,
    project_paths,
    stage_archive_root,
)
from tools.schema_loader import load_schema, resolve_governance_path  # noqa: E402

LEGACY_DIR = "governance/stage_archives"  # 靶场自造的人肉期阶段档案位置(非缺省)


def _show(msg: str) -> None:
    print(msg, flush=True)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _stage_level() -> dict:
    return load_schema("config")["governance_structure"]["timeline_enforcement"]["stage_level"]


def _legacy_gov(root: Path) -> Path:
    """人肉期形治理根: 阶段档案在 governance/stage_archives/(十余篇同语义快照之一), 缺省 stage_archive/ 不存在。"""
    gov = _gov(root)
    for p in (gov / "stage_archive").glob("*"):
        p.unlink()
    (gov / "stage_archive").rmdir()
    _write(gov / LEGACY_DIR / "2026-09-21_stage.md", "# 阶段快照(人肉期)\n")
    _write(gov / LEGACY_DIR / "README.md", "# 索引(非快照)\n")
    _write(gov / "project.json", json.dumps({"project": "rng-proj"}, ensure_ascii=False, indent=2) + "\n")
    return gov


# ---------------------------------------------------------------------------
# 验收① 临时治理根: 未声明 finalize 拒并给出口; set-paths 声明后过闸; 缺省布局行为不变
# ---------------------------------------------------------------------------

def test_item1_item2_undeclared_refused_with_exit_then_set_paths_then_finalize_passes(tmp_path: Path, monkeypatch):
    gov = _legacy_gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo)
    task = "RNG-145"
    tip = _card_branch(repo, task)
    _verdict(gov, task, tip)
    base = _git(repo, "rev-parse", "HEAD")

    blocked = fz.finalize_task(task, EXEC, repo, governance_root=gov)
    _show(f"[件①②·未声明 finalize] {blocked['verdict']}: {blocked['message']}")
    assert blocked["verdict"] == "BLOCK" and "stage archive dir missing" in blocked["message"]
    assert str(gov / "stage_archive") in blocked["message"]
    exit_text = _stage_level()["next_step"].format(key=STAGE_ARCHIVE_PATH_KEY)
    assert exit_text in blocked["message"]
    assert f"lybra project set-paths --key {STAGE_ARCHIVE_PATH_KEY} --value <相对治理根路径>" in blocked["message"]
    assert blocked["stage_gate"]["declared"] is False and blocked["stage_gate"]["path_key"] == STAGE_ARCHIVE_PATH_KEY
    assert _git(repo, "rev-parse", "HEAD") == base  # 未合并

    pj = gov / "project.json"
    before = pj.read_bytes()
    argv = ["project", "set-paths", "--workspace-root", str(gov), "--key", STAGE_ARCHIVE_PATH_KEY, "--value", LEGACY_DIR]
    rc, out, err = _cli(argv)
    _show(f"[件①·set-paths 预演] rc={rc}\n{out}{err}")
    assert rc == 0 and pj.read_bytes() == before and f"paths.{STAGE_ARCHIVE_PATH_KEY}: None → '{LEGACY_DIR}'" in out
    rc, out, err = _cli(argv + ["--confirm"])
    _show(f"[件①·set-paths --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and json.loads(pj.read_text(encoding="utf-8"))["paths"] == {STAGE_ARCHIVE_PATH_KEY: LEGACY_DIR}

    resolved = project_paths(gov)
    assert resolved[STAGE_ARCHIVE_PATH_KEY] == gov / LEGACY_DIR and resolved["declared"][STAGE_ARCHIVE_PATH_KEY] is True
    done = fz.finalize_task(task, EXEC, repo, governance_root=gov)
    _show("[件①·声明后 finalize] " + done["verdict"] + "\n  " + "\n  ".join(done["operations"]))
    assert done["verdict"] == "PASS", done["message"]
    assert f"Stage gate OK: 1 stage snapshot(s) in {gov / LEGACY_DIR}" in "\n".join(done["operations"])  # README 不计(判据不变)
    assert _git(repo, "rev-parse", "HEAD^2") == tip


def test_item1_default_layout_unchanged(tmp_path: Path):
    """缺省布局(lybra 形: 治理根下 stage_archive/, project.json 不声明)= 行为不变: 同一目录、同一判据、同一结果。"""
    gov = _gov(tmp_path / "gov")
    _write(gov / "project.json", json.dumps({"project": "rng-proj"}) + "\n")
    default_dir = resolve_governance_path("stage_archive", gov)
    assert stage_archive_root(gov) == default_dir == gov / "stage_archive"
    assert project_paths(gov)["declared"][STAGE_ARCHIVE_PATH_KEY] is False
    gate = fz.check_stage_archive_gate(gov)
    _show(f"[件①·缺省布局] {gate}")
    assert gate["passed"] is True and gate["stage_archive_dir"] == str(default_dir) and gate["snapshot_count"] == 1
    # 无 project.json(脚手架建根之前 / 老夹具)也取缺省
    bare = tmp_path / "bare"
    (bare / "stage_archive").mkdir(parents=True)
    _write(bare / "stage_archive" / "S0.md", "# S0\n")
    assert fz.check_stage_archive_gate(bare)["passed"] is True
    # 缺省布局下拒因同样附出口(项目档案在别处时的声明方式)
    (gov / "stage_archive" / "S1.md").unlink()
    empty = fz.check_stage_archive_gate(gov)
    _show(f"[件②·缺省布局空目录拒] {empty['message']}")
    assert empty["passed"] is False and "no stage snapshot" in empty["message"]
    assert _stage_level()["next_step"].format(key=STAGE_ARCHIVE_PATH_KEY) in empty["message"]


def test_item1_declared_absolute_and_declared_but_missing(tmp_path: Path):
    gov = _legacy_gov(tmp_path / "gov")
    elsewhere = tmp_path / "archives-elsewhere"
    _write(elsewhere / "S9.md", "# S9\n")
    _write(gov / "project.json", json.dumps({"project": "rng-proj", "paths": {STAGE_ARCHIVE_PATH_KEY: str(elsewhere)}}) + "\n")
    assert fz.check_stage_archive_gate(gov)["stage_archive_dir"] == str(elsewhere)
    _write(gov / "project.json", json.dumps({"project": "rng-proj", "paths": {STAGE_ARCHIVE_PATH_KEY: "typo/archives"}}) + "\n")
    gate = fz.check_stage_archive_gate(gov)
    _show(f"[件②·声明了但目录不存在] {gate['message']}")
    assert gate["passed"] is False and str(gov / "typo/archives") in gate["message"] and gate["declared"] is True
    assert f"project.json paths.{STAGE_ARCHIVE_PATH_KEY}" in gate["message"]


def test_item1_set_paths_validates_new_key(tmp_path: Path):
    gov = _legacy_gov(tmp_path / "gov")
    rc, out, err = _cli(["project", "set-paths", "--workspace-root", str(gov), "--key", "stage_archive_dir", "--value", LEGACY_DIR])
    _show(f"[件①·set-paths 未知键] rc={rc}\n{err}")
    assert rc == 1 and "PATHS_KEY_UNKNOWN" in err and STAGE_ARCHIVE_PATH_KEY in err  # 可写键清单含新键(按 schema 校验)
    rc, out, err = _cli(["project", "set-paths", "--workspace-root", str(gov), "--key", STAGE_ARCHIVE_PATH_KEY, "--value", " "])
    assert rc == 1 and "PATHS_VALUE_INVALID" in err


# ---------------------------------------------------------------------------
# 声明: 新键只进既有 schema, 缺省取 governance_structure(不写第二份字面量), 清单只追加
# ---------------------------------------------------------------------------

def test_declaration_single_source():
    config = load_schema("config")
    spec = config["configuration_sources"]["project_json"]["schema"]["paths"]["schema"][STAGE_ARCHIVE_PATH_KEY]
    stage_level = _stage_level()
    assert spec["type"] == "string" and "default" not in spec and spec.get("optional") is not True
    assert spec["default_from"] == f"governance_structure.paths.{stage_level['path_key']}"
    assert STAGE_ARCHIVE_PATH_KEY in PROJECT_PATH_KEYS and PROJECT_PATH_KEYS[-1] == STAGE_ARCHIVE_PATH_KEY  # 只追加
    assert "{key}" in stage_level["next_step"] and "lybra project set-paths" in stage_level["next_step"]


# ---------------------------------------------------------------------------
# 验收② 读点清单: 产品代码读写阶段档案落点只经唯一读取口(棘轮: 不得再出现直接解析/写死)
# ---------------------------------------------------------------------------

STAGE_LITERALS = {"stage_archive", "stage_archive/", "stage_archives", "governance/stage_archives"}
# 允许的字面量出现(文件 → 次数): 只减不增
LITERAL_ALLOWED = {
    "tools/aipos_cli/project_structure.py": 1,  # export 文档分类标签 startswith("stage_archive/"), 非读点(不定位阶段档案)
}
# 经唯一读取口(stage_archive_root / project_paths[STAGE_ARCHIVE_PATH_KEY])的读写点(文件, 函数)
READ_POINTS = {
    ("tools/aipos_cli/finalize.py", "check_stage_archive_gate"),
    ("tools/aipos_cli/brief.py", "_get_stage_snapshot_info"),
    ("tools/aipos_cli/governance_commit.py", "check_governance_completeness"),
    ("tools/aipos_cli/board_adapter.py", "close_task"),
    ("tools/aipos_cli/governance_add.py", "add_stage"),
    ("tools/aipos_cli/workspace_config.py", "scaffold_project"),
    ("tools/aipos_cli/onboarding.py", "project_new_products"),
    ("tools/aipos_cli/aipos_cli.py", "main"),  # project new 建后列首份阶段快照
}


def _product_py() -> list[Path]:
    return [p for p in sorted((REPO_ROOT / "tools").rglob("*.py")) if "tests" not in p.parts and not p.name.startswith("test_")]


def test_item2_read_points_single_entry_ratchet():
    literal_hits: dict[str, int] = {}
    direct_resolve: list[str] = []
    callers: set[tuple[str, str]] = set()
    for path in _product_py():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in STAGE_LITERALS:
                literal_hits[rel] = literal_hits.get(rel, 0) + 1
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", None))
                if name == "resolve_governance_path" and node.args and isinstance(node.args[0], ast.Constant) \
                        and node.args[0].value == "stage_archive":
                    direct_resolve.append(f"{rel}:{node.lineno}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                called = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in ast.walk(node) if isinstance(c, ast.Call)}
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                # 经唯一读取口: 调 stage_archive_root, 或调 project_paths 并按 STAGE_ARCHIVE_PATH_KEY 取(阶段闸门同时要「是否声明」)
                if "stage_archive_root" in called or ("project_paths" in called and "STAGE_ARCHIVE_PATH_KEY" in names):
                    callers.add((rel, node.name))
    _show(f"[件②·读点] 经唯一读取口: {sorted(callers)}")
    _show(f"[件②·读点] 阶段档案字面量: {literal_hits}; 直接 resolve_governance_path('stage_archive'): {direct_resolve}")
    assert literal_hits == LITERAL_ALLOWED
    assert direct_resolve == []
    assert READ_POINTS <= callers, sorted(READ_POINTS - callers)


def test_item2_single_resolution_in_project_paths():
    """唯一读取口 stage_archive_root = project_paths[STAGE_ARCHIVE_PATH_KEY]; 缺省经声明 default_from 解析(不另写一份)。"""
    src = (REPO_ROOT / "tools" / "aipos_cli" / "workspace_config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "stage_archive_root")
    calls = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "project_paths" in calls


# ---------------------------------------------------------------------------
# 件③ 接入向导: 既有项目声明阶段档案位置一步
# ---------------------------------------------------------------------------

def test_item3_onboarding_guide_declares_stage_archive_step(tmp_path: Path):
    from tools.aipos_cli.onboarding import format_guide_text, generate_onboarding_guide

    guide = generate_onboarding_guide("probe_proj", home_root=str(tmp_path), code_repo=str(tmp_path / "repo"), host_segment="h")
    step1 = guide["legacy_onboarding"][0]
    text = format_guide_text(guide)
    section = text[text.index("═══ 附: 接入既有人肉项目"):]
    _show("[件③·接入向导第 1 步]\n" + step1["command"] + "\n" + step1["purpose"])
    assert f"--key {STAGE_ARCHIVE_PATH_KEY}" in step1["command"] and STAGE_ARCHIVE_PATH_KEY in step1["purpose"]
    assert "阶段档案" in section and "probe_proj" not in json.dumps([k for k in guide if k != "project_name"])
    readme = (REPO_ROOT / "tools" / "aipos_cli" / "README.md").read_text(encoding="utf-8")
    assert f"--key {STAGE_ARCHIVE_PATH_KEY}" in readme
