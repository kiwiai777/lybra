"""AIPOS-F87 件①②③ 夹具: 卡面 YAML 根治(写入侧单源+写后回读)·存量识别与保值规整·开工选卡由产品给出。

靶场复用 F73D/F78/F78C 既有靶场(_card / _single_gov / _ensure_worktree / EXEC), 坏卡按真实现场三张坏卡同一模式
(`result_summary: **…**` 未转义, F42 claimed / F22·F22B completed)合成, 不读真实治理根。

件① 写入侧: 交回路径(return_task 预览 = 确认阶段原样落盘的卡文本)怪值靶场逐字还原; 记录侧原手拼写入点(修订记录 / 部署记录 /
     令牌轮换记录 / 治理条目头 / 门进度事件 / RETURN 骨架; launch-check 事件随模块 F91 退役)怪值可解析且逐字还原(main 上为红);
     写后回读校验: 序列化产出与待写值不一致 = 拒写且卡不落盘; 卡字段序只剩一份定义。
件② lint FRONTMATTER_INVALID 点名坏卡; state repair dry-run 不落盘; 落盘后除被规整行外逐字节不变、全部字段值语义不变、
     队列目录/文件名不变、写 events/<ID>/event_repair_*.md; 无法安全规整 = unresolved 拒改且非零退出; queue repair 同一实现。
件③ my-tasks next_card: [无工作树旧卡, 有工作树新卡] 选新卡; 都可开工选最近认领; 全不可选给原因列表; planGo 原样转述。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import EXEC  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _card, _make_gov  # noqa: E402
from test_aipos_f78c_card_repo import _single_gov  # noqa: E402
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter  # noqa: E402
from tools.aipos_cli.next_resolver import _ensure_worktree  # noqa: E402

GO_TS = REPO_ROOT / "agents" / "harness" / "pi" / "_shared" / "extensions" / "go.ts"
GATE_TEXT_RE = re.compile(r"lybra next|next --run|lybra_\w+|queue claim|queue return")

# 怪值靶场(卡面件① 列举: **加粗开头**、冒号、#、&、*、多行、前导空格; 另加真实坏卡原形与 YAML 类型陷阱)
POISON = {
    "bold_lead": "**加粗开头**",
    "bold_lead_real_f42": "**已实现 workspace_root 参数并验证路由生效，但测试中发现越权漏洞（token 项目域=lybra 却能操作别的工作区），已登记 finding。**",
    "bold_fullwidth_colon_real_f22": "**完成**：三大项全部实现并 commit（3aebdba）——enroll 落点按角色类判定。",
    "colon": "结论: 通过 key: value",
    "hash": "值 # 不是注释",
    "ampersand": "&anchor 开头",
    "star": "*alias 开头",
    "multiline": "第一行\n第二行\n  缩进第三行\n---\n第五行",
    "leading_space": "  前导空格",
    "quotes": "'单引号' 与 \"双引号\"",
    "bool_trap": "true",
    "null_trap": "null",
    "int_trap": "123",
    "flow_trap": "[不是列表] {不是映射}",
}


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _fm_of(text: str) -> tuple[dict, str, list]:
    return parse_markdown_frontmatter(text)


# ===========================================================================
# 件① 写入侧单源 + 写后回读
# ===========================================================================

def _claimed_card_for_return(root: Path) -> tuple[str, str, str, Path]:
    """与 tools/aipos_cli/tests/test_board_adapter_execute_integration 同形的最小交回靶场(卡经单源渲染)。"""
    from tools.aipos_cli.queue_mutation import render_task_markdown

    for state in ("pending", "claimed", "completed", "blocked"):
        (root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
    task_id, session_id, claim_id = "AIPOS-F87V", "session_AIPOS-F87V_x", "claim_AIPOS-F87V_x"
    for rel in (f"5_tasks/records/sessions/{task_id}", f"5_tasks/records/claims/{task_id}"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / f"5_tasks/records/sessions/{task_id}/{session_id}.md").write_text(
        f"---\nrecord_type: session_record\ntask_id: {task_id}\nsession_id: {session_id}\n---\n", encoding="utf-8")
    (root / f"5_tasks/records/claims/{task_id}/{claim_id}.md").write_text(
        f"---\nrecord_type: claim_record\ntask_id: {task_id}\nclaim_id: {claim_id}\n---\n", encoding="utf-8")
    meta = {
        "task_id": task_id, "title": "F87 怪值靶场", "project": "lybra", "task_mode": "code", "task_class": "simple",
        "status": "claimed", "assigned_to": EXEC, "agent_instance": EXEC, "context_bundle": "default", "priority": "high",
        "created_by": "advisor", "needs_owner": False, "output_target": "tools/", "artifact_policy": "formal_write",
        "claimed_by": EXEC, "claim_id": claim_id, "active_session_id": session_id, "claimed_at": "2026-10-02T07:00:00Z",
        "governance_refs": ["★依据: `**加粗**` 与 冒号: 值", "第二条 # 井号"],
        "lane": {"repo": "/srv/product", "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]},
    }
    card = root / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md"
    card.write_text(render_task_markdown(meta, "# 卡正文\n\n---\n正文里的分隔线不影响 frontmatter。\n"), encoding="utf-8")
    return task_id, session_id, claim_id, card


def test_item1_return_path_poison_values_land_parseable_and_verbatim(tmp_path):
    """交回路径: return_task 预览的 rendered_markdown = 确认阶段 `target.write_text(data["rendered_markdown"])` 原样落盘的卡文本。
    落盘后经产品读取口回读: 无告警, result_summary 逐字等于门记录的值, 其余字段(lane / governance_refs)不变。"""
    from tools.aipos_cli.board_adapter import return_task

    task_id, session_id, claim_id, card = _claimed_card_for_return(tmp_path)
    original_meta, original_body, _ = _fm_of(card.read_text(encoding="utf-8"))
    for name, value in POISON.items():
        resp = return_task(task_id=task_id, actor=EXEC, agent_instance=EXEC, owner_policy_ref="owner_policy:test",
                           claim_id=claim_id, active_session_id=session_id, result_summary=value, dry_run=True,
                           repo_root=tmp_path)
        data = resp["data"]
        intended = data["updated_frontmatter"]["result_summary"]
        # 门对 result_summary 的既有入参规整只有 strip()(board_adapter.return_task summary_text), 序列化不改值
        assert intended == value.strip(), (name, intended)
        card.write_text(data["rendered_markdown"], encoding="utf-8")  # = 确认阶段落盘
        meta, body, warnings = _fm_of(card.read_text(encoding="utf-8"))
        assert warnings == [], (name, warnings)
        assert meta["result_summary"] == intended, (name, meta["result_summary"])
        assert meta["lane"] == original_meta["lane"] and meta["governance_refs"] == original_meta["governance_refs"]
        assert body == original_body
        line = next(ln for ln in data["rendered_markdown"].splitlines() if ln.startswith("result_summary:"))
        _show(f"[件① 交回路径·{name}] {line[:110]}")
    # 前导空格: 门入参规整前的原值经单源序列化同样逐字还原(序列化层不丢空白)
    from tools.aipos_cli.queue_mutation import render_task_markdown

    text = render_task_markdown({"task_id": task_id, "result_summary": POISON["leading_space"]}, "x")
    assert _fm_of(text)[0]["result_summary"] == POISON["leading_space"]


def _poison_value() -> str:
    return "**加粗开头**: 冒号 # 井号 & *星"


def test_item1_amend_record_and_card_with_poison_reason_parseable(tmp_path, monkeypatch):
    """修订(queue amend)路径: 原 f-string 手拼修订记录, 理由以 ** 起头即写坏(main 红)。现记录与卡均经单源。"""
    from tools.aipos_cli.board_adapter import amend_task

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    card = _card(gov, "AIPOS-F87A", "pending")
    reason = _poison_value()
    resp = amend_task(task_id="AIPOS-F87A", actor="advisor.lybra.test", amendments={"title": "**新标题**: 改"},
                      amendment_reason=reason, dry_run=False, repo_root=gov)
    assert resp.get("ok") is True, resp
    records = sorted((gov / "5_tasks" / "records" / "amendments" / "AIPOS-F87A").glob("*.md"))
    assert len(records) == 1
    rec_meta, _b, rec_warn = _fm_of(records[0].read_text(encoding="utf-8"))
    _show(f"[件① 修订记录] {records[0].name} reason={rec_meta.get('reason')!r} warnings={rec_warn}")
    assert rec_warn == [] and rec_meta["reason"] == reason and rec_meta["record_type"] == "amendment_record"
    card_meta, _b2, card_warn = _fm_of(card.read_text(encoding="utf-8"))
    assert card_warn == [] and card_meta["title"] == "**新标题**: 改"


def test_item1_record_side_writers_poison_parseable(tmp_path, monkeypatch):
    """记录侧原手拼写入点(复查报告 M11 列举, 车道内): 怪值写出后均可解析且逐字还原。"""
    value = _poison_value()

    from tools.aipos_cli.deployment_record import build_deployment_record, render_record_markdown

    dep = build_deployment_record(commit="a" * 40, actor="advisor.lybra.test", authorization_type="dev_override",
                                  authorization_ref=value, deployed_at="2026-10-02T00:00:00Z")
    meta, _b, w = _fm_of(render_record_markdown(dep))
    assert w == [] and meta["authorization_ref"] == value and meta["dev_override_reason"] == value

    from tools.aipos_cli import token_rotation

    monkeypatch.setattr(token_rotation, "_records_dir", lambda ws: tmp_path / "rot")
    path = token_rotation._write_record(tmp_path, record_type="token_rotation", filename="rotation_x.md",
                                        frontmatter={"record_type": "token_rotation", "note": value, "count": 2},
                                        body_lines=["# body"])
    meta, _b, w = _fm_of(path.read_text(encoding="utf-8"))
    assert w == [] and meta["note"] == value and meta["count"] == 2

    from tools.aipos_cli.governance_add import _render_frontmatter

    block = _render_frontmatter({"status": "active", "title": value, "superseded_by": None})
    meta, _b, w = _fm_of(block + "\n# x\n")
    assert w == [] and meta == {"status": "active", "title": value, "superseded_by": None}

    # launch-check 事件写入器随 agent_launch_check 模块 AIPOS-F91 退役删除, 本段随删

    from tools.mcp_server.tools import lybra_task_progress

    ws = tmp_path / "gate-ws"
    (ws / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    token = json.dumps({"role": "executor", "operations": ["task_progress"], "token_ref": "test-executor",
                        "expires_at": "2999-01-01T00:00:00Z"})
    with patch.dict(os.environ, {"LYBRA_CAPABILITY_TOKEN": token}), patch("tools.mcp_server.tools._repo_root", return_value=ws):
        res = lybra_task_progress({"task_id": "AIPOS-F87P", "event_type": "progress", "actor": EXEC, "summary": value})
    sc = res.get("structuredContent", {})
    assert sc.get("ok") is True, sc
    meta, _b, w = _fm_of((ws / sc["event_file"]).read_text(encoding="utf-8"))
    assert w == [] and meta["summary"] == value

    from tools.aipos_cli.record_writer import build_return_skeleton_markdown

    meta, _b, w = _fm_of(build_return_skeleton_markdown("AIPOS-F87S"))
    assert w == [] and meta["branch"] == "(待填写: 卡分支名 card/AIPOS-F87S)"  # AIPOS-F93 件①: 说明读声明 field_hints
    _show("[件① 记录侧] 部署记录 / 令牌轮换记录 / 治理条目头 / 门进度事件 / RETURN 骨架: 怪值均逐字还原")


def test_item1_write_after_readback_refuses_and_card_not_landed(tmp_path, monkeypatch):
    """写后回读校验: 序列化产出可解析但值被改(F46 只验类型的自检拦不住)→ FrontmatterRoundtripError; 经真实写卡路径(amend)
    时卡与记录均不落盘(卡字节不变、无修订记录)。"""
    from tools.aipos_cli import record_writer
    from tools.aipos_cli.board_adapter import amend_task
    from tools.aipos_cli.record_writer import FrontmatterRoundtripError, render_markdown

    real_dump = record_writer._dump_frontmatter_yaml

    def lossy_dump(ordered_meta):
        text = real_dump(ordered_meta)
        return text.replace("**", "")  # 仍可解析、仍是字符串, 但值被改

    monkeypatch.setattr(record_writer, "_dump_frontmatter_yaml", lossy_dump)
    with pytest.raises(FrontmatterRoundtripError) as exc:
        render_markdown({"task_id": "X", "result_summary": "**加粗**"}, "body")
    _show(f"[件① 写后回读拒写] {exc.value}")

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    card = _card(gov, "AIPOS-F87R", "pending")
    before = card.read_bytes()
    resp = amend_task(task_id="AIPOS-F87R", actor="advisor.lybra.test", amendments={"title": "**改**"},
                      amendment_reason="**理由**", dry_run=False, repo_root=gov)
    assert resp.get("ok") is not True, resp
    assert card.read_bytes() == before
    assert not list((gov / "5_tasks" / "records" / "amendments").rglob("*.md"))


CARD_ORDER_UNDECLARED_IN_CARD_SCHEMA = {
    "recurrence", "blocked_by", "blocked_at", "block_reason", "reopened_by", "reopen_reason",
    "withdrawn_by", "withdrawn_at", "withdrawal_reason",
}


def test_item1_card_field_order_single_definition():
    """卡字段序两份定义收一份(record_writer.CARD_FRONTMATTER_ORDER); 其中 card.schema 未声明的键是登记的 schema 缺口。"""
    from tools.aipos_cli import draft_writer, queue_mutation, record_writer

    assert draft_writer.FRONTMATTER_ORDER is record_writer.CARD_FRONTMATTER_ORDER
    assert queue_mutation.FRONTMATTER_ORDER is record_writer.CARD_FRONTMATTER_ORDER
    defs = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "tools").rglob("*.py")
        if "/tests/" not in str(p) and re.search(r'^(CARD_)?FRONTMATTER_ORDER = \[\n\s+"task_id"', p.read_text(encoding="utf-8"), re.M)
    ]
    assert defs == ["tools/aipos_cli/record_writer.py"], defs
    declared = set(json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))["fields"])
    undeclared = {k for k in record_writer.CARD_FRONTMATTER_ORDER if k not in declared}
    assert undeclared == CARD_ORDER_UNDECLARED_IN_CARD_SCHEMA, undeclared
    # 既有落盘序稳定: 以此序写过的卡再写一遍, 字节不变(无全量重排)
    meta = {k: f"v-{k}" for k in record_writer.CARD_FRONTMATTER_ORDER[:12]}
    meta.update({"anchor_refs": ["N1"], "lane": {"repo": "r", "paths": ["a/"]}})
    once = queue_mutation.render_task_markdown(meta, "# b\n")
    again = queue_mutation.render_task_markdown(_fm_of(once)[0], _fm_of(once)[1])
    assert once == again


# ===========================================================================
# 件② lint FRONTMATTER_INVALID + repair 保值规整
# ===========================================================================

def _bad_card_text(task_id: str, status: str, summary_line: str, tail: list[str] | None = None) -> str:
    """真实三张坏卡同一模式: 门曾把 result_summary 以裸文本写进卡面(值以 ** 开头未转义)。其余行按单源格式。"""
    lines = [
        "---",
        f"task_id: {task_id}",
        f"title: {task_id} 坏卡靶场",
        "project: lybra",
        f"assigned_to: {EXEC}",
        f"agent_instance: {EXEC}",
        "task_mode: code",
        "priority: high",
        f"status: {status}",
        "needs_owner: false",
        "claimed_at: '2026-08-26T07:00:00Z'",
        "governance_refs:",
        "- '★依据: `**加粗**` 与 冒号: 值'",
        "- 第二条 plain",
        "lane:",
        "  repo: /srv/product",
        "  paths:",
        "  - tools/aipos_cli/",
        "  roles:",
        "  - executor",
        "owner_verify: true",
        summary_line,
        *(tail or ["return_event_ref: return_x_20260826_155033_exec"]),
        "---",
        f"# {task_id}",
        "",
        "正文: **加粗** 与 key: value 都在正文里, 不受影响。",
        "",
    ]
    return "\n".join(lines)


BAD_F42_LINE = "result_summary: **已实现 workspace_root 参数并验证路由生效，但测试中发现越权漏洞，已登记 finding。**"
BAD_F22_LINE = "result_summary: **完成**：三大项全部实现并 commit（3aebdba）——enroll 落点按角色类判定。"
BAD_F22B_LINE = "result_summary: **代码完成，Gate Confirm 被阻塞**。三大项均已实现并 commit，dry_run 通过 (WARN)。"


def _bad_gov(tmp_path, monkeypatch, shape: str = "lybra") -> tuple[Path, dict[str, Path]]:
    gov = _make_gov(tmp_path, monkeypatch, shape=shape)
    cards = {
        "AIPOS-F42X": gov / "5_tasks/queue/claimed/aipos-f42x.md",
        "AIPOS-F22X": gov / "5_tasks/queue/completed/aipos-f22x.md",
        "AIPOS-F22BX": gov / "5_tasks/queue/completed/aipos-f22bx.md",
    }
    cards["AIPOS-F42X"].write_text(_bad_card_text("AIPOS-F42X", "claimed", BAD_F42_LINE), encoding="utf-8")
    cards["AIPOS-F22X"].write_text(_bad_card_text("AIPOS-F22X", "completed", BAD_F22_LINE), encoding="utf-8")
    cards["AIPOS-F22BX"].write_text(_bad_card_text("AIPOS-F22BX", "completed", BAD_F22B_LINE), encoding="utf-8")
    _card(gov, "AIPOS-F87OK", "claimed")
    return gov, cards


def _cli(argv: list[str], capsys) -> tuple[int, str]:
    from tools.aipos_cli.aipos_cli import main

    capsys.readouterr()
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_item2_lint_names_exactly_the_three_bad_cards(tmp_path, monkeypatch, capsys):
    gov, cards = _bad_gov(tmp_path, monkeypatch)
    rc, out = _cli(["state", "lint", "--workspace-root", str(gov), "--json"], capsys)
    issues = [i for i in json.loads(out)["issues"] if i.get("code") == "FRONTMATTER_INVALID"]
    for issue in issues:
        _show(f"[件② lint] [{issue['severity']}] {issue['task_id']}: {issue['message']}")
    assert rc == 1
    assert sorted(i["task_id"] for i in issues) == sorted(cards)
    assert all(i["severity"] == "ERROR" and f"lybra state repair --task-id {i['task_id']}" in i["message"] for i in issues)


@pytest.mark.parametrize("shape", ["lybra", "chris"])
def test_item2_repair_dry_run_then_apply_is_value_preserving_byte_exact(tmp_path, monkeypatch, capsys, shape):
    gov, cards = _bad_gov(tmp_path, monkeypatch, shape=shape)
    for task_id, card in cards.items():
        original = card.read_text(encoding="utf-8")
        bad_line = next(ln for ln in original.split("\n") if ln.startswith("result_summary:"))
        raw_value = bad_line.split(": ", 1)[1]

        rc, out = _cli(["state", "repair", "--task-id", task_id, "--workspace-root", str(gov), "--dry-run"], capsys)
        _show(f"[件② repair dry-run {shape} {task_id}] rc={rc}\n{out.rstrip()}")
        assert rc == 0 and card.read_text(encoding="utf-8") == original  # dry-run 不落盘
        assert not (gov / "5_tasks/records/events" / task_id).exists()

        rc, out = _cli(["state", "repair", "--task-id", task_id, "--workspace-root", str(gov), "--json"], capsys)
        result = json.loads(out)
        assert rc == 0 and result["repaired"] is True, result
        repaired = card.read_text(encoding="utf-8")
        # 队列状态不变: 原目录原文件名
        assert card.exists() and len(list(gov.glob(f"5_tasks/queue/*/{card.name}"))) == 1
        # 逐字节: 只有被规整行变化, 且只加引号
        before_lines, after_lines = original.split("\n"), repaired.split("\n")
        changed = [i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b]
        assert len(before_lines) == len(after_lines) and changed == [before_lines.index(bad_line)]
        new_line = after_lines[changed[0]]
        assert new_line == f"result_summary: '{raw_value}'", new_line
        # 值语义: 被规整字段 = 原行文本; 其余字段 = 原文去掉该行的解析结果; 正文不变
        import yaml

        meta, body, warnings = _fm_of(repaired)
        assert warnings == [] and meta["result_summary"] == raw_value
        rest = yaml.safe_load("\n".join(ln for ln in original.split("\n")[1:before_lines.index("---", 1)] if ln != bad_line))
        assert {k: v for k, v in meta.items() if k != "result_summary"} == rest
        assert body == _fm_of(original)[1]
        # 修复记录
        records = sorted((gov / "5_tasks/records/events" / task_id).glob("event_repair_*.md"))
        assert len(records) == 1
        rec, rec_body, rec_warn = _fm_of(records[0].read_text(encoding="utf-8"))
        assert rec_warn == [] and rec["event_type"] == "frontmatter_repair"
        assert rec["sha256_before"] == hashlib.sha256(original.encode()).hexdigest()
        assert rec["sha256_after"] == hashlib.sha256(repaired.encode()).hexdigest()
        assert f"- {bad_line}" in rec_body and f"+ {new_line}" in rec_body
        _show(f"[件② repair 落盘 {shape} {task_id}] 改动行 L{changed[0] + 1}: {new_line[:70]}… 记录 {records[0].name}")
        # 规整后 lint 不再点名, 再跑 repair = 不适用(幂等)
        rc, out = _cli(["state", "lint", "--workspace-root", str(gov), "--task-id", task_id, "--json"], capsys)
        assert not [i for i in json.loads(out)["issues"] if i.get("code") == "FRONTMATTER_INVALID"]


def test_item2_unresolvable_is_refused_without_write(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    cases = {
        "AIPOS-F87U1": ["result_summary: [未闭合的流式列表, 引号化会改义"],
        "AIPOS-F87U2": ["result_summary: **加粗**", "  续行: 缩进子结构"],
        "AIPOS-F87U3": ["lane:", "  repo: **bad nested**"],
    }
    for task_id, lines in cases.items():
        card = gov / "5_tasks/queue/claimed" / f"{task_id.lower()}.md"
        text = "\n".join(["---", f"task_id: {task_id}", f"assigned_to: {EXEC}", "status: claimed", *lines, "---", "# b", ""])
        card.write_text(text, encoding="utf-8")
        rc, out = _cli(["state", "repair", "--task-id", task_id, "--workspace-root", str(gov)], capsys)
        _show(f"[件② unresolved {task_id}] rc={rc} {out.strip()[:200]}")
        assert rc == 1 and "unresolved" in out and f"✗ 拒改 {task_id}" in out
        assert card.read_text(encoding="utf-8") == text
        assert not (gov / "5_tasks/records/events" / task_id).exists()


def test_item2_queue_repair_entry_delegates_to_same_implementation(tmp_path, monkeypatch):
    """F65C `queue repair` 入口与 state repair 同一实现(原正则规整退役)。"""
    from tools.aipos_cli.queue_mutation import repair_bad_frontmatter

    gov, cards = _bad_gov(tmp_path, monkeypatch)
    res = repair_bad_frontmatter(gov, "AIPOS-F42X", actor="advisor.lybra.test", dry_run=True)
    assert res["verdict"] == "OK" and res["frontmatter_repair"]["applicable"] is True
    assert res["frontmatter_repair"]["repairs"][0]["after"].startswith("result_summary: '**已实现")
    import inspect

    from tools.aipos_cli import queue_mutation

    src = inspect.getsource(queue_mutation.repair_bad_frontmatter)
    assert "repair_frontmatter_invalid" in src and "re.match" not in src and "write_text" not in src


# ===========================================================================
# 件③ next_card 由产品选卡
# ===========================================================================

def _my_tasks(gov: Path, capsys) -> dict:
    rc, out = _cli(["--workspace-root", str(gov), "my-tasks", "--actor", EXEC, "--json"], capsys)
    assert rc == 0, out
    return json.loads(out)


def _plan_go(data: dict, tmp_path: Path) -> dict:
    node = shutil.which("node")
    assert node, "node 不在 PATH: go.ts 夹具需 Node ≥ 22"
    data_file = tmp_path / "my-tasks.json"
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    script = (
        f"import {{ planGo }} from {json.dumps(GO_TS.as_uri())};\n"
        "import { readFileSync } from 'node:fs';\n"
        f"process.stdout.write(JSON.stringify(planGo(JSON.parse(readFileSync({json.dumps(str(data_file))}, 'utf-8')))));\n"
    )
    proc = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_item3_old_card_without_worktree_vs_new_card_with_worktree_picks_new(tmp_path, monkeypatch, capsys):
    gov, _repo = _single_gov(tmp_path, monkeypatch)
    # 旧卡认领更晚也不行: 判据先看可开工(工作树已建), 再看认领时间
    _card(gov, "AIPOS-F87OLD", "claimed", extra={"claimed_at": "2026-10-02T09:00:00Z"})
    _card(gov, "AIPOS-F87NEW", "claimed", extra={"claimed_at": "2026-10-02T08:00:00Z"})
    assert _ensure_worktree(gov, "AIPOS-F87NEW")["ok"]
    data = _my_tasks(gov, capsys)
    _show("[件③ 旧卡无树/新卡有树] " + json.dumps({k: data[k] for k in ("next_card", "next_card_excluded")}, ensure_ascii=False))
    assert data["next_card"]["task_id"] == "AIPOS-F87NEW"
    assert data["next_card_excluded"] == [{
        "task_id": "AIPOS-F87OLD", "code": "WORKTREE_NOT_CREATED",
        "reason": next(t for t in data["tasks"] if t["task_id"] == "AIPOS-F87OLD")["worktree_refusal"]["reason"],
    }]
    assert data["next_card_rule"]["source"].startswith("AIPOS-F87")
    plan = _plan_go(data, tmp_path)
    assert plan["kind"] == "kickoff" and plan["taskId"] == "AIPOS-F87NEW"
    assert f"工作树路径: {data['next_card']['worktree_path']}" in plan["kickoff"]


def test_item3_multiple_ready_cards_most_recent_claim_wins(tmp_path, monkeypatch, capsys):
    gov, _repo = _single_gov(tmp_path, monkeypatch)
    for task_id, at in (("AIPOS-F87A1", "2026-10-01T00:00:00Z"), ("AIPOS-F87A2", "2026-10-02T00:00:00Z"),
                        ("AIPOS-F87A3", "2026-09-30T00:00:00Z")):
        _card(gov, task_id, "claimed", extra={"claimed_at": at})
        assert _ensure_worktree(gov, task_id)["ok"]
    data = _my_tasks(gov, capsys)
    _show("[件③ 多张可开工] " + json.dumps({k: data[k] for k in ("next_card", "next_card_excluded")}, ensure_ascii=False))
    assert data["next_card"]["task_id"] == "AIPOS-F87A2"
    assert [(e["task_id"], e["code"]) for e in data["next_card_excluded"]] == [
        ("AIPOS-F87A1", "NOT_MOST_RECENT"), ("AIPOS-F87A3", "NOT_MOST_RECENT")]


def test_item3_none_selectable_gives_reasons_and_go_relays_verbatim(tmp_path, monkeypatch, capsys):
    gov, _repo = _single_gov(tmp_path, monkeypatch)
    _card(gov, "AIPOS-F87NT", "claimed", extra={"claimed_at": "2026-10-02T00:00:00Z"})
    bad = gov / "5_tasks/queue/claimed/aipos-f87bad.md"
    bad.write_text(_bad_card_text("AIPOS-F87BAD", "claimed", BAD_F42_LINE), encoding="utf-8")
    assert _ensure_worktree(gov, "AIPOS-F87BAD")["ok"]  # 坏卡即便工作树已建也不可选(卡面判据先于工作树)
    _card(gov, "AIPOS-F87PD", "pending")
    data = _my_tasks(gov, capsys)
    _show("[件③ 全不可选] " + json.dumps({k: data[k] for k in ("next_card", "next_card_excluded")}, ensure_ascii=False))
    assert data["next_card"] is None
    codes = {e["task_id"]: e["code"] for e in data["next_card_excluded"]}
    assert codes == {"AIPOS-F87BAD": "FRONTMATTER_INVALID", "AIPOS-F87NT": "WORKTREE_NOT_CREATED"}
    for e in data["next_card_excluded"]:
        assert not GATE_TEXT_RE.search(e["reason"]), e
    plan = _plan_go(data, tmp_path)
    _show("[件③ planGo 原文] " + plan.get("message", ""))
    assert plan["kind"] == "refused"
    for e in data["next_card_excluded"]:
        assert f"- {e['task_id']} {e['code']}: {e['reason']}" in plan["message"]
    # 无 claimed 卡 = 等待分配(none), 不是拒
    gov2, _ = _single_gov(tmp_path / "empty", monkeypatch)
    _card(gov2, "AIPOS-F87P2", "pending")
    empty = _my_tasks(gov2, capsys)
    assert empty["next_card"] is None and empty["next_card_excluded"] == []
    assert _plan_go(empty, tmp_path)["kind"] == "none"


def test_item3_go_ts_has_no_local_card_picking():
    src = GO_TS.read_text(encoding="utf-8")
    assert "claimedTasks[0]" not in src and "claimedTasks" not in src
    assert not re.search(r"tasks\s*\.\s*filter|\[0\]", src), "go.ts 不得自行筛选/取首张"
    assert "next_card" in src and "next_card_excluded" in src
