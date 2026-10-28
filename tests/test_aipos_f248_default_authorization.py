"""AIPOS-F248 件① 产品缺省(项目授权将尽): 收件箱「项目授权将尽」事项的缺省推荐 = 按原范围扩大(总卡数 = 已用 + 原总卡数, 到期 = 现在 +
原有效期天数, 天数封顶 renew_days_cap), 看板一键的请求体经写例外①同一实现(envelope_board.handle_mint_request mode authorization)预演通过。

靶场 = AIPOS-F224 项目授权靶场(F134 双顾问 + F90 loop 靶场, 进程内 Owner 门; 禁第二份), 与 F232 同一造法跑满 4/5 张触发告知。
与 tests/test_aipos_f248_needs_you_one_click.py 分文件: 两处靶场夹具同名 rig(F90 / F175), 一个模块只能取一个。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f224_project_authorization as f224  # noqa: E402  — 项目授权构件(禁第二份)
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具
from test_aipos_f134_multi_advisor_lane_envelope import two  # noqa: E402,F401  — pytest 夹具
from test_aipos_f224_project_authorization import owner  # noqa: E402,F401  — pytest 夹具


def _show(text: str) -> None:
    print(text, flush=True)


# ===========================================================================
# 件① 产品缺省: 项目授权将尽 = 按原范围扩大(总卡数 = 已用 + 原总卡数, 天数封顶), 请求体经写例外①同一实现预演通过
# ===========================================================================

def test_item1_default_authorization_extend_previews_through_same_mint(owner, tmp_path):  # noqa: F811
    from tools.aipos_cli import owner_inbox as oi
    from tools.aipos_cli.envelope_board import handle_mint_request, session_ref
    from tools.aipos_cli.owner_recommend import recommend_declaration
    from web.board.auth_otc import PreviewTokenStore

    o = owner
    rc, out = f224._sign_two_lanes(o, max_tasks=5)
    assert rc == 0, out
    cards = ("PROBE-F248-A1", "PROBE-F248-A2", "PROBE-F248-A3", "PROBE-F248-A4")
    for card in cards:
        f224._card(o.gov, card, "ota")
    for card in cards:
        _res, text = f224._loop(o, card, f224.ADV_OTA)
        assert "run claim @" in text, text
    inbox = oi.collect_project_inbox(o.gov, home_root=o.gov.parent)
    warn = [i for i in inbox["notices"] if i["category"] == "envelope_warning"]
    assert len(warn) == 1
    rec = warn[0]["recommended"]
    _show(f"[件①·项目授权将尽缺省推荐] {json.dumps(rec, ensure_ascii=False)}")
    assert rec["source"] == "default" and rec["action"] == "extend_authorization" and rec["max_tasks"] == 5
    assert rec["params"]["mode"] == "authorization" and rec["params"]["action"] == "extend" and rec["params"]["max_tasks"] == 4 + 5
    assert rec["days"] is None and "expires_at" not in rec["params"] and "续到 2999-01-01" in rec["reason"], \
        "原授权到 2999 年 = 不动到期(扩大不缩短), 只加张数"
    assert recommend_declaration()["renew_days_cap"] >= 1
    session = {"role": "owner", "token_ref": "owner-fixture", "session_ref": session_ref("f248-probe-session"), "login_method": "otc",
               "source_ip": "127.0.0.1"}
    status, pre = handle_mint_request({**rec["params"], "stage": "preview"}, session=session, home_root=o.gov.parent,
                                      preview_store=PreviewTokenStore(ttl_seconds=300), connection_json=None,
                                      audit_log_path=tmp_path / "audit.jsonl", content_type="application/json")
    _show("---- 件①·扩大授权按钮请求体经写例外①预演原文 ----\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and pre["ok"] and pre["dry_run"]["verdict"] == "PASS"
