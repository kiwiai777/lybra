"""AIPOS-F172 — 看板信封页 + Owner 在看板授权签发 / 续签信封(看板写例外①); 信封状态单一推导(旧信封被接替不再预警, gap #160)。

件① 信封页(只读): GET /owner/envelopes 按项目列信封(policy_id / 驱动方 / 作用范围 / 用量上限 / 到期 / 状态色), 每个驱动方「当前在用」在上,
    已接替 / 已过期 / 已停用折叠; 状态全部读 autonomy_policy.envelope_overview(唯一推导)。
件② Owner 授权(写例外): POST /api/owner/envelopes/mint stage=preview → 预演全文 + 本会话预演令牌; stage=confirm(同参数 + 令牌)→ envelope mint
    唯一实现经门签发 + F158 落账; actor = 登录会话 Owner, 来源 = 看板(会话指纹 / 登录方式 / 来源 IP); 非 Owner 403; workspace 缺 / 不在 home 根拒;
    无 / 错 / 过期 / 他会话 / 已用 / 改参数令牌拒; 审计日志写不进拒签; 其余写路由仍 405。
件③ 收件箱联动: 被同驱动方更新信封接替的旧信封不再预警(收件箱 / loop status 预警同读); 信封项「裁决」指向看板信封页(保留 CLI 命令)。

靶场 = 临时 home 根(git 仓 + 临时远端裸仓, 两个靶场项目)+ 靶场 Owner 凭据(假 token, 仓外)+ 进程内门替身(门侧真实写入实现,
只替换 HTTP 传输; 复用 F158 InProcessGate)+ 测试进程内线程起的看板(测试客户端 urllib)。全程不读真实凭据、不碰真实治理根、不对真实门写。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
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

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源
from test_aipos_f158_envelope_landing_record_type_migration import InProcessGate  # noqa: E402  — 门替身唯一来源

P1, P2 = "f172-one", "f172-two"     # 靶场项目名(非真实项目)
OWNER = "owner"
FIXTURE_TOKEN = "fixture-owner-token-f172-do-not-leak"
ADV1 = f"advisor.{P1}.h1"
ADV1B = f"advisor.{P1}.h2"
AUD1 = f"audit.{P1}.h1"
ADV2 = f"advisor.{P2}.h1"

#: 下线的看板 POST 业务路由抽样(F171 全表之一部分) + 信封页只读 GET 路径的写方法
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


def _policy(gov: Path, pid: str, *, who: str, max_tasks: int, active_from, expires_at, task_mode: str = "code") -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=who, active_from=_iso(active_from), expires_at=_iso(expires_at), max_tasks=max_tasks,
        owner_approval_ref=f"fixture-{pid}", task_selector_task_mode=task_mode))


def _claims(gov: Path, pid: str, n: int) -> None:
    """已放行 PreAuthorized 认领记录 n 条(门 count_bound 同一计数口径)。"""
    for i in range(n):
        _write(gov / "5_tasks" / "records" / "claims" / f"T-{pid}-{i}" / "claim.md",
               f"---\nrecord_type: claim_record\nautonomy_mode: PreAuthorized\nowner_policy_ref: {pid}\n---\n")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """home 根 = 治理仓(带 origin 裸仓), 两个靶场项目各若干信封; gap #160 形: P1 旧角色级信封(advisor)将到期, 已被同驱动方更新的实例信封接替。"""
    from tools.aipos_cli.clock import utc_now

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    for name in (P1, P2):
        rc, out = _cli("project", "new", name, "--home-root", str(hroot), "--actor", OWNER)
        assert rc == 0, out
    g1, g2 = hroot / P1, hroot / P2
    now = utc_now()
    d = timedelta(days=1)
    # P1: 旧角色级信封 loop_3(5 小时后到期 = 原本会预警)/ loop_2(已过期)被实例信封 loop_9 接替; 另一实例 h2 的信封不被接替; 审计信封将尽
    _policy(g1, "pol_one_loop_2", who="advisor", max_tasks=20, active_from=now - 9 * d, expires_at=now - d)
    _policy(g1, "pol_one_loop_3", who="advisor", max_tasks=20, active_from=now - 5 * d, expires_at=now + timedelta(hours=5))
    _policy(g1, "pol_one_loop_9", who=ADV1, max_tasks=40, active_from=now - d, expires_at=now + 20 * d)
    _policy(g1, "pol_one_side_1", who=ADV1B, max_tasks=40, active_from=now - 3 * d, expires_at=now + 20 * d)
    _policy(g1, "pol_one_audit_1", who=AUD1, max_tasks=3, active_from=now - 2 * d, expires_at=now + 20 * d, task_mode="audit")
    _claims(g1, "pol_one_loop_9", 2)
    _claims(g1, "pol_one_audit_1", 1)  # AIPOS-F201 件④: 未用过(used=0)的信封不报剩余预警; 用过 1 张、剩余 2/3 = 照报
    # P2: 用尽(无接替者 = 仍是当前, 待续签)+ 生效中的另一驱动方信封
    _policy(g2, "pol_two_loop_1", who=ADV2, max_tasks=2, active_from=now - 2 * d, expires_at=now + 10 * d)
    _claims(g2, "pol_two_loop_1", 2)
    _policy(g2, "pol_two_audit_1", who=f"audit.{P2}.h1", max_tasks=30, active_from=now - 2 * d, expires_at=now + 30 * d, task_mode="audit")
    remote = init_governance_repo(hroot)
    conn = tmp_path / "owner-vault" / "connection.json"  # 靶场 Owner 凭据(假 token, 仓外; 只出指纹)
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                             "tokens": [{"role": "owner", "token": FIXTURE_TOKEN, "scopes": ["owner_confirm"], "token_ref": "owner-fixture"}]}))
    return {"home": hroot, "g1": g1, "g2": g2, "remote": remote, "conn": conn, "tmp": tmp_path}


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
               "advisor": f"{SESSION_COOKIE_NAME}={advisor_sid}", "owner_sid": owner_sid, "server": server}
    finally:
        server.shutdown()
        server.server_close()


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


