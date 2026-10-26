"""AIPOS-F240: pi 运行中上下文超限的续接(10-26 chris 理想 HBLX-86 三次运行以 launch_context_overflow 结束, 输入约 96k 词元;
实为首轮约 9.5k、运行约 20 分钟里整文件读入大材料累积超窗口, pi 退出前已写压缩记录, 原样重跑同一读法同一处再超)。

件① 诊断分两种: 首轮即超 = launch_context_overflow(原义, 不续接); 首轮正常、运行中累积超 = run_context_overflow(诊断记首轮 / 末轮输入、
     读入最大的工具结果工具名与字数、压缩记录, 不含正文)。
件② 运行中超且超限后有压缩记录 = 同会话续接(指令含卡面路径 / 本卡此刻要点 / 已有产物清单 / 读法纪律, 记 resume_kind), 至多 N 次后以
     run_context_overflow 结束(结束原文附出口全集); 无压缩 = 新会话拉起一次, 开工稿附上次运行摘要(经打码)与已落产物清单。
件③ 读法纪律只在 verbs.schema lybra_kickoff.read_discipline 一处声明, 开工稿 / 执行与审计章程 / 续接指令同读。
件④ lybra next / loop status 对 run_context_overflow 给出口全集原文(续跑 loop 自动续接 / 拆卡 / 换模型), 不再写「续接只会再超」。
件⑤ `lybra loop status --task-id <卡> --rediagnose` 只读重判(靶场同形样本; 真实 chris 记录的重判原文见交回报告)。

靶场: F95 拉起靶场(lrig: 假 pi → 假 harness, 临时 HOME 下写 pi 会话记录); 不起真实 pi、不连真实门、不碰真实治理根与 ~/.pi。
"""
from __future__ import annotations

import copy
import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  - loop 靶场唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  - 拉起靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  - pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  - pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f169_pi_model_declaration import _real_pi_template, install_fake_pi  # noqa: E402
from tools.aipos_cli import loop_driver  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import early_exit_declaration, load_loop_contract, run_loop  # noqa: E402

TASK = f90.TASK
EXITS = ("续跑 loop(原命令)= 自动续接", "拆卡: 按材料分段出子卡", "换窗口更大的模型: 只列出口, 不替 Owner 改")


def _show(text: str) -> None:
    print(text, flush=True)


def _pi_rig(lrig, monkeypatch) -> None:
    _real_pi_template(monkeypatch)
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)


def _loop(r, **kw):
    out = io.StringIO()
    res = run_loop(TASK, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=kw.pop("max_wait", 60),
                   max_steps=kw.pop("max_steps", 30), **kw)
    return res, out.getvalue()


def _runs(gov: Path) -> list[dict]:
    return [nr._read_frontmatter(p) for p in sorted((gov / "5_tasks" / "records" / "loop_runs" / TASK).glob("looprun_*.md"))]


def _status(gov: Path) -> tuple[str, dict]:
    _rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov)])
    _rcj, outj, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov), "--json"])
    return out, json.loads(outj)


def _seed_slot(gov: Path) -> Path:
    """本卡报告落点已有一份产物(上次运行落下的笔记; 续接指令 / 新会话开工稿应列出)。"""
    slot = nr.card_report_path(gov, TASK).parent
    (slot / "artifacts").mkdir(parents=True, exist_ok=True)
    note = slot / "artifacts" / "notes.md"
    note.write_text("# 已读材料笔记\n- PDF p9-11 密钥派生\n", encoding="utf-8")
    return note


