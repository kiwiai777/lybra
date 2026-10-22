"""AIPOS-F215 — 碎片化整理 F2: 交回占位拒收与文案收尾(gap #219 + F208 P2-7 余项 + F212 局限 2/3/4)。

病根(gap #219, AIPOS-F213R 审计 F-1): 执行体在第二次全量未完时先写出 RETURN.md, 正文含 RUNALL_TIP2_PLACEHOLDER / RESIDUAL_PLACEHOLDER
占位与预写计数; loop 以报告文件出现为交回信号即续推, 产物入口只核 frontmatter, 门交回也未拒正文占位。

件① 交回占位拒收: 交回 / 审计报告入门(artifact_ingest)时正文仍有占位 = 拒 INGEST_REPORT_UNFILLED, 拒因逐条列行号与原文;
   判据声明 transitions.schema artifact_ingest.report_placeholder(与认领骨架「待填写」同一判据族; 原代码常量 PLACEHOLDER_PATTERNS
   与 next_resolver 骨架前缀常量收进声明), 唯一实现 validation_common.report_body_placeholders; 门交回自检同读; 引用 / 代码行内不误拦。
   loop 门拒原文抽得该码 = 例行 advisor_fix。
件② RETURN 最后落盘: 开工稿与执行 / 审计章程同读 validation_common.report_write_last_rule(先写临时名再改名)。
件③ 文案收尾: 「缺任一项或仍为占位拒收」一处声明 artifact_ingest.report_refusal_clause; `lybra --workspace-root X` 前缀一个渲染函数
   workspace_config.lybra_command_prefix; regression baseline argparse help 读 state_labels; 开工稿禁写行由 write_boundary.role_rows
   派生(roles.schema write_boundary.no_write)。

靶场一律临时治理根(F78 交回入门靶场 lybra / chris 两形; F190 chris 形开工稿 / 章程靶场); 不连生产门、不碰真实治理根 / 工位、不起 pi。
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
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f73d_loop_driver import POLICY  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR as F78_AUDITOR  # noqa: E402
from test_aipos_f78_engine_agnostic import _card as _f78_card  # noqa: E402
from test_aipos_f78_engine_agnostic import _fm as _f78_fm  # noqa: E402
from test_aipos_f78_engine_agnostic import _ingest_setup, _write  # noqa: E402
from test_aipos_f190_validation_root import AUD, CARD, EXEC, _cli, rig  # noqa: E402,F401
from test_aipos_f94_governance_landing import DRIVER  # noqa: E402

AIPOS = REPO_ROOT / "tools" / "aipos_cli"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _gap219_return(task_id: str, sha: str, tree: str) -> str:
    """gap #219 形: frontmatter 已填(执行体自认交回), 正文第二次全量计数与残留进程仍是占位(残留进程原文段落在代码块里)。"""
    return _f78_fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{task_id}"},
                   f"# RETURN — {task_id}\n\n## 一句话结论\n四件完成, 以第二次全量为准。\n\n## 两边基线\n"
                   "- tip 侧第二次全量: RUNALL_TIP2_PLACEHOLDER\n- 首跑 474 ✓ / 0 ✗\n\n## 残留进程\n```\nRESIDUAL_PLACEHOLDER\n```\n")


def _filled_return(task_id: str, sha: str, tree: str) -> str:
    """同一份报告补完证据后: 计数实值; 正文引用占位字样(反引号 / 句中 / 代码行内)不算占位(AIPOS-F65C 件④)。"""
    return _f78_fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{task_id}"},
                   f"# RETURN — {task_id}\n\n## 一句话结论\n四件完成。\n\n## 两边基线\n"
                   "- tip 侧第二次全量: 475 ✓ / 0 ✗(上轮报告的 `RUNALL_TIP2_PLACEHOLDER` 已填实)\n"
                   "- 说明: 骨架占位形如 \"(待填写: …)\", 本报告无此行; 这是一个(待填写)的引用\n\n## 残留进程\n```\n(无输出)\n"
                   "N6_LANDING_ACTOR_PLACEHOLDER = \"<驱动方实例>\"\n```\n")


def _ingest(monkeypatch, task_id: str, gov: Path) -> tuple[int, dict]:
    from tools.aipos_cli.aipos_cli import main

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    try:
        rc = main(["artifact", "ingest", "--task-id", task_id, "--workspace-root", str(gov), "--dry-run", "--json"])
    finally:
        monkeypatch.setattr(sys, "stdout", sys.__stdout__)
    return rc, json.loads(buf.getvalue())


