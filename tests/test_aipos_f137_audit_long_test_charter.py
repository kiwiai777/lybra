"""AIPOS-F137 件③(gap #117/#101): 审计章程「长测试写法」——每侧只跑一次、tee 落日志、bash 工具显式超时、计数读日志、禁后台跑。

唯一文本源 = 章程母本 agents/roles/auditor/AGENTS.md(「独立全量基线」第 7 条); 技能 audit-independent-evidence 只引用。
命令式样与超时数值由 charter_render_context(章程占位唯一键表)按项目声明渲染:
  - runall_command = 声明的测试清单位置拼 `bash <清单> 2>&1 | tee <日志>`(未声明 = 明确文字);
  - runall_timeout_seconds = test_contract.post_merge_regression.timeout_seconds(同一清单全量一次的既有时限声明;
    唯一读取口 workspace_config.project_test_contract, 未声明取 config.schema 缺省), 不新增键、不写死数值。
靶场全部临时目录(复用 test_aipos_f99 靶场 helper); 不碰真实治理根与真实工位。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f99_audit_runall_baseline import (  # noqa: E402  — 靶场唯一来源
    AUDIT_SKILL,
    AUDITOR_MASTER,
    CHARTER_RENDER,
    _auditor_ctx,
    _baseline_section,
    _make_gov,
)
from tools.aipos_cli.charter_render import render_charter  # noqa: E402
from tools.schema_loader import load_schema  # noqa: E402


def _long_test_item(text: str) -> str:
    """全文(母本或渲染物)→「独立全量基线」条内第 7 项「长测试写法」全文。"""
    m = re.search(r"^  7\. \*\*长测试写法.*", _baseline_section(text), flags=re.S | re.M)
    assert m, "缺「长测试写法」条"
    return m.group(0)


def _schema_default_timeout() -> int:
    tc = load_schema("config")["configuration_sources"]["project_json"]["schema"]["test_contract"]
    return int(tc["schema"]["post_merge_regression"]["schema"]["timeout_seconds"]["default"])


def test_master_long_test_rule_content():
    """母本条文: 只跑一次 / 命令与超时只经占位 / 计数读日志 / 禁为别的计数重跑 / 禁裸 & 后台; 不写死清单名与秒数。"""
    item = _long_test_item(AUDITOR_MASTER.read_text(encoding="utf-8"))
    for needle in ("每侧只跑一次", "{{runall_command}}", "显式传 timeout**, 值 = {{runall_timeout_seconds}}", "禁省略 timeout",
                   "计数一律 grep 该日志", "禁为取另一种计数再跑全量", "禁裸 `&` 后台跑后结束会话", "串行重跑"):
        assert needle in item, needle
    assert "run-all" not in item and not re.search(r"\b1500\b", item)


def test_render_lybra_shape_contains_command_and_declared_timeout(tmp_path, monkeypatch):
    """lybra 形(声明清单 + post_merge_regression.timeout_seconds=1500): 渲染物含实际命令与 1500 秒及其来源, 无残留占位。"""
    gov = _make_gov(tmp_path, monkeypatch, project="lybra", test_contract={
        "runall_path": "tests/run-all.sh", "require_tests": True,
        "post_merge_regression": {"mode": "warn", "execution": "async", "timeout_seconds": 1500}})
    ctx = _auditor_ctx(tmp_path, gov, "lybra")
    rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), ctx)
    assert "{{" not in rendered and "}}" not in rendered
    item = _long_test_item(rendered)
    print("\n----- F137 渲染样例: lybra 形·长测试写法 -----\n" + item)
    assert "`bash tests/run-all.sh 2>&1 | tee <本工位临时目录>/runall-<tip|main>.log`" in item
    assert "显式传 timeout**, 值 = `1500` 秒" in item and "post_merge_regression 的 timeout_seconds" in item
    assert "project.json test_contract" in item  # 来源指项目声明, 不是产品写死


def test_render_timeout_follows_declaration_and_schema_default(tmp_path, monkeypatch):
    """换项目/配置演进: 改声明值 → 渲染随之变; 未声明 timeout → 取 config.schema 缺省(行为不变), 不写死。"""
    gov = _make_gov(tmp_path, monkeypatch, project="otherproj", test_contract={
        "runall_path": "ci/full-suite.sh", "post_merge_regression": {"timeout_seconds": 2400}})
    item = _long_test_item(render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), _auditor_ctx(tmp_path, gov, "otherproj")))
    assert "`bash ci/full-suite.sh 2>&1 | tee" in item and "`2400` 秒" in item
    gov2 = _make_gov(tmp_path, monkeypatch, project="plainproj", test_contract={"runall_path": "ci/all.sh"})
    item2 = _long_test_item(render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), _auditor_ctx(tmp_path, gov2, "plainproj")))
    assert f"`{_schema_default_timeout()}` 秒" in item2 and "config.schema" in item2


def test_render_undeclared_project_says_so(tmp_path, monkeypatch):
    gov = _make_gov(tmp_path, monkeypatch, project="rangeproj", test_contract=None)
    rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), _auditor_ctx(tmp_path, gov, "rangeproj"))
    item = _long_test_item(rendered)
    assert "本项目未声明测试清单, 无全量可跑" in item and "{{" not in rendered and "run-all" not in rendered


def test_single_key_table_and_skill_only_references():
    """新键只在 charter_render_context 键表赋值一次; 技能只引用章程, 不复述命令/超时/计数式样。"""
    src = CHARTER_RENDER.read_text(encoding="utf-8")
    assert src.count('ctx["runall_command"]') == 1 and src.count('ctx["runall_timeout_seconds"]') == 1
    assert "1500" not in src
    skill = AUDIT_SKILL.read_text(encoding="utf-8")
    assert "长测试写法" in skill and "以章程" in skill
    assert "tee" not in skill and "timeout" not in skill and "grep -c" not in skill and "1500" not in skill
