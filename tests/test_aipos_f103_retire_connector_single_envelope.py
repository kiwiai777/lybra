"""AIPOS-F103 夹具: 退役旧跨机连接器与执行体派工命令(N-a: N2) + 信封挑选只留一个判据(M5)。

依据: governance/research/2026-10-04-fragmentation-recheck.md(N2/M5); Owner 10-04 裁定旧跨机连接器整体退役
(执行体自认领自确认违执行体零门)。

验收(卡面 ★验收 ①–④; ⑤⑥ 见 RETURN):
  ① 被删命令 argparse 报不存在(agent 拉取/材料化/回推子命令、`agent watch` 门拉取模式、顶层派工命令),
    `agent watch --workspace-root` 照常(loop 唯一哨兵)。
  ② git grep 删除物零引用(task_cards/ 除外; 豁免面逐条声明: 本夹具的断言清单、F96 文档棘轮的退役做法探测式样)。
  ③ 信封挑选单实现: autonomy_policy.select_envelope 是唯一挑选, 判据只有 match_claim_envelope; 驱动方(loop_driver.find_envelope)、
    工位推导(workstation_wiring.derive_effective_owner_policy_ref)、契约节(gate_contract_section)、审计上下文自发现
    (audit_helpers.resolve_audit_context)、推导核派审(next_resolver)全部经它; 信封目录读 project.json paths.policies_root。
  ④ 文档棘轮基线减少计数(README/QUICKSTART 待退役段与 agents/** 两条已修并删出基线; 扫描面含仓根 skills/)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_LYBRA = REPO_ROOT / "bin" / "lybra"
THIS_FILE = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

# 删除物清单(②的断言数据)。文件 = 必须不存在; 字面 = 仓内(豁免面外)零引用。
DELETED_FILES = [
    "tools/aipos_cli/agent_connector.py",
    "tools/aipos_cli/agent_materialize.py",
    "tools/aipos_cli/kickoff_safe.py",
    "tools/aipos_cli/policy_resolver.py",
    "tools/aipos_cli/test_dispatch_integration.py",
    "tools/aipos_cli/tests/test_agent_connector.py",
    "tools/aipos_cli/tests/test_aipos363_materialize.py",
    "tools/aipos_cli/tests/test_aipos363_materialize_gate.py",
    "tests/test_aipos_340f1_s6_policy_resolver.py",
    "skills/lybra-executor/SKILL.md",
    "skills/owner-console/SKILL.md",
    "skills/lybra-planner/SKILL.md",
]
DELETED_LITERALS = [
    "agent_connector",
    "agent_materialize",
    "kickoff_safe",
    "KICKOFF_HAZARDS",
    "policy_resolver",
    "find_active_policy",
    "_policy_matches_role",
    "_resolve_active_policy",
    "_run_dispatch",
    "run_materialize",
    "run_pushback",
    "test_dispatch_integration",
    # 仓根旧技能按 SKILL.md 路径判(工位分发物名 skills/lybra-executor 是工位侧技能目录名, 如 F113 夹具的假分发输出, 不是仓根旧技能)
    "skills/lybra-executor/SKILL.md",
    "skills/owner-console",
    "skills/lybra-planner",
    "agent fetch",
    "agent materialize",
    "agent pushback",
    "lybra dispatch",
    "watch --gate-url",
    "POLICIES_DIR",
]
# 豁免面(逐条声明理由, 见模块文档 ②)
GREP_EXEMPT = [
    "task_cards/",
    THIS_FILE,
    "tests/test_aipos_f96_docs_retired_practice_ratchet.py",  # 退役做法探测式样(字面即探测器)
]


def _lybra(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(BIN_LYBRA), *args], cwd=str(cwd or REPO_ROOT), capture_output=True, text=True, timeout=60)


# ── ① 被删命令 argparse 报不存在; agent watch --workspace-root 照常 ─────────────────────────────


@pytest.mark.parametrize("sub", ["fetch", "materialize", "pushback"])
def test_retired_agent_subcommands_do_not_exist(sub):
    proc = _lybra("agent", sub, "--gate-url", "http://127.0.0.1:1", "--task-id", "X-1")
    assert proc.returncode == 2, proc
    assert f"invalid choice: '{sub}'" in proc.stderr and "(choose from watch)" in proc.stderr, proc.stderr


def test_retired_top_level_dispatch_does_not_exist():
    proc = _lybra("dispatch", "X-1", "--to", "exec.x")
    assert proc.returncode == 2, proc
    assert "invalid choice: 'dispatch'" in proc.stderr, proc.stderr
    # 审计派发(lybra audit dispatch)是另一条现行命令, 不受影响
    assert _lybra("audit", "dispatch", "--help").returncode == 0


def test_agent_watch_gate_mode_is_gone(tmp_path):
    proc = _lybra("agent", "watch", "--gate-url", "http://127.0.0.1:1")
    assert proc.returncode == 2 and "the following arguments are required: --workspace-root" in proc.stderr, proc.stderr
    (tmp_path / "5_tasks" / "queue").mkdir(parents=True)
    proc = _lybra("agent", "watch", "--workspace-root", str(tmp_path), "--gate-url", "http://127.0.0.1:1", "--timeout", "1")
    assert proc.returncode == 2 and "unrecognized arguments: --gate-url" in proc.stderr, proc.stderr


def test_agent_watch_workspace_root_still_works(tmp_path):
    """loop 唯一哨兵照常: 无变化 → 超时静默 exit 2; 预期产物已在 → exit 0。"""
    (tmp_path / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (tmp_path / "5_tasks" / "records").mkdir(parents=True)
    proc = _lybra("agent", "watch", "--workspace-root", str(tmp_path), "--timeout", "1", "--interval", "0.2")
    assert proc.returncode == 2 and proc.stdout == "" and proc.stderr == "", proc
    (tmp_path / "5_tasks" / "records" / "RETURN.md").write_text("done\n", encoding="utf-8")
    proc = _lybra("agent", "watch", "--workspace-root", str(tmp_path), "--expect", "5_tasks/records/RETURN.md",
                  "--timeout", "5", "--interval", "0.2")
    assert proc.returncode == 0, proc


# ── ② 删除物零引用 ──────────────────────────────────────────────────────────────────────────────


def test_deleted_files_are_gone():
    present = [rel for rel in DELETED_FILES if (REPO_ROOT / rel).exists()]
    assert not present, present
    assert not (REPO_ROOT / "skills").exists() or not any((REPO_ROOT / "skills").rglob("*")), "仓根旧技能已删"


# 车道外逐字面豁免(F103 车道不含 schema/verbs.schema.json): F110 拉起声明文案「kickoff_safe 危险字符」只是文字指称(无 import),
# 登记为缺口由改该声明的卡改写; 只豁免这一字面在这一文件
LANE_BLOCKED_LITERAL_EXEMPT = {("kickoff_safe", "schema/verbs.schema.json")}


def test_git_grep_deleted_items_zero_references():
    hits: list[str] = []
    for literal in DELETED_LITERALS:
        proc = subprocess.run(["git", "grep", "-n", "-F", literal], cwd=REPO_ROOT, capture_output=True, text=True)
        assert proc.returncode in (0, 1), proc.stderr  # 1 = 无命中
        for line in proc.stdout.splitlines():
            if line.startswith(tuple(GREP_EXEMPT)) or (literal, line.split(":", 1)[0]) in LANE_BLOCKED_LITERAL_EXEMPT:
                continue
            hits.append(f"{literal!r}: {line[:200]}")
    assert not hits, "\n".join(hits)


# ── ③ 信封挑选单实现 ─────────────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _policy(policy_id: str, *, agent_or_role: str, task_mode: str = "code", project: str = "fx", status: str = "active",
            expires_at: str = "2099-01-01T00:00:00Z", max_tasks: int = 5) -> str:
    return (
        "---\n"
        "record_type: owner_autonomy_policy\n"
        f"policy_id: {policy_id}\n"
        "mode: PreAuthorized\n"
        f"status: {status}\n"
        "approved_by_owner: true\n"
        f"owner_approval_ref: dec_{policy_id}\n"
        "active_from: '2020-01-01T00:00:00Z'\n"
        f"expires_at: '{expires_at}'\n"
        f"agent_or_role: {agent_or_role}\n"
        f"task_selector_task_mode: '{task_mode}'\n"
        f"task_selector_project: '{project}'\n"
        "task_selector_task_ids: []\n"
        f"max_tasks: {max_tasks}\n"
        "---\n"
        f"# Owner Autonomy Policy: {policy_id}\n"
    )


def _gov(tmp_path: Path, *, policies_root: str | None = None) -> tuple[Path, Path]:
    """治理根夹具: 一张有效信封 + 三张各错一处(过期 / 空选择器 / 身份不覆盖), 外加一张 audit-mode 的审计信封。"""
    gov = tmp_path / "gov"
    project: dict = {"project": "fx", "config_version": 1, "manual_gate_mode": True}
    if policies_root:
        project["paths"] = {"policies_root": policies_root}
    gov.mkdir(parents=True)
    (gov / "project.json").write_text(json.dumps(project), encoding="utf-8")
    pdir = gov / (policies_root or "5_tasks/policies")
    pdir.mkdir(parents=True)
    (pdir / "pol_a_expired.md").write_text(_policy("pol_a_expired", agent_or_role="exec.fx.h", expires_at="2021-01-01T00:00:00Z"), encoding="utf-8")
    (pdir / "pol_b_empty_selector.md").write_text(_policy("pol_b_empty_selector", agent_or_role="exec.fx.h", task_mode="", project=""), encoding="utf-8")
    (pdir / "pol_c_other.md").write_text(_policy("pol_c_other", agent_or_role="someone.else"), encoding="utf-8")
    (pdir / "pol_d_valid.md").write_text(_policy("pol_d_valid", agent_or_role="exec.fx.h"), encoding="utf-8")
    (pdir / "pol_e_audit.md").write_text(_policy("pol_e_audit", agent_or_role="audit.fx.h"), encoding="utf-8")
    (pdir / "pol_f_driver.md").write_text(_policy("pol_f_driver", agent_or_role="advisor"), encoding="utf-8")
    return gov, pdir


def test_select_envelope_is_the_only_selection_and_uses_the_single_predicate():
    """源级: 挑选只在 autonomy_policy.select_envelope; 各调用方经它, 不再自带有效性/覆盖判定。"""
    ap = (REPO_ROOT / "tools/aipos_cli/autonomy_policy.py").read_text(encoding="utf-8")
    assert ap.count("def select_envelope(") == 1 and "matched, reason, _code = match_claim_envelope(" in ap
    callers = {
        "tools/aipos_cli/loop_driver.py": "select_envelope(",
        "tools/aipos_cli/workstation_wiring.py": "select_envelope(",
        "tools/aipos_cli/gate_contract_section.py": "select_envelope(",
        "tools/aipos_cli/audit_helpers.py": "select_envelope(",
    }
    for rel, needle in callers.items():
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert needle in src, rel
        for banned in ('"approved_by_owner"', "approved_by_owner\")", 'policy.get("status")', "normalize_policy(", 'glob("pol_*.md")'):
            assert banned not in src, (rel, banned)
    nr = (REPO_ROOT / "tools/aipos_cli/next_resolver.py").read_text(encoding="utf-8")
    assert "_driver_envelope_ref(" in nr and "from tools.aipos_cli.loop_driver import find_envelope" in nr
    # 除声明的挑选实现外, 产品代码里只有门侧逐请求核验(给定 owner_policy_ref)调 match_claim_envelope
    proc = subprocess.run(["git", "grep", "-l", "match_claim_envelope(", "--", "tools/*.py", "tools/**/*.py"], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    files = {f for f in proc.stdout.split() if "/tests/" not in f}
    assert files == {"tools/aipos_cli/autonomy_policy.py", "tools/mcp_server/tools.py"}, files
    # 信封目录不写死(读 project.json paths.policies_root)
    proc = subprocess.run(["git", "grep", "-nE", r'"5_tasks" / "policies"|Path\("5_tasks/policies"\)', "--", "tools", ":!**/tests/**"], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    assert proc.stdout == "", proc.stdout


def test_policies_root_declared_in_config_schema_and_reader():
    cfg = json.loads((REPO_ROOT / "schema/config.schema.json").read_text(encoding="utf-8"))
    decl = cfg["configuration_sources"]["project_json"]["schema"]["paths"]["schema"]["policies_root"]
    assert decl["default"] == "5_tasks/policies" and decl["type"] == "string"
    from tools.aipos_cli.workspace_config import PROJECT_PATH_KEYS

    assert "policies_root" in PROJECT_PATH_KEYS


@pytest.mark.parametrize("policies_root", [None, "governance/envelopes"])
def test_all_callers_pick_the_same_valid_envelope(tmp_path, policies_root):
    """同一治理根(含过期/空选择器/不覆盖的干扰信封), 各调用方挑出同一张有效信封; 声明目录改了全部跟随。"""
    from tools.aipos_cli.autonomy_policy import load_policy, policies_dir, select_envelope
    from tools.aipos_cli.gate_contract_section import render_gate_contract_section
    from tools.aipos_cli.workstation_wiring import derive_effective_owner_policy_ref

    gov, pdir = _gov(tmp_path, policies_root=policies_root)
    assert policies_dir(gov).resolve() == pdir.resolve()
    assert load_policy(gov, "pol_d_valid")["policy_id"] == "pol_d_valid"
    ident = [("exec.fx.h", "exec.fx.h", None)]
    assert select_envelope(gov, identities=ident, now=NOW)[0]["policy_id"] == "pol_d_valid"
    assert select_envelope(gov, identities=ident, task={"task_id": "FX-1", "task_mode": "code", "project": "fx"}, now=NOW)[0]["policy_id"] == "pol_d_valid"
    # 工位推导: 旧实现会挑空选择器信封(门上不授权任何卡); 唯一判据下挑有效信封
    assert derive_effective_owner_policy_ref(gov, role="executor", agent_instance="exec.fx.h", now=NOW) == ("pol_d_valid", "matched")
    # 契约节(manual_gate_mode 项目): 卡面实例 + 本卡判定
    card = {"task_mode": "code", "project": "fx", "assigned_to": "exec.fx.h", "agent_instance": "exec.fx.h", "audit_by": "audit.fx.h"}
    section = render_gate_contract_section({}, card, role="executor", gate_url="http://127.0.0.1:1", connection_json_rel=".lybra/connection.json",
                                           workspace_display=str(gov), task_id="FX-1", workspace_root=gov)
    assert "pol_d_valid" in section and "pol_b_empty_selector" not in section
    # 驱动方 loop: 角色类 advisor 信封
    from tools.aipos_cli.loop_driver import find_envelope

    policy, _ = find_envelope(gov, task_id="FX-1", task_fm={"task_mode": "code", "project": "fx"}, driver_actor="advisor.fx.h", now=NOW)
    assert policy["policy_id"] == "pol_f_driver"
    # 原因可读(每张未中信封各一条)
    none, reasons = select_envelope(gov, identities=[("nobody", "nobody", None)], now=NOW)
    assert none is None and len(reasons) == 6 and any("pol_a_expired: policy has expired" in r for r in reasons), reasons


def test_every_caller_routes_through_match_claim_envelope(tmp_path, monkeypatch):
    """行为级: 判据唯一 —— 把 match_claim_envelope 换成恒拒, 所有挑选调用方都挑不出信封。"""
    import tools.aipos_cli.autonomy_policy as ap
    from tools.aipos_cli.gate_contract_section import render_gate_contract_section
    from tools.aipos_cli.loop_driver import find_envelope
    from tools.aipos_cli.workstation_wiring import derive_effective_owner_policy_ref

    gov, _pdir = _gov(tmp_path)
    calls: list[str] = []

    def deny(**kwargs):
        calls.append(str(kwargs["policy"].get("policy_id")))
        return False, "denied by spy", None

    monkeypatch.setattr(ap, "match_claim_envelope", deny)
    assert derive_effective_owner_policy_ref(gov, role="executor", agent_instance="exec.fx.h", now=NOW)[0] is None
    assert find_envelope(gov, task_id="FX-1", task_fm={"task_mode": "code", "project": "fx"}, driver_actor="advisor.fx.h", now=NOW)[0] is None
    with pytest.raises(ValueError, match="denied by spy"):
        render_gate_contract_section({}, {"task_mode": "code", "project": "fx", "agent_instance": "exec.fx.h"}, role="executor",
                                     gate_url="http://127.0.0.1:1", connection_json_rel=".lybra/connection.json",
                                     workspace_display=str(gov), task_id="FX-1", workspace_root=gov)
    assert set(calls) >= {"pol_a_expired", "pol_b_empty_selector", "pol_c_other", "pol_d_valid", "pol_e_audit", "pol_f_driver"}


def test_audit_context_discovery_uses_single_selection(tmp_path):
    """审计上下文自发现: 身份 = token_resolver 单源挑中的凭据条目; 信封经唯一挑选。"""
    from tools.aipos_cli.audit_helpers import resolve_audit_context

    gov, _pdir = _gov(tmp_path)
    (gov / ".lybra").mkdir()
    (gov / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
        "tokens": [{"role": "auditor", "token": "fx-synthetic-auditor", "agent_instance": "audit.fx.h", "scopes": []}],
    }), encoding="utf-8")
    ctx = resolve_audit_context(workspace_root=gov, role="auditor", gate_url="http://127.0.0.1:1")
    assert ctx["agent_instance"] == "audit.fx.h" and ctx["owner_policy_ref"] == "pol_e_audit", ctx


def test_owner_decision_writer_lands_envelope_in_declared_root(tmp_path):
    """门写入面: 信封工件落声明目录; 声明在治理根外 = BLOCK(fail-closed)。"""
    from tools.aipos_cli.owner_decision_writer import build_owner_decision_record

    payload = {
        "decision_id": "dec-fx-1", "actor": "owner", "decided_by_ref": "owner",
        "autonomy_policy": {"policy_id": "pol_fx_new", "agent_or_role": "exec.fx.h", "active_from": "2026-10-04T00:00:00Z",
                            "expires_at": "2026-10-10T00:00:00Z", "max_tasks": 3, "task_selector": {"task_mode": "code"}},
    }
    gov, _ = _gov(tmp_path / "a", policies_root="governance/envelopes")
    res = build_owner_decision_record(gov, payload, actor="owner", dry_run=True)
    assert res["autonomy_policy_path"] == "governance/envelopes/pol_fx_new.md", res.get("blocking_reasons")
    outside = tmp_path / "b"
    gov2, _ = _gov(outside, policies_root=str(tmp_path / "elsewhere"))
    res2 = build_owner_decision_record(gov2, payload, actor="owner", dry_run=True)
    assert any("policies_root" in str(r) for r in res2["blocking_reasons"]), res2["blocking_reasons"]


def test_next_resolver_dispatch_uses_driver_envelope():
    """推导核派审/交回/裁决: owner_policy_ref = 覆盖驱动方与本卡的信封(_driver_envelope_ref → loop_driver.find_envelope →
    select_envelope), 不再另从认领记录取信封(旧第二来源删除)。"""
    import tools.aipos_cli.next_resolver as nr

    assert not hasattr(nr, "_resolve_active_policy")
    src = Path(nr.__file__).read_text(encoding="utf-8")
    block = src[src.index('if not latest_audit_dispatch and (task_mode == "code" or audit_required):'):]
    block = block[: block.index("return {")]
    assert "policy_ref = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg)" in block
    assert "driver_policy or policy_ref" not in src


# ── ④ 文档棘轮基线减少 ───────────────────────────────────────────────────────────────────────────


def test_docs_ratchet_baseline_shrunk_and_scope_covers_skills():
    baseline = json.loads((REPO_ROOT / "tests/f96_docs_retired_practice_baseline.json").read_text(encoding="utf-8"))
    files = {e["file"] for e in baseline["entries"]}
    # F96 基线 27 条: AIPOS-F105 删 templates/ 与 lybra init 段 24 条后余 3 条(README 旧连接器 1 + agents/** 2), 本卡修掉这 3 条 → 0
    assert baseline["count"] == len(baseline["entries"]) == 0, baseline["entries"]
    assert not files & {"README.md", "QUICKSTART.md", "agents/roles/advisor/AGENTS.md", "agents/harness/pi/README.md"}, files
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    try:
        import test_aipos_f96_docs_retired_practice_ratchet as f96
    finally:
        sys.path.pop(0)
    assert f96.in_doc_scope("skills/x/SKILL.md")
    for retired in ("lybra agent fetch --gate-url x", "lybra dispatch X-1 --to y"):
        assert f96.scan_text("skills/x/SKILL.md", retired + "\n"), retired


def test_readme_quickstart_board_teach_only_current_practice():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quick = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    board = (REPO_ROOT / "web/board/app.py").read_text(encoding="utf-8")
    assert "Legacy entry points" not in readme and "旧入口(待退役)" not in quick
    assert "lybra agent watch --workspace-root" in readme and "lybra agent watch --workspace-root" in quick
    assert "--gate-url {gate_url} --token" not in board and "lybra agent watch --workspace-root {workspace_root}" in board
