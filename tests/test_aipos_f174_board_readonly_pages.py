"""AIPOS-F174 — 看板只读页: 路线图与验收(里程碑进度 / 偏差 / 逐条标准与证据)、项目接入(接入步骤完成度)、总览导航;
每个需 Owner 动作处给「告诉 <顾问实例>: …」与顾问命令(不放按钮)。

靶场 = 临时 HOME + 临时 home 根(git 仓 + 临时远端裸仓; `lybra project new` 建三个靶场项目):
  P1 = 有路线图(经产品命令 `lybra roadmap add-* --confirm` 写)+ 挂接卡 + 门裁决记录 + 审计报告(带 / 不带运行证据节)+ loop 运行记录
       (charter_checks)+ 接入日志 land 事件(顾问 / 子 agent 执行者 / 审计工位)+ execution / pi_models 声明 + 生效信封;
  P2 = 无路线图、无接入、信封用尽(人肉期接入样本: 只有治理根与卡);
  P3 = project.json status=paused。
卡 / 记录 / 运行记录按声明落点造(record_writer.record_dir / record_file_prefix、loop_run_record.loop_runs_dir、enrollment._trail_line)。
项目名 / 实例名 / 卡号均为靶场值(产品不写死)。看板 = 测试进程内线程起的 ThreadingHTTPServer(测试客户端 urllib), 用完即 shutdown。
全程不读真实凭据、不碰真实治理根、不对真实门写、不起子进程。

验收(卡面 ★验收): ① 三页 HTML 原文断言(进度条数值、偏差条、逐条标准与证据摘录、「告诉顾问」话术与命令、如何开始引导、
接入清单各项状态、总览卡片与链接)② 新路由仅 GET, 其它方法 405 原文, 非登录 302 原文 ⑤ i18n 单通道键齐 + 防碎片化不变量
(③ 真实只读渲染 / ④ 两边基线 / ⑥ 残留进程 由执行体在 RETURN 附原文)。
"""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源
from test_aipos_f171_roadmap_board_readonly import _cli, _git, _tree_digest  # noqa: E402

P1, P2, P3 = "f174-alpha", "f174-beta", "f174-paused"   # 靶场项目名(非真实项目)
ADV1 = f"advisor.{P1}.h1"
ADV1B = f"advisor.{P1}.h2"
EXEC1 = f"exec.{P1}.h1"
AUD1 = f"audit.{P1}.h1"
NEW_PAGES = ("/owner/overview", "/owner/project", "/owner/roadmap", "/owner/onboarding")  # AIPOS-F181: + project page


def _show(text: str) -> None:
    print(text, flush=True)


def _iso(delta_days: float) -> str:
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=delta_days))


def _day(delta_days: int) -> str:
    from tools.aipos_cli.clock import utc_now

    return (utc_now() + timedelta(days=delta_days)).date().isoformat()


def _card(gov: Path, project: str, state: str, task_id: str, *, created_days_ago: float, **extra) -> None:
    from tools.aipos_cli.record_writer import render_markdown
    from tools.aipos_cli.task_loader import queue_root_for

    meta = {"task_id": task_id, "title": f"{task_id} probe", "project": project, "status": state, "task_mode": "code",
            "created_by": extra.pop("created_by", ADV1), "created_at": _iso(-created_days_ago), **extra}
    _write(queue_root_for(gov) / state / f"{task_id.lower()}.md", render_markdown(meta, f"# {task_id}\n"))


def _verdict(gov: Path, task_id: str, verdict: str, *, report_text: str, days_ago: float) -> str:
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix, render_markdown

    ref = f"out/audit/{task_id}R/RETURN.md"
    _write(gov / ref, report_text)
    vid = f"{record_file_prefix('audit_verdicts')}_{task_id}_probe"
    _write(record_dir(gov, "audit_verdicts", task_id) / f"{vid}.md", render_markdown(
        {"record_type": "audit_verdict_record", "verdict_id": vid, "verdict": verdict, "reviewed_task_id": task_id,
         "audit_task_id": f"{task_id}R", "verdict_at": _iso(-days_ago), "evidence_refs": [ref]}, "# verdict\n"))
    return vid


