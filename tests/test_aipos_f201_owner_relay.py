"""AIPOS-F201 — Owner 授权小事看板闭环。

件① needs_owner 卡收件箱「放行继续」(release): Owner 定后由顾问 owner decide 落记录并经既有 amend 出口解除 needs_owner(修订理由带决策记录 ID
    与 Owner 原话), 自动落账; 已认领 / 已阻塞的卡选 release = 声明拒因(零写入)。
件② 授权请求建议覆盖验证 / 派 pi 卡: 仓(卡面 lane.repo)+ 拉起 pi(workspace_config.card_execution_mode)+ 业务卡张数(F192), 看板按建议签发与 CLI 同读。
件③ 每个待决项在看板每个选项下有「告诉 <驱动顾问>」可复制话术; Owner 签发 / 定夺后「已定待转达」按顾问合并成段(CLI 与看板同文), 顾问接着做后消失。
件④ 新签未用信封(used = 0)不报剩余预警(到期临近照报); 用过后剩余 ≤ 3 照报。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; 项目名带连字符, 卡形照 chris HBJOTA-61V / HBLX-20CV 自造, 不读不写真实 chris)+ 靶场 Owner
凭据(假 token, 仓外)+ 进程内门替身(F158 InProcessGate, 门侧真实写入实现)+ 测试进程内线程起的看板(测试客户端 urllib)。全程不碰真实治理根 / 真实门。
设 F201_SNAPSHOT_DIR 时把收件箱页静态 HTML 快照写到该目录(给 Owner 预览; 缺省不写)。
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import threading
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write, init_governance_repo  # noqa: E402  — single source of rig helpers
from test_aipos_f158_envelope_landing_record_type_migration import InProcessGate  # noqa: E402  — single source of the gate double
from test_aipos_f180_grant_request import (  # noqa: E402  — single source of the F180 board client helpers
    _card_html, _card_request, _cli, _free_port, _git, _mint, _request, _show, _tree_digest)

P = "f201-relay"
OWNER = "owner"
ADV_OTA = f"advisor-ota.{P}.mac"      # 61V 形: 卡面 planner_agent / created_by
ADV_LX = f"advisor-lixiang.{P}.mac"   # 20CV 形: loop 驱动方
LYBRA_ADV = f"advisor.{P}.dev"        # 代录的 Lybra 顾问(与驱动顾问不同)
FIXTURE_TOKEN = "fixture-owner-token-f201-do-not-leak"
SEP = "已定待转达"


def _iso(dt) -> str:
    from tools.aipos_cli.clock import iso_z

    return iso_z(dt)


def _card(gov: Path, task_id: str, queue: str, **extra) -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} 夹具", "project": P, "task_mode": "code", "status": queue, "audit": "required",
            "audit_by": f"audit.{P}.dev", "output_target": "docs/", "priority": "high", "needs_owner": False, "context_bundle": "t",
            "artifact_policy": "formal_write"}
    meta.update(extra)
    if isinstance(meta.get("lane"), dict):
        meta["lane"] = json.dumps(meta["lane"])
    path = gov / "5_tasks" / "queue" / queue / f"{task_id.lower()}.md"
    _write(path, _fm(meta, f"# {task_id}\n\n## Goal\n夹具卡。\n"))
    return path


def _policy(gov: Path, pid: str, *, who: str, max_tasks: int, task_mode: str, active_from, expires_at, launch=None, repos=None) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=who, active_from=_iso(active_from), expires_at=_iso(expires_at), max_tasks=max_tasks,
        owner_approval_ref=f"fixture-{pid}", task_selector_task_mode=task_mode, launch_harnesses=list(launch or []),
        task_selector_lane_repo=list(repos or [])))


def _claims(gov: Path, pid: str, n: int) -> None:
    for i in range(n):
        _write(gov / "5_tasks" / "records" / "claims" / f"T-{pid}-{i}" / "claim.md",
               f"---\nrecord_type: claim_record\nautonomy_mode: PreAuthorized\nowner_policy_ref: {pid}\n---\n")


def _ended_run(gov: Path, task_id: str, driver: str, *, reason: str, message: str, envelope: str | None = None) -> str:
    """loop 自身的记录器落一份已结束运行(真实运行记录形)。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    rec = LoopRunRecorder(gov, task_id, driver=driver, contract=load_loop_contract(), warn=_show)
    if envelope:
        rec.set_envelope(envelope)
    rec.end(outcome=reason, exit_code=5, reason=reason, message=message)
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
    for name in ("ios-x", "android-x"):
        repos[name] = tmp_path / "repos" / name
        repos[name].mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repos[name]), check=True)
    pj = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    pj.pop("code_repo", None)
    pj["repos"] = {"default": "ios-x", "items": {k: str(v) for k, v in repos.items()}}
    # 人肉期接入项目同形: 非代码卡(validation / config / docs)缺省派 pi 工位(Owner 10-20 定, F196)
    pj["execution"] = {"default_mode": "subagent", "subagent_executor": f"exec.{P}.mac", "auditor": f"audit.{P}.dev",
                       "pi_executor": f"coder.{P}.dev", "pi_allowed_task_modes": ["validation", "config", "docs"]}
    (gov / "project.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2), encoding="utf-8")
    now = utc_now()
    d = timedelta(days=1)
    # OTA-61V(61V 形): 验证卡发布为 needs_owner 待授权, 顾问自设门槛; 前提满足后须 Owner 同意放行
    _card(gov, "OTA-61V", "pending", title="固定双App候选真实隔离后台联合验证", task_mode="validation", harness="pi",
          assigned_to=f"coder.{P}.dev", agent_instance=f"coder.{P}.dev", created_by=ADV_OTA, planner_agent=ADV_OTA, needs_owner=True,
          lane={"repo": "ios-x", "paths": ["5_tasks/records/returns/OTA-61V/artifacts/"], "roles": ["executor"]})
    # OTA-62C: 已认领在途卡仍带 needs_owner(选 release = 拒并给原因)
    _card(gov, "OTA-62C", "claimed", task_mode="code", assigned_to=f"exec.{P}.mac", created_by=ADV_OTA, needs_owner=True,
          claimed_at="2026-10-20T00:00:00Z", claim_id="claim_OTA-62C_fixture")
    # LX-20CV(20CV 形): 验证卡, 未写 harness / assigned_to(按 execution 派 pi), 驱动顾问只有 code 信封 → no_envelope
    _card(gov, "LX-20CV", "pending", title="理想Android固定候选App构建与55项界面验证", task_mode="validation", created_by=ADV_LX,
          planner_agent=ADV_LX, lane={"repo": "android-x", "paths": ["5_tasks/records/returns/LX-20CV/artifacts/"], "roles": ["executor"]})
    _policy(gov, "pol_f201_lx_loop_3", who=ADV_LX, max_tasks=40, task_mode="code", active_from=now - 3 * d, expires_at=now + 6 * d)
    _claims(gov, "pol_f201_lx_loop_3", 5)
    _ended_run(gov, "LX-20CV", ADV_LX, reason="no_envelope", message="无有效 autonomy 信封, 拒绝裸跑。")
    # LX-21B: 同一驱动顾问另一张卡, loop 停在门拒等 Owner(裁决后与 LX-20CV 的签发合成一段转达)
    _card(gov, "LX-21B", "claimed", task_mode="code", assigned_to=f"exec.{P}.mac", created_by=ADV_LX)
    _ended_run(gov, "LX-21B", ADV_LX, reason="gate_rejected", message="GATE_DESIGN_FORK: 方案 A / B 二选一", envelope="pol_f201_lx_loop_3")
    remote = init_governance_repo(hroot)
    conn = tmp_path / "owner-vault" / "connection.json"
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                             "tokens": [{"role": "owner", "token": FIXTURE_TOKEN, "scopes": ["owner_confirm"], "token_ref": "owner-fixture"}]}))
    return {"home": hroot, "gov": gov, "remote": remote, "conn": conn, "tmp": tmp_path, "now": now}


