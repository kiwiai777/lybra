"""AIPOS-F270 — 看板写例外⑤「签注册码」(产品化 P3 第三张)。

验收(卡面 ★验收):
  ① 临时 home 根 + 临时治理根 + 临时门(临时端口)起看板(子进程, tools/aipos_cli/tests/fixture_procs.py), Owner 会话经既有 OTC 登录夹具
     预演 → 确认签码: 预演 JSON 原文 + 确认应答(码原文打码)+ 审计日志行原文。
  ② 用该码在临时执行体目录跑既有 enroll 兑换成功(端到端: 交给对方的那一句 paste_text, 只换 --workspace 占位)。
  ③ 六类令牌核验对新路由各一用例。 ④ 非 Owner 会话拒。 ⑤ 未注册项目 / 非法角色 / 缺实例名三种预演拒原文。
  ⑥ 日志与审计文件 grep 码原文 0 命中(另: 预演令牌表 / 页面重载不含码)。 ⑦ 既有四写例外回归: 路由计数 / 写例外清单只追加(各自用例在
     test_aipos_f172 / f176 / f226 / f269 全过)。 ⑧ 接入页中 / 英 HTML 关键段原文(签码按钮 / 「已签, 待兑换」)。
靶场: 临时 home(夹具项目 + 自签 owner 凭据 + 覆盖执行类的信封, 构件 tools/test_aipos_r2_enroll._seed_project)+ 本检出起的临时门子进程
(随机端口) + 看板(子进程 / 进程内)。不碰真实治理根 / 真实门 / 真实工位; 子进程只经 fixture_procs 起停。码只打印指纹 / 打码形。
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.tests import fixture_procs  # noqa: E402
from tools.test_aipos_r2_enroll import GATE_READY_MARK, _free_port, _seed_project  # noqa: E402  — 临时门靶场构件唯一来源

WRITE_PATH = "/api/owner/enroll-code"
ONBOARDING = "/owner/onboarding"
CODE_RE = re.compile(r"LYBRAENROLL1\.[A-Za-z0-9_=-]+")
SUB = "exec.proj-r2.f270sub"
PI = "exec.proj-r2.f270pi"
AUDITOR = "audit.proj-r2.f270host"
OWNER2_TOK = "owner2-f270-test-token-QRS"
ADVISOR_TOK = "advisor-f270-test-token-ABC"


def _show(text: str) -> None:
    print(CODE_RE.sub("LYBRAENROLL1.<已打码>", text), flush=True)


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _wait_port(port: int, proc: subprocess.Popen, seconds: float = 90.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"子进程提前退出 rc={proc.returncode}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise AssertionError(f"{seconds}s 内未监听 127.0.0.1:{port}")


def _start_gate(home: Path, port: int, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    """临时门(本检出, --home-root 靶场 home): 经 fixture_procs.spawn 起(新进程组 + 登记标签); 就绪 = 门打印 listening 行(同步读)。"""
    gate = fixture_procs.spawn([sys.executable, "-m", "tools.mcp_server", "serve-http", "--host", "127.0.0.1", "--port", str(port),
                                "--home-root", str(home)], env=env, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    seen: list[str] = []
    for line in gate.stdout:
        seen.append(line)
        if GATE_READY_MARK in line or len(seen) > 200:
            break
    log_path.write_text("".join(seen), encoding="utf-8")
    if not any(GATE_READY_MARK in line for line in seen):
        fixture_procs.reap(gate)
        raise AssertionError(f"临时门未就绪:\n{''.join(seen)[-2000:]}")

    def _drain() -> None:  # 就绪后持续把门输出抽到日志文件(防管道写满阻塞门); 日志文件也在 ⑥ grep 范围内
        with log_path.open("a", encoding="utf-8") as fh:
            for rest in gate.stdout:
                fh.write(rest)
                fh.flush()

    threading.Thread(target=_drain, daemon=True).start()
    return gate


class _Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def request(self, method: str, path: str, body: dict | None = None, cookie: str | None = None,
                headers: dict | None = None) -> tuple[int, str, str | None, str | None]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        hdrs = dict(headers or {})
        if cookie:
            hdrs["Cookie"] = cookie
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        out = (resp.status, data, resp.getheader("Set-Cookie"), resp.getheader("Location"))
        conn.close()
        return out

    def otc_login(self, token: str) -> str:
        """既有 OTC 登录通道: 测试令牌 mint 一次性码 → GET /login?otc=… 换会话 cookie。"""
        from web.board.app import SESSION_COOKIE_NAME, parse_session_cookie

        status, body, _sc, _loc = self.request("POST", "/api/auth/otc/mint", {"token": token})
        assert status == 200, body
        otc = json.loads(body)["otc"]
        status, _b, set_cookie, loc = self.request("GET", f"/login?otc={otc}")
        assert status == 302 and set_cookie, (status, loc)
        return f"{SESSION_COOKIE_NAME}={parse_session_cookie(set_cookie)}"

    def post(self, body: dict, cookie: str | None, headers: dict | None = None) -> tuple[int, dict]:
        status, text, _sc, _loc = self.request("POST", WRITE_PATH, body, cookie=cookie, headers=headers)
        return status, json.loads(text)

    def page(self, path: str, cookie: str) -> str:
        status, text, _sc, _loc = self.request("GET", path, cookie=cookie)
        assert status == 200, (path, status, text[:500])
        return text


def _redacted(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _section(page: str, pattern: str) -> str:
    m = re.search(pattern, page, re.S)
    assert m, pattern
    return m.group(0)


@pytest.fixture(scope="module")
def rng(tmp_path_factory):
    """临时 home(夹具项目 proj-r2: 队列 + project.json + 执行类信封 + 自签 owner 凭据; 执行方式声明子 agent / pi / 审计三实例)+ 临时门子进程。
    本进程只见靶场(HOME / LYBRA_HOME_ROOT 指临时目录, 其余 LYBRA_ / AIPOS_ 环境变量清掉)。"""
    tmp = tmp_path_factory.mktemp("f270")
    home = tmp / "home"
    user_home = tmp / "user-home"
    user_home.mkdir()
    port = _free_port()
    gate_url = f"http://127.0.0.1:{port}"
    project, owner_token = _seed_project(home, gate_url)
    pj = json.loads((project / "project.json").read_text(encoding="utf-8"))
    pj["execution"] = {"default_mode": "subagent", "subagent_executor": SUB, "pi_executor": PI, "auditor": AUDITOR}
    (project / "project.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"PYTHONPATH": str(REPO_ROOT), "HOME": str(user_home), "LYBRA_HOME_ROOT": str(home)})
    with pytest.MonkeyPatch.context() as mp:
        for key in [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]:
            mp.delenv(key)
        mp.setenv("HOME", str(user_home))
        mp.setenv("LYBRA_HOME_ROOT", str(home))
        gate = _start_gate(home, port, env, tmp / "gate.log")
        try:
            yield {"tmp": tmp, "home": home, "project": project.resolve(), "owner_token": owner_token, "gate_url": gate_url, "env": env,
                   "gate_log": tmp / "gate.log", "codes": []}
        finally:
            fixture_procs.reap(gate)
            assert not fixture_procs.tagged(gate), "临时门已整组回收"


@pytest.fixture(scope="module")
def board(rng):
    """进程内看板(临时端口; 与产品同一 make_handler / OTC / 会话 / 预演令牌表), 签发凭据库 = 夹具项目 connection.json(与门同一 owner 凭据);
    登录凭据库另一份(Owner 同一令牌 + 第二个 Owner + advisor), 便于拨预演令牌时钟与造他会话 / 非 Owner 会话。"""
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
        repo_root=rng["project"], home_root=rng["home"], session_store=SessionStore(), connection_paths=[login], otc_store=OTCStore(),
        device_store=DeviceCodeStore(), auth_log_path=runtime / "auth-log.jsonl", preview_store=previews, board_audit_log_path=audit_log,
        mint_connection_json=rng["project"] / ".lybra" / "connection.json"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = _Client(server.server_address[1])
    try:
        yield {**rng, "client": client, "previews": previews, "audit_log": audit_log, "owner": client.otc_login(rng["owner_token"]),
               "owner2": client.otc_login(OWNER2_TOK), "advisor": client.otc_login(ADVISOR_TOK)}
    finally:
        server.shutdown()
        server.server_close()


def _audit(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _registry(project: Path) -> str:
    path = project / ".lybra" / "enrollments.json"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ===========================================================================
# ① 子进程看板 + 临时门: OTC 登录 → 预演 → 确认签码 → ② enroll 兑换端到端 → ⑧ 接入页中 / 英
# ===========================================================================

def test_item1_2_8_subprocess_board_sign_enroll_and_onboarding(rng):
    project = rng["project"]
    port = _free_port()
    env = dict(rng["env"])
    argv = [sys.executable, "-m", "web.board.app", "--host", "127.0.0.1", "--port", str(port), "--repo-root", str(project),
            "--no-prewarm"]
    with fixture_procs.running(argv, env=env, cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        _wait_port(port, proc)
        client = _Client(port)
        owner = client.otc_login(rng["owner_token"])
        _show(f"[①·看板子进程] pid={proc.pid} 127.0.0.1:{port} 看板工作区 = 签发凭据库所在 = {project}; 临时门 {rng['gate_url']}")

        before = _registry(project)
        body = {"workspace": project.name, "role": "executor", "instance": PI, "ttl": 3600, "stage": "preview"}
        status, pre = client.post(body, owner)
        _show(f"[①·预演 JSON 原文] HTTP {status}\n{_redacted(pre)}")
        assert status == 200 and pre["ok"] and pre["stage"] == "preview" and pre["preview_token"]
        pv = pre["preview"]
        assert (pv["role"], pv["instance"], pv["ttl_seconds"]) == ("executor", PI, 3600)
        assert pv["governance_root"] == str(project) and pv["gate_url"] == rng["gate_url"] == pre["params"]["gate_url"]
        assert pre["params"]["actor"].startswith("board:owner:svc-owner:session-sha256:") and pre["capture_surface"] == "board"
        assert pre["params"]["owner_authorization_ref"] == pre["params"]["actor"] and "同机" in pre["gate_url_source"]
        assert not CODE_RE.search(json.dumps(pre)) and _registry(project) == before, "预演不出码、注册码库零写入"

        status, done = client.post({**body, "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
        code = done["code"]["self_contained_code"]
        rng["codes"].append(code)
        _show(f"[①·确认应答(码原文打码)] HTTP {status}\n{_redacted(done)}")
        assert status == 200 and done["ok"] and CODE_RE.fullmatch(code) and done["capture_surface"] == "board"
        from tools.aipos_cli.onboarding import render_enroll_command

        assert done["code"]["paste_text"] == render_enroll_command(code), "交给对方的一句 = onboarding 既有渲染"
        assert done["code"]["instance"] == PI and done["code"]["code_id"] in _registry(project)
        assert done["exits"]["revoke"].startswith(f"lybra roles enroll-revoke {done['code']['code_id']} ")
        assert f"--project {project.name} --code-id {done['code']['code_id']}" in done["exits"]["void"]
        audit_path = project / ".lybra" / "board-audit-log.jsonl"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        _show(f"[①·看板审计日志原文 {audit_path}]\n" + "\n".join(lines))
        audit = [json.loads(ln) for ln in lines]
        assert [(a["action"], a["stage"], a["outcome"]) for a in audit] == [
            ("enroll_code", "preview", "previewed"), ("enroll_code", "confirm", "confirming"), ("enroll_code", "confirm", "issued")]
        last = audit[-1]
        assert (last["role"], last["instance"], last["ttl_seconds"], last["code_id"]) == ("executor", PI, 3600, done["code"]["code_id"])
        assert last["code_fingerprint"] == done["code"]["fingerprint"] and last["expires_at"] == done["code"]["expires_at"]
        assert code not in audit_path.read_text(encoding="utf-8")

        # ② 端到端: 交给对方的那一句(paste_text), 只把 --workspace 占位换成临时执行体目录, 走既有 enroll 兑换
        ws = rng["tmp"] / "pi-workstation"
        ws.mkdir()
        argv_enroll = shlex.split(done["code"]["paste_text"].replace("<工位目录或治理根>", str(ws)))
        assert argv_enroll[:3] == ["lybra", "roles", "enroll"]
        rc, out, err = _cli(*argv_enroll[1:])
        _show(f"[②·enroll 兑换(交给对方的一句) rc={rc}]\n{out}{err}")
        assert rc == 0, out + err
        conn = json.loads((ws / ".lybra" / "connection.json").read_text(encoding="utf-8"))
        entry = conn["tokens"][0]
        role_file = json.loads((ws / ".lybra" / "role").read_text(encoding="utf-8"))
        _show(f"[②·兑换后工位凭据] role={entry['role']} agent_instance={entry.get('agent_instance')} fingerprint={entry.get('fingerprint')} "
              f"role 文件 instance={role_file.get('instance')} owner_policy_ref={role_file.get('owner_policy_ref')}")
        assert entry["role"] == "executor" and entry["agent_instance"] == PI and role_file["instance"] == PI
        status_after = json.loads(_registry(project))[done["code"]["code_id"]]["status"]
        assert status_after == "used", status_after

        # ⑧ 再签一张不兑换(审计实例) → 接入页「已签, 待兑换」; 中 / 英关键段原文
        status, pre2 = client.post({"workspace": project.name, "role": "auditor", "instance": AUDITOR, "stage": "preview"}, owner)
        assert status == 200, pre2
        status, done2 = client.post({"workspace": project.name, "role": "auditor", "instance": AUDITOR, "stage": "confirm",
                                     "preview_token": pre2["preview_token"]}, owner)
        assert status == 200 and done2["ok"], done2
        rng["codes"].append(done2["code"]["self_contained_code"])
        assert done2["code"]["ttl_seconds"] == 86400, "时效缺省 = CLI 缺省"
        for lang, pending_text, button_text in (("zh", "已签, 待兑换(到期 ", "签注册码"), ("en", "Signed, awaiting redemption (expires ",
                                                                                         "Sign enrollment code")):
            page = client.page(ONBOARDING + ("?lang=en" if lang == "en" else ""), owner)
            assert not any(c in page for c in rng["codes"]), "页面重载不含码原文"
            proj = _section(page, rf'<section class="rp-ob-project" id="{project.name}".*?</section>')
            auditor_row = _section(proj, r'<li class="rp-ob-item[^"]*" data-item="auditor".*?(?=<li class="rp-ob-item|</ol>)')
            advisor_row = _section(proj, r'<li class="rp-ob-item[^"]*" data-item="advisor".*?(?=<li class="rp-ob-item|</ol>)')
            _show(f"[⑧·接入页 {lang} 审计接入项原文]\n{auditor_row}\n[⑧·接入页 {lang} 顾问接入项原文]\n{advisor_row}")
            assert pending_text in auditor_row and done2["code"]["code_id"] in auditor_row and AUDITOR in auditor_row
            assert f'data-ec-role="auditor" data-ec-instance="{AUDITOR}"' in auditor_row
            assert 'class="ec-sign" data-ec-role="advisor" data-ec-instance="advisor.proj-r2.' in advisor_row and button_text in advisor_row
            assert "data-ec-roles=" in proj and 'data-ec-owner="true"' in page and '<script src="/enroll-code.js">' in page
            assert '<section class="ec-panel" id="ec-panel" hidden>' in page
    assert proc.poll() is not None and not fixture_procs.tagged(proc), "看板子进程已整组回收"


# ===========================================================================
# ③ 预演令牌六类 ④ 非 Owner ⑤ 三类预演拒
# ===========================================================================

def test_item3_six_token_refusals_on_new_route(board):
    client, owner, project = board["client"], board["owner"], board["project"]
    before = _registry(project)
    base = {"workspace": project.name, "role": "auditor", "instance": AUDITOR}
    lines = []

    def confirm(token, *, ttl=None, cookie=owner):
        body = {**base, "stage": "confirm", "ttl": ttl}
        if token is not None:
            body["preview_token"] = token
        return client.post(body, cookie)

    status, resp = confirm(None)
    lines.append(f"① 缺令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_REQUIRED"
    status, resp = confirm("not-a-real-token")
    lines.append(f"② 错令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, other = client.post({**base, "stage": "preview"}, board["owner2"])
    status, resp = confirm(other["preview_token"])
    lines.append(f"③ 他会话令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_SESSION_MISMATCH"
    _s, pre = client.post({**base, "stage": "preview"}, owner)
    status, resp = confirm(pre["preview_token"], ttl=60)
    lines.append(f"④ 改参数(预演缺省时效, 确认改 60 秒) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_PARAMS_MISMATCH"
    status, resp = confirm(pre["preview_token"])
    lines.append(f"⑤ 已用令牌(单次即焚) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, pre = client.post({**base, "stage": "preview"}, owner)
    store = board["previews"]
    real_now = store._now
    store._now = lambda: real_now() + 301
    try:
        status, resp = confirm(pre["preview_token"])
    finally:
        store._now = real_now
    lines.append(f"⑥ 过期令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_EXPIRED"
    _show("[③·新路由令牌六类拒原文]\n" + "\n".join(lines))
    assert _registry(project) == before, "被拒确认不签码"
    outcomes = [a["outcome"] for a in _audit(board["audit_log"]) if a["action"] == "enroll_code"]
    _show(f"[③·审计日志 outcome 序列] {outcomes}")
    assert "issued" not in outcomes and "confirming" not in outcomes and outcomes.count("PREVIEW_TOKEN_INVALID") == 2


def test_item4_non_owner_refused_and_no_buttons(board):
    client, project = board["client"], board["project"]
    before = _registry(project)
    lines = []
    body = {"workspace": project.name, "role": "auditor", "instance": AUDITOR}
    status, resp = client.post({**body, "stage": "preview"}, board["advisor"])
    lines.append(f"非 Owner(advisor)会话预演 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    status, resp = client.post({**body, "stage": "confirm", "preview_token": "x"}, board["advisor"])
    lines.append(f"非 Owner(advisor)会话确认 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    status, _text, _sc, loc = client.request("POST", WRITE_PATH, {**body, "stage": "preview"})
    lines.append(f"无会话 → {status} Location={loc}(鉴权闸门先拦, 不进写路由)")
    assert status == 302 and str(loc).startswith("/login")
    status, resp = client.post({**body, "stage": "preview"}, board["owner"], headers={"Origin": "http://evil.example"})
    lines.append(f"跨站 Origin → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "ORIGIN_MISMATCH"
    _show("[④·非 Owner 与框架核验拒原文]\n" + "\n".join(lines))
    assert _registry(project) == before
    roles = [a["role"] for a in _audit(board["audit_log"]) if a["action"] == "enroll_code" and a["outcome"] == "OWNER_ONLY"]
    assert roles[:2] == ["advisor", "advisor"], "非 Owner 被拒也留痕"
    page = client.page(ONBOARDING, board["advisor"])
    proj = _section(page, rf'<section class="rp-ob-project" id="{project.name}".*?</section>')
    _show(f"[④·非 Owner 接入页: 签码按钮数 {proj.count('ec-sign')} 脚本 {'enroll-code.js' in page}]")
    assert "ec-sign" not in proj and "enroll-code.js" not in page and "ec-panel" not in page and "data-ec-roles" not in proj


def test_item5_preview_refusals_unregistered_role_instance(board):
    client, owner, project = board["client"], board["owner"], board["project"]
    before = _registry(project)
    lines = []
    cases = [({"workspace": "no-such-project", "role": "executor", "instance": PI}, 400, "WORKSPACE_NOT_IN_HOME", "未注册项目"),
             ({"workspace": project.name, "role": "wizard", "instance": PI}, 400, "ROLE_INVALID", "非法角色(不存在)"),
             ({"workspace": project.name, "role": "owner", "instance": "owner.proj-r2.x"}, 400, "ROLE_INVALID", "非法角色(owner 不可签)"),
             ({"workspace": project.name, "role": "advisor"}, 400, "INSTANCE_REQUIRED", "缺实例名(治理席位类)"),
             ({"workspace": project.name, "role": "executor"}, 400, "INSTANCE_REQUIRED", "缺实例名(已声明子 agent 执行者的执行类)"),
             ({"workspace": project.name, "role": "executor", "instance": "exec x"}, 400, "INSTANCE_INVALID", "实例名含空白"),
             ({"workspace": project.name, "role": "auditor", "instance": AUDITOR, "ttl": -5}, 400, "PARAMS_INVALID", "时效非正整数")]
    for body, want_status, want_code, label in cases:
        status, resp = client.post({**body, "stage": "preview"}, owner)
        lines.append(f"{label}: {json.dumps(body, ensure_ascii=False)} → {status} {resp['error']} {resp['message']}")
        assert (status, resp["error"]) == (want_status, want_code), (label, resp)
        assert "preview_token" not in resp
    _show("[⑤·预演拒原文]\n" + "\n".join(lines))
    assert _registry(project) == before, "预演拒零写入"


# ===========================================================================
# ⑥ 码原文 grep 0 命中 ⑦ 既有写例外不变 + 声明 / i18n / 单一实现守卫
# ===========================================================================

def test_item6_code_never_in_logs_audit_or_preview_store(board):
    from tools.aipos_cli.enrollment import decode_self_contained_code

    client, owner, project = board["client"], board["owner"], board["project"]
    body = {"workspace": project.name, "role": "advisor", "instance": "advisor.proj-r2.f270adv"}
    _s, pre = client.post({**body, "stage": "preview"}, owner)
    status, done = client.post({**body, "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
    assert status == 200 and done["ok"], done
    board["codes"].append(done["code"]["self_contained_code"])
    codes = list(board["codes"])
    inners = [decode_self_contained_code(c)["code"] for c in codes]
    assert len(codes) >= 3
    hits = []
    scanned = []
    for path in sorted(p for p in board["tmp"].rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned.append(path)
        hits += [f"{path}: 自包含码" for c in codes if c in text]
        if path.name != "enrollments.json":  # 门注册码库按设计存内层码(兑换比对用); 其余任何文件都不得有
            hits += [f"{path}: 内层码" for c in inners if c in text]
    logs = [p for p in scanned if p.suffix in (".jsonl", ".log", ".md") or "log" in p.name]
    _show(f"[⑥·grep 码原文] 码 {len(codes)} 张(自包含 + 内层); 扫描临时根全部文件 {len(scanned)} 个, 其中日志 / 审计 / 留痕类 {len(logs)} 个:\n"
          + "\n".join(f"  {p.relative_to(board['tmp'])}" for p in logs) + f"\n命中 {len(hits)} 处 {hits}")
    assert hits == [] and any(p.name == "board-audit-log.jsonl" for p in logs) and any(p.name == "gate.log" for p in logs)
    store_dump = repr(board["previews"]._tickets)
    assert not any(c in store_dump for c in codes + inners), "预演令牌表不含码"
    page = client.page(ONBOARDING, owner)
    assert not any(c in page for c in codes + inners), "页面重载不含码"


def test_item7_existing_write_exceptions_and_declarations():
    from tools.aipos_cli.board_enroll_code import board_declaration, role_choices
    from tools.aipos_cli.envelope_board import board_envelopes_declaration
    from tools.aipos_cli.board_project_new import board_declaration as project_new_board
    from web.board.app import _I18N_TEMPLATES, board_readonly_declaration
    from web.board.readonly_pages import _EC_JS_KEYS, EC_JS_ERROR_CODES

    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    decl = board_declaration()
    ro = verbs["board_readonly"]
    _show(f"[⑦·声明] write_path={decl['write_path']} script={decl['script_path']} action={decl['action']} 写例外清单={ro['write_exceptions']}")
    assert list(ro["write_exceptions"])[:5] == ["envelope_mint", "project_upgrade", "owner_inbox_decide", "project_new", "enroll_code"]  # AIPOS-F272: 清单只追加(⑥⑦)
    assert decl["write_path"] == WRITE_PATH and decl["page"] == "onboarding"
    assert len({decl["write_path"], project_new_board()["write_path"], board_envelopes_declaration()["write_path"]}) == 3
    assert decl["write_path"] not in board_readonly_declaration()["allowed_post_paths"]
    src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    do_post = src[src.index("def do_POST(self)"):src.index("def do_PUT(self)")]
    assert do_post.count("if path == ") == len(board_readonly_declaration()["allowed_post_paths"]) + 7  # AIPOS-F272: + 写例外⑥⑦
    assert set(EC_JS_ERROR_CODES) <= set(decl["errors"]), "页面解释的拒因码都在声明码表内"
    zh = {k for k in _I18N_TEMPLATES["zh"] if k.startswith(decl["text_prefix"])}
    en = {k for k in _I18N_TEMPLATES["en"] if k.startswith(decl["text_prefix"])}
    assert zh == en and set(_EC_JS_KEYS) <= zh and {"ec_js_err_" + c for c in EC_JS_ERROR_CODES} <= zh
    mod = (REPO_ROOT / "tools" / "aipos_cli" / "board_enroll_code.py").read_text(encoding="utf-8")
    for needed in ("handle_owner_write(", "gate_enroll_code(", "load_role_token(", "governance_seat_instance_refusal(",
                   "resolve_gate_url_default(", "list_enrollment_codes(", "default_instance_name("):
        assert needed in mod, needed
    for banned in ("create_enrollment_code(", "issue_self_contained_code(", "write_text(", "subprocess", "except Exception"):
        assert banned not in mod, banned
    assert not re.search(r"(?<![\w.])(?:print|open)\(", mod), "本模块不写文件 / 不打印(码只进应答)"
    js = (REPO_ROOT / "web" / "board" / "static" / "enroll-code.js").read_text(encoding="utf-8")
    assert not re.search(r"[一-鿿]", js), "脚本不含界面中文(文字走 data-ec-texts)"
    assert "localStorage" not in js and "sessionStorage" not in js, "码不进浏览器存储"
    defs = subprocess.run(["git", "grep", "-n", "def create_enrollment_code\\|def issue_self_contained_code\\|def gate_enroll_code", "--", "tools", "web"],
                          cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip().splitlines()
    _show("[⑦·发码唯一实现 git grep]\n" + "\n".join(defs))
    assert len(defs) == 3
    assert role_choices.__module__ == "tools.aipos_cli.board_enroll_code"


def test_role_choices_and_targets_are_derived(board):
    from tools.aipos_cli.board_enroll_code import enroll_targets, role_choices, suggested_instance

    project = board["project"]
    choices = role_choices(project)
    _show(f"[角色清单(注册表 workstation / governance_seat + 自定义)] {choices}")
    assert {"executor", "auditor", "advisor", "planner"} <= set(choices) and "owner" not in choices
    row = {"key": "executor", "state": "todo", "details": [("rp_ob_detail_instance_missing", {"instance": SUB, "reason": "x"}),
                                                          ("rp_ob_detail_instance_missing", {"instance": PI, "reason": "x"})]}
    assert [t["instance"] for t in enroll_targets(project.name, row)] == [SUB, PI]
    assert enroll_targets(project.name, {**row, "state": "ok"}) == []
    assert enroll_targets(project.name, {"key": "envelope", "state": "todo", "details": []}) == []
    adv = enroll_targets(project.name, {"key": "advisor", "state": "todo", "details": [("rp_ob_detail_advisor_none", {})]})
    assert adv == [{"role": "advisor", "instance": suggested_instance("advisor", project.name), "suggested": True}]
    assert adv[0]["instance"].startswith(f"advisor.{project.name}.")