def _report(with_evidence: bool) -> str:
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration

    decl = runtime_evidence_declaration()
    body = "---\nverdict: PASS\n---\n# audit\n\n## 一句话结论\n通过\n"
    if with_evidence:
        body += (f"\n## {decl['section_heading']}\n\n```{decl['block_language']}\n"
                 '[{"command": "pytest -q tests/test_probe.py", "worktree": "/w/K-1R", "exit_code": 0, '
                 '"log_excerpt": "3 passed in 0.12s"}]\n```\n')
    return body


def _policy(gov: Path, pid: str, *, who: str, max_tasks: int, used: int, days_left: float) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=who, active_from=_iso(-2), expires_at=_iso(days_left), max_tasks=max_tasks,
        owner_approval_ref=f"fixture-{pid}", task_selector_task_mode="code"))
    for i in range(used):
        _write(gov / "5_tasks" / "records" / "claims" / f"T-{pid}-{i}" / "claim.md",
               f"---\nrecord_type: claim_record\nautonomy_mode: PreAuthorized\nowner_policy_ref: {pid}\n---\n")


def _land(gov: Path, project: str, *, role: str, instance: str, reason: str) -> None:
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path

    _write_trail_line(enrollment_trail_path(gov), _trail_line(action="land", code_id=f"enroll_{instance.replace('.', '_')}", role=role,
                                                              instance=instance, project=project, by="(agent-enroll)", reason=reason))