@pytest.fixture
def board(rig, monkeypatch: pytest.MonkeyPatch):
    import tools.aipos_cli.confirm_client as confirm_client
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler
    from web.board.auth_otc import PreviewTokenStore

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    store = SessionStore()
    owner_sid = store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="device_code")
    advisor_sid = store.create(role="advisor", scopes=["queue_publish"], token_ref="advisor-fixture", login_method="token")
    serve_root = rig["tmp"] / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=serve_root, home_root=rig["home"], session_store=store, preview_store=PreviewTokenStore(ttl_seconds=300),
        mint_connection_json=rig["conn"], board_audit_log_path=rig["tmp"] / "board-runtime" / "board-audit-log.jsonl"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**rig, "base": f"http://127.0.0.1:{server.server_address[1]}", "owner": f"{SESSION_COOKIE_NAME}={owner_sid}",
               "advisor": f"{SESSION_COOKIE_NAME}={advisor_sid}"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _inbox(rig, *extra: str) -> tuple[int, str]:
    return _cli("owner", "inbox", "--home-root", str(rig["home"]), *extra)


def _inbox_json(rig) -> dict:
    rc, out = _inbox(rig, "--json")
    assert rc in (0, 4), out
    return json.loads(out)


def _item(data: dict, task_id: str, category: str) -> dict | None:
    return next((i for i in data["items"] if i.get("task_id") == task_id and i["category"] == category), None)


