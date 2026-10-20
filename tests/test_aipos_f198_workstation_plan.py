"""AIPOS-F198 — 看板「工位设置」页 + `lybra project workstation-plan`(Owner 10-20: 把 chris 手工切换产品化)。

件① 现状: 子 agent 执行者 / pi 执行工位 / 审计工位(实例、接入、工位、章程状态)/ 交 pi 的卡类型 / 目标主机 ssh 别名 / 验证根摘要 / 各卡类型派给谁, 缺项标红。
件② 目标分工: 看板 GET 表单(URL 参数, 不写服务器)/ CLI 参数, 同一规范形 normalize_target。
件③ 计划与话术: 依赖序(决策日志 → Owner 签信封 → 接入 → 章程接管 / 同步 → set-execution → set-host → 核验 → 通知), 只列缺的步骤, 已完成标出;
    给项目总顾问 / 各专项顾问两段话术。
件④ CLI 同源: 看板「完整计划」<pre> = CLI 文本逐字节同一。

靶场 = 临时 HOME + 临时 home 根, 三个项目(三形态): ① 未声明 pi(只有子 agent + 审计); ② 手写章程工位未接入(chris 现状形: 工位 .lybra/role 在、
AGENTS.md 是带「禁止 Lybra 分发器覆盖」标记的手写章程、未在本项目接入, 已有覆盖信封); ③ 已切换完成。工位章程用假门 RepoGate 按产品 sync 渲染
(test_aipos_f82 / f95 同一靶场件)。看板在测试进程线程内起(禁真门 / 真治理根 / 真工位 / 生产看板)。验收原文经 _show 打到 stdout(RETURN 证据);
F198_SNAPSHOT_DIR 设了 = 各页另存独立 HTML(样式内联)供截图。
"""
from __future__ import annotations

import html
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f95_harness_launch as f95  # noqa: E402  — 工位 / 章程同步靶场件(禁第二份)
from test_aipos_f171_roadmap_board_readonly import _cli, _tree_digest  # noqa: E402
from test_aipos_f73d_loop_driver import _write  # noqa: E402

P1, P2, P3 = "f198-none", "f198-hand", "f198-done"
HOST, ALIAS = "sim-mac", "sim-mac-alias"
MODES = "validation,config,docs"
HAND = ("# 角色：coder — 人肉期执行工位(靶场)\n\n"
        "<!-- 手工维护：本章程属本项目，禁止 Lybra 分发器按角色类别覆盖（靶场） -->\n\n手写章程正文。\n")


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _sub(p: str) -> str:
    return f"exec.{p}.advhost"


def _aud(p: str) -> str:
    return f"audit.{p}.devbox"


def _coder(p: str) -> str:
    return f"coder.{p}.devbox"


def _iso(days: float) -> str:
    from tools.aipos_cli.clock import iso_z, utc_now

    return iso_z(utc_now() + timedelta(days=days))


def _make_project(hroot: Path, name: str, tmp: Path) -> Path:
    gov = hroot / name
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (gov / "5_tasks" / "queue" / sub).mkdir(parents=True)
    (gov / "5_tasks" / "policies").mkdir(parents=True)
    repo = tmp / "repos" / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True)
    _write(gov / "project.json", json.dumps({"project": name, "code_repo": str(repo), "config_version": 1}))
    return gov


def _land(gov: Path, instance: str, role: str, ws: Path | None = None) -> None:
    from tools.aipos_cli.enrollment import _append_enrollment_trail, subagent_land_detail

    if ws is None:
        _append_enrollment_trail(gov, action="land", code_id=f"e-{instance}", role=role, instance=instance, by="(agent-enroll)",
                                 reason=subagent_land_detail(host="advhost", harness="claude-code", mode="subagent", files=["connection.json"]))
    else:
        _append_enrollment_trail(gov, action="land", code_id=f"e-{instance}", role=role, instance=instance, by="(agent-enroll)",
                                 reason=f"host={socket.gethostname()} workstation={ws} files=['connection.json', 'role']")


