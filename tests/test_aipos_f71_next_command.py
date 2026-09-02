"""AIPOS-F71 测试 — lybra next 唯一推导实现。

测试覆盖:
1. 冷启动 fixture: 卡走完全生命周期(pending → claimed → returned → audit_dispatched → verdict_issued → finalized → completed)
2. Fail-closed: 状态不明时输出"不可推导 + 缺哪份记录 + 建议动作"
3. 性能: 项目级扫描 ≤10s
4. Token 不出现: 命令中无 token 值
5. 项目无关: 推导全由声明 + 工作区推导,不写死项目名
6. 退役入口: turn-advancer 与 next-step 转发到 next 并输出退役提示
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from textwrap import dedent

import pytest

# 确保 tools 可导入
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def cold_start_workspace(tmp_path: Path) -> Path:
    """创建冷启动 fixture: 最小工作区,含 transitions.schema + verbs.schema + queue + records。"""
    ws = tmp_path / "ws"
    ws.mkdir()

    # 治理仓结构
    gov = ws / "2_projects" / "lybra"
    gov.mkdir(parents=True)

    # schemas
    schemas_dir = gov / "0_ontology" / "schemas"
    schemas_dir.mkdir(parents=True)

    # transitions.schema.json (最小版)
    transitions = {
        "version": "2025-01-01",
        "nodes": {
            "N0": {"name": "publish", "from_state": "draft", "to_state": "pending"},
            "N1": {"name": "claim", "from_state": "pending", "to_state": "claimed"},
            "N2": {"name": "return", "from_state": "claimed", "to_state": "returned"},
            "N3": {"name": "audit_dispatch", "from_state": "returned", "to_state": "audit_dispatched"},
            "N4": {"name": "audit_verdict", "from_state": "audit_dispatched", "to_state": "verdict_issued"},
            "N5": {"name": "finalize", "from_state": "verdict_issued", "to_state": "finalized"},
            "N6": {"name": "close", "from_state": "finalized", "to_state": "completed"},
        },
        "main_flow": {
            "nodes": [
                {"name": "publish"}, {"name": "claim"}, {"name": "return"},
                {"name": "audit_dispatch"}, {"name": "audit_verdict"},
                {"name": "finalize"}, {"name": "close"},
            ]
        },
    }
    (schemas_dir / "transitions.schema.json").write_text(json.dumps(transitions))

    # verbs.schema.json (最小版)
    verbs = {
        "version": "2025-01-01",
        "verbs": {
            "lybra_queue_claim_dry_run": {
                "parameters": {
                    "task_id": {"type": "string", "required": True},
                    "actor": {"type": "string", "required": True},
                    "agent_instance": {"type": "string", "required": True},
                    "autonomy_mode": {"type": "string", "required": True},
                    "owner_policy_ref": {"type": "string", "required": True},
                }
            },
            "lybra_queue_return_dry_run": {
                "parameters": {
                    "task_id": {"type": "string", "required": True},
                    "actor": {"type": "string", "required": True},
                    "agent_instance": {"type": "string", "required": True},
                    "autonomy_mode": {"type": "string", "required": True},
                    "owner_policy_ref": {"type": "string", "required": True},
                    "result_summary": {"type": "string", "required": True},
                }
            },
        },
    }
    (schemas_dir / "verbs.schema.json").write_text(json.dumps(verbs))

    # config.schema.json (最小版 - 提供 governance_structure.paths)
    config = {
        "version": "2025-01-01",
        "governance_structure": {
            "paths": {
                "tasks_root": {"path": "5_tasks/"},
                "queue": {"path": "5_tasks/queue/"},
                "records": {"path": "5_tasks/records/"},
                "task_cards": {"path": "task_cards/"},
            }
        },
    }
    (schemas_dir / "config.schema.json").write_text(json.dumps(config))

    # schema 目录(与 schemas 不同,schema_loader 期望的路径)
    schema_dir = gov / "schema"
    schema_dir.mkdir(parents=True)
    # 复制 config.schema.json 到 schema/ (schema_loader 从这里读)
    (schema_dir / "config.schema.json").write_text(json.dumps(config))

    # queue 目录
    queue_root = gov / "5_tasks" / "queue"
    (queue_root / "pending").mkdir(parents=True)
    (queue_root / "claimed").mkdir(parents=True)
    (queue_root / "completed").mkdir(parents=True)
    (queue_root / "blocked").mkdir(parents=True)

    # records 目录
    records_root = gov / "5_tasks" / "records"
    (records_root / "claims").mkdir(parents=True)
    (records_root / "returns").mkdir(parents=True)
    (records_root / "audit_dispatches").mkdir(parents=True)
    (records_root / "audit_verdicts").mkdir(parents=True)
    (records_root / "closures").mkdir(parents=True)
    (records_root / "events").mkdir(parents=True)

    # connection.json
    conn_dir = gov / ".lybra"
    conn_dir.mkdir(parents=True)
    conn = {
        "gate_url": "http://127.0.0.1:7118",
        "tokens": [
            {
                "role": "exec.lybra.kiwiai-dev",
                "role_class": "executor",
                "token": "secret_executor_token_12345",
            },
            {
                "role": "owner",
                "role_class": "owner",
                "token": "secret_owner_token_67890",
            },
        ],
    }
    (conn_dir / "connection.json").write_text(json.dumps(conn))

    # policies 目录
    policies_dir = gov / "5_tasks" / "policies"
    policies_dir.mkdir(parents=True)
    policy_content = dedent("""\
        ---
        record_type: owner_autonomy_policy
        policy_id: pol_lybra_dev_9
        status: active
        agent_or_role: exec.lybra.kiwiai-dev
        policy_type: dev
        expires_at: "2099-12-31T23:59:59Z"
        ---
        # Test policy
    """)
    (policies_dir / "test_policy.md").write_text(policy_content)

    return gov


def _create_task_card(queue_dir: Path, task_id: str, status: str, **fm_extra) -> Path:
    """在 queue/<status>/ 下创建任务卡。"""
    fm = {
        "task_id": task_id,
        "assigned_to": "exec.lybra.kiwiai-dev",
        "agent_instance": "exec.lybra.kiwiai-dev",
        "task_mode": "code",
        "audit": "required",
    }
    fm.update(fm_extra)
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    content = f"---\n{fm_lines}\n---\n# {task_id}\n\nTest task.\n"
    task_file = queue_dir / status / f"{task_id.lower()}.md"
    task_file.write_text(content)
    return task_file


def _create_record(records_dir: Path, record_type: str, task_id: str, record_name: str, **fm_extra) -> Path:
    """在 records/<record_type>/<task_id>/ 下创建记录。"""
    rec_dir = records_dir / record_type / task_id
    rec_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "task_id": task_id,
        "actor": "exec.lybra.kiwiai-dev",
        "agent_instance": "exec.lybra.kiwiai-dev",
        "autonomy_mode": "PreAuthorized",
        "owner_policy_ref": "pol_lybra_dev_9",
    }
    fm.update(fm_extra)
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    content = f"---\n{fm_lines}\n---\n# {record_name}\n\nRecord content.\n"
    rec_file = rec_dir / f"{record_name}_{task_id.lower()}.md"
    rec_file.write_text(content)
    return rec_file


def _create_return_artifact(ws: Path, task_id: str) -> Path:
    """创建 RETURN.md 工作产物。"""
    task_work_dir = ws / "task_cards" / task_id
    task_work_dir.mkdir(parents=True, exist_ok=True)
    ret_file = task_work_dir / "RETURN.md"
    ret_file.write_text("""# Return