def _set_project_json(gov: Path, **values) -> None:
    from tools.aipos_cli.workspace_config import project_json_path

    path = project_json_path(gov)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(values)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_record(gov: Path, task_id: str, checks: list[dict]) -> None:
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import loop_runs_dir, run_record_declaration
    from tools.aipos_cli.record_writer import render_markdown

    decl = run_record_declaration(load_loop_contract())
    meta = {"record_type": decl["record_type"], "run_id": f"looprun_{task_id}_probe", "task_id": task_id, "status": "ended",
            "started_at": _iso(-1), "end_reason": "probe", "charter_checks": checks}
    _write(loop_runs_dir(gov, task_id) / f"looprun_{task_id}_20261016T000000Z_1.md", render_markdown(meta, "# run\n"))


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    for name in (P1, P2, P3):
        rc, out, err = _cli("project", "new", name, "--home-root", str(hroot), "--actor", ADV1)
        assert rc == 0, out + err
    init_governance_repo(hroot)
    g1, g2, g3 = hroot / P1, hroot / P2, hroot / P3
    roadmap = [
        ("add-milestone", "--id", "M1", "--lane", "core", "--title", "核心链路可交付", "--status", "active", "--target-date", _day(-3)),
        ("add-criterion", "--milestone", "M1", "--id", "C1", "--statement", "接口单测全绿", "--verify", "auto"),
        ("add-criterion", "--milestone", "M1", "--id", "C2", "--statement", "导出文件可下载", "--verify", "artifact"),
        ("add-criterion", "--milestone", "M1", "--id", "C3", "--statement", "错误码文档齐", "--verify", "auto"),
        ("add-milestone", "--id", "W1", "--lane", "web", "--title", "页面可浏览", "--status", "planned"),
        ("add-criterion", "--milestone", "W1", "--id", "W1C1", "--statement", "首页渲染测试绿", "--verify", "auto"),
    ]
    for argv in roadmap:
        rc, out, err = _cli("roadmap", argv[0], "--workspace-root", str(g1), *argv[1:], "--confirm", "--actor", ADV1)
        assert rc == 0, f"{argv}\n{out}{err}"
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration

    evidence = {runtime_evidence_declaration()["card_field"]: {"required": True, "commands": ["pytest -q tests/test_probe.py"]}}
    # K-1: 结案 PASS + 运行证据节 → C1 满足; K-2: 结案 PASS, C2 verify=artifact 未声明产物 → 未验证(AIPOS-F175); C3 无卡 → 无卡覆盖(严重); M1 逾期(严重)
    _card(g1, P1, "completed", "K-1", created_days_ago=6, milestone="M1", criteria=["C1"], completed_at=_iso(-1), **evidence)
    vid1 = _verdict(g1, "K-1", "PASS", report_text=_report(True), days_ago=1)
    _card(g1, P1, "completed", "K-2", created_days_ago=6, milestone="M1", criteria=["C2"], completed_at=_iso(-1))
    _verdict(g1, "K-2", "PASS", report_text=_report(False), days_ago=1)
    _card(g1, P1, "claimed", "K-3", created_days_ago=1, milestone="W1", criteria=["W1C1"], claimed_at=_iso(-1), created_by=ADV1B)
    _card(g1, P1, "pending", "K-4", created_days_ago=0)  # AIPOS-F181: created after the roadmap (counts as unlinked)
    # 接入: 顾问 + 子 agent 执行者 + 审计工位(本机, 无 host = 缺省本机); 执行方式 + pi 模型声明; 审计工位一次章程核对记录(fresh)
    _land(g1, P1, role="advisor", instance=ADV1, reason=f"workstation={tmp_path / 'ws-adv'} files=['connection.json']")
    _land(g1, P1, role="executor", instance=EXEC1, reason="host=h1 mode=subagent harness=claude-code files=['connection.json']")
    _land(g1, P1, role="auditor", instance=AUD1, reason=f"workstation={tmp_path / 'ws-aud'} files=['connection.json']")
    _set_project_json(g1, execution={"default_mode": "subagent", "subagent_executor": EXEC1, "auditor": AUD1, "pi_executor": None,
                                     "pi_allowed_task_modes": [], "pi_models": {"default": "probe/model-x", "owner_ref": "fixture owner ref"}})
    _run_record(g1, "K-1", [{"card": "K-1R", "instance": AUD1, "workstation": str(tmp_path / "ws-aud"), "outcome": "fresh",
                             "repair_command": "lybra sync --dry-run", "detail": "fresh: probe", "at": _iso(-1)}])
    _policy(g1, "pol_alpha_loop_1", who=ADV1, max_tasks=40, used=2, days_left=20)
    # P2: 人肉期样本 —— 无路线图、无接入登记、信封用尽、一张在途卡
    _card(g2, P2, "claimed", "B-1", created_days_ago=3, claimed_at=_iso(-3))
    _policy(g2, "pol_beta_loop_1", who=f"advisor.{P2}.h1", max_tasks=2, used=2, days_left=10)
    _set_project_json(g3, status="paused")
    _git(hroot, "add", "-A")
    _git(hroot, "commit", "-q", "-m", "fixture: cards + gate records + enrollment")
    return {"home": hroot, "g1": g1, "g2": g2, "g3": g3, "tmp": tmp_path, "vid1": vid1}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def board(rig):
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'], token_ref='owner-fixture')}"
    serve_root = rig["tmp"] / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=serve_root, home_root=rig["home"], session_store=store, connection_paths=[],
        board_audit_log_path=rig["tmp"] / "board-runtime" / "audit.jsonl"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**rig, "base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie}
    finally:
        server.shutdown()
        server.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def _request(b, path: str, method: str = "GET", *, cookie: bool = True) -> tuple[int, str, dict]:
    data = b"{}" if method != "GET" else None
    req = urllib.request.Request(b["base"] + path, data=data, method=method, headers={"Content-Type": "application/json"})
    if cookie:
        req.add_header("Cookie", b["cookie"])
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


def _section(page: str, start_marker: str, end_tag: str) -> str:
    start = page.index(start_marker)
    return page[start:page.index(end_tag, start) + len(end_tag)]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


# ===========================================================================
# ① 路线图与验收页
# ===========================================================================

