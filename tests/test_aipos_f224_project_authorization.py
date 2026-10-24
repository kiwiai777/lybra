"""AIPOS-F224 — 项目常设授权替代逐卡信封(设计稿 governance/research/2026-10-24-owner-intervention-tiers-design.md §4 / §8)。

件① 授权记录(声明 verbs.schema lybra_loop.envelope.project_authorization; 落 project.json paths.authorizations_root, 只追加修订)+ 覆盖判定唯一实现:
    门 _match_claim_envelope / _match_driver_envelope 与 loop find_envelope → select_envelope 都经 autonomy_policy.match_claim_envelope, 遇授权分派到
    judge_project_authorization; 优先级 = 授权先、未迁移旧信封兼容(select_envelope)。额度 = 根业务卡(审计 / 返工派生不扣, 沿用信封规则)。
件② 越界(车道 / 任务类型 / 主机 / 禁区 / 总额 / 到期)= loop owner_needed(结束原因 authorization_out_of_bound, 点名维度 + 一次扩大授权命令),
    收件箱事项选项 = 扩大授权 / 不批准; 告知(80% / 到期前 N 天)不阻塞。
件③ 迁移(draft --from-envelopes 预演 → 签发后旧信封 superseded, 文件不改不删)+ 验证根声明授权内免 owner-ref / 授权外拒。

靶场一律临时根: F90 / F134 进程内真门动词 + 临时治理根 / 临时产品仓 / 临时 HOME; Owner 签发经门 lybra_owner_decision_record_dry_run →
_confirm 真实处理器(Owner 能力在进程内, 假 token, 只出指纹)。禁真门 / 真治理根 / 真工位 / pi。人肉期样本在临时根自造。
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 全链靶场唯一来源
import test_aipos_f134_multi_advisor_lane_envelope as f134  # noqa: E402  — 双顾问双仓靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具
from test_aipos_f134_multi_advisor_lane_envelope import ADV_MAIN, ADV_OTA, two  # noqa: E402,F401  — pytest 夹具
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import autonomy_policy as ap  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402

AUTH = "auth_lybra"  # 声明 id_template auth_{project}; 靶场 project.json project = lybra
OWNER_TOKEN = "fixture-owner-token-f224"
OWNER_CAP = {"role": "owner", "role_class": "owner", "token_ref": "owner-fixture-f224", "expires_at": "2999-01-01T00:00:00Z",
             "scopes": ["owner_confirm"]}
HOST = "test"  # 靶场执行实例 exec.lybra.test 的 host 段(roles.schema naming.template)


def _show(text: str) -> None:
    print(text, flush=True)


def _quiet(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.startswith("[ENVELOPE"))


class OwnerGate:
    """GateClient 进程内替身(Owner 凭据): owner_decision_record 两跳走门侧真实处理器(tools.mcp_server.tools), 能力 = Owner(owner_confirm)。"""

    calls: list[str] = []

    def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
        assert token == OWNER_TOKEN  # token 只在进程内, 永不上屏

    def initialize(self) -> dict:
        return {}

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        from tools.mcp_server import tools as gate

        OwnerGate.calls.append(name)
        with gate.request_capability_scope(OWNER_CAP):
            res = getattr(gate, name)(dict(arguments))
        return res["structuredContent"] if isinstance(res, dict) and isinstance(res.get("structuredContent"), dict) else res


@pytest.fixture
def owner(two, monkeypatch: pytest.MonkeyPatch):
    """F134 双顾问靶场 + 第三仓 gamma(授权外车道)+ Owner 凭据(仓外假 token)+ 进程内 Owner 门。删掉两张旧信封: 授权内认领不需要任何信封。"""
    import tools.aipos_cli.confirm_client as confirm_client

    gamma = f78._init_product_repo(two.gov.parent / "product-gamma")
    decl = json.loads((two.gov / "project.json").read_text(encoding="utf-8"))
    decl["repos"]["items"]["gamma"] = str(gamma)
    _write(two.gov / "project.json", json.dumps(decl))
    conn = two.gov.parent / "owner-vault" / "connection.json"
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                             "tokens": [{"role": "owner", "token": OWNER_TOKEN, "scopes": ["owner_confirm"]}]}))
    monkeypatch.setattr(confirm_client, "GateClient", OwnerGate)
    OwnerGate.calls = []
    for pid in (f134.POL_MAIN, f134.POL_OTA):
        (two.gov / "5_tasks" / "policies" / f"{pid}.md").unlink()
    init_governance_repo(two.gov)
    return two.__class__(**{**vars(two), "owner_conn": str(conn), "gamma": gamma})


def _auth_cli(o, sub: str, *args: str, confirm: bool = True) -> tuple[int, str]:
    argv = ["project", "grant", sub, "--workspace-root", str(o.gov), *args]
    if confirm:
        argv += ["--confirm", "--connection-json", o.owner_conn]
    rc, out, err = _cli(argv)
    return rc, _quiet(out + err)


def _sign_two_lanes(o, *, max_tasks: int = 4, expires: str = "2999-01-01T00:00:00Z") -> tuple[int, str]:
    return _auth_cli(o, "sign", "--grant-lane", f"ota:code,validation:{ADV_OTA}", "--grant-lane", f"lantu:code,validation:{ADV_MAIN}",
                     "--host", HOST, "--max-tasks", str(max_tasks), "--expires-at", expires)


def _card(gov: Path, task_id: str, repo: str, *, task_mode: str = "code", **extra) -> None:
    f90._card(gov, task_id, "pending", lane={"repo": repo, "paths": ["tests/"], "roles": ["executor"]}, task_mode=task_mode, **extra)


def _loop(o, task_id: str, actor: str, watch=None, **kw):
    out = io.StringIO()
    res = run_loop(task_id, o.gov, actor=actor, out=out, watch=watch or f134._stop_watch, interval=0.05, max_wait=kw.pop("max_wait", 1),
                   max_steps=kw.pop("max_steps", 6), **kw)
    return res, _quiet(out.getvalue())


def _claim_refs(gov: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in sorted((gov / "5_tasks" / "records" / "claims").rglob("*.md")):
        fm = nr._read_frontmatter(p)
        out.setdefault(str(fm.get("task_id")), []).append(f"{fm.get('autonomy_mode')}:{fm.get('owner_policy_ref')}")
    return out


def _usage(gov: Path) -> str:
    u = ap.envelope_usage(gov, AUTH)
    return f"{AUTH}: state={u['state']} used={u['used']}/{u['max_tasks']} released_cards={u['released_cards']} claim_records={u['claim_records']}"


# ===========================================================================
# 验收① 签一份授权(两车道 × validation + code × 本机主机 × 期限 + 总卡数)→ 两位驱动方范围内连续认领多张卡, 无任何信封; 总卡数正确扣减
# (审计卡认领归根卡不扣)
# ===========================================================================

def test_item1_one_authorization_two_drivers_claim_many_cards_without_envelopes(owner):
    o = owner
    rc, out = _sign_two_lanes(o)
    _show(f"---- ① Owner 签发项目授权(经门 owner_decision_record 两跳)原文 exit {rc} ----\n{out}")
    assert rc == 0, out
    assert OwnerGate.calls == ["lybra_owner_decision_record_dry_run", "lybra_owner_decision_record_confirm"]
    rev = o.gov / "5_tasks" / "authorizations" / AUTH / "r0001.md"
    assert rev.is_file() and f"wrote 5_tasks/authorizations/{AUTH}/r0001.md" in out and "落账: verdict=PASS" in out
    decision = o.gov / "5_tasks" / "records" / "owner_decisions" / f"grant-{AUTH}-r0001.md"
    assert decision.is_file(), sorted(p.name for p in (o.gov / "5_tasks" / "records" / "owner_decisions").glob("*.md"))
    _show("---- ① 授权修订 r0001 frontmatter ----\n" + rev.read_text(encoding="utf-8").split("\n---\n")[0])
    assert list((o.gov / "5_tasks" / "policies").glob("*.md")) == [], "靶场无任何信封"

    # 驱动方 OTA: 全链(认领 → 交回 → 派审 → 审计认领 → 裁决 → 合入 → 结案), 审计卡认领归根卡不扣
    _card(o.gov, f134.OTA_CARD, "ota")
    seen: list[str] = []
    res, text = _loop(o, f134.OTA_CARD, ADV_OTA, watch=f90._agent_watch(o, seen), max_wait=5, max_steps=30)
    _show(f"---- ① OTA 顾问全链 loop 原文 exit {res.exit_code} outcome={res.outcome} ----\n{text}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert f"envelope={AUTH} driver={ADV_OTA}" in text and f"挑中项目授权 {AUTH}(r1; 项目授权优先于旧信封)" in text
    after_chain = _usage(o.gov)
    # 两位驱动方各在自己车道再认领(认领后等待即超时 = 声明退出码 3; 认领已落)
    _card(o.gov, "PROBE-OTA-2", "ota")
    _card(o.gov, "PROBE-LT-1", "lantu")
    _card(o.gov, "PROBE-LT-2", "lantu", task_mode="validation", validation_source_task="PROBE-LT-1",
          validation_candidate=f78._git(o.lantu, "rev-parse", "HEAD"))
    lines = [f"全链后 {after_chain}"]
    for card, actor in (("PROBE-OTA-2", ADV_OTA), ("PROBE-LT-1", ADV_MAIN), ("PROBE-LT-2", ADV_MAIN)):
        res, text = _loop(o, card, actor)
        _show(f"---- ① {actor} 推 {card} loop 原文 exit {res.exit_code} ----\n{text}")
        assert f"run claim @" in text and f"envelope={AUTH} driver={actor}" in text, text
        lines.append(f"{card}({actor}) 认领后 {_usage(o.gov)}")
    refs = _claim_refs(o.gov)
    _show("---- ① 用量全程(单位 = 根业务卡)----\n" + "\n".join(lines))
    _show("[① 认领记录 autonomy_mode:owner_policy_ref] " + json.dumps(refs, ensure_ascii=False))
    assert all(v == [f"PreAuthorized:{AUTH}"] for v in refs.values()) and f"{f134.OTA_CARD}R" in refs  # 审计卡也经授权认领
    usage = ap.envelope_usage(o.gov, AUTH)
    assert "used=1/4" in lines[0] and usage["used"] == 4 and usage["claim_records"] == 5 and usage["state"] == "exhausted"
    assert usage["released_cards"] == sorted([f134.OTA_CARD, "PROBE-OTA-2", "PROBE-LT-1", "PROBE-LT-2"])
    assert list((o.gov / "5_tasks" / "policies").glob("*.md")) == []


# ===========================================================================
# 验收② 越界四例(新车道 / 新任务类型 / 授权外主机 / 总额用尽)+ 驱动方 / 禁区 / 到期: owner_needed 原文与收件箱事项; 一次扩大授权后续跑通过
# ===========================================================================

def _expect_out_of_bound(o, card: str, actor: str, label: str) -> str:
    res, text = _loop(o, card, actor)
    _show(f"---- ② {card}({actor})越界原文 exit {res.exit_code} reason={res.reason} ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "no_envelope") and res.reason == "authorization_out_of_bound", text
    assert f"项目授权 {AUTH}(修订 r" in res.message and f"越界 —— {label}" in res.message, res.message
    assert "lybra project grant" in res.suggested_action and "--confirm" in res.suggested_action
    assert not (o.gov / "5_tasks" / "records" / "claims" / card).exists(), "越界未推进任何步"
    status = _cli(["loop", "status", "--task-id", card, "--workspace-root", str(o.gov), "--json"])
    na = json.loads(status[1])["next_action"]
    _show(f"[② {card} loop status 下一动作] {json.dumps(na, ensure_ascii=False)}")
    assert na["action"] == "owner_needed" and na["reason"] == "authorization_out_of_bound" and label in na["detail"]
    return res.suggested_action


def test_item2_out_of_bound_cases_owner_needed_inbox_and_one_extension(owner):
    from tools.aipos_cli.owner_inbox import collect_project_inbox, decide, grant_line

    o = owner
    rc, out = _sign_two_lanes(o, max_tasks=2)
    assert rc == 0, out
    _card(o.gov, "PROBE-GAMMA-1", "gamma")  # 新车道
    _card(o.gov, "PROBE-DOCS-1", "ota", task_mode="docs")  # 新任务类型
    _card(o.gov, "PROBE-HOST-1", "ota", assigned_to="exec.lybra.otherhost", agent_instance="exec.lybra.otherhost")  # 授权外主机
    _card(o.gov, "PROBE-ZONE-1", "ota", authorization_zones=["production"])  # 禁区(缺省全禁)
    _card(o.gov, "PROBE-DRV-1", "lantu")  # OTA 顾问推 lantu 卡 = 驱动方不在本车道
    commands = {
        "lane": _expect_out_of_bound(o, "PROBE-GAMMA-1", ADV_OTA, "新车道"),
        "task_mode": _expect_out_of_bound(o, "PROBE-DOCS-1", ADV_OTA, "新任务类型"),
        "host": _expect_out_of_bound(o, "PROBE-HOST-1", ADV_OTA, "授权外主机"),
        "zone": _expect_out_of_bound(o, "PROBE-ZONE-1", ADV_OTA, "禁区"),
        "driver": _expect_out_of_bound(o, "PROBE-DRV-1", ADV_OTA, "驱动方不在本车道授权内"),
    }
    assert "--grant-lane gamma --task-mode code --driver " + ADV_OTA in commands["lane"]
    assert "--grant-lane ota --task-mode docs" in commands["task_mode"] and "--host otherhost" in commands["host"]
    assert "--open-zone production" in commands["zone"] and f"--grant-lane lantu --task-mode code --driver {ADV_OTA}" in commands["driver"]
    # 总额: 两张范围内卡用掉 2/2, 第三张新卡 = 总额用尽
    for card in ("PROBE-Q-1", "PROBE-Q-2", "PROBE-Q-3"):
        _card(o.gov, card, "ota")
    for card in ("PROBE-Q-1", "PROBE-Q-2"):
        res, text = _loop(o, card, ADV_OTA)
        assert "run claim @" in text, text
    commands["quota"] = _expect_out_of_bound(o, "PROBE-Q-3", ADV_OTA, "总额用尽")
    assert "--max-tasks 3" in commands["quota"]

    # 收件箱: 每例一项 loop_owner_needed, 事由 authorization_out_of_bound, 选项 = 扩大授权 / 不批准 / 暂停, 建议 = 扩大项目授权
    inbox = collect_project_inbox(o.gov, home_root=o.gov.parent)
    items = {i["task_id"]: i for i in inbox["items"] if i["category"] == "loop_owner_needed"}
    for card in ("PROBE-GAMMA-1", "PROBE-DOCS-1", "PROBE-HOST-1", "PROBE-ZONE-1", "PROBE-DRV-1", "PROBE-Q-3"):
        item = items[card]
        _show(f"[② 收件箱事项 {card}] {item['item_id']} options={[x['key'] for x in item['options']]}\n{grant_line(item)}")
        assert [x["key"] for x in item["options"]] == ["extend_authorization", "deny_grant", "pause"]
        assert item["grant"]["kind"] == "authorization" and item["grant"]["authorization_id"] == AUTH
    # 一次裁决扩大授权(总额): Owner 收件箱选「扩大项目授权」= 决策记录 + 授权新修订 r0002 同一写路径落盘, 再续跑通过
    q_item = items["PROBE-Q-3"]["item_id"]
    report = decide(q_item, "extend_authorization", home_root=o.gov.parent, relayed_by=ADV_OTA, owner_words="扩到 3 张", confirm=True)
    _show(f"---- ② owner decide extend_authorization 原文 ----\n{json.dumps({k: report[k] for k in ('ok', 'stage', 'written', 'rc_outcome')}, ensure_ascii=False)}")
    assert report["ok"] and any(p.endswith(f"authorizations/{AUTH}/r0002.md") for p in report["written"]), report
    assert ap.load_authorization(o.gov, AUTH)["max_tasks"] == 3
    res, text = _loop(o, "PROBE-Q-3", ADV_OTA)
    _show(f"---- ② 扩大后续跑原文 exit {res.exit_code} ----\n{text}")
    assert "run claim @" in text and f"envelope={AUTH}" in text, text
    # 一次扩大(CLI)加新车道后续跑通过
    rc, out = _auth_cli(o, "extend", "--grant-lane", f"gamma:code:{ADV_OTA}", "--max-tasks", "4")
    _show(f"---- ② CLI 扩大授权(新车道 gamma)原文 exit {rc} ----\n{out}")
    assert rc == 0 and f"wrote 5_tasks/authorizations/{AUTH}/r0003.md" in out, out
    res, text = _loop(o, "PROBE-GAMMA-1", ADV_OTA)
    assert "run claim @" in text and f"envelope={AUTH}" in text, text
    hist = [(r["revision"], r["action"], r["max_tasks"]) for r in ap.authorization_history(o.gov, AUTH)]
    _show(f"[② 修订史(只追加)] {hist}")
    assert hist == [(1, "grant", 2), (2, "extend", 3), (3, "extend", 4)]


def test_item2_expiry_out_of_bound_and_extension_by_delay(owner):
    """到期: 签发后过了到期时刻(loop 以 now 注入未来时刻)= 越界(到期), 扩大 = 延期; 显式 --envelope 指授权 + 门拒后出口(_envelope_ending
    → authorization_ending)同一判定, 不给信封续签。"""
    from datetime import timedelta

    from tools.aipos_cli.clock import iso_z, utc_now
    from tools.aipos_cli.loop_driver import _envelope_ending

    o = owner
    soon = utc_now() + timedelta(days=2)
    rc, out = _sign_two_lanes(o, expires=iso_z(soon.replace(microsecond=0)))
    assert rc == 0, out
    _card(o.gov, "PROBE-EXP-1", "ota")
    later = soon + timedelta(days=1)
    res, text = _loop(o, "PROBE-EXP-1", ADV_OTA, now=later)
    _show(f"---- ② 到期越界原文 exit {res.exit_code} reason={res.reason} ----\n{text}")
    assert res.reason == "authorization_out_of_bound" and "越界 —— 到期" in res.message and "--expires-at " in res.suggested_action
    ending = _envelope_ending(o.gov, AUTH, task_id="PROBE-EXP-1", task_fm=nr._read_frontmatter(
        o.gov / "5_tasks" / "queue" / "pending" / "probe-exp-1.md"), driver_actor=ADV_OTA, driver_role="advisor",
        what="return 步(PROBE-EXP-1)被拒, 未执行", now=later)
    _show(f"[② 门拒后出口(显式授权)] reason={ending['reason']} message={ending['message'].splitlines()[0]}")
    assert ending["reason"] == "authorization_out_of_bound" and "到期" in ending["message"] and "return 步" in ending["message"]
    assert "envelope mint" not in ending["message"]


# ===========================================================================
# 验收③ 80% 告知不阻塞
# ===========================================================================

def test_item3_eighty_percent_notice_does_not_block(owner):
    o = owner
    rc, out = _sign_two_lanes(o, max_tasks=5)
    assert rc == 0, out
    for card in ("PROBE-N-1", "PROBE-N-2", "PROBE-N-3", "PROBE-N-4"):
        _card(o.gov, card, "ota")
    texts = []
    for card in ("PROBE-N-1", "PROBE-N-2", "PROBE-N-3", "PROBE-N-4"):
        res, text = _loop(o, card, ADV_OTA)
        texts.append(text)
        assert "run claim @" in text, text
    notices = ap.authorization_notices(o.gov, AUTH)
    _show(f"---- ③ 用到 4/5 = 80% 后告知 ----\n{notices}\n第 4 张 loop 原文(启动行带上一张后的告知):\n{texts[-1]}")
    assert notices == ["已用 4/5 张业务卡(≥80%)"]
    from tools.aipos_cli.owner_inbox import collect_project_inbox

    inbox = collect_project_inbox(o.gov, home_root=o.gov.parent)
    warn = [i for i in inbox["items"] if i["category"] == "envelope_warning"]
    _show(f"[③ 收件箱告知项(envelope_warning 类, 不阻塞)] {json.dumps([{k: i.get(k) for k in ('subject', 'ask', 'recommend')} for i in warn], ensure_ascii=False)}")
    assert len(warn) == 1 and warn[0]["policy_id"] == AUTH and "已用 4/5 张业务卡(≥80%)" in warn[0]["ask"]
    assert "lybra project grant extend" in warn[0]["recommend"] and "--max-tasks 8" in warn[0]["recommend"]
    _card(o.gov, "PROBE-N-5", "ota")
    res, text = _loop(o, "PROBE-N-5", ADV_OTA)
    _show(f"---- ③ 告知中继续推第 5 张(不阻塞)原文 exit {res.exit_code} ----\n{text}")
    assert f"项目授权 {AUTH} 告知: 已用 4/5 张业务卡(≥80%)(不阻塞)" in text and "run claim @" in text


# ===========================================================================
# 验收④ 迁移: 靶场 3 个旧信封 → 草案预演原文 → 确认后旧信封 superseded(文件不改不删)、loop 按授权覆盖; 只有旧信封的项目行为不变(回归)
# ===========================================================================

def _envelope(gov: Path, pid: str, agent: str, *, max_tasks: int, task_mode: str = "code", lane: str | None = None,
              expires_at: str = "2999-01-01T00:00:00Z", launch: list[str] | None = None) -> None:
    _write(gov / "5_tasks" / "policies" / f"{pid}.md", ap.build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=agent, active_from="2026-01-01T00:00:00Z", expires_at=expires_at, max_tasks=max_tasks,
        owner_approval_ref=f"fixture-{pid}", task_selector_task_mode=task_mode, task_selector_lane_repo=lane, launch_harnesses=launch))


def _legacy_claim(gov: Path, task_id: str, pid: str) -> None:
    from test_aipos_f78_engine_agnostic import _fm

    _write(gov / "5_tasks" / "records" / "claims" / task_id / f"claim_{task_id}_legacy.md",
           _fm({"record_type": "claim_record", "task_id": task_id, "autonomy_mode": "PreAuthorized", "owner_policy_ref": pid,
                "claimed_at": "2026-10-01T00:00:00Z"}, "legacy\n"))


def _digest(gov: Path) -> dict[str, str]:
    import hashlib

    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((gov / "5_tasks" / "policies").glob("*.md"))}


def test_item4_migration_draft_preview_confirm_supersedes_and_loop_uses_authorization(owner):
    o = owner
    _envelope(o.gov, "pol_mig_ota_1", ADV_OTA, max_tasks=10, lane="ota", expires_at="2999-03-01T00:00:00Z", launch=["pi"])
    _envelope(o.gov, "pol_mig_lt_1", ADV_MAIN, max_tasks=5, lane="lantu", expires_at="2999-06-01T00:00:00Z")
    _envelope(o.gov, "pol_mig_val_1", ADV_MAIN, max_tasks=3, task_mode="validation", lane="lantu", expires_at="2999-02-01T00:00:00Z")
    f90._card(o.gov, "PROBE-MIG-1", "claimed", lane={"repo": "ota", "paths": ["tests/"], "roles": ["executor"]})
    _legacy_claim(o.gov, "PROBE-MIG-1", "pol_mig_ota_1")  # 在途卡: 旧信封已放行(迁移后由授权承接, 不扣授权额度)
    _legacy_claim(o.gov, "PROBE-MIG-0", "pol_mig_lt_1")
    before = _digest(o.gov)
    rc, out = _auth_cli(o, "draft", "--from-envelopes", confirm=False)
    _show(f"---- ④ draft --from-envelopes 预演原文 exit {rc} ----\n{out}")
    assert rc == 0 and "预演(零写入" in out and f"将写 5_tasks/authorizations/{AUTH}/r0001.md" in out
    assert not (o.gov / "5_tasks" / "authorizations").exists(), "预演零写入"
    assert "max_tasks: 16" in out  # 总卡数 = 在用信封剩余额度和 = (10-1) + (5-1) + (3-0)(用量按根业务卡, 各信封各自计)
    assert "expires_at: '2999-06-01T00:00:00Z'" in out  # 期限取最晚
    for pid in ("pol_mig_ota_1", "pol_mig_lt_1", "pol_mig_val_1"):
        assert f"- {pid}" in out
    rc, out = _auth_cli(o, "draft", "--from-envelopes")
    _show(f"---- ④ Owner 确认迁移草案(经门)原文 exit {rc} ----\n{out}")
    assert rc == 0 and f"wrote 5_tasks/authorizations/{AUTH}/r0001.md" in out, out
    assert _digest(o.gov) == before, "旧信封文件不改不删"
    rows = {r["policy_id"]: r for r in ap.envelope_overview(o.gov)["envelopes"]}
    _show("[④ 迁移后信封状态] " + json.dumps({k: (v["status"], v["superseded_by"], v["current"]) for k, v in rows.items()}, ensure_ascii=False))
    assert all(rows[p]["status"] == "superseded" and rows[p]["superseded_by"] == AUTH and not rows[p]["current"]
               for p in ("pol_mig_ota_1", "pol_mig_lt_1", "pol_mig_val_1"))
    auth = ap.load_authorization(o.gov, AUTH)
    assert {l["repo"]: sorted(l["drivers"]) for l in auth["lanes"]} == {"ota": [ADV_OTA], "lantu": [ADV_MAIN]}
    assert sorted(next(l for l in auth["lanes"] if l["repo"] == "lantu")["task_modes"]) == ["code", "validation"]
    assert auth["launch_harnesses"] == ["pi"] and HOST in auth["hosts"]
    from tools.aipos_cli.workstation_wiring import derive_effective_owner_policy_ref

    ref, why = derive_effective_owner_policy_ref(o.gov, role="executor", agent_instance=f78.EXEC)
    _show(f"[④ 工位接入推导 owner_policy_ref(执行工位在授权主机上)] {ref} ({why})")
    assert ref == AUTH  # 旧信封已 superseded, 工位推导改认项目授权(工位 host 段在授权允许主机内)
    q = ap.envelope_quota_state(o.gov, AUTH, "PROBE-MIG-1")
    _show(f"[④ 在途卡承接] {q} usage={_usage(o.gov)}")
    assert q["card_released"] and q["used"] == 0
    # loop 按授权覆盖(不挑旧信封); 显式 --envelope 指旧信封 = 改用迁入的授权
    _card(o.gov, "PROBE-MIG-2", "ota")
    res, text = _loop(o, "PROBE-MIG-2", ADV_OTA, policy_id="pol_mig_ota_1")
    _show(f"---- ④ 显式旧信封续跑原文 exit {res.exit_code} ----\n{text}")
    assert f"envelope={AUTH}" in text and "run claim @" in text and "--owner-policy-ref auth_lybra" in text
    assert "pol_mig_ota_1 已被同驱动方更新信封 auth_lybra 接替" in text
    # 门: 直接拿旧信封认领 = 拒(superseded), 回落 Supervised 不放行
    _card(o.gov, "PROBE-MIG-3", "ota")
    rc, cli_out, cli_err = _cli(["queue", "claim", "--task-id", "PROBE-MIG-3", "--actor", f78.EXEC, "--confirm", "--connection-json", o.conn,
                                 "--agent-instance", f78.EXEC, "--autonomy-mode", "PreAuthorized", "--owner-policy-ref", "pol_mig_lt_1"])
    text = _quiet(cli_out + cli_err)
    _show(f"---- ④ 门侧直接用已迁移旧信封认领原文 exit {rc} ----\n{text}")
    assert rc != 0 and not (o.gov / "5_tasks" / "records" / "claims" / "PROBE-MIG-3").exists()


def test_item4_regression_envelope_only_project_unchanged(two):
    """只有旧信封、没有任何项目授权的项目: 挑选 / 认领 / 信封页状态与原规则一致(授权目录不存在, 判定零额外分支)。"""
    o = two
    f134._lane_card(o.gov, f134.LT_CARD, "lantu")
    init_governance_repo(o.gov)
    assert ap.authorization_ids(o.gov) == [] and not (o.gov / "5_tasks" / "authorizations").exists()
    res, text = _loop(o, f134.LT_CARD, ADV_MAIN)
    _show(f"---- ④ 回归: 只有旧信封的项目 loop 原文 exit {res.exit_code} ----\n{text}")
    assert f"envelope={f134.POL_MAIN} driver={ADV_MAIN}" in text and "run claim @" in text and "项目授权" not in text
    rows = ap.envelope_overview(o.gov)["envelopes"]
    assert {r["policy_id"]: r["status"] for r in rows} == {f134.POL_MAIN: "active", f134.POL_OTA: "active"}
    policy, reasons = ap.select_envelope(o.gov, identities=[(ADV_OTA, ADV_OTA, "advisor")],
                                         task={"task_id": f134.LT_CARD, "task_mode": "code", "project": "lybra",
                                               "lane": {"repo": "lantu"}})
    _show(f"[④ 回归: OTA 信封不覆盖 lantu 卡] {reasons}")
    assert policy is None and any("ENVELOPE" in r or "lane" in r for r in reasons)


# ===========================================================================
# 验收⑤ 验证根声明: 主机在授权内免 owner-ref(自动引用授权记录)/ 授权外拒并指向扩大授权; 项目无授权 = 照旧须 --owner-ref
# ===========================================================================

def test_item5_validation_root_owner_ref_from_authorization(owner, tmp_path):
    from tools.aipos_cli import validation_root as vr

    from test_aipos_f187_kickoff_worktree_cleanup import _land_subagent  # 接入 land 事件唯一靶场构件

    o = owner
    _land_subagent(o.gov, f78.EXEC, project="lybra", harness="claude-code", host=vr.local_host_label())
    _card(o.gov, "PROBE-VR-1", "ota")
    base = str(tmp_path / "vr-base")
    kw = dict(host=vr.local_host_label(), base=base, task_id="PROBE-VR-1", instance=f78.EXEC)
    # 无授权: 缺 --owner-ref = 照旧拒(行为不变)
    with pytest.raises(vr.ValidationRootError) as exc:
        vr.apply_validation_root_change(o.gov, "declare", dry_run=True, actor=ADV_OTA, **kw)
    _show(f"[⑤ 无授权缺 --owner-ref] {exc.value.code}: {exc.value.problems}")
    rc, out = _auth_cli(o, "sign", "--grant-lane", f"ota:code:{ADV_OTA}", "--host", HOST, "--host", vr.local_host_label(), "--max-tasks", "3",
                        "--expires-at", "2999-01-01T00:00:00Z")
    assert rc == 0, out
    try:
        got = vr.apply_validation_root_change(o.gov, "declare", dry_run=True, actor=ADV_OTA, **kw)
    except vr.ValidationRootError as err:  # 实例未接入等与本卡无关的既有拒因 = 原文出声
        pytest.fail(f"{err.code}: {err.problems}")
    _show(f"---- ⑤ 授权内主机声明预演(无 --owner-ref)----\nowner_ref={got['entry']['owner_ref']}\n" + "\n".join(got["notes"]))
    assert got["entry"]["owner_ref"] == f"{AUTH}@r1" and any("免逐次 Owner 引用" in n for n in got["notes"])
    with pytest.raises(vr.ValidationRootError) as exc:
        vr.apply_validation_root_change(o.gov, "set-host", dry_run=True, actor=ADV_OTA, host="kiwiai-mac-out", ssh_alias="kiwiai-mac-out",
                                        owner_ref="Owner 口头同意")
    _show(f"---- ⑤ 授权外主机(带 --owner-ref 也拒)原文 ----\n{exc.value.code}: " + "\n".join(exc.value.problems))
    assert exc.value.code == "VR_HOST_NOT_AUTHORIZED" and "lybra project grant extend" in "\n".join(exc.value.problems)
    assert "--host kiwiai-mac-out" in "\n".join(exc.value.problems)


# ===========================================================================
# 单一实现 / 声明守卫
# ===========================================================================

def test_single_judgment_and_declaration_guards():
    """覆盖判定只有一处(judge_project_authorization 只由 match_claim_envelope / judge_authorizations 调用); 声明 fail-closed;
    越界维度拒因码都在 transitions envelope_guards; 结束原因已声明且 loop status 判 owner_needed。"""
    import ast

    decl = ap.project_authorization_declaration()
    assert set(decl["dimension_order"]) == set(decl["dimensions"])
    assert {d["error_code"] for d in decl["dimensions"].values()} <= set(ap.envelope_guard_codes())
    contract = load_loop_contract()
    assert decl["end_reason"] in contract["run_record"]["end_reasons"]
    from tools.schema_loader import code_repo_schema_root, load_schema

    status = load_schema("verbs", code_repo_schema_root())["verbs"]["lybra_loop_status"]["next_action"]
    assert status["by_end_reason"][decl["end_reason"]] == "owner_needed" and decl["end_reason"] in status["owner_reasons"]
    callers: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "tools").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and getattr(sub.func, "id", getattr(sub.func, "attr", "")) == "judge_project_authorization":
                        callers.setdefault(path.relative_to(REPO_ROOT).as_posix(), []).append(node.name)
    _show(f"[单一判定] judge_project_authorization 调用方: {callers}")
    assert callers == {"tools/aipos_cli/autonomy_policy.py": ["match_claim_envelope", "judge_authorizations", "judge_authorizations"]}
