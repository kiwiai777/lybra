"""AIPOS-F134 夹具: 同一治理根多顾问实例独立凭据 + 信封 lane 选择器(CONVERGENCE §3c-1/2, gap #94)。

依据(10-08 只读调研): ① next_resolver._driver_token_instance / _driver_role_name 与薄壳取凭据都取连接文件里「第一个」驱动方 token,
不按实例挑——同一治理根接入两个顾问实例时 loop 用错身份; ② 治理根 .lybra/role 只有一个槽, 后接入覆盖先接入; ③ 顾问码不带实例 →
角色级条目按角色匹配互相 retired; ④ 信封 task_selector 无 lane 键——OTA 顾问的信封也能推另一 lane 的卡。

各件唯一并入点(禁第二路径):
  件① 挑凭据 = token_resolver.select_token_entry(经 two_phase_shell_factory.driver_token_entry, 只按实例, 无即拒);
      读身份 = loop_context.ConnectionResolver.resolve_identity; 驱动方信封身份集合 = autonomy_policy.driver_envelope_identities
      (loop find_envelope 与门 _match_driver_envelope / _match_claim_envelope 同一函数)。
  件② 发码拒 = enrollment.governance_seat_instance_refusal(create_enrollment_code 与门 dry-run 同用); role 分槽 = enroll_client
      .write_role_file(slot_by_instance); 分槽读法 = ConnectionResolver.resolve_identity。
  件③ lane 判定 = autonomy_policy.match_claim_envelope 谓词 lane_repo + 原因链; 卡仓解析 = workspace_config.resolve_card_repo
      (autonomy_policy.card_lane_refs); 签发 = owner_decision_writer._normalize_autonomy_policy(`lybra envelope mint --lane-repo`)。

靶场: 临时治理根 / 临时产品仓 / 进程内真门动词(F90 靶场 InProcessGate 同法, 按请求 token 取能力); 无真实凭据、不连真实门。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + loop 全链靶场(禁第二份)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.token_resolver import token_fingerprint  # noqa: E402

ADV_MAIN = "advisor.f134probe.h1"
ADV_OTA = "advisor-ota.f134probe.h1"
TOK_MAIN, TOK_OTA, TOK_SVC = "fixture-adv-main", "fixture-adv-ota", "fixture-svc-advisor"
POL_MAIN, POL_OTA = "pol_f134_main", "pol_f134_ota"
OTA_CARD = f90.TASK  # 全链卡沿用 F90 靶场卡号(其模拟执行体/审计体按此卡号落产物)
LT_CARD = "PROBE-LT-1"
CAPS = {
    TOK_MAIN: {"role": "advisor", "role_class": "advisor", "agent_instance": ADV_MAIN},
    TOK_OTA: {"role": "advisor", "role_class": "advisor", "agent_instance": ADV_OTA},
    TOK_SVC: {"role": "advisor", "role_class": "advisor"},
}


def _show(text: str) -> None:
    print(text)


class TokenGate(f90.InProcessGate):
    """进程内真门: 能力 = 请求 token 对应的条目(门侧按 bearer 定身份, 与真实门同形)。log 记 (门动词, 提交实例)。"""

    calls: list[tuple[str, str]] = []

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        assert token in CAPS, "token 只在进程内, 永不上屏"
        self.cap = {**CAPS[token], "token_ref": f"ref-{token_fingerprint(token)}", "expires_at": "2999-01-01T00:00:00Z",
                    "scopes": ["owner_confirm"]}

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        from tools.mcp_server import tools as gate

        TokenGate.calls.append((name, str(self.cap.get("agent_instance") or "(角色级)")))
        with gate.request_capability_scope(self.cap):
            res = getattr(gate, name)(dict(arguments))
        return res["structuredContent"] if isinstance(res, dict) and isinstance(res.get("structuredContent"), dict) else res


def _policy(gov: Path, pid: str, agent: str, *, lane: str | None = None) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=agent, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope", task_selector_task_mode="code", task_selector_lane_repo=lane))


@pytest.fixture
def two(rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """F90 靶场 + 同一治理根两个顾问实例(同角色 advisor, 各自 token; 连接文件首条是角色级 svc 条目 = 「取首个」必错)
    + 双仓清单(ota = 原产品仓, lantu = 第二仓) + 两张按实例签的信封(OTA 信封限 lane ota)。"""
    gov = rig.gov
    _write(gov / ".lybra" / "connection.json", json.dumps({
        "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "governance_root": str(gov),
        "tokens": [{"role": "advisor", "role_class": "advisor", "token": TOK_SVC, "token_ref": "svc-advisor", "scopes": []},
                   {"role": "advisor", "role_class": "advisor", "agent_instance": ADV_MAIN, "token": TOK_MAIN, "scopes": []},
                   {"role": "advisor", "role_class": "advisor", "agent_instance": ADV_OTA, "token": TOK_OTA, "scopes": []}],
    }))
    from tools.aipos_cli.enroll_client import write_role_file

    (gov / ".lybra" / "role").unlink()
    write_role_file(gov / ".lybra", "advisor", ADV_MAIN, POL_MAIN, slot_by_instance=True)
    write_role_file(gov / ".lybra", "advisor", ADV_OTA, POL_OTA, slot_by_instance=True)
    lantu = f78._init_product_repo(tmp_path / "product-lantu")
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl["repos"] = {"default": "ota", "items": {"ota": decl["code_repo"], "lantu": str(lantu)}}
    _write(gov / "project.json", json.dumps(decl))
    (gov / "5_tasks" / "policies" / f"{f90.POLICY}.md").unlink()  # F90 的角色类信封覆盖全部顾问, 本靶场按实例签
    _policy(gov, POL_MAIN, ADV_MAIN)
    _policy(gov, POL_OTA, ADV_OTA, lane="ota")
    from tools.aipos_cli import two_phase_shell_factory

    monkeypatch.setattr(two_phase_shell_factory, "GateClient", TokenGate)
    TokenGate.calls = []
    return SimpleNamespace(**vars(rig), lantu=lantu, conn=str(gov / ".lybra" / "connection.json"))


def _lane_card(gov: Path, task_id: str, repo: str) -> None:
    f90._card(gov, task_id, "pending", lane={"repo": repo, "paths": ["tests/"], "roles": ["executor"]})


def _stop_watch(args, expect_ready):  # 认领后不模拟工位: 等待即超时(声明退出码 3), loop 以「等待产物超时」收
    return 3


# ===========================================================================
# 件① 驱动身份按实例(loop --actor 各取自己的凭据; 不存在实例拒; 未指明不取首个)
# ===========================================================================

def test_item1_loop_actor_picks_own_credential_end_to_end(two):
    """OTA 顾问 loop 推 OTA 卡: 全链每个门动词都以 OTA 实例凭据提交(连接文件首条角色级 svc 与主顾问条目都不被取到)。"""
    _lane_card(two.gov, OTA_CARD, "ota")
    init_governance_repo(two.gov)
    out = io.StringIO()
    res = run_loop(OTA_CARD, two.gov, actor=ADV_OTA, out=out, watch=f90._agent_watch(two, []), interval=0.05, max_wait=5,
                   max_steps=30)
    text = out.getvalue()
    _show(text)
    _show(f"[件①·全链] 门动词 × 提交实例: {TokenGate.calls}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert f"envelope={POL_OTA} driver={ADV_OTA}" in text
    assert TokenGate.calls and {inst for _n, inst in TokenGate.calls} == {ADV_OTA}, TokenGate.calls
    claims = sorted((two.gov / "5_tasks" / "records" / "claims").rglob("claim_*.md"))
    _show("[件①·认领记录] " + "; ".join(f"{p.parent.name}: owner_policy_ref=" + re.search(r"owner_policy_ref: (\S+)", p.read_text()).group(1)
                                        for p in claims))
    assert len(claims) == 2 and all(f"owner_policy_ref: {POL_OTA}" in p.read_text(encoding="utf-8") for p in claims)


def test_item1_each_actor_resolves_its_own_token_and_role(two, monkeypatch):
    from tools.aipos_cli.two_phase_shell_factory import (
        DRIVER_INSTANCE_ENV,
        DriverTokenError,
        load_role_token,
        resolve_driver_role_from_connection,
    )

    seen = {}
    for actor, token in ((ADV_MAIN, TOK_MAIN), (ADV_OTA, TOK_OTA)):
        with nr.driver_scope(actor=actor, policy_id=None):
            inst = nr._driver_token_instance(two.gov)
            role = nr._driver_role_name(two.gov)
            got = load_role_token(connection_json_path=two.conn, role=role)
        seen[actor] = (inst, role, token_fingerprint(got))
        assert inst == actor and role == "advisor" and got == token
    _show(f"[件①·按实例] --actor → (token 绑定实例, 角色名, 所取凭据指纹): {seen}")
    assert seen[ADV_MAIN][2] != seen[ADV_OTA][2] != token_fingerprint(TOK_SVC)
    # 未指明实例 + 连接文件绑定 2 个驱动方实例 = 拒(不取首个)
    monkeypatch.delenv(DRIVER_INSTANCE_ENV, raising=False)
    with pytest.raises(DriverTokenError) as exc:
        resolve_driver_role_from_connection(connection_json_path=two.conn)
    _show(f"[件①·未指明] {exc.value}")
    assert ADV_MAIN in str(exc.value) and ADV_OTA in str(exc.value) and "禁取首个" in str(exc.value)
    assert nr._driver_token_instance(two.gov) == "" and nr._driver_role_name(two.gov) == ""
    # 派生命令子进程: loop 经环境变量下传实例, 薄壳按实例取
    monkeypatch.setenv(DRIVER_INSTANCE_ENV, ADV_OTA)
    assert resolve_driver_role_from_connection(connection_json_path=two.conn) == "advisor"
    assert load_role_token(connection_json_path=two.conn, role="advisor") == TOK_OTA


def test_item1_unknown_actor_refused_and_no_actor_with_two_slots_refused(two):
    _lane_card(two.gov, OTA_CARD, "ota")
    out = io.StringIO()
    ghost = "advisor-ghost.f134probe.h1"
    res = run_loop(OTA_CARD, two.gov, actor=ghost, out=out, watch=_stop_watch, max_wait=1, interval=0.05)
    _show(f"[件①·不存在实例] exit {res.exit_code}: {res.message}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "not_derivable")
    assert f"驱动方实例 '{ghost}'" in res.message and "禁回落首个驱动方 token" in res.message
    assert ADV_MAIN in res.message and ADV_OTA in res.message and not TokenGate.calls
    # 连接文件绑定两个驱动方实例、未给 --actor = 驱动方不定(不取 role 顶层/首个冒名)
    out = io.StringIO()
    res = run_loop(OTA_CARD, two.gov, out=out, watch=_stop_watch, max_wait=1, interval=0.05)
    _show(f"[件①·未给 --actor] exit {res.exit_code}: {res.message}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "not_derivable")
    assert res.missing_records == [nr.DRIVER_ACTOR_MISSING] and "须显式 --actor" in nr.DRIVER_ACTOR_MISSING


def test_item1_subprocess_env_carries_driver_instance(two, monkeypatch):
    """loop 派生命令以子进程执行时, 已校验的驱动方实例经 LYBRA_DRIVER_INSTANCE 下传(唯一写入点 _run_product_command)。"""
    captured: dict = {}

    def fake_run(argv, *a, **kw):
        captured.update(kw.get("env") or {})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with nr.driver_scope(actor=ADV_OTA, policy_id=POL_OTA):
        nr._run_product_command("lybra queue close --task-id X --actor y", "close")
    assert captured.get("LYBRA_DRIVER_INSTANCE") == ADV_OTA


def test_item1_gate_and_loop_share_one_identity_builder():
    """身份口径一致: 驱动方信封身份集合只由 driver_envelope_identities 构造(loop 与门两条驱动方路径同一函数)。"""
    from tools.aipos_cli.autonomy_policy import driver_envelope_identities

    assert driver_envelope_identities(ADV_OTA, "hbj-advisor", "advisor") == [(ADV_OTA, ADV_OTA, "hbj-advisor"), (ADV_OTA, ADV_OTA, "advisor")]
    assert driver_envelope_identities("", "advisor", "advisor") == [("advisor", "advisor", "advisor")]  # 角色级 token: 实例缺 = 角色类
    loop_src = (REPO_ROOT / "tools/aipos_cli/loop_driver.py").read_text(encoding="utf-8")
    gate_src = (REPO_ROOT / "tools/mcp_server/tools.py").read_text(encoding="utf-8")
    assert "identities=driver_envelope_identities(driver_actor, driver_role, DRIVER_ROLE)" in loop_src
    assert gate_src.count("driver_envelope_identities(bound, ") == 2  # _match_driver_envelope + _match_claim_envelope(驱动方)


# ===========================================================================
# 件② 顾问接入不互覆(码必带实例; 连接文件 + role 按实例并存; svc 角色级条目语义不变)
# ===========================================================================

def _enroll(gov: Path, gate: Path, instance: str, token: str, *, role: str = "advisor") -> dict:
    from tools.aipos_cli.enroll_client import enroll
    from tools.aipos_cli.enrollment import issue_self_contained_code

    sc = issue_self_contained_code(gate, role=role, instance=instance, ttl_seconds=600, governance_root=str(gov), by="t")["self_contained_code"]
    exchange = {"ok": True, "token_entry": {"role": role, "agent_instance": instance, "fingerprint": token_fingerprint(token),
                                            "scopes": ["advisor"], "token": token}}
    with patch("tools.aipos_cli.enroll_client.exchange_enrollment_code", return_value=exchange), \
            patch("tools.aipos_cli.enroll_client.land_enrollment_code", return_value=True):
        return enroll(code=sc, gate_url="", workspace_root=gov)


def test_item2_two_advisors_enroll_same_governance_root_coexist(tmp_path, monkeypatch):
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    (gov / ".lybra" / "role").unlink()
    gate = tmp_path / "gate"
    _write(gate / ".lybra" / "connection.json", json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": []}))
    # 门注册表(治理根 connection.json)里已有角色级 svc 条目(lybra roles register / serve 角色凭据形)
    _write(gov / ".lybra" / "connection.json", json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
                                                            "tokens": [{"role": "advisor", "token": TOK_SVC, "token_ref": "svc-advisor"}]}))
    r1 = _enroll(gov, gate, ADV_MAIN, TOK_MAIN)
    r2 = _enroll(gov, gate, ADV_OTA, TOK_OTA)
    assert r1["ok"] and r2["ok"] and not r1["rotated"] and not r2["rotated"]
    conn = json.loads((gov / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    role = json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))
    shown = [{k: (token_fingerprint(v) if k == "token" else v) for k, v in t.items() if k in ("role", "agent_instance", "token", "token_ref", "retired")}
             for t in conn["tokens"] if t.get("role") == "advisor"]
    _show(f"[件②·连接文件(token 只出指纹)] {json.dumps(shown, ensure_ascii=False)}")
    _show(f"[件②·role 记录] {json.dumps(role, ensure_ascii=False, indent=1)}")
    active = {t.get("agent_instance") or "(角色级)": t for t in conn["tokens"] if t.get("role") == "advisor" and not t.get("retired")}
    assert set(active) == {"(角色级)", ADV_MAIN, ADV_OTA}  # 互不 retired; svc 角色级条目原样
    assert set(role["instances"]) == {ADV_MAIN, ADV_OTA} and role["instance"] == ADV_OTA
    assert role["instances"][ADV_MAIN]["instance"] == ADV_MAIN and role["instances"][ADV_OTA]["instance"] == ADV_OTA
    # 读口不新增: 既有读口 ConnectionResolver.resolve_identity 照读顶层(= 最近接入实例); 按实例取凭据经 select_token_entry
    from tools.loop_context import ConnectionResolver

    ident = ConnectionResolver.resolve_identity(workspace_root=gov, env={})
    assert ident["agent_instance"]["value"] == ADV_OTA and ident["role"]["value"] == "advisor"
    for inst in (ADV_MAIN, ADV_OTA):
        ident = ConnectionResolver.resolve_identity(workspace_root=gov, env={}, explicit={"agent_instance": inst})
        assert ident["token"]["value"] == {ADV_MAIN: TOK_MAIN, ADV_OTA: TOK_OTA}[inst]
    # 驱动方不靠 role 顶层: 连接文件绑定 2 个驱动方实例而未给 --actor = 不定("")
    assert nr._driver_actor(gov) == "" and nr._driver_actor(gov, fallback="advisor") == "advisor"
    # 同一实例重接入: 只轮换该实例自己的条目, 另一实例与 svc 不动; 另一实例的槽不被改写
    before_ota = dict(role["instances"][ADV_OTA])
    r3 = _enroll(gov, gate, ADV_MAIN, "fixture-adv-main-2")
    conn = json.loads((gov / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    retired = [t.get("agent_instance") for t in conn["tokens"] if t.get("retired")]
    role = json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))
    _show(f"[件②·重接入 {ADV_MAIN}] rotated={r3['rotated']} retired 条目实例={retired}")
    assert r3["rotated"] and retired == [ADV_MAIN] and role["instances"][ADV_OTA] == before_ota


def test_item2_governance_seat_code_without_instance_refused(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import create_enrollment_code, issue_self_contained_code

    gate = tmp_path / "gate"
    _write(gate / ".lybra" / "connection.json", json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": []}))
    for role in ("advisor", "planner"):
        with pytest.raises(ValueError) as exc:
            create_enrollment_code(gate, role=role)
        _show(f"[件②·{role} 码无实例] {exc.value}")
        assert "必须带实例" in str(exc.value) and "governance_seat" not in str(exc.value)
    assert not (gate / ".lybra" / "enrollments.json").exists()  # 拒 = 零写入
    assert issue_self_contained_code(gate, role="executor", ttl_seconds=60, by="t")["code_id"]  # 工位类语义不变
    # 门 dry-run 预检同一判据(INSTANCE_REQUIRED)
    from tools.mcp_server import tools as gate_tools

    monkeypatch.setattr(gate_tools, "_repo_root", lambda: gate)
    _validated, err = gate_tools._validate_enroll_code_args({"role": "advisor", "owner_authorization_ref": "fixture"})
    payload = err.get("structuredContent", err)
    _show(f"[件②·门 dry-run] {json.dumps(payload, ensure_ascii=False)[:300]}")
    assert "INSTANCE_REQUIRED" in json.dumps(payload)
    validated, err = gate_tools._validate_enroll_code_args({"role": "advisor", "instance": ADV_OTA, "owner_authorization_ref": "fixture"})
    assert err is None and validated["instance"] == ADV_OTA


def test_item2_workstation_role_file_shape_unchanged(tmp_path):
    """工位(及单实例治理根)role 文件不分槽: 换实例重写 = 既有合并语义, 无 instances 键。"""
    from tools.aipos_cli.enroll_client import write_role_file

    d = tmp_path / ".lybra"
    d.mkdir()
    write_role_file(d, "executor", "exec.a.h", "pol_x")
    write_role_file(d, "executor", "exec.b.h", None)
    data = json.loads((d / "role").read_text(encoding="utf-8"))
    assert "instances" not in data and data["instance"] == "exec.b.h" and data["owner_policy_ref"] == "pol_x"


# ===========================================================================
# 件③ 信封 lane 选择器(OTA 信封推 OTA 卡过、推另一 lane 卡拒; 无 lane 信封不变)
# ===========================================================================

def test_item3_loop_lane_envelope_passes_own_lane_refuses_other(two):
    from tools.aipos_cli.loop_driver import find_envelope

    _lane_card(two.gov, OTA_CARD, "ota")
    _lane_card(two.gov, LT_CARD, "lantu")
    ota_fm = nr._read_frontmatter(nr._find_task_in_queue(two.gov, OTA_CARD)[0])
    lt_fm = nr._read_frontmatter(nr._find_task_in_queue(two.gov, LT_CARD)[0])
    pol, _ = find_envelope(two.gov, task_id=OTA_CARD, task_fm=ota_fm, driver_actor=ADV_OTA, driver_role="advisor")
    assert pol and pol["policy_id"] == POL_OTA and pol["task_selector_lane_repo"] == "ota"
    none, reasons = find_envelope(two.gov, task_id=LT_CARD, task_fm=lt_fm, driver_actor=ADV_OTA, driver_role="advisor")
    _show(f"[件③·loop OTA 信封推 lantu 卡] 原因链: {reasons}")
    assert none is None and any(r.startswith(f"{POL_OTA}: card lane.repo resolves to") and "task_selector.lane_repo='ota'" in r for r in reasons)
    # 无 lane 信封(主顾问)行为不变: 两个 lane 的卡都覆盖
    for fm, tid in ((ota_fm, OTA_CARD), (lt_fm, LT_CARD)):
        pol, _ = find_envelope(two.gov, task_id=tid, task_fm=fm, driver_actor=ADV_MAIN, driver_role="advisor")
        assert pol and pol["policy_id"] == POL_MAIN
    # loop 端到端: OTA 顾问推 lantu 卡 = exit 5(无信封, 原因链带 lane); 主顾问推同卡 = 经门认领, 卡工作树建在 lantu 仓
    out = io.StringIO()
    res = run_loop(LT_CARD, two.gov, actor=ADV_OTA, out=out, watch=_stop_watch, max_wait=1, interval=0.05)
    _show(f"[件③·loop --actor {ADV_OTA} {LT_CARD}] exit {res.exit_code}\n{out.getvalue()}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "no_envelope") and "lane_repo='ota'" in res.message
    assert not TokenGate.calls
    out = io.StringIO()
    res = run_loop(LT_CARD, two.gov, actor=ADV_MAIN, out=out, watch=_stop_watch, max_wait=1, interval=0.05)
    _show(f"[件③·loop --actor {ADV_MAIN} {LT_CARD}] exit {res.exit_code} 门动词: {TokenGate.calls}")
    assert res.steps and res.steps[0].action_type == "claim" and res.steps[0].ok, out.getvalue()
    assert {inst for _n, inst in TokenGate.calls} == {ADV_MAIN}
    repo, worktree = nr.card_worktree_location(two.gov, LT_CARD)
    assert Path(repo).resolve() == two.lantu.resolve() and worktree.is_dir()


def test_item3_gate_lane_predicate_reason_chain(two):
    """门侧同一判据: OTA 驱动方 token + OTA 信封 → OTA 卡放行; lantu 卡 ENVELOPE_SELECTOR_LANE_REPO_MISMATCH(声明出口)。"""
    from tools.mcp_server import tools as gate

    _lane_card(two.gov, OTA_CARD, "ota")
    _lane_card(two.gov, LT_CARD, "lantu")
    cap = {**CAPS[TOK_OTA], "token_ref": "r", "expires_at": "2999-01-01T00:00:00Z"}
    with gate.request_capability_scope(cap):
        ok = gate._match_claim_envelope(two.gov, owner_policy_ref=POL_OTA, task_id=OTA_CARD, task_path=None,
                                        canonical_agent_instance=f78.EXEC, actor=f78.EXEC)
        bad = gate._match_claim_envelope(two.gov, owner_policy_ref=POL_OTA, task_id=LT_CARD, task_path=None,
                                         canonical_agent_instance=f78.EXEC, actor=f78.EXEC)
        drv_ok, drv_err_none = gate._match_driver_envelope(two.gov, owner_policy_ref=POL_OTA, task_id=OTA_CARD, verb="return")
        _pid, drv_err = gate._match_driver_envelope(two.gov, owner_policy_ref=POL_OTA, task_id=LT_CARD, verb="return")
    _show(f"[件③·门 claim] OTA 卡={ok[0]} / lantu 卡 error_code={bad[2]}")
    _show(f"[件③·门 return] OTA 卡={drv_ok} / lantu 卡拒: {json.dumps(drv_err, ensure_ascii=False)[:400]}")
    assert ok[0] == POL_OTA and bad[0] is None and bad[2] == "ENVELOPE_SELECTOR_LANE_REPO_MISMATCH"
    assert drv_ok == POL_OTA and drv_err_none is None
    # 门拒文案 = transitions envelope_guards 声明(error_message + next_step), 与 claim 路径同一声明源
    assert "ENVELOPE_SELECTOR_LANE_REPO_MISMATCH" in json.dumps(drv_err)
    assert "task_selector_lane_repo 不匹配" in json.dumps(drv_err, ensure_ascii=False)
    decl = json.loads((REPO_ROOT / "schema/transitions.schema.json").read_text(encoding="utf-8"))["envelope_guards"]["guards"]
    assert decl["selector_lane_repo_mismatch"]["error_code"] == "ENVELOPE_SELECTOR_LANE_REPO_MISMATCH"


def test_item3_mint_lane_repo_declared_validated_and_default_unchanged(two):
    from tools.aipos_cli.aipos_cli import _envelope_mint_payload, build_parser
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown, load_policy
    from tools.aipos_cli.owner_decision_writer import build_owner_decision_record

    args = build_parser().parse_args(["envelope", "mint", "--policy-id", "pol_f134_m", "--agent-or-role", ADV_OTA, "--max-tasks", "5",
                                      "--task-mode", "code", "--expires-at", "2999-01-01T00:00:00Z", "--decision-summary", "s",
                                      "--lane-repo", "ota", "--dry-run"])
    assert args.lane_repo == "ota"
    payload = _envelope_mint_payload(policy_id="pol_f134_m", agent_or_role=ADV_OTA, max_tasks=5, task_mode="code",
                                     expires_at="2999-01-01T00:00:00Z", decision_summary="s", actor="owner", lane_repo="ota")
    assert payload["autonomy_policy"]["task_selector"] == {"task_mode": "code", "lane_repo": "ota"}
    res = build_owner_decision_record(two.gov, payload, actor="owner", dry_run=False)
    assert not res.get("blocking_reasons"), res
    text = (two.gov / "5_tasks" / "policies" / "pol_f134_m.md").read_text(encoding="utf-8")
    _show("[件③·mint --lane-repo ota 落盘信封]\n" + text)
    assert "task_selector_lane_repo: ota" in text and load_policy(two.gov, "pol_f134_m")["task_selector_lane_repo"] == "ota"
    bad = _envelope_mint_payload(policy_id="pol_f134_bad", agent_or_role=ADV_OTA, max_tasks=5, task_mode="code",
                                 expires_at="2999-01-01T00:00:00Z", decision_summary="s", actor="owner", lane_repo="nope")
    res = build_owner_decision_record(two.gov, bad, actor="owner", dry_run=True)
    _show(f"[件③·mint --lane-repo nope] blocking_reasons={res.get('blocking_reasons')}")
    assert any("lane_repo='nope' 不可解析" in r and "LANE_REPO_UNDECLARED" in r for r in res.get("blocking_reasons") or [])
    # 缺省 = 不限 lane: 渲染逐字节同于不传该参数(存量信封与新铸无 lane 信封不变)
    kw = dict(policy_id="p", agent_or_role="a", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z", max_tasks=1,
              owner_approval_ref="d", task_selector_task_mode="code")
    plain = build_autonomy_policy_markdown(**kw)
    assert build_autonomy_policy_markdown(**kw, task_selector_lane_repo=None) == plain and "lane" not in plain.lower()


# ===========================================================================
# 不变量(棘轮只减): 「连接文件首个驱动方条目」扫描只许存量无实例路径, 新增 = 红
# ===========================================================================

BASELINE = Path(__file__).resolve().parent / "f134_driver_first_token_baseline.jsonl"
FIRST_DRIVER_SCAN = re.compile(r"""tok\.get\(\s*["']role_class["']\s*\)\s*or\s*tok\.get\(\s*["']role["']\s*\)""")


def test_ratchet_first_driver_token_scan_only_shrinks():
    """驱动方凭据挑选唯一 = driver_token_entry → select_token_entry; 「遍历 tokens 取首个 role_class==driver」只许基线条目
    (next_resolver 未指明实例且连接文件仅 1 个驱动方实例时的存量路径), 新增命中 = 红, 基线条目已不存在 = 红(须删该行)。"""
    import hashlib

    from ratchet_baseline import read_entries
    from tools.aipos_cli.runall_discovery import repo_files

    hits = []
    for rel in repo_files(REPO_ROOT):
        parts = rel.split("/")
        if not rel.endswith(".py") or "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py":
            continue
        for line in (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines():
            if FIRST_DRIVER_SCAN.search(line):
                hits.append((rel, hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:12]))
    base = [(e["file"], e["fp"]) for e in read_entries(BASELINE)]
    _show(f"[棘轮] 现存命中 {len(hits)} / 基线 {len(base)}: {hits}")
    assert sorted(hits) == sorted(base), {"新增": sorted(set(hits) - set(base)), "基线残留": sorted(set(base) - set(hits))}
    src = (REPO_ROOT / "tools/aipos_cli/two_phase_shell_factory.py").read_text(encoding="utf-8")
    assert "entry = select_token_entry(tokens, agent_instance=instance, source=path)" in src
    for fn in ("_driver_token_instance", "_driver_role_name"):
        body = re.search(rf"def {fn}\(.*?(?=\ndef )", (REPO_ROOT / "tools/aipos_cli/next_resolver.py").read_text(encoding="utf-8"), re.S).group(0)
        assert "driver_token_entry(" in body, fn
