"""AIPOS-F94 — 真相落账三件(第一次接入验收第五节新发现)。

靶场一律临时 git 仓 + 临时远端裸仓(验 pushed) + 临时治理根; 门侧效果用 F73D GateDouble(只落记录), 落账步走真实产品命令
(`lybra governance-commit --task-id`, 经 CLI 入口在本进程执行)。不连生产门, 不碰真实治理仓。

件① loop 结案后自动 N6 落账:
   ① 一张卡 loop 从认领(claimed)跑到结案后自动落账: 治理仓 git log 出现该卡提交、工作区该卡路径干净、远端已含;
      他项目脏文件 / 他卡记录 / 无关治理文件原样不动(task 范围精确提交, 禁整根)
   ② 他人暂存存在 → 落账步 exit 2 明确拒因, 他人暂存原样; 处理后重跑 exit 0; 再跑 = 幂等(无新提交)
   close 的 next_step = 产品命令(无手写 git add/commit)
件② 接入向导第 1 / 2 步之后各落账一次: 命令原文过 argparse; 在临时 git home 原样执行 → 提交仅含本步产物且已推送
     (建项目的 decision_log 桩带声明 frontmatter, 过提交门 B②)
件③ state lint GOVERNANCE_UNCOMMITTED: 未跟踪 / 未提交 / 未推送三态点名 + 出口(审计卡指向被审卡); .gitignore 排除者不报;
     治理根不在 git 仓 = 报; 落账后清零
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f73d_loop_driver import (  # noqa: E402
    AUDITOR,
    init_governance_repo,
    DRIVER,
    EXEC,
    GateDouble,
    _claim_record,
    _fm,
    _policy,
    _substantive_return,
    _write,
)
from tools.aipos_cli import governance_commit as gc  # noqa: E402
from tools.aipos_cli.aipos_cli import build_parser  # noqa: E402
from tools.aipos_cli.loop_driver import check_command_parses, run_loop  # noqa: E402
from tools.aipos_cli.next_resolver import derive_next_step  # noqa: E402
from tools.aipos_cli.record_writer import CLOSURE_ID_PREFIX, build_return_skeleton_markdown  # noqa: E402
from tools.aipos_cli.state_lint import GOVERNANCE_UNCOMMITTED, run_state_lint  # noqa: E402

TASK = "AIPOS-F94T"
AUDIT = f"{TASK}R"
OTHER = "AIPOS-F94O"
GOV_REL = "2_projects/gov"


def _show(line: str) -> None:
    print(line, flush=True)


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True).stdout.strip()


def _card(gov: Path, task_id: str, queue: str, *, assigned: str, task_mode: str = "code", extra: dict | None = None) -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} test", "project": "gov", "task_mode": task_mode,
            "assigned_to": assigned, "agent_instance": assigned, "status": queue, "audit": "required", "audit_by": AUDITOR}
    meta.update(extra or {})
    path = gov / "5_tasks" / "queue" / queue / f"{task_id.lower()}.md"
    _write(path, _fm(meta, f"# {task_id}\n"))
    return path


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """共用治理仓(2_projects/gov = 治理根, 2_projects/other = 他项目) + 远端裸仓; 基线后弄脏他项目与无关治理文件。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    repo = tmp_path / "repo"
    gov = repo / GOV_REL
    other = repo / "2_projects" / "other"
    for sub in ("pending", "claimed", "completed", "blocked"):
        (gov / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events"):
        (gov / "5_tasks" / "records" / sub).mkdir(parents=True)
    (gov / "5_tasks" / "policies").mkdir()
    (gov / "task_cards").mkdir()
    (gov / "governance" / "decision_log").mkdir(parents=True)
    (gov / "governance" / "NOTES.md").write_text("---\nstatus: active\n---\n# notes\n", encoding="utf-8")
    code_repo = tmp_path / "product"
    code_repo.mkdir()
    _write(gov / "project.json", json.dumps({"project": "gov", "code_repo": str(code_repo), "config_version": 1}))
    _write(gov / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    _write(other / "plan.md", "plan v1\n")
    remote = init_governance_repo(repo)
    # 弄脏: 他项目已跟踪改动 + 未跟踪; 本治理根无关治理文件改动; 他卡的未跟踪记录
    _write(other / "plan.md", "plan v2 (other project, uncommitted)\n")
    _write(other / "untracked.md", "other project untracked\n")
    _write(gov / "governance" / "NOTES.md", "---\nstatus: active\n---\n# notes\nedited, not this card's\n")
    _write(gov / "5_tasks" / "records" / "claims" / OTHER / f"claim_{OTHER}_x.md",
           _fm({"record_type": "claim_record", "task_id": OTHER, "agent_instance": EXEC}))
    return {"repo": repo, "gov": gov, "other": other, "remote": remote}


def _scene_outside(rig: dict) -> dict:
    repo = rig["repo"]
    return {"other": _git(repo, "status", "--porcelain", "-uall", "--", "2_projects/other"),
            "notes": _git(repo, "status", "--porcelain", "--", f"{GOV_REL}/governance/NOTES.md"),
            "other_card": _git(repo, "status", "--porcelain", "-uall", "--", f"{GOV_REL}/5_tasks/records/claims/{OTHER}")}


def _closed_card(gov: Path, task_id: str = TASK) -> None:
    """直接构造一张已结案卡(门侧效果同 GateDouble 全程): completed 卡 + 审计卡 + claim/return/dispatch/verdict/finalization/closure。"""
    audit = f"{task_id}R"
    rec = gov / "5_tasks" / "records"
    _card(gov, task_id, "completed", assigned=EXEC)
    _card(gov, audit, "completed", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": task_id})
    _claim_record(gov, task_id, EXEC)
    _claim_record(gov, audit, AUDITOR)
    _write(rec / "returns" / task_id / f"return_{task_id}_x.md", _fm({"record_type": "return", "return_id": f"return_{task_id}_x", "task_id": task_id}))
    _write(rec / "audit_dispatches" / audit / f"dispatch_{audit}_x.md", _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": task_id}))
    _write(rec / "audit_verdicts" / task_id / f"verdict_{task_id}_x.md",
           _fm({"record_type": "audit_verdict_record", "verdict_id": "v", "verdict": "PASS", "reviewed_task_id": task_id, "audit_task_id": audit}))
    _write(rec / "finalizations" / task_id / "finalization_x.md", _fm({"record_type": "finalization_record", "task_id": task_id, "merge_commit": "a" * 40}))
    _write(rec / "closures" / task_id / f"{CLOSURE_ID_PREFIX}_{task_id}_x_exec.md",
           _fm({"record_type": "closure", "task_id": task_id, "actor": EXEC, "closed_at": "2026-10-04T00:00:00Z"}))
    _write(gov / "task_cards" / task_id / "RETURN.md", _substantive_return(task_id))
    _write(gov / "task_cards" / audit / "RETURN.md", "# audit\n\n## 一句话结论\nPASS\n")


def _head_files(repo: Path, rev: str = "HEAD") -> set[str]:
    return set(_git(repo, "show", "--name-only", "--no-renames", "--format=", rev).splitlines())


# ===========================================================================
# 件① 验收①: loop 从认领跑到结案后自动落账
# ===========================================================================

def test_item1_loop_claimed_to_completed_then_lands_task_scope_and_pushes(rig: dict):
    gov, repo = rig["gov"], rig["repo"]
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _policy(gov)
    _write(gov / "task_cards" / TASK / "RETURN.md", build_return_skeleton_markdown(TASK))
    # 认领时门落的卡与记录已在治理仓(顾问认领后落账的现场形): 结案搬动 = 旧位删除也属本卡
    _git(repo, "add", "--", f"{GOV_REL}/5_tasks/queue/claimed", f"{GOV_REL}/5_tasks/records/claims/{TASK}", f"{GOV_REL}/5_tasks/policies")
    _git(repo, "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "-m", "claim landed")
    _git(repo, "push", "-q")
    before_outside = _scene_outside(rig)
    gate = GateDouble(gov)
    out = io.StringIO()

    def agents() -> None:
        time.sleep(0.15)
        _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))
        deadline = time.time() + 5
        while time.time() < deadline and not (gov / "task_cards" / AUDIT / "RETURN.md").exists():
            time.sleep(0.02)
        time.sleep(0.15)
        _write(gov / "task_cards" / AUDIT / "audit_report.md",
               _fm({"task_id": AUDIT, "reviewed_task_id": TASK, "verdict": "PASS", "commit_sha": "a" * 40,
                    "actor": AUDITOR, "agent_instance": AUDITOR}, "# audit\n\n## 一句话结论\nPASS\n"))

    t = threading.Thread(target=agents, daemon=True)
    t.start()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=gate, interval=0.03, max_wait=8, max_steps=20)
    t.join(timeout=10)
    text = out.getvalue()
    _show("[①·loop 原文]\n" + text)
    assert res.exit_code == 0, text
    assert [c[0] for c in gate.calls] == ["return", "dispatch", "claim", "verdict", "finalize", "close", "governance_commit"], gate.calls
    landing_step = [s for s in res.steps if s.action_type == "governance_commit"][0]
    assert landing_step.command == gc.governance_commit_command(TASK, DRIVER, gov)
    assert "治理已落账" in res.message
    log = _git(repo, "log", "--oneline", "-3")
    _show(f"[①·治理仓 git log -3]\n{log}")
    # AIPOS-F147 件①: N6 落账提交之后, loop 出口 0 再补一次落账带走本次运行记录终态(同一提交口)——HEAD = 补落账提交(只含运行记录),
    # HEAD~1 = N6 落账提交
    assert f"N6 收账 {TASK}" in _git(repo, "log", "-1", "--format=%s")
    relanded = _head_files(repo)
    assert relanded and all(f.startswith(f"{GOV_REL}/5_tasks/records/loop_runs/{TASK}/looprun_") and f.endswith(".md") for f in relanded), relanded
    assert "补落账:" in text and "loop 运行记录终态" in text
    assert f"N6 收账 {TASK}" in _git(repo, "log", "-1", "--format=%s", "HEAD~1")
    committed = _head_files(repo, "HEAD~1")
    _show("[①·落账提交文件]\n  " + "\n  ".join(sorted(committed)))
    prefix = f"{GOV_REL}/"
    assert committed and all(f.startswith(prefix) for f in committed)
    assert f"{prefix}5_tasks/queue/completed/{TASK.lower()}.md" in committed
    assert f"{prefix}5_tasks/queue/claimed/{TASK.lower()}.md" in committed  # 旧位删除随本卡落账
    assert f"{prefix}5_tasks/queue/completed/{AUDIT.lower()}.md" in committed
    assert any(f.startswith(f"{prefix}5_tasks/records/closures/{TASK}/") for f in committed)
    assert any(f.startswith(f"{prefix}5_tasks/records/claims/{AUDIT}/") for f in committed)
    assert not any("NOTES.md" in f or OTHER in f or "2_projects/other" in f for f in committed)
    scope = gc.task_scope_candidates(gov, TASK)["paths"]
    assert _git(gov, "status", "--porcelain", "-uall", "--", *scope) == ""  # 工作区该卡路径干净
    assert _git(repo, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")  # 已推送
    assert _scene_outside(rig) == before_outside  # 他项目 / 无关治理文件 / 他卡记录原样
    assert _git(gov, "status", "--porcelain", "-uall", "--", f"5_tasks/records/loop_runs/{TASK}") == ""  # 运行记录已落账, .log 已排除
    # 再跑 loop: 已结案且已落账 → 一轮 exit 0, 不重复卡的落账; AIPOS-F147 件①: 本次运行自身的运行记录照样补落账(只此一份)
    head = _git(repo, "rev-parse", "HEAD")
    again = run_loop(TASK, gov, actor=DRIVER, out=io.StringIO(), execute=gate, interval=0.01, max_wait=1, max_steps=3)
    assert again.exit_code == 0 and len(again.steps) == 1
    assert _git(repo, "rev-parse", "HEAD~1") == head
    assert _head_files(repo) == {f"{GOV_REL}/5_tasks/records/loop_runs/{TASK}/{Path(again.run_record).name}"}


# ===========================================================================
# 件① 验收②: 他人暂存 → 明确拒因退出, 不动他人暂存; 处理后重跑落账; 幂等
# ===========================================================================

def test_item1_foreign_staged_rejects_with_reason_and_leaves_it_untouched(rig: dict):
    gov, repo = rig["gov"], rig["repo"]
    _closed_card(gov)
    _policy(gov)
    _git(repo, "add", "--", "2_projects/other/plan.md")  # 他人暂存(他项目)
    staged_before = _git(repo, "diff", "--cached", "--name-only")
    head = _git(repo, "rev-parse", "HEAD")
    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=GateDouble(gov), interval=0.01, max_wait=1, max_steps=5)
    text = out.getvalue()
    _show("[②·他人暂存 loop 原文(截)]\n" + text[:1500])
    assert res.exit_code == 2 and res.outcome == "gate_rejected", text
    assert "落账拒 @ governance_commit" in res.message and "2_projects/other/plan.md" in res.message
    assert "staged" in res.message
    assert _git(repo, "diff", "--cached", "--name-only") == staged_before == "2_projects/other/plan.md"  # 他人暂存原样
    assert (rig["other"] / "plan.md").read_text(encoding="utf-8") == "plan v2 (other project, uncommitted)\n"
    assert _git(repo, "rev-parse", "HEAD") == head  # 无提交
    # 他人处理掉自己的暂存后, 同一条 loop 重跑即落账
    _git(repo, "reset", "-q", "--", "2_projects/other/plan.md")
    res2 = run_loop(TASK, gov, actor=DRIVER, out=io.StringIO(), execute=GateDouble(gov), interval=0.01, max_wait=1, max_steps=5)
    assert res2.exit_code == 0, res2.message
    landed_head = _git(repo, "rev-parse", "HEAD")
    assert landed_head != head and _git(repo, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")
    # 幂等: 产品命令再跑 = PASS no-op, 不产生新提交
    again = gc.governance_commit(gov, TASK, DRIVER)
    _show(f"[②·幂等] verdict={again['verdict']} committed={again['committed']} message={again['message']}")
    assert again["verdict"] == "PASS" and again["committed"] is False and again.get("severity") == "info"
    assert _git(repo, "rev-parse", "HEAD") == landed_head


def test_item1_task_scope_is_derived_never_whole_root(rig: dict):
    gov, repo = rig["gov"], rig["repo"]
    _closed_card(gov)
    (gov / ".gitignore").write_text("task_cards/\n", encoding="utf-8")  # 项目把台账目录列入忽略(lybra / probe 现场形)
    _git(repo, "add", "--", f"{GOV_REL}/.gitignore")
    _git(repo, "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "-m", "ignore task_cards")
    _git(repo, "push", "-q")
    scope = gc.resolve_task_scope(gov, TASK)
    _show(f"[①·task 范围] paths={scope['paths']}\n  ignored={scope['ignored']}")
    assert scope["ignored"] == [f"task_cards/{TASK}", f"task_cards/{AUDIT}"]
    assert all(TASK in p or TASK.lower() in p for p in scope["paths"])
    assert not any(OTHER in p or "NOTES" in p for p in scope["paths"])
    dry = gc.governance_commit(gov, TASK, DRIVER, dry_run=True)
    assert dry["verdict"] == "PASS" and dry["dry_run"] and dry["selected_paths"] == scope["paths"]
    assert any(op.startswith("Task scope (task_scope, AIPOS-F94)") for op in dry["operations"])
    real = gc.governance_commit(gov, TASK, DRIVER)
    assert real["verdict"] == "PASS" and real["committed"] and real["pushed"], real["message"]
    assert not any("task_cards" in f for f in _head_files(repo))  # 被忽略者不提交、不报错
    # 空范围(卡号写错)= BLOCK 带拒因, 不做整根提交
    empty = gc.governance_commit(gov, "AIPOS-NOPE", DRIVER)
    assert empty["verdict"] == "BLOCK" and "落账范围为空" in empty["message"] and empty["committed"] is False


def test_item1_close_next_step_is_product_command_not_hand_git():
    from tools.aipos_cli import board_adapter

    step = board_adapter._n6_landing_next_step(TASK, Path("/tmp/gov root"), DRIVER, preview=False)
    _show(f"[①·close next_step] {step}")
    assert step["command"] == gc.governance_commit_command(TASK, DRIVER, "/tmp/gov root")
    assert check_command_parses(step["command"]) == (True, "")
    placeholder = board_adapter._n6_landing_next_step(TASK, Path("/tmp/g"), None, preview=True)["command"]
    assert check_command_parses(placeholder) == (True, "") and gc.N6_LANDING_ACTOR_PLACEHOLDER in placeholder
    src = inspect.getsource(board_adapter.close_task)
    assert "git add" not in src and "git commit" not in src


def test_item1_derive_n6_landing_step(rig: dict, tmp_path: Path):
    gov = rig["gov"]
    _closed_card(gov)
    d = derive_next_step(TASK, gov)
    _show(f"[①·推导核 N6 未落账] derivable={d['derivable']} verb={d['verb']} command={d['command']}")
    assert d["derivable"] and d["verb"] == "lybra_governance_commit" and d["current_state"] == "completed"
    assert check_command_parses(d["command"]) == (True, "")
    assert gc.governance_commit(gov, TASK, DRIVER)["pushed"] is True
    done = derive_next_step(TASK, gov)
    assert done["derivable"] and done["command"].startswith("#") and done["notes"] == "N6: 任务已结案"
    # 治理根不在 git 仓 = 不可推导(fail-closed), 硬停动作点名
    bare = tmp_path / "nogit"
    for sub in ("completed",):
        (bare / "5_tasks" / "queue" / sub).mkdir(parents=True)
    _write(bare / "project.json", json.dumps({"project": "nogit", "config_version": 1}))
    _closed_card(bare)
    nd = derive_next_step(TASK, bare)
    _show(f"[①·推导核 非 git 治理根] {nd['missing_records']}")
    assert nd["derivable"] is False and nd["action"]["type"] == "record_missing" and "不在 git 工作树内" in nd["missing_records"][0]


# ===========================================================================
# 件② 接入向导第 1 / 2 步落账
# ===========================================================================

def test_item2_guide_steps_1_2_land_their_products(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    from tools.aipos_cli.aipos_cli import main as cli_main
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))  # 无 ~/.lybra/connection.json → project new 走本地脚手架
    (tmp_path / "userhome").mkdir()
    home = tmp_path / "home"
    init_governance_repo(home)
    guide = generate_onboarding_guide("probe-f94", home_root=str(home), actor="advisor.probe-f94.h",
                                      repos=[f"app={tmp_path}/app"])
    gov = home / "probe-f94"
    for idx, expected in ((0, {"project.json", "governance/decision_log.md", "stage_archive"}), (1, {"project.json"})):
        lines = [l for l in guide["steps"][idx]["command"].splitlines() if l.strip() and not l.startswith("#")]
        landing = [l for l in lines if l.startswith("lybra governance-commit")]
        _show(f"[②·Step {idx + 1} 命令原文]\n" + "\n".join(lines))
        assert len(landing) == 1 and landing[0] == lines[-1]
        assert check_command_parses(landing[0]) == (True, "")
        argv = shlex.split(landing[0])
        assert {argv[i + 1] for i, a in enumerate(argv) if a == "--paths"} == expected
        assert "--task-id" not in argv and "." not in {argv[i + 1] for i, a in enumerate(argv) if a == "--paths"}
        for line in lines:
            capsys.readouterr()
            rc = cli_main(shlex.split(line)[1:])
            out = capsys.readouterr().out
            assert rc == 0, out[-2000:]
        _show(f"[②·Step {idx + 1} 落账输出(截)]\n" + "\n".join(l for l in out.splitlines() if l.startswith(("Verdict", "✓", "Message"))))
        files = _head_files(home)
        _show(f"[②·Step {idx + 1} 提交文件] {sorted(files)}")
        assert files and all(f.startswith("probe-f94/") for f in files)
        rels = {f.split("/", 1)[1] for f in files}
        assert all(any(r == e or r.startswith(e + "/") for e in expected) for r in rels), rels
        assert _git(home, "rev-parse", "HEAD") == _git(home.parent / "home-remote.git", "rev-parse", "main")
    assert (gov / "governance" / "decision_log.md").read_text(encoding="utf-8").startswith("---\nstatus: active\n")
    assert json.loads((gov / "project.json").read_text(encoding="utf-8"))["repos"]["default"] == "app"


def test_item2_advisor_skill_states_landing_rule():
    text = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    assert "每张卡由 `lybra loop` 结案后自动落账" in text
    assert "非卡改动" in text and "governance-commit --paths" in text
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("lybra governance-commit --task-id") or s.startswith("lybra state lint"):
            cmd = s.split("#", 1)[0].strip().replace("<卡ID>", "X-1").replace("<你的顾问实例>", "advisor.p.h").replace("<治理根>", "/tmp/g")
            with contextlib.redirect_stderr(io.StringIO()):
                build_parser().parse_args(shlex.split(cmd)[1:])


# ===========================================================================
# 件③ state lint GOVERNANCE_UNCOMMITTED
# ===========================================================================

def _gu(gov: Path) -> dict[str, dict]:
    return {i["task_id"]: i for i in run_state_lint(gov)["issues"] if i.get("code") == GOVERNANCE_UNCOMMITTED}


def test_item3_lint_names_uncommitted_unpushed_and_clears_after_landing(rig: dict, tmp_path: Path):
    gov, repo = rig["gov"], rig["repo"]
    _closed_card(gov)
    issues = _gu(gov)
    for tid, issue in sorted(issues.items()):
        _show(f"[③·lint 未落账] [{issue['severity']}] {tid}: {issue['message']}")
    assert set(issues) == {TASK, AUDIT}
    assert all(i["severity"] == "WARN" for i in issues.values())
    assert issues[AUDIT]["exit_task_id"] == TASK and f"--task-id {TASK} " in issues[AUDIT]["message"]
    exit_cmd = issues[TASK]["message"].split("出口: ", 1)[1].split("(", 1)[0].strip()
    assert check_command_parses(exit_cmd.replace(gc.N6_LANDING_ACTOR_PLACEHOLDER, DRIVER).replace("'", "")) == (True, "")
    assert not any(OTHER == t for t in issues)  # 未结案的卡不报
    # 出口执行 → 清零
    assert gc.governance_commit(gov, TASK, DRIVER)["pushed"] is True
    assert _gu(gov) == {}
    # 已提交未推送 → 点名「未推送」
    _write(gov / "5_tasks" / "records" / "events" / TASK / "event_x.md", _fm({"record_type": "task_progress_event", "task_id": TASK}))
    _git(repo, "add", "--", f"{GOV_REL}/5_tasks/records/events/{TASK}")
    _git(repo, "-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "-m", "local only")
    unpushed = _gu(gov)
    _show(f"[③·lint 未推送] {unpushed[TASK]['message']}")
    assert set(unpushed) == {TASK} and "已提交未推送" in unpushed[TASK]["message"] and unpushed[TASK]["unpushed"]
    # 治理根不在 git 仓 = 每张已结案卡都报(无仓即无可追溯真相)
    nogit = tmp_path / "nogit"
    (nogit / "5_tasks" / "queue" / "completed").mkdir(parents=True)
    _write(nogit / "project.json", json.dumps({"project": "nogit", "config_version": 1}))
    _closed_card(nogit)
    ng = _gu(nogit)
    assert set(ng) == {TASK, AUDIT} and "不在 git 工作树内" in ng[TASK]["message"]


def test_item3_cli_lint_json_and_text(rig: dict, capsys):
    from tools.aipos_cli.aipos_cli import main as cli_main

    gov = rig["gov"]
    _closed_card(gov)
    capsys.readouterr()
    rc = cli_main(["state", "lint", "--workspace-root", str(gov)])
    text = capsys.readouterr().out
    _show("[③·CLI 文本]\n" + text)
    assert rc == 1 and f"[WARN] {TASK}: GOVERNANCE_UNCOMMITTED" in text and f"[WARN] {AUDIT}: GOVERNANCE_UNCOMMITTED" in text


# ===========================================================================
# 纪律: 无吞异常 / 声明与登记
# ===========================================================================

def test_discipline_no_swallowed_exceptions_and_registered_in_runall():
    from tools.aipos_cli import loop_driver, next_resolver, onboarding, state_lint

    funcs = [gc.card_own_paths, gc.task_scope_candidates, gc.resolve_task_scope, gc.governance_landing, gc.task_landing,
             gc._task_scope_selection, gc.governance_commit, next_resolver._derive_n6_landing, next_resolver._run_cli_in_process,
             loop_driver._landing, loop_driver._drive, state_lint.governance_uncommitted_issues, onboarding.project_new_products,
             gc.n6_landing_declaration, gc.governance_commit_cli, gc.non_git_exit_command, state_lint.check_severity,
             onboarding.governance_repo_status]
    from tools.aipos_cli import enrollment, governance_add

    funcs += [governance_add.governance_doc_frontmatter, enrollment._append_enrollment_trail]
    for fn in funcs:
        src = inspect.getsource(fn)
        assert "except Exception" not in src and "except:" not in src, fn.__name__
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    from tools.aipos_cli.workspace_config import runall_unregistered  # AIPOS-F109 件④: 登记判据 = 门同一实现(清单 discover = 命中式样即登记)

    assert runall_unregistered(["tests/test_aipos_f94_governance_landing.py"], runall)[0] == []


def test_item3_lookup_scope_same_answers_as_per_card_lookup(rig: dict):
    """全量 lint 的一趟索引(task_card_lookup_scope)与逐卡查找同判据: 同结果、多义同样拒; 作用域外行为不变。"""
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card, task_card_lookup_scope

    gov = rig["gov"]
    _closed_card(gov)
    _card(gov, OTHER, "claimed", assigned=EXEC)
    plain = {t: find_task_card(gov, t) for t in (TASK, AUDIT, OTHER, "NOPE-1")}
    with task_card_lookup_scope(gov):
        scoped = {t: find_task_card(gov, t) for t in (TASK, AUDIT, OTHER, "NOPE-1")}
    assert scoped == plain and plain["NOPE-1"] == (None, None)
    _card(gov, OTHER, "pending", assigned=EXEC)  # 同 task_id 两份文件
    with pytest.raises(AmbiguousTaskCard), task_card_lookup_scope(gov):
        find_task_card(gov, OTHER)
    with pytest.raises(AmbiguousTaskCard):
        find_task_card(gov, OTHER)


# ===========================================================================
# 补完 N1/N2/N5/N6(顾问 2026-10-04: 不转债, 车道补入 schema/ 与 templates/)
# ===========================================================================

def test_n1_landing_step_declared_once_and_code_reads_it(monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import loop_driver
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.next_resolver import _action_type_for_command, _transition_node

    decl = _transition_node("N6")["landing"]
    _show(f"[N1·transitions nodes.N6.landing] action_type={decl['action_type']} verb={decl['verb']} "
          f"command_template={decl['command_template']} envelope={decl['envelope'][:40]}…")
    loop = load_loop_contract()
    assert decl["action_type"] in loop["envelope"]["allowed_verbs"]  # 独立授权, 声明里写明
    assert "governance_commit" in loop["envelope"]["allowed_verbs_note"]
    assert "治理已落账" in loop["exit_codes"]["completed"]["meaning"] and "落账" in loop["exit_codes"]["gate_rejected"]["meaning"]
    cmd = gc.governance_commit_command(TASK, DRIVER, "/tmp/g")
    assert cmd == decl["command_template"].format(task_id=TASK, actor=DRIVER, governance_root="/tmp/g")
    assert _action_type_for_command(cmd) == decl["action_type"]
    src = inspect.getsource(loop_driver)
    assert "_ENVELOPE_VERB_FOR_ACTION" not in src and '"governance_commit"' not in src  # 代码不再私定授权映射/动作名
    # 改声明 → 渲染跟随(单源)
    from tools.aipos_cli import next_resolver

    real = next_resolver._transition_node
    monkeypatch.setattr(next_resolver, "_transition_node",
                        lambda nid: {**real(nid), "landing": {**decl, "command_template": decl["command_template"] + " --no-push"}} if nid == "N6" else real(nid))
    assert gc.governance_commit_command(TASK, DRIVER, "/tmp/g").endswith(" --no-push")
    monkeypatch.setattr(next_resolver, "_transition_node", lambda nid: {k: v for k, v in real(nid).items() if k != "landing"} if nid == "N6" else real(nid))
    from tools.schema_loader import SchemaLoadError

    with pytest.raises(SchemaLoadError):
        gc.governance_commit_command(TASK, DRIVER, "/tmp/g")


def test_n2_lint_severity_from_declaration(rig: dict, monkeypatch: pytest.MonkeyPatch):
    from tools import schema_loader
    from tools.aipos_cli import state_lint
    from tools.schema_loader import SchemaLoadError

    gov = rig["gov"]
    _closed_card(gov)
    assert not hasattr(state_lint, "GOVERNANCE_UNCOMMITTED_SEVERITY")
    declared = schema_loader.load_schema("transitions")["state_consistency"]["check_severity"][GOVERNANCE_UNCOMMITTED]["severity"]
    assert {i["severity"] for i in _gu(gov).values()} == {declared}
    real = schema_loader.load_schema

    def patched(name, *a, sev="ERROR", **kw):
        data = real(name, *a, **kw)
        if name == "transitions":
            data = json.loads(json.dumps(data))
            if sev is None:
                data["state_consistency"].pop("check_severity")
            else:
                data["state_consistency"]["check_severity"][GOVERNANCE_UNCOMMITTED]["severity"] = sev
        return data

    monkeypatch.setattr(schema_loader, "load_schema", patched)
    assert {i["severity"] for i in _gu(gov).values()} == {"ERROR"}  # 改声明 → 级别跟随
    monkeypatch.setattr(schema_loader, "load_schema", lambda name, *a, **kw: patched(name, *a, sev=None, **kw))
    with pytest.raises(SchemaLoadError):
        run_state_lint(gov)  # 缺声明 = fail-closed


def test_n5_guide_inits_governance_repo_when_home_not_git_and_non_git_rejects_carry_exit(tmp_path: Path):
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    home = tmp_path / "plain home"
    guide = generate_onboarding_guide("probe-n5", home_root=str(home), actor="advisor.probe-n5.h")
    lines = [l for l in guide["steps"][0]["command"].splitlines() if l.strip() and not l.startswith("#")]
    _show("[N5·非 git home 第 1 步]\n" + guide["steps"][0]["command"])
    exit_cmd = gc.non_git_exit_command(home / "probe-n5")
    assert guide["governance_repo"] == {"git": False, "repo_root": None}
    assert lines[0] == exit_cmd and lines[0].startswith("lybra home git-init --home-root ")
    assert all(check_command_parses(l) == (True, "") for l in lines if l.startswith("lybra"))
    git_home = tmp_path / "git-home"
    init_governance_repo(git_home)
    g2 = generate_onboarding_guide("probe-n5", home_root=str(git_home), actor="advisor.probe-n5.h")
    assert g2["governance_repo"]["git"] and "home git-init" not in g2["steps"][0]["command"]
    # 非 git 治理根: 推导核 / 落账命令 / lint 拒因都带同一出口
    nogit = tmp_path / "nogit" / "gov"
    (nogit / "5_tasks" / "queue" / "completed").mkdir(parents=True)
    _write(nogit / "project.json", json.dumps({"project": "gov", "config_version": 1}))
    _closed_card(nogit)
    want = gc.non_git_exit_command(nogit)
    d = derive_next_step(TASK, nogit)
    blocked = gc.governance_commit(nogit, TASK, DRIVER)
    lint = _gu(nogit)[TASK]["message"]
    _show(f"[N5·非 git 拒因] derive.suggested={d['suggested_action']}\n  commit={blocked['message'][:300]}\n  lint={lint[:300]}")
    assert want in d["suggested_action"] and want in blocked["message"] and want in lint
    assert blocked["verdict"] == "BLOCK" and blocked["committed"] is False


def test_n6_gate_written_governance_docs_carry_declared_frontmatter_and_headless_is_identified(tmp_path: Path):
    from tools.aipos_cli.enrollment import _append_enrollment_trail
    from tools.aipos_cli.governance_add import governance_doc_frontmatter
    from tools.aipos_cli.governance_guardrails import check_entries, load_guardrail_declarations

    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    repo = tmp_path / "repo"
    gov = repo / "proj"
    (gov / "governance").mkdir(parents=True)
    (gov / "5_tasks").mkdir()
    trail = _append_enrollment_trail(gov, action="create", code_id="c1", role="executor", instance="e.p.h", by="pol", reason="t")
    text = trail.read_text(encoding="utf-8")
    _show(f"[N6·门写 enrollment_log 首建]\n{text}")
    assert text.startswith(governance_doc_frontmatter() + "\n# Enrollment Codes Log")
    rep = check_entries(repo, [("A", "proj/governance/enrollment_log.md")], decls, current_branch="main")
    assert rep.ok, rep.violations
    _append_enrollment_trail(gov, action="use", code_id="c1", role="executor", instance="e.p.h", by="x", reason="t")
    assert trail.read_text(encoding="utf-8").count("---") == 2  # 追加不重复写头
    # 存量无头文件(旧写侧形 / probe 现场形)被提交门 B② 识别(只读判据)
    (gov / "governance" / "decision_log.md").write_text("# proj Decision Log\n", encoding="utf-8")
    old = check_entries(repo, [("A", "proj/governance/decision_log.md")], decls, current_branch="main")
    assert [v["check"] for v in old.violations] == ["B②"]