def test_item1_roadmap_page_progress_signals_criteria_evidence(board):
    status, page, _h = _request(board, f"/owner/roadmap?project={P1}&milestone=M1")
    assert status == 200
    import html as _html

    source = _section(page, '<p class="rp-source">', "</p>")
    _show(f"[①·路线图页 来源行 HTML 原文] {source}")
    assert 'data-met="1" data-total="4"' in source  # 全项目标准满足 1/4(C1)
    left = _section(page, '<aside class="rp-left">', "</aside>")
    m1 = re.search(r'<li class="roadmap-ms[^"]*" data-milestone="M1".*?</li>', left, re.S).group(0)
    w1 = re.search(r'<li class="roadmap-ms[^"]*" data-milestone="W1".*?</li>', left, re.S).group(0)
    _show(f"[①·左栏 里程碑 M1 HTML 原文] {m1}\n[①·左栏 里程碑 W1 HTML 原文] {w1}")
    assert 'data-met="1" data-total="3" data-percent="33"' in m1 and "rp-is-overdue" in m1 and 'data-overdue="true"' in m1
    assert "已逾期" in m1 and "进行中" in m1 and f"目标日 {_day(-3)}" in m1 and "rp-selected" in m1
    assert 'data-met="0" data-total="1" data-percent="0"' in w1 and "计划中" in w1 and 'data-overdue="false"' in w1
    assert '<h3 class="roadmap-lane" data-lane="core">' in left and '<h3 class="roadmap-lane" data-lane="web">' in left
    signals = _section(page, '<section class="rp-signals"', "</section>")
    blocks = re.findall(r'<div class="rp-signal rp-sev-(\w+)" data-kind="(\w+)".*?</details></div>', signals, re.S)
    _show("[①·偏差红黄条(按严重度排序)] " + json.dumps(blocks))
    kinds = {k: sev for sev, k in blocks}
    assert kinds["overdue"] == "inbox" and kinds["uncovered"] == "inbox" and kinds["roadmap_changed"] == "warn"
    assert [sev for sev, _k in blocks] == sorted((sev for sev, _k in blocks), key=lambda s: 0 if s == "inbox" else 1)
    overdue = re.search(r'<div class="rp-signal rp-sev-inbox" data-kind="overdue".*?</details></div>', signals, re.S).group(0)
    _show(f"[①·逾期信号 HTML 原文] {overdue}")
    assert "里程碑 M1 目标日" in overdue and f"告诉 {ADV1}:处理路线图偏差「里程碑逾期」" in _html.unescape(overdue)
    assert "<code>lybra roadmap set --workspace-root " in overdue and "--field target_date" in overdue
    uncovered = re.search(r'data-kind="uncovered".*?</details></div>', signals, re.S).group(0)
    assert 'data-criterion="C3"' in uncovered and "<code>" in uncovered
    right = _section(page, '<section class="rp-right"', "</section>")
    c1 = re.search(r'<article class="vb-station rp-criterion rp-met" data-criterion="C1".*?</article>', right, re.S).group(0)
    _show(f"[①·标准 C1(已满足 + 证据摘录) HTML 原文] {c1}")
    assert 'data-satisfied="true"' in c1 and "已满足" in c1 and "卡 K-1 · 队列 completed" in c1
    assert 'data-verdict="PASS"' in c1 and f"record_id={board['vid1']}" in c1 and "看裁决记录" in c1
    assert "<code>pytest -q tests/test_probe.py</code>" in c1 and "退出码 0" in c1 and "3 passed in 0.12s" in c1
    assert "运行证据(摘自 out/audit/K-1R/RETURN.md)" in c1
    c2 = re.search(r'<article class="vb-station rp-criterion rp-unmet" data-criterion="C2".*?</article>', right, re.S).group(0)
    _show(f"[①·标准 C2(verify=artifact 未声明产物) 文本] {_text(c2)}")
    assert "卡面 artifacts 未声明标准 C2 的产物" in _html.unescape(c2) and "看产物" in c2 and 'data-verdict="PASS"' in c2
    assert 'data-state="unverified"' in c2 and "验证状态:未验证" in _html.unescape(c2)
    c3 = re.search(r'data-criterion="C3".*?</article>', right, re.S).group(0)
    assert "未满足:无挂接卡" in c3
    nxt = _section(page, '<section class="rp-next">', "</section>")
    _show(f"[①·下一步建议 文本] {_text(nxt)}")
    assert "游离卡(建路线图后出的卡里未挂里程碑的)1 张:K-4" in _html.unescape(nxt)  # AIPOS-F181 wording
    # 选 W1: 右栏换成 W1 的标准(在途卡无裁决)
    status, page_w1, _h = _request(board, f"/owner/roadmap?project={P1}&milestone=W1")
    right_w1 = _section(page_w1, '<section class="rp-right"', "</section>")
    assert status == 200 and 'data-milestone="W1"' in right_w1 and "卡 K-3 · 队列 claimed" in right_w1 and "尚无审计裁决" in right_w1
    assert "状态与推导不一致" in right_w1  # planned 但已有卡在推进


