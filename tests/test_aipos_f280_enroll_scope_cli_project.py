"""AIPOS-F280 — 签码与 Owner 凭据收口(F274 遗留 1–3)。

件① 签码权限: lybra_enroll_code_dry_run / _confirm / lybra_roles_enroll_code 声明 required_scope(owner_decision_record: 只 Owner 类与顾问类),
     门 scope 唯一读取口执法; 签发方角色类可签哪些角色类 = roles.schema enroll_code_issuance 声明表(缺省: 顾问只签执行 / 审计, Owner 全部),
     门侧与看板 role_choices 同读一份声明; 拒因码 / 文案声明化(中英双语)。
件② CLI --project: envelope mint --confirm / project grant sign --confirm / roles enroll-code 透传显式目标项目; 缺省行为不变。
件③ "*" 推断: Owner ["*"] 凭据缺显式目标 = 结构化 PROJECT_AMBIGUOUS 类拒因(带「加 --project」出口), 非门内部错误; 其他动词不回归。

验收(卡面 ★验收)①②③④⑥ 在本文件; ⑤ = F274 / F270 / F272 既有用例全量; ⑦⑧⑨ 见交回报告。
靶场: 临时 home 根三项目 A(proj-r2, 门注册表 / 签发凭据所在)/ B(proj-b)/ C(proj-c, Owner 多项目与 "*" 凭据所在)+ 本检出起的临时门子进程
(--home-root, 门进程 LYBRA_LANG=en: 验门侧新拒因英文零中日韩字符); 子进程只经 tools/aipos_cli/tests/fixture_procs.py 起停。
不碰真实治理根 / 真实门 / 真实工位; 凭据 / 令牌 / 码一律不打印原文。
"""
from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 临时治理仓 + 裸远端
from test_aipos_f270_board_enroll_code import CODE_RE, _cli, _start_gate  # noqa: E402  — 临时门 / CLI 进程内调用
from test_aipos_f274_owner_cross_project import _client_conn, _entry, _make_project  # noqa: E402  — F274 靶场构件(同形凭据条目)
from tools.aipos_cli.tests import fixture_procs  # noqa: E402
from tools.test_aipos_r2_enroll import _free_port, _seed_project  # noqa: E402

A, B, C = "proj-r2", "proj-b", "proj-c"
HOST = "f280h"
CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
#: 只读 / 执行 / 审计 / 规划 / 派审 / 自定义(执行类、审计类): 无 owner_decision_record = 不能签任何码
NO_SCOPE = (("copilot", None), ("executor", None), ("auditor", None), ("planner", None), ("owner-dispatch", None),
            ("kiwiops", "executor"), ("reviewx", "auditor"))
#: 顾问类(内建 + 自定义): 按声明表只签工位类
ADVISOR_CLASS = (("advisor", None), ("seatx", "advisor"))


def _show(text: str) -> None:
    print(CODE_RE.sub("LYBRAENROLL1.<已打码>", text), flush=True)


def _brief(resp: dict) -> str:
    keep = {k: resp.get(k) for k in ("ok", "error_code", "message") if k in resp}
    nxt = resp.get("suggested_next_action") if resp.get("error_code") == "ENROLL_CODE_ROLE_NOT_ISSUABLE" else None
    if nxt:
        keep["next"] = nxt
    return json.dumps(keep, ensure_ascii=False)[:1200]


# ===========================================================================
# 靶场
# ===========================================================================

