"""AIPOS-F202 — 审计连续 BLOCK 的正式恢复入口(Owner 收件箱「重新派审」)。

实撞(10-21 chris 理想 HBLX-21B): 审计 HBLX-21BR / HBLX-21BR2 连续 BLOCK(模拟器未绑定 SIM_NOT_BOUND、Mac 审计运行证据当时核不了)
→ owner_needed(audit_blocked_repeated); Owner 经收件箱定「等产品修复」(wait_product), F200 上线后顾问续跑 loop 仍 1 秒即
audit_blocked_repeated exit 4——连续 BLOCK 计数无视 Owner 裁决, 收件箱无「重新派审」, 产品无正式恢复入口。

件① 收件箱: audit_blocked_repeated 项选项含 reaudit(人话「环境 / 产品已修好, 重新派审」; 声明 pinned_options_by_reason = 顾问 loop note
    写了自定义选项也恒列); 之前选过「等修复」(wait_product)、之后部署提交已变 = 该卡新事项多一行提示「可选重新派审」。
件② loop: owner decide --choice reaudit --confirm(既有同一写路径, 自动落账)后原命令重跑 loop → 连续 BLOCK 以该裁决为界重新计数
    (唯一实现 next_resolver.audit_block_state, 声明 transitions nodes.N4.audit_blocked.owner_reaudit)→ 推导核可推导「派下一轮审计」
    (既有派审动词, R 序号续增)→ loop 执行并照常拉起新一轮审计; 新一轮再 BLOCK 1 次不升, 2 次再升 Owner。历史记录不改不删。
件③ loop status 对已有 reaudit 裁决的运行给 advisor_fix(owner_ruled), 下一条 = 原命令重跑 loop; 看板该项每个选项有转达话术(F201 relay)。

靶场 = F90 进程内门替身 + 临时治理根 / 产品仓(F179 BLOCK 夹具同一份), 部署提交经注入的 DeployGuard 模拟(两次运行代码不同 = 修复已上线)。
全程不碰真实治理根 / 真实门 / 真实 pi。
"""
from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  - loop rig (single source)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  - pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f179_long_run_resources_block import _block_watch, _quiet, _status  # noqa: E402  - F179 BLOCK rig (single source)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import owner_inbox as oi  # noqa: E402
from tools.aipos_cli.deploy_runtime import DeployGuard, RunningCode  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402

TASK, AUDIT = f90.TASK, f90.AUDIT
COMMIT_OLD = "309e7f1fef631bacf427e3401b06851db91b35ef"   # 「等修复」时的运行代码(照 HBLX-21B 形自造)
COMMIT_NEW = "30f70e7a38f391e63e45c4beb3195ff55f744d39"   # 修复上线后的运行代码


def _show(text: str) -> None:
    print(text, flush=True)


def _loop(rig, watch, commit: str):
    import io

    out = io.StringIO()
    guard = DeployGuard(RunningCode(REPO_ROOT, "worktree", None, commit, f"fixture-{commit[:7]}"))
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=watch, interval=0.05, max_wait=5, max_steps=40,
                   deploy_guard=guard)
    return res, out.getvalue()


def _home(rig) -> Path:
    return rig.gov.parent


def _inbox_item(rig) -> dict:
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(_home(rig)), "--project", rig.gov.name, "--json"])
    assert rc == 0, out + err
    items = [i for i in json.loads(out)["items"] if i.get("task_id") == TASK and i["category"] == "loop_owner_needed"]
    assert len(items) == 1, out
    return items[0]


def _inbox_text(rig) -> str:
    rc, out, err = _cli(["owner", "inbox", "--home-root", str(_home(rig)), "--project", rig.gov.name])
    assert rc == 0, out + err
    return out


def _decide(rig, item_id: str, choice: str, *, confirm: bool, note: str | None = None) -> tuple[int, str]:
    argv = ["owner", "decide", "--item", item_id, "--choice", choice, "--relayed-by", DRIVER,
            "--owner-words", f"Owner 定 {choice}", "--home-root", str(_home(rig))]
    if note:
        argv += ["--note", note]
    if confirm:
        argv.append("--confirm")
    rc, out, err = _cli(argv)
    return rc, out + err


