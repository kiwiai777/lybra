"""AIPOS-F140 — 身份读口认 role 分槽 + 产品自建治理文档带声明头(gap #122)。

件①: `.lybra/role` 分槽读取只有一个函数 enroll_client.read_role_record(lybra_dir, instance=None)(与写入器 write_role_file 的分槽格式
     同处); charter_render.workstation_identity(…, instance=) 与 `lybra charter --instance` 经它取该实例槽内记录(角色 / harness)。
件②: 其余治理席位实例读点(my-tasks --workstation 带 --actor、loop --actor 无连接文件时的驱动方角色名)走同一读口。
件③: 产品首次创建的 governance/ 下追加型 .md(custom_roles_log / naming_profile_log / dispatch_mode_log / enrollment_log)经唯一写口
     governance_add.append_governance_doc_line 写声明头(config.schema file_declarations.governance_doc.template_frontmatter 单源)。

靶场: 临时 HOME / 临时 home 根 / `lybra project new` 同一脚手架(workspace_config.scaffold_project)建的临时治理根;
两个顾问实例按 enroll 同一写入器(write_role_file slot_by_instance=True)与同一 land 事件 writer(_append_enrollment_trail)接入;
自定义角色按门动词 lybra_roles_register 的同一实现(custom_roles.register_custom_role)登记。禁真门 / 真治理根 / 真工位 / 真 pi。
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f131_loop_run_status import _cli  # noqa: E402  — 进程内 CLI 读法唯一来源
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

PROJECT = "f140probe"
ADV_MAIN = f"advisor.{PROJECT}.hosta"
OTA_ROLE = "advisor-ota"
ADV_OTA = f"{OTA_ROLE}.{PROJECT}.hostb"
EXEC = f"exec.{PROJECT}.hostc"


def _show(line: str) -> None:
    print(line, flush=True)


def _code(name: str) -> int:
    return declared_exit_code("lybra_charter", name)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`lybra project new` 同一脚手架建的临时治理根(git 仓, 便于提交门预演)。"""
    from tools.aipos_cli.workspace_config import scaffold_project

    for var in ("LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT", "LYBRA_WORKSPACE_ROOT", "LYBRA_HOME_ROOT"):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    hroot = tmp_path / "hroot"
    code_repo = tmp_path / "product"
    code_repo.mkdir()
    root = scaffold_project(hroot, PROJECT, code_repo=code_repo, registered_by="owner")
    (root / ".lybra").mkdir(exist_ok=True)
    (root / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1, "governance_root": str(root), "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
        "tokens": [{"role": "advisor", "role_class": "advisor", "agent_instance": ADV_MAIN, "token": "fixture-f140-main-not-a-secret",
                    "token_ref": "fixture-main", "scopes": []}],
    }, indent=2), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "f140@example.invalid")
    _git(root, "config", "user.name", "f140")
    _git(root, "add", "-A", "--", "project.json", "governance", "5_tasks")
    _git(root, "commit", "-q", "-m", "scaffold")
    return root


def _land(gov: Path, instance: str, role: str, workstation: Path) -> None:
    from tools.aipos_cli.enrollment import _append_enrollment_trail

    _append_enrollment_trail(gov, action="land", code_id=f"enroll_f140_{role}", role=role, instance=instance, by="(agent-enroll)",
                             reason=f"host={socket.gethostname()} workstation={workstation} files=['connection.json', 'role']")


def _enroll_two_advisors(gov: Path, session: Path) -> None:
    """先接入 advisor(claude-code, 本机会话目录), 后接入自定义角色 advisor-ota(codex, 他机会话)——enroll 同一写入器 / land writer。"""
    from tools.aipos_cli.custom_roles import register_custom_role
    from tools.aipos_cli.enroll_client import write_role_file

    register_custom_role(gov, OTA_ROLE, "advisor", by="owner", reason="F140 夹具: 第二个顾问角色")
    write_role_file(gov / ".lybra", "advisor", ADV_MAIN, None, harness={"kind": "claude-code", "dir": str(session)},
                    slot_by_instance=True)
    _land(gov, ADV_MAIN, "advisor", gov)
    write_role_file(gov / ".lybra", OTA_ROLE, ADV_OTA, None, harness={"kind": "codex", "dir": None, "host": "hostb"},
                    slot_by_instance=True)
    _land(gov, ADV_OTA, OTA_ROLE, gov)