@pytest.fixture(scope="module")
def rng(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("f280")
    home = tmp / "home"
    user_home = tmp / "user-home"
    user_home.mkdir()
    product = tmp / "product-app"
    product.mkdir()
    subprocess.run(["git", "init", "-q", str(product)], check=True)
    port = _free_port()
    gate_url = f"http://127.0.0.1:{port}"
    hub, _owner_token = _seed_project(home, gate_url)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"PYTHONPATH": str(REPO_ROOT), "HOME": str(user_home), "LYBRA_HOME_ROOT": str(home), "LYBRA_LANG": "en"})
    with pytest.MonkeyPatch.context() as mp:
        for key in [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]:
            mp.delenv(key)
        mp.setenv("HOME", str(user_home))
        mp.setenv("LYBRA_HOME_ROOT", str(home))
        from tools.aipos_cli.workspace_config import set_project_repos

        set_project_repos(home, A, {"app": str(product)}, default="app", dry_run=False)
        gov_b = _make_project(home, B, product)
        gov_c = _make_project(home, C, product)
        conn_path = hub / ".lybra" / "connection.json"
        conn = json.loads(conn_path.read_text(encoding="utf-8"))
        conn["tokens"][0]["expires_at"] = "2999-01-01T00:00:00Z"
        clients: dict[str, Path] = {"owner": _client_conn(tmp, "owner", conn["tokens"][0], gate_url)}
        for role, role_class in NO_SCOPE + ADVISOR_CLASS:
            entry, _tok = _entry(role, role_class, [A], f"f280-{role}")
            conn["tokens"].append(entry)
            clients[role] = _client_conn(tmp, role, entry, gate_url)
        conn_path.write_text(json.dumps(conn, indent=2), encoding="utf-8")
        # C 的门注册表: Owner 多项目凭据(无缺省)/ Owner "*"
        c_tokens = []
        for label, projects in (("owner-multi", [A, C]), ("owner-star", ["*"])):
            entry, _tok = _entry("owner", None, projects, f"f280-{label}")
            c_tokens.append(entry)
            clients[label] = _client_conn(tmp, label, entry, gate_url)
        c_conn = gov_c / ".lybra" / "connection.json"
        c_conn.parent.mkdir(parents=True, exist_ok=True)
        c_conn.write_text(json.dumps({"config_version": 1, "mcp": {"rpc_url": f"{gate_url}/mcp"}, "tokens": c_tokens}), encoding="utf-8")
        c_conn.chmod(0o600)
        init_governance_repo(home)
        gate = _start_gate(home, port, env, tmp / "gate.log")
        try:
            yield {"tmp": tmp, "home": home, "hub": hub.resolve(), "gate_url": gate_url, "gov_b": gov_b.resolve(),
                   "gov_c": gov_c.resolve(), "clients": clients, "mp": mp}
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


def _code_args(role: str, instance: str, **extra) -> dict:
    return {"role": role, "instance": instance, "owner_authorization_ref": "owner", "actor": "f280-fixture", **extra}


def _call(client, verb: str, args: dict) -> dict:
    """门应答(结构化); 门内部错误(无 structuredContent)= GateError 原样上抛 = 用例失败(验收④: 不得是门内部错误)。"""
    return client.call_tool(verb, args)


# ===========================================================================
# 声明与唯一读取口(件①: 门侧 / verb_contract / 看板同源)
# ===========================================================================

