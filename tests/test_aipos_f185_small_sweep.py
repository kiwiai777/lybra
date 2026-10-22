"""AIPOS-F185 — 小卡清扫②(gap #187 / #189 / #163 / #168 / #162 / #184)。

各件唯一并入点(禁第二路径):
  件① 等交回上限 = loop_driver.loop_wait_limit(声明 verbs.schema lybra_loop.wait_limits; 执行方式判定唯一
      loop_run_record.card_wait_execution_mode, 与 loop status 下一动作 run_execution_mode 同读; 项目覆盖读取口
      workspace_config.project_subagent_max_wait, 形校验并入 project_execution; 写入口 set-execution --subagent-max-wait)。
  件② 顾问章程(agents/roles/advisor/AGENTS.md、agents/skills/advisor-commands/SKILL.md)教 queue supersede, 与 tools/aipos_cli/README.md 同说法。
  件③ 信封挑选 = autonomy_policy.select_envelope(取最新生效者, 已接替者不挑); 接替判定唯一 autonomy_policy._supersession(看板信封页 /
      收件箱 envelope_overview 同读); loop 显式 --envelope 已被接替 = 改用接替者(envelope_successor, 同一判定)并写明。
  件④ board open / approve 凭据 = board_login.locate_connection_json(治理根经 F144 唯一薄壳 _find_repo_root_for_args)。
  件⑤ draft publish 不再读 governance/project-map.md(地图文件不删)。
  件⑥ 退役守卫只看产品文件集 runall_discovery.repo_files(被 git 忽略的 __pycache__ 残留不算)。

靶场: F90 进程内真门 + loop 全链靶场(rig), F78 治理根骨架, AIPOS-271 进程内真看板(_Boardd)。无真实凭据、不连真门、不读写真实治理根。
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 治理根骨架唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内真门 + loop 全链靶场
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import POLICY, TASK, rig  # noqa: E402,F401  — pytest 夹具
from test_aipos_f143_execution_mode import _cli, _registry, AUDITOR, SUB  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract, run_loop  # noqa: E402

POLICY_NEW = "pol_probe_loop_2"


def _show(text: str) -> None:
    print(text, flush=True)


def _declare(gov: Path, **extra) -> None:
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl["execution"] = {"default_mode": "subagent", "subagent_executor": f78.EXEC, "auditor": AUDITOR, **extra}
    _write(gov / "project.json", json.dumps(decl))


def _loop_until_wait(gov: Path, **kw) -> tuple[object, str, list[float]]:
    """loop 认领后进入等交回: 记下等待上限(哨兵 --timeout), 不模拟执行体 = 等待即超时(声明退出码 3)。"""
    seen: list[float] = []

    def watch(args, expect_ready, **_kw):
        seen.append(float(args.timeout))
        return 3

    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, watch=watch, interval=0.05, max_steps=10, **kw)
    return res, out.getvalue(), seen


# ===========================================================================
# 件① 子 agent 执行方式等交回上限放宽 + 超时人话
# ===========================================================================

def test_item1_subagent_default_wait_and_human_timeout(rig):
    gov = rig.gov
    _declare(gov)
    f90._card(gov, TASK, "pending")  # harness=claude-code, assigned_to=EXEC = 子 agent 执行方式
    init_governance_repo(gov)
    res, out, seen = _loop_until_wait(gov, policy_id=POLICY)
    _show(f"---- 件①·子 agent 执行方式 loop 原文(未给 --max-wait) ----\n{out}")
    decl = load_loop_contract()["wait_limits"]
    default = float(decl["by_execution_mode"]["subagent"]["default"])
    assert default == 5400 and seen == [default], seen
    assert f"max_wait={default:g}s(等交回上限: {decl['messages']['source_mode_default']})" in out
    assert res.outcome == "wait_timeout" and "执行体可能仍在做" in res.message and "用原命令重跑 loop 续等" in res.message
    rc, sout, serr = _cli("loop", "status", "--task-id", TASK, "--workspace-root", str(gov))
    _show(f"---- 件①·超时后 lybra loop status 原文 exit {rc} ----\n{sout}{serr}")
    assert "顾问下一动作: advisor_fix(wait_timeout)" in sout
    assert "执行体可能仍在做 → 用原命令重跑 loop 续等" in sout and "执行体可能仍在做(子 agent 跑全量测试等长任务很常见)" in sout
    assert f"lybra loop --task-id {TASK} --workspace-root {gov} --actor {DRIVER} --envelope {POLICY}" in sout


def test_item1_project_override_and_explicit_wins(rig):
    gov = rig.gov
    _declare(gov, subagent_max_wait=7200)
    f90._card(gov, TASK, "pending")
    init_governance_repo(gov)
    _res, out, seen = _loop_until_wait(gov, policy_id=POLICY)
    _show(f"---- 件①·project.json execution.subagent_max_wait=7200 覆盖 ----\n{out.splitlines()[2] if len(out.splitlines()) > 2 else out}")
    assert seen == [7200.0] and "max_wait=7200s(等交回上限: 项目覆盖(project.json execution.subagent_max_wait))" in out
    _res, out, seen = _loop_until_wait(gov, policy_id=POLICY, max_wait=1)
    assert seen == [1.0] and "max_wait=1s(等交回上限: 显式 --max-wait)" in out


def test_item1_pi_mode_and_undeclared_unchanged(rig):
    gov = rig.gov
    _declare(gov)
    f90._card(gov, TASK, "pending", harness="pi")  # 卡要 pi 执行工位 = pi 拉起模式(这里 no_launch 由信封未授权拉起 → 手工等待)
    init_governance_repo(gov)
    res, out, seen = _loop_until_wait(gov, policy_id=POLICY)
    _show(f"---- 件①·pi 模式(缺省不变) ----\n{out}")
    assert seen == [1800.0] and "max_wait=1800s(等交回上限: 缺省(verbs.schema lybra_loop.parameters.max_wait.default))" in out
    assert "执行体可能仍在做" not in res.message


def test_item1_undeclared_execution_keeps_default(rig):
    gov = rig.gov
    f90._card(gov, TASK, "pending")
    init_governance_repo(gov)
    _res, out, seen = _loop_until_wait(gov, policy_id=POLICY)
    assert seen == [1800.0], seen


def test_item1_override_shape_fail_closed(tmp_path, monkeypatch):
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError, project_execution, project_subagent_max_wait

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    for bad in ("3600", 0, True, -5):
        _declare(gov, subagent_max_wait=bad)
        with pytest.raises(ExecutionDeclarationError) as ctx:
            project_execution(gov)
        assert ctx.value.code == "EXECUTION_DECLARATION_INVALID" and "subagent_max_wait" in str(ctx.value)
    _declare(gov, subagent_max_wait=None)
    assert project_subagent_max_wait(gov) is None
    _declare(gov, subagent_max_wait=4000)
    assert project_subagent_max_wait(gov) == 4000.0


def test_item1_set_execution_writes_and_preserves(tmp_path, monkeypatch):
    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    _registry(gov)
    init_governance_repo(gov)
    base = ("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", SUB, "--auditor", AUDITOR)
    rc, out, err = _cli(*base, "--subagent-max-wait", "6000", "--confirm")
    _show(f"[件①·set-execution --subagent-max-wait 6000 --confirm rc={rc}]\n{out}{err}")
    assert rc == 0 and "execution.subagent_max_wait = 6000" in out
    assert json.loads((gov / "project.json").read_text(encoding="utf-8"))["execution"]["subagent_max_wait"] == 6000
    rc, out, err = _cli(*base, "--confirm")  # 未给 = 原值保留
    assert rc == 0, out + err
    assert json.loads((gov / "project.json").read_text(encoding="utf-8"))["execution"]["subagent_max_wait"] == 6000
    rc, out, err = _cli(*base, "--subagent-max-wait", "0")
    assert rc == 1 and "EXECUTION_DECLARATION_INVALID" in err and "subagent_max_wait" in err


def test_item1_single_judgement_shared_with_loop_status():
    """等待上限与 loop status 下一动作同读一个执行方式判定(禁第二判定)。"""
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_run_record.py").read_text(encoding="utf-8")
    body = src[src.index("def run_execution_mode("):src.index("def _execution_rule(")]
    assert "card_wait_execution_mode(" in body and "card_execution_mode(" not in body.replace("card_wait_execution_mode(", "")
    drv = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    fn = drv[drv.index("def loop_wait_limit("):drv.index("# ---", drv.index("def loop_wait_limit("))]
    assert "card_wait_execution_mode(" in fn and "project_subagent_max_wait(" in fn


# ===========================================================================
# 件② 顾问章程教 queue supersede(与 README 同说法)
# ===========================================================================

def test_item2_advisor_docs_teach_supersede():
    readme = (REPO_ROOT / "tools" / "aipos_cli" / "README.md").read_text(encoding="utf-8")
    agents = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    for text in (readme, agents, skill):
        assert "lybra queue supersede --task-id <原卡> --by <新卡> --actor <你的顾问实例>" in text
    assert "不要以执行体身份 block 原卡" in agents
    assert "**不要**再以执行体身份 `queue block` 原卡" in skill and "#### `lybra queue supersede`(AIPOS-F184)" in skill
    assert "撤卡:`lybra queue withdraw`(写入后自动落账)" in agents


# ===========================================================================
# 件③ 同驱动方多个有效信封: 取最新生效者, 已接替者不再被消耗
# ===========================================================================

def _newer_policy(gov: Path, pid: str = POLICY_NEW, **sel) -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    kw = {"task_selector_task_mode": "code", **sel}
    _write(gov / "5_tasks" / "policies" / f"{pid}.md", build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role="advisor", active_from="2026-06-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref=f"fixture-{pid}", **kw))


def _claim_refs(gov: Path) -> list[str]:
    from tools.aipos_cli.frontmatter import require_frontmatter

    refs = []
    for path in sorted((gov / "5_tasks" / "records" / "claims").rglob("*.md")):
        meta, _body = require_frontmatter(path)
        refs.append(str(meta.get("owner_policy_ref") or ""))
    return refs


def test_item3_claim_uses_newest_envelope_and_board_agrees(rig):
    from tools.aipos_cli.autonomy_policy import envelope_overview, envelope_status

    gov = rig.gov
    _newer_policy(gov)
    f90._card(gov, TASK, "pending")
    init_governance_repo(gov)
    rows = {r["policy_id"]: r for r in envelope_overview(gov)["envelopes"]}
    _show("[件③·看板/收件箱同读 envelope_overview] " + json.dumps(
        {k: {"status": v["status"], "superseded_by": v["superseded_by"], "current": v["current"]} for k, v in rows.items()}, ensure_ascii=False))
    assert rows[POLICY]["status"] == "superseded" and rows[POLICY]["superseded_by"] == POLICY_NEW and rows[POLICY_NEW]["current"]
    _res, out, _seen = _loop_until_wait(gov)  # 不给 --envelope: 扫描挑选
    _show(f"---- 件③·loop(扫描挑选)原文 ----\n{out}")
    assert f"envelope={POLICY_NEW} driver={DRIVER}" in out
    assert f"信封: 挑中信封 {POLICY_NEW}(覆盖本卡与驱动方的有效信封中最新生效者); 被它接替的旧信封 {POLICY} 不再消耗" in out
    refs = _claim_refs(gov)
    _show(f"[件③·认领记录 owner_policy_ref] {refs}")
    assert refs == [POLICY_NEW]
    assert envelope_status(gov, POLICY)["used"] == 0 and envelope_status(gov, POLICY_NEW)["used"] == 1


def test_item3_explicit_superseded_envelope_switches_to_successor(rig):
    gov = rig.gov
    _newer_policy(gov)
    f90._card(gov, TASK, "pending")
    init_governance_repo(gov)
    _res, out, _seen = _loop_until_wait(gov, policy_id=POLICY)  # 续跑命令一律带的显式旧信封
    _show(f"---- 件③·loop --envelope {POLICY}(已被接替)原文 ----\n{out}")
    assert f"信封: 显式信封 {POLICY} 已被同驱动方更新信封 {POLICY_NEW} 接替(看板信封页 / 收件箱同一判定), 改用 {POLICY_NEW}" in out
    assert f"envelope={POLICY_NEW}" in out and _claim_refs(gov) == [POLICY_NEW]


def test_item3_select_envelope_order_and_reasons(rig):
    from tools.aipos_cli.autonomy_policy import driver_envelope_identities, select_envelope

    gov = rig.gov
    task = {"task_id": TASK, "task_mode": "code", "project": "lybra"}
    ids = driver_envelope_identities(DRIVER, None, "advisor")
    # 新信封范围更窄(只覆盖别的卡)= 不接替旧信封; 本卡仍由旧信封覆盖
    _newer_policy(gov, task_selector_task_ids=["OTHER-1"])
    notes: list[str] = []
    policy, _ = select_envelope(gov, identities=ids, task=task, notes=notes)
    assert policy["policy_id"] == POLICY and notes == [f"挑中信封 {POLICY}(覆盖本卡与驱动方的有效信封中最新生效者)"]
    # 两个都覆盖本卡且新者范围 ⊇ 旧者 = 新者接替: 挑新者; 只核旧者(显式)时旧者照常可核(不另挑, 由 loop 层改用接替者)
    _newer_policy(gov)
    policy, _ = select_envelope(gov, identities=ids, task=task)
    assert policy["policy_id"] == POLICY_NEW
    policy, _ = select_envelope(gov, identities=ids, task={**task, "task_mode": "audit"})
    assert policy is None
    _p, reasons = select_envelope(gov, identities=ids, task={**task, "task_mode": "audit"})
    _show(f"[件③·不匹配原因] {reasons}")
    assert any(r.startswith(f"{POLICY}: 已被同驱动方更新信封 {POLICY_NEW} 接替") for r in reasons)


def test_item3_supersession_single_function():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "autonomy_policy.py").read_text(encoding="utf-8")
    assert src.count("_scope_covers(other[\"policy\"], row[\"policy\"])") == 1, "接替判定只在 _supersession 一处"
    for fn in ("def _envelope_overview(", "def select_envelope(", "def envelope_successor("):
        body = src[src.index(fn):]
        body = body[:body.index("\ndef ", 1)]
        assert "_supersession(" in body, fn


# ===========================================================================
# 件④ board approve / open 凭据走 F144 治理根解析梯
# ===========================================================================

@pytest.fixture
def board_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.tests.test_board_login import _Boardd, _make_conn

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra")
    for var in ("LYBRA_WORKSPACE_ROOT", "AIPOS_WORKSPACE_ROOT", "LYBRA_CONNECTION_JSON", "LYBRA_HOME_ROOT"):
        monkeypatch.delenv(var, raising=False)
    (gov / ".lybra").mkdir(exist_ok=True)
    conn = _make_conn(gov / ".lybra", "fixture-owner-f185-not-a-secret")
    boardd = _Boardd(conn)
    try:
        yield {"gov": gov, "conn": conn, "boardd": boardd, "tmp": tmp_path}
    finally:
        boardd.shutdown()


def test_item4_approve_in_governance_root_uses_its_credential(board_gov, monkeypatch):
    gov, boardd = board_gov["gov"], board_gov["boardd"]
    code = boardd.device_code()
    monkeypatch.chdir(gov / "5_tasks")  # 治理根内任意目录(F144 所在治理根)
    rc, out, err = _cli("board", "approve", code, "--url", boardd.base_url)
    _show(f"---- 件④·治理根目录下 lybra board approve(靶场看板)rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    assert f"凭据文件: {board_gov['conn'].resolve()}(治理根 .lybra/connection.json(治理根解析: " in out
    assert "fixture-owner-f185-not-a-secret" not in out + err
    _s, _sc, body = boardd.device_poll(code)
    assert body.get("status") == "approved"


def test_item4_workspace_root_flag_and_human_refusals(board_gov, monkeypatch):
    gov, boardd, tmp = board_gov["gov"], board_gov["boardd"], board_gov["tmp"]
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    code = boardd.device_code()
    rc, out, err = _cli("board", "approve", code, "--workspace-root", str(gov), "--url", boardd.base_url)
    assert rc == 0 and f"凭据文件: {board_gov['conn'].resolve()}" in out, out + err
    # 不在治理根、也没给 --workspace-root / --connection-json = 人话拒因(该在哪运行 / 用哪个参数)
    rc, out, err = _cli("board", "approve", "123456", "--url", boardd.base_url)
    _show(f"---- 件④·治理根外运行 rc={rc} ----\n{out}{err}")
    assert rc == 1 and "找不到看板登录用的凭据文件: 治理根解析不出" in err and "--workspace-root <治理根>" in err and "--connection-json" in err
    # 治理根下没有凭据文件 = 点名该放哪
    board_gov["conn"].unlink()
    monkeypatch.chdir(gov)
    rc, out, err = _cli("board", "open", "--no-browser", "--url", boardd.base_url)
    _show(f"---- 件④·治理根无 .lybra/connection.json rc={rc} ----\n{out}{err}")
    assert rc == 1 and f"治理根 {gov.resolve()} 下没有凭据文件 {(gov / '.lybra' / 'connection.json').resolve()}" in err
    assert "该文件应放在治理根的 .lybra/connection.json" in err and "--connection-json <路径>" in err


def test_item4_no_hidden_default_credential_path():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_login.py").read_text(encoding="utf-8")
    assert not re.search(r"^DEFAULT_CONNECTION_JSON\s*=", src, re.M) and "agent_credentials.json\"" not in src
    cli = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    for fn in ("def _run_board_open(", "def _run_board_approve("):
        body = cli[cli.index(fn):]
        body = body[:body.index("\ndef ", 1)]
        # 原「except Exception: workspace_root = None(降级到 connection.json)」静默吞拒因已删
        assert "_board_login_target(args)" in body and "降级到 connection.json" not in body, fn


# ===========================================================================
# 件⑤ draft publish 不再读 project-map.md
# ===========================================================================

def test_item5_publish_no_project_map_warning(tmp_path):
    from datetime import datetime, timedelta, timezone

    from tools.aipos_cli.draft_writer import publish_draft

    repo = tmp_path / "gov"
    (repo / "5_tasks" / "queue").mkdir(parents=True)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    _write(repo / "governance" / "project-map.md", f"---\nmap_version: 1\nupdated: {old}\ncurrent: M1\nmilestones: []\n---\n# Map\n")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(repo / "5_tasks" / "records" / "returns" / "T-1" / "return_T-1.md",
           f"---\nrecord_type: return_record\ntask_id: T-1\nreturned_at: {recent}\n---\n")
    draft = repo / "5_tasks" / "drafts" / "t-pub.md"
    _write(draft, "---\ntask_id: T-PUB\ntitle: Test Task\nproject: lybra\nassigned_to: test-agent\ncontext_bundle: test\ntask_mode: code\n"
                  "priority: low\nstatus: pending\ncreated_by: test\nneeds_owner: false\noutput_target: test/\nartifact_policy: formal_write\n---\n# Test\n")
    before = (repo / "governance" / "project-map.md").read_text(encoding="utf-8")
    result = publish_draft(repo, draft, dry_run=True)
    warnings = result.get("warnings", [])
    _show(f"[件⑤·陈旧地图 + 最近收编下 publish 预演 warnings] {warnings}")
    assert not [w for w in warnings if "PROJECT_MAP" in w or "地图" in w]
    assert (repo / "governance" / "project-map.md").read_text(encoding="utf-8") == before  # 文件不删不改
    src = (REPO_ROOT / "tools" / "aipos_cli" / "draft_writer.py").read_text(encoding="utf-8")
    assert "_check_project_map_staleness" not in src and '"project-map.md"' not in src


def test_item5_project_map_module_callers_listed():
    """AIPOS-F216 件①(F208 P3-3): project_map.py 已删(产品零调用方; 测试依赖随删迁走, AIPOS-276 旧测试一并删); 不得复活调用方。"""
    out = subprocess.run(["git", "grep", "-l", "-E", r"tools\.aipos_cli\.project_map|from tools\.aipos_cli import project_map", "--", "."],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert out.returncode in (0, 1), out.stderr
    callers = sorted(set(out.stdout.split()) - {"tests/test_aipos_f185_small_sweep.py"})
    _show(f"[件⑤·project_map 调用方] {callers}")
    assert callers == [] and not (REPO_ROOT / "tools" / "aipos_cli" / "project_map.py").exists()


# ===========================================================================
# 件⑥ 被 git 忽略的 __pycache__ 残留不进产品文件集(退役守卫同读)
# ===========================================================================

def test_item6_repo_files_ignore_bytecode_residue(tmp_path):
    from tools.aipos_cli.runall_discovery import repo_files

    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    _write(repo / ".gitignore", "__pycache__/\n*.pyc\n")
    _write(repo / "tools" / "keep.py", "# kept\n")
    _write(repo / "tools" / "retired_mod" / "__pycache__" / "rules.cpython-314.pyc", "x")
    _write(repo / "tools" / "aipos_cli" / "__pycache__" / "agent_supervise.cpython-314.pyc", "x")
    files = repo_files(repo)
    _show(f"[件⑥·产品文件集(含被忽略残留的检出)] {files}")
    assert files == [".gitignore", "tools/keep.py"]
    _write(repo / "tools" / "retired_mod" / "rules.py", "# 回潮\n")  # 源文件回潮(未跟踪未忽略)仍算产品文件 = 守卫照红
    assert "tools/retired_mod/rules.py" in repo_files(repo)
    for rel in ("tests/test_aipos_f91_retirement_invariants.py", "tests/test_aipos_f95_harness_launch.py"):
        assert "repo_files(REPO_ROOT)" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