# ===========================================================================
# 件① 读口本身(纯函数): 分槽 / 顶层 / 未登记拒 / 工位不变 / 坏文件 fail-closed
# ===========================================================================

def test_item1_read_role_record_slots_top_and_refusal(tmp_path: Path):
    from tools.aipos_cli.enroll_client import RoleInstanceNotRegistered, RoleRecordError, read_role_record, write_role_file

    seat = tmp_path / "seat" / ".lybra"
    seat.mkdir(parents=True)
    write_role_file(seat, "advisor", ADV_MAIN, "pol_main", slot_by_instance=True)
    write_role_file(seat, OTA_ROLE, ADV_OTA, "pol_ota", slot_by_instance=True)
    doc = json.loads((seat / "role").read_text(encoding="utf-8"))
    assert doc["instance"] == ADV_OTA and set(doc["instances"]) == {ADV_MAIN, ADV_OTA}
    main = read_role_record(seat, ADV_MAIN)
    ota = read_role_record(seat, ADV_OTA)
    top = read_role_record(seat)
    _show(f"[件① 读口] {ADV_MAIN} → role={main['role']} policy={main['owner_policy_ref']}; "
          f"{ADV_OTA} → role={ota['role']} policy={ota['owner_policy_ref']}; 未给实例 → 顶层 {top['instance']}")
    assert (main["role"], main["instance"], main["owner_policy_ref"]) == ("advisor", ADV_MAIN, "pol_main")
    assert (ota["role"], ota["instance"], ota["owner_policy_ref"]) == (OTA_ROLE, ADV_OTA, "pol_ota")
    assert top["instance"] == ADV_OTA and "instances" not in top and "instances" not in main
    with pytest.raises(RoleInstanceNotRegistered) as exc:
        read_role_record(seat, f"advisor.{PROJECT}.nowhere")
    _show(f"[件① 未登记实例拒] {exc.value}")
    assert exc.value.registered == [ADV_OTA, ADV_MAIN] and ADV_MAIN in str(exc.value) and ADV_OTA in str(exc.value)

    # 工位(无 instances 键): 未给实例 / 给顶层实例 = 顶层; 给别的实例 = 拒(列出唯一登记实例)
    ws = tmp_path / "ws" / ".lybra"
    ws.mkdir(parents=True)
    write_role_file(ws, "executor", EXEC, "pol_x")
    assert "instances" not in json.loads((ws / "role").read_text(encoding="utf-8"))
    assert read_role_record(ws)["instance"] == EXEC == read_role_record(ws, EXEC)["instance"]
    with pytest.raises(RoleInstanceNotRegistered) as exc:
        read_role_record(ws, ADV_MAIN)
    assert exc.value.registered == [EXEC] and exc.value.top_instance == EXEC and not exc.value.slotted

    # 无文件 = None(调用方判); 坏文件 / 非对象 / 槽非对象 = RoleRecordError(fail-closed, 不当「未声明」)
    assert read_role_record(tmp_path / "none" / ".lybra") is None
    bad = tmp_path / "bad" / ".lybra"
    bad.mkdir(parents=True)
    for text, needle in (("{not json", "非 JSON"), ("[1]", "须为 JSON 对象"),
                         (json.dumps({"role": "advisor", "instance": ADV_MAIN, "instances": {ADV_MAIN: "x"}}), "槽")):
        (bad / "role").write_text(text, encoding="utf-8")
        with pytest.raises(RoleRecordError) as exc:
            read_role_record(bad, ADV_MAIN)
        assert needle in str(exc.value), str(exc.value)


# ===========================================================================
# 验收①: 同一临时治理根先后接入 advisor 与 advisor-ota 两实例, 两者 `lybra charter --instance` 均成功, 渲染上下文各取其槽
# ===========================================================================

