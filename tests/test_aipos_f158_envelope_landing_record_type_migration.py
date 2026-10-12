"""AIPOS-F158(gap #130) — 信封签发记录随门落账 + 旧审计快照补 record_type 迁移命令。

件① `lybra envelope mint --confirm` 签发成功后, 本次门写出的记录(决策记录 / 信封 / decision_log 自动指针, 以门应答
    performed_writes 为准)经 N6 同一提交口 governance_commit.land_written_paths 精确提交并推送(提交信息含 policy_id);
    落账失败不影响信封生效, 给拒因原文 + 补账命令并以声明退出码退出; --dry-run 零写入不变。revoke / renew 核: 无写路径(非预演即门外拒), 无记录可落。
件② `lybra migrate record-type`: 缺省预演列出缺 record_type 而被 B④ 拒入的记录与推导出的类型, --confirm 改写并经同一提交口落账;
    推导不出的列出不改。

靶场 = 临时 home 根(git 仓 + 临时远端裸仓, 治理根嵌在仓内子目录 = chris 形前缀)+ 进程内门替身(门侧真实写入实现
board_adapter.record_owner_decision / execute_dry_run, 只替换 HTTP 传输)。全程不读真实凭据、不碰真实治理根。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源

PROJECT = "f158-probe"  # 靶场项目名(非真实项目)
OWNER = "owner"


def _show(text: str) -> None:
    print(text)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
                          cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _cli(*argv: str) -> tuple[int, str]:
    from tools.aipos_cli.aipos_cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue()


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
                "LYBRA_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out = _cli("project", "new", PROJECT, "--home-root", str(hroot), "--actor", OWNER)
    assert rc == 0, out
    remote = init_governance_repo(hroot)
    conn = tmp_path / "owner-vault" / "connection.json"  # 靶场 Owner 凭据(假 token, 仓外; 只出指纹)
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                             "tokens": [{"role": "owner", "token": "fixture-owner-token-f158", "scopes": ["owner_confirm"]}]}))
    return {"home": hroot, "gov": hroot / PROJECT, "remote": remote, "conn": conn, "tmp": tmp_path}


class InProcessGate:
    """门替身: 只替换 HTTP 传输, 预演 / 确认走门侧真实实现(board_adapter.record_owner_decision / execute_dry_run)。"""

    calls: list[str] = []

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url

    def initialize(self) -> None:
        return None

    def call_tool(self, name: str, arguments: dict) -> dict:
        from tools.aipos_cli.board_adapter import execute_dry_run, record_owner_decision

        InProcessGate.calls.append(name)
        args = dict(arguments)
        root = Path(args.pop("workspace_root"))
        if name == "lybra_owner_decision_record_dry_run":
            return record_owner_decision(args, dry_run=True, repo_root=root, actor=str(args.get("actor") or OWNER))
        if name == "lybra_owner_decision_record_confirm":
            return execute_dry_run(args["dry_run_token"], str(args.get("actor") or OWNER),
                                   owner_confirmation_token=args.get("owner_confirmation_token"), repo_root=root)
        raise AssertionError(f"unexpected gate tool {name}")


def _mint(rig, *policy_ids: str, mode: str = "--confirm") -> tuple[int, str]:
    argv = ["envelope", "mint", "--workspace-root", str(rig["gov"]), "--connection-json", str(rig["conn"]),
            "--max-tasks", "3", "--task-mode", "code", "--expires-at", "2999-01-01T00:00:00Z",
            "--decision-summary", "F158 靶场签发", mode]
    for pid in policy_ids:
        argv += ["--policy-id", pid, "--agent-or-role", f"advisor.{PROJECT}.h1"]
    return _cli(*argv)


# ===========================================================================
# 件① 签发即落账
# ===========================================================================

def test_item1_mint_confirm_lands_records_and_pointer(rig, monkeypatch: pytest.MonkeyPatch):
    import tools.aipos_cli.confirm_client as confirm_client

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    home, gov = rig["home"], rig["gov"]
    head_before = _git(home, "rev-parse", "HEAD")
    rc, out = _mint(rig, "pol_f158_a", "pol_f158_b")
    _show(f"[件①·mint --confirm] rc={rc}\n{out}")
    assert rc == 0, out
    assert "落账: verdict=PASS committed=True pushed=True" in out
    msg = _git(home, "log", "-1", "--format=%B")
    names = _git(home, "show", "--name-only", "--format=", "HEAD").splitlines()
    status = _git(home, "status", "--porcelain", "-uall")
    _show(f"[件①·git log -1]\n{msg}\n[件①·git show --name-only HEAD]\n" + "\n".join(names) + f"\n[件①·git status --porcelain -uall] {status!r}")
    assert "信封签发落账 pol_f158_a pol_f158_b" in msg and f"Actor: {OWNER}" in msg
    prefix = f"{PROJECT}/"
    for pid in ("pol_f158_a", "pol_f158_b"):
        assert f"{prefix}5_tasks/records/owner_decisions/envelope-{pid}.md" in names
        assert f"{prefix}5_tasks/policies/{pid}.md" in names
    pointers = [n for n in names if n.startswith(f"{prefix}governance/decision_log/") and n.endswith("-auto-pointer.md")]
    assert pointers, "decision_log 自动指针随同一提交落账"
    assert status == "", "签发产生的记录与指针全部入账, 工作树干净"
    assert _git(home, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main") != head_before, "推送策略同 N6: 已推送"


def test_item1_mint_dry_run_zero_write(rig):
    home = rig["home"]
    before, status_before = _tree_digest(home), _git(home, "status", "--porcelain", "-uall")
    rc, out = _mint(rig, "pol_f158_dry", mode="--dry-run")
    _show(f"[件①·mint --dry-run] rc={rc}\n{out[:1500]}")
    assert rc == 0 and '"dry_run": true' in out
    assert _tree_digest(home) == before and _git(home, "status", "--porcelain", "-uall") == status_before, "--dry-run 零写入"


def test_item1_landing_failure_keeps_envelope_and_prints_remedy(rig, monkeypatch: pytest.MonkeyPatch):
    """落账被拒(治理仓有他人已暂存、在本次范围外的文件 = 同一提交口 BLOCK, 不动他人暂存): 信封照常生效, 出拒因 + 补账命令,
    以声明退出码退出; 他人处理完暂存后按补账命令原样重跑即落账并推送。"""
    import shlex

    import tools.aipos_cli.confirm_client as confirm_client
    from tools.aipos_cli.governance_commit import post_write_landing_declaration

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    home, gov = rig["home"], rig["gov"]
    _write(home / "others-wip.md", "someone else's staged work\n")
    _git(home, "add", "others-wip.md")
    rc, out = _mint(rig, "pol_f158_fail")
    _show(f"[件①·落账失败] rc={rc}\n{out}")
    assert rc == post_write_landing_declaration()["landing_failed_exit_code"] == 3
    assert "⚠ 落账未完成(写入已生效, 勿重复执行写命令)" in out and "补账: lybra governance-commit --governance-root" in out
    assert (gov / "5_tasks" / "policies" / "pol_f158_fail.md").is_file(), "落账失败不影响信封生效"
    assert "A  others-wip.md" in _git(home, "status", "--porcelain"), "他人暂存不动"
    remedy = next(ln.split("补账: ", 1)[1] for ln in out.splitlines() if "补账: " in ln)
    _git(home, "reset", "-q", "--", "others-wip.md")
    argv = shlex.split(remedy)
    assert argv[:2] == ["lybra", "governance-commit"]
    rc2, out2 = _cli(*argv[1:])
    _show(f"[件①·按补账命令重跑] $ {remedy}\nrc={rc2}\n{out2[-1500:]}")
    assert rc2 == 0
    assert _git(home, "status", "--porcelain", "-uall") == "?? others-wip.md"
    assert _git(home, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")


def test_item1_push_failure_reported_not_silent(rig, monkeypatch: pytest.MonkeyPatch):
    """推送失败(远端不可达)= 已提交未推送: 同样以声明退出码出声, 拒因原文含推送原因(推送策略同 N6, 不静默)。"""
    import tools.aipos_cli.confirm_client as confirm_client

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    home = rig["home"]
    _git(home, "remote", "set-url", "origin", str(rig["tmp"] / "no-such-remote.git"))
    rc, out = _mint(rig, "pol_f158_push")
    _show(f"[件①·推送失败] rc={rc}\n{out}")
    assert rc == 3 and "committed=True pushed=False" in out and "补账:" in out


def test_item1_revoke_and_renew_have_no_write_path(rig):
    """同理核 revoke / renew: 非预演即门外拒(record_owner_decision dry_run=False = _blocked_execute), 零写入 → 无记录可落。"""
    home, gov = rig["home"], rig["gov"]
    before = _tree_digest(home)
    rc1, out1 = _cli("--workspace-root", str(gov), "envelope", "revoke", "--policy-id", "pol_x", "--revocation-reason", "r")
    rc2, out2 = _cli("--workspace-root", str(gov), "envelope", "renew", "--policy-id", "pol_x", "--add-tasks", "2", "--decision-summary", "s")
    _show(f"[件①·revoke 非预演] rc={rc1}\n{out1[:600]}\n[件①·renew 非预演] rc={rc2}\n{out2[:600]}")
    assert rc1 == 1 and rc2 == 1 and _tree_digest(home) == before


def test_item1_gate_reports_pointer_in_performed_writes(rig):
    from tools.aipos_cli.board_adapter import execute_dry_run, record_owner_decision
    from tools.schema_loader import resolve_governance_path

    gov = rig["gov"]
    dry = record_owner_decision({"decision_id": "envelope-pol_f158_ptr", "actor": OWNER, "decided_by_ref": OWNER,
                                 "decision_summary": "ptr", "autonomy_policy": {
                                     "policy_id": "pol_f158_ptr", "agent_or_role": "advisor", "active_from": "2026-01-01T00:00:00Z",
                                     "expires_at": "2999-01-01T00:00:00Z", "max_tasks": 1, "task_selector": {"task_mode": "code"},
                                     "launch_harnesses": []}},
                                dry_run=True, repo_root=gov, actor=OWNER)
    assert dry.get("dry_run_token"), dry
    done = execute_dry_run(dry["dry_run_token"], OWNER, owner_confirmation_token="OWNER_CONFIRMED", repo_root=gov)
    writes = [w["path"] for w in done["performed_writes"]]
    _show(f"[件①·门应答 performed_writes] {writes}")
    log_dir = resolve_governance_path("decision_log_dir", gov).relative_to(gov).as_posix()
    assert [w for w in writes if w.startswith(log_dir + "/") and w.endswith("-auto-pointer.md")], "指针在门应答内(落账范围以门为准)"
    assert all((gov / w).is_file() for w in writes)


# ===========================================================================
# 件② 旧快照迁移
# ===========================================================================

REPORT = "---\nverdict: PASS\ncommit_sha: \"" + "a" * 40 + "\"\ntask_id: PRB-1R\n---\n# 审计报告 — PRB-1R\n\n## 一句话结论\nPASS\n"


def _seed_legacy_snapshots(rig) -> dict[str, str]:
    """人肉期样本: 裁决记录已入库(引用快照 + sha), 快照是报告原文直拷(无记录头)滞留未跟踪; 另造推导不出 / 不可复原样本。"""
    home, gov = rig["home"], rig["gov"]
    rec = gov / "5_tasks" / "records"
    vid = "verdict_PRB-1_20261003_093650_audit-probe"
    snap_rel = f"5_tasks/records/audit_verdicts/PRB-1/report_snapshots/{vid}.md"
    sha = hashlib.sha256(REPORT.encode("utf-8")).hexdigest()
    _write(rec / "audit_verdicts" / "PRB-1" / f"{vid}.md", _fm({
        "record_type": "audit_verdict_record", "verdict_id": vid, "reviewed_task_id": "PRB-1", "audit_task_id": "PRB-1R",
        "verdict": "PASS", "report_snapshot_ref": snap_rel, "report_snapshot_sha256": sha,
        "report_source_ref": "task_cards/PRB-1R/RETURN.md"}, "# verdict\n"))
    vid2 = "verdict_PRB-2_20261003_064559_audit-probe"
    snap2_rel = f"5_tasks/records/audit_verdicts/PRB-2/report_snapshots/{vid2}.md"
    _write(rec / "audit_verdicts" / "PRB-2" / f"{vid2}.md", _fm({
        "record_type": "audit_verdict_record", "verdict_id": vid2, "reviewed_task_id": "PRB-2", "audit_task_id": "PRB-2R",
        "verdict": "PASS", "report_snapshot_ref": snap2_rel, "report_snapshot_sha256": "0" * 64,
        "report_source_ref": "task_cards/PRB-2R/RETURN.md"}, "# verdict\n"))
    _git(home, "add", "-A")
    _git(home, "commit", "-q", "-m", "seed verdict records")
    _git(home, "push", "-q", "origin", "main")
    _write(gov / snap_rel, REPORT)                                             # 可推导: sha 与裁决记录一致
    _write(gov / snap2_rel, REPORT)                                            # 推导不出: sha 对不上
    _write(rec / "claims" / "PRB-3" / "claim_PRB-3_20261003T000000Z_x.md", "---\nclaim_id: c\n---\n")  # 推导出类型, 不可复原
    _write(rec / "misc" / "note.md", "---\nfoo: bar\n---\n")                  # 无落点命中
    return {"snap": snap_rel, "snap2": snap2_rel, "claim": "5_tasks/records/claims/PRB-3/claim_PRB-3_20261003T000000Z_x.md",
            "misc": "5_tasks/records/misc/note.md"}


def test_item2_migrate_preview_confirm_then_guard_passes(rig):
    from tools.aipos_cli.board_adapter import unwrap_report_snapshot

    home, gov = rig["home"], rig["gov"]
    paths = _seed_legacy_snapshots(rig)
    # 迁移前: 同一提交口对快照的预演被 B④ 拒(滞留病根)
    rc0, out0 = _cli("governance-commit", "--governance-root", str(gov), "--actor", OWNER, "--paths", paths["snap"], "--dry-run")
    _show(f"[件②·迁移前 governance-commit --dry-run] rc={rc0}\n{out0[-900:]}")
    assert rc0 == 1 and "B④" in out0 and "record_type" in out0

    before, status_before = _tree_digest(home), _git(home, "status", "--porcelain", "-uall")
    rc1, out1 = _cli("migrate", "record-type", "--workspace-root", str(gov))
    _show(f"[件②·预演] rc={rc1}\n{out1}")
    assert rc1 == 0 and _tree_digest(home) == before and _git(home, "status", "--porcelain", "-uall") == status_before, "预演零写入"
    for key in ("snap", "snap2", "claim", "misc"):
        assert paths[key] in out1
    assert "audit_report_snapshot" in out1

    rc_bad, out_bad = _cli("migrate", "record-type", "--workspace-root", str(gov), "--confirm")
    assert rc_bad == 2 and "--actor" in out_bad

    rc2, out2 = _cli("migrate", "record-type", "--workspace-root", str(gov), "--actor", OWNER, "--confirm")
    _show(f"[件②·确认] rc={rc2}\n{out2}")
    assert rc2 == 0 and "落账: verdict=PASS committed=True pushed=True" in out2
    text = (gov / paths["snap"]).read_text(encoding="utf-8")
    assert text.startswith("---\nrecord_type: audit_report_snapshot\n") and unwrap_report_snapshot(text) == REPORT, "记录头 + 原文逐字节"
    msg = _git(home, "log", "-1", "--format=%B")
    names = _git(home, "show", "--name-only", "--format=", "HEAD").splitlines()
    _show(f"[件②·git log -1]\n{msg}\n[件②·git show --name-only HEAD] {names}")
    assert "记录类型补齐迁移 1 份" in msg and names == [f"{PROJECT}/{paths['snap']}"]
    status = _git(home, "status", "--porcelain", "-uall")
    _show(f"[件②·确认后 git status] {status!r}")
    assert f"{PROJECT}/{paths['snap']}" not in status
    for key in ("snap2", "claim", "misc"):  # 推导不出 / 不可复原的列出不改
        assert f"?? {PROJECT}/{paths[key]}" in status
    assert (gov / paths["snap2"]).read_text(encoding="utf-8") == REPORT

    # B④ 守卫随后放行: 同一提交口的护栏行(与 pre-commit hook 同一模块)
    assert "Guardrails (governance_guardrails, same module as pre-commit hook): PASS" in out2
    rc3, out3 = _cli("migrate", "record-type", "--workspace-root", str(gov), "--actor", OWNER, "--confirm")
    _show(f"[件②·重跑(幂等)] rc={rc3}\n{out3}")
    assert rc3 == 0 and paths["snap"] not in out3 and "无可补齐(未写)" in out3


def test_item2_non_git_root_refused_with_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    hroot = tmp_path / "plain-home"
    assert _cli("project", "new", PROJECT, "--home-root", str(hroot), "--actor", OWNER)[0] == 0
    rc, out = _cli("migrate", "record-type", "--workspace-root", str(hroot / PROJECT))
    _show(f"[件②·非 git 治理根] rc={rc}\n{out}")
    assert rc == 1 and "lybra home git-init --home-root" in out


# ===========================================================================
# 防碎片化: 两件同一提交口 / 快照记录头同一构造 / 声明齐
# ===========================================================================

def test_single_implementations_and_declarations():
    import ast

    from tools.aipos_cli.aipos_cli import _two_phase_family_declaration
    from tools.aipos_cli.governance_commit import post_write_landing_declaration

    decl = post_write_landing_declaration()
    assert set(decl["commands"]) >= {"envelope_mint", "migrate_record_type"}
    # AIPOS-F160 件③: migrate 族第二项 decision-log-pointers 同族同口径(清单只追加)
    assert _two_phase_family_declaration("records_migration_writers")["commands"][:2] == ["record-type", "decision-log-pointers"]
    src = {p: (REPO_ROOT / p).read_text(encoding="utf-8") for p in (
        "tools/aipos_cli/aipos_cli.py", "tools/aipos_cli/record_type_migration.py", "tools/aipos_cli/governance_commit.py",
        "tools/aipos_cli/board_adapter.py")}
    # 落账只经 land_written_paths → governance_commit; 迁移模块不自起 git 写
    assert "land_written_paths(" in src["tools/aipos_cli/aipos_cli.py"] and "land_written_paths(" in src["tools/aipos_cli/record_type_migration.py"]
    assert '"commit"' not in src["tools/aipos_cli/record_type_migration.py"] and '"add"' not in src["tools/aipos_cli/record_type_migration.py"]
    tree = ast.parse(src["tools/aipos_cli/governance_commit.py"])
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "land_written_paths")
    assert "governance_commit" in {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    # 快照记录头: 门写快照与迁移同一构造
    bt = ast.parse(src["tools/aipos_cli/board_adapter.py"])
    plan = next(n for n in bt.body if isinstance(n, ast.FunctionDef) and n.name == "_audit_report_snapshot_plan")
    assert "render_report_snapshot" in {c.func.id for c in ast.walk(plan) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "render_report_snapshot" in src["tools/aipos_cli/record_type_migration.py"]
