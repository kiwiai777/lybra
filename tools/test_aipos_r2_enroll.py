#!/usr/bin/env python3
"""AIPOS-R2 integration test — 完整 enroll 流程活体验收(临时门 + 临时治理根靶场)。

验收断言(任务卡):
1. dev 上对一个测试角色跑一条 enroll → 该角色用自发现(不 source 任何脚本)跑通一次 gate 读操作(如 queue_list, 只见本项目);
2. 跨机(Mac)对一个测试角色 enroll → Mac 侧配置落位、同样自发现跑通(用测试角色,不动 kaia-* 业务角色);
3. token 明文不出现在命令输出/聊天/治理文本;
4. agency 现有角色零回归(不动它们的 tokens)。

本测试覆盖断言 1、3、4(断言 2 需要跨机手工验证)。

AIPOS-F121 件①(gap #34/#70): 原版经**生产门 127.0.0.1:7118** 发码兑换, 读**真实治理根** .lybra/connection.json 取 bootstrap
凭据, 每跑一次往真实 governance/enrollment_log.md 写一组 test.enroll.aipos-r2 的 create/use/land(至 10-06 已 238 行)。
现改为靶场: tmp home 下一个已建项目(夹具自签 owner 凭据) + 本检出起的临时门子进程(serve-http --home-root <tmp home>,
随机端口), 发码/兑换/落盘/轮换全在靶场内; 原意(发码 → 兑换 → 自发现 → 幂等轮换 → 既有凭据零回归 → 明文不出输出)全保留,
另加: 接入事件落靶场项目 enrollment_log、输出不含任何 token 明文。
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.confirm_client import token_fingerprint  # noqa: E402
from tools.aipos_cli.enroll_client import enroll  # noqa: E402
from tools.aipos_cli.enrollment import TRANSPORT_TOKEN_ROLE, enrollment_trail_path  # noqa: E402
from tools.loop_context import ConnectionResolver  # noqa: E402

INSTANCE = "test.enroll.aipos-r2"
GATE_READY_MARK = "Lybra MCP HTTP/SSE listening on"
GATE_READY_TIMEOUT = 60


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_project(home: Path, gate_url: str) -> tuple[Path, str]:
    """tmp home + 已建项目 proj-r2(队列 + project.json) + 夹具自签 owner 凭据; 返回 (项目根, owner token)。"""
    project = home / "proj-r2"
    for state in ("pending", "claimed", "completed", "blocked"):
        (project / "5_tasks" / "queue" / state).mkdir(parents=True)
    (project / "project.json").write_text(json.dumps({
        "project": "proj-r2", "code_repo": None, "registered_at": "2026-01-01T00:00:00Z",
        "registered_by": "r2-fixture", "config_version": 1}), encoding="utf-8")
    # executor 是工位类角色: enroll 须从项目生效 PreAuthorized 信封推导 owner_policy_ref(与 tests/test_aipos_f54.py 夹具同形)
    (project / "5_tasks" / "policies").mkdir(parents=True)
    (project / "5_tasks" / "policies" / "pol_r2_exec_1.md").write_text(
        "---\nrecord_type: owner_autonomy_policy\npolicy_id: pol_r2_exec_1\nmode: PreAuthorized\nstatus: active\n"
        "approved_by_owner: true\nowner_approval_ref: dec_r2_fixture\nactive_from: '2020-01-01T00:00:00Z'\n"
        "expires_at: '2099-01-01T00:00:00Z'\nagent_or_role: executor\ntask_selector_task_mode: ''\n"
        "task_selector_project: proj-r2\ntask_selector_task_ids: []\nmax_tasks: 50\n---\n"
        "# Owner Autonomy Policy: pol_r2_exec_1\n", encoding="utf-8")
    owner_token = secrets.token_urlsafe(32)
    (project / ".lybra").mkdir()
    conn = project / ".lybra" / "connection.json"
    conn.write_text(json.dumps({
        "config_version": 1,
        "workspace_root": str(project),
        "mcp": {"rpc_url": f"{gate_url}/mcp"},
        "tokens": [{
            "role": "owner", "token": owner_token, "token_ref": "svc-owner",
            "scopes": ["queue_claim", "queue_return", "owner_confirm", "draft_publish", "owner_decision_record",
                       "queue_amend", "queue_withdraw"],
            "fingerprint": token_fingerprint(owner_token), "agent_instance": "owner.r2.fixture",
        }],
    }, indent=2), encoding="utf-8")
    conn.chmod(0o600)
    return project, owner_token


def _start_gate(home: Path, port: int, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    """本检出起临时门(--home-root 靶场 home)。就绪 = 门在 bind 之后打印的 listening 行(同步读, 不轮询)。"""
    gate = subprocess.Popen(
        [sys.executable, "-m", "tools.mcp_server", "serve-http", "--host", "127.0.0.1", "--port", str(port),
         "--home-root", str(home)],
        cwd=str(REPO_ROOT), env=env, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    seen: list[str] = []
    for line in gate.stdout:
        seen.append(line)
        if GATE_READY_MARK in line:
            break
        if len(seen) > 200:
            break
    log_path.write_text("".join(seen), encoding="utf-8")
    if not any(GATE_READY_MARK in line for line in seen):
        _stop_gate(gate)
        raise AssertionError(f"临时门未就绪(exit={gate.poll()}):\n{''.join(seen)[-2000:]}")

    def _drain() -> None:  # 就绪后持续把门输出抽到日志文件(防管道写满阻塞门)
        with log_path.open("a", encoding="utf-8") as fh:
            for rest in gate.stdout:
                fh.write(rest)

    threading.Thread(target=_drain, daemon=True).start()
    return gate


def _stop_gate(gate: subprocess.Popen) -> None:
    try:
        os.killpg(gate.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        gate.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(gate.pid, signal.SIGKILL)
        gate.wait(timeout=10)


def _run_cli_json(project: Path, env: dict[str, str], *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(project), *args, "--json"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"CLI {args[:2]} 失败(exit={proc.returncode}):\n{proc.stderr[-1500:]}"
    return json.loads(proc.stdout)


def _assert_no_secret(text: str, secrets_: list[str], where: str) -> None:
    for value in secrets_:
        assert value not in text, f"{where} 含 token 明文(指纹 {token_fingerprint(value)})"


def test_enroll_flow(tmp_path, monkeypatch):
    """测试完整 enroll 流程(靶场: 临时门 + 临时治理根; 不触生产门/真实治理根/真实工位)。"""
    print("=" * 70)
    print("AIPOS-R2 Enroll Integration Test (临时门靶场)")
    print("=" * 70)

    home = tmp_path / "home"
    fake_user_home = tmp_path / "user-home"
    fake_user_home.mkdir()
    port = _free_port()
    gate_url = f"http://127.0.0.1:{port}"
    project, owner_token = _seed_project(home, gate_url)
    owner_conn = project / ".lybra" / "connection.json"
    entries_before = json.loads(owner_conn.read_text(encoding="utf-8"))["tokens"]

    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"PYTHONPATH": str(REPO_ROOT), "HOME": str(fake_user_home), "LYBRA_HOME_ROOT": str(home)})
    # 本进程内的 enroll / ConnectionResolver 同样只见靶场
    monkeypatch.setenv("HOME", str(fake_user_home))
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    monkeypatch.delenv("LYBRA_GATE_URL", raising=False)

    gate = _start_gate(home, port, env, tmp_path / "gate.log")
    try:
        test_workspace = tmp_path / "test_workspace"
        test_workspace.mkdir()
        print(f"\n[Setup] 靶场 home: {home}  项目: {project}  临时门: {gate_url}")

        # Step 1: 生成 enrollment code(CLI 薄壳 → 临时门动词两阶段)
        print("\n[1] Generating enrollment code for test role...")
        result = _run_cli_json(project, env, "roles", "enroll-code", "--role", "executor", "--instance", INSTANCE,
                               "--ttl", "3600", "--gate-url", gate_url,
                               "--owner-authorization-ref", "AIPOS-R2-integration-test", "--reason", "AIPOS-R2 integration test")
        assert result.get("ok"), result
        code = result["self_contained_code"]
        print(f"✓ Generated: {result['code_id']}")
        print(f"  Role: {result['role']}, Instance: {result.get('instance')}")
        print(f"  Code fingerprint: {result['fingerprint']}")
        print(f"  Governance root: {result.get('governance_root')}")
        assert result.get("governance_root") == str(project)
        # 验证: token 不在输出中
        assert "transport_token" not in result and "token" not in result, "Raw token leaked in enrollment code generation output"
        print("✓ Security check: No raw token field in output (fingerprints only; 自包含码本身单次展示)")

        # Step 2: 使用 enrollment code 进行 enroll
        print("\n[2] Enrolling with code at test workspace...")
        enroll_result = enroll(code=code, gate_url=gate_url, workspace_root=test_workspace, policy=None)
        assert enroll_result.get("ok"), "Enroll returned ok=False"
        print("✓ Enroll successful")
        print(f"  Role: {enroll_result['role']}  Instance: {enroll_result.get('agent_instance', '(none)')}")
        print(f"  Fingerprint: {enroll_result['fingerprint']}  Rotated: {enroll_result['rotated']}")
        print(f"  Files written: {', '.join(enroll_result['files_written'])}")

        # Step 3: 验证 .lybra/ 配置落位
        print("\n[3] Verifying .lybra/ configuration...")
        connection_file = test_workspace / ".lybra" / "connection.json"
        assert connection_file.is_file(), "connection.json not created"
        assert stat.S_IMODE(connection_file.stat().st_mode) == 0o600, "connection.json permissions != 0600"
        connection_data = json.loads(connection_file.read_text(encoding="utf-8"))
        tokens = connection_data["tokens"]
        assert len(tokens) == 1, f"Expected 1 token, got {len(tokens)}"
        token_entry = tokens[0]
        assert token_entry.get("role") == "executor"
        assert token_entry.get("agent_instance") == INSTANCE
        assert len(token_entry.get("token") or "") >= 10, "Token value missing or invalid"
        print(f"✓ connection.json (0600) contains valid token entry, fingerprint {token_fingerprint(token_entry['token'])}")
        role_data = json.loads((test_workspace / ".lybra" / "role").read_text(encoding="utf-8"))
        assert role_data.get("role") == "executor" and role_data.get("instance") == INSTANCE, role_data
        written = set(enroll_result.get("files_written") or [])
        # 文件集随产品演进: role 项现带信封推导标注「role(含 owner_policy_ref)」(F54), 另有工位接线项; 原断言写死 "role" 已过期
        assert "connection.json" in written and any(item.startswith("role") for item in written), sorted(written)
        assert "owner_policy_ref" in role_data and role_data["owner_policy_ref"] == "pol_r2_exec_1", role_data
        print(f"✓ role file (JSON, owner_policy_ref={role_data['owner_policy_ref']}) + files_written = {sorted(written)}")

        # Step 4: 自发现(ConnectionResolver)
        print("\n[4] Testing auto-discovery with ConnectionResolver...")
        discovered_token = ConnectionResolver.resolve_token(workspace_root=test_workspace, role="executor",
                                                            agent_instance=INSTANCE, env={})
        assert discovered_token == token_entry["token"], "Discovered token mismatch"
        discovered_url = ConnectionResolver.resolve_gate_url(workspace_root=test_workspace, env={})
        assert discovered_url == f"{gate_url}/mcp", f"Discovered gate URL mismatch: {discovered_url}"
        print("✓ ConnectionResolver auto-discovered token + gate URL")

        # Step 5: 幂等性(重复 enroll 同 instance 应轮换 token)
        print("\n[5] Testing idempotency (re-enrolling same instance)...")
        result2 = _run_cli_json(project, env, "roles", "enroll-code", "--role", "executor", "--instance", INSTANCE,
                                "--ttl", "3600", "--gate-url", gate_url,
                                "--owner-authorization-ref", "AIPOS-R2-idempotency-test", "--reason", "Test token rotation")
        assert result2.get("ok"), result2
        old_token = token_entry["token"]
        enroll_result2 = enroll(code=result2["self_contained_code"], gate_url=gate_url, workspace_root=test_workspace, policy=None)
        assert enroll_result2.get("rotated"), "Expected rotated=True for second enroll"
        connection_data2 = json.loads(connection_file.read_text(encoding="utf-8"))
        # AIPOS-F59 起轮换语义 = 旧条目留痕退场(retired, 不删) + 新条目追加; 原断言「仍只 1 条」已过期
        live = [t for t in connection_data2["tokens"] if not t.get("retired")]
        retired = [t for t in connection_data2["tokens"] if t.get("retired")]
        assert len(live) == 1 and len(retired) == 1, [{k: v for k, v in t.items() if k != "token"} for t in connection_data2["tokens"]]
        new_token = live[0]["token"]
        assert new_token != old_token and retired[0]["token"] == old_token, "Token not rotated"
        assert ConnectionResolver.resolve_token(workspace_root=test_workspace, role="executor",
                                                agent_instance=INSTANCE, env={}) == new_token, "自发现未选中新凭据"
        print(f"✓ Token rotated: {token_fingerprint(old_token)} → {token_fingerprint(new_token)}")

        # Step 6: 既有凭据零回归(断言 4): 靶场项目既有凭据条目逐字段不变(兑换会往发码治理根登记新铸 executor 条目——门注册表,
        # 属设计; 零回归判据 = 既有条目不改不退场)
        print("\n[6] Verifying existing workspace tokens unchanged...")
        entries_after = json.loads(owner_conn.read_text(encoding="utf-8"))["tokens"]
        for entry in entries_before:
            assert entry in entries_after, f"既有凭据条目被改动/移除: {token_fingerprint(entry['token'])}"
        added = [t for t in entries_after if t not in entries_before]
        assert all(t.get("role") == TRANSPORT_TOKEN_ROLE or t.get("agent_instance") == INSTANCE for t in added), \
            [(t.get("role"), t.get("agent_instance")) for t in added]
        print(f"✓ 既有 {len(entries_before)} 条凭据不变; 门注册表新增 {len(added)} 条均为本测试发码的运输凭证/测试实例")

        # Step 7(F121): 接入事件落靶场项目 enrollment_log; 输出/日志不含 token 明文
        print("\n[7] Verifying enrollment trail lands in the fixture project...")
        trail = enrollment_trail_path(project)
        trail_text = trail.read_text(encoding="utf-8")
        actions = [ln.split()[2] for ln in trail_text.splitlines() if ln.startswith("- ") and f"instance={INSTANCE}" in ln]
        print(f"✓ {trail} 事件: {actions}")
        assert actions.count("create") == 2 and actions.count("use") == 2 and actions.count("land") == 2, actions
        all_secrets = [owner_token, old_token, new_token]
        _assert_no_secret(trail_text, all_secrets, "enrollment_log")
        _assert_no_secret(json.dumps({k: v for k, v in result.items() if k not in ("self_contained_code", "paste_text")}),
                          all_secrets, "enroll-code 输出")
    finally:
        _stop_gate(gate)

    print("\n" + "=" * 70)
    print("✓ All tests passed!")
    print("=" * 70)
    print("  [✓] 1. enroll + 自发现配置落位(临时门)")
    print("  [⊗] 2. 跨机 enroll(需手工验证 Mac)")
    print("  [✓] 3. token 明文不泄露")
    print("  [✓] 4. 现有角色零回归")
    print("  [✓] 5. 幂等性(重复 enroll 轮换 token)")
