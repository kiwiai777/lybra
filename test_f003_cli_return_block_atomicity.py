#!/usr/bin/env python3
"""
AIPOS-R6A F-003 回归测试: CLI return BLOCK 时必须零副作用
验证: BLOCK verdict 返回时，任务卡文件保持不变

AIPOS-F121 件①: 原版写死真实治理根(/home/kiwi/ai-project-os/2_projects/lybra)、对真实卡跑 PATH 上的 `lybra`(部署树版本),
且找不到卡时 return 2 当 SKIP。现改为 tmp_path 治理根靶场 + 本检出 CLI(python -m tools.aipos_cli.aipos_cli),
零副作用判据从「卡文件哈希」加强为「整个靶场治理根逐文件哈希」。
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

CARD = """---
task_id: AIPOS-R6A
title: AIPOS-R6A F-003 fixture
project: fixture
assigned_to: exec.fixture.local
agent_instance: exec.fixture.local
context_bundle: exec.fixture.local
task_mode: code
priority: medium
status: claimed
created_by: tester
needs_owner: false
output_target: tools/
artifact_policy: formal_write
claimed_by: exec.fixture.local
claim_id: claim_AIPOS-R6A_fixture
---
body
"""


def tree_digest(root: Path) -> dict[str, str]:
    """靶场治理根逐文件 SHA256(相对路径 → 哈希)。"""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_cli_return_block_atomicity(tmp_path):
    """测试CLI return在BLOCK时不修改文件"""
    gov = tmp_path / "gov"
    for state in ("pending", "claimed", "completed", "blocked"):
        (gov / "5_tasks" / "queue" / state).mkdir(parents=True)
    (gov / "5_tasks" / "queue" / "claimed" / "aipos-r6a.md").write_text(CARD, encoding="utf-8")

    before = tree_digest(gov)

    # 尝试用错误的actor执行return (应该BLOCK)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(gov),
         "queue", "return",
         "--task-id", "AIPOS-R6A",
         "--actor", "wrong.actor.test",
         "--agent-instance", "wrong.actor.test",
         "--owner-policy-ref", "pol_test",
         "--result-summary", "test block atomicity"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    print(f"exit={result.returncode}")

    # 返回值非0 且输出含 BLOCK
    assert result.returncode != 0, f"Command succeeded but should have blocked:\n{result.stdout}"
    assert "BLOCK" in result.stdout, f"Output does not contain BLOCK verdict:\n{result.stdout}\n{result.stderr}"

    # 零副作用: 靶场治理根逐文件不变(不增不删不改)
    after = tree_digest(gov)
    assert after == before, f"BLOCK 仍有副作用: before={before} after={after}"
    print("✅ PASS: CLI return BLOCK with zero side effects")