def test_declaration_single_source_and_board_choices(tmp_path):
    from tools.aipos_cli import board_enroll_code, i18n
    from tools.aipos_cli.board_enroll_code import board_declaration, role_choices
    from tools.aipos_cli.custom_roles import resolve_role_to_class, role_classes_in_group
    from tools.aipos_cli.enrollment import ENROLL_CODE_VERBS, enroll_issuance_declaration, issuable_role_classes
    from tools.aipos_cli.envelope_board import board_envelopes_declaration
    from tools.aipos_cli.verb_contract import get_verb_contract, who_holds_scope
    from tools.mcp_server import tools as gate
    from tools.schema_loader import get_all_role_names

    decl = enroll_issuance_declaration()
    scope = decl["required_scope"]
    contracts = {v: get_verb_contract(v)["required_scope"] for v in ENROLL_CODE_VERBS}
    holders = who_holds_scope(scope)
    table = {cls: issuable_role_classes(cls) for cls in get_all_role_names()}
    _show(f"[件①·声明] required_scope={scope} verb_contract={contracts} 持有该 scope 的角色={holders}\n"
          f"[件①·声明表 issuers] {json.dumps(decl['issuers'], ensure_ascii=False)}\n[件①·各签发方角色类可签] {table}")
    assert scope == "owner_decision_record" and set(contracts.values()) == {scope}
    assert set(holders) == {"owner", "advisor"}, "只 Owner 类与顾问类持有签码 scope"
    assert table["advisor"] == list(role_classes_in_group("workstation")) == ["executor", "auditor"]
    assert table["owner"] == get_all_role_names(), "Owner 类可签全部"
    assert all(table[cls] is None for cls in table if cls not in ("owner", "advisor")), "表外签发方不可签"
    # 门侧判定唯一: 三动词经 _enroll_issuer_denied → enrollment.enroll_issuer_refusal; scope 经 _verb_scope_allowed(与其他门动词同一处)
    src = inspect.getsource(gate)
    assert src.count("enroll_issuer_refusal(") == 1 and src.count("_enroll_issuer_denied(") == 3
    assert '_verb_scope_allowed(verb_name)' in inspect.getsource(gate._validate_enroll_code_args)
    assert '_verb_scope_allowed("lybra_enroll_code_confirm")' in inspect.getsource(gate.lybra_enroll_code_confirm)
    # 看板 role_choices 读同一声明: 看板签发凭据角色类的可签集合 ∩ 声明 board.role_groups(验收②)
    board = board_declaration()
    signer = resolve_role_to_class(str(board_envelopes_declaration()["token_role"]))
    expect = [r for r in get_all_role_names() if r in set(issuable_role_classes(signer))
              and r in {c for g in board["role_groups"] for c in role_classes_in_group(g)}]
    gov = tmp_path / "gov"
    (gov / ".lybra").mkdir(parents=True)
    (gov / ".lybra" / "connection.json").write_text(json.dumps({"config_version": 1, "tokens": []}), encoding="utf-8")
    choices = role_choices(gov)
    _show(f"[验收②·看板角色清单] 签发凭据角色类={signer} role_groups={board['role_groups']} → {choices}(由声明推导 {expect})")
    assert choices == expect and "owner" not in choices
    assert "issuable_role_classes(" in inspect.getsource(board_enroll_code.role_choices)
    assert "workstation" not in inspect.getsource(board_enroll_code.role_choices), "分组不再写死在代码"
    # 拒因文案双语块: 结构 / 占位一致, en 零中日韩字符
    block = i18n.owner_block("enroll_issuance")
    assert not i18n.block_problems(block) and not i18n.placeholder_problems(block)
    assert not [k for k, v in block["en"].items() if CJK.search(v)]


# ===========================================================================
# 验收① 临时 home 根门: 逐角色签码权限(门侧)
# ===========================================================================

