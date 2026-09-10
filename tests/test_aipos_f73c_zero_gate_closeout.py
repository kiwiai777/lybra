"""AIPOS-F73C 收口夹具(顾问代修, Owner 2026-09-08 仲裁 C)。

覆盖:
  件① 角色判据读注册表(name / naming.prefix / 自定义角色), 禁子串猜;
  件③ token scope 声明:executor/auditor 只剩 task_progress, advisor 持账务动词;
  件⑤ `lybra queue rework` CLI 子命令存在且 argparse 可解析。
靶场分根:注册表读产品仓(REPO_ROOT), 治理数据不落产品仓。
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.draft_writer import _card_role_class  # noqa: E402
from tools.schema_loader import load_schema  # noqa: E402


# ---------------------------------------------------------------------------
# 件① 角色判据
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"assigned_to": "exec.lybra.kiwiai-dev", "agent_instance": "exec.lybra.kiwiai-dev"}, "executor"),
        ({"assigned_to": "audit.lybra.kiwiai-dev"}, "auditor"),
        ({"agent_instance": "advisor.lybra.kiwiai-dev"}, "advisor"),
        ({"assigned_to": "executor"}, "executor"),
        ({"assigned_to": "auditor.chris.kiwiai-dev"}, "auditor"),
    ],
)
def test_f73c_item1_role_class_from_registry(metadata, expected):
    assert _card_role_class(metadata, REPO_ROOT) == expected


def test_f73c_item1_unknown_role_is_none_not_substring_guess():
    # "exec" 子串出现在名字里但既不是注册表 role 也不是 naming.prefix → 不得猜成 executor
    assert _card_role_class({"assigned_to": "someexecutive.lybra.host"}, REPO_ROOT) is None
    assert _card_role_class({"assigned_to": ""}, REPO_ROOT) is None


def test_f73c_item1_registry_unreadable_is_warning_not_silent(monkeypatch, capsys):
    # 注册表读不到(SchemaLoadError)→ 返回 None 且 stderr 有 warning(禁静默 fail-open)
    import tools.schema_loader as sl

    def _boom(*_a, **_k):
        raise sl.SchemaLoadError("simulated: roles.schema.json missing")

    monkeypatch.setattr(sl, "load_schema", _boom)
    result = _card_role_class({"assigned_to": "exec.lybra.kiwiai-dev"}, None)
    assert result is None
    assert "role registry unreadable" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 件③ scope 声明
# ---------------------------------------------------------------------------

def _scopes(role: str) -> list[str]:
    for spec in load_schema("roles", REPO_ROOT).get("roles", []):
        if spec.get("role") == role:
            return list(spec.get("scopes", []))
    raise AssertionError(f"role {role} missing from registry")


def test_f73c_item3_executor_auditor_hold_no_gate_verbs():
    assert _scopes("executor") == ["task_progress"]
    assert _scopes("auditor") == ["task_progress"]


def test_f73c_item3_advisor_holds_account_verbs():
    advisor = set(_scopes("advisor"))
    assert {"queue_claim", "queue_return", "audit_verdict", "queue_close", "queue_rework"} <= advisor


# ---------------------------------------------------------------------------
# 件⑤ queue rework CLI
# ---------------------------------------------------------------------------

def test_f73c_item5_queue_rework_cli_parses():
    from tools.aipos_cli.aipos_cli import build_parser

    parser = build_parser()
    cmd = (
        "lybra queue rework --task-id AIPOS-T1 --actor advisor.lybra.kiwiai-dev "
        "--verdict-ref verdict_AIPOS-T1_x --focus-items '[\"a\"]' "
        "--acceptance-criteria '[\"b\"]' --confirm --json"
    )
    args = parser.parse_args(shlex.split(cmd)[1:])
    assert args.command == "queue"
    assert args.queue_command == "rework"
    assert args.confirm is True
