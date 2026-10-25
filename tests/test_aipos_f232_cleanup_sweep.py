"""AIPOS-F232 — 收尾清扫(F222R / F223R / F224 / F225 / F225R 审计备注与执行体自报):

① 顾问撤回本人旧请求 `lybra loop note --retract`(原请求留档, 追加撤回字段); F225 前无第一档事由的旧顾问请求降为第二档告知
   (不计待定, 列「旧请求, 可撤回」并附撤回命令)——靶场自造 chris 人肉期样本(理想 HBLX-24BV/35V/37V/39V/31 同形);
② 审计报告头读不出(F225R 实撞 `model: glm-5.3 (provider: kiwiai)` 未加引号)= 报告格式错例行修法: loop 续接审计会话只修报告头
   (指令含解析错行与原因); 会话不可续接 = audit_report_form_error(advisor_fix, 结束原文带修头指令), 不再 investigate;
   F232 前以 frontmatter_unreadable 停线的运行, loop status 复核后同判;
③ 项目授权尾巴: loop status / brief 显示授权告知; 全新项目接入推导不误报「信封目录不存在」; 收件箱授权告知文案去「信封」;
   产品仓顾问文档补 project grant;
④ 运行证据 run 主机不符拒因 = 「运行证据 run 不属于卡面声明主机 X 的验证根」(原「验证根声明读不出」不准确);
⑤ 顾问复核记录 ID / 返工轮登记时刻毫秒精度: 同秒两次复核不撞名、轮次不被误认「针对」下一次复核; 秒级存量记录照读。

靶场 = F90 / F95 / F171B / F196 / F223 / F224 / F221 / F222 构件(禁第二份)+ 临时治理根 / 产品仓 / 临时 HOME(禁真门 / 真治理根 /
真工位 / 起 pi)。验收原文经 _show 打到 stdout(RETURN 证据)。项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
import test_aipos_f95_harness_launch as f95  # noqa: E402
import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402  — 快车道复核打回构件
import test_aipos_f222_rework_limit_arbitration as f222  # noqa: E402  — 返工重交回构件
import test_aipos_f223_multi_host_evidence_host as f223  # noqa: E402  — 多主机运行证据构件
import test_aipos_f224_project_authorization as f224  # noqa: E402  — 项目授权构件
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest 夹具(断言无残留进程)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f134_multi_advisor_lane_envelope import two  # noqa: E402,F401  — pytest 夹具
from test_aipos_f171b_loop_single_instance_resume import _launch_rig, _records, _run  # noqa: E402
from test_aipos_f196_validation_to_pi import vrig  # noqa: E402,F401  — pytest 夹具
from test_aipos_f223_multi_host_evidence_host import twohost  # noqa: E402,F401  — pytest 夹具
from test_aipos_f224_project_authorization import owner  # noqa: E402,F401  — pytest 夹具
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import owner_inbox as oi  # noqa: E402

TASK, AUDIT = f95.TASK, f95.AUDIT
OTHER = "advisor.other.test"  # 靶场: 非补写人的另一位顾问实例


def _show(text: str) -> None:
    print(text, flush=True)


# ===========================================================================
# ① 撤回 + 旧请求降档
# ===========================================================================

def _ended_run(gov: Path, task: str, reason: str = "gate_rejected") -> Path:
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    f78._card(gov, task, "claimed", extra={"claimed_by": EXEC, "claimed_at": "'2026-10-01T00:00:00Z'"})
    rec = LoopRunRecorder(gov, task, driver=DRIVER, contract=load_loop_contract(), warn=_show)
    rec.end(outcome=reason, exit_code=2, reason=reason, message="门拒: SOME_GATE_CODE 交回自检未过")
    return rec.path


def _legacy_ask(path: Path, ask: str, noted_by: str = DRIVER) -> None:
    """靶场自造 F225 前的顾问请求(人肉期样本: owner_ask 无 owner_reason; 写法同 F168 loop note 当时的落盘形)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.record_writer import render_markdown

    fm, body = require_frontmatter(path)
    fm["owner_ask"] = {"ask": ask, "options": [{"key": "product_recovery", "label": "Lybra修复并提供正式恢复入口"}],
                       "recommend": "保留BLOCK及证据, 不放宽校验", "noted_by": noted_by, "noted_at": "2026-10-21T07:51:22Z"}
    path.write_text(render_markdown(fm, body.lstrip("\n"), list(fm)), encoding="utf-8")