def test_item1_gate_enforces_scope_and_issuance_table(rng):
    lines: list[str] = []
    refused_texts: list[str] = []
    # 无 scope 的凭据: 两阶段预演与单相动词都 SCOPE_DENIED
    for label, _cls in NO_SCOPE:
        client = _gate(rng, label)
        dry = _call(client, "lybra_enroll_code_dry_run", _code_args("executor", f"exec.{A}.{HOST}a"))
        single = _call(client, "lybra_roles_enroll_code", _code_args("executor", f"exec.{A}.{HOST}a"))
        conf = _call(client, "lybra_enroll_code_confirm", {"dry_run_token": "enrolldr_x", "owner_confirmation_token": "OWNER_CONFIRMED"})
        lines.append(f"--- {label}\n  签执行码预演: {_brief(dry)}\n  单相签码: {_brief(single)}\n  确认跳: {_brief(conf)}")
        for resp in (dry, single, conf):
            assert resp.get("ok") is False and resp.get("error_code") == "SCOPE_DENIED", (label, resp)
            assert "owner_decision_record" in resp.get("message", "")
    # 顾问类(内建 + 自定义): 签执行 / 审计码过; 签顾问 / 规划 / Owner 码按声明表拒
    for label, _cls in ADVISOR_CLASS:
        client = _gate(rng, label)
        ok_exec = _call(client, "lybra_enroll_code_dry_run", _code_args("executor", f"exec.{A}.{HOST}b"))
        ok_aud = _call(client, "lybra_enroll_code_dry_run", _code_args("auditor", f"audit.{A}.{HOST}b"))
        no_adv = _call(client, "lybra_enroll_code_dry_run", _code_args("advisor", f"advisor.{A}.{HOST}b"))
        no_plan = _call(client, "lybra_enroll_code_dry_run", _code_args("planner", f"planner.{A}.{HOST}b"))
        no_owner = _call(client, "lybra_enroll_code_dry_run", _code_args("owner", f"owner.{A}.{HOST}b"))
        lines.append(f"--- {label}\n  签执行码: ok={ok_exec.get('ok')}\n  签审计码: ok={ok_aud.get('ok')}\n  签顾问码: {_brief(no_adv)}\n"
                     f"  签规划码: {_brief(no_plan)}\n  签 Owner 码: {_brief(no_owner)}")
        assert ok_exec.get("ok") is True and ok_aud.get("ok") is True, (label, ok_exec, ok_aud)
        for resp in (no_adv, no_plan):
            assert resp.get("ok") is False and resp.get("error_code") == "ENROLL_CODE_ROLE_NOT_ISSUABLE", (label, resp)
            refused_texts += [resp["message"], resp["suggested_next_action"]]
        assert no_owner.get("ok") is False and no_owner.get("error_code") == "OWNER_CODE_REQUIRES_OWNER"
    # Owner: 全部角色类可签(预演)
    owner = _gate(rng, "owner")
    for role in ("executor", "auditor", "advisor", "planner", "owner", "copilot", "owner-dispatch"):
        resp = _call(owner, "lybra_enroll_code_dry_run", _code_args(role, f"{role}.{A}.{HOST}o"))
        lines.append(f"--- owner 签 {role} 码预演: ok={resp.get('ok')} {'' if resp.get('ok') else _brief(resp)}")
        assert resp.get("ok") is True, (role, resp)
    # 确认跳按确认方凭据复核, 被拒不消费预演令牌: Owner 预演顾问码 → 顾问确认拒(声明表)→ 执行确认拒(scope)→ Owner 确认出码
    dry = _call(owner, "lybra_enroll_code_dry_run", _code_args("advisor", f"advisor.{A}.{HOST}c"))
    by_adv = _call(_gate(rng, "advisor"), "lybra_enroll_code_confirm",
                   {"dry_run_token": dry["dry_run_token"], "owner_confirmation_token": "OWNER_CONFIRMED"})
    by_exec = _call(_gate(rng, "executor"), "lybra_enroll_code_confirm",
                    {"dry_run_token": dry["dry_run_token"], "owner_confirmation_token": "OWNER_CONFIRMED"})
    by_owner = _call(owner, "lybra_enroll_code_confirm", {"dry_run_token": dry["dry_run_token"], "owner_confirmation_token": "OWNER_CONFIRMED"})
    lines.append(f"--- 确认跳复核(Owner 预演顾问码)\n  顾问确认: {_brief(by_adv)}\n  执行确认: {_brief(by_exec)}\n"
                 f"  Owner 确认: ok={by_owner.get('ok')} code_id={by_owner.get('code_id')} role={by_owner.get('role')}")
    assert by_adv.get("error_code") == "ENROLL_CODE_ROLE_NOT_ISSUABLE" and by_exec.get("error_code") == "SCOPE_DENIED"
    assert by_owner.get("ok") is True and str(by_owner.get("self_contained_code", "")).startswith("LYBRAENROLL1.")
    # 顾问签执行码两阶段出码(放行面)
    adv = _gate(rng, "advisor")
    dry2 = _call(adv, "lybra_enroll_code_dry_run", _code_args("executor", f"exec.{A}.{HOST}d"))
    done2 = _call(adv, "lybra_enroll_code_confirm", {"dry_run_token": dry2["dry_run_token"], "owner_confirmation_token": "OWNER_CONFIRMED"})
    lines.append(f"--- 顾问两阶段签执行码: ok={done2.get('ok')} role={done2.get('role')} code_id={done2.get('code_id')}")
    assert done2.get("ok") is True and done2.get("role") == "executor"
    _show("[验收①·临时 home 根门(门进程 LYBRA_LANG=en)· 逐角色签码 · 门应答原文]\n" + "\n".join(lines))
    # 验收⑥: en 下新拒因(拒因与出口)零中日韩字符
    leaks = [t for t in refused_texts if CJK.search(t)]
    _show(f"[验收⑥·en 新拒因 {len(refused_texts)} 条, 含中日韩字符 {len(leaks)} 条]")
    assert refused_texts and not leaks, leaks


# ===========================================================================
# 验收④ "*" 凭据无显式目标 = 明确拒因, 非门内部错误; 其他动词不回归
# ===========================================================================

