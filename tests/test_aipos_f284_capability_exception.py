"""AIPOS-F284 — 主机能力声明: 需要本机图形会话 / 钥匙串的卡声明所需能力, 由具备该能力主机上的顾问会话派子 agent 执行
(「非代码一律 pi」的唯一声明化例外, 留痕, 审计仍 pi; Owner 11-02 裁定 governance/decision_log/2026-11/2026-11-02-图形会话签名构建执行方.md)。

件① 声明: enums.schema host_capability(值域 + 双语 texts) / card.schema requires_capabilities / project.json execution.capability_executors
    (唯一写入口 set-execution --capability-executor; 只允许子 agent 模式实例, 绑定登记主机)。
件② 判定: 例外只在 execution_division._strict_refusal 一处(五处同判); 无实例具备 = 拒 + 出口; 不带能力的非代码卡仍 pi; 审计卡豁免。
件③ 留痕与出口: 认领 / 交回记录 capability_exception; loop 手工提示含主机与能力; 开工稿「图形会话终端内执行, 不经 ssh」; 看板卡行显示所需能力。

靶场: 临时治理根 / 临时 HOME(复用 F78 / F143 / F256 靶场构件, 禁第二份); 不连真实门、不读真实凭据、不起 pi、不起子进程。
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f143_execution_mode import AUDITOR, DRIVER, PIX, SUB, _cli, _land, _meta  # noqa: E402  — 接入登记靶场唯一来源
from test_aipos_f256_execution_division import _gov  # noqa: E402  — F256 分工靶场唯一来源
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter  # noqa: E402

MAC = "exec.lybra.machost"   # 具备图形会话 + 登录钥匙串的子 agent 执行实例(登记主机 machost)
HOST = "machost"
CAPS = ["gui_session", "login_keychain"]
TID = "F284-SIGN"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _land_mac(gov: Path) -> None:
    from tools.aipos_cli.enrollment import subagent_land_detail

    _land(gov, MAC, "executor", subagent_land_detail(host=HOST, harness="claude-code", mode="subagent", files=["connection.json"]))


def _capable_gov(tmp_path, monkeypatch, *, declare: bool = True, git: bool = False) -> Path:
    from tools.aipos_cli.enrollment import set_project_execution

    gov = _gov(tmp_path, monkeypatch, git=git)
    _land_mac(gov)
    if declare:
        set_project_execution(gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=PIX,
                              capability_executors=[f"{MAC}={','.join(CAPS)}"], dry_run=False)
    return gov


def _cap_meta(task_id: str = TID, **extra) -> dict:
    return _meta(task_id, task_mode="validation", requires_capabilities=["login_keychain"], **extra)


def _f284(reasons: list) -> list[str]:
    return [str(r) for r in reasons if "AIPOS-F284" in str(r) or "AIPOS-F256" in str(r)]


# ===========================================================================
# 件① 声明与写入口
# ===========================================================================

def test_item1_declaration_and_set_execution_binds_enrolled_host(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import set_project_execution
    from tools.aipos_cli.i18n import pick
    from tools.aipos_cli.next_resolver import host_capability_declaration
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError, project_capability_executors, project_execution
    from tools.schema_loader import get_enum_values

    decl = host_capability_declaration()
    assert list(get_enum_values("host_capability")) == CAPS and decl["card_field"] == "requires_capabilities"
    assert decl["exempt_kinds"] == ["pi_auditor"]
    gov = _capable_gov(tmp_path, monkeypatch)
    declared = project_capability_executors(gov)
    _show(f"[件①·声明写入] execution.capability_executors = {json.dumps(declared, ensure_ascii=False)}")
    assert declared == {MAC: {"host": HOST, "capabilities": CAPS}}
    assert "capability_executors" not in project_execution(gov)  # 视图不含(读取口另设, 同 pi_models)
    # 未给 = 原值保留(整段重声明执行方式不清能力声明)
    set_project_execution(gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=PIX, dry_run=False)
    assert project_capability_executors(gov) == declared
    # 写前核身份: 非子 agent 模式 / 未接入 / 与审计者同一实例 / 值不合 = 零写入拒
    import socket

    ws = "exec.lybra.piws2"  # 工位模式登记(有工位, 非子 agent 模式)
    _land(gov, ws, "executor", f"host={socket.gethostname()} workstation={gov.parent / 'ws-pi2'} files=['connection.json']")
    cases = {"工位模式": f"{ws}=gui_session", "pi 执行工位": f"{PIX}=gui_session", "未接入": "exec.lybra.ghost=gui_session", "审计者": f"{AUDITOR}=gui_session",
             "值不合": f"{MAC}=gpu"}
    for label, spec in cases.items():
        try:
            set_project_execution(gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=PIX, capability_executors=[spec], dry_run=False)
        except ExecutionDeclarationError as exc:
            _show(f"[件①·写入拒·{label}] {exc.code}: {exc.problems[0]}\n    en: {pick(exc.problems[0], 'en')}")
            assert exc.code == "EXECUTION_CAPABILITY_INSTANCE_INVALID"
        else:
            raise AssertionError(f"{label} 应拒")
    assert project_capability_executors(gov) == declared
    # <实例>= 留空 = 删除
    set_project_execution(gov, subagent_executor=SUB, auditor=AUDITOR, pi_executor=PIX, capability_executors=[f"{MAC}="], dry_run=False)
    assert project_capability_executors(gov) == {}
    assert "capability_executors" not in json.loads((gov / "project.json").read_text(encoding="utf-8"))["execution"]


# ===========================================================================
# 验收①: 有实例具备能力 → 五处判定执行方 = 该实例, 记录留痕原文
# ===========================================================================

def test_accept1_capable_instance_five_stages_and_trace(tmp_path, monkeypatch):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.artifact_ingest import validate_task_artifact
    from tools.aipos_cli.board_adapter import _mcp_claim_record_plan, _mcp_return_record_plan
    from tools.aipos_cli.draft_writer import create_draft, publish_identity_refusals
    from tools.aipos_cli.queue_mutation import mutate_queue_task
    from tools.aipos_cli.subagent_kickoff import KickoffRefused, render_kickoff_brief

    gov = _capable_gov(tmp_path, monkeypatch)
    # 起草: 卡稿不写执行方 → 起草缺省派具备能力的实例(不派 pi)
    draft = create_draft(gov, _cap_meta(), "## Goal\n\nx\n", dry_run=True)
    fm = parse_markdown_frontmatter(draft["rendered_markdown"])[0]
    _show(f"[验收①·起草] assigned_to={fm.get('assigned_to')} harness={fm.get('harness')} agent_instance={fm.get('agent_instance')} "
          f"audit_by={fm.get('audit_by')}; 分工拒因={_f284(draft['blocking_reasons'])}")
    assert (fm["assigned_to"], fm["agent_instance"], fm["harness"]) == (MAC, MAC, "claude-code") and fm["audit_by"] == AUDITOR
    assert not _f284(draft["blocking_reasons"])
    # 发卡
    refusals, view = publish_identity_refusals(gov, dict(fm))
    _show(f"[验收①·发卡] 分工拒因={_f284(refusals)} assigned_to 核验={view['instances'].get('assigned_to')}")
    assert not _f284(refusals) and view["instances"]["assigned_to"]["instance"] == MAC
    # 认领(CLI 认领执行器预览 + 记录预览)
    extra = {"harness": "claude-code", "requires_capabilities": ["login_keychain"]}
    f78._card(gov, TID, "pending", assigned=MAC, task_mode="validation", extra=extra)
    claim = mutate_queue_task(gov, "claim", task_id=TID, actor=MAC, dry_run=True, profiles=load_agent_profiles(gov), with_records=True)
    _show(f"[验收①·认领] 分工拒因={_f284(claim['blocking_reasons'])}")
    assert not _f284(claim["blocking_reasons"])
    claim_log = claim["record_previews"][0]["rendered_markdown"]
    trace_line = parse_markdown_frontmatter(claim_log)[0]["capability_exception"]
    _show(f"[验收①·认领记录留痕(CLI 认领日志 frontmatter capability_exception)] {trace_line}")
    assert f"实例={MAC}" in trace_line and "requires_capabilities=login_keychain" in trace_line and HOST in trace_line
    mcp = _mcp_claim_record_plan(repo_root=gov, task_id=TID, task_path="5_tasks/queue/claimed/f284-sign.md", actor=MAC,
                                 canonical_agent_instance=MAC, owner_policy_ref="pol_x",
                                 updated_metadata={**parse_markdown_frontmatter((gov / "5_tasks/queue/pending/f284-sign.md").read_text())[0],
                                                   "claim_id": "claim_F284-SIGN_x", "active_session_id": "session_F284-SIGN_x",
                                                   "claimed_at": "2026-11-02T00:00:00Z"})
    mcp_claim = mcp["record_previews"][0]["rendered_markdown"]
    assert parse_markdown_frontmatter(mcp_claim)[0]["capability_exception"] and f"- 能力例外: requires_capabilities=login_keychain, 实例={MAC}" in mcp_claim
    _show("[验收①·认领记录留痕(门认领记录正文)] " + next(line for line in mcp_claim.splitlines() if line.startswith("- 能力例外")))
    # 开工稿(认领后; validation 卡停在本靶场未布置的报告落点前提上 = 分工判定已放行)
    (gov / "5_tasks" / "queue" / "pending" / "f284-sign.md").unlink()
    f78._card(gov, TID, "claimed", assigned=MAC, task_mode="validation", extra={**extra, "claimed_by": MAC})
    try:
        render_kickoff_brief(gov, TID)
        kickoff = "放行(出稿)"
    except KickoffRefused as exc:
        kickoff = exc.message if exc.code == "KICKOFF_EXECUTOR_DIVISION" else f"放行(分工; 后续 {exc.code})"
    _show(f"[验收①·开工稿] {kickoff}")
    assert kickoff.startswith("放行")
    # 交回入口
    f78._write(gov / "task_cards" / TID / "RETURN.md", f78._return_text(TID, f78.SHA_A, f78.SHA_A))
    check = validate_task_artifact(TID, gov)
    _show(f"[验收①·交回] category={check['category']}(分工判定已放行; 后续为本靶场未布置的前提)")
    assert check["category"] not in ("EXECUTOR_DIVISION_REFUSED", "EXECUTOR_CAPABILITY_REFUSED", "EXECUTOR_CAPABILITY_UNDECLARED")
    ret = _mcp_return_record_plan(repo_root=gov, task_id=TID, task_path="5_tasks/queue/claimed/f284-sign.md", actor=MAC,
                                  canonical_agent_instance=MAC, owner_policy_ref="pol_x",
                                  source_metadata={**parse_markdown_frontmatter((gov / "5_tasks/queue/claimed/f284-sign.md").read_text())[0],
                                                   "claim_id": "claim_F284-SIGN_x", "active_session_id": "session_F284-SIGN_x"},
                                  updated_metadata={"executor_completed_at": "2026-11-02T01:00:00Z"}, result_summary="done",
                                  artifact_refs=[], completion_report_ref=None)
    ret_md = next(p["rendered_markdown"] for p in ret["record_previews"] if p["record_type"] == "return_record")
    ret_line = parse_markdown_frontmatter(ret_md)[0]["capability_exception"]
    _show(f"[验收①·交回记录留痕(门交回记录 frontmatter capability_exception)] {ret_line}")
    assert f"实例={MAC}" in ret_line


def test_accept1_kickoff_line_and_executor_identity_matrix(tmp_path, monkeypatch):
    """开工稿全文(docs 能力卡可完整出稿)含所需能力与「图形会话终端内执行, 不经 ssh」; 非能力实例 / pi 认领能力卡 = 拒 + 改派出口。"""
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task
    from tools.aipos_cli.subagent_kickoff import render_kickoff_brief

    gov = _capable_gov(tmp_path, monkeypatch, git=True)
    f78._card(gov, "F284-DOC", "claimed", assigned=MAC, task_mode="docs",
              extra={"harness": "claude-code", "claimed_by": MAC, "requires_capabilities": CAPS})
    text = render_kickoff_brief(gov, "F284-DOC", lang="zh")["text"]
    line = next(x for x in text.splitlines() if "AIPOS-F284" in x)
    _show(f"[验收①·开工稿能力行] {line}")
    assert HOST in line and "图形会话终端内执行, 不经 ssh" in line and "登录钥匙串(login_keychain)" in line
    en = render_kickoff_brief(gov, "F284-DOC", lang="en")["text"]
    assert any("not over ssh" in x and HOST in x for x in en.splitlines())
    f78._card(gov, "F284-WRONG", "pending", assigned=SUB, task_mode="validation",
              extra={"harness": "claude-code", "requires_capabilities": ["login_keychain"]})
    for who in (SUB, PIX):
        claim = mutate_queue_task(gov, "claim", task_id="F284-WRONG", actor=who, dry_run=True, profiles=load_agent_profiles(gov))
        reason = next(r for r in claim["blocking_reasons"] if "EXECUTOR_CAPABILITY_REFUSED" in r)
        _show(f"[验收①·{who} 认领能力卡拒] {reason}")
        assert MAC in reason and "queue amend" in reason
    # 拒因出口照抄 → 改派后具备能力的实例认领通过
    command = re.search(r"(lybra --workspace-root \S+ queue amend .*?capable instance\")", str(reason)).group(1)
    rc, out, err = _cli(*shlex.split(command)[1:])
    _show(f"[验收①·照抄改派出口] exit {rc}")
    assert rc == 0, out + err
    ok = mutate_queue_task(gov, "claim", task_id="F284-WRONG", actor=MAC, dry_run=True, profiles=load_agent_profiles(gov))
    assert not _f284(ok["blocking_reasons"]), ok["blocking_reasons"]


# ===========================================================================
# 验收②: 无具备能力实例 = 发卡拒 + 出口原文(照出口声明后通过)
# ===========================================================================

def test_accept2_no_capable_instance_refused_with_exit(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft, publish_identity_refusals

    gov = _capable_gov(tmp_path, monkeypatch, declare=False, git=True)
    draft = create_draft(gov, _cap_meta(), "## Goal\n\nx\n", dry_run=True)
    reason = next(r for r in draft["blocking_reasons"] if "EXECUTOR_CAPABILITY_UNDECLARED" in r)
    _show(f"[验收②·起草拒] {reason}")
    refusals, _view = publish_identity_refusals(gov, _cap_meta(harness="claude-code", assigned_to=MAC, audit_by=AUDITOR))
    published = next(r for r in refusals if "EXECUTOR_CAPABILITY_UNDECLARED" in r)
    _show(f"[验收②·发卡拒(出口原文)] {published}")
    assert "--capability-executor <实例>=login_keychain" in published
    command = re.search(r"(lybra project set-execution .*?) --dry-run", published).group(1).replace("<实例>", MAC)
    rc, out, err = _cli(*shlex.split(command)[1:], "--actor", DRIVER, "--confirm")
    _show(f"[验收②·照出口声明] $ {command} --actor {DRIVER} --confirm\nexit {rc}\n{out[-400:]}{err}")
    assert rc == 0 and "落账: verdict=PASS" in out, out + err
    again, _view = publish_identity_refusals(gov, _cap_meta(harness="claude-code", assigned_to=MAC, audit_by=AUDITOR))
    assert not _f284(again), again


# ===========================================================================
# 验收③④: 不带能力的非代码卡仍 pi; 子 agent 接不带能力的非代码卡仍拒(F256 回归); 审计卡豁免
# ===========================================================================

def test_accept3_4_regressions_without_capabilities(tmp_path, monkeypatch):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.draft_writer import create_draft
    from tools.aipos_cli.execution_division import capability_exception, division_refusal
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    gov = _capable_gov(tmp_path, monkeypatch)
    plain = create_draft(gov, _meta("F284-PLAIN", task_mode="validation"), "## Goal\n\nx\n", dry_run=True)
    fm = parse_markdown_frontmatter(plain["rendered_markdown"])[0]
    _show(f"[验收③·不带能力的非代码卡] harness={fm['harness']} assigned_to={fm['assigned_to']}")
    assert (fm["harness"], fm["assigned_to"]) == ("pi", PIX)
    for index, who in enumerate((SUB, MAC)):
        f78._card(gov, f"F284-R{index}", "pending", assigned=who, task_mode="docs", extra={"harness": "claude-code"})
        claim = mutate_queue_task(gov, "claim", task_id=f"F284-R{index}", actor=who, dry_run=True, profiles=load_agent_profiles(gov))
        reason = next(r for r in claim["blocking_reasons"] if "EXECUTOR_DIVISION_REFUSED" in r)
        _show(f"[验收④·{who} 接不带能力的 docs 卡] {reason[:120]}…")
    audit = {"task_id": "F284-AR", "task_mode": "audit", "requires_capabilities": CAPS, "harness": "pi", "assigned_to": AUDITOR}
    assert capability_exception(gov, audit) is None and division_refusal(gov, audit, stage="claim", actor=AUDITOR) is None
    _show("[验收③·审计卡豁免] task_mode=audit 带能力 → 不适用能力例外, 仍按 pi 审计工位判(放行)")


# ===========================================================================
# 写面: 验证根 / 发布目标只认 pi 的判定对能力卡放行该实例
# ===========================================================================

def test_surface_class_allows_capable_instance_only(tmp_path, monkeypatch):
    from tools.aipos_cli.execution_division import surface_class_problem

    gov = _capable_gov(tmp_path, monkeypatch)
    f78._card(gov, TID, "claimed", assigned=MAC, task_mode="validation",
              extra={"harness": "claude-code", "claimed_by": MAC, "requires_capabilities": ["login_keychain"]})
    f78._card(gov, "F284-NOCAP", "claimed", assigned=MAC, task_mode="code", extra={"harness": "claude-code", "claimed_by": MAC})
    for surface in ("validation_root", "release_target"):
        assert surface_class_problem(gov, surface=surface, instance=MAC, task_id=TID) is None
        denied = surface_class_problem(gov, surface=surface, instance=MAC, task_id="F284-NOCAP")
        assert denied and denied["code"] == "SURFACE_EXECUTOR_CLASS"
        assert surface_class_problem(gov, surface=surface, instance=SUB, task_id=TID)["code"] == "SURFACE_EXECUTOR_CLASS"
    _show("[写面] 能力卡 + 具备能力实例 = 放行; 同实例无能力卡 / 非能力实例 = SURFACE_EXECUTOR_CLASS")


# ===========================================================================
# 验收⑤: loop 手工提示含主机与能力; 看板卡行显示所需能力
# ===========================================================================

def test_accept5_loop_manual_hint_and_board_row(tmp_path, monkeypatch):
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract, plan_launch
    from tools.aipos_cli.machine_zone import card_stats
    from web.board.readonly_pages import _card_rows, _Ctx

    gov = _capable_gov(tmp_path, monkeypatch)
    monkeypatch.setenv("LYBRA_LANG", "zh")
    f78._card(gov, TID, "claimed", assigned=MAC, task_mode="validation",
              extra={"harness": "claude-code", "claimed_by": MAC, "requires_capabilities": ["login_keychain"]})
    plan = plan_launch(gov, TID, policy=None, no_launch=False, decl=launch_declaration(load_loop_contract()), already_launched=set())
    _show(f"[验收⑤·loop 手工提示] refusal={plan.refusal}\n  manual_hint={plan.manual_hint}")
    assert f"请在主机 {HOST} 的顾问会话派子 agent" in plan.manual_hint and "登录钥匙串(login_keychain)" in plan.manual_hint
    monkeypatch.setenv("LYBRA_LANG", "en")
    plan_en = plan_launch(gov, TID, policy=None, no_launch=False, decl=launch_declaration(load_loop_contract()), already_launched=set())
    assert f"advisor session on host {HOST}" in plan_en.manual_hint
    rows = [r for r in card_stats(gov)["rows"] if r["task_id"] == TID]
    assert rows and rows[0]["capabilities"] == ["login_keychain"]
    from tools.aipos_cli.board_pages import pages_declaration

    decl = pages_declaration()
    zh, en = _card_rows(_Ctx(decl, "zh"), rows), _card_rows(_Ctx(decl, "en"), rows)
    _show(f"[验收⑤·看板卡行] zh: {re.search(r'<span class=.rp-chip rp-chip-capability.*?</span>', zh).group(0)}\n"
          f"  en: {re.search(r'<span class=.rp-chip rp-chip-capability.*?</span>', en).group(0)}")
    assert "所需主机能力: 登录钥匙串(login_keychain)" in zh and "Required host capabilities: login keychain (login_keychain)" in en


# ===========================================================================
# 验收⑥: en 下新文案零 CJK(全部新文案键 + 真实出声点)
# ===========================================================================

def test_accept6_english_texts_have_no_cjk(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import publish_identity_refusals
    from tools.aipos_cli.execution_division import capability_line, capability_trace, division_refusal
    from tools.aipos_cli.i18n import has_cjk, pick
    from tools.aipos_cli.next_resolver import host_capability_declaration

    en = host_capability_declaration()["texts"]["en"]

    def leaves(node, prefix=""):
        for k, v in node.items():
            yield from (leaves(v, f"{prefix}{k}.") if isinstance(v, dict) else [(f"{prefix}{k}", v)])

    bad = [k for k, v in leaves(en) if has_cjk(v)]
    assert not bad, bad
    gov = _capable_gov(tmp_path, monkeypatch)
    card = {**_cap_meta(), "harness": "claude-code", "assigned_to": MAC}
    rendered = {"trace": capability_trace(gov, card, MAC), "kickoff": capability_line(gov, card, kind="kickoff_line", lang="en"),
                "manual": capability_line(gov, card, kind="manual_hint", lang="en")}
    for stage, actor in (("draft", None), ("claim", SUB), ("kickoff", SUB), ("return", PIX)):
        wrong = {**card, "assigned_to": SUB}
        rendered[f"refused.{stage}"] = division_refusal(gov, wrong, stage=stage, actor=actor)["message"]
    rendered["refused.claim.pi"] = division_refusal(gov, {**card, "harness": "pi", "assigned_to": PIX}, stage="claim", actor=PIX)["message"]
    bare = _capable_gov(tmp_path / "bare", monkeypatch, declare=False)
    rendered["undeclared"] = next(r for r in publish_identity_refusals(bare, dict(card))[0] if "UNDECLARED" in r)
    out = {k: pick(v, "en") for k, v in rendered.items()}
    for key, text in out.items():
        _show(f"[验收⑥·en] {key}: {text}")
        assert text and not has_cjk(text), (key, text)


# ===========================================================================
# 唯一实现守卫: 例外只在 execution_division 一处; 卡面形校验同一实现
# ===========================================================================

def test_single_implementation_and_card_shape_check(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft

    gov = _capable_gov(tmp_path, monkeypatch)
    bad = create_draft(gov, _meta("F284-BAD", task_mode="docs", requires_capabilities=["gpu"]), "## Goal\n\nx\n", dry_run=True)
    reason = next(r for r in bad["blocking_reasons"] if "CARD_CAPABILITIES_INVALID" in r)
    _show(f"[形校验] {reason}")
    assert bad["verdict"] == "BLOCK"
    hits = []
    for path in sorted((REPO_ROOT / "tools").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "capability_executors" in text and "/tests/" not in str(path):
            hits.append(path.relative_to(REPO_ROOT).as_posix())
    _show(f"[唯一实现] 读写 capability_executors 的模块: {hits}")
    assert set(hits) <= {"tools/aipos_cli/workspace_config.py", "tools/aipos_cli/enrollment.py", "tools/aipos_cli/execution_division.py",
                         "tools/aipos_cli/aipos_cli.py"}, hits
    judged = [p.relative_to(REPO_ROOT).as_posix() for p in sorted((REPO_ROOT / "tools").rglob("*.py"))
              if "def _capability_refusal" in p.read_text(encoding="utf-8")]
    assert judged == ["tools/aipos_cli/execution_division.py"]
