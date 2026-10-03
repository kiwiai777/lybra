"""AIPOS-F89 件③ — 审计卡开工面(Owner 2026-10-03 裁定 A2 + 10-03 三处实撞)。靶场复用 F73D/F78/F78C/F86/F88 既有构件。

a) 认领审计卡时产品为被审分支 tip 建只读 detached 取证工作树(落点 card_worktree_location: 被审卡所在仓 <worktree_root>/<审计卡ID>,
   同一建树入口 _ensure_worktree; 须建树判据 card_needs_worktree 门认领与 loop 核验同读); 选卡 select_next_card 以其存在为准,
   审计工位 /go 能选中审计卡。
b) my-tasks --workstation <工位目录>: 实例与治理根由产品经 charter_render.workstation_identity / resolve_workstation_governance_root
   解析; go.ts 不读 .lybra/role, 只读 connection.json lybra_bin, 读不到即报错(无 "lybra" 缺省)。
c) 报告完成判据声明化(transitions artifact_ingest.verdict.readiness): 认领时空模板与真实报告同名同位(card_report_path), 必填
   frontmatter(verdict/commit_sha)全填且非占位才算完成; 空模板 = 未交(等待), 已交未填全 = artifact_invalid 硬停; ingest 同判据。
d) 审计报告快照包记录头(record_type 等)后写入 records, 过护栏 B④; 复原 = unwrap_report_snapshot(去头 + 核 sha256)。

验收③④⑤ 对应 test_item3_a_* / test_item3_c_* / test_item3_d_*。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import AUDITOR, EXEC, _fm, _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import _branch_with_commit, _card, _git  # noqa: E402
from test_aipos_f78c_card_repo import _single_gov  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.agent_profiles import load_agent_profiles  # noqa: E402
from tools.aipos_cli.queue_mutation import mutate_queue_task  # noqa: E402
from tools.aipos_cli.task_loader import queue_root_for  # noqa: E402

GO_TS = REPO_ROOT / "agents" / "harness" / "pi" / "_shared" / "extensions" / "go.ts"
REVIEWED = "AIPOS-F89A"
AUDIT = "AIPOS-F89AR"


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _audit_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reviewed_mode: str = "code") -> tuple[Path, Path, str]:
    """单仓治理根 + 被审代码卡(claimed, 分支有提交) + 待认领审计卡(pending)。返回 (gov, repo, 被审 tip)。"""
    gov, repo = _single_gov(tmp_path, monkeypatch)
    _card(gov, REVIEWED, "claimed", task_mode=reviewed_mode)
    tip = _branch_with_commit(repo, REVIEWED)[0] if reviewed_mode == "code" else ""
    _card(gov, AUDIT, "pending", assigned=AUDITOR, task_mode="audit",
          extra={"reviewed_task_id": REVIEWED, "derived_from": REVIEWED, "audit": "none"})
    return gov, repo, tip


def _claim_audit(gov: Path) -> dict:
    """门认领核(门 controlled-execute 与 file-CLI 同调 mutate_queue_task, 带记录 = 认领记录 + 报告空模板)。"""
    return mutate_queue_task(gov, "claim", task_id=AUDIT, actor=AUDITOR, dry_run=False,
                             profiles=load_agent_profiles(gov), with_records=True)


def _workstation(tmp_path: Path, gov: Path, *, role: str = "auditor", instance: str = AUDITOR, conn: dict | None = None) -> Path:
    """工位目录(enroll 后形): .lybra/role + connection.json(governance_root / lybra_bin; 夹具不放 token)。"""
    ws = tmp_path / f"ws-{role}"
    _write(ws / ".lybra" / "role", json.dumps({"role": role, "instance": instance}))
    _write(ws / ".lybra" / "connection.json", json.dumps(conn if conn is not None else
                                                          {"governance_root": str(gov), "lybra_bin": "/opt/lybra/bin/lybra"}))
    return ws


def _my_tasks_cli(argv: list[str], capsys: pytest.CaptureFixture) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    capsys.readouterr()
    rc = main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# ===========================================================================
# a) 取证工作树 + /go 选中审计卡(验收③)
# ===========================================================================

def test_item3_a_audit_claim_builds_readonly_detached_forensic_tree_and_go_selects_audit_card(tmp_path, monkeypatch, capsys):
    gov, repo, tip = _audit_rig(tmp_path, monkeypatch)
    result = _claim_audit(gov)
    _show("[③a·认领审计卡] " + json.dumps({k: result.get(k) for k in (
        "verdict", "worktree_created", "worktree_path", "worktree_branch", "worktree_detached", "worktree_commit",
        "skeleton_path", "blocking_reasons")}, ensure_ascii=False))
    assert result["wrote"] and result["worktree_created"] is True and result["worktree_detached"] is True
    worktree = Path(result["worktree_path"])
    assert worktree == repo / ".worktrees" / AUDIT == nr.card_worktree_location(gov, AUDIT)[1]
    assert result["worktree_commit"] == tip == _git(worktree, "rev-parse", "HEAD")
    detached = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=worktree, capture_output=True, text=True)
    _show(f"[③a·取证树] HEAD={_git(worktree, 'rev-parse', 'HEAD')} symbolic-ref rc={detached.returncode}(1 = detached, 无分支可提交)")
    assert detached.returncode == 1
    assert result["worktree_branch"] == nr.card_branch_name(REVIEWED)  # 被审分支(只读取证), 不建审计卡分支
    assert subprocess.run(["git", "rev-parse", "--verify", f"refs/heads/{nr.card_branch_name(AUDIT)}"], cwd=repo,
                          capture_output=True).returncode != 0

    ws = _workstation(tmp_path, gov)
    rc, out, err = _my_tasks_cli(["my-tasks", "--workstation", str(ws), "--json"], capsys)
    assert rc == 0, err
    data = json.loads(out)
    _show("[③a·my-tasks --workstation] " + json.dumps({"workstation": data.get("workstation"), "next_card": data.get("next_card"),
                                                       "next_card_excluded": data.get("next_card_excluded")}, ensure_ascii=False))
    assert data["workstation"]["instance"] == AUDITOR and data["workstation"]["governance_root"] == str(gov.resolve())
    assert data["next_card"] and data["next_card"]["task_id"] == AUDIT
    assert data["next_card"]["worktree_path"] == str(worktree)
    assert data["next_card"]["report_path"] == str(nr.audit_report_artifact_path(gov, AUDIT))


def test_item3_a_forensic_tree_repoints_on_new_tip_refuses_dirty_and_missing_branch_blocks_claim(tmp_path, monkeypatch):
    gov, repo, tip = _audit_rig(tmp_path, monkeypatch)
    fm = nr._read_frontmatter(queue_root_for(gov) / "pending" / f"{AUDIT.lower()}.md")
    built = nr._ensure_worktree(gov, AUDIT, card_frontmatter=fm)
    assert built["ok"] and built["commit"] == tip
    again = nr._ensure_worktree(gov, AUDIT, card_frontmatter=fm)
    assert again["ok"] and "already" in again["message"]
    # 复审: 被审分支前进 → 取证树重指到新 tip
    _git(repo, "checkout", "-q", nr.card_branch_name(REVIEWED))
    _write(repo / "tools" / "aipos_cli" / "y.py", "# fix\n")
    _git(repo, "add", "tools/aipos_cli/y.py")
    _git(repo, "commit", "-q", "-m", "fix")
    new_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    moved = nr._ensure_worktree(gov, AUDIT, card_frontmatter=fm)
    _show(f"[③a·重指] {moved['message']}")
    assert moved["ok"] and moved["commit"] == new_tip == _git(Path(moved["worktree_path"]), "rev-parse", "HEAD")
    # 取证树有本地改动 → 拒重指(只读语义)
    _write(Path(moved["worktree_path"]) / "README.md", "tampered\n")
    _git(repo, "checkout", "-q", nr.card_branch_name(REVIEWED))
    _write(repo / "tools" / "aipos_cli" / "z.py", "# fix2\n")
    _git(repo, "add", "tools/aipos_cli/z.py")
    _git(repo, "commit", "-q", "-m", "fix2")
    _git(repo, "checkout", "-q", "main")
    dirty = nr._ensure_worktree(gov, AUDIT, card_frontmatter=fm)
    _show(f"[③a·脏树拒] {dirty['message']}")
    assert dirty["ok"] is False and "本地改动" in dirty["message"]

    # 被审分支不存在 → 门拒认领, 队列零变更
    gov2, _repo2, _ = _audit_rig(tmp_path / "nobranch", monkeypatch)
    _git(_repo2, "branch", "-D", nr.card_branch_name(REVIEWED))
    blocked = _claim_audit(gov2)
    _show(f"[③a·无被审分支] verdict={blocked['verdict']} reasons={blocked['blocking_reasons']}")
    assert blocked["verdict"] == "BLOCK" and any(r.startswith("WORKTREE_CREATE_FAILED") and "不存在" in r for r in blocked["blocking_reasons"])
    assert (queue_root_for(gov2) / "pending" / f"{AUDIT.lower()}.md").is_file()


def test_item3_a_audit_of_non_code_card_builds_no_tree(tmp_path, monkeypatch):
    """被审卡非代码卡(无卡分支): 审计卡认领不建树(与 F90 前非代码卡语义一致), 认领不因无分支被拒。"""
    gov, repo, _ = _audit_rig(tmp_path, monkeypatch, reviewed_mode="docs")
    assert nr.card_needs_worktree(gov, nr._read_frontmatter(queue_root_for(gov) / "pending" / f"{AUDIT.lower()}.md")) is False
    result = _claim_audit(gov)
    assert result["wrote"] and "worktree_created" not in result and not (repo / ".worktrees" / AUDIT).exists()


def test_item3_b_my_tasks_workstation_resolves_identity_and_refuses_cleanly(tmp_path, monkeypatch, capsys):
    gov, _repo, _ = _audit_rig(tmp_path, monkeypatch)
    rc, _out, err = _my_tasks_cli(["my-tasks", "--json"], capsys)
    assert rc == 2 and "--workstation" in err
    bare = tmp_path / "bare-ws"
    bare.mkdir()
    rc, _out, err = _my_tasks_cli(["my-tasks", "--workstation", str(bare), "--json"], capsys)
    _show(f"[③b·非工位目录] rc={rc} err={err.strip()}")
    assert rc == 1 and "WORKSTATION_IDENTITY_UNRESOLVED" in err
    ws = _workstation(tmp_path, gov)
    rc, _out, err = _my_tasks_cli(["my-tasks", "--workstation", str(ws), "--actor", EXEC, "--json"], capsys)
    assert rc == 2 and AUDITOR in err
    rc, out, _err = _my_tasks_cli(["my-tasks", "--workstation", str(ws), "--actor", AUDITOR, "--json"], capsys)
    assert rc == 0 and json.loads(out)["actor"] == AUDITOR
    assert "fixture" not in out and "token" not in json.dumps(json.loads(out).get("workstation"))


def test_item3_b_go_ts_reads_only_lybra_bin_no_role_no_default(tmp_path):
    src = GO_TS.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith(("*", "/*", "//")))
    _show("[③b·go.ts 静态] .lybra/role 读取=%s, 'lybra' 缺省=%s, workspace_root=%s" % (
        bool(re.search(r'"role"', code)), bool(re.search(r'\|\|\s*"lybra"', code)), "workspace_root" in code))
    assert not re.search(r'"role"', code), "go.ts 仍拼 .lybra/role 路径"
    assert not re.search(r'\|\|\s*"lybra"', code) and "lybra_bin ||" not in code, "go.ts 仍有 lybra 缺省"
    assert "workspace_root" not in code and "workspaceRoot" not in code
    assert '"--workstation"' in code and "readLybraBin(" in code
    node = shutil.which("node")
    assert node, "node 不在 PATH(run-all TS 夹具同前置)"
    conn_ok = tmp_path / "ok.json"
    conn_ok.write_text(json.dumps({"lybra_bin": "/opt/l/bin/lybra", "tokens": [{"token": "fixture-not-a-secret"}]}), encoding="utf-8")
    conn_nokey = tmp_path / "nokey.json"
    conn_nokey.write_text(json.dumps({"governance_root": "/g"}), encoding="utf-8")
    script = (
        f"import {{ readLybraBin, myTasksArgv }} from {json.dumps(GO_TS.as_uri())};\n"
        "import * as fs from 'node:fs';\n"
        "console.log(JSON.stringify({"
        f"ok: readLybraBin(fs, {json.dumps(str(conn_ok))}), "
        f"nokey: readLybraBin(fs, {json.dumps(str(conn_nokey))}), "
        f"missing: readLybraBin(fs, {json.dumps(str(tmp_path / 'absent.json'))}), "
        "argv: myTasksArgv('/ws/probe-auditor', '')}));\n"
    )
    proc = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    _show(f"[③b·readLybraBin] {json.dumps(data, ensure_ascii=False)}")
    assert data["ok"] == {"ok": True, "bin": "/opt/l/bin/lybra"} and "fixture-not-a-secret" not in proc.stdout
    assert data["nokey"]["ok"] is False and "缺 lybra_bin" in data["nokey"]["message"]
    assert data["missing"]["ok"] is False and "不存在" in data["missing"]["message"]
    assert data["argv"] == ["my-tasks", "--workstation", "/ws/probe-auditor", "--json"]


# ===========================================================================
# c) 报告完成判据(验收④)
# ===========================================================================

def test_item3_c_claim_template_same_place_not_counted_filled_report_recognized(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    gov, _repo, tip = _audit_rig(tmp_path, monkeypatch)
    result = _claim_audit(gov)
    template = gov / result["skeleton_path"]
    _show(f"[③c·空模板] {template}\n" + template.read_text(encoding="utf-8")[:240])
    assert template == nr.card_report_path(gov, AUDIT) == nr.audit_report_artifact_path(gov, AUDIT)  # 与真实报告同名同位
    fm = nr._read_frontmatter(template)
    assert set(nr.required_verdict_frontmatter()) <= set(fm) and not nr.verdict_report_ready(fm)
    # 空模板: 推导核等待(不是产物), ingest 拒「未找到已完成报告」, 开工核验不判「产物已交」
    assert nr._check_verdict_artifact(gov, AUDIT) is None
    d = nr.derive_next_step(AUDIT, gov)
    assert d["derivable"] is False and (d.get("action") or {}).get("type") != "artifact_invalid", d
    assert validate_task_artifact(AUDIT, gov)["category"] == "INGEST_VERDICT_MISSING"
    assert nr.kickoff_refusal(gov, AUDIT, AUDITOR, queue_state="claimed", card_frontmatter=nr._read_frontmatter(
        queue_root_for(gov) / "claimed" / f"{AUDIT.lower()}.md")) is None
    # 已交未填全(只填 verdict): artifact_invalid 硬停点名 commit_sha, 不入门
    _write(template, _fm({"verdict": "PASS", "commit_sha": "(待填写: tip)"}, "# r\n\n## 一句话结论\nPASS\n"))
    partial = nr.derive_next_step(AUDIT, gov)
    _show(f"[③c·未填全] action={partial.get('action')} missing={partial.get('missing_records')}")
    assert partial["action"]["type"] == "artifact_invalid" and "commit_sha" in partial["missing_records"][0]
    # 填好: 识别为完成, 推导核派生入门命令, ingest 判据通过(commit_sha = 被审 tip)
    _write(template, _fm({"verdict": "PASS", "commit_sha": tip, "reviewed_task_id": REVIEWED}, "# r\n\n## 一句话结论\nPASS\n"))
    assert nr._check_verdict_artifact(gov, AUDIT) == template
    done = nr.derive_next_step(AUDIT, gov)
    assert done["derivable"] and done["verb"] == "lybra_audit_verdict_dry_run" and "--kind verdict" in done["command"]
    check = validate_task_artifact(AUDIT, gov)
    _show(f"[③c·填好] derive verb={done['verb']} ingest={check['category']}")
    assert check["ok"] and check["path"] == str(template)


def test_item3_c_loop_waits_on_template_and_wakes_on_filled_report(tmp_path, monkeypatch):
    """loop 等待的就绪谓词 = 推导核(同判据): 空模板落盘不唤醒; 填好后唤醒。等待根/glob 读声明 verdict_root。"""
    from tools.aipos_cli import loop_driver

    gov, _repo, tip = _audit_rig(tmp_path, monkeypatch)
    _claim_audit(gov)
    root, patterns = nr.auditor_artifact_watch(gov, AUDIT)
    assert root == gov and patterns[0] == f"task_cards/{AUDIT}/RETURN.md"
    src = Path(loop_driver.__file__).read_text(encoding="utf-8")
    assert "auditor_artifact_watch(governance_root, audit_card)" in src and "auditor_artifact_patterns" not in src.replace(
        "两个 *_artifact_patterns", "").replace("auditor_artifact_patterns\n", "")
    ready = lambda: bool(nr.derive_next_step(AUDIT, gov).get("derivable"))  # noqa: E731 — 与 loop _ready 同一谓词
    assert ready() is False
    _write(nr.card_report_path(gov, AUDIT), _fm({"verdict": "PASS", "commit_sha": tip}, "# r\n\n## 一句话结论\nPASS\n"))
    assert ready() is True


# ===========================================================================
# d) 快照包记录头, 过护栏 B④(验收⑤)
# ===========================================================================

def test_item3_d_report_snapshot_carries_record_header_and_passes_guardrail_b4(tmp_path, monkeypatch):
    import hashlib

    from tools.aipos_cli.board_adapter import _audit_report_snapshot_plan, unwrap_report_snapshot
    from tools.aipos_cli.governance_guardrails import check_entries, load_guardrail_declarations

    gov, _repo, tip = _audit_rig(tmp_path, monkeypatch)
    (gov / "governance").mkdir(exist_ok=True)
    report = gov / "task_cards" / AUDIT / "RETURN.md"
    original = _fm({"verdict": "PASS", "commit_sha": tip, "reviewed_task_id": REVIEWED}, "# 审计\n\n## 一句话结论\nPASS\n")
    _write(report, original)
    plan = _audit_report_snapshot_plan(gov, AUDIT, REVIEWED, f"verdict_{REVIEWED}_x")
    target = gov / plan["path"]
    _write(target, plan["content"])
    head = plan["content"].split("\n---\n", 1)[0]
    _show(f"[③d·快照记录头]\n{head}\n---")
    header = nr._read_frontmatter(target)
    assert header["record_type"] == "audit_report_snapshot" and header["verdict_id"] == f"verdict_{REVIEWED}_x"
    assert plan["sha256"] == hashlib.sha256(original.encode("utf-8")).hexdigest() == header["report_sha256"]
    assert unwrap_report_snapshot(target.read_text(encoding="utf-8")) == original
    decls = load_guardrail_declarations()
    rel = str(target.relative_to(gov))
    ok = check_entries(gov, [("A", rel)], decls, current_branch="main")
    _show(f"[③d·护栏 B④ 包头后] ok={ok.ok} violations={ok.violations}")
    assert ok.ok and not ok.violations
    # 先红复演(2026-10-03 F91 原文): 原文直拷进 records = B④ 拒
    raw = target.with_name("raw-copy.md")
    _write(raw, original)
    red = check_entries(gov, [("A", str(raw.relative_to(gov)))], decls, current_branch="main")
    _show(f"[③d·护栏 B④ 原文直拷] violations={red.violations}")
    assert any(v["check"] == "B④" and "record_type" in v["reason"] for v in red.violations)
    with pytest.raises(ValueError):
        unwrap_report_snapshot(plan["content"] + "tampered")


def test_item3_fixture_registered_in_runall():
    from tools.aipos_cli.board_adapter import RUNALL_RELATIVE_PATH

    text = (REPO_ROOT / RUNALL_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "tests/test_aipos_f89_audit_kickoff_surface.py" in text