def _policy(gov: Path, policy_id: str, who: str) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{policy_id}.md", build_autonomy_policy_markdown(
        policy_id=policy_id, agent_or_role=who, active_from=_iso(-1), expires_at=_iso(6), max_tasks=10,
        owner_approval_ref="fixture-envelope-f198", task_selector_task_mode="validation", launch_harnesses=["pi"]))


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.enrollment import set_project_execution
    from tools.aipos_cli.validation_root import apply_validation_root_change

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "LYBRA_HARNESS_ROOT", "LYBRA_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    govs = {p: _make_project(hroot, p, tmp_path) for p in (P1, P2, P3)}
    ws: dict[str, dict[str, Path]] = {}
    for p, gov in govs.items():
        base = tmp_path / f"pi-{p}"  # 每个项目一个工位父根(本地清单 _distributed/.version-<角色> 不跨项目共享)
        ws[p] = {"auditor": f95._workstation(base, gov, f"{p}-auditor", "auditor", _aud(p)),
                 "coder": f95._workstation(base, gov, f"{p}-coder", "executor", _coder(p))}
        _land(gov, _sub(p), "executor")
        _land(gov, _aud(p), "auditor", ws[p]["auditor"])
    _land(govs[P2], f"advisor.{P2}.devbox", "advisor")
    # ① 未声明 pi: 审计工位按产品渲染; coder 工位只是夹具副本(未渲染、未接入)
    f95.sync_fixture_workstations(monkeypatch, govs[P1], ws[P1]["auditor"])
    set_project_execution(govs[P1], subagent_executor=_sub(P1), auditor=_aud(P1), dry_run=False)
    # ② chris 形: coder 工位是手写章程(带手工维护标记)且未在本项目接入; Owner 已签覆盖它的信封
    f95.sync_fixture_workstations(monkeypatch, govs[P2], ws[P2]["auditor"])
    (ws[P2]["coder"] / "AGENTS.md").write_text(HAND, encoding="utf-8")
    _policy(govs[P2], "pol_f198_hand_coder", _coder(P2))
    set_project_execution(govs[P2], subagent_executor=_sub(P2), auditor=_aud(P2), dry_run=False)
    # ③ 已切换完成: coder 已接入 + 产品渲染章程 + 执行方式交 pi + 目标主机 ssh 别名
    _land(govs[P3], _coder(P3), "executor", ws[P3]["coder"])
    f95.sync_fixture_workstations(monkeypatch, govs[P3], ws[P3]["auditor"], ws[P3]["coder"])
    set_project_execution(govs[P3], subagent_executor=_sub(P3), auditor=_aud(P3), pi_executor=_coder(P3),
                          pi_allowed_task_modes=["validation", "config", "docs"], dry_run=False)
    apply_validation_root_change(govs[P3], "set-host", dry_run=False, actor="advisor.fixture", host=HOST, ssh_alias=ALIAS,
                                 owner_ref="Owner 靶场授权")
    return {"home": home, "hroot": hroot, "govs": govs, "ws": ws, "tmp": tmp_path}


def _plan_cli(r: dict, project: str, *extra: str) -> tuple[int, str, str]:
    return _cli("project", "workstation-plan", project, "--home-root", str(r["hroot"]), *extra)


TARGET = ("--pi-modes", MODES, "--host", HOST, "--ssh-alias", ALIAS)


def _states(out: str) -> list[tuple[str, str]]:
    """计划段每步 (序号, 状态)。"""
    section = out.split("计划(按依赖排序", 1)[1].split("给项目总顾问的话", 1)[0]
    return re.findall(r"^  (\d+)\. \[([^\]]+)\]", section, re.M)


# ===========================================================================
# 件①③: 三形态现状与计划(依赖序、只列缺的、已完成标出)
# ===========================================================================

