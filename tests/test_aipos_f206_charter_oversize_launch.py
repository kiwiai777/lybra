"""AIPOS-F206 件③ — 工位章程超体积上限: sync 判 oversize 需重渲染; loop 拉起前核章程即停(charter_sync_failed, 不拉起必爆上下文的会话)。

靶场 = AIPOS-F95 拉起靶场 lrig(F90 tmp 治理根 + 进程内门 + tmp 工位 + 假 harness; 工位章程经假门 RepoGate 由产品 sync 渲染),
与 AIPOS-F164 章程新鲜度用例同一靶场 / 同一 loop 入口; 上限经 monkeypatch charter_render.charter_size_guard 调小模拟「工位旧章程超限」。
不读写任何真实治理根 / 真实工位 / 真门, 不起真实 pi。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f164_charter_freshness as f164  # noqa: E402  — 拉起前章程核用例同一靶场入口
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — 拉起靶场(pytest fixture)


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _small_guard(monkeypatch, max_bytes: int) -> None:
    from tools.aipos_cli import charter_render

    real = charter_render.charter_size_guard()
    monkeypatch.setattr(charter_render, "charter_size_guard", lambda: {**real, "max_bytes": max_bytes})


def test_item3_loop_refuses_launch_when_charter_oversize(lrig, monkeypatch):
    from tools.aipos_cli import distribution_sync as ds

    f164._start_card(lrig)
    agents = lrig.ws_exec / "AGENTS.md"
    before = hashlib.md5(agents.read_bytes()).hexdigest()
    size = agents.stat().st_size
    _small_guard(monkeypatch, 2048)
    fresh = ds.charter_freshness(lrig.ws_exec, lrig.gov)
    _show(f"[③ 工位旧章程 {size} 字节 > 上限 2048] charter_freshness: {fresh['state']}: {fresh['detail']}")
    assert fresh["fresh"] is False and "oversize(工位章程 " in fresh["detail"] and "> 上限 2048 字节" in fresh["detail"]
    res, text = f164._loop(lrig)
    _show("---- ③ loop 拉起前章程超限: 输出原文(章程行)----\n" + "\n".join(f164._charter_lines(text)) + f"\n结束: {res.reason}: {res.message}")
    assert res.reason == "charter_sync_failed", text
    assert not [s.launch for s in res.steps if s.launch and s.launch.get("launched")], "超限 = 不拉起"
    assert "CHARTER_OVERSIZE" in res.message and "> 上限 2048 字节" in res.message
    assert hashlib.md5(agents.read_bytes()).hexdigest() == before, "拒渲染 = 不落半成品"


def test_item3_fresh_charter_within_limit_not_flagged(lrig):
    from tools.aipos_cli import distribution_sync as ds

    f164._start_card(lrig)
    fresh = ds.charter_freshness(lrig.ws_audit, lrig.gov)
    assert fresh["fresh"] is True and "oversize" not in fresh["detail"], fresh
