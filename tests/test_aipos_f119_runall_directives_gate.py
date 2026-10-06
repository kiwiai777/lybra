"""AIPOS-F119 — 门侧测试登记判据先行: 认测试清单的 `# lybra-runall: discover/exclude` 声明行(F109 交回的引导前置)。

件① 唯一判据 workspace_config.runall_unregistered(test_files, runall_content) → (missing, directives)(自 card/AIPOS-F109
   逐字移植, 声明位 config.schema test_contract.runall_directives); board_adapter._check_test_in_runall 改调它。
件② 三种清单靶场:
   A 旧式逐文件登记(无 discover 行)——行为与现状完全相同: 登记了的放行, 未登记的按原文案拒, 文件名命中亦算登记;
   B discover 清单——命中声明式样的新增/修改测试不逐个列名也视为登记;
   C discover + exclude——被 exclude 声明排除的测试文件改动被拒, 拒因附该行声明理由与出口。
   另: 声明行形坏 = RUNALL_DIRECTIVE_INVALID 拒(fail-closed)。靶场复用 F97/F93/F78/F73D 既有构件(禁第二份靶场构造逻辑)。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _card, _git  # noqa: E402
from test_aipos_f97_test_file_criterion import RUNALL, _card_branch, _rig, _runall_reasons  # noqa: E402

import tools.aipos_cli.board_adapter as adapter  # noqa: E402
from tools.aipos_cli.workspace_config import runall_directives, runall_unregistered  # noqa: E402

DISCOVER_RUNALL = (
    "#!/bin/bash\n"
    "# 产品仓测试按 test_file_globs 自动发现\n"
    "# lybra-runall: discover\n"
    "# lybra-runall: exclude tests/test_old.py 读真实治理根, 夹具环境不可执行\n"
    "# lybra-runall: known-failure tools/acceptance/tests/test_acc.py 存量红, 转绿即删\n"
)


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _main_runall(repo: Path, text: str) -> None:
    """把 main 上的测试清单换成给定文本(在 F97 _rig 基础上, 不另造靶场)。"""
    _write(repo / RUNALL, text)
    _git(repo, "add", RUNALL)
    _git(repo, "commit", "-q", "-m", "runall")


# ===========================================================================
# 靶场 A: 旧式逐文件登记——行为与现状完全相同
# ===========================================================================

def test_range_a_legacy_per_file_registry_unchanged(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    # A-1 清单里以完整路径登记过的老测试被修改 = 放行
    _card(gov, "LEG-1", "claimed")
    _card_branch(repo, "LEG-1", write={"tests/test_old.py": "def test_old(): assert 1\n",
                                       "tools/acceptance/tests/test_acc.py": "def test_acc(): assert 1\n"})
    reasons, warnings = _runall_reasons(gov, "LEG-1")
    _show(f"[A-1·旧式·已登记] reasons={reasons} warnings={warnings}")
    assert reasons == [] and warnings == []

    # A-2 新增未登记测试 = 按原文案拒(缺失项 + 三条出口逐字不变)
    _card(gov, "LEG-2", "claimed")
    _card_branch(repo, "LEG-2", write={"tests/test_new_unlisted.py": "def test_n(): pass\n"})
    reasons, _w = _runall_reasons(gov, "LEG-2")
    _show(f"[A-2·旧式·未登记] reasons={reasons}")
    assert len(reasons) == 1
    assert reasons[0].startswith(f"TEST_NOT_IN_RUNALL: 本卡新增/修改的 test 文件未登记进项目声明的测试清单 {RUNALL}")
    assert "缺失项: tests/test_new_unlisted.py。" in reasons[0]
    assert f"出口: ①在 {RUNALL} 中登记这些测试; ②若属卡面漏列, 请顾问 amend 车道后重试; ③若确属越界, 请回退该文件。" in reasons[0]

    # A-3 只以文件名(basename)登记也算(原判据: 完整路径或文件名出现在清单文本)
    _card(gov, "LEG-3", "claimed")
    _card_branch(repo, "LEG-3", write={"tools/x/tests/test_by_name.py": "def test_b(): pass\n",
                                       RUNALL: "#!/bin/bash\npython3 -m pytest tests/test_old.py tools/acceptance/tests/test_acc.py\n# test_by_name.py\n"})
    reasons, _w = _runall_reasons(gov, "LEG-3")
    _show(f"[A-3·旧式·文件名登记] reasons={reasons}")
    assert reasons == []


def test_range_a_criterion_equals_legacy_substring_rule():
    """无 discover 行时 runall_unregistered 与原内联判据(路径或文件名子串)逐例一致(当前 main 逐文件登记格式的各种写法)。"""
    def legacy(files: list[str], text: str) -> list[str]:  # 原 _check_test_in_runall 第 3 步内联判据(对照用, 非实现)
        return [f for f in files if f not in text and f.split("/")[-1] not in text]

    per_file = (
        '#!/usr/bin/env bash\n'
        'declare -a files=(\n  "tests/ts/f32-custom-role-envelope.test.ts"\n)\n'
        '# AIPOS-F97: 交回检查「测试文件」判据单源\n'
        'run_pytest "tests/test_aipos_f97_test_file_criterion.py" "$REPO_ROOT/tests/test_aipos_f97_test_file_criterion.py"\n'
        'run_pytest "F117 units" "$REPO_ROOT/tools/aipos_cli/tests/test_gate_drift.py"\n'
        'if PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tests/test_aipos_f41_hard_rules.py"; then echo ok; fi\n'
        '# 只以文件名提到: test_by_name_only.py\n'
    )
    files = ["tests/test_aipos_f97_test_file_criterion.py", "tools/aipos_cli/tests/test_gate_drift.py",
             "tests/test_aipos_f41_hard_rules.py", "tests/ts/f32-custom-role-envelope.test.ts",
             "other/dir/test_by_name_only.py", "tests/test_never_registered_zz.py", "a/b/test_old.py"]
    cases = [(files, per_file), (files, "python3 -m pytest test_old.py\n"), (files, ""), ([], per_file)]
    for test_files, text in cases:
        missing, directives = runall_unregistered(test_files, text)
        _show(f"[A·判据对照] 清单长={len(text)} missing={missing} discover={directives['discover']}")
        assert directives == {"discover": False, "exclude": {}, "known_failures": {}}
        assert missing == legacy(test_files, text)
    assert runall_unregistered(files, per_file)[0] == ["tests/test_never_registered_zz.py", "a/b/test_old.py"]


# ===========================================================================
# 靶场 B: discover 清单——命中式样即登记
# ===========================================================================

def test_range_b_discover_registers_matching_tests(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    _main_runall(repo, DISCOVER_RUNALL)
    _card(gov, "DISC-1", "claimed")
    _card_branch(repo, "DISC-1", write={
        "tests/test_brand_new.py": "def test_n(): pass\n",
        "tools/aipos_cli/tests/test_legacy.py": "def test_legacy(): assert 1\n",
        "tools/acceptance/tests/test_acc.py": "def test_acc(): assert 1\n",  # known-failure 照常执行 = 登记
        "tools/aipos_cli/core.py": "# core v2\n",
    })
    reasons, warnings = _runall_reasons(gov, "DISC-1")
    _show(f"[B·discover·新改测试未逐个列名] reasons={reasons} warnings={warnings}")
    assert reasons == [] and warnings == []
    assert "test_brand_new.py" not in DISCOVER_RUNALL and "test_legacy.py" not in DISCOVER_RUNALL


# ===========================================================================
# 靶场 C: exclude 声明的文件改动被拒, 附声明理由与出口
# ===========================================================================

def test_range_c_excluded_test_rejected_with_reason_and_exit(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    _main_runall(repo, DISCOVER_RUNALL)
    _card(gov, "EXCL-1", "claimed")
    _card_branch(repo, "EXCL-1", write={"tests/test_old.py": "def test_old(): assert 1\n",
                                        "tests/test_brand_new.py": "def test_n(): pass\n"})
    reasons, _w = _runall_reasons(gov, "EXCL-1")
    _show(f"[C·exclude 改动] reasons={reasons}")
    assert len(reasons) == 1
    text = reasons[0]
    assert text.startswith(f"TEST_NOT_IN_RUNALL: 本卡测试文件被项目声明的测试清单 {RUNALL}")
    assert "tests/test_old.py(声明理由: 读真实治理根, 夹具环境不可执行)" in text
    assert "test_brand_new.py" not in text  # 未被排除的新测试照常视为登记
    assert "出口: ①删该 exclude 行让其随自动发现执行; ②确属不可在夹具环境执行, 请顾问裁定后再交" in text


# ===========================================================================
# fail-closed: 声明行形坏 = RUNALL_DIRECTIVE_INVALID 拒
# ===========================================================================

@pytest.mark.parametrize("bad_line", [
    "# lybra-runall: discovr",
    "# lybra-runall: discover tests/",
    "# lybra-runall: exclude tests/test_old.py",
    "# lybra-runall: exclude",
])
def test_malformed_directive_rejects(tmp_path, monkeypatch, bad_line):
    with pytest.raises(ValueError, match="^RUNALL_DIRECTIVE_INVALID"):
        runall_directives(f"#!/bin/bash\n{bad_line}\n")
    gov, repo = _rig(tmp_path, monkeypatch)
    _main_runall(repo, f"#!/bin/bash\n# lybra-runall: discover\n{bad_line}\n" if "discover" not in bad_line else f"#!/bin/bash\n{bad_line}\n")
    _card(gov, "BAD-1", "claimed")
    _card_branch(repo, "BAD-1", write={"tests/test_brand_new.py": "def test_n(): pass\n"})
    reasons, _w = _runall_reasons(gov, "BAD-1")
    _show(f"[形坏 {bad_line!r}] reasons={reasons}")
    assert len(reasons) == 1 and reasons[0].startswith("RUNALL_DIRECTIVE_INVALID")
    assert f"清单 {RUNALL}, 卡分支 card/BAD-1" in reasons[0] and "出口: 修正声明行后重交" in reasons[0]


def test_duplicate_target_rejects():
    with pytest.raises(ValueError, match="重复声明"):
        runall_directives("# lybra-runall: exclude a/test_x.py r1\n# lybra-runall: known-failure a/test_x.py r2\n")


# ===========================================================================
# 单源: 门只经唯一判据, 原内联子串循环已删; 声明行前缀/指令词读 schema 声明
# ===========================================================================

def test_gate_routes_through_single_criterion_and_schema_declaration():
    src = inspect.getsource(adapter._check_test_in_runall)
    assert "runall_unregistered(" in src
    assert "basename not in runall_content" not in src and "test_file not in runall_content" not in src
    from tools.schema_loader import load_schema

    decl = load_schema("config")["configuration_sources"]["project_json"]["schema"]["test_contract"]["runall_directives"]
    _show(f"[单源] 声明 prefix={decl['prefix']!r} directives={sorted(decl['directives'])}")
    assert decl["prefix"] == "lybra-runall:" and set(decl["directives"]) == {"discover", "exclude", "known-failure"}
    parsed = runall_directives(DISCOVER_RUNALL)
    assert parsed == {"discover": True,
                      "exclude": {"tests/test_old.py": "读真实治理根, 夹具环境不可执行"},
                      "known_failures": {"tools/acceptance/tests/test_acc.py": "存量红, 转绿即删"}}