def _history(gov: Path) -> dict[str, str]:
    """历史指纹: 裁决记录 / 审计卡 / 审计报告 / 派审记录 / 运行记录(当前已有的每个文件 → sha256)。"""
    roots = [gov / "5_tasks" / "records" / "audit_verdicts", gov / "5_tasks" / "records" / "audit_dispatches",
             gov / "5_tasks" / "records" / "loop_runs", gov / "task_cards", gov / "5_tasks" / "queue"]
    out: dict[str, str] = {}
    for root in roots:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if root.name == "queue" and f"{TASK}R".lower() not in path.name:
                continue  # 源卡卡面随认领 / 派审链接字段演进(门侧既有写), 历史指纹只取审计卡
            out[str(path.relative_to(gov))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _two_blocks(rig) -> str:
    """造人肉期样本: 首轮审计 BLOCK → 顾问按例行修法派 R2 → R2 再 BLOCK = owner_needed(audit_blocked_repeated)。"""
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = _loop(rig, _block_watch(rig, {AUDIT: "BLOCK"}), COMMIT_OLD)
    assert res.reason == "audit_blocked", text
    reaudit = next(s for s in _status(rig.gov, TASK)[1]["next_action"]["fix"] if s.startswith("修好后重新派审计下一轮: "))
    rc, out, err = _cli(shlex.split(reaudit.split(": ", 1)[1])[1:])
    assert rc == 0, out + err
    res, text = _loop(rig, _block_watch(rig, {f"{TASK}R2": "BLOCK"}), COMMIT_OLD)
    _show(f"---- ① 连续 2 次 BLOCK: lybra loop exit {res.exit_code} reason={res.reason} ----\n{_quiet(text)}")
    assert res.reason == "audit_blocked_repeated" and "连续 2 次" in text, text
    return text


def _no_auto_reaudit(monkeypatch) -> None:
    """AIPOS-F225: 连续 BLOCK 的常规出口改为第二档自动重新派审(部署 / 验证根有变化即自动); 本文件验的是 Owner「重新派审」手动兜底,
    故把自动判定钉为「判不出变化」(next_resolver.auto_reaudit_check 唯一判定, loop 与 loop status 同读)。"""
    real = nr.auto_reaudit_check

    def no_auto(*a, **k):
        return {**real(*a, **k), "eligible": False, "why": "fixture: 自动重新派审判不出变化(手动兜底路径)"}

    monkeypatch.setattr(nr, "auto_reaudit_check", no_auto)


def _escalate(rig) -> None:
    """AIPOS-F225: 第二档未自动放行 = 归顾问; 顾问修不动且确属第一档(改验收 / 换审计环境 = rescope)才带事由升 Owner。"""
    rc, out, err = _cli(["loop", "note", "--task-id", TASK, "--ask", "审计连续 BLOCK, 环境修不动: 请定修环境方案 / 改卡面验收",
                         "--owner-reason", "rescope", "--workspace-root", str(rig.gov), "--actor", DRIVER, "--confirm"])
    assert rc == 0, out + err


def test_reaudit_entry_end_to_end(rig, monkeypatch):
    _no_auto_reaudit(monkeypatch)
    _two_blocks(rig)
    status_text, status = _status(rig.gov, TASK)
    # AIPOS-F225: 连续 BLOCK = 第二档, 判不出变化 = advisor_fix(归顾问); 顾问带第一档事由升 Owner 后才进收件箱
    assert status["next_action"]["action"] == "advisor_fix" and status["next_action"]["reason"] == "audit_blocked_repeated"
    _escalate(rig)
    before = _history(rig.gov)
    assert any("audit_verdicts" in k for k in before) and any(f"{TASK}R2" in k for k in before)

    # 件①: 收件箱该项含「重新派审」, 顾问建议(声明)指向它
    item = _inbox_item(rig)
    keys = [o["key"] for o in item["options"]]
    _show("---- ① 收件箱 audit_blocked_repeated 项 ----\n" + "\n".join(oi.item_lines(item, oi.inbox_declaration()["texts"])))
    assert keys[0] == "reaudit" and "wait_product" in keys, keys
    assert dict((o["key"], o["label"]) for o in item["options"])["reaudit"] == "环境 / 产品已修好, 重新派审"
    assert "重新派审" in str(item.get("recommend")) and not item.get("reaudit_hint")

    # Owner 先定「等产品修复」→ 修复上线(运行代码已变)后续跑 loop: 仍 owner_needed(等修复不清零 = 修前行为)
    rc, out = _decide(rig, item["item_id"], "wait_product", confirm=True)
    assert rc == 0, out
    res, text = _loop(rig, _block_watch(rig, {}), COMMIT_NEW)
    _show(f"---- ① wait_product 裁决后、修复上线后续跑 loop(对照修前行为): exit {res.exit_code} reason={res.reason} ----\n{_quiet(text)}")
    assert res.reason == "audit_blocked_repeated" and res.exit_code == exit_code_for(load_loop_contract(), "not_derivable"), text

    # 件①: 新事项提示「可选重新派审」(上次裁决 wait_product, 部署提交已变; AIPOS-F225: 顾问再次带事由升 Owner)
    _escalate(rig)
    item2 = _inbox_item(rig)
    assert item2["item_id"] != item["item_id"]
    text_inbox = _inbox_text(rig)
    _show("---- ① 收件箱(修复上线后)----\n" + text_inbox)
    assert item2["reaudit_hint"] and f"{COMMIT_OLD[:7]} → {COMMIT_NEW[:7]}" in item2["reaudit_hint"], item2
    assert f"  提示: {item2['reaudit_hint']}" in text_inbox and "reaudit=环境 / 产品已修好, 重新派审" in text_inbox

    # 件③: 看板该项给转达话术(F201 relay 机制; 只生成文字)与提示行
    from web.board.readonly_pages import render_owner_inbox

    page = render_owner_inbox(str(_home(rig)), {}, owner=False)
    script = "\n".join(oi.choice_script(item2, "reaudit"))
    _show("---- ④ 看板「重新派审」转达话术 ----\n" + script)
    assert 'data-choice="reaudit"' in page and "ib-hint" in page
    assert f"lybra owner decide --item {item2['item_id']} --choice reaudit --relayed-by {DRIVER}" in script
    assert "原命令重跑 loop" in script and "连续 BLOCK 以本裁决为界重新计数" in script

    # 件②: reaudit 预演(零写入)→ 确认(写决策记录, 自动落账)
    decisions = rig.gov / "5_tasks" / "records" / "owner_decisions"
    n_before = len(list(decisions.glob("*.md")))
    rc, out = _decide(rig, item2["item_id"], "reaudit", confirm=False, note="模拟器绑定已修, F200 证据取回已上线")
    _show(f"---- ② owner decide --choice reaudit 预演 rc={rc} ----\n{out[:1200]}")
    assert rc == 0 and len(list(decisions.glob("*.md"))) == n_before, out
    rc, out = _decide(rig, item2["item_id"], "reaudit", confirm=True, note="模拟器绑定已修, F200 证据取回已上线")
    _show(f"---- ② owner decide --choice reaudit --confirm rc={rc} ----\n{out[:1500]}")
    assert rc == 0 and len(list(decisions.glob("*.md"))) == n_before + 1, out

    # 件③: loop status = advisor_fix(owner_ruled), 下一条 = 原命令重跑 loop
    status_text, status = _status(rig.gov, TASK)
    _show("---- ③ loop status(reaudit 裁决后)----\n" + status_text)
    na = status["next_action"]
    assert na["action"] == "advisor_fix" and na["reason"] == "owner_ruled" and na["owner_ruling"]["choice"] == "reaudit", na
    assert "重新派审" in na["hint"] and f"lybra loop --task-id {TASK}" in na["hint"], na

    # 推导核: 以裁决为界清零 → 可推导派下一轮审计 R3(既有派审动词)
    d = nr.derive_next_step(TASK, rig.gov)
    _show("---- ② lybra next 推导(reaudit 裁决后)----\n" + json.dumps(
        {k: d.get(k) for k in ("derivable", "current_node", "command", "verb", "suggested_action", "notes")}, ensure_ascii=False, indent=2))
    assert d["derivable"] and d["verb"] == "lybra_audit_dispatch_dry_run", d
    assert d["command"].startswith(f"lybra audit dispatch --source-task-id {TASK} --actor {DRIVER}")
    assert f"--audit-task-id {TASK}R3 --audit-agent-instance {AUDITOR}" in d["command"] and "owner_reaudit: Owner 裁决 owner-inbox-" in d["command"]
    assert d["audit_blocked"]["consecutive"] == 0 and d["audit_blocked"]["consecutive_total"] == 2

    # 原命令重跑 loop → 派生 R3 并照常拉起审计; R3 再 BLOCK 1 次 = 不升(audit_blocked 例行修环境)
    res, text = _loop(rig, _block_watch(rig, {f"{TASK}R3": "BLOCK"}), COMMIT_NEW)
    _show(f"---- ② 续跑 loop(派 R3, R3 BLOCK 1 次): exit {res.exit_code} reason={res.reason} ----\n{_quiet(text)}")
    assert f"run dispatch @ audit_verdict/claimed ({TASK})" in text and f"--audit-task-id {TASK}R3" in text, text
    assert res.reason == "audit_blocked" and "连续第" not in text, text
    assert nr._read_task_records(rig.gov, TASK)["audit_round_ids"] == [AUDIT, f"{TASK}R2", f"{TASK}R3"]
    r3_path, _q = nr._find_task_in_queue(rig.gov, f"{TASK}R3")
    r3 = nr._read_frontmatter(r3_path)
    assert "owner_reaudit: Owner 裁决 owner-inbox-" in str(r3.get("dispatch_reason")), r3

    # R4 再 BLOCK = 界后连续 2 次 → 再升 Owner
    reaudit = next(s for s in _status(rig.gov, TASK)[1]["next_action"]["fix"] if s.startswith("修好后重新派审计下一轮: "))
    assert f"--audit-task-id {TASK}R4" in reaudit, reaudit
    rc, out, err = _cli(shlex.split(reaudit.split(": ", 1)[1])[1:])
    assert rc == 0, out + err
    res, text = _loop(rig, _block_watch(rig, {f"{TASK}R4": "BLOCK"}), COMMIT_NEW)
    _show(f"---- ② R4 再 BLOCK(界后连续 2 次): exit {res.exit_code} reason={res.reason} ----\n{_quiet(text)}")
    assert res.reason == "audit_blocked_repeated" and "连续第 2 次" in text, text
    assert nr.audit_block_state(rig.gov, TASK)["consecutive_total"] == 4

    # 历史记录不变(前后指纹: 已有文件逐个字节一致, 一个不少)
    after = _history(rig.gov)
    changed = sorted(k for k, v in before.items() if after.get(k) != v)
    _show(f"---- ② 历史指纹: 前 {len(before)} 个文件, 后 {len(after)} 个(新增 {len(set(after) - set(before))}), 变化 {changed} ----")
    assert not changed


def test_boundary_unreadable_and_declarations(rig, monkeypatch):
    _no_auto_reaudit(monkeypatch)
    _two_blocks(rig)
    _escalate(rig)
    item = _inbox_item(rig)
    rc, out = _decide(rig, item["item_id"], "reaudit", confirm=True)
    assert rc == 0, out
    state = nr.audit_block_state(rig.gov, TASK)
    assert state["consecutive"] == 0 and state["owner_reaudit"]["choice"] == "reaudit" and not state["repeated"]
    record = Path(state["owner_reaudit"]["record"])
    text = record.read_text(encoding="utf-8")
    record.write_text(text.replace(f"decided_at: '{state['owner_reaudit']['decided_at']}'", "decided_at: 'not-a-time'"), encoding="utf-8")
    d = nr.derive_next_step(TASK, rig.gov)
    _show("---- 裁决时刻读不出 = 硬停(fail-closed) ----\n" + json.dumps({k: d.get(k) for k in ("derivable", "missing_records", "notes")},
                                                                     ensure_ascii=False, indent=2))
    assert not d["derivable"] and "decided_at 读不出" in json.dumps(d, ensure_ascii=False)
    record.write_text(text, encoding="utf-8")

    decl = nr.audit_blocked_declaration()
    assert decl["owner_reaudit"]["choices"] == ["reaudit"] and decl["owner_needed_after"] == 2
    idecl = oi.inbox_declaration()
    cat = idecl["categories"]["loop_owner_needed"]
    assert set(decl["owner_reaudit"]["choices"]) <= set(idecl["options"])
    assert cat["pinned_options_by_reason"]["audit_blocked_repeated"] == ["reaudit"]
    assert "wait_product" in cat["reaudit_hint"]["after_choices"]
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert src.count("audit_block_state(workspace_root, task_id)") == 1, "one judgement point in the N4 branch (F179)"
    assert src.count("owner_reaudit_ruling(") == 2, "one definition, one caller (audit_block_state)"
    import inspect

    for fn in (nr.owner_reaudit_ruling, nr._verdict_moment, nr.audit_block_state, nr._reaudit_dispatch_reason, nr._derive_owner_reaudit,
               oi.reaudit_hint, oi._loop_items):
        assert "except Exception" not in inspect.getsource(fn), fn.__name__


def test_pinned_option_survives_advisor_options(rig, monkeypatch):
    """顾问 loop note 写了自定义选项(HBLX-21B 形: product_fix)时 reaudit 仍列在其后; 选 reaudit 走同一出口。"""
    _no_auto_reaudit(monkeypatch)
    _two_blocks(rig)
    rc, out, err = _cli(["loop", "note", "--task-id", TASK, "--ask", "请 Lybra 提供恢复入口", "--options", "product_fix=Lybra修复恢复入口后续跑独立审计",
                         "--recommend", "保留候选与截图", "--owner-reason", "rescope", "--workspace-root", str(rig.gov), "--actor", DRIVER,
                         "--confirm"])
    assert rc == 0, out + err
    item = _inbox_item(rig)
    _show("---- ③ 顾问自定义选项 + 恒列 reaudit ----\n" + "\n".join(oi.item_lines(item, oi.inbox_declaration()["texts"])))
    assert [o["key"] for o in item["options"]] == ["product_fix", "reaudit"], item["options"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
