"""AIPOS-F267 — 看板「设置自检」页(产品化 P3 第一张): 复用 setup_check.run_setup_check 结构化结果, 只读 GET; pi 未装 / 无可用模型 =
页顶醒目提醒 + 安装出口(只读 setup_check.pi 声明); 首页 / 接入页一行自检摘要链到本页; 看板进程内短时缓存 +「重新检查」GET。

靶场 = 临时 HOME + 临时 XDG_CONFIG_HOME + 临时 home 根(`lybra project new` 建一个靶场项目, git 仓)+ 看板服务进程工作区 = 临时治理根
(其 .lybra/connection.json 只声明 mcp.rpc_url 指向本用例的门替身, 另放一个假令牌用于断言「不进页面」); PATH 只含本用例自建 bin 目录:
python3 / git / node 指向真实可执行, pi 为夹具替身(装了 + 有模型 / 没装 / 装了无模型三种)。看板 = 测试进程内线程起的
ThreadingHTTPServer(临时端口, 测试客户端 urllib), 门替身 = 本进程线程起的 HTTP 夹具(对任何路径回 404 = 门已应答)。
不碰真实治理根 / 真实门 / 真实用户 systemd, 不起真实 pi; 夹具子进程(若有)一律经 tools/aipos_cli/tests/fixture_procs.py。

验收(卡面 ★验收): ① 三种环境页面关键段原文(全 ✓ / 去掉 pi / pi 无模型)② 首页 / 接入页摘要原文 ③ 英文页原文(声明原文照显)
④ 缓存命中与「重新检查」两次请求的检查时刻原文 ⑤ 只读(请求前后临时根 git status / 文件 mtime 不变, 进程内无写打开, 令牌不进页面)
+ 声明 / i18n / 单一实现不变量。
"""
from __future__ import annotations

import http.server
import json
import os
import html
import re
import shutil
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 靶场构件唯一来源
from test_aipos_f171_roadmap_board_readonly import _cli, _git, _tree_digest  # noqa: E402

PROJECT = "f267-probe"            # 靶场项目名(非真实项目)
ADVISOR = f"advisor.{PROJECT}.h1"
FAKE_TOKEN = "f267-fixture-token-not-a-secret"
SETUP = "/owner/setup"

PI_OK = ('case "$1" in --version) echo 0.85.1;; --list-models) printf "provider  model  context\\nkiwiai  m-one  100K\\n'
         'kiwiai  m-two  100K\\n";; esac\nexit 0\n')
PI_NO_MODELS = 'case "$1" in --version) echo 0.85.1;; --list-models) echo "No models available.";; esac\nexit 0\n'


def _show(text: str) -> None:
    print(text, flush=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def hermetic_toolchain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pi: str | None = None,
                       gate_url: str | None = None) -> Path:
    """本用例的 PATH(python3 / git / node = 真实可执行的符号链接; pi = 夹具替身或不装)+ 门地址兜底(env LYBRA_GATE_URL; 缺省 = 已关闭端口)。
    别的看板用例 GET 设置自检页时也用它: 页面会跑 run_setup_check, 须不起真实 pi、不探真实门。"""
    bindir = tmp_path / "f267-bin"
    real = {name: shutil.which(name) for name in ("python3", "git", "node")}  # 先解析(再次调用时 PATH 已是本目录)
    real = {name: os.path.realpath(path) for name, path in real.items() if path}
    if bindir.exists():
        shutil.rmtree(bindir)
    bindir.mkdir()
    for name, path in real.items():
        os.symlink(path, bindir / name)
    if pi is not None:
        _script(bindir / "pi", pi)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("LYBRA_GATE_URL", gate_url or f"http://127.0.0.1:{_free_port()}")
    return bindir


class _Gate404(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):  # noqa: N802 - 门对未知路径回 404 结构化错误的替身
        type(self).hits.append(self.path)
        body = b'{"error":{"code":"NOT_FOUND"}}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: D401
        return None


@pytest.fixture
def fake_gate():
    _Gate404.hits = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Gate404)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gate: str):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", PROJECT, "--home-root", str(hroot), "--actor", ADVISOR)
    assert rc == 0, out + err
    init_governance_repo(hroot)
    gov = hroot / PROJECT
    # 看板服务进程工作区 = 临时治理根; 门地址只经其 connection.json mcp.rpc_url(与 CLI 同一推导口), 令牌原文不得进页面
    conn = gov / ".lybra" / "connection.json"
    conn.parent.mkdir(parents=True, exist_ok=True)
    conn.write_text(json.dumps({"config_version": 1, "mcp": {"rpc_url": f"{fake_gate}/mcp"},
                                "tokens": [{"role": "owner", "token": FAKE_TOKEN, "scopes": ["owner_confirm"], "token_ref": "owner-fixture"}]}),
                    encoding="utf-8")
    return {"tmp": tmp_path, "home": hroot, "gov": gov, "gate": fake_gate, "monkeypatch": monkeypatch}


