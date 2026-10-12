"""AIPOS-F157 夹具: 身份与文档收口(gap #114 / #119 / #125 / #129 / #131 / #132)。

各件唯一并入点(禁第二路径):
  件① 身份收口(#132): roles enroll-code / enroll-deliver 门客户端凭据 = aipos_cli._roles_issuer_client → F154 signer_role_for_actor
      (F134 driver_token_entry)+ load_role_token(agent_instance=--actor); 缺 --actor = load_role_token 按角色(同角色 ≥2 实例即拒)。
      audit-verdict 自发现 / 显式参数两路 = audit_helpers.resolve_audit_credential → 同一 load_role_token。按角色取凭据的其余薄壳
      (信封签发 / 门客户端偏好序 / lybra confirm)同改走 load_role_token。#114: 门侧授权判定 custom_roles.authorize_instance_class
      (F148 件③ INSTANCE_NOT_CANONICAL)——本卡只补返工节路径与 MCP 裁决路径的回归原文。
  件② queue amend 普通路径(#132): 门 lybra_queue_amend_confirm 确认令牌口径 = F154 lybra_queue_rework_confirm 同一(只认 dry_run_token,
      execute_dry_run 以令牌计划原始参数重放 board_adapter.amend_task 核快照后写入); 两阶段包装 = two_phase_shell_factory.execute_two_phase_verb。
  件③ 子 agent 等待提示(#125): loop_run_record.run_execution_mode 与 loop_driver.plan_launch 同读 loop_driver.wait_instance_is_subagent
      (→ enrollment.instance_enrollment governance_root_mode); 文档(#129 / #131)以 CLI --help 与 verbs.schema 为准。
  件④ #119 夹具: tests/test_aipos_f118_post_merge_regression.py 残留判定只按本用例标记 + 精确 pid(见该文件 _settled_leftover)。

靶场: F90 rig(临时治理根 + 临时产品仓 + 进程内真门动词); 无真实凭据、不连真实门、不读真实治理根。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + 产品 CLI 靶场(禁第二份)
from test_aipos_f78_engine_agnostic import _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli.token_resolver import token_fingerprint  # noqa: E402

PROJECT = "f157probe"
A1, A2 = f"advisor.{PROJECT}.h1", f"advisor.{PROJECT}.h2"  # 同一角色名 advisor 的两个顾问实例
AU1, AU2 = f"audit.{PROJECT}.h1", f"audit.{PROJECT}.h2"  # 同一角色名 auditor 的两个审计实例
GHOST = f"advisor.{PROJECT}.ghost"  # 未接入本治理根
TOKENS = [
    {"role": "owner", "token": "fixture-owner", "token_ref": "tr-owner", "scopes": []},
    {"role": "advisor", "role_class": "advisor", "agent_instance": A1, "token": "fixture-adv-1", "token_ref": "tr-a1", "scopes": []},
    {"role": "advisor", "role_class": "advisor", "agent_instance": A2, "token": "fixture-adv-2", "token_ref": "tr-a2", "scopes": []},
    {"role": "auditor", "role_class": "auditor", "agent_instance": AU1, "token": "fixture-au-1", "token_ref": "tr-au1", "scopes": []},
    {"role": "auditor", "role_class": "auditor", "agent_instance": AU2, "token": "fixture-au-2", "token_ref": "tr-au2", "scopes": []},
]
CAPS = {t["token"]: {k: t[k] for k in ("role", "role_class", "agent_instance") if k in t} for t in TOKENS}


def _show(text: str) -> None:
    print(text, flush=True)


def _cap(token: str) -> dict:
    """门侧按 bearer 定的能力(TokenGate 与直调门动词同用)。"""
    return {**CAPS[token], "token_ref": f"ref-{token_fingerprint(token)}", "expires_at": "2999-01-01T00:00:00Z", "scopes": ["owner_confirm"]}


def _sc(res: dict) -> dict:
    """门动词应答的结构化内容(isError 应答只有 content 文本)。"""
    if isinstance(res.get("structuredContent"), dict):
        return res["structuredContent"]
    if res.get("content"):
        return json.loads(res["content"][0]["text"])
    return res


class TokenGate(f90.InProcessGate):
    """进程内真门: 能力 = 请求 token 对应的条目(门侧按 bearer 定身份)。calls 记 (门动词, token 绑定实例, 请求 actor)。"""

    calls: list[tuple[str, str, str]] = []

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        assert token in CAPS, "token 只在进程内, 永不上屏"
        self.cap = _cap(token)

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        from tools.mcp_server import tools as gate

        TokenGate.calls.append((name, str(self.cap.get("agent_instance") or f"(角色级 {self.cap.get('role')})"),
                                str(arguments.get("actor") or "")))
        with gate.request_capability_scope(self.cap):
            res = getattr(gate, name)(dict(arguments))
        return _sc(res) if isinstance(res, dict) else res


@pytest.fixture
def two(rig, monkeypatch: pytest.MonkeyPatch):
    """F90 靶场 + 治理根连接文件: Owner 角色级条目 + 同角色 advisor 两实例 + 同角色 auditor 两实例; 未设驱动方实例环境变量。"""
    from tools.aipos_cli import confirm_client, two_phase_shell_factory
    from tools.aipos_cli.two_phase_shell_factory import DRIVER_INSTANCE_ENV

    conn = rig.gov / ".lybra" / "connection.json"
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "workspace_root": str(rig.gov), "tokens": TOKENS}))
    monkeypatch.delenv(DRIVER_INSTANCE_ENV, raising=False)
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(rig.gov.parent))  # 门发码校验治理根 = home 根下已登记项目(靶场 home 根 = 治理根父目录)
    monkeypatch.setattr(two_phase_shell_factory, "GateClient", TokenGate)
    monkeypatch.setattr(confirm_client, "GateClient", TokenGate)
    TokenGate.calls = []
    return SimpleNamespace(**vars(rig), conn=str(conn))


def _cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


# ===========================================================================
# 件① roles enroll-code / enroll-deliver: --actor 按实例取凭据; 同角色两实例缺 --actor 即拒
# ===========================================================================

def _enroll_code_argv(gov: Path, *extra: str) -> list[str]:
    return ["roles", "--workspace-root", str(gov), "enroll-code", "--role", "executor", "--instance", f"exec.{PROJECT}.h9",
            "--governance-root", str(gov), "--owner-authorization-ref", "pol_probe", *extra]


def test_item1_enroll_code_signs_with_actor_instance(two):
    for actor in (A1, A2):
        TokenGate.calls = []
        rc, out, err = _cli(_enroll_code_argv(two.gov, "--actor", actor))
        _show(f"[件①·enroll-code --actor {actor}] exit {rc}\nstdout: {out.strip()}\nstderr: {err.strip()[:300]}\n"
              f"门动词 × (token 绑定实例, 请求 actor): {TokenGate.calls}")
        assert rc == 0, err
        assert TokenGate.calls == [("lybra_enroll_code_dry_run", actor, actor)], TokenGate.calls  # 预演只调 dry_run, 凭据 = 该实例


def test_item1_enroll_code_without_actor_refused_on_same_role_two_instances(two):
    rc, out, err = _cli(_enroll_code_argv(two.gov))
    _show(f"[件①·enroll-code 缺 --actor(advisor 两实例)] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls
    assert "禁取首个冒名" in err and A1 in err and A2 in err and "--actor" in err
    rc, out, err = _cli(_enroll_code_argv(two.gov, "--actor", GHOST))
    _show(f"[件①·enroll-code --actor 未接入 {GHOST}] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls and GHOST in err and "取凭据被拒" in err and "禁回落首个驱动方 token" in err


def test_item1_enroll_code_owner_role_level_unchanged(two):
    """--token-role owner(角色级条目, 无实例绑定)= 既有行为: 按角色取, 不要求 --actor。"""
    rc, out, err = _cli(_enroll_code_argv(two.gov, "--token-role", "owner"))
    _show(f"[件①·enroll-code --token-role owner] exit {rc} 门动词: {TokenGate.calls}")
    assert rc == 0, err
    assert TokenGate.calls == [("lybra_enroll_code_dry_run", "(角色级 owner)", "cli:roles-enroll-code:owner")]


def _deliver_argv(gov: Path, *extra: str) -> list[str]:
    return ["roles", "--workspace-root", str(gov), "enroll-deliver", "--role", "advisor", "--instance", f"advisor.{PROJECT}.h3",
            "--governance-root", str(gov), "--workspace", str(gov), "--owner-authorization-ref", "pol_probe", *extra]


def test_item1_enroll_deliver_same_single_entry(two):
    TokenGate.calls = []
    rc, out, err = _cli(_deliver_argv(two.gov, "--actor", A2))
    _show(f"[件①·enroll-deliver --actor {A2}] exit {rc}\nstdout: {out.strip()[:500]}\nstderr: {err.strip()[:300]}\n门动词: {TokenGate.calls}")
    assert TokenGate.calls and {c[1] for c in TokenGate.calls} == {A2} and TokenGate.calls[0][2] == A2
    TokenGate.calls = []
    rc, out, err = _cli(_deliver_argv(two.gov))
    _show(f"[件①·enroll-deliver 缺 --actor] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls and "禁取首个冒名" in err and "--actor" in err


# ===========================================================================
# 件① 审计自发现 / 显式参数: 按实例取凭据; 同角色两实例缺实例即拒
# ===========================================================================

def test_item1_audit_context_by_instance_and_multi_refused(two):
    from tools.aipos_cli.audit_helpers import resolve_audit_context
    from tools.aipos_cli.two_phase_shell_factory import DriverTokenError

    with pytest.raises(DriverTokenError) as exc:
        resolve_audit_context(workspace_root=two.gov, role="auditor", gate_url="http://127.0.0.1:1")
    _show(f"[件①·审计自发现 按角色 auditor(两实例)] 拒因原文: {exc.value}")
    assert AU1 in str(exc.value) and AU2 in str(exc.value) and "禁取首个冒名" in str(exc.value)
    ctx = resolve_audit_context(workspace_root=two.gov, role="auditor", gate_url="http://127.0.0.1:1", agent_instance=AU2)
    _show(f"[件①·审计自发现 --agent-instance {AU2}] role={ctx['role']} agent_instance={ctx['agent_instance']} "
          f"token={token_fingerprint(ctx['token'])}")
    assert ctx["token"] == "fixture-au-2" and ctx["agent_instance"] == AU2 and ctx["actor"] == AU2
    ctx = resolve_audit_context(workspace_root=two.gov, role=None, gate_url="http://127.0.0.1:1", agent_instance=AU1)
    assert (ctx["role"], ctx["token"], ctx["agent_instance"]) == ("auditor", "fixture-au-1", AU1)  # 未给角色 = 该实例唯一条目的角色
    with pytest.raises(DriverTokenError) as exc:
        resolve_audit_context(workspace_root=two.gov, role="auditor", gate_url="http://127.0.0.1:1", agent_instance=GHOST)
    _show(f"[件①·审计自发现 未接入实例] 拒因原文: {exc.value}")
    assert "禁回落按角色取首个" in str(exc.value)


def test_item1_audit_verdict_cli_signs_with_instance(two):
    base = ["--workspace-root", str(two.gov), "audit-verdict", "--reviewed-task-id", "PROBE-F157-X", "--verdict", "PASS",
            "--findings-summary", "probe"]
    rc, out, err = _cli([*base, "--token-role", "auditor"])
    _show(f"[件①·audit-verdict 自发现 缺实例] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls and "禁取首个冒名" in err
    rc, out, err = _cli([*base, "--actor", AU2])
    _show(f"[件①·audit-verdict --actor {AU2}] exit {rc} 门动词 × (token 绑定实例, 请求 actor): {TokenGate.calls}")
    assert TokenGate.calls and TokenGate.calls[0][0] == "lybra_audit_verdict_dry_run"
    assert TokenGate.calls[0][1:] == (AU2, AU2)  # 凭据 = 该实例, 请求 actor = 显式给出的实例(不被自发现覆盖)


def test_item1_role_based_shells_refuse_multi_instance(two, monkeypatch):
    """其余按角色取凭据的薄壳(门客户端偏好序 / 信封签发)同走 load_role_token: 同角色两实例 = 拒(不换下一个角色掩盖)。"""
    from tools.aipos_cli.confirm_client import load_gate_client_token
    from tools.aipos_cli.two_phase_shell_factory import DriverTokenError

    with pytest.raises(DriverTokenError) as exc:
        load_gate_client_token(two.conn)
    _show(f"[件①·门客户端偏好序 advisor 两实例] 拒因原文: {exc.value}")
    assert "禁取首个冒名" in str(exc.value)
    single = two.gov / "single.json"
    _write(single, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": [TOKENS[0], TOKENS[1]]}))
    assert load_gate_client_token(single) == ("advisor", "fixture-adv-1")  # 单实例: 既有行为
    from tools.aipos_cli.two_phase_shell_factory import DRIVER_INSTANCE_ENV

    monkeypatch.setenv(DRIVER_INSTANCE_ENV, A2)  # 驱动方实例已知 = 按实例取(F134 既有路径)
    assert load_gate_client_token(two.conn) == ("advisor", "fixture-adv-2")


def test_ratchet_role_first_token_bypasses_gone():
    """棘轮: 产品代码(tools/aipos_cli, tools/mcp_server)里 `load_owner_token(` 调用只许在凭据唯一口内部
    (confirm_client 自身定义 / env 取值, two_phase_shell_factory.load_role_token 末段); 各命令薄壳一律经 load_role_token。"""
    call = re.compile(r"\bload_owner_token\(")
    hits: list[str] = []
    for base in ("tools/aipos_cli", "tools/mcp_server"):
        for path in sorted((REPO_ROOT / base).glob("*.py")):
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if call.search(line) and not line.lstrip().startswith(("def ", "#")) and "load_owner_token(`" not in line:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{line.strip()}")
    _show("[棘轮] load_owner_token( 调用点:\n  " + "\n  ".join(hits))
    allowed = {"tools/aipos_cli/two_phase_shell_factory.py", "tools/aipos_cli/confirm_client.py"}
    assert {h.split(":", 1)[0] for h in hits} <= allowed, hits
    cc = [h for h in hits if h.startswith("tools/aipos_cli/confirm_client.py")]
    assert all("token_env=" in h for h in cc), cc  # confirm_client 内只剩 env 取值一处(连接文件取值经 load_role_token)
    src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    body = re.search(r"def _roles_issuer_client\(.*?(?=\ndef )", src, re.S).group(0)
    assert "signer_role_for_actor(" in body and "load_role_token(" in body and "load_owner_token" not in body


# ===========================================================================
# 件① #114 门侧授权须完整实例名(F148 件③ 已收紧): 返工节 / MCP 裁决两路回归原文
# ===========================================================================

def test_item1_gate_bare_role_name_refused_on_rework(two):
    from tools.mcp_server import tools as gate

    card = f90._card(two.gov, "PROBE-F157-RW", "claimed")
    before = card.read_bytes()
    args = {"task_id": "PROBE-F157-RW", "verdict_ref": "verdict_x", "focus_items": ["a"], "acceptance_criteria": "b",
            "workspace_root": str(two.gov)}
    with gate.request_capability_scope(_cap("fixture-adv-1")):
        bare = _sc(gate.lybra_queue_rework_dry_run({**args, "actor": "advisor", "agent_instance": "advisor"}))
        full = _sc(gate.lybra_queue_rework_dry_run({**args, "actor": A1, "agent_instance": A1}))
    _show(f"[件①·#114 返工节 agent_instance=advisor(裸角色名)] verdict={bare.get('verdict')} 拒因原文: {bare.get('blocking_reasons')}")
    assert bare.get("verdict") == "BLOCK" and "INSTANCE_NOT_CANONICAL" in json.dumps(bare, ensure_ascii=False)
    assert bare["data"]["role_resolution"]["reject_code"] == "INSTANCE_NOT_CANONICAL"
    _show(f"[件①·#114 返工节 agent_instance={A1}(完整实例名)] verdict={full.get('verdict')} 拒因: {full.get('blocking_reasons')}")
    assert full.get("verdict") != "BLOCK", full
    assert card.read_bytes() == before  # 预演零写入


# ===========================================================================
# 件② queue amend 普通(非 --restricted)路径: 门侧两阶段 预演 → 凭令牌确认
# ===========================================================================

def test_item2_amend_two_phase_via_wrapper(two):
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb

    card = f90._card(two.gov, "PROBE-F157-AM", "pending")
    args = {"task_id": "PROBE-F157-AM", "actor": A1, "amendments": {"priority": "low"}, "amendment_reason": "F157 普通路径",
            "workspace_root": str(two.gov)}
    code, resp = execute_two_phase_verb(verb_base="lybra_queue_amend", args_dict=args, connection_json_path=two.conn,
                                        role="advisor", json_output=True, signer_instance=A1)
    _show(f"[件②·amend 两阶段包装] exit {code} 门动词 × (token 绑定实例, actor): {TokenGate.calls}\n"
          f"确认应答: {json.dumps({k: resp.get(k) for k in ('ok', 'verdict', 'operation', 'data')}, ensure_ascii=False)}")
    assert code == 0 and [c[0] for c in TokenGate.calls] == ["lybra_queue_amend_dry_run", "lybra_queue_amend_confirm"]
    assert {c[1] for c in TokenGate.calls} == {A1}
    meta, _ = require_frontmatter(card)
    assert meta["priority"] == "low"
    rec = sorted((two.gov / "5_tasks/records/amendments/PROBE-F157-AM").glob("*.md"))
    assert len(rec) == 1 and re.search(rf"^amended_by: {re.escape(A1)}$", rec[0].read_text(encoding="utf-8"), re.M)


def test_item2_amend_confirm_token_contract(two):
    """确认只认本动词预演令牌: 缺令牌 = DRY_RUN_REQUIRED(原: 按入参裸重做); 别的动词令牌 = INCOMPATIBLE_DRY_RUN; 预演后卡被改 = 快照不符拒。"""
    from tools.mcp_server import tools as gate

    card = f90._card(two.gov, "PROBE-F157-AM2", "pending")
    args = {"task_id": "PROBE-F157-AM2", "actor": A1, "amendments": {"priority": "low"}, "amendment_reason": "r",
            "workspace_root": str(two.gov)}

    sc = _sc

    with gate.request_capability_scope(_cap("fixture-adv-1")):
        raw = sc(gate.lybra_queue_amend_confirm(dict(args)))
        _show(f"[件②·confirm 无令牌(旧口径按入参裸重做)] {raw.get('error_code')}: {raw.get('message')}")
        assert raw.get("error_code") == "DRY_RUN_REQUIRED"
        dry = sc(gate.lybra_queue_amend_dry_run(dict(args)))
        token = dry["dry_run_token"]
        other = sc(gate.lybra_queue_rework_confirm({"dry_run_token": token, "actor": A1, "owner_confirmation_token": "OWNER_CONFIRMED"}))
        assert other.get("error_code") == "INCOMPATIBLE_DRY_RUN"
        # 预演后卡被移出 pending(仿他人认领)= 重放预演被拒, 不写
        claimed = two.gov / "5_tasks" / "queue" / "claimed" / card.name
        claimed.parent.mkdir(parents=True, exist_ok=True)
        card.rename(claimed)
        stale = sc(gate.lybra_queue_amend_confirm({"dry_run_token": token, "actor": A1, "owner_confirmation_token": "OWNER_CONFIRMED",
                                                   "workspace_root": str(two.gov)}))
        _show(f"[件②·预演后卡已不在 pending] verdict={stale.get('verdict')} 拒因: {stale.get('blocking_reasons') or stale.get('errors')}")
        assert stale.get("verdict") == "BLOCK" and not list((two.gov / "5_tasks/records/amendments").rglob("*.md"))


# ===========================================================================
# 件③ 子 agent 等待提示: 执行者为子 agent 接入(未声明 execution)时等交回超时不出工位 /go
# ===========================================================================

def test_item3_subagent_enrolled_wait_timeout_no_workstation_go(rig):
    """执行者 EXEC 以子 agent 模式接入(land 事件 mode=subagent, 无工位), project.json 未声明 execution: 等待中提示与等交回超时
    都是子 agent 口径(原: 等待中已是子 agent 提示, 超时却回到 owner_needed「请 Owner 在工位敲 /go」)。"""
    from test_aipos_f143_execution_mode import _land
    from test_aipos_f156_subagent_wait_card_owner import _run_until_wait_timeout
    from test_aipos_f73d_loop_driver import init_governance_repo
    from test_aipos_f78_engine_agnostic import EXEC
    from tools.aipos_cli import loop_run_record as lrr
    from tools.aipos_cli.enrollment import subagent_land_detail
    from tools.aipos_cli.verb_contract import declared_exit_code

    gov = rig.gov
    assert "execution" not in json.loads((gov / "project.json").read_text(encoding="utf-8"))
    _land(gov, EXEC, "executor", subagent_land_detail(host="advhost", harness="claude-code", mode="subagent", files=["connection.json"]))
    f90._card(gov, f90.TASK, "pending")
    init_governance_repo(gov)
    seen: dict = {}
    res = _run_until_wait_timeout(gov, seen)
    assert res.outcome == "wait_timeout", res.message
    rc, out, err = seen["kickoff"]
    _show(f"---- 件③·子 agent 接入(未声明 execution)等待中 lybra loop status 原文 exit {rc} ----\n{out}{err}")
    assert "continue_wait(manual_kickoff)" in out and "派子 agent" in out and "会话敲 /go" not in out and "在工位敲 /go" not in out
    rc, out, err = _cli(["loop", "status", "--task-id", f90.TASK, "--workspace-root", str(gov), "--wait", "5"])
    _show(f"---- 件③·子 agent 接入等交回超时 lybra loop status --wait 原文 exit {rc} ----\n{out}{err}")
    assert rc == declared_exit_code("lybra_loop_status", "advisor_fix"), out + err
    assert "advisor_fix(wait_timeout)" in out and "不找 Owner" in out and "在工位敲 /go" not in out and "会话敲 /go" not in out
    assert lrr.run_execution_mode(gov, {"task_id": f90.TASK, "wait_card": f90.TASK}) == ("subagent", "")


def test_item3_wait_prompt_exits_single_source():
    """等待 / 开工提示只在 F156 执行方式分流处出: manual_hint(工位 /go 三形 + subagent)与 owner_reasons.wait_timeout 只在 verbs.schema 声明;
    产品代码不另写「敲 /go」等待文案; plan_launch 与 run_execution_mode 同读 wait_instance_is_subagent / instance_enrollment。"""
    from tools.schema_loader import load_schema

    verbs = load_schema("verbs")
    hint = verbs["verbs"]["lybra_loop"]["launch"]["manual_hint"]
    assert "敲 /go" not in hint["subagent"] and "派子 agent" in hint["subagent"]
    hits = []
    for path in sorted((REPO_ROOT / "tools/aipos_cli").glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if "敲 /go" in stripped and not stripped.startswith("#") and '"""' not in stripped:
                hits.append(f"{path.name}: {stripped[:160]}")
    _show("[件③·产品代码中含「敲 /go」的非注释行]\n  " + "\n  ".join(hits))
    # 只剩 CLI 帮助 / 模块说明 / 接入向导(pi 工位步骤); 顾问下一动作与等待判定所在模块不另写等待文案(只读 verbs.schema 声明)
    assert all(h.startswith(("aipos_cli.py:", "onboarding.py:", "loop_driver.py:")) for h in hits), hits
    assert not any(h.startswith(("loop_run_record.py:", "next_resolver.py:", "brief.py:")) for h in hits), hits
    src = (REPO_ROOT / "tools/aipos_cli/loop_run_record.py").read_text(encoding="utf-8")
    assert "wait_instance_is_subagent(" in src


