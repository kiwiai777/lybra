"""AIPOS-F226 — 看板收件箱一键裁决(看板写例外③): Owner 在「待你决定」页对每个选项点按钮(预演 → 确认)即落 Owner 本人裁决;
顾问下次 loop / next / loop status 直接读到裁决续推进, 话术降为折叠兜底。

件① 写路由 = verbs.lybra_owner_inbox.board.decide.write_path, 安全判定全部经 F172 同一框架 envelope_board.handle_owner_write
    (Owner 会话 / Origin / workspace / 本会话一次性预演令牌 / 看板审计日志), 裁决本身 = owner_inbox.decide 同一实现(预演 / --confirm);
    记录 captured_by / 会话指纹 / 登录方式 / 来源 IP 同 F172 信封签发口径, relayed_by = owner, owner_words = Owner 一句或选项标签。
件② 顾问侧零转达: reaudit / extend_rework / release 各裁决落定后, 顾问 `lybra loop status` / `lybra next` 读到裁决与接续命令;
    看板该事项移入「已裁决待接续」(F201 relays 同一推导), 顾问接续后自动消失。

靶场 = 临时 HOME + 临时治理根(git 仓 + 临时远端裸仓)+ 进程内门替身(F90 靶场)+ 测试进程内线程起的看板(测试 Owner 会话, 测试端口);
人肉期样本全部自造(F202 连续 BLOCK / F222 返工上限 / F168 needs_owner 构件, 禁第二份)。全程不碰真实治理根 / 生产看板 / 真实凭据 / 真门。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
import test_aipos_f168_owner_inbox as f168  # noqa: E402  — 轻靶场构件(禁第二份)
import test_aipos_f202_owner_reaudit as f202  # noqa: E402  — 连续 BLOCK 人肉期样本(禁第二份)
import test_aipos_f222_rework_limit_arbitration as f222  # noqa: E402  — 返工上限样本(禁第二份)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f179_long_run_resources_block import _block_watch, _quiet, _status  # noqa: E402

TASK = f90.TASK
RW = "PROBE-F226-RW"     # 返工上限(审计裁决型卡)
NO = "PROBE-F226-NO"     # needs_owner 待放行
WORDS = "演示要用, 环境已修好, 重新派审"
DECIDE_PATH = "/api/owner/inbox/decide"


def _show(text: str) -> None:
    print(text, flush=True)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *argv], cwd=str(repo), check=True, capture_output=True,
                          text=True).stdout.strip()


def _digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Board:
    """测试进程内线程起的看板(测试端口, 测试会话表 / 预演令牌表 / 审计日志均为夹具注入)。请求不跟随重定向(未登录 = 302 原样)。"""

    def __init__(self, home: Path, tmp: Path) -> None:
        from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler
        from web.board.auth_otc import PreviewTokenStore

        self.store = SessionStore()
        self.owner_sid = self.store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="device_code")
        self.owner2_sid = self.store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="otc")
        self.advisor_sid = self.store.create(role="advisor", scopes=["queue_publish"], token_ref="advisor-fixture", login_method="token")
        self.cookie_name = SESSION_COOKIE_NAME
        self.previews = PreviewTokenStore(ttl_seconds=300)
        serve_root = tmp / "serve-root"
        (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True, exist_ok=True)
        self.audit_log = tmp / "board-runtime" / "board-audit-log.jsonl"
        self.server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
            repo_root=serve_root, home_root=home, session_store=self.store, preview_store=self.previews,
            board_audit_log_path=self.audit_log))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def sid(self, who: str | None) -> str | None:
        return {"owner": self.owner_sid, "owner2": self.owner2_sid, "advisor": self.advisor_sid}.get(who or "", who)

    def request(self, path: str, method: str = "GET", body: dict | None = None, who: str | None = "owner") -> tuple[int, str, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        headers = {"Content-Type": "application/json"}
        sid = self.sid(who)
        if sid:
            headers["Cookie"] = f"{self.cookie_name}={sid}"
        conn.request(method, path, body=json.dumps(body or {}).encode("utf-8") if method != "GET" else None, headers=headers)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8")
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, text, hdrs

    def decide(self, body: dict, who: str | None = "owner") -> tuple[int, dict]:
        status, text, hdrs = self.request(DECIDE_PATH, "POST", body, who=who)
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, {"_raw": text[:200], "_location": hdrs.get("location")}


def _audit_lines(board: Board) -> list[dict]:
    if not board.audit_log.is_file():
        return []
    return [json.loads(ln) for ln in board.audit_log.read_text(encoding="utf-8").splitlines()]


def _item(home: Path, task_id: str, category: str) -> dict:
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    items = [i for i in json.loads(out)["items"] if i.get("task_id") == task_id and i["category"] == category]
    assert len(items) == 1, (task_id, category, out[-2000:], err)
    return items[0]


def _relay_entries(home: Path) -> list[dict]:
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    return [e for g in json.loads(out).get("relays") or [] for e in g.get("entries") or []]


def _decision_text(gov: Path, decision_id: str) -> str:
    return (gov / "5_tasks" / "records" / "owner_decisions" / f"{decision_id}.md").read_text(encoding="utf-8")


def _fm_lines(text: str, keys: tuple[str, ...]) -> list[str]:
    fm = text.split("---")[1]
    return [ln for ln in fm.splitlines() if ln.split(":", 1)[0] in keys]


def _section(page: str, item_id: str) -> str:
    m = re.search(r'<section class="owner-inbox-item[^>]*data-item-id="' + re.escape(item_id) + r'".*?</section>', page, re.S)
    assert m, item_id
    return m.group(0)


def _rule(board: Board, item: dict, choice: str, words: str | None = None) -> tuple[dict, dict]:
    """看板点选项 → 预演 → 确认(两跳均经同一写路由)。"""
    body = {"workspace": item["project"], "item_id": item["item_id"], "choice": choice}
    if words is not None:
        body["owner_words"] = words
    status, pre = board.decide({**body, "stage": "preview"})
    _show(f"---- 看板预演 {item['task_id']} 选 {choice} → HTTP {status} ----\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and pre["ok"], pre
    status, done = board.decide({**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show(f"---- 看板确认 {item['task_id']} 选 {choice} → HTTP {status} ----\n" + json.dumps(
        {k: done.get(k) for k in ("ok", "decision_id", "choice", "advisor", "actor", "captured_by", "capture_surface", "landing_line",
                                  "redirect", "error", "message")}, ensure_ascii=False, indent=1))
    assert status == 200 and done["ok"], done
    return pre, done


# ===========================================================================
# 靶场: 三类人肉期样本(连续 BLOCK → reaudit / 返工上限 → extend_rework / needs_owner → release)
# ===========================================================================

@pytest.fixture
def three(rig, tmp_path, monkeypatch):  # noqa: F811
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": DRIVER,
               "focus_items": ["x"]} for n in (1, 2)]
    f78._card(rig.gov, RW, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'",
                                            "rework_rounds": rounds})
    f78._card(rig.gov, NO, "pending", extra={"needs_owner": True})
    v3 = f222._verdict(rig.gov, RW, 3, "2026-10-09T00:00:00Z")
    # AIPOS-F228: F225 分级口径——连续 BLOCK(audit_blocked_repeated)= 第二档, 部署 / 验证根判不出变化 = 归顾问(advisor_fix), 不进收件箱;
    # 顾问修不动且确属第一档(改验收 / 换审计环境 = rescope)才带事由升 Owner。本夹具验 Owner「重新派审」手动兜底, 故复用 F202 同一构件:
    # 自动判定钉为「判不出变化」+ 顾问带第一档事由升级(禁第二份)。
    f202._no_auto_reaudit(monkeypatch)
    f202._two_blocks(rig)  # 起治理仓(含上面两张卡与裁决记录)+ 连续两次 BLOCK(第二档 → 归顾问)
    f202._escalate(rig)  # 顾问 loop note --owner-reason rescope = 第一档 loop_owner_needed(reaudit 选项恒列)
    board = Board(rig.gov.parent, tmp_path)
    try:
        yield {"rig": rig, "home": rig.gov.parent, "gov": rig.gov, "board": board, "v3": v3}
    finally:
        board.close()


def test_item1_item2_three_kinds_one_click_and_advisor_reads(three):
    rig, home, gov, board = three["rig"], three["home"], three["gov"], three["board"]
    reaudit = _item(home, TASK, "loop_owner_needed")
    rework = _item(home, RW, "rework_limit")
    release = _item(home, NO, "needs_owner_card")
    _show(f"[靶场三类事项] reaudit={reaudit['item_id']} {[o['key'] for o in reaudit['options']]} / extend_rework={rework['item_id']} "
          f"{[o['key'] for o in rework['options']]} / release={release['item_id']} {[o['key'] for o in release['options']]}")
    assert "reaudit" in [o["key"] for o in reaudit["options"]] and rework["options"][0]["key"] == "extend_rework"
    assert release["options"][0]["key"] == "release"

    # 件①: Owner 会话页每个选项一个按钮, 话术折叠为兜底; 非 Owner 会话只读(无按钮 / 无脚本)
    status, page, _h = board.request("/owner/inbox")
    sec = _section(page, reaudit["item_id"])
    buttons = re.findall(r'<button type="button" class="ib-decide-btn[^"]*" data-ib-choice="(\w+)"', sec)
    _show(f"[件①·Owner 会话收件箱 {TASK} 按钮] {buttons}\n[页面脚本] {re.findall(r'<script src=\"([^\"]+)\"', page)}")
    assert status == 200 and buttons == [o["key"] for o in reaudit["options"]]
    assert '<details class="ib-fallback"><summary>顾问没跟上时可复制的话术</summary>' in sec and "<form" not in sec
    assert '<script src="/inbox-decide.js"></script>' in page and f'data-ib-write-path="{DECIDE_PATH}"' in page
    status, ro, _h = board.request("/owner/inbox", who="advisor")
    assert status == 200 and "ib-decide-btn" not in ro and "inbox-decide.js" not in ro and "直接裁决只对 Owner 登录会话开放" in ro
    status, script, _h = board.request("/inbox-decide.js")
    assert status == 200 and "data-ib-write-path" in script

    # 件①: reaudit 一键裁决(预演零写入 → 确认落记录 + 自动落账)
    head0 = _git(gov, "rev-parse", "HEAD")
    before = _digest(gov)
    body = {"workspace": reaudit["project"], "item_id": reaudit["item_id"], "choice": "reaudit", "owner_words": WORDS}
    status, pre = board.decide({**body, "stage": "preview"})
    _show(f"---- 件①·reaudit 预演原文 HTTP {status} ----\n" + "\n".join(pre["preview_lines"]))
    assert status == 200 and _digest(gov) == before and _git(gov, "rev-parse", "HEAD") == head0, "预演零写入"
    assert pre["preview"]["decision"]["verdict"] == "PASS" and f"  Owner 原话: {WORDS}" in pre["preview_lines"]
    assert any(line.startswith("  记录人: board:owner:owner-fixture:session-sha256:") for line in pre["preview_lines"])
    assert any(f"将由 {DRIVER} 的下一次 loop 读到" in line and "原命令重跑 loop" in line for line in pre["preview_lines"])
    status, done = board.decide({**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show(f"---- 件①·reaudit 确认原文 HTTP {status} ----\n{json.dumps({k: done.get(k) for k in ('ok', 'decision_id', 'advisor', 'captured_by', 'capture_surface', 'landing_line', 'redirect')}, ensure_ascii=False, indent=1)}")
    assert status == 200 and done["ok"] and done["decision_id"] == reaudit["decision_id"] and done["advisor"] == DRIVER
    assert "落账: verdict=PASS committed=True pushed=True" in done["landing_line"]
    assert done["redirect"] == f"/owner/inbox?ruled={reaudit['item_id']}"
    text = _decision_text(gov, reaudit["decision_id"])
    keys = ("decided_by_ref", "captured_by", "capture_surface", "decision_type", "task_id")
    _show("[件①·reaudit 决策记录字段原文]\n" + "\n".join(_fm_lines(text, keys)) + "\n" + "\n".join(
        ln for ln in text.splitlines() if ln.startswith(("- Rationale:", "- Evidence ref:", "- Allowed next action:"))))
    assert "capture_surface: board" in text and "decided_by_ref: owner" in text
    assert re.search(r"^captured_by: board:owner:owner-fixture:session-sha256:[0-9a-f]{16}$", text, re.M)
    assert f"- Rationale: Owner 原话: {WORDS}" in text and "- Allowed next action: reaudit" in text
    assert re.search(r"- Evidence ref: 来源=看板 Owner 会话\(会话指纹 sha256:[0-9a-f]{16}; 登录方式 device_code; 来源 IP 127\.0\.0\.1; "
                     r"凭据引用 owner-fixture\)", text)
    assert board.owner_sid not in text and board.owner_sid not in json.dumps(done)
    landed_files = _git(gov, "show", "--name-only", "--format=%s", "HEAD").splitlines()
    _show(f"[件①·落账 git show --name-only HEAD]\n" + "\n".join(landed_files))
    assert _git(gov, "rev-parse", "HEAD") != head0 and "Owner 待决裁决落账" in landed_files[0]
    assert f"5_tasks/records/owner_decisions/{reaudit['decision_id']}.md" in landed_files
    assert "owner_decisions" not in _git(gov, "status", "--porcelain", "-uall"), "裁决写出已全部落账(靶场 loop 自身未落账的文件不计)"

    # 横幅: 刷新落点(真相推导)「已裁决 → 将由 <顾问> 的下一次 loop 接续」; 该事项移入「已裁决待接续」
    status, landed, _h = board.request(done["redirect"])
    banner = re.search(r'<p class="env-banner ib-banner"[^>]*>(.*?)</p>', landed).group(1)
    _show(f"[件①·刷新后横幅原文] {banner}")
    assert banner == f"已裁决「环境 / 产品已修好, 重新派审」→ 将由 {DRIVER} 的下一次 loop 接续"
    assert f'data-item-id="{reaudit["item_id"]}"' not in landed and "已裁决待接续 · 顾问下次 loop 自动接续" in landed

    # AIPOS-F248 件④: 看板裁决告知(lybra next / loop status 末尾同一渲染 owner_inbox.board_notice_lines; 该卡驱动顾问视角)
    from tools.aipos_cli.owner_inbox import board_notice_lines

    notice = board_notice_lines(gov, DRIVER)
    _show("---- AIPOS-F248 件④·顾问侧看板裁决告知原文 ----\n" + "\n".join(notice))
    assert notice and notice[0].startswith("Owner 在看板直接定了") and any(WORDS in ln and "reaudit" in ln for ln in notice[1:])

    # 件②: 顾问侧 loop status / next 直接读到(无需 owner decide)
    st_text, st = _status(gov, TASK)
    na = st["next_action"]
    _show("---- 件②·顾问 lybra loop status(reaudit 裁决后)原文尾 ----\n" + "\n".join(st_text.splitlines()[-4:]))
    assert na["action"] == "advisor_fix" and na["reason"] == "owner_ruled" and na["owner_ruling"]["choice"] == "reaudit"
    assert f"lybra loop --task-id {TASK}" in na["hint"] and f"(Owner 本人在看板裁决; Owner 原话: 「{WORDS}」)" in na["detail"]
    rc, nxt, err = _cli(["--workspace-root", str(gov), "next", "--task-id", TASK])
    _show(f"---- 件②·顾问 lybra next --task-id {TASK} 原文尾 ----\n" + "\n".join(nxt.splitlines()[-3:]))
    assert "Owner 已裁决: 选 reaudit(环境 / 产品已修好, 重新派审)" in nxt and "  顾问下一条: 按 Owner 裁决重新派审" in nxt

    # 件①: extend_rework / release 同一写路由一键裁决(选项标签作 Owner 原话)
    _pre, done_rw = _rule(board, rework, "extend_rework")
    _pre, done_no = _rule(board, release, "release")
    rw_text, no_text = _decision_text(gov, rework["decision_id"]), _decision_text(gov, release["decision_id"])
    assert "- Rationale: Owner 原话: 看板选「授权本卡追加一轮返工(每次裁决只放一轮)」" in rw_text and "capture_surface: board" in rw_text
    assert "- Allowed next action: release" in no_text and "capture_surface: board" in no_text
    card = (gov / "5_tasks" / "queue" / "pending" / f"{NO.lower()}.md").read_text(encoding="utf-8")
    amend = sorted((gov / "5_tasks" / "records" / "amendments" / NO).glob("*.md"))[0].read_text(encoding="utf-8")
    _show(f"[件①·release 效果] 卡面 needs_owner 行: {re.search(r'^needs_owner: .*$', card, re.M).group(0)}; 修订理由: "
          f"{re.search(r'^reason: (.*)$', amend, re.M).group(1)}")
    assert re.search(r"^needs_owner: false$", card, re.M) and release["decision_id"] in amend

    # 件②: 顾问侧读到 extend_rework(next 行 + 返工出口按裁决放一轮)/ release(next 行)
    rc, nxt, err = _cli(["--workspace-root", str(gov), "next", "--task-id", RW])
    _show(f"---- 件②·顾问 lybra next --task-id {RW} 原文尾 ----\n" + "\n".join(nxt.splitlines()[-2:]))
    assert "Owner 已裁决: 选 extend_rework(授权本卡追加一轮返工" in nxt and "queue rework --task-id " + RW in nxt
    rc, st_rw, err = _cli(["--workspace-root", str(gov), "loop", "status", "--task-id", RW])
    _show(f"---- 件②·顾问 lybra loop status --task-id {RW}(本卡无 loop 运行)原文 ----\n{st_rw}{err}")
    assert "Owner 已裁决: 选 extend_rework" in st_rw and "queue rework --task-id " + RW in st_rw
    res = f222._queue_rework(gov, RW, three["v3"], dry_run=True)
    assert res.get("ok") and res["data"]["rework_round"]["owner_extension_ref"] == rework["decision_id"], res
    rc, nxt, err = _cli(["--workspace-root", str(gov), "next", "--task-id", NO])
    _show(f"---- 件②·顾问 lybra next --task-id {NO} 原文尾 ----\n" + "\n".join(nxt.splitlines()[-2:]))
    assert "Owner 已裁决: 选 release(放行继续" in nxt and "按常规推进" in nxt
    rc, st_no, err = _cli(["--workspace-root", str(gov), "loop", "status", "--task-id", NO])
    _show(f"---- 件②·顾问 lybra loop status --task-id {NO}(本卡无 loop 运行)原文 ----\n{st_no}{err}")
    assert "Owner 已裁决: 选 release" in st_no

    # 件②④: 三件都在「已裁决待接续」(F201 relays 同一推导); 顾问续跑 / 登记后各自消失
    entries = {e["task_id"]: e for e in _relay_entries(home)}
    _show(f"[件④·已裁决待接续] {sorted((k, v['choice']) for k, v in entries.items())}")
    assert {TASK, RW, NO} <= set(entries)
    res, text = f202._loop(rig, _block_watch(rig, {f"{TASK}R3": "BLOCK"}), f202.COMMIT_NEW)
    _show(f"---- 件④·顾问原命令续跑 loop(派 R3): reason={res.reason} ----\n" + "\n".join(_quiet(text).splitlines()[:6]))
    assert f"--audit-task-id {TASK}R3" in text
    res = f222._queue_rework(gov, RW, three["v3"], dry_run=False)
    assert res.get("ok"), res
    from tools.aipos_cli.loop_driver import run_loop

    import io

    run_loop(NO, gov, actor=DRIVER, policy_id=f90.POLICY, out=io.StringIO(), watch=lambda args, expect_ready, deploy_guard=None: 3,
             interval=0.05, max_wait=1, max_steps=6)
    left = {e["task_id"] for e in _relay_entries(home)}
    _show(f"[件④·顾问接续后「已裁决待接续」剩余] {sorted(left)}")
    assert not ({TASK, RW, NO} & left)
    status, page, _h = board.request(done["redirect"])
    assert "ib-banner" in page and 'data-pending="false"' in page, "接续后横幅只说已裁决(不再点名待接续)"
    log = _audit_lines(board)
    _show("[看板审计日志 outcome] " + str([(r["action"], r.get("item_id"), r["outcome"]) for r in log]))
    assert [r["outcome"] for r in log] == ["previewed", "confirming", "ruled"] * 3
    assert all(r["action"] == "owner_inbox_decide" and r["session_ref"].startswith("sha256:") for r in log)
    assert {done_rw["decision_id"], done_no["decision_id"]} == {rework["decision_id"], release["decision_id"]}


# ===========================================================================
# 件① 拒因: 未登录 / 会话过期 / 非 Owner / 预演令牌过期 / 重复确认 / 他会话 / 改参数 / 已裁决
# ===========================================================================

def _light_rig(tmp_path: Path, monkeypatch) -> dict:
    """F168 同形轻靶场(两项目 alpha / beta-x 自造人肉期样本), 另加一对 needs_owner 孪生卡(看板 / CLI 各撤一张, 比效果)。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out = f168._cli("project", "new", f168.ALPHA, "--home-root", str(hroot), "--actor", "owner")
    assert rc == 0, out
    alpha = hroot / f168.ALPHA
    f168._card(alpha, "ALPHA-1", "claimed", f168.ALPHA)
    f168._owner_needed_run(alpha, "ALPHA-1", f168.DRIVER_A, owner_reason="direction_fork")  # AIPOS-F228: F225 口径, 门拒设计分叉 = 顾问带第一档事由升
    for task in ("ALPHA-WB", "ALPHA-WC"):
        f168._card(alpha, task, "pending", f168.ALPHA, needs_owner=True)
    init_governance_repo(hroot)
    return {"home": hroot, "alpha": alpha}


