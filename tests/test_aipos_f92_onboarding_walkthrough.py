"""AIPOS-F92 — 接入向导可走通三件(开接入验收前置)。

件① 信封签发产品化: `lybra envelope mint --confirm` 经门 owner_decision_record envelope 路径真实落盘(--policy-id/--agent-or-role
    成对可重复, 一条签一组); 预演(--dry-run)与执行同一 payload 构造; Owner 凭据跨项目(roles.schema owner project_scope=cross_project,
    门 home 注册表不按来源项目收窄, 来源项目只作路由缺省 default_project)。
件② 新项目首凭据与门可达(Owner 2026-10-03 方向更正: 单门 home 根约定, 不登记 home 根外治理根): guide 九步, Owner 一次性动作
    = 第 3 步(签发顾问注册码)+ 第 4 步(一条签三张信封); 顾问 enroll 到治理根 `--harness claude-code --harness-dir <会话目录>`,
    顾问技能经 distribution 声明(advisor-skills, harness=claude-code)由同一分发引擎交付到 <会话目录>/.claude/skills/;
    `lybra project set-repos` 声明产品仓(project.json repos/code_repo, 经声明校验)。
件③ 新项目首次 finalize 不被拦: `lybra project new` 经 governance add stage 写首份阶段快照「项目创建」(阶段门判据不变);
    finalize 阶段门 / 分支整合声明读 Lybra 自身 schema(不读项目产品仓); 产品仓内工作树根登记进 .git/info/exclude;
    无部署机制的产品仓部署不适用(deploy_status=skipped)。另: state lint 审计卡终态 = 门生裁决(不报「无 closure」断层)。

test_walkthrough_* = 隔离靶场(临时 HOME / 临时 home 根 / 两个临时产品仓 / 自起临时门 `lybra serve`)按 guide 原样走到一张卡结案。
token 永不上屏(只断言指纹/记录); 注册码只在进程内传递。
"""
from __future__ import annotations

import io
import contextlib
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源

CODE_RE = re.compile(r"LYBRAENROLL1\.[A-Za-z0-9_-]+")


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
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


@pytest.fixture
def iso_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    return home


# ===========================================================================
# 件③ 首份阶段快照 / finalize 不被拦
# ===========================================================================

def test_item3_project_new_writes_first_stage_snapshot_gate_unchanged(iso_home: Path):
    from tools.aipos_cli.finalize import check_stage_archive_gate

    home_root = iso_home / "projects-home"
    rc, out = _cli("project", "new", "probe-x", "--home-root", str(home_root), "--actor", "advisor.probe-x.h")
    print(out)
    assert rc == 0 and "local scaffold" in out and "来源: 显式 --home-root" in out
    gov = home_root / "probe-x"
    snaps = [p for p in (gov / "stage_archive").glob("*.md") if p.name.lower() != "readme.md"]
    assert len(snaps) == 1 and "stage snapshot:" in out and snaps[0].name.endswith("_项目创建.md")
    text = snaps[0].read_text(encoding="utf-8")
    assert "stage_name: 项目创建" in text and "status: archived" in text
    gate = check_stage_archive_gate(gov)
    print("[③·阶段门]", gate["message"])
    assert gate["passed"] is True and gate["snapshot_count"] == 1
    snaps[0].unlink()  # 判据不变: 无快照仍 BLOCK
    assert check_stage_archive_gate(gov)["passed"] is False


def test_item3_finalize_reads_lybra_schema_not_product_repo():
    """阶段门与分支整合声明的 schema 根 = Lybra 自身(repo_root=None), 不是项目产品仓(新项目产品仓无 schema/)。"""
    src = (REPO_ROOT / "tools" / "aipos_cli" / "finalize.py").read_text(encoding="utf-8")
    assert "check_stage_archive_gate(governance_root, repo_root=workspace_root)" not in src
    assert "_load_branch_integration(workspace_root)" not in src
    assert "stage_gate = check_stage_archive_gate(governance_root)" in src


