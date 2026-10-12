"""AIPOS-F159(gap #142 / #143 / #144) — 配置与记录落账收尾。

件① project 族写 project.json 的命令(set-paths / set-repo / set-repos / set-workstation / set-meta / set-execution / set-return-summary /
    freeze-legacy / dispatch-mode set)--confirm 成功后, 经 F158 写后落账同一包装 governance_commit.land_written_paths(N6 同一提交口)
    精确提交并推送本次写出的文件, 提交信息含子命令与项目名; 落账失败不回滚写入、给补账命令并以声明退出码退出; --dry-run 零写入不变。
件② decision_log 自动指针文件名唯一键 = 完整 decision_id(信封 = envelope-<policy_id>): 同日多签全部生成; 旧名指针不重命名, 读取侧
    (brief / decision_log_pointers)按目录与正文 Record 行读; 历史缺指针 `lybra migrate decision-log-pointers` 只列不补。
件③ 剩余按角色取凭据收口到 F134/F154 按实例选凭据唯一口 two_phase_shell_factory.load_role_token(ConnectionResolver / TUI 两会话 /
    confined_worker); R2 接入指南的命令行带码示例改 enroll-deliver。

靶场 = 临时 home 根(git 仓 + 临时远端裸仓, 治理根嵌在仓内子目录 = chris 形前缀)。项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
全程不读真实凭据、不碰真实治理根。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shlex
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源
from test_aipos_f158_envelope_landing_record_type_migration import InProcessGate  # noqa: E402  — 门替身唯一来源(只替换 HTTP 传输)

PROJECT = "f159-probe"                 # 靶场项目名(非真实项目)
ADVISOR = f"advisor.{PROJECT}.h1"
SUB = f"exec.{PROJECT}.advhost"        # 子 agent 执行者
AUDITOR = f"audit.{PROJECT}.ws"        # pi 审计工位


def _show(text: str) -> None:
    print(text, flush=True)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
                          cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _tree_digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """home 根 = 治理仓(带 origin 裸仓); 治理根 = <home>/<PROJECT>(仓内子目录, 提交前缀 = chris 形)。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", PROJECT, "--home-root", str(hroot), "--actor", ADVISOR)
    assert rc == 0, out + err
    remote = init_governance_repo(hroot)
    conn = tmp_path / "owner-vault" / "connection.json"  # 靶场 Owner 凭据(假 token, 仓外; 只出指纹)
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                             "tokens": [{"role": "owner", "token": "fixture-owner-token-f159", "scopes": ["owner_confirm"]}]}))
    return {"home": hroot, "gov": hroot / PROJECT, "remote": remote, "conn": conn, "tmp": tmp_path}