def _relay_block(text: str) -> str:
    return text.partition(SEP)[2]


def _board_relays(page: str) -> dict[str, str]:
    return {html.unescape(m.group(1)): html.unescape(m.group(2)) for m in re.finditer(
        r'<article class="ib-relay" data-advisor="([^"]*)"[^>]*>.*?<pre class="upgrade-lines ib-relay-text">(.*?)</pre></article>', page, re.S)}


def _cli_relays(text: str) -> dict[str, str]:
    """CLI 段: 每段首行「告诉 X:」, 段内行前缀两格(去前缀后与看板 <pre> 逐字比较)。"""
    out: dict[str, str] = {}
    for para in _relay_block(text).split("\n\n")[1:]:
        lines = [ln[2:] for ln in para.splitlines() if ln.startswith("  ")]
        if lines and lines[0].startswith("告诉 "):
            out[lines[0][len("告诉 "):].rstrip(":")] = "\n".join(lines)
    return out


def _snapshot(name: str, page: str) -> None:
    out = os.environ.get("F201_SNAPSHOT_DIR")
    if not out:
        return
    css = (REPO_ROOT / "web" / "board" / "static" / "readonly-pages.css").read_text(encoding="utf-8")
    doc = page.replace('<link rel="stylesheet" href="/readonly-pages.css">', f"<style>{css}</style>")
    doc = re.sub(r'<script src="[^"]+"></script>', "", doc)
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / name).write_text(doc, encoding="utf-8")


# ===========================================================================
# 件① 放行继续
# ===========================================================================