def test_item1_roadmap_page_how_to_start_and_unknowns(board):
    import html as _html

    status, page, _h = _request(board, f"/owner/roadmap?project={P2}")
    howto = _section(page, '<section class="rp-howto"', "</section>")
    _show(f"[①·无路线图项目「如何开始」HTML 原文] {howto}")
    plain = _html.unescape(howto)
    assert status == 200 and "如何开始" in plain and f"告诉 你的顾问:为项目 {P2} 建路线图" in plain
    assert "<code>lybra roadmap add-milestone --id &lt;里程碑id&gt;" in howto and "lybra roadmap add-criterion" in plain
    status, page, _h = _request(board, "/owner/roadmap?project=no-such-project")
    assert status == 200 and "项目 no-such-project 不在 home 根已注册项目中" in page
    status, page, _h = _request(board, f"/owner/roadmap?project={P1}&milestone=ZZ")
    assert status == 200 and "里程碑 ZZ 不在路线图中" in page and 'data-milestone="M1"' in _section(page, '<section class="rp-right"', ">")
    # 缺省项目 = 首个未暂停项目; 暂停项目在项目栏带标注
    status, page, _h = _request(board, "/owner/roadmap")
    tabs = _section(page, '<nav class="rp-projects">', "</nav>")
    _show(f"[①·项目栏 HTML 原文] {tabs}")
    assert f'data-project="{P1}" aria-current="page"' in tabs and f"{P3} (已暂停)" in tabs


# ===========================================================================
# ① 项目接入页
# ===========================================================================

def _items(page: str, project: str) -> dict[str, str]:
    sec = _section(page, f'<section class="rp-ob-project" id="{project}"', "</section>")
    return {m.group(1): m.group(0) for m in re.finditer(r'<li class="rp-ob-item[^"]*" data-item="(\w+)".*?</li>(?=<li class="rp-ob-item|</ol>)',
                                                         sec, re.S)}


def test_item1_onboarding_page_checklists(board):
    import html as _html

    status, page, _h = _request(board, "/owner/onboarding")
    assert status == 200
    one, two = _items(page, P1), _items(page, P2)
    order = list(one)
    _show("[①·接入清单 项序] " + ", ".join(order))
    assert order == ["governance_root", "advisor", "executor", "auditor", "envelope", "charter", "pi_model", "roadmap", "first_closed"]
    states1 = {k: re.search(r'data-state="(\w+)"', v).group(1) for k, v in one.items()}
    states2 = {k: re.search(r'data-state="(\w+)"', v).group(1) for k, v in two.items()}
    _show(f"[①·接入清单 {P1}] {states1}\n[①·接入清单 {P2}] {states2}")
    assert states1 == {k: "ok" for k in order}
    assert states2 == {"governance_root": "ok", "advisor": "todo", "executor": "todo", "auditor": "todo", "envelope": "todo",
                       "charter": "na", "pi_model": "na", "roadmap": "todo", "first_closed": "todo"}
    head1 = re.search(rf'<section class="rp-ob-project" id="{P1}"[^>]*>', page).group(0)
    assert 'data-done="9" data-total="9"' in head1
    head2 = re.search(rf'<section class="rp-ob-project" id="{P2}"[^>]*>', page).group(0)
    assert 'data-done="1" data-total="7"' in head2  # 不适用项不计入分母
    for key in ("advisor", "executor", "auditor", "envelope", "charter", "pi_model"):
        _show(f"[①·{P1} {key} 文本] {_text(one[key])}")
    assert f"已接入:{ADV1}" in _html.unescape(one["advisor"])
    assert f"{EXEC1} 已接入(land)" in _html.unescape(one["executor"]) and f"{AUD1} 已接入(land)" in _html.unescape(one["auditor"])
    assert "pol_alpha_loop_1" in one["envelope"] and "2/40" in one["envelope"] and 'href="/owner/envelopes?project=' in one["envelope"]
    assert f"{AUD1} 最近一次拉起前章程核对 fresh" in _html.unescape(one["charter"])
    assert "auditor 用 probe/model-x(来源 execution.pi_models.default)" in _html.unescape(one["pi_model"])
    assert "rp-tell" not in one["advisor"] and "rp-cmd" not in one["advisor"]  # 已完成项不出话术 / 命令
    for key in ("advisor", "executor", "envelope", "roadmap", "first_closed"):
        _show(f"[①·{P2} {key} HTML 原文] {two[key]}")
    plain2 = {k: _html.unescape(v) for k, v in two.items()}
    assert f"告诉 你的顾问:给项目 {P2} 接入顾问实例" in plain2["advisor"] and f"<code>lybra onboarding guide {P2}</code>" in plain2["advisor"]
    assert "project.json 未声明执行方式(execution)" in plain2["executor"] and f"lybra project set-execution {P2} --subagent-executor" in plain2["executor"]
    assert "将尽或用尽:pol_beta_loop_1" in plain2["envelope"] and "lybra envelope" in plain2["envelope"]
    assert "lybra roadmap add-milestone" in plain2["roadmap"] and f'href="/owner/roadmap?project={P2}"' in two["roadmap"]
    assert "还没有结案的卡(在途 1 张)" in plain2["first_closed"] and f"lybra loop status --workspace-root {board['g2']}" in plain2["first_closed"]
    paused = _section(page, '<details class="rp-paused"', "</details>")
    assert 'data-paused="1"' in paused and "已暂停的项目(1 个)" in paused and f'id="{P3}"' in paused