def _mint(b, body: dict, who: str = "owner", headers: dict | None = None) -> tuple[int, dict]:
    status, text = _request(b, "/api/owner/envelopes/mint", "POST", body, who=who, headers=headers)
    return status, json.loads(text)


def _renew_body(**extra) -> dict:
    from tools.aipos_cli.clock import utc_now

    return {"workspace": P1, "mode": "renew", "template_policy_id": "pol_one_loop_9", "max_tasks": 30,
            "expires_at": _iso(utc_now() + timedelta(days=7)), **extra}


def _row(html_text: str, pid: str) -> str:
    m = re.search(r'<tr id="' + re.escape(pid) + r'"[^>]*>.*?</tr>', html_text, re.S)
    assert m, pid
    return m.group(0)


# ===========================================================================
# 单一推导(状态 / 接替)
# ===========================================================================

def test_single_derivation_superseded_and_states(rig):
    from tools.aipos_cli.autonomy_policy import envelope_overview, envelope_warning

    ov = envelope_overview(rig["g1"])
    rows = {r["policy_id"]: r for r in ov["envelopes"]}
    table = "\n".join(f"{pid}: who={r['agent_or_role']} state={r['state']} status={r['status']} current={r['current']} "
                      f"superseded_by={r['superseded_by']} used={r['used']}/{r['max_tasks']} why={r['why']}" for pid, r in rows.items())
    _show(f"[推导·{P1} envelope_overview]\n{table}\n[drivers] {ov['drivers']}")
    assert rows["pol_one_loop_3"]["status"] == "superseded" and rows["pol_one_loop_3"]["superseded_by"] == "pol_one_loop_9"
    assert envelope_warning(rows["pol_one_loop_3"]["usage"]), "未接替判定时它本会预警(5 小时后到期) = gap #160 原形"
    assert rows["pol_one_loop_2"]["status"] == "expired" and not rows["pol_one_loop_2"]["current"]
    assert rows["pol_one_loop_9"]["status"] == "active" and rows["pol_one_loop_9"]["current"] and rows["pol_one_loop_9"]["used"] == 2
    assert rows["pol_one_loop_9"]["next_policy_id"] == "pol_one_loop_10"
    assert rows["pol_one_side_1"]["current"] and rows["pol_one_side_1"]["superseded_by"] is None, "另一具体实例的信封互不接替"
    assert rows["pol_one_audit_1"]["status"] == "warn" and rows["pol_one_audit_1"]["current"]
    two = {r["policy_id"]: r for r in envelope_overview(rig["g2"])["envelopes"]}
    assert two["pol_two_loop_1"]["status"] == "exhausted" and two["pol_two_loop_1"]["current"], "用尽且无接替者 = 仍当前(待续签)"