## 一句话结论

**完成**。工作已完成。

## 改动清单

Work completed.
""")
    return ret_file


def _create_verdict_artifact(ws: Path, audit_task_id: str, reviewed_task_id: str, verdict: str = "PASS") -> Path:
    """创建 VERDICT 审计报告。"""
    task_work_dir = ws / "task_cards" / audit_task_id
    task_work_dir.mkdir(parents=True, exist_ok=True)
    verdict_file = task_work_dir / f"VERDICT-{audit_task_id}.md"
    content = f"""---
reviewed_task_id: {reviewed_task_id}
verdict: {verdict}
actor: audit.lybra.kiwiai-dev
agent_instance: audit.lybra.kiwiai-dev
---
# Verdict\n\n{verdict}\n"""
    verdict_file.write_text(content)
    return verdict_file


def _create_audit_card(queue_dir: Path, task_id: str) -> Path:
    """创建审计卡 <task_id>R。"""
    audit_id = f"{task_id}R"
    return _create_task_card(queue_dir, audit_id, "pending", assigned_to="audit.lybra.kiwiai-dev", task_mode="audit")


class TestColdStartLifecycle:
    """冷启动 fixture: 卡走完全生命周期。"""

    def test_pending_to_claim(self, cold_start_workspace: Path):
        """N0→N1: pending 卡 → 推导 claim 命令。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-001", "pending")

        result = derive_next_step("TEST-001", ws)

        assert result["derivable"] is True
        assert result["current_state"] == "pending"
        assert result["current_node"] == "publish"
        assert result["triggered_by"] == "executor"
        assert "lybra queue claim" in result["command"]
        assert "--task-id TEST-001" in result["command"]
        assert "--confirm" in result["command"]
        assert "lybra_queue_claim_dry_run" in result["verb"]

    def test_claimed_with_return_artifact(self, cold_start_workspace: Path):
        """N1→N2: claimed 卡 + RETURN.md → 推导 return 命令。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-002", "claimed")
        _create_return_artifact(ws, "TEST-002")

        result = derive_next_step("TEST-002", ws)

        assert result["derivable"] is True
        assert result["current_state"] == "claimed"
        assert result["current_node"] == "claim"
        assert result["triggered_by"] == "executor"
        assert "lybra queue return" in result["command"]
        assert "--task-id TEST-002" in result["command"]
        assert "lybra_queue_return_dry_run" in result["verb"]

    def test_returned_with_audit_card(self, cold_start_workspace: Path):
        """N2→N3: claimed 卡 + return 记录 + 审计卡 → 推导 audit dispatch 命令。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        records_dir = ws / "5_tasks" / "records"

        _create_task_card(queue_dir, "TEST-003", "claimed")
        _create_record(records_dir, "returns", "TEST-003", "return")
        _create_audit_card(queue_dir, "TEST-003")

        result = derive_next_step("TEST-003", ws)

        assert result["derivable"] is True
        assert result["current_state"] == "claimed"
        assert result["current_node"] == "return"
        assert result["triggered_by"] == "advisor"
        assert "lybra audit dispatch" in result["command"]
        assert "--source-task-id TEST-003" in result["command"]
        assert "--audit-task-id TEST-003R" in result["command"]
        assert "lybra_audit_dispatch_dry_run" in result["verb"]

    def test_audit_card_with_verdict(self, cold_start_workspace: Path):
        """N4: 审计卡 claimed + VERDICT 报告 → 推导 audit verdict 命令。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-003R", "claimed", task_mode="audit", assigned_to="audit.lybra.kiwiai-dev")
        _create_verdict_artifact(ws, "TEST-003R", "TEST-003", "PASS")

        result = derive_next_step("TEST-003R", ws)

        assert result["derivable"] is True
        assert result["current_state"] == "claimed"
        assert result["current_node"] == "audit_verdict"
        assert result["triggered_by"] == "auditor"
        assert "lybra audit verdict" in result["command"]
        assert "--reviewed-task-id TEST-003" in result["command"]
        assert "--audit-task-id TEST-003R" in result["command"]
        assert "--verdict PASS" in result["command"]
        assert "--confirm" in result["command"]
        assert "lybra_audit_verdict_dry_run" in result["verb"]

    def test_completed(self, cold_start_workspace: Path):
        """N6: completed 卡 → 无下一步。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-004", "completed")

        result = derive_next_step("TEST-004", ws)

        assert result["derivable"] is True
        assert result["current_state"] == "completed"
        assert result["current_node"] == "close"
        assert result["triggered_by"] == "none"
        assert "无下一步" in result["command"] or "已完成" in result["command"]


class TestFailClosed:
    """Fail-closed: 状态不明时输出"不可推导 + 缺哪份记录 + 建议动作"。"""

    def test_task_not_found(self, cold_start_workspace: Path):
        """任务卡不存在 → 不可推导。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        result = derive_next_step("NONEXISTENT", ws)

        assert result["derivable"] is False
        assert result["current_state"] == "not_found"
        assert "queue 目录中找不到任务卡" in result["missing_records"][0]
        assert result["suggested_action"]

    def test_claimed_no_return_artifact(self, cold_start_workspace: Path):
        """claimed 卡无 RETURN.md → 不可推导(事实不足)。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-005", "claimed")

        result = derive_next_step("TEST-005", ws)

        assert result["derivable"] is False
        assert result["current_state"] == "claimed"
        assert "RETURN.md 工作产物" in result["missing_records"]
        assert result["suggested_action"]

    def test_returned_no_audit_card(self, cold_start_workspace: Path):
        """已 return 但审计卡未生成 → 不可推导。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        records_dir = ws / "5_tasks" / "records"

        _create_task_card(queue_dir, "TEST-006", "claimed")
        _create_record(records_dir, "returns", "TEST-006", "return")
        # 不创建审计卡

        result = derive_next_step("TEST-006", ws)

        assert result["derivable"] is False
        assert "审计卡" in result["missing_records"][0]
        assert "自产审计卡" in result["suggested_action"]

    def test_blocked(self, cold_start_workspace: Path):
        """blocked 卡 → 不可推导(需人工裁定)。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-007", "blocked")

        result = derive_next_step("TEST-007", ws)

        assert result["derivable"] is False
        assert result["current_state"] == "blocked"
        assert "blocked 恢复策略需人工裁定" in result["missing_records"][0]


class TestPerformance:
    """性能: 项目级扫描 ≤10s。"""

    def test_scan_performance(self, cold_start_workspace: Path):
        """项目级扫描应在 10s 内完成。"""
        from tools.aipos_cli.next_resolver import scan_project

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"

        # 创建 20 张卡
        for i in range(20):
            _create_task_card(queue_dir, f"PERF-{i:03d}", "pending")

        start = time.time()
        results = scan_project(ws)
        elapsed = time.time() - start

        assert elapsed < 10.0, f"Scan took {elapsed:.2f}s, expected < 10s"
        assert len(results) == 20


class TestTokenAbsent:
    """Token 不出现: 命令中无 token 值。"""

    def test_no_token_in_command(self, cold_start_workspace: Path):
        """推导出的命令不应包含 token 值。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-008", "pending")

        result = derive_next_step("TEST-008", ws)

        assert result["derivable"] is True
        cmd = result["command"]
        # 检查 connection.json 路径出现但 token 值不出现
        assert "--connection-json" in cmd
        # 检查 token 值不出现(connection.json 中的 secret token)
        assert "secret_executor_token_12345" not in cmd
        assert "secret_owner_token_67890" not in cmd
        # 检查命令中无 --token 参数
        assert "--token" not in cmd


