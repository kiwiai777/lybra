"""AIPOS-F99: 审计章程「独立全量基线」——测试清单位置由产品按项目声明渲染

件① 章程母本 agents/roles/auditor/AGENTS.md 审计流程新增硬规矩「独立全量基线」, 清单位置只用占位 `{{runall_path}}`;
件② charter_render_context(章程占位唯一键表)新增 runall_path, 读 workspace_config.project_test_contract
    (project.json test_contract.runall_path; 未声明 = 明确「本项目未声明测试清单」文字; 形坏 = ValueError fail-closed);
件③ 审计技能 audit-independent-evidence 取证步骤引用章程(不复述细则);
件④ 靶场: lybra 形(声明 tests/run-all.sh)与未声明 test_contract 的靶场项目各渲染一次审计章程, 前者含实值、后者含未声明文字,
    二者均无残留 `{{` 占位。

靶场全部临时目录; 不碰真实治理根与真实工位。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.charter_render import (  # noqa: E402
    charter_render_context,
    render_charter,
    render_context_fingerprint,
)

AUDITOR_MASTER = REPO_ROOT / "agents" / "roles" / "auditor" / "AGENTS.md"
AUDIT_SKILL = REPO_ROOT / "agents" / "skills" / "audit-independent-evidence" / "SKILL.md"
CHARTER_RENDER = REPO_ROOT / "tools" / "aipos_cli" / "charter_render.py"
UNDECLARED_TEXT = "本项目未声明测试清单"
UNDECLARED_REPORT_NOTE = "项目未声明 test_contract.runall_path"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, project: str, test_contract: dict | None) -> Path:
    """靶场治理根: project.json(code_repo 指临时 git 仓)+ 可选 test_contract 声明。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    root = tmp_path / f"gov-{project}"
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    (root / "task_cards").mkdir()
    code_repo = tmp_path / f"product-{project}"
    code_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(code_repo), check=True, capture_output=True)
    decl: dict = {
        "project": project,
        "code_repo": str(code_repo),
        "config_version": 1,
        "paths": {"manual_gate_mode": False, "return_root": "task_cards", "verdict_root": "task_cards",
                  "queue_root": "5_tasks/queue", "task_cards_root": "task_cards"},
    }
    if test_contract is not None:
        decl["test_contract"] = test_contract
    _write(root / "project.json", json.dumps(decl))
    return root


def _auditor_ctx(tmp_path: Path, gov: Path, project: str) -> dict:
    harness = tmp_path / f"pi-{project}" / f"{project}-auditor"
    harness.mkdir(parents=True, exist_ok=True)
    identity = {"harness_root": str(harness), "role": "auditor", "instance": f"audit.{project}.hostx", "project": project,
                "owner_policy_ref": None, "governance_root_declared": None, "token_projects": [], "gate_url": None}
    return charter_render_context(gov, identity=identity, product_commit="deadbeef")


def _baseline_section(rendered: str) -> str:
    """渲染物中「独立全量基线」条目全文(到下一条顶层列表项为止)。"""
    m = re.search(r"^- \*\*独立全量基线.*?(?=^- )", rendered, flags=re.S | re.M)
    assert m, "渲染物缺「独立全量基线」条"
    return m.group(0)


# ===========================================================================
# 件④ 两种渲染
# ===========================================================================

def test_render_lybra_declared_runall_path(tmp_path, monkeypatch):
    """lybra 形(声明 tests/run-all.sh): 审计章程渲染含实值、无残留占位、无未声明文字。"""
    gov = _make_gov(tmp_path, monkeypatch, project="lybra", test_contract={"runall_path": "tests/run-all.sh", "require_tests": True})
    ctx = _auditor_ctx(tmp_path, gov, "lybra")
    assert "`tests/run-all.sh`" in ctx["runall_path"]
    rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), ctx)
    assert "{{" not in rendered and "}}" not in rendered
    section = _baseline_section(rendered)
    assert "`tests/run-all.sh`" in section
    assert UNDECLARED_TEXT not in rendered
    print("\n----- F99 渲染样例: lybra 形(声明 tests/run-all.sh)·独立全量基线节 -----\n" + section)


def test_render_undeclared_project_has_explicit_text(tmp_path, monkeypatch):
    """未声明 test_contract 的靶场项目: 渲染为明确「本项目未声明测试清单」文字(含报告注明语), 不留空占位。"""
    gov = _make_gov(tmp_path, monkeypatch, project="rangeproj", test_contract=None)
    ctx = _auditor_ctx(tmp_path, gov, "rangeproj")
    assert ctx["runall_path"] and UNDECLARED_TEXT in ctx["runall_path"]
    rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), ctx)
    assert "{{" not in rendered and "}}" not in rendered
    section = _baseline_section(rendered)
    assert UNDECLARED_TEXT in section and UNDECLARED_REPORT_NOTE in section
    assert "run-all.sh" not in rendered  # 未声明项目不得出现任何写死的清单名
    print("\n----- F99 渲染样例: 未声明 test_contract 靶场·独立全量基线节 -----\n" + section)