def test_acceptance1_two_advisor_instances_each_get_own_charter(gov: Path, tmp_path: Path):
    from tools.aipos_cli import charter_render as cr
    from tools.distribution_manifest import get_product_commit

    session = tmp_path / "advisor-session"
    session.mkdir()
    _enroll_two_advisors(gov, session)
    role_doc = json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))
    _show(f"[① 治理根 .lybra/role] 顶层实例={role_doc['instance']} 槽={sorted(role_doc['instances'])}")
    assert role_doc["instance"] == ADV_OTA  # 顶层 = 最近接入实例(修前 charter 只读顶层, 先接入的顾问被拒)

    outs: dict[str, str] = {}
    for inst, harness, dist_id in ((ADV_MAIN, "claude-code", "advisor-charter-claude-code"),
                                   (ADV_OTA, "codex", "advisor-charter-codex")):
        rc, out, err = _cli(["charter", "--role", "advisor", "--instance", inst, "--workspace-root", str(gov)])
        _show(f"---- ① `lybra charter --role advisor --instance {inst}` exit {rc}, stderr={err.strip()!r}, stdout 含实例/机器段行 ----\n"
              + "\n".join(l for l in out.splitlines() if inst in l or "lybra:charter-render" in l)[:1200])
        assert rc == _code("ok") == 0, err
        assert all(l.startswith("Warning: ") for l in err.splitlines()), err  # 只许夹具注册表告警, 无拒因
        assert "<!-- lybra:charter-render" in out and "{{" not in out and f"实例 `{inst}`" in out
        res = cr.instance_charter(gov, role_class="advisor", instance=inst)
        identity = cr.workstation_identity(gov, instance=inst)
        _show(f"[① 渲染上下文] instance={res['instance']} role={res['role']} harness={res['harness']} "
              f"distribution={res['distribution_id']} identity.harness={identity['harness']}")
        assert (res["instance"], res["harness"], res["distribution_id"]) == (inst, harness, dist_id)
        assert identity["instance"] == inst and identity["harness"]["kind"] == harness
        ctx = cr.charter_render_context(gov, identity=identity, product_commit=get_product_commit(REPO_ROOT))
        assert ctx["instance"] == inst and ctx["machine"] == inst.rsplit(".", 1)[1] and ctx["role"] == identity["role"]
        assert out == res["text"]
        assert "fixture-f140-main-not-a-secret" not in out
        outs[inst] = out
    assert cr.workstation_identity(gov, instance=ADV_OTA)["role"] == OTA_ROLE
    assert cr.workstation_identity(gov, instance=ADV_MAIN)["role"] == "advisor"
    assert f"实例 `{ADV_OTA}`" not in outs[ADV_MAIN] and f"实例 `{ADV_MAIN}`" not in outs[ADV_OTA]

    # 未给 --instance = 现行(顶层 = 最近接入实例)
    rc, out, _ = _cli(["charter", "--role", "advisor", "--workspace-root", str(gov)])
    assert rc == 0 and out == outs[ADV_OTA]

    # 未登记实例: ① 无 land 事件 = 既有拒因; ② 有 land 事件但 role 文件无其槽 = 读口拒, 列出本治理根已登记实例
    nowhere = f"advisor.{PROJECT}.nowhere"
    rc, out, err = _cli(["charter", "--role", "advisor", "--instance", nowhere, "--workspace-root", str(gov)])
    _show(f"[① 未接入实例拒] exit {rc}: {err.strip().splitlines()[-1]}")
    assert rc == _code("refused") and "未在本治理根接入" in err and out == ""
    ghost = f"advisor.{PROJECT}.ghost"
    _land(gov, ghost, "advisor", gov)
    rc, out, err = _cli(["charter", "--role", "advisor", "--instance", ghost, "--workspace-root", str(gov)])
    _show(f"[① 已 land 但 role 未登记实例拒] exit {rc}: {err.strip().splitlines()[-1]}")
    assert rc == _code("refused") and out == ""
    assert f"≠ '{ghost}'" in err and ADV_MAIN in err and ADV_OTA in err and "已登记实例" in err