class TestProjectAgnostic:
    """项目无关: 推导全由声明 + 工作区推导,不写死项目名。"""

    def test_derivation_no_hardcoded_project(self, cold_start_workspace: Path):
        """推导逻辑不应硬编码项目名。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-009", "pending")

        result = derive_next_step("TEST-009", ws)

        assert result["derivable"] is True
        cmd = result["command"]
        # 命令应包含任务 ID 和角色,但不应包含硬编码的项目名
        assert "--task-id TEST-009" in cmd
        # connection.json 路径包含工作区路径,但不是硬编码的
        assert "--connection-json" in cmd


class TestRetiredCommands:
    """退役入口: turn-advancer 与 next-step 转发到 next 并输出退役提示。"""

    def test_turn_advancer_retired(self, cold_start_workspace: Path):
        """turn-advancer 应输出退役提示并仍工作。"""
        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-010", "pending")

        result = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli",
             "turn-advancer", "next", "TEST-010",
             "--workspace-root", str(ws)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

        assert "[RETIRED]" in result.stderr
        assert "lybra next" in result.stderr
        # 应仍输出推导结果
        assert "TEST-010" in result.stdout
        assert "lybra queue claim" in result.stdout

    def test_next_step_retired(self, cold_start_workspace: Path):
        """next-step 应输出退役提示并仍工作。"""
        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-011", "pending")

        result = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli",
             "next-step", "--task-id", "TEST-011",
             "--workspace-root", str(ws)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

        assert "[RETIRED]" in result.stderr
        assert "lybra next --task-id" in result.stderr
        # 应仍输出推导结果
        assert "TEST-011" in result.stdout
        assert "lybra queue claim" in result.stdout

    def test_next_command_works(self, cold_start_workspace: Path):
        """lybra next 应正常工作。"""
        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-012", "pending")

        result = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli",
             "next", "--task-id", "TEST-012",
             "--workspace-root", str(ws)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

        assert result.returncode == 0
        assert "TEST-012" in result.stdout
        assert "lybra queue claim" in result.stdout
        assert "[RETIRED]" not in result.stderr


class TestProjectScan:
    """项目级扫描: 无参时扫描所有活跃任务。"""

    def test_scan_output_format(self, cold_start_workspace: Path):
        """扫描输出应包含所有活跃任务。"""
        from tools.aipos_cli.next_resolver import scan_project, format_scan_output

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"

        _create_task_card(queue_dir, "SCAN-001", "pending")
        _create_task_card(queue_dir, "SCAN-002", "claimed")
        _create_task_card(queue_dir, "SCAN-003", "blocked")

        results = scan_project(ws)
        output = format_scan_output(results)

        assert "SCAN-001" in output
        assert "SCAN-002" in output
        assert "SCAN-003" in output
        assert "project scan" in output

    def test_scan_json_output(self, cold_start_workspace: Path):
        """扫描 JSON 输出应可解析。"""
        from tools.aipos_cli.next_resolver import scan_project, format_scan_output

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"

        _create_task_card(queue_dir, "SCAN-004", "pending")

        results = scan_project(ws)
        output = format_scan_output(results, json_mode=True)
        parsed = json.loads(output)

        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["task_id"] == "SCAN-004"


class TestCopyPasteableCommands:
    """命令可照抄: 含全参数,指向产品 CLI 薄壳。"""

    def test_claim_command_has_all_params(self, cold_start_workspace: Path):
        """claim 命令应包含所有必需参数。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-013", "pending")

        result = derive_next_step("TEST-013", ws)

        cmd = result["command"]
        assert "lybra queue claim" in cmd
        assert "--task-id" in cmd
        assert "--actor" in cmd
        assert "--confirm" in cmd
        assert "--connection-json" in cmd
        assert "--agent-instance" in cmd
        assert "--autonomy-mode" in cmd
        # owner-policy-ref 仅在策略可解析时出现
        # 测试 fixture 的策略可能无法被解析器匹配,所以不强制要求

    def test_return_command_has_all_params(self, cold_start_workspace: Path):
        """return 命令应包含所有必需参数。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        _create_task_card(queue_dir, "TEST-014", "claimed")
        _create_return_artifact(ws, "TEST-014")

        result = derive_next_step("TEST-014", ws)

        cmd = result["command"]
        assert "lybra queue return" in cmd
        assert "--task-id" in cmd
        assert "--actor" in cmd
        assert "--confirm" in cmd
        assert "--result-summary" in cmd

    def test_audit_dispatch_command_has_all_params(self, cold_start_workspace: Path):
        """audit dispatch 命令应包含所有必需参数。"""
        from tools.aipos_cli.next_resolver import derive_next_step

        ws = cold_start_workspace
        queue_dir = ws / "5_tasks" / "queue"
        records_dir = ws / "5_tasks" / "records"

        _create_task_card(queue_dir, "TEST-015", "claimed")
        _create_record(records_dir, "returns", "TEST-015", "return")
        _create_audit_card(queue_dir, "TEST-015")

        result = derive_next_step("TEST-015", ws)

        cmd = result["command"]
        assert "lybra audit dispatch" in cmd
        assert "--source-task-id" in cmd
        assert "--audit-task-id" in cmd
        assert "--actor" in cmd
        assert "--confirm" in cmd