def _session(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")


def _assistant(stop: str, inp: int, cache: int = 0) -> dict:
    return {"type": "message", "message": {"role": "assistant", "provider": "kiwiai", "model": "qwen3.7-plus", "stopReason": stop,
                                           "usage": {"input": inp, "output": 1, "cacheRead": cache}, "content": []}}


def _tool(name: str, chars: int) -> dict:
    return {"type": "message", "message": {"role": "toolResult", "toolName": name, "content": [{"type": "text", "text": "x" * chars}]}}


# ===========================================================================
# 件① 诊断分两种(单元: 夹具会话记录, 唯一实现 loop_run_record.session_diagnosis)
# ===========================================================================

def test_item1_diagnosis_run_vs_launch_overflow(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    early = early_exit_declaration(load_loop_contract())
    sessions = tmp_path / ".pi" / "agent" / "sessions" / "--ws--"
    run_entries = [{"type": "session", "id": "s-run"}, _assistant("toolUse", 9453), _tool("read", 31550), _tool("bash", 32585),
                   _tool("read", 24876), _tool("bash", 120), _assistant("length", 3338, 93568),
                   {"type": "compaction", "summary": "SECRET-SUMMARY-BODY", "tokensBefore": 96907}]
    _session(sessions / "2026-10-26T00-00-00-000Z_s-run.jsonl", run_entries)
    diag = lrr.session_diagnosis("pi", "s-run", early)
    line = lrr.diagnosis_line(diag, early)
    _show(f"---- 件① 首轮小 / 末轮超 / 有压缩 → 诊断原文 ----\n{line}\n诊断记录: {json.dumps(diag, ensure_ascii=False)}")
    assert diag["kind"] == "run_context_overflow" and early["end_reasons"]["run_context"] == "run_context_overflow"
    assert diag["first_input_size"] == 9453 and diag["input_size"] == 96906 and diag["assistant_turns"] == 2
    assert diag["top_tool_results"] == [{"tool": "bash", "chars": 32585}, {"tool": "read", "chars": 31550}, {"tool": "read", "chars": 24876}]
    assert diag["compaction"] == {"count": 1, "after_overflow": True}
    assert "运行中上下文累积超出模型窗口(stopReason=length)" in line and "首轮输入 9453 词元 → 末轮输入 96906 词元(助手 2 轮)" in line
    assert "读入最大的工具结果: bash 32585 字, read 31550 字, read 24876 字" in line and "压缩记录: 1 条(超限之后有)" in line
    assert "SECRET-SUMMARY-BODY" not in json.dumps(diag, ensure_ascii=False) + line, "诊断不含会话正文 / 摘要"
    assert "开局上下文" not in line

    # 首轮即超(只有一轮助手消息)= 原义 launch_context_overflow
    _session(sessions / "2026-10-26T00-00-00-000Z_s-launch.jsonl",
             [{"type": "session", "id": "s-launch"}, _assistant("length", 4725, 93568)])
    launch = lrr.session_diagnosis("pi", "s-launch", early)
    _show(f"---- 件① 首轮即超 → 诊断原文 ----\n{lrr.diagnosis_line(launch, early)}")
    assert launch["kind"] == "context_overflow" and "开局上下文超出模型窗口" in lrr.diagnosis_line(launch, early)
    # 多轮但首轮已占末轮大半(≥ launch_first_ratio)= 仍为开局超
    _session(sessions / "2026-10-26T00-00-00-000Z_s-big.jsonl",
             [{"type": "session", "id": "s-big"}, _assistant("toolUse", 80000), _tool("read", 100), _assistant("length", 96000)])
    assert lrr.session_diagnosis("pi", "s-big", early)["kind"] == "context_overflow"
    # 压缩记录在超限之前 = after_overflow False; 摘要读口经打码
    _session(sessions / "2026-10-26T00-00-00-000Z_s-early.jsonl",
             [{"type": "session", "id": "s-early"}, _assistant("toolUse", 9000),
              {"type": "compaction", "summary": "进度: 已读 p9\napi token: fixture-not-a-secret-value"}, _tool("read", 50000),
              _assistant("length", 3000, 90000)])
    early_diag = lrr.session_diagnosis("pi", "s-early", early)
    summary = lrr.session_compaction_summary("pi", "s-early", 4000)
    _show(f"---- 件① 压缩在超限之前: compaction={early_diag['compaction']}; 摘要读口(已打码)----\n{summary}")
    assert early_diag["kind"] == "run_context_overflow" and early_diag["compaction"] == {"count": 1, "after_overflow": False}
    assert summary is not None and "进度: 已读 p9" in summary and "fixture-not-a-secret-value" not in summary and "整行已隐去" in summary
    assert lrr.session_compaction_summary("pi", "s-launch", 4000) is None


# ===========================================================================
# 件② 同会话续接: 续接完成 / 续接到上限以出口全集结束(lybra loop 真跑, 假 pi)
# ===========================================================================

def test_item2_compacted_session_resumes_same_session_then_completes(lrig, monkeypatch):
    _pi_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    note = _seed_slot(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "run_length_once")
    res, text = _loop(lrig)
    _show(f"---- 件② run_length_once: lybra loop exit {res.exit_code} ----\n" + "\n".join(
        ln for ln in text.splitlines() if re.search(r"early_exit|resume|fresh|exit \d|done|ready", ln)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    launches = [l for m in _runs(lrig.gov) for l in m.get("launches") or [] if l["card"] == TASK]
    assert launches[0]["diagnosis"]["kind"] == "run_context_overflow" and launches[0]["resume_kind"] is None
    assert launches[1]["session_id"] == launches[0]["session_id"] and launches[1]["resume"] == 1
    assert launches[1]["resume_kind"] == "context_overflow" and launches[1]["outcome"] == "artifact_ready"
    prompt = (lrig.log / f"{TASK}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"---- 件② 同会话续接指令原文(会话 {launches[1]['session_id']}) ----\n{prompt}")
    card_path = lrig.gov / "5_tasks" / "queue" / "claimed" / f"{TASK.lower()}.md"  # 续接时卡在 claimed(结案后已移走)
    assert "上一轮因上下文累积超出模型窗口中断, 会话已压缩" in prompt and "不要从头重读已读材料" in prompt
    assert f"先读本卡卡面 {card_path}(含返工 / 复核要点)" in prompt
    assert "本卡此刻要点(产品推导):" in prompt and f"{note.relative_to(note.parent.parent)}(" in prompt
    assert "大文件读法: 单次读入可能超过 20000 字的文件" in prompt
    assert "resume: 运行中上下文超限, 会话已压缩 → 同会话" in text


def test_item2_resume_cap_ends_run_context_overflow_with_exits_and_status_next(lrig, monkeypatch):
    _pi_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "run_length")
    res, text = _loop(lrig)
    _show(f"---- 件② run_length(每次都超): lybra loop exit {res.exit_code} reason {res.reason} ----\n{res.message}")
    assert res.reason == "run_context_overflow" and res.exit_code == 3, text
    launches = [l for l in _runs(lrig.gov)[-1]["launches"] if l["card"] == TASK]
    assert [l["resume_kind"] for l in launches] == [None, "context_overflow", "context_overflow"]
    assert len({l["session_id"] for l in launches}) == 1
    assert "已同会话续接 2/2 次、新会话重拉 0/1 次仍超" in res.message
    for exit_text in EXITS:
        assert exit_text in res.message, exit_text
    assert "续接只会再超" not in res.message and "本次模型 qwen3.7-plus" in res.message

    # 件④ loop status 与 lybra next 同一下一动作(出口全集)
    status_text, status = _status(lrig.gov)
    _show("---- 件④ loop status 节选 ----\n" + "\n".join(
        ln for ln in status_text.splitlines() if "早退原因" in ln or "下一动作" in ln or ln.strip()[:2] in ("1)", "2)", "3)", "4)")
        or "续接类型" in ln))
    na = status["next_action"]
    assert na["action"] == "advisor_fix" and na["reason"] == "run_context_overflow"
    assert "续跑 loop(本修法末行命令)= 自动续接" in na["hint"] and "拆卡" in na["hint"] and "换窗口更大的模型" in na["hint"]
    assert "首轮输入 9453 词元 → 末轮输入 96906 词元" in status_text and "续接类型 context_overflow" in status_text
    assert "续接只会再超" not in status_text
    derived = nr.derive_next_step(TASK, lrig.gov)
    next_text = nr.format_output(derived)
    _show("---- 件④ lybra next 文本(Loop 段) ----\n" + "\n".join(ln for ln in next_text.splitlines() if ln.startswith(("Task", "State", "Loop", "  "))))
    assert derived["loop_end"]["reason"] == "run_context_overflow" and derived["loop_end"]["hint"] == na["hint"]
    assert "Loop: 最近一次 loop 运行" in next_text and "以 run_context_overflow 结束(下一动作 advisor_fix)" in next_text
    assert "拆卡: 材料确实读不完" in next_text and "换窗口更大的模型" in next_text


# ===========================================================================
# 件② 无压缩记录 → 新会话拉起一次, 开工稿附上次运行摘要(已打码)与已落产物清单
# ===========================================================================

def test_item2_no_compaction_relaunches_fresh_session_with_redacted_summary(lrig, monkeypatch):
    _pi_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    _seed_slot(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "run_length_nocompact_once")
    res, text = _loop(lrig)
    _show(f"---- 件② run_length_nocompact_once: lybra loop exit {res.exit_code} ----\n" + "\n".join(
        ln for ln in text.splitlines() if re.search(r"early_exit|resume|fresh|exit \d|ready", ln)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    launches = [l for m in _runs(lrig.gov) for l in m.get("launches") or [] if l["card"] == TASK]
    first, second = launches[0], launches[1]
    assert first["diagnosis"]["kind"] == "run_context_overflow" and first["diagnosis"]["compaction"]["after_overflow"] is False
    assert second["session_id"] != first["session_id"] and second["resume"] == 0 and second["resume_kind"] == "context_overflow_fresh"
    kickoff = (lrig.log / f"{TASK}.kickoff").read_text(encoding="utf-8")
    note = kickoff[kickoff.index("## 上次运行摘要"):]
    _show(f"---- 件② 新会话 {second['session_id']} 开工稿所附上次运行摘要原文 ----\n{note}")
    assert f"已认领任务卡 {TASK}。" in kickoff and f"上次会话 {first['session_id']} 运行中上下文累积超出模型窗口且无压缩记录" in note
    assert "已读 PDF p9-11 / p14 / p32" in note and "fixture-not-a-secret-value" not in note and "整行已隐去" in note
    assert "artifacts/notes.md(" in note and "大文件读法: 单次读入可能超过 20000 字的文件" in note
    assert "fresh: 运行中上下文超限, 会话" in text and "无压缩记录 → 新会话" in text
    log = Path(_runs(lrig.gov)[0]["log_path"]).read_text(encoding="utf-8")
    assert "[fresh] pi" in log


def test_item2_no_compaction_fresh_session_still_overflows_ends_with_exits(lrig, monkeypatch):
    _pi_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "run_length_nocompact")
    res, _text = _loop(lrig)
    _show(f"---- 件② run_length_nocompact(新会话仍超): reason {res.reason} ----\n{res.message}")
    assert res.reason == "run_context_overflow" and "已同会话续接 0/2 次、新会话重拉 1/1 次仍超" in res.message
    assert all(e in res.message for e in EXITS)
    launches = [l for l in _runs(lrig.gov)[-1]["launches"] if l["card"] == TASK]
    assert len(launches) == 2 and launches[1]["resume_kind"] == "context_overflow_fresh"


def test_item1_launch_overflow_still_not_resumed(lrig, monkeypatch):
    """首轮即超(F231 length 夹具: 只有一轮助手消息)= 仍为 launch_context_overflow, 不续接, 结束原文不再写「续接只会再超」。"""
    _pi_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "length")
    res, _text = _loop(lrig)
    _show(f"---- 件① 首轮即超: reason {res.reason} ----\n{res.message}")
    assert res.reason == "launch_context_overflow" and "开局上下文超出模型窗口(stopReason=length)" in res.message
    assert "不续接(开局即超出模型窗口" in res.message and "续接只会再超" not in res.message
    assert len(_runs(lrig.gov)[-1]["launches"]) == 1 and not list(lrig.log.glob(f"{TASK}.argv.resume*.json"))
    derived = nr.derive_next_step(TASK, lrig.gov)
    assert derived["loop_end"]["reason"] == "launch_context_overflow"  # next 同给 loop status 修法(精简开局 / 换模型)


# ===========================================================================
# 件③ 读法纪律: 一处声明, 开工稿 / 章程 / 续接指令同读
# ===========================================================================

def test_item3_read_discipline_single_source_in_kickoff_and_charters():
    from tools.aipos_cli.subagent_kickoff import charter_timing_values, kickoff_contract, read_discipline_rule

    contract = kickoff_contract()
    rule = read_discipline_rule(contract)
    _show(f"---- 件③ 读法纪律原文(lybra_kickoff.read_discipline) ----\n{rule}")
    assert "先 grep / head / wc 定位, 再按行段分段读" in rule and "远端源码不整文件 cat" in rule and "草稿目录笔记" in rule
    assert "20000" in rule
    for role in ("executor", "auditor"):
        assert "read_discipline" in contract["layout"][role]
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        line = next(ln for ln in master.splitlines() if "{{read_discipline_rule}}" in ln)
        _show(f"---- 件③ {role} 章程母本行 ----\n{line}")
    assert charter_timing_values("1500")["read_discipline_rule"] == rule
    assert contract["texts"]["read_discipline"] == "- 大文件读法(AIPOS-F240): {read_discipline}"
    bad = copy.deepcopy(contract)
    bad["read_discipline"]["text"] = "无占位"
    from tools.schema_loader import SchemaLoadError

    with pytest.raises(SchemaLoadError):
        read_discipline_rule(bad)


def test_item3_kickoff_and_charter_render_contain_discipline(tmp_path, monkeypatch):
    from test_aipos_f137_audit_long_test_charter import AUDITOR_MASTER, _auditor_ctx, _make_gov
    from tools.aipos_cli.charter_render import render_charter

    gov = _make_gov(tmp_path, monkeypatch, project="lybra", test_contract={"runall_path": "tests/run-all.sh", "require_tests": True})
    rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), _auditor_ctx(tmp_path, gov, "lybra"))
    line = next(ln for ln in rendered.splitlines() if "AIPOS-F240" in ln)
    _show(f"---- 件③ 审计章程渲染行 ----\n{line}")
    assert "{{" not in rendered and "单次读入可能超过 20000 字的文件" in line


# ===========================================================================
# 件⑤ 只读重判 CLI(靶场同形样本: 旧记录 launch_context_overflow → 重判 run_context_overflow, 不写记录)
# ===========================================================================

def test_item5_rediagnose_cli_read_only(lrig, monkeypatch):
    _pi_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "run_length")
    res, _text = _loop(lrig)
    assert res.reason == "run_context_overflow"
    path = sorted((lrig.gov / "5_tasks" / "records" / "loop_runs" / TASK).glob("looprun_*.md"))[-1]
    # 造 F240 前的旧判(与 chris HBLX-86 记录同形): 首次拉起诊断记成 context_overflow
    text = path.read_text(encoding="utf-8").replace("kind: run_context_overflow", "kind: context_overflow", 1)
    path.write_text(text, encoding="utf-8")
    before = {p: p.read_bytes() for p in (lrig.gov / "5_tasks" / "records").rglob("*") if p.is_file()}
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov), "--rediagnose"])
    _show(f"---- 件⑤ lybra loop status --rediagnose(exit {rc}) ----\n{out}{err}")
    assert rc == 0 and "只读重判" in out and "原判 context_overflow" in out and "→ 重判 run_context_overflow / 结束原因 run_context_overflow" in out
    assert "首轮输入 9453 词元 → 末轮输入 96906 词元" in out
    after = {p: p.read_bytes() for p in (lrig.gov / "5_tasks" / "records").rglob("*") if p.is_file()}
    assert before == after, "重判不写任何记录"
    rc2, _out2, err2 = _cli(["loop", "status", "--workspace-root", str(lrig.gov), "--rediagnose"])
    assert rc2 == 2 and "--rediagnose 须给 --task-id" in err2