def test_three_shapes_status_plan_and_dependency_order(rig):
    expected = {
        P1: ["待办", "待办", "待办", "不适用", "待办", "待办", "待办", "核验", "待办"],
        P2: ["待办", "已完成", "待办", "待办", "不适用", "待办", "待办", "核验", "待办"],
        P3: ["不适用", "已完成", "已完成", "不适用", "已完成", "已完成", "已完成", "核验", "不适用"],
    }
    for project in (P1, P2, P3):
        rc, out, err = _plan_cli(rig, project, "--pi-executor", _coder(project), *TARGET)
        _show(f"---- 件①③ {project} workstation-plan exit {rc} ----\n{out}{err}")
        assert rc == 0, err
        assert [s for _n, s in _states(out)] == expected[project], _states(out)
    # 依赖顺序: 接入在执行方式声明之前, 信封在接入之前, 决策在最前; 已完成项跳过(总顾问话术只列待办)
    rc, out, _e = _plan_cli(rig, P2, "--pi-executor", _coder(P2), *TARGET)
    order = [out.index(k) for k in ("1. [待办] 决策日志记录", "3. [待办] 接入 pi 执行工位", "4. [待办] 正式接管工位手写章程",
                                     "6. [待办] 声明执行方式", "7. [待办] 声明目标主机")]
    assert order == sorted(order)
    lead = out.split("给项目总顾问的话(执行切换)", 1)[1].split("给各专项顾问的话", 1)[0]
    _show(f"[件③ P2 总顾问话术原文]\n{lead}")
    assert "2. Owner 签覆盖 pi 执行工位的信封: 已完成, 跳过" in lead and "--owner-authorization-ref pol_f198_hand_coder" in lead
    assert f"lybra --workspace-root {rig['govs'][P2]} roles enroll-deliver --role executor --instance {_coder(P2)}" in lead
    assert f"--actor advisor.{P2}.devbox" in lead  # 本项目唯一已接入顾问 = 填实; 多个 / 无 = 占位
    assert "--adopt-hand-charter --owner-ref governance/decision_log/" in lead
    assert "现状: " not in lead  # 话术不带工程化现状行
    status = out.split("现状\n", 1)[1].split("\n\n", 1)[0]
    assert f"pi 执行工位: (未声明)" in status and "交 pi 的卡类型: (无: 全部卡由子 agent 执行)" in status
    risks = out.split("风险\n", 1)[1].split("\n\n", 1)[0]
    _show(f"[件① P2 风险原文]\n{risks}")
    assert "⚠ 验证卡交 pi, 但没有选目标主机" not in risks  # 给了目标主机
    rc, out1, _e = _plan_cli(rig, P1, "--pi-executor", _coder(P1), "--pi-modes", MODES)
    assert "⚠ 验证卡交 pi, 但没有选目标主机与 ssh 别名(验证在他机时需要)" in out1
    env = out1.split("2. [待办] Owner 签覆盖 pi 执行工位的信封", 1)[1].split("3. [", 1)[0]
    _show(f"[件③ P1 信封步原文(顾问补充: 接入前须有覆盖信封)]\n{env}")
    assert f"lybra envelope mint --workspace-root {rig['govs'][P1]}" in env and f"--agent-or-role {_coder(P1)}" in env
    assert "--dry-run" in env and "--confirm" in env and "/owner/envelopes?project=" in env
    rc, out3, _e = _plan_cli(rig, P3, *TARGET)
    _show(f"[件③ P3 已切换完成: 已完成项]\n{out3.split('计划(', 1)[1].split('给各专项顾问的话', 1)[0]}")
    assert "目标与现状一致, 无需切换。" in out3 and "现状已符合目标分工, 无需执行切换命令" in out3
    assert f"pi 执行工位: {_coder(P3)}(已接入); 工位 {rig['ws'][P3]['coder']}; 章程: 产品渲染(最新)" in out3
    assert f"目标主机与 ssh 别名: {HOST} → ssh 别名 {ALIAS}" in out3
    assert f"验证卡 → pi 执行工位 {_coder(P3)}(loop 自动拉起); 审计: 独立审计 {_aud(P3)}" in out3
    assert f"环境卡 → pi 执行工位 {_coder(P3)}(loop 自动拉起); 审计: 顾问复核" in out3
    assert f"代码卡 → 子 agent {_sub(P3)}(顾问派工)" in out3


def test_status_charter_states_and_candidates(rig):
    from tools.aipos_cli.distribution_sync import charter_local_state
    from tools.aipos_cli.workstation_plan import collect_status

    states = {name: charter_local_state(rig["ws"][p][k], rig["govs"][p])["state"]
              for name, p, k in (("P1 审计(产品渲染)", P1, "auditor"), ("P1 coder(夹具副本)", P1, "coder"),
                                 ("P2 coder(手写)", P2, "coder"), ("P3 coder(产品渲染)", P3, "coder"))}
    states["他项目工位"] = charter_local_state(rig["ws"][P1]["coder"], rig["govs"][P2])["state"]
    states["目录不在"] = charter_local_state(rig["tmp"] / "nowhere", rig["govs"][P1])["state"]
    _show(f"[件① 章程状态(sync --dry-run 同一判据, 不连门)] {states}")
    assert states == {"P1 审计(产品渲染)": "current", "P1 coder(夹具副本)": "stale", "P2 coder(手写)": "hand_maintained",
                      "P3 coder(产品渲染)": "current", "他项目工位": "other_project", "目录不在": "not_workstation"}
    st = collect_status(rig["govs"][P2], project=P2, home_root=rig["hroot"])
    _show(f"[件② P2 工位候选(同父目录、本项目实例段、排除审计 / 子 agent)] {st['candidates']}")
    assert st["candidates"] == {"items": [{"instance": _coder(P2), "dir": str(rig["ws"][P2]["coder"])}], "errors": []}