def test_item1_release_option_choice_message_and_decide_lands(board):
    home, gov = board["home"], board["gov"]
    data = _inbox_json(board)
    item = _item(data, "OTA-61V", "needs_owner_card")
    assert item, data
    opts = [(o["key"], o["label"]) for o in item["options"]]
    _show(f"[件①·61V 形收件箱选项] {opts} 转达对象={item['relay_to']}")
    assert opts[0] == ("release", "放行继续(解除 needs_owner, 按现状推进)") and item["relay_to"] == ADV_OTA
    rc, text = _inbox(board)
    block = text[text.index(item["item_id"]):].split("\n\n")[0]
    _show(f"---- 件①·CLI 收件箱该项原文 ----\n{block}")
    assert f"  转达对象: {ADV_OTA}" in block and "release=放行继续" in block
    # 看板: 该项每个选项一段话术(只读, 无按钮无表单); release =「同意放行」给卡的驱动顾问
    status, page = _request(board, "/owner/inbox")
    section = re.search(r'<section class="owner-inbox-item[^>]*data-item-id="' + item["item_id"] + r'".*?</section>', page, re.S).group(0)
    choices = re.findall(r'<details class="rp-cmd ib-choice" data-choice="(\w+)"><summary class="rp-cmd-label">(.*?)</summary>'
                         r'<pre class="upgrade-lines ib-choice-text">(.*?)</pre></details>', section, re.S)
    assert status == 200 and [c[0] for c in choices] == ["release", "withdraw", "freeze_legacy", "acknowledge"]
    assert "<form" not in section and "<button" not in section
    summary, script = html.unescape(choices[0][1]), html.unescape(choices[0][2])
    _show(f"[件①·看板「同意放行」话术原文] {summary}\n{script}")
    assert summary == f"选「放行继续(解除 needs_owner, 按现状推进)」→ 给 {ADV_OTA} 的话术"
    assert script.startswith(f"告诉 {ADV_OTA}:\nOwner 在看板对 {P} 卡 OTA-61V [pending] 选了「放行继续(解除 needs_owner, 按现状推进)」。")
    preview = re.search(r"`(lybra owner decide [^`]+)`", script).group(1)
    assert preview == (f"lybra owner decide --item {item['item_id']} --choice release --relayed-by {ADV_OTA} "
                       f"--owner-words '看板选「放行继续(解除 needs_owner, 按现状推进)」' --home-root {home}")
    assert "核对预演无误后同一命令末尾加 --confirm" in script
    assert f"`lybra loop --task-id OTA-61V --workspace-root {gov} --actor {ADV_OTA}`" in script
    _snapshot("01-inbox-choice-messages.html", page)
    # 顾问按话术: 预演(零写入)→ 确认(决策记录 + amend 解除 + 自动落账)
    before = _tree_digest(home)
    words = "前提满足, 同意放行 61V"
    argv = ("owner", "decide", "--item", item["item_id"], "--choice", "release", "--relayed-by", ADV_OTA, "--owner-words", words,
            "--home-root", str(home))
    rc, out = _cli(*argv)
    _show(f"---- 件①·decide release 预演原文 exit {rc} ----\n{out}")
    assert rc == 0 and "解除动作 release_needs_owner(既有队列出口): ok=True" in out and _tree_digest(home) == before
    rc, out = _cli(*argv, "--confirm")
    _show(f"---- 件①·decide release --confirm 原文 exit {rc} ----\n{out}")
    assert rc == 0 and "落账: verdict=PASS" in out
    card = (gov / "5_tasks" / "queue" / "pending" / "ota-61v.md").read_text(encoding="utf-8")
    amend = sorted((gov / "5_tasks" / "records" / "amendments" / "OTA-61V").glob("*.md"))
    amend_text = amend[0].read_text(encoding="utf-8")
    reason = re.search(r"^reason: (.*)$", amend_text, re.M).group(1)
    decision = (gov / "5_tasks" / "records" / "owner_decisions" / f"{item['decision_id']}.md").read_text(encoding="utf-8")
    _show(f"[件①·卡面 needs_owner] {re.search(r'^needs_owner: .*$', card, re.M).group(0)}\n[件①·修订理由原文] {reason}\n"
          f"[件①·决策记录 frontmatter 节选] " + " | ".join(ln for ln in decision.splitlines()
                                                      if ln.startswith(("decision_id:", "allowed_next_action:", "captured_by:"))))
    assert "needs_owner: false" in card and len(amend) == 1
    assert item["decision_id"] in reason and f"Owner 原话: {words}" in reason
    assert "allowed_next_action: release" in decision and f"captured_by: {ADV_OTA}" in decision
    assert _git(home, "status", "--porcelain", "-uall") == "" and _git(home, "rev-parse", "HEAD") == _git(board["remote"], "rev-parse", "main")
    # 件③(b): 已放行而驱动顾问还没推进 = 已定待转达(CLI 与看板同文), 点名卡的驱动顾问
    rc, text = _inbox(board)
    after = _inbox_json(board)
    assert _item(after, "OTA-61V", "needs_owner_card") is None
    cli = _cli_relays(text)
    status, page = _request(board, "/owner/inbox")
    shown = _board_relays(page)
    _show(f"---- 件③·放行后「已定待转达」CLI 段原文 ----\n{cli.get(ADV_OTA)}")
    assert shown[ADV_OTA] == cli[ADV_OTA], "看板与 CLI 同文"
    assert (f"1. {P} 卡 OTA-61V [pending]: Owner 已定「放行继续(解除 needs_owner, 按现状推进)」(决策记录 {item['decision_id']}; "
            f"Owner 原话「{words}」)。下一步: 卡已由裁决放行") in cli[ADV_OTA]
    assert f"`lybra loop --task-id OTA-61V --workspace-root {gov} --actor {ADV_OTA}`" in cli[ADV_OTA]
    _snapshot("02-inbox-released-relay.html", page)
    # 顾问接着做(本卡起了新 loop 运行)= 自动消失
    _ended_run(gov, "OTA-61V", ADV_OTA, reason="no_envelope", message="无有效 autonomy 信封")
    rc, text = _inbox(board)
    assert ADV_OTA not in _cli_relays(text), _relay_block(text)


