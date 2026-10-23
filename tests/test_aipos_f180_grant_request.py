"""AIPOS-F180 — 授权交互重做: 顾问的授权请求带结构化建议 → 收件箱与看板显示人话请求卡 →「按建议签发」(人话确认 → 成功卡片 + 返回)。

件① 请求带建议: no_envelope / envelope_renew 类收件箱项自动带建议(驱动方 / 卡面模式 / 卡面仓 / 同驱动方上一信封额度 / 项目缺省到期 / 卡标题),
    顾问 `lybra loop note --grant-*` 逐字段覆盖; CLI `owner inbox` 一行人话。
件② 看板人话请求卡: 收件箱页与信封页顶部「待授权请求」, 每张一句人话 + 按建议签发 / 修改后签发 / 不批准(告诉顾问)。
件③ 签发流程人话化: 复用 F172 唯一写路由(mode=new, 参数 = 建议)与安全框架; 确认只显示人话摘要, 成功卡片带返回链接, 失败给人话原因 +
    下一步, 技术详情默认折叠; 签发成功后对应收件箱项自动消失(信封生效即不再 no_envelope)。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; 项目名带连字符)+ 靶场 Owner 凭据(假 token, 仓外)+ 进程内门替身(F158 InProcessGate,
门侧真实写入实现)+ 测试进程内线程起的看板(测试客户端 urllib)+ node 跑真实 static/envelopes.js(最小 DOM 替身, 喂测试客户端拿到的真实应答)。
全程不读真实凭据、不碰真实治理根、不对真实门写。设 F180_SNAPSHOT_DIR 时把签发各步的静态 HTML 快照写到该目录(给 Owner 预览; 缺省不写)。
"""
from __future__ import annotations

import contextlib
import hashlib
import html
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write, init_governance_repo  # noqa: E402  — single source of rig helpers
from test_aipos_f158_envelope_landing_record_type_migration import InProcessGate  # noqa: E402  — single source of the gate double

# 靶场项目名(带连字符 = 从人肉期接入的项目形; 非真实项目)
P = "f180-lane"
OWNER = "owner"
# LANE-HW1 的驱动顾问: 上一信封是 code 模式, 卡要 docs(10-16 实撞形)
DRIVER = f"advisor.{P}.h1"
# LANE-UI2 的驱动顾问: 信封额度用尽 = envelope_renew
DRIVER2 = f"advisor.{P}.h2"
FIXTURE_TOKEN = "fixture-owner-token-f180-do-not-leak"
HW1_TITLE = "厂商资料准备"
STILL_READONLY_POSTS = ("/api/queue", "/api/execute/confirm", "/api/verify/approve", "/api/ai-author/confirm", "/api/owner/envelopes",
                        "/api/owner/inbox")


def _show(text: str) -> None:
    print(text, flush=True)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
                          cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _cli(*argv: str) -> tuple[int, str]:
    from tools.aipos_cli.aipos_cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue()


def _tree_digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


def _iso(dt) -> str:
    from tools.aipos_cli.clock import iso_z

    return iso_z(dt)


def _card(gov: Path, task_id: str, *, task_mode: str, title: str, lane: dict | None = None) -> None:
    meta = {"task_id": task_id, "title": title, "project": P, "task_mode": task_mode, "assigned_to": f"exec.{P}.h1",
            "agent_instance": f"exec.{P}.h1", "status": "claimed", "audit": "required", "audit_by": f"audit.{P}.h1",
            "output_target": "docs/", "priority": "high", "created_by": DRIVER, "needs_owner": False, "context_bundle": "t",
            "artifact_policy": "formal_write"}
    if lane:
        meta["lane"] = json.dumps(lane)
    _write(gov / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md", _fm(meta, f"# {task_id}\n\n## Goal\n夹具卡。\n"))


def _policy(gov: Path, pid: str, *, who: str, max_tasks: int, task_mode: str, active_from, expires_at) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=who, active_from=_iso(active_from), expires_at=_iso(expires_at), max_tasks=max_tasks,
        owner_approval_ref=f"fixture-{pid}", task_selector_task_mode=task_mode))


def _claims(gov: Path, pid: str, n: int) -> None:
    for i in range(n):
        _write(gov / "5_tasks" / "records" / "claims" / f"T-{pid}-{i}" / "claim.md",
               f"---\nrecord_type: claim_record\nautonomy_mode: PreAuthorized\nowner_policy_ref: {pid}\n---\n")


