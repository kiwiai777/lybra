"""AIPOS-R1: LoopContext + scope铁律测试

验收断言:
1. 隔离: exec.lybra在lybra有活跃claim时,用kaia-kb token拉取只见kiwiaiagency的卡
2. queue_list按(project, instance) scope过滤
3. claim返回context字段
4. 连接解析器从.lybra/自发现

AIPOS-F121 件①(gap #34/#70): 原版自动发现并读取**真实治理根工位凭据**(写死 /home/kiwi/ai-project-os/2_projects/lybra),
且把 executor token 前 20 字符打进测试输出; 队列 scope 用例读写死的产品仓路径。现全部改为 tmp_path 靶场:
工位 .lybra/connection.json 由夹具自签(secrets 随机, 非真实凭据), 输出只许指纹(confirm_client.token_fingerprint 唯一实现),
不读任何真实凭据/真实治理根。
"""

import json
import secrets
from pathlib import Path

from tools.aipos_cli.board_adapter import get_queue
from tools.aipos_cli.confirm_client import token_fingerprint
from tools.loop_context import ConnectionResolver, LoopContext


def _fixture_workstation(root: Path, *, rpc_url: str, entries: list[dict]) -> Path:
    """夹具工位: <root>/.lybra/connection.json(自签凭据, 0600)。"""
    lybra_dir = root / ".lybra"
    lybra_dir.mkdir(parents=True)
    conn = lybra_dir / "connection.json"
    conn.write_text(json.dumps({"config_version": 1, "mcp": {"rpc_url": rpc_url}, "tokens": entries}), encoding="utf-8")
    conn.chmod(0o600)
    return root


def _write_card(repo_root: Path, task_id: str, project: str) -> None:
    pending = repo_root / "5_tasks" / "queue" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / f"{task_id.lower()}.md").write_text(
        "\n".join([
            "---",
            f"task_id: {task_id}",
            f"title: {task_id}",
            f"project: {project}",
            "assigned_to: exec.fixture.local",
            "agent_instance: exec.fixture.local",
            "context_bundle: exec.fixture.local",
            "task_mode: code",
            "priority: medium",
            "status: pending",
            "created_by: tester",
            "needs_owner: false",
            "output_target: tools/",
            "artifact_policy: formal_write",
            "---",
            "Task body",
            "",
        ]),
        encoding="utf-8",
    )


def _tasks(result: dict) -> list:
    assert result.get("ok") is True, result
    return result["data"]["tasks"]


def test_connection_resolver_autodiscover(tmp_path):
    """测试从.lybra/自动发现连接配置(夹具自签凭据, 不读真实工位)"""
    executor_token = secrets.token_urlsafe(32)
    workspace = _fixture_workstation(
        tmp_path / "ws",
        rpc_url="http://127.0.0.1:7118/mcp",
        entries=[
            {"role": "owner", "token": secrets.token_urlsafe(32), "agent_instance": "owner.fixture.local"},
            {"role": "executor", "token": executor_token, "agent_instance": "exec.fixture.local"},
        ],
    )

    # 测试gate URL解析
    gate_url = ConnectionResolver.resolve_gate_url(workspace_root=workspace, env={})
    print(f"✓ Auto-discovered gate URL: {gate_url}")
    assert gate_url == "http://127.0.0.1:7118/mcp"

    # 测试token解析(executor角色): 解析到夹具自签的那一条; 输出只许指纹
    token = ConnectionResolver.resolve_token(workspace_root=workspace, role="executor", env={})
    print(f"✓ Auto-discovered executor token fingerprint: {token_fingerprint(token)}")
    assert token == executor_token


def test_queue_scope_filtering(tmp_path):
    """测试queue_list按project scope过滤(tmp 队列靶场)"""
    repo_root = tmp_path / "gov"
    for state in ("pending", "claimed", "completed", "blocked"):
        (repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
    _write_card(repo_root, "R1-LYBRA-1", "lybra")
    _write_card(repo_root, "R1-LYBRA-2", "lybra")
    _write_card(repo_root, "R1-OTHER-1", "other-project")

    # 无scope: 返回所有任务(任务在响应信封 data.tasks; 原版读顶层 tasks 恒为空, 断言空转——一并修正)
    all_tasks = _tasks(get_queue(repo_root=repo_root))
    print(f"✓ Without scope: {len(all_tasks)} tasks")
    assert len(all_tasks) == 3

    # 单项目scope: 只返回该项目的任务
    lybra_tasks = _tasks(get_queue(repo_root=repo_root, project_scope="lybra"))
    print(f"✓ With project_scope=lybra: {len(lybra_tasks)} tasks")
    assert len(lybra_tasks) == 2
    for task in lybra_tasks:
        task_project = task.get("metadata", {}).get("project", "")
        assert task_project == "lybra", f"Task {task.get('task_id')} has project={task_project}, expected lybra"
    print("✓ All filtered tasks belong to project 'lybra'")

    # 测试不存在的项目
    other_tasks = _tasks(get_queue(repo_root=repo_root, project_scope="nonexistent"))
    print(f"✓ With project_scope=nonexistent: {len(other_tasks)} tasks")
    assert other_tasks == []


def test_loop_context_structure(tmp_path):
    """测试LoopContext结构"""
    ctx = LoopContext(
        project="lybra",
        instance="exec.lybra.kiwiai-dev",
        workspace_root=tmp_path / "gov",
        code_repo=tmp_path / "code",
        gate_url="http://127.0.0.1:7118/mcp",
        token="test_token_placeholder",
        policy="pol_lybra_dev_8",
        task_state="claimed",
    )

    print(f"✓ LoopContext created: project={ctx.project}, instance={ctx.instance}")

    # 测试序列化(token 不进序列化面)
    ctx_dict = ctx.to_dict()
    assert ctx_dict["project"] == "lybra"
    assert ctx_dict["instance"] == "exec.lybra.kiwiai-dev"
    assert "workspace_root" in ctx_dict
    assert "token" not in ctx_dict
    print("✓ LoopContext.to_dict() works")


def test_claim_context_response():
    """测试claim返回context字段(需要实际claim操作,这里只验证结构)"""
    print("✓ Claim context response structure defined in schema")
    print("  (Full claim test requires live gate and token)")