def test_item4_wildcard_without_target_is_clear_refusal(rng):
    from tools.aipos_cli.envelope_mint import build_mint_payload, mint_via_gate

    star = _gate(rng, "owner-star")
    multi = _gate(rng, "owner-multi")
    rows: list[str] = []
    texts: list[str] = []
    for label, client in (("owner-star", star), ("owner-multi", multi)):
        for verb, args in (("lybra_enroll_code_dry_run", _code_args("executor", f"exec.{C}.{HOST}s")),
                           ("lybra_project_status", {}), ("lybra_queue_list", {})):
            resp = _call(client, verb, args)  # 门内部错误 = GateError 上抛 = 用例失败
            rows.append(f"{label} {verb}(无显式目标): {_brief(resp)}")
            assert resp.get("ok") is False and resp.get("error_code") == "PROJECT_SCOPE_DENIED", (label, verb, resp)
            assert "PROJECT_AMBIGUOUS" in resp["message"] and "--project" in resp["message"], resp["message"]
            texts.append(resp["message"])
    # 不回归: "*" 凭据不需项目的动词照常; 显式目标照常(F274 放宽); 只给显式 workspace_root 签信封照常
    version = _call(star, "lybra_gate_version", {})
    explicit = _call(star, "lybra_enroll_code_dry_run", _code_args("executor", f"exec.{B}.{HOST}s", project=B))
    payload = build_mint_payload(policy_id="pol_f280_star_root", agent_or_role="executor", max_tasks=3, task_mode="code",
                                 expires_at="2099-01-01T00:00:00Z", decision_summary="f280 fixture", actor="owner")
    mint = mint_via_gate([payload], governance_root=rng["gov_b"], actor="owner", connection_json=str(rng["clients"]["owner-star"]),
                         token_role="owner")
    rows += [f"owner-star lybra_gate_version: ok={version.get('ok')}",
             f"owner-star 签码预演 project={B}: ok={explicit.get('ok')} governance_root={(explicit.get('preview') or {}).get('governance_root')}",
             f"owner-star 签信封(只给 workspace_root={B}): exit={mint['exit_code']} signed={[s['policy_id'] for s in mint['signed']]}"]
    _show("[验收④·\"*\" / 多项目凭据缺显式目标 · 门应答原文(门进程 LYBRA_LANG=en)]\n" + "\n".join(rows))
    assert version.get("ok") is True and explicit.get("ok") is True and mint["exit_code"] == 0 and mint["signed"]
    assert not [t for t in texts if CJK.search(t)], "验收⑥: en 下新出口零中日韩字符"


# ===========================================================================
# 验收③ CLI 三命令 --project(Owner 多项目无缺省凭据)
# ===========================================================================