def test_item3_worktree_root_excluded_and_deploy_applicability(tmp_path: Path):
    from tools.aipos_cli.deploy_gate import deploy_mechanism_present
    from tools.aipos_cli.next_resolver import _exclude_worktree_root

    repo = tmp_path / "product"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "a.txt", "a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    wt = repo / ".worktrees" / "PROBE-1"
    assert _exclude_worktree_root(repo, wt) is None
    assert _exclude_worktree_root(repo, wt) is None  # 幂等
    _write(wt / "x.txt", "x\n")
    status = _git(repo, "status", "--porcelain")
    print("[③·工作树根登记后 git status]", repr(status))
    assert status == ""
    assert _exclude_worktree_root(repo, tmp_path / "elsewhere" / "PROBE-1") is None  # 仓外 = 无需登记
    assert "/.worktrees/" in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert deploy_mechanism_present(repo) is False
    (repo / ".deploy").mkdir()
    assert deploy_mechanism_present(repo) is True


def test_state_lint_audit_card_terminal_is_gate_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from test_aipos_f78_engine_agnostic import _make_gov
    from tools.aipos_cli.state_lint import run_state_lint

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _write(gov / "5_tasks" / "queue" / "completed" / "probe-1r.md", _fm({
        "task_id": "PROBE-1R", "task_mode": "audit", "reviewed_task_id": "PROBE-1", "status": "completed"}))
    issues = run_state_lint(gov)["issues"]
    assert any(i["task_id"] == "PROBE-1R" and i["severity"] == "ERROR" for i in issues), issues  # 无裁决 = 仍是断层
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / "PROBE-1" / "verdict_PROBE-1_x.md", _fm({
        "record_type": "audit_verdict_record", "verdict": "PASS", "reviewed_task_id": "PROBE-1", "audit_task_id": "PROBE-1R"}))
    issues = run_state_lint(gov)["issues"]
    print("[审计卡终态 lint]", issues)
    assert not any(i["task_id"] == "PROBE-1R" and i["severity"] == "ERROR" for i in issues)


# ===========================================================================
# 件① 信封 CLI
# ===========================================================================

def test_item1_envelope_mint_mode_required_and_pairs_checked(iso_home: Path):
    from tools.aipos_cli.aipos_cli import build_parser

    base = ["envelope", "mint", "--policy-id", "p1", "--agent-or-role", "a", "--max-tasks", "3",
            "--expires-at", "2999-01-01T00:00:00Z", "--decision-summary", "s"]
    with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit):
        build_parser().parse_args(base)  # 必须二选一: --dry-run(预演)/ --confirm(经门落盘), 不再有「静默只预览」
    args = build_parser().parse_args(base + ["--policy-id", "p2", "--agent-or-role", "b", "--confirm"])
    assert args.policy_id == ["p1", "p2"] and args.agent_or_role == ["a", "b"] and args.confirm is True
    rc, out = _cli(*base[:4], "--policy-id", "p2", *base[4:], "--confirm")
    assert rc == 2 and "须按顺序成对" in out


def test_item1_owner_cross_project_in_home_registry(tmp_path: Path):
    """门 home 联合注册表: owner(project_scope=cross_project)缺 projects 不收窄, 来源项目只作 default_project; 其余角色仍收窄。"""
    from tools.mcp_server.http_sse import load_unified_service_role_registry

    home = tmp_path / "home"
    for name in ("ops", "probe"):
        (home / name / "5_tasks" / "queue").mkdir(parents=True)
        _write(home / name / "project.json", json.dumps({"project": name}))
    _write(home / "ops" / ".lybra" / "connection.json", json.dumps({"tokens": [
        {"role": "owner", "token": "fixture-owner-secret", "scopes": ["owner_confirm"], "fingerprint": "fp-o", "token_ref": "svc-owner"},
        {"role": "advisor", "token": "fixture-advisor-secret", "scopes": ["draft_submit"], "fingerprint": "fp-a", "token_ref": "svc-advisor"},
    ]}))
    reg = load_unified_service_role_registry(home, error_stream=io.StringIO())
    by_role = {e["role"]: e for e in reg.values()}
    print("[①·注册表]", {r: {k: v for k, v in e.items() if k in ("projects", "default_project")} for r, e in by_role.items()})
    assert "projects" not in by_role["owner"] and by_role["owner"]["default_project"] == "ops"
    assert by_role["advisor"]["projects"] == ["ops"] and "default_project" not in by_role["advisor"]