# ===========================================================================
# 件① 交回 / 审计报告正文占位入门即拒
# ===========================================================================

@pytest.mark.parametrize("shape", ["lybra", "chris"])
def test_item1_return_with_body_placeholder_rejected_with_line_numbers_then_passes(tmp_path, monkeypatch, shape):
    from tools.aipos_cli.artifact_ingest import INGEST_EXIT_REJECTED, validate_task_artifact

    task_id = f"F215-RET-{shape.upper()}"
    gov, _repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, shape, task_id)
    _write(ret, _gap219_return(task_id, sha, tree))
    rc, data = _ingest(monkeypatch, task_id, gov)
    _show(f"[件①·{shape}] 带占位交回 → artifact ingest rc={rc} category={data['category']}\n  拒因原文: {data['reasons'][0]}")
    assert rc == INGEST_EXIT_REJECTED and data["ok"] is False and data["category"] == "INGEST_REPORT_UNFILLED", data
    reason = data["reasons"][0]
    lines = ret.read_text(encoding="utf-8").split("\n")
    tip2 = lines.index("- tip 侧第二次全量: RUNALL_TIP2_PLACEHOLDER") + 1
    residual = lines.index("RESIDUAL_PLACEHOLDER") + 1
    assert reason.startswith("INGEST_REPORT_UNFILLED: ") and str(ret) in reason and "仍有占位 2 处" in reason
    assert f"第 {tip2} 行「- tip 侧第二次全量: RUNALL_TIP2_PLACEHOLDER」" in reason and f"第 {residual} 行「RESIDUAL_PLACEHOLDER」" in reason
    assert "写该交回报告的执行体" in reason and "先写临时名再改名" in reason  # 出口
    assert [p["line"] for p in data.get("placeholders") or validate_task_artifact(task_id, gov)["placeholders"]] == [tip2, residual]
    _write(ret, _filled_return(task_id, sha, tree))
    rc2, data2 = _ingest(monkeypatch, task_id, gov)
    _show(f"[件①·{shape}] 去占位(引用字样保留)后 → artifact ingest rc={rc2} category={data2['category']} message={data2.get('message')}")
    assert rc2 == 0 and data2["ok"] and data2["category"] == "DRY_RUN", data2
    assert validate_task_artifact(task_id, gov)["category"] == "OK"


def test_item1_verdict_skeleton_body_rejected_same_judgment(tmp_path, monkeypatch):
    """审计报告同判: frontmatter 已填(裁决 / commit_sha 实值)但正文仍是认领骨架的待填写行 = 拒, 行号指向骨架行。"""
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.record_writer import build_verdict_skeleton_markdown

    task_id = "F215-VER"
    audit = f"{task_id}R"
    gov, _repo, sha, _tree, _ret = _ingest_setup(tmp_path, monkeypatch, "chris", task_id)
    _f78_card(gov, audit, "claimed", assigned=F78_AUDITOR, task_mode="audit", extra={"reviewed_task_id": task_id})
    report = gov / "5_tasks" / "records" / "audit_verdicts" / audit / "RETURN.md"
    skeleton = build_verdict_skeleton_markdown(audit, task_id)
    body = skeleton.split("---\n", 2)[2]
    _write(report, _f78_fm({"verdict": "PASS", "reviewed_task_id": task_id, "commit_sha": sha}, body))
    bad = validate_task_artifact(audit, gov)
    _show(f"[件①·审计报告] 正文仍为骨架 → category={bad['category']}\n  拒因原文: {bad['reasons'][0]}")
    assert bad["ok"] is False and bad["kind"] == "verdict" and bad["category"] == "INGEST_REPORT_UNFILLED", bad
    text_lines = report.read_text(encoding="utf-8").split("\n")
    expected = [i + 1 for i, ln in enumerate(text_lines) if ln.startswith("(待填写")]
    assert len(expected) == 3 and [p["line"] for p in bad["placeholders"]] == expected
    assert "写该审计报告的审计体" in bad["reasons"][0]
    _write(report, _f78_fm({"verdict": "PASS", "reviewed_task_id": task_id, "commit_sha": sha},
                           "# audit\n\n## 一句话结论\nPASS\n\n## Findings\n无\n\n## 逐条验收核验\n- ① 夹具绿(原文见日志)\n"))
    good = validate_task_artifact(audit, gov)
    assert good["ok"] and good["category"] == "OK", good