def test_item1_onboarding_states_follow_truth(board):
    """改真相(章程核对未过 / 撤执行方式里的审计工位 land) → 清单项随之变化, 给修法命令(看板不另算)。"""
    import html as _html

    _run_record(board["g1"], "K-2", [{"card": "K-2R", "instance": AUD1, "workstation": "/w", "outcome": "refused",
                                     "repair_command": "lybra sync --harness-root /w --workspace-root /g", "detail": "stale: probe",
                                     "at": _iso(0)}])
    status, page, _h = _request(board, "/owner/onboarding")
    charter = _html.unescape(_items(page, P1)["charter"])
    _show(f"[①·章程未过 文本] {_text(charter)}")
    assert status == 200 and 'data-state="todo"' in charter and "最近一次章程核对未过 refused" in charter
    assert "<code>lybra sync --harness-root /w --workspace-root /g</code>" in charter and f"告诉 {ADV1}:核对项目 {P1} 的工位章程({AUD1})" in charter


# ===========================================================================
# ① 总览页
# ===========================================================================

def test_item1_overview_cards_and_links(board):
    import html as _html

    status, page, _h = _request(board, "/owner/overview")
    assert status == 200
    card1 = re.search(rf'<article class="rp-card" data-project="{P1}".*?</article>', page, re.S).group(0)
    card2 = re.search(rf'<article class="rp-card" data-project="{P2}".*?</article>', page, re.S).group(0)
    _show(f"[①·总览卡片 {P1} HTML 原文] {card1}\n[①·总览卡片 {P2} HTML 原文] {card2}")
    # AIPOS-F181: in flight / to do / closed from machine_zone.card_stats
    assert 'data-inflight="1" data-todo="1" data-closed="2"' in card1 and "在途 1 张 · 待办 1 张 · 已结案 2 张" in card1
    assert re.search(r'data-inbox-count="(\d+)"', card1).group(1) == "2"  # 逾期 + 无卡覆盖 两条严重偏差进收件箱
    assert 'href="/owner/inbox"' in card1 and "待你决定 2 件" in card1
    assert 'data-envelope-state="ok"' in card1 and f'href="/owner/envelopes?project={P1}"' in card1 and "pol_alpha_loop_1" in card1
    assert "路线图:标准满足 1/4 · 里程碑 2 个 · 偏差" in _html.unescape(card1) and f'href="/owner/roadmap?project={P1}"' in card1
    assert 'data-met="1" data-total="4"' in card1
    assert 'data-done="9" data-total="9"' in card1 and f'href="/owner/onboarding#{P1}"' in card1 and f'href="/owner/project?project={P1}"' in card1
    assert 'data-envelope-state="low"' in card2 and "路线图:未声明" in card2 and 'data-done="1" data-total="7"' in card2
    paused = _section(page, '<details class="rp-paused"', "</details>")
    assert f'data-project="{P3}" data-paused="true"' in paused and "暂停项目不进收件箱" in paused
    nav = _section(page, '<header class="rp-nav">', "</header>")
    _show(f"[①·导航 HTML 原文] {nav}")
    for href in ('href="/owner/overview" aria-current="page"', 'href="/owner/roadmap"', 'href="/owner/onboarding"',
                 'href="/owner/inbox"', 'href="/owner/envelopes"', 'href="/owner/project"'):
        assert href in nav, href
    assert "本页只读" in page and "<form" not in page and "<button" not in page