def _handwritten_unit(tmp_path: Path) -> Path:
    """临时 XDG 下一个手写常驻单元(ExecStart 含 `serve … start`)= 门常驻 ✓(resident_status 只读识别, 不跑 systemctl)。"""
    unit = tmp_path / "xdg" / "systemd" / "user" / "f267-hand.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Service]\nExecStart=/usr/bin/env lybra serve --workspace-root /x start\n", encoding="utf-8")
    return unit


@pytest.fixture
def board(rig):
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'], token_ref='owner-fixture')}"
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=rig["gov"], home_root=rig["home"], session_store=store, connection_paths=[],
        board_audit_log_path=rig["tmp"] / "board-runtime" / "audit.jsonl"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**rig, "base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie}
    finally:
        server.shutdown()
        server.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def _request(b, path: str, method: str = "GET", *, cookie: bool = True) -> tuple[int, str, dict]:
    data = b"{}" if method != "GET" else None
    req = urllib.request.Request(b["base"] + path, data=data, method=method, headers={"Content-Type": "application/json"})
    if cookie:
        req.add_header("Cookie", b["cookie"])
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


def _page(b, path: str) -> str:
    status, body, _h = _request(b, path)
    assert status == 200, (path, body[:400])
    return body


def _main(page: str) -> str:
    return re.search(r"<main>.*</main>", page, re.S).group(0)


def _items(page: str) -> list[tuple[str, str]]:
    return re.findall(r'<li class="sc-item sc-\w+" data-item="(\w+)" data-status="(\w+)">', page)


def _when(page: str) -> dict[str, str]:
    m = re.search(r'<p class="sc-when" data-checked-at="([^"]*)" data-run="([^"]*)" data-cached="(\w+)">(.*?)</p>', page, re.S)
    assert m, page[:2000]
    return {"checked_at": m.group(1), "run": m.group(2), "cached": m.group(3), "text": re.sub(r"<[^>]+>", "", m.group(4))}


# ===========================================================================
# ① 三种环境(全 ✓ / 去掉 pi / pi 无模型)
# ===========================================================================

def test_item1_all_green_no_reminder(board):
    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=PI_OK)
    _handwritten_unit(board["tmp"])
    page = _page(board, SETUP)
    _show("---- ① 全 ✓ · 设置自检页 <main> 原文 ----\n" + _main(page))
    assert 'data-readonly-page="setup"' in page and 'data-nav="engineering" aria-current="page"' in page
    assert _items(page) == [(i, "ok") for i in ("python", "node", "git", "pi", "pi_models", "gate", "gate_resident")]
    assert "sc-pi-alert" not in page and "data-pi-alert" not in page  # 全部 ✓: 不出提醒块
    assert 'data-fail="0" data-warn="0" data-ok="7"' in page and "没有 ✗ 项(7 项 ✓, 0 项 ⚠ 提示)" in page
    # 门地址 = 看板所在工作区 connection.json mcp.rpc_url(与 CLI 同一推导口), 只发一次 GET /, 永不探 /mcp
    assert f"✓</span> <strong>门(在跑且可达)</strong></p><p class=\"sc-detail\">{board['gate']}(地址来源: 工作区 {board['gov'].resolve()}" in page
    assert _Gate404.hits == ["/"]
    assert "2 个已配置模型: kiwiai/m-one, kiwiai/m-two" in page and "检测到已有手写常驻单元 f267-hand.service" in page