# ===========================================================================
# 验收①(续): 工位 charter / identity 行为不变
# ===========================================================================

def _exec_workstation(tmp_path: Path, gov: Path) -> Path:
    ws = tmp_path / "workstations" / "exec"
    (ws / ".lybra").mkdir(parents=True)
    (ws / ".lybra" / "role").write_text(json.dumps({"role": "executor", "instance": EXEC}), encoding="utf-8")
    (ws / ".lybra" / "connection.json").write_text(json.dumps({
        "governance_root": str(gov), "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
        "tokens": [{"role": "executor", "role_class": "executor", "agent_instance": EXEC, "token": "fixture-ws-not-a-secret",
                    "scopes": []}]}), encoding="utf-8")
    return ws


def test_acceptance1_workstation_charter_and_identity_unchanged(gov: Path, tmp_path: Path):
    from tools.aipos_cli import charter_render as cr

    ws = _exec_workstation(tmp_path, gov)
    plain = cr.workstation_identity(ws)
    assert plain == cr.workstation_identity(ws, instance=EXEC)
    assert (plain["role"], plain["instance"], plain["project"], plain["harness"]) == ("executor", EXEC, PROJECT, None)
    _land(gov, EXEC, "executor", ws)
    rc, out, err = _cli(["charter", "--role", "executor", "--instance", EXEC, "--workspace-root", str(gov)])
    _show(f"[① 工位 pi 章程] exit {rc}, 首行: {out.splitlines()[0] if out else ''}")
    assert rc == 0 and f"实例 `{EXEC}`" in out and "{{" not in out
    # 工位已被别的实例接入: 拒因原文与修前同(无槽工位不附登记清单)
    other = f"exec.{PROJECT}.hostd"
    _land(gov, other, "executor", ws)
    rc, out, err = _cli(["charter", "--role", "executor", "--instance", other, "--workspace-root", str(gov)])
    _show(f"[① 工位实例不符拒] exit {rc}: {err.strip().splitlines()[-1]}")
    assert rc == _code("refused") and out == ""
    assert f"登记工位 {ws} 的 .lybra/role 实例 = '{EXEC}' ≠ '{other}'(工位已被别的实例接入); 出口: 核对实例名或重新 enroll" in err
    with pytest.raises(cr.WorkstationInstanceNotRegistered):
        cr.workstation_identity(ws, instance=other)


# ===========================================================================
# 件②: my-tasks --workstation <治理根> --actor <非顶层顾问实例> 经读口取该实例槽; 工位 actor 不符拒因不变
# ===========================================================================

def test_item2_my_tasks_workstation_actor_reads_slot(gov: Path, tmp_path: Path):
    import argparse

    from tools.aipos_cli.aipos_cli import _resolve_my_tasks_workstation

    session = tmp_path / "advisor-session"
    session.mkdir()
    _enroll_two_advisors(gov, session)
    args = argparse.Namespace(workstation=str(gov), actor=ADV_MAIN, workspace_root=None, global_workspace_root=None)
    assert _resolve_my_tasks_workstation(args) is None
    _show(f"[② my-tasks --workstation 治理根 --actor {ADV_MAIN}] → {args.workstation_identity}")
    assert args.actor == ADV_MAIN and args.workstation_identity["role"] == "advisor"
    args = argparse.Namespace(workstation=str(gov), actor=None, workspace_root=None, global_workspace_root=None)
    assert _resolve_my_tasks_workstation(args) is None and args.actor == ADV_OTA  # 未给 actor = 顶层(现行)

    ws = _exec_workstation(tmp_path, gov)
    import contextlib
    import io

    err = io.StringIO()
    args = argparse.Namespace(workstation=str(ws), actor=ADV_MAIN, workspace_root=None, global_workspace_root=None)
    with contextlib.redirect_stderr(err):
        rc = _resolve_my_tasks_workstation(args)
    _show(f"[② 工位 actor 不符] rc={rc}: {err.getvalue().strip()}")
    assert rc == 2 and f"--actor {ADV_MAIN} ≠ 工位 {ws.resolve()} 的实例 {EXEC}(二者择一, 以工位身份为准)" in err.getvalue()