# ===========================================================================
# 件① 信封页(只读部分)
# ===========================================================================

def test_item1_envelope_page_rows_status_tone_and_folding(board):
    status, page = _request(board, "/owner/envelopes")
    assert status == 200
    p1 = page[page.index(f'data-project="{P1}"'):page.index(f'data-project="{P2}"')]
    current_part = re.search(r'<table class="envelope-current">.*?</table>', p1, re.S).group(0)
    folded_part = p1.split('<details class="envelope-history">', 1)[1]
    shown = {pid: _row(p1, pid) for pid in ("pol_one_loop_9", "pol_one_side_1", "pol_one_audit_1", "pol_one_loop_3", "pol_one_loop_2")}
    _show("[件①·信封页 HTML 原文(测试客户端) 项目 " + P1 + "]\n" + "\n".join(shown.values()))
    summary = re.search(r"<summary>(.*?)</summary>", folded_part).group(1)
    _show(f"[件①·折叠摘要] {summary}")
    assert 'data-tone="ok"' in shown["pol_one_loop_9"] and "pol_one_loop_9" in current_part
    assert 'data-tone="alert"' in shown["pol_one_audit_1"] and "剩余 2/3 张业务卡" in shown["pol_one_audit_1"]  # AIPOS-F192: 单位 = 业务卡
    assert 'data-status="superseded"' in shown["pol_one_loop_3"] and 'data-tone="muted"' in shown["pol_one_loop_3"]
    assert "被 pol_one_loop_9 接替" in shown["pol_one_loop_3"] and "pol_one_loop_3" in folded_part and "pol_one_loop_3" not in current_part
    assert 'data-status="expired"' in shown["pol_one_loop_2"] and "pol_one_loop_2" in folded_part
    assert summary.startswith("已接替 / 已过期 / 已停用 2 张")
    assert "task_mode=audit" in shown["pol_one_audit_1"] and "拉起=只手工 /go" in shown["pol_one_audit_1"]
    p2 = page[page.index(f'data-project="{P2}"'):]
    _show("[件①·项目 " + P2 + " 用尽行] " + _row(p2, "pol_two_loop_1"))
    assert 'data-status="exhausted"' in _row(p2, "pol_two_loop_1") and 'data-tone="alert"' in _row(p2, "pol_two_loop_1")
    # Owner 会话: 当前在用行有续签表单(预填 policy_id +1), 每项目有新签表单
    assert 'data-mode="renew"' in shown["pol_one_loop_9"] and "续签 → pol_one_loop_10" in shown["pol_one_loop_9"]
    assert f'data-mode="new" data-workspace="{P1}"' in page and '<script src="/envelopes.js"></script>' in page
    assert FIXTURE_TOKEN not in page


def test_item1_non_owner_sees_readonly_page_without_forms(board):
    status, page = _request(board, "/owner/envelopes", who="advisor")
    _show(f"[件①·非 Owner 会话页] status={status} 含表单={'<form' in page} 只读提示="
          f"{re.search(r'<p class=.envelope-readonly.>(.*?)</p>', page).group(1)}")
    assert status == 200 and "<form" not in page and "envelopes.js" not in page and 'data-owner="false"' in page
    status, _ = _request(board, "/owner/envelopes", who=None)
    assert status == 200  # urllib 跟随 302 到登录页
    status, api = _request(board, "/api/owner/envelopes", who="advisor")
    data = json.loads(api)["data"]
    assert status == 200 and [p["project"] for p in data["projects"]] == [P1, P2]
    assert FIXTURE_TOKEN not in api and "usage" not in json.dumps(data["projects"][0]["envelopes"][0])