def test_item1_release_refused_for_claimed_card(rig):
    item = _item(_inbox_json(rig), "OTA-62C", "needs_owner_card")
    assert [o["key"] for o in item["options"]] == ["withdraw", "freeze_legacy", "acknowledge"]
    before = _tree_digest(rig["home"])
    rc, out = _cli("owner", "decide", "--item", item["item_id"], "--choice", "release", "--relayed-by", ADV_OTA,
                   "--owner-words", "同意放行", "--home-root", str(rig["home"]), "--confirm")
    _show(f"[件①·已认领卡 release 拒原文 exit {rc}] {out.strip()}")
    assert rc == 2 and _tree_digest(rig["home"]) == before
    assert out.strip() == ("lybra owner decide: 卡 OTA-62C 已认领(claimed), 不能放行: 放行只解除未认领(pending)卡的 needs_owner 拦截, "
                           "在途卡由驱动方按卡面推进(要换目标用撤换 lybra queue supersede); 本事项可选: withdraw, freeze_legacy, acknowledge")


# ===========================================================================
# 件② 验证卡授权建议: 仓 + pi + 1 张
# ===========================================================================

def test_item2_validation_grant_suggests_repo_pi_one_card_and_board_signs(board):
    gov = board["gov"]
    data = _inbox_json(board)
    item = _item(data, "LX-20CV", "loop_owner_needed")
    g = item["grant"]
    _show("[件②·20CV 形授权建议] " + json.dumps({k: g[k] for k in ("agent_or_role", "task_mode", "lane_repos", "launch_harnesses", "max_tasks",
                                                                 "policy_id", "sources", "quota_basis")}, ensure_ascii=False, sort_keys=True))
    assert (g["agent_or_role"], g["task_mode"], g["lane_repos"], g["launch_harnesses"], g["max_tasks"]) == (
        ADV_LX, "validation", ["android-x"], ["pi"], 1)
    assert g["sources"]["launch_harnesses"] == "card_execution" and g["sources"]["lane_repos"] == "card"
    assert g["sources"]["max_tasks"] == "card_need" and g["previous_policy_id"] == "pol_f201_lx_loop_3"
    assert g["policy_id"] == "pol_f201_lx_loop_4"
    rc, text = _inbox(board)
    line = next(ln.strip() for ln in text.splitlines() if "授权请求:" in ln and "LX-20CV" in ln)
    _show(f"[件②·CLI 人话行原文] {line}")
    assert line == (f"授权请求: {ADV_LX} 请求为 LX-20CV(理想Android固定候选App构建与55项界面验证)开一个做固定候选验证的授权, 仅 android-x, "
                    f"允许 loop 拉起 pi 工位, 1 张业务卡(每张含其审计与返工), 到 {g['expires_at'][:10]}。理由: 理想Android固定候选App构建与55项界面验证")
    status, page = _request(board, "/owner/inbox")
    card = _card_html(page, item["item_id"])
    sentence = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", re.search(r'<p class="gr-sentence">.*?</p>', card).group(0))).split())
    _show(f"[件②·看板请求卡人话] {sentence}")
    assert ",仅 android-x,允许 loop 拉起 pi 工位,1 张业务卡" in sentence
    req = _card_request(card)
    assert req["payload"]["launch_harness"] == ["pi"] and req["payload"]["lane_repo"] == ["android-x"] and req["payload"]["max_tasks"] == 1
    # 签发成功面板的转达段(policy id 由页脚本按实际签发 id 填)
    assert req["relay"].startswith(f"告诉 {ADV_LX}:\n") and "Owner 已签发信封 {policy_id}" in req["relay"] and "允许 loop 拉起 pi 工位" in req["relay"]
    assert req["relay_note"] == f"{ADV_LX} 还不知道你已签发: 把下面这段复制给它, 它会接着推进 LX-20CV。"
    status, pre = _mint(board, {**req["payload"], "stage": "preview"})
    assert status == 200 and pre["ok"], pre
    status, done = _mint(board, {**req["payload"], "stage": "confirm", "preview_token": pre["preview_token"]})
    assert status == 200 and done["ok"] and done["signed"][0]["policy_id"] == "pol_f201_lx_loop_4", done
    policy = (gov / "5_tasks" / "policies" / "pol_f201_lx_loop_4.md").read_text(encoding="utf-8").split("---")[1].strip()
    _show(f"[件②·按建议签发落盘信封 frontmatter]\n{policy}")
    for want in (f"agent_or_role: {ADV_LX}", "task_selector_task_mode: validation", "max_tasks: 1", "launch_harnesses:\n- pi",
                 "task_selector_lane_repo:\n- android-x"):
        assert want in policy, want
    # 件③(c): 签发生效 → 请求项消失, 转入「已定待转达」(驱动顾问未续跑); 件④: 新签 used=0 不报剩余预警
    after = _inbox_json(board)
    assert _item(after, "LX-20CV", "loop_owner_needed") is None
    assert not [i for i in after["items"] if i["category"] == "envelope_warning" and i.get("policy_id") == "pol_f201_lx_loop_4"]
    rc, text = _inbox(board)
    cli = _cli_relays(text)
    _show(f"---- 件③·签发后「已定待转达」CLI 段原文 ----\n{cli.get(ADV_LX)}")
    assert (f"1. {P} 卡 LX-20CV: Owner 已签发信封 pol_f201_lx_loop_4(覆盖 {ADV_LX}; 允许 loop 拉起 pi 工位)。下一步: "
            f"`lybra loop --task-id LX-20CV --workspace-root {gov} --actor {ADV_LX} --envelope pol_f201_lx_loop_4`") in cli[ADV_LX]
    status, page = _request(board, "/owner/inbox")
    assert _board_relays(page)[ADV_LX] == cli[ADV_LX] and req["relay"].replace("{policy_id}", "pol_f201_lx_loop_4") == cli[ADV_LX]
    _snapshot("03-inbox-after-signing-relay.html", page)


