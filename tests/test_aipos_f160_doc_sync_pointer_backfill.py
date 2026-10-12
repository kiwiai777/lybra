"""AIPOS-F160(gap #145) — 自动落账后的文档同步 + 历史决策指针补生成 + 重签提示去 --code。

件① 文档同步: F158/F159 后 `envelope mint --confirm` 与 project 族 `--confirm` 已写后即落账, 产品文档(接入 / 顾问命令 skill、
    QUICKSTART、docs、README)与接入向导输出不再教「--confirm 后再手跑 governance-commit」; governance-commit 只留真实需要的场景
    (N6 卡落账 / project new 建根后 / 手改治理文档 / 落账失败补账)。
件② 重签提示: token_resolver 全 retired 出口(config.schema token.entry.reenroll_exit)等用户可见提示里的 `--code <码>` 改为
    enroll-deliver 口径(签码 + 兑换同一进程, 码不进命令行); 出口形 = 向导同一渲染 onboarding.render_enroll_deliver_command。
件③ 历史缺 decision_log 指针补生成: `lybra migrate decision-log-pointers` 归 migrate 族(与 record-type 同一两阶段、同一提交口):
    缺省预演零写入并列出将生成的指针名, --confirm --actor 经门落库同一生成实现 board_adapter._auto_generate_decision_log_pointer
    写出并经 F158 落账包装 governance_commit.land_written_paths 提交推送; 幂等; 推导不出的列出不补。

靶场 = 临时 home 根(git 仓 + 临时远端裸仓, 治理根嵌在仓内子目录)。项目名 / 实例名 / 决策 ID 均为靶场值(产品不写死)。
全程不读真实凭据、不碰真实治理根。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源
from test_aipos_f158_envelope_landing_record_type_migration import InProcessGate  # noqa: E402  — 门替身唯一来源(只替换 HTTP 传输)

PROJECT = "f160-probe"                 # 靶场项目名(非真实项目)
ADVISOR = f"advisor.{PROJECT}.h1"
RECORDS = "5_tasks/records/owner_decisions"


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


def _snapshot(home: Path) -> tuple:
    return _tree_digest(home), _git(home, "status", "--porcelain", "-uall"), _git(home, "rev-parse", "HEAD")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """home 根 = 治理仓(带 origin 裸仓); 治理根 = <home>/<PROJECT>(仓内子目录, 提交前缀 = 人肉期接入项目形)。"""
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
                             "tokens": [{"role": "owner", "token": "fixture-owner-token-f160", "scopes": ["owner_confirm"]}]}))
    return {"home": hroot, "gov": hroot / PROJECT, "remote": remote, "conn": conn, "tmp": tmp_path}


def _mint(rig, *policy_ids: str) -> tuple[int, str, str]:
    argv = ["envelope", "mint", "--workspace-root", str(rig["gov"]), "--connection-json", str(rig["conn"]),
            "--max-tasks", "3", "--task-mode", "code", "--expires-at", "2999-01-01T00:00:00Z",
            "--decision-summary", "F160 靶场签发", "--confirm"]
    for pid in policy_ids:
        argv += ["--policy-id", pid, "--agent-or-role", ADVISOR]
    return _cli(*argv)


# ===========================================================================
# 件③ 历史缺 decision_log 指针补生成
# ===========================================================================

def _pointerless_fixture(rig, monkeypatch) -> dict[str, str]:
    """造人肉期 / gap #142 样本: 门签三张信封(各带指针) → 删 b、c 的指针(#142 同日重名遗留形); 另造
    old(合格决策记录, 决策时刻在往月)、x(record_type 非门决策类)、y(缺一句话结论行)、z(同名指针文件已在但指向别处)。"""
    import tools.aipos_cli.confirm_client as confirm_client
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.schema_loader import resolve_governance_path

    monkeypatch.setattr(confirm_client, "GateClient", InProcessGate)
    home, gov = rig["home"], rig["gov"]
    rc, out, err = _mint(rig, "pol_f160_a", "pol_f160_b", "pol_f160_c")
    assert rc == 0, out + err
    log_dir = resolve_governance_path("decision_log_dir", gov)
    for pid in ("pol_f160_b", "pol_f160_c"):
        next(log_dir.rglob(f"*-envelope-{pid}-auto-pointer.md")).unlink()
    rec_dir = gov / RECORDS
    b_text = (rec_dir / "envelope-pol_f160_b.md").read_text(encoding="utf-8")
    b_meta, _ = require_frontmatter(rec_dir / "envelope-pol_f160_b.md")
    old_text = b_text.replace("envelope-pol_f160_b", "dec-f160-old").replace(str(b_meta["decided_at"]), "2026-08-23T00:00:00Z")
    _write(rec_dir / "dec-f160-old.md", old_text)
    _write(rec_dir / "dec-f160-x.md", "---\nrecord_type: owner_decision\ndecision_id: dec-f160-x\n---\n# x\n")
    _write(rec_dir / "dec-f160-y.md", re.sub(r"(?m)^- Summary: .*\n", "", b_text.replace("envelope-pol_f160_b", "dec-f160-y")))
    _write(rec_dir / "dec-f160-z.md", b_text.replace("envelope-pol_f160_b", "dec-f160-z"))
    date = str(b_meta["decided_at"])[:10]
    clash = log_dir / date[:7] / f"{date}-dec-f160-z-auto-pointer.md"
    _write(clash, "---\nstatus: active\ndecision_type: auto_pointer\n---\n\n## other\n\n**Record**: `5_tasks/records/owner_decisions/other.md`\n")
    _git(home, "add", "-A")
    _git(home, "commit", "-q", "-m", "fixture: pointerless decisions (人肉期 / #142 形)")
    _git(home, "push", "-q", "origin", "main")
    return {"log_dir": log_dir.relative_to(gov).as_posix(), "date": date, "clash": clash.relative_to(gov).as_posix()}


def test_item3_backfill_preview_confirm_idempotent(rig, monkeypatch):
    home, gov = rig["home"], rig["gov"]
    fx = _pointerless_fixture(rig, monkeypatch)
    log_dir, date = fx["log_dir"], fx["date"]
    expect = {
        "envelope-pol_f160_b": f"{log_dir}/{date[:7]}/{date}-envelope-pol_f160_b-auto-pointer.md",
        "envelope-pol_f160_c": f"{log_dir}/{date[:7]}/{date}-envelope-pol_f160_c-auto-pointer.md",
        "dec-f160-old": f"{log_dir}/2026-08/2026-08-23-dec-f160-old-auto-pointer.md",
    }

    # 预演(缺省): 列出缺指针记录与将生成的指针名, 推导不出的带原因; 零写入、不落账
    before = _snapshot(home)
    rc, out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov))
    _show(f"[件③·预演] rc={rc}\n{out}{err}")
    assert rc == 0 and "预览(未写; 加 --confirm 写入并落账)" in out
    for did, pointer in expect.items():
        assert f"[补齐] {RECORDS}/{did}.md  decision_id={did}  → {pointer}" in out, did
    assert re.search(rf"\[推导不出·不改\] {RECORDS}/dec-f160-x\.md .*record_type=owner_decision 非 owner_decision_record", out)
    assert re.search(rf"\[推导不出·不改\] {RECORDS}/dec-f160-y\.md .*一句话结论行", out)
    assert re.search(rf"\[推导不出·不改\] {RECORDS}/dec-f160-z\.md .*同名指针文件已在", out)
    assert "envelope-pol_f160_a" not in out, "已有指针者不列"
    assert _snapshot(home) == before, "预演零写入、不落账"

    rc, out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov), "--confirm")
    _show(f"[件③·--confirm 缺 --actor] rc={rc}\n{out}{err}")
    assert rc == 2 and "--actor" in err and _snapshot(home) == before

    # 确认: 经同一生成实现写出 3 份, 经同一提交口落账(只提交本次写出的指针)并推送
    rc, out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov), "--actor", ADVISOR, "--confirm")
    msg = _git(home, "log", "-1", "--format=%B")
    names = _git(home, "show", "--name-only", "--format=", "HEAD").splitlines()
    status = _git(home, "status", "--porcelain", "-uall")
    pushed = _git(home, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")
    _show(f"[件③·确认] rc={rc}\n{out}{err}[件③·git log -1]\n{msg}\n[件③·git show --name-only HEAD] {names}\n"
          f"[件③·status] {status!r} pushed={pushed}")
    assert rc == 0 and "已写入" in out and "落账: verdict=PASS committed=True pushed=True" in out
    assert msg.startswith("chore(governance): decision_log 指针补生成 3 份") and f"Actor: {ADVISOR}" in msg
    assert "Command: lybra migrate decision-log-pointers --confirm" in msg
    assert sorted(names) == sorted(f"{PROJECT}/{p}" for p in expect.values()) and status == "" and pushed
    for did, pointer in expect.items():
        body = (gov / pointer).read_text(encoding="utf-8")
        assert f"**Record**: `{RECORDS}/{did}.md`" in body and "decision_type: auto_pointer" in body
        assert "**Backfilled pointer** (AIPOS-F160)" in body and "`lybra migrate decision-log-pointers --confirm`" in body
    old_body = (gov / expect["dec-f160-old"]).read_text(encoding="utf-8")
    _show(f"[件③·补生成指针原文 {expect['dec-f160-old']}]\n{old_body}")
    assert "decided_at: '2026-08-23T00:00:00Z'" in old_body or "decided_at: 2026-08-23T00:00:00Z" in old_body
    a_pointer = next((gov / log_dir).rglob("*-envelope-pol_f160_a-auto-pointer.md")).read_text(encoding="utf-8")
    assert "**Auto-generated pointer** (AIPOS-R6M 大项A②)" in a_pointer, "门落库路径的指针文案不变"
    assert (gov / fx["clash"]).read_text(encoding="utf-8").count("other.md") == 1, "同名指针不覆盖"

    # 幂等: 重跑确认 = 无可补齐, 不提交; 预演只剩推导不出的三份
    head = _git(home, "rev-parse", "HEAD")
    rc, out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov), "--actor", ADVISOR, "--confirm")
    _show(f"[件③·重跑确认(幂等)] rc={rc}\n{out}{err}")
    assert rc == 0 and "无可补齐(未写)" in out and "wrote " not in out and "落账" not in out
    assert _git(home, "rev-parse", "HEAD") == head and _git(home, "status", "--porcelain", "-uall") == ""
    rc, out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov), "--json")
    plan = json.loads(out)
    assert rc == 0 and plan["counts"] == {"derived": 0, "underivable": 3} and plan["written"] == [] and plan["landing"] is None
    assert sorted(i["decision_id"] for i in plan["items"]) == ["dec-f160-x", "dec-f160-y", "dec-f160-z"]


def test_item3_landing_failure_keeps_pointers_and_gives_remedy(rig, monkeypatch):
    """落账失败(他人暂存挡提交)= 指针照写(不回滚), 给补账命令并以声明退出码退出; 补账后重跑无可补。"""
    from tools.aipos_cli.governance_commit import post_write_landing_declaration

    home, gov = rig["home"], rig["gov"]
    _pointerless_fixture(rig, monkeypatch)
    _write(gov / "governance" / "notes-f160.md", "---\nstatus: active\n---\n# other's staged work\n")
    _git(home, "add", f"{PROJECT}/governance/notes-f160.md")
    rc, out, err = _cli("migrate", "decision-log-pointers", "--workspace-root", str(gov), "--actor", ADVISOR, "--confirm")
    _show(f"[件③·落账失败] rc={rc}\n{out}{err}")
    assert rc == post_write_landing_declaration()["landing_failed_exit_code"]
    assert "⚠ 落账未完成(写入已生效, 勿重复执行写命令)" in err and "补账: lybra governance-commit" in err
    written = [ln.split("wrote ", 1)[1] for ln in out.splitlines() if ln.strip().startswith("wrote ")]
    assert len(written) == 3 and all((gov / w).is_file() for w in written)


def test_item3_declarations_and_single_implementations():
    """防碎片化: 归 migrate 族同一两阶段 + 同一提交口; 生成只经门落库同一函数, 不另写指针渲染 / 命名 / git 提交。"""
    from tools.aipos_cli.aipos_cli import _two_phase_family_declaration, build_parser
    from tools.aipos_cli.decision_log_pointers import backfill_declaration
    from tools.aipos_cli.governance_commit import post_write_landing_declaration

    decl = backfill_declaration()
    fam = _two_phase_family_declaration(decl["two_phase_family"])
    assert decl["policy"] == "two_phase" and decl["two_phase_family"] == "records_migration_writers"
    assert decl["command"] == "lybra migrate decision-log-pointers" and "decision-log-pointers" in fam["commands"]
    assert "record-type" in fam["commands"], "与 record-type 同族"
    assert decl["landing_command"] in post_write_landing_declaration()["commands"]
    sub = build_parser()._subparsers._group_actions[0].choices["migrate"]._subparsers._group_actions[0].choices
    helps = {name: sub[name].format_help() for name in ("record-type", "decision-log-pointers")}
    _show("[件③·--help]\n" + helps["decision-log-pointers"])
    for flag in ("--dry-run", "--confirm", "--actor", "--json", "--workspace-root"):
        assert flag in helps["decision-log-pointers"] and flag in helps["record-type"], flag
    src = (REPO_ROOT / "tools/aipos_cli/decision_log_pointers.py").read_text(encoding="utf-8")
    assert src.count("_auto_generate_decision_log_pointer(") == 2 and "land_written_paths(" in src
    for banned in ("write_text(", '"commit"', '"add"', "auto-pointer", "render_frontmatter_block"):
        assert banned not in src, banned


# ===========================================================================
# 件② 用户可见提示不再把注册码写进命令行
# ===========================================================================

def test_item2_reenroll_exit_is_enroll_deliver_without_code():
    from tools.aipos_cli.onboarding import render_enroll_deliver_command
    from tools.aipos_cli.token_resolver import TOKEN_REENROLL_EXIT

    _show(f"[件②·重签出口] {TOKEN_REENROLL_EXIT}")
    assert "--code" not in TOKEN_REENROLL_EXIT and " enroll-deliver " in TOKEN_REENROLL_EXIT
    expected = render_enroll_deliver_command(["lybra", "roles"], role="<角色>", instance="<实例>", governance_root="<治理根>",
                                             workspace="<工位根>", extra=["--owner-authorization-ref", "<授权引用>"], phase="--dry-run")
    assert TOKEN_REENROLL_EXIT.startswith(expected), "出口形 = 向导同一渲染(签码 + 兑换一体)"
    argv = shlex.split(expected.replace("<", "x").replace(">", "x"))
    from tools.aipos_cli.aipos_cli import build_parser

    with contextlib.redirect_stderr(io.StringIO()):
        build_parser().parse_args(argv[1:])  # 出口命令可被 CLI 解析(参数齐)


def test_item2_unenrolled_sync_hint_uses_enroll_deliver(tmp_path):
    from tools.aipos_cli.distribution_sync import _validate_enrolled

    ws = tmp_path / "ws"
    (ws / ".lybra").mkdir(parents=True)
    with pytest.raises(ValueError) as exc:
        _validate_enrolled(ws)
    _show(f"[件②·未接入工位 sync 提示] {exc.value}")
    assert "未完成 enroll" in str(exc.value) and "enroll-deliver" in str(exc.value) and "--code" not in str(exc.value)


#: 用户可见提示里「注册码写进命令行」的式样(占位或真码示例)
_CODE_ON_CMDLINE = re.compile(r"--code (?:<|LYBRAENROLL1|\"|')")
#: 保留处(逐条有理由): 发码契约本体与其说明 —— enroll-code 按既有契约展示一次自包含码交给他机接收方(verbs.schema
#: roles_writers.output_scope; 无 ssh 的跨机路径), paste_text 唯一渲染 onboarding.render_enroll_command 及其文档串 / 门侧动词说明
_RETAINED = {
    "tools/aipos_cli/onboarding.py": "render_enroll_command 文档串: 发码 paste_text 唯一渲染(enroll-code 契约本体)",
    "tools/aipos_cli/enrollment.py": "发码交付文案说明(paste_text = enroll-code 契约本体)",
}


def test_item2_no_user_visible_code_on_command_line():
    files = subprocess.run(["git", "ls-files", "--", "tools/aipos_cli", "tools/mcp_server", "schema", "agents", "docs", "QUICKSTART.md",
                            "README.md"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for rel in files:
        if "/tests/" in f"/{rel}" or Path(rel).name.startswith("test_") or not rel.endswith((".py", ".md", ".json", ".ts", ".sh")):
            continue
        for no, line in enumerate((REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _CODE_ON_CMDLINE.search(line):
                hits.append(f"{rel}:{no}:{line.strip()[:160]}")
    _show(f"[件②·注册码进命令行式样命中 {len(hits)} 处]\n  " + "\n  ".join(hits))
    assert _CODE_ON_CMDLINE.search("lybra roles enroll --code <码> --workspace x"), "式样自检"
    bad = [h for h in hits if h.split(":", 1)[0] not in _RETAINED]
    assert not bad, bad
    assert {h.split(":", 1)[0] for h in hits} <= set(_RETAINED)


# ===========================================================================
# 件① 文档不再教「自动落账后再手跑 governance-commit」
# ===========================================================================

_AUTO_LANDING = re.compile(r"lybra (?:project (?:set-[a-z-]+|freeze-legacy|dispatch-mode set)|envelope mint|migrate [a-z-]+)\b.*--confirm")
_DOCS = ["QUICKSTART.md", "README.md", "agents/skills/lybra-onboarding/SKILL.md", "agents/skills/advisor-commands/SKILL.md",
         "agents/roles/advisor/AGENTS.md", "docs/release_discipline.md", "docs/mcp-agent-setup.md", "tools/aipos_cli/README.md"]


def test_item1_docs_do_not_teach_manual_landing_after_auto_landing_confirm():
    """代码块里自动落账命令的 --confirm 行之后(下一条非注释命令)不得紧跟手跑 governance-commit。"""
    problems = []
    for rel in _DOCS:
        cmds = [ln.strip() for ln in (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
                if ln.strip().startswith("lybra ")]
        for prev, cur in zip(cmds, cmds[1:]):
            if _AUTO_LANDING.search(prev) and cur.startswith("lybra governance-commit"):
                problems.append(f"{rel}: {prev!r} → {cur!r}")
    onboarding = (REPO_ROOT / "agents/skills/lybra-onboarding/SKILL.md").read_text(encoding="utf-8")
    quick = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    _show("[件①·问题] " + json.dumps(problems, ensure_ascii=False))
    assert not problems
    assert "照 guide 原样跑该步的 `lybra governance-commit" not in onboarding
    assert "每步之后的落账(`lybra governance-commit --paths <本步产物>`)向导已原样给出" not in quick


def test_item1_guide_step2_no_redundant_landing_line(tmp_path):
    """向导第 2 步(set-repos --confirm 写后即落账)不再给多余的手跑 governance-commit 行; 第 1 步(project new 建根)保留。"""
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    guide = generate_onboarding_guide("probe-f160", home_root=str(tmp_path / "home"), actor="advisor.probe-f160.h",
                                      repos=[f"app={tmp_path}/app"])
    step1, step2 = guide["steps"][0]["command"], guide["steps"][1]["command"]
    _show(f"[件①·向导 Step 2 原文]\n{step2}")
    assert any(ln.startswith("lybra governance-commit") for ln in step1.splitlines())
    assert not [ln for ln in step2.splitlines() if ln.startswith("lybra governance-commit")]
    assert any(ln.startswith("lybra project set-repos") and ln.endswith("--confirm") for ln in step2.splitlines())