# ===========================================================================
# 件② 产品仓声明 / 凭据幂等
# ===========================================================================

def test_item2_set_repos_validates_and_restores(iso_home: Path, tmp_path: Path):
    home_root = iso_home / "h"
    assert _cli("project", "new", "probe-x", "--home-root", str(home_root), "--actor", "a.b.c")[0] == 0
    before = (home_root / "probe-x" / "project.json").read_text(encoding="utf-8")
    rc, out = _cli("project", "set-repos", "probe-x", "--home-root", str(home_root), "--repo", "app=relative/path")
    assert rc == 1 and "REPOS_CONFLICT" in out
    assert (home_root / "probe-x" / "project.json").read_text(encoding="utf-8") == before  # 不留半成品
    rc, out = _cli("project", "set-repos", "probe-x", "--home-root", str(home_root), "--repo", f"app={tmp_path}/app",
                   "--repo", f"lib={tmp_path}/lib")
    assert rc == 2 and "--default" in out
    rc, out = _cli("project", "set-repos", "probe-x", "--home-root", str(home_root), "--repo", f"app={tmp_path}/app",
                   "--repo", f"lib={tmp_path}/lib", "--default", "lib", "--confirm")  # AIPOS-F125: 缺省预演, --confirm 才写
    print(out)
    data = json.loads((home_root / "probe-x" / "project.json").read_text(encoding="utf-8"))
    assert rc == 0 and data["repos"] == {"default": "lib", "items": {"app": f"{tmp_path}/app", "lib": f"{tmp_path}/lib"}}
    assert data["code_repo"] == f"{tmp_path}/lib" and data["project"] == "probe-x" and data["registered_by"] == "a.b.c"


def test_item2_upsert_same_token_is_idempotent():
    from tools.aipos_cli.enroll_client import upsert_token_entry

    entry = {"role": "advisor", "agent_instance": "advisor.p.h", "token": "fixture-same", "fingerprint": "sha256:x"}
    conn = {"tokens": [dict(entry)]}
    assert upsert_token_entry(conn, dict(entry)) is False
    assert len(conn["tokens"]) == 1 and not conn["tokens"][0].get("retired")
    assert upsert_token_entry(conn, {**entry, "token": "fixture-new", "fingerprint": "sha256:y"}) is True  # 换凭据仍退场旧条目
    assert [bool(t.get("retired")) for t in conn["tokens"]] == [True, False]


def test_item2_advisor_skills_declared_for_claude_code_only():
    from tools.aipos_cli.distribution_sync import declared_harness_kinds, harness_distributions
    from tools.aipos_cli.workstation_wiring import declared_role_distributions, declared_role_skills

    dists = declared_role_distributions("advisor", "advisor")
    cc = harness_distributions(dists, "claude-code")
    # AIPOS-F136 件②: claude-code 顾问另收顾问章程(.claude/rules/), codex 顾问收章程 AGENTS.md(同一母本同一渲染器)
    assert [d["distribution_id"] for d in cc] == ["advisor-skills", "advisor-charter-claude-code"] and cc[0]["target_path"] == ".claude/skills"
    assert declared_role_skills(dists) == {}  # 顾问技能不再走 pi 挂载
    assert set(declared_harness_kinds()) == {"pi", "claude-code", "codex"}  # AIPOS-F129: 合法值 = harness_semantics.kinds 的键
    assert [(d["distribution_id"], d["kind"]) for d in harness_distributions(dists, "codex")] == [("advisor-charter-codex", "charter")]
    assert "go.ts" not in json.dumps(cc)