def test_item1_without_pi_reminder_with_install_command(board):
    from tools.aipos_cli.setup_check import setup_check_declaration

    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=None)
    page = _page(board, SETUP)
    _show("---- ① PATH 去掉 pi · 设置自检页 <main> 原文 ----\n" + _main(page))
    spec = setup_check_declaration()["pi"]
    alert = re.search(r'<section class="sc-pi-alert".*?</section>', page, re.S).group(0)
    _show("---- ① 提醒块原文 ----\n" + alert)
    assert 'data-pi-alert="missing"' in alert and "还没装 pi" in alert
    assert f'<pre class="sc-copy"><code>{spec["install"]["command"]}</code></pre>' in alert  # 安装命令 = 声明原文, 可复制代码块
    assert f'href="{spec["install"]["url"]}"' in alert
    from tools.aipos_cli.i18n import fill, texts

    # AIPOS-F277: 出口文案在 setup_check.texts 双语块(经唯一读取器取缺省语言那份, 测试不复制原文)
    exits = texts(setup_check_declaration()["texts"], None, where="setup_check.texts")["exits"]
    expected = fill(exits["pi_missing"], {"command": spec["install"]["command"], "url": spec["install"]["url"]}, where="t")
    assert expected[:12] in alert and spec["install"]["command"] in alert  # 提醒文案 = 声明 exits.pi_missing(由 run_setup_check 填好), 不另写
    assert page.index("sc-pi-alert") < page.index('class="sc-items"')  # 提醒块在页顶(清单之前)
    items = _items(page)
    assert items[0] == ("pi", "fail"), items  # ✗ 项置顶
    assert dict(items)["pi_models"] == "warn" and dict(items)["gate_resident"] == "warn"
    assert "有 1 项 ✗ 需要先处理" in page and 'class="sc-verdict rp-alert"' in page


def test_item1_pi_without_models_reminder(board):
    from tools.aipos_cli.setup_check import setup_check_declaration

    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=PI_NO_MODELS)
    page = _page(board, SETUP)
    alert = re.search(r'<section class="sc-pi-alert".*?</section>', page, re.S).group(0)
    _show("---- ① pi 在但无模型(假 pi 脚本)· 提醒块 + 清单原文 ----\n" + alert + "\n" + re.search(r'<ol class="sc-items">.*?</ol>', page, re.S).group(0))
    spec = setup_check_declaration()["pi"]
    assert 'data-pi-alert="models"' in alert and "pi 还没有可用模型" in alert and "sc-copy" not in alert
    from tools.aipos_cli.i18n import texts

    exit_models = texts(setup_check_declaration()["texts"], None, where="setup_check.texts")["exits"]["pi_models"]
    assert "<code>pi --list-models</code>" in alert and exit_models.split("`")[0] in alert
    items = _items(page)
    assert items[0] == ("pi_models", "fail") and dict(items)["pi"] == "ok"


# ===========================================================================
# ② 首页 / 接入页摘要(同一次结果的派生, 链到本页; 有 ✗ 首页标红)
# ===========================================================================

def test_item2_home_and_onboarding_summary_same_source(board):
    from tools.aipos_cli.board_home import home_declaration
    from test_aipos_f246_board_home import forbidden_hits

    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=None)
    home = _page(board, "/owner/home")
    none_line = re.search(r'<p class="sc-sum.*?</p>', home, re.S).group(0)
    _show("---- ② 首页摘要(未运行过)原文 ----\n" + none_line)
    assert 'data-setup-state="none"' in none_line and "本机安装自检还没运行" in none_line and f'href="{SETUP}"' in none_line
    setup = _page(board, SETUP)
    home = _page(board, "/owner/home")
    onboarding = _page(board, "/owner/onboarding")
    home_line = re.search(r'<p class="sc-sum.*?</p>', home, re.S).group(0)
    ob_line = re.search(r'<p class="sc-sum.*?</p>', onboarding, re.S).group(0)
    _show("---- ② 首页摘要原文 ----\n" + home_line + "\n---- ② 接入页摘要原文 ----\n" + ob_line)
    when = _when(setup)
    for line, css in ((home_line, "hm-setup"), (ob_line, "rp-ob-setup")):
        assert f"sc-sum {css} sc-sum-fail rp-alert sc-sum-bad" in line and 'data-setup-fail="1"' in line
        assert f'href="{SETUP}"' in line and "查看设置自检 →" in line
        assert f"检查于 {when['checked_at'][11:19]} UTC" in line  # 与本页同一次结果
    counts = re.search(r'data-fail="(\d+)" data-warn="(\d+)" data-ok="(\d+)"', setup).groups()
    assert re.search(r'data-setup-ok="(\d+)" data-setup-fail="(\d+)" data-setup-warn="(\d+)"', home_line).groups() == (counts[2], counts[0], counts[1])
    assert home.index("sc-sum") < home.index("hm-asks")  # 首页显著位置(「需要你」之前)
    hits = forbidden_hits(home, home_declaration()["forbidden_words"])
    assert hits == [], hits
    # 全 ✓ 后摘要不再标红
    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=PI_OK)
    _handwritten_unit(board["tmp"])
    _page(board, SETUP + "?recheck=1")
    home_ok = re.search(r'<p class="sc-sum.*?</p>', _page(board, "/owner/home"), re.S).group(0)
    _show("---- ② 全 ✓ 后首页摘要原文 ----\n" + home_ok)
    assert 'data-setup-state="ok"' in home_ok and "sc-sum-bad" not in home_ok and "✓ 7 · ✗ 0 · ⚠ 0" in home_ok