def test_item3_cli_project_flag(rng, monkeypatch):
    home, gov_c = rng["home"], rng["gov_c"]
    conn = str(rng["clients"]["owner-multi"])
    neutral = rng["tmp"] / "neutral-cwd"
    neutral.mkdir(exist_ok=True)
    monkeypatch.chdir(neutral)
    for key in [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    lines: list[str] = []

    def run(label: str, *argv: str) -> tuple[int, str]:
        rc, out, err = _cli(*argv)
        text = out + err
        lines.append(f"$ lybra {' '.join(argv[:6])} …  [{label}]\n  rc={rc}\n  " + "\n  ".join(
            ln for ln in text.splitlines() if ln.strip() and not ln.startswith("签码凭据文件"))[:1500])
        return rc, text

    env_common = ["--max-tasks", "3", "--task-mode", "code", "--expires-at", "2099-01-01T00:00:00Z", "--decision-summary", "f280 cli",
                  "--connection-json", conn, "--confirm"]
    rc, text = run("envelope mint 不带 --project", "envelope", "mint", "--policy-id", "pol_f280_cli_none", "--agent-or-role", "executor",
                   "--workspace-root", str(gov_c), *env_common)
    assert rc == 1 and "PROJECT_AMBIGUOUS" in text and "--project" in text
    rc, text = run("envelope mint --project", "envelope", "mint", "--policy-id", "pol_f280_cli", "--agent-or-role", "executor",
                   "--project", C, *env_common)
    assert rc == 0 and "signed pol_f280_cli" in text, text
    assert (gov_c / "5_tasks" / "records" / "owner_decisions" / "envelope-pol_f280_cli.md").is_file()

    grant = ["project", "grant", "sign", C, "--home-root", str(home), "--grant-lane", "app", "--task-mode", "code",
             "--driver", f"advisor.{C}.{HOST}", "--host", HOST, "--max-tasks", "5", "--expires-at", "2099-01-01T00:00:00Z",
             "--connection-json", conn, "--confirm"]
    rc, text = run("project grant sign 不带 --project", *grant)
    assert rc != 0 and "PROJECT_AMBIGUOUS" in text and "--project" in text
    rc, text = run("project grant sign 名与 --project 不一致", *grant, "--project", B)
    assert rc == 2 and "--project" in text and B in text
    rc, text = run("project grant sign --project", *grant, "--project", C)
    assert rc == 0 and f"signed auth_{C}" in text, text

    enroll = ["roles", "--connection-json", conn, "enroll-code", "--role", "executor", "--instance", f"exec.{C}.{HOST}cli",
              "--owner-authorization-ref", f"auth_{C}", "--token-role", "owner", "--confirm"]
    rc, text = run("roles enroll-code 不带 --project", *enroll, "--governance-root", C)
    assert rc == 1 and "PROJECT_AMBIGUOUS" in text and "--project" in text
    rc, text = run("roles enroll-code --project", *enroll, "--project", C)
    assert rc == 0 and "LYBRAENROLL1." in text, text
    enrollments = json.loads((gov_c / ".lybra" / "enrollments.json").read_text(encoding="utf-8"))
    rec = [r for r in enrollments.values() if isinstance(r, dict) and r.get("instance") == f"exec.{C}.{HOST}cli"]
    lines.append(f"[码记录 {C}/.lybra/enrollments.json] " + json.dumps(
        [{k: r.get(k) for k in ("role", "instance", "governance_root", "status")} for r in rec], ensure_ascii=False))
    assert rec and rec[0]["governance_root"] == str(gov_c)
    _show("[验收③·CLI 三命令 · Owner 多项目无缺省凭据 [proj-r2, proj-c] · 原文(码已打码)]\n" + "\n".join(lines))
    # --help 给出该参数
    for argv in (("envelope", "mint", "--help"), ("project", "grant", "sign", "--help"), ("roles", "enroll-code", "--help")):
        rc, out, _err = _cli(*argv)
        assert "--project" in out and "AIPOS-F280" in out, argv


def test_onboarding_guide_step3_carries_project(tmp_path, monkeypatch):
    """件②: 向导第 3 步(grant sign)命令带 --project <项目>, PROJECT_AMBIGUOUS 出口文案中英双语(F277 双语块)。"""
    from tools.aipos_cli import i18n

    for code in ("zh", "en"):
        tpl = i18n.owner_block("onboarding_guide")[code]["step3"]["fail_ambiguous"]
        assert "--project {pq}" in tpl and (code == "zh" or not CJK.search(tpl))
    src = inspect.getsource(__import__("tools.aipos_cli.onboarding", fromlist=["x"]))
    assert '"--home-root", hq, "--project", pq]' in src and '"PROJECT_AMBIGUOUS": _t("step3.fail_ambiguous", pq=pq)' in src


def test_refusal_text_follows_user_language(monkeypatch):
    """门侧拒因按用户语言出声(i18n.resolve_language 唯一解析): zh 有中文, en 零中日韩字符; 语言设置非法 = 缺省语言(不变门内部错误)。"""
    from tools.aipos_cli.enrollment import enroll_issuer_refusal
    from tools.mcp_server.tools import _owner_voice, _target_exit_text

    refusal = enroll_issuer_refusal({"role": "advisor"}, "advisor", "advisor")
    assert refusal and refusal["code"] == "ENROLL_CODE_ROLE_NOT_ISSUABLE" and refusal["allowed"] == ["executor", "auditor"]
    assert enroll_issuer_refusal({"role": "advisor"}, "executor", "executor") is None
    assert enroll_issuer_refusal({"role": "copilot"}, "executor", "executor")["allowed"] is None
    out = {}
    for lang in ("zh", "en", "xx"):
        monkeypatch.setenv("LYBRA_LANG", lang)
        out[lang] = (_owner_voice(refusal["message"]), _owner_voice(refusal["next"]), _target_exit_text())
    _show("[拒因按用户语言]\n" + "\n".join(f"{k}: {v}" for k, v in out.items()))
    assert CJK.search(out["zh"][0]) and not any(CJK.search(t) for t in out["en"])
    assert out["xx"] == out["zh"], "非法语言设置 = 缺省语言"
