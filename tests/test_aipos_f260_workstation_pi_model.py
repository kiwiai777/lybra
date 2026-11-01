"""AIPOS-F260 — 工位设置按「非代码一律 pi」重建模(F256 / F256B 之后)。

旧模型: workstation-plan / 看板工位页按「勾哪些卡类型交 pi」(execution.pi_allowed_task_modes)推导——已按新分工配好(声明了 pi_executor、无退役键)
的项目, 现状显示「交 pi 的卡类型: 无」、目标「不交 pi」; 页面一勾卡类型, 计划给的 set-execution 确认命令就把退役键写回; 旧形项目不勾卡类型,
确认命令不带 --pi-executor, 把已声明的 pi 执行工位清成 null。
新模型: 目标分工只选「pi 执行工位是谁、在哪」; 非代码卡(enums.schema task_mode.executor)一律交它, 不可选配; 现状读 execution.pi_executor + 工位位置,
不读退役键; 计划的执行方式一步只经 set-execution, 命令永不带 --pi-allowed-task-mode, 目标缺省 = 已声明值(永不清成空)。

靶场 = 临时 HOME + 临时 home 根 + 临时治理根 + 临时产品仓(test_aipos_f198 同一靶场件, 禁第二份): 项目 ① 已按新分工配好(lybra 形); ② 未声明
pi 执行工位(工位目录在、未接入, Owner 已签覆盖信封); ③ 旧形(带退役键 + pi_executor)。计划命令照抄实跑(真产品命令)。验收原文经 _show 打到
stdout(RETURN 证据); F260_SNAPSHOT_DIR 设了 = 两种项目的看板工位页另存 HTML 供查看。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f198_workstation_plan as f198  # noqa: E402  — 工位设置靶场件(禁第二份)
from test_aipos_f171_roadmap_board_readonly import _cli  # noqa: E402

f95 = f198.f95
PI, NOPI, LEGACY = "f260-pi", "f260-nopi", "f260-legacy"
NONCODE = "文档卡、治理卡、环境卡、验证卡"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _execution(gov: Path) -> dict:
    return json.loads((gov / "project.json").read_text(encoding="utf-8"))["execution"]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.enrollment import set_project_execution

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "LYBRA_HARNESS_ROOT", "LYBRA_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    govs = {p: f198._make_project(hroot, p, tmp_path) for p in (PI, NOPI, LEGACY)}
    ws: dict[str, dict[str, Path]] = {}
    for p, gov in govs.items():
        base = tmp_path / f"pi-{p}"
        ws[p] = {"auditor": f95._workstation(base, gov, f"{p}-auditor", "auditor", f198._aud(p)),
                 "coder": f95._workstation(base, gov, f"{p}-coder", "executor", f198._coder(p)),
                 # 备选工位另一个父根(同父根的工位共享本地分发清单 _distributed/.version-<角色>, 同角色两工位会互相顶掉渲染指纹)
                 "coder2": f95._workstation(tmp_path / f"pi2-{p}", gov, f"{p}-coder2", "executor", f"coder2.{p}.devbox")}
        f198._land(gov, f198._sub(p), "executor")
        f198._land(gov, f198._aud(p), "auditor", ws[p]["auditor"])
        f198._land(gov, f"advisor.{p}.devbox", "advisor")
    for p in (PI, LEGACY):  # 已接入 pi 执行工位(另一个备选工位 coder2 也已接入, 供换工位)
        f198._land(govs[p], f198._coder(p), "executor", ws[p]["coder"])
        f198._land(govs[p], f"coder2.{p}.devbox", "executor", ws[p]["coder2"])
    f198._policy(govs[NOPI], "pol_f260_nopi_coder", f198._coder(NOPI))
    # ① lybra 形: 声明 pi_executor、无退役键(F256 后 set-execution 不给 --pi-allowed-task-mode 即不写)
    set_project_execution(govs[PI], subagent_executor=f198._sub(PI), auditor=f198._aud(PI), pi_executor=f198._coder(PI), dry_run=False)
    # ② 未声明 pi 执行工位
    set_project_execution(govs[NOPI], subagent_executor=f198._sub(NOPI), auditor=f198._aud(NOPI), dry_run=False)
    # ③ 旧形: 退役键 + pi_executor(F256 前的声明; 兼容读取)
    set_project_execution(govs[LEGACY], subagent_executor=f198._sub(LEGACY), auditor=f198._aud(LEGACY), pi_executor=f198._coder(LEGACY),
                          pi_allowed_task_modes=["config"], dry_run=False)
    assert "pi_allowed_task_modes" not in _execution(govs[PI]) and _execution(govs[LEGACY])["pi_allowed_task_modes"] == ["config"]
    for p in (PI, LEGACY):  # 工位章程按产品渲染(声明落定后 sync = 最新)
        f95.sync_fixture_workstations(monkeypatch, govs[p], ws[p]["auditor"], ws[p]["coder"], ws[p]["coder2"])
    f95.sync_fixture_workstations(monkeypatch, govs[NOPI], ws[NOPI]["auditor"])
    return {"home": home, "hroot": hroot, "govs": govs, "ws": ws, "tmp": tmp_path}


def _plan(r: dict, project: str, *extra: str) -> tuple[int, str, str]:
    return _cli("project", "workstation-plan", project, "--home-root", str(r["hroot"]), *extra)


def _run(command: str) -> tuple[int, str, str]:
    argv = shlex.split(command.replace("<你的顾问实例>", "advisor.fixture.devbox"))
    assert argv[0] == "lybra"
    return _cli(*argv[1:])


def _pending_confirms(out: str) -> list[tuple[int, str]]:
    """计划段每个待办步的确认命令(序号, 命令)。"""
    section = out.split("计划(按依赖排序", 1)[1].split("给项目总顾问的话", 1)[0]
    blocks = re.split(r"\n  (?=\d+\. \[)", section)
    return [(int(b.split(".", 1)[0]), m.group(1)) for b in blocks if b[:1].isdigit()
            for m in [re.search(r"^     确认: (.*)$", b, re.M)] if m]


# ===========================================================================
# 验收①: 声明了 pi_executor 且无退役键的项目(lybra 形)跑工位设置预演 / 确认: pi_executor 保持、无退役键
# ===========================================================================

def test_accept1_declared_pi_executor_kept_and_no_retired_key(rig):
    gov = rig["govs"][PI]
    pi = f198._coder(PI)
    rc, out, err = _plan(rig, PI)
    _show(f"---- 验收① {PI}(声明 pi_executor、无退役键)工位设置预演 exit {rc} ----\n{out}{err}")
    assert rc == 0, err
    assert f"非代码卡(一律交 pi 执行工位): {NONCODE} → pi 执行工位 {pi}(不可按项目选配)" in out
    assert f"6. [已完成] 声明 pi 执行工位(非代码卡一律交它)\n     现状: pi_executor={pi}" in out
    assert f"目标分工\n  非代码卡({NONCODE})一律交 pi 执行工位 {pi}" in out
    assert "--pi-allowed-task-mode" not in out and "pi_allowed_task_modes" not in out and "不交 pi" not in out
    # 旧提示词 / 章程里的 --pi-modes 照抄: 提示已退役并忽略(计划与不给逐字节同一, 不写回退役键)
    rc, legacy_out, legacy_err = _plan(rig, PI, "--pi-modes", "validation,config,docs", "--pi-executor", pi)
    _show(f"[验收① 旧参数 --pi-modes 照抄] exit {rc} stderr: {legacy_err.strip()}")
    assert rc == 0 and "--pi-modes 已退役(AIPOS-F260)" in legacy_err and legacy_out == out
    # 确认: 加目标主机 ssh 别名(计划出决策 / set-host 两个待办), 照抄全部待办确认命令实跑 → pi_executor 保持、无退役键
    rc, out2, err = _plan(rig, PI, "--host", f198.HOST, "--ssh-alias", f198.ALIAS)
    confirms = _pending_confirms(out2)
    _show(f"[验收① 待办确认命令] {confirms}")
    assert [n for n, _c in confirms] == [1, 7] and "set-execution" not in out2 and "5. [已完成] 同步工位章程" in out2
    for n, command in confirms:
        rc, o, e = _run(command)
        _show(f"[验收① 照抄第 {n} 步确认 exit {rc}]\n{o}{e}")
        assert rc in (0, 3), e  # 3 = 写入已生效、落账未完成(临时根不是 git 仓)
    after = _execution(gov)
    _show(f"[验收① 确认后 project.json execution] {json.dumps(after, ensure_ascii=False)}")
    assert after["pi_executor"] == pi and "pi_allowed_task_modes" not in after
    rc, out3, _e = _plan(rig, PI, "--owner-ref", "fixture-decision", "--host", f198.HOST, "--ssh-alias", f198.ALIAS)
    assert "目标与现状一致, 无需切换。" in out3


def test_accept1_switching_pi_executor_never_writes_retired_key(rig):
    """换 pi 执行工位(既有项目 → 另一已接入工位): set-execution 命令只带 --pi-executor, 旧形项目的退役键随整段重声明清掉。"""
    for project in (PI, LEGACY):
        gov, new = rig["govs"][project], f"coder2.{project}.devbox"
        rc, out, err = _plan(rig, project, "--pi-executor", new, "--owner-ref", "fixture-decision")
        block = out.split("6. [待办] 声明 pi 执行工位", 1)[1].split("\n  7. [", 1)[0]
        _show(f"[验收① {project} 换 pi 执行工位 → 执行方式一步原文]{block}")
        confirm = re.search(r"^     确认: (.*)$", block, re.M).group(1)
        assert f"--pi-executor {new}" in confirm and "--pi-allowed-task-mode" not in confirm
        rc, o, e = _run(confirm)
        assert rc in (0, 3), e
        after = _execution(gov)
        _show(f"[验收① {project} 确认后 execution] {json.dumps(after, ensure_ascii=False)}")
        assert after["pi_executor"] == new and "pi_allowed_task_modes" not in after


def test_accept1_legacy_shape_not_cleared(rig):
    """旧形(带退役键 config + pi_executor): 修前「不勾卡类型」= 确认命令不带 --pi-executor、把 pi 执行工位清成 null; 修后执行方式一步已完成, 不出命令。"""
    gov = rig["govs"][LEGACY]
    rc, out, err = _plan(rig, LEGACY, "--pi-modes", "")
    _show(f"---- 验收① {LEGACY}(旧形)工位设置 exit {rc} ----\n{out.split('计划(', 1)[0]}{err}")
    assert rc == 0 and f"6. [已完成] 声明 pi 执行工位(非代码卡一律交它)\n     现状: pi_executor={f198._coder(LEGACY)}" in out
    assert "set-execution" not in out
    assert _execution(gov)["pi_executor"] == f198._coder(LEGACY)


# ===========================================================================
# 验收②: 未声明 pi 执行工位的项目, 工位设置走完后 project.json 含 pi_executor 且无退役键
# ===========================================================================

def test_accept2_undeclared_project_after_plan_has_pi_executor(rig, monkeypatch):
    gov = rig["govs"][NOPI]
    pi = f198._coder(NOPI)
    rc, out, err = _plan(rig, NOPI)
    _show(f"---- 验收② {NOPI}(未声明 pi 执行工位)现状与风险 exit {rc} ----\n{out.split('计划(', 1)[0]}")
    assert f"非代码卡(一律交 pi 执行工位): {NONCODE} 无法派发: 未声明 pi 执行工位(需接入 pi 执行工位)" in out
    assert f"⚠ 未声明 pi 执行工位: 非代码卡({NONCODE})无法派发(起草即拒 EXECUTOR_PI_UNDECLARED), 需接入 pi 执行工位。下一步:" in out
    assert "验证卡 → 无法派发: 须由 pi 执行工位做, 但未声明 pi 执行工位" in out
    rc, out, err = _plan(rig, NOPI, "--pi-executor", pi, "--pi-modes", "validation")
    states = re.findall(r"^  (\d+)\. \[([^\]]+)\]", out.split("计划(按依赖排序", 1)[1].split("给项目总顾问的话", 1)[0], re.M)
    _show(f"[验收② 选 pi 执行工位 {pi} 后计划步骤] {states}")
    assert [s for _n, s in states] == ["待办", "已完成", "待办", "不适用", "待办", "待办", "不适用", "核验", "待办"]
    confirms = dict(_pending_confirms(out))
    assert f"--pi-executor {pi}" in confirms[6] and "--pi-allowed-task-mode" not in out
    # 照计划依赖序走完: 决策 → 接入(靶场以 land 事件模拟 enroll-deliver 结果)→ 章程同步(产品 sync)→ set-execution 确认
    rc, o, e = _run(confirms[1])
    assert rc == 0, e
    f198._land(gov, pi, "executor", rig["ws"][NOPI]["coder"])
    f95.sync_fixture_workstations(monkeypatch, gov, rig["ws"][NOPI]["coder"])
    rc, o, e = _run(confirms[6])
    _show(f"[验收② 照抄第 6 步确认 exit {rc}]\n{o}{e}")
    assert rc in (0, 3), e
    after = _execution(gov)
    _show(f"[验收② 走完后 project.json execution] {json.dumps(after, ensure_ascii=False)}")
    assert after["pi_executor"] == pi and "pi_allowed_task_modes" not in after
    owner_ref = re.search(r"1\. \[待办\] 决策日志记录 Owner 分工\n     现状: (\S+)", out).group(1)
    rc, out2, _e = _plan(rig, NOPI, "--owner-ref", owner_ref)
    assert "目标与现状一致, 无需切换。" in out2 and "6. [已完成] 声明 pi 执行工位" in out2


# ===========================================================================
# 验收③: 看板工位页渲染原文(两种项目)
# ===========================================================================

def test_accept3_board_page_two_projects(rig):
    import html

    from web.board.readonly_pages import render_workstation

    pages = {p: render_workstation(rig["hroot"], {"project": [p]}) for p in (PI, NOPI)}
    snap = os.environ.get("F260_SNAPSHOT_DIR")
    for project, page in pages.items():
        status = re.search(r'<section class="ws-status">.*?</section>', page, re.S).group(0)
        risks = re.search(r'<section class="ws-risks".*?</section>', page, re.S).group(0)
        form = re.search(r'<section class="ws-choose">.*?</section>', page, re.S).group(0)
        _show(f"[验收③ {project} 看板工位页 现状表原文] {status}\n[验收③ {project} 风险原文] {risks}\n[验收③ {project} 目标分工表单原文] {form}")
        assert 'name="pi_mode"' not in form and 'name="plan"' not in form
        assert f"非代码卡({NONCODE})一律交 pi 执行工位, 不可按项目选配" in html.unescape(form)
        if snap:
            Path(snap).mkdir(parents=True, exist_ok=True)
            (Path(snap) / f"workstation-{project}.html").write_text(page, encoding="utf-8")
    pi_row = re.search(r'<tr class="ws-row[^"]*" data-row="row_noncode">.*?</tr>', pages[PI], re.S).group(0)
    assert 'class="ws-row"' in pi_row and f"→ pi 执行工位 {f198._coder(PI)}(不可按项目选配)" in html.unescape(pi_row)
    nopi_row = re.search(r'<tr class="ws-row[^"]*" data-row="row_noncode">.*?</tr>', pages[NOPI], re.S).group(0)
    assert 'class="ws-row rp-alert"' in nopi_row and "无法派发: 未声明 pi 执行工位(需接入 pi 执行工位)" in html.unescape(nopi_row)
    nopi_risks = html.unescape(re.search(r'<section class="ws-risks".*?</section>', pages[NOPI], re.S).group(0))
    assert "非代码卡" in nopi_risks and "无法派发" in nopi_risks and "下一步: 在下方「选择目标分工」填 pi 执行工位实例" in nopi_risks
    en = render_workstation(rig["hroot"], {"project": [NOPI], "lang": ["en"]})
    assert "cannot be dispatched: no pi executor declared" in html.unescape(en)


# ===========================================================================
# 验收④ 守卫: 工位设置的代码面不读不写退役键(只剩兼容读取 / 迁移 / 退役说明处)
# ===========================================================================

def test_accept4_workstation_code_never_touches_retired_key():
    hits = []
    for rel in ("tools/aipos_cli/workstation_plan.py", "web/board/readonly_pages.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", src, flags=re.S)
        for no, line in enumerate(code.splitlines(), 1):
            stmt = line.split("#", 1)[0]
            if "pi_allowed_task_modes" in stmt or "pi-allowed-task-mode" in stmt:
                hits.append(f"{rel}: {line.strip()}")
    _show(f"[验收④ 工位设置代码面退役键 / 旗标 / 旧声明引用] {hits or '无'}")
    assert hits == []
    from tools.aipos_cli import workstation_plan as wp

    decl = wp.declaration()
    assert wp.noncode_modes() == ["docs", "governance", "config", "validation"] and wp.routed_modes()[0] == "code"
    assert set(decl["texts"]["zh"]) == set(decl["texts"]["en"])