def test_render_runall_path_follows_declaration_rename(tmp_path, monkeypatch):
    """配置演进: 清单改名只改 project.json → 渲染跟随, 且声明指纹变(触发重渲染)。"""
    gov = _make_gov(tmp_path, monkeypatch, project="lybra", test_contract={"runall_path": "tests/run-all.sh"})
    ctx_a = _auditor_ctx(tmp_path, gov, "lybra")
    decl = json.loads((gov / "project.json").read_text())
    decl["test_contract"]["runall_path"] = "ci/full-suite.sh"
    (gov / "project.json").write_text(json.dumps(decl))
    ctx_b = _auditor_ctx(tmp_path, gov, "lybra")
    assert "`ci/full-suite.sh`" in ctx_b["runall_path"]
    assert render_context_fingerprint(ctx_a) != render_context_fingerprint(ctx_b)
    rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), ctx_b)
    assert "`ci/full-suite.sh`" in _baseline_section(rendered) and "run-all.sh" not in _baseline_section(rendered)


def test_render_repo_override_applies_to_default_repo(tmp_path, monkeypatch):
    """test_contract.repos 对缺省产品仓的覆盖经同一读取口生效(卡仓 = 缺省产品仓)。"""
    gov = _make_gov(tmp_path, monkeypatch, project="multi", test_contract=None)
    decl = json.loads((gov / "project.json").read_text())
    code_repo = decl.pop("code_repo")
    decl["repos"] = {"default": "app", "items": {"app": code_repo}}
    decl["test_contract"] = {"runall_path": "tests/run-all.sh", "repos": {"app": {"runall_path": "scripts/all-tests.sh"}}}
    (gov / "project.json").write_text(json.dumps(decl))
    ctx = _auditor_ctx(tmp_path, gov, "multi")
    assert "`scripts/all-tests.sh`" in ctx["runall_path"]


@pytest.mark.parametrize("bad", [{"runall_path": "/abs/run-all.sh"}, {"runall_path": "../x.sh"}, {"runall_path": ""}, "tests/run-all.sh"])
def test_invalid_test_contract_fails_closed(tmp_path, monkeypatch, bad):
    """声明形坏 = 拒渲染(ValueError TEST_CONTRACT_INVALID 原样抛), 不降级为「未声明」。"""
    gov = _make_gov(tmp_path, monkeypatch, project="lybra", test_contract=None)
    decl = json.loads((gov / "project.json").read_text())
    decl["test_contract"] = bad
    (gov / "project.json").write_text(json.dumps(decl))
    with pytest.raises(ValueError, match="TEST_CONTRACT_INVALID"):
        _auditor_ctx(tmp_path, gov, "lybra")


# ===========================================================================
# 件① 母本硬规矩条文 / 件② 唯一键表与唯一读取口
# ===========================================================================

def test_master_baseline_rule_content():
    """母本条文: 两处独立跑、worktree --detach 建/remove 删、禁 stash/checkout、同一 grep 式样、新增失败=FAIL、
    计数异常先串行重跑、未声明跳过并注明; 清单位置只经占位, 不写死。"""
    text = AUDITOR_MASTER.read_text(encoding="utf-8")
    m = re.search(r"^- \*\*独立全量基线.*?(?=^- )", text, flags=re.S | re.M)
    assert m, "母本缺「独立全量基线」条"
    rule = m.group(0)
    for needle in ("{{runall_path}}", "被审 tip", "main", "git worktree add --detach", "git worktree remove",
                   "git stash", "git checkout", 'grep -c "^✗"', 'grep -c "^FAILED"', "失败清单 diff",
                   "新增失败 = FAIL", "存量失败只记录不判", "串行重跑", UNDECLARED_REPORT_NOTE):
        assert needle in rule, needle
    assert "run-all.sh" not in text  # 禁写死清单名(位置只读声明)


def test_runall_path_single_key_table_and_single_reader():
    """runall_path 只在 charter_render_context(唯一键表)赋值, 且只经 workspace_config.project_test_contract 读取;
    charter_render.py 不直接读 project.json test_contract、不写死清单名。"""
    src = CHARTER_RENDER.read_text(encoding="utf-8")
    assert src.count('ctx["runall_path"]') == 1
    assert "project_test_contract(gov" in src
    assert 'get("test_contract")' not in src and "['test_contract']" not in src and '["test_contract"]' not in src
    assert "run-all.sh" not in src
    # 键表之外别处不得再定义 runall_path 渲染键(禁第二份键表)
    others = [p for p in (REPO_ROOT / "tools").rglob("*.py")
              if p != CHARTER_RENDER and re.search(r"""["']runall_path["']\s*:\s*f?["']`""", p.read_text(encoding="utf-8", errors="ignore"))]
    assert others == [], others


# ===========================================================================
# 件③ 审计技能引用章程
# ===========================================================================

def test_skill_references_charter_without_duplicating_details():
    text = AUDIT_SKILL.read_text(encoding="utf-8")
    assert "独立全量基线" in text and "以章程为准" in text
    # 不复述细则(防两处漂移): 计数式样与判据细节只在章程
    assert 'grep -c' not in text and "串行重跑" not in text and "run-all" not in text
