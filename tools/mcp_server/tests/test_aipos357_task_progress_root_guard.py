"""AIPOS-357 交付 3b — lybra_task_progress 工作区根护栏测试。

守护/审计 agent 把 blocked_verdict_submit 事件写入【产品仓】5_tasks/records/events/
(错根,应为工作区)的 S10 misdirect 根因之一:lybra_task_progress 若收到产品仓根
的写入意图,应拒并提示。本测试直接 patch _repo_root 为非工作区根(无 5_tasks/queue),
断言 lybra_task_progress 拒写并返回 EVENTS_ROOT_NOT_WORKSPACE。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools.mcp_server.tools import lybra_task_progress


def _executor_token() -> str:
    return json.dumps({
        "role": "executor",
        "operations": ["task_progress"],
        "token_ref": "test-executor",
        "expires_at": "2999-01-01T00:00:00Z",
    })


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    """lybra_task_progress 直调返回 _tool_result 包装; payload 在 structuredContent。"""
    return result.get("structuredContent", {})


class TaskProgressRootGuardTests(unittest.TestCase):
    """交付 3b: lybra_task_progress 工作区根护栏(拒产品仓根写入)。"""

    def test_rejects_non_workspace_root(self) -> None:
        bogus_root = Path(tempfile.mkdtemp(prefix="aipos357_product_"))
        self.assertFalse((bogus_root / "5_tasks" / "queue").exists())

        with patch.dict(os.environ, {"LYBRA_CAPABILITY_TOKEN": _executor_token()}), \
             patch("tools.mcp_server.tools._repo_root", return_value=bogus_root):
            result = lybra_task_progress({
                "task_id": "AIPOS-357",
                "event_type": "blocked",
                "actor": "audit.lybra.kiwiai-dev",
                "reason": "verdict submit failed (guard test)",
            })

        sc = _payload(result)
        self.assertFalse(sc.get("ok"), "非工作区根写入应被拒")
        self.assertEqual(sc.get("error_code"), "EVENTS_ROOT_NOT_WORKSPACE")
        msg = str(sc.get("message", ""))
        self.assertIn("workspace", msg.lower())
        # 护栏生效: 产品仓根不应有事件落盘
        stray = list((bogus_root / "5_tasks" / "records" / "events").rglob("*.md"))
        self.assertEqual(stray, [], "护栏生效: 产品仓根不应有事件落盘")

    def test_allows_workspace_root(self) -> None:
        """回归: 正常工作区根(有 5_tasks/queue)→ 正常落盘,护栏不误伤。"""
        ws = Path(tempfile.mkdtemp(prefix="aipos357_ws_"))
        (ws / "5_tasks" / "queue" / "pending").mkdir(parents=True)

        with patch.dict(os.environ, {"LYBRA_CAPABILITY_TOKEN": _executor_token()}), \
             patch("tools.mcp_server.tools._repo_root", return_value=ws):
            result = lybra_task_progress({
                "task_id": "AIPOS-357",
                "event_type": "progress",
                "actor": "exec.lybra.kiwiai-dev",
                "summary": "guard not blocking valid workspace",
            })

        sc = _payload(result)
        self.assertTrue(sc.get("ok"), f"正常工作区根应放行: {sc}")
        evdir = ws / "5_tasks" / "records" / "events" / "AIPOS-357"
        self.assertEqual(len(list(evdir.glob("*.md"))), 1, "事件应正常落盘")


if __name__ == "__main__":
    unittest.main()
