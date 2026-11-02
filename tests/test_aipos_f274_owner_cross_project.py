"""AIPOS-F274 — Owner 中央凭据跨项目: 看板写例外对 home 根内任一已注册项目(含刚由看板新建的)都能签授权 / 签码 / 设执行;
只放宽 Owner 角色类, 其余角色不变。

验收(卡面 ★验收):
  ① 临时 home 根两项目 A(proj-r2, 门 / 看板服务工作区 / 签发凭据所在)/ B(proj-b, 看板新建): Owner 会话在看板对 B 首签授权、签码、
     设执行全部成功(签发凭据 = A 的 Owner 凭据, projects 显式只列 A —— F272 遗留 1 实测被门拒的形态)。
  ② 同场景顾问 / 执行 / 审计(及规划 / 只读 / 派审 / 自定义)凭据对 B 调同类动词仍被拒(逐角色)。
  ③ B 未注册(目录存在但未注册)= 拒。 ④ 记录里「签发凭据所属项目」与「目标项目」两字段原文。
  另: 判定唯一执法点逐角色矩阵(放宽只对 project_scope=cross_project 角色类; 缺显式目标 / 开关关 / 显式根不符 = 不放宽); "*" 明确拒并带出口。
靶场: 临时 home + 本检出起的临时门子进程(随机端口, test_aipos_f270_board_enroll_code._start_gate)+ 看板子进程; pi = PATH 前置夹具替身
(只答 --version / --list-models)。不碰真实治理根 / 真实门 / 真实工位; 子进程只经 tools/aipos_cli/tests/fixture_procs.py 起停。
凭据 / 令牌 / 码一律不打印原文(码打码, 令牌只在本进程内用)。
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 临时治理仓 + 裸远端
from test_aipos_f270_board_enroll_code import CODE_RE, _Client, _cli, _start_gate, _wait_port  # noqa: E402  — 临时门 / 看板客户端构件
from tools.aipos_cli.tests import fixture_procs  # noqa: E402
from tools.test_aipos_r2_enroll import _free_port, _seed_project  # noqa: E402

A, B, C = "proj-r2", "proj-b", "proj-c"
UNREG = "proj-unreg"
HOST = "f274h"
GRANT = "/api/owner/project-grant"
EXEC = "/api/owner/project-execution"
ENROLL = "/api/owner/enroll-code"
PNEW = "/api/owner/projects/new"
ONBOARDING = "/owner/onboarding"
SETUP = "/owner/setup"
MODELS = ("fixprov/model-a",)
PI_SH = ('#!/bin/sh\ncase "$1" in --version) echo 0.85.1;; --list-models) printf "provider  model  context\\n'
         + "".join(f"{m.split('/')[0]}  {m.split('/')[1]}  100K\\n" for m in MODELS) + '";; *) echo "fixture pi: no run" >&2; exit 9;; esac\nexit 0\n')
#: 非 Owner 角色(内建 + 自定义): 逐角色断言未被放宽。自定义角色类 = 内建类(executor / advisor / auditor)。
NON_OWNER = (("advisor", None), ("executor", None), ("auditor", None), ("planner", None), ("copilot", None), ("owner-dispatch", None),
             ("kiwiops", "executor"), ("seatx", "advisor"), ("reviewx", "auditor"))
TOKEN_RE = re.compile(r'("preview_token": ")[^"]+(")')


def _show(text: str) -> None:
    print(TOKEN_RE.sub(r"\1[已打码]\2", CODE_RE.sub("LYBRAENROLL1.<已打码>", text)), flush=True)


def _dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _post(client: _Client, path: str, body: dict, cookie: str | None) -> tuple[int, dict]:
    status, text, _sc, _loc = client.request("POST", path, body, cookie=cookie)
    return status, json.loads(text)


def _section(page: str, pattern: str) -> str:
    m = re.search(pattern, page, re.S)
    assert m, pattern
    return m.group(0)


def _entry(role: str, role_class: str | None, projects: list[str] | None, label: str) -> tuple[dict, str]:
    """门注册表条目(与 service_mode._role_token_entry 同形; 作用域由门按角色(类)调用时解析)。返回 (条目, 令牌)——令牌只在本进程内用。"""
    from tools.aipos_cli.confirm_client import token_fingerprint
    from tools.schema_loader import get_role_scopes

    token = secrets.token_urlsafe(32)
    entry = {"role": role, "token": token, "token_ref": f"svc-f274-{label}", "fingerprint": token_fingerprint(token),
             "scopes": list(get_role_scopes(role_class or role))}
    if role_class:
        entry["role_class"] = role_class
    if projects is not None:
        entry["projects"] = list(projects)
        entry["projects_enforced"] = True
    return entry, token


def _client_conn(tmp: Path, label: str, entry: dict, gate_url: str) -> Path:
    """客户端侧凭据文件(只含这一张, load_role_token 同角色唯一); 门注册表另在各项目 connection.json。"""
    path = tmp / "clients" / label / ".lybra" / "connection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"config_version": 1, "mcp": {"rpc_url": f"{gate_url}/mcp"}, "tokens": [entry]}), encoding="utf-8")
    path.chmod(0o600)
    return path


def _make_project(home: Path, name: str, product: Path) -> Path:
    from tools.aipos_cli.workspace_config import scaffold_project, set_project_repos

    root = scaffold_project(home, name, registered_by="f274-fixture")
    set_project_repos(home, name, {"app": str(product)}, default="app", dry_run=False)
    return root


# ===========================================================================
# 判定唯一执法点: 逐角色矩阵(无门, 只调 ProjectEnforcer)
# ===========================================================================

@pytest.fixture()
def home2(tmp_path, monkeypatch):
    """两已注册项目 A / B + 未注册目录(只有队列根 / 只有 project.json 各一)。"""
    home = tmp_path / "home"
    product = tmp_path / "prod"
    product.mkdir()
    subprocess.run(["git", "init", "-q", str(product)], check=True)
    for key in [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path / "uh"))
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    _make_project(home, A, product)
    _make_project(home, B, product)
    (home / UNREG / "5_tasks" / "queue").mkdir(parents=True)
    (home / "proj-pjonly").mkdir()
    (home / "proj-pjonly" / "project.json").write_text(json.dumps({"project": "proj-pjonly"}), encoding="utf-8")
    return home


def test_enforcer_matrix_only_owner_class_relaxed(home2, monkeypatch):
    from tools.project_resolution import ProjectEnforcer, role_is_cross_project
    from tools.schema_loader import get_all_role_names

    roles = [(r, None) for r in get_all_role_names()] + [(r, c) for r, c in NON_OWNER if c] + [("ownerx", "owner")]
    rows, relaxed = [], []
    for role, role_class in roles:
        tok = {"role": role, **({"role_class": role_class} if role_class else {}), "projects": [A]}
        explicit = ProjectEnforcer.check_project_scope(tok, B, explicit=True, home_root=home2)
        implicit = ProjectEnforcer.check_project_scope(tok, B, explicit=False, home_root=home2)
        rooted = ProjectEnforcer.check_project_scope(tok, B, explicit=True, target_root=home2 / B, home_root=home2)
        own = ProjectEnforcer.check_project_scope(tok, A, explicit=False, home_root=home2)
        rows.append(f"{role:<15} class={role_class or role:<15} 显式B={explicit[0]!s:<5} 未显式B={implicit[0]!s:<5} "
                    f"显式根B={rooted[0]!s:<5} 本项目A={own[0]}")
        assert own[0] is True, "本项目照常放行"
        assert implicit[0] is False, f"{role}: 缺显式目标不放宽(不猜)"
        assert explicit[0] is rooted[0] is role_is_cross_project(tok), f"{role}: 放宽只对 Owner 角色类"
        if explicit[0]:
            relaxed.append(role)
    _show("[矩阵·凭据 projects=[proj-r2], 目标 proj-b(已注册)]\n" + "\n".join(rows))
    assert relaxed == ["owner", "ownerx"], relaxed
    owner = {"role": "owner", "projects": [A]}
    cases = {
        "未注册(只有队列根)": ProjectEnforcer.check_project_scope(owner, UNREG, explicit=True, home_root=home2),
        "未注册(只有 project.json)": ProjectEnforcer.check_project_scope(owner, "proj-pjonly", explicit=True, home_root=home2),
        "不存在的项目": ProjectEnforcer.check_project_scope(owner, "proj-none", explicit=True, home_root=home2),
        "显式根不是 <home>/<目标>": ProjectEnforcer.check_project_scope(owner, B, explicit=True, target_root=home2 / A, home_root=home2),
        "缺显式目标": ProjectEnforcer.check_project_scope(owner, B, explicit=False, home_root=home2),
    }
    _show("[Owner 不放宽的情形 · 拒因原文]\n" + "\n".join(f"{k}: {v}" for k, v in cases.items()))
    assert all(v[0] is False for v in cases.values())
    star = ProjectEnforcer.check_project_scope({"role": "advisor", "projects": ["*"]}, B, explicit=True, home_root=home2)
    star_owner = ProjectEnforcer.check_project_scope({"role": "owner", "projects": ["*"]}, B, explicit=True, home_root=home2)
    _show(f"[\"*\" · 非 Owner 拒因原文] {star}\n[\"*\" · Owner 显式目标(走放宽, 与 \"*\" 无关)] {star_owner}")
    assert star[0] is False and '"*" is not a wildcard' in star[1] and "lybra serve rotate --project" in star[1]
    assert star_owner == (True, None)
    import tools.project_resolution as pr

    real = pr.owner_explicit_target_declaration
    monkeypatch.setattr(pr, "owner_explicit_target_declaration", lambda: {**real(), "enabled": False})
    off = ProjectEnforcer.check_project_scope(owner, B, explicit=True, home_root=home2)
    _show(f"[开关关(enabled=false)· Owner 显式 B] {off}")
    assert off[0] is False and "disabled" in off[1]


def test_declaration_and_single_reader():
    """规则与开关在 roles.schema 声明并带缺省; 门联合注册表的跨项目角色判定委托同一实现(禁第二份)。"""
    import inspect

    from tools.mcp_server import http_sse
    from tools.project_resolution import owner_explicit_target_declaration

    decl = owner_explicit_target_declaration()
    _show("[roles.schema project_scope_semantics.owner_explicit_target]\n" + _dump(decl))
    assert decl["enabled"] is True and decl["wildcard"]["treatment"] == "refuse"
    assert decl["trace_fields"] == ["credential_project", "target_project"]
    assert "role_is_cross_project(entry)" in inspect.getsource(http_sse._role_is_cross_project)
    tools_src = (REPO_ROOT / "tools" / "mcp_server" / "tools.py").read_text(encoding="utf-8")
    assert "if target_project not in [str(p) for p in projects]" not in tools_src, "显式 workspace_root 第二份成员判定已收口"
    assert tools_src.count("ProjectEnforcer.check_project_scope(") == 3


# ===========================================================================
# 临时门靶场: A = proj-r2(签发凭据所在), B = proj-b, C = proj-c(多项目凭据的另一项目), 未注册目录
# ===========================================================================

@pytest.fixture(scope="module")
def rng(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("f274")
    home = tmp / "home"
    user_home = tmp / "user-home"
    user_home.mkdir()
    bindir = tmp / "bin"
    bindir.mkdir()
    (bindir / "pi").write_text(PI_SH, encoding="utf-8")
    (bindir / "pi").chmod(0o755)
    product = tmp / "product-app"
    product.mkdir()
    subprocess.run(["git", "init", "-q", str(product)], check=True)
    port = _free_port()
    gate_url = f"http://127.0.0.1:{port}"
    hub, owner_token = _seed_project(home, gate_url)
    path_env = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"PYTHONPATH": str(REPO_ROOT), "HOME": str(user_home), "LYBRA_HOME_ROOT": str(home), "PATH": path_env})
    with pytest.MonkeyPatch.context() as mp:
        for key in [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]:
            mp.delenv(key)
        mp.setenv("HOME", str(user_home))
        mp.setenv("LYBRA_HOME_ROOT", str(home))
        mp.setenv("PATH", path_env)
        from tools.aipos_cli.workspace_config import set_project_repos

        set_project_repos(home, A, {"app": str(product)}, default="app", dry_run=False)
        gov_b = _make_project(home, B, product)
        gov_c = _make_project(home, C, product)
        (home / UNREG / "5_tasks" / "queue").mkdir(parents=True)
        # A 的门注册表: Owner 凭据显式只列 A(serve rotate --project 形态 = F272 遗留 1 被拒形态)+ 各非 Owner 角色(同只列 A)
        conn_path = hub / ".lybra" / "connection.json"
        conn = json.loads(conn_path.read_text(encoding="utf-8"))
        conn["tokens"][0]["projects"] = [A]
        conn["tokens"][0]["projects_enforced"] = True
        clients: dict[str, Path] = {"owner": _client_conn(tmp, "owner", conn["tokens"][0], gate_url)}
        for role, role_class in NON_OWNER:
            entry, _tok = _entry(role, role_class, [A], role)
            conn["tokens"].append(entry)
            clients[role] = _client_conn(tmp, role, entry, gate_url)
        conn_path.write_text(json.dumps(conn, indent=2), encoding="utf-8")
        # C 的门注册表: Owner 多项目凭据(无缺省 = 原 PROJECT_AMBIGUOUS)/ Owner "*" / 顾问 "*"
        variants = {"owner-multi": ("owner", None, [A, C]), "owner-star": ("owner", None, ["*"]), "advisor-star": ("advisor", None, ["*"])}
        c_tokens = []
        for label, (role, role_class, projects) in variants.items():
            entry, _tok = _entry(role, role_class, projects, label)
            c_tokens.append(entry)
            clients[label] = _client_conn(tmp, label, entry, gate_url)
        c_conn = gov_c / ".lybra" / "connection.json"
        c_conn.parent.mkdir(parents=True, exist_ok=True)
        c_conn.write_text(json.dumps({"config_version": 1, "mcp": {"rpc_url": f"{gate_url}/mcp"}, "tokens": c_tokens}), encoding="utf-8")
        c_conn.chmod(0o600)
        init_governance_repo(home)
        gate = _start_gate(home, port, env, tmp / "gate.log")
        try:
            yield {"tmp": tmp, "home": home, "hub": hub.resolve(), "owner_token": owner_token, "gate_url": gate_url, "env": env,
                   "product": product, "gov_b": gov_b.resolve(), "gov_c": gov_c.resolve(), "clients": clients}
        finally:
            fixture_procs.reap(gate)
            assert not fixture_procs.tagged(gate), "临时门已整组回收"


def _gate(rng, label: str):
    from tools.aipos_cli.confirm_client import GateClient
    from tools.aipos_cli.two_phase_shell_factory import load_role_token

    path = rng["clients"][label]
    role = json.loads(path.read_text(encoding="utf-8"))["tokens"][0]["role"]
    client = GateClient(rng["gate_url"], load_role_token(connection_json_path=str(path), role=role), timeout=60.0)
    client.initialize()
    return client


def _envelope_payload(policy_id: str) -> dict:
    from tools.aipos_cli.envelope_mint import build_mint_payload

    return build_mint_payload(policy_id=policy_id, agent_or_role="executor", max_tasks=3, task_mode="code",
                              expires_at="2099-01-01T00:00:00Z", decision_summary="f274 fixture", actor="owner")


def _mint(rng, label: str, payload: dict, root: Path, project: str | None) -> dict:
    from tools.aipos_cli.envelope_mint import mint_via_gate

    role = json.loads(rng["clients"][label].read_text(encoding="utf-8"))["tokens"][0]["role"]
    return mint_via_gate([payload], governance_root=root, actor="owner", connection_json=str(rng["clients"][label]), token_role=role,
                         project=project)


def _enroll_args(rng, project: str | None, root: Path | str | None, role: str = "executor", instance: str | None = None) -> dict:
    args = {"role": role, "instance": instance or f"exec.{B}.{HOST}x", "owner_authorization_ref": "owner", "actor": "owner"}
    if project:
        args["project"] = project
    if root:
        args["governance_root"] = str(root)
    return args


def _brief(resp: dict) -> str:
    keep = {k: resp.get(k) for k in ("ok", "error_code", "message") if k in resp}
    if not keep.get("message"):
        errs = resp.get("errors") or []
        keep["errors"] = [{"category": e.get("category"), "message": e.get("message")} for e in errs if isinstance(e, dict)]
    return json.dumps(keep, ensure_ascii=False)[:900]


def test_item2_non_owner_roles_still_refused_for_b(rng):
    """② 逐角色: 非 Owner 凭据(projects=[A])对 B 签信封 / 签授权 / 签码 —— 带显式 project 与只带显式根两种调法都拒。"""
    from tools.aipos_cli import autonomy_policy as ap

    gov_b = rng["gov_b"]
    lines = []
    for role, _cls in NON_OWNER:
        rep = _mint(rng, role, _envelope_payload(f"pol_f274_{role.replace('-', '_')}"), gov_b, B)
        rep_root = _mint(rng, role, _envelope_payload(f"pol_f274_{role.replace('-', '_')}_r"), gov_b, None)
        block = ap.authorization_block(gov_b, action="grant", lanes=[{"repo": "app", "task_modes": ["code"], "drivers": [f"advisor.{B}.{HOST}"]}],
                                       hosts=[HOST], launch_harnesses=[], zones_open=[], max_tasks=5, expires_at="2099-01-01T00:00:00Z")
        grant = {"decision_id": f"grant-{block['authorization_id']}-r0001-{role}", "project_authorization": block, "actor": "owner",
                 "decided_by_ref": "owner"}
        rep_grant = _mint(rng, role, grant, gov_b, B)
        client = _gate(rng, role)
        enr = client.call_tool("lybra_enroll_code_dry_run", _enroll_args(rng, B, gov_b))
        enr_root = client.call_tool("lybra_enroll_code_dry_run", _enroll_args(rng, None, gov_b))
        lines.append(f"--- {role}\n  签信封(project=B): exit={rep['exit_code']} {str(rep['error'])[:300]}\n"
                     f"  签信封(只给 workspace_root=B): exit={rep_root['exit_code']} {str(rep_root['error'])[:300]}\n"
                     f"  首签授权(project=B): exit={rep_grant['exit_code']} {str(rep_grant['error'])[:300]}\n"
                     f"  签码预演(project=B): {_brief(enr)}\n  签码预演(只给 governance_root=B): {_brief(enr_root)}")
        assert rep["exit_code"] == 1 and not rep["signed"] and "PROJECT_SCOPE_DENIED" in str(rep["error"]), (role, rep["error"])
        assert rep_root["exit_code"] == 1 and not rep_root["signed"], (role, rep_root["error"])
        assert rep_grant["exit_code"] == 1 and not rep_grant["signed"] and "PROJECT_SCOPE_DENIED" in str(rep_grant["error"])
        assert enr.get("ok") is False and "PROJECT_SCOPE_DENIED" in json.dumps(enr), (role, enr)
        assert enr_root.get("ok") is False and "PROJECT_SCOPE_DENIED" in json.dumps(enr_root), (role, enr_root)
    _show("[②·非 Owner 凭据(projects=[proj-r2])对 proj-b 同类动词 · 门拒因原文]\n" + "\n".join(lines))
    assert not (gov_b / "5_tasks" / "records" / "owner_decisions").exists() or not any(
        (gov_b / "5_tasks" / "records" / "owner_decisions").iterdir()), "B 无任何决策记录落盘"
    # 非 Owner 凭据为本项目签 Owner 类角色码 = 拒(否则可自签 Owner 码兑换出 Owner 凭据, 绕过「只放宽 Owner」)
    adv = _gate(rng, "advisor").call_tool("lybra_enroll_code_dry_run", _enroll_args(rng, None, rng["hub"], role="owner",
                                                                                     instance=f"owner.{A}.{HOST}"))
    _show(f"[②·顾问凭据为本项目签 owner 角色码] {_brief(adv)}")
    assert adv.get("ok") is False and "OWNER_CODE_REQUIRES_OWNER" in json.dumps(adv)
    # 非 Owner 凭据为本项目签码照常(放行面不变)
    own = _gate(rng, "advisor").call_tool("lybra_enroll_code_dry_run", _enroll_args(rng, None, rng["hub"], instance=f"exec.{A}.{HOST}x"))
    _show(f"[②·顾问凭据为本项目(proj-r2)签执行码预演] ok={own.get('ok')} governance_root={(own.get('preview') or {}).get('governance_root')}")
    assert own.get("ok") is True


def test_item2_star_and_ambiguous_variants(rng):
    """"*" 明确拒(非 Owner 带出口); Owner 多项目 / "*" 凭据带显式目标 = 走放宽成功; 缺显式目标仍照现行解析(不猜)。"""
    gov_b = rng["gov_b"]
    adv = _mint(rng, "advisor-star", _envelope_payload("pol_f274_advstar"), gov_b, B)
    multi = _mint(rng, "owner-multi", _envelope_payload("pol_f274_multi"), gov_b, B)
    star = _mint(rng, "owner-star", _envelope_payload("pol_f274_star"), gov_b, B)
    multi_implicit = _mint(rng, "owner-multi", _envelope_payload("pol_f274_multi_implicit"), gov_b, None)
    _show(f"[\"*\"·顾问凭据 project=B] exit={adv['exit_code']} {adv['error']}\n"
          f"[Owner 多项目凭据 [proj-r2, proj-c](无缺省)project=B] exit={multi['exit_code']} signed={multi['signed']} {multi['error']}\n"
          f"[Owner \"*\" 凭据 project=B] exit={star['exit_code']} signed={star['signed']} {star['error']}\n"
          f"[Owner 多项目凭据 缺 project 参数(只给 workspace_root)] exit={multi_implicit['exit_code']} {multi_implicit['error']}")
    assert adv["exit_code"] == 1 and "is not a wildcard" in str(adv["error"]) and "lybra serve rotate --project" in str(adv["error"])
    assert multi["exit_code"] == 0 and multi["signed"] and star["exit_code"] == 0 and star["signed"]
    assert multi_implicit["exit_code"] == 1 and "PROJECT_AMBIGUOUS" in str(multi_implicit["error"]), "缺显式目标不猜"


def test_item3_unregistered_target_refused(rng):
    """③ 目录存在但未注册(无 project.json)= 拒: 显式 project / 显式 workspace_root / 显式 governance_root 三种调法。"""
    unreg = rng["home"] / UNREG
    rep = _mint(rng, "owner", _envelope_payload("pol_f274_unreg"), unreg, UNREG)
    rep_root = _mint(rng, "owner", _envelope_payload("pol_f274_unreg_r"), unreg, None)
    client = _gate(rng, "owner")
    enr = client.call_tool("lybra_enroll_code_dry_run", _enroll_args(rng, None, unreg, instance=f"exec.{UNREG}.{HOST}"))
    enr_p = client.call_tool("lybra_enroll_code_dry_run", _enroll_args(rng, UNREG, None, instance=f"exec.{UNREG}.{HOST}"))
    _show(f"[③·未注册 {UNREG}(只有队列根)· Owner 凭据]\n  签信封(project=未注册): exit={rep['exit_code']} {rep['error']}\n"
          f"  签信封(只给 workspace_root): exit={rep_root['exit_code']} {rep_root['error']}\n"
          f"  签码预演(governance_root=未注册): {_brief(enr)}\n  签码预演(project=未注册): {_brief(enr_p)}")
    assert rep["exit_code"] == 1 and "is not a registered project" in str(rep["error"])
    assert rep_root["exit_code"] == 1 and not rep_root["signed"]
    assert enr.get("ok") is False and "UNKNOWN_GOVERNANCE_ROOT" in json.dumps(enr)
    assert enr_p.get("ok") is False and "PROJECT_SCOPE_DENIED" in json.dumps(enr_p)
    assert not (unreg / "5_tasks" / "records").exists()


def test_item1_4_board_end_to_end_for_new_project(rng):
    """① 看板子进程(服务工作区 = A, 签发凭据 = A 的 Owner 凭据, projects 只列 A): 新建 B → 签三张码 → 兑换 → 首签授权 → 设执行方式 →
    设模型 全部成功。④ B 的 Owner 决定记录 / 码记录 / 接入日志带 credential_project / target_project。顾问会话对 B 首签 = 拒。"""
    home, hub, tmp = rng["home"], rng["hub"], rng["tmp"]
    project = "proj-new"
    adv, sub, aud = f"advisor.{project}.{HOST}", f"exec.{project}.{HOST}", f"audit.{project}.{HOST}"
    port = _free_port()
    argv = [sys.executable, "-m", "web.board.app", "--host", "127.0.0.1", "--port", str(port), "--repo-root", str(hub), "--no-prewarm"]
    hub_conn = json.loads((hub / ".lybra" / "connection.json").read_text(encoding="utf-8"))
    advisor_token = next(t["token"] for t in hub_conn["tokens"] if t["role"] == "advisor")
    with fixture_procs.running(argv, env=dict(rng["env"]), cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        _wait_port(port, proc)
        client = _Client(port)
        owner = client.otc_login(rng["owner_token"])
        advisor = client.otc_login(advisor_token)
        steps: list[str] = [f"看板子进程 pid={proc.pid} 127.0.0.1:{port}(服务工作区 = {hub.name}; 签发凭据 = {hub.name} 的 Owner 凭据, "
                            f"projects={hub_conn['tokens'][0]['projects']}); 临时门 {rng['gate_url']}"]

        def two_phase(path: str, body: dict, label: str) -> dict:
            status, pre = _post(client, path, {**body, "stage": "preview"}, owner)
            assert status == 200 and pre["ok"], (label, pre)
            status, done = _post(client, path, {**body, "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
            assert status == 200 and done["ok"], (label, _dump(done))
            steps.append(f"{label}: 预演 HTTP 200 → 确认 HTTP 200")
            return done

        def rows() -> dict[str, str]:
            page = client.page(ONBOARDING, owner)
            proj = _section(page, rf'<section class="rp-ob-project" id="{project}".*?</section>')
            return dict(re.findall(r'data-item="(\w+)" data-state="(\w+)"', proj))

        client.page(SETUP, owner)
        done = two_phase(PNEW, {"workspace": project, "code_repo": str(rng["product"])}, "① 新建项目 proj-new(写例外④)")
        gov = Path(done["created"]["governance_root"])
        codes, issued = {}, {}
        for role, inst in (("advisor", adv), ("executor", sub), ("auditor", aud)):
            got = two_phase(ENROLL, {"workspace": project, "role": role, "instance": inst}, f"② 签注册码 {role} {inst}(写例外⑤)")
            codes[role] = got["code"]["paste_text"]
            issued[role] = got["code"]["code_id"]

        def redeem(role: str, workspace: Path, *extra: str) -> None:
            argv_enroll = shlex.split(codes[role].replace("<工位目录或治理根>", str(workspace))) + list(extra)
            rc, out, err = _cli(*argv_enroll[1:])
            steps.append(f"③ 测试内兑换 {role}: rc={rc} {'; '.join(ln.strip() for ln in (out + err).splitlines() if '✓' in ln or 'Error' in ln)[:300]}")
            assert rc == 0, out + err

        redeem("advisor", gov, "--harness", "codex")
        redeem("executor", gov, "--executor-mode", "subagent", "--harness", "codex")
        page = client.page(ONBOARDING, owner)
        prefill = json.loads(html.unescape(re.search(rf'id="{project}"[^>]*data-ps-setup="([^"]*)"', page).group(1)))["grant"]
        body = {k: prefill[k] for k in ("lanes", "task_modes", "drivers", "hosts", "launch_harnesses", "zones_open", "max_tasks", "expires_at")}
        # 顾问会话对新项目首签 = 拒(看板写例外只给 Owner 会话; 不触门)
        status, refused = _post(client, GRANT, {"workspace": project, **body, "stage": "preview"}, advisor)
        steps.append(f"②' 顾问会话预演首签: HTTP {status} {refused.get('error')} {refused.get('message')}")
        assert status == 403 and not refused.get("ok")
        signed = two_phase(GRANT, {"workspace": project, **body}, "④ 首签项目授权(写例外⑥)")
        ws = tmp / "ws-f274-auditor"
        ws.mkdir()
        redeem("auditor", ws)
        two_phase(EXEC, {"workspace": project, "action": "execution", "subagent_executor": sub, "auditor": aud}, "⑤ 设执行方式(写例外⑦)")
        page = client.page(ONBOARDING, owner)
        setup = json.loads(html.unescape(re.search(rf'id="{project}"[^>]*data-ps-setup="([^"]*)"', page).group(1)))
        two_phase(EXEC, {"workspace": project, "action": "model", "model": setup["model"]["models"][0]}, "⑥ 设 pi 模型(写例外⑦)")
        final = rows()
        steps.append(f"   清单(终): {final}")
        _show("[①·看板端到端(签发凭据只列 proj-r2, 目标 = 看板新建的 proj-new)原文]\n" + "\n".join(steps))
        for key in ("governance_root", "advisor", "executor", "auditor", "envelope", "pi_model"):
            assert final[key] == "ok", (key, final)
        # ④ 追溯字段原文: Owner 决定记录 frontmatter / 码记录 / 接入日志 create 行 / 看板确认应答
        decision = gov / "5_tasks" / "records" / "owner_decisions" / f"grant-auth_{project}-r0001.md"
        head = [ln for ln in decision.read_text(encoding="utf-8").splitlines() if ln.split(":")[0] in
                ("record_type", "decision_id", "captured_by", "capture_surface", "credential_project", "target_project")]
        enrollments = json.loads((gov / ".lybra" / "enrollments.json").read_text(encoding="utf-8"))
        rec = {k: enrollments[issued["executor"]].get(k) for k in ("role", "instance", "governance_root", "credential_project", "target_project", "by")}
        trail = [ln for p in gov.rglob("*.md") if "enroll" in p.name for ln in p.read_text(encoding="utf-8").splitlines()
                 if "  create  " in ln and issued["executor"] in ln]
        _show(f"[④·Owner 决定记录 {decision.relative_to(home)}(节选)]\n" + "\n".join(head)
              + f"\n[④·确认应答 signed] {_dump(signed['signed'])}\n[④·码记录 {gov.name}/.lybra/enrollments.json · {issued['executor']}] {_dump(rec)}"
              + "\n[④·接入日志 create 行]\n" + "\n".join(trail))
        assert f"credential_project: {A}" in head and f"target_project: {project}" in head
        assert signed["signed"][0]["credential_project"] == A and signed["signed"][0]["target_project"] == project
        assert rec["credential_project"] == A and rec["target_project"] == project and rec["governance_root"] == str(gov)
        assert trail and f"credential_project={A}" in trail[0] and f"target_project={project}" in trail[0]
    assert proc.poll() is not None and not fixture_procs.tagged(proc), "看板子进程已整组回收"


def test_item4_same_project_records_unchanged(rng):
    """④ 反面: 凭据所属项目 = 目标项目时不写追溯字段(记录与旧版逐字相同); 客户端自带的追溯字段被门丢弃(防伪造)。"""
    payload = {**_envelope_payload("pol_f274_same"), "credential_project": "forged-x", "target_project": "forged-y"}
    rep = _mint(rng, "owner", payload, rng["hub"], A)
    rec = rng["hub"] / "5_tasks" / "records" / "owner_decisions" / "envelope-pol_f274_same.md"
    head = [ln for ln in rec.read_text(encoding="utf-8").splitlines() if ln.split(":")[0] in ("decision_id", "credential_project", "target_project")]
    _show(f"[④·同项目签发(A→A, 客户端自带伪造追溯字段)] exit={rep['exit_code']} 记录节选: {head}")
    assert rep["exit_code"] == 0 and head == ["decision_id: envelope-pol_f274_same"]