def test_item2_routing_reads_card_execution_mode(rig):
    from tools.aipos_cli.owner_inbox import suggest_grant

    base = {"task_id": "X-1", "title": "x", "lane": {"repo": "android-x"}}
    got = {mode: suggest_grant(rig["gov"], P, task_id="X-1", card_fm={**base, "task_mode": mode}, driver=ADV_LX)["launch_harnesses"]
           for mode in ("validation", "config", "docs", "code")}
    pinned = suggest_grant(rig["gov"], P, task_id="X-1", card_fm={**base, "task_mode": "code", "harness": "pi"}, driver=ADV_LX)
    _show(f"[件②·按卡类型的拉起建议] {got} / code+harness=pi → {pinned['launch_harnesses']}({pinned['sources']['launch_harnesses']})")
    # AIPOS-F211 件③(F208 P1-5): 代码卡执行阶段 = 子 agent, 但其审计阶段(独立审计, 审计卡恒 pi)要 loop 拉 pi → 建议含 pi(原 [] 致审计停手工)
    assert got == {"validation": ["pi"], "config": ["pi"], "docs": ["pi"], "code": ["pi"]}
    assert pinned["launch_harnesses"] == ["pi"] and pinned["sources"]["launch_harnesses"] == "card_execution"


# ===========================================================================
# 件③ 定夺后话术: 同一顾问合并一段; 顾问续跑后消失
# ===========================================================================