# ===========================================================================
# ③ 英文页(骨架英文; 声明 / 探测原文照显, 不报错不空白)
# ===========================================================================

def test_item3_english_page_shows_declared_text(board):
    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=None)
    page = _page(board, SETUP + "?lang=en")
    _show("---- ③ 英文设置自检页 <main> 原文 ----\n" + _main(page))
    assert '<html lang="en">' in page and "<h1>Setup check</h1>" in page and "pi is not installed" in page
    # AIPOS-F277: 检查项标签 / 现值 / 出口为双语渲染值, 英文页显示英文(经唯一读取器取声明, 测试不复制原文)
    from tools.aipos_cli.i18n import fill, texts
    from tools.aipos_cli.setup_check import setup_check_declaration

    en = texts(setup_check_declaration()["texts"], "en", where="setup_check.texts")
    assert html.escape(en["labels"]["pi"]) in page and fill(en["details"]["not_on_path"], {"exe": "pi"}, where="t") in page
    assert "How to fix" in page
    assert "shown as declared by the product" in page
    assert 'href="/owner/setup?recheck=1&amp;lang=en"' in page and "Check again" in page
    home = _page(board, "/owner/home?lang=en")
    line = re.search(r'<p class="sc-sum.*?</p>', home, re.S).group(0)
    _show("---- ③ 英文首页摘要原文 ----\n" + line)
    assert "Install check ✓" in line and 'href="/owner/setup?lang=en"' in line


# ===========================================================================
# ④ 短时缓存 +「重新检查」(带查询参数的 GET, 不新增写路由)
# ===========================================================================

def test_item4_cache_hit_and_recheck(board):
    from tools.aipos_cli.setup_check import board_cache_seconds

    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=PI_OK)
    first, second = _when(_page(board, SETUP)), _when(_page(board, SETUP))
    recheck = _when(_page(board, SETUP + "?recheck=1"))
    _show("---- ④ 两次打开 + 重新检查 · 检查时刻原文 ----\n" + "\n".join(json.dumps(w, ensure_ascii=False) for w in (first, second, recheck)))
    assert first["cached"] == "false" and second["cached"] == "true"
    assert second["checked_at"] == first["checked_at"] and second["run"] == first["run"]  # 缓存命中: 同一次结果
    assert f"{board_cache_seconds()} 秒内再打开不重跑" in second["text"]
    assert recheck["cached"] == "false" and int(recheck["run"]) > int(first["run"]) and recheck["checked_at"] >= first["checked_at"]
    assert _Gate404.hits == ["/", "/"]  # 两次运行各探一次门, 缓存命中不探
    # 写方法照旧只读应答(无新增写路由)
    status, text, _h = _request(board, SETUP, "POST")
    assert status == 405 and json.loads(text)["error"] == "BOARD_READ_ONLY"
    status, _t, headers = _request(board, SETUP, cookie=False)
    assert status == 302 and headers.get("Location") == "/login"


def test_item4_summary_never_waits_and_refreshes_stale_in_background(rig, monkeypatch):
    from tools.aipos_cli import board_pages, setup_check

    hermetic_toolchain(rig["tmp"], monkeypatch, pi=PI_OK)
    ws = rig["gov"]
    assert board_pages.setup_check_latest(ws) is None  # 没跑过 = 不等待、不当场跑
    first = board_pages.setup_check_entry(ws)
    latest = board_pages.setup_check_latest(ws)
    assert latest["run"] == first["run"] and latest["stale"] is False
    monkeypatch.setattr(setup_check, "board_cache_seconds", lambda decl=None: 1)
    board_pages._SETUP_CACHE[board_pages._setup_key(ws)]["at"] -= 5  # 过期
    stale = board_pages.setup_check_latest(ws)  # 先给上次结果, 后台重跑
    assert stale["stale"] is True and stale["run"] == first["run"]
    after = board_pages.setup_check_entry(ws)  # 等后台那一次(同一时刻只跑一次)
    _show(f"[④ 摘要过期后台重跑] first run={first['run']} stale run={stale['run']} after run={after['run']}")
    assert after["run"] > first["run"]