def test_item1_judgment_rules_reference_vs_blank():
    """判据: 填空位(整行 / 标签值 / 表格单元格 / 代码块整行)才算; 句中、反引号、引号、代码行内的引用不算(F65C 件④ 不回退)。"""
    from tools.aipos_cli.validation_common import report_body_placeholders

    cases = {
        "- 全量计数: RUNALL_TIP2_PLACEHOLDER": True,
        "计数 RUNALL_TIP2_PLACEHOLDER(待补)": True,
        "| 全量 | (待填写) |": True,
        "- tip 侧计数: TODO": True,
        "| TBD | 类 | 冻结决策(值) |": False,  # 表头列名(存量 chris HBJOTA-22 报告实例), 单元格只判骨架前缀
        "TBD": True,
        "(待填写: 一句话概括任务完成情况)": True,
        "commit_sha: (待填写: 照抄)": True,
        "上轮的 `RUNALL_TIP2_PLACEHOLDER` 已填实": False,
        "引用 \"RESIDUAL_PLACEHOLDER\" 字样": False,
        "这是一个(待填写)的文本": False,
        "TODO: 实现这个功能 FIXME: 修复 bug": False,
        "N6_LANDING_ACTOR_PLACEHOLDER 是常量名": True,
        "_PLACEHOLDER 后缀": False,
        "PLACEHOLDER_DETECTED: 门拒码": False,
    }
    for line, hit in cases.items():
        got = bool(report_body_placeholders(line))
        assert got is hit, (line, got)
    fenced = "```\nRESIDUAL_PLACEHOLDER\n(待填写: 测试命令及输出)\nN6_LANDING_ACTOR_PLACEHOLDER = \"<x>\"\nTODO\n```\n"
    assert [h["line"] for h in report_body_placeholders(fenced)] == [2, 3]  # 代码块: 只认整行占位, 代码行内与 TODO 行不算


def test_item1_gate_return_self_check_reads_same_judgment():
    """门交回自检(board_adapter._check_return_not_skeleton → check_placeholder_in_text)与产物入口同一判定, 拒因附行号。"""
    from tools.aipos_cli.validation_common import check_placeholder_in_text, report_body_placeholders

    text = _gap219_return("F215-GATE", "a" * 40, "b" * 40)
    reasons = check_placeholder_in_text(text, "RETURN.md 内容")
    lines = [h["line"] for h in report_body_placeholders(text)]
    _show(f"[件①·门交回自检] {reasons[0]}")
    assert len(reasons) == 1 and reasons[0].startswith("PLACEHOLDER_DETECTED: RETURN.md 内容 包含占位符: RUNALL_TIP2_PLACEHOLDER, RESIDUAL_PLACEHOLDER")
    assert f"(第 {lines[0]}、{lines[1]} 行)" in reasons[0]


def test_item1_single_source_no_second_placeholder_table():
    """占位判定只一处: 原代码常量 PLACEHOLDER_PATTERNS 与 next_resolver 骨架前缀常量退役, 声明是唯一来源; 认领骨架被同一判据全数认出。"""
    from tools.aipos_cli import validation_common as vc
    from tools.aipos_cli.record_writer import build_return_skeleton_markdown, build_verdict_skeleton_markdown

    decl = vc.report_placeholder_declaration()
    assert not hasattr(vc, "PLACEHOLDER_PATTERNS")
    for rel in ("next_resolver.py", "validation_common.py", "artifact_ingest.py", "board_adapter.py"):
        src = (AIPOS / rel).read_text(encoding="utf-8")
        assert '"(待填写"' not in src and "_RETURN_PLACEHOLDER_PREFIX" not in src, rel
    for skeleton in (build_return_skeleton_markdown("F215-SK"), build_verdict_skeleton_markdown("F215-SKR", "F215-SK")):
        body_lines = [i + 1 for i, ln in enumerate(skeleton.split("\n")) if decl["skeleton_prefix"] in ln.lstrip("|- ")[:12]]
        hits = vc.report_body_placeholders(skeleton)
        assert hits and {h["line"] for h in hits} >= set(body_lines), (body_lines, hits)