# ===========================================================================
# 声明 / 单一实现
# ===========================================================================

def test_declarations_fail_closed_and_single_sources():
    contract = load_loop_contract()
    early = early_exit_declaration(contract)
    ro = early["run_overflow"]
    assert ro["resume_max"] == 2 and ro["fresh_max"] == 1 and 0 < ro["launch_first_ratio"] <= 1
    assert contract["run_record"]["end_reasons"]["run_context_overflow"]
    rules = lrr.next_action_declaration(lrr.run_record_declaration(contract))
    assert rules["by_end_reason"]["run_context_overflow"] == "advisor_fix"
    assert "run_context_overflow" in rules["routine_rejections"]["end_reasons"]
    from tools.schema_loader import SchemaLoadError

    for mutate in (lambda d: d["launch"]["early_exit"]["run_overflow"].pop("resume_prompt"),
                   lambda d: d["launch"]["early_exit"]["run_overflow"].update(launch_first_ratio=0),
                   lambda d: d["launch"]["early_exit"]["run_overflow"].update(resume_kind="x", fresh_kind="x"),
                   lambda d: d["launch"]["early_exit"]["end_reasons"].pop("run_context"),
                   lambda d: d["launch"]["early_exit"]["run_overflow"].update(next_end_reasons=["no_such_reason"])):
        bad = copy.deepcopy(contract)
        mutate(bad)
        with pytest.raises(SchemaLoadError):
            early_exit_declaration(bad)
    from tools.schema_loader import code_repo_schema_root, load_schema

    locator = load_schema("card", code_repo_schema_root())["intent_face"]["harness"]["session_record_locators"]["pi"]
    for key in ("entry_type_path", "compaction_entry_type", "compaction_summary_path", "tool_result_role", "tool_name_path", "text_key"):
        assert key in locator, key
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert "续接只会再超" not in src and src.count("def _fresh_session_plan(") == 1 and "except Exception" not in src
    rec = (REPO_ROOT / "tools" / "aipos_cli" / "loop_run_record.py").read_text(encoding="utf-8")
    assert rec.count("def session_diagnosis(") == 1 and rec.count("def card_runs(") == 1
    kick = (REPO_ROOT / "tools" / "aipos_cli" / "subagent_kickoff.py").read_text(encoding="utf-8")
    assert kick.count("def read_discipline_rule(") == 1 and "单次读入可能超过" not in kick, "纪律文本只在声明里"