# ===========================================================================
# 隔离靶场: guide 原样走到一张卡结案(真门进程 lybra serve, 临时端口)
# ===========================================================================

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Range:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.hroot = self.home / "lybra-home"
        self.session = self.home / "lybra-probe"
        self.bin = tmp / "bin"
        self.log: list[str] = []
        self.codes: dict[str, str] = {}
        self.home.mkdir()
        self.bin.mkdir()
        _write(self.bin / "lybra", f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(REPO_ROOT)!r})\n"
                                   "from tools.aipos_cli.aipos_cli import main\nsys.exit(main())\n")
        (self.bin / "lybra").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT", "LYBRA_HARNESS_ROOT")}
        self.env.update(HOME=str(self.home), LYBRA_HOME_ROOT=str(self.hroot), LYBRA_ACTIVE_PROJECT="ops",
                        PYTHONPATH=str(REPO_ROOT), PATH=f"{self.bin}:/usr/local/bin:/usr/bin:/bin")

    def sh(self, cmd: str, *, cwd: Path | None = None, ok: tuple[int, ...] = (0,)) -> str:
        p = subprocess.run(["bash", "-c", cmd], cwd=str(cwd or self.session), env=self.env, capture_output=True, text=True, timeout=300)
        out = (p.stdout + p.stderr).rstrip()
        trace_free = "\n".join(l for l in out.splitlines() if not l.startswith("[ENVELOPE_TRACE]"))  # 门侧信封判定逐谓词留痕, 不入原文
        shown = CODE_RE.sub("LYBRAENROLL1.<redacted>", f"$ {cmd}\n{trace_free}\n[exit {p.returncode}]")
        self.log.append(shown)
        assert p.returncode in ok, shown[-3000:]
        return out