# ===========================================================================
# 计划给出的命令 = 当前产品命令(照抄可跑; 依赖序真实成立: 未接入时 set-execution 拒)
# ===========================================================================

def _step_commands(out: str, n: int) -> dict[str, str]:
    block = out.split("计划(按依赖排序", 1)[1].split(f"\n  {n}. [", 1)[1].split(f"\n  {n + 1}. [", 1)[0]
    return {k: v for k, v in re.findall(r"^     (预演|确认|落账|核验): (.*)$", block, re.M)}


def _run_cmd(command: str) -> tuple[int, str, str]:
    argv = shlex.split(command)
    assert argv[0] == "lybra"
    return _cli(*argv[1:])


def test_plan_commands_are_live_product_commands(rig, monkeypatch):
    from test_aipos_f82_workstation_intake import RepoGate

    from tools.aipos_cli import confirm_client

    gov = rig["govs"][P2]
    rc, out, _e = _plan_cli(rig, P2, "--pi-executor", _coder(P2), *TARGET)
    before = _tree_digest(rig["hroot"])
    ws_before = _tree_digest(rig["ws"][P2]["coder"])
    # 1 决策日志预演
    rc, o, e = _run_cmd(_step_commands(out, 1)["预演"])
    _show(f"[命令实跑 1 决策日志预演] exit {rc}\n{o}{e}")
    assert rc == 0 and "DRY-RUN" in o
    plan_ref = re.search(r"--owner-ref (governance/decision_log/\S+)", out).group(1)
    assert plan_ref in o  # 计划预告的决策记录路径 = 产品命令将写的路径(同一命名 decision_entry_path)
    # 6 执行方式预演: 接入前 = EXECUTION_INSTANCE_NOT_ENROLLED(所以接入排在前面)
    exec_cmd = _step_commands(out, 6)["预演"]
    rc, o, e = _run_cmd(exec_cmd)
    _show(f"[命令实跑 6 set-execution 预演(未接入)] exit {rc}\n{o}{e}")
    assert rc == 1 and "EXECUTION_INSTANCE_NOT_ENROLLED" in (o + e)
    # 4 手写章程接管预演(假门 RepoGate = 真 build_role_manifest)
    RepoGate.roles = {f95.ws_token("executor"): ("executor", None)}
    monkeypatch.setattr(confirm_client, "GateClient", RepoGate)
    rc, o, e = _run_cmd(_step_commands(out, 4)["预演"])
    _show(f"[命令实跑 4 章程接管预演] exit {rc}\n{o}{e}")
    assert rc == 0 and "would-backup(手写章程采纳" in o
    # 7 set-host 预演
    rc, o, e = _run_cmd(_step_commands(out, 7)["预演"])
    _show(f"[命令实跑 7 set-host 预演] exit {rc}\n{o}{e}")
    assert rc == 0 and ALIAS in o
    assert _tree_digest(rig["hroot"]) == before and _tree_digest(rig["ws"][P2]["coder"]) == ws_before, "预演零写入"
    # 接入完成后(靶场以接入日志 land 事件模拟 enroll-deliver 的结果)同一条 set-execution 预演即通过; 计划把接入标为已完成
    _land(gov, _coder(P2), "executor", rig["ws"][P2]["coder"])
    rc, o, e = _run_cmd(exec_cmd)
    _show(f"[命令实跑 6 set-execution 预演(接入后)] exit {rc}\n{o}{e}")
    assert rc == 0 and '"validation"' in o and _coder(P2) in o
    rc, out2, _e = _plan_cli(rig, P2, "--pi-executor", _coder(P2), *TARGET)
    assert [s for _n, s in _states(out2)][:4] == ["待办", "已完成", "已完成", "待办"]