def test_item2_loop_actor_role_name_without_connection_reads_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """loop --actor 在 scope 内且治理根无连接文件(靶场形): 驱动方角色名 = 该实例槽的 role(修前: 非顶层实例取不到 = "")。"""
    from tools.aipos_cli import next_resolver
    from tools.aipos_cli.enroll_client import write_role_file

    root = tmp_path / "gov"
    (root / ".lybra").mkdir(parents=True)
    write_role_file(root / ".lybra", "advisor", ADV_MAIN, None, slot_by_instance=True)
    write_role_file(root / ".lybra", OTA_ROLE, ADV_OTA, None, slot_by_instance=True)
    monkeypatch.setattr(next_resolver, "_find_connection_json", lambda _root: None)
    for actor, want in ((ADV_MAIN, "advisor"), (ADV_OTA, OTA_ROLE), (f"advisor.{PROJECT}.nowhere", "")):
        monkeypatch.setattr(next_resolver, "_scoped_driver", lambda a=actor: {"actor": a})
        got = next_resolver._driver_role_name(root)
        _show(f"[② loop --actor {actor}(无连接文件)] 驱动方角色名 = {got!r}")
        assert got == want


# ===========================================================================
# 防碎片化: 分槽读取只有一个函数; 产品代码无第二处 json 读 role 文件
# ===========================================================================

def test_single_slot_reader_invariant():
    """读 role 身份的模块(调用工位身份 / 身份解析 / 分槽读口者)不得自取 instances 槽; 分槽读取只在 enroll_client.read_role_record。"""
    import re

    files = subprocess.run(["git", "ls-files", "tools/aipos_cli/*.py"], cwd=str(REPO_ROOT), capture_output=True, text=True,
                           check=True).stdout.split()
    readers = re.compile(r"workstation_identity\(|resolve_identity\(|resolve_role\(|read_role_record\(")
    slot_access = re.compile(r"""\.get\(\s*["']instances["']\s*\)|\[\s*["']instances["']\s*\]|ROLE_SLOTS_KEY""")
    role_readers, hits = [], []
    for f in (x for x in files if "/tests/" not in x):
        text = (REPO_ROOT / f).read_text(encoding="utf-8")
        if not readers.search(text):
            continue
        role_readers.append(f)
        hits += [f"{f}:{n}" for n, line in enumerate(text.splitlines(), 1) if slot_access.search(line)]
    _show(f"[防碎片化] 读 role 身份的模块 {len(role_readers)} 个; 其中 instances 槽访问点: " + ", ".join(hits))
    assert "tools/aipos_cli/charter_render.py" in role_readers and "tools/aipos_cli/next_resolver.py" in role_readers
    assert hits and {h.split(":", 1)[0] for h in hits} == {"tools/aipos_cli/enroll_client.py"}, hits
    enroll_src = (REPO_ROOT / "tools" / "aipos_cli" / "enroll_client.py").read_text(encoding="utf-8")
    assert enroll_src.count("def read_role_record(") == 1
    cr_src = (REPO_ROOT / "tools" / "aipos_cli" / "charter_render.py").read_text(encoding="utf-8")
    body = cr_src.split("def workstation_identity(", 1)[1].split("\ndef ", 1)[0]
    assert "read_role_record(" in body and "role_file.read_text" not in body


# ===========================================================================
# 件③ / 验收③: 新治理根首次 roles register 后 custom_roles_log.md 带声明头, 过提交门 B② 预演; 产品写治理文档入口不变量
# ===========================================================================