@pytest.fixture
def probe_range(tmp_path: Path):
    r = Range(tmp_path)
    r.session.mkdir()
    _git(r.session, "init", "-q", "-b", "main")
    for name in ("app", "lib"):
        repo = r.session / name
        (repo / "src" / name).mkdir(parents=True)
        _write(repo / "src" / name / "__init__.py", f'def hello():\n    return "{name}"\n')
        _write(repo / "tests" / f"test_{name}.py",
               "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
               f"import {name}\n\ndef test_hello():\n    assert {name}.hello() == '{name}'\n")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"init {name}")
    # AIPOS-F94: home 根 = 治理仓(git, 带临时远端裸仓 origin)——向导第 1 / 2 步之后与首卡结案后的落账都提交并推送到这里
    init_governance_repo(r.hroot)
    r.sh(f"lybra project new ops --home-root {r.hroot} --actor owner")
    r.mcp_port, r.board_port = _free_port(), _free_port()
    gate = subprocess.Popen(["lybra", "serve", "--workspace-root", str(r.hroot / "ops"), "start", "--mcp-port", str(r.mcp_port),
                             "--board-port", str(r.board_port)], cwd=str(tmp_path), env=r.env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r.sh(f"lybra agent watch --workspace-root {r.hroot / 'ops'} --expect .lybra/service_state.json --timeout 30 --interval 0.5")
        deadline = time.time() + 15  # MCP 端口可连(子进程 bind 晚于 service_state 落盘)
        while True:
            try:
                socket.create_connection(("127.0.0.1", r.mcp_port), timeout=0.5).close()
                break
            except OSError:
                assert time.time() < deadline, "临时门 MCP 端口 15s 内不可连"
                time.sleep(0.2)
        yield r
    finally:
        os.killpg(gate.pid, signal.SIGTERM)
        gate.wait(timeout=20)


def test_walkthrough_guide_onboards_new_project_to_first_card_completed(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    exec_ws, audit_ws = r.home / "kiwiai-pi" / "probe-executor", r.home / "kiwiai-pi" / "probe-auditor"
    guide_args = (f"lybra-probe --repo app={r.session}/app --repo lib={r.session}/lib --default-repo app --advisor-dir {r.session} "
                  f"--workspace-dir {exec_ws} --auditor-dir {audit_ws} --envelope-days 7 --max-tasks 20")
    text = r.sh(f"lybra onboarding guide {guide_args}")
    assert "Owner 一次性动作 2 条: Step 3, Step 4" in text and f"http://127.0.0.1:{r.mcp_port}" in text
    guide = json.loads(r.sh(f"lybra onboarding guide {guide_args} --json"))
    assert guide["home_root"] == str(r.hroot) and guide["home_root_source"] == "环境变量 LYBRA_HOME_ROOT"
    assert guide["owner_actions"] == [3, 4]

    fills: dict[str, str] = {}
    for step in guide["steps"]:
        if step["step_number"] == 9:
            break
        texts = step["commands"] if step.get("commands") else [step["command"]]
        for line in (l.strip() for t in texts for l in t.splitlines()):
            if not line or line.startswith("#") or line in ("pi", "/go") or line.startswith("cd "):
                continue  # 靶场无 pi: 工位会话由本夹具扮演
            for k, v in fills.items():
                line = line.replace(k, v)
            out = r.sh(line)
            if "enroll-code" in line:
                role = re.search(r"--role (\w+)", line).group(1)
                fills[f"<{role.upper()}_CODE>"] = CODE_RE.findall(out)[0]

    # 件①: 三张信封门生落盘 + Owner 指纹(无 token 原文)
    for pid in guide["policies"].values():
        assert (gov / "5_tasks" / "policies" / f"{pid}.md").is_file()
        assert (gov / "5_tasks" / "records" / "owner_decisions" / f"envelope-{pid}.md").is_file()
    # 件②: 顾问凭据在治理根, 顾问技能在会话目录 .claude/skills
    role = json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))
    assert role["harness"] == {"kind": "claude-code", "dir": str(r.session)} and role["owner_policy_ref"] == guide["policies"]["driver"]
    skills = sorted(p.name for p in (r.session / ".claude" / "skills").iterdir())
    print("[②·会话目录技能]", skills)
    assert {"advisor-commands", "lybra-onboarding", "truth-navigator"} <= set(skills)
    assert (exec_ws / ".pi" / "extensions" / "go.ts").is_symlink()  # 首个工位 go.ts 由 sync 补挂

    # Step 9: 发卡 → loop(工位由本夹具扮演: my-tasks 选卡 → 写产物)
    _write(r.session / "cards" / "probe-1.json", json.dumps({"frontmatter": {
        "task_id": "PROBE-1", "title": "PROBE-1 greet", "project": "lybra-probe",
        "assigned_to": guide["instances"]["executor"], "agent_instance": guide["instances"]["executor"],
        "context_bundle": guide["instances"]["executor"], "task_mode": "code", "task_class": "simple", "priority": "medium",
        "status": "pending", "created_by": guide["instances"]["advisor"], "needs_owner": False, "output_target": "src/, tests/",
        "artifact_policy": "formal_write", "claim_policy": "assigned_agent_only", "audit": "required",
        "audit_by": guide["instances"]["auditor"], "harness": "pi",
        "lane": {"repo": "app", "paths": ["src/", "tests/"], "roles": ["executor"]}}, "body": "# PROBE-1\n"}))
    fills.update({"<CARD_DRAFT_JSON>": str(r.session / "cards" / "probe-1.json"), "<DRAFT_PATH>": "5_tasks/drafts/probe-1.md",
                  "<TASK_ID>": "PROBE-1"})
    step9 = [l.strip() for l in guide["steps"][8]["command"].splitlines() if l.strip() and not l.startswith("#")]
    loop_cmd = None
    for line in step9:
        for k, v in fills.items():
            line = line.replace(k, v)
        if line.startswith("lybra loop"):
            loop_cmd = line + " --max-wait 3 --interval 0.5"
            continue
        r.sh(line)
    assert "等待产物超时" in r.sh(loop_cmd, ok=(3,))

    def kickoff(ws: Path) -> dict:
        return json.loads(r.sh(f"lybra my-tasks --workstation {ws} --json"))["next_card"]

    card = kickoff(exec_ws)
    wt = Path(card["worktree_path"])
    _write(wt / "src" / "app" / "__init__.py", 'def hello():\n    return "app"\n\n\ndef greet(name):\n    return f"hello, {name}"\n')
    _write(wt / "tests" / "test_app.py", (wt / "tests" / "test_app.py").read_text(encoding="utf-8")
           + "\n\ndef test_greet():\n    assert app.greet('p') == 'hello, p'\n")
    _git(wt, "add", "src/app/__init__.py", "tests/test_app.py")
    _git(wt, "commit", "-q", "-m", "PROBE-1 greet")
    sha, tree = _git(wt, "rev-parse", "HEAD"), _git(wt, "rev-parse", "HEAD^{tree}")
    _write(Path(card["report_path"]), _fm({"commit_sha": f"'{sha}'", "tree_hash": f"'{tree}'", "branch": "'card/PROBE-1'",
                                           "model": "'claude-opus-5-5'"}, "# RETURN — PROBE-1\n\n## 一句话结论\ngreet 已加。\n"))
    assert "等待产物超时" in r.sh(loop_cmd, ok=(3,))  # 交回 → 派审 → 审计卡认领(取证树) → 等审计报告

    audit = kickoff(audit_ws)
    assert audit["task_id"] == "PROBE-1R" and _git(Path(audit["worktree_path"]), "rev-parse", "HEAD") == sha
    _write(Path(audit["report_path"]), _fm({"verdict": "PASS", "commit_sha": f"'{sha}'", "model": "'claude-opus-5-5'"},
                                           "# 审计报告 — PROBE-1R\n\n## 一句话结论\nPASS\n"))
    final = r.sh(loop_cmd)
    print(final)
    assert "finalize 成功" in final and "已结案(closure 记录存在)" in final
    # AIPOS-F94: 结案后 loop 自动 N6 落账(task 范围精确提交并推送); 向导第 1 / 2 步之后各落账一次
    assert "run governance_commit" in final and "治理已落账" in final
    hlog = _git(r.hroot, "log", "--format=%s")
    print("[F94·home 根治理仓 git log]\n" + hlog)
    assert "chore(governance): N6 收账 PROBE-1" in hlog and hlog.count("治理批次更新") >= 2
    assert _git(r.hroot, "rev-parse", "HEAD") == _git(r.hroot.parent / f"{r.hroot.name}-remote.git", "rev-parse", "main")
    assert _git(r.hroot, "status", "--porcelain", "-uall", "--", "lybra-probe/5_tasks/queue", "lybra-probe/5_tasks/records/claims/PROBE-1",
                "lybra-probe/project.json") == ""

    # 件③: 首次 finalize 通过(阶段门有快照; 无部署机制 = skipped), 产品仓 main 已合并, 主检出干净
    fin = next((gov / "5_tasks" / "records" / "finalizations" / "PROBE-1").glob("finalization_*.md")).read_text(encoding="utf-8")
    assert "deploy_status: skipped" in fin
    assert "Merge card/PROBE-1" in _git(r.session / "app", "log", "--oneline", "-1", "main")
    assert _git(r.session / "app", "status", "--porcelain") == ""
    assert (gov / "5_tasks" / "queue" / "completed" / "probe-1.md").is_file()
    assert "state lint OK" in r.sh(f"lybra state lint --workspace-root {gov}")
    blob = "\n".join(r.log)
    for t in (gov / ".lybra" / "connection.json", r.hroot / "ops" / ".lybra" / "connection.json"):
        for tok in json.loads(t.read_text(encoding="utf-8"))["tokens"]:
            assert tok["token"] not in blob  # token 永不上屏
    print(f"[靶场] {len(r.log)} 条命令, 一张卡 pending→completed")
