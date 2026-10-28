"""AIPOS-F248 — 看板「需要你」: 每件给推荐选项 + 一句理由, 推荐按钮醒目可一键生效(含验收抽查与授权续期), 裁完自动告知对应顾问。

件① 推荐(结构化): `lybra owner recommend` 写只追加记录(写入方 = 事项转达顾问本人或 Owner, 他人拒), 事项真相变了推荐失效;
    无顾问推荐 = 产品缺省(信封将尽 = 按原范围续期; 验收抽查无缺省)。
件② 一键: 验收抽查「符合 / 不符(带一句)」走看板写例外③同一路由同一框架(会话 / Origin / 一次性预演令牌 / 参数摘要含事项哈希 /
    看板审计日志), 裁决本身 = `lybra roadmap verify` 同一实现(记 owner_verifications); 续期「续 N 天」走写例外①信封签发同一实现。
件③ 呈现: 首页每件 = 人话标题 + 一句理由 + 看产物 + 推荐按钮(主色实心标「推荐」)+ 其他选项小字; 同一里程碑多件抽查合并可展开。
件④ 告知顾问: 看板裁决 / 抽查 / 续期落账后, 顾问下一次 `lybra next` / `lybra loop status` 末尾读到原文。

靶场 = AIPOS-F175 抽查靶场(临时 HOME + 临时 home 根 git 仓 + 临时远端裸仓; 路线图 / 卡 / 门裁决 / 产物经产品命令或声明落点造)+ F246
接入构件(顾问登记 + 治理根连接文件, 假 token 非密钥)+ F172 信封构件与进程内门替身(门侧真实写入实现, 只替换 HTTP 传输)+ 测试进程内
线程起的看板(测试端口, 测试会话表 / 预演令牌表 / 审计日志)。全程不碰真实治理根 / 生产看板 / 真实凭据 / 真门。验收原文经 _show 打到 stdout。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import sys
import threading
import time
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f175_verification_owner_check as f175  # noqa: E402  — 抽查靶场(禁第二份)
from test_aipos_f158_envelope_landing_record_type_migration import InProcessGate  # noqa: E402  — 门替身唯一来源
from test_aipos_f171_roadmap_board_readonly import _cli  # noqa: E402
from test_aipos_f172_board_envelopes import _claims, _policy  # noqa: E402  — 信封构件(禁第二份)
from test_aipos_f175_verification_owner_check import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f181_board_usability import _free_port  # noqa: E402
from test_aipos_f246_board_home import _enroll, forbidden_hits  # noqa: E402  — 顾问接入构件 / 首页禁词守卫

ADVISOR, OTHER = f175.ADVISOR, f"advisor-b.{f175.PROJECT}.h1"
DECIDE_PATH = "/api/owner/inbox/decide"
MINT_PATH = "/api/owner/envelopes/mint"
ENVELOPE = "pol_f248_loop_1"
REASON = "首页截图里导航与卡片布局都和设计稿一致"


def _show(text: str) -> None:
    print(text, flush=True)


def _digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


class Board:
    """测试进程内线程起的看板(测试端口; 会话表 / 预演令牌表 / 审计日志 / Owner 签发凭据均为夹具)。请求不跟随重定向。"""

    def __init__(self, home: Path, tmp: Path, conn: Path) -> None:
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
            mint_connection_json=conn, board_audit_log_path=self.audit_log))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def request(self, path: str, method: str = "GET", body: dict | None = None, who: str | None = "owner",
                headers: dict | None = None) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        sid = {"owner": self.owner_sid, "owner2": self.owner2_sid, "advisor": self.advisor_sid}.get(who or "", who)
        if sid:
            hdrs["Cookie"] = f"{self.cookie_name}={sid}"
        conn.request(method, path, body=json.dumps(body or {}).encode("utf-8") if method != "GET" else None, headers=hdrs)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8")
        conn.close()
        return resp.status, text

    def post(self, path: str, body: dict, who: str | None = "owner", headers: dict | None = None) -> tuple[int, dict]:
        status, text = self.request(path, "POST", body, who=who, headers=headers)
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, {"_raw": text[:200]}

    def page(self, path: str, who: str | None = "owner") -> str:
        status, text = self.request(path, who=who)
        assert status == 200 and "rp-page-error" not in text, text[-800:]
        return text


@pytest.fixture
def f248(rig, monkeypatch):  # noqa: F811
    """F175 抽查靶场 + 两位已接入顾问(连接文件: 两位顾问驱动方凭据 + Owner 凭据)+ 一只将尽的信封(原有效期 7 天、3 张, 已用 1 张)+ 看板。"""
    import tools.aipos_cli.confirm_client as confirm_client
    from tools.aipos_cli.clock import utc_now

    gov, home, tmp = rig["gov"], rig["home"], rig["tmp"]
    _enroll(gov, [ADVISOR, OTHER], owner=True)
    now = utc_now()
    _policy(gov, ENVELOPE, who=ADVISOR, max_tasks=3, active_from=now - timedelta(days=4), expires_at=now + timedelta(days=3))
    _claims(gov, ENVELOPE, 1)
    f175._commit(home, "fixture: enrollment + envelope")
    conn = tmp / "owner-vault" / "connection.json"  # 看板签发用 Owner 凭据(假 token, 仓外; 只出指纹)
    conn.parent.mkdir(parents=True, exist_ok=True)
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"}, "tokens": [
        {"role": "owner", "token": "fixture-f248-owner-not-a-secret", "scopes": ["owner_confirm"], "token_ref": "owner-fixture"}]}),
        encoding="utf-8")
    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    board = Board(home, tmp, conn)
    try:
        yield {**rig, "board": board, "conn": conn}
    finally:
        board.close()


def _inbox(home: Path) -> dict:
    rc, out, err = _cli("owner", "inbox", "--home-root", str(home), "--include-paused", "--json")
    assert rc in (0, 3), out + err
    return json.loads(out)


def _item(home: Path, category: str, criterion: str | None = None) -> dict:
    data = _inbox(home)
    found = [i for i in [*data["items"], *data["notices"]] if i["category"] == category
             and (criterion is None or (i.get("facts") or {}).get("criterion") == criterion)]
    assert len(found) == 1, (category, criterion, [i["item_id"] for i in found])
    return found[0]


def _audit(board: Board) -> list[dict]:
    return [json.loads(ln) for ln in board.audit_log.read_text(encoding="utf-8").splitlines()] if board.audit_log.is_file() else []


# ===========================================================================
# 件① 推荐: 顾问写 / 他人写拒 / 事项变化后失效 / 产品缺省
# ===========================================================================

def test_item1_recommend_writer_refusals_invalidation_and_default(f248):
    from tools.aipos_cli.owner_recommend import recommend_declaration, recommendation_for

    home, gov = f248["home"], f248["gov"]
    c4 = _item(home, "owner_check", "C4")
    _show(f"[件①·C4 事项] {c4['item_id']} relay_to={c4['relay_to']} options={[o['key'] for o in c4['options']]} recommended={c4['recommended']}")
    assert c4["relay_to"] == ADVISOR and c4["recommended"] is None, "验收抽查无产品缺省 = 不标推荐"
    before = _digest(home)
    rc, out, err = _cli("owner", "recommend", "--item", c4["item_id"], "--choice", "owner_confirm", "--reason", REASON,
                        "--home-root", str(home))
    _show(f"---- 件①·顾问写推荐 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "预演(零写入)" in out and _digest(home) == before
    # 他人写拒: 另一位顾问(不是该事项转达对象)/ 选项不属该事项 / 理由空 / 多行
    for argv, code in (((*("--actor", OTHER), "--choice", "owner_confirm", "--reason", REASON), "not_self"),
                       (("--choice", "release", "--reason", REASON), "bad_choice"),
                       (("--choice", "owner_confirm", "--reason", " "), "empty"),
                       (("--choice", "owner_confirm", "--reason", "第一行\n第二行"), "too_long")):
        rc, out, err = _cli("owner", "recommend", "--item", c4["item_id"], *argv, "--home-root", str(home), "--confirm")
        _show(f"---- 件①·拒 {code} exit {rc} ----\n{err.strip()}")
        assert rc == 1 and f"拒({code})" in err, (code, out, err)
    assert _digest(home) == before, "被拒 = 零写入"
    rc, out, err = _cli("owner", "recommend", "--item", c4["item_id"], "--choice", "owner_confirm", "--reason", REASON,
                        "--home-root", str(home), "--confirm")
    _show(f"---- 件①·顾问写推荐 确认 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "已写推荐" in out and "落账: verdict=PASS committed=True pushed=True" in out
    recs = sorted((gov / "5_tasks" / "records" / "owner_recommendations" / c4["item_id"]).glob("recommend_*.md"))
    assert len(recs) == 1
    _show(f"[件①·推荐记录原文 {recs[0].relative_to(gov)}]\n{recs[0].read_text(encoding='utf-8')}")
    text = recs[0].read_text(encoding="utf-8")
    assert "record_type: owner_recommendation" in text and f"written_by: {ADVISOR}" in text and "choice: owner_confirm" in text
    c4 = _item(home, "owner_check", "C4")
    _show(f"[件①·收件箱 recommended] {json.dumps(c4['recommended'], ensure_ascii=False)}")
    assert c4["recommended"]["choice"] == "owner_confirm" and c4["recommended"]["source"] == "advisor"
    assert c4["recommended"]["reason"] == REASON and c4["recommended"]["by"] == ADVISOR and c4["recommended"]["action"] == "verify"
    rc, out, err = _cli("owner", "inbox", "--home-root", str(home))
    line = next(ln for ln in out.splitlines() if "推荐:" in ln and REASON in ln)
    _show(f"[件①·CLI 收件箱推荐行] {line}")
    assert f"推荐: 符合 — {REASON}(顾问 {ADVISOR} 建议)" in line
    # Owner 也可写(只追加, 取最新一条; 同秒文件名相撞 = 拒, 故这里给下一秒的时刻走同一写入实现)
    from tools.aipos_cli.clock import utc_now
    from tools.aipos_cli.owner_recommend import write_recommendation

    by_owner = write_recommendation(c4["item_id"], choice="owner_confirm", reason="Owner 自己看过", actor="owner", home_root=home,
                                    confirm=True, moment=utc_now() + timedelta(seconds=2))
    _show(f"[件①·Owner 写推荐] {by_owner['record']} 写入方 {by_owner['actor']}({by_owner['writer_role']}); {by_owner['landing']['line']}")
    assert by_owner["landing"]["landed"]
    # 事项哈希不等 = 失效(同一 ID 下问题本身变了, 如选项变了): 唯一读取口直接判
    changed = {**c4, "options": [o for o in c4["options"] if o["key"] != "owner_mismatch"]}
    assert recommendation_for(gov, changed) is None and recommendation_for(gov, c4)["reason"] == "Owner 自己看过"
    _show(f"[件①·hash_basis] {recommend_declaration()['hash_basis']}")
    # 真相变了(C4 新交付: 又一张卡挂同一标准且已自动验证通过 = 新锚点 = 新事项)→ 旧推荐不再挂到任何事项
    f175._card(gov, "completed", "K-4B", criteria=["C4"], completed_at=f175._iso(-0.5),
               artifacts=[{"criterion": "C4", "path_glob": "home2.png", "kind": "screenshot"}])
    f175._verdict(gov, "K-4B")
    f175._artifact(gov, "K-4B", "home2.png", f175.PNG)
    f175._commit(home, "fixture: second delivery for C4")
    fresh = _item(home, "owner_check", "C4")
    _show(f"[件①·真相变后] 旧事项 {c4['item_id']} → 新事项 {fresh['item_id']} recommended={fresh['recommended']}")
    assert fresh["item_id"] != c4["item_id"] and fresh["recommended"] is None
    # 产品缺省: 信封将尽 = 按原范围续期(原有效期 7 天、原额度 3 张)
    env = _item(home, "envelope_warning")
    _show(f"[件①·信封将尽缺省推荐] {json.dumps(env['recommended'], ensure_ascii=False)}")
    rec = env["recommended"]
    assert rec["source"] == "default" and rec["action"] == "renew_envelope" and rec["days"] == 7 and rec["max_tasks"] == 3
    assert rec["params"]["mode"] == "renew" and rec["params"]["template_policy_id"] == ENVELOPE and rec["params"]["request_item"] == env["item_id"]


# ===========================================================================
# 件②③ 验收抽查一键: 首页呈现 / 预演零写入 / 令牌各类拒 / 确认落 owner_verifications / 进度变化 / 审计日志
# ===========================================================================

def test_item2_spot_check_one_click_end_to_end(f248):
    from tools.aipos_cli.board_home import home_declaration

    home, gov, board = f248["home"], f248["gov"], f248["board"]
    c4, c5 = _item(home, "owner_check", "C4"), _item(home, "owner_check", "C5")
    rc, out, err = _cli("owner", "recommend", "--item", c4["item_id"], "--choice", "owner_confirm", "--reason", REASON,
                        "--home-root", str(home), "--confirm")
    assert rc == 0, out + err
    page = board.page("/owner/home")
    asks = re.search(r'<section class="hm-asks".*?</section>', page, re.S).group(0)
    _show(f"[件③·首页「需要你」原文(Owner 会话)]\n{asks}")
    group = re.search(r'<div class="hm-ask hm-group"[^>]*data-group="2".*?</details></div></div>', asks, re.S)
    assert group, "同一里程碑两件抽查合并成一件「2 项验收抽查」"
    assert "页面可交付</span>:2 项验收抽查" in group.group(0) and "逐项看" in group.group(0)
    one = re.search(r'<div class="hm-ask" id="ask-' + c4["item_id"] + r'".*?</div></div>(?=<div class="hm-ask"|</details>)', asks, re.S).group(0)
    assert 'data-recommended="true"' in one and '<button type="button" class="hm-btn" data-ask-primary="true"' in one
    assert "<small>推荐</small>符合</button>" in one and '>不符…</button>' in one and "顾问建议「符合」:" not in one
    assert f"建议「符合」:<span data-authored>{REASON}</span>" in one and "看产物 →" in one and "2 个产物" in one
    five = re.search(r'<div class="hm-ask" id="ask-' + c5["item_id"] + r'".*?</div></div>(?=<div class="hm-ask"|</details>)', asks, re.S).group(0)
    assert 'data-recommended="false"' in five and "hm-btn" not in five and ">符合</button>" in five, "无推荐 = 选项平列"
    assert '<script src="/owner-asks.js"></script>' in page and 'data-asks-owner="true"' in page
    hits = forbidden_hits(page, home_declaration()["forbidden_words"], extra=[ADVISOR, OTHER, c4["item_id"]], include_authored=False)
    _show(f"[件③·首页禁词(界面文字)] {hits}")
    assert hits == []
    reader = board.page("/owner/home", who="advisor")
    assert "hm-btn" not in reader and "owner-asks.js" not in reader and REASON in reader, "非 Owner 会话只看推荐, 不出按钮"
    payload = json.loads(re.search(r'data-ask-payload="([^"]+)"', one).group(1).replace("&quot;", '"'))
    assert re.search(r'data-ask-path="([^"]+)"', one).group(1) == DECIDE_PATH
    body = {**payload, "owner_words": ""}
    _show(f"[件②·推荐按钮请求体(页面原样)] {payload}")
    before = _digest(home)
    rows = []
    status, d = board.post(DECIDE_PATH, {**body, "stage": "preview"}, who="advisor")
    rows.append(("非 Owner", status, d.get("error")))
    assert status == 403 and d["error"] == "OWNER_ONLY"
    status, d = board.post(DECIDE_PATH, {**body, "stage": "preview"}, headers={"Origin": "http://evil.example", "Host": f"127.0.0.1:{board.port}"})
    rows.append(("跨站 Origin", status, d.get("error")))
    assert status == 403 and d["error"] == "ORIGIN_MISMATCH"
    status, pre = board.post(DECIDE_PATH, {**body, "stage": "preview"})
    _show("---- 件②·预演原文 ----\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)])
          + f"\n  confirm_line: {pre.get('confirm_line')}")
    assert status == 200 and pre["ok"] and "将记录 Owner 本人验收抽查" in pre["preview_lines"][0]
    assert any("结论: confirm(符合); 标准状态 待 Owner 抽查 → Owner 已确认" in ln for ln in pre["preview_lines"])
    assert pre["confirm_line"] == f"将记录 Owner 本人裁决「符合」; 记录后告知 {ADVISOR}"
    assert _digest(home) == before, "预演零写入"
    # 令牌六类拒: 缺 / 错 / 已用 / 过期 / 他会话 / 改参数
    status, d = board.post(DECIDE_PATH, {**body, "stage": "confirm"})
    rows.append(("缺令牌", status, d.get("error")))
    assert status == 403 and d["error"] == "PREVIEW_TOKEN_REQUIRED"
    status, d = board.post(DECIDE_PATH, {**body, "stage": "confirm", "preview_token": "not-a-token"})
    rows.append(("错令牌", status, d.get("error")))
    assert status == 403 and d["error"] == "PREVIEW_TOKEN_INVALID"
    status, d = board.post(DECIDE_PATH, {**body, "stage": "confirm", "preview_token": pre["preview_token"]}, who="owner2")
    rows.append(("他会话令牌", status, d.get("error")))
    assert status == 403 and d["error"] == "PREVIEW_SESSION_MISMATCH"
    status, d = board.post(DECIDE_PATH, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    rows.append(("已用令牌(他会话已焚)", status, d.get("error")))
    assert status == 403 and d["error"] == "PREVIEW_TOKEN_INVALID"
    status, pre = board.post(DECIDE_PATH, {**body, "stage": "preview"})
    real_now = board.previews._now
    board.previews._now = lambda: real_now() + 301
    try:
        status, d = board.post(DECIDE_PATH, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    finally:
        board.previews._now = real_now
    rows.append(("过期令牌", status, d.get("error")))
    assert status == 403 and d["error"] == "PREVIEW_TOKEN_EXPIRED"
    status, pre = board.post(DECIDE_PATH, {**body, "stage": "preview"})
    status, d = board.post(DECIDE_PATH, {**body, "owner_words": "改了原话", "stage": "confirm", "preview_token": pre["preview_token"]})
    rows.append(("预演后改参数", status, d.get("error")))
    assert status == 403 and d["error"] == "PREVIEW_PARAMS_MISMATCH"
    assert _digest(home) == before, "以上全部拒 = 零写入"
    # 不符须带一句
    status, d = board.post(DECIDE_PATH, {"workspace": c5["project"], "item_id": c5["item_id"], "choice": "owner_mismatch", "stage": "preview"})
    rows.append(("不符未写原话", status, d.get("error"), d.get("message")))
    assert status == 400 and d["error"] == "PARAMS_INVALID" and "须写一句哪里不对" in d["message"]
    _show("[件②·拒因原文]\n" + "\n".join(" | ".join(str(x) for x in row) for row in rows))
    # 确认: 落 owner_verifications(看板来源)+ 落账; 进度 0/2 → 1/2(owner_check 两条)
    met_before = f175._status(gov)
    status, pre = board.post(DECIDE_PATH, {**body, "stage": "preview"})
    status, done = board.post(DECIDE_PATH, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show("---- 件②·确认应答原文 ----\n" + json.dumps({k: done.get(k) for k in ("ok", "decision_id", "choice", "advisor", "actor",
                                                                                 "captured_by", "capture_surface", "landing_line",
                                                                                 "state_after", "audit_logged")}, ensure_ascii=False, indent=1))
    assert status == 200 and done["ok"] and done["state_after"] == "Owner 已确认" and "committed=True pushed=True" in done["landing_line"]
    record = gov / done["decision_id"]
    text = record.read_text(encoding="utf-8")
    _show(f"[件②·owner_verifications 记录原文 {done['decision_id']}]\n{text}")
    for needle in ("record_type: owner_verification", "decided_via: web_session", "relayed_by: owner", f"inbox_item: {c4['item_id']}",
                   f"relay_to: {ADVISOR}", "capture_surface: board", "result: confirm", "criterion: C4"):
        assert needle in text, needle
    after = f175._status(gov)
    _show(f"[件②·标准状态] C4 {met_before['C4']['state']} → {after['C4']['state']}; C5 {after['C5']['state']}")
    assert after["C4"]["state"] == "owner_confirmed" and after["C4"]["satisfied"]
    status, d = board.post(DECIDE_PATH, {**body, "stage": "preview"})
    _show(f"[件②·已抽查后再点] HTTP {status} {d.get('error')}: {d.get('message')}")
    assert status == 409 and d["error"] == "ALREADY_RULED" and done["decision_id"] in d["message"]
    # 不符(带一句): 记录 + 自动起草修复卡
    mis = {"workspace": c5["project"], "item_id": c5["item_id"], "choice": "owner_mismatch", "owner_words": "列表页少了分页"}
    status, pre = board.post(DECIDE_PATH, {**mis, "stage": "preview"})
    _show("---- 件②·不符预演原文 ----\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and any("将起草 K-5-oc1" in ln for ln in pre["preview_lines"]), pre
    status, done5 = board.post(DECIDE_PATH, {**mis, "stage": "confirm", "preview_token": pre["preview_token"]})
    assert status == 200 and done5["ok"], done5
    text5 = (gov / done5["decision_id"]).read_text(encoding="utf-8")
    assert "result: mismatch" in text5 and "owner_words: 列表页少了分页" in text5 and "fix_task_id: K-5-oc1" in text5
    assert f175._status(gov)["C5"]["state"] == "mismatch"
    outcomes = [(r.get("action"), r.get("outcome")) for r in _audit(board)]
    _show(f"[件②·看板审计日志(action, outcome)] {outcomes}")
    for code in ("OWNER_ONLY", "ORIGIN_MISMATCH", "previewed", "PREVIEW_TOKEN_REQUIRED", "PREVIEW_TOKEN_INVALID", "PREVIEW_SESSION_MISMATCH",
                 "PREVIEW_TOKEN_EXPIRED", "PREVIEW_PARAMS_MISMATCH", "PARAMS_INVALID", "confirming", "verified", "ALREADY_RULED"):
        assert ("owner_inbox_decide", code) in outcomes, code
    line = next(r for r in _audit(board) if r.get("outcome") == "verified")
    _show(f"[件②·审计日志 verified 行] {json.dumps(line, ensure_ascii=False)}")
    assert line["item_id"] == c4["item_id"] and line["category"] == "owner_check" and line["digest"]


# ===========================================================================
# 件② 续期一键(写例外①信封签发同一实现)+ 件④ 顾问侧告知原文
# ===========================================================================

def test_item2_renew_one_click_same_mint_path_and_item4_advisor_notice(f248, monkeypatch):
    import tools.aipos_cli.envelope_board as envelope_board
    from tools.aipos_cli.autonomy_policy import envelope_overview
    from tools.aipos_cli.envelope_board import board_envelopes_declaration

    home, gov, board = f248["home"], f248["gov"], f248["board"]
    env = _item(home, "envelope_warning")
    page = board.page("/owner/home")
    ask = re.search(r'<div class="hm-ask" id="ask-' + env["item_id"] + r'".*?(?=\n<div class="hm-ask"|\n<a class="hm-more"|\n</section>)',
                    page, re.S).group(0)
    _show(f"[件②·首页续期件原文]\n{ask}")
    assert "<small>推荐</small>续 7 天</button>" in ask and "改范围…" in ask and ">不续签, 到期 / 用尽即停</button>" in ask
    assert "按原范围续 7 天、3 张(取原授权)" in ask and "看授权范围 →" in ask
    path = re.search(r'data-ask-primary="true" data-ask-path="([^"]+)"', ask).group(1)
    payload = json.loads(re.search(r'data-ask-primary="true"[^>]*data-ask-payload="([^"]+)"', ask).group(1).replace("&quot;", '"'))
    _show(f"[件②·续期按钮] path={path} payload={payload}")
    assert path == MINT_PATH == board_envelopes_declaration()["write_path"], "续期按钮 = 信封页签发同一写路由"
    calls = []
    real = envelope_board.handle_mint_request

    def spy(*a, **kw):
        calls.append(a[0].get("stage"))
        return real(*a, **kw)

    monkeypatch.setattr(envelope_board, "handle_mint_request", spy)
    before = _digest(home)
    status, pre = board.post(path, {**payload, "stage": "preview"})
    _show("---- 件②·续期预演原文 ----\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and pre["ok"] and _digest(home) == before
    status, done = board.post(path, {**payload, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show("---- 件②·续期确认应答原文 ----\n" + json.dumps({k: done.get(k) for k in ("ok", "signed", "actor", "captured_by", "capture_surface",
                                                                                     "landing_line", "error")}, ensure_ascii=False, indent=1))
    assert status == 200 and done["ok"] and done["signed"]
    assert calls == ["preview", "confirm"], "预演与确认都经信封签发唯一实现 envelope_board.handle_mint_request"
    rows = {r["policy_id"]: r for r in envelope_overview(gov)["envelopes"]}
    new = next(pid for pid in rows if pid != ENVELOPE)
    _show(f"[件②·续期后信封] {[(pid, r['status'], r['max_tasks'], r['expires_at'], r['superseded_by']) for pid, r in rows.items()]}")
    assert rows[ENVELOPE]["status"] == "superseded" and rows[new]["max_tasks"] == 3 and rows[new]["agent_or_role"] == ADVISOR
    decision = (gov / "5_tasks" / "records" / "owner_decisions" / f"envelope-{new}.md").read_text(encoding="utf-8")
    assert "capture_surface: board" in decision and env["item_id"] in decision, "签发记录带收件箱事项 ID(request_item)"
    assert not [i for i in _inbox(home)["notices"] if i["category"] == "envelope_warning"], "旧信封被接替 = 将尽事项自行消失"
    # 一键抽查一件(看板裁决的另一种来源), 再看顾问侧告知
    c4 = _item(home, "owner_check", "C4")
    body = {"workspace": c4["project"], "item_id": c4["item_id"], "choice": "owner_confirm", "owner_words": "看过了, 符合"}
    status, pre = board.post(DECIDE_PATH, {**body, "stage": "preview"})
    status, ok = board.post(DECIDE_PATH, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    assert status == 200 and ok["ok"], ok
    rc, out, err = _cli("next", "--workspace-root", str(gov), "--actor", ADVISOR)
    _show(f"---- 件④·顾问 lybra next(项目扫描)末尾原文 exit {rc} ----\n" + "\n".join(out.splitlines()[-6:]))
    assert "Owner 在看板直接定了 2 件(Owner 不再转话; 涉及的卡照常 next / loop 续推):" in out
    assert "验收抽查 C4「首页符合设计」: 符合(Owner 原话「看过了, 符合」" in out and f"看板续签 {ENVELOPE} → {new}" in out
    rc, out, err = _cli("loop", "status", "--workspace-root", str(gov), "--actor", ADVISOR)
    _show(f"---- 件④·顾问 lybra loop status 末尾原文 exit {rc} ----\n" + "\n".join(out.splitlines()[-4:]))
    assert "Owner 在看板直接定了 2 件" in out and "验收抽查 C4" in out
    rc, out, err = _cli("next", "--workspace-root", str(gov), "--actor", OTHER)
    assert "Owner 在看板直接定了" not in out, "不是该顾问的事不告知他"


# ===========================================================================
# 性能 / 新鲜度: 首页热态 ≤0.5s(F245 真相指纹缓存), 真相变化下一次请求即反映
# ===========================================================================

def test_home_hot_within_budget_and_fresh_after_recommend(f248, monkeypatch):
    """F245 同一口径: 真相缓存开(同看板服务进程), racy window 置 0(夹具文件刚写, 否则一律不存 = 每次现算)。"""
    import tools.aipos_cli.frontmatter as fm
    from tools.aipos_cli.task_loader import enable_truth_cache, truth_cache_stats

    home, board = f248["home"], f248["board"]
    c4 = _item(home, "owner_check", "C4")
    monkeypatch.setattr(fm, "_FILE_CACHE_RACY_WINDOW_NS", 0)
    enable_truth_cache(True)
    try:
        board.page("/owner/home")  # 冷
        started = time.perf_counter()
        hot = board.page("/owner/home")
        elapsed = time.perf_counter() - started
        stats = truth_cache_stats()
        _show(f"[性能·首页热态] {elapsed:.3f}s; 缓存 {stats}")
        assert elapsed <= 0.5 and stats["hits"] >= 1
        assert "<small>推荐</small>符合" not in hot
        rc, out, err = _cli("owner", "recommend", "--item", c4["item_id"], "--choice", "owner_confirm", "--reason", REASON,
                            "--home-root", str(home), "--confirm")
        assert rc == 0, out + err
        fresh = board.page("/owner/home")
        _show(f"[新鲜度·写推荐后下一次请求] 推荐按钮出现={'<small>推荐</small>符合' in fresh}; 缓存 {truth_cache_stats()}")
        assert "<small>推荐</small>符合" in fresh and REASON in fresh
    finally:
        enable_truth_cache(False)

