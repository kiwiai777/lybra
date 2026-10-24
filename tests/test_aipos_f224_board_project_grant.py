"""AIPOS-F224 件③ — 看板信封页「项目授权」主视图 + 历史信封; Owner 在看板确认一次迁移草案生效(F172 同一写路由 mode = authorization,
同一安全框架 envelope_board.handle_owner_write), 扩大 / 撤销同路由; 非 Owner 会话只读无表单。

靶场 = F172 看板靶场(临时 home 根两个项目 + 进程内看板服务 + 门替身走门侧真实实现), 只在临时根写; 不连真门、不读真实凭据。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f172_board_envelopes as f172  # noqa: E402  — 看板靶场唯一来源
from test_aipos_f172_board_envelopes import P1, board, rig  # noqa: E402,F401  — pytest 夹具
from tools.aipos_cli import autonomy_policy as ap  # noqa: E402

AUTH = f"auth_{P1}"


def _show(text: str) -> None:
    print(text, flush=True)


def _code_repo(b) -> Path:
    """P1 声明代码仓(迁移草案按「不限 lane = 项目全部仓」推车道; project new 的靶场项目无仓)。"""
    repo = b["tmp"] / "p1-code"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    decl = json.loads((b["g1"] / "project.json").read_text(encoding="utf-8"))
    decl["code_repo"] = str(repo)
    (b["g1"] / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    return repo


def test_board_migrate_preview_confirm_then_main_view_and_history(board):
    b = board
    repo = _code_repo(b)
    status, page = f172._request(b, f"/owner/envelopes?tab=current&project={P1}")
    assert status == 200 and "authorization-migrate" in page and 'data-action="migrate"' in page, page[:2000]
    body = {"workspace": P1, "mode": "authorization", "action": "migrate"}
    status, pre = f172._mint(b, {**body, "stage": "preview"})
    _show(f"---- 看板迁移预演 status={status} ----\n" + "\n".join(pre.get("summary_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and pre["ok"] and pre["params"]["policy_id"] == AUTH
    assert any("迁入并退役的旧信封: " in line and "pol_one_loop_9" in line for line in pre["summary_lines"])
    assert not (b["g1"] / "5_tasks" / "authorizations").exists(), "预演零写入"
    status, done = f172._mint(b, {**body, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show(f"---- 看板确认迁移 status={status} ----\n{json.dumps({k: done.get(k) for k in ('ok', 'signed', 'landing_line', 'capture_surface')}, ensure_ascii=False)}")
    assert status == 200 and done["ok"] and done["signed"][0]["policy_id"] == AUTH and done["capture_surface"] == "board"
    rev = b["g1"] / "5_tasks" / "authorizations" / AUTH / "r0001.md"
    assert rev.is_file() and "落账: verdict=PASS" in done["landing_line"]
    auth = ap.load_authorization(b["g1"], AUTH)
    assert auth["lanes"][0]["repo"] == str(repo) and set(auth["supersedes_policies"]) >= {"pol_one_loop_9", "pol_one_side_1", "pol_one_audit_1"}
    decision = (b["g1"] / "5_tasks" / "records" / "owner_decisions" / f"grant-{AUTH}-r0001.md").read_text(encoding="utf-8")
    assert "capture_surface: board" in decision and "captured_by: board:owner:owner-fixture:session-" in decision
    # 主视图: 「项目授权」段在前; 迁入的旧信封全部落「历史」(superseded), 「在用」无信封
    status, page = f172._request(b, f"/owner/envelopes?tab=current&project={P1}")
    section = re.search(r'<div class="authorization-view" data-project="' + P1 + r'".*?</div>', page, re.S).group(0)
    _show(f"---- 「项目授权」主视图(节选)----\n{re.sub(r'<[^>]+>', ' ', section)[:900]}")
    assert f'id="{AUTH}"' in section and "authorization-extend" in section and "authorization-revoke" in section
    assert "authorization-migrate" not in section, "迁移后无在用信封可迁"
    status, hist = f172._request(b, f"/owner/envelopes?tab=history&project={P1}")
    for pid in ("pol_one_loop_9", "pol_one_side_1", "pol_one_audit_1"):
        row = f172._row(hist, pid)
        assert 'data-status="superseded"' in row and f"被 {AUTH} 接替" in row, row
    # 非 Owner 会话: 只读, 无表单
    status, ro = f172._request(b, f"/owner/envelopes?tab=current&project={P1}", who="advisor")
    assert status == 200 and f'id="{AUTH}"' in ro and 'data-mode="authorization"' not in ro
    # 扩大(同一路由): 加主机 + 总卡数
    ext = {"workspace": P1, "mode": "authorization", "action": "extend", "host": "kiwiai-mac", "max_tasks": "60"}
    status, pre = f172._mint(b, {**ext, "stage": "preview"})
    assert status == 200 and pre["ok"] and any("kiwiai-mac" in line for line in pre["summary_lines"]), pre
    status, done = f172._mint(b, {**ext, "stage": "confirm", "preview_token": pre["preview_token"]})
    assert status == 200 and done["ok"] and ap.load_authorization(b["g1"], AUTH)["max_tasks"] == 60
    log = [json.loads(ln) for ln in b["audit_log"].read_text(encoding="utf-8").splitlines()]
    _show("[看板审计日志] " + json.dumps([(r["action"], r.get("outcome")) for r in log], ensure_ascii=False))
    assert [r.get("outcome") for r in log if r["action"] == "authorization_sign"] == ["previewed", "confirming", "signed"] * 2


def test_board_authorization_write_guards(board):
    b = board
    status, res = f172._mint(b, {"workspace": P1, "mode": "authorization", "action": "extend", "host": "x", "stage": "preview"})
    _show(f"[无授权时扩大] {status} {res.get('error')}: {res.get('message')}")
    assert status == 400 and res["error"] == "PARAMS_INVALID" and "还没有项目授权" in res["message"]
    status, res = f172._mint(b, {"workspace": P1, "mode": "authorization", "action": "migrate", "stage": "preview"}, who="advisor")
    assert status == 403 and res["error"] == "OWNER_ONLY"
    status, res = f172._mint(b, {"workspace": P1, "mode": "authorization", "action": "bogus", "stage": "preview"})
    assert status == 400 and res["error"] == "PARAMS_INVALID"