def _inbox(home: Path) -> tuple[str, dict]:
    _rc, out, _err = _cli(["owner", "inbox", "--home-root", str(home)])
    _rc2, js, _err2 = _cli(["owner", "inbox", "--home-root", str(home), "--json"])
    return out, json.loads(js)


def test_item1_legacy_asks_downgraded_and_retract(rig):  # noqa: F811
    home = rig.gov.parent
    legacy = {"PROBE-F232-24BV": "新信封已生效但 UI 入口报 Expired authority capture, 审计 pi 退出 0 无报告: 请 Lybra 确认正式审计恢复入口",
              "PROBE-F232-35V": "审计会话 exit0 未交报告, 续接 2/2 仍早退: 请 Lybra 核验原因并确定正式恢复入口",
              "PROBE-F232-31": "候选验证需 Owner 定是否换候选"}
    paths = {task: _ended_run(rig.gov, task) for task in legacy}
    for task, ask in legacy.items():
        _legacy_ask(paths[task], ask)
    fresh = "PROBE-F232-NEW"
    _ended_run(rig.gov, fresh)
    init_governance_repo(rig.gov)
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "note", "--task-id", fresh, "--ask", "这卡要不要换方案",
                         "--owner-reason", "direction_fork", "--actor", DRIVER, "--confirm"])
    assert rc == 0, out + err

    text, data = _inbox(home)
    _show(f"---- ① 收件箱(3 件 F225 前旧请求 + 1 件 F225 后带第一档事由的新请求)----\n{text}")
    items = {i["task_id"]: i for i in data["items"] if str(i.get("task_id") or "").startswith("PROBE-F232")}
    notices = {i["task_id"]: i for i in data["notices"] if str(i.get("task_id") or "").startswith("PROBE-F232")}
    assert set(items) == {fresh} and items[fresh]["owner_tier"] == 1, "新请求带第一档事由 = 照列第一档"
    assert set(notices) == set(legacy), "F225 前旧请求(无第一档事由)= 第二档告知, 不计待定"
    for task, item in notices.items():
        assert item["owner_tier"] == 2 and item["legacy_ask"]["label"] == "旧请求, 可撤回"
        assert item["ask"].startswith(f"【旧请求, 可撤回】{legacy[task]}")
        assert f"lybra loop note --task-id {task} --retract --actor {DRIVER} --workspace-root {rig.gov} --confirm" in item["ask"]
        assert f"  · {item['item_id']}  " in text and "【旧请求, 可撤回】" in text
    rc, st, _err = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", "PROBE-F232-24BV"])
    _show(f"---- ① loop status(旧请求)----\n{st[-900:]}")
    assert "已进 Owner 收件箱第二档告知: " in st and "旧请求无第一档事由, 不计待定" in st

    # 撤回: 非补写人 / 与 --ask 同给 = 拒(零写入); 预演零写入; 确认 = 留档 + 追加撤回字段 + 收件箱核对
    target = "PROBE-F232-24BV"
    before = paths[target].read_text(encoding="utf-8")
    base = ["--workspace-root", str(rig.gov), "loop", "note", "--task-id", target, "--retract"]
    rc, out, err = _cli([*base, "--actor", OTHER, "--confirm"])
    _show(f"---- ① loop note --retract(非补写人)rc={rc} ----\n{out}{err}")
    assert rc == 7 and "RETRACT_REFUSED" in err and f"由 {DRIVER} 补写, 只有补写人本人可撤回" in err
    rc, out, err = _cli([*base, "--ask", "换个请求", "--confirm"])
    _show(f"---- ① loop note --retract --ask(互斥)rc={rc} ----\n{out}{err}")
    assert rc == 7 and "--retract 只撤回" in err
    rc, out, err = _cli([*base, "--retract-reason", "已由 F232 产品修复, 请求过时"])
    _show(f"---- ① loop note --retract 预演 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "loop note --retract 预演(未写入)" in out and paths[target].read_text(encoding="utf-8") == before
    rc, out, err = _cli([*base, "--retract-reason", "已由 F232 产品修复, 请求过时", "--confirm"])
    _show(f"---- ① loop note --retract --confirm rc={rc} ----\n{out}{err}")
    assert rc == 0 and "loop note --retract 已写入" in out and "收件箱核对: 事项 OI-" in out and "已不再列出" in out
    from tools.aipos_cli.loop_driver import load_loop_contract

    ask = lrr.read_run(paths[target], lrr.run_record_declaration(load_loop_contract()))["owner_ask"]
    _show(f"[① 运行记录 owner_ask(留档 + 撤回字段)] {json.dumps(ask, ensure_ascii=False)}")
    assert ask["ask"] == legacy[target] and ask["noted_by"] == DRIVER and ask["retracted_by"] == DRIVER and ask["retracted_at"]
    assert ask["retract_reason"] == "已由 F232 产品修复, 请求过时"
    rc, st, _err = _cli(["--workspace-root", str(rig.gov), "loop", "status", "--task-id", target])
    _show(f"---- ① loop status(撤回后)----\n{st[-700:]}")
    assert f"Owner 待决(已撤回: {DRIVER} @ " in st and "已进 Owner 收件箱" not in st
    rc, out, err = _cli([*base, "--confirm"])
    _show(f"---- ① 再撤一次 rc={rc} ----\n{err}")
    assert rc == 7 and "已于" in err and "撤回" in err
    # 新请求(第一档)本人撤回 = 离开第一档
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "note", "--task-id", fresh, "--retract", "--confirm"])
    assert rc == 0 and "已不再列出" in out, out + err
    text, data = _inbox(home)
    _show(f"---- ① 收件箱(撤回 2 件后)----\n{text}")
    left = [i["task_id"] for i in [*data["items"], *data["notices"]] if str(i.get("task_id") or "").startswith("PROBE-F232")]
    assert sorted(left) == ["PROBE-F232-31", "PROBE-F232-35V"]