def test_item1_rejection_is_routine_advisor_fix(tmp_path, monkeypatch):
    """loop 门拒原文(execute_derived_action 产物入口)抽得 INGEST_REPORT_UNFILLED = 例行码 → advisor_fix(派写报告者补完证据, 原命令续跑)。"""
    from tools.aipos_cli import loop_run_record as lrr
    from tools.aipos_cli import next_resolver as nr
    from tools.aipos_cli.loop_driver import load_loop_contract

    task_id = "F215-LOOP"
    gov, _repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, "chris", task_id)
    _write(ret, _gap219_return(task_id, sha, tree))
    command = f"lybra artifact ingest --task-id {task_id} --kind return --workspace-root {gov}"
    out = nr.execute_derived_action({"derivable": True, "task_id": task_id, "current_node": "claim", "command": command}, gov, None)
    msg = f"门拒 @ return ({task_id}) exit {out['exit_code']}: {out['message']}\n{out['output']}"  # loop_driver 门拒结束原文同形
    decl = lrr.run_record_declaration(load_loop_contract())
    codes = lrr.extract_end_codes(msg, decl)
    assert out["ok"] is False and codes == ["INGEST_REPORT_UNFILLED"], (out, codes)
    view = {"task_id": task_id, "run_id": "r", "state": "ended", "end_reason": "gate_rejected", "end_message": msg.splitlines()[0],
            "end_codes": codes, "driver": DRIVER, "envelope": POLICY}
    na = lrr.next_action(view, lrr.next_action_declaration(decl), governance_root=gov)
    _show(f"[件①·loop] 入门拒 → 下一动作 {na['action']}({na['reason']}) codes={na.get('codes')}\n{na['hint']}")
    assert na["action"] == "advisor_fix" and na["codes"] == ["INGEST_REPORT_UNFILLED"]
    assert "先写临时名再改名" in na["hint"] and na["hint"].splitlines()[-1].startswith(f"lybra loop --task-id {task_id}")


# ===========================================================================
# 件② RETURN 最后落盘: 开工稿与执行 / 审计章程同一渲染
# ===========================================================================

def test_item2_kickoff_and_charters_render_write_last_rule(rig):
    from tools.aipos_cli.next_resolver import card_report_path
    from tools.aipos_cli.validation_common import report_placeholder_declaration, report_write_last_rule
    from tools.aipos_cli.workspace_config import project_paths

    gov = rig["gov"]
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0, err
    report = card_report_path(gov, CARD)
    rule = report_write_last_rule(str(report))
    line = next(ln for ln in brief.splitlines() if "最后才写报告" in ln)
    _show(f"---- 件② 开工稿「最后落盘」句原文 ----\n{line}")
    assert line == f"- {rule}" and f"`{report}{report_placeholder_declaration()['temp_suffix']}`" in line
    paths = project_paths(gov)
    for role, instance, root, slot in (("executor", EXEC, paths["return_root"], "<卡ID>"), ("auditor", AUD, paths["verdict_root"], "<审计卡ID>")):
        rc, charter, err = _cli("charter", "--role", role, "--instance", instance, "--workspace-root", gov)
        assert rc == 0, err
        cline = next(ln for ln in charter.splitlines() if ln.startswith("- **报告最后落盘**(AIPOS-F215):"))
        _show(f"---- 件② {role} 章程「最后落盘」句原文 ----\n{cline}")
        assert cline.endswith(report_write_last_rule(f"{root}/{slot}/RETURN.md"))
    for role, key in (("executor", "return_write_last_rule"), ("auditor", "verdict_write_last_rule")):
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert "{{" + key + "}}" in master and "先写临时名" not in master, role


def test_item2_temp_name_is_not_a_report_candidate(tmp_path, monkeypatch):
    """临时名不被产物入口认作报告(候选 return_file_candidates 含 *.md 通配): 只有临时名 = 交回缺失, 改名后才入门。"""
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.validation_common import report_placeholder_declaration

    task_id = "F215-TMP"
    gov, _repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, "lybra", task_id)
    temp = ret.with_name(ret.name + report_placeholder_declaration()["temp_suffix"])
    _write(temp, _filled_return(task_id, sha, tree))
    assert validate_task_artifact(task_id, gov)["category"] == "INGEST_RETURN_MISSING"
    temp.rename(ret)
    assert validate_task_artifact(task_id, gov)["category"] == "OK"


# ===========================================================================
# 件③ 文案收尾
# ===========================================================================

def _product_texts() -> dict[str, str]:
    files = [*AIPOS.glob("*.py"), *(REPO_ROOT / "tools" / "mcp_server").glob("*.py"), *(REPO_ROOT / "schema").glob("*.json"),
             *(REPO_ROOT / "agents").rglob("*.md")]
    return {str(p.relative_to(REPO_ROOT)): p.read_text(encoding="utf-8") for p in files}