def _ended_run(gov: Path, task_id: str, driver: str, *, reason: str, message: str) -> str:
    """loop 自身的记录器落一份已结束运行(真实运行记录形)。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    rec = LoopRunRecorder(gov, task_id, driver=driver, contract=load_loop_contract(), warn=_show)
    rec.end(outcome="no_envelope", exit_code=5, reason=reason, message=message)
    return rec.run_id


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.clock import utc_now

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out = _cli("project", "new", P, "--home-root", str(hroot), "--actor", OWNER)
    assert rc == 0, out
    gov = hroot / P
    repos = {}
    for name in ("web-x", "contracts-x"):
        repos[name] = tmp_path / "repos" / name
        repos[name].mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repos[name]), check=True)
    pj = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    pj.pop("code_repo", None)
    pj["repos"] = {"default": "web-x", "items": {k: str(v) for k, v in repos.items()}}
    (gov / "project.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2), encoding="utf-8")
    now = utc_now()
    d = timedelta(days=1)
    # LANE-HW1: docs 卡, 驱动顾问只有 code 信封(40 次, 仍有效)= 无覆盖本卡的信封 → no_envelope
    _card(gov, "LANE-HW1", task_mode="docs", title=HW1_TITLE, lane={"repo": "contracts-x", "paths": ["docs/"], "roles": ["executor"]})
    _policy(gov, "pol_f180_lane_loop_3", who=DRIVER, max_tasks=40, task_mode="code", active_from=now - d, expires_at=now + 6 * d)
    _ended_run(gov, "LANE-HW1", DRIVER, reason="no_envelope", message="无有效 autonomy 信封, 拒绝裸跑。")
    # LANE-UI2: code 卡(未写 lane.repo = 缺省仓), 驱动顾问信封额度用尽 → envelope_renew
    _card(gov, "LANE-UI2", task_mode="code", title="界面第二轮")
    _policy(gov, "pol_f180_side_1", who=DRIVER2, max_tasks=2, task_mode="code", active_from=now - 2 * d, expires_at=now + 9 * d)
    _claims(gov, "pol_f180_side_1", 2)
    _ended_run(gov, "LANE-UI2", DRIVER2, reason="envelope_exhausted", message="信封 pol_f180_side_1 额度已用尽")
    remote = init_governance_repo(hroot)
    conn = tmp_path / "owner-vault" / "connection.json"
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                             "tokens": [{"role": "owner", "token": FIXTURE_TOKEN, "scopes": ["owner_confirm"], "token_ref": "owner-fixture"}]}))
    return {"home": hroot, "gov": gov, "remote": remote, "conn": conn, "tmp": tmp_path, "now": now}


@pytest.fixture
def frozen_clock(rig, monkeypatch: pytest.MonkeyPatch):
    """AIPOS-F189 件①(gap #166): 授权建议到期 = 当前时刻 + 天数(秒精度, suggest_grant 取产品唯一时钟入口 clock.utc_now);
    同一测试里 CLI json / CLI 人话行 / 看板页各自推导再逐字比较, 跨秒(人话行按日: 跨零点)边界会偶发不一致。
    冻结该入口于夹具时刻 rig["now"](同取自该入口), 不另造时间源。只给只读断言的测试用: 写盘测试的记录名同取该入口, 冻结会同秒撞名。"""
    import tools.aipos_cli.clock as clock

    monkeypatch.setattr(clock, "utc_now", lambda: rig["now"])
    return rig["now"]


def _inbox(rig, *extra: str) -> tuple[int, str]:
    return _cli("owner", "inbox", "--home-root", str(rig["home"]), *extra)


def _inbox_json(rig) -> dict:
    rc, out = _inbox(rig, "--json")
    assert rc in (0, 4), out
    return json.loads(out)


def _item(data: dict, task_id: str) -> dict | None:
    return next((i for i in data["items"] if i.get("task_id") == task_id and i["category"] == "loop_owner_needed"), None)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def board(rig, monkeypatch: pytest.MonkeyPatch):
    import tools.aipos_cli.confirm_client as confirm_client
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler
    from web.board.auth_otc import PreviewTokenStore

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    store = SessionStore()
    owner_sid = store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="device_code")
    owner2_sid = store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="otc")
    advisor_sid = store.create(role="advisor", scopes=["queue_publish"], token_ref="advisor-fixture", login_method="token")
    previews = PreviewTokenStore(ttl_seconds=300)
    serve_root = rig["tmp"] / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    audit_log = rig["tmp"] / "board-runtime" / "board-audit-log.jsonl"
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=serve_root, home_root=rig["home"], session_store=store, preview_store=previews,
        mint_connection_json=rig["conn"], board_audit_log_path=audit_log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**rig, "base": f"http://127.0.0.1:{server.server_address[1]}", "previews": previews, "audit_log": audit_log,
               "owner": f"{SESSION_COOKIE_NAME}={owner_sid}", "owner2": f"{SESSION_COOKIE_NAME}={owner2_sid}",
               "advisor": f"{SESSION_COOKIE_NAME}={advisor_sid}", "owner_sid": owner_sid}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _request(b, path: str, method: str = "GET", body: dict | None = None, who: str | None = "owner",
             headers: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(b["base"] + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    if who:
        req.add_header("Cookie", b[who])
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _mint(b, body: dict, who: str = "owner") -> tuple[int, dict]:
    status, text = _request(b, "/api/owner/envelopes/mint", "POST", body, who=who)
    return status, json.loads(text)


def _card_html(page: str, item_id: str) -> str:
    m = re.search(r'<article class="gr-card" id="' + re.escape(item_id) + r'".*?</article>', page, re.S)
    assert m, item_id
    return m.group(0)


def _card_request(card: str) -> dict:
    return json.loads(html.unescape(re.search(r'data-request="([^"]*)"', card).group(1)))


def _page_conf(page: str) -> dict:
    return json.loads(html.unescape(re.search(r'data-gr-texts="([^"]*)"', page).group(1)))


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split()


def _plain(fragment: str) -> str:
    return " ".join(_text(fragment))


# ---------------------------------------------------------------------------
# 静态快照(给 Owner 预览; 设 F180_SNAPSHOT_DIR 才写)。panel 段按 envelopes.js 同一模板与真实应答填好(浏览器里由脚本渲染)。
# ---------------------------------------------------------------------------

def _fill(template: str, **values) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def _snapshot(name: str, page: str, panel: str | None = None) -> None:
    out = os.environ.get("F180_SNAPSHOT_DIR")
    if not out:
        return
    css = (REPO_ROOT / "web" / "board" / "static" / "readonly-pages.css").read_text(encoding="utf-8")
    doc = page.replace('<link rel="stylesheet" href="/readonly-pages.css">', f"<style>{css}</style>")
    doc = re.sub(r'<script src="[^"]+"></script>', "", doc)
    if panel is not None:
        doc = re.sub(r'<section class="gr-panel" hidden aria-live="polite">.*?</section>', panel, doc, count=1, flags=re.S)
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / name).write_text(doc, encoding="utf-8")


def _panel_html(state: str, heading: str, lines: list[str], tech: list[str], actions: str, tech_label: str) -> str:
    esc = html.escape
    return (f'<section class="gr-panel" aria-live="polite" data-state="{state}"><h2>{esc(heading)}</h2><div class="gr-panel-body">'
            + "".join(f"<p>{esc(x)}</p>" for x in lines) + "</div>"
            + (f'<details class="gr-tech gr-panel-tech"><summary>{esc(tech_label)}</summary><pre>{esc(chr(10).join(tech))}</pre></details>'
               if tech else "") + f'<div class="gr-panel-actions">{actions}</div></section>')


# ===========================================================================
# 件① 请求带建议(临时 home)
# ===========================================================================

def test_item1_inbox_auto_suggestion_cli_line(rig, frozen_clock):
    rc, text = _inbox(rig)
    _show(f"---- 件① lybra owner inbox 原文 exit {rc} ----\n{text}")
    assert rc == 0, text
    data = _inbox_json(rig)
    hw1, ui2 = _item(data, "LANE-HW1"), _item(data, "LANE-UI2")
    assert hw1 and ui2
    g = hw1["grant"]
    _show("[件①·LANE-HW1 自动建议] " + json.dumps(g, ensure_ascii=False, sort_keys=True))
    assert g["agent_or_role"] == DRIVER and g["task_mode"] == "docs" and g["lane_repos"] == ["contracts-x"]
    # AIPOS-F192: 额度 = 业务卡张数(本卡所需 1 张), 不再照抄同驱动方上一信封的 40
    assert g["max_tasks"] == 1 and g["sources"]["max_tasks"] == "card_need" and g["previous_policy_id"] == "pol_f180_lane_loop_3"
    assert g["quota_basis"] == "本卡 LANE-HW1 是一张新业务卡, 需 1 张"
    assert g["policy_id"] == "pol_f180_lane_loop_4", "F166 next_policy_id: 同驱动方同前缀最大序号 +1"
    assert g["reason"] == HW1_TITLE and g["sources"]["reason"] == "card" and g["sources"]["lane_repos"] == "card"
    assert g["expires_at"][:10] == _iso(rig["now"] + timedelta(days=7))[:10] and g["sources"]["expires_at"] == "project_default"
    u = ui2["grant"]
    _show("[件①·LANE-UI2 自动建议(envelope_renew)] " + json.dumps(u, ensure_ascii=False, sort_keys=True))
    assert ui2["facts"]["reason"] == "envelope_renew" and u["agent_or_role"] == DRIVER2 and u["task_mode"] == "code"
    assert u["lane_repos"] == ["web-x"] and u["sources"]["lane_repos"] == "derived", "未写 lane.repo = project.json 里指向卡所在仓的仓名"
    assert u["max_tasks"] == 1 and u["sources"]["max_tasks"] == "card_need" and u["policy_id"] == "pol_f180_side_2"  # AIPOS-F192: 原 2
    line = next(ln for ln in text.splitlines() if "授权请求:" in ln and "LANE-HW1" in ln)
    _show(f"[件①·CLI 人话行原文] {line.strip()}")
    # AIPOS-F211 件③(F208 P1-5): 项目未声明 execution 时卡面无 harness = loop 按 card_harness 缺省(default_by_task_mode)拉 pi, 收件箱同读
    # (原收件箱按「卡面 harness 是否 pi」= 不含 pi, 与 loop 两口径); 本卡 audit: required 的审计阶段亦拉 pi
    assert g["launch_harnesses"] == ["pi"] and g["sources"]["launch_harnesses"] == "card_execution"
    assert line.strip() == (f"授权请求: {DRIVER} 请求为 LANE-HW1({HW1_TITLE})开一个只写文档的授权, 仅 contracts-x, 允许 loop 拉起 pi 工位, 1 张业务卡(每张含其审计与返工), "
                            f"到 {g['expires_at'][:10]}。理由: {HW1_TITLE}")
    decide = next(ln for ln in text.split("LANE-HW1", 1)[1].splitlines() if ln.strip().startswith("裁决:"))
    _show(f"[件①·裁决行原文] {decide.strip()}")
    assert f"/owner/inbox#{hw1['item_id']}" in decide and "deny_grant" in decide
    assert [o["key"] for o in hw1["options"]] == ["renew", "deny_grant", "pause"]


def test_item1_loop_note_grant_overrides_and_validation(rig):
    from tools.aipos_cli.clock import utc_now

    until = _iso(utc_now() + timedelta(days=13))[:10]
    base = ("loop", "note", "--task-id", "LANE-HW1", "--workspace-root", str(rig["gov"]))
    bad = [("--grant-mode", "bogus"), ("--grant-max-tasks", "0"), ("--grant-expires", "2001-01-01"), ("--grant-expires", "2030-01-01T00:00:00")]
    lines = []
    for flag, value in bad:
        rc, out = _cli(*base, flag, value)
        lines.append(f"{flag} {value} → exit {rc}: {out.strip()}")
        assert rc == 2, out
    rc, out = _cli(*base)
    lines.append(f"(无 --ask 也无 --grant-*) → exit {rc}: {out.strip()}")
    assert rc == 2
    _show("[件①·loop note --grant-* 校验拒原文]\n" + "\n".join(lines))
    rc, out = _cli(*base, "--grant-max-tasks", "6", "--grant-expires", until, "--grant-reason", "只补 contracts-x 文档准备, 不改代码",
                   "--actor", DRIVER, "--confirm")
    _show(f"---- 件①·loop note --grant-* --confirm 原文 exit {rc} ----\n{out}")
    assert rc == 0, out
    assert "授权建议(覆盖推导值): max_tasks=6 · expires_at=" + until in out and "请 Owner 按授权建议签发信封" in out
    rc, text = _inbox(rig)
    data = _inbox_json(rig)
    g = _item(data, "LANE-HW1")["grant"]
    _show("[件①·覆盖后建议] " + json.dumps({k: g[k] for k in ("max_tasks", "expires_at", "reason", "task_mode", "lane_repos", "sources")},
                                          ensure_ascii=False, sort_keys=True))
    assert g["max_tasks"] == 6 and g["expires_at"] == f"{until}T23:59:59Z" and g["reason"] == "只补 contracts-x 文档准备, 不改代码"
    assert g["sources"]["max_tasks"] == g["sources"]["expires_at"] == g["sources"]["reason"] == "advisor"
    assert g["sources"]["task_mode"] == "card" and g["lane_repos"] == ["contracts-x"]
    line = next(ln for ln in text.splitlines() if "授权请求:" in ln and "LANE-HW1" in ln)
    _show(f"[件①·覆盖后 CLI 人话行原文] {line.strip()}")
    assert line.strip().endswith(f"仅 contracts-x, 允许 loop 拉起 pi 工位, 6 张业务卡(每张含其审计与返工), 到 {until}。理由: 只补 contracts-x 文档准备, 不改代码")
    msg = _git(rig["home"], "log", "-1", "--format=%s")
    assert _git(rig["home"], "status", "--porcelain", "-uall") == "", f"loop note 写后已落账: {msg}"


def test_item1_project_default_and_unreadable_named(rig):
    """AIPOS-F192: 额度 = project.json grant_request.max_tasks 显式声明者, 未声明 = 本卡所需(1 张业务卡; 原缺省 20); 段形不合 = 收件箱点名, 事项照列(不猜)。"""
    from tools.aipos_cli.owner_inbox import suggest_grant

    pj_path = rig["gov"] / "project.json"
    pj = json.loads(pj_path.read_text(encoding="utf-8"))
    fm = {"task_id": "LANE-X", "task_mode": "docs", "title": "x", "lane": {"repo": "contracts-x"}}
    got = suggest_grant(rig["gov"], P, task_id="LANE-X", card_fm=fm, driver=f"advisor.{P}.fresh")
    assert got["max_tasks"] == 1 and got["sources"]["max_tasks"] == "card_need" and got["policy_id"] == f"pol_{P}_loop_1"
    pj["grant_request"] = {"max_tasks": 9, "days": 3}
    pj_path.write_text(json.dumps(pj), encoding="utf-8")
    got = suggest_grant(rig["gov"], P, task_id="LANE-X", card_fm=fm, driver=f"advisor.{P}.fresh", now=rig["now"])
    assert got["max_tasks"] == 9 and got["sources"]["max_tasks"] == "project_default"
    assert got["expires_at"][:10] == _iso(rig["now"] + timedelta(days=3))[:10]
    pj["grant_request"] = {"max_tasks": 0, "bogus": 1}
    pj_path.write_text(json.dumps(pj), encoding="utf-8")
    rc, text = _inbox(rig)
    _show(f"[件①·grant_request 段不合 → 收件箱原文(节选)]\n" + "\n".join(ln for ln in text.splitlines() if "授权请求" in ln))
    assert rc == 0 and "推不出建议(GrantRequestDefaultsError: GRANT_REQUEST_INVALID" in text and "bogus" in text


# ===========================================================================
# 件②③ 看板(测试客户端)
# ===========================================================================

def test_item2_inbox_and_envelope_pages_show_request_cards(board, frozen_clock):
    data = _inbox_json(board)
    hw1 = _item(data, "LANE-HW1")
    status, page = _request(board, "/owner/inbox")
    assert status == 200
    card = _card_html(page, hw1["item_id"])
    _show(f"---- 件②·收件箱页请求卡 HTML 原文 ----\n{card}")
    sentence = _plain(re.search(r'<p class="gr-sentence">.*?</p>', card).group(0))
    _show(f"[件②·请求卡人话原文] {sentence}")
    assert sentence == (f"{DRIVER} 请求:为 LANE-HW1({HW1_TITLE})开一个只写文档的授权,仅 contracts-x,允许 loop 拉起 pi 工位,1 张业务卡(每张含其审计与返工),到 "
                        f"{hw1['grant']['expires_at'][:10]}。理由:{HW1_TITLE}")
    buttons = re.findall(r'<button type="button"[^>]*data-gr-action="(\w+)">(.*?)</button>', card)
    _show(f"[件②·按钮] {buttons}")
    assert buttons == [("sign", "按建议签发"), ("edit", "修改后签发"), ("deny", "不批准(告诉顾问)")]
    edit = re.search(r'<form class="gr-edit" hidden>.*?</form>', card).group(0)
    names = re.findall(r'<input name="(\w+)"', edit)
    _show(f"[件②·修改后签发表单(只露三字段)原文] {edit}")
    assert names == ["max_tasks", "expires_date", "lane_repo"]
    deny = _plain(re.search(r'<div class="gr-deny" hidden>.*?</details></div>', card).group(0))  # AIPOS-F181: advisor command folded
    _show(f"[件②·不批准(告诉顾问)原文] {deny}")
    assert deny.startswith(f"告诉 {DRIVER}:这次不批准 LANE-HW1 的授权") and f"--item {hw1['item_id']}" in deny
    tech = re.search(r'<details class="gr-tech">.*?</details>', card, re.S).group(0)
    _show(f"[件②·技术详情默认折叠原文] {tech}")
    assert "<details class=\"gr-tech\">" in tech and " open" not in tech and "policy_id=pol_f180_lane_loop_4" in tech
    req = _card_request(card)
    assert req["payload"] == {"workspace": P, "mode": "new", "agent_or_role": DRIVER, "task_mode": "docs", "lane_repo": ["contracts-x"],
                              "launch_harness": ["pi"], "max_tasks": 1, "expires_at": hw1["grant"]["expires_at"],
                              "policy_id": "pol_f180_lane_loop_4", "request_item": hw1["item_id"]}
    # F174 skeleton: top nav on the page; owner sign client loaded; no read-only note on this page
    nav = re.search(r'<header class="rp-nav">.*?</header>', page).group(0)
    _show(f"[件②·顶部导航原文] {nav}")
    assert 'href="/owner/inbox" aria-current="page"' in nav and 'href="/owner/envelopes"' in nav and 'href="/owner/overview"' in nav
    assert '<script src="/envelopes.js"></script>' in page and 'data-owner="true"' in page and "rp-readonly-note" not in page
    assert f'data-owner-inbox-count="{data["count"]}"' in page and FIXTURE_TOKEN not in page
    _snapshot("01-inbox-request-cards.html", page)
    status, env_page = _request(board, "/owner/envelopes")
    top = env_page[env_page.index('<section class="gr-requests"'):env_page.index('<section class="envelope-project"')]
    _show(f"[件②·信封页顶部「待授权请求」标题] {_plain(re.search(r'<h2>.*?</h2>', top).group(0))}")
    assert status == 200 and "待授权请求(2)" in top and hw1["item_id"] in top and 'href="/owner/envelopes" aria-current="page"' in env_page
    _snapshot("02-envelopes-request-cards.html", env_page)


def test_item2_non_owner_sees_request_without_buttons(board):
    status, page = _request(board, "/owner/inbox", who="advisor")
    hw1 = _item(_inbox_json(board), "LANE-HW1")
    card = _card_html(page, hw1["item_id"])
    note = _plain(re.search(r'<section class="gr-requests".*?<p class="rp-readonly-note">.*?</p>', page, re.S).group(0).split("</h2>")[1])
    _show(f"[件②·非 Owner 会话] 按钮={'data-gr-action' in card} 脚本={'envelopes.js' in page} 提示={note}")
    assert status == 200 and "data-gr-action" not in card and "<form" not in card and "envelopes.js" not in page
    assert 'data-owner="false"' in page and "data-gr-texts" not in page and "签发只对 Owner 登录会话开放" in note
    status, env_page = _request(board, "/owner/envelopes", who="advisor")
    assert status == 200 and "<form" not in env_page and "envelopes.js" not in env_page


def test_item3_sign_as_suggested_end_to_end_and_item_disappears(board):
    home, gov = board["home"], board["gov"]
    data = _inbox_json(board)
    hw1 = _item(data, "LANE-HW1")
    status, page = _request(board, "/owner/inbox")
    payload = _card_request(_card_html(page, hw1["item_id"]))["payload"]
    before = _tree_digest(home)
    status, pre = _mint(board, {**payload, "stage": "preview"})
    _show(f"[件③·按建议签发 → 预演 status={status}] params={json.dumps(pre.get('params'), ensure_ascii=False, sort_keys=True)}")
    assert status == 200 and pre["ok"] and pre["params"]["policy_id"] == "pol_f180_lane_loop_4" and pre["params"]["task_mode"] == "docs"
    assert pre["params"]["lane_repo"] == ["contracts-x"] and pre["params"]["request_item"] == hw1["item_id"]
    assert pre["params"]["decision_summary"].endswith(f"(应授权请求 {hw1['item_id']})")
    assert _tree_digest(home) == before, "预演零写入"
    status, done = _mint(board, {**payload, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show(f"[件③·确认 status={status}] signed={done.get('signed')} landing_line={done.get('landing_line')}")
    assert status == 200 and done["ok"] and done["signed"][0]["policy_id"] == "pol_f180_lane_loop_4"
    policy = (gov / "5_tasks" / "policies" / "pol_f180_lane_loop_4.md").read_text(encoding="utf-8")
    _show(f"[件③·信封落盘 frontmatter]\n{policy.split('---')[1].strip()}")
    assert f"agent_or_role: {DRIVER}" in policy and "task_selector_task_mode: docs" in policy and "contracts-x" in policy
    assert _git(home, "status", "--porcelain", "-uall") == "" and _git(home, "rev-parse", "HEAD") == _git(board["remote"], "rev-parse", "main")
    # 信封生效 = 该请求自行解除: CLI 与看板都不再列
    after = _inbox_json(board)
    _show(f"[件③·签发后收件箱] 事项 {[(i['category'], i.get('task_id')) for i in after['items']]} (签前 {data['count']} 项, 签后 {after['count']} 项)")
    # AIPOS-F192: 建议额度 = 1 张业务卡 → 新信封 1/1 张即达 F166 剩余额度预警阈值(≤3), 收件箱多一条该信封的 envelope_warning
    fresh_warn = [i for i in after["items"] if i["category"] == "envelope_warning" and "pol_f180_lane_loop_4" in json.dumps(i, ensure_ascii=False)]
    assert _item(after, "LANE-HW1") is None and _item(after, "LANE-UI2") is not None
    assert after["count"] == data["count"] - 1 + len(fresh_warn) and len(fresh_warn) <= 1
    status, page_after = _request(board, "/owner/inbox")
    assert hw1["item_id"] not in page_after and "待授权请求(1)" in page_after
    rc, text = _inbox(board)
    _show(f"[件③·签发后 CLI 收件箱原文]\n{text}")
    # AIPOS-F201 件③: the request item is gone; the card now only appears in the "decided, to relay" paragraph for its driver
    items_part, _, relay_part = text.partition("已定待转达")
    assert "LANE-HW1" not in items_part and f"告诉 {DRIVER}:" in relay_part and "Owner 已签发信封 pol_f180_lane_loop_4" in relay_part
    _snapshot("06-inbox-after-signing.html", page_after)
    _flow_snapshots(board, page, pre, done, hw1)


def _flow_snapshots(board, page: str, pre: dict, done: dict, item: dict) -> None:
    """确认 / 成功两步的静态快照: panel 按 envelopes.js 同一模板(页面 data-gr-texts)与真实应答填写。"""
    conf = _page_conf(page)
    t = conf["t"]
    p = pre["params"]
    lines = [_fill(t["gr_confirm_who"], agent=p["agent_or_role"]), _fill(t["gr_confirm_what"], mode=conf["modes"][p["task_mode"]]),
             _fill(t["gr_confirm_repos"], repos=", ".join(p["lane_repo"]) or t["gr_repos_any"]), _fill(t["gr_confirm_count"], count=p["max_tasks"]),
             _fill(t["gr_confirm_until"], date=p["expires_at"][:10]), _fill(t["gr_confirm_project"], project=p["project"]),
             _fill(t["gr_confirm_card"], task_id=item["task_id"], title_part=_fill(t["gr_title_part"], title=item["title"]))]
    tech = list(pre["preview_lines"]) + ["", json.dumps(pre["dry_run"], indent=2, ensure_ascii=False)]
    actions = (f'<button type="button" class="gr-primary">{html.escape(t["gr_confirm_btn"])}</button>'
               f'<button type="button">{html.escape(t["gr_cancel_btn"])}</button>')
    _snapshot("03-confirm-plain-summary.html", page, _panel_html("confirm", t["gr_confirm_heading"], lines, tech, actions, t["gr_tech"]))
    ok_tech = [f"{s['policy_id']} -> {', '.join(s['written'])}" for s in done["signed"]] + [done.get("landing_line") or "",
               f"actor={done['actor']} / {done['capture_surface']} / {done['captured_by']}"]
    _snapshot("04-signed-success-card.html", page, _panel_html(
        "signed", _fill(t["gr_ok_heading"], policy_id=done["signed"][0]["policy_id"]),
        [conf["refreshing"]], ok_tech, "", t["gr_tech"]))


def test_item3_edit_then_sign_three_fields_and_failure_plain_reason(board):
    from tools.aipos_cli.clock import utc_now

    data = _inbox_json(board)
    ui2 = _item(data, "LANE-UI2")
    status, page = _request(board, "/owner/inbox")
    req = _card_request(_card_html(page, ui2["item_id"]))
    conf = _page_conf(page)
    date = _iso(utc_now() + timedelta(days=4))[:10]
    edited = {**req["payload"], "max_tasks": 5, "expires_at": date + conf["date_only_time"], "lane_repo": ["web-x", "contracts-x"]}
    status, pre = _mint(board, {**edited, "stage": "preview"})
    _show(f"[件③·修改后签发 预演 status={status}] {json.dumps({k: pre['params'][k] for k in ('policy_id', 'max_tasks', 'expires_at', 'lane_repo')}, ensure_ascii=False)}")
    assert status == 200 and pre["params"]["max_tasks"] == 5 and pre["params"]["expires_at"] == f"{date}T23:59:59Z"
    assert pre["params"]["lane_repo"] == ["web-x", "contracts-x"] and pre["params"]["policy_id"] == "pol_f180_side_2"
    # failure: confirm with a changed parameter → plain reason + next step from the page texts (same keys the script uses)
    status, bad = _mint(board, {**edited, "max_tasks": 50, "stage": "confirm", "preview_token": pre["preview_token"]})
    reason, nxt = conf["errors"][bad["error"]]
    shown = [_fill(reason, message=bad["message"]), _fill(conf["t"]["gr_fail_next"], next=_fill(nxt, message=bad["message"]))]
    _show(f"[件③·失败 HTTP {status} {bad['error']}] 人话原因原文: {shown[0]} / {shown[1]}")
    assert status == 403 and bad["error"] == "PREVIEW_PARAMS_MISMATCH"
    assert shown == ["确认前内容变了(可能刚有人签了同一个编号)。", "下一步:刷新页面后重新签。"]
    _snapshot("05-failure-plain-reason.html", page, _panel_html(
        "failed", conf["t"]["gr_fail_heading"], shown, [f"HTTP {status} {bad['error']}", bad["message"]],
        f'<button type="button" class="gr-primary">{html.escape(conf["t"]["gr_retry_btn"])}</button>', conf["t"]["gr_tech"]))
    status, pre2 = _mint(board, {**edited, "stage": "preview"})
    status, done = _mint(board, {**edited, "stage": "confirm", "preview_token": pre2["preview_token"]})
    _show(f"[件③·修改后签发 确认 status={status}] signed={done.get('signed')}")
    assert status == 200 and done["signed"][0]["policy_id"] == "pol_f180_side_2"
    assert _item(_inbox_json(board), "LANE-UI2") is None


def test_item3_every_declared_error_has_plain_reason_and_next_step():
    from tools.aipos_cli.envelope_board import board_envelopes_declaration
    from tools.aipos_cli.owner_inbox import grant_declaration
    from tools.schema_loader import get_enum_values
    from web.board.app import _I18N_TEMPLATES

    codes = list(board_envelopes_declaration()["errors"])
    for lang in ("zh", "en"):
        missing = [f"gr_{kind}_{code}" for code in codes for kind in ("err", "next") if f"gr_{kind}_{code}" not in _I18N_TEMPLATES[lang]]
        missing += [f"gr_mode_{m}" for m in get_enum_values("task_mode") if f"gr_mode_{m}" not in _I18N_TEMPLATES[lang]]
        assert not missing, (lang, missing)
    zh = _I18N_TEMPLATES["zh"]
    assert {m: zh[f"gr_mode_{m}"] for m in get_enum_values("task_mode")} == grant_declaration()["mode_labels"], "CLI 与看板模式说法一致"
    assert sorted(k for k in _I18N_TEMPLATES["zh"] if k.startswith("gr_")) == sorted(k for k in _I18N_TEMPLATES["en"] if k.startswith("gr_"))


def test_item3_security_six_token_refusals_and_other_writes_405(board):
    """F172 安全框架对授权请求签发照样成立: 非 Owner / 跨站 / 令牌六类(缺 / 错 / 已用 / 过期 / 他会话 / 改参数)拒; 其余写路由 405。"""
    data = _inbox_json(board)
    status, page = _request(board, "/owner/inbox")
    payload = _card_request(_card_html(page, _item(data, "LANE-HW1")["item_id"]))["payload"]
    before = _tree_digest(board["home"])
    lines = []

    def confirm(token, who="owner", **over):
        body = {**payload, **over, "stage": "confirm"}
        if token is not None:
            body["preview_token"] = token
        return _mint(board, body, who=who)

    status, resp = _mint(board, {**payload, "stage": "preview"}, who="advisor")
    lines.append(f"非 Owner → {status} {resp['error']}")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    status, text = _request(board, "/api/owner/envelopes/mint", "POST", {**payload, "stage": "preview"}, headers={"Origin": "http://evil.example"})
    lines.append(f"跨站 Origin → {status} {json.loads(text)['error']}")
    assert status == 403 and json.loads(text)["error"] == "ORIGIN_MISMATCH"
    status, resp = confirm(None)
    lines.append(f"缺令牌 → {status} {resp['error']}")
    assert resp["error"] == "PREVIEW_TOKEN_REQUIRED"
    status, resp = confirm("not-a-real-token")
    lines.append(f"错令牌 → {status} {resp['error']}")
    assert resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, other = _mint(board, {**payload, "stage": "preview"}, who="owner2")
    status, resp = confirm(other["preview_token"])
    lines.append(f"他会话令牌 → {status} {resp['error']}")
    assert resp["error"] == "PREVIEW_SESSION_MISMATCH"
    _s, pre = _mint(board, {**payload, "stage": "preview"})
    status, resp = confirm(pre["preview_token"], max_tasks=99)
    lines.append(f"改参数 → {status} {resp['error']}")
    assert resp["error"] == "PREVIEW_PARAMS_MISMATCH"
    status, resp = confirm(pre["preview_token"])
    lines.append(f"已用令牌 → {status} {resp['error']}")
    assert resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, pre = _mint(board, {**payload, "stage": "preview"})
    store = board["previews"]
    real_now = store._now
    store._now = lambda: real_now() + 301
    try:
        status, resp = confirm(pre["preview_token"])
    finally:
        store._now = real_now
    lines.append(f"过期令牌 → {status} {resp['error']}")
    assert resp["error"] == "PREVIEW_TOKEN_EXPIRED"
    status, resp = _mint(board, {**payload, "request_item": "not an id", "stage": "preview"})
    lines.append(f"request_item 形不合 → {status} {resp['error']} {resp['message']}")
    assert status == 400 and resp["error"] == "PARAMS_INVALID"
    for route in STILL_READONLY_POSTS:
        status, text = _request(board, route, "POST", {"workspace": P, "owner_confirmed": True})
        lines.append(f"POST {route} → {status} {json.loads(text)['error']}")
        assert status == 405 and json.loads(text)["error"] == "BOARD_READ_ONLY", route
    _show("[件③·安全拒原文]\n" + "\n".join(lines))
    assert _tree_digest(board["home"]) == before and not (board["gov"] / "5_tasks" / "policies" / "pol_f180_lane_loop_4.md").exists()
    assert FIXTURE_TOKEN not in board["audit_log"].read_text(encoding="utf-8")


# ===========================================================================
# 真实前端脚本(node 跑 static/envelopes.js, 最小 DOM 替身, 喂测试客户端拿到的真实应答)
# ===========================================================================

_NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
function el(tag) {
  const e = { tagName: tag, children: [], attrs: {}, hidden: false, open: false, textContent: '', className: '', listeners: {},
    appendChild(c) { this.children.push(c); return c; }, setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    addEventListener(t, f) { this.listeners[t] = f; }, scrollIntoView() {}, querySelector() { return null; } };
  Object.defineProperty(e, 'textContent', { get() { return this._t || ''; }, set(v) { this._t = String(v); if (v === '') { this.children = []; } } });
  return e;
}
const h2 = el('h2'), bodyDiv = el('div'), pre = el('pre'), tech = el('details'), actions = el('div'), panel = el('section');
tech.querySelector = (s) => (s === 'pre' ? pre : null);
panel.hidden = true;
panel.querySelector = (s) => ({ 'h2': h2, '.gr-panel-body': bodyDiv, '.gr-panel-tech': tech, '.gr-panel-actions': actions }[s] || null);
const body = el('body');
Object.assign(body.attrs, { 'data-owner': 'true', 'data-write-path': input.write_path, 'data-gr-texts': input.conf });
const card = el('article');
if (input.request) { card.attrs['data-request'] = input.request; }
// AIPOS-F217: envelope page renew / new forms (input.form = {mode, workspace, template, inputs: {name: value}})
const form = el('form');
if (input.form) {
  Object.assign(form.attrs, { 'data-mode': input.form.mode, 'data-workspace': input.form.workspace });
  if (input.form.template) { form.attrs['data-template'] = input.form.template; }
  const inputs = Object.keys(input.form.inputs).map((name) => ({ name, value: input.form.inputs[name] }));
  form.querySelectorAll = (s) => (s === 'input[name]' ? inputs : []);
}
const doc = { body, createElement: el,
  querySelector: (s) => (s === '.gr-panel' ? panel : null),
  querySelectorAll: (s) => (s === '.gr-card[data-request]' ? (input.request ? [card] : [])
    : s === 'form.envelope-form' ? (input.form ? [form] : []) : []) };
const calls = [];
let i = 0;
function fakeFetch(url, opts) {
  calls.push({ url, body: JSON.parse(opts.body) });
  const r = input.responses[i++];
  return Promise.resolve({ status: r.status, json: () => Promise.resolve(r.data) });
}
const nav = { assigned: null, reloaded: false };
const ctx = { document: doc, window: { location: { reload() { nav.reloaded = true; }, assign(u) { nav.assigned = String(u); } } },
  fetch: fakeFetch, JSON, Object, Array, String, parseInt, Promise };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(input.script, 'utf8'), ctx);
function snap() {
  return { state: panel.getAttribute('data-state'), hidden: panel.hidden, heading: h2.textContent,
    lines: bodyDiv.children.map((c) => c.textContent), tech_hidden: tech.hidden, tech_open: tech.open, tech: pre.textContent,
    actions: actions.children.map((c) => ({ tag: c.tagName, text: c.textContent, href: c.href || null })) };
}
const out = {};
(async () => {
  if (input.form) { form.listeners.submit({ preventDefault() {} }); } else { card.listeners.click({ target: { getAttribute: () => 'sign' } }); }
  await new Promise((r) => setTimeout(r, 20));
  out.after_preview = snap();
  const confirmBtn = actions.children[0];
  if (input.responses.length > 1 && confirmBtn && confirmBtn.listeners.click) {
    confirmBtn.listeners.click();
    await new Promise((r) => setTimeout(r, 20));
    out.after_confirm = snap();
  }
  out.card_hidden = card.hidden;
  out.navigated = nav;
  out.calls = calls.map((c) => ({ url: c.url, stage: c.body.stage, has_token: !!c.body.preview_token, request_item: c.body.request_item }));
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run_client(tmp_path: Path, page: str, card: str | None, responses: list[dict], form: dict | None = None) -> dict:
    """Run the real static/envelopes.js under node with this page's data-gr-texts: sign a request card, or (AIPOS-F217) submit an
    envelope-page form (form = {mode, workspace, template, inputs}). out.navigated = where the script sent the page after success."""
    harness = tmp_path / "harness.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    spec = {"script": str(REPO_ROOT / "web" / "board" / "static" / "envelopes.js"),
            "write_path": "/api/owner/envelopes/mint", "conf": html.unescape(re.search(r'data-gr-texts="([^"]*)"', page).group(1)),
            "request": html.unescape(re.search(r'data-request="([^"]*)"', card).group(1)) if card else None, "responses": responses,
            "form": form}
    (tmp_path / "input.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(tmp_path / "input.json")], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_item3_real_client_script_plain_confirm_success_and_failure(board, tmp_path: Path):
    data = _inbox_json(board)
    hw1 = _item(data, "LANE-HW1")
    status, page = _request(board, "/owner/inbox")
    card = _card_html(page, hw1["item_id"])
    payload = _card_request(card)["payload"]
    _s, pre = _mint(board, {**payload, "stage": "preview"})
    _s, done = _mint(board, {**payload, "stage": "confirm", "preview_token": pre["preview_token"]})
    assert done["ok"]
    (tmp_path / "ok").mkdir()
    out = _run_client(tmp_path / "ok", page, card, [{"status": 200, "data": pre}, {"status": 200, "data": done}])
    _show("[件③·真实脚本 确认步] " + json.dumps(out["after_preview"], ensure_ascii=False))
    _show("[件③·真实脚本 成功步] " + json.dumps(out["after_confirm"], ensure_ascii=False))
    conf = out["after_preview"]
    until = pre["params"]["expires_at"][:10]
    assert conf["state"] == "confirm" and conf["heading"] == "确认签发这个授权?" and conf["hidden"] is False
    assert conf["lines"] == [f"给谁:{DRIVER}", "能做什么:只写文档", "哪些仓:contracts-x", "几张卡:1 张业务卡(每张卡认领时扣 1;其交回、审计、返工、合入、结案不另扣,只受到期与撤销约束)", f"到哪天:{until}",
                             f"哪个项目:{P}", f"影响哪张卡:LANE-HW1({HW1_TITLE})"]
    assert conf["tech_hidden"] is False and conf["tech_open"] is False and "dry_run_token" not in conf["tech"], "技术详情默认折叠"
    assert [a["text"] for a in conf["actions"]] == ["确认签发", "取消"]
    ok = out["after_confirm"]
    assert ok["state"] == "signed" and ok["heading"] == "已签发 pol_f180_lane_loop_4"
    # AIPOS-F217: success = the whole inbox page reloads from the server (the paragraph to copy is its "decided, to relay" section,
    # AIPOS-F201 item 3); the panel only says it is refreshing
    assert ok["lines"] == ["已签发, 正在刷新页面…"] and ok["actions"] == []
    assert out["navigated"] == {"assigned": "/owner/inbox", "reloaded": False}
    assert ok["tech_open"] is False and "pol_f180_lane_loop_4 ->" in ok["tech"]
    assert [(c["stage"], c["has_token"], c["request_item"]) for c in out["calls"]] == [("preview", False, hw1["item_id"]),
                                                                                       ("confirm", True, hw1["item_id"])]
    # AIPOS-F217: the same request signed from the envelope page lands on its "to sign" tab with the signed banner; the server's
    # "decided, to relay" section there carries the paragraph for the driving advisor; the request card is gone
    status, env_page = _request(board, "/owner/envelopes")
    (tmp_path / "env").mkdir()
    out = _run_client(tmp_path / "env", env_page, card, [{"status": 200, "data": pre}, {"status": 200, "data": done}])
    target = out["navigated"]["assigned"]
    assert target == "/owner/envelopes?tab=pending&signed=pol_f180_lane_loop_4#pol_f180_lane_loop_4"
    status, landed = _request(board, target)
    relay = html.unescape(re.search(r'<article class="ib-relay" data-advisor="[^"]*"[^>]*>.*?<pre[^>]*>(.*?)</pre>', landed, re.S).group(1))
    _show(f"[AIPOS-F217·信封页按建议签发 → 跳转 {target}]\n横幅: {_plain(re.search(r'<p class=.env-banner.*?</p>', landed).group(0))}\n"
          f"已定待转达:\n{relay}")
    assert status == 200 and re.search(r'<a class="env-tab" data-tab="pending" data-count="\d+" href="[^"]*" aria-current="page">', landed)
    assert '<p class="env-banner" data-signed="pol_f180_lane_loop_4">' in landed and hw1["item_id"] not in landed
    assert relay.startswith(f"告诉 {DRIVER}:\n") and "Owner 已签发信封 pol_f180_lane_loop_4" in relay
    (tmp_path / "bad").mkdir()
    bad = {"ok": False, "verdict": "BLOCK", "error": "PREVIEW_TOKEN_EXPIRED", "message": "预演令牌已过期(300 秒); 请重新预演"}
    out = _run_client(tmp_path / "bad", page, card, [{"status": 403, "data": bad}])
    fail = out["after_preview"]
    _show("[件③·真实脚本 失败步] " + json.dumps(fail, ensure_ascii=False))
    assert fail["state"] == "failed" and fail["heading"] == "没签成"
    assert fail["lines"] == ["确认等得太久,已经超时。", "下一步:重新点「按建议签发」,几分钟内确认。"]
    assert fail["tech_open"] is False and "PREVIEW_TOKEN_EXPIRED" in fail["tech"]


# ===========================================================================
# 声明 / 单一实现 / 棘轮
# ===========================================================================

def test_single_write_route_single_mint_path_and_cjk_guard():
    import importlib.util

    from web.board.app import board_readonly_declaration

    src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    do_post = src[src.index("def do_POST(self)"):src.index("def do_PUT(self)")]
    assert do_post.count("if path == ") == len(board_readonly_declaration()["allowed_post_paths"]) + 2, "不新增写路由"
    pages = (REPO_ROOT / "web" / "board" / "readonly_pages.py").read_text(encoding="utf-8")
    script = (REPO_ROOT / "web" / "board" / "static" / "envelopes.js").read_text(encoding="utf-8")
    for name in ("mint_via_gate(", "record_owner_decision(", "load_role_token(", "fetch("):
        assert name not in pages, name
    assert script.count("fetch(") == 1 and "writePath" in script and "/api/" not in script, "前端只打页面声明的那一条写路由"
    spec = importlib.util.spec_from_file_location("f180_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    hits = {name: guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / name) for name in ("static/envelopes.js", "readonly_pages.py")}
    _show(f"[CJK 守卫逐行判定] {hits}")
    assert hits == {"static/envelopes.js": [], "readonly_pages.py": []}
    inbox_src = (REPO_ROOT / "tools" / "aipos_cli" / "owner_inbox.py").read_text(encoding="utf-8")
    for literal in ("/home/", "2_projects", "chris", "kiwiai", "lantu"):
        assert literal not in inbox_src.split('"""', 2)[2], f"产品代码写死 {literal!r}"
    assert "find_envelope(" in inbox_src and "next_policy_id(" in inbox_src and "envelope_overview(" in inbox_src
