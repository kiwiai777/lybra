"""AIPOS-F184 — 队列写命令落账与撤换正门(gap #164 / #165 / #186)。

靶场一律临时 git 仓 + 临时远端裸仓 + 临时治理根(不连生产门, 不碰真实治理仓); 落账走真实产品实现
(governance_commit.governance_commit / CLI main 本进程执行)。

件① N6 归因: 顾问在 N6「范围外预暂存检查」与 commit 之间并发 `git add governance/LEDGER.md`(10-16 F173 实撞)→ N6 提交只含本卡清单,
   LEDGER 仍在索引里未提交(原样); 同卡重复 N6 幂等(无新提交); 推送失败 = 首行人话提示 + 重试命令, 远端恢复后重跑同一命令只补推送
   (无新提交); 补落账提交信息与首次 N6 区分。
件② 队列写落账: CLI queue withdraw / block / reopen 写入后 git log 出现「队列写落账」提交, 只含卡迁移前后路径(+ 记录), 已推送。
件③ queue supersede: claimed 卡(会话 < 24h, withdraw 被在途保护拒)与 blocked 卡各一例 预演 / 确认; 卡面 superseded_*;
   收件箱该卡事项消失; Owner 原话落 owner_decisions(owner decide 同一记录类型); 非顾问身份 / 新卡不存在 / 新卡 = 原卡 拒。
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f73d_loop_driver import AUDITOR, DRIVER, EXEC, _fm, _write, init_governance_repo  # noqa: E402
from test_aipos_f94_governance_landing import _closed_card  # noqa: E402
from tools.aipos_cli import governance_commit as gc  # noqa: E402
from tools.aipos_cli.clock import iso_z  # noqa: E402

TASK = "AIPOS-F184T"
GOV_REL = "2_projects/gov"
LEDGER = "governance/LEDGER.md"


def _show(line: str) -> None:
    print(line, flush=True)


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True).stdout.strip()


def _head_files(repo: Path, rev: str = "HEAD") -> set[str]:
    return set(_git(repo, "show", "--name-only", "--no-renames", "--format=", rev).splitlines())


def _unstaged_only(repo: Path, path: str) -> bool:
    """工作树有改动、索引无改动(未暂存, 原样)。"""
    return path in _git(repo, "diff", "--name-only").splitlines() and path not in _git(repo, "diff", "--cached", "--name-only").splitlines()


def _card(gov: Path, task_id: str, queue: str, *, assigned: str = EXEC, extra: dict | None = None) -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} test", "project": "gov", "task_mode": "code", "assigned_to": assigned,
            "agent_instance": assigned, "status": queue, "audit": "required", "audit_by": AUDITOR, "created_by": DRIVER,
            "context_bundle": assigned, "priority": "high", "needs_owner": False, "output_target": "tools/",
            "artifact_policy": "formal_write"}
    meta.update(extra or {})
    path = gov / "5_tasks" / "queue" / queue / f"{task_id.lower()}.md"
    _write(path, _fm(meta, f"# {task_id}\n"))
    return path


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    repo = tmp_path / "repo"
    gov = repo / GOV_REL
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (gov / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events", "sessions"):
        (gov / "5_tasks" / "records" / sub).mkdir(parents=True)
    (gov / "5_tasks" / "policies").mkdir()
    (gov / "task_cards").mkdir()
    (gov / "governance" / "decision_log").mkdir(parents=True)
    _write(gov / LEDGER, "---\nstatus: active\n---\n# LEDGER\n\n| 日期 | 事 |\n|---|---|\n")
    code_repo = tmp_path / "product"
    code_repo.mkdir()
    for argv in (["init", "-q", "-b", "main"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *argv], cwd=code_repo, check=True, capture_output=True)
    _write(gov / "project.json", json.dumps({"project": "gov", "code_repo": str(code_repo), "config_version": 1}))
    _write(gov / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    for keep in gov.rglob("*"):
        if keep.is_dir() and not any(keep.iterdir()):
            (keep / ".gitkeep").write_text("", encoding="utf-8")
    remote = init_governance_repo(repo)
    return {"repo": repo, "gov": gov, "remote": remote}


def _cli(gov: Path, *argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(["--workspace-root", str(gov), *argv])
    # 去掉治理根解析标注首行(「治理根: 项目 …」, governance_root_resolution.announce_commands)
    text = "".join(ln for ln in out.getvalue().splitlines(keepends=True) if not ln.startswith("治理根: "))
    return rc, text, err.getvalue()


# ---------------------------------------------------------------------------
# 件① N6 归因
# ---------------------------------------------------------------------------

def test_item1_n6_commits_only_card_scope_while_advisor_stages_ledger(rig: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _closed_card(gov, TASK)
    ledger_edit = "| 2026-10-18 | 顾问同时段记账(不是本卡的) |\n"
    real_guardrails = gc._run_guardrails_on_manifest

    def guardrails_then_advisor_stages(*a, **kw):  # 10-16 F173 实撞: 预暂存检查已过、commit 之前, 顾问并发 git add LEDGER
        out = real_guardrails(*a, **kw)
        with (gov / LEDGER).open("a", encoding="utf-8") as fh:
            fh.write(ledger_edit)
        _git(repo, "add", "--", f"{GOV_REL}/{LEDGER}")
        return out

    monkeypatch.setattr(gc, "_run_guardrails_on_manifest", guardrails_then_advisor_stages)
    result = gc.governance_commit(gov, TASK, DRIVER)
    _show(f"[件①] verdict={result['verdict']} committed={result['committed']} pushed={result['pushed']} message={result['message']}")
    assert result["verdict"] == "PASS" and result["committed"] and result["pushed"], result
    head = _head_files(repo)
    _show(f"[件①] N6 提交 {_git(repo, 'log', '-1', '--format=%h %s')} 文件数={len(head)}; 含 LEDGER={f'{GOV_REL}/{LEDGER}' in head}")
    assert f"{GOV_REL}/{LEDGER}" not in head
    assert all(f.startswith(f"{GOV_REL}/5_tasks/") or f.startswith(f"{GOV_REL}/task_cards/") for f in head), head
    status = _git(repo, "status", "--porcelain", "--", f"{GOV_REL}/{LEDGER}")
    _show(f"[件①] LEDGER 状态(提交后) = {status!r}; 暂存内容末行 = {_git(repo, 'show', f':{GOV_REL}/{LEDGER}').splitlines()[-1]!r}")
    assert status.startswith("M "), status  # 仍在索引里、未提交, 内容原样
    assert _git(repo, "show", f":{GOV_REL}/{LEDGER}").endswith(ledger_edit.strip())
    assert f"{GOV_REL}/{LEDGER}" not in _git(repo, "diff", "--name-only", "origin/main~1", "origin/main")

    # 同卡重复 N6 = 幂等(无新提交); 顾问的 LEDGER 暂存仍不动
    monkeypatch.setattr(gc, "_run_guardrails_on_manifest", real_guardrails)
    before = _git(repo, "rev-parse", "HEAD")
    again = gc.governance_commit(gov, TASK, DRIVER)
    _show(f"[件①] 重复 N6: verdict={again['verdict']} committed={again['committed']} message={again['message']}")
    assert again["verdict"] == "PASS" and not again["committed"] and _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "status", "--porcelain", "--", f"{GOV_REL}/{LEDGER}").startswith("M ")


def test_item1_unstaged_ledger_edit_untouched(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _closed_card(gov, TASK)
    with (gov / LEDGER).open("a", encoding="utf-8") as fh:
        fh.write("| 2026-10-18 | 顾问正在写, 未暂存 |\n")
    result = gc.governance_commit(gov, TASK, DRIVER)
    assert result["verdict"] == "PASS" and result["pushed"], result
    status = _git(repo, "status", "--porcelain", "--", f"{GOV_REL}/{LEDGER}")
    _show(f"[件①] 未暂存的 LEDGER 改动在 N6 后 = {status!r}")
    assert _unstaged_only(repo, f"{GOV_REL}/{LEDGER}") and f"{GOV_REL}/{LEDGER}" not in _head_files(repo)


def test_item1_push_failure_hint_then_retry_pushes_without_new_commit(rig: dict) -> None:
    repo, gov, remote = rig["repo"], rig["gov"], rig["remote"]
    _closed_card(gov, TASK)
    parked = remote.with_name(remote.name + ".offline")
    remote.rename(parked)  # 远端不可达
    failed = gc.governance_commit(gov, TASK, DRIVER)
    first = failed["message"].splitlines()[0]
    _show(f"[件①] 推送失败首行: {first}")
    _show(f"[件①] retry_command = {failed.get('retry_command')}")
    assert failed["verdict"] != "PASS" and failed["committed"] and not failed["pushed"]
    assert first.startswith("推送未完成: 本地已有提交 ") and failed["retry_command"] == gc.governance_commit_command(TASK, DRIVER, gov)
    assert failed["retry_command"] in first and failed["operations"][-1].startswith(gc.PUSH_NOT_DONE_PREFIX)
    local = _git(repo, "rev-parse", "HEAD")
    parked.rename(remote)  # 远端恢复 → 重跑同一命令
    retried = gc.governance_commit(gov, TASK, DRIVER)
    _show(f"[件①] 重跑: verdict={retried['verdict']} committed={retried['committed']} pushed={retried['pushed']} message={retried['message']}")
    assert retried["verdict"] == "PASS" and retried["pushed"] and not retried["committed"] and retried.get("retried_push")
    assert _git(repo, "rev-parse", "HEAD") == local == _git(repo, "rev-parse", "origin/main")
    third = gc.governance_commit(gov, TASK, DRIVER)
    assert third["verdict"] == "PASS" and not third["committed"] and "已落账" in third["message"], third["message"]


def test_item1_reland_subject_distinct_from_first_n6(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _closed_card(gov, TASK)
    first = gc.governance_commit(gov, TASK, DRIVER)
    assert first["pushed"]
    _write(gov / "5_tasks" / "records" / "events" / TASK / "post_merge_regression_x.md",
           _fm({"record_type": "task_progress_event", "event_type": "post_merge_regression", "task_id": TASK, "actor": EXEC,
                "timestamp": "2026-10-18T00:00:00Z"}))
    out = gc.reland_after_write(gov, TASK, EXEC, what="合并后回归事件记录")
    subjects = _git(repo, "log", "-2", "--format=%s").splitlines()
    _show(f"[件①] 补落账行: {out['line']}")
    _show(f"[件①] 提交名: {subjects}")
    assert out["committed"] and out["pushed"]
    assert subjects == [f"chore(governance): N6 收账 {TASK} 补落账: 合并后回归事件记录", f"chore(governance): N6 收账 {TASK}"]


def test_item1_reland_template_declared() -> None:
    decl = gc.n6_landing_declaration()
    assert "{task_id}" in decl["reland_message_template"] and "{actor}" in decl["reland_message_template"]
    assert "--only" in decl["commit_scope"]


# ---------------------------------------------------------------------------
# 件② 队列写落账
# ---------------------------------------------------------------------------

def test_item2_withdraw_lands(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _card(gov, "AIPOS-F184W", "pending")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "publish F184W")
    _git(repo, "push", "-q", "origin", "main")
    with (gov / LEDGER).open("a", encoding="utf-8") as fh:
        fh.write("| x | 顾问未提交 |\n")
    rc, out, err = _cli(gov, "queue", "withdraw", "--task-id", "AIPOS-F184W", "--actor", DRIVER, "--reason", "方向变了")
    _show(f"[件②] withdraw rc={rc} 落账行: {err.strip() or [l for l in out.splitlines() if l.startswith('落账')]}")
    log = _git(repo, "log", "-1", "--format=%s%n%b")
    _show(f"[件②] git log -1: {log.splitlines()[0]}; 文件: {sorted(_head_files(repo))}")
    assert rc == 0, (out, err)
    assert log.startswith("chore(governance): 队列写落账 withdraw AIPOS-F184W")
    assert _head_files(repo) == {f"{GOV_REL}/5_tasks/queue/pending/aipos-f184w.md", f"{GOV_REL}/5_tasks/queue/withdrawn/aipos-f184w.md"}
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")
    assert _unstaged_only(repo, f"{GOV_REL}/{LEDGER}")


def _session(gov: Path, task_id: str, session_id: str, actor: str = EXEC, created_at: str | None = None) -> None:
    from tools.aipos_cli.record_writer import build_session_record_markdown, session_record_path

    path = session_record_path(gov, task_id, session_id)
    _write(path, build_session_record_markdown(task_id=task_id, task_path=f"5_tasks/queue/claimed/{task_id.lower()}.md", actor=actor,
                                               session_id=session_id, claim_id=f"claim_{task_id}_x", created_at=created_at or iso_z()))


def test_item2_block_then_reopen_land(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    sid = "session_AIPOS-F184B_x"
    _card(gov, "AIPOS-F184B", "claimed", extra={"claimed_by": EXEC, "claimed_at": "2026-10-18T00:00:00Z", "claim_id": "claim_AIPOS-F184B_x",
                                               "active_session_id": sid})
    _session(gov, "AIPOS-F184B", sid)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "claimed F184B")
    _git(repo, "push", "-q", "origin", "main")
    rc, out, err = _cli(gov, "queue", "block", "--task-id", "AIPOS-F184B", "--actor", EXEC, "--reason", "等 Owner", "--with-records")
    blocked_files = _head_files(repo)
    _show(f"[件②] block rc={rc} log={_git(repo, 'log', '-1', '--format=%s')} 文件={sorted(blocked_files)}")
    assert rc == 0, (out, err)
    assert _git(repo, "log", "-1", "--format=%s") == "chore(governance): 队列写落账 block AIPOS-F184B"
    assert {f"{GOV_REL}/5_tasks/queue/claimed/aipos-f184b.md", f"{GOV_REL}/5_tasks/queue/blocked/aipos-f184b.md"} <= blocked_files
    assert any("/records/sessions/" in f for f in blocked_files), blocked_files
    rc, out, err = _cli(gov, "queue", "reopen", "--task-id", "AIPOS-F184B", "--actor", EXEC, "--reason", "Owner 已定")
    _show(f"[件②] reopen rc={rc} log={_git(repo, 'log', '-1', '--format=%s')} 文件={sorted(_head_files(repo))}")
    assert rc == 0, (out, err)
    assert _git(repo, "log", "-1", "--format=%s") == "chore(governance): 队列写落账 reopen AIPOS-F184B"
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")


def test_item2_landing_failure_keeps_queue_state(rig: dict) -> None:
    repo, gov, remote = rig["repo"], rig["gov"], rig["remote"]
    _card(gov, "AIPOS-F184X", "pending")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "publish F184X")
    _git(repo, "push", "-q", "origin", "main")
    remote.rename(remote.with_name(remote.name + ".offline"))
    rc, out, err = _cli(gov, "queue", "withdraw", "--task-id", "AIPOS-F184X", "--actor", DRIVER, "--reason", "r")
    _show(f"[件②] 推送失败 rc={rc} stderr={err.strip()[:400]}")
    assert rc == 3 and "落账未完成" in err and "补账:" in err
    assert (gov / "5_tasks" / "queue" / "withdrawn" / "aipos-f184x.md").is_file()  # 不回滚


# ---------------------------------------------------------------------------
# 件③ queue supersede
# ---------------------------------------------------------------------------

def _inbox_task_ids(gov: Path) -> list[str]:
    from tools.aipos_cli.owner_inbox import collect_project_inbox

    return sorted(str(i.get("task_id")) for i in collect_project_inbox(gov)["items"])


def _commit_all(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    _git(repo, "push", "-q", "origin", "main")


def test_item3_supersede_claimed_card_with_recent_session(rig: dict) -> None:
    from tools.aipos_cli.board_adapter import withdraw_task
    from tools.aipos_cli.frontmatter import require_frontmatter

    repo, gov = rig["repo"], rig["gov"]
    sid = "session_AIPOS-F184C_x"
    _card(gov, "AIPOS-F184C", "claimed", extra={"claimed_by": EXEC, "claimed_at": iso_z(), "claim_id": "claim_AIPOS-F184C_x",
                                               "active_session_id": sid, "active_worktree_path": "/tmp/wt/AIPOS-F184C",
                                               "active_worktree_branch": "card/AIPOS-F184C"})
    _session(gov, "AIPOS-F184C", sid)
    _card(gov, "AIPOS-F184N", "pending")
    _commit_all(repo, "claimed F184C + new F184N")
    w = withdraw_task(task_id="AIPOS-F184C", actor=DRIVER, reason="Owner 改目标", dry_run=True, repo_root=gov)
    refusal = str((w.get("errors") or [{}])[0].get("message") or "")
    _show(f"[件③] 对照: withdraw 预演 = {w.get('verdict')} — {refusal}")
    # AIPOS-F234 件①: 在途保护按真相判(存活 loop 运行 / 拉起进程), 不再看会话创建时刻——近期会话而无存活进程 = 非在途, 撤回预演放行
    assert w.get("verdict") != "BLOCK" and (w.get("data") or {}).get("in_transit", {}).get("in_transit") is False, refusal
    words = "F184C 改为统一到官方设计系统, 原目标不做了"
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184C", "--by", "AIPOS-F184N", "--actor", DRIVER,
                        "--owner-words", words, "--owner-ref", "chat-2026-10-18")
    _show(f"[件③] claimed 预演 rc={rc}:\n{out.strip()}")
    assert rc == 0 and out.startswith("预演: AIPOS-F184C [claimed] → withdrawn, superseded_by=AIPOS-F184N")
    assert "原卡工作树 /tmp/wt/AIPOS-F184C(分支 card/AIPOS-F184C)保留" in out
    assert (gov / "5_tasks" / "queue" / "claimed" / "aipos-f184c.md").is_file()  # 预演零写入
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184C", "--by", "AIPOS-F184N", "--actor", DRIVER,
                        "--owner-words", words, "--owner-ref", "chat-2026-10-18", "--confirm")
    _show(f"[件③] claimed 确认 rc={rc}:\n{out.strip()}\n{err.strip()}")
    assert rc == 0, (out, err)
    fm, _ = require_frontmatter(gov / "5_tasks" / "queue" / "withdrawn" / "aipos-f184c.md")
    face = {k: fm.get(k) for k in ("status", "superseded_by", "superseded_owner_words", "superseded_owner_ref", "superseded_decision_id",
                                    "withdrawn_by", "active_session_id", "last_session_id")}
    _show(f"[件③] 卡面: {json.dumps(face, ensure_ascii=False)}")
    assert face["status"] == "withdrawn" and face["superseded_by"] == "AIPOS-F184N" and face["superseded_owner_words"] == words
    assert face["superseded_owner_ref"] == "chat-2026-10-18" and face["active_session_id"] is None and face["last_session_id"] == sid
    decision = gov / "5_tasks" / "records" / "owner_decisions" / f"{face['superseded_decision_id']}.md"
    dfm, dbody = require_frontmatter(decision)
    _show(f"[件③] 决策记录 {decision.name}: decision_type={dfm.get('decision_type')} allowed_next_action={dfm.get('allowed_next_action')} "
          f"captured_by={dfm.get('captured_by')} decided_by_ref={dfm.get('decided_by_ref')}")
    assert dfm["decision_type"] == "owner_inbox_ruling" and dfm["allowed_next_action"] == "supersede" and dfm["captured_by"] == DRIVER
    assert f"Owner 原话: {words}" in dbody
    log = _git(repo, "log", "-1", "--format=%s")
    files = sorted(_head_files(repo))
    _show(f"[件③] 落账提交: {log}; 文件={files}")
    assert log == "chore(governance): 队列写落账 supersede AIPOS-F184C"
    assert f"{GOV_REL}/5_tasks/queue/claimed/aipos-f184c.md" in files and f"{GOV_REL}/5_tasks/queue/withdrawn/aipos-f184c.md" in files
    assert any("owner_decisions" in f for f in files)
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")
    from tools.aipos_cli.owner_inbox import rulings_for_task

    assert [r.get("allowed_next_action") for r in rulings_for_task(gov, "AIPOS-F184C")] == ["supersede"]


def test_item3_supersede_blocked_card_clears_inbox(rig: dict) -> None:
    from tools.aipos_cli.frontmatter import require_frontmatter

    repo, gov = rig["repo"], rig["gov"]
    _card(gov, "AIPOS-F184K", "blocked", extra={"claimed_by": EXEC, "blocked_by": DRIVER, "blocked_at": "2026-10-17T11:00:00Z",
                                               "block_reason": "Owner 改目标, 暂停", "needs_owner": True})
    _card(gov, "AIPOS-F184M", "pending")
    _commit_all(repo, "blocked F184K + new F184M")
    before = _inbox_task_ids(gov)
    _show(f"[件③] 撤换前收件箱事项卡: {before}")
    assert "AIPOS-F184K" in before
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184K", "--by", "AIPOS-F184M", "--actor", DRIVER,
                        "--owner-words", "改成统一设计系统", "--dry-run")
    _show(f"[件③] blocked 预演 rc={rc}: {out.strip()}")
    assert rc == 0 and out.startswith("预演: AIPOS-F184K [blocked] → withdrawn")
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184K", "--by", "AIPOS-F184M", "--actor", DRIVER,
                        "--owner-words", "改成统一设计系统", "--confirm")
    _show(f"[件③] blocked 确认 rc={rc}: {out.strip()}")
    assert rc == 0, (out, err)
    after = _inbox_task_ids(gov)
    _show(f"[件③] 撤换后收件箱事项卡: {after}")
    assert "AIPOS-F184K" not in after
    fm, _ = require_frontmatter(gov / "5_tasks" / "queue" / "withdrawn" / "aipos-f184k.md")
    assert fm["superseded_by"] == "AIPOS-F184M" and fm["needs_owner"] is False


def test_item3_supersede_refusals(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _card(gov, "AIPOS-F184R", "pending")
    _card(gov, "AIPOS-F184S", "pending")
    _commit_all(repo, "two pending")
    head = _git(repo, "rev-parse", "HEAD")
    cases = {
        "非顾问身份": ("--actor", EXEC, "--by", "AIPOS-F184S"),
        "新卡不存在": ("--actor", DRIVER, "--by", "AIPOS-F184ZZ"),
        "新卡=原卡": ("--actor", DRIVER, "--by", "AIPOS-F184R"),
    }
    for label, extra in cases.items():
        rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184R", "--owner-words", "x", "--confirm", *extra)
        _show(f"[件③] 拒·{label} rc={rc}: {out.strip()}")
        assert rc == 1 and out.startswith("撤换被拒(零写入)"), (out, err)
    rc, out, _ = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184R", "--owner-words", "x", "--confirm", "--actor", EXEC,
                      "--by", "AIPOS-F184S")
    assert "ROLE_VIOLATION" in out
    rc, out, _ = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184R", "--owner-words", "x", "--confirm", "--actor", DRIVER,
                      "--by", "AIPOS-F184ZZ")
    assert "新卡 AIPOS-F184ZZ 不在队列" in out
    rc, out, _ = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F184R", "--owner-words", " ", "--confirm", "--actor", DRIVER,
                      "--by", "AIPOS-F184S")
    _show(f"[件③] 拒·缺原话 rc={rc}: {out.strip()}")
    assert rc == 1 and "缺 --owner-words" in out
    assert _git(repo, "rev-parse", "HEAD") == head and (gov / "5_tasks" / "queue" / "pending" / "aipos-f184r.md").is_file()
    assert not (gov / "5_tasks" / "records" / "owner_decisions").exists() or not any((gov / "5_tasks" / "records" / "owner_decisions").iterdir())


def test_item3_declarations_single_source() -> None:
    from tools.aipos_cli import queue_mutation
    from tools.aipos_cli.owner_inbox import inbox_declaration
    from tools.schema_loader import code_repo_schema_root, load_schema

    decl = queue_mutation.supersede_declaration()
    assert queue_mutation.ALLOWED_TRANSITIONS["supersede"] == (("pending", "claimed", "blocked"), "withdrawn")
    assert decl["decision_choice"] in inbox_declaration()["options"]
    card_fields = set(load_schema("card", code_repo_schema_root())["fields"])
    assert set(decl["card_fields"].values()) <= card_fields  # AIPOS-F213 件⑤: 角色 → 卡面键名
    verbs = load_schema("verbs", code_repo_schema_root())["verbs"]
    assert verbs["lybra_queue_supersede"]["cli_command"] == "lybra queue supersede"
    landing = verbs["lybra_governance_commit"]["post_write_landing"]["commands"]["queue_writers"]
    assert landing["subcommands"][:4] == ["block", "reopen", "withdraw", "supersede"]  # AIPOS-F207 追加 correct(清单只追加)
    assert verbs["lybra_queue_supersede"]["exit_codes"]["landing_failed"]["code"] == \
        verbs["lybra_governance_commit"]["post_write_landing"]["landing_failed_exit_code"]
