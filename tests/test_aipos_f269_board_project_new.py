"""AIPOS-F269 — 看板写例外④「新建项目」(产品化 P3 第二张)。

验收(卡面 ★验收):
  ① 临时 home 根 + 临时治理根起看板(临时端口, 子进程经 tools/aipos_cli/tests/fixture_procs.py), Owner 会话经既有 OTC 登录
     (测试令牌 mint → /login?otc=… 换 cookie)预演 → 确认建项目: 预演 JSON 原文 + 确认后目录树与 project.json 原文 + 审计日志行原文。
  ② 新路由的预演令牌六类核验(缺 / 错 / 已用 / 过期 / 他会话 / 改参数)各一用例。
  ③ 非 Owner 会话拒(既有拒绝码 OWNER_ONLY)。
  ④ 同名 / 非法名 / 产品仓不存在三类预演拒原文(零写入)。
  ⑤ 既有三写例外不变: 框架缺省 workspace 解析(已注册项目)照旧; 三写例外各自的回归用例在 test_aipos_f172 / f176 / f226 全过。
  ⑥ 接入页建成后 HTML 关键段原文(中 / 英): 新建入口(Owner 表单 / 他人只读提示)+ 建成横幅 + 新项目清单首个未完成项标「下一步」。
另: home 根不在 git 仓 = 建成但落账未完成(写入已生效, 带补账命令); 预演后目录被占 = 确认拒; 声明 / i18n / 单一实现守卫。
不碰真实治理根 / 真实 home 根 / 真实门; 全部在 pytest 临时目录; 子进程看板只经 fixture_procs 起停。
"""
from __future__ import annotations

import http.client
import json
import os
import re
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  (临时治理仓 + 裸远端: 单一夹具)
from tools.aipos_cli.tests import fixture_procs  # noqa: E402

WRITE_PATH = "/api/owner/projects/new"
ONBOARDING = "/owner/onboarding"
EXISTING = "alpha"
OWNER_TOK = "owner-f269-test-token-XYZ"
OWNER2_TOK = "owner2-f269-test-token-QRS"
ADVISOR_TOK = "advisor-f269-test-token-ABC"


def _show(text: str) -> None:
    print(text, flush=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True).stdout.strip()


def _snapshot(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".git" not in p.relative_to(root).parts)