def test_item3_decided_items_merge_per_advisor_and_disappear(board):
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown
    from tools.aipos_cli.clock import utc_now

    home, gov = board["home"], board["gov"]
    data = _inbox_json(board)
    fork = _item(data, "LX-21B", "loop_owner_needed")
    assert fork["relay_to"] == ADV_LX and [o["key"] for o in fork["options"]] == ["continue", "rework", "pause"]
    # Lybra 顾问代录 Owner 定夺(转达人 ≠ 驱动顾问)
    rc, out = _cli("owner", "decide", "--item", fork["item_id"], "--choice", "continue", "--relayed-by", LYBRA_ADV, "--owner-words",
                   "按方案 A 继续", "--home-root", str(home), "--confirm")
    assert rc == 0, out
    # 同一驱动顾问另一件: 信封签发生效(Owner 已签, 驱动方覆盖本卡)
    now = utc_now()
    _write(gov / "5_tasks" / "policies" / "pol_f201_lx_loop_4.md", build_autonomy_policy_markdown(
        policy_id="pol_f201_lx_loop_4", agent_or_role=ADV_LX, active_from=_iso(now - timedelta(minutes=5)),
        expires_at=_iso(now + timedelta(days=7)), max_tasks=1, owner_approval_ref="fixture", task_selector_task_mode="validation",
        launch_harnesses=["pi"], task_selector_lane_repo=["android-x"]))
    rc, text = _inbox(board)
    data = _inbox_json(board)
    group = next(g for g in data["relays"] if g["advisor"] == ADV_LX)
    _show(f"---- 件③·同一顾问合并一段(CLI 原文) ----\n" + "\n".join(group["lines"]))
    assert [e["kind"] for e in group["entries"]] == ["envelope", "decision"] and group["lines"][1].startswith("Owner 已处理下面 2 件事")
    assert group["lines"][3] == (f"2. {P} 卡 LX-21B: Owner 已定「按顾问建议继续推进」(决策记录 {fork['decision_id']}; Owner 原话「按方案 A 继续」)。"
                                 f"下一步: 按 Owner 裁决继续: lybra loop --task-id LX-21B --workspace-root {gov} --actor {ADV_LX} "
                                 f"--envelope pol_f201_lx_loop_3")
    assert group["lines"][-1].startswith("注意: 只用产品命令推进") and data["relay_count"] == len(data["relays"])
    assert _cli_relays(text)[ADV_LX] == "\n".join(group["lines"])
    status, page = _request(board, "/owner/inbox?lang=en")
    en = _board_relays(page)[ADV_LX]
    _show(f"---- 件③·看板英文同段 ----\n{en}")
    assert en.startswith(f"Tell {ADV_LX}:\nThe Owner has handled the 2 thing(s) below") and fork["decision_id"] in en
    # 驱动顾问续跑两张卡(新 loop 运行)= 这一段消失
    for task_id in ("LX-20CV", "LX-21B"):
        _ended_run(gov, task_id, ADV_LX, reason="completed", message="夹具: 续跑")
    assert ADV_LX not in [g["advisor"] for g in _inbox_json(board)["relays"]]


def test_item3_skip_choices_and_window(rig, monkeypatch: pytest.MonkeyPatch):
    import tools.aipos_cli.clock as clock

    item = _item(_inbox_json(rig), "OTA-62C", "needs_owner_card")
    rc, out = _cli("owner", "decide", "--item", item["item_id"], "--choice", "acknowledge", "--relayed-by", ADV_OTA, "--owner-words",
                   "知道了", "--home-root", str(rig["home"]), "--confirm")
    assert rc == 0, out
    data = _inbox_json(rig)
    assert not any(e.get("task_id") == "OTA-62C" for g in data["relays"] for e in g["entries"]), "知悉 = 不需顾问动作, 不进转达"
    fork = _item(data, "LX-21B", "loop_owner_needed")
    rc, out = _cli("owner", "decide", "--item", fork["item_id"], "--choice", "pause", "--relayed-by", ADV_LX, "--owner-words", "先停",
                   "--home-root", str(rig["home"]), "--confirm")
    assert rc == 0, out
    assert any(e.get("task_id") == "LX-21B" for g in _inbox_json(rig)["relays"] for e in g["entries"])
    later = rig["now"] + timedelta(days=8)
    monkeypatch.setattr(clock, "utc_now", lambda: later)
    assert not any(e.get("task_id") == "LX-21B" for g in _inbox_json(rig)["relays"] for e in g["entries"]), "窗口(7 天)外不再转达"


# ===========================================================================
# 件④ 新签未用信封不报剩余预警
# ===========================================================================