def test_item4_english_and_narrow_screen(board):
    status, page, _h = _request(board, "/owner/onboarding?lang=en")
    _show(f"[④·英文 标题与导航] {_text(_section(page, '<header class=\"rp-nav\">', '</header>'))} | {_text(_section(page, '<h1>', '</h1>'))}")
    assert status == 200 and '<html lang="en">' in page and "Project onboarding checklist · 3 projects" in page
    assert "Advisor instance enrolled" in page and "Tell your advisor: Enroll an advisor instance for" in page
    assert 'href="/owner/roadmap?lang=en"' in page and 'data-set-lang="zh"' in page
    status, page, _h = _request(board, f"/owner/roadmap?project={P1}&lang=en")
    assert status == 200 and "Roadmap &amp; acceptance · " in page and "Severe" in page and f"Tell {ADV1}: Handle the roadmap drift" in page
    css = (REPO_ROOT / "web" / "board" / "static" / "readonly-pages.css").read_text(encoding="utf-8")
    assert "@media (max-width: 760px)" in css and ".rp-layout { grid-template-columns: 1fr; }" in css
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page


# ===========================================================================
# ② 仅 GET / 405 / 302 / 零写入
# ===========================================================================

def test_item2_get_only_405_and_login_redirect(board):
    before = _tree_digest(board["home"])
    lines = []
    for path in NEW_PAGES:
        status, _page, _h = _request(board, path)
        assert status == 200, path
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, text, _h = _request(board, path, method)
            payload = json.loads(text)
            lines.append(f"{method} {path} -> {status} {payload['error']} {payload['message']}")
            assert status == 405 and payload["error"] == "BOARD_READ_ONLY" and payload["message"] == "看板只读, 请经顾问", (method, path)
        status, _text_body, headers = _request(board, path, cookie=False)
        lines.append(f"GET {path} (no session) -> {status} Location={headers.get('Location')}")
        assert status == 302 and headers.get("Location") == "/login"
    _show("[②·新路由逐条(测试客户端)]\n" + "\n".join(lines))
    assert _tree_digest(board["home"]) == before, "只读页零写入"


def test_item2_page_paths_declared_and_no_new_write_route():
    from tools.aipos_cli.board_pages import pages_declaration
    from web.board.app import board_readonly_declaration

    decl = pages_declaration()
    assert tuple(decl["pages"].values()) == NEW_PAGES
    src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    do_post = src[src.index("def do_POST(self)"):src.index("def do_PUT(self)")]
    # 鉴权通道 + 信封签发(F172) + 项目升级(F176 写例外②); 本卡三页无新增写路由
    assert do_post.count("if path == ") == len(board_readonly_declaration()["allowed_post_paths"]) + 3  # AIPOS-F226: + 写例外③ 收件箱裁决
    assert "_readonly_pages" not in do_post and "render_page" not in do_post


# ===========================================================================
# ⑤ i18n 单通道 + 防碎片化不变量
# ===========================================================================

