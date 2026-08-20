"""AIPOS-F5 — 卡号模式入声明: 归属解析等一切"这是不是卡号"判断读同一份项目声明。

覆盖 (验收夹具=实景):
  ① 858655a 原文归属成功 (fix(sync) 不再误抓, 句尾命中)
  ② feat/fix/chore/docs/refactor/test/perf(ID) + 裸前缀 + Merge 全家族回归
  ③ 伪造 fix(notacard): ... (无处有卡号) → 判无归属
  ④ 临时改声明模式为 XX-[0-9]+ → 解析行为跟随 (验完还原, 证明读声明)
  ⑤ 声明缺失时出声报错, 禁内置默认模式 (C2 原则)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.aipos_cli.deployment_authorization import (
    _branch_pattern_regex,
    _resolve_task_id_pattern,
    _task_id_from_commit_subject,
)
from tools.schema_loader import SchemaLoadError

REPO_ROOT = Path(__file__).resolve().parents[1]

_TASK_ID_PATTERN = "AIPOS-[A-Z0-9]+"


@pytest.fixture
def gov_root(tmp_path):
    """临时治理工作区, 声明 task_id_pattern (卡号形状单源)。"""
    gov = tmp_path / "gov"
    gov.mkdir()
    (gov / "card_policy.json").write_text(
        json.dumps({"schema_version": "1.0.0", "task_id_pattern": _TASK_ID_PATTERN}),
        encoding="utf-8",
    )
    return gov


def _decl_file(gov_root: Path) -> Path:
    return gov_root / "card_policy.json"


# ---------------------------------------------------------------------------
# ① 858655a 原文: fix(sync) 不再误抓, 句尾 (AIPOS-F3) 命中
# ---------------------------------------------------------------------------

def test_858655a_sentence_end_hit(gov_root):
    subject = "fix(sync): harness-root 解析禁按 cwd 猜, 解析不到即出声停 (AIPOS-F3)"
    assert _task_id_from_commit_subject(subject, repo_root=REPO_ROOT, governance_root=gov_root) == "AIPOS-F3"


# ---------------------------------------------------------------------------
# ② 全家族回归
# ---------------------------------------------------------------------------

def test_conventional_family(gov_root):
    for subject, expected in [
        ("feat(AIPOS-X): 新增功能", "AIPOS-X"),
        ("fix(AIPOS-C3B): FIX轮", "AIPOS-C3B"),
        ("chore(AIPOS-C4): 清理", "AIPOS-C4"),
        ("docs(AIPOS-C1): 手册", "AIPOS-C1"),
        ("refactor(AIPOS-C2): 重构", "AIPOS-C2"),
        ("test(AIPOS-C3): 测试", "AIPOS-C3"),
        ("perf(AIPOS-A1): 性能", "AIPOS-A1"),
    ]:
        assert _task_id_from_commit_subject(subject, repo_root=REPO_ROOT, governance_root=gov_root) == expected, subject


def test_bare_prefix(gov_root):
    assert _task_id_from_commit_subject("AIPOS-C2: 身份配置单一真相", repo_root=REPO_ROOT, governance_root=gov_root) == "AIPOS-C2"


def test_merge_message(gov_root):
    subject = "Merge card/AIPOS-A1: 顾问产生侧工具化 (PASS_WITH_NOTES verdict_AIPOS-A1_20260819_143222)"
    assert _task_id_from_commit_subject(subject, repo_root=REPO_ROOT, governance_root=gov_root) == "AIPOS-A1"
    assert _task_id_from_commit_subject("Merge branch 'card/AIPOS-C3B'", repo_root=REPO_ROOT, governance_root=gov_root) == "AIPOS-C3B"


# ---------------------------------------------------------------------------
# ③ 伪造 fix(notacard) → 判无归属
# ---------------------------------------------------------------------------

def test_forged_notacard_no_ownership(gov_root):
    assert _task_id_from_commit_subject("fix(notacard): 无卡号提交", repo_root=REPO_ROOT, governance_root=gov_root) is None
    assert _task_id_from_commit_subject("Initial commit", repo_root=REPO_ROOT, governance_root=gov_root) is None


# ---------------------------------------------------------------------------
# ④ 声明跟随: 改模式 → 解析行为跟随 (验完还原)
# ---------------------------------------------------------------------------

def test_pattern_follows_declaration(gov_root):
    decl = _decl_file(gov_root)
    original = decl.read_text(encoding="utf-8")

    # 改声明为 XX-[0-9]+
    data = json.loads(original)
    data["task_id_pattern"] = "XX-[0-9]+"
    decl.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    try:
        assert _task_id_from_commit_subject("fix(XX-1): 新声明测试", repo_root=REPO_ROOT, governance_root=gov_root) == "XX-1"
        assert _task_id_from_commit_subject("XX-2: 裸前缀", repo_root=REPO_ROOT, governance_root=gov_root) == "XX-2"
        # 旧模式不再命中 → 判无归属
        assert _task_id_from_commit_subject("feat(AIPOS-X): 不再匹配", repo_root=REPO_ROOT, governance_root=gov_root) is None
    finally:
        decl.write_text(original, encoding="utf-8")

    # 还原后旧模式恢复
    assert _task_id_from_commit_subject("feat(AIPOS-X): 恢复", repo_root=REPO_ROOT, governance_root=gov_root) == "AIPOS-X"


# ---------------------------------------------------------------------------
# ⑤ 声明缺失 → 出声报错 (C2 原则, 禁内置默认)
# ---------------------------------------------------------------------------

def test_missing_declaration_raises(tmp_path):
    empty_gov = tmp_path / "empty_gov"
    empty_gov.mkdir()  # 无 card_policy.json
    with pytest.raises(SchemaLoadError):
        _task_id_from_commit_subject("feat(AIPOS-X): x", repo_root=REPO_ROOT, governance_root=empty_gov)


def test_missing_pattern_key_raises(tmp_path):
    gov = tmp_path / "gov"
    gov.mkdir()
    (gov / "card_policy.json").write_text(
        json.dumps({"schema_version": "1.0.0"}), encoding="utf-8"  # 无 task_id_pattern 键
    )
    with pytest.raises(SchemaLoadError):
        _task_id_from_commit_subject("feat(AIPOS-X): x", repo_root=REPO_ROOT, governance_root=gov)


def test_none_governance_root_raises():
    with pytest.raises(SchemaLoadError):
        _task_id_from_commit_subject("feat(AIPOS-X): x", repo_root=REPO_ROOT, governance_root=None)


# ---------------------------------------------------------------------------
# branch pattern 派生使用同一份声明
# ---------------------------------------------------------------------------

def test_branch_pattern_regex_uses_declared_pattern():
    regex = _branch_pattern_regex(REPO_ROOT, _TASK_ID_PATTERN)
    assert regex is not None
    import re
    m = re.search(regex, "Merge card/AIPOS-C3C: summary (verdict_X)")
    assert m and m.group(1) == "AIPOS-C3C"


def test_resolve_task_id_pattern(gov_root):
    assert _resolve_task_id_pattern(gov_root, REPO_ROOT) == _TASK_ID_PATTERN
