"""AIPOS-F154 夹具: CLI 两阶段薄壳身份单源——签发者即 --actor 的命令按 --actor 实例选凭据并作 agent_instance 交门;
薄壳按角色取凭据时同角色多实例 = 拒(禁取首个冒名)。

依据(10-11 chris 岚图 LANTU-SW3 返工实撞): `lybra queue rework` 报 AGENT_INSTANCE_REQUIRED(args_dict 不含 agent_instance,
门 tools.lybra_queue_rework_dry_run 必填), 且薄壳按角色类取连接文件首个 advisor token——同一治理根三个顾问实例会以错误身份签发。

各件唯一并入点(禁第二路径):
  件① queue rework 身份 = two_phase_shell_factory.signer_role_for_actor(→ F134 driver_token_entry → token_resolver.select_token_entry,
      未接入即拒) + 薄壳工厂 signer_instance(→ load_role_token 按实例取同一条目的 token); 角色类授权 = 门侧 F132
      custom_roles.authorize_instance_class(board_adapter.queue_rework_task 既有调用)。返工节写入 = F75 queue_mutation.build_rework_round
      + amend_task 受限模式(不变)。
  件② queue adopt 签发者 = --actor(门判 actor = token 绑定实例)→ 同一 signer_role_for_actor; task-progress --confirm =
      progress_reporter_identity; 按角色取凭据(含 audit-verdict --token-role 显式覆盖与显式参数旧路径)= load_role_token 的多实例拒。

靶场: 临时治理根 / 临时产品仓 / 进程内真门动词(F90 靶场 InProcessGate 同法, 按请求 token 取能力); 无真实凭据、不连真实门。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + 产品 CLI 靶场(禁第二份)
from test_aipos_f78_engine_agnostic import _fm, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli.token_resolver import token_fingerprint  # noqa: E402

PROJECT = "f154probe"
ADV_MAIN = f"advisor.{PROJECT}.h1"
ADV_OTA = f"advisor-ota.{PROJECT}.h1"
ADV_LANTU = f"advisor-lantu.{PROJECT}.h1"
ADV_GHOST = f"advisor-ghost.{PROJECT}.h1"  # 未接入本治理根
TOK_SVC, TOK_MAIN, TOK_OTA, TOK_LANTU = "fixture-svc-advisor", "fixture-adv-main", "fixture-adv-ota", "fixture-adv-lantu"
TOKENS = [  # 首条 = 角色级 svc 条目(「取首个 advisor token」必错); 后三条 = 三个顾问类实例(内建 advisor + 两个自定义顾问角色)
    {"role": "advisor", "role_class": "advisor", "token": TOK_SVC, "token_ref": "svc-advisor", "scopes": []},
    {"role": "advisor", "role_class": "advisor", "agent_instance": ADV_MAIN, "token": TOK_MAIN, "token_ref": "tr-main", "scopes": []},
    {"role": "advisor-ota", "role_class": "advisor", "agent_instance": ADV_OTA, "token": TOK_OTA, "token_ref": "tr-ota", "scopes": []},
    {"role": "advisor-lantu", "role_class": "advisor", "agent_instance": ADV_LANTU, "token": TOK_LANTU, "token_ref": "tr-lantu",
     "scopes": []},
]
CAPS = {t["token"]: {k: t[k] for k in ("role", "role_class", "agent_instance") if k in t} for t in TOKENS}
FOCUS = ["修 SW3 审计 FAIL 点 1", "补测 2"]
CRITERIA = ["夹具绿", "两边基线"]


def _show(text: str) -> None:
    print(text, flush=True)


class TokenGate(f90.InProcessGate):
    """进程内真门: 能力 = 请求 token 对应的条目(门侧按 bearer 定身份, 与真实门同形)。log 记 (门动词, token 绑定实例, 请求 actor, 请求 agent_instance)。"""

    calls: list[tuple[str, str, str, str]] = []

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        assert token in CAPS, "token 只在进程内, 永不上屏"
        self.cap = {**CAPS[token], "token_ref": f"ref-{token_fingerprint(token)}", "expires_at": "2999-01-01T00:00:00Z",
                    "scopes": ["owner_confirm"]}

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        from tools.mcp_server import tools as gate

        TokenGate.calls.append((name, str(self.cap.get("agent_instance") or "(角色级)"), str(arguments.get("actor") or ""),
                                str(arguments.get("agent_instance") or "")))
        with gate.request_capability_scope(self.cap):
            res = getattr(gate, name)(dict(arguments))
        return res["structuredContent"] if isinstance(res, dict) and isinstance(res.get("structuredContent"), dict) else res


@pytest.fixture
def three(rig, monkeypatch: pytest.MonkeyPatch):
    """F90 靶场 + 同一治理根三个顾问类实例(各自凭据; 连接文件首条是角色级 svc 条目), 未设驱动方实例环境变量。"""
    from tools.aipos_cli import two_phase_shell_factory
    from tools.aipos_cli.two_phase_shell_factory import DRIVER_INSTANCE_ENV

    conn = rig.gov / ".lybra" / "connection.json"
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "governance_root": str(rig.gov), "tokens": TOKENS}))
    monkeypatch.delenv(DRIVER_INSTANCE_ENV, raising=False)
    monkeypatch.setattr(two_phase_shell_factory, "GateClient", TokenGate)
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


def _rework_argv(task_id: str, actor: str, *extra: str) -> list[str]:
    return ["queue", "rework", "--task-id", task_id, "--actor", actor, "--verdict-ref", f"verdict_{task_id}_fail",
            "--focus-items", json.dumps(FOCUS, ensure_ascii=False), "--acceptance-criteria", json.dumps(CRITERIA, ensure_ascii=False),
            "--confirm", *extra]


def _claimed(gov: Path, task_id: str) -> Path:
    return f90._card(gov, task_id, "claimed")


def _amendments(gov: Path, task_id: str) -> list[Path]:
    return sorted((gov / "5_tasks" / "records" / "amendments" / task_id).glob("*.md"))


# ===========================================================================
# 件① queue rework: --actor 指定实例签发, agent_instance=该实例交门; 未接入实例拒
# ===========================================================================

def test_item1_rework_each_advisor_signs_with_own_credential(three):
    """三个顾问类实例各返工一张卡: 门两跳(dry_run/confirm)都以该实例自己的凭据提交, 请求 actor=agent_instance=该实例,
    返工节 created_by 与修订记录 amended_by 均为该实例原文; 连接文件首条角色级 svc 条目与其他顾问条目都不被取到。"""
    from tools.aipos_cli.frontmatter import require_frontmatter

    for idx, actor in enumerate((ADV_MAIN, ADV_OTA, ADV_LANTU), start=1):
        task_id = f"PROBE-F154-{idx}"
        card = _claimed(three.gov, task_id)
        TokenGate.calls = []
        rc, out, err = _cli(_rework_argv(task_id, actor))
        _show(f"[件①·{actor}] exit {rc}\nstdout: {out.strip()}\nstderr: {err.strip()}\n门动词 × (token 绑定实例, actor, agent_instance): {TokenGate.calls}")
        assert rc == 0, err
        assert [c[0] for c in TokenGate.calls] == ["lybra_queue_rework_dry_run", "lybra_queue_rework_confirm"], TokenGate.calls
        assert {c[1] for c in TokenGate.calls} == {actor}, TokenGate.calls  # 签发凭据 = 该实例
        assert TokenGate.calls[0][2:] == (actor, actor), TokenGate.calls  # dry_run 请求 actor/agent_instance = 该实例
        assert TokenGate.calls[1][2] == actor
        meta, _body = require_frontmatter(card)
        rounds = meta.get("rework_rounds")
        _show(f"[件①·{actor}] 卡面 rework_rounds: {json.dumps(rounds, ensure_ascii=False)}")
        assert isinstance(rounds, list) and len(rounds) == 1
        assert rounds[0]["created_by"] == actor and rounds[0]["focus_items"] == FOCUS
        assert rounds[0]["verdict_ref"] == f"verdict_{task_id}_fail"
        assert rounds[0]["acceptance_criteria"] == "\n".join(CRITERIA)  # card.schema rework_rounds.acceptance_criteria = string(F75)
        records = _amendments(three.gov, task_id)
        assert len(records) == 1, records
        rec_text = records[0].read_text(encoding="utf-8")
        _show(f"[件①·{actor}] amendment 记录 {records[0].name}:\n{rec_text}")
        assert re.search(rf"^amended_by: {re.escape(actor)}$", rec_text, re.M), rec_text
        assert "amendment_type: add_rework_round" in rec_text


def test_item1_rework_unenrolled_actor_refused_before_gate(three):
    """未接入本治理根的实例 = 拒(原文点名已接入的驱动方实例与出口), 不发门、零写入。"""
    task_id = "PROBE-F154-GHOST"
    card = _claimed(three.gov, task_id)
    before = card.read_bytes()
    rc, out, err = _cli(_rework_argv(task_id, ADV_GHOST))
    _show(f"[件①·未接入 {ADV_GHOST}] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls
    assert ADV_GHOST in err and "无可用凭据" in err and "禁回落首个驱动方 token" in err
    for inst in (ADV_MAIN, ADV_OTA, ADV_LANTU):
        assert inst in err  # 点名该连接文件已绑定的驱动方实例
    assert card.read_bytes() == before and not _amendments(three.gov, task_id)


def test_item1_rework_actor_conflicts_with_loop_driver_refused(three, monkeypatch):
    """loop 已定驱动方实例(环境变量 LYBRA_DRIVER_INSTANCE)而 --actor 是另一实例 = 拒(禁以 A 的驱动冒签 B), 不发门。"""
    from tools.aipos_cli.two_phase_shell_factory import DRIVER_INSTANCE_ENV

    task_id = "PROBE-F154-CONFLICT"
    _claimed(three.gov, task_id)
    monkeypatch.setenv(DRIVER_INSTANCE_ENV, ADV_MAIN)
    rc, _out, err = _cli(_rework_argv(task_id, ADV_OTA))
    _show(f"[件①·冲突 env={ADV_MAIN} --actor {ADV_OTA}] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls and "不一致" in err and ADV_MAIN in err and ADV_OTA in err
    # 同一实例 = 照常签发
    rc, _out, err = _cli(_rework_argv(task_id, ADV_MAIN))
    assert rc == 0, err
    assert {c[1] for c in TokenGate.calls} == {ADV_MAIN}


def test_item1_rework_gate_still_authorizes_role_class(three):
    """角色类授权仍在门侧 F132(authorize_instance_class): 绑定在非顾问类角色上的实例即便有凭据也拒在 CLI 唯一口(驱动方角色类过滤)。"""
    conn = json.loads(Path(three.conn).read_text(encoding="utf-8"))
    exec_inst = f"exec.{PROJECT}.h1"
    conn["tokens"].append({"role": "executor", "agent_instance": exec_inst, "token": "fixture-exec", "token_ref": "tr-exec", "scopes": []})
    _write(Path(three.conn), json.dumps(conn))
    task_id = "PROBE-F154-EXEC"
    _claimed(three.gov, task_id)
    rc, _out, err = _cli(_rework_argv(task_id, exec_inst))
    _show(f"[件①·执行体实例 {exec_inst}] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls and "只有非驱动方角色类凭据" in err


# ===========================================================================
# 件② 同类薄壳: adopt 按 --actor; 按角色取凭据多实例拒; task-progress 按 --actor 实例
# ===========================================================================

def test_item2_adopt_preview_signs_with_actor_instance(three):
    """queue adopt(门判 actor = token 绑定实例): 三顾问治理根未设驱动方实例, --actor OTA → 以 OTA 凭据发门(原: 未指明驱动方即拒);
    未接入实例拒在发门前。"""
    task_id = "PROBE-F154-ADOPT"
    _claimed(three.gov, task_id)
    rc, out, err = _cli(["queue", "adopt", "--task-id", task_id, "--branch", "legacy/x", "--actor", ADV_OTA,
                         "--owner-policy-ref", "pol_x", "--json"])
    _show(f"[件②·adopt --actor {ADV_OTA}] exit {rc} 门动词: {TokenGate.calls}\nstderr: {err.strip()[:400]}")
    assert TokenGate.calls and TokenGate.calls[0][0] == "lybra_queue_adopt_dry_run"
    assert {c[1] for c in TokenGate.calls} == {ADV_OTA} and TokenGate.calls[0][2] == ADV_OTA
    assert "INSTANCE_MISMATCH" not in out
    TokenGate.calls = []
    rc, _out, err = _cli(["queue", "adopt", "--task-id", task_id, "--branch", "legacy/x", "--actor", ADV_GHOST,
                          "--owner-policy-ref", "pol_x"])
    _show(f"[件②·adopt 未接入 {ADV_GHOST}] exit {rc}\nstderr 原文: {err.strip()}")
    assert rc == 1 and not TokenGate.calls and ADV_GHOST in err


def _conn(tmp_path: Path, tokens: list[dict]) -> str:
    path = tmp_path / "conn" / "connection.json"
    _write(path, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": tokens}))
    return str(path)


def test_item2_role_level_token_multi_instance_refused(tmp_path, monkeypatch):
    """薄壳唯一口 load_role_token: 按角色取时同角色 ≥2 实例 = 拒(禁取首个冒名); ≤1 实例行为不变; 显式实例只取本实例本角色条目。"""
    from tools.aipos_cli.two_phase_shell_factory import DRIVER_INSTANCE_ENV, DriverTokenError, load_role_token

    monkeypatch.delenv(DRIVER_INSTANCE_ENV, raising=False)
    a, b = f"advisor.{PROJECT}.h1", f"advisor.{PROJECT}.h2"
    multi = _conn(tmp_path / "m", [{"role": "advisor", "agent_instance": a, "token": "fixture-a"},
                                   {"role": "advisor", "agent_instance": b, "token": "fixture-b"}])
    with pytest.raises(DriverTokenError) as exc:
        load_role_token(connection_json_path=multi, role="advisor")
    _show(f"[件②·按角色多实例] {exc.value}")
    assert a in str(exc.value) and b in str(exc.value) and "禁取首个冒名" in str(exc.value)
    assert load_role_token(connection_json_path=multi, role="advisor", agent_instance=b) == "fixture-b"
    with pytest.raises(DriverTokenError) as exc:
        load_role_token(connection_json_path=multi, role="advisor", agent_instance=ADV_GHOST)
    assert "禁回落按角色取首个" in str(exc.value)
    single = _conn(tmp_path / "s", [{"role": "owner", "token": "fixture-owner"},
                                    {"role": "advisor", "agent_instance": a, "token": "fixture-a"}])
    assert load_role_token(connection_json_path=single, role="advisor") == "fixture-a"  # 单实例: 既有行为
    assert load_role_token(connection_json_path=single, role="owner") == "fixture-owner"  # 角色级条目: 既有行为
    # loop 驱动方实例已知 = 按实例取(F134 既有路径不变)
    monkeypatch.setenv(DRIVER_INSTANCE_ENV, b)
    assert load_role_token(connection_json_path=multi, role="advisor") == "fixture-b"


def test_item2_token_role_override_multi_instance_refused_before_gate(tmp_path, monkeypatch):
    """audit-verdict --token-role 显式覆盖等按角色取的薄壳调用: 多实例未指明 = exit 1 且不发门(原: 取首个实例签发)。"""
    from unittest.mock import patch

    from tools.aipos_cli.two_phase_shell_factory import DRIVER_INSTANCE_ENV, execute_two_phase_verb

    monkeypatch.delenv(DRIVER_INSTANCE_ENV, raising=False)
    conn = _conn(tmp_path, [{"role": "advisor", "agent_instance": ADV_MAIN, "token": "fixture-a"},
                            {"role": "advisor", "agent_instance": f"advisor.{PROJECT}.h2", "token": "fixture-b"}])
    with patch("tools.aipos_cli.two_phase_shell_factory.GateClient") as gc:
        code, resp = execute_two_phase_verb(verb_base="lybra_audit_verdict", args_dict={"reviewed_task_id": "X", "verdict": "PASS"},
                                            connection_json_path=conn, role="advisor")
    _show(f"[件②·--token-role advisor 多实例] exit {code} {resp}")
    assert code == 1 and not gc.called and "禁取首个冒名" in resp["error"]


def test_item2_task_progress_identity_by_actor(tmp_path, monkeypatch):
    """task-progress --confirm 身份唯一口 progress_reporter_identity: --actor 持本角色凭据 = 按实例取; 否则按角色取(多实例拒)。"""
    from tools.aipos_cli.two_phase_shell_factory import (
        DRIVER_INSTANCE_ENV,
        DriverTokenError,
        load_role_token,
        progress_reporter_identity,
    )

    monkeypatch.delenv(DRIVER_INSTANCE_ENV, raising=False)
    e1, e2 = f"exec.{PROJECT}.h1", f"exec.{PROJECT}.h2"
    conn = _conn(tmp_path, [{"role": "executor", "agent_instance": e1, "token": "fixture-e1"},
                            {"role": "executor", "agent_instance": e2, "token": "fixture-e2"}])
    role, signer = progress_reporter_identity(connection_json_path=conn, actor=e2)
    assert (role, signer) == ("executor", e2)
    assert load_role_token(connection_json_path=conn, role=role, agent_instance=signer) == "fixture-e2"
    role, signer = progress_reporter_identity(connection_json_path=conn, actor=ADV_GHOST)
    assert (role, signer) == ("executor", None)
    with pytest.raises(DriverTokenError):
        load_role_token(connection_json_path=conn, role=role, agent_instance=signer)
    auditor_only = _conn(tmp_path / "a", [{"role": "auditor", "agent_instance": "audit.x.h1", "token": "fixture-au"}])
    assert progress_reporter_identity(connection_json_path=auditor_only, actor="audit.x.h1") == ("auditor", "audit.x.h1")
    with pytest.raises(ValueError):
        progress_reporter_identity(connection_json_path=_conn(tmp_path / "n", [{"role": "owner", "token": "fixture-o"}]), actor="x")


# ===========================================================================
# 棘轮: 角色类取首个凭据(resolve_role_from_connection)只许在薄壳工厂内; 签发者即 --actor 的薄壳只走唯一口
# ===========================================================================

def test_ratchet_role_class_first_token_only_inside_factory():
    """非测试 .py 中 `resolve_role_from_connection(` 调用只许在 two_phase_shell_factory.py(唯一口内部); 各命令薄壳禁自拼身份。
    queue rework / queue adopt 薄壳经 signer_role_for_actor 且把 --actor 作 signer_instance 交工厂。"""
    from tools.aipos_cli.runall_discovery import repo_files

    call = re.compile(r"\bresolve_role_from_connection\(")
    hits = []
    for rel in repo_files(REPO_ROOT):
        parts = rel.split("/")
        if not rel.endswith(".py") or "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py":
            continue
        for line in (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines():
            if call.search(line) and not line.lstrip().startswith("def "):
                hits.append(rel)
    _show(f"[棘轮] resolve_role_from_connection 调用点: {hits}")
    assert hits and set(hits) == {"tools/aipos_cli/two_phase_shell_factory.py"}, hits
    src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    for marker in ('getattr(args, "queue_command", None) == "rework"', 'getattr(args, "queue_command", None) == "adopt"'):
        start = src.index(marker)
        block = src[start:src.index("\n    if args.command", start + len(marker))]
        assert "signer_role_for_actor(" in block and "signer_instance=args.actor" in block, marker
        assert "resolve_driver_role_from_connection(" not in block and "load_owner_token(" not in block, marker


def test_docs_rework_usage_names_actor_instance():
    """件③ 文档: 命令说明与接入指南写明返工/收编按 --actor 实例签发(含可照抄示例)。"""
    readme = (REPO_ROOT / "tools/aipos_cli/README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs/mcp-agent-setup.md").read_text(encoding="utf-8")
    assert "lybra queue rework --task-id <卡ID> --actor <顾问实例>" in readme
    assert "lybra queue rework --task-id <card> --actor <advisor instance>" in guide