def _landed_state(rig) -> tuple[str, list[str], str, bool]:
    home = rig["home"]
    msg = _git(home, "log", "-1", "--format=%B")
    names = _git(home, "show", "--name-only", "--format=", "HEAD").splitlines()
    status = _git(home, "status", "--porcelain", "-uall")
    pushed = _git(home, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")
    return msg, names, status, pushed


def _land_event(gov: Path, instance: str, role: str, reason: str) -> None:
    """接入日志 land 行(产品唯一渲染 _trail_line + 唯一写口 _write_trail_line), 项目 = 靶场项目。"""
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path

    _write_trail_line(enrollment_trail_path(gov), _trail_line(action="land", code_id="enroll_fixture", role=role, instance=instance,
                                                              project=PROJECT, by="(agent-enroll)", reason=reason))


# ===========================================================================
# 件① project 族 --confirm 写后落账
# ===========================================================================

def test_item1_set_paths_and_set_execution_confirm_land_dry_run_zero_write(rig):
    from tools.aipos_cli.enrollment import subagent_land_detail

    home, gov = rig["home"], rig["gov"]
    _land_event(gov, SUB, "executor", subagent_land_detail(host="advhost", harness="claude-code", mode="subagent", files=["connection.json"]))
    _land_event(gov, AUDITOR, "auditor", f"host={socket.gethostname()} workstation={gov.parent / 'ws-audit'} files=['connection.json']")
    _git(home, "add", "-A")
    _git(home, "commit", "-q", "-m", "fixture: enrollment trail")
    _git(home, "push", "-q", "origin", "main")

    paths_argv = ("project", "set-paths", "--workspace-root", str(gov), "--key", "finalize_mode", "--value", "external")
    exec_argv = ("project", "set-execution", "--workspace-root", str(gov), "--subagent-executor", SUB, "--auditor", AUDITOR)
    for argv in (paths_argv, exec_argv):
        before, status_before, head_before = _tree_digest(home), _git(home, "status", "--porcelain", "-uall"), _git(home, "rev-parse", "HEAD")
        rc, out, err = _cli(*argv, "--dry-run")
        _show(f"[件①·{argv[1]} --dry-run] rc={rc}\n{out}{err}")
        assert rc == 0 and "预览" in out and "落账" not in out + err
        assert (_tree_digest(home), _git(home, "status", "--porcelain", "-uall"), _git(home, "rev-parse", "HEAD")) == (
            before, status_before, head_before), "--dry-run 零写入、不落账"

        rc, out, err = _cli(*argv, "--actor", ADVISOR, "--confirm")
        msg, names, status, pushed = _landed_state(rig)
        _show(f"[件①·{argv[1]} --confirm] rc={rc}\n{out}{err}[件①·git log -1]\n{msg}\n[件①·git show --name-only HEAD] {names}\n"
              f"[件①·git status --porcelain -uall] {status!r}  HEAD==origin/main: {pushed}")
        assert rc == 0 and "已写入" in out and "落账: verdict=PASS committed=True pushed=True" in out, out + err
        assert f"chore(governance): 项目配置落账 {argv[1]} {PROJECT}" in msg and f"Actor: {ADVISOR}" in msg
        assert f"Command: lybra project {argv[1]} --confirm" in msg
        assert names == [f"{PROJECT}/project.json"] and status == "" and pushed

    # 无改动的 --confirm: 同一提交口核一次 = PASS no-op(不另起提交)
    head = _git(home, "rev-parse", "HEAD")
    rc, out, err = _cli(*paths_argv, "--actor", ADVISOR, "--confirm")
    _show(f"[件①·set-paths --confirm 无改动] rc={rc}\n{out}{err}")
    assert rc == 0 and "无改动" in out and "落账: verdict=PASS" in out and _git(home, "rev-parse", "HEAD") == head


def test_item1_landing_failure_keeps_write_gives_remedy_and_remedy_lands(rig):
    """落账被拒(他人已暂存、在本次范围外的文件 = 同一提交口 BLOCK): 写入照常生效, 出拒因 + 补账命令, 以声明退出码退出;
    他人处理完暂存后按补账命令原样重跑即落账并推送。"""
    from tools.aipos_cli.governance_commit import post_write_landing_declaration
    from tools.aipos_cli.workspace_config import project_paths

    home, gov = rig["home"], rig["gov"]
    _write(home / "others-wip.md", "someone else's staged work\n")
    _git(home, "add", "others-wip.md")
    rc, out, err = _cli("project", "set-paths", "--workspace-root", str(gov), "--key", "finalize_mode", "--value", "external",
                        "--actor", ADVISOR, "--confirm")
    _show(f"[件①·落账失败] rc={rc}\nstdout:\n{out}stderr:\n{err}")
    assert rc == post_write_landing_declaration()["landing_failed_exit_code"] == 3
    assert "⚠ 落账未完成(写入已生效, 勿重复执行写命令)" in err and "补账: lybra governance-commit --governance-root" in err
    assert project_paths(gov)["finalize_mode"] == "external", "落账失败不回滚写入"
    assert "A  others-wip.md" in _git(home, "status", "--porcelain"), "他人暂存不动"
    remedy = next(ln.split("补账: ", 1)[1] for ln in err.splitlines() if "补账: " in ln)
    assert remedy.endswith("--paths project.json")
    _git(home, "reset", "-q", "--", "others-wip.md")
    argv = shlex.split(remedy)
    rc2, out2, err2 = _cli(*argv[1:])
    _show(f"[件①·按补账命令重跑] $ {remedy}\nrc={rc2}\n{(out2 + err2)[-800:]}")
    assert rc2 == 0
    assert _git(home, "status", "--porcelain", "-uall") == "?? others-wip.md"
    assert _git(home, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")


def test_item1_json_output_carries_landing_and_not_in_git_is_nonzero(rig, tmp_path):
    from tools.aipos_cli.workspace_config import scaffold_project

    gov = rig["gov"]
    rc, out, err = _cli("project", "set-meta", "--workspace-root", str(gov), "--note", "F159 靶场", "--actor", ADVISOR, "--json", "--confirm")
    payload = json.loads(out)
    _show(f"[件①·set-meta --json --confirm] rc={rc} landing={json.dumps(payload.get('landing'), ensure_ascii=False)[:400]}")
    assert rc == 0 and payload["landing"]["landed"] and payload["landing"]["paths"] == ["project.json"]
    rc, out, err = _cli("project", "set-meta", "--workspace-root", str(gov), "--note", "F159 预演", "--json")
    assert rc == 0 and "landing" not in json.loads(out), "预演不落账"
    # 治理根不在任何 git 仓 = 落账失败: 写入已生效、非零退出并给补账命令(不静默成功)
    bare_root = scaffold_project(tmp_path / "nogit-home", "f159-nogit", registered_by=ADVISOR)
    rc, out, err = _cli("project", "set-meta", "--workspace-root", str(bare_root), "--note", "x", "--actor", ADVISOR, "--confirm")
    _show(f"[件①·治理根不在治理仓] rc={rc}\n{err}")
    assert rc == 3 and "补账: lybra governance-commit" in err and "已写入" in out


def test_item1_freeze_legacy_and_dispatch_mode_set_land_their_writes(rig):
    home, gov = rig["home"], rig["gov"]
    card = gov / "5_tasks" / "queue" / "claimed" / "prb-159-1.md"
    _write(card, "---\ntask_id: PRB-159-1\ntitle: legacy\nproject: f159-probe\nstatus: claimed\ntask_mode: code\n---\n# x\n")
    _git(home, "add", "-A")
    _git(home, "commit", "-q", "-m", "fixture: legacy card")
    _git(home, "push", "-q", "origin", "main")
    rc, out, err = _cli("project", "freeze-legacy", "--workspace-root", str(gov), "--task-ids", "PRB-159-1", "--reason", "人肉期存量",
                        "--actor", ADVISOR, "--confirm")
    msg, names, status, pushed = _landed_state(rig)
    _show(f"[件①·freeze-legacy --confirm] rc={rc}\n{out}{err}[git log -1]\n{msg}\n[names] {names}\n[status] {status!r} pushed={pushed}")
    assert rc == 0 and "落账: verdict=PASS committed=True pushed=True" in out
    assert f"项目配置落账 freeze-legacy {PROJECT}" in msg and f"{PROJECT}/project.json" in names
    assert any(n.startswith(f"{PROJECT}/5_tasks/records/legacy_baseline/legacy_freeze_") for n in names) and status == "" and pushed

    rc, out, err = _cli("project", "dispatch-mode", "set", "--project-root", str(gov), "--mode", "manual", "--by", ADVISOR, "--reason", "F159")
    msg, names, status, pushed = _landed_state(rig)
    _show(f"[件①·dispatch-mode set] rc={rc}\n{out}{err}[git log -1]\n{msg}\n[names] {names}\n[status] {status!r} pushed={pushed}")
    assert rc == 0 and f"项目配置落账 dispatch-mode set {PROJECT}" in msg and f"{PROJECT}/project.json" in names
    assert len(names) == 2 and status == "" and pushed


def test_item1_every_project_json_writer_has_one_landing_point():
    """防碎片化: 写 project.json 的两阶段命令全部经输出包装 _project_json_two_phase_emit(唯一落账点, 必带 actor);
    落账薄壳只调 F158 land_written_paths; 声明 subcommands 覆盖 project_json_writers 全部命令 + freeze-legacy + dispatch-mode set。"""
    import ast

    from tools.schema_loader import load_schema

    src = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    emits = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_project_json_two_phase_emit"]
    assert emits and all(any(k.arg == "actor" for k in n.keywords) for n in emits)
    shell = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_land_project_writes")
    called = {c.func.id for c in ast.walk(shell) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "land_written_paths" in called and "governance_commit" not in called
    verbs = load_schema("verbs", REPO_ROOT)
    writers = verbs["two_phase_protocol"]["project_json_writers"]["commands"]
    landing = verbs["verbs"]["lybra_governance_commit"]["post_write_landing"]["commands"]["project_writers"]
    _show(f"[件①·声明] project_json_writers={writers}; project_writers.subcommands={landing['subcommands']}")
    assert set(writers) | {"freeze-legacy", "dispatch-mode set"} == set(landing["subcommands"])
    assert "{subcommand}" in landing["message_template"] and "{project}" in landing["message_template"]


def test_item1_onboarding_step2_confirm_carries_actor_and_still_parses(tmp_path):
    from tools.aipos_cli.aipos_cli import build_parser
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    guide = generate_onboarding_guide("probe-g", home_root=str(tmp_path / "h"), actor=ADVISOR, repos=[f"app={tmp_path}/app"])
    step2 = next(s for s in guide["steps"] if s["step_number"] == 2)["command"]
    confirm = next(ln for ln in step2.splitlines() if ln.startswith("lybra project set-repos") and ln.endswith(" --confirm"))
    _show(f"[件①·向导 Step 2 确认行] {confirm}")
    args = build_parser().parse_args(shlex.split(confirm)[1:])
    assert args.confirm and args.actor == ADVISOR


# ===========================================================================
# 件② decision_log 自动指针: 同日多签不重名; 旧名兼容读; 缺指针只预演列出
# ===========================================================================

def _mint(rig, *policy_ids: str) -> tuple[int, str, str]:
    argv = ["envelope", "mint", "--workspace-root", str(rig["gov"]), "--connection-json", str(rig["conn"]),
            "--max-tasks", "3", "--task-mode", "code", "--expires-at", "2999-01-01T00:00:00Z",
            "--decision-summary", "F159 靶场签发", "--confirm"]
    for pid in policy_ids:
        argv += ["--policy-id", pid, "--agent-or-role", ADVISOR]
    return _cli(*argv)


def test_item2_same_day_two_envelopes_two_pointers_landed(rig, monkeypatch):
    import tools.aipos_cli.confirm_client as confirm_client
    from tools.schema_loader import resolve_governance_path

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    gov = rig["gov"]
    rc, out, err = _mint(rig, "pol_f159_a", "pol_f159_b")
    msg, names, status, pushed = _landed_state(rig)
    log_dir = resolve_governance_path("decision_log_dir", gov)
    pointers = sorted(p.relative_to(gov).as_posix() for p in log_dir.rglob("*-auto-pointer.md"))
    _show(f"[件②·同日签两张] rc={rc}\n{out}{err}[件②·指针] {pointers}\n[件②·落账] {names} status={status!r} pushed={pushed}")
    assert rc == 0 and len(pointers) == 2
    for pid in ("pol_f159_a", "pol_f159_b"):
        hit = [p for p in pointers if p.endswith(f"-envelope-{pid}-auto-pointer.md")]
        assert len(hit) == 1, (pid, pointers)
        body = (gov / hit[0]).read_text(encoding="utf-8")
        _show(f"[件②·指针原文 {hit[0]}]\n{body}")
        assert f"5_tasks/records/owner_decisions/envelope-{pid}.md" in body
        assert f"{PROJECT}/{hit[0]}" in names
    assert status == "" and pushed


def test_item2_legacy_named_pointer_still_read_and_missing_listed_not_backfilled(rig, monkeypatch):
    import tools.aipos_cli.confirm_client as confirm_client
    from tools.aipos_cli.brief import _get_decision_log_entries
    from tools.aipos_cli.decision_log_pointers import missing_pointer_plan, pointer_index
    from tools.schema_loader import resolve_governance_path

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    home, gov = rig["home"], rig["gov"]
    rc, out, err = _mint(rig, "pol_f159_c", "pol_f159_d")
    assert rc == 0, out + err
    log_dir = resolve_governance_path("decision_log_dir", gov)
    # 人肉期 / F159 前样本: d 的指针是旧名(decision_id 首个 `_` 前段 → envelope-pol), 新名不在; e 的决策记录无任何指针
    new_d = next(log_dir.rglob("*-envelope-pol_f159_d-auto-pointer.md"))
    legacy = new_d.with_name(new_d.name.replace("envelope-pol_f159_d", "envelope-pol"))
    new_d.rename(legacy)
    rec_dir = gov / "5_tasks" / "records" / "owner_decisions"
    _write(rec_dir / "dec-f159-e.md", "---\nrecord_type: owner_decision\ndecision_id: dec-f159-e\n---\n# e\n")
    _git(home, "add", "-A")
    _git(home, "commit", "-q", "-m", "fixture: legacy-named pointer + pointerless decision")
    index = pointer_index(gov)
    _show(f"[件②·读取侧索引] {json.dumps(index['by_record'], ensure_ascii=False)}")
    assert index["by_record"][f"5_tasks/records/owner_decisions/envelope-pol_f159_d.md"] == [legacy.relative_to(gov).as_posix()]
    brief_paths = {Path(e["path"]).name for e in _get_decision_log_entries(gov)}
    _show(f"[件②·brief 读到的 decision_log 条目] {sorted(brief_paths)}")
    assert legacy.name in brief_paths, "旧名指针仍被 brief 读到(读取侧不依赖文件名)"

    before, status_before = _tree_digest(home), _git(home, "status", "--porcelain", "-uall")
    rc, out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov))
    _show(f"[件②·缺指针预演] rc={rc}\n{out}{err}")
    assert rc == 0 and "只列不补" in out and "[缺指针] 5_tasks/records/owner_decisions/dec-f159-e.md" in out
    assert "envelope-pol_f159_c" not in out and "envelope-pol_f159_d" not in out
    assert (_tree_digest(home), _git(home, "status", "--porcelain", "-uall")) == (before, status_before), "预演零写入、不补"
    plan = missing_pointer_plan(gov)
    assert plan["policy"] == "list_only" and [m["decision_id"] for m in plan["missing"]] == ["dec-f159-e"]
    rc, _out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov), "--confirm")
    assert rc == 2, "无 --confirm(只列不补)"