def test_item4_cache_seconds_declared_with_default():
    from tools.aipos_cli.setup_check import BOARD_CACHE_DEFAULT_SECONDS, SetupCheckError, board_cache_seconds, setup_check_declaration

    decl = setup_check_declaration()
    assert decl["board"]["cache_seconds"] == BOARD_CACHE_DEFAULT_SECONDS == board_cache_seconds()
    assert board_cache_seconds({k: v for k, v in decl.items() if k != "board"}) == BOARD_CACHE_DEFAULT_SECONDS  # 缺键 = 缺省
    for bad in (0, -1, "60", True, None):
        with pytest.raises(SetupCheckError):
            board_cache_seconds({**decl, "board": {"cache_seconds": bad}})


# ===========================================================================
# ⑤ 只读: 请求前后临时根 git status / mtime 不变, 进程内无写打开, 令牌不进页面
# ===========================================================================

def _mtimes(root: Path) -> dict[str, int]:
    return {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def test_item5_read_only_no_writes_no_token(board):
    hermetic_toolchain(board["tmp"], board["monkeypatch"], pi=None)
    roots = {"home_root": board["home"], "xdg": board["tmp"] / "xdg", "home": board["tmp"] / "home"}
    for r in roots.values():
        r.mkdir(parents=True, exist_ok=True)
    status_before = _git(board["home"], "status", "--porcelain")
    before = {k: (_tree_digest(r), _mtimes(r)) for k, r in roots.items()}
    writes: list[str] = []
    watching = threading.Event()

    def hook(event: str, args: tuple) -> None:
        if watching.is_set() and event == "open" and len(args) >= 2 and isinstance(args[1], str) and any(c in args[1] for c in "wax+"):
            writes.append(f"{args[0]} {args[1]}")

    sys.addaudithook(hook)
    watching.set()  # 进程内写打开只盯设置自检页本身(首页 / 接入页其余推导是既有实现, 由下面的 git status / 摘要 / mtime 核零写入)
    try:
        pages = [_page(board, p) for p in (SETUP, SETUP + "?recheck=1", SETUP + "?lang=en")]
    finally:
        watching.clear()
    pages += [_page(board, p) for p in ("/owner/home", "/owner/onboarding")]
    after = {k: (_tree_digest(r), _mtimes(r)) for k, r in roots.items()}
    status_after = _git(board["home"], "status", "--porcelain")
    _show(f"[⑤ 只读] git status 前={status_before!r} 后={status_after!r}; 文件数 "
          + ", ".join(f"{k}={len(v[0])}" for k, v in after.items()) + f"; 进程内写打开={writes}")
    assert status_after == status_before and after == before
    assert writes == []
    assert not (board["tmp"] / "xdg" / "systemd").exists(), "设置自检只读, 不建单元目录"
    assert all(FAKE_TOKEN not in p for p in pages), "令牌原文不进页面"


# ===========================================================================
# 声明 / i18n / 单一实现
# ===========================================================================

def test_declarations_i18n_and_single_implementation():
    from tools.aipos_cli.board_home import ENGINEERING_LINKS, home_declaration
    from tools.aipos_cli.board_pages import PAGES, pages_declaration
    from web.board.app import _I18N_TEMPLATES

    decl = pages_declaration()
    assert decl["pages"]["setup"] == SETUP and "setup" in PAGES and decl["query"]["recheck"] == "recheck"
    assert decl["setup"]["recheck_value"] == "1" and "setup" in home_declaration()["engineering_links"] and "setup" in ENGINEERING_LINKS
    assert "setup" not in decl["home"]["nav"]  # 归「工程」导航(顶栏四项不变), 首页另有摘要入口
    zh, en = _I18N_TEMPLATES["zh"], _I18N_TEMPLATES["en"]
    render = (REPO_ROOT / "web" / "board" / "readonly_pages.py").read_text(encoding="utf-8")
    used = set(re.findall(r"""['"](sc_[a-z_]+)['"]""", render)) - {"sc_pi_"}
    used |= {f"sc_pi_{k}_title" for k in ("missing", "models")} | {"eng_setup", "eng_setup_desc"}
    missing = sorted(k for k in used if k not in zh or k not in en)
    assert not missing, missing
    pages = (REPO_ROOT / "tools" / "aipos_cli" / "board_pages.py").read_text(encoding="utf-8")
    assert pages.count("run_setup_check(") == 1 and "run_setup_check(" not in render  # 数据只调唯一实现一处, 渲染不另算
    for banned in ("shutil.which", "_version_of", "_probe_gate", "--version", "--list-models", "urlopen", "resident_status("):
        assert banned not in pages and banned not in render, banned  # 看板不另写探测
    app = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    assert "workspace_root=repo_root" in app and "start_setup_refresh" not in app  # 门地址 = 看板所在工作区; 启动不预跑(不起探测子进程)