def test_acceptance3_first_roles_register_log_passes_b2(gov: Path):
    from tools.aipos_cli.custom_roles import register_custom_role
    from tools.aipos_cli.governance_add import governance_doc_frontmatter
    from tools.aipos_cli.governance_commit import governance_commit
    from tools.aipos_cli.governance_guardrails import check_entries, load_guardrail_declarations

    log = gov / "governance" / "custom_roles_log.md"
    assert not log.exists()
    register_custom_role(gov, OTA_ROLE, "advisor", by="owner", reason="F140 首次登记")
    text = log.read_text(encoding="utf-8")
    _show(f"[③ 首建 custom_roles_log.md]\n{text}")
    assert text.startswith(governance_doc_frontmatter() + "\n# Custom Roles Registry Log (append-only)\n\n")
    register_custom_role(gov, "ops-lead", "planner", by="owner", reason="第二次")
    assert log.read_text(encoding="utf-8").count("---") == 2 and log.read_text(encoding="utf-8").count("register") == 2

    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    rel = log.relative_to(gov.parent).as_posix()
    rep = check_entries(gov.parent, [("A", rel)], decls, current_branch="main")
    _show(f"[③ 提交门 B② 判据 check_entries({rel})] ok={rep.ok} violations={rep.violations}")
    assert rep.ok, rep.violations
    dry = governance_commit(gov, None, ADV_MAIN, repo_root=REPO_ROOT, dry_run=True, push=False,
                            paths=["governance/custom_roles_log.md"])
    _show(f"[③ governance-commit --paths governance/custom_roles_log.md --dry-run] verdict={dry.get('verdict')} "
          f"message={str(dry.get('message'))[:300]!r}")
    assert dry.get("verdict") == "PASS", dry
    assert not [v for v in (dry.get("guardrail_report") or {}).get("violations", []) if v.get("check") == "B②"]


def test_item3_every_product_governance_trail_writer_creates_declared_header(gov: Path, tmp_path: Path):
    """不变量: 产品写 governance/ 下追加型文档的各入口, 新建文件即过 B②; 既有无头文件不动(不补头)。"""
    from tools.aipos_cli.enrollment import _append_enrollment_trail
    from tools.aipos_cli.governance_add import governance_doc_frontmatter
    from tools.aipos_cli.governance_guardrails import check_entries, load_guardrail_declarations
    from tools.aipos_cli.naming_profile import _append_naming_trail
    from tools.aipos_cli.workspace_config import set_dispatch_mode

    writers = {
        "custom_roles_log.md": lambda: __import__("tools.aipos_cli.custom_roles", fromlist=["x"])._append_custom_role_trail(
            gov, change_type="register", name="x-role", builtin_class="executor", by="owner", reason="t"),
        "naming_profile_log.md": lambda: _append_naming_trail(gov, change_type="switch", by="owner", reason="t",
                                                              profile={"prefix_mapping": {"executor": "exec"}}),
        "dispatch_mode_log.md": lambda: set_dispatch_mode(gov, "auto", by="owner", reason="t"),
        "enrollment_log.md": lambda: _append_enrollment_trail(gov, action="create", code_id="c1", role="executor",
                                                              instance=EXEC, by="pol", reason="t"),
    }
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    for name, write in writers.items():
        path = gov / "governance" / name
        if path.exists():
            path.unlink()
        write()
        write()
        text = path.read_text(encoding="utf-8")
        rep = check_entries(gov.parent, [("A", path.relative_to(gov.parent).as_posix())], decls, current_branch="main")
        _show(f"[③ 不变量] {name}: 头={text.startswith(governance_doc_frontmatter())} B②={'PASS' if rep.ok else rep.violations}")
        assert text.startswith(governance_doc_frontmatter() + "\n# ") and text.count("---") == 2 and rep.ok, (name, text)
    # 既有无头文件不动: 追加不补头
    legacy = gov / "governance" / "naming_profile_log.md"
    legacy.write_text("# Naming Profile Switch Log (append-only)\n\n- old line\n", encoding="utf-8")
    _append_naming_trail(gov, change_type="switch", by="owner", reason="t2", profile={"prefix_mapping": {}})
    assert legacy.read_text(encoding="utf-8").startswith("# Naming Profile Switch Log (append-only)\n\n- old line\n")
    # 源码不变量: 这些入口不再各自写首行标题(唯一写口 governance_add.append_governance_doc_line)
    for rel in ("tools/aipos_cli/custom_roles.py", "tools/aipos_cli/naming_profile.py", "tools/aipos_cli/workspace_config.py",
                "tools/aipos_cli/enrollment.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "append_governance_doc_line" in src and 'trail.open("a"' not in src, rel