def test_item2_pointer_naming_lives_in_one_function():
    """防碎片化: 指针文件名只在生成实现 board_adapter._auto_generate_decision_log_pointer 拼(唯一键 = 完整 decision_id)。"""
    hits = []
    for base in ("tools/aipos_cli", "tools/mcp_server"):
        for path in sorted((REPO_ROOT / base).glob("*.py")):
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "-auto-pointer.md" in line and not line.lstrip().startswith("#"):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{no}:{line.strip()}")
    _show("[件②·指针文件名拼接点]\n  " + "\n  ".join(hits))
    assert len(hits) == 1 and hits[0].startswith("tools/aipos_cli/board_adapter.py") and 'f"{date_str}-{slug}-' in hits[0], hits
    src = (REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")
    body = re.search(r"def _auto_generate_decision_log_pointer\(.*?(?=\ndef )", src, re.S).group(0)
    assert 'decision_id.split("_")[0]' not in body


# ===========================================================================
# 件③ 剩余按角色取凭据收口 + R2 指南
# ===========================================================================

def _conn(path: Path, tokens: list[dict]) -> Path:
    _write(path, json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:9/mcp"}, "tokens": tokens}))
    return path


def test_item3_connection_resolver_no_fallback_to_other_instance(tmp_path, monkeypatch):
    from tools.aipos_cli.token_resolver import token_fingerprint
    from tools.aipos_cli.two_phase_shell_factory import DriverTokenError
    from tools.loop_context import ConnectionResolver

    monkeypatch.delenv("LYBRA_DRIVER_INSTANCE", raising=False)
    ws = tmp_path / "ws"
    _conn(ws / ".lybra" / "connection.json", [
        {"role": "executor", "agent_instance": "exec.p.one", "token": "tok-one-f159"},
        {"role": "executor", "agent_instance": "exec.p.two", "token": "tok-two-f159"},
    ])
    got = ConnectionResolver.resolve_token(workspace_root=ws, role="executor", agent_instance="exec.p.two", env={})
    _show(f"[件③·ConnectionResolver 按实例] exec.p.two → {token_fingerprint(got)}")
    assert got == "tok-two-f159"
    with pytest.raises(DriverTokenError) as ghost:
        ConnectionResolver.resolve_token(workspace_root=ws, role="executor", agent_instance="exec.p.ghost", env={"LYBRA_TOKEN": "env-x"})
    _show(f"[件③·ConnectionResolver 未接入实例] 拒因原文: {ghost.value}")
    assert "禁回落按角色取首个" in str(ghost.value) and "tok-" not in str(ghost.value)
    with pytest.raises(DriverTokenError) as multi:
        ConnectionResolver.resolve_token(workspace_root=ws, role="executor", env={})
    _show(f"[件③·ConnectionResolver 按角色两实例] 拒因原文: {multi.value}")
    assert "禁取首个冒名" in str(multi.value)
    # 连接文件里根本没有该角色 = 本层无候选, 照旧落 env 兜底(声明优先级不变)
    assert ConnectionResolver.resolve_token(workspace_root=ws, role="auditor", env={"LYBRA_TOKEN": "env-x"}) == "env-x"
    # resolve_identity: 声明实例无本角色条目 = token.error(不取他实例)
    _write(ws / ".lybra" / "role", json.dumps({"role": "executor", "instance": "exec.p.ghost"}))
    ident = ConnectionResolver.resolve_identity(workspace_root=ws, env={"LYBRA_TOKEN": "env-x"})
    _show(f"[件③·resolve_identity 声明实例无条目] value={ident['token']['value']} error={ident['token'].get('error')}")
    assert ident["token"]["value"] is None and "禁回落按角色取首个" in ident["token"]["error"]
    _write(ws / ".lybra" / "role", json.dumps({"role": "executor", "instance": "exec.p.one"}))
    assert ConnectionResolver.resolve_identity(workspace_root=ws, env={})["token"]["value"] == "tok-one-f159"


def test_item3_tui_sessions_and_confined_worker_refuse_multi_instance(tmp_path, monkeypatch):
    from tools.aipos_cli.confirm_client import load_client_token
    from tools.sandbox_runtime import confined_worker as cw

    monkeypatch.delenv("LYBRA_DRIVER_INSTANCE", raising=False)
    two = _conn(tmp_path / "two.json", [
        {"role": "executor", "agent_instance": "exec.p.one", "token": "tok-one-f159"},
        {"role": "executor", "agent_instance": "exec.p.two", "token": "tok-two-f159"},
        {"role": "owner", "token": "tok-owner-f159"},
    ])
    one = _conn(tmp_path / "one.json", [{"role": "executor", "agent_instance": "exec.p.one", "token": "tok-one-f159"}])
    with pytest.raises(cw.ConfinedWorkerError) as ei:
        cw.executor_token(two)
    _show(f"[件③·confined_worker 两执行实例] 拒因原文: {ei.value}")
    assert "禁取首个冒名" in str(ei.value) and "tok-" not in str(ei.value)
    assert cw.executor_token(one) == "tok-one-f159"
    with pytest.raises(ValueError) as tui:
        load_client_token(connection_json=str(two), role="executor")
    _show(f"[件③·TUI 会话取凭据 两实例] 拒因原文: {tui.value}")
    assert "禁取首个冒名" in str(tui.value)
    assert load_client_token(connection_json=str(two), role="owner") == "tok-owner-f159"  # 角色级条目(无实例)= 既有行为
    monkeypatch.setenv("F159_TOKEN_ENV", "env-tok-f159")
    assert load_client_token(role="owner", token_env="F159_TOKEN_ENV") == "env-tok-f159"


def test_item3_ratchet_role_first_selection_gone_from_four_call_sites():
    """棘轮: gap #143 四处(ConnectionResolver / TUI 两会话 / confined_worker)不再自行按角色挑凭据——
    loop_context / lybra_tui / sandbox_runtime 里不得出现 load_owner_token( 调用、带 role= 的 select_token_entry( 或带角色实参的
    get_token_for_role_and_project(; 取凭据经 load_role_token / load_client_token。"""
    files = [REPO_ROOT / "tools/loop_context.py", *sorted((REPO_ROOT / "tools/lybra_tui").glob("*.py")),
             *sorted((REPO_ROOT / "tools/sandbox_runtime").glob("*.py"))]
    bad = re.compile(r"\bload_owner_token\(|\bselect_token_entry\([^)]*\brole=|\bget_token_for_role_and_project\((?![^,]*,\s*None)")
    hits = []
    for path in files:
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if bad.search(line) and not stripped.startswith(("def ", "#", "from ", "import ")) and "`" not in line:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{no}:{stripped}")
    _show("[件③·棘轮] 按角色自挑凭据调用点: " + ("\n  ".join(hits) or "0"))
    assert hits == []
    src = {p: (REPO_ROOT / p).read_text(encoding="utf-8") for p in
           ("tools/lybra_tui/state.py", "tools/lybra_tui/copilot.py", "tools/sandbox_runtime/confined_worker.py", "tools/loop_context.py")}
    assert "load_client_token(" in src["tools/lybra_tui/state.py"] and "load_client_token(" in src["tools/lybra_tui/copilot.py"]
    assert "load_role_token(" in src["tools/sandbox_runtime/confined_worker.py"] and "load_role_token(" in src["tools/loop_context.py"]


def test_item3_r2_enroll_guide_has_no_code_on_command_line():
    pattern = r"--code"
    hits = subprocess.run(["git", "grep", "-n", "-e", pattern, "--", "tools/AIPOS-R2-ENROLL-GUIDE.md"], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    text = (REPO_ROOT / "tools/AIPOS-R2-ENROLL-GUIDE.md").read_text(encoding="utf-8")
    _show(f"[件③·R2 指南] git grep -n -e {pattern!r} → rc={hits.returncode} 命中={hits.stdout.strip() or 0}; "
          f"enroll-deliver 出现 {text.count('enroll-deliver')} 次")
    assert hits.returncode == 1 and hits.stdout == ""  # rc=1 = 无命中(非用法错误)
    assert "--code" not in text and text.count("enroll-deliver") >= 2