def test_item4_fresh_unused_envelope_no_remaining_warning(rig):
    from tools.aipos_cli.autonomy_policy import envelope_overview, envelope_renew_declaration, envelope_warnings

    gov, now = rig["gov"], rig["now"]
    d = timedelta(days=1)
    _policy(gov, "pol_f201_fresh_1", who=ADV_OTA, max_tasks=1, task_mode="validation", active_from=now - timedelta(hours=1),
            expires_at=now + 9 * d, launch=["pi"])
    _policy(gov, "pol_f201_used_1", who=f"advisor-b.{P}.mac", max_tasks=4, task_mode="code", active_from=now - d, expires_at=now + 9 * d)
    _claims(gov, "pol_f201_used_1", 1)
    _policy(gov, "pol_f201_soon_1", who=f"advisor-c.{P}.mac", max_tasks=1, task_mode="code", active_from=now - d,
            expires_at=now + timedelta(hours=5))
    rows = {r["policy_id"]: r for r in envelope_overview(gov)["envelopes"]}
    shown = {pid: (rows[pid]["status"], rows[pid]["used"], rows[pid]["max_tasks"], rows[pid]["why"])
             for pid in ("pol_f201_fresh_1", "pol_f201_used_1", "pol_f201_soon_1")}
    _show(f"[件④·信封状态] {json.dumps(shown, ensure_ascii=False)}\n[件④·口径声明] {envelope_renew_declaration()['warn']['unused_exempt_note']}")
    assert shown["pol_f201_fresh_1"] == ("active", 0, 1, None), "新签 used=0: 不报剩余不足"
    assert shown["pol_f201_used_1"][:3] == ("warn", 1, 4) and shown["pol_f201_used_1"][3].startswith("剩余 3/4 张业务卡(阈值: ≤3 张或 ≤10%)")
    assert shown["pol_f201_soon_1"][0] == "warn" and "到期" in shown["pol_f201_soon_1"][3] and "剩余" not in shown["pol_f201_soon_1"][3]
    warns = [w["policy_id"] for w in envelope_warnings(gov)]
    items = [i["policy_id"] for i in _inbox_json(rig)["items"] if i["category"] == "envelope_warning"]
    _show(f"[件④·brief 预警] {warns} / [收件箱信封将尽] {items}")
    assert warns == ["pol_f201_soon_1", "pol_f201_used_1"] and sorted(items) == warns


# ===========================================================================
# 声明单源 / 中英 / CJK 守卫 / 样式令牌 / 看板只读
# ===========================================================================

def test_declaration_bilingual_guards_and_styles():
    import importlib.util

    from tools.aipos_cli.owner_inbox import inbox_declaration, relay_declaration

    decl = inbox_declaration()
    r = relay_declaration(decl)
    _show(f"[声明] relay 键={sorted(r)} 语言={sorted(r['texts'])} skip={r['skip_choices']} driver_fields={r['driver_fields']}")
    assert set(r["texts"]["zh"]) == set(r["texts"]["en"]) and r["window_days"] == 7
    assert decl["options"]["release"]["label"] == "放行继续(解除 needs_owner, 按现状推进)"
    assert set(decl["categories"]["needs_owner_card"]["choice_refused_by_queue_state"]["release"]) == {"claimed", "blocked"}
    spec = importlib.util.spec_from_file_location("f201_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    hits = {name: guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / name) for name in ("readonly_pages.py", "static/envelopes.js")}
    _show(f"[CJK 守卫逐行判定] {hits}")
    assert hits == {"readonly_pages.py": [], "static/envelopes.js": []}
    src = (REPO_ROOT / "tools" / "aipos_cli" / "owner_inbox.py").read_text(encoding="utf-8")
    relay_src = src[src.index("# AIPOS-F201 件①③"):src.index("def _collect_inbox(")]
    code = re.sub(r'""".*?"""', "", relay_src, flags=re.S)
    literal = [ln.strip() for ln in code.splitlines() if not ln.lstrip().startswith("#") and re.search(r"[一-鿿]", ln.split("  #")[0])
               and "SchemaLoadError(" not in ln and "errors.append(" not in ln]
    _show(f"[话术单源] owner_inbox 话术段除声明坏拒因 / 读不出点名外的中文字面量: {literal or '无'}")
    assert not literal
    css = (REPO_ROOT / "web" / "board" / "static" / "readonly-pages.css").read_text(encoding="utf-8")
    block = css.split("/* AIPOS-F201:", 1)[1].split("/* upgrade page */", 1)[0]
    assert not re.findall(r"#[0-9A-Fa-f]{3,6}\b|rgb|radius|shadow|gradient", block), block


def test_board_inbox_stays_read_only_for_relays(board):
    from tools.aipos_cli.board_pages import pages_declaration

    status, page = _request(board, "/owner/inbox", who="advisor")
    assert status == 200 and "ib-choice" in page and "envelopes.js" not in page and "<form" not in page
    for path in ("/api/owner/inbox", "/owner/inbox"):
        status, _text = _request(board, path, "POST", {"choice": "release"})
        _show(f"[看板只读] POST {path} → {status}")
        assert status in (403, 404, 405), path
    assert pages_declaration()["nav_links"]["inbox"] == "/owner/inbox"