# ===========================================================================
# 件②④: 看板页 = CLI 同一计划逐字节; 看板零写入; 输入不合法
# ===========================================================================

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
        repo_root=serve_root, home_root=rig["hroot"], session_store=store, connection_paths=[],
        board_audit_log_path=rig["tmp"] / "board-runtime" / "audit.jsonl"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**rig, "base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _get(b: dict, path: str, method: str = "GET") -> tuple[int, str]:
    req = urllib.request.Request(b["base"] + path, headers={"Cookie": b["cookie"]}, method=method,
                                 data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _snapshot(name: str, page: str) -> None:
    out = os.environ.get("F198_SNAPSHOT_DIR")
    if not out:
        return
    css = (REPO_ROOT / "web" / "board" / "static" / "readonly-pages.css").read_text(encoding="utf-8")
    Path(out).mkdir(parents=True, exist_ok=True)
    page = re.sub(r"<details", "<details open", page)  # 截图展开折叠段(命令 / 话术 / 完整计划)
    (Path(out) / f"{name}.html").write_text(page.replace('<link rel="stylesheet" href="/readonly-pages.css">', f"<style>{css}</style>"),
                                            encoding="utf-8")


def _page_url(project: str, *, lang: str | None = None, pi_executor: str | None = None, modes: str = MODES, host: bool = True) -> str:
    q: list[tuple[str, str]] = [("project", project), ("plan", "1")] + [("pi_mode", m) for m in modes.split(",") if m]
    if pi_executor:
        q.append(("pi_executor", pi_executor))
    if host:
        q += [("host", HOST), ("ssh_alias", ALIAS)]
    if lang:
        q.append(("lang", lang))
    return "/owner/workstation?" + urlencode(q)


def _pre(page: str) -> str:
    return html.unescape(re.search(r'<pre class="upgrade-lines ws-plan-text">(.*?)</pre>', page, re.S).group(1))


def _fingerprint(r: dict) -> dict[str, dict[str, str]]:
    return {"home": _tree_digest(r["hroot"]), **{f"ws-{p}-{k}": _tree_digest(d) for p, ws in r["ws"].items() for k, d in ws.items()}}


def test_board_page_same_plan_as_cli_and_zero_writes(board):
    before = _fingerprint(board)
    for project, lang in ((P1, None), (P2, None), (P3, None), (P2, "en")):
        status, page = _get(board, _page_url(project, lang=lang, pi_executor=_coder(project)))
        assert status == 200, page[:500]
        _snapshot(f"workstation-{project}{'-' + lang if lang else ''}", page)
        argv = ["--pi-executor", _coder(project), *TARGET] + (["--lang", lang] if lang else [])
        rc, out, err = _plan_cli(board, project, *argv)
        assert rc == 0, err
        same = _pre(page) == out.rstrip("\n")
        _show(f"[件④ {project} lang={lang or 'zh'} 看板「完整计划」与 CLI 逐字节一致] {same} ({len(out.encode())} 字节)")
        assert same
    page = _get(board, _page_url(P2, pi_executor=_coder(P2)))[1]
    status_block = re.search(r'<section class="ws-status">.*?</section>', page, re.S).group(0)
    _show(f"[件① P2 现状表原文] {status_block}")
    assert 'data-row="pi_executor" data-enrollment="" data-charter=""' in status_block
    assert 'class="ws-row rp-alert" data-row="pi_executor"' in status_block  # 缺项标红
    steps = re.search(r'<ol class="ws-steps">.*?</ol>', page, re.S).group(0)
    assert re.findall(r'data-step="([a-z_]+)" data-state="([a-z]+)"', steps) == [
        ("decision", "pending"), ("envelope", "done"), ("enroll", "pending"), ("charter_adopt", "pending"), ("charter_sync", "na"),
        ("execution", "pending"), ("ssh_host", "pending"), ("verify", "check"), ("notify", "pending")]
    form = re.search(r'<form class="ws-form" method="get".*?</form>', page, re.S).group(0)
    _show(f"[件② 目标分工表单原文(GET, 选择只在 URL)] {form}")
    assert 'method="get" action="/owner/workstation"' in form and 'name="pi_mode" value="validation" checked' in form
    assert f'<option value="{_coder(P2)}">' in form and "method=\"post\"" not in form
    for key in ("lead", "specialist"):
        assert f'data-prompt="{key}"' in page
    assert 'href="/owner/workstation" aria-current="page"' in page  # 顶部导航同一骨架
    proj = _get(board, f"/owner/project?project={P2}")[1]
    assert f'class="rp-link rp-workstation-link" href="/owner/workstation?project={P2}"' in proj  # 项目页入口
    status, refused = _get(board, "/owner/workstation", method="POST")
    _show(f"[件② 看板零写入: POST 工位页] {status} {refused[:200]}")
    assert status != 200 or "board_readonly" in refused
    after = _fingerprint(board)
    changed = sorted(k for k in before if before[k] != after[k])
    _show(f"[件② 看板零写入: 请求前后治理根 + 工位目录指纹] 变化 = {changed or '无'}({sum(len(v) for v in before.values())} 个文件)")
    assert not changed


def test_input_errors_and_target_defaults(board):
    rc, out, err = _plan_cli(board, P1, "--pi-modes", "validation,code")
    _show(f"[件② 不可交 pi 的卡类型] exit {rc}: {err.strip()}")
    assert rc == 2 and "卡类型 code 不在可选值 validation, config, docs" in err
    rc, out, err = _plan_cli(board, P1, "--pi-modes", MODES, "--pi-executor", "bad name")
    assert rc == 2 and "不合命名模板" in err
    rc, out, err = _plan_cli(board, P1, "--pi-modes", MODES, "--pi-executor", _aud(P1))
    assert rc == 2 and "不能是审计工位" in err
    rc, out, err = _plan_cli(board, P1, "--host", HOST)
    assert rc == 2 and "成对" in err
    page = _get(board, _page_url(P1, modes="validation,code"))[1]
    _show(f"[件② 看板输入不合法] {re.search(r'<p class=.rp-err ws-input-error.>.*?</p>', page).group(0)}")
    assert "ws-input-error" in page and "ws-status" in page  # 仍给现状
    # 只给卡类型: pi 执行工位缺 = 风险 + 占位; 只给实例 = 工位目录取同父目录候选
    rc, out, err = _plan_cli(board, P1, "--pi-modes", MODES)
    assert "⚠ 目标要把卡交 pi, 但没有选 pi 执行工位实例" in out and "--pi-executor <pi执行实例>" in out
    rc, out, err = _plan_cli(board, P1, "--pi-modes", MODES, "--pi-executor", _coder(P1))
    assert f"--workspace {board['ws'][P1]['coder']}" in out
    # 已有 Owner 决策引用 = 决策步已完成, 各步 --owner-ref 用它
    rc, out, err = _plan_cli(board, P2, "--pi-executor", _coder(P2), *TARGET, "--owner-ref", "coord-fixture-decision")
    assert "1. [已完成] 决策日志记录 Owner 分工" in out and "--owner-ref coord-fixture-decision" in out
    rc, out, err = _plan_cli(board, P2, "--pi-executor", _coder(P2), *TARGET, "--json")
    view = json.loads(out)
    assert [s["state"] for s in view["steps"]][:3] == ["pending", "done", "pending"] and view["lines"][0].startswith("工位设置 · 项目")


# ===========================================================================
# 声明单源 / 文案中英 / CJK 守卫 / 样式令牌
# ===========================================================================

def test_declaration_single_source_and_guards():
    import importlib.util

    from tools.aipos_cli import workstation_plan as wp

    decl = wp.declaration()
    _show(f"[声明] steps={[s['name'] for s in decl['steps']]} offered={decl['offered_pi_modes']} page={decl['board']['page_path']}")
    assert set(decl["texts"]) == {"zh", "en"} and set(decl["texts"]["zh"]) == set(decl["texts"]["en"])
    spec = importlib.util.spec_from_file_location("f198_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    hits = guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / "readonly_pages.py")
    _show(f"[CJK 守卫逐行判定 readonly_pages.py] {hits}")
    assert hits == []
    src = (REPO_ROOT / "tools" / "aipos_cli" / "workstation_plan.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    literal = [line.strip() for line in code.splitlines() if not line.lstrip().startswith("#")
               and re.search(r"[一-鿿]", line.split("  #")[0]) and "raise SchemaLoadError(" not in line]
    _show(f"[话术单源] workstation_plan.py 除声明坏拒因外的中文字面量: {literal or '无'}")
    assert not literal, "话术 / 文案只在 verbs.schema 声明"
    css = (REPO_ROOT / "web" / "board" / "static" / "readonly-pages.css").read_text(encoding="utf-8")
    block = css.split("/* workstation page (AIPOS-F198)", 1)[1].split("@media", 1)[0]
    assert not re.findall(r"#[0-9A-Fa-f]{3,6}\b|rgb|radius|shadow|gradient", block)