@pytest.fixture
def lite(tmp_path, monkeypatch):
    data = _light_rig(tmp_path, monkeypatch)
    board = Board(data["home"], tmp_path)
    try:
        yield {**data, "board": board}
    finally:
        board.close()


def test_item1_refusals_each_with_its_reason(lite):
    home, board = lite["home"], lite["board"]
    item = _item(home, "ALPHA-1", "loop_owner_needed")
    body = {"workspace": item["project"], "item_id": item["item_id"], "choice": "continue"}
    before = _digest(home)
    rows = []
    # 未登录: 鉴权闸门 302 到登录页(不进写路由, 零写入)
    status, text, hdrs = board.request(DECIDE_PATH, "POST", {**body, "stage": "preview"}, who=None)
    rows.append(("未登录", status, hdrs.get("location")))
    assert status == 302 and hdrs.get("location") == "/login"
    # 会话过期(会话已撤销 / 服务重启): 同一闸门
    expired = board.store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="otc")
    board.store.revoke(expired)
    status, text, hdrs = board.request(DECIDE_PATH, "POST", {**body, "stage": "preview"}, who=expired)
    rows.append(("会话过期", status, hdrs.get("location")))
    assert status == 302 and hdrs.get("location") == "/login"
    # 非 Owner 会话
    status, d = board.decide({**body, "stage": "preview"}, who="advisor")
    rows.append(("非 Owner", status, d.get("error"), d.get("message")))
    assert status == 403 and d["error"] == "OWNER_ONLY"
    # 选项不属该事项 / 缺参数 / workspace 不符
    status, d = board.decide({**body, "choice": "release", "stage": "preview"})
    rows.append(("选项不属该事项", status, d.get("error"), d.get("message")))
    assert status == 400 and d["error"] == "CHOICE_INVALID" and "可选: continue, rework, pause" in d["message"]
    status, d = board.decide({"workspace": item["project"], "stage": "preview"})
    rows.append(("缺 item_id", status, d.get("error"), d.get("message")))
    assert status == 400 and d["error"] == "PARAMS_INVALID"
    # 预演令牌过期(令牌表时钟拨过 TTL)
    status, pre = board.decide({**body, "stage": "preview"})
    assert status == 200
    real_now = board.previews._now
    board.previews._now = lambda: real_now() + 301
    try:
        status, d = board.decide({**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    finally:
        board.previews._now = real_now
    rows.append(("预演令牌过期", status, d.get("error"), d.get("message")))
    assert status == 403 and d["error"] == "PREVIEW_TOKEN_EXPIRED" and "300 秒" in d["message"]
    # 他会话的令牌 / 预演后改了参数
    status, pre = board.decide({**body, "stage": "preview"})
    status, d = board.decide({**body, "stage": "confirm", "preview_token": pre["preview_token"]}, who="owner2")
    rows.append(("他会话令牌", status, d.get("error"), d.get("message")))
    assert status == 403 and d["error"] == "PREVIEW_SESSION_MISMATCH"
    status, pre = board.decide({**body, "stage": "preview"})
    status, d = board.decide({**body, "choice": "pause", "stage": "confirm", "preview_token": pre["preview_token"]})
    rows.append(("预演后改选项", status, d.get("error"), d.get("message")))
    assert status == 403 and d["error"] == "PREVIEW_PARAMS_MISMATCH"
    status, pre = board.decide({**body, "stage": "preview"})
    status, d = board.decide({**body, "stage": "confirm", "preview_token": "not-a-token"})
    rows.append(("令牌错 / 已用", status, d.get("error"), d.get("message")))
    assert status == 403 and d["error"] == "PREVIEW_TOKEN_INVALID"
    status, d = board.decide({**body, "stage": "confirm"})
    rows.append(("未预演直接确认", status, d.get("error"), d.get("message")))
    assert status == 403 and d["error"] == "PREVIEW_TOKEN_REQUIRED"
    assert _digest(home) == before, "以上全部拒 = 零写入"
    # 正常确认一次; 同一令牌重复确认 = 拒; 再预演 = 已裁决(收件箱已无该事项)
    status, pre = board.decide({**body, "stage": "preview"})
    status, ok = board.decide({**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    assert status == 200 and ok["ok"], ok
    status, d = board.decide({**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    rows.append(("重复确认(同一令牌)", status, d.get("error"), d.get("message")))
    assert status == 409 and d["error"] == "ALREADY_RULED" and item["decision_id"] in d["message"]
    status, d = board.decide({**body, "stage": "preview"})
    rows.append(("已裁决后再点", status, d.get("error"), d.get("message")))
    assert status == 409 and d["error"] == "ALREADY_RULED"
    _show("[件①·拒因原文]\n" + "\n".join(" | ".join(str(x) for x in row) for row in rows))
    decisions = sorted((lite["alpha"] / "5_tasks" / "records" / "owner_decisions").glob("owner-inbox-*.md"))
    assert [p.stem for p in decisions] == [item["decision_id"]], "只落一条裁决记录"
    outcomes = [r["outcome"] for r in _audit_lines(board)]
    _show(f"[看板审计日志 outcome(含被拒)] {outcomes}")
    for code in ("OWNER_ONLY", "CHOICE_INVALID", "PARAMS_INVALID", "PREVIEW_TOKEN_EXPIRED", "PREVIEW_SESSION_MISMATCH",
                 "PREVIEW_PARAMS_MISMATCH", "PREVIEW_TOKEN_INVALID", "PREVIEW_TOKEN_REQUIRED", "ALREADY_RULED", "ruled"):
        assert code in outcomes, code


def test_item1_withdraw_effect_same_as_cli_decide(lite):
    home, alpha, board = lite["home"], lite["alpha"], lite["board"]
    wb = _item(home, "ALPHA-WB", "needs_owner_card")
    wc = _item(home, "ALPHA-WC", "needs_owner_card")
    _pre, done = _rule(board, wb, "withdraw", words="不做了")
    rc, out = f168._cli("owner", "decide", "--item", wc["item_id"], "--choice", "withdraw", "--relayed-by", "owner", "--owner-words", "不做了",
                        "--home-root", str(home), "--confirm")
    _show(f"---- 件③·CLI owner decide withdraw 原文 exit {rc} ----\n{out}")
    assert rc == 0, out

    def view(task: str, decision_id: str) -> dict:
        card_path = alpha / "5_tasks" / "queue" / "withdrawn" / f"{task.lower()}.md"
        fm = {k: v for k, v in (ln.split(": ", 1) for ln in card_path.read_text(encoding="utf-8").split("---")[1].strip().splitlines()
                                 if ": " in ln)}
        return {"queue": card_path.parent.name, "status": fm.get("status"), "owner_ruling_ref": fm.get("withdraw_owner_ruling_ref")
                or decision_id in card_path.read_text(encoding="utf-8"), "fields": sorted(fm)}

    b, c = view("ALPHA-WB", wb["decision_id"]), view("ALPHA-WC", wc["decision_id"])
    _show(f"[件③·撤卡效果对照] 看板={b}\nCLI={c}")
    assert b["queue"] == c["queue"] == "withdrawn" and b["status"] == c["status"] and b["fields"] == c["fields"]
    assert b["owner_ruling_ref"] and c["owner_ruling_ref"]
    tb, tc = _decision_text(alpha, wb["decision_id"]), _decision_text(alpha, wc["decision_id"])
    shape = lambda t: sorted(ln.split(":", 1)[0] for ln in t.split("---")[1].strip().splitlines() if ":" in ln and not ln.startswith(" "))  # noqa: E731
    assert shape(tb) == shape(tc) and "capture_surface: board" in tb and "capture_surface: cli" in tc
    assert "- Rationale: Owner 原话: 不做了" in tb and "- Rationale: Owner 原话: 不做了" in tc


# ===========================================================================
# 声明 / 单一实现 / 只读例外清单
# ===========================================================================

def test_declaration_single_framework_and_guards():
    import importlib.util

    from tools.aipos_cli.envelope_board import board_envelopes_declaration
    from tools.aipos_cli.owner_inbox import board_decide_declaration
    from web.board.app import board_readonly_declaration

    spec = board_decide_declaration()
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    ro = verbs["board_readonly"]
    _show(f"[声明] write_path={spec['write_path']} script={spec['script_path']} 写例外清单={ro['write_exceptions']}")
    assert spec["write_path"] == DECIDE_PATH and list(ro["write_exceptions"]) == ["envelope_mint", "project_upgrade", "owner_inbox_decide"]
    assert spec["write_path"] not in board_readonly_declaration()["allowed_post_paths"]
    assert spec["write_path"] != board_envelopes_declaration()["write_path"]
    src = (REPO_ROOT / "tools" / "aipos_cli" / "owner_inbox.py").read_text(encoding="utf-8")
    handler = src.split("def handle_board_decide(", 1)[1].split("\ndef ", 1)[0]
    assert "return handle_owner_write(" in handler and "decide(" in handler and "record_owner_decision(" not in handler
    assert "preview_store.take(" not in src and "append_board_audit(" not in src, "令牌 / 审计日志只在 F172 框架一处"
    app = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    do_post = app[app.index("def do_POST(self)"):app.index("def do_PUT(self)")]
    assert "if path == self._inbox_decide_path:" in do_post and "record_owner_decision(" not in app and "execute_dry_run(" not in app
    js = (REPO_ROOT / "web" / "board" / "static" / "inbox-decide.js").read_text(encoding="utf-8")
    assert js.count("fetch(") == 1 and "/api/" not in js and "writePath" in js, "前端只打页面声明的那一条写路由"
    guard_spec = importlib.util.spec_from_file_location("f226_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(guard_spec)
    guard_spec.loader.exec_module(guard)
    hits = {name: guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / name) for name in ("static/inbox-decide.js", "readonly_pages.py")}
    _show(f"[CJK 守卫逐行判定] {hits}")
    assert hits == {"static/inbox-decide.js": [], "readonly_pages.py": []}
    from web.board.app import _I18N_TEMPLATES

    keys = {k for k in _I18N_TEMPLATES["zh"] if k.startswith(("ib_dc_", "ib_decide", "ib_ruled", "ib_words", "ib_fallback", "ib_deny"))}
    assert keys and keys <= set(_I18N_TEMPLATES["en"])


def test_f226_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f226_board_inbox_decide.py", default_test_contract())