def _write_connection(serve_root: Path) -> None:
    from web.board.app import _token_fingerprint

    conn = {"tokens": [
        {"role": "owner", "token_ref": "owner-fixture", "scopes": ["owner_confirm"], "token": OWNER_TOK, "fingerprint": _token_fingerprint(OWNER_TOK)},
        {"role": "owner", "token_ref": "owner2-fixture", "scopes": ["owner_confirm"], "token": OWNER2_TOK,
         "fingerprint": _token_fingerprint(OWNER2_TOK)},
        {"role": "advisor", "token_ref": "advisor-fixture", "scopes": ["queue_publish"], "token": ADVISOR_TOK,
         "fingerprint": _token_fingerprint(ADVISOR_TOK)},
    ]}
    path = serve_root / ".lybra" / "connection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(conn), encoding="utf-8")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 HOME / home 根(治理仓 + 裸远端)/ 已注册项目 alpha / 看板工作区(含测试令牌)/ 临时产品仓(git)。"""
    from tools.aipos_cli.workspace_config import scaffold_project

    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    (tmp_path / "user-home").mkdir()
    home = tmp_path / "lybra-home"
    home.mkdir()
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    scaffold_project(home, EXISTING, registered_by="fixture")
    init_governance_repo(home)
    serve = tmp_path / "serve-root"
    (serve / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    _write_connection(serve)
    product = tmp_path / "product-repo"
    product.mkdir()
    _git(product, "init", "-q", "-b", "main")
    return {"tmp": tmp_path, "home": home.resolve(), "serve": serve, "product": product.resolve()}


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
        """既有 OTC 登录通道: 测试令牌 mint 一次性码 → GET /login?otc=… 换会话 cookie(令牌本身不出现在 cookie / 应答里)。"""
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


@pytest.fixture
def board(rig):
    """进程内看板(临时端口; 与产品同一 make_handler / OTC / 会话 / 预演令牌表), 便于拨预演令牌时钟。"""
    from web.board.app import SessionStore, make_handler
    from web.board.auth_otc import DeviceCodeStore, OTCStore, PreviewTokenStore

    previews = PreviewTokenStore(ttl_seconds=300)
    audit_log = rig["tmp"] / "board-runtime" / "board-audit-log.jsonl"
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=rig["serve"], home_root=rig["home"], session_store=SessionStore(),
        connection_paths=[rig["serve"] / ".lybra" / "connection.json"], otc_store=OTCStore(), device_store=DeviceCodeStore(),
        auth_log_path=rig["tmp"] / "board-runtime" / "auth-log.jsonl", preview_store=previews, board_audit_log_path=audit_log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = _Client(server.server_address[1])
    try:
        yield {**rig, "client": client, "previews": previews, "audit_log": audit_log, "owner": client.otc_login(OWNER_TOK),
               "owner2": client.otc_login(OWNER2_TOK), "advisor": client.otc_login(ADVISOR_TOK)}
    finally:
        server.shutdown()
        server.server_close()


def _audit(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _section(page: str, pattern: str) -> str:
    m = re.search(pattern, page, re.S)
    assert m, pattern
    return m.group(0)


# ===========================================================================
# ① 子进程看板(临时端口)+ OTC 登录 → 预演 → 确认 → 接入页(⑥ 中 / 英)
# ===========================================================================

def _wait_port(port: int, proc: subprocess.Popen, seconds: float = 90.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"看板子进程提前退出 rc={proc.returncode}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise AssertionError(f"看板 {seconds}s 内未监听 127.0.0.1:{port}")


def test_item1_and_6_subprocess_board_otc_preview_confirm_onboarding(rig):
    home, product = rig["home"], rig["product"]
    port = _free_port()
    env = {**os.environ, "HOME": str(rig["tmp"] / "user-home"), "LYBRA_HOME_ROOT": str(home), "PYTHONPATH": str(REPO_ROOT)}
    argv = [sys.executable, "-m", "web.board.app", "--host", "127.0.0.1", "--port", str(port), "--repo-root", str(rig["serve"]),
            "--no-prewarm"]
    with fixture_procs.running(argv, env=env, cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        _wait_port(port, proc)
        client = _Client(port)
        owner, advisor = client.otc_login(OWNER_TOK), client.otc_login(ADVISOR_TOK)
        _show(f"[①·看板子进程] pid={proc.pid} 127.0.0.1:{port} home 根={home} 看板工作区={rig['serve']}(OTC 登录 Owner / advisor 各一会话)")

        page = client.page(ONBOARDING, owner)
        new_section = _section(page, r'<section class="pn-new".*?</section>')
        _show(f"[⑥·接入页「新建项目」入口 Owner 会话 原文]\n{new_section}")
        assert 'data-owner="true"' in new_section and 'name="workspace"' in new_section and 'name="code_repo"' in new_section
        assert 'data-pn-owner="true"' in page and f'data-pn-write-path="{WRITE_PATH}"' in page and '<script src="/project-new.js">' in page
        ro = client.page(ONBOARDING, advisor)
        ro_section = _section(ro, r'<section class="pn-new".*?</section>')
        _show(f"[⑥·接入页「新建项目」入口 非 Owner 会话 原文]\n{ro_section}")
        assert 'data-owner="false"' in ro_section and "<form" not in ro_section and "project-new.js" not in ro
        assert "新建项目只对 Owner 登录会话开放" in ro_section and "lybra project new &lt;项目名&gt; --home-root" in ro_section

        before = _snapshot(home)
        name = "beta"
        status, pre = client.post({"workspace": name, "code_repo": str(product), "stage": "preview"}, owner)
        _show(f"[①·预演 JSON 原文] HTTP {status}\n{json.dumps(pre, ensure_ascii=False, indent=1)}")
        assert status == 200 and pre["ok"] and pre["stage"] == "preview" and pre["preview_token"]
        plan = pre["plan"]
        assert pre["params"]["governance_root"] == str(home / name) and pre["params"]["code_repo"] == str(product)
        assert {"project.json", "governance/decision_log.md", "5_tasks/queue/pending/"} <= set(plan["tree"])
        rehearsed = json.loads(plan["project_json"])
        assert rehearsed["project"] == name and rehearsed["registered_by"] == pre["params"]["actor"]
        assert rehearsed["registered_by"].startswith("board:owner:owner-fixture:session-sha256:")
        assert rehearsed["repos"] == {"default": name, "items": {name: str(product)}} and rehearsed["code_repo"] == str(product)
        assert plan["repo"] == {"path": str(product), "exists": True, "git_root": str(product), "is_git_root": True}
        assert plan["governance_repo"] == {"git": True, "repo_root": str(home)} and plan["non_git_exit"] is None
        assert _snapshot(home) == before, "预演零写入(演练在一次性临时 home 根)"

        head_before = _git(home, "rev-parse", "HEAD")
        status, done = client.post({"workspace": name, "code_repo": str(product), "stage": "confirm",
                                    "preview_token": pre["preview_token"]}, owner)
        _show(f"[①·确认应答] HTTP {status}\n{json.dumps({k: v for k, v in done.items() if k != 'created'}, ensure_ascii=False, indent=1)}")
        assert status == 200 and done["ok"] and done["stage"] == "confirm" and done["capture_surface"] == "board"
        root = home / name
        tree = subprocess.run(["find", ".", "-not", "-path", "./.git*"], cwd=str(root), capture_output=True, text=True, check=True).stdout
        _show(f"[①·确认后目录树 {root}]\n{tree}")
        pj = (root / "project.json").read_text(encoding="utf-8")
        _show(f"[①·确认后 project.json 原文]\n{pj}")
        data = json.loads(pj)
        assert data["project"] == name and data["registered_by"] == done["actor"] and data["repos"]["items"] == {name: str(product)}
        assert done["created"]["tree"] == plan["tree"] and done["redirect"] == f"{ONBOARDING}?created={name}#{name}"
        assert done["landing"]["landed"] is True and done["landing"]["pushed"] is True
        log = _git(home, "log", "-1", "--format=%B", "--name-only")
        _show(f"[①·落账 git log -1]\n{log}")
        assert _git(home, "rev-parse", "HEAD") != head_before and f"Actor: {done['actor']}" in log and f"{name}/project.json" in log
        assert _git(home, "rev-parse", "HEAD") == _git(home, "rev-parse", "origin/main"), "已推送"
        audit_path = rig["serve"] / ".lybra" / "board-audit-log.jsonl"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        _show(f"[①·看板审计日志原文 {audit_path}]\n" + "\n".join(lines))
        audit = [json.loads(ln) for ln in lines]
        assert [(a["action"], a["stage"], a["outcome"]) for a in audit] == [
            ("project_new", "preview", "previewed"), ("project_new", "confirm", "confirming"), ("project_new", "confirm", "created")]
        assert audit[-1]["project"] == name and audit[-1]["landed"] is True and audit[-1]["actor"] == done["actor"]
        assert not any(tok in audit_path.read_text(encoding="utf-8") for tok in (OWNER_TOK, OWNER2_TOK, ADVISOR_TOK)), "审计日志不含令牌原文"

        for lang, banner_text, next_text in (("zh", f"已建项目 {name}。下一步:", "下一步"), ("en", f"Project {name} created. Next step:", "Next step")):
            query = f"?created={name}" + ("&lang=en" if lang == "en" else "")
            after = client.page(ONBOARDING + query, owner)
            banner = _section(after, r'<section class="pn-created".*?</section>')
            proj = _section(after, rf'<section class="rp-ob-project" id="{name}".*?</section>')
            nxt = _section(proj, r'<li class="rp-ob-item[^"]*" data-item="[^"]+" data-state="(?:todo|error)" data-next="true">.*?</li>')
            _show(f"[⑥·建成后接入页 {lang} 横幅原文]\n{banner}\n[⑥·{lang} 新项目清单首个未完成项原文]\n{nxt}")
            assert banner_text in banner and f'href="#{name}"' in banner and "lybra onboarding guide beta --home-root" in banner
            assert next_text in nxt and proj.count('data-next="true"') == 1
            item = re.search(r'data-item="([^"]+)"', nxt).group(1)
            assert f'data-next-item="{item}"' in banner
    assert proc.poll() is not None and not fixture_procs.tagged(proc), "看板子进程已整组回收"


# ===========================================================================
# ② 预演令牌六类 ③ 非 Owner ④ 三类预演拒
# ===========================================================================

def test_item2_six_token_refusals_on_new_route(board):
    client, owner, home = board["client"], board["owner"], board["home"]
    before = _snapshot(home)
    lines = []

    def confirm(token, *, code_repo=None, cookie=owner):
        body = {"workspace": "gamma", "stage": "confirm", "code_repo": code_repo}
        if token is not None:
            body["preview_token"] = token
        return client.post(body, cookie)

    status, resp = confirm(None)
    lines.append(f"① 缺令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_REQUIRED"
    status, resp = confirm("not-a-real-token")
    lines.append(f"② 错令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, other = client.post({"workspace": "gamma", "stage": "preview"}, board["owner2"])
    status, resp = confirm(other["preview_token"])
    lines.append(f"③ 他会话令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_SESSION_MISMATCH"
    _s, pre = client.post({"workspace": "gamma", "stage": "preview"}, owner)
    status, resp = confirm(pre["preview_token"], code_repo=str(board["product"]))
    lines.append(f"④ 改参数(预演无产品仓, 确认带产品仓) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_PARAMS_MISMATCH"
    status, resp = confirm(pre["preview_token"])
    lines.append(f"⑤ 已用令牌(单次即焚) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, pre = client.post({"workspace": "gamma", "stage": "preview"}, owner)
    store = board["previews"]
    real_now = store._now
    store._now = lambda: real_now() + 301
    try:
        status, resp = confirm(pre["preview_token"])
    finally:
        store._now = real_now
    lines.append(f"⑥ 过期令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_EXPIRED"
    _show("[②·新路由令牌六类拒原文]\n" + "\n".join(lines))
    assert _snapshot(home) == before and not (home / "gamma").exists(), "被拒确认零写入"
    outcomes = [a["outcome"] for a in _audit(board["audit_log"])]
    _show(f"[②·审计日志 outcome 序列] {outcomes}")
    assert "created" not in outcomes and "confirming" not in outcomes and outcomes.count("PREVIEW_TOKEN_INVALID") == 2


def test_item3_non_owner_and_framework_checks(board):
    client, home = board["client"], board["home"]
    before = _snapshot(home)
    lines = []
    status, resp = client.post({"workspace": "delta", "stage": "preview"}, board["advisor"])
    lines.append(f"非 Owner(advisor)会话预演 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    status, resp = client.post({"workspace": "delta", "stage": "confirm", "preview_token": "x"}, board["advisor"])
    lines.append(f"非 Owner(advisor)会话确认 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    status, resp = client.post({"workspace": "delta", "stage": "preview"}, board["owner"], headers={"Origin": "http://evil.example"})
    lines.append(f"跨站 Origin → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "ORIGIN_MISMATCH"
    status, resp = client.post({"workspace": "delta", "stage": "preview"}, board["owner"], headers={"Content-Type": "text/plain"})
    lines.append(f"非 JSON 体 → {status} {resp['error']} {resp['message']}")
    assert status == 400 and resp["error"] == "PARAMS_INVALID"
    status, resp = client.post({"workspace": "delta", "stage": "go"}, board["owner"])
    lines.append(f"stage 不合 → {status} {resp['error']} {resp['message']}")
    assert status == 400 and resp["error"] == "STAGE_INVALID"
    status, text, _sc, _loc = client.request("PUT", WRITE_PATH, {"workspace": "delta", "stage": "preview"}, cookie=board["owner"])
    lines.append(f"PUT 写路由 → {status} {json.loads(text)['error']}")
    assert status == 405
    _show("[③·非 Owner 与框架核验拒原文]\n" + "\n".join(lines))
    assert _snapshot(home) == before
    assert [a["role"] for a in _audit(board["audit_log"])][:2] == ["advisor", "advisor"], "非 Owner 被拒也留痕"


def test_item4_preview_refusals_name_exists_invalid_repo_missing(board):
    client, owner, home = board["client"], board["owner"], board["home"]
    (home / "occupied").mkdir()
    (home / "occupied" / "notes.txt").write_text("not a project\n", encoding="utf-8")
    before = _snapshot(home)
    lines = []
    cases = [({"workspace": EXISTING}, 409, "PROJECT_EXISTS", "同名(home 根已注册项目)"),
             ({"workspace": "occupied"}, 409, "PROJECT_EXISTS", "同名(目录已存在且非空)"),
             ({"workspace": "../escape"}, 400, "PROJECT_NAME_INVALID", "非法名(../)"),
             ({"workspace": "a/b"}, 400, "PROJECT_NAME_INVALID", "非法名(含 /)"),
             ({"workspace": str(home / "abs")}, 400, "PROJECT_NAME_INVALID", "非法名(绝对路径)"),
             ({"workspace": ".."}, 400, "PROJECT_NAME_INVALID", "非法名(..)"),
             ({"workspace": "  "}, 400, "WORKSPACE_REQUIRED", "空名"),
             ({"workspace": "epsilon", "code_repo": str(board["tmp"] / "no-such-repo")}, 400, "CODE_REPO_NOT_FOUND", "产品仓不存在"),
             ({"workspace": "epsilon", "code_repo": "relative/repo"}, 400, "CODE_REPO_INVALID", "产品仓非绝对路径(set-repos 同一校验)"),
             ({"workspace": "epsilon", "code_repo": 7}, 400, "PARAMS_INVALID", "产品仓非字符串")]
    for body, want_status, want_code, label in cases:
        status, resp = client.post({**body, "stage": "preview"}, owner)
        lines.append(f"{label}: workspace={body['workspace']!r} → {status} {resp['error']} {resp['message']}")
        assert (status, resp["error"]) == (want_status, want_code), (label, resp)
        assert "preview_token" not in resp
    _show("[④·预演拒原文]\n" + "\n".join(lines))
    assert _snapshot(home) == before and not (home.parent / "escape").exists(), "预演拒零写入"
    msg = next(ln for ln in lines if "产品仓非绝对路径" in ln)
    assert "lybra-project-new-rehearsal-" not in msg and str(home) in msg, "拒因里的演练临时根换回真 home 根"


# ===========================================================================
# 其余行为: 治理仓未就绪 = 建成但落账未完成; 预演后目录被占 = 确认拒; ⑤ 框架缺省解析不变
# ===========================================================================

def test_not_in_git_creates_but_landing_reports_remedy(tmp_path, monkeypatch):
    from tools.aipos_cli.board_project_new import handle_project_new_request
    from web.board.auth_otc import PreviewTokenStore

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / "plain-home"
    home.mkdir()
    store = PreviewTokenStore(ttl_seconds=300)
    session = {"role": "owner", "token_ref": "owner-fixture", "session_ref": "sha256:0123456789abcdef", "login_method": "otc",
               "source_ip": "127.0.0.1"}
    kw = {"session": session, "home_root": home, "preview_store": store, "audit_log_path": tmp_path / "audit.jsonl",
          "content_type": "application/json"}
    status, pre = handle_project_new_request({"workspace": "solo", "stage": "preview"}, **kw)
    _show(f"[非 git home 根·预演落账事实] governance_repo={pre['plan']['governance_repo']} non_git_exit={pre['plan']['non_git_exit']}")
    assert status == 200 and pre["plan"]["governance_repo"]["git"] is False and "home git-init" in pre["plan"]["non_git_exit"]
    status, done = handle_project_new_request({"workspace": "solo", "stage": "confirm", "preview_token": pre["preview_token"]}, **kw)
    _show(f"[非 git home 根·确认] HTTP {status} landing_line=\n{done['landing_line']}")
    assert status == 200 and done["ok"] and (home / "solo" / "project.json").is_file()
    assert done["landing"]["landed"] is False and "补账: lybra governance-commit" in done["landing_line"]
    last = _audit(tmp_path / "audit.jsonl")[-1]
    assert last["outcome"] == "created" and last["landed"] is False


def test_confirm_refused_when_target_taken_after_preview(board):
    client, owner, home = board["client"], board["owner"], board["home"]
    _s, pre = client.post({"workspace": "zeta", "stage": "preview"}, owner)
    (home / "zeta").mkdir()
    (home / "zeta" / "someone-else.txt").write_text("x\n", encoding="utf-8")
    status, resp = client.post({"workspace": "zeta", "stage": "confirm", "preview_token": pre["preview_token"]}, owner)
    _show(f"[预演后目录被占·确认] {status} {resp['error']} {resp['message']}")
    assert status == 409 and resp["error"] == "PROJECT_EXISTS" and not (home / "zeta" / "project.json").exists()


def test_item5_framework_default_resolution_unchanged(tmp_path, monkeypatch):
    """写例外①②③不传 workspace = 缺省 registered(home 根已注册项目); 未知解析方式 = 声明 / 实现错(fail-closed)。"""
    from tools.aipos_cli.envelope_board import WORKSPACE_MODES, handle_owner_write
    from tools.aipos_cli.project_upgrade import board_declaration as upgrade_board
    from tools.schema_loader import SchemaLoadError
    from web.board.auth_otc import PreviewTokenStore

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / "h"
    home.mkdir()
    session = {"role": "owner", "token_ref": "t", "session_ref": "sha256:00", "login_method": "otc", "source_ip": "-"}

    def never(*_a, **_k):
        raise AssertionError("回调不应被调用")

    kw = {"decl": upgrade_board(), "action": "probe", "session": session, "home_root": home, "preview_store": PreviewTokenStore(ttl_seconds=300),
          "audit_log_path": tmp_path / "a.jsonl", "prepare": never, "preview": never, "confirm": never, "content_type": "application/json"}
    status, resp = handle_owner_write({"workspace": "fresh", "stage": "preview"}, **kw)
    _show(f"[⑤·缺省解析 registered] {status} {resp['error']} {resp['message']}")
    assert status == 400 and resp["error"] == "WORKSPACE_NOT_IN_HOME"
    assert WORKSPACE_MODES == ("registered", "new")
    with pytest.raises(SchemaLoadError):
        handle_owner_write({"workspace": "fresh", "stage": "preview"}, workspace="other", **kw)


# ===========================================================================
# 声明 / i18n / 单一实现守卫
# ===========================================================================

def test_declarations_i18n_and_single_implementation():
    from tools.aipos_cli.board_project_new import board_declaration
    from tools.aipos_cli.governance_commit import post_write_landing_declaration
    from web.board.app import _I18N_TEMPLATES
    from web.board.readonly_pages import _PN_JS_KEYS, PN_JS_ERROR_CODES

    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    decl = board_declaration()
    _show(f"[声明] write_path={decl['write_path']} script={decl['script_path']} action={decl['action']} 写例外清单="
          f"{verbs['board_readonly']['write_exceptions']}")
    assert decl["write_path"] == WRITE_PATH and decl["action"] == "project_new" and decl["page"] == "onboarding"
    assert list(verbs["board_readonly"]["write_exceptions"])[:4] == ["envelope_mint", "project_upgrade", "owner_inbox_decide", "project_new"]  # AIPOS-F270: 清单只追加(⑤ enroll_code)
    assert verbs["verbs"]["lybra_project_new"]["single_implementation"] == "tools/aipos_cli/workspace_config.scaffold_project"
    assert decl["landing"] in post_write_landing_declaration()["commands"]
    assert set(PN_JS_ERROR_CODES) <= set(decl["errors"]), "页面解释的拒因码都在声明码表内"
    zh = {k for k in _I18N_TEMPLATES["zh"] if k.startswith(decl["text_prefix"])}
    en = {k for k in _I18N_TEMPLATES["en"] if k.startswith(decl["text_prefix"])}
    assert zh == en and set(_PN_JS_KEYS) <= zh and {"pn_js_err_" + c for c in PN_JS_ERROR_CODES} <= zh
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_project_new.py").read_text(encoding="utf-8")
    for needed in ("handle_owner_write(", "scaffold_project(", "set_project_repos(", "land_after_write(", "project_new_products(",
                   'workspace="new"'):
        assert needed in src, needed
    for banned in ("write_text(", "mkdir(", "subprocess", "load_role_token(", "GateClient(", "except Exception", "token\"]"):
        assert banned not in src, banned
    js = (REPO_ROOT / "web" / "board" / "static" / "project-new.js").read_text(encoding="utf-8")
    assert not re.search(r"[一-鿿]", js), "脚本不含界面中文(文字走 data-pn-texts)"