def test_item1_retract_refused_after_owner_ruling(rig):  # noqa: F811
    home = rig.gov.parent
    task = "PROBE-F232-RULED"
    _legacy_ask(_ended_run(rig.gov, task), "要不要换候选")
    init_governance_repo(rig.gov)
    _text, data = _inbox(home)
    item = next(i for i in data["notices"] if i.get("task_id") == task)
    rc, out, err = _cli(["owner", "decide", "--item", item["item_id"], "--choice", "product_recovery", "--home-root", str(home),
                         "--relayed-by", "owner", "--owner-words", "先修产品", "--confirm"])
    assert rc == 0, out + err
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "note", "--task-id", task, "--retract", "--confirm"])
    _show(f"---- ① 已裁决后撤回 rc={rc} ----\n{err}")
    assert rc == 7 and f"该请求所在事项 {item['item_id']} 已有 Owner 裁决" in err


def test_item1_retract_merged_into_rework_limit(rig, monkeypatch):  # noqa: F811
    """并入返工上限事项的请求撤回 = 请求撤掉, 返工上限事项本身(返工轮次达上限)照列, 回显点明与本请求无关。"""
    home = rig.gov.parent
    f222._setup_fast_lane(rig)
    f221._loop(rig, [], lambda r: f221._report(r, "v1"))
    with f222._past(monkeypatch, 400, 390):
        assert f221._rework(rig, f221.FOCUS1, f221.CRITERIA1)[0] == 0
    f222._rewrite_and_reloop(rig, "v2", 2)
    with f222._past(monkeypatch, 300, 290):
        assert f221._rework(rig, ["第 2 轮: 超时清理未实现"])[0] == 0
    f222._rewrite_and_reloop(rig, "v3", 4)
    assert f222._note(rig, "--confirm")[0] == 0
    assert f221._rework(rig, ["第 3 轮: 超时清理仍缺"])[0] == 1  # 升返工上限事项, owner_ask 并入
    _text, data = _inbox(home)
    item = next(i for i in data["items"] if i.get("task_id") == f221.CARD)
    assert item["category"] == "rework_limit" and item["recommend"] == "建议授权追加一轮: 只差超时清理一项"
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "loop", "note", "--task-id", f221.CARD, "--retract", "--actor", DRIVER, "--confirm"])
    _show(f"---- ① 撤回并入返工上限事项的请求 rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"该请求曾并入返工上限事项 {item['item_id']}, 请求已撤" in out
    _text, data = _inbox(home)
    after = next(i for i in data["items"] if i.get("task_id") == f221.CARD)
    assert after["item_id"] == item["item_id"] and after["recommend"] != "建议授权追加一轮: 只差超时清理一项", "请求的建议随撤回去掉"


# ===========================================================================
# ② 审计报告头读不出 = 报告格式错例行修法
# ===========================================================================

def _fixes(gov: Path) -> list[dict]:
    return [f for r in _records(gov) for f in r.get("report_fixes") or []]


def test_item2_yaml_error_resumes_audit_session_to_fix_header(lrig, monkeypatch):  # noqa: F811
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "yaml_bad_once")
    res, text = _run(lrig)
    _show("---- ② 审计报告头 YAML 错 → 同会话续接修头 → 入门 → 结案: lybra loop 输出(摘录)----\n"
          + "\n".join(ln for ln in text.splitlines() if re.search(r"launch:|report_fix:|报告头读不出|exit \d|done:", ln)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    fix_line = next(ln for ln in text.splitlines() if "report_fix:" in ln)
    assert "报告头读不出" in fix_line and "续接修报告头 1/1" in fix_line
    prompt = (lrig.log / f"{AUDIT}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[② 修头指令(续接会话)]\n{prompt}")
    assert prompt.startswith("你的审计报告 ") and "model: glm-5.3 (provider: kiwiai)" in prompt and "PyYAML" in prompt
    assert re.search(r"报告头第 \d+ 行 `model: glm-5\.3 \(provider: kiwiai\)` 读不出", prompt)
    assert "整个值加单引号" in prompt
    fixes = _fixes(lrig.gov)
    _show("[② 运行记录 report_fixes] " + json.dumps([{k: f[k] for k in ("card", "role", "outcome", "kinds")} for f in fixes], ensure_ascii=False))
    assert [(f["card"], f["role"], f["outcome"], f["kinds"]) for f in fixes] == [(AUDIT, "auditor", "resumed", ["unreadable"])]


def test_item2_yaml_error_no_session_routine_fix_not_investigate(lrig, monkeypatch):  # noqa: F811
    # F95 模板无会话声明 = 审计无固定会话(子 agent 审计同形)= 不可续接 → audit_report_form_error(advisor_fix), 结束原文带修头指令
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "yaml_bad_once")
    res, text = _run(lrig)
    _show("---- ② 会话不可续接: lybra loop 结束原文 ----\n" + res.message)
    assert res.reason == "audit_report_form_error" and res.exit_code == 2, text
    assert res.message.startswith(f"报告头读不出(报告格式错), 审计会话不可续接(本卡运行记录无该审计卡的固定会话 id): 审计卡 {AUDIT} 报告头读不出")
    assert "修头指令(会话不可续接时顾问派给本卡审计实例): 你的审计报告 " in res.message
    rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show("---- ② loop status(会话不可续接)----\n" + out[-2200:])
    assert rc == 0 and "顾问下一动作: advisor_fix(audit_report_form_error) — 例行结束 audit_report_form_error 第 1/3 次" in out
    assert "审计报告头读不出(YAML 解析错" in out and "你的审计报告 " in out and "model: glm-5.3 (provider: kiwiai)" in out

    # F232 前的停线形(F225R 实撞: end_reason = frontmatter_unreadable)= loop status 复核后同判例行修法, 不再 investigate
    record = Path(_records(lrig.gov)[-1]["log_path"]).with_suffix(".md")
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.record_writer import render_markdown

    fm, body = require_frontmatter(record)
    fm["end_reason"], fm["outcome"], fm["exit_code"] = "frontmatter_unreadable", "not_derivable", 4
    fm["end_message"] = f"frontmatter 读不出 @ None/frontmatter_unreadable ({AUDIT}): (F225R 停线形)"
    record.write_text(render_markdown(fm, body.lstrip("\n"), list(fm)), encoding="utf-8")
    rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show("---- ② loop status(F225R 停线形 frontmatter_unreadable 运行)----\n" + out[-1600:])
    assert "顾问下一动作: advisor_fix(audit_report_form_error)" in out and "investigate(frontmatter_unreadable)" not in out
    na = lrr.loop_status(lrig.gov, TASK)["next_action"]
    assert na["action"] == "advisor_fix" and na["reason"] == "audit_report_form_error"


def test_item2_executor_return_unreadable_same_mechanism(rig):  # noqa: F811
    """执行体交回报告头读不出 = 同一判定(role executor), 修头指令用交回措辞; loop status 复核 frontmatter_unreadable 运行 = return_report_form_error。"""
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder, header_fix_prompt_value

    task = "PROBE-F232-EXEC"
    f78._card(rig.gov, task, "claimed", extra={"claimed_by": EXEC, "claimed_at": "'2026-10-01T00:00:00Z'"})
    ret = rig.gov / "task_cards" / task / "RETURN.md"
    f78._write(ret, "---\ncommit_sha: abc\nmodel: glm-5.3 (provider: kiwiai)\n---\n# RETURN\n")
    init_governance_repo(rig.gov)
    check = validate_task_artifact(task, rig.gov)
    _show(f"[② 执行交回报告头读不出] category={check['category']}\n  header_problem={json.dumps(check.get('header_problem'), ensure_ascii=False)}")
    assert check["category"] == "INGEST_FRONTMATTER_UNREADABLE" and check["header_problem"]["role"] == "executor"
    assert check["header_problem"]["kinds"] == ["unreadable"] and check["header_problem"]["line_no"] == 3
    prompt = header_fix_prompt_value(rig.gov, task)
    _show(f"[② 执行交回修头指令] {prompt}")
    assert prompt.startswith(f"你的交回报告 {ret} 的报告头(frontmatter)读不出") and "报告头第 3 行 `model: glm-5.3 (provider: kiwiai)` 读不出" in prompt
    rec = LoopRunRecorder(rig.gov, task, driver=DRIVER, contract=load_loop_contract(), warn=_show)
    rec.end(outcome="not_derivable", exit_code=4, reason="frontmatter_unreadable", message=f"frontmatter 读不出 @ None/frontmatter_unreadable ({task})")
    na = lrr.loop_status(rig.gov, task)["next_action"]
    _show(f"[② loop status(执行交回读不出的 frontmatter_unreadable 运行)] {na['action']}({na['reason']})")
    assert na["action"] == "advisor_fix" and na["reason"] == "return_report_form_error" and "你的交回报告 " in na["hint"]


def test_item2_unreadable_card_still_hard_stops(tmp_path):
    """读不出的不是本卡报告(卡面 / 记录)= 不归报告格式错(report_unreadable_problem = None), 照旧硬停。"""
    from tools.aipos_cli.artifact_ingest import report_unreadable_problem
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    gov = tmp_path / "gov"
    (gov / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
    (gov / "project.json").write_text(json.dumps({"project": "lybra"}), encoding="utf-8")
    exc = FrontmatterReadError(gov / "5_tasks" / "queue" / "claimed" / "x.md", "PyYAML parse failed", 3)
    assert report_unreadable_problem(gov, "PROBE-X", exc, is_audit=False) is None


# ===========================================================================
# ③ 项目授权尾巴
# ===========================================================================

def test_item3_authorization_notice_in_loop_status_brief_inbox(owner):  # noqa: F811
    o = owner
    rc, out = f224._sign_two_lanes(o, max_tasks=5)
    assert rc == 0, out
    cards = ("PROBE-F232-A1", "PROBE-F232-A2", "PROBE-F232-A3", "PROBE-F232-A4")
    for card in cards:
        f224._card(o.gov, card, "ota")
    for card in cards:
        res, text = f224._loop(o, card, f224.ADV_OTA)
        assert "run claim @" in text, text
    rc, st, _err = _cli(["--workspace-root", str(o.gov), "loop", "status", "--task-id", cards[-1]])
    _show(f"---- ③ loop status(运行用项目授权, 已用 4/5)----\n{st[-1500:]}")
    assert f"项目授权 {f224.AUTH} 告知: 已用 4/5 张业务卡(≥80%)(不阻塞); 扩大授权(Owner 执行): lybra project grant extend" in st
    rc, br, _err = _cli(["brief", "--workspace-root", str(o.gov)])
    _show("---- ③ brief(摘录)----\n" + "\n".join(ln for ln in br.splitlines() if "项目授权" in ln or "信封预警" in ln))
    assert f"项目授权 {f224.AUTH} 告知: 已用 4/5 张业务卡(≥80%)(不阻塞)" in br and "--max-tasks 8" in br
    rc, js, _err = _cli(["brief", "--workspace-root", str(o.gov), "--json"])
    warns = json.loads(js)["authorization_warnings"]
    assert [w["authorization_id"] for w in warns] == [f224.AUTH]
    inbox = oi.collect_project_inbox(o.gov, home_root=o.gov.parent)
    warn = [i for i in inbox["notices"] if i["category"] == "envelope_warning"]
    _show("[③ 收件箱授权告知] " + json.dumps([{k: i.get(k) for k in ("subject", "ask", "recommend", "impact", "decide_line")} for i in warn],
                                         ensure_ascii=False))
    assert len(warn) == 1 and warn[0]["subject"] == f"项目授权 {f224.AUTH}" and warn[0]["category_label"] == "项目授权将尽"
    assert warn[0]["ask"] == "已用 4/5 张业务卡(≥80%): 定是否扩大项目授权(总卡数 / 到期)"
    assert all("信封" not in str(warn[0][k]) for k in ("category_label", "subject", "ask", "recommend", "impact", "decide_line"))
    _rc, text, _err = _cli(["owner", "inbox", "--home-root", str(o.gov.parent)])
    line = next(ln for ln in text.splitlines() if warn[0]["item_id"] in ln)
    _show(f"[③ 收件箱 CLI 第二档告知行] {line}")
    assert "(项目授权将尽): 已用 4/5 张业务卡(≥80%): 定是否扩大项目授权" in line and "信封" not in line
    assert [o_["key"] for o_ in warn[0]["options"]] == ["acknowledge", "let_expire"]


def test_item3_fresh_project_wiring_no_envelope_dir_message(tmp_path, owner):  # noqa: F811
    from tools.aipos_cli.workstation_wiring import derive_effective_owner_policy_ref, enroll_envelope_requirement

    gov = tmp_path / "fresh-gov"
    (gov / "5_tasks" / "queue").mkdir(parents=True)
    (gov / "project.json").write_text(json.dumps({"project": "fresh"}), encoding="utf-8")
    pid, why = derive_effective_owner_policy_ref(gov, role="executor", agent_instance="exec.fresh.test")
    _show(f"[③ 全新项目(无信封目录、无项目授权)接入推导] policy={pid} reason={why}")
    assert pid is None and "信封目录不存在" not in why and "本项目尚未签项目授权" in why and "lybra project grant sign" in why
    req = enroll_envelope_requirement(gov, role="executor", role_class="executor", agent_instance="exec.fresh.test", executor_mode="workstation")
    _show(f"[③ 接入拒因(全新项目)]\n{req['refusal']}")
    assert req["refusal"] and "lybra project grant sign --workspace-root" in req["refusal"] and "信封目录不存在" not in req["refusal"]
    # 有项目授权而无信封目录(owner 夹具已删全部信封)= 照常经授权判覆盖
    rc, out = f224._sign_two_lanes(owner, max_tasks=5)
    assert rc == 0, out
    pid, why = derive_effective_owner_policy_ref(owner.gov, role="advisor", agent_instance=f224.ADV_OTA)
    _show(f"[③ 有项目授权的项目接入推导] policy={pid} reason={why}")
    assert pid == f224.AUTH and why == "matched"


def test_item3_docs_cover_project_grant():
    for rel in ("agents/skills/advisor-commands/SKILL.md", "agents/skills/lybra-onboarding/SKILL.md", "docs/mcp-agent-setup.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        hits = [ln.strip()[:120] for ln in text.splitlines() if "lybra project grant sign" in ln]
        _show(f"[③ 文档 {rel}] project grant sign 行: {hits[:2]}")
        assert hits, rel


# ===========================================================================
# ④ 主机不符拒因措辞
# ===========================================================================

def test_item4_host_mismatch_refusal_prefix(twohost):  # noqa: F811
    gov, local_decl, remote_decl = twohost.gov, twohost.local_decl, twohost.remote_decl
    candidate, tree = twohost.candidate, twohost.tree
    local_run = f223._prepare_run(gov, local_decl)
    local_entries = f223._exec_commands(local_run, candidate, tree)
    remote_entries = [{**e, "run_id": Path(twohost.remote_run).name, "run_dir": twohost.remote_run} for e in local_entries]
    host = f223._local()
    assert f223._amend(gov, f223._evidence(host))[0] == 0
    bad = f223._check(gov, f223.VALR, f223.VAL, candidate, remote_entries)
    _show(f"[④ host={host} · 报告 run 属未选中远端声明 · 裁决入口拒因]\n  " + "\n  ".join(bad["reasons"]))
    assert f"运行证据 run 不属于卡面声明主机 {host} 的验证根(fail-closed): VR_EVIDENCE_HOST_MISMATCH" in bad["reasons"][0]
    assert "验证根声明读不出" not in bad["reasons"][0]


# ===========================================================================
# ⑤ 复核记录 / 返工轮时刻毫秒精度
# ===========================================================================

def test_item5_same_second_reviews_do_not_collide(rig, monkeypatch):  # noqa: F811
    from tools.aipos_cli import board_adapter, queue_mutation
    from tools.aipos_cli import next_resolver as nr

    f222._setup_fast_lane(rig)
    f221._loop(rig, [], lambda r: f221._report(r, "v1"))
    second = (datetime.now(timezone.utc) - timedelta(seconds=100)).replace(microsecond=0)
    stamp = second.strftime("%Y-%m-%dT%H:%M:%S")
    with monkeypatch.context() as m:
        m.setattr(board_adapter, "iso_ms", lambda: f"{stamp}.100Z")
        m.setattr(queue_mutation, "iso_ms", lambda: f"{stamp}.200Z")
        rc, payload, err = f221._rework(rig, f221.FOCUS1, f221.CRITERIA1)
    assert rc == 0, f221._reason(payload, err)
    f222._rewrite_and_reloop(rig, "v2", 2)
    with monkeypatch.context() as m:  # 第二次复核与第 1 轮登记同一秒(仅毫秒在后)
        m.setattr(board_adapter, "iso_ms", lambda: f"{stamp}.300Z")
        m.setattr(queue_mutation, "iso_ms", lambda: f"{stamp}.400Z")
        rc, payload, err = f221._rework(rig, ["第 2 轮: 超时清理未实现"])
    events = f221._events(rig)
    rounds = f221._rounds(rig)
    _show(f"---- ⑤ 同秒两次复核 rc={rc} ----\n复核记录: {[p.name for p in events]}\n返工轮: "
          + json.dumps([{k: r.get(k) for k in ("round", "verdict_ref", "created_at")} for r in rounds], ensure_ascii=False))
    assert rc == 0 and payload["data"]["round_added"] == 2, f221._reason(payload, err)
    assert len(events) == 2 and len({p.name for p in events}) == 2
    compact = stamp.replace("-", "").replace(":", "").replace("T", "_")
    assert [p.stem for p in events] == [f"advisor_review_{f221.CARD}_{compact}_100", f"advisor_review_{f221.CARD}_{compact}_300"]
    assert [r["created_at"] for r in rounds] == [f"{stamp}.200Z", f"{stamp}.400Z"]
    assert rounds[1]["verdict_ref"] == events[1].stem
    # 读侧解析比较: 秒级存量裁决时刻(…Z)与毫秒级轮次混比按时刻; 毫秒级同秒先后分得开
    legacy_view = {"verdict_id": "legacy", "verdict_at": f"{stamp}Z", "audit_task_id": ""}
    assert nr.rework_round_for_verdict({"rework_rounds": [{"round": 1, "verdict_ref": "x", "created_at": f"{stamp}.200Z"}]}, legacy_view)
    later_view = {"verdict_id": "later", "verdict_at": f"{stamp}.300Z", "audit_task_id": ""}
    assert nr.rework_round_for_verdict({"rework_rounds": [{"round": 1, "verdict_ref": "x", "created_at": f"{stamp}.200Z"}]}, later_view) is None
    anchor = nr.advisor_review_anchor(rig.gov, f221.CARD)
    assert anchor is not None and anchor[1]["verdict_id"] == events[1].stem


def test_item5_iso_ms_format():
    from tools.aipos_cli.clock import iso_ms

    moment = datetime(2026, 10, 25, 4, 11, 49, 123456, tzinfo=timezone.utc)
    _show(f"[⑤ iso_ms] {iso_ms(moment)}")
    assert iso_ms(moment) == "2026-10-25T04:11:49.123Z" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", iso_ms())


# ===========================================================================
# 声明守卫(fail-closed)
# ===========================================================================

def test_declarations_fail_closed(monkeypatch):
    import copy

    import tools.schema_loader as sl
    from tools.schema_loader import SchemaLoadError

    real = sl.load_schema

    def _patched(mutate):
        def load(name, *a, **k):
            data = real(name, *a, **k)
            if name != "verbs":
                return data
            data = copy.deepcopy(data)
            mutate(data["verbs"])
            return data
        return load

    cases = {
        "删 advisor_ask.legacy": lambda v: v["lybra_owner_inbox"]["categories"]["loop_owner_needed"]["advisor_ask"].pop("legacy"),
        "legacy 档位非 2": lambda v: v["lybra_owner_inbox"]["categories"]["loop_owner_needed"]["advisor_ask"]["legacy"].__setitem__("tier", 1),
        "删 envelope_warning.authorization": lambda v: v["lybra_owner_inbox"]["categories"]["envelope_warning"].pop("authorization"),
    }
    for label, mutate in cases.items():
        with monkeypatch.context() as m:
            m.setattr(sl, "load_schema", _patched(mutate))
            with pytest.raises(SchemaLoadError) as exc:
                oi.inbox_declaration()
            _show(f"[声明守卫: {label}] {exc.value}")
    from tools.aipos_cli.verb_contract import declared_exit_codes

    assert declared_exit_codes("lybra_loop_note")["retract_refused"] == 7
