"""AIPOS-F97 — 交回检查「测试文件」判据单源且读声明(修 F95/F96 交回误拒)。

① 判据单源: workspace_config.card_test_files 为唯一判据, TEST_NOT_IN_RUNALL 与 NO_TESTS 共用; 改动集带状态
   (board_adapter._card_branch_changed_files(with_status=True) = git diff --name-status main...card/<ID>), 删除项 D 排除,
   重命名/复制取新路径。车道检查等其余调用方(缺省形)行为不变, 仍看得到删除项。
② 式样读声明: config.schema test_contract.test_file_globs(文件名 glob; 缺省在 schema, 项目 project.json 可整体覆盖)。
③ 靶场: 删除测试不要求登记; 文件名含 test 的 md/数据/输出不算; tests/ 下辅助脚本(fake_harness.py)不算; 新增/修改的
   test_*.py 未登记仍拒; 只删测试的 code 卡 NO_TESTS 仍按声明判; 项目覆盖 test_file_globs 生效; 缺省覆盖 lybra 产品仓
   现有全部测试文件命名(实测 git ls-files)。靶场复用 F73D/F78/F78C/F93 既有构件(禁第二份靶场构造逻辑)。
"""
from __future__ import annotations

import copy
import fnmatch
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _card, _git  # noqa: E402
from test_aipos_f78c_card_repo import _single_gov  # noqa: E402
from test_aipos_f93_report_contract_declared import _declare_contract  # noqa: E402

import tools.aipos_cli.board_adapter as adapter  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.task_loader import queue_root_for  # noqa: E402
from tools.aipos_cli.workspace_config import card_test_files, project_test_contract  # noqa: E402

RUNALL = "tests/run-all.sh"
LYBRA_CONTRACT = {"runall_path": RUNALL, "require_tests": True}


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _schema_default_globs() -> list[str]:
    from tools.schema_loader import load_schema

    decl = load_schema("config")["configuration_sources"]["project_json"]["schema"]["test_contract"]["schema"]
    return list(decl["test_file_globs"]["default"])


def _rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contract: dict | None = LYBRA_CONTRACT) -> tuple[Path, Path]:
    """lybra 形单仓: main 上已有登记过的测试、未登记的老测试、测试清单。"""
    gov, repo = _single_gov(tmp_path, monkeypatch)
    _declare_contract(gov, contract)
    _write(repo / RUNALL, "#!/bin/bash\npython3 -m pytest tests/test_old.py tools/acceptance/tests/test_acc.py\n")
    _write(repo / "tests" / "test_old.py", "def test_old(): pass\n")
    _write(repo / "tools" / "acceptance" / "tests" / "test_acc.py", "def test_acc(): pass\n")
    _write(repo / "tools" / "aipos_cli" / "tests" / "test_legacy.py", "def test_legacy(): pass\n")
    _write(repo / "tools" / "aipos_cli" / "core.py", "# core\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return gov, repo


def _card_branch(repo: Path, task_id: str, *, write: dict[str, str] | None = None, delete: list[str] | None = None,
                 rename: dict[str, str] | None = None) -> None:
    _git(repo, "checkout", "-q", "-b", f"card/{task_id}")
    for rel, text in (write or {}).items():
        _write(repo / rel, text)
    for rel in delete or []:
        _git(repo, "rm", "-q", rel)
    for old, new in (rename or {}).items():
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "mv", old, new)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{task_id}: work")
    _git(repo, "checkout", "-q", "main")


def _fm_of(gov: Path, task_id: str) -> dict:
    return nr._read_frontmatter(queue_root_for(gov) / "claimed" / f"{task_id.lower()}.md")


def _runall_reasons(gov: Path, task_id: str) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    reasons = adapter._check_test_in_runall(task_id=task_id, repo_root=gov, card_frontmatter=_fm_of(gov, task_id), warnings=warnings)
    return reasons, warnings


def _has_tests_reasons(gov: Path, task_id: str) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    reasons = adapter._check_has_tests(task_id=task_id, repo_root=gov, card_frontmatter=_fm_of(gov, task_id), warnings=warnings)
    return reasons, warnings