# ===========================================================================
# 件② Owner 授权
# ===========================================================================

def test_item2_owner_renew_preview_confirm_lands_with_board_provenance(board):
    home, g1 = board["home"], board["g1"]
    head_before = _git(home, "rev-parse", "HEAD")
    before = _tree_digest(home)
    body = _renew_body()
    status, pre = _mint(board, {**body, "stage": "preview"})
    _show(f"[件②·预演 status={status}]\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and pre["ok"] and pre["params"]["policy_id"] == "pol_one_loop_10"
    assert any(line.startswith(f"影响谁: {ADV1}") for line in pre["preview_lines"])
    assert "额度: 30 张业务卡(每张卡认领时扣 1; 其交回、审计、返工、合入、结案不另扣、不受额度拦截, 只受到期与撤销约束)" in pre["preview_lines"] and any("续签自 pol_one_loop_9" in line for line in pre["preview_lines"])
    assert pre["dry_run"]["data"]["autonomy_policy_id"] == "pol_one_loop_10" and "dry_run_token" not in json.dumps(pre["dry_run"])
    assert _tree_digest(home) == before, "预演零写入(治理根)"
    status, done = _mint(board, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show(f"[件②·确认 status={status}] {json.dumps(done, ensure_ascii=False)}")
    assert status == 200 and done["ok"] and done["signed"][0]["policy_id"] == "pol_one_loop_10"
    assert done["actor"] == OWNER and done["capture_surface"] == "board" and done["captured_by"].startswith("board:owner:owner-fixture:session-sha256:")
    assert done["landing"]["landed"] is True and "落账: verdict=PASS committed=True pushed=True" in done["landing_line"]
    policy = (g1 / "5_tasks" / "policies" / "pol_one_loop_10.md").read_text(encoding="utf-8")
    decision = (g1 / "5_tasks" / "records" / "owner_decisions" / "envelope-pol_one_loop_10.md").read_text(encoding="utf-8")
    dec_fm = decision.split("---")[1]
    _show(f"[件②·信封落盘 frontmatter]\n{policy.split('---')[1].strip()}\n[件②·决策记录 frontmatter(节选)]\n"
          + "\n".join(ln for ln in dec_fm.splitlines() if re.match(r"(decided_by_ref|captured_by|capture_surface|recorded_by|decision_summary|decision_rationale):", ln)))
    assert f"agent_or_role: {ADV1}" in policy and "max_tasks: 30" in policy and "task_selector_task_mode: code" in policy
    assert "capture_surface: board" in dec_fm and "decided_by_ref: owner" in dec_fm and "captured_by: board:owner:owner-fixture:session-" in dec_fm
    assert "来源=看板 Owner 会话" in decision and "登录方式 device_code" in decision and "来源 IP 127.0.0.1" in decision
    msg = _git(home, "log", "-1", "--format=%B")
    _show(f"[件②·落账 git log -1]\n{msg}")
    assert "信封签发落账 pol_one_loop_10" in msg and f"Actor: {OWNER}" in msg
    assert _git(home, "rev-parse", "HEAD") == _git(board["remote"], "rev-parse", "main") != head_before
    assert _git(home, "status", "--porcelain", "-uall") == ""
    log = [json.loads(ln) for ln in board["audit_log"].read_text(encoding="utf-8").splitlines()]
    _show("[件②·看板审计日志]\n" + "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in log))
    assert [r["outcome"] for r in log] == ["previewed", "confirming", "signed"]
    assert all(r["session_ref"].startswith("sha256:") and r["role"] == "owner" and r["project"] == P1 for r in log)
    assert log[-1]["credential_fingerprint"].startswith("sha256:")
    whole = board["audit_log"].read_text(encoding="utf-8") + json.dumps(done) + json.dumps(pre)
    assert FIXTURE_TOKEN not in whole and board["owner_sid"] not in whole, "凭据原文 / 会话 secret 不进应答与日志"
    # 签发后: 旧 loop_9 被新签的 loop_10 接替(单一推导同读)
    from tools.aipos_cli.autonomy_policy import envelope_status

    assert envelope_status(g1, "pol_one_loop_9")["superseded_by"] == "pol_one_loop_10"


def test_item2_owner_new_envelope_for_other_project(board):
    from tools.aipos_cli.clock import utc_now

    body = {"workspace": P2, "mode": "new", "agent_or_role": ADV2, "task_mode": "code", "lane_repo": "", "launch_harness": "",
            "max_tasks": 12, "expires_at": _iso(utc_now() + timedelta(days=5)), "policy_id": ""}
    status, pre = _mint(board, {**body, "stage": "preview"})
    _show(f"[件②·新签预演 status={status}]\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and pre["params"]["policy_id"] == f"pol_{P2}_loop_1", "新签缺省 policy_id = F166 next_policy_id(该驱动方同前缀最大序号 +1)"
    status, done = _mint(board, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show(f"[件②·新签确认 status={status}] signed={done.get('signed')} landing={done.get('landing')}")
    assert status == 200 and done["signed"] and (board["g2"] / "5_tasks" / "policies" / f"{pre['params']['policy_id']}.md").is_file()


def test_item2_non_owner_forbidden_and_workspace_guard(board):
    before = _tree_digest(board["home"])
    lines = []
    status, resp = _mint(board, {**_renew_body(), "stage": "preview"}, who="advisor")
    lines.append(f"非 Owner 会话 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    status, resp = _mint(board, {**_renew_body(), "stage": "confirm", "preview_token": "x"}, who="advisor")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    req = urllib.request.Request(board["base"] + "/api/owner/envelopes/mint", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda *a, **k: None}))
    try:
        opener.open(req, timeout=30)
        raise AssertionError("未登录不应放行")
    except urllib.error.HTTPError as exc:
        lines.append(f"未登录 → {exc.code} Location={exc.headers.get('Location')}")
        assert exc.code == 302 and exc.headers.get("Location") == "/login"
    body = _renew_body()
    body.pop("workspace")
    status, resp = _mint(board, {**body, "stage": "preview"})
    lines.append(f"缺 workspace → {status} {resp['error']} {resp['message']}")
    assert status == 400 and resp["error"] == "WORKSPACE_REQUIRED"
    for bad in ("no-such-project", "../" + P1, str(board["g1"])):
        status, resp = _mint(board, {**_renew_body(workspace=bad), "stage": "preview"})
        lines.append(f"workspace={bad!r} → {status} {resp['error']} {resp['message']}")
        assert status == 400 and resp["error"] == "WORKSPACE_NOT_IN_HOME"
    status, resp = _mint(board, {**_renew_body(template_policy_id="pol_one_loop_3"), "stage": "preview"})
    lines.append(f"续签模板=已接替的旧信封 → {status} {resp['error']} {resp['message']}")
    assert status == 400 and resp["error"] == "TEMPLATE_NOT_CURRENT"
    status, resp = _mint(board, {**_renew_body(), "stage": "preview"}, headers={"Origin": "http://evil.example"})
    lines.append(f"跨站 Origin → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "ORIGIN_MISMATCH"
    status, text = _request(board, "/api/owner/envelopes/mint", "POST", {**_renew_body(), "stage": "preview"}, headers={"Content-Type": "text/plain"})
    lines.append(f"非 JSON → {status} {json.loads(text)['error']}")
    assert status == 400
    _show("[件②·拒绝原文]\n" + "\n".join(lines))
    assert _tree_digest(board["home"]) == before, "被拒请求零写入"


def test_item2_confirm_requires_session_bound_fresh_token(board):
    home = board["home"]
    before = _tree_digest(home)
    body = _renew_body()
    lines = []

    def confirm(token, who="owner", **over):
        payload = {**body, **over, "stage": "confirm"}
        if token is not None:
            payload["preview_token"] = token
        status, resp = _mint(board, payload, who=who)
        return status, resp

    status, resp = confirm(None)
    lines.append(f"无令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_REQUIRED"
    status, resp = confirm("not-a-real-token")
    lines.append(f"错令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    # 他会话的令牌: owner2 预演 → owner 拿去确认
    _s, pre_other = _mint(board, {**body, "stage": "preview"}, who="owner2")
    status, resp = confirm(pre_other["preview_token"])
    lines.append(f"他会话令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_SESSION_MISMATCH"
    # 预演后改参数
    _s, pre = _mint(board, {**body, "stage": "preview"})
    status, resp = confirm(pre["preview_token"], max_tasks=99)
    lines.append(f"预演后改额度 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_PARAMS_MISMATCH"
    status, resp = confirm(pre["preview_token"])
    lines.append(f"同一令牌再用(单次即焚) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    # 过期: 预演后把令牌表时钟拨过 TTL
    _s, pre = _mint(board, {**body, "stage": "preview"})
    store = board["previews"]
    real_now = store._now
    store._now = lambda: real_now() + 301
    try:
        status, resp = confirm(pre["preview_token"])
    finally:
        store._now = real_now
    lines.append(f"过期令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_EXPIRED"
    _show("[件②·确认令牌拒绝原文]\n" + "\n".join(lines))
    assert _tree_digest(home) == before and not (board["g1"] / "5_tasks" / "policies" / "pol_one_loop_10.md").exists()
    outcomes = [json.loads(ln)["outcome"] for ln in board["audit_log"].read_text(encoding="utf-8").splitlines()]
    _show(f"[件②·审计日志 outcome 序列] {outcomes}")
    assert "signed" not in outcomes and outcomes.count("PREVIEW_TOKEN_INVALID") == 2


def test_item2_audit_log_unwritable_refuses_to_sign(board, tmp_path: Path):
    """审计日志写不进 = 拒签(fail-closed): 落点父路径是普通文件。"""
    from web.board.app import make_handler

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    handler = make_handler(repo_root=board["tmp"] / "serve-root", home_root=board["home"], session_store=board["server"].RequestHandlerClass._sessions,
                           preview_store=board["previews"], mint_connection_json=board["conn"], board_audit_log_path=blocker / "audit.jsonl")
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        b2 = {**board, "base": f"http://127.0.0.1:{server.server_address[1]}"}
        before = _tree_digest(board["home"])
        _s, pre = _mint(b2, {**_renew_body(), "stage": "preview"})
        assert pre["ok"] and pre["audit_logged"] is False
        status, resp = _mint(b2, {**_renew_body(), "stage": "confirm", "preview_token": pre["preview_token"]})
        _show(f"[件②·审计日志写不进] {status} {resp['error']} {resp['message']}")
        assert status == 503 and resp["error"] == "AUDIT_LOG_UNAVAILABLE"
        assert _tree_digest(board["home"]) == before
    finally:
        server.shutdown()
        server.server_close()


def test_item2_other_write_routes_still_405(board):
    before = _tree_digest(board["home"])
    lines = []
    for route in STILL_READONLY_POSTS:
        status, text = _request(board, route, "POST", {"workspace": P1, "owner_confirmed": True})
        lines.append(f"POST {route} -> {status} {json.loads(text)['error']}")
        assert status == 405 and json.loads(text)["error"] == "BOARD_READ_ONLY", route
    for method in ("PUT", "PATCH", "DELETE"):
        status, text = _request(board, "/api/owner/envelopes/mint", method, {"stage": "confirm"})
        lines.append(f"{method} /api/owner/envelopes/mint -> {status} {json.loads(text)['error']}")
        assert status == 405
    _show("[件②·其余写路由仍 405(测试客户端)]\n" + "\n".join(lines))
    assert _tree_digest(board["home"]) == before


def test_item1_overview_links_envelope_page():
    static = REPO_ROOT / "web" / "board" / "static"
    overview = (static / "overview.html").read_text(encoding="utf-8")
    i18n = (static / "i18n.js").read_text(encoding="utf-8")
    assert 'href="/owner/envelopes"' in overview and "i18n.t('overview.owner_envelopes')" in overview
    assert i18n.count("'overview.owner_envelopes':") == 2  # zh + en


def test_item2_write_path_declared_and_single_route():
    """写例外只此一条: 路由读声明 write_path; do_POST 里除鉴权通道外只有它; 签发经 envelope_mint 唯一实现(看板源码不另调门 / 不读凭据)。"""
    from tools.aipos_cli.envelope_board import board_envelopes_declaration
    from web.board.app import board_readonly_declaration

    src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    do_post = src[src.index("def do_POST(self)"):src.index("def do_PUT(self)")]
    assert re.findall(r'if path == "(/api/auth/[^"]+)"', do_post) == board_readonly_declaration()["allowed_post_paths"]
    # AIPOS-F176: write exception 2 (project upgrade) is the only other write route, via the same framework handle_owner_write
    assert do_post.count("if path == ") == len(board_readonly_declaration()["allowed_post_paths"]) + 2
    assert 'if path == self._upgrade_paths["write_path"]' in do_post
    assert 'if path == self._envelope_paths["write_path"]' in do_post
    assert board_envelopes_declaration()["write_path"] == "/api/owner/envelopes/mint"
    board_mod = (REPO_ROOT / "tools" / "aipos_cli" / "envelope_board.py").read_text(encoding="utf-8")
    for name in ("load_role_token(", "GateClient(", "call_tool(", "land_written_paths(", "read_text(", "write_text("):
        assert name not in board_mod and name not in src.split("def make_handler(")[1], name
    assert "mint_via_gate(" in board_mod and "mint_preview(" in board_mod and "build_mint_payload(" in board_mod
    cli = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    assert "mint_via_gate(" in cli and "mint_preview(" in cli and "client.call_tool(\"lybra_owner_decision_record" not in cli


# ===========================================================================
# 件③ 收件箱联动
# ===========================================================================

def test_item3_inbox_skips_superseded_and_points_to_board(board):
    rc, text = _cli("owner", "inbox", "--home-root", str(board["home"]), "--json")
    data = json.loads(text)
    env_items = [i for i in data["items"] if i["category"] == "envelope_warning"]
    _show("[件③·收件箱信封项] " + json.dumps([(i["project"], i["policy_id"], i["ask"]) for i in env_items], ensure_ascii=False))
    assert rc == 0 and [i["policy_id"] for i in env_items] == ["pol_one_audit_1"], "被接替的 pol_one_loop_3 不再预警"
    rc, plain = _cli("owner", "inbox", "--home-root", str(board["home"]))
    block = plain[plain.index("信封 pol_one_audit_1"):]
    decide = next(ln for ln in block.splitlines() if ln.strip().startswith("裁决:"))
    _show(f"[件③·收件箱裁决行原文] {decide.strip()}")
    assert f"/owner/envelopes?project={P1}#pol_one_audit_1" in decide and "lybra owner decide --item" in decide
    status, page = _request(board, "/owner/inbox")
    assert status == 200 and f'href="/owner/envelopes?project={P1}#pol_one_audit_1"' in page
    # loop status 预警(F166)同读: 运行所用信封被接替 = 不预警; 当前在用且达阈值 = 照常预警
    from tools.aipos_cli.loop_run_record import envelope_warning_view

    assert envelope_warning_view(board["g1"], {"envelope": "pol_one_loop_3", "driver": ADV1, "task_id": "T-X"}) is None
    warn = envelope_warning_view(board["g1"], {"envelope": "pol_one_audit_1", "driver": AUD1, "task_id": "T-X"})
    _show(f"[件③·loop status 预警(当前在用)] {warn['line']}")
    assert warn and warn["policy_id"] == "pol_one_audit_1"


def test_item3_brief_warnings_skip_superseded(rig):
    from tools.aipos_cli.autonomy_policy import envelope_warnings

    warns = envelope_warnings(rig["g1"])
    _show(f"[件③·brief 预警] {[w['policy_id'] for w in warns]}")
    assert [w["policy_id"] for w in warns] == ["pol_one_audit_1"]