def test_item5_i18n_keys_single_channel_zh_en():
    from tools.aipos_cli.board_pages import ONBOARDING_CHECKS, STATES, pages_declaration
    from tools.aipos_cli.roadmap import roadmap_declaration
    from tools.aipos_cli.roadmap_drift import VERIFICATION_STATES, status_declaration
    from tools.aipos_cli.task_loader import QUEUE_STATES
    from web.board.app import _I18N_TEMPLATES

    zh, en = _I18N_TEMPLATES["zh"], _I18N_TEMPLATES["en"]
    assert set(zh) == set(en)
    sources = [(REPO_ROOT / p).read_text(encoding="utf-8") for p in ("web/board/readonly_pages.py", "tools/aipos_cli/board_pages.py")]
    used = set()
    for text in sources:
        used |= set(re.findall(r'"(rp_[a-z_]+)"', text)) | set(re.findall(r"'(rp_[a-z_]+)'", text))
    dynamic = ({f"rp_rm_status_{s}" for s in roadmap_declaration()["status"]["enum"]}
               | {f"rp_rm_verify_{v}" for v in roadmap_declaration()["verify"]["enum"]}
               | {f"rp_sev_{s}" for s in status_declaration()["severity_labels"]}
               | {f"rp_ob_{kind}_{k}" for k in ONBOARDING_CHECKS for kind in ("item", "ask")}
               | {f"rp_ob_state_{s}" for s in STATES}
               | {f"rp_rm_state_{s}" for s in VERIFICATION_STATES}  # AIPOS-F175: verification five states
               | {f"rp_qs_{s}" for s in QUEUE_STATES})  # AIPOS-F181: queue state labels
    prefixes = {"rp_rm_status_", "rp_rm_verify_", "rp_sev_", "rp_ob_item_", "rp_ob_ask_", "rp_ob_state_", "rp_ob_", "rp_rm_state_", "rp_qs_"}
    missing = sorted(k for k in (used | dynamic) if k not in prefixes and k not in zh)
    assert not missing, missing
    assert set(pages_declaration()["onboarding_items"]) <= set(ONBOARDING_CHECKS)
    # AIPOS-F174 第 1 轮返工(F-F174R-1): 用 CJK 守卫自身的逐行判定(scan_file_for_cjk)核新增渲染模块零命中——行内注释也算
    import importlib.util

    spec = importlib.util.spec_from_file_location("f174_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    hits = guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / "readonly_pages.py")
    assert hits == [], hits
    for path in ("web/board/readonly_pages.py",):
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
        assert not re.search(r"[一-鿿]", code), f"{path}: 界面文字须走 _I18N_TEMPLATES"


def test_item5_single_implementations_no_second_derivation():
    pages = (REPO_ROOT / "tools" / "aipos_cli" / "board_pages.py").read_text(encoding="utf-8")
    render = (REPO_ROOT / "web" / "board" / "readonly_pages.py").read_text(encoding="utf-8")
    for name in ("roadmap_status(", "envelope_overview(", "collect_inbox(", "advisor_instances(", "instance_enrollment(",
                 "latest_charter_checks(", "pi_model_for_role(", "card_stats(", "project_execution(", "home_projects("):  # F181: card_stats
        assert name in pages, name
    for banned in ("read_text(", "write_text(", ".glob(", "iterdir(", "GateClient", "sync(", "charter_freshness(", "load_connection_tokens(",
                   "workstation_identity(", "subprocess", "except Exception"):
        assert banned not in pages and banned not in render, banned
    assert "board_pages" in render and "roadmap_status" not in render and "envelope_overview" not in render
    drift = (REPO_ROOT / "tools" / "aipos_cli" / "roadmap_drift.py").read_text(encoding="utf-8")
    assert drift.count("parse_runtime_evidence_section(") == 1  # 证据读取只在 card_evidence 一处


def test_item5_card_evidence_shared_by_verify_and_page(rig):
    from tools.aipos_cli.roadmap_drift import roadmap_status

    view = roadmap_status(rig["g1"], project=P1)
    crit = {c["id"]: c for m in view["milestones"] for c in m["criteria"]}
    ev = crit["C1"]["evidence"][0]
    _show(f"[⑤·C1 证据(随行带出)] {json.dumps(ev, ensure_ascii=False)}")
    assert crit["C1"]["satisfied"] and ev["verdict"] == "PASS" and ev["evidence"][0]["exit_code"] == 0 and ev["evidence_required"] is True
    assert crit["C2"]["evidence"][0]["evidence"] is None and crit["C3"]["evidence"] == []
