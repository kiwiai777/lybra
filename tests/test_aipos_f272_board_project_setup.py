"""AIPOS-F272 — 看板写例外⑥「首签项目授权」与⑦「执行方式 / pi 模型」(产品化 P3 收尾)。

验收(卡面 ★验收):
  ① 临时根上件①②各自预演 JSON + 确认后记录 / project.json diff 原文 + 审计日志行。
  ② 六类令牌核验对两条新路由各一用例。 ③ 非 Owner 拒。 ④ 非法输入预演拒(实例未接入 / 子 agent 执行者非 subagent 模式 / 已有在用授权)原文。
  ⑤ 既有五写例外回归(路由计数 / 写例外清单只追加; 各自用例在全量里全过)。 ⑥ 件④端到端走查原文(只用看板: 新建项目 → 签三张注册码 →
     测试内兑换 → 首签授权 → 设执行方式 → 设模型)。 ⑦ 接入页中 / 英关键段原文。
靶场: 临时 home(枢纽项目 proj-r2 = 看板服务工作区 + 签发凭据库; 构件 tools/test_aipos_r2_enroll._seed_project)+ 本检出起的临时门子进程
(随机端口, test_aipos_f270_board_enroll_code._start_gate)+ 看板(进程内 / 子进程)。pi = PATH 前置的夹具替身(只答 --version / --list-models,
不起真实 pi)。不碰真实治理根 / 真实门 / 真实工位; 子进程只经 tools/aipos_cli/tests/fixture_procs.py 起停。凭据 / 令牌 / 码一律打码。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f95_harness_launch as f95  # noqa: E402  — land 事件写法唯一来源(_land)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 临时治理仓 + 裸远端
from test_aipos_f270_board_enroll_code import CODE_RE, _Client, _cli, _start_gate, _wait_port  # noqa: E402  — 临时门 / 看板客户端构件
from tools.aipos_cli.tests import fixture_procs  # noqa: E402
from tools.test_aipos_r2_enroll import _free_port, _seed_project  # noqa: E402

GRANT = "/api/owner/project-grant"
EXEC = "/api/owner/project-execution"
ENROLL = "/api/owner/enroll-code"
PNEW = "/api/owner/projects/new"
ONBOARDING = "/owner/onboarding"
SETUP = "/owner/setup"
HOST = "f272h"
MODELS = ("fixprov/model-a", "fixprov/model-b")
OWNER2_TOK = "owner2-f272-test-token-QRS"
ADVISOR_TOK = "advisor-f272-test-token-ABC"
TOKEN_RE = re.compile(r'("preview_token": ")[^"]+(")')
PI_SH = ('#!/bin/sh\ncase "$1" in --version) echo 0.85.1;; --list-models) printf "provider  model  context\\n'
         + "".join(f"{m.split('/')[0]}  {m.split('/')[1]}  100K\\n" for m in MODELS) + '";; *) echo "fixture pi: no run" >&2; exit 9;; esac\nexit 0\n')


def _show(text: str) -> None:
    print(TOKEN_RE.sub(r"\1[已打码]\2", CODE_RE.sub("LYBRAENROLL1.<已打码>", text)), flush=True)


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _post(client: _Client, path: str, body: dict, cookie: str | None, headers: dict | None = None) -> tuple[int, dict]:
    status, text, _sc, _loc = client.request("POST", path, body, cookie=cookie, headers=headers)
    return status, json.loads(text)


def _audit(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()] if path.is_file() else []


def _section(page: str, pattern: str) -> str:
    m = re.search(pattern, page, re.S)
    assert m, pattern
    return m.group(0)


def _row(page: str, project: str, key: str) -> str:
    proj = _section(page, rf'<section class="rp-ob-project" id="{re.escape(project)}".*?</section>')
    return _section(proj, rf'<li class="rp-ob-item[^"]*" data-item="{key}".*?(?=<li class="rp-ob-item|</ol>)')


def _make_project(home: Path, name: str, product: Path) -> Path:
    """临时根上建项目(产品同一实现 scaffold_project + set_project_repos), 仓名 app。"""
    from tools.aipos_cli.workspace_config import scaffold_project, set_project_repos

    root = scaffold_project(home, name, registered_by="f272-fixture")
    set_project_repos(home, name, {"app": str(product)}, default="app", dry_run=False)
    return root


def _owner_conn(gov: Path, gate_url: str) -> str:
    """项目自己的 Owner 凭据(与 _seed_project 同形, 无 projects 键 = 门按来源项目定范围); 进程内看板以它为签发凭据库。返回令牌(只在进程内用)。"""
    import secrets

    from tools.aipos_cli.confirm_client import token_fingerprint

    token = secrets.token_urlsafe(32)
    path = gov / ".lybra" / "connection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"config_version": 1, "workspace_root": str(gov), "mcp": {"rpc_url": f"{gate_url}/mcp"}, "tokens": [{
        "role": "owner", "token": token, "token_ref": f"svc-owner-{gov.name}", "fingerprint": token_fingerprint(token),
        "scopes": ["queue_claim", "queue_return", "owner_confirm", "draft_publish", "owner_decision_record", "queue_amend", "queue_withdraw"],
        "agent_instance": f"owner.{gov.name}.fixture"}]}, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return token


def _seed_instances(gov: Path, tmp: Path, project: str) -> dict[str, str]:
    """人肉期接入样本(临时根自造, 产品同一 trail writer): 子 agent 模式执行者 / 审计工位 / 工位模式执行者(作子 agent 执行者 = 模式不合)。"""
    from tools.aipos_cli.enrollment import _append_enrollment_trail, subagent_land_detail

    names = {"sub": f"exec.{project}.{HOST}", "aud": f"audit.{project}.{HOST}", "wsx": f"exec.{project}.{HOST}ws",
             "ghost": f"exec.{project}.ghost"}
    _append_enrollment_trail(gov, action="land", code_id=f"enroll_fixture_{project}_sub", role="executor", instance=names["sub"],
                             by="(agent-enroll)", reason=subagent_land_detail(host=HOST, harness="codex", mode="subagent",
                                                                             files=["connection.json"]))
    for key, role in (("aud", "auditor"), ("wsx", "executor")):
        ws = tmp / f"ws-{project}-{key}"
        ws.mkdir(parents=True, exist_ok=True)
        f95._land(gov, names[key], role, ws)
    return names


# ===========================================================================
# 靶场
# ===========================================================================

@pytest.fixture(scope="module")
def rng(tmp_path_factory):
    """临时 home(枢纽项目 proj-r2: 签发凭据库 owner 凭据, projects 显式列出靶场各项目; 产品仓 app)+ 治理仓(裸远端)+ 夹具 pi + 临时门子进程。
    本进程只见靶场(HOME / LYBRA_HOME_ROOT 指临时目录, PATH 前置夹具 pi, 其余 LYBRA_ / AIPOS_ 环境变量清掉)。"""
    tmp = tmp_path_factory.mktemp("f272")
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
    conn_path = hub / ".lybra" / "connection.json"
    conn = json.loads(conn_path.read_text(encoding="utf-8"))
    # 端到端走查用的看板签发凭据(子进程看板服务工作区 = proj-r2): 门按凭据项目范围核 workspace_root 与活动项目(多项目凭据无缺省项目 =
    # PROJECT_AMBIGUOUS, "*" 不当通配), 故显式只给将由看板新建的那个项目
    conn["tokens"][0]["projects"] = ["proj-e2e"]
    conn_path.write_text(json.dumps(conn, indent=2), encoding="utf-8")
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

        set_project_repos(home, hub.name, {"app": str(product)}, default="app", dry_run=False)
        gov_a = _make_project(home, "proj-a", product)
        gov_t = _make_project(home, "proj-t", product)
        names_a = _seed_instances(gov_a, tmp, "proj-a")
        _owner_conn(gov_a, gate_url)
        init_governance_repo(home)
        gate = _start_gate(home, port, env, tmp / "gate.log")
        try:
            yield {"tmp": tmp, "home": home, "hub": hub.resolve(), "owner_token": owner_token, "gate_url": gate_url, "env": env,
                   "product": product, "gov_a": gov_a.resolve(), "gov_t": gov_t.resolve(), "names_a": names_a, "codes": []}
        finally:
            fixture_procs.reap(gate)
            assert not fixture_procs.tagged(gate), "临时门已整组回收"


@pytest.fixture(scope="module")
def board(rng):
    """进程内看板(临时端口; 与产品同一 make_handler / OTC / 会话 / 预演令牌表), 服务工作区 = proj-r2, 签发凭据库 = proj-a 的 Owner 凭据
    (门按凭据所属项目放行首签); 登录凭据库另一份(Owner + 第二个 Owner + advisor)。"""
    from web.board.app import SessionStore, _token_fingerprint, make_handler
    from web.board.auth_otc import DeviceCodeStore, OTCStore, PreviewTokenStore

    login = rng["tmp"] / "board-login" / ".lybra" / "connection.json"
    login.parent.mkdir(parents=True)
    login.write_text(json.dumps({"tokens": [
        {"role": "owner", "token_ref": "owner-fixture", "scopes": ["owner_confirm"], "token": rng["owner_token"],
         "fingerprint": _token_fingerprint(rng["owner_token"])},
        {"role": "owner", "token_ref": "owner2-fixture", "scopes": ["owner_confirm"], "token": OWNER2_TOK, "fingerprint": _token_fingerprint(OWNER2_TOK)},
        {"role": "advisor", "token_ref": "advisor-fixture", "scopes": ["queue_publish"], "token": ADVISOR_TOK,
         "fingerprint": _token_fingerprint(ADVISOR_TOK)}]}), encoding="utf-8")
    previews = PreviewTokenStore(ttl_seconds=300)
    runtime = rng["tmp"] / "board-runtime"
    audit_log = runtime / "board-audit-log.jsonl"
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=rng["hub"], home_root=rng["home"], session_store=SessionStore(), connection_paths=[login], otc_store=OTCStore(),
        device_store=DeviceCodeStore(), auth_log_path=runtime / "auth-log.jsonl", preview_store=previews, board_audit_log_path=audit_log,
        mint_connection_json=rng["gov_a"] / ".lybra" / "connection.json"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = _Client(server.server_address[1])
    try:
        yield {**rng, "client": client, "previews": previews, "audit_log": audit_log, "owner": client.otc_login(rng["owner_token"]),
               "owner2": client.otc_login(OWNER2_TOK), "advisor": client.otc_login(ADVISOR_TOK)}
    finally:
        server.shutdown()
        server.server_close()


def _grant_body(project: str, driver: str, **over) -> dict:
    return {"workspace": project, "lanes": ["app"], "task_modes": ["code", "validation"], "drivers": [driver], "hosts": [HOST],
            "launch_harnesses": ["pi"], "zones_open": [], "max_tasks": 20, "expires_at": "2099-01-01T00:00:00Z", **over}


# ===========================================================================
# ① 件① 首签授权 / 件② 执行方式 + 模型: 预演 JSON + 确认后记录 / project.json diff + 审计日志
# ===========================================================================

def test_item1_grant_preview_confirm_record_and_audit(board):
    client, owner, gov = board["client"], board["owner"], board["gov_a"]
    driver = f"advisor.proj-a.{HOST}"
    body = _grant_body("proj-a", driver)
    auth_dir = gov / "5_tasks" / "authorizations"
    status, pre = _post(client, GRANT, {**body, "stage": "preview"}, owner)
    _show(f"[①·首签授权预演 JSON 原文] HTTP {status}\n{_dump({k: v for k, v in pre.items() if k != 'dry_run'})}")
    assert status == 200 and pre["ok"] and pre["preview_token"] and pre["params"]["policy_id"] == "auth_proj-a"
    assert pre["params"]["action"] == "grant" and pre["params"]["lanes"] == [{"repo": "app", "task_modes": ["code", "validation"],
                                                                                 "drivers": [driver]}]
    assert pre["params"]["actor"].startswith("board:owner:owner-fixture:session-") and pre["capture_surface"] == "board"
    assert any("auth_proj-a" in line for line in pre["summary_lines"]) and pre["authorization_markdown"]
    assert not auth_dir.exists(), "预演零写入"
    status, done = _post(client, GRANT, {**body, "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
    _show(f"[①·首签授权确认应答] HTTP {status}\n{_dump(done)}")
    assert status == 200 and done["ok"] and done["signed"][0]["policy_id"] == "auth_proj-a" and done["capture_surface"] == "board"
    rev = auth_dir / "auth_proj-a" / "r0001.md"
    decision = gov / "5_tasks" / "records" / "owner_decisions" / "grant-auth_proj-a-r0001.md"
    _show(f"[①·授权修订记录原文 {rev.relative_to(board['home'])}]\n{rev.read_text(encoding='utf-8')}\n"
          f"[①·Owner 决定记录(节选) {decision.relative_to(board['home'])}]\n"
          + "\n".join(ln for ln in decision.read_text(encoding="utf-8").splitlines() if ln.split(":")[0] in
                      ("record_type", "decision_id", "actor", "captured_by", "capture_surface", "decided_by_ref")))
    assert rev.is_file() and "落账: verdict=PASS" in str(done["landing_line"])
    text = decision.read_text(encoding="utf-8")
    assert "capture_surface: board" in text and "captured_by: board:owner:owner-fixture:session-" in text
    from tools.aipos_cli.autonomy_policy import load_authorization

    auth = load_authorization(gov, "auth_proj-a")
    assert auth["lanes"][0]["drivers"] == [driver] and auth["max_tasks"] == 20 and auth["hosts"] == [HOST]
    lines = [a for a in _audit(board["audit_log"]) if a["action"] == "project_grant"]
    _show("[①·看板审计日志行(project_grant)]\n" + "\n".join(json.dumps(a, ensure_ascii=False) for a in lines))
    assert [a["outcome"] for a in lines][-3:] == ["previewed", "confirming", "signed"]
    assert not any(board["owner_token"] in json.dumps(a) for a in lines), "审计日志不含令牌原文"


def test_item1_execution_and_model_preview_confirm_diff_and_audit(board):
    client, owner, gov, names = board["client"], board["owner"], board["gov_a"], board["names_a"]
    pj = gov / "project.json"
    before = pj.read_text(encoding="utf-8")
    body = {"workspace": "proj-a", "action": "execution", "subagent_executor": names["sub"], "auditor": names["aud"]}
    status, pre = _post(client, EXEC, {**body, "stage": "preview"}, owner)
    _show(f"[①·执行方式预演 JSON 原文] HTTP {status}\n{_dump(pre)}")
    assert status == 200 and pre["ok"] and pre["command"] == "set-execution" and '"subagent_executor": "' + names["sub"] in pre["plan"]["diff"]
    assert pj.read_text(encoding="utf-8") == before, "预演零写入"
    status, done = _post(client, EXEC, {**body, "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
    _show(f"[①·执行方式确认应答(含 project.json diff 原文与落账行)] HTTP {status}\n{_dump(done)}")
    assert status == 200 and done["ok"] and done["plan"]["written"] and done["landing"]["landed"]
    from tools.aipos_cli.workspace_config import project_execution

    assert (project_execution(gov) or {}).get("subagent_executor") == names["sub"]
    # 模型: 候选 = 看板 setup check 缓存(先开设置自检页跑一次; 夹具 pi), 页面带候选; 预演 diff + 影响工位 → 确认
    client.page(SETUP, owner)
    page = client.page(ONBOARDING, owner)
    setup_attr = re.search(r'id="proj-a"[^>]*data-ps-setup="([^"]*)"', page)
    assert setup_attr, "Owner 接入页带 data-ps-setup"
    import html

    setup = json.loads(html.unescape(setup_attr.group(1)))
    _show(f"[①·接入页 pi 模型候选(复用 setup check 缓存)] {setup.get('model')}")
    assert setup["model"]["models"] == list(MODELS) and setup["model"]["role"] == "auditor"
    mbody = {"workspace": "proj-a", "action": "model", "model": MODELS[1], "role": "auditor"}
    status, pre = _post(client, EXEC, {**mbody, "stage": "preview"}, owner)
    _show(f"[①·pi 模型预演 JSON 原文] HTTP {status}\n{_dump(pre)}")
    assert status == 200 and pre["ok"] and pre["command"] == "set-model" and MODELS[1] in pre["plan"]["diff"]
    assert pre["plan"]["affected"][0]["role"] == "auditor" and pre["plan"]["affected"][0]["instance"] == names["aud"]
    status, done = _post(client, EXEC, {**mbody, "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
    _show(f"[①·pi 模型确认应答] HTTP {status}\n{_dump(done)}")
    assert status == 200 and done["ok"] and done["landing"]["landed"]
    pm = json.loads(pj.read_text(encoding="utf-8"))["execution"]["pi_models"]
    assert pm["by_role"]["auditor"] == MODELS[1] and pm["owner_ref"].startswith("board:owner:owner-fixture:session-")
    lines = [a for a in _audit(board["audit_log"]) if a["action"] == "project_execution"]
    _show("[①·看板审计日志行(project_execution)]\n" + "\n".join(json.dumps(a, ensure_ascii=False) for a in lines))
    assert [(a["setup_action"], a["outcome"]) for a in lines][-6:] == [
        ("execution", "previewed"), ("execution", "confirming"), ("execution", "written"),
        ("model", "previewed"), ("model", "confirming"), ("model", "written")]
    log = subprocess.run(["git", "log", "-3", "--format=%s%n%b"], cwd=board["home"], capture_output=True, text=True).stdout
    _show(f"[①·治理仓落账提交(最近 3 条)]\n{log}")
    assert "set-model" in log and "set-execution" in log


# ===========================================================================
# ② 六类令牌(两条新路由各一组) ③ 非 Owner ④ 非法输入预演拒
# ===========================================================================

@pytest.mark.parametrize("route", ["grant", "execution"])
def test_item2_six_token_refusals_on_new_routes(board, route):
    client, owner, gov, names = board["client"], board["owner"], board["gov_t"], board["names_a"]
    if route == "grant":
        path, base, change = GRANT, _grant_body("proj-t", f"advisor.proj-t.{HOST}"), {"max_tasks": 21}
    else:
        path, base, change = EXEC, {"workspace": "proj-a", "action": "execution", "subagent_executor": names["sub"], "auditor": names["aud"]}, \
            {"pi_executor": names["aud"]}
    gov_files = sorted(p.read_text(encoding="utf-8") for p in (gov / "project.json", board["gov_a"] / "project.json"))
    lines = []

    def confirm(token, cookie=owner, **extra):
        payload = {**base, **extra, "stage": "confirm"}
        if token is not None:
            payload["preview_token"] = token
        return _post(client, path, payload, cookie)

    status, resp = confirm(None)
    lines.append(f"① 缺令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_REQUIRED"
    status, resp = confirm("not-a-real-token")
    lines.append(f"② 错令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, other = _post(client, path, {**base, "stage": "preview"}, board["owner2"])
    assert _s == 200, other
    status, resp = confirm(other["preview_token"])
    lines.append(f"③ 他会话令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_SESSION_MISMATCH"
    _s, pre = _post(client, path, {**base, "stage": "preview"}, owner)
    status, resp = confirm(pre["preview_token"], **change)
    lines.append(f"④ 改参数({change}) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_PARAMS_MISMATCH"
    status, resp = confirm(pre["preview_token"])
    lines.append(f"⑤ 已用令牌(单次即焚) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, pre = _post(client, path, {**base, "stage": "preview"}, owner)
    store = board["previews"]
    real_now = store._now
    store._now = lambda: real_now() + 301
    try:
        status, resp = confirm(pre["preview_token"])
    finally:
        store._now = real_now
    lines.append(f"⑥ 过期令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_EXPIRED"
    _show(f"[②·{path} 令牌六类拒原文]\n" + "\n".join(lines))
    assert sorted(p.read_text(encoding="utf-8") for p in (gov / "project.json", board["gov_a"] / "project.json")) == gov_files
    if route == "grant":
        assert not (gov / "5_tasks" / "authorizations").exists(), "被拒确认不签"


def test_item3_non_owner_refused_and_readonly_page(board):
    client = board["client"]
    lines = []
    for path, body in ((GRANT, _grant_body("proj-t", f"advisor.proj-t.{HOST}")),
                       (EXEC, {"workspace": "proj-t", "action": "model", "model": MODELS[0]})):
        status, resp = _post(client, path, {**body, "stage": "preview"}, board["advisor"])
        lines.append(f"{path} 非 Owner(advisor)预演 → {status} {resp['error']} {resp['message']}")
        assert status == 403 and resp["error"] == "OWNER_ONLY"
        status, resp = _post(client, path, {**body, "stage": "confirm", "preview_token": "x"}, board["advisor"])
        lines.append(f"{path} 非 Owner(advisor)确认 → {status} {resp['error']}")
        assert status == 403 and resp["error"] == "OWNER_ONLY"
        status, _t, _sc, loc = client.request("POST", path, {**body, "stage": "preview"})
        lines.append(f"{path} 无会话 → {status} Location={loc}")
        assert status == 302 and str(loc).startswith("/login")
        status, resp = _post(client, path, {**body, "stage": "preview"}, board["owner"], headers={"Origin": "http://evil.example"})
        lines.append(f"{path} 跨站 Origin → {status} {resp['error']}")
        assert status == 403 and resp["error"] == "ORIGIN_MISMATCH"
    _show("[③·非 Owner 与框架核验拒原文]\n" + "\n".join(lines))
    roles = [a["role"] for a in _audit(board["audit_log"]) if a["action"] in ("project_grant", "project_execution") and a["outcome"] == "OWNER_ONLY"]
    assert roles.count("advisor") >= 4, "非 Owner 被拒也留痕"
    page = client.page(ONBOARDING, board["advisor"])
    row = _row(page, "proj-t", "envelope")
    _show(f"[③·非 Owner 接入页 proj-t 项目授权行原文]\n{row}")
    assert "ps-open" not in page and "project-setup.js" not in page and "ps-panel" not in page and "data-ps-setup" not in page
    assert 'class="rp-readonly-note ps-readonly" data-ps-action="grant"' in row and "lybra project grant sign" in row


def test_item4_illegal_preview_refusals(board):
    client, owner, names = board["client"], board["owner"], board["names_a"]
    lines = []
    cases = [
        ({"workspace": "proj-a", "action": "execution", "subagent_executor": names["ghost"], "auditor": names["aud"]}, EXEC,
         "EXECUTION_REFUSED", "EXECUTION_INSTANCE_NOT_ENROLLED", "实例未接入"),
        ({"workspace": "proj-a", "action": "execution", "subagent_executor": names["wsx"], "auditor": names["aud"]}, EXEC,
         "EXECUTION_REFUSED", "EXECUTION_SUBAGENT_MODE_MISMATCH", "子 agent 执行者非 subagent 模式"),
        ({"workspace": "proj-a", "action": "execution", "subagent_executor": names["sub"], "auditor": names["sub"]}, EXEC,
         "EXECUTION_REFUSED", "EXECUTION_DECLARATION_INVALID", "审计与执行者同一实例"),
        ({"workspace": "proj-a", "action": "model", "model": "fixprov/not-there"}, EXEC, "EXECUTION_REFUSED", "PI_MODEL_NOT_CONFIGURED",
         "模型不在本机 pi 清单"),
        ({"workspace": "proj-t", "action": "model", "model": MODELS[0]}, EXEC, "EXECUTION_REFUSED", "PI_MODEL_EXECUTION_UNDECLARED",
         "未声明执行方式先设模型"),
        ({"workspace": "proj-a", "action": "bogus"}, EXEC, "PARAMS_INVALID", None, "未知 action"),
        (_grant_body("proj-a", f"advisor.proj-a.{HOST}"), GRANT, "AUTHORIZATION_IN_USE", None, "已有在用授权"),
        (_grant_body("proj-t", f"advisor.proj-t.{HOST}", lanes=["no-such-repo"]), GRANT, "PREVIEW_BLOCKED", None, "车道仓不在 project.json"),
        (_grant_body("proj-t", f"advisor.proj-t.{HOST}", max_tasks=""), GRANT, "PARAMS_INVALID", None, "缺总卡数"),
        (_grant_body("no-such-project", "x"), GRANT, "WORKSPACE_NOT_IN_HOME", None, "未注册项目"),
    ]
    for body, path, code, refusal, label in cases:
        status, resp = _post(client, path, {**body, "stage": "preview"}, owner)
        lines.append(f"{label}: {path} {json.dumps(body, ensure_ascii=False)} → {status} {resp['error']} {resp['message']}")
        assert resp["error"] == code and "preview_token" not in resp, (label, resp)
        if refusal:
            assert refusal in resp["message"], (label, resp)
    _show("[④·非法输入预演拒原文]\n" + "\n".join(lines))
    assert "auth_proj-a r1" in lines[-4] and "lybra project grant extend" in lines[-4]


# ===========================================================================
# ⑤ 既有写例外不变 + 声明 / i18n / 单一实现守卫
# ===========================================================================

def test_item5_existing_write_exceptions_and_declarations():
    from tools.aipos_cli.board_project_setup import execution_board_declaration, grant_declaration
    from web.board.app import _I18N_TEMPLATES, board_readonly_declaration
    from web.board.readonly_pages import _PS_JS_KEYS, PS_JS_ERROR_CODES

    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    ro = verbs["board_readonly"]
    g, e = grant_declaration(), execution_board_declaration()
    _show(f"[⑤·声明] ⑥ {g['write_path']} ⑦ {e['write_path']} actions={e['actions']} 写例外清单={ro['write_exceptions']}")
    assert list(ro["write_exceptions"]) == ["envelope_mint", "project_upgrade", "owner_inbox_decide", "project_new", "enroll_code",
                                            "project_grant", "project_execution"]
    assert verbs["verbs"]["lybra_project_authorization"]["surface"] == ["cli", "board"]
    assert {g["write_path"], e["write_path"]}.isdisjoint(board_readonly_declaration()["allowed_post_paths"])
    src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    do_post = src[src.index("def do_POST(self)"):src.index("def do_PUT(self)")]
    assert do_post.count("if path == ") == len(board_readonly_declaration()["allowed_post_paths"]) + 7
    for decl in (g, e):
        assert set(PS_JS_ERROR_CODES) & set(decl["errors"]), "页面解释的拒因码在声明码表内"
    assert set(PS_JS_ERROR_CODES) <= set(g["errors"]) | set(e["errors"])
    zh = {k for k in _I18N_TEMPLATES["zh"] if k.startswith("ps_")}
    en = {k for k in _I18N_TEMPLATES["en"] if k.startswith("ps_")}
    assert zh == en and set(_PS_JS_KEYS) <= zh and {"ps_js_err_" + c for c in PS_JS_ERROR_CODES} <= zh
    for key in ("rp_ob_detail_authorization_ok", "rp_ob_detail_authorization_low"):
        assert key in _I18N_TEMPLATES["zh"] and key in _I18N_TEMPLATES["en"]
    mod = (REPO_ROOT / "tools" / "aipos_cli" / "board_project_setup.py").read_text(encoding="utf-8")
    for needed in ("handle_owner_write(", "_authorization_callbacks(", "_authorization_payload(", "set_project_execution(",
                   "set_project_pi_model(", "land_after_write(", "execution_instance_refusals(", "subagent_mode_mismatch(", "grant_plan(",
                   "advisor_instances(", "render_authorization_command("):
        assert needed in mod, needed
    for banned in ("update_project_json(", "mint_via_gate(", "harness_model_catalog(", "subprocess", "except Exception", "write_text(", "open("):
        assert banned not in mod, banned
    js = (REPO_ROOT / "web" / "board" / "static" / "project-setup.js").read_text(encoding="utf-8")
    assert not re.search(r"[一-鿿]", js), "脚本不含界面中文(文字走 data-ps-texts)"
    assert "localStorage" not in js and "sessionStorage" not in js
    defs = subprocess.run(["git", "grep", "-n", "-E", r"def (set_project_execution|set_project_pi_model|authorization_block|run_setup_check)\(",
                           "--", "tools", "web"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip().splitlines()
    _show("[⑤·唯一实现 git grep]\n" + "\n".join(defs))
    assert len(defs) == 4


# ===========================================================================
# ⑥ 件④ 端到端走查(子进程看板 + 临时门, 只用看板) ⑦ 接入页中 / 英关键段
# ===========================================================================

def test_item6_7_end_to_end_onboarding_only_with_board(rng):
    home, hub, tmp = rng["home"], rng["hub"], rng["tmp"]
    project = "proj-e2e"
    adv, sub, aud = f"advisor.{project}.{HOST}", f"exec.{project}.{HOST}", f"audit.{project}.{HOST}"
    port = _free_port()
    argv = [sys.executable, "-m", "web.board.app", "--host", "127.0.0.1", "--port", str(port), "--repo-root", str(hub), "--no-prewarm"]
    with fixture_procs.running(argv, env=dict(rng["env"]), cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        _wait_port(port, proc)
        client = _Client(port)
        owner = client.otc_login(rng["owner_token"])
        steps: list[str] = [f"看板子进程 pid={proc.pid} 127.0.0.1:{port}(服务工作区 = 签发凭据库所在 {hub.name}); 临时门 {rng['gate_url']}"]

        def two_phase(path: str, body: dict, label: str) -> dict:
            status, pre = _post(client, path, {**body, "stage": "preview"}, owner)
            assert status == 200 and pre["ok"], (label, pre)
            status, done = _post(client, path, {**body, "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
            assert status == 200 and done["ok"], (label, done)
            steps.append(f"{label}: 预演 HTTP 200 → 确认 HTTP 200")
            return done

        def rows() -> dict[str, str]:
            page = client.page(ONBOARDING, owner)
            proj = _section(page, rf'<section class="rp-ob-project" id="{project}".*?</section>')
            return dict(re.findall(r'data-item="(\w+)" data-state="(\w+)"', proj))

        client.page(SETUP, owner)  # 设置自检(夹具 pi 有模型): 看板缓存结果, 接入页模型候选复用
        done = two_phase(PNEW, {"workspace": project, "code_repo": str(rng["product"])}, "① 新建项目(写例外④)")
        gov = Path(done["created"]["governance_root"])
        steps.append(f"   清单: {rows()}")
        codes = {}
        for role, inst in (("advisor", adv), ("executor", sub), ("auditor", aud)):
            got = two_phase(ENROLL, {"workspace": project, "role": role, "instance": inst}, f"② 签注册码 {role} {inst}(写例外⑤)")
            codes[role] = got["code"]["paste_text"]
            rng["codes"].append(got["code"]["self_contained_code"])

        def redeem(role: str, workspace: Path, *extra: str) -> None:
            argv_enroll = shlex.split(codes[role].replace("<工位目录或治理根>", str(workspace))) + list(extra)
            rc, out, err = _cli(*argv_enroll[1:])
            steps.append(f"③ 测试内兑换 {role}: rc={rc} {'; '.join(ln.strip() for ln in (out + err).splitlines() if '✓' in ln or 'Error' in ln)[:400]}")
            assert rc == 0, out + err

        redeem("advisor", gov, "--harness", "codex")
        redeem("executor", gov, "--executor-mode", "subagent", "--harness", "codex")
        steps.append(f"   清单: {rows()}")
        page = client.page(ONBOARDING, owner)
        envelope_row = _row(page, project, "envelope")
        assert 'data-ps-action="grant"' in envelope_row, envelope_row
        import html

        setup = json.loads(html.unescape(re.search(rf'id="{project}"[^>]*data-ps-setup="([^"]*)"', page).group(1)))
        prefill = setup["grant"]
        steps.append(f"④ 首签表单预填(既有推导): 车道 {prefill['lanes']} 任务类型 {prefill['task_modes']} 驱动方 {prefill['drivers']} "
                     f"主机 {prefill['hosts']} 拉起 {prefill['launch_harnesses']} 总卡数 {prefill['max_tasks']} 到期 {prefill['expires_at']}")
        assert prefill["lanes"] == [project] and prefill["drivers"] == [adv] and HOST in prefill["hosts"]
        body = {k: prefill[k] for k in ("lanes", "task_modes", "drivers", "hosts", "launch_harnesses", "zones_open", "max_tasks", "expires_at")}
        two_phase(GRANT, {"workspace": project, **body}, "④ 首签项目授权(写例外⑥, 预填原样)")
        ws = tmp / "ws-e2e-auditor"
        ws.mkdir()
        redeem("auditor", ws)
        steps.append(f"   清单: {rows()}")
        page = client.page(ONBOARDING, owner)
        setup = json.loads(html.unescape(re.search(rf'id="{project}"[^>]*data-ps-setup="([^"]*)"', page).group(1)))
        slots = setup["execution"]["slots"]
        steps.append(f"⑤ 执行方式候选(set-execution 既有校验): {slots}")
        assert slots["subagent_executor"] == [sub] and slots["auditor"] == [aud]
        two_phase(EXEC, {"workspace": project, "action": "execution", "subagent_executor": sub, "auditor": aud}, "⑤ 设执行方式(写例外⑦ execution)")
        page = client.page(ONBOARDING, owner)
        setup = json.loads(html.unescape(re.search(rf'id="{project}"[^>]*data-ps-setup="([^"]*)"', page).group(1)))
        steps.append(f"⑥ pi 模型候选(setup check 缓存): {setup['model']['models']} 预选角色 {setup['model']['role']}")
        two_phase(EXEC, {"workspace": project, "action": "model", "model": setup["model"]["models"][0]}, "⑥ 设 pi 模型(写例外⑦ model, 项目缺省)")
        final = rows()
        steps.append(f"   清单(终): {final}")
        _show("[⑥·端到端走查原文]\n" + "\n".join(steps))
        for key in ("governance_root", "advisor", "executor", "auditor", "envelope", "pi_model"):
            assert final[key] == "ok", (key, final)
        left = {k: v for k, v in final.items() if v != "ok"}
        _show(f"[⑥·仍须离开看板的项] {left}")
        assert set(left) <= {"charter", "roadmap", "first_closed"}
        # ⑦ 接入页中 / 英关键段原文(另一新项目尚未签授权 / 未设执行方式时的按钮; 本项目完成后的绿项)
        for lang in ("zh", "en"):
            page = client.page(ONBOARDING + ("?lang=en" if lang == "en" else ""), owner)
            env_ok, model_ok = _row(page, project, "envelope"), _row(page, project, "pi_model")
            open_env, open_exec = _row(page, "proj-t", "envelope"), _row(page, "proj-t", "executor")
            _show(f"[⑦·接入页 {lang} {project} 项目授权行]\n{env_ok}\n[⑦·{lang} {project} pi 模型行]\n{model_ok}\n"
                  f"[⑦·{lang} proj-t 项目授权行(未签)]\n{open_env}\n[⑦·{lang} proj-t 执行行(未声明)]\n{open_exec}")
            assert 'data-state="ok"' in env_ok and "auth_proj-e2e r1" in env_ok and 'data-state="ok"' in model_ok
            want = ("首签项目授权", "设执行方式", "项目授权在用") if lang == "zh" else ("Sign the first project grant", "Set execution mode",
                                                                                   "Project grant in use")
            assert want[0] in open_env and 'data-ps-action="grant"' in open_env and want[1] in open_exec and want[2] in env_ok
            assert '<script src="/project-setup.js">' in page and '<section class="ps-panel" id="ps-panel" hidden>' in page
    assert proc.poll() is not None and not fixture_procs.tagged(proc), "看板子进程已整组回收"