# ===========================================================================
# 件③ 文档: SKILL / agents 与 CLI --help / verbs.schema 对齐
# ===========================================================================

def test_item3_docs_enroll_deliver_and_advisor_commands():
    pattern = r"roles enroll --code|--code '|--code \"|--code <|enroll --code"
    hits = subprocess.run(["git", "grep", "-nE", pattern, "--", "agents"], cwd=REPO_ROOT, capture_output=True, text=True)
    _show(f"[件③·agents/ git grep -nE {pattern!r}] rc={hits.returncode} 命中={hits.stdout.strip() or 0}")
    assert hits.returncode == 1 and hits.stdout == ""
    skill = (REPO_ROOT / "agents/skills/advisor-commands/SKILL.md").read_text(encoding="utf-8")
    for needle in ("advisor_fix", "wait_timeout", "lybra queue rework --task-id <卡ID> --actor <你的顾问实例>", "--confirm",
                   "等返工交回", "draft publish --path 5_tasks/drafts/<卡ID小写>.md --actor <你的顾问实例>", "card_done_take_next",
                   "lybra next --actor <你的顾问实例>", "enroll-deliver", "--actor <你的顾问实例> --owner-authorization-ref <信封ID>"):
        assert needle in skill, needle
    assert "python3 -m tools.aipos_cli.enroll_deliver" not in skill  # 旧转发入口示例退役, 以 CLI 为准
    from tools.aipos_cli.aipos_cli import build_parser

    parser = build_parser()
    for argv in (["roles", "--workspace-root", "g", "enroll-deliver", "--role", "executor", "--instance", "exec.p.h", "--governance-root", "g",
                  "--workspace", "w", "--actor", "advisor.p.h", "--owner-authorization-ref", "pol", "--dry-run"],
                 ["roles", "--workspace-root", "g", "enroll-code", "--role", "auditor", "--instance", "audit.p.h", "--governance-root", "g",
                  "--actor", "advisor.p.h", "--owner-authorization-ref", "pol", "--ttl", "86400", "--confirm"],
                 ["roles", "--workspace-root", "g", "enroll-deliver", "--role", "executor", "--instance", "exec.p.h", "--governance-root", "g",
                  "--workspace", "w", "--ssh", "u@h", "--gate-url", "http://h:1", "--actor", "advisor.p.h", "--owner-authorization-ref", "pol",
                  "--dry-run"],
                 ["queue", "rework", "--task-id", "X", "--actor", "advisor.p.h", "--verdict-ref", "v", "--focus-items", "[]",
                  "--acceptance-criteria", "[]", "--confirm"],
                 ["audit-verdict", "--reviewed-task-id", "X", "--verdict", "PASS", "--actor", "advisor.p.h", "--findings-summary", "s"]):
        parser.parse_args(argv)  # 文档示例形以 CLI --help 为准: 解析不过 = SystemExit 判红
    verbs = json.loads((REPO_ROOT / "schema/verbs.schema.json").read_text(encoding="utf-8"))
    roles_cmds = verbs["two_phase_protocol"]["roles_writers"]["commands"]
    assert {"enroll-code", "enroll-deliver"} <= set(roles_cmds)