def test_item3_refusal_clause_single_declaration_rendered_everywhere(rig):
    from tools.aipos_cli.next_resolver import render_report_frontmatter_clause, report_frontmatter_contract, report_refusal_clause

    clause = report_refusal_clause()
    hits = {rel: text.count(clause) for rel, text in _product_texts().items() if clause in text}
    _show(f"[件③ 拒收条款] 「{clause}」字面出现处: {hits}")
    assert hits == {"schema/transitions.schema.json": 1}
    assert render_report_frontmatter_clause(report_frontmatter_contract("return")).endswith("。" + clause)
    gov = rig["gov"]
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0 and f"frontmatter 必填({clause}; sha 一律" in brief, err
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    for key in ("template", "template_no_worktree", "template_governance_root"):
        assert "{refusal_clause}" in verbs["lybra_my_tasks"]["kickoff"][key], key


def test_item3_workspace_root_prefix_single_renderer():
    from tools.aipos_cli.post_merge_regression import audit_baseline_command
    from tools.aipos_cli.run_long import charter_commands, wait_command
    from tools.aipos_cli.validation_root import _gov_cmd
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    root = Path("/srv/gov root")
    prefix = lybra_command_prefix(root)
    assert prefix == "lybra --workspace-root '/srv/gov root'"
    assert audit_baseline_command(root).startswith(prefix + " regression baseline")
    assert wait_command(root, "X-1", "full").startswith(prefix + " run-long wait")
    assert all(v.startswith(f"`{prefix} run-long ") for v in charter_commands(root).values())
    assert _gov_cmd(root) == prefix
    pattern = re.compile(r"""f["']lybra --workspace-root \{|["']lybra["'], ["']--workspace-root["']""")
    offenders = [p.name for p in AIPOS.glob("*.py") if p.name != "workspace_config.py" and pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], offenders  # 前缀只在 workspace_config.lybra_command_prefix 一处


def test_item3_regression_baseline_help_reads_state_labels():
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.post_merge_regression import baseline_cli_help, baseline_state_labels

    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    regression = sub.choices["regression"]
    rsub = next(a for a in regression._actions if a.__class__.__name__ == "_SubParsersAction")
    help_text = next(a.help for a in rsub._choices_actions if a.dest == "baseline")
    _show(f"[件③ argparse help] regression baseline: {help_text}")
    assert help_text == baseline_cli_help()
    assert all(f"「{label}」" in help_text for label in baseline_state_labels().values())
    src = (AIPOS / "aipos_cli.py").read_text(encoding="utf-8")
    assert "self_run_required = " not in src and "pending = 该 sha" not in src


def test_item3_kickoff_no_write_lines_derive_from_role_rows(rig):
    from tools.aipos_cli.write_boundary import build_write_boundary, no_write_lines, role_rows

    gov = rig["gov"]
    kickoff = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]["lybra_kickoff"]
    assert not {"no_write_governance", "no_write_harness", "no_write_product_auditor"} & set(kickoff["texts"])
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0, err
    section = brief[brief.index("## 禁写与禁止动作"):brief.index("## 硬规矩")]
    _show(f"---- 件③ 开工稿禁写节(执行体 {EXEC}, 子 agent 无工位)----\n{section}")
    boundary = build_write_boundary(gov, role="executor", harness_root=gov)
    rows = {r["surface"]: r["path"] for r in role_rows(boundary, "executor", task_id=CARD)}
    assert f"- 禁写治理根 `{rows['governance_root']}`(唯一例外: 本卡报告落点 `{rows['return_slot']}` 及其下" in section
    assert "禁写工位" not in section  # 子 agent 无工位 = 不出工位行(与改前一致)
    ws = Path(rig["home"]) / "workstations" / "chris-auditor"
    aud = no_write_lines(build_write_boundary(gov, role="auditor", harness_root=ws), "auditor", task_id=f"{CARD}R")
    _show("---- 件③ 审计体禁写行(write_boundary.no_write_lines)----\n" + "\n".join(aud))
    assert [ln.split("`")[0].split("(")[0] for ln in aud] == ["禁写治理根 ", "产品仓只读", "禁写工位 "]
    assert f"`{ws}`" in aud[2] and f"/{CARD}R`" in aud[0]
    src = (AIPOS / "subagent_kickoff.py").read_text(encoding="utf-8")
    assert "no_write_lines(" in src and "禁写工位" not in src