def _card_tests(gov: Path, repo: Path, task_id: str) -> list[str]:
    changes = adapter._card_branch_changed_files(repo, task_id, with_status=True)
    return card_test_files(changes, project_test_contract(gov, repo))


# ===========================================================================
# ③-a 删除测试文件不要求登记; ③-e 只删测试的 code 卡 NO_TESTS 仍按声明判; 车道检查仍看得到删除项
# ===========================================================================

def test_deleted_tests_need_no_registration_but_delete_only_is_no_tests(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    _card(gov, "DEL-1", "claimed")
    _card_branch(repo, "DEL-1", delete=["tests/test_old.py", "tools/acceptance/tests/test_acc.py"],
                 write={RUNALL: "#!/bin/bash\n", "docs/cleanup.md": "# cleanup\n"})
    changes = adapter._card_branch_changed_files(repo, "DEL-1", with_status=True)
    _show(f"[③-a·删除] 改动集(带状态)={changes}")
    assert ("D", "tests/test_old.py") in changes and ("D", "tools/acceptance/tests/test_acc.py") in changes
    assert _card_tests(gov, repo, "DEL-1") == []
    reasons, warnings = _runall_reasons(gov, "DEL-1")
    _show(f"[③-a·删除] TEST_NOT_IN_RUNALL reasons={reasons} warnings={warnings}")
    assert reasons == [] and warnings == []
    no_tests, _w = _has_tests_reasons(gov, "DEL-1")
    _show(f"[③-e·只删不加 code 卡] NO_TESTS reasons={no_tests}")
    assert len(no_tests) == 1 and no_tests[0].startswith("NO_TESTS") and "删除不计" in no_tests[0]
    # 车道检查(缺省形, 其余调用方)行为不变: 仍看得到删除项, 删除越界照拒
    names = adapter._card_branch_changed_files(repo, "DEL-1")
    _show(f"[①·缺省形不变] _card_branch_changed_files={names}")
    assert names == [path for _s, path in changes]
    assert "tests/test_old.py" in names and "tools/acceptance/tests/test_acc.py" in names
    scope = adapter._check_changes_in_scope(task_id="DEL-1", output_target="", repo_root=gov, lane_paths=["docs/", RUNALL],
                                            card_frontmatter=_fm_of(gov, "DEL-1"))
    _show(f"[①·车道仍看删除] CHANGES_OUT_OF_SCOPE={scope}")
    assert len(scope) == 1 and scope[0].startswith("CHANGES_OUT_OF_SCOPE")
    assert "tests/test_old.py" in scope[0] and "tools/acceptance/tests/test_acc.py" in scope[0]


# ===========================================================================
# ③-b 文件名含 test 的 md/数据/输出不算; ③-c tests/ 下非测试辅助脚本不算
# ===========================================================================

NOISE = {
    "tools/mcp_server/AIPOS-231_transport_test_stability_micro_plan.md": "# plan\n",
    "G2-FIX-TEST-OUTPUT.md": "# output\n",
    "task_cards/AIPOS-274/TEST-CHECKLIST.md": "- [ ] x\n",
    "tests/fixtures/test_data.json": "{}\n",
    "tests/output/latest_test_run.txt": "ok\n",
    "tests/f97_fragment_baseline.json": "{}\n",
    "tests/fake_harness.py": "# helper\n",
    "tools/aipos_cli/tests/fixtures/README.md": "# fixtures\n",
    "tests/playwright/playwright.config.cjs": "module.exports = {}\n",
}


def test_names_containing_test_and_helpers_are_not_test_files(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    _card(gov, "NAME-1", "claimed")
    _card_branch(repo, "NAME-1", write={**NOISE, "tools/aipos_cli/core.py": "# core v2\n",
                                        "tests/test_name1.py": "def test_n(): pass\n",
                                        RUNALL: "#!/bin/bash\npython3 -m pytest tests/test_old.py tests/test_name1.py\n"})
    found = _card_tests(gov, repo, "NAME-1")
    _show(f"[③-b/c·噪声文件] 本卡测试文件={found}")
    assert found == ["tests/test_name1.py"]
    assert _runall_reasons(gov, "NAME-1") == ([], [])
    assert _has_tests_reasons(gov, "NAME-1") == ([], [])
    # 只有辅助脚本 + 名含 test 的文档 + 代码: 不要求登记, 也不算有测试(NO_TESTS 按声明拒)
    _card(gov, "NAME-2", "claimed")
    _card_branch(repo, "NAME-2", write={"tests/fake_harness.py": "# helper\n", "G2-FIX-TEST-OUTPUT.md": "# out\n",
                                        "tools/aipos_cli/core.py": "# core v3\n"})
    reasons, warnings = _runall_reasons(gov, "NAME-2")
    no_tests, _w = _has_tests_reasons(gov, "NAME-2")
    _show(f"[③-c·只加辅助脚本] TEST_NOT_IN_RUNALL={reasons} NO_TESTS={no_tests}")
    assert reasons == [] and warnings == []
    assert len(no_tests) == 1 and no_tests[0].startswith("NO_TESTS")


# ===========================================================================
# ③-d 新增/修改的 test_*.py 未登记仍拒; 重命名取新路径
# ===========================================================================

def test_added_or_modified_unregistered_tests_still_rejected(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    _card(gov, "ADD-1", "claimed")
    _card_branch(repo, "ADD-1", write={"tests/test_new.py": "def test_new(): pass\n",
                                       "tools/aipos_cli/tests/test_legacy.py": "def test_legacy(): assert 1\n",
                                       "tests/test_old.py": "def test_old(): assert 1\n"})
    reasons, _w = _runall_reasons(gov, "ADD-1")
    _show(f"[③-d·新增+修改未登记] {reasons}")
    assert len(reasons) == 1 and reasons[0].startswith("TEST_NOT_IN_RUNALL")
    assert "tests/test_new.py" in reasons[0] and "tools/aipos_cli/tests/test_legacy.py" in reasons[0]
    assert "tests/test_old.py" not in reasons[0].split("缺失项:")[1]  # 修改的已登记测试: 登记判定不变(路径或文件名在清单)
    assert _has_tests_reasons(gov, "ADD-1") == ([], [])
    # 重命名: 取新路径(须按新路径登记), 旧路径不算
    _card(gov, "REN-1", "claimed")
    _card_branch(repo, "REN-1", rename={"tests/test_old.py": "tests/unit/test_renamed.py"})
    changes = adapter._card_branch_changed_files(repo, "REN-1", with_status=True)
    _show(f"[③-d·重命名] 改动集={changes}")
    assert len(changes) == 1 and changes[0][0].startswith("R") and changes[0][1] == "tests/unit/test_renamed.py"
    assert adapter._card_branch_changed_files(repo, "REN-1") == ["tests/unit/test_renamed.py"]
    reasons_r, _w = _runall_reasons(gov, "REN-1")
    _show(f"[③-d·重命名未登记] {reasons_r}")
    assert len(reasons_r) == 1 and "tests/unit/test_renamed.py" in reasons_r[0]
    # 其他语言缺省式样: TS / shell 测试未登记同样拒
    _card(gov, "ADD-2", "claimed")
    _card_branch(repo, "ADD-2", write={"tests/ts/f97-x.test.ts": "// t\n", "tests/test_f97_live.sh": "#!/bin/bash\n",
                                       "tests/playwright/f97.visual.spec.js": "// v\n"})
    reasons2, _w = _runall_reasons(gov, "ADD-2")
    _show(f"[③-d·TS/shell/spec 未登记] {reasons2}")
    assert len(reasons2) == 1
    for rel in ("tests/ts/f97-x.test.ts", "tests/test_f97_live.sh", "tests/playwright/f97.visual.spec.js"):
        assert rel in reasons2[0]


# ===========================================================================
# ③-f 项目声明覆盖 test_file_globs 生效(整体替换); 形坏 fail-closed; 按仓覆盖
# ===========================================================================

def test_project_declared_globs_override_and_malformed_fail_closed(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch, contract={**LYBRA_CONTRACT, "test_file_globs": ["*_spec.rb"]})
    contract = project_test_contract(gov, repo)
    _show(f"[③-f·项目覆盖] contract={json.dumps(contract, ensure_ascii=False)}")
    assert contract["test_file_globs"] == ["*_spec.rb"] and contract["test_file_globs_source"].endswith("test_contract.test_file_globs")
    _card(gov, "OVR-1", "claimed")
    _card_branch(repo, "OVR-1", write={"spec/user_spec.rb": "# rb\n", "tests/test_ignored.py": "def test_i(): pass\n"})
    assert _card_tests(gov, repo, "OVR-1") == ["spec/user_spec.rb"]
    reasons, _w = _runall_reasons(gov, "OVR-1")
    _show(f"[③-f·覆盖后未登记] {reasons}")
    assert len(reasons) == 1 and "spec/user_spec.rb" in reasons[0] and "test_ignored.py" not in reasons[0]
    # 覆盖后 test_*.py 不再算测试: 只加 test_*.py 的 code 卡 = NO_TESTS, 拒因引用声明式样与来源
    _card(gov, "OVR-2", "claimed")
    _card_branch(repo, "OVR-2", write={"tests/test_only.py": "def test_o(): pass\n", "tools/aipos_cli/core.py": "# v\n"})
    no_tests, _w = _has_tests_reasons(gov, "OVR-2")
    _show(f"[③-f·覆盖后 NO_TESTS] {no_tests}")
    assert len(no_tests) == 1 and "*_spec.rb" in no_tests[0] and "test_contract.test_file_globs" in no_tests[0]
    # 形坏 = TEST_CONTRACT_INVALID(fail-closed, 不回落缺省)
    for bad in ([], "test_*.py", ["tests/test_*.py"], [""], [3]):
        _declare_contract(gov, {**LYBRA_CONTRACT, "test_file_globs": bad})
        r, _w = _runall_reasons(gov, "OVR-1")
        assert r and r[0].startswith("TEST_CONTRACT_INVALID") and "test_file_globs" in r[0], (bad, r)
    _show("[③-f·形坏] [] / 'test_*.py' / ['tests/test_*.py'] / [''] / [3] 均 TEST_CONTRACT_INVALID")
    # 按仓覆盖: repos.items 内的仓取覆盖式样, 其余取顶层(未声明 = schema 缺省)
    other = tmp_path / "repo-other"
    other.mkdir()
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl.pop("code_repo", None)
    decl["repos"] = {"default": "app", "items": {"app": str(repo), "lib": str(other)}}
    decl["test_contract"] = {**LYBRA_CONTRACT, "repos": {"lib": {"test_file_globs": ["*Test.java"]}}}
    (gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    assert project_test_contract(gov, repo)["test_file_globs"] == _schema_default_globs()
    assert project_test_contract(gov, other)["test_file_globs"] == ["*Test.java"]


def test_default_globs_come_from_schema_and_missing_default_fail_closed(tmp_path, monkeypatch):
    from tools import schema_loader
    from tools.schema_loader import SchemaLoadError

    gov, repo = _rig(tmp_path, monkeypatch, contract=None)
    contract = project_test_contract(gov, repo)
    _show(f"[②·缺省读 schema] contract={json.dumps(contract, ensure_ascii=False)}")
    assert contract["test_file_globs"] == _schema_default_globs()
    assert contract["test_file_globs_source"].startswith("config.schema")
    # 改声明缺省 → 判据跟随(代码不写死式样)
    real = schema_loader.load_schema("config")
    probe = copy.deepcopy(real)
    tc = probe["configuration_sources"]["project_json"]["schema"]["test_contract"]["schema"]
    tc["test_file_globs"]["default"] = ["*.probe"]
    monkeypatch.setattr(schema_loader, "load_schema", lambda kind, *a, **k: probe if kind == "config" else real)
    assert card_test_files([("A", "x/a.probe"), ("A", "tests/test_a.py")], project_test_contract(gov, repo)) == ["x/a.probe"]
    # 声明缺省缺失 = SchemaLoadError(fail-closed, 不猜)
    del tc["test_file_globs"]["default"]
    with pytest.raises(SchemaLoadError, match="test_file_globs"):
        project_test_contract(gov, repo)
    _show("[②·schema 缺省缺失] SchemaLoadError")


# ===========================================================================
# ① fail-closed: 改动集状态判不了 = 拒(两处检查同一拒因), 约定缺式样 = 拒
# ===========================================================================

def test_unresolvable_change_status_fail_closed_in_both_checks(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    _card(gov, "UNK-1", "claimed")
    _card_branch(repo, "UNK-1", write={"tests/test_u.py": "def test_u(): pass\n"})
    monkeypatch.setattr(adapter, "_card_branch_changed_files",
                        lambda root, tid, *, with_status=False: [("X", "tests/test_u.py")] if with_status else ["tests/test_u.py"])
    r1, _w = _runall_reasons(gov, "UNK-1")
    r2, _w = _has_tests_reasons(gov, "UNK-1")
    _show(f"[①·状态判不了] TEST_NOT_IN_RUNALL 位={r1} NO_TESTS 位={r2}")
    assert len(r1) == 1 and r1[0].startswith("TEST_FILES_UNRESOLVED") and r1 == r2
    with pytest.raises(ValueError, match="TEST_FILES_UNRESOLVED"):
        card_test_files([("A", "tests/test_a.py")], {"runall_path": RUNALL})


# ===========================================================================
# ② 缺省覆盖 lybra 产品仓现有全部测试文件命名(实测本仓 git ls-files, 只读)
# ===========================================================================

# 位于 tests/ 目录但不是自动化测试的文件(命名式样不应命中; 每条带理由)
NON_TEST_UNDER_TESTS = {
    "__init__.py": "包标记",
    "run-all.sh": "测试清单/总入口(runall_path 本身, 判据另行排除)",
    "playwright.config.cjs": "Playwright 配置",
    "f23_live_acceptance.sh": "人工起真门的活体验收脚本, 不在任何自动化 runner 中",
    "manual_test_aipos366.py": "人工对活体门的验证脚本(依赖 requests + 真门), 不在任何自动化 runner 中",
    "fake_harness.py": "F95 假 harness 辅助脚本(被夹具按 launch 模板拉起; run-all 只 py_compile, 非测试)",
}
CODE_SUFFIXES = (".py", ".sh", ".ts", ".js", ".cjs", ".mjs")


def _repo_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def test_schema_default_covers_all_existing_lybra_test_files():
    globs = _schema_default_globs()
    contract = {"runall_path": RUNALL, "test_file_globs": globs}
    files = _repo_files()
    under_tests = [f for f in files if "/tests/" in f"/{f}" and f.endswith(CODE_SUFFIXES)]
    expected_tests = [f for f in under_tests if f.rsplit("/", 1)[-1] not in NON_TEST_UNDER_TESTS]
    # pytest 缺省收集式样之外的放置: tests/ 目录外的 test_*.py 也是现存测试
    expected_tests += [f for f in files if "/tests/" not in f"/{f}" and fnmatch.fnmatchcase(f.rsplit("/", 1)[-1], "test_*.py")]
    found = card_test_files([("M", f) for f in files], contract)
    missed = sorted(set(expected_tests) - set(found))
    _show(f"[②·缺省覆盖实测] 式样={globs} 现存测试文件={len(expected_tests)} 命中={len(found)} 漏={missed}")
    assert expected_tests and missed == []
    # 位于 tests/ 下的非测试文件不命中; 名含 test 的文档/数据不命中
    non_tests = [f for f in under_tests if f.rsplit("/", 1)[-1] in NON_TEST_UNDER_TESTS]
    noise = [f for f in files if "test" in f.lower() and not f.endswith(CODE_SUFFIXES)]
    hits = sorted(set(found) & set(non_tests + noise))
    _show(f"[②·非测试不命中] tests/ 下非测试={non_tests} 名含 test 的非代码文件数={len(noise)} 误命中={hits}")
    assert hits == []


# ===========================================================================
# ① 单源: 两处检查只经唯一判据, 子串判据已删
# ===========================================================================

def test_both_checks_route_through_single_criterion():
    runall_src = inspect.getsource(adapter._check_test_in_runall)
    has_tests_src = inspect.getsource(adapter._check_has_tests)
    for src in (runall_src, has_tests_src):
        assert "_card_test_files_for(" in src
        assert '"test" in' not in src and '"/tests/" in' not in src and 'startswith("tests/")' not in src
    assert "card_test_files(" in inspect.getsource(adapter._card_test_files_for)
    _show("[①·单源] _check_test_in_runall / _check_has_tests 均经 _card_test_files_for → workspace_config.card_test_files; 子串判据 0 处")
